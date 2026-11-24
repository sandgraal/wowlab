"""Profile links on the Crew tab of `wowlab ui` (M21-05).

A player opened from the crew list, or from the players a match or an
open-world fight names, shows their profile links (the Blizzard armory, the
Journey Companion, Raider.IO) in the panel's own static links of page.html,
each only when the address `crew show --json` carries passes the page's
check for that link's site; a link with no address says why, as text. The
Spy panel's player does the same from `spy show --json`.

Two installs, both copies in `tmp_path` of the captured
`fixtures/macos-70888/` (a Forever client, `portal "US"`): a crew book
built at run time by `parser/_crew_build.py` (CONSTRUCTED, every key one of
its invented keys or assembled from one), through `test_ui_crew.py`'s
fixture; and the real Spy capture (`fixtures/macos-71037/`, its
`WTF/Account` only), through `test_ui_spy.py`'s. The page runs under Node
with their harness (the stand-in DOM of `test_ui_explorer.py`: HTML sinks
throw; navigation, attributes outside a closed set and elements such as
`a` and `img` are trapped, so every link is one of page.html's), routing
each `crew` and `spy` run to the real server and the real CLI with the user
data directory in `tmp_path`. The allowlist of outside origins is what the
server serves (`links.origins()`), or, in tests named `constructed`, a
hostile one. Nothing reads or writes a real install, and no address is
requested.
"""

# ruff: noqa: F811 - `crew_at` and `served` are imported fixtures, requested by name

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import test_ui_crew as crew_tab
import test_ui_spy as spy_tab
from test_ui_confirm import _commands
from test_ui_crew import crew_at  # noqa: F401 - the fixture
from test_ui_explorer import _node, _page_ids
from test_ui_spy import served  # noqa: F401 - the fixture

from wowlab_core import links, ui

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _crew_build import FLAVOR, KEY_A, KEY_B, KEY_C, KEY_D, book, match, player

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
ARMORY = "https://worldofwarcraft.blizzard.com/en-us/forever/us/armory/character/"
JOURNEY = "https://wofwforever.com/en/tools/one-shot-manager/?region=us&"
RULESET = "the character's ruleset (Normal, PvP, Roleplay or Roleplay PvP) is not known"

_VIEW = r"""
// Node's own URL parser, as `test_ui_dressing.py` gives it: the page's
// check of an outside address parses it.
ctx.URL = URL;
function profileView(panel) {
  const slot = (s) => {
    const a = ids[panel + "-profile-" + s];
    return { hidden: a.hidden, href: a.href === undefined ? null : String(a.href) };
  };
  const why = ids[panel + "-profiles-why"];
  return { hidden: ids[panel + "-profiles"].hidden, armory: slot("armory"), journey: slot("journey"),
    raiderio: slot("raiderio"), why: why.children.map((c) => c.textContent), whyHidden: why.hidden,
    title: ids[panel + "-player-title"].textContent };
}
function peopleView() {
  const list = ids["crew-matches-people"];
  return { hidden: list.hidden, wordsHidden: ids["crew-matches-people-words"].hidden,
    names: list.all().filter((e) => e.tagName === "button").map((e) => e.textContent),
    classes: list.all().map((e) => e.className), titles: list.all().map((e) => e.title) };
}
"""

_STEPS = r"""
      } else if (step.links_open !== undefined) {
        const opens = ids[step.links_open + "-table"].all().filter((e) => e.tagName === "button" && has(e, "hub-open"));
        if (!opens[step.row]) { out.steps.push({ missing: "open " + step.row }); continue; }
        opens[step.row].fire("click"); await settle();
        out.steps.push({ profiles: profileView(step.links_open) });
      } else if (step.links_click !== undefined) {
        ids[step.links_click].fire("click"); await settle();
        out.steps.push({ people: peopleView() });
      } else if (step.links_person !== undefined) {
        const opens = ids["crew-matches-people"].all().filter((e) => e.tagName === "button");
        if (!opens[step.links_person]) { out.steps.push({ missing: "person " + step.links_person }); continue; }
        opens[step.links_person].fire("click"); await settle();
        out.steps.push({ profiles: profileView("crew"), people: peopleView() });
"""


