"""Snapshot kinds, the restore hint and short references (M16-09).

`docs/LAB_PLAN.md` §18.1 item 3: each manifest records a `kind`; one written
before kinds existed reads as `user`, except one a journal record names,
which reads as `pre-write`. `explain` (and the Explorer, which shows its
words and hints verbatim) compares against the newest `user`, `first-run`
or `profile` snapshot, and says in words what restoring a pre-write copy
means. `latest`, `latest-user`, `~N` and a unique id suffix resolve in
`snap show`, `snap diff` and `snap restore`.

The install is the captured tree as `test_cli.py` builds it (copied into
`tmp_path`, the user data directory redirected, the process table a fake).
Labels, file edits and the old manifests rewritten without a `kind` are
constructed (named so in the test ids, L8). Nothing touches a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    FLAVOR,
    _json,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, firstrun, guard, install, layout, profiles, snapshot
from wowlab_core.snapshot import SnapshotStore, kind_of, manifest_bytes, resolve_reference

CONFIG = "WTF/Config.wtf"


def _store(user_data: Path) -> SnapshotStore:
    return SnapshotStore(user_data / "store")


def _create(*args: str) -> str:
    out = json.loads(ok("snap", "create", *args, "--json").stdout)
    snapshot_id: str = out["id"]
    return snapshot_id


def _strip_kind(store: SnapshotStore, snapshot_id: str) -> Path:
    """Rewrite one manifest as it was written before kinds existed (no
    `kind` key). The fingerprint does not cover `kind`, so it still loads."""
    manifest = store.show(snapshot_id)
    path = store.manifests_dir / f"{snapshot_id}.json"
    path.write_bytes(manifest_bytes(manifest.model_copy(update={"kind": None})))
    assert "kind" not in json.loads(path.read_bytes())
    assert store.show(snapshot_id).kind is None
    return path


def _restore_yes(snapshot_id: str) -> cli.RestoreReport:
    done = _json(cli.RestoreReport, "snap", "restore", snapshot_id, "--yes")
    assert done.applied and done.transaction is not None
    return done


def _pre_write_of(user_data: Path, record_id: str) -> str:
    record = next(r for r in guard.history(store=_store(user_data).path) if r.id == record_id)
    snapshot_id: str = record.snapshot_id
    return snapshot_id


# ─── old manifests: what they read as ────────────────────────────────────────


def test_constructed_old_pre_write_manifest_reads_as_pre_write_by_its_journal_record(
    root: Path, flavor: Path, user_data: Path
) -> None:
    taken = _create("-m", "constructed")
    (flavor / CONFIG).write_bytes(b'SET constructed "wiped"\n')
    done = _restore_yes(taken)
    pre = _pre_write_of(user_data, done.transaction or "")
    store = _store(user_data)
    _strip_kind(store, pre)
    _strip_kind(store, taken)

    journal = cli._pre_write_ids(store)
    assert pre in journal
    assert kind_of(store.show(pre), journal) == "pre-write"
    assert kind_of(store.show(taken), journal) == "user"
    # Without the journal, an old manifest is a user one, whatever its label.
    assert kind_of(store.show(pre)) == "user"
    # `snap show --json` gains the kind, read through the journal.
    assert json.loads(ok("snap", "show", pre, "--json").stdout)["kind"] == "pre-write"
    assert json.loads(ok("snap", "show", taken, "--json").stdout)["kind"] == "user"


def test_constructed_old_manifest_labelled_like_guards_without_a_record_reads_as_user(
    root: Path, user_data: Path
) -> None:
    store = _store(user_data)
    taken = _create("-m", "guard pre-write: constructed")
    _strip_kind(store, taken)
    assert kind_of(store.show(taken), cli._pre_write_ids(store)) == "user"
    assert json.loads(ok("snap", "show", taken, "--json").stdout)["kind"] == "user"
    assert "  kind:     user, one you took\n" in ok("snap", "show", taken).stdout


def test_constructed_old_profile_manifest_reads_as_profile(root: Path, user_data: Path) -> None:
    ok("profile", "save", "constructed", "--preset", "macros")
    store = _store(user_data)
    (manifest,) = store.list()
    assert manifest.kind == "profile"
    _strip_kind(store, manifest.id)
    assert kind_of(store.show(manifest.id)) == "profile"


def test_constructed_set_label_keeps_an_old_pre_write_manifest_without_a_kind(
    root: Path, flavor: Path, user_data: Path
) -> None:
    """A label rewrite (profile relabels use it) never writes `kind: "user"`
    into a manifest that has none: the bytes change in the label alone."""
    taken = _create("-m", "constructed")
    (flavor / CONFIG).write_bytes(b'SET constructed "wiped"\n')
    pre = _pre_write_of(user_data, _restore_yes(taken).transaction or "")
    store = _store(user_data)
    path = _strip_kind(store, pre)
    before = json.loads(path.read_bytes())

    updated = store.set_label(pre, "constructed relabel")

    after = json.loads(path.read_bytes())
    assert updated.kind is None
    assert "kind" not in after
    assert {k: v for k, v in after.items() if k != "label"} == {
        k: v for k, v in before.items() if k != "label"
    }
    assert kind_of(store.show(pre), cli._pre_write_ids(store)) == "pre-write"


def test_constructed_set_label_keeps_a_recorded_kind(root: Path, user_data: Path) -> None:
    ok("profile", "save", "constructed", "--preset", "macros")
    store = _store(user_data)
    (manifest,) = store.list()
    assert store.set_label(manifest.id, "profile:renamed").kind == "profile"
    assert json.loads((store.manifests_dir / f"{manifest.id}.json").read_bytes())["kind"] == (
        "profile"
    )


# ─── who records which kind ──────────────────────────────────────────────────


def test_doctor_first_run_records_first_run_and_snap_create_records_user(
    root: Path, user_data: Path
) -> None:
    """`firstrun.snapshot_flavor` is shared: doctor's first run passes
    `first-run`, `snap create` keeps the default `user`."""
    result = run("doctor", "--yes", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    store = _store(user_data)
    (first,) = store.list()
    assert first.kind == "first-run"
    assert json.loads((store.manifests_dir / f"{first.id}.json").read_bytes())["kind"] == (
        "first-run"
    )
    taken = _create("-m", "constructed")
    assert store.show(taken).kind == "user"
    assert "taken by doctor on its first run" in ok("snap", "show", first.id).stdout


def test_snapshot_flavor_defaults_to_user(root: Path, user_data: Path) -> None:
    inst = install.discover(root)
    (chosen,) = [f for f in inst.flavors if f.folder == FLAVOR]
    lay = layout.Layout.for_flavor(chosen, inst.root)
    manifest = firstrun.snapshot_flavor(_store(user_data), inst, chosen, lay)
    assert manifest.kind == "user"


def test_profiles_save_records_profile(root: Path, user_data: Path) -> None:
    ok("profile", "save", "constructed", "--preset", "macros")
    found = profiles.find(_store(user_data), "constructed")
    assert found.manifest.kind == "profile"
    assert found.manifest.purpose == "profile"


# ─── explain: the panel's sequence ───────────────────────────────────────────


def _explain(path: str) -> cli.ExplainReport:
    return _json(cli.ExplainReport, "explain", path)


def test_constructed_wipe_recover_explain_compares_with_the_user_snapshot(
    root: Path, flavor: Path, user_data: Path
) -> None:
    """The panel's sequence: snapshot, wipe the file, recover it with `snap
    restore`, then `explain` it. The newest snapshot is the restore's
    pre-write copy (it holds the wiped file); `explain` compares against the
    player's own snapshot, finds the file the same, offers no restore, and
    says what the newer pre-write copy would put back."""
    original = (flavor / CONFIG).read_bytes()
    taken = _create("-m", "constructed")
    (flavor / CONFIG).write_bytes(b"")  # wiped
    pre = _pre_write_of(user_data, _restore_yes(taken).transaction or "")
    assert (flavor / CONFIG).read_bytes() == original
    assert pre != taken

    report = _explain(CONFIG)
    facts = report.snapshot
    assert facts is not None
    assert facts.snapshot_id == taken
    assert facts.kind == "user"
    assert facts.live == "same"
    assert facts.newer_pre_write is not None and facts.newer_pre_write.id == pre
    assert all(pre not in h.command and pre not in h.argv for h in report.hints)
    assert not [h for h in report.hints if h.argv[:2] == ["snap", "restore"]]
    words = facts.words or ""
    assert taken in words and pre in words
    # The snapshot named is the player's latest, not the latest of all.
    assert words.startswith(f"In your latest snapshot ({taken}"), words
    assert "taken automatically just before a change" in words
    assert "as they were just before that change" in words
    text = ok("explain", CONFIG).stdout
    assert pre in text and "taken automatically just before a change" in text


def test_constructed_wipe_recover_change_explain_hints_the_user_snapshot(
    root: Path, flavor: Path, user_data: Path
) -> None:
    """After the recovery the file changes again: the restore hint names the
    player's snapshot, never the pre-write copy."""
    taken = _create("-m", "constructed")
    (flavor / CONFIG).write_bytes(b"")
    pre = _pre_write_of(user_data, _restore_yes(taken).transaction or "")
    (flavor / CONFIG).write_bytes(b'SET constructed "again"\n')

    report = _explain(CONFIG)
    assert report.snapshot is not None and report.snapshot.snapshot_id == taken
    restores = [h for h in report.hints if h.argv[:2] == ["snap", "restore"]]
    assert len(restores) == 1
    assert restores[0].argv[2] == taken
    assert all(pre not in h.command for h in report.hints)
    assert "taken automatically" not in restores[0].does


def test_constructed_old_store_without_kinds_still_hints_the_user_snapshot(
    root: Path, flavor: Path, user_data: Path
) -> None:
    """The same sequence on manifests written before kinds existed: the
    journal tells the pre-write copy apart."""
    taken = _create("-m", "constructed")
    (flavor / CONFIG).write_bytes(b"")
    pre = _pre_write_of(user_data, _restore_yes(taken).transaction or "")
    store = _store(user_data)
    _strip_kind(store, taken)
    _strip_kind(store, pre)
    (flavor / CONFIG).write_bytes(b'SET constructed "again"\n')

    report = _explain(CONFIG)
    assert report.snapshot is not None and report.snapshot.snapshot_id == taken
    assert all(pre not in h.command for h in report.hints)


def test_constructed_only_a_pre_write_copy_says_what_restoring_it_means(
    root: Path, flavor: Path, user_data: Path
) -> None:
    """With no snapshot but pre-write copies (a write made before any `snap
    create`), the hint may name one, and says in words what it puts back."""
    inst = install.discover(root)
    (chosen,) = [f for f in inst.flavors if f.folder == FLAVOR]
    with guard.transaction(chosen, label="constructed", store=_store(user_data).path) as tx:
        tx.write(CONFIG, b'SET constructed "written"\n')
    (record,) = guard.history(store=_store(user_data).path)

    report = _explain(CONFIG)
    facts = report.snapshot
    assert facts is not None and facts.snapshot_id == record.snapshot_id
    assert facts.kind == "pre-write" and facts.newer_pre_write is None
    (restore,) = [h for h in report.hints if h.argv[:2] == ["snap", "restore"]]
    assert "taken automatically just before a change" in restore.does
    assert "as they were just before that change" in restore.does
    assert "taken automatically just before a change" in (facts.words or "")


# ─── short references ────────────────────────────────────────────────────────


def _three(flavor: Path, user_data: Path) -> tuple[str, str, str]:
    """A user snapshot, a second user snapshot, and the pre-write copy a
    restore of the first takes (the newest)."""
    first = _create("-m", "constructed one")
    (flavor / CONFIG).write_bytes(b'SET constructed "two"\n')
    second = _create("-m", "constructed two")
    pre = _pre_write_of(user_data, _restore_yes(first).transaction or "")
    return first, second, pre


def test_constructed_latest_latest_user_and_tilde(
    root: Path, flavor: Path, user_data: Path
) -> None:
    first, second, pre = _three(flavor, user_data)
    store = _store(user_data)
    journal = cli._pre_write_ids(store)
    assert resolve_reference(store, "latest", journal) == pre
    assert resolve_reference(store, "~0", journal) == pre
    assert resolve_reference(store, "~1", journal) == second
    assert resolve_reference(store, "~2", journal) == first
    assert resolve_reference(store, "latest-user", journal) == second

    def shown(ref: str) -> Any:
        return json.loads(ok("snap", "show", ref, "--json").stdout)["id"]

    assert shown("latest") == pre
    assert shown("latest-user") == second
    assert shown("~2") == first
    diff = _json(cli.DiffReport, "snap", "diff", "~2", "latest-user")
    assert (diff.a, diff.b) == (first, second)
    plan = _json(cli.RestoreReport, "snap", "restore", "latest-user", "--dry-run")
    assert not plan.applied and plan.snapshot_id == second


def test_constructed_latest_user_on_an_old_store_skips_the_pre_write_copy(
    root: Path, flavor: Path, user_data: Path
) -> None:
    first, second, pre = _three(flavor, user_data)
    store = _store(user_data)
    for snapshot_id in (first, second, pre):
        _strip_kind(store, snapshot_id)
    assert json.loads(ok("snap", "show", "latest-user", "--json").stdout)["id"] == second


def test_constructed_unique_suffix_and_prefix(root: Path, flavor: Path, user_data: Path) -> None:
    first, second, pre = _three(flavor, user_data)
    store = _store(user_data)
    fingerprint = first.rsplit("-", 1)[1]
    assert resolve_reference(store, fingerprint) == first
    assert resolve_reference(store, fingerprint[-6:]) == first
    assert resolve_reference(store, "-" + fingerprint) == first
    assert resolve_reference(store, first[:20]) == first  # a unique prefix, as before
    # The restore's pre-write copy is of the same tree as `second`, so the
    # two share a fingerprint: the suffix is ambiguous, the full id is not.
    assert pre.rsplit("-", 1)[1] == second.rsplit("-", 1)[1]
    with pytest.raises(snapshot.SnapshotNotFoundError, match="ambiguous"):
        resolve_reference(store, pre.rsplit("-", 1)[1])
    assert resolve_reference(store, pre) == pre
    assert resolve_reference(store, second) == second
    assert json.loads(ok("snap", "show", first.rsplit("-", 1)[1], "--json").stdout)["id"] == first


def test_constructed_prefix_of_one_id_and_suffix_of_another_is_ambiguous(
    tmp_path: Path,
) -> None:
    """Prefix and suffix matches are pooled: a reference that starts one id
    and ends another is refused, never resolved to the prefix match."""
    store = SnapshotStore(tmp_path / "store")
    store.manifests_dir.mkdir(parents=True)
    older = "20250101T000000.000000Z-aaaaaaaa"
    newer = "20260101T000000.000000Z-bbbb2025"
    for snapshot_id in (older, newer):
        (store.manifests_dir / f"{snapshot_id}.json").write_bytes(b"{}")
    with pytest.raises(snapshot.SnapshotNotFoundError, match="ambiguous"):
        resolve_reference(store, "2025")
    assert resolve_reference(store, older) == older  # an exact id is never ambiguous
    assert resolve_reference(store, "20250") == older
    assert resolve_reference(store, "bbbb2025") == newer


@pytest.mark.parametrize(
    "reference",
    [
        pytest.param("~", id="constructed-tilde-alone"),
        pytest.param("~x", id="constructed-tilde-word"),
        pytest.param("~-1", id="constructed-tilde-negative"),
        pytest.param("~9", id="constructed-tilde-past-the-oldest"),
        pytest.param("latest-users", id="constructed-misspelled"),
        pytest.param("ffffffffffff", id="constructed-no-match"),
        pytest.param("Z-", id="constructed-ambiguous"),
    ],
)
def test_constructed_bad_references_are_refused(
    root: Path, flavor: Path, user_data: Path, reference: str
) -> None:
    _three(flavor, user_data)
    with pytest.raises(snapshot.SnapshotNotFoundError):
        resolve_reference(_store(user_data), reference)
    assert run("snap", "show", reference).exit_code != 0


def test_constructed_latest_user_without_a_user_snapshot(root: Path, user_data: Path) -> None:
    ok("profile", "save", "constructed", "--preset", "macros")
    with pytest.raises(snapshot.SnapshotNotFoundError):
        resolve_reference(_store(user_data), "latest-user")
    assert run("snap", "show", "latest-user").exit_code != 0
