"""Graders for LAB_PLAN §6.10, amendment of 2026-11-11 (a): a change killed
between the temp cleanup and its snapshot changed nothing (M16-93T).

Written before M16-69, from the M16-93T and M16-69 tickets in
docs/BACKLOG.md (the owner's answer of 2026-11-11 to M16-69's question),
docs/LAB_PLAN.md §6.10 (item 2 of the 2026-09-22 amendment: each removal is
journaled and fsynced before its unlink; the 2026-11-10 amendment (c):
`doctor` reports a most recent `open` record with exit 1 and the exact
`wowlab undo --expect <id>` line; the 2026-11-11 amendment (a), (a1) for
the undo route, from M16-100T, and (a2), from M16-108T) and ADR-0021. Every
grader carries one marker line that M16-69 deletes; nothing else in this
file is the implementer's to change.

What the amendment fixes, as graded here:

- Leftover temps are removed before the pre-write snapshot, so a kill after
  the cleanup and before the snapshot leaves a journal record K that is still
  `open`, lists the removals in `temps_removed`, and names no snapshot the
  store holds. The install then differs from before the killed change only
  by the removed temps: the change touched no player file.
- While K is `open` it is the undo target: `wowlab undo` offers K's empty
  plan and `--expect <D id>` is a mismatch (2026-11-11 (a2), "what is
  unchanged"; M16-108T).
- `undo` of K (the library's `guard.undo` and the CLI's
  `wowlab undo --expect <id> --yes`, exit 0) closes it without restoring
  anything: every file's bytes are as they were before the undo, no later
  record names a path or a created folder, the most recent record is no
  longer `open`. Closing K does not settle the interrupted change D killed
  mid-write before it, whether K was an `undo` of D (amendment (a1) and its
  undo-target ruling; graders changed by M16-100T) or a `transaction`
  (amendment (a2), "Skip all, show D"; graders changed by M16-108T):
  `doctor` exits 1, reports D with D's own undo line and names K as
  interrupted before it changed anything; `wowlab undo` passes over K's
  closed record and offers D; D's own line then settles D and `doctor` is
  clean.
- (a1) as made precise on 2026-11-11 (M16-100T rounds 1 and 2), widened by
  (a2): every record `undo()` writes carries `undoes`, and a record appended
  to close K names K; `history()` refuses an `undoes` that is not the id of
  an earlier record of the same flavor; a closed record that never took a
  pre-write snapshot (and the record that closes it) is passed over, never a
  completed undo nor one killed after its snapshot; `--expect` must name the
  target, so a passed-over id, or D behind a newer target, is refused.
- (a2), the failed-snapshot route (M16-108T): a change T1 completes and
  strands a temp; the next change's pre-write snapshot raises `SnapshotError`
  after its cleanup removed that temp (injected in this process at the
  `SnapshotStore` seam). The change fails with that error, writes no player
  file, and the gate closes its record itself. That record is passed over:
  T1 stays the target (`wowlab undo --json` offers T1,
  `guard.undo(expected_id=T1)` succeeds, the failed record's id is a
  mismatch), and `doctor` reports nothing for it. The positive control, a
  failed snapshot with nothing to clean up, holds on `main` today and
  carries no marker.
- (a2), "what never took a snapshot means" (2026-11-11, M16-108T round 2):
  a record whose manifest is missing is not passed over (the snapshot
  error stands); a record whose journal file is forged to list a path or
  a folder is not passed over (or is refused as damaged); a record naming
  a passed-over K in `undoes` that lists a path or took a snapshot is not
  passed over; two passed-over records in a row are both passed over; a
  transaction rolled back after its snapshot is never passed over. Every
  refusal and every plan-only `wowlab undo --json` leaves the store's
  snapshots as they were. The graders here that hold on `main` (the
  missing manifest, the rolled-back transaction) carry no marker.
- No removed temp comes back: not on disk after the crash or the undo, and
  no manifest in the store holds one, by path or by bytes.
- `doctor` on the killed record still reports it (exit 1, the exact undo
  line), and says something other than what it says for a change killed
  mid-write. The wording is not in the spec, so it is not pinned: the
  grader compares the two problems with the record ids masked and requires
  them to differ. `doctor` itself writes nothing.

Which "close" means (a later record over it, as `test_doctor_interrupted.py`
pins for an ordinary interrupted change, or a state change of the record
itself) is not pinned: only that the most recent record is no longer `open`
and nothing after the killed record names a path.

Constructed (L8, boundary): the crash point. The install is the captured
tree, copied into `tmp_path` as `test_cli.py` builds it, with the user data
directory redirected and a fake process table (no client runs). A second
Python process (`CRASH_SCRIPT`, same redirection, empty process table)
first leaves two leftover temps the way a crash does: a real transaction
whose first rename is refused and whose temp unlink is ignored, and which
dies (`os._exit`) inside its second rename. A second child then enters again,
through `transaction()` or `undo()`, and dies (`os._exit`) at the first
sign of the pre-write snapshot landing. The file-level seam is any
write-open, rename, replace or link into the store's `objects/` or
`manifests/`. So that it sees them on every platform, the child sets
`snapshot._WRITE_BY_DIR_FD = False`: the store, which on POSIX otherwise
decides at import to write through descriptors, writes by path as it does on
Windows. The seam also resolves a `dir_fd` / `dst_dir_fd` it is handed
(through `/proc/self/fd` or `F_GETPATH`). A call to `SnapshotStore.create`,
`add_snapshot` or `put_object` is a second net. That is the moment between
the cleanup and the snapshot; no internal order of `guard.py` is read to
find it. The review probe
`review/test_m16_93t_crash_seam_misses_dir_fd_store_writes.py` checks that
the file-level seam fires without the method patches.

Before the second kill a player file (`WTF/Config.wtf`) is changed outside
guard, as the client does at logout, so the install no longer matches any
stored snapshot: an undo that restored a stale snapshot would show in the
bytes, not only in the journal. Nothing reads or writes a real install.

Constructed (L8, boundary), the failed-snapshot route: everything runs in
this process. T1's stranded temp comes from a refused `os.replace` and an
ignored unlink of the temp, as in the child; the snapshot failure from
`SnapshotStore.create`, `add_snapshot` and `put_object` raising
`SnapshotError`, patched only for the failing change's enter.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_cli import (
    SYNDICATOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401  (autouse: the user data directory is redirected)
)

from wowlab_core import guard, snapshot
from wowlab_core.snapshot import SnapshotError, SnapshotStore

CONFIG = "WTF/Config.wtf"
STRANDED_BYTES = b"constructed: Syndicator bytes whose rename was refused\n"
DYING_BYTES = b"constructed: Config.wtf bytes the dead change never renamed into place\n"
CRASHED_BYTES = b"constructed: what the change killed at its snapshot meant to write\n"
PLAYER_BYTES = b"constructed: Config.wtf as the client wrote it at logout, after the dead change\n"

TEMP_NAME = re.compile(r"\.wowlab-[0-9a-f]{32}\.tmp")
DIED_IN_RENAME = 17
DIED_AT_SNAPSHOT = 19
CHILD_TIMEOUT = 120.0

ROUTES = [pytest.param(r, id=f"constructed-killed-in-{r}") for r in ("transaction", "undo")]

CRASH_SCRIPT = r'''
"""A wowlab process killed at a constructed point (M16-93T graders)."""
import builtins
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import platformdirs
import psutil

args = json.loads(sys.argv[1])
user_data = Path(args["user_data"])
platformdirs.user_data_path = lambda *a, **k: user_data
# Constructed: no game client, and the real process table is never listed.
psutil.process_iter = lambda *a, **k: iter(())

from wowlab_core import guard, snapshot  # noqa: E402

flavor = SimpleNamespace(path=Path(args["flavor"]), version=None)
store = Path(args["store"])
mode = args["mode"]

if mode == "die-in-rename":
    strand = set(args["strand"])
    die_on = args["die_on"]
    stranded = set()
    real = {name: getattr(os, name) for name in ("replace", "rename", "unlink", "remove")}

    def moving(name):
        def call(src, dst, *a, **k):
            target = os.path.basename(os.fsdecode(dst))
            if target == die_on:
                os._exit(17)  # dies inside the rename: nothing after it runs
            if target in strand:
                stranded.add(os.path.basename(os.fsdecode(src)))
                raise PermissionError(13, "constructed: the rename is refused")
            return real[name](src, dst, *a, **k)

        return call

    def removing(name):
        def call(path, *a, **k):
            if os.path.basename(os.fsdecode(path)) in stranded:
                return None  # the stranded temp file stays behind
            return real[name](path, *a, **k)

        return call

    os.replace = moving("replace")
    os.rename = moving("rename")
    os.unlink = removing("unlink")
    os.remove = removing("remove")
    if args.get("route") == "undo":
        # An undo killed inside the rename of a file it restores: after its
        # pre-write snapshot (M16-100T round 2).
        guard.undo(store=store)
        sys.stdout.write("survived\n")
        sys.exit(3)
    with guard.transaction(flavor, label="child-died", store=store) as tx:
        for rel, data in args["writes"]:
            try:
                tx.write(rel, bytes.fromhex(data))
            except guard.GuardError:
                pass
    sys.stdout.write("survived\n")
    sys.exit(3)

# mode "die-at-snapshot": killed the moment the pre-write snapshot starts to land.
landing = [os.path.realpath(store / "objects"), os.path.realpath(store / "manifests")]


def fd_path(fd):
    """The directory an open descriptor names, or None where it cannot be told."""
    try:
        return os.readlink(f"/proc/self/fd/{fd}")  # Linux
    except OSError:
        pass
    try:
        import fcntl

        got = fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH"), b"\0" * 1024)  # macOS
        return os.fsdecode(got.split(b"\0", 1)[0])
    except (ImportError, AttributeError, OSError, TypeError, ValueError):
        return None


def lands(path, dir_fd=None):
    if isinstance(path, int):
        return False
    try:
        name = os.fsdecode(path)
        if dir_fd is not None and not os.path.isabs(name):
            base = fd_path(dir_fd)
            if base is None:
                return False
            name = os.path.join(base, name)
        where = os.path.realpath(name)
    except (TypeError, ValueError):
        return False
    return any(where == d or where.startswith(d + os.sep) for d in landing)


def die(*a, **k):
    os._exit(19)


for name in ("create", "add_snapshot", "put_object"):
    setattr(snapshot.SnapshotStore, name, die)

# The store decides at import whether it writes through descriptors (POSIX)
# or by path (Windows). Path writes are what the file-level seam sees on every
# platform, so the store is made to use them here; the seam also resolves a
# `dir_fd` / `dst_dir_fd` it is handed, as a second net.
snapshot._WRITE_BY_DIR_FD = False

real_os_open = os.open
real_open = builtins.open
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def os_open(path, flags, *a, **k):
    if flags & WRITE_FLAGS and lands(path, k.get("dir_fd")):
        die()
    return real_os_open(path, flags, *a, **k)


def py_open(file, mode="r", *a, **k):
    if any(c in mode for c in "wax+") and lands(file):
        die()
    return real_open(file, mode, *a, **k)


def moving_in(real):
    def call(src, dst, *a, **k):
        if lands(dst, k.get("dst_dir_fd")):
            die()
        return real(src, dst, *a, **k)

    return call


os.open = os_open
builtins.open = py_open
io.open = py_open
os.replace = moving_in(os.replace)
os.rename = moving_in(os.rename)
os.link = moving_in(os.link)

if args["route"] == "transaction":
    with guard.transaction(flavor, label="killed-at-snapshot", store=store) as tx:
        tx.write(args["write"][0], bytes.fromhex(args["write"][1]))
else:
    guard.undo(store=store)
sys.stdout.write("survived\n")
sys.exit(3)
'''


# ─── helpers ─────────────────────────────────────────────────────────────────


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree(root: Path) -> dict[str, bytes]:
    """Every file under the install root with its bytes."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def listing(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


