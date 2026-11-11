# Probe from review of m16/108t-closed-unsnapshotted-passed-over; reproduces that the
# missing-manifest grader cannot catch an M16-69 that passes over a closed, path-less
# record because its manifest is missing (LAB_PLAN §6.10 (a2), "what never took a
# snapshot means").
"""Constructed (L8, boundary). The branch's
`test_constructed_a_record_whose_manifest_is_missing_is_not_passed_over` deletes
the manifest of a record that lists a path, which (a2)'s path rule already keeps
from being passed over. This probe deletes the manifest of a record with no
paths and no created folders that did take its snapshot: a transaction rolled
back before any write. (a2) says it is not passed over."""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_cli import flavor, root, run, user_data  # noqa: F401
from test_guard_cleanup_crash_m16_93 import CONFIG, T1_BYTES, commit, manifest_ids

from wowlab_core import guard, snapshot


def test_constructed_a_pathless_record_whose_manifest_is_missing_is_not_passed_over(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path
) -> None:
    store = snapshot.default_store_path()
    t1 = commit(flavor, store, "t1", CONFIG, T1_BYTES)
    ns = SimpleNamespace(path=flavor, version=None)
    with (
        pytest.raises(RuntimeError),
        guard.transaction(ns, label="rolled-back", store=store),
    ):
        raise RuntimeError("constructed: the caller fails before any write")
    r = guard.history(store=store)[-1]
    assert r.label == "rolled-back" and r.state != "open" and r.paths == ()
    assert r.created_dirs == ()
    # Positive control: with its manifest, R is the target.
    assert r.snapshot_id in manifest_ids(store)
    assert json.loads(run("undo", "--json").stdout)["undone"] == r.id

    (store / "manifests" / f"{r.snapshot_id}.json").unlink()

    offered = json.loads(run("undo", "--json").stdout)
    assert offered.get("undone") == r.id, (
        "a missing manifest does not count as never having taken a snapshot",
        offered,
    )
    with pytest.raises(guard.GuardError):
        guard.undo(store=store, expected_id=t1.id)
