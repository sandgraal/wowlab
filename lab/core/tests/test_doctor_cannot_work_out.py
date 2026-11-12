"""`doctor`'s one answer for a failure wowlab cannot work out (M16-109, ADR-0037).

- A journal that does not load, or a change `undo` would act on whose
  pre-write snapshot cannot be read as undo reads it (its manifest is
  missing, or an object a replaced file needs is missing or corrupt), is
  one `doctor` problem, exit 1. It says wowlab cannot work out safely what
  the change did, that undo changes nothing and the snapshots are kept,
  names the newest snapshot `snap verify` finds whole of the install doctor
  checked (of the change's flavor first), and ends with
  `wowlab snap restore <id> --dry-run`, plus the `--root` doctor was given;
  the command runs as printed.
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
import shutil
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    CAPTURE,
    FLAVOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, doctorredact, guard, install, snapshot

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


# ─── a missing or corrupt pre-write object ──────────────────────────────────


def _replace_config(flavor: Path) -> guard.HistoryRecord:
    """A real change through the write gate that replaces a file, so `undo`
    needs the object of its pre-write copy: `snap restore --paths` puts
    `WTF/Config.wtf` back after a constructed edit. Two snapshots result:
    the one `snap create` took, then the change's pre-write snapshot."""
    ok("snap", "create")
    cfg = flavor / "WTF" / "Config.wtf"
    cfg.write_bytes(cfg.read_bytes() + b'SET constructedM16109 "1"\n')
    ok("snap", "restore", _snapshots()[0], "--paths", "WTF/Config.wtf", "--yes")
    last = guard.history()[-1]
    assert [p.before for p in last.paths if p.before], "positive control: a file was replaced"
    assert len(_snapshots()) == 2, "positive control: two snapshots"
    return last


@pytest.mark.parametrize(
    "state", ["open", "rollback_incomplete"], ids=["interrupted", "rollback-unfinished"]
)
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_a_lost_pre_write_object_is_the_generic_answer_constructed(
    flavor: Path, state: str, damage: str
) -> None:
    """The manifest loads, but `undo` also reads every object the journal's
    `before` names and refuses when one is missing or does not re-hash."""
    last = _replace_config(flavor)
    older = _snapshots()[0]
    _rewrite(last.id, state=state)
    store = _store()
    store.show(last.snapshot_id)  # positive control: the manifest loads
    for change in last.paths:
        if change.before is None:
            continue
        obj = store.object_path(change.before)
        if damage == "missing":
            obj.unlink()
        else:
            obj.chmod(0o600)
            obj.write_bytes(b"constructed: not what the name hashes to\n")

    result, report = _doctor_json()
    assert result.exit_code == 1, result.output
    unsure = report["cannot_work_out"]
    assert unsure["cause"] == "snapshot_unreadable"
    assert unsure["record_id"] == last.id
    (problem,) = report["problems"]
    assert "undo --expect" not in problem
    assert "cannot work out safely" in problem
    if unsure["snapshot_id"] is not None:
        # The older snapshot shares the object, so it is whole only when the
        # damaged object is the change's alone.
        assert unsure["snapshot_id"] == older
        assert ok(*shlex.split(unsure["command"])[1:]).exit_code == 0

    journal = _record_path(last.id).read_bytes()
    refused = run("undo", "--expect", last.id, "--yes")
    assert refused.exit_code != 0, "positive control: undo refuses this change"
    assert _record_path(last.id).read_bytes() == journal


def test_a_whole_pre_write_object_keeps_the_undo_line_constructed(flavor: Path) -> None:
    """Positive control for the object check: nothing lost, the usual answer."""
    last = _replace_config(flavor)
    _rewrite(last.id, state="open")
    result, report = _doctor_json()
    assert result.exit_code == 1
    assert "cannot_work_out" not in report
    assert report["problems"] == [cli._interrupted(last.id)]


# ─── the offered command runs as printed ─────────────────────────────────────


