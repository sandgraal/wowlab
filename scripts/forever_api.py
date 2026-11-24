#!/usr/bin/env python3
"""Regenerate the lab-addon's API reference for the Forever client (M21-09).

The list `lab/addon/README.md` cites, the four files of `lab/addon/forever_api/`
(`global.txt`, `global-load-on-demand.txt`, `event.txt`, `widget-method.txt`;
one file would be within a few KB of the repository's 512 KB cap), is
generated here from six files of Ketho/BlizzardInterfaceResources at one
pinned commit of its `forever` branch: GlobalAPI, FrameXML, Frames, LuaEnum,
Events and WidgetAPI (the idea from mparke/forever-addons, which built luacheck
standards from the same six; nothing of that GPL project is copied). Three
steps, the first by hand and never in CI or a test (ADR-0012):

    uv run python scripts/forever_api.py fetch DIR     # network: the six files into DIR
    uv run python scripts/forever_api.py generate DIR  # offline: DIR -> lab/addon/forever_api/
    uv run python scripts/forever_api.py check         # offline: `make lint-lua` runs it

Keep DIR outside the repository (a scratch folder): Ketho's files are not
committed, only the names read from them.

`fetch` downloads each file from the pinned commit and refuses one whose
SHA-256 is not the pin below, so `generate` always reads the same bytes. To
move to a newer commit, change `COMMIT`, `BUILD` and the six pins together
(each digest is printed by a `fetch` that refuses), run the three steps, and
update the README's Baseline column wherever `check` says it disagrees.

How the Lua files are read: as data, never run (L3). Each is a Lua chunk of
table literals: `local NAME = { ... }` statements and a closing
`return NAME` or `return {NAME, ...}`, and LuaEnum's top-level `NAME = ...`
assignments. A narrow pass blanks the word `local` at the start of a line
(so the statement is the assignment the project's own SavedVariables parser,
`wowlab_core.luadata`, reads) and blanks the closing `return` line after
checking it names exactly the locals the file declared. Blanking keeps every
byte offset, so a `luadata` error points at the real line and column.
Anything else in the file (a call, a function, an operator) is refused by
`luadata` itself. Then each file's shape is checked and every name must be a
plain Lua name, dotted at most for a namespace.

`check` reads the generated list, `lab/addon/wow_client.yml` (the selene
standard `make lint-lua` uses) and the README's API table. For each client
global the addon declares, the README's Baseline cell must start with `yes`
when the list has the name and `no` when it does not. And the README must
name the commit and build the list was generated from. It fails with every
disagreement listed, so a regenerated list that drops or adds a name the
addon uses stops the lint until the README says so.

The client's lists (M22-18). The same three steps also write the three files
of `lab/core/src/wowlab_core/client_lists/`, which the library reads at run
time (`wowlab_core.clientlists`): the macros view marks a `/command` that is
not one of the client's, and the keys view names each key-binding command.

- `slash-command.txt`: every slash command text the client's own interface
  knows. The commands are registered in Gethe/wow-ui-source's
  `Blizzard_ChatFrameBase` (`Shared/SlashCommands.lua` and the `Mainline`
  family's `SlashCommandsOverrides.lua`) under a key such as `CAST`; the
  texts a player types are the global strings `SLASH_<KEY><n>`, looked up
  by `Shared/SlashCommandsRegistry.lua` (and by the chat frame for chat
  types). Those strings, and the emotes' `EMOTE<n>_CMD<m>`, are not in
  wow-ui-source: they come from Ketho's `GlobalStrings/<locale>.lua` at
  the commit pinned above, in every language Ketho has (`itIT` is
  byte-identical to `enUS` there and is not fetched), so the list is the
  union of every client language's texts, each line saying which languages
  name it (the library narrows to the install's `textLocale` and English).
  Each line is `text<TAB>source<TAB>key<TAB>languages`, the source being `registered` (a client string
  names it and one of the two files registers its key), `named` (a client
  string names it; neither file registers the key: a chat type, or a command
  another file registers), `emote` or `channel` (`/1` to
  `Constants.ChatFrameConstants.MaxChatChannels`, from the API
  documentation), the languages a comma list of codes or `*` (every one).
  Not applied, **[verify]**: `SlashCommands.lua`'s game-mode filter
  (`COMMANDS_BY_GAME_MODE`; which mode Forever runs is not read), and
  whether each file that registers a `named` command loads on Forever.
- `binding-command.txt`: every key-binding command of the client's own
  interface, from `Blizzard_FrameXML/Bindings_Camelot.xml` (Forever's game
  type is `camelot`: the `[Game]` folders the TOC files load for it are
  named `Camelot`, and the owner's record of 70888 holds
  `TOGGLELEGACYSYSTEM`, which only that file of the five defines) and every
  Blizzard addon's `Bindings.xml`. Each line is
  `name<TAB>category<TAB>category label<TAB>label<TAB>file`, the two labels
  the English text of the client's strings (`BINDING_NAME_<name>`, and the
  category's global string), empty when there is none.
  `Blizzard_SettingsDefinitions_Frame/Keybindings.xml`, which the ticket
  named for the headers, is read and holds no binding (frame templates
  only); the headers are each binding's `category`.
- `encoding-limit.txt`: the documented `C_EncodingUtil` limits
  (`EncodingUtilConstantsDocumentation.lua`), `name<TAB>value<TAB>type`.

Read as data, never run (L3): the API documentation files go through
`wowlab_core.luadata` (their opening `local NAME =` and closing
`APIDocumentation:AddDocumentationTable(NAME);` blanked, as above); the XML
and the other Lua files are read by narrow line recognizers, and every line
those recognizers expected to read and could not is listed in the
generated file's `# not read:` lines, with the other things left out.

`check` also reads the three files: each must carry the generated mark,
name the commits and builds pinned below, and hold only well-formed lines.

Standard library only, apart from `wowlab_core.luadata` for `generate`
(imported there, so `fetch` and `check` need nothing but Python).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "lab" / "addon"
OUT = ADDON / "forever_api"
STD = ADDON / "wow_client.yml"
README = ADDON / "README.md"

REPOSITORY = "https://github.com/Ketho/BlizzardInterfaceResources"
BRANCH = "forever"
COMMIT = "e230342a476eb4ef484ba7b34adc0da6ecc6acc1"
# The commit's own message ("1.60.1 (70938)"), with the 1.60.1 patch.
BUILD = "1.60.1.70938"
RAW_URL = (
    "https://raw.githubusercontent.com/Ketho/BlizzardInterfaceResources/{commit}/Resources/{name}"
)

# SHA-256 of each file at COMMIT (from the download of 2026-11-23), written as
# `sha256sum` prints them: a `"...API.lua": "<hex>"` literal reads as an API key
# to the secret scanner.
PINS: dict[str, str] = {
    name: digest
    for digest, name in (
        line.split("  ")
        for line in """
