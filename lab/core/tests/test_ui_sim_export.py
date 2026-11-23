"""The Character tab's Copy for WoWSims (M21-08).

Driven under Node with the harness of `test_ui_character_page.py` (that of
`test_ui_home.py` and the modules before it: HTML sinks throw, attributes
outside a closed set and navigation are trapped, requests outside the
server's API are recorded), fed what the real CLI printed through the real
server for the two captures side by side (`constructed`, L8, as that module
says): the warlock's card is opened, then the button is clicked. The
export's talents are left out there (no game table is cached in the
server's data folder), which the page lists like anything else left out.

Cases labelled `constructed` edit the real `char sim-export` report before
the page reads it: hostile text in the export and in what it leaves out,
a report of another shape, a failed run. The page must show each as text,
copy the export exactly, and make no request but its own `/api/run`.
Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import test_ui_account as hub_tab
import test_ui_character_page as page_tab
import test_ui_home as home_tab

from wowlab_core import ui

both = page_tab.both
home = page_tab.home
server = page_tab.server
PARAMS = page_tab.PARAMS
SIM = ["char", "sim-export"]
RLO = chr(0x202E)  # a right-to-left override, written by its number
_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"

_STEPS = r"""
      } else if (step.sim !== undefined) {
        const before = requests.length;
        if (step.sim === "click") { ids["char-sim-button"].fire("click"); await tick(); }
        if (step.sim === "copy") {
          const buttons = ids["char-sim-copy"].all().filter((e) => e.tagName === "button");
          if (!buttons.length) { out.steps.push({ missing: "copy" }); continue; }
          buttons[0].fire("click"); await tick();
        }
        out.steps.push(Object.assign(simView(), { clipboard: clipboard.slice(), newRequests: requests.slice(before) }));
"""

_VIEW = r"""
function simView() {
  const button = ids["char-sim-button"];
  return {
    buttonHidden: button.hidden,
    buttonText: button.textContent,
    expanded: button.getAttribute("aria-expanded"),
    boxHidden: ids["char-sim"].hidden,
    status: ids["char-sim-status"].textContent,
    textHidden: ids["char-sim-text"].hidden,
    text: ids["char-sim-text"].textContent,
    copies: ids["char-sim-copy"].all().filter((e) => e.tagName === "button").length,
    leftHidden: ids["char-sim-left"].hidden,
    left: ids["char-sim-left"].all().filter((e) => e.tagName === "li").map((e) => e.textContent),
    note: ids["char-sim-note"].textContent,
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


def _scenario(server: Any, steps: list[dict[str, Any]]) -> dict[str, Any]:
    scenario = page_tab._scenario(server, steps)
    scenario["runs"].append(hub_tab._run_of(server, SIM, PARAMS))
    return scenario


def _sim_run(scenario: dict[str, Any]) -> dict[str, Any]:
    [run] = [r for r in scenario["runs"] if r["command"] == SIM]
    return run


def _report(scenario: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(_sim_run(scenario)["stdout"])
    return report


def _only_api(out: dict[str, Any]) -> list[dict[str, Any]]:
    """Every request went to the server's own API; the run bodies."""
    bodies = []
    for request in out["requests"]:
        assert request["url"].startswith(("/api/commands", "/api/run")), request
        if request["body"] is not None:
            bodies.append(request["body"])
    return bodies


def _sim_steps(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in out["steps"] if "buttonHidden" in s]


# ─── the markup ──────────────────────────────────────────────────────────────


def test_the_button_is_static_markup_with_no_link() -> None:
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert (
        '<button id="char-sim-button" class="sim-button" type="button" aria-controls="char-sim" '
        'aria-expanded="false" hidden>Copy for WoWSims</button>'
    ) in page
    # No address of WoWSims anywhere in the page or its script.
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert "wowsims.com" not in page.lower() and "wowsims.com" not in script.lower()
    assert len(re.findall(r"<a\b", page)) == 1  # the dressing-room link only


def test_the_csp_is_unchanged_by_the_export(server: Any) -> None:
    csp = page_tab.first_stage._page(server).headers["Content-Security-Policy"]
    assert "wowsims" not in csp.lower() and "https:" not in csp
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp


# ─── the export ──────────────────────────────────────────────────────────────


def test_a_click_runs_sim_export_and_shows_the_text_to_copy(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"sim": "look"}, {"sim": "click"}, {"sim": "copy"}])
    report = _report(scenario)
    assert _sim_run(scenario)["exit_code"] == 0
    sim = report["sim"]
    assert json.loads(sim["text"])["class"] == "warlock"
    out = _drive(tmp_path / "node", scenario)
    shown, clicked, copied = _sim_steps(out)
    # Before the click: the button only, and no run of sim-export.
    assert shown["buttonHidden"] is False and shown["boxHidden"] is True
    # The stand-in DOM holds page.html's elements without their static text
    # or attributes (the markup test reads those); only what the script set.
    assert shown["expanded"] != "true"
    bodies = _only_api(out)
    assert [b for b in bodies if b["command"] == SIM] == [{"command": SIM, "params": PARAMS}]
    assert clicked["newRequests"] and all(r["url"] == "/api/run" for r in clicked["newRequests"])
    assert clicked["boxHidden"] is False and clicked["expanded"] == "true"
    assert clicked["status"] == (
        "Paste this into WoWSims' Addon Import, on the sim page for this character's class."
    )
    assert clicked["textHidden"] is False and clicked["text"] == sim["text"]
    assert clicked["copies"] == 1
    assert clicked["left"] == [f"Left out: {x['what']}: {x['why']}" for x in sim["left_out"]]
    assert clicked["leftHidden"] is (not sim["left_out"])
    assert "wowlab sends nothing to WoWSims" in clicked["note"]
    # Copy puts the export on the clipboard exactly, and asks for nothing.
    assert copied["clipboard"] == [sim["text"]]
    assert copied["newRequests"] == []


def test_a_hostile_export_is_text_and_copied_exactly_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"sim": "click"}, {"sim": "copy"}])
    report = _report(scenario)
    hostile = '{"class":"<img src=x onerror=alert(1)>","x":"' + RLO + '</pre><script>"}'
    report["sim"]["text"] = hostile
    report["sim"]["left_out"] = [
        {"what": "<b>race</b>", "why": "javascript:alert(1)"},
        {"what": 5, "why": "not a string"},
        "not an object",
    ]
    _sim_run(scenario)["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node", scenario)
    clicked, copied = _sim_steps(out)
    assert clicked["text"] == hostile.replace(RLO, "⟨U+202E⟩")
    assert clicked["left"] == ["Left out: <b>race</b>: javascript:alert(1)"]
    assert copied["clipboard"] == [hostile]
    assert not out["traps"]
    _only_api(out)


def test_a_report_of_another_shape_shows_no_text_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"sim": "click"}])
    for stdout in ('{"sim": {"text": 5}}', '{"sim": null}', "[]", '{"sim": {"text": ""}}'):
        _sim_run(scenario)["stdout"] = stdout
        [clicked] = _sim_steps(_drive(tmp_path / "node", scenario))
        assert clicked["textHidden"] is True and clicked["copies"] == 0
        assert (
            clicked["status"] == "wowlab char sim-export printed something this page does not know."
        )


def test_a_failed_run_shows_the_clis_words_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"sim": "click"}])
    run = _sim_run(scenario)
    run.update(exit_code=1, stdout="", stderr="no export: the sim needs the class")
    [clicked] = _sim_steps(_drive(tmp_path / "node", scenario))
    assert clicked["status"] == "no export: the sim needs the class"
    assert clicked["textHidden"] is True and clicked["copies"] == 0


def test_the_page_never_passes_out() -> None:
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert script.count('runJson(["char", "sim-export"], params)') == 1
    assert re.search(r"sim-export[^\n]*\bout\b", script) is None
