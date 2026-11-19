# Probe from review of m19/04-capture-refuses-crew; reproduces a filled crew book gzipped under lab/addon/ (not a source file) passing the repository scan
"""CONSTRUCTED INPUT, in memory; nothing is written to the tree.

`docs/LAB_PLAN.md` §20.5 term 3 allows the crew book's name in the addon's
sources, not in any file under `lab/addon/`. The repository test treats every
`lab/addon/` path as a "checked place" and checks only column-0 assignments in
its raw bytes, never unpacking it, so a compressed filled book there passes.
"""

from __future__ import annotations

import gzip
import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
# The name is assembled at run time so this file does not trip the scan itself.
NAME = b"WowLab" + b"CrewDB"
FILLED = (
    b"\r\n"
    + NAME
    + b' = {\r\n["players"] = {\r\n["Inventeda-Fakerealm"] = {\r\n["note"] = "x",\r\n},\r\n},\r\n}\r\n'
)


def _scan_module() -> ModuleType:
    path = ROOT / "tests" / "repo" / "test_crew_book_out_of_repo.py"
    spec = importlib.util.spec_from_file_location("m19_04_crew_scan_probe_gz", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_positive_control_the_same_gz_in_a_fixture_dir_is_flagged() -> None:
    scan = _scan_module().scan
    assert scan([("lab/core/tests/fixtures/WowLab.lua.gz", gzip.compress(FILLED))], index=b"")


def test_a_filled_book_gzipped_under_lab_addon_is_flagged() -> None:
    scan = _scan_module().scan
    assert scan([("lab/addon/fixtures/WowLab.lua.gz", gzip.compress(FILLED))], index=b"")
