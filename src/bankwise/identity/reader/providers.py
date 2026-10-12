import base64
import json
import os
import re
import time
from typing import Callable
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

from bankwise.core.config import ReaderConfig
from bankwise.core.errors import ReaderError

Caller = Callable[[np.ndarray, str], dict]
RETRY_STATUSES = (429, 500, 502, 503, 504)
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_OUTPUT_TOKENS = 2000


def encode_image(image: np.ndarray, cfg: ReaderConfig) -> str:
    height, width = image.shape[:2]
    scale = cfg.max_image_side / max(height, width)
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, cfg.jpeg_quality])
    if not ok:
        raise ReaderError("image encoding failed")
    return base64.b64encode(buffer).decode("ascii")


def parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", str(text).strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    try:
        value = json.loads(match.group(0) if match else cleaned)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ReaderError("reader returned invalid json") from exc
    if not isinstance(value, dict):
        raise ReaderError("reader returned a non-object")
    return value


def pick(data, *path):
    current = data
    try:
        for key in path:
            current = current[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReaderError("unexpected reader response shape") from exc
    return current


def post_with_retry(url: str, cfg: ReaderConfig, headers: dict, payload: dict) -> dict:
    delay = cfg.retry_initial_delay
    for attempt in range(1, cfg.max_retries + 1):
        last = attempt == cfg.max_retries
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=cfg.timeout_seconds
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if last:
                raise ReaderError("reader network failure")
            time.sleep(delay)
            delay = min(delay * 2, cfg.retry_max_delay)
            continue
        if response.status_code in RETRY_STATUSES and not last:
            time.sleep(delay)
            delay = min(delay * 2, cfg.retry_max_delay)
            continue
        if response.status_code >= 400:
            raise ReaderError(f"reader http error {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ReaderError("reader returned a non-json body") from exc
    raise ReaderError("reader retries exhausted")


def ollama_text(cfg: ReaderConfig, image_b64: str, prompt: str) -> str:
    payload = {
        "model": cfg.model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
    }
    data = post_with_retry(cfg.base_url.rstrip("/") + "/api/chat", cfg, {}, payload)
    return pick(data, "message", "content")


def gemini_text(cfg: ReaderConfig, key: str, image_b64: str, prompt: str) -> str:
    payload = {
        "contents": [
            {"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": image_b64}}, {"text": prompt}]}
        ],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    data = post_with_retry(GEMINI_URL.format(model=cfg.model), cfg, {"x-goog-api-key": key}, payload)
    return pick(data, "candidates", 0, "content", "parts", 0, "text")


def anthropic_text(cfg: ReaderConfig, key: str, image_b64: str, prompt: str) -> str:
    payload = {
        "model": cfg.model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    data = post_with_retry(ANTHROPIC_URL, cfg, headers, payload)
    blocks = pick(data, "content")
    return "".join(block.get("text", "") for block in blocks if isinstance(block, dict))


def groq_text(cfg: ReaderConfig, key: str, image_b64: str, prompt: str) -> str:
    payload = {
        "model": cfg.model,
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }
        ],
    }
    data = post_with_retry(GROQ_URL, cfg, {"Authorization": f"Bearer {key}"}, payload)
    return pick(data, "choices", 0, "message", "content")


CLOUD_PROVIDERS = {"gemini": gemini_text, "anthropic": anthropic_text, "groq": groq_text}


def build_caller(cfg: ReaderConfig) -> Caller:
    if cfg.provider == "ollama":
        if urlparse(cfg.base_url).hostname not in LOOPBACK_HOSTS:
            raise ReaderError("ollama base_url must be a loopback address")

        def local_caller(image: np.ndarray, prompt: str) -> dict:
            return parse_json_object(ollama_text(cfg, encode_image(image, cfg), prompt))

        return local_caller
    if not cfg.allow_cloud:
        raise ReaderError(
            f"cloud provider {cfg.provider} is disabled: set reader.allow_cloud to true to send card images to it"
        )
    key = os.environ.get(cfg.api_key_env)
    if not key:
        raise ReaderError(f"missing environment variable {cfg.api_key_env}")
    send = CLOUD_PROVIDERS[cfg.provider]

    def cloud_caller(image: np.ndarray, prompt: str) -> dict:
        return parse_json_object(send(cfg, key, encode_image(image, cfg), prompt))

    return cloud_caller