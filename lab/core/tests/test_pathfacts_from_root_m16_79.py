"""`pathfacts._compare`, `explain`'s hash of the named file, opens from the
install root one folder at a time (M16-79), so a folder on the way that is a
link is refused rather than followed.

Constructed: a synthetic install in `tmp_path` whose file matches the
snapshot's copy byte for byte. A folder on the way (or the flavor folder, or
the file itself) is then replaced by a link to an identical copy elsewhere.
Followed, the copy would compare `same`; refused, the comparison is
`unreadable`, nothing behind the link is read, and `explain`'s line says the
file on disk could not be read. The store is a stand-in that lists the given
manifest; nothing is written anywhere.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

from wowlab_core import cli, pathfacts, snapshot

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"), reason="symlinks need a privilege"
)

FLAVOR = "0"  # digits only: nothing here names a real flavor
REL = "WTF/Account/x.lua"
DATA = b"constructed = { 1, 2, 3 }\n"
SID = "20261101T000000.000000Z-00000001"


class _Listed:
    """A store that lists one manifest (constructed)."""

    def __init__(self, manifest: snapshot.Manifest) -> None:
        self.manifest = manifest

    def list_lenient(self) -> snapshot.SnapshotListing:
        return snapshot.SnapshotListing(manifests=(self.manifest,), invalid=())

    def imported_ids(self) -> frozenset[str]:
        return frozenset()


def _install(tmp_path: Path) -> tuple[Path, _Listed]:
    root = tmp_path / "install-constructed"
    target = root / FLAVOR / REL
    target.parent.mkdir(parents=True)
    target.write_bytes(DATA)
    entry = snapshot.Entry(
        path=f"{FLAVOR}/{REL}", sha256=hashlib.sha256(DATA).hexdigest(), size=len(DATA)
    )
    manifest = snapshot.Manifest(
        id=SID,
        created_at="2026-11-01T00:00:00.000000Z",
        install_root=str(root),
        flavor_folder=FLAVOR,
        subtrees=(f"{FLAVOR}/WTF",),
        entries=(entry,),
    )
    return root, _Listed(manifest)


def _facts(root: Path, store: _Listed) -> pathfacts.SnapshotFacts:
    return pathfacts.snapshot_facts(
        store,  # type: ignore[arg-type]
        root,
        FLAVOR,
        REL,
        ["WTF"],
    )


def _link_out(folder: Path, elsewhere: Path) -> None:
    """Move `folder` to `elsewhere` and leave a link to it in its place."""
    shutil.move(folder, elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=folder.is_dir() or elsewhere.is_dir())


def _hashed(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count every `os.read` while the comparison runs."""
    reads: list[int] = []
    real = os.read

    def spy(fd: int, n: int) -> bytes:
        reads.append(fd)
        return real(fd, n)

    monkeypatch.setattr(os, "read", spy)
    return reads


def test_a_plain_path_still_compares_same_constructed(tmp_path: Path) -> None:
    root, store = _install(tmp_path)
    facts = _facts(root, store)
    assert (facts.holds, facts.live, facts.differs) == (True, "same", False)


@pytest.mark.parametrize(
    "linked",
    [FLAVOR, f"{FLAVOR}/WTF", f"{FLAVOR}/WTF/Account"],
    ids=["flavor-folder", "wtf", "account"],
)
@pytest.mark.parametrize("nofollow", [True, False], ids=["dir-fd-walk", "lstat-walk"])
def test_a_linked_folder_on_the_way_is_not_compared_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: str, nofollow: bool
) -> None:
    """The copy behind the link is the same bytes, so a comparison that
    followed the link would say `same`. Both walks refuse it: the `dir_fd`
    one (POSIX) and the `lstat` one Windows takes (`_O_NOFOLLOW` 0)."""
    if not nofollow:
        monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    root, store = _install(tmp_path)
    _link_out(root / linked, tmp_path / "elsewhere-constructed")
    assert (root / FLAVOR / REL).read_bytes() == DATA  # followed, it would match
    reads = _hashed(monkeypatch)
    facts = _facts(root, store)
    assert (facts.holds, facts.live, facts.differs) == (True, "unreadable", None)
    assert reads == []
    assert cli._snapshot_line(facts, "WTF/") == (
        f"In the latest snapshot ({SID}, 2026-11-01T00:00:00.000000Z): "
        "the file on disk could not be read"
    )


def test_the_walk_names_the_linked_folder_constructed(tmp_path: Path) -> None:
    """What `_compare` catches: the walk's `LinkedPathError`, with the
    folder named in M16-65's words."""
    root, _ = _install(tmp_path)
    account = root / FLAVOR / "WTF" / "Account"
    _link_out(account, tmp_path / "elsewhere-constructed")
    with pytest.raises(snapshot.LinkedPathError) as caught:
        snapshot.open_regular_file_from_root(root / FLAVOR / REL, root)
    assert caught.value.folder == account
    assert str(caught.value) == snapshot.linked_folder_words(root / FLAVOR / REL, account)


def test_a_file_that_is_a_link_is_not_compared_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, store = _install(tmp_path)
    _link_out(root / FLAVOR / REL, tmp_path / "elsewhere-constructed.lua")
    reads = _hashed(monkeypatch)
    facts = _facts(root, store)
    assert (facts.live, facts.differs) == ("unreadable", None)
    assert reads == []


def test_a_folder_swapped_after_explain_looked_is_not_compared_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race the ticket closes: the folders were real when the facts were
    gathered (the covering snapshot was chosen through them), and one is a
    link by the time the file is opened. The open is the only thing that
    can see it."""
    root, store = _install(tmp_path)
    real = pathfacts._compare

    def swapped_first(*args: object) -> pathfacts.Live:
        _link_out(root / FLAVOR / "WTF", tmp_path / "elsewhere-constructed")
        return real(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(pathfacts, "_compare", swapped_first)
    reads = _hashed(monkeypatch)
    facts = _facts(root, store)
    assert (facts.holds, facts.live, facts.differs) == (True, "unreadable", None)
    assert reads == []


def test_a_missing_file_is_still_missing_constructed(tmp_path: Path) -> None:
    root, store = _install(tmp_path)
    (root / FLAVOR / REL).unlink()
    assert _facts(root, store).live == "missing"
