# Probe from review of m11/02-addon-install; reproduces an install that reports success after a file the plan called up to date was changed before the confirm.
"""`addoninstall.install` compares the gate's plan with the dry-run plan so a
file changed between the plan and the confirm makes the gate roll back. Only
the paths in `plan.plan` are written and compared; a file the dry run found
already up to date (`plan.unchanged`) is neither written nor checked. If it
changes between the plan and the confirm, install commits, says it installed
the lab-addon, and leaves that file differing from the sources.

Constructed: an installed copy with one stale file (so the plan is not
empty) and one up-to-date file edited after the plan, in the captured tree
copied into `tmp_path`. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    FLAVOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import addoninstall, install

LAB = "Interface/AddOns/WowLab"
SOURCE = Path(__file__).resolve().parents[4] / "lab" / "addon" / "WowLab"


def _flavor(root: Path) -> install.Flavor:
    return next(f for f in install.discover(root).flavors if f.folder == FLAVOR)


def test_positive_control_a_planned_file_changed_after_the_plan_rolls_back_constructed(
    root: Path, flavor: Path
) -> None:
    chosen = _flavor(root)
    plan = addoninstall.plan_install(chosen)
    target = flavor / LAB / "Core.lua"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"-- written after the plan\n")
    with pytest.raises(addoninstall.AddonChangedError):
        addoninstall.install(plan, chosen)
    assert target.read_bytes() == b"-- written after the plan\n"
    assert not (flavor / LAB / "WowLab.toc").exists()


def test_an_unchanged_file_changed_after_the_plan_is_not_left_behind_constructed(
    root: Path, flavor: Path
) -> None:
    ok("addon", "install", "lab", "--yes")
    (flavor / LAB / "Gear.lua").write_bytes(b"-- stale\n")
    chosen = _flavor(root)
    plan = addoninstall.plan_install(chosen)
    assert [i.path for i in plan.plan] == [f"{LAB}/Gear.lua"]
    assert f"{LAB}/Core.lua" in plan.unchanged

    core = flavor / LAB / "Core.lua"
    core.write_bytes(b"-- edited between the plan and the confirm\n")

    try:
        addoninstall.install(plan, chosen)
    except addoninstall.AddonChangedError:
        return  # the gate rolled back: acceptable
    # Install committed and reported success: the installed addon must then be the sources.
    assert core.read_bytes() == (SOURCE / "Core.lua").read_bytes(), (
        "install committed while a file the plan called up to date differs from the sources"
    )
