"""A change whose rollback did not finish is visible (M16-71).

A most recent journal record in `rollback_incomplete` left files part-changed
as an `open` one does (M16-13): `doctor` reports it as a problem (exit 1)
with the exact `wowlab undo --expect <id>` line, and running that line puts
back what was there before the change, keeping what the other program wrote
in the undo's own pre-write snapshot.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one (constructed: no client is running). Nothing reads or writes a real
install.

Constructed (L8, boundary): the failure. A real write-gate transaction
writes `WTF/Config.wtf`, another program then rewrites that file, and the
caller's body raises: the gate leaves the other program's bytes alone and
ends the record `rollback_incomplete`, as it does in use. The journal is not
edited.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import hashlib
import json
import re
import shlex
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

from wowlab_core import cli, guard, install, snapshot

CONFIG = "WTF/Config.wtf"
CHANGED = b'SET constructed "by wowlab"\n'
OTHER = b'SET constructed "by another program"\n'


class _StopError(Exception):
    """The caller's failure that makes the gate roll back (constructed)."""


def _fail_to_roll_back(root: Path, flavor: Path) -> str:
    """A change that failed after another program rewrote the file it had
    written, so its rollback could not finish. The record's id."""
    (discovered,) = install.read_install(root).flavors
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(discovered, label="constructed: fails") as tx:
            tx.write(CONFIG, CHANGED)
            (flavor / CONFIG).write_bytes(OTHER)  # another program, mid-change
            raise _StopError
    last = guard.history()[-1]
    assert last.state == "rollback_incomplete"
    assert (flavor / CONFIG).read_bytes() == OTHER, "the gate left the other bytes alone"
    return last.id


def test_an_unfinished_rollback_is_a_problem_with_the_exact_undo_line_constructed(
    root: Path, flavor: Path
) -> None:
    record_id = _fail_to_roll_back(root, flavor)
    line = f"wowlab undo --expect {record_id}"

    text = run("doctor")
    assert text.exit_code == 1, text.output
    assert "Problems:\n" in text.stdout
    problems = " ".join(text.stdout.split("Problems:\n", 1)[1].split())
    assert "putting the files back did not finish" in problems
    assert line in problems

    data = run("doctor", "--json")
    assert data.exit_code == 1
    report = json.loads(data.stdout)
    assert report["store"]["last_transaction"]["state"] == "rollback_incomplete"
    assert [p for p in report["problems"] if line in p] == [cli._rollback_unfinished(record_id)]
    # The command is last, with nothing after the id: a copied trailing
    # character would make `--expect` refuse.
    (problem,) = [p for p in report["problems"] if line in p]
    assert problem.endswith(f": {line}")
    assert text.stdout.split("Problems:\n", 1)[1].splitlines()[0].endswith(f": {line}")


def test_the_printed_line_puts_back_what_was_there_before_the_change_constructed(
    root: Path, flavor: Path
) -> None:
    before = (flavor / CONFIG).read_bytes()
    record_id = _fail_to_roll_back(root, flavor)
    printed = next(p for p in json.loads(run("doctor", "--json").stdout)["problems"] if "undo" in p)
    found = re.search(r"wowlab undo --expect ([0-9]{8}-[0-9a-f]{8})\b", printed)
    assert found is not None, printed
    argv = shlex.split(found.group(0))
    assert argv == ["wowlab", "undo", "--expect", record_id]

    ok(*argv[1:], "--yes")

    assert (flavor / CONFIG).read_bytes() == before
    assert [r.state for r in guard.history()] == ["rollback_incomplete", "committed"]
    # What the other program wrote is held by the undo's pre-write snapshot,
    # as the problem says.
    held = snapshot.SnapshotStore(snapshot.default_store_path()).show(
        guard.history()[-1].snapshot_id
    )
    entry = next(e for e in held.entries if e.path.endswith(CONFIG))
    assert entry.sha256 == hashlib.sha256(OTHER).hexdigest()

    after = run("doctor")
    assert after.exit_code == 0, after.output
    assert "Problems:" not in after.stdout

    # "running wowlab undo again brings it back": a second undo undoes the
    # undo, and the other program's bytes are in the file again.
    ok("undo", "--yes")
    assert (flavor / CONFIG).read_bytes() == OTHER


def test_a_finished_rollback_is_not_a_problem_constructed(root: Path, flavor: Path) -> None:
    """A rollback that put every file back left nothing part-changed."""
    (discovered,) = install.read_install(root).flavors
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(discovered, label="constructed: fails cleanly") as tx:
            tx.write(CONFIG, CHANGED)
            raise _StopError
    assert guard.history()[-1].state == "rolled_back"
    result = run("doctor", "--json")
    assert result.exit_code == 0, result.output
    assert not json.loads(result.stdout)["problems"]
