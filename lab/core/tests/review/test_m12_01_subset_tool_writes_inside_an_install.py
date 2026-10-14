# Probe from review of m12/01-trait-spell-tables; reproduces scripts/wago_subset.py writing its --out file inside a game install.
"""`scripts/wago_subset.py` opens `--out` for writing wherever it points,
including inside a game install (a directory tree with `.build.info` at its
root). L1 and L2 (`AGENTS.md`): nothing but `guard` writes into an install,
and there is no second code path. Every other writer in the repository refuses
such a location: `scripts/lab_capture.py` refuses an `--out` inside the
install ("--out is inside the install; the install is never written to (L1)"),
`gamedata` refuses a cache inside one (`_refuse_install`, which walks the
resolved path's parents for `.build.info`), and the Wave 3 page and database
tickets (M12-03, M12-08) each require the same refusal.

All inputs are constructed (labelled): a two-line CSV and a fake install root
in `tmp_path`. Nothing touches a real install.

Expected: an `--out` inside an install, directly or through a symlinked
directory, is a refusal (exit 1, the tool's `wago_subset: refused:` prefix,
nothing written). Positive control: the same run with `--out` in a plain
directory writes the subset.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "wago_subset.py"
SOURCE = b"ID,Name\n1,a\n2,b\n"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wago_subset_review_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wago_subset = _load()


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "SpellName.csv"
    src.write_bytes(SOURCE)
    return src


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    (root / "flavor").mkdir(parents=True)
    (root / ".build.info").write_bytes(b"Branch!STRING:0|Version!STRING:0\nus|1.0.0.1\n")
    return root


def _run(src: Path, out: Path) -> int:
    argv = [
        str(src),
        "--sha256",
        hashlib.sha256(SOURCE).hexdigest(),
        "--column",
        "ID",
        "--value",
        "1",
        "--out",
        str(out),
    ]
    code: int = wago_subset.main(argv)
    return code


def test_constructed_positive_control_out_in_a_plain_directory_is_written(tmp_path: Path) -> None:
    plain = tmp_path / "plain" / "flavor"
    plain.mkdir(parents=True)
    out = plain / "SpellName.subset.csv"
    assert _run(_source(tmp_path), out) == 0
    assert out.read_bytes() == b"ID,Name\n1,a\n"


def test_constructed_out_inside_an_install_is_refused_and_nothing_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install(tmp_path)
    out = root / "flavor" / "SpellName.subset.csv"
    code = _run(_source(tmp_path), out)
    written = out.exists()
    assert (code, written) == (1, False), (
        f"exit {code}, file written inside the install: {written}; "
        "nothing but guard writes into an install (L1, L2)"
    )
    assert capsys.readouterr().err.startswith("wago_subset: refused: ")


def test_constructed_out_through_a_symlink_into_an_install_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install(tmp_path)
    link = tmp_path / "looks-harmless"
    try:
        link.symlink_to(root / "flavor", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    out = link / "SpellName.subset.csv"
    code = _run(_source(tmp_path), out)
    written = (root / "flavor" / "SpellName.subset.csv").exists()
    assert (code, written) == (1, False), (
        f"exit {code}, file written inside the install through a symlink: {written}"
    )
    assert capsys.readouterr().err.startswith("wago_subset: refused: ")
