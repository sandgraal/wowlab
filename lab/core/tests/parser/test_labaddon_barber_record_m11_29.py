"""`wowlab_core.labaddon` reads schema 2, the addon format M11-29 introduces.

Schema 2 is schema 1 plus two keys on the customization record:
`chr_model_id_absent` (which outcome the `GetViewingChrModel` call had when
the section gathered) and `events_received` (each registered event, with how
many times it reached the section in the session that saved the file).
Schema 1 files are read exactly as before, and their model is not loosened.

The real captures are schema 1 and are read unchanged. No capture holds
schema 2 yet: the owner's capture after M11-29 is the first
(`lab/addon/README.md`, "M11-29 capture step"). So the schema-2 cases are
constructed (L8: boundary and hostile cases, `constructed` in the test id).
Each starts from the real M11-23 record (1.60.1.70058, index row in
`fixtures/README.md`), with `schema` set to 2 and one key added or changed in
the shape the addon's own source writes. The reasons and event names are read
from `lab/addon/WowLab/Customization.lua`, not typed here, so the reader and
the addon cannot drift apart. Hostile characters are built with `chr()`, so
this file holds none of them raw.
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
REAL_CHARACTERS = [
    "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua",
    "macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua",
    RECORD_70058,
]
REAL_ACCOUNT = "macos/forever/WTF/Account/90000001#6/SavedVariables/WowLab.lua"
CUSTOMIZATION_LUA = ROOT / "lab" / "addon" / "WowLab" / "Customization.lua"

_BASE: dict[str, Any] = luadata.parse((FIXTURES / RECORD_70058).read_bytes()).to_python()[  # type: ignore[assignment]
    "WowLabCharDB"
]
_SOURCE = CUSTOMIZATION_LUA.read_text(encoding="utf-8")


def _names(field: str) -> tuple[str, ...]:
    match = re.search(rf"^\s*{field} = \{{(.*?)\}},$", _SOURCE, re.M | re.S)
    assert match, f"no `{field} = {{ ... }},` in Customization.lua"
    return tuple(re.findall(r'"([A-Z0-9_]+)"', match.group(1)))


# Every reason the addon writes for the model id, and every event it asks for.
MODEL_REASONS = tuple(sorted(set(re.findall(r'record\.chr_model_id_absent = "([^"]+)"', _SOURCE))))
CHANGE_EVENTS = _names("events")
COUNT_ONLY = _names("count_only")
RECEIVED_HEAD = (
    "times each registered event reached the section in the session that saved this file"
)
MODEL_TAIL = (
    "how the call went when the section gathered, not why no model was named; "
    "the body type comes from sex"
)


def _base_v2() -> dict[str, Any]:
    raw = copy.deepcopy(_BASE)
    raw["schema"] = 2
    return raw


def _base_v1() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


def _text(char: labaddon.CharDB) -> str:
    return "\n".join(labaddon.describe(char))


def _refused(raw: object, *fragments: str) -> str:
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.load_char(raw)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message, message
    return message


def _v2_customization(char: labaddon.CharDB) -> labaddon.CustomizationV2:
    assert isinstance(char, labaddon.CharDBV2)
    record = char.customization
    assert isinstance(record, labaddon.CustomizationV2)
    return record


def test_the_addon_source_lists_what_these_tests_use() -> None:
    assert len(MODEL_REASONS) == 4, MODEL_REASONS
    assert CHANGE_EVENTS == ("BARBER_SHOP_OPEN", "BARBER_SHOP_APPEARANCE_APPLIED")
    assert len(COUNT_ONLY) == 6, COUNT_ONLY
    assert "WOWLAB_CONTROL_NOT_A_REAL_EVENT" in COUNT_ONLY


# ─── schema 1 is read exactly as before ──────────────────────────────────────


@pytest.mark.parametrize("name", REAL_CHARACTERS)
def test_real_capture_is_schema_1_and_shows_no_schema_2_line(name: str) -> None:
    char = labaddon.parse_char((FIXTURES / name).read_bytes())
    assert type(char) is labaddon.CharDBV1
    assert char.customization is None or type(char.customization) in (
        labaddon.Customization,
        labaddon.AbsentSection,
    )
    text = _text(char)
    assert "  model: " not in text
    assert RECEIVED_HEAD not in text
    assert labaddon.unknown_keys(char) == []


def test_real_account_capture_is_schema_1() -> None:
    account = labaddon.parse_account((FIXTURES / REAL_ACCOUNT).read_bytes())
    assert type(account) is labaddon.AccountDBV1


def test_schema_1_models_hold_only_their_own_keys() -> None:
    """The schema-1 models are not loosened: the M11-29 keys exist only on
    the schema-2 customization models."""
    assert set(labaddon.Customization.model_fields) == {
        "events_unregistered",
        "as_of",
        "recorded_at",
        "recorded_load",
        "carried",
        "choices",
        "race_id",
        "sex",
        "chr_model_id",
    }
    assert set(labaddon.AbsentSection.model_fields) == {
        "absent",
        "absent_clipped",
        "events_unregistered",
    }
    new = {"events_received", "chr_model_id_absent", "chr_model_id_absent_clipped"}
    assert new <= set(labaddon.CustomizationV2.model_fields)
    assert "events_received" in labaddon.AbsentCustomizationV2.model_fields
    v1 = labaddon.CharDBV1.model_fields
    v2 = labaddon.CharDBV2.model_fields
    assert list(v1) == list(v2)  # the same keys in the same order; only two types differ
    changed = [name for name in v1 if v1[name].annotation != v2[name].annotation]
    assert changed == ["schema_", "customization"], changed


def test_constructed_schema_1_file_with_the_model_reason_is_refused_as_today() -> None:
    """The schema-1 model keeps refusing text in an unknown key."""
    raw = _base_v1()
    raw["customization"]["chr_model_id_absent"] = MODEL_REASONS[0]
    message = _refused(
        raw,
        "WowLabCharDB does not fit the schema-1 model",
        "the unknown key chr_model_id_absent holds text; text is accepted only in the fields "
        "the addon writes as text",
    )
    assert MODEL_REASONS[0] not in message


def test_constructed_schema_1_file_with_event_counts_keeps_them_as_an_unknown_key() -> None:
    """Numbers in an unknown key are kept, as before M11-29; schema 1 does not
    read them as `events_received`."""
    raw = _base_v1()
    raw["customization"]["events_received"] = {"BARBER_SHOP_OPEN": 1}
    char = labaddon.load_char(raw)
    assert type(char) is labaddon.CharDBV1
    assert labaddon.unknown_keys(char) == ["customization.events_received"]
    assert RECEIVED_HEAD not in _text(char)


@pytest.mark.parametrize("name", REAL_CHARACTERS)
def test_constructed_real_capture_relabelled_schema_2_reads_the_same(name: str) -> None:
    """Schema 2 reads every schema-1 key the same way: the same capture with
    only its number changed describes identically."""
    raw = luadata.parse((FIXTURES / name).read_bytes()).to_python()["WowLabCharDB"]
    assert isinstance(raw, dict)
    v1 = labaddon.load_char(raw)
    raw["schema"] = 2
    v2 = labaddon.load_char(raw)
    assert type(v2) is labaddon.CharDBV2
    assert labaddon.describe(v2) == labaddon.describe(v1)
    assert labaddon.customization_loads_ago(v2) == labaddon.customization_loads_ago(v1)


# ─── schema numbers ──────────────────────────────────────────────────────────


def test_constructed_schema_3_is_refused_as_another_version() -> None:
    raw = _base_v2()
    raw["schema"] = 3
    _refused(
        raw,
        "WowLabCharDB is schema 3, and this reader knows schema 1, 2 only: it was written by "
        "another version of the lab-addon; nothing was read",
    )


def test_constructed_account_schema_2_reads_and_3_is_refused() -> None:
    assert type(labaddon.load_account({"schema": 2})) is labaddon.AccountDBV2
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.load_account({"schema": 3})
    assert "WowLabDB is schema 3, and this reader knows schema 1, 2 only" in str(info.value)


def test_constructed_schema_2_file_from_lua_text() -> None:
    """End to end from bytes: the real 70058 file with its schema number
    changed and the two keys added the way the client writes a table (CRLF,
    no indentation), read through `parse_char`."""
    data = (FIXTURES / RECORD_70058).read_bytes()
    assert data.count(b'["schema"] = 1,\r\n') == 1
    assert data.count(b'["customization"] = {\r\n') == 1
    data = data.replace(b'["schema"] = 1,\r\n', b'["schema"] = 2,\r\n')
    data = data.replace(
        b'["customization"] = {\r\n',
        b'["customization"] = {\r\n'
        b'["chr_model_id_absent"] = "C_BarberShop.GetViewingChrModel returned nil",\r\n'
        b'["events_received"] = {\r\n["BARBER_SHOP_OPEN"] = 1,\r\n'
        b'["BARBER_SHOP_CLOSE"] = 1,\r\n},\r\n',
    )
    record = _v2_customization(labaddon.parse_char(data))
    assert record.chr_model_id_absent == "C_BarberShop.GetViewingChrModel returned nil"
    assert record.events_received == {"BARBER_SHOP_OPEN": 1, "BARBER_SHOP_CLOSE": 1}


# ─── the model reason ────────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", MODEL_REASONS)
def test_constructed_every_model_reason_the_addon_writes_is_read(reason: str) -> None:
    raw = _base_v2()
    raw["customization"]["chr_model_id_absent"] = reason
    char = labaddon.load_char(raw)
    record = _v2_customization(char)
    assert record.chr_model_id_absent == reason
    assert record.chr_model_id_absent_clipped is None
    assert f"  model: absent ({reason}), {MODEL_TAIL}" in _text(char)
    assert labaddon.unknown_keys(char) == []


def test_constructed_model_and_its_reason_together_are_refused() -> None:
    raw = _base_v2()
    raw["customization"]["chr_model_id"] = 9
    raw["customization"]["chr_model_id_absent"] = MODEL_REASONS[0]
    _refused(raw, "both chr_model_id and chr_model_id_absent")


def test_constructed_model_number_shows_no_reason_line() -> None:
    raw = _base_v2()
    raw["customization"]["chr_model_id"] = 9
    text = _text(labaddon.load_char(raw))
    assert "model 9" in text
    assert "  model: " not in text


def test_constructed_model_reason_is_clipped_not_refused() -> None:
    """A reason is the one text the reader clips (M11-27), this one included."""
    raw = _base_v2()
    raw["customization"]["chr_model_id_absent"] = "error: " + "x" * 2000
    record = _v2_customization(labaddon.load_char(raw))
    assert record.chr_model_id_absent_clipped is not None
    assert record.chr_model_id_absent_clipped.truncated
    assert record.chr_model_id_absent is not None
    assert len(record.chr_model_id_absent) == labaddon.REASON_LIMIT


def test_constructed_model_reason_clip_flag_in_the_file_is_refused() -> None:
    """Only the reader writes a `_clipped` flag (M11-27)."""
    raw = _base_v2()
    raw["customization"]["chr_model_id_absent"] = MODEL_REASONS[0]
    raw["customization"]["chr_model_id_absent_clipped"] = {
        "original_length": 3000,
        "escaped": False,
        "truncated": True,
    }
    _refused(raw, "chr_model_id_absent_clipped is written by this reader, never by the addon")


def test_constructed_carried_record_with_neither_model_nor_reason() -> None:
    """`carry` keeps only the number, so a carried record may hold neither."""
    raw = _base_v2()
    raw["customization"]["carried"] = True
    raw["customization"]["events_received"] = dict.fromkeys(CHANGE_EVENTS + COUNT_ONLY, 0)
    record = _v2_customization(labaddon.load_char(raw))
    assert record.carried and record.chr_model_id is None
    assert record.chr_model_id_absent is None


# ─── the event counts ────────────────────────────────────────────────────────


def test_constructed_events_received_on_a_present_record() -> None:
    """One visit with one Accept, as the M11-29 runbook asks: every event the
    section asks for has an entry, shown by name with its count."""
    raw = _base_v2()
    counts = dict.fromkeys(CHANGE_EVENTS + COUNT_ONLY, 0)
    counts["BARBER_SHOP_OPEN"] = 1
    counts["BARBER_SHOP_APPEARANCE_APPLIED"] = 1
    raw["customization"]["events_received"] = counts
    raw["customization"]["recorded_at"] = "applied"
    char = labaddon.load_char(raw)
    record = _v2_customization(char)
    assert record.events_received == counts
    shown = ", ".join(f"{name} {counts[name]}" for name in sorted(counts))
    assert f"  {RECEIVED_HEAD}: {shown}" in _text(char)
    assert labaddon.unknown_keys(char) == []
    again = labaddon.CharDBV2.model_validate_json(char.model_dump_json())
    assert again == char
    assert json.loads(char.model_dump_json())["customization"]["events_received"] == counts


def test_constructed_events_received_on_a_never_gathered_section() -> None:
    """No visit in the session: the section is absent with its reason, and
    the counts are all 0."""
    raw = _base_v2()
    counts = dict.fromkeys(CHANGE_EVENTS + COUNT_ONLY, 0)
    raw["customization"] = {
        "absent": "no barber-shop visit recorded with the addon enabled",
        "events_unregistered": [],
        "events_received": counts,
    }
    char = labaddon.load_char(raw)
    assert isinstance(char, labaddon.CharDBV2)
    assert isinstance(char.customization, labaddon.AbsentCustomizationV2)
    assert char.customization.events_received == counts
    zeros = ", ".join(f"{name} 0" for name in sorted(counts))
    assert (
        "Customization: absent (no barber-shop visit recorded with the addon enabled); "
        f"all its change events registered; {RECEIVED_HEAD}: {zeros}"
    ) in _text(char)


def test_constructed_events_received_empty_table_reads_none_recorded() -> None:
    """Every event refused: the addon writes an empty table (read as `{}`)."""
    raw = _base_v2()
    raw["customization"]["events_received"] = {}
    assert f"  {RECEIVED_HEAD}: none recorded" in _text(labaddon.load_char(raw))


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
    raw = _base_v2()
    raw["customization"]["events_received"] = value
    _refused(raw, "WowLabCharDB.customization.events_received")


# Hostile keys: each is refused, and the message never carries the key (a
# refusal reaches the terminal; the reader's location would otherwise hold it).
HOSTILE_KEYS = {
    "lowercase": "barber_shop_open",
    "space": "BARBER SHOP",
    "escape": chr(0x1B) + "[31mBARBER",
    "newline": "BARBER" + chr(0x0A) + "SHOP",
    "trailing-newline": "BARBER_SHOP_OPEN" + chr(0x0A),
    "rlo": chr(0x202E) + "BARBER",
    "line-separator": "BARBER" + chr(0x2028),
    "too-long": "B" * 129,
}


@pytest.mark.parametrize("key", HOSTILE_KEYS.values(), ids=HOSTILE_KEYS.keys())
def test_constructed_events_received_key_that_is_not_an_event_name_is_never_echoed(
    key: str,
) -> None:
    raw = _base_v2()
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
    raw = _base_v2()
    raw["customization"]["events_received"] = {1: 1}
    _refused(raw, "events_received", "the number key 1")


# ─── an unknown key never reaches the text (M11-29 security review) ──────────
#
# A model keeps an unknown key and hands it back as an attribute, so the text
# must read only declared fields. Each value below is what `luadata` gives for
# a Lua table the reader keeps as an unknown key: `{ 3, 1 }`, `5`,
# `{ [1] = 1, A = 2 }`, `{ PLAYER_NAME = 1 }`.

UNKNOWN_SHAPES = {
    "list": [3, 1],
    "number": 5,
    "mixed-keys": {1: 1, "A": 2},
    "plain-name": {"PLAYER_NAME": 1},
}
# (schema, section): where `events_received` is not a declared field.
UNDECLARED = [(1, "customization"), (1, "professions"), (2, "professions")]


@pytest.mark.parametrize("value", UNKNOWN_SHAPES.values(), ids=UNKNOWN_SHAPES.keys())
@pytest.mark.parametrize(
    ("schema", "section"), UNDECLARED, ids=["v1-customization", "v1-professions", "v2-professions"]
)
def test_constructed_unknown_events_received_on_an_absent_section_is_kept_not_shown(
    schema: int, section: str, value: object
) -> None:
    raw = _base_v1()
    raw["schema"] = schema
    raw[section] = {
        "absent": "a reason",
        "events_unregistered": [],
        "events_received": value,
    }
    char = labaddon.load_char(raw)
    text = _text(char)  # must not raise
    assert "times each registered event reached" not in text
    assert "PLAYER_NAME" not in text.split("Keys this reader does not know")[0]
    assert f"{section}.events_received" in labaddon.unknown_keys(char)
    label = "Customization" if section == "customization" else "Professions"
    assert f"{label}: absent (a reason); all its change events registered" in text


@pytest.mark.parametrize("value", UNKNOWN_SHAPES.values(), ids=UNKNOWN_SHAPES.keys())
def test_constructed_schema_2_absent_customization_refuses_those_shapes(value: object) -> None:
    """On schema 2's absent customization the field is declared, so each
    shape is refused rather than kept."""
    raw = _base_v2()
    raw["customization"] = {"absent": "a reason", "events_received": value}
    if value == {"PLAYER_NAME": 1}:
        # An event-shaped name is a valid key there: kept as a count.
        char = labaddon.load_char(raw)
        assert "PLAYER_NAME 1" in _text(char)
        return
    _refused(raw, "WowLabCharDB.customization.events_received")


def test_constructed_unknown_events_unregistered_on_an_absent_client_is_kept_not_shown() -> None:
    """The same cause on main: `events_unregistered` is declared on absent
    sections only, so on the absent client block it is an unknown key."""
    raw = _base_v1()
    raw["client"] = {"absent": "GetBuildInfo missing", "events_unregistered": [3, 1]}
    char = labaddon.load_char(raw)
    text = _text(char)  # must not raise
    assert "Client: absent (GetBuildInfo missing)\n" in text + "\n"
    assert "client.events_unregistered" in labaddon.unknown_keys(char)


def test_constructed_unknown_events_received_from_lua_text() -> None:
    """End to end from bytes, as `char show` reads it: the real schema-1 file
    with its customization record made absent and `{ 3, 1 }` added."""
    data = (FIXTURES / RECORD_70058).read_bytes()
    start = data.index(b'["customization"] = {\r\n')
    end = data.index(b'["gear"] = {\r\n', start)
    data = (
        data[:start]
        + b'["customization"] = {\r\n["absent"] = "a reason",\r\n'
        + b'["events_received"] = {\r\n3,\r\n1,\r\n},\r\n},\r\n'
        + data[end:]
    )
    char = labaddon.parse_char(data)
    assert type(char) is labaddon.CharDBV1
    text = _text(char)
    assert "Customization: absent (a reason)" in text
    assert "times each registered event reached" not in text
    assert "customization.events_received" in labaddon.unknown_keys(char)