9d6c48d345417e51fab30c471ca8398d4c934af69b5ace33f26f0237fd209f4a  GlobalAPI.lua
6b40eb9f9b243155d2a6e3a8929aacabcbfc9674ebc8e21b15fa490b9fbc1b66  FrameXML.lua
7defd4ffde87cfa96d98a2a125e34a6e9d76fb977306dc4fd661e1f3ea38f06f  Frames.lua
ad1640d33555be9bd4911fb9344502618859db43bb836636349491d5fc76863f  LuaEnum.lua
0414ad92ad3bcea637f055e251d4794eb44011e92db6788f994567c01dbe9af7  Events.lua
227a8707d5a9d44ff1b057fc2ff3f9e663ae7dab31387ee54bed44349a29347f  WidgetAPI.lua
""".strip().splitlines()
    )
}
MAX_FILE_BYTES = 8 * 1024 * 1024

# Ketho's client strings at the same commit (M22-18), one file per language,
# under `Resources/GlobalStrings/`. `itIT.lua` is byte-identical to `enUS.lua`
# at that commit (both 7f7dd72e…), so it is not fetched.
STRING_PINS: dict[str, str] = {
    name: digest
    for digest, name in (
        line.split("  ")
        for line in """
c5cf4ae6cf71c3edd45bb23181cf62a8c78e77c583d1bdfee7f8dcedab9c7c62  deDE.lua
7f7dd72eb4ceb63be55d6bb950aa4c92454bc7910f10608917373e64deb75dad  enUS.lua
c2035be9fd13de3c8699d646b8559f1322771b09a4668c9bcc3b1aeb0ca8f313  esES.lua
c42b1faa0a50e77a61332ee4593cd1eb47e1dcab368e7fe14fdc0f3cb3266633  esMX.lua
dbe58013a2dc62a7228158f58e2ecdb53c5672b9d98411412eae1f98cd7d5b63  frFR.lua
a5f88e773389caa1c236b8f5842406e4f9e1edd46bbd85630e19ef11bd423fba  koKR.lua
5c09a3a105dc3af5e5c6ee481e3b658d357b7fb001512ba883f0c3c00cebf80c  ptBR.lua
c059e9acc4e2bf664fbae7846c09b42f3fb3ea05dc0dc05470e327845b8409b5  ruRU.lua
876d99257128355c29d01ad6c49733ca35ff4a924f95d65ed8f2dd88093d8ec7  zhCN.lua
40855f9a9bd86c7bc0f103c0e9a641dc60d57fa922430ed2b5fdb9aa9c9c833a  zhTW.lua
""".strip().splitlines()
    )
}
STRINGS_DIR = "GlobalStrings"
ENGLISH = "enUS.lua"

# Gethe/wow-ui-source, branch `forever`, at the commit whose message is
# "1.60.1 (71037)" (2026-11-20), the files the client's lists are read from.
UI_REPOSITORY = "https://github.com/Gethe/wow-ui-source"
UI_BRANCH = "forever"
UI_COMMIT = "734f065329cff4992835b01a84f210a297db91b6"
UI_BUILD = "1.60.1.71037"
UI_RAW_URL = "https://raw.githubusercontent.com/Gethe/wow-ui-source/{commit}/{path}"
UI_DIR = "wow-ui-source"
_ADDONS = "Interface/AddOns"
_CHAT = f"{_ADDONS}/Blizzard_ChatFrameBase"
_DOCS = f"{_ADDONS}/Blizzard_APIDocumentationGenerated"
VERSION_FILE = "version.txt"
SLASH_FILES = (f"{_CHAT}/Shared/SlashCommands.lua", f"{_CHAT}/Mainline/SlashCommandsOverrides.lua")
REGISTRY_FILE = f"{_CHAT}/Shared/SlashCommandsRegistry.lua"
CHAT_CONSTANTS_FILE = f"{_DOCS}/ChatConstantsDocumentation.lua"
ENCODING_FILE = f"{_DOCS}/EncodingUtilConstantsDocumentation.lua"
# Forever's bindings first: a command another file also defines keeps this
# file's category.
BINDING_FILES = (
    f"{_ADDONS}/Blizzard_FrameXML/Bindings_Camelot.xml",
    f"{_ADDONS}/Blizzard_Commentator/Bindings.xml",
    f"{_ADDONS}/Blizzard_Gamepad/Bindings.xml",
    f"{_ADDONS}/Blizzard_GamepadActionBars/Bindings.xml",
    f"{_ADDONS}/Blizzard_GamepadTargeting/Bindings.xml",
    f"{_ADDONS}/Blizzard_GlueXML/Bindings.xml",
    f"{_ADDONS}/Blizzard_GroupFinder_VanillaStyle/Bindings.xml",
    f"{_ADDONS}/Blizzard_PTRFeedback/Bindings.xml",
    f"{_ADDONS}/Blizzard_PingUI/Bindings.xml",
)
KEYBINDINGS_FILE = f"{_ADDONS}/Blizzard_SettingsDefinitions_Frame/Keybindings.xml"
UI_PINS: dict[str, str] = {
    path: digest
    for digest, path in (
        line.split("  ")
        for line in """
