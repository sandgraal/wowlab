"""`m2` against the derived fixture of the owner's install (M15-08; ADR-0033
layer b): what `scripts/casc_facts.py --m2` read through `wowlab_core.m2`
from `humanmale_hd` and `humanfemale_hd` and their skins on the launch
client (`_forever_`, product `wowf`, 1.60.1.70959), never a byte of a file. Provenance: `fixtures/README.md`, row
`macos-70959/derived/m2-models.json`.

The checks tie the record to `docs/LAB_PLAN.md` §17.3 (measured on the beta,
`_classic_beta_`, `wow_classic_beta`, at 70450:
version 272, 215 bones, 45 attachments, 113 sections, eleven textures of
types 1, 6, 2, 15, 16, 18, 17, 19 and three of type 0, the 37,813-entry
lookup and 147,966 indices, levels 0 to 2, no `SKID`) and to the reader's
own rules: every section inside the lookup and the indices with
`level << 16` added to the index start and not to the vertex start; the
type-0 textures, and only they, carrying a `TXID` id; Z-up; the view
skin's lookup the identity; each skin's `vertex_base` the sum of the
earlier skins' lookups, ending at the model's vertex count.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.parser

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos-70959" / "derived"
TEXT = (FIXTURE / "m2-models.json").read_text(encoding="utf-8")
FACTS: dict[str, Any] = json.loads(TEXT)
MODELS = {m["name"]: m for m in FACTS["models"]}


def test_the_record_is_the_build_and_kind_it_says() -> None:
    assert FACTS["kind"] == "m2-models-derived" and FACTS["schema"] == 1
    assert FACTS["made_by"] == "scripts/casc_facts.py --m2"
    assert (FACTS["flavor"], FACTS["product"], FACTS["version"]) == (
        "_forever_",
        "wowf",
        "1.60.1.70959",
    )
    assert FACTS["build"] == 70959
    assert sorted(MODELS) == ["humanfemale_hd", "humanmale_hd"]
    assert MODELS["humanmale_hd"]["file_data_id"] == 1011653
    assert MODELS["humanfemale_hd"]["file_data_id"] == 1000764


def test_no_bytes_hash_path_or_tags_are_recorded() -> None:
    assert not re.search(r"[0-9a-f]{32}", TEXT), "a hash-like run of hex"
    for token in ("geoip", "acct-", "speech?", "/Users/", "\\\\", "_key"):
        assert token not in TEXT
    assert len(TEXT) < 64_000


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_version_272_with_bones_and_attachments_inside_and_no_skid(name: str) -> None:
    model = MODELS[name]
    assert model["state"] == "local" and model["version"] == 272 and model["notes"] == []
    tags = [c["tag"] for c in model["chunks"]]
    assert tags == ["MD21", "TXAC", "AFID", "LDV1", "SFID", "TXID", "BFID"]
    assert "SKID" not in tags
    assert [c["tag"] for c in model["chunks"] if c["read"]] == ["MD21", "SFID", "TXID"]
    # The chunks fill the file: eight bytes of tag and size before each.
    assert sum(8 + c["size"] for c in model["chunks"]) == model["decoded_size"]
    assert model["bone_count"] > 0 and model["attachment_count"] > 0
    assert model["md20_name"] == ""


def test_humanmale_hd_matches_the_plan_s_measurements() -> None:
    model = MODELS["humanmale_hd"]
    assert (model["bone_count"], model["attachment_count"], model["vertex_count"]) == (
        215,
        45,
        226_301,
    )
    assert [t["type"] for t in model["textures"]] == [1, 6, 2, 15, 16, 18, 17, 19, 0, 0, 0]
    view = model["skins"][0]
    assert (view["section_count"], view["lookup_count"], view["index_count"]) == (
        113,
        37_813,
        147_966,
    )
    assert view["levels"] == {"0": 47, "1": 57, "2": 9}


def test_humanfemale_hd_has_its_own_counts() -> None:
    model = MODELS["humanfemale_hd"]
    assert (model["bone_count"], model["attachment_count"], model["vertex_count"]) == (
        201,
        43,
        248_970,
    )
    assert [t["type"] for t in model["textures"]] == [1, 6, 0, 2, 15, 16, 18, 17, 19, 0, 0, 0]
    assert model["skins"][0]["section_count"] == 126


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_txid_ids_belong_to_the_type_zero_textures_only(name: str) -> None:
    model = MODELS[name]
    textures = model["textures"]
    assert len(model["texture_file_data_ids"]) == len(textures)
    for texture, fdid in zip(textures, model["texture_file_data_ids"], strict=True):
        assert (texture["type"] == 0) == (fdid != 0)
        assert texture["file_data_id"] == (fdid or None)
    assert sum(t["type"] == 0 for t in textures) >= 3


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_sfid_is_one_view_then_six_lod_skins(name: str) -> None:
    model = MODELS[name]
    assert model["view_count"] == 1 and len(model["skin_file_data_ids"]) == 1
    assert len(model["lod_skin_file_data_ids"]) == 6
    skins = [s["file_data_id"] for s in model["skins"]]
    assert skins == model["skin_file_data_ids"] + model["lod_skin_file_data_ids"]


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_sections_stay_inside_the_lookup_and_indices_with_the_level_shift(name: str) -> None:
    for skin in MODELS[name]["skins"]:
        assert skin["index_count"] % 3 == 0
        # Every section was checked by the reader with `level << 16` added to
        # the index start, and the furthest ends at the last index. For the
        # skins with more than 131,070 indices that end is unreachable
        # without the shift (a u16 start plus a u16 count); for the others
        # it shows the shift keeps every section in bounds.
        assert skin["index_end"] == skin["index_count"] > 0xFFFF
        assert any(int(level) > 0 for level in skin["levels"])
        # The vertex start is not shifted: the furthest section ends at the
        # last lookup entry, below 65,536.
        assert skin["vertex_end"] == skin["lookup_count"] <= 0xFFFF
        assert skin["lookup_max"] == skin["lookup_count"] - 1
        assert skin["bone_index_count"] == skin["lookup_count"]
        assert skin["batch_count"] == skin["section_count"]


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_the_skins_share_one_vertex_array_through_vertex_base(name: str) -> None:
    model = MODELS[name]
    base = 0
    for skin in model["skins"]:
        assert skin["vertex_base"] == base
        base += skin["lookup_count"]
    assert base == model["vertex_count"]
    assert model["skins"][0]["lookup_is_identity"] is True


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_geoset_ids_are_group_times_100_plus_value(name: str) -> None:
    for skin in MODELS[name]["skins"]:
        ids = skin["geoset_ids"]
        assert len(ids) == skin["section_count"]
        assert ids[0] == 0 and ids == sorted(ids)
        assert all(0 <= g < 10_000 for g in ids)
        assert all(g % 100 != 0 for g in ids if g >= 100), "value 0 above group 0"
        assert ids == MODELS[name]["skins"][0]["geoset_ids"]
    groups = {g // 100 for g in MODELS[name]["skins"][0]["geoset_ids"]}
    assert {0, 4, 5, 7, 8, 9, 10, 11, 12, 13, 15, 17, 18, 20, 22, 32, 51} <= groups


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_positions_are_z_up(name: str) -> None:
    lo, hi = MODELS[name]["position_min"], MODELS[name]["position_max"]
    extent = [h - low for low, h in zip(lo, hi, strict=True)]
    assert extent[2] == max(extent) and 1.5 < hi[2] < 2.5 and abs(lo[2]) < 0.1


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_skin_headers_are_64_bytes_with_shadow_batches_then_zeros(name: str) -> None:
    counts = {s["shadow_batch_count"] for s in MODELS[name]["skins"]}
    assert counts == {104 if name == "humanmale_hd" else 117}
    for skin in MODELS[name]["skins"]:
        assert skin["skin_header_size"] == 64
        assert skin["header_bytes_0x38_to_0x40_zero"] is True


def test_head_attachment_sits_at_head_height() -> None:
    assert MODELS["humanmale_hd"]["head_attachment_z"] == 1.8476
    assert MODELS["humanfemale_hd"]["head_attachment_z"] == 1.7119
    for model in MODELS.values():
        assert 11 in model["attachment_ids"]
        assert model["head_attachment_z"] <= model["position_max"][2]


@pytest.mark.parametrize("name", ["humanmale_hd", "humanfemale_hd"])
def test_lod_lookups_read_relative_to_vertex_base_make_short_edges(name: str) -> None:
    view, *lods = MODELS[name]["skins"]
    assert view["mean_edge_with_vertex_base"] == view["mean_edge_from_vertex_0"]
    for skin in lods:
        with_base, from_zero = skin["mean_edge_with_vertex_base"], skin["mean_edge_from_vertex_0"]
        # With the base the edges stay near the view skin's (coarser LODs
        # are a little longer); without it they are 2.7 to 5.2 times longer.
        assert (
            view["mean_edge_with_vertex_base"]
            <= with_base
            < 1.5 * view["mean_edge_with_vertex_base"]
        )
        assert from_zero > 2.5 * with_base
