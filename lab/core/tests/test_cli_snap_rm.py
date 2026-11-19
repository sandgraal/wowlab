"""`wowlab snap rm` (M19-09; docs/LAB_PLAN.md §6.9 as amended 2026-11-19,
§20.3, §20.8 decision 9; ADR-0036 term 5; ADR-0037).

The command removes snapshot manifests from wowlab's own store, under the
store lock, after a plan and a confirmation; `snap gc` then drops the
objects nothing refers to. It refuses a snapshot a journal record needs
unless `--break-undo` is given, and `undo` of such a record then refuses
with the message it gives for any snapshot it cannot read, changing
nothing.

Real fixtures (L8): the M11-03 capture copied into `tmp_path` as
`test_cli.py` builds it, with the user data directory (store, journal,
locks) redirected into `tmp_path` and a fake process table. Constructed
inputs, labelled in the test names: an edited SavedVariables file standing
for a later session, hostile snapshot labels, a manifest made unreadable by
hand, a store directory planted inside an install, and object mtimes set
back past gc's grace period. Nothing reads or writes a real install, and
the real user data directory is never touched.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_cli import (
    FLAVOR,
    _create,
    _edit_syndicator,
    _json,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_cli_player_copy import Install as PlayerCopyInstall
from test_cli_player_copy import hits

from wowlab_core import cli, guard, snapshot
from wowlab_core.snapshot import GcReport, Manifest, SnapshotStore, VerifyReport

TRUE_ON = b'"show_tooltips_on_shift"] = false'
TRUE_OFF = b'"show_tooltips_on_shift"] = true'


def _store(user_data: Path) -> SnapshotStore:
    return SnapshotStore(user_data / "store")


def _manifest_file(user_data: Path, snapshot_id: str) -> Path:
    return user_data / "store" / "manifests" / f"{snapshot_id}.json"


def _three(flavor: Path) -> tuple[Manifest, Manifest, Manifest]:
    """Three snapshots: `a` alone holds the captured Syndicator.lua; `b` and
    `c` hold an edited copy (constructed: a later session)."""
    a = _create("a")
    _edit_syndicator(flavor, TRUE_ON, TRUE_OFF)
    b = _create("b")
    c = _create("c")
    return a, b, c


def _age_objects(user_data: Path) -> None:
    """Every stored object older than gc's grace period (constructed)."""
    for path in (user_data / "store" / "objects").rglob("*"):
        if path.is_file():
            old = path.stat().st_mtime - 2 * cli.GC_GRACE_SECONDS
            os.utime(path, (old, old))


def _objects(user_data: Path) -> set[str]:
    return {
        p.parent.name + p.name
        for p in (user_data / "store" / "objects").rglob("*")
        if p.is_file() and len(p.parent.name) == 2
    }


def _restore_a_change(flavor: Path, a: Manifest) -> guard.HistoryRecord:
    """A committed change through the gate: `snap restore` of `a` over the
    edited file, which takes a pre-write snapshot first."""
    ok("snap", "restore", a.id, "--yes")
    (record,) = guard.history()
    assert record.state == "committed" and record.snapshot_id
    return record


def _player_copy_problems(root: Path, *texts: str) -> list[str]:
    said = PlayerCopyInstall.of(root)
    return [h for text in texts for h in hits(text, said)]


# ─── plan, confirm, removal ──────────────────────────────────────────────────


def test_plan_then_confirm_removes_the_manifest_and_the_rest_verifies(
    root: Path, flavor: Path, user_data: Path
) -> None:
    a, b, c = _three(flavor)
    install_before = _state(root)
    declined = run("snap", "rm", a.id, input="n\n")
    assert declined.exit_code == 1
    plan = declined.stdout
    assert f"Remove 1 snapshot(s) from wowlab's snapshot folder ({user_data / 'store'}):" in plan
    assert a.id in plan and b.id not in plan
    assert 'label: "a"' in plan
    assert "`wowlab snap gc`" in plan
    assert "Remove them?" in declined.stderr
    assert _manifest_file(user_data, a.id).is_file(), "a no removes nothing"

    done = ok("snap", "rm", a.id, "--yes")
    assert "Removed 1 snapshot(s)." in done.stdout
    assert not _manifest_file(user_data, a.id).exists()
    assert {m.id for m in _store(user_data).list()} == {b.id, c.id}
    verified = _json(VerifyReport, "snap", "verify")
    assert verified.ok and verified.manifests_checked == 2
    assert _state(root) == install_before, "the install is never touched"
    assert _player_copy_problems(root, declined.stdout, declined.stderr, done.stdout) == []


def test_gc_afterwards_drops_only_the_objects_the_removed_snapshot_alone_held(
    root: Path, flavor: Path, user_data: Path
) -> None:
    a, b, c = _three(flavor)
    store = _store(user_data)
    kept = {e.sha256 for m in (b, c) for e in m.entries if e.sha256 is not None}
    only_a = {e.sha256 for e in a.entries if e.sha256 is not None} - kept
    assert only_a, "the captured Syndicator.lua is held by `a` alone"
    before = _objects(user_data)
    ok("snap", "rm", a.id, "--yes")
    assert _objects(user_data) == before, "rm deletes no object itself"
    _age_objects(user_data)
    report = _json(GcReport, "snap", "gc", "--yes")
    assert set(report.removed) == only_a
    assert _objects(user_data) == before - only_a
    assert kept <= _objects(user_data)
    verified = store.verify()
    assert verified.ok and verified.manifests_checked == 2


