"""Profiles hardening (docs/LAB_PLAN.md §13.3, M11-12).

(a) `profiles._differs` walks from the install root to an entry one folder
at a time through directory descriptors, so a folder swapped for a link
between the check and the open is never read through; on Windows, where
`os.open` takes no `dir_fd`, it keeps the `lstat` walk and the junction check.
(b) `profile save` (and `profile show`) note every saved subtree root that is
itself a link: the profile holds the link, not what is behind it.

Every tree here is constructed (labelled in the test ids): a folder moved out
of the copied install and replaced by a symlink or a junction to it, a small
synthetic tree in `tmp_path`, and a folder swapped for a link part way
through a comparison. Nothing reads or writes a real install; no format is
parsed.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest
from test_cli import (
    FLAVOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_profiles import SV, _json_of, forever_pair  # noqa: F401  (autouse)

from wowlab_core import cli, profiles
from wowlab_core.snapshot import Entry, SnapshotStore

ON_WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(
    not ON_WINDOWS, reason="directory junctions exist only on Windows (_winapi.CreateJunction)"
)
posix_only = pytest.mark.skipif(ON_WINDOWS, reason="the dir_fd walk is POSIX-only")


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:  # Windows without the symlink privilege
        pytest.skip("cannot create a symlink here")


def _junction(link: Path, target: Path) -> None:
    import _winapi  # Windows only; the callers are skipped elsewhere

    _winapi.CreateJunction(str(target), str(link))


def _identities(top: Path) -> set[tuple[int, int]]:
    """`(st_dev, st_ino)` of `top` and everything under it."""
    return {(s.st_dev, s.st_ino) for s in (p.lstat() for p in (top, *top.rglob("*")))}


def _open_fds() -> set[str]:
    return {p.name for p in Path("/dev/fd").iterdir()}


# ─── (a) `_differs` never reads through a link ───────────────────────────────


class _Tree:
    """Constructed: `<root>/<Flavor>/Interface/AddOns/Linked/a.lua` inside the
    install, and `outside/a.lua` with other bytes beside it."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "install"
        self.flavor = "_flavor_"
        self.parent = self.root / self.flavor / "Interface" / "AddOns" / "Linked"
        self.parent.mkdir(parents=True)
        self.saved = b"-- saved\n"
        (self.parent / "a.lua").write_bytes(self.saved)
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        (self.outside / "a.lua").write_bytes(b"-- outside the install\n")
        self.entry = Entry(
            path=f"{self.flavor}/Interface/AddOns/Linked/a.lua",
            kind="file",
            sha256=hashlib.sha256(self.saved).hexdigest(),
            size=len(self.saved),
        )

    def differs(self) -> str:
        return profiles._differs(self.root, self.flavor, self.entry)


@pytest.fixture
def tree(tmp_path: Path) -> _Tree:
    return _Tree(tmp_path)


@pytest.fixture(params=["dir_fd", "lstat"])
def walk(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run a case through each walk: by directory descriptor (POSIX only) and
    the `lstat` walk Windows uses, forced here so POSIX CI covers it too."""
    if request.param == "dir_fd":
        if not profiles._DIR_FD_WALK:
            pytest.skip("no dir_fd walk on this platform")
    else:
        monkeypatch.setattr(profiles, "_DIR_FD_WALK", False)
    return str(request.param)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """`(st_dev, st_ino)` of everything `profiles` opens, by `fstat` of the descriptor."""
    seen: list[tuple[int, int]] = []
    real_open = os.open

    def spy(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        st = os.fstat(fd)
        seen.append((st.st_dev, st.st_ino))
        return fd

    monkeypatch.setattr(profiles.os, "open", spy)
    return seen


def _last_part(path: Any) -> str | None:
    if not isinstance(path, str | bytes | os.PathLike):
        return None
    text = os.fsdecode(os.fspath(path)).replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1]


@pytest.fixture
def swap_after_first_touch(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str, Path, Path], None]]:
    """Arm a race: the first `os.open`, `os.stat` or `os.lstat` naming `name`
    runs, and then `folder` is renamed aside (inside the install) and replaced
    by a symlink to `outside` before the caller's next step."""
    armed: dict[str, Any] = {}

    def arm(name: str, folder: Path, outside: Path) -> None:
        armed.update(name=name, folder=folder, outside=outside, done=False)

    def wrap(real: Callable[..., Any]) -> Callable[..., Any]:
        def inner(path: Any, *args: Any, **kwargs: Any) -> Any:
            hit = armed and not armed["done"] and _last_part(path) == armed["name"]
            result = real(path, *args, **kwargs)
            if hit:
                armed["done"] = True
                folder: Path = armed["folder"]
                folder.rename(folder.with_name(folder.name + ".real"))
                folder.symlink_to(armed["outside"], target_is_directory=True)
            return result

        return inner

    for name in ("open", "stat", "lstat"):
        monkeypatch.setattr(os, name, wrap(getattr(os, name)))
    yield arm
    assert armed.get("done"), "the race was never triggered"


@posix_only
def test_the_walk_uses_directory_descriptors_on_posix() -> None:
    assert profiles._DIR_FD_WALK


@posix_only
@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        ("Linked", "behind_link"),  # swapped after its check: O_NOFOLLOW refuses the open
        ("a.lua", "same"),  # swapped after its folder was opened: the descriptor holds
    ],
)
def test_a_parent_swapped_for_a_link_mid_walk_is_never_read_through_constructed(
    tree: _Tree,
    trigger: str,
    expected: str,
    swap_after_first_touch: Callable[[str, Path, Path], None],
    opened: list[tuple[int, int]],
) -> None:
    """Constructed race: right after `profiles` first looks at the folder
    (`Linked`) or at the file in it (`a.lua`), the folder is replaced by a
    symlink to a folder outside the install. Nothing outside is opened, no
    descriptor is left open, and the answer is about the folder it checked."""
    outside = _identities(tree.outside)
    before = _open_fds()
    swap_after_first_touch(trigger, tree.parent, tree.outside)
    result = tree.differs()
    assert result == expected
    assert not outside & set(opened), "read through a link outside the install"
    assert _open_fds() == before, "a directory descriptor was left open"
    assert (tree.outside / "a.lua").read_bytes() == b"-- outside the install\n"


