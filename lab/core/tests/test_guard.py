"""Graders for `wowlab_core.guard`, the write gate (M10-11T).

Written before `guard.py` exists, from `docs/LAB_PLAN.md` §6.10 (with §6.7 and
§6.9 for the two modules it consumes), ADR-0021, ADR-0023 and invariants L1,
L2 and L7. Every test carries one marker line that M10-11 deletes; nothing
else in this file is the implementer's to change.

Everything here is constructed, as the ticket requires: the install is a
synthetic tree under `tmp_path`, the snapshot store is a directory under
`tmp_path`, the user data directory is redirected, and the process table is
injected. No test needs or touches a real install, the real user data
directory or the real process table.

The seam these graders hold `guard` to
--------------------------------------
§6.10 fixes `transaction(flavor, label=...)`, `tx.write(rel_path, data)`,
`tx.delete(rel_path)`, `tx.restore(snapshot_id, paths=None)`, `undo()` and
`history()`. It does not name the errors, the journal fields or the injection
points, so they are pinned here, as small as they could be made:

- `guard.transaction(flavor, *, label="", store=None, process_iter=None,
  dry_run=False)` is a context manager that yields the transaction.
  `flavor` is anything with the attributes of `Flavor` in §6.1 (`guard` does
  not depend on M10-05, so it must not `isinstance`-check); `flavor.path` is
  the flavor folder, its parent is the install root, and every `rel_path` is
  `/`-separated and relative to `flavor.path`.
  `store` is a `wowlab_core.snapshot.SnapshotStore`; `None` means the default
  store. `process_iter` is the test seam of `wowlab_core.process`, passed
  through untouched; `None` means that module's default probe.
- `guard.undo(*, store=None, process_iter=None)` and
  `guard.history(*, store=None)`.
- Errors: `guard.GuardError` is the base of everything `guard` raises on
  purpose; `guard.ClientRunningError` (running, unknown, or a probe that
  raised) and `guard.PathNotAllowedError` are subclasses. A write the
  operating system refuses surfaces as a `GuardError`, never a bare `OSError`.
- A history record has `label`, `snapshot_id` (the pre-write snapshot),
  `rolled_back` and `paths`; each item of `paths` has `path` (as the caller
  spelled it), `before` and `after` (SHA-256 hex, `None` for absent).
- `tx.plan` is a tuple of items with `path`, `before`, `after` and `size`
  (bytes that would be written).
- The atomic replace is `os.replace` (or `Path.replace`, which calls it),
  looked up at call time, and `os.fsync` runs on the temp file first.

Nothing is asserted about how the pre-write snapshot is rooted: entry paths
are matched by suffix, so `WTF/Config.wtf` and `_retail_/WTF/Config.wtf` both
satisfy these graders.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from pydantic import BaseModel

from wowlab_core.snapshot import (
    Entry,
    Manifest,
    SnapshotError,
    SnapshotStore,
    manifest_bytes,
    tree_fingerprint,
)

# ─── the synthetic install ───────────────────────────────────────────────────

FLAVOR_FOLDER = "_retail_"  # tests may name a flavor folder; the library may not (L6)

CONFIG = "WTF/Config.wtf"
BINDINGS = "WTF/Account/ACCT/bindings-cache.wtf"
SAVED = "WTF/Account/ACCT/SavedVariables/Addon.lua"
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


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _user_data_redirected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A default store, if `guard` ever reaches for one, lands in `tmp_path`."""
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected


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
def idle(tmp_path: Path) -> ProcessTable:
    """A process table with no game client in it."""
    return table(*bystanders(tmp_path))


def record_for(guard: Any, store: SnapshotStore, label: str) -> Any:
    matches = [r for r in guard.history(store=store) if r.label == label]
    assert len(matches) == 1, f"expected one journal record labelled {label!r}, got {matches!r}"
    return matches[0]


def changes(record_or_plan_items: Iterable[Any]) -> dict[str, tuple[str | None, str | None]]:
    return {item.path: (item.before, item.after) for item in record_or_plan_items}


def entry_for(manifest: Manifest, rel: str) -> Entry:
    """The manifest entry for a flavor-relative path, however the snapshot is rooted."""
    found = [e for e in manifest.entries if e.path == rel or e.path.endswith("/" + rel)]
    assert len(found) == 1, f"{rel} should appear once in snapshot {manifest.id}: {found!r}"
    return found[0]


def manifest_prefix(manifest: Manifest) -> str:
    """What precedes a flavor-relative path in this manifest's entry paths."""
    anchor = entry_for(manifest, CONFIG)
    return anchor.path[: -len(CONFIG)]


