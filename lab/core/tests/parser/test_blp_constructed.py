"""`blp` on constructed textures (M15-07; ADR-0033 layer a, labelled
`constructed`): 4 x 4 and 8 x 8 textures of each encoding with RGBA worked
out by hand, the two DXT rules and the refusal where they disagree, BC5 and
the other refusals, hostile headers (the allocation bound), stored mip
sizes not trusted, and §17.2's timing target.

The real install is graded by the derived fixture (`test_blp_derived.py`)
and by hand (`test_blp_live.py`, `@live`).
"""

from __future__ import annotations

import random
import time
import tracemalloc

import pytest
from _blp_build import BLUE, GREEN, GREY, PATTERN, RED, bc1, bc4_alpha, blp_file, explicit_alpha, px

from wowlab_core import blp

pytestmark = pytest.mark.parser

OPAQUE = 255


# ─── DXT1 ────────────────────────────────────────────────────────────────────


def test_dxt1_four_colour_block_4x4_constructed() -> None:
    """c0 > c1: four opaque colours, the two between rounded down."""
    image = blp.decode(blp_file(4, 4, 2, 0, 0, bc1(RED, BLUE, PATTERN)))
    r, b = (255, 0, 0, 255), (0, 0, 255, 255)
    c2, c3 = (170, 0, 85, 255), (85, 0, 170, 255)
    assert image.format == "dxt1"
    assert (image.width, image.height) == (4, 4)
    assert image.rows() == [px(r, b, c2, c3), px(c3, c2, b, r), px(r, r, r, r), px(b, b, b, b)]


def test_dxt1_three_colour_block_has_transparent_black_4x4_constructed() -> None:
    """c0 <= c1 in DXT1: three colours, the midpoint rounded down, and
    index 3 transparent black (alpha depth 1, as the client's DXT1 with
    1-bit alpha is stored)."""
    image = blp.decode(blp_file(4, 4, 2, 1, 0, bc1(BLUE, RED, PATTERN)))
    b, r, mid, clear = (0, 0, 255, 255), (255, 0, 0, 255), (127, 0, 127, 255), (0, 0, 0, 0)
    assert image.rows() == [
        px(b, r, mid, clear),
        px(clear, mid, r, b),
        px(b, b, b, b),
        px(r, r, r, r),
    ]


def test_dxt1_8x8_blocks_land_in_place_and_565_is_widened_constructed() -> None:
    """Four solid blocks, left to right then top to bottom; RGB565 widened
    by repeating the top bits (16/32/16 -> 132/130/132)."""
    solid = [[0] * 4] * 4
    body = bc1(RED, 0, solid) + bc1(BLUE, 0, solid) + bc1(GREEN, 0, solid) + bc1(GREY, 0, solid)
    image = blp.decode(blp_file(8, 8, 2, 0, 0, body))
    top = px(*[(255, 0, 0, 255)] * 4, *[(0, 0, 255, 255)] * 4)
    bottom = px(*[(0, 255, 0, 255)] * 4, *[(132, 130, 132, 255)] * 4)
    assert image.rows() == [top] * 4 + [bottom] * 4


def test_dxt1_side_not_a_multiple_of_four_is_cut_from_whole_blocks_constructed() -> None:
    """A 6 x 5 texture is stored as 2 x 2 blocks and cut to 6 x 5; a 2 x 2
    one (a small mip's shape) is one block cut to 2 x 2."""
    solid = [[0] * 4] * 4
    body = bc1(RED, 0, solid) + bc1(BLUE, 0, solid) + bc1(GREEN, 0, solid) + bc1(GREY, 0, solid)
    image = blp.decode(blp_file(6, 5, 2, 0, 0, body))
    assert image.mip0_size == 32
    assert len(image.rgba) == 6 * 5 * 4
    top = px(*[(255, 0, 0, 255)] * 4, *[(0, 0, 255, 255)] * 2)
    bottom = px(*[(0, 255, 0, 255)] * 4, *[(132, 130, 132, 255)] * 2)
    assert image.rows() == [top] * 4 + [bottom]
    small = blp.decode(blp_file(2, 2, 2, 0, 0, bc1(RED, BLUE, PATTERN)))
    assert small.rows() == [
        px((255, 0, 0, 255), (0, 0, 255, 255)),
        px((85, 0, 170, 255), (170, 0, 85, 255)),
    ]


# ─── DXT3 ────────────────────────────────────────────────────────────────────


