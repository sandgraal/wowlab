"""crewedit: the bytes of a crew book after a forget, a rating or a note
(docs/LAB_PLAN.md §20.3, M19-03).

Each function takes the bytes of an account `WowLab.lua` (or of the game's
`WowLab.lua.bak` beside it) and returns the bytes the file would hold after
the change: a targeted edit of the parsed `luadata` document, serialized
again, so every node the change does not touch keeps its bytes (L4). Nothing
here reads or writes a file (L1, L2: the CLI hands the bytes to `guard`) and
no Lua is run (L3): the file is parsed as data.

What these edits never do (§20.3): create the book, change `on`, or touch a
book whose `schema` this wowlab does not know (`crewbook.parse_book` refuses
it first). Every result is checked before it is returned: parsed again, it
must hold exactly the change asked for and nothing else (every other
top-level variable, and every other field of the book, as it was).

Schema 1 and schema 2 (§21.2, M20-03) are both edited, and each is written
back at the schema it was read at: no edit changes `schema`. At schema 2 a
forget also takes the key out of every match's `team` and `foes`, and a
forget of every player empties `matches`; `probe`, `warned`, `matched` and
any field this wowlab does not know keep their bytes.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable, Collection
from typing import Literal

from wowlab_core import crewbook, luadata
from wowlab_core.errors import WowlabError
from wowlab_core.luadata import Entry, KeyStyle, LuaDocument, LuaString, LuaTable
from wowlab_core.luaquote import lua_quote

# The longest note, in UTF-8 bytes after trimming spaces (§20.1): the same
# measure the addon applies in game.
NOTE_MAX_BYTES = 200

Field = Literal["rating", "note"]

_PLAYERS = "players"
_CURRENT = "current"
# Schema 2 (§21.2): the match list, and the two lists of keys in each entry.
_MATCHES = "matches"
_SIDES = ("team", "foes")


class CrewEditError(WowlabError, ValueError):
    """The change cannot be made. The message is in the player's words and
    never quotes text from the file."""


# ─── the note rule ───────────────────────────────────────────────────────────


def check_note(text: str) -> str | None:
    """The note to store for `text`, as typed at the desk: spaces trimmed
    from both ends, None when nothing is left (the note is dropped). Refused
    (`CrewEditError`): text that is not valid UTF-8, holds a control
    character (C0, DEL or C1), an invisible formatting character (Unicode
    categories Cf, Zl and Zp: bidi controls, zero-width characters, the line
    and paragraph separators U+2028 and U+2029; the addon prints a
    note as it is, so one would reorder or hide part of that line) or `|`
    (the game's escape character), or is
    over `NOTE_MAX_BYTES` bytes after trimming (never cut: a cut can split a
    character). The game removes control characters and `|` from a note; the
    desk refuses them, and Cf too, a superset (§20.1)."""
    trimmed = text.strip(" ")
    try:
        data = trimmed.encode("utf-8")
    except UnicodeEncodeError:
        raise CrewEditError("The note is not valid text; nothing was changed.") from None
    if any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in trimmed):
        raise CrewEditError(
            "The note holds a control character (a tab or a line break, say), which a note "
            "cannot hold; nothing was changed."
        )
    if any(unicodedata.category(c) in ("Cf", "Zl", "Zp") for c in trimmed):
        raise CrewEditError(
            "The note holds an invisible formatting character (one that reverses the "
            "direction of text, a zero-width one, or a line or paragraph separator), which "
            "would change how the game shows the line it prints; nothing was changed."
        )
    if "|" in trimmed:
        raise CrewEditError(
            "The note holds |, which the game reads as the start of a colour or link code; "
            "nothing was changed."
        )
    if len(data) > NOTE_MAX_BYTES:
        raise CrewEditError(
            f"The note is {len(data)} bytes long; a note holds at most {NOTE_MAX_BYTES} bytes "
            "(about 66 characters of Chinese, Japanese or Korean text). It was not cut; "
            "nothing was changed."
        )
    return trimmed or None


# ─── finding the book in a document ──────────────────────────────────────────


def _key_text(entry: Entry) -> str | None:
    """A keyed entry's key as text (as `crewbook` reads it), else None."""
    if entry.style == KeyStyle.NAME and isinstance(entry.key, str):
        return entry.key
    if entry.style == KeyStyle.STRING and isinstance(entry.key, LuaString):
        return entry.key.value
    return None


def _book_index(document: LuaDocument) -> int | None:
    """The index of the one assignment of the book, None when there is none."""
    found = [i for i, a in enumerate(document.assignments) if a.name == crewbook.CREW_VARIABLE]
    if len(found) > 1:
        raise CrewEditError(
            "This file sets the crew book more than once, which the game is not known to "
            "write; wowlab does not change it. Nothing was changed."
        )
    if not found:
        return None
    if not isinstance(document.assignments[found[0]].value, LuaTable):
        raise CrewEditError("The crew book in this file is not readable. Nothing was changed.")
    return found[0]


def _field_index(table: LuaTable, name: str) -> int | None:
    """The index of the book's top-level field `name`; refused when the
    book holds it twice."""
    found = [i for i, e in enumerate(table.entries) if _key_text(e) == name]
    if len(found) > 1:
        raise CrewEditError(
            f"The crew book holds its {name} list twice, which the game is not known to "
            "write; wowlab does not change it. Nothing was changed."
        )
    return found[0] if found else None


def _trivia(slot: bytes | None) -> bool:
    """Whether a trivia slot holds anything but whitespace (a comment)."""
    return slot is not None and slot.strip(b" \t\r\n\f\v") != b""


def _without(table: LuaTable, drop: Callable[[int, Entry], bool]) -> LuaTable:
    """`table` with every entry `drop` picks taken out, with the bytes in
    front of it. Where an entry taken out had a comment beside or before it,
    the slot after it is laid out again (`luadata.serialize`'s rule), so no
    comment that stood with it stays."""
    out: list[Entry] = []
    close = table.close_lead
    again = False
    for i, entry in enumerate(table.entries):
        if drop(i, entry):
            again = again or _trivia(entry.lead) or entry.comment is not None
            continue
        if again:
            entry = entry._replace(lead=None)
            again = False
        out.append(entry)
    if again:
        close = None
    return table._replace(entries=tuple(out), close_lead=close)


def _with_entry(table: LuaTable, entry: Entry) -> LuaTable:
    """`table` with `entry` added after its last entry, laid out as the
    file's other entries are (`luadata.serialize`)."""
    entries = list(table.entries)
    if entries and entries[-1].sep == b"":
        entries[-1] = entries[-1]._replace(sep=None)
    return table._replace(entries=(*entries, entry), close_lead=None)


def _string_entry(name: str, value: str) -> Entry:
    """`["name"] = "value",` as the client writes a field."""
    return Entry(
        lead=None,
        style=KeyStyle.STRING,
        key=LuaString(None, lua_quote(name.encode("utf-8"))),
        key_close_lead=None,
        eq_lead=None,
        value=LuaString(None, lua_quote(value.encode("utf-8", "surrogateescape"))),
        sep_lead=None,
        sep=None,
        comment=None,
        duplicate=False,
    )


class _Book:
    """The parsed file and its book's table, for one edit."""

    def __init__(self, data: bytes) -> None:
        self.document = luadata.parse(data)
        self.loaded = self.document.to_python()
        # Refuses an unknown schema and a book whose top level is unreadable
        # (`crewbook.CrewBookError`) before anything is planned.
        self.read = crewbook.load_book(self.loaded.get(crewbook.CREW_VARIABLE))
        self.index = _book_index(self.document)

    @property
    def table(self) -> LuaTable:
        assert self.index is not None
        value = self.document.assignments[self.index].value
        assert isinstance(value, LuaTable)
        return value

    def field(self, name: str) -> LuaTable | None:
        """The book's `players` or `current` table, None when it has none
        (a book whose field is not a table was refused by `parse_book`)."""
        at = _field_index(self.table, name)
        if at is None:
            return None
        value = self.table.entries[at].value
        return value if isinstance(value, LuaTable) else None

    def replaced(self, new: dict[str, LuaTable]) -> bytes:
        """The file's bytes with each of the book's fields named in `new`
        replaced by its table, serialized once."""
        entries = list(self.table.entries)
        for name, table in new.items():
            at = _field_index(self.table, name)
            assert at is not None
            entries[at] = entries[at]._replace(value=table)
        book = self.table._replace(entries=tuple(entries))
        assignments = list(self.document.assignments)
        assert self.index is not None
        assignments[self.index] = assignments[self.index]._replace(value=book)
        return luadata.serialize(self.document._replace(assignments=tuple(assignments)))


def keys_held(data: bytes) -> list[str]:
    """Every player key the book in `data` holds, as stored, in `players`
    (read or not) and in `current`, and, at schema 2, in any match's `team`
    or `foes`, once each in file order; empty when the file holds no book.
    Refuses an unknown schema (`crewbook.CrewBookError`)."""
    return _keys(_Book(data))


def _entry_key(entry: Entry) -> str | None:
    """A keyed entry's key, or a positional entry's string value: how a
    player key is held in `players`, `current`, `team` and `foes`."""
    key = _key_text(entry)
    if key is None and entry.style == KeyStyle.POSITIONAL:
        key = entry.value.value if isinstance(entry.value, LuaString) else None
    return key


def _side_tables(book: _Book) -> list[LuaTable]:
    """Every `team` and `foes` table of the book's matches (schema 2)."""
    if book.read.schema_number is None or book.read.schema_number < 2:
        return []
    matches = book.field(_MATCHES)
    out: list[LuaTable] = []
    for match in matches.entries if matches is not None else ():
        if isinstance(match.value, LuaTable):
            for entry in match.value.entries:
                if _key_text(entry) in _SIDES and isinstance(entry.value, LuaTable):
                    out.append(entry.value)
    return out


def _keys(book: _Book) -> list[str]:
    if book.index is None:
        return []
    out: dict[str, None] = {}
    tables = [book.field(name) for name in (_PLAYERS, _CURRENT)]
    for table in [*(t for t in tables if t is not None), *_side_tables(book)]:
        for entry in table.entries:
            key = _entry_key(entry)
            if key is not None:
                out.setdefault(key, None)
    return list(out)


# ─── checking a result ───────────────────────────────────────────────────────


def _same(a: object, b: object) -> bool:
    """Equal as loaded values (a NaN equals a NaN)."""
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


def _checked(
    before: dict[str, object], after: bytes, change: Callable[[dict[str, object]], None]
) -> bytes:
    """`after`, once it is shown to hold exactly `before` with `change`
    applied to its book (`before` holds the loaded values of the file as
    read, and is changed here): every other variable, and every
    other part of the book, as it was. Anything else is refused: wowlab
    would rather change nothing than change something it did not mean to."""
    want = before
    book = want.get(crewbook.CREW_VARIABLE)
    assert isinstance(book, dict)
    change(book)
    got = luadata.parse(after).to_python()
    if not _same(want, got):
        raise CrewEditError(
            "wowlab could not make this change without touching other data in the file, so "
            "it changed nothing."
        )
    return after


def _named(value: object, keys: Collection[str]) -> bool:
    """Whether `value` is a string `keys` holds. Only a string can be a
    player key: a nested table, a number or a boolean in a list is never
    compared (a table cannot be hashed, and is not a key)."""
    return isinstance(value, str) and value in keys


def _dropped(value: object, keys: Collection[str]) -> object:
    """A loaded table without the entries `keys` name (as string keys, or
    as the string values of a list), as `forget` takes them out of the
    document (`_entry_key`); an emptied table loads as `{}`. Anything else
    stays, a key inside a nested table included: `names_left` finds such a
    name, so the command refuses with nothing written."""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not _named(k, keys)}
    if isinstance(value, list):
        kept = [v for v in value if not _named(v, keys)]
        return kept if kept else {}
    return value


