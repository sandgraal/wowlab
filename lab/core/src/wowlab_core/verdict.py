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
import stat
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

# The executable rule is `filemap`'s copy of guard's; its tables are named
# here too, for `test_verdict.py`'s table-equality check against `guard`.
from wowlab_core.filemap import _EXECUTABLE_SUFFIXES as _EXECUTABLE_SUFFIXES
from wowlab_core.filemap import _VERSIONED_SHARED_LIBRARY as _VERSIONED_SHARED_LIBRARY
from wowlab_core.filemap import FileMapEntry, _is_executable

Level = Literal["gate", "no", "folder", "not_applicable", "unclassified"]

LABELS: dict[str, str] = {
    "gate": "wowlab can edit",
    "no": "wowlab won't touch",
    "folder": "folder",
    "not_applicable": "not used",
    "unclassified": "unknown",
}
"""Each level in a player's words, for a badge (owner, 2026-10-16)."""

LINK_LABEL = "a link; wowlab won't follow it"
"""The label of a link (a symlink, a junction or another Windows reparse
point that names another path), or a path inside one, at any level:
the gate refuses it whatever its row says (M13-25 round 2)."""

SERVER_LABEL = "server may overwrite"
"""The second marker on a file the server may replace at login."""

_GATE = "gate"
_GATE_WORDS = "yes, through wowlab's write gate only, with the client closed"
_SERVER_MAY_REPLACE = "(server may replace)"
_SERVER_WORDS = "; the server may replace it at login (sync settings: wowlab doctor)"
_UNCLASSIFIED_WORDS = "no file-map row; wowlab does not know what this file is"
_UNCLASSIFIED_FOLDER_WORDS = "no file-map row; wowlab does not know what this folder is"
# The same words as `layout`'s reason for such a path (M13-29).
_NOT_FOUND_WORDS = (
    "not found; nothing is at this path, and no file-map row matches a name like this"
)
# What a restore can do at a path with nothing on it: put back a copy a
# snapshot holds, or, naming the path with --paths, delete a file created
# there after the snapshot (`guard` deletes a named path the snapshot's
# subtrees cover but that it does not hold).
_MISSING_RESTORED = (
    "; `snap restore --paths` naming it puts back a snapshot's copy, "
    "or removes a file created here after that snapshot"
)
_LINK_WORDS = (
    "a link, or inside one; wowlab never follows it or writes through it, "
    "and a restore does not put it back"
)
_TIER_B_WORDS = "; unsupported by Blizzard, a patch can reset it"
_REPARSE_NOT_LINK = (
    "a Windows reparse point that is not a link (for example a cloud placeholder, "
    "a deduplicated file or a file compressed with compact /EXE), or inside one"
)
_PLACEHOLDER_WORDS = f"; {_REPARSE_NOT_LINK}: wowlab's write gate does not write through these"
# At the gate level the usual words open with "yes"; under such a reparse
# point they open with this instead, and keep their other suffixes.
_PLACEHOLDER_GATE_WORDS = (
    "no, not here: the file map says it goes through wowlab's write gate, but that gate "
    f"does not write through {_REPARSE_NOT_LINK}"
)
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
# load-bearing; its private rule is copied here rather than imported (the
# executable rule is `filemap`'s copy, which its `<executable>` pattern
# segment uses too), and `test_verdict.py::test_restorable_agrees_with_the_write_gate`
# fails when the two drift apart.

_ROOTS = ("WTF", "Interface", "Fonts")
_ADDONS = "AddOns"
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{device}{n}" for device in ("com", "lpt") for n in (*"0123456789", "¹", "²", "³")}
)
_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*\\\x7f') | frozenset(chr(n) for n in range(32))
_MAX_NAME_BYTES = 255


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


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


# ─── the write gate's link rule, copied ──────────────────────────────────────
# A copy of `guard._is_link`, for the same reason as the path rule above:
# `test_verdict.py::test_gate_refuses_stat_agrees_with_the_write_gate` fails
# when the two drift apart. The gate refuses every Windows reparse point,
# while `snapshot` and `gaps` call only a name surrogate a link; the ones in
# between (cloud placeholders, deduplicated files) are walked and described
# but never written.


