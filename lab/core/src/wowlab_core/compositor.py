"""compositor: a render plan's body texture, composed in Python
(docs/LAB_PLAN.md §17.5, M15-10).

Input: a `renderplan.RenderPlan` and a `TextureReader` (`casc.CascSource`
is one) that gives a file's bytes by file data id. This module opens no
file itself and writes nothing; the composite is returned as bytes inside a
`BodyTexture`, and where it goes is the caller's choice.

**What is painted.** A canvas of the body's texture layout
(`plan.body.layout_width` x `layout_height`), opaque black to start, as
wow.export clears its canvas. Then:

1. The customization layers of texture type 1 (the body), in the plan's
   order: `ChrModelTextureLayer.Layer`, then the element id. wow.export
   sorts by the texture target id instead; in the recorded table the two
   orders differ in 67 of 87 layouts, so which the client follows is
   unconfirmed (**[verify]**). A layer whose mask is -1 covers the whole
   canvas; any other layer is painted into each section its mask names (a
   mask with two or more bits does not occur in the recorded table, and
   painting it into each is unconfirmed). Layers of any other texture type
   (6 hair, 19 eyes, ...) are separate textures, never painted onto the
   body: they are listed in `not_painted`, and `separate_layers` gives them.
2. The plan's item body-texture pieces over them, ordered by the slot's
   texture layer (`ITEM_SLOT_LAYER`, wow.export's table: shirt and legs
   first, the cloak last; **[verify]**), then the component section, then
   the plan's order, each with blend mode 15 (wow.export paints a piece
   with the blend mode of the layout's layer for that section and turns 0
   and 1 into 15; every section layer of the recorded layouts is 15).

A texture is stretched to its section by whole-pixel repetition: its
section must be a whole multiple of it on each side (the real textures are
a half or a quarter of their sections: a 1024 x 512 skin on a 2048 x 1024
canvas, 128 x 64 item pieces in 512 x 256 sections). wow.export lets the
GPU magnify with linear filtering; nearest pixels are what the Lab can
afford in Python, so edges are blockier than in game (**[verify]** how the
client filters).

**Blend modes** (`ChrModelTextureLayer.BlendMode`, names from wow.export's
character shader; on texture type 1 the recorded table holds 1, 4, 6, 7, 9
and 15, all six painted here; 0, 2, 3, 5, 8 and 10 to 14 and anything
above 15 are refused with `BlendModeError`, the number named). With `d` the
canvas pixel, `l` the layer pixel, all channels 0 to 255 and
`m(x, y) = (x * y + 127) // 255`:

- 1 *blit*: the layer replaces the canvas, alpha included.
- 15 *alpha blend*: `s = l`; then the over step with `a = l.alpha`.
- 9 *alpha straight*: as 15, but the alpha channel is `a + m(d.a, 255 - a)`
  (`s.alpha` taken as 255).
- 4 *multiply*: `s = m(d, l)` on all four channels; over step with
  `a = s.alpha`.
- 7 *screen*: `s = 255 - m(255 - d, 255 - l)` on colour, `s.alpha = l.alpha`.
- 6 *overlay* (on the layer's value, as wow.export's shader has it):
  `m(2d, l)` where `l < 128`, else `255 - m(2(255 - d), 255 - l)`;
  `s.alpha = l.alpha`.

The over step, per channel: `m(s, a) + m(d, 255 - a)` (the alpha channel
too: wow.export's blend function `SRC_ALPHA, ONE_MINUS_SRC_ALPHA` applies
to all four). Each product is rounded on its own, so a channel can be one
below the exact value; the sum never exceeds 255.

**Bounds.** Everything is checked before the canvas is allocated: the
layout's size against `blp.MAX_DIMENSION` and `blp.MAX_PIXELS`
(`CanvasError`), every blend mode (`BlendModeError`), every section inside
the canvas (`SectionError`), then every texture's header (read, parsed,
dropped): its encoding decodable and its size at most its section's and a
whole fraction of it (`LayerSizeError`). Each texture is then read again
and decoded alone.

**What a composite costs.** Memory: the canvas (`W * H * 4` bytes, 8 MiB at
2048 x 1024), one texture file, one decode (`blp`'s peak, about three times
the texture's RGBA, which is at most its section's), row-sized
temporaries, and the final copy of the canvas: the peak is the canvas plus
the larger of three textures' RGBA and a second canvas, at most four
canvases plus one file (`compositor_peak_bytes`; 32 MiB at 2048 x 1024, 256
MiB at the 4096 x 4096 cap; 32 MiB measured with tracemalloc at 2048 x 1024
with a full-size base texture). Time (§17.5's cost model; CPU time on the
owner's M1, 2026-11-18): a blit row and a fully opaque alpha-blend row are
one slice copy per canvas row (0.01 s for a whole 2048 x 1024 canvas), the
transparent ends of a row cost nothing, and every other pixel costs two
byte-table lookups per channel for 15 and 9 (about 0.25 s per 2048 x 1024
pixels) and three for 4, 6 and 7 (about 0.48 s). The fixture characters
take 0.14 to 0.24 s with their reads and decodes; the busiest recorded body
layout with every pixel partial, 2.6 s.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Iterable
from functools import cache
from operator import itemgetter
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import blp, png
from wowlab_core.errors import WowlabError
from wowlab_core.renderplan import RenderPlan, Section, TextureLayer

__all__ = [
    "BLEND_NAMES",
    "BODY_TEXTURE_TYPE",
    "ITEM_BLEND_MODE",
    "ITEM_SLOT_LAYER",
    "BlendModeError",
    "BodyTexture",
    "CanvasError",
    "CompositorError",
    "LayerSizeError",
    "NotPainted",
    "PaintJob",
    "PaintedLayer",
    "SectionError",
    "TextureReader",
    "blend_rows",
    "compose",
    "compositor_peak_bytes",
    "paint_jobs",
    "separate_layers",
]

BODY_TEXTURE_TYPE = 1
"""The M2 texture type of the body texture; the only one composed here."""

BLIT, MULTIPLY, OVERLAY, SCREEN, ALPHA_STRAIGHT, ALPHA_BLEND = 1, 4, 6, 7, 9, 15

BLEND_NAMES: dict[int, str] = {
    BLIT: "blit",
    MULTIPLY: "multiply",
    OVERLAY: "overlay",
    SCREEN: "screen",
    ALPHA_STRAIGHT: "alpha straight",
    ALPHA_BLEND: "alpha blend",
}
"""The blend modes painted, by `ChrModelTextureLayer.BlendMode`."""

ITEM_BLEND_MODE = ALPHA_BLEND
"""The blend mode an item body-texture piece is painted with."""

ITEM_SLOT_LAYER: dict[int, int] = {
    4: 10,  # shirt
    7: 10,  # legs
    1: 11,  # head
    8: 11,  # feet
    3: 13,  # shoulders
    5: 13,  # chest
    19: 17,  # tabard
    6: 18,  # waist
    9: 19,  # wrists
    10: 20,  # hands
    16: 21,  # main hand
    17: 22,  # off hand
    15: 23,  # back
}
"""An equipment slot's texture layer: lower is painted first (wow.export's
table; a slot it does not list is 10)."""
_DEFAULT_SLOT_LAYER = 10

_CANVAS_START = b"\x00\x00\x00\xff"  # opaque black, as wow.export clears it


# ─── errors ──────────────────────────────────────────────────────────────────


class CompositorError(WowlabError):
    """The body texture cannot be composed from this plan and these files."""


class CanvasError(CompositorError):
    """The layout has no size, or one past `blp`'s limits."""


