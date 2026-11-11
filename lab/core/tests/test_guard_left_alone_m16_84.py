"""Graders for M16-84: the public journal record names the paths a rollback
left alone (M16-84T).

Written before M16-84, from the ticket in docs/BACKLOG.md, the M16-71
domain review it cites (#441: `doctor` can only say "some files" because the
paths are only in the record's free-text note), docs/LAB_PLAN.md §6.10
amendment item 3 as clarified 2026-09-22 (the rollback contract: a touched
path whose disk no longer matches guard's own record of it is left as it
is, named in the note and in the error at exit, and the record ends
`rollback_incomplete`; a path whose disk already holds its pre-transaction
content counts as rolled back; a refused path makes the record
`rollback_incomplete` only if the disk moved since guard read it) and
ADR-0021. Every grader carries one marker line that M16-84 deletes; nothing
else in this file is the implementer's to change.

The seam
--------
- `HistoryRecord.paths_left: tuple[str, ...]` (the name was this file's
  choice, accepted by both reviewers of #453; the ticket and the plan give
  none): a tuple of the flavor-relative, `/`-separated
  paths the rollback of this record left as they are, spelled as the
  record's `paths` spell them. A tuple, like every other sequence on the
  frozen record (`created_dirs`, `temps_removed`, `temps_left`).
- It is empty for every record that is not `rollback_incomplete`
  (committed, rolled back), and names exactly the left-alone paths of one
  that is: never a path the rollback put back, never one that already held
  its pre-transaction content, never an absolute path, never the note's
  message text. Every entry is one of the record's own `paths` (or a path
  whose only operation was refused, which `paths` need not name), has no
  empty, `.` or `..` part and no control character. Order is not pinned
  (the rollback walks the paths in reverse); duplicates are not allowed. A
  path name may contain `; ` (the gate allows it), so the list cannot come
  from splitting the note.
- `undo()` is a transaction: its own record follows the same rule.
- A journal record the M16-71 gate wrote (format 2, no structured list)
  still reads through `history()` with `paths_left == ()`, whatever its
  note says: the note is never parsed (reviewers' ruling on #453).
- The journal file carries the list under the key `paths_left` (whatever
  the format number). A record read back from disk whose `paths_left` is
  not a list of paths the record journals (its `paths`, or refused paths
  if the gate journals those), flavor-relative and plain, or that
  names a path on a record that is not `rollback_incomplete`, is damaged:
  `history()` raises `GuardError` and nothing in the install or the store
  changes (`_load_journal`: "a damaged record is an error, never skipped").
- A directory the rollback created and could not remove stays out of the
  list (reviewers' ruling on #453; not graded here).

Everything is constructed, as in `test_guard_owner_decisions.py`, whose
synthetic install, fixtures and race hooks (`AfterJournalFsync`,
`AfterTempFsync`, `_swap`) this file loads rather than copies: the install
is a tree under `tmp_path`, the user data directory is redirected, the
process table is injected. "Another program" is this test process writing
the install directly, outside guard. The refusals are a `PermissionError`
injected into `os.replace` / `os.rename` / `os.unlink` for one target
path only (`os.rename` is how guard creates a missing target on Windows).
"""

from __future__ import annotations

import errno
import importlib.util
import json
import os
import sys
import types
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore


def _load(name: str, filename: str) -> types.ModuleType:
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OD = _load("_m16_84_owner_graders", "test_guard_owner_decisions.py")
G = OD.G

_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
idle = G.idle

Flavor = G.Flavor
FLAVOR_FOLDER = G.FLAVOR_FOLDER
ALLOWLISTED_FILES = G.ALLOWLISTED_FILES
CONFIG = G.CONFIG
BINDINGS = G.BINDINGS
NEW_SAVED = G.NEW_SAVED
ICON = G.ICON
FONT = G.FONT
NEW_CONFIG = G.NEW_CONFIG
NEW_LUA = G.NEW_LUA
sha = G.sha
record_for = G.record_for
EXTERNAL = OD.EXTERNAL
ICON_NEW = OD.ICON_NEW
SECOND_FONT = OD.SECOND_FONT
AfterJournalFsync = OD.AfterJournalFsync
AfterTempFsync = OD.AfterTempFsync
error_text = OD.error_text
refused = OD.refused

