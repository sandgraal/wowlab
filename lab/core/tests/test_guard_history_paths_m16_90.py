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
  `paths[].path`, `created_dirs`, `temps_removed` and `temps_left`, and the
  journal-only `temps` (the next enter's cleanup reads it from every record
  and copies a refused entry into its own `temps_left`; security review of
  #472, F2). A record that holds a traversal, an absolute path (POSIX, drive
  or one inside the install), a UNC path in either spelling, an empty or `.`
  component, a C0 control character or DEL (a terminal escape included), or
  a plain name the lexical check refuses all the same (`Data/`, `.build.info`,
  an executable, a DOS device name, a trailing dot or space, a case-variant
  allowlist root, a lone surrogate) in any of them is damaged: `history()` raises `GuardError` and nothing in the install or the
  store changes. `paths_left` and `paths_refused` are M16-84T's.
- `flavor_path` is absolute by design and is not checked for that here. It
  must hold no C0, DEL, C1, Cf, Zl or Zp character (owner ruling
  2026-11-11, "2 yes", §6.10 (e); `undo`'s plan header prints it). Its
  absoluteness is graded nowhere in this file.
- The write gate agrees (§6.10 (e)): `transaction()`, and `undo()` of an
  earlier record, on a flavor whose absolute path holds such a character
  raise `GuardError` before any snapshot or journal record, with the install
  and the store unchanged and no new journal file. The path checked is the
  resolved one that would be journaled, so a clean symlink to such a folder
  is refused too, and a dry run refuses as well. The set is C0, DEL, C1,
  `svmerge._FORMAT_CHARACTERS` and lone surrogates (§6.10 (e), be19c9b on
  docs/owner-answer-undo-crash); a lone surrogate is skipped where the
  platform cannot make such a path. The gate refuses the whole set, and
  refuses exactly what `history()` refuses (a narrower gate would let a
  player write a record that then locks them out). A flavor path with
  ordinary non-ASCII letters is still accepted by the gate and by
  `history()`.
- A damaged record is an error, never skipped: a damaged older record under
  a sound newer one still makes `history()` raise.
- Positive controls: the record the gate itself wrote (one new file in two
  new folders, so `created_dirs` is not empty) reads back unchanged before
  each plant, and the same record with well-formed temp names planted in
  `temps_removed` and `temps_left` still reads. A record the gate wrote
  after creating the allowlist roots `Interface` and `Interface/AddOns`
  reads too: those are directories, refused as file paths, so `created_dirs`
  must be checked with `directory=True`.

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
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import Manifest, SnapshotStore, manifest_bytes


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
    assert len(raw["temps"]) == 1, f"the gate journals its one temp path: {raw.get('temps')!r}"
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
    elif field in ("created_dirs", "temps"):
        raw[field] = [*raw[field], value]
    else:
        raw[field] = [value]


def u(code: int) -> str:
    return f"U+{code:04X}"


def assert_history_refuses(guard: Any, store: Path, world: Path, what: str = "") -> None:
    """`history()` raises a plain `GuardError`; `what` names the case in the
    failure (`for U+XXXX`)."""
    install_before, store_before = G.content(world), G.content(store)
    try:
        guard.history(store=store)
    except guard.GuardError as exc:
        assert type(exc).__name__ not in {"GuardBusyError", "ChangedSinceSnapshotError"}, (
            f"a damaged record is a plain GuardError, not {type(exc).__name__}{what}"
        )
    else:
        pytest.fail(f"DID NOT RAISE GuardError{what}")
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
    # Plain, with no traversal and no control character, yet refused by the
    # lexical check all the same (security review of #472, F3): checked on
    # main with `_check_rel` in both modes before they were added here.
    ("data", "Data/x"),
    ("build-info", ".build.info"),
    ("client-executable", "Wow.exe"),
    ("executable-under-wtf", "WTF/x.exe"),
    ("dos-device-name", "WTF/CON"),
    ("trailing-dot", "WTF/x."),
    ("trailing-space", "WTF/x "),
    ("case-variant-wtf", "wtf/Config.wtf"),
    ("case-variant-addons", "Interface/addons/x"),
    ("lone-surrogate", "WTF/\ud800x.lua"),
)