class BlendModeError(CompositorError):
    """A body layer's blend mode is not one of `BLEND_NAMES`."""

    def __init__(self, message: str, blend_mode: int, file_data_id: int) -> None:
        super().__init__(message)
        self.blend_mode = blend_mode
        self.file_data_id = file_data_id


class SectionError(CompositorError):
    """A section is empty or not inside the canvas."""


class LayerSizeError(CompositorError):
    """A texture is larger than its section, or its section is not a whole
    multiple of it."""

    def __init__(self, message: str, file_data_id: int) -> None:
        super().__init__(message)
        self.file_data_id = file_data_id


# ─── models ──────────────────────────────────────────────────────────────────


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TextureReader(Protocol):
    """Gives a file's bytes by file data id (`casc.CascSource.read`)."""

    def read(self, fdid: int) -> bytes | bytearray | memoryview: ...


class PaintJob(_Frozen):
    """One texture painted into one rectangle of the canvas. `kind`
    "customization": a plan layer (`layer`, `layer_row_id`, `element_id`);
    "item": an item piece (`slot`, `component_section`,
    `material_res_row_id`). `section_type` None: the whole canvas."""

    kind: Literal["customization", "item"]
    file_data_id: int
    blend_mode: int
    section_type: int | None
    x: int
    y: int
    width: int
    height: int
    layer: int | None = None
    layer_row_id: int | None = None
    element_id: int | None = None
    slot: int | None = None
    component_section: int | None = None
    material_res_row_id: int | None = None

    @property
    def blend(self) -> str:
        return BLEND_NAMES[self.blend_mode]


