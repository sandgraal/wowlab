"""svmerge: a structural merge of one SavedVariables document (docs/LAB_PLAN.md
§13.4 with its 2026-09-29 "M11-09T conductor rulings"; M11-09).

`merge(ours, theirs, *, base=None, keys=(), take=None)` works on `luadata`
documents and nothing else: it reads no file, writes no file, and executes
nothing (L1, L3). Ours is the target, theirs the source. The `wowlab sv
merge` command reads the files, runs the loader check and hands the merged
document to `luadata.serialize` and the result to `guard` (L2).

Rules, by key path:

- Tables present on both sides are merged key by key. A conflict is a leaf:
  a scalar, or a key holding a table on one side and a scalar on the other.
  Positional entries are keyed by their index, the Lua key they load as, and
  `[1]`, `[1.0]` and the first positional entry are one key, as in Lua 5.1.
  For duplicate keys the entry Lua 5.1 would load is the one merged (the
  rule `LuaDocument.to_python` documents); the others are left as they are.
- Two-way (no `base`): a key whose values differ is a conflict. A key on one
  side only is `absent`, with the side it is missing from, and is neither
  added to ours nor deleted from it.
- Three-way (`base` given): a key changed on one side only is taken from
  that side (ours already holds its own changes); changed the same way on
  both sides it is kept once; changed differently it is a conflict. A key
  theirs added (in neither base nor ours) is taken. Every other key on one
  side only is `absent` and is never deleted from ours.
- `take="ours"` or `"theirs"` resolves every conflict that way; resolved
  conflicts are still listed (with `resolved` set). Without it a conflict
  keeps ours, and the caller writes nothing while one is unresolved.
- `keys` limits the merge. Each is a path in `wowlab sv dump --path` syntax
  (`Var.key[3]["some key"]`), or `SRC=DST`. Two-way, theirs' subtree at
  `SRC` is copied whole to `DST` in ours (a copy within one file is
  `merge(doc, doc, keys=["SRC=DST"])`); three-way, the rules above apply
  inside the named subtree only and nothing outside it is taken or listed.
  A `DST` whose parent tables ours does not have is reported `absent`; one
  whose parent is not a table is refused (`MergeError`).

Output: the merged document is ours wherever nothing is taken, node for
node, so unchanged bytes stay as they are and keys keep ours' order. A
taken scalar keeps its own text (a number's `raw`) and ours' leading bytes.
A taken table is placed with every trivia slot and separator set to `None`,
so `luadata.serialize` lays it out in the target document's own style (line
endings, indentation, spacing, `-- [n]` comments); an added key goes after
the table's last entry.

Paths are spelled as `wowlab sv dump` prints them: `WowLabCharDB["probe"]
["loads"]`, `[n]` for a number key (its text) or a positional entry, `.name`
for a bare-name key.

The loader check's reading of the lab-addon's probe (§13.1:
`WowLabCharDB.probe.loads` and `.lost`) is `read_probe`, also pure.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import luadata
from wowlab_core.luadata import (
    Assignment,
    Entry,
    KeyStyle,
    LuaBool,
    LuaDocument,
    LuaNil,
    LuaNumber,
    LuaString,
    LuaTable,
    LuaValue,
)

__all__ = [
    "LAB_ADDON_CHARACTER_VARIABLE",
    "LAB_ADDON_FILE",
    "Absent",
    "Conflict",
    "MergeError",
    "MergeResult",
    "Probe",
    "Side",
    "Taken",
    "check_keys",
    "merge",
    "read_probe",
]

Side = Literal["ours", "theirs"]

#: The lab-addon's SavedVariables file and its per-character variable (§13.1).
#: The Lab's own addon, not a flavor fact (L6).
LAB_ADDON_FILE = "WowLab.lua"
LAB_ADDON_CHARACTER_VARIABLE = "WowLabCharDB"

# Lua 5.1 stores positional fields 50 at a time (LFIELDS_PER_FLUSH), which
# decides which of two equal keys it loads; `luadata.to_python` follows it.
_LFIELDS_PER_FLUSH = 50


class MergeError(ValueError):
    """A `--key` that cannot be read, or a merge that cannot be placed."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Conflict(_Frozen):
    """A leaf changed differently on the two sides (two-way: any differing
    leaf). The values are shown as `wowlab sv dump` shows them."""

    path: str
    ours: str
    theirs: str
    base: str | None  # three-way: the base's value, or None when base lacks it
    resolved: Side | None  # --take; None: kept ours, nothing may be written