def test_dxt3_explicit_alpha_and_four_colours_always_4x4_constructed() -> None:
    """DXT3: explicit 4-bit alpha times 17; the colour block is read with
    four colours even when c0 <= c1."""
    alpha = [[0, 5, 10, 15], [15] * 4, [15] * 4, [15] * 4]
    body = explicit_alpha(alpha) + bc1(BLUE, RED, PATTERN)
    image = blp.decode(blp_file(4, 4, 2, 8, 1, body))
    b, r, c2, c3 = (0, 0, 255), (255, 0, 0), (85, 0, 170), (170, 0, 85)
    assert image.format == "dxt3"
    assert image.rows() == [
        px((*b, 0), (*r, 85), (*c2, 170), (*c3, 255)),
        px((*c3, 255), (*c2, 255), (*r, 255), (*b, 255)),
        px(*[(*b, 255)] * 4),
        px(*[(*r, 255)] * 4),
    ]


def test_dxt3_8x8_constructed() -> None:
    solid = [[0] * 4] * 4
    blocks = [
        explicit_alpha([[1] * 4] * 4) + bc1(RED, 0, solid),
        explicit_alpha([[2] * 4] * 4) + bc1(BLUE, 0, solid),
        explicit_alpha([[3] * 4] * 4) + bc1(GREEN, 0, solid),
        explicit_alpha([[4] * 4] * 4) + bc1(GREY, 0, solid),
    ]
    image = blp.decode(blp_file(8, 8, 2, 4, 1, b"".join(blocks)))
    top = px(*[(255, 0, 0, 17)] * 4, *[(0, 0, 255, 34)] * 4)
    bottom = px(*[(0, 255, 0, 51)] * 4, *[(132, 130, 132, 68)] * 4)
    assert image.rows() == [top] * 4 + [bottom] * 4


# ─── DXT5 ────────────────────────────────────────────────────────────────────


def test_dxt5_eight_value_alpha_4x4_constructed() -> None:
    """a0 > a1: eight values, the six between rounded down."""
    alpha = [[0, 1, 2, 3], [4, 5, 6, 7], [0] * 4, [0] * 4]
    body = bc4_alpha(255, 0, alpha) + bc1(RED, RED, [[0] * 4] * 4)
    image = blp.decode(blp_file(4, 4, 2, 8, 7, body))
    assert image.format == "dxt5"
    assert image.rgba[0::4] == bytes([255] * 16)
    assert image.rgba[3::4] == bytes([255, 0, 218, 182, 145, 109, 72, 36] + [255] * 8)


def test_dxt5_six_value_alpha_with_0_and_255_4x4_constructed() -> None:
    """a0 <= a1: six values, then 0 and 255."""
    alpha = [[0, 1, 2, 3], [4, 5, 6, 7], [7] * 4, [6] * 4]
    body = bc4_alpha(0, 200, alpha) + bc1(BLUE, RED, PATTERN)
    image = blp.decode(blp_file(4, 4, 2, 8, 7, body))
    assert image.rgba[3::4] == bytes([0, 200, 40, 80, 120, 160, 0, 255] + [255] * 4 + [0] * 4)
    # four colours in DXT5 even when c0 <= c1
    assert image.rows()[0][:12] == bytes((0, 0, 255, 0, 255, 0, 0, 200, 85, 0, 170, 40))


def test_dxt5_8x8_constructed() -> None:
    solid = [[0] * 4] * 4
    blocks = [
        bc4_alpha(10, 0, solid) + bc1(RED, 0, solid),
        bc4_alpha(20, 0, solid) + bc1(BLUE, 0, solid),
        bc4_alpha(30, 0, solid) + bc1(GREEN, 0, solid),
        bc4_alpha(0, 9, [[7] * 4] * 4) + bc1(GREY, 0, solid),
    ]
    image = blp.decode(blp_file(8, 8, 2, 8, 7, b"".join(blocks)))
    top = px(*[(255, 0, 0, 10)] * 4, *[(0, 0, 255, 20)] * 4)
    bottom = px(*[(0, 255, 0, 30)] * 4, *[(132, 130, 132, 255)] * 4)
    assert image.rows() == [top] * 4 + [bottom] * 4


# ─── palette and BGRA ────────────────────────────────────────────────────────


def _palette() -> bytes:
    """256 BGRX entries: entry i is B = i, G = 255 - i, R = i ^ 0x55, X = 0
    (the real palettes' fourth byte was 0 throughout; it is not alpha)."""
    return b"".join(bytes((i, 255 - i, i ^ 0x55, 0)) for i in range(256))


