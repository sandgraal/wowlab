"""The Characters home of `wowlab ui` (M18-04, docs/design/COMPANION.md):
two status cards (the backup, the addons), one card per character folder,
a dashed "Missing a character?" card, and, when the name tables are not
downloaded, one line at the top that offers `names fetch`.

The page is driven under Node with the harness of `test_ui_account.py`
(itself `test_ui_character.py`'s and `test_ui_explorer.py`'s: HTML sinks
throw, `a` and `svg` elements, attributes outside a closed set and
navigation are trapped, and DOM collections are not Arrays), fed what the
real CLI printed through the real server. The install is that module's two
captures side by side (`constructed`, L8): `_classic_beta_` holds two
schema-1 records and a folder with no record, `_forever_` the launch
client's schema-4 record of a warlock. Constructed too, as labelled: report
fields edited before the page reads them (names as if downloaded, an old
backup), the reply of a `names fetch` (no live call, ADR-0012), and a
`char history` report given two saves near today, so a gain is "this
week" whenever the test runs. Every snapshot goes into the redirected
home's data folder; nothing reads or writes a real install.
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

import test_ui_account as hub_tab
import test_ui_account_backup as backup_tab
import test_ui_explorer as first_stage

both = hub_tab.both
home = hub_tab.home
server = hub_tab.server
no_env_flavor = hub_tab.no_env_flavor

BETA = hub_tab.BETA
FOREVER = hub_tab.FOREVER
FIRST = hub_tab.FIRST
SECOND = hub_tab.SECOND
BARE = hub_tab.BARE
WARLOCK = hub_tab.WARLOCK
ACCOUNT = hub_tab.ACCOUNT
LAUNCH_ACCOUNT = hub_tab.LAUNCH_ACCOUNT
HISTORY = ["char", "history"]
NAMES = ["names", "fetch"]
HOLDS = "settings, keybinds, macros and addon data"
DAY_NS = 86_400 * 10**9

_VIEW = r"""
function homeView() {
  const t = (id) => (ids[id] ? ids[id].textContent : null);
  const inside = (c, cls) => c.all().find((e) => has(e, cls));
  const text = (c, cls) => { const e = inside(c, cls); return e ? e.textContent : null; };
  const cards = ids["char-cards"].children.map((c) => ({
    cls: c.className,
    name: text(c, "card-name"),
    who: text(c, "card-class"),
    level: text(c, "card-level"),
    ilvl: text(c, "card-ilvl-value"),
    profs: c.all().filter((e) => has(e, "card-prof")).map((p) => ({
      head: p.children[0].textContent,
      fill: (inside(p, "bar-fill") || { className: null }).className })),
    foot: (inside(c, "card-foot") || { children: [{ textContent: null }] }).children[0].textContent,
    gain: text(c, "card-gain"),
    note: text(c, "card-note"),
    buttons: c.all().filter((e) => e.tagName === "button").map((b) => b.className + ": " + b.textContent),
    text: c.textContent,
  }));
  return {
    namesHidden: ids["names-offer"].hidden,
    namesFetchHidden: ids["names-fetch"].hidden,
    namesStatus: t("names-status"),
    backup: { cls: ids["home-backup"].className, title: t("home-backup-title"),
      detail: t("home-backup-detail"), status: t("home-backup-status"),
      nowDisabled: ids["home-backup-now"].disabled, restoreHidden: ids["home-restore"].hidden },
    addons: { cls: ids["home-addons"].className, title: t("home-addons-title"),
      detail: t("home-addons-detail") },
    cards: cards,
    cardsStatus: t("cards-status"),
  };
}
"""

_STEPS = r"""
      } else if (step.home !== undefined) {
        out.steps.push({ home: homeView() });
      } else if (step.home_open !== undefined) {
        const before = requests.length;
        const card = ids["char-cards"].children[step.home_open];
        const button = card && card.all().find((e) => e.tagName === "button" && has(e, "card-open"));
        if (!button) { out.steps.push({ missing: "open " + step.home_open }); continue; }
        button.fire("click"); await tick();
        out.steps.push({ home: homeView(), char: charView(), picker: ids["char-picker"].value,
          characterHidden: ids["character-view"].hidden, newRequests: requests.slice(before) });
      } else if (step.home_click !== undefined) {
        const before = requests.length;
        let button = ids[step.home_click];
        if (!button) {
          button = ids["char-cards"].all().find((e) => e.tagName === "button" && has(e, step.home_click));
        }
        if (!button) { out.steps.push({ missing: step.home_click }); continue; }
        button.fire("click"); await tick();
        const shown = Object.keys(ids).filter((id) => id.endsWith("-view") && !ids[id].hidden);
        out.steps.push({ home: homeView(), newRequests: requests.slice(before), shown: shown,
          title: ids.title.textContent,
          fields: ids.fields.all().filter((e) => e.tagName === "input").map((e) => ({ id: e.id, value: e.value })),
          backupStatus: ids["backup-status"].textContent });
      } else if (step.push_run !== undefined) {
        scenario.runs.push(step.push_run);
        out.steps.push({});
"""

_ANCHOR = hub_tab._ANCHOR
_VIEW_ANCHOR = hub_tab._VIEW_ANCHOR
_HUB_HARNESS = hub_tab._harness


def _harness() -> str:
    text = _HUB_HARNESS()
    assert text.count(_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_ANCHOR, _STEPS.lstrip("\n") + _ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = hub_tab._harness
    hub_tab._harness = _harness
    try:
        return hub_tab._drive(folder, scenario)
    finally:
        hub_tab._harness = original


def _history_params(character: str, account: str, flavor: str) -> dict[str, Any]:
    return {"character": character, "account": account, "flavor": flavor}


def _history(server: Any) -> dict[str, Any]:
    """The real `char history` of the warlock: one save, the file in the
    install (no snapshot holds another)."""
    run = hub_tab._run_of(server, HISTORY, _history_params(WARLOCK, LAUNCH_ACCOUNT, FOREVER))
    assert run["exit_code"] == 0, run["stderr"]
    return run


def _patch_day(server: Any) -> list[dict[str, Any]]:
    """The patch-day card's three real reads of each flavor, as the page asks."""
    runs = []
    for flavor in (BETA, FOREVER):
        runs.append(hub_tab._run_of(server, ["addons", "check"], {"flavor": flavor}))
        for flag in ("orphans", "unreadable"):
            runs.append(hub_tab._run_of(server, ["sv", "list"], {flag: True, "flavor": flavor}))
    return runs


HOUR_NS = 3_600 * 10**9


def _played_recently(scenario: dict[str, Any]) -> None:
    """Constructed: each recorded row of the report played in the last few
    hours, in the order the install's file times give (the warlock last),
    so their gains are read whenever the test runs."""
    report = _summary(scenario)
    rows = [r for r in report["rows"] if r["state"] == "recorded"]
    rows.sort(key=lambda r: r["mtime_ns"], reverse=True)
    now = time.time_ns()
    for at, row in enumerate(rows):
        row["mtime_ns"] = now - (at + 1) * HOUR_NS
    _edit_summary(scenario, report)


def _scenario(
    server: Any, steps: list[dict[str, Any]], *, reads: bool = True, recent: bool = True
) -> dict[str, Any]:
    scenario = hub_tab._scenario(server, steps)
    if reads:
        scenario["runs"] += _patch_day(server)
        scenario["runs"].append(_history(server))
    if recent:
        _played_recently(scenario)
    return scenario


def _summary(scenario: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(hub_tab._summary_run(scenario)["stdout"])
    return report


def _edit_summary(scenario: dict[str, Any], report: dict[str, Any]) -> None:
    hub_tab._summary_run(scenario)["stdout"] = json.dumps(report)


def _card(view: dict[str, Any], name: str) -> dict[str, Any]:
    [card] = [c for c in view["cards"] if c["name"] == name]
    return card


def _names(character: str) -> str:
    return character.split("/")[1]


# ─── the character cards ─────────────────────────────────────────────────────


def test_a_card_per_character_then_the_missing_card_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))
    view = out["steps"][0]["home"]
    names = [c["name"] for c in view["cards"]]
    # Latest played first (the warlock, then FIRST, then SECOND), the folder
    # with no lab record after them, the dashed card last.
    assert names == [
        _names(WARLOCK),
        _names(FIRST),
        _names(SECOND),
        _names(BARE),
        "Missing a character?",
    ]
    warlock = view["cards"][0]
    # The class colour comes from the client's token, as a class the style
    # sheet colours; the level tile reads "Level <n>" to a screen reader.
    assert "cls-warlock" in warlock["cls"].split()
    assert re.fullmatch(r"Level \d+", warlock["level"])
    # Names are not downloaded in this test home: the class token in words,
    # and the race's id.
    assert re.fullmatch(r"Warlock · race \d+", warlock["who"]), warlock["who"]
    assert re.fullmatch(r"\d+\.\d", warlock["ilvl"])
    # Top professions, at most four, as bars in five-percent steps.
    assert 1 <= len(warlock["profs"]) <= 4
    for prof in warlock["profs"]:
        assert re.fullmatch(r"Skill line \d+\d+/\d+", prof["head"]), prof
        assert re.fullmatch(r"bar-fill fill-(\d|1\d|20)", prof["fill"]), prof
    # Last played, as people say it, and the game, since the cards span two.
    assert re.fullmatch(r"Last played [A-Z][a-z]{2} \d{1,2} · WoW Forever", warlock["foot"]), (
        warlock
    )
    assert warlock["buttons"] == [f"card-open: {_names(WARLOCK)}"]
    beta = _card(view, _names(FIRST))
    assert beta["foot"].endswith(" · WoW Classic Beta")


