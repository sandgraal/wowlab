"""`scripts/renderplan_facts.py` on a constructed install (M15-09; labelled
`constructed`): it writes the derived record of a plan's file states, holds
no file bytes and no install path, refuses an output inside an install, one
already there and a tables folder missing a table, and changes nothing in
the install. The tables are the 70959 recordings copied under the
constructed install's version name; the plan's files are in none of its
blocks, so each is `not_in_build`."""

from __future__ import annotations

import importlib.util
import json
import random
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest
from _casc_build import FLAVOR, VERSION, make_source_install, tree_state

from wowlab_core import renderplan

pytestmark = pytest.mark.parser

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "renderplan_facts.py"
FIXTURES = REPO / "lab" / "core" / "tests" / "fixtures"
RECORDED = "1.60.1.70959"
SANGRAAL = (
    FIXTURES
    / "macos-70450/forever/WTF/Account/90000001#6/1/Sangraal-Sett/SavedVariables/WowLab.lua"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("renderplan_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


facts_script = _load()


def _tables(tmp: Path, *, skip: str | None = None) -> Path:
    folder = tmp / "tables"
    folder.mkdir()
    for name in renderplan.REQUIRED_TABLES:
        if name == skip:
            continue
        for suffix in ("csv", "subset.csv"):
            src = FIXTURES / "wago" / f"{name}.{RECORDED}.{suffix}"
            if src.is_file():
                shutil.copyfile(src, folder / f"{name}.{VERSION}.{suffix}")
    return folder


def _install(tmp: Path) -> Path:
    return make_source_install(
        tmp / "install", stored=[], encoding_rows=[], root_blocks=[], rng=random.Random(9)
    )


def _run(root: Path, tables: Path, out: Path) -> int:
    return int(
        facts_script.main(
            [
                "--root",
                str(root),
                "--flavor",
                FLAVOR,
                "--tables",
                str(tables),
                "--char",
                f"Sangraal-Sett={SANGRAAL}",
                "--captured",
                "2026-11-17",
                "--out",
                str(out),
            ]
        )
    )


def test_writes_the_plan_files_record_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    tables = _tables(tmp_path)
    before = tree_state(root)
    out = tmp_path / "out.json"
    assert _run(root, tables, out) == 0
    assert tree_state(root) == before
    facts = json.loads(out.read_text(encoding="utf-8"))
    assert facts["kind"] == "renderplan-files-derived"
    assert facts["version"] == facts["tables_build"] == VERSION
    (char,) = facts["characters"]
    assert (char["label"], char["chr_model_id"], char["look"]) == ("Sangraal-Sett", 9, "record")
    assert [f["file_data_id"] for f in char["files"]][:2] == [959310, 959287]
    assert {f["state"] for f in char["files"]} == {"not_in_build"}
    assert str(root) not in out.read_text(encoding="utf-8")


def test_refuses_an_output_already_there_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.json"
    out.write_text("{}", encoding="utf-8")
    assert _run(_install(tmp_path), _tables(tmp_path), out) == 1
    assert "never replaced" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "{}"


def test_refuses_an_output_inside_an_install_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install(tmp_path)
    before = tree_state(root)
    assert _run(root, _tables(tmp_path), root / "out.json") == 1
    assert "inside a game install" in capsys.readouterr().err
    assert tree_state(root) == before


def test_refuses_a_missing_table_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.json"
    assert _run(_install(tmp_path), _tables(tmp_path, skip="TextureFileData"), out) == 1
    assert "no TextureFileData for" in capsys.readouterr().err
    assert not out.exists()
