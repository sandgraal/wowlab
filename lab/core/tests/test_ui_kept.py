"""Characters kept only in a backup on the Characters home of `wowlab ui`
(M18-06, docs/design/COMPANION.md): a dimmed card after the others, and a
read-only character page drawn from the backup.

The page is driven under Node with `test_ui_home.py`'s harness, fed what
the real CLI printed through the real server. The install is that module's
two captures side by side, and the store is `constructed` (L8, a boundary
input): the owner's two beta characters of `fixtures/macos-70450/` are
copied into `_classic_beta_`'s account, a snapshot of its `WTF/` is taken
into the stand-in home's data folder, and the two folders are deleted from
the install, so the store holds records the install lacks. Nothing reads or
writes a real install.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import test_cli_char_kept as kept_cli
import test_ui_account as hub_tab
import test_ui_explorer as first_stage
import test_ui_home as home_tab

both = hub_tab.both
home = hub_tab.home
server = hub_tab.server
no_env_flavor = hub_tab.no_env_flavor

BETA = hub_tab.BETA
FOREVER = hub_tab.FOREVER
ACCOUNT = hub_tab.ACCOUNT
SETT = kept_cli.SETT
PARTICIPLE = kept_cli.PARTICIPLE
KEPT = ["char", "kept"]
# The reads a page may run; none of them changes anything.
READS = {("char", "list"), ("char", "kept"), ("char", "gear"), ("char", "binds")}
READS |= {("char", "macros"), ("char", "history")}
SNAPPED = datetime(2026, 10, 19, 18, 0, 0, tzinfo=UTC)

_STEPS = r"""
      } else if (step.kept_open !== undefined) {
        const before = requests.length;
        const card = ids["char-cards"].children[step.kept_open];
        const button = card && card.all().find((e) => e.tagName === "button" && has(e, "card-open"));
        if (!button) { out.steps.push({ missing: "open " + step.kept_open }); continue; }
        button.fire("click"); await tick();
        const view = ids["character-view"];
        out.steps.push({ char: charView(), picker: ids["char-picker"].value,
          characterHidden: view.hidden, kept: ids["char-kept"].textContent, keptHidden: ids["char-kept"].hidden,
          barHidden: ids["char-bar-panel"].hidden, progress: ids["char-progress"].textContent,
          buttons: ["char-header", "char-gear", "char-progress", "char-changes", "char-profs", "char-sources",
            "char-history"].flatMap((id) => ids[id].all().filter((e) => e.tagName === "button").map((b) => b.textContent)),
          newRequests: requests.slice(before) });
      } else if (step.keys_after !== undefined) {
        const before = requests.length;
        ids["sec-keybinds"].fire("click"); await tick();
        out.steps.push({ kept: ids["char-kept"].textContent, keptHidden: ids["char-kept"].hidden,
          picker: ids["char-picker"].value, newRequests: requests.slice(before) });
"""

_ANCHOR = hub_tab._ANCHOR
_HOME_HARNESS = home_tab._harness


def _harness() -> str:
    text = _HOME_HARNESS()
    assert text.count(_ANCHOR) == 1
    return text.replace(_ANCHOR, _STEPS.lstrip("\n") + _ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = home_tab._harness
    home_tab._harness = _harness
    try:
        return home_tab._drive(folder, scenario)
    finally:
        home_tab._harness = original


def _account(root: Path) -> Path:
    return root / BETA / "WTF" / "Account" / ACCOUNT


@pytest.fixture
def kept(both: Path) -> Path:
    from wowlab_core import snapshot

    for character, ns in ((SETT, kept_cli.SETT_NS), (PARTICIPLE, kept_cli.PARTICIPLE_NS)):
        shutil.copytree(kept_cli.OWNER / character, _account(both) / character)
        os.utime(_account(both) / character / kept_cli.SV, ns=(ns, ns))
    snapshot.SnapshotStore().create(
        both,
        [f"{BETA}/WTF"],
        flavor_folder=BETA,
        flavor_version=kept_cli.BETA_VERSION,
        now=SNAPPED,
    )
    for character in (SETT, PARTICIPLE):
        shutil.rmtree(_account(both) / character)
    return both


def _kept_params(character: str) -> dict[str, Any]:
    return {"character": character, "account": ACCOUNT, "flavor": BETA}


def _scenario(server: Any, steps: list[dict[str, Any]]) -> dict[str, Any]:
    scenario = home_tab._scenario(server, steps)
    for character in (SETT, PARTICIPLE):
        scenario["runs"].append(hub_tab._run_of(server, KEPT, _kept_params(character)))
    return scenario


def _cards(out: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = out["steps"][0]["home"]["cards"]
    return cards


def _kept_at(out: dict[str, Any], name: str) -> int:
    return next(i for i, c in enumerate(_cards(out)) if c["name"] == name)


def test_kept_characters_are_dimmed_cards_after_the_others_constructed(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))
    cards = _cards(out)
    names = [c["name"] for c in cards]
    # The live cards first, then the kept ones (newest save first), then the dashed card.
    assert names[-3:] == ["Sangraal-Sett", "Dangling-Participle", "Missing a character?"]
    assert all("card-kept" not in c["cls"] for c in cards[:-3])
    sett, participle = cards[-3], cards[-2]
    for card in (sett, participle):
        assert "card-kept" in card["cls"].split()
        assert re.fullmatch(r"Level \d+", card["level"])
        assert re.fullmatch(r"\d+\.\d", card["ilvl"])
        assert re.fullmatch(
            r"WoW Classic Beta · kept in your backup from Oct \d{1,2}", card["foot"]
        ), card
        assert card["buttons"] == [f"card-open: {card['name']}"]
        assert card["gain"] is None
    assert "cls-warlock" in sett["cls"].split() and "cls-mage" in participle["cls"].split()
    # No names downloaded in this home: the class token in words, the race's id.
    assert sett["who"] == "Warlock · race 5"
    assert participle["who"] == "Mage · race 2"
    assert sett["level"] == "Level 15" and participle["level"] == "Level 10"


def test_a_kept_card_shows_no_folder_path_or_snapshot_id_constructed(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    from wowlab_core import snapshot

    (manifest,) = snapshot.SnapshotStore().list_lenient().manifests
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))
    for card in _cards(out)[-3:-1]:
        for word in (BETA, FOREVER, ACCOUNT, manifest.id, "WowLab.lua", "SavedVariables", "WTF/"):
            assert word not in card["text"], (word, card["text"])


def test_names_cached_name_the_kept_character_constructed(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    """Constructed: names written into the report, as once downloaded."""
    scenario = _scenario(server, [{"home": True}])
    report = home_tab._summary(scenario)
    for row in report["kept_in_backups"]:
        if row["label"] == "Sangraal-Sett":
            row["race_name"], row["class_name"] = "Undead", "Warlock"
    home_tab._edit_summary(scenario, report)
    card = home_tab._card(_drive(tmp_path / "node", scenario)["steps"][0]["home"], "Sangraal-Sett")
    assert card["who"] == "Undead Warlock"


def test_a_kept_card_opens_a_read_only_page_from_the_backup_constructed(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    probe = _drive(tmp_path / "probe", _scenario(server, [{"home": True}]))
    at = _kept_at(probe, "Sangraal-Sett")
    out = _drive(tmp_path / "node", _scenario(server, [{"kept_open": at}]))
    step = out["steps"][0]
    assert step["characterHidden"] is False
    bodies = hub_tab._bodies(step["newRequests"])
    assert {"command": KEPT, "params": _kept_params(SETT)} in bodies
    # Every run is a read; nothing restores, backs up or installs.
    assert {tuple(b["command"]) for b in bodies} <= READS
    assert step["keptHidden"] is False
    assert re.fullmatch(
        r"From your backup of Oct \d{1,2}\. This character is no longer in your game folder; "
        r"this page changes nothing\.",
        step["kept"],
    )
    char = step["char"]
    assert char["hero"]["name"] == "Sangraal-Sett"
    assert char["hero"]["who"] == "Warlock · race 5 · WoW Classic Beta"
    assert char["hero"]["tile"] == "Level 15"
    assert char["rows"], "the backup's gear is drawn"
    assert char["errors"] == []
    # No action bar (its keys are not read), and no button that writes.
    assert step["barHidden"] is True
    for text in step["buttons"]:
        for word in ("Restore", "Back up", "Install", "Apply", "Confirm", "Delete", "Remove"):
            assert word not in text, text
    # The page's own buttons are the gear's item names, which open a slot's details.
    assert step["buttons"], "the gear's items are buttons"
    # The picker holds no kept character: it lists the game folder's.
    assert step["picker"] == ""
    assert all("Sangraal" not in o["text"] for o in char["options"])


def test_keybinds_after_a_kept_page_goes_back_to_a_listed_character_constructed(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    probe = _drive(tmp_path / "probe", _scenario(server, [{"home": True}]))
    at = _kept_at(probe, "Dangling-Participle")
    out = _drive(tmp_path / "node", _scenario(server, [{"kept_open": at}, {"keys_after": True}]))
    first, after = out["steps"]
    assert first["keptHidden"] is False
    assert after["keptHidden"] is True and after["kept"] == ""
    assert after["picker"] != ""


def test_a_kept_record_that_cannot_be_read_has_no_open_button_constructed(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    """Constructed: the row as `char list --summary` gives an unreadable copy."""
    scenario = _scenario(server, [{"home": True}])
    report = home_tab._summary(scenario)
    row = report["kept_in_backups"][0]
    for name in ("level", "class_id", "class_file", "race_id", "item_level", "professions"):
        row[name] = None
    row["state"], row["error"] = "not_read", "its stored copy cannot be used"
    home_tab._edit_summary(scenario, report)
    card = home_tab._card(_drive(tmp_path / "node", scenario)["steps"][0]["home"], "Sangraal-Sett")
    assert card["buttons"] == []
    assert card["note"] == "wowlab could not read what your backup holds for this character."
    assert card["foot"].startswith("WoW Classic Beta · kept in your backup from ")


def test_hostile_kept_names_draw_as_inert_text_constructed(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"home": True}])
    report = home_tab._summary(scenario)
    row = report["kept_in_backups"][0]
    row["label"] = first_stage.IMG
    row["flavor"] = "_<b>x</b>_"
    row["class_name"] = "<b>Mage</b>"
    home_tab._edit_summary(scenario, report)
    cards = _drive(tmp_path / "node", scenario)["steps"][0]["home"]["cards"]
    card = cards[-3]
    assert card["name"] == first_stage.IMG
    assert card["foot"].startswith("WoW <b>x</b> · kept in your backup")


def test_the_kept_cards_use_text_nodes_only() -> None:
    script = (first_stage._TEMPLATES / "page.js").read_text(encoding="utf-8")
    body = script[
        script.index("// ─── characters kept only in a backup") : script.index(
            "// ─── the gains, from `char history`"
        )
    ]
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", ".style"):
        assert sink not in body, sink
    css = (first_stage._TEMPLATES / "page.css").read_text(encoding="utf-8")
    assert re.search(r"\.card-kept \{ opacity: 0\.72; \}", css)
