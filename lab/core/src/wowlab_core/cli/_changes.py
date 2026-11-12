"""Changes by name (M16-10): the plan and diff lines that `snap diff`,
`snap restore`, `undo` and `profile apply` print, and what a restore leaves
alone (M16-111)."""

import hashlib
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field

from wowlab_core import (
    guard,
    layout,
    longpath,
    luadata,
    profiles,
    snapshot,
    wtfdiff,
)
from wowlab_core.cli._cmd_filemap import (
    _plain,
)
from wowlab_core.cli._cmd_sv import (
    _flatten,
)
from wowlab_core.cli._common import (
    _bytes,
    _Out,
)
from wowlab_core.filemap import FileMapEntry
from wowlab_core.verdict import (
    gate_names_but_for_executable,
    has_executable_part,
)


def _is_saved_variables(path: str) -> bool:
    parts = [p.casefold() for p in path.split("/")]
    if not parts[-1].endswith(".lua") or "wtf" not in parts[:-1]:
        return False
    return "savedvariables" in parts[:-1] or parts[-1] == "savedvariables.lua"


def _leaves(data: bytes) -> dict[str, str]:
    doc = luadata.parse(data)
    out: dict[str, str] = {}
    for a in doc.assignments:
        for p, v in _flatten(a.name, a.value):
            out[p] = v
    return out


# The login caveat leads with the files the plan lists (M16-103). Whether
# the client reads Config.wtf again at login, or only when it starts, is
# unconfirmed, so the note ties it to the next start. The file map lists no
# server replacement for Config.wtf (its row and the Timing rules; it holds
# machine-scope settings), so its note names no server. The three sync CVars
# `doctor` shows cover binds, macros and settings; which other *-cache files
# the server replaces, and under which setting, is unconfirmed
# (docs/LAB_FILE_MAP.md, its server sync note), so the note says so (M16-97).
_RELOGIN = (
    "The client reads the *-cache files at login. The server may replace binds, macros and "
    "account or character settings at login when their sync setting is on (see wowlab "
    "doctor); it may also replace other *-cache files then, and under which setting is not "
    "yet known."
)


_RESTART = "The next time you start the client, it reads Config.wtf again."


_RESTART_RELOGIN = (
    "The next time you start the client, it reads Config.wtf again; it reads the other "
    "files at login. The server may replace binds, macros and account or character "
    "settings at login when their sync setting is on (see wowlab doctor); it may also "
    "replace other *-cache files then, and under which setting is not yet known."
)


def _relogin_notes(paths: Sequence[str]) -> list[str]:
    """The login caveat, when a change touches Config.wtf or a *-cache file
    under `WTF/` (paths relative to the flavor folder), in the words of the
    files it touches: `_RELOGIN` for *-cache files only, `_RESTART` for
    Config.wtf only, `_RESTART_RELOGIN` for both (M16-103). A file in a
    SavedVariables folder, or outside `WTF/` (an addon's own file), is
    neither, whatever its name (M16-97)."""
    config, cache_file = profiles.login_files(paths)
    if config and cache_file:
        return [_RESTART_RELOGIN]
    if config:
        return [_RESTART]
    if cache_file:
        return [_RELOGIN]
    return []


# ─── changes by name (M16-10) ────────────────────────────────────────────────
#
# Under a changed bindings, macros or CVar file, `snap diff` and the plans
# of `snap restore`, `profile apply` and `undo` say what changes by name;
# under a changed SavedVariables file, how many values go and the size.
# Both sides are read read-only (the snapshot store, or the install through
# the guarded opener) and parsed as data by the existing parsers.

_DETAIL_INDENT = "      "
_DETAIL_PLAN_LIMIT = 20  # named lines per file in a plan; `snap diff` lists every one
_DETAIL_READ_LIMIT = 64 * 1024 * 1024  # a side larger than this is not compared
_VALUE_WIDTH = 60


class _Unread:
    """A side that holds bytes wowlab could not read back (missing from the
    store, changed on disk since the plan's hash, or over the size limit).
    `why`, when set, is said in place of the general words: a file on disk
    reached through a linked folder, the folder named (M16-65)."""

    def __init__(self, why: str | None = None) -> None:
        self.why = why


_UNREAD = _Unread()
type _Side = bytes | _Unread | None