class PaintedLayer(_Frozen):
    """A job as painted: the texture's size and format and the whole-pixel
    scale it was stretched by."""

    job: PaintJob
    texture_width: int
    texture_height: int
    texture_format: blp.Format
    scale_x: int
    scale_y: int


class NotPainted(_Frozen):
    """A layer or piece of the plan not painted onto the body, and why."""

    kind: Literal["customization", "item"]
    file_data_id: int
    texture_type: int | None = None
    slot: int | None = None
    reason: str


class BodyTexture(_Frozen):
    """The composite: `width * height` pixels of RGBA, row by row from the
    top; what was painted, in order; what was not, and why."""

    width: int
    height: int
    rgba: bytes = Field(repr=False)
    painted: tuple[PaintedLayer, ...]
    not_painted: tuple[NotPainted, ...]

    def rows(self) -> list[bytes]:
        step = self.width * 4
        return [self.rgba[y * step : (y + 1) * step] for y in range(self.height)]

    def rgba_md5(self) -> str:
        return hashlib.md5(self.rgba, usedforsecurity=False).hexdigest()

    def png(self) -> bytes:
        return png.encode(self.width, self.height, self.rgba)


# ─── the plan's jobs ─────────────────────────────────────────────────────────


def compositor_peak_bytes(width: int, height: int) -> int:
    """The stated bound on the composite's working memory, a texture file's
    bytes aside: four canvases (the canvas and the larger of a decode's
    peak, about three times a texture no larger than the canvas, and the
    final copy)."""
    return 4 * width * height * 4


def _canvas_size(plan: RenderPlan) -> tuple[int, int]:
    width, height = plan.body.layout_width, plan.body.layout_height
    if width is None or height is None:
        raise CanvasError(
            f"texture layout {plan.body.layout_id} has no size, so there is no canvas to paint"
        )
    if width < 1 or height < 1:
        raise CanvasError(f"a canvas of {width} x {height}; both sides must be at least 1")
    if width > blp.MAX_DIMENSION or height > blp.MAX_DIMENSION or width * height > blp.MAX_PIXELS:
        raise CanvasError(
            f"a canvas of {width} x {height}; the cap is {blp.MAX_DIMENSION} a side and "
            f"{blp.MAX_PIXELS:,} pixels"
        )
    return width, height


def _check_section(section: Section, width: int, height: int, what: str) -> None:
    if section.width < 1 or section.height < 1:
        raise SectionError(
            f"{what}: section {section.section_type} is {section.width} x {section.height}"
        )
    if (
        section.x < 0
        or section.y < 0
        or section.x + section.width > width
        or section.y + section.height > height
    ):
        raise SectionError(
            f"{what}: section {section.section_type} at ({section.x}, {section.y}), "
            f"{section.width} x {section.height}, is not inside the {width} x {height} canvas"
        )