RELATIVE_FIELDS = ("paths", "created_dirs", "temps", "temps_removed", "temps_left")


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
@pytest.mark.parametrize("field", RELATIVE_FIELDS)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(
            v,
            id=f"constructed-{n}",
            # Windows file names may hold a lone surrogate, and the lexical
            # check accepts one there; on POSIX it is not a name.
            marks=[pytest.mark.skipif(sys.platform == "win32", reason="a legal Windows name")]
            if any(0xD800 <= ord(c) <= 0xDFFF for c in v)
            else [],
        )
        for n, v in HOSTILE_RELATIVE
    ],
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
    # The owner's ruling of 2026-11-11 ("2 yes", \u00a76.10 (e)): every C0, DEL,
    # C1, Cf, Zl and Zp character, the bidi marks included.
    ("folder-lrm", "folder", "\u200e"),
    ("folder-rlm", "folder", "\u200f"),
    ("folder-arabic-letter-mark", "folder", "\u061c"),
    ("folder-lre", "folder", "\u202a"),
    ("folder-pdf", "folder", "\u202c"),
    ("folder-lro", "folder", "\u202d"),
    ("folder-lri", "folder", "\u2066"),
    ("folder-fsi", "folder", "\u2068"),
    ("folder-pdi", "folder", "\u2069"),
    ("folder-line-separator", "folder", "\u2028"),
    ("folder-paragraph-separator", "folder", "\u2029"),
    ("folder-zero-width-space", "folder", "\u200b"),
    ("folder-bom", "folder", "\ufeff"),
    ("folder-soft-hyphen", "folder", "\u00ad"),
    ("folder-invisible-plus-u2065", "folder", "\u2065"),
    ("folder-tag-character", "folder", "\U000e0041"),
    ("folder-lone-low-surrogate", "folder", "\udc80"),
    ("folder-lone-high-surrogate", "folder", "\ud800"),
    ("install-terminal-clear", "install", "\x1b[2J"),
    ("install-bidi-override", "install", "\u202e"),
    ("install-lone-surrogate", "install", "\udcff"),
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


