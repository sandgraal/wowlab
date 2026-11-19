"""`charmodel` on the owner's install, by hand (M15-11; ADR-0033 layer c).
Never run in CI (ADR-0012):

    WOWLAB_WOW_ROOT=<the install> uv run pytest -m live lab/core/tests/test_charmodel_live.py -s

Builds the plan of each character the derived fixture
(`fixtures/macos-70959/derived/charmodel.json`) records, from the recorded
tables and the `macos-70450` lab-addon records, with each file's state from
`casc.open_source`, draws it with `charmodel.build`, re-reads the `.glb`
with the tests' stdlib reader, and checks its facts (counts and the MD5s
of its chunks), each model's and each PNG's, and the body model's
attachment points with their bones' pivots, against the fixture. Skipped
when the install has patched past the fixture's build. A read: nothing in
the install is opened for writing, and nothing is written anywhere.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from wowlab_core import casc, charmodel, m2, renderplan
from wowlab_core.install import ENV_ROOT, discover

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _charmodel_build as cb
from _glb_read import read_glb

HERE = Path(__file__).resolve().parent
FACTS: dict[str, Any] = json.loads(
    (HERE / "fixtures" / "macos-70959" / "derived" / "charmodel.json").read_text(encoding="utf-8")
)


def _script() -> ModuleType:
    path = HERE.parents[2] / "scripts" / "charmodel_facts.py"
    spec = importlib.util.spec_from_file_location("charmodel_facts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def source() -> casc.CascSource:
    value = os.environ.get(ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {ENV_ROOT} to the install to run the live char model tests")
    opened = casc.open_source(discover(Path(value)), FACTS["flavor"])
    if opened.report.index.version != FACTS["version"]:
        pytest.skip(
            f"the install is at {opened.report.index.version}; the fixture at {FACTS['version']}"
        )
    return opened


@pytest.mark.live
@pytest.mark.parametrize("char", FACTS["characters"], ids=lambda c: c["label"])
def test_live_the_glb_is_the_recorded_one(source: casc.CascSource, char: dict[str, Any]) -> None:
    plan = renderplan.plan_for_record(cb.tables(), cb.record(char["label"]), statter=source)
    built = charmodel.build(plan, source)
    glb = read_glb(built.glb)
    assert built.facts.model_dump() == char["glb"]
    assert hashlib.md5(built.glb).hexdigest() == char["glb_md5"]
    assert len(glb.nodes) == char["glb"]["nodes"]
    assert hashlib.md5(glb.binary).hexdigest() == char["glb"]["bin_md5"]
    assert [
        (m.file_data_id, m.kind, m.placed, m.drawn, m.vertices, m.triangles) for m in built.models
    ] == [
        (m["file_data_id"], m["kind"], m["placed"], m["drawn"], m["vertices"], m["triangles"])
        for m in char["models"]
    ]
    assert [(p.name, p.file_data_id, hashlib.md5(p.png).hexdigest()) for p in built.pngs] == [
        (p["name"], p["file_data_id"], p["md5"]) for p in char["pngs"]
    ]
    assert [f.file_data_id for f in built.left_out] == [f["file_data_id"] for f in char["left_out"]]
    data = source.read(plan.body.file_data_id)
    body = m2.parse_m2(data)
    assert _script().attachment_points(body, data) == char["attachment_points"]
