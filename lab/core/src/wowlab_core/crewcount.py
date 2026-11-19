"""crewcount: whether files carry the crew book, and how many players (M19-11).

`snap export` says when the archive it writes holds a non-empty crew book,
and the plans of `snap restore`, `undo` and `sv merge` say when they bring
one back (docs/LAB_PLAN.md §20.3, "Other routes"; §20.5 term 4). Both are
decided from the bytes, never from a file's name: a file names the book
when its bytes hold the variable's name, and is then read as data through
`crewbook` (no Lua is run, L3).

What comes out is counts and states only: how many files, whether a book
among them is on, how many players they keep, how many could not be read.
Never a key, a name, a guild or a note. `crewbook` is imported only when a
file names the book, so a command that finds none never loads it.

Reads only the bytes it is given; writes nothing (L1).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import luadata

# The book's variable (`crewbook.CREW_VARIABLE`), spelled here so a file
# that does not name it never loads the reader.
_NAME = "WowLabCrewDB"
_NAME_BYTES = _NAME.encode("ascii")
# A file that names the book but is not data `luadata` reads (addon code, a
# `.toc`) carries no book; one that assigns it at the start of a line, as
# the client writes it, and still cannot be read is said to be unreadable.
_ASSIGNED = re.compile(rb"(?m)^" + _NAME_BYTES + rb"[ \t]*=[ \t]*\{")


class CrewBookCarried(BaseModel):
    """The crew book in the files a command writes, exports or puts back
    (§20.3, §20.5 term 4). Counts only: never a key, a name, a guild or a
    note."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paths: list[str]
    """The files that hold a book, as the command names them."""
    on: bool
    """True when a book among them that could be read is turned on."""
    readable: int
    """Files whose book was read (turned on or off)."""
    players: int
    """Distinct players those books keep, entries that could not be read
    included."""
    unreadable: int
    """Files that hold a book wowlab cannot read (an unknown version, a
    damaged table or file)."""


State = Literal["on", "off", "unreadable"]


@dataclass(frozen=True)
class _Found:
    state: State
    keys: frozenset[str]
    value: object  # the book as data, for comparing two files' books


def names_the_book(data: bytes) -> bool:
    """Whether `data` holds the book's name at all: the cheap test before
    any parse; a file without it carries no book."""
    return _NAME_BYTES in data


def _found(data: bytes) -> _Found | None:
    """The book `data` holds, or None when it holds none (it never names
    it, names it outside data, or assigns nil)."""
    if not names_the_book(data):
        return None
    unreadable = _Found("unreadable", frozenset(), None)
    if len(data) > luadata.MAX_FILE_BYTES:
        return unreadable if _ASSIGNED.search(data) else None
    try:
        values = luadata.parse(data).to_python()
    except luadata.LuaDataError:
        return unreadable if _ASSIGNED.search(data) else None
    value = values.get(_NAME)
    if value is None:
        return None
    from wowlab_core import crewbook  # only now: a file names the book

    try:
        book = crewbook.load_book(value)
    except crewbook.CrewBookError:
        return _Found("unreadable", frozenset(), value)
    if book.state == "not_turned_on":
        return None
    keys = frozenset([p.key for p in book.players] + [u.key for u in book.unread])
    return _Found(book.state, keys, value)


def _summary(found: list[tuple[str, _Found]]) -> CrewBookCarried | None:
    if not found:
        return None
    read = [f for _, f in found if f.state != "unreadable"]
    keys: set[str] = set()
    for f in read:
        keys |= f.keys
    return CrewBookCarried(
        paths=sorted({path for path, _ in found}),
        on=any(f.state == "on" for f in read),
        readable=len(read),
        players=len(keys),
        unreadable=len(found) - len(read),
    )


def carried(files: Iterable[tuple[str, bytes]]) -> CrewBookCarried | None:
    """What `files` (each a name and its bytes) carry of the crew book:
    None when no file holds one. A book that is empty (no player) still
    counts here; `snap export` says only a non-empty or unreadable one."""
    found: list[tuple[str, _Found]] = []
    for path, data in files:
        f = _found(data)
        if f is not None:
            found.append((path, f))
    return _summary(found)


def brought_back(
    files: Iterable[tuple[str, bytes, Callable[[], bytes | None]]],
) -> CrewBookCarried | None:
    """What a write brings back of the crew book: each file is its name,
    the bytes the write puts there and a read of what is there now (None
    when nothing is, or it cannot be read). A file whose book is the same,
    as data, as the one there now brings nothing back and is left out;
    None when no file brings one back. The read of what is there now is
    made only for a file whose new bytes hold a book."""
    found: list[tuple[str, _Found]] = []
    for path, data, now in files:
        f = _found(data)
        if f is None:
            continue
        if f.state != "unreadable":
            current = now()
            there = _found(current) if current is not None else None
            if there is not None and there.state != "unreadable" and there.value == f.value:
                continue
        found.append((path, f))
    return _summary(found)
