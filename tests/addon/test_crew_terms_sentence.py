"""The sentence of ADR-0036 term 2 is one sentence in four places
(docs/LAB_PLAN.md §20.5, §21.4; M20-01): `cli.CREW_BOOK_SENTENCE` (what
`wowlab crew --help` says), `TERMS` in the addon's `Crew.lua` (the notice
printed when the book is turned on), the quote in §20.5 and the quote in
the addon README. From M20-01 it names wowlab's own crew database as a
place records are kept.
"""

from __future__ import annotations

import re
from pathlib import Path

from wowlab_core import cli

REPO = Path(__file__).resolve().parents[2]
CREW_LUA = REPO / "lab" / "addon" / "WowLab" / "Crew.lua"
README = REPO / "lab" / "addon" / "README.md"
PLAN = REPO / "docs" / "LAB_PLAN.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _lua_terms() -> str:
    """`local TERMS = "…" .. "…"`: the string literals joined."""
    source = CREW_LUA.read_text(encoding="utf-8")
    start = source.index("local TERMS = ")
    end = source.index("\nlocal ", start + 1)
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', source[start:end])
    assert parts and not any("\\" in p for p in parts)
    return "".join(parts)


def _quoted_after(path: Path, lead: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(lead) + len(lead)
    return text[start : text.index('"', start)]


def test_the_term_2_sentence_is_the_same_in_all_four_places() -> None:
    want = _flat(cli.CREW_BOOK_SENTENCE)
    assert _flat(_lua_terms()) == want
    assert _flat(_quoted_after(PLAN, 'The sentence of term 2: "')) == want
    assert _flat(_quoted_after(README, 'either way it prints what the book keeps: "')) == want


def test_the_sentence_names_wowlabs_own_database() -> None:
    assert "in wowlab's own crew database on this computer" in _flat(cli.CREW_BOOK_SENTENCE)
