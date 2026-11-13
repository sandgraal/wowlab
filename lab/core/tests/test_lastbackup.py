"""A flavor's newest backup and whether the game updated since (M17-04,
docs/LAB_PLAN.md §19 CW-3): `lastbackup`, `doctor`'s next step, and the
`backups` of `char list --summary --json`, which the UI's Account tab draws.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
as `test_cli.py` builds it, with the user data directory redirected there.
Constructed (labelled in the test ids): a client "updated" by a byte
replacement of the version cell in `.build.info`, as
`test_doctor_next_steps.py` does, and hand-built manifests for the library's
rules. Nothing here reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_cli import (
    VERSION,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_doctor_next_steps import FOLDER, _agrees, _block, _report, _set_version, _text

from wowlab_core import cli, lastbackup, snapshot

NEWER = "1.60.2.70500"
UPDATED = "Backup: the game updated since your last snapshot"


def _backup_steps() -> list[cli.NextStep]:
    return [s for s in _report().next_steps if s.item == "backup"]


def _summary() -> dict[str, object]:
    done = run("char", "list", "--summary", "--json")
    assert done.exit_code == 0, (done.stdout, done.stderr)
    report: dict[str, object] = json.loads(done.stdout)
    return report


def _snap() -> str:
    made: str = json.loads(ok("snap", "create", "--json").stdout)["id"]
    return made


# ─── doctor ──────────────────────────────────────────────────────────────────


def test_doctor_says_the_game_updated_since_the_last_snapshot_constructed(root: Path) -> None:
    _snap()
    _set_version(root, NEWER)
    [step] = _backup_steps()
    assert (step.flavor_folder, step.state) == (FOLDER, "stale")
    assert step.command == "wowlab snap create"
    assert step.detail == (
        f"your last snapshot was taken on version {VERSION}; the game is now {NEWER}"
    )
    assert step.fix == "Back up now: run `wowlab snap create`."
    report = _report()
    assert [s.item for s in report.next_steps][-1] == "backup"
    text = _text()
    assert (
        f"{UPDATED}; your last snapshot was taken on version {VERSION}; the game is now "
        f"{NEWER}. Back up now: run `wowlab snap create`."
    ) in _block(text)
    _agrees(text, report)


def test_doctor_says_nothing_once_a_snapshot_names_this_client_constructed(root: Path) -> None:
    _snap()
    _set_version(root, NEWER)
    assert _backup_steps()
    _snap()
    assert _backup_steps() == []
    assert UPDATED not in _text()


def test_doctor_says_nothing_with_no_snapshot_or_the_same_client(root: Path) -> None:
    assert _backup_steps() == []
    _snap()
    assert _backup_steps() == []


# ─── char list --summary --json: the Account tab's backup line ──────────────


def test_summary_has_no_backup_before_the_first(root: Path) -> None:
    report = _summary()
    assert report["backups"] == [
        {
            "flavor": FOLDER,
            "version": VERSION,
            "snapshot_id": None,
            "created_at": None,
            "snapshot_version": None,
            "game_updated": False,
        }
    ]
    assert report["backup_error"] is None


def test_summary_names_the_newest_backup_and_the_update_constructed(root: Path) -> None:
    _snap()
    newest = _snap()
    _set_version(root, NEWER)
    [line] = _summary()["backups"]  # type: ignore[misc]
    assert line["snapshot_id"] == newest
    assert line["snapshot_version"] == VERSION
    assert line["version"] == NEWER
    assert line["game_updated"] is True
    manifest = snapshot.SnapshotStore().show(newest)
    assert line["created_at"] == manifest.created_at


def test_summary_says_why_when_the_store_cannot_be_read_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_store: snapshot.SnapshotStore) -> object:
        raise snapshot.SnapshotError("cannot list the store")

    monkeypatch.setattr(lastbackup, "store_listing", refuse)
    report = _summary()
    assert report["backups"] == []
    assert (
        report["backup_error"] == "wowlab could not read its snapshot store: cannot list the store"
    )


# ─── the library's rules (hand-built manifests: constructed) ────────────────


def _manifest(
    at: str,
    *,
    root: Path,
    folder: str = "_a_",
    version: str | None = "1.0.0.1",
    kind: snapshot.SnapshotKind | None = "user",
    purpose: str | None = None,
    tag: str = "0",
) -> snapshot.Manifest:
    return snapshot.Manifest(
        id=f"2026{at}-{tag}",
        created_at=f"2026-{at}Z",
        install_root=str(root),
        flavor_folder=folder,
        flavor_version=version,
        subtrees=(),
        entries=(),
        kind=kind,
        purpose=purpose,  # type: ignore[arg-type]
    )


def test_the_newest_backup_of_this_install_and_flavor_constructed(tmp_path: Path) -> None:
    here = tmp_path / "wow"
    here.mkdir()
    there = tmp_path / "other"
    there.mkdir()
    old = _manifest("01-01", root=here, kind="first-run")
    pre_write = _manifest("02-01", root=here, kind="pre-write")
    others = [
        _manifest("03-01", root=here, kind="profile"),
        _manifest("03-02", root=here, kind=None, purpose="profile"),
        _manifest("03-03", root=here, tag="imp"),
        _manifest("03-04", root=here, folder="_b_"),
        _manifest("03-05", root=there),
    ]
    found = [old, pre_write, *others]
    latest = lastbackup.latest_backup(found, here, "_a_", {"202603-03-imp"})
    assert latest is pre_write
    assert lastbackup.latest_backup(others[:3], here, "_a_", {"202603-03-imp"}) is None


def test_the_game_updated_only_when_both_versions_are_known_constructed(tmp_path: Path) -> None:
    one = _manifest("01-01", root=tmp_path)
    assert lastbackup.game_updated(one, "1.0.0.2") is True
    assert lastbackup.game_updated(one, "1.0.0.1") is False
    assert lastbackup.game_updated(one, None) is False
    assert lastbackup.game_updated(None, "1.0.0.2") is False
    unknown = _manifest("01-01", root=tmp_path, version=None)
    assert lastbackup.game_updated(unknown, "1.0.0.2") is False
