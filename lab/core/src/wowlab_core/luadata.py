"""luadata: SavedVariables parser (docs/LAB_PLAN.md §6.4, docs/LAB_FORMATS.md §4, M10-04).

Load-bearing. SavedVariables are Lua-syntax *data*: this module reads them
with a constrained literal grammar and never evaluates anything (L3). It
imports no interpreter and no third-party parser, and it opens files
read-only (L1). The serializer half is M10-12.

Grammar (LAB_FORMATS §4.1 with the 2026-09-22 amendments; the client's Lua
is taken to be Lua 5.1, **[verify]** for Forever)::

    document   := { trivia | assignment } trivia
    assignment := NAME "=" ( value | "nil" )
    value      := table | string | number | "true" | "false"
    table      := "{" { entry ("," | ";") } [ entry ] "}"
    entry      := value                                     -- positional
                | "[" ( string | number | "true" | "false" ) "]" "=" value
                | NAME "=" value
    trivia     := whitespace | "--" line comment            -- long comments rejected

Everything else is refused with a `LuaDataError` carrying a 1-based line and
column (CRLF, LFCR, LF and CR each end one line, as in Lua 5.1; the column
counts bytes) and the offending token: function definitions, calls,
`setmetatable`, `..`, arithmetic, comparisons, `and`/`or`/`not`, bare
identifiers as values, long-bracket strings and long comments, a raw NUL
anywhere, statements other than a top-level assignment, `nil` inside a
table, and any number spelling other than the ones below (so every
non-finite spelling, until a fixture shows the client writing one).

Numbers: `-`? then a decimal integer, a decimal with a fractional part, either
with an exponent, or a hex integer (`0x1F`). `.5` and `5.` are refused (the
client does not write them). Every Lua 5.1 number is a double; `LuaNumber`
keeps the source text in `raw` and converts on request.

Strings are byte strings (Lua 5.1). `LuaString.raw` is the literal's source
bytes, quotes included; `.data` is the decoded bytes; `.value` is `data`
decoded as UTF-8 with `surrogateescape`, so invalid UTF-8 survives. Escapes
are Lua 5.1's: `\\a \\b \\f \\n \\r \\t \\v \\\\ \\" \\'`, `\\ddd` (one to three
digits, one byte, at most 255) and a backslash before a line break (CRLF,
LFCR, LF or CR, decoded to `\\n`). Any other escape is refused. A raw CR or
LF inside a literal is an unterminated string; other raw bytes are kept.

The document alone rebuilds the source byte for byte (§6.4 amendment item
7). Every token keeps the bytes in front of it (whitespace, line breaks,
comments) in a `lead`-style slot, every entry keeps its separator, and the
document keeps the bytes after the last token in `tail`:

    assignment   lead NAME eq_lead "=" value
    value        lead <token>              (a table: lead "{" entries close_lead "}")
    entry        lead [ "[" key key_close_lead "]" | NAME ] eq_lead "=" value sep_lead sep

A positional entry's leading bytes sit on the entry (its value's `lead` is
`b""`); a keyed entry's value `lead` is the bytes between `=` and the value.
A slot the grammar does not have for an entry is `b""`. `Entry.comment` is a
view of the line comment that follows the entry on its line (after its
separator, or after its value when it has none); the same bytes stay in the
next token's `lead`, which is what rebuilds the file.

Duplicates are kept in source order. `Entry.duplicate` is `True` only on the
later of two entries whose keys are equal under Lua key equality (`a` and
`["a"]`; `[1]`, `[1.0]` and the first positional entry; `[1]` and `["1"]`
differ; so do `[true]` and `[1]`).

Bounds (constructed hostile inputs, L8): a document over `MAX_FILE_BYTES`, a
table nested deeper than `MAX_DEPTH` (a table assigned at top level is depth
1), or a string literal whose source between the quotes is longer than
`MAX_STRING_BYTES` raises `LuaLimitError`; nothing is truncated. The parser
is iterative: a 10 000-deep table raises `LuaLimitError`, never
`RecursionError`.

The value and document types are immutable `NamedTuple`s (a frozen
dataclass costs about eight times as much to build, and a 50 MB file holds
millions of entries). They are tuples, so `len(table)` counts fields: count
entries with `len(table.entries)`.
"""

from __future__ import annotations

import gc
import math
import os
import re
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, NoReturn

__all__ = [
    "MAX_DEPTH",
    "MAX_FILE_BYTES",
    "MAX_STRING_BYTES",
    "Assignment",
    "Entry",
    "KeyStyle",
    "LuaBool",
    "LuaDataError",
    "LuaDocument",
    "LuaKey",
    "LuaLimitError",
    "LuaNil",
    "LuaNumber",
    "LuaString",
    "LuaTable",
    "LuaValue",
    "parse",
    "read",
]

