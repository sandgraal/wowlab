"""`compositor` on constructed layers (M15-10; ADR-0033 layer a, labelled
`constructed`): 8 x 8 canvases with known results per blend mode, a mode
outside the six refused with its number, the section rectangles, the
whole-pixel stretch, the paint order, hostile plans refused before the
canvas is allocated, and §17.5's timing target.

The textures are constructed BLP2 files (BGRA, alpha depth 8) built with
`_blp_build.blp_file`; the plans are constructed `RenderPlan`s. The real
composite is graded by the derived fixture (`test_compositor_derived.py`)
and by hand (`test_compositor_live.py`, `@live`).
"""

from __future__ import annotations

import random
import tracemalloc
from collections.abc import Mapping, Sequence

import pytest
from _blp_build import blp_file
from _cpu_clock import cpu_clock

from wowlab_core import blp, compositor
from wowlab_core.compositor import (
    BlendModeError,
    CanvasError,
    LayerSizeError,
    SectionError,
)
from wowlab_core.renderplan import (
    BodyModel,
    GeosetPlan,
    ItemTexturePiece,
    LookAsOf,
    RenderPlan,
    Section,
    TextureLayer,
)

pytestmark = pytest.mark.parser

Pixel = tuple[int, int, int, int]


# ─── constructed inputs ──────────────────────────────────────────────────────


def texture(width: int, height: int, pixels: Sequence[Pixel]) -> bytes:
    """A constructed BGRA BLP2 (alpha depth 8) of `pixels`, row by row."""
    assert len(pixels) == width * height
    body = b"".join(bytes((b, g, r, a)) for r, g, b, a in pixels)
    return blp_file(width, height, blp.ENCODING_BGRA, 8, 8, body)


def layer(
    fdid: int,
    mode: int,
    order: int,
    *,
    sections: Sequence[Section] = (),
    texture_type: int = 1,
) -> TextureLayer:
    return TextureLayer(
        element_id=1000 + order,
        choice_id=1,
        material_id=1,
        texture_target_id=order + 1,
        material_resources_id=1,
        file_data_id=fdid,
        usage_type=0,
        layer_row_id=500 + order,
        texture_type=texture_type,
        layer=order,
        blend_mode=mode,
        section_mask=-1 if not sections else sum(1 << s.section_type for s in sections),
        section_mask2=-1,
        whole_canvas=not sections,
        sections=tuple(sections),
        layout_id=1,
        layout_from_chr_model_id=1,
    )


def piece(fdid: int, slot: int, section: Section | None, component: int = 0) -> ItemTexturePiece:
    return ItemTexturePiece(
        slot=slot,
        item_id=10 + slot,
        item_display_info_id=20 + slot,
        material_res_row_id=30 + slot * 10 + component,
        component_section=component,
        material_resources_id=40,
        file_data_id=fdid,
        match="any race and body type",
        section=section,
    )


def plan(
    layers: Sequence[TextureLayer],
    pieces: Sequence[ItemTexturePiece] = (),
    *,
    width: int | None = 8,
    height: int | None = 8,
) -> RenderPlan:
    body = BodyModel(
        race_id=1,
        sex=0,
        chr_model_id=1,
        drawn_with_chr_model_id=1,
        display_id=1,
        creature_model_data_id=1,
        file_data_id=1,
        layout_id=1,
        layout_from_chr_model_id=1,
        layout_width=width,
        layout_height=height,
    )
    return RenderPlan(
        build="0.0.0.0",
        class_id=None,
        body=body,
        look=LookAsOf(source="defaults"),
        choices=(),
        not_chosen=(),
        geosets=GeosetPlan(),
        layers=tuple(layers),
        unplaced=(),
        unsupported=(),
        item_pieces=tuple(pieces),
        files=(),
        notes=(),
    )


