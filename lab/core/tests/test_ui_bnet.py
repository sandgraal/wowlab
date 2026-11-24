"""The Blizzard profile panels of `wowlab ui` (M22-11, ADR-0040 (2)).

One panel on the Character page (`bnet-char-*`), one on a crew player
(`bnet-crew-*`). Opening a character or a player runs nothing; Show the
saved profile runs `blizzard show`, and Fetch from Blizzard, offered only
when an API client is kept, runs `blizzard fetch`.

Driven under Node with the harnesses of `test_ui_character_page.py` (the
Character page) and `test_ui_crew.py` (the Crew tab over the real server):
HTML sinks throw, attributes outside a closed set and elements such as `a`,
`img` and `script` are trapped, and every request is recorded. The
`blizzard` runs' output is **constructed**: the report the real CLI's
models make (`cli.BlizzardProfileReport`) from the constructed replies of
`test_bnet.py` (provenance there), since no test asks Blizzard anything
(ADR-0012). Cases labelled `constructed` also put hostile words in it. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import test_bnet as recorded
import test_ui_account as hub_tab
import test_ui_character_page as page_tab
import test_ui_crew as crew_tab
import test_ui_home as home_tab
from test_ui_explorer import _node, _page_ids

from wowlab_core import bnet, cli, ui

both = page_tab.both
home = page_tab.home
server = page_tab.server
crew_at = crew_tab.crew_at
FOREVER = page_tab.FOREVER
SHOW = ["blizzard", "show"]
FETCH = ["blizzard", "fetch"]
CHAR_PARAMS = {"name": "Sandgraal-Rizzen", "flavor": FOREVER}
RLO = chr(0x202E)
_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"

_STEPS = r"""
      } else if (step.bnet !== undefined) {
        const before = requests.length;
        const where = step.where || "char";
        if (step.bnet === "show") { ids["bnet-" + where + "-show"].fire("click"); await tick(); await tick(); }
        if (step.bnet === "fetch") { ids["bnet-" + where + "-fetch"].fire("click"); await tick(); await tick(); }
        out.steps.push(Object.assign(bnetView(where), { newRequests: requests.slice(before) }));
"""

_VIEW = r"""
function bnetView(where) {
  const id = (s) => ids["bnet-" + where + "-" + s];
  const drawn = [];
  for (const s of ["lines", "gear", "portrait", "status"]) drawn.push(...id(s).all());
  return {
    where: where,
    showDisabled: id("show").disabled,
    fetchHidden: id("fetch").hidden,
    status: id("status").textContent,
    statusError: has(id("status"), "error"),
    lines: id("lines").children.map((c) => c.textContent),
    gear: id("gear").children.map((c) => c.textContent),
    portraitHidden: id("portrait").hidden,
    portraitChildren: id("portrait").children.length,
    drawn: drawn.map((e) => ({ tag: e.tagName, cls: e.className, id: e.id, title: e.title,
      attrs: Object.keys(e.attrs), href: e.href === undefined ? null : String(e.href),
      src: e.src === undefined ? null : String(e.src) })),
  };
}
"""


def _report(
    *, configured: bool, fetched: bool = False, profile: bnet.BnetProfile | None = None
) -> str:
    return cli.BlizzardProfileReport(
        configured=configured,
        setup=None if configured else bnet.SETUP_WORDS,
        game="forever",
        region="us",
        name="Sandgraal Rizzen",
        fetched=fetched,
        saved_fetches=0 if profile is None else 1,
        profile=profile,
    ).model_dump_json()


def _profile(**over: Any) -> bnet.BnetProfile:
    """The profile the CLI builds from the constructed replies."""
    game = bnet.game_for_version("1.60.1.70888")
    assert game is not None
    send = recorded.Recorded(recorded.answers(**over))
    profile, _ = bnet.fetch_profile(
        recorded.creds(), game, "us", recorded.NAME, send=send, now=recorded.clock(recorded.T0)
    )
    return profile


def _run(command: list[str], params: dict[str, Any], stdout: str, code: int = 0) -> dict[str, Any]:
    return {"command": command, "params": params, "stdout": stdout, "exit_code": code, "stderr": ""}


# ─── the Character page ──────────────────────────────────────────────────────


def _char_harness() -> str:
    text = page_tab._harness()
    assert text.count(home_tab._ANCHOR) == 1 and text.count(home_tab._VIEW_ANCHOR) == 1
    text = text.replace(home_tab._VIEW_ANCHOR, _VIEW + home_tab._VIEW_ANCHOR, 1)
    return text.replace(home_tab._ANCHOR, _STEPS.lstrip("\n") + home_tab._ANCHOR, 1)


def _char_drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = hub_tab._harness
    hub_tab._harness = _char_harness
    try:
        return hub_tab._drive(folder, scenario)
    finally:
        hub_tab._harness = original


def _bnet_steps(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in out["steps"] if "showDisabled" in s]


def _blizzard_bodies(out: dict[str, Any]) -> list[dict[str, Any]]:
    bodies = []
    for request in out["requests"]:
        assert request["url"].startswith(("/api/commands", "/api/run")), request
        body = request["body"]
        if body is not None and body["command"][0] == "blizzard":
            bodies.append(body)
    return bodies


def _inert(view: dict[str, Any]) -> None:
    """Everything the panel drew is plain text: no link, picture, id or
    attribute, and only its own classes."""
    for e in view["drawn"]:
        assert e["tag"] in {"li", "span", "#text"}, e
        assert e["cls"] in {"", "bnet-url"}, e
        assert e["id"] == "" and e["title"] == "" and e["attrs"] == [], e
        assert e["href"] is None and e["src"] is None, e
    assert view["portraitHidden"] is True and view["portraitChildren"] == 0


def test_without_an_api_client_the_panel_says_how_and_offers_no_fetch_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = page_tab._scenario(server, [{"bnet": "look"}, {"bnet": "show"}])
    scenario["runs"].append(_run(SHOW, CHAR_PARAMS, _report(configured=False)))
    out = _char_drive(tmp_path / "node", scenario)
    looked, shown = _bnet_steps(out)
    # Opening the character ran nothing of Blizzard's.
    assert looked["showDisabled"] is False and looked["fetchHidden"] is True
    assert looked["lines"] == [] and looked["status"] == ""
    assert shown["newRequests"][0]["body"] == {"command": SHOW, "params": CHAR_PARAMS}
    assert _blizzard_bodies(out) == [{"command": SHOW, "params": CHAR_PARAMS}]
    assert shown["fetchHidden"] is True
    assert shown["status"].startswith("Nothing fetched from the armory for Sandgraal Rizzen yet.")
    assert bnet.SETUP_WORDS in shown["status"]
    assert "develop.battle.net" in shown["status"] and "wowlab blizzard login" in shown["status"]
    assert shown["lines"] == [] and shown["gear"] == []
    _inert(shown)
    assert not out["traps"]


def test_a_saved_profile_shows_each_part_with_its_time_then_fetch_asks_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    profile = _profile()
    later = _profile(media=recorded.reply(404)).model_copy(
        update={
            "portrait": profile.portrait,
            "portrait_state": profile.portrait_state.model_copy(update={"from_earlier": True}),
        }
    )
    scenario = page_tab._scenario(server, [{"bnet": "show"}, {"bnet": "fetch"}])
    scenario["runs"].append(_run(SHOW, CHAR_PARAMS, _report(configured=True, profile=profile)))
    scenario["runs"].append(
        _run(FETCH, CHAR_PARAMS, _report(configured=True, fetched=True, profile=later))
    )
    out = _char_drive(tmp_path / "node", scenario)
    shown, fetched = _bnet_steps(out)
    assert _blizzard_bodies(out) == [
        {"command": SHOW, "params": CHAR_PARAMS},
        {"command": FETCH, "params": CHAR_PARAMS},
    ]
    assert shown["fetchHidden"] is False and shown["status"] == ""
    assert shown["lines"] == [
        "Level 29 Undead Warlock (Affliction), Horde; ruleset: PvP · fetched 2026-11-24 18:00 UTC",
        "Item level: 31 average, 30 equipped",
        "Guild: The Crew (12 members) · fetched 2026-11-24 18:00 UTC",
        f"Portrait: {recorded.RENDER}-avatar.jpg · fetched 2026-11-24 18:00 UTC",
        "Gear: 2 items · fetched 2026-11-24 18:00 UTC",
    ]
    assert shown["gear"] == [
        "Head: Simple Blouse (item level 30, Uncommon)",
        "Ranged: Shadow Wand (item level 32, Rare)",
    ]
    _inert(shown)
    # The portrait's address is text, never loaded or linked.
    assert any(e["cls"] == "bnet-url" for e in shown["drawn"])
    assert fetched["lines"][3].endswith("(an earlier fetch; the last one did not get it)")
    assert not out["traps"]


def test_hostile_words_are_text_and_a_failed_fetch_says_why_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    hostile = f'<img src=x onerror="alert(1)">{RLO}Crew'
    body = {**recorded.PROFILE_BODY, "race": {"name": hostile}}
    guild = {**recorded.GUILD_BODY, "name": "<script>alert(1)</script>"}
    profile = _profile(profile=recorded.reply(200, body), guild=recorded.reply(200, guild))
    missing = _profile(profile=recorded.reply(404))
    scenario = page_tab._scenario(server, [{"bnet": "show"}, {"bnet": "fetch"}])
    scenario["runs"].append(_run(SHOW, CHAR_PARAMS, _report(configured=True, profile=profile)))
    scenario["runs"].append(
        _run(FETCH, CHAR_PARAMS, _report(configured=True, fetched=True, profile=missing), code=1)
    )
    out = _char_drive(tmp_path / "node", scenario)
    shown, failed = _bnet_steps(out)
    assert shown["lines"][0].startswith(
        'Level 29 <img src=x onerror="alert(1)">⟨U+202E⟩Crew Warlock'
    )
    assert shown["lines"][2].startswith("Guild: <script>alert(1)</script> (12 members)")
    _inert(shown)
    # A fetch that ended with an error still shows its report, and says so.
    assert failed["statusError"] is True
    assert failed["lines"][0].startswith("Profile: Blizzard has no public profile")
    _inert(failed)
    assert not out["traps"]


@pytest.mark.parametrize(
    "stdout",
    ["not json", json.dumps({"configured": "yes"}), json.dumps([1, 2])],
    ids=["constructed: not json", "constructed: another shape", "constructed: a list"],
)
def test_a_report_of_another_shape_is_an_error_line_constructed(
    server: Any, both: Path, tmp_path: Path, stdout: str
) -> None:
    scenario = page_tab._scenario(server, [{"bnet": "show"}])
    scenario["runs"].append(_run(SHOW, CHAR_PARAMS, stdout))
    out = _char_drive(tmp_path / "node", scenario)
    (shown,) = _bnet_steps(out)
    assert shown["statusError"] is True and shown["lines"] == []
    assert shown["fetchHidden"] is True
    assert not out["traps"]


# ─── a crew player ───────────────────────────────────────────────────────────


def _crew_harness() -> str:
    text = crew_tab._harness()
    assert text.count(crew_tab._ANCHOR) == 1 and text.count(crew_tab._VIEW_ANCHOR) == 1
    text = text.replace(crew_tab._VIEW_ANCHOR, _VIEW + crew_tab._VIEW_ANCHOR, 1)
    return text.replace(crew_tab._ANCHOR, _STEPS.lstrip("\n") + crew_tab._ANCHOR, 1)


def _crew_drive(
    crew: Any, folder: Path, steps: list[dict[str, Any]], runs: list[dict[str, Any]]
) -> dict[str, Any]:
    """`test_ui_crew._drive`, with `runs` for the stand-in (the blizzard
    runs; every crew run still goes to the real server)."""
    folder.mkdir(parents=True, exist_ok=True)
    scenario = {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": {"commands": crew_tab._commands(crew.server), "home": "", "sep": os.sep},
        "first_flavor": crew_tab.FLAVOR,
        "trees": {},
        "explains": {},
        "runs": runs,
        "held": [],
        "steps": steps,
        "crew_server": f"http://127.0.0.1:{crew.server.port}",
        "crew_token": crew.server.token,
        "outcomes": {
            str(code): ui._outcome(ui._Run(code, [], [], False, False), None) for code in range(4)
        },
    }
    harness = folder / "m22-11-harness.js"
    harness.write_text(_crew_harness(), encoding="utf-8")
    data = folder / "m22-11-scenario.json"
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
    return out


def test_a_crew_player_gets_the_panel_on_a_click_only_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    crew = crew_at(crew_tab._three())
    key = crew_tab.KEY_A
    candidates = [{"name": key}, {"name": key, "flavor": crew_tab.FLAVOR}]
    profile = _profile()
    runs = [_run(SHOW, p, _report(configured=True, profile=profile)) for p in candidates]
    steps = [
        {"crew_tab": True},
        {"crew_open": 0},
        {"bnet": "look", "where": "crew"},
        {"bnet": "show", "where": "crew"},
    ]
    out = _crew_drive(crew, tmp_path / "node", steps, runs)
    opened = out["steps"][1]
    assert opened["player"]["hidden"] is False
    looked, shown = _bnet_steps(out)
    assert looked["showDisabled"] is False and looked["lines"] == []
    bodies = _blizzard_bodies(out)
    assert len(bodies) == 1 and bodies[0]["command"] == SHOW
    assert bodies[0]["params"] in candidates
    assert shown["lines"][0].startswith("Level 29 Undead Warlock")
    assert shown["fetchHidden"] is False
    _inert(shown)


# ─── the markup and the script ───────────────────────────────────────────────


def _section() -> str:
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    start = script.index("// ─── armory profiles (M22-11) ───")
    return script[start : script.index("// ─── the account hub", start)]


def test_the_panels_run_only_blizzard_show_and_fetch_and_draw_text_only() -> None:
    body = _section()
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))
    assert re.findall(r"runJson\(\[([^\]]*)\]", code) == [
        '"blizzard", "fetch"',
        '"blizzard", "show"',
    ]
    # The file map's words stay out of the script (test_ui_explorer.py).
    assert "Blizzard" not in code
    for sink in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "setAttribute",
        ".href",
        ".src",
        "dataset",
        "eval(",
    ):
        assert sink not in code, sink
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    # Opening a character or a player only remembers the name.
    assert script.count('bnetFor("char", ') == 1 and script.count('bnetFor("crew", ') == 1
    assert (
        script.count('runBnet(where, "show")') == 1 and script.count('runBnet(where, "fetch")') == 1
    )


def test_the_panels_are_static_markup_with_no_link_or_picture() -> None:
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    for where in ("char", "crew"):
        start = page.index(f'<section id="bnet-{where}-panel"')
        part = page[start : page.index("</section>", start)]
        assert (
            "<a " not in part and "<img" not in part and "href" not in part and "src=" not in part
        )
        assert f'<button id="bnet-{where}-fetch" type="button" hidden>' in part
        assert f'<button id="bnet-{where}-show" type="button" disabled>' in part


def test_the_csp_is_unchanged_by_the_panels(server: Any) -> None:
    csp = page_tab.first_stage._page(server).headers["Content-Security-Policy"]
    assert "blizzard" not in csp.lower() and "worldofwarcraft" not in csp.lower()
    assert "https:" not in csp and "zamimg" not in csp
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp


def test_login_and_logout_are_not_offered_from_the_page(server: Any) -> None:
    listed = {tuple(c["path"]): c for c in crew_tab._commands(server)}
    assert listed[("blizzard", "login")]["excluded"] == (
        "the API client's secret is typed in a terminal, not into a page"
    )
    # Logout removes the kept client in one run, with no confirm step.
    assert listed[("blizzard", "logout")]["excluded"] == (
        "removing the API client is offered on the command line only"
    )
    for leaf in (
        ("blizzard", "show"),
        ("blizzard", "fetch"),
        ("blizzard", "status"),
    ):
        assert listed[leaf]["excluded"] is None, leaf