def separate_layers(plan: RenderPlan) -> tuple[TextureLayer, ...]:
    """The plan's layers of texture types other than the body's: hair (6),
    eyes (19) and the rest, each its own texture, never painted here."""
    return tuple(la for la in plan.layers if la.texture_type != BODY_TEXTURE_TYPE)


def paint_jobs(plan: RenderPlan) -> tuple[tuple[PaintJob, ...], tuple[NotPainted, ...]]:
    """What `compose` paints, in order, and what it leaves out, from the
    plan alone (no file is read). Refused: a canvas without a size or past
    the cap (`CanvasError`), a body layer's blend mode not in `BLEND_NAMES`
    (`BlendModeError`), a section that is empty or outside the canvas
    (`SectionError`)."""
    width, height = _canvas_size(plan)
    whole = Section(section_type=-1, x=0, y=0, width=width, height=height)
    jobs: list[PaintJob] = []
    skipped: list[NotPainted] = []
    for la in plan.layers:
        if la.texture_type != BODY_TEXTURE_TYPE:
            skipped.append(
                NotPainted(
                    kind="customization",
                    file_data_id=la.file_data_id,
                    texture_type=la.texture_type,
                    reason=f"texture type {la.texture_type} is a texture of its own, "
                    "not part of the body texture",
                )
            )
            continue
        what = f"layer {la.layer} (file {la.file_data_id})"
        if la.blend_mode not in BLEND_NAMES:
            raise BlendModeError(
                f"{what}: blend mode {la.blend_mode} is not one the compositor paints "
                f"({', '.join(f'{n} {name}' for n, name in sorted(BLEND_NAMES.items()))})",
                la.blend_mode,
                la.file_data_id,
            )
        sections: Iterable[Section] = (whole,) if la.whole_canvas else la.sections
        placed = False
        for section in sections:
            _check_section(section, width, height, what)
            placed = True
            jobs.append(
                PaintJob(
                    kind="customization",
                    file_data_id=la.file_data_id,
                    blend_mode=la.blend_mode,
                    section_type=None if la.whole_canvas else section.section_type,
                    x=section.x,
                    y=section.y,
                    width=section.width,
                    height=section.height,
                    layer=la.layer,
                    layer_row_id=la.layer_row_id,
                    element_id=la.element_id,
                )
            )
        if not placed:
            skipped.append(
                NotPainted(
                    kind="customization",
                    file_data_id=la.file_data_id,
                    texture_type=la.texture_type,
                    reason="the layer's masks name no section of the layout",
                )
            )
    order = sorted(
        range(len(plan.item_pieces)),
        key=lambda i: (
            ITEM_SLOT_LAYER.get(plan.item_pieces[i].slot, _DEFAULT_SLOT_LAYER),
            plan.item_pieces[i].component_section,
            i,
        ),
    )
    for i in order:
        piece = plan.item_pieces[i]
        if piece.section is None:
            skipped.append(
                NotPainted(
                    kind="item",
                    file_data_id=piece.file_data_id,
                    slot=piece.slot,
                    reason=f"the layout has no section {piece.component_section}",
                )
            )
            continue
        _check_section(
            piece.section, width, height, f"item {piece.item_id} (file {piece.file_data_id})"
        )
        jobs.append(
            PaintJob(
                kind="item",
                file_data_id=piece.file_data_id,
                blend_mode=ITEM_BLEND_MODE,
                section_type=piece.section.section_type,
                x=piece.section.x,
                y=piece.section.y,
                width=piece.section.width,
                height=piece.section.height,
                slot=piece.slot,
                component_section=piece.component_section,
                material_res_row_id=piece.material_res_row_id,
            )
        )
    return tuple(jobs), tuple(skipped)


def _scale(job: PaintJob, header: blp.BlpHeader) -> tuple[blp.Format, int, int]:
    """The texture's format and its whole-pixel scale into the job's
    section, from the header alone; refused when it does not fit."""
    fmt = blp.format_of(header)
    blp.check_size(header.width, header.height)
    tw, th = header.width, header.height
    if tw > job.width or th > job.height or job.width % tw or job.height % th:
        where = "the whole canvas" if job.section_type is None else f"section {job.section_type}"
        raise LayerSizeError(
            f"file {job.file_data_id} is {tw} x {th}, {where} is {job.width} x {job.height}: "
            "a texture must be no larger than its section and fit it a whole number of times "
            "on each side",
            job.file_data_id,
        )
    return fmt, job.width // tw, job.height // th