def manifest_ids(store: Path) -> set[str]:
    return {m.id for m in SnapshotStore(store).list()}


def child(tmp_path: Path, user_data: Path, **args: Any) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "m16_93t_crash_child.py"
    if not script.exists():
        script.write_text(CRASH_SCRIPT, encoding="utf-8")
    args["user_data"] = str(user_data)
    return subprocess.run(
        [sys.executable, str(script), json.dumps(args)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=CHILD_TIMEOUT,
        check=False,
    )


def locks_released(flavor: Path, store: Path) -> None:
    """On Windows the system may take a moment to drop a dead process's locks
    (LockFileEx documentation); POSIX drops them with the process. A dry run
    takes the same locks and writes nothing in the install."""
    if sys.platform != "win32":
        return
    ns = SimpleNamespace(path=flavor, version=None)
    deadline = time.monotonic() + 30.0
    while True:
        try:
            with guard.transaction(ns, label="probe", store=store, dry_run=True):
                return
        except guard.GuardBusyError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


@dataclass(frozen=True)
class Crashed:
    root: Path
    flavor: Path
    store: Path
    temps: dict[str, bytes]
    """Flavor-relative path of each leftover temp, and its bytes."""
    dead_id: str
    """The change killed mid-write that left the temps."""
    record: Any
    """The record of the change killed between the cleanup and the snapshot."""
    before: dict[str, bytes]
    """The install right before the killed change, temps included."""
    manifests: set[str]
    """The snapshots the store held right before the killed change."""


def leave_two_temps(tmp_path: Path, user_data: Path, flavor: Path, store: Path) -> dict[str, bytes]:
    """A real transaction strands one temp (rename refused, unlink ignored)
    and dies inside the rename of the second: two leftover temps."""
    sv_dir = flavor / Path(SYNDICATOR).parent
    wtf_dir = flavor / Path(CONFIG).parent
    sv_before, wtf_before = listing(sv_dir), listing(wtf_dir)
    config_bytes = (flavor / CONFIG).read_bytes()
    syndicator_bytes = (flavor / SYNDICATOR).read_bytes()
    died = child(
        tmp_path,
        user_data,
        mode="die-in-rename",
        writes=[[SYNDICATOR, STRANDED_BYTES.hex()], [CONFIG, DYING_BYTES.hex()]],
        strand=[Path(SYNDICATOR).name],
        die_on=Path(CONFIG).name,
        flavor=str(flavor),
        store=str(store),
    )
    assert died.returncode == DIED_IN_RENAME, died.stderr[-4000:]
    locks_released(flavor, store)
    (a,) = listing(sv_dir) - sv_before
    (b,) = listing(wtf_dir) - wtf_before
    for name in (a, b):
        assert TEMP_NAME.fullmatch(name), f"guard's temp name {name!r} is §6.10's pattern"
    assert (flavor / CONFIG).read_bytes() == config_bytes, "the death came before the rename"
    assert (flavor / SYNDICATOR).read_bytes() == syndicator_bytes, "the stranded rename was refused"
    rels = (f"{Path(SYNDICATOR).parent.as_posix()}/{a}", f"{Path(CONFIG).parent.as_posix()}/{b}")
    temps = {rel: (flavor / rel).read_bytes() for rel in rels}
    assert temps[rels[0]] == STRANDED_BYTES and temps[rels[1]] == DYING_BYTES
    return temps


def kill_between_cleanup_and_snapshot(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, route: str
) -> Crashed:
    """Leave two temps, then kill the next enter (by `route`) at its snapshot,
    and check what the journal and the install hold afterwards."""
    store = snapshot.default_store_path()
    temps = leave_two_temps(tmp_path, user_data, flavor, store)
    dead = guard.history(store=store)[-1]
    assert dead.label == "child-died" and dead.state == "open", "positive control"
    manifests = manifest_ids(store)
    assert manifests, "positive control: the dead change's own pre-write snapshot is stored"
    # Constructed: the player's file changes outside guard, as the client
    # writes at logout, so no stored snapshot matches the install any more and
    # a stale restore would show in the bytes.
    (flavor / CONFIG).write_bytes(PLAYER_BYTES)
    for manifest in SnapshotStore(store).list():
        held = [e for e in manifest.entries if e.path.endswith("/" + CONFIG)]
        assert held and all(e.sha256 != sha(PLAYER_BYTES) for e in held), (
            f"positive control: snapshot {manifest.id} holds an older {CONFIG}"
        )
    before = tree(root)

    killed = child(
        tmp_path,
        user_data,
        mode="die-at-snapshot",
        route=route,
        write=[CONFIG, CRASHED_BYTES.hex()],
        flavor=str(flavor),
        store=str(store),
    )
    assert killed.returncode == DIED_AT_SNAPSHOT, (
        "the second child dies at its pre-write snapshot",
        killed.stdout,
        killed.stderr[-4000:],
    )
    locks_released(flavor, store)

    # Cleaning first: the record exists before its snapshot does.
    record = guard.history(store=store)[-1]
    assert record.id != dead.id, "the killed enter journaled a record of its own before it died"
    assert record.state == "open", "killed: the record was never closed"
    assert set(record.temps_removed) == set(temps), "the cleanup ran, and is journaled"
    assert record.temps_left == ()
    assert record.paths == () and record.created_dirs == (), "no player file was touched"
    for rel in temps:
        assert not (flavor / rel).exists(), f"the cleanup removed {rel} before the kill"
    assert manifest_ids(store) == manifests, "the kill came before any snapshot landed"
    assert record.snapshot_id not in manifests, "the record names no snapshot the store holds"

    expected = {k: v for k, v in before.items() if k not in {f"{flavor.name}/{r}" for r in temps}}
    assert len(expected) == len(before) - len(temps), "positive control: the temps were in it"
    assert tree(root) == expected, "the killed change touched no player file"
    return Crashed(root, flavor, store, temps, dead.id, record, before, manifests)


def assert_no_manifest_holds_a_removed_temp(c: Crashed) -> None:
    removed = {sha(data) for data in c.temps.values()}
    for manifest in SnapshotStore(c.store).list():
        for entry in manifest.entries:
            assert not any(
                entry.path == rel or entry.path.endswith("/" + rel) for rel in c.temps
            ), f"snapshot {manifest.id} holds the removed temp {entry.path}"
            assert entry.sha256 not in removed, (
                f"snapshot {manifest.id} holds a removed temp's bytes as {entry.path}"
            )


def assert_closed_without_restoring(c: Crashed, now: dict[str, bytes]) -> None:
    """After an undo of the killed record: nothing restored, no temp back, the
    record no longer the open most recent one."""
    assert tree(c.root) == now, "undo restored nothing: every file's bytes are as before it"
    for rel in c.temps:
        assert not (c.flavor / rel).exists(), f"the removed temp {rel} is not brought back"
    assert_no_manifest_holds_a_removed_temp(c)

    records = guard.history(store=c.store)
    ids = [r.id for r in records]
    assert c.record.id in ids and c.dead_id in ids, "the journal only grows"
    killed = records[ids.index(c.record.id)]
    assert killed.paths == () and killed.created_dirs == (), (
        "the killed record, read again after the undo, still names no path"
    )
    assert (c.flavor / CONFIG).read_bytes() == PLAYER_BYTES, "the player's later bytes stay"
    assert records[-1].state != "open", "undo closed the killed record"
    for later in records[ids.index(c.record.id) + 1 :]:
        assert later.paths == () and later.created_dirs == (), (
            f"record {later.id} after the killed one restores nothing"
        )
        assert later.state != "open"
        assert not set(later.temps_removed) & set(c.temps), (
            "a removed temp is gone, not removed again"
        )


def problem_naming(report: dict[str, Any], record_id: str) -> str:
    line = f"wowlab undo --expect {record_id}"
    found = [p for p in report["problems"] if line in p]
    assert len(found) == 1, (line, report["problems"])
    return str(found[0])


# ─── the journal after the kill ──────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_a_kill_between_cleanup_and_snapshot_leaves_a_record_that_touched_nothing(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, route: str
) -> None:
    """The record is `open`, journals both removals, names no stored
    snapshot and no path; the install differs only by the removed temps, and
    no snapshot in the store holds one."""
    c = kill_between_cleanup_and_snapshot(tmp_path, user_data, root, flavor, route)
    assert_no_manifest_holds_a_removed_temp(c)


