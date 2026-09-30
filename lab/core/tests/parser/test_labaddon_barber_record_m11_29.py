"""`wowlab_core.labaddon` reads the keys M11-29 adds to the customization record.

The addon now writes `chr_model_id_absent` (why `GetViewingChrModel` gave no
number) and `events_received` (each event the section registered, with how
many times it reached the section in the session that saved the file). No
capture holds them yet: the owner's capture after M11-29 is the first
(`lab/addon/README.md`, "M11-29 capture step"). So every test but the first
is constructed (L8: boundary and hostile cases, `constructed` in the test id).
Each starts from the real M11-23 record (1.60.1.70058, index row in
`fixtures/README.md`), read as `luadata.to_python` gives it, and adds or
changes one key in the shape the addon's own source writes; the reasons and
event names are read from `lab/addon/WowLab/Customization.lua`, not typed
here, so the reader and the addon cannot drift apart.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RECORD_70058 = (
    "macos-70058/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
)
CUSTOMIZATION_LUA = ROOT / "lab" / "addon" / "WowLab" / "Customization.lua"

_BASE: dict[str, Any] = luadata.parse((FIXTURES / RECORD_70058).read_bytes()).to_python()[  # type: ignore[assignment]
    "WowLabCharDB"
]
_SOURCE = CUSTOMIZATION_LUA.read_text(encoding="utf-8")
# Every reason the addon writes for the model id, and every event it asks for.
MODEL_REASONS = tuple(sorted(set(re.findall(r'record\.chr_model_id_absent = "([^"]+)"', _SOURCE))))
CHANGE_EVENTS = tuple(
    re.findall(r'"([A-Z0-9_]+)"', re.search(r"^\s*events = \{(.*)\},$", _SOURCE, re.M)[1])  # type: ignore[index]
)
COUNT_ONLY = tuple(
    re.findall(r'"([A-Z0-9_]+)"', re.search(r"^\s*count_only = \{(.*)\},$", _SOURCE, re.M)[1])  # type: ignore[index]
)
RECEIVED_HEAD = "events that reached the section in the session that saved this file"


def _base() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


def _text(char: labaddon.CharDBV1) -> str:
    return "\n".join(labaddon.describe(char))


def _refused(raw: object, *fragments: str) -> str:
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.load_char(raw)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message, message
    return message


def test_the_addon_source_lists_what_these_tests_use() -> None:
    assert len(MODEL_REASONS) == 4, MODEL_REASONS
    assert CHANGE_EVENTS == ("BARBER_SHOP_OPEN", "BARBER_SHOP_APPEARANCE_APPLIED")
    assert len(COUNT_ONLY) == 3, COUNT_ONLY


def test_real_70058_record_predates_m11_29_and_shows_neither_line() -> None:
    """The committed M11-23 record holds neither key; nothing new is shown."""
    char = labaddon.parse_char((FIXTURES / RECORD_70058).read_bytes())
    record = char.customization
    assert isinstance(record, labaddon.Customization)
    assert record.chr_model_id is None and record.chr_model_id_absent is None
    assert record.events_received is None
    text = _text(char)
    assert "  model: " not in text
    assert RECEIVED_HEAD not in text


@pytest.mark.parametrize("reason", MODEL_REASONS)
def test_constructed_every_model_reason_the_addon_writes_is_read(reason: str) -> None:
    raw = _base()
    raw["customization"]["chr_model_id_absent"] = reason
    char = labaddon.load_char(raw)
    record = char.customization
    assert isinstance(record, labaddon.Customization)
    assert record.chr_model_id_absent == reason
    assert record.chr_model_id_absent_clipped is None
    assert f"  model: absent ({reason})" in _text(char)
    assert labaddon.unknown_keys(char) == []


def test_constructed_model_and_its_reason_together_are_refused() -> None:
    raw = _base()
    raw["customization"]["chr_model_id"] = 9
    raw["customization"]["chr_model_id_absent"] = MODEL_REASONS[0]
    _refused(raw, "both chr_model_id and chr_model_id_absent")


def test_constructed_model_number_shows_no_reason_line() -> None:
    raw = _base()
    raw["customization"]["chr_model_id"] = 9
    text = _text(labaddon.load_char(raw))
    assert "model 9" in text
    assert "  model: " not in text


def test_constructed_model_reason_is_clipped_not_refused() -> None:
    """A reason is the one text the reader clips (M11-27), this one included."""
    raw = _base()
    raw["customization"]["chr_model_id_absent"] = "error: " + "x" * 2000
    record = labaddon.load_char(raw).customization
    assert isinstance(record, labaddon.Customization)
    assert record.chr_model_id_absent_clipped is not None
    assert record.chr_model_id_absent_clipped.truncated
    assert record.chr_model_id_absent is not None
    assert len(record.chr_model_id_absent) == labaddon.REASON_LIMIT


def test_constructed_model_reason_clip_flag_in_the_file_is_refused() -> None:
    """Only the reader writes a `_clipped` flag (M11-27)."""
    raw = _base()
    raw["customization"]["chr_model_id_absent"] = MODEL_REASONS[0]
    raw["customization"]["chr_model_id_absent_clipped"] = {
        "original_length": 3000,
        "escaped": False,
        "truncated": True,
    }
    _refused(raw, "chr_model_id_absent_clipped is written by this reader, never by the addon")


def test_constructed_events_received_on_a_present_record() -> None:
    """One visit with one Accept, as the M11-29 runbook asks: every event the
    section asks for has an entry, shown by name with its count."""
    raw = _base()
    counts = dict.fromkeys(CHANGE_EVENTS + COUNT_ONLY, 0)
    counts["BARBER_SHOP_OPEN"] = 1
    counts["BARBER_SHOP_APPEARANCE_APPLIED"] = 1
    raw["customization"]["events_received"] = counts
    raw["customization"]["recorded_at"] = "applied"
    char = labaddon.load_char(raw)
    record = char.customization
    assert isinstance(record, labaddon.Customization)
    assert record.events_received == counts
    shown = ", ".join(f"{name} {counts[name]}" for name in sorted(counts))
    assert f"  {RECEIVED_HEAD}: {shown}" in _text(char)
    assert labaddon.unknown_keys(char) == []
    again = labaddon.CharDBV1.model_validate_json(char.model_dump_json())
    assert again == char
    assert json.loads(char.model_dump_json())["customization"]["events_received"] == counts


def test_constructed_events_received_on_a_never_gathered_section() -> None:
    """No visit in the session: the section is absent with its reason, and
    the counts are all 0."""
    raw = _base()
    counts = dict.fromkeys(CHANGE_EVENTS + COUNT_ONLY, 0)
    raw["customization"] = {
        "absent": "no barber-shop visit recorded with the addon enabled",
        "events_unregistered": [],
        "events_received": counts,
    }
    char = labaddon.load_char(raw)
    assert isinstance(char.customization, labaddon.AbsentSection)
    assert char.customization.events_received == counts
    zeros = ", ".join(f"{name} 0" for name in sorted(counts))
    assert (
        "Customization: absent (no barber-shop visit recorded with the addon enabled); "
        f"all its change events registered; {RECEIVED_HEAD}: {zeros}"
    ) in _text(char)


def test_constructed_events_received_empty_table_reads_none_recorded() -> None:
    """Every event refused: the addon writes an empty table (read as `{}`)."""
    raw = _base()
    raw["customization"]["events_received"] = {}
    char = labaddon.load_char(raw)
    assert f"  {RECEIVED_HEAD}: none recorded" in _text(char)


def test_constructed_carried_record_with_neither_model_nor_reason() -> None:
    """`carry` keeps only the number, so a carried record may hold neither."""
    raw = _base()
    raw["customization"]["carried"] = True
    raw["customization"]["events_received"] = dict.fromkeys(CHANGE_EVENTS + COUNT_ONLY, 0)
    record = labaddon.load_char(raw).customization
    assert isinstance(record, labaddon.Customization)
    assert record.carried and record.chr_model_id is None
    assert record.chr_model_id_absent is None


@pytest.mark.parametrize(
    "value",
    [
        {"BARBER_SHOP_OPEN": "2"},
        {"BARBER_SHOP_OPEN": -1},
        {"BARBER_SHOP_OPEN": 1.5},
        {"BARBER_SHOP_OPEN": True},
        {"BARBER_SHOP_OPEN": 2**53 + 1},
        ["BARBER_SHOP_OPEN"],
    ],
    ids=["text", "negative", "fraction", "boolean", "past-2-53", "list"],
)
def test_constructed_events_received_takes_whole_counts_from_zero(value: object) -> None:
    raw = _base()
    raw["customization"]["events_received"] = value
    _refused(raw, "WowLabCharDB.customization.events_received")


# Hostile keys: each is refused, and the message never carries the key (a
# refusal reaches the terminal; the reader's location would otherwise hold it).
HOSTILE_KEYS = {
    "lowercase": "barber_shop_open",
    "space": "BARBER SHOP",
    "escape": "\x1b[31mBARBER",
    "newline": "BARBER\nSHOP",
    "trailing-newline": "BARBER_SHOP_OPEN\n",
    "rlo": "‮BARBER",
    "too-long": "B" * 129,
}


@pytest.mark.parametrize("key", HOSTILE_KEYS.values(), ids=HOSTILE_KEYS.keys())
def test_constructed_events_received_key_that_is_not_an_event_name_is_never_echoed(
    key: str,
) -> None:
    raw = _base()
    raw["customization"]["events_received"] = {"BARBER_SHOP_OPEN": 1, key: 1}
    message = _refused(
        raw,
        "WowLabCharDB.customization.events_received",
        f"a key of {len(key)} characters that is not an event name",
    )
    assert key not in message
    assert key.strip() not in message
    assert message.isprintable()


def test_constructed_events_received_number_key_is_refused() -> None:
    raw = _base()
    raw["customization"]["events_received"] = {1: 1}
    _refused(raw, "events_received", "the number key 1")