def forbidden_flavor_characters() -> list[int]:
    """The whole set §6.10 (e) names: C0, DEL, C1, the pinned table
    `svmerge._FORMAT_CHARACTERS`, and lone surrogates (both ends of each
    half, and the bytes a POSIX name that is not UTF-8 decodes to)."""
    from wowlab_core import svmerge

    codes = [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
    codes += [c for lo, hi in svmerge._FORMAT_CHARACTERS for c in range(lo, hi + 1)]
    codes += [0xD800, 0xDBFF, 0xDC00, 0xDC80, 0xDCFF, 0xDFFF]
    assert 0x2065 in codes and 0x202E in codes and 0x2028 in codes, "the pinned table"
    return codes


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
def test_constructed_every_forbidden_character_in_a_flavor_path_is_damaged(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None
) -> None:
    """Every character of the set, one at a time, in the flavor folder's
    name: each makes the record damaged. The gate's record is restored byte
    for byte between characters."""
    store, file, raw = gate_record(guard, tmp_path, flavor)
    original = file.read_bytes()
    for code in forbidden_flavor_characters():
        raw["flavor_path"] = str(flavor.path.parent / f"{flavor.path.name}{chr(code)}")
        write_record(file, raw)
        assert_history_refuses(guard, store, world, f" for {u(code)}")
        file.write_bytes(original)


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


def test_constructed_created_allowlist_roots_still_read(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None
) -> None:
    """Control: with `Interface/` missing, the gate creates `Interface` and
    `Interface/AddOns` (allowlist roots, which the lexical check refuses as
    file paths) and journals them in `created_dirs`; the record reads back
    with them. An implementation that checks `created_dirs` as files fails
    here."""
    store = tmp_path / "store"
    shutil.rmtree(flavor.path / "Interface")
    with guard.transaction(flavor, label="roots", store=store) as tx:
        tx.write("Interface/AddOns/NewThing/NewThing.toc", b"## Interface: 1\n")
    install_before = G.content(world)
    record = record_for(guard, SnapshotStore(store), "roots")
    assert record.state == "committed"
    assert tuple(record.created_dirs) == (
        "Interface",
        "Interface/AddOns",
        "Interface/AddOns/NewThing",
    )
    assert G.content(world) == install_before


# ─── the gate refuses such a flavor (owner ruling 2026-11-11, §6.10 (e)) ─────

# (id, the character). A C0 character cannot be in a Windows file name, so
# that case is POSIX only; the others are legal names on every platform.
GATE_HOSTILE: tuple[tuple[str, str], ...] = (
    ("c0-escape", "\x1b"),
    ("c1-csi", "\x9b"),
    ("bidi-override", "\u202e"),
    ("rlm", "\u200f"),
    ("line-separator", "\u2028"),
    # What a POSIX file name with a byte that is not UTF-8 decodes to.
    ("lone-surrogate", "\udc80"),
)
GATE_PARAMS = [
    pytest.param(
        c,
        id=f"constructed-{n}",
        marks=[pytest.mark.skipif(sys.platform == "win32", reason="no C0 in a Windows name")]
        if c < " "
        else [],
    )
    for n, c in GATE_HOSTILE
]


def moved_install(world: Path, flavor: Flavor, parent_name: str) -> tuple[Path, Flavor]:
    """Move the synthetic install (with the folder beside it) under a new
    folder named `parent_name`; return the new world and the same flavor
    there. The flavor's absolute path then holds `parent_name`."""
    parent = world.parent / parent_name
    try:
        parent.mkdir()
    except (OSError, UnicodeError) as exc:
        if not any(0xD800 <= ord(c) <= 0xDFFF for c in parent_name):
            raise
        pytest.skip(f"this platform cannot make a path holding a lone surrogate: {exc!r}")
    moved = parent / world.name
    world.rename(moved)
    new_path = moved / flavor.path.relative_to(world)
    return moved, flavor.model_copy(update={"path": new_path})


def journal_files(store: Path) -> set[str]:
    return {p.name for p in (store / "journal").iterdir()}


def assert_transaction_refused(
    guard: Any, odd: Flavor, moved: Path, store: Path, *, dry_run: bool
) -> None:
    install_before, store_before = G.content(moved), G.content(store)
    journal_before = journal_files(store)
    with (
        pytest.raises(guard.GuardError) as raised,
        guard.transaction(odd, label="refused", store=store, dry_run=dry_run) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
    assert type(raised.value).__name__ not in {"GuardBusyError", "ChangedSinceSnapshotError"}
    assert G.content(moved) == install_before, "the refused transaction touched the install"
    assert G.content(store) == store_before, "the refused transaction touched the store"
    assert journal_files(store) == journal_before, "the refused transaction journaled"


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
@pytest.mark.parametrize(
    "dry_run",
    [pytest.param(False, id="constructed-write"), pytest.param(True, id="constructed-dry-run")],
)
@pytest.mark.parametrize("char", GATE_PARAMS)
def test_constructed_transaction_refuses_a_flavor_path_with_a_forbidden_character(
    guard: Any,
    tmp_path: Path,
    world: Path,
    flavor: Flavor,
    idle: None,
    char: str,
    dry_run: bool,
) -> None:
    """A store with one sound record (of the install at its plain path); the
    install is then moved under a folder whose name holds `char`. A
    transaction on it, a dry run included, raises `GuardError` before any
    snapshot or journal record: install and store unchanged, no new journal
    file. The earlier record is left as the gate wrote it, so it cannot be
    the reason."""
    store, _, _ = gate_record(guard, tmp_path, flavor)
    moved, odd = moved_install(world, flavor, f"odd{char}folder")
    assert char in str(odd.path) and odd.path.is_dir()
    assert_transaction_refused(guard, odd, moved, store, dry_run=dry_run)


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
@pytest.mark.parametrize(
    "dry_run",
    [pytest.param(False, id="constructed-write"), pytest.param(True, id="constructed-dry-run")],
)
def test_constructed_transaction_refuses_a_clean_symlink_to_a_forbidden_path(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None, dry_run: bool
) -> None:
    """What is checked is the resolved flavor path that would be journaled,
    install root included, never the caller's spelling (§6.10 (e)): the
    flavor is reached through `clean`, a symlink whose target folder's name
    holds U+202E. The spelling the caller passes is clean; the transaction
    is refused all the same, with nothing journaled and the store
    unchanged."""
    store, _, _ = gate_record(guard, tmp_path, flavor)
    moved, odd = moved_install(world, flavor, "odd\u202efolder")
    clean = tmp_path / "clean"
    G.symlink_or_skip(clean, moved.parent, is_dir=True)
    via_link = flavor.model_copy(update={"path": clean / moved.name / odd.path.relative_to(moved)})
    assert "\u202e" not in str(via_link.path) and via_link.path.is_dir()
    assert "\u202e" in str(via_link.path.resolve())
    assert_transaction_refused(guard, via_link, moved, store, dry_run=dry_run)


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
@pytest.mark.parametrize("char", GATE_PARAMS)
def test_constructed_undo_refuses_a_flavor_path_with_a_forbidden_character(
    guard: Any,
    tmp_path: Path,
    world: Path,
    install_root: Path,
    flavor: Flavor,
    idle: None,
    char: str,
) -> None:
    """A record the gate wrote; the install is then moved under a folder
    whose name holds `char`, and the record's `flavor_path` and
    `install_root` and its pre-write snapshot's `install_root` follow it
    (what a gate without the check would have written there; with the plain
    path left in the manifest, `undo()` on main refuses for that mismatch
    instead). `undo()` raises `GuardError` before any snapshot or journal
    record: install and store unchanged, no new journal file."""
    store, file, raw = gate_record(guard, tmp_path, flavor)
    moved, odd = moved_install(world, flavor, f"odd{char}folder")
    for key in ("flavor_path", "install_root"):
        assert raw[key].startswith(str(world)), (key, raw[key])
        raw[key] = str(moved) + raw[key][len(str(world)) :]
    assert raw["flavor_path"] == str(odd.path)
    write_record(file, raw)
    manifest = SnapshotStore(store).manifests_dir / f"{raw['snapshot_id']}.json"
    loaded = Manifest.model_validate_json(manifest.read_bytes())
    assert loaded.install_root == str(world / install_root.name), loaded.install_root
    moved_root = str(moved / install_root.name)
    # The store's own canonical encoding, which carries a lone surrogate too.
    manifest.write_bytes(manifest_bytes(loaded.model_copy(update={"install_root": moved_root})))
    assert SnapshotStore(store).show(raw["snapshot_id"]).install_root == moved_root
    install_before, store_before = G.content(moved), G.content(store)
    journal_before = journal_files(store)
    with pytest.raises(guard.GuardError) as raised:
        guard.undo(store=store)
    assert type(raised.value).__name__ not in {"GuardBusyError", "ChangedSinceSnapshotError"}
    assert G.content(moved) == install_before, "the refused undo touched the install"
    assert G.content(store) == store_before, "the refused undo touched the store"
    assert journal_files(store) == journal_before, "the refused undo journaled"


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("W\u00f6rld", id="constructed-latin-umlaut"),
        pytest.param("\u9b54\u517d\u4e16\u754c", id="constructed-cjk"),
    ],
)
def test_constructed_a_flavor_path_with_ordinary_non_ascii_letters_is_accepted(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None, name: str
) -> None:
    """Control: an install under a folder named with ordinary non-ASCII
    letters is written by the gate, and its record reads back through
    `history()` with that `flavor_path`."""
    _, odd = moved_install(world, flavor, name)
    store = tmp_path / "store"
    with guard.transaction(odd, label="letters", store=store) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    assert (odd.path / CONFIG).read_bytes() == NEW_CONFIG
    record = record_for(guard, SnapshotStore(store), "letters")
    assert record.state == "committed"
    assert Path(record.flavor_path) == odd.path
    assert name in record.flavor_path


