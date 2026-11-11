"""Graders for M16-90: `history()` refuses a journal record whose paths are
not plain flavor-relative names (M16-90T).

Written before M16-90, from the ticket in docs/BACKLOG.md (filed 2026-11-10
from the M16-84T security review, #453, and the #455 review), docs/LAB_PLAN.md
§6.10 (the journal; the 2026-09-22 amendment: "a journal that cannot be read
in full (... any record `history()` would refuse as damaged) raises
`GuardError`"; the M16-84 amendment (b): each journaled path "must pass the
allowlist's lexical check ... Anything else is a damaged record: `history()`
raises `GuardError`") and ADR-0021. Every grader carries one marker line
that M16-90 deletes; nothing else in this file is the implementer's to
change.

What is graded
--------------
- Every flavor-relative path field the public record exposes, read back from
  a journal file on disk, must pass the same lexical check the write gate
  applies to a path (`_check_rel`; `directory=True` for `created_dirs`):
  `paths[].path`, `created_dirs`, `temps_removed` and `temps_left`. A record
  that holds a traversal, an absolute path (POSIX, drive or one inside the
  install), a UNC path in either spelling, an empty or `.` component, or a
  C0 control character or DEL (a terminal escape included) in any of them is
  damaged: `history()` raises `GuardError` and nothing in the install or the
  store changes. `paths_left` and `paths_refused` are M16-84T's.
- `flavor_path` is absolute by design and is not checked for that here. It
  must hold no control character (C0, DEL or C1) and no bidi control
  character, since `undo`'s plan header prints it. Its absoluteness is
  graded nowhere in this file.
- A damaged record is an error, never skipped: a damaged older record under
  a sound newer one still makes `history()` raise.
- Positive controls: the record the gate itself wrote (one new file in two
  new folders, so `created_dirs` is not empty) reads back unchanged before
  each plant, and the same record with well-formed temp names planted in
  `temps_removed` and `temps_left` still reads.

Not graded (see the M16-90T report): bidi and C1 characters in the
flavor-relative fields. The write gate on main commits a file named
`WTF/\\u202eftw.lua` (and `\\u2067`, `\\x9b`) and journals it, `_check_rel`
accepts those names, and §6.10 (M16-84 (d)) says "a real file name may hold
a bidi mark". Refusing them in `history()` would make the gate's own record
damaged and, by the 2026-09-22 amendment, refuse every later transaction.

Everything is constructed: the synthetic install, fixtures and helpers are
`test_guard.py`'s (through `test_guard_owner_decisions.py`, as
`test_guard_left_alone_m16_84.py` loads them); records are planted by
rewriting a journal file the gate wrote, as M16-84T does.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
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


OD = _load("_m16_90_owner_graders", "test_guard_owner_decisions.py")
G = OD.G

_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
idle = G.idle

Flavor = G.Flavor
CONFIG = G.CONFIG
NEW_CONFIG = G.NEW_CONFIG
NEW_LUA = G.NEW_LUA
record_for = G.record_for

LABEL = "planted"
NEW_FILE = "WTF/Account/NEWACCT/SavedVariables/New.lua"
NEW_DIRS = ("WTF/Account/NEWACCT", "WTF/Account/NEWACCT/SavedVariables")
TEMP_A = "WTF/Account/NEWACCT/SavedVariables/.wowlab-0123456789abcdef0123456789abcdef.tmp"
TEMP_B = "WTF/.wowlab-fedcba9876543210fedcba9876543210.tmp"

WORLD_OUTSIDE = "<world>/outside/x"  # expanded to an absolute path under `world`
FLAVOR_INSIDE = "<flavor>/WTF/Config.wtf"  # expanded to an absolute path in the flavor


# ─── helpers ─────────────────────────────────────────────────────────────────


def gate_record(guard: Any, tmp_path: Path, flavor: Flavor) -> tuple[Path, Path, dict[str, Any]]:
    """A committed record the gate writes itself: one new file in two new
    folders. It reads back through `history()` with exactly what the gate
    did (the positive control every plant starts from). Returns the store,
    the record's journal file and its parsed JSON."""
    store = tmp_path / "store"
    with guard.transaction(flavor, label=LABEL, store=store) as tx:
        tx.write(NEW_FILE, NEW_LUA)
    record = record_for(guard, SnapshotStore(store), LABEL)
    assert record.state == "committed"
    assert [p.path for p in record.paths] == [NEW_FILE]
    assert tuple(record.created_dirs) == NEW_DIRS
    assert tuple(record.temps_removed) == () and tuple(record.temps_left) == ()
    assert Path(record.flavor_path) == flavor.path
    file = store / "journal" / f"{record.id}.json"
    raw = json.loads(file.read_bytes().decode("ascii"))
    assert raw["paths"][0]["path"] == NEW_FILE, sorted(raw)
    assert raw["created_dirs"] == list(NEW_DIRS), sorted(raw)
    assert raw["flavor_path"] == str(flavor.path), sorted(raw)
    assert raw["temps_removed"] == [] and raw["temps_left"] == [], sorted(raw)
    return store, file, raw


