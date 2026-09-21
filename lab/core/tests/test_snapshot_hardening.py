"""Hostile-input and boundary tests for `wowlab_core.snapshot` (M10-10 review round 1).

Every input here is constructed: the store and its manifest are our own
format, and these are the hostile and boundary cases L8 allows to be built
by hand. Nothing touches a real install or the real user data directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from wowlab_core import snapshot
from wowlab_core.snapshot import (
    Entry,
    Manifest,
    ManifestIntegrityError,
    SnapshotError,
    SnapshotStore,
    StoreLocationError,
    manifest_bytes,
    tree_fingerprint,
)

T0 = datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


def _listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "Install"
    (root / "WTF").mkdir(parents=True)
    (root / "WTF" / "Config.wtf").write_bytes(b'SET portal "US"\n')
    (root / "WTF" / "other.wtf").write_bytes(b"other\n")
    return root


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "userdata" / "store")


# ── 1. overlap is judged by identity, not spelling ──────────────────────────


def _case_insensitive(tmp_path: Path) -> bool:
    (tmp_path / "CaseProbe").mkdir()
    return (tmp_path / "caseprobe").exists()


def test_case_variant_spellings_of_an_overlap_are_refused_constructed(
    source: Path, tmp_path: Path
) -> None:
    if not _case_insensitive(tmp_path):
        pytest.skip("case-sensitive volume: a different spelling is a different directory")
    before = _listing(source)
    variant = tmp_path / "install"  # same directory as `source`, other spelling

    # store inside source, store path spelled differently, at two depths
    for path in (variant / "store", variant / "wtf" / "deep" / "store", variant):
        with pytest.raises(StoreLocationError):
            SnapshotStore(path).create(source, ["."], now=T0)
    # source inside store: an existing store that contains the source
    (tmp_path / "Outer" / "manifests").mkdir(parents=True)
    inner = tmp_path / "Outer" / "tree"
    (inner / "WTF").mkdir(parents=True)
    with pytest.raises(StoreLocationError):
        SnapshotStore(tmp_path / "outer").create(inner, ["."], now=T0)

    assert _listing(source) == before
    assert _listing(inner) == ["WTF"]


def test_a_sibling_store_with_a_similar_name_is_allowed(source: Path, tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "Install-store")  # shares a string prefix, not a tree
    assert store.create(source, ["."], now=T0).entries


# ── 2. every string create() accepts loads again ────────────────────────────

HOSTILE_TEXT = [
    "bad\udc80label",
    "\ud83d\ude00 as two lone surrogates",
    "nul\x00inside",
    "looks escaped \x00DC80 but is literal",
    "\x00",
    "naïve ✓ 設定 😀",
]


@pytest.mark.parametrize("label", HOSTILE_TEXT, ids=lambda s: f"constructed-{s!r}")
def test_a_hostile_label_round_trips_through_the_store(
    source: Path, store: SnapshotStore, label: str
) -> None:
    m = store.create(source, ["WTF"], label=label, now=T0)
    assert store.show(m.id) == m and store.show(m.id).label == label
    raw = (store.manifests_dir / f"{m.id}.json").read_bytes()
    raw.decode("ascii")
    assert raw == manifest_bytes(m)
    assert Manifest.model_validate_json(raw) == m
    assert [x.id for x in store.list()] == [m.id]
    assert store.verify().ok
    store.gc(dry_run=False)

    relabelled = store.set_label(m.id, label + "\udcff")
    assert store.show(m.id) == relabelled
    assert store.set_label(m.id, "plain").label == "plain"


def test_ordinary_strings_are_written_unescaped(source: Path, store: SnapshotStore) -> None:
    m = store.create(source, ["WTF"], label="naïve ✓", now=T0)
    data = json.loads((store.manifests_dir / f"{m.id}.json").read_bytes())
    assert data["label"] == "naïve ✓"
    assert data["entries"][0]["path"] == "WTF/Config.wtf"


@pytest.mark.parametrize("text", HOSTILE_TEXT, ids=lambda s: f"constructed-{s!r}")
def test_hostile_text_round_trips_in_every_string_field(text: str) -> None:
    entries = (
        Entry(path=f"WTF/{text.replace(chr(0), '')}.txt", sha256="0" * 64, size=1),
        Entry(path="WTF/link", kind="symlink", target=text),
    )
    m = Manifest(
        id="20260101T000000.000000Z-00000000",
        created_at="2026-01-01T00:00:00.000000Z",
        label=text,
        install_root=f"/nowhere/{text}",
        flavor_folder=text,
        flavor_version=text,
        subtrees=(text,),
        excluded=(text,),
        entries=entries,
    )
    assert Manifest.model_validate_json(manifest_bytes(m)) == m


@posix_only
def test_an_undecodable_file_name_and_link_target_are_captured_constructed(
    source: Path, store: SnapshotStore
) -> None:
    made = []
    try:
        (source / "WTF" / "link").symlink_to(os.fsdecode(b"target-\x80"))
        made.append("link")
    except OSError:
        pass
    try:
        Path(os.fsdecode(os.fsencode(source / "WTF") + b"/name-\x80.txt")).write_bytes(b"x")
        made.append("name")
    except OSError:
        pass
    if not made:
        pytest.skip("this volume refuses undecodable names and link targets")

    m = store.create(source, ["WTF"], now=T0)
    assert store.show(m.id) == m and store.list() == (m,)
    if "link" in made:
        link = m.entry("WTF/link")
        assert link is not None and link.target == "target-\udc80"
    if "name" in made:
        assert store.read_file(m.id, "WTF/name-\udc80.txt") == b"x"


@pytest.mark.parametrize(
    "bad", ["\x00dc80", "\x00DC8", "\x00ZZZZ", "\x000041"], ids=lambda s: f"constructed-{s!r}"
)
def test_a_malformed_or_non_canonical_escape_does_not_load(
    source: Path, store: SnapshotStore, bad: str
) -> None:
    m = store.create(source, ["WTF"], label="x", now=T0)
    path = store.manifests_dir / f"{m.id}.json"
    data = json.loads(path.read_bytes())
    data["label"] = bad
    path.write_text(json.dumps(data, sort_keys=True))
    with pytest.raises(ManifestIntegrityError):
        store.show(m.id)


def test_create_never_publishes_a_manifest_that_would_not_load_constructed(
    source: Path, store: SnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = store.create(source, ["WTF"], now=T0)
    real = snapshot.manifest_bytes

    # An encoder fault: bytes that are not a manifest at all.
    monkeypatch.setattr(snapshot, "manifest_bytes", lambda m: b'{"label":"\\udc80"}\n')
    with pytest.raises(SnapshotError, match="would not load"):
        store.create(source, ["WTF"], now=T1)
    with pytest.raises(SnapshotError, match="would not load"):
        store.set_label(good.id, "new")

    # An encoder fault that still loads, but as something else.
    monkeypatch.setattr(
        snapshot, "manifest_bytes", lambda m: real(m.model_copy(update={"label": "else"}))
    )
    with pytest.raises(SnapshotError, match="round-trip"):
        store.create(source, ["WTF"], label="mine", now=T2)

    monkeypatch.undo()
    assert store.list() == (good,)
    assert store.verify().ok and not list(store.tmp_dir.iterdir())


# ── 3. a symlink is never followed, wherever it sits in a subtree path ──────


@posix_only
def test_a_subtree_through_a_symlinked_directory_is_refused_constructed(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    (outside / "a" / "b").mkdir(parents=True)
    (outside / "a" / "b" / "secret.txt").write_bytes(b"outside")
    (source / "WTF" / "link").symlink_to(outside)
    (source / "WTF" / "inner").symlink_to("../WTF")  # stays inside the root: still a link

    for subtree in ("WTF/link/a", "WTF/link/a/b/secret.txt", "WTF/inner/Config.wtf"):
        with pytest.raises(SnapshotError, match="symlink"):
            store.create(source, [subtree], now=T0)
    with pytest.raises(SnapshotError, match="symlink"):
        store.create(source, ["WTF", "WTF/link/a"], now=T0)
    assert store.list() == ()

    # Named last, or met while walking, it is recorded and not followed.
    m = store.create(source, ["WTF/link", "WTF"], now=T0)
    assert [(e.path, e.kind) for e in m.entries] == [
        ("WTF/Config.wtf", "file"),
        ("WTF/inner", "symlink"),
        ("WTF/link", "symlink"),
        ("WTF/other.wtf", "file"),
    ]
    # A subtree under something that does not exist is simply empty.
    assert store.create(source, ["WTF/absent/deeper"], now=T1).entries == ()


# ── 4 and 7. subtree spellings ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad", ["a\x00b", "\x00", "", "WTF/\x00"], ids=lambda s: f"constructed-{s!r}"
)
def test_nul_and_empty_subtrees_raise_the_typed_error(
    source: Path, store: SnapshotStore, bad: str
) -> None:
    with pytest.raises(SnapshotError):
        store.create(source, [bad], now=T0)
    with pytest.raises(SnapshotError):
        store.create(source, ["WTF"], exclude=[bad], now=T0)
    assert not store.path.exists()


def test_dot_is_the_explicit_spelling_of_the_root(source: Path, store: SnapshotStore) -> None:
    m = store.create(source, ["."], now=T0)
    assert m.subtrees == (".",)
    assert [e.path for e in m.entries] == ["WTF/Config.wtf", "WTF/other.wtf"]


# ── 5. a damaged object is never silently reused ────────────────────────────


@pytest.mark.parametrize("damage", ["garbage", "swap", "truncate", "empty"])
def test_create_heals_a_damaged_object_instead_of_reusing_it_constructed(
    source: Path, store: SnapshotStore, damage: str
) -> None:
    content = (source / "WTF" / "Config.wtf").read_bytes()
    first = store.create(source, ["WTF"], now=T0)
    path = store.object_path(hashlib.sha256(content).hexdigest())
    good = path.read_bytes()
    path.write_bytes(
        {
            "garbage": b"not zlib at all",
            "swap": zlib.compress(b"something else entirely"),
            "truncate": good[:-3],
            "empty": b"",
        }[damage]
    )
    assert not store.verify().ok

    second = store.create(source, ["WTF"], now=T1)

    assert store.verify().ok
    assert store.read_file(second.id, "WTF/Config.wtf") == content
    assert store.read_file(first.id, "WTF/Config.wtf") == content  # the old snapshot too
    assert zlib.decompress(path.read_bytes()) == content
    assert not list(store.tmp_dir.iterdir())


def test_a_sound_object_is_reused_without_being_rewritten(
    source: Path, store: SnapshotStore
) -> None:
    store.create(source, ["WTF"], now=T0)
    before = {p: (p.stat().st_ino, p.read_bytes()) for p in store.objects_dir.glob("*/*")}
    store.create(source, ["WTF"], now=T1)
    assert {p: (p.stat().st_ino, p.read_bytes()) for p in store.objects_dir.glob("*/*")} == before


# ── 6. entry paths are held to the root on load ─────────────────────────────

ESCAPING = ["../../escape.txt", "WTF/../../escape.txt", "/etc/passwd", "WTF//x", "./WTF/x", ""]


@pytest.mark.parametrize("path", [*ESCAPING, "WTF/a\x00b"], ids=lambda s: f"constructed-{s!r}")
def test_an_entry_cannot_name_a_path_outside_the_root(path: str) -> None:
    with pytest.raises(ValidationError):
        Entry(path=path, sha256="0" * 64, size=1)


@pytest.mark.parametrize("path", ESCAPING, ids=lambda s: f"constructed-{s!r}")
def test_a_manifest_with_an_escaping_path_and_a_valid_fingerprint_does_not_load(
    source: Path, store: SnapshotStore, path: str
) -> None:
    m = store.create(source, ["WTF"], now=T0)
    file = store.manifests_dir / f"{m.id}.json"
    data = json.loads(file.read_bytes())
    data["entries"][0]["path"] = path

    # Recompute the fingerprint the way the store does, so only the path check can object.
    forged = [Entry.model_construct(**e) for e in data["entries"]]
    new_id = m.id[:-8] + tree_fingerprint(data["subtrees"], data["excluded"], forged)
    data["id"] = new_id
    file.unlink()
    forged_file = store.manifests_dir / f"{new_id}.json"
    forged_file.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(ManifestIntegrityError, match="does not load"):
        store.show(new_id)
    with pytest.raises(ManifestIntegrityError):
        store.list()
    with pytest.raises(ManifestIntegrityError):
        store.gc(dry_run=False)
    assert [x.name for x in store.verify().invalid_manifests] == [forged_file.name]