def test_a_card_shows_no_folder_path_or_id_of_wowlab_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))
    for card in out["steps"][0]["home"]["cards"]:
        for word in (
            BETA,
            FOREVER,
            ACCOUNT,
            LAUNCH_ACCOUNT,
            "WowLab.lua",
            "SavedVariables",
            "WTF/",
            "Account/",
        ):
            assert word not in card["text"], (word, card["text"])


def test_a_character_with_no_lab_record_says_what_puts_one_there_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))
    bare = _card(out["steps"][0]["home"], _names(BARE))
    # No record: no class, no level, nothing to open.
    assert bare["cls"] == "char-card card-bare"
    assert bare["level"] == "Level ?"
    assert bare["who"] is None and bare["ilvl"] is None and bare["profs"] == []
    assert bare["buttons"] == []
    assert bare["note"] == (
        "No details yet. Log in with this character once with the wowlab addon installed, then log out."
    )


def test_a_schema_1_record_has_no_class_colour_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))
    first = _card(out["steps"][0]["home"], _names(FIRST))
    assert first["cls"] == "char-card"
    assert first["who"] is None
    assert first["buttons"] == [f"card-open: {_names(FIRST)}"]


def test_names_cached_show_race_class_and_profession_names_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """The report as it reads once the tables are downloaded (constructed:
    the names written into the recorded report)."""
    scenario = _scenario(server, [{"home": True}])
    report = _summary(scenario)
    assert report["names_missing"], "this test home has no name table"
    report["names_missing"] = []
    for row in report["rows"]:
        if row["label"] == _names(WARLOCK):
            row["race_name"] = "Undead"
            row["class_name"] = "Warlock"
            for at, prof in enumerate(row["professions"]):
                prof["name"] = f"Craft {at}"
    _edit_summary(scenario, report)
    view = _drive(tmp_path / "node", scenario)["steps"][0]["home"]
    warlock = _card(view, _names(WARLOCK))
    assert warlock["who"] == "Undead Warlock"
    assert [p["head"].split(" ")[0] for p in warlock["profs"]] == ["Craft"] * len(warlock["profs"])
    assert view["namesHidden"] is True


