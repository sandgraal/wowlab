# Probe from review of m10/02-lab-capture-followup; reproduces an own realm's spaced
# spelling written out untouched when only a `<digits>/<Name>-<Realm>` folder names it.
"""Found by running `scripts/lab_capture.py` at ebba464 on a tree built here.

ebba464 reads the Forever beta layout `<account>/<digits>/<Name>-<Realm>/` and
folds the folder's realm spelling (spaces dropped, "StormRage") into a known
realm when a retail twin `<account>/<Realm>/<Name>/` or `realmName` names it.
With neither, the realm is registered only in its folder spelling, whose
`REALM_TRANSFORMS` only ever remove separators, so "Storm Rage" inside a
SavedVariables key is not an identity token and not a survivor form: it is
written out with exit 0, no refusal and no note.

Constructed input with invented names, in the tool's own layout, built in
`tmp_path`. No real install (ADR-0012).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"
BUILD_INFO = (
    "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|Version!STRING:0|Product!STRING:0\n"
    "us|1|fedcba9876543210fedcba9876543210|1.60.1.60001|wow_classic_beta\n"
)
FLAVOR = "_classic_beta_"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_folder_only_realm"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _forever(root: Path, *, twin: bool) -> None:
    _write(root / ".build.info", BUILD_INFO)
    base = root / FLAVOR
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow_classic_beta\n")
    _write(base / "WTF" / "Config.wtf", 'SET portal "us"\n')
    account = base / "WTF" / "Account" / "123456789#1"
    _write(account / "config-cache.wtf", 'SET chatBubbles "1"\n')
    _write(
        account / "70" / "Vex-StormRage" / "SavedVariables" / "DBM-Core.lua",
        '\nDBM_Chars = {\n\t["Vex-StormRage"] = 1,\n\t["Vex - Storm Rage"] = 2,\n}\n',
    )
    if twin:
        _write(account / "Storm Rage" / "Vex" / "AddOns.txt", "Solo: enabled\n")


def _written(root: Path, out: Path) -> dict[str, bytes]:
    argv = ["--root", str(root), "--out", str(out), "--platform", "macos", "--sv", "DBM-Core.lua"]
    lab_capture.main(argv)
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def test_constructed_positive_control_a_twin_supplies_the_spaced_spelling(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    _forever(root, twin=True)
    written = _written(root, tmp_path / "incoming")
    assert any(name.endswith("DBM-Core.lua") for name in written)
    assert not [name for name, data in written.items() if b"Storm" in data or "Storm" in name]


def test_constructed_spaced_spelling_of_a_folder_only_realm_never_leaves(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    _forever(root, twin=False)
    written = _written(root, tmp_path / "incoming")
    leaked = [name for name, data in written.items() if b"Storm Rage" in data]
    assert not leaked, f"own realm's spaced spelling written out in {leaked}"