# ─── blending ────────────────────────────────────────────────────────────────


def _m(x: int, y: int) -> int:
    return (x * y + 127) // 255


@cache
def _table(kind: str) -> bytes:
    """A 65,536-byte table indexed by `hi << 8 | lo`."""
    if kind == "mul":
        values = (_m(hi, lo) for hi in range(256) for lo in range(256))
    elif kind == "screen":
        values = (255 - _m(255 - hi, 255 - lo) for hi in range(256) for lo in range(256))
    else:  # overlay: hi the canvas, lo the layer
        values = (
            _m(2 * hi, lo) if lo < 128 else 255 - _m(2 * (255 - hi), 255 - lo)
            for hi in range(256)
            for lo in range(256)
        )
    return bytes(values)


_INVERT = bytes(255 - v for v in range(256))
_LITTLE = sys.byteorder == "little"


def _lookup(table: bytes, hi: bytes | bytearray, lo: bytes | bytearray) -> bytes:
    """`table[hi[i] << 8 | lo[i]]` for each i. `itemgetter` over the keys
    (about 40% faster than `map` here) returns a tuple only for two keys or
    more; a pixel is four."""
    keys = bytearray(2 * len(lo))
    if _LITTLE:
        keys[0::2], keys[1::2] = lo, hi
    else:
        keys[0::2], keys[1::2] = hi, lo
    view = memoryview(keys).cast("H")
    if len(view) < 2:
        return bytes(map(table.__getitem__, view))
    return bytes(itemgetter(*view)(table))


def _spread_alpha(alpha: bytes | bytearray) -> bytearray:
    out = bytearray(4 * len(alpha))
    for c in range(4):
        out[c::4] = alpha
    return out


def _over(dst: bytes | bytearray, src: bytes | bytearray, a4: bytes | bytearray) -> bytes:
    """`m(src, a) + m(dst, 255 - a)` per byte; never above 255, so the two
    byte strings add as integers without a carry."""
    mul = _table("mul")
    total = int.from_bytes(_lookup(mul, a4, src), "little") + int.from_bytes(
        _lookup(mul, a4.translate(_INVERT), dst), "little"
    )
    return total.to_bytes(len(dst), "little")


def blend_rows(mode: int, dst: bytes | bytearray, src: bytes | bytearray) -> bytes:
    """One run of RGBA pixels of the layer (`src`) blended onto the canvas
    (`dst`) with `mode` (the module docstring's formulas)."""
    if len(dst) != len(src) or len(src) % 4:
        raise ValueError(f"{len(dst)} canvas bytes and {len(src)} layer bytes")
    alpha = src[3::4]
    if mode == BLIT:
        return bytes(src)
    if mode == ALPHA_BLEND:
        return _over(dst, src, _spread_alpha(alpha))
    if mode == ALPHA_STRAIGHT:
        solid = bytearray(src)
        solid[3::4] = b"\xff" * len(alpha)
        return _over(dst, solid, _spread_alpha(alpha))
    if mode == MULTIPLY:
        product = _lookup(_table("mul"), dst, src)
        return _over(dst, product, _spread_alpha(product[3::4]))
    if mode in (SCREEN, OVERLAY):
        mixed = bytearray(_lookup(_table("screen" if mode == SCREEN else "overlay"), dst, src))
        mixed[3::4] = alpha
        return _over(dst, mixed, _spread_alpha(alpha))
    raise BlendModeError(f"blend mode {mode} is not one the compositor paints", mode, 0)


def _widen(row: bytes | bytearray, k: int) -> bytes | bytearray:
    """Each pixel of `row` repeated `k` times."""
    if k == 1:
        return row
    out = bytearray(len(row) * k)
    wide = memoryview(out).cast("I")
    narrow = memoryview(row).cast("I")
    for j in range(k):
        wide[j::k] = narrow
    return out


