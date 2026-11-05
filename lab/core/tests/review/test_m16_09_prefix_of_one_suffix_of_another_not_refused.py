# Probe from review of m16/09-snapshot-kinds; reproduces a reference that is a unique prefix of one id and a suffix of another resolving silently instead of being refused as ambiguous
"""Constructed store (not a real capture): two manifest files, named as the
store names them, one taken in 2025 and one whose fingerprint ends in
`2025`. `resolve_reference(store, "2025")` matches the first by prefix and
the second by suffix. The PR's stated ruling is that prefix and suffix
matches are pooled and a reference matching more than one id is refused;
the code tries `resolve_id` (prefix only) first and returns its unique hit
without looking at suffixes, so the reference resolves to the 2025 snapshot.
`snap restore` takes the same path. `resolve_reference` reads only the
manifest file names here, so the files' contents are not needed.

Positive control: a reference that is a suffix of two ids (and a prefix of
none) is refused as ambiguous, as the ruling says.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core.snapshot import SnapshotNotFoundError, SnapshotStore, resolve_reference

OLDER = "20250101T000000.000000Z-aaaaaaaa"
NEWER = "20260101T000000.000000Z-bbbb2025"
THIRD = "20260102T000000.000000Z-cccc2025"


def _store(tmp_path: Path, ids: tuple[str, ...]) -> SnapshotStore:
    store = SnapshotStore(tmp_path / "store")
    (store.path / "manifests").mkdir(parents=True)
    for snapshot_id in ids:
        (store.path / "manifests" / f"{snapshot_id}.json").write_bytes(b"{}")
    return store


def test_positive_control_a_suffix_of_two_ids_is_ambiguous(tmp_path: Path) -> None:
    store = _store(tmp_path, (NEWER, THIRD))
    with pytest.raises(SnapshotNotFoundError, match="ambiguous"):
        resolve_reference(store, "2025")


def test_constructed_prefix_of_one_and_suffix_of_another_is_ambiguous(tmp_path: Path) -> None:
    store = _store(tmp_path, (OLDER, NEWER))
    # Fails today: returns OLDER.
    with pytest.raises(SnapshotNotFoundError, match="ambiguous"):
        resolve_reference(store, "2025")
