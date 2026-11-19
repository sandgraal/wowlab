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
from typing import Any

import pytest

from wowlab_core import charmodel, labaddon, renderplan

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
    assert "where on the body" in (kinds["no attachment known"].reason or "")
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
    built = charmodel.build(plan, source, held=False)  # as before M15-27
    main, off = built.models[1:]
    assert (main.attachment_id, main.placed) == (1, False)
    assert "rest pose" in (main.reason or "") and main.file_data_id not in source.reads
    assert (off.attachment_id, off.placed) == (2, True)
    assert built.facts.nodes == 2
    # Held items on (M15-27): this body has no sheath point, so the main
    # hand stays not drawn with that reason, and its model is still not read.
    source.reads.clear()
    held = charmodel.build(plan, source)
    main = held.models[1]
    assert not main.placed and main.at_origin and main.file_data_id not in source.reads
    assert main.sheath_reason == "the body model has no sheath point 32"
    assert held.facts.nodes == 2


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
    assert (gone[cb.TXID_FILE].state, gone[cb.TXID_FILE].words) == (
        "unreadable",
        "could not be read",
    )


def test_the_sentences_say_what_the_model_is_not() -> None:
    assert "not a picture of how the character looks in game" in charmodel.LIKENESS
    assert "rest pose" in charmodel.REST_POSE
    assert all("stand-in" in s for s in charmodel.STAND_IN.values())


def test_a_record_for_another_body_says_so_not_no_record() -> None:
    """W1: the stand-in's reason, from the plan's `stand_in`."""
    record = cb.record("Sangraal-Sett")
    tables = cb.tables()
    look = labaddon.customization_import(record).model_copy(update={"body_type": 1})
    plan = renderplan.build_plan(tables, labaddon.identity_of(record), look)
    assert (plan.look.source, plan.look.stand_in) == ("defaults", "other body")
    assert charmodel.stand_in_words(plan.look) == charmodel.STAND_IN["other body"]
    assert charmodel.stand_in_words(plan.look).startswith("The barber-shop record is for another")
    none = renderplan.plan_for_record(tables, cb.record("Dangling-Participle"))
    assert none.look.stand_in == "no record"
    assert charmodel.stand_in_words(none.look).startswith("No barber-shop record")
    assert charmodel.stand_in_words(_plan("Sangraal-Sett").look) is None


def test_extra_batches_of_a_drawn_section_are_counted_not_drawn_constructed() -> None:
    plan, source, _ = _built("Sangraal-Sett")
    _, sections = cb._triangles(cb.BODY_GEOSETS)
    n = len(sections)
    batches = [
        (i, 1 if cb.BODY_TEXTURE_OF.get(g) == 1 else 0, cb.BODY_TEXTURE_OF.get(g, 2))
        for i, g in enumerate(cb.BODY_GEOSETS)
    ]
    shown = cb.BODY_GEOSETS.index(0)
    hidden = cb.BODY_GEOSETS.index(1701)
    source.files[plan.body.file_data_id + cb.SKIN_OFFSET] = cb.mb.skin(
        lookup=tuple(range(3 * n)),
        indices=tuple(range(3 * n)),
        sections=sections,
        batches=(*batches, (shown, 0, 4), (shown, 0, 1), (hidden, 0, 4)),
    )
    built = charmodel.build(plan, source)
    body = built.models[0]
    assert body.extra_batches == 2  # the two of a drawn section; a hidden one is not counted
    assert body.triangles == body.drawn


def test_a_file_not_on_this_computer_says_so_as_casc_stat_does() -> None:
    bare = renderplan.plan_for_record(cb.tables(), cb.record("Sangraal-Sett"))
    files = cb.files_for(bare)
    gone = bare.item_models[0].file_data_id
    del files[gone]
    source = cb.Source(files)
    built = charmodel.build(_plan("Sangraal-Sett", source), source)
    left = {f.file_data_id: f for f in built.left_out}[gone]
    assert left.words == (
        "not in your client's local files (part of this game version, but not on this computer)"
    )


