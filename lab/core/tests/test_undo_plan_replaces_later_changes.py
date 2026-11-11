"""`undo`'s plan says what the undo does to later changes, in every state (M16-92).

When a file no longer holds what the change being undone wrote, the plan says
who probably changed it since, in words that fit the record's state (a change
that failed and was put back has no edits of its own left; one that did not
finish may not have written the file), and, whatever the state, that the undo
replaces those changes, keeps a copy of them first, and that running `wowlab
undo` again next, before playing, brings them back. Each test then holds the
plan to its word: the undo puts the file back as it was before the change,
and a second undo puts the later changes back.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one (constructed: no client is running). Nothing reads or writes a real
install.

Constructed (L8, boundary): the changes. Real write-gate transactions rewrite
`WTF/Config.wtf` (one CVar of the capture changed); the later change is one
`SET` line appended by the test, standing for the client at logout or, for a
change whose rollback did not finish, another program while the change ran.
A record that did not finish (`open`) is the committed record with only its
state changed, given to the plan alone; the journal on disk is not edited.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import typing
from collections.abc import Callable
from pathlib import Path

import pytest
from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard, install

CONFIG = "WTF/Config.wtf"
USE_TARGET_FPS = (b'SET useTargetFPS "0"', b'SET useTargetFPS "1"')
LATER = b'SET constructedLater "1"\r\n'

CONSEQUENCE = (
    "keeps a copy first, and running wowlab undo again next, before you play, brings them back)"
)
CHANGED_SINCE = (
    "      (changed since that transaction, probably by the game at logout or /reload: the "
    "lines below compare what is on disk now with what undo puts back, and include those "
    "later changes; undo replaces those changes, but keeps a copy first, and running wowlab "
    "undo again next, before you play, brings them back)"
)
CHANGED_UNFINISHED = (
    "      (changed by another program, such as the game at logout or /reload, while that "
    "change ran or since; that change stopped before it finished and may not have written "
    "this file: the lines below compare what is on disk now with what undo puts back; undo "
    "replaces those changes, but keeps a copy first, and running wowlab undo again next, "
    "before you play, brings them back)"
)
CHANGED_AFTER_ROLLBACK = (
    "      (changed since that transaction, probably by the game at logout or /reload; that "
    "change's own edits were already put back when it failed, so every line below is a "
    "later change, and undo only replaces them; it keeps a copy first, and running wowlab "
    "undo again next, before you play, brings them back)"
)
CHANGED_DURING = (
    "      (changed by another program, such as the game at logout or /reload, while that "
    "change ran, or since: the lines below compare what is on disk now with what undo puts "
    "back; undo replaces that program's changes, but keeps a copy first, and running wowlab "
    "undo again next, before you play, brings them back)"
)
LINE = {
    "committed": CHANGED_SINCE,
    "open": CHANGED_UNFINISHED,
    "rolled_back": CHANGED_AFTER_ROLLBACK,
    "rollback_incomplete": CHANGED_DURING,
}
STATES = typing.get_args(guard._State)


class _StopError(Exception):
    """The caller's failure that makes the gate roll back (constructed)."""


def _flavor(root: Path) -> install.Flavor:
    (discovered,) = install.read_install(root).flavors
    return discovered


def _changed(original: bytes) -> bytes:
    return original.replace(*USE_TARGET_FPS, 1)


def _committed(root: Path, flavor: Path) -> guard.HistoryRecord:
    original = (flavor / CONFIG).read_bytes()
    with guard.transaction(_flavor(root), label="constructed: finished") as tx:
        tx.write(CONFIG, _changed(original))
    (flavor / CONFIG).write_bytes(_changed(original) + LATER)  # the client at logout
    record = guard.history()[-1]
    assert record.state == "committed"
    return record


def _open(root: Path, flavor: Path) -> guard.HistoryRecord:
    return _committed(root, flavor).model_copy(update={"state": "open"})


def _rolled_back(root: Path, flavor: Path) -> guard.HistoryRecord:
    original = (flavor / CONFIG).read_bytes()
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(_flavor(root), label="constructed: fails cleanly") as tx:
            tx.write(CONFIG, _changed(original))
            raise _StopError
    (flavor / CONFIG).write_bytes(original + LATER)  # the client at logout
    record = guard.history()[-1]
    assert record.state == "rolled_back"
    return record


