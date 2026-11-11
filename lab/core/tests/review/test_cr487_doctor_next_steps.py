# Probe from review of m16/37-doctor-next-steps; reproduces a next-step command that does not work after `doctor --root`, and "nothing to do" over items wowlab cannot judge.
"""Two defects in `doctor`'s "Next steps" block (M16-37).

1. The fix names `wowlab addon install lab` without the `--root` the player
   gave `doctor`. Run as printed, it finds no install (or, on a machine with
   an install in the default place, a different one). The positive control:
   with `WOWLAB_WOW_ROOT` set, the same printed command works.
2. With every item `unknown` (no fix for any of them), the block's heading
   says "Next steps: nothing to do." over lines saying wowlab cannot tell.

Constructed: the captured tree copied into `tmp_path` as `test_cli.py`
builds it; a `Logs/` listing that fails is monkeypatched. Nothing reads or
writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401
    replayed,  # noqa: F401
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import addoninstall, cli, install


def _printed_command(*doctor_args: str) -> list[str]:
    result = run("doctor", "--json", *doctor_args)
    report = cli.DoctorReport.model_validate_json(result.stdout)
    (step,) = [s for s in report.next_steps if s.item == "lab_addon"]
    assert step.fix is not None
    return shlex.split(step.fix.split("`")[1])


def test_control_the_printed_command_works_with_the_variable_constructed(root: Path) -> None:
    argv = _printed_command()
    assert argv[0] == "wowlab"
    result = run(*argv[1:], "--yes")
    assert result.exit_code == 0, (result.stdout, result.stderr)


def test_the_printed_command_works_after_doctor_root_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(install.ENV_ROOT)
    argv = _printed_command("--root", str(root))
    result = run(*argv[1:], "--yes")
    assert result.exit_code == 0, (argv, result.stdout, result.stderr)


def test_nothing_to_do_is_not_said_over_unknown_items_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    unknown = addoninstall.LabAddonState(status="unknown", detail="the TOC cannot be read")
    steps = [
        cli._lab_addon_step("_a_", unknown, several=False),
        cli.NextStep(
            item="combat_log",
            flavor_folder="_a_",
            state="unknown",
            detail="wowlab could not look in Logs/: Permission denied",
        ),
    ]
    cli._print_next_steps(steps)
    out = capsys.readouterr().out
    assert "cannot tell" in out  # control: the items are unknown
    assert "nothing to do" not in out, out
