"""`wowlab_core.snapshot` (M10-10, docs/LAB_PLAN.md §6.9).

Every tree here is constructed in `tmp_path`: the store and its manifest are
our own format, not a client format, so there is no real capture to grade
against (L8 applies to external formats). No test touches a real install or
the real user data directory.
"""

from __future__ import annotations

import json
import os
import sys
import zlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import platformdirs
import pytest
from pydantic import ValidationError

from wowlab_core import snapshot
from wowlab_core.snapshot import (
    Manifest,
    ManifestIntegrityError,
    ObjectCorruptError,
    SnapshotError,
    SnapshotExistsError,
    SnapshotNotFoundError,
    SnapshotStore,
    StoreLocationError,
    manifest_bytes,
)

T0 = datetime(2026, 9, 21, 12, 0, 0, 123456, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
T2 = T0 + timedelta(minutes=10)
FIXED_MTIME_NS = 1_750_000_000_123_456_789

SUBTREES = ("WTF", "Interface/AddOns", "Fonts")

FILES: dict[str, bytes] = {
    "WTF/Config.wtf": b'SET portal "US"\nSET gxApi "metal"\n',
    "WTF/Account/ACCT/SavedVariables/Addon.lua": b'AddonDB = {\n\t["a"] = 1,\n}\n',
    "WTF/Account/ACCT/SavedVariables/Addon.lua.bak": b'AddonDB = {\n\t["a"] = 1,\n}\n',
    "WTF/Account/ACCT/bindings-cache.wtf": b"bind W MOVEFORWARD\n",
    "Interface/AddOns/Thing/Thing.toc": b"## Interface: 0\n## Title: Thing\nThing.lua\n",
    "Interface/AddOns/Thing/Thing.lua": b"-- nothing\n",
    "Interface/AddOns/Thing/empty.txt": b"",
    "Interface/Icons/override.blp": b"BLP2\x00\x01\x02",
    "Logs/Client.log": b"noise\n",
    "Data/data/data.001": b"\x00" * 64,
}


def build_tree(root: Path, files: dict[str, bytes] | None = None) -> Path:
    """Write a synthetic install-shaped tree with pinned modes and mtimes."""
    for rel, data in (FILES if files is None else files).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644)
        os.utime(path, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))
    return root


def tree_state(root: Path) -> dict[str, tuple[int, int, int]]:
    """Every path under `root` (directories too) with size, mtime and mode."""
    state: dict[str, tuple[int, int, int]] = {}
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        size = st.st_size if path.is_file() else 0
        state[path.relative_to(root).as_posix()] = (size, st.st_mtime_ns, st.st_mode)
    return state


def object_names(store: SnapshotStore) -> set[str]:
    if not store.objects_dir.is_dir():
        return set()
    return {p.parent.name + p.name for p in store.objects_dir.glob("*/*") if p.is_file()}


def manifest_file(store: SnapshotStore, snapshot_id: str) -> Path:
    return store.manifests_dir / f"{snapshot_id}.json"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return build_tree(tmp_path / "install" / "_flavor_")


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "userdata" / "wowlab" / "store")


# ── acceptance: deterministic manifests ─────────────────────────────────────


def test_same_tree_same_clock_gives_byte_identical_manifest_files(
    source: Path, tmp_path: Path
) -> None:
    a = SnapshotStore(tmp_path / "store-a")
    b = SnapshotStore(tmp_path / "store-b")
    ma = a.create(source, SUBTREES, label="x", now=T0)
    mb = b.create(source, SUBTREES, label="x", now=T0)
    assert ma.id == mb.id
    bytes_a = manifest_file(a, ma.id).read_bytes()
    assert bytes_a == manifest_file(b, mb.id).read_bytes()
    assert bytes_a == manifest_bytes(ma)


