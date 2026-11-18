# Probe from review of m15/10-compositor; reproduces a texture that is larger on its second read painting past its section and growing BodyTexture.rgba past width * height * 4
"""Constructed (L8: hostile input, labelled; no real file is needed).

`compositor.compose` reads each texture twice: once for the header (the
size check against the section, `_scale`) and once to decode. It never
checks that the decoded image is the size the header pass approved; `_paint`
uses the decoded size with the scale computed from the first read. A reader
that returns a larger texture the second time (an install patched between
the two reads, or any `TextureReader` that is not `CascSource`) paints past
the section, and the bytearray slice assignment past the canvas's end
extends it: a `BodyTexture` of 8 x 8 whose `rgba` is not 256 bytes, which
`png()` and every later consumer then trust.

The positive control is the same plan with a reader that returns the same
bytes on every read.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
from _blp_build import blp_file

from wowlab_core import blp, compositor
from wowlab_core.compositor import CompositorError
from wowlab_core.renderplan import (
    BodyModel,
    GeosetPlan,
    LookAsOf,
    RenderPlan,
    Section,
    TextureLayer,
)


def _texture(side: int, value: int) -> bytes:
    return blp_file(side, side, blp.ENCODING_BGRA, 8, 8, bytes((value, 0, 0, 255)) * side * side)


def _layer(fdid: int, order: int, sections: tuple[Section, ...]) -> TextureLayer:
    return TextureLayer(
        element_id=1000 + order,
        choice_id=1,
        material_id=1,
        texture_target_id=order + 1,
        material_resources_id=1,
        file_data_id=fdid,
        usage_type=0,
        layer_row_id=500 + order,
        texture_type=1,
        layer=order,
        blend_mode=1,
        section_mask=-1 if not sections else sum(1 << s.section_type for s in sections),
        section_mask2=-1,
        whole_canvas=not sections,
        sections=sections,
        layout_id=1,
        layout_from_chr_model_id=1,
    )


def _plan() -> RenderPlan:
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
        layout_width=8,
        layout_height=8,
    )
    corner = Section(section_type=3, x=4, y=4, width=4, height=4)
    return RenderPlan(
        build="0.0.0.0",
        class_id=None,
        body=body,
        look=LookAsOf(source="defaults"),
        choices=(),
        not_chosen=(),
        geosets=GeosetPlan(),
        layers=(_layer(1, 0, ()), _layer(2, 1, (corner,))),
        unplaced=(),
        unsupported=(),
        item_pieces=(),
        files=(),
        notes=(),
    )


class _Reader:
    """File 2 is 2 x 2 on its first read; with `grows`, 8 x 8 after."""

    def __init__(self, grows: bool) -> None:
        self.grows = grows
        self.reads_of_2 = 0

    def read(self, fdid: int) -> bytes:
        if fdid == 1:
            return _texture(8, 9)
        self.reads_of_2 += 1
        if self.grows and self.reads_of_2 > 1:
            return _texture(8, 200)
        return _texture(2, 200)


def _rgba_or_refusal(grows: bool) -> int | str:
    try:
        return len(compositor.compose(_plan(), _Reader(grows)).rgba)
    except CompositorError as exc:
        return type(exc).__name__


def test_positive_control_a_stable_reader_gives_one_canvas_constructed() -> None:
    assert _rgba_or_refusal(grows=False) == 8 * 8 * 4


def test_a_texture_larger_on_its_second_read_is_refused_constructed() -> None:
    got = _rgba_or_refusal(grows=True)
    assert got == "LayerSizeError" or got == 8 * 8 * 4, f"rgba of {got} bytes for an 8 x 8 canvas"