class Taken(_Frozen):
    """A value taken from theirs into ours."""

    path: str
    value: str


class Absent(_Frozen):
    """A key present on one side only: never deleted, and in the two-way
    merge never added (an addon may leave out values equal to its defaults)."""

    path: str
    missing_from: Side


class MergeResult(BaseModel):
    """What `merge` returns: the merged document (ours where nothing was
    taken) and what was found on the way, in document order."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    document: LuaDocument
    mode: Literal["two-way", "three-way"]
    conflicts: tuple[Conflict, ...]
    taken: tuple[Taken, ...]
    absent: tuple[Absent, ...]

    @property
    def unresolved(self) -> bool:
        """True when a conflict kept ours only because no `take` was given."""
        return any(c.resolved is None for c in self.conflicts)


class Probe(_Frozen):
    """The lab-addon's probe as a `WowLab.lua` holds it (§13.1). `loads` is
    None when the file has no readable `probe.loads`."""

    loads: int | None
    lost: bool


# ─── key paths (`wowlab sv dump --path` syntax) ──────────────────────────────

_PATH_HEAD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH_STEP = re.compile(
    r"""\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)"""
    r"""|\[\s*(?:"(?P<dq>(?:[^"\\]|\\.)*)"|'(?P<sq>(?:[^'\\]|\\.)*)'"""
    r"""|(?P<bool>true|false)|(?P<num>-?(?:0[xX][0-9A-Fa-f]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)))\s*\]"""
)
_PATH_ESCAPE = re.compile(r"\\(.)")

# A Lua key as the loader compares it: ("n", float) for numbers and
# positional entries, ("s", bytes) for strings and bare names, ("b", bool).
_KeyId = tuple[str, object]


class _Step:
    """One step of a key path: its Lua key and how to spell it when ours
    does not hold it yet."""

    __slots__ = ("key_id", "spelling", "text")

    def __init__(self, key_id: _KeyId, spelling: str, text: str | None) -> None:
        self.key_id = key_id
        self.spelling = spelling
        self.text = text  # the string, for a text step


class _Path:
    __slots__ = ("head", "source", "steps")

    def __init__(self, head: str, steps: list[_Step], source: str) -> None:
        self.head = head
        self.steps = steps
        self.source = source

    def same(self, other: _Path) -> bool:
        """Whether both name one Lua key path (`A.p` and `A["p"]` do)."""
        return self.head == other.head and [s.key_id for s in self.steps] == [
            s.key_id for s in other.steps
        ]


def _lua_quote(data: bytes) -> bytes:
    """A double-quoted Lua 5.1 literal that loads as `data`."""
    out = bytearray(b'"')
    for byte in data:
        if byte in (0x22, 0x5C):  # " and backslash
            out += b"\\" + bytes([byte])
        elif byte == 0x0A:
            out += b"\\n"
        elif byte == 0x0D:
            out += b"\\r"
        elif byte < 0x20 or byte == 0x7F:
            out += b"\\%03d" % byte
        else:
            out.append(byte)
    out += b'"'
    return bytes(out)


def _number_text(value: float) -> str:
    if not math.isfinite(value):
        raise MergeError(f"{value!r} is not a number a SavedVariables file can hold")
    return str(int(value)) if value.is_integer() else repr(value)


