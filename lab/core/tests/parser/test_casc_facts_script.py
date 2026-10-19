"""`scripts/casc_facts.py` on a constructed install (M15-03; labelled
`constructed`): it writes the derived record, holds no file bytes and no
install path, refuses an output inside an install or one already there, and
changes nothing in the install."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from _casc_build import FLAVOR, PRODUCT, make_install, tree_state

pytestmark = pytest.mark.parser

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "casc_facts.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("casc_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


casc_facts = _load()


def _run(root: Path, out: Path) -> int:
    args = ["--root", str(root), "--flavor", FLAVOR, "--captured", "2026-10-19", "--out", str(out)]
    return int(casc_facts.main(args))


def test_writes_the_derived_record_constructed(tmp_path: Path) -> None:
    root, _ = make_install(tmp_path / "install")
    before = tree_state(root)
    out = tmp_path / "out" / "casc-index.json"
    out.parent.mkdir()
    assert _run(root, out) == 0
    assert tree_state(root) == before
    record = json.loads(out.read_text(encoding="utf-8"))
    assert (record["product"], record["flavor"], record["build"]) == (PRODUCT, FLAVOR, 65432)
    assert (record["bucket_count"], record["entry_count"]) == (2, 10)
    assert record["encoding_size"] == {"decoded": 1000, "encoded": 970}
    assert [v["entries_from_previous_bucket"] for v in record["versions"]] == [0, 0]
    assert record["missing_buckets"] == [b for b in range(16) if b not in (3, 9)]
    assert record["key_rows"][0]["why"] == "the build config's encoding key"
    assert record["key_rows"][0]["size"] == 1000
    text = out.read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "speech" not in text


def test_refuses_an_output_inside_the_install_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = make_install(tmp_path)
    before = tree_state(root)
    assert _run(root, root / "Data" / "facts.json") == 1
    assert "inside a game install" in capsys.readouterr().err
    assert tree_state(root) == before


def test_refuses_an_output_already_there_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = make_install(tmp_path / "install")
    out = tmp_path / "facts.json"
    out.write_text("kept", encoding="utf-8")
    assert _run(root, out) == 1
    assert "already there" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "kept"


def test_a_casc_error_is_one_line_and_writes_nothing_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = make_install(tmp_path / "install", indexes={"0000000001.idx": b"\x00" * 8})
    out = tmp_path / "facts.json"
    assert _run(root, out) == 1
    err = capsys.readouterr().err
    assert err.startswith("casc_facts: ") and err.count("\n") == 1
    assert not out.exists()
