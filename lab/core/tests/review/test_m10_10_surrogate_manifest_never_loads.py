# Probe from review of m10/10-snapshot-store; reproduces create() writing a
# manifest that can never be loaded again, which then makes list() and gc()
# raise for the whole store.
"""`manifest_bytes` uses `ensure_ascii=True`, which happily encodes a lone
surrogate as `\\udc80`. `Manifest.model_validate_json` refuses that escape.
So a string carrying a lone surrogate goes out but never comes back in.

Two ways in: a `label` passed by the caller (reproduced end to end below),
and a file name. Python surfaces undecodable POSIX file-name bytes
(surrogateescape) and unpaired UTF-16 units in Windows file names as lone
surrogates. APFS refuses to create such a name, so the path variant is
reproduced at the model level.

Every input here is constructed (hostile-input case).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path

from wowlab_core.snapshot import Entry, Manifest, SnapshotError, SnapshotStore, manifest_bytes

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 2, tzinfo=UTC)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    (source / "WTF").mkdir(parents=True)
    (source / "WTF" / "a.txt").write_bytes(b"x")
    return source


def test_a_label_create_accepts_must_load_again_constructed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    store = SnapshotStore(tmp_path / "store")

    # Positive control: non-ASCII text round-trips through the store.
    good = store.create(source, ["WTF"], label="naïve ✓ 設定", now=NOW)
    assert store.show(good.id).label == "naïve ✓ 設定"
    assert len(store.list()) == 1

    # Refusing the label up front is a fine fix; so is storing it loadably.
    with contextlib.suppress(SnapshotError):
        store.create(source, ["WTF"], label="bad\udc80label", now=LATER)
    # Whatever create() did, the store must still be readable and collectable.
    assert len(store.list()) >= 1
    store.gc(dry_run=True)


def test_a_manifest_with_a_surrogate_path_round_trips_constructed() -> None:
    def manifest(path: str) -> Manifest:
        return Manifest(
            id="20260101T000000.000000Z-00000000",
            created_at="2026-01-01T00:00:00.000000Z",
            install_root="/nowhere",
            subtrees=("WTF",),
            entries=(Entry(path=path, sha256="0" * 64, size=1, mode=0o644),),
        )

    # Positive control.
    ok = manifest("WTF/ä.txt")
    assert Manifest.model_validate_json(manifest_bytes(ok)) == ok

    # What os.scandir yields for the POSIX file name b"WTF/\x80.txt".
    hostile = manifest("WTF/\udc80.txt")
    assert Manifest.model_validate_json(manifest_bytes(hostile)) == hostile
