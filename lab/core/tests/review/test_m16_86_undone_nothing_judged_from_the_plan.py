# Probe from review of m16/86-undo-plan-rollback; reproduces "Undone; no file needed changing" printed after an undo that changed a file, because it is judged from the plan shown before the prompt
"""`undo` says "Undone; no file needed changing ... nothing for `wowlab undo`
to reverse" when the plan it showed held the no-change line. The plan is
made before the prompt; the undo runs after it. A journaled file the client
rewrites while the player reads the plan is put back by the undo, so a file
did change and a second `wowlab undo` does reverse it, yet the message says
otherwise and the next-login note is left out. The undo's own journal record
says what it changed.

Constructed (boundary): a write-gate transaction that rolls back cleanly, as
`test_undo_plan_rollback_unfinished.py` makes it, and a stand-in for the
player's answer that rewrites `Syndicator.lua` before saying yes (the client
writing it at logout while the prompt waits). No real install is read or
written.
"""

# ruff: noqa: F811
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    SYNDICATOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard, install

NOTHING_DONE = "Undone; no file needed changing."


class _StopError(Exception):
    """The caller's failure that makes the gate roll back (constructed)."""


def _rolled_back(root: Path) -> guard.HistoryRecord:
    (discovered,) = install.read_install(root).flavors
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(discovered, label="constructed: fails cleanly") as tx:
            tx.write(SYNDICATOR, b"Syndicator = {}\n")
            raise _StopError
    record = guard.history()[-1]
    assert record.state == "rolled_back"
    return record


def _answer_yes_after(flavor: Path | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the prompt with yes; with `flavor`, rewrite the journaled file
    first, as the client would while the prompt waits."""

    def confirm(question: str, yes: bool) -> None:
        if flavor is not None:
            (flavor / SYNDICATOR).write_bytes(b"Syndicator = { written = true }\n")

    monkeypatch.setattr(cli, "_confirm", confirm)


def test_positive_control_nothing_changed_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _rolled_back(root)
    _answer_yes_after(None, monkeypatch)
    done = ok("undo", "--expect", record.id)
    assert NOTHING_DONE in done.stdout, done.stdout
    assert guard.history()[-1].paths == ()


def test_the_message_follows_what_the_undo_changed_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _rolled_back(root)
    _answer_yes_after(flavor, monkeypatch)
    done = ok("undo", "--expect", record.id)
    changed = [p.path for p in guard.history()[-1].paths]
    assert changed == [SYNDICATOR], changed  # the undo did put a file back
    assert NOTHING_DONE not in done.stdout, done.stdout
