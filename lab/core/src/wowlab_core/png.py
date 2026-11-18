"""png: RGBA rows to a PNG file's bytes, with the standard library only
(`zlib`, `crc32`; docs/LAB_PLAN.md §17.2, M15-07).

One image: colour type 6 (RGBA), 8 bits a channel, no interlace, every
row filtered with filter type 0 (none), the rows deflated as one IDAT
chunk, then IEND. The bytes are returned, not written: where they go is
the caller's choice (never the install, L1).
"""

from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_SIDE = 2**31 - 1  # the PNG specification's bound on a side
_COLOUR_RGBA = 6
_DEPTH = 8


class PngError(ValueError):
    """The pixels do not make an image: a side below 1 or over the PNG
    bound, or a byte count that is not `width * height * 4`."""


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(body, zlib.crc32(kind)))
    )


def encode(
    width: int, height: int, rgba: bytes | bytearray | memoryview, *, level: int = 6
) -> bytes:
    """The PNG of `width` x `height` RGBA pixels, row by row from the top."""
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
        raise PngError(f"an image of {width} x {height}; each side must be 1 to {MAX_SIDE}")
    pixels = memoryview(rgba).cast("B")
    line = width * 4
    if len(pixels) != line * height:
        raise PngError(f"{len(pixels)} bytes for {width} x {height} RGBA; it takes {line * height}")
    deflate = zlib.compressobj(level)
    parts: list[bytes] = []
    for y in range(height):
        parts.append(deflate.compress(b"\x00"))
        parts.append(deflate.compress(pixels[y * line : (y + 1) * line]))
    parts.append(deflate.flush())
    header = struct.pack(">IIBBBBB", width, height, _DEPTH, _COLOUR_RGBA, 0, 0, 0)
    return (
        SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", b"".join(parts))
        + _chunk(b"IEND", b"")
    )
