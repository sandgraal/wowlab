"""The Character tab's See my look link (M21-06): the character on Purist.

Driven under Node on the harness of `test_ui_character.py` (the stand-in
DOM of `test_ui_explorer.py`: HTML sinks throw, navigation and requests
outside the server's API are trapped), with Node's own `URL` given to the
page as a browser gives it. The page is fed what the real CLI printed
through the real server for the M15-01 capture (`macos-70450/`, two
schema-4 characters, as `test_ui_dressing.py` sets it up): Sangraal-Sett
has a barber-shop record of its own race and body type, Dangling-Participle
none.

Cases labelled `constructed` replace the report's address with a hostile
one (another origin, a lookalike host, a userinfo form, `javascript:`, a
form the URL parser would rewrite): each must leave the link without an
href and hidden. Nothing reads or writes a real install, and no request
leaves the page.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import test_ui_character as character_tab
import test_ui_dressing as dressing_tab
import test_ui_explorer as first_stage
from test_ui_character import _STEP_ANCHOR, _VIEW_ANCHOR, _report, _scenario, _set_report
from test_ui_explorer import _node, _page

from wowlab_core import links, ui

home = first_stage.home
install = first_stage.install
server = first_stage.server
forever = dressing_tab.forever

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
DANGLING = dressing_tab.DANGLING
SANGRAAL = dressing_tab.SANGRAAL

_VIEW = r"""
ctx.URL = URL;
function puristView() {
  const link = ids["char-purist-link"];
  return {
    linkHidden: link.hidden,
    href: typeof link.href === "string" ? link.href : null,
    hrefAttr: link.getAttribute("href"),
    linkTitle: link.title,
    linkText: link.textContent,
    note: ids["char-purist-note"].textContent,
  };
}
"""

_STEPS = r"""
      } else if (step.purist !== undefined) {
        out.steps.push(Object.assign(puristView(), { requestsSoFar: requests.slice() }));
"""


def _harness() -> str:
    text = character_tab._harness()
    assert text.count(_STEP_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_STEP_ANCHOR, _STEPS.lstrip("\n") + _STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m21-06-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m21-06-scenario.json"
    data.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run(
        [_node(), str(harness), str(_TEMPLATES / "page.js"), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert "error" not in out, out["error"]
    assert not out["traps"], out["traps"]
    return out


def _only_api(out: dict[str, Any]) -> None:
    for step in out["steps"]:
        for request in step.get("requestsSoFar", []):
            assert request["url"].startswith(("/api/commands", "/api/run")), request


# ─── the markup ──────────────────────────────────────────────────────────────


def test_the_link_is_static_markup_beside_the_dressing_room() -> None:
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    dress = page.index('<a id="char-dressing-link"')
    look = page.index(
        '<a id="char-purist-link" class="dressing-link" target="_blank" '
        'rel="noopener noreferrer" hidden>See my look</a>'
    )
    sim = page.index('<button id="char-sim-button"')
    assert dress < look < sim
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert script.count('const address = outsideAddress(purist.url, "purist");') == 1
    assert script.count("pointLink(look, address);") == 1
    assert script.index('outsideAddress(purist.url, "purist")') < script.index(
        "pointLink(look, address);"
    )
    assert "purist.pages.dev" not in page and "purist.pages.dev" not in script


def test_the_csp_is_unchanged_and_purist_is_served_on_the_allowlist(server: Any) -> None:
    response = _page(server)
    csp = response.headers["Content-Security-Policy"]
    assert "purist" not in csp and "https:" not in csp
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp
    served = first_stage._send(server, "GET", "/api/commands").json()["link_origins"]
    assert served["purist"] == links.origins()["purist"] == "https://purist.pages.dev"


# ─── the link ────────────────────────────────────────────────────────────────


def test_the_links_href_is_the_reports_url(server: Any, forever: Path, tmp_path: Path) -> None:
    steps = [{"char_tab": True}, {"pick": 0}, {"purist": True}]
    scenario = _scenario(server, steps, [SANGRAAL, DANGLING])
    report = _report(scenario, SANGRAAL)
    url = report["purist"]["url"]
    assert url.startswith("https://purist.pages.dev/#2|Sangraal%20Sett|5.0.1.1.9.forever|0.0.0|")
    assert report["purist"]["left_out"] == []
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][-1]
    assert view["linkHidden"] is False and view["href"] == url == view["hrefAttr"]
    assert view["linkText"] == "See my look"
    assert view["linkTitle"] == "Opens Purist in a new tab; wowlab sends nothing"
    assert view["note"] == ""
    _only_api(out)


def test_what_the_look_leaves_out_is_said(server: Any, forever: Path, tmp_path: Path) -> None:
    """Dangling-Participle's record holds no barber-shop visit: the link is
    drawn with an empty customization part, and the note says so."""
    steps = [{"char_tab": True}, {"pick": 1}, {"purist": True}]
    scenario = _scenario(server, steps, [SANGRAAL, DANGLING])
    report = _report(scenario, DANGLING)
    assert re.fullmatch(
        r"https://purist\.pages\.dev/#2\|Dangling%20Participle\|2\.0\.1\.1\.8\.forever"
        r"\|0\.0\.0\|\|[0-9,]+",
        report["purist"]["url"],
    )
    view = _drive(tmp_path / "node", scenario)["steps"][-1]
    assert view["linkHidden"] is False and view["href"] == report["purist"]["url"]
    assert view["note"] == (
        "See my look leaves out the face and hair: the record holds no barber-shop visit."
    )


def test_no_link_says_why(server: Any, forever: Path, tmp_path: Path) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"purist": True}], [SANGRAAL])
    report = _report(scenario, SANGRAAL)
    report["purist"] = {
        "link": "purist_look",
        "site": "purist",
        "site_name": "Purist",
        "label": "Purist",
        "why": "the record does not name the character's race, body type and class",
    }
    _set_report(scenario, SANGRAAL, report)
    view = _drive(tmp_path / "node", scenario)["steps"][-1]
    assert view["linkHidden"] is True and view["hrefAttr"] is None
    assert view["note"] == (
        "No See my look link: the record does not name the character's race, body type and class."
    )


_HOSTILE = [
    "https://www.example.test/#2|x",
    "https://purist.pages.dev.evil.example/#2|x",
    "https://purist.pages.dev@evil.example/#2|x",
    "https://u:p@purist.pages.dev/#2|x",
    "http://purist.pages.dev/#2|x",
    "javascript:alert(document.domain)",
    "https://purist.pages.dev/#2|A b|1",  # the parser writes the space as %20
    "HTTPS://PURIST.PAGES.DEV/#2|x",
    "https://purist.pages.dev:443/#2|x",
    "https://www.wowhead.com/forever/dressing-room#x",  # an allowed origin, another site's
    "",
]


@pytest.mark.parametrize("url", _HOSTILE, ids=lambda u: f"constructed: {u!r}")
def test_a_hostile_address_is_left_unrendered_constructed(
    server: Any, forever: Path, tmp_path: Path, url: str
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"purist": True}], [SANGRAAL])
    report = _report(scenario, SANGRAAL)
    report["purist"]["url"] = url
    _set_report(scenario, SANGRAAL, report)
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][-1]
    assert view["href"] is None and view["hrefAttr"] is None and view["linkHidden"] is True
    assert view["note"] == (
        "The look's address in the report is not one this page links to, so no link is shown."
    )
    _only_api(out)


@pytest.mark.parametrize(
    "value",
    [None, "https://purist.pages.dev/#2", {"url": 5}, {"why": 5}, [], {"left_out": ["x"]}],
    ids=lambda v: f"constructed: {v!r}",
)
def test_a_malformed_value_draws_no_link_constructed(
    server: Any, forever: Path, tmp_path: Path, value: Any
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"purist": True}], [SANGRAAL])
    report = _report(scenario, SANGRAAL)
    report["purist"] = value
    _set_report(scenario, SANGRAAL, report)
    view = _drive(tmp_path / "node", scenario)["steps"][-1]
    assert view["hrefAttr"] is None and view["linkHidden"] is True


def test_a_later_pick_clears_the_link(server: Any, forever: Path, tmp_path: Path) -> None:
    steps = [{"char_tab": True}, {"purist": True}, {"pick": 1}, {"purist": True}]
    scenario = _scenario(server, steps, [SANGRAAL, DANGLING])
    report = _report(scenario, DANGLING)
    report["purist"] = None
    _set_report(scenario, DANGLING, report)
    out = _drive(tmp_path / "node", scenario)
    first, last = out["steps"][1], out["steps"][3]
    assert first["linkHidden"] is False and first["hrefAttr"] is not None
    assert last["linkHidden"] is True and last["hrefAttr"] is None and last["note"] == ""
