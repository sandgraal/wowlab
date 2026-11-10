# Probe from review of m16/10-plan-diff-names; reproduces a SavedVariables-only restore plan printing the WTF scope line and the server-sync login caveat
"""A restore whose only change is a SavedVariables file gets one detail line
(the values summary), and that alone appends `_SCOPE_LINE` ("the game uses
the account or character copy depending on its settings") and `_RELOGIN`
("the server may replace binds, macros, settings and other *-cache files")
to the plan. Neither is true of a SavedVariables file: the client loads both
the account and the character SavedVariables, and server sync does not
touch them. `_relogin_notes`, which decides the same caveat after the
write, returns nothing for this path, so the plan and the result disagree.

The control changes `bindings-cache.wtf`, where both lines belong. Real
captures: two sessions' `WowLab.lua` (`macos-70425`, `macos-70450` rows).
Nothing touches a real install.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    ACCOUNT,
    CHARACTER,
    FIXTURES,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import cli

ACC = f"WTF/Account/{ACCOUNT}"
LAB_SV = f"{ACC}/1/{CHARACTER}/SavedVariables/WowLab.lua"
SV_EARLY = FIXTURES / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme"
SV_LATER = FIXTURES / "macos-70450/forever/WTF/Account/90000001#6/1/Sangraal-Sett"


def _snapshot() -> str:
    return str(json.loads(ok("snap", "create", "-m", "constructed", "--json").stdout)["id"])


def test_control_a_bindings_restore_plan_has_both_lines(root: Path, flavor: Path) -> None:
    snap = _snapshot()
    b = flavor / ACC / "bindings-cache.wtf"
    b.write_bytes(b.read_bytes().replace(b"bind Q MULTIACTIONBAR1BUTTON1\r\n", b""))
    out = _plain(ok("snap", "restore", snap, "--dry-run").stdout).splitlines()
    assert cli._SCOPE_LINE in out and cli._RELOGIN in out


def test_a_saved_variables_only_restore_plan_has_neither_line(root: Path, flavor: Path) -> None:
    sv = flavor / LAB_SV
    sv.parent.mkdir()
    shutil.copyfile(SV_EARLY / "SavedVariables/WowLab.lua", sv)
    snap = _snapshot()
    shutil.copyfile(SV_LATER / "SavedVariables/WowLab.lua", sv)
    out = _plain(ok("snap", "restore", snap, "--dry-run").stdout).splitlines()
    assert any(ln.startswith("      values: ") for ln in out), out
    assert cli._relogin_notes([LAB_SV]) == []
    assert cli._SCOPE_LINE not in out, out
    assert cli._RELOGIN not in out, out
