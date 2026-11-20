"""`wowlab snap rm` of a damaged snapshot, by its full id (M19-12;
docs/LAB_PLAN.md §6.9 as amended 2026-11-19; ADR-0036 term 5; ADR-0037).

A snapshot whose manifest cannot be read is removed only when the id typed
is its complete id, and only with `--break-undo`; the plan says it is
damaged and that wowlab cannot tell what it held or whether a change needs
it. `snap gc` still refuses while a manifest is damaged, in the player's
words, and runs once the damaged one is gone. Nothing the damaged file holds
is ever printed: a marker string is planted in it and searched for.

Real fixtures (L8): the M11-03 capture copied into `tmp_path` as
`test_cli.py` builds it, with the user data directory redirected into
`tmp_path` and a fake process table. Constructed inputs, labelled in the
test names: the damaged manifests (cut short, wrong types, empty, another
format number), a link standing in a manifest's place, an edited
SavedVariables file standing for a later session, and object mtimes set back
past gc's grace period. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from test_cli import (
    FLAVOR,
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
from test_cli_snap_rm import (
    _age_objects,
    _manifest_file,
    _objects,
    _player_copy_problems,
    _restore_a_change,
    _store,
    _three,
)

from wowlab_core import cli, guard, snapshot
from wowlab_core.snapshot import GcReport, Manifest, SnapshotStore, VerifyReport

MARKER = "PLANTED-M19-12-MARKER-7f3a"

# What a loader's message would carry; none of it reaches the player.
LOADER_WORDS = ("validation", "pydantic", "Manifest", "json_invalid", "line 1", "input_value")


def _cut_short(good: bytes) -> bytes:
    data = json.loads(good)
    data["label"] = MARKER
    text = json.dumps(data).encode()
    return text[: text.index(MARKER.encode()) + len(MARKER) + 2]


def _wrong_types(good: bytes) -> bytes:
    data = json.loads(good)
    data["entries"] = MARKER
    data["created_at"] = [MARKER]
    return json.dumps(data).encode()


def _empty(good: bytes) -> bytes:
    return b""


def _other_format(good: bytes) -> bytes:
    data = json.loads(good)
    data["format"] = data["format"] + 1
    data["label"] = MARKER
    return json.dumps(data).encode()


DAMAGE: dict[str, Callable[[bytes], bytes]] = {
    "cut-short": _cut_short,
    "wrong-types": _wrong_types,
    "empty": _empty,
    "other-format": _other_format,
}


def _damage(user_data: Path, snapshot_id: str, how: Callable[[bytes], bytes]) -> bytes:
    path = _manifest_file(user_data, snapshot_id)
    bad = how(path.read_bytes())
    path.write_bytes(bad)
    with pytest.raises(snapshot.ManifestIntegrityError):
        _store(user_data).show(snapshot_id)
    return bad


def _said(*results: object) -> str:
    return "\n".join(
        getattr(r, part) for r in results for part in ("stdout", "stderr") if getattr(r, part)
    )


def _quotes_nothing(text: str) -> None:
    assert MARKER not in text, text
    for raw in LOADER_WORDS:
        assert raw not in text, (raw, text)


# ─── removed by full id, with --break-undo ───────────────────────────────────


@pytest.mark.parametrize("how", list(DAMAGE), ids=[f"constructed-{k}" for k in DAMAGE])
def test_a_damaged_snapshot_is_removed_by_full_id_with_break_undo_and_gc_runs_after(
    root: Path, flavor: Path, user_data: Path, how: str
) -> None:
    a, b, c = _three(flavor)
    kept = {e.sha256 for m in (b, c) for e in m.entries if e.sha256 is not None}
    only_a = {e.sha256 for e in a.entries if e.sha256 is not None} - kept
    assert only_a
    install_before = _state(root)
    bad = _damage(user_data, a.id, DAMAGE[how])
    path = _manifest_file(user_data, a.id)

    # Refused without --break-undo, as text and as --json.
    refused = run("snap", "rm", a.id, "--yes")
    refused_json = run("snap", "rm", a.id, "--yes", "--json")
    for result in (refused, refused_json):
        assert result.exit_code == 1
    text = " ".join(refused.stderr.split())
    assert (
        f"{a.id} is damaged: its record in the snapshot folder cannot be read, so wowlab "
        "cannot tell what it held or whether a change of yours needs it. Nothing was "
        "removed. Add --break-undo to remove it anyway." in text
    )
    assert " ".join(json.loads(refused_json.stdout)["message"].split()) in text
    # Refused by a start or end of the id, and as `latest`, even with --break-undo.
    for ref in (a.id[:20], a.id[-8:], "latest", "~2"):
        result = run("snap", "rm", ref, "--break-undo", "--yes")
        assert result.exit_code == 1, ref
        said = " ".join(result.stderr.split())
        assert "only when you give its full id, with --break-undo" in said, ref
        assert "`wowlab snap verify` names it." in said
        _quotes_nothing(_said(result))
    assert path.read_bytes() == bad, "nothing was removed"

    # snap gc refuses in the player's words while it is there.
    gc_refused = run("snap", "gc", "--yes")
    gc_refused_json = run("snap", "gc", "--yes", "--json")
    assert gc_refused.exit_code == gc_refused_json.exit_code == 1
    assert " ".join(cli._GC_DAMAGED.split()) in " ".join(gc_refused.stderr.split())
    assert json.loads(gc_refused_json.stdout)["message"] == cli._GC_DAMAGED

    # The plan, text and --json alike.
    dry = ok("snap", "rm", a.id, "--break-undo", "--dry-run")
    report = _json(cli.SnapRmReport, "snap", "rm", a.id, "--break-undo", "--dry-run")
    assert dry.stdout.splitlines() == [*report.plan_lines, "Dry run: nothing was removed."]
    plan = " ".join(dry.stdout.split())
    assert (
        f"{a.id} is damaged: its record in the snapshot folder cannot be read, so wowlab "
        "cannot tell what it held or whether a change of yours needs it. It is removed only "
        "with --break-undo." in plan
    )
    assert (
        "wowlab's change history does not name it, but with its record unreadable wowlab "
        "cannot tell whether one of your changes needed it." in plan
    )
    (item,) = report.snapshots
    assert item.damaged and item.id == a.id and item.needed_by == []
    assert item.created_at is None and item.kind is None and item.files is None
    assert not report.journal_named and not report.breaks_undo
    assert path.read_bytes() == bad

    done = ok("snap", "rm", a.id, "--break-undo", "--yes")
    assert "Removed 1 snapshot(s)." in done.stdout
    assert not path.exists()
    for out in (refused, refused_json, gc_refused, gc_refused_json, dry, done):
        _quotes_nothing(_said(out))
    assert _player_copy_problems(root, _said(refused, gc_refused, dry, done)) == []

    verified = _json(VerifyReport, "snap", "verify")
    assert verified.ok and verified.manifests_checked == 2
    before = _objects(user_data)
    _age_objects(user_data)
    gc = _json(GcReport, "snap", "gc", "--yes")
    assert set(gc.removed) == only_a, "only what the removed snapshot alone held"
    assert _objects(user_data) == before - only_a
    assert kept <= _objects(user_data)
    assert _store(user_data).verify().ok
    assert _state(root) == install_before, "the install is never touched"


def test_a_damaged_and_a_healthy_snapshot_together_constructed(
    root: Path, flavor: Path, user_data: Path
) -> None:
    a, b, c = _three(flavor)
    _damage(user_data, a.id, _cut_short)
    # Without --break-undo the whole run is refused.
    refused = run("snap", "rm", b.id, a.id, "--yes")
    assert refused.exit_code == 1 and "Add --break-undo" in refused.stderr
    # A damaged one by a prefix refuses the healthy one with it.
    refused = run("snap", "rm", b.id, a.id[:20], "--break-undo", "--yes")
    assert refused.exit_code == 1
    assert _manifest_file(user_data, a.id).is_file()
    assert _manifest_file(user_data, b.id).is_file()
    # The healthy one may be a prefix; the damaged one is complete.
    report = _json(cli.SnapRmReport, "snap", "rm", b.id[:20], a.id, a.id, "--break-undo", "--yes")
    assert [s.id for s in report.snapshots] == [b.id, a.id]
    assert [s.damaged for s in report.snapshots] == [False, True]
    assert report.removed == [b.id, a.id] and report.applied
    assert {m.id for m in _store(user_data).list()} == {c.id}
    assert _json(VerifyReport, "snap", "verify").ok


# ─── refused when a link stands in its place ─────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("target", ["garbage", "dangling", "good"])
def test_a_link_in_a_manifests_place_is_refused_constructed(
    root: Path, flavor: Path, user_data: Path, tmp_path: Path, target: str
) -> None:
    a, b, _ = _three(flavor)
    path = _manifest_file(user_data, a.id)
    elsewhere = tmp_path / "elsewhere.json"
    if target == "garbage":
        elsewhere.write_text(MARKER, encoding="utf-8")
    elif target == "good":
        elsewhere.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(elsewhere)
    result = run("snap", "rm", a.id, "--break-undo", "--yes")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    text = " ".join(result.stderr.split())
    said = (
        "something other than a snapshot record (a link, or not a regular file) stands in "
        f"the place of the record of {a.id} in wowlab's snapshot folder "
        f"({user_data / 'store'}). wowlab leaves it alone and never removes it; you can "
        "remove it by hand."
    )
    assert f"{said} Nothing was removed." in text
    # snap gc says the same of it, and does not send the player to snap rm.
    gc = run("snap", "gc", "--yes")
    gc_json = run("snap", "gc", "--yes", "--json")
    assert gc.exit_code == gc_json.exit_code == 1
    gc_text = " ".join(gc.stderr.split())
    assert (
        f"{said} snap gc removes nothing while it is there, since wowlab cannot tell which "
        "stored files that snapshot needs." in gc_text
    )
    assert "snap rm" not in gc_text
    assert " ".join(json.loads(gc_json.stdout)["message"].split()) in gc_text
    _quotes_nothing(_said(result, gc, gc_json))
    assert _player_copy_problems(root, result.stderr, gc.stderr) == []
    assert path.is_symlink()
    if target != "dangling":
        assert elsewhere.is_file()
    # The library refuses it too.
    store = _store(user_data)
    with pytest.raises(snapshot.ManifestIntegrityError):
        store.remove_damaged(a.id)
    assert path.is_symlink()
    assert _manifest_file(user_data, b.id).is_file()


# ─── a damaged snapshot a journal record names ───────────────────────────────


def test_a_damaged_pre_write_snapshot_names_the_change_and_undo_refuses_after(
    root: Path, flavor: Path, user_data: Path
) -> None:
    a, _, _ = _three(flavor)
    record = _restore_a_change(flavor, a)
    pre = record.snapshot_id
    assert pre is not None
    _damage(user_data, pre, _wrong_types)
    refused = run("snap", "rm", pre, "--yes")
    assert refused.exit_code == 1
    assert (
        f"{pre} is damaged: its record in the snapshot folder cannot be read, so wowlab "
        "cannot tell what it held or whether a change of yours needs it; it is the copy "
        f'wowlab took just before your change "{record.label}" ({record.created_at}). '
        "Nothing was removed. Add --break-undo to remove it anyway."
        in " ".join(refused.stderr.split())
    )
    dry = ok("snap", "rm", pre, "--break-undo", "--dry-run").stdout
    plan = " ".join(dry.split())
    assert "cannot tell what it held or whether a change of yours needs it" in plan
    assert (
        "It is the copy wowlab took just before your most recent change "
        f'{record.id} ("{record.label}", {record.created_at}): `wowlab undo` will no longer '
        "be able to reverse it." in plan
    )
    assert "change history does not name it" not in plan
    report = _json(cli.SnapRmReport, "snap", "rm", pre, "--break-undo", "--yes")
    (item,) = report.snapshots
    assert item.damaged and item.needed_by == [record.id] and item.undo_reverses
    assert report.journal_named and report.breaks_undo and report.removed == [pre]
    assert not _manifest_file(user_data, pre).exists()
    for out in (refused.stderr, dry):
        _quotes_nothing(out)
    assert _player_copy_problems(root, refused.stderr, dry) == []

    install_before = _state(root)
    journal_before = guard.history()
    undone = run("undo", "--yes")
    assert undone.exit_code == 3
    # main's message for a pre-write snapshot it cannot read: no new wording.
    message = " ".join(undone.stderr.split())
    assert f"cannot read the pre-write snapshot {pre!r}: no snapshot matches {pre!r}" in message
    assert _state(root) == install_before
    assert guard.history() == journal_before


# ─── the library ─────────────────────────────────────────────────────────────


def _library(tmp_path: Path, root: Path) -> tuple[SnapshotStore, Manifest]:
    store = SnapshotStore(tmp_path / "lib-damaged-store")
    return store, store.create(root, [f"{FLAVOR}/WTF"], label="library")


def test_the_library_removes_only_a_damaged_manifest_by_full_id_constructed(
    root: Path, tmp_path: Path
) -> None:
    store, m = _library(tmp_path, root)
    other = store.create(root, [f"{FLAVOR}/WTF/Config.wtf"], label="other")
    path = store.manifests_dir / f"{m.id}.json"
    with pytest.raises(snapshot.SnapshotError, match="loads"):
        store.remove_damaged(m.id)  # healthy: `remove` is for it
    with pytest.raises(snapshot.SnapshotError):
        store.check_damaged(m.id)
    assert path.is_file()
    path.write_bytes(_cut_short(path.read_bytes()))
    for ref in (m.id[:20], "latest", ""):
        with pytest.raises(snapshot.SnapshotError, match="not a snapshot id"):
            store.remove_damaged(ref)
    store.check_damaged(m.id)  # reads only
    assert path.is_file()
    marker = store.path / "imported" / m.id
    marker.parent.mkdir()
    marker.write_bytes(b"")  # constructed: as add_snapshot records an import
    store.remove_damaged(m.id)
    assert not path.exists() and not marker.exists()
    with pytest.raises(snapshot.SnapshotNotFoundError):
        store.remove_damaged(m.id)
    assert {x.id for x in store.list()} == {other.id}
    assert store.verify().ok
    store.gc(dry_run=False)  # runs again


def test_the_library_gc_raises_its_own_error_for_a_damaged_manifest_constructed(
    root: Path, tmp_path: Path
) -> None:
    store, m = _library(tmp_path, root)
    path = store.manifests_dir / f"{m.id}.json"
    path.write_bytes(b"")
    with pytest.raises(snapshot.DamagedManifestError):
        store.gc(dry_run=True)
    assert issubclass(snapshot.DamagedManifestError, snapshot.ManifestIntegrityError)


def test_a_store_inside_an_install_is_refused_constructed(root: Path, tmp_path: Path) -> None:
    _, m = _library(tmp_path, root)
    inside = SnapshotStore(root / FLAVOR / "WTF" / "planted-store")  # constructed
    with pytest.raises(snapshot.StoreLocationError):
        inside.remove_damaged(m.id)
    assert not inside.path.exists()
