"""`charmodel`: held items drawn sheathed (M15-27, §17.6, the note of
2026-11-19).

Two kinds of input, each labelled. *Constructed:* the transform arithmetic
(a bone with a known rotation and pivot under a parent with a known
transform) and the cases that cannot be resolved. *Derived:* the two
fixture body models (959310 and 917116 at 1.60.1.70959) rebuilt by
`_charmodel_build.fixture_body_files` from `macos-70959/derived/
charmodel.json` (the sheath points 26 to 33, their bones' pivots and
parents, and each sheath bone's recorded first rotation key), with the real
`macos-70450` lab-addon records and the recorded wago tables for the plans.
No install is read. Every placement here is arithmetic on recorded numbers;
whether it is where the game draws the weapon is the owner's look in a
glTF viewer, and nothing here can say so.
"""

from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path

import pytest

from wowlab_core import charmodel, gltf, renderplan

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

import _charmodel_build as cb
from _glb_read import read_glb

LABELS = ("Sangraal-Sett", "Dangling-Participle")
# The `.glb` and PNG MD5s `main` wrote (commit e139432, before M15-27) for
# each fixture character on the constructed body with its hand points at
# the origin, as the real body models store them (`cb.ORIGIN_HANDS`), and
# with them away from it (`cb.ATTACHMENTS`). The bones and sheath points
# the derived body adds draw nothing on their own, so `held=False` must give
# these bytes on either body.
MAIN_GLB = {
    ("Sangraal-Sett", "origin"): "1f887d78b0639d4dcfc88cf0eb99c3ff",
    ("Sangraal-Sett", "hands"): "f6d7a0a4dd710dde57e3575483d0515f",
    ("Dangling-Participle", "origin"): "753cc83854102d743c387a9061249550",
    ("Dangling-Participle", "hands"): "3f5e8476f3faa10fde1d1ad43e016a7e",
}
MAIN_PNG = {
    "body": "d25473d55139ca4c49b4f8b243690a93",
    "hair": "2e59a51beca11559e5df264b0a92cd50",
    "eyes": "2e59a51beca11559e5df264b0a92cd50",
}
# What the recorded `Item` subset gives each fixture weapon (§17.6, M15-24's
# note), and the point `SHEATH_POINTS` then takes.
EXPECTED = {
    "Sangraal-Sett": {2632: (3, 32), 15969: (3, 33)},
    "Dangling-Participle": {5778: (2, 30)},
}


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _built(
    label: str, *, held: bool = True, fixture: bool = True, attachments: tuple = cb.ORIGIN_HANDS
) -> tuple[renderplan.RenderPlan, charmodel.CharModel]:
    tables = cb.tables()
    bare = renderplan.plan_for_record(tables, cb.record(label))
    source = cb.Source(cb.files_for(bare, attachments, fixture=fixture))
    plan = renderplan.plan_for_record(tables, cb.record(label), statter=source)
    return plan, charmodel.build(plan, source, held=held)


def _length(q: tuple[float, ...] | list[float]) -> float:
    return math.sqrt(sum(v * v for v in q))


# ─── the arithmetic, constructed ─────────────────────────────────────────────

QUARTER = math.sin(math.pi / 4), math.cos(math.pi / 4)
TURN_Z = (0.0, 0.0, QUARTER[0], QUARTER[1])  # 90 degrees about z
TURN_X = (QUARTER[0], 0.0, 0.0, QUARTER[1])  # 90 degrees about x


def test_a_bone_turns_a_point_about_its_pivot_constructed() -> None:
    pose = charmodel.BonePose(pivot=(1.0, 2.0, 3.0), rotation=TURN_Z)
    moved = charmodel.apply(charmodel.bone_transform(pose), (2.0, 2.0, 3.0))
    assert moved == pytest.approx((1.0, 3.0, 3.0))
    # The pivot itself stays put: a sheath point stored at its bone's pivot
    # keeps its stored position, and only its axes turn.
    assert charmodel.apply(charmodel.bone_transform(pose), pose.pivot) == pytest.approx(pose.pivot)