FIELD = "paths_left"  # judgment call: the spec names the list but not the field
FAILURE = "constructed failure"
ODD = "WTF/Account/ACCT/SavedVariables/odd; name.lua"  # constructed: the gate allows `;`
EXTERNAL_AGAIN = b"constructed: another program changed it again after the refusal\n"


# ─── helpers ─────────────────────────────────────────────────────────────────


def left_alone(record: Any) -> tuple[str, ...]:
    """The record's left-alone list, after checking its shape."""
    assert FIELD in type(record).model_fields, (
        f"HistoryRecord has no {FIELD!r}: the left-alone paths are only in the note"
    )
    value = getattr(record, FIELD)
    assert isinstance(value, tuple), f"{FIELD} is a tuple like the record's other lists: {value!r}"
    assert all(isinstance(item, str) for item in value), value
    return value


def assert_left_alone(
    record: Any, expected: Iterable[str], flavor: Flavor, *, refused: Iterable[str] = ()
) -> None:
    """`record` names exactly `expected`, flavor-relative and bare. Each entry
    is one of the record's `paths`, or one of `refused`: a path whose only
    operation was refused, which §6.10 keeps out of what `undo()` acts on, so
    `paths` need not name it."""
    value = left_alone(record)
    want = sorted(expected)
    assert len(value) == len(set(value)), f"{FIELD} names a path twice: {value!r}"
    assert sorted(value) == want, f"{FIELD} is {value!r}, expected {want!r}"
    journaled = {p.path for p in record.paths} | set(refused)
    for item in value:
        assert not Path(item).is_absolute() and "\\" not in item, f"not flavor-relative: {item!r}"
        assert str(flavor.path) not in item and FLAVOR_FOLDER not in item, item
        assert item in journaled, f"{item!r} is not one of the record's paths {journaled!r}"
        assert not {"", ".", ".."} & set(item.split("/")), f"not a plain path: {item!r}"
        assert not any(ord(c) < 32 or c == "\x7f" for c in item), f"control char: {item!r}"
    dumped = record.model_dump(mode="json")
    assert sorted(dumped[FIELD]) == want, "the list is part of the public record's JSON"


def fail_inside(
    guard: Any, flavor: Flavor, store: Path, label: str, body: Callable[[Any], None]
) -> BaseException:
    """Run `body(tx)` in a transaction, then raise; return the exception at exit."""
    with (
        pytest.raises(RuntimeError, match=FAILURE) as raised,
        guard.transaction(flavor, label=label, store=store) as tx,
    ):
        body(tx)
        raise RuntimeError(FAILURE)
    return raised.value


def _same(path: Any, target: Path) -> bool:
    try:
        return Path(os.fspath(path)).resolve() == target.resolve()
    except (OSError, TypeError, ValueError):
        return False