_MIB = 1024 * 1024
#: Deepest table nesting accepted; a table assigned at top level is depth 1.
MAX_DEPTH = 200
#: Largest document accepted, in bytes (§6.4: 256 MB, read as MiB).
MAX_FILE_BYTES = 256 * _MIB
#: Longest string literal accepted, counted on its source bytes between the quotes.
MAX_STRING_BYTES = 64 * _MIB

# Lua 5.1 stores pending positional entries this many at a time
# (`LFIELDS_PER_FLUSH` in lopcodes.h); §6.4 amendment item 6, **[verify]**.
_LFIELDS_PER_FLUSH = 50


# ── errors ──────────────────────────────────────────────────────────────────


class LuaDataError(ValueError):
    """A document the constrained grammar refuses. Never evaluated.

    `line` and `column` are 1-based (the column counts bytes); `token` is the
    offending source bytes, `b""` at the end of input.
    """

    def __init__(self, message: str, *, line: int, column: int, token: bytes) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.token = token
        super().__init__(f"line {line}, column {column}: {message} (at {token!r})")


class LuaLimitError(LuaDataError):
    """A depth, file-size or string-length bound was exceeded (and nothing else)."""


# ── the document model ──────────────────────────────────────────────────────


class KeyStyle(StrEnum):
    """How an entry's key is written (§6.4, amendment item 8)."""

    POSITIONAL = "positional"
    STRING = "string"  # ["text"] or ['text']
    NUMBER = "number"  # [42]
    NAME = "name"  # bare identifier, `key = value`
    BOOLEAN = "boolean"  # [true] / [false]


class LuaString(NamedTuple):
    """A string literal. `raw` is its source bytes, quotes included."""

    lead: bytes
    raw: bytes

    @property
    def data(self) -> bytes:
        """The decoded bytes (Lua 5.1 escapes; `\\ddd` is one byte)."""
        raw = self.raw
        if b"\\" not in raw:
            return raw[1:-1]
        return _unescape(raw[1:-1])

    @property
    def value(self) -> str:
        """`data` decoded as UTF-8 with `surrogateescape` (lossless, not always
        JSON-safe: invalid UTF-8 becomes lone surrogates)."""
        return self.data.decode("utf-8", "surrogateescape")


class LuaNumber(NamedTuple):
    """A number literal. `raw` is its source text, kept exactly (`100.000`,
    `1E-07`, `0x1F` and `-0` stay as written). Lua 5.1 holds every number as
    a double; the conversions read the text."""

    lead: bytes
    raw: str

    @property
    def is_integer_spelling(self) -> bool:
        """True when the text is written as an integer (decimal digits or hex,
        no point, no exponent). A spelling, not a client type."""
        return _INT_SPELLING.fullmatch(self.raw) is not None

    def as_int(self) -> int:
        """The integer the text spells, exactly (`9007199254740993` stays that
        integer, although the client loads the nearest double). A decimal or
        exponent spelling of a whole number (`1.0`, `1e+15`) gives that whole
        number; anything else raises `ValueError`."""
        text = self.raw
        if _INT_SPELLING.fullmatch(text) is not None:
            return _spelled_int(text)
        number = self.as_float()
        if math.isfinite(number) and number.is_integer():
            return int(number)
        raise ValueError(f"{text!r} is not a whole number")

    def as_float(self) -> float:
        """The double the text denotes (IEEE round-to-nearest; `-0` keeps its
        sign; a hex integer beyond the double range gives an infinity)."""
        return _spelled_float(self.raw)


class LuaBool(NamedTuple):
    """`true` or `false`."""

    lead: bytes
    value: bool


class LuaNil(NamedTuple):
    """`nil`; legal only as a top-level value (§4.1)."""

    lead: bytes


class LuaTable(NamedTuple):
    """A table constructor: `lead "{" entries close_lead "}"`."""

    lead: bytes
    entries: tuple[Entry, ...]
    close_lead: bytes


LuaValue = LuaTable | LuaString | LuaNumber | LuaBool | LuaNil
#: `None` (positional), `LuaString`, `LuaNumber`, the identifier (bare name) or `LuaBool`.
LuaKey = LuaString | LuaNumber | LuaBool | str | None


class Entry(NamedTuple):
    """One table entry, in source order.

    `key` is `None` (positional), a `LuaString`, a `LuaNumber`, the bare
    identifier as a `str`, or a `LuaBool`. `sep` is `b","`, `b";"` or `b""`.
    `comment` is a view of the line comment after the entry on its line,
    from `--` to the line break, or `None`. `duplicate` flags the later of
    two entries with equal Lua keys.
    """

    lead: bytes
    style: KeyStyle
    key: LuaKey
    key_close_lead: bytes
    eq_lead: bytes
    value: LuaValue
    sep_lead: bytes
    sep: bytes
    comment: bytes | None
    duplicate: bool