def _drop_keys(keys: Collection[str], schema: int | None) -> Callable[[dict[str, object]], None]:
    def change(book: dict[str, object]) -> None:
        for name in (_PLAYERS, _CURRENT):
            if name in book:
                book[name] = _dropped(book[name], keys)
        matches = book.get(_MATCHES)
        if schema is None or schema < 2 or not isinstance(matches, (dict, list)):
            return
        for match in matches.values() if isinstance(matches, dict) else matches:
            if isinstance(match, dict):
                for side in _SIDES:
                    if side in match:
                        match[side] = _dropped(match[side], keys)

    return change


# ─── the edits ───────────────────────────────────────────────────────────────


def _matches_without(book: _Book, drop: Callable[[int, Entry], bool]) -> LuaTable | None:
    """The book's `matches` with every key `drop` picks taken out of each
    entry's `team` and `foes` (schema 2), the entries themselves kept; None
    when no match names any of them."""
    if book.read.schema_number is None or book.read.schema_number < 2:
        return None
    matches = book.field(_MATCHES)
    if matches is None:
        return None
    changed = False
    out: list[Entry] = []
    for match in matches.entries:
        record = match.value
        if isinstance(record, LuaTable):
            inner = list(record.entries)
            for j, entry in enumerate(inner):
                side = entry.value
                if (
                    _key_text(entry) in _SIDES
                    and isinstance(side, LuaTable)
                    and any(drop(i, e) for i, e in enumerate(side.entries))
                ):
                    inner[j] = entry._replace(value=_without(side, drop))
                    changed = True
            match = match._replace(value=record._replace(entries=tuple(inner)))
        out.append(match)
    return matches._replace(entries=tuple(out)) if changed else None


