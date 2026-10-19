"""`wowlab_core.labaddon` reads schema 4, the addon format M15-01 introduces.

Schema 4 is schema 3 plus one section, `identity`: `race_id`, `sex_raw`
(`UnitSex()`'s value, kept raw), `class_id` and `class_file`, each exactly
one of the value and `<field>_absent` (`lab/addon/WowLab/Identity.lua`).
`identity_of` gives the reports' `CharIdentity`, deriving `sex`
(`Enum.UnitSex`) from `sex_raw`.

The real schema-1 and schema-3 captures are read unchanged and report
`identity` absent. No real schema-2 capture exists (the M11-29 addon's
first capture was already schema 3), so schema 2 is the real M11-23 record
with `schema` set to 2, as `test_labaddon_barber_record_m11_29.py` builds it.
No real schema-4 capture exists yet either: it is the owner's step
(`docs/handoffs/M15-01.md`); until it is committed with its provenance row,
schema 4 is graded on the real M13-05 record (1.60.1.70425, index row in
`fixtures/README.md`) with `schema` set to 4 and an `identity` table added
in the shape the addon's source writes. Every such case is `constructed`
in its test id (L8: boundary and hostile cases). The reasons are read from
`Identity.lua`, not typed here, so the reader and the addon cannot drift.
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
IDENTITY_LUA = ROOT / "lab" / "addon" / "WowLab" / "Identity.lua"
RECORD_70425 = (
    "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)
RECORD_70058 = (
    "macos-70058/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
)
REAL = {
    "schema-1-70009-a": "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua",
    "schema-1-70009-b": "macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua",
    "schema-1-70058": RECORD_70058,
    "schema-3-70425": RECORD_70425,
}
FIELDS = ("race_id", "sex", "sex_raw", "class_id", "class_file")


def _raw(relative: str) -> dict[str, Any]:
    values = luadata.parse((FIXTURES / relative).read_bytes()).to_python()
    record = values[labaddon.CHAR_VARIABLE]
    assert isinstance(record, dict)
    return record


def _addon_reasons() -> dict[str, list[str]]:
    """Each `<field>_absent` reason `Identity.lua` writes, by field."""
    text = IDENTITY_LUA.read_text(encoding="utf-8")
    found: dict[str, list[str]] = {}
    for field, reason in re.findall(r'record\.(\w+)_absent = "([^"]+)"', text):
        found.setdefault(field, []).append(reason)
    return found


# The identity a warlock (class 9 on Retail, from memory) would give; only
# its shape matters here, and the M11-23 record holds race_id 5.
GOOD = {"race_id": 5, "sex_raw": 2, "class_id": 9, "class_file": "WARLOCK"}


def _v4(identity: object) -> dict[str, Any]:
    raw = copy.deepcopy(_raw(RECORD_70425))
    raw["schema"] = 4
    raw["identity"] = copy.deepcopy(identity)
    return raw


def _identity(identity: object) -> labaddon.CharIdentity:
    return labaddon.identity_of(labaddon.load_char(_v4(identity)))


# ─── schema 1 to 3: read as before, identity absent ──────────────────────────


@pytest.mark.parametrize("name", sorted(REAL))
def test_real_capture_reports_identity_absent(name: str) -> None:
    raw = _raw(REAL[name])
    char = labaddon.load_char(raw)
    assert not isinstance(char, labaddon.CharDBV4)
    assert "identity" not in char.model_dump(by_alias=True)
    got = labaddon.identity_of(char)
    reason = (
        f"the record is schema {raw['schema']}; identity is recorded from schema 4 (the "
        "lab-addon from M15-01: install it with wowlab addon install lab, then log in and out "
        "on this character)"
    )
    for field in FIELDS:
        assert getattr(got, field) is None
        assert getattr(got, f"{field}_absent") == reason
    assert not any(line.startswith("Identity:") for line in labaddon.describe(char))


def test_constructed_schema_2_reports_identity_absent() -> None:
    raw = copy.deepcopy(_raw(RECORD_70058))
    raw["schema"] = 2
    char = labaddon.load_char(raw)
    assert type(char) is labaddon.CharDBV2
    got = labaddon.identity_of(char)
    assert got.race_id is None
    assert got.race_id_absent is not None and got.race_id_absent.startswith(
        "the record is schema 2; identity is recorded from schema 4"
    )
    assert not any(line.startswith("Identity:") for line in labaddon.describe(char))


def test_constructed_schema_3_record_reads_the_same_as_schema_4_without_identity() -> None:
    """Schema 4 changes nothing but `identity`: the real 70425 record read as
    schema 4 dumps exactly as it does as schema 3, apart from the number."""
    as_3 = labaddon.load_char(_raw(RECORD_70425))
    raw = copy.deepcopy(_raw(RECORD_70425))
    raw["schema"] = 4
    as_4 = labaddon.load_char(raw)
    assert type(as_3) is labaddon.CharDBV3 and type(as_4) is labaddon.CharDBV4
    assert isinstance(as_4, labaddon.CharDBV3)  # every schema-3 reader takes it
    dump_3 = as_3.model_dump(by_alias=True)
    dump_4 = as_4.model_dump(by_alias=True)
    assert dump_4.pop("identity") is None
    assert dump_4.pop("schema") == 4 and dump_3.pop("schema") == 3
    assert dump_3 == dump_4


def test_constructed_text_gains_exactly_one_line() -> None:
    """`char show`'s record lines for schema 4 are schema 3's plus one
    `Identity:` line, after the spec."""
    as_3 = labaddon.describe(labaddon.load_char(_raw(RECORD_70425)))
    as_4 = labaddon.describe(labaddon.load_char(_v4(GOOD)))
    added = [line for line in as_4 if line not in as_3]
    assert added == [
        "Identity: class WARLOCK (class id 9), race id 5, body type 0 (Enum.UnitSex, from "
        "UnitSex 2)"
    ]
    assert len(as_4) == len(as_3) + 1
    spec = next(i for i, line in enumerate(as_4) if line.startswith("Spec:"))
    assert as_4.index(added[0]) > spec
    assert [line for line in as_4 if line != added[0]] == as_3


# ─── schema 4 ────────────────────────────────────────────────────────────────


def test_constructed_schema_4_identity_is_read() -> None:
    char = labaddon.load_char(_v4(GOOD))
    assert isinstance(char, labaddon.CharDBV4)
    assert isinstance(char.identity, labaddon.Identity)
    got = labaddon.identity_of(char)
    assert got == labaddon.CharIdentity(
        race_id=5, sex=0, sex_raw=2, class_id=9, class_file="WARLOCK"
    )
    assert labaddon.unknown_keys(char) == []


@pytest.mark.parametrize(
    ("raw", "sex", "reason"),
    [(2, 0, None), (3, 1, None), (1, None, "UnitSex returned 1"), (0, None, "UnitSex returned 0")],
)
def test_constructed_sex_is_derived_from_unitsex(
    raw: int, sex: int | None, reason: str | None
) -> None:
    got = _identity({**GOOD, "sex_raw": raw})
    assert got.sex_raw == raw
    assert got.sex == sex
    assert got.sex_absent == reason


def test_constructed_every_addon_reason_is_read_and_reported() -> None:
    reasons = _addon_reasons()
    assert sorted(reasons) == sorted(labaddon.IDENTITY_FIELDS)
    for field, texts in reasons.items():
        for text in texts:
            got = _identity(
                {**{k: v for k, v in GOOD.items() if k != field}, f"{field}_absent": text}
            )
            assert getattr(got, field) is None
            assert getattr(got, f"{field}_absent") == text
            if field == "sex_raw":
                assert got.sex_absent == f"no sex_raw to derive it from: {text}"


def test_constructed_whole_section_absent() -> None:
    char = labaddon.load_char(_v4({"absent": labaddon.SWITCHED_OFF}))
    got = labaddon.identity_of(char)
    for field in FIELDS:
        assert getattr(got, f"{field}_absent") == (
            f"the identity section is absent ({labaddon.SWITCHED_OFF})"
        )
    assert "Identity: absent (switched off by the owner)" in labaddon.describe(char)


def test_constructed_section_missing_from_a_schema_4_file() -> None:
    raw = _v4(GOOD)
    del raw["identity"]
    got = labaddon.identity_of(labaddon.load_char(raw))
    assert got.class_file_absent == f"identity is {labaddon.NOT_IN_FILE}"


# ─── hostile values: set aside with a reason, never a crash ──────────────────


def test_constructed_bad_values_are_set_aside_not_refused() -> None:
    """The ticket's three: a non-numeric race id, a class id out of range, a
    missing class file. The file still reads; each is absent with a reason,
    and the bad value is never echoed."""
    char = labaddon.load_char(_v4({"race_id": "Orc<script>", "sex_raw": 3, "class_id": 2**31}))
    got = labaddon.identity_of(char)
    assert got.race_id is None
    assert got.race_id_absent == (
        f"race_id is not a whole number from 1 to {labaddon.ID_MAX}; set aside by the reader"
    )
    assert got.class_id is None
    assert got.class_id_absent == (
        f"class_id is not a whole number from 1 to {labaddon.ID_MAX}; set aside by the reader"
    )
    assert got.class_file is None
    assert got.class_file_absent == "the record holds neither class_file nor class_file_absent"
    assert got.sex == 1
    dumped = char.model_dump_json(by_alias=True) + got.model_dump_json()
    assert "Orc" not in dumped and "script" not in dumped
    assert char.gear is not None  # the rest of the file is still read


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("race_id", 0),
        ("race_id", -5),
        ("race_id", 1.5),
        ("race_id", True),
        ("race_id", {"1": 2}),
        ("race_id", 2**31),
        ("class_id", 0),
        ("class_id", 3.0),
        ("class_id", "9"),
        ("sex_raw", 2**60),
        ("sex_raw", 2.0),
        ("sex_raw", "male"),
        ("class_file", "warlock"),
        ("class_file", ""),
        ("class_file", "A" * 33),
        ("class_file", 9),
        ("class_file", "WAR" + chr(0x202E) + "LOCK"),
    ],
)
def test_constructed_value_that_does_not_fit_is_set_aside(field: str, value: object) -> None:
    char = labaddon.load_char(_v4({**GOOD, field: value}))
    assert isinstance(char, labaddon.CharDBV4) and isinstance(char.identity, labaddon.Identity)
    assert getattr(char.identity, field) is None
    reason = getattr(char.identity, f"{field}_set_aside")
    assert reason is not None and reason.endswith("; set aside by the reader")
    got = labaddon.identity_of(char)
    assert getattr(got, field) is None
    assert getattr(got, f"{field}_absent") == reason
    for other in labaddon.IDENTITY_FIELDS:
        if other != field:
            assert getattr(got, other) == GOOD[other]


def test_constructed_value_beside_its_reason_is_set_aside() -> None:
    got = _identity({**GOOD, "class_id_absent": "the client has no UnitClass function"})
    assert got.class_id is None
    assert got.class_id_absent == (
        "the client has no UnitClass function (class_id is written beside class_id_absent; set "
        "aside by the reader)"
    )


@pytest.mark.parametrize("bad", [7, "", {"a": 1}, True])
def test_constructed_reason_that_is_not_text_is_set_aside(bad: object) -> None:
    raw = {k: v for k, v in GOOD.items() if k != "race_id"}
    got = _identity({**raw, "race_id_absent": bad})
    assert got.race_id_absent == "race_id_absent is not a non-empty text; set aside by the reader"


def test_constructed_long_reason_is_clipped_not_refused() -> None:
    raw = {k: v for k, v in GOOD.items() if k != "class_file"}
    got = _identity({**raw, "class_file_absent": "x" * 2000})
    assert got.class_file_absent is not None
    assert got.class_file_absent.startswith("x" * labaddon.REASON_LIMIT + " [the reason is 2000")


def test_constructed_set_aside_key_in_the_file_is_refused() -> None:
    with pytest.raises(labaddon.LabAddonError, match="race_id_set_aside is written by this reader"):
        labaddon.load_char(_v4({**GOOD, "race_id_set_aside": "planted"}))


def test_constructed_identity_holding_unknown_text_is_refused_as_elsewhere() -> None:
    with pytest.raises(labaddon.LabAddonError, match="holds text"):
        labaddon.load_char(_v4({**GOOD, "class_name": "Warlock"}))


# ─── JSON ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "identity",
    [GOOD, {"race_id": "x", "sex_raw": 7, "class_file": "WARRIOR"}, {"absent": "error: boom"}],
    ids=["constructed-good", "constructed-set-aside", "constructed-absent"],
)
def test_json_round_trip(identity: object) -> None:
    char = labaddon.load_char(_v4(identity))
    again = labaddon.CharDBV4.model_validate_json(char.model_dump_json(by_alias=True))
    assert again == char
    got = labaddon.identity_of(char)
    assert labaddon.CharIdentity.model_validate_json(got.model_dump_json()) == got
    assert labaddon.identity_of(again) == got


def test_constructed_set_aside_is_left_out_of_json_when_unset() -> None:
    char = labaddon.load_char(_v4(GOOD))
    dumped = json.loads(char.model_dump_json(by_alias=True))["identity"]
    assert {k: v for k, v in dumped.items() if v is not None} == GOOD
    assert not [k for k in dumped if k.endswith(("_set_aside", "_clipped"))]


def test_charidentity_holds_exactly_one_of_value_and_reason() -> None:
    with pytest.raises(ValueError, match="exactly one of race_id and race_id_absent"):
        labaddon.CharIdentity(
            race_id=1,
            race_id_absent="x",
            sex=0,
            sex_raw=2,
            class_id=1,
            class_file="WARRIOR",
        )
