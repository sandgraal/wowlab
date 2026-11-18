"""blp: a BLP2 texture's first mip as RGBA rows (docs/LAB_PLAN.md §17.2,
docs/LAB_FORMATS.md §11, ADR-0033, M15-07).

The file is bytes the caller already holds (`casc.CascSource.read` gives
them); this module opens nothing and writes nothing.

**The header.** `BLP2`, a `uint32` version (1), then four bytes: the colour
encoding (1 palette, 2 DXT, 3 BGRA), the alpha depth (0, 1, 4 or 8), the
alpha type (the wiki's "preferred format": 0 DXT1, 1 DXT3, 7 DXT5, 11
BC5, …) and has-mips; the width and height (`uint32`); sixteen mip
offsets and sixteen mip sizes (`uint32`): 148 bytes. A 1024-byte palette
of 256 BGRX colours follows (read only for the palette encoding).

**What is decoded.** Mip 0 only:

- *DXT1, DXT3, DXT5* (encoding 2). Which one is chosen by two published
  rules that disagree: the wiki's (the alpha type alone: 0 DXT1, 1 DXT3,
  7 DXT5) and wow.export's (`alphaDepth > 1 ? (alphaType == 7 ? DXT5 :
  DXT3) : DXT1`). A texture where the two give different answers is
  refused with `BlpRuleConflict`, both values and both answers named,
  until a real texture settles which rule the client follows
  (**[verify]**); BC5 (alpha type 11) and any other alpha type are refused
  with `BlpUnsupportedError`. The colour block is the standard BC1 block
  (two RGB565 endpoints, sixteen 2-bit indices, row by row, the low bits
  first): in DXT1, `c0 > c1` gives four opaque colours and `c0 <= c1`
  three, the fourth transparent black; in DXT3 and DXT5 always four. An
  interpolated channel is `(2a + b) // 3` or `(a + b) // 2`, rounded
  down; RGB565 is widened by repeating the top bits. DXT3 alpha is sixteen
  explicit 4-bit values (low nibble first) times 17; DXT5 alpha is two
  endpoints and sixteen 3-bit indices (eight values when `a0 > a1`, else
  six and then 0 and 255), rounded down. A side that is not a multiple of
  four is decoded in whole blocks and cut.
- *Palette* (encoding 1). One index byte per pixel into the BGRX palette,
  then the alpha: none (depth 0, opaque), one bit per pixel, the lowest
  bit first (1), one nibble per pixel, the low nibble first, times 17 (4;
  the wiki is incomplete here, **[verify]**), or one byte per pixel (8).
- *BGRA* (encoding 3). Four bytes per pixel, B G R A; with alpha depth 0
  the alpha byte is ignored and the pixel is opaque; depth 8 keeps it.
  Any other depth is refused.

**What is trusted.** Nothing that sizes an allocation. The width and height
are each held to `MAX_DIMENSION` and their product to `MAX_PIXELS` before
anything is decoded (`BlpTooLargeError`), so the largest output is
`MAX_PIXELS * 4` bytes (64 MiB). Mip 0's size is computed from the width,
the height and the encoding, never taken from the stored mip size (some
stored sizes are wrong, §11), and the offset and that computed size must
lie inside the file (`BlpMalformedError`) before the output is allocated;
so the output is at most eight times the input (DXT1's ratio) and never
more than the cap.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from itertools import chain
from operator import add
from typing import Literal

from pydantic import BaseModel, ConfigDict

MAGIC = b"BLP2"
VERSION = 1
HEADER_SIZE = 148
PALETTE_SIZE = 1024  # 256 BGRX colours, right after the header
MIP_SLOTS = 16

ENCODING_PALETTE = 1
ENCODING_DXT = 2
ENCODING_BGRA = 3

ALPHA_TYPE_DXT1 = 0
ALPHA_TYPE_DXT3 = 1
ALPHA_TYPE_DXT5 = 7
ALPHA_TYPE_BC5 = 11

ALPHA_DEPTHS = (0, 1, 4, 8)

MAX_DIMENSION = 8192
"""The widest or tallest texture decoded; past it the header is refused."""
MAX_PIXELS = 4096 * 4096
"""The most pixels decoded: the output is at most 64 MiB of RGBA."""

_HEADER = struct.Struct("<4sIBBBBII16I16I")

Format = Literal["palette", "dxt1", "dxt3", "dxt5", "bgra"]
_BLOCK_BYTES: dict[str, int] = {"dxt1": 8, "dxt3": 16, "dxt5": 16}


# ─── errors ──────────────────────────────────────────────────────────────────


class BlpError(Exception):
    """Base class for everything this module raises on purpose."""


class BlpMalformedError(BlpError):
    """Not a BLP2 this reader can read as one: too short, the wrong magic,
    a zero side, or mip 0 not inside the file."""


class BlpTooLargeError(BlpError):
    """A side over `MAX_DIMENSION` or a pixel count over `MAX_PIXELS`.
    Refused from the header, before anything is allocated."""

    def __init__(self, message: str, width: int, height: int) -> None:
        super().__init__(message)
        self.width = width
        self.height = height


class BlpUnsupportedError(BlpError):
    """A well-formed header this decoder does not decode: another version,
    an unknown colour encoding, BC5 or another alpha type, or an alpha depth
    the encoding does not take. The three header values are kept."""

    def __init__(self, message: str, encoding: int, alpha_depth: int, alpha_type: int) -> None:
        super().__init__(message)
        self.encoding = encoding
        self.alpha_depth = alpha_depth
        self.alpha_type = alpha_type


class BlpRuleConflict(BlpUnsupportedError):  # noqa: N818 - says what it is, like casc's
    """A DXT texture for which the wiki's rule and wow.export's rule pick
    different encodings (`wiki`, `wow_export`). Refused, not guessed
    (§17.2, **[verify]**)."""

    def __init__(
        self, message: str, alpha_depth: int, alpha_type: int, wiki: str | None, wow_export: str
    ) -> None:
        super().__init__(message, ENCODING_DXT, alpha_depth, alpha_type)
        self.wiki = wiki
        self.wow_export = wow_export


# ─── models ──────────────────────────────────────────────────────────────────


class BlpHeader(BaseModel):
    """The 148-byte header as stored. `mip_count` is how many mip slots,
    from the first, have a nonzero offset and size."""

    model_config = ConfigDict(frozen=True)

    version: int
    encoding: int
    alpha_depth: int
    alpha_type: int
    has_mips: int
    width: int
    height: int
    mip_offsets: tuple[int, ...]
    mip_sizes: tuple[int, ...]

    @property
    def mip_count(self) -> int:
        n = 0
        for offset, size in zip(self.mip_offsets, self.mip_sizes, strict=True):
            if not offset or not size:
                break
            n += 1
        return n


class BlpImage(BaseModel):
    """Mip 0 of a texture: `width * height` pixels of RGBA, row by row from
    the top, four bytes each. `mip0_size` is the size computed for mip 0
    (`stored_mip0_size` is what the header says; they may differ)."""

    model_config = ConfigDict(frozen=True)

    header: BlpHeader
    format: Format
    width: int
    height: int
    mip0_size: int
    rgba: bytes

    @property
    def stored_mip0_size(self) -> int:
        return self.header.mip_sizes[0]

    def rows(self) -> list[bytes]:
        """The pixel rows, top first, `width * 4` bytes each."""
        step = self.width * 4
        return [self.rgba[y * step : (y + 1) * step] for y in range(self.height)]


# ─── the header and the DXT rules ────────────────────────────────────────────


def wiki_rule(alpha_type: int) -> str | None:
    """The wiki's DXT choice: the alpha type alone. None for any other type."""
    return {ALPHA_TYPE_DXT1: "dxt1", ALPHA_TYPE_DXT3: "dxt3", ALPHA_TYPE_DXT5: "dxt5"}.get(
        alpha_type
    )