class RefuseAt:
    """`os.replace` and `os.rename` (to `target`) and `os.unlink` (of
    `target`) raise `PermissionError`, as a system that refuses the rollback
    would. `os.rename` is guard's create of a missing target on Windows.
    Every other call goes through."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self.fired = False
        self._replace = os.replace
        self._rename = os.rename
        self._unlink = os.unlink

    def arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "replace", self._replace_or_refuse)
        monkeypatch.setattr(os, "rename", self._rename_or_refuse)
        monkeypatch.setattr(os, "unlink", self._unlink_or_refuse)

    def _refuse(self) -> None:
        self.fired = True
        raise PermissionError(errno.EACCES, "constructed: permission denied", str(self.target))

    def _replace_or_refuse(self, src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if _same(dst, self.target):
            self._refuse()
        self._replace(src, dst, *args, **kwargs)

    def _rename_or_refuse(self, src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if _same(dst, self.target):
            self._refuse()
        self._rename(src, dst, *args, **kwargs)

    def _unlink_or_refuse(self, path: Any, *args: Any, **kwargs: Any) -> None:
        if _same(path, self.target):
            self._refuse()
        self._unlink(path, *args, **kwargs)


# ─── an empty list when nothing was left alone ───────────────────────────────


def test_constructed_a_committed_record_names_no_path_left_alone(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    store = tmp_path / "store"
    with guard.transaction(flavor, label="plain", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
        tx.delete(BINDINGS)
    record = record_for(guard, SnapshotStore(store), "plain")
    assert record.state == "committed"
    assert_left_alone(record, [], flavor)


def test_constructed_a_complete_rollback_names_no_path_left_alone(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    """Every touched path is put back, or already held its pre-transaction
    content (another program restored Fonts): `rolled_back`, empty list."""
    store = tmp_path / "store"

    def body(tx: Any) -> None:
        tx.write(CONFIG, NEW_CONFIG)
        tx.write(NEW_SAVED, NEW_LUA)
        tx.write(FONT, SECOND_FONT)
        (flavor.path / FONT).write_bytes(ALLOWLISTED_FILES[FONT])

    fail_inside(guard, flavor, store, "failing", body)
    assert (flavor.path / CONFIG).read_bytes() == ALLOWLISTED_FILES[CONFIG]
    assert not (flavor.path / NEW_SAVED).exists()
    record = record_for(guard, SnapshotStore(store), "failing")
    assert record.state == "rolled_back"
    assert_left_alone(record, [], flavor)


# ─── another program changed one of two files ────────────────────────────────

# (id, path, how the transaction touches it, what the other program does)
OTHER_PROGRAM_CASES = (
    ("overwritten-after-a-write", CONFIG, "write", "modify"),
    ("removed-after-a-write", CONFIG, "write", "remove"),
    ("recreated-after-a-delete", BINDINGS, "delete", "create"),
    ("changed-after-a-create", NEW_SAVED, "write", "modify"),
    # A name with the note's own separator in it: a list read back from the
    # note (split on "; ", then on ": ") would name "name.lua" or the half
    # before the semicolon, never this path.
    ("changed-after-a-create-of-a-name-with-a-semicolon", ODD, "write", "modify"),
)


@pytest.mark.parametrize(
    ("rel", "op", "change"),
    [pytest.param(r, o, c, id=f"constructed-{n}") for n, r, o, c in OTHER_PROGRAM_CASES],
)
def test_constructed_another_program_changed_one_of_two_files(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, rel: str, op: str, change: str
) -> None:
    """The transaction changes the icon and `rel`; another program then
    changes `rel`; the transaction fails. The rollback puts the icon back and
    leaves `rel`: the record names `rel` and only `rel`."""
    store = tmp_path / "store"
    target = flavor.path / rel
    held: list[bytes | None] = []

    def body(tx: Any) -> None:
        tx.write(ICON, ICON_NEW)
        OD._touch(op, tx, rel, NEW_LUA)
        held.append(OD._change(change, target))

    exc = fail_inside(guard, flavor, store, "failing", body)
    assert OD._held(target) == held[0], "the other program's state was left as it is"
    assert (flavor.path / ICON).read_bytes() == ALLOWLISTED_FILES[ICON], "the icon was put back"
    assert rel in error_text(exc)
    record = record_for(guard, SnapshotStore(store), "failing")
    assert record.state == "rollback_incomplete"
    assert ICON in {p.path for p in record.paths} and rel in {p.path for p in record.paths}
    assert_left_alone(record, [rel], flavor)


def test_constructed_another_program_changed_both_files(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    store = tmp_path / "store"

    def body(tx: Any) -> None:
        tx.write(ICON, ICON_NEW)
        tx.write(CONFIG, NEW_CONFIG)
        (flavor.path / ICON).write_bytes(EXTERNAL)
        (flavor.path / CONFIG).write_bytes(EXTERNAL)

    fail_inside(guard, flavor, store, "failing", body)
    record = record_for(guard, SnapshotStore(store), "failing")
    assert record.state == "rollback_incomplete"
    assert_left_alone(record, [ICON, CONFIG], flavor)


def test_constructed_another_program_changed_a_file_while_the_rollback_ran(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    """The change lands during the rollback itself, right after it journals
    the first temp name it will use: Config.wtf is left with the other
    program's bytes, the icon is put back."""
    store = tmp_path / "store"
    config = flavor.path / CONFIG
    exiting = [False]
    hook = AfterJournalFsync(store, lambda: config.write_bytes(EXTERNAL), when=lambda: exiting[0])
    with (
        pytest.raises(RuntimeError, match=FAILURE) as raised,
        pytest.MonkeyPatch.context() as patched,  # outermost of the two: covers the exit
        guard.transaction(flavor, label="failing", store=store) as tx,
    ):
        tx.write(ICON, ICON_NEW)
        tx.write(CONFIG, NEW_CONFIG)
        hook.arm(patched)
        exiting[0] = True
        raise RuntimeError(FAILURE)
    assert hook.fired, "positive control: the rollback journaled before writing"
    assert config.read_bytes() == EXTERNAL
    assert (flavor.path / ICON).read_bytes() == ALLOWLISTED_FILES[ICON]
    assert CONFIG in error_text(raised.value)
    record = record_for(guard, SnapshotStore(store), "failing")
    assert record.state == "rollback_incomplete"
    assert_left_alone(record, [CONFIG], flavor)


