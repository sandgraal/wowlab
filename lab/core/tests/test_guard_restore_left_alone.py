"""Graders for "a whole restore lists what it leaves alone" (M16-11T).

Written before M16-11, from docs/LAB_PLAN.md §18.1 item 3 ("A whole restore
lists what it leaves alone"), the M16-11 ticket in docs/BACKLOG.md, §6.10
(`tx.restore(snapshot_id, paths=None)`: a whole restore puts back every file
the snapshot holds; a named path the snapshot's subtrees cover but does not
hold is deleted), ADR-0013, ADR-0021 and invariants L1, L2 and L7. Every
grader carries one marker line that M16-11 deletes; nothing else in this file
is the implementer's to change.

Everything here is constructed. The synthetic install, the fixtures and the
helpers are the M10-11T ones in `test_guard.py` (loaded, not copied): the
install is a tree under `tmp_path`, the store a directory under `tmp_path`,
the user data directory is redirected and the process table is injected.
Link and FIFO graders are POSIX-only and skip where the platform cannot make
one.

The seam these graders hold `guard` to (conductor ruling, review round 1)
--------------------------------------------------------------------------
§18.1 and the ticket say the plan lists the left-alone files; they do not
say how. Putting them inside `tx.plan` breaks every caller that compares or
reads plans (`profile apply`, `snap restore`), so the list is a property of
its own and `tx.plan`, `PlanItem` and every `--json` model stay as they are:

- `tx.left_alone` is a read-only property (no setter) holding a tuple of
  flavor-relative, `/`-separated paths. It is `()` until a whole restore
  fills it and stays `()` for a transaction that only writes, deletes or
  restores by name.
- After `tx.restore(snapshot_id)` (no `paths`) it lists exactly the regular
  files now on disk under one of the snapshot's subtrees
  (`Manifest.subtrees`), outside `Manifest.excluded`, that the snapshot does
  not hold: the files added since the snapshot. Each once. It is the same
  tuple in a dry run and in a transaction that commits.
- Only regular files. A link (to a file or a directory, inside or outside
  the install, or a loop) is neither listed nor followed: nothing behind
  it is listed or read, its `lstat` is unchanged, and the whole restore
  still returns. A FIFO is not listed and is never opened. Walking with
  `lstat` only, as `SnapshotStore.list_tree` (snapshot.py, "Every path
  `create` would walk under `subtrees`") already does, is one way to get
  all of this; it refuses links and reports FIFOs as `other`.
- A left-alone file is never written or deleted: bytes, inode, mtime and
  mode are unchanged after commit, `tx.plan` and the journal record do not
  name it, and a later `undo()` does not touch it. This holds for an added
  file named like an executable and for an added Edit `no` file (a
  `.lua.bak`): the restore succeeds and lists them.
- A later `tx.write` or `tx.delete` of a left-alone path in the same
  transaction is an ordinary `PlanItem` whose `before` is the file's real
  SHA-256 (the first touch reads the disk as for any path).
- A file outside the snapshot's subtrees is never listed, and neither is a
  file the snapshot holds, changed or not.

Not pinned: the order inside the tuple beyond "the same in a dry run and a
commit", whether a path stays in `tx.left_alone` after the same transaction
writes or deletes it, and empty directories (ungraded by ruling).

Notes for M16-11, left to its own CLI tests and not graded here
---------------------------------------------------------------
- `snap restore` with added files, together with a skipped Edit `no` file
  and a set-aside screenshot: the dry-run plan equals the commit plan, and
  the commit succeeds without weakening the plan comparison in `cli.py`
  (`if tx.plan != tuple(plan) or moved:` in the restore command).
- The CLI turns some whole restores into restores by name (screenshots set
  aside, Edit `no` files skipped, §6.10 "restored by name"). For those
  `tx.left_alone` is `()` by this seam, so the CLI computes the left-alone
  list itself, by the same rule, for the "left alone" heading.
"""

from __future__ import annotations

import builtins
import importlib.util
import inspect
import io
import os
import sys
import threading
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore


def _load_m10_11t_graders() -> types.ModuleType:
    """`test_guard.py` as a module of its own, for its synthetic install,
    fixtures and helpers (as `test_guard_owner_decisions.py` loads it)."""
    path = Path(__file__).resolve().parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_m16_11t_base_graders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = _load_m10_11t_graders()

# The M10-11T fixtures, registered here under their own names: the user data
# directory and the process table are redirected for every test in this file.
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
store = G.store
idle = G.idle