def test_a_bone_under_a_parent_with_a_known_transform_constructed() -> None:
    """Child: pivot (1, 0, 0), a quarter turn about z. Parent: pivot at the
    origin, a quarter turn about x, then moved (0, 0, 5). A marker at
    (2, 0, 0): the child turns it to (1, 1, 0); the parent turns that to
    (1, 0, 1) and moves it to (1, 0, 6)."""
    child = charmodel.BonePose(pivot=(1.0, 0.0, 0.0), rotation=TURN_Z)
    parent = charmodel.BonePose(pivot=(0.0, 0.0, 0.0), rotation=TURN_X, translation=(0.0, 0.0, 5.0))
    world = charmodel.chain_transform((child, parent))
    assert charmodel.apply(world, (2.0, 0.0, 0.0)) == pytest.approx((1.0, 0.0, 6.0))
    # The world rotation is the parent's after the child's.
    assert charmodel.rotate(world[0], (1.0, 0.0, 0.0)) == pytest.approx((0.0, 0.0, 1.0))
    # A parent away from the origin turns about its own pivot.
    lifted = parent.model_copy(update={"pivot": (0.0, 0.0, 1.0)})
    world = charmodel.chain_transform((child, lifted))
    # (1, 1, 0) about (0, 0, 1): (1, 1, -1) turns to (1, 1, 1), back to (1, 1, 2), up 5.
    assert charmodel.apply(world, (2.0, 0.0, 0.0)) == pytest.approx((1.0, 1.0, 7.0))


def test_parents_in_the_rest_pose_move_nothing_constructed() -> None:
    child = charmodel.BonePose(pivot=(0.5, 0.0, 1.5), rotation=TURN_Z)
    rest = (charmodel.BonePose(pivot=(0.3, 0.2, 1.0)), charmodel.BonePose(pivot=(9.0, 9.0, 9.0)))
    alone = charmodel.chain_transform((child,))
    chained = charmodel.chain_transform((child, *rest))
    assert chained[0] == pytest.approx(alone[0]) and chained[1] == pytest.approx(alone[1])


def test_a_rotation_maps_to_y_up_as_its_vectors_do_constructed() -> None:
    q = (0.304422, 0.449751, 0.638203, 0.545610)
    v = (0.2, -0.7, 1.1)
    turned = gltf.to_y_up(*charmodel.rotate(q, v))
    assert charmodel.rotate(gltf.quaternion_to_y_up(*q), gltf.to_y_up(*v)) == pytest.approx(turned)


# ─── the fixture characters, derived ─────────────────────────────────────────


@pytest.mark.parametrize("label", LABELS)
def test_the_fixture_weapons_are_drawn_sheathed_with_the_caveat_derived(label: str) -> None:
    plan, built = _built(label)
    held = {m.item_id: m for m in built.models if m.what == "item"}
    assert set(held) == set(EXPECTED[label])
    body = plan.body.file_data_id
    row = cb.derived_character(body)
    points = {p["id"]: p for p in row["other_attachment_points"]}  # type: ignore[attr-defined]
    keys = row["sheath_rotation_keys"]
    for item_id, (sheathe, point) in EXPECTED[label].items():
        model = next(m for m in plan.item_models if m.item_id == item_id)
        assert model.sheathe_type == sheathe
        drawn = held[item_id]
        assert drawn.placed and drawn.sheathed and drawn.sheath_point == point
        assert drawn.placement == charmodel.SHEATHED
        assert drawn.placement == "drawn sheathed, placement approximate (not yet confirmed)"
        assert drawn.sheath_reason is None and not drawn.at_origin
        # The point is stored at its bone's pivot, so turning about the pivot
        # leaves it where it is stored.
        assert drawn.position == pytest.approx(gltf.to_y_up(*points[point]["position"]), abs=1e-5)
        first = keys[str(point)]["rotation"]["first_array"]["first_value"]  # type: ignore[index]
        norm = _length(first)
        expected = gltf.quaternion_to_y_up(*(v / norm for v in first))
        assert drawn.rotation is not None
        assert drawn.rotation == pytest.approx(expected, abs=1e-4)