def test_the_derived_fixture_is_consistent() -> None:
    """The recorded facts of the two fixture characters at 70959 (written by
    `scripts/charmodel_facts.py` on the owner's install; asserted against
    the install by `test_charmodel_live.py`): one node per placed model, the
    hand points stored at the origin, no path and no geometry; and the
    held-items probe's rows (M15-24): points 26 to 33 away from the origin,
    the hand bones' parents at the origin and the next bone up not, and
    each named bone's key id, flags and track counts; and the rotation
    probe's rows (M15-26), which agree with those counts."""
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
        points = char["attachment_points"]
        assert sorted(points, key=int) == ["0", "1", "2", "5", "6", "11", "12"]
        for held in ("0", "1", "2"):  # held items: at the origin, on bones pivoted there
            assert not any(points[held]["position"]) and not any(points[held]["bone_pivot"])
        for worn in ("5", "6", "11", "12"):  # shoulders, head, back: away from the origin
            assert any(points[worn]["position"]) and points[worn]["position"][2] > 1.0
        assert all(not m["placed"] for m in char["models"] if m["what"] == "item")
        # M15-24, the held-items probe. Every other point the file holds, in
        # file order, none of the seven above, each at its bone's pivot.
        others = char["other_attachment_points"]
        ids = [o["id"] for o in others]
        assert ids == sorted(set(ids)) and not set(ids) & {int(p) for p in points}
        assert all(o["position"] == o["bone_pivot"] for o in others)
        # Points 26 to 33 (the community list's sheath points, 29 excepted):
        # all eight held, each away from the origin.
        sheath = {o["id"]: o for o in others if 26 <= o["id"] <= 33}
        assert sorted(sheath) == list(range(26, 34))
        assert all(o["position"][2] > 1.0 for o in sheath.values())
        # The hand bones' parents rest at the origin; the next bone up does not.
        parents = char["held_point_parents"]
        assert sorted(parents, key=int) == ["0", "1", "2"]
        for chain in parents.values():
            assert len(chain) == 2
            assert not any(chain[0]["bone_pivot"]) and any(chain[1]["bone_pivot"])
        # Points 26 to 33's bones' parents, by the same walk.
        sheath_parents = char["sheath_point_parents"]
        assert sorted(sheath_parents, key=int) == [str(i) for i in range(26, 34)]
        assert all(len(chain) <= 2 for chain in sheath_parents.values())
        # Each bone those rows name, and no other, has its key id, flags,
        # parent and two counted tracks.
        bones = char["bones"]
        by_id = {o["id"]: o for o in others} | {int(k): v for k, v in points.items()}
        named = {by_id[i]["bone"] for i in (0, 1, 2, 5, 6, 11, 12, *range(26, 34))}
        for chains in (parents, sheath_parents):
            named |= {link["bone"] for chain in chains.values() for link in chain}
        assert sorted(bones, key=int) == [str(b) for b in sorted(named)]
        for bone in bones.values():
            for track in (bone["translation"], bone["rotation"]):
                assert (track["sequences"] == 0) == (track["first_keys"] is None)
        for point, chain in parents.items():
            # The hand point's own bone has no key id and no translation track;
            # the two bones above it are key bones with one per sequence.
            own = bones[str(points[point]["bone"])]
            assert own["key_bone_id"] == -1 and own["translation"]["sequences"] == 0
            for link in chain:
                above = bones[str(link["bone"])]
                assert above["key_bone_id"] >= 0 and above["translation"]["global_sequence"] == -1
                assert above["translation"]["sequences"] > 1

        # M15-26, the rotation probe: points 26 to 33's bones' rotation keys
        # and the hand points' bones' keys agree with the counts above, and
        # every decoded rotation read is a unit quaternion.
        def agrees(track: dict[str, Any], counted: dict[str, Any]) -> None:
            assert track["global_sequence"] == counted["global_sequence"]
            assert track["arrays"] == counted["sequences"]
            first = track["first_array"]
            assert (first is None) == (track["arrays_with_keys"] == 0)
            for read in (first, track["animation_0_array"]):
                if read is None or read["first_value"] is None:
                    continue
                assert read["in_m2"] and read["first_time"] is not None
                if read["first_norm"] is not None:
                    assert abs(read["first_norm"] - 1) < 1e-3
                    assert 0 <= read["max_degrees_from_first"] <= 180
            if first is not None and first["index"] == 0:
                assert first["keys"] == counted["first_keys"]

        sheath_keys = char["sheath_rotation_keys"]
        assert sorted(sheath_keys, key=int) == [str(i) for i in range(26, 34)]
        for point, row in sheath_keys.items():
            assert row["bone"] == by_id[int(point)]["bone"]
            agrees(row["rotation"], bones[str(row["bone"])]["rotation"])
        hand_keys = char["hand_bone_keys"]
        assert sorted(hand_keys, key=int) == ["1", "2"]
        for point, rows in hand_keys.items():
            chain = [points[point]["bone"], *(link["bone"] for link in parents[point])]
            assert [r["bone"] for r in rows] == chain
            for r in rows:
                for kind in ("translation", "rotation"):
                    agrees(r[kind], bones[str(r["bone"])][kind])

    # The hand bones' two ancestors carry the same key ids on both body models.
    def ancestor_keys(char: dict[str, Any]) -> dict[str, list[int]]:
        bones = char["bones"]
        return {
            point: [bones[str(link["bone"])]["key_bone_id"] for link in chain]
            for point, chain in char["held_point_parents"].items()
        }

    first, second = facts["characters"]
    assert ancestor_keys(first) == ancestor_keys(second)