@pytest.mark.parametrize(
    "moved_again",
    [pytest.param(m, id=f"constructed-{'moved' if m else 'kept'}") for m in (True, False)],
)
def test_constructed_a_refused_path_is_listed_only_if_the_disk_moved_after_guard_read_it(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, moved_again: bool
) -> None:
    """Config.wtf changes after the pre-write snapshot, so its first touch is
    refused. If another program changes it again before exit, the record is
    `rollback_incomplete` and names it; if not, the record is `rolled_back`
    and names nothing. The error at exit is the gate's own `GuardError`."""
    store = tmp_path / "store"
    config = flavor.path / CONFIG
    reached_end = False
    with (
        pytest.raises(guard.GuardError) as raised,
        guard.transaction(flavor, label="refused", store=store) as tx,
    ):
        tx.write(ICON, ICON_NEW)
        config.write_bytes(EXTERNAL)
        with refused(guard, "ChangedSinceSnapshotError", naming=CONFIG):
            tx.write(CONFIG, NEW_CONFIG)
        if moved_again:
            config.write_bytes(EXTERNAL_AGAIN)
        reached_end = True
    assert reached_end
    assert (flavor.path / ICON).read_bytes() == ALLOWLISTED_FILES[ICON]
    assert config.read_bytes() == (EXTERNAL_AGAIN if moved_again else EXTERNAL)
    record = record_for(guard, SnapshotStore(store), "refused")
    if moved_again:
        assert CONFIG in error_text(raised.value)
        assert record.state == "rollback_incomplete"
        assert_left_alone(record, [CONFIG], flavor, refused=[CONFIG])
    else:
        assert record.state == "rolled_back"
        assert_left_alone(record, [], flavor)


# ─── a rollback the system refused ───────────────────────────────────────────

# (id, path, how the transaction touches it); the rollback's replace or
# unlink of that path is refused with a PermissionError.
SYSTEM_REFUSED_CASES = (
    ("put-back-overwritten", CONFIG, "write"),
    ("remove-created", NEW_SAVED, "write"),
    ("recreate-deleted", BINDINGS, "delete"),
)


