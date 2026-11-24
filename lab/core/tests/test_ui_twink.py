"""The Character tab's Twink panel (M22-06).

Driven under Node with the harness of `test_ui_character_page.py` (that of
`test_ui_home.py` and the modules before it: HTML sinks throw, attributes
outside a closed set and navigation are trapped, requests outside the
server's API are recorded), fed what the real CLI printed through the real
server for the two captures side by side (`constructed`, L8, as that module
says): the warlock's card (Sandgraal, `macos-70888`) is opened, then the
panel is used. The game tables of 1.60.1.70888 are the recordings under
`fixtures/wago/`, put into the server's stand-in data folder before the
runs, so `char twink` downloads nothing.

Cases labelled `constructed` edit the real `char twink` report before the
page reads it: hostile names and words, addresses for other origins, a
report of another shape, a failed run. The page must show each as text,
give a link only an address of its own origin, and make no request but its
own `/api/run`. Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import test_ui_account as hub_tab
import test_ui_character_page as page_tab
import test_ui_home as home_tab

from wowlab_core import gamedata, twink, ui

both = page_tab.both
home = page_tab.home
server = page_tab.server
PARAMS = page_tab.PARAMS
TWINK = ["char", "twink"]
PICKED = {**PARAMS, "bracket": 29, "weights": "affliction"}
BUILD = "1.60.1.70888"
WAGO = Path(__file__).resolve().parent / "fixtures" / "wago"
PLANNER = "https://forevertwinking.com/planner?class=warlock&race=undead&bracket=l29"
RLO = chr(0x202E)  # a right-to-left override, written by its number
_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"

_STEPS = r"""
      } else if (step.twink !== undefined) {
        const before = requests.length;
        if (step.twink === "show") { ids["char-twink-show"].fire("click"); await tick(); }
        if (step.twink === "bracket") {
          ids["char-twink-bracket"].value = String(step.value);
          ids["char-twink-bracket"].fire("change"); await tick();
        }
        if (step.twink === "open") {
          const names = ids["char-twink"].all().filter((e) => e.tagName === "button" && has(e, "gear-name"));
          if (!names[step.at]) { out.steps.push({ missing: "candidate " + step.at }); continue; }
          names[step.at].fire("click"); await tick();
        }
        out.steps.push(Object.assign(twinkView(), { newRequests: requests.slice(before) }));
"""

_VIEW = r"""
function twinkView() {
  const box = ids["char-twink"];
  const planner = ids["char-twink-planner"];
  const wowhead = ids["char-twink-wowhead"];
  const textOf = (node, c) => { const e = node.all().find((x) => has(x, c)); return e ? e.textContent : null; };
  return {
    panelHidden: ids["char-twink-panel"].hidden,
    showDisabled: ids["char-twink-show"].disabled,
    status: ids["char-twink-status"].textContent,
    statusError: has(ids["char-twink-status"], "error"),
    brackets: Array.from(ids["char-twink-bracket"].options, (o) => [o.value, o.textContent]),
    bracket: ids["char-twink-bracket"].value,
    weights: Array.from(ids["char-twink-weights"].options, (o) => [o.value, o.textContent]),
    weight: ids["char-twink-weights"].value,
    plannerHidden: planner.hidden, plannerHref: planner.href || null,
    wowheadHidden: wowhead.hidden, wowheadHref: wowhead.href || null, wowheadText: wowhead.textContent,
    slots: box.all().filter((e) => has(e, "twink-slot")).map((li) => ({
      slot: textOf(li, "gear-slot"), worn: textOf(li, "twink-worn"), gap: textOf(li, "twink-gap"),
      candidates: li.all().filter((e) => has(e, "twink-cand")).map((c) => ({
        name: textOf(c, "gear-name"), facts: textOf(c, "twink-facts"),
        detailHidden: c.all().find((x) => has(x, "gear-detail")).hidden,
        detail: c.all().filter((x) => x.tagName === "dt" || x.tagName === "dd").map((x) => x.textContent) })),
    })),
    notes: ids["char-twink-notes"].all().filter((e) => e.tagName === "li").map((e) => e.textContent),
  };
}
"""


def _harness() -> str:
    text = page_tab._harness()
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


@pytest.fixture
def tables(home: Path) -> Path:
    """The recorded tables of `BUILD` in the server's stand-in data folder."""
    folder = gamedata.default_cache_dir() / "tables" / BUILD
    folder.mkdir(parents=True)
    for table in twink.TABLES:
        whole = WAGO / f"{table}.{BUILD}.csv"
        source = whole if whole.is_file() else WAGO / f"{table}.{BUILD}.subset.csv"
        shutil.copyfile(source, folder / f"{table}.csv")
    return folder


def _scenario(server: Any, steps: list[dict[str, Any]]) -> dict[str, Any]:
    scenario = page_tab._scenario(server, steps)
    scenario["runs"].append(hub_tab._run_of(server, TWINK, PARAMS))
    scenario["runs"].append(hub_tab._run_of(server, TWINK, PICKED))
    return scenario


def _twink_run(scenario: dict[str, Any], bracket: int | None = None) -> dict[str, Any]:
    params = PARAMS if bracket is None else {**PICKED, "bracket": bracket}
    [run] = [r for r in scenario["runs"] if r["command"] == TWINK and r["params"] == params]
    return run


def _report(scenario: dict[str, Any], bracket: int | None = None) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(_twink_run(scenario, bracket)["stdout"])
    return report


def _only_api(out: dict[str, Any]) -> list[dict[str, Any]]:
    bodies = []
    for request in out["requests"]:
        assert request["url"].startswith(("/api/commands", "/api/run")), request
        if request["body"] is not None:
            bodies.append(request["body"])
    return bodies


def _twink_steps(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in out["steps"] if "panelHidden" in s]


# ─── the markup ──────────────────────────────────────────────────────────────


def test_the_links_are_static_markup_with_no_href() -> None:
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    anchors = re.findall(r"<a\b[^>]*>", page)
    twink_links = [
        '<a id="char-twink-planner" class="dressing-link" target="_blank" '
        'rel="noopener noreferrer" hidden>',
        '<a id="char-twink-wowhead" class="dressing-link" target="_blank" '
        'rel="noopener noreferrer" hidden>',
    ]
    assert anchors[-2:] == twink_links  # after the dressing room's and M21-03's
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    # Since M21-03 the page names no origin: each twink link takes only an
    # address checked against the origin the server's allowlist gives its
    # own site (`link_sites.toml`; test_ui_links.py).
    assert "PLANNER_ORIGIN" not in script and "DRESSING_ORIGIN" not in script
    assert script.count('outsideAddress(panel.planner, "forevertwinking")') == 1
    assert script.count('outsideAddress(c.wowhead, "wowhead")') == 1
    assert script.count("link.href = checked;") == 1
    # The one sink's helper: its definition and its calls, each given an
    # address `outsideAddress` returned for that link's own site (the
    # dressing room, the three saved links, the planner, the Wowhead link,
    # and M21-04's `pointOutside`, which every profile, guild and gear-row
    # link goes through; test_ui_char_links.py; and since M21-06 See my
    # look, graded in test_ui_purist.py).
    assert script.count("pointLink(") == 7
    assert script.count("function pointLink(link, checked) {") == 1
    for call, checked in (
        ("pointLink(link, checked);", "const checked = dressingAddress(dressing.url);"),
        ("pointLink(link, checked);", "const checked = outsideAddress(row.url, slot.site);"),
        (
            "pointLink(link, planner);",
            'const planner = outsideAddress(panel.planner, "forevertwinking");',
        ),
        ("pointLink(link, address);", 'outsideAddress(c.wowhead, "wowhead")'),
        ("pointLink(anchor, checked);", "const checked = outsideAddress(url, site);"),
    ):
        assert script.count(call) == (2 if call == "pointLink(link, checked);" else 1), call
        assert script.count(checked) == 1, checked
        assert script.index(checked) < script.index(call, script.index(checked)), call
    assert script.count('runJson(["char", "twink"], params)') == 1


def test_the_csp_is_unchanged_by_the_panel(server: Any) -> None:
    csp = page_tab.first_stage._page(server).headers["Content-Security-Policy"]
    assert "forevertwinking" not in csp.lower() and "https:" not in csp
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp


# ─── the panel ───────────────────────────────────────────────────────────────


def test_nothing_runs_until_asked_then_the_gaps_show(
    server: Any, both: Path, tables: Path, tmp_path: Path, home: Path
) -> None:
    scenario = _scenario(server, [{"twink": "look"}, {"twink": "show"}])
    run = _twink_run(scenario)
    assert run["exit_code"] == 0, run["stderr"]
    assert "downloading" not in run["stderr"]  # the recorded tables were found
    report = _report(scenario)
    panel = report["twink"]
    assert (panel["bracket"], panel["tables_build"]) == (19, BUILD)
    out = _drive(tmp_path / "node", scenario)
    looked, shown = _twink_steps(out)
    # Before Show: the pickers' defaults and no run of char twink.
    assert looked["panelHidden"] is False and looked["showDisabled"] is False
    assert looked["brackets"] == [["", "The first at or above this level"]]
    assert looked["weights"] == [["", "The class's first"]]
    assert looked["slots"] == [] and looked["plannerHidden"] is True
    bodies = _only_api(out)
    assert [b for b in bodies if b["command"] == TWINK] == [{"command": TWINK, "params": PARAMS}]
    assert shown["newRequests"] and all(r["url"] == "/api/run" for r in shown["newRequests"])
    # After: the report's own words, slot by slot.
    assert shown["status"] == panel["words"] and shown["statusError"] is False
    assert shown["brackets"] == [[str(b), f"Level {b}"] for b in panel["brackets"]]
    assert shown["bracket"] == "19" and shown["weight"] == "affliction"
    assert [w for w, _ in shown["weights"]] == panel["weight_sets"]
    assert [s["gap"] for s in shown["slots"]] == [s["gap_words"] for s in panel["slots"]]
    head = shown["slots"][0]
    assert head["slot"] == "Head" and head["worn"] == "Empty"
    first = panel["slots"][0]["candidates"][0]
    assert head["candidates"][0]["name"] == first["name"]
    assert head["candidates"][0]["facts"].startswith(f"score {first['score']:g}")
    assert all(c["detailHidden"] for s in shown["slots"] for c in s["candidates"])
    chest = next(s for s in shown["slots"] if s["slot"] == "Chest")
    assert chest["worn"] == "Worn: Barbaric Linen Vest · score 3.6"
    waist = next(s for s in shown["slots"] if s["slot"] == "Waist")
    assert waist["worn"] == (
        "Worn: Aboriginal Sash of the Sorcerer · not scored: a hotfix the copy of the game "
        "tables wowlab downloads lacks"
    )
    assert shown["plannerHidden"] is False
    assert shown["plannerHref"] == PLANNER.replace("l29", "l19")
    assert shown["notes"] == panel["notes"]
    # Where the weights can be edited, the home folder written as "~".
    edit = [n for n in shown["notes"] if n.startswith("The weights, the brackets and each class")]
    assert len(edit) == 1 and "twink.toml" in edit[0] and str(home) not in edit[0]
    assert not out["traps"]


def test_a_bracket_picked_runs_again_and_an_item_opens_its_wowhead_link(
    server: Any, both: Path, tables: Path, tmp_path: Path
) -> None:
    steps = [{"twink": "show"}, {"twink": "bracket", "value": 29}, {"twink": "open", "at": 0}]
    scenario = _scenario(server, steps)
    panel = _report(scenario, 29)["twink"]
    out = _drive(tmp_path / "node", scenario)
    found = _twink_steps(out)
    assert len(found) == 3, [s.get("missing") or sorted(s)[:4] for s in out["steps"]]
    _, picked, opened = found
    bodies = [b for b in _only_api(out) if b["command"] == TWINK]
    # The second run names what the pickers show: the bracket picked and the
    # weight set the first run used.
    assert bodies[-1] == {"command": TWINK, "params": PICKED}
    assert picked["bracket"] == "29" and picked["status"] == panel["words"]
    assert picked["plannerHref"] == PLANNER
    assert picked["wowheadHidden"] is True
    first = panel["slots"][0]["candidates"][0]
    assert opened["newRequests"] == []  # opening an item asks for nothing
    assert opened["wowheadHidden"] is False and opened["wowheadHref"] == first["wowhead"]
    assert opened["wowheadText"] == f"Open {first['name']} on Wowhead"
    detail = opened["slots"][0]["candidates"][0]
    assert detail["detailHidden"] is False
    assert detail["detail"][:2] == ["Stats", "12 stamina, 14 shadow damage"]
    assert detail["detail"][-2:] == ["Where to get it", "not known yet"]


def test_hostile_words_are_text_and_foreign_addresses_get_no_link_constructed(
    server: Any, both: Path, tables: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"twink": "show"}, {"twink": "open", "at": 0}])
    report = _report(scenario)
    panel = report["twink"]
    panel["words"] = "<img src=x onerror=alert(1)>" + RLO
    panel["planner"] = "https://forevertwinking.com.evil.example/planner"
    first = panel["slots"][0]["candidates"][0]
    first["name"] = "<b>Shiny</b>" + RLO
    first["wowhead"] = "https://www.wowhead.com@evil.example/item=1"
    panel["notes"] = ["javascript:alert(1)"]
    _twink_run(scenario)["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node", scenario)
    shown, opened = _twink_steps(out)
    assert shown["status"] == "<img src=x onerror=alert(1)>⟨U+202E⟩"
    assert shown["plannerHidden"] is True and shown["plannerHref"] is None
    assert shown["notes"][-1] == "javascript:alert(1)"
    assert shown["slots"][0]["candidates"][0]["name"] == "<b>Shiny</b>⟨U+202E⟩"
    assert opened["wowheadHidden"] is True and opened["wowheadHref"] is None
    assert not out["traps"]
    _only_api(out)