# ─── undo closes it without restoring anything ───────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_guard_undo_closes_the_killed_record_without_restoring_anything(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, route: str
) -> None:
    c = kill_between_cleanup_and_snapshot(tmp_path, user_data, root, flavor, route)
    now = tree(c.root)

    guard.undo(store=c.store, expected_id=c.record.id)

    assert_closed_without_restoring(c, now)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_cli_undo_closes_a_killed_record_and_doctor_reports_the_change_before_it(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, route: str
) -> None:
    """Both routes. The killed record K was an `undo` of the interrupted
    change D (2026-11-11 (a1), and its undo-target ruling, "1 yes"), or a
    `transaction` entered after D was killed mid-write (2026-11-11 (a2),
    "Skip all, show D"; M16-108T). Until M16-108T the transaction route
    expected the target after the close to be K or its closer and `doctor`
    to be clean; (a2) passes over every closed record that never took a
    snapshot, so both routes now expect the same. Closing K (the line
    `doctor` prints, with `--yes`) restores nothing, but does not settle D:

    - `doctor` exits 1 and reports D with D's own `wowlab undo --expect <D id>`
      line, and names K (its wording is not pinned) without an undo line of
      its own;
    - `wowlab undo` passes over K's closed record, which changed no file, and
      offers D (the plan only: `--json` never asks);
    - `wowlab undo --expect <D id> --yes` settles D, and `doctor` is clean."""
    c = kill_between_cleanup_and_snapshot(tmp_path, user_data, root, flavor, route)
    (dead,) = [r for r in guard.history(store=c.store) if r.id == c.dead_id]
    assert dead.state == "open" and dead.paths, (
        "positive control: D is an interrupted change that journaled a file, so an undo "
        "of it has something to put back"
    )
    now = tree(c.root)

    closed = run("undo", "--expect", c.record.id, "--yes", "--json")

    assert closed.exit_code == 0, (closed.stdout, closed.stderr)
    report = json.loads(closed.stdout)
    assert report["undone"] == c.record.id
    assert report["plan"] == [] and report["created_dirs"] == [], "nothing to restore"
    assert_closed_without_restoring(c, now)
    journal = [(r.id, r.state) for r in guard.history(store=c.store)]
    held = manifest_ids(c.store)

    # A stale `--expect`: K, and any record C appended to close it, are passed
    # over, so neither is the target any more (M16-100T round 2).
    ids = [i for i, _ in journal]
    stale = ids[ids.index(c.record.id) :]
    assert stale[0] == c.record.id
    for gone in stale:
        with pytest.raises(guard.GuardError):
            guard.undo(store=c.store, expected_id=gone)
        again = run("undo", "--expect", gone, "--yes", "--json")
        assert again.exit_code == 3, (gone, again.stdout, again.stderr)
        assert tree(c.root) == now, f"a stale --expect {gone} changes no file"
        assert manifest_ids(c.store) == held, f"a stale --expect {gone} stores no snapshot"
        assert [(r.id, r.state) for r in guard.history(store=c.store)] == journal, (
            f"a stale --expect {gone} journals nothing"
        )

    # doctor: D again, with D's own line; K named, not offered.
    killed_line = f"wowlab undo --expect {c.record.id}"
    data = run("doctor", "--json")
    assert data.exit_code == 1, (
        "closing K does not settle the change killed mid-write before it",
        data.stdout,
    )
    problems = [str(p) for p in json.loads(data.stdout)["problems"]]
    problem_naming({"problems": problems}, c.dead_id)
    assert any(c.record.id in p for p in problems), (
        "doctor names K as interrupted before it changed anything",
        problems,
    )
    assert not any(killed_line in p for p in problems), (
        "K, already closed, is not offered to undo again",
        problems,
    )
    text = run("doctor")
    assert text.exit_code == 1, text.stdout
    shown = text.stdout.split("Problems:\n", 1)
    assert len(shown) == 2, text.stdout
    assert f"wowlab undo --expect {c.dead_id}" in shown[1], text.stdout
    assert c.record.id in shown[1], text.stdout
    assert killed_line not in text.stdout

    # `wowlab undo` picks D, not K's closed record.
    offered = run("undo", "--json")
    assert offered.exit_code == 1, ("--json without --yes shows the plan only", offered.stdout)
    plan = json.loads(offered.stdout)
    assert plan["undone"] == c.dead_id, (
        "K's closed record, which never took a snapshot, is passed over as the target",
        plan,
    )
    assert plan["applied"] is False and plan["plan"], plan
    asked = run("undo")
    assert asked.exit_code == 1, ("no answer is not a yes", asked.stdout)
    assert "changed no files" not in asked.stdout, (
        "the plan offered is D's, not K's empty one",
        asked.stdout,
    )
    assert tree(c.root) == now, "neither doctor nor an unconfirmed undo writes"
    assert manifest_ids(c.store) == held, "neither doctor nor an unconfirmed undo stores a snapshot"
    assert [(r.id, r.state) for r in guard.history(store=c.store)] == journal

    # D's own line settles D.
    settled = run("undo", "--expect", c.dead_id, "--yes", "--json")

    assert settled.exit_code == 0, (settled.stdout, settled.stderr)
    done = json.loads(settled.stdout)
    assert done["undone"] == c.dead_id and done["applied"] is True, done
    records = guard.history(store=c.store)
    assert [(r.id, r.state) for r in records[: len(journal)]] == journal, "the journal only grows"
    assert len(records) > len(journal) and records[-1].state != "open"
    for rel in c.temps:
        assert not (c.flavor / rel).exists(), f"the removed temp {rel} is not brought back"
    assert_no_manifest_holds_a_removed_temp(c)

    after = run("doctor", "--json")
    assert after.exit_code == 0, after.stdout
    assert not json.loads(after.stdout)["problems"]
    clean = run("doctor")
    assert clean.exit_code == 0, clean.stdout
    assert "Problems:" not in clean.stdout