@pytest.mark.parametrize(
    ("rel", "op"),
    [pytest.param(r, o, id=f"constructed-{n}") for n, r, o in SYSTEM_REFUSED_CASES],
)
def test_constructed_a_rollback_the_system_refused_names_the_path(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None, rel: str, op: str
) -> None:
    """The system refuses the rollback's write to (or removal of) `rel` with
    a permission error. `rel` keeps what the transaction wrote; the icon is
    put back; the record names `rel` alone, without the error text."""
    store = tmp_path / "store"
    target = flavor.path / rel
    refusal = RefuseAt(target)
    with (
        pytest.raises(RuntimeError, match=FAILURE) as raised,
        pytest.MonkeyPatch.context() as patched,  # outermost of the two: covers the exit
        guard.transaction(flavor, label="failing", store=store) as tx,
    ):
        tx.write(ICON, ICON_NEW)
        OD._touch(op, tx, rel, NEW_LUA)
        written = OD._held(target)
        refusal.arm(patched)
        raise RuntimeError(FAILURE)
    assert refusal.fired, "positive control: the rollback tried to put the path back"
    assert OD._held(target) == written, "the refused path keeps what the transaction left"
    assert (flavor.path / ICON).read_bytes() == ALLOWLISTED_FILES[ICON], "the icon was put back"
    assert rel in error_text(raised.value)
    record = record_for(guard, SnapshotStore(store), "failing")
    assert record.state == "rollback_incomplete"
    assert_left_alone(record, [rel], flavor)