def _paint(
    canvas: bytearray, stride: int, job: PaintJob, image: blp.BlpImage, kx: int, ky: int
) -> None:
    """Paint `image` into the job's rectangle, `kx` x `ky` canvas pixels a
    texture pixel. Per texture row: a blit is one slice copy per canvas row;
    otherwise the transparent pixels at either end are left out (they change
    nothing in any mode), a row with nothing else is skipped, an opaque
    alpha blend is a slice copy, and an alpha blend's layer half,
    `m(s, a)`, is computed once at the texture's size; only the canvas half
    is per canvas pixel. The result is `blend_rows`' byte for byte."""
    mode = job.blend_mode
    tw = image.width
    line = tw * 4
    rgba = image.rgba
    mul = _table("mul")
    for r in range(image.height):
        src = rgba[r * line : (r + 1) * line]
        first, end = 0, tw
        if mode != BLIT:
            alpha = src[3::4]
            first = tw - len(alpha.lstrip(b"\x00"))
            if first == tw:
                continue  # nothing of this row shows, whatever the mode
            end = len(alpha.rstrip(b"\x00"))
            src = src[first * 4 : end * 4]
        alpha = src[3::4]
        span = (end - first) * kx * 4
        left = (job.x + first * kx) * 4
        top = job.y + r * ky
        blending = mode in (ALPHA_BLEND, ALPHA_STRAIGHT)
        if mode == BLIT or (blending and alpha.count(255) == len(alpha)):
            wide = _widen(src, kx)
            for y in range(top, top + ky):
                at = y * stride + left
                canvas[at : at + span] = wide
            continue
        if blending:
            a4 = _spread_alpha(alpha)
            if mode == ALPHA_STRAIGHT:
                solid = bytearray(src)
                solid[3::4] = b"\xff" * len(alpha)
                src = bytes(solid)
            layer_half = int.from_bytes(_widen(_lookup(mul, a4, src), kx), "little")
            inverse = _widen(a4.translate(_INVERT), kx)
            for y in range(top, top + ky):
                at = y * stride + left
                total = layer_half + int.from_bytes(
                    _lookup(mul, inverse, canvas[at : at + span]), "little"
                )
                canvas[at : at + span] = total.to_bytes(span, "little")
            continue
        wide = _widen(src, kx)
        for y in range(top, top + ky):
            at = y * stride + left
            canvas[at : at + span] = blend_rows(mode, canvas[at : at + span], wide)


# ─── the composite ───────────────────────────────────────────────────────────


def compose(plan: RenderPlan, reader: TextureReader) -> BodyTexture:
    """The plan's body texture. Refusals, all before the canvas is
    allocated: `CanvasError`, `BlendModeError`, `SectionError`,
    `LayerSizeError`, and `blp`'s own errors for a texture it does not
    decode; the reader's errors (`casc.FileUnavailable`, ...) pass
    through."""
    width, height = _canvas_size(plan)
    jobs, skipped = paint_jobs(plan)
    fits: list[tuple[blp.Format, int, int]] = []
    for job in jobs:  # every header first: nothing is allocated for a plan that fails
        fits.append(_scale(job, blp.parse_header(reader.read(job.file_data_id))))
    canvas = bytearray(_CANVAS_START * (width * height))
    stride = width * 4
    painted: list[PaintedLayer] = []
    for job, (fmt, kx, ky) in zip(jobs, fits, strict=True):
        image = blp.decode(reader.read(job.file_data_id))
        _paint(canvas, stride, job, image, kx, ky)
        painted.append(
            PaintedLayer(
                job=job,
                texture_width=image.width,
                texture_height=image.height,
                texture_format=fmt,
                scale_x=kx,
                scale_y=ky,
            )
        )
        del image
    return BodyTexture(
        width=width,
        height=height,
        rgba=bytes(canvas),
        painted=tuple(painted),
        not_painted=skipped,
    )