@pytest.mark.parametrize("which", ["parent", "flavor"])
def test_a_linked_parent_or_flavor_folder_is_behind_a_link_constructed(
    walk: str, tree: _Tree, which: str, opened: list[tuple[int, int]]
) -> None:
    """Constructed: a folder on the entry's path, or the flavor folder itself,
    is a symlink to a copy outside the install holding the same bytes."""
    folder = tree.parent if which == "parent" else tree.root / tree.flavor
    copy = tree.outside / "copy"
    shutil.move(str(folder), str(copy))
    _symlink(folder, copy)
    outside = _identities(tree.outside)
    assert tree.differs() == "behind_link"
    assert not outside & set(opened)


def test_an_unchanged_entry_is_same_and_a_changed_one_differs_constructed(
    walk: str, tree: _Tree
) -> None:
    assert tree.differs() == "same"
    (tree.parent / "a.lua").write_bytes(b"-- changed\n")
    assert tree.differs() == "differs"
    (tree.parent / "a.lua").unlink()
    assert tree.differs() == "differs"
    tree.parent.rmdir()
    assert tree.differs() == "differs"


def test_a_file_where_a_folder_was_differs_constructed(walk: str, tree: _Tree) -> None:
    shutil.rmtree(tree.parent)
    tree.parent.write_bytes(b"not a folder\n")
    assert tree.differs() == "differs"


def test_a_file_swapped_for_a_link_differs_and_is_not_followed_constructed(
    walk: str, tree: _Tree, opened: list[tuple[int, int]]
) -> None:
    """Constructed: the entry itself is now a symlink to the outside file."""
    (tree.parent / "a.lua").unlink()
    try:
        (tree.parent / "a.lua").symlink_to(tree.outside / "a.lua")
    except OSError:
        pytest.skip("cannot create a symlink here")
    assert tree.differs() == "differs"
    assert not _identities(tree.outside) & set(opened)


def test_a_symlink_entry_is_compared_by_its_text_constructed(walk: str, tree: _Tree) -> None:
    try:
        (tree.parent / "l").symlink_to("a.lua")
    except OSError:
        pytest.skip("cannot create a symlink here")
    entry = Entry(path=f"{tree.flavor}/Interface/AddOns/Linked/l", kind="symlink", target="a.lua")
    assert profiles._differs(tree.root, tree.flavor, entry) == "same"
    other = entry.model_copy(update={"target": "b.lua"})
    assert profiles._differs(tree.root, tree.flavor, other) == "differs"


@windows_only
def test_a_junctioned_parent_is_behind_a_link_constructed(
    tree: _Tree, opened: list[tuple[int, int]]
) -> None:
    """Constructed (Windows): a folder on the path is a junction to a copy
    outside the install; the `lstat` walk's junction check stops there."""
    copy = tree.outside / "copy"
    shutil.move(str(tree.parent), str(copy))
    _junction(tree.parent, copy)
    assert tree.differs() == "behind_link"
    assert not _identities(tree.outside) & set(opened)


# ─── (b) `profile save` notes a subtree root that is itself a link ──────────


def _note(path: str) -> str:
    return profiles.LINKED_ROOT_NOTE.format(path=path)


def _link_out(folder: Path, outside: Path, make: Callable[[Path, Path], None]) -> None:
    shutil.move(str(folder), str(outside))
    make(folder, outside)


@pytest.mark.parametrize("rel", ["Interface/AddOns", SV])
def test_save_and_show_note_a_linked_subtree_root_constructed(
    root: Path, flavor: Path, tmp_path: Path, rel: str
) -> None:
    """Constructed: `Interface/AddOns` or the account's SavedVariables folder
    (roots the `addons` preset saves) is a symlink to a folder outside the
    install. The profile holds the link; save and show say so, in human and
    `--json` output."""
    _link_out(flavor / rel, tmp_path / "outside", _symlink)
    note = _note(f"{FLAVOR}/{rel}")
    human = ok("profile", "save", "mods", "--preset", "addons")
    assert note in human.stdout
    report = _json_of(cli.ProfileReport, "profile", "show", "mods")
    assert note in report.notes
    assert [n for n in report.notes if "is a link;" in n] == [note]
    held = [e for e in report.entries if e.path == f"{FLAVOR}/{rel}"]
    assert [e.kind for e in held] == ["symlink"]
    assert not [e for e in report.entries if e.path.startswith(f"{FLAVOR}/{rel}/")]
    assert note in ok("profile", "show", "mods").stdout