def test_constructed_a_refused_rollback_and_another_programs_change_are_both_named(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    store = tmp_path / "store"
    refusal = RefuseAt(flavor.path / CONFIG)
    with (
        pytest.raises(RuntimeError, match=FAILURE),
        pytest.MonkeyPatch.context() as patched,
        guard.transaction(flavor, label="failing", store=store) as tx,
    ):
        tx.write(ICON, ICON_NEW)
        tx.write(FONT, SECOND_FONT)
        tx.write(CONFIG, NEW_CONFIG)
        (flavor.path / ICON).write_bytes(EXTERNAL)
        refusal.arm(patched)
        raise RuntimeError(FAILURE)
    assert refusal.fired
    assert (flavor.path / FONT).read_bytes() == ALLOWLISTED_FILES[FONT]
    record = record_for(guard, SnapshotStore(store), "failing")
    assert record.state == "rollback_incomplete"
    assert_left_alone(record, [ICON, CONFIG], flavor)


# ─── undo is a transaction too ───────────────────────────────────────────────


def test_constructed_an_undo_that_ends_rollback_incomplete_names_the_path(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    """Config.wtf is swapped by another program just after `undo()` fsyncs
    the temp file that would put it back: undo is refused, its own record
    ends `rollback_incomplete` and names Config.wtf; the undone record's own
    list stays empty."""
    store = tmp_path / "store"
    config = flavor.path / CONFIG
    with guard.transaction(flavor, label="mine", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    hook = AfterTempFsync(config.parent, OD._swap("another-file", config))
    with pytest.MonkeyPatch.context() as patched:
        hook.arm(patched)
        with refused(guard, "ChangedSinceSnapshotError", naming=CONFIG):
            guard.undo(store=store)
    assert hook.fired, "positive control: undo wrote a temp file and fsynced it"
    assert config.read_bytes() == EXTERNAL
    last = guard.history(store=store)[-1]
    assert last.label != "mine" and last.state == "rollback_incomplete"
    assert_left_alone(last, [CONFIG], flavor, refused=[CONFIG])
    assert_left_alone(record_for(guard, SnapshotStore(store), "mine"), [], flavor)


# ─── records the M16-71 gate wrote still read ────────────────────────────────


PLAIN_NOTE = (
    f"{CONFIG}: changed by something else after this transaction last wrote it; left as it is"
)
HOSTILE_NOTE = "../../etc/passwd: x; \x1b[31mWTF/Config.wtf\u202e: y"  # constructed


@pytest.mark.parametrize(
    "note",
    [
        pytest.param(PLAIN_NOTE, id="constructed-plain-note"),
        pytest.param(HOSTILE_NOTE, id="constructed-hostile-note"),
    ],
)
def test_constructed_format_2_records_without_the_list_still_read(
    guard: Any, tmp_path: Path, install_root: Path, flavor: Flavor, idle: None, note: str
) -> None:
    """Two format-2 records laid out as the gate on main before M16-84 writes
    them (no structured list; the left-alone path only in the note) read
    through `history()`, and both name nothing: the note is never parsed,
    whether it names a journaled path or carries traversal and terminal
    escapes."""
    store = tmp_path / "store"
    root = install_root.resolve()
    pre = SnapshotStore(store).create(
        root,
        [f"{FLAVOR_FOLDER}/{top}" for top in ("WTF", "Interface", "Fonts")],
        label="guard pre-write: format two",
        flavor_folder=FLAVOR_FOLDER,
        flavor_version=flavor.version,
        client_running=False,
    )
    (store / "journal").mkdir(parents=True)
    states = {
        "00000001-0123abcd": ("committed", None),
        "00000002-0123abce": ("rollback_incomplete", note),
    }
    for number, (record_id, (state, text_note)) in enumerate(states.items(), start=1):
        record = {
            "cleanup_finished": True,
            "created_at": f"2026-11-0{number}T12:00:00.000000Z",
            "created_dirs": [],
            "flavor_path": str(root / FLAVOR_FOLDER),
            "flavor_version": flavor.version,
            "format": 2,
            "id": record_id,
            "install_root": str(root),
            "label": state,
            "note": text_note,
            "paths": [
                {"after": sha(NEW_CONFIG), "before": sha(ALLOWLISTED_FILES[CONFIG]), "path": CONFIG}
            ],
            "snapshot_id": pre.id,
            "state": state,
            "temps": [],
            "temps_left": [],
            "temps_removed": [],
        }
        text = json.dumps(record, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
        (store / "journal" / f"{record_id}.json").write_bytes(text.encode("ascii"))

    loaded = {r.label: r for r in guard.history(store=store)}
    assert set(loaded) == {"committed", "rollback_incomplete"}
    assert_left_alone(loaded["committed"], [], flavor)
    assert_left_alone(loaded["rollback_incomplete"], [], flavor)


# ─── a damaged list on disk is a damaged record ──────────────────────────────

WORLD_OUTSIDE = "<world>/outside/x"  # replaced by the absolute path under `world`

# (id, the `paths_left` value written into the record, the state it is given,
# the note it is given: None keeps the gate's note, or clears it for a state
# other than `rollback_incomplete`)
HOSTILE_LISTS: tuple[tuple[str, object, str, str | None], ...] = (
    ("traversal", ["../../etc"], "rollback_incomplete", None),
    ("traversal-into-data", ["../Data/data.001"], "rollback_incomplete", None),
    ("absolute-posix", ["/etc/passwd"], "rollback_incomplete", None),
    ("absolute-under-world-outside", [WORLD_OUTSIDE], "rollback_incomplete", None),
    ("unc", ["//constructed-server/share/x"], "rollback_incomplete", None),
    ("terminal-clear", ["WTF/Config.wtf\x1b[2J"], "rollback_incomplete", None),
    ("terminal-hyperlink", ["WTF/\x1b]8;;http://x\x07Config.wtf"], "rollback_incomplete", None),
    ("bidi-override", ["WTF/\u202eftw.gifnoC"], "rollback_incomplete", None),
    ("not-in-the-record-paths", ["WTF/Other.wtf"], "rollback_incomplete", None),
    ("duplicate", [CONFIG, CONFIG], "rollback_incomplete", None),
    ("on-a-committed-record", [CONFIG], "committed", None),
    ("not-a-string", [1], "rollback_incomplete", None),
    ("a-bare-string", CONFIG, "rollback_incomplete", None),
    ("on-a-rolled-back-record", [CONFIG], "rolled_back", None),
    ("on-an-open-record", [CONFIG], "open", None),
    # The note names the path; only a key of its own may vouch for a path
    # that `paths` does not name, never the note.
    (
        "not-in-paths-but-named-in-the-note",
        ["WTF/Other.wtf"],
        "rollback_incomplete",
        "WTF/Other.wtf: refused",
    ),
)


@pytest.mark.parametrize(
    ("hostile", "state", "note"),
    [pytest.param(v, st, nt, id=f"constructed-{n}") for n, v, st, nt in HOSTILE_LISTS],
)
def test_constructed_a_damaged_left_alone_list_on_disk_is_a_damaged_record(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    hostile: object,
    state: str,
    note: str | None,
) -> None:
    """A real `rollback_incomplete` record (another program changed
    Config.wtf; the icon was put back) is written by the gate with
    `paths_left: [Config.wtf]` in its journal file, and reads back with it:
    that is the positive control. Its list is then replaced on disk (and, for
    some cases, its state or its note): `history()` raises `GuardError` and changes nothing
    in the install or the store."""
    store = tmp_path / "store"

    def body(tx: Any) -> None:
        tx.write(ICON, ICON_NEW)
        tx.write(CONFIG, NEW_CONFIG)
        (flavor.path / CONFIG).write_bytes(EXTERNAL)

    fail_inside(guard, flavor, store, "failing", body)
    record = record_for(guard, SnapshotStore(store), "failing")
    assert record.state == "rollback_incomplete"
    assert {p.path for p in record.paths} == {ICON, CONFIG}
    assert_left_alone(record, [CONFIG], flavor)
    file = store / "journal" / f"{record.id}.json"
    raw = json.loads(file.read_bytes().decode("ascii"))
    assert raw.get(FIELD) == [CONFIG], f"the journal file carries {FIELD!r}: {sorted(raw)}"

    if hostile == [WORLD_OUTSIDE]:
        hostile = [(world / "outside" / "x").as_posix()]
    raw[FIELD] = hostile
    raw["state"] = state
    if state != "rollback_incomplete":
        raw["note"] = None
    if note is not None:
        raw["note"] = note
    text = json.dumps(raw, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
    file.write_bytes(text.encode("ascii"))
    install_before, store_before = G.content(world), G.content(store)

    with pytest.raises(guard.GuardError):
        guard.history(store=store)
    assert G.content(world) == install_before, "reading a damaged record touched the install"
    assert G.content(store) == store_before, "reading a damaged record touched the store"


def test_constructed_a_refused_records_list_vouches_only_for_its_own_refused_path(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None
) -> None:
    """The real record of a refused path that moved again (Config.wtf is
    listed although `paths` does not have to name it) reads back with its
    list: the positive control. Its on-disk list is then rewritten to a
    plain, valid path that is neither journaled nor refused: `history()`
    raises `GuardError` and changes nothing. Whatever lets the gate accept
    Config.wtf there, it does not accept any other path."""
    store = tmp_path / "store"
    config = flavor.path / CONFIG
    with (
        pytest.raises(guard.GuardError),
        guard.transaction(flavor, label="refused", store=store) as tx,
    ):
        tx.write(ICON, ICON_NEW)
        config.write_bytes(EXTERNAL)
        with refused(guard, "ChangedSinceSnapshotError", naming=CONFIG):
            tx.write(CONFIG, NEW_CONFIG)
        config.write_bytes(EXTERNAL_AGAIN)
    record = record_for(guard, SnapshotStore(store), "refused")
    assert record.state == "rollback_incomplete"
    assert_left_alone(record, [CONFIG], flavor, refused=[CONFIG])
    file = store / "journal" / f"{record.id}.json"
    raw = json.loads(file.read_bytes().decode("ascii"))
    assert raw.get(FIELD) == [CONFIG], f"the journal file carries {FIELD!r}: {sorted(raw)}"

    raw[FIELD] = ["WTF/Other.wtf"]
    text = json.dumps(raw, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
    file.write_bytes(text.encode("ascii"))
    install_before, store_before = G.content(world), G.content(store)

    with pytest.raises(guard.GuardError):
        guard.history(store=store)
    assert G.content(world) == install_before, "reading a damaged record touched the install"
    assert G.content(store) == store_before, "reading a damaged record touched the store"
