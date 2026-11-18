"""`png` (M15-07): RGBA to PNG bytes, re-read here by a small parser on the
standard library (signature, chunk lengths and CRCs, IHDR, the inflated
rows and their filter bytes), and a decoded constructed BLP written and
read back."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import pytest
from _blp_build import BLUE, PATTERN, RED, bc1, blp_file

from wowlab_core import blp, png


@dataclass
class Png:
    width: int
    height: int
    depth: int
    colour: int
    interlace: int
    chunks: list[bytes]
    rgba: bytes


def read_png(data: bytes) -> Png:
    """A stdlib PNG reader for 8-bit RGBA without interlace, filter 0 only
    (what `png.encode` writes); every CRC checked."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    at = 8
    chunks: list[bytes] = []
    idat = b""
    header = b""
    while at < len(data):
        (length,) = struct.unpack_from(">I", data, at)
        kind = data[at + 4 : at + 8]
        body = data[at + 8 : at + 8 + length]
        (crc,) = struct.unpack_from(">I", data, at + 8 + length)
        assert crc == zlib.crc32(kind + body), kind
        chunks.append(kind)
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            idat += body
        at += 12 + length
    assert at == len(data)
    width, height, depth, colour, method, filtering, interlace = struct.unpack(">IIBBBBB", header)
    assert (method, filtering) == (0, 0)
    raw = zlib.decompress(idat)
    line = width * 4
    assert len(raw) == height * (line + 1)
    rows = []
    for y in range(height):
        start = y * (line + 1)
        assert raw[start] == 0, "filter type 0"
        rows.append(raw[start + 1 : start + 1 + line])
    return Png(width, height, depth, colour, interlace, chunks, b"".join(rows))


def test_rgba_round_trips_through_a_stdlib_reader() -> None:
    rgba = bytes(range(256)) * 3  # 192 pixels
    data = png.encode(16, 12, rgba)
    image = read_png(data)
    assert (image.width, image.height, image.depth, image.colour, image.interlace) == (
        16,
        12,
        8,
        6,
        0,
    )
    assert image.chunks == [b"IHDR", b"IDAT", b"IEND"]
    assert image.rgba == rgba


def test_one_pixel_and_a_memoryview() -> None:
    image = read_png(png.encode(1, 1, memoryview(b"\x01\x02\x03\x04")))
    assert (image.width, image.height, image.rgba) == (1, 1, b"\x01\x02\x03\x04")


def test_a_decoded_constructed_blp_written_and_read_back_constructed() -> None:
    texture = blp.decode(blp_file(4, 4, 2, 0, 0, bc1(RED, BLUE, PATTERN)))
    image = read_png(png.encode(texture.width, texture.height, texture.rgba))
    assert (image.width, image.height) == (4, 4)
    assert image.rgba == texture.rgba


@pytest.mark.parametrize(
    ("width", "height", "size", "words"),
    [
        (0, 1, 0, "each side"),
        (1, 0, 0, "each side"),
        (2**31, 1, 4, "each side"),
        (2, 2, 15, "it takes 16"),
        (2, 2, 17, "it takes 16"),
    ],
)
def test_pixels_that_do_not_make_the_image_are_refused(
    width: int, height: int, size: int, words: str
) -> None:
    with pytest.raises(png.PngError, match=words):
        png.encode(width, height, bytes(size))
