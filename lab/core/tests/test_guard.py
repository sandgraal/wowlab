"""Graders for `wowlab_core.guard`, the write gate (M10-11T).

Written before `guard.py` exists, from `docs/LAB_PLAN.md` §6.10 (with §6.7 as
amended and §6.9 for the two modules it consumes), ADR-0021, ADR-0023 and
invariants L1, L2 and L7. Every test carries one marker line that M10-11
deletes; nothing else in this file is the implementer's to change.

Everything here is constructed, as the ticket requires: the install is a
synthetic tree under `tmp_path`, the snapshot store is a directory under
`tmp_path`, the user data directory is redirected, and the process table is
injected by replacing `psutil.process_iter` for the length of a test. No test
needs or touches a real install, the real user data directory or the real
process table.

The seam these graders hold `guard` to
--------------------------------------
§6.10 fixes `transaction(flavor, label=...)`, `tx.write(rel_path, data)`,
`tx.delete(rel_path)`, `tx.restore(snapshot_id, paths=None)`, `undo()` and
`history()`. It does not name the errors, the journal fields or the store
parameter, so they are pinned here, as small as they could be made:

- `guard.transaction(flavor, *, label="", store=None, dry_run=False)` is a
  context manager (a function or a class) that yields the transaction.
  `flavor` is anything with the attributes of `Flavor` in §6.1 (`guard` does
  not depend on M10-05, so it must not `isinstance`-check); `flavor.path` is
  the flavor folder, its parent is the install root, and every `rel_path` is
  `/`-separated and relative to `flavor.path`. A flavor whose folder is
  missing, has no `.flavor.info`, whose parent has no `.build.info`, or whose
  path is relative is refused on enter.
  `store: Path | None` is the store directory; `guard` constructs the
  `SnapshotStore` itself, and `None` means `snapshot.default_store_path()`.
  A store that overlaps the install (inside it, equal to it, or containing
  it) is refused on enter before anything is written anywhere (L1).
  There is no probe parameter. The client check is `wowlab_core.process`
  with its default probe, given the install root, the flavor folder and the
  executable names found in the flavor folder (`extra_names`); `unknown`
  and any exception out of the probe count as running.
- `guard.undo(*, store=None)` and `guard.history(*, store=None)`. `undo()`
  is itself a transaction against the flavor the most recent record names:
  the same flavor, store and client checks, a pre-write snapshot first, and
  a journal record of its own (label distinct from the one it undoes,
  `rolled_back=False`). So the bytes it discards are always held, and a
  second `undo()` undoes the undo ("most recent" stays literal; redo is
  free). The flavor path a record carries is untrusted: it is validated
  like any caller's flavor.
  Undo is journal-scoped (owner decision, 2026-09-22): it restores only the
  paths the undone transaction journaled, from that transaction's pre-write
  snapshot, and refuses (before writing anything) if any of those paths is
  forbidden, is a symlink entry in the snapshot, or has a snapshot entry
  whose hash disagrees with the journal's `before` (an entry present where
  `before` is `None`, or absent where it is not, disagrees too). Entries
  for paths it did not journal are not its business: an unchanged
  symlinked addon folder or a `release.sh` inside an addon does not stop
  an undo. It never creates a symlink.
  Design choice: because the snapshot comes first, restoring a path whose
  current bytes match neither the journal's `after` nor `before` is
  allowed (it is recoverable); that rule is not graded.
- Errors: `guard.GuardError` is the base of everything `guard` raises on
  purpose; `guard.ClientRunningError` (running, unknown, or a probe that
  raised) and `guard.PathNotAllowedError` are subclasses of it and not of
  each other. A write the operating system refuses surfaces as a
  `GuardError`, never a bare `OSError`.
- The public surface of the module is `transaction`, `undo`, `history`,
  `GuardError`, `ClientRunningError`, `PathNotAllowedError`, plus data-only
  Pydantic models (below). The transaction object has exactly the public
  methods `write`, `delete`, `restore`, the public data attribute `plan`
  and optionally read-only properties, and is dead after exit.
- A history record has `label`, `snapshot_id` (the pre-write snapshot),
  `rolled_back` and `paths`; each item of `paths` has `path` (as the caller
  spelled it), `before` and `after` (SHA-256 hex, `None` for absent).
- `tx.plan` is a tuple of items with `path`, `before`, `after` and either
  `size` or `data` (the bytes that would be written).
- The atomic replace is `os.replace` (or `Path.replace`, which calls it),
  looked up at call time; `os.fsync` runs on the temp file's descriptor
  first. Rollback happens on any `BaseException`, `KeyboardInterrupt`
  included, and everything restored from a snapshot or a journal is checked
  against the allowlist again on the restoring platform: the store is
  untrusted input at restore time, and a symlink is never created. That
  holds for every write route (`tx.write`, `tx.delete`, `tx.restore`,
  rollback and `undo()`): a link (symbolic, junction or hard) planted after
  an earlier check never redirects a write out of the install, and the
  parent directory is checked again between the check and the temp-file
  open and again before the rename.
- The journal entry is opened on enter, before the body runs, naming the
  pre-write snapshot, and every touched path is on record once the
  operation returns (a hard-killed transaction is still in `history()`).
  A refused enter and a dry run leave `history()` unchanged. `undo()` on a
  store with no record raises `GuardError` and creates nothing. Labels and
  paths round-trip through the journal exactly; one transaction is one
  record, whatever characters they hold.
- `rolled_back=True` is a claim that every touched path holds its
  pre-transaction bytes again; a rollback that could not finish says so.
- Write-ahead journal: before any mutation of a path in the install (the
  replace, rename or unlink that changes it), the journal entry naming that
  path and its `before` hash (absent for a created path) is on disk and
  `os.fsync`ed, as a literal string in a file under the store outside
  `objects/` and `manifests/`. So a process killed right after the
  mutation, with no rollback and no further journal write, is still undone
  by `undo()`.
- Permission bits: a file that exists keeps its mode through a write,
  restore or undo; a file that restore or undo recreates gets no bit its
  recorded mode lacks, and no execute bit. POSIX only.
- "Executable" means a path with any component whose suffix, compared
  case-insensitively, is one of `EXECUTABLE_SUFFIXES` below (this includes
  the bundle layouts `.app`, `.bundle`, `.framework`, `.plugin`, so a file
  inside one is refused too). No content is sniffed.
- No module global of `guard` other than dunders and `TYPE_CHECKING` is a
  `bool`: there is no switch to flip.
- Callers spell allowlist roots canonically (`WTF`, `Interface/AddOns`,
  `Fonts`); a respelling is refused on every volume. `guard` may map a
  canonical name to the spelling found on disk (an install with
  `Interface/Addons`); these graders neither require nor forbid that.
- Public data classes (history records, plan items) may be public Pydantic
  models, frozen, with no public methods of their own; the transaction may
  add read-only properties beside `plan`. Every value `tx.plan` or such a
  property returns is immutable (tuple, str, int, None, or a frozen model
  or dataclass whose fields are). Public non-callable module globals are
  modules, types, str, int, tuple, frozenset or Enum members only;
  `TYPE_CHECKING` is allowed only as imported from `typing` and used only
  as the test of an `if`.

Nothing is asserted about how the pre-write snapshot is rooted: entry paths
are matched by suffix, so `WTF/Config.wtf` and `_retail_/WTF/Config.wtf`
both satisfy these graders.
"""

from __future__ import annotations
import __future__

import ast
import builtins
import contextlib
import dataclasses
import enum
import hashlib
import importlib
import inspect
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import types
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from pydantic import BaseModel, ValidationError

from wowlab_core.snapshot import (
    Entry,
    Manifest,
    SnapshotError,
    SnapshotStore,
    manifest_bytes,
    tree_fingerprint,
)

MARKER = "M10-11 not implemented"

# ─── the synthetic install ───────────────────────────────────────────────────

FLAVOR_FOLDER = "_retail_"  # tests may name a flavor folder; the library may not (L6)

CONFIG = "WTF/Config.wtf"
BINDINGS = "WTF/Account/ACCT/bindings-cache.wtf"
SAVED = "WTF/Account/ACCT/SavedVariables/Addon.lua"
NEW_SAVED = "WTF/Account/ACCT/SavedVariables/New.lua"
TOC = "Interface/AddOns/Thing/Thing.toc"
ADDON_LUA = "Interface/AddOns/Thing/Thing.lua"
ICON = "Interface/Icons/override.blp"
FONT = "Fonts/FRIZQT__.TTF"

# Relative to the flavor folder; these are what a pre-write snapshot holds.
ALLOWLISTED_FILES: dict[str, bytes] = {
    CONFIG: b'SET portal "US"\nSET gxApi "metal"\n',
    BINDINGS: b"bind W MOVEFORWARD\n",
    SAVED: b'AddonDB = {\n\t["a"] = 1,\n}\n',
    TOC: b"## Interface: 0\n## Title: Thing\nThing.lua\n",
    ADDON_LUA: b"-- nothing\n",
    ICON: b"BLP2\x00\x01\x02",
    FONT: b"\x00\x01\x00\x00constructed-font",
}

# Relative to the flavor folder; never writable, never snapshotted by default.
FLAVOR_ONLY_FILES: dict[str, bytes] = {
    ".flavor.info": b"Product Flavor!STRING:0\nwow\n",
    "Wow.exe": b"MZ constructed client executable",
    "World of Warcraft.app/Contents/MacOS/World of Warcraft": b"\xcf\xfa\xed\xfe constructed",
    "Logs/Client.log": b"noise\n",
    "Cache/ADB/enUS/DBCache.bin": b"XFTH constructed",
    "Screenshots/WoWScrnShot_010126_000000.jpg": b"\xff\xd8 constructed",
}

# Relative to the install root.
ROOT_FILES: dict[str, bytes] = {
    ".build.info": (
        b"Branch!STRING:0|Active!DEC:1|Version!STRING:0|Product!STRING:0\nus|1|12.1.5.65432|wow\n"
    ),
    ".product.db": b"\x0a\x03constructed",
    "World of Warcraft Launcher.exe": b"MZ constructed launcher",
    "Data/data/data.001": b"\x00" * 64,
}

OUTSIDE_TARGET = b"outside the install; must never change\n"
MUST_NEVER_LAND = b"constructed: must never land"

NEW_CONFIG = b'SET portal "EU"\nSET gxApi "metal"\n'
NEWER_CONFIG = b'SET portal "KR"\n'
NEW_LUA = b'NewDB = {\n\t["b"] = 2,\n}\n'