def wow_export_rule(alpha_depth: int, alpha_type: int) -> str:
    """wow.export's DXT choice: the alpha depth first, then the type."""
    if alpha_depth > 1:
        return "dxt5" if alpha_type == ALPHA_TYPE_DXT5 else "dxt3"
    return "dxt1"


def parse_header(data: bytes | bytearray | memoryview) -> BlpHeader:
    """The header of a BLP2 file; refused when too short or not `BLP2`."""
    if len(data) < HEADER_SIZE:
        raise BlpMalformedError(f"{len(data)} bytes; a BLP2 header is {HEADER_SIZE}")
    fields = _HEADER.unpack_from(data, 0)
    magic = bytes(fields[0])
    if magic != MAGIC:
        raise BlpMalformedError(f"magic {magic!r}; a BLP2 file starts with {MAGIC!r}")
    return BlpHeader(
        version=fields[1],
        encoding=fields[2],
        alpha_depth=fields[3],
        alpha_type=fields[4],
        has_mips=fields[5],
        width=fields[6],
        height=fields[7],
        mip_offsets=tuple(fields[8 : 8 + MIP_SLOTS]),
        mip_sizes=tuple(fields[8 + MIP_SLOTS :]),
    )


def _unsupported(header: BlpHeader, why: str) -> BlpUnsupportedError:
    return BlpUnsupportedError(
        f"{why} (encoding {header.encoding}, alpha depth {header.alpha_depth}, "
        f"alpha type {header.alpha_type})",
        header.encoding,
        header.alpha_depth,
        header.alpha_type,
    )


