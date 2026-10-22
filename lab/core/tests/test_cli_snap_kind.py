"""Graders for the kind `wowlab snap create` records (M16-09T).

Written before M16-09, from the M16-09T and M16-09 tickets in
docs/BACKLOG.md and docs/LAB_PLAN.md §18.1 item 3: each manifest records a
`kind` (`user`, `first-run`, `profile` or `pre-write`). A snapshot the
player takes with `snap create` is a `user` snapshot, whatever its label
says; the pre-write copy the gate takes before `snap restore --yes` is a
`pre-write` one (graded at the `guard` seam in `test_guard_snapshot_kind.py`,
and here once end to end). The manifest gains `kind` and no other key.

The pins at the end carry no marker: they hold today's other manifest
fields and the store's layout (nothing beside today's entries, so the kind
is not kept outside the manifest; security-reviewer finding, PR #333) and
pass before and after M16-09.

Each grader carries one marker line that M16-09 deletes; nothing else in
this file is the implementer's to change. The install is the captured tree
as `test_cli.py` builds it (copied into `tmp_path`, the user data directory
redirected, the process table a fake). The snapshot labels are constructed
(named `constructed` in the test ids, L8). Nothing reads or writes a real
install.
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
    user_data,  # noqa: F401
)
from test_guard_snapshot_kind import assert_store_holds_only_todays_layout

from wowlab_core import cli, guard
from wowlab_core.snapshot import SnapshotStore

# The keys of a manifest file `snap create` writes today (`purpose` is left
# out when it is None). M16-09 adds `kind` and nothing else.
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

LABELS = [
    pytest.param(("-m", ""), "", id="constructed-no-label"),
    pytest.param(("-m", "before the patch"), "before the patch", id="constructed-label"),
    # A label spelled like guard's own does not make a pre-write snapshot.
    pytest.param(
        ("-m", "guard pre-write: constructed"),
        "guard pre-write: constructed",
        id="constructed-label-spelled-like-guards",
    ),
    pytest.param(
        ("-m", "constructed", "--screenshots"), "constructed", id="constructed-with-screenshots"
    ),
]


def _store(user_data: Path) -> SnapshotStore:
    return SnapshotStore(user_data / "store")


def _raw(user_data: Path, snapshot_id: str) -> dict[str, Any]:
    raw = json.loads((_store(user_data).manifests_dir / f"{snapshot_id}.json").read_bytes())
    assert isinstance(raw, dict)
    return raw


def _create(*args: str) -> dict[str, Any]:
    out = json.loads(ok("snap", "create", *args, "--json").stdout)
    assert isinstance(out, dict)
    return out


# ─── the graders ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("args", "label"), LABELS)
@pytest.mark.xfail(strict=True, reason="M16-09 not implemented")
def test_snap_create_records_kind_user(
    root: Path, user_data: Path, args: tuple[str, ...], label: str
) -> None:
    out = _create(*args)
    snapshot_id = out["id"]
    raw = _raw(user_data, snapshot_id)

    assert out["label"] == label
    assert out.get("kind") == "user", sorted(out)
    assert raw.get("kind") == "user", sorted(raw)
    assert _store(user_data).show(snapshot_id).kind == "user"
    assert set(raw) == MANIFEST_KEYS | {"kind"}, sorted(set(raw) ^ (MANIFEST_KEYS | {"kind"}))


@pytest.mark.xfail(strict=True, reason="M16-09 not implemented")
def test_snap_restore_takes_a_pre_write_snapshot_and_leaves_the_user_one_a_user_one(
    root: Path, flavor: Path, user_data: Path
) -> None:
    """End to end: `snap restore --yes` journals a transaction whose snapshot
    is `pre-write`; the snapshot restored from stays `user`. The two are the
    only manifests in the store."""
    taken = _create("-m", "constructed")["id"]
    config = flavor / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + b'SET constructed "1"\n')

    done = _json(cli.RestoreReport, "snap", "restore", taken, "--yes")
    assert done.applied and done.transaction is not None
    record = next(
        r for r in guard.history(store=_store(user_data).path) if r.id == done.transaction
    )

    kinds = {m.id: m.kind for m in _store(user_data).list()}
    assert kinds == {taken: "user", record.snapshot_id: "pre-write"}
    assert _raw(user_data, taken).get("kind") == "user"
    assert _raw(user_data, record.snapshot_id).get("kind") == "pre-write"


# ─── pins: nothing else changes (no marker; they pass before and after) ─────


@pytest.mark.parametrize(("args", "label"), LABELS)
def test_snap_create_manifest_keeps_every_other_field(
    root: Path, user_data: Path, args: tuple[str, ...], label: str
) -> None:
    out = _create(*args)
    raw = _raw(user_data, out["id"])

    assert set(raw) - {"kind"} == MANIFEST_KEYS, sorted(set(raw) - {"kind"} ^ MANIFEST_KEYS)
    assert "purpose" not in raw, "a snapshot the player takes has no purpose"
    assert raw["format"] == 1
    assert raw["label"] == label
    assert raw["flavor_folder"] == FLAVOR
    assert raw["client_running"] is False
    # `--json` prints the manifest the store holds.
    assert raw == out


# What the store holds at its root after `snap create` alone, and after a
# `snap restore --yes` (which journals), today.
SNAP_CREATE_STORE_ROOT = frozenset({"lock", "manifests", "objects", "tmp"})
SNAP_RESTORE_STORE_ROOT = frozenset({"journal", "lock", "manifests", "objects", "tmp"})


@pytest.mark.parametrize(("args", "label"), LABELS)
def test_snap_create_store_holds_nothing_beside_todays_layout(
    root: Path, user_data: Path, args: tuple[str, ...], label: str
) -> None:
    out = _create(*args)
    assert_store_holds_only_todays_layout(
        _store(user_data).path, SNAP_CREATE_STORE_ROOT, {out["id"]}, set()
    )


def test_snap_restore_store_holds_nothing_beside_todays_layout(
    root: Path, flavor: Path, user_data: Path
) -> None:
    taken = _create("-m", "constructed")["id"]
    config = flavor / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + b'SET constructed "1"\n')
    done = _json(cli.RestoreReport, "snap", "restore", taken, "--yes")
    assert done.applied and done.transaction is not None

    store = _store(user_data)
    records = {r.id: r.snapshot_id for r in guard.history(store=store.path)}
    assert set(records) == {done.transaction}
    assert_store_holds_only_todays_layout(
        store.path,
        SNAP_RESTORE_STORE_ROOT,
        {taken, records[done.transaction]},
        set(records),
    )
