# Probe from review of m15/27-sheathed-held-items; reproduces a sheath bone (or
# its parent) with a pivot that is not a finite number failing the whole model.
"""Constructed (hostile). M15-27: "An item whose point, bone or sheathe type
cannot be resolved stays not drawn, with the reason." A body model whose
sheath bone, or that bone's parent, stores a NaN or infinite pivot is turned
into a non-finite translation by `charmodel._sheath`, which `gltf.encode_glb`
then refuses for the whole scene (`GltfError`): no `.glb` at all, instead of
the one weapon left out with a reason. On `main` these bones were never
read, so such a body model drew. The positive control is the same body with
finite pivots, which draws the staff sheathed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wowlab_core import charmodel, renderplan

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parser"))

import _charmodel_build as cb

LABEL = "Dangling-Participle"  # its staff goes to sheath point 30


def _build(which: str | None) -> charmodel.CharModel:
    tables = cb.tables()
    bare = renderplan.plan_for_record(tables, cb.record(LABEL))
    fdid = bare.body.file_data_id
    sheath, bones, loops = cb.fixture_sheath(fdid)
    bone = next(b for point, b, _ in sheath if point == 30)
    bones = list(bones)
    if which == "bone":
        bones[bone] = (bones[bone][0], (float("nan"), 0.0, 1.0), bones[bone][2])
    elif which == "parent":
        parent = bones[bone][0]
        bones[parent] = (bones[parent][0], (float("inf"), 0.0, 1.0), None)
    files = cb.files_for(bare, cb.ATTACHMENTS)
    files.update(
        cb.body_files(fdid, cb.ORIGIN_HANDS + sheath, bone_list=bones, global_sequences=loops)
    )
    source = cb.Source(files)
    plan = renderplan.plan_for_record(tables, cb.record(LABEL), statter=source)
    return charmodel.build(plan, source)


def test_positive_control_finite_pivots_draw_the_staff_sheathed_constructed() -> None:
    built = _build(None)
    (staff,) = [m for m in built.models if m.what == "item"]
    assert staff.placed and staff.sheathed and staff.sheath_point == 30


@pytest.mark.parametrize("which", ["bone", "parent"])
def test_hostile_pivot_not_finite_leaves_the_item_out_with_a_reason_constructed(
    which: str,
) -> None:
    built = _build(which)  # today: GltfError, "a translation that is not a finite number"
    (staff,) = [m for m in built.models if m.what == "item"]
    assert not staff.sheathed and staff.sheath_reason
    assert built.facts.nodes == 1
