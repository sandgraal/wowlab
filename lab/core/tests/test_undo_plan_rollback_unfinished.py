"""`undo`'s plan for a change whose rollback did not finish (M16-86).

The plan lists only the steps the undo takes: a file the rollback already put
back, or already removed, holds what was there before the change, so the undo
leaves it alone and the plan does not name it. A file another program changed
while the change ran is said to be that program's doing, not the client's at
logout. The header says the record's state in words, never by its name, and
`--json` keeps its fields as they were.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one (constructed: no client is running). Nothing reads or writes a real
install.

Constructed (L8, boundary): the failure. A real write-gate transaction
rewrites `WTF/Config.wtf` and `Syndicator.lua` and creates a new
SavedVariables file; another program then rewrites `WTF/Config.wtf`, and the
caller's body raises. The gate puts `Syndicator.lua` back, removes the new
file, leaves the other program's bytes alone, and ends the record
`rollback_incomplete`, as it does in use. The journal is not edited.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

from pathlib import Path

import pytest
from test_cli import (
    SV_DIR,
    SYNDICATOR,
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
CREATED = f"{SV_DIR}/Constructed.lua"
CHANGED = b'SET constructed "by wowlab"\n'
USE_TARGET_FPS = (b'SET useTargetFPS "0"', b'SET useTargetFPS "1"')


class _StopError(Exception):
    """The caller's failure that makes the gate roll back (constructed)."""


def _fail_to_roll_back(root: Path, flavor: Path) -> guard.HistoryRecord:
    (discovered,) = install.read_install(root).flavors
    original = (flavor / CONFIG).read_bytes()
    assert USE_TARGET_FPS[0] in original
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(discovered, label="constructed: fails") as tx:
            tx.write(CONFIG, CHANGED)
            tx.write(SYNDICATOR, b"Syndicator = {}\n")
            tx.write(CREATED, b"Constructed = {}\n")
            # Another program, mid-change: the captured file with one CVar changed.
            (flavor / CONFIG).write_bytes(original.replace(*USE_TARGET_FPS, 1))
            raise _StopError
    last = guard.history()[-1]
    assert last.state == "rollback_incomplete"
    assert [p.path for p in last.paths] == [CONFIG, SYNDICATOR, CREATED]
    assert not (flavor / CREATED).exists(), "the rollback removed the new file"
    return last


def _plan(record: guard.HistoryRecord, flavor: Path) -> list[str]:
    """The plan for `record`: the undo puts back the other program's
    `useTargetFPS` and leaves the two files the rollback finished alone."""
    return [
        f'Undo {record.id} ("constructed: fails", {record.created_at}; '
        "failed, and putting the files back did not finish)",
        f"  in {flavor}:",
        f"  put back   {CONFIG}  (as it was before that transaction)",
        "      (changed by another program while that change ran, or since: the lines below "
        "compare what is on disk now with what undo puts back, and include that program's "
        "changes)",
        '      CVar useTargetFPS: "1" -> "0"',
        "  Each file is one scope (machine, account or character): key bindings come from "
        'the account or the character file, as "Character Specific Key Bindings" sets; '
        "account and character macros are two separate sets; each CVar is read from the "
        "file of its own scope.",
        "The client reads these files at its next login; with server sync on (see wowlab "
        "doctor), the server may replace binds, macros, settings and other *-cache files "
        "then.",
    ]


def test_the_plan_lists_only_what_the_undo_changes_and_why_constructed(
    root: Path, flavor: Path
) -> None:
    record = _fail_to_roll_back(root, flavor)
    expected = _plan(record, flavor)
    assert cli._undo_lines(record) == expected

    shown = run("undo", input="n\n")
    assert shown.exit_code == 1, shown.output
    assert shown.stdout.startswith("\n".join(expected) + "\n"), shown.stdout
    # Neither the state's name nor the logout guess is printed, and neither
    # file the rollback finished is named.
    for absent in ("rollback_incomplete", "at logout", SYNDICATOR, CREATED, "delete"):
        assert absent not in shown.stdout, absent


def test_the_json_report_keeps_its_fields_constructed(root: Path, flavor: Path) -> None:
    """`--json` is an interface: the state by name, every journaled path in
    `plan` (what the record holds, skipped or not), the text lines as shown."""
    record = _fail_to_roll_back(root, flavor)
    result = run("undo", "--json", input="n\n")
    # The declined prompt's echoed answer comes before the report.
    report = cli.UndoReport.model_validate_json(result.stdout[result.stdout.index("{") :])
    assert report.undone == record.id
    assert report.state == "rollback_incomplete"
    assert report.created_at == record.created_at
    assert report.plan == list(record.paths)
    assert [p.path for p in report.plan] == [CONFIG, SYNDICATOR, CREATED]
    assert report.created_dirs == []
    assert report.applied is False
    assert report.transaction is None
    assert report.plan_lines == _plan(record, flavor)


def test_the_undo_takes_exactly_the_listed_step_constructed(root: Path, flavor: Path) -> None:
    before = {rel: (flavor / rel).read_bytes() for rel in (CONFIG, SYNDICATOR)}
    record = _fail_to_roll_back(root, flavor)
    ok("undo", "--expect", record.id, "--yes")
    assert {rel: (flavor / rel).read_bytes() for rel in (CONFIG, SYNDICATOR)} == before
    assert not (flavor / CREATED).exists()


def test_a_plan_that_changes_nothing_says_so_constructed(root: Path, flavor: Path) -> None:
    """A rollback that finished left every file as it was before: the undo
    changes no file, and the plan says that instead of listing steps."""
    (discovered,) = install.read_install(root).flavors
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(discovered, label="constructed: fails cleanly") as tx:
            tx.write(SYNDICATOR, b"Syndicator = {}\n")
            tx.write(CREATED, b"Constructed = {}\n")
            raise _StopError
    record = guard.history()[-1]
    assert record.state == "rolled_back"
    assert cli._undo_lines(record) == [
        f'Undo {record.id} ("constructed: fails cleanly", {record.created_at}; '
        "failed, and its files were put back)",
        f"  in {flavor}:",
        "  every file already holds what was there before that transaction, so the undo "
        "changes no file",
    ]