def format_of(header: BlpHeader) -> Format:
    """What mip 0 is stored as, or the typed refusal: another version, an
    unknown encoding or alpha depth, BC5, an unknown alpha type, or a DXT
    texture the two published rules disagree on."""
    if header.version != VERSION:
        raise _unsupported(header, f"BLP2 version {header.version}; only {VERSION} is read")
    depth, kind = header.alpha_depth, header.alpha_type
    if header.encoding == ENCODING_PALETTE:
        if depth not in ALPHA_DEPTHS:
            raise _unsupported(header, f"palette alpha depth {depth}")
        return "palette"
    if header.encoding == ENCODING_BGRA:
        if depth not in (0, 8):
            raise _unsupported(header, f"BGRA alpha depth {depth}")
        return "bgra"
    if header.encoding != ENCODING_DXT:
        raise _unsupported(header, f"colour encoding {header.encoding}")
    if kind == ALPHA_TYPE_BC5:
        raise _unsupported(header, "BC5 (alpha type 11) is not decoded")
    if depth not in ALPHA_DEPTHS:
        raise _unsupported(header, f"DXT alpha depth {depth}")
    wiki = wiki_rule(kind)
    if wiki is None:
        raise _unsupported(header, f"DXT alpha type {kind}")
    other = wow_export_rule(depth, kind)
    if wiki != other:
        raise BlpRuleConflict(
            f"alpha depth {depth} with alpha type {kind}: the wiki's rule reads "
            f"{wiki.upper()}, wow.export's reads {other.upper()}; refused until a real "
            "texture settles it (LAB_PLAN §17.2, [verify])",
            depth,
            kind,
            wiki,
            other,
        )
    return "dxt1" if wiki == "dxt1" else "dxt3" if wiki == "dxt3" else "dxt5"


def check_size(width: int, height: int) -> None:
    """The allocation bound: both sides at least 1 and at most
    `MAX_DIMENSION`, and at most `MAX_PIXELS` pixels."""
    if width < 1 or height < 1:
        raise BlpMalformedError(f"a texture of {width} x {height}; both sides must be at least 1")
    if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise BlpTooLargeError(
            f"a texture of {width} x {height}; the cap is {MAX_DIMENSION} a side and "
            f"{MAX_PIXELS:,} pixels",
            width,
            height,
        )


