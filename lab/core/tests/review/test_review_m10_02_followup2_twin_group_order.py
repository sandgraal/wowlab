# Probe from review of m10/02-lab-capture-followup-2; reproduces a Forever character
# paired with another character's twin, decided by which digits folder sorts first.
"""Found by running `scripts/lab_capture.py` at ffd35b5 on trees built here.

`_twin_realms` prefers, for each digits group, the realm folders whose children
are ALL first names of that group, and then lets a group with one preferred
candidate claim it. The preference is treated as exhaustive: when two groups
share a first name and a leftover twin (a `<Realm>/<First>/` folder whose
`<digits>/<First>-<Second>/` folder is gone) sits in one realm folder, both
groups prefer the other realm folder. The group listed first claims it; the
second is left with no candidate at all, although a realm folder holding its
first name is still free. So which character gets which twin depends on the
digits folders' names, not on the data: renaming `70` and `71` flips it, and
in one of the two orders a character is paired with a twin that belongs to a
different character (its AddOns.txt is captured as the played character's).

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
    name = "lab_capture_review_probe_followup2_twins"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _pairing(tmp: Path, folders: dict[str, str], twins: list[str]) -> dict[str, str | None]:
    """Character folder name -> the twin paired with it (relative), for one account."""
    account = tmp / "account"
    for group, character in folders.items():
        (account / group / character).mkdir(parents=True)
    for twin in twins:
        (account / twin).mkdir(parents=True)
        (account / twin / "AddOns.txt").write_bytes(b"x\n")
    result: dict[str, str | None] = {}
    for unit in lab_capture.character_units(account):
        if lab_capture.is_group(unit[0].parent.name):
            twin = unit[1].relative_to(account).as_posix() if len(unit) > 1 else None
            result[unit[0].name] = twin
    return result


def _both_orders(tmp_path: Path, twins: list[str]) -> tuple[dict[str, str | None], ...]:
    first = _pairing(tmp_path / "a", {"70": "Alyra-Qorv", "71": "Alyra-Glade"}, twins)
    second = _pairing(tmp_path / "b", {"71": "Alyra-Qorv", "70": "Alyra-Glade"}, twins)
    return first, second


def test_constructed_positive_control_pairing_ignores_the_digits_folder_names(
    tmp_path: Path,
) -> None:
    """No leftover: the data cannot tell the two realms apart, and in either order
    both characters stay unpaired rather than being guessed."""
    first, second = _both_orders(tmp_path, ["Some Realm/Alyra", "Other Realm/Alyra"])
    assert first == second == {"Alyra-Qorv": None, "Alyra-Glade": None}


def test_constructed_leftover_twin_does_not_make_pairing_depend_on_folder_order(
    tmp_path: Path,
) -> None:
    """`Some Realm/Dax` is a leftover twin (its `<digits>/Dax-<Second>` was deleted).
    It says nothing about which group is `Some Realm`, so renaming the digits folders
    must not move a twin from one character to the other."""
    twins = ["Some Realm/Alyra", "Some Realm/Dax", "Other Realm/Alyra"]
    first, second = _both_orders(tmp_path, twins)
    # At ffd35b5 the order 70, 71 pairs Alyra-Qorv with Other Realm/Alyra and leaves
    # Alyra-Glade unpaired; the order 71, 70 does the reverse.
    assert first == second
