"""`wowlab sv list`, `sv dump` and `sv merge` (§13.4, M11-09) (M16-111)."""

import base64
import contextlib
import datetime
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple

import typer
from pydantic import Field

from wowlab_core import (
    addoncheck,
    guard,
    install,
    layout,
    longpath,
    luadata,
    snapshot,
    svmerge,
    wtfconfig,
)
from wowlab_core.cli._cmd_filemap import (
    _hint,
    _is_inside,
    _plain_arg,
)
from wowlab_core.cli._common import (
    _NAME_THE_INSTALL_BY_ITSELF,
    EXIT_ERROR,
    EXIT_REFUSED,
    EXIT_USAGE,
    AccountOpt,
    CharacterOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    YesOpt,
    _bytes,
    _confirm,
    _discover,
    _emit,
    _handled,
    _install_words,
    _json_text,
    _keep_notes,
    _listed,
    _lua_refusal_why,
    _map_strings,
    _note,
    _open,
    _Out,
    _player_copy,
    _plural,
    _Report,
    _say,
    _say_err,
    _say_notes,
    _say_prose,
    _select_account,
    _select_character,
    _sv_name,
    sv_app,
)

# ─── sv ──────────────────────────────────────────────────────────────────────


_SV_TIMING = (
    "SavedVariables are written at logout, /reload or a clean exit: these files are from the "
    "last save, not the session in progress, and a running client overwrites them at its "
    "next save."
)


class SvReadFailure(_Out):
    """One SavedVariables file `sv list --unreadable` could not read (M16-131)."""

    path: str  # as in `files`, relative to the flavor folder
    backup: bool  # a `.lua.bak`, the game's previous save, which it does not load
    # `refused`: not data the SavedVariables parser accepts; `too_large`: past
    # one of the parser's bounds (size, depth, budget), so not checked;
    # `not_opened`: the file could not be opened to read.
    kind: Literal["refused", "too_large", "not_opened"]
    reason: str  # in the words the text output prints
    line: int | None  # 1-based, where the parser stopped (`refused` only)
    column: int | None  # 1-based, in bytes (`refused` only)
    detail: str  # the parser's own message, or the system's error


class SvListReport(_Out):
    """`wowlab sv list --json`."""

    flavor_folder: str
    files: list[layout.SavedVariablesFile]
    notes: list[str]  # the caveats the text output prints
    # M16-34: with --orphans, `files` holds only those no installed TOC declares.
    orphans: bool = False
    # M16-131: with --unreadable, `files` holds only those wowlab could not
    # read, and `read_failures` says why, one per file in the same order.
    unreadable: bool = False
    read_failures: list[SvReadFailure] = Field(default_factory=list)


OrphansOpt = Annotated[
    bool,
    typer.Option(
        "--orphans",
        help="Only the files that no installed addon declares (saved data an addon left "
        "behind), with their size and the total.",
    ),
]
_ORPHAN_REINSTALL = (
    "If the addon is installed again it reads this file again, so deleting it loses those settings."
)
_ORPHAN_WHAT = (
    "A file is listed when Interface/AddOns/ holds no folder of its name, or when that "
    "folder's TOC declares no SavedVariables (account-wide file) or no "
    "SavedVariablesPerCharacter (character file). Such a file is usually left by an addon "
    "that was removed, renamed, or stopped saving at that scope. The client does not delete "
    "it, and neither does wowlab. " + _ORPHAN_REINSTALL
)
_ORPHAN_BLIZZARD = (
    "Blizzard's own files (SavedVariables.lua and Blizzard_*.lua) are never listed: the "
    "client loads its own addons from game data, not from Interface/AddOns/ [verify]."
)


UnreadableOpt = Annotated[
    bool,
    typer.Option(
        "--unreadable",
        help="Only the files wowlab cannot read as saved data (a file cut off during a save, "
        "say), each with where and why. Reads every file.",
    ),
]
# The caveat when a listed file is one the game loads (not a `.lua.bak`)
# and was refused or could not be opened.
_UNREADABLE_LOSS = (
    "If the game cannot load such a file either, whatever saved it (an addon, or the game's "
    "own interface) finds no saved settings, and most addons then start from their defaults; "
    "the game saves what it then holds over the file at the next logout or /reload [verify]. "
    "wowlab snap create keeps a copy of each listed file, and of its .lua.bak, as it is now."
)
# The caveat when every such file was only past a bound (`too_large`).
_UNREADABLE_TOO_LARGE = "wowlab did not check a file past its limits; that says nothing about whether the game loads it."
# Said after the reason of a listed `.lua.bak`.
_UNREADABLE_BACKUP = (
    "This is the game's backup copy, which it does not load; it is replaced at a later "
    "save [verify]."
)
_UNREADABLE_WHAT = (
    "Each file is read as data and never run. wowlab is stricter than the game in places: it "
    "refuses things the game is not known to write (a long comment, a long-bracket string, a "
    "function) and number spellings not yet seen in a real file, such as an infinite value "
    "[verify]. So a file edited by hand, or one holding such a number, may be listed here and "
    "still load in the game."
)
_UNREADABLE_WITH_ORPHANS = "--unreadable and --orphans are separate lists: give one of them"


def _lab_records_caveat(n: int) -> str:
    return (
        f"Not listed: {_plural(n, 'WowLab.lua file')}, the lab-addon's records, which wowlab "
        "reads whether or not the lab-addon is installed."
    )


def _no_toc_caveat(folders: Sequence[str]) -> str:
    return (
        f"Not listed: the saved data of {', '.join(folders)}, whose folder has no TOC named "
        "after it (often an addon unzipped one folder too deep); `wowlab addons check` shows it."
    )


def _not_judged_caveat(folders: Sequence[str]) -> str:
    return (
        f"Not judged: the saved data of {', '.join(folders)}, whose TOC could not be read; "
        "`wowlab addons check` says why."
    )