def forget(data: bytes, keys: Collection[str]) -> bytes:
    """`data` with every player in `keys` (stored keys) removed from the
    book's `players` and `current`: their entries, and an entry of `current`
    that is the key itself in a list; at schema 2, also from every match's
    `team` and `foes` (§21.2), each match kept. The same bytes when the book
    holds none of them, or the file holds no book (never created)."""
    book = _Book(data)
    if book.index is None:
        return data
    wanted = set(keys)

    def drop(_: int, entry: Entry) -> bool:
        return _entry_key(entry) in wanted

    new: dict[str, LuaTable] = {}
    for name in (_PLAYERS, _CURRENT):
        table = book.field(name)
        if table is not None and any(drop(i, e) for i, e in enumerate(table.entries)):
            new[name] = _without(table, drop)
    matches = _matches_without(book, drop)
    if matches is not None:
        new[_MATCHES] = matches
    if not new:
        return data
    out = book.replaced(new)
    return _checked(book.loaded, out, _drop_keys(wanted, book.read.schema_number))


def forget_all(data: bytes) -> bytes:
    """`data` with the book's `players` and `current` emptied, and at schema
    2 its `matches` (§21.2); `schema`, `on`, `started`, `probe`, `skipped`,
    `warned`, `matched` (a map and a time, no player) and anything else in
    the book as they were (§20.3). The same bytes when all are already
    empty, or the file holds no book."""
    book = _Book(data)
    if book.index is None:
        return data
    names = [_PLAYERS, _CURRENT]
    if book.read.schema_number is not None and book.read.schema_number >= 2:
        names.append(_MATCHES)
    new: dict[str, LuaTable] = {}
    for name in names:
        table = book.field(name)
        if table is not None and table.entries:
            new[name] = table._replace(entries=(), close_lead=None)
    if not new:
        return data
    out = book.replaced(new)

    def change(value: dict[str, object]) -> None:
        for name in names:
            if isinstance(value.get(name), (dict, list)):
                value[name] = {}

    return _checked(book.loaded, out, change)


