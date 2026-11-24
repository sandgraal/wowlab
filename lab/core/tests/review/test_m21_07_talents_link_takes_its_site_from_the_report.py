# Probe from review of m21/07-talent-link; reproduces Open my talents linking to whatever listed site the report names, not WoW Forever Builds only
"""`drawTalents` checks the report's address with
`outsideAddress(made.url, made.site)`: the site comes from the report, so a
report naming another site of `link_sites.toml` (here Wowhead) with that
site's address gets an **Open my talents** link to it. The branch's own
`docs/LAB_PLAN.md` amendment says the address is given to the link "only
when it passes the page's check for the `wowforeverbuilds` origin of
`link_sites.toml`", and every other outside link of the tab pins its site in
the page (the Twink planner `"forevertwinking"`, its item link `"wowhead"`,
the saved links their slot's site). The positive control is the same report
with the WoW Forever Builds address. Constructed report (L8, a hostile
input), on the real scenario `test_ui_talents.py` drives.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_dressing as dress
import test_ui_talents as tt

server = tt.server
home = tt.home
forever = dress.forever

ADDRESS = tt.ADDRESS
WOWHEAD = "https://www.wowhead.com/forever/item=6948"


def _view(server: Any, tmp_path: Path, url: str, site: str) -> dict[str, Any]:
    scenario = tt._with(server, [{"char_tab": True}, {"talents": True}], tt._link(url, site))
    return tt._views(tt._drive(tmp_path, scenario))[-1]


def test_positive_control_the_sites_own_address_is_linked(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    view = _view(server, tmp_path / "control", ADDRESS, "wowforeverbuilds")
    assert view["linkHidden"] is False and view["href"] == ADDRESS


def test_another_listed_sites_address_is_not_linked_as_open_my_talents(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    view = _view(server, tmp_path / "probe", WOWHEAD, "wowhead")
    assert view["linkHidden"] is True and view["hrefAttr"] is None, view