def test_names_not_cached_offer_the_download_once_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """The names line: one offer at the top; a click runs `names fetch` for
    each game version the report names, through the CLI path, then reads
    the report again (constructed: `names fetch`'s reply)."""
    scenario = _scenario(server, [])
    missing = _summary(scenario)["names_missing"]
    assert missing
    fetched = {
        "command": NAMES,
        "stdout": json.dumps({"flavors": [], "fetched": 4}),
        "exit_code": 0,
        "stderr": "",
    }
    steps: list[dict[str, Any]] = [{"home": True}]
    for entry in missing:
        steps.append({"push_run": dict(fetched, params={"flavor": entry["flavor"]})})
    steps.append({"home_click": "names-fetch"})
    scenario["steps"] = steps
    out = _drive(tmp_path / "node", scenario)
    before = out["steps"][0]["home"]
    assert before["namesHidden"] is False and before["namesFetchHidden"] is False
    after = out["steps"][-1]
    bodies = hub_tab._bodies(after["newRequests"])
    asked = [b for b in bodies if b["command"] == NAMES]
    assert asked == [{"command": NAMES, "params": {"flavor": e["flavor"]}} for e in missing]
    # Then the report is read again, once.
    assert bodies[len(asked)] == {"command": ["char", "list"], "params": hub_tab.SUMMARY}
    assert after["home"]["namesStatus"] == "Names downloaded."