def set_field(data: bytes, key: str, field: Field, value: str | None) -> bytes:
    """`data` with the player `key`'s `rating` or `note` set to `value`, or
    taken out when `value` is None: the one entry changed, written with the
    shared quoting helper (`luaquote`). Refused when the book does not hold
    the player as one readable entry."""
    book = _Book(data)
    players = book.field(_PLAYERS) if book.index is not None else None
    found = [
        i
        for i, e in enumerate(players.entries if players is not None else ())
        if _key_text(e) == key
    ]
    if players is None or not found:
        raise CrewEditError("The crew book does not hold this player. Nothing was changed.")
    if len(found) > 1:
        raise CrewEditError(
            "The crew book holds this player twice, which the game is not known to write; "
            "wowlab does not change it. Nothing was changed."
        )
    entry = players.entries[found[0]]
    record = entry.value
    if not isinstance(record, LuaTable):
        raise CrewEditError(
            "This player's entry in the crew book could not be read, so wowlab does not "
            "change it. Nothing was changed."
        )
    at = [i for i, e in enumerate(record.entries) if _key_text(e) == field]
    if value is None:
        new = _without(record, lambda i, e: i in at) if at else record
    elif at:
        entries = list(record.entries)
        old = entries[at[-1]]
        entries[at[-1]] = old._replace(
            value=LuaString(old.value.lead, lua_quote(value.encode("utf-8", "surrogateescape")))
        )
        new = record._replace(entries=tuple(entries))
        if len(at) > 1:  # the game loads the last; the earlier ones go
            new = _without(new, lambda i, e: i in at[:-1])
    else:
        new = _with_entry(record, _string_entry(field, value))
    if new is record:
        return data
    entries = list(players.entries)
    entries[found[0]] = entry._replace(value=new)
    out = book.replaced({_PLAYERS: players._replace(entries=tuple(entries))})
    if out == data:
        return data

    def change(loaded: dict[str, object]) -> None:
        table = loaded.get(_PLAYERS)
        assert isinstance(table, dict)
        record = table[key]
        assert isinstance(record, dict)
        if value is None:
            record.pop(field, None)
        else:
            record[field] = value

    return _checked(book.loaded, out, change)