def _harness() -> str:
    text = spy_tab._harness()
    text = text.replace(crew_tab._VIEW_ANCHOR, _VIEW + crew_tab._VIEW_ANCHOR, 1)
    return text.replace(crew_tab._ANCHOR, _STEPS.lstrip("\n") + crew_tab._ANCHOR, 1)


def _drive(
    server: ui.UiServer,
    folder: Path,
    steps: list[dict[str, Any]],
    origins: Any = None,
    sent: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Runs `steps` after opening the Crew tab; returns what each step
    recorded. `origins` replaces the served allowlist (constructed); `sent`,
    when given, gets the body of every crew and spy run the page asked for."""
    folder.mkdir(parents=True, exist_ok=True)
    served_origins = links.origins() if origins is None else origins
    scenario = {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": {
            "commands": _commands(server),
            "home": "",
            "sep": os.sep,
            "link_origins": served_origins,
        },
        "first_flavor": FLAVOR,
        "trees": {},
        "explains": {},
        "runs": [],
        "held": [],
        "steps": [{"crew_tab": True}, *steps],
        "crew_server": f"http://127.0.0.1:{server.port}",
        "crew_token": server.token,
        "outcomes": {
            str(code): ui._outcome(ui._Run(code, [], [], False, False), None) for code in range(4)
        },
    }
    harness = folder / "m21-05-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m21-05-scenario.json"
    data.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run(
        [_node(), str(harness), str(_TEMPLATES / "page.js"), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert "error" not in out, out["error"]
    assert not out["traps"], out["traps"]
    if sent is not None:
        sent.extend(r["body"] for r in out["requests"] if r["body"] and "command" in r["body"])
    steps_out: list[dict[str, Any]] = out["steps"][1:]
    assert not any("missing" in s for s in steps_out), steps_out
    return steps_out


def _match_book() -> bytes:
    """Four invented players and one battleground: A and B with you, C and
    D against you (constructed)."""
    return book(
        {KEY_A: player(), KEY_B: player(rating="good", met=7), KEY_C: player(), KEY_D: player()},
        schema=2,
        matches=[match([KEY_A, KEY_B], [KEY_C, KEY_D])],
    )


def _shows_links(view: dict[str, Any], slug: str, first: str, second: str) -> None:
    assert view["hidden"] is False, json.dumps(view)
    assert view["armory"] == {"hidden": False, "href": ARMORY + slug}
    assert view["journey"] == {"hidden": False, "href": f"{JOURNEY}name={first}&surname={second}"}
    assert view["raiderio"]["hidden"] is True and view["raiderio"]["href"] is None
    assert view["why"] == [f"Raider.IO: no link: {RULESET}"] and view["whyHidden"] is False


def test_a_crew_row_opens_the_players_links(crew_at: Any, tmp_path: Path) -> None:
    crew = crew_at(_match_book())
    listed = [p["key"] for p in crew.report(crew_tab.LIST, {"sort": "last"})["players"]]
    row = listed.index(KEY_C)
    (step,) = _drive(crew.server, tmp_path / "node", [{"links_open": "crew", "row": row}])
    view = step["profiles"]
    assert view["title"] == KEY_C
    _shows_links(view, "inventedc-otherfakerealm", "Inventedc", "Otherfakerealm")


def test_the_players_of_a_match_each_open_their_links(crew_at: Any, tmp_path: Path) -> None:
    """Both teams of the match history: a button per player, by stored
    key, that opens their panel with their links."""
    crew = crew_at(_match_book())
    first, opened = _drive(
        crew.server,
        tmp_path / "node",
        [{"links_click": "crew-matches-show"}, {"links_person": 3}],
    )
    people = first["people"]
    assert people["hidden"] is False and people["wordsHidden"] is False
    assert people["names"] == [KEY_A, KEY_B, KEY_C, KEY_D]
    # Fixed classes and no title: nothing drawn carries crew data but text.
    assert set(people["classes"]) <= {"", "hub-open"} and set(people["titles"]) == {""}
    view = opened["profiles"]
    assert view["title"] == KEY_D
    _shows_links(view, "inventedd-otherfakerealm", "Inventedd", "Otherfakerealm")


def test_a_book_with_no_match_lists_no_players(crew_at: Any, tmp_path: Path) -> None:
    crew = crew_at(book({KEY_A: player()}))
    (step,) = _drive(crew.server, tmp_path / "node", [{"links_click": "crew-matches-show"}])
    assert step["people"]["hidden"] is True and step["people"]["wordsHidden"] is True
    assert step["people"]["names"] == []


def test_constructed_a_hostile_key_gets_reasons_as_text(crew_at: Any, tmp_path: Path) -> None:
    """A key holding markup, a script tag and control characters (assembled
    at run time): no address is built, and the reasons are text."""
    hostile = crew_tab._hostile(KEY_A)
    crew = crew_at(book({hostile: player()}))
    (step,) = _drive(crew.server, tmp_path / "node", [{"links_open": "crew", "row": 0}])
    view = step["profiles"]
    assert view["hidden"] is True
    for slot in ("armory", "journey", "raiderio"):
        assert view[slot]["hidden"] is True and view[slot]["href"] is None
    assert view["why"][0].startswith("Armory: no link: the first name holds")
    assert view["why"][1].startswith("Journey Companion: no link: the first name holds")


def test_constructed_an_origin_off_the_list_draws_no_link(crew_at: Any, tmp_path: Path) -> None:
    """The served allowlist names the armory's site with an `http:` origin
    and leaves the Journey Companion out (constructed): neither address is
    drawn as a link, and each says so."""
    crew = crew_at(book({KEY_A: player()}))
    origins = {**links.origins(), "blizzard": "http://worldofwarcraft.blizzard.com"}
    del origins["journey"]
    (step,) = _drive(
        crew.server, tmp_path / "node", [{"links_open": "crew", "row": 0}], origins=origins
    )
    view = step["profiles"]
    assert view["hidden"] is True
    assert view["armory"]["href"] is None and view["journey"]["href"] is None
    assert view["why"] == [
        "Armory: no link: its address is not one this page links to",
        "Journey Companion: no link: its address is not one this page links to",
        f"Raider.IO: no link: {RULESET}",
    ]


def test_a_spy_row_opens_the_players_links(served: Any, tmp_path: Path) -> None:
    """The real Spy capture: the first row (newest first) is Nefzik-Spoonz."""
    spy = served()
    first = spy.report(("spy", "list"), {"sort": "last"})["players"][0]
    assert first["key"] == "Nefzik-Spoonz"
    steps = [{"spy_click": "spy-show"}, {"links_open": "spy", "row": 0}]
    _, step = _drive(spy.server, tmp_path / "node", steps)
    view = step["profiles"]
    assert view["title"] == "Nefzik-Spoonz"
    _shows_links(view, "nefzik-spoonz", "Nefzik", "Spoonz")


def test_the_list_and_the_match_history_ask_for_no_links(crew_at: Any, tmp_path: Path) -> None:
    """The list and the match history draw no profile link, so the page
    asks for none (`--no-links`), which keeps a long list's report small;
    the player opened gets theirs from `crew show`, which carries them."""
    crew = crew_at(_match_book())
    sent: list[dict[str, Any]] = []
    _drive(
        crew.server,
        tmp_path / "node",
        [{"links_click": "crew-matches-show"}, {"links_person": 0}],
        sent=sent,
    )
    by_command: dict[str, list[dict[str, Any]]] = {}
    for body in sent:
        by_command.setdefault(" ".join(body["command"]), []).append(body["params"])
    assert by_command["crew list"] and all(p["no_links"] is True for p in by_command["crew list"])
    assert by_command["crew matches"] == [
        {"account": crew_tab.ACCOUNT, "flavor": FLAVOR, "no_links": True}
    ]
    assert all("no_links" not in p for p in by_command["crew show"])
    # What those runs print: no link in the list or the history, the
    # player's own in crew show.
    listed = crew.report(crew_tab.LIST, {"sort": "last", "no_links": True})
    assert listed["players"] and all(p["links"] == [] for p in listed["players"])
    history = crew.report(("crew", "matches"), {"no_links": True})
    assert history["profiles"] and all(p["links"] == [] for p in history["profiles"])
    assert all(p["links"] for p in crew.report(crew_tab.LIST, {"sort": "last"})["players"])
    shown = crew.report(crew_tab.SHOW, {"name": KEY_A})
    assert [m["link"] for m in shown["player"]["links"]] == [
        "armory_character",
        "journey",
        "raiderio",
    ]
