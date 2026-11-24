"""The Character tab's "Open my talents" (M21-07).

Driven under Node on the Character tab's harness of `test_ui_dressing.py`
(the stand-in DOM of `test_ui_explorer.py`: HTML sinks throw, `<a>` cannot
be created, navigation and requests outside the server's API are trapped),
with Node's own `URL` given to the page as a browser gives it. The page is
fed what the real CLI printed through the real server for the M15-01
capture (`macos-70450/`, the warlock Sangraal-Sett): no game table is
cached in the server's data folder, so the real report says the talent
tables are not downloaded and names them, which the page offers to
download. Cases labelled `constructed` replace the report's `talents` with
a link (the `link_sites.toml` example's address), a hostile address, or a
hostile download list. No request leaves the page, and nothing reads or
writes a real install.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import test_ui_dressing as dress
import test_ui_explorer as first_stage
from test_ui_character import _STEP_ANCHOR, _VIEW_ANCHOR, _report, _scenario, _set_report
from test_ui_explorer import _node

from wowlab_core import talentlink, ui

home = first_stage.home
server = first_stage.server
forever = dress.forever

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
ADDRESS = "https://wowforeverbuilds.com/talents/warlock?b=022&race=undead"  # [verify] M21-O2
GEAR = ["char", "gear"]
FETCH = ["db2", "fetch"]

_TALENTS_VIEW = r"""
function talentsView() {
  const link = ids["char-talents-link"];
  const button = ids["char-talents-fetch"];
  return {
    talentsView: true,
    hidden: ids["char-talents"].hidden,
    linkHidden: link.hidden,
    href: typeof link.href === "string" ? link.href : null,
    hrefAttr: link.getAttribute("href"),
    linkText: link.textContent,
    linkTitle: link.title,
    why: ids["char-talents-why"].textContent,
    buttonHidden: button.hidden,
    buttonDisabled: button.disabled,
  };
}
"""

_TALENTS_STEPS = r"""
      } else if (step.talents !== undefined) {
        out.steps.push(Object.assign(talentsView(), { requestsSoFar: requests.slice() }));
      } else if (step.talents_fetch !== undefined) {
        const before = requests.length;
        ids["char-talents-fetch"].fire("click"); await tick(); await tick();
        out.steps.push(Object.assign(talentsView(), { newRequests: requests.slice(before) }));
"""


def _harness() -> str:
    text = dress._harness()
    assert text.count(_STEP_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _TALENTS_VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_STEP_ANCHOR, _TALENTS_STEPS.lstrip("\n") + _STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m21-07-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m21-07-scenario.json"
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


def _views(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in out["steps"] if s.get("talentsView")]


def _only_api(out: dict[str, Any]) -> None:
    for step in out["steps"]:
        for request in step.get("requestsSoFar", []) + step.get("newRequests", []):
            assert request["url"].startswith(("/api/commands", "/api/run")), request


def _with(server: Any, steps: list[dict[str, Any]], talents: dict[str, Any] | None) -> Any:
    scenario = _scenario(server, steps, [dress.SANGRAAL, dress.DANGLING])
    if talents is not None:
        report = _report(scenario, dress.SANGRAAL)
        report["talents"] = talents
        _set_report(scenario, dress.SANGRAAL, report)
    return scenario


def _link(url: str, site: str = "wowforeverbuilds") -> dict[str, Any]:
    return {
        "link": {
            "link": "talents",
            "site": site,
            "site_name": "WoW Forever Builds",
            "label": "Open my talents",
            "url": url,
        },
        "ranks": "022",
        "tables_build": "1.60.1.70450",
        "record_build": "1.60.1.70450",
        "table_note": None,
        "download": [],
        "download_build": None,
    }


# ─── the markup ──────────────────────────────────────────────────────────────


def test_the_link_is_static_markup_with_no_href() -> None:
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert (
        '<a id="char-talents-link" class="dressing-link" target="_blank" '
        'rel="noopener noreferrer" hidden>Open my talents</a>'
    ) in page
    assert '<button id="char-talents-fetch" class="link-button" type="button" hidden>' in page
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert "wowforeverbuilds.com" not in script.lower()
    # The page's own site key and table list: the link is checked against
    # WoW Forever Builds' origin whatever site the report names, and only the
    # tables `char gear` can name are downloaded.
    assert script.count('const TALENTS_SITE = "wowforeverbuilds";') == 1
    tables = ", ".join(f'"{t}"' for t in talentlink.TABLES)
    assert script.count(f"const TALENT_TABLES = [{tables}];") == 1


def test_the_csp_is_unchanged(server: Any) -> None:
    csp = first_stage._page(server).headers["Content-Security-Policy"]
    assert "wowforeverbuilds" not in csp and "https:" not in csp
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp


# ─── the tab ─────────────────────────────────────────────────────────────────


def test_without_the_tables_the_reason_and_the_download_are_shown(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario = _with(server, [{"char_tab": True}, {"talents": True}], None)
    talents = _report(scenario, dress.SANGRAAL)["talents"]
    assert talents["download"] == list(talentlink.TABLES)
    assert talents["download_build"] == "1.60.1.70450"
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    assert view["hidden"] is False and view["linkHidden"] is True and view["hrefAttr"] is None
    assert view["why"] == (
        "No talents link: the game's talent tables for 1.60.1.70450 are not downloaded."
    )
    assert view["buttonHidden"] is False and view["buttonDisabled"] is False


def test_the_download_runs_db2_fetch_for_each_table_then_char_gear(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario = _with(server, [{"char_tab": True}, {"talents_fetch": True}], None)
    build = "1.60.1.70450"
    for table in talentlink.TABLES:
        scenario["runs"].append(
            {"command": FETCH, "params": {"table": table, "build": build}, "stdout": "{}"}
        )
    out = _drive(tmp_path / "node", scenario)
    view = _views(out)[-1]
    runs = [r["body"] for r in view["newRequests"] if r["body"] is not None]
    assert [r["command"] for r in runs] == [FETCH, FETCH, FETCH, GEAR]
    assert [r["params"] for r in runs[:3]] == [
        {"table": t, "build": build} for t in talentlink.TABLES
    ]
    assert runs[3]["params"] == {
        "character": dress.SANGRAAL,
        "account": dress.ACCOUNT,
        "flavor": first_stage.FLAVOR,
    }
    # The stand-in server answers `char gear` as before, so the reason and
    # the button are drawn again.
    assert view["buttonHidden"] is False and view["why"].startswith("No talents link:")
    _only_api(out)


def test_a_failed_download_says_so_and_offers_it_again(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario = _with(server, [{"char_tab": True}, {"talents_fetch": True}], None)
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    runs = [r["body"] for r in view["newRequests"] if r["body"] is not None]
    assert [r["command"] for r in runs] == [FETCH]  # stops at the first failure
    assert view["why"].startswith("The talent tables could not all be downloaded: ")
    assert view["buttonHidden"] is False and view["buttonDisabled"] is False


def test_the_links_href_is_the_reports_address_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario = _with(server, [{"char_tab": True}, {"talents": True}], _link(ADDRESS))
    out = _drive(tmp_path / "node", scenario)
    view = _views(out)[-1]
    assert view["hidden"] is False and view["linkHidden"] is False
    assert view["href"] == ADDRESS
    assert view["linkText"] == "Open my talents"
    assert view["linkTitle"] == (
        "Opens WoW Forever Builds in a new tab: the talents of the spec active when the "
        "character last saved; wowlab sends nothing"
    )
    assert view["why"] == "" and view["buttonHidden"] is True
    _only_api(out)


_HOSTILE = [
    ("https://evil.example/talents/warlock?b=022", "wowforeverbuilds"),
    ("https://wowforeverbuilds.com.evil.example/talents/warlock", "wowforeverbuilds"),
    ("https://u:p@wowforeverbuilds.com/talents/warlock?b=022", "wowforeverbuilds"),
    ("http://wowforeverbuilds.com/talents/warlock?b=022", "wowforeverbuilds"),
    ("HTTPS://WOWFOREVERBUILDS.COM/talents/warlock?b=022", "wowforeverbuilds"),
    ("https://wowforeverbuilds.com/talents/war lock?b=022", "wowforeverbuilds"),
    ("javascript:alert(1)", "wowforeverbuilds"),
    # Another listed site's own address: the page checks against WoW Forever
    # Builds only, whatever site the report names.
    ("https://www.wowhead.com/forever/item=6948", "wowhead"),
    ("https://www.wowhead.com/forever/item=6948", "wowforeverbuilds"),
]


def test_a_hostile_address_is_left_unrendered_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    """Each address of `_HOSTILE` (constructed), on one real scenario: the
    CLI's runs are read once, the page is driven once per address."""
    base = _with(server, [{"char_tab": True}, {"talents": True}], None)
    for n, (url, site) in enumerate(_HOSTILE):
        scenario = copy.deepcopy(base)
        report = _report(scenario, dress.SANGRAAL)
        report["talents"] = _link(url, site)
        _set_report(scenario, dress.SANGRAAL, report)
        view = _views(_drive(tmp_path / f"node-{n}", scenario))[-1]
        assert view["linkHidden"] is True and view["hrefAttr"] is None, url
        assert view["why"] == (
            "The talents address in the report is not one this page links to, so no link is shown."
        ), url


_HOSTILE_DOWNLOADS: list[tuple[Any, Any]] = [
    # Any other game table, well formed or not: only the three are offered.
    (["ItemSparse"], "1.60.1.70450"),
    (["TraitNode", "ItemSparse"], "1.60.1.70450"),
    (["Trait Node"], "1.60.1.70450"),
    (["../x"], "1.60.1.70450"),
    (["TraitNode"], "1.60.1.70450 --yes"),
    (["TraitNode"], None),
    ("TraitNode", "1.60.1.70450"),
]


def test_a_hostile_download_is_not_offered_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    base = _with(server, [{"char_tab": True}, {"talents": True}], None)
    for n, (tables, build) in enumerate(_HOSTILE_DOWNLOADS):
        talents = _link(ADDRESS)
        talents["link"] = {
            "link": "talents",
            "site": "wowforeverbuilds",
            "site_name": "WoW Forever Builds",
            "label": "Open my talents",
            "why": "constructed <b>why</b>",
        }
        talents["download"] = tables
        talents["download_build"] = build
        scenario = copy.deepcopy(base)
        report = _report(scenario, dress.SANGRAAL)
        report["talents"] = talents
        _set_report(scenario, dress.SANGRAAL, report)
        view = _views(_drive(tmp_path / f"node-{n}", scenario))[-1]
        assert view["buttonHidden"] is True, (tables, build)
        assert view["why"] == "No talents link: constructed <b>why</b>."


def test_a_later_pick_clears_the_link_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    steps = [{"char_tab": True}, {"talents": True}, {"pick": 1}, {"talents": True}]
    scenario = _with(server, steps, _link(ADDRESS))
    first, second = _views(_drive(tmp_path / "node", scenario))
    assert first["href"] == ADDRESS
    assert second["hrefAttr"] is None and second["linkHidden"] is True
    assert re.match(r"No talents link: ", second["why"])
