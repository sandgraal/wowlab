"""The views that use a schema-5 record's own text (M20-13).

`char show` (labaddon.describe), the keymap views, `macros check` and the
combat-log reader behind `binds coach` read what the schema-5 lab-addon keeps
of the owner's own account: the character's name, realm, guild and GUID,
every macro with its index, every binding command as the client returns it.

CONSTRUCTED INPUT (the owner's schema-5 capture is taken after M20-13 merges):
each record starts from the real 70450 schema-4 record with the schema-5
keys added (as in parser/test_labaddon_schema_5_constructed_m20_13.py), and
each combat-log line is made up in the real log's format-22 layout. Every
macro, name, realm, guild and GUID is invented.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from wowlab_core import combatlog, keymap, labaddon, luadata, macrocheck, wtfconfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECORD_70450 = (
    "macos-70450/forever/WTF/Account/90000001#6/1/Dangling-Participle/SavedVariables/WowLab.lua"
)
GUID = "Player-0000-0A0B0C0D"
OTHER_GUID = "Player-0000-0E0E0E0E"


def _v5(macro_slot: int | None = 1) -> labaddon.CharDBV5:
    raw: dict[str, Any] = copy.deepcopy(
        luadata.parse((FIXTURES / RECORD_70450).read_bytes()).to_python()["WowLabCharDB"]  # type: ignore[index]
    )
    raw["schema"] = 5
    raw["written_at"] = 1763600002
    raw["written_by"] = "logout"
    raw["identity"].update({"guid": GUID, "name": "Inventedname", "realm": "Invented Realm"})
    raw["identity"]["guild_absent"] = "the first return of GetGuildInfo was not a non-empty string"
    bindings = raw["bindings"]
    for key in ("commands_left_out", "keys_left_out", "probes"):
        bindings.pop(key, None)
    bindings["list"].append({"command": "MACRO Testmacro", "keys": ["SHIFT-9"]})
    bindings["commands"] += 1
    bindings["dropped"] = 0
    raw["macros"] = {
        "account": 2,
        "character": 0,
        "first_character_index": 121,
        "list": [
            {"index": 1, "scope": "account", "name": "Testmacro", "body": "/cast Invented Spell\n"},
            {"index": 2, "scope": "account", "name": "Idle", "body": "/sit"},
        ],
        "dropped": 0,
    }
    bars = raw["actionbars"]
    bars.pop("probes")
    bars["dropped"] = 0
    bars["slots"] = [s for s in bars["slots"] if s.get("type") != "macro"]
    if macro_slot is not None:
        free = next(
            n
            for n in range(bars["first_slot"], bars["last_slot"] + 1)
            if all(s["slot"] != n for s in bars["slots"])
        )
        bars["slots"].append({"slot": free, "type": "macro", "id": macro_slot})
        bars["slots"].sort(key=lambda s: s["slot"])
    for section in ("spellbook",):
        raw[section].pop("probes")
        raw[section]["dropped"] = 0
    char = labaddon.load_char(raw)
    assert isinstance(char, labaddon.CharDBV5)
    return char


def test_constructed_char_show_prints_the_own_text() -> None:
    lines = labaddon.describe(_v5())
    text = "\n".join(lines)
    assert (
        "Character: name Inventedname, realm Invented Realm, no guild, or the guild not "
        "loaded yet [verify]; game ID kept (in --json)"
    ) in lines
    assert "Written: 2025-11-20 00:53 UTC at logout (a crash keeps the write before it)" in lines
    assert "Macros: 2 account-wide macros and 0 macros of this character's" in text
    assert "every command the game's binding list gives: 1 macro or click command" in text
    assert "probes: none recorded" not in text
    assert "dropped: 0 values" in text
    assert GUID not in text  # the GUID itself is in --json only


def test_constructed_keymap_names_a_macro_slot_by_the_stored_macro() -> None:
    char = _v5()
    got = keymap.join_record(account=None, character=None, record=char)
    rows: list[keymap.KeyRow | keymap.UnreachedSlot] = [*got.keys, *got.unreached_slots]
    words = [r.does for r in rows if r.holds is not None and r.holds.type == "macro"]
    assert words == ["runs macro Testmacro (macro 1, by its index [verify])"], words


def _cache(*macros: tuple[str, str]) -> list[macrocheck.SourceMacro]:
    lines: list[bytes] = []
    for i, (name, body) in enumerate(macros, start=1):
        lines.append(b'VER 3 %016X "%s" "134400"' % (i, name.encode()))
        lines += [line.encode() for line in body.split("\n")]
        lines.append(b"END")
    doc = wtfconfig.parse_macros(b"\r\n".join(lines) + b"\r\n")
    return [macrocheck.SourceMacro(scope="account", file="x", macro=m) for m in doc.macros]


def test_constructed_macros_check_judges_by_the_stored_macros() -> None:
    cache = _cache(("Testmacro", "/cast Invented Spell"), ("Idle", "/sit"), ("Gone", "/wave"))
    result = macrocheck.check(cache, _v5())
    off_bar = [f for f in result.findings if f.rule == "not-on-action-bar"]
    assert [f.name for f in off_bar] == ["Idle"]
    assert "macro 2 in the lab-addon's record" in off_bar[0].detail
    reasons = [c.reason for c in result.cannot_judge if c.rule == "not-on-action-bar"]
    assert reasons == [
        "1 macro(s) match no macro in the lab-addon's record by name and body: the file and "
        "the record were written at different times"
    ]
    assert result.on_no_bar_at_least == 1


def test_constructed_macros_check_falls_back_without_stored_macros() -> None:
    """Control: a schema-4 record keeps the M13-06 reasoning."""
    char = _v5()
    v4 = labaddon.load_char(
        luadata.parse((FIXTURES / RECORD_70450).read_bytes()).to_python()["WowLabCharDB"]
    )
    cache = _cache(("Idle", "/sit"))
    assert [f.rule for f in macrocheck.check(cache, char).findings] == ["not-on-action-bar"]
    assert "not-on-action-bar" not in [f.rule for f in macrocheck.check(cache, v4).findings]


ADVANCED = "Player-9999-00000001,0000000000000000,183,183,37,0,84,0,0,0,0,322,322,35,2689.43,-919.91,1420,2.5087,1"
HEADER = "4/1/2026 02:16:30.666-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,1.60.1,PROJECT_ID,18"


def _cast(source: str) -> str:
    fields = [
        source,
        '"Constructed-Unit-"',
        "0x511",
        "0x80000000",
        "Creature-0-1-0-2-1554-0000000000",
        '"Constructed Target"',
        "0x10a48",
        "0x80000000",
        "172",
        '"Constructed Bolt"',
        "0x20",
    ]
    return f"4/1/2026 02:16:37.862-4  SPELL_CAST_SUCCESS,{','.join(fields)},{ADVANCED}"


def test_constructed_casts_are_picked_by_the_stored_guid() -> None:
    data = "".join(
        f"{line}\r\n" for line in (HEADER, _cast(GUID), _cast(OTHER_GUID), _cast(GUID.lower()))
    ).encode("utf-8")
    plain = combatlog.cast_counts(combatlog.tokenize(data))
    assert (plain.mine, plain.by_guid, plain.other_guid) == (3, False, 0)
    picked = combatlog.cast_counts(combatlog.tokenize(data), GUID)
    assert (picked.mine, picked.by_guid, picked.other_guid, picked.sources) == (2, True, 1, 2)
