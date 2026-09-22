"""filemap: what every path in an install is (docs/LAB_PLAN.md §6.2, M10-06).

The data lives in `filemap.toml`, next to this module. It is the single
source of truth: the tables in `docs/LAB_FILE_MAP.md` are rendered from it
between `<!-- filemap:begin <section> -->` / `<!-- filemap:end <section> -->`
markers (`scripts/gen_file_map.py --write`), and a test fails when the doc
and the data drift. Edit the TOML, never the rendered tables.

This module is pure: it knows patterns and rows, and matches a path already
split into segments relative to the install root or to a flavor folder.
Deciding which of the two a real path is inside, and whether it is a
directory, is `layout.classify()`'s job. Nothing here names a flavor,
product, interface or build (L6).

Pattern grammar (also documented at the top of `filemap.toml`): `/`-separated
segments; each is an fnmatch pattern compared case-insensitively, `**` (zero
or more segments), `<digits>` (only ASCII digits), `<name>` (not only ASCII
digits), or the whole pattern `.` (the base folder itself). A match pattern
ending in `/` matches directories only, one ending in `**` anything, any
other files only. `exclude` patterns match regardless of kind. When several
entries match, the first in file order wins.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from collections.abc import Sequence
from functools import cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = [
    "DATA_FILE",
    "DOC_COLUMNS",
    "Base",
    "FileMap",
    "FileMapEntry",
    "Kind",
    "Section",
    "load",
    "parse",
    "render_doc",
    "render_section",
]

DATA_FILE = "filemap.toml"
DOC_COLUMNS = ("Path", "What", "Written by / when", "Edit", "Tier", "Module")

Base = Literal["root", "flavor", "any"]
# What is being classified: a directory, a file, or unknown (a path that is
# not on disk, or a symlink, which is never followed to find out).
Kind = Literal["file", "dir", "any"]

_DIGITS = "<digits>"
_NAME = "<name>"
_ANY_DEPTH = "**"
_SELF = "."
_BEGIN = "<!-- filemap:begin {} -->"
_END = "<!-- filemap:end {} -->"
_BLOCK = re.compile(
    r"^<!-- filemap:begin (?P<id>[a-z0-9-]+) -->\n(?P<body>.*?)^<!-- filemap:end (?P=id) -->$",
    re.MULTILINE | re.DOTALL,
)
_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


class Section(BaseModel):
    """One table in `docs/LAB_FILE_MAP.md`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str


