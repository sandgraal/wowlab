"""clientlists: the client's slash commands, key-binding commands and encoding limits (M22-18).

Three generated files ship inside the package, under `client_lists/`:
`slash-command.txt`, `binding-command.txt` and `encoding-limit.txt`. They are
written by hand, at development time, by `scripts/forever_api.py` from one
pinned commit of Blizzard's interface code (Gethe/wow-ui-source) and the
client's strings (Ketho/BlizzardInterfaceResources), and hold names and
short English labels only; that script's docstring says what each line
means and how it was read. Nothing here opens a file outside the package,
reads an install, or goes on the network.

The build the lists were read at is in each file's header (`ui-build`) and is
read from there, never written here (L6). A list from one build may miss a
command a later client adds, so every word built from these lists that says
"not the client's" names that build.

- `slash_command(text)`: the client's entry for a `/command` as typed (case
  does not matter, as in the client, which upper-cases a command), or None.
  The list is the union of every client language's texts, so a None means
  that no client language has it: an addon's command, or a typo.
- `binding_command(name)`: the client's key-binding command, with its English
  label and category label when the client's strings have them, or None: a
  command the client's own interface does not define (an addon's).
- `encoding_limits()`: the documented `C_EncodingUtil` constants.
"""

from __future__ import annotations

import functools
import re
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "BINDING_FILE",
    "DATA_DIR",
    "LIMIT_FILE",
    "SLASH_FILE",
    "BindingCommand",
    "ClientLists",
    "ClientListsError",
    "SlashCommand",
    "SlashSource",
    "binding_command",
    "encoding_limits",
    "lists",
    "parse",
    "slash_command",
]

DATA_DIR = "client_lists"
SLASH_FILE = "slash-command.txt"
BINDING_FILE = "binding-command.txt"
LIMIT_FILE = "encoding-limit.txt"

SlashSource = Literal["registered", "named", "emote", "channel"]
_SOURCES: tuple[SlashSource, ...] = ("registered", "named", "emote", "channel")
_HEADER = re.compile(r"# ([a-z-]+): (\S+)")
_COMMAND_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


class ClientListsError(ValueError):
    """A shipped list is not what the generator writes: a packaging fault."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SlashCommand(_Model):
    """One slash command text the client knows. `source`: `registered` (the
    client's slash-command code registers its key), `named` (a client string
    names it; registered elsewhere, or a chat type), `emote`, or `channel`
    (`/1` and up, a chat channel's number). `key` is the client's own name
    for the command (`CAST`), the emote's token, or `CHANNEL`."""

    text: str
    source: SlashSource
    key: str


class BindingCommand(_Model):
    """One key-binding command of the client's own interface. The labels are
    the client's English strings (None when the client has none); `category`
    is the category as the binding file names it (None: none)."""

    name: str
    category: str | None
    category_label: str | None
    label: str | None
    file: str  # the binding file, under the client's Interface/AddOns


class ClientLists(_Model):
    """The three lists as shipped, with the build they were read at."""

    ui_build: str
    ui_commit: str
    strings_build: str
    slash: dict[str, SlashCommand]  # by the text, case-folded
    bindings: dict[str, BindingCommand]  # by the command name
    limits: dict[str, int]


def _rows(file: str, text: str, columns: int) -> tuple[dict[str, str], list[list[str]]]:
    header: dict[str, str] = {}
    rows: list[list[str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("#"):
            if (match := _HEADER.fullmatch(line)) is not None:
                header.setdefault(match.group(1), match.group(2))
            continue
        if not line:
            continue
        cells = line.split("\t")
        if len(cells) != columns:
            raise ClientListsError(f"{file} line {number}: {len(cells)} columns, not {columns}")
        rows.append(cells)
    for field in ("ui-build", "ui-commit", "strings-build"):
        if field not in header:
            raise ClientListsError(f"{file}: no {field} line")
    if not rows:
        raise ClientListsError(f"{file}: no line")
    return header, rows


def parse(slash_text: str, binding_text: str, limit_text: str) -> ClientLists:
    """The three files' text as one `ClientLists`. Raises `ClientListsError`."""
    heads: list[dict[str, str]] = []
    header, rows = _rows(SLASH_FILE, slash_text, 3)
    heads.append(header)
    slash: dict[str, SlashCommand] = {}
    for text, source, key in rows:
        if not text.startswith("/") or source not in _SOURCES:
            raise ClientListsError(f"{SLASH_FILE}: {text!r} {source!r} is not a slash command")
        entry = SlashCommand.model_validate({"text": text, "source": source, "key": key})
        slash.setdefault(text.casefold(), entry)
    header, rows = _rows(BINDING_FILE, binding_text, 5)
    heads.append(header)
    bindings: dict[str, BindingCommand] = {}
    for name, category, category_label, label, file in rows:
        if _COMMAND_NAME.fullmatch(name) is None:
            raise ClientListsError(f"{BINDING_FILE}: {name!r} is not a command name")
        bindings[name] = BindingCommand(
            name=name,
            category=category or None,
            category_label=category_label or None,
            label=label or None,
            file=file,
        )
    header, rows = _rows(LIMIT_FILE, limit_text, 3)
    heads.append(header)
    limits: dict[str, int] = {}
    for name, value, _kind in rows:
        try:
            limits[name] = int(value)
        except ValueError:
            raise ClientListsError(f"{LIMIT_FILE}: {name} = {value!r}") from None
    first = heads[0]
    for other in heads[1:]:
        for field in ("ui-build", "ui-commit", "strings-build"):
            if other[field] != first[field]:
                raise ClientListsError(f"the lists name different {field}s")
    return ClientLists(
        ui_build=first["ui-build"],
        ui_commit=first["ui-commit"],
        strings_build=first["strings-build"],
        slash=slash,
        bindings=bindings,
        limits=limits,
    )


@functools.cache
def lists() -> ClientLists:
    """The shipped lists, read once."""
    folder = resources.files("wowlab_core").joinpath(DATA_DIR)
    texts = [
        folder.joinpath(name).read_text(encoding="utf-8")
        for name in (SLASH_FILE, BINDING_FILE, LIMIT_FILE)
    ]
    return parse(*texts)


def slash_command(text: str) -> SlashCommand | None:
    """The client's entry for a `/command` as typed, or None."""
    return lists().slash.get(text.casefold())


def binding_command(name: str) -> BindingCommand | None:
    """The client's key-binding command of that name, or None."""
    return lists().bindings.get(name)


def encoding_limits() -> dict[str, int]:
    """The documented `C_EncodingUtil` constants, by name."""
    return dict(lists().limits)
