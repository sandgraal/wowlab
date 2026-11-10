"""WTF text formats: ``Config.wtf``, ``config-cache.wtf``, ``bindings-cache.wtf``,
``macros-cache.txt`` (docs/LAB_PLAN.md §6.5, docs/LAB_FORMATS.md §5-§7) and a
character's ``AddOns.txt`` (M16-34; LAB_FORMATS amendment of 2026-09-22).

Read-only and lossless (L1, L4). Every parser works on bytes and returns a
document whose ``lines`` hold everything in the file, in order, each line
typed or :class:`Unknown`, each keeping its own text and its own line ending.
For any input within the budget, ``doc.bom + b"".join(line.raw for line in
doc.lines) == data`` and ``doc.to_bytes() == data``.

A leading UTF-8 byte order mark (``EF BB BF``) is its own prefix token,
``doc.bom`` (``b""`` when there is none), never part of line 1, so a ``SET``
line right after it is typed and ``cvar get`` reports it as line 1. Only the
first three bytes of the file can be the BOM; a second one is line 1's text.
Whether the client ever writes a BOM into these files is not observed: no
real capture has one **[verify]** (M16-25).

Budgets (M16-25, docs/LAB_PLAN.md §6.5): each file type has a byte budget
and a line budget (``CONFIG_BUDGET``, ``BINDINGS_BUDGET``,
``MACROS_BUDGET``). A document over either is refused with
:class:`WtfLimitError`, at the first byte or the first line past the
budget, before any line model is built. The readers read at most the byte
budget plus one byte, so refusing a giant file costs no more than that.

Line model, from the real files (LAB_FORMATS amendments of 2026-09-22):

- A line ends at LF. Its ending is ``b"\\r\\n"`` when a CR precedes that LF,
  ``b"\\n"`` otherwise, and ``b""`` for a last line with no break. Endings
  follow the file kind and may mix within one file, so they are per line. A
  lone CR is not a break; it stays in the line's text. The SET, bind and VER
  grammars below never match a CR, so outside a macro body such a line is
  ``Unknown``; inside a macro body it is body, like any other line.
- Nothing is decoded on the way in. Names, values and bodies are exposed as
  ``str`` decoded as UTF-8 with ``surrogateescape``, so a byte that is not
  UTF-8 survives as a lone surrogate and encodes back to the same byte.

Grammars (anything that does not match exactly is ``Unknown``):

- ``SET <name> "<value>"``: name is a run of bytes other than space, CR and
  LF (it may contain ``-``); value is any bytes other than ``"``, CR and LF,
  and may be empty or carry control bytes. Names are case-insensitive to the
  client; lookups fold ASCII case only and keep the original spelling.
- ``bind <key> <action>``: key is a run of bytes other than space, CR, LF;
  action is the rest of the line (non-empty, not starting with a space, no
  CR or LF). An action written as ``"..."`` with no inner quote is reported
  unwrapped, with ``quoted``.
- ``VER <version> <hex id> "<name>" "<icon>"`` opens a macro record. The
  pattern is anchored on the icon (no ``"``, space, CR or LF: the observed
  icon is a numeric file id, and icon names are believed to contain neither
  **[verify]**), so a name containing ``"`` still parses; with more than two
  quoted fields, everything before the last ``" "`` is the name. Every
  following line is body up to a line whose text is exactly ``END``
  (compared byte for byte: no case folding, no trimming). Lines outside a
  record are ``Unknown``. A record that reaches the end of the file without
  ``END`` is reported with ``complete=False``.
- ``<addon>: <state>`` in ``AddOns.txt``: the addon name is a run of bytes
  other than ``:``, CR and LF; one ``: `` follows; the state is a run of
  bytes other than space, ``:``, CR and LF. Only ``enabled`` is observed;
  ``disabled`` is **[verify]**, and any other word is kept as written. An
  addon the file does not list is at the client's default, so a missing
  name is not "disabled".

Hand-edited lines that the client may still read (``set`` in lower case,
extra spaces or tabs, an unquoted value) **[verify]** do not match these
grammars, so they are ``Unknown`` and are not seen by ``cvars``,
``effective``, ``get``, ``duplicates``, ``bindings`` or ``macros``. A caller
that reports a value from a document must also report that the document
has ``Unknown`` lines (``unknown_lines()``).

Documents validate that their lines are exactly what their own parser would
produce from ``to_bytes()``: a typed line's text matches its grammar, and an
``Unknown`` line never carries text the parser would type in that position.
``model_copy(update=...)`` skips that validation and keeps the cached views
of the original, so never use it on a document: build a new one instead.
Bytes fields do not survive a JSON round trip; a JSON-facing layer needs its
own output models.

No writer in Wave 1 (only ``guard`` writes into an install, L2). The typed
line models validate their own text, so a future writer can build lines and
trust the document stays well-formed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wowlab_core import snapshot

__all__ = [
    "BINDINGS_BUDGET",
    "BOM",
    "CONFIG_BUDGET",
    "MACROS_BUDGET",
    "MAX_ADDONS_TXT_BYTES",
    "AddonState",
    "AddonStateLine",
    "AddonsDocument",
    "BindLine",
    "Binding",
    "BindingsDocument",
    "CVar",
    "ConfigDocument",
    "DuplicateCVar",
    "Ending",
    "Macro",
    "MacroBodyLine",
    "MacroEndLine",
    "MacroHeaderLine",
    "MacrosDocument",
    "SetLine",
    "Unknown",
    "WtfBudget",
    "WtfLimitError",
    "count_cvar_names",
    "count_macro_records",
    "fold_name",
    "parse_addons",
    "parse_bindings",
    "parse_config",
    "parse_macros",
    "read_addons",
    "read_bindings",
    "read_config",
    "read_macros",
    "split_lines",
]

Ending = Literal[b"", b"\n", b"\r\n"]
Bom = Literal[b"", b"\xef\xbb\xbf"]

#: The UTF-8 byte order mark, a document's ``bom`` when the file starts with it.
BOM: Bom = b"\xef\xbb\xbf"
_MIB = 1024 * 1024

_SET = re.compile(rb'SET ([^ \r\n]+) "([^"\r\n]*)"')
_BIND = re.compile(rb"bind ([^ \r\n]+) ([^ \r\n][^\r\n]*)")
_QUOTED_ACTION = re.compile(rb'"([^"]*)"')
_VER = re.compile(rb'VER ([0-9]+) ([0-9A-Fa-f]+) "([^\r\n]*)" "([^" \r\n]*)"')
_END = b"END"
_ADDON_STATE = re.compile(rb"([^:\r\n]+): ([^ :\r\n]+)")
MAX_ADDONS_TXT_BYTES = 1 << 20
"""`read_addons` refuses a larger ``AddOns.txt``: the real one is 2 lines,
and one line per installed addon stays far below this."""
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def fold_name(name: str) -> str:
    """The lookup key for a CVar name: ASCII letters lowered, nothing else
    touched (the client compares names case-insensitively; non-ASCII folding
    is not something it is known to do)."""
    return name.translate(_ASCII_FOLD)


def split_lines(data: bytes) -> Iterator[tuple[bytes, Ending]]:
    """Yield ``(text, ending)`` for every line; joining them gives ``data``."""
    start = 0
    size = len(data)
    while start < size:
        lf = data.find(b"\n", start)
        if lf == -1:
            yield data[start:], b""
            return
        if lf > start and data[lf - 1] == 0x0D:
            yield data[start : lf - 1], b"\r\n"
        else:
            yield data[start:lf], b"\n"
        start = lf + 1


# --------------------------------------------------------------------------
# Budgets and the BOM (M16-25)


class WtfBudget(BaseModel):
    """The most one file of a type may hold: bytes (the whole file, BOM
    included) and lines (the document's ``lines``; the BOM is not one)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    max_bytes: int
    max_lines: int


# The real files are small: the Forever capture's `Config.wtf` is 3.7 KiB in
# 130 lines, and every `config-cache.wtf`, `bindings-cache.wtf` and
# `macros-cache.txt` in the corpus is under 1.1 KiB (fixtures/README.md). A
# client's whole CVar set is a few thousand names and a full key map a few
# thousand binds. A macros file holds one account's or one character's
# macros (120 and 30 slots are believed **[verify]**), each body at most 255
# characters, so a record is at most 258 lines (`VER`, 256 body lines of
# breaks alone, `END`) and a full file at most about 38,700 lines.
#
# The budgets also bound what one parse holds. A line model costs about 0.4
# to 0.7 KiB, and a two-line macro record also builds a `Macro` view entry.
# The serial RSS probe (`tests/parser/test_wtfconfig_budget_rss_constructed.py`)
# measures the densest shapes at each budget on Linux, the document and its
# views: about 61 MiB for config (the worst case), 55 MiB for bindings,
# 35 MiB for one long macro record and 55 MiB for 25,000 two-line macro
# records. Every document at its budget stays under 100 MiB, and the probe
# holds Linux to that. The macros line budget was 100,000 lines until review
# (M16-25 round 1): 50,000 two-line records then grew RSS by about 117 MiB.

#: `Config.wtf` and `config-cache.wtf` (`parse_config`, `read_config`).
CONFIG_BUDGET = WtfBudget(max_bytes=4 * _MIB, max_lines=50_000)
#: `bindings-cache.wtf` (`parse_bindings`, `read_bindings`).
BINDINGS_BUDGET = WtfBudget(max_bytes=4 * _MIB, max_lines=50_000)
#: `macros-cache.txt` (`parse_macros`, `read_macros`).
MACROS_BUDGET = WtfBudget(max_bytes=4 * _MIB, max_lines=50_000)


class WtfLimitError(ValueError):
    """A WTF file over its type's byte or line budget, refused before any
    line model is built.

    ``offset`` is the 0-based byte offset in the file of the first byte past
    the budget (``kind == "bytes"``) or of the first byte of the first line
    past it (``kind == "lines"``). ``line`` and ``column`` give the same
    position, 1-based, in the document's terms: ``line`` is the index in
    ``lines`` plus one, and ``column`` counts bytes from the start of that
    line, a BOM not included. ``limit`` is the budget exceeded; ``size`` is
    the file's size in bytes as far as it is known (a reader stops one byte
    past the budget and reports the size the open file showed).
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["bytes", "lines"],
        limit: int,
        size: int,
        line: int,
        column: int,
        offset: int,
    ) -> None:
        self.message = message
        self.kind = kind
        self.limit = limit
        self.size = size
        self.line = line
        self.column = column
        self.offset = offset
        super().__init__(f"line {line}, column {column}: {message}")


def _split_bom(data: bytes) -> tuple[Bom, bytes]:
    if data.startswith(BOM):
        return BOM, data[len(BOM) :]
    return b"", data


def _check_budget(data: bytes, budget: WtfBudget, what: str, size: int | None) -> None:
    """Refuse `data` (a whole file, or a reader's first `max_bytes + 1`
    bytes of one) when it is over `budget`, at the first byte or line past
    it. Counts at C speed and holds nothing per line."""
    size = len(data) if size is None else max(size, len(data))
    bom = len(BOM) if data.startswith(BOM) else 0
    if len(data) > budget.max_bytes:
        offset = budget.max_bytes
        line_start = max(data.rfind(b"\n", 0, offset) + 1, bom)
        raise WtfLimitError(
            f"{what} is {size} bytes, over its {budget.max_bytes}-byte budget",
            kind="bytes",
            limit=budget.max_bytes,
            size=size,
            line=data.count(b"\n", 0, offset) + 1,
            column=offset - line_start + 1,
            offset=offset,
        )
    lines = data.count(b"\n")
    if len(data) > bom and not data.endswith(b"\n"):
        lines += 1  # a last line with no ending
    if lines > budget.max_lines:
        offset = bom
        for _ in range(budget.max_lines):
            offset = data.index(b"\n", offset) + 1
        raise WtfLimitError(
            f"{what} has {lines} lines, over its {budget.max_lines}-line budget",
            kind="lines",
            limit=budget.max_lines,
            size=size,
            line=budget.max_lines + 1,
            column=1,
            offset=offset,
        )


# --------------------------------------------------------------------------
# Line models


class _Line(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    text: bytes
    """The line's bytes without its ending."""
    ending: Ending
    """``b"\\n"``, ``b"\\r\\n"``, or ``b""`` for a last line with no break."""

    @model_validator(mode="after")
    def _no_break_in_text(self) -> _Line:
        if b"\n" in self.text:
            raise ValueError("a line's text cannot contain LF")
        if self.ending == b"\n" and self.text.endswith(b"\r"):
            raise ValueError("text ending in CR before an LF ending is a CRLF ending")
        return self

    @property
    def raw(self) -> bytes:
        """The line exactly as it appears in the file, ending included."""
        return self.text + self.ending


class Unknown(_Line):
    """A line no grammar of this document matched, kept verbatim (L4)."""

    kind: Literal["unknown"] = "unknown"


class SetLine(_Line):
    """``SET name "value"`` in ``Config.wtf`` or a ``config-cache.wtf``."""

    kind: Literal["set"] = "set"

    @model_validator(mode="after")
    def _matches(self) -> SetLine:
        if _SET.fullmatch(self.text) is None:
            raise ValueError(f"not a SET line: {self.text!r}")
        return self

    def _match(self) -> re.Match[bytes]:
        match = _SET.fullmatch(self.text)
        assert match is not None  # guaranteed by the validator
        return match

    @property
    def name(self) -> str:
        """The CVar name as written (original case)."""
        return _text(self._match().group(1))

    @property
    def value(self) -> str:
        """The value between the quotes, possibly empty."""
        return _text(self._match().group(2))


class BindLine(_Line):
    """``bind KEY ACTION`` in ``bindings-cache.wtf``."""

    kind: Literal["bind"] = "bind"

    @model_validator(mode="after")
    def _matches(self) -> BindLine:
        if _BIND.fullmatch(self.text) is None:
            raise ValueError(f"not a bind line: {self.text!r}")
        return self

    def _match(self) -> re.Match[bytes]:
        match = _BIND.fullmatch(self.text)
        assert match is not None  # guaranteed by the validator
        return match

    @property
    def key(self) -> str:
        return _text(self._match().group(1))

    @property
    def action_text(self) -> str:
        """The action exactly as written, quotes included if any."""
        return _text(self._match().group(2))

    @property
    def quoted(self) -> bool:
        """The action is written as ``"..."`` (e.g. a ``CLICK`` binding)."""
        return _QUOTED_ACTION.fullmatch(self._match().group(2)) is not None

    @property
    def action(self) -> str:
        """The action, with the surrounding quotes removed when ``quoted``."""
        raw = self._match().group(2)
        quoted = _QUOTED_ACTION.fullmatch(raw)
        return _text(quoted.group(1) if quoted is not None else raw)


class MacroHeaderLine(_Line):
    """``VER <version> <hex id> "<name>" "<icon>"``, opening a macro record."""

    kind: Literal["macro-header"] = "macro-header"

    @model_validator(mode="after")
    def _matches(self) -> MacroHeaderLine:
        if _VER.fullmatch(self.text) is None:
            raise ValueError(f"not a macro header: {self.text!r}")
        return self

    def _match(self) -> re.Match[bytes]:
        match = _VER.fullmatch(self.text)
        assert match is not None  # guaranteed by the validator
        return match

    @property
    def version(self) -> str:
        """The record format version as written (``3`` on the observed client)."""
        return _text(self._match().group(1))

    @property
    def macro_id(self) -> str:
        """The macro id as written: hex digits, case and leading zeros kept."""
        return _text(self._match().group(2))

    @property
    def name(self) -> str:
        """The macro name as written. A ``"`` inside it is accepted in case the
        client writes one; whether the macro editor allows it is not observed
        **[verify]**."""
        return _text(self._match().group(3))

    @property
    def icon(self) -> str:
        """The icon as written. Observed: a numeric file id (``134400``, believed
        to be the question mark, meaning "use the spell's icon" **[verify]**).
        The icon-name form (``INV_MISC_QUESTIONMARK``) comes from community
        documentation and has not been observed **[verify]**."""
        return _text(self._match().group(4))


class MacroBodyLine(_Line):
    """One line of a macro body. Any bytes, except that the text is not
    exactly ``END`` (that line closes the record)."""

    kind: Literal["macro-body"] = "macro-body"

    @model_validator(mode="after")
    def _not_end(self) -> MacroBodyLine:
        if self.text == _END:
            raise ValueError("a body line cannot be exactly END")
        return self


class MacroEndLine(_Line):
    """The ``END`` line that closes a macro record."""

    kind: Literal["macro-end"] = "macro-end"

    @model_validator(mode="after")
    def _is_end(self) -> MacroEndLine:
        if self.text != _END:
            raise ValueError(f"an END line is exactly END, not {self.text!r}")
        return self


ConfigLine = Annotated[SetLine | Unknown, Field(discriminator="kind")]
BindingsLine = Annotated[BindLine | Unknown, Field(discriminator="kind")]


class AddonStateLine(_Line):
    """``<addon>: <state>`` in a character's ``AddOns.txt``."""

    kind: Literal["addon-state"] = "addon-state"

    @model_validator(mode="after")
    def _matches(self) -> AddonStateLine:
        if _ADDON_STATE.fullmatch(self.text) is None:
            raise ValueError(f"not an addon state line: {self.text!r}")
        return self

    def _match(self) -> re.Match[bytes]:
        match = _ADDON_STATE.fullmatch(self.text)
        assert match is not None  # guaranteed by the validator
        return match

    @property
    def name(self) -> str:
        """The addon's name as written."""
        return _text(self._match().group(1))

    @property
    def state(self) -> str:
        """The state word as written (``enabled`` observed)."""
        return _text(self._match().group(2))


AddonsLine = Annotated[AddonStateLine | Unknown, Field(discriminator="kind")]
MacrosLine = Annotated[
    MacroHeaderLine | MacroBodyLine | MacroEndLine | Unknown, Field(discriminator="kind")
]


def _join(lines: Sequence[_Line]) -> bytes:
    return b"".join(line.raw for line in lines)


def _unknown_lines(lines: Sequence[_Line]) -> tuple[tuple[int, bytes], ...]:
    return tuple((i, line.text) for i, line in enumerate(lines) if isinstance(line, Unknown))


def _reject_typed_text(lines: Sequence[_Line], grammar: re.Pattern[bytes], what: str) -> None:
    for i, line in enumerate(lines):
        if isinstance(line, Unknown) and grammar.fullmatch(line.text) is not None:
            raise ValueError(f"line {i}: an Unknown line cannot hold a {what} line")


def _check_line_sequence(lines: Sequence[_Line]) -> None:
    """Only the last line may lack an ending, and it cannot also be empty:
    otherwise joining and re-parsing would not give the same lines back."""
    for i, line in enumerate(lines):
        if line.ending == b"" and i != len(lines) - 1:
            raise ValueError(f"line {i}: only the last line may have no ending")
    if lines and lines[-1].ending == b"" and lines[-1].text == b"":
        raise ValueError("an empty last line with no ending is not a line")


def _check_bom_prefix(bom: Bom, lines: Sequence[_Line]) -> None:
    """A document with no ``bom`` cannot start with one: its bytes would
    parse with those three bytes as the BOM, not as line 0's text."""
    if bom == b"" and lines and lines[0].text.startswith(BOM):
        raise ValueError("line 0 starts with a BOM; a leading BOM is the document's bom")


# --------------------------------------------------------------------------
# Config.wtf / config-cache.wtf


class CVar(BaseModel):
    """One ``SET`` line as a name and a value, with its position in ``lines``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    value: str
    index: int


class DuplicateCVar(BaseModel):
    """A CVar name (case-folded) set on more than one line of one file.
    ``effective`` is the last entry, per LAB_PLAN §6.5; that the client
    applies the last line is believed, not observed **[verify]**."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    folded_name: str
    entries: tuple[CVar, ...]

    @property
    def effective(self) -> CVar:
        return self.entries[-1]


class ConfigDocument(BaseModel):
    """A parsed ``Config.wtf`` or ``config-cache.wtf``.

    Frozen, and validated to equal ``parse_config(self.to_bytes())``.
    ``model_copy(update=...)`` skips validation and keeps the cached views,
    so build a new document instead.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lines: tuple[ConfigLine, ...]
    bom: Bom = b""
    """``BOM`` when the file starts with one, else ``b""``; never part of line 1."""

    @model_validator(mode="after")
    def _line_sequence(self) -> ConfigDocument:
        _check_line_sequence(self.lines)
        _check_bom_prefix(self.bom, self.lines)
        _reject_typed_text(self.lines, _SET, "SET")
        return self

    def to_bytes(self) -> bytes:
        return self.bom + _join(self.lines)

    def unknown_lines(self) -> tuple[tuple[int, bytes], ...]:
        """``(index, text)`` for every ``Unknown`` line. These are not seen
        by the views below; report them alongside any value."""
        return _unknown_lines(self.lines)

    @cached_property
    def cvars(self) -> tuple[CVar, ...]:
        """Every ``SET`` line, in file order, duplicates included."""
        return tuple(
            CVar(name=line.name, value=line.value, index=i)
            for i, line in enumerate(self.lines)
            if isinstance(line, SetLine)
        )

    @cached_property
    def _effective(self) -> dict[str, CVar]:
        result: dict[str, CVar] = {}
        for cvar in self.cvars:
            result[fold_name(cvar.name)] = cvar
        return result

    def effective(self) -> dict[str, CVar]:
        """One entry per name: the last line in *this file* that sets it,
        keyed by the case-folded name, in order of each name's first
        appearance.

        This is not the value the client uses. That also depends on the
        other scope files (``Config.wtf``, account and character
        ``config-cache.wtf``), on the client default for names absent here,
        on the server, which may replace ``config-cache.wtf`` at login when
        settings sync is on, and on timing: the file holds only what the
        client wrote at its last exit. ``Unknown`` lines are not seen.
        """
        return dict(self._effective)

    def get(self, name: str) -> CVar | None:
        """The ``effective()`` entry for ``name`` (the last line in this file),
        compared case-insensitively; see ``effective()`` for what it is not."""
        return self._effective.get(fold_name(name))

    def __getitem__(self, name: str) -> str:
        cvar = self.get(name)
        if cvar is None:
            raise KeyError(name)
        return cvar.value

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def duplicates(self) -> tuple[DuplicateCVar, ...]:
        """Every name set more than once, in order of first appearance."""
        groups: dict[str, list[CVar]] = {}
        for cvar in self.cvars:
            groups.setdefault(fold_name(cvar.name), []).append(cvar)
        return tuple(
            DuplicateCVar(folded_name=folded, entries=tuple(entries))
            for folded, entries in groups.items()
            if len(entries) > 1
        )


def parse_config(data: bytes) -> ConfigDocument:
    """Parse a ``Config.wtf`` or ``config-cache.wtf``. Over
    ``CONFIG_BUDGET``, raises :class:`WtfLimitError`."""
    return _parse_config(data, None)


def _parse_config(data: bytes, size: int | None) -> ConfigDocument:
    _check_budget(data, CONFIG_BUDGET, "the config file", size)
    bom, body = _split_bom(data)
    lines: list[SetLine | Unknown] = []
    for text, ending in split_lines(body):
        if _SET.fullmatch(text) is not None:
            lines.append(SetLine(text=text, ending=ending))
        else:
            lines.append(Unknown(text=text, ending=ending))
    return ConfigDocument(lines=tuple(lines), bom=bom)


# --------------------------------------------------------------------------
# bindings-cache.wtf


class Binding(BaseModel):
    """One ``bind`` line as a key and an action, with its position in ``lines``.
    An action of ``NONE`` may mean the key was cleared **[verify]**."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    key: str
    action: str
    quoted: bool
    index: int


class BindingsDocument(BaseModel):
    """A parsed ``bindings-cache.wtf`` (account or character).

    Which file the client applies depends on the per-character bindings
    toggle, and the server may replace the file at login when bindings sync
    is on.

    Frozen, and validated to equal ``parse_bindings(self.to_bytes())``.
    ``model_copy(update=...)`` skips validation and keeps the cached views,
    so build a new document instead.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lines: tuple[BindingsLine, ...]
    bom: Bom = b""
    """``BOM`` when the file starts with one, else ``b""``; never part of line 1."""

    @model_validator(mode="after")
    def _line_sequence(self) -> BindingsDocument:
        _check_line_sequence(self.lines)
        _check_bom_prefix(self.bom, self.lines)
        _reject_typed_text(self.lines, _BIND, "bind")
        return self

    def to_bytes(self) -> bytes:
        return self.bom + _join(self.lines)

    def unknown_lines(self) -> tuple[tuple[int, bytes], ...]:
        """``(index, text)`` for every ``Unknown`` line (not in ``bindings``)."""
        return _unknown_lines(self.lines)

    @cached_property
    def bindings(self) -> tuple[Binding, ...]:
        """Every ``bind`` line, in file order."""
        return tuple(
            Binding(key=line.key, action=line.action, quoted=line.quoted, index=i)
            for i, line in enumerate(self.lines)
            if isinstance(line, BindLine)
        )


def parse_bindings(data: bytes) -> BindingsDocument:
    """Parse a ``bindings-cache.wtf``. Over ``BINDINGS_BUDGET``, raises
    :class:`WtfLimitError`."""
    return _parse_bindings(data, None)


def _parse_bindings(data: bytes, size: int | None) -> BindingsDocument:
    _check_budget(data, BINDINGS_BUDGET, "the bindings file", size)
    bom, body = _split_bom(data)
    lines: list[BindLine | Unknown] = []
    for text, ending in split_lines(body):
        if _BIND.fullmatch(text) is not None:
            lines.append(BindLine(text=text, ending=ending))
        else:
            lines.append(Unknown(text=text, ending=ending))
    return BindingsDocument(lines=tuple(lines), bom=bom)


# --------------------------------------------------------------------------
# macros-cache.txt


class Macro(BaseModel):
    """One macro record. ``body`` is the body lines joined with their own
    endings, so the line break before ``END`` is part of it: these are the
    file's bytes. The macro text the client shows is believed to be
    ``body_lines`` joined by LF with no final break **[verify]**, and any
    length limit applies to that, not to ``body``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    version: str
    macro_id: str
    name: str
    icon: str
    body: str
    body_lines: tuple[str, ...]
    """Each body line's text, without its ending."""
    header_index: int
    """Position of the ``VER`` line in ``lines``."""
    complete: bool
    """False when the file ended before this record's ``END``."""


class MacrosDocument(BaseModel):
    """A parsed ``macros-cache.txt`` (account or character). The server may
    replace the file at login when macro sync is on.

    Frozen, and validated to equal ``parse_macros(self.to_bytes())``.
    ``model_copy(update=...)`` skips validation and keeps the cached views,
    so build a new document instead.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lines: tuple[MacrosLine, ...]
    bom: Bom = b""
    """``BOM`` when the file starts with one, else ``b""``; never part of line 1."""

    @model_validator(mode="after")
    def _records_are_well_formed(self) -> MacrosDocument:
        _check_line_sequence(self.lines)
        _check_bom_prefix(self.bom, self.lines)
        in_record = False
        for i, line in enumerate(self.lines):
            if isinstance(line, Unknown) and _VER.fullmatch(line.text) is not None:
                raise ValueError(f"line {i}: a VER line outside a record is a header")
            if isinstance(line, MacroHeaderLine):
                if in_record:
                    raise ValueError(f"line {i}: a VER line inside a record is body")
                in_record = True
            elif isinstance(line, MacroBodyLine | MacroEndLine):
                if not in_record:
                    raise ValueError(f"line {i}: {line.kind} outside a macro record")
                in_record = isinstance(line, MacroBodyLine)
            elif in_record:
                raise ValueError(f"line {i}: unknown line inside a macro record is body")
        return self

    def to_bytes(self) -> bytes:
        return self.bom + _join(self.lines)

    def unknown_lines(self) -> tuple[tuple[int, bytes], ...]:
        """``(index, text)`` for every ``Unknown`` line (outside any record)."""
        return _unknown_lines(self.lines)

    @cached_property
    def macros(self) -> tuple[Macro, ...]:
        """Every record, in file order."""
        result: list[Macro] = []
        header: MacroHeaderLine | None = None
        header_index = 0
        body: list[MacroBodyLine] = []

        def close(complete: bool) -> None:
            assert header is not None
            result.append(
                Macro(
                    version=header.version,
                    macro_id=header.macro_id,
                    name=header.name,
                    icon=header.icon,
                    body=_text(_join(body)),
                    body_lines=tuple(_text(line.text) for line in body),
                    header_index=header_index,
                    complete=complete,
                )
            )

        for i, line in enumerate(self.lines):
            if isinstance(line, MacroHeaderLine):
                header, header_index, body = line, i, []
            elif isinstance(line, MacroBodyLine):
                body.append(line)
            elif isinstance(line, MacroEndLine):
                close(complete=True)
                header = None
        if header is not None:
            close(complete=False)
        return tuple(result)


def parse_macros(data: bytes) -> MacrosDocument:
    """Parse a ``macros-cache.txt``. Over ``MACROS_BUDGET``, raises
    :class:`WtfLimitError`."""
    return _parse_macros(data, None)


def _parse_macros(data: bytes, size: int | None) -> MacrosDocument:
    _check_budget(data, MACROS_BUDGET, "the macros file", size)
    bom, body = _split_bom(data)
    lines: list[MacroHeaderLine | MacroBodyLine | MacroEndLine | Unknown] = []
    in_record = False
    for text, ending in split_lines(body):
        if in_record:
            if text == _END:
                lines.append(MacroEndLine(text=text, ending=ending))
                in_record = False
            else:
                lines.append(MacroBodyLine(text=text, ending=ending))
        elif _VER.fullmatch(text) is not None:
            lines.append(MacroHeaderLine(text=text, ending=ending))
            in_record = True
        else:
            lines.append(Unknown(text=text, ending=ending))
    return MacrosDocument(lines=tuple(lines), bom=bom)


# --------------------------------------------------------------------------
# AddOns.txt


class AddonState(BaseModel):
    """One ``<addon>: <state>`` line, with its position in ``lines``.
    ``enabled`` is True for ``enabled``, False for ``disabled`` (a word not
    yet seen in a real file **[verify]**) and None for any other word."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    state: str
    enabled: bool | None
    index: int


class AddonsDocument(BaseModel):
    """A parsed ``AddOns.txt`` of a character folder: which addons are
    enabled for that character. Which of a Forever character's two copies
    the client reads, and whether it reads this file at all there, are
    **[verify]** (LAB_FORMATS, amendment of 2026-09-22).

    Frozen, and validated to equal ``parse_addons(self.to_bytes())``.
    ``model_copy(update=...)`` skips validation and keeps the cached views,
    so build a new document instead.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lines: tuple[AddonsLine, ...]

    @model_validator(mode="after")
    def _line_sequence(self) -> AddonsDocument:
        _check_line_sequence(self.lines)
        _reject_typed_text(self.lines, _ADDON_STATE, "addon state")
        return self

    def to_bytes(self) -> bytes:
        return _join(self.lines)

    def unknown_lines(self) -> tuple[tuple[int, bytes], ...]:
        """``(index, text)`` for every ``Unknown`` line (not in ``states``)."""
        return _unknown_lines(self.lines)

    @cached_property
    def states(self) -> tuple[AddonState, ...]:
        """Every ``<addon>: <state>`` line, in file order."""
        out = []
        for i, line in enumerate(self.lines):
            if isinstance(line, AddonStateLine):
                state = line.state
                enabled = True if state == "enabled" else False if state == "disabled" else None
                out.append(AddonState(name=line.name, state=state, enabled=enabled, index=i))
        return tuple(out)

    def state_of(self, addon: str) -> AddonState | None:
        """The last line naming ``addon`` (ASCII case folded, as folder names
        compare on the client's file systems **[verify]**), or None when the
        file does not list it."""
        return self.by_folded_name.get(fold_name(addon))

    @cached_property
    def by_folded_name(self) -> dict[str, AddonState]:
        """Each folded name's last line, built once per document."""
        return {fold_name(s.name): s for s in self.states}


def parse_addons(data: bytes) -> AddonsDocument:
    lines: list[AddonStateLine | Unknown] = []
    for text, ending in split_lines(data):
        if _ADDON_STATE.fullmatch(text) is not None:
            lines.append(AddonStateLine(text=text, ending=ending))
        else:
            lines.append(Unknown(text=text, ending=ending))
    return AddonsDocument(lines=tuple(lines))


# --------------------------------------------------------------------------
# Counts without a document (M16-35): one pass over the bytes, by the same
# line split and grammar as the parsers above, building no line models.


def count_cvar_names(data: bytes) -> int:
    """``len(parse_config(data).effective())``: the distinct CVar names
    (``fold_name``) set on a ``SET`` line, without building the document. A
    leading BOM is skipped as the parser skips it. No budget: it holds one
    name per distinct CVar, and its caller bounds what it reads."""
    names: set[str] = set()
    for text, _ending in split_lines(_split_bom(data)[1]):
        match = _SET.fullmatch(text)
        if match is not None:
            names.add(fold_name(_text(match.group(1))))
    return len(names)


def count_macro_records(data: bytes) -> int:
    """``len(parse_macros(data).macros)``: the ``VER`` records, complete or
    not, read by `parse_macros`'s own state machine, without building the
    document. A leading BOM is skipped as the parser skips it. No budget: it
    holds nothing per line, and its caller bounds what it reads."""
    records = 0
    in_record = False
    for text, _ending in split_lines(_split_bom(data)[1]):
        if in_record:
            in_record = text != _END
        elif _VER.fullmatch(text) is not None:
            records += 1
            in_record = True
    return records


# --------------------------------------------------------------------------
# Path helpers: open read-only, never write (L1), through the guarded opener
# (`snapshot.read_regular_file`, M16-26): one non-blocking open that does not
# follow a final link, then an `fstat` that must show a regular file, so a
# FIFO or a link is refused (`UnsafeReadError`, or `ELOOP`) instead of read.
# A path past Windows' plain limit is opened by its extended-length spelling.
# At most the type's byte budget plus one byte is read (M16-25), so a file
# over it is refused at the first byte past the budget, whatever its size.

_READ_CHUNK = 1024 * 1024


def _read(path: Path, budget: WtfBudget) -> tuple[bytes, int]:
    """At most the file's first `max_bytes + 1` bytes, and its size as the
    open file's `fstat` showed it (or as far as it was read, if it grew)."""
    fd, st = snapshot.open_regular_file(Path(path))
    try:
        cap = budget.max_bytes + 1
        chunks: list[bytes] = []
        total = 0
        while total < cap:
            # The first read asks for the whole file plus one byte, so the
            # usual file arrives in one piece.
            want = st.st_size + 1 if not chunks else _READ_CHUNK
            chunk = os.read(fd, min(want, cap - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks), max(st.st_size, total)


def read_config(path: Path) -> ConfigDocument:
    data, size = _read(path, CONFIG_BUDGET)
    return _parse_config(data, size)


def read_bindings(path: Path) -> BindingsDocument:
    data, size = _read(path, BINDINGS_BUDGET)
    return _parse_bindings(data, size)


def read_macros(path: Path) -> MacrosDocument:
    data, size = _read(path, MACROS_BUDGET)
    return _parse_macros(data, size)


def read_addons(path: Path, *, limit: int = MAX_ADDONS_TXT_BYTES) -> AddonsDocument:
    """A character's ``AddOns.txt``; one over ``limit`` bytes is refused
    (``UnsafeReadError``) before it is read."""
    return parse_addons(snapshot.read_regular_file(Path(path), limit=limit))