def _rgb(i: int) -> tuple[int, int, int]:
    return (i ^ 0x55, 255 - i, i)


@pytest.mark.parametrize("side", [4, 8])
def test_palette_without_alpha_constructed(side: int) -> None:
    indices = bytes((7 * k) % 256 for k in range(side * side))
    image = blp.decode(blp_file(side, side, 1, 0, 8, indices, palette=_palette()))
    assert image.format == "palette"
    assert image.rgba == b"".join(bytes((*_rgb(i), OPAQUE)) for i in indices)


@pytest.mark.parametrize("side", [4, 8])
def test_palette_8_bit_alpha_constructed(side: int) -> None:
    n = side * side
    indices = bytes(range(n))
    alpha = bytes((255 - 3 * k) % 256 for k in range(n))
    image = blp.decode(blp_file(side, side, 1, 8, 8, indices + alpha, palette=_palette()))
    assert image.rgba == b"".join(bytes((*_rgb(i), a)) for i, a in zip(indices, alpha, strict=True))


@pytest.mark.parametrize("side", [4, 8])
def test_palette_4_bit_alpha_low_nibble_first_constructed(side: int) -> None:
    n = side * side
    indices = bytes(range(n))
    nibbles = [k % 16 for k in range(n)]
    alpha = bytes(nibbles[k] | nibbles[k + 1] << 4 for k in range(0, n, 2))
    image = blp.decode(blp_file(side, side, 1, 4, 8, indices + alpha, palette=_palette()))
    assert image.rgba[3::4] == bytes(v * 17 for v in nibbles)
    assert image.rgba[0::4] == bytes(i ^ 0x55 for i in indices)


@pytest.mark.parametrize("side", [4, 8])
def test_palette_1_bit_alpha_lowest_bit_first_constructed(side: int) -> None:
    n = side * side
    indices = bytes(range(n))
    bits = [1 if k % 3 == 0 else 0 for k in range(n)]
    alpha = bytes(sum(bits[k + j] << j for j in range(8)) for k in range(0, n, 8))
    image = blp.decode(blp_file(side, side, 1, 1, 8, indices + alpha, palette=_palette()))
    assert image.rgba[3::4] == bytes(255 if b else 0 for b in bits)


def test_palette_1_bit_alpha_of_a_pixel_count_not_a_multiple_of_8_constructed() -> None:
    """2 x 2: four pixels, one alpha byte, its four high bits unused."""
    alpha = bytes([0b1111_0101])
    image = blp.decode(blp_file(2, 2, 1, 1, 8, bytes(4) + alpha, palette=_palette()))
    assert image.rgba[3::4] == bytes((255, 0, 255, 0))
    assert image.mip0_size == 5


@pytest.mark.parametrize("side", [4, 8])
@pytest.mark.parametrize("depth", [0, 8])
def test_bgra_constructed(side: int, depth: int) -> None:
    """B G R A per pixel; with alpha depth 0 the stored alpha is ignored."""
    n = side * side
    body = b"".join(bytes((k, 2 * k % 256, 3 * k % 256, 100 + k)) for k in range(n))
    image = blp.decode(blp_file(side, side, 3, depth, 8, body))
    assert image.format == "bgra"
    want = b"".join(
        bytes((3 * k % 256, 2 * k % 256, k, 100 + k if depth else 255)) for k in range(n)
    )
    assert image.rgba == want


# ─── the DXT rules ───────────────────────────────────────────────────────────

AGREE = {
    (0, 0): "dxt1",
    (1, 0): "dxt1",
    (4, 1): "dxt3",
    (8, 1): "dxt3",
    (4, 7): "dxt5",
    (8, 7): "dxt5",
}
DISAGREE = [(8, 0), (1, 1), (4, 0), (0, 1), (0, 7), (1, 7)]


def test_the_two_rules_disagree_exactly_where_lab_formats_says_constructed() -> None:
    """§11 lists where the wiki's rule and wow.export's differ: depth 8
    with type 0, depth 1 with type 1, depth 4 with type 0, depth 0 with
    type 1, and depth 0 or 1 with type 7."""
    found = [
        (d, t)
        for d in blp.ALPHA_DEPTHS
        for t in (0, 1, 7)
        if blp.wiki_rule(t) != blp.wow_export_rule(d, t)
    ]
    assert sorted(found) == sorted(DISAGREE)
    for (depth, kind), fmt in AGREE.items():
        assert blp.wiki_rule(kind) == blp.wow_export_rule(depth, kind) == fmt
    assert blp.wiki_rule(11) is None and blp.wiki_rule(8) is None