def test_save_json_notes_a_linked_explicit_subtree_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    """Constructed: an explicit `--subtree` naming an addon folder that is a symlink."""
    addon = flavor / "Interface/AddOns/Tool"
    addon.mkdir()
    (addon / "Tool.toc").write_bytes(b"## Title: constructed\n")
    _link_out(addon, tmp_path / "outside", _symlink)
    report = _json_of(
        cli.ProfileReport, "profile", "save", "tool", "--subtree", "Interface/AddOns/Tool"
    )
    assert _note(f"{FLAVOR}/Interface/AddOns/Tool") in report.notes


def test_no_link_note_without_a_linked_root_constructed(root: Path) -> None:
    report = _json_of(cli.ProfileReport, "profile", "save", "mods", "--preset", "addons")
    assert not [n for n in report.notes if "is a link;" in n]
    assert profiles.linked_roots(SnapshotStore().show(report.profile.snapshot_id)) == ()


def test_save_under_a_linked_wtf_is_refused_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    """Constructed: `WTF` itself is a symlink. No profile can have `WTF` as a
    root (`--subtree WTF` is too broad), so every preset root lies behind the
    link and `snapshot` refuses the save: nothing is saved, nothing behind the
    link is stored."""
    _link_out(flavor / "WTF", tmp_path / "outside", _symlink)
    result = run("profile", "save", "look", "--preset", "ui")
    assert result.exit_code == 1, result.output
    assert "passes through the symlink" in result.stderr
    assert _json_of(cli.ProfileListReport, "profile", "list").profiles == []


@windows_only
def test_save_notes_a_junctioned_addons_folder_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    """Constructed (Windows): `Interface/AddOns` is a junction to a folder outside."""
    _link_out(flavor / "Interface/AddOns", tmp_path / "outside", _junction)
    report = _json_of(cli.ProfileReport, "profile", "save", "mods", "--preset", "addons")
    assert _note(f"{FLAVOR}/Interface/AddOns") in report.notes


# ─── crafted manifest paths (M11-12 fix round 1) ─────────────────────────────


@pytest.mark.parametrize(
    "tail",
    [
        "D:/Windows/win.ini",  # a drive part
        "Interface/D:a.lua",  # drive-relative on Windows
        "Interface/a.lua:stream",  # an NTFS alternate data stream
        "\\\\server\\share\\a.lua",  # a UNC path in one part
        "Interface/..\\..\\outside\\a.lua",  # backslash traversal
        "\\Windows\\win.ini",  # rooted on the current drive
    ],
)
def test_a_crafted_part_is_never_opened_on_any_platform_constructed(
    walk: str, tree: _Tree, tail: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed hostile manifest: a part holding `\\` or `:` is valid in an
    `Entry` (both are ordinary POSIX name characters) but on Windows would make
    the joined path a drive, UNC, rooted or `..` path. `_differs` refuses it on
    every platform before touching the disk."""
    parts = [tree.flavor, *tail.split("/")]
    joined = PureWindowsPath("C:\\install").joinpath(*parts)
    assert (
        joined.drive != "C:"
        or not joined.as_posix().startswith("C:/install/")
        or ".." in joined.parts
        or ":" in "".join(joined.parts[1:])
    ), "the case would be harmless on Windows"
    entry = Entry(path=f"{tree.flavor}/{tail}", kind="file", sha256="0" * 64)
    touched: list[str] = []

    def spy(name: str) -> Callable[..., Any]:
        real = getattr(os, name)

        def inner(*args: Any, **kwargs: Any) -> Any:
            touched.append(name)
            return real(*args, **kwargs)

        return inner

    assert profiles._entry_parts(tree.flavor, entry) is None
    for name in ("open", "stat", "lstat"):
        monkeypatch.setattr(profiles.os, name, spy(name))
    assert profiles._differs(tree.root, tree.flavor, entry) == "differs"
    monkeypatch.undo()
    assert touched == []


@posix_only
def test_a_close_failure_mid_walk_is_differs_and_leaks_nothing_constructed(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: `os.close` of a parent descriptor fails once; `_differs`
    answers `differs` instead of raising, and the child is still closed."""
    real_close = os.close
    failed: list[int] = []

    def close_fails_once(fd: int) -> None:
        real_close(fd)
        if not failed:
            failed.append(fd)
            raise OSError("constructed close failure")

    before = _open_fds()
    monkeypatch.setattr(profiles.os, "close", close_fails_once)
    assert tree.differs() == "differs"
    monkeypatch.undo()
    assert failed
    assert _open_fds() == before