# ─── the gate refuses exactly the set history() refuses ──────────────────────

# Characters outside the set: a flavor path may hold them, and the gate and
# `history()` both accept them (letters, spaces, marks, a hyphen, an emoji).
ALLOWED_NEIGHBOURS = (
    0x41,
    0x20,
    0xA0,
    0xE9,
    0x300,
    0x200A,
    0x2010,
    0x2027,
    0x202F,
    0x2030,
    0x2070,
    0xFE0F,
    0x9B54,
    0x1F600,
)


def gate_refuses(guard: Any, world: Path, flavor: Flavor, store: Path, code: int) -> bool | None:
    """Move the install under `odd<char>folder`, run a dry-run transaction on
    it, move it back. True if the gate raised a plain `GuardError` (and
    journaled nothing, store unchanged), False if it ran, None if the file
    system cannot make the folder."""
    parent = world.parent / f"odd{chr(code)}folder"
    try:
        parent.mkdir()
    except (OSError, UnicodeError, ValueError):
        return None
    moved = parent / world.name
    world.rename(moved)
    try:
        odd = flavor.model_copy(update={"path": moved / flavor.path.relative_to(world)})
        store_before, journal_before = G.content(store), journal_files(store)
        try:
            with guard.transaction(odd, label="probe", store=store, dry_run=True) as tx:
                tx.write(CONFIG, NEW_CONFIG)
        except guard.GuardError as exc:
            name = type(exc).__name__
            assert name not in {"GuardBusyError", "ChangedSinceSnapshotError"}, f"{name} {u(code)}"
            assert journal_files(store) == journal_before, f"journaled for {u(code)}"
            assert G.content(store) == store_before, f"store changed for {u(code)}"
            return True
        return False
    finally:
        moved.rename(world)
        parent.rmdir()