def _strings(value: object, path: tuple[object, ...]) -> list[tuple[tuple[object, ...], str]]:
    """Every string of a loaded value, keys and values, with where it is."""
    out: list[tuple[tuple[object, ...], str]] = []
    if isinstance(value, str):
        out.append((path, value))
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                out.append(((*path, k, None), k))
            out.extend(_strings(v, (*path, k)))
    elif isinstance(value, list):
        for i, v in enumerate(value, 1):
            out.extend(_strings(v, (*path, i)))
    return out


class NameSpot:
    """Where a forgotten name is still found in a file: the stored key of
    the player whose entry holds it and which field (`note`, `guild`,
    `where`, ...), or neither when it is elsewhere in the file."""

    def __init__(self, player: str | None, field: str | None) -> None:
        self.player = player
        self.field = field


# What §20.2's matching rule leaves out of a typed name (as `crewbook.fold`).
_IGNORED = frozenset(" '" + chr(0x2019))


def _word_char(c: str) -> bool:
    """A letter or a digit, in any script (Unicode categories L and N)."""
    return unicodedata.category(c)[0] in ("L", "N")


def _folded(text: str, fold: bool) -> tuple[str, list[int]]:
    """`text` (NFC) folded char by char as `crewbook.fold` folds it (case
    folding, spaces and the two apostrophes left out), with the index in
    the NFC text each folded character came from. Without `fold`, the text
    as it is."""
    t = unicodedata.normalize("NFC", text)
    if not fold:
        return t, list(range(len(t)))
    chars: list[str] = []
    owner: list[int] = []
    for i, c in enumerate(t):
        if c in _IGNORED:
            continue
        for f in c.casefold():
            chars.append(f)
            owner.append(i)
    return "".join(chars), owner


def _bounded_in(text: str, name: str, *, fold: bool = True) -> bool:
    """Whether `name` occurs in `text` (under §20.2's rule when `fold`) with
    no letter or digit directly before or after it in `text`: "Bob-Realm"
    is in "met Bob-Realm, twice" but not in "Jimbob-Realm" or "Bob-Realmx"."""
    hay, owner = _folded(text, fold)
    want, _ = _folded(name, fold)
    if not want:
        return False
    t = unicodedata.normalize("NFC", text) if fold else hay
    at = hay.find(want)
    while at >= 0:
        first, last = owner[at], owner[at + len(want) - 1]
        before = t[first - 1] if first > 0 else ""
        after = t[last + 1] if last + 1 < len(t) else ""
        if not (before and _word_char(before)) and not (after and _word_char(after)):
            return True
        at = hay.find(want, at + 1)
    return False


def _book_key(path: tuple[object, ...]) -> bool:
    """Whether a loaded string is a player key of the book (in `players` or
    `current`)."""
    return (
        len(path) == 4
        and path[0] == crewbook.CREW_VARIABLE
        and path[1] in (_PLAYERS, _CURRENT)
        and path[3] is None
    )


def names_left(data: bytes, keys: Collection[str]) -> list[NameSpot]:
    """Where any of `keys` is still in `data`, as the same name under §20.2's
    matching rule (`crewbook.fold`: NFC, case folding, spaces and the two
    apostrophes ignored): a player key of the book when its whole key folds
    the same; any other string the file loads as (keys and values, an
    escaped spelling included: a note, a guild or instance name, another
    variable) when the name is in it with no letter or digit directly
    before or after it; and the bytes of the file, so bounded, for what the
    file does not load (a comment). A name inside a Lua comment in another
    spelling, or split across two strings, is not found. Empty when none
    is left."""
    wanted = [k for k in keys if crewbook.fold(k)]
    folded = {crewbook.fold(k) for k in wanted}
    spots: list[NameSpot] = []
    loaded = luadata.parse(data).to_python()
    for path, text in _strings(loaded, ()):
        if _book_key(path):
            if crewbook.fold(text) in folded:
                spots.append(_spot(path))
        else:
            # The cheap test first: most strings hold no form of the name.
            plain = crewbook.fold(text)
            if any(f in plain for f in folded) and any(_bounded_in(text, k) for k in wanted):
                spots.append(_spot(path))
    if not spots:
        whole = data.decode("utf-8", "surrogateescape")
        if any(k in whole and _bounded_in(whole, k, fold=False) for k in wanted):
            spots.append(NameSpot(None, None))
    return spots


def _spot(path: tuple[object, ...]) -> NameSpot:
    """The player and field a loaded string's path names, when it is inside
    the book's player list."""
    if (
        len(path) >= 4
        and path[0] == crewbook.CREW_VARIABLE
        and path[1] == _PLAYERS
        and isinstance(path[2], str)
    ):
        return NameSpot(path[2], "key" if path[3] is None else str(path[3]))
    return NameSpot(None, None)