# ─── (a1) made precise: `undoes`, and which records are passed over ──────────


REFUSED = 3  # the CLI's exit for a refused `--expect` (2026-09-28 amendment)
UNDOES = "undoes"


NO_FIELD = "<no undoes field>"
NO_UNDOES = (None, NO_FIELD)
"""What `undoes` reads as on a record `undo()` did not write: unset, or no
such field at all (whether the field exists on such a record is not pinned)."""


def undoes(record: Any) -> Any:
    """A record's `undoes`, or a marker that it has no such field yet."""
    return getattr(record, UNDOES, NO_FIELD)


def journal(store: Path) -> list[tuple[str, str]]:
    return [(r.id, r.state) for r in guard.history(store=store)]


def commit(flavor: Path, store: Path, label: str, rel: str, data: bytes) -> Any:
    """A change that completes normally, through the gate, in this process
    (no client in the process table); its record."""
    ns = SimpleNamespace(path=flavor, version=None)
    with guard.transaction(ns, label=label, store=store) as tx:
        tx.write(rel, data)
    record = guard.history(store=store)[-1]
    assert record.label == label and record.state != "open" and record.paths, "positive control"
    return record


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_a_killed_record_and_the_record_that_closes_it_name_what_they_undo(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, route: str
) -> None:
    """Every record `undo()` writes carries `undoes`. A killed undo K names
    D, before and after it is closed; a killed transaction K, which `undo()`
    did not write, names nothing (M16-108T: the transaction route, (a2)). A
    record appended to close K names K and changes no file, on both routes
    ((a1), and (a2): "that record names K in `undoes`")."""
    c = kill_between_cleanup_and_snapshot(tmp_path, user_data, root, flavor, route)
    names: tuple[Any, ...] = (c.dead_id,) if route == "undo" else NO_UNDOES
    assert undoes(c.record) in names, f"the killed {route} K: `undoes` is {undoes(c.record)!r}"

    guard.undo(store=c.store, expected_id=c.record.id)

    records = guard.history(store=c.store)
    ids = [r.id for r in records]
    k = records[ids.index(c.record.id)]
    assert k.state != "open", "K is closed"
    assert undoes(k) in names, "K, read again after it is closed, still names what it did"
    for later in records[ids.index(c.record.id) + 1 :]:
        assert undoes(later) == c.record.id, (
            f"record {later.id}, appended to close K, names K in `undoes`"
        )
        assert later.paths == () and later.created_dirs == (), "closing K changed no file"


@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_a_completed_undo_that_changed_no_file_is_not_passed_over(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path
) -> None:
    """D completes; Config.wtf is then put back outside guard (constructed:
    as a player or another program might), so `undo` of D completes normally
    and changes no file. That undo U is not passed over: it is the target,
    `--expect <D id>` is a mismatch, and U names D in `undoes`."""
    store = snapshot.default_store_path()
    original = (flavor / CONFIG).read_bytes()
    assert original != CRASHED_BYTES, "positive control"
    d = commit(flavor, store, "changed", CONFIG, CRASHED_BYTES)
    (flavor / CONFIG).write_bytes(original)

    guard.undo(store=store, expected_id=d.id)

    u = guard.history(store=store)[-1]
    assert u.id != d.id, "the completed undo is journaled"
    assert u.state != "open" and u.paths == () and u.created_dirs == (), (
        "the undo completed and changed no file: the disk already matched"
    )
    assert (flavor / CONFIG).read_bytes() == original
    before, recorded, held = tree(root), journal(store), manifest_ids(store)

    offered = run("undo", "--json")
    assert offered.exit_code == 0, (offered.stdout, offered.stderr)
    assert json.loads(offered.stdout)["undone"] == u.id, (
        "a normally completed undo is never passed over, even one that changed nothing"
    )
    with pytest.raises(guard.GuardError, match=re.escape(d.id)):
        guard.undo(store=store, expected_id=d.id)
    refused = run("undo", "--expect", d.id, "--yes", "--json")
    assert refused.exit_code == REFUSED, (refused.stdout, refused.stderr)
    assert tree(root) == before and journal(store) == recorded, "a mismatch changes nothing"
    assert manifest_ids(store) == held, "a mismatch and a plan store no snapshot"

    assert undoes(u) == d.id, "the completed undo names D in `undoes`"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_expect_d_is_a_mismatch_while_a_newer_record_is_not_passed_over(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, route: str
) -> None:
    """After K is closed, a fresh change T completes. T is not passed over,
    so it is the target: `--expect <D id>` raises (library) and is refused
    with exit 3 (CLI), and nothing changes. Both routes: under (a2) a closed
    killed transaction is passed over too, so D would be the target but for
    T (M16-108T)."""
    c = kill_between_cleanup_and_snapshot(tmp_path, user_data, root, flavor, route)
    guard.undo(store=c.store, expected_id=c.record.id)
    t = commit(flavor, c.store, "fresh", SYNDICATOR, STRANDED_BYTES)
    before, recorded, held = tree(root), journal(c.store), manifest_ids(c.store)

    offered = run("undo", "--json")
    assert json.loads(offered.stdout)["undone"] == t.id, "positive control: T is the target"
    with pytest.raises(guard.GuardError, match=re.escape(c.dead_id)):
        guard.undo(store=c.store, expected_id=c.dead_id)
    refused = run("undo", "--expect", c.dead_id, "--yes", "--json")
    assert refused.exit_code == REFUSED, (refused.stdout, refused.stderr)
    assert tree(root) == before and journal(c.store) == recorded, "a mismatch changes nothing"
    assert manifest_ids(c.store) == held, "a mismatch and a plan store no snapshot"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_an_open_killed_record_stays_the_target_until_undo_closes_it(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, route: str
) -> None:
    """(a2), "what is unchanged": a K that is still `open` is the target. So
    `wowlab undo --json` offers K, with nothing to restore, not D;
    `--expect <D id>` raises (library) and is refused with exit 3 (CLI), and
    nothing changes. Only once `undo` has closed K is D the target (M16-108T)."""
    c = kill_between_cleanup_and_snapshot(tmp_path, user_data, root, flavor, route)
    before, recorded, held = tree(root), journal(c.store), manifest_ids(c.store)

    offered = run("undo", "--json")
    plan = json.loads(offered.stdout)
    assert plan["undone"] == c.record.id, ("the open K is the target, not D", plan)
    assert plan["applied"] is False and plan["plan"] == [], ("K has nothing to restore", plan)
    with pytest.raises(guard.GuardError, match=re.escape(c.dead_id)):
        guard.undo(store=c.store, expected_id=c.dead_id)
    refused = run("undo", "--expect", c.dead_id, "--yes", "--json")
    assert refused.exit_code == REFUSED, (refused.stdout, refused.stderr)
    assert tree(root) == before and journal(c.store) == recorded, (
        "an --expect that names D while K is open changes nothing"
    )
    assert manifest_ids(c.store) == held, "a mismatch and a plan store no snapshot"

    guard.undo(store=c.store, expected_id=c.record.id)

    again = json.loads(run("undo", "--json").stdout)
    assert again["undone"] == c.dead_id, ("once K is closed, D is the target", again)