def _rollback_incomplete(root: Path, flavor: Path) -> guard.HistoryRecord:
    original = (flavor / CONFIG).read_bytes()
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(_flavor(root), label="constructed: fails") as tx:
            tx.write(CONFIG, b'SET constructed "by wowlab"\n')
            # Another program, mid-change.
            (flavor / CONFIG).write_bytes(_changed(original) + LATER)
            raise _StopError
    record = guard.history()[-1]
    assert record.state == "rollback_incomplete"
    return record


MAKE: dict[str, Callable[[Path, Path], guard.HistoryRecord]] = {
    "committed": _committed,
    "open": _open,
    "rolled_back": _rolled_back,
    "rollback_incomplete": _rollback_incomplete,
}


def test_every_state_is_pinned() -> None:
    assert set(MAKE) == set(LINE) == set(STATES)


CHANGED_UNKNOWN_STATE = (
    "      (changed while that transaction ran or since; this version of wowlab cannot tell "
    "by what: the lines below compare what is on disk now with what undo puts back; undo "
    "replaces those changes, but keeps a copy first, and running wowlab undo again next, "
    "before you play, brings them back)"
)


def test_a_state_this_version_does_not_know_guesses_no_one_constructed(
    root: Path, flavor: Path
) -> None:
    """M16-98: a record in a state this version does not know (a journal a
    later version wrote; constructed by changing the committed record's
    state, given to the plan alone) gets a neutral line, not the finished
    change's guess that the game changed the file."""
    record = _committed(root, flavor).model_copy(update={"state": "constructed_later"})
    lines = cli._undo_lines(record)
    at = lines.index(f"  put back   {CONFIG}  (as it was before that transaction)")
    assert lines[at + 1] == CHANGED_UNKNOWN_STATE, lines
    assert "probably" not in lines[at + 1] and "the game" not in lines[at + 1]
    assert sum(ln.endswith(CONSEQUENCE) for ln in lines) == 1, lines


@pytest.mark.parametrize("state", STATES)
def test_the_plan_says_the_undo_replaces_later_changes_constructed(
    root: Path, flavor: Path, state: str
) -> None:
    record = MAKE[state](root, flavor)
    assert record.state == state
    lines = cli._undo_lines(record)
    at = lines.index(f"  put back   {CONFIG}  (as it was before that transaction)")
    assert lines[at + 1] == LINE[state], lines
    assert lines[at + 1].endswith(CONSEQUENCE)
    assert sum(ln.endswith(CONSEQUENCE) for ln in lines) == 1, lines


@pytest.mark.parametrize("state", STATES)
def test_the_text_plan_shows_the_line_constructed(
    root: Path, flavor: Path, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = MAKE[state](root, flavor)
    monkeypatch.setattr(guard, "history", lambda **k: (record,))
    shown = run("undo", input="n\n")
    assert shown.exit_code == 1, shown.output
    assert LINE[state] in shown.stdout.splitlines(), shown.stdout


@pytest.mark.parametrize("state", STATES)
def test_the_undo_keeps_a_copy_and_a_second_undo_brings_it_back_constructed(
    root: Path, flavor: Path, state: str
) -> None:
    """What the line claims: the undo writes over the later changes, and a
    second undo, from the copy the first one kept, puts them back. The gate
    undoes a record the same way whatever its state; for `open`, the record
    on disk is the committed one the plan was built from."""
    original = (flavor / CONFIG).read_bytes()
    record = MAKE[state](root, flavor)
    later = (flavor / CONFIG).read_bytes()
    assert later != original and later.endswith(LATER)

    ok("undo", "--expect", record.id, "--yes")
    assert (flavor / CONFIG).read_bytes() == original
    first = guard.history()[-1]
    (shot,) = [p for p in first.paths if p.path == CONFIG]
    assert shot.after != shot.before  # the undo replaced the later bytes

    ok("undo", "--expect", first.id, "--yes")
    assert (flavor / CONFIG).read_bytes() == later