def test_a_failed_names_download_says_why_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [])
    flavor = _summary(scenario)["names_missing"][0]["flavor"]
    failed = {
        "command": NAMES,
        "params": {"flavor": flavor},
        "stdout": json.dumps({"flavors": [], "fetched": 0}),
        "exit_code": 1,
        "stderr": "wago.tools could not be reached.",
    }
    scenario["steps"] = [{"push_run": failed}, {"home_click": "names-fetch"}]
    after = _drive(tmp_path / "node", scenario)["steps"][-1]
    assert after["home"]["namesStatus"] == (
        "The names could not all be downloaded: wago.tools could not be reached."
    )
    assert after["home"]["namesFetchHidden"] is False
    # Nothing read again after a failure.
    bodies = hub_tab._bodies(after["newRequests"])
    assert [b["command"] for b in bodies] == [NAMES]


def test_a_card_opens_the_character_page_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [{"home_open": 0}]
    scenario = _scenario(server, steps)
    picks = ((WARLOCK, LAUNCH_ACCOUNT, FOREVER),)
    for character, account, flavor in picks:
        for command in hub_tab.PICK_READS:
            scenario["runs"].append(
                hub_tab._run_of(server, list(command), _history_params(character, account, flavor))
            )
    out = _drive(tmp_path / "node", scenario)
    step = out["steps"][0]
    assert step["characterHidden"] is False
    gear = [b for b in hub_tab._bodies(step["newRequests"]) if b["command"] == ["char", "gear"]]
    assert gear == [
        {"command": ["char", "gear"], "params": _history_params(WARLOCK, LAUNCH_ACCOUNT, FOREVER)}
    ]


def test_the_missing_card_opens_the_install_plan_and_runs_nothing_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home_click": "link-button"}]))
    step = out["steps"][0]
    missing = step["home"]["cards"][-1]
    assert missing["name"] == "Missing a character?"
    assert missing["cls"] == "char-card card-missing"
    assert missing["buttons"] == ["link-button: Install the wowlab addon"]
    assert "Log in with it once and log out." in missing["text"]
    # The Commands view on `addon install`, its addon named; the plan is
    # the player's to ask for, so nothing ran.
    assert step["shown"] == ["commands-view"]
    assert step["title"] == "wowlab addon install"
    assert {"id": "param-name", "value": "lab"} in step["fields"]
    assert hub_tab._bodies(step["newRequests"]) == []


# ─── the gains ───────────────────────────────────────────────────────────────


