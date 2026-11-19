"""`scripts/charmodel_facts.py` on a constructed install (M15-11; labelled
`constructed`): its refusals, which are `renderplan_facts.py`'s (an output
already there, one inside an install, a missing table), and a plan whose
files the install does not hold ends with exit 1, nothing written and
nothing in the install changed. The real record is written on the owner's
install (`fixtures/macos-70959/derived/charmodel.json`)."""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest
from _casc_build import FLAVOR, make_source_install, tree_state
from test_renderplan_facts_script import SANGRAAL, _tables

pytestmark = pytest.mark.parser

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "charmodel_facts.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("charmodel_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load()


def _install(tmp: Path) -> Path:
    return make_source_install(
        tmp / "install", stored=[], encoding_rows=[], root_blocks=[], rng=random.Random(10)
    )


def _run(root: Path, tables: Path, out: Path) -> int:
    return int(
        script.main(
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
                "2026-11-18",
                "--out",
                str(out),
            ]
        )
    )


def test_files_the_install_lacks_end_with_exit_1_and_nothing_written_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install(tmp_path)
    before = tree_state(root)
    out = tmp_path / "out.json"
    assert _run(root, _tables(tmp_path), out) == 1
    assert capsys.readouterr().err.startswith("charmodel_facts: ")
    assert not out.exists()
    assert tree_state(root) == before


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
    assert _run(_install(tmp_path), _tables(tmp_path, skip="ChrModelTextureLayer"), out) == 1
    assert "no ChrModelTextureLayer for" in capsys.readouterr().err
    assert not out.exists()