class Flavor(BaseModel, frozen=True):
    """`Flavor` exactly as docs/LAB_PLAN.md §6.1 declares it. `install` (M10-05)
    is not a dependency of M10-11, so `guard` takes this shape structurally."""

    folder: str
    product: str
    version: str | None
    build: int | None
    build_key: str | None
    path: Path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _put(base: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def content(root: Path) -> dict[str, bytes | str]:
    """Every path under `root`: file bytes, `<dir>`, or `-> target` for a link.
    Links are never followed."""
    out: dict[str, bytes | str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in (*dirnames, *filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                out[rel] = "-> " + str(path.readlink())
            elif path.is_dir():
                out[rel] = "<dir>"
            else:
                out[rel] = path.read_bytes()
    return out


def strict_state(root: Path) -> dict[str, tuple[bytes | str, int, int]]:
    """`content` plus mtime and mode of every path, directories included, so a
    temp file that was created and removed again still shows."""
    out: dict[str, tuple[bytes | str, int, int]] = {}
    for rel, value in content(root).items():
        st = (root / rel).lstat()
        out[rel] = (value, st.st_mtime_ns, st.st_mode)
    return out


def case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe.tmp"
    probe.write_bytes(b"")
    try:
        return (directory / "caseprobe.TMP").exists()
    finally:
        probe.unlink()


def symlink_or_skip(link: Path, target: Path | str, *, is_dir: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this platform or account cannot create symlinks: {exc}")


def flavor_key(rel: str) -> str:
    """The `content(world)` key of a flavor-relative path."""
    return f"World of Warcraft/{FLAVOR_FOLDER}/{rel}"


# ─── the injected process table ──────────────────────────────────────────────

DENIED = object()


class FakeProcess:
    """The `ProcessLike` surface of `wowlab_core.process`, and nothing more."""

    def __init__(
        self, pid: int, *, name: Any = "", exe: Any = "", cmdline: Any = (), status: Any = "running"
    ) -> None:
        self._pid = pid
        self._values = {"name": name, "exe": exe, "cmdline": cmdline, "status": status}

    @property
    def pid(self) -> int:
        return self._pid

    def _answer(self, what: str) -> Any:
        value = self._values[what]
        if value is DENIED:
            raise psutil.AccessDenied(self._pid)
        if isinstance(value, BaseException):
            raise value
        return value

    def name(self) -> str:
        return str(self._answer("name"))

    def exe(self) -> str:
        return str(self._answer("exe"))

    def cmdline(self) -> list[str]:
        return list(self._answer("cmdline"))

    def status(self) -> str:
        return str(self._answer("status"))


ProcessTable = Callable[[], Iterable[Any]]


def table(*procs: FakeProcess) -> ProcessTable:
    return lambda: iter(procs)


def bystanders(tmp_path: Path) -> tuple[FakeProcess, ...]:
    """Processes that are plainly not a game client: readable name, and an
    executable path the operating system reported, outside the install."""
    elsewhere = tmp_path / "elsewhere"
    return (
        FakeProcess(101, name="python3", exe=str(elsewhere / "bin" / "python3")),
        FakeProcess(102, name="Finder", exe=str(elsewhere / "Finder.app" / "Finder")),
    )


def running_table(tmp_path: Path) -> ProcessTable:
    """A known client name, wherever its executable lives (§6.7)."""
    return table(
        *bystanders(tmp_path),
        FakeProcess(7300, name="Wow.exe", exe=str(tmp_path / "elsewhere" / "Wow.exe")),
    )


def use_probe(monkeypatch: pytest.MonkeyPatch, probe: ProcessTable) -> None:
    """`wowlab_core.process` reads `psutil.process_iter` per call, so this is
    the whole of the injection: the real process table is never listed."""
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: probe())


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _user_data_redirected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A default store, if `guard` ever reaches for one, lands in `tmp_path`."""
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected


@pytest.fixture(autouse=True)
def _real_process_table_is_never_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default for every test: listing processes is an error. A test that
    wants a table installs one with `use_probe` (the `idle` fixture does)."""

    def refuse(*a: Any, **k: Any) -> Iterator[Any]:
        pytest.fail("a grader reached the real process table")

    monkeypatch.setattr(psutil, "process_iter", refuse)


@pytest.fixture
def guard() -> Any:
    """The module under test. Imported here, not at the top of the file, so a
    missing `guard.py` fails each grader rather than collection."""
    return importlib.import_module("wowlab_core.guard")


@pytest.fixture
def world(tmp_path: Path) -> Path:
    """Everything a write could reach: the install and a directory beside it."""
    world = tmp_path / "world"
    root = world / "World of Warcraft"
    _put(root, ROOT_FILES)
    _put(root / FLAVOR_FOLDER, FLAVOR_ONLY_FILES)
    _put(root / FLAVOR_FOLDER, ALLOWLISTED_FILES)
    _put(world / "outside", {"target.txt": OUTSIDE_TARGET})
    return world


@pytest.fixture
def install_root(world: Path) -> Path:
    return world / "World of Warcraft"


@pytest.fixture
def flavor(install_root: Path) -> Flavor:
    return Flavor(
        folder=FLAVOR_FOLDER,
        product="wow",
        version="12.1.5.65432",
        build=65432,
        build_key=None,
        path=install_root / FLAVOR_FOLDER,
    )


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "store")


@pytest.fixture
def idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no game client in it."""
    use_probe(monkeypatch, table(*bystanders(tmp_path)))


def record_for(guard: Any, store: SnapshotStore, label: str) -> Any:
    matches = [r for r in guard.history(store=store.path) if r.label == label]
    assert len(matches) == 1, f"expected one journal record labelled {label!r}, got {matches!r}"
    return matches[0]


def changes(items: Iterable[Any]) -> dict[str, tuple[str | None, str | None]]:
    return {item.path: (item.before, item.after) for item in items}


def entry_for(manifest: Manifest, rel: str) -> Entry:
    """The manifest entry for a flavor-relative path, however the snapshot is rooted."""
    found = [e for e in manifest.entries if e.path == rel or e.path.endswith("/" + rel)]
    assert len(found) == 1, f"{rel} should appear once in snapshot {manifest.id}: {found!r}"
    return found[0]


def manifest_prefix(manifest: Manifest) -> str:
    """What precedes a flavor-relative path in this manifest's entry paths."""
    anchor = entry_for(manifest, CONFIG)
    return anchor.path[: -len(CONFIG)]


def refused_both_ops(guard: Any, tx: Any, rel: str, error: type[BaseException]) -> None:
    with pytest.raises(error):
        tx.write(rel, MUST_NEVER_LAND)
    with pytest.raises(error):
        tx.delete(rel)


# ─── positive controls: allowlisted paths are written ────────────────────────


@pytest.mark.parametrize(
    ("rel", "data"),
    [
        pytest.param(CONFIG, NEW_CONFIG, id="constructed-wtf-replace"),
        pytest.param(NEW_SAVED, NEW_LUA, id="constructed-wtf-create"),
        pytest.param(TOC, b"## Interface: 0\n## Title: Changed\n", id="constructed-addons-replace"),
        pytest.param(
            "Interface/AddOns/Thing/Extra.lua", b"-- extra\n", id="constructed-addons-create"
        ),
        pytest.param(FONT, b"\x00\x01\x00\x00other-font", id="constructed-fonts-replace"),
        pytest.param(
            "Fonts/ARIALN.TTF", b"\x00\x01\x00\x00new-font", id="constructed-fonts-create"
        ),
        pytest.param(ICON, b"BLP2\x09\x09", id="constructed-loose-interface-replace"),
        pytest.param(
            "Interface/Icons/another.blp", b"BLP2\x07", id="constructed-loose-interface-create"
        ),
        pytest.param(CONFIG, b"", id="constructed-empty-payload"),
    ],
)
def test_constructed_allowlisted_path_is_written(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, rel: str, data: bytes
) -> None:
    expected = content(world)
    expected[flavor_key(rel)] = data

    with guard.transaction(flavor, label="positive", store=store.path) as tx:
        tx.write(rel, data)

    assert (flavor.path / rel).read_bytes() == data
    # Exactly that one path changed: no temp file, journal, lock or backup was
    # left anywhere in the install or beside it (L1, L2).
    assert content(world) == expected


def test_constructed_write_creates_missing_parent_directories_inside_the_allowlist(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    # The API has no mkdir, and the tools ADR-0021 names (addon scaffolding,
    # restore of a deleted addon) need files in directories that do not exist.
    rel = "Interface/AddOns/Scaffolded/Scaffolded.toc"
    with guard.transaction(flavor, label="scaffold", store=store.path) as tx:
        tx.write(rel, b"## Title: Scaffolded\n")
    assert (flavor.path / rel).read_bytes() == b"## Title: Scaffolded\n"


def test_constructed_delete_removes_an_allowlisted_file(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    expected = content(world)
    del expected[flavor_key(BINDINGS)]

    with guard.transaction(flavor, label="delete", store=store.path) as tx:
        tx.delete(BINDINGS)

    assert not (flavor.path / BINDINGS).exists()
    assert content(world) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="permission bits are a POSIX thing")
def test_constructed_replaced_file_keeps_its_permission_bits(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    (flavor.path / CONFIG).chmod(0o640)
    with guard.transaction(flavor, label="mode", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert stat.S_IMODE((flavor.path / CONFIG).stat().st_mode) == 0o640


def test_constructed_write_to_a_hard_link_never_reaches_the_other_name(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """A hard link is one inode under two names: writing in place would change
    `outside/target.txt`. Refusing is fine; writing through a temp file and a
    rename is fine; touching the other name is not."""
    link = flavor.path / "WTF" / "hard.wtf"
    try:
        os.link(world / "outside" / "target.txt", link)
    except OSError as exc:
        pytest.skip(f"this platform or volume cannot hard-link: {exc}")

    with (
        contextlib.suppress(guard.GuardError),
        guard.transaction(flavor, label="hard-link", store=store.path) as tx,
    ):
        tx.write("WTF/hard.wtf", MUST_NEVER_LAND)

    assert (world / "outside" / "target.txt").read_bytes() == OUTSIDE_TARGET


# ─── the store ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "where",
    [
        pytest.param("install-root", id="constructed-under-the-install-root"),
        pytest.param("flavor", id="constructed-under-the-flavor-folder"),
        pytest.param("wtf", id="constructed-under-an-allowlisted-subtree"),
        pytest.param("symlink", id="constructed-through-a-symlink-from-outside"),
        pytest.param("install-root-itself", id="constructed-the-install-root-itself"),
        pytest.param("flavor-itself", id="constructed-the-flavor-folder-itself"),
        pytest.param("world", id="constructed-a-directory-containing-the-install"),
    ],
)
def test_constructed_store_inside_the_install_is_refused_on_enter(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, idle: None, where: str
) -> None:
    """L1: nothing but the transaction's own writes lands in an install, and
    the store is not one of those. A store that contains the install would
    snapshot itself and is refused the same way."""
    if where == "install-root":
        location = install_root / "store"
    elif where == "flavor":
        location = flavor.path / "store"
    elif where == "wtf":
        location = flavor.path / "WTF" / "store"
    elif where == "install-root-itself":
        location = install_root
    elif where == "flavor-itself":
        location = flavor.path
    elif where == "world":
        location = world
    else:
        location = world / "storelink"
        symlink_or_skip(location, install_root / "store", is_dir=True)
    before = strict_state(world)
    entered = False

    with (
        pytest.raises(guard.GuardError),
        guard.transaction(flavor, label="bad-store", store=location) as tx,
    ):
        entered = True
        tx.write(CONFIG, NEW_CONFIG)

    assert not entered
    assert strict_state(world) == before


def test_constructed_symlinked_install_root_is_written_and_still_guarded(
    guard: Any, world: Path, install_root: Path, store: SnapshotStore, idle: None
) -> None:
    """The real macOS layout: `/Applications/World of Warcraft` is a symlink
    to a volume. A flavor reached through a link above the install root is
    an ordinary flavor; a link below the root is still an escape."""
    link = world / "WoW-link"
    symlink_or_skip(link, install_root, is_dir=True)
    linked = Flavor(
        folder=FLAVOR_FOLDER,
        product="wow",
        version="12.1.5.65432",
        build=65432,
        build_key=None,
        path=link / FLAVOR_FOLDER,
    )
    symlink_or_skip(install_root / FLAVOR_FOLDER / "WTF" / "escape", world / "outside", is_dir=True)
    expected = content(world)
    expected[flavor_key(CONFIG)] = NEW_CONFIG

    with guard.transaction(linked, label="via-link", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        refused_both_ops(guard, tx, "WTF/escape/target.txt", guard.PathNotAllowedError)
        refused_both_ops(guard, tx, "WTF/escape", guard.PathNotAllowedError)
        refused_both_ops(guard, tx, "../.build.info", guard.PathNotAllowedError)

    assert (install_root / FLAVOR_FOLDER / CONFIG).read_bytes() == NEW_CONFIG
    assert content(world) == expected
    assert (world / "outside" / "target.txt").read_bytes() == OUTSIDE_TARGET


def test_constructed_default_store_is_under_the_user_data_directory(
    guard: Any, world: Path, flavor: Flavor, idle: None, _user_data_redirected: Path
) -> None:
    """`store=None` means the store of §6.9, under the (here redirected) user
    data directory: never inside the install, never beside it."""
    expected = content(world)
    expected[flavor_key(CONFIG)] = NEW_CONFIG

    with guard.transaction(flavor, label="default-store", store=None) as tx:
        tx.write(CONFIG, NEW_CONFIG)

    assert content(world) == expected
    default_store = SnapshotStore(_user_data_redirected / "wowlab" / "store")
    assert default_store.path.is_dir()
    assert len(default_store.list()) == 1
    assert {r.label for r in guard.history()} == {"default-store"}


# ─── the client check ────────────────────────────────────────────────────────


def _raising_probe() -> Iterable[Any]:
    raise RuntimeError("constructed: the probe itself broke")


def _probe_that_dies_midway(tmp_path: Path) -> ProcessTable:
    def probe() -> Iterator[Any]:
        yield from bystanders(tmp_path)
        raise PermissionError("constructed: process listing denied part way")

    return probe


def _refusing_tables(tmp_path: Path, install_root: Path) -> dict[str, ProcessTable]:
    elsewhere = tmp_path / "elsewhere"
    return {
        # §6.7: a known client name, wherever the executable lives.
        "running-by-name": running_table(tmp_path),
        # §6.7 as amended: an unlisted executable name is only caught by its
        # path, so guard has to hand the process check the install root it is
        # writing to.
        "running-unlisted-name-under-the-flavor-folder": table(
            FakeProcess(
                7002,
                name="ForeverClient.exe",
                exe=str(install_root / FLAVOR_FOLDER / "ForeverClient.exe"),
            )
        ),
        "running-unlisted-name-at-the-install-root": table(
            FakeProcess(7003, name="ForeverClient.exe", exe=str(install_root / "ForeverClient.exe"))
        ),
        # §6.7 as amended: a readable, unlisted name with a denied exe() is not
        # reported unless the caller passes the names discovery found. The
        # synthetic flavor folder holds `ForeverClient.exe` in this case.
        "running-unlisted-name-exe-denied-but-present-on-disk": table(
            *bystanders(tmp_path), FakeProcess(7005, name="ForeverClient.exe", exe=DENIED)
        ),
        # Nothing could be read about the process: unknown, which counts as running.
        "unknown-access-denied": table(
            *bystanders(tmp_path), FakeProcess(7004, name=DENIED, exe=DENIED, cmdline=DENIED)
        ),
        # A probe that raises is a refusal: not a crash, and not a pass.
        "probe-raises": _raising_probe,
        "probe-raises-midway": _probe_that_dies_midway(tmp_path),
        # An error psutil did not classify and `process` does not catch.
        "process-method-raises-valueerror": table(
            *bystanders(tmp_path),
            FakeProcess(7006, name=ValueError("constructed: unclassified"), exe=str(elsewhere)),
        ),
    }


REFUSING_TABLES = (
    "running-by-name",
    "running-unlisted-name-under-the-flavor-folder",
    "running-unlisted-name-at-the-install-root",
    "running-unlisted-name-exe-denied-but-present-on-disk",
    "unknown-access-denied",
    "probe-raises",
    "probe-raises-midway",
    "process-method-raises-valueerror",
)


@pytest.mark.parametrize("which", [pytest.param(w, id=f"constructed-{w}") for w in REFUSING_TABLES])
def test_constructed_transaction_refuses_when_the_client_is_running_or_unknown(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
    which: str,
) -> None:
    if which == "running-unlisted-name-exe-denied-but-present-on-disk":
        (flavor.path / "ForeverClient.exe").write_bytes(b"MZ constructed unlisted client")
    use_probe(monkeypatch, _refusing_tables(tmp_path, install_root)[which])
    before = strict_state(world)
    entered = False

    with (
        pytest.raises(guard.ClientRunningError),
        guard.transaction(flavor, label="refused", store=store.path) as tx,
    ):
        entered = True
        tx.write(CONFIG, NEW_CONFIG)

    assert not entered, "the body of a refused transaction must never run"
    assert strict_state(world) == before
    assert store.list() == (), "a refused enter takes no snapshot"
    assert list(guard.history(store=store.path)) == [], "and opens no journal entry"


def test_constructed_client_refusal_is_a_guard_error_and_not_a_path_refusal(guard: Any) -> None:
    assert issubclass(guard.ClientRunningError, guard.GuardError)
    assert issubclass(guard.PathNotAllowedError, guard.GuardError)
    assert not issubclass(guard.ClientRunningError, guard.PathNotAllowedError)
    assert not issubclass(guard.PathNotAllowedError, guard.ClientRunningError)
    assert not issubclass(guard.GuardError, OSError)


def test_constructed_client_check_uses_the_process_modules_default_probe(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no probe parameter (§6.7 as amended: injection is a test seam;
    a public one would be a flag that skips the client check, ADR-0021). The
    only way to a different answer is what `psutil.process_iter` returns."""
    assert "process_iter" not in inspect.signature(guard.transaction).parameters
    assert "process_iter" not in inspect.signature(guard.undo).parameters

    use_probe(monkeypatch, running_table(tmp_path))
    before = strict_state(world)
    with (
        pytest.raises(guard.ClientRunningError),
        guard.transaction(flavor, label="default-probe", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
    assert strict_state(world) == before

    # Positive control through the same default: no client, so the write lands.
    use_probe(monkeypatch, table(*bystanders(tmp_path)))
    with guard.transaction(flavor, label="default-probe-idle", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG


FORBIDDEN_IMPORTS = {"psutil", "ctypes", "subprocess", "_winapi", "dotenv", "platformdirs"}
FORBIDDEN_OS_NAMES = {
    "environ",
    "environb",
    "getenv",
    "getenvb",
    "putenv",
    "unsetenv",
    "expandvars",  # os.path.expandvars reads the environment through a string
}


def test_constructed_guard_never_touches_psutil_or_the_environment_itself(guard: Any) -> None:
    """Process listing belongs to `wowlab_core.process` (ADR-0023, §6.7 as
    amended: production callers use the default probe and never wrap psutil
    objects); the store location belongs to `wowlab_core.snapshot`; and no
    environment variable may steer the gate (ADR-0021), so `guard` has no
    reason to read one."""
    tree = ast.parse(Path(guard.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    os_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module in ("os", "os.path"):
                os_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute) and (
            node.attr == "expandvars"
            or (isinstance(node.value, ast.Name) and node.value.id == "os")
        ):
            os_names.add(node.attr)
    assert imported.isdisjoint(FORBIDDEN_IMPORTS), sorted(imported & FORBIDDEN_IMPORTS)
    assert os_names.isdisjoint(FORBIDDEN_OS_NAMES), sorted(os_names & FORBIDDEN_OS_NAMES)
    assert "wowlab_core" in imported or any(
        isinstance(node, ast.ImportFrom) and node.level > 0 for node in ast.walk(tree)
    ), "guard must reach the process check and the store through wowlab_core"


@pytest.mark.parametrize("which", [pytest.param(w, id=f"constructed-{w}") for w in REFUSING_TABLES])
def test_constructed_undo_refuses_while_the_client_runs(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    which: str,
) -> None:
    if which == "running-unlisted-name-exe-denied-but-present-on-disk":
        (flavor.path / "ForeverClient.exe").write_bytes(b"MZ constructed unlisted client")
    with guard.transaction(flavor, label="to-undo", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    before = strict_state(world)
    history = list(guard.history(store=store.path))
    snapshots = store.list()

    use_probe(monkeypatch, _refusing_tables(tmp_path, install_root)[which])
    with pytest.raises(guard.ClientRunningError):
        guard.undo(store=store.path)

    assert strict_state(world) == before
    assert list(guard.history(store=store.path)) == history, "a refused undo is not recorded"
    assert store.list() == snapshots, "a refused undo takes no snapshot"


def test_constructed_undo_with_nothing_to_undo_is_a_typed_error_and_creates_nothing(
    guard: Any, tmp_path: Path, world: Path, idle: None, _user_data_redirected: Path
) -> None:
    before = strict_state(world)
    empty = tmp_path / "empty-store"
    with pytest.raises(guard.GuardError) as refused:
        guard.undo(store=empty)
    assert not isinstance(refused.value, guard.ClientRunningError)
    assert not empty.exists()
    with pytest.raises(guard.GuardError):
        guard.undo()
    assert strict_state(world) == before
    assert not _user_data_redirected.exists()


# ─── the flavor ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "defect",
    [
        pytest.param("missing-folder", id="constructed-folder-missing"),
        pytest.param("no-flavor-info", id="constructed-no-flavor-info"),
        pytest.param("no-build-info", id="constructed-parent-has-no-build-info"),
        pytest.param("relative-path", id="constructed-relative-path"),
    ],
)
def test_constructed_flavor_that_is_not_a_flavor_is_refused(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    _user_data_redirected: Path,
    defect: str,
) -> None:
    """§6.1: a flavor is a `_*_` folder with `.flavor.info` inside an install,
    which is a directory with `.build.info`. Anything else is not a place
    `guard` writes."""
    if defect == "missing-folder":
        flavor = flavor.model_copy(update={"path": install_root / "_missing_"})
    elif defect == "no-flavor-info":
        (flavor.path / ".flavor.info").unlink()
    elif defect == "no-build-info":
        (install_root / ".build.info").unlink()
    else:
        flavor = flavor.model_copy(update={"path": Path(FLAVOR_FOLDER)})
    before = strict_state(world)
    entered = False

    with (
        pytest.raises(guard.GuardError),
        guard.transaction(flavor, label="not-a-flavor", store=store.path) as tx,
    ):
        entered = True
        tx.write(CONFIG, NEW_CONFIG)

    assert not entered
    assert strict_state(world) == before
    assert store.list() == ()
    assert list(guard.history(store=store.path)) == []
    assert not _user_data_redirected.exists()


def test_constructed_flavor_folder_that_is_a_symlink_out_of_the_install_is_refused(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """A flavor folder inside the install that is a link to a directory
    elsewhere (holding a `.flavor.info`) is not a flavor of this install."""
    elsewhere = world / "outside" / "_x_"
    _put(elsewhere, {".flavor.info": FLAVOR_ONLY_FILES[".flavor.info"], CONFIG: b"constructed\n"})
    link = install_root / "_linked_"
    symlink_or_skip(link, elsewhere, is_dir=True)
    linked = flavor.model_copy(update={"folder": "_linked_", "path": link})
    before = strict_state(world)
    entered = False

    with (
        pytest.raises(guard.GuardError),
        guard.transaction(linked, label="linked-flavor", store=store.path) as tx,
    ):
        entered = True
        tx.write(CONFIG, NEW_CONFIG)

    assert not entered
    assert strict_state(world) == before
    assert store.list() == ()
    assert list(guard.history(store=store.path)) == []


# ─── the allowlist ───────────────────────────────────────────────────────────

# What the module docstring calls an executable: any path component with one
# of these suffixes, compared case-insensitively. No content sniffing.
EXECUTABLE_SUFFIXES = (
    ".exe",
    ".dll",
    ".dylib",
    ".so",
    ".scr",
    ".com",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".command",
    ".app",
    ".bundle",
    ".plugin",
    ".framework",
    ".vbs",
    ".js",
    ".wsf",
    ".hta",
    ".msi",
    ".lnk",
    ".cpl",
    ".jar",
)

# `<WORLD>`, `<FLAVOR>` and `<FLAVOR_FOLDER>` are replaced at run time.
FORBIDDEN_PATHS: tuple[tuple[str, str], ...] = (
    # Data/, executables and the agent's files (§6.10, L7).
    ("data-existing-file", "../Data/data/data.001"),
    ("data-new-file", "../Data/new.bin"),
    ("data-inside-the-flavor-folder", "Data/new.bin"),
    ("client-executable-windows", "Wow.exe"),
    ("client-executable-mac-bundle", "World of Warcraft.app/Contents/MacOS/World of Warcraft"),
    ("launcher-at-the-install-root", "../World of Warcraft Launcher.exe"),
    ("build-info", "../.build.info"),
    ("product-db", "../.product.db"),
    ("flavor-info", ".flavor.info"),
    # Anything at the install root, and the roots themselves.
    ("install-root-new-file", "../notes.txt"),
    ("install-root-itself", ".."),
    ("flavor-root-itself", "."),
    ("empty-path", ""),
    ("flavor-root-new-file", "notes.txt"),
    # Subtrees of a flavor that are not on the allowlist.
    ("logs", "Logs/Client.log"),
    ("cache", "Cache/ADB/enUS/DBCache.bin"),
    ("screenshots", "Screenshots/new.jpg"),
    ("errors", "Errors/crash.txt"),
    # Names that only start like an allowlisted subtree.
    ("prefix-lookalike-wtf", "WTFx/Config.wtf"),
    ("prefix-lookalike-wtf-bak", "WTF.bak/Config.wtf"),
    ("prefix-lookalike-interface", "Interface.old/AddOns/Thing/Thing.toc"),
    ("prefix-lookalike-fonts", "Fontsy/FRIZQT__.TTF"),
    # Executables inside the allowlist: the client loads nothing from there,
    # and a tool that writes one is not a tool this repository ships (L7).
    ("executable-dll-in-addons", "Interface/AddOns/Thing/Thing.dll"),
    ("executable-exe-in-addons", "Interface/AddOns/Thing/Thing.exe"),
    ("executable-dylib-in-fonts", "Fonts/x.dylib"),
    ("executable-so-in-wtf", "WTF/x.so"),
    ("executable-app-bundle-in-addons", "Interface/AddOns/Thing/x.app/Contents/MacOS/x"),
    ("executable-scr", "Interface/AddOns/Thing/x.scr"),
    ("executable-com", "Interface/AddOns/Thing/x.com"),
    ("executable-bat", "WTF/x.bat"),
    ("executable-cmd", "WTF/x.cmd"),
    ("executable-ps1", "WTF/x.ps1"),
    ("executable-sh", "Fonts/x.sh"),
    ("executable-dll-case-variant", "Interface/AddOns/Thing/THING.DLL"),
    ("executable-command", "WTF/x.command"),
    ("executable-command-case-variant", "WTF/x.COMMAND"),
    ("executable-bundle", "Interface/AddOns/Thing/x.bundle"),
    ("executable-inside-a-bundle", "Interface/AddOns/Thing/x.bundle/Contents/MacOS/x"),
    ("executable-plugin", "Interface/AddOns/Thing/x.plugin"),
    ("executable-framework", "Interface/AddOns/Thing/x.framework"),
    ("executable-inside-a-framework", "Interface/AddOns/Thing/x.framework/Versions/A/x"),
    ("executable-vbs", "WTF/x.vbs"),
    ("executable-js", "Interface/AddOns/Thing/x.js"),
    ("executable-wsf", "WTF/x.wsf"),
    ("executable-hta", "WTF/x.hta"),
    ("executable-msi", "Fonts/x.msi"),
    ("executable-lnk", "WTF/x.lnk"),
    ("executable-cpl", "WTF/x.cpl"),
    ("executable-jar", "Interface/AddOns/Thing/x.jar"),
    ("executable-jar-case-variant", "Interface/AddOns/Thing/x.Jar"),
    # The allowlist roots and directories: the API is per file.
    ("allowlist-root-wtf", "WTF"),
    ("allowlist-root-wtf-trailing-slash", "WTF/"),
    ("allowlist-root-interface", "Interface"),
    ("allowlist-root-addons", "Interface/AddOns"),
    ("allowlist-root-fonts", "Fonts"),
    ("allowlist-root-dot-slash", "./WTF"),
    ("existing-directory", "WTF/Account"),
    ("double-slash", "WTF//Config.wtf"),
    ("file-with-trailing-slash", "WTF/Config.wtf/"),
    # `..` traversal that starts inside the allowlist.
    ("traversal-to-build-info", "WTF/../../.build.info"),
    ("traversal-to-flavor-info", "WTF/../.flavor.info"),
    ("traversal-to-executable", "Interface/AddOns/../../Wow.exe"),
    ("traversal-to-data", "Interface/AddOns/../../../Data/data/data.001"),
    ("traversal-out-of-the-install", "WTF/Account/../../../../outside/target.txt"),
    ("traversal-to-logs", "Fonts/../Logs/Client.log"),
    # `..` that resolves back inside the allowlist is still not a plain path.
    ("traversal-back-into-wtf", "WTF/../WTF/Config.wtf"),
    ("traversal-through-the-install-root", "../<FLAVOR_FOLDER>/WTF/Config.wtf"),
    ("traversal-addons-to-icons", "Interface/AddOns/../Icons/x.blp"),
    # Absolute paths, outside and inside the install.
    ("absolute-outside-existing", "<WORLD>/outside/target.txt"),
    ("absolute-outside-new", "<WORLD>/outside/new.txt"),
    ("absolute-forbidden-inside", "<FLAVOR>/Wow.exe"),
    ("absolute-even-when-allowlisted", "<FLAVOR>/WTF/Config.wtf"),
    # Windows spellings. On POSIX each is one odd file name at the flavor
    # root; on Windows each is a traversal, a drive or a drive-relative path.
    ("backslash-traversal", "WTF\\..\\..\\.build.info"),
    ("backslash-parent", "..\\.build.info"),
    ("drive-absolute-backslash", "C:\\Windows\\win.ini"),
    ("drive-absolute-slash", "C:/Windows/win.ini"),
    ("drive-relative", "C:WTF\\Config.wtf"),
    # Hostile input.
    ("nul-byte", "WTF/Con\x00fig.wtf"),
)


def _expand(rel: str, world: Path, flavor: Flavor) -> str:
    return (
        rel.replace("<WORLD>", str(world))
        .replace("<FLAVOR>", str(flavor.path))
        .replace("<FLAVOR_FOLDER>", flavor.path.name)
    )


@pytest.mark.parametrize("op", ["write", "delete"])
@pytest.mark.parametrize(
    "rel", [pytest.param(rel, id=f"constructed-{name}") for name, rel in FORBIDDEN_PATHS]
)
def test_constructed_path_outside_the_allowlist_is_refused(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, rel: str, op: str
) -> None:
    target = _expand(rel, world, flavor)
    before = strict_state(world)

    with guard.transaction(flavor, label="forbidden", store=store.path) as tx:
        with pytest.raises(guard.PathNotAllowedError):
            if op == "write":
                tx.write(target, MUST_NEVER_LAND)
            else:
                tx.delete(target)
        # Positive control in the same transaction: the refusal above was
        # about the path, not about the transaction, the probe or the store.
        tx.write(CONFIG, NEW_CONFIG)

    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    after = strict_state(world)
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    # Only the control write shows: its file, and the mtime of its directory.
    assert changed <= {flavor_key(CONFIG), flavor_key("WTF")}, sorted(changed)


@pytest.mark.parametrize(
    "rel",
    [
        pytest.param(f"Interface/AddOns/Thing/x{spelled}", id=f"constructed-{spelled}")
        for suffix in EXECUTABLE_SUFFIXES
        for spelled in (suffix, suffix.upper())
    ],
)
def test_constructed_every_executable_suffix_is_refused_in_either_case(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, rel: str
) -> None:
    before = strict_state(world)
    with guard.transaction(flavor, label="executable", store=store.path) as tx:
        refused_both_ops(guard, tx, rel, guard.PathNotAllowedError)
    assert strict_state(world) == before


def test_constructed_refusal_that_propagates_rolls_back_the_writes_before_it(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    before = content(world)
    with (
        pytest.raises(guard.PathNotAllowedError),
        guard.transaction(flavor, label="refused-midway", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        tx.write("../.build.info", MUST_NEVER_LAND)
    assert content(world) == before
    assert record_for(guard, store, "refused-midway").rolled_back is True


def test_constructed_overlong_name_component_is_a_typed_error(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    rel = "WTF/" + "a" * 300 + ".wtf"
    before = strict_state(world)
    with guard.transaction(flavor, label="long", store=store.path) as tx:
        with pytest.raises(guard.GuardError) as refused:
            tx.write(rel, MUST_NEVER_LAND)
        assert not isinstance(refused.value, (OSError, guard.ClientRunningError))
    assert strict_state(world) == before


CASE_VARIANTS_OF_FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("build-info-upper", "../.BUILD.INFO"),
    ("product-db-mixed", "../.Product.DB"),
    ("flavor-info-mixed", ".Flavor.Info"),
    ("executable-upper", "WOW.EXE"),
    ("data-lower", "../data/data/data.001"),
    ("data-upper", "../DATA/DATA/DATA.001"),
    ("traversal-through-lowercase-allowlist", "wtf/../wow.exe"),
    ("logs-lower", "logs/client.log"),
)


@pytest.mark.parametrize("op", ["write", "delete"])
@pytest.mark.parametrize(
    "rel",
    [pytest.param(rel, id=f"constructed-{name}") for name, rel in CASE_VARIANTS_OF_FORBIDDEN],
)
def test_constructed_case_variant_of_a_forbidden_path_is_refused(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, rel: str, op: str
) -> None:
    """Every one of these is outside the allowlist on any volume; on a
    case-insensitive one it is also the forbidden file itself."""
    before = strict_state(world)
    with (
        guard.transaction(flavor, label="case", store=store.path) as tx,
        pytest.raises(guard.PathNotAllowedError),
    ):
        if op == "write":
            tx.write(rel, MUST_NEVER_LAND)
        else:
            tx.delete(rel)
    assert strict_state(world) == before


RESPELLED_ALLOWLIST_ROOTS: tuple[tuple[str, str], ...] = (
    ("wtf-lower", "wtf/Config.wtf"),
    ("wtf-mixed", "Wtf/Config.wtf"),
    ("fonts-lower", "fonts/ARIALN.TTF"),
    ("interface-lower", "interface/AddOns/Thing/Thing.toc"),
    ("addons-lower", "Interface/addons/Thing/Thing.toc"),
)


@pytest.mark.parametrize("op", ["write", "delete"])
@pytest.mark.parametrize(
    "rel",
    [pytest.param(rel, id=f"constructed-{name}") for name, rel in RESPELLED_ALLOWLIST_ROOTS],
)
def test_constructed_respelled_allowlist_root_is_refused_on_every_volume(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, rel: str, op: str
) -> None:
    """The allowlist names the subtrees as the client spells them. On a
    case-insensitive volume a respelling lands on the same files under a name
    the journal and snapshot do not know; on a case-sensitive one it creates a
    sibling tree the client never reads. Neither is a write the gate makes."""
    before = strict_state(world)
    with (
        guard.transaction(flavor, label="respelled", store=store.path) as tx,
        pytest.raises(guard.PathNotAllowedError),
    ):
        if op == "write":
            tx.write(rel, MUST_NEVER_LAND)
        else:
            tx.delete(rel)
    assert strict_state(world) == before


CASE_COLLISIONS: tuple[tuple[str, str], ...] = (
    ("file-name", "WTF/config.wtf"),
    ("new-file-under-a-respelled-directory", "WTF/account/ACCT/New.lua"),
)


@pytest.mark.parametrize(
    "rel", [pytest.param(rel, id=f"constructed-{name}") for name, rel in CASE_COLLISIONS]
)
def test_constructed_case_collision_with_an_existing_path_is_refused(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    rel: str,
) -> None:
    """§6.10 "case-collision tricks": on a case-insensitive volume a respelled
    path lands on the existing file while the journal and the snapshot know it
    under another name, so a later rollback by name could delete the original."""
    if not case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive filesystem (default macOS volumes, Windows)")
    before = strict_state(world)
    with (
        guard.transaction(flavor, label="collision", store=store.path) as tx,
        pytest.raises(guard.PathNotAllowedError),
    ):
        tx.write(rel, MUST_NEVER_LAND)
    assert strict_state(world) == before


# ─── symlinks and junctions ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("dir-link-out-of-the-install", id="constructed-dir-link-out-of-the-install"),
        pytest.param("dir-link-itself", id="constructed-dir-link-itself"),
        pytest.param("file-link-out-of-the-install", id="constructed-file-link-out-of-the-install"),
        pytest.param("dir-link-to-data", id="constructed-dir-link-to-data"),
        pytest.param("file-link-to-the-executable", id="constructed-file-link-to-the-executable"),
    ],
)
def test_constructed_symlink_that_escapes_the_allowlist_is_refused(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    case: str,
) -> None:
    wtf = flavor.path / "WTF"
    if case == "dir-link-out-of-the-install":
        symlink_or_skip(wtf / "escape", world / "outside", is_dir=True)
        rel = "WTF/escape/target.txt"
    elif case == "dir-link-itself":
        symlink_or_skip(wtf / "escape", world / "outside", is_dir=True)
        rel = "WTF/escape"
    elif case == "file-link-out-of-the-install":
        symlink_or_skip(wtf / "escape.wtf", world / "outside" / "target.txt", is_dir=False)
        rel = "WTF/escape.wtf"
    elif case == "dir-link-to-data":
        symlink_or_skip(wtf / "data", install_root / "Data", is_dir=True)
        rel = "WTF/data/data/data.001"
    else:
        symlink_or_skip(wtf / "client", flavor.path / "Wow.exe", is_dir=False)
        rel = "WTF/client"
    before = content(world)

    with guard.transaction(flavor, label="symlink", store=store.path) as tx:
        refused_both_ops(guard, tx, rel, guard.PathNotAllowedError)

    assert content(world) == before
    assert (world / "outside" / "target.txt").read_bytes() == OUTSIDE_TARGET


def test_constructed_allowlisted_subtree_that_is_itself_an_escaping_symlink_is_refused(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    fonts = flavor.path / "Fonts"
    (fonts / "FRIZQT__.TTF").unlink()
    fonts.rmdir()
    symlink_or_skip(fonts, world / "outside", is_dir=True)
    before = content(world)

    # Refused at the write, or already at the pre-write snapshot (the store
    # refuses a subtree that is only reachable through a link). Either way
    # nothing lands on the far side of the link.
    with (
        pytest.raises((guard.GuardError, SnapshotError)),
        guard.transaction(flavor, label="linked-subtree", store=store.path) as tx,
    ):
        tx.write("Fonts/pwned.ttf", MUST_NEVER_LAND)

    assert content(world) == before
    assert not (world / "outside" / "pwned.ttf").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only: NTFS junctions")
def test_constructed_windows_junction_that_escapes_the_install_is_refused(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """A junction is not a symlink to `stat.S_ISLNK` on Python 3.12, so a check
    built on `is_symlink()` alone walks straight through it. Runs in the
    `lab (windows)` CI job; creating a junction needs no privilege."""
    junction = flavor.path / "WTF" / "junction"
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(world / "outside")],
        capture_output=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"mklink /J failed: {made.stderr!r}")
    outside_before = content(world / "outside")

    with guard.transaction(flavor, label="junction", store=store.path) as tx:
        refused_both_ops(guard, tx, "WTF/junction/pwned.txt", guard.PathNotAllowedError)
        refused_both_ops(guard, tx, "WTF/junction/target.txt", guard.PathNotAllowedError)
        refused_both_ops(guard, tx, "WTF/junction", guard.PathNotAllowedError)

    assert content(world / "outside") == outside_before


# ─── snapshot first ──────────────────────────────────────────────────────────


def test_constructed_snapshot_is_taken_on_enter_before_the_first_write(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    assert store.list() == ()

    with guard.transaction(flavor, label="snapshot-first", store=store.path) as tx:
        taken = store.list()
        assert len(taken) == 1, "the pre-write snapshot exists before any operation"
        pre = taken[0]
        assert store.read_object(entry_for(pre, CONFIG).sha256 or "") == ALLOWLISTED_FILES[CONFIG]
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)

    # The snapshot still holds the bytes that are no longer on disk.
    assert store.read_object(entry_for(pre, CONFIG).sha256 or "") == ALLOWLISTED_FILES[CONFIG]
    assert store.read_object(entry_for(pre, BINDINGS).sha256 or "") == ALLOWLISTED_FILES[BINDINGS]
    assert record_for(guard, store, "snapshot-first").snapshot_id == pre.id


def test_constructed_pre_write_snapshot_covers_the_default_subtrees_and_nothing_else(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """§6.9 default subtrees: `WTF/`, `Interface/AddOns/`, `Fonts/` and loose
    `Interface/` files; never `Data/`, `Cache/`, `Logs/`, `Screenshots/`."""
    with guard.transaction(flavor, label="coverage", store=store.path):
        pass
    pre = store.show(record_for(guard, store, "coverage").snapshot_id)
    prefix = manifest_prefix(pre)
    assert {e.path.removeprefix(prefix) for e in pre.entries} == set(ALLOWLISTED_FILES)
    for entry in pre.entries:
        assert entry.sha256 == sha(ALLOWLISTED_FILES[entry.path.removeprefix(prefix)])


def test_constructed_rollback_survives_a_store_gc_during_the_open_transaction(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """Rollback is "from the pre-write snapshot" (§6.10), so what it needs has
    to be held by a manifest, not by loose objects a `gc` would collect."""
    before = content(world)
    with (
        pytest.raises(RuntimeError, match="constructed failure"),
        guard.transaction(flavor, label="gc", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
        store.gc(dry_run=False)
        raise RuntimeError("constructed failure")
    assert content(world) == before


# ─── the journal ─────────────────────────────────────────────────────────────


def test_constructed_journal_records_before_and_after_hashes_for_every_touched_path(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    with guard.transaction(flavor, label="journal", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(NEW_SAVED, NEW_LUA)
        tx.delete(BINDINGS)

    record = record_for(guard, store, "journal")
    assert changes(record.paths) == {
        CONFIG: (sha(ALLOWLISTED_FILES[CONFIG]), sha(NEW_CONFIG)),
        NEW_SAVED: (None, sha(NEW_LUA)),
        BINDINGS: (sha(ALLOWLISTED_FILES[BINDINGS]), None),
    }
    assert record.rolled_back is False
    assert store.show(record.snapshot_id).id == record.snapshot_id


def test_constructed_journal_entry_is_opened_on_enter_and_names_each_path_as_it_goes(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """§6.10: "open a journal entry under the store" on enter. A guard that
    writes the journal only at exit leaves a hard-killed transaction with
    nothing on record."""
    with guard.transaction(flavor, label="ahead", store=store.path) as tx:
        opened = [r for r in guard.history(store=store.path) if r.label == "ahead"]
        assert len(opened) == 1, "the record exists before the first operation"
        (pre,) = store.list()
        assert opened[0].snapshot_id == pre.id
        tx.write(CONFIG, NEW_CONFIG)
        after_one = record_for(guard, store, "ahead")
        assert CONFIG in [item.path for item in after_one.paths]
        tx.delete(BINDINGS)
        after_two = record_for(guard, store, "ahead")
        assert {CONFIG, BINDINGS} <= {item.path for item in after_two.paths}
    assert len(list(guard.history(store=store.path))) == 1


INJECTED_LABEL = 'x"}\n{"label":"evil"'
INJECTED_PATH = 'WTF/a"\nb.wtf'


def test_constructed_journal_holds_hostile_labels_and_paths_exactly(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """A label or path that looks like journal syntax is refused, or
    round-trips exactly as one record; it never becomes a second record."""
    before = content(world)
    try:
        with (
            guard.transaction(flavor, label=INJECTED_LABEL, store=store.path) as tx,
            contextlib.suppress(guard.GuardError),
        ):
            tx.write(INJECTED_PATH, NEW_LUA)
    except guard.GuardError as refused:
        assert not isinstance(refused, guard.ClientRunningError)
        assert list(guard.history(store=store.path)) == []
        assert content(world) == before
        return

    records = list(guard.history(store=store.path))
    assert len(records) == 1, [r.label for r in records]
    assert records[0].label == INJECTED_LABEL
    assert [item.path for item in records[0].paths] in ([], [INJECTED_PATH])
    assert "evil" not in {r.label for r in records}


def test_constructed_journal_lists_a_path_once_with_its_first_before_and_last_after(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    with guard.transaction(flavor, label="twice", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(CONFIG, NEWER_CONFIG)
    record = record_for(guard, store, "twice")
    assert [item.path for item in record.paths] == [CONFIG]
    assert changes(record.paths) == {CONFIG: (sha(ALLOWLISTED_FILES[CONFIG]), sha(NEWER_CONFIG))}


def test_constructed_journal_lives_with_the_store_and_leaves_the_store_healthy(
    guard: Any,
    tmp_path: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    _user_data_redirected: Path,
) -> None:
    assert list(guard.history(store=store.path)) == []

    with guard.transaction(flavor, label="first", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    with guard.transaction(flavor, label="second", store=store.path) as tx:
        tx.write(CONFIG, NEWER_CONFIG)

    # Read back through a second store object: the journal is on disk, under
    # the store it was given, and not in memory or anywhere else.
    assert {r.label for r in guard.history(store=store.path)} == {"first", "second"}
    assert list(guard.history(store=tmp_path / "another-store")) == []
    assert not _user_data_redirected.exists(), "an injected store means the default is not used"

    # The journal must not break the store's own contracts (§6.9): every
    # manifest still loads, objects verify, and gc still runs.
    assert store.verify().ok
    assert len(store.list()) >= 1
    store.gc(dry_run=True)


# ─── atomic replace ──────────────────────────────────────────────────────────


class SimulatedCrashError(BaseException):
    """Not an `Exception`: nothing in `guard` may catch it and carry on."""


def _is_under(path: Path, ancestor: Path) -> bool:
    return ancestor.resolve() in path.resolve().parents


FileId = tuple[int, int]


def _file_id(st: os.stat_result) -> FileId:
    return (st.st_dev, st.st_ino)


class ReplaceSpy:
    """Stands in for `os.replace`, `os.rename` and `os.fsync`.

    Replace calls whose target lies inside the install are recorded (with what
    both sides held at that instant, and whether the source had been fsynced);
    the `fail_on`-th of them raises `error` (and every later one, with
    `persistent`, so a retry cannot succeed), and `before_call`, if given,
    runs just before the real replace. Every other call goes through. Calls
    made with `src_dir_fd` / `dst_dir_fd` are resolved through the descriptor
    so a guard using that defence is recorded, not failed.
    """

    def __init__(
        self,
        install_root: Path,
        *,
        fail_on: int = 0,
        persistent: bool = False,
        error: BaseException | None = None,
        before_call: Callable[[int, Path, Path], None] | None = None,
    ) -> None:
        self.install_root = install_root
        self.fail_on = fail_on
        self.persistent = persistent
        self.error = error
        self.before_call = before_call
        self.fsynced: set[FileId] = set()
        self.seen: list[dict[str, Any]] = []
        self._dirs: dict[FileId, Path] = {}
        self._replace = os.replace
        self._fsync = os.fsync
        self._fdatasync = getattr(os, "fdatasync", os.fsync)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "replace", self.replace)
        monkeypatch.setattr(os, "rename", self.replace)
        monkeypatch.setattr(os, "fsync", self.fsync)
        if hasattr(os, "fdatasync"):  # not on every platform (macOS has none)
            monkeypatch.setattr(os, "fdatasync", self.fdatasync)

    def fsync(self, fd: Any) -> None:
        # `os.fsync` accepts a descriptor or any object with `fileno()`.
        self.fsynced.add(_file_id(os.fstat(fd if isinstance(fd, int) else fd.fileno())))
        self._fsync(fd)

    def fdatasync(self, fd: Any) -> None:
        self.fsynced.add(_file_id(os.fstat(fd if isinstance(fd, int) else fd.fileno())))
        self._fdatasync(fd)

    def _dir_of(self, fd: int) -> Path:
        key = _file_id(os.fstat(fd))
        if key not in self._dirs:
            for directory in (self.install_root, *self.install_root.rglob("*")):
                if directory.is_dir() and not directory.is_symlink():
                    self._dirs[_file_id(directory.stat())] = directory
        if key not in self._dirs:
            pytest.fail("os.replace was given a directory descriptor outside the install")
        return self._dirs[key]

    def _path(self, arg: Any, dir_fd: int | None) -> Path:
        path = Path(os.fsdecode(arg))
        if dir_fd is not None and not path.is_absolute():
            path = self._dir_of(dir_fd) / path
        return path

    def replace(
        self, src: Any, dst: Any, *, src_dir_fd: int | None = None, dst_dir_fd: int | None = None
    ) -> None:
        source = self._path(src, src_dir_fd)
        target = self._path(dst, dst_dir_fd)
        if _is_under(target, self.install_root):
            number = len(self.seen) + 1
            self.seen.append(
                {
                    "src": source,
                    "dst": target,
                    "src_bytes": source.read_bytes() if source.is_file() else None,
                    "dst_bytes": target.read_bytes() if target.is_file() else None,
                    "src_fsynced": source.exists() and _file_id(source.stat()) in self.fsynced,
                }
            )
            if self.before_call is not None:
                self.before_call(number, source, target)
            failing = number == self.fail_on or (self.persistent and number > self.fail_on > 0)
            if failing and self.error is not None:
                raise self.error
        self._replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)


def test_constructed_crash_between_temp_write_and_rename_leaves_the_original_intact(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    before = content(world)
    spy = ReplaceSpy(install_root, fail_on=1, error=SimulatedCrashError())

    with (
        pytest.raises(SimulatedCrashError),
        guard.transaction(flavor, label="crash", store=store.path) as tx,
        pytest.MonkeyPatch.context() as patched,  # inside: the pre-write snapshot is not graded
    ):
        spy.install(patched)
        tx.write(CONFIG, NEW_CONFIG)

    # What the disk looked like at the instant of the rename (§6.10: temp file
    # in the same directory, fsync, atomic replace).
    assert spy.seen, "the write must go through os.replace"
    at_rename = spy.seen[0]
    assert at_rename["dst"].resolve() == (flavor.path / CONFIG).resolve()
    assert at_rename["dst_bytes"] == ALLOWLISTED_FILES[CONFIG], "original intact until the rename"
    assert at_rename["src_bytes"] == NEW_CONFIG, "the temp file is complete before the rename"
    assert at_rename["src"].resolve().parent == (flavor.path / CONFIG).resolve().parent
    assert at_rename["src"].resolve() != at_rename["dst"].resolve()
    assert at_rename["src_fsynced"], "the temp file itself (by inode) is fsynced before the rename"

    # After the crash every original path still has its original bytes. A
    # leftover temp file is what a real crash leaves, so extras are tolerated.
    after = content(world)
    assert {k: after.get(k) for k in before} == before

    # The journal entry was opened on enter (§6.10), so the interrupted
    # transaction is on record with the snapshot that undoes it.
    record = record_for(guard, store, "crash")
    assert store.show(record.snapshot_id).id == record.snapshot_id


def test_constructed_keyboard_interrupt_inside_the_transaction_rolls_back(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    before = content(world)
    with (
        pytest.raises(KeyboardInterrupt),
        guard.transaction(flavor, label="interrupt", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        tx.write("Interface/AddOns/Scaffolded/Scaffolded.toc", b"## Title: Scaffolded\n")
        raise KeyboardInterrupt
    assert content(world) == before
    assert record_for(guard, store, "interrupt").rolled_back is True


def test_constructed_operating_system_refusal_is_a_typed_error_and_rolls_everything_back(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """The Windows locked-file case, simulated on every platform: the second
    replace is refused by the operating system."""
    before = content(world)
    spy = ReplaceSpy(
        install_root, fail_on=2, error=PermissionError(13, "constructed: file is locked")
    )

    with (
        pytest.raises(guard.GuardError) as refused,
        guard.transaction(flavor, label="locked", store=store.path) as tx,
        pytest.MonkeyPatch.context() as patched,
    ):
        spy.install(patched)
        tx.write(CONFIG, NEW_CONFIG)
        assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
        tx.write(BINDINGS, b"bind S MOVEBACKWARD\n")

    assert not isinstance(refused.value, (guard.ClientRunningError, guard.PathNotAllowedError))
    assert not isinstance(refused.value, OSError)
    assert "bindings-cache.wtf" in str(refused.value), "the report names the file"
    assert content(world) == before, "the first write is rolled back and no temp file is left"
    assert record_for(guard, store, "locked").rolled_back is True


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only: a file held open is locked")
def test_constructed_windows_locked_file_is_a_typed_error_and_rolls_back(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """M10-11 acceptance, for the `lab (windows)` CI job: the client can hold
    its configuration files open (ADR-0021)."""
    before = content(world)
    with (flavor.path / BINDINGS).open("rb") as held:  # no FILE_SHARE_DELETE
        with (
            pytest.raises(guard.GuardError) as refused,
            guard.transaction(flavor, label="win-lock", store=store.path) as tx,
        ):
            tx.write(CONFIG, NEW_CONFIG)
            tx.write(BINDINGS, b"bind S MOVEBACKWARD\n")
        assert held.read() == ALLOWLISTED_FILES[BINDINGS]

    assert not isinstance(refused.value, (guard.ClientRunningError, guard.PathNotAllowedError))
    assert not isinstance(refused.value, OSError)
    assert content(world) == before
    assert record_for(guard, store, "win-lock").rolled_back is True


def test_constructed_parent_swapped_for_a_symlink_before_the_rename_does_not_escape(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """Between the allowlist check and the rename, `WTF` becomes a symlink to
    a directory outside the install. Whatever the guard does next (a typed
    error, or a write into the real directory through a held descriptor),
    nothing lands outside."""
    wtf = flavor.path / "WTF"
    real = flavor.path / "WTF_real"
    outside_before = content(world / "outside")

    def swap(number: int, source: Path, target: Path) -> None:
        if number == 1:
            wtf.rename(real)
            symlink_or_skip(wtf, world / "outside", is_dir=True)

    spy = ReplaceSpy(install_root, before_call=swap)
    with (
        contextlib.suppress(guard.GuardError),
        guard.transaction(flavor, label="swap", store=store.path) as tx,
        pytest.MonkeyPatch.context() as patched,
    ):
        spy.install(patched)
        tx.write(CONFIG, NEW_CONFIG)

    assert content(world / "outside") == outside_before
    assert (real / "Config.wtf").read_bytes() in (ALLOWLISTED_FILES[CONFIG], NEW_CONFIG)


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT


class OpenSwap:
    """Runs `swap` once, at the first write-mode open of a path inside the
    install (or any open relative to a directory descriptor), just before the
    real open. Wraps `os.open`, `builtins.open` and `io.open` (which
    `Path.open` uses)."""

    def __init__(self, install_root: Path, swap: Callable[[], bool]) -> None:
        self.roots = {str(install_root), str(install_root.resolve())}
        self.swap = swap
        self.swapped = False
        self.fired = False
        self._os_open = os.open
        self._io_open = io.open

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "open", self.os_open)
        monkeypatch.setattr(builtins, "open", self.py_open)
        monkeypatch.setattr(io, "open", self.py_open)

    def _maybe_swap(self, path: Any, writing: bool, dir_fd: int | None) -> None:
        if self.fired or not writing or isinstance(path, int):
            return
        if dir_fd is None:
            absolute = str(Path(os.fsdecode(path)).absolute())  # lexical: links not followed
            if not any(absolute.startswith(root + os.sep) for root in self.roots):
                return
        self.fired = True
        self.swapped = self.swap()

    def os_open(
        self, path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        self._maybe_swap(path, bool(flags & _WRITE_FLAGS), dir_fd)
        return self._os_open(path, flags, mode, dir_fd=dir_fd)

    def py_open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        self._maybe_swap(file, any(c in mode for c in "wxa+"), None)
        return self._io_open(file, mode, *args, **kwargs)


def test_constructed_parent_swapped_for_a_symlink_before_the_temp_open_does_not_escape(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """The earlier window: `WTF` becomes a symlink out of the install after
    the allowlist check and before the temp file is opened. A guard that
    opens by path and renames by path writes the file outside. Two portable
    defences pass: opening relative to a directory descriptor taken before
    the check (POSIX), or re-checking where the opened temp file really is
    and removing it before raising `GuardError`. Graded on bytes: no file
    outside gains or changes content. A temp file created and removed again
    outside is not detectable portably and is not graded (the `dir_fd`
    defence is not available on Windows)."""
    wtf = flavor.path / "WTF"
    real = flavor.path / "WTF_real"
    outside_before = content(world / "outside")

    def swap() -> bool:
        try:
            wtf.rename(real)
        except OSError:
            return False
        try:
            wtf.symlink_to(world / "outside", target_is_directory=True)
        except (OSError, NotImplementedError):
            real.rename(wtf)
            return False
        return True

    spy = OpenSwap(install_root, swap)
    with (
        contextlib.suppress(guard.GuardError),
        guard.transaction(flavor, label="open-swap", store=store.path) as tx,
        pytest.MonkeyPatch.context() as patched,
    ):
        spy.install(patched)
        tx.write(CONFIG, NEW_CONFIG)

    assert spy.fired, "no write-mode open inside the install was seen"
    if not spy.swapped:
        pytest.skip("could not swap the directory for a symlink on this platform")
    assert content(world / "outside") == outside_before
    assert (real / "Config.wtf").read_bytes() in (ALLOWLISTED_FILES[CONFIG], NEW_CONFIG)


def test_constructed_rollback_that_cannot_finish_does_not_claim_it_did(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """Two writes land, the body raises, and every replace from the second
    rollback replace on is refused. `rolled_back=True` would be a lie."""
    spy = ReplaceSpy(
        install_root,
        fail_on=4,
        persistent=True,
        error=PermissionError(13, "constructed: file is locked"),
    )
    with (
        pytest.raises((RuntimeError, guard.GuardError)),
        pytest.MonkeyPatch.context() as patched,  # outside: still active during rollback
        guard.transaction(flavor, label="half", store=store.path) as tx,
    ):
        spy.install(patched)
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(BINDINGS, b"bind S MOVEBACKWARD\n")
        raise RuntimeError("constructed failure")

    assert len(spy.seen) >= 4, "rollback of two replaced files needs two replaces"
    assert record_for(guard, store, "half").rolled_back is not True


def test_constructed_rollback_through_an_inside_symlink_restores_both_names(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """`WTF/alias.wtf` is a link to `WTF/Config.wtf`, inside the allowlist.
    Writing through it may be refused; if it is not, rollback must leave the
    link a link and the file its original bytes."""
    symlink_or_skip(flavor.path / "WTF" / "alias.wtf", "Config.wtf", is_dir=False)
    before = content(world)
    with (
        pytest.raises((RuntimeError, guard.GuardError)),
        guard.transaction(flavor, label="alias", store=store.path) as tx,
    ):
        with contextlib.suppress(guard.PathNotAllowedError):
            tx.write("WTF/alias.wtf", NEW_CONFIG)
        raise RuntimeError("constructed failure")
    assert content(world) == before


# ─── rollback, undo, restore ─────────────────────────────────────────────────


def test_constructed_exception_inside_the_transaction_rolls_every_touched_path_back(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    before = content(world)
    scaffolded = "Interface/AddOns/Scaffolded/Scaffolded.toc"

    with (
        pytest.raises(RuntimeError, match="constructed failure"),
        guard.transaction(flavor, label="rollback", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(NEW_SAVED, NEW_LUA)
        tx.write(scaffolded, b"## Title: Scaffolded\n")  # a directory the transaction created
        tx.write(ICON, b"BLP2\x09")
        tx.delete(BINDINGS)
        tx.delete(FONT)
        # The operations really happened; this is not a deferred plan.
        assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
        assert (flavor.path / NEW_SAVED).read_bytes() == NEW_LUA
        assert (flavor.path / scaffolded).read_bytes() == b"## Title: Scaffolded\n"
        assert not (flavor.path / BINDINGS).exists()
        raise RuntimeError("constructed failure")

    assert content(world) == before, "files, and the directory the transaction created"
    assert record_for(guard, store, "rollback").rolled_back is True


def test_constructed_undo_restores_the_pre_transaction_bytes(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    before = content(world)
    with guard.transaction(flavor, label="undo-me", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(NEW_SAVED, NEW_LUA)
        tx.delete(BINDINGS)
    changed = content(world)
    assert changed != before

    guard.undo(store=store.path)

    assert content(world) == before

    # `undo()` is a transaction like any other (L2): the bytes it discarded
    # are in its pre-write snapshot and its journal names every path, so the
    # owner's `wowlab undo` after a play session never loses the
    # SavedVariables the client wrote at logout.
    records = list(guard.history(store=store.path))
    assert [r.label for r in records[:-1]] == ["undo-me"]
    undo_record = records[-1]
    assert undo_record.label != "undo-me"
    assert undo_record.rolled_back is False
    assert changes(undo_record.paths) == {
        CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG])),
        NEW_SAVED: (sha(NEW_LUA), None),
        BINDINGS: (None, sha(ALLOWLISTED_FILES[BINDINGS])),
    }
    held = store.show(undo_record.snapshot_id)
    assert store.read_object(entry_for(held, CONFIG).sha256 or "") == NEW_CONFIG
    assert store.read_object(entry_for(held, NEW_SAVED).sha256 or "") == NEW_LUA

    # The undo is now the most recent transaction, so undoing it is a redo.
    guard.undo(store=store.path)
    assert content(world) == changed
    assert len(list(guard.history(store=store.path))) == 3


def test_constructed_undo_reverts_the_most_recent_transaction_only(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    with guard.transaction(flavor, label="older", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    with guard.transaction(flavor, label="newer", store=store.path) as tx:
        tx.write(CONFIG, NEWER_CONFIG)
        tx.write(TOC, b"## Title: Newer\n")

    guard.undo(store=store.path)

    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    assert (flavor.path / TOC).read_bytes() == ALLOWLISTED_FILES[TOC]


def test_constructed_restore_puts_back_the_bytes_of_a_snapshot(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    with guard.transaction(flavor, label="change", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
    original = record_for(guard, store, "change").snapshot_id

    # `paths` narrows the restore to what was asked for.
    with guard.transaction(flavor, label="restore-one", store=store.path) as tx:
        tx.restore(original, paths=[CONFIG])
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
    assert not (flavor.path / BINDINGS).exists()
    assert changes(record_for(guard, store, "restore-one").paths) == {
        CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG]))
    }

    # `paths=None` restores every file the snapshot holds.
    with guard.transaction(flavor, label="restore-all", store=store.path) as tx:
        tx.restore(original)
    for rel, data in ALLOWLISTED_FILES.items():
        assert (flavor.path / rel).read_bytes() == data
    assert changes(record_for(guard, store, "restore-all").paths) == {
        BINDINGS: (None, sha(ALLOWLISTED_FILES[BINDINGS]))
    }


def forge_manifest(
    store: SnapshotStore,
    base: Manifest,
    hostile_rel: str,
    *,
    link_target: str | None = None,
    mode: int | None = None,
    raw: bool = False,
) -> str:
    """Publish a manifest that is `base` plus one entry at `hostile_rel`.

    Built with the snapshot module's public functions, so it is a manifest the
    store itself accepts: the id matches the entries and `show` loads it. A
    file entry reuses the content object of `WTF/Config.wtf` (with `mode` if
    given); with `link_target` the entry is a symlink, which the store records
    and never follows (§6.9). With `raw` the entry path is used as given,
    without the prefix the snapshot's own entries carry, so a snapshot rooted
    at the install root names install-root files and other flavors.
    """
    anchor = entry_for(base, CONFIG)
    path = hostile_rel if raw else manifest_prefix(base) + hostile_rel
    assert base.entry(path) is None, f"{path!r} is already in the snapshot"
    if link_target is None:
        fields = {**anchor.model_dump(), "path": path}
        if mode is not None:
            fields["mode"] = mode
        hostile = Entry(**fields)
    else:
        hostile = Entry(
            path=path, kind="symlink", sha256=None, size=0, mode=0o777, target=link_target
        )
    entries = tuple(sorted((*base.entries, hostile), key=lambda e: e.path))
    fingerprint = tree_fingerprint(base.subtrees, base.excluded, entries)
    forged = Manifest(
        **{
            **base.model_dump(),
            "id": f"20260921T130000.000000Z-{fingerprint}",
            "created_at": "2026-09-21T13:00:00.000000Z",
            "label": "constructed: forged",
            "entries": entries,
        }
    )
    (store.manifests_dir / f"{forged.id}.json").write_bytes(manifest_bytes(forged))
    assert store.show(forged.id) == forged, "the store accepts it; refusing is guard's job"
    return forged.id


HOSTILE_ENTRIES: tuple[tuple[str, str], ...] = (
    ("flavor-info", ".flavor.info"),
    ("client-executable", "Wow.exe"),
    ("new-executable-at-the-flavor-root", "Injected.exe"),
    ("executable-in-addons", "Interface/AddOns/Thing/evil.dll"),
    ("data", "Data/new.bin"),
    ("logs", "Logs/Client.log"),
    ("flavor-root-file", "notes.txt"),
    ("prefix-lookalike", "WTFx/Config.wtf"),
)

# `Entry.path` is platform-neutral on purpose (M10-10): each of these is an
# ordinary file name on the POSIX machine that captured it and something else
# entirely on Windows. Checking them on the restoring platform is guard's job.
WINDOWS_HOSTILE_ENTRIES: tuple[tuple[str, str], ...] = (
    ("backslash-separator", "WTF/a\\b.lua"),
    ("backslash-traversal", "WTF/..\\..\\evil.txt"),
    ("backslash-traversal-out-of-the-install", "WTF/..\\..\\..\\..\\outside\\target.txt"),
    ("drive-relative", "C:evil.txt"),
    ("drive-relative-inside-the-allowlist", "WTF/C:evil.txt"),
    ("alternate-data-stream", "WTF/Config.wtf:hidden"),
    ("drive-absolute", "C:\\Windows\\evil.txt"),
    ("rooted", "\\evil.txt"),
)


def _entry_accepts(rel: str) -> bool:
    try:
        Entry(path=rel)
    except ValidationError:
        return False
    return True


# Every forbidden path the manifest model itself accepts (no `..`, `.`, empty
# part, NUL or leading `/`), deduplicated against HOSTILE_ENTRIES.
_NAMED = {rel for _, rel in HOSTILE_ENTRIES}
FORGEABLE_FORBIDDEN: tuple[tuple[str, str], ...] = tuple(
    (f"{group}-{name}", rel)
    for group, table in (
        ("forbidden", FORBIDDEN_PATHS),
        ("case-variant", CASE_VARIANTS_OF_FORBIDDEN),
        ("respelled", RESPELLED_ALLOWLIST_ROOTS),
    )
    for name, rel in table
    if "<" not in rel and rel not in _NAMED and _entry_accepts(rel)
)

OTHER_FLAVOR = "_classic_"

# Entry paths used raw (see `forge_manifest`): relative to whatever root the
# snapshot has. None of them is a path this flavor's transaction may write.
RAW_TARGETS: tuple[tuple[str, str], ...] = (
    ("raw-build-info", ".build.info"),
    ("raw-product-db", ".product.db"),
    ("raw-launcher", "World of Warcraft Launcher.exe"),
    ("raw-data", "Data/data/data.001"),
    ("raw-other-flavor-config", f"{OTHER_FLAVOR}/WTF/Config.wtf"),
    ("raw-other-flavor-new-file", f"{OTHER_FLAVOR}/WTF/New.wtf"),
    ("raw-other-flavor-info", f"{OTHER_FLAVOR}/.flavor.info"),
)

# (id, entry path, raw)
FORGED_ENTRIES: tuple[tuple[str, str, bool], ...] = (
    *((name, rel, False) for name, rel in HOSTILE_ENTRIES),
    *((name, rel, False) for name, rel in FORGEABLE_FORBIDDEN),
    *((name, rel, True) for name, rel in RAW_TARGETS),
)


def add_other_flavor(install_root: Path) -> Path:
    """A second flavor in the same install, which no transaction on the first
    one may touch."""
    other = install_root / OTHER_FLAVOR
    _put(other, {".flavor.info": b"Product Flavor!STRING:0\nwow_classic\n", CONFIG: b"SET a 1\n"})
    return other


SYMLINK_ENTRY = "WTF/escape"
SYMLINK_TARGETS: tuple[tuple[str, str], ...] = (
    ("out-of-the-install", "../../../outside"),
    ("to-data", "../Data"),
    ("to-a-sibling-file", "Config.wtf"),
)


def _refuses_forged_restore(
    guard: Any,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    hostile_rel: str,
    *,
    raw: bool = False,
) -> None:
    other = add_other_flavor(flavor.path.parent)
    other_before = content(other)
    with guard.transaction(flavor, label="base", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    base = store.show(record_for(guard, store, "base").snapshot_id)
    forged_id = forge_manifest(store, base, hostile_rel, raw=raw)
    before = content(world)
    # A raw entry may simply not be a path of this flavor ("not in the
    # snapshot"); any other entry is a path refusal.
    expected = guard.GuardError if raw else guard.PathNotAllowedError

    with (
        pytest.raises(expected) as refused,
        guard.transaction(flavor, label="forged", store=store.path) as tx,
    ):
        tx.restore(forged_id)
    assert not isinstance(refused.value, guard.ClientRunningError)

    # Nothing from the forged manifest stays: not the hostile entry, and not
    # the legitimate entries restored before it was reached.
    assert content(world) == before

    # Naming the hostile path outright is refused too.
    with (
        guard.transaction(flavor, label="forged-named", store=store.path) as tx,
        pytest.raises(expected) as refused,
    ):
        tx.restore(forged_id, paths=[hostile_rel])
    assert not isinstance(refused.value, guard.ClientRunningError)
    assert content(world) == before
    assert content(other) == other_before

    # Positive control: the same store and flavor restore a legitimate path.
    with guard.transaction(flavor, label="legit", store=store.path) as tx:
        tx.restore(base.id, paths=[CONFIG])
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]


@pytest.mark.parametrize(
    ("hostile_rel", "raw"),
    [pytest.param(rel, raw, id=f"constructed-{name}") for name, rel, raw in FORGED_ENTRIES],
)
def test_constructed_restore_treats_the_manifest_as_untrusted(
    guard: Any,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    hostile_rel: str,
    raw: bool,
) -> None:
    _refuses_forged_restore(guard, world, flavor, store, hostile_rel, raw=raw)


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only: what these names mean on NTFS")
@pytest.mark.parametrize(
    "hostile_rel",
    [pytest.param(rel, id=f"constructed-windows-{name}") for name, rel in WINDOWS_HOSTILE_ENTRIES],
)
def test_constructed_windows_restore_refuses_posix_names_that_are_not_plain_paths(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, hostile_rel: str
) -> None:
    _refuses_forged_restore(guard, world, flavor, store, hostile_rel)


@pytest.mark.parametrize(
    "target", [pytest.param(t, id=f"constructed-{name}") for name, t in SYMLINK_TARGETS]
)
def test_constructed_restore_never_creates_a_symlink(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, target: str
) -> None:
    """The store records symlinks and never follows them (§6.9). Putting one
    back would let a manifest point the next write anywhere."""
    with guard.transaction(flavor, label="base", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    base = store.show(record_for(guard, store, "base").snapshot_id)
    forged_id = forge_manifest(store, base, SYMLINK_ENTRY, link_target=target)
    before = content(world)

    with (
        pytest.raises(guard.GuardError) as refused,
        guard.transaction(flavor, label="link", store=store.path) as tx,
    ):
        tx.restore(forged_id)
    assert not isinstance(refused.value, guard.ClientRunningError)
    assert content(world) == before

    with (
        guard.transaction(flavor, label="link-named", store=store.path) as tx,
        pytest.raises(guard.GuardError),
    ):
        tx.restore(forged_id, paths=[SYMLINK_ENTRY])
    assert content(world) == before
    assert not (flavor.path / SYMLINK_ENTRY).is_symlink()


POISONED_SNAPSHOTS: tuple[tuple[str, str, str | None, bool], ...] = (
    *((name, rel, None, raw) for name, rel, raw in FORGED_ENTRIES),
    *((f"symlink-{name}", SYMLINK_ENTRY, target, False) for name, target in SYMLINK_TARGETS),
)


@pytest.mark.parametrize(
    ("hostile_rel", "link_target", "raw"),
    [
        pytest.param(rel, t, raw, id=f"constructed-{name}")
        for name, rel, t, raw in POISONED_SNAPSHOTS
    ],
)
def test_constructed_rollback_and_undo_treat_the_pre_write_snapshot_as_untrusted(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    hostile_rel: str,
    link_target: str | None,
    raw: bool,
) -> None:
    """The journal legitimately points at a pre-write snapshot the store hands
    back; that snapshot is still untrusted input when rollback or `undo()`
    reads it. The hostile entry sits at a path no transaction here touches,
    so journal-scoped undo may succeed or refuse; either way it never lands."""
    other = add_other_flavor(install_root)
    other_before = content(other)
    with guard.transaction(flavor, label="base", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    base = store.show(record_for(guard, store, "base").snapshot_id)
    forged = store.show(forge_manifest(store, base, hostile_rel, link_target=link_target, raw=raw))
    # `guard` builds its own SnapshotStore from the path, so the poison goes
    # in at the class: every `create` in this test hands back the forgery.
    monkeypatch.setattr(SnapshotStore, "create", lambda self, *a, **k: forged)
    before = content(world)
    hostile_path = flavor.path / hostile_rel

    def untouched_but_config() -> None:
        # The hostile entry never lands: an existing target keeps its bytes, a
        # new one is not created, and no symlink appears anywhere.
        assert not hostile_path.is_symlink()
        assert not (install_root / hostile_rel).is_symlink()
        assert content(other) == other_before
        after = content(world)
        assert {k: v for k, v in after.items() if k != flavor_key(CONFIG)} == {
            k: v for k, v in before.items() if k != flavor_key(CONFIG)
        }
        assert after[flavor_key(CONFIG)] in (NEW_CONFIG, NEWER_CONFIG, ALLOWLISTED_FILES[CONFIG])

    # Rollback from the poisoned snapshot: the hostile entry never lands.
    with (
        pytest.raises((RuntimeError, guard.GuardError)),
        guard.transaction(flavor, label="poisoned", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEWER_CONFIG)
        raise RuntimeError("constructed failure")
    untouched_but_config()
    assert record_for(guard, store, "poisoned").snapshot_id == forged.id

    # A committed transaction with the same poisoned pre-write snapshot, so
    # the undo below grades the untrusted-snapshot rule and nothing about
    # whether `undo()` acts on a rolled-back record.
    with guard.transaction(flavor, label="poisoned-commit", store=store.path) as tx:
        tx.write(CONFIG, NEWER_CONFIG)
    untouched_but_config()
    assert record_for(guard, store, "poisoned-commit").snapshot_id == forged.id

    # `undo()` of that transaction reads the poisoned snapshot. It journaled
    # only Config.wtf, so it may restore that and ignore the rest, or refuse.
    try:
        guard.undo(store=store.path)
    except (guard.GuardError, SnapshotError) as refused:
        assert not isinstance(refused, guard.ClientRunningError)
    untouched_but_config()


NEW_BINDINGS = b"bind S MOVEBACKWARD\n"
LINK_PLANTS = ("wtf-root-dir-link", "ancestor-dir-link", "final-file-link", "hard-link")
LINK_ROUTES = ("undo", "restore", "rollback")


def plant_link(kind: str, world: Path, flavor: Flavor) -> None:
    """Make `WTF/Account/ACCT/bindings-cache.wtf` resolve outside the install.

    Directory links first copy the real directory to `outside/` (so the file
    the write would reach there already exists) and move the real one out of
    the install, as an attacker racing the gate would."""
    outside = world / "outside"
    if kind in ("wtf-root-dir-link", "ancestor-dir-link"):
        real = flavor.path / ("WTF" if kind == "wtf-root-dir-link" else "WTF/Account")
        shutil.copytree(real, outside / real.name, symlinks=True)
        real.rename(world / f"moved-{real.name}")
        symlink_or_skip(real, outside / real.name, is_dir=True)
    elif kind == "final-file-link":
        (flavor.path / BINDINGS).unlink()
        symlink_or_skip(flavor.path / BINDINGS, outside / "target.txt", is_dir=False)
    else:
        (flavor.path / BINDINGS).unlink()
        try:
            os.link(outside / "target.txt", flavor.path / BINDINGS)
        except OSError as exc:
            pytest.skip(f"this platform or volume cannot hard-link: {exc}")


@pytest.mark.parametrize("kind", [pytest.param(k, id=f"constructed-{k}") for k in LINK_PLANTS])
@pytest.mark.parametrize("route", [pytest.param(r, id=f"constructed-{r}") for r in LINK_ROUTES])
def test_constructed_link_planted_after_the_check_never_redirects_undo_restore_or_rollback(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, route: str, kind: str
) -> None:
    """Every write route resolves links again, per path, at the moment it
    writes: not only `tx.write` and `tx.delete`. A symbolic link is refused;
    a hard link may be refused or replaced by a rename (which never touches
    the other name). Either way nothing outside the install changes."""
    outside = world / "outside"
    symbolic = kind != "hard-link"

    if route == "rollback":
        outside_before: dict[str, bytes | str] = {}
        with (
            contextlib.suppress(RuntimeError, guard.GuardError, SnapshotError),
            guard.transaction(flavor, label="rollback", store=store.path) as tx,
        ):
            tx.write(BINDINGS, NEW_BINDINGS)
            plant_link(kind, world, flavor)
            outside_before.update(content(outside))
            raise RuntimeError("constructed failure")
        assert content(outside) == outside_before
        if record_for(guard, store, "rollback").rolled_back is True:
            # A claim of success means the path is a plain file in the install
            # again, with its original bytes.
            touched = flavor.path / BINDINGS
            assert not any(
                p.is_symlink() for p in (touched, *touched.parents) if flavor.path in p.parents
            )
            assert touched.read_bytes() == ALLOWLISTED_FILES[BINDINGS]
        return

    with guard.transaction(flavor, label="first", store=store.path) as tx:
        tx.write(BINDINGS, NEW_BINDINGS)
    pre_id = record_for(guard, store, "first").snapshot_id
    plant_link(kind, world, flavor)
    outside_before = content(outside)

    def act() -> None:
        if route == "undo":
            guard.undo(store=store.path)
        else:
            with guard.transaction(flavor, label="restore", store=store.path) as tx:
                tx.restore(pre_id)

    if symbolic:
        with pytest.raises((guard.GuardError, SnapshotError)) as refused:
            act()
        assert not isinstance(refused.value, guard.ClientRunningError)
    else:
        with contextlib.suppress(guard.GuardError):
            act()
    assert content(outside) == outside_before


def forge_edited(
    store: SnapshotStore, base: Manifest, edit: Callable[[dict[str, Entry], str], None]
) -> Manifest:
    """Publish `base` with its entries edited in place by `edit(entries,
    prefix)`, where `entries` maps entry path to `Entry`. The store accepts
    the result (id matches entries), as with `forge_manifest`."""
    entries = {e.path: e for e in base.entries}
    edit(entries, manifest_prefix(base))
    ordered = tuple(entries[k] for k in sorted(entries))
    fingerprint = tree_fingerprint(base.subtrees, base.excluded, ordered)
    forged = Manifest(
        **{
            **base.model_dump(),
            "id": f"20260921T140000.000000Z-{fingerprint}",
            "created_at": "2026-09-21T14:00:00.000000Z",
            "label": "constructed: forged",
            "entries": ordered,
        }
    )
    (store.manifests_dir / f"{forged.id}.json").write_bytes(manifest_bytes(forged))
    assert store.show(forged.id) == forged
    return forged


def _poison_touched(case: str) -> Callable[[dict[str, Entry], str], None]:
    def edit(entries: dict[str, Entry], prefix: str) -> None:
        config = entries[prefix + CONFIG]
        if case == "config-is-a-symlink-out-of-the-install":
            entries[prefix + CONFIG] = Entry(
                path=prefix + CONFIG, kind="symlink", target="../../../outside/target.txt"
            )
        elif case == "config-is-a-symlink-to-data":
            entries[prefix + CONFIG] = Entry(
                path=prefix + CONFIG, kind="symlink", target="../../Data"
            )
        elif case == "config-hash-disagrees-with-before":
            bindings = entries[prefix + BINDINGS]
            entries[prefix + CONFIG] = config.model_copy(
                update={"sha256": bindings.sha256, "size": bindings.size}
            )
        elif case == "config-missing-though-it-existed":
            del entries[prefix + CONFIG]
        else:  # an entry for a path the transaction created (`before` is None)
            entries[prefix + NEW_SAVED] = config.model_copy(update={"path": prefix + NEW_SAVED})

    return edit


POISONED_TOUCHED = (
    "config-is-a-symlink-out-of-the-install",
    "config-is-a-symlink-to-data",
    "config-hash-disagrees-with-before",
    "config-missing-though-it-existed",
    "entry-present-for-a-created-path",
)


@pytest.mark.parametrize("case", [pytest.param(c, id=f"constructed-{c}") for c in POISONED_TOUCHED])
def test_constructed_undo_refuses_a_poisoned_entry_at_a_path_it_journaled(
    guard: Any,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """The entries undo does read, the journaled ones, are checked: no
    symlink, and the hash agrees with the journal's `before`. Refused before
    anything is written."""
    with guard.transaction(flavor, label="base", store=store.path):
        pass
    base = store.show(record_for(guard, store, "base").snapshot_id)
    forged = forge_edited(store, base, _poison_touched(case))
    monkeypatch.setattr(SnapshotStore, "create", lambda self, *a, **k: forged)
    with guard.transaction(flavor, label="touched", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(NEW_SAVED, NEW_LUA)
    assert record_for(guard, store, "touched").snapshot_id == forged.id
    before = content(world)

    with pytest.raises(guard.GuardError) as refused:
        guard.undo(store=store.path)
    assert not isinstance(refused.value, guard.ClientRunningError)
    assert content(world) == before
    assert not (flavor.path / CONFIG).is_symlink()


JOURNAL_SUBSTITUTES: tuple[tuple[str, str], ...] = (
    ("traversal-to-build-info", "../.build.info"),
    ("executable-in-addons", "Interface/AddOns/Thing/evil.dll"),
    ("client-executable", "Wow.exe"),
    ("data", "Data/new.bin"),
)


@pytest.mark.parametrize(
    "substitute", [pytest.param(sub, id=f"constructed-{name}") for name, sub in JOURNAL_SUBSTITUTES]
)
def test_constructed_undo_refuses_a_journal_that_names_a_forbidden_path(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    substitute: str,
) -> None:
    """Format-agnostic: whatever the journal looks like on disk, the path it
    recorded for the created file is byte-replaced with a forbidden one.
    `undo()` would delete that path (it did not exist before), so it must
    re-check it."""
    with guard.transaction(flavor, label="created", store=store.path) as tx:
        tx.write(NEW_SAVED, NEW_LUA)
    build_info = (install_root / ".build.info").read_bytes()

    # The journal first (whatever is under the store but not an object or a
    # manifest); every file only if the journal turns out to live elsewhere.
    new = substitute.encode()
    replaced = byte_replace(
        store, NEW_SAVED.encode(), new, skip=(store.objects_dir, store.manifests_dir)
    ) or byte_replace(store, NEW_SAVED.encode(), new)
    assert replaced >= 1, "the journal is expected to name the path it touched"
    before = content(world)

    with pytest.raises((guard.GuardError, SnapshotError)) as refused:
        guard.undo(store=store.path)
    assert not isinstance(refused.value, guard.ClientRunningError)
    assert (install_root / ".build.info").read_bytes() == build_info
    assert content(world) == before


@pytest.mark.parametrize(
    "route",
    [
        pytest.param("undo", id="constructed-undo"),
        pytest.param("restore-named-path", id="constructed-restore-named-path"),
    ],
)
def test_constructed_unchanged_symlinked_addon_and_script_do_not_block_undo_or_restore(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    route: str,
) -> None:
    """Positive control for journal-scoped undo: an addon folder symlinked in
    from elsewhere in the install (the common addon-developer layout) and a
    `release.sh` shipped inside an addon are in every snapshot, unchanged.
    Neither may stop the owner undoing, or restoring, a Config.wtf edit, and
    both stay exactly as they were: the link a link, the script its bytes."""
    _put(install_root / "AddOnDev" / "Linked", {"Linked.toc": b"## Title: Linked\n"})
    symlink_or_skip(
        flavor.path / "Interface" / "AddOns" / "Linked",
        install_root / "AddOnDev" / "Linked",
        is_dir=True,
    )
    (flavor.path / "Interface" / "AddOns" / "Thing" / "release.sh").write_bytes(
        b"#!/bin/sh\necho constructed\n"
    )
    pristine = content(world)

    with guard.transaction(flavor, label="edit", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG

    if route == "undo":
        guard.undo(store=store.path)
    else:
        pre_id = record_for(guard, store, "edit").snapshot_id
        with guard.transaction(flavor, label="restore", store=store.path) as tx:
            tx.restore(pre_id, paths=[CONFIG])

    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
    assert content(world) == pristine, "link, script and everything else as before; nothing new"


def byte_replace(
    store: SnapshotStore, old: bytes, new: bytes, *, skip: tuple[Path, ...] = ()
) -> int:
    """Replace `old` with `new` in every regular file under the store that is
    not under one of `skip`; return how many files changed."""
    replaced = 0
    for path in sorted(store.path.rglob("*")):
        if any(parent in path.parents for parent in skip):
            continue
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            if old in data:
                path.write_bytes(data.replace(old, new))
                replaced += 1
    return replaced


def _json_spellings(text: str) -> tuple[bytes, ...]:
    """A path as bytes: plain, then as JSON escapes it (backslashes on Windows)."""
    escaped = json.dumps(text)[1:-1]
    return (text.encode(), escaped.encode())


def test_constructed_undo_refuses_an_install_root_the_store_points_elsewhere(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """The flavor `undo()` acts on comes from the store, which is untrusted:
    it is validated like any caller's flavor (§6.1: `.flavor.info` inside an
    install with `.build.info`), not taken on faith."""
    with guard.transaction(flavor, label="created", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    elsewhere = world / "elsewhere"
    _put(elsewhere / FLAVOR_FOLDER, {CONFIG: b"constructed: not an install\n"})
    # The escaped spelling first: on Windows the plain one is a substring of it.
    replaced = 0
    for old, new in reversed(
        list(zip(_json_spellings(str(install_root)), _json_spellings(str(elsewhere)), strict=True))
    ):
        replaced += byte_replace(store, old, new)
    assert replaced >= 1, "the store is expected to name the install root"
    before = content(world)

    with pytest.raises((guard.GuardError, SnapshotError)) as refused:
        guard.undo(store=store.path)
    assert not isinstance(refused.value, guard.ClientRunningError)
    assert content(world) == before


def test_constructed_undo_refuses_a_store_inside_the_install(
    guard: Any, world: Path, install_root: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    with guard.transaction(flavor, label="created", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    seeded = install_root / "store"
    shutil.copytree(store.path, seeded)
    before = strict_state(world)

    with pytest.raises(guard.GuardError) as refused:
        guard.undo(store=seeded)
    assert not isinstance(refused.value, guard.ClientRunningError)
    assert strict_state(world) == before


@pytest.mark.skipif(sys.platform == "win32", reason="setuid and exec bits are a POSIX thing")
def test_constructed_restore_never_widens_permission_bits(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """A manifest entry carrying mode 04755 (setuid, executable) is data from
    the store; restoring it must not put those bits on disk, whether the
    entry is refused or written."""
    original_mode = stat.S_IMODE((flavor.path / CONFIG).stat().st_mode)
    with guard.transaction(flavor, label="base", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    base = store.show(record_for(guard, store, "base").snapshot_id)
    planted = "WTF/Planted.wtf"
    forged_id = forge_manifest(store, base, planted, mode=0o4755)

    with (
        contextlib.suppress(guard.GuardError),
        guard.transaction(flavor, label="setuid", store=store.path) as tx,
    ):
        tx.restore(forged_id)

    for rel in (CONFIG, planted):
        path = flavor.path / rel
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode & 0o7000 == 0, f"{rel}: setuid/setgid/sticky bit {mode:o}"
            assert mode & 0o111 <= original_mode & 0o111, f"{rel}: exec bits widened to {mode:o}"


# ─── write-ahead journal ─────────────────────────────────────────────────────


def _dir_of_fd(fd: int, roots: Iterable[Path]) -> Path | None:
    """The directory under one of `roots` that descriptor `fd` refers to,
    found by device and inode without following links; None if it is not
    under any of them."""
    key = _file_id(os.fstat(fd))
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, _files in os.walk(root, followlinks=False):
            if _file_id(Path(dirpath).lstat()) == key:
                return Path(dirpath)
    return None


def _spelled(path: Any, dir_fd: int | None, roots: Iterable[Path]) -> str | None:
    """An absolute spelling of `path` without following links. A path
    relative to a directory descriptor (the `dir_fd` defence) is resolved
    through the descriptor's directory under `roots`; None when that
    directory is not under any of them, or `path` is itself a descriptor."""
    if isinstance(path, int):
        return None
    spelled = Path(os.fsdecode(path))
    if dir_fd is not None and not spelled.is_absolute():
        base = _dir_of_fd(dir_fd, roots)
        if base is None:
            return None
        spelled = base / spelled
    return str(spelled.absolute())


class KillSwitch:
    """Simulates the process dying mid-transaction.

    Install-bound mutations (`os.replace`, `os.rename`, `os.unlink`,
    `os.remove` touching a path inside the install) are counted as they
    complete, including ones made relative to a directory descriptor
    (resolved through the descriptor's directory). After the `after`-th
    completes, `SimulatedCrashError` is raised, and from then on every
    mutation or write-mode open through these entry points inside the install
    or the store (or relative to a descriptor that cannot be placed) raises
    it before doing anything. That blocks the rollback and journal updates
    this module can see; a guard could still write through a route not
    wrapped here, which is why this grader is paired with the fsync-before-
    change grader below."""

    def __init__(self, install_root: Path, store_path: Path, *, after: int) -> None:
        self.install = {str(install_root), str(install_root.resolve())}
        store_path.mkdir(parents=True, exist_ok=True)
        self.guarded = self.install | {str(store_path), str(store_path.resolve())}
        self.roots = (install_root, store_path)
        self.after = after
        self.completed = 0
        self.killed = False
        self._real = {
            name: getattr(os, name)
            for name in ("replace", "rename", "unlink", "remove", "rmdir", "mkdir", "open")
        }
        self._io_open = io.open

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("replace", "rename"):
            monkeypatch.setattr(os, name, self._two_path(name))
        for name in ("unlink", "remove", "rmdir", "mkdir"):
            monkeypatch.setattr(os, name, self._one_path(name))
        monkeypatch.setattr(os, "open", self._os_open)
        monkeypatch.setattr(io, "open", self._py_open)
        monkeypatch.setattr(builtins, "open", self._py_open)

    @staticmethod
    def _under(spelled: str | None, roots: set[str]) -> bool:
        return spelled is None or any(spelled == r or spelled.startswith(r + os.sep) for r in roots)

    def _blocked(self, *spellings: str | None) -> None:
        if self.killed and any(self._under(sp, self.guarded) for sp in spellings):
            raise SimulatedCrashError("constructed: the process is dead")

    def _count(self, *spellings: str | None) -> None:
        if not self.killed and any(
            sp is not None and self._under(sp, self.install) for sp in spellings
        ):
            self.completed += 1
            if self.completed == self.after:
                self.killed = True
                raise SimulatedCrashError("constructed: killed right after the mutation")

    def _two_path(self, name: str) -> Callable[..., None]:
        def call(
            src: Any, dst: Any, *, src_dir_fd: int | None = None, dst_dir_fd: int | None = None
        ) -> None:
            a = _spelled(src, src_dir_fd, self.roots)
            b = _spelled(dst, dst_dir_fd, self.roots)
            self._blocked(a, b)
            self._real[name](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            self._count(b if b is not None else a)

        return call

    def _one_path(self, name: str) -> Callable[..., None]:
        def call(path: Any, *args: Any, dir_fd: int | None = None, **kwargs: Any) -> None:
            spelled = _spelled(path, dir_fd, self.roots)
            self._blocked(spelled)
            self._real[name](path, *args, dir_fd=dir_fd, **kwargs)
            if name in ("unlink", "remove"):
                self._count(spelled)

        return call

    def _os_open(
        self, path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if flags & _WRITE_FLAGS:
            self._blocked(_spelled(path, dir_fd, self.roots))
        return self._real["open"](path, flags, mode, dir_fd=dir_fd)

    def _py_open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if not isinstance(file, int) and any(c in mode for c in "wxa+"):
            self._blocked(_spelled(file, None, self.roots))
        return self._io_open(file, mode, *args, **kwargs)


# (id, operations, which install mutation the kill follows, created path or None)
KILL_CASES: tuple[tuple[str, tuple[tuple[str, str], ...], int, str | None], ...] = (
    ("overwrite", (("write", CONFIG),), 1, None),
    ("create", (("write", NEW_SAVED),), 1, NEW_SAVED),
    ("delete", (("delete", BINDINGS),), 1, None),
    ("second-of-two-writes", (("write", CONFIG), ("write", ICON)), 2, None),
)


def _run_ops(tx: Any, ops: Iterable[tuple[str, str]]) -> None:
    for op, rel in ops:
        if op == "write":
            tx.write(rel, MUST_NEVER_LAND)
        else:
            tx.delete(rel)


@pytest.mark.parametrize(
    ("ops", "after", "created"),
    [
        pytest.param(ops, after, created, id=f"constructed-{name}")
        for name, ops, after, created in KILL_CASES
    ],
)
def test_constructed_kill_right_after_an_install_mutation_is_undone_from_the_journal(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    ops: tuple[tuple[str, str], ...],
    after: int,
    created: str | None,
) -> None:
    """Write-ahead: the journal names a path before the path changes. Here
    the process dies the instant a mutation lands, so no rollback and no
    later journal write happens; `undo()` must still put every original byte
    back and remove what the transaction created. (A guard that journals a
    path only after writing it has nothing to undo from.) Leftover temp files
    are what a real kill leaves and are tolerated."""
    before = content(world)
    killer = KillSwitch(install_root, store.path, after=after)

    with (
        pytest.raises(SimulatedCrashError),
        pytest.MonkeyPatch.context() as patched,  # outermost: the kill outlives the body
        guard.transaction(flavor, label="killed", store=store.path) as tx,
    ):
        killer.arm(patched)
        _run_ops(tx, ops)

    assert killer.killed, "the kill must have happened inside the install"
    changed = content(world)
    assert changed != before, "positive control: the mutation landed before the kill"

    guard.undo(store=store.path)

    after_undo = content(world)
    assert {k: after_undo.get(k) for k in before} == before
    if created is not None:
        assert not (flavor.path / created).exists(), "the created file is removed by undo"


class JournalAheadSpy:
    """At each install mutation that changes a touched path, checks, before
    calling through, that a file under the store (outside `objects/` and
    `manifests/`) holds the path and its `before` hash as literal text and
    has been fsynced (by inode, via `os.fsync` or `os.fdatasync` on a
    descriptor or a file object). Mutations relative to a directory
    descriptor are resolved through the descriptor's directory."""

    def __init__(
        self,
        flavor: Flavor,
        store: SnapshotStore,
        touched: dict[str, str | None],
    ) -> None:
        self.store = store
        self.touched = {
            str((flavor.path / rel).absolute()): (rel, before) for rel, before in touched.items()
        }
        self.roots = (Path(flavor.path),)
        self.fsynced: set[FileId] = set()
        self.checked: set[str] = set()
        self.problems: list[str] = []
        self._real = {
            name: getattr(os, name) for name in ("replace", "rename", "unlink", "remove", "fsync")
        }
        self._fdatasync = getattr(os, "fdatasync", None)

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "fsync", self._fsync)
        if self._fdatasync is not None:  # not on every platform (macOS has none)
            monkeypatch.setattr(os, "fdatasync", self._datasync)
        for name in ("replace", "rename"):
            monkeypatch.setattr(os, name, self._two_path(name))
        for name in ("unlink", "remove"):
            monkeypatch.setattr(os, name, self._one_path(name))

    def _fsync(self, fd: Any) -> None:
        number = fd if isinstance(fd, int) else fd.fileno()
        self.fsynced.add(_file_id(os.fstat(number)))
        self._real["fsync"](fd)

    def _datasync(self, fd: Any) -> None:
        number = fd if isinstance(fd, int) else fd.fileno()
        self.fsynced.add(_file_id(os.fstat(number)))
        assert self._fdatasync is not None
        self._fdatasync(fd)

    def _journal_files(self) -> list[Path]:
        skip = (self.store.objects_dir, self.store.manifests_dir)
        return [
            p
            for p in self.store.path.rglob("*")
            if p.is_file() and not p.is_symlink() and not any(d in p.parents for d in skip)
        ]

    def _check(self, *spellings: str | None) -> None:
        for spelled in spellings:
            if spelled is None or spelled not in self.touched:
                continue
            rel, before = self.touched[spelled]
            needles = [rel.encode()] + ([before.encode()] if before else [])
            holding = [
                p for p in self._journal_files() if all(n in p.read_bytes() for n in needles)
            ]
            if not holding:
                self.problems.append(f"{rel}: not in the journal when it was changed")
            elif not any(_file_id(p.stat()) in self.fsynced for p in holding):
                self.problems.append(f"{rel}: journal entry written but not fsynced")
            self.checked.add(rel)

    def _two_path(self, name: str) -> Callable[..., None]:
        def call(
            src: Any, dst: Any, *, src_dir_fd: int | None = None, dst_dir_fd: int | None = None
        ) -> None:
            self._check(
                _spelled(src, src_dir_fd, self.roots), _spelled(dst, dst_dir_fd, self.roots)
            )
            self._real[name](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        return call

    def _one_path(self, name: str) -> Callable[..., None]:
        def call(path: Any, *args: Any, **kwargs: Any) -> None:
            self._check(_spelled(path, kwargs.get("dir_fd"), self.roots))
            self._real[name](path, *args, **kwargs)

        return call


@pytest.mark.parametrize(
    "ops",
    [pytest.param(ops, id=f"constructed-{name}") for name, ops, _, _ in KILL_CASES],
)
def test_constructed_journal_entry_is_fsynced_before_the_path_changes(
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    ops: tuple[tuple[str, str], ...],
) -> None:
    touched: dict[str, str | None] = {
        rel: (sha(ALLOWLISTED_FILES[rel]) if rel in ALLOWLISTED_FILES else None) for _, rel in ops
    }
    spy = JournalAheadSpy(flavor, store, touched)
    with (
        guard.transaction(flavor, label="ahead", store=store.path) as tx,
        pytest.MonkeyPatch.context() as patched,
    ):
        spy.arm(patched)
        _run_ops(tx, ops)

    assert spy.checked == set(touched), "positive control: every touched path was seen changing"
    assert spy.problems == []


# ─── permission bits through undo and restore ────────────────────────────────


@pytest.fixture
def umask_022() -> Iterator[None]:
    """A known umask, so a file created with default permissions is 0o644,
    wider than the 0o600 these graders record."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


# Not 0o600: that is what `mkstemp` creates, so a guard that forgets to put
# the mode back would look right.
NARROW = 0o640

PERMISSION_CASES: tuple[tuple[str, str, str], ...] = (
    ("write-then-undo", "write", "undo"),
    ("write-then-restore-named", "write", "restore"),
    ("delete-then-undo", "delete", "undo"),
    ("delete-then-restore-named", "delete", "restore"),
)


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits; Windows has none to keep"
)
@pytest.mark.parametrize(
    ("op", "back"),
    [pytest.param(op, back, id=f"constructed-{name}") for name, op, back in PERMISSION_CASES],
)
def test_constructed_undo_and_restore_never_widen_permission_bits(
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    umask_022: None,
    op: str,
    back: str,
) -> None:
    """A file made narrower than the default (0o640) goes through a write or
    a delete and comes back by `undo()` or a named restore, with the same
    bytes. A file that still existed keeps exactly its mode; a recreated one
    gets no bit its recorded mode lacks (no group/other write, no execute)
    and the owner can still read and write it (a mode of 0 would break the
    client)."""
    target = flavor.path / BINDINGS
    target.chmod(NARROW)
    with guard.transaction(flavor, label="change", store=store.path) as tx:
        if op == "write":
            tx.write(BINDINGS, NEW_BINDINGS)
        else:
            tx.delete(BINDINGS)
    assert target.exists() == (op == "write"), "positive control: the change landed"

    if back == "undo":
        guard.undo(store=store.path)
    else:
        pre_id = record_for(guard, store, "change").snapshot_id
        with guard.transaction(flavor, label="back", store=store.path) as tx:
            tx.restore(pre_id, paths=[BINDINGS])

    assert target.read_bytes() == ALLOWLISTED_FILES[BINDINGS], "positive control: it came back"
    mode = stat.S_IMODE(target.stat().st_mode)
    if op == "write":
        assert mode == NARROW, f"an existing file's mode changed from {NARROW:o} to {mode:o}"
    else:
        assert mode & ~NARROW == 0, f"widened from {NARROW:o} to {mode:o}"
        assert mode & 0o600 == 0o600, f"owner lost read/write: {mode:o}"


# ─── Windows names ───────────────────────────────────────────────────────────


def _windows_short_name(path: Path) -> str | None:
    """The 8.3 alias of `path`'s last component, or None when 8.3 names are
    disabled on the volume (the alias is then the long name)."""
    listed = subprocess.run(
        ["cmd", "/c", f'for %I in ("{path}") do @echo %~sI'],
        capture_output=True,
        text=True,
        check=False,
    )
    short = listed.stdout.strip().rsplit("\\", 1)[-1]
    return None if not short or short.lower() == path.name.lower() else short


WINDOWS_NAMES: tuple[tuple[str, str], ...] = (
    ("alternate-data-stream", "WTF/Config.wtf:hidden"),
    ("drive-relative-inside-allowlist", "WTF/C:evil.txt"),
    ("backslash-traversal", "WTF\\..\\Wow.exe"),
    ("mixed-separators-traversal", "WTF/..\\..\\.build.info"),
    ("reserved-con", "WTF/CON"),
    ("reserved-nul-with-extension", "WTF/NUL.wtf"),
    ("reserved-com1", "WTF/COM1"),
    ("reserved-lpt1-with-extension", "WTF/lpt1.txt"),
    ("trailing-dot", "WTF/Config.wtf."),
    ("trailing-space", "WTF/Config.wtf "),
    ("short-name", "<SHORT>"),
)


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only: what these names mean on NTFS")
@pytest.mark.parametrize("op", ["write", "delete"])
@pytest.mark.parametrize(
    "rel", [pytest.param(rel, id=f"constructed-windows-{name}") for name, rel in WINDOWS_NAMES]
)
def test_constructed_windows_names_that_are_not_plain_paths_are_refused(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None, rel: str, op: str
) -> None:
    if rel == "<SHORT>":
        short = _windows_short_name(flavor.path / "WTF" / "Account" / "ACCT" / "SavedVariables")
        if short is None:
            pytest.skip("8.3 short names are disabled on this volume")
        rel = f"WTF/Account/ACCT/{short}/Addon.lua"
    before = content(world)
    with (
        guard.transaction(flavor, label="win-names", store=store.path) as tx,
        pytest.raises(guard.PathNotAllowedError),
    ):
        if op == "write":
            tx.write(rel, MUST_NEVER_LAND)
        else:
            tx.delete(rel)
    assert content(world) == before


# ─── dry run ─────────────────────────────────────────────────────────────────


def planned_size(item: Any) -> int:
    size = getattr(item, "size", None)
    return int(size) if size is not None else len(item.data)


def test_constructed_dry_run_returns_the_plan_and_touches_nothing(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    before = strict_state(world)

    with guard.transaction(flavor, label="plan", store=store.path, dry_run=True) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(NEW_SAVED, NEW_LUA)
        tx.delete(BINDINGS)

    assert strict_state(world) == before, "not a byte, a temp file or an mtime"
    assert list(guard.history(store=store.path)) == [], "a plan is not a transaction"
    plan = tuple(tx.plan)
    assert changes(plan) == {
        CONFIG: (sha(ALLOWLISTED_FILES[CONFIG]), sha(NEW_CONFIG)),
        NEW_SAVED: (None, sha(NEW_LUA)),
        BINDINGS: (sha(ALLOWLISTED_FILES[BINDINGS]), None),
    }
    sizes = {item.path: planned_size(item) for item in plan if item.after is not None}
    assert sizes == {CONFIG: len(NEW_CONFIG), NEW_SAVED: len(NEW_LUA)}


def test_constructed_dry_run_restore_plans_without_touching_anything(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    with guard.transaction(flavor, label="change", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    original = record_for(guard, store, "change").snapshot_id
    before = strict_state(world)

    with guard.transaction(flavor, label="plan-restore", store=store.path, dry_run=True) as tx:
        tx.restore(original, paths=[CONFIG])

    assert strict_state(world) == before
    assert changes(tx.plan) == {CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG]))}


def test_constructed_dry_run_still_refuses_a_forbidden_path(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """The CLI prints the plan and then runs it (§6.10); a plan the gate would
    refuse is not a plan."""
    before = strict_state(world)
    with guard.transaction(flavor, label="plan-bad", store=store.path, dry_run=True) as tx:
        with pytest.raises(guard.PathNotAllowedError):
            tx.write("../.build.info", MUST_NEVER_LAND)
        with pytest.raises(guard.PathNotAllowedError):
            tx.delete("Wow.exe")
    assert strict_state(world) == before


# ─── no bypass ───────────────────────────────────────────────────────────────

PUBLIC_API = {
    "transaction",
    "undo",
    "history",
    "GuardError",
    "ClientRunningError",
    "PathNotAllowedError",
}


def _own_public_methods(cls: type) -> set[str]:
    """Public methods defined on `cls` itself: inherited machinery (a Pydantic
    base, `object`) is not the transaction's surface."""
    return {
        name
        for name, member in inspect.getmembers(cls, callable)
        if not name.startswith("_")
        and getattr(member, "__qualname__", "").startswith(cls.__name__ + ".")
    }


def _own_public_data(tx: Any) -> set[str]:
    """Public instance state: `__dict__` entries, `__slots__` names along the
    MRO, and properties on the class itself. A `__slots__` class is measured,
    not crashed on."""
    instance = {name for name in getattr(tx, "__dict__", {}) if not name.startswith("_")}
    slots = {
        name
        for cls in type(tx).__mro__
        for name in (
            (getattr(cls, "__slots__", ()),)
            if isinstance(getattr(cls, "__slots__", ()), str)
            else getattr(cls, "__slots__", ())
        )
        if not name.startswith("_")
    }
    properties = {
        name
        for name, member in vars(type(tx)).items()
        if isinstance(member, property) and not name.startswith("_")
    }
    return instance | slots | properties


def _immutable(value: Any) -> bool:
    """None, str, bytes, numbers, Enum members, and tuples, frozensets,
    frozen Pydantic models and frozen dataclasses made only of those."""
    if value is None or isinstance(value, (str, bytes, int, float, enum.Enum)):
        return True
    if isinstance(value, (tuple, frozenset)):
        return all(_immutable(item) for item in value)
    if isinstance(value, BaseModel):
        return type(value).model_config.get("frozen") is True and all(
            _immutable(getattr(value, field)) for field in type(value).model_fields
        )
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return type(value).__dataclass_params__.frozen and all(  # type: ignore[attr-defined]
            _immutable(getattr(value, f.name)) for f in dataclasses.fields(value)
        )
    return False


def _type_checking_is_only_an_if_test(guard: Any) -> None:
    tree = ast.parse(Path(guard.__file__).read_text(encoding="utf-8"))
    assert vars(guard)["TYPE_CHECKING"] is False
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "typing"
        and any(a.name == "TYPE_CHECKING" and a.asname is None for a in node.names)
        for node in ast.walk(tree)
    ), "TYPE_CHECKING must come from typing"
    if_tests = {
        id(node.test)
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
    }
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "TYPE_CHECKING"]
    assert all(id(n) in if_tests for n in uses), (
        "TYPE_CHECKING used other than as `if TYPE_CHECKING:`"
    )


def test_constructed_public_surface_is_exactly_the_gate(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """No parameter, attribute, helper or flag beyond §6.10's API: nothing to
    reach for that skips the client check, the snapshot or the allowlist."""
    ours = {
        name: obj
        for name, obj in vars(guard).items()
        if not name.startswith("_")
        and callable(obj)
        and getattr(obj, "__module__", None) == guard.__name__
    }
    # Beyond the API, only data: public Pydantic models (history records, plan
    # items) that the CLI can validate `--json` against, with no public
    # methods of their own.
    for name in set(ours) - PUBLIC_API:
        obj = ours[name]
        assert inspect.isclass(obj) and issubclass(obj, BaseModel), f"not in the API: {name}"
        assert _own_public_methods(obj) == set(), f"{name} has public methods"
    assert {"transaction", "undo", "history"} <= set(ours)
    for name, obj in vars(guard).items():
        if name.startswith("_") or callable(obj) or obj is __future__.annotations:
            continue
        if name == "TYPE_CHECKING":
            _type_checking_is_only_an_if_test(guard)
            continue
        allowed = isinstance(obj, types.ModuleType) or (
            isinstance(obj, (str, int, tuple, frozenset, enum.Enum))
            and not isinstance(obj, bool)
            and _immutable(obj)
        )
        assert allowed, f"public module global {name} is a {type(obj).__name__}"
    for name in set(ours) - PUBLIC_API:
        assert ours[name].model_config.get("frozen") is True, f"{name} is not frozen"

    with guard.transaction(flavor, label="surface", store=store.path) as tx:
        assert _own_public_methods(type(tx)) == {"write", "delete", "restore"}
        data = _own_public_data(tx)
        assert "plan" in data
        # Anything else public is a read-only property: nothing to assign.
        for name in data - {"plan"}:
            member = inspect.getattr_static(type(tx), name, None)
            assert isinstance(member, property), f"public writable attribute: {name}"
            assert member.fset is None, f"property with a setter: {name}"
            assert _immutable(getattr(tx, name)), f"{name} hands back a mutable value"
        assert _immutable(tx.plan)
    with guard.transaction(
        flavor, label="surface-plan", store=store.path, dry_run=True
    ) as planning:
        planning.write(CONFIG, NEW_CONFIG)
        assert len(planning.plan) == 1
        assert _immutable(planning.plan), "plan items are frozen, all the way down"

    transaction = inspect.signature(guard.transaction).parameters
    assert list(transaction) == ["flavor", "label", "store", "dry_run"]
    for name in ("label", "store", "dry_run"):
        assert transaction[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert transaction["label"].default == ""
    assert transaction["store"].default is None
    assert transaction["dry_run"].default is False
    assert list(inspect.signature(guard.undo).parameters) == ["store"]
    assert list(inspect.signature(guard.history).parameters) == ["store"]
    # `store` is a directory path, never an object a caller could have
    # prepared: guard builds its own SnapshotStore (the last injection seam).
    for fn in (guard.transaction, guard.undo, guard.history):
        annotated = inspect.signature(fn, eval_str=True).parameters["store"].annotation
        assert annotated == Path | None, f"{fn.__name__}: store is {annotated!r}"
    with (
        pytest.raises((guard.GuardError, TypeError, AttributeError)),
        guard.transaction(flavor, label="object", store=store),  # a store object, not a path
    ):
        pass
    assert list(inspect.signature(tx.write).parameters) == ["rel_path", "data"]
    assert list(inspect.signature(tx.delete).parameters) == ["rel_path"]
    restore = inspect.signature(tx.restore).parameters
    assert list(restore) == ["snapshot_id", "paths"]
    assert restore["paths"].default is None


def test_constructed_no_module_global_is_a_boolean_switch(guard: Any) -> None:
    """A private `_SKIP_CLIENT_CHECK = False` is a flag one edit or one
    monkeypatch away from skipping the gate."""
    switches = [
        name
        for name, value in vars(guard).items()
        if isinstance(value, bool)
        and not (name.startswith("__") and name.endswith("__"))
        and name != "TYPE_CHECKING"
    ]
    assert switches == []


def test_constructed_transaction_object_is_dead_after_exit(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    with guard.transaction(flavor, label="done", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    snapshot_id = record_for(guard, store, "done").snapshot_id
    after = content(world)
    history = list(guard.history(store=store.path))

    with pytest.raises(guard.GuardError):
        tx.write(CONFIG, NEWER_CONFIG)
    with pytest.raises(guard.GuardError):
        tx.delete(BINDINGS)
    with pytest.raises(guard.GuardError):
        tx.restore(snapshot_id)

    assert content(world) == after
    assert list(guard.history(store=store.path)) == history


def test_constructed_no_boolean_flag_skips_the_client_check_or_the_snapshot(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Name-blind: every boolean parameter of `transaction`, set either way,
    either writes nothing at all or behaves like the gate."""
    flags = [
        name
        for name, parameter in inspect.signature(guard.transaction).parameters.items()
        if isinstance(parameter.default, bool)
    ]
    assert "dry_run" in flags
    pristine = content(world)

    for number, (flag, value) in enumerate((f, v) for f in flags for v in (True, False)):
        # Client running: refused, or a mode that writes nothing.
        use_probe(monkeypatch, running_table(tmp_path))
        refused_store = SnapshotStore(tmp_path / f"flag-store-{number}-running")
        try:
            with guard.transaction(
                flavor, label="flag", store=refused_store.path, **{flag: value}
            ) as tx:
                tx.write(CONFIG, NEW_CONFIG)
        except guard.ClientRunningError:
            pass
        assert content(world) == pristine, f"{flag}={value} wrote while the client was running"

        # Client idle: if the write landed, the old bytes are in a snapshot.
        use_probe(monkeypatch, table(*bystanders(tmp_path)))
        idle_store = SnapshotStore(tmp_path / f"flag-store-{number}-idle")
        with guard.transaction(flavor, label="flag", store=idle_store.path, **{flag: value}) as tx:
            tx.write(CONFIG, NEW_CONFIG)
        if content(world) != pristine:
            held = [
                idle_store.read_object(entry_for(m, CONFIG).sha256 or "") for m in idle_store.list()
            ]
            assert ALLOWLISTED_FILES[CONFIG] in held, f"{flag}={value} wrote without a snapshot"
            (flavor.path / CONFIG).write_bytes(
                ALLOWLISTED_FILES[CONFIG]
            )  # reset the synthetic tree
            assert content(world) == pristine


@pytest.mark.parametrize(
    "variable",
    [
        pytest.param(name, id=f"constructed-{name}")
        for name in (
            "WOWLAB_FORCE",
            "WOWLAB_UNSAFE",
            "WOWLAB_SKIP_SNAPSHOT",
            "WOWLAB_NO_SNAPSHOT",
            "WOWLAB_SKIP_CLIENT_CHECK",
            "WOWLAB_NO_CLIENT_CHECK",
            "WOWLAB_ASSUME_NOT_RUNNING",
            "WOWLAB_GUARD_DISABLE",
        )
    ],
)
def test_constructed_no_environment_variable_skips_the_gate(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    monkeypatch.setenv(variable, "1")
    before = strict_state(world)

    use_probe(monkeypatch, running_table(tmp_path))
    with (
        pytest.raises(guard.ClientRunningError),
        guard.transaction(flavor, label="env-running", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
    assert strict_state(world) == before

    use_probe(monkeypatch, table(*bystanders(tmp_path)))
    with guard.transaction(flavor, label="env-idle", store=store.path) as tx:
        with pytest.raises(guard.PathNotAllowedError):
            tx.write("../.build.info", MUST_NEVER_LAND)
        assert len(store.list()) == 1, "the snapshot is still taken first"
        tx.write(CONFIG, NEW_CONFIG)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
