"""Header-level validation of uploaded images. Decides from the first bytes only (no decoding library is invoked),
so a hostile file cannot reach an image decoder before it has passed the type, size and pixel-count limits.

It proves the file LOOKS like a JPEG or PNG of acceptable dimensions. It does not prove the data is a valid image
or free of tricks inside the compressed stream: the decoder that eventually opens it must still treat it as untrusted."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Tuple

from bankwise.core.errors import InvalidInputError

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_JPEG_SEGMENTS = 1000


class ImageRejected(InvalidInputError):
    """Public message is the generic validation error; `reason` is a short code for audit metadata and logs."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"image rejected: {reason}")
        self.reason = reason


@dataclass(frozen=True)
class ImageInfo:
    kind: str
    width: int
    height: int
    size_bytes: int

    @property
    def pixels(self) -> int:
        return self.width * self.height


def _png_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 24 or data[12:16] != b"IHDR" or struct.unpack(">I", data[8:12])[0] != 13:
        return None
    return struct.unpack(">II", data[16:24])


def _jpeg_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    i, n = 2, len(data)
    for _ in range(_MAX_JPEG_SEGMENTS):
        if i + 2 > n or data[i] != 0xFF:
            return None
        while i < n and data[i] == 0xFF:                          # fill bytes
            i += 1
        if i >= n:
            return None
        marker = data[i]
        i += 1
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:      # markers without a length
            continue
        if marker in (0xD9, 0xDA):                                # end of image / start of scan before any frame header
            return None
        if i + 2 > n:
            return None
        length = (data[i] << 8) | data[i + 1]
        if length < 2:
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):       # start-of-frame
            if length < 8 or i + 7 > n:
                return None
            return (data[i + 5] << 8) | data[i + 6], (data[i + 3] << 8) | data[i + 4]
        i += length
    return None


def inspect_image(data: object, max_bytes: int, max_pixels: int, min_side: int) -> ImageInfo:
    """Return the image kind and dimensions or raise `ImageRejected(reason)`. Reasons: not_bytes, empty, too_large,
    unsupported_type, unreadable_dimensions, too_many_pixels, too_small."""
    if not isinstance(data, (bytes, bytearray)):
        raise ImageRejected("not_bytes")                         # a str/Path is never accepted: no file paths from callers
    if not data:
        raise ImageRejected("empty")
    if len(data) > max_bytes:
        raise ImageRejected("too_large")
    blob = bytes(data)
    if blob.startswith(b"\xff\xd8\xff"):
        kind, dims = "jpeg", _jpeg_dimensions(blob)
    elif blob.startswith(_PNG_SIGNATURE):
        kind, dims = "png", _png_dimensions(blob)
    else:
        raise ImageRejected("unsupported_type")
    if dims is None or dims[0] < 1 or dims[1] < 1:
        raise ImageRejected("unreadable_dimensions")
    width, height = dims
    if width * height > max_pixels:
        raise ImageRejected("too_many_pixels")
    if min(width, height) < min_side:
        raise ImageRejected("too_small")
    return ImageInfo(kind, width, height, len(blob))
