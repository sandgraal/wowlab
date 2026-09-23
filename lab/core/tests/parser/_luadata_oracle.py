"""Test-side oracle for the `wowlab_core.luadata` graders (M10-04T).

Not a parser and not a serializer: nothing here is on the library's path.
It reads a parsed document through the seam pinned in
`test_luadata_fixtures.py` and turns it back into (a) the document's tokens
and (b) the document's bytes, from the document alone (`docs/LAB_PLAN.md`
§6.4, amendment of 2026-09-22, item 7). Nothing is taken from the source.

Kept small so a reviewer can check it against §6.4 at a glance.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"

# The key styles of §6.4 (as amended, item 8), as the graders spell them.
POSITIONAL, STRING, NUMBER, NAME, BOOLEAN = "positional", "string", "number", "name", "boolean"


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


def _b(value: Any) -> bytes:
    """Trivia may be absent where the grammar has no such token (`None`)."""
    if value is None:
        return b""
    assert isinstance(value, bytes), f"trivia must be bytes, not {type(value).__name__}"
    return value


# ── tokens (no trivia) ──────────────────────────────────────────────────────

_ESC = rb"\\(?:\r\n|\n\r|.)"
_TOKEN = re.compile(
    rb"(?P<ws>[ \t\r\n\f\v]+)"
    rb"|(?P<comment>--[^\r\n]*)"
    rb'|(?P<string>"(?:[^"\\\r\n]|' + _ESC + rb')*"|\'(?:[^\'\\\r\n]|' + _ESC + rb")*')"
    rb"|(?P<number>-?(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?))"
    rb"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    rb"|(?P<punct>[{}\[\]=,;])",
    re.DOTALL,
)


def source_tokens(data: bytes) -> list[bytes]:
    """Every token of a well-formed document in source order, separators
    included, whitespace and comments dropped."""
    out: list[bytes] = []
    pos = 0
    while pos < len(data):
        m = _TOKEN.match(data, pos)
        assert m, f"oracle cannot tokenize at offset {pos}: {data[pos : pos + 20]!r}"
        if m.lastgroup not in ("ws", "comment"):
            out.append(m.group())
        pos = m.end()
    return out


def _scalar(lua: Any, value: Any) -> bytes:
    if isinstance(value, lua.LuaString):
        assert isinstance(value.raw, bytes)
        assert value.raw[:1] in (b'"', b"'"), value.raw
        return value.raw
    if isinstance(value, lua.LuaNumber):
        assert isinstance(value.raw, str)
        return value.raw.encode("ascii")
    if isinstance(value, lua.LuaBool):
        assert isinstance(value.value, bool)
        return b"true" if value.value else b"false"
    if isinstance(value, lua.LuaNil):
        return b"nil"
    raise AssertionError(f"not one of the five value types of §6.4: {value!r}")


def _key_check(lua: Any, entry: Any) -> None:
    style = entry.style
    if style == POSITIONAL:
        assert entry.key is None
    elif style == NAME:
        assert isinstance(entry.key, str)
    elif style == STRING:
        assert isinstance(entry.key, lua.LuaString)
    elif style == NUMBER:
        assert isinstance(entry.key, lua.LuaNumber)
    else:
        assert style == BOOLEAN, f"unknown key style {style!r}"
        assert isinstance(entry.key, lua.LuaBool)


def document_tokens(lua: Any, doc: Any) -> list[bytes]:
    """The same projection as `source_tokens`, from the document."""

    def value(v: Any) -> Iterator[bytes]:
        if not isinstance(v, lua.LuaTable):
            yield _scalar(lua, v)
            return
        yield b"{"
        for e in v.entries:
            _key_check(lua, e)
            if e.style == NAME:
                yield from (e.key.encode("ascii"), b"=")
            elif e.style != POSITIONAL:
                yield from (b"[", _scalar(lua, e.key), b"]", b"=")
            yield from value(e.value)
            if e.sep:
                yield e.sep
        yield b"}"

    out: list[bytes] = []
    for a in doc.assignments:
        out += [a.name.encode("ascii"), b"="]
        out += list(value(a.value))
    return out


# ── bytes, from the document alone ──────────────────────────────────────────


def rebuild(lua: Any, doc: Any) -> bytes:
    """Every token with the bytes kept in front of it, then the document's
    tail. Positional entries have no key tokens, so their leading bytes may
    sit on the entry or on its value; either rebuilds the same."""

    def value(v: Any) -> bytes:
        head = _b(v.lead)
        if not isinstance(v, lua.LuaTable):
            return head + _scalar(lua, v)
        parts = [head, b"{"]
        for e in v.entries:
            _key_check(lua, e)
            parts.append(_b(e.lead))
            if e.style == NAME:
                parts += [e.key.encode("ascii"), _b(e.eq_lead), b"="]
            elif e.style != POSITIONAL:
                parts += [b"[", value(e.key), _b(e.key_close_lead), b"]", _b(e.eq_lead), b"="]
            parts += [value(e.value), _b(e.sep_lead), _b(e.sep)]
        parts += [_b(v.close_lead), b"}"]
        return b"".join(parts)

    out = [
        _b(a.lead) + a.name.encode("ascii") + _b(a.eq_lead) + b"=" + value(a.value)
        for a in doc.assignments
    ]
    return b"".join(out) + _b(doc.tail)


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