@pytest.mark.parametrize(("depth", "kind"), DISAGREE)
def test_a_texture_the_rules_disagree_on_is_refused_naming_both_constructed(
    depth: int, kind: int
) -> None:
    body = bytes(16)
    with pytest.raises(blp.BlpRuleConflict) as caught:
        blp.decode(blp_file(4, 4, 2, depth, kind, body))
    exc = caught.value
    assert (exc.alpha_depth, exc.alpha_type) == (depth, kind)
    assert exc.wiki == blp.wiki_rule(kind) and exc.wow_export == blp.wow_export_rule(depth, kind)
    assert exc.wiki != exc.wow_export
    message = str(exc)
    assert f"alpha depth {depth}" in message and f"alpha type {kind}" in message
    assert exc.wiki.upper() in message and exc.wow_export.upper() in message
    assert isinstance(exc, blp.BlpUnsupportedError)


@pytest.mark.parametrize(("depth", "kind"), sorted(AGREE))
def test_a_texture_the_rules_agree_on_is_decoded_constructed(depth: int, kind: int) -> None:
    fmt = AGREE[(depth, kind)]
    body = bytes(8 if fmt == "dxt1" else 16)
    assert blp.decode(blp_file(4, 4, 2, depth, kind, body)).format == fmt


# ─── refusals ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("depth", [0, 8])
def test_bc5_is_refused_with_a_typed_error_constructed(depth: int) -> None:
    with pytest.raises(blp.BlpUnsupportedError, match="BC5") as caught:
        blp.decode(blp_file(4, 4, 2, depth, 11, bytes(16)))
    assert not isinstance(caught.value, blp.BlpRuleConflict)
    assert caught.value.alpha_type == 11


@pytest.mark.parametrize(
    ("encoding", "depth", "kind", "words"),
    [
        (0, 0, 0, "colour encoding 0"),
        (4, 0, 0, "colour encoding 4"),
        (2, 0, 2, "DXT alpha type 2"),
        (2, 2, 0, "DXT alpha depth 2"),
        (1, 2, 8, "palette alpha depth 2"),
        (3, 4, 8, "BGRA alpha depth 4"),
    ],
)
def test_other_encodings_are_refused_with_a_typed_error_constructed(
    encoding: int, depth: int, kind: int, words: str
) -> None:
    with pytest.raises(blp.BlpUnsupportedError, match=words) as caught:
        blp.decode(blp_file(4, 4, encoding, depth, kind, bytes(64)))
    assert (caught.value.encoding, caught.value.alpha_depth, caught.value.alpha_type) == (
        encoding,
        depth,
        kind,
    )


@pytest.mark.parametrize("version", [0, 2])
def test_another_version_is_refused_constructed(version: int) -> None:
    with pytest.raises(blp.BlpUnsupportedError, match=f"version {version}"):
        blp.decode(blp_file(4, 4, 2, 0, 0, bytes(8), version=version))


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (b"", "0 bytes"),
        (b"BLP2" + bytes(100), "104 bytes"),
        (blp_file(4, 4, 2, 0, 0, bytes(8), magic=b"BLP1"), "magic"),
        (blp_file(4, 4, 2, 0, 0, bytes(8), magic=b"\x89PNG"), "magic"),
    ],
)
def test_short_or_foreign_files_are_refused_constructed(data: bytes, words: str) -> None:
    with pytest.raises(blp.BlpMalformedError, match=words):
        blp.decode(data)


# ─── hostile headers: the allocation bound ───────────────────────────────────


