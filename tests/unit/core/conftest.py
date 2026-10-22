import logging
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))          # makes `bankwise` importable next to `id_ocr`

ENV_VARS = ("BANKWISE_ENV", "BANKWISE_CONFIG_DIR", "BANKWISE_LOG_LEVEL", "BANKWISE_LOG_FORMAT",
            "BANKWISE_JWT_SECRET", "BANKWISE_PII_HMAC_KEY")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    from bankwise.core.pii import clear_registered_secrets

    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BANKWISE_ENV", "development")
    clear_registered_secrets()
    yield
    clear_registered_secrets()
    logger = logging.getLogger("bankwise")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
