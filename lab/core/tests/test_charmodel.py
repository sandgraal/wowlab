"""`charmodel`: a render plan drawn as one glTF scene (M15-11, §17.6).

The plans are real: the `macos-70450` lab-addon records of the two fixture
characters against the recorded wago tables at 1.60.1.70959. The files
are constructed (`_charmodel_build`, labelled `constructed`): a body M2
with one triangle per chosen geoset id and the body's texture types, a
one-section M2 per item model, and a 4 x 4 texture for every texture id.
Each `.glb` is re-read by `_glb_read`, a stdlib reader written from the
glTF specification. Nothing here reads or writes a file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wowlab_core import charmodel, renderplan

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _charmodel_build as cb
from _glb_read import read_glb

PLANS: dict[str, renderplan.RenderPlan] = {}


def _plan(label: str, source: cb.Source | None = None) -> renderplan.RenderPlan:
    tables = cb.tables()
    if source is None:
        bare = renderplan.plan_for_record(tables, cb.record(label))
        source = cb.Source(cb.files_for(bare))
    return renderplan.plan_for_record(tables, cb.record(label), statter=source)


def _built(label: str) -> tuple[renderplan.RenderPlan, cb.Source, charmodel.CharModel]:
    bare = renderplan.plan_for_record(cb.tables(), cb.record(label))
    source = cb.Source(cb.files_for(bare))
    plan = _plan(label, source)
    return plan, source, charmodel.build(plan, source)


@pytest.mark.parametrize("label", ["Sangraal-Sett", "Dangling-Participle"])
def test_one_node_per_placed_model_and_the_glb_reads_back_constructed(label: str) -> None:
    plan, _, built = _built(label)
    glb = read_glb(built.glb)
    placed = [m for m in plan.item_models if m.kind != "no attachment known"]
    assert len(glb.nodes) == 1 + len(placed) == built.facts.nodes
    assert glb.nodes[0]["name"] == "body"
    assert [n["name"] for n in glb.nodes[1:]] == [
        f"item {m.item_id} (slot {m.slot})" for m in placed
    ]
    assert [m.what for m in built.models] == ["body", *["item"] * len(plan.item_models)]
    assert built.facts.bytes == len(built.glb)
    assert built.left_out == ()


def test_the_body_shows_exactly_the_plans_visible_geosets_constructed() -> None:
    plan, _, built = _built("Sangraal-Sett")
    visible = renderplan.visible_geosets(plan.geosets, cb.BODY_GEOSETS)
    body = built.models[0]
    assert (body.sections, body.drawn) == (len(cb.BODY_GEOSETS), len(visible))
    assert body.triangles == len(visible) and 1701 not in visible
    glb = read_glb(built.glb)
    mesh = glb.document["meshes"][glb.nodes[0]["mesh"]]
    assert sum(
        glb.document["accessors"][p["indices"]]["count"] // 3 for p in mesh["primitives"]
    ) == (len(visible))


def test_attached_items_sit_at_their_attachment_point_y_up_constructed() -> None:
    plan, _, built = _built("Sangraal-Sett")
    glb = read_glb(built.glb)
    points = {a: p for a, _, p in cb.ATTACHMENTS}
    for node, model in zip(glb.nodes[1:], plan.item_models, strict=True):
        assert model.attachment_id is not None
        x, y, z = points[model.attachment_id]
        assert node["translation"] == pytest.approx([x, z, -y])


def test_the_textures_are_the_body_hair_eyes_and_the_rest_constructed() -> None:
    _, _, built = _built("Sangraal-Sett")
    assert [p.name for p in built.pngs] == ["body", "hair", "eyes"]
    body = built.pngs[0]
    assert (body.width, body.height, body.file_data_id) == (2048, 1024, None)
    assert built.body_texture.composed
    assert len(built.body_texture.painted) == 5 + 11
    glb = read_glb(built.glb)
    materials = glb.document["materials"]
    assert materials[0]["alphaMode"] == "OPAQUE"  # the body, whatever its alpha
    hair = [m for m in materials if m["alphaMode"] == "MASK"]
    assert hair and all(m["doubleSided"] for m in hair)
    # body, hair, eyes, the cape, the body's own file, two weapon textures
    assert built.facts.images == 7


def test_a_body_model_that_is_not_local_ends_the_run_naming_it_constructed() -> None:
    bare = renderplan.plan_for_record(cb.tables(), cb.record("Sangraal-Sett"))
    files = cb.files_for(bare)
    del files[bare.body.file_data_id]
    source = cb.Source(files)
    plan = _plan("Sangraal-Sett", source)
    with pytest.raises(charmodel.BodyNotDrawnError, match=str(bare.body.file_data_id)) as caught:
        charmodel.build(plan, source)
    assert caught.value.file_data_id == bare.body.file_data_id
    assert "not in your client's local files" in str(caught.value)


def test_a_body_skin_that_is_not_local_ends_the_run_naming_it_constructed() -> None:
    bare = renderplan.plan_for_record(cb.tables(), cb.record("Sangraal-Sett"))
    files = cb.files_for(bare)
    skin = bare.body.file_data_id + cb.SKIN_OFFSET
    del files[skin]
    source = cb.Source(files)
    with pytest.raises(charmodel.BodyNotDrawnError, match=str(skin)):
        charmodel.build(_plan("Sangraal-Sett", source), source)


def test_other_files_not_local_are_left_out_and_listed_constructed() -> None:
    bare = renderplan.plan_for_record(cb.tables(), cb.record("Sangraal-Sett"))
    files = cb.files_for(bare)
    weapon = bare.item_models[0].file_data_id
    piece = bare.item_pieces[0].file_data_id
    del files[weapon], files[piece]
    source = cb.Source(files, states={piece: "encrypted"})
    built = charmodel.build(_plan("Sangraal-Sett", source), source)
    gone = {f.file_data_id: f for f in built.left_out}
    assert gone[weapon].state == "not_local" and gone[piece].state == "encrypted"
    model = next(m for m in built.models if m.file_data_id == weapon)
    assert not model.placed and str(weapon) in (model.reason or "")
    assert built.facts.nodes == 2  # the body and the other weapon
    assert all(p.job.file_data_id != piece for p in built.body_texture.painted)
    assert weapon not in source.reads


def test_collection_and_unplaced_models_constructed() -> None:
    """A collection model is drawn at the origin with its sections filtered
    by the plan's geosets; one with no attachment known is listed, not
    placed, and not read."""
    plan, source, _ = _built("Sangraal-Sett")
    first, second = plan.item_models
    collection = first.model_copy(update={"kind": "collection", "attachment_id": None})
    unplaced = second.model_copy(update={"kind": "no attachment known", "attachment_id": None})
    hidden = 1701  # a hidden group: a collection section of it is not drawn
    source.files.update(cb.item_files(collection.file_data_id, geoset=hidden))
    changed = plan.model_copy(update={"item_models": (collection, unplaced)})
    built = charmodel.build(changed, source)
    kinds = {m.kind: m for m in built.models}
    assert not kinds["collection"].placed and kinds["collection"].drawn == 0
    source.files.update(cb.item_files(collection.file_data_id, geoset=0))
    built = charmodel.build(changed, source)
    kinds = {m.kind: m for m in built.models}
    assert kinds["collection"].placed and kinds["collection"].position is None
    assert not kinds["no attachment known"].placed
    assert "attachment point" in (kinds["no attachment known"].reason or "")
    glb = read_glb(built.glb)
    assert len(glb.nodes) == 2 and "translation" not in glb.nodes[1]


def test_a_missing_attachment_point_leaves_the_item_out_constructed() -> None:
    plan, source, _ = _built("Sangraal-Sett")
    odd = plan.item_models[0].model_copy(update={"attachment_id": 99})
    built = charmodel.build(plan.model_copy(update={"item_models": (odd,)}), source)
    assert not built.models[1].placed and "99" in (built.models[1].reason or "")
    assert built.facts.nodes == 1


def test_an_attachment_stored_at_the_origin_is_not_placed_constructed() -> None:
    """As the hand points of both fixture body models at 70959 are: the
    game moves them with the animation, so the rest pose has no place for
    them, and the item is listed, not drawn at the feet."""
    plan, source, _ = _built("Sangraal-Sett")
    body = plan.body.file_data_id
    blob = bytearray(source.files[body])
    origin = cb.mb.md20(
        vertices=cb._triangles(cb.BODY_GEOSETS)[0],
        textures=tuple((kind, 0) for kind, _ in cb.BODY_TEXTURES),
        materials=((0, 0), (0x4, 1)),
        combos=tuple(range(len(cb.BODY_TEXTURES))),
        attachments=((1, 0, (0.0, 0.0, 0.0)), (2, 0, (0.1, -0.6, 1.0))),
        lookup=(),
    )
    skin_id = body + cb.SKIN_OFFSET
    source.files[body] = cb.mb.chunked(
        bytes(origin), sfid=(skin_id,), txid=tuple(t for _, t in cb.BODY_TEXTURES), extra=()
    )
    assert bytes(blob) != source.files[body]
    source.reads.clear()
    built = charmodel.build(plan, source)
    main, off = built.models[1:]
    assert (main.attachment_id, main.placed) == (1, False)
    assert "rest pose" in (main.reason or "") and main.file_data_id not in source.reads
    assert (off.attachment_id, off.placed) == (2, True)
    assert built.facts.nodes == 2


def test_a_separate_texture_with_two_layers_draws_the_first_and_says_so_constructed() -> None:
    plan, source, _ = _built("Sangraal-Sett")
    eyes = next(la for la in plan.layers if la.texture_type == 19)
    second = eyes.model_copy(update={"file_data_id": 4242, "layer": eyes.layer + 1})
    source.files[4242] = cb.texture((1, 2, 3, 255))
    built = charmodel.build(plan.model_copy(update={"layers": (*plan.layers, second)}), source)
    png = next(p for p in built.pngs if p.name == "eyes")
    assert (png.file_data_id, png.not_drawn) == (eyes.file_data_id, (4242,))
    assert any("4242" in n for n in built.notes)


def test_a_body_texture_past_the_budget_is_drawn_grey_constructed() -> None:
    plan, source, _ = _built("Sangraal-Sett")
    built = charmodel.build(plan, source, work_canvases=1)
    assert not built.body_texture.composed and "stops at 1" in (built.body_texture.why or "")
    assert "body" not in [p.name for p in built.pngs]
    glb = read_glb(built.glb)
    assert glb.document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [
        0.5,
        0.5,
        0.5,
        1.0,
    ]


def test_a_texture_that_is_not_a_blp_is_left_out_constructed() -> None:
    plan, source, _ = _built("Sangraal-Sett")
    source.files[cb.TXID_FILE] = b"not a texture"
    built = charmodel.build(plan, source)
    gone = {f.file_data_id: f for f in built.left_out}
    assert "not a texture wowlab reads" in gone[cb.TXID_FILE].reason


def test_the_sentences_say_what_the_model_is_not() -> None:
    assert "not a picture of how the character looks in game" in charmodel.LIKENESS
    assert "rest pose" in charmodel.REST_POSE
    assert "stand-in" in charmodel.STAND_IN


def test_the_derived_fixture_is_consistent() -> None:
    """The recorded facts of the two fixture characters at 70959 (written by
    `scripts/charmodel_facts.py` on the owner's install; asserted against
    the install by `test_charmodel_live.py`): one node per placed model, the
    hand points stored at the origin, no path and no geometry."""
    import json

    path = cb.FIXTURES / "macos-70959" / "derived" / "charmodel.json"
    text = path.read_text(encoding="utf-8")
    facts = json.loads(text)
    assert facts["kind"] == "charmodel-derived" and facts["version"] == "1.60.1.70959"
    assert "/Users/" not in text and "/Volumes/" not in text and "\\\\" not in text
    for char in facts["characters"]:
        placed = [m for m in char["models"] if m["placed"]]
        assert char["glb"]["nodes"] == len(placed)
        assert char["glb"]["vertices"] == sum(m["vertices"] for m in placed)
        assert char["glb"]["triangles"] == sum(m["triangles"] for m in placed)
        for point in char["attachment_points"].values():
            assert not any(point["position"])
        assert all(not m["placed"] for m in char["models"] if m["what"] == "item")