class Assignment(NamedTuple):
    """A top-level `name = value`."""

    lead: bytes
    name: str
    eq_lead: bytes
    value: LuaValue


class LuaDocument(NamedTuple):
    """A parsed SavedVariables file: its assignments in source order and the
    bytes after the last token."""

    assignments: tuple[Assignment, ...]
    tail: bytes

    def to_python(self) -> dict[str, object]:
        """Plain `dict`/`list`/scalars, a fresh copy on every call. One-way and
        lossy, for consumers that do not need fidelity:

        - Top level: every assigned name, in order of first appearance; a
          name assigned twice takes the later value (the client runs the file
          top to bottom); `X = nil` gives `None` (it differs from a file that
          never names `X`).
        - Strings become `str` (`LuaString.value`; invalid UTF-8 appears as
          lone surrogates). Numbers become `int` when written as an integer
          (decimal or hex) and `float` otherwise: this follows the spelling,
          not a client type, since Lua 5.1 holds every number as a double.
        - A table becomes a `list` only when its keys, as Lua 5.1 would load
          them, are exactly `1..n`; otherwise a `dict` keyed in order of first
          appearance; an empty table is `{}`. For duplicate keys the value is
          the one Lua 5.1 would load: keyed entries are stored when reached,
          positional ones 50 at a time (before the next field once 50 are
          pending, and at the closing brace).
        - Python's own key equality applies to the result: `[true]` and `[1]`
          are distinct Lua keys but collide in a `dict` (`True == 1`), and the
          later value wins.
        """
        out: dict[str, object] = {}
        for assignment in self.assignments:
            out[assignment.name] = _to_python(assignment.value)
        return out


# ── numbers and strings ─────────────────────────────────────────────────────

