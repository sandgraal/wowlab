"""luadata: SavedVariables parser and serializer (docs/LAB_PLAN.md §6.4,
docs/LAB_FORMATS.md §4; M10-04, M10-12).

Load-bearing. SavedVariables are Lua-syntax *data*: this module reads them
with a constrained literal grammar and never evaluates anything (L3). It
imports no interpreter and no third-party parser, and it opens files
read-only (L1). `serialize` turns a document back into bytes and writes
nothing (writing into an install is `guard`'s, L2); it refuses anything
that is not data, so its output always parses (L3 for writes).

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

A parsed document holds bytes in every slot. An edited or built one may hold
`None` in any trivia slot (`lead`, `eq_lead`, `key_close_lead`, `sep_lead`,
`close_lead`, `tail`) or in `sep`: `serialize` fills it from the detected
style (§6.4 amendment of 2026-09-27; see `serialize`).

Duplicates are kept in source order. `Entry.duplicate` is `True` only on the
later of two entries whose keys are equal under Lua key equality (`a` and
`["a"]`; `[1]`, `[1.0]` and the first positional entry; `[1]` and `["1"]`
differ; so do `[true]` and `[1]`).

Bounds (constructed hostile inputs, L8): a document over `MAX_FILE_BYTES`, a
table nested deeper than `MAX_DEPTH` (a table assigned at top level is depth
1), a string literal whose source between the quotes is longer than
`MAX_STRING_BYTES`, a number literal longer than `MAX_NUMBER_CHARS`, or a
document whose table entries and top-level assignments cost more than
`MAX_COST` (a budget in bytes of what the parse holds; see the budget
comment below) raises `LuaLimitError` (§6.4 amendment of 2026-09-23);
nothing is truncated. The parser
is iterative: a 10 000-deep table raises `LuaLimitError`, never
`RecursionError`.

The value and document types are immutable `NamedTuple`s (a frozen
dataclass costs about eight times as much to build, and a 50 MB file holds
millions of entries); Pydantic models are built at the CLI output boundary
(M10-14). Equality also requires the same type. They are tuples, so
`len(table)` counts fields: count entries with `len(table.entries)`.

Identical immutable nodes are shared: two equal positional entries, scalar
values, string keys, trivia or top-level assignments of one document may be
the same object. So `is` and `id()` do not identify an entry or its place in
the document; walk by position. `serialize` lays nodes out by where they
are placed and never tracks edits by object identity.
"""

from __future__ import annotations

import gc
import math
import os
import re
import stat
import threading
from collections.abc import Iterable, Iterator
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, NoReturn, cast

__all__ = [
    "MAX_COST",
    "MAX_DEPTH",
    "MAX_ENTRIES",
    "MAX_FILE_BYTES",
    "MAX_NUMBER_CHARS",
    "MAX_STRING_BYTES",
    "SIBLING_PREFIX_BYTES",
    "SIBLING_READ_LIMIT",
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
    "serialize",
]

_MIB = 1024 * 1024
#: Deepest table nesting accepted; a table assigned at top level is depth 1.
MAX_DEPTH = 200
#: Largest document accepted, in bytes (§6.4: 256 MB, read as MiB).
MAX_FILE_BYTES = 256 * _MIB
#: Longest string literal accepted, counted on its source bytes between the quotes.
MAX_STRING_BYTES = 64 * _MIB
#: Longest number literal accepted, in characters: CPython's own default
#: limit on int parsing, so no conversion of an accepted literal is quadratic.
MAX_NUMBER_CHARS = 4300
# Distinct items each sharing cache of the parser holds (trivia, values,
# string keys, positional entries, top-level assignments); past it, objects
# are built as usual. Only trivia of at most `_SHARE_TRIVIA_BYTES` is cached.
_SHARE_LIMIT = 1 << 16
_SHARE_TRIVIA_BYTES = 64

# ── the cost budget (§6.4 amendment of 2026-09-23) ──
#
# Every document is charged, in bytes, roughly what the parse holds for it:
# the input buffer itself, then per table entry and per top-level
# assignment a base cost plus every object built for it that is not shared
# (a bytes object costs `_C_BYTES` plus its length; a two-field node
# `_C_NODE`; a number's text `_C_STR` plus its length; a table `_C_TABLE`).
# An entry or assignment that is shared whole (an identical positional
# entry or assignment already built) still costs `_C_SHARED`: it holds
# almost no memory, but it takes parse time, and that charge is what bounds
# the time of a dense list. A trailing comment is charged twice, once as
# `Entry.comment` and once inside the next token's lead, because it is held
# twice. The constants are calibrated against measured peak RSS in the
# M10-04 PR (lab/core/tests/luadata_bench_constructed.py --at-budget).
_C_BYTES = 40
_C_NODE = 56
_C_STR = 56
_C_TABLE = 208  # the LuaTable and its entries tuple, and the time its close takes
_C_ENTRY = 190  # the 10-field Entry and its list and tuple slots
_C_KEYED = 40  # a keyed entry's slot in the table's key set
_C_ASSIGN = 96  # the 4-field Assignment and its slots
_C_SHARED = 170
# Time charges, in the same unit (the budget is also what bounds parse time):
# an entry the fast regex cannot take costs `_C_SLOW` more, since the full
# `_ENTRY` path takes about twice as long; every backslash in a string
# literal (key or value, shared or not) costs `_C_ESCAPE`, which bounds the
# escapes decoded later (`LuaString.data`, `to_python()`); decoding an
# escaped key at parse costs `_C_DECODE`, once per distinct short key text,
# and every escaped-key occurrence `_C_ESCAPED_KEY`.
_C_SLOW = 120
_C_ESCAPE = 100
_C_DECODE = 500  # decoding one escaped key at parse (once per distinct short text)
_C_ESCAPED_KEY = 80  # every escaped-key occurrence: its lookup and key-set entry
#: The budget, in the bytes described above, that one document may cost; a
#: document over it raises `LuaLimitError` at the entry or assignment that
#: crosses it.
MAX_COST = 1_150_000_000
# The most one entry whose source (lead to separator) spans at most 64 bytes
# can be charged: every object it can build unshared, with each byte of the
# span counted twice, every byte of it a backslash, plus the span itself in
# the input buffer.
_SHORT_ENTRY_BYTES = 64
_C_SHORT_ENTRY_MAX = (
    _C_ENTRY
    + _C_KEYED
    + _C_TABLE
    + _C_SLOW
    + _C_DECODE
    + _C_ESCAPED_KEY
    + 3 * _C_NODE
    + 2 * _C_STR
    + 8 * _C_BYTES
    + (3 + _C_ESCAPE) * _SHORT_ENTRY_BYTES
)
#: How many table entries of up to 64 source bytes each (lead to separator)
#: fit the budget, whatever they hold, in a document holding nothing else:
#: `MAX_COST` divided by the most such an entry can be charged. Not itself a
#: bound: the budget refuses, and denser documents (a list of repeated values
#: costs `_C_SHARED` an entry) hold far more.
MAX_ENTRIES = MAX_COST // _C_SHORT_ENTRY_MAX

# Lua 5.1 stores pending positional entries this many at a time
# (`LFIELDS_PER_FLUSH` in lopcodes.h); §6.4 amendment item 6, **[verify]**.
_LFIELDS_PER_FLUSH = 50


# ── errors ──────────────────────────────────────────────────────────────────


class LuaDataError(ValueError):
    """A document the constrained grammar refuses. Never evaluated.

    `line` and `column` are 1-based (the column counts bytes); `offset` is the
    same position as a 0-based byte offset; `token` is the offending source
    bytes, `b""` at the end of input.
    """

    def __init__(
        self, message: str, *, line: int, column: int, token: bytes, offset: int = 0
    ) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.token = token
        self.offset = offset
        super().__init__(f"line {line}, column {column}: {message} (at {token!r})")


class LuaLimitError(LuaDataError):
    """A depth, file-size or string-length bound was exceeded (and nothing else)."""


# ── the document model ──────────────────────────────────────────────────────


def _typed_eq(self: tuple[object, ...], other: object) -> bool:
    """Tuple equality that also requires the same type, so a value never
    equals a plain tuple or another node type with the same fields."""
    return type(other) is type(self) and tuple.__eq__(self, other)


class KeyStyle(StrEnum):
    """How an entry's key is written (§6.4, amendment item 8)."""

    POSITIONAL = "positional"
    STRING = "string"  # ["text"] or ['text']
    NUMBER = "number"  # [42]
    NAME = "name"  # bare identifier, `key = value`
    BOOLEAN = "boolean"  # [true] / [false]


class LuaString(NamedTuple):
    """A string literal. `raw` is its source bytes, quotes included."""

    lead: bytes | None
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

    def __eq__(self, other: object) -> bool:
        return _typed_eq(self, other)

    def __ne__(self, other: object) -> bool:
        return not _typed_eq(self, other)

    def __hash__(self) -> int:
        return tuple.__hash__(self)


class LuaNumber(NamedTuple):
    """A number literal. `raw` is its source text, kept exactly (`100.000`,
    `1E-07`, `0x1F` and `-0` stay as written). Lua 5.1 holds every number as
    a double; the conversions read the text."""

    lead: bytes | None
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

    def __eq__(self, other: object) -> bool:
        return _typed_eq(self, other)

    def __ne__(self, other: object) -> bool:
        return not _typed_eq(self, other)

    def __hash__(self) -> int:
        return tuple.__hash__(self)


class LuaBool(NamedTuple):
    """`true` or `false`."""

    lead: bytes | None
    value: bool

    def __eq__(self, other: object) -> bool:
        return _typed_eq(self, other)

    def __ne__(self, other: object) -> bool:
        return not _typed_eq(self, other)

    def __hash__(self) -> int:
        return tuple.__hash__(self)


class LuaNil(NamedTuple):
    """`nil`; legal only as a top-level value (§4.1)."""

    lead: bytes | None

    def __eq__(self, other: object) -> bool:
        return _typed_eq(self, other)

    def __ne__(self, other: object) -> bool:
        return not _typed_eq(self, other)

    def __hash__(self) -> int:
        return tuple.__hash__(self)


class LuaTable(NamedTuple):
    """A table constructor: `lead "{" entries close_lead "}"`."""

    lead: bytes | None
    entries: tuple[Entry, ...]
    close_lead: bytes | None

    def __eq__(self, other: object) -> bool:
        return _typed_eq(self, other)

    def __ne__(self, other: object) -> bool:
        return not _typed_eq(self, other)

    def __hash__(self) -> int:
        return tuple.__hash__(self)


