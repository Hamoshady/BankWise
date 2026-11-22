"""Bound uploads before parsing, never spool ID images to temporary files."""
import logging

from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from bankwise.api.errors import error_response
from bankwise.core.errors import BankWiseError, InvalidInputError, PayloadTooLarge
from bankwise.core.identifiers import new_opaque_uuid
from bankwise.core.logging import correlation_scope


class TransportMiddleware:
    def __init__(self, app, max_body_bytes, max_inflight=8):
        self.app, self.max_body_bytes, self.max_inflight = app, max_body_bytes, max_inflight
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        with correlation_scope(str(new_opaque_uuid())) as cid:
            started = False
            async def safe_send(message):
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                    message["headers"] = list(message.get("headers", [])) + [
                        (b"x-request-id", cid.encode()), (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
                await send(message)

            if self.inflight >= self.max_inflight:
                from bankwise.core.errors import RateLimitExceeded
                return await error_response(RateLimitExceeded(retry_after_seconds=1))(scope, receive, safe_send)
            self.inflight += 1
            try:
                limit = self.max_body_bytes if scope["path"] in {"/api/v1/identity/verify", "/api/v1/identity/extract", "/api/v1/identity/reviews", "/api/v1/onboarding/images"} else 16 * 1024
                lengths = [v for k, v in scope["headers"] if k.lower() == b"content-length"]
                if lengths:
                    if len(lengths) != 1 or not lengths[0].isdigit():
                        raise InvalidInputError()
                    if int(lengths[0]) > limit:
                        raise PayloadTooLarge()
                received = 0
                async def bounded_receive():
                    nonlocal received
                    message = await receive()
                    if message["type"] == "http.request":
                        received += len(message.get("body", b""))
                        if received > limit:
                            raise PayloadTooLarge()
                    return message
                await self.app(scope, bounded_receive, safe_send)
            except BankWiseError as error:
                if not started:
                    await error_response(error)(scope, receive, safe_send)
            except Exception:
                # Do not log exception text, tracebacks, request bodies or SQL parameters.
                logging.getLogger("bankwise.api").error("request failed")
                if not started:
                    await error_response(BankWiseError())(scope, receive, safe_send)
            finally:
                self.inflight -= 1


def parse_images(content_type: str, body: bytes, image_limit: int):
    """Exactly two named image parts, bounded headers and bytes, all in memory."""
    try:
        media_type, options = parse_options_header(content_type)
        boundary = options.get(b"boundary", b"")
        if media_type != b"multipart/form-data" or not 1 <= len(boundary) <= 70:
            raise InvalidInputError()
        images, headers = {}, {}
        field, value, data = bytearray(), bytearray(), bytearray()
        name = None
        ended = False
        parts = 0

        def part_begin():
            nonlocal parts
            parts += 1
            if parts > 2:
                raise InvalidInputError()
            headers.clear()
            data.clear()

        def header_field(blob, start, end):
            field.extend(blob[start:end])

        def header_value(blob, start, end):
            value.extend(blob[start:end])

        def header_end():
            key = bytes(field).lower()
            if key in headers:
                raise InvalidInputError()
            headers[key] = bytes(value)
            field.clear()
            value.clear()

        def headers_finished():
            nonlocal name
            disposition, attrs = parse_options_header(headers.get(b"content-disposition", b""))
            name = attrs.get(b"name")
            if disposition != b"form-data" or name not in {b"front", b"back"} or name in images:
                raise InvalidInputError()
            if headers.get(b"content-type") not in {b"image/jpeg", b"image/png"}:
                raise InvalidInputError()

        def part_data(blob, start, end):
            if len(data) + end - start > image_limit:
                raise PayloadTooLarge()
            data.extend(blob[start:end])

        def part_end():
            images[name] = bytes(data)

        def end():
            nonlocal ended
            ended = True

        parser = MultipartParser(boundary, callbacks={
            "on_part_begin": part_begin, "on_header_field": header_field, "on_header_value": header_value,
            "on_header_end": header_end, "on_headers_finished": headers_finished, "on_part_data": part_data,
            "on_part_end": part_end, "on_end": end}, max_header_count=4, max_header_size=2048)
        parser.write(body)
        parser.finalize()
        if not ended or set(images) != {b"front", b"back"}:
            raise InvalidInputError()
        return images[b"front"], images[b"back"]
    except (MultipartParseError, ValueError, TypeError, KeyError):
        raise InvalidInputError() from None