d0a5be6a80043834fd4bd7ae7d0b62c51c38942dc921893642adace10d78f3af  version.txt
8c59b81deb2d8ccf9cb9687dfacd7ff7e82d1a8a83f8414843a23b8d58819072  Interface/AddOns/Blizzard_ChatFrameBase/Shared/SlashCommands.lua
d20da3ee05765b662116b8c197fd1163646b25bdb7f4c068a019261f1327d86b  Interface/AddOns/Blizzard_ChatFrameBase/Mainline/SlashCommandsOverrides.lua
422d7f47d599c98bec963c9ceccd42c739e31bf45a141ab243b5ad267da190ad  Interface/AddOns/Blizzard_ChatFrameBase/Shared/SlashCommandsRegistry.lua
054dbce92640323faefa4f187526a9ce1b62f3e4a4ec7b3e2615e947a231f69e  Interface/AddOns/Blizzard_APIDocumentationGenerated/ChatConstantsDocumentation.lua
48c6026803adadcf9461a5879d89fa47a26fa568a69b65b7660b06c0af1890d6  Interface/AddOns/Blizzard_APIDocumentationGenerated/EncodingUtilConstantsDocumentation.lua
8e7077066d7a91159593c6dde87a98603c9a5c0875a6a4b19f755311fcaa05c1  Interface/AddOns/Blizzard_FrameXML/Bindings_Camelot.xml
0044e6a9881e3a151b4def1e746e930d80352cfc48752c75fa19bd7378bb7b2c  Interface/AddOns/Blizzard_Commentator/Bindings.xml
c9c0792adb753ade847b4622f5583b7db4194fd6c0f57b8cb4208635ca11e770  Interface/AddOns/Blizzard_Gamepad/Bindings.xml
ba115fd6f11488a8905b54e250708afb6d64e7778fa3cf200389bed0d2c6a748  Interface/AddOns/Blizzard_GamepadActionBars/Bindings.xml
c6b3f3d7834f8b901048c3b1fd809f4bc11ade99668666f19e816817cbaf92f4  Interface/AddOns/Blizzard_GamepadTargeting/Bindings.xml
852f635e99cb54b81f08cf8c8bd53d4b245ed29db7b82ba3ae3c6303073caf74  Interface/AddOns/Blizzard_GlueXML/Bindings.xml
113fe1271235d46ac617411d693b8e4258bba4fad856fd1fec8ead71377799ff  Interface/AddOns/Blizzard_GroupFinder_VanillaStyle/Bindings.xml
06e31ca95020579a5439348c16f24b53d27e185eecb4f4522b7bdb8e0a9f08fb  Interface/AddOns/Blizzard_PTRFeedback/Bindings.xml
1ffa1bcfda853c9bedaf05a294cfcc296b4fd1713097c4a3223dc36d529bdeb3  Interface/AddOns/Blizzard_PingUI/Bindings.xml
7054d39a6a4de0713358574d4c93db7139b4d8f8ba4758551a90a2b303b53855  Interface/AddOns/Blizzard_SettingsDefinitions_Frame/Keybindings.xml
""".strip().splitlines()
    )
}

# The three files of the client's lists, inside the package (M22-18).
LISTS = ROOT / "lab" / "core" / "src" / "wowlab_core" / "client_lists"
SLASH_LIST = "slash-command.txt"
BINDING_LIST = "binding-command.txt"
LIMIT_LIST = "encoding-limit.txt"
LIST_FILES = (SLASH_LIST, BINDING_LIST, LIMIT_LIST)
SLASH_SOURCES = ("registered", "named", "emote", "channel")
ANY_LANGUAGE = "*"

GENERATED_MARK = "# GENERATED by scripts/forever_api.py; do not edit by hand."
# The addon's own globals (its SavedVariables and slash command) are the ones
# wow_client.yml lets it write whole; every other declared name is the client's.
OWN_PROPERTY = "full-write"

# The generated list: one file per section, `lab/addon/forever_api/<section>.txt`.
GLOBAL = "global"
GLOBAL_LOD = "global-load-on-demand"
EVENT = "event"
WIDGET_METHOD = "widget-method"
SECTIONS = (GLOBAL, GLOBAL_LOD, EVENT, WIDGET_METHOD)

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_DOTTED = re.compile(rf"{_NAME}(?:\.{_NAME})*")
_PLAIN = re.compile(_NAME)
_EVENT = re.compile(r"[A-Z][A-Z0-9_]*")
_LOCAL = re.compile(rb"local([ \t]+)(" + _NAME.encode() + rb")[ \t]*=")
_RETURN = re.compile(
    rb"return[ \t]+(?:\{[ \t]*("
    + _NAME.encode()
    + rb"(?:[ \t]*,[ \t]*"
    + _NAME.encode()
    + rb")*)[ \t]*\}|("
    + _NAME.encode()
    + rb"))[ \t]*"
)


class ApiError(ValueError):
    """A Ketho file, the generated list or the README is not what this expects."""


# --- reading Ketho's files as data ---------------------------------------------


def read_chunk(name: str, data: bytes) -> dict[str, object]:
    """One Ketho resource file as plain Python values, read as data (L3).

    `local NAME =` at the start of a line loses its `local`; a closing
    `return` line must name exactly the declared locals (in order) and is
    blanked; both are replaced by spaces, so offsets do not move. The rest
    goes to `wowlab_core.luadata`, which refuses anything but literals.
    """
    from wowlab_core import luadata

    out = bytearray(data)
    declared: list[str] = []
    returned: list[str] | None = None
    offset = 0
    for line in data.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        if returned is not None and body.strip():
            raise ApiError(f"{name}: a statement after the closing return")
        if (match := _LOCAL.match(body)) is not None:
            declared.append(match.group(2).decode("ascii"))
            out[offset : offset + 5] = b"     "
        elif (match := _RETURN.fullmatch(body)) is not None:
            listed = match.group(1) or match.group(2)
            returned = [part.strip().decode("ascii") for part in listed.split(b",")]
            if returned != declared:
                raise ApiError(f"{name}: returns {returned}, declared {declared}")
            out[offset : offset + len(body)] = b" " * len(body)
        elif body.startswith((b"local", b"return")):
            raise ApiError(f"{name}: a statement this reader does not take: {body[:60]!r}")
        offset += len(line)
    if declared and returned is None:
        raise ApiError(f"{name}: declares locals {declared} and returns none of them")
    try:
        return luadata.parse(bytes(out)).to_python()
    except luadata.LuaDataError as error:
        raise ApiError(f"{name}: {error}") from error


def _strings(name: str, where: str, value: object, pattern: re.Pattern[str]) -> list[str]:
    if value == {}:  # `luadata` reads an empty table as a dict
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ApiError(f"{name}: {where} is not a list of strings")
    for item in value:
        if pattern.fullmatch(item) is None:
            raise ApiError(f"{name}: {where} holds {item!r}, not a name")
    return list(value)


def _keys(name: str, value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise ApiError(f"{name}: top-level names {sorted(value)}, expected {sorted(expected)}")


def _table(name: str, where: str, value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ApiError(f"{name}: {where} is not a table with name keys")
    for key in value:
        if _PLAIN.fullmatch(key) is None:
            raise ApiError(f"{name}: {where} has the key {key!r}, not a name")
    return value


def collect(files: Mapping[str, bytes]) -> dict[str, set[str]]:
    """The four sections of the list from the six files' bytes."""
    sections: dict[str, set[str]] = {section: set() for section in SECTIONS}

    api = read_chunk("GlobalAPI.lua", files["GlobalAPI.lua"])
    _keys("GlobalAPI.lua", api, {"GlobalAPI", "LuaAPI"})
    for key in ("GlobalAPI", "LuaAPI"):
        sections[GLOBAL].update(_strings("GlobalAPI.lua", key, api[key], _DOTTED))

    for file, first in (("FrameXML.lua", "FrameXML"), ("Frames.lua", "Frames")):
        chunk = read_chunk(file, files[file])
        _keys(file, chunk, {first, "LoadOnDemand"})
        sections[GLOBAL].update(_strings(file, first, chunk[first], _DOTTED))
        sections[GLOBAL_LOD].update(_strings(file, "LoadOnDemand", chunk["LoadOnDemand"], _DOTTED))

    enums = read_chunk("LuaEnum.lua", files["LuaEnum.lua"])
    for key, value in enums.items():
        if _PLAIN.fullmatch(key) is None:
            raise ApiError(f"LuaEnum.lua: the top-level name {key!r} is not a name")
        sections[GLOBAL].add(key)
        if key in ("Enum", "Constants"):
            for member, inner in _table("LuaEnum.lua", key, value).items():
                _table("LuaEnum.lua", f"{key}.{member}", inner)
                sections[GLOBAL].add(f"{key}.{member}")
        elif not isinstance(value, int) or isinstance(value, bool):
            raise ApiError(f"LuaEnum.lua: {key} is not a whole number")

    events = read_chunk("Events.lua", files["Events.lua"])
    _keys("Events.lua", events, {"Events"})
    for system, names in _table("Events.lua", "Events", events["Events"]).items():
        sections[EVENT].update(_strings("Events.lua", f"Events.{system}", names, _EVENT))

    widgets = read_chunk("WidgetAPI.lua", files["WidgetAPI.lua"])
    _keys("WidgetAPI.lua", widgets, {"WidgetAPI"})
    for widget, spec in _table("WidgetAPI.lua", "WidgetAPI", widgets["WidgetAPI"]).items():
        fields = _table("WidgetAPI.lua", widget, spec)
        if not {"inherits", "methods"} <= set(fields) <= {"inherits", "methods", "handlers"}:
            raise ApiError(f"WidgetAPI.lua: {widget} has the fields {sorted(fields)}")
        for method in _strings("WidgetAPI.lua", f"{widget}.methods", fields["methods"], _PLAIN):
            sections[WIDGET_METHOD].add(f"{widget}:{method}")

    sections[GLOBAL_LOD] -= sections[GLOBAL]
    return sections