def test_no_gain_with_one_save_constructed(server: Any, both: Path, tmp_path: Path) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))
    view = out["steps"][0]["home"]
    assert _card(view, _names(WARLOCK))["gain"] == ""
    # `char history` was asked for each recorded character, after the
    # patch-day reads, one at a time.
    bodies = hub_tab._bodies(out["requests"])
    history = [b for b in bodies if b["command"] == HISTORY]
    wanted = [
        {"command": HISTORY, "params": _history_params(c, a, f)}
        for c, a, f in (
            (FIRST, ACCOUNT, BETA),
            (SECOND, ACCOUNT, BETA),
            (WARLOCK, LAUNCH_ACCOUNT, FOREVER),
        )
    ]
    assert sorted(map(json.dumps, history)) == sorted(map(json.dumps, wanted))
    last_card_read = max(
        i for i, b in enumerate(bodies) if b["command"] in (["addons", "check"], ["sv", "list"])
    )
    assert all(i > last_card_read for i, b in enumerate(bodies) if b["command"] == HISTORY)


def _two_saves(run: dict[str, Any], levels: int, item_level: float, base_days: float) -> str:
    """Constructed: the real report's one save, given an earlier save
    `base_days` before it (lower by `levels` and `item_level`), the newest
    saved a day ago."""
    report = json.loads(run["stdout"])
    [live] = report["rows"]
    now = time.time_ns()
    earlier = copy.deepcopy(live)
    live["saved_at_ns"] = now - DAY_NS
    earlier["saved_at_ns"] = live["saved_at_ns"] - int(base_days * DAY_NS)
    earlier["level"] = live["level"] - levels
    earlier["item_level"] = live["item_level"] - item_level
    report["rows"] = [earlier, live]
    return json.dumps(report)


def test_a_gain_this_week_is_said_constructed(server: Any, both: Path, tmp_path: Path) -> None:
    scenario = _scenario(server, [{"home": True}])
    run = scenario["runs"][-1]
    assert run["command"] == HISTORY
    run["stdout"] = _two_saves(run, 6, 2.0, 3)
    view = _drive(tmp_path / "node", scenario)["steps"][0]["home"]
    assert _card(view, _names(WARLOCK))["gain"] == "+6 levels this week"


def test_an_item_level_gain_when_no_level_was_gained_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"home": True}])
    run = scenario["runs"][-1]
    run["stdout"] = _two_saves(run, 0, 1.5, 2)
    view = _drive(tmp_path / "node", scenario)["steps"][0]["home"]
    assert _card(view, _names(WARLOCK))["gain"] == "+1.5 item level this week"


def test_a_gain_from_an_older_save_names_its_date_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"home": True}])
    run = scenario["runs"][-1]
    run["stdout"] = _two_saves(run, 1, 0.0, 20)
    gain = _card(_drive(tmp_path / "node", scenario)["steps"][0]["home"], _names(WARLOCK))["gain"]
    assert re.fullmatch(r"\+1 level since [A-Z][a-z]{2} \d{1,2}(, \d{4})?", gain), gain


def test_no_gain_when_the_newest_save_is_older_than_a_week_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"home": True}])
    run = scenario["runs"][-1]
    report = json.loads(_two_saves(run, 6, 2.0, 3))
    for row in report["rows"]:
        row["saved_at_ns"] -= 10 * DAY_NS
    run["stdout"] = json.dumps(report)
    view = _drive(tmp_path / "node", scenario)["steps"][0]["home"]
    assert _card(view, _names(WARLOCK))["gain"] == ""


# ─── the status cards ────────────────────────────────────────────────────────


def test_no_backup_yet_offers_back_up_now_and_no_restore_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    view = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))["steps"][0]["home"]
    card = view["backup"]
    assert card["title"] == "Your UI is not backed up yet"
    assert card["detail"] == f"A backup keeps your {HOLDS}."
    assert "status-warn" in card["cls"].split()
    assert card["nowDisabled"] is False and card["restoreHidden"] is True


