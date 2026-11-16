"""The character page of `wowlab ui` (M18-05, docs/design/COMPANION.md):
the header (level tile in the class colour, name, "Race Class · game", item
level, professions, last played, See it dressed, back to All characters),
the gear as the character sheet lays it out, the Progress card and What
changed from the saves, the professions as bars, the action bar's keys 1 to
0, and every caveat under About this data.

Driven under Node with the harness of `test_ui_home.py` (itself
`test_ui_account.py`'s, `test_ui_character.py`'s and `test_ui_explorer.py`'s:
HTML sinks throw, attributes outside a closed set and navigation are
trapped, DOM collections are not Arrays), fed what the real CLI printed
through the real server for the two captures side by side that module uses
(`constructed`, L8). Cases labelled `constructed` edit a real report before
the page reads it: names as if downloaded, every slot filled. The saves
(one and several) are graded in `test_ui_character_history.py`. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import test_ui_account as hub_tab
import test_ui_explorer as first_stage
import test_ui_home as home_tab

from wowlab_core import ui

both = hub_tab.both
home = hub_tab.home
server = hub_tab.server
FOREVER = hub_tab.FOREVER
BETA = hub_tab.BETA
WARLOCK = hub_tab.WARLOCK
ACCOUNT = hub_tab.ACCOUNT
LAUNCH_ACCOUNT = hub_tab.LAUNCH_ACCOUNT
GEAR = ["char", "gear"]
BINDS = ["char", "binds"]
PARAMS = {"character": WARLOCK, "account": LAUNCH_ACCOUNT, "flavor": FOREVER}
_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"

_STEPS = r"""
      } else if (step.page !== undefined) {
        const shown = (id) => !ids[id].hidden;
        out.steps.push({ char: charView(),
          main: ["char-header", "char-gear", "char-progress", "char-changes", "char-profs", "char-bar"].map((id) =>
            visibleText(ids[id])),
          panels: { page: shown("char-page"), about: shown("char-about"), keys: shown("char-keys-panel"),
            macros: shown("char-macros-panel") },
          title: ids["char-view-title"].textContent,
          shown: Object.keys(ids).filter((id) => id.endsWith("-view") && !ids[id].hidden) });
"""

# The text a player sees without a click: everything but a hidden element
# (a gear item's detail) and what only a screen reader reads.
_VIEW = r"""
function visibleText(node) {
  if (node.hidden || (typeof node.className === "string" && node.className.split(" ").includes("unseen"))) return "";
  if (!node.children || !node.children.length) return node.textContent || "";
  return node.children.map(visibleText).join(" ");
}
"""


def _harness() -> str:
    text = home_tab._harness()
    assert text.count(home_tab._ANCHOR) == 1 and text.count(home_tab._VIEW_ANCHOR) == 1
    text = text.replace(home_tab._VIEW_ANCHOR, _VIEW + home_tab._VIEW_ANCHOR, 1)
    return text.replace(home_tab._ANCHOR, _STEPS.lstrip("\n") + home_tab._ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = hub_tab._harness
    hub_tab._harness = _harness
    try:
        return hub_tab._drive(folder, scenario)
    finally:
        hub_tab._harness = original


def _scenario(server: Any, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """The home's scenario, the warlock's card opened first, then `steps`,
    with the warlock's four reads as the real CLI printed them."""
    scenario = home_tab._scenario(server, [{"home_open": 0}, {"page": True}, *steps])
    for command in hub_tab.PICK_READS:
        scenario["runs"].append(hub_tab._run_of(server, list(command), PARAMS))
    return scenario


def _run(scenario: dict[str, Any], command: list[str]) -> dict[str, Any]:
    found = [r for r in scenario["runs"] if r["command"] == command and r["params"] == PARAMS]
    assert found, command
    run: dict[str, Any] = found[-1]
    return run


def _report(scenario: dict[str, Any], command: list[str]) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(_run(scenario, command)["stdout"])
    return report


def _edit(scenario: dict[str, Any], command: list[str], report: dict[str, Any]) -> None:
    for run in scenario["runs"]:
        if run["command"] == command and run["params"] == PARAMS:
            run["stdout"] = json.dumps(report)


def _row(scenario: dict[str, Any]) -> dict[str, Any]:
    [row] = [r for r in home_tab._summary(scenario)["rows"] if r["label"] == WARLOCK.split("/")[1]]
    return row


def _page(out: dict[str, Any]) -> dict[str, Any]:
    step: dict[str, Any] = out["steps"][1]
    assert step["shown"] == ["character-view"], step["shown"]
    return step


# ─── the header ──────────────────────────────────────────────────────────────


def test_the_header_names_the_character_as_its_card_does_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [])
    gear = _report(scenario, GEAR)
    row = _row(scenario)
    page = _page(_drive(tmp_path / "node", scenario))
    hero = page["char"]["hero"]
    # The level tile in the class colour, read "Level <n>" by a screen reader.
    assert hero["tile"] == f"Level {gear['level']}"
    assert "cls-warlock" in hero["look"].split()
    assert hero["name"] == WARLOCK.split("/")[1]
    # No name table in this home: the class token in words, the race's id,
    # then the game as people say it, never its folder.
    race = gear["identity"]["race_id"]
    assert hero["who"] == f"Warlock · race {race} · WoW Forever"
    profs = len(row["professions"])
    assert profs >= 1
    stats = dict(hero["stats"])
    assert list(stats) == ["Item level", "Professions", "Last played"]
    assert stats["Item level"] == f"{gear['average']['equipped']:.1f}"
    assert stats["Professions"] == str(profs)
    assert re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}(, \d{4})?", stats["Last played"])


