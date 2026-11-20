# Probe from review of m20/04-crew-schema2-checks; reproduces `probe.pvp` written into a book built in a local and then stored whole, which every schema 2 rule passes
"""CONSTRUCTED INPUT: an edit of the grader's constructed schema 2 Crew.lua
(`_CREW_SOURCE`); no real crew record, no player name, no Lua is run (L3).
The book's global name is taken from the grader, never written here.

Fix round 1 refuses `pvp` as a constructor key and `probe` as one unless it
is `{ loads = 1 }` or `{ loads = 1, lost = true }`. The `probe.pvp` rule
still looks only at stores through the book's global name. In
`ns.CrewCommand`, a new book built in a local (`local book = { ..., probe =
{ loads = 1 } }`), given `book.probe.pvp = { ... }`, and then stored whole
(`<book> = book`) passes Crew.lua's rules, every relaxed rule and the whole
installed grader. The positive control (the same store through the global)
is refused.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
ADDON_TESTS = ROOT / "tests" / "addon"
TAIL = '    print(nameAbsent() or "Crew book.")\n'
NEW = (
    "    local book = { schema = CREW_SCHEMA, players = {}, current = {}, "
    "matches = {}, probe = { loads = 1 } }\n"
)


def _grader(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ADDON_TESTS))
    return importlib.import_module("test_lab_addon")


def _edited(g: ModuleType, lines: str) -> str:
    src: str = g._CREW_SOURCE
    assert src.count(TAIL) == 1
    return src.replace(TAIL, lines + TAIL, 1)


def _problems(g: ModuleType, text: str) -> list[str]:
    rules = g._crew_rules(g._CREW_PATH, text)
    return list(g._crew_problems(g._CREW_PATH, text, g.CREW_PINS_2)) + [
        p for bad in rules.values() for p in bad
    ]


def test_control_the_store_through_the_global_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g = _grader(monkeypatch)
    book = g.CREW_DB
    text = _edited(g, f"{NEW}    {book} = book\n    {book}.probe.pvp = {{ ns.Number(time()) }}\n")
    assert _problems(g, text)


def test_probe_pvp_set_on_a_local_book_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    g = _grader(monkeypatch)
    book = g.CREW_DB
    text = _edited(g, f"{NEW}    book.probe.pvp = {{ ns.Number(time()) }}\n    {book} = book\n")
    assert _problems(g, text), "probe.pvp written outside the two pinned stores passed"