_NO_KEY_LINE = "(no line: the client's default binding)"
_NO_CVAR_LINE = "(no line: set elsewhere or the client default)"
_SCOPE_LINE = (
    "  Each file is one scope (machine, account or character): key bindings come from "
    'the account or the character file, as "Character Specific Key Bindings" sets; '
    "account and character macros are two separate sets; each CVar is read from the "
    "file of its own scope."
)


def _plan_footer(named: bool, paths: Sequence[str]) -> list[str]:
    """The lines a plan ends with before the prompt: the scope line when it
    named a key, macro or CVar change (`named`: a bindings, macros or CVar
    file got detail lines), and the login caveat whenever a listed path is
    one the caveat is about, with or without detail lines under it (a file
    wowlab cannot read, a delete or a recreate; M16-97). A
    SavedVariables-only plan gets neither."""
    scope = [_SCOPE_LINE] if named else []
    return [*scope, *_relogin_notes(paths)]


def _value_text(value: str | None, *, cvar: bool) -> str:
    if value is None:
        return _NO_CVAR_LINE if cvar else _NO_KEY_LINE
    if len(value) > _VALUE_WIDTH:
        value = value[: _VALUE_WIDTH - 3] + "..."
    return f'"{value}"' if cvar else value


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _macro_label(change: wtfdiff.NamedChange) -> str:
    """A macro's name as printed: quoted, `(2)` for the second of a name; a
    blank or whitespace-only name by its place among the file's macros."""
    if not change.name.strip():
        return f"(blank name, {_ordinal(change.position)})"
    label = f'"{change.name}"'
    return label if change.occurrence <= 1 else f"{label} ({change.occurrence})"


def _named_line(change: wtfdiff.NamedChange) -> str:
    if change.what == "macro":
        if change.before is None:
            how = "added"
        elif change.after is None:
            how = "removed"
        else:
            how = " and ".join(change.parts) + " changed"
        return f"{_DETAIL_INDENT}macro {_macro_label(change)}: {how}"
    label = "key" if change.what == "key" else "CVar"
    cvar = change.what == "cvar"
    return (
        f"{_DETAIL_INDENT}{label} {change.name}: {_value_text(change.before, cvar=cvar)}"
        f" -> {_value_text(change.after, cvar=cvar)}"
    )


# What a line `wtfdiff` does not read by name is, in each kind of file: the
# lines its parser keeps as unknown, said by their form, not their effect (the
# client may still read a CVar line typed by hand; M16-98).
_OTHER_LINES: dict[wtfdiff.Kind, str] = {
    "bindings": "for example a binding mode or modifier line",
    "macros": "lines outside any macro wowlab can read",
    "cvars": "lines not in the game's usual setting form, such as a setting typed in by hand",
}


def _wtf_lines(
    kind: wtfdiff.Kind, before: bytes | None, after: bytes | None, limit: int | None
) -> list[str]:
    try:
        diff = wtfdiff.compare(kind, before, after)
    except ValueError as exc:  # a parser invariant; never expected on real bytes
        return [f"{_DETAIL_INDENT}(not compared by name: {exc})"]
    lines = [_named_line(c) for c in diff.changes]
    if limit is not None and len(lines) > limit:
        more = len(lines) - limit
        lines = [*lines[:limit], f"{_DETAIL_INDENT}... and {more} more (snap diff lists them all)"]
    if diff.repeated_names:
        lines.append(
            f"{_DETAIL_INDENT}macros with the same name are compared in file order; an "
            "inserted or removed one shifts the rest"
        )
    if diff.other_lines_differ:
        lines.append(
            f"{_DETAIL_INDENT}other lines differ too ({_OTHER_LINES[kind]}), which wowlab "
            "does not read by name"
        )
    elif not diff.changes and before is not None and after is not None:
        if kind == "macros":
            lines.append(
                f"{_DETAIL_INDENT}nothing changes by name; the macros' order or ids differ, "
                "and a button that holds a macro may then run a different one"
            )
        else:
            lines.append(
                f"{_DETAIL_INDENT}nothing changes by name (the order, line endings, spelling "
                "or a line an earlier one overrides differ)"
            )
    return lines


def _size_text(data: bytes | None) -> str:
    return "none" if data is None else _bytes(len(data))


def _sv_summary(
    removed: int | None, added: int, changed: int, before_size: str, after_size: str
) -> str:
    """The one summary line under a SavedVariables file; `removed` is None
    when the values were not compared (a side does not parse)."""
    size = f"size {before_size} -> {after_size}"
    if removed is None:
        return f"{_DETAIL_INDENT}values not compared; {size}"
    return f"{_DETAIL_INDENT}values: {removed} removed, {added} added, {changed} changed; {size}"