class Reader:
    """Constructed files by id; records every read."""

    def __init__(self, files: Mapping[int, bytes]) -> None:
        self.files = dict(files)
        self.reads: list[int] = []

    def read(self, fdid: int) -> bytes:
        self.reads.append(fdid)
        return self.files[fdid]


def pixels_of(result: compositor.BodyTexture) -> list[Pixel]:
    data = result.rgba
    return [(data[i], data[i + 1], data[i + 2], data[i + 3]) for i in range(0, len(data), 4)]


# ─── the reference: the docstring's formulas, one pixel at a time ────────────


def m(x: int, y: int) -> int:
    return (x * y + 127) // 255


def over(s: Pixel, a: int, d: Pixel) -> Pixel:
    r, g, b, al = (m(s[c], a) + m(d[c], 255 - a) for c in range(4))
    return (r, g, b, al)


def reference(mode: int, d: Pixel, la: Pixel) -> Pixel:
    if mode == 1:
        return la
    if mode == 15:
        return over(la, la[3], d)
    if mode == 9:
        return over((la[0], la[1], la[2], 255), la[3], d)
    if mode == 4:
        s = (m(d[0], la[0]), m(d[1], la[1]), m(d[2], la[2]), m(d[3], la[3]))
        return over(s, s[3], d)
    if mode == 7:
        s = tuple(255 - m(255 - d[c], 255 - la[c]) for c in range(3))
        return over((s[0], s[1], s[2], la[3]), la[3], d)
    if mode == 6:

        def ov(dc: int, lc: int) -> int:
            return m(2 * dc, lc) if lc < 128 else 255 - m(2 * (255 - dc), 255 - lc)

        s = tuple(ov(d[c], la[c]) for c in range(3))
        return over((s[0], s[1], s[2], la[3]), la[3], d)
    raise AssertionError(mode)


# The canvas: a gradient, opaque but for column 7 (alpha 200), so the modes'
# alpha rules show. The layer: rows 0 and 7 fully transparent, row 1
# opaque, the rest of mixed alpha with transparent ends (the fast paths).
CANVAS: list[Pixel] = [
    (x * 32 + 7, y * 32 + 3, (x + y) * 15, 200 if x == 7 else 255)
    for y in range(8)
    for x in range(8)
]
ALPHAS = (0, 64, 128, 255, 1, 254, 100, 0)


def layer_pixel(x: int, y: int) -> Pixel:
    colour = ((x * 37 + y * 11) % 256, (x * 5 + y * 71) % 256, (255 - x * 29) % 256)
    if y in (0, 7):
        alpha = 0
    elif y == 1:
        alpha = 255
    else:
        alpha = 0 if x in (0, 7) else ALPHAS[(x + y) % 8]
    return (*colour, alpha)


LAYER: list[Pixel] = [layer_pixel(x, y) for y in range(8) for x in range(8)]

MODES = (1, 4, 6, 7, 9, 15)


@pytest.mark.parametrize("mode", MODES)
def test_each_blend_mode_on_8x8_matches_the_formulas_constructed(mode: int) -> None:
    """Every pixel of an 8 x 8 layer over an 8 x 8 canvas, against the
    per-pixel reference written from the module docstring."""
    files = {1: texture(8, 8, CANVAS), 2: texture(8, 8, LAYER)}
    result = compositor.compose(plan([layer(1, 1, 0), layer(2, mode, 1)]), Reader(files))
    want = [reference(mode, d, la) for d, la in zip(CANVAS, LAYER, strict=True)]
    assert pixels_of(result) == want
    assert [p.job.blend_mode for p in result.painted] == [1, mode]
    assert result.painted[1].job.blend == compositor.BLEND_NAMES[mode]


