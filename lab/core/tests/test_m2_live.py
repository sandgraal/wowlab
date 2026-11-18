"""`m2` on the owner's install, by hand (M15-08; ADR-0033 layer c). Never
run in CI (ADR-0012):

    WOWLAB_WOW_ROOT=<the install> uv run pytest -m live lab/core/tests/test_m2_live.py -s

The tests read `humanmale_hd`, `humanfemale_hd` and every skin their
`SFID` names through `casc.open_source` and `m2`, and compare what was
read with the derived fixture (`fixtures/macos-70959/derived/m2-models.json`),
skipped when the install has patched past that build. They also hold L1:
every open read-only, nothing under `Data/data` changed, and they print
the read and parse times.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from wowlab_core import casc, m2
from wowlab_core.install import ENV_ROOT, discover

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "macos-70959" / "derived"
FACTS: dict[str, Any] = json.loads((FIXTURE / "m2-models.json").read_text(encoding="utf-8"))
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "casc_facts.py"
_WRITING = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_TIMES = "read_and_parse_seconds"


def _root() -> Path:
    value = os.environ.get(ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {ENV_ROOT} to the install to run the live m2 tests")
    return Path(value)


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("casc_facts_m2_live", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


def _untimed(record: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in record.items() if k != _TIMES}
    if "skins" in out:
        out["skins"] = [{k: v for k, v in s.items() if k != _TIMES} for s in out["skins"]]
    return out


@pytest.fixture(scope="module")
def source() -> casc.CascSource:
    opened = casc.open_source(discover(_root()), FACTS["flavor"])
    if opened.report.index.version != FACTS["version"]:
        pytest.skip(
            f"the install is at {opened.report.index.version}; the fixture at {FACTS['version']}"
        )
    return opened


@pytest.mark.live
@pytest.mark.parametrize("index", [0, 1], ids=["humanmale_hd", "humanfemale_hd"])
def test_live_models_and_skins_read_as_the_fixture_records(
    source: casc.CascSource, index: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    want = FACTS["models"][index]
    data_dir = source.storage.root / "Data" / "data"
    before = _state(data_dir)
    opened: list[int] = []
    real_open = os.open

    def spy_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        opened.append(flags)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)
    started = time.perf_counter()
    got = _script()._m2_model_facts(source, want["name"], want["file_data_id"])
    seconds = time.perf_counter() - started
    monkeypatch.undo()
    assert _untimed(got) == _untimed(want)
    assert all(flags & _WRITING == 0 for flags in opened)
    assert _state(data_dir) == before, "Data/data changed during the read"
    print(f"\n{want['name']}: the model and {len(got['skins'])} skins in {seconds:.3f} s")


@pytest.mark.live
def test_live_view_skin_triangles_name_real_vertices(source: casc.CascSource) -> None:
    want = FACTS["models"][0]
    model = m2.parse_m2(source.read(want["file_data_id"]))
    skin = m2.parse_skin(
        source.read(model.skin_file_data_ids[0]), vertex_count=model.vertices.count
    )
    started = time.perf_counter()
    total = 0
    for section in skin.sections:
        tri = skin.triangles(section)
        assert len(tri) == section.index_count
        if tri:
            assert max(tri) < model.vertices.count
        total += len(tri)
    assert total == len(skin.indices)
    head = model.attachment(11)  # the head attachment (§17.4)
    assert head is not None and head.position[2] > 1.0
    print(f"\n{total} indices through the lookup in {time.perf_counter() - started:.3f} s")
