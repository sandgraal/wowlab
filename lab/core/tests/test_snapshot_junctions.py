"""NTFS junctions in a snapshot subtree are links, never directories (M10-10 follow-up).

CPython reports only `IO_REPARSE_TAG_SYMLINK` as a link on Windows; a
junction `lstat`s as a directory. These tests hold the walk to "record it as
a link with its target and never descend". The Windows-only ones build real
junctions in `tmp_path`; the portable ones stand a real directory in for a
junction through the module's `_is_junction` seam and are labelled
`constructed`. Nothing touches a real install or the real user data directory.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wowlab_core import snapshot
from wowlab_core.snapshot import SnapshotError, SnapshotStore

T0 = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
SECRET = b"outside the install: do not capture\n"

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="NTFS junctions exist only on Windows"
)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "Install"
    (root / "WTF").mkdir(parents=True)
    (root / "WTF" / "Config.wtf").write_bytes(b'SET portal "US"\n')
    (root / "WTF" / "other.wtf").write_bytes(b"other\n")
    (root / "Interface" / "AddOns" / "Real").mkdir(parents=True)
    (root / "Interface" / "AddOns" / "Real" / "Real.toc").write_bytes(b"## Title: Real\n")
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    secret = tmp_path / "outside" / "secret"
    (secret / "deeper").mkdir(parents=True)
    (secret / "Secret.lua").write_bytes(SECRET)
    (secret / "deeper" / "More.lua").write_bytes(SECRET)
    return secret


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "userdata" / "store")


def _stored_contents(store: SnapshotStore) -> list[bytes]:
    if not store.objects_dir.is_dir():
        return []  # nothing stored at all
    names = [
        shard.name + obj.name
        for shard in store.objects_dir.iterdir()
        if shard.is_dir()
        for obj in shard.iterdir()
    ]
    return [store.read_object(name) for name in names]


def _junction(link: Path, target: Path) -> None:
    import _winapi  # Windows only; the callers are skipped elsewhere

    _winapi.CreateJunction(str(target), str(link))


# ── real junctions (Windows) ────────────────────────────────────────────────


@windows_only
def test_a_junction_pointing_outside_is_recorded_as_a_link_and_not_followed(
    source: Path, outside: Path, store: SnapshotStore
) -> None:
    link = source / "Interface" / "AddOns" / "Linked"
    _junction(link, outside)
    assert link.is_junction() and not link.is_symlink()  # the case CPython misses

    m = store.create(source, ["Interface/AddOns", "WTF"], now=T0)

    linked = m.entry("Interface/AddOns/Linked")
    assert linked is not None
    assert (linked.kind, linked.sha256) == ("symlink", None)
    assert linked.target is not None
    assert linked.target.rstrip("\\/").endswith(outside.name)
    assert linked.target == str(link.readlink())
    assert [e.path for e in m.entries if e.path.startswith("Interface/AddOns/Linked/")] == []
    assert SECRET not in _stored_contents(store)
    with pytest.raises(SnapshotError, match="symlink"):
        store.read_file(m.id, "Interface/AddOns/Linked")


@windows_only
def test_a_junction_to_its_own_parent_does_not_loop(source: Path, store: SnapshotStore) -> None:
    _junction(source / "WTF" / "loop", source / "WTF")

    m = store.create(source, ["WTF"], now=T0)

    assert [(e.path, e.kind) for e in m.entries] == [
        ("WTF/Config.wtf", "file"),
        ("WTF/loop", "symlink"),
        ("WTF/other.wtf", "file"),
    ]


@windows_only
def test_a_subtree_through_a_junction_is_refused_and_one_named_is_a_link(
    source: Path, outside: Path, store: SnapshotStore
) -> None:
    _junction(source / "WTF" / "link", outside)

    for subtree in ("WTF/link/deeper", "WTF/link/Secret.lua"):
        with pytest.raises(SnapshotError, match="junction"):
            store.create(source, [subtree], now=T0)
    assert store.list() == ()

    m = store.create(source, ["WTF/link"], now=T0)
    assert [(e.path, e.kind) for e in m.entries] == [("WTF/link", "symlink")]
    assert SECRET not in _stored_contents(store)


# ── the same rules through the seam, on every platform ─────────────────────


@pytest.fixture
def fake_junction(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make every directory named `Linked` look like a junction to `elsewhere`."""
    name = "Linked"
    target = Path("C:/elsewhere/secret")
    real_readlink = Path.readlink

    def is_junction(path: Path | os.DirEntry[str]) -> bool:
        return Path(path).name == name and Path(path).is_dir()

    def readlink(self: Path) -> Path:
        return target if self.name == name else real_readlink(self)

    monkeypatch.setattr(snapshot, "_is_junction", is_junction)
    monkeypatch.setattr(Path, "readlink", readlink)
    return str(target)


def test_a_junction_is_recorded_as_a_link_and_not_descended_constructed(
    source: Path, outside: Path, store: SnapshotStore, fake_junction: str
) -> None:
    # A real directory with real content stands in for the junction.
    linked = source / "Interface" / "AddOns" / "Linked"
    (linked / "deeper").mkdir(parents=True)
    (linked / "Secret.lua").write_bytes(SECRET)
    (linked / "deeper" / "More.lua").write_bytes(SECRET)

    m = store.create(source, ["Interface/AddOns", "WTF"], now=T0)

    assert [(e.path, e.kind, e.target) for e in m.entries] == [
        ("Interface/AddOns/Linked", "symlink", fake_junction),
        ("Interface/AddOns/Real/Real.toc", "file", None),
        ("WTF/Config.wtf", "file", None),
        ("WTF/other.wtf", "file", None),
    ]
    assert m.entries[0].sha256 is None
    assert SECRET not in _stored_contents(store)


def test_a_subtree_at_or_through_a_junction_constructed(
    source: Path, store: SnapshotStore, fake_junction: str
) -> None:
    linked = source / "WTF" / "Linked"
    (linked / "deeper").mkdir(parents=True)
    (linked / "deeper" / "Secret.lua").write_bytes(SECRET)

    for subtree in ("WTF/Linked/deeper", "WTF/Linked/deeper/Secret.lua"):
        with pytest.raises(SnapshotError, match="junction"):
            store.create(source, [subtree], now=T0)
    assert store.list() == ()

    m = store.create(source, ["WTF/Linked"], now=T1)
    assert [(e.path, e.kind, e.target) for e in m.entries] == [
        ("WTF/Linked", "symlink", fake_junction)
    ]
    assert SECRET not in _stored_contents(store)


def test_is_junction_is_false_for_ordinary_directories_and_files(source: Path) -> None:
    assert not snapshot._is_junction(source / "WTF")
    assert not snapshot._is_junction(source / "WTF" / "Config.wtf")
    with os.scandir(source / "WTF") as it:
        assert not any(snapshot._is_junction(entry) for entry in it)
