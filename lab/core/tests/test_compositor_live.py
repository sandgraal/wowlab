"""`compositor` on the owner's install, by hand (M15-10; ADR-0033 layer c).
Never run in CI (ADR-0012):

    WOWLAB_WOW_ROOT=<the install> uv run pytest -m live lab/core/tests/test_compositor_live.py -s

Builds the plan of each character the derived fixture
(`fixtures/macos-70959/derived/compositor.json`) records, from the recorded
tables and the `macos-70450` lab-addon records, composes its body texture
reading every file through `casc.open_source`, and checks the composite's
MD5 and every painted layer's texture facts against the fixture. Skipped
when the install has patched past the fixture's build. A read: nothing in
the install is opened for writing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from wowlab_core import casc, compositor, labaddon, renderplan
from wowlab_core.install import ENV_ROOT, discover

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FACTS: dict[str, Any] = json.loads(
    (FIXTURES / "macos-70959" / "derived" / "compositor.json").read_text(encoding="utf-8")
)
CHARS = FIXTURES / "macos-70450" / "forever" / "WTF" / "Account" / "90000001#6" / "1"
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "renderplan_facts.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("renderplan_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def source() -> casc.CascSource:
    value = os.environ.get(ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {ENV_ROOT} to the install to run the live compositor tests")
    opened = casc.open_source(discover(Path(value)), FACTS["flavor"])
    if opened.report.index.version != FACTS["version"]:
        pytest.skip(
            f"the install is at {opened.report.index.version}; the fixture at {FACTS['version']}"
        )
    return opened


@pytest.mark.live
@pytest.mark.parametrize("char", FACTS["characters"], ids=lambda c: c["label"])
def test_live_the_composite_is_the_recorded_one(
    source: casc.CascSource, char: dict[str, Any]
) -> None:
    tables = _script().read_tables(FIXTURES / "wago", FACTS["tables_build"])
    record = labaddon.read_char(CHARS / char["label"] / "SavedVariables" / "WowLab.lua")
    plan = renderplan.plan_for_record(tables, record)
    result = compositor.compose(plan, source)
    assert result.rgba_md5() == char["rgba_md5"]
    assert [
        (
            p.job.file_data_id,
            [p.texture_width, p.texture_height],
            p.texture_format,
            [p.scale_x, p.scale_y],
        )
        for p in result.painted
    ] == [(p["file_data_id"], p["texture"], p["format"], p["scale"]) for p in char["painted"]]