def test_several_ids_in_one_call_and_an_id_given_twice(
    root: Path, flavor: Path, user_data: Path
) -> None:
    a, b, c = _three(flavor)
    report = _json(cli.SnapRmReport, "snap", "rm", a.id, b.id, a.id, a.id[:20], "--yes")
    assert [s.id for s in report.snapshots] == [a.id, b.id]
    assert report.removed == [a.id, b.id] and report.applied
    assert {m.id for m in _store(user_data).list()} == {c.id}


def test_an_unknown_id_refuses_the_whole_run(root: Path, flavor: Path, user_data: Path) -> None:
    a, b, _ = _three(flavor)
    unknown = "20990101T000000.000000Z-0badf00d"
    result = run("snap", "rm", a.id, unknown, b.id, "--yes")
    assert result.exit_code == 1
    assert "no snapshot matches" in result.stderr
    assert _manifest_file(user_data, a.id).is_file()
    assert _manifest_file(user_data, b.id).is_file()
    assert len(_store(user_data).list()) == 3


def test_without_a_store_nothing_is_created(root: Path, user_data: Path) -> None:
    result = run("snap", "rm", "latest", "--yes")
    assert result.exit_code == 1
    assert "there is no snapshot store" in result.stderr
    assert not (user_data / "store").exists()


# ─── a snapshot a journal record needs ───────────────────────────────────────


def test_a_snapshot_a_journal_record_needs_is_refused_without_break_undo(
    root: Path, flavor: Path, user_data: Path
) -> None:
    a, _, _ = _three(flavor)
    record = _restore_a_change(flavor, a)
    pre = record.snapshot_id
    for extra in ((), ("--dry-run",)):
        result = run("snap", "rm", pre, *extra, "--yes")
        assert result.exit_code == 1, extra
        text = " ".join(result.stderr.split())
        assert f"`wowlab undo` needs {pre}" in text
        assert "nothing was removed" in text
        assert "--break-undo" in text
        assert _manifest_file(user_data, pre).is_file()
    # Refused as a whole when the needed one is named among others.
    result = run("snap", "rm", a.id, pre, "--yes")
    assert result.exit_code == 1
    assert _manifest_file(user_data, a.id).is_file()


def test_break_undo_says_so_and_undo_then_refuses_with_its_existing_message(
    root: Path, flavor: Path, user_data: Path
) -> None:
    a, _, _ = _three(flavor)
    record = _restore_a_change(flavor, a)
    pre = record.snapshot_id
    dry = ok("snap", "rm", pre, "--break-undo", "--dry-run").stdout
    plan = " ".join(dry.split())
    assert (
        f"It is the copy wowlab took just before the change {record.id} "
        f'("{record.label}", {record.created_at}): `wowlab undo` of that change and of every '
        "earlier one becomes impossible." in plan
    )
    assert "Dry run: nothing was removed." in plan
    report = _json(cli.SnapRmReport, "snap", "rm", pre, "--break-undo", "--yes")
    assert report.breaks_undo and report.removed == [pre]
    (item,) = report.snapshots
    assert item.needed_by == [record.id] and item.kind == "pre-write"
    assert not _manifest_file(user_data, pre).exists()

    install_before = _state(root)
    journal_before = guard.history()
    undone = run("undo", "--yes")
    assert undone.exit_code == 3
    # The gate's message for a pre-write snapshot it cannot read, as on main
    # (guard.py is not changed by M19-09): no new recovery wording.
    message = " ".join(undone.stderr.split())
    assert f"cannot read the pre-write snapshot {pre!r}: no snapshot matches {pre!r}" in message
    assert _state(root) == install_before
    assert guard.history() == journal_before


# ─── the store lock ──────────────────────────────────────────────────────────

_HOLD = (
    "import sys\n"
    "from pathlib import Path\n"
    "from wowlab_core import guard\n"
    "with guard.store_lock(Path(sys.argv[1])):\n"
    "    print('held', flush=True)\n"
    "    sys.stdin.read()\n"
)


def test_a_store_locked_by_another_process_is_refused(
    root: Path, flavor: Path, user_data: Path, tmp_path: Path
) -> None:
    a, _, _ = _three(flavor)
    env = {k: v for k, v in os.environ.items() if not k.startswith("WOWLAB_")}
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD, str(user_data / "store")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        result = run("snap", "rm", a.id, "--yes")
        assert result.exit_code == 3
        assert "refused by the write gate" in result.stderr
        assert _manifest_file(user_data, a.id).is_file()
    finally:
        if child.stdin is not None:
            child.stdin.close()
        child.wait(timeout=60)
    ok("snap", "rm", a.id, "--yes")  # released: it runs now
    assert not _manifest_file(user_data, a.id).exists()