def mip0_size(fmt: Format, width: int, height: int, alpha_depth: int) -> int:
    """Mip 0's size in bytes, computed (the stored size is not trusted)."""
    if fmt in _BLOCK_BYTES:
        return ((width + 3) // 4) * ((height + 3) // 4) * _BLOCK_BYTES[fmt]
    pixels = width * height
    if fmt == "bgra":
        return pixels * 4
    return pixels + (pixels * alpha_depth + 7) // 8


# ─── decoding ────────────────────────────────────────────────────────────────


def decode(data: bytes | bytearray | memoryview) -> BlpImage:
    """Mip 0 of a BLP2 file as RGBA. Refusals: `BlpMalformedError`,
    `BlpTooLargeError`, `BlpUnsupportedError`, `BlpRuleConflict`."""
    view = memoryview(data).cast("B")
    header = parse_header(view)
    fmt = format_of(header)
    width, height = header.width, header.height
    check_size(width, height)
    size = mip0_size(fmt, width, height, header.alpha_depth)
    offset = header.mip_offsets[0]
    if fmt == "palette" and len(view) < HEADER_SIZE + PALETTE_SIZE:
        raise BlpMalformedError(
            f"{len(view)} bytes; a palette texture needs {HEADER_SIZE + PALETTE_SIZE} "
            "for the header and the palette"
        )
    if offset < HEADER_SIZE or offset + size > len(view):
        raise BlpMalformedError(
            f"mip 0 at offset {offset}, {size} bytes computed for {width} x {height} "
            f"{fmt.upper()}: not inside the file's {len(view)} bytes after the header"
        )
    mip = bytes(view[offset : offset + size])
    if fmt == "palette":
        palette = bytes(view[HEADER_SIZE : HEADER_SIZE + PALETTE_SIZE])
        rgba = _palette(mip, palette, width * height, header.alpha_depth)
    elif fmt == "bgra":
        rgba = _bgra(mip, header.alpha_depth)
    else:
        rgba = _dxt(mip, fmt, width, height)
    return BlpImage(
        header=header, format=fmt, width=width, height=height, mip0_size=size, rgba=rgba
    )


def _bgra(mip: bytes, depth: int) -> bytes:
    out = bytearray(mip)
    out[0::4] = mip[2::4]
    out[2::4] = mip[0::4]
    if depth == 0:
        out[3::4] = b"\xff" * (len(mip) // 4)
    return bytes(out)


def _table(fn: Callable[[int], int]) -> bytes:
    return bytes(fn(b) for b in range(256))


_NIBBLE_LO = _table(lambda b: (b & 0x0F) * 17)
_NIBBLE_HI = _table(lambda b: (b >> 4) * 17)
_BITS = tuple(bytes(255 if b >> k & 1 else 0 for b in range(256)) for k in range(8))


def _palette(mip: bytes, palette: bytes, pixels: int, depth: int) -> bytes:
    indices = mip[:pixels]
    alpha = mip[pixels:]
    out = bytearray(pixels * 4)
    out[0::4] = indices.translate(palette[2::4])  # BGRX: R is the third byte
    out[1::4] = indices.translate(palette[1::4])
    out[2::4] = indices.translate(palette[0::4])
    if depth == 0:
        out[3::4] = b"\xff" * pixels
    elif depth == 8:
        out[3::4] = alpha
    elif depth == 4:
        spread = bytearray(len(alpha) * 2)
        spread[0::2] = alpha.translate(_NIBBLE_LO)
        spread[1::2] = alpha.translate(_NIBBLE_HI)
        out[3::4] = spread[:pixels]
    else:  # depth 1: the lowest bit first
        spread = bytearray(len(alpha) * 8)
        for k in range(8):
            spread[k::8] = alpha.translate(_BITS[k])
        out[3::4] = spread[:pixels]
    return bytes(out)


_R5 = [(v << 3) | (v >> 2) for v in range(32)]
_G6 = [(v << 2) | (v >> 4) for v in range(64)]
_PAIR = tuple(bytes((b >> (2 * j)) & 3 for b in range(256)) for j in range(4))
_TRIPLE = [bytes((v >> (3 * j)) & 7 for j in range(4)) for v in range(4096)]


def _colours(key: int, four: bool) -> tuple[bytes, bytes, bytes, bytes]:
    """The four RGBA colours of a BC1 block whose two endpoints are `key`
    (`c0` in the low 16 bits)."""
    c0, c1 = key & 0xFFFF, key >> 16
    r0, g0, b0 = _R5[c0 >> 11], _G6[(c0 >> 5) & 63], _R5[c0 & 31]
    r1, g1, b1 = _R5[c1 >> 11], _G6[(c1 >> 5) & 63], _R5[c1 & 31]
    if four or c0 > c1:
        c2 = bytes(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255))
        c3 = bytes(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255))
    else:
        c2 = bytes(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255))
        c3 = b"\x00\x00\x00\x00"
    return bytes((r0, g0, b0, 255)), bytes((r1, g1, b1, 255)), c2, c3