def _second_install(tmp_path: Path) -> Path:
    """Constructed: another copy of the captured tree, a second install."""
    other = tmp_path / "Second WoW"
    other.mkdir()
    shutil.copy2(CAPTURE / ".build.info", other / ".build.info")
    shutil.copytree(CAPTURE / "forever", other / FLAVOR)
    return other


def test_a_newer_snapshot_of_another_install_is_not_offered_constructed(
    flavor: Path, tmp_path: Path
) -> None:
    last = _install_change()
    own = _snapshots()[-1]
    other = _second_install(tmp_path)
    ok("snap", "create", "--root", str(other))
    foreign = _snapshots()[-1]
    assert foreign != own, "positive control: the newest snapshot is the other install's"
    _damage_journal(last.id)

    result, report = _doctor_json()
    assert result.exit_code == 1, result.output
    assert report["cannot_work_out"]["snapshot_id"] == own
    argv = shlex.split(report["cannot_work_out"]["command"])
    assert argv == ["wowlab", "snap", "restore", own, "--dry-run"]
    assert ok(*argv[1:]).exit_code == 0
    # Positive control: the other install's snapshot is refused here.
    assert run("snap", "restore", foreign, "--dry-run").exit_code != 0


def test_only_another_installs_snapshots_offer_nothing_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the store holding only the other install's snapshots, doctor
    has none of this install to offer, and says so."""
    other = _second_install(tmp_path)
    monkeypatch.setenv(install.ENV_ROOT, str(other))
    last = _install_change()
    _damage_journal(last.id)
    result, report = _doctor_json("--root", str(root))
    assert result.exit_code == 1, result.output
    assert report["cannot_work_out"]["snapshot_id"] is None
    assert report["cannot_work_out"]["command"] is None
    (problem,) = report["problems"]
    assert "none of them is whole" in problem


def test_the_command_carries_the_root_doctor_was_given_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Constructed: a space in the root, so the quoting shows.
    spaced = tmp_path / "W o W"
    shutil.copytree(root, spaced)
    monkeypatch.setenv(install.ENV_ROOT, str(spaced))
    last = _install_change()
    own = _snapshots()[-1]
    _damage_journal(last.id)
    monkeypatch.setenv(install.ENV_ROOT, str(_second_install(tmp_path)))

    result, report = _doctor_json("--root", str(spaced))
    assert result.exit_code == 1, result.output
    command = report["cannot_work_out"]["command"]
    assert command == shlex.join(
        ["wowlab", "snap", "restore", own, "--dry-run", "--root", str(spaced)]
    )
    assert report["problems"][-1].endswith(f"run: {command}")
    assert ok(*shlex.split(command)[1:]).exit_code == 0
    # Without --root, the default install is found: no --root in the command.
    _, plain = _doctor_json()
    assert plain["cannot_work_out"]["command"] is None, "the default install has no snapshot"


def test_the_change_flavor_is_preferred_and_named_constructed(root: Path, flavor: Path) -> None:
    """On an install with several flavors, the change's own flavor's newest
    whole snapshot is offered over a newer one of another flavor. The
    command names no --flavor: `snap restore` takes it from the snapshot."""
    second = root / "_constructed_"
    shutil.copytree(flavor, second)
    ok("snap", "create", "--flavor", FLAVOR)
    ok("addon", "install", "lab", "--yes", "--flavor", FLAVOR)
    last = guard.history()[-1]
    own = _snapshots()[0]
    ok("snap", "create", "--flavor", "_constructed_")
    assert _snapshots()[-1] not in (own, last.snapshot_id)
    _rewrite(last.id, state="open")
    _drop_manifest(last.snapshot_id)

    result, report = _doctor_json()
    assert result.exit_code == 1, result.output
    assert report["cannot_work_out"]["snapshot_id"] == own
    argv = shlex.split(report["cannot_work_out"]["command"])
    assert argv == ["wowlab", "snap", "restore", own, "--dry-run"]
    assert ok(*argv[1:]).exit_code == 0


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