def test_a_backup_says_how_long_ago_and_what_it_holds_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    backup_tab._created(server)
    view = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))["steps"][0]["home"]
    card = view["backup"]
    assert card["title"] == "Your UI is backed up"
    assert re.fullmatch(rf"(Just now|\d+ minutes? ago) · {HOLDS}", card["detail"]), card["detail"]
    assert "status-ok" in card["cls"].split()
    assert card["restoreHidden"] is False


def test_an_old_backup_says_days_constructed(server: Any, both: Path, tmp_path: Path) -> None:
    backup_tab._created(server)
    scenario = _scenario(server, [{"home": True}])
    report = _summary(scenario)
    for line in report["backups"]:
        if line["flavor"] == BETA:
            line["created_at"] = "2020-01-01T00:00:00.000000Z"
    _edit_summary(scenario, report)
    card = _drive(tmp_path / "node", scenario)["steps"][0]["home"]["backup"]
    assert re.fullmatch(rf"\d+ days ago · {HOLDS}", card["detail"]), card["detail"]


def test_the_game_updated_since_the_backup_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    backup_tab._created(server)
    backup_tab._update_beta(both)
    view = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))["steps"][0]["home"]
    card = view["backup"]
    assert card["title"] == "The game updated since your last backup"
    assert re.fullmatch(
        rf"Last backup (just now|\d+ minutes? ago) · back up now to keep your {HOLDS} as they are\.",
        card["detail"],
    ), card["detail"]
    assert "status-warn" in card["cls"].split()
    assert card["restoreHidden"] is False


def test_back_up_now_on_the_home_runs_snap_create_for_its_game_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"home_click": "home-backup-now"}])
    scenario["runs"].append(backup_tab._created(server))
    step = _drive(tmp_path / "node", scenario)["steps"][0]
    bodies = hub_tab._bodies(step["newRequests"])
    assert bodies == [{"command": ["snap", "create"], "params": {"flavor": BETA}}]
    # The card's own words name no folder and no id; Backups keeps both.
    assert step["home"]["backup"]["status"].startswith("Backed up just now.")
    assert BETA in step["backupStatus"]
    assert step["home"]["backup"]["title"] == "Your UI is backed up"


def test_restore_on_the_home_opens_the_restore_plan_and_runs_nothing_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    made = json.loads(backup_tab._created(server)["stdout"])["id"]
    step = _drive(tmp_path / "node", _scenario(server, [{"home_click": "home-restore"}]))["steps"][
        0
    ]
    assert step["shown"] == ["commands-view"]
    assert step["title"] == "wowlab snap restore"
    assert {"id": "param-snapshot_id", "value": made} in step["fields"]
    assert hub_tab._bodies(step["newRequests"]) == []


def test_the_addons_card_counts_what_loads_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}]))
    view = out["steps"][0]["home"]
    card = view["addons"]
    # The beta flavor's checked addons, as `addons check` read them.
    assert {"command": ["addons", "check"], "params": {"flavor": BETA}} in _patch_day_runs(out)
    assert re.fullmatch(
        r"(\d+ of \d+ addons|All \d+ addons|Your one addon) loads? on this version", card["title"]
    ), card
    assert card["detail"].endswith(("Nothing to fix.", "See Addons for what to fix."))
    assert "status-wait" not in card["cls"].split()


def _patch_day_runs(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        b
        for b in hub_tab._bodies(out["requests"])
        if b["command"] in (["addons", "check"], ["sv", "list"])
    ]


