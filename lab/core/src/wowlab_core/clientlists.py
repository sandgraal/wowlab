"""clientlists: the client's slash commands, key-binding commands and encoding limits (M22-18).

Three generated files ship inside the package, under `client_lists/`:
`slash-command.txt`, `binding-command.txt` and `encoding-limit.txt`. They are
written by hand, at development time, by `scripts/forever_api.py` from one
pinned commit of Blizzard's interface code (Gethe/wow-ui-source) and the
client's strings (Ketho/BlizzardInterfaceResources), and hold names and
short English labels only; that script's docstring says what each line
means and how it was read. Nothing here opens a file outside the package,
reads an install, or goes on the network.

The builds the lists were read at are in each file's header (`ui-build` for
the interface code, `strings-build` for the strings) and are read from
there, never written here (L6); `source_words()` names both. The lists judge
only the client they were read from: `mismatch(client)` compares the opened
flavor's build (`Client.build`, from `.build.info`, which the caller
discovered) with `ui-build` at major.minor.patch, and a caller that gets a
reason back says "cannot judge" rather than "not the game's".

- `slash_command(text, language)`: the client's entry for a `/command` as
  typed (case does not matter, as in the client, which upper-cases a
  command), or None. With a language (the install's `textLocale`), only
  that language's texts and English ones count; without one, every
  language's (the union), and the words built from a None say so.
  Not applied **[verify]**: the game-mode filter of the client's
  slash-command code, and whether every file that registers a command loads
  on Forever (`scripts/forever_api.py`).
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
    "ANY_LANGUAGE",
    "BINDING_FILE",
    "DATA_DIR",
    "ENGLISH",
    "LIMIT_FILE",
    "SLASH_FILE",
    "BindingCommand",
    "Client",
    "ClientLists",
    "ClientListsError",
    "SlashCommand",
    "SlashSource",
    "binding_command",
    "encoding_limits",
    "languages_words",
    "lists",
    "mismatch",
    "parse",
    "slash_command",
    "source_words",
]

DATA_DIR = "client_lists"
SLASH_FILE = "slash-command.txt"
BINDING_FILE = "binding-command.txt"
LIMIT_FILE = "encoding-limit.txt"
ANY_LANGUAGE = "*"
ENGLISH = "enUS"

SlashSource = Literal["registered", "named", "emote", "channel"]
_SOURCES: tuple[SlashSource, ...] = ("registered", "named", "emote", "channel")
_HEADER = re.compile(r"# ([a-z-]+): (\S+)")
_COMMAND_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_LANGUAGE = re.compile(r"[a-z]{2}[A-Z]{2}")


class ClientListsError(ValueError):
    """A shipped list is not what the generator writes: a packaging fault."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SlashCommand(_Model):
    """One slash command text the client knows. `source`: `registered` (the
    client's slash-command code registers its key), `named` (a client string
    names it; registered elsewhere, or a chat type), `emote`, or `channel`
    (`/1` and up, a chat channel's number). `key` is the client's own name
    for the command (`CAST`), the emote's token, or `CHANNEL`. `languages`:
    the client languages whose strings name it; None for every one."""

    text: str
    source: SlashSource
    key: str
    languages: tuple[str, ...] | None


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
    """The three lists as shipped, with the builds they were read at."""

    ui_build: str
    ui_commit: str
    strings_build: str
    slash: dict[str, list[SlashCommand]]  # by the text, case-folded; file order
    bindings: dict[str, BindingCommand]  # by the command name
    limits: dict[str, int]


class Client(_Model):
    """What the opened install says about the client the lists would judge:
    the flavor's build (`.build.info`'s Version; None when it has none) and
    its text language (`Config.wtf`'s `textLocale`; None when not set)."""

    build: str | None
    language: str | None = None


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


def _languages(file: str, text: str, said: str) -> tuple[str, ...] | None:
    if said == ANY_LANGUAGE:
        return None
    parts = tuple(said.split(","))
    if any(_LANGUAGE.fullmatch(part) is None for part in parts):
        raise ClientListsError(f"{file}: {text!r} has the languages {said!r}")
    return parts


def parse(slash_text: str, binding_text: str, limit_text: str) -> ClientLists:
    """The three files' text as one `ClientLists`. Raises `ClientListsError`."""
    heads: list[dict[str, str]] = []
    header, rows = _rows(SLASH_FILE, slash_text, 4)
    heads.append(header)
    slash: dict[str, list[SlashCommand]] = {}
    for text, source, key, said in rows:
        if not text.startswith("/") or source not in _SOURCES:
            raise ClientListsError(f"{SLASH_FILE}: {text!r} {source!r} is not a slash command")
        entry = SlashCommand.model_validate(
            {
                "text": text,
                "source": source,
                "key": key,
                "languages": _languages(SLASH_FILE, text, said),
            }
        )
        slash.setdefault(text.casefold(), []).append(entry)
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


def source_words(read: ClientLists | None = None) -> str:
    """The two builds the lists were read at, in words: "interface code
    <ui-build>, strings <strings-build>"."""
    read = read or lists()
    return f"interface code {read.ui_build}, strings {read.strings_build}"


def _patch(build: str) -> tuple[str, ...]:
    return tuple(build.split(".")[:3])


def mismatch(client: Client | None, read: ClientLists | None = None) -> str | None:
    """None when the lists may judge `client` (no client given, or its build
    has the lists' major.minor.patch); otherwise why not, naming both builds."""
    if client is None:
        return None
    read = read or lists()
    if client.build is not None and _patch(client.build) == _patch(read.ui_build):
        return None
    this = f"this client is {client.build}" if client.build else "this client's build is not known"
    return f"the game's command lists were read at {source_words(read)}, and {this}"


def _speaks(entry: SlashCommand, language: str) -> bool:
    return entry.languages is None or language in entry.languages or ENGLISH in entry.languages


def slash_command(text: str, language: str | None = None) -> SlashCommand | None:
    """The client's entry for a `/command` as typed, or None. With
    `language`, only that language's and English texts count; one text
    that stands for different commands in different languages gives the
    entry of `language`, then English, then the first."""
    found = lists().slash.get(text.casefold(), [])
    if language is not None:
        found = [e for e in found if _speaks(e, language)]
        mine = [e for e in found if e.languages is not None and language in e.languages]
        picked = mine or found
        return picked[0] if picked else None
    english = [e for e in found if e.languages is None or ENGLISH in e.languages]
    picked = english or found
    return picked[0] if picked else None


def languages_words(language: str | None) -> str:
    """Which client languages `slash_command` looked in, in words."""
    if language is None:
        return "in any client language, this install's not being known"
    if language == ENGLISH:
        return "in English, this install's language"
    return f"in English or this install's language, {language}"


def binding_command(name: str) -> BindingCommand | None:
    """The client's key-binding command of that name, or None."""
    return lists().bindings.get(name)


def encoding_limits() -> dict[str, int]:
    """The documented `C_EncodingUtil` constants, by name."""
    return dict(lists().limits)