def _read_path(text: str, pos: int, whole: str) -> tuple[_Path, int]:
    head = _PATH_HEAD.match(text, pos)
    if head is None:
        raise MergeError(f"--key must start with a variable name: {whole!r}")
    steps: list[_Step] = []
    pos = head.end()
    while pos < len(text) and text[pos] != "=":
        m = _PATH_STEP.match(text, pos)
        if m is None:
            raise MergeError(
                f"cannot read --key {whole!r} at column {pos + 1}; use .name, [n], "
                '["text"] or [true], and SRC=DST for a copy'
            )
        if m.group("name") is not None or m.group("dq") is not None or m.group("sq") is not None:
            if m.group("name") is not None:
                value = m.group("name")
            else:
                quoted = m.group("dq") if m.group("dq") is not None else m.group("sq")
                value = _PATH_ESCAPE.sub(r"\1", quoted)
            data = value.encode("utf-8", "surrogateescape")
            spelling = "[" + _lua_quote(data).decode("utf-8", "backslashreplace") + "]"
            steps.append(_Step(("s", data), spelling, value))
        elif m.group("bool") is not None:
            flag = m.group("bool") == "true"
            steps.append(_Step(("b", flag), f"[{m.group('bool')}]", None))
        else:
            number = LuaNumber(b"", m.group("num")).as_float()
            steps.append(_Step(("n", number), f"[{_number_text(number)}]", None))
        pos = m.end()
    return _Path(head.group(), steps, text[:pos]), pos


def _parse_key(key: str) -> tuple[_Path, _Path]:
    """(SRC, DST) of a `--key` string: `PATH` or `SRC=DST`."""
    if not isinstance(key, str):
        raise MergeError(f"a --key is a str, not {type(key).__name__}")
    src, pos = _read_path(key, 0, key)
    if pos == len(key):
        return src, src
    rest = key[pos + 1 :]
    dst, end = _read_path(rest, 0, key)
    if end != len(rest):
        raise MergeError(f"--key {key!r} has more than one '='; use SRC=DST")
    return src, dst


# ─── Lua keys, as the loader sees them ───────────────────────────────────────


def _key_id(entry: Entry, position: int | None) -> _KeyId:
    if position is not None:
        return ("n", float(position))
    key = entry.key
    if isinstance(key, str):
        return ("s", key.encode("ascii"))
    if isinstance(key, LuaString):
        return ("s", key.data)
    if isinstance(key, LuaNumber):
        return ("n", key.as_float())
    if isinstance(key, LuaBool):
        return ("b", key.value)
    raise MergeError(f"an entry with no key: {entry.style}")


def _lua_text(raw: bytes) -> str:
    return raw.decode("utf-8", "backslashreplace")


def _segment(entry: Entry, position: int | None) -> str:
    """The entry's step as `wowlab sv dump` spells it."""
    if position is not None:
        return f"[{position}]"
    key = entry.key
    if isinstance(key, str):
        return f".{key}"
    if isinstance(key, LuaString):
        return f"[{_lua_text(key.raw)}]"
    if isinstance(key, LuaNumber):
        return f"[{key.raw}]"
    if isinstance(key, LuaBool):
        return "[true]" if key.value else "[false]"
    raise MergeError(f"an entry with no key: {entry.style}")