# One canvas pixel (200, 100, 50, 255) under one layer pixel (100, 200, 250,
# 128), worked out by hand with m(x, y) = (x * y + 127) // 255.
BY_HAND: dict[int, Pixel] = {
    1: (100, 200, 250, 128),  # blit: the layer, alpha included
    15: (150, 150, 150, 191),  # m(l, 128) + m(d, 127); alpha m(128, 128) + 127
    9: (150, 150, 150, 255),  # as 15, alpha m(255, 128) + 127
    4: (139, 89, 50, 191),  # s = m(d, l) = (78, 78, 49, 128), then over
    7: (211, 161, 151, 191),  # s = (222, 222, 251), then over
    6: (179, 144, 149, 191),  # s = (157, 188, 247), then over
}


@pytest.mark.parametrize("mode", MODES)
def test_each_blend_mode_by_hand_constructed(mode: int) -> None:
    canvas = [(200, 100, 50, 255)] * 64
    top = [(100, 200, 250, 128)] * 64
    files = {1: texture(8, 8, canvas), 2: texture(8, 8, top)}
    result = compositor.compose(plan([layer(1, 1, 0), layer(2, mode, 1)]), Reader(files))
    assert set(pixels_of(result)) == {BY_HAND[mode]}
    assert compositor.blend_rows(mode, bytes(canvas[0]), bytes(top[0])) == bytes(BY_HAND[mode])