@pytest.mark.parametrize("label", LABELS)
def test_the_glb_holds_each_sheathed_item_with_a_valid_transform_derived(label: str) -> None:
    _, built = _built(label)
    glb = read_glb(built.glb)
    sheathed = [m for m in built.models if m.sheathed]
    assert len(glb.nodes) == built.facts.nodes == 1 + len(sheathed)
    assert "rotation" not in glb.nodes[0] and "translation" not in glb.nodes[0]
    for node, model in zip(glb.nodes[1:], sheathed, strict=True):
        assert node["name"] == f"item {model.item_id} (slot {model.slot})"
        rotation, translation = node["rotation"], node["translation"]
        assert len(rotation) == 4 and abs(_length(rotation) - 1) < 1e-6
        assert len(translation) == 3 and all(math.isfinite(v) for v in translation)
        assert rotation == pytest.approx(model.rotation) and translation == pytest.approx(
            model.position
        )
        # On the back or the hip: above the knees and below the head of a
        # body model about two units tall (Y-up), never at its feet.
        assert 1.0 < translation[1] < 2.2


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("body", ["origin", "hands"])
def test_no_held_writes_mains_bytes_derived(label: str, body: str) -> None:
    """`held=False` gives the `.glb` and PNGs `main` gave before M15-27, on
    the derived body (bones and sheath points added) and on the constructed
    one with its hand points away from the origin."""
    if body == "origin":
        _, built = _built(label, held=False)
    else:
        _, built = _built(label, held=False, fixture=False, attachments=cb.ATTACHMENTS)
    assert _md5(built.glb) == MAIN_GLB[(label, body)]
    assert {p.name: _md5(p.png) for p in built.pngs} == MAIN_PNG
    assert not any(m.sheathed or m.sheath_reason for m in built.models)


@pytest.mark.parametrize("label", LABELS)
def test_held_items_change_only_the_item_nodes_derived(label: str) -> None:
    _, sheathed = _built(label)
    _, bare = _built(label, held=False)
    assert {p.name: _md5(p.png) for p in sheathed.pngs} == MAIN_PNG
    assert sheathed.models[0] == bare.models[0]
    assert sheathed.body_texture == bare.body_texture
    with_items, without = read_glb(sheathed.glb), read_glb(bare.glb)
    assert with_items.nodes[0] == without.nodes[0] and len(without.nodes) == 1


# ─── what cannot be resolved, constructed ────────────────────────────────────


def _with(label: str, update: dict[str, object]) -> renderplan.RenderPlan:
    tables = cb.tables()
    bare = renderplan.plan_for_record(tables, cb.record(label))
    models = tuple(m.model_copy(update=update) for m in bare.item_models)
    return bare.model_copy(update={"item_models": models})


def _build(plan: renderplan.RenderPlan, files: dict[int, bytes]) -> charmodel.CharModel:
    return charmodel.build(plan, cb.Source(files))


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"sheathe_type": None}, "the game's tables give no sheathe type for this item"),
        (
            {"sheathe_type": 0},
            "its sheathe type in the game's tables (0) is not one wowlab places",
        ),
        (
            {"sheathe_type": 7},
            "its sheathe type in the game's tables (7) is not one wowlab places",
        ),
    ],
)
def test_an_unresolved_sheathe_type_stays_not_drawn_with_a_plain_reason_constructed(
    update: dict[str, object], reason: str
) -> None:
    plan = _with("Dangling-Participle", update)
    built = _build(plan, cb.files_for(plan, fixture=True))
    (staff,) = [m for m in built.models if m.what == "item"]
    assert not staff.placed and not staff.sheathed and staff.at_origin
    assert staff.sheath_reason == reason
    assert staff.reason is not None and staff.reason.startswith(reason + ", so it is not drawn")
    assert _md5(built.glb) == MAIN_GLB[("Dangling-Participle", "origin")]


