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

from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core.filemap import FileMapEntry

Level = Literal["gate", "no", "folder", "not_applicable", "unclassified"]

_GATE = "gate"
_GATE_WORDS = "yes, through wowlab's write gate only, with the client closed"
_SERVER_MAY_REPLACE = "(server may replace)"
_UNCLASSIFIED_WORDS = "no file-map row"
# The subtrees the write gate may write, spelled as the client spells them.
_GATE_AREAS = frozenset({"wtf", "interface", "fonts"})
_TIER_WORDS = {
    "A": "A: ordinary addon-user behaviour",
    "A (read)": "A (read): reading it is ordinary addon-user behaviour",
    "A (read, later wave)": "A (read, later wave): reading it is ordinary; wowlab does not read it yet",
    "B": "B: works, but Blizzard does not support it and a patch can reset it",
    "—": "—: nothing wowlab does with it",
}


class Verdict(BaseModel):
    """Whether wowlab may edit a path, in a level and in words.

    `restorable`: the path lies in a flavor folder's `WTF/`, `Interface/` or
    `Fonts/`, the subtrees the write gate writes, so a restore could put it
    back. For a `no` file that means a restore leaves it alone unless it is
    named with `--paths` (owner decision 2026-09-27).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Level
    restorable: bool
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
    """Whether a path relative to `base` lies where the write gate could put
    it back when named (a flavor folder's `WTF/`, `Interface/`, `Fonts/`)."""
    return base == "flavor" and path.split("/", 1)[0].casefold() in _GATE_AREAS


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
        return Verdict(level="unclassified", restorable=can_restore, words=_UNCLASSIFIED_WORDS)
    level = edit_level(entry.edit)
    words = _edit_words(entry.edit, level, entry.tier, restorable=can_restore)
    return Verdict(level=level, restorable=can_restore, words=words)
