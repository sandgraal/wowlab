# Probe from review of m10/02-lab-capture-followup-2 (fix round 2); reproduces `--character`
# on a two-flavor install exiting 2 with nothing captured unless `--flavor` is also given.
"""Found by running `scripts/lab_capture.py` at af9afb5 on a tree built here.

Fix round 2 makes `--character` that matches nothing stop with exit 2, per
flavor, whenever that flavor has characters. A character lives in one flavor,
so on an install with retail and the Forever beta, `--character <First>-<Second>`
(the runbook's own advice for choosing another character, with both flavors
captured in one run and `--flavor` never mentioned) now fails on the retail
flavor: exit 2, nothing written, and the message "no such character in this
flavor" does not say which flavor. At 4d2e65e and on main the same command
captured both flavors.

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
    "us|1|0123456789abcdef0123456789abcdef|12.0.5.66666|wow\n"
    "us|1|fedcba9876543210fedcba9876543210|1.60.1.60001|wow_classic_beta\n"
)
ACCOUNT = "123456789#1"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup2_other_flavor"
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


def _run(tmp_path: Path, *extra: str) -> tuple[int, list[str]]:
    root = tmp_path / "World of Warcraft"
    _write(root / ".build.info", BUILD_INFO)
    for flavor, product in (("_retail_", "wow"), ("_classic_beta_", "wow_classic_beta")):
        _write(root / flavor / ".flavor.info", f"Product Flavor!STRING:0\n{product}\n")
        _write(root / flavor / "WTF" / "Config.wtf", 'SET portal "us"\n')
    retail = root / "_retail_" / "WTF" / "Account" / ACCOUNT
    _write(retail / "Area 52" / "Zugzug" / "chat-cache.txt", "SAY 255 255 255\n")
    forever = root / "_classic_beta_" / "WTF" / "Account" / ACCOUNT
    _write(forever / "70" / "Alyra-Qorv" / "chat-cache.txt", "SAY 255 255 255\n")
    _write(forever / "Some Realm" / "Alyra" / "AddOns.txt", "Plain: enabled\n")
    out = tmp_path / "out"
    argv = ["--root", str(root), "--out", str(out), "--platform", "macos", *extra]
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = int(lab_capture.main(argv))
    except SystemExit as stop:
        rc = int(stop.code or 0)
    written = [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()]
    return rc, written if out.exists() else []


def test_constructed_positive_control_with_flavor_the_character_is_captured(
    tmp_path: Path,
) -> None:
    rc, written = _run(tmp_path, "--character", "Alyra-Qorv", "--flavor", "_classic_beta_")
    assert rc == 0 and any(d.endswith("-Labrealmb/chat-cache.txt") for d in written), written


def test_constructed_character_in_one_flavor_does_not_stop_the_other(tmp_path: Path) -> None:
    rc, written = _run(tmp_path, "--character", "Alyra-Qorv")
    # At af9afb5: rc == 2 and nothing is written.
    assert rc == 0, rc
    assert any(d.endswith("/chat-cache.txt") and "_classic_beta_" in d for d in written), written