@pytest.mark.parametrize(
    "address",
    [
        "http://forevertwinking.com/planner?class=warlock",
        "https://u:p@forevertwinking.com/planner",
        "https://www.wowhead.com/forever/item=1",
        "javascript:alert(1)",
        "//forevertwinking.com/planner",
    ],
)
def test_the_planner_link_takes_only_its_origin_constructed(
    server: Any, both: Path, tables: Path, tmp_path: Path, address: str
) -> None:
    scenario = _scenario(server, [{"twink": "show"}])
    report = _report(scenario)
    report["twink"]["planner"] = address
    _twink_run(scenario)["stdout"] = json.dumps(report)
    [shown] = _twink_steps(_drive(tmp_path / "node", scenario))
    assert shown["plannerHidden"] is True and shown["plannerHref"] is None


def test_a_report_of_another_shape_or_a_failed_run_constructed(
    server: Any, both: Path, tables: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"twink": "show"}])
    for stdout in ('{"twink": {"slots": 5}}', '{"twink": null}', "[]"):
        _twink_run(scenario)["stdout"] = stdout
        [shown] = _twink_steps(_drive(tmp_path / "node", scenario))
        assert shown["slots"] == [] and shown["statusError"] is True
        assert shown["status"] == "wowlab char twink printed something this page does not know."
    _twink_run(scenario).update(exit_code=1, stdout="", stderr="the game tables could not be read")
    [shown] = _twink_steps(_drive(tmp_path / "node", scenario))
    assert shown["status"] == "the game tables could not be read" and shown["statusError"]