def gate_refuses_stat(st: os.stat_result) -> bool:
    """Whether the write gate refuses a path whose `lstat` is `st` (or a
    path under it) as a link: `guard._is_link(st)`."""
    attributes = int(getattr(st, "st_file_attributes", 0))
    return stat.S_ISLNK(st.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


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
    at the next login. `None` for every folder, whatever its level: a folder
    is judged by the files under it. `None` for a link too (a symlink, a
    junction or another reparse point that names another path), which a
    snapshot never captures. `False` for a file at or under a Windows
    reparse point that is not a link (a cloud placeholder, a deduplicated
    file, a file compressed with `compact /EXE`): the gate refuses to write through any reparse point.

    `server_may_replace`: the Edit cell is `gate (server may replace)`, so a
    reader never has to parse `words` for it.

    `label`: the level in a player's words, for a badge (M13-25, owner
    2026-10-16). `server_label`: the words for a second marker when
    `server_may_replace`, else `None`. Both follow from the fields above and
    are filled in when not given; a value that disagrees is refused.

    `link`: the path is a link (a symlink, a junction or another reparse
    point that names another path), or lies inside one. The level stays the
    row's, but the label is `LINK_LABEL`: wowlab never follows it
    or writes through it.

    `placeholder` (M13-32): the path is, or lies under, a Windows reparse
    point that is not a link (a cloud placeholder, a deduplicated file, a
    file compressed with `compact /EXE`). The level stays the row's, but the gate refuses to
    write through it, so the label is the `no` label. `link` wins.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Level
    restorable: bool | None
    server_may_replace: bool
    words: str
    label: str = ""
    server_label: str | None = None
    link: bool = False
    placeholder: bool = False

    @model_validator(mode="before")
    @classmethod
    def _fill_labels(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            level = data.get("level")
            if data.get("link") is True:
                data.setdefault("label", LINK_LABEL)
            elif data.get("placeholder") is True:
                data.setdefault("label", LABELS["no"])
            elif isinstance(level, str) and level in LABELS:
                data.setdefault("label", LABELS[level])
            data.setdefault(
                "server_label", SERVER_LABEL if data.get("server_may_replace") else None
            )
        return data

    @model_validator(mode="after")
    def _check_labels(self) -> Verdict:
        if self.link:
            expected = LINK_LABEL
        elif self.placeholder:
            expected = LABELS["no"]
        else:
            expected = LABELS[self.level]
        if self.label != expected:
            raise ValueError(
                f"label {self.label!r} is not the label of level {self.level!r} "
                f"(link: {self.link}, placeholder: {self.placeholder})"
            )
        if self.server_label != (SERVER_LABEL if self.server_may_replace else None):
            raise ValueError(
                f"server_label {self.server_label!r} disagrees with server_may_replace"
            )
        return self


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


def _edit_words(edit: str, level: Level, tier: str, *, restorable: bool, folder: bool) -> str:
    if level == "gate":
        rest = edit[len(_GATE) :].strip()
        if rest == _SERVER_MAY_REPLACE:
            words = _GATE_WORDS + _SERVER_WORDS
        else:
            words = _GATE_WORDS + (f" {rest}" if rest else "")
    elif level == "no":
        if not restorable:
            words = "no; wowlab never writes it"
        elif folder:
            # `snap restore --paths` takes file paths; naming a folder is refused.
            words = (
                "no; wowlab leaves the files in it alone "
                "(a restore writes one only if you name that file with --paths)"
            )
        else:
            words = (
                "no; wowlab leaves it alone (a restore writes it only if you name it with --paths)"
            )
    elif level == "not_applicable":
        words = "n/a; not a file wowlab touches"
    else:  # folder
        words = "—; a folder, see the files in it"
    if tier == "B":
        words += _TIER_B_WORDS
    return words


def verdict_of(
    entry: FileMapEntry | None,
    base: str | None,
    path: str,
    *,
    folder: bool = False,
    link: bool = False,
    placeholder: bool = False,
    missing: bool = False,
) -> Verdict:
    """The verdict for `path` (relative to `base`, `/`-separated), classified
    as `entry`; `None` is a path with no file-map row. `folder`: the path is
    a folder (a row at level `folder` is one whatever the caller says).
    `link`: the path is a link (a symlink, a junction or another reparse
    point that names another path), or lies inside one. The gate refuses any path through a link and a snapshot never captures one, so
    the words are the link words at every level (with the tier suffix).
    `placeholder` (M13-32): the path is, or lies under, a reparse point that
    is no link (`gate_refuses_stat` but not a name surrogate): the words are
    the usual ones plus a clause saying the gate does not write through it,
    and a file is not `restorable`.
    `missing` (M13-29): nothing is on disk at the path. With no row, the
    words say "not found" instead of naming a file or folder that is not
    there; `restorable` is as for any path (a restore may put a file back).
    A path with a row is judged by its row."""
    if placeholder and not link:
        # The words as for a path the gate never writes (base None), the
        # level and whether `restorable` has an answer as for this one.
        plain = verdict_of(entry, base, path, folder=folder, missing=missing)
        words = verdict_of(entry, None, path, folder=folder, missing=missing).words
        if words.startswith(_GATE_WORDS):
            words = _PLACEHOLDER_GATE_WORDS + words[len(_GATE_WORDS) :]
        else:
            words += _PLACEHOLDER_WORDS
        fields = plain.model_dump(exclude={"label"})
        fields.update(
            restorable=None if plain.restorable is None else False,
            words=words,
            placeholder=True,
        )
        return Verdict(**fields)
    can_restore = restorable(base, path)
    if link:
        tier = entry.tier if entry is not None else ""
        server = entry is not None and entry.edit == f"{_GATE} {_SERVER_MAY_REPLACE}"
        return Verdict(
            level="unclassified" if entry is None else edit_level(entry.edit),
            restorable=None,
            server_may_replace=server,
            words=("no file-map row; " if entry is None else "")
            + _LINK_WORDS
            + (_SERVER_WORDS if server else "")
            + (_TIER_B_WORDS if tier == "B" else ""),
            link=True,
        )
    if entry is None:
        if missing:
            words = _NOT_FOUND_WORDS + (_MISSING_RESTORED if can_restore and not folder else "")
        elif folder:
            words = _UNCLASSIFIED_FOLDER_WORDS
        else:
            words = _UNCLASSIFIED_WORDS + (_UNCLASSIFIED_RESTORED if can_restore else "")
        return Verdict(
            level="unclassified",
            restorable=None if folder else can_restore,
            server_may_replace=False,
            words=words,
        )
    level = edit_level(entry.edit)
    return Verdict(
        level=level,
        restorable=None if folder or level == "folder" else can_restore,
        server_may_replace=entry.edit == f"{_GATE} {_SERVER_MAY_REPLACE}",
        words=_edit_words(
            entry.edit,
            level,
            entry.tier,
            restorable=can_restore,
            folder=folder or level == "folder",
        ),
    )