def test_names_cached_name_race_class_and_professions_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [])
    gear = _report(scenario, GEAR)
    gear["names"] = dict(gear["names"], class_name="Warlock", race="Undead")
    _edit(scenario, GEAR, gear)
    summary = home_tab._summary(scenario)
    names = iter(["Tailoring", "Enchanting", "First Aid", "Cooking", "Fishing", "Herbalism"])
    for row in summary["rows"]:
        if row["label"] == WARLOCK.split("/")[1]:
            for p in row["professions"]:
                p["name"] = next(names)
    home_tab._edit_summary(scenario, summary)
    page = _page(_drive(tmp_path / "node", scenario))
    assert page["char"]["hero"]["who"] == "Undead Warlock · WoW Forever"
    heads = [p["text"] for p in page["char"]["profs"]]
    row = _row(scenario)
    assert len(heads) == len(row["professions"])
    for text, p in zip(heads, row["professions"], strict=True):
        assert text == f"{p['name']}{p['rank']}/{p['max_rank']}"
    assert "Skill line" not in page["char"]["profsText"]


def test_names_not_cached_show_the_id_as_the_cards_do_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [])
    row = _row(scenario)
    page = _page(_drive(tmp_path / "node", scenario))
    profs = page["char"]["profs"]
    # Every profession, not the card's four, with its bar in 5% steps.
    assert len(profs) == len(row["professions"])
    for drawn, p in zip(profs, row["professions"], strict=True):
        assert drawn["text"] == f"Skill line {p['skill_line']}{p['rank']}/{p['max_rank']}"
        step = round(min(1, max(0, p["rank"] / p["max_rank"])) * 20)
        assert drawn["fill"] == f"bar-fill fill-{step}"


# ─── the gear ────────────────────────────────────────────────────────────────

SHEET = [[1, 2, 3, 15, 5, 4, 19, 9], [10, 6, 7, 8, 11, 12, 13, 14], [16, 17, 18]]


def test_the_gear_is_two_columns_and_the_weapons_with_empty_slots_greyed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [])
    gear = _report(scenario, GEAR)
    assert gear["empty_slots"], "the capture has empty slots"
    page = _page(_drive(tmp_path / "node", scenario))
    names = {s["slot"]: s["slot_name"] for s in gear["slots"] + gear["empty_slots"]}
    filled = {s["slot"]: s for s in gear["slots"]}
    columns = page["char"]["columns"]
    assert [[c.split(" · ")[0] for c in col] for col in columns] == [
        [names[n] for n in col] for col in SHEET
    ]
    rows = page["char"]["rows"]
    assert len(rows) == 19
    for drawn, n in zip(rows, [n for col in SHEET for n in col], strict=True):
        if n in filled:
            assert not drawn["empty"] and drawn["name"] == filled[n]["name"]
            assert drawn["cells"][2] == str(filled[n]["item_level"])
        else:
            assert drawn["empty"] and drawn["cells"][1:] == ["Empty", ""]


def test_full_gear_has_no_empty_slot_constructed(server: Any, both: Path, tmp_path: Path) -> None:
    """Constructed: every empty slot of the real report filled with a copy of
    its first item."""
    scenario = _scenario(server, [])
    gear = _report(scenario, GEAR)
    first = gear["slots"][0]
    for empty in gear["empty_slots"]:
        gear["slots"].append(dict(first, slot=empty["slot"], slot_name=empty["slot_name"]))
    gear["empty_slots"] = []
    _edit(scenario, GEAR, gear)
    page = _page(_drive(tmp_path / "node", scenario))
    rows = page["char"]["rows"]
    assert len(rows) == 19 and not any(r["empty"] for r in rows)
    assert "Empty" not in page["main"][1]


