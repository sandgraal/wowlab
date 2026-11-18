"""Constructed BLP2 files for the `blp` tests (M15-07; labelled
`constructed` where used): the header and palette laid out as the real
files are, and BC1, DXT3 and DXT5 blocks packed from index grids."""

from __future__ import annotations

import struct

from wowlab_core import blp


def blp_file(
    width: int,
    height: int,
    encoding: int,
    alpha_depth: int,
    alpha_type: int,
    body: bytes,
    *,
    palette: bytes = bytes(1024),
    has_mips: int = 0,
    stored: int | None = None,
    offset: int | None = None,
    version: int = 1,
    magic: bytes = b"BLP2",
) -> bytes:
    """A BLP2 file laid out as the real ones are: the 148-byte header, the
    1024-byte palette, mip 0 at 1172 (`docs/LAB_FORMATS.md` §11)."""
    offsets = [0] * 16
    sizes = [0] * 16
    offsets[0] = 148 + 1024 if offset is None else offset
    sizes[0] = len(body) if stored is None else stored
    head = struct.pack(
        "<4sIBBBBII16I16I",
        magic,
        version,
        encoding,
        alpha_depth,
        alpha_type,
        has_mips,
        width,
        height,
        *offsets,
        *sizes,
    )
    assert len(head) == blp.HEADER_SIZE and len(palette) == blp.PALETTE_SIZE
    return head + palette + body


def bc1(c0: int, c1: int, rows: list[list[int]]) -> bytes:
    """A BC1 colour block: two RGB565 endpoints, then 2-bit indices, the
    first pixel of each row in the lowest bits."""
    word = 0
    for r, row in enumerate(rows):
        for x, i in enumerate(row):
            word |= i << (2 * (4 * r + x))
    return struct.pack("<HHI", c0, c1, word)


def bc4_alpha(a0: int, a1: int, rows: list[list[int]]) -> bytes:
    """A DXT5 alpha block: two endpoints, then 3-bit indices (48 bits)."""
    word = 0
    for r, row in enumerate(rows):
        for x, i in enumerate(row):
            word |= i << (3 * (4 * r + x))
    return bytes((a0, a1)) + word.to_bytes(6, "little")


def explicit_alpha(rows: list[list[int]]) -> bytes:
    """A DXT3 alpha block: sixteen 4-bit values, the low nibble first."""
    out = bytearray()
    for row in rows:
        out.append(row[0] | row[1] << 4)
        out.append(row[2] | row[3] << 4)
    return bytes(out)


def px(*colours: tuple[int, int, int, int]) -> bytes:
    return b"".join(bytes(c) for c in colours)


RED = 0xF800
BLUE = 0x001F
GREEN = 0x07E0
GREY = (16 << 11) | (32 << 5) | 16  # widened by bit repetition: 132, 130, 132

PATTERN = [[0, 1, 2, 3], [3, 2, 1, 0], [0, 0, 0, 0], [1, 1, 1, 1]]