Flavor = G.Flavor
CONFIG = G.CONFIG
BINDINGS = G.BINDINGS
SAVED = G.SAVED
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
FLAVOR_ONLY_FILES = G.FLAVOR_ONLY_FILES
NEW_CONFIG = G.NEW_CONFIG
sha = G.sha
record_for = G.record_for
changes = G.changes

# Files added after the snapshot, one under each default subtree (§6.9:
# `WTF/`, `Interface/AddOns/`, `Fonts/`, loose `Interface/` files), some in
# directories that did not exist when the snapshot was taken.
ADDED: dict[str, bytes] = {
    "WTF/Account/ACCT/SavedVariables/Added.lua": b'AddedDB = {\n\t["x"] = 1,\n}\n',
    "WTF/Account/OTHER/bindings-cache.wtf": b"bind S MOVEBACKWARD\n",
    "Interface/AddOns/NewAddon/NewAddon.toc": b"## Interface: 0\n## Title: New\n",
    "Interface/AddOns/Thing/Added.lua": b"-- added since\n",
    "Fonts/ADDED.TTF": b"\x00\x01\x00\x00added-font",
    "Interface/Icons/added.blp": b"BLP2\x05\x05",
}

# Files added after the snapshot outside every subtree it covers.
ADDED_OUTSIDE: dict[str, bytes] = {
    "Logs/Added.log": b"constructed log\n",
    "Screenshots/WoWScrnShot_020226_000000.jpg": b"\xff\xd8 constructed added",
    "Cache/ADB/enUS/Added.bin": b"XFTH constructed added",
}

# One regular added file, the positive control beside links and FIFOs.
CONTROL = "Interface/AddOns/Thing/Added.lua"

# Where links and FIFOs are planted: an addon folder and an account folder.
LINK_BASES = ("Interface/AddOns", "WTF/Account/ACCT")

OUTSIDE_FILE = b"constructed: outside the install, behind a link\n"

BOUND = 30.0
"""Seconds a restore may take before it counts as hung (a loop or a FIFO)."""


