import threading

import pytest

from bankwise.core.errors import ConfigError, InvalidInputError, RateLimitExceeded
from bankwise.core.rate_limit import InMemoryRateLimiter, RateLimit


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(limit=3, window=10, **kwargs):
    clock = Clock()
    return InMemoryRateLimiter({"p": RateLimit(limit, window)}, clock=clock, **kwargs), clock


def test_limit_boundary_is_exact():
    limiter, clock = make(limit=3, window=10)
    assert [limiter.acquire("p", "k").remaining for _ in range(3)] == [2, 1, 0]
    denied = limiter.acquire("p", "k")
    assert (denied.allowed, denied.remaining, denied.retry_after_seconds) == (False, 0, 10.0)


def test_window_boundary_a_hit_leaves_exactly_after_the_window():
    limiter, clock = make(limit=2, window=10)
    assert limiter.acquire("p", "k").allowed and limiter.acquire("p", "k").allowed
    clock.now += 9.999
    blocked = limiter.acquire("p", "k")
    assert not blocked.allowed and 0 < blocked.retry_after_seconds < 0.01
    clock.now += 0.001                                                           # t0 + 10.0: oldest hits have expired
    assert limiter.acquire("p", "k").allowed


def test_sliding_window_releases_hits_gradually():
    limiter, clock = make(limit=2, window=10)
    limiter.acquire("p", "k")                                                    # t=0
    clock.now += 6
    limiter.acquire("p", "k")                                                    # t=6
    clock.now += 5                                                               # t=11: first expired, second not
    assert limiter.acquire("p", "k").allowed
    assert not limiter.acquire("p", "k").allowed


def test_refused_attempts_do_not_extend_the_lockout():
    limiter, clock = make(limit=1, window=10)
    limiter.acquire("p", "k")
    for _ in range(50):
        clock.now += 0.1
        assert not limiter.acquire("p", "k").allowed
    clock.now = 1010.0
    assert limiter.acquire("p", "k").allowed


def test_keys_and_policies_are_independent():
    limiter = InMemoryRateLimiter({"a": RateLimit(1, 10), "b": RateLimit(1, 10)}, clock=Clock())
    assert limiter.acquire("a", "u1").allowed and limiter.acquire("a", "u2").allowed
    assert not limiter.acquire("a", "u1").allowed
    assert limiter.acquire("b", "u1").allowed


@pytest.mark.parametrize("key", [None, "", "   ", "x" * 129, 5, b"k", ("a",)])
def test_unknown_or_invalid_identities_are_rejected_not_pooled(key):
    limiter, _ = make()
    with pytest.raises(InvalidInputError):
        limiter.acquire("p", key)


def test_unknown_policy_is_a_configuration_error():
    limiter, _ = make()
    with pytest.raises(ConfigError):
        limiter.acquire("nope", "k")


def test_enforce_raises_with_a_retry_hint():
    limiter, clock = make(limit=1, window=10)
    limiter.enforce("p", "k")
    clock.now += 4
    with pytest.raises(RateLimitExceeded) as info:
        limiter.enforce("p", "k")
    assert info.value.to_public()["code"] == "rate_limited" and info.value.retry_after_seconds == pytest.approx(6)


def test_full_table_fails_closed_for_new_keys_and_recovers_after_expiry():
    limiter, clock = make(limit=1, window=10, max_keys=2)
    assert limiter.acquire("p", "a").allowed and limiter.acquire("p", "b").allowed
    assert not limiter.acquire("p", "c").allowed                                 # table full, nothing expired yet
    assert not limiter.acquire("p", "a").allowed                                 # existing keys keep their own state
    clock.now += 10
    assert limiter.acquire("p", "c").allowed                                     # expired entries were purged


@pytest.mark.parametrize("limit,window", [(0, 1), (-1, 1), (True, 1), (1.5, 1), ("3", 1), (1, 0), (1, -1), (1, True), (1, "5")])
def test_invalid_limits_are_rejected(limit, window):
    with pytest.raises(ConfigError):
        RateLimit(limit, window)


def test_concurrent_callers_never_exceed_the_limit():
    limiter = InMemoryRateLimiter({"p": RateLimit(50, 3600)})
    allowed = []

    def worker():
        for _ in range(40):
            allowed.append(limiter.acquire("p", "shared").allowed)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(allowed) == 50 and len(allowed) == 320