LuaValue = LuaTable | LuaString | LuaNumber | LuaBool | LuaNil
#: `None` (positional), `LuaString`, `LuaNumber`, the identifier (bare name) or `LuaBool`.
LuaKey = LuaString | LuaNumber | LuaBool | str | None


class Entry(NamedTuple):
    """One table entry, in source order.

    `key` is `None` (positional), a `LuaString`, a `LuaNumber`, the bare
    identifier as a `str`, or a `LuaBool`. `sep` is `b","`, `b";"` or `b""`
    (or `None` in an edit, for `serialize` to fill; so may any trivia slot).
    `comment` is a view of the line comment after the entry on its line,
    from `--` to the line break, or `None`. `duplicate` flags the later of
    two entries with equal Lua keys.
    """

    lead: bytes | None
    style: KeyStyle
    key: LuaKey
    key_close_lead: bytes | None
    eq_lead: bytes | None
    value: LuaValue
    sep_lead: bytes | None
    sep: bytes | None
    comment: bytes | None
    duplicate: bool

    def __eq__(self, other: object) -> bool:
        return _typed_eq(self, other)

    def __ne__(self, other: object) -> bool:
        return not _typed_eq(self, other)

    def __hash__(self) -> int:
        return tuple.__hash__(self)


class Assignment(NamedTuple):
    """A top-level `name = value`."""

    lead: bytes | None
    name: str
    eq_lead: bytes | None
    value: LuaValue

    def __eq__(self, other: object) -> bool:
        return _typed_eq(self, other)

    def __ne__(self, other: object) -> bool:
        return not _typed_eq(self, other)

    def __hash__(self) -> int:
        return tuple.__hash__(self)


class LuaDocument(NamedTuple):
    """A parsed SavedVariables file: its assignments in source order and the
    bytes after the last token."""

    assignments: tuple[Assignment, ...]
    tail: bytes | None

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

    def __eq__(self, other: object) -> bool:
        return _typed_eq(self, other)

    def __ne__(self, other: object) -> bool:
        return not _typed_eq(self, other)

    def __hash__(self) -> int:
        return tuple.__hash__(self)


# ── numbers and strings ─────────────────────────────────────────────────────