def test_the_addons_card_counts_each_kind_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Constructed: `addons check`'s report for the beta flavor replaced by
    three addons, one current, one made for another version, one that
    needs an addon that is not installed."""
    scenario = _scenario(server, [{"home": True}])
    [run] = [
        r
        for r in scenario["runs"]
        if r["command"] == ["addons", "check"] and r["params"] == {"flavor": BETA}
    ]
    report = json.loads(run["stdout"])
    toc = {"file": "A.toc", "interface_status": "matches", "interface_versions": [11500]}

    def addon(name: str, **more: Any) -> dict[str, Any]:
        return {
            "name": name,
            "path": f"Interface/AddOns/{name}",
            "selection": "flavor",
            "counted_tocs": ["A.toc"],
            "tocs": [dict(toc)],
            **more,
        }

    late = addon("Late", out_of_date=True)
    late["tocs"][0]["interface_status"] = "differs"
    late["tocs"][0]["interface_versions"] = [10000]
    needy = addon("Needy", missing_dependencies=["Gone"])
    report["check"]["addons"] = [addon("Fine"), late, needy]
    report["check"]["client_interface"] = 11500
    run["stdout"] = json.dumps(report)
    card = _drive(tmp_path / "node", scenario)["steps"][0]["home"]["addons"]
    assert card["title"] == "2 of 3 addons load on this version"
    assert card["detail"] == (
        "1 is made for another game version, so the game skips it. "
        "1 needs an addon that will not load. See Addons for what to fix."
    )
    assert "status-warn" in card["cls"].split()


def test_see_addons_opens_the_addons_section_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    step = _drive(tmp_path / "node", _scenario(server, [{"home_click": "home-addons-open"}]))[
        "steps"
    ][0]
    assert step["shown"] == ["addons-view"]


def test_an_addons_check_that_failed_says_so_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    # No patch-day run recorded: the harness answers each with an error.
    view = _drive(tmp_path / "node", _scenario(server, [{"home": True}], reads=False))["steps"][0][
        "home"
    ]
    assert view["addons"]["title"] == "wowlab could not check your addons"
    assert view["addons"]["detail"] == "Addons says why."
    # A history run that fails leaves the card without a gain.
    assert _card(view, _names(WARLOCK))["gain"] == ""


# ─── what the home may hold ──────────────────────────────────────────────────


def _section() -> str:
    script = (first_stage._TEMPLATES / "page.js").read_text(encoding="utf-8")
    start = script.index("// ─── the Characters home (M18-04")
    return script[start : script.index("// ─── the patch-day card on the Account tab", start)]


def test_the_home_runs_only_names_fetch_and_char_history() -> None:
    runs = re.findall(r"runJson\(([^)]*)\)", _section())
    assert runs == [
        '["names", "fetch"], { flavor: flavor }, true, true',
        '["char", "history"], params, true, true',
    ]
    body = _section()
    for word in ("innerHTML", "insertAdjacentHTML", "setAttribute", ".style", "fetch(", "--yes"):
        assert word not in body, word


def test_the_page_names_every_class_colour_it_draws() -> None:
    """Each class token the script colours has its three rules in the sheet,
    and its colour is the one COMPANION.md gives."""
    css = (first_stage._TEMPLATES / "page.css").read_text(encoding="utf-8")
    design = {
        "warrior": "#c69b6d",
        "paladin": "#f48cba",
        "hunter": "#aad372",
        "rogue": "#fff468",
        "priest": "#ffffff",
        "deathknight": "#c41e3a",
        "shaman": "#0070dd",
        "mage": "#3fc7eb",
        "warlock": "#8788ee",
        "monk": "#00ff98",
        "druid": "#ff7c0a",
        "demonhunter": "#a330c9",
        "evoker": "#33937f",
    }
    tokens = re.findall(
        r'"([A-Z]+)"', _section()[_section().index("CLASS_TOKENS") :].split("];")[0]
    )
    assert sorted(t.lower() for t in tokens) == sorted(design)
    for name, colour in design.items():
        assert f"--class-{name}: {colour};" in css, name
        for part in ("card-stripe", "card-level", "card-class"):
            assert f".cls-{name} .{part} {{" in css, (name, part)
    for step in range(21):
        assert f".fill-{step} {{ width: {step * 5}%; }}" in css


# ─── review round 1 on #566 ──────────────────────────────────────────────────


def test_gains_are_read_for_the_twelve_latest_played_this_week_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Constructed: the warlock's row copied fifteen times under other
    names, fourteen played in the last fourteen hours and one ten days ago.
    `char history` runs for the twelve played last, newest first, and for
    no character last played more than a week ago."""
    scenario = _scenario(server, [{"home": True}])
    report = _summary(scenario)
    [warlock] = [r for r in report["rows"] if r["label"] == _names(WARLOCK)]
    now = time.time_ns()
    copies = []
    for at in range(15):
        row = copy.deepcopy(warlock)
        row["label"] = f"Copy{at:02d}-Rizzen"
        row["mtime_ns"] = now - (at + 1) * HOUR_NS if at < 14 else now - 10 * DAY_NS
        copies.append(row)
    report["rows"] = [r for r in report["rows"] if r["state"] != "recorded"] + copies
    _edit_summary(scenario, report)
    out = _drive(tmp_path / "node", scenario)
    asked = [
        b["params"]["character"]
        for b in hub_tab._bodies(out["requests"])
        if b["command"] == HISTORY
    ]
    assert asked == [f"1/Copy{at:02d}-Rizzen" for at in range(12)]


