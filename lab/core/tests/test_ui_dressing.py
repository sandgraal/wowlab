"""The Character tab's dressing-room link (M15-02, §16.3's amendment of 2026-10-19).

Driven under Node on the harness of `test_ui_character.py` (the stand-in
DOM of `test_ui_explorer.py`: HTML sinks throw, navigation and requests
outside the server's API are trapped), with Node's own `URL` given to the
page as a browser gives it. The page is fed what the real CLI printed
through the real server for the M15-01 capture (`macos-70450/`, two
schema-4 characters, files-only, inside the install root borrowed from
`macos/` as its fixture row says) and, for the fallback, for the M14-08
capture (`macos/`, schema 1, no identity).

Cases labelled `constructed` replace the report's address with a hostile
one: another origin, a lookalike host, a userinfo form, `javascript:` and
`data:` addresses, and forms the URL parser would rewrite. Each must leave
the link without an href and hidden. Nothing reads or writes a real install,
and no request leaves the page.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import test_ui_character as character_tab
import test_ui_explorer as first_stage
from test_ui_character import (
    _STEP_ANCHOR,
    _VIEW_ANCHOR,
    _report,
    _scenario,
    _set_report,
)
from test_ui_explorer import FLAVOR, _node, _page

from wowlab_core import dressing, ui

home = first_stage.home
install = first_stage.install
server = first_stage.server
timed = character_tab.timed

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACCOUNT = "90000001#6"
DANGLING = "1/Dangling-Participle"
SANGRAAL = "1/Sangraal-Sett"
DERIVED = json.loads(
    (FIXTURES / "macos-70450" / "derived" / "dressing-room.json").read_text(encoding="utf-8")
)
ADDRESSES = {"1/" + e["record"].split("/")[-3]: e["address"] for e in DERIVED["examples"]}

_DRESS_VIEW = r"""
ctx.URL = URL;
function dressingView() {
  const link = ids["char-dressing-link"];
  const items = ids["char-dressing-items"];
  return {
    boxHidden: ids["char-dressing"].hidden,
    linkHidden: link.hidden,
    href: typeof link.href === "string" ? link.href : null,
    hrefAttr: link.getAttribute("href"),
    linkTitle: link.title,
    note: ids["char-dressing-note"].textContent,
    itemsHidden: items.hidden,
    itemsText: items.textContent,
    ids: items.all().filter((e) => e.tagName === "code").map((e) => e.textContent),
    copies: items.all().filter((e) => e.tagName === "button").length,
  };
}
"""

_DRESS_STEPS = r"""
      } else if (step.dressing !== undefined) {
        out.steps.push(Object.assign(dressingView(), { requestsSoFar: requests.slice() }));
      } else if (step.copy_ids !== undefined) {
        const buttons = ids["char-dressing-items"].all().filter((e) => e.tagName === "button");
        if (!buttons.length) { out.steps.push({ missing: "copy ids" }); continue; }
        const before = requests.length;
        buttons[0].fire("click"); await tick();
        out.steps.push({ clipboard: clipboard.slice(), newRequests: requests.slice(before) });
"""


def _harness() -> str:
    text = character_tab._harness()
    assert text.count(_STEP_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _DRESS_VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_STEP_ANCHOR, _DRESS_STEPS.lstrip("\n") + _STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m15-02-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m15-02-scenario.json"
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


def _lab_file(root: Path, character: str) -> Path:
    return root / FLAVOR / "WTF" / "Account" / ACCOUNT / character / "SavedVariables" / "WowLab.lua"


@pytest.fixture
def forever(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The M15-01 capture inside the install root borrowed from `macos/`,
    Sangraal-Sett written last so the tab reads it first."""
    root = home / "Games" / "World of Warcraft"
    root.mkdir(parents=True)
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70450" / "forever", root / FLAVOR)
    shutil.copy2(FIXTURES / "macos" / "forever" / ".flavor.info", root / FLAVOR / ".flavor.info")
    for character, ns in (
        (SANGRAAL, 1_760_000_000_000_000_000),
        (DANGLING, 1_750_000_000_000_000_000),
    ):
        os.utime(_lab_file(root, character), ns=(ns, ns))
    monkeypatch.setenv("WOWLAB_WOW_ROOT", str(root))
    return root


def _only_api(out: dict[str, Any]) -> None:
    """Every request the page made went to the server's own API, by path."""
    for step in out["steps"]:
        for request in step.get("requestsSoFar", []) + step.get("newRequests", []):
            assert request["url"].startswith(("/api/commands?", "/api/run?")), request


# ─── the markup ──────────────────────────────────────────────────────────────


def test_the_link_is_static_markup_with_no_href(server: Any) -> None:
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    anchors = re.findall(r"<a\b[^>]*>", page)
    assert anchors == [
        '<a id="char-dressing-link" class="dressing-link" target="_blank" '
        'rel="noopener noreferrer" hidden>'
    ]
    body = _page(server).body.decode("utf-8")
    assert body.count('target="_blank" rel="noopener noreferrer"') == 1
    assert "See it dressed</a>" in body


def test_the_csp_is_unchanged(server: Any) -> None:
    csp = _page(server).headers["Content-Security-Policy"]
    assert "wowhead" not in csp and "https:" not in csp
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp


# ─── the link ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("character", [SANGRAAL, DANGLING])
def test_the_links_href_is_the_reports_url(
    server: Any, forever: Path, tmp_path: Path, character: str
) -> None:
    pick = 0 if character == SANGRAAL else 1
    steps = [{"char_tab": True}, {"pick": pick}, {"dressing": True}]
    scenario = _scenario(server, steps, [SANGRAAL, DANGLING])
    report = _report(scenario, character)
    assert report["dressing"]["url"] == ADDRESSES[character]
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][-1]
    assert view["boxHidden"] is False and view["linkHidden"] is False
    assert view["href"] == report["dressing"]["url"]
    assert view["linkTitle"] == "Opens Wowhead's dressing room in a new tab"
    assert view["note"] == report["dressing"]["note"]
    assert view["itemsHidden"] is True and view["copies"] == 0
    _only_api(out)


