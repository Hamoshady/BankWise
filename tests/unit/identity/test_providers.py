import base64

import cv2
import numpy as np
import pytest

from bankwise.core.config import load_identity_config
from bankwise.core.errors import ReaderError
from bankwise.identity.reader import providers
from bankwise.identity.reader.providers import (
    build_caller,
    encode_image,
    parse_json_object,
    post_with_retry,
)

KEY = "super-secret-key-123"
IMAGE = np.zeros((100, 200, 3), dtype=np.uint8)


class FakeResponse:
    def __init__(self, status=200, payload=None, text="secret-response-body"):
        self.status_code = status
        self.payload = payload
        self.text = text

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json,
                "timeout": timeout,
            }
        )
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def base():
    return load_identity_config().reader


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda seconds: None)


def cloud(base, provider, model="m"):
    return base.model_copy(
        update={
            "provider": provider,
            "allow_cloud": True,
            "model": model,
        }
    )


def ollama(base, **overrides):
    return base.model_copy(
        update={
            "provider": "ollama",
            **overrides,
        }
    )


def test_cloud_providers_are_disabled_by_default(base):
    config = base.model_copy(
        update={"provider": "gemini", "allow_cloud": False}
    )
    with pytest.raises(ReaderError) as error:
        build_caller(config)

    assert "allow_cloud" in str(error.value)


def test_cloud_requires_api_key_in_environment(base, monkeypatch):
    monkeypatch.delenv(base.api_key_env, raising=False)

    with pytest.raises(ReaderError) as error:
        build_caller(cloud(base, "gemini"))

    assert base.api_key_env in str(error.value)


def test_ollama_must_be_loopback(base):
    config = ollama(base)

    with pytest.raises(ReaderError):
        build_caller(
            config.model_copy(
                update={"base_url": "http://example.com:11434"}
            )
        )

    caller = build_caller(
        config.model_copy(
            update={"base_url": "http://127.0.0.1:11434"}
        )
    )
    assert callable(caller)


def test_ollama_request_shape(base, monkeypatch):
    recorder = Recorder(
        FakeResponse(200, {"message": {"content": '{"a": 1}'}})
    )
    monkeypatch.setattr(providers.requests, "post", recorder)

    config = ollama(base)
    assert build_caller(config)(IMAGE, "prompt") == {"a": 1}

    call = recorder.calls[0]
    assert call["url"] == "http://localhost:11434/api/chat"
    assert call["json"]["format"] == "json"
    assert call["json"]["options"]["temperature"] == 0
    assert call["json"]["messages"][0]["images"]


def test_gemini_key_is_sent_in_header_only(base, monkeypatch):
    monkeypatch.setenv(base.api_key_env, KEY)

    body = {
        "candidates": [
            {"content": {"parts": [{"text": '{"ok": true}'}]}}
        ]
    }
    recorder = Recorder(FakeResponse(200, body))
    monkeypatch.setattr(providers.requests, "post", recorder)

    assert build_caller(
        cloud(base, "gemini", "gem-model")
    )(IMAGE, "p") == {"ok": True}

    call = recorder.calls[0]
    assert KEY not in call["url"]
    assert call["url"].startswith("https://")
    assert "gem-model" in call["url"]
    assert call["headers"]["x-goog-api-key"] == KEY


def test_anthropic_request_and_parse(base, monkeypatch):
    monkeypatch.setenv(base.api_key_env, KEY)

    body = {"content": [{"type": "text", "text": '{"v": 2}'}]}
    recorder = Recorder(FakeResponse(200, body))
    monkeypatch.setattr(providers.requests, "post", recorder)

    assert build_caller(
        cloud(base, "anthropic")
    )(IMAGE, "p") == {"v": 2}

    call = recorder.calls[0]
    assert call["headers"]["x-api-key"] == KEY
    assert call["url"] == providers.ANTHROPIC_URL


def test_groq_request_and_parse(base, monkeypatch):
    monkeypatch.setenv(base.api_key_env, KEY)

    body = {"choices": [{"message": {"content": '{"v": 3}'}}]}
    recorder = Recorder(FakeResponse(200, body))
    monkeypatch.setattr(providers.requests, "post", recorder)

    assert build_caller(
        cloud(base, "groq")
    )(IMAGE, "p") == {"v": 3}

    assert (
        recorder.calls[0]["headers"]["Authorization"]
        == f"Bearer {KEY}"
    )


def test_retries_then_succeeds(base, monkeypatch):
    recorder = Recorder(
        FakeResponse(503),
        FakeResponse(429),
        FakeResponse(200, {"ok": 1}),
    )
    monkeypatch.setattr(providers.requests, "post", recorder)

    assert post_with_retry(
        "http://localhost/x", base, {}, {}
    ) == {"ok": 1}
    assert len(recorder.calls) == 3


def test_retries_exhausted_raises_without_leaking(base, monkeypatch):
    recorder = Recorder(FakeResponse(503))
    monkeypatch.setattr(providers.requests, "post", recorder)

    with pytest.raises(ReaderError) as error:
        post_with_retry(
            "http://localhost/x",
            base,
            {"x-api-key": KEY},
            {},
        )

    assert "503" in str(error.value)
    assert KEY not in str(error.value)
    assert "secret-response-body" not in str(error.value)
    assert len(recorder.calls) == base.max_retries


def test_client_error_is_not_retried_and_hides_body(base, monkeypatch):
    recorder = Recorder(FakeResponse(400))
    monkeypatch.setattr(providers.requests, "post", recorder)

    with pytest.raises(ReaderError) as error:
        post_with_retry("http://localhost/x", base, {}, {})

    assert "secret-response-body" not in str(error.value)
    assert len(recorder.calls) == 1


def test_timeout_then_success(base, monkeypatch):
    recorder = Recorder(
        providers.requests.exceptions.Timeout(),
        FakeResponse(200, {"ok": 1}),
    )
    monkeypatch.setattr(providers.requests, "post", recorder)

    assert post_with_retry(
        "http://localhost/x", base, {}, {}
    ) == {"ok": 1}


def test_persistent_network_failure_raises(base, monkeypatch):
    recorder = Recorder(
        providers.requests.exceptions.ConnectionError()
    )
    monkeypatch.setattr(providers.requests, "post", recorder)

    with pytest.raises(ReaderError):
        post_with_retry("http://localhost/x", base, {}, {})


def test_non_json_and_malformed_shapes_raise(base, monkeypatch):
    monkeypatch.setattr(
        providers.requests,
        "post",
        Recorder(FakeResponse(200, None)),
    )

    with pytest.raises(ReaderError):
        post_with_retry("http://localhost/x", base, {}, {})

    monkeypatch.setattr(
        providers.requests,
        "post",
        Recorder(FakeResponse(200, {"unexpected": 1})),
    )

    with pytest.raises(ReaderError):
        build_caller(ollama(base))(IMAGE, "p")


def test_parse_json_object_variants():
    assert parse_json_object(
        '```json\n{"a": 1}\n```'
    ) == {"a": 1}

    assert parse_json_object(
        'Here you go: {"a": 2} thanks'
    ) == {"a": 2}

    with pytest.raises(ReaderError):
        parse_json_object("not json")

    with pytest.raises(ReaderError):
        parse_json_object("[1, 2]")


def test_encode_image_limits_size(base):
    large = np.zeros((2000, 3000, 3), dtype=np.uint8)

    decoded = cv2.imdecode(
        np.frombuffer(
            base64.b64decode(encode_image(large, base)),
            dtype=np.uint8,
        ),
        cv2.IMREAD_COLOR,
    )

    assert max(decoded.shape[:2]) <= base.max_image_side