def _sv_lines(before: bytes | None, after: bytes | None) -> list[str]:
    try:
        old = _leaves(before) if before is not None else {}
        new = _leaves(after) if after is not None else {}
    except luadata.LuaDataError:
        return [_sv_summary(None, 0, 0, _size_text(before), _size_text(after))]
    removed = sum(1 for p in old if p not in new)
    added = sum(1 for p in new if p not in old)
    changed = sum(1 for p, v in new.items() if p in old and old[p] != v)
    return [_sv_summary(removed, added, changed, _size_text(before), _size_text(after))]


def _detail_lines(path: str, before: _Side, after: _Side, *, limit: int | None) -> list[str]:
    """The lines under one changed file: by name for a bindings, macros or
    CVar file, the summary for a SavedVariables file, else none."""
    kind = wtfdiff.kind_of(path)
    if kind is None and not _is_saved_variables(path):
        return []
    if isinstance(before, _Unread) or isinstance(after, _Unread):
        why = next(
            (s.why for s in (before, after) if isinstance(s, _Unread) and s.why is not None),
            "wowlab could not read both versions back",
        )
        return [f"{_DETAIL_INDENT}(not compared: {why})"]
    if kind is not None:
        return _wtf_lines(kind, before, after, limit)
    return _sv_lines(before, after)


def _wants_detail(path: str) -> bool:
    return wtfdiff.kind_of(path) is not None or _is_saved_variables(path)


def _stored(store: snapshot.SnapshotStore, sha: str | None, size: int | None = None) -> _Side:
    """An object's bytes from the store, checked against its hash. One whose
    recorded `size` is over the detail limit is not read; with no recorded
    size, one that inflates past the limit is refused."""
    if sha is None:
        return None
    if size is not None and size > _DETAIL_READ_LIMIT:
        return _UNREAD
    try:
        return store.read_object(sha, size=_DETAIL_READ_LIMIT if size is None else size)
    except (snapshot.SnapshotError, OSError):
        return _UNREAD


# What the undo does to a file someone changed after the change it undoes:
# it writes over it, but its own transaction snapshots the file before it
# writes, so a second undo puts those changes back, as long as it comes next
# and the game has not rewritten the file at logout meanwhile. Said in every
# state, after a line on who probably changed the file, which depends on it.
_KEEPS_A_COPY = (
    "keeps a copy first, and running wowlab undo again next, before you play, brings them back"
)
_COMPARE = "the lines below compare what is on disk now with what undo puts back"
# A change that finished.
_CHANGED_SINCE = (
    f"{_DETAIL_INDENT}(changed since that transaction, probably by the game at logout or "
    f"/reload: {_COMPARE}, and include those later changes; undo replaces those changes, "
    f"but {_KEEPS_A_COPY})"
)
# A change that failed and whose rollback finished: every journaled path
# already held what was there before it, so a listed file differs only by
# later changes.
_CHANGED_AFTER_ROLLBACK = (
    f"{_DETAIL_INDENT}(changed since that transaction, probably by the game at logout or "
    "/reload; that change's own edits were already put back when it failed, so every line "
    f"below is a later change, and undo only replaces them; it {_KEEPS_A_COPY})"
)
# A change that did not finish (the run was stopped): the record cannot show
# whether another program wrote during the run or after it, nor whether the
# run wrote this file at all.
_CHANGED_UNFINISHED = (
    f"{_DETAIL_INDENT}(changed by another program, such as the game at logout or /reload, "
    "while that change ran or since; that change stopped before it finished and may not "
    f"have written this file: {_COMPARE}; undo replaces those changes, but {_KEEPS_A_COPY})"
)
# For a change whose rollback did not finish: the gate leaves a file another
# program changed while the change ran as that program left it.
_CHANGED_DURING = (
    f"{_DETAIL_INDENT}(changed by another program, such as the game at logout or /reload, "
    f"while that change ran, or since: {_COMPARE}; undo replaces that program's changes, "
    f"but {_KEEPS_A_COPY})"
)
# A state this version does not know (a journal written by a later one): the
# record cannot be read for who changed the file or when, so the line guesses
# neither (M16-98).
_CHANGED_UNKNOWN_STATE = (
    f"{_DETAIL_INDENT}(changed while that transaction ran or since; this version of wowlab "
    f"cannot tell what changed it: {_COMPARE}; undo replaces those changes, but {_KEEPS_A_COPY})"
)
_CHANGED_LINE = {
    "committed": _CHANGED_SINCE,
    "open": _CHANGED_UNFINISHED,
    "rolled_back": _CHANGED_AFTER_ROLLBACK,
    "rollback_incomplete": _CHANGED_DURING,
}
# Every journaled path already holds its pre-transaction bytes and no folder
# it created is left: the undo changes nothing on disk. For a change that did
# not finish, or whose rollback did not, `doctor` reports it until it is
# undone, so that line says what answering yes does.
_NOTHING_TO_PUT_BACK = (
    "  every file already holds what was there before that transaction, so the undo changes no file"
)
_NOTHING_TO_PUT_BACK_UNFINISHED = (
    f"{_NOTHING_TO_PUT_BACK}; answering yes records the undo, and wowlab doctor stops "
    "reporting that change"
)
# A record that never took its pre-write snapshot (2026-11-11 (a)): the
# change was stopped after it removed leftover temporary files and before
# it changed any file. The undo closes it; nothing is put back.
_STOPPED_BEFORE_ANY_FILE = (
    "  that change stopped before it changed any file, so the undo changes no file; "
    "answering yes closes it, and wowlab doctor stops reporting it"
)