_HOSTILE = [
    "https://www.example.test/",
    "https://www.wowhead.com.evil.example/",
    "https://www.wowhead.com@evil.example/",
    "https://u:p@www.wowhead.com/forever/dressing-room#x",
    "javascript:alert(document.domain)",
    "data:text/html,<script>alert(1)</script>",
    "http://www.wowhead.com/forever/dressing-room#x",
    "HTTPS://WWW.WOWHEAD.COM/forever/dressing-room#x",
    "https://www.wowhead.com:443/forever/dressing-room#x",
    "https://www.wowhead.com/forever/dress\ning-room#x",
    " https://www.wowhead.com/forever/dressing-room#x",
    "//www.wowhead.com/forever/dressing-room#x",
    "/forever/dressing-room#x",
    "",
]


@pytest.mark.parametrize("url", _HOSTILE, ids=lambda u: f"constructed: {u!r}")
def test_a_hostile_address_is_left_unrendered_constructed(
    server: Any, forever: Path, tmp_path: Path, url: str
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"dressing": True}], [SANGRAAL])
    report = _report(scenario, SANGRAAL)
    report["dressing"]["url"] = url
    _set_report(scenario, SANGRAAL, report)
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][-1]
    assert view["href"] is None and view["hrefAttr"] is None
    assert view["linkHidden"] is True
    assert view["note"] == (
        "The dressing-room address in the report is not one this page links to, so no link "
        "is shown."
    )
    _only_api(out)


@pytest.mark.parametrize(
    "dressing_value",
    [None, "https://www.wowhead.com/", {"url": 5, "note": "x"}, {"note": "x"}, []],
    ids=lambda v: f"constructed: {v!r}",
)
def test_a_malformed_dressing_draws_no_link_constructed(
    server: Any, forever: Path, tmp_path: Path, dressing_value: Any
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"dressing": True}], [SANGRAAL])
    report = _report(scenario, SANGRAAL)
    report["dressing"] = dressing_value
    _set_report(scenario, SANGRAAL, report)
    view = _drive(tmp_path / "node", scenario)["steps"][-1]
    assert view["href"] is None and view["linkHidden"] is True and view["copies"] == 0


# ─── the fallback ────────────────────────────────────────────────────────────


def test_the_fallback_lists_the_item_ids_with_a_copy_button(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    """The M14-08 capture is schema 1: no identity, so no link."""
    newer = character_tab.NEWER
    steps = [{"char_tab": True}, {"dressing": True}, {"copy_ids": True}]
    scenario = _scenario(server, steps, [newer])
    report = _report(scenario, newer)
    assert set(report["dressing"]) == {"items", "note"}
    out = _drive(tmp_path / "node", scenario)
    view, copied = out["steps"][1], out["steps"][2]
    assert view["boxHidden"] is False and view["linkHidden"] is True and view["href"] is None
    assert view["note"] == report["dressing"]["note"]
    assert view["itemsHidden"] is False and view["copies"] == 1
    assert view["ids"] == [" ".join(report["dressing"]["items"])]
    assert copied["clipboard"] == [" ".join(report["dressing"]["items"])]
    assert copied["newRequests"] == []
    _only_api(out)


def test_a_later_pick_clears_the_link(server: Any, forever: Path, tmp_path: Path) -> None:
    """A pick whose gear cannot be read leaves no link from the one before."""
    steps = [{"char_tab": True}, {"dressing": True}, {"pick": 1}, {"dressing": True}]
    scenario = _scenario(server, steps, [SANGRAAL, DANGLING])
    report = _report(scenario, DANGLING)
    report["dressing"] = {"items": ["1", "<b>2</b>"], "note": "constructed"}
    _set_report(scenario, DANGLING, report)
    out = _drive(tmp_path / "node", scenario)
    first, last = out["steps"][1], out["steps"][3]
    assert first["href"] == ADDRESSES[SANGRAAL] and first["linkHidden"] is False
    assert last["linkHidden"] is True and last["hrefAttr"] is None
    assert last["ids"] == ["1 <b>2</b>"]


def test_the_origin_constant_is_the_shipped_sites() -> None:
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert script.count("://") == 1
    assert f'const DRESSING_ORIGIN = "{dressing.load_sites().site.origin}";' in script
    assert script.count(".href") == 1 and "link.href = checked;" in script