# ─── positive controls: allowlisted paths are written ────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.parametrize(
    ("rel", "data"),
    [
        pytest.param(CONFIG, NEW_CONFIG, id="constructed-wtf-replace"),
        pytest.param(
            "WTF/Account/ACCT/SavedVariables/New.lua", NEW_LUA, id="constructed-wtf-create"
        ),
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
def test_allowlisted_path_is_written(
    guard: Any,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    rel: str,
    data: bytes,
) -> None:
    expected = content(world)
    expected[f"World of Warcraft/{FLAVOR_FOLDER}/{rel}"] = data

    with guard.transaction(flavor, label="positive", store=store, process_iter=idle) as tx:
        tx.write(rel, data)

    assert (flavor.path / rel).read_bytes() == data
    # Exactly that one path changed: no temp file, journal, lock or backup was
    # left anywhere in the install or beside it (L1, L2).
    assert content(world) == expected


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_write_creates_missing_parent_directories_inside_the_allowlist(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    # The API has no mkdir, and the tools ADR-0021 names (addon scaffolding,
    # restore of a deleted addon) need files in directories that do not exist.
    rel = "Interface/AddOns/Scaffolded/Scaffolded.toc"
    with guard.transaction(flavor, label="scaffold", store=store, process_iter=idle) as tx:
        tx.write(rel, b"## Title: Scaffolded\n")
    assert (flavor.path / rel).read_bytes() == b"## Title: Scaffolded\n"


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_delete_removes_an_allowlisted_file(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    expected = content(world)
    del expected[f"World of Warcraft/{FLAVOR_FOLDER}/{BINDINGS}"]

    with guard.transaction(flavor, label="delete", store=store, process_iter=idle) as tx:
        tx.delete(BINDINGS)

    assert not (flavor.path / BINDINGS).exists()
    assert content(world) == expected


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
        "running-by-name": table(
            *bystanders(tmp_path), FakeProcess(7001, name="Wow.exe", exe=str(elsewhere / "Wow.exe"))
        ),
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
        # Nothing could be read about the process: unknown, which counts as running.
        "unknown-access-denied": table(
            *bystanders(tmp_path), FakeProcess(7004, name=DENIED, exe=DENIED, cmdline=DENIED)
        ),
        # A probe that raises is a refusal: not a crash, and not a pass.
        "probe-raises": _raising_probe,
        "probe-raises-midway": _probe_that_dies_midway(tmp_path),
    }


REFUSING_TABLES = (
    "running-by-name",
    "running-unlisted-name-under-the-flavor-folder",
    "running-unlisted-name-at-the-install-root",
    "unknown-access-denied",
    "probe-raises",
    "probe-raises-midway",
)


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.parametrize("which", [pytest.param(w, id=f"constructed-{w}") for w in REFUSING_TABLES])
def test_transaction_refuses_when_the_client_is_running_or_unknown(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    which: str,
) -> None:
    probe = _refusing_tables(tmp_path, install_root)[which]
    before = strict_state(world)
    entered = False

    with (
        pytest.raises(guard.ClientRunningError),
        guard.transaction(flavor, label="refused", store=store, process_iter=probe) as tx,
    ):
        entered = True
        tx.write(CONFIG, NEW_CONFIG)

    assert not entered, "the body of a refused transaction must never run"
    assert strict_state(world) == before


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_client_refusal_is_a_guard_error_and_not_a_path_refusal(guard: Any) -> None:
    assert issubclass(guard.ClientRunningError, guard.GuardError)
    assert issubclass(guard.PathNotAllowedError, guard.GuardError)
    assert not issubclass(guard.ClientRunningError, guard.PathNotAllowedError)
    assert not issubclass(guard.PathNotAllowedError, guard.ClientRunningError)
    assert not issubclass(guard.GuardError, OSError)


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_default_probe_is_the_process_modules_default(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no `process_iter`, guard asks `wowlab_core.process`, whose default
    probe is `psutil.process_iter`. The real process table is never read here:
    `psutil.process_iter` is replaced for the length of the test."""
    assert inspect.signature(guard.transaction).parameters["process_iter"].default is None
    assert inspect.signature(guard.undo).parameters["process_iter"].default is None

    client = FakeProcess(7100, name="World of Warcraft", exe=str(tmp_path / "elsewhere" / "wow"))
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([client]))
    before = strict_state(world)
    with (
        pytest.raises(guard.ClientRunningError),
        guard.transaction(flavor, label="default-probe", store=store) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
    assert strict_state(world) == before

    # Positive control through the same default: no client, so the write lands.
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(bystanders(tmp_path)))
    with guard.transaction(flavor, label="default-probe-idle", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_guard_never_touches_psutil_itself(guard: Any) -> None:
    """Process listing belongs to `wowlab_core.process` (ADR-0023, §6.7 as
    amended: production callers use the default probe and never wrap psutil
    objects). `guard` has no reason to import psutil, ctypes or a subprocess."""
    tree = ast.parse(Path(guard.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"psutil", "ctypes", "subprocess", "_winapi"}), sorted(imported)
    assert "wowlab_core" in imported or any(
        isinstance(node, ast.ImportFrom) and node.level > 0 for node in ast.walk(tree)
    ), "guard must reach the process check through wowlab_core.process"


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_undo_refuses_while_the_client_runs(
    guard: Any, tmp_path: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    with guard.transaction(flavor, label="to-undo", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)

    running = table(FakeProcess(7200, name="WowClassic.exe", exe=str(tmp_path / "WowClassic.exe")))
    with pytest.raises(guard.ClientRunningError):
        guard.undo(store=store, process_iter=running)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG

    with pytest.raises(guard.ClientRunningError):
        guard.undo(store=store, process_iter=_raising_probe)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG


# ─── the allowlist ───────────────────────────────────────────────────────────

# `<WORLD>` and `<FLAVOR>` are replaced with absolute paths at run time.
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
    # `..` traversal that starts inside the allowlist.
    ("traversal-to-build-info", "WTF/../../.build.info"),
    ("traversal-to-flavor-info", "WTF/../.flavor.info"),
    ("traversal-to-executable", "Interface/AddOns/../../Wow.exe"),
    ("traversal-to-data", "Interface/AddOns/../../../Data/data/data.001"),
    ("traversal-out-of-the-install", "WTF/Account/../../../../outside/target.txt"),
    ("traversal-to-logs", "Fonts/../Logs/Client.log"),
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
    return rel.replace("<WORLD>", str(world)).replace("<FLAVOR>", str(flavor.path))


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.parametrize("op", ["write", "delete"])
@pytest.mark.parametrize(
    "rel", [pytest.param(rel, id=f"constructed-{name}") for name, rel in FORBIDDEN_PATHS]
)
def test_path_outside_the_allowlist_is_refused(
    guard: Any,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    rel: str,
    op: str,
) -> None:
    target = _expand(rel, world, flavor)
    before = strict_state(world)

    with guard.transaction(flavor, label="forbidden", store=store, process_iter=idle) as tx:
        with pytest.raises(guard.PathNotAllowedError):
            if op == "write":
                tx.write(target, b"constructed: must never land")
            else:
                tx.delete(target)
        # Positive control in the same transaction: the refusal above was
        # about the path, not about the transaction, the probe or the store.
        tx.write(CONFIG, NEW_CONFIG)

    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    after = strict_state(world)
    config_key = f"World of Warcraft/{FLAVOR_FOLDER}/{CONFIG}"
    config_dir = f"World of Warcraft/{FLAVOR_FOLDER}/WTF"
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    # Only the control write shows: its file, and the mtime of its directory.
    assert changed <= {config_key, config_dir}, sorted(changed)


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_refusal_that_propagates_rolls_back_the_writes_before_it(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    before = content(world)
    with (
        pytest.raises(guard.PathNotAllowedError),
        guard.transaction(flavor, label="refused-midway", store=store, process_iter=idle) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        tx.write("../.build.info", b"constructed: must never land")
    assert content(world) == before
    assert record_for(guard, store, "refused-midway").rolled_back is True


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


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.parametrize("op", ["write", "delete"])
@pytest.mark.parametrize(
    "rel",
    [pytest.param(rel, id=f"constructed-{name}") for name, rel in CASE_VARIANTS_OF_FORBIDDEN],
)
def test_case_variant_of_a_forbidden_path_is_refused(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    rel: str,
    op: str,
) -> None:
    if not case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive filesystem (default macOS volumes, Windows)")
    before = strict_state(world)
    with (
        guard.transaction(flavor, label="case", store=store, process_iter=idle) as tx,
        pytest.raises(guard.PathNotAllowedError),
    ):
        if op == "write":
            tx.write(rel, b"constructed: must never land")
        else:
            tx.delete(rel)
    assert strict_state(world) == before


CASE_COLLISIONS: tuple[tuple[str, str], ...] = (
    ("file-name", "WTF/config.wtf"),
    ("subtree-name", "wtf/Config.wtf"),
    ("middle-directory", "Interface/addons/Thing/Thing.toc"),
    ("new-file-under-a-respelled-directory", "WTF/account/ACCT/New.lua"),
)


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.parametrize(
    "rel", [pytest.param(rel, id=f"constructed-{name}") for name, rel in CASE_COLLISIONS]
)
def test_case_collision_with_an_existing_path_is_refused(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    rel: str,
) -> None:
    """§6.10 "case-collision tricks": on a case-insensitive volume a respelled
    path lands on the existing file while the journal and the snapshot know it
    under another name, so a later rollback by name could delete the original."""
    if not case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive filesystem (default macOS volumes, Windows)")
    before = strict_state(world)
    with (
        guard.transaction(flavor, label="collision", store=store, process_iter=idle) as tx,
        pytest.raises(guard.PathNotAllowedError),
    ):
        tx.write(rel, b"constructed: must never land")
    assert strict_state(world) == before


# ─── symlinks and junctions ──────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.parametrize(
    "case",
    [
        pytest.param("dir-link-out-of-the-install", id="constructed-dir-link-out-of-the-install"),
        pytest.param("file-link-out-of-the-install", id="constructed-file-link-out-of-the-install"),
        pytest.param("dir-link-to-data", id="constructed-dir-link-to-data"),
        pytest.param("file-link-to-the-executable", id="constructed-file-link-to-the-executable"),
    ],
)
def test_symlink_that_escapes_the_allowlist_is_refused(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    case: str,
) -> None:
    wtf = flavor.path / "WTF"
    if case == "dir-link-out-of-the-install":
        symlink_or_skip(wtf / "escape", world / "outside", is_dir=True)
        rel = "WTF/escape/target.txt"
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

    with guard.transaction(flavor, label="symlink", store=store, process_iter=idle) as tx:
        with pytest.raises(guard.PathNotAllowedError):
            tx.write(rel, b"constructed: must never land")
        with pytest.raises(guard.PathNotAllowedError):
            tx.delete(rel)

    assert content(world) == before
    assert (world / "outside" / "target.txt").read_bytes() == OUTSIDE_TARGET


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_allowlisted_subtree_that_is_itself_an_escaping_symlink_is_refused(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
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
        guard.transaction(flavor, label="linked-subtree", store=store, process_iter=idle) as tx,
    ):
        tx.write("Fonts/pwned.ttf", b"constructed: must never land")

    assert content(world) == before
    assert not (world / "outside" / "pwned.ttf").exists()


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.skipif(sys.platform != "win32", reason="windows-only: NTFS junctions")
def test_windows_junction_that_escapes_the_install_is_refused(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
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

    with guard.transaction(flavor, label="junction", store=store, process_iter=idle) as tx:
        with pytest.raises(guard.PathNotAllowedError):
            tx.write("WTF/junction/pwned.txt", b"constructed: must never land")
        with pytest.raises(guard.PathNotAllowedError):
            tx.write("WTF/junction/target.txt", b"constructed: must never land")
        with pytest.raises(guard.PathNotAllowedError):
            tx.delete("WTF/junction/target.txt")

    assert content(world / "outside") == outside_before


# ─── snapshot first ──────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_snapshot_is_taken_on_enter_before_the_first_write(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    assert store.list() == ()

    with guard.transaction(flavor, label="snapshot-first", store=store, process_iter=idle) as tx:
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


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_pre_write_snapshot_covers_the_default_subtrees_and_nothing_else(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    """§6.9 default subtrees: `WTF/`, `Interface/AddOns/`, `Fonts/` and loose
    `Interface/` files; never `Data/`, `Cache/`, `Logs/`, `Screenshots/`."""
    with guard.transaction(flavor, label="coverage", store=store, process_iter=idle):
        pass
    pre = store.show(record_for(guard, store, "coverage").snapshot_id)
    prefix = manifest_prefix(pre)
    assert {e.path.removeprefix(prefix) for e in pre.entries} == set(ALLOWLISTED_FILES)
    for entry in pre.entries:
        assert entry.sha256 == sha(ALLOWLISTED_FILES[entry.path.removeprefix(prefix)])


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_rollback_survives_a_store_gc_during_the_open_transaction(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    """Rollback is "from the pre-write snapshot" (§6.10), so what it needs has
    to be held by a manifest, not by loose objects a `gc` would collect."""
    before = content(world)
    with (
        pytest.raises(RuntimeError, match="constructed failure"),
        guard.transaction(flavor, label="gc", store=store, process_iter=idle) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
        store.gc(dry_run=False)
        raise RuntimeError("constructed failure")
    assert content(world) == before


# ─── the journal ─────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_journal_records_before_and_after_hashes_for_every_touched_path(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    created = "WTF/Account/ACCT/SavedVariables/New.lua"
    with guard.transaction(flavor, label="journal", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(created, NEW_LUA)
        tx.delete(BINDINGS)

    record = record_for(guard, store, "journal")
    assert changes(record.paths) == {
        CONFIG: (sha(ALLOWLISTED_FILES[CONFIG]), sha(NEW_CONFIG)),
        created: (None, sha(NEW_LUA)),
        BINDINGS: (sha(ALLOWLISTED_FILES[BINDINGS]), None),
    }
    assert record.rolled_back is False
    assert store.show(record.snapshot_id).id == record.snapshot_id


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_journal_lists_a_path_once_with_its_first_before_and_last_after(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    with guard.transaction(flavor, label="twice", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(CONFIG, NEWER_CONFIG)
    record = record_for(guard, store, "twice")
    assert [item.path for item in record.paths] == [CONFIG]
    assert changes(record.paths) == {CONFIG: (sha(ALLOWLISTED_FILES[CONFIG]), sha(NEWER_CONFIG))}


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_journal_lives_with_the_store_and_leaves_the_store_healthy(
    guard: Any,
    tmp_path: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    _user_data_redirected: Path,
) -> None:
    assert list(guard.history(store=store)) == []

    with guard.transaction(flavor, label="first", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    with guard.transaction(flavor, label="second", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEWER_CONFIG)

    # Read back through a second store object: the journal is on disk, under
    # the store it was given, and not in memory or anywhere else.
    reopened = SnapshotStore(store.path)
    assert {r.label for r in guard.history(store=reopened)} == {"first", "second"}
    assert list(guard.history(store=SnapshotStore(tmp_path / "another-store"))) == []
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


class ReplaceSpy:
    """Stands in for `os.replace` and `os.rename`. Calls that land inside the
    install are recorded, and the `fail_on`-th of them raises once; every
    other call (the snapshot store's own, a rollback's) goes through."""

    def __init__(self, install_root: Path, *, fail_on: int, error: BaseException) -> None:
        self.install_root = install_root
        self.fail_on = fail_on
        self.error = error
        self.fsyncs = 0
        self.seen: list[dict[str, Any]] = []
        self._replace = os.replace
        self._fsync = os.fsync

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "replace", self.replace)
        monkeypatch.setattr(os, "rename", self.replace)
        monkeypatch.setattr(os, "fsync", self.fsync)

    def fsync(self, fd: int) -> None:
        self.fsyncs += 1
        self._fsync(fd)

    def replace(self, src: Any, dst: Any, **kwargs: Any) -> None:
        source, target = Path(os.fsdecode(src)), Path(os.fsdecode(dst))
        if _is_under(target, self.install_root):
            self.seen.append(
                {
                    "src": source,
                    "dst": target,
                    "src_bytes": source.read_bytes(),
                    "dst_bytes": target.read_bytes() if target.exists() else None,
                    "fsyncs": self.fsyncs,
                }
            )
            if len(self.seen) == self.fail_on:
                raise self.error
        self._replace(src, dst, **kwargs)


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_crash_between_temp_write_and_rename_leaves_the_original_intact(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = content(world)
    spy = ReplaceSpy(install_root, fail_on=1, error=SimulatedCrashError())

    with (
        pytest.raises(SimulatedCrashError),
        guard.transaction(flavor, label="crash", store=store, process_iter=idle) as tx,
    ):
        spy.install(monkeypatch)  # after enter: the pre-write snapshot is not what is graded
        tx.write(CONFIG, NEW_CONFIG)
    monkeypatch.undo()

    # What the disk looked like at the instant of the rename (§6.10: temp file
    # in the same directory, fsync, atomic replace).
    assert spy.seen, "the write must go through os.replace"
    at_rename = spy.seen[0]
    assert at_rename["dst"].resolve() == (flavor.path / CONFIG).resolve()
    assert at_rename["dst_bytes"] == ALLOWLISTED_FILES[CONFIG], "original intact until the rename"
    assert at_rename["src_bytes"] == NEW_CONFIG, "the temp file is complete before the rename"
    assert at_rename["src"].resolve().parent == (flavor.path / CONFIG).resolve().parent
    assert at_rename["src"].resolve() != at_rename["dst"].resolve()
    assert at_rename["fsyncs"] >= 1, "the temp file is fsynced before the rename"

    # After the crash every original path still has its original bytes. A
    # leftover temp file is what a real crash leaves, so extras are tolerated.
    after = content(world)
    assert {k: after.get(k) for k in before} == before


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_operating_system_refusal_is_a_typed_error_and_rolls_everything_back(
    guard: Any,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows locked-file case, simulated on every platform: the second
    replace is refused by the operating system."""
    before = content(world)
    spy = ReplaceSpy(
        install_root, fail_on=2, error=PermissionError(13, "constructed: file is locked")
    )

    with pytest.raises(guard.GuardError) as refused:  # noqa: SIM117 - the raise is the point
        with guard.transaction(flavor, label="locked", store=store, process_iter=idle) as tx:
            spy.install(monkeypatch)
            tx.write(CONFIG, NEW_CONFIG)
            assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
            tx.write(BINDINGS, b"bind S MOVEBACKWARD\n")
    monkeypatch.undo()

    assert not isinstance(refused.value, (guard.ClientRunningError, guard.PathNotAllowedError))
    assert not isinstance(refused.value, OSError)
    assert "bindings-cache.wtf" in str(refused.value), "the report names the file"
    assert content(world) == before, "the first write is rolled back and no temp file is left"
    assert record_for(guard, store, "locked").rolled_back is True


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.skipif(sys.platform != "win32", reason="windows-only: a file held open is locked")
def test_windows_locked_file_is_a_typed_error_and_rolls_back(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    """M10-11 acceptance, for the `lab (windows)` CI job: the client can hold
    its configuration files open (ADR-0021)."""
    before = content(world)
    with (flavor.path / BINDINGS).open("rb") as held:  # no FILE_SHARE_DELETE
        with pytest.raises(guard.GuardError) as refused:  # noqa: SIM117
            with guard.transaction(flavor, label="win-lock", store=store, process_iter=idle) as tx:
                tx.write(CONFIG, NEW_CONFIG)
                tx.write(BINDINGS, b"bind S MOVEBACKWARD\n")
        assert held.read() == ALLOWLISTED_FILES[BINDINGS]

    assert not isinstance(refused.value, (guard.ClientRunningError, guard.PathNotAllowedError))
    assert not isinstance(refused.value, OSError)
    assert content(world) == before
    assert record_for(guard, store, "win-lock").rolled_back is True


# ─── rollback, undo, restore ─────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_exception_inside_the_transaction_rolls_every_touched_path_back(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    before = content(world)
    created = "WTF/Account/ACCT/SavedVariables/New.lua"

    with (
        pytest.raises(RuntimeError, match="constructed failure"),
        guard.transaction(flavor, label="rollback", store=store, process_iter=idle) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(created, NEW_LUA)
        tx.write(ICON, b"BLP2\x09")
        tx.delete(BINDINGS)
        tx.delete(FONT)
        # The operations really happened; this is not a deferred plan.
        assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
        assert (flavor.path / created).read_bytes() == NEW_LUA
        assert not (flavor.path / BINDINGS).exists()
        raise RuntimeError("constructed failure")

    assert content(world) == before
    record = record_for(guard, store, "rollback")
    assert record.rolled_back is True


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_undo_restores_the_pre_transaction_bytes(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    before = content(world)
    with guard.transaction(flavor, label="undo-me", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write("WTF/Account/ACCT/SavedVariables/New.lua", NEW_LUA)
        tx.delete(BINDINGS)
    assert content(world) != before

    guard.undo(store=store, process_iter=idle)

    assert content(world) == before


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_undo_reverts_the_most_recent_transaction_only(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    with guard.transaction(flavor, label="older", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    with guard.transaction(flavor, label="newer", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEWER_CONFIG)
        tx.write(TOC, b"## Title: Newer\n")

    guard.undo(store=store, process_iter=idle)

    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    assert (flavor.path / TOC).read_bytes() == ALLOWLISTED_FILES[TOC]


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_restore_puts_back_the_bytes_of_a_snapshot(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    with guard.transaction(flavor, label="change", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
    original = record_for(guard, store, "change").snapshot_id

    # `paths` narrows the restore to what was asked for.
    with guard.transaction(flavor, label="restore-one", store=store, process_iter=idle) as tx:
        tx.restore(original, paths=[CONFIG])
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
    assert not (flavor.path / BINDINGS).exists()
    assert changes(record_for(guard, store, "restore-one").paths) == {
        CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG]))
    }

    # `paths=None` restores every file the snapshot holds.
    with guard.transaction(flavor, label="restore-all", store=store, process_iter=idle) as tx:
        tx.restore(original)
    for rel, data in ALLOWLISTED_FILES.items():
        assert (flavor.path / rel).read_bytes() == data
    assert changes(record_for(guard, store, "restore-all").paths) == {
        BINDINGS: (None, sha(ALLOWLISTED_FILES[BINDINGS]))
    }


def forge_manifest(store: SnapshotStore, base: Manifest, hostile_rel: str) -> str:
    """Publish a manifest that is `base` plus one entry at `hostile_rel`.

    Built with the snapshot module's public functions, so it is a manifest the
    store itself accepts: the id matches the entries and `show` loads it. The
    hostile entry reuses the content object of `WTF/Config.wtf`.
    """
    anchor = entry_for(base, CONFIG)
    hostile = Entry(**{**anchor.model_dump(), "path": manifest_prefix(base) + hostile_rel})
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


def _refuses_forged_restore(
    guard: Any,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    hostile_rel: str,
) -> None:
    with guard.transaction(flavor, label="base", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    base = store.show(record_for(guard, store, "base").snapshot_id)
    forged_id = forge_manifest(store, base, hostile_rel)
    before = content(world)

    with (
        pytest.raises(guard.PathNotAllowedError),
        guard.transaction(flavor, label="forged", store=store, process_iter=idle) as tx,
    ):
        tx.restore(forged_id)

    # Nothing from the forged manifest stays: not the hostile entry, and not
    # the legitimate entries restored before it was reached.
    assert content(world) == before

    # Naming the hostile path outright is refused too.
    with (
        guard.transaction(flavor, label="forged-named", store=store, process_iter=idle) as tx,
        pytest.raises(guard.PathNotAllowedError),
    ):
        tx.restore(forged_id, paths=[hostile_rel])
    assert content(world) == before

    # Positive control: the same store and flavor restore a legitimate path.
    with guard.transaction(flavor, label="legit", store=store, process_iter=idle) as tx:
        tx.restore(base.id, paths=[CONFIG])
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.parametrize(
    "hostile_rel", [pytest.param(rel, id=f"constructed-{name}") for name, rel in HOSTILE_ENTRIES]
)
def test_restore_treats_the_manifest_as_untrusted(
    guard: Any,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    hostile_rel: str,
) -> None:
    _refuses_forged_restore(guard, world, flavor, store, idle, hostile_rel)


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.skipif(sys.platform != "win32", reason="windows-only: what these names mean on NTFS")
@pytest.mark.parametrize(
    "hostile_rel",
    [pytest.param(rel, id=f"constructed-windows-{name}") for name, rel in WINDOWS_HOSTILE_ENTRIES],
)
def test_windows_restore_refuses_posix_names_that_are_not_plain_paths_on_windows(
    guard: Any,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    hostile_rel: str,
) -> None:
    _refuses_forged_restore(guard, world, flavor, store, idle, hostile_rel)


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
@pytest.mark.skipif(sys.platform != "win32", reason="windows-only: what these names mean on NTFS")
@pytest.mark.parametrize(
    "rel",
    [
        pytest.param("WTF/Config.wtf:hidden", id="constructed-windows-alternate-data-stream"),
        pytest.param("WTF/C:evil.txt", id="constructed-windows-drive-relative-inside-allowlist"),
        pytest.param("WTF\\..\\Wow.exe", id="constructed-windows-backslash-traversal"),
    ],
)
def test_windows_write_refuses_names_that_are_not_plain_paths(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable, rel: str
) -> None:
    before = content(world)
    with (
        guard.transaction(flavor, label="win-names", store=store, process_iter=idle) as tx,
        pytest.raises(guard.PathNotAllowedError),
    ):
        tx.write(rel, b"constructed: must never land")
    assert content(world) == before


# ─── dry run ─────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_dry_run_returns_the_plan_and_touches_nothing(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    created = "WTF/Account/ACCT/SavedVariables/New.lua"
    before = strict_state(world)

    with guard.transaction(
        flavor, label="plan", store=store, process_iter=idle, dry_run=True
    ) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(created, NEW_LUA)
        tx.delete(BINDINGS)

    assert strict_state(world) == before, "not a byte, a temp file or an mtime"
    plan = tuple(tx.plan)
    assert changes(plan) == {
        CONFIG: (sha(ALLOWLISTED_FILES[CONFIG]), sha(NEW_CONFIG)),
        created: (None, sha(NEW_LUA)),
        BINDINGS: (sha(ALLOWLISTED_FILES[BINDINGS]), None),
    }
    sizes = {item.path: item.size for item in plan}
    assert sizes[CONFIG] == len(NEW_CONFIG)
    assert sizes[created] == len(NEW_LUA)


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_dry_run_restore_plans_without_touching_anything(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    with guard.transaction(flavor, label="change", store=store, process_iter=idle) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    original = record_for(guard, store, "change").snapshot_id
    before = strict_state(world)

    with guard.transaction(
        flavor, label="plan-restore", store=store, process_iter=idle, dry_run=True
    ) as tx:
        tx.restore(original, paths=[CONFIG])

    assert strict_state(world) == before
    assert changes(tx.plan) == {CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG]))}


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_dry_run_still_refuses_a_forbidden_path(
    guard: Any, world: Path, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    """The CLI prints the plan and then runs it (§6.10); a plan the gate would
    refuse is not a plan."""
    before = strict_state(world)
    with guard.transaction(
        flavor, label="plan-bad", store=store, process_iter=idle, dry_run=True
    ) as tx:
        with pytest.raises(guard.PathNotAllowedError):
            tx.write("../.build.info", b"constructed: must never land")
        with pytest.raises(guard.PathNotAllowedError):
            tx.delete("Wow.exe")
    assert strict_state(world) == before


# ─── no bypass ───────────────────────────────────────────────────────────────

BYPASS_NAME = re.compile(
    r"force|skip|unsafe|bypass|override|ignore|insecure|trust|assume|allow"
    r"|no_?snap|no_?check|no_?verify|no_?journal|no_?rollback"
    r"|check_client|client_check|check_running|running|snapshot$|take_snapshot|allowlist",
    re.IGNORECASE,
)


def _public_callables(guard: Any, tx: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        f"guard.{name}": obj
        for name, obj in vars(guard).items()
        if not name.startswith("_")
        and callable(obj)
        and not inspect.isclass(obj)
        and getattr(obj, "__module__", None) == guard.__name__
    }
    for name in dir(type(tx)):
        if not name.startswith("_") and callable(getattr(type(tx), name)):
            found[f"tx.{name}"] = getattr(type(tx), name)
    return found


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_no_public_parameter_offers_a_way_around_the_gate(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: ProcessTable
) -> None:
    with guard.transaction(flavor, label="signature", store=store, process_iter=idle) as tx:
        callables = _public_callables(guard, tx)

    for expected in ("guard.transaction", "guard.undo", "guard.history"):
        assert expected in callables
    for expected in ("tx.write", "tx.delete", "tx.restore"):
        assert expected in callables

    offenders = [
        f"{where}({name})"
        for where, fn in callables.items()
        for name in inspect.signature(fn).parameters
        if BYPASS_NAME.search(name) and (where, name) != ("tx.restore", "snapshot_id")
    ]
    assert offenders == [], f"parameters that read as a bypass: {offenders}"

    transaction = inspect.signature(guard.transaction).parameters
    assert transaction["store"].default is None
    assert transaction["process_iter"].default is None
    assert transaction["dry_run"].default is False
    assert transaction["dry_run"].kind is inspect.Parameter.KEYWORD_ONLY
    assert transaction["process_iter"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
def test_no_boolean_flag_skips_the_client_check_or_the_snapshot(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: ProcessTable,
) -> None:
    """Name-blind: every boolean parameter of `transaction`, set either way,
    either writes nothing at all or behaves like the gate."""
    flags = [
        name
        for name, parameter in inspect.signature(guard.transaction).parameters.items()
        if isinstance(parameter.default, bool)
    ]
    assert "dry_run" in flags
    running = table(FakeProcess(7300, name="Wow.exe", exe=str(tmp_path / "elsewhere" / "Wow.exe")))
    pristine = content(world)

    for number, (flag, value) in enumerate((f, v) for f in flags for v in (True, False)):
        # Client running: refused, or a mode that writes nothing.
        refused_store = SnapshotStore(tmp_path / f"flag-store-{number}-running")
        try:
            with guard.transaction(
                flavor, label="flag", store=refused_store, process_iter=running, **{flag: value}
            ) as tx:
                tx.write(CONFIG, NEW_CONFIG)
        except guard.ClientRunningError:
            pass
        assert content(world) == pristine, f"{flag}={value} wrote while the client was running"

        # Client idle: if the write landed, the old bytes are in a snapshot.
        idle_store = SnapshotStore(tmp_path / f"flag-store-{number}-idle")
        with guard.transaction(
            flavor, label="flag", store=idle_store, process_iter=idle, **{flag: value}
        ) as tx:
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


@pytest.mark.xfail(strict=True, reason="M10-11 not implemented")
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
def test_no_environment_variable_skips_the_gate(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    store: SnapshotStore,
    idle: ProcessTable,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    monkeypatch.setenv(variable, "1")
    before = strict_state(world)

    running = table(FakeProcess(7400, name="Wow.exe", exe=str(tmp_path / "elsewhere" / "Wow.exe")))
    with (
        pytest.raises(guard.ClientRunningError),
        guard.transaction(flavor, label="env-running", store=store, process_iter=running) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
    assert strict_state(world) == before

    with guard.transaction(flavor, label="env-idle", store=store, process_iter=idle) as tx:
        with pytest.raises(guard.PathNotAllowedError):
            tx.write("../.build.info", b"constructed: must never land")
        assert len(store.list()) == 1, "the snapshot is still taken first"
        tx.write(CONFIG, NEW_CONFIG)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