class FileMapEntry(BaseModel):
    """One row of the file map: what a path is, who writes it, whether the
    Lab may edit it, and which module reads it. The display fields are
    Markdown exactly as rendered in the doc."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    section: str
    base: Base
    doc_path: str
    what: str
    written_by: str
    # `gate` (through `guard` only, client closed), `no`, `n/a` or `—`,
    # sometimes with a note in parentheses; see the doc's Edit column key.
    edit: str
    tier: str
    module: str
    match: tuple[str, ...]
    exclude: tuple[str, ...] = ()

    @field_validator("doc_path", "what", "written_by", "edit", "tier", "module")
    @classmethod
    def _one_table_cell(cls, value: str) -> str:
        if not value or "|" in value or "\n" in value or value != value.strip():
            raise ValueError(f"not a single Markdown table cell: {value!r}")
        return value

    @field_validator("match", "exclude")
    @classmethod
    def _patterns_well_formed(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            if pattern == _SELF:
                continue
            body = pattern.removesuffix("/")
            if not body or body.startswith("/") or "\\" in pattern:
                raise ValueError(f"malformed pattern {pattern!r}")
            if any(seg in ("", ".", "..") for seg in body.split("/")):
                raise ValueError(f"malformed pattern {pattern!r}")
        return value

    @property
    def gated(self) -> bool:
        """True when the Lab may write this path, through `guard` only."""
        return self.edit.startswith("gate")

    def matches(self, parts: Sequence[str], kind: Kind) -> bool:
        """Whether a path (segments relative to this entry's base) matches."""
        path = tuple(parts)
        if any(_match(_segments(p), path) for p in self.exclude):
            return False
        return any(_matches_pattern(p, path, kind) for p in self.match)


class FileMap(BaseModel):
    """Every section and entry, in file order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: tuple[Section, ...]
    entries: tuple[FileMapEntry, ...]

    def entry(self, entry_id: str) -> FileMapEntry:
        for e in self.entries:
            if e.id == entry_id:
                return e
        raise KeyError(entry_id)

    def matches(
        self, parts: Sequence[str], base: Literal["root", "flavor"], kind: Kind
    ) -> tuple[FileMapEntry, ...]:
        """Every entry that matches, in file order (the first one wins).

        `parts` are the path's segments relative to `base`; `()` is the base
        folder itself.
        """
        return tuple(
            e for e in self.entries if e.base in (base, "any") and e.matches(tuple(parts), kind)
        )

    def classify(
        self, parts: Sequence[str], base: Literal["root", "flavor"], kind: Kind
    ) -> FileMapEntry | None:
        """The first matching entry, or `None`."""
        found = self.matches(parts, base, kind)
        return found[0] if found else None


def _segments(pattern: str) -> tuple[str, ...]:
    if pattern == _SELF:
        return ()
    return tuple(pattern.removesuffix("/").split("/"))


def _matches_pattern(pattern: str, parts: Sequence[str], kind: Kind) -> bool:
    segments = _segments(pattern)
    if pattern == _SELF or pattern.endswith("/"):
        wanted: Kind = "dir"
    elif segments[-1] == _ANY_DEPTH:
        wanted = "any"
    else:
        wanted = "file"
    if wanted != "any" and kind != "any" and wanted != kind:
        return False
    return _match(segments, tuple(parts))


def _segment(pattern: str, name: str) -> bool:
    if pattern == _DIGITS:
        return name.isascii() and name.isdigit()
    if pattern == _NAME:
        return not (name.isascii() and name.isdigit())
    return fnmatch.fnmatchcase(name.casefold(), pattern.casefold())


def _match(segments: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    if not segments:
        return not parts
    head, rest = segments[0], segments[1:]
    if head == _ANY_DEPTH:
        return any(_match(rest, parts[i:]) for i in range(len(parts) + 1))
    return bool(parts) and _segment(head, parts[0]) and _match(rest, parts[1:])


def parse(text: str) -> FileMap:
    """Build a `FileMap` from TOML text and check it is coherent: unique
    entry and section ids, every entry in a known section, every section
    with at least one entry, every entry with a match pattern."""
    data = tomllib.loads(text)
    sections = tuple(Section(**s) for s in data.get("section", []))
    entries = tuple(FileMapEntry(**e) for e in data.get("entry", []))
    section_ids = [s.id for s in sections]
    entry_ids = [e.id for e in entries]
    problems: list[str] = []
    for ident in section_ids + entry_ids:
        if not _ID.fullmatch(ident):
            problems.append(f"id {ident!r} is not lowercase-hyphenated")
    if len(set(section_ids)) != len(section_ids):
        problems.append("duplicate section id")
    if len(set(entry_ids)) != len(entry_ids):
        problems.append("duplicate entry id")
    for e in entries:
        if e.section not in section_ids:
            problems.append(f"{e.id}: unknown section {e.section!r}")
        if not e.match:
            problems.append(f"{e.id}: no match pattern")
    for s in sections:
        if not any(e.section == s.id for e in entries):
            problems.append(f"section {s.id!r} has no entries")
    if problems:
        raise ValueError("file map is incoherent: " + "; ".join(problems))
    return FileMap(sections=sections, entries=entries)


@cache
def load() -> FileMap:
    """The packaged file map (`filemap.toml`), parsed once per process."""
    text = resources.files("wowlab_core").joinpath(DATA_FILE).read_text(encoding="utf-8")
    return parse(text)


def render_section(section_id: str, filemap: FileMap | None = None) -> str:
    """One section's Markdown table: header, separator, one line per entry,
    each line ending in a newline."""
    fm = load() if filemap is None else filemap
    lines = [
        "| " + " | ".join(DOC_COLUMNS) + " |",
        "|" + "---|" * len(DOC_COLUMNS),
    ]
    for e in fm.entries:
        if e.section == section_id:
            cells = (e.doc_path, e.what, e.written_by, e.edit, e.tier, e.module)
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_doc(doc: str, filemap: FileMap | None = None) -> str:
    """`doc` with every marked block replaced by its rendered section.

    Raises `ValueError` when a section has no block, a block names no
    section, or a section has two blocks; everything outside the blocks is
    returned unchanged.
    """
    fm = load() if filemap is None else filemap
    known = [s.id for s in fm.sections]
    found = [m.group("id") for m in _BLOCK.finditer(doc)]
    problems = []
    for ident in known:
        if found.count(ident) != 1:
            problems.append(f"section {ident!r} has {found.count(ident)} marked blocks")
    problems.extend(
        f"marked block {ident!r} names no section" for ident in found if ident not in known
    )
    if problems:
        raise ValueError("; ".join(problems))

    def _replace(m: re.Match[str]) -> str:
        ident = m.group("id")
        return f"{_BEGIN.format(ident)}\n{render_section(ident, fm)}{_END.format(ident)}"

    return _BLOCK.sub(_replace, doc)
