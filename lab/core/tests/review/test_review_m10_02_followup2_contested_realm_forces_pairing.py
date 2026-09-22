# Probe from review of m10/02-lab-capture-followup-2 (fix round 2); reproduces a group paired
# with a leftover realm folder because its real one was dropped as "contested".
"""Found by running `scripts/lab_capture.py` at af9afb5 on trees built here.

`_twin_realms` ends by giving each unsettled group "the free candidates no
other unsettled group also has". Dropping a contested realm folder is safe
only while two or more candidates remain; when exactly one remains, `_twin`
pairs with it as though the data had decided. Tree: `70/Eve-Qorv` and
`71/Cara-Moon`; `Some Realm/{Eve, Cara}`, `Other Realm/{Cara}`, and a
leftover `Old Realm/{Eve}`. Group 70 may be `Some Realm` or `Old Realm`, and
group 71 `Some Realm` or `Other Realm`; `70 -> Some, 71 -> Other` and
`70 -> Old, 71 -> Some or Other` all fit the folders. The tool drops
`Some Realm` from both and pairs `Eve-Qorv` with `Old Realm/Eve` and
`Cara-Moon` with `Other Realm/Cara`, silently. Without the leftover folder,
group 70 has one candidate and the pairing is forced and correct.

A randomised search (3000 trees of 3 to 4 groups, every twin present, 0 to 3
leftovers) found 10 such pairings with a realm other than the one the tree was
built from; order freedom held in every tree.

Constructed input, invented names, built in `tmp_path`. No real install
(ADR-0012).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup2_contested"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()
OWN = ["Some Realm/Eve", "Some Realm/Cara", "Other Realm/Cara"]


def _pairing(account: Path, twins: list[str]) -> dict[str, str | None]:
    for character in ("70/Eve-Qorv", "71/Cara-Moon"):
        (account / character).mkdir(parents=True)
    for twin in twins:
        (account / twin).mkdir(parents=True)
        (account / twin / "AddOns.txt").write_bytes(b"x\n")
    return {
        unit[0].name: unit[1].relative_to(account).as_posix() if len(unit) > 1 else None
        for unit in lab_capture.character_units(account)
        if lab_capture.is_group(unit[0].parent.name)
    }


def test_constructed_positive_control_a_single_candidate_is_forced(tmp_path: Path) -> None:
    assert _pairing(tmp_path / "account", OWN) == {
        "Eve-Qorv": "Some Realm/Eve",
        "Cara-Moon": "Other Realm/Cara",
    }


def test_constructed_a_group_whose_realm_is_ambiguous_is_not_paired(tmp_path: Path) -> None:
    paired = _pairing(tmp_path / "account", [*OWN, "Old Realm/Eve"])
    # At af9afb5: {"Eve-Qorv": "Old Realm/Eve", "Cara-Moon": "Other Realm/Cara"}.
    assert paired["Eve-Qorv"] is None, paired
