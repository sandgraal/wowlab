# Probe from review of m22/25-curseforge; reproduces `lab_capture.py --file` without `--root` writing its output inside an install when `--out` points there
"""Review probe for `scripts/lab_capture.py --file` (M22-25).

CONSTRUCTED INPUT (L8, boundary case). The install is a stand-in under
`tmp_path` (a folder holding `.build.info` and a flavor folder), the app
file is invented, and the home folder is a stand-in. No test needs or
touches a real install (ADR-0012).

The install mode refuses an `--out` inside the install ("the install is
never written to (L1)"), and `--file` mode refuses a `--file` inside an
install (`_in_an_install`). But `--file` mode without `--root` never checks
`--out`: pointed at a flavor's `Interface/AddOns`, it creates
`<source>/<platform>/<name>` there, a new folder the client reads as an
addon folder. `--out` inside an install should refuse in this mode too.

Positive control: the same capture with `--out` outside the install
writes and returns 0, so the refusal under test is the only difference.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "lab_capture.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lab_capture_m22_25_out_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    app = home / "Library" / "Application Support" / "SomeManager"
    app.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    src = app / "records.json"
    src.write_bytes(b'{"a": 1}')
    root = tmp_path / "World of Warcraft"
    (root / "_x_" / "Interface" / "AddOns").mkdir(parents=True)
    (root / ".build.info").write_bytes(b"Branch!STRING:0|Active!DEC:1\n")
    return src, root


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _capture(src: Path, out: Path) -> int:
    argv = ["--file", str(src), "--out", str(out), "--platform", "macos", "--source", "x"]
    try:
        return int(lab_capture.main(argv))
    except SystemExit as exc:  # argparse-style exits count as refusals
        return int(exc.code or 0)


def test_out_inside_an_install_is_refused_in_file_mode_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, root = _setup(tmp_path, monkeypatch)
    before = _tree(root)
    rc = _capture(src, root / "_x_" / "Interface" / "AddOns")
    assert _tree(root) == before, "lab_capture --file wrote inside the install"
    assert rc != 0


def test_positive_control_out_outside_the_install_writes_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, root = _setup(tmp_path, monkeypatch)
    before = _tree(root)
    out = tmp_path / "staging"
    assert _capture(src, out) == 0
    assert (out / "x" / "macos" / "records.json").read_bytes() == b'{"a": 1}'
    assert _tree(root) == before
