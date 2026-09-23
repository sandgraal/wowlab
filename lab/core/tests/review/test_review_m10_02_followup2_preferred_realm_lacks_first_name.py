# Probe from review of m10/02-lab-capture-followup-2 (fix round 1); reproduces a digits
# group paired with a realm folder that has no twin for one of the group's first names.
"""Found by running `scripts/lab_capture.py` at 4d2e65e on trees built here.

`_twin_realms` falls back to "preferred" candidates: realm folders whose
children are all first names of the group (children <= firsts). That test does
not ask whether the realm folder holds a twin for every first name of the
group. One group `70/{Alyra-Qorv, Bren-Moon}`, its realm folder
`Some Realm/{Alyra, Bren, Cara}` (Cara a leftover), and a leftover realm folder
`Old Realm/{Alyra}`: `Old Realm` is the only preferred candidate, so the group
is settled on it. `Alyra-Qorv` is paired with `Old Realm/Alyra` although only
`Some Realm` holds both first names, and `Bren-Moon` is left unpaired with a
note that counts one candidate folder. Without the leftover realm folder the
same group pairs both characters with `Some Realm`.

A randomised search over 3 to 4 groups (3000 trees, every group order) found no
order dependence, but 229 pairings with a realm other than the one the tree was
built from, 155 of them onto a realm folder missing one of the group's first
names, as here.

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
    name = "lab_capture_review_probe_followup2_preferred"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _pairing(account: Path, twins: list[str]) -> dict[str, str | None]:
    for character in ("70/Alyra-Qorv", "70/Bren-Moon"):
        (account / character).mkdir(parents=True)
    for twin in twins:
        (account / twin).mkdir(parents=True)
        (account / twin / "AddOns.txt").write_bytes(b"x\n")
    return {
        unit[0].name: unit[1].relative_to(account).as_posix() if len(unit) > 1 else None
        for unit in lab_capture.character_units(account)
        if lab_capture.is_group(unit[0].parent.name)
    }


OWN = ["Some Realm/Alyra", "Some Realm/Bren", "Some Realm/Cara"]


def test_constructed_positive_control_the_realm_holding_both_first_names_pairs(
    tmp_path: Path,
) -> None:
    assert _pairing(tmp_path / "account", OWN) == {
        "Alyra-Qorv": "Some Realm/Alyra",
        "Bren-Moon": "Some Realm/Bren",
    }


def test_constructed_a_realm_folder_missing_a_first_name_is_never_the_groups_realm(
    tmp_path: Path,
) -> None:
    paired = _pairing(tmp_path / "account", [*OWN, "Old Realm/Alyra"])
    # At 4d2e65e: {"Alyra-Qorv": "Old Realm/Alyra", "Bren-Moon": None}.
    assert paired["Alyra-Qorv"] != "Old Realm/Alyra", paired
    assert paired["Bren-Moon"] in {"Some Realm/Bren", None}, paired
