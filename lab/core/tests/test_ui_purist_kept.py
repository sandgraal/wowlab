"""See my look on a character kept only in a backup (M21-06).

`char kept --json` carries the backup's gear report, `purist` included, and
the read-only page drawn from it shows See my look as the listed
characters' pages do. Driven under Node with `test_ui_kept.py`'s harness
and store (`constructed`, L8, a boundary input: the owner's two beta
characters of `fixtures/macos-70450/` snapshotted and then deleted from
the install), with Node's own `URL` given to the page as a browser gives
it. Nothing reads or writes a real install, and no request leaves the
page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import test_ui_account as hub_tab
import test_ui_home as home_tab
import test_ui_kept as kept_tab

both = kept_tab.both
home = kept_tab.home
server = kept_tab.server
no_env_flavor = kept_tab.no_env_flavor
kept = kept_tab.kept

_VIEW = r"""
ctx.URL = URL;
function puristView() {
  const link = ids["char-purist-link"];
  return {
    linkHidden: link.hidden,
    href: typeof link.href === "string" ? link.href : null,
    hrefAttr: link.getAttribute("href"),
    note: ids["char-purist-note"].textContent,
  };
}
"""

_STEPS = r"""
      } else if (step.purist !== undefined) {
        out.steps.push(puristView());
"""


def _harness() -> str:
    text = kept_tab._harness()
    view, step = hub_tab._VIEW_ANCHOR, hub_tab._ANCHOR
    assert text.count(view) == 1 and text.count(step) == 1
    text = text.replace(view, _VIEW + view, 1)
    return text.replace(step, _STEPS.lstrip("\n") + step, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = home_tab._harness
    home_tab._harness = _harness
    try:
        return home_tab._drive(folder, scenario)
    finally:
        home_tab._harness = original


def test_a_kept_page_shows_see_my_look_constructed(server: Any, kept: Path, tmp_path: Path) -> None:
    probe = _drive(tmp_path / "probe", kept_tab._scenario(server, [{"home": True}]))
    at = kept_tab._kept_at(probe, "Sangraal-Sett")
    scenario = kept_tab._scenario(server, [{"kept_open": at}, {"purist": True}])
    run = next(
        r
        for r in scenario["runs"]
        if r["command"] == kept_tab.KEPT and r["params"]["character"] == kept_tab.SETT
    )
    report = json.loads(run["stdout"])
    url = report["gear"]["purist"]["url"]
    assert url.startswith("https://purist.pages.dev/#2|Sangraal%20Sett|5.0.1.1.9.forever|")
    out = _drive(tmp_path / "node", scenario)
    assert out["steps"][0]["characterHidden"] is False
    view = out["steps"][1]
    assert view["linkHidden"] is False and view["href"] == url
    assert view["note"] == ""