def _put(base: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _state(base: Path, rels: list[str]) -> dict[str, tuple[bytes, int, int, int]]:
    out: dict[str, tuple[bytes, int, int, int]] = {}
    for rel in rels:
        st = (base / rel).lstat()
        out[rel] = ((base / rel).read_bytes(), st.st_ino, st.st_mtime_ns, st.st_mode)
    return out


def _link_state(link: Path) -> tuple[int, int, int, str]:
    st = link.lstat()
    return (st.st_ino, st.st_mtime_ns, st.st_mode, str(link.readlink()))


def _snapshot_then_change(guard: Any, flavor: Flavor, store: SnapshotStore) -> str:
    """A snapshot of the synthetic install (the pre-write snapshot of a
    transaction), then a change it can restore: `CONFIG` rewritten and
    `BINDINGS` deleted. Returns the snapshot id."""
    with guard.transaction(flavor, label="change", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
    return str(record_for(guard, store, "change").snapshot_id)


EXPECTED_CHANGES = {
    CONFIG: (sha(NEW_CONFIG), sha(ALLOWLISTED_FILES[CONFIG])),
    BINDINGS: (None, sha(ALLOWLISTED_FILES[BINDINGS])),
}


def _restore(
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    snapshot_id: str,
    *,
    dry_run: bool,
    paths: list[str] | None = None,
    label: str = "restore",
    around: Callable[[], Any] | None = None,
) -> tuple[tuple[str, ...], tuple[Any, ...]]:
    """One transaction that restores `snapshot_id` and returns
    (`tx.left_alone`, `tx.plan`) as they stand after the restore. `around`,
    when given, is a context manager factory held for the restore call only."""
    with guard.transaction(flavor, label=label, store=store.path, dry_run=dry_run) as tx:
        if around is None:
            tx.restore(snapshot_id, paths)
        else:
            with around():
                tx.restore(snapshot_id, paths)
        left = tx.left_alone
        plan = tx.plan
    assert isinstance(left, tuple), f"tx.left_alone is a tuple, not {type(left).__name__}"
    assert all(isinstance(p, str) for p in left), left
    assert len(left) == len(set(left)), f"a path listed twice: {left!r}"
    return left, plan


def _bounded(fn: Callable[[], Any], *, unblock: Callable[[], None] | None = None) -> Any:
    """Run `fn` in a daemon thread; fail if it has not returned within
    `BOUND` seconds (after calling `unblock`, to free a reader stuck on a
    FIFO). Re-raises whatever `fn` raised."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True, name="m16-11t-restore")
    worker.start()
    worker.join(BOUND)
    if worker.is_alive():
        if unblock is not None:
            unblock()
            worker.join(5.0)
        pytest.fail(f"the whole restore did not return within {BOUND} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _under(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r + os.sep) for r in roots)


@contextmanager
def _no_reads_behind(
    monkeypatch: pytest.MonkeyPatch, forbidden: list[Path], hits: list[str]
) -> Iterator[None]:
    """For as long as it is held: any open or directory listing of a path
    that resolves under `forbidden` (the targets of the planted links) is
    recorded in `hits` and refused, so a walk that reads through a link
    fails visibly instead of passing by luck."""
    roots = [os.path.realpath(p) for p in forbidden]

    def check(target: Any) -> None:
        if isinstance(target, int):
            return
        try:
            resolved = os.path.realpath(os.fsdecode(target))
        except (TypeError, ValueError, OSError):
            return
        if _under(resolved, roots):
            hits.append(os.fsdecode(target))
            raise PermissionError(f"constructed trap: read through a link: {target!r}")

    def wrap(original: Callable[..., Any]) -> Callable[..., Any]:
        def trapped(target: Any = ".", *args: Any, **kwargs: Any) -> Any:
            check(target)
            return original(target, *args, **kwargs)

        return trapped

    with monkeypatch.context() as m:
        m.setattr(os, "open", wrap(os.open))
        m.setattr(io, "open", wrap(io.open))
        m.setattr(builtins, "open", wrap(builtins.open))
        m.setattr(os, "scandir", wrap(os.scandir))
        m.setattr(os, "listdir", wrap(os.listdir))
        yield


# ─── the seam ────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.parametrize(
    "dry_run", [True, False], ids=["constructed-dry-run", "constructed-commit"]
)
def test_constructed_left_alone_is_a_read_only_empty_tuple_without_a_whole_restore(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    with guard.transaction(flavor, label="plain", store=store.path, dry_run=dry_run) as tx:
        member = inspect.getattr_static(type(tx), "left_alone", None)
        assert isinstance(member, property), "tx.left_alone is a property"
        assert member.fset is None, "tx.left_alone has no setter"
        assert tx.left_alone == ()
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
        assert tx.left_alone == ()
        assert set(changes(tx.plan)) == {CONFIG, BINDINGS}


# ─── a whole restore lists the added files ──────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.parametrize(
    "dry_run", [True, False], ids=["constructed-dry-run", "constructed-commit"]
)
def test_constructed_whole_restore_lists_every_added_file_and_the_plan_none(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)

    left, plan = _restore(guard, flavor, store, snapshot_id, dry_run=dry_run)

    assert set(left) == set(ADDED)
    # The plan is what the restore changes, as before M16-11, and nothing else.
    assert changes(plan) == EXPECTED_CHANGES
    assert not set(left) & {item.path for item in plan}


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_dry_run_and_commit_list_the_same_tuple(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)

    planned, _ = _restore(guard, flavor, store, snapshot_id, dry_run=True, label="plan")
    committed, _ = _restore(guard, flavor, store, snapshot_id, dry_run=False, label="commit")

    assert set(planned) == set(ADDED)
    assert planned == committed


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.parametrize("rel", sorted(ADDED), ids=lambda rel: f"constructed-{rel}")
def test_constructed_each_subtree_lists_a_single_added_file(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, rel: str
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, {rel: ADDED[rel]})

    left, _ = _restore(guard, flavor, store, snapshot_id, dry_run=True)

    assert left == (rel,)


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_whole_restore_with_nothing_added_lists_nothing(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)

    left, plan = _restore(guard, flavor, store, snapshot_id, dry_run=False)

    assert left == ()
    assert changes(plan) == EXPECTED_CHANGES


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_files_outside_the_subtrees_and_files_the_snapshot_holds_are_not_listed(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """Positive control beside the negative: the added file under a subtree
    is listed, the ones outside every subtree and the ones the snapshot holds
    (changed, deleted or untouched since) are not."""
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    snapshot = store.show(snapshot_id)
    prefix = flavor.folder + "/"
    for rel in ADDED_OUTSIDE:
        assert not any((prefix + rel).startswith(s + "/") for s in snapshot.subtrees), rel
    _put(flavor.path, {CONTROL: ADDED[CONTROL]})
    _put(flavor.path, ADDED_OUTSIDE)

    left, _ = _restore(guard, flavor, store, snapshot_id, dry_run=True)

    assert left == (CONTROL,)
    for rel in (*ADDED_OUTSIDE, *FLAVOR_ONLY_FILES, *ALLOWLISTED_FILES):
        assert rel not in left, rel


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_files_under_an_excluded_path_are_not_listed(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """A snapshot taken with `exclude`: what sits under an excluded path,
    added since or already there, is not the restore's and is not listed."""
    folder = flavor.folder
    excluded = "WTF/Account/ACCT/SavedVariables"
    manifest = store.create(
        flavor.path.parent,
        [f"{folder}/WTF", f"{folder}/Interface", f"{folder}/Fonts"],
        exclude=[f"{folder}/{excluded}"],
        label="user, with an exclude",
        flavor_folder=folder,
        flavor_version=flavor.version,
        client_running=False,
    )
    assert manifest.excluded, "the constructed snapshot records its exclude"
    assert not any(e.path.endswith("/" + SAVED) for e in manifest.entries)
    under_excluded = f"{excluded}/Added.lua"
    _put(flavor.path, {under_excluded: ADDED[under_excluded], CONTROL: ADDED[CONTROL]})

    left, _ = _restore(guard, flavor, store, manifest.id, dry_run=True)

    assert left == (CONTROL,)
    assert under_excluded not in left
    assert SAVED not in left


# ─── left alone means never written or deleted ──────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_left_alone_files_are_never_written_deleted_or_journaled(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)
    before = _state(flavor.path, sorted(ADDED))

    left, plan = _restore(guard, flavor, store, snapshot_id, dry_run=False)
    assert set(left) == set(ADDED)
    assert changes(plan) == EXPECTED_CHANGES

    # The restore did what it is for ...
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
    assert (flavor.path / BINDINGS).read_bytes() == ALLOWLISTED_FILES[BINDINGS]
    # ... and the added files are exactly as they were: same bytes, same
    # inode (no replace), same mtime (no rewrite), same mode.
    assert _state(flavor.path, sorted(ADDED)) == before
    # The journal names only what was touched.
    assert changes(record_for(guard, store, "restore").paths) == EXPECTED_CHANGES

    # Undoing the restore does not touch them either.
    guard.undo(store=store.path)
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG
    assert not (flavor.path / BINDINGS).exists()
    assert _state(flavor.path, sorted(ADDED)) == before


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_dry_run_whole_restore_leaves_the_install_untouched(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)
    before = G.strict_state(flavor.path)

    left, _ = _restore(guard, flavor, store, snapshot_id, dry_run=True)

    assert set(left) == set(ADDED)
    assert G.strict_state(flavor.path) == before


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_added_executable_and_edit_no_files_are_listed_and_untouched(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """An added file named like an executable (the gate refuses to write
    one, L7) and an added Edit `no` file (`.lua.bak`, docs/LAB_FILE_MAP.md):
    the whole restore succeeds, lists both, and neither writes nor journals
    them."""
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    added = {
        "Interface/AddOns/Thing/build.sh": b"#!/bin/sh\n# constructed\n",
        f"{SAVED}.bak": b'AddonDB = {\n\t["a"] = 0,\n}\n',
    }
    _put(flavor.path, added)
    before = _state(flavor.path, sorted(added))

    left, plan = _restore(guard, flavor, store, snapshot_id, dry_run=False)

    assert set(left) == set(added)
    assert changes(plan) == EXPECTED_CHANGES
    assert _state(flavor.path, sorted(added)) == before
    assert changes(record_for(guard, store, "restore").paths) == EXPECTED_CHANGES


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.parametrize("op", ["write", "delete"], ids=lambda op: f"constructed-{op}")
@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "commit"])
def test_constructed_later_touch_of_a_left_alone_path_records_its_real_hash(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, op: str, dry_run: bool
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, {CONTROL: ADDED[CONTROL]})
    replacement = b"-- replaced in the same transaction\n"

    with guard.transaction(flavor, label="restore", store=store.path, dry_run=dry_run) as tx:
        tx.restore(snapshot_id)
        assert tx.left_alone == (CONTROL,)
        if op == "write":
            tx.write(CONTROL, replacement)
        else:
            tx.delete(CONTROL)
        plan = changes(tx.plan)

    after = sha(replacement) if op == "write" else None
    assert plan == {**EXPECTED_CHANGES, CONTROL: (sha(ADDED[CONTROL]), after)}
    if not dry_run:
        assert changes(record_for(guard, store, "restore").paths)[CONTROL] == (
            sha(ADDED[CONTROL]),
            after,
        )


# ─── a restore by name lists none ───────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.parametrize(
    "dry_run", [True, False], ids=["constructed-dry-run", "constructed-commit"]
)
def test_constructed_restore_by_paths_lists_nothing(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)
    before = _state(flavor.path, sorted(ADDED))

    left, plan = _restore(
        guard, flavor, store, snapshot_id, dry_run=dry_run, paths=[CONFIG, BINDINGS]
    )

    assert left == ()
    assert changes(plan) == EXPECTED_CHANGES
    assert _state(flavor.path, sorted(ADDED)) == before


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
def test_constructed_restore_naming_an_added_file_deletes_it_and_lists_nothing(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None
) -> None:
    """§6.10: a named path the subtrees cover but the snapshot does not hold
    is deleted. Named, it is a change in the plan; nothing is left alone."""
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    _put(flavor.path, ADDED)
    others = sorted(set(ADDED) - {CONTROL})
    before = _state(flavor.path, others)

    left, plan = _restore(guard, flavor, store, snapshot_id, dry_run=False, paths=[CONTROL])

    assert left == ()
    assert changes(plan) == {CONTROL: (sha(ADDED[CONTROL]), None)}
    assert not (flavor.path / CONTROL).exists()
    assert _state(flavor.path, others) == before