class _Keys:
    """A table's entries by Lua key: which entry Lua 5.1 loads for each key
    (keyed entries when reached, positional ones 50 at a time), in order of
    first appearance."""

    __slots__ = ("ids", "loaded", "npos", "positions", "table")

    def __init__(self, table: LuaTable) -> None:
        self.table = table
        self.ids: list[_KeyId] = []
        self.positions: list[int | None] = []
        self.loaded: dict[_KeyId, int] = {}
        pending: list[tuple[_KeyId, int]] = []
        npos = 0
        for index, entry in enumerate(table.entries):
            if len(pending) == _LFIELDS_PER_FLUSH:
                self.loaded.update(pending)
                pending.clear()
            position: int | None = None
            if entry.style is KeyStyle.POSITIONAL:
                npos += 1
                position = npos
            kid = _key_id(entry, position)
            self.ids.append(kid)
            self.positions.append(position)
            if position is not None:
                pending.append((kid, index))
            else:
                self.loaded[kid] = index
        self.loaded.update(pending)
        self.npos = npos

    def order(self) -> list[int]:
        """Indices of the loaded entries, in document order."""
        return [i for i, kid in enumerate(self.ids) if self.loaded[kid] == i]

    def value(self, kid: _KeyId) -> LuaValue | None:
        index = self.loaded.get(kid)
        return None if index is None else self.table.entries[index].value

    def spelled(self, index: int) -> str:
        return _segment(self.table.entries[index], self.positions[index])


_EMPTY = LuaTable(b"", (), b"")


def _keys(table: LuaTable | None) -> _Keys:
    return _Keys(_EMPTY if table is None else table)


# ─── values ──────────────────────────────────────────────────────────────────


def _number(value: LuaNumber) -> int | float:
    # The comparison `to_python` makes: the integer a whole-number spelling
    # denotes, else the double.
    return value.as_int() if value.is_integer_spelling else value.as_float()


