# Probe from review of m10/02-lab-capture-followup-2 (fix round 1); reproduces an explicit
# `--character` that matches no character capturing none, with exit 0 and nothing printed.
"""Found by running `scripts/lab_capture.py` at 4d2e65e on a tree built here.

The runbook tells the owner to choose a character with `--character REALM/NAME`.
Since fix round 1 a `<Realm>/<First>/` twin folder is never a character of its
own, so when the pairing is ambiguous its REALM/NAME label matches no unit. The
tool then captures no character folder at all, exits 0, and prints nothing
about it: no note, no refusal. (At ffd35b5 the same label captured the twin's
AddOns.txt.) The root cause is older: `--account` with no match stops with
exit 2 ("no such account folder in this flavor"), while `--character` with no
match, a typo included, silently selects nobody on main as well.

Constructed input, invented names, built in `tmp_path`. No real install
(ADR-0012).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"
BUILD_INFO = (
    "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|Version!STRING:0|Product!STRING:0\n"
    "us|1|fedcba9876543210fedcba9876543210|1.60.1.60001|wow_classic_beta\n"
)
FLAVOR = "_classic_beta_"
ACCOUNT = "123456789#1"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup2_character"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())


def _run(tmp_path: Path, wanted: str) -> tuple[int, str, list[str]]:
    root = tmp_path / "World of Warcraft"
    _write(root / ".build.info", BUILD_INFO)
    base = root / FLAVOR
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow_classic_beta\n")
    _write(base / "WTF" / "Config.wtf", 'SET portal "us"\n')
    account = base / "WTF" / "Account" / ACCOUNT
    _write(account / "70" / "Alyra-Qorv" / "chat-cache.txt", "SAY 255 255 255\n")
    for realm in ("Some Realm", "Other Realm"):  # ambiguous: the character stays unpaired
        _write(account / realm / "Alyra" / "AddOns.txt", "Plain: enabled\n")
    out = tmp_path / "out"
    buf = io.StringIO()
    argv = ["--root", str(root), "--out", str(out), "--platform", "macos"]
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = int(lab_capture.main([*argv, "--character", wanted]))
    except SystemExit as stop:
        rc = int(stop.code or 0)
    written = [p.name for p in out.rglob("*") if p.is_file()] if out.exists() else []
    return rc, buf.getvalue(), written


def test_constructed_positive_control_the_folder_label_captures_the_character(
    tmp_path: Path,
) -> None:
    rc, _, written = _run(tmp_path, "Alyra-Qorv")
    assert rc == 0 and "chat-cache.txt" in written


def test_constructed_a_character_label_that_matches_nothing_is_reported(
    tmp_path: Path,
) -> None:
    rc, stdout, written = _run(tmp_path, "Some Realm/Alyra")
    assert "chat-cache.txt" not in written
    # At 4d2e65e: rc == 0 and no line of the output mentions the selection.
    assert rc != 0 or "--character" in stdout, stdout