_INT_SPELLING = re.compile(r"-?(?:0[xX][0-9A-Fa-f]+|[0-9]+)")
_FLOAT_SPELLING = re.compile(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _spelled_int(text: str) -> int:
    """The integer a decimal or hex spelling denotes, exactly.

    Relies on CPython's process-wide limit on decimal text (4300 digits by
    default): the parser refuses literals over `MAX_NUMBER_CHARS`, so with the
    default limit this never fails and is never quadratic. A host that lowers
    the limit with `sys.set_int_max_str_digits` gets `ValueError` here (from
    `as_int()` and `to_python()`) for an accepted literal of up to 4300 digits.
    """
    negative = text.startswith("-")
    body = text[1:] if negative else text
    # The parser refuses literals over MAX_NUMBER_CHARS, inside CPython's
    # default limit on decimal text, so this is exact and never quadratic.
    number = int(body[2:], 16) if body[:2] in ("0x", "0X") else int(body, 10)
    return -number if negative else number


def _spelled_float(text: str) -> float:
    if _INT_SPELLING.fullmatch(text) is not None:
        if "x" not in text and "X" not in text:
            return float(text)
        number = _spelled_int(text)
        try:
            return float(number) if number or not text.startswith("-") else -0.0
        except OverflowError:
            return math.inf if number > 0 else -math.inf  # never converts `number` again
    if _FLOAT_SPELLING.fullmatch(text) is None:
        raise ValueError(f"{text!r} is not a Lua number spelling this parser accepts")
    return float(text)


# Escapes are decoded at C speed: CPython's `unicode_escape` codec reads
# exactly Lua 5.1's simple escapes (`\a \b \f \n \r \t \v \\ \" \'`) and octal
# `\ooo`, and passes every other byte through as Latin-1, so encoding the
# result as Latin-1 gives the bytes back. Only the two Lua escapes it reads
# differently are rewritten first, in one linear pass into a `bytearray`: a
# backslash before a line break (to `\n`) and decimal `\ddd` (to the same
# byte in octal). Every escape the grammar refuses (`\x`, `\u`,
# `\N`, `\z`, any other) is refused here before the codec sees it.
_ESCAPE_BAD = re.compile(rb"(?<!\\)(?:\\\\)*+\\(?![abfnrtv\\\"'0-9\r\n])")
_ESCAPE_SPECIAL = re.compile(rb"(?<!\\)(?:\\\\)*+\\[0-9\r\n]")
# A decimal escape (group 2) or an escaped line break (group 3), after any
# run of escaped backslashes (group 1).
_ESCAPE_REWRITE = re.compile(rb"(?<!\\)((?:\\\\)*+)\\(?:([0-9]{1,3}+)|(\r\n|\n\r|\r|\n))")
_OCTAL = {b"%d" % code: b"\\%03o" % code for code in range(256)}
_OCTAL.update({b"%02d" % code: b"\\%03o" % code for code in range(100)})
_OCTAL.update({b"%03d" % code: b"\\%03o" % code for code in range(256)})


def _rewrite_escapes(body: bytes) -> bytes:
    """`body` with every decimal escape rewritten in octal and every escaped
    line break as `\\n`, built in one `bytearray` (linear memory, one loop
    step per rewritten escape)."""
    out = bytearray()
    view = memoryview(body)
    last = 0
    for m in _ESCAPE_REWRITE.finditer(body):
        digits = m.group(2)
        if digits is None:
            replacement = b"\\n"
        else:
            octal = _OCTAL.get(digits)
            if octal is None:
                raise ValueError(f"escape \\{digits!r} is above 255")
            replacement = octal
        out += view[last : m.end(1)]  # up to the escape's backslash
        out += replacement
        last = m.end()
    out += view[last:]
    return bytes(out)


def _unescape(body: bytes) -> bytes:
    """The bytes a string literal's body (quotes removed) decodes to under
    Lua 5.1's escapes. Memory is linear in the body; runs of ordinary bytes
    and simple escapes are decoded at C speed, decimal escapes and escaped
    line breaks at one loop step each (the parser charges every backslash against `MAX_COST`).
    Raises `ValueError` for an escape the grammar refuses (only reachable for
    a hand-built `LuaString`: the parser refuses those first)."""
    if _ESCAPE_BAD.search(body) is not None:
        raise ValueError("not a Lua 5.1 escape, or a lone backslash at the end")
    if _ESCAPE_SPECIAL.search(body) is not None:
        body = _rewrite_escapes(body)
    return body.decode("unicode_escape").encode("latin-1")


def _escape_over_255(raw: bytes) -> bool:
    """For a literal whose escapes the grammar has already checked, whether
    one `\\ddd` is above 255."""
    return _first_over_255(raw, 0, len(raw)) >= 0


# A backslash and three digits above 255 (a `\\ddd` escape takes at most
# three digits). It starts with a literal byte, so the regex engine finds
# candidates at C speed; a candidate is an escape only if an even run of
# backslashes precedes it.
_OVER_255 = re.compile(rb"\\(?:25[6-9]|2[6-9][0-9]|[3-9][0-9]{2})")


def _first_over_255(data: bytes, start: int, end: int) -> int:
    """Offset of the backslash of the first decimal escape above 255 in
    `data[start:end]` (a run of the grammar's string body), or -1."""
    search = _OVER_255.search
    pos = start
    while True:
        m = search(data, pos, end)
        if m is None:
            return -1
        at = m.start()
        if _backslashes_before(data, at, start) % 2 == 0:
            return at
        pos = at + 1


def _backslashes_before(data: bytes, at: int, start: int) -> int:
    """How many backslashes immediately precede `at` (not before `start`),
    counted in doubling chunks at C speed, so a long run costs its length
    once."""
    count = 0
    width = 64
    while True:
        low = max(start, at - count - width)
        chunk = data[low : at - count]
        kept = chunk.rstrip(b"\\")
        count += len(chunk) - len(kept)
        if kept or low == start:
            return count
        width *= 2


# ── the grammar as regular expressions ──────────────────────────────────────
#
# Every regex below is complete for what the grammar accepts, so a failed
# match always means the input is refused. The diagnosis (which token, what
# message) is then done by `_Diagnoser`, token by token. All repetition sits
# in atomic groups or is possessive, so a failed match never backtracks
# (a long unterminated string or run of trivia is scanned once).

_WS = rb"[ \t\r\n\f\v]"
_TRIVIA = rb"(?>" + _WS + rb"*(?:--(?!\[=*\[)[^\r\n\x00]*" + _WS + rb"*)*)"
# A string body: runs of ordinary bytes, runs of one-line escapes (simple or
# decimal: a whole run is one regex step, so an escape-dense string is
# scanned about three times faster than escape by escape), and escaped line
# breaks.
_ESCAPES = rb"(?:\\(?:[0-9]{1,3}+|[abfnrtv\\\"']))++|\\(?:\r\n|\n\r|[\r\n])"
_DQ_BODY = rb'(?:[^"\\\r\n\x00]++|' + _ESCAPES + rb")*+"
_SQ_BODY = rb"(?:[^'\\\r\n\x00]++|" + _ESCAPES + rb")*+"
_DQ = rb'(?>"' + _DQ_BODY + rb'")'
_SQ = rb"(?>'" + _SQ_BODY + rb"')"
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
# The common entry shapes, tried before `_ENTRY` because they match in
# about half the time: whitespace, then an optional `["string"] = ` or
# `[number] = ` key (no trivia inside the brackets, at most one space on
# either side of `=`), then a double-quoted string, a number or a boolean
# followed at once by `,` or `;`, or a `{`. Every match is also an `_ENTRY`
# match with the same parts (kl, kc and sep_lead empty; eq_lead and the
# value's lead are the captured spaces); anything else falls through to
# `_ENTRY`. Strings are checked for escapes above 255 after either match.
_FAST = re.compile(
    rb"([ \t\r\n\f\v]*+)(?:\[(?:(" + _DQ + rb")|(" + _NUM + rb"))\]( ?)=(?!=)( ?))?"
    rb"(?:(?:(" + _DQ + rb")|(" + _NUM + rb")|(" + _BOOL + rb"))([,;])|(\{))"
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
_LONG_OPEN = re.compile(rb"\[=*\[")
# A string body the grammar accepts: ordinary bytes and well-formed escapes.
_BODY_DQ = re.compile(_DQ_BODY).match
_BODY_SQ = re.compile(_SQ_BODY).match
_BAD_RUN = re.compile(rb"[^ \t\r\n\f\v,;{}\[\]=]+")


def _position(data: bytes | bytearray, offset: int) -> tuple[int, int]:
    """1-based line and byte column of `offset`; CRLF, LFCR, LF and CR each
    end one line (Lua 5.1's `inclinenumber`).

    Every CR and LF is a line break except the second byte of a pair, and
    Lua pairs a CR with an LF next to it greedily from the left, which is
    exactly the leftmost-first matching of `\\r\\n|\\n\\r`. Counted at C
    speed and in memory bounded by `_POSITION_CHUNK`: directly when the
    prefix has only CRs, only LFs or only CRLFs, and otherwise by
    `_mixed_pairs`."""
    line_start = max(data.rfind(b"\n", 0, offset), data.rfind(b"\r", 0, offset)) + 1
    column = offset - line_start + 1
    lfs = data.count(b"\n", 0, line_start)
    crs = data.count(b"\r", 0, line_start)
    if not lfs or not crs:
        return lfs + crs + 1, column
    if data.count(b"\r\n", 0, line_start) == lfs == crs:  # pure CRLF, the client's
        return lfs + 1, column
    return lfs + crs - _mixed_pairs(data, line_start) + 1, column


# `_mixed_pairs` reads at most this many bytes at a time (module-level so a
# test can make it tiny and put a cut at every offset).
_POSITION_CHUNK = 1 << 20
_CR_MASK = bytes(0xFF if b == 0x0D else 0 for b in range(256))
_LF_MASK = bytes(0xFF if b == 0x0A else 0 for b in range(256))


def _mixed_pairs(data: bytes | bytearray, end: int) -> int:
    """The number of leftmost-first `\\r\\n|\\n\\r` matches in `data[:end]`.

    The pure-CRLF prefix, up to the slice holding the first stray CR or LF,
    is counted with `bytes.count` (`_crlf_prefix`). The rest is read in
    slices of `_POSITION_CHUNK` bytes, each counted from its first byte by
    `_pairs`. A slice may end anywhere, even inside a run of line breaks:
    greedy pairing restarts at every slice start unless the cut splits a
    pair, which happens only when the slice's last byte is left unpaired
    and the next byte is the other line break. Then that pair is counted
    here, and the next slice starts after it."""
    begin, pairs = _crlf_prefix(data, end)
    even = int.from_bytes(b"\x01\x00" * (_POSITION_CHUNK // 2 + 1), "little")
    while begin < end:
        cut = min(begin + _POSITION_CHUNK, end)
        count, last_paired = _pairs(data, begin, cut, even)
        pairs += count
        begin = cut
        if not last_paired and cut < end:
            before, after = data[cut - 1], data[cut]
            if before != after and before in b"\r\n" and after in b"\r\n":
                pairs += 1
                begin = cut + 1
    return pairs


def _crlf_prefix(data: bytes | bytearray, end: int) -> tuple[int, int]:
    """Where the pure-CRLF prefix of `data[:end]` ends, as a slice start, and
    the pairs before it.

    A slice is pure when every CR in it is followed by an LF and every LF
    follows a CR, which three `bytes.count` calls confirm; then its pairs
    are its CRLFs. Every slice here ends just after an LF, so no pair is
    split. The start returned is that of the first slice that is not pure,
    or `end`."""
    begin = pairs = 0
    while begin < end:
        cut = data.find(b"\n", min(begin + _POSITION_CHUNK, end) - 1, end)
        cut = end if cut < 0 else cut + 1
        crlfs = data.count(b"\r\n", begin, cut)
        if not crlfs == data.count(b"\r", begin, cut) == data.count(b"\n", begin, cut):
            break
        pairs += crlfs
        begin = cut
    return begin, pairs


def _pairs(data: bytes | bytearray, begin: int, end: int, even: int) -> tuple[int, bool]:
    """Leftmost-first `\\r\\n|\\n\\r` matches in `data[begin:end]`, counted
    from `begin` at C speed, and whether the last byte is the second byte of
    one. `even` has a 1 in the low bit of every even-numbered byte, at least
    as long as the slice.

    With one pair order only, the pairs are its occurrences. With both, the
    line-break bytes form maximal alternating runs (a run ends at any other
    byte or at two equal breaks side by side), greedy pairing restarts at
    each run's start and takes every second byte from there. That is done
    with integer bit operations on one mask byte per input byte: `d` marks
    each byte that is a CR or LF followed by the other, and adding a run's
    first bit to `d` ripples a carry through the run, which picks out the
    runs that start on an even byte."""
    if data.find(b"\n\r", begin, end) < 0:
        return data.count(b"\r\n", begin, end), data.endswith(b"\r\n", begin, end)
    if data.find(b"\r\n", begin, end) < 0:
        return data.count(b"\n\r", begin, end), data.endswith(b"\n\r", begin, end)
    chunk = data[begin:end]
    cr = int.from_bytes(chunk.translate(_CR_MASK), "little")
    lf = int.from_bytes(chunk.translate(_LF_MASK), "little")
    d = (cr & (lf >> 8)) | (lf & (cr >> 8))  # 0xFF where a pair can start
    starts = d ^ (d & (d << 8))
    even_runs = d ^ (d & (d + (starts & even)))  # the carry clears each even-start run
    odd_runs = d ^ even_runs
    taken = (even_runs & even) | (odd_runs & (even << 8))  # 1 where a pair starts
    return taken.bit_count(), bool(taken >> (8 * (len(chunk) - 2)))


# An error keeps at most this many bytes of its offending token (and says how
# long the token was), so refusing a huge token never copies it into a message.
_TOKEN_KEEP = 40


def _error(
    data: bytes | bytearray,
    offset: int,
    token: bytes,
    message: str,
    cls: type[LuaDataError] = LuaDataError,
    length: int | None = None,
) -> LuaDataError:
    length = len(token) if length is None else length
    if length > _TOKEN_KEEP:
        token = token[:_TOKEN_KEEP]
        message = f"{message} (a {length}-byte token; its first {_TOKEN_KEEP} bytes are kept)"
    line, column = _position(data, offset)
    return cls(message, line=line, column=column, token=token, offset=offset)


# ── diagnosis of a refused input ────────────────────────────────────────────


class _Diagnoser:
    """Walks the input token by token from where a grammar regex failed and
    raises the positioned error. Reached only for refused input."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def fail(self, offset: int, token: bytes, message: str) -> NoReturn:
        raise _error(self.data, offset, token, message)

    def fail_span(self, start: int, end: int, message: str) -> NoReturn:
        """Refuse the token `data[start:end]` without copying more of it than
        the error keeps."""
        raise _error(
            self.data,
            start,
            self.data[start : min(end, start + _TOKEN_KEEP)],
            message,
            length=end - start,
        )

    def word(self, start: int, end: int) -> bytes:
        """A name's bytes for comparing with keywords: a name longer than any
        keyword is cut (it cannot equal one), so a huge name is never copied."""
        return self.data[start : min(end, start + 16)]

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
                self.fail_span(
                    p, run.end() if run else p + 1, "not a number this parser accepts (§4.2, §6.4)"
                )
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
        """End of the string literal at `p`, or the positioned error.

        One regex takes the longest run of the body the grammar accepts
        (ordinary bytes and well-formed escapes) at C speed; the first
        decimal escape above 255 in that run is found with `_first_over_255`; the
        byte where the run stops says what is wrong."""
        data = self.data
        n = len(data)
        body = _BODY_DQ if data[p] == 0x22 else _BODY_SQ
        m = body(data, p + 1)
        i = m.end() if m else p + 1
        at = _first_over_255(data, p + 1, i)
        if at >= 0:
            self.fail(at, data[at : at + 4], "decimal escape above 255")
        if i >= n:
            self.fail_span(p, i, "unterminated string")
        c = data[i]
        if c == data[p]:
            return i + 1
        if c == 0x5C:
            if i + 1 >= n:
                self.fail_span(p, i, "unterminated string")
            if data[i + 1] == 0:  # the NUL is what is refused, as it is anywhere else
                self.fail(i + 1, b"\x00", "a NUL byte is rejected (§4.3)")
            self.fail(i, data[i : i + 2], "not a Lua 5.1 escape")
        if c == 0:
            self.fail(i, b"\x00", "a NUL byte is rejected (§4.3)")
        self.fail_span(p, i, "unterminated string (raw line break)")

    def peek(self, pos: int) -> bytes:
        """The next byte after trivia, without raising (for `value()`'s
        message choice only; `entry()` uses `skip()`/`token()`, since after a
        name that may be a key, a refused trivia is the first offender)."""
        m = _TRIVIA_RE.match(self.data, pos)
        p = m.end() if m else pos
        return self.data[p : p + 1]

    def value(self, pos: int, *, allow_nil: bool) -> tuple[str, int]:
        """Checks one value head at `pos`; returns (kind, end) when it is
        acceptable, raises otherwise."""
        data = self.data
        kind, s, e = self.token(self.skip(pos))
        if kind in ("string", "number", "{"):
            return kind, e
        if kind == "name":
            word = self.word(s, e)
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
            # After `=`, a bare identifier is refused whatever follows it, so it
            # is the offending token: the lookahead only picks the message and
            # must not raise on a later long comment or NUL.
            if self.peek(e) in (b'"', b"'", b"{", b"(", b":"):
                self.fail_span(s, e, "calls are rejected (L3)")
            self.fail_span(s, e, "bare identifiers are not values (L3)")
        if kind == "longbracket":
            opener = _LONG_OPEN.match(data, s)
            token = opener.group() if opener else data[s : s + 1]
            self.fail(s, token, "long-bracket strings are rejected (§6.4)")
        if kind == "eof":
            self.fail(s, b"", "unexpected end of input, expected a value")
        self.fail_span(s, e, "expected a value (operators and expressions are rejected)")

    def internal(self, pos: int) -> NoReturn:
        self.fail(pos, self.data[pos : pos + 20], "input refused (no single offending token)")

    def top(self, pos: int) -> NoReturn:
        kind, s, e = self.token(self.skip(pos))
        if kind != "name":
            what = "end of input" if kind == "eof" else "this"
            self.fail_span(s, e, f"expected `name = value`, found {what}")
        word = self.word(s, e)
        if word in _KEYWORDS:
            self.fail(s, word, "only top-level `name = value` assignments are accepted")
        kind, s2, e2 = self.token(self.skip(e))
        if kind != "=":
            self.fail_span(s2, e2, "expected `=` after the name")
        self.value(e2, allow_nil=True)
        self.internal(pos)

    def entry(self, pos: int, table_at: int) -> NoReturn:
        p = self.skip(pos)
        kind, s, e = self.token(p)
        if kind == "eof":
            self.unterminated(table_at)
        if kind == "[":
            kind, s2, e2 = self.token(self.skip(e))
            word = self.word(s2, e2)
            if kind == "name" and word in (b"true", b"false"):
                pass
            elif kind == "name" and word == b"nil":
                self.fail(s2, word, "nil cannot be a key")
            elif kind in ("string", "number"):
                pass
            else:
                self.fail_span(s2, e2, "a bracketed key is a string, a number, true or false")
            kind, s3, e3 = self.token(self.skip(e2))
            if kind != "]":
                self.fail_span(s3, e3, "expected `]`")
            kind, s4, e4 = self.token(self.skip(e3))
            if kind != "=":
                self.fail_span(s4, e4, "expected `=` after the key")
            vkind, end = self.value(e4, allow_nil=False)
        elif kind == "name" and self.word(s, e) not in _KEYWORDS:
            nxt, _ns, ne = self.token(self.skip(e))  # a refused trivia or byte comes first
            if nxt != "=":
                self.value(p, allow_nil=False)  # a bare identifier: raises
            vkind, end = self.value(ne, allow_nil=False)
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
        self.fail_span(s, e, "expected `,`, `;` or `}` after a table entry")

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
    grammar refuses, and `LuaLimitError` for the depth, size, cost-budget,
    number-length and string-length bounds. Bad input raises nothing else.

    The cyclic garbage collector is paused process-wide while a parse runs
    (the parse builds millions of small tuples and no reference cycles, so
    the collector would only re-scan them, about half the parse time on a
    50 MB file). The caller's collector state is restored when the
    outermost of any concurrent parses finishes.
    """
    data = bytes(data)  # the same object when it is already `bytes`
    if len(data) > MAX_FILE_BYTES:
        raise LuaLimitError(
            f"document is {len(data)} bytes, over the {MAX_FILE_BYTES}-byte bound",
            line=1,
            column=1,
            token=b"",
        )
    _pause_gc()
    try:
        return _Parser(data).run()
    finally:
        _resume_gc()


_gc_lock = threading.Lock()
_gc_pauses = 0
_gc_was_enabled = False


def _pause_gc() -> None:
    global _gc_pauses, _gc_was_enabled
    with _gc_lock:
        if _gc_pauses == 0:
            _gc_was_enabled = gc.isenabled()
            gc.disable()
        _gc_pauses += 1


def _resume_gc() -> None:
    global _gc_pauses
    with _gc_lock:
        _gc_pauses -= 1
        if _gc_pauses == 0 and _gc_was_enabled:
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


def _parse_prefix(data: bytes) -> LuaDocument:
    """Parse a prefix of a document, cut after a line break, for sibling
    style detection only (never returned to a caller). Where the input ends
    inside open tables, they are closed with `close_lead=None` (their real
    end is unknown); anything else the grammar refuses still raises."""
    _pause_gc()
    try:
        return _Parser(data, partial=True).run()
    finally:
        _resume_gc()


def _only_trivia_left(data: bytes, pos: int) -> bool:
    m = _TRIVIA_RE.match(data, pos)
    return m is not None and m.end() == len(data)


class _Parser:
    __slots__ = ("data", "partial")

    def __init__(self, data: bytes, *, partial: bool = False) -> None:
        self.data = data
        self.partial = partial

    def run(self) -> LuaDocument:
        """One flat loop over grammar-sized matches; the open tables live on
        an explicit stack, so nesting never recurses.

        Identical immutable objects are built once and shared (bounded
        caches of `_SHARE_LIMIT` distinct items each): trivia of at most
        `_SHARE_TRIVIA_BYTES`, scalar values with the same lead and text,
        string keys with the same lead and text, whole positional entries
        with the same lead, value and separator (and nothing between the
        value and the separator, and no comment), and whole scalar top-level
        assignments with the same bytes. A dense array of repeated values
        then costs a list slot per entry. Every entry and assignment is
        charged against `MAX_COST` as the module's budget comment says.
        """
        data = self.data
        limit = _SHARE_LIMIT
        trivia_max = _SHARE_TRIVIA_BYTES
        trivia: dict[bytes, bytes] = {}
        tget = trivia.get
        cost = len(data)  # the input buffer is held for the whole parse
        budget = MAX_COST

        def share(text: bytes) -> bytes:
            """The shared copy of a trivia slot, charging an unshared one.
            (Empty and one-byte `bytes` are CPython singletons: free.)"""
            nonlocal cost
            if len(text) < 2:
                return text
            found = tget(text)
            if found is not None:
                return found
            cost += _C_BYTES + len(text)
            if len(text) <= trivia_max and len(trivia) < limit:
                trivia[text] = text
            return text

        assignments: list[Assignment] = []
        # Each open table's saved state: [at, lead, head, entries, seen, npos].
        # `head` is what the table's parent needs when it closes: an
        # assignment's (lead, name, eq_lead) or an entry's (lead, style, key,
        # key_close_lead, eq_lead, duplicate).
        stack: list[list[object]] = []
        entry_match = _ENTRY.match
        fast_match = _FAST.match
        close_match = _CLOSE.match
        sep_match = _SEP.match
        comment_match = _COMMENT_AFTER.match
        values: dict[tuple[bytes, bytes], LuaValue] = {}  # (lead, text) -> scalar
        keys: dict[tuple[bytes, bytes], LuaString] = {}  # (lead, text) -> string key
        positional: dict[tuple[bytes, bytes, bytes], Entry] = {}  # (lead, text, sep)
        decoded: dict[bytes, bytes] = {}  # escaped key text -> its `"` + data + `"`
        whole: dict[bytes, Assignment] = {}  # source bytes -> scalar assignment
        # The innermost open table, unpacked into locals.
        at = 0
        t_lead = b""
        head: tuple[object, ...] = ()
        entries: list[Entry] = []
        append = entries.append
        # Keys of keyed entries (positional keys are 1..npos): a string or name key
        # as `"` + data + `"`, a number key as its double, a boolean as a sentinel.
        seen: set[object] = set()
        npos = 0
        depth = 0
        pos = 0
        need_close = False
        partial = self.partial
        close_lead: bytes | None
        value: LuaValue | None
        key: LuaKey
        lk: object
        while True:
            if not depth:
                start = pos
                m = _ASSIGN.match(data, pos)
                if m is None:
                    _Diagnoser(data).top(pos)
                if m.end() - start > trivia_max and cost + m.end() - start > budget:
                    self.cost_bound(max(m.start(2), start))  # before copying the match
                lead, name, eq, vl, vs, vn, vb, _nil, vt, _end = m.groups(b"")
                if m.end() - pos > MAX_STRING_BYTES:
                    self.check_string_bound(m, 5)
                if not name:
                    return _new(LuaDocument, (tuple(assignments), share(lead)))
                pos = m.end()
                if vt:
                    cost += _C_ASSIGN + _C_STR + len(name) + _C_TABLE
                    lead, eq, vl = share(lead), share(eq), share(vl)
                    if cost > budget:
                        self.cost_bound(start + len(lead))
                    at, t_lead, head = pos - 1, vl, (lead, name.decode("ascii"), eq)
                    entries = []
                    append = entries.append
                    seen = set()
                    npos = 0
                    depth = 1
                    continue
                span = data[start:pos] if pos - start <= trivia_max else b""
                assignment = whole.get(span) if span else None
                if assignment is not None:
                    cost += _C_SHARED
                    if cost > budget:
                        self.cost_bound(start + len(lead))
                    assignments.append(assignment)
                    continue
                cost += _C_ASSIGN + _C_STR + len(name) + _C_NODE
                lead, eq, vl = share(lead), share(eq), share(vl)
                if vs:
                    if b"\\" in vs:
                        if _escape_over_255(vs):
                            _Diagnoser(data).top(start)
                        cost += _C_ESCAPE * vs.count(b"\\")
                    cost += _C_BYTES + len(vs)
                    value = _new(LuaString, (vl, vs))
                elif vn:
                    if len(vn) > MAX_NUMBER_CHARS:
                        self.number_bound(m, 6)
                    cost += _C_STR + len(vn)
                    value = _new(LuaNumber, (vl, vn.decode("ascii")))
                elif vb:
                    value = _new(LuaBool, (vl, vb == b"true"))
                else:
                    value = _new(LuaNil, (vl,))
                if cost > budget:
                    self.cost_bound(start + len(lead))
                assignment = _new(Assignment, (lead, name.decode("ascii"), eq, value))
                if span and len(whole) < limit:
                    whole[span] = assignment
                    cost += _C_BYTES + len(span)
                assignments.append(assignment)
                continue

            if need_close:
                m = close_match(data, pos)
                if m is None:
                    if not (partial and _only_trivia_left(data, pos)):
                        _Diagnoser(data).close(pos, at)
                    close_lead = None  # a prefix ends inside this table
                    pos = len(data)
                else:
                    close_lead = share(m.group(1))
                    pos = m.end()
                need_close = False
            else:
                start = pos
                # `_FAST` and `_ENTRY` number their groups differently: the
                # unpacking just below, and the group numbers passed to
                # `check_string_bound` and `number_bound` further down, are
                # `_ENTRY`'s. A `_FAST` match never reaches those bound checks
                # (it is at most MAX_NUMBER_CHARS long, far under the string
                # bound), so the numbers must stay in step with `_ENTRY` only.
                m = fast_match(data, pos)
                if m is not None and m.end() - pos > MAX_NUMBER_CHARS:
                    m = None  # a long match takes the full path, which checks every bound
                if m is not None:
                    lead, ks, kn, ke, vl, vs, vn, vb, sep, vt = m.groups(b"")
                    close = kl = kb = kc = kw = sl = b""
                    if len(lead) > 1:
                        found = tget(lead)
                        if found is None:
                            cost += _C_BYTES + len(lead)
                            if len(lead) <= trivia_max and len(trivia) < limit:
                                trivia[lead] = lead
                        else:
                            lead = found
                else:
                    m = entry_match(data, pos)
                    if m is None:
                        if partial and _only_trivia_left(data, pos):
                            need_close = True  # a prefix ends inside this table
                            continue
                        _Diagnoser(data).entry(pos, at)
                    if m.end() - pos > trivia_max and cost + m.end() - pos > budget:
                        self.cost_bound(pos)  # before copying the match
                    (lead, close, kl, ks, kn, kb, kc, kw, ke, vl, vs, vn, vb, sl, sep, vt) = (
                        m.groups(b"")
                    )
                    if close:
                        close_lead = share(lead)
                    else:
                        cost += _C_SLOW
                        lead, kl, kc, ke, vl, sl = (
                            share(lead),
                            share(kl),
                            share(kc),
                            share(ke),
                            share(vl),
                            share(sl),
                        )
                if not close:
                    if m.end() - pos > MAX_STRING_BYTES:
                        self.check_string_bound(m, 4, 11)
                    if ks:
                        style = _S
                        shared_key = keys.get((kl, ks))
                        if shared_key is None:
                            cost += _C_NODE + _C_BYTES + len(ks)
                            shared_key = _new(LuaString, (kl, ks))
                            if len(keys) < limit:
                                keys[kl, ks] = shared_key
                                cost += _C_NODE
                        key = shared_key
                        # String and name keys are compared as `"` + data + `"`
                        # (injective, so Lua key equality): for a plain
                        # double-quoted literal that is its own raw bytes,
                        # already held by the key, so nothing new is kept.
                        if b"\\" in ks:
                            # Every occurrence pays for its backslashes (later
                            # decoding, `to_python()`); decoding at parse
                            # happens once per distinct short key text.
                            cost += _C_ESCAPED_KEY + _C_ESCAPE * ks.count(b"\\")
                            known = decoded.get(ks)
                            if known is None:
                                if _escape_over_255(ks):
                                    _Diagnoser(data).entry(pos, at)
                                cost += _C_DECODE + _C_BYTES + len(ks)
                                if cost > budget:
                                    self.cost_bound(start + len(lead))  # before decoding it
                                known = b'"' + _unescape(ks[1:-1]) + b'"'
                                if len(ks) <= trivia_max and len(decoded) < limit:
                                    decoded[ks] = known
                            lk = known
                        elif ks[0] == 0x22:
                            lk = shared_key.raw
                        else:
                            lk = b'"' + ks[1:-1] + b'"'
                            cost += _C_BYTES + len(ks)
                        cost += _C_KEYED
                        dup = lk in seen
                        seen.add(lk)
                    elif kn:
                        if len(kn) > MAX_NUMBER_CHARS:
                            self.number_bound(m, 5)
                        style = _N
                        text = kn.decode("ascii")
                        try:
                            number = float(kn)  # every decimal spelling
                        except ValueError:
                            number = _spelled_float(text)  # hex
                        cost += _C_NODE + _C_STR + len(kn) + 24 + _C_KEYED  # and the double
                        key = _new(LuaNumber, (kl, text))
                        dup = number in seen or (number.is_integer() and 1 <= number <= npos)
                        seen.add(number)
                    elif kw:
                        style = _W
                        key = kw.decode("ascii")
                        lk = b'"' + kw + b'"'
                        cost += _C_STR + _C_BYTES + 2 * len(kw) + _C_KEYED
                        dup = lk in seen
                        seen.add(lk)
                    elif kb:
                        style = _B
                        truth = kb == b"true"
                        key = _new(LuaBool, (kl, truth))
                        cost += _C_NODE + _C_KEYED
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
                        cost += _C_ENTRY + _C_TABLE
                        if cost > budget:
                            self.cost_bound(start + len(lead))
                        stack.append([at, t_lead, head, entries, seen, npos])
                        pos = m.end()
                        at, t_lead, head = pos - 1, vl, (lead, style, key, kc, ke, dup)
                        entries = []
                        append = entries.append
                        seen = set()
                        npos = 0
                        depth += 1
                        continue
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
                            cost += _C_BYTES + len(comment)
                    text_bytes = vs or vn or vb
                    if vs and b"\\" in vs:
                        cost += _C_ESCAPE * vs.count(b"\\")
                    shareable = style is _P and sep and not sl and comment is None and not dup
                    if shareable:
                        shared = positional.get((lead, text_bytes, sep))
                        if shared is not None:
                            cost += _C_SHARED
                            if cost > budget:
                                self.cost_bound(start + len(lead))
                            append(shared)
                            continue
                    cost += _C_ENTRY
                    if vs:
                        if b"\\" in vs and _escape_over_255(vs):
                            _Diagnoser(data).entry(start, at)
                        cost += _C_NODE + _C_BYTES + len(vs)
                        value = _new(LuaString, (vl, vs))
                    else:
                        value = values.get((vl, text_bytes))
                        if value is None:
                            if vn:
                                if len(vn) > MAX_NUMBER_CHARS:
                                    self.number_bound(m, 12)
                                cost += _C_NODE + _C_STR + len(vn)
                                value = _new(LuaNumber, (vl, vn.decode("ascii")))
                            else:
                                cost += _C_NODE
                                value = _new(LuaBool, (vl, vb == b"true"))
                            if len(values) < limit:
                                values[vl, text_bytes] = value
                                cost += _C_NODE + _C_BYTES + len(text_bytes)
                    if cost > budget:
                        self.cost_bound(start + len(lead))
                    built = _new(Entry, (lead, style, key, kc, ke, value, sl, sep, comment, dup))
                    if shareable and len(positional) < limit:
                        positional[lead, text_bytes, sep] = built
                        cost += _C_NODE + _C_BYTES + len(text_bytes)
                    append(built)
                    continue
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
                sl = share(sl)
            else:
                sl = b""
                need_close = True
            comment = None
            c = data[pos : pos + 1]
            if c == b" " or c == b"-" or c == b"\t":
                cm = comment_match(data, pos)
                if cm is not None:
                    comment = cm.group(1)
                    cost += _C_BYTES + len(comment)
            if cost > budget:
                self.cost_bound(pos)
            e_lead, e_style, e_key, e_kc, e_eq, e_dup = closed
            append(
                _new(Entry, (e_lead, e_style, e_key, e_kc, e_eq, table, sl, sep, comment, e_dup))
            )

    def cost_bound(self, at: int) -> NoReturn:
        raise _error(
            self.data,
            at,
            self.data[at : at + 40],
            f"document over the parse budget of {MAX_COST} (MAX_COST)",
            LuaLimitError,
        )

    def number_bound(self, m: re.Match[bytes], group: int) -> NoReturn:
        text = m.group(group)
        raise _error(
            self.data,
            m.start(group),
            text[:20],
            f"number literal of {len(text)} characters, over the {MAX_NUMBER_CHARS} bound",
            LuaLimitError,
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


# ── the serializer (M10-12) ─────────────────────────────────────────────────
#
# `serialize` writes every given slot's bytes as they are, so an unmodified
# document is its own source byte for byte (§6.4, amendment 2026-09-22 item
# 7), and fills every slot holding `None` from the detected style (§6.4,
# amendment 2026-09-27): the document's own, else its sibling SavedVariables'
# (newest first), else the Forever fallback. Everything it writes is checked
# as data first (items 2, 8, 9), so its output always parses (L3 for writes).

# A given trivia slot: whitespace and `--` line comments, never a long
# comment and never a NUL. In every slot but the document's tail a comment
# ends with a line break inside the slot, so it cannot swallow what follows.
_SLOT_OK = re.compile(rb"(?:[ \t\r\n\f\v]++|--(?!\[=*\[)[^\r\n\x00]*+[\r\n])*+").fullmatch
_TAIL_OK = re.compile(rb"(?:[ \t\r\n\f\v]++|--(?!\[=*\[)[^\r\n\x00]*+(?:[\r\n]|\Z))*+").fullmatch
# `Entry.comment`: one line comment, no line break in it.
_COMMENT_OK = re.compile(rb"--(?!\[=*\[)[^\r\n\x00]*+").fullmatch
_STR_OK = re.compile(_STR).fullmatch
_NUM_OK = re.compile(_NUM).fullmatch
_NAME_OK = re.compile(_NAME).fullmatch
_SEPARATORS = (b",", b";", b"")
_NODES = (LuaTable, LuaString, LuaNumber, LuaBool, LuaNil)
_NODE_TYPES = frozenset(_NODES)

# Style detection reads a key or `=` spacing only when it is spaces and tabs,
# and an indentation only when it is a whole number of one unit per level.
# A spacing, an indentation unit or an inline empty-table form longer than
# `_STYLE_TEXT_MAX` bytes counts as not shown, so a hostile document or
# sibling cannot make every generated line huge.
_STYLE_TEXT_MAX = 16
_SPACING_OK = re.compile(rb"[ \t]*+").fullmatch
_INDENT_OK = re.compile(rb"[ \t]++").fullmatch
_LINE_BREAK = re.compile(rb"\r\n|\n\r|\r|\n")
_ARRAY_COMMENT = re.compile(rb"--[ \t]*+\[[0-9]++\][ \t]*+").fullmatch

#: The most of each sibling file that style detection reads, in bytes. A
#: longer sibling is read up to its last line break within the bound and
#: parsed as a prefix (tables still open there are left unclosed); a prefix
#: the grammar refuses skips the sibling like any unparsable one.
SIBLING_PREFIX_BYTES = 1 << 16
#: At most this many siblings, the newest, are read for style.
SIBLING_READ_LIMIT = 64
# At most this many directory entries are examined while listing siblings.
_SIBLING_SCAN_LIMIT = 1 << 14

# With nothing to read (item 6): the layout every captured file shows. A
# `[number]` key is written `[n] = ` (item 8).
_FALLBACK: dict[object, object] = {
    "eol": b"\r\n",
    "blank": True,
    "sep": b",",
    "empty": None,  # `{`, a line break, the closing indentation, `}`
    "pairing": (b"", False),  # (indentation unit, `-- [n]` array comments)
}
_FALLBACK_SPACING = {"klead": b"", "kclose": b"", "eq": b" ", "val": b" "}
_ASSIGN_FORM = "assign"
# Every property but the pairing a document can show: the five above but the
# pairing, `=` and value spacing of an assignment, key lead, key close, `=`
# and value spacing of each bracketed key style, and `=` and value spacing
# of a name key.
_PROPERTY_COUNT = 4 + 2 + 3 * 4 + 2
# The spacing properties of each keyed style: key lead, key close, `=` and
# value spacing (a name key has no brackets, so only the last two apply).
_FORM_KEYS = {
    kind: ((kind, "klead"), (kind, "kclose"), (kind, "eq"), (kind, "val"))
    for kind in (KeyStyle.STRING, KeyStyle.NUMBER, KeyStyle.BOOLEAN, KeyStyle.NAME)
}


def serialize(
    document: LuaDocument,
    *,
    target: str | os.PathLike[str] | None = None,
    lab_written: Iterable[str | os.PathLike[str]] = frozenset(),
) -> bytes:
    """The bytes of the SavedVariables file for `document`. Writes nothing,
    anywhere (L1; writing is `guard`'s, L2).

    Every given slot (a `bytes` trivia slot or `sep`, a `raw`, a key, a
    name) is written exactly as it is, so `serialize(parse(x)) == x` and an
    edited document keeps the bytes of every node it did not change. A slot
    holding `None` is filled from the detected style (§6.4, amendment
    2026-09-27), property by property: what the document itself shows, else
    what its sibling SavedVariables show, else the layout every captured
    file shows (Forever beta 1.60.1, macOS, 105 of 105 files; the fallback
    on every flavor, L6: no indentation, no `-- [n]`, CRLF, a leading empty
    line, `,` after every entry, an empty table as `{` and `}` on two lines,
    `[n] = `). Indentation and `-- [n]` array comments are decided together:
    whoever shows either decides both (tab indentation if it shows only
    comments; comments if it shows an indentation, none if it shows column
    0). A spacing, indentation unit or inline empty-table form longer than
    16 bytes counts as not shown.

    `target` is the path the bytes are meant for (it need not exist); it is
    only a place to look from. The flavor folder is the one above the
    nearest `WTF/Account` in `target`, matched with case folded as `layout`
    does (macOS and Windows installs are case-insensitive), found from the
    absolute path without following links. The siblings are the
    client-written `WTF/Account/*/SavedVariables.lua`,
    `WTF/Account/*/SavedVariables/*.lua` and
    `WTF/Account/*/*/*/SavedVariables/*.lua` of that folder, names matched
    with case folded (links are not followed); never the target, a
    `*.lua.bak`, a file in another flavor folder or outside `WTF/Account`,
    and never a file in `lab_written` (the files the guard journal records
    as last written by the Lab). A file is the target or in `lab_written`
    when it is the same file (`st_dev`, `st_ino`), whatever its spelling; a
    path that does not exist is compared after `Path.resolve()`. For each
    property the most recently modified sibling that shows it decides, ties
    going to the lowest byte-wise path relative to the flavor folder.
    Siblings are only read, at most `SIBLING_PREFIX_BYTES` of each and at
    most the `SIBLING_READ_LIMIT` (64) newest, and only when a slot needs
    them; one that cannot be read or parsed is skipped, so a flavor folder
    with no readable sibling falls back silently. Listing stops after 16384
    directory entries.

    Positions: a new entry goes on its own line (the line ending plus one
    indentation unit per level); after a new positional entry that ends its
    line, `-- [n]` when the layout has array comments, `n` counting the
    positional entries up to it. To append to a parsed table, set its
    `close_lead` to `None`: the old last entry's `Entry.comment`, whose
    bytes were in that `close_lead`, is written after its separator with one
    space, and own-line comments that stood before the old `}` are dropped
    with that `close_lead`. To insert before a parsed entry, or to delete the
    entry before it, set that following entry's `lead` to `None` as well:
    its `lead` holds the previous entry's trailing comment, which is then
    written after the previous entry instead. An `Entry.comment` is written
    only when the slot after its entry is generated; a given slot already
    holds those bytes. The `-- [n]` of untouched entries is not renumbered;
    the client rewrites it at its next write. Nodes are laid out by where
    they are placed, never by object identity.

    Number text is written as given; the client loads it as a Lua 5.1
    double, so an integer beyond 2^53 or a text with more than 17
    significant digits loads as a different value. The client has been seen
    writing only decimal integers and floats with at most 16 significant
    digits (LAB_FORMATS §4 amendments).

    Data only (L3 for writes): `LuaDataError` (`LuaLimitError` for a bound)
    when a given trivia slot holds anything but whitespace and `--` line
    comments (a comment outside the document's `tail` must end with a line
    break inside its slot), `sep` is not `,`, `;` or empty (or is empty
    before another entry), `Entry.comment` is not one line comment, a
    `raw`, key or name is not a literal the parser accepts, a `nil` is
    inside a table, an empty lead would join a name to the value before it,
    a node or slot is not exactly of the model's types (no subclasses of
    `bytes` or `str`), tables nest deeper than `MAX_DEPTH`, or the output
    grows over `MAX_FILE_BYTES` or certainly over the `MAX_COST` parse
    budget (both checked as it is built). `line` and `column` give where the
    refused bytes would start in the output built so far (a refused
    `Entry.comment` at its `--`, a refused `nil` at `nil`), and `token`
    holds at most the first 40 bytes of the refused slot. Finally the output
    is parsed; anything `parse` refuses (the `MAX_COST` budget among it) is
    refused with the parser's error, positioned in the output.
    """
    return _Writer(_Style(document, target, lab_written)).document(document)


def _token(value: object) -> bytes:
    """The bytes an error shows for a node or slot of the wrong type."""
    return repr(value).encode("ascii", "backslashreplace")[:_TOKEN_KEEP]


class _Writer:
    """Lays out one document into `buf`, checking every slot it writes."""

    __slots__ = ("budget", "buf", "entries", "known", "limit", "numbers", "strings", "style")

    def __init__(self, style: _Style) -> None:
        self.buf = bytearray()
        self.style = style
        # The output bound and the parse budget, read when the call starts.
        # Every table entry costs the parser at least `_C_SHARED` on top of
        # the input buffer, so `len(buf) + entries * _C_SHARED` over
        # `MAX_COST` means `parse` would refuse the output: refuse it here,
        # before building the rest.
        self.limit = MAX_FILE_BYTES
        self.budget = MAX_COST
        self.entries = 0
        # Short given trivia slots, string literals and number texts already
        # checked (the parser shares them, so a big document checks each
        # distinct one once), each bounded to `_SHARE_LIMIT` items.
        self.known: set[bytes] = {b""}
        self.strings: set[bytes] = set()
        self.numbers: dict[str, bytes] = {}

    def refuse(
        self, token: bytes, message: str, cls: type[LuaDataError] = LuaDataError
    ) -> NoReturn:
        buf = self.buf  # positioned in place: the output is never copied to refuse it
        raise _error(buf, len(buf), token, message, cls)

    def check_size(self) -> None:
        """Refuse as soon as the output built so far is over `MAX_FILE_BYTES`,
        or certain to be over the parse budget."""
        size = len(self.buf)
        if size > self.limit:
            self.refuse(b"", f"output over the {self.limit}-byte bound", LuaLimitError)
        if size + self.entries * _C_SHARED > self.budget:
            self.refuse(
                b"", f"output over the parse budget of {self.budget} (MAX_COST)", LuaLimitError
            )

    def trivia(self, slot: object, what: str) -> None:
        if type(slot) is not bytes:  # exactly bytes: a subclass could fake the membership test
            self.refuse(_token(slot), f"{what} is {type(slot).__name__}, not bytes or None")
        if slot not in self.known:
            if _SLOT_OK(slot) is None:
                self.refuse(
                    slot,
                    f"{what} holds something other than whitespace and `--` line "
                    "comments that end in a line break (L3)",
                )
            if len(slot) <= _SHARE_TRIVIA_BYTES and len(self.known) < _SHARE_LIMIT:
                self.known.add(slot)
        self.buf += slot

    def name(self, name: object, what: str) -> bytes:
        if type(name) is str:
            text = name.encode("utf-8", "backslashreplace")
            if name.isascii() and _NAME_OK(text) is not None:
                return text
        else:
            text = _token(name)
        self.refuse(text, f"{what} is not a Lua name (L3)")

    def scalar(self, value: object, *, allow_nil: bool) -> None:
        buf = self.buf
        if isinstance(value, LuaString):
            raw = value.raw
            if type(raw) is not bytes:
                self.refuse(_token(raw), "a LuaString's raw is bytes")
            if len(raw) - 2 > MAX_STRING_BYTES:
                self.refuse(
                    raw, f"string literal over the {MAX_STRING_BYTES}-byte bound", LuaLimitError
                )
            if _STR_OK(raw) is None:
                self.refuse(raw, "not a string literal the grammar accepts (L3)")
            if b"\\" in raw and _escape_over_255(raw):
                self.refuse(raw, "decimal escape above 255")
            if len(raw) <= _SHARE_TRIVIA_BYTES and len(self.strings) < _SHARE_LIMIT:
                self.strings.add(raw)
            buf += raw
        elif isinstance(value, LuaNumber):
            number = value.raw
            if type(number) is not str:
                self.refuse(_token(number), "a LuaNumber's raw is str")
            text = number.encode("utf-8", "backslashreplace")
            if len(text) > MAX_NUMBER_CHARS:
                self.refuse(
                    text,
                    f"number literal of {len(text)} characters, over the {MAX_NUMBER_CHARS} bound",
                    LuaLimitError,
                )
            if _NUM_OK(text) is None:
                self.refuse(text, "not a number literal the grammar accepts (L3)")
            if len(text) <= _SHARE_TRIVIA_BYTES and len(self.numbers) < _SHARE_LIMIT:
                self.numbers[number] = text
            buf += text
        elif isinstance(value, LuaBool):
            truth = value.value
            if truth is True:
                buf += b"true"
            elif truth is False:
                buf += b"false"
            else:
                self.refuse(_token(truth), "a LuaBool's value is a bool")
        elif isinstance(value, LuaNil):
            if not allow_nil:
                self.refuse(b"nil", "nil is legal only as a top-level value (§4.1)")
            buf += b"nil"
        else:
            self.refuse(_token(value), "not a LuaTable, LuaString, LuaNumber, LuaBool or LuaNil")

    def document(self, document: object) -> bytes:
        if not isinstance(document, LuaDocument):
            self.refuse(_token(document), "not a LuaDocument")
        assignments = document.assignments
        if not isinstance(assignments, tuple | list):
            self.refuse(_token(assignments), "a LuaDocument's assignments are a tuple")
        buf = self.buf
        style = self.style
        for index, assignment in enumerate(assignments):
            if not isinstance(assignment, Assignment):
                self.refuse(_token(assignment), "not an Assignment")
            lead = assignment.lead
            if lead is None:
                buf += style.eol() if index or style.blank() else b""
            else:
                self.trivia(lead, "an assignment's lead")
            name = self.name(assignment.name, "an assignment's name")
            if index and lead is not None and not lead and buf[-1:].isalnum():
                self.refuse(name, "an empty lead would join this name to the value before it")
            buf += name
            eq = assignment.eq_lead
            if eq is None:
                buf += style.spacing((_ASSIGN_FORM, "eq"))
            else:
                self.trivia(eq, "an assignment's eq_lead")
            buf += b"="
            value = assignment.value
            if not isinstance(value, _NODES):
                self.refuse(
                    _token(value), "not a LuaTable, LuaString, LuaNumber, LuaBool or LuaNil"
                )
            if value.lead is None:
                buf += style.spacing((_ASSIGN_FORM, "val"))
            else:
                self.trivia(value.lead, "a value's lead")
            if isinstance(value, LuaTable):
                self.table(value, 1)
            else:
                self.scalar(value, allow_nil=True)
            self.check_size()
        tail = document.tail
        if tail is None:
            buf += style.eol()
        elif type(tail) is not bytes or _TAIL_OK(tail) is None:
            self.refuse(
                tail if type(tail) is bytes else _token(tail),
                "the document's tail holds something other than whitespace and `--` line "
                "comments (L3)",
            )
        else:
            buf += tail
        self.check_size()
        out = bytes(buf)
        buf.clear()
        # Item 2: the output is parsed, so what `parse` refuses (the `MAX_COST`
        # budget among it, one cost model) is refused here too.
        try:
            parse(out)
        except LuaDataError as exc:
            raise type(exc)(
                f"the serialized output does not parse: {exc.message}",
                line=exc.line,
                column=exc.column,
                token=exc.token,
                offset=exc.offset,
            ) from exc
        return out

    def table(self, table: LuaTable, depth: int) -> None:
        """`{`, the entries, the closing bytes and `}` of a table at `depth`
        (a table assigned at top level is depth 1). Recursion is bounded by
        `MAX_DEPTH`. Slots and scalars already checked in this document are
        written on a fast path; everything else goes through the checks."""
        buf = self.buf
        style = self.style
        trivia = self.trivia
        known = self.known
        numbers = self.numbers
        strings = self.strings
        if depth > MAX_DEPTH:
            self.refuse(b"{", f"tables nested deeper than {MAX_DEPTH}", LuaLimitError)
        buf += b"{"
        entries = table.entries
        if not isinstance(entries, tuple | list):
            self.refuse(_token(entries), "a LuaTable's entries are a tuple of Entry")
        close_lead = table.close_lead
        last = len(entries) - 1
        npos = 0
        for index, entry in enumerate(entries):
            if type(entry) is not Entry and not isinstance(entry, Entry):
                self.refuse(_token(entry), "not an Entry")
            lead = entry.lead
            if lead is None:
                buf += style.line(depth)
            elif type(lead) is bytes and lead in known:
                buf += lead
            else:
                trivia(lead, "an entry's lead")
            kind = entry.style
            if kind.__class__ is not KeyStyle:
                try:
                    kind = KeyStyle(kind)
                except (TypeError, ValueError):
                    self.refuse(_token(kind), "not a KeyStyle")
            key = entry.key
            if kind is _P:
                if key is not None:
                    self.refuse(_token(key), "a positional entry has no key")
                npos += 1
            else:
                if kind is _W:
                    buf += self.name(key, "a name key")
                else:
                    buf += b"["
                    wanted = LuaString if kind is _S else LuaNumber if kind is _N else LuaBool
                    if not isinstance(key, wanted):
                        self.refuse(_token(key), f"a {kind} key is a {wanted.__name__}")
                    if key.lead is None:
                        buf += style.spacing((kind, "klead"))
                    else:
                        trivia(key.lead, "a key's lead")
                    if type(key) is LuaString and type(key.raw) is bytes and key.raw in strings:
                        buf += key.raw
                    else:
                        self.scalar(key, allow_nil=False)
                    close = entry.key_close_lead
                    if close is None:
                        buf += style.spacing((kind, "kclose"))
                    elif type(close) is bytes and close in known:
                        buf += close
                    else:
                        trivia(close, "an entry's key_close_lead")
                    buf += b"]"
                eq = entry.eq_lead
                if eq is None:
                    buf += style.spacing((kind, "eq"))
                elif type(eq) is bytes and eq in known:
                    buf += eq
                else:
                    trivia(eq, "an entry's eq_lead")
                buf += b"="
            value = entry.value
            if type(value) not in _NODE_TYPES and not isinstance(value, _NODES):
                self.refuse(
                    _token(value), "not a LuaTable, LuaString, LuaNumber, LuaBool or LuaNil"
                )
            value_lead = value.lead
            if value_lead is None:
                if kind is not _P:  # a positional entry's leading bytes are the entry's
                    buf += style.spacing((kind, "val"))
            elif type(value_lead) is bytes and value_lead in known:
                buf += value_lead
            else:
                trivia(value_lead, "a value's lead")
            if type(value) is LuaTable:
                self.table(value, depth + 1)
            elif type(value) is LuaNumber and type(value.raw) is str and value.raw in numbers:
                buf += numbers[value.raw]
            elif type(value) is LuaString and type(value.raw) is bytes and value.raw in strings:
                buf += value.raw
            elif type(value) is LuaBool and value.value is True:
                buf += b"true"
            elif type(value) is LuaBool and value.value is False:
                buf += b"false"
            elif isinstance(value, LuaTable):
                self.table(value, depth + 1)
            else:
                self.scalar(value, allow_nil=False)
            sep_lead = entry.sep_lead
            if sep_lead is not None:
                if type(sep_lead) is bytes and sep_lead in known:
                    buf += sep_lead
                else:
                    trivia(sep_lead, "an entry's sep_lead")
            sep = entry.sep
            if sep is None:
                sep = style.sep()
            elif type(sep) is not bytes or sep not in _SEPARATORS:
                self.refuse(
                    sep if type(sep) is bytes and sep else _token(sep),
                    "a separator is `,`, `;` or empty",
                )
            elif not sep and index < last:
                self.refuse(b"", "an entry followed by another needs a separator")
            buf += sep
            self.entries += 1
            if len(buf) > self.limit or len(buf) + self.entries * _C_SHARED > self.budget:
                self.check_size()
            comment = entry.comment
            if comment is None and (lead is not None or kind is not _P):
                continue
            # What comes after this entry: a comment is written only when that
            # slot is generated too (a given slot already holds its bytes).
            if index < last:
                following = entries[index + 1]
                after = following.lead if isinstance(following, Entry) else b""
            else:
                after = close_lead
            if comment is not None:
                buf += b" "  # where the comment goes, so a refusal points at its `--`
                if type(comment) is not bytes or _COMMENT_OK(comment) is None:
                    self.refuse(
                        comment if type(comment) is bytes else _token(comment),
                        "an Entry.comment is one `--` line comment with no line break",
                    )
                if after is None:
                    buf += comment
                else:
                    del buf[-1:]
            elif after is None and style.comments():  # a new positional entry
                buf += b" -- [%d]" % npos
        if close_lead is None:
            if entries:
                buf += style.line(depth - 1)
            else:
                form = style.empty()
                buf += style.line(depth - 1) if form is None else form
        else:
            trivia(close_lead, "a table's close_lead")
        buf += b"}"


# ── style detection ─────────────────────────────────────────────────────────


class _Style:
    """The style a `None` slot is filled from, property by property, each
    resolved on first use: the document, then its siblings newest first,
    then `_FALLBACK`."""

    __slots__ = ("_cache", "_lab_written", "_lines", "_own", "_paths", "_siblings", "_target")

    def __init__(
        self,
        document: object,
        target: str | os.PathLike[str] | None,
        lab_written: Iterable[str | os.PathLike[str]],
    ) -> None:
        self._own = _Shown(document)
        self._target = target
        self._lab_written = lab_written
        self._paths: list[Path] | None = None
        self._siblings: list[_Shown] = []
        self._cache: dict[object, object] = {}
        self._lines: dict[int, bytes] = {}

    def get(self, key: object) -> object:
        try:
            return self._cache[key]
        except KeyError:
            pass
        found, value = self._own.get(key)
        if not found:
            for shown in self._each_sibling():
                found, value = shown.get(key)
                if found:
                    break
            else:
                value = _FALLBACK_SPACING[key[1]] if isinstance(key, tuple) else _FALLBACK[key]
        self._cache[key] = value
        return value

    def _each_sibling(self) -> Iterator[_Shown]:
        """The parsed siblings in decision order, read on demand."""
        if self._paths is None:
            target = self._target
            self._paths = [] if target is None else _sibling_paths(target, self._lab_written)
            self._paths.reverse()  # popped from the end, newest first
        index = 0
        while True:
            if index < len(self._siblings):
                yield self._siblings[index]
                index += 1
            elif self._paths:
                document = _read_sibling(self._paths.pop())
                if document is not None:
                    self._siblings.append(_Shown(document))
            else:
                return

    def eol(self) -> bytes:
        return cast(bytes, self.get("eol"))

    def blank(self) -> bool:
        return cast(bool, self.get("blank"))

    def sep(self) -> bytes:
        return cast(bytes, self.get("sep"))

    def empty(self) -> bytes | None:
        return cast(bytes | None, self.get("empty"))

    def comments(self) -> bool:
        return cast(tuple[bytes, bool], self.get("pairing"))[1]

    def spacing(self, key: tuple[str, str]) -> bytes:
        return cast(bytes, self.get(key))

    def line(self, depth: int) -> bytes:
        """A line break, then the indentation of `depth` levels."""
        text = self._lines.get(depth)
        if text is None:
            unit = cast(tuple[bytes, bool], self.get("pairing"))[0]
            text = self._lines[depth] = self.eol() + unit * depth
        return text


class _Shown:
    """The style properties one document shows, each taken from its first
    showing in document order; the document is walked only as far as the
    properties asked for need."""

    __slots__ = ("comments", "found", "indent", "steps")

    def __init__(self, document: object) -> None:
        self.found: dict[object, object] = {}
        self.indent: bytes | None = None
        self.comments: bool | None = None
        self.steps: Iterator[None] | None = _walk(document, self)

    def get(self, key: object) -> tuple[bool, object]:
        """(True, value) when the document shows the property `key`."""
        found = self.found
        while True:
            if key == "pairing":
                if self.indent is not None and self.comments is not None:
                    break
            elif key in found:
                return True, found[key]
            if self.steps is None:
                break
            try:
                next(self.steps)
            except StopIteration:
                self.steps = None
        if key != "pairing":
            return False, None
        indent, comments = self.indent, self.comments
        if indent is None and comments is None:
            return False, None
        if indent is None:
            indent = b"\t" if comments else b""  # §4.2's form goes with its comments
        if comments is None:
            comments = indent != b""
        return True, (indent, comments)


def _walk(document: object, shown: _Shown) -> Iterator[None]:
    """Record in `shown` what `document` shows, in document order, pausing
    after each entry that showed something new. Only given `bytes` slots
    show anything; a node of the wrong type is passed over (the writer
    refuses it)."""
    found = shown.found
    progress = 0  # bumped whenever something new is recorded

    def eol(slot: object) -> None:
        nonlocal progress
        if "eol" not in found and type(slot) is bytes:
            m = _LINE_BREAK.search(slot)
            if m is not None:
                found["eol"] = m.group()
                progress += 1

    def spacing(key: object, slot: object) -> None:
        nonlocal progress
        if (
            key not in found
            and type(slot) is bytes
            and len(slot) <= _STYLE_TEXT_MAX
            and _SPACING_OK(slot) is not None
        ):
            found[key] = slot
            progress += 1

    if not isinstance(document, LuaDocument) or not isinstance(document.assignments, tuple | list):
        return
    assignments: tuple[object, ...] | list[object] = document.assignments
    first = assignments[0] if assignments else None
    if isinstance(first, Assignment) and type(first.lead) is bytes:
        found["blank"] = first.lead[:1] in (b"\r", b"\n")
    seen = -1
    # Tables already walked, by identity: only style detection uses this, so
    # a document that places one table many times (the parser never shares
    # tables; a caller may) is walked once per distinct table, not once per
    # place (which doubles per level of nesting).
    walked: set[int] = set()
    for assignment in assignments:
        if not isinstance(assignment, Assignment):
            continue
        eol(assignment.lead)
        eol(assignment.eq_lead)
        spacing((_ASSIGN_FORM, "eq"), assignment.eq_lead)
        value = assignment.value
        if isinstance(value, _NODES):
            eol(value.lead)
            spacing((_ASSIGN_FORM, "val"), value.lead)
        if progress != seen:
            seen = progress
            yield
        if not isinstance(value, LuaTable) or id(value) in walked:
            continue
        walked.add(id(value))
        work: list[tuple[LuaTable, int, int]] = [(value, 1, 0)]
        while work:
            table, depth, index = work.pop()
            entries: object = table.entries
            if not isinstance(entries, tuple | list):
                continue
            descended = False
            while index < len(entries):
                entry = entries[index]
                index += 1
                if not isinstance(entry, Entry):
                    continue
                style = entry.style
                kind = style if style.__class__ is KeyStyle else None
                if kind is None:
                    for member in KeyStyle:
                        if style == member:
                            kind = member
                lead = entry.lead
                child = entry.value
                child_lead = child.lead if isinstance(child, _NODES) else None
                if "eol" not in found:
                    eol(lead)
                    if kind is not _P:
                        eol(getattr(entry.key, "lead", None))
                        eol(entry.key_close_lead)
                        eol(entry.eq_lead)
                    eol(child_lead)
                    eol(entry.sep_lead)
                if type(lead) is bytes:
                    if shown.indent is None:
                        cut = max(lead.rfind(b"\n"), lead.rfind(b"\r"))
                        run = lead[cut + 1 :]
                        if cut < 0:
                            pass  # on the line of what comes before: shows nothing
                        elif not run:
                            shown.indent = b""
                        elif _INDENT_OK(run) is not None and len(run) % depth == 0:
                            unit = run[: len(run) // depth]
                            if len(unit) <= _STYLE_TEXT_MAX and unit * depth == run:
                                shown.indent = unit
                        if shown.indent is not None:
                            progress += 1
                    if shown.comments is None and kind is _P:
                        comment = entry.comment
                        if comment is None:
                            shown.comments = False
                        elif type(comment) is bytes and _ARRAY_COMMENT(comment) is not None:
                            shown.comments = True
                        if shown.comments is not None:
                            progress += 1
                if kind is not None and kind is not _P:
                    klead, kclose, eq, val = _FORM_KEYS[kind]
                    if kind is not _W:
                        if klead not in found:
                            spacing(klead, getattr(entry.key, "lead", None))
                        if kclose not in found:
                            spacing(kclose, entry.key_close_lead)
                    if eq not in found:
                        spacing(eq, entry.eq_lead)
                    if val not in found:
                        spacing(val, child_lead)
                if "sep" not in found:
                    sep = entry.sep
                    if sep == b"," or sep == b";":
                        found["sep"] = sep
                        progress += 1
                if progress != seen:
                    seen = progress
                    yield
                    if (
                        len(found) >= _PROPERTY_COUNT
                        and shown.indent is not None
                        and shown.comments is not None
                    ):
                        return
                if isinstance(child, LuaTable) and depth < MAX_DEPTH and id(child) not in walked:
                    walked.add(id(child))
                    work.append((table, depth, index))
                    work.append((child, depth + 1, 0))
                    descended = True
                    break
            if descended:
                continue
            close = table.close_lead
            eol(close)
            if not entries and "empty" not in found and type(close) is bytes:
                if _LINE_BREAK.search(close) is not None:
                    found["empty"] = None
                    progress += 1
                elif len(close) <= _STYLE_TEXT_MAX and _SPACING_OK(close) is not None:
                    found["empty"] = close
                    progress += 1
            if progress != seen:
                seen = progress
                yield
    eol(document.tail)


def _account_folder(target: Path) -> tuple[Path, Path] | None:
    """(the flavor folder, its `WTF/Account` folder) for the nearest
    `WTF/Account` above `target`, names compared with case folded (macOS and
    Windows installs are case-insensitive), both spelled as in `target`."""
    parts = target.parts
    for i in range(len(parts) - 2, 0, -1):
        if parts[i].casefold() == "wtf" and parts[i + 1].casefold() == "account":
            return Path(*parts[:i]), Path(*parts[: i + 2])
    return None


def _file_id(path: str | os.PathLike[str]) -> tuple[int, int] | None:
    """(`st_dev`, `st_ino`) of an existing file, or `None`."""
    try:
        info = Path(path).stat()
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino) if info.st_ino else None


def _sibling_paths(
    target: str | os.PathLike[str], lab_written: Iterable[str | os.PathLike[str]]
) -> list[Path]:
    """The sibling files of `target`, newest first (equal times in byte-wise
    order of the path relative to the flavor folder), at most
    `SIBLING_READ_LIMIT` of them.

    The flavor folder is found from the absolute path of `target` without
    following links, so a `WTF` that is a link (to a sync folder, say) still
    counts as this flavor's. A file is the target or in `lab_written` when
    it is the same file (`st_dev`, `st_ino`), whatever its spelling; a path
    that does not exist is compared after `Path.resolve()`."""
    try:
        target_path = Path(os.path.normpath(Path(target).absolute()))
    except (TypeError, ValueError):
        return []
    folders = _account_folder(target_path)
    if folders is None:
        return []
    flavor, account = folders
    skip_ids: set[tuple[int, int]] = set()
    skip_paths: set[Path] = set()
    for path in (target_path, *lab_written):
        identity = _file_id(path)
        if identity is not None:
            skip_ids.add(identity)
            continue
        try:
            skip_paths.add(Path(path).resolve())
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    ranked: list[tuple[int, bytes, Path]] = []
    for path in _list_siblings(account):
        try:
            info = path.stat(follow_symlinks=False)
        except (OSError, ValueError):
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_ino and (info.st_dev, info.st_ino) in skip_ids:
            continue
        if skip_paths:
            try:
                if path.resolve() in skip_paths:
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
        order = os.fsencode(path.relative_to(flavor).as_posix())
        ranked.append((-info.st_mtime_ns, order, path))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [path for _time, _order, path in ranked[:SIBLING_READ_LIMIT]]


def _list_siblings(account: Path) -> list[Path]:
    """Candidate sibling files under `account` (`<flavor>/WTF/Account`):
    `*/SavedVariables.lua`, `*/SavedVariables/*.lua` and
    `*/*/*/SavedVariables/*.lua`, names matched with case folded. Directory
    and file links are not followed. At most `_SIBLING_SCAN_LIMIT` directory
    entries are examined in all; past that, the rest is not listed."""
    found: list[Path] = []
    left = _SIBLING_SCAN_LIMIT

    def entries(folder: str | Path) -> list[os.DirEntry[str]]:
        nonlocal left
        listed: list[os.DirEntry[str]] = []
        if left <= 0:
            return listed
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    left -= 1
                    if left < 0:
                        break
                    listed.append(entry)
        except OSError:
            pass
        return listed

    def is_dir(entry: os.DirEntry[str]) -> bool:
        try:
            return entry.is_dir(follow_symlinks=False)
        except OSError:
            return False

    def is_file(entry: os.DirEntry[str]) -> bool:
        try:
            return entry.is_file(follow_symlinks=False)
        except OSError:
            return False

    def lua_files(folder: str) -> None:
        for entry in entries(folder):
            if entry.name.casefold().endswith(".lua") and is_file(entry):
                found.append(Path(entry.path))

    for per_account in entries(account):
        if not is_dir(per_account):
            continue
        for child in entries(per_account.path):
            name = child.name.casefold()
            if name == "savedvariables.lua":
                if is_file(child):
                    found.append(Path(child.path))
            elif name == "savedvariables":
                if is_dir(child):
                    lua_files(child.path)
            elif is_dir(child):  # a realm, or the Forever `<digits>` folder
                for character in entries(child.path):
                    if not is_dir(character):
                        continue
                    for folder in entries(character.path):
                        if folder.name.casefold() == "savedvariables" and is_dir(folder):
                            lua_files(folder.path)
    return found


def _read_sibling(path: Path) -> LuaDocument | None:
    """A sibling's document, from at most `SIBLING_PREFIX_BYTES` of it, or
    `None` when it cannot be read, is not a regular file or does not parse.
    Opened read-only, without following a link and without blocking (a FIFO
    is refused, not waited on), then checked on the open descriptor (L1)."""
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
        )
    except (OSError, ValueError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks: list[bytes] = []
        left = SIBLING_PREFIX_BYTES + 1
        while left > 0:
            chunk = os.read(fd, left)
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
    except OSError:
        return None
    finally:
        os.close(fd)
    data = b"".join(chunks)
    try:
        if len(data) <= SIBLING_PREFIX_BYTES:
            return parse(data)
        data = data[:SIBLING_PREFIX_BYTES]
        cut = max(data.rfind(b"\n"), data.rfind(b"\r")) + 1
        return _parse_prefix(data[:cut]) if cut else None
    except LuaDataError:
        return None
