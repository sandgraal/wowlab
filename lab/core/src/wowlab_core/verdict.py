"""The safe-to-edit answer for one path: a level, whether a restore could put
it back, and the words `explain` and `tree --explain` print (M13-21,
`docs/LAB_PLAN.md` §15.6).

The level comes from the file map's Edit cell, and the mapping is fixed:
`gate` and `gate (…)` are `gate`; `no` is `no`; `—`, a folder row, is
`folder`; `n/a` is `not_applicable`. `unclassified` means only that the file
map has no row for the path. An Edit cell outside that set is an error, so a
new cell in `filemap.toml` fails the test that maps every cell.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core.filemap import FileMapEntry

Level = Literal["gate", "no", "folder", "not_applicable", "unclassified"]

_GATE = "gate"
_GATE_WORDS = "yes, through wowlab's write gate only, with the client closed"
_SERVER_MAY_REPLACE = "(server may replace)"
_UNCLASSIFIED_WORDS = "no file-map row; wowlab does not know what this file is"
_UNCLASSIFIED_RESTORED = (
    "; a whole snapshot restore puts it back like any file here (it skips only Edit `no` files)"
)
_TIER_WORDS = {
    "A": "A: ordinary addon-user behaviour",
    "A (read)": "A (read): reading it is ordinary addon-user behaviour",
    "A (read, later wave)": "A (read, later wave): reading it is ordinary; wowlab does not read it yet",
    "B": "B: works, but Blizzard does not support it and a patch can reset it",
    "—": "—: nothing wowlab does with it",
}


# ─── the write gate's path rule, copied ──────────────────────────────────────
# A copy of `guard._check_rel` for a file path with executables refused, so
# `restorable` says exactly what the gate would write. `guard.py` is
# load-bearing; its private rule is copied here rather than imported, and
# `test_verdict.py::test_restorable_agrees_with_the_write_gate` fails when
# the two drift apart.

_ROOTS = ("WTF", "Interface", "Fonts")
_ADDONS = "AddOns"
_EXECUTABLE_SUFFIXES = (
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
_VERSIONED_SHARED_LIBRARY = re.compile(r"\.so(\.\d+)+\Z", re.IGNORECASE)
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{device}{n}" for device in ("com", "lpt") for n in (*"0123456789", "¹", "²", "³")}
)
_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*\\\x7f') | frozenset(chr(n) for n in range(32))
_MAX_NAME_BYTES = 255


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def _is_executable(name: str) -> bool:
    folded = name.casefold()
    return folded.endswith(_EXECUTABLE_SUFFIXES) or bool(_VERSIONED_SHARED_LIBRARY.search(name))


def _gate_writes(rel: str) -> bool:
    """Whether the gate accepts `rel` (flavor-relative, `/`-separated) as a
    file it may write: `guard._check_rel(rel)` returns instead of raising."""
    if rel == "" or rel.startswith("/"):
        return False
    if any(c in _FORBIDDEN_CHARACTERS for c in rel):
        return False
    try:
        os.fsencode(rel)
    except UnicodeError:
        return False
    parts = tuple(rel.split("/"))
    for part in parts:
        if part in ("", ".", ".."):
            return False
        if part != part.rstrip(". "):
            return False
        if part.split(".", 1)[0].rstrip(" ").casefold() in _RESERVED_NAMES:
            return False
        if len(part.encode("utf-8", "surrogateescape")) > _MAX_NAME_BYTES:
            return False
        if _is_executable(part):
            return False
    if parts[0] not in _ROOTS:
        return False
    if parts[0] == "Interface" and len(parts) > 1:
        if parts[1] != _ADDONS and _fold(parts[1]) == _fold(_ADDONS):
            return False
        if parts[1] == _ADDONS and len(parts) < 3:
            return False
    return len(parts) >= 2


# ─── the verdict ─────────────────────────────────────────────────────────────


class Verdict(BaseModel):
    """Whether wowlab may edit a path, in a level and in words.

    `restorable`: the write gate would accept the path as a file to write, so
    a snapshot restore could put it back. It is the gate's own lexical rule:
    inside a flavor folder's `WTF/`, `Interface/AddOns/` or `Fonts/`, spelled
    as the client spells them, with no executable, reserved or unplain name.
    A `no` file there is still left alone by a whole restore unless it is
    named with `--paths` (owner decision 2026-09-27). A restore is not the
    last word on a `server_may_replace` file: the server may replace it again
    at the next login. `None` for a folder, which is judged by the files
    under it.

    `server_may_replace`: the Edit cell is `gate (server may replace)`, so a
    reader never has to parse `words` for it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Level
    restorable: bool | None
    server_may_replace: bool
    words: str


def edit_level(edit: str) -> Level:
    """The level of a file-map Edit cell. Raises `ValueError` on a cell
    outside the fixed mapping."""
    if edit == _GATE or (edit.startswith(_GATE + " (") and edit.endswith(")")):
        return "gate"
    if edit == "no":
        return "no"
    if edit == "—":
        return "folder"
    if edit == "n/a":
        return "not_applicable"
    raise ValueError(f"file-map Edit cell with no verdict level: {edit!r}")


def restorable(base: str | None, path: str) -> bool:
    """Whether the write gate would write `path` (relative to `base`) as a
    file, so a restore could put it back. Only flavor paths qualify."""
    return base == "flavor" and _gate_writes(path)


def tier_words(tier: str) -> str:
    """The file map's Tier cell in words."""
    return _TIER_WORDS.get(tier, tier)


def _edit_words(edit: str, level: Level, tier: str, *, restorable: bool) -> str:
    if level == "gate":
        rest = edit[len(_GATE) :].strip()
        if rest == _SERVER_MAY_REPLACE:
            words = (
                f"{_GATE_WORDS}; the server may replace it at login (sync settings: wowlab doctor)"
            )
        else:
            words = _GATE_WORDS + (f" {rest}" if rest else "")
    elif level == "no":
        words = (
            "no; wowlab leaves it alone (a restore writes it only if you name it with --paths)"
            if restorable
            else "no; wowlab never writes it"
        )
    elif level == "not_applicable":
        words = "n/a; not a file wowlab touches"
    else:  # folder
        words = "—; a folder, see the files in it"
    if tier == "B":
        words += "; unsupported by Blizzard, a patch can reset it"
    return words


def verdict_of(entry: FileMapEntry | None, base: str | None, path: str) -> Verdict:
    """The verdict for `path` (relative to `base`, `/`-separated), classified
    as `entry`; `None` is a path with no file-map row."""
    can_restore = restorable(base, path)
    if entry is None:
        words = _UNCLASSIFIED_WORDS + (_UNCLASSIFIED_RESTORED if can_restore else "")
        return Verdict(
            level="unclassified", restorable=can_restore, server_may_replace=False, words=words
        )
    level = edit_level(entry.edit)
    return Verdict(
        level=level,
        restorable=None if level == "folder" else can_restore,
        server_may_replace=entry.edit == f"{_GATE} {_SERVER_MAY_REPLACE}",
        words=_edit_words(entry.edit, level, entry.tier, restorable=can_restore),
    )
