"""`wowlab_core.keymap` (M14-06, docs/LAB_PLAN.md §16.2): key to slot to spell.

The real inputs are the first schema-3 record (`fixtures/macos-70425/`,
M13-05: 124 commands with a key out of 299, 26 filled slots, 28 spellbook
items, `commands_left_out` 1, `keys_left_out` 0) and the account
`bindings-cache.wtf` of the 69913 capture (`fixtures/macos/`, three `bind`
lines). The two are from different capture runs and builds, so a test that
joins them says `cross-capture` in its id. The counts the tests assert are in
their ids.

Inputs labelled `constructed` are boundary cases (L8): `bind` lines written
by the test in the real file's form (CRLF), and the real record with one
section, slot or binding replaced. Nothing here opens an install.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from wowlab_core import bindscoach, keymap, labaddon, wtfconfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_3 = (
    FIXTURES
    / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)
ACCOUNT_BINDINGS = FIXTURES / "macos/forever/WTF/Account/90000001#6/bindings-cache.wtf"
SCHEMA_1 = (
    FIXTURES / "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
)


def real_record() -> labaddon.CharDBV3:
    rec = labaddon.read_char(SCHEMA_3)
    assert isinstance(rec, labaddon.CharDBV3)
    return rec


def real_file() -> wtfconfig.BindingsDocument:
    return wtfconfig.read_bindings(ACCOUNT_BINDINGS)


def lines(*binds: str) -> wtfconfig.BindingsDocument:
    """A constructed bindings file: one `bind` line each, CRLF as the client writes."""
    return wtfconfig.parse_bindings("".join(f"bind {b}\r\n" for b in binds).encode())


def row(km: keymap.KeyMap, key: str, command: str) -> keymap.KeyRow:
    found = [r for r in km.keys if (r.key, r.command) == (key, command)]
    assert len(found) == 1, found
    return found[0]


def with_slots(
    rec: labaddon.CharDBV3, slots: dict[int, tuple[str | None, int | None]]
) -> labaddon.CharDBV3:
    bars = rec.actionbars
    assert isinstance(bars, labaddon.ActionBars)
    kept = [s for s in bars.slots if s.slot not in slots]
    added = [
        labaddon.ActionSlot.model_validate({"slot": n, "type": t, "id": i} if t else {"slot": n})
        for n, (t, i) in slots.items()
    ]
    new = sorted([*kept, *added], key=lambda s: s.slot)
    return rec.model_copy(update={"actionbars": bars.model_copy(update={"slots": new})})


# ─── the real record ─────────────────────────────────────────────────────────


def test_schema3_record_alone_131_keys_124_commands_15_on_slots_131_not_in_a_file_11_unreached() -> (
    None
):
    rec = real_record()
    km = keymap.join_record(account=None, character=None, record=rec)
    assert isinstance(rec.bindings, labaddon.Bindings)
    assert len(rec.bindings.list_) == 124
    assert len(km.keys) == 131
    assert len({r.command for r in km.keys}) == 124
    assert sum(1 for r in km.keys if r.slot is not None) == 15
    assert all(r.sources == ["record"] for r in km.keys)
    assert all(r.agreement == "not in a file" and not r.disagrees for r in km.keys)
    assert (km.commands_left_out, km.keys_left_out) == (1, 0)
    assert (km.cannot_place, km.cannot_compare) == (None, None)
    assert [u.slot for u in km.unreached_slots] == [49, 50, *range(64, 73)]
    assert not any(u.unfollowed_page for u in km.unreached_slots)


def test_schema3_slots_hold_what_the_bars_and_spellbook_say_12_main_bar_3_bottom_left() -> None:
    km = keymap.join_record(account=None, character=None, record=real_record())
    placed = [(r.key, r.command, r.slot, r.does) for r in km.keys if r.slot is not None]
    assert placed[:3] == [
        ("1", "ACTIONBUTTON1", 1, "casts Immolate (Rank 2, spell 707)"),
        ("2", "ACTIONBUTTON2", 2, "casts Corruption (Rank 1, spell 172)"),
        ("3", "ACTIONBUTTON3", 3, "casts Bane of Agony (Rank 1, spell 980)"),
    ]
    assert placed[7] == ("8", "ACTIONBUTTON8", 8, "uses item 3434")
    assert placed[11] == ("NUMPADMINUS", "ACTIONBUTTON12", 12, "casts Shoot (spell 5019)")
    assert placed[12:] == [
        ("Q", "MULTIACTIONBAR1BUTTON1", 61, "casts Will of the Forsaken (Racial, spell 7744)"),
        ("BUTTON3", "MULTIACTIONBAR1BUTTON2", 62, "casts Life Tap (Rank 1, spell 1454)"),
        ("E", "MULTIACTIONBAR1BUTTON3", 63, "casts Fear (Rank 1, spell 5782)"),
    ]
    one = row(km, "1", "ACTIONBUTTON1")
    assert one.holds == keymap.Holds(
        type="spell", id=707, sub_type="spell", name="Immolate", subtext="Rank 2"
    )
    assert one.page_note is not None and one.page_note.startswith(
        "main bar button 1, page 1, the page shown with no form, stance or stealth active; "
        "paging, a form, stance or stealth, or a possess, override or vehicle bar [verify] "
        "shows another page"
    )
    assert row(km, "Q", "MULTIACTIONBAR1BUTTON1").page_note == "bottom left bar button 1, page 6"
    # A command that is no action button presses no slot.
    stance = row(km, "CTRL-F1", "SHAPESHIFTBUTTON1")
    assert (stance.slot, stance.holds, stance.page_note) == (None, None, None)
    assert stance.does == "stance bar button 1 (not followed) [verify]"
    assert (
        row(km, "CTRL-1", "BONUSACTIONBUTTON1").does == "pet bar button 1 (not followed) [verify]"
    )
    assert row(km, "SHIFT-6", "ACTIONPAGE6").does == "shows main-bar page 6"
    assert row(km, "M", "TOGGLEWORLDMAP").does == "a binding command, not an action slot"


def test_schema3_and_the_account_file_3_agree_128_not_in_a_file_0_disagree_cross_capture() -> None:
    km = keymap.join_record(account=real_file(), character=None, record=real_record())
    assert Counter(r.agreement for r in km.keys) == {"both": 3, "not in a file": 128}
    assert not any(r.disagrees for r in km.keys)
    both = [(r.key, r.command, r.sources) for r in km.keys if r.agreement == "both"]
    assert both == [
        ("Q", "MULTIACTIONBAR1BUTTON1", ["account file", "record"]),
        ("BUTTON3", "MULTIACTIONBAR1BUTTON2", ["account file", "record"]),
        ("E", "MULTIACTIONBAR1BUTTON3", ["account file", "record"]),
    ]
    assert all(not r.disagrees and r.note is None for r in km.keys if r.agreement == "both")
    assert {r.note for r in km.keys if r.agreement == "not in a file"} == {
        keymap.NOT_IN_A_FILE_NOTE
    }
    # With a record to compare, the notes say neither source is the truth.
    assert any(n.startswith("The bindings files are what the client last wrote") for n in km.notes)


def test_the_two_macro_slots_64_65_are_given_by_index_and_no_name() -> None:
    km = keymap.join_record(account=None, character=None, record=real_record())
    macros = [u for u in km.unreached_slots if u.holds.type == "macro"]
    assert [(u.slot, u.holds.id, u.holds.sub_type, u.holds.name) for u in macros] == [
        (64, 149, None, None),
        (65, 140, None, None),
    ]
    assert macros[0].does == (
        "runs macro 149 (a macro index [verify]); the record keeps no macro name or body"
    )
    assert macros[0].page_note == "bottom left bar button 4, page 6"


def test_bindscoach_uses_keymaps_slot_rule_and_spellbook() -> None:
    assert bindscoach.command_slot is keymap.command_slot
    assert bindscoach.BAR_PAGES is keymap.BAR_PAGES
    assert bindscoach.UNFOLLOWED_SLOTS is keymap.UNFOLLOWED_SLOTS
    assert bindscoach.GET_RECORD == keymap.GET_RECORD
    names, subtexts, why = keymap.spellbook(real_record())
    assert (len(names), len(subtexts), why) == (
        27,
        23,
        None,
    )  # the flyout has no spell id; 4 have no subtext
    assert keymap.held(705, subtexts) == "Rank 3 (705)"
    assert keymap.held(5019, subtexts) == "5019"


# ─── constructed cases ───────────────────────────────────────────────────────


def test_a_file_only_key_disagrees_and_names_what_the_record_has_constructed() -> None:
    km = keymap.join_record(
        account=lines("CTRL-F11 TOGGLEBACKPACK", "1 TOGGLEWORLDMAP"),
        character=None,
        record=real_record(),
    )
    f5 = row(km, "CTRL-F11", "TOGGLEBACKPACK")
    assert (f5.sources, f5.agreement, f5.disagrees) == (["account file"], "file only", True)
    assert f5.note == "the record has no command on CTRL-F11"
    assert (f5.slot, f5.holds, f5.does) == (None, None, "a binding command, not an action slot")
    # Key 1 is on another command in the record: both rows say so, neither is chosen.
    file_one = row(km, "1", "TOGGLEWORLDMAP")
    assert (file_one.agreement, file_one.note) == ("file only", "the record has 1 on ACTIONBUTTON1")
    record_one = row(km, "1", "ACTIONBUTTON1")
    assert (record_one.agreement, record_one.note) == (
        "record only",
        "the account file has 1 on TOGGLEWORLDMAP",
    )


def test_a_file_only_click_command_is_one_the_record_leaves_out_constructed() -> None:
    km = keymap.join_record(
        account=lines('BUTTON4 "CLICK SomeAddonButton:LeftButton"'),
        character=None,
        record=real_record(),
    )
    click = row(km, "BUTTON4", "CLICK SomeAddonButton:LeftButton")
    assert click.agreement == "file only"
    assert click.note is not None and "commands_left_out" in click.note


def test_a_record_only_key_is_marked_and_not_chosen_constructed() -> None:
    km = keymap.join_record(
        account=lines("Q MULTIACTIONBAR1BUTTON1"), character=None, record=real_record()
    )
    # No file line names key 2: the record alone has it, and that is no disagreement.
    two = row(km, "2", "ACTIONBUTTON2")
    assert (two.sources, two.agreement, two.disagrees) == (["record"], "not in a file", False)
    assert (
        two.note
        == keymap.NOT_IN_A_FILE_NOTE
        == (
            "not in a bindings file: the file may hold only the changes from the client's "
            "defaults, or may not have been rewritten since the key was set [verify]"
        )
    )
    assert two.does == "casts Corruption (Rank 1, spell 172)"
    # A file line puts key 3 on another command: the record's row disagrees.
    km = keymap.join_record(account=lines("3 TOGGLEBACKPACK"), character=None, record=real_record())
    three = row(km, "3", "ACTIONBUTTON3")
    assert (three.agreement, three.disagrees) == ("record only", True)
    assert three.note == "the account file has 3 on TOGGLEBACKPACK"
    # A file line clearing key 2 is said as a clear, not as a command named NONE.
    km = keymap.join_record(account=None, character=lines("2 NONE"), record=real_record())
    two = row(km, "2", "ACTIONBUTTON2")
    assert (two.agreement, two.disagrees) == ("record only", True)
    assert two.note == "the character file clears 2 (NONE)"


def test_a_key_on_a_paged_slot_constructed() -> None:
    """MULTIACTIONBAR2BUTTON1 shows page 5: slot 49, which then is reached."""
    km = keymap.join_record(
        account=lines("CTRL-Q MULTIACTIONBAR2BUTTON1"), character=None, record=real_record()
    )
    paged = row(km, "CTRL-Q", "MULTIACTIONBAR2BUTTON1")
    assert (paged.slot, paged.does) == (49, "casts Immolate (Rank 2, spell 707)")
    assert paged.page_note == "bottom right bar button 1, page 5"
    assert 49 not in [u.slot for u in km.unreached_slots]
    assert [u.slot for u in km.unreached_slots][:1] == [50]


def test_a_slot_on_an_unfollowed_main_bar_page_is_marked_constructed() -> None:
    rec = with_slots(real_record(), {14: ("spell", 705), 75: ("spell", 696), 145: ("item", 118)})
    km = keymap.join_record(account=None, character=None, record=rec)
    by_slot = {u.slot: u for u in km.unreached_slots}
    assert (by_slot[14].unfollowed_page, by_slot[75].unfollowed_page) == (True, True)
    assert by_slot[14].page_note.startswith("page 2, button 2: a main-bar page shown by paging")
    assert by_slot[75].page_note.endswith(
        "the main bar's button 3 key presses it only while that page is shown"
    )
    assert by_slot[145].unfollowed_page is False
    assert by_slot[145].page_note == (
        "bar 5 button 1, page 13 (unconfirmed: no capture has had a key on this bar)"
    )


def test_a_macro_slot_with_a_key_constructed() -> None:
    km = keymap.join_record(
        account=lines("R MULTIACTIONBAR1BUTTON4"), character=None, record=real_record()
    )
    macro = row(km, "R", "MULTIACTIONBAR1BUTTON4")
    assert macro.slot == 64
    assert macro.holds == keymap.Holds(type="macro", id=149, sub_type=None, name=None, subtext=None)
    assert macro.does.startswith("runs macro 149 (a macro index [verify])")


def test_a_macro_slot_with_a_subtype_constructed() -> None:
    rec = real_record()
    bars = rec.actionbars
    assert isinstance(bars, labaddon.ActionBars)
    slots = [
        s.model_copy(update={"sub_type": "spell", "id": 705}) if s.slot == 64 else s
        for s in bars.slots
    ]
    rec = rec.model_copy(update={"actionbars": bars.model_copy(update={"slots": slots})})
    km = keymap.join_record(account=None, character=None, record=rec)
    macro = next(u for u in km.unreached_slots if u.slot == 64)
    # By id only: the macro's spell is not named, since which id it is stays [verify].
    assert macro.holds.name is None
    assert macro.does.startswith("runs a macro that shows spell 705 now [verify]")


def test_an_empty_slot_and_one_past_the_walked_range_constructed() -> None:
    km = keymap.join_record(
        account=lines("CTRL-E MULTIACTIONBAR3BUTTON1"), character=None, record=real_record()
    )
    empty = row(km, "CTRL-E", "MULTIACTIONBAR3BUTTON1")
    assert (empty.slot, empty.holds, empty.does) == (
        25,
        None,
        "empty slot (nothing recorded in it)",
    )
    assert empty.page_note == "right bar button 1, page 3"
    rec = real_record()
    bars = rec.actionbars
    assert isinstance(bars, labaddon.ActionBars)
    short = rec.model_copy(update={"actionbars": bars.model_copy(update={"last_slot": 24})})
    km = keymap.join_record(
        account=lines("CTRL-E MULTIACTIONBAR3BUTTON1"), character=None, record=short
    )
    assert row(km, "CTRL-E", "MULTIACTIONBAR3BUTTON1").does == (
        "not walked by the record (slots 1 to 24)"
    )


def test_a_cleared_key_agrees_when_the_record_has_nothing_on_it_constructed() -> None:
    km = keymap.join_record(account=lines("- NONE", "Q NONE"), character=None, record=real_record())
    dash = row(km, "-", "NONE")
    assert (dash.agreement, dash.disagrees, dash.does) == (
        "cleared",
        False,
        "clears the key (NONE) [verify]",
    )
    q = row(km, "Q", "NONE")
    assert (q.agreement, q.note) == ("file only", "the record has Q on MULTIACTIONBAR1BUTTON1")


def test_account_and_character_files_are_kept_apart_constructed() -> None:
    km = keymap.join_record(
        account=lines("Q MULTIACTIONBAR1BUTTON1", "F6 TOGGLEBACKPACK"),
        character=lines("Q MULTIACTIONBAR1BUTTON1", "F6 OPENALLBAGS"),
        record=real_record(),
    )
    assert row(km, "Q", "MULTIACTIONBAR1BUTTON1").sources == [
        "account file",
        "character file",
        "record",
    ]
    assert row(km, "F6", "TOGGLEBACKPACK").sources == ["account file"]
    assert row(km, "F6", "OPENALLBAGS").sources == ["character file"]


def test_without_a_schema_3_record_the_file_rows_cannot_be_placed() -> None:
    rec = labaddon.read_char(SCHEMA_1)
    assert rec.schema_ == 1
    km = keymap.join_record(account=real_file(), character=None, record=rec)
    assert [(r.key, r.slot, r.does, r.agreement) for r in km.keys] == [
        ("Q", 61, keymap.CANNOT_PLACE, None),
        ("BUTTON3", 62, keymap.CANNOT_PLACE, None),
        ("E", 63, keymap.CANNOT_PLACE, None),
    ]
    assert keymap.CANNOT_PLACE == "cannot place: no action-bar record"
    assert km.cannot_place is not None and km.cannot_place.startswith(
        "no action-bar record (schema 1 has none;"
    )
    assert km.cannot_compare is not None and km.cannot_compare.startswith("no bindings record")
    assert (km.unreached_slots, km.commands_left_out, km.keys_left_out) == ([], None, None)
    assert not any(n.startswith("The bindings files are what") for n in km.notes)
    assert (
        "Without a bindings record only the files' lines are listed; a file may leave out "
        "keys at the client's defaults [verify], so keys not listed may still be bound."
    ) in km.notes
    none = keymap.join_record(account=real_file(), character=None, record=None)
    assert none.cannot_place is not None and none.cannot_place.startswith("no lab-addon record")
    assert [r.does for r in none.keys] == [keymap.CANNOT_PLACE] * 3


@pytest.mark.parametrize("section", ["actionbars", "bindings", "spellbook"])
def test_an_absent_section_constructed(section: str) -> None:
    absent = labaddon.AbsentProbedSection(absent="in combat: constructed")
    rec = real_record().model_copy(update={section: absent})
    km = keymap.join_record(account=real_file(), character=None, record=rec)
    if section == "actionbars":
        assert km.cannot_place == "the action-bar section is absent: in combat: constructed"
        assert row(km, "Q", "MULTIACTIONBAR1BUTTON1").does == keymap.CANNOT_PLACE
    elif section == "bindings":
        assert km.cannot_compare == "the bindings section is absent: in combat: constructed"
        assert len(km.keys) == 3 and all(r.agreement is None for r in km.keys)
        assert row(km, "Q", "MULTIACTIONBAR1BUTTON1").does.startswith("casts Will of")
    else:
        assert row(km, "Q", "MULTIACTIONBAR1BUTTON1").does == (
            "casts spell 7744 (the spellbook record does not name it)"
        )
        assert any(n.startswith("no spellbook record (the section is absent") for n in km.notes)


def test_an_incomplete_bar_record_is_said_constructed() -> None:
    rec = with_slots(real_record(), {30: (None, None)})
    km = keymap.join_record(account=None, character=None, record=rec)
    assert km.cannot_place is None
    assert any(
        n.startswith("The action-bar record is incomplete (1 slot(s) have no type)")
        for n in km.notes
    )
    untyped = next(u for u in km.unreached_slots if u.slot == 30)
    assert untyped.does == "holds something the record gave no type for"


@pytest.mark.parametrize(
    ("slot_type", "ident", "does"),
    [
        ("item", 118, "uses item 118"),
        ("flyout", 259, "opens flyout 259"),
        (
            "equipmentset",
            None,
            "equips an equipment set (the record keeps no id: a set's id is its name [verify])",
        ),
        ("companion", 7, "holds companion 7"),
    ],
)
def test_what_other_slot_types_do_constructed(slot_type: str, ident: int | None, does: str) -> None:
    rec = with_slots(real_record(), {30: (slot_type, ident)})
    km = keymap.join_record(account=None, character=None, record=rec)
    found = next(u for u in km.unreached_slots if u.slot == 30)
    assert (found.holds.type, found.holds.id, found.holds.name) == (slot_type, ident, None)
    assert found.does == does
