# Probe from review of m10/02-lab-capture-followup-2; reproduces an unpaired Forever twin
# chosen as the played character, so no `<digits>/<First>-<Second>/` file is captured.
"""Found by running `scripts/lab_capture.py` at ffd35b5 on a tree built here.

One digits group (`70/Alyra-Sett`) and two realm folders that both hold an
`Alyra/` twin (one of them a leftover). `_twin_realms` keeps both as candidates,
`_twin` finds two `Alyra` folders and the second name `Sett` spells neither
realm, so the character stays unpaired and BOTH twins become characters of
their own. A twin is written at every logout, so it is at least as new as the
character folder, and `newest_unit` picks it: the capture holds one AddOns.txt
and none of the character folder's files (caches, SavedVariables), exit 0,
with no note. This is the failure the follow-up set out to fix, left in place
whenever the pairing is ambiguous.

Constructed input, invented names, built in `tmp_path`. No real install
(ADR-0012).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
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
OLD = 1_000_000_000


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup2_unpaired"
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


def _stamp(when: int, *paths: Path) -> None:
    for path in paths:
        for entry in [*path.rglob("*"), path]:
            os.utime(entry, (when, when))


def _capture(tmp_path: Path, realm_folders: list[str]) -> list[str]:
    root = tmp_path / "World of Warcraft"
    _write(root / ".build.info", BUILD_INFO)
    base = root / FLAVOR
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow_classic_beta\n")
    _write(base / "WTF" / "Config.wtf", 'SET portal "us"\n')
    account = base / "WTF" / "Account" / ACCOUNT
    _write(account / "70" / "Alyra-Sett" / "chat-cache.txt", "SAY 255 255 255\n")
    for realm in realm_folders:
        _write(account / realm / "Alyra" / "AddOns.txt", "Plain: enabled\n")
    _stamp(OLD, root)
    # Played last, then the twin written at the same logout, a second later.
    _stamp(OLD + 100, account / "70")
    _stamp(OLD + 101, account / realm_folders[0])
    out = tmp_path / "out"
    with contextlib.redirect_stdout(io.StringIO()):
        rc = lab_capture.main(["--root", str(root), "--out", str(out), "--platform", "macos"])
    assert rc == 0
    return [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()]


def test_constructed_positive_control_one_twin_the_character_folder_is_captured(
    tmp_path: Path,
) -> None:
    written = _capture(tmp_path, ["Some Realm"])
    assert any(d.endswith("/1/Labchara-Labrealma/chat-cache.txt") for d in written), written
    assert any(d.endswith("/Labchara/AddOns.txt") for d in written), written


def test_constructed_leftover_twin_elsewhere_the_character_folder_is_still_captured(
    tmp_path: Path,
) -> None:
    """Whether or not a twin can be paired, the played character is the digits folder."""
    written = _capture(tmp_path, ["Some Realm", "Other Realm"])
    # At ffd35b5 the only account-level file written is `<Some Realm>/Labchara/AddOns.txt`.
    assert any(d.endswith("/chat-cache.txt") for d in written), written
