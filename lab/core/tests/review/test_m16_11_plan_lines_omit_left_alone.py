# Probe from review of m16/11-restore-left-alone; reproduces `--json` plan_lines omitting the "Left alone" lines the text plan shows.
"""`RestoreReport.plan_lines` is "the text plan, line by line as the terminal
shows it" for the UI's confirm step (docs/LAB_PLAN.md §18.1 item 3 and item
7, M16-10). The merge of main into M16-11 (f598111) prints the "Left alone"
lines in the text plan but keeps them out of `plan_lines`, so a player who
confirms in the UI is never told the added files stay.

Inputs labelled constructed (L8): a SavedVariables value flipped, and files
added under the snapshot's folders since it was taken. Nothing touches a real
install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    _flip_syndicator,
    _json,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    user_data,  # noqa: F401
)
from test_cli_restore_left_alone import ADDED, _add, _snapshot

from wowlab_core import cli

DRY = "Dry run: nothing was changed."


def _text_plan(snapshot_id: str) -> list[str]:
    lines = _plain(ok("snap", "restore", snapshot_id, "--dry-run").stdout).splitlines()
    assert lines[-1] == DRY
    return lines[:-1]


def test_plan_lines_match_text_plan_without_added_files_control(root: Path, flavor: Path) -> None:
    """Positive control: with nothing added, plan_lines is the text plan."""
    a = _snapshot()
    _flip_syndicator(flavor)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert report.plan_lines == _text_plan(a.id)


def test_plan_lines_carry_the_left_alone_lines_constructed(root: Path, flavor: Path) -> None:
    a = _snapshot()
    _flip_syndicator(flavor)
    _add(flavor, ADDED)
    text = _text_plan(a.id)
    assert any(ln.startswith("  Left alone: ") for ln in text)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert report.plan_lines == text