@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_an_undo_killed_after_its_snapshot_is_never_passed_over(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path
) -> None:
    """D is killed mid-write; the player's Config.wtf then changes, so an
    undo of D has a file to put back. That undo, U, is killed inside the
    rename of Config.wtf: after its pre-write snapshot. U has `undoes` and is
    then undone (which closes it), but it took a snapshot, so neither U nor
    its closer is passed over: `wowlab undo` targets the closer, not D, and
    `--expect <D id>` is a mismatch (exit 3)."""
    store = snapshot.default_store_path()
    leave_two_temps(tmp_path, user_data, flavor, store)
    d = guard.history(store=store)[-1]
    assert d.label == "child-died" and d.state == "open", "positive control"
    (flavor / CONFIG).write_bytes(PLAYER_BYTES)
    manifests = manifest_ids(store)

    killed = child(
        tmp_path,
        user_data,
        mode="die-in-rename",
        route="undo",
        writes=[],
        strand=[],
        die_on=Path(CONFIG).name,
        flavor=str(flavor),
        store=str(store),
    )
    assert killed.returncode == DIED_IN_RENAME, (killed.stdout, killed.stderr[-4000:])
    locks_released(flavor, store)
    u = guard.history(store=store)[-1]
    assert u.id != d.id and u.state == "open", "the undo journaled itself and was killed"
    assert u.snapshot_id in manifest_ids(store) - manifests, "U was killed after its snapshot"

    guard.undo(store=store, expected_id=u.id)
    records = guard.history(store=store)
    closer = records[-1]
    assert closer.id != u.id and closer.state != "open", "U is undone, by a record of its own"
    before, recorded, held = tree(root), journal(store), manifest_ids(store)

    offered = run("undo", "--json")
    assert json.loads(offered.stdout)["undone"] == closer.id, (
        "an undo killed after its snapshot is not passed over, nor is its closer",
        offered.stdout,
    )
    refused = run("undo", "--expect", d.id, "--yes", "--json")
    assert refused.exit_code == REFUSED, (refused.stdout, refused.stderr)
    with pytest.raises(guard.GuardError, match=re.escape(d.id)):
        guard.undo(store=store, expected_id=d.id)
    assert tree(root) == before and journal(store) == recorded, "a mismatch changes nothing"
    assert manifest_ids(store) == held, "a mismatch and a plan store no snapshot"

    assert undoes(records[[r.id for r in records].index(u.id)]) == d.id, "U names D"
    assert undoes(closer) == u.id, "U's closer names U"


ID = re.compile(r"[0-9]{8}-[0-9a-f]{8}")