_INT_SPELLING = re.compile(r"-?(?:0[xX][0-9A-Fa-f]+|[0-9]+)")
_FLOAT_SPELLING = re.compile(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _spelled_int(text: str) -> int:
    negative = text.startswith("-")
    body = text[1:] if negative else text
    if body[:2] in ("0x", "0X"):
        number = int(body[2:], 16)
    elif len(body) <= 4000:
        number = int(body, 10)
    else:  # past `int()`'s default digit limit for text; exact all the same
        number = int(Decimal(body))
    return -number if negative else number


def _spelled_float(text: str) -> float:
    if _INT_SPELLING.fullmatch(text) is not None:
        if "x" not in text and "X" not in text:
            return float(text)
        number = _spelled_int(text)
        try:
            return float(number) if number or not text.startswith("-") else -0.0
        except OverflowError:
            return math.copysign(math.inf, number)
    if _FLOAT_SPELLING.fullmatch(text) is None:
        raise ValueError(f"{text!r} is not a Lua number spelling this parser accepts")
    return float(text)


_SIMPLE_ESCAPES = {
    ord("a"): b"\x07",
    ord("b"): b"\x08",
    ord("f"): b"\x0c",
    ord("n"): b"\n",
    ord("r"): b"\r",
    ord("t"): b"\t",
    ord("v"): b"\x0b",
    ord("\\"): b"\\",
    ord('"'): b'"',
    ord("'"): b"'",
}
_ESCAPE = re.compile(rb"\\(?:([0-9]{1,3})|(\r\n|\n\r|\r|\n)|(.))", re.DOTALL)


def _unescape_one(m: re.Match[bytes]) -> bytes:
    digits, _brk, other = m.groups()
    if digits is not None:
        code = int(digits)
        if code > 255:
            raise ValueError(f"escape {m.group()!r} is above 255")
        return bytes((code,))
    if other is None:
        return b"\n"
    replacement = _SIMPLE_ESCAPES.get(other[0])
    if replacement is None:
        raise ValueError(f"escape {m.group()!r} is not a Lua 5.1 escape")
    return replacement


def _unescape(body: bytes) -> bytes:
    parts: list[bytes] = []
    last = 0
    for m in _ESCAPE.finditer(body):
        parts += (body[last : m.start()], _unescape_one(m))
        last = m.end()
    rest = body[last:]
    if b"\\" in rest:
        raise ValueError("string ends in a lone backslash")
    parts.append(rest)
    return b"".join(parts)


def _escape_over_255(raw: bytes) -> bool:
    """For a literal whose escapes the grammar has already checked, whether
    one `\\ddd` is above 255. A backslash starts an escape when an even
    run of backslashes precedes it, so the check is one linear search."""
    return _OVER_255.search(raw) is not None


# An odd backslash (after a whole run of `\\\\` pairs), then three digits
# above 255 (a `\\ddd` escape takes at most three digits).
_OVER_255 = re.compile(rb"(?<!\\)(?:\\\\)*+\\(?:25[6-9]|2[6-9][0-9]|[3-9][0-9]{2})")


# ── the grammar as regular expressions ──────────────────────────────────────
#
# Every regex below is complete for what the grammar accepts, so a failed
# match always means the input is refused. The diagnosis (which token, what
# message) is then done by `_Diagnoser`, token by token. All repetition sits
# in atomic groups or is possessive, so a failed match never backtracks
# (a long unterminated string or run of trivia is scanned once).

_WS = rb"[ \t\r\n\f\v]"
_TRIVIA = rb"(?>" + _WS + rb"*(?:--(?!\[=*\[)[^\r\n\x00]*" + _WS + rb"*)*)"
_DQ = rb'(?>"[^"\\\r\n\x00]*+(?:\\(?:\r\n|\n\r|[0-9]{1,3}+|[abfnrtv\\"\'\r\n])[^"\\\r\n\x00]*+)*+")'
_SQ = rb"(?>'[^'\\\r\n\x00]*+(?:\\(?:\r\n|\n\r|[0-9]{1,3}+|[abfnrtv\\\"'\r\n])[^'\\\r\n\x00]*+)*+')"
_STR = _DQ + rb"|" + _SQ
_NUM = rb"(?>-?(?:0[xX][0-9A-Fa-f]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?))(?![0-9A-Za-z_.])"
_BOOL = rb"(?:true|false)(?![A-Za-z0-9_])"
_KEYWORDS = (
    b"and break do else elseif end false for function if in local nil not or "
    b"repeat return then true until while"
).split()
_NAME = rb"(?!(?:" + b"|".join(_KEYWORDS) + rb")(?![A-Za-z0-9_]))(?>[A-Za-z_][A-Za-z0-9_]*)"

# Top level: an assignment, or the end of the document.
_ASSIGN = re.compile(
    rb"(" + _TRIVIA + rb")(?:"
    rb"(" + _NAME + rb")(" + _TRIVIA + rb")=(?!=)(" + _TRIVIA + rb")"
    rb"(?:(" + _STR + rb")|(" + _NUM + rb")|(" + _BOOL + rb")|(nil)(?![A-Za-z0-9_])|(\{))"
    rb"|(\Z))"
)
# Inside a table, where an entry or the closing brace may start.
_ENTRY = re.compile(
    rb"(" + _TRIVIA + rb")(?:(\})|"
    rb"(?:(?:\[(" + _TRIVIA + rb")(?:(" + _STR + rb")|(" + _NUM + rb")|(" + _BOOL + rb"))"
    rb"(" + _TRIVIA + rb")\]|(" + _NAME + rb"))(" + _TRIVIA + rb")=(?!=)(" + _TRIVIA + rb"))?"
    rb"(?:(?:(" + _STR + rb")|(" + _NUM + rb")|(" + _BOOL + rb"))(" + _TRIVIA + rb")([,;]?)|(\{)))"
)
# After an entry with no separator: only the closing brace.
_CLOSE = re.compile(rb"(" + _TRIVIA + rb")\}")
# After a table-valued entry closes: an optional separator.
_SEP = re.compile(rb"(" + _TRIVIA + rb")([,;]?)")
# The comment view: a line comment following on the same line.
_COMMENT_AFTER = re.compile(rb"[ \t\f\v]*(--[^\r\n\x00]*)")
_TRIVIA_RE = re.compile(_TRIVIA)
_NUM_RE = re.compile(_NUM)
_NAME_RUN = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
_LINE_BREAK = re.compile(rb"\r\n|\n\r|\r|\n")
_LONG_OPEN = re.compile(rb"\[=*\[")
_PLAIN_DQ = re.compile(rb'[^"\\\r\n\x00]*').match
_PLAIN_SQ = re.compile(rb"[^'\\\r\n\x00]*").match
_SIMPLE_ESCAPES_RUN = re.compile(rb"(?:\\[abfnrtv\\\"'])++").match
_BAD_RUN = re.compile(rb"[^ \t\r\n\f\v,;{}\[\]=]+")


def _position(data: bytes, offset: int) -> tuple[int, int]:
    """1-based line and byte column of `offset`; CRLF, LFCR, LF and CR each
    end one line (Lua 5.1's `inclinenumber`)."""
    line = 1
    start = 0
    for m in _LINE_BREAK.finditer(data, 0, offset):
        line += 1
        start = m.end()
    return line, offset - start + 1


def _error(
    data: bytes, offset: int, token: bytes, message: str, cls: type[LuaDataError] = LuaDataError
) -> LuaDataError:
    line, column = _position(data, offset)
    return cls(message, line=line, column=column, token=token)


# ── diagnosis of a refused input ────────────────────────────────────────────


class _Diagnoser:
    """Walks the input token by token from where a grammar regex failed and
    raises the positioned error. Reached only for refused input."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def fail(self, offset: int, token: bytes, message: str) -> NoReturn:
        raise _error(self.data, offset, token, message)

    def skip(self, pos: int) -> int:
        data = self.data
        m = _TRIVIA_RE.match(data, pos)
        assert m is not None  # the trivia pattern matches the empty string
        p = m.end()
        if data.startswith(b"--", p):
            opener = re.match(rb"--\[=*\[", data[p : p + 64])
            token = opener.group() if opener else data[p : p + 2]
            self.fail(p, token, "long comments are rejected (§6.4)")
        return p

    def token(self, p: int) -> tuple[str, int, int]:
        """(kind, start, end) of the token at `p`. Kinds: "eof", "string",
        "number", "name", "longbracket", one of `{}[]=,;`, or "other"."""
        data = self.data
        n = len(data)
        if p >= n:
            return "eof", p, p
        c = data[p]
        if c == 0:
            self.fail(p, b"\x00", "a NUL byte is rejected (§4.3)")
        if c in b"\"'":
            return "string", p, self.string(p)
        if c in b"0123456789" or (c == 0x2D and data[p + 1 : p + 2].isdigit()):
            m = _NUM_RE.match(data, p)
            if m is None:
                run = _BAD_RUN.match(data, p)
                token = run.group() if run else data[p : p + 1]
                self.fail(p, token, "not a number this parser accepts (§4.2, §6.4)")
            return "number", p, m.end()
        if c == 0x5F or 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A:
            m = _NAME_RUN.match(data, p)
            assert m is not None
            return "name", p, m.end()
        if c == 0x5B and _LONG_OPEN.match(data, p):
            return "longbracket", p, p + 1
        if c == 0x3D and data[p + 1 : p + 2] == b"=":
            return "other", p, p + 2
        if c in b"{}[]=,;":
            return chr(c), p, p + 1
        for op in (b"...", b"..", b"~=", b"<=", b">=", b"=="):
            if data.startswith(op, p):
                return "other", p, p + len(op)
        run = _BAD_RUN.match(data, p)
        end = run.end() if run and c == 0x2D else p + 1
        return "other", p, end

    def string(self, p: int) -> int:
        """End of the string literal at `p`, or the positioned error."""
        data = self.data
        n = len(data)
        quote = data[p]
        plain = _PLAIN_DQ if quote == 0x22 else _PLAIN_SQ
        i = p + 1
        while True:
            run = plain(data, i)  # a run of ordinary bytes, at C speed
            i = run.end() if run else i
            if i >= n:
                self.fail(p, data[p : min(i, p + 40)], "unterminated string")
            c = data[i]
            if c == quote:
                return i + 1
            if c == 0x5C:
                simple = _SIMPLE_ESCAPES_RUN(data, i)
                if simple:
                    i = simple.end()
                    continue
                d = data[i + 1] if i + 1 < n else -1
                if d == -1:
                    self.fail(p, data[p : min(i, p + 40)], "unterminated string")
                if d in b"abfnrtv\\\"'":
                    i += 2
                elif d in (0x0A, 0x0D):
                    i += 2
                    if i < n and data[i] in (0x0A, 0x0D) and data[i] != d:
                        i += 1
                elif 0x30 <= d <= 0x39:
                    j = i + 1
                    while j < n and j < i + 4 and 0x30 <= data[j] <= 0x39:
                        j += 1
                    if int(data[i + 1 : j]) > 255:
                        self.fail(i, data[i:j], "decimal escape above 255")
                    i = j
                else:
                    self.fail(i, data[i : i + 2], "not a Lua 5.1 escape")
            elif c in (0x0A, 0x0D):
                self.fail(p, data[p : min(i, p + 40)], "unterminated string (raw line break)")
            elif c == 0:
                self.fail(i, b"\x00", "a NUL byte is rejected (§4.3)")
            else:
                i += 1

    def peek(self, pos: int, width: int = 1) -> bytes:
        """The next `width` bytes after trivia, without raising (a lookahead
        must not report a later error before the one being diagnosed)."""
        m = _TRIVIA_RE.match(self.data, pos)
        p = m.end() if m else pos
        return self.data[p : p + width]

    def text(self, kind: str, start: int, end: int) -> bytes:
        return b"" if kind == "eof" else self.data[start:end]

    def value(self, pos: int, *, allow_nil: bool) -> tuple[str, int]:
        """Checks one value head at `pos`; returns (kind, end) when it is
        acceptable, raises otherwise."""
        data = self.data
        kind, s, e = self.token(self.skip(pos))
        if kind in ("string", "number", "{"):
            return kind, e
        if kind == "name":
            word = data[s:e]
            if word in (b"true", b"false"):
                return "bool", e
            if word == b"nil":
                if allow_nil:
                    return "nil", e
                self.fail(s, word, "nil is legal only as a top-level value (§4.1)")
            if word == b"function":
                self.fail(s, word, "function definitions are rejected (L3)")
            if word in _KEYWORDS:
                self.fail(s, word, f"keyword {word.decode()!r} is not a value")
            if self.peek(e) in (b'"', b"'", b"{", b"(", b":"):
                self.fail(s, word, "calls are rejected (L3)")
            self.fail(s, word, "bare identifiers are not values (L3)")
        if kind == "longbracket":
            opener = _LONG_OPEN.match(data, s)
            token = opener.group() if opener else data[s : s + 1]
            self.fail(s, token, "long-bracket strings are rejected (§6.4)")
        if kind == "eof":
            self.fail(s, b"", "unexpected end of input, expected a value")
        self.fail(s, data[s:e], "expected a value (operators and expressions are rejected)")

    def internal(self, pos: int) -> NoReturn:
        self.fail(pos, self.data[pos : pos + 20], "input refused (no single offending token)")

    def top(self, pos: int) -> NoReturn:
        data = self.data
        kind, s, e = self.token(self.skip(pos))
        if kind != "name":
            what = "end of input" if kind == "eof" else "this"
            self.fail(s, self.text(kind, s, e), f"expected `name = value`, found {what}")
        word = data[s:e]
        if word in _KEYWORDS:
            self.fail(s, word, "only top-level `name = value` assignments are accepted")
        kind, s2, e2 = self.token(self.skip(e))
        if kind != "=":
            self.fail(s2, self.text(kind, s2, e2), "expected `=` after the name")
        self.value(e2, allow_nil=True)
        self.internal(pos)

    def entry(self, pos: int, table_at: int) -> NoReturn:
        data = self.data
        p = self.skip(pos)
        kind, s, e = self.token(p)
        if kind == "eof":
            self.unterminated(table_at)
        if kind == "[":
            kind, s2, e2 = self.token(self.skip(e))
            word = data[s2:e2]
            if kind == "name" and word in (b"true", b"false"):
                pass
            elif kind == "name" and word == b"nil":
                self.fail(s2, word, "nil cannot be a key")
            elif kind in ("string", "number"):
                pass
            else:
                self.fail(
                    s2,
                    self.text(kind, s2, e2),
                    "a bracketed key is a string, a number, true or false",
                )
            kind, s3, e3 = self.token(self.skip(e2))
            if kind != "]":
                self.fail(s3, self.text(kind, s3, e3), "expected `]`")
            kind, s4, e4 = self.token(self.skip(e3))
            if kind != "=":
                self.fail(s4, self.text(kind, s4, e4), "expected `=` after the key")
            vkind, end = self.value(e4, allow_nil=False)
        elif kind == "name" and data[s:e] not in _KEYWORDS:
            if self.peek(e) != b"=" or self.peek(e, 2) == b"==":
                self.value(p, allow_nil=False)  # a bare identifier: raises
            vkind, end = self.value(self.skip(e) + 1, allow_nil=False)
        else:
            vkind, end = self.value(p, allow_nil=False)
        if vkind == "{":
            self.internal(pos)
        self.close(end, table_at)

    def close(self, pos: int, table_at: int) -> NoReturn:
        kind, s, e = self.token(self.skip(pos))
        if kind == "eof":
            self.unterminated(table_at)
        if kind in (",", ";", "}"):
            self.internal(pos)
        self.fail(s, self.text(kind, s, e), "expected `,`, `;` or `}` after a table entry")

    def unterminated(self, table_at: int) -> NoReturn:
        self.fail(table_at, b"{", "unterminated table (end of input before its `}`)")


# ── the parser ──────────────────────────────────────────────────────────────


_TRUE_KEY = object()
_FALSE_KEY = object()
_P = KeyStyle.POSITIONAL
_S = KeyStyle.STRING
_N = KeyStyle.NUMBER
_W = KeyStyle.NAME
_B = KeyStyle.BOOLEAN

_new = tuple.__new__


def parse(data: bytes) -> LuaDocument:
    """Parse a SavedVariables document from its bytes. Never evaluates.

    Raises `LuaDataError` (with line, column and token) for anything the
    grammar refuses, and `LuaLimitError` for the depth, size and string
    bounds.
    """
    data = bytes(data)  # the same object when it is already `bytes`
    if len(data) > MAX_FILE_BYTES:
        raise LuaLimitError(
            f"document is {len(data)} bytes, over the {MAX_FILE_BYTES}-byte bound",
            line=1,
            column=1,
            token=b"",
        )
    # The parse builds millions of small tuples and no reference cycles, so
    # the cyclic collector would only re-scan them (about half the parse
    # time on a 50 MB file). It is paused for the parse and restored after.
    collecting = gc.isenabled()
    gc.disable()
    try:
        return _Parser(data).run()
    finally:
        if collecting:
            gc.enable()


def read(path: str | Path) -> LuaDocument:
    """Read and parse one file. Opens it read-only, reads it once and writes
    nothing anywhere (L1). The size bound is checked before reading."""
    with Path(path).open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > MAX_FILE_BYTES:
            raise LuaLimitError(
                f"file is {size} bytes, over the {MAX_FILE_BYTES}-byte bound",
                line=1,
                column=1,
                token=b"",
            )
        data = handle.read(MAX_FILE_BYTES + 1)
    return parse(data)


class _Parser:
    __slots__ = ("data",)

    def __init__(self, data: bytes) -> None:
        self.data = data

    def run(self) -> LuaDocument:
        """One flat loop over grammar-sized matches; the open tables live on
        an explicit stack, so nesting never recurses."""
        data = self.data
        intern = {b"": b"", b"\r\n": b"\r\n", b"\n": b"\n"}.setdefault
        assignments: list[Assignment] = []
        # Each open table's saved state: [at, lead, head, entries, seen, npos].
        # `head` is what the table's parent needs when it closes: an
        # assignment's (lead, name, eq_lead) or an entry's (lead, style, key,
        # key_close_lead, eq_lead, duplicate).
        stack: list[list[object]] = []
        entry_match = _ENTRY.match
        close_match = _CLOSE.match
        sep_match = _SEP.match
        comment_match = _COMMENT_AFTER.match
        # The innermost open table, unpacked into locals.
        at = 0
        t_lead = b""
        head: tuple[object, ...] = ()
        entries: list[Entry] = []
        append = entries.append
        seen: set[object] = set()  # keys of keyed entries (positional keys are 1..npos)
        npos = 0
        depth = 0
        pos = 0
        need_close = False
        value: LuaValue
        key: LuaKey
        lk: object
        while True:
            if not depth:
                m = _ASSIGN.match(data, pos)
                if m is None:
                    _Diagnoser(data).top(pos)
                lead, name, eq, vl, vs, vn, vb, _nil, vt, _end = m.groups(b"")
                if m.end() - pos > MAX_STRING_BYTES:
                    self.check_string_bound(m, 5)
                if not name:
                    return _new(LuaDocument, (tuple(assignments), lead))
                pos = m.end()
                if vt:
                    at, t_lead, head = pos - 1, vl, (lead, name.decode("ascii"), eq)
                    entries = []
                    append = entries.append
                    seen = set()
                    npos = 0
                    depth = 1
                    continue
                if vs:
                    if b"\\" in vs and _escape_over_255(vs):
                        _Diagnoser(data).top(m.start())
                    value = _new(LuaString, (vl, vs))
                elif vn:
                    value = _new(LuaNumber, (vl, vn.decode("ascii")))
                elif vb:
                    value = _new(LuaBool, (vl, vb == b"true"))
                else:
                    value = _new(LuaNil, (vl,))
                assignments.append(_new(Assignment, (lead, name.decode("ascii"), eq, value)))
                continue

            if need_close:
                m = close_match(data, pos)
                if m is None:
                    _Diagnoser(data).close(pos, at)
                close_lead = m.group(1)
                pos = m.end()
                need_close = False
            else:
                m = entry_match(data, pos)
                if m is None:
                    _Diagnoser(data).entry(pos, at)
                (lead, close, kl, ks, kn, kb, kc, kw, ke, vl, vs, vn, vb, sl, sep, vt) = m.groups(
                    b""
                )
                if not close:
                    if m.end() - pos > MAX_STRING_BYTES:
                        self.check_string_bound(m, 4, 11)
                    lead = intern(lead, lead)
                    if ks:
                        if b"\\" in ks:
                            if _escape_over_255(ks):
                                _Diagnoser(data).entry(pos, at)
                            lk = _unescape(ks[1:-1])
                        else:
                            lk = ks[1:-1]
                        style = _S
                        key = _new(LuaString, (kl, ks))
                        dup = lk in seen
                        seen.add(lk)
                    elif kn:
                        style = _N
                        text = kn.decode("ascii")
                        try:
                            number = float(kn)  # every decimal spelling
                        except ValueError:
                            number = _spelled_float(text)  # hex
                        key = _new(LuaNumber, (kl, text))
                        dup = number in seen or (number.is_integer() and 1 <= number <= npos)
                        seen.add(number)
                    elif kw:
                        style = _W
                        key = kw.decode("ascii")
                        dup = kw in seen
                        seen.add(kw)
                    elif kb:
                        style = _B
                        truth = kb == b"true"
                        key = _new(LuaBool, (kl, truth))
                        lk = _TRUE_KEY if truth else _FALSE_KEY
                        dup = lk in seen
                        seen.add(lk)
                    else:
                        style = _P
                        key = None
                        npos += 1
                        dup = npos in seen if seen else False
                    if vt:
                        if depth >= MAX_DEPTH:
                            raise _error(
                                data,
                                m.end() - 1,
                                b"{",
                                f"tables nested deeper than {MAX_DEPTH}",
                                LuaLimitError,
                            )
                        stack.append([at, t_lead, head, entries, seen, npos])
                        pos = m.end()
                        at, t_lead, head = pos - 1, vl, (lead, style, key, kc, ke, dup)
                        entries = []
                        append = entries.append
                        seen = set()
                        npos = 0
                        depth += 1
                        continue
                    if vs:
                        if b"\\" in vs and _escape_over_255(vs):
                            _Diagnoser(data).entry(pos, at)
                        value = _new(LuaString, (vl, vs))
                    elif vn:
                        value = _new(LuaNumber, (vl, vn.decode("ascii")))
                    else:
                        value = _new(LuaBool, (vl, vb == b"true"))
                    if sep:
                        pos = m.end()
                    else:
                        pos = m.end() - len(sl)
                        sl = b""
                        need_close = True
                    comment = None
                    c = data[pos : pos + 1]
                    if c == b" " or c == b"-" or c == b"\t":
                        cm = comment_match(data, pos)
                        if cm is not None:
                            comment = cm.group(1)
                    append(_new(Entry, (lead, style, key, kc, ke, value, sl, sep, comment, dup)))
                    continue
                close_lead = intern(lead, lead)
                pos = m.end()

            # The innermost table closes.
            table = _new(LuaTable, (t_lead, tuple(entries), close_lead))
            closed = head
            depth -= 1
            if not depth:
                a_lead, a_name, a_eq = closed
                assignments.append(_new(Assignment, (a_lead, a_name, a_eq, table)))
                continue
            at, t_lead, head, entries, seen, npos = stack.pop()  # type: ignore[assignment]
            append = entries.append
            sm = sep_match(data, pos)
            assert sm is not None  # both parts may be empty
            sl, sep = sm.groups()
            if sep:
                pos = sm.end()
            else:
                sl = b""
                need_close = True
            comment = None
            c = data[pos : pos + 1]
            if c == b" " or c == b"-" or c == b"\t":
                cm = comment_match(data, pos)
                if cm is not None:
                    comment = cm.group(1)
            e_lead, e_style, e_key, e_kc, e_eq, e_dup = closed
            append(
                _new(Entry, (e_lead, e_style, e_key, e_kc, e_eq, table, sl, sep, comment, e_dup))
            )

    def check_string_bound(self, m: re.Match[bytes], *groups: int) -> None:
        for group in groups:
            text = m.group(group)
            if text is not None and len(text) - 2 > MAX_STRING_BYTES:
                raise _error(
                    self.data,
                    m.start(group),
                    text[:20],
                    f"string literal over the {MAX_STRING_BYTES}-byte bound",
                    LuaLimitError,
                )


# ── to_python ───────────────────────────────────────────────────────────────


def _to_python(value: LuaValue) -> object:
    if isinstance(value, LuaTable):
        return _table_to_python(value)
    if isinstance(value, LuaString):
        return value.value
    if isinstance(value, LuaNumber):
        text = value.raw
        if _INT_SPELLING.fullmatch(text) is not None:
            return _spelled_int(text)
        return _spelled_float(text)
    if isinstance(value, LuaBool):
        return value.value
    return None


def _entry_keys(entry: Entry) -> tuple[object, object]:
    """(Lua key, Python key) of a keyed entry."""
    key = entry.key
    if isinstance(key, str):
        return key.encode("ascii"), key
    if isinstance(key, LuaString):
        return key.data, key.value
    if isinstance(key, LuaNumber):
        python_key = _to_python(key)
        return _spelled_float(key.raw), python_key
    if isinstance(key, LuaBool):
        return (_TRUE_KEY if key.value else _FALSE_KEY), key.value
    raise TypeError(f"entry has no key: {entry!r}")


def _is_index(key: object, count: int) -> bool:
    """Whether a Lua key is one of the integers 1..count (keys are unique, so
    `count` of them that all pass are exactly 1..count)."""
    if type(key) is int:
        return 1 <= key <= count
    if type(key) is float:
        return key.is_integer() and 1 <= key <= count
    return False


def _table_to_python(table: LuaTable) -> object:
    order: dict[object, object] = {}  # Lua key -> Python key, first appearance
    stored: dict[object, LuaValue] = {}  # Lua key -> the value Lua 5.1 loads
    pending: list[tuple[int, LuaValue]] = []
    npos = 0
    for entry in table.entries:
        if len(pending) == _LFIELDS_PER_FLUSH:
            stored.update(pending)
            pending.clear()
        if entry.style == KeyStyle.POSITIONAL:
            npos += 1
            if npos not in order:
                order[npos] = npos
            pending.append((npos, entry.value))
        else:
            lua_key, python_key = _entry_keys(entry)
            if lua_key not in order:
                order[lua_key] = python_key
            stored[lua_key] = entry.value
    stored.update(pending)
    if not order:
        return {}
    count = len(order)
    if all(_is_index(k, count) for k in order):
        return [_to_python(stored[i]) for i in range(1, count + 1)]
    return {python_key: _to_python(stored[k]) for k, python_key in order.items()}
