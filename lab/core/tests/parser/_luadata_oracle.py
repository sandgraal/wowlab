"""Test-side oracle for the `wowlab_core.luadata` graders (M10-04T).

Not a parser and not a serializer: nothing here is on the library's path.
It reads a parsed document through the seam pinned in
`test_luadata_fixtures.py` and turns it back into (a) the document's tokens
and (b) the document's bytes, taking layout from the source bytes and
everything else from the parse. Both graders therefore check the parse
alone; `serialize()` is M10-12's and is graded by M10-12T.

Kept deliberately small so a reviewer can check it against
`docs/LAB_FORMATS.md` §4.2 (as amended 2026-09-22) at a glance.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"

# The key styles §6.4 names, as the graders spell them.
POSITIONAL, STRING, NUMBER, NAME = "positional", "string", "number", "name"


def load() -> Any:
    """The module under test, imported per test so a missing module fails
    each grader rather than collection."""
    return importlib.import_module("wowlab_core.luadata")


def indexed(kind: str) -> list[str]:
    """Paths of every index row of `kind`, in index order."""
    found = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) > 1 and cells[1] == kind:
            found.append(cells[0].strip("`"))
    return found


# ── tokens ──────────────────────────────────────────────────────────────────

_TOKEN = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<comment>--[^\r\n]*)
    | (?P<string>"(?:[^"\\\r\n]|\\.)*"|'(?:[^'\\\r\n]|\\.)*')
    | (?P<number>-?(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?))
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<punct>[{}\[\]=,;])
    """,
    re.VERBOSE,
)


def source_tokens(data: bytes) -> list[str]:
    """Every token of a well-formed document in source order, separators
    (`,` and `;`) and whitespace dropped, comments kept verbatim."""
    text = data.decode("utf-8")
    out: list[str] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        assert m, f"oracle cannot tokenize at offset {pos}: {text[pos : pos + 20]!r}"
        if m.lastgroup not in ("ws",) and m.group() not in (",", ";"):
            out.append(m.group())
        pos = m.end()
    return out


def _scalar_text(lua: Any, value: Any) -> str:
    if isinstance(value, lua.LuaString | lua.LuaNumber):
        text = value.raw
        assert isinstance(text, str)
        return text
    if isinstance(value, lua.LuaBool):
        assert isinstance(value.value, bool)
        return "true" if value.value else "false"
    if isinstance(value, lua.LuaNil):
        return "nil"
    raise AssertionError(f"not one of the five value types of §6.4: {value!r}")


def _key_tokens(lua: Any, entry: Any) -> list[str]:
    style = entry.style
    if style == POSITIONAL:
        assert entry.key is None
        return []
    if style == NAME:
        assert isinstance(entry.key, str)
        return [entry.key, "="]
    if style == STRING:
        assert isinstance(entry.key, lua.LuaString)
    elif style == NUMBER:
        assert isinstance(entry.key, lua.LuaNumber)
    else:
        # §4.1 allows `[true]` / `[false]`; §6.4 does not name their style.
        assert isinstance(entry.key, lua.LuaBool), f"unknown key style {style!r}"
    return ["[", _scalar_text(lua, entry.key), "]", "="]


def document_tokens(lua: Any, doc: Any) -> list[str]:
    """The same projection as `source_tokens`, rebuilt from a parse."""
    out: list[str] = list(doc.leading_comments)

    def value(v: Any) -> Iterator[str]:
        if isinstance(v, lua.LuaTable):
            yield "{"
            for entry in v.entries:
                yield from _key_tokens(lua, entry)
                yield from value(entry.value)
                if entry.comment is not None:
                    yield entry.comment
            yield "}"
        else:
            yield _scalar_text(lua, v)

    for assignment in doc.assignments:
        out += [assignment.name, "="]
        out += list(value(assignment.value))
    out += list(doc.trailing_comments)
    return out


# ── bytes ───────────────────────────────────────────────────────────────────


def rebuild(lua: Any, doc: Any, data: bytes) -> bytes:
    """The document's bytes in the client's own layout (§4.2 as amended
    2026-09-22): one entry per line, `,` after every entry including the
    last, none after the `}` that closes a top-level assignment, a trailing
    comment after one space, `[key] = ` for bracketed keys and `name = ` for
    bare ones.

    Layout is taken from `data`: its line ending (CRLF if it has one), its
    indent unit (a tab per level if any line starts with a tab, otherwise
    none, as the Forever client writes) and its leading blank lines.
    Everything else (names, key styles, key and value source text, order,
    comments) comes from the parse."""
    text = data.decode("utf-8")
    eol = "\r\n" if "\r\n" in text else "\n"
    unit = "\t" if re.search(r"^\t", text, re.MULTILINE) else ""
    lead = re.match(r"(?:[ \t]*\r?\n)*", text)
    assert lead is not None
    out: list[str] = [lead.group()]
    for comment in doc.leading_comments:
        out += [comment, eol]

    def value(v: Any, level: int) -> str:
        if not isinstance(v, lua.LuaTable):
            return _scalar_text(lua, v)
        lines = ["{", eol]
        for entry in v.entries:
            key = _key_tokens(lua, entry)
            prefix = ""
            if key and key[0] == "[":
                prefix = f"[{key[1]}] = "
            elif key:
                prefix = f"{key[0]} = "
            line = unit * (level + 1) + prefix + value(entry.value, level + 1) + ","
            if entry.comment is not None:
                line += " " + entry.comment
            lines += [line, eol]
        lines.append(unit * level + "}")
        return "".join(lines)

    for assignment in doc.assignments:
        out += [f"{assignment.name} = ", value(assignment.value, 0), eol]
    for comment in doc.trailing_comments:
        out += [comment, eol]
    return "".join(out).encode("utf-8")


# ── walking ─────────────────────────────────────────────────────────────────


def tables(lua: Any, doc: Any) -> Iterator[tuple[int, Any]]:
    """Every table in the document with its nesting depth (a table assigned
    at top level is depth 1)."""
    stack = [(1, a.value) for a in reversed(doc.assignments)]
    while stack:
        depth, v = stack.pop()
        if isinstance(v, lua.LuaTable):
            yield depth, v
            stack += [(depth + 1, e.value) for e in reversed(v.entries)]


def entry(lua: Any, table: Any, key: str) -> Any:
    """The single string-keyed entry `key` of `table`."""
    hits = [
        e
        for e in table.entries
        if e.style == STRING and isinstance(e.key, lua.LuaString) and e.key.value == key
    ]
    assert len(hits) == 1, f"{key!r}: {len(hits)} entries"
    return hits[0]


def path(lua: Any, doc: Any, name: str, *keys: str) -> Any:
    """The value at `name` then string keys `keys`, through the parse."""
    [assignment] = [a for a in doc.assignments if a.name == name]
    v = assignment.value
    for key in keys:
        v = entry(lua, v, key).value
    return v
