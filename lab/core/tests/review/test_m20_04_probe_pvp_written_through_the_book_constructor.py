# Probe from review of m20/04-crew-schema2-checks; reproduces `probe.pvp` written through the new book's constructor in the slash handler, which every schema 2 rule passes
"""CONSTRUCTED INPUT: an edit of the grader's constructed schema 2 Crew.lua
(`_CREW_SOURCE`); no real crew record, no player name, no Lua is run (L3).

§21.2 says `probe.pvp` holds what the probe found and nothing else, and
`_crew_schema_2_problems` says it is "written only by the two pinned stores".
The rule looks only for `WowLabCrewDB . probe` and for `"pvp"` as a bracket
key. A book created in `ns.CrewCommand` as
`WowLabCrewDB = { ..., probe = { pvp = { ... } } }` sets `probe.pvp` to any
value the slash handler can reach, and Crew.lua's rules and every relaxed
rule pass it. The positive control (the same block written as a field store)
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


def _grader(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ADDON_TESTS))
    return importlib.import_module("test_lab_addon")


def _edited(g: ModuleType, line: str) -> str:
    src: str = g._CREW_SOURCE
    assert src.count(TAIL) == 1
    return src.replace(TAIL, line + TAIL, 1)


def _problems(g: ModuleType, text: str) -> list[str]:
    rules = g._crew_rules(g._CREW_PATH, text)
    return list(g._crew_problems(g._CREW_PATH, text, g.CREW_PINS_2)) + [
        p for bad in rules.values() for p in bad
    ]


def test_control_a_field_store_of_probe_pvp_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    g = _grader(monkeypatch)
    text = _edited(g, "    WowLabCrewDB.probe.pvp = { started = ns.Number(time()) }\n")
    assert _problems(g, text)


def test_probe_pvp_set_in_the_book_constructor_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g = _grader(monkeypatch)
    text = _edited(
        g,
        "    WowLabCrewDB = { schema = CREW_SCHEMA, players = {}, current = {}, "
        "matches = {}, probe = { pvp = { started = ns.Number(time()) } } }\n",
    )
    assert _problems(g, text), "probe.pvp written outside the two pinned stores passed"
