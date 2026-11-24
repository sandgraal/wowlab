"""The Character tab's profile, guild and gear links (M21-04).

Driven under Node on the Character tab's harness of `test_ui_dressing.py`
(the stand-in DOM of `test_ui_explorer.py`: HTML sinks throw, `<a>` cannot
be created, navigation and requests outside the server's API are trapped),
with Node's own `URL` given to the page as a browser gives it, fed what the
real CLI printed through the real server for the M15-01 capture
(`macos-70450/`, whose flavor folder holds no Config.wtf, so the region is
not known: the row shows no profile link and says why, as the CLI wrote it).

Cases labelled `constructed` give `char gear`'s report the links of a
character whose region is known (the addresses `link_sites.toml` records
for the owner's character Sandgraal Rizzen and its guild), a saved
ForeverTwinking page (the owner's recorded example), or hostile addresses:
an origin outside the list, another site's origin, `http:`, a user name, a
form the URL parser would rewrite, `javascript:`. Each hostile address is
left with no href and hidden, and the row says the link is not drawn.
Nothing reads or writes a real install, and no request leaves the page.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import test_ui_character as character_tab
import test_ui_dressing as dress
import test_ui_explorer as first_stage
from test_ui_character import _STEP_ANCHOR, _VIEW_ANCHOR, _report, _scenario, _set_report
from test_ui_explorer import FLAVOR, _node

from wowlab_core import ui

home = first_stage.home
server = first_stage.server
forever = dress.forever

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
ARMORY = "https://worldofwarcraft.blizzard.com/en-us/forever/us/armory/character/sandgraal-rizzen"
JOURNEY = (
    "https://wofwforever.com/en/tools/one-shot-manager/?region=us&name=Sandgraal&surname=Rizzen"
)
RAIDERIO = "https://forever.raider.io/characters/us/pvp-rp/Sandgraal%20Rizzen"
GUILD = "https://worldofwarcraft.blizzard.com/en-us/forever/us/armory/guild/booty-bay-cartel"
FT = "https://forevertwinking.com/armory/13-sandgraal-rizzen"
NO_REGION = "the character's region is not known"
RLO = chr(0x202E)  # a right-to-left override, drawn as a visible mark

_LINKS_VIEW = r"""
ctx.URL = URL;
function anchorView(a) {
  return { hidden: a.hidden, href: typeof a.href === "string" ? a.href : null, text: a.textContent,
    target: a.tagName === "a" ? "static" : null };
}
function linksView() {
  const gear = ids["char-gear"];
  const facts = (box) => box.all().filter((e) => e.tagName === "dt" || e.tagName === "dd").map((e) => e._text);
  return {
    linksView: true,
    rowHidden: ids["char-profiles"].hidden,
    armory: anchorView(ids["char-profile-armory"]),
    ft: anchorView(ids["char-profile-ft"]),
    journey: anchorView(ids["char-profile-journey"]),
    raiderio: anchorView(ids["char-profile-raiderio"]),
    why: ids["char-profiles-why"].textContent,
    guildHidden: ids["char-guild"].hidden,
    guild: anchorView(ids["char-guild-link"]),
    guildText: ids["char-guild-text"].textContent,
    items: gear.all().filter((e) => e.tagName === "li" && has(e, "gear-row") && !has(e, "gear-row-empty")).map((li) => {
      const name = li.all().find((e) => has(e, "gear-name"));
      const more = li.all().find((e) => has(e, "gear-more"));
      const detail = li.all().find((e) => has(e, "gear-detail"));
      return { tag: name.tagName, id: name.id, text: name.textContent, className: name.className, title: name.title,
        hidden: name.hidden, href: typeof name.href === "string" ? name.href : null,
        more: more ? { tag: more.tagName, text: more.textContent, expanded: more.getAttribute("aria-expanded"),
          label: more.getAttribute("aria-label") } : null,
        detailHidden: detail.hidden, facts: facts(detail) };
    }),
  };
}
"""

_LINKS_STEPS = r"""
      } else if (step.links !== undefined) {
        out.steps.push(Object.assign(linksView(), { requestsSoFar: requests.slice() }));
      } else if (step.more !== undefined) {
        const buttons = ids["char-gear"].all().filter((e) => has(e, "gear-more"));
        if (!buttons[step.more]) { out.steps.push({ missing: "more " + step.more }); continue; }
        const before = requests.length;
        buttons[step.more].fire("click"); await tick();
        out.steps.push(Object.assign(linksView(), { newRequests: requests.slice(before) }));
"""


def _harness() -> str:
    text = character_tab._harness()
    assert text.count(_STEP_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _LINKS_VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_STEP_ANCHOR, _LINKS_STEPS.lstrip("\n") + _STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m21-04-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m21-04-scenario.json"
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
    return [s for s in out["steps"] if s.get("linksView")]


def _only_api(out: dict[str, Any]) -> None:
    for step in out["steps"]:
        for request in step.get("requestsSoFar", []) + step.get("newRequests", []):
            assert request["url"].startswith(("/api/commands", "/api/run")), request


def _link(link: str, site: str, label: str, url: str) -> dict[str, str]:
    return {"link": link, "site": site, "site_name": label, "label": label, "url": url}


def _nolink(link: str, site: str, label: str, why: str) -> dict[str, str]:
    return {"link": link, "site": site, "site_name": label, "label": label, "why": why}


def _known(**over: Any) -> dict[str, Any]:
    """The links of a character whose region is known (constructed)."""
    made: dict[str, Any] = {
        "profiles": [
            _link("armory_character", "blizzard", "Armory", ARMORY),
            _link("journey", "journey", "Journey Companion", JOURNEY),
        ],
        "guild": None,
        "guild_absent": "no guild, or the guild was not loaded yet when the game saved",
        "guild_link": None,
    }
    made.update(over)
    return made


def _with(
    server: Any,
    steps: list[dict[str, Any]],
    made: dict[str, Any] | None = None,
    saved: list[dict[str, Any]] | None = None,
    item_links: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = _scenario(server, steps, [dress.SANGRAAL])
    report = _report(scenario, dress.SANGRAAL)
    if made is not None:
        report["links"] = made
    if saved is not None:
        report["saved_links"] = saved
    if item_links is not None:
        report["item_links"] = item_links
    _set_report(scenario, dress.SANGRAAL, report)
    return scenario, report


def _saved_ft(url: str) -> dict[str, str]:
    return {
        "flavor_folder": FLAVOR,
        "account": dress.ACCOUNT,
        "character": dress.SANGRAAL,
        "site": "forevertwinking",
        "site_name": "ForeverTwinking",
        "kind": "profile",
        "label": "ForeverTwinking",
        "url": url,
        "saved_at": "2026-11-24T12:00:00Z",
    }


# ─── the markup ──────────────────────────────────────────────────────────────


def test_every_new_link_is_static_markup_with_no_href() -> None:
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    for anchor in (
        "char-profile-armory",
        "char-profile-ft",
        "char-profile-journey",
        "char-profile-raiderio",
        "char-guild-link",
    ):
        tag = re.search(rf'<a id="{anchor}"[^>]*>', page)
        assert tag is not None, anchor
        assert tag.group(0) == (
            f'<a id="{anchor}" class="dressing-link" target="_blank" '
            'rel="noopener noreferrer" hidden>'
        )
    gear = re.findall(r'<a id="char-gear-wowhead-(\d+)"[^>]*>', page)
    assert gear == [str(n) for n in range(20)]
    assert (
        page.count(
            'class="gear-name gear-wowhead" target="_blank" rel="noopener noreferrer" hidden></a>'
        )
        == 20
    )
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert "const GEAR_LINK_COUNT = 20;" in script
    # One site per link, by the allowlist's key; the page names no origin.
    for call in (
        'pointOutside(anchor, link.url, "wowhead")',
        "pointOutside(byId(slot.id), row.url, slot.site)",
        'pointOutside(anchor, row.url, "forevertwinking")',
        'pointOutside(anchor, link.url, "blizzard")',
    ):
        assert script.count(call) == 1, call


# ─── the Profiles row ────────────────────────────────────────────────────────


def test_the_real_report_with_no_region_shows_the_row_and_says_why(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario, report = _with(server, [{"char_tab": True}, {"links": True}])
    assert report["region"] is None
    assert {p["why"] for p in report["links"]["profiles"]} == {NO_REGION}
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    assert view["rowHidden"] is False
    for key in ("armory", "journey", "raiderio", "ft"):
        assert view[key]["hidden"] is True and view[key]["href"] is None, key
    assert view["why"] == f"Armory and Journey Companion: no link ({NO_REGION})"
    # The capture's records are schema 4: no guild, so no guild line.
    assert view["guildHidden"] is True


def test_each_profile_link_is_its_own_static_link_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    made = _known(
        profiles=[
            _link("armory_character", "blizzard", "Armory", ARMORY),
            _link("journey", "journey", "Journey Companion", JOURNEY),
            _link("raiderio", "raiderio", "Raider.IO", RAIDERIO),
        ]
    )
    scenario, _ = _with(server, [{"char_tab": True}, {"links": True}], made, [_saved_ft(FT)])
    out = _drive(tmp_path / "node", scenario)
    view = _views(out)[-1]
    assert view["rowHidden"] is False and view["why"] == ""
    for key, url, _words in (
        ("armory", ARMORY, "Armory"),
        ("journey", JOURNEY, "Journey Companion"),
        ("raiderio", RAIDERIO, "Raider.IO"),
        ("ft", FT, "ForeverTwinking"),
    ):
        assert view[key]["hidden"] is False and view[key]["href"] == url, key
        assert view[key]["text"] == "", key  # the static link's own words are page.html's
    _only_api(out)


def test_raiderio_is_left_out_while_the_report_leaves_it_out_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario, _ = _with(server, [{"char_tab": True}, {"links": True}], _known(), [])
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    assert view["armory"]["href"] == ARMORY and view["journey"]["href"] == JOURNEY
    assert view["raiderio"]["hidden"] is True and view["raiderio"]["href"] is None
    assert view["ft"]["hidden"] is True and view["why"] == ""


def test_each_reason_is_said_once_with_its_links_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    made = _known(
        profiles=[
            _nolink(
                "armory_character", "blizzard", "Armory", "Armory has no pages for the cn region"
            ),
            _link("journey", "journey", "Journey Companion", JOURNEY),
            _nolink("raiderio", "raiderio", "Raider.IO", f"<b>ruleset</b>{RLO}"),
        ]
    )
    scenario, _ = _with(server, [{"char_tab": True}, {"links": True}], made, [])
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    assert view["armory"]["hidden"] is True and view["journey"]["href"] == JOURNEY
    assert view["why"] == (
        "Armory: no link (Armory has no pages for the cn region); "
        "Raider.IO: no link (<b>ruleset</b>⟨U+202E⟩)"
    )


_HOSTILE = [
    ("armory", "https://evil.example/en-us/forever/us/armory/character/x-y"),
    ("armory", JOURNEY),  # another site's origin in this link
    ("armory", ARMORY.replace("https:", "http:")),
    ("armory", ARMORY.replace("https://", "https://u:p@")),
    ("armory", ARMORY.replace("worldofwarcraft", "WORLDOFWARCRAFT")),  # rewritten by the parser
    ("journey", JOURNEY.replace("wofwforever.com", "wofwforever.com:443")),
    ("journey", "javascript:alert(1)"),
    ("journey", "//wofwforever.com/en/tools/"),
]


@pytest.mark.parametrize(("which", "url"), _HOSTILE, ids=lambda v: f"constructed: {v!r}")
def test_a_hostile_profile_address_is_not_drawn_constructed(
    server: Any, forever: Path, tmp_path: Path, which: str, url: str
) -> None:
    link, site, label = {
        "armory": ("armory_character", "blizzard", "Armory"),
        "journey": ("journey", "journey", "Journey Companion"),
    }[which]
    other = "journey" if which == "armory" else "armory"
    made = _known()
    made["profiles"] = [
        _link(link, site, label, url) if p["link"] == link else p for p in made["profiles"]
    ]
    scenario, _ = _with(server, [{"char_tab": True}, {"links": True}], made, [])
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    assert view[which]["hidden"] is True and view[which]["href"] is None
    assert view[other]["hidden"] is False
    assert view["why"] == f"{label}: no link (its address is not one this page links to)"


def test_a_hostile_saved_page_is_not_in_the_row_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    hostile = "https://evil.example/armory/13-sandgraal-rizzen"
    scenario, _ = _with(
        server, [{"char_tab": True}, {"links": True}], _known(), [_saved_ft(hostile)]
    )
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    assert view["ft"]["hidden"] is True and view["ft"]["href"] is None


def test_no_allowlist_draws_no_profile_link_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario, _ = _with(server, [{"char_tab": True}, {"links": True}], _known(), [_saved_ft(FT)])
    scenario["commands"]["link_origins"] = {}
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    for key in ("armory", "journey", "ft"):
        assert view[key]["hidden"] is True and view[key]["href"] is None, key
    assert all(i["tag"] == "span" and i["href"] is None for i in view["items"])


# ─── the guild ───────────────────────────────────────────────────────────────


def test_the_guild_name_is_its_armory_link_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    made = _known(
        guild="Booty Bay Cartel",
        guild_absent=None,
        guild_link={
            "link": "armory_guild",
            "site": "blizzard",
            "site_name": "Armory",
            "label": "Armory guild page",
            "url": GUILD,
        },
    )
    scenario, _ = _with(server, [{"char_tab": True}, {"links": True}], made, [])
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    assert view["guildHidden"] is False
    assert view["guild"]["href"] == GUILD and view["guild"]["text"] == "Booty Bay Cartel"
    assert view["guildText"] == ""


@pytest.mark.parametrize(
    ("link", "said"),
    [
        (
            {
                "link": "armory_guild",
                "site": "blizzard",
                "site_name": "Armory",
                "label": "Armory guild page",
                "why": NO_REGION,
            },
            NO_REGION,
        ),
        (
            {
                "link": "armory_guild",
                "site": "blizzard",
                "site_name": "Armory",
                "label": "Armory guild page",
                "url": "https://evil.example/guild/x",
            },
            "its address is not one this page links to",
        ),
    ],
    ids=["constructed: no region", "constructed: an origin outside the list"],
)
def test_a_guild_with_no_link_is_text_that_says_why_constructed(
    server: Any, forever: Path, tmp_path: Path, link: dict[str, str], said: str
) -> None:
    made = _known(guild=f"<i>Booty</i> Bay{RLO}", guild_absent=None, guild_link=link)
    scenario, _ = _with(server, [{"char_tab": True}, {"links": True}], made, [])
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    assert view["guildHidden"] is False
    assert view["guild"]["hidden"] is True and view["guild"]["href"] is None
    assert view["guildText"] == f"<i>Booty</i> Bay⟨U+202E⟩ (no link: {said})"


# ─── the gear rows ───────────────────────────────────────────────────────────


def test_each_item_name_is_its_wowhead_link(server: Any, forever: Path, tmp_path: Path) -> None:
    scenario, report = _with(server, [{"char_tab": True}, {"links": True}])
    urls = {i["slot"]: i["link"]["url"] for i in report["item_links"]}
    names = {s["slot"]: s["name"] for s in report["slots"]}
    assert urls and set(urls) == set(names)
    for slot in report["slots"]:
        assert urls[slot["slot"]] == f"https://www.wowhead.com/forever/item={slot['item_id']}"
    out = _drive(tmp_path / "node", scenario)
    items = _views(out)[-1]["items"]
    assert len(items) == len(urls)
    assert sorted(i["href"] for i in items) == sorted(urls.values())
    assert sorted(i["text"] for i in items) == sorted(names.values())
    for item in items:
        assert item["tag"] == "a" and item["id"].startswith("char-gear-wowhead-")
        assert item["hidden"] is False
        assert item["className"].split()[:2] == ["gear-name", "gear-wowhead"]
        assert item["more"] == {
            "tag": "button",
            "text": "Details",
            "expanded": "false",
            "label": "Details of " + item["text"],
        }
        assert item["detailHidden"] is True and "Wowhead" not in item["facts"]
    # Each static link once, in the order the rows are drawn.
    assert [i["id"] for i in items] == [f"char-gear-wowhead-{n}" for n in range(len(items))]
    _only_api(out)


def test_details_open_with_their_own_button(server: Any, forever: Path, tmp_path: Path) -> None:
    scenario, _ = _with(server, [{"char_tab": True}, {"more": 0}, {"more": 0}])
    opened, closed = _views(_drive(tmp_path / "node", scenario))
    assert opened["items"][0]["detailHidden"] is False
    assert opened["items"][0]["more"]["expanded"] == "true"
    assert opened["newRequests"] == []
    assert closed["items"][0]["detailHidden"] is True
    assert closed["items"][0]["more"]["expanded"] == "false"


_HOSTILE_ITEMS = [
    "https://evil.example/forever/item=1",
    "http://www.wowhead.com/forever/item=1",
    "https://u@www.wowhead.com/forever/item=1",
    "https://WWW.WOWHEAD.COM/forever/item=1",
    "https://www.wowhead.com/forever/item=1 x",
    FT,  # another listed site's origin
    "javascript:alert(1)",
]


@pytest.mark.parametrize("url", _HOSTILE_ITEMS, ids=lambda u: f"constructed: {u!r}")
def test_a_hostile_item_address_leaves_the_name_plain_constructed(
    server: Any, forever: Path, tmp_path: Path, url: str
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"more": 0}], [dress.SANGRAAL])
    report = _report(scenario, dress.SANGRAAL)
    first = character_tab._first_drawn(report)
    for item in report["item_links"]:
        if item["slot"] == first["slot"]:
            item["link"]["url"] = url
    _set_report(scenario, dress.SANGRAAL, report)
    view = _views(_drive(tmp_path / "node", scenario))[-1]
    row = view["items"][0]
    assert row["text"] == first["name"]
    assert row["tag"] == "span" and row["href"] is None
    assert row["className"].split()[0] == "gear-name" and "gear-wowhead" not in row["className"]
    assert row["detailHidden"] is False
    assert row["facts"][row["facts"].index("Wowhead") + 1] == (
        "no link: its address is not one this page links to"
    )
    # The other rows keep their links, starting at the first static link.
    assert view["items"][1]["id"] == "char-gear-wowhead-0"


def test_an_item_with_no_link_says_why_in_its_detail_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"more": 0}], [dress.SANGRAAL])
    report = _report(scenario, dress.SANGRAAL)
    first = character_tab._first_drawn(report)
    for item in report["item_links"]:
        if item["slot"] == first["slot"]:
            item["link"] = {
                "link": "wowhead_item",
                "site": "wowhead",
                "site_name": "Wowhead",
                "label": "Wowhead",
                "why": "Wowhead has no pages wowlab knows for this game version",
            }
    _set_report(scenario, dress.SANGRAAL, report)
    row = _views(_drive(tmp_path / "node", scenario))[-1]["items"][0]
    assert row["tag"] == "span" and row["href"] is None
    assert row["facts"][row["facts"].index("Wowhead") + 1] == (
        "no link: Wowhead has no pages wowlab knows for this game version"
    )


def test_a_report_without_item_links_draws_plain_names_constructed(
    server: Any, forever: Path, tmp_path: Path
) -> None:
    scenario, _ = _with(server, [{"char_tab": True}, {"links": True}], item_links=[])
    items = _views(_drive(tmp_path / "node", scenario))[-1]["items"]
    assert items and all(i["tag"] == "span" and i["href"] is None for i in items)
    assert all("Wowhead" not in i["facts"] for i in items)
