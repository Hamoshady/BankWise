"""Sliding-window rate limiter.

LIMITATIONS (read before relying on it): state lives in this process's memory. It is NOT shared between
processes, containers or machines, it is reset on restart, and it protects nothing that does not call it
(there is no API in this repository yet). Use it for development, tests and single-process tools only; a
shared store (for example Redis) is needed for real multi-process enforcement.

Semantics: a hit at time t counts during [t, t + window). With limit N, the (N+1)-th hit inside one
window is refused and the next one is allowed exactly when the oldest hit leaves the window. Refused
attempts are not recorded, so hammering a limited key does not extend its lockout."""
from __future__ import annotations

import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Mapping, Protocol, Tuple

from bankwise.core.errors import ConfigError, InvalidInputError, RateLimitExceeded

MAX_KEY_CHARS = 128


def normalise_key(key: str) -> str:
    """Canonical form of a caller-supplied key, so trivial variants ('Alice', 'ALICE ', full-width letters)
    cannot get separate budgets. Raises InvalidInputError for empty, oversized or control-character keys."""
    if not isinstance(key, str):
        raise InvalidInputError("rate limit key must be a string")
    canonical = unicodedata.normalize("NFKC", key).strip().casefold()
    if not canonical or len(canonical) > MAX_KEY_CHARS or not canonical.isprintable():
        raise InvalidInputError("rate limit key must be a non-empty printable string of at most 128 characters")
    return canonical


@dataclass(frozen=True)
class RateLimit:
    limit: int
    window_seconds: float

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
            raise ConfigError("rate limit 'limit' must be an integer >= 1")
        if isinstance(self.window_seconds, bool) or not isinstance(self.window_seconds, (int, float)) \
                or self.window_seconds <= 0:
            raise ConfigError("rate limit 'window_seconds' must be a number > 0")


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float


class RateLimiterBackend(Protocol):
    """What callers (see guard.py) need from a limiter. `InMemoryRateLimiter` is the only implementation;
    a shared-store implementation can replace it without touching callers."""

    def acquire(self, policy: str, key: str) -> RateDecision:
        ...

    def peek(self, policy: str, key: str) -> RateDecision:
        ...

    def enforce(self, policy: str, key: str) -> RateDecision:
        ...


class InMemoryRateLimiter:
    def __init__(self, policies: Mapping[str, RateLimit], clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10000) -> None:
        self._policies = dict(policies)
        self._clock = clock
        self._max_keys = max_keys
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}
        self._lock = threading.Lock()

    def acquire(self, policy: str, key: str) -> RateDecision:
        """Record one hit for `key` under `policy` if allowed. The key must be an explicit non-empty string:
        there is no shared 'anonymous' bucket, so callers choose one on purpose (for example 'anon:<client>')."""
        rule = self._rule_for(policy, key)
        key = normalise_key(key)
        now = self._clock()
        with self._lock:
            slot = (policy, key)
            hits = self._hits.get(slot)
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    self._purge(now)
                if len(self._hits) >= self._max_keys:
                    return RateDecision(False, 0, rule.window_seconds)      # fail closed when full
                hits = self._hits[slot] = deque()
            while hits and hits[0] <= now - rule.window_seconds:
                hits.popleft()
            if len(hits) >= rule.limit:
                return RateDecision(False, 0, max(hits[0] + rule.window_seconds - now, 0.0))
            hits.append(now)
            return RateDecision(True, rule.limit - len(hits), 0.0)

    def peek(self, policy: str, key: str) -> RateDecision:
        """Report what `acquire` would decide, WITHOUT recording a hit. Used to refuse callers who already
        exhausted a budget that is only charged on failures (see guard.py)."""
        rule = self._rule_for(policy, key)
        key = normalise_key(key)
        now = self._clock()
        with self._lock:
            hits = self._hits.get((policy, key))
            if hits is not None:
                while hits and hits[0] <= now - rule.window_seconds:
                    hits.popleft()
            used = len(hits) if hits else 0
            if used >= rule.limit:
                return RateDecision(False, 0, max(hits[0] + rule.window_seconds - now, 0.0))
            return RateDecision(True, rule.limit - used, 0.0)

    def _rule_for(self, policy: str, key: str) -> RateLimit:
        rule = self._policies.get(policy)
        if rule is None:
            raise ConfigError(f"unknown rate limit policy '{policy}'")
        normalise_key(key)                                  # validates; callers re-normalise for the table slot
        return rule

    def enforce(self, policy: str, key: str) -> RateDecision:
        decision = self.acquire(policy, key)
        if not decision.allowed:
            raise RateLimitExceeded(f"rate limit '{policy}' exceeded", decision.retry_after_seconds)
        return decision

    def _purge(self, now: float) -> None:
        for slot in list(self._hits):
            rule = self._policies[slot[0]]
            hits = self._hits[slot]
            while hits and hits[0] <= now - rule.window_seconds:
                hits.popleft()
            if not hits:
                del self._hits[slot]
