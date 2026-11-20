"""The fixes from the owner's first schema-5 run (M20-16; 71037, 2026-11-20).

What `char show` printed after one login and logout with the M20-13 addon:
`Macros: absent (MAX_ACCOUNT_MACROS is not a number)`, a realm absent
(`GetRealmName` gave nothing usable), an action-bar line still saying a
macro's text is never recorded, and a key-bindings line that could not say
whether the binding list holds any command that is not a plain name. The
addon now records where the macro slot count came from, the realm's source,
and how many listed commands are not plain; the reader takes the new keys
and the desk says what each record holds.

CONSTRUCTED INPUT: each record starts from the real 70450 schema-4 record
(`macos-70450/`, index row in `lab/core/tests/fixtures/README.md`) with the
schema-5 keys added the way `lab/addon/WowLab/` writes them. Every macro,
name, realm, guild and GUID below is invented.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECORD_70450 = (
    "macos-70450/forever/WTF/Account/90000001#6/1/Dangling-Participle/SavedVariables/WowLab.lua"
)
GUID = "Player-0000-0A0B0C0D"


def _v4() -> dict[str, Any]:
    raw = luadata.parse((FIXTURES / RECORD_70450).read_bytes()).to_python()["WowLabCharDB"]  # type: ignore[index]
    assert isinstance(raw, dict) and raw["schema"] == 4
    return copy.deepcopy(raw)


def _raw(**macros: Any) -> dict[str, Any]:
    """The real 70450 record as the M20-16 addon would write it
    (constructed), with one macro on a bar."""
    raw = _v4()
    raw["schema"] = 5
    raw["identity"].update({"guid": GUID, "name": "Inventedname", "realm": "Invented Realm"})
    raw["identity"]["realm_from"] = "realm_name"
    raw["identity"]["guild_absent"] = "the first return of GetGuildInfo was not a non-empty string"
    bindings = raw["bindings"]
    for key in ("commands_left_out", "keys_left_out", "probes"):
        bindings.pop(key, None)
    bindings["list"].append({"command": "MACRO Testmacro", "keys": ["SHIFT-9"]})
    bindings["commands"] += 1
    bindings["dropped"] = 0
    raw["macros"] = {
        "account": 1,
        "character": 1,
        "first_character_index": 121,
        "account_slots_from": "fallback",
        "list": [
            {"index": 1, "scope": "account", "name": "Testmacro", "body": "/cast Invented Spell"},
            {"index": 121, "scope": "character", "name": "Idle", "body": "/sit"},
        ],
        "dropped": 0,
        **macros,
    }
    bars = raw["actionbars"]
    bars.pop("probes")
    bars["dropped"] = 0
    if not any(s.get("type") == "macro" for s in bars["slots"]):
        free = next(
            n
            for n in range(bars["first_slot"], bars["last_slot"] + 1)
            if all(s["slot"] != n for s in bars["slots"])
        )
        bars["slots"].append({"slot": free, "type": "macro", "id": 1})
        bars["slots"].sort(key=lambda s: s["slot"])
    raw["spellbook"].pop("probes")
    raw["spellbook"]["dropped"] = 0
    return raw


def _char(raw: dict[str, Any]) -> labaddon.CharDBV5:
    char = labaddon.load_char(raw)
    assert isinstance(char, labaddon.CharDBV5)
    return char


def _text(raw: dict[str, Any]) -> str:
    return "\n".join(labaddon.describe(_char(raw)))


def _macro_line(raw: dict[str, Any]) -> str:
    lines = [line for line in labaddon.describe(_char(raw)) if "macro slots:" in line]
    assert len(lines) == 1, lines
    return lines[0]


# 1. Macros without MAX_ACCOUNT_MACROS ------------------------------------------


def test_constructed_macros_with_the_fallback_slot_count_read_and_say_so() -> None:
    char = _char(_raw())
    assert isinstance(char.macros, labaddon.Macros)
    assert char.macros.account_slots_from == "fallback"
    assert [m.index for m in char.macros.list_] == [1, 121]
    assert (
        "  this character's macros asked from index 121: the game's count of account macro "
        "slots is loaded only once the macro window has been opened, so the addon assumed 120 "
        "[verify]"
    ) in labaddon.describe(char)


def test_constructed_macros_from_the_client_constant_say_so() -> None:
    lines = labaddon.describe(_char(_raw(account_slots_from="MAX_ACCOUNT_MACROS")))
    assert (
        "  this character's macros asked from index 121, after the game's own count of "
        "account macro slots"
    ) in lines


def test_constructed_macros_written_before_m20_16_add_no_slot_line() -> None:
    raw = _raw()
    del raw["macros"]["account_slots_from"]
    text = _text(raw)
    assert "Macros: 1 account-wide macro and 1 macro of this character's" in text
    assert "asked from index" not in text


def test_constructed_unknown_slot_source_is_refused() -> None:
    with pytest.raises(labaddon.LabAddonError, match="account_slots_from"):
        labaddon.load_char(_raw(account_slots_from="guessed"))


# 3. The action-bar line says where a macro's text is ----------------------------


def test_constructed_schema_5_action_bars_point_at_the_macros_section() -> None:
    line = _macro_line(_raw())
    assert line.endswith("; each macro's name and body are in the Macros section below")
    assert "never recorded" not in line


def test_constructed_schema_5_action_bars_say_why_the_macros_section_is_absent() -> None:
    raw = _raw()
    raw["macros"] = {"absent": "MAX_ACCOUNT_MACROS is not a number", "dropped": 0}
    line = _macro_line(raw)
    assert line.endswith(
        "; a macro's name and body would be in the Macros section, which is absent "
        "(MAX_ACCOUNT_MACROS is not a number)"
    ), line


def test_constructed_schema_5_action_bars_without_a_macros_section() -> None:
    raw = _raw()
    del raw["macros"]
    line = _macro_line(raw)
    assert "a macro's name and body would be in the Macros section: " in line
    assert "never recorded" not in line


def test_the_real_schema_4_action_bars_keep_the_old_words() -> None:
    """Control: a schema-4 record's macro text was never recorded."""
    raw = _v4()
    assert any(s.get("type") == "macro" for s in raw["actionbars"]["slots"])
    char = labaddon.load_char(raw)
    lines = [line for line in labaddon.describe(char) if "macro slots:" in line]
    assert lines and lines[0].endswith("; a macro's name and body are never recorded")


