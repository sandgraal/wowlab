"""`compositor` against the derived fixture (M15-10; ADR-0033 layer b):
`fixtures/macos-70959/derived/compositor.json`, written on the owner's
install by `scripts/compositor_facts.py`, holds per fixture character the
composite's MD5 and every painted layer's ids, blend mode, rectangle and
texture facts; no pixel and no file byte.

Offline, the plan built from the recorded tables (`fixtures/wago/`, the
`macos-70450` lab-addon records) must give the paint jobs the fixture
records, in its order: the ids, the blend modes, the rectangles. The
pixels themselves are graded by `test_compositor_live.py` (`@live`), which
composes on the install and checks the MD5.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from wowlab_core import compositor, labaddon, renderplan

pytestmark = pytest.mark.parser

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "lab" / "core" / "tests" / "fixtures"
FACTS: dict[str, Any] = json.loads(
    (FIXTURES / "macos-70959" / "derived" / "compositor.json").read_text(encoding="utf-8")
)
CHARS = FIXTURES / "macos-70450" / "forever" / "WTF" / "Account" / "90000001#6" / "1"


def _script() -> ModuleType:
    path = REPO / "scripts" / "renderplan_facts.py"
    spec = importlib.util.spec_from_file_location("renderplan_facts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tables() -> renderplan.RenderTables:
    return _script().read_tables(FIXTURES / "wago", FACTS["tables_build"])


def test_the_record_names_its_build_and_holds_no_pixels() -> None:
    assert (FACTS["kind"], FACTS["product"], FACTS["version"]) == (
        "compositor-derived",
        "wowf",
        "1.60.1.70959",
    )
    text = json.dumps(FACTS)
    assert "Users" not in text and "World of Warcraft" not in text
    for char in FACTS["characters"]:
        assert re.fullmatch(r"[0-9a-f]{32}", char["rgba_md5"])
        assert set(char) == {
            "label",
            "chr_model_id",
            "layout_id",
            "size",
            "rgba_md5",
            "target_order_rgba_md5",
            "painted",
            "not_painted",
            "dxt1_depth0_alpha0_pixels",
            "seconds_best_of_3",
        }


@pytest.mark.parametrize("char", FACTS["characters"], ids=lambda c: c["label"])
def test_the_plan_paints_what_the_fixture_records(
    tables: renderplan.RenderTables, char: dict[str, Any]
) -> None:
    record = labaddon.read_char(CHARS / char["label"] / "SavedVariables" / "WowLab.lua")
    plan = renderplan.plan_for_record(tables, record)
    jobs, skipped = compositor.paint_jobs(plan)
    assert [plan.body.layout_width, plan.body.layout_height] == char["size"] == [2048, 1024]
    assert [
        (j.kind, j.file_data_id, j.blend_mode, j.section_type, [j.x, j.y, j.width, j.height])
        for j in jobs
    ] == [
        (p["kind"], p["file_data_id"], p["blend_mode"], p["section_type"], p["rect"])
        for p in char["painted"]
    ]
    assert [n.model_dump() for n in skipped] == char["not_painted"]


@pytest.mark.parametrize("char", FACTS["characters"], ids=lambda c: c["label"])
def test_what_the_fixture_characters_show(char: dict[str, Any]) -> None:
    """The facts the docstrings rest on: body layers of modes 1 and 15 only,
    every texture a half or a quarter of its section, hair (6) and eyes
    (19) left as their own textures, the two layer orders giving one
    composite, no transparent pixel from a DXT1 of alpha depth 0, and the
    composite well inside §17.5's 3 s."""
    painted = char["painted"]
    assert {p["blend_mode"] for p in painted} == {1, 15}
    assert {tuple(p["scale"]) for p in painted} <= {(2, 2), (4, 4)}
    assert {n["texture_type"] for n in char["not_painted"]} == {6, 19}
    assert char["target_order_rgba_md5"] == char["rgba_md5"]
    assert set(char["dxt1_depth0_alpha0_pixels"].values()) == {0}
    assert char["seconds_best_of_3"] < 1.0