_ABOUT = {
    GLOBAL: (
        "Functions (GlobalAPI and LuaAPI), FrameXML globals, frames, the Enum and",
        "Constants tables and each of their tables, and LuaEnum's other constants.",
    ),
    GLOBAL_LOD: (
        "Globals defined only once a load-on-demand Blizzard addon has loaded",
        "(FrameXML's and Frames' LoadOnDemand lists, less any also in global.txt).",
    ),
    EVENT: ("Event names, every system's together.",),
    WIDGET_METHOD: ("Widget methods as Widget:Method, each widget's own (not inherited).",),
}


def render(sections: Mapping[str, set[str]], digests: Mapping[str, str]) -> dict[str, str]:
    """File name -> text, one file per section, each under the same header."""
    header = [
        GENERATED_MARK,
        "# Regenerate: scripts/forever_api.py fetch DIR, then generate DIR (its docstring).",
        f"# repository: {REPOSITORY}",
        f"# branch: {BRANCH}",
        f"# commit: {COMMIT}",
        f"# build: {BUILD}",
    ]
    header += [f"# sha256: {digests[file]}  Resources/{file}" for file in sorted(digests)]
    header += [
        "#",
        "# A name here is in Ketho's dump of the client at that build: the client knows",
        "# the name. Not what a call returns, nor that an addon may call it on Forever",
        "# (a restricted call is listed too). One name per line, sorted.",
    ]
    out = {}
    for section in SECTIONS:
        lines = [*header, "#", *(f"# {line}" for line in _ABOUT[section]), ""]
        out[f"{section}.txt"] = "\n".join([*lines, *sorted(sections[section])]) + "\n"
    return out


def generate(source: Path, pins: Mapping[str, str] = PINS) -> dict[str, str]:
    files: dict[str, bytes] = {}
    for file, expected in pins.items():
        path = source / file
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ApiError(f"{file}: larger than {MAX_FILE_BYTES} bytes")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ApiError(f"{file}: SHA-256 {actual}, pinned {expected}")
        files[file] = data
    return render(collect(files), dict(pins))


# --- the client's lists (M22-18) ----------------------------------------------------

_STRING_LINE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*) = "((?:[^"\\\n]|\\.)*)";')
_INDEXED_LINE = re.compile(r'_G\["(?:[^"\\\n]|\\.)*"\] = "(?:[^"\\\n]|\\.)*";')
_ESCAPE = re.compile(r"\\(\d{1,3}|.)", re.DOTALL)
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}
_SLASH_NAME = re.compile(r"SLASH_([A-Z0-9_]+?)(\d+)")
_EMOTE_NAME = re.compile(r"EMOTE(\d+)_CMD(\d+)")
_KEY = re.compile(r"[A-Za-z0-9_]+")
_COMMAND_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_NAME_RE = re.compile(_NAME)
_REGISTRY_LOOKUP = re.compile(r'_G\["SLASH_"\s*\.\.\s*\w+\s*\.\.\s*\w+\]')
_TABLE_OPEN = "SLASH_COMMAND = {"
_TABLE_ENTRY = re.compile(r'\t([A-Z0-9_]+) = "([A-Z0-9_]+)",')
_TABLE_ADD = re.compile(r'\s*SLASH_COMMAND\.([A-Z0-9_]+) = "([A-Z0-9_]+)";')
_REGISTER = re.compile(
    r"\s*SlashCommandUtil\.CheckAdd(?:Secure)?SlashCommand\(SLASH_COMMAND\.([A-Z0-9_]+),"
)
_SOURCE_SLASH = re.compile(r'\s*(SLASH_[A-Z0-9_]+) = "((?:[^"\\\n]|\\.)*)";')
_MAX_CHANNELS = re.compile(r'\s*\{ Name = "MaxChatChannels", Type = "number", Value = (\d+) \},')
_DOC_CLOSE = re.compile(
    rb"APIDocumentation:AddDocumentationTable\((" + _NAME.encode() + rb")\);[ \t]*"
)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG = re.compile(r"<([A-Za-z]+)")
_BINDING_TAG = re.compile(r"<Binding\s([^>]*)>")
_ATTRIBUTE = re.compile(r'\s*([A-Za-z]+)="([^"&<>]*)"')
_PRIORITY = {
    source: rank for rank, source in enumerate(("registered", "named", "emote", "channel"))
}