@sv_app.command("list", short_help="Every SavedVariables file, account-wide and per character.")
@_handled
def sv_list(
    account: AccountOpt = None,
    character: CharacterOpt = None,
    orphans: OrphansOpt = False,
    unreadable: UnreadableOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """Every SavedVariables file, account-wide and per character. With
    --orphans, only the files that no installed addon declares, with their
    size and the total; with --unreadable, only the files wowlab cannot
    read as saved data, with where and why. Their standing notes are behind
    --notes. Reads only: a file is read as data and never run.

    \f
    JSON: SvListReport."""
    if orphans and unreadable:
        raise CliError(_UNREADABLE_WITH_ORPHANS, EXIT_USAGE)
    inst, chosen, lay = _open(root, flavor)
    walk = lay.wtf_walk()
    files = list(walk.saved_variables)
    if account is not None or character is not None:
        acct = _select_account(lay, account)
        files = [f for f in files if f.account == acct.folder]
        if character is not None:
            char = _select_character(acct, character)
            files = [
                f
                for f in files
                if f.realm_folder == char.realm_folder and f.character_folder == char.folder
            ]
    given_root = str(inst.root) if root is not None and str(root) != "" else None
    no_account = (
        _no_account_notes(walk, chosen.folder, given_root)
        if account is None and character is None
        else []
    )
    if orphans:
        _sv_orphans(lay, chosen.folder, files, no_account, json_out, notes_out)
        return
    if unreadable:
        _sv_unreadable(lay, chosen.folder, files, no_account, json_out, notes_out)
        return
    report = SvListReport(flavor_folder=chosen.folder, files=files, notes=[_SV_TIMING, *no_account])
    if json_out:
        _emit(report)
        return
    if not files:
        _say("No SavedVariables files.")
    for f in files:
        _say(_sv_row(f))
    if files:
        _say(_SV_TIMING)
    for line in no_account:
        _say(line)


def _sv_row(f: layout.SavedVariablesFile) -> str:
    who = "Blizzard UI" if f.blizzard else (f.addon or "?")
    flags = " (previous write, .bak)" if f.backup else ""
    return f"{f.scope:<9}  {who:<32}  {_bytes(f.size):>14}  {f.path}{flags}"


def _sv_orphans(
    lay: layout.Layout,
    flavor_folder: str,
    files: Sequence[layout.SavedVariablesFile],
    no_account: Sequence[str],
    json_out: bool,
    notes_out: bool,
) -> None:
    """M16-34, `sv list --orphans`: the answer (a count, then the rows), at
    most one caveat for this result, then the standing notes or a footer."""
    found = addoncheck.orphan_saved_variables(files, lay.addons())
    # What was left out, most telling first: the first is this result's one
    # caveat, any other joins the standing notes ahead of them.
    left_out = [
        *([_lab_records_caveat(len(found.lab_addon_records))] if found.lab_addon_records else []),
        *([_no_toc_caveat(found.no_toc)] if found.no_toc else []),
        *([_not_judged_caveat(found.not_judged)] if found.not_judged else []),
    ]
    caveat = left_out[0] if left_out else None
    standing = [*left_out[1:], _ORPHAN_WHAT, _ORPHAN_BLIZZARD, _SV_TIMING, *no_account]
    report = SvListReport(
        flavor_folder=flavor_folder,
        files=list(found.files),
        notes=[caveat, *standing] if caveat is not None else standing,
        orphans=True,
    )
    if json_out:
        _emit(report)
        return
    if not found.files:
        _say("No SavedVariables file is left without an installed addon that declares it.")
    else:
        addons = {wtfconfig.fold_name(f.addon or "") for f in found.files}
        total = sum(f.size for f in found.files)
        verb = "is" if len(found.files) == 1 else "are"
        _say_prose(
            f"{_plural(len(found.files), 'SavedVariables file')} of "
            f"{_plural(len(addons), 'addon')}, {_bytes(total)} in all, {verb} declared by no "
            f"installed addon. {_ORPHAN_REINSTALL}"
        )
        for f in found.files:
            _say(_sv_row(f))
    _say_notes(standing, notes_out, caveat, keep=[*found.not_judged, *found.no_toc, *no_account])


def _read_failure(lay: layout.Layout, f: layout.SavedVariablesFile) -> SvReadFailure | None:
    """Why `f` does not read as saved data, or None when it does. Parsed
    by `luadata` as data, never run (L3), within its bounds, opened
    read-only from the install root (L1). A file gone since the listing is
    not a failure."""
    try:
        _listed(luadata.read, lay, f.path)
    except FileNotFoundError:
        return None
    except luadata.LuaLimitError as exc:
        return SvReadFailure(
            path=f.path,
            backup=f.backup,
            kind="too_large",
            reason=f"{_limit_words(exc.message)}, so wowlab did not check it",
            line=None,
            column=None,
            detail=exc.message,
        )
    except luadata.LuaDataError as exc:
        return SvReadFailure(
            path=f.path,
            backup=f.backup,
            kind="refused",
            # The size the listing found: a value cut short at the end of
            # the file is named as cut off.
            reason=_lua_refusal_why(exc, f.size),
            line=exc.line,
            column=exc.column,
            detail=str(exc),
        )
    except OSError as exc:
        said = exc.strerror or str(exc)
        return SvReadFailure(
            path=f.path,
            backup=f.backup,
            kind="not_opened",
            reason=f"wowlab could not open it: {said}",
            line=None,
            column=None,
            detail=said,
        )
    return None


def _mib_or_bytes(n: int) -> str:
    mib = 1024 * 1024
    return f"{n // mib:,} MiB" if n % mib == 0 else _bytes(n)


def _limit_words(message: str) -> str:
    """Which of the parser's bounds a file is past, in player words; the
    parser's own message stays in `detail`."""
    if message.startswith(("document is ", "file is ")):
        return f"it is larger than wowlab reads (over {_mib_or_bytes(luadata.MAX_FILE_BYTES)})"
    if message.startswith("tables nested deeper"):
        return f"it holds tables nested more than {luadata.MAX_DEPTH} deep"
    if message.startswith("document over the parse budget"):
        return "it holds too much data for wowlab to hold in memory"
    if message.startswith("number literal of"):
        return f"it holds a number written with more than {luadata.MAX_NUMBER_CHARS:,} characters"
    if message.startswith("string literal over"):
        return (
            f"it holds a text value larger than wowlab reads "
            f"(over {_mib_or_bytes(luadata.MAX_STRING_BYTES)})"
        )
    return "it is beyond what wowlab reads"


def _unreadable_caveat(failures: Sequence[SvReadFailure]) -> str | None:
    """The one caveat for this result: the possible loss when a file the
    game loads was refused or not opened; else, when one was past a bound,
    that wowlab did not check it; else (only `.lua.bak` files) none."""
    if any(not r.backup and r.kind in ("refused", "not_opened") for r in failures):
        return _UNREADABLE_LOSS
    if any(r.kind == "too_large" for r in failures):
        return _UNREADABLE_TOO_LARGE
    return None


def _sv_unreadable(
    lay: layout.Layout,
    flavor_folder: str,
    files: Sequence[layout.SavedVariablesFile],
    no_account: Sequence[str],
    json_out: bool,
    notes_out: bool,
) -> None:
    """M16-131, `sv list --unreadable`: each file read as data; the answer
    (a count, then each file with why), the one caveat when any is listed,
    then the standing notes or a footer."""
    failed: list[tuple[layout.SavedVariablesFile, SvReadFailure]] = []
    for f in files:
        failure = _read_failure(lay, f)
        if failure is not None:
            failed.append((f, failure))
    caveat = _unreadable_caveat([r for _, r in failed])
    standing = [_UNREADABLE_WHAT, _SV_TIMING, *no_account]
    report = SvListReport(
        flavor_folder=flavor_folder,
        files=[f for f, _ in failed],
        notes=[caveat, *standing] if caveat is not None else standing,
        unreadable=True,
        read_failures=[r for _, r in failed],
    )
    if json_out:
        _emit(report)
        return
    if not files:
        _say("No SavedVariables files.")
    elif not failed:
        _say(f"wowlab read all {_plural(len(files), 'SavedVariables file')} as saved data.")
    else:
        verb = "is" if len(failed) == 1 else "are"
        _say(
            f"{len(failed):,} of {_plural(len(files), 'SavedVariables file')} {verb} not "
            "saved data wowlab can read:"
        )
        for f, failure in failed:
            _say(_sv_row(f))
            why = failure.reason + "."
            if failure.backup:
                why += " " + _player_copy(_UNREADABLE_BACKUP)
            _say_prose(why, indent="    ", hang="    ")
    _say_notes(standing, notes_out, caveat, keep=list(no_account))


def _no_account_notes(walk: layout.WtfWalk, flavor: str, root: str | None) -> list[str]:
    """M13-50: one line per folder under WTF/Account/ that is no account and
    holds files, which `sv list` leaves out (the client's own
    `WTF/Account/SavedVariables/`, say). The `tree` command names the flavor
    folder, and the install with --root when it was given, as `explain`'s
    hints do."""
    out = []
    with_root = ["--root", root] if root is not None else []
    for folder in walk.not_accounts:
        n = sum(1 for f in walk.wtf_files if f.path.startswith(folder + "/"))
        if n:
            command = _hint(["tree", folder, "--flavor", flavor, *with_root], "").command
            out.append(
                f"{folder}/ holds {_plural(n, 'file')} that "
                f"{'belongs' if n == 1 else 'belong'} to no account; "
                f"`{command}` lists {'it' if n == 1 else 'them'}."
            )
    return out


# The JSON shape of `sv dump --json`. Flat: every value is one row naming its
# parent row, so the JSON nests to a fixed depth however deep the Lua tables
# go (a nested shape stops validating near depth 60, below the parser's
# MAX_DEPTH of 200), and rows can be written one at a time.


class LuaStringNode(_Out):
    type: Literal["string"]
    text: str  # the decoded bytes as UTF-8; invalid bytes shown as \xNN
    utf8: bool  # whether the bytes are valid UTF-8
    base64: str | None  # the exact bytes, when they are not valid UTF-8


class LuaNumberNode(_Out):
    type: Literal["number"]
    raw: str  # the number exactly as written


class LuaBoolNode(_Out):
    type: Literal["boolean"]
    value: bool


class LuaNilNode(_Out):
    type: Literal["nil"]


class LuaTableNode(_Out):
    type: Literal["table"]
    entries: int  # how many entries it holds; they are the rows whose parent is this row


LuaNode = Annotated[
    LuaTableNode | LuaStringNode | LuaNumberNode | LuaBoolNode | LuaNilNode,
    Field(discriminator="type"),
]


class SvValue(_Out):
    """One value: a top-level variable, the `--path` value, or a table entry."""

    id: int  # the row's index in `values`
    parent: int | None  # the table row holding it; None for a variable or the --path value
    # `variable`: a top-level `name = value`; `path`: the value --path named;
    # otherwise how the entry's key is written.
    style: Literal["variable", "path", "positional", "string", "number", "name", "boolean"]
    name: str | None  # the variable name, a bare-name key, or the --path text
    position: int | None  # 1-based, positional entries only
    key: LuaStringNode | LuaNumberNode | LuaBoolNode | None  # a bracketed key
    value: LuaNode
    duplicate: bool  # a later entry with a key an earlier one already has
    comment: str | None  # the line comment after the entry, e.g. "-- [1]"


class SvDumpReport(_Out):
    """`wowlab sv dump --json`: every value, depth first in file order (a
    table's entries follow its row). Written compact, one row at a time."""

    file: str
    path: str | None
    values: list[SvValue]


def _lua_text(raw: bytes) -> str:
    return raw.decode("utf-8", "backslashreplace")


def _string_node(value: luadata.LuaString) -> dict[str, Any]:
    data = value.data
    try:
        return {"type": "string", "text": data.decode("utf-8"), "utf8": True, "base64": None}
    except UnicodeDecodeError:
        return {
            "type": "string",
            "text": _lua_text(data),
            "utf8": False,
            "base64": base64.b64encode(data).decode("ascii"),
        }


def _value_node(value: luadata.LuaValue) -> dict[str, Any]:
    if isinstance(value, luadata.LuaTable):
        return {"type": "table", "entries": len(value.entries)}
    if isinstance(value, luadata.LuaString):
        return _string_node(value)
    if isinstance(value, luadata.LuaNumber):
        return {"type": "number", "raw": value.raw}
    if isinstance(value, luadata.LuaBool):
        return {"type": "boolean", "value": value.value}
    return {"type": "nil"}


def _key_node(entry: luadata.Entry) -> dict[str, Any] | None:
    key = entry.key
    if isinstance(key, luadata.LuaString):
        return _string_node(key)
    if isinstance(key, luadata.LuaNumber):
        return {"type": "number", "raw": key.raw}
    if isinstance(key, luadata.LuaBool):
        return {"type": "boolean", "value": key.value}
    return None


def _rows(tops: Sequence[tuple[str, str, luadata.LuaValue]]) -> Iterator[dict[str, Any]]:
    """`SvValue` rows as plain dicts, one at a time, depth first; iterative,
    so the parser's depth bound is never a Python recursion problem."""
    next_id = 0
    for style, name, value in tops:
        top = next_id
        next_id += 1
        yield {
            "id": top,
            "parent": None,
            "style": style,
            "name": name,
            "position": None,
            "key": None,
            "value": _value_node(value),
            "duplicate": False,
            "comment": None,
        }
        if not isinstance(value, luadata.LuaTable):
            continue
        stack: list[tuple[int, Iterator[luadata.Entry], list[int]]] = [
            (top, iter(value.entries), [0])
        ]
        while stack:
            parent, entries, counter = stack[-1]
            entry = next(entries, None)
            if entry is None:
                stack.pop()
                continue
            position: int | None = None
            if entry.style is luadata.KeyStyle.POSITIONAL:
                counter[0] += 1
                position = counter[0]
            row = next_id
            next_id += 1
            yield {
                "id": row,
                "parent": parent,
                "style": entry.style.value,
                "name": entry.key if isinstance(entry.key, str) else None,
                "position": position,
                "key": _key_node(entry),
                "value": _value_node(entry.value),
                "duplicate": entry.duplicate,
                "comment": _lua_text(entry.comment) if entry.comment is not None else None,
            }
            if isinstance(entry.value, luadata.LuaTable):
                stack.append((row, iter(entry.value.entries), [0]))


def _stream_dump(file: str, path: str | None, rows: Iterator[dict[str, Any]]) -> None:
    """Write `SvDumpReport` JSON to stdout row by row: only one row is held
    at a time, and nothing is indented (a deep file would otherwise print a
    wall of spaces on every line). Each row is one `encode` call, which takes
    the C encoder; `iterencode` would take the pure-Python one (M16-24). A row
    is at most three levels deep, so the C encoder's recursion is bounded."""
    encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"))
    out = typer.get_text_stream("stdout")
    head = {"file": _json_text(file), "path": None if path is None else _json_text(path)}
    _Report.printed = True
    out.write(encoder.encode(head)[:-1] + ',"values":[')
    for n, row in enumerate(rows):
        if n:
            out.write(",")
        out.write(encoder.encode(_map_strings(row, _json_text)))
    out.write("]}\n")
    out.flush()


def _scalar(value: luadata.LuaValue) -> str:
    if isinstance(value, luadata.LuaTable):
        return "{}"
    if isinstance(value, luadata.LuaString):
        return _lua_text(value.raw)
    if isinstance(value, luadata.LuaNumber):
        return value.raw
    if isinstance(value, luadata.LuaBool):
        return "true" if value.value else "false"
    return "nil"


def _flatten(path: str, value: luadata.LuaValue) -> Iterator[tuple[str, str]]:
    """(path, value text) for every leaf and empty table, in file order.

    Positional entries count from 1 as Lua numbers them. Iterative, so the
    parser's depth bound is never a Python recursion problem."""
    if not isinstance(value, luadata.LuaTable) or not value.entries:
        yield path, _scalar(value)
        return
    stack: list[tuple[str, Iterator[luadata.Entry], list[int]]] = [(path, iter(value.entries), [0])]
    while stack:
        prefix, entries, counter = stack[-1]
        entry = next(entries, None)
        if entry is None:
            stack.pop()
            continue
        position: int | None = None
        if entry.style is luadata.KeyStyle.POSITIONAL:
            counter[0] += 1
            position = counter[0]
        child = prefix + svmerge.path_step(entry, position)
        inner = entry.value
        if isinstance(inner, luadata.LuaTable) and inner.entries:
            stack.append((child, iter(inner.entries), [0]))
        else:
            yield child, _scalar(inner)


def _parse_path(expr: str) -> svmerge.KeyPath:
    """`--path`, read by the grammar `sv merge --key` reads (M11-25)."""
    try:
        return svmerge.parse_path(expr, "--path")
    except svmerge.MergeError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc


def _resolve_path(doc: luadata.LuaDocument, expr: str) -> luadata.LuaValue:
    path = _parse_path(expr)
    name = path.head
    found = [a for a in doc.assignments if a.name == name]
    if not found:
        raise CliError(f"no top-level variable {name!r} in this file")
    value: luadata.LuaValue = found[-1].value  # the client runs the file top to bottom
    where = name
    for step in path.steps:
        if not isinstance(value, luadata.LuaTable):
            raise CliError(f"{where} is not a table, so it has no {step.spelling}")
        matches = []
        position = 0
        for entry in value.entries:
            pos: int | None = None
            if entry.style is luadata.KeyStyle.POSITIONAL:
                position += 1
                pos = position
            if step.matches(entry, pos):
                matches.append(entry)
        if not matches:
            raise CliError(f"{where} has no key {step.spelling}")
        if len(matches) > 1:
            raise CliError(
                f"{where} has the key {step.spelling} {len(matches)} times (duplicate keys); "
                "see `wowlab sv dump` without --path"
            )
        where += step.spelling
        value = matches[0].value
    return value


def _sv_file(
    path: str,
    root: Path | None,
    flavor: str | None,
    account: str | None = None,
    character: str | None = None,
) -> Path:
    """FILE as `sv dump` takes it, plain (a `\\\\?\\` spelling named by its plain
    one, so no output shows it), looked up past the plain limit too (M13-44).
    A relative FILE that is not there, under the current folder or the
    flavor folder, is a name `sv list` lists (M16-18): `_sv_named`."""
    given = Path(_plain_arg(path))
    if given.is_absolute() or longpath.lexists(given):
        if account is not None or character is not None:
            _note("--account and --character choose among names only; {} was read as typed", path)
        return given
    _, chosen, lay = _open(root, flavor)
    direct = lay.flavor_path / given
    if longpath.lexists(direct):
        if account is not None or character is not None:
            _note("--account and --character choose among names only; {} was read as typed", path)
        return direct
    return lay.flavor_path / _sv_named(lay, chosen.folder, given, account, character).path


def _sv_file_and_root(
    path: str,
    root: Path | None,
    flavor: str | None,
    account: str | None = None,
    character: str | None = None,
) -> tuple[Path, Path | None]:
    """FILE as `_sv_file` finds it (a relative FILE not on disk is looked up
    under the flavor folder, then by name, `_sv_named`, M16-18), and the root
    of the install it lies in (M16-49): the install `--root` or discovery
    names, when FILE is under it as spelled; otherwise None, and the file is
    read with only its last part checked for a link. A path in the install
    spelled with `..`, or through another spelling of the install (a link
    into one of its folders among them, M16-65), is refused (exit 3)."""
    target = _sv_file(path, root, flavor, account, character)
    try:
        # A FILE `_sv_file` looked up under the flavor folder has had the
        # install found already; one given as a path may lie outside any.
        found, _ = _discover(root)
    except install.InstallError:
        return target, None
    install_root = found.root
    holding = _install_holding(target, install_root)
    if holding is not None and ".." in target.absolute().parts:
        raise CliError(
            f"refused: {target} has '..' in it, and wowlab reads a file in an install only "
            "by a path without '..'",
            EXIT_REFUSED,
        )
    if holding is None and _really_inside(target, install_root):
        # Another spelling of the install (a link to it, the folder named in
        # another case, a working folder reached by its real path): the walk
        # cannot start from a root the path is not spelled under, and
        # checking the last part only would follow a linked folder in it.
        raise CliError(
            f"refused: {target} is in the install at {install_root}, but not spelled under "
            "that folder; give the path as it sits under that folder",
            EXIT_REFUSED,
        )
    linked = _link_out_of_install(target, install_root) if holding is None else None
    if linked is not None:
        # A link into a folder of the install, and a linked folder in the
        # install that leads out again (M16-65): neither check above sees
        # the install, and checking the last part only would follow it.
        raise CliError(
            f"refused: {snapshot.linked_folder_words(target, linked)}",
            EXIT_REFUSED,
        )
    return target, holding


def _install_holding(path: Path, root: Path) -> Path | None:
    """`root`, absolute, when `path` names something inside it once its
    `..` are taken lexically (no link resolved); otherwise None. The path
    itself is not changed: one spelled with `..` inside the install is then
    refused by the read, never read with only its last part checked."""
    base = Path(root).absolute()
    norm = Path(os.path.normpath(path.absolute()))
    n = len(base.parts)
    if len(norm.parts) <= n or type(base)(*norm.parts[:n]) != base:
        return None
    return base


def _really_inside(path: Path, root: Path) -> bool:
    """Whether `path` names something under `root` by another spelling of
    it: some folder above the path, as spelled (`..` taken lexically), is the root's folder by
    device and inode (each `stat`ed following links, so a link to the root
    and the root named in another case on macOS or Windows count, and a
    linked folder further down cannot hide it); or, as a second check, some
    folder above its real path is. False when the root cannot be `stat`ed.
    A path that enters the install by a link and leaves it again by a linked
    folder is `_link_out_of_install`'s."""
    try:
        st = longpath.stat(root)
    except (OSError, ValueError):
        return False
    want = (st.st_dev, st.st_ino)
    # `..` taken lexically first, as `_install_holding` takes it, so a folder
    # a `..` steps back out of is not counted as holding the file.
    folders = list(reversed(Path(os.path.normpath(path.absolute())).parents))
    with contextlib.suppress(OSError, ValueError):
        folders += list(Path(longpath.realpath(path)).parents)
    for folder in folders:
        try:
            here = longpath.stat(folder)
        except OSError:
            continue
        if (here.st_dev, here.st_ino) == want:
            return True
    return False


_MAX_LINK_HOPS = 40  # links resolved on one path before the walk gives up (as POSIX's ELOOP)


def _links_met(path: Path) -> list[tuple[Path, list[Path]]]:
    """Each link an open of `path` meets on the way to its folder, with the
    folders that hold it at that moment (from the top of the drive down to
    the folder the link is in), each link resolved where it is met, its
    target read, never followed blindly (M16-65). On POSIX the parts are
    taken as typed and each `..` steps out of the folder actually reached,
    as the system's open takes it (after a link, into the link's target's
    parent); on Windows, which takes `..` as text, they are taken lexically
    first. The walk stops at the first part that is missing, is not a
    folder, or cannot be read, and after `_MAX_LINK_HOPS` links. Nothing is
    opened; each part is `lstat`ed."""
    full = path.absolute()
    if longpath.ON_WINDOWS:
        full = Path(os.path.normpath(full))
    chain = [Path(full.anchor)]  # the folders reached, the last one the current
    pending = list(full.parts[1:-1])  # the folders; the file itself is not one
    met: list[tuple[Path, list[Path]]] = []
    while pending:
        name = pending.pop(0)
        if name in ("", "."):
            continue
        if name == "..":
            if len(chain) > 1:
                chain.pop()  # every folder in `chain` is a real one, no link
            continue
        step = chain[-1] / name
        try:
            st = longpath.lstat(step)
            linked = stat.S_ISLNK(st.st_mode) or longpath.is_junction(step)
            target = longpath.readlink(step) if linked else None
        except (OSError, ValueError):
            break
        if target is not None:
            met.append((step, list(chain)))
            if len(met) > _MAX_LINK_HOPS:
                break
            if target.is_absolute():
                chain = [Path(target.anchor)]
                pending[:0] = target.parts[1:]
            else:
                pending[:0] = target.parts
            continue
        if not stat.S_ISDIR(st.st_mode):
            break
        chain.append(step)
    return met


def _link_out_of_install(path: Path, root: Path) -> Path | None:
    """The first link an open of `path` meets inside the install at `root`
    (a folder holding it is the root's folder by device and inode), as the
    open reaches it: a shortcut into a folder of the install whose way on
    leaves it by a linked folder (M16-65). None when there is none, or the
    root cannot be `stat`ed."""
    try:
        st = longpath.stat(root)
    except (OSError, ValueError):
        return None
    want = (st.st_dev, st.st_ino)
    for link, holders in _links_met(path):
        for folder in holders:
            try:
                here = longpath.stat(folder)
            except OSError:
                continue
            if (here.st_dev, here.st_ino) == want:
                return link
    return None


_SV_LAST_PART_ONLY = (
    "This file is not in an install wowlab found, so only the file itself was checked for a "
    "link: a linked folder on the way to it was not looked for."
)


def _sv_not_found(target: Path) -> CliError:
    """`sv dump` of a file that is not there (M16-19): the lister that names
    every SavedVariables file by the path `sv dump` takes."""
    return CliError(
        f"no SavedVariables file at {target}; wowlab sv list lists them, each with the "
        "path to give sv dump (relative to the flavor folder)"
    )


def _sv_named(
    lay: layout.Layout,
    folder: str,
    given: Path,
    account: str | None,
    character: str | None,
) -> layout.SavedVariablesFile:
    """M16-18: the one SavedVariables file `sv list` lists that `given`
    names: by its path relative to its account folder
    (`SavedVariables/<Addon>.lua`, `<Realm>/<Character>/SavedVariables/…`),
    by its file name, or by its addon name (a `.bak` only by its name or
    path), case-insensitively, among the files of `--account` and
    `--character` when given; `--account` alone prefers its account-wide
    file. None is exit 1 and several exit 2, each with the paths that would
    do."""
    files = list(lay.wtf_walk().saved_variables)
    if account is not None or character is not None:
        acct = _select_account(lay, account)
        files = [f for f in files if f.account == acct.folder]
        if character is not None:
            char = _select_character(acct, character)
            files = [
                f
                for f in files
                if f.realm_folder == char.realm_folder and f.character_folder == char.folder
            ]
    text = "/".join(given.parts)
    key = text.casefold()

    def in_account(f: layout.SavedVariablesFile) -> str:
        return f.path.split("/", 3)[3] if f.path.count("/") >= 3 else f.path

    found = [
        f
        for f in files
        if key in (in_account(f).casefold(), _sv_name(f).casefold())
        or (not f.backup and f.addon is not None and f.addon.casefold() == key)
    ]
    if account is not None and character is None and len(found) > 1:
        # `--account` alone names the account-wide file among several.
        found = [f for f in found if f.scope == "account"] or found
    if len(found) == 1:
        return found[0]
    if not found:
        raise CliError(
            f"no SavedVariables file {text!r}: nothing at that path in the flavor folder "
            f"{folder}, and no file `wowlab sv list` lists has that name, addon or path "
            "in its account folder"
        )
    listing = "; ".join(f.path for f in found)
    raise CliError(
        f"{text!r} names {len(found)} SavedVariables files ({listing}); pick one with "
        "--account or --character, or name it by its path in the account folder",
        EXIT_USAGE,
    )


def _sv_inside_install(
    file: str,
    root: Path | None,
    flavor: str | None,
    account: str | None = None,
    character: str | None = None,
) -> tuple[Path, Path, Path]:
    """`sv dump --install-only`: FILE as `_sv_file` finds it, and its real path
    (every link on the way resolved), refused (exit 3) unless that is under
    the real path of the install wowlab finds by itself, without `--root`
    (`WOWLAB_WOW_ROOT` or the default places, as `wowlab ui` finds it). A
    `--root` that names another install is refused too, so whoever chose the
    arguments cannot plant an install around the file. Nothing of the file is
    read first, so a refusal quotes none of it; the caller reads the real path
    that was checked, not the spelling given (returns both: the spelling for
    the report), with the real path of the install, which the read starts
    from. No install found is refused (exit 3) as well. Where
    `realpath` keeps the case a name was given in (macOS, Linux), a file of
    the install named in another case is refused: the check errs towards
    refusing; where it gives the stored case (Windows), it is allowed."""
    try:
        found, _ = _discover(None)
    except install.InstallError as exc:
        # --root cannot help here, given or not: only the variable can.
        words = _install_words(exc, _NAME_THE_INSTALL_BY_ITSELF)
        raise _keep_notes(CliError(f"refused: {words}", EXIT_REFUSED), exc, root=False) from exc
    try:
        home = longpath.realpath(found.root)
        if root is not None and longpath.realpath(_discover(root)[0].root) != home:
            raise CliError(
                f"refused: --root {root} is not the install wowlab finds, {found.root}",
                EXIT_REFUSED,
            )
        target = _sv_file(file, root, flavor, account, character)
    except install.InstallError as exc:
        raise _keep_notes(CliError(f"refused: {_install_words(exc)}", EXIT_REFUSED), exc) from exc
    try:
        real = Path(longpath.realpath(target))
        inside = real.is_relative_to(home)
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise CliError(f"refused: {target} is not in the install at {found.root}", EXIT_REFUSED)
    return target, real, Path(home)


@sv_app.command("dump", short_help="One SavedVariables file as data, value by value.")
@_handled
def sv_dump(
    file: Annotated[
        str,
        typer.Argument(
            help="A SavedVariables file: absolute, relative to the current folder, "
            "or relative to the flavor folder; else as `sv list` lists it: an addon "
            "name, a file name, or a path relative to its account folder."
        ),
    ],
    path: Annotated[
        str | None,
        typer.Option(
            "--path",
            help='One value, e.g. Var.key[3].name or Var["some key"], spelled as the dump '
            'prints it; a quoted key takes Lua 5.1 escapes (\\n, \\\\, \\", \\ddd).',
        ),
    ] = None,
    install_only: Annotated[
        bool,
        typer.Option(
            "--install-only",
            help="Refuse a FILE that is not in the install wowlab finds by itself, "
            "links followed, and a --root that names another, before reading anything. "
            "The browser view always passes this.",
        ),
    ] = False,
    account: Annotated[
        str | None,
        typer.Option(
            "--account",
            help="When FILE is a name several files have: the account folder's "
            "(its account-wide file, unless --character names one of its characters).",
        ),
    ] = None,
    character: Annotated[
        str | None,
        typer.Option(
            "--character",
            help="When FILE is a name several files have: this character folder's, "
            "as <Character> or <Realm folder>/<Character>.",
        ),
    ] = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """A SavedVariables file as data: one `path = value` line per value, in file
    order. Read as data, never run as code.

    \f
    JSON: SvDumpReport."""
    last_part_only = False
    if install_only:
        # Read the real path that was checked, opened from the real path of
        # the install one folder at a time (M16-49), so a folder swapped for
        # a link after the check is refused, not followed.
        target, real, home = _sv_inside_install(file, root, flavor, account, character)
        read_root: Path | None = home
    else:
        target, read_root = _sv_file_and_root(file, root, flavor, account, character)
        real = target
        # Outside any install wowlab found: only the file itself is checked.
        last_part_only = read_root is None
    try:
        doc = luadata.read(real, root=read_root)
    except FileNotFoundError as exc:
        raise _sv_not_found(target) from exc
    if path is not None:
        value = _resolve_path(doc, path)
        if json_out:
            _stream_dump(str(target), path, _rows([("path", path, value)]))
        else:
            for p, v in _flatten(path, value):
                _say(f"{p} = {v}")
    elif json_out:
        tops = [("variable", a.name, a.value) for a in doc.assignments]
        _stream_dump(str(target), None, _rows(tops))
    else:
        for a in doc.assignments:
            for p, v in _flatten(a.name, a.value):
                _say(f"{p} = {v}")
    if last_part_only:
        _note(_SV_LAST_PART_ONLY)
    _note(_SV_TIMING)


# ─── sv merge (§13.4, M11-09) ────────────────────────────────────────────────


class SvMergeReport(_Out):
    """`wowlab sv merge --json`: what the merge found and whether it wrote.
    Printed when unresolved conflicts stop the write, too."""

    file: str  # the target, relative to the flavor folder
    scope: Literal["account", "character"]  # the file's, from `layout`
    into: str  # the --into character, <realm folder>/<character folder>
    source: str  # "character <realm folder>/<folder>", "snapshot <id>" or "this file"
    base: str | None  # the --base snapshot, for a three-way merge
    mode: Literal["two-way", "three-way"]
    keys: list[str]  # --key as given
    take: Literal["ours", "theirs"] | None
    conflicts: list[svmerge.Conflict]  # resolved ones too (`resolved` says how)
    absent: list[svmerge.Absent]  # on one side only: never deleted from the target
    taken: list[svmerge.Taken]  # taken from the source into the target
    written: bool
    transaction: str | None  # the journal record of the write (`wowlab undo` reverses it)
    notes: list[str]  # the warnings and caveats the text output prints
    # The target's SHA-256 as this run read it, before the merge: `--expect`
    # compares a later run's with it (M14-03).
    target_sha256: str
    # The SHA-256 of the bytes the merge writes (or would write): what
    # `--expect`'s second half compares, so a source that changed after the
    # plan is refused (M13-74). None when unresolved conflicts stop the merge.
    merged_sha256: str | None
    # Every key the merge removes from the target, the highest key that goes
    # (what is under it goes with it), with the target's value (M16-12).
    removed: list[svmerge.Removed]
    replace: bool  # --replace: each --key subtree copied whole


_MERGE_PROVEN = (
    "The addon reads the merged file at its next login, may migrate it, and rewrites it at "
    "logout: the merge is proven only after one login and logout, and a subtree taken from "
    "an older version of the addon may be reset by it."
)
_ABSENT_NOTE = (
    "A key listed as absent is on one side only. Many addons leave out values equal to their "
    "defaults [verify], which wowlab cannot see, so absent is not deleted: it is never "
    "removed from the target."
)
_NO_LOGIN = "no login between the two snapshots; the loader was not re-checked"
_PROBE_KEPT = "probe kept from the target: it counts that character's logins (§13.4)"
# The merge notes that are wowlab's own words; the others name a path or a
# snapshot and print as they are (M16-15).
_MERGE_OWN_NOTES = frozenset({_PROBE_KEPT, _ABSENT_NOTE, _MERGE_PROVEN})
_LOADER_BUG = (
    "the target addon would load its defaults at the next login and save them at logout, "
    "overwriting the merge (the Forever beta had such a SavedVariables loader bug, "
    "https://github.com/nobewayo/ForeverSVFix). Log in and out once with the lab-addon "
    "enabled and check again, or pass --force-loader-check to merge anyway"
)


_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


def _merge_expect(expect: str | None) -> tuple[str | None, str | None]:
    """`sv merge --expect TARGET:MERGED`, split into its two SHA-256s (M13-74).
    Both are required: the target alone does not pin what is written."""
    if expect is None:
        return None, None
    target, sep, merged = expect.partition(":")
    if not sep or not _SHA256.fullmatch(target) or not _SHA256.fullmatch(merged):
        raise CliError(
            f"--expect {expect!r} is not TARGET:MERGED, the two lowercase SHA-256s "
            "target_sha256 and merged_sha256 of an earlier run's --json",
            EXIT_USAGE,
        )
    return target, merged


def _into_character(account: layout.Account, spec: str, option: str) -> layout.Character:
    """A character folder for --into/--from; never a `<Realm>/<First>/` twin
    of `<digits>/<First>-<Second>/` folders (§13.4)."""
    char = _select_character(account, spec)
    if char.shape == "realm_name" and char.twins:
        raise CliError(
            f"{option} {spec!r} is the folder {char.realm_folder}/{char.folder}, a twin of "
            f"{', '.join(char.twins)}; SavedVariables are kept in the <digits>/<First>-<Second> "
            f"folder, so name that one with {option}",
            EXIT_USAGE,
        )
    return char


def _is_character(account: layout.Account, spec: str) -> bool:
    realm, _, folder = spec.rpartition("/")
    for r in account.realms:
        for c in r.characters:
            if realm and c.realm_folder == realm and c.folder == folder:
                return True
            if not realm and c.folder.casefold() == spec.casefold():
                return True
    return False


def _character_sv(
    files: Sequence[layout.SavedVariablesFile],
    account: layout.Account,
    char: layout.Character,
    name: str,
) -> layout.SavedVariablesFile | None:
    """The file `name` in `char`'s SavedVariables/ in `account` (another
    account may hold a character folder of the same name), or None. More
    than one match (names differing only in case) is an error, never read
    as "none"."""
    mine = [
        f
        for f in files
        if f.scope == "character"
        and not f.backup
        and f.account == account.folder
        and f.realm_folder == char.realm_folder
        and f.character_folder == char.folder
    ]
    exact = [f for f in mine if _sv_name(f) == name]
    match = exact or [f for f in mine if _sv_name(f).casefold() == name.casefold()]
    if len(match) > 1:
        raise CliError(
            f"{char.path}/SavedVariables/ holds {len(match)} files named like {name!r}: "
            + ", ".join(f.path for f in match)
        )
    return match[0] if match else None


# The parts of a character's SavedVariables path that `layout` and
# `_character_sv` match case-folded: WTF/Account/<account>/<realm or
# digits>/<character>/SavedVariables/<file> (M11-31).
_CHARACTER_SV_PARTS = 7
_FOLDED_WTF = "WTF".casefold()
_FOLDED_ACCOUNT = "Account".casefold()
_FOLDED_SAVED_VARIABLES = "SavedVariables".casefold()


def _is_character_lab_file(rel: str) -> bool:
    """Whether `rel`, a flavor-relative path as a guard journal record spells
    it, can be a file the loader check reads: a character's own lab-addon
    file, which `_character_sv` returns for any `--into` character (§13.4;
    M11-31, conductor ruling on M11-31T). That is
    `WTF/Account/<account>/<realm or digits>/<character>/SavedVariables/WowLab.lua`,
    seven parts, with `WTF`, `Account`, `SavedVariables` and the file name
    compared case-folded: `layout` finds each folder by its case-folded name,
    and `_character_sv` falls back to a case-folded file name when none is
    spelled exactly, while the journal records the spelling on disk. The
    account-wide `WowLab.lua`, a `WowLab.lua.bak` beside the character's file
    and every other file are not it. Decided from the path alone: `snap gc`
    takes no install and reads none. The split stops after seven separators,
    so a longer path yields eight parts and fails the length test without
    splitting a crafted journal path of any length in full."""
    parts = rel.split("/", _CHARACTER_SV_PARTS)
    return (
        len(parts) == _CHARACTER_SV_PARTS
        and parts[0].casefold() == _FOLDED_WTF
        and parts[1].casefold() == _FOLDED_ACCOUNT
        and parts[5].casefold() == _FOLDED_SAVED_VARIABLES
        and parts[6].casefold() == svmerge.LAB_ADDON_FILE.casefold()
    )


def _merge_target(
    lay: layout.Layout,
    files: Sequence[layout.SavedVariablesFile],
    file: str,
    account: layout.Account,
    char: layout.Character,
) -> layout.SavedVariablesFile:
    """FILE: a bare name in the --into character's SavedVariables/, then the
    account's; or a path as `sv dump` takes it, whose scope `layout` gives."""
    usable = [f for f in files if not f.backup and f.account == account.folder]
    if "/" not in file and os.sep not in file:
        found = _character_sv(usable, account, char, file)
        if found is not None:
            return found
        acct = [f for f in usable if f.scope == "account"]
        match = [f for f in acct if _sv_name(f) == file] or [
            f for f in acct if _sv_name(f).casefold() == file.casefold()
        ]
        if len(match) == 1:
            return match[0]
        raise CliError(
            f"no SavedVariables file {file!r} in {char.path}/SavedVariables/ or in "
            f"WTF/Account/{account.folder}/SavedVariables/"
        )
    given = Path(file)
    if not given.is_absolute() and not longpath.lexists(given):
        given = lay.flavor_path / given
    absolute = Path(os.path.normpath(given.absolute()))
    if not _is_inside(absolute, lay.flavor_path):
        raise CliError(f"{file} is not inside the flavor folder {lay.flavor_path}", EXIT_USAGE)
    rel = absolute.relative_to(lay.flavor_path).as_posix()
    every = [f for f in files if f.path == rel] or [
        f for f in files if f.path.casefold() == rel.casefold()
    ]
    if len(every) != 1:
        raise CliError(f"{file} is not a SavedVariables file wowlab lists (see `wowlab sv list`)")
    target = every[0]
    if target.backup:
        raise CliError(
            f"{file} is the client's backup of the previous write; not merged", EXIT_USAGE
        )
    if target.account != account.folder:
        raise CliError(f"{file} is in account {target.account}, not {account.folder}", EXIT_USAGE)
    if target.scope == "character" and (
        target.realm_folder != char.realm_folder or target.character_folder != char.folder
    ):
        raise CliError(
            f"{file} belongs to {target.realm_folder}/{target.character_folder}, not to the "
            f"--into character {char.realm_folder}/{char.folder}",
            EXIT_USAGE,
        )
    return target


def _read_sv(lay: layout.Layout, rel: str) -> bytes:
    """A SavedVariables file's bytes, `rel` under the flavor folder, opened
    from the install root one folder at a time (M16-65), so a link anywhere
    on the way is refused with the folder named, and without blocking on
    anything but a regular file (L1)."""
    return _listed(snapshot.read_regular_file, lay, rel, limit=luadata.MAX_FILE_BYTES)


def _snapshot_sv(store: snapshot.SnapshotStore, snapshot_id: str, rel: str, folder: str) -> bytes:
    manifest = store.show(snapshot_id)
    held = f"{manifest.flavor_folder or folder}/{rel}"
    entry = manifest.entry(held)
    if entry is None or entry.kind != "file" or entry.sha256 is None:
        raise CliError(f"snapshot {manifest.id} does not hold {held}")
    return store.read_object(entry.sha256, size=entry.size)


def _same_root(a: str, b: Path) -> bool:
    if Path(a) == b:
        return True
    try:
        return longpath.resolve(a) == longpath.resolve(b)
    except (OSError, RuntimeError):
        return False


_LAB_WROTE = "the Lab wrote WowLab.lua after that snapshot; the loader was not re-checked"
_CLOCK_AHEAD = (
    "later than now, so the loader check ignored it (a clock that ran ahead must not "
    "turn the check off)"
)

# A point in the order the loader check compares states in: a snapshot's
# (created_at, id), the key `holding` sorts by.
_When = tuple[str, str]


class _LabWrite(NamedTuple):
    """A committed guard write that changed the file the loader check reads."""

    at: _When  # the point it happened right after
    record: str  # its journal record id
    after: str | None  # SHA-256 of what it wrote; None: it removed the file


def _stamp_now() -> str:
    """The current time as manifests and journal records spell theirs."""
    when = datetime.datetime.now(datetime.UTC)
    return f"{when:%Y-%m-%dT%H:%M:%S}.{when.microsecond:06d}Z"


def _lab_writes(
    store: snapshot.SnapshotStore,
    flavor_path: Path,
    rel: str,
    when_of: dict[str, _When],
    now: str,
    notes: list[str],
) -> list[_LabWrite]:
    """Committed guard writes that changed the file `rel` (the full path
    relative to the flavor folder, never just its name) in this flavor,
    oldest first. Records that rolled back, did not finish rolling back, or
    are still open (killed) are left out: only a committed write is the
    Lab's. A write happened right after its pre-write snapshot; when that
    snapshot is not in `when_of` (gone, or dated in the future), after the
    record's own `created_at`. A record dated after `now` is ignored, with a
    note naming it (§13.4, owner ruling amending "A Lab write is not a
    loader failure")."""
    out: list[_LabWrite] = []
    for record in guard.history(store=store.path):
        if record.state != "committed" or not _same_root(record.flavor_path, flavor_path):
            continue
        changes = [c for c in record.paths if c.path == rel and c.before != c.after]
        if not changes:
            continue
        if record.created_at > now:
            notes.append(
                f"{rel}: journal record {record.id} is dated {record.created_at}, {_CLOCK_AHEAD}."
            )
            continue
        at = when_of.get(record.snapshot_id, (record.created_at, ""))
        out.append(_LabWrite(at, record.id, changes[-1].after))
    return out


def _latest_in(writes: Sequence[_LabWrite], older: _When, newer: _When | None) -> _LabWrite | None:
    """W: the latest Lab write between the states `older` (a snapshot) and
    `newer` (a later snapshot; None: the disk now), or None. A write right
    after snapshot S lies after S and before the next snapshot, so a pair
    whose newer side is S does not span it."""
    found: _LabWrite | None = None
    for w in writes:
        if (
            older <= w.at
            and (newer is None or w.at < newer)
            and (found is None or w.at >= found.at)
        ):
            found = w
    return found


def _loader_check(
    store: snapshot.SnapshotStore,
    inst: install.Install,
    chosen: install.Flavor,
    lay: layout.Layout,
    files: Sequence[layout.SavedVariablesFile],
    account: layout.Account,
    char: layout.Character,
) -> tuple[str | None, list[str]]:
    """(the reason to refuse, or None; notes) from the --into character's
    `WowLab.lua` on disk, the newest snapshot of it, and the newest snapshot
    together with the newest older one whose bytes differ from it (§13.4 as
    ruled on 2026-09-29, and the owner rulings for M11-24). A comparison
    that spans a committed guard write W to that file is made from the state
    W left, never skipped: the newer side byte-identical to W's bytes passes
    with a note (no session since W); otherwise W's bytes, kept in the
    store, are the older side; W's bytes that cannot be read refuse.
    Snapshots and journal records dated in the future are ignored with a
    note. The notes are returned with a refusal too. Reads only."""
    name = svmerge.LAB_ADDON_FILE
    found = _character_sv(files, account, char, name)
    who = f"{char.realm_folder}/{char.folder}"
    if found is None:
        return None, [
            f"No {name} for {who}: the SavedVariables loader was not checked. With the "
            "lab-addon installed (`wowlab addon install lab`), one login and logout writes it."
        ]
    rel = found.path
    disk = _read_sv(lay, rel)
    try:
        probe = svmerge.read_probe(luadata.parse(disk))
    except luadata.LuaDataError as exc:
        return f"{rel} is not data the parser accepts ({exc}), so its probe cannot be read", []
    if probe.lost:
        return (
            f"{rel}: probe.lost is true. At its last load the lab-addon found WowLabCharDB "
            f"without its probe, which is what a SavedVariables loader failure leaves: "
            f"{_LOADER_BUG}.",
            [],
        )
    if probe.loads is None:
        return f"{rel} holds no probe.loads, so the loader cannot be checked; {_LOADER_BUG}.", []
    disk_loads: int = probe.loads
    disk_sha = hashlib.sha256(disk).hexdigest()
    now = _stamp_now()
    notes: list[str] = []  # printed with a refusal too
    passed: list[str] = []  # rule-1 notes: printed only when the whole check passes
    held = f"{chosen.folder}/{rel}"
    when_of: dict[str, _When] = {}
    holding: list[tuple[snapshot.Manifest, snapshot.Entry]] = []
    for m in store.list_lenient().manifests:
        if m.flavor_folder != chosen.folder or not _same_root(m.install_root, Path(inst.root)):
            continue
        entry = m.entry(held)
        holds = entry is not None and entry.kind == "file" and entry.sha256 is not None
        if m.created_at > now:
            if holds:
                notes.append(f"{rel}: snapshot {m.id} is dated {m.created_at}, {_CLOCK_AHEAD}.")
            continue
        when_of[m.id] = (m.created_at, m.id)
        if holds:
            assert entry is not None
            holding.append((m, entry))
    holding.sort(key=lambda pair: (pair[0].created_at, pair[0].id))
    writes = _lab_writes(store, lay.flavor_path, rel, when_of, now, notes)

    def when(m: snapshot.Manifest) -> _When:
        return (m.created_at, m.id)

    def loads_in(m: snapshot.Manifest, entry: snapshot.Entry) -> int | str:
        """`probe.loads` in the snapshot, or the reason to refuse."""
        assert entry.sha256 is not None
        data = store.read_object(entry.sha256, size=entry.size)
        try:
            loads = svmerge.read_probe(luadata.parse(data)).loads
        except luadata.LuaDataError as exc:
            return f"{rel} in snapshot {m.id} is not data the parser accepts ({exc})"
        if loads is None:
            return f"{rel} in snapshot {m.id} holds no probe.loads, so loads cannot be compared"
        return loads

    def from_lab_write(
        w: _LabWrite, older: str, newer_sha: str, newer_loads: Callable[[], int | str], newer: str
    ) -> str | None:
        """The comparison `older` -> `newer`, which spans W, made from the
        state W left. None: it passes (with the note when no session ran
        since W); else the reason to refuse."""
        if newer_sha == w.after:  # rule 1, before anything is read
            passed.append(
                f"{rel}: {_LAB_WROTE} (snapshot {older}, then journal record {w.record})."
            )
            return None
        unreadable = (
            f"{rel}: the Lab wrote it after snapshot {older} (journal record {w.record}) and "
            f"{newer} holds other bytes, so the loader is checked from what that write left, "
            "which cannot be read"
        )
        again = (
            "Take a snapshot now (wowlab snap create), log in and out once with the "
            "lab-addon enabled, take another snapshot, then check again; or pass "
            "--force-loader-check if you are sure the last session loaded."
        )
        if w.after is None:
            return f"{unreadable}: the write removed the file. {again}"
        try:
            data = store.read_object(w.after, size=luadata.MAX_FILE_BYTES)
            before = svmerge.read_probe(luadata.parse(data)).loads
        except (snapshot.SnapshotError, OSError) as exc:
            return f"{unreadable}: {exc}. {again}"
        except luadata.LuaDataError as exc:
            return f"{unreadable}: it is not data the parser accepts ({exc}). {again}"
        if before is None:
            return f"{unreadable}: it holds no probe.loads. {again}"
        after = newer_loads()
        if isinstance(after, str):
            return after
        if after > before:
            return None
        went = "went down" if after < before else "did not go up"
        return (
            f"{rel}: probe.loads {went}, from {before} in what the Lab wrote (journal record "
            f"{w.record}, after snapshot {older}) to {after} in {newer}. It goes up at every "
            f"load of the lab-addon, so the client did not load this character's "
            f"SavedVariables back in the session since: {_LOADER_BUG}."
        )

    if holding:
        # The disk against the newest snapshot (ruling of 2026-09-29): lower
        # `loads` means the session since did not load its SavedVariables.
        # Equal `loads` with other bytes is that session with the file edited
        # after the snapshot, and passes.
        newest, newest_entry = holding[-1]
        if disk_sha != newest_entry.sha256:
            w = _latest_in(writes, when(newest), None)
            if w is not None:
                refusal = from_lab_write(
                    w, newest.id, disk_sha, lambda: disk_loads, "the file on disk"
                )
                if refusal is not None:
                    return refusal, notes
            else:
                newest_loads = loads_in(newest, newest_entry)
                if isinstance(newest_loads, str):
                    return newest_loads, notes
                if disk_loads < newest_loads:
                    return (
                        f"{rel}: loads on disk ({disk_loads}) went down from the newest snapshot "
                        f"({newest_loads}): the last session's SavedVariables did not load "
                        f"(snapshot {newest.id}); {_LOADER_BUG}.",
                        notes,
                    )
    if len(holding) < 2:
        return None, [
            *notes,
            *passed,
            f"Fewer than two snapshots hold {rel}, so probe.loads was not compared across "
            "sessions (`wowlab snap create` before and after a login makes the check possible).",
        ]
    # The newest snapshot against the newest older one whose bytes differ
    # (owner ruling for M11-24): snapshots with the same bytes are one state,
    # and guard snapshots before every write, whatever the file.
    new, new_entry = holding[-1]
    assert new_entry.sha256 is not None
    differing = [pair for pair in holding[:-1] if pair[1].sha256 != new_entry.sha256]
    if not differing:
        old = holding[-2][0]
        return None, [
            *notes,
            *passed,
            f"{rel}: {_NO_LOGIN} (snapshots {old.id} and {new.id} hold the same bytes).",
        ]
    old, old_entry = differing[-1]
    w = _latest_in(writes, when(old), when(new))
    if w is not None:
        refusal = from_lab_write(
            w, old.id, new_entry.sha256, lambda: loads_in(new, new_entry), f"snapshot {new.id}"
        )
        return refusal, notes if refusal is not None else [*notes, *passed]
    before = loads_in(old, old_entry)
    if isinstance(before, str):
        return before, notes
    after = loads_in(new, new_entry)
    if isinstance(after, str):
        return after, notes
    if after > before:
        return None, [*notes, *passed]
    went = "went down" if after < before else "did not go up"
    same = "" if old is holding[-2][0] else " (the snapshots after it hold the same bytes)"
    return (
        f"{rel}: probe.loads {went}, from {before} in snapshot {old.id}{same} to {after} in "
        f"snapshot {new.id}. It goes up at every load of the lab-addon, so the client did not "
        f"load this character's SavedVariables back in between: {_LOADER_BUG}.",
        notes,
    )


def _lab_written(store: snapshot.SnapshotStore, flavor_path: Path) -> list[Path]:
    """Files the guard journal records as last written by wowlab in this
    flavor: `luadata.serialize` never takes its style from them."""
    last: dict[str, str | None] = {}
    for record in guard.history(store=store.path):
        if record.state != "committed" or Path(record.flavor_path) != flavor_path:
            continue
        for change in record.paths:
            last[change.path] = change.after
    return [flavor_path / p for p, after in last.items() if after is not None]


def _print_merge(show: Callable[[str], None], report: SvMergeReport) -> None:
    base = f", base snapshot {report.base}" if report.base else ""
    show(
        f"Merge {report.file} ({report.scope}, {report.mode}{base}) from {report.source} "
        f"into {report.into}"
    )
    if report.keys:
        how = " (copied whole: --replace)" if report.replace else ""
        show(f"  limited to --key {', '.join(report.keys)}{how}")
    if report.removed:
        show(f"  {len(report.removed)} entries or settings removed from the target:")
        for r in report.removed:
            if r.entries is not None:
                was, now = r.entries
                entries = f"{was} entr{'y' if was == 1 else 'ies'} -> {now}"
                show(f"    {r.path}  list replaced, {entries} (was {r.value})")
            else:
                show(f"    {r.path}  (was {r.value})")
    if report.conflicts:
        how = {None: "unresolved", "ours": "kept ours", "theirs": "took theirs"}[report.take]
        show(f"  {len(report.conflicts)} conflict(s), {how}:")
        for c in report.conflicts:
            base_text = f", base {c.base}" if c.base is not None else ""
            show(f"    {c.path}: ours {c.ours}, theirs {c.theirs}{base_text}")
    if report.absent:
        show(f"  {len(report.absent)} key(s) absent from one side (never deleted):")
        for a in report.absent:
            show(f"    {a.path}  (missing from {a.missing_from})")
    if report.taken:
        show(f"  {len(report.taken)} value(s) taken from the source:")
        for t in report.taken:
            show(f"    {t.path} = {t.value}")


@sv_app.command(
    "merge", short_help="Merge one SavedVariables file into a character's (asks first)."
)
@_handled
def sv_merge(
    file: Annotated[
        str,
        typer.Argument(
            help="A SavedVariables file: a name (WowLab.lua), looked up in the --into "
            "character's SavedVariables/ and then the account's; or a path as `sv dump` takes it."
        ),
    ],
    into: Annotated[
        str,
        typer.Option(
            "--into",
            help="The target character folder (<digits>/<First>-<Second> on Forever); its "
            "WowLab.lua is read for the loader check.",
        ),
    ],
    from_: Annotated[
        str | None,
        typer.Option(
            "--from",
            help="The source: a character folder (per-character files only) or a snapshot "
            "id. Left out, --key SRC=DST merges SRC into DST within the file (a missing DST, or an "
            "empty one when the source's is a table, is copied whole).",
        ),
    ] = None,
    base: Annotated[
        str | None,
        typer.Option(
            "--base",
            help="A snapshot holding the common ancestor: with --from SNAPSHOT, a three-way merge.",
        ),
    ] = None,
    key: Annotated[
        list[str] | None,
        typer.Option(
            "--key",
            help='Merge only this subtree, e.g. Var.profile or Var["some key"], spelled as sv '
            'dump prints it; a quoted key takes Lua 5.1 escapes (\\n, \\\\, \\", \\ddd). '
            "SRC=DST merges SRC into DST. Inside it the usual rules apply: differing keys are "
            "conflicts, and keys on one side only are listed and left as they are. If the "
            "target has no DST at all (or an empty one, when the source's is a table), the "
            "source's subtree is copied whole. "
            "--replace copies it whole and removes what the target alone had. Repeatable.",
        ),
    ] = None,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Copy each --key subtree whole instead of merging inside it: keys only the "
            "target has there are removed, and the plan lists each one. Not with --base.",
        ),
    ] = False,
    take: Annotated[
        Literal["ours", "theirs"] | None,
        typer.Option(
            "--take", help="Resolve every conflict with the target's or the source's value."
        ),
    ] = None,
    force_loader_check: Annotated[
        bool,
        typer.Option(
            "--force-loader-check", help="Merge even when the SavedVariables loader check refuses."
        ),
    ] = False,
    expect: Annotated[
        str | None,
        typer.Option(
            "--expect",
            metavar="TARGET:MERGED",
            help="Write only if the target still has the SHA-256 TARGET and the merge would "
            "write bytes with the SHA-256 MERGED (target_sha256 and merged_sha256 in an "
            "earlier run's --json): else refused, exit 3, nothing written. For a plan "
            "shown by one run and confirmed by another.",
        ),
    ] = None,
    account: AccountOpt = None,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Merge one SavedVariables file into a character's copy, by key path, and
    write it through the write gate (client closed, snapshot first, `wowlab undo`
    reverses it). Read and written as data, never run.

    Two-way (--from a character or a snapshot): every key whose values differ is a
    conflict. Three-way (--from SNAPSHOT --base SNAPSHOT): a change on one side is
    taken, the same change on both once, different changes are conflicts. A list
    (the entries 1, 2, 3, ...) is one value, so --take theirs takes the whole list;
    to merge the entries at one position on both sides, name it with --key
    Var.list[n] (positions can differ between characters; check with sv dump
    first). Conflicts are listed and nothing is written unless --take resolves
    them; exit 1. A key outside a list that is on one side only is listed as absent
    and never deleted. The plan lists everything the merge removes: list entries the
    list it takes drops or changes, keys under a table that the source's value
    replaces, and the keys --replace drops. An account-wide file is shared by every
    character, so merging it from another character is refused (exit 2); use --from
    SNAPSHOT, or --key SRC=DST to merge between the per-character keys an addon
    keeps inside it. Before writing, the --into character's WowLab.lua is checked
    for a SavedVariables loader failure (exit 3).

    \f
    JSON: SvMergeReport."""
    keys = list(key or [])
    expect_target, expect_merged = _merge_expect(expect)
    try:
        same_path = svmerge.check_keys(keys)
    except svmerge.MergeError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    if from_ is None:
        for given, same in zip(keys, same_path, strict=True):
            if same:
                raise CliError(
                    f"--key {given} names the same path on both sides, which needs --from: "
                    "within one file, copy with --key SRC=DST",
                    EXIT_USAGE,
                )
    if from_ is None and base is not None:
        raise CliError(
            "--base names the common ancestor of --from SNAPSHOT; give --from", EXIT_USAGE
        )
    if replace and not keys:
        raise CliError("--replace copies the --key subtrees whole; give --key", EXIT_USAGE)
    if replace and base is not None:
        raise CliError(
            "--replace copies a subtree whole, so it takes no --base: leave out one of them",
            EXIT_USAGE,
        )
    if from_ is None and not keys:
        raise CliError(
            "give --from CHARACTER|SNAPSHOT, or --key SRC=DST to copy within the file", EXIT_USAGE
        )
    store = snapshot.SnapshotStore()
    inst, chosen, lay = _open(root, flavor)
    acct = _select_account(lay, account)
    char = _into_character(acct, into, "--into")
    files = lay.saved_variables()
    target = _merge_target(lay, files, _plain_arg(file), acct, char)
    source_char: layout.Character | None = None
    source_snap: str | None = None
    if from_ is not None:
        if _is_character(acct, from_):
            source_char = _into_character(acct, from_, "--from")
        else:
            try:
                source_snap = store.resolve_id(from_)
            except snapshot.SnapshotNotFoundError as exc:
                raise CliError(
                    f"--from {from_!r} is neither a character folder in account {acct.folder} "
                    f"nor a snapshot id ({exc})",
                    EXIT_USAGE,
                ) from exc
    if source_char is not None:
        if target.scope == "account":
            raise CliError(
                f"{target.path} is account-wide: every character on account {acct.folder} "
                "shares this one file, so there is nothing to merge between two characters. "
                "Use --from SNAPSHOT (another machine or an earlier state), or --key SRC=DST "
                "to copy between per-character keys inside it",
                EXIT_USAGE,
            )
        if source_char.path == char.path:
            raise CliError("--from and --into name the same character", EXIT_USAGE)
        if base is not None:
            raise CliError("--base goes with --from SNAPSHOT, not a character", EXIT_USAGE)
    base_id = store.resolve_id(base) if base is not None else None
    target_path = lay.flavor_path / target.path
    ours_bytes = _read_sv(lay, target.path)
    expected = hashlib.sha256(ours_bytes).hexdigest()
    if expect_target is not None and expect_target != expected:
        # Final already: this run would merge into other bytes than the plan
        # it is to confirm. The same compare is made again under the write
        # gate's lock, below, for a change made after this read.
        raise CliError(
            f"{target.path} has SHA-256 {expected}, not the expected {expect_target}: it changed "
            "after the plan was made, so nothing was written; run the command again to see "
            "the new plan",
            EXIT_REFUSED,
        )
    if source_char is not None:
        found = _character_sv(files, acct, source_char, _sv_name(target))
        if found is None:
            raise CliError(f"{source_char.path}/SavedVariables/ has no {_sv_name(target)}")
        theirs_bytes = _read_sv(lay, found.path)
        source = f"character {source_char.realm_folder}/{source_char.folder}"
    elif source_snap is not None:
        theirs_bytes = _snapshot_sv(store, source_snap, target.path, chosen.folder)
        source = f"snapshot {source_snap}"
    else:
        theirs_bytes = ours_bytes
        source = "this file"
    base_bytes = (
        _snapshot_sv(store, base_id, target.path, chosen.folder) if base_id is not None else None
    )

    refusal, notes = _loader_check(store, inst, chosen, lay, files, acct, char)
    if refusal is not None:
        if not force_loader_check:
            for n in notes:  # e.g. an ignored future-dated snapshot or record
                _say_err(n)
            raise CliError(f"refused by the loader check: {refusal}", EXIT_REFUSED)
        notes.append(f"The loader check was overridden by --force-loader-check: {refusal}")

    ours = luadata.parse(ours_bytes)
    theirs = ours if source_char is None and source_snap is None else luadata.parse(theirs_bytes)
    base_doc = luadata.parse(base_bytes) if base_bytes is not None else None
    try:
        result = svmerge.merge(ours, theirs, base=base_doc, keys=keys, take=take, replace=replace)
    except svmerge.MergeError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    lab_file = _character_sv(files, acct, char, svmerge.LAB_ADDON_FILE)
    is_lab_file = lab_file is not None and lab_file.path == target.path
    if is_lab_file:
        # The --into character's own WowLab.lua: its probe counts that
        # character's logins, and the next loader check reads it (§13.4,
        # owner ruling for M11-24).
        try:
            result, kept = svmerge.keep_probe(ours, result)
        except svmerge.MergeError as exc:
            raise CliError(str(exc), EXIT_USAGE) from exc
        if kept:
            notes.append(_PROBE_KEPT)
    if result.absent:
        notes.append(_ABSENT_NOTE)
    show = _say if not json_out else _say_err
    data = (
        None
        if result.unresolved
        else luadata.serialize(
            result.document, target=target_path, lab_written=_lab_written(store, chosen.path)
        )
    )
    merged = hashlib.sha256(data).hexdigest() if data is not None else None

    def report(*, written: bool, transaction: str | None = None) -> SvMergeReport:
        return SvMergeReport(
            file=target.path,
            scope=target.scope,
            into=f"{char.realm_folder}/{char.folder}",
            source=source,
            base=base_id,
            mode=result.mode,
            keys=keys,
            take=take,
            conflicts=list(result.conflicts),
            absent=list(result.absent),
            taken=list(result.taken),
            removed=list(result.removed),
            replace=replace,
            written=written,
            transaction=transaction,
            notes=[*notes, _MERGE_PROVEN] if written else notes,
            target_sha256=expected,
            merged_sha256=merged,
        )

    # The notes printed with the plan, before the prompt (M16-12): not again
    # after it.
    shown: list[str] = []

    def finish(done: SvMergeReport, line: str) -> None:
        if json_out:
            _emit(done)
            return
        _print_merge(_say, done)
        for n in done.notes:  # wowlab's own notes in player words; any other as it is
            if n not in shown:
                _say_err(_player_copy(n) if n in _MERGE_OWN_NOTES else n)
        _say(line)

    if expect_merged is not None and expect_merged != merged:
        # The target is as the plan read it, but what this run would write is
        # not what the plan showed: its source (a live character file, or a
        # snapshot id prefix that now resolves elsewhere) changed (M13-74).
        # With --json the report of this run's plan is printed, so a caller
        # sees what changed; nothing is written either way.
        if json_out:
            _emit(report(written=False))
        now = "has unresolved conflicts" if merged is None else f"would write SHA-256 {merged}"
        raise CliError(
            f"the merge into {target.path} {now}, not the expected {expect_merged}: what it "
            "merges from changed after the plan was made, so nothing was written; run the "
            "command again to see the new plan",
            EXIT_REFUSED,
        )
    if data is None:
        finish(
            report(written=False),
            f"{len(result.conflicts)} conflict(s) left unresolved: nothing was written. "
            "Resolve them with --take ours|theirs, or limit the merge with --key.",
        )
        raise typer.Exit(EXIT_ERROR)
    if data == ours_bytes:
        finish(report(written=False), f"Nothing to change: {target.path} already holds the merge.")
        return
    if not yes:
        _print_merge(show, report(written=False))
        show(f"  replace  {target.path}  ({_bytes(len(data))})")
        # The loader check's notes and the others belong to the plan, so
        # they print before the prompt (M16-12).
        for n in notes:
            _say_err(_player_copy(n) if n in _MERGE_OWN_NOTES else n)
        shown.extend(notes)
        show(_player_copy(_MERGE_PROVEN))
        shown.append(_MERGE_PROVEN)
    try:
        _confirm("Write the merge?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(written=False))
        raise
    with guard.transaction(chosen, label=f"sv merge {target.path}", store=store.path) as tx:
        if is_lab_file:
            # The next loader check compares from what this write leaves
            # (§13.4, owner ruling amending "A Lab write is not a loader
            # failure"): keep those bytes as a store object. A failure here
            # stops the transaction before anything is written.
            try:
                store.put_object(data)
            except snapshot.SnapshotError as exc:
                raise CliError(
                    f"cannot keep the merged {target.path} in the snapshot store ({exc}); "
                    "nothing was written"
                ) from exc
        tx.write(target.path, data)
        if expect_target is not None and tx.plan[0].before != expect_target:
            # Under the write gate's locks: raising here rolls the write back.
            raise CliError(
                f"{target.path} changed after the plan was made (its SHA-256 is "
                f"{tx.plan[0].before}, not the expected {expect_target}), so the merge was "
                "rolled back; run the command again to see the new plan",
                EXIT_REFUSED,
            )
        if expect_merged is not None and tx.plan[0].after != expect_merged:
            # The SHA-256 the gate took of the bytes `tx.write` received,
            # checked under the same locks (M13-74).
            raise CliError(
                f"the merge into {target.path} would write SHA-256 {tx.plan[0].after}, not "
                f"the expected {expect_merged}, so it was rolled back; run the command again "
                "to see the new plan",
                EXIT_REFUSED,
            )
        if [item.before for item in tx.plan] != [expected]:
            raise CliError(
                f"{target.path} changed after it was read, so the merge was rolled back; "
                "run the command again"
            )
    record = guard.history(store=store.path)[-1].id
    finish(
        report(written=True, transaction=record),
        f"Merged into {target.path}: {len(result.taken)} value(s) taken. `wowlab undo` puts "
        "back what was there before.",
    )