# ─── what it prints ──────────────────────────────────────────────────────────

RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE

HOSTILE_LABELS = [
    "<script>alert(1)</script>",
    "esc \x1b[31mred\x1b[0m",
    "csi \x9b31m",
    f"bidi {RLO}evil",
    "osc \x1b]0;title\x07",
    "--yes",
]


def test_hostile_labels_reach_the_terminal_only_escaped_constructed(
    root: Path, flavor: Path, user_data: Path
) -> None:
    made = [_create(label) for label in HOSTILE_LABELS]
    ids = [m.id for m in made]
    dry = ok("snap", "rm", *ids, "--dry-run")
    report = _json(cli.SnapRmReport, "snap", "rm", *ids, "--dry-run")
    for out in (dry.stdout, "\n".join(report.plan_lines)):
        for raw in ("\x1b", "\x9b", RLO, "\x07"):
            assert raw not in out, (raw, out)
        assert "\\x1b[31mred" in out
        assert "\\xe2\\x80\\xae" in out  # U+202E, as its UTF-8 bytes
    assert [s.label for s in report.snapshots] == HOSTILE_LABELS, "JSON keeps the label"
    assert len(_store(user_data).list()) == len(HOSTILE_LABELS)


def test_text_and_json_say_the_same(root: Path, flavor: Path, user_data: Path) -> None:
    a, b, _ = _three(flavor)
    text = ok("snap", "rm", a.id, b.id, "--dry-run").stdout.splitlines()
    report = _json(cli.SnapRmReport, "snap", "rm", a.id, b.id, "--dry-run")
    assert text == [*report.plan_lines, "Dry run: nothing was removed."]
    assert report.dry_run and not report.applied and report.removed == []
    assert [s.id for s in report.snapshots] == [a.id, b.id]
    assert report.store == str(user_data / "store")
    # A declined --json run prints the plan on stderr and the report on stdout.
    declined = run("snap", "rm", a.id, "--json", input="n\n")
    assert declined.exit_code == 1
    # The test runner echoes typed input onto stdout; a terminal does not.
    said = cli.SnapRmReport.model_validate_json(declined.stdout[declined.stdout.index("{") :])
    assert not said.applied and said.removed == []
    assert said.plan_lines[0] in declined.stderr
    assert _manifest_file(user_data, a.id).is_file()
    # And a confirmed one prints the report alone.
    done = ok("snap", "rm", a.id, "--json", "--yes")
    assert json.loads(done.stdout)["removed"] == [a.id]


# ─── the library: any store, refusals ────────────────────────────────────────


def _library_store(tmp_path: Path, root: Path, name: str) -> tuple[SnapshotStore, Manifest]:
    store = SnapshotStore(tmp_path / name)
    m = store.create(root, [f"{FLAVOR}/WTF"], label="library")
    return store, m


def test_a_store_outside_the_user_data_directory_behaves_the_same(
    root: Path, tmp_path: Path, user_data: Path
) -> None:
    store, m = _library_store(tmp_path, root, "elsewhere-store")
    other = store.create(root, [f"{FLAVOR}/WTF/Config.wtf"], label="other")
    assert store.remove(m.id) == m
    assert {x.id for x in store.list()} == {other.id}
    assert store.verify().ok
    with pytest.raises(snapshot.SnapshotNotFoundError):
        store.remove(m.id)
    assert not (user_data / "store").exists(), "the user data store is never touched"


def test_the_library_refuses_a_prefix_a_damaged_manifest_and_a_store_in_an_install_constructed(
    root: Path, tmp_path: Path
) -> None:
    store, m = _library_store(tmp_path, root, "lib-store")
    with pytest.raises(snapshot.SnapshotError):
        store.remove(m.id[:20])
    path = store.manifests_dir / f"{m.id}.json"
    good = path.read_bytes()
    path.write_bytes(good[: len(good) // 2])  # constructed: cut short
    with pytest.raises(snapshot.ManifestIntegrityError):
        store.remove(m.id)
    assert path.is_file(), "a damaged manifest is not removed"
    path.write_bytes(good)
    inside = SnapshotStore(root / FLAVOR / "WTF" / "planted-store")  # constructed
    with pytest.raises(snapshot.StoreLocationError):
        inside.remove(m.id)
    assert not inside.path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_linked_manifests_folder_is_never_followed_constructed(
    root: Path, tmp_path: Path
) -> None:
    store, m = _library_store(tmp_path, root, "link-store")
    elsewhere = tmp_path / "elsewhere-manifests"
    store.manifests_dir.rename(elsewhere)
    store.manifests_dir.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(snapshot.SnapshotError):
        store.remove(m.id)
    assert (elsewhere / f"{m.id}.json").is_file()


def test_an_imported_snapshot_loses_its_import_record(root: Path, tmp_path: Path) -> None:
    store, m = _library_store(tmp_path, root, "import-store")
    marker = store.path / "imported" / m.id
    marker.parent.mkdir()
    marker.write_bytes(b"")  # constructed: as add_snapshot records an import
    assert store.imported_ids() == {m.id}
    store.remove(m.id)
    assert not marker.exists() and store.imported_ids() == frozenset()