def _decode(name: str, data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ApiError(f"{name}: not UTF-8 ({error})") from error


def unescape(body: str) -> str | None:
    """A Lua string literal's body as text, or None when it holds an escape
    this reader does not take (a byte escape above 127, `\\z`, `\\x`, ...)."""
    bad = False

    def one(match: re.Match[str]) -> str:
        nonlocal bad
        escape = match.group(1)
        if escape.isdigit():
            if int(escape) > 127:
                bad = True
                return ""
            return chr(int(escape))
        if escape not in _SIMPLE_ESCAPES:
            bad = True
            return ""
        return _SIMPLE_ESCAPES[escape]

    text = _ESCAPE.sub(one, body)
    return None if bad else text


def read_strings(name: str, data: bytes) -> tuple[dict[str, str], int]:
    """A GlobalStrings file's plain `NAME = "text";` lines (the bodies as
    written, escapes kept) and how many other lines were not read. `_G["…"]`
    lines name globals that are not Lua names; none is a name read here."""
    strings: dict[str, str] = {}
    unread = 0
    for line in _decode(name, data).splitlines():
        if not line.strip() or _INDEXED_LINE.fullmatch(line):
            continue
        if (match := _STRING_LINE.fullmatch(line)) is None:
            unread += 1
            continue
        strings[match.group(1)] = match.group(2)
    return strings, unread


def read_documentation(name: str, data: bytes) -> dict[str, object]:
    """A generated API documentation file as plain Python values, read as data
    (L3): its opening `local NAME =` and its closing
    `APIDocumentation:AddDocumentationTable(NAME);` are blanked (offsets kept),
    and the rest goes to `wowlab_core.luadata`, which refuses anything but
    literals."""
    from wowlab_core import luadata

    out = bytearray(data)
    declared: str | None = None
    closed = False
    offset = 0
    for line in data.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        if closed and body.strip():
            raise ApiError(f"{name}: a statement after the closing call")
        if declared is None and (match := _LOCAL.match(body)) is not None:
            declared = match.group(2).decode("ascii")
            out[offset : offset + 5] = b"     "
        elif (match := _DOC_CLOSE.fullmatch(body)) is not None:
            if match.group(1).decode("ascii") != declared:
                raise ApiError(f"{name}: the closing call names {match.group(1)!r}, not {declared}")
            out[offset : offset + len(body)] = b" " * len(body)
            closed = True
        elif body == b"};":  # the table's closing brace ends the statement with `;`
            out[offset + 1] = ord(" ")
        elif body.startswith((b"local", b"APIDocumentation", b"return")):
            raise ApiError(f"{name}: a statement this reader does not take: {body[:60]!r}")
        offset += len(line)
    if declared is None or not closed:
        raise ApiError(f"{name}: not one local table and its closing documentation call")
    try:
        return luadata.parse(bytes(out)).to_python()
    except luadata.LuaDataError as error:
        raise ApiError(f"{name}: {error}") from error


class Slash:
    """What `collect_slash` read: (text, source, key) -> the client languages
    whose strings name it (`*`: every language, a text not in the strings),
    and what it left out. One text may stand for different commands in
    different languages (`/em` is the emote chat type in English and another
    language's battle-pet command), so each is its own entry."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], set[str]] = {}
        self.languages: set[str] = set()
        self.not_read: list[str] = []

    def add(self, text: str, source: str, key: str, language: str) -> None:
        self.entries.setdefault((text, source, key), set()).add(language)

    def lines(self) -> list[str]:
        """`text<TAB>source<TAB>key<TAB>languages`, sorted by text, source
        rank and key; `*` for a text every language (or no string) names."""
        out = []
        ordered = sorted(self.entries.items(), key=lambda e: (e[0][0], _PRIORITY[e[0][1]], e[0][2]))
        for (text, source, key), languages in ordered:
            every = ANY_LANGUAGE in languages or languages >= self.languages
            said = ANY_LANGUAGE if every else ",".join(sorted(languages))
            out.append(f"{text}\t{source}\t{key}\t{said}")
        return out


def _command_table(name: str, text: str, not_read: list[str]) -> dict[str, str]:
    """The `SLASH_COMMAND` table literal (constant -> command key) and any
    later `SLASH_COMMAND.X = "X";` line."""
    lines = text.splitlines()
    try:
        start = lines.index(_TABLE_OPEN)
    except ValueError:
        raise ApiError(f"{name}: no `{_TABLE_OPEN}` line") from None
    table: dict[str, str] = {}
    for number, line in enumerate(lines[start + 1 :], start + 2):
        if line == "};":
            break
        if (match := _TABLE_ENTRY.fullmatch(line)) is not None:
            table[match.group(1)] = match.group(2)
        elif line.strip() and not line.strip().startswith("--"):
            not_read.append(f"{name} line {number}: {line.strip()[:60]}")
    else:
        raise ApiError(f"{name}: the SLASH_COMMAND table does not close")
    for line in lines:
        if (match := _TABLE_ADD.fullmatch(line)) is not None:
            table[match.group(1)] = match.group(2)
    return table


def collect_slash(ui: Mapping[str, bytes], strings: Mapping[str, bytes]) -> Slash:
    """Every slash command text the client knows, from the pinned files."""
    slash = Slash()
    registry = _decode(REGISTRY_FILE, ui[REGISTRY_FILE])
    if _REGISTRY_LOOKUP.search(registry) is None:
        raise ApiError(f"{REGISTRY_FILE}: no longer looks a command up as SLASH_<KEY><n>")
    first = _decode(SLASH_FILES[0], ui[SLASH_FILES[0]])
    table = _command_table(SLASH_FILES[0], first, slash.not_read)
    registered: set[str] = set()
    named: dict[str, str] = {}  # in-source SLASH_<KEY><n> strings, read as the client's
    for file in SLASH_FILES:
        for number, line in enumerate(_decode(file, ui[file]).splitlines(), 1):
            if (match := _REGISTER.match(line)) is not None:
                key = table.get(match.group(1))
                if key is None:
                    slash.not_read.append(
                        f"{file} line {number}: registers SLASH_COMMAND.{match.group(1)}, "
                        "which the table does not hold"
                    )
                else:
                    registered.add(key)
            elif "CheckAdd" in line and "SlashCommand" in line:
                # The two functions' own definitions and the one forwarding
                # call (its key is a parameter) register nothing.
                if not line.startswith("function ") and "(commandKey," not in line:
                    slash.not_read.append(f"{file} line {number}: {line.strip()[:60]}")
            elif (match := _SOURCE_SLASH.fullmatch(line)) is not None:
                named[match.group(1)] = match.group(2)
    parameters = 0
    texts: set[str] = set()
    sets: list[tuple[str, str, dict[str, str]]] = []
    for file, data in sorted(strings.items()):
        found, unread = read_strings(file, data)
        if unread:
            slash.not_read.append(
                f'{STRINGS_DIR}/{file}: {unread} lines not of the form NAME = "text";'
            )
        language = file.removesuffix(".lua")
        slash.languages.add(language)
        sets.append((f"{STRINGS_DIR}/{file}", language, found))
    # The code's own SLASH_<KEY><n> strings are every language's.
    sets.append((SLASH_FILES[0], ANY_LANGUAGE, named))
    for where, language, found in sets:
        for name, body in sorted(found.items()):
            if (match := _SLASH_NAME.fullmatch(name)) is not None:
                source, key = (
                    ("registered" if match.group(1) in registered else "named"),
                    match.group(1),
                )
            elif (match := _EMOTE_NAME.fullmatch(name)) is not None:
                token = unescape(found.get(f"EMOTE{match.group(1)}_TOKEN", ""))
                key = token if token and _KEY.fullmatch(token) else f"EMOTE{match.group(1)}"
                source = "emote"
            else:
                continue
            text = unescape(body)
            if text is None:
                slash.not_read.append(f"{where}: {name}, an escape not read")
            elif not text.startswith("/"):
                parameters += 1
            elif re.search(r"\s", text) or len(text) < 2:
                slash.not_read.append(f"{where}: {name} = {text!r}, not one word")
            else:
                texts.add(key)
                slash.add(text, source, key, language)
    if parameters:
        slash.not_read.append(
            f"{parameters} strings named like a command whose text has no leading / "
            "(a command's parameters, such as the stopwatch's), left out"
        )
    for key in sorted(registered - texts):
        slash.not_read.append(f"{key}: registered, and no client string read names a text for it")
    channels = _decode(CHAT_CONSTANTS_FILE, ui[CHAT_CONSTANTS_FILE])
    found_max = [int(m.group(1)) for m in map(_MAX_CHANNELS.fullmatch, channels.splitlines()) if m]
    if len(found_max) != 1:
        raise ApiError(f"{CHAT_CONSTANTS_FILE}: not one MaxChatChannels line")
    for channel in range(1, found_max[0] + 1):
        slash.add(f"/{channel}", "channel", "CHANNEL", ANY_LANGUAGE)
    return slash


class BindingRow:
    """One key-binding command, as `collect_bindings` read it."""

    def __init__(self, name: str, category: str, category_label: str, label: str, file: str):
        self.name, self.category, self.file = name, category, file
        self.category_label, self.label = category_label, label


def _english(strings: Mapping[str, str], name: str) -> str:
    """An English client string as one line of text, or "" when there is none
    or it is not one line."""
    body = strings.get(name)
    text = unescape(body) if body is not None else None
    if text is None or any(ch < " " for ch in text):
        return ""
    return text


def collect_bindings(
    ui: Mapping[str, bytes], english: Mapping[str, str]
) -> tuple[list[BindingRow], list[str]]:
    """Every key-binding command of the pinned binding files, in file order
    (the first file to define a command keeps it), and what was left out."""
    rows: dict[str, BindingRow] = {}
    not_read: list[str] = []
    keybindings = _decode(KEYBINDINGS_FILE, ui[KEYBINDINGS_FILE])
    if "<Binding" in _COMMENT.sub("", keybindings):
        raise ApiError(f"{KEYBINDINGS_FILE}: now holds a Binding; read it as a bindings file")
    short = KEYBINDINGS_FILE.removeprefix(f"{_ADDONS}/")
    not_read.append(f"{short}: frame templates only, no binding")
    for file in BINDING_FILES:
        short = file.removeprefix(f"{_ADDONS}/")
        raw = _decode(file, ui[file])
        text = _COMMENT.sub(lambda match: " " * len(match.group(0)), raw)
        hidden = raw.count("<Binding ") - text.count("<Binding ")
        if hidden:
            not_read.append(f"{short}: {hidden} bindings inside a comment, left out")
        tags = set(_TAG.findall(text)) - {"Bindings", "Binding", "ModifiedClick"}
        if tags:
            raise ApiError(f"{file}: elements this reader does not take: {sorted(tags)}")
        clicks = text.count("<ModifiedClick")
        if clicks:
            not_read.append(f"{short}: {clicks} ModifiedClick lines (modified clicks, not keys)")
        for match in _BINDING_TAG.finditer(text):
            attributes = match.group(1).rstrip("/ \t\r\n")
            pairs = _ATTRIBUTE.findall(attributes)
            if "".join(f' {k}="{v}"' for k, v in pairs).split() != attributes.split():
                not_read.append(
                    f"{short}: a Binding whose attributes are not read: {attributes[:60]}"
                )
                continue
            values = dict(pairs)
            name = values.get("name", "")
            if _COMMAND_NAME.fullmatch(name) is None:
                not_read.append(f"{short}: a binding named {name!r}, not a plain command name")
                continue
            if name in rows:
                continue
            category = values.get("category", "")
            category_label = (
                _english(english, category) if _NAME_RE.fullmatch(category) else category
            )
            label = _english(english, f"BINDING_NAME_{name}")
            rows[name] = BindingRow(name, category, category_label, label, short)
    unlabelled = sum(1 for row in rows.values() if not row.label)
    if unlabelled:
        not_read.append(f"{unlabelled} commands with no English BINDING_NAME string (label empty)")
    return list(rows.values()), not_read


def collect_limits(ui: Mapping[str, bytes]) -> list[tuple[str, int, str]]:
    """The documented `C_EncodingUtil` constants, in file order."""
    doc = read_documentation(ENCODING_FILE, ui[ENCODING_FILE])
    if len(doc) != 1:
        raise ApiError(f"{ENCODING_FILE}: {sorted(doc)}, not one table")
    (top,) = doc.values()
    tables = _table(ENCODING_FILE, "the documentation", top).get("Tables")
    if not isinstance(tables, list):
        raise ApiError(f"{ENCODING_FILE}: no Tables list")
    out: list[tuple[str, int, str]] = []
    for table in tables:
        fields = _table(ENCODING_FILE, "a table", table)
        if fields.get("Type") != "Constants":
            continue
        for value in fields.get("Values") or []:
            entry = _table(ENCODING_FILE, "a constant", value)
            name, kind, number = entry.get("Name"), entry.get("Type"), entry.get("Value")
            if (
                not isinstance(name, str)
                or _PLAIN.fullmatch(name) is None
                or not isinstance(kind, str)
                or _PLAIN.fullmatch(kind) is None
                or not isinstance(number, int)
                or isinstance(number, bool)
            ):
                raise ApiError(f"{ENCODING_FILE}: a constant that is not a named whole number")
            out.append((name, number, kind))
    if not out:
        raise ApiError(f"{ENCODING_FILE}: no constant")
    return out


_LIST_ABOUT = {
    SLASH_LIST: (
        "Every slash command text the client's own interface knows, in every client",
        "language Ketho has: text<TAB>source<TAB>key<TAB>languages. source: registered",
        "(SlashCommands.lua or its Mainline override registers the key), named (a client",
        "string names it, those two files do not register it: a chat type, or a command",
        "another file registers), emote, channel (/1 to MaxChatChannels). languages: the",
        "client languages whose strings name it, * for every one. A text not here is not",
        "one of the client's: an addon's command, or a typo. Not applied [verify]: the",
        "game-mode filter (COMMANDS_BY_GAME_MODE in SlashCommands.lua), and whether the",
        "files that register the other commands load on Forever (whether the TOC's",
        "[AllowLoadGameType mainline] covers ClassTalentHelper.lua's commands, for one).",
    ),
    BINDING_LIST: (
        "Every key-binding command of the client's own interface:",
        "name<TAB>category<TAB>category label<TAB>label<TAB>file. The labels are the",
        "English client strings (BINDING_NAME_<name>, and the category's string), empty",
        "when there is none. A command not here is not the client's: an addon's.",
    ),
    LIMIT_LIST: ("The documented C_EncodingUtil constants: name<TAB>value<TAB>type.",),
}


def render_lists(
    slash: Slash,
    bindings: tuple[list[BindingRow], list[str]],
    limits: list[tuple[str, int, str]],
    digests: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    """File name -> text for the three client lists."""
    header = [
        GENERATED_MARK,
        "# Regenerate: scripts/forever_api.py fetch DIR, then generate DIR (its docstring).",
        f"# ui-repository: {UI_REPOSITORY}",
        f"# ui-branch: {UI_BRANCH}",
        f"# ui-commit: {UI_COMMIT}",
        f"# ui-build: {UI_BUILD}",
        f"# strings-repository: {REPOSITORY}",
        f"# strings-commit: {COMMIT}",
        f"# strings-build: {BUILD}",
    ]
    rows, binding_notes = bindings
    bodies = {
        SLASH_LIST: [*slash.lines()],
        BINDING_LIST: [
            "\t".join((r.name, r.category, r.category_label, r.label, r.file))
            for r in sorted(rows, key=lambda r: r.name)
        ],
        LIMIT_LIST: [f"{name}\t{value}\t{kind}" for name, value, kind in limits],
    }
    notes = {SLASH_LIST: slash.not_read, BINDING_LIST: binding_notes, LIMIT_LIST: []}
    out: dict[str, str] = {}
    for file in LIST_FILES:
        lines = list(header)
        lines += [f"# sha256: {d}  {p}" for p, d in sorted(digests[file].items())]
        lines += ["#", *(f"# {line}" for line in _LIST_ABOUT[file])]
        lines += [f"# not read: {note}" for note in notes[file]]
        out[file] = "\n".join([*lines, "", *bodies[file]]) + "\n"
    return out


def _pinned(folder: Path, pins: Mapping[str, str]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for file, expected in pins.items():
        path = folder / file
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ApiError(f"{file}: larger than {MAX_FILE_BYTES} bytes")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ApiError(f"{file}: SHA-256 {actual}, pinned {expected}")
        files[file] = data
    return files


def generate_lists(
    source: Path,
    ui_pins: Mapping[str, str] = UI_PINS,
    string_pins: Mapping[str, str] = STRING_PINS,
) -> dict[str, str]:
    """The three client lists from the pinned files under `source`
    (`wow-ui-source/<path>` and `GlobalStrings/<locale>.lua`)."""
    ui = _pinned(source / UI_DIR, ui_pins)
    strings = _pinned(source / STRINGS_DIR, string_pins)
    version = _decode(VERSION_FILE, ui[VERSION_FILE]).strip()
    if version != UI_BUILD:
        raise ApiError(f"{VERSION_FILE}: {version!r}, not the pinned build {UI_BUILD}")
    english, _ = read_strings(ENGLISH, strings[ENGLISH])
    slash_files = (*SLASH_FILES, REGISTRY_FILE, CHAT_CONSTANTS_FILE, VERSION_FILE)
    digests = {
        SLASH_LIST: {
            **{p: ui_pins[p] for p in slash_files},
            **{f"{STRINGS_DIR}/{f}": d for f, d in string_pins.items()},
        },
        BINDING_LIST: {
            **{p: ui_pins[p] for p in (*BINDING_FILES, KEYBINDINGS_FILE, VERSION_FILE)},
            f"{STRINGS_DIR}/{ENGLISH}": string_pins[ENGLISH],
        },
        LIMIT_LIST: {p: ui_pins[p] for p in (ENCODING_FILE, VERSION_FILE)},
    }
    return render_lists(
        collect_slash(ui, strings), collect_bindings(ui, english), collect_limits(ui), digests
    )


# --- fetching, by hand ------------------------------------------------------------


def _download(url: str, name: str, expected: str) -> bytes:
    print(f"forever_api: downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ApiError(f"{name}: larger than {MAX_FILE_BYTES} bytes")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ApiError(f"{name}: SHA-256 {actual}, pinned {expected}")
    return data


def fetch(target: Path) -> None:
    """Download every pinned file into `target`: Ketho's six files, its
    GlobalStrings under `GlobalStrings/`, and the wow-ui-source files under
    `wow-ui-source/`. Network; by hand only."""
    target.mkdir(parents=True, exist_ok=True)
    for file, expected in PINS.items():
        data = _download(RAW_URL.format(commit=COMMIT, name=file), file, expected)
        (target / file).write_bytes(data)
    for file, expected in STRING_PINS.items():
        name = f"{STRINGS_DIR}/{file}"
        data = _download(RAW_URL.format(commit=COMMIT, name=name), name, expected)
        (target / STRINGS_DIR).mkdir(exist_ok=True)
        (target / name).write_bytes(data)
    for path, expected in UI_PINS.items():
        data = _download(UI_RAW_URL.format(commit=UI_COMMIT, path=path), path, expected)
        (target / UI_DIR / path).parent.mkdir(parents=True, exist_ok=True)
        (target / UI_DIR / path).write_bytes(data)


# --- checking the README against the list -------------------------------------------


def parse_list(texts: Mapping[str, str]) -> tuple[dict[str, str], dict[str, set[str]]]:
    """The generated files' shared header fields and each file's names."""
    headers: list[dict[str, str]] = []
    sections: dict[str, set[str]] = {}
    for section in SECTIONS:
        file = f"{section}.txt"
        lines = texts[file].splitlines()
        if not lines or lines[0] != GENERATED_MARK:
            raise ApiError(f"{file}: the first line is not the generated mark")
        header: dict[str, str] = {}
        names: set[str] = set()
        for line in lines[1:]:
            if (match := re.fullmatch(r"# (commit|build): (\S+)", line)) is not None:
                header[match.group(1)] = match.group(2)
            elif line and not line.startswith("#"):
                names.add(line)
        if set(header) != {"commit", "build"}:
            raise ApiError(f"{file}: the header names {sorted(header)}, not commit and build")
        headers.append(header)
        sections[section] = names
    if any(header != headers[0] for header in headers):
        raise ApiError(f"{OUT.name}/: the files name different commits or builds")
    return headers[0], sections


def read_list(folder: Path = OUT) -> dict[str, str]:
    return {f"{s}.txt": (folder / f"{s}.txt").read_text(encoding="utf-8") for s in SECTIONS}


def std_globals(text: str) -> dict[str, str | None]:
    """The names under `globals:` in a selene standard (the same reading as
    tests/addon/test_lab_addon.py), each with its `property:` if it has one."""
    names: dict[str, str | None] = {}
    in_globals = False
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("globals:"):
            in_globals = True
        elif in_globals and (match := re.match(r"^  ([A-Za-z_][\w.]*):\s*$", line)):
            current = match.group(1)
            names[current] = None
        elif current is not None and (match := re.match(r"^    property:\s*(\S+)\s*$", line)):
            names[current] = match.group(1)
    return names


def client_globals(text: str) -> set[str]:
    """The client's names in a selene standard: all but the addon's own."""
    return {name for name, prop in std_globals(text).items() if prop != OWN_PROPERTY}


def readme_baselines(text: str) -> dict[str, str]:
    """API table rows (`| `name` | used for | status | baseline |`): name -> baseline."""
    rows: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\|\s*`([^`]+)`\s*\|(.*)\|\s*$", line)
        if match:
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", match.group(2))]
            rows[match.group(1)] = cells[-1]
    return rows


def check(list_texts: Mapping[str, str], std_text: str, readme_text: str) -> list[str]:
    header, sections = parse_list(list_texts)
    known = sections[GLOBAL] | sections[GLOBAL_LOD]
    # A namespace table (`C_Secrets`) is known when the list has a member of it.
    known |= {
        ".".join(parts[:end])
        for parts in (name.split(".") for name in known)
        for end in range(1, len(parts))
    }
    rows = readme_baselines(readme_text)
    problems: list[str] = []
    for name in sorted(client_globals(std_text)):
        baseline = rows.get(name)
        expected = "yes" if name in known else "no"
        if baseline is None:
            problems.append(f"{name}: no row in the README API table")
        elif not re.match(rf"{expected}\b", baseline):
            problems.append(
                f"{name}: README Baseline {baseline!r}; the list at {header['build']} says {expected}"
            )
    for field in ("commit", "build"):
        if header[field] not in readme_text:
            problems.append(f"README does not name the list's {field} {header[field]}")
    return problems


def read_lists(folder: Path = LISTS) -> dict[str, str]:
    return {file: (folder / file).read_text(encoding="utf-8") for file in LIST_FILES}


_LIST_HEADER = {
    "ui-commit": UI_COMMIT,
    "ui-build": UI_BUILD,
    "strings-commit": COMMIT,
    "strings-build": BUILD,
}
_LIST_COLUMNS = {SLASH_LIST: 4, BINDING_LIST: 5, LIMIT_LIST: 3}
_LANGUAGE = re.compile(r"[a-z]{2}[A-Z]{2}")


def _list_line_problem(file: str, cells: list[str]) -> str | None:
    if file == SLASH_LIST:
        text, source, key, languages = cells
        if languages != ANY_LANGUAGE and any(
            _LANGUAGE.fullmatch(part) is None for part in languages.split(",")
        ):
            return f"{text}: the languages {languages!r} are not language codes"
        if not text.startswith("/") or len(text) < 2 or re.search(r"\s", text):
            return f"{text!r} is not a slash command text"
        if source not in SLASH_SOURCES:
            return f"{text}: the source {source!r} is not one of {', '.join(SLASH_SOURCES)}"
        if _KEY.fullmatch(key) is None:
            return f"{text}: the key {key!r} is not a name"
    elif file == BINDING_LIST:
        if _COMMAND_NAME.fullmatch(cells[0]) is None:
            return f"{cells[0]!r} is not a command name"
        if any(ch < " " for cell in cells for ch in cell):
            return f"{cells[0]}: a control character"
    else:
        name, value, kind = cells
        if _PLAIN.fullmatch(name) is None or _PLAIN.fullmatch(kind) is None:
            return f"{name!r} is not a named constant"
        if re.fullmatch(r"-?\d+", value) is None:
            return f"{name}: {value!r} is not a whole number"
    return None


def check_lists(texts: Mapping[str, str]) -> list[str]:
    """The client lists as committed: the generated mark, the pinned commits
    and builds, and well-formed, sorted, unique lines."""
    problems: list[str] = []
    for file in LIST_FILES:
        lines = texts[file].splitlines()
        where = f"{LISTS.name}/{file}"
        if not lines or lines[0] != GENERATED_MARK:
            problems.append(f"{where}: the first line is not the generated mark")
            continue
        header = dict(
            match.groups()
            for match in map(re.compile(r"# ([a-z-]+): (\S+)").fullmatch, lines)
            if match is not None
        )
        for field, pinned in _LIST_HEADER.items():
            if header.get(field) != pinned:
                problems.append(f"{where}: {field} {header.get(field)!r}, pinned {pinned}")
        keys: list[str] = []
        for number, line in enumerate(lines, 1):
            if not line or line.startswith("#"):
                continue
            cells = line.split("\t")
            if len(cells) != _LIST_COLUMNS[file]:
                problems.append(f"{where} line {number}: {len(cells)} columns")
                continue
            problem = _list_line_problem(file, cells)
            if problem is not None:
                problems.append(f"{where} line {number}: {problem}")
            keys.append("\t".join(cells[:3]) if file == SLASH_LIST else cells[0])
        if not keys:
            problems.append(f"{where}: no line")
        elif file == SLASH_LIST:
            # One text may stand for several commands (one per language);
            # the texts are in order and each (text, source, key) is once.
            firsts = [key.split("\t")[0] for key in keys]
            if firsts != sorted(firsts) or len(set(keys)) != len(keys):
                problems.append(f"{where}: not sorted and unique")
        elif file != LIMIT_LIST and keys != sorted(set(keys)):
            problems.append(f"{where}: not sorted and unique")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = commands.add_parser("fetch", help="download the pinned files (network)")
    fetch_cmd.add_argument("dir", type=Path)
    generate_cmd = commands.add_parser(
        "generate", help=f"write {OUT.name}/ and {LISTS.name}/ from DIR"
    )
    generate_cmd.add_argument("dir", type=Path)
    commands.add_parser(
        "check", help="README Baseline column against the list; the client lists' shape"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            fetch(args.dir)
        elif args.command == "generate":
            texts = generate(args.dir)
            lists = generate_lists(args.dir)
            for folder, written in ((OUT, texts), (LISTS, lists)):
                folder.mkdir(exist_ok=True)
                for file, text in written.items():
                    (folder / file).write_text(text, encoding="utf-8", newline="\n")
                print(f"forever_api: wrote {folder.relative_to(ROOT)}/", file=sys.stderr)
        else:
            problems = check(
                read_list(),
                STD.read_text(encoding="utf-8"),
                README.read_text(encoding="utf-8"),
            ) + check_lists(read_lists())
            for problem in problems:
                print(f"forever_api: {problem}", file=sys.stderr)
            if problems:
                return 1
    except (ApiError, OSError) as error:
        print(f"forever_api: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