def _read_detail(flavor_path: Path, rel: str) -> bytes:
    """The install's file at `rel` for a detail line, read-only through the
    guarded opener, from the install root (the flavor folder's parent, as
    `layout` takes it) one folder at a time (M16-65): a link anywhere on the
    way is refused, `snapshot.LinkedPathError` naming it."""
    return snapshot.read_regular_file(
        flavor_path / rel, limit=_DETAIL_READ_LIMIT, root=flavor_path.parent
    )


def _linked_side(exc: snapshot.LinkedPathError) -> _Unread:
    """The install's side of a detail, refused as a link: why, in the
    detail line's words (the file is named on the line above it)."""
    if exc.final:
        return _Unread("the install's copy is a link")
    return _Unread(f"the install's copy is reached through a linked folder, {exc.folder}")


def _disk_now(flavor_path: Path, rel: str) -> _Side:
    """What the install holds at `rel` now, read-only through the guarded
    opener: None when nothing is there, `_UNREAD` when it cannot be read as
    a regular file under the detail limit (a FIFO, too large); a link, or a
    file reached through a linked folder, says so (M16-65)."""
    try:
        return _read_detail(flavor_path, rel)
    except FileNotFoundError:
        return None
    except snapshot.LinkedPathError as exc:
        return _linked_side(exc)
    except OSError:
        return _UNREAD


def _on_disk(store: snapshot.SnapshotStore, flavor_path: Path, rel: str, sha: str | None) -> _Side:
    """The bytes the install holds at `rel` when they still hash to `sha`
    (read-only, through the guarded opener), else the store's copy. A link,
    or a file reached through a linked folder, is not read and not stood in
    for by the store's copy: the detail says so (M16-65)."""
    if sha is None:
        return None
    try:
        data = _read_detail(flavor_path, rel)
    except snapshot.LinkedPathError as exc:
        return _linked_side(exc)
    except OSError:
        data = None
    if data is not None and hashlib.sha256(data).hexdigest() == sha:
        return data
    return _stored(store, sha)


def _plan_item_lines(
    store: snapshot.SnapshotStore, flavor_path: Path, item: guard.PlanItem
) -> list[str]:
    """The detail lines under one item of a write plan: `before` is on disk
    now, `after` is what the write puts there (a stored object)."""
    if not _wants_detail(item.path):
        return []
    before = _on_disk(store, flavor_path, item.path, item.before)
    after = _stored(store, item.after, item.size)
    return _detail_lines(item.path, before, after, limit=_DETAIL_PLAN_LIMIT)


class SkippedPath(_Out):
    """A file a whole-snapshot restore left alone (owner decision 2026-09-27)."""

    path: str  # relative to the flavor folder
    entry_id: str  # its file-map row, whose Edit cell is `no`
    what: str


class NotRestoredPath(_Out):
    """A screenshot in a snapshot taken with `snap create --screenshots`. Its
    file-map row (`screenshots`) has Edit `n/a`: the write gate never writes
    `Screenshots/`, so a whole restore leaves it out and restores the rest,
    and naming it with --paths is refused (M13-30). It stays in the snapshot."""

    path: str  # relative to the flavor folder
    entry_id: str  # its file-map row, whose Edit cell is `n/a`
    what: str
    # The disk against the snapshot, read-only; `behind_link`: not read, as a
    # folder on its way (Screenshots/ itself) is now a link wowlab does not follow.
    disk: Literal["same", "differs", "missing", "behind_link"]