def _counting_decode(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count `blp.decode` calls. The tests below build with `work_canvases=0`,
    so the compositor decodes nothing and every call is `charmodel`'s."""
    calls: list[int] = []
    real = charmodel.blp.decode

    def counted(data: object) -> object:
        calls.append(1)
        return real(data)  # type: ignore[arg-type]

    monkeypatch.setattr(charmodel.blp, "decode", counted)
    return calls


def _one_item(count: int, first: int) -> tuple[renderplan.RenderPlan, cb.Source]:
    plan, source, _ = _built("Sangraal-Sett")
    item = plan.item_models[0]
    source.files.update(cb.many_textures_item(item.file_data_id, count, first))
    source.reads.clear()
    return plan.model_copy(update={"item_models": (item,)}), source


def test_a_model_naming_65_textures_holds_64_and_decodes_no_more_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2: an item model whose 65 sections each name a texture of their own."""
    plan, source = _one_item(65, 9_000_000)
    calls = _counting_decode(monkeypatch)
    built = charmodel.build(plan, source, work_canvases=0)
    assert built.facts.images == charmodel.MAX_IMAGES == len(calls)
    capped = [f for f in built.left_out if "more pictures than wowlab draws" in f.reason]
    assert capped and all(f.file_data_id >= 9_000_000 for f in capped)
    assert all(f.file_data_id not in source.reads for f in capped)
    assert built.models[1].placed  # the model is drawn, its extra parts grey


def test_a_byte_total_at_the_cap_decodes_nothing_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, source = _one_item(10, 9_100_000)
    monkeypatch.setattr(charmodel, "MAX_SCENE_BYTES", 0)
    calls = _counting_decode(monkeypatch)
    with pytest.raises(charmodel.BodyNotDrawnError, match="larger than wowlab writes"):
        charmodel.build(plan, source, work_canvases=0)
    assert calls == []


def test_an_item_past_the_byte_cap_is_not_placed_and_not_decoded_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, source = _one_item(10, 9_300_000)
    bare = charmodel.build(plan.model_copy(update={"item_models": ()}), source, work_canvases=0)
    # Room for the body and some of the item's pictures, not for its shape.
    monkeypatch.setattr(charmodel, "MAX_SCENE_BYTES", bare.held_bytes + 300)
    calls = _counting_decode(monkeypatch)
    source.reads.clear()
    built = charmodel.build(plan, source, work_canvases=0)
    assert built.facts.nodes == 1 and not built.models[1].placed
    assert "already fill" in (built.models[1].reason or "")
    assert built.held_bytes <= charmodel.MAX_SCENE_BYTES
    # T2: the shape is measured first, so none of the item's ten textures is
    # read or decoded, and the file holds only the body's pictures.
    assert len(calls) == bare.facts.images
    assert not [r for r in source.reads if 9_300_000 <= r < 9_300_010]
    assert built.facts.images == bare.facts.images


def test_decoded_pixels_past_the_cap_are_not_decoded_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, source = _one_item(10, 9_200_000)
    monkeypatch.setattr(charmodel, "MAX_DECODED_PIXELS", 16 * 5)  # five 4 x 4 textures
    calls = _counting_decode(monkeypatch)
    built = charmodel.build(plan, source, work_canvases=0)
    assert len(calls) == 5
    assert any("larger in total than wowlab decodes" in f.reason for f in built.left_out)


def test_once_the_pixel_cap_is_reached_no_texture_is_read_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5: past the pixel cap the model stops reading, not only decoding."""
    plan, source = _one_item(300, 9_400_000)
    monkeypatch.setattr(charmodel, "MAX_DECODED_PIXELS", 16 * 5)  # five 4 x 4 textures
    built = charmodel.build(plan, source, work_canvases=0)
    texture_reads = [r for r in source.reads if r >= 9_400_000]
    # The five that fit, then the one whose header passes the cap; no more.
    assert len(texture_reads) <= 6
    assert any("larger in total than wowlab decodes" in f.reason for f in built.left_out)


def test_bad_textures_count_against_the_read_cap_constructed() -> None:
    """S5: 300 files that are not textures wowlab reads: each read attempt
    counts, and reading stops at `MAX_TEXTURE_READS`."""
    plan, source = _one_item(300, 9_500_000)
    for fdid in range(9_500_000, 9_500_300):
        source.files[fdid] = b"not a texture"
    built = charmodel.build(plan, source, work_canvases=0)
    texture_reads = [r for r in source.reads if r >= 9_500_000]
    other_reads = [r for r in source.reads if r < 9_500_000]
    assert len(texture_reads) + len(other_reads) >= charmodel.MAX_TEXTURE_READS
    assert len(texture_reads) <= charmodel.MAX_TEXTURE_READS
    assert any("names more pictures than wowlab reads" in f.reason for f in built.left_out)
    assert built.models[1].placed  # drawn grey