@pytest.mark.parametrize(
    ("width", "height"),
    [(2**31, 4), (4, 2**31), (2**32 - 1, 2**32 - 1), (8193, 4), (8192, 8192), (4097, 4096)],
)
def test_a_side_or_pixel_count_over_the_cap_is_refused_before_allocation_constructed(
    width: int, height: int
) -> None:
    data = blp_file(width, height, 2, 0, 0, bytes(8))
    tracemalloc.start()
    try:
        with pytest.raises(blp.BlpTooLargeError) as caught:
            blp.decode(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert (caught.value.width, caught.value.height) == (width, height)
    assert str(width) in str(caught.value) and str(blp.MAX_DIMENSION) in str(caught.value)
    assert peak < 256 * 1024, peak


@pytest.mark.parametrize(("width", "height"), [(0, 4), (4, 0), (0, 0)])
def test_a_zero_side_is_refused_constructed(width: int, height: int) -> None:
    with pytest.raises(blp.BlpMalformedError, match="at least 1"):
        blp.decode(blp_file(width, height, 2, 0, 0, bytes(8)))


def test_the_cap_itself_is_accepted_as_a_size_constructed() -> None:
    blp.check_size(blp.MAX_DIMENSION, blp.MAX_PIXELS // blp.MAX_DIMENSION)
    blp.check_size(4096, 4096)
    with pytest.raises(blp.BlpTooLargeError):
        blp.check_size(4096, 4097)


@pytest.mark.parametrize("encoding", [(2, 0, 0), (2, 8, 7), (1, 8, 8), (3, 8, 8)])
def test_a_mip_past_the_end_of_the_file_is_refused_before_allocation_constructed(
    encoding: tuple[int, int, int],
) -> None:
    """A 4096 x 4096 header (inside the cap) over a 1 KiB body: mip 0's
    computed size is past the file, so nothing the size of the image is
    allocated."""
    data = blp_file(4096, 4096, *encoding, bytes(1024))
    tracemalloc.start()
    try:
        with pytest.raises(blp.BlpMalformedError, match="not inside the file"):
            blp.decode(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 256 * 1024, peak


@pytest.mark.parametrize("offset", [10**9, 2**32 - 1, 1172 + 1, 0, 147])
def test_a_mip_offset_past_the_file_or_inside_the_header_is_refused_constructed(
    offset: int,
) -> None:
    with pytest.raises(blp.BlpMalformedError, match="mip 0 at offset"):
        blp.decode(blp_file(4, 4, 2, 0, 0, bytes(8), offset=offset))


def test_a_palette_texture_too_short_for_its_palette_is_refused_constructed() -> None:
    data = blp_file(4, 4, 1, 0, 8, bytes(16), offset=148)[: 148 + 16]
    with pytest.raises(blp.BlpMalformedError, match="palette"):
        blp.decode(data)


@pytest.mark.parametrize("stored", [0, 1, 7, 9, 2**32 - 1])
def test_the_stored_mip_size_is_not_trusted_constructed(stored: int) -> None:
    """The stored mip 0 size is kept in the header and not used: the
    computed one decides how much is read."""
    image = blp.decode(blp_file(4, 4, 2, 0, 0, bc1(RED, BLUE, PATTERN), stored=stored))
    assert image.mip0_size == 8
    assert image.stored_mip0_size == stored
    assert image.rows()[2] == px(*[(255, 0, 0, 255)] * 4)


def test_header_fields_and_mip_count_constructed() -> None:
    data = blp_file(4, 4, 2, 1, 0, bc1(RED, BLUE, PATTERN), has_mips=17)
    header = blp.parse_header(data)
    assert (header.version, header.encoding, header.alpha_depth, header.alpha_type) == (1, 2, 1, 0)
    assert (header.has_mips, header.width, header.height) == (17, 4, 4)
    assert header.mip_offsets[0] == 1172 and header.mip_sizes[0] == 8
    assert header.mip_count == 1
    assert len(header.mip_offsets) == len(header.mip_sizes) == 16


def test_a_bytearray_and_a_read_only_memoryview_decode_alike_constructed() -> None:
    """`casc.CascSource.read` gives a read-only `memoryview`."""
    data = blp_file(4, 4, 2, 0, 0, bc1(RED, BLUE, PATTERN))
    want = blp.decode(data).rgba
    assert blp.decode(bytearray(data)).rgba == want
    assert blp.decode(memoryview(data)).rgba == want


# ─── §17.2's target ──────────────────────────────────────────────────────────


@pytest.mark.serial
def test_a_1024_square_dxt1_decodes_within_half_a_second_constructed() -> None:
    """§17.2: a 1024 x 1024 DXT1 in at most 0.5 s on the owner's machine.
    Random blocks, so every block has its own endpoints (the worst case
    for the per-endpoint cache; a real texture repeats many). Best of
    three by wall clock. Measured on the owner's M1 on 2026-11-17: 0.12 s
    here, 0.09 s for a real 1024 x 1024 DXT1 (`test_blp_live.py`)."""
    body = random.Random(1024).randbytes(256 * 256 * 8)
    data = blp_file(1024, 1024, 2, 0, 0, body)
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        image = blp.decode(data)
        best = min(best, time.perf_counter() - started)
    assert len(image.rgba) == 1024 * 1024 * 4
    assert best <= 0.5, f"a 1024 x 1024 DXT1 took {best:.3f} s; §17.2's target is 0.5 s"
