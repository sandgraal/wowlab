"""`wowlab_core.labaddon`, M11-27 (`wowlab char show` follow-ups), on
constructed input (L8: boundary and hostile cases; every test id says
`constructed`).

Each case starts from the real first-character capture (M11-03, index row in
`fixtures/README.md`) as `luadata.to_python` gives it and changes one thing:
(a) a second Legacy candidate config with points of its own, (b) an
`events_unregistered` list on `collections.appearances`, which registers no
events, (c) a reason longer than 1024 characters or holding characters
outside printable ASCII (a long Lua error the addon stored).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIRST = "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"

_BASE: Any = luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]

APPEARANCES_REASON = (
    "not gathered: asking the client for the appearance collection crashed the Forever client "
    "once (M11-03); the addon no longer asks"
)


def _base() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


def _load(raw: dict[str, Any]) -> labaddon.CharDBV1:
    return labaddon.load_char(raw)


def _lines(char: labaddon.CharDBV1) -> list[str]:
    return labaddon.describe(char)


def _refused(raw: object, *fragments: str) -> str:
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.load_char(raw)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message, message
    return message


def _printable(text: str) -> bool:
    return all(" " <= ch <= "~" for ch in text)


# ─── (a) Legacy candidates: one set of figures per config ────────────────────


def _legacy(raw: dict[str, Any]) -> dict[str, Any]:
    legacy: dict[str, Any] = raw["talents"]["legacy"]
    return legacy


def _with_quantity(config: dict[str, Any], quantity: int) -> dict[str, Any]:
    for tree in config["trees"]:
        if isinstance(tree["currencies"], list):
            for row in tree["currencies"]:
                row["quantity"] = quantity
    return config


def _second_config(raw: dict[str, Any], config_id: int, quantity: int) -> None:
    configs = _legacy(raw)["configs"]
    second = _with_quantity(copy.deepcopy(configs[0]), quantity)
    second["id"] = config_id
    configs.append(second)


def _headline(raw: dict[str, Any]) -> str:
    talents = _load(raw).talents
    assert talents is not None and isinstance(talents.legacy, labaddon.LegacyTalents)
    return labaddon.legacy_headline(talents.legacy)


SEVERAL = (
    "Legacy candidates: present, {n} configs{level}; the file does not say which one is the "
    "Legacy system, so each has its own figures (not added together): "
)
SEVERAL_SECOND_LINE = (  # reworded in M11-32 (test_labaddon_legacy_wording.py)
    "  the addon lists every trait config except the active class talents and the types below; "
    "which of these is the Legacy system is not recorded; panel opener ToggleLegacySystemUI "
    "present: yes"
)
ONE_SECOND_LINE = (
    "  which config is the Legacy system is inferred by elimination; panel opener "
    "ToggleLegacySystemUI present: yes"
)


def _legacy_lines(raw: dict[str, Any]) -> list[str]:
    lines = _lines(_load(raw))
    start = next(i for i, line in enumerate(lines) if line.startswith("Legacy candidates:"))
    return lines[start : start + 2]


def test_constructed_two_legacy_configs_with_points_are_not_summed() -> None:
    raw = _base()
    _with_quantity(_legacy(raw)["configs"][0], 2)
    _second_config(raw, 640756, 3)
    expected = SEVERAL.format(n=2, level=" (level 13)") + (
        "config 640755: nothing spent, 2 points available; "
        "config 640756: nothing spent, 3 points available"
    )
    assert _headline(raw) == expected
    assert "5 points" not in expected
    # the level sits after the count, never glued to the last config's figures
    assert _legacy_lines(raw) == [expected, SEVERAL_SECOND_LINE]


def test_constructed_several_configs_without_a_level_leave_it_out() -> None:
    raw = _base()
    _second_config(raw, 640756, 3)
    del _legacy(raw)["player_level"]
    assert _headline(raw).startswith(SEVERAL.format(n=2, level="") + "config 640755:")
    assert "level" not in _legacy_lines(raw)[0]


def test_constructed_one_config_keeps_its_wording_and_level() -> None:
    lines = _legacy_lines(_base())
    assert lines == [
        "Legacy candidates: present, nothing spent, 0 points available (level 13)",
        ONE_SECOND_LINE,
    ]


def test_constructed_two_legacy_configs_spent_points_are_per_config() -> None:
    raw = _base()
    configs = _legacy(raw)["configs"]
    _second_config(raw, 640756, 0)
    for config, spent in zip(configs, (1, 4), strict=True):
        for tree in config["trees"]:
            for row in tree["currencies"] if isinstance(tree["currencies"], list) else []:
                row["spent"] = spent
            if isinstance(tree["nodes"], list) and tree["nodes"]:  # `{}` when empty
                tree["nodes"][0]["active_rank"] = spent
    headline = _headline(raw)
    assert "config 640755: 3 ranks active, 1 point spent, 0 points available" in headline
    assert "config 640756: 12 ranks active, 4 points spent, 0 points available" in headline
    assert "5 points spent" not in headline and "15 ranks" not in headline


def test_constructed_one_disagreeing_config_leaves_the_other_config_figures() -> None:
    raw = _base()
    _second_config(raw, 640756, 3)
    rows = [
        t["currencies"][0]
        for t in _legacy(raw)["configs"][0]["trees"]
        if isinstance(t["currencies"], list) and t["currencies"]
    ]
    rows[1]["quantity"] = 2
    assert _headline(raw) == SEVERAL.format(n=2, level=" (level 13)") + (
        "config 640755: nothing spent, points available not added up: currency 4225 reports "
        "different values on different trees (see below); "
        "config 640756: nothing spent, 3 points available"
    )


def test_constructed_absent_candidate_beside_a_present_one_is_listed() -> None:
    raw = _base()
    _with_quantity(_legacy(raw)["configs"][0], 2)
    _legacy(raw)["configs"].append(
        {"id": 7, "absent": "C_Traits.GetConfigInfo returned nothing", "found_by": ["type:Generic"]}
    )
    expected = SEVERAL.format(n=2, level=" (level 13)") + (
        "config 640755: nothing spent, 2 points available; "
        "config 7: absent with a reason (see below)"
    )
    assert _legacy_lines(raw) == [expected, SEVERAL_SECOND_LINE]
    assert (
        "  config 7 absent (C_Traits.GetConfigInfo returned nothing); found by type:Generic"
        in _lines(_load(raw))
    )


def test_constructed_every_candidate_absent_keeps_its_line() -> None:
    raw = _base()
    _legacy(raw)["configs"] = [
        {"id": 7, "absent": "C_Traits.GetConfigInfo returned nothing", "found_by": ["type:Generic"]}
    ]
    assert _legacy_lines(raw) == [
        "Legacy candidates: present, every config absent with a reason (level 13)",
        ONE_SECOND_LINE,
    ]


# ─── (b) collections.appearances registers no events ─────────────────────────


APPEARANCES_NOTE = (
    "  events_unregistered is in the file, though the addon registers no events for this "
    "section (kept in --json)"
)


@pytest.mark.parametrize("events", [{}, [], ["TRANSMOG_COLLECTION_UPDATED"]])
def test_constructed_appearances_events_get_a_note_not_a_registration_claim(events: Any) -> None:
    raw = _base()
    raw["collections"]["appearances"]["events_unregistered"] = events
    raw["gear"]["events_unregistered"] = {}  # control: a section that does register events
    char = _load(raw)
    lines = _lines(char)
    at = lines.index(f"Appearances: absent ({APPEARANCES_REASON})")
    assert lines[at + 1] == APPEARANCES_NOTE
    assert "TRANSMOG" not in "\n".join(lines)
    assert "  all its change events registered" in lines  # gear still says it
    assert lines.count("  all its change events registered") == 1
    assert char.collections is not None and char.collections.appearances is not None
    kept = char.collections.appearances.events_unregistered
    assert kept == (events if events else [])  # still in the model and --json


def test_constructed_appearances_without_events_say_nothing() -> None:
    lines = _lines(_load(_base()))
    at = lines.index(f"Appearances: absent ({APPEARANCES_REASON})")
    assert lines[at + 1].startswith("Currencies:")
    assert APPEARANCES_NOTE not in lines


def test_constructed_appearances_not_in_the_file() -> None:
    raw = _base()
    del raw["collections"]["appearances"]
    assert any(line.startswith("Appearances: not in the file") for line in _lines(_load(raw)))


# ─── (c) a reason is clipped, never the string that refuses the file ─────────

RLO = chr(0x202E)
LRO = chr(0x202D)
PDF = chr(0x202C)
RLI = chr(0x2067)
PDI = chr(0x2069)
LRM = chr(0x200E)
ZWSP = chr(0x200B)
ZWJ = chr(0x200D)
LSEP = chr(0x2028)
PSEP = chr(0x2029)
NEL = chr(0x0085)
BOM = chr(0xFEFF)
ESC = chr(0x1B)
E_ACUTE = chr(0x00E9)
ASTRAL = chr(0x1F600)

# Each hostile character and how it is shown: the bytes the file holds for it
# (UTF-8, or the one invalid byte `luadata` kept as a lone surrogate).
HOSTILE = {
    "RLO": (RLO, "\\xe2\\x80\\xae"),
    "LRO": (LRO, "\\xe2\\x80\\xad"),
    "PDF": (PDF, "\\xe2\\x80\\xac"),
    "RLI": (RLI, "\\xe2\\x81\\xa7"),
    "PDI": (PDI, "\\xe2\\x81\\xa9"),
    "LRM": (LRM, "\\xe2\\x80\\x8e"),
    "ZWSP": (ZWSP, "\\xe2\\x80\\x8b"),
    "ZWJ": (ZWJ, "\\xe2\\x80\\x8d"),
    "LSEP": (LSEP, "\\xe2\\x80\\xa8"),
    "PSEP": (PSEP, "\\xe2\\x80\\xa9"),
    "NEL": (NEL, "\\xc2\\x85"),
    "BOM": (BOM, "\\xef\\xbb\\xbf"),
    "ESC": (ESC, "\\x1b"),
    "newline": ("\n", "\\x0a"),
    "tab": ("\t", "\\x09"),
    "DEL": ("\x7f", "\\x7f"),
    "NUL": ("\x00", "\\x00"),
    "e-acute": (E_ACUTE, "\\xc3\\xa9"),
    "astral": (ASTRAL, "\\xf0\\x9f\\x98\\x80"),
    "invalid-byte": (chr(0xDCFF), "\\xff"),
}


def _escaped_suffix(size: int, cut: bool) -> str:
    tail = ", and cut to at most 1024 characters" if cut else ""
    return (
        f" [the reason is {size} bytes in the file; shown with each byte outside printable "
        f"ASCII written as \\xHH and each backslash as \\\\{tail}; the file is unchanged]"
    )


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_constructed_reason_with_a_character_outside_printable_ascii_reads_escaped(
    name: str,
) -> None:
    char, escape = HOSTILE[name]
    reason = f"Core.lua:12: bad{char}CANARY"
    size = len(reason.encode("utf-8", "surrogateescape"))
    raw = _base()
    raw["customization"] = {"absent": reason}
    record = _load(raw)
    section = record.customization
    assert isinstance(section, labaddon.AbsentSection)
    assert section.absent == f"Core.lua:12: bad{escape}CANARY"
    assert section.absent_clipped == labaddon.ClippedReason(
        original_length=size, escaped=True, truncated=False
    )
    lines = _lines(record)
    assert not any(char in line for line in lines)
    assert all(_printable(line) for line in lines)
    expected = f"Customization: absent (Core.lua:12: bad{escape}CANARY)"
    assert expected + _escaped_suffix(size, cut=False) in lines
    # every other section still reads
    assert isinstance(record.gear, labaddon.Gear)
    assert any(line.startswith("Professions (by skill line)") for line in lines)


def test_constructed_invalid_utf8_byte_from_lua_text_shows_as_the_file_byte() -> None:
    data = b'WowLabCharDB = { ["schema"] = 1, ["gear"] = { ["absent"] = "bad \xff \xc3\xa9" } }'
    gear = labaddon.parse_char(data).gear
    assert isinstance(gear, labaddon.AbsentSection)
    assert gear.absent == "bad \\xff \\xc3\\xa9"
    assert gear.absent_clipped == labaddon.ClippedReason(
        original_length=8, escaped=True, truncated=False
    )


def test_constructed_long_ascii_reason_is_truncated_not_refused() -> None:
    reason = "Talents.lua:363: " + "x" * 3000
    raw = _base()
    raw["talents"]["legacy"] = {"absent": reason}
    record = _load(raw)
    assert record.talents is not None
    legacy = record.talents.legacy
    assert isinstance(legacy, labaddon.AbsentSection)
    assert legacy.absent == reason[: labaddon.REASON_LIMIT]
    assert legacy.absent_clipped == labaddon.ClippedReason(
        original_length=3017, escaped=False, truncated=True
    )
    lines = _lines(record)
    assert (
        f"Legacy candidates: absent ({reason[:1024]}) [the reason is 3017 bytes in the file; "
        "shown cut to its first 1024 characters; the file is unchanged]"
    ) in lines
    assert isinstance(record.gear, labaddon.Gear)


def test_constructed_long_reason_with_non_ascii_is_escaped_and_truncated() -> None:
    reason = (RLO + "x") * 2000
    raw = _base()
    raw["gear"] = {"absent": reason}
    record = _load(raw)
    gear = record.gear
    assert isinstance(gear, labaddon.AbsentSection)
    assert gear.absent_clipped == labaddon.ClippedReason(
        original_length=8000, escaped=True, truncated=True
    )
    assert len(gear.absent) <= labaddon.REASON_LIMIT
    assert gear.absent.startswith("\\xe2\\x80\\xaex\\xe2\\x80\\xaex")
    assert _printable(gear.absent) and RLO not in gear.absent
    assert f"Gear: absent ({gear.absent}){_escaped_suffix(8000, cut=True)}" in _lines(record)


def test_constructed_truncation_never_splits_an_escape() -> None:
    reason = "x" * 1020 + RLO + "tail"  # RLO is three bytes, twelve characters escaped
    text, clip = labaddon.clip_reason(reason)
    assert text == "x" * 1020 + "\\xe2"  # the second byte's escape does not fit
    assert clip == labaddon.ClippedReason(original_length=1027, escaped=True, truncated=True)
    text, clip = labaddon.clip_reason("x" * 1021 + E_ACUTE)
    assert text == "x" * 1021  # "\xc3" (four characters) does not fit in the last three
    assert clip is not None and clip.truncated
    text, clip = labaddon.clip_reason("x" * 1016 + E_ACUTE)
    assert text == "x" * 1016 + "\\xc3\\xa9"  # exactly 1024
    assert clip is not None and not clip.truncated


def test_constructed_backslash_is_doubled_only_when_the_reason_is_escaped() -> None:
    plain = "Interface\\AddOns\\WowLab\\Core.lua:12: attempt to index nil"
    assert labaddon.clip_reason(plain) == (plain, None)
    long_plain = plain + "x" * 2000  # only cut: backslashes stay single
    text, clip = labaddon.clip_reason(long_plain)
    assert text == long_plain[:1024] and clip is not None and not clip.escaped
    text, clip = labaddon.clip_reason(plain + E_ACUTE)
    assert text == "Interface\\\\AddOns\\\\WowLab\\\\Core.lua:12: attempt to index nil\\xc3\\xa9"
    assert clip is not None and clip.escaped and not clip.truncated
    # a literal backslash-x in an escaped reason is told apart from an escape
    literal, _ = labaddon.clip_reason("\\xe9" + E_ACUTE)
    assert literal == "\\\\xe9\\xc3\\xa9"


def test_constructed_reason_at_the_cap_is_untouched() -> None:
    reason = "x" * 1024
    assert labaddon.clip_reason(reason) == (reason, None)
    raw = _base()
    raw["customization"] = {"absent": reason}
    section = _load(raw).customization
    assert isinstance(section, labaddon.AbsentSection) and section.absent_clipped is None


def test_constructed_empty_reason_is_still_refused() -> None:
    raw = _base()
    raw["customization"] = {"absent": ""}
    _refused(raw, "WowLabCharDB.customization")


def test_constructed_surrogate_from_another_caller_does_not_raise() -> None:
    # Not reachable from a file (`luadata` makes only U+DC80..U+DCFF), but a
    # JSON input can hold any lone surrogate: it is escaped, never an error.
    text, clip = labaddon.clip_reason("a" + chr(0xD800) + "b")
    assert text == "a\\xed\\xa0\\x80b"
    assert clip == labaddon.ClippedReason(original_length=5, escaped=True, truncated=False)


@pytest.mark.parametrize("field", ["export_absent", "last_selected_config_absent"])
def test_constructed_talent_absent_reasons_are_clipped(field: str) -> None:
    raw = _base()
    talents = raw["talents"]["class"]
    talents.pop(field.removesuffix("_absent"), None)
    reason = "C_Traits.GenerateImportString raised an error: " + LSEP + "y" * 2000
    size = len(reason.encode())
    talents[field] = reason
    record = _load(raw)
    assert record.talents is not None
    section = record.talents.class_
    assert isinstance(section, labaddon.ClassTalents)
    clip = getattr(section, f"{field}_clipped")
    assert clip == labaddon.ClippedReason(original_length=size, escaped=True, truncated=True)
    lines = _lines(record)
    assert all(_printable(line) for line in lines)
    label = "export string" if field == "export_absent" else "last selected saved loadout"
    shown = [line for line in lines if line.startswith(f"  {label}: absent (")]
    assert len(shown) == 1
    assert "\\xe2\\x80\\xa8" in shown[0]
    assert shown[0].endswith(_escaped_suffix(size, cut=True))


@pytest.mark.parametrize(
    "path", ["customization", "gear.average", "client", "talents.class.config"]
)
def test_constructed_clip_flag_in_the_file_is_refused(path: str) -> None:
    raw = _base()
    parent = raw
    *heads, last = path.split(".")
    for part in heads:
        parent = parent[part]
    parent[last] = {
        "absent": "reason",
        "absent_clipped": {"original_length": 5000, "escaped": True, "truncated": True},
    }
    _refused(raw, "absent_clipped is written by this reader, never by the addon")


def test_constructed_talent_clip_flag_in_the_file_is_refused() -> None:
    raw = _base()
    raw["talents"]["class"]["export_absent_clipped"] = {
        "original_length": 5000,
        "escaped": False,
        "truncated": True,
    }
    del raw["talents"]["class"]["export"]
    raw["talents"]["class"]["export_absent"] = "reason"
    _refused(raw, "export_absent_clipped is written by this reader, never by the addon")


def test_constructed_clipped_record_round_trips_through_json() -> None:
    raw = _base()
    raw["customization"] = {"absent": RLO + "x" * 3000}  # 3003 bytes
    raw["talents"]["class"].pop("export")
    raw["talents"]["class"]["export_absent"] = "err" + E_ACUTE
    record = _load(raw)
    dumped = record.model_dump_json()
    assert _printable(dumped)
    again = labaddon.CharDBV1.model_validate_json(dumped)
    assert again == record
    customization = again.customization
    assert isinstance(customization, labaddon.AbsentSection)
    assert customization.absent_clipped == labaddon.ClippedReason(
        original_length=3003, escaped=True, truncated=True
    )


def test_constructed_unclipped_records_dump_no_clip_keys() -> None:
    record = _load(_base())
    assert "_clipped" not in record.model_dump_json()


@pytest.mark.parametrize(
    ("original_length", "escaped", "truncated"), [(0, True, True), (5, False, False)]
)
def test_constructed_clip_flag_shape_is_checked(
    original_length: int, escaped: bool, truncated: bool
) -> None:
    with pytest.raises(ValueError):
        labaddon.ClippedReason(
            original_length=original_length, escaped=escaped, truncated=truncated
        )


# ─── (c) every other string keeps its limits ─────────────────────────────────


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("talents", "legacy", "configs", 0, "found_by", 0), "type:Generic" + E_ACUTE),
        (("talents", "legacy", "configs", 0, "found_by", 0), "type:" + "G" * 2000),
        (("gear", "slots", 0, "link"), "|cnIQ2:|Hitem:9749::|h[" + "B" * 1100 + "]|h|r"),
        (("gear", "slots", 0, "link"), "|cnIQ2:|Hitem:9749::|h[B" + RLO + "]|h|r"),
        (("talents", "class", "export"), "A" * 5000),
        (("client", "version"), "1.60.1" + E_ACUTE),
        (("skip",), ["gear" + ZWSP]),
        (("gear", "events_unregistered"), ["PLAYER_EQUIPMENT_CHANGED" + RLO]),
    ],
)
def test_constructed_other_text_fields_keep_their_limits(
    path: tuple[str | int, ...], value: Any
) -> None:
    raw = _base()
    parent: Any = raw
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = value
    message = _refused(raw, "does not fit the schema-1 model")
    assert RLO not in message and E_ACUTE not in message


def test_constructed_text_in_an_unknown_key_is_still_refused() -> None:
    raw = _base()
    raw["customization"] = {"absent": "reason", "note": "CANARY" + RLO}
    assert "CANARY" not in _refused(raw, "the unknown key note holds text")