def test_a_body_without_the_point_stays_not_drawn_constructed() -> None:
    plan = _with("Dangling-Participle", {})
    built = _build(plan, cb.files_for(plan, cb.ORIGIN_HANDS))
    (staff,) = [m for m in built.models if m.what == "item"]
    assert not staff.placed and staff.sheath_reason == "the body model has no sheath point 30"


def test_a_body_without_the_point_but_a_hand_point_draws_as_before_constructed() -> None:
    """A constructed body that stores its hand points away from the origin
    and has no sheath points: the item is placed at its hand point, as
    before, and says why it is not sheathed."""
    plan = _with("Dangling-Participle", {})
    built = _build(plan, cb.files_for(plan, cb.ATTACHMENTS))
    (staff,) = [m for m in built.models if m.what == "item"]
    assert staff.placed and not staff.sheathed and staff.attachment_id == 1
    assert staff.sheath_reason == "the body model has no sheath point 30"
    assert _md5(built.glb) == MAIN_GLB[("Dangling-Participle", "hands")]


def _fixture_files_with(
    plan: renderplan.RenderPlan, bone_update: tuple[object, ...] | None
) -> dict[int, bytes]:
    """The derived body with the bone under point 30 replaced."""
    fdid = plan.body.file_data_id
    sheath, bones, loops = cb.fixture_sheath(fdid)
    bone = next(b for p, b, _ in sheath if p == 30)
    parent, pivot, _ = bones[bone]
    bones[bone] = (parent, pivot, bone_update)  # type: ignore[assignment]
    out = cb.files_for(plan, fixture=True)
    out.update(
        cb.body_files(fdid, cb.ORIGIN_HANDS + sheath, bone_list=bones, global_sequences=loops)
    )
    return out


@pytest.mark.parametrize(
    ("track", "why"),
    [
        (None, "the bone of sheath point 30 gives no angle (its rotation is not on a global"),
        ((0, 1, []), "the bone of sheath point 30 gives no angle (its rotation track holds no"),
        ((0, 1, [(0, 0, 0, 0)]), "the bone of sheath point 30 gives no angle (its key is not a"),
        ((9, 1, [(0, 0, 0, -1)]), "the bone of sheath point 30 cannot be read: a bone's rotation"),
    ],
)
def test_a_bone_without_an_angle_stays_not_drawn_constructed(
    track: tuple[object, ...] | None, why: str
) -> None:
    """(0, 0, 0, 0) as i16 decodes to (-1, -1, -1, -1), length 2: not a
    rotation. Global sequence 9 is past the body's."""
    plan = _with("Dangling-Participle", {})
    built = _build(plan, _fixture_files_with(plan, track))
    (staff,) = [m for m in built.models if m.what == "item"]
    assert not staff.placed and staff.sheath_reason is not None
    assert staff.sheath_reason.startswith(why)
    # The player reads plain words; the detail stays in `sheath_reason`.
    assert staff.reason == (
        "the game's model file does not hold what wowlab needs to place it on the back or hip, "
        "so it is not drawn sheathed, and in this pose the model keeps no place for it in the hand"
    )
    assert "sequence" not in staff.reason and "bone" not in staff.reason


def test_a_non_held_item_is_never_sheathed_constructed() -> None:
    plan = _with("Sangraal-Sett", {})
    models = list(plan.item_models)
    models[1] = models[1].model_copy(update={"slot": 3, "attachment_id": 6})
    plan = plan.model_copy(update={"item_models": tuple(models)})
    built = _build(plan, cb.files_for(plan, fixture=True))
    shoulder = next(m for m in built.models if m.slot == 3)
    assert not shoulder.sheathed and shoulder.sheath_reason is None


def test_the_sheath_map_is_the_documented_assumption() -> None:
    assert charmodel.SHEATH_POINTS == {1: (26, 27), 2: (30, 31), 3: (32, 33), 4: (28, 28)}
