"""M16-55: in a schema-5 debug session the views count a section's dropped
values as the addon's `dropped` does, leaving out issecretvalue's own
errors (each is a value already counted as `secret` on the API that gave
it); schema 3 and 4 records keep the count they always printed.

The record is the real schema-3 capture's action bars, with constructed
probe counts (labelled `constructed`): an issecretvalue that raised on two
values, both dropped and counted on GetActionInfo.
"""

from __future__ import annotations

from pathlib import Path

from wowlab_core import keymap, labaddon, macrocheck

SCHEMA_3 = (
    Path(__file__).resolve().parent
    / "fixtures/macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables"
    / "WowLab.lua"
)


def _record() -> labaddon.CharDBV3:
    rec = labaddon.read_char(SCHEMA_3)
    assert isinstance(rec, labaddon.CharDBV3)
    return rec


def _raised_twice(bars: labaddon.ActionBars) -> list[labaddon.ApiProbe]:
    """GetActionInfo dropped two values because issecretvalue raised on both."""
    out = []
    for p in bars.probes:
        if p.api == "GetActionInfo":
            p = p.model_copy(update={"secret": 2})
        elif p.api == "issecretvalue":
            p = p.model_copy(update={"errors": 2, "answered": p.answered - 2})
        out.append(labaddon.ApiProbe.model_validate(p.model_dump(by_alias=True)))
    return out


def _bars(schema5: bool) -> labaddon.ActionBars:
    bars = _record().actionbars
    assert isinstance(bars, labaddon.ActionBars)
    data = bars.model_dump(by_alias=True)
    data["probes"] = [p.model_dump(by_alias=True) for p in _raised_twice(bars)]
    model = labaddon.ActionBarsV5 if schema5 else labaddon.ActionBars
    return model.model_validate(data)


EXPECTED_V5 = "0 call(s) raised an error and 2 value(s) were dropped as secret or untestable"
EXPECTED_V3 = "2 call(s) raised an error and 2 value(s) were dropped as secret or untestable"


def test_a_debug_session_counts_each_dropped_value_once_constructed() -> None:
    bars = _bars(schema5=True)
    assert isinstance(bars, labaddon.ActionBarsV5) and bars.dropped is None
    assert keymap.dropped(bars, frozenset()) == [EXPECTED_V5]
    assert macrocheck._dropped(bars, frozenset()) == [EXPECTED_V5]
    assert "issecretvalue" not in {p.api for p in labaddon.counted_probes(bars)}


def test_schema_3_and_4_keep_issecretvalue_errors_in_the_count_constructed() -> None:
    bars = _bars(schema5=False)
    assert not isinstance(bars, labaddon.ActionBarsV5)
    assert keymap.dropped(bars, frozenset()) == [EXPECTED_V3]
    assert macrocheck._dropped(bars, frozenset()) == [EXPECTED_V3]


def test_the_views_note_says_the_debug_session_count_constructed() -> None:
    for schema5, expected in ((True, EXPECTED_V5), (False, EXPECTED_V3)):
        rec = _record().model_copy(update={"actionbars": _bars(schema5)})
        km = keymap.join_record(account=None, character=None, record=rec)
        assert f"The action-bar record is incomplete ({expected}):" in " ".join(km.notes)


def test_looked_up_names_are_never_counted_and_a_section_without_probes_counts_none() -> None:
    bars = _record().actionbars
    assert {p.api for p in labaddon.counted_probes(bars)} == {
        "GetActionInfo",
        "GetMacroInfo",
        "issecretvalue",
    }
    assert labaddon.counted_probes(None) == []
