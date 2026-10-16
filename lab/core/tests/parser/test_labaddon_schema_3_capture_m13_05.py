"""`wowlab_core.labaddon` on the first real schema-3 capture (M13-05, M11-29).

The owner's capture of 2026-10-16 (Forever beta 1.60.1.70425, macOS; index
rows in `fixtures/README.md`, run folder `macos-70425/`): the account
`WowLabDB` and one level-15 warlock's `WowLabCharDB`, written by the
schema-3 addon (#176) in one session that also held the M11-29 barber-shop
check. These tests read it beside the committed schema-1 captures (M11-03 and
M11-23; schema 2 has no real capture, and its cases are constructed in
`test_labaddon_schema_3_m13_05.py` and `test_labaddon_barber_record_m11_29.py`),
pin what the capture settled for the `docs/LAB_FORMATS.md` amendment of
2026-10-16, and check that the older captures still read exactly as before.
Bytes are read here and parsed; the committed tree is never handed to a
reader that opens files.
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUN = "macos-70425/forever/WTF/Account/90000001#6"
ACCOUNT_3 = f"{RUN}/SavedVariables/WowLab.lua"
CHAR_3 = f"{RUN}/1/Labchard-Labrealme/SavedVariables/WowLab.lua"

ACCOUNT_1 = "macos/forever/WTF/Account/90000001#6/SavedVariables/WowLab.lua"
RECORD_70058 = (
    "macos-70058/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
)
OLDER = {
    # The committed schema-1 captures and the SHA-256 of their JSON as the
    # reader gave it when this capture landed. A change here means an older
    # capture no longer reads identically.
    "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua": (
        "a2e3f244ff4e98633f392c79ebc5f4bc1ea4d1ab422dd14d0702d67b69de63fc"
    ),
    "macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua": (
        "76865d65d092eb7d67eb7dac35ccc2c7204f2ac8422121b65d161e8d68a3a941"
    ),
    "macos-70058/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua": (
        "ac3eac0fda04a4ab5b7b3050990e319bee9eed76532f75a41018a7bd9cc223b7"
    ),
}

# The schema-3 sections and the APIs each asked, in the addon's order.
PROBES = {
    "spellbook": [
        "C_SpellBook.GetNumSpellBookSkillLines",
        "C_SpellBook.GetSpellBookSkillLineInfo",
        "C_SpellBook.GetSpellBookItemInfo",
        "C_SpellBook.GetSpellBookItemName",
        "issecretvalue",
        "C_Secrets",
        "canaccessvalue",
    ],
    "actionbars": ["GetActionInfo", "GetMacroInfo", "issecretvalue", "C_Secrets", "canaccessvalue"],
    "bindings": [
        "GetCurrentBindingSet",
        "GetNumBindings",
        "GetBinding",
        "GetBindingKey",
        "issecretvalue",
        "C_Secrets",
        "canaccessvalue",
    ],
}
LOOKED_UP_ONLY = ("C_Secrets", "canaccessvalue")


def _bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _char() -> labaddon.CharDBV3:
    char = labaddon.parse_char(_bytes(CHAR_3))
    assert type(char) is labaddon.CharDBV3
    return char


def _text() -> str:
    return "\n".join(labaddon.describe(_char()))


def _probes(section: Any) -> dict[str, labaddon.ApiProbe]:
    return {p.api: p for p in section.probes}


# ─── schema 3 beside schema 1 ────────────────────────────────────────────────


def test_schema_3_account_reads_beside_schema_1() -> None:
    assert type(labaddon.parse_account(_bytes(ACCOUNT_3))) is labaddon.AccountDBV3
    assert type(labaddon.parse_account(_bytes(ACCOUNT_1))) is labaddon.AccountDBV1
    # The two account files differ in the schema digit only.
    assert _bytes(ACCOUNT_3) == _bytes(ACCOUNT_1).replace(b"= 1,", b"= 3,")


@pytest.mark.parametrize("name", [ACCOUNT_3, CHAR_3])
def test_schema_3_capture_round_trips_byte_for_byte(name: str) -> None:
    data = _bytes(name)
    assert luadata.serialize(luadata.parse(data)) == data


def test_schema_3_capture_reads_with_no_unknown_key() -> None:
    char = _char()
    assert char.schema_ == 3
    assert labaddon.unknown_keys(char) == []
    assert char.client is not None and not isinstance(char.client, labaddon.AbsentRecord)
    assert (char.client.version, char.client.build, char.client.interface) == (
        "1.60.1",
        "70425",
        16001,
    )
    assert char.probe.loads == 1
    assert char.probe.lost is None


def test_schema_3_capture_json_reads_back_the_same() -> None:
    char = _char()
    assert labaddon.CharDBV3.model_validate_json(char.model_dump_json()) == char


@pytest.mark.parametrize("name", sorted(OLDER))
def test_older_captures_still_read_identically(name: str) -> None:
    char = labaddon.parse_char(_bytes(name))
    assert type(char) is labaddon.CharDBV1
    digest = hashlib.sha256(char.model_dump_json().encode()).hexdigest()
    assert digest == OLDER[name]


# ─── checklist items 1, 2 and 10: every probe recorded ───────────────────────


@pytest.mark.parametrize("key", sorted(PROBES))
def test_every_probed_call_is_recorded_present_and_without_error(key: str) -> None:
    section = getattr(_char(), key)
    assert section.events_unregistered == []
    probes = _probes(section)
    assert list(probes) == PROBES[key]
    for api, probe in probes.items():
        assert probe.present is True, api
        assert probe.errors == 0, api
        assert probe.secret == 0, api
        if api in LOOKED_UP_ONLY:
            assert (probe.calls, probe.answered) == (0, 0), api
    secret = probes["issecretvalue"]
    assert secret.calls > 0
    assert secret.answered == secret.calls  # a boolean every time


def test_left_out_config_is_the_active_class_config() -> None:
    talents = _char().talents
    assert talents is not None
    legacy, cls = talents.legacy, talents.class_
    assert isinstance(legacy, labaddon.LegacyTalentsV3)
    assert isinstance(cls, labaddon.ClassTalents)
    assert cls.config is not None and not isinstance(cls.config, labaddon.AbsentConfig)
    assert legacy.left_out_config == cls.config.id == 640511
    assert legacy.left_out_config_absent is None
    assert all(c.id != legacy.left_out_config for c in legacy.configs)


# ─── checklist items 3 to 5: the spellbook ───────────────────────────────────


def test_spellbook_skill_lines_tile_the_items() -> None:
    book = _char().spellbook
    assert isinstance(book, labaddon.Spellbook)
    assert [(s.offset, s.count) for s in book.skill_lines] == [(0, 9), (9, 7), (16, 7), (23, 5)]
    assert [i.slot for i in book.list_] == list(range(1, 29))
    probes = _probes(book)
    assert probes["C_SpellBook.GetSpellBookItemInfo"].answered == 28
    assert probes["C_SpellBook.GetSpellBookItemName"].answered == 28


def test_spellbook_item_types_and_one_item_per_rank() -> None:
    book = _char().spellbook
    assert isinstance(book, labaddon.Spellbook)
    assert book.item_types == {
        "None": 0,
        "Spell": 1,
        "FutureSpell": 2,
        "PetAction": 3,
        "Flyout": 4,
    }
    assert sorted({i.type for i in book.list_}) == [1, 4]  # no future spell in this book
    ranks = [(i.spell_id, i.subtext) for i in book.list_ if i.spell_name == "Shadow Bolt"]
    assert ranks == [(686, "Rank 1"), (695, "Rank 2"), (705, "Rank 3")]
    subtexts = {i.subtext for i in book.list_}
    assert subtexts - {f"Rank {n}" for n in (1, 2, 3)} == {
        None,
        "Racial",
        "Passive",
        "Racial Passive",
        "Summon",
    }


def test_spellbook_flyout_has_an_action_id_and_no_spell_id() -> None:
    book = _char().spellbook
    assert isinstance(book, labaddon.Spellbook)
    flyouts = [i for i in book.list_ if i.type == book.item_types["Flyout"]]
    assert [(i.action_id, i.spell_id, i.spell_name) for i in flyouts] == [
        (259, None, "Summon Demon")
    ]


# ─── checklist items 6 and 7: action bars and macro slots ────────────────────


def test_action_bars_slots_past_the_filled_ones_answer_nothing() -> None:
    bars = _char().actionbars
    assert isinstance(bars, labaddon.ActionBars)
    assert (bars.first_slot, bars.last_slot) == (1, 180)
    action = _probes(bars)["GetActionInfo"]
    assert (action.calls, action.errors, action.answered) == (180, 0, len(bars.slots))
    assert max(s.slot for s in bars.slots) == 72
    kinds = [s.type for s in bars.slots]
    assert (kinds.count("spell"), kinds.count("item"), kinds.count("macro")) == (20, 4, 2)


def test_macro_slots_keep_an_index_only_and_each_names_a_macro() -> None:
    bars = _char().actionbars
    assert isinstance(bars, labaddon.ActionBars)
    macros = [s for s in bars.slots if s.type == "macro"]
    # Forever gave no subtype for either macro slot, the `#showtooltip`
    # macro's included (LAB_FORMATS amendment of 2026-10-16).
    assert [(s.id, s.sub_type) for s in macros] == [(149, None), (140, None)]
    macro_info = _probes(bars)["GetMacroInfo"]
    assert macro_info.calls == macro_info.answered == len(macros)


# ─── checklist item 8: bindings ──────────────────────────────────────────────


def test_bindings_counts_and_the_raw_binding_set() -> None:
    record = _char().bindings
    assert isinstance(record, labaddon.Bindings)
    assert record.binding_set == 2
    assert (record.account_bindings, record.character_bindings) == (None, None)
    assert (record.commands, record.commands_left_out, record.keys_left_out) == (299, 1, 0)
    probes = _probes(record)
    assert probes["GetBinding"].calls == record.commands
    key = probes["GetBindingKey"]
    assert key.calls == record.commands - record.commands_left_out
    assert key.answered == len(record.list_) == 124
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*", b.command) for b in record.list_)


def test_bindings_cover_every_key_the_cache_rebinds() -> None:
    """The `bind` lines of the run's `bindings-cache.wtf` (account and
    character copies byte-identical, not committed), as `command: key`:
    each is what `GetBindingKey` returned. Its two `NONE` lines unbind `-`
    and `=`, and no command holds either key."""
    cache = {
        "MULTIACTIONBAR1BUTTON2": "BUTTON3",
        "MULTIACTIONBAR1BUTTON1": "Q",
        "MULTIACTIONBAR1BUTTON3": "E",
        "ACTIONBUTTON11": "NUMPADPLUS",
        "ACTIONBUTTON12": "NUMPADMINUS",
        "ITEMTREE_OPEN": "ALT-I",
    }
    record = _char().bindings
    assert isinstance(record, labaddon.Bindings)
    live = {b.command: b.keys for b in record.list_}
    for command, key in cache.items():
        assert live[command] == [key], command
    assert not any(k in ("-", "=") for keys in live.values() for k in keys)


# ─── checklist item 13: privacy ──────────────────────────────────────────────

# `scripts/lab_capture.py`'s NAME_REALM_SHAPE_RE, which noted 27 matches.
NAME_REALM_SHAPE = re.compile(
    rb'"([A-Za-z\x80-\xff]{2,24})(?: - |-)([A-Za-z\x80-\xff][^"\r\n]{1,40})"'
)


def test_every_name_realm_shaped_string_is_a_bound_key() -> None:
    record = _char().bindings
    assert isinstance(record, labaddon.Bindings)
    keys = {k for b in record.list_ for k in b.keys}
    found = [m.group(0)[1:-1].decode() for m in NAME_REALM_SHAPE.finditer(_bytes(CHAR_3))]
    assert len(found) == 27
    assert set(found) <= keys


def test_no_macro_text_in_the_capture() -> None:
    data = _bytes(CHAR_3).lower()
    for text in (b"#showtooltip", b"/cast", b"/say", b"macro "):
        assert text not in data


# ─── M11-29: the barber-shop record after one applied change ─────────────────


def _hair_colour_position(choice: int) -> int:
    """The 1-based position of a Hair Color choice (option 61, model 9) by
    `OrderIndex` in the committed 70009 table: the swatch the owner counted."""
    with (FIXTURES / "wago" / "ChrCustomizationChoice.1.60.1.70009.csv").open(
        encoding="utf-8", newline=""
    ) as fh:
        rows = [r for r in csv.DictReader(fh) if r["ChrCustomizationOptionID"] == "61"]
    order = sorted(rows, key=lambda r: int(r["OrderIndex"]))
    return [int(r["ID"]) for r in order].index(choice) + 1


def test_barber_record_counts_the_applied_event_and_a_later_open() -> None:
    record = _char().customization
    assert isinstance(record, labaddon.CustomizationV2)
    assert record.recorded_at == "open"
    assert record.recorded_load == 1 and record.carried is None
    assert record.events_received == {
        "BARBER_SHOP_RESULT": 1,
        "BARBER_SHOP_COST_UPDATE": 0,
        "BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE": 0,
        "BARBER_SHOP_APPEARANCE_APPLIED": 1,
        "BARBER_SHOP_CLOSE": 2,
        "BARBER_SHOP_OPEN": 2,
    }
    # No entry for the made-up control: this client refuses a name it does
    # not know, so every entry above is a name it knows, and the pre-9.0
    # name is not one.
    assert record.events_unregistered == []
    assert record.chr_model_id is None
    assert record.chr_model_id_absent == "C_BarberShop.GetViewingChrModel returned nil"


def test_barber_record_holds_the_new_hair_colour_at_a_1_based_index() -> None:
    """The owner moved the hair colour from swatch 7 to swatch 1. The
    M11-23 record (70058) had it at index 7, choice 962; this one has the
    new look at index 1, choice 956."""
    record = _char().customization
    assert isinstance(record, labaddon.CustomizationV2)
    hair = [c for c in record.choices if c.option == 61]
    assert [(c.choice_index, c.choice) for c in hair] == [(1, 956)]
    assert _hair_colour_position(956) == 1
    assert _hair_colour_position(962) == 7
    older = labaddon.parse_char(_bytes(RECORD_70058))
    assert isinstance(older.customization, labaddon.Customization)
    assert [(c.choice_index, c.choice) for c in older.customization.choices if c.option == 61] == [
        (7, 962)
    ]


def test_describe_reads_the_capture() -> None:
    text = _text()
    assert "Spellbook: 28 items in 4 skill lines" in text
    assert "Action bars: 26 of slots 1 to 180 filled (4 item, 2 macro, 20 spell" in text
    assert "Key bindings: 124 commands with a key, of 299 listed" in text
    assert "left out as the active class config at this gather: config 640511" in text
    assert "BARBER_SHOP_APPEARANCE_APPLIED 1" in text