def test_no_gain_is_read_for_a_character_played_long_ago_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    # The install's own file times, months before any test run.
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}], recent=False))
    assert [b for b in hub_tab._bodies(out["requests"]) if b["command"] == HISTORY] == []
    assert _card(out["steps"][0]["home"], _names(WARLOCK))["gain"] == ""


def test_a_failed_backup_puts_no_path_on_the_home_card_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Constructed: `snap create` refused by a read-only store, its message
    naming the store's folder. Backups shows it whole; the home card only
    says where to look."""
    scenario = _scenario(server, [{"home_click": "home-backup-now"}])
    where = "/Users/someone/Library/Application Support/wowlab/snapshots"
    scenario["runs"].append(
        {
            "command": ["snap", "create"],
            "params": {"flavor": BETA},
            "stdout": "",
            "exit_code": 1,
            "stderr": f"Error: cannot write to {where}: [Errno 30] Read-only file system",
        }
    )
    step = _drive(tmp_path / "node", scenario)["steps"][0]
    status = step["home"]["backup"]["status"]
    assert status == "The backup could not be taken. See Backups for details."
    assert "Read-only file system" in step["backupStatus"]
    assert "snapshots" in step["backupStatus"]


def test_restore_not_offered_puts_no_id_on_the_home_card_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Constructed: a command list without `snap restore`. Backups names
    the command and the backup's id; the home card names neither."""
    made = json.loads(backup_tab._created(server)["stdout"])["id"]
    scenario = _scenario(server, [{"home_click": "home-restore"}])
    commands = scenario["commands"]
    commands["commands"] = [c for c in commands["commands"] if c["path"] != ["snap", "restore"]]
    step = _drive(tmp_path / "node", scenario)["steps"][0]
    status = step["home"]["backup"]["status"]
    assert status == "Restoring is not offered on this page. See Backups for details."
    assert made in step["backupStatus"]
    assert hub_tab._bodies(step["newRequests"]) == []


def test_a_two_word_class_token_is_named_in_english_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Constructed (review round 2 on #566): with no name table cached, the
    warlock's row given each two-word class token, and a one-word token for
    comparison. Each card reads its English name, coloured by its class."""
    scenario = _scenario(server, [{"home": True}])
    report = _summary(scenario)
    [warlock] = [r for r in report["rows"] if r["label"] == _names(WARLOCK)]
    assert warlock["class_name"] is None
    tokens = {"DEATHKNIGHT": "Death Knight", "DEMONHUNTER": "Demon Hunter", "WARRIOR": "Warrior"}
    for at, token in enumerate(tokens):
        row = copy.deepcopy(warlock)
        row["label"] = f"Token{at}-Rizzen"
        row["class_file"] = token
        report["rows"].append(row)
    _edit_summary(scenario, report)
    view = _drive(tmp_path / "node", scenario)["steps"][0]["home"]
    for at, (token, name) in enumerate(tokens.items()):
        card = _card(view, f"Token{at}-Rizzen")
        assert card["who"].split(" · ")[0] == name, card["who"]
        assert f"cls-{token.lower()}" in card["cls"].split()
