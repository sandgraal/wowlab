# Probe from review of m16/84-left-alone-paths; reproduces `doctor` saying "undo leaves this record as it is" (and offering no command) for a record with no paths but a created folder, which `wowlab undo --expect <id> --yes` does undo and record.
"""For a `rollback_incomplete` record whose `paths` is empty, `doctor`
(`cli._rollback_unfinished`, `paths == 0`) says the change wrote nothing,
"undo leaves this record as it is", and offers no command. `wowlab undo`
returns early only when a record has no paths *and* no `created_dirs`
(`cli.py`, `if not last.paths and not last.created_dirs`). A create into a
folder the change made, refused at the re-check because another program
wrote the target after guard's temp file was fsynced, leaves a record with
no paths, one created folder (its removal fails: the other program's file
is in it) and the refused path in `paths_left`. For that record `undo`
runs, exits 0 and journals a committed "undo: ..." transaction, so the
problem's sentence is false and the player is told there is nothing to run.

Positive control: the same refused create into a folder that already
exists (no created folder). There `undo` records nothing, as `doctor`
says.

Constructed (L8, hostile/boundary): a tree under `tmp_path`, the user data
directory redirected by `test_cli`'s autouse fixture, the race made by
wrapping `os.fsync`. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    ACCOUNT,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    replayed,  # noqa: F401
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import guard, install

TEMP = re.compile(r"\.wowlab-[0-9a-f]{32}\.tmp")
LEAVES = "undo leaves this record as it is"


def _refused_create(root: Path, flavor: Path, rel: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """A write of the new file `rel`, refused at the re-check because another
    program created it right after guard's temp file was fsynced."""
    target = flavor / rel
    real_fsync = os.fsync
    real_fdatasync = getattr(os, "fdatasync", None)
    fired: list[bool] = []

    def wrap(real: Any) -> Any:
        def call(fd: Any) -> None:
            real(fd)
            if fired or not target.parent.is_dir():
                return
            if any(TEMP.fullmatch(p.name) for p in target.parent.iterdir()):
                fired.append(True)
                target.write_bytes(b"constructed: another program\n")

        return call

    (discovered,) = install.read_install(root).flavors
    monkeypatch.setattr(os, "fsync", wrap(real_fsync))
    if real_fdatasync is not None:
        monkeypatch.setattr(os, "fdatasync", wrap(real_fdatasync))
    with (
        pytest.raises(guard.GuardError),
        guard.transaction(discovered, label="constructed: refused create") as tx,
    ):
        tx.write(rel, b"constructed: wowlab\n")
    monkeypatch.setattr(os, "fsync", real_fsync)
    if real_fdatasync is not None:
        monkeypatch.setattr(os, "fdatasync", real_fdatasync)
    assert fired, "the race did not run"
    last = guard.history()[-1]
    assert last.state == "rollback_incomplete"
    assert last.paths == ()
    assert last.paths_left == (rel,)
    return last.id


def _check(record_id: str) -> None:
    text = run("doctor")
    assert text.exit_code == 1, text.output
    problems = " ".join(text.stdout.split("Problems:\n", 1)[1].split())
    assert f"(record {record_id})" in problems
    said_leaves = LEAVES in problems
    before = [r.id for r in guard.history()]
    undone = run("undo", "--expect", record_id, "--yes")
    after = [r.id for r in guard.history()]
    recorded = after != before
    assert not (said_leaves and recorded), (
        f"doctor said {LEAVES!r} with no command, but undo exited "
        f"{undone.exit_code} and journaled {after[len(before) :]}"
    )


def test_constructed_positive_control_existing_folder(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = f"WTF/Account/{ACCOUNT}/SavedVariables/ConstructedNew.lua"
    assert (flavor / rel).parent.is_dir()
    record_id = _refused_create(root, flavor, rel, monkeypatch)
    assert guard.history()[-1].created_dirs == ()
    _check(record_id)


def test_constructed_a_created_folder_makes_undo_record(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = f"WTF/Account/{ACCOUNT}/ConstructedFolder/ConstructedNew.lua"
    assert not (flavor / rel).parent.exists()
    record_id = _refused_create(root, flavor, rel, monkeypatch)
    assert guard.history()[-1].created_dirs != ()
    _check(record_id)
