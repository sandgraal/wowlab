# Probe from review of m15/10-compositor; reproduces a texture whose mip 0 lies past the file's end refused only after the 4096 x 4096 canvas is allocated
"""Constructed (L8: hostile input, labelled; no real file is needed).

`compositor.compose`'s docstring promises every refusal, `blp`'s own errors
for a texture it does not decode included, before the canvas is allocated.
The header pass (`_scale`) checks the encoding and the size, not that mip 0
lies inside the file, so a truncated texture passes it and `blp.decode`
refuses it only after `bytearray(... width * height)`: a 64 MiB canvas for a
plan that was always going to fail.

The positive control is the same plan with a texture too large for its
section: refused from the header pass, far under one canvas.
"""

from __future__ import annotations

import sys
import tracemalloc
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
from _blp_build import blp_file

from wowlab_core import blp, compositor
from wowlab_core.renderplan import (
    BodyModel,
    GeosetPlan,
    LookAsOf,
    RenderPlan,
    Section,
    TextureLayer,
)

SIDE = 4096
LIMIT = 4 * 1024 * 1024  # far less than one 64 MiB canvas


def _texture(width: int, height: int) -> bytes:
    return blp_file(width, height, blp.ENCODING_BGRA, 8, 8, b"\x10\x20\x30\xff" * (width * height))


def _layer(fdid: int, order: int, section: Section) -> TextureLayer:
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
        blend_mode=15,
        section_mask=1 << section.section_type,
        section_mask2=-1,
        whole_canvas=False,
        sections=(section,),
        layout_id=1,
        layout_from_chr_model_id=1,
    )


def _plan(layers: list[TextureLayer]) -> RenderPlan:
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
        layout_width=SIDE,
        layout_height=SIDE,
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
        item_pieces=(),
        files=(),
        notes=(),
    )


class _Reader:
    def __init__(self, files: dict[int, bytes]) -> None:
        self.files = files

    def read(self, fdid: int) -> bytes:
        return self.files[fdid]


def _peak_of_refusal(files: dict[int, bytes], error: type[Exception]) -> int:
    section = Section(section_type=2, x=0, y=0, width=8, height=8)
    the_plan = _plan([_layer(1, 0, section)])
    tracemalloc.start()
    try:
        with pytest.raises(error):
            compositor.compose(the_plan, _Reader(files))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def test_positive_control_a_too_large_texture_is_refused_before_the_canvas_constructed() -> None:
    assert _peak_of_refusal({1: _texture(16, 16)}, compositor.LayerSizeError) < LIMIT


def test_a_truncated_texture_is_refused_before_the_canvas_constructed() -> None:
    truncated = _texture(8, 8)[:-16]  # mip 0 now ends past the file
    peak = _peak_of_refusal({1: truncated}, blp.BlpMalformedError)
    assert peak < LIMIT, f"refused after {peak / 2**20:.0f} MiB were allocated"