def _same(a: LuaValue, b: LuaValue) -> bool:
    """Equal as data (layout and number spelling aside)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, LuaTable) and isinstance(b, LuaTable):
        ka, kb = _Keys(a), _Keys(b)
        if ka.loaded.keys() != kb.loaded.keys():
            return False
        return all(
            _same(a.entries[i].value, b.entries[kb.loaded[kid]].value)
            for kid, i in ka.loaded.items()
        )
    if isinstance(a, LuaString) and isinstance(b, LuaString):
        return a.data == b.data
    if isinstance(a, LuaNumber) and isinstance(b, LuaNumber):
        return _number(a) == _number(b)
    if isinstance(a, LuaBool) and isinstance(b, LuaBool):
        return a.value == b.value
    return isinstance(a, LuaNil)


def _show(value: LuaValue | None) -> str:
    if value is None:
        return "absent"
    if isinstance(value, LuaTable):
        n = len(value.entries)
        return f"table ({n} entr{'y' if n == 1 else 'ies'})"
    if isinstance(value, LuaString):
        return _lua_text(value.raw)
    if isinstance(value, LuaNumber):
        return value.raw
    if isinstance(value, LuaBool):
        return "true" if value.value else "false"
    return "nil"


def _bare(value: LuaValue, lead: bytes | None) -> LuaValue:
    """`value` for placing in ours: `lead` in front, and every slot inside a
    table left to the serializer (the target's style); scalars keep their
    text."""
    if not isinstance(value, LuaTable):
        return value._replace(lead=lead)
    return LuaTable(lead, tuple(_bare_entry(e) for e in value.entries), None)


def _bare_entry(entry: Entry) -> Entry:
    key = entry.key
    if isinstance(key, LuaString | LuaNumber | LuaBool):
        key = key._replace(lead=None)
    return Entry(
        lead=None,
        style=entry.style,
        key=key,
        key_close_lead=None,
        eq_lead=None,
        value=_bare(entry.value, None),
        sep_lead=None,
        sep=None,
        comment=None,
        duplicate=entry.duplicate,
    )


def _adopt(theirs: LuaValue, ours: LuaValue | None) -> LuaValue:
    return _bare(theirs, None if ours is None else ours.lead)


_BLANK = re.compile(rb"[ \t\f\v]*")


def _appended(table: LuaTable, entries: list[Entry], added: Sequence[Entry]) -> LuaTable:
    """`table` with `entries` (its own, some replaced) and `added` after them.

    The old last entry's trailing comment, which sits at the start of the
    closing bytes, stays with that entry (the serializer writes it after the
    entry when the slot after it is generated). Closing bytes that still hold
    a comment are then kept, so own-line comments before `}` stay there; any
    other closing is generated in the target's style, so each added entry
    gets its own line and a new positional entry its `-- [n]` where the
    layout has them."""
    close = table.close_lead
    if entries:
        last = entries[-1]
        if last.sep == b"":
            last = last._replace(sep=None)
        if last.comment is not None:
            stripped = False
            if isinstance(close, bytes):
                m = _BLANK.match(close)
                start = m.end() if m is not None else 0
                if close.startswith(last.comment, start):
                    close = close[start + len(last.comment) :]
                    stripped = True
            if not stripped:
                last = last._replace(comment=None)
        entries[-1] = last
    if isinstance(close, bytes) and b"--" not in close:
        close = None
    return LuaTable(table.lead, tuple(entries) + tuple(added), close)


def _new_entry(kid: _KeyId, like: Entry | None, npos: int, value: LuaValue) -> Entry:
    """An entry for key `kid`, appended to a table with `npos` positional
    entries: positional when it is the next index, else theirs' key (`like`)
    or, for a `--key` destination ours lacks, a key written from `kid`."""
    kind, raw_key = kid
    style: KeyStyle
    key: luadata.LuaKey
    if kind == "n" and isinstance(raw_key, float) and raw_key == npos + 1:
        style, key = KeyStyle.POSITIONAL, None
    elif like is not None and like.style is not KeyStyle.POSITIONAL:
        style = like.style
        key = (
            like.key._replace(lead=None)
            if isinstance(like.key, LuaString | LuaNumber | LuaBool)
            else like.key
        )
    elif kind == "n" and isinstance(raw_key, float):
        style, key = KeyStyle.NUMBER, LuaNumber(None, _number_text(raw_key))
    elif kind == "s" and isinstance(raw_key, bytes):
        style, key = KeyStyle.STRING, LuaString(None, _lua_quote(raw_key))
    elif kind == "b" and isinstance(raw_key, bool):
        style, key = KeyStyle.BOOLEAN, LuaBool(None, raw_key)
    else:  # pragma: no cover - every key id is one of the above
        raise MergeError(f"cannot write the key {kid!r}")
    return Entry(
        lead=None,
        style=style,
        key=key,
        key_close_lead=None,
        eq_lead=None,
        value=_bare(value, None),
        sep_lead=None,
        sep=None,
        comment=None,
        duplicate=False,
    )


# ─── the merge ───────────────────────────────────────────────────────────────


class _Merger:
    def __init__(self, *, three: bool, take: Side | None) -> None:
        self.three = three
        self.take = take
        # By path, in the order first met: one leaf is one item, however many
        # overlapping `--key` passes meet it (a later pass updates `taken`).
        self._conflicts: dict[str, Conflict] = {}
        self._taken: dict[str, Taken] = {}
        self._absent: dict[str, Absent] = {}

    @property
    def conflicts(self) -> tuple[Conflict, ...]:
        return tuple(self._conflicts.values())

    @property
    def taken(self) -> tuple[Taken, ...]:
        return tuple(self._taken.values())

    @property
    def absent(self) -> tuple[Absent, ...]:
        return tuple(self._absent.values())

    def took(self, path: str, value: LuaValue) -> None:
        self._taken[path] = Taken(path=path, value=_show(value))

    def missing(self, path: str, side: Side) -> None:
        self._absent.setdefault(path, Absent(path=path, missing_from=side))

    def conflict(self, item: Conflict) -> None:
        self._conflicts.setdefault(item.path, item)

    def slot(
        self, ours: LuaValue | None, theirs: LuaValue | None, base: LuaValue | None, path: str
    ) -> LuaValue | None:
        """What ours holds at `path` after the merge (None: still absent)."""
        if ours is not None and theirs is not None:
            return self.both(ours, theirs, base, path)
        if ours is not None:
            self.missing(path, "theirs")
            return ours
        if theirs is not None:
            if self.three and base is None:
                self.took(path, theirs)
                return _adopt(theirs, None)
            self.missing(path, "ours")
        return None

    def both(self, ours: LuaValue, theirs: LuaValue, base: LuaValue | None, path: str) -> LuaValue:
        if isinstance(ours, LuaTable) and isinstance(theirs, LuaTable):
            return self.table(ours, theirs, base if isinstance(base, LuaTable) else None, path)
        if _same(ours, theirs):
            return ours
        if self.three and base is not None:
            if _same(ours, base):
                self.took(path, theirs)
                return _adopt(theirs, ours)
            if _same(theirs, base):
                return ours
        self.conflict(
            Conflict(
                path=path,
                ours=_show(ours),
                theirs=_show(theirs),
                base=_show(base) if self.three and base is not None else None,
                resolved=self.take,
            )
        )
        if self.take == "theirs":
            self.took(path, theirs)
            return _adopt(theirs, ours)
        return ours

    def table(self, ours: LuaTable, theirs: LuaTable, base: LuaTable | None, path: str) -> LuaTable:
        o, t, b = _Keys(ours), _Keys(theirs), _keys(base)
        entries = list(ours.entries)
        changed = False
        for index in o.order():
            kid = o.ids[index]
            entry = entries[index]
            merged = self.slot(entry.value, t.value(kid), b.value(kid), path + o.spelled(index))
            if merged is not entry.value and merged is not None:
                entries[index] = entry._replace(value=merged)
                changed = True
        added: list[Entry] = []
        npos = o.npos
        for index in t.order():
            kid = t.ids[index]
            if kid in o.loaded:
                continue
            like = theirs.entries[index]
            merged = self.slot(None, like.value, b.value(kid), path + t.spelled(index))
            if merged is not None:
                entry = _new_entry(kid, like, npos, merged)
                npos += entry.style is KeyStyle.POSITIONAL
                added.append(entry)
        if added:
            return _appended(ours, entries, added)
        if not changed:
            return ours
        return ours._replace(entries=tuple(entries))

    def document(
        self, ours: LuaDocument, theirs: LuaDocument, base: LuaDocument | None
    ) -> LuaDocument:
        o, t = _top(ours), _top(theirs)
        b = _top(base) if base is not None else {}
        assignments = list(ours.assignments)
        changed = False
        for index, assignment in enumerate(ours.assignments):
            if o[assignment.name] != index:
                continue
            tv = _top_value(theirs, t, assignment.name)
            bv = _top_value(base, b, assignment.name)
            merged = self.slot(assignment.value, tv, bv, assignment.name)
            if merged is not assignment.value and merged is not None:
                assignments[index] = assignment._replace(value=merged)
                changed = True
        for index, assignment in enumerate(theirs.assignments):
            name = assignment.name
            if t[name] != index or name in o:
                continue
            merged = self.slot(None, assignment.value, _top_value(base, b, name), name)
            if merged is not None:
                assignments.append(Assignment(None, name, None, _bare(merged, None)))
                changed = True
        if not changed:
            return ours
        return ours._replace(assignments=tuple(assignments))


def _top(document: LuaDocument) -> dict[str, int]:
    """Top-level names to the assignment that counts (the client runs the
    file top to bottom, so the last)."""
    return {a.name: i for i, a in enumerate(document.assignments)}


def _top_value(document: LuaDocument | None, index: dict[str, int], name: str) -> LuaValue | None:
    if document is None or name not in index:
        return None
    return document.assignments[index[name]].value


# ─── a named subtree ─────────────────────────────────────────────────────────


class _Found:
    """What a path resolved to in one document: the value (None: absent) and
    the path spelled as far as it goes."""

    __slots__ = ("spelled", "value")

    def __init__(self, value: LuaValue | None, spelled: str) -> None:
        self.value = value
        self.spelled = spelled


def _find(document: LuaDocument | None, path: _Path) -> _Found:
    spelled = path.head
    if document is None:
        return _Found(None, spelled + "".join(s.spelling for s in path.steps))
    top = _top(document)
    value = _top_value(document, top, path.head)
    for n, step in enumerate(path.steps):
        if not isinstance(value, LuaTable):
            rest = "".join(s.spelling for s in path.steps[n:])
            return _Found(None, spelled + rest)
        keys = _Keys(value)
        index = keys.loaded.get(step.key_id)
        if index is None:
            rest = "".join(s.spelling for s in path.steps[n:])
            return _Found(None, spelled + rest)
        spelled += keys.spelled(index)
        value = value.entries[index].value
    return _Found(value, spelled)


class _Placer:
    """Replaces or adds the value at a path in ours, rebuilding only the
    tables on the way."""

    def __init__(self, merger: _Merger) -> None:
        self.merger = merger

    def place(
        self,
        ours: LuaDocument,
        path: _Path,
        change: _Change,
    ) -> LuaDocument:
        top = _top(ours)
        if path.head not in top:
            if path.steps:
                self.merger.missing(path.head, "ours")
                return ours
            new = change(None, path.head)
            if new is None:
                return ours
            return ours._replace(
                assignments=(*ours.assignments, Assignment(None, path.head, None, _bare(new, None)))
            )
        index = top[path.head]
        assignment = ours.assignments[index]
        if path.steps:
            new_value = self.inside(assignment.value, path.steps, path.head, change)
        else:
            placed = change(assignment.value, path.head)
            new_value = assignment.value if placed is None else placed
        if new_value is assignment.value:
            return ours
        assignments = list(ours.assignments)
        assignments[index] = assignment._replace(value=new_value)
        return ours._replace(assignments=tuple(assignments))

    def inside(
        self, value: LuaValue, steps: list[_Step], spelled: str, change: _Change
    ) -> LuaValue:
        if not isinstance(value, LuaTable):
            raise MergeError(
                f"{spelled} is not a table in the target, so it has no {steps[0].spelling}"
            )
        step, rest = steps[0], steps[1:]
        keys = _Keys(value)
        index = keys.loaded.get(step.key_id)
        if index is None:
            here = spelled + step.spelling
            if rest:
                self.merger.missing(here, "ours")
                return value
            new = change(None, here)
            if new is None:
                return value
            entry = _new_entry(step.key_id, None, keys.npos, new)
            return _appended(value, list(value.entries), [entry])
        entry = value.entries[index]
        here = spelled + keys.spelled(index)
        if rest:
            new_value = self.inside(entry.value, rest, here, change)
        else:
            placed = change(entry.value, here)
            new_value = entry.value if placed is None else placed
        if new_value is entry.value:
            return value
        entries = list(value.entries)
        entries[index] = entry._replace(value=new_value)
        return value._replace(entries=tuple(entries))


class _Change:
    """Called with ours' value at the destination (None: absent) and its
    spelled path; returns the value ours should hold there (None: none)."""

    def __call__(self, ours: LuaValue | None, path: str) -> LuaValue | None:  # pragma: no cover
        raise NotImplementedError


class _Copy(_Change):
    """Two-way `--key`: theirs' subtree, whole."""

    def __init__(self, merger: _Merger, source: _Found) -> None:
        self.merger = merger
        self.source = source

    def __call__(self, ours: LuaValue | None, path: str) -> LuaValue | None:
        theirs = self.source.value
        if theirs is None:
            self.merger.missing(self.source.spelled, "theirs")
            return ours
        if ours is not None and _same(ours, theirs):
            return ours
        self.merger.took(path, theirs)
        return _adopt(theirs, ours)


class _Limited(_Change):
    """Three-way `--key`: the three-way rules inside the subtree only."""

    def __init__(self, merger: _Merger, theirs: _Found, base: _Found) -> None:
        self.merger = merger
        self.theirs = theirs
        self.base = base

    def __call__(self, ours: LuaValue | None, path: str) -> LuaValue | None:
        if ours is None and self.theirs.value is None:
            self.merger.missing(self.theirs.spelled, "theirs")
            return None
        return self.merger.slot(ours, self.theirs.value, self.base.value, path)


# ─── the entry point ─────────────────────────────────────────────────────────


def merge(
    ours: LuaDocument,
    theirs: LuaDocument,
    *,
    base: LuaDocument | None = None,
    keys: Iterable[str] = (),
    take: Side | None = None,
) -> MergeResult:
    """Merge `theirs` into `ours` (§13.4; see the module docstring). Pure:
    reads nothing, writes nothing, and never changes its arguments (the
    documents are immutable). `MergeError` for a `--key` that cannot be
    read or placed, or a bad `take`."""
    for name, doc in (("ours", ours), ("theirs", theirs)):
        if not isinstance(doc, LuaDocument):
            raise MergeError(f"{name} is a luadata.LuaDocument, not {type(doc).__name__}")
    if base is not None and not isinstance(base, LuaDocument):
        raise MergeError(f"base is a luadata.LuaDocument, not {type(base).__name__}")
    if take not in (None, "ours", "theirs"):
        raise MergeError(f"take is 'ours', 'theirs' or None, not {take!r}")
    if isinstance(keys, str):
        raise MergeError("keys is a list of --key strings, not one string")
    parsed = [_parse_key(k) for k in keys]
    three = base is not None
    merger = _Merger(three=three, take=take)
    if not parsed:
        document = merger.document(ours, theirs, base)
    else:
        placer = _Placer(merger)
        document = ours
        for src, dst in parsed:
            if three:
                if not src.same(dst):
                    raise MergeError(
                        f"--key {src.source}={dst.source}: a copy between two paths is a "
                        "two-way merge; it takes no --base"
                    )
                change: _Change = _Limited(merger, _find(theirs, src), _find(base, src))
            else:
                change = _Copy(merger, _find(theirs, src))
            document = placer.place(document, dst, change)
    return MergeResult(
        document=document,
        mode="three-way" if three else "two-way",
        conflicts=merger.conflicts,
        taken=merger.taken,
        absent=merger.absent,
    )


# ─── the lab-addon's probe (§13.1, for the loader check) ─────────────────────


def read_probe(document: LuaDocument) -> Probe:
    """`WowLabCharDB.probe` of a character's `WowLab.lua`: `loads` (None
    when it is missing or not a whole number) and `lost` (true only when the
    file holds `lost = true`)."""
    top = _top(document)
    db = _top_value(document, top, LAB_ADDON_CHARACTER_VARIABLE)
    probe = _field(db, "probe")
    loads = _field(probe, "loads")
    lost = _field(probe, "lost")
    count: int | None = None
    if isinstance(loads, LuaNumber):
        try:
            count = loads.as_int()
        except ValueError:
            count = None
    return Probe(loads=count, lost=isinstance(lost, LuaBool) and lost.value)


def _field(table: LuaValue | None, name: str) -> LuaValue | None:
    if not isinstance(table, LuaTable):
        return None
    return _Keys(table).value(("s", name.encode("ascii")))


def check_keys(keys: Iterable[str]) -> list[bool]:
    """Raise `MergeError` for a `--key` string `merge` could not read, before
    anything else is done with it. For each key, whether it names one path
    on both sides (`PATH`, or `SRC=DST` with SRC and DST the same path)."""
    if isinstance(keys, str):
        raise MergeError("keys is a list of --key strings, not one string")
    same: list[bool] = []
    for key in keys:
        src, dst = _parse_key(key)
        same.append(src.same(dst))
    return same