def history_refuses(guard: Any, file: Path, raw: dict[str, Any], store: Path, code: int) -> bool:
    """Plant `flavor_path` with the character in the folder's name, read
    `history()`, restore the record. True if it raised `GuardError`."""
    original = file.read_bytes()
    planted = dict(raw, flavor_path=str(Path(raw["flavor_path"] + chr(code))))
    write_record(file, planted)
    try:
        guard.history(store=store)
    except guard.GuardError:
        return True
    finally:
        file.write_bytes(original)
    return False


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
def test_constructed_the_gate_refuses_every_forbidden_character_in_a_flavor_path(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None
) -> None:
    """Every character `history()` refuses in a `flavor_path` (the whole
    §6.10 (e) set), one at a time in a folder above the install: a dry-run
    transaction raises `GuardError` and journals nothing. A narrower gate
    check would let a player write a record that then locks them out. NUL
    and any character the file system cannot hold in a name are skipped."""
    store, _, _ = gate_record(guard, tmp_path, flavor)
    tried = 0
    for code in forbidden_flavor_characters():
        if code == 0:
            continue
        refused = gate_refuses(guard, world, flavor, store, code)
        if refused is None:
            continue
        tried += 1
        if not refused:
            pytest.fail(f"DID NOT RAISE GuardError for {u(code)}")
    assert tried > 100, f"the file system held only {tried} of the characters"


@pytest.mark.xfail(strict=True, reason="M16-90 not implemented")
def test_constructed_the_gate_and_history_refuse_the_same_characters(
    guard: Any, tmp_path: Path, world: Path, flavor: Flavor, idle: None
) -> None:
    """Symmetry: for each character of the set, and for ordinary neighbours
    outside it, the gate refuses the flavor exactly when `history()` refuses
    a record naming it, and both refuse exactly the set. The set comes
    first, so on a gate without the check the first failure is a refusal
    that did not happen."""
    forbidden = forbidden_flavor_characters()
    assert not set(ALLOWED_NEIGHBOURS) & set(forbidden), "a neighbour is in the set"
    store, file, raw = gate_record(guard, tmp_path, flavor)
    for code in [*forbidden, *ALLOWED_NEIGHBOURS]:
        if code == 0:
            continue
        gate = gate_refuses(guard, world, flavor, store, code)
        if gate is None:
            continue
        history = history_refuses(guard, file, raw, store, code)
        expected = code in forbidden
        if expected and not gate:
            pytest.fail(f"DID NOT RAISE GuardError for {u(code)} (the gate)")
        if expected and not history:
            pytest.fail(f"DID NOT RAISE GuardError for {u(code)} (history)")
        assert gate == history == expected, (
            f"{u(code)}: gate refuses {gate}, history refuses {history}, in the set {expected}"
        )
