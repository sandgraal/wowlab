# Probe from review of m11/09-sv-merge; reproduces the loader check silently skipped (and --from failing) when another account folder holds a character folder of the same name.
"""`_loader_check` and the `--from CHARACTER` lookup call `_character_sv` with
every account's SavedVariables files (`lay.saved_variables()`), not only the
`--account` one. `_character_sv` filters by realm folder and character
folder but not by account, and returns None unless exactly one file matches.
So when a second account folder holds a `1/<First>-<Second>/` folder of the
same name (a character moved between two WoW licences on one Battle.net
account leaves its old WTF folder behind; the client never cleans it up),
the loader check finds two `WowLab.lua` files, treats that as "no capture",
prints "No WowLab.lua ... the SavedVariables loader was not checked", and
writes, although the two newest snapshots of the target account's file show
`probe.loads` going down (docs/LAB_PLAN.md §13.4, M11-09T rulings: refused,
exit 3). `--from` the same way reports "has no WowLab.lua" for a file that
is there. `_merge_target` already filters by account (`usable`); the other
two call sites do not.

Positive control: the same sequence without the second account folder is
refused with exit 3.

Constructed: the captured tree copied into `tmp_path`, with a copy of one
character's real `WowLab.lua` placed under a second, invented account
folder. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    ACCOUNT,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_svmerge import CHAR_A, CHAR_B, DBM_NAME, DBM_VAR, LAB, LAB_A, LAB_B, REAL_B, _snap

OTHER_ACCOUNT = "90000002#1"


def _second_account_with(flavor: Path, character: str, source: Path) -> None:
    folder = flavor / "WTF" / "Account" / OTHER_ACCOUNT / "1" / character / "SavedVariables"
    folder.mkdir(parents=True)
    shutil.copyfile(source, folder / LAB)


def _loads_went_down(flavor: Path) -> None:
    _snap("loads 4")
    (flavor / LAB_A).write_bytes(REAL_B)  # loads 2
    _snap("loads 2")


def _copy_within() -> Any:
    key = f'{DBM_VAR}["Labchard Labrealmg"]={DBM_VAR}["Labcharb Labrealmf"]'
    return run(
        "sv", "merge", DBM_NAME, "--into", CHAR_A, "--account", ACCOUNT,
        "--key", key, "--yes",
    )  # fmt: skip


def test_positive_control_loads_down_is_refused_with_one_account_constructed(
    flavor: Path,
) -> None:
    _loads_went_down(flavor)
    result = _copy_within()
    assert result.exit_code == 3, (result.exit_code, result.stdout, result.stderr)


def test_loads_down_is_refused_when_another_account_has_the_same_character_constructed(
    flavor: Path,
) -> None:
    _second_account_with(flavor, CHAR_A, flavor / LAB_A)
    _loads_went_down(flavor)
    before = (flavor / "WTF" / "Account" / ACCOUNT / "SavedVariables" / DBM_NAME).read_bytes()
    result = _copy_within()
    after = (flavor / "WTF" / "Account" / ACCOUNT / "SavedVariables" / DBM_NAME).read_bytes()
    assert result.exit_code == 3, (result.exit_code, result.stdout, result.stderr)
    assert after == before, "nothing may be written when the loader check refuses"


def test_from_character_finds_its_file_when_another_account_has_the_same_character_constructed(
    flavor: Path,
) -> None:
    _second_account_with(flavor, CHAR_B, flavor / LAB_B)
    result = run(
        "sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--account", ACCOUNT,
        "--take", "theirs", "--yes",
    )  # fmt: skip
    assert "has no" not in result.stderr, result.stderr
    assert result.exit_code == 0, (result.exit_code, result.stdout, result.stderr)