# 2. The realm's three sources --------------------------------------------------


@pytest.mark.parametrize(
    ("source", "realm", "normalized", "words"),
    [
        ("realm_name", "Invented Realm", None, "realm Invented Realm, "),
        (
            "normalized_realm_name",
            "InventedRealm",
            True,
            "realm InventedRealm (written without spaces: the game gave no realm name with "
            "spaces), ",
        ),
        (
            "full_name",
            "InventedRealm",
            True,
            "realm InventedRealm (written without spaces: taken from the character's full "
            "name; the game gave no realm name), ",
        ),
    ],
    ids=["constructed-realm-name", "constructed-normalized", "constructed-full-name"],
)
def test_constructed_realm_from_each_source(
    source: str, realm: str, normalized: bool | None, words: str
) -> None:
    raw = _raw()
    raw["identity"].update({"realm": realm, "realm_from": source})
    if normalized is not None:
        raw["identity"]["realm_normalized"] = normalized
    char = _char(raw)
    assert isinstance(char.identity, labaddon.IdentityV5)
    assert (char.identity.realm, char.identity.realm_from) == (realm, source)
    assert char.identity.realm_normalized is normalized
    assert labaddon.unknown_keys(char) == []
    assert f"Character: name Inventedname, {words}" in _text(raw)


def test_constructed_realm_absent_from_all_three_sources() -> None:
    raw = _raw()
    del raw["identity"]["realm"]
    del raw["identity"]["realm_from"]
    raw["identity"]["realm_absent"] = (
        "the return of GetRealmName was not a non-empty string; the return of "
        "GetNormalizedRealmName was not a non-empty string; the second return of UnitFullName "
        "was not a non-empty string"
    )
    text = _text(raw)
    assert "realm absent (the return of GetRealmName was not a non-empty string; " in text


def test_constructed_realm_found_on_a_later_gather_replaces_the_absent_one() -> None:
    """The addon keeps the identity section dirty while it has no realm
    (`again_without`, tests/addon) and a later gather writes a whole new
    record: the file holds the realm and no `realm_absent`, and the desk
    prints the realm."""
    first = _raw()
    del first["identity"]["realm"]
    del first["identity"]["realm_from"]
    first["identity"]["realm_absent"] = "the return of GetRealmName was not a non-empty string"
    assert "realm absent (" in _text(first)
    later = _raw()
    later["identity"].update({"realm": "InventedRealm", "realm_from": "full_name"})
    later["identity"]["realm_normalized"] = True
    char = _char(later)
    assert isinstance(char.identity, labaddon.IdentityV5)
    assert char.identity.realm_absent is None
    assert "realm absent" not in _text(later)


def test_constructed_unknown_realm_source_is_refused() -> None:
    raw = _raw()
    raw["identity"]["realm_from"] = "guessed"
    with pytest.raises(labaddon.LabAddonError, match="realm_from"):
        labaddon.load_char(raw)


# 4. How many listed commands are not plain -------------------------------------


def _not_plain_line(raw: dict[str, Any]) -> str:
    lines = [line for line in labaddon.describe(_char(raw)) if "not a plain upper-case" in line]
    assert len(lines) == 1, lines
    return lines[0]


def test_constructed_not_plain_commands_with_and_without_a_key() -> None:
    raw = _raw()
    raw["bindings"]["commands_not_plain"] = 3
    assert _not_plain_line(raw) == (
        "  not a plain upper-case name (as in MACRO x): 3 commands of those listed, 1 with a "
        "key and 2 without"
    )


def test_constructed_not_plain_count_written_before_m20_16() -> None:
    assert _not_plain_line(_raw()) == (
        "  not a plain upper-case name (as in MACRO x): 1 with a key; how many without one is "
        "not in this record (written by an older lab-addon)"
    )


@pytest.mark.parametrize(
    "count",
    [0, 10_000],
    ids=["constructed-below-the-keyed-ones", "constructed-above-the-commands"],
)
def test_constructed_not_plain_count_that_cannot_be_is_refused(count: int) -> None:
    raw = _raw()
    raw["bindings"]["commands_not_plain"] = count
    with pytest.raises(labaddon.LabAddonError, match="commands_not_plain"):
        labaddon.load_char(raw)