# ─── links and FIFOs: listed never, followed never, opened never ────────────


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "commit"])
@pytest.mark.parametrize("base", LINK_BASES, ids=lambda b: b.split("/")[0])
@pytest.mark.parametrize(
    "kind",
    ["dir-out", "file-out", "dir-loop"],
    ids=lambda k: f"constructed-{k}",
)
def test_constructed_added_link_is_neither_listed_nor_followed(
    guard: Any,
    flavor: Flavor,
    store: SnapshotStore,
    idle: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    base: str,
    dry_run: bool,
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    outside = tmp_path / "linked-outside"
    outside.mkdir()
    (outside / "inside.lua").write_bytes(OUTSIDE_FILE)
    (outside / "Nested").mkdir()
    (outside / "Nested" / "deeper.lua").write_bytes(OUTSIDE_FILE)
    link_dir = flavor.path / base
    link_dir.mkdir(parents=True, exist_ok=True)
    if kind == "dir-out":
        link_rel = f"{base}/LinkedDir"
        G.symlink_or_skip(flavor.path / link_rel, outside, is_dir=True)
    elif kind == "file-out":
        link_rel = f"{base}/Linked.lua"
        G.symlink_or_skip(flavor.path / link_rel, outside / "inside.lua", is_dir=False)
    else:
        link_rel = f"{base}/Loop"
        G.symlink_or_skip(flavor.path / link_rel, ".", is_dir=True)
    _put(flavor.path, {CONTROL: ADDED[CONTROL]})
    link = flavor.path / link_rel
    link_before = _link_state(link)
    outside_before = G.strict_state(outside)
    hits: list[str] = []

    def trap() -> Any:
        return _no_reads_behind(monkeypatch, [outside], hits)

    left, _ = _bounded(
        lambda: _restore(guard, flavor, store, snapshot_id, dry_run=dry_run, around=trap)
    )

    assert hits == [], f"the restore read through the link: {hits!r}"
    assert left == (CONTROL,), "regular files only: the link and what is behind it are absent"
    assert not any(p == link_rel or p.startswith(link_rel + "/") for p in left)
    assert _link_state(link) == link_before
    assert G.strict_state(outside) == outside_before


@pytest.mark.xfail(strict=True, reason="M16-11 not implemented")
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX (os.mkfifo)")
@pytest.mark.parametrize(
    "dry_run", [True, False], ids=["constructed-dry-run", "constructed-commit"]
)
def test_constructed_added_fifo_is_not_listed_and_the_restore_returns(
    guard: Any, flavor: Flavor, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    snapshot_id = _snapshot_then_change(guard, flavor, store)
    fifo = flavor.path / "WTF/Account/ACCT/SavedVariables/Pipe.lua"
    fifo.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(fifo)
    _put(flavor.path, {CONTROL: ADDED[CONTROL]})
    fifo_before = fifo.lstat()

    def unblock() -> None:
        # Free a reader stuck in open() on the FIFO, so the thread can end.
        fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        os.close(fd)

    left, _ = _bounded(
        lambda: _restore(guard, flavor, store, snapshot_id, dry_run=dry_run), unblock=unblock
    )

    assert left == (CONTROL,)
    after = fifo.lstat()
    assert (after.st_ino, after.st_mode, after.st_mtime_ns) == (
        fifo_before.st_ino,
        fifo_before.st_mode,
        fifo_before.st_mtime_ns,
    )
