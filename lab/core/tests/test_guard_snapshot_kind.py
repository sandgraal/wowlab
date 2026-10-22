"""Graders for the kind of guard's pre-write snapshot (M16-09T).

Written before M16-09, from the M16-09T and M16-09 tickets in
docs/BACKLOG.md and docs/LAB_PLAN.md §18.1 item 3 ("Each manifest records a
`kind` (`user`, `first-run`, `profile` or `pre-write`)"). Guard creates the
pre-write snapshot on entering every transaction that is not a dry run
(§6.10), `undo()` included, so recording its kind changes what `guard` does
(ADR-0013) and these graders land first.

What is graded
--------------
- Every manifest guard creates for a transaction (a write, a delete, a
  restore by name or whole, one that is refused, rolled back or empty, and
  the transaction `undo()` runs) records `kind="pre-write"`: on the model
  (`Manifest.kind`) and in the manifest file under `<store>/manifests/`,
  which gains that one key and no other.
- Nothing else in the manifest or the journal changes. These are pins of
  today's behaviour, not graders, so they carry no marker and pass before
  and after M16-09: the other manifest keys and their values, the canonical
  encoding, the tree fingerprint in the id (a pre-write snapshot of the same
  tree as a user snapshot has the same fingerprint, so `kind` is not in it,
  like `label` and `purpose`), the journal record's keys and format, and a
  dry run creating no manifest at all.

`snap create` recording `kind="user"` is graded through the CLI in
`test_cli_snap_kind.py`.

Each grader carries one marker line that M16-09 deletes; nothing else in
this file is the implementer's to change. Everything is constructed, as in
`test_guard.py`, whose synthetic install, fixtures and helpers this file
loads (not copies): the install is a tree under `tmp_path`, the user data
directory is redirected and the process table is injected. No test needs
or touches a real install.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore, manifest_bytes, tree_fingerprint


def _load(name: str, filename: str) -> types.ModuleType:
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = _load("_m16_09t_guard_graders", "test_guard.py")

# The M10-11T fixtures, registered here under their own names: the user data
# directory and the process table are redirected for every test in this file.
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
store = G.store
idle = G.idle

Flavor = G.Flavor
FLAVOR_FOLDER = G.FLAVOR_FOLDER
CONFIG = G.CONFIG
BINDINGS = G.BINDINGS
NEW_CONFIG = G.NEW_CONFIG
NEW_SAVED = G.NEW_SAVED
NEW_LUA = G.NEW_LUA

# The keys of a manifest file as `manifest_bytes` writes one for guard today
# (`purpose` is left out when it is None). M16-09 adds `kind` and nothing else.
MANIFEST_KEYS = frozenset(
    {
        "format",
        "id",
        "created_at",
        "label",
        "install_root",
        "flavor_folder",
        "flavor_version",
        "subtrees",
        "excluded",
        "client_running",
        "entries",
    }
)
MANIFEST_FORMAT_TODAY = 1

# The keys of a journal record file (journal format 2). M16-09 changes none.
JOURNAL_KEYS = frozenset(
    {
        "format",
        "id",
        "created_at",
        "label",
        "snapshot_id",
        "install_root",
        "flavor_path",
        "flavor_version",
        "state",
        "paths",
        "created_dirs",
        "note",
        "temps",
        "temps_removed",
        "temps_left",
        "cleanup_finished",
    }
)
JOURNAL_FORMAT_TODAY = 2

GUARD_ROOTS = ("Fonts", "Interface", "WTF")


class _RefusedError(Exception):
    """Raised inside a transaction so that it rolls back."""


# ─── helpers ─────────────────────────────────────────────────────────────────


def _ids(store: SnapshotStore) -> set[str]:
    return {m.id for m in store.list()}


def _raw_manifest(store: SnapshotStore, snapshot_id: str) -> dict[str, Any]:
    raw = json.loads((store.manifests_dir / f"{snapshot_id}.json").read_bytes())
    assert isinstance(raw, dict)
    return raw


def _raw_record(store: SnapshotStore, record_id: str) -> dict[str, Any]:
    raw = json.loads((store.path / "journal" / f"{record_id}.json").read_bytes())
    assert isinstance(raw, dict)
    return raw


def _write(guard: Any, flavor: Flavor, store: SnapshotStore, label: str) -> None:
    with guard.transaction(flavor, label=label, store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)


def _last_record(guard: Any, store: SnapshotStore) -> Any:
    records = list(guard.history(store=store.path))
    assert records, "the step journaled nothing"
    return records[-1]


# Each scenario: (setup, step). `setup` runs first and may create snapshots of
# its own; `step` is the transaction whose pre-write snapshot is graded.
Step = Callable[[Any, Flavor, SnapshotStore], None]


def _nothing(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    return None


def _step_write(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    with guard.transaction(flavor, label="constructed write", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(NEW_SAVED, NEW_LUA)


def _step_delete(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    with guard.transaction(flavor, label="constructed delete", store=store.path) as tx:
        tx.delete(BINDINGS)


def _setup_earlier_write(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    _write(guard, flavor, store, "constructed earlier")


def _earlier_snapshot(guard: Any, store: SnapshotStore) -> str:
    snapshot_id: str = next(iter(guard.history(store=store.path))).snapshot_id
    return snapshot_id


def _step_restore_named(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    source = _earlier_snapshot(guard, store)
    with guard.transaction(flavor, label="constructed restore named", store=store.path) as tx:
        tx.restore(source, paths=[CONFIG])


def _step_restore_whole(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    source = _earlier_snapshot(guard, store)
    with guard.transaction(flavor, label="constructed restore whole", store=store.path) as tx:
        tx.restore(source)


def _step_rolled_back(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    with (
        pytest.raises(_RefusedError),
        guard.transaction(flavor, label="constructed rolled back", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        raise _RefusedError


def _step_refused(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    with (
        pytest.raises(guard.PathNotAllowedError),
        guard.transaction(flavor, label="constructed refused", store=store.path) as tx,
    ):
        tx.write("Data/constructed.bin", b"constructed: must never land")


def _step_empty(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    with guard.transaction(flavor, label="constructed empty", store=store.path):
        pass


def _step_undo(guard: Any, flavor: Flavor, store: SnapshotStore) -> None:
    guard.undo(store=store.path)


SCENARIOS: dict[str, tuple[Step, Step]] = {
    "write": (_nothing, _step_write),
    "delete": (_nothing, _step_delete),
    "restore-named": (_setup_earlier_write, _step_restore_named),
    "restore-whole": (_setup_earlier_write, _step_restore_whole),
    "rolled-back": (_nothing, _step_rolled_back),
    "refused": (_nothing, _step_refused),
    "empty": (_nothing, _step_empty),
    "undo": (_setup_earlier_write, _step_undo),
}
SCENARIO_PARAMS = [pytest.param(name, id=f"constructed-{name}") for name in SCENARIOS]


def _pre_write_of(guard: Any, flavor: Flavor, store: SnapshotStore, scenario: str) -> str:
    """Run the scenario; return the one manifest its step created, which is
    the snapshot its journal record names."""
    setup, step = SCENARIOS[scenario]
    setup(guard, flavor, store)
    before = _ids(store) if store.path.exists() else set()
    step(guard, flavor, store)
    created = _ids(store) - before
    record = _last_record(guard, store)
    assert created == {record.snapshot_id}, (created, record.snapshot_id)
    snapshot_id: str = record.snapshot_id
    return snapshot_id


# ─── the graders ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", SCENARIO_PARAMS)
@pytest.mark.xfail(strict=True, reason="M16-09 not implemented")
def test_constructed_every_pre_write_manifest_records_kind_pre_write(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, scenario: str
) -> None:
    """The manifest guard creates on entering a transaction records
    `kind="pre-write"`, read back through the store and in the file itself,
    and the file gains that one key."""
    snapshot_id = _pre_write_of(guard, flavor, store, scenario)

    assert store.show(snapshot_id).kind == "pre-write"
    raw = _raw_manifest(store, snapshot_id)
    assert raw.get("kind") == "pre-write", sorted(raw)
    assert set(raw) == MANIFEST_KEYS | {"kind"}, sorted(set(raw) ^ (MANIFEST_KEYS | {"kind"}))


@pytest.mark.xfail(strict=True, reason="M16-09 not implemented")
def test_constructed_every_manifest_of_a_session_of_writes_is_pre_write(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """Several transactions and an undo, one store: every manifest in it is a
    pre-write copy, one per journal record, each named by its record."""
    _step_write(guard, flavor, store)
    _step_delete(guard, flavor, store)
    _step_undo(guard, flavor, store)
    _step_empty(guard, flavor, store)

    records = list(guard.history(store=store.path))
    manifests = store.list()
    assert len(records) == 4
    assert {m.id for m in manifests} == {r.snapshot_id for r in records}
    assert {m.id: m.kind for m in manifests} == {m.id: "pre-write" for m in manifests}


# ─── pins: nothing else changes (no marker; they pass before and after) ─────


@pytest.mark.parametrize("scenario", SCENARIO_PARAMS)
def test_constructed_pre_write_manifest_keeps_every_other_field(
    guard: Any,
    flavor: Flavor,
    install_root: Path,
    store: SnapshotStore,
    idle: None,
    scenario: str,
) -> None:
    snapshot_id = _pre_write_of(guard, flavor, store, scenario)
    record = _last_record(guard, store)
    raw = _raw_manifest(store, snapshot_id)
    manifest = store.show(snapshot_id)

    assert set(raw) - {"kind"} == MANIFEST_KEYS, sorted(set(raw) - {"kind"} ^ MANIFEST_KEYS)
    assert "purpose" not in raw, "a pre-write snapshot has no purpose"
    assert raw["format"] == MANIFEST_FORMAT_TODAY
    assert raw["id"] == snapshot_id
    assert raw["label"] == f"guard pre-write: {record.label}"
    assert raw["client_running"] is False
    assert raw["flavor_folder"] == FLAVOR_FOLDER
    assert raw["flavor_version"] == flavor.version
    assert raw["subtrees"] == [f"{FLAVOR_FOLDER}/{r}" for r in GUARD_ROOTS]
    assert raw["excluded"] == []
    assert raw["install_root"] == _raw_record(store, record.id)["install_root"]
    # The one canonical encoding, and a fingerprint over the tree alone.
    assert (store.manifests_dir / f"{snapshot_id}.json").read_bytes() == manifest_bytes(manifest)
    assert manifest.fingerprint == tree_fingerprint(
        manifest.subtrees, manifest.excluded, manifest.entries
    )


def test_constructed_pre_write_and_user_snapshot_of_one_tree_share_a_fingerprint(
    guard: Any, flavor: Flavor, install_root: Path, store: SnapshotStore, idle: None
) -> None:
    """`kind` is not part of what the id fingerprints: a snapshot the caller
    takes and the pre-write snapshot of the same tree carry the same one."""
    taken = store.create(
        install_root,
        [f"{FLAVOR_FOLDER}/{r}" for r in GUARD_ROOTS],
        label="constructed",
        flavor_folder=FLAVOR_FOLDER,
        flavor_version=flavor.version,
        client_running=False,
    )
    _step_empty(guard, flavor, store)
    pre_write = store.show(_last_record(guard, store).snapshot_id)

    assert pre_write.id != taken.id
    assert pre_write.fingerprint == taken.fingerprint


@pytest.mark.parametrize("scenario", SCENARIO_PARAMS)
def test_constructed_journal_record_is_unchanged(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, scenario: str
) -> None:
    snapshot_id = _pre_write_of(guard, flavor, store, scenario)
    record = _last_record(guard, store)
    raw = _raw_record(store, record.id)

    assert set(raw) == JOURNAL_KEYS, sorted(set(raw) ^ JOURNAL_KEYS)
    assert raw["format"] == JOURNAL_FORMAT_TODAY
    assert raw["snapshot_id"] == snapshot_id
    assert raw["label"] == record.label
    assert "kind" not in json.dumps(raw["paths"]), "the journal's paths carry no snapshot kind"


def test_constructed_dry_run_creates_no_manifest(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """A dry run has no pre-write snapshot, so there is nothing to give a kind."""
    _step_empty(guard, flavor, store)
    before = _ids(store)
    with guard.transaction(flavor, label="constructed dry", store=store.path, dry_run=True) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert _ids(store) == before