def test_identical_trees_differ_only_in_id_timestamp_and_root(tmp_path: Path) -> None:
    one = build_tree(tmp_path / "one")
    # Built in a different order, in a different place: same tree.
    two = build_tree(tmp_path / "two", dict(reversed(list(FILES.items()))))
    store = SnapshotStore(tmp_path / "store")
    m1 = store.create(one, SUBTREES, now=T0)
    m2 = store.create(two, SUBTREES, now=T1)

    d1 = json.loads(manifest_file(store, m1.id).read_bytes())
    d2 = json.loads(manifest_file(store, m2.id).read_bytes())
    assert d1["id"] != d2["id"]
    assert d1["created_at"] != d2["created_at"]
    for key in ("id", "created_at", "install_root"):
        d1.pop(key)
        d2.pop(key)
    assert d1 == d2
    # The fingerprint half of the id is the tree, so it matches too.
    assert m1.fingerprint == m2.fingerprint

    # With id, timestamp and root fixed, the bytes are identical.
    pinned = {"id": m1.id, "created_at": m1.created_at, "install_root": m1.install_root}
    assert manifest_bytes(m2.model_copy(update=pinned)) == manifest_bytes(m1)


def test_manifest_encoding_is_sorted_compact_ascii_with_sorted_entries(
    source: Path, store: SnapshotStore
) -> None:
    (source / "WTF" / "Account" / "ACCT" / "naïve-ß.txt").write_bytes(b"x")
    m = store.create(source, SUBTREES, now=T0)
    raw = manifest_file(store, m.id).read_bytes()
    assert raw.endswith(b"\n")
    raw.decode("ascii")  # non-ASCII names are escaped, never locale-encoded
    data = json.loads(raw)
    assert raw == (json.dumps(data, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n")
    paths = [e["path"] for e in data["entries"]]
    assert paths == sorted(paths)
    assert "WTF/Account/ACCT/naïve-ß.txt" in paths
    assert data["subtrees"] == sorted(SUBTREES)


def test_manifest_records_what_the_spec_lists(source: Path, store: SnapshotStore) -> None:
    m = store.create(
        source,
        SUBTREES,
        label="before keybinds",
        flavor_folder="_flavor_",
        flavor_version="1.2.3.45678",
        client_running=True,
        now=T0,
    )
    assert m.id == f"20260921T120000.123456Z-{m.fingerprint}"
    assert m.created_at == "2026-09-21T12:00:00.123456Z"
    assert m.label == "before keybinds"
    assert m.install_root == str(source)
    assert (m.flavor_folder, m.flavor_version, m.client_running) == (
        "_flavor_",
        "1.2.3.45678",
        True,
    )
    entry = m.entry("WTF/Config.wtf")
    assert entry is not None
    assert entry.size == len(FILES["WTF/Config.wtf"])
    # NTFS resolves timestamps to 100 ns; a nanosecond-exact os.utime round
    # trip does not hold there, so this is a tolerance rather than equality.
    assert abs(entry.mtime_ns - FIXED_MTIME_NS) < 1000
    assert entry.sha256 is not None and len(entry.sha256) == 64
    if sys.platform != "win32":
        assert entry.mode == 0o644
    assert store.show(m.id) == m


def test_clock_must_be_aware_and_is_normalised_to_utc(source: Path, store: SnapshotStore) -> None:
    with pytest.raises(SnapshotError, match="timezone-aware"):
        store.create(source, SUBTREES, now=datetime(2026, 9, 21, 12, 0, 0))  # noqa: DTZ001
    local = T0.astimezone(timezone(timedelta(hours=-7)))
    assert store.create(source, SUBTREES, now=local).created_at == "2026-09-21T12:00:00.123456Z"


# ── acceptance: objects are stored once ─────────────────────────────────────


def test_unchanged_files_are_stored_once_across_snapshots(
    source: Path, store: SnapshotStore
) -> None:
    m1 = store.create(source, SUBTREES, now=T0)
    first = object_names(store)
    distinct = {e.sha256 for e in m1.entries}
    assert first == distinct
    # Addon.lua and Addon.lua.bak share content: two entries, one object.
    assert len(m1.entries) == len(distinct) + 1

    stamps = {p: p.stat().st_size for p in store.objects_dir.glob("*/*")}
    (source / "WTF" / "Config.wtf").write_bytes(b'SET portal "EU"\n')
    m2 = store.create(source, SUBTREES, now=T1)

    second = object_names(store)
    assert len(second) == len(first) + 1  # exactly the one new content
    assert {p: p.stat().st_size for p in store.objects_dir.glob("*/*") if p in stamps} == stamps
    assert len(store.list()) == 2
    assert m2.entry("WTF/Config.wtf") != m1.entry("WTF/Config.wtf")
    assert not list(store.tmp_dir.iterdir())


def test_objects_are_zlib_of_the_content_under_the_sha256_path(
    source: Path, store: SnapshotStore
) -> None:
    import hashlib

    m = store.create(source, SUBTREES, now=T0)
    content = FILES["Interface/AddOns/Thing/Thing.toc"]
    digest = hashlib.sha256(content).hexdigest()
    path = store.objects_dir / digest[:2] / digest[2:]
    assert zlib.decompress(path.read_bytes()) == content
    assert store.read_object(digest) == content
    assert store.read_file(m.id, "Interface/AddOns/Thing/Thing.toc") == content
    assert store.read_file(m.id, "Interface/AddOns/Thing/empty.txt") == b""


def test_a_file_larger_than_one_chunk_round_trips(tmp_path: Path, store: SnapshotStore) -> None:
    big = bytes(range(256)) * 9000 + b"tail"  # a little over two 1 MiB chunks
    root = build_tree(tmp_path / "big", {"WTF/big.lua": big})
    m = store.create(root, ["WTF"], now=T0)
    entry = m.entry("WTF/big.lua")
    assert entry is not None and entry.size == len(big)
    assert store.read_file(m.id, "WTF/big.lua") == big
    assert store.verify().ok


# ── what gets captured ──────────────────────────────────────────────────────


def test_only_the_named_subtrees_are_captured(source: Path, store: SnapshotStore) -> None:
    m = store.create(source, SUBTREES, now=T0)
    paths = [e.path for e in m.entries]
    assert paths == sorted(
        p for p in FILES if p.startswith(("WTF/", "Interface/AddOns/", "Fonts/"))
    )
    # `Fonts` does not exist in this tree: recorded, contributes nothing.
    assert "Fonts" in m.subtrees


def test_exclude_and_overlapping_subtrees(source: Path, store: SnapshotStore) -> None:
    m = store.create(
        source,
        ["Interface", "Interface/AddOns/Thing", "WTF/Config.wtf"],
        exclude=["Interface/AddOns"],
        now=T0,
    )
    assert [e.path for e in m.entries] == ["Interface/Icons/override.blp", "WTF/Config.wtf"]
    assert m.excluded == ("Interface/AddOns",)

    whole = store.create(source, ["."], exclude=["Data", "Logs"], now=T1)
    assert [e.path for e in whole.entries] == sorted(
        p for p in FILES if not p.startswith(("Data/", "Logs/"))
    )


@pytest.mark.parametrize(
    "bad",
    ["/etc", "../outside", "WTF/../../outside"],
    ids=["constructed-absolute", "constructed-parent", "constructed-nested-parent"],
)
def test_subtrees_cannot_leave_the_root(source: Path, store: SnapshotStore, bad: str) -> None:
    with pytest.raises(SnapshotError):
        store.create(source, [bad], now=T0)
    with pytest.raises(SnapshotError):
        store.create(source, ["WTF"], exclude=[bad], now=T0)
    assert not store.path.exists()


def test_subtrees_are_required_and_root_must_be_a_directory(
    source: Path, store: SnapshotStore
) -> None:
    with pytest.raises(SnapshotError, match="at least one subtree"):
        store.create(source, [], now=T0)
    with pytest.raises(SnapshotError, match="not a directory"):
        store.create(source / "WTF" / "Config.wtf", ["."], now=T0)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinks_are_recorded_and_never_followed(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    outside = build_tree(tmp_path / "outside", {"secret/Secret.lua": b"do not capture\n"})
    (source / "Interface" / "AddOns" / "Linked").symlink_to(outside / "secret")
    (source / "WTF" / "link.wtf").symlink_to("Config.wtf")
    m = store.create(source, SUBTREES, now=T0)

    linked = m.entry("Interface/AddOns/Linked")
    assert linked is not None
    assert (linked.kind, linked.sha256, linked.target) == ("symlink", None, str(outside / "secret"))
    assert m.entry("Interface/AddOns/Linked/Secret.lua") is None
    file_link = m.entry("WTF/link.wtf")
    assert file_link is not None and file_link.target == "Config.wtf"
    with pytest.raises(SnapshotError, match="symlink"):
        store.read_file(m.id, "WTF/link.wtf")
    for name in object_names(store):
        assert store.read_object(name) != b"do not capture\n"


# ── acceptance: diff ────────────────────────────────────────────────────────


def test_diff_reports_added_removed_changed(source: Path, store: SnapshotStore) -> None:
    a = store.create(source, SUBTREES, now=T0)
    (source / "WTF" / "Config.wtf").write_bytes(b'SET portal "EU"\n')
    (source / "WTF" / "Account" / "ACCT" / "bindings-cache.wtf").unlink()
    (source / "Fonts").mkdir()
    (source / "Fonts" / "FRIZQT__.TTF").write_bytes(b"font")
    # Touched but identical: not a change.
    os.utime(source / "Interface" / "AddOns" / "Thing" / "Thing.lua", ns=(1, 1))
    b = store.create(source, SUBTREES, now=T1)

    d = store.diff(a.id, b.id)
    assert (d.a, d.b) == (a.id, b.id)
    assert [e.path for e in d.added] == ["Fonts/FRIZQT__.TTF"]
    assert [e.path for e in d.removed] == ["WTF/Account/ACCT/bindings-cache.wtf"]
    assert [c.path for c in d.changed] == ["WTF/Config.wtf"]
    assert d.changed[0].before.sha256 != d.changed[0].after.sha256
    assert d.mode_changed == ()
    assert not d.is_empty

    back = store.diff(b.id, a.id)
    assert [e.path for e in back.added] == ["WTF/Account/ACCT/bindings-cache.wtf"]
    assert [e.path for e in back.removed] == ["Fonts/FRIZQT__.TTF"]
    assert [c.path for c in back.changed] == ["WTF/Config.wtf"]
    assert store.diff(a.id, a.id).is_empty


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_diff_reports_a_mode_only_change_separately(source: Path, store: SnapshotStore) -> None:
    a = store.create(source, SUBTREES, now=T0)
    (source / "WTF" / "Config.wtf").chmod(0o444)
    b = store.create(source, SUBTREES, now=T1)
    d = store.diff(a.id, b.id)
    assert d.changed == () and [c.path for c in d.mode_changed] == ["WTF/Config.wtf"]
    assert a.fingerprint != b.fingerprint


# ── acceptance: verify ──────────────────────────────────────────────────────


def _object_of(store: SnapshotStore, m: Manifest, rel: str) -> tuple[str, Path]:
    entry = m.entry(rel)
    assert entry is not None and entry.sha256 is not None
    return entry.sha256, store.object_path(entry.sha256)


def test_verify_passes_on_a_healthy_store(source: Path, store: SnapshotStore) -> None:
    m = store.create(source, SUBTREES, now=T0)
    report = store.verify()
    assert report.ok
    assert report.objects_checked == len({e.sha256 for e in m.entries})
    assert report.manifests_checked == 1


@pytest.mark.parametrize("damage", ["flip", "truncate", "swap", "garbage", "trailing"])
def test_verify_detects_a_corrupted_object_constructed(
    source: Path, store: SnapshotStore, damage: str
) -> None:
    m = store.create(source, SUBTREES, now=T0)
    name, path = _object_of(store, m, "WTF/Config.wtf")
    raw = path.read_bytes()
    if damage == "flip":
        mid = len(raw) // 2
        path.write_bytes(raw[:mid] + bytes([raw[mid] ^ 0xFF]) + raw[mid + 1 :])
    elif damage == "truncate":
        path.write_bytes(raw[:-4])
    elif damage == "swap":  # a valid zlib stream of the wrong content
        path.write_bytes(zlib.compress(b"something else entirely"))
    elif damage == "garbage":
        path.write_bytes(b"not zlib at all")
    else:
        path.write_bytes(raw + b"extra")

    report = store.verify()
    assert not report.ok
    assert report.corrupt_objects == (name,)
    assert report.missing_objects == () and report.invalid_manifests == ()
    with pytest.raises(ObjectCorruptError):
        store.read_file(m.id, "WTF/Config.wtf")


def test_verify_detects_a_missing_object_and_a_stray_file(
    source: Path, store: SnapshotStore
) -> None:
    m = store.create(source, SUBTREES, now=T0)
    name, path = _object_of(store, m, "WTF/Config.wtf")
    path.unlink()
    (store.objects_dir / "zz").mkdir()
    (store.objects_dir / "zz" / "not-an-object").write_bytes(b"?")

    report = store.verify()
    assert [(x.snapshot_id, x.path, x.sha256) for x in report.missing_objects] == [
        (m.id, "WTF/Config.wtf", name)
    ]
    assert report.corrupt_objects == ("zz/not-an-object",)
    with pytest.raises(ObjectCorruptError, match="missing"):
        store.read_object(name)


def test_verify_detects_an_edited_manifest_constructed(source: Path, store: SnapshotStore) -> None:
    m = store.create(source, SUBTREES, now=T0)
    path = manifest_file(store, m.id)
    data = json.loads(path.read_bytes())
    data["entries"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(data))

    report = store.verify()
    assert [x.name for x in report.invalid_manifests] == [path.name]
    assert "altered" in report.invalid_manifests[0].reason
    with pytest.raises(ManifestIntegrityError):
        store.show(m.id)


# ── acceptance: gc ──────────────────────────────────────────────────────────


def _orphan_by_deleting_a_snapshot(source: Path, store: SnapshotStore) -> tuple[Manifest, set[str]]:
    """Two snapshots, the second with two new contents; drop the second."""
    keep = store.create(source, SUBTREES, now=T0)
    (source / "WTF" / "Config.wtf").write_bytes(b"orphan one\n")
    (source / "WTF" / "new.wtf").write_bytes(b"orphan two\n")
    gone = store.create(source, SUBTREES, now=T1)
    before = object_names(store)
    manifest_file(store, gone.id).unlink()
    expected = {e.sha256 for e in gone.entries if e.sha256} - {
        e.sha256 for e in keep.entries if e.sha256
    }
    assert len(expected) == 2 and expected < before
    return keep, expected


def test_gc_dry_run_lists_exactly_the_unreferenced_objects_and_removes_nothing(
    source: Path, store: SnapshotStore
) -> None:
    _, expected = _orphan_by_deleting_a_snapshot(source, store)
    before = object_names(store)

    report = store.gc()  # dry-run is the default
    assert report.dry_run
    assert set(report.unreferenced) == expected
    assert list(report.unreferenced) == sorted(report.unreferenced)
    assert report.removed == ()
    assert report.unreferenced_bytes == sum(store.object_path(n).stat().st_size for n in expected)
    assert object_names(store) == before


def test_gc_real_run_removes_only_the_unreferenced_objects(
    source: Path, store: SnapshotStore
) -> None:
    keep, expected = _orphan_by_deleting_a_snapshot(source, store)
    before = object_names(store)
    planned = store.gc(dry_run=True)

    report = store.gc(dry_run=False)
    assert not report.dry_run
    assert report.unreferenced == planned.unreferenced
    assert set(report.removed) == expected
    assert object_names(store) == before - expected
    assert object_names(store) == {e.sha256 for e in keep.entries}
    assert store.verify().ok
    assert store.read_file(keep.id, "WTF/Config.wtf") == FILES["WTF/Config.wtf"]
    assert store.gc(dry_run=False).unreferenced == ()


def test_gc_with_nothing_unreferenced_is_a_no_op(source: Path, store: SnapshotStore) -> None:
    store.create(source, SUBTREES, now=T0)
    before = object_names(store)
    assert store.gc(dry_run=False).removed == ()
    assert object_names(store) == before
    assert SnapshotStore(store.path / "nowhere").gc(dry_run=False).unreferenced == ()


def test_gc_refuses_when_a_manifest_does_not_load_constructed(
    source: Path, store: SnapshotStore
) -> None:
    m = store.create(source, SUBTREES, now=T0)
    manifest_file(store, m.id).write_bytes(b"{ truncated")
    before = object_names(store)
    with pytest.raises(ManifestIntegrityError):
        store.gc(dry_run=False)
    assert object_names(store) == before


def test_gc_grace_period_spares_recent_objects(source: Path, store: SnapshotStore) -> None:
    _, expected = _orphan_by_deleting_a_snapshot(source, store)
    assert store.gc(dry_run=False, grace_seconds=3600).unreferenced == ()
    old = datetime.now(UTC).timestamp() - 7200
    aged = sorted(expected)[0]
    os.utime(store.object_path(aged), (old, old))
    assert store.gc(dry_run=False, grace_seconds=3600).removed == (aged,)


# ── acceptance: immutability ────────────────────────────────────────────────


def test_manifest_models_are_frozen(source: Path, store: SnapshotStore) -> None:
    m = store.create(source, SUBTREES, now=T0)
    with pytest.raises(ValidationError):
        m.label = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        m.entries[0].sha256 = "0" * 64  # type: ignore[misc]
    assert isinstance(m.entries, tuple) and isinstance(m.subtrees, tuple)


def test_no_operation_but_set_label_changes_a_manifest_file(
    source: Path, store: SnapshotStore
) -> None:
    m1 = store.create(source, SUBTREES, label="first", now=T0)
    path = manifest_file(store, m1.id)
    original = path.read_bytes()

    (source / "WTF" / "Config.wtf").write_bytes(b"changed\n")
    m2 = store.create(source, SUBTREES, now=T1)
    store.list()
    store.show(m1.id)
    store.diff(m1.id, m2.id)
    store.verify()
    store.gc(dry_run=False)
    store.read_file(m1.id, "WTF/Config.wtf")
    assert path.read_bytes() == original

    mutators = {
        name
        for name in dir(SnapshotStore)
        if not name.startswith("_") and callable(getattr(SnapshotStore, name))
    }
    assert mutators == {
        "create",
        "list",
        "show",
        "diff",
        "verify",
        "gc",
        "set_label",
        "read_object",
        "read_file",
        "resolve_id",
        "object_path",
    }


def test_set_label_changes_the_label_and_nothing_else(source: Path, store: SnapshotStore) -> None:
    m = store.create(source, SUBTREES, label="first", now=T0)
    other = store.create(source, SUBTREES, label="other", now=T1)
    other_bytes = manifest_file(store, other.id).read_bytes()
    before = json.loads(manifest_file(store, m.id).read_bytes())

    updated = store.set_label(m.id, "renamed")
    after = json.loads(manifest_file(store, m.id).read_bytes())
    assert updated.label == "renamed" and store.show(m.id) == updated
    assert after.pop("label") == "renamed" and before.pop("label") == "first"
    assert after == before
    assert manifest_file(store, m.id).read_bytes() == manifest_bytes(updated)
    assert manifest_file(store, other.id).read_bytes() == other_bytes
    assert len(store.list()) == 2 and store.verify().ok


def test_set_label_is_guarded_by_full_id(source: Path, store: SnapshotStore) -> None:
    m = store.create(source, SUBTREES, label="first", now=T0)
    path = manifest_file(store, m.id)
    original = path.read_bytes()

    with pytest.raises(SnapshotError):
        store.set_label(m.id[:8], "by prefix")  # show() takes a prefix; a write does not
    with pytest.raises(SnapshotNotFoundError):
        store.set_label(f"20260921T120000.123456Z-{'0' * 8}", "no such snapshot")
    assert path.read_bytes() == original

    # A manifest that no longer matches its id is refused, not relabelled.
    data = json.loads(original)
    data["entries"].pop()
    path.write_text(json.dumps(data))
    tampered = path.read_bytes()
    with pytest.raises(ManifestIntegrityError):
        store.set_label(m.id, "launder")
    assert path.read_bytes() == tampered


def test_an_existing_manifest_is_never_overwritten_by_create(
    source: Path, store: SnapshotStore
) -> None:
    m = store.create(source, SUBTREES, label="first", now=T0)
    original = manifest_file(store, m.id).read_bytes()
    # Same microsecond, same tree, same everything: a no-op.
    assert store.create(source, SUBTREES, label="first", now=T0) == m
    # Same id, different manifest: refused.
    with pytest.raises(SnapshotExistsError):
        store.create(source, SUBTREES, label="second", now=T0)
    assert manifest_file(store, m.id).read_bytes() == original
    assert len(store.list()) == 1


# ── acceptance: where the store lives, and L1 ───────────────────────────────


def test_default_store_is_under_the_user_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: Path
) -> None:
    real = platformdirs.user_data_path("wowlab")  # computes a path; touches nothing
    assert snapshot.default_store_path() == real / "store"
    assert SnapshotStore().path == real / "store"

    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    store = SnapshotStore()
    assert store.path == redirected / "store"
    m = store.create(source, SUBTREES, now=T0)
    assert manifest_file(store, m.id).is_file()
    assert manifest_file(store, m.id).is_relative_to(redirected)
    assert all(p.is_relative_to(redirected / "store") for p in store.path.rglob("*"))


def _set_read_only(root: Path, read_only: bool) -> None:
    paths = [root, *root.rglob("*")]
    for path in sorted(paths, reverse=read_only):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o555 if read_only else 0o755)
        else:
            path.chmod(0o444 if read_only else 0o644)


def test_create_writes_nothing_inside_a_read_only_source(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    _set_read_only(source, True)
    try:
        if sys.platform != "win32" and os.geteuid() != 0:
            with pytest.raises(PermissionError):  # the source really is read-only
                (source / "WTF" / "probe").write_bytes(b"")
        before = tree_state(source)
        parent_before = sorted(p.name for p in source.parent.iterdir())

        m = store.create(source, SUBTREES, now=T0)
        store.create(source, ["."], now=T1)
        store.verify()
        store.gc(dry_run=False)

        assert tree_state(source) == before
        assert sorted(p.name for p in source.parent.iterdir()) == parent_before
        assert len(m.entries) == 7
        assert store.read_file(m.id, "WTF/Config.wtf") == FILES["WTF/Config.wtf"]
    finally:
        _set_read_only(source, False)


@pytest.mark.parametrize("where", ["inside", "is-root", "contains"])
def test_store_and_source_must_not_overlap(source: Path, where: str) -> None:
    before = tree_state(source)
    if where == "inside":
        store, root = SnapshotStore(source / "WTF" / ".wowlab-store"), source
    elif where == "is-root":
        store, root = SnapshotStore(source), source
    else:
        store, root = SnapshotStore(source.parent), source
    with pytest.raises(StoreLocationError):
        store.create(root, ["."], now=T0)
    assert tree_state(source) == before


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_store_inside_the_source_is_still_refused(source: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(source / "WTF")
    with pytest.raises(StoreLocationError):
        SnapshotStore(alias / "store").create(source, ["."], now=T0)
    assert not (source / "WTF" / "store").exists()


def test_reading_an_absent_store_creates_nothing(store: SnapshotStore) -> None:
    assert store.list() == ()
    assert store.verify().ok
    assert store.gc().unreferenced == ()
    with pytest.raises(SnapshotNotFoundError):
        store.show("20260921")
    assert not store.path.exists()


# ── ids ─────────────────────────────────────────────────────────────────────


def test_list_is_oldest_first_and_show_accepts_a_unique_prefix(
    source: Path, store: SnapshotStore
) -> None:
    m2 = store.create(source, SUBTREES, now=T2)
    m0 = store.create(source, SUBTREES, now=T0)
    (source / "WTF" / "Config.wtf").write_bytes(b"changed\n")
    m1 = store.create(source, SUBTREES, now=T1)
    assert [m.id for m in store.list()] == [m0.id, m1.id, m2.id]

    assert store.show(m1.id[:13]) == m1  # 20260921T1205
    assert store.resolve_id(m2.id) == m2.id
    with pytest.raises(SnapshotNotFoundError, match="ambiguous"):
        store.show("20260921T12")
    with pytest.raises(SnapshotNotFoundError, match="no snapshot"):
        store.show("19990101")


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "..", "", "20260921T120000.123456Z-0000000g", "a/b", "*"],
    ids=lambda s: f"constructed-{s!r}",
)
def test_ids_cannot_address_anything_but_a_manifest(
    source: Path, store: SnapshotStore, hostile: str
) -> None:
    store.create(source, SUBTREES, now=T0)
    with pytest.raises(SnapshotError):
        store.show(hostile)
    with pytest.raises(SnapshotError):
        store.set_label(hostile, "x")
    with pytest.raises(SnapshotError):
        store.object_path(hostile)
