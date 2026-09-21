# Probe from review of m10/10-snapshot-store; reproduces create() writing the
# store inside the source tree when the store path spells the root with a
# different case on a case-insensitive volume (L1).
"""`_refuse_overlap` compares `Path.resolve()` results, and `resolve()` does
not canonicalise case on macOS or Windows. `<tmp>/Install` and
`<tmp>/install/store` are the same directory tree on those volumes, yet the
overlap check sees two unrelated paths and `create` proceeds to write
objects, a manifest and a tmp directory inside the tree it is capturing.

Every tree here is constructed in `tmp_path`; no install is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from wowlab_core.snapshot import SnapshotStore, StoreLocationError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def test_case_variant_store_path_inside_source_is_refused_constructed(tmp_path: Path) -> None:
    source = tmp_path / "Install"
    (source / "WTF").mkdir(parents=True)
    (source / "WTF" / "Config.wtf").write_bytes(b"SET a 1\n")
    if not (tmp_path / "install").exists():
        pytest.skip("case-sensitive volume: the two spellings are different directories")

    # Positive control: the same-case spelling is refused and writes nothing.
    before = _listing(source)
    with pytest.raises(StoreLocationError):
        SnapshotStore(source / "store").create(source, ["."], now=NOW)
    assert _listing(source) == before

    # The defect: a case-variant spelling of the same location.
    store = SnapshotStore(tmp_path / "install" / "store")
    try:
        with pytest.raises(StoreLocationError):
            store.create(source, ["."], now=NOW)
    finally:
        assert _listing(source) == before, "create() wrote inside the source tree (L1)"