class AccountMapped(_Out):
    """One account folder a restore into another target maps."""

    snapshot: str  # the folder under WTF/Account/ as the snapshot names it
    target: str  # the folder it is written to in the target


class IntoReport(_Out):
    """A restore into a target the player named (--into-flavor, --into-root)."""

    from_flavor_folder: str  # the flavor folder the snapshot was taken of
    from_install_root: str  # the install root it was taken in
    accounts: list[AccountMapped]
    # Folders under the snapshot's WTF/Account/ that are no account folder and
    # so are not mapped: they go back under their own name. Left out when none.
    unmapped: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)


class RestoreReport(_Out):
    """`wowlab snap restore --json`: the plan, and with `--yes` what was done."""

    snapshot_id: str
    flavor_path: str
    snapshot_version: str | None  # the flavor version the snapshot was taken on
    flavor_version: str | None  # the flavor's version now
    paths: list[str] | None  # --paths as given; None restores the whole snapshot
    plan: list[guard.PlanItem]  # path, before/after SHA-256 (None: absent), bytes written
    # File-map Edit `no` files a whole restore does not write: client-managed
    # ones, and executables whose content changed or that are missing (M13-38).
    skipped: list[SkippedPath]
    # Screenshots (file-map Edit `n/a`) a whole restore leaves out and --paths
    # cannot restore either; they stay in the snapshot.
    not_restored: list[NotRestoredPath]
    dry_run: bool
    applied: bool
    transaction: str | None  # the journal record of the applied restore
    notes: list[str]
    # With --into-flavor or --into-root: where the snapshot was taken, and the
    # account folders mapped. Left out of the JSON otherwise.
    into: IntoReport | None = Field(default=None, exclude_if=lambda v: v is None)
    # The text plan, line by line, as the terminal shows it before the
    # prompt, with keys, macros and CVars by name (M16-10).
    plan_lines: list[str] = Field(default_factory=list)


class UndoReport(_Out):
    """`wowlab undo --json`: the transaction to undo, and whether it was."""

    undone: str  # the journal record undone (or to be)
    label: str
    state: str
    created_at: str
    flavor_path: str
    # As that transaction journaled them: `before` is what the undo puts back
    # (None: the file is deleted), `after` is what is there now.
    plan: list[guard.PathChange]
    created_dirs: list[str]
    applied: bool
    transaction: str | None  # the journal record of the undo itself
    notes: list[str]
    # The text plan, line by line, as the terminal shows it before the
    # prompt, with keys, macros and CVars by name (M16-10).
    plan_lines: list[str] = Field(default_factory=list)
    # The paths the undo wrote (relative to `flavor_path`), from its own
    # journal record `transaction`: the listed ones, and any that changed
    # after the plan read it. Empty when nothing was undone (M16-105).
    written_paths: list[str] = Field(default_factory=list)
    # Those of `written_paths` the text plan did not list (`plan` is the
    # whole record undone, so it cannot tell them apart): files that changed
    # after the plan read them, which the text result names after "Undone."
    # (M16-105).
    unlisted_written: list[str] = Field(default_factory=list)


def _client_managed(
    lay: layout.Layout, plan: Sequence[guard.PlanItem]
) -> tuple[list[guard.PlanItem], list[SkippedPath]]:
    """Split a whole-snapshot plan: files whose file-map row says `no` are
    ones wowlab leaves alone (client-written backups such as `.lua.bak` and
    `.old`, `Blizzard_*` folders, most likely copied in by the owner, and
    file-browser metadata); they are skipped unless named with --paths."""
    kept: list[guard.PlanItem] = []
    skipped: list[SkippedPath] = []
    for item in plan:
        found = lay.classify(item.path, is_dir=False)
        if isinstance(found, layout.Classified) and found.entry.edit == "no":
            skipped.append(
                SkippedPath(path=item.path, entry_id=found.entry.id, what=_plain(found.entry.what))
            )
        else:
            kept.append(item)
    return kept, skipped


# The file-map row of `Screenshots/`, which `snap create --screenshots`
# captures and the write gate never writes (Edit `n/a`).
_SCREENSHOTS_ROW = "screenshots"


