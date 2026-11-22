"""SMS delivery for login OTPs.

Production uses an HTTPS webhook (``BANKWISE_SMS_URL`` / ``BANKWISE_SMS_API_KEY``); credentials come only from
the environment. When no provider is configured, development gets a clearly separated in-memory mock. The mock
never writes codes to disk, logs, or the response of any production endpoint; it exists so the 2FA flow can be
exercised locally without a real gateway. It is a hard start-up error to fall back to it in production.
"""
from __future__ import annotations

import threading
from typing import Dict, Mapping, Optional, Protocol, Tuple

import httpx

from bankwise.core.errors import ConfigError, ServiceUnavailable
from bankwise.core.pii import register_secret_value

SMS_URL_ENV = "BANKWISE_SMS_URL"
SMS_KEY_ENV = "BANKWISE_SMS_API_KEY"


class SmsProvider(Protocol):
    def send(self, phone_e164: str, code: str, *, reference: str) -> None:
        """Deliver `code` to the already-verified `phone_e164`. `reference` is an opaque challenge id
        (never a secret) used only to correlate delivery for local testing."""


class MockSmsProvider:
    """DEVELOPMENT ONLY. Keeps delivered codes in process memory, keyed by an opaque challenge reference.

    The phone number is deliberately discarded: even in development we retain only what a test needs to read a
    code back. Instances are constructed only by ``build_sms_provider`` when BANKWISE_ENV=development and no real
    provider is configured.
    """

    def __init__(self) -> None:
        self._codes: Dict[str, str] = {}
        self._lock = threading.Lock()

    def send(self, phone_e164: str, code: str, *, reference: str) -> None:
        with self._lock:
            self._codes[str(reference)] = code

    def code_for(self, reference: str) -> Optional[str]:
        with self._lock:
            return self._codes.get(str(reference))

    def forget(self, reference: str) -> None:
        with self._lock:
            self._codes.pop(str(reference), None)


class HttpSmsProvider:
    """Generic HTTPS webhook SMS gateway. The gateway decides how to reach the handset; this service only
    sends the destination, the single code and an opaque reference. No credentials or codes are logged."""

    def __init__(self, url: str, api_key: Optional[str] = None) -> None:
        if not url.lower().startswith("https://"):
            raise ConfigError(f"{SMS_URL_ENV} must be an https:// URL")
        self._url = url
        self._api_key = api_key

    def send(self, phone_e164: str, code: str, *, reference: str) -> None:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        payload = {"to": phone_e164, "message": f"Your BankWise verification code is {code}",
                   "reference": reference}
        try:
            # trust_env=False: never route credentials through an ambient proxy from the environment.
            with httpx.Client(timeout=5.0, trust_env=False) as client:
                response = client.post(self._url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError:
            # The exception may contain the request body (and therefore the OTP); never log or re-raise it.
            raise ServiceUnavailable() from None


def build_sms_provider(environ: Mapping[str, str], is_production: bool) -> Tuple[SmsProvider, bool]:
    """Return (provider, is_mock). Refuses to run production on the mock provider or an insecure endpoint."""
    url = (environ.get(SMS_URL_ENV) or "").strip()
    key = (environ.get(SMS_KEY_ENV) or "").strip()
    if url:
        if not url.lower().startswith("https://"):
            raise ConfigError(f"{SMS_URL_ENV} must be an https:// URL")
        if is_production and not key:
            raise ConfigError(f"{SMS_KEY_ENV} is required in production")
        if key:
            register_secret_value(key)
        return HttpSmsProvider(url, key or None), False
    if is_production:
        raise ConfigError(f"production requires an SMS provider: set {SMS_URL_ENV} and {SMS_KEY_ENV}")
    return MockSmsProvider(), True