# ─── the action bar ──────────────────────────────────────────────────────────


def _words(row: dict[str, Any] | None) -> str:
    """What the page says a key's slot holds, in the player's words."""
    if row is None:
        return "Not bound"
    if row["slot"] is None:
        return "Another command"
    holds = row["holds"]
    if holds is None:
        return "Empty"
    if holds["type"] == "spell":
        if not holds["name"]:
            return "A spell"
        return holds["name"] + (f" ({holds['subtext']})" if holds["subtext"] else "")
    return {
        "macro": "A macro",
        "item": "An item",
        "flyout": "A spell flyout",
        "equipmentset": "An equipment set",
    }.get(holds["type"], "Something")


def test_the_action_bar_says_what_keys_1_to_0_cast(server: Any, both: Path, tmp_path: Path) -> None:
    scenario = _scenario(server, [])
    binds = _report(scenario, BINDS)
    page = _page(_drive(tmp_path / "node", scenario))
    keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
    expected = []
    for key in keys:
        rows = [r for r in binds["keys"] if r["key"] == key]
        row = next((r for r in rows if r["slot"] is not None), rows[0] if rows else None)
        expected.append([key, _words(row)])
    assert page["char"]["bar"] == expected
    assert any(w not in ("Empty", "Not bound") for _, w in expected), "the capture casts something"


def test_without_an_action_bar_record_the_bar_says_so_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [])
    binds = _report(scenario, BINDS)
    binds["cannot_place"] = "no action-bar record"
    _edit(scenario, BINDS, binds)
    page = _page(_drive(tmp_path / "node", scenario))
    assert page["char"]["bar"] == []
    assert page["char"]["barText"] == "The action bars were not recorded for this character."


# ─── the main view and About this data ───────────────────────────────────────


def test_the_main_view_holds_no_path_folder_id_or_caveat(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [])
    gear = _report(scenario, GEAR)
    page = _page(_drive(tmp_path / "node", scenario))
    main = "\n".join(page["main"])
    for word in (
        FOREVER,
        BETA,
        LAUNCH_ACCOUNT,
        WARLOCK,
        "WowLab.lua",
        "SavedVariables",
        "WTF/",
        "[verify]",
        "wowlab ",
        "spell ",
        "item id",
        "modification time",
        "schema",
    ):
        assert word not in main, word
    # The item ids and the link's text are one click away, in each item's
    # detail; none is shown before it.
    for slot in gear["slots"]:
        assert slot["raw_link"] not in main
    # What the old header said moved under About this data: nothing deleted.
    facts = dict(
        zip(page["char"]["headerFacts"][::2], page["char"]["headerFacts"][1::2], strict=True)
    )
    assert facts["File"] == gear["file"] and facts["Character folder"] == gear["character"]
    assert facts["Written"].endswith("(the file's modification time)")
    assert page["char"]["notes"] == gear["notes"]
    assert page["panels"] == {"page": True, "about": True, "keys": False, "macros": False}


def test_keybinds_shows_the_keys_and_macros_with_a_label_of_their_own(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"home_click": "sec-keybinds"}, {"page": True}])
    out = _drive(tmp_path / "node", scenario)
    keys = out["steps"][3]
    assert keys["panels"] == {"page": False, "about": False, "keys": True, "macros": True}
    # The view is labelled by its own heading, which names the section shown,
    # not by the hidden Character tab.
    assert keys["title"] == "Keybinds"
    body = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert '<section id="character-view" role="tabpanel" aria-labelledby="char-view-title"' in body
    assert out["steps"][1]["title"] == "Character"


def test_all_characters_goes_back_to_the_cards(server: Any, both: Path, tmp_path: Path) -> None:
    scenario = _scenario(server, [{"home_click": "char-back"}])
    out = _drive(tmp_path / "node", scenario)
    back = out["steps"][2]
    assert back["shown"] == ["account-view"]
    assert hub_tab._bodies(back["newRequests"]) == []


def test_the_keys_table_wraps_between_words() -> None:
    """At phone width "What it does" wrapped one letter at a time (review of
    M18-03): in the Keybinds table a cell wraps only between words, as the
    hub's does since M17-14, and the column keeps a readable width."""
    css = first_stage._css()
    rule = re.search(r"#char-keys \.char-table td \{([^}]*)\}", css)
    assert rule is not None
    assert "overflow-wrap: normal" in rule.group(1) and "word-break: normal" in rule.group(1)
    does = re.search(r"#char-keys \.key-does \{([^}]*)\}", css)
    assert does is not None and "min-width" in does.group(1)