def _screenshot_row(lay: layout.Layout, rel: str, *, is_dir: bool | None) -> FileMapEntry | None:
    found = lay.classify(rel, is_dir=is_dir)
    if isinstance(found, layout.Classified) and found.entry.id == _SCREENSHOTS_ROW:
        return found.entry
    return None


# The file-map row of an executable under `Interface/AddOns/` (Edit `no`):
# the write gate never writes, restores or deletes one (L7).
_EXECUTABLE_ROW = "addon-executable"


def _changed_executables(
    lay: layout.Layout, entries: Sequence[snapshot.Entry], install_root: Path, folder: str
) -> tuple[list[snapshot.Entry], list[SkippedPath]]:
    """Split this flavor's `entries` for a whole restore: a file named like
    an executable whose file-map row has Edit `no` (an addon's `helper.sh`,
    row `addon-executable`, or the `._helper.sh` macOS writes beside it, row
    `os-metadata`) and whose content the disk no longer holds, changed or
    missing, is skipped and listed with its row (M13-38); every other entry
    is to be restored.

    Only a regular-file entry the gate would refuse for being an executable
    and nothing else (`verdict`'s copy of its name rules) is skipped, and
    only when the disk shows a plain change (`_plainly_on_disk`): a regular
    file with other bytes, spelled exactly as in the snapshot, or nothing
    at all in real folders. Everything else (a reserved name, a link, a
    folder or a link where the file was, a name spelled otherwise on disk,
    one behind a linked folder, same bytes with other permission bits) stays
    named, and the gate refuses it as a whole restore does. Reads only,
    following no link (`profiles.disk_state`, folder listings, `lstat`)."""
    prefix = folder + "/"
    rest: list[snapshot.Entry] = []
    skipped: list[SkippedPath] = []
    for entry in entries:
        rel = entry.path.removeprefix(prefix)
        mine = entry.path.startswith(prefix)  # another flavor's path: the gate refuses it
        if mine and entry.kind == "file" and entry.sha256 is not None and has_executable_part(rel):
            found = lay.classify(rel, is_dir=False)
            if (
                isinstance(found, layout.Classified)
                and found.entry.edit == "no"
                and gate_names_but_for_executable(rel)
                and _plainly_changed(install_root, folder, entry, rel)
            ):
                skipped.append(
                    SkippedPath(path=rel, entry_id=found.entry.id, what=_plain(found.entry.what))
                )
                continue
        rest.append(entry)
    return rest, skipped


def _plainly_changed(install_root: Path, folder: str, entry: snapshot.Entry, rel: str) -> bool:
    """Whether the disk holds a plain change of `entry`: its content differs
    and `rel` is a regular file there, or it is missing from real folders."""
    state = profiles.disk_state(install_root, folder, entry)
    if state not in ("differs", "missing"):
        return False
    return _plainly_on_disk(install_root / folder, rel) == (
        "file" if state == "differs" else "missing"
    )


def _is_reparse(st: os.stat_result) -> bool:
    """A Windows reparse point of any kind (a junction, a symlink, or one that
    is neither); always False elsewhere, where the attribute is absent."""
    return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _plainly_on_disk(flavor_dir: Path, rel: str) -> Literal["file", "missing"] | None:
    """What `rel` is under `flavor_dir`, read without following a link: `file`
    when every part is in its folder's listing spelled exactly as in `rel`,
    each folder on the way is a real folder (by `lstat`: not a link, a
    junction or another reparse point) and the last part a regular file;
    `missing` when the folders on the way that are there are real folders,
    spelled exactly, and the first part that is not there (the file, or a
    folder on its way: a deleted addon, sub-folder or `.app`) is absent from
    its real parent's listing in every letter case; None for anything else
    (a link, a junction or another reparse point on the way, a folder or a
    link at the leaf, a part spelled otherwise, an error)."""
    parts = rel.split("/")
    current = flavor_dir
    try:
        for n, part in enumerate(parts):
            names = {child.name for child in longpath.iterdir(current)}
            last = n == len(parts) - 1
            if part not in names:
                folded = part.casefold()
                if all(name.casefold() != folded for name in names):
                    return "missing"  # nothing below it is there either
                return None  # spelled otherwise on disk
            current = current / part
            st = longpath.lstat(current)
            if _is_reparse(st):
                return None
            if last:
                return "file" if stat.S_ISREG(st.st_mode) else None
            if not stat.S_ISDIR(st.st_mode) or longpath.is_junction(current):
                return None
    except OSError:
        return None
    return None


