import asyncio

import pytest

from bankwise.api.transport import TransportMiddleware


def test_chunked_upload_limit_is_enforced_without_content_length():
    async def run():
        messages = []
        async def app(scope, receive, send):
            await receive()
            await receive()
            pytest.fail("oversized stream reached application")
        chunks = iter([{"type": "http.request", "body": b"a" * 8, "more_body": True},
                       {"type": "http.request", "body": b"b" * 8, "more_body": False}])
        async def receive():
            return next(chunks)
        async def send(message):
            messages.append(message)
        await TransportMiddleware(app, max_body_bytes=10)(
            {"type": "http", "path": "/api/v1/identity/verify", "headers": []}, receive, send)
        assert messages[0]["status"] == 413
        assert b"aaaaaaaa" not in messages[1]["body"]
    asyncio.run(run())


def test_transport_capacity_is_bounded():
    async def run():
        middleware = TransportMiddleware(None, max_body_bytes=10, max_inflight=1)
        middleware.inflight = 1
        messages = []
        async def receive():
            pytest.fail("overload should not read body")
        async def send(message):
            messages.append(message)
        await middleware({"type": "http", "path": "/api/v1/identity/verify", "headers": []}, receive, send)
        assert messages[0]["status"] == 429
    asyncio.run(run())
