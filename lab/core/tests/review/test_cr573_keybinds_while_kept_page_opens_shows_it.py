# Probe from review of m18/06-backup-characters; reproduces Keybinds clicked while a kept card is opening leaves the kept page under the Keybinds tab
"""`openKept` awaits the Character tab's list before `pickKept` sets
`character.kept`. Keybinds clicked in that window finds `character.kept`
null, so `showTab` does not call `leaveKept`; the list then resolves,
`pickKept` draws the kept page, and the Keybinds tab shows a character
whose keys are not read (no keys, the action bar hidden). Seen in headless
Chrome too, at 1440 and 390 px. The positive control is the same two clicks
with the open allowed to finish first (`leaveKept` runs). Constructed store
(L8, a boundary input) as test_ui_kept.py builds it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_home as home_tab
import test_ui_kept as kt

both = kt.both
home = kt.home
server = kt.server
no_env_flavor = kt.no_env_flavor
kept = kt.kept

_RACE = r"""
      } else if (step.kept_then_keys !== undefined) {
        const card = ids["char-cards"].children[step.kept_then_keys];
        const button = card && card.all().find((e) => e.tagName === "button" && has(e, "card-open"));
        if (!button) { out.steps.push({ missing: "open " + step.kept_then_keys }); continue; }
        button.fire("click");
        ids["sec-keybinds"].fire("click");
        await tick(); await tick();
        out.steps.push({ kept: ids["char-kept"].textContent, keptHidden: ids["char-kept"].hidden,
          barHidden: ids["char-bar-panel"].hidden, picker: ids["char-picker"].value });
"""


def _harness() -> str:
    text = kt._HOME_HARNESS()
    return text.replace(kt._ANCHOR, kt._STEPS.lstrip("\n") + _RACE.lstrip("\n") + kt._ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = home_tab._harness
    home_tab._harness = _harness
    try:
        return home_tab._drive(folder, scenario)
    finally:
        home_tab._harness = original


def test_positive_control_keybinds_after_the_open_leaves_the_kept_page(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    probe = _drive(tmp_path / "probe", kt._scenario(server, [{"home": True}]))
    at = kt._kept_at(probe, "Sangraal-Sett")
    out = _drive(tmp_path / "node", kt._scenario(server, [{"kept_open": at}, {"keys_after": True}]))
    assert out["steps"][1]["keptHidden"] is True


def test_keybinds_clicked_while_a_kept_card_opens_leaves_the_kept_page(
    server: Any, kept: Path, tmp_path: Path
) -> None:
    probe = _drive(tmp_path / "probe", kt._scenario(server, [{"home": True}]))
    at = kt._kept_at(probe, "Sangraal-Sett")
    out = _drive(tmp_path / "node", kt._scenario(server, [{"kept_then_keys": at}]))
    step = out["steps"][0]
    assert "missing" not in step, step
    # Keybinds is the tab shown: its page is a listed character's, with its keys.
    assert step["keptHidden"] is True, step
    assert step["barHidden"] is False, step