def _alphas(key: int) -> tuple[int, ...]:
    """The eight alpha values of a DXT5 block whose endpoints are `key`
    (`a0` in the low byte)."""
    a0, a1 = key & 0xFF, key >> 8
    if a0 > a1:
        return (a0, a1, *(((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)))
    return (a0, a1, *(((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)), 0, 255)


def _dxt(mip: bytes, fmt: Format, width: int, height: int) -> bytes:
    """Decode whole blocks row by row of blocks into padded rows, then cut
    to `width` x `height`. Each block's palette is built once per distinct
    endpoint pair and the pixels are gathered from it."""
    block = _BLOCK_BYTES[fmt]
    across = (width + 3) // 4
    down = (height + 3) // 4
    row_bytes = across * block
    four = fmt != "dxt1"
    colour_at = 0 if fmt == "dxt1" else 8
    ends = struct.Struct(f"<{colour_at}xI{block - colour_at - 4}x")
    cache: dict[int, tuple[bytes, bytes, bytes, bytes]] = {}
    alpha_cache: dict[int, tuple[int, ...]] = {}
    base4 = [4 * (i // 4) for i in range(4 * across)]
    base8 = [8 * (i // 4) for i in range(4 * across)]
    spread = bytearray(4 * across)
    out = bytearray(width * height * 4)
    line = width * 4
    for by in range(down):
        blocks = mip[by * row_bytes : (by + 1) * row_bytes]
        keys = [k for (k,) in ends.iter_unpack(blocks)]
        for k in set(keys).difference(cache):
            cache[k] = _colours(k, four)
        colours = list(chain.from_iterable(map(cache.__getitem__, keys)))
        alpha_lists: list[int] = []
        words: list[int] = []
        if fmt == "dxt5":
            akeys = [blocks[o] | blocks[o + 1] << 8 for o in range(0, len(blocks), block)]
            for k in set(akeys).difference(alpha_cache):
                alpha_cache[k] = _alphas(k)
            alpha_lists = list(chain.from_iterable(map(alpha_cache.__getitem__, akeys)))
            words = [
                int.from_bytes(blocks[o + 2 : o + 8], "little")
                for o in range(0, len(blocks), block)
            ]
        for r in range(4):
            y = by * 4 + r
            if y >= height:
                break
            index_bytes = blocks[colour_at + 4 + r :: block]
            for j in range(4):
                spread[j::4] = index_bytes.translate(_PAIR[j])
            pixels = b"".join(map(colours.__getitem__, map(add, base4, spread)))
            row = bytearray(pixels[:line])
            if fmt == "dxt3":
                alpha = bytearray(4 * across)
                lo = blocks[2 * r :: block]
                hi = blocks[2 * r + 1 :: block]
                alpha[0::4] = lo.translate(_NIBBLE_LO)
                alpha[1::4] = lo.translate(_NIBBLE_HI)
                alpha[2::4] = hi.translate(_NIBBLE_LO)
                alpha[3::4] = hi.translate(_NIBBLE_HI)
                row[3::4] = alpha[:width]
            elif fmt == "dxt5":
                shift = 12 * r
                idx = b"".join(_TRIPLE[(w >> shift) & 0xFFF] for w in words)
                row[3::4] = bytes(map(alpha_lists.__getitem__, map(add, base8, idx)))[:width]
            out[y * line : (y + 1) * line] = row
    return bytes(out)
