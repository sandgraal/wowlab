# Probe from review of m20/11-crew-matches; reproduces three edits of the real Crew.lua that break a kept rule (once per match, the 20,000 limit) and pass every Crew.lua rule
"""CONSTRUCTED INPUT: one-line edits of the real `lab/addon/WowLab/Crew.lua`,
graded by `_crew_problems` of `tests/addon/test_lab_addon.py`; no crew record,
no player name, no Lua is run (L3). The book's global name is taken from the
grader, never written here.

1. Deleting the `matched` early return in `recordMatch` (the read stays as an
   unused local) lets every score event and every `/reload` count the match
   again; the "matched read and then written first" rule passes.
2. Deleting `and makeRoom()` in `recordMatch` adds scoreboard players past
   MAX_PLAYERS; nothing refuses it.
3. `oldest = "x"` in `makeRoom` drops no one and still returns true, so the
   20,001st player is kept; M20-04's "an oldest taken from elsewhere" case
   caught this and was removed with the key rules.

Positive control: the real file passes, and moving the marker store after the
counters (the rule's own constructed case) is refused.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
ADDON_TESTS = ROOT / "tests" / "addon"
CREW = ROOT / "lab" / "addon" / "WowLab" / "Crew.lua"

MARKER_RETURN = (
    '    if type(marker) == "table" and marker.map_id == mapId then\n        return\n    end\n'
)
MAKE_ROOM = "players[key] == nil and makeRoom() then"
OLDEST = "            if oldest == nil or last < oldestLast then\n                oldest = key\n"


def _grader(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ADDON_TESTS))
    return importlib.import_module("test_lab_addon")


def _edited(found: str, replacement: str) -> str:
    src = CREW.read_text(encoding="utf-8")
    assert src.count(found) == 1, found
    return src.replace(found, replacement, 1)


def _problems(g: ModuleType, text: str) -> list[str]:
    return list(g._crew_problems(g._CREW_PATH, text))


def test_positive_control(monkeypatch: pytest.MonkeyPatch) -> None:
    g = _grader(monkeypatch)
    assert _problems(g, CREW.read_text(encoding="utf-8")) == []
    store = f"    {g.CREW_DB}.matched = {{ map_id = mapId, at = now }}\n"
    moved = _edited(store, "")
    moved = moved.replace("    local record = {", store + "    local record = {", 1)
    assert _problems(g, moved)


def test_the_matched_early_return_deleted_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    g = _grader(monkeypatch)
    assert _problems(g, _edited(MARKER_RETURN, ""))


def test_a_scoreboard_player_added_without_make_room_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g = _grader(monkeypatch)
    assert _problems(g, _edited(MAKE_ROOM, "players[key] == nil then"))


def test_make_room_dropping_no_one_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    g = _grader(monkeypatch)
    edited = OLDEST.replace("oldest = key", 'oldest = "x"')
    assert _problems(g, _edited(OLDEST, edited))
