"""No ticket id in a string the lab-addon stores or prints (M16-64).

Every string literal in the addon's Lua sources, and every `## ` directive
value of its TOC, is text the addon may store in `WowLab.lua` or print in
chat, where a player reads it. None may hold a ticket id (`M\\d\\d-\\d\\d`).
Comments may keep them: the lexer drops comments before this check runs.

Static only (L3): the sources are tokenized by the lexer in
`test_lab_addon.py`, loaded by path, and nothing runs Lua.

What it sees: a literal holding the id, and an id split over literals
joined directly by `..` (`"M11" .. "-03"`), which is folded into one text
before the check. What it does not see: an id built through anything but
adjacent literals (`"M11" .. sep .. "03"`, `string.format("M%d-%02d", ...)`,
`string.rep`, a table lookup); review catches those.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_lexer() -> ModuleType:
    path = Path(__file__).with_name("test_lab_addon.py")
    spec = importlib.util.spec_from_file_location("wowlab_m16_64_lab_addon_lexer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up
    spec.loader.exec_module(module)
    return module


_LEXER = _load_lexer()
ADDON: Path = _LEXER.ADDON
SOURCES: list[Path] = _LEXER.SOURCES
TOC: Path = _LEXER.TOC

TICKET_ID = re.compile(r"M\d\d-\d\d")


def _string_texts(src: str) -> list[tuple[int, str]]:
    """(line, text) of each string literal, with literals joined directly
    by `..` folded into one text (so `"M11" .. "-03"` reads `M11-03`)."""
    tokens = _LEXER.tokenize(src)
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.kind != "string":
            i += 1
            continue
        text, line = token.text, token.line
        while (
            i + 2 < len(tokens)
            and tokens[i + 1].kind == "op"
            and tokens[i + 1].text == ".."
            and tokens[i + 2].kind == "string"
        ):
            text += tokens[i + 2].text
            i += 2
        out.append((line, text))
        i += 1
    return out


def ticket_id_problems(name: str, src: str) -> list[str]:
    return [
        f"{name}:{line} holds a ticket id ({TICKET_ID.search(text).group(0)}): {text!r}"  # type: ignore[union-attr]
        for line, text in _string_texts(src)
        if TICKET_ID.search(text)
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_addon_string_holds_a_ticket_id(path: Path) -> None:
    problems = ticket_id_problems(path.name, path.read_text(encoding="utf-8"))
    assert not problems, problems


def test_no_toc_directive_holds_a_ticket_id() -> None:
    """A `## ` line is read by the client (Title and Notes show in its
    addon list); a `# ` line is a comment and may keep one."""
    lines = TOC.read_text(encoding="utf-8").splitlines()
    directives = [line for line in lines if line.startswith("## ")]
    assert any(line.startswith("## Notes:") for line in directives)
    bad = [line for line in directives if TICKET_ID.search(line)]
    assert not bad, bad


# Constructed sources (boundary inputs for this check, never the addon).
STORED = 'ns.Section({ key = "x", not_gathered = "not gathered: crashed once (M11-03)" })\n'
PRINTED = 'ns.Print("saved; see M20-17 for why")\n'
CONCATENATED = 'local reason = "crashed once (" .. "M11" .. "-03" .. ")"\n'
CONCATENATED_THROUGH_A_NAME = 'local d = "-"\nlocal reason = "M11" .. d .. "03"\n'
IN_A_COMMENT = '-- M11-03: why this section is absent\nlocal x = "plain words" -- M20-17\n'


@pytest.mark.parametrize(
    "src",
    [STORED, PRINTED, CONCATENATED],
    ids=["stored-constructed", "printed-constructed", "concatenated-constructed"],
)
def test_a_ticket_id_in_a_string_is_refused_constructed(src: str) -> None:
    problems = ticket_id_problems("Constructed.lua", src)
    assert len(problems) == 1, problems
    assert "Constructed.lua:1 holds a ticket id (M" in problems[0]


def test_comments_may_keep_ticket_ids_constructed() -> None:
    assert ticket_id_problems("Constructed.lua", IN_A_COMMENT) == []


def test_an_id_built_through_a_name_is_not_seen_constructed() -> None:
    """The documented gap: only literals joined directly are folded."""
    assert ticket_id_problems("Constructed.lua", CONCATENATED_THROUGH_A_NAME) == []


def test_every_real_source_is_scanned() -> None:
    assert ADDON.is_dir() and len(SOURCES) >= 10
    assert {p.name for p in SOURCES} >= {"Collections.lua", "Core.lua", "Crew.lua"}
