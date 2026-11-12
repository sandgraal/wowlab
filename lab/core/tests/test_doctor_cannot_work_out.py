"""`doctor`'s one answer for a failure wowlab cannot work out (M16-109, ADR-0037).

- A journal that does not load, or a change `undo` would act on whose
  pre-write snapshot cannot be read (its manifest is missing), is one
  `doctor` problem, exit 1. It says wowlab cannot work out safely what the
  change did, that undo changes nothing and the snapshots are kept, names
  the newest snapshot `snap verify` finds whole, and ends with
  `wowlab snap restore <id> --dry-run`.
- With no whole snapshot left, it says so and offers no command.
- `--json` carries it as `cannot_work_out` (`cause`, `record_id`,
  `snapshot_id`, `command`); `--redact` keeps the snapshot id, which names
  nobody, through the redactor.
- `undo` refuses such a change and changes nothing.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one (constructed: no client is running). Nothing reads or writes a real
install.

Constructed (L8, boundary): every failure here. A real `addon install`
runs through the write gate; then its journal record is damaged, or turned
`open` / `rollback_incomplete` and its pre-write snapshot's manifest
deleted, or every stored object overwritten.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

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

from wowlab_core import cli, doctorredact, guard, snapshot

RESTORE = "wowlab snap restore"


def _store() -> snapshot.SnapshotStore:
    return snapshot.SnapshotStore()


def _record_path(record_id: str) -> Path:
    return snapshot.default_store_path() / "journal" / f"{record_id}.json"


def _snapshots() -> list[str]:
    return [m.id for m in _store().list_lenient().manifests]


def _install_change() -> guard.HistoryRecord:
    """A real change through the write gate, after a snapshot of its own:
    the store then holds two whole snapshots, the older one taken by
    `snap create` and the newer one the change's pre-write snapshot."""
    ok("snap", "create")
    ok("addon", "install", "lab", "--yes")
    last = guard.history()[-1]
    assert last.snapshot_id in _snapshots(), "positive control: its snapshot is stored"
    assert len(_snapshots()) == 2, "positive control: two snapshots"
    return last


def _rewrite(record_id: str, **fields: Any) -> None:
    path = _record_path(record_id)
    record = json.loads(path.read_text(encoding="ascii"))
    record.update(fields)
    path.write_text(json.dumps(record, sort_keys=True, indent=1) + "\n", encoding="ascii")


def _damage_journal(record_id: str) -> None:
    """Constructed: the record's file cut short, as a hand edit or a bad disk leaves it."""
    path = _record_path(record_id)
    path.write_bytes(path.read_bytes()[:40])
    with pytest.raises(guard.GuardError):
        guard.history()


def _drop_manifest(snapshot_id: str) -> None:
    """Constructed: the pre-write snapshot's manifest deleted from the store."""
    (_store().manifests_dir / f"{snapshot_id}.json").unlink()


def _corrupt_every_object() -> None:
    """Constructed: every stored object overwritten, so no snapshot is whole."""
    objects = [p for p in _store().objects_dir.rglob("*") if p.is_file()]
    assert objects, "positive control: the store holds objects"
    for p in objects:
        p.chmod(0o600)
        p.write_bytes(b"constructed: not what the name hashes to\n")
    assert not _store().verify().ok


def _doctor_json(*args: str) -> tuple[Any, dict[str, Any]]:
    result = run("doctor", "--json", *args)
    return result, json.loads(result.stdout)


def _problems_block(text: str) -> list[str]:
    block = text.split("Problems:\n", 1)[1]
    lines: list[str] = []
    for line in block.splitlines():
        if not line.startswith("  "):
            break  # the next block (Next steps)
        lines.append(line.strip())
    return lines


# ─── a damaged journal record ────────────────────────────────────────────────


def test_a_damaged_journal_record_is_one_problem_naming_the_newest_whole_snapshot_constructed(
    flavor: Path,
) -> None:
    last = _install_change()
    newest = _snapshots()[-1]
    _damage_journal(last.id)

    result, report = _doctor_json()
    assert result.exit_code == 1, result.output
    command = f"{RESTORE} {newest} --dry-run"
    assert report["cannot_work_out"] == {
        "cause": "journal_unreadable",
        "record_id": None,
        "snapshot_id": newest,
        "command": command,
    }
    (problem,) = report["problems"]
    assert "cannot work out safely" in problem
    assert "snapshots are kept" in problem
    assert problem.endswith(f"run: {command}"), "the command is last"
    assert "undo --expect" not in problem
    argv = shlex.split(problem.rsplit("run: ", 1)[1])
    assert argv == ["wowlab", "snap", "restore", newest, "--dry-run"]

    text = run("doctor")
    assert text.exit_code == 1, text.output
    assert _problems_block(text.stdout) == [problem]
    # The printed command plans and changes nothing.
    assert ok(*argv[1:]).exit_code == 0


def test_a_damaged_journal_record_is_refused_by_undo_constructed(flavor: Path) -> None:
    last = _install_change()
    _damage_journal(last.id)
    before = _record_path(last.id).read_bytes()
    manifests = _snapshots()
    result = run("undo", "--yes")
    assert result.exit_code != 0, result.output
    assert _record_path(last.id).read_bytes() == before
    assert _snapshots() == manifests, "undo took no snapshot and changed nothing"