def _set_aside(
    lay: layout.Layout, manifest: snapshot.Manifest, install_root: Path, folder: str
) -> tuple[list[snapshot.Entry], list[NotRestoredPath]]:
    """Split a snapshot's entries for a whole restore: the screenshots (every
    entry under a top-level folder whose file-map row is `screenshots`) are
    set aside, each with what the disk holds now (read-only: size first,
    then content; `profiles.disk_state`); every other entry is to be
    restored. Each top-level folder is classified once."""
    prefix = folder + "/"
    rest: list[snapshot.Entry] = []
    aside: list[NotRestoredPath] = []
    tops: dict[str, FileMapEntry | None] = {}
    for entry in manifest.entries:
        if not entry.path.startswith(prefix):
            rest.append(entry)  # another flavor's path: the gate refuses the restore
            continue
        rel = entry.path.removeprefix(prefix)
        top = rel.split("/", 1)[0]
        if top not in tops:
            tops[top] = _screenshot_row(lay, top, is_dir=True)
        row = tops[top]
        if row is None:
            rest.append(entry)
            continue
        state = profiles.disk_state(install_root, folder, entry)
        aside.append(
            NotRestoredPath(
                path=rel,
                entry_id=row.id,
                what=_plain(row.what),
                disk=state,
            )
        )
    return rest, aside


def _names_for_whole(
    entries: Sequence[snapshot.Entry], install_root: Path, folder: str
) -> tuple[list[str], list[snapshot.Entry]]:
    """A whole restore of `entries` (all of this flavor), as names for the
    gate's restore by name, and the entries left unnamed.

    The gate's whole restore leaves an unchanged link or executable alone,
    and refuses a changed one; by name it refuses both. So an unchanged
    link or executable (by the gate's own name rule, `verdict`'s copy) is
    left unnamed, the disk read first (read-only, following no link:
    `profiles.disk_state`). Every other entry is named, unchanged or not,
    so the gate checks each one as a whole restore does: it refuses what it
    would refuse there (a reserved name, a case collision, a changed link),
    and a file that changes between the plan and the confirm shows in its
    plan."""
    prefix = folder + "/"
    names: list[str] = []
    left: list[snapshot.Entry] = []
    for e in entries:
        rel = e.path.removeprefix(prefix)
        special = e.kind != "file" or has_executable_part(rel)
        if special and _unchanged(install_root, folder, e):
            left.append(e)
        else:
            names.append(rel)
    return names, left


def _unchanged(install_root: Path, folder: str, entry: snapshot.Entry) -> bool:
    """Whether a link or executable `entry` is one the gate's whole restore
    leaves alone: a link with the entry's target, or a file the gate would
    accept but for being an executable (`verdict`'s copy of its name rules),
    spelled on disk exactly as in the snapshot (every part, case included,
    is in its folder's listing), and a regular file with the entry's bytes
    and permission bits (`guard._unchanged_executable`). Anything else is
    named, so the gate refuses it as a whole restore does. Reads only,
    following no link (`profiles.disk_state`, listings, then an `lstat`)."""
    if profiles.disk_state(install_root, folder, entry) != "same":
        return False
    if entry.kind != "file":
        return True
    rel = entry.path.removeprefix(folder + "/")
    if not gate_names_but_for_executable(rel):
        return False
    parent = install_root / folder
    try:
        for part in rel.split("/"):
            if part not in {child.name for child in longpath.iterdir(parent)}:
                return False  # spelled otherwise on disk (case): the gate refuses it
            parent = parent / part
        st = longpath.lstat(parent)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == entry.mode


def _skipped_lines(skipped: Sequence[SkippedPath]) -> list[str]:
    """The text plan's skipped files: the ones --paths can restore, then the
    addon executables, which it cannot (the gate never writes one)."""
    lines: list[str] = []
    others = [s for s in skipped if not has_executable_part(s.path)]
    executables = [s for s in skipped if has_executable_part(s.path)]
    if others:
        lines.append(
            f'  Skipped {len(others)} file(s) wowlab leaves alone (file-map Edit "no": '
            "client-written backups, wowlab's leftover temp files, Blizzard_* folders, "
            "file-browser metadata; name one with --paths to restore it):"
        )
        lines += [f"    {sk.path}  [{sk.entry_id}]" for sk in others]
    if executables:
        lines.append(
            f"  Skipped {len(executables)} executable(s) whose content changed or that are "
            'missing on disk (file-map Edit "no"): wowlab never writes, restores or deletes '
            "an executable, not even with --paths; reinstall the addon to get one back:"
        )
        lines += [f"    {sk.path}  [{sk.entry_id}]" for sk in executables]
    return lines


