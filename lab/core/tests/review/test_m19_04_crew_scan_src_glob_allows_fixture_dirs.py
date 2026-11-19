# Probe from review of m19/04-capture-refuses-crew; reproduces SOURCE_GLOB allowing a crew book in any path with a /src/ part under lab/
"""CONSTRUCTED INPUT, in memory; nothing is written to the tree.

`docs/LAB_PLAN.md` §20.5 term 3 allows the crew book's name only in the addon and
Lab sources, `docs/` and a fixed list of constructed test files. The
repository test's `SOURCE_GLOB = "lab/*/src/*"` is matched with `fnmatch`,
whose `*` crosses `/`, so a fixture directory that merely contains a `src`
part counts as a Lab source.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
# The name is assembled at run time so this file does not trip the scan itself.
BOOK = b"\r\n" + b"WowLab" + b"CrewDB" + b' = {\r\n["schema"] = 1,\r\n}\r\n'


def _scan_module() -> ModuleType:
    path = ROOT / "tests" / "repo" / "test_crew_book_out_of_repo.py"
    spec = importlib.util.spec_from_file_location("m19_04_crew_scan_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_positive_control_a_fixture_path_is_flagged() -> None:
    scan = _scan_module().scan
    assert scan([("lab/core/tests/fixtures/macos/WowLab.lua", BOOK)])


def test_a_fixture_path_with_a_src_part_is_flagged() -> None:
    scan = _scan_module().scan
    assert scan([("lab/core/tests/fixtures/macos/src/WowLab.lua", BOOK)])