# ─── a missing manifest ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state", ["open", "rollback_incomplete"], ids=["interrupted", "rollback-unfinished"]
)
def test_a_missing_pre_write_manifest_is_one_problem_naming_an_older_whole_snapshot_constructed(
    flavor: Path, state: str
) -> None:
    last = _install_change()
    older = _snapshots()[0]
    _rewrite(last.id, state=state)
    assert guard.history()[-1].state == state, "positive control: the record still loads"
    _drop_manifest(last.snapshot_id)

    result, report = _doctor_json()
    assert result.exit_code == 1, result.output
    command = f"{RESTORE} {older} --dry-run"
    assert report["cannot_work_out"] == {
        "cause": "snapshot_unreadable",
        "record_id": last.id,
        "snapshot_id": older,
        "command": command,
    }
    (problem,) = report["problems"]
    assert f"(record {last.id})" in problem
    assert "cannot work out safely" in problem
    assert "snapshots are kept" in problem
    assert "undo --expect" not in problem, "undo would refuse, so it is not offered"
    assert problem.endswith(f"run: {command}")
    assert report["store"]["damaged"] == [], "a missing manifest is not a damaged one"

    text = run("doctor")
    assert text.exit_code == 1, text.output
    assert _problems_block(text.stdout) == [problem]

    journal = _record_path(last.id).read_bytes()
    refused = run("undo", "--expect", last.id, "--yes")
    assert refused.exit_code != 0, refused.output
    assert _record_path(last.id).read_bytes() == journal
    assert [r.id for r in guard.history()] == [last.id], "undo recorded nothing"


def test_a_readable_pre_write_snapshot_keeps_the_undo_line_constructed(flavor: Path) -> None:
    """Positive control: with the manifest there, the interrupted change keeps its own answer."""
    last = _install_change()
    _rewrite(last.id, state="open")
    result, report = _doctor_json()
    assert result.exit_code == 1
    assert "cannot_work_out" not in report
    assert report["problems"] == [cli._interrupted(last.id)]


def test_a_settled_journal_has_no_such_problem(flavor: Path) -> None:
    _install_change()
    result, report = _doctor_json()
    assert result.exit_code == 0, result.output
    assert "cannot_work_out" not in report
    assert report["problems"] == []


# ─── no whole snapshot ───────────────────────────────────────────────────────


@pytest.mark.parametrize("cause", ["journal", "manifest"])
def test_with_no_whole_snapshot_the_problem_says_so_and_offers_no_command_constructed(
    flavor: Path, cause: str
) -> None:
    last = _install_change()
    if cause == "journal":
        _damage_journal(last.id)
    else:
        _rewrite(last.id, state="open")
        _drop_manifest(last.snapshot_id)
    _corrupt_every_object()

    result, report = _doctor_json()
    assert result.exit_code == 1, result.output
    unsure = report["cannot_work_out"]
    assert unsure["snapshot_id"] is None
    assert unsure["command"] is None
    (problem,) = report["problems"]
    assert "none of them is whole" in problem
    assert "run:" not in problem and RESTORE not in problem, "no command is offered"
    text = run("doctor")
    assert _problems_block(text.stdout) == [problem]


def test_a_snapshot_with_a_missing_object_is_not_offered_constructed(flavor: Path) -> None:
    """The newest snapshot loses one object: `snap verify` finds it not whole,
    so the older one is named."""
    ok("snap", "create")
    # Constructed: a file only the newer snapshot holds.
    (flavor / "WTF" / "constructed-m16-109.txt").write_bytes(b"only in the newer snapshot\n")
    ok("addon", "install", "lab", "--yes")
    last = guard.history()[-1]
    older, newest = _snapshots()
    _damage_journal(last.id)
    store = _store()
    only_newest = {e.sha256 for e in store.show(newest).entries if e.sha256} - {
        e.sha256 for e in store.show(older).entries if e.sha256
    }
    assert only_newest, "positive control: the newest snapshot holds an object of its own"
    store.object_path(sorted(only_newest)[0]).unlink()
    _, report = _doctor_json()
    assert report["cannot_work_out"]["snapshot_id"] == older


# ─── --redact ────────────────────────────────────────────────────────────────


def test_redact_covers_the_snapshot_id_constructed(flavor: Path) -> None:
    last = _install_change()
    older = _snapshots()[0]
    _rewrite(last.id, state="open")
    _drop_manifest(last.snapshot_id)

    for field in ("cause", "record_id", "snapshot_id", "command"):
        assert f"cannot_work_out.{field}" in doctorredact.DOCTOR_FIELDS, field
    assert doctorredact.DOCTOR_FIELDS["cannot_work_out.snapshot_id"] == doctorredact.REDACT

    result, report = _doctor_json("--redact")
    assert result.exit_code == 1, result.output
    # A snapshot id names nobody: redacted, it is still the id.
    assert report["cannot_work_out"]["snapshot_id"] == older
    assert report["cannot_work_out"]["cause"] == "snapshot_unreadable"
    (problem,) = report["problems"]
    assert problem.endswith(f"run: {RESTORE} {older} --dry-run")

    text = run("doctor", "--redact")
    assert text.exit_code == 1
    assert f"{RESTORE} {older} --dry-run" in text.stdout
