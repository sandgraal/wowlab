"""The Account tab of `wowlab ui`: the account hub, the home tab (M17-01,
docs/LAB_PLAN.md §19 CW-1, §14.4).

The page is driven under Node with the stand-in DOM of `test_ui_explorer.py`
and the Character tab's steps of `test_ui_character.py`, fed what the real
CLI printed through the real server. The install is the two captures of
`test_cli_char_summary.py` side by side (`fixtures/macos/` as
`_classic_beta_`, schema-1 records and a folder with no record;
`fixtures/macos-70888/` as `_forever_`, the launch client's schema-4
record), under a stand-in home folder: that combination is `constructed`
(L8), as is every test labelled so (a hostile folder name, an exit 1).
Nothing reads or writes a real install.

The harness gains steps for this tab (look at it, sort by a column, filter,
open a row, copy the fix command) and a view of it. What the page may hold
is checked by reading the templates.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import test_cli_char_summary as hub_cli
import test_ui_character as char_tab
import test_ui_explorer as first_stage
from test_ui_explorer import IMG, RLO, _page, _page_ids, _send, _stdout

home = first_stage.home
server = first_stage.server

BETA = hub_cli.BETA
FOREVER = hub_cli.FOREVER
FIRST = hub_cli.FIRST
SECOND = hub_cli.SECOND
BARE = hub_cli.BARE
WARLOCK = hub_cli.WARLOCK
ACCOUNT = hub_cli.ACCOUNT
LAUNCH_ACCOUNT = hub_cli.LAUNCH_ACCOUNT
SUMMARY = {"summary": True}
BRIEF = {"brief": True}
PICK_READS = char_tab.PICK_READS
COLUMNS = [
    "Character",
    "Realm folder",
    "Flavor",
    "Level",
    "Class",
    "Race",
    "Spec",
    "Item level",
    "Professions",
    "Last logout or /reload",
]
# Fixed file times: the warlock saved last, then FIRST, then SECOND.
TIMES = {WARLOCK: 1_770_000_000_000_000_000, FIRST: 1_760_000_000_000_000_000}
TIMES[SECOND] = 1_750_000_000_000_000_000

_VIEW = r"""
function hubView() {
  const box = ids["hub-table"];
  const trs = box.all().filter((e) => e.tagName === "tr" && has(e, "hub-row"));
  const options = (id) => Array.from(ids[id].options, (o) => ({ value: o.value, text: o.textContent }));
  const active = documentStub.activeElement;
  return {
    hidden: ids["account-view"].hidden,
    tabSelected: ids["tab-account"].getAttribute("aria-selected"),
    status: ids["hub-status"].textContent,
    statusRole: ids["hub-status"].getAttribute("role"),
    flavorBoxHidden: ids["hub-flavor-box"].hidden,
    flavorOptions: options("hub-flavor"),
    classOptions: options("hub-class"),
    caption: (box.all().find((e) => e.tagName === "caption") || { textContent: null }).textContent,
    heads: box.all().filter((e) => e.tagName === "th").map((th) => ({
      text: th.textContent, sort: th.ariaSort === undefined ? null : th.ariaSort, scope: th.scope,
      buttons: th.children.filter((c) => c.tagName === "button").length })),
    rows: trs.map((tr) => ({
      bare: has(tr, "hub-row-bare"),
      cells: tr.children.map((c) => c.textContent),
      titles: tr.children.map((c) => c.title),
      spans: tr.children.map((c) => c.colSpan || 1),
      open: tr.all().filter((e) => e.tagName === "button" && has(e, "hub-open")).length,
      code: tr.all().filter((e) => e.tagName === "code").map((e) => e.textContent) })),
    active: active ? (active.id || active.className) : null,
    activeText: active ? active.textContent : null,
  };
}
"""

_STEPS = r"""
      } else if (step.hub !== undefined) {
        out.steps.push({ hub: hubView() });
      } else if (step.hub_sort !== undefined) {
        const before = requests.length;
        const buttons = ids["hub-table"].all().filter((e) => e.tagName === "button" && has(e, "hub-sort"));
        buttons[step.hub_sort].fire("click"); await tick();
        out.steps.push({ hub: hubView(), newRequests: requests.slice(before) });
      } else if (step.hub_filter !== undefined) {
        const before = requests.length;
        const picker = ids["hub-" + step.hub_filter];
        picker.value = step.value; picker.fire("change"); await tick();
        out.steps.push({ hub: hubView(), newRequests: requests.slice(before) });
      } else if (step.hub_open !== undefined) {
        const before = requests.length;
        const trs = ids["hub-table"].all().filter((e) => e.tagName === "tr" && has(e, "hub-row"));
        const tr = trs[step.hub_open];
        const target = step.on_row ? tr : tr.all().find((e) => e.tagName === "button" && has(e, "hub-open"));
        if (!target) { out.steps.push({ missing: "open " + step.hub_open }); continue; }
        target.fire("click"); await tick();
        out.steps.push({ hub: hubView(), char: charView(), picker: ids["char-picker"].value,
          runFlavor: ids["run-flavor"].value, newRequests: requests.slice(before) });
      } else if (step.hub_copy !== undefined) {
        const before = requests.length;
        const buttons = ids["hub-table"].all().filter((e) => e.tagName === "button" && has(e, "copy"));
        buttons[step.hub_copy].fire("click"); await tick();
        out.steps.push({ clipboard: clipboard.slice(), newRequests: requests.slice(before) });
"""

_ANCHOR = "      } else if (step.preview !== undefined) {\n"
_VIEW_ANCHOR = "vm.createContext(ctx);\n"
_CHAR_HARNESS = char_tab._harness


def _harness() -> str:
    text = _CHAR_HARNESS()
    assert text.count(_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    text = text.replace(_ANCHOR, _STEPS.lstrip("\n") + _ANCHOR, 1)
    return text.replace(
        "out.first = snap();", "out.first = Object.assign(snap(), { hub: hubView() });", 1
    )


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = char_tab._harness
    char_tab._harness = _harness
    try:
        return char_tab._drive(folder, scenario)
    finally:
        char_tab._harness = original


@pytest.fixture(autouse=True)
def no_env_flavor(monkeypatch: pytest.MonkeyPatch) -> None:
    from wowlab_core import install as discovery

    monkeypatch.delenv(discovery.ENV_FLAVOR, raising=False)


def _lab(root: Path, flavor: str, account: str, character: str) -> Path:
    return root / flavor / "WTF" / "Account" / account / character / "SavedVariables" / "WowLab.lua"


@pytest.fixture
def both(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from wowlab_core import install as discovery

    root = hub_cli.two_flavors(home / "Games" / "World of Warcraft")
    for character, account, flavor in (
        (WARLOCK, LAUNCH_ACCOUNT, FOREVER),
        (FIRST, ACCOUNT, BETA),
        (SECOND, ACCOUNT, BETA),
    ):
        ns = TIMES[character]
        os.utime(_lab(root, flavor, account, character), ns=(ns, ns))
    monkeypatch.setenv(discovery.ENV_ROOT, str(root))
    return root


def _run_of(server: Any, command: list[str], params: dict[str, Any]) -> dict[str, Any]:
    reply = first_stage._run(server, command, params).json()
    return {
        "command": command,
        "params": params,
        "stdout": reply["stdout"],
        "exit_code": reply["exit_code"],
        "stderr": reply["stderr"],
    }


def _scenario(
    server: Any,
    steps: list[dict[str, Any]],
    picks: tuple[tuple[str, str, str], ...] = (),
    *,
    tree: bool = False,
) -> dict[str, Any]:
    """The command list, the whole install's tree, `char list --summary`,
    `char list --brief` with no flavor and with each, and the three reads of
    each (character, account, flavor) in `picks`, as the real CLI printed
    them through the real server. Without `tree` the explorer's tree is not
    held (its run says so, and the header's flavor picker stays hidden): the
    hub does not need it, and reading it is the slow part."""
    runs = [_run_of(server, ["char", "list"], SUMMARY)]
    for flavor in (None, BETA, FOREVER):
        params = dict(BRIEF) if flavor is None else dict(BRIEF, flavor=flavor)
        runs.append(_run_of(server, ["char", "list"], params))
    for character, account, flavor in picks:
        params = {"character": character, "account": account, "flavor": flavor}
        for command in PICK_READS:
            runs.append(_run_of(server, list(command), params))
    return {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": _send(server, "GET", "/api/commands").json(),
        "first_flavor": BETA,
        "trees": {},
        "install_tree": (
            _stdout(server, ["tree"], {"whole_install": True, "explain": True}) if tree else None
        ),
        "explains": {},
        "runs": runs,
        "held": [],
        "steps": steps,
    }


def _summary_run(scenario: dict[str, Any]) -> dict[str, Any]:
    run: dict[str, Any] = scenario["runs"][0]
    assert run["params"] == SUMMARY
    return run


def _cells(row: dict[str, Any]) -> dict[str, str]:
    return dict(zip(COLUMNS, row["cells"], strict=False))


def _bodies(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r["body"] for r in requests if r["body"] is not None]


# ─── the page ────────────────────────────────────────────────────────────────


def test_account_is_the_first_tab_and_opens_by_default(server: Any) -> None:
    body = _page(server).body.decode("utf-8")
    tabs = re.findall(r'<button id="tab-([a-z]+)" class="tab"[^>]*role="tab"', body)
    # Explorer moves after Character.
    assert tabs == ["account", "character", "explorer", "gaps", "commands", "looks"]
    assert re.search(r'<button id="tab-account"[^>]*aria-selected="true"', body)
    selected = re.findall(r'<button id="tab-([a-z]+)"[^>]*aria-selected="true"', body)
    assert selected == ["account"]
    assert re.search(r'<section id="account-view"[^>]*role="tabpanel"', body)
    assert not re.search(r'<section id="account-view"[^>]*\bhidden\b', body)
    for view in ("explorer", "gaps", "commands", "character", "looks"):
        assert re.search(rf'<section id="{view}-view"[^>]*\bhidden\b', body), view
    # The table scrolls in its own labelled, focusable box (WCAG 1.4.10), and
    # the panel starts at an H2; the status line is a status.
    assert re.search(
        r'<div id="hub-table"[^>]*role="region"[^>]*aria-label="Characters"[^>]*tabindex="0"', body
    )
    view = body[
        body.index('<section id="account-view"') : body.index('<section id="explorer-view"')
    ]
    assert '<h2 class="unseen">Account</h2>' in view
    assert '<p id="hub-status" role="status"></p>' in view
    assert "<label>Class <select" in view and ">Flavor <select" in view


def test_the_hub_reads_every_flavor_at_the_start_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"hub": True}]))
    runs = _bodies(out["requests"])
    # The home tab's one run comes first, with no flavor, before the tree.
    assert runs[0] == {"command": ["char", "list"], "params": SUMMARY}
    # M17-05: the patch-day card's reads start from the hub's report and run
    # beside the tree, which waits for none of them.
    card = (["addons", "check"], ["sv", "list"])
    others = [r for r in runs[1:] if r["command"] not in card]
    assert others[0] == {"command": ["tree"], "params": {"whole_install": True, "explain": True}}
    assert sum(1 for r in runs if r["params"] == SUMMARY) == 1
    view = out["first"]["hub"]
    # page.html selects the tab and shows its view; the script keeps it so.
    assert view["hidden"] is False and out["first"]["explorerHidden"] is True
    assert view["status"] == "4 character folders." and view["statusRole"] == "status"
    assert [h["text"] for h in view["heads"]] == [
        *COLUMNS[:-1],
        COLUMNS[-1] + " ▼",
    ]
    assert all(h["buttons"] == 1 and h["scope"] == "col" for h in view["heads"])
    assert [h["sort"] for h in view["heads"]] == [None] * 9 + ["descending"]
    assert view["caption"] == "Characters, sorted by last logout or /reload, descending"
    # Latest saved first; the folder with no record (no time) last.
    names = [r["cells"][0] for r in view["rows"]]
    assert names == [p.split("/")[1] for p in (WARLOCK, FIRST, SECOND, BARE)]


def test_each_row_shows_what_the_record_holds_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [])
    report = json.loads(_summary_run(scenario)["stdout"])
    out = _drive(tmp_path / "node", scenario)
    rows = out["first"]["hub"]["rows"]
    warlock, first, second, _bare = rows
    assert _cells(warlock) == {
        "Character": "Sandgraal-Rizzen",
        "Realm folder": "1",
        "Flavor": FOREVER,
        "Level": "13",
        "Class": "WARLOCK",
        "Race": "race 5",
        "Spec": "spec 1490",
        "Item level": "8.2",
        "Professions": "skill line 197 21/75, skill line 393 90/150, skill line 129 37/75, "
        "skill line 356 1/75, skill line 185 17/75",
        "Last logout or /reload": char_tab._local(TIMES[WARLOCK]),
    }
    assert warlock["open"] == 1 and not warlock["bare"]
    assert warlock["titles"][7] == "equipped average, rounded to one decimal; exact: 8.1875"
    # Schema 1: no class and no race. The cell says "not recorded", never a
    # blank or a zero, and its title is the report's reason.
    cells = _cells(first)
    assert (cells["Class"], cells["Race"]) == ("not recorded", "not recorded")
    reason = next(r for r in report["rows"] if r["label"] == FIRST.split("/")[1])["class_absent"]
    assert first["titles"][4] == first["titles"][5] == reason
    assert (cells["Level"], cells["Spec"], cells["Item level"]) == ("13", "spec 1490", "3.3")
    assert _cells(second)["Level"] == "10"
    for row in (warlock, first, second):
        assert "" not in row["cells"] and "0" not in row["cells"]


def test_a_folder_with_no_record_gives_the_one_action_that_fixes_it_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"hub_copy": 0}])
    out = _drive(tmp_path / "node", scenario)
    bare = out["first"]["hub"]["rows"][3]
    assert bare["bare"] and bare["open"] == 0
    assert bare["cells"][:3] == [BARE.split("/")[1], "1", BETA]
    assert bare["spans"] == [1, 1, 1, len(COLUMNS) - 3]
    assert bare["code"] == ["wowlab addon install lab"]
    assert bare["cells"][3].startswith(
        "No lab record: run `wowlab addon install lab`, then log in on this character"
    )
    copied = out["steps"][0]
    assert copied["clipboard"] == ["wowlab addon install lab"]
    assert copied["newRequests"] == [], "copying runs nothing"


def test_sorting_by_each_column_keeps_not_recorded_last_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    steps = [{"hub_sort": 3}, {"hub_sort": 3}, {"hub_sort": 4}, {"hub_sort": 0}]
    out = _drive(tmp_path / "node", _scenario(server, steps))
    by_level, by_level_down, by_class, by_name = (s["hub"] for s in out["steps"])
    names = [r["cells"][0] for r in by_name["rows"]]
    level_up = [r["cells"][3] for r in by_level["rows"]]
    assert level_up[:3] == ["10", "13", "13"] and by_level["rows"][3]["bare"]
    assert [r["cells"][3] for r in by_level_down["rows"]][:3] == ["13", "13", "10"]
    assert by_level_down["rows"][3]["bare"], "not recorded sorts last either way"
    assert [h["sort"] for h in by_level["heads"]][3] == "ascending"
    assert [h["sort"] for h in by_level_down["heads"]][3] == "descending"
    assert sum(1 for h in by_level["heads"] if h["sort"]) == 1
    assert by_level["caption"] == "Characters, sorted by level, ascending"
    # Focus stays on the sort button that was used, redrawn.
    assert by_level["active"] == "hub-sort" and by_level["activeText"].startswith("Level")
    assert by_class["rows"][0]["cells"][4] == "WARLOCK"
    assert names == sorted(names, key=str.lower)
    # Sorting runs nothing.
    assert all(s["newRequests"] == [] for s in out["steps"])


def test_filtering_by_flavor_and_class_constructed(server: Any, both: Path, tmp_path: Path) -> None:
    steps = [
        {"hub_filter": "flavor", "value": "f:" + FOREVER},
        {"hub_filter": "flavor", "value": ""},
        {"hub_filter": "class", "value": "c:WARLOCK"},
        {"hub_filter": "class", "value": "none"},
    ]
    out = _drive(tmp_path / "node", _scenario(server, steps))
    first = out["first"]["hub"]
    assert first["flavorBoxHidden"] is False
    assert first["flavorOptions"] == [
        {"value": "", "text": "All"},
        {"value": "f:" + BETA, "text": BETA},
        {"value": "f:" + FOREVER, "text": FOREVER},
    ]
    assert first["classOptions"] == [
        {"value": "", "text": "All"},
        {"value": "c:WARLOCK", "text": "WARLOCK"},
        {"value": "none", "text": "not recorded"},
    ]
    forever, every, warlocks, unknown = (s["hub"] for s in out["steps"])
    assert [r["cells"][0] for r in forever["rows"]] == ["Sandgraal-Rizzen"]
    assert forever["status"] == "Showing 1 of 4 character folders."
    assert len(every["rows"]) == 4 and every["status"] == "4 character folders."
    assert [r["cells"][0] for r in warlocks["rows"]] == ["Sandgraal-Rizzen"]
    assert {r["cells"][0] for r in unknown["rows"]} == {
        p.split("/")[1] for p in (FIRST, SECOND, BARE)
    }
    assert all(s["newRequests"] == [] for s in out["steps"])


def test_one_flavor_hides_the_flavor_filter(
    server: Any, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wowlab_core import install as discovery

    root = first_stage._copy_install(home / "Games" / "World of Warcraft")
    monkeypatch.setenv(discovery.ENV_ROOT, str(root))
    scenario = _scenario(server, [])
    out = _drive(tmp_path / "node", scenario)
    view = out["first"]["hub"]
    assert view["flavorBoxHidden"] is True
    assert len(view["rows"]) == 3


def test_opening_a_row_opens_the_character_tab_on_it_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """The header's flavor starts on the first flavor; opening the launch
    flavor's character moves it there, and the picker takes that character.
    Opening a beta character by a click on its row moves it back."""
    picks = ((WARLOCK, LAUNCH_ACCOUNT, FOREVER), (FIRST, ACCOUNT, BETA))
    steps = [{"hub_open": 0}, {"tab": "account"}, {"hub_open": 1, "on_row": True}]
    out = _drive(tmp_path / "node", _scenario(server, steps, picks, tree=True))
    opened, _, again = out["steps"]
    assert opened["char"]["hidden"] is False and opened["char"]["tabSelected"] == "true"
    assert opened["hub"]["hidden"] is True and opened["hub"]["tabSelected"] == "false"
    assert opened["runFlavor"] == FOREVER
    assert _bodies(opened["newRequests"]) == [
        {"command": ["char", "list"], "params": dict(BRIEF, flavor=FOREVER)},
        *[
            {
                "command": list(c),
                "params": {"character": WARLOCK, "account": LAUNCH_ACCOUNT, "flavor": FOREVER},
            }
            for c in PICK_READS
        ],
    ]
    assert opened["char"]["options"][int(opened["picker"])]["text"].startswith("Sandgraal-Rizzen")
    # Focus goes to the picker, which names the character.
    assert opened["hub"]["active"] == "char-picker"
    assert again["runFlavor"] == BETA
    assert _bodies(again["newRequests"]) == [
        {"command": ["char", "list"], "params": dict(BRIEF, flavor=BETA)},
        *[
            {"command": list(c), "params": {"character": FIRST, "account": ACCOUNT, "flavor": BETA}}
            for c in PICK_READS
        ],
    ]
    assert again["char"]["options"][int(again["picker"])]["text"].startswith("Labchard-Labrealmg")
    # The hub was read once, whatever was opened.
    assert sum(1 for r in _bodies(out["requests"]) if r["params"] == SUMMARY) == 1


def test_a_run_that_exits_1_shows_its_rows_and_says_why_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _lab(both, BETA, ACCOUNT, SECOND).write_bytes(b"WowLabCharDB = {\n")
    scenario = _scenario(server, [])
    run = _summary_run(scenario)
    assert run["exit_code"] == 1 and run["stderr"]
    out = _drive(tmp_path / "node", scenario)
    view = out["first"]["hub"]
    assert len(view["rows"]) == 4
    unread = next(r for r in view["rows"] if r["cells"][0] == SECOND.split("/")[1])
    assert unread["bare"] and unread["open"] == 0
    assert unread["cells"][3].startswith("Not read: ")
    assert view["status"].startswith("4 character folders. ")
    assert view["statusRole"] == "alert"


def test_hostile_folder_names_draw_as_inert_text_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"hub_open": 0}])
    run = _summary_run(scenario)
    report = json.loads(run["stdout"])
    warlock = next(r for r in report["rows"] if r["label"] == WARLOCK.split("/")[1])
    warlock["label"] = IMG
    warlock["realm_folder"] = "1" + RLO
    warlock["class_name"] = "<b>Mage</b>"
    run["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node", scenario)
    row = out["first"]["hub"]["rows"][0]
    assert row["cells"][0] == IMG
    assert row["cells"][1] == "1⟨U+202E⟩"
    assert row["cells"][4] == "<b>Mage</b>"


def test_the_tab_runs_only_char_list_and_holds_no_rules() -> None:
    script = (first_stage._TEMPLATES / "page.js").read_text(encoding="utf-8")
    body = script[script.index("// ─── the account hub") : script.index("// ─── tabs ───")]
    runs = re.findall(r"runJson\(([^)]*)\)", body)
    assert runs == ['["char", "list"], { summary: true }, true, true']
    # The advice and the command are the report's, never the page's.
    assert "addon install" not in body
    assert "innerHTML" not in body and "setAttribute" not in body


def test_a_row_in_a_flavor_the_header_cannot_move_to_opens_no_other_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Code review of M17-01: the beta character's row is made to name a
    flavor the tree does not list (constructed), so the header stays on the
    beta flavor, whose list holds the same folders under another flavor.
    The tab says the character is not in its list, and no character's
    record is read in its place."""
    scenario = _scenario(server, [{"hub_open": 1}], ((FIRST, ACCOUNT, BETA),), tree=True)
    run = _summary_run(scenario)
    report = json.loads(run["stdout"])
    gone = "_gone_"
    first = next(r for r in report["rows"] if r["label"] == FIRST.split("/")[1])
    first["flavor"] = gone
    run["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node", scenario)
    [opened] = out["steps"]
    assert opened["runFlavor"] == BETA
    assert opened["char"]["hidden"] is False
    assert opened["char"]["status"] == (
        f"{FIRST.split('/')[1]} ({gone}) is not in this tab's list of characters, so it was not "
        "opened: the tab is reading another flavor."
    )
    assert opened["char"]["statusError"] is True
    # Only the tab's list was read: no gear, keys or macros of anyone.
    assert _bodies(opened["newRequests"]) == [
        {"command": ["char", "list"], "params": dict(BRIEF, flavor=BETA)}
    ]
    assert opened["char"]["header"] == "" and opened["char"]["rows"] == []