# How many changed screenshots the text plan names; --json lists every one.
_NOT_RESTORED_SHOWN = 10


def _left_alone_lines(left_alone: Sequence[str]) -> list[str]:
    """The text plan's "left alone" heading: the files added under the
    snapshot's folders since it was taken, which a whole restore never
    writes or deletes."""
    if not left_alone:
        return []
    return [
        f"  Left alone: {len(left_alone)} file(s) added since the snapshot was taken. "
        "A whole restore never deletes them; name one with --paths to delete it:",
        *(f"    {rel}" for rel in left_alone),
    ]


def _added_since(
    store: snapshot.SnapshotStore, manifest: snapshot.Manifest, install_root: Path, folder: str
) -> tuple[str, ...]:
    """What a whole restore of `manifest` (spelled as this flavor spells it)
    leaves alone, by the gate's rule: every regular file under its subtrees,
    outside its excluded paths, that it does not hold, held to the gate's
    allowlist roots as the gate holds its own list. Reads directory entries
    and `lstat` only, following no link; flavor-relative, sorted."""
    prefix = folder + "/"
    held = {e.path for e in manifest.entries}
    ours = guard._allowlisted_trees(manifest.subtrees, folder)
    if not ours:
        return ()
    excluded = guard._allowlisted_trees(manifest.excluded, folder)
    listed = store.list_tree(install_root, ours, exclude=excluded)
    return tuple(
        path.removeprefix(prefix)
        for path, kind in listed
        if kind == "file"
        and path.startswith(prefix)
        and guard._in_allowlist_root(path.removeprefix(prefix))
        and path not in held
    )


def _not_restored_lines(snapshot_id: str, aside: Sequence[NotRestoredPath]) -> list[str]:
    shown = [a for a in aside if a.disk != "same"]
    if not shown:
        return [f"  The snapshot also holds {len(aside)} screenshot(s); all still match the disk."]
    changed = sum(a.disk in ("differs", "missing") for a in aside)
    unread = [a for a in aside if a.disk == "behind_link"]
    clauses: list[str] = []
    if changed:
        one = changed == 1
        clauses.append(
            f"{changed} {'differs' if one else 'differ'} from or {'is' if one else 'are'} "
            "missing on disk"
        )
    if unread:
        top = unread[0].path.split("/", 1)[0]
        # M16-80: the readers' rule for a link, said of the folder that is now one.
        subject = "it" if len(unread) == 1 else "each"
        clauses.append(
            f"{len(unread)} not read ({subject} is reached through {top}/, which is now a "
            "link, and wowlab does not read through links)"
        )
    lines = [
        f"  Not restored: {len(aside)} screenshot(s) wowlab never writes (file-map Edit "
        f'"n/a"; --paths cannot restore them either). They stay in snapshot {snapshot_id}; '
        + "; ".join(clauses)
        + ":"
    ]
    lines += [f"    {a.path}  [{a.disk}]" for a in shown[:_NOT_RESTORED_SHOWN]]
    if len(shown) > _NOT_RESTORED_SHOWN:
        lines.append(f"    and {len(shown) - _NOT_RESTORED_SHOWN} more (--json lists every one)")
    return lines


def _screenshot_note(
    lay: layout.Layout,
    manifest: snapshot.Manifest,
    folder: str,
    named: Sequence[str],
    exc: Exception,
) -> None:
    """When the gate refused a named screenshot, say why on one more line."""
    for rel in named:
        if repr(rel) in str(exc) and _screenshot_row(lay, rel, is_dir=False) is not None:
            held = manifest.entry(f"{folder}/{rel}") is not None
            where = f"; the file stays in snapshot {manifest.id}" if held else ""
            exc.add_note(f'wowlab never writes Screenshots/ (file-map Edit "n/a"){where}.')
            return


def _executable_note(lay: layout.Layout, named: Sequence[str], exc: Exception) -> None:
    """When the gate refused a named executable, say what the owner can do."""
    for rel in named:
        if f"{rel!r} is an executable" not in str(exc):
            continue
        found = lay.classify(rel, is_dir=False)
        addon = isinstance(found, layout.Classified) and found.entry.id == _EXECUTABLE_ROW
        exc.add_note(
            "wowlab never writes, restores or deletes an executable, not even with --paths: "
            "name the other files with --paths"
            + (", or reinstall the addon to get its executable back." if addon else ".")
        )
        return