def write_record(file: Path, raw: dict[str, Any]) -> None:
    """The gate's own layout: ASCII JSON, sorted keys, one-space indent."""
    text = json.dumps(raw, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
    file.write_bytes(text.encode("ascii"))


def expand(value: str, world: Path, flavor: Flavor) -> str:
    if value == WORLD_OUTSIDE:
        return (world / "outside" / "x").as_posix()
    if value == FLAVOR_INSIDE:
        return (flavor.path / CONFIG).as_posix()
    return value


def plant(raw: dict[str, Any], field: str, value: str) -> None:
    if field == "paths":
        raw["paths"][0]["path"] = value
    elif field == "created_dirs":
        raw["created_dirs"] = [*raw["created_dirs"], value]
    else:
        raw[field] = [value]


def assert_history_refuses(guard: Any, store: Path, world: Path) -> None:
    install_before, store_before = G.content(world), G.content(store)
    with pytest.raises(guard.GuardError) as raised:
        guard.history(store=store)
    assert type(raised.value).__name__ not in {"GuardBusyError", "ChangedSinceSnapshotError"}, (
        f"a damaged record is a plain GuardError, not {type(raised.value).__name__}"
    )
    assert G.content(world) == install_before, "reading a damaged record touched the install"
    assert G.content(store) == store_before, "reading a damaged record touched the store"


# ─── flavor-relative fields: every value the lexical check refuses ───────────

# (id, the value planted). Each fails the write gate's lexical check: the
# gate refuses it as a path to write, so no record the gate writes holds it.
HOSTILE_RELATIVE: tuple[tuple[str, str], ...] = (
    ("traversal", "../../etc/passwd"),
    ("traversal-from-inside", "WTF/../../.build.info"),
    ("traversal-then-escape", "../../etc/passwd\x1b[2J"),  # the ticket's probe
    ("parent-only", ".."),
    ("dot-component", "WTF/./Config.wtf"),
    ("empty-component", "WTF//Config.wtf"),
    ("empty", ""),
    ("absolute-posix", "/etc/passwd"),
    ("absolute-outside-the-install", WORLD_OUTSIDE),
    ("absolute-inside-the-flavor", FLAVOR_INSIDE),
    ("drive-absolute", "C:/Windows/win.ini"),
    ("unc-slash", "//constructed-server/share/x"),
    ("unc-backslash", "\\\\constructed-server\\share\\x"),
    ("backslash-traversal", "WTF\\..\\..\\.build.info"),
    ("terminal-clear", "WTF/Config.wtf\x1b[2J"),
    ("terminal-hyperlink", "WTF/\x1b]8;;http://x\x07Config.wtf"),
    ("newline", "WTF/Config.wtf\nWTF/Other.wtf"),
    ("carriage-return", "WTF/Other.wtf\rWTF/Config.wtf"),
    ("tab", "WTF/Con\tfig.wtf"),
    ("nul", "WTF/Con\x00fig.wtf"),
    ("del", "WTF/Config\x7f.wtf"),
)

RELATIVE_FIELDS = ("paths", "created_dirs", "temps_removed", "temps_left")


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
@pytest.mark.parametrize("field", RELATIVE_FIELDS)
@pytest.mark.parametrize(
    "value", [pytest.param(v, id=f"constructed-{n}") for n, v in HOSTILE_RELATIVE]
)
def test_constructed_a_record_whose_relative_path_is_not_plain_is_damaged(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    field: str,
    value: str,
) -> None:
    """The gate's record reads back (control); with one hostile value planted
    in `field` it is damaged: `history()` raises `GuardError` and changes
    nothing in the install or the store."""
    store, file, raw = gate_record(guard, tmp_path, flavor)
    plant(raw, field, expand(value, world, flavor))
    write_record(file, raw)
    assert_history_refuses(guard, store, world)


# ─── flavor_path: absolute by design, but no control or bidi character ───────

# (id, where the character goes, the character)
HOSTILE_FLAVOR_PATH: tuple[tuple[str, str, str], ...] = (
    ("folder-terminal-clear", "folder", "\x1b[2J"),
    ("folder-terminal-hyperlink", "folder", "\x1b]8;;http://x\x07"),
    ("folder-newline", "folder", "\n"),
    ("folder-carriage-return", "folder", "\r"),
    ("folder-nul", "folder", "\x00"),
    ("folder-del", "folder", "\x7f"),
    ("folder-c1-csi", "folder", "\x9b2J"),
    ("folder-c1-next-line", "folder", "\x85"),
    ("folder-bidi-override", "folder", "\u202e"),
    ("folder-bidi-isolate", "folder", "\u2067"),
    ("folder-bidi-embedding", "folder", "\u202b"),
    ("install-terminal-clear", "install", "\x1b[2J"),
    ("install-bidi-override", "install", "\u202e"),
)


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
@pytest.mark.parametrize(
    ("where", "char"),
    [pytest.param(w, c, id=f"constructed-{n}") for n, w, c in HOSTILE_FLAVOR_PATH],
)
def test_constructed_a_flavor_path_with_a_control_or_bidi_character_is_damaged(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    where: str,
    char: str,
) -> None:
    """The gate's record reads back with its absolute `flavor_path`
    (control). With a control or bidi character in the flavor folder's name,
    or in a folder above it, the record is damaged: `history()` raises
    `GuardError` and changes nothing. The path stays absolute, so only the
    character can be the reason."""
    store, file, raw = gate_record(guard, tmp_path, flavor)
    if where == "folder":
        hostile = flavor.path.parent / f"{flavor.path.name}{char}"
    else:
        hostile = flavor.path.parent.parent / f"{flavor.path.parent.name}{char}" / flavor.path.name
    assert hostile.is_absolute()
    raw["flavor_path"] = str(hostile)
    write_record(file, raw)
    assert_history_refuses(guard, store, world)


# ─── a damaged record is never skipped ───────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
@pytest.mark.parametrize("field", RELATIVE_FIELDS)
def test_constructed_a_damaged_older_record_is_not_skipped(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None, field: str
) -> None:
    """A damaged older record under a sound newer one: `history()` raises
    rather than listing the newer one alone."""
    store, file, raw = gate_record(guard, tmp_path, flavor)
    with guard.transaction(flavor, label="newer", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert [r.label for r in guard.history(store=store)] == [LABEL, "newer"]
    plant(raw, field, "../../etc/passwd\x1b[2J")
    write_record(file, raw)
    assert_history_refuses(guard, store, world)


# ─── positive controls ───────────────────────────────────────────────────────


def test_constructed_the_gates_own_record_still_reads(
    guard: Any, tmp_path: Path, flavor: Flavor, idle: None
) -> None:
    """Control: a committed record with a new file and two created folders
    reads back with exactly those, whatever M16-90 checks."""
    gate_record(guard, tmp_path, flavor)


def test_constructed_well_formed_temp_names_still_read(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None
) -> None:
    """Control: temp names of guard's own pattern, flavor-relative and
    plain, planted in `temps_removed` and `temps_left`, read back as written,
    and so do the record's own `paths`, `created_dirs` and `flavor_path`."""
    store, file, raw = gate_record(guard, tmp_path, flavor)
    raw["temps_removed"] = [TEMP_A]
    raw["temps_left"] = [TEMP_B]
    write_record(file, raw)
    install_before = G.content(world)
    (record,) = guard.history(store=store)
    assert tuple(record.temps_removed) == (TEMP_A,)
    assert tuple(record.temps_left) == (TEMP_B,)
    assert [p.path for p in record.paths] == [NEW_FILE]
    assert tuple(record.created_dirs) == NEW_DIRS
    assert Path(record.flavor_path) == flavor.path
    assert G.content(world) == install_before
