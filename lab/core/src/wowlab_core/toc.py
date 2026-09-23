"""toc: addon manifest parser (docs/LAB_PLAN.md §6.3, docs/LAB_FORMATS.md §3, M10-06).

Read-only and lossless (L1, L4). `parse_toc` takes bytes and returns a
`TocDocument` whose `lines` hold every line of the file in order, each with
its own bytes and its own line ending; `serialize_toc(parse_toc(x)) == x`
for any input.

Line model (LAB_FORMATS §3 and its 2026-09-22 amendment, from the real
DBM-Challenges TOC):

- A line ends at LF; its ending is CRLF when a CR precedes that LF, LF
  otherwise, and empty for a last line with no break. A lone CR is not a
  break and stays in the line. A UTF-8 BOM at the start of the file is kept
  in `TocDocument.bom`, outside the first line.
- `## Key: Value` is a directive. After `##`, optional spaces or tabs, then
  the key (a run of characters with no whitespace and no `:`), optional
  spaces or tabs, `:`, and the value; spaces and tabs around the value are
  insignificant (`## Title:|cff…` has none). Keys are kept as found and
  looked up with ASCII case folded. Directives may follow blank lines and
  file lines; parsing never stops at the first blank.
- Any other line starting with `#` is a comment (`# text`, and `##` without a
  `key:`, including `## Some words: …`, whose "key" has a space).
- A line of spaces and tabs only, or empty, is blank.
- Anything else is a file to load. Its text is split into the path and its
  bracketed load conditions, evaluating nothing: a `[…]` group separated
  from the path by whitespace is a condition, before the path
  (`[AllowLoadGameType mainline] Foo.lua`, **[verify]**) or after it
  (`Shadowlands\\Torghast.lua [AllowLoadGameType standard]`, observed). A
  `[…]` group touching path characters is a variable inside the path
  (`Locales\\[TextLocale].lua`, `[Family]\\Bar.xml`, both **[verify]**).
  Both `\\` and `/` occur as separators; the path text is kept as written.

Text fields decode UTF-8 with `errors="replace"`, so every model dumps to
JSON; the bytes (`raw`) are what is kept, and they round-trip through JSON as
base64. `decode_errors` says whether any byte was not UTF-8.

`## Interface:` is one number or a comma-separated list; each item that is
not an ASCII decimal is kept as text in `Interface.unparsed` and never
raises. An addon's list is what its author claims, not client evidence.
Which of two duplicate directives the client honours is **[verify]**; `get`
returns the first and `get_all` all of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "KNOWN_DIRECTIVES",
    "KNOWN_DIRECTIVE_PREFIXES",
    "MAX_TOC_BYTES",
    "Blank",
    "Comment",
    "Condition",
    "Directive",
    "FileLine",
    "Interface",
    "TocDocument",
    "TocLine",
    "TocTooLargeError",
    "parse_interface",
    "parse_toc",
    "read_toc",
    "serialize_toc",
]

# The closed directive vocabulary in LAB_FORMATS §3 and its 2026-09-21
# amendment, used only to flag a directive as known. Unknown ones are kept
# exactly like known ones (L4).
KNOWN_DIRECTIVES = frozenset(
    name.casefold()
    for name in (
        "Interface",
        "Title",
        "Notes",
        "Author",
        "Version",
        "SavedVariables",
        "SavedVariablesPerCharacter",
        "SavedVariablesMachine",
        "Dependencies",
        "RequiredDeps",
        "OptionalDeps",
        "LoadOnDemand",
        "LoadWith",
        "LoadManagers",
        "DefaultState",
        "IconTexture",
        "IconAtlas",
        "Group",
        "AllowLoad",
        "AllowLoadGameType",
        "OnlyBetaAndPTR",
        "LoadSavedVariablesFirst",
        "UseSecureEnvironment",
        "AllowAddOnTableAccess",
        "LoadFirst",
        "OptionalDep",
        "RequiredDep",
        "Dep",
    )
)
KNOWN_DIRECTIVE_PREFIXES = ("X-", "AddonCompartmentFunc", "Category")

# A TOC is a short text file; one larger than this is not read.
MAX_TOC_BYTES = 1 << 20

_BOM = b"\xef\xbb\xbf"
# Linear on any input: the value is taken whole and trimmed afterwards.
_DIRECTIVE = re.compile(rb"##[ \t]*([^\s:]+)[ \t]*:(.*)")
_VARIABLE = re.compile(r"\[([^\[\]]*)\]")
# A localized directive: `Title-deDE`, `X-DBM-Mod-Name-koKR`.
_LOCALE_SUFFIX = re.compile(r"(.+)-([a-z]{2}[A-Z]{2})")
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

Ending = Literal["", "\n", "\r\n"]


class TocTooLargeError(ValueError):
    """A TOC file is larger than the read limit."""


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _fold(key: str) -> str:
    return key.translate(_ASCII_FOLD)


class _Model(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )


class _Line(_Model):
    raw: bytes  # the line's bytes without its ending (and without a BOM)
    ending: Ending

    @field_validator("raw")
    @classmethod
    def _no_line_break(cls, value: bytes) -> bytes:
        if b"\n" in value:
            raise ValueError("a line's bytes cannot contain LF")
        return value

    @property
    def text(self) -> str:
        """The line decoded as UTF-8, undecodable bytes replaced."""
        return _text(self.raw)


class Directive(_Line):
    kind: Literal["directive"] = "directive"
    key: str  # as found, e.g. "Title-deDE"
    value: str  # trimmed of surrounding spaces and tabs

    @property
    def base_key(self) -> str:
        """The key without a locale suffix: `Title-deDE` -> `Title`."""
        m = _LOCALE_SUFFIX.fullmatch(self.key)
        return m.group(1) if m else self.key

    @property
    def locale(self) -> str | None:
        """The locale suffix of a key (`Title-deDE` -> `deDE`), or `None`.

        Keys ending in a locale code also occur on `X-` directives
        (`X-DBM-Mod-Name-koKR`); for those it is the addon's own naming, and
        whether the client resolves a locale suffix on anything but `Title`
        and `Notes` is **[verify]**.
        """
        m = _LOCALE_SUFFIX.fullmatch(self.key)
        return m.group(2) if m else None

    @property
    def known(self) -> bool:
        """Whether the key (without a locale suffix) is on the LAB_FORMATS §3 list."""
        base = self.base_key
        return base.casefold() in KNOWN_DIRECTIVES or any(
            base.casefold().startswith(p.casefold()) for p in KNOWN_DIRECTIVE_PREFIXES
        )


class Comment(_Line):
    kind: Literal["comment"] = "comment"


class Blank(_Line):
    kind: Literal["blank"] = "blank"


class Condition(_Model):
    """A bracketed load condition on a file line, kept as text, never evaluated."""

    text: str  # inside the brackets, e.g. "AllowLoadGameType standard"
    position: Literal["before", "after"]


class FileLine(_Line):
    kind: Literal["file"] = "file"
    path: str  # as written, separators untouched, conditions removed
    conditions: tuple[Condition, ...]  # in the order they appear on the line
    variables: tuple[str, ...]  # `[Name]` groups inside the path, names only


TocLine = Annotated[Directive | Comment | Blank | FileLine, Field(discriminator="kind")]


class Interface(_Model):
    """A parsed `## Interface:` value."""

    text: str  # the directive's value as found
    versions: tuple[int, ...]  # every item that is an ASCII decimal, in order
    unparsed: tuple[str, ...]  # every other non-empty item, as text

    @property
    def ok(self) -> bool:
        return bool(self.versions) and not self.unparsed


def parse_interface(value: str) -> Interface:
    """`"100, 200"` -> versions `(100, 200)`. Never raises."""
    versions: list[int] = []
    unparsed: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if item.isascii() and item.isdecimal():
            versions.append(int(item))
        else:
            unparsed.append(item)
    if not versions and not unparsed and value.strip():
        unparsed.append(value.strip())
    return Interface(text=value, versions=tuple(versions), unparsed=tuple(unparsed))


class TocDocument(_Model):
    """A parsed TOC: every line in order, and whether the file opened with a BOM."""

    bom: bool
    lines: tuple[TocLine, ...]
    decode_errors: bool

    def to_bytes(self) -> bytes:
        """The file, byte for byte."""
        body = b"".join(line.raw + line.ending.encode("ascii") for line in self.lines)
        return (_BOM if self.bom else b"") + body

    @property
    def directives(self) -> tuple[Directive, ...]:
        return tuple(line for line in self.lines if isinstance(line, Directive))

    @property
    def files(self) -> tuple[FileLine, ...]:
        return tuple(line for line in self.lines if isinstance(line, FileLine))

    @property
    def comments(self) -> tuple[Comment, ...]:
        return tuple(line for line in self.lines if isinstance(line, Comment))

    def get_all(self, key: str) -> tuple[Directive, ...]:
        """Every directive whose key equals `key`, ASCII case folded, in order."""
        wanted = _fold(key)
        return tuple(d for d in self.directives if _fold(d.key) == wanted)

    def get(self, key: str) -> str | None:
        """The first directive's value for `key` (see the module docstring on
        duplicates), or `None`. `Title-deDE` is its own key."""
        found = self.get_all(key)
        return found[0].value if found else None

    def list_value(self, key: str) -> tuple[str, ...]:
        """A comma-separated directive as items: `"A, B,,C"` -> `("A", "B", "C")`."""
        value = self.get(key)
        if value is None:
            return ()
        return tuple(item.strip() for item in value.split(",") if item.strip())

    @property
    def interface(self) -> Interface | None:
        value = self.get("Interface")
        return None if value is None else parse_interface(value)

    @property
    def saved_variables(self) -> tuple[str, ...]:
        return self.list_value("SavedVariables")

    @property
    def saved_variables_per_character(self) -> tuple[str, ...]:
        return self.list_value("SavedVariablesPerCharacter")


def _split_lines(data: bytes) -> Iterator[tuple[bytes, Ending]]:
    start = 0
    size = len(data)
    while start < size:
        lf = data.find(b"\n", start)
        if lf == -1:
            yield data[start:], ""
            return
        if lf > start and data[lf - 1] == 0x0D:
            yield data[start : lf - 1], "\r\n"
        else:
            yield data[start:lf], "\n"
        start = lf + 1


def _split_conditions(text: str) -> tuple[list[Condition], str, list[Condition]]:
    """(conditions before, path, conditions after) in one pass each way.

    A `[…]` group (no bracket inside) is a condition when whitespace
    separates it from a non-empty path: leading groups are peeled left to
    right, trailing groups right to left. Every character is visited a
    bounded number of times, so hostile lines stay linear.
    """
    left, right = 0, len(text.rstrip())
    while left < right and text[left].isspace():
        left += 1
    before: list[Condition] = []
    while left < right and text[left] == "[":
        close = text.find("]", left + 1, right)
        if close == -1 or text.find("[", left + 1, close) != -1:
            break
        after_ws = close + 1
        while after_ws < right and text[after_ws].isspace():
            after_ws += 1
        if after_ws == close + 1 or after_ws >= right:
            break  # touching the path (a variable) or nothing after it
        before.append(Condition(text=text[left + 1 : close], position="before"))
        left = after_ws
    after: list[Condition] = []
    while right - left > 1 and text[right - 1] == "]":
        opening = text.rfind("[", left, right - 1)
        if opening == -1 or text.find("]", opening + 1, right - 1) != -1:
            break
        path_end = opening
        while path_end > left and text[path_end - 1].isspace():
            path_end -= 1
        if path_end in (opening, left):
            break  # touching the path, or no path before it
        after.append(Condition(text=text[opening + 1 : right - 1], position="after"))
        right = path_end
    after.reverse()
    return before, text[left:right].strip(), after


def _file_line(raw: bytes, ending: Ending) -> FileLine:
    before, path, after = _split_conditions(_text(raw))
    return FileLine(
        raw=raw,
        ending=ending,
        path=path,
        conditions=(*before, *after),
        variables=tuple(_VARIABLE.findall(path)),
    )


def _line(raw: bytes, ending: Ending) -> TocLine:
    if m := _DIRECTIVE.fullmatch(raw):
        value = m.group(2).strip(b" \t")
        return Directive(raw=raw, ending=ending, key=_text(m.group(1)), value=_text(value))
    if raw.startswith(b"#"):
        return Comment(raw=raw, ending=ending)
    if not raw.strip(b" \t"):
        return Blank(raw=raw, ending=ending)
    return _file_line(raw, ending)


def parse_toc(data: bytes) -> TocDocument:
    """Parse TOC bytes (LAB_FORMATS §3). Never raises on content."""
    bom = data.startswith(_BOM)
    body = data[len(_BOM) :] if bom else data
    try:
        body.decode("utf-8")
        errors = False
    except UnicodeDecodeError:
        errors = True
    lines = tuple(_line(raw, ending) for raw, ending in _split_lines(body))
    return TocDocument(bom=bom, lines=lines, decode_errors=errors)


def serialize_toc(document: TocDocument) -> bytes:
    """The bytes of `document`; `serialize_toc(parse_toc(x)) == x`."""
    return document.to_bytes()


def read_toc(path: Path | str, *, max_bytes: int = MAX_TOC_BYTES) -> TocDocument:
    """Read and parse one TOC file, opened read-only (L1).

    Raises `TocTooLargeError` for a file over `max_bytes` and `OSError` when
    it cannot be read; the caller reports either.
    """
    with Path(path).open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise TocTooLargeError(f"{path}: larger than {max_bytes} bytes")
    return parse_toc(data)