@pytest.mark.parametrize("mode", MODES)
def test_the_stretched_fast_paths_equal_blend_rows_constructed(mode: int) -> None:
    """A 4 x 2 layer stretched 2 x 4 onto the 8 x 8 canvas: each canvas row
    is `blend_rows` of the canvas row and the widened layer row."""
    small = [layer_pixel(x * 2, y * 3 + 1) for y in range(2) for x in range(4)]
    files = {1: texture(8, 8, CANVAS), 2: texture(4, 2, small)}
    result = compositor.compose(plan([layer(1, 1, 0), layer(2, mode, 1)]), Reader(files))
    for y in range(8):
        src = b"".join(bytes(small[(y // 4) * 4 + x // 2]) for x in range(8))
        dst = b"".join(bytes(CANVAS[y * 8 + x]) for x in range(8))
        assert result.rows()[y] == compositor.blend_rows(mode, dst, src), y
    assert (result.painted[1].scale_x, result.painted[1].scale_y) == (2, 4)


@pytest.mark.parametrize("mode", [0, 2, 3, 5, 8, 10, 11, 12, 13, 14, 16, 17, 255])
def test_a_mode_outside_the_six_is_refused_with_its_number_constructed(mode: int) -> None:
    reader = Reader({1: texture(8, 8, CANVAS), 2: texture(8, 8, LAYER)})
    with pytest.raises(BlendModeError, match=rf"blend mode {mode} is not one") as caught:
        compositor.compose(plan([layer(1, 1, 0), layer(2, mode, 1)]), reader)
    assert caught.value.blend_mode == mode and caught.value.file_data_id == 2
    assert reader.reads == []  # refused from the plan alone


def test_a_hair_or_eye_layer_is_never_painted_onto_the_body_constructed() -> None:
    """Texture types 6 and 19 are separate textures: listed, not read, and
    their blend mode not checked (it is not the body's)."""
    red = [(255, 0, 0, 255)] * 64
    reader = Reader({1: texture(8, 8, CANVAS), 6: texture(8, 8, red), 19: texture(8, 8, red)})
    layers = [layer(1, 1, 0), layer(6, 1, 1, texture_type=6), layer(19, 17, 2, texture_type=19)]
    result = compositor.compose(plan(layers), reader)
    assert pixels_of(result) == CANVAS
    assert [(n.file_data_id, n.texture_type) for n in result.not_painted] == [(6, 6), (19, 19)]
    assert set(reader.reads) == {1}
    assert [la.file_data_id for la in compositor.separate_layers(plan(layers))] == [6, 19]


# ─── sections and the stretch ────────────────────────────────────────────────


def test_a_section_layer_touches_only_its_rectangle_constructed() -> None:
    """A 2 x 2 texture into the 4 x 2 section at (2, 5): each texel 2 x 1."""
    white = [(255, 255, 255, 255)] * 64
    quad = [(10, 0, 0, 255), (20, 0, 0, 255), (30, 0, 0, 255), (40, 0, 0, 255)]
    section = Section(section_type=3, x=2, y=5, width=4, height=2)
    reader = Reader({1: texture(8, 8, white), 2: texture(2, 2, quad)})
    result = compositor.compose(plan([layer(1, 1, 0), layer(2, 15, 1, sections=[section])]), reader)
    got = pixels_of(result)
    for y in range(8):
        for x in range(8):
            if 2 <= x < 6 and 5 <= y < 7:
                want = quad[(y - 5) * 2 + (x - 2) // 2]
            else:
                want = (255, 255, 255, 255)
            assert got[y * 8 + x] == want, (x, y)
    job = result.painted[1].job
    assert (job.section_type, job.x, job.y, job.width, job.height) == (3, 2, 5, 4, 2)


def test_the_canvas_starts_opaque_black_constructed() -> None:
    result = compositor.compose(plan([]), Reader({}))
    assert set(pixels_of(result)) == {(0, 0, 0, 255)}
    assert result.painted == () and result.not_painted == ()


def test_a_layer_bigger_than_its_section_is_refused_constructed() -> None:
    section = Section(section_type=0, x=0, y=0, width=4, height=4)
    reader = Reader({1: texture(8, 8, CANVAS), 2: texture(8, 8, LAYER)})
    with pytest.raises(LayerSizeError, match="file 2 is 8 x 8, section 0 is 4 x 4") as caught:
        compositor.compose(plan([layer(1, 1, 0), layer(2, 15, 1, sections=[section])]), reader)
    assert caught.value.file_data_id == 2


def test_a_layer_that_does_not_fit_its_section_whole_is_refused_constructed() -> None:
    """3 x 4 into 8 x 8: 8 is not a multiple of 3."""
    reader = Reader({2: texture(3, 4, LAYER[:12])})
    with pytest.raises(LayerSizeError, match="a whole number of times"):
        compositor.compose(plan([layer(2, 15, 0)]), reader)


@pytest.mark.parametrize(
    ("x", "y", "w", "h"),
    [(-1, 0, 4, 4), (0, -1, 4, 4), (6, 0, 4, 4), (0, 6, 4, 4), (0, 0, 0, 4), (0, 0, 4, 0)],
)
def test_a_section_outside_the_canvas_is_refused_unread_constructed(
    x: int, y: int, w: int, h: int
) -> None:
    section = Section(section_type=5, x=x, y=y, width=w, height=h)
    reader = Reader({2: texture(4, 4, LAYER[:16])})
    with pytest.raises(SectionError, match="section 5"):
        compositor.compose(plan([layer(2, 15, 0, sections=[section])]), reader)
    assert reader.reads == []


def test_an_item_piece_outside_the_canvas_is_refused_constructed() -> None:
    section = Section(section_type=1, x=4, y=4, width=8, height=8)
    with pytest.raises(SectionError, match="item 15"):
        compositor.compose(plan([], [piece(2, 5, section)]), Reader({}))


@pytest.mark.parametrize(
    ("width", "height"),
    [(None, 8), (8, None), (0, 8), (8193, 1), (1, 8193), (4097, 4096), (-8, 8)],
)
def test_a_canvas_without_a_size_or_past_the_cap_is_refused_constructed(
    width: int | None, height: int | None
) -> None:
    reader = Reader({})
    with pytest.raises(CanvasError):
        compositor.compose(plan([layer(2, 15, 0)], width=width, height=height), reader)
    assert reader.reads == []


def test_hostile_plans_are_refused_before_the_canvas_is_allocated_constructed() -> None:
    """A 4096 x 4096 canvas (64 MiB) whose last layer is too big for its
    section, or whose texture is a hostile header: refused with far less
    than one canvas allocated, after only the headers were read."""
    small = Section(section_type=2, x=0, y=0, width=4, height=4)
    files = {1: texture(8, 8, CANVAS), 2: texture(8, 8, LAYER)}
    hostile = bytearray(texture(8, 8, LAYER))
    hostile[12:16] = (8193).to_bytes(4, "little")  # the width, past blp's cap
    files[3] = bytes(hostile)
    for bad, error in ((2, LayerSizeError), (3, blp.BlpTooLargeError)):
        layers = [layer(1, 1, 0), layer(bad, 15, 1, sections=[small])]
        tracemalloc.start()
        try:
            with pytest.raises(error):
                compositor.compose(plan(layers, width=4096, height=4096), Reader(files))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 4 * 1024 * 1024, peak


# ─── the paint order ─────────────────────────────────────────────────────────


def test_the_paint_order_is_the_plans_then_items_by_slot_layer_constructed() -> None:
    """Customization layers in the plan's order (its `Layer`), then the
    pieces by the slot's texture layer, the component section, and the
    plan's order; a piece the layout has no section for is listed."""
    whole = Section(section_type=0, x=0, y=0, width=8, height=8)
    pieces = [
        piece(51, 15, whole),  # back: 23
        piece(52, 10, whole, 2),  # hands: 20
        piece(53, 10, whole, 1),  # hands: 20, section 1 first
        piece(54, 4, whole),  # shirt: 10
        piece(55, 5, whole),  # chest: 13
        piece(56, 7, whole),  # legs: 10, after the shirt (plan order)
        piece(57, 6, None),  # no section
        piece(58, 99, whole),  # not in the table: 10
    ]
    layers = [layer(1, 1, 0), layer(2, 15, 2), layer(3, 4, 5)]
    jobs, skipped = compositor.paint_jobs(plan(layers, pieces))
    assert [(j.kind, j.file_data_id) for j in jobs] == [
        ("customization", 1),
        ("customization", 2),
        ("customization", 3),
        ("item", 54),
        ("item", 56),
        ("item", 58),
        ("item", 55),
        ("item", 53),
        ("item", 52),
        ("item", 51),
    ]
    assert {j.blend_mode for j in jobs if j.kind == "item"} == {compositor.ITEM_BLEND_MODE}
    assert [(n.kind, n.file_data_id, n.slot) for n in skipped] == [("item", 57, 6)]


def test_items_are_painted_over_the_customization_constructed() -> None:
    """A half-transparent item piece over an opaque skin: alpha blend."""
    skin = [(200, 100, 50, 255)] * 64
    cloth = [(100, 200, 250, 128)] * 16
    whole = Section(section_type=0, x=0, y=0, width=8, height=8)
    reader = Reader({1: texture(8, 8, skin), 2: texture(4, 4, cloth)})
    result = compositor.compose(plan([layer(1, 1, 0)], [piece(2, 5, whole)]), reader)
    assert set(pixels_of(result)) == {BY_HAND[15]}
    assert result.painted[-1].job.kind == "item"


def test_a_layer_in_two_sections_is_painted_in_each_constructed() -> None:
    a = Section(section_type=0, x=0, y=0, width=4, height=4)
    b = Section(section_type=1, x=4, y=4, width=4, height=4)
    green = [(0, 255, 0, 255)] * 16
    reader = Reader({1: texture(8, 8, CANVAS), 2: texture(4, 4, green)})
    result = compositor.compose(plan([layer(1, 1, 0), layer(2, 15, 1, sections=[a, b])]), reader)
    got = pixels_of(result)
    for y in range(8):
        for x in range(8):
            inside = (x < 4 and y < 4) or (x >= 4 and y >= 4)
            assert got[y * 8 + x] == ((0, 255, 0, 255) if inside else CANVAS[y * 8 + x])
    assert [p.job.section_type for p in result.painted] == [None, 0, 1]


def test_the_composite_writes_a_png_constructed() -> None:
    result = compositor.compose(plan([layer(1, 1, 0)]), Reader({1: texture(8, 8, CANVAS)}))
    data = result.png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(result.rgba_md5()) == 32 and len(result.rows()) == 8


# ─── cost ────────────────────────────────────────────────────────────────────


def _noise(rng: random.Random, width: int, height: int) -> bytes:
    """A constructed BGRA BLP2 of random colours, every alpha partial (1 to
    254), so that no fast path applies."""
    body = bytearray(rng.randbytes(width * height * 4))
    body[3::4] = body[3::4].translate(bytes(1 + v % 254 for v in range(256)))
    return blp_file(width, height, blp.ENCODING_BGRA, 8, 8, bytes(body))


def _worst_layout() -> tuple[RenderPlan, Reader]:
    """The busiest body layout of the recorded table at 1.60.1.70959 (layout
    105: a blit base, four whole-canvas blending layers, modes 4, 6, 6 and
    9, and six section layers of mode 15 on the two 1024 x 1024 face
    sections and the 512 x 256 section 5), every texture at the real
    half-size and every pixel partially transparent, plus eleven item
    pieces of 128 x 64 into 512 x 256 sections."""
    rng = random.Random(1015)
    face = Section(section_type=9, x=1024, y=0, width=1024, height=1024)
    face2 = Section(section_type=10, x=1024, y=0, width=1024, height=1024)
    torso = Section(section_type=5, x=512, y=384, width=512, height=256)
    files = {
        1: _noise(rng, 1024, 512),
        2: _noise(rng, 512, 512),
        3: _noise(rng, 256, 128),
        4: _noise(rng, 128, 64),
    }
    layers = [layer(1, 1, 0)]
    layers += [layer(1, mode, 1 + i) for i, mode in enumerate((4, 6, 6, 9))]
    layers += [layer(2, 15, 5 + i, sections=[face if i % 2 else face2]) for i in range(4)]
    layers += [layer(3, 15, 9 + i, sections=[torso]) for i in range(2)]
    pieces = [piece(4, 5, Section(section_type=0, x=0, y=0, width=512, height=256))] * 11
    return plan(layers, pieces, width=2048, height=1024), Reader(files)


def test_a_2048x1024_composite_within_three_seconds_constructed() -> None:
    """§17.5's target on the worst body layout with every pixel partial
    (the real fixture characters take about 0.2 s; §17.5's cost model)."""
    the_plan, reader = _worst_layout()
    tiny = Reader({1: texture(8, 8, CANVAS), 2: texture(8, 8, LAYER)})
    for mode in MODES:  # the lookup tables, built once outside the clock
        compositor.compose(plan([layer(1, 1, 0), layer(2, mode, 1)]), tiny)
    clock, name = cpu_clock()
    start = clock()
    result = compositor.compose(the_plan, reader)
    took = clock() - start
    assert (result.width, result.height) == (2048, 1024)
    assert took <= 3.0, f"{took:.2f} s of {name} time for a 2048 x 1024 composite"


def test_the_peak_is_inside_the_stated_bound_constructed() -> None:
    """tracemalloc's peak for a 2048 x 1024 composite whose base is a
    full-size texture (the largest decode a canvas admits) under a blended
    layer, against `compositor_peak_bytes` (four canvases) plus the file
    the reader returns (allocated here before the trace starts)."""
    rng = random.Random(7)
    base = _noise(rng, 2048, 1024)
    corner = Section(section_type=4, x=0, y=0, width=256, height=128)
    reader = Reader({1: base, 2: _noise(rng, 128, 64)})
    the_plan = plan([layer(1, 1, 0), layer(2, 4, 1, sections=[corner])], width=2048, height=1024)
    tracemalloc.start()
    try:
        result = compositor.compose(the_plan, reader)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    canvas = 2048 * 1024 * 4
    assert len(result.rgba) == canvas
    assert 2 * canvas <= peak <= compositor.compositor_peak_bytes(2048, 1024) + len(base), peak