@pytest.mark.parametrize(
    "names",
    [
        pytest.param("absent", id="constructed-undoes-names-an-absent-earlier-id"),
        pytest.param("malformed", id="constructed-undoes-names-a-malformed-id"),
        pytest.param("later", id="constructed-undoes-names-a-later-record"),
        pytest.param("itself", id="constructed-undoes-names-the-record-itself"),
        pytest.param("other-flavor", id="constructed-undoes-names-another-flavors-record"),
        pytest.param("empty", id="constructed-undoes-is-empty"),
        pytest.param("int", id="constructed-undoes-is-an-int"),
        pytest.param("null", id="constructed-undoes-is-null"),
        pytest.param("list", id="constructed-undoes-is-a-list"),
    ],
)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_history_refuses_an_undoes_that_names_no_earlier_record(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, names: str
) -> None:
    """A change X in a second flavor folder (a constructed copy of the first,
    a sibling under the same install), then a real change D and its real
    undo U. U carries `undoes: <D id>` in its journal file and reads back
    with it, also after the file is written back unchanged in the
    serialization this test uses (the positive control: the rewrite alone is
    not what is refused). Rewritten on disk to a value that is not the id of
    an earlier record of U's flavor, `history()` raises `GuardError` (a
    damaged record) and changes nothing."""
    store = snapshot.default_store_path()
    other = root / f"{flavor.name}copy_"
    shutil.copytree(flavor, other)
    x = commit(other, store, "other-flavor", CONFIG, CRASHED_BYTES)
    d = commit(flavor, store, "changed", CONFIG, CRASHED_BYTES)
    assert d.flavor_path != x.flavor_path, "positive control: X is in another flavor"
    guard.undo(store=store, expected_id=d.id)
    u = guard.history(store=store)[-1]
    later = commit(flavor, store, "later", SYNDICATOR, STRANDED_BYTES)
    assert u.id != d.id and later.id not in {u.id, d.id}
    ids = [r.id for r in guard.history(store=store)]
    assert ids.index(x.id) < ids.index(u.id) < ids.index(later.id)

    file = store / "journal" / f"{u.id}.json"
    raw = json.loads(file.read_bytes().decode("ascii"))
    assert raw.get(UNDOES) == d.id, f"the journal file carries {UNDOES!r}: {sorted(raw)}"

    def rewrite(value: Any) -> None:
        raw[UNDOES] = value
        text = json.dumps(raw, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
        file.write_bytes(text.encode("ascii"))

    rewrite(d.id)
    (reread,) = [r for r in guard.history(store=store) if r.id == u.id]
    assert undoes(reread) == d.id, "written back unchanged, U still reads with `undoes == D`"

    number, _, tail = d.id.partition("-")
    absent = f"{number}-{''.join('0' if ch != '0' else '1' for ch in tail)}"
    assert ID.fullmatch(absent) and absent not in ids and absent < u.id, absent
    malformed = "ffffffff-" + tail[::-1]
    assert not ID.fullmatch(malformed) and malformed not in ids
    rewrite(
        {
            "absent": absent,
            "malformed": malformed,
            "later": later.id,
            "itself": u.id,
            "other-flavor": x.id,
            "empty": "",
            "int": 1,
            "null": None,
            "list": [d.id],
        }[names]
    )
    install_before = tree(root)
    store_before = tree(store)

    with pytest.raises(guard.GuardError):
        guard.history(store=store)
    assert tree(root) == install_before, "reading a damaged record touched the install"
    assert tree(store) == store_before, "reading a damaged record touched the store"


# ─── doctor says the change stopped before it touched anything ───────────────


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_doctor_tells_a_change_killed_before_its_snapshot_from_one_killed_mid_write(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, route: str
) -> None:
    """Both are an `open` most recent record: exit 1 and the exact undo line
    (2026-11-10 (c)). The killed-before-snapshot one is reported differently
    (2026-11-11 (a): it stopped before it touched anything); its wording is
    not pinned, only that it is not the mid-write problem with the id
    swapped. `doctor` writes nothing."""
    store = snapshot.default_store_path()
    leave_two_temps(tmp_path, user_data, flavor, store)
    dead = guard.history(store=store)[-1]
    mid = run("doctor", "--json")
    assert mid.exit_code == 1, mid.stdout
    mid_write = problem_naming(json.loads(mid.stdout), dead.id)

    killed = child(
        tmp_path,
        user_data,
        mode="die-at-snapshot",
        route=route,
        write=[CONFIG, CRASHED_BYTES.hex()],
        flavor=str(flavor),
        store=str(store),
    )
    assert killed.returncode == DIED_AT_SNAPSHOT, killed.stderr[-4000:]
    locks_released(flavor, store)
    record = guard.history(store=store)[-1]
    assert record.id != dead.id and record.state == "open"
    assert record.temps_removed, "the cleanup ran before the kill"
    assert record.snapshot_id not in manifest_ids(store)

    install_before = tree(root)
    journal_before = [(r.id, r.state) for r in guard.history(store=store)]
    manifests_before = manifest_ids(store)

    data = run("doctor", "--json")
    text = run("doctor")

    assert data.exit_code == 1, data.stdout
    assert text.exit_code == 1, text.stdout
    report = json.loads(data.stdout)
    assert report["store"]["last_transaction"]["state"] == "open"
    stopped = problem_naming(report, record.id)
    line = f"wowlab undo --expect {record.id}"
    assert line in text.stdout.split("Problems:\n", 1)[-1]
    assert stopped.replace(record.id, "<id>") != mid_write.replace(dead.id, "<id>"), (
        "doctor tells a change that stopped before it touched anything from one killed "
        "mid-write, which may have changed some files"
    )

    assert tree(root) == install_before, "doctor writes nothing in the install"
    assert [(r.id, r.state) for r in guard.history(store=store)] == journal_before
    assert manifest_ids(store) == manifests_before


# ─── (a2): a snapshot that fails after the cleanup is closed and passed over ─

SNAPSHOT_FAILS = "constructed: no room for the pre-write snapshot"
T1_BYTES = b"constructed: Config.wtf as the completed change T1 wrote it\n"


RARESCANNER = f"{Path(SYNDICATOR).parent.as_posix()}/RareScanner.lua"
STRANDS = {
    SYNDICATOR: STRANDED_BYTES,
    RARESCANNER: b"constructed: RareScanner bytes whose rename was refused\n",
}


def commit_stranding_a_temp(
    monkeypatch: pytest.MonkeyPatch,
    flavor: Path,
    store: Path,
    strand: tuple[str, ...] = (SYNDICATOR,),
) -> tuple[Any, dict[str, bytes]]:
    """T1, in this process: a change that completes and writes Config.wtf,
    and whose write of each file in `strand` (SavedVariables files) strands
    its temp (the rename refused, the temp's unlink ignored, as the child's
    `die-in-rename` does). T1's record, and each stranded temp
    (flavor-relative path, bytes)."""
    ns = SimpleNamespace(path=flavor, version=None)
    sv_dir = flavor / Path(SYNDICATOR).parent
    assert all(Path(rel).parent == Path(SYNDICATOR).parent for rel in strand)
    sv_before = listing(sv_dir)
    kept = {rel: (flavor / rel).read_bytes() for rel in strand}
    refused_names = {Path(rel).name for rel in strand}
    stranded: dict[str, str] = {}

    def moving(real: Any) -> Any:
        def call(src: Any, dst: Any, *a: Any, **k: Any) -> Any:
            if Path(os.fsdecode(dst)).name in refused_names:
                stranded[Path(os.fsdecode(src)).name] = Path(os.fsdecode(dst)).name
                raise PermissionError(13, "constructed: the rename is refused")
            return real(src, dst, *a, **k)

        return call

    def removing(real: Any) -> Any:
        def call(path: Any, *a: Any, **k: Any) -> Any:
            if not isinstance(path, int) and Path(os.fsdecode(path)).name in stranded:
                return None  # the stranded temp stays behind
            return real(path, *a, **k)

        return call

    with monkeypatch.context() as m:
        for name, wrap in (
            ("replace", moving),
            ("rename", moving),
            ("unlink", removing),
            ("remove", removing),
        ):
            m.setattr(os, name, wrap(getattr(os, name)))
        with guard.transaction(ns, label="t1", store=store) as tx:
            for rel in strand:
                with pytest.raises(guard.GuardError):
                    tx.write(rel, STRANDS[rel])
            tx.write(CONFIG, T1_BYTES)

    t1 = guard.history(store=store)[-1]
    assert t1.label == "t1" and t1.state != "open", "positive control: T1 completed"
    assert CONFIG in [p.path for p in t1.paths], "positive control: T1 wrote Config.wtf"
    assert (flavor / CONFIG).read_bytes() == T1_BYTES
    for rel, data in kept.items():
        assert (flavor / rel).read_bytes() == data, f"the stranded rename of {rel} was refused"
    names = listing(sv_dir) - sv_before
    assert names == set(stranded), (names, stranded)
    temps = {}
    for name in sorted(names):
        assert TEMP_NAME.fullmatch(name), f"guard's temp name {name!r} is §6.10's pattern"
        rel = f"{Path(SYNDICATOR).parent.as_posix()}/{name}"
        target = f"{Path(SYNDICATOR).parent.as_posix()}/{stranded[name]}"
        assert (flavor / rel).read_bytes() == STRANDS[target]
        temps[rel] = STRANDS[target]
    assert len(temps) == len(strand)
    return t1, temps


def errors_in(exc: BaseException) -> list[BaseException]:
    """`exc` and every exception it was raised from or during."""
    seen: list[BaseException] = []
    at: BaseException | None = exc
    while at is not None and all(at is not s for s in seen):
        seen.append(at)
        at = at.__cause__ or at.__context__
    return seen


def enter_with_a_failing_snapshot(
    monkeypatch: pytest.MonkeyPatch, flavor: Path, store: Path
) -> None:
    """A change whose pre-write snapshot raises `SnapshotError` (constructed:
    every way the store takes one raises, only for this enter). The change
    fails with that error and its body never runs."""
    ns = SimpleNamespace(path=flavor, version=None)
    tried: list[str] = []
    ran = []

    def failing(name: str) -> Any:
        def call(*a: Any, **k: Any) -> Any:
            tried.append(name)
            raise SnapshotError(SNAPSHOT_FAILS)

        return call

    with monkeypatch.context() as m:
        for name in ("create", "add_snapshot", "put_object"):
            m.setattr(SnapshotStore, name, failing(name))
        with (
            pytest.raises((guard.GuardError, SnapshotError)) as raised,
            guard.transaction(ns, label="snapshot-failed", store=store) as tx,
        ):
            ran.append(True)
            tx.write(CONFIG, PLAYER_BYTES)
    assert tried, "positive control: the change tried to take its pre-write snapshot"
    assert not ran, "a change whose snapshot failed never runs its body"
    assert any(
        isinstance(e, SnapshotError) and SNAPSHOT_FAILS in str(e) for e in errors_in(raised.value)
    ), f"the change fails because its snapshot failed: {raised.value!r}"


CLEANUPS = [
    pytest.param(
        "leftover",
        id="constructed-the-snapshot-fails-after-a-cleanup",
        marks=pytest.mark.xfail(strict=True, reason="M16-69 not implemented"),
    ),
    # Positive control: nothing to clean up. It holds on main, where the
    # snapshot comes first and a failure journals nothing, so it has no marker.
    pytest.param("nothing", id="constructed-the-snapshot-fails-with-nothing-to-clean-up"),
]


@pytest.mark.parametrize("cleanup", CLEANUPS)
def test_constructed_a_change_whose_snapshot_fails_is_closed_by_the_gate_and_passed_over(
    tmp_path: Path,
    user_data: Path,
    root: Path,
    flavor: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup: str,
) -> None:
    """2026-11-11 (a2), the failed-snapshot route (M16-108T). T1 completes
    and wrote Config.wtf (and, with `leftover`, stranded a temp). The next
    change's pre-write snapshot raises `SnapshotError`, after its cleanup
    removed T1's temp. The change wrote no player file and the gate closed
    its record K itself; K is passed over:

    - `wowlab undo --json` offers T1, and `--expect <K id>` is a mismatch;
    - `doctor` has no problem (exit 0);
    - `guard.undo(expected_id=T1)` succeeds and puts Config.wtf back, the
      removed temp stays gone, and `doctor` is still clean.

    With nothing to clean up, this grader does not require the failed change
    to leave a record (so the control holds on main, where the snapshot
    comes first); any record it leaves is closed and names no path. That
    (a) puts the record before the snapshot is graded where it matters, by
    the two-in-a-row grader below."""
    store = snapshot.default_store_path()
    original = (flavor / CONFIG).read_bytes()
    assert original != T1_BYTES, "positive control"
    if cleanup == "leftover":
        t1, temps = commit_stranding_a_temp(monkeypatch, flavor, store)
    else:
        t1, temps = commit(flavor, store, "t1", CONFIG, T1_BYTES), {}
    ids_before = [r.id for r in guard.history(store=store)]
    manifests = manifest_ids(store)
    install_before = tree(root)

    enter_with_a_failing_snapshot(monkeypatch, flavor, store)

    for rel in temps:
        assert not (flavor / rel).exists(), f"the cleanup removed {rel} before the snapshot failed"
    removed = {f"{flavor.name}/{rel}" for rel in temps}
    expected = {k: v for k, v in install_before.items() if k not in removed}
    assert tree(root) == expected, "the failed change wrote no player file"
    assert manifest_ids(store) == manifests, "no snapshot of the failed change landed"
    records = guard.history(store=store)
    assert [r.id for r in records[: len(ids_before)]] == ids_before, "the journal only grows"
    failed = records[len(ids_before) :]
    if temps:
        assert failed, "the failed change journaled its removals in a record of its own"
        assert set(failed[0].temps_removed) == set(temps), "the cleanup ran, and is journaled"
    for k in failed:
        assert k.state != "open", f"the gate closed record {k.id} of the failed change itself"
        assert k.paths == () and k.created_dirs == (), f"record {k.id} names no path"
        assert k.snapshot_id not in manifests, f"record {k.id} names no snapshot the store holds"
    install_now, recorded, held = tree(root), journal(store), manifest_ids(store)

    # T1 stays the target; the failed change's record is passed over.
    offered = run("undo", "--json")
    plan = json.loads(offered.stdout)
    assert plan["undone"] == t1.id, ("the failed change's record is passed over", plan)
    assert plan["applied"] is False and plan["plan"], ("T1's plan puts Config.wtf back", plan)
    for k in failed:
        with pytest.raises(guard.GuardError, match=re.escape(k.id)):
            guard.undo(store=store, expected_id=k.id)
        refused = run("undo", "--expect", k.id, "--yes", "--json")
        assert refused.exit_code == REFUSED, (k.id, refused.stdout, refused.stderr)
    assert tree(root) == install_now and journal(store) == recorded, (
        "the plan and a mismatch change nothing"
    )
    assert manifest_ids(store) == held, "the plan and a mismatch store no snapshot"

    # doctor: nothing new for the failed change.
    data = run("doctor", "--json")
    assert data.exit_code == 0, data.stdout
    assert not json.loads(data.stdout)["problems"], data.stdout
    text = run("doctor")
    assert text.exit_code == 0, text.stdout
    assert "Problems:" not in text.stdout

    # `--expect <T1 id>` is accepted, and undoes T1.
    guard.undo(store=store, expected_id=t1.id)

    assert (flavor / CONFIG).read_bytes() == original, "T1 is undone"
    after = guard.history(store=store)
    assert [r.id for r in after[: len(recorded)]] == [i for i, _ in recorded]
    assert len(after) > len(recorded) and after[-1].state != "open"
    for rel in temps:
        assert not (flavor / rel).exists(), f"the removed temp {rel} is not brought back"
        for manifest in SnapshotStore(store).list():
            assert not any(e.path.endswith("/" + rel) for e in manifest.entries), (
                f"snapshot {manifest.id} holds the removed temp {rel}"
            )
    clean = run("doctor", "--json")
    assert clean.exit_code == 0, clean.stdout
    assert not json.loads(clean.stdout)["problems"], clean.stdout


# ─── (a2) "what never took a snapshot means" (2026-11-11, M16-108T round 2) ──


def offered(store: Path) -> str:
    """The id `wowlab undo --json` offers (the plan only), which stores no
    snapshot."""
    held = manifest_ids(store)
    result = run("undo", "--json")
    plan = json.loads(result.stdout)
    assert manifest_ids(store) == held, "a plan stores no snapshot"
    return str(plan["undone"])


def assert_mismatch(root: Path, store: Path, expected_id: str) -> None:
    """`--expect <expected_id>` names a record that is not the target: the
    library raises naming it, the CLI exits 3, and nothing changes."""
    before, recorded, held = tree(root), journal(store), manifest_ids(store)
    with pytest.raises(guard.GuardError, match=re.escape(expected_id)):
        guard.undo(store=store, expected_id=expected_id)
    refused = run("undo", "--expect", expected_id, "--yes", "--json")
    assert refused.exit_code == REFUSED, (expected_id, refused.stdout, refused.stderr)
    assert tree(root) == before, f"--expect {expected_id} changes no file"
    assert journal(store) == recorded, f"--expect {expected_id} journals nothing"
    assert manifest_ids(store) == held, f"--expect {expected_id} stores no snapshot"


def closed_by_the_gate(
    monkeypatch: pytest.MonkeyPatch, flavor: Path, store: Path
) -> tuple[Any, list[Any]]:
    """T1 completes and strands a temp; the next change's snapshot fails
    after its cleanup removed it. T1, and the records the failed change left
    (at least one: it journaled the removal)."""
    t1, temps = commit_stranding_a_temp(monkeypatch, flavor, store)
    n = len(guard.history(store=store))
    enter_with_a_failing_snapshot(monkeypatch, flavor, store)
    for rel in temps:
        assert not (flavor / rel).exists(), f"the cleanup removed {rel} before the snapshot failed"
    failed = guard.history(store=store)[n:]
    assert failed and set(failed[0].temps_removed) == set(temps), "the removal is journaled"
    for k in failed:
        assert k.state != "open" and k.paths == () and k.created_dirs == (), k
    return t1, failed


def rewrite_record(store: Path, record_id: str, **changes: Any) -> None:
    """Rewrite a journal file on disk (constructed), in the serialization the
    `history()` grader above uses; with no changes, the same content."""
    file = store / "journal" / f"{record_id}.json"
    raw = json.loads(file.read_bytes().decode("ascii"))
    raw.update(changes)
    text = json.dumps(raw, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
    file.write_bytes(text.encode("ascii"))


def raw_record(store: Path, record_id: str) -> dict[str, Any]:
    data = json.loads((store / "journal" / f"{record_id}.json").read_bytes().decode("ascii"))
    assert isinstance(data, dict)
    return data


def test_constructed_a_record_whose_manifest_is_missing_is_not_passed_over(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path
) -> None:
    """(a2), "what never took a snapshot means": a snapshot id that names no
    manifest does not count. T completes and writes Config.wtf; its manifest
    file is then removed from the store (constructed: deleted by hand). T is
    still the target: `wowlab undo --json` offers T, `undo --expect T --yes`
    refuses because T's snapshot cannot be read, and `--expect <T0 id>`, the
    record before T, is a mismatch. Nothing changes.

    Holds on main (no record is passed over there), so it carries no marker;
    it is here so that M16-69 cannot pass over a record by a missing manifest."""
    store = snapshot.default_store_path()
    t0 = commit(flavor, store, "t0", SYNDICATOR, STRANDED_BYTES)
    t = commit(flavor, store, "t", CONFIG, T1_BYTES)
    manifest = store / "manifests" / f"{t.snapshot_id}.json"
    assert manifest.is_file(), "positive control: T's manifest is where the store keeps it"
    manifest.unlink()
    assert t.snapshot_id not in manifest_ids(store)
    before, recorded, held = tree(root), journal(store), manifest_ids(store)

    assert offered(store) == t.id, "a record whose manifest is missing is still the target"
    with pytest.raises((guard.GuardError, SnapshotError)) as raised:
        guard.undo(store=store, expected_id=t.id)
    assert t.snapshot_id in str(raised.value) or any(
        isinstance(e, SnapshotError) for e in errors_in(raised.value)
    ), f"the undo of T is refused because its snapshot cannot be read: {raised.value!r}"
    cli = run("undo", "--expect", t.id, "--yes", "--json")
    assert cli.exit_code != 0, (cli.stdout, cli.stderr)
    assert t.snapshot_id in json.loads(cli.stdout)["message"], (
        "refused because T's snapshot cannot be read, not as a mismatch",
        cli.stdout,
    )
    assert tree(root) == before and journal(store) == recorded and manifest_ids(store) == held, (
        "a refused undo changes nothing"
    )
    assert (flavor / CONFIG).read_bytes() == T1_BYTES

    assert_mismatch(root, store, t0.id)


FORGED_PATHS = [
    pytest.param("paths", id="constructed-the-gate-closed-record-forged-to-list-a-path"),
    pytest.param("created_dirs", id="constructed-the-gate-closed-record-forged-to-list-a-folder"),
]


@pytest.mark.parametrize("field", FORGED_PATHS)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_a_record_with_no_snapshot_that_lists_a_path_is_not_passed_over(
    tmp_path: Path,
    user_data: Path,
    root: Path,
    flavor: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """(a2): K's `paths` and `created_dirs` must both be empty; "a record that
    lists a path or a created folder is never passed over, whatever it says
    about snapshots". K, closed by the gate after its snapshot failed, has its
    journal file rewritten (constructed) to list T1's path, or a folder. The
    spec does not say whether that record is damaged, so either is accepted:
    `history()` refuses it with `GuardError`, or K is the target and
    `--expect <T1 id>` is a mismatch. Either way nothing changes, and T1 is
    not offered behind it. The positive control: written back unchanged, K is
    still passed over."""
    store = snapshot.default_store_path()
    t1, failed = closed_by_the_gate(monkeypatch, flavor, store)
    k = failed[-1]
    rewrite_record(store, k.id)
    assert offered(store) == t1.id, "positive control: the rewrite alone passes K over still"

    if field == "paths":
        forged: Any = raw_record(store, t1.id)["paths"]
        assert forged, "positive control: T1's record lists Config.wtf"
    else:
        forged = ["WTF/Account/constructed-folder"]
    rewrite_record(store, k.id, **{field: forged})
    before, held = tree(root), manifest_ids(store)

    try:
        records = guard.history(store=store)
    except guard.GuardError:
        assert tree(root) == before and manifest_ids(store) == held
        return
    (reread,) = [r for r in records if r.id == k.id]
    assert getattr(reread, field), f"the forged {field} reads back"
    assert offered(store) == k.id, f"a record that lists {field} is never passed over"
    assert_mismatch(root, store, t1.id)


CLOSERS = [
    pytest.param("paths", id="constructed-a-record-naming-k-that-lists-a-path"),
    pytest.param("snapshot", id="constructed-a-record-naming-k-that-took-a-snapshot"),
]


@pytest.mark.parametrize("shape", CLOSERS)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_a_record_naming_a_passed_over_record_is_passed_over_only_if_it_changed_nothing(
    tmp_path: Path,
    user_data: Path,
    root: Path,
    flavor: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
) -> None:
    """(a2): "a record that closes K is passed over only if it too has no
    `paths`, no `created_dirs` and no snapshot". K is closed by the gate and
    passed over. A completed change T follows, and its journal file is
    rewritten (constructed) to carry `undoes: <K id>`: with its paths and
    stored snapshot as they are, or with `paths` and `created_dirs` emptied
    and its stored snapshot kept. Neither is passed over: `history()` refuses
    it as damaged, or T is the target and `--expect <T1 id>` is a mismatch
    (the spec does not say whether `undoes` on a transaction's record is
    damaged, so either is accepted)."""
    store = snapshot.default_store_path()
    t1, failed = closed_by_the_gate(monkeypatch, flavor, store)
    k = failed[-1]
    t = commit(flavor, store, "t", CONFIG, PLAYER_BYTES)
    assert t.snapshot_id in manifest_ids(store), "positive control: T took a stored snapshot"
    rewrite_record(store, t.id)
    assert offered(store) == t.id, "positive control: T is the target"

    changes: dict[str, Any] = {UNDOES: k.id}
    if shape == "snapshot":
        changes.update(paths=[], created_dirs=[])
    rewrite_record(store, t.id, **changes)
    before, held = tree(root), manifest_ids(store)

    try:
        records = guard.history(store=store)
    except guard.GuardError:
        assert tree(root) == before and manifest_ids(store) == held
        return
    (reread,) = [r for r in records if r.id == t.id]
    assert undoes(reread) == k.id, "the forged `undoes` reads back"
    assert offered(store) == t.id, "a record naming K that changed something is not passed over"
    assert_mismatch(root, store, t1.id)
    assert_mismatch(root, store, k.id)


IN_A_ROW = [
    pytest.param("two-failed-snapshots", id="constructed-two-failed-snapshots-in-a-row"),
    pytest.param(
        "failed-then-killed", id="constructed-a-failed-snapshot-then-a-closed-killed-change"
    ),
]


def leave_a_directory_at(flavor: Path, rel: str) -> bytes:
    """Constructed: a directory where a leftover temp file was, which the
    next cleanup must leave alone and list in `temps_left` (§6.10 item 2: "a
    link, junction, reparse point or directory at the name is not absent").
    The temp's bytes, to put the file back later."""
    data = (flavor / rel).read_bytes()
    (flavor / rel).unlink()
    (flavor / rel).mkdir()
    return data


def put_the_file_back(flavor: Path, rel: str, data: bytes) -> None:
    (flavor / rel).rmdir()
    (flavor / rel).write_bytes(data)


@pytest.mark.parametrize("shape", IN_A_ROW)
@pytest.mark.xfail(strict=True, reason="M16-69 not implemented")
def test_constructed_passed_over_records_in_a_row_are_all_passed_over(
    tmp_path: Path,
    user_data: Path,
    root: Path,
    flavor: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
) -> None:
    """(a2): "any number of such records in a row are passed over". Before
    the two, a record B: T1 (completed) in `two-failed-snapshots`, D (open,
    killed mid-write) in `failed-then-killed`. B leaves two temps, and the
    second is made a directory (constructed), so the first change after B
    removes one temp and leaves the other in `temps_left`; the file is put
    back, and the second change removes it. Each change therefore journals a
    removal, so each has a record before its snapshot (§6.10 item 2, (a)):

    - `two-failed-snapshots`: both changes fail at their snapshot, and the
      gate closes each record;
    - `failed-then-killed`: the first fails at its snapshot; the second, a
      transaction, is killed between its cleanup and its snapshot and then
      closed by `undo`.

    The target is B; `--expect` naming any record after B is a mismatch;
    `doctor` is clean when B completed, and reports B with B's own undo line
    (exit 1) when B is open."""
    store = snapshot.default_store_path()
    if shape == "two-failed-snapshots":
        b, temps = commit_stranding_a_temp(monkeypatch, flavor, store, (SYNDICATOR, RARESCANNER))
    else:
        temps = leave_two_temps(tmp_path, user_data, flavor, store)
        b = guard.history(store=store)[-1]
        assert b.label == "child-died" and b.state == "open", "positive control"
    first, second = sorted(temps)
    second_bytes = leave_a_directory_at(flavor, second)

    n = len(guard.history(store=store))
    enter_with_a_failing_snapshot(monkeypatch, flavor, store)
    k1 = guard.history(store=store)[n:]
    assert k1, "the first change journaled its cleanup in a record before its snapshot failed"
    assert set(k1[0].temps_removed) == {first} and second in k1[0].temps_left, k1[0]
    assert not (flavor / first).exists() and (flavor / second).is_dir()
    put_the_file_back(flavor, second, second_bytes)

    n = len(guard.history(store=store))
    if shape == "two-failed-snapshots":
        enter_with_a_failing_snapshot(monkeypatch, flavor, store)
    else:
        killed = child(
            tmp_path,
            user_data,
            mode="die-at-snapshot",
            route="transaction",
            write=[CONFIG, CRASHED_BYTES.hex()],
            flavor=str(flavor),
            store=str(store),
        )
        assert killed.returncode == DIED_AT_SNAPSHOT, (killed.stdout, killed.stderr[-4000:])
        locks_released(flavor, store)
        k2 = guard.history(store=store)[-1]
        assert k2.state == "open" and second in k2.temps_removed, k2
        guard.undo(store=store, expected_id=k2.id)
    k2s = guard.history(store=store)[n:]
    assert k2s and second in k2s[0].temps_removed, "the second change removed the other temp"
    assert not (flavor / second).exists()

    records = guard.history(store=store)
    ids = [r.id for r in records]
    after_b = records[ids.index(b.id) + 1 :]
    assert len(after_b) >= 2, [(r.id, r.state) for r in after_b]
    for k in after_b:
        assert k.state != "open" and k.paths == () and k.created_dirs == (), k

    assert offered(store) == b.id, "every passed-over record in a row is skipped"
    for k in after_b:
        assert_mismatch(root, store, k.id)

    data = run("doctor", "--json")
    problems = [str(p) for p in json.loads(data.stdout)["problems"]]
    if b.state == "open":
        assert data.exit_code == 1, data.stdout
        problem_naming({"problems": problems}, b.id)
        for k in after_b:
            assert not any(f"wowlab undo --expect {k.id}" in p for p in problems), problems
    else:
        assert data.exit_code == 0 and not problems, data.stdout


ROLLED_BACK = [
    pytest.param(True, id="constructed-rolled-back-after-a-write"),
    pytest.param(False, id="constructed-rolled-back-before-any-write"),
]


@pytest.mark.parametrize("wrote", ROLLED_BACK)
def test_constructed_a_transaction_rolled_back_after_its_snapshot_is_never_passed_over(
    tmp_path: Path, user_data: Path, root: Path, flavor: Path, wrote: bool
) -> None:
    """(a2), "what is unchanged": a record that took a pre-write snapshot is
    never passed over. A transaction R takes its snapshot, then its caller
    fails (after a write, or before any), and R rolls back. R is the target,
    not T1 before it: `wowlab undo --json` offers R and `--expect <T1 id>`
    is a mismatch.

    Holds on main (no record is passed over there), so it carries no marker;
    it keeps M16-69 from passing over a rolled-back record with an empty
    `paths`."""
    store = snapshot.default_store_path()
    t1 = commit(flavor, store, "t1", CONFIG, T1_BYTES)
    ns = SimpleNamespace(path=flavor, version=None)
    with (
        pytest.raises(RuntimeError, match="constructed: the caller fails"),
        guard.transaction(ns, label="rolled-back", store=store) as tx,
    ):
        if wrote:
            tx.write(SYNDICATOR, STRANDED_BYTES)
        raise RuntimeError("constructed: the caller fails")
    r = guard.history(store=store)[-1]
    assert r.label == "rolled-back" and r.state != "open", r
    assert r.snapshot_id in manifest_ids(store), "positive control: R took a stored snapshot"
    assert bool(r.paths) is wrote, r.paths

    assert offered(store) == r.id, "a rolled-back record that took a snapshot is the target"
    assert_mismatch(root, store, t1.id)
