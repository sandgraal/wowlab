"""WTF text formats: ``Config.wtf``, ``config-cache.wtf``, ``bindings-cache.wtf``
and ``macros-cache.txt`` (docs/LAB_PLAN.md §6.5, docs/LAB_FORMATS.md §5-§7).

Read-only and lossless (L1, L4). Every parser works on bytes and returns a
document whose ``lines`` hold everything in the file, in order, each line
typed or :class:`Unknown`, each keeping its own text and its own line ending.
For any input, ``b"".join(line.raw for line in doc.lines) == data`` and
``doc.to_bytes() == data``.

Line model, from the real files (LAB_FORMATS amendments of 2026-09-22):

- A line ends at LF. Its ending is ``b"\\r\\n"`` when a CR precedes that LF,
  ``b"\\n"`` otherwise, and ``b""`` for a last line with no break. Endings
  follow the file kind and may mix within one file, so they are per line. A
  lone CR is not a break; it stays in the line's text (and a typed grammar
  below never matches it, so such a line is ``Unknown``).
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
  CR or LF). An action written
  as ``"..."`` with no inner quote is reported unwrapped, with ``quoted``.
- ``VER <version> <hex id> "<name>" "<icon>"`` opens a macro record; every
  following line is body up to a line whose text is exactly ``END``. Lines
  outside a record are ``Unknown``. A record that reaches the end of the file
  without ``END`` is reported with ``complete=False``.

No writer in Wave 1 (only ``guard`` writes into an install, L2). The typed
line models validate their own text, so a future writer can build lines and
trust the document stays well-formed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
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
    "fold_name",
    "parse_bindings",
    "parse_config",
    "parse_macros",
    "read_bindings",
    "read_config",
    "read_macros",
    "split_lines",
]

Ending = Literal[b"", b"\n", b"\r\n"]

_SET = re.compile(rb'SET ([^ \r\n]+) "([^"\r\n]*)"')
_BIND = re.compile(rb"bind ([^ \r\n]+) ([^ \r\n][^\r\n]*)")
_QUOTED_ACTION = re.compile(rb'"([^"]*)"')
_VER = re.compile(rb'VER ([0-9]+) ([0-9A-Fa-f]+) "([^"\r\n]*)" "([^"\r\n]*)"')
_END = b"END"
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
        return _text(self._match().group(3))

    @property
    def icon(self) -> str:
        """The icon as written: a numeric file id or an icon name."""
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
MacrosLine = Annotated[
    MacroHeaderLine | MacroBodyLine | MacroEndLine | Unknown, Field(discriminator="kind")
]


def _join(lines: Sequence[_Line]) -> bytes:
    return b"".join(line.raw for line in lines)


def _check_line_sequence(lines: Sequence[_Line]) -> None:
    """Only the last line may lack an ending, and it cannot also be empty:
    otherwise joining and re-parsing would not give the same lines back."""
    for i, line in enumerate(lines):
        if line.ending == b"" and i != len(lines) - 1:
            raise ValueError(f"line {i}: only the last line may have no ending")
    if lines and lines[-1].ending == b"" and lines[-1].text == b"":
        raise ValueError("an empty last line with no ending is not a line")


# --------------------------------------------------------------------------
# Config.wtf / config-cache.wtf


class CVar(BaseModel):
    """One ``SET`` line as a name and a value, with its position in ``lines``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    value: str
    index: int


class DuplicateCVar(BaseModel):
    """A CVar name (case-folded) set on more than one line. The client keeps
    the last one, so ``effective`` is the last entry."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    folded_name: str
    entries: tuple[CVar, ...]

    @property
    def effective(self) -> CVar:
        return self.entries[-1]


class ConfigDocument(BaseModel):
    """A parsed ``Config.wtf`` or ``config-cache.wtf``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lines: tuple[ConfigLine, ...]

    @model_validator(mode="after")
    def _line_sequence(self) -> ConfigDocument:
        _check_line_sequence(self.lines)
        return self

    def to_bytes(self) -> bytes:
        return _join(self.lines)

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
        """One entry per name, the last line that sets it, keyed by the case-
        folded name, in order of each name's first appearance."""
        return dict(self._effective)

    def get(self, name: str) -> CVar | None:
        """The effective (last) entry for ``name``, compared case-insensitively."""
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
    lines: list[SetLine | Unknown] = []
    for text, ending in split_lines(data):
        if _SET.fullmatch(text) is not None:
            lines.append(SetLine(text=text, ending=ending))
        else:
            lines.append(Unknown(text=text, ending=ending))
    return ConfigDocument(lines=tuple(lines))


# --------------------------------------------------------------------------
# bindings-cache.wtf


class Binding(BaseModel):
    """One ``bind`` line as a key and an action, with its position in ``lines``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    key: str
    action: str
    quoted: bool
    index: int


class BindingsDocument(BaseModel):
    """A parsed ``bindings-cache.wtf``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lines: tuple[BindingsLine, ...]

    @model_validator(mode="after")
    def _line_sequence(self) -> BindingsDocument:
        _check_line_sequence(self.lines)
        return self

    def to_bytes(self) -> bytes:
        return _join(self.lines)

    @cached_property
    def bindings(self) -> tuple[Binding, ...]:
        """Every ``bind`` line, in file order."""
        return tuple(
            Binding(key=line.key, action=line.action, quoted=line.quoted, index=i)
            for i, line in enumerate(self.lines)
            if isinstance(line, BindLine)
        )


def parse_bindings(data: bytes) -> BindingsDocument:
    lines: list[BindLine | Unknown] = []
    for text, ending in split_lines(data):
        if _BIND.fullmatch(text) is not None:
            lines.append(BindLine(text=text, ending=ending))
        else:
            lines.append(Unknown(text=text, ending=ending))
    return BindingsDocument(lines=tuple(lines))


# --------------------------------------------------------------------------
# macros-cache.txt


class Macro(BaseModel):
    """One macro record. ``body`` is the body lines joined with their own
    endings, so the line break before ``END`` is part of it."""

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
    """A parsed ``macros-cache.txt`` (account or character)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lines: tuple[MacrosLine, ...]

    @model_validator(mode="after")
    def _records_are_well_formed(self) -> MacrosDocument:
        _check_line_sequence(self.lines)
        in_record = False
        for i, line in enumerate(self.lines):
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
        return _join(self.lines)

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
    lines: list[MacroHeaderLine | MacroBodyLine | MacroEndLine | Unknown] = []
    in_record = False
    for text, ending in split_lines(data):
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
    return MacrosDocument(lines=tuple(lines))


# --------------------------------------------------------------------------
# Path helpers: open read-only, never write (L1)


def read_config(path: Path) -> ConfigDocument:
    return parse_config(path.read_bytes())


def read_bindings(path: Path) -> BindingsDocument:
    return parse_bindings(path.read_bytes())


def read_macros(path: Path) -> MacrosDocument:
    return parse_macros(path.read_bytes())
