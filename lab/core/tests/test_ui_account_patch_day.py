"""The Account tab's patch-day card (M17-05, docs/LAB_PLAN.md §19 CW-4):
per flavor, the addons `addons check` finds out of date for this client or
missing a required addon, the SavedVariables files `sv list --unreadable`
cannot read as data (M16-131), and those `sv list --orphans` finds that no
installed addon declares, each line opening the Explorer on its file; with
nothing found, "All N addons are current for this game version".

The page is driven under Node with the harness of `test_ui_account.py`,
fed what the real CLI printed through the real server. The install is that
module's two captures side by side (`constructed`, L8): the beta flavor is
`fixtures/macos/`, whose two DBM addons (out of date, `DBM-Core` missing)
and four orphaned addons' saved data are real (M16-34's grading set).
Constructed too, as labelled: report fields edited before the page reads
them (all addons current, a hostile addon name), and a run the scenario
does not hold. Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import test_ui_account as hub_tab
from test_ui_explorer import RLO

from wowlab_core import cli

both = hub_tab.both
home = hub_tab.home
server = hub_tab.server
no_env_flavor = hub_tab.no_env_flavor

BETA = hub_tab.BETA
FOREVER = hub_tab.FOREVER
CHECK = ["addons", "check"]
SV_LIST = ["sv", "list"]
ORPHANS = {"orphans": True}
UNREADABLE = {"unreadable": True}
ACCOUNT_SV = "WTF/Account/90000001#6/SavedVariables"
ORPHAN_FILES = [
    f"{ACCOUNT_SV}/DBM-Party-Vanilla.lua",
    f"{ACCOUNT_SV}/DBM-StatusBarTimers.lua",
    f"{ACCOUNT_SV}/RareScanner.lua",
    f"{ACCOUNT_SV}/RareScanner.lua.bak",
    f"{ACCOUNT_SV}/Syndicator.lua",
]

_VIEW = r"""
function patchDayView() {
  const blocks = ids["patch-day-lines"].children.map((li) => ({
    flavor: (li.children.find((e) => e.tagName === "h4") || { textContent: null }).textContent,
    what: li.all().filter((e) => has(e, "patch-day-what")).map((e) => e.textContent),
    items: li.all().filter((e) => has(e, "patch-day-item")).map((e) => {
      const b = e.children.find((c) => c.tagName === "button");
      return { name: b ? b.textContent : null, cls: b ? b.className : null, title: b ? b.title : null,
        text: e.textContent };
    }),
    clear: li.all().filter((e) => has(e, "patch-day-clear")).map((e) => e.textContent),
    notes: li.all().filter((e) => has(e, "patch-day-note")).map((e) => e.textContent),
    first: (li.children.find((e) => e.tagName !== "h4") || { textContent: null }).textContent,
    errors: li.all().filter((e) => has(e, "patch-day-error")).map((e) => e.textContent),
  }));
  return {
    blocks: blocks,
    status: ids["patch-day-status"].textContent,
    statusRole: ids["patch-day-status"].getAttribute("role"),
  };
}
"""

_STEPS = r"""
      } else if (step.patch_day !== undefined) {
        out.steps.push({ patchDay: patchDayView() });
      } else if (step.patch_click !== undefined) {
        const before = requests.length;
        const buttons = ids["patch-day-lines"].all().filter((e) => e.tagName === "button" && has(e, "patch-day-open"));
        const button = buttons[step.patch_click];
        if (!button) { out.steps.push({ missing: "patch-day " + step.patch_click }); continue; }
        button.fire("click"); await tick();
        const selected = treeEl().all().filter((e) => has(e, "row") && has(e, "selected")).map((e) => e.dataset.path);
        out.steps.push({ newRequests: requests.slice(before), accountHidden: ids["account-view"].hidden,
          explorerHidden: ids["explorer-view"].hidden, explainPath: ids["explain-path"].textContent,
          explorerStatus: ids["explorer-status"].textContent, selected: selected });
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


def _card_runs(server: Any, flavors: tuple[str, ...] = (BETA, FOREVER)) -> list[dict[str, Any]]:
    """`addons check`, `sv list --orphans` and `sv list --unreadable` of each
    flavor, as the real CLI printed them through the real server."""
    runs = []
    for flavor in flavors:
        runs.append(hub_tab._run_of(server, CHECK, {"flavor": flavor}))
        runs.append(hub_tab._run_of(server, SV_LIST, {"orphans": True, "flavor": flavor}))
        runs.append(hub_tab._run_of(server, SV_LIST, {"unreadable": True, "flavor": flavor}))
    for run in runs:
        assert run["exit_code"] == 0, run["stderr"]
    return runs


def _scenario(server: Any, steps: list[dict[str, Any]], *, tree: bool = False) -> dict[str, Any]:
    scenario = hub_tab._scenario(server, steps, tree=tree)
    scenario["runs"] += _card_runs(server)
    return scenario


def _run(
    scenario: dict[str, Any], command: list[str], flavor: str, option: dict[str, bool] = ORPHANS
) -> dict[str, Any]:
    """The run of `command` for `flavor`; of `sv list`, the one with `option`."""
    [run] = [
        r
        for r in scenario["runs"]
        if r["command"] == command
        and r["params"].get("flavor") == flavor
        and (command != SV_LIST or option.items() <= r["params"].items())
    ]
    return run


def _blocks(out: dict[str, Any]) -> dict[str, dict[str, Any]]:
    view = out["steps"][0]["patchDay"]
    return {b["flavor"]: b for b in view["blocks"]}


# ─── the card on the fixtures ────────────────────────────────────────────────


def test_the_page_holds_the_card_in_the_addons_section(server: Any) -> None:
    # M18-03: the card moved from the Account tab to the Addons section, as
    # it was; its intro went under About this data.
    body = hub_tab._page(server).body.decode("utf-8")
    view = body[body.index('<section id="addons-view"') : body.index('<section id="explorer-view"')]
    assert re.search(
        r'<div id="patch-day" class="panel patch-day" role="region" '
        r'aria-labelledby="patch-day-heading">',
        view,
    )
    assert '<h3 id="patch-day-heading">Patch day</h3>' in view
    assert '<p id="patch-day-status" role="status"></p>' in view
    assert '<h2 class="page-title">Addons</h2>' in view
    assert view.index('id="patch-day"') < view.index('<details class="about">')


def test_the_card_reads_each_flavor_after_the_hub_beside_the_tree_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"patch_day": True}]))
    runs = hub_tab._bodies(out["requests"])
    assert runs[0]["params"] == hub_tab.SUMMARY
    # The card's reads, one at a time in the flavors' order; the tree is
    # asked for as soon as the hub's report is read, waiting for none of them.
    assert [r for r in runs if r["command"] in (CHECK, SV_LIST)] == [
        {"command": CHECK, "params": {"flavor": BETA}},
        {"command": SV_LIST, "params": {"orphans": True, "flavor": BETA}},
        {"command": SV_LIST, "params": {"unreadable": True, "flavor": BETA}},
        {"command": CHECK, "params": {"flavor": FOREVER}},
        {"command": SV_LIST, "params": {"orphans": True, "flavor": FOREVER}},
        {"command": SV_LIST, "params": {"unreadable": True, "flavor": FOREVER}},
    ]
    assert ["tree"] in [r["command"] for r in runs]
    view = out["steps"][0]["patchDay"]
    assert view["status"] == "" and view["statusRole"] == "status"
    assert [b["flavor"] for b in view["blocks"]] == [BETA, FOREVER]


def test_the_two_dbm_addons_and_the_four_orphans_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"patch_day": True}])
    check = json.loads(_run(scenario, CHECK, BETA)["stdout"])["check"]
    assert check["client_interface"] == 16001
    listed = json.loads(_run(scenario, SV_LIST, BETA)["stdout"])
    assert [f["path"] for f in listed["files"]] == ORPHAN_FILES
    sizes = {f["path"]: f["size"] for f in listed["files"]}
    out = _drive(tmp_path / "node", scenario)
    beta = _blocks(out)[BETA]
    assert beta["errors"] == []
    assert beta["clear"] == ["0 of 2 addons are current for this game version."]
    assert beta["what"] == [
        "Made for another game version",
        "Needs an addon that will not load",
        "Saved data no installed addon declares",
    ]
    # No caveat for this result: the commands' notes are the standing ones.
    assert beta["notes"] == [
        'The game skips these unless "Load out of date AddOns" is ticked in its AddOns list '
        "(not yet confirmed).",
        "If the addon is installed again it reads this file again, so deleting it loses those "
        "settings.",
    ]
    assert beta["notes"][1] == cli._ORPHAN_REINSTALL
    names = [i["name"] for i in beta["items"]]
    dbm = ["DBM-Brawlers", "DBM-Challenges"]
    assert names == [*dbm, *dbm, *ORPHAN_FILES]
    texts = [i["text"] for i in beta["items"]]
    assert texts[:4] == [
        "DBM-Brawlers lists interface 120100, made for another game version; this game is 16001.",
        "DBM-Challenges lists interfaces 50504, 120100, made for another game version; this "
        "game is 16001.",
        "DBM-Brawlers needs DBM-Core, which is not installed.",
        "DBM-Challenges needs DBM-Core, which is not installed.",
    ]

    def size(path: str) -> str:
        n = sizes[path]
        return f"{n:,} byte" + ("" if n == 1 else "s")

    orphans = texts[4:]
    assert orphans[0] == (
        f"{ORPHAN_FILES[0]} ({size(ORPHAN_FILES[0])}): no addon named DBM-Party-Vanilla is "
        "installed."
    )
    assert orphans[3] == (
        f"{ORPHAN_FILES[3]} ({size(ORPHAN_FILES[3])}), the previous save: no addon named "
        "RareScanner is installed."
    )
    assert orphans[4].endswith(": no addon named Syndicator is installed.")
    for item in beta["items"]:
        assert item["cls"] == "patch-day-open"
        assert item["title"].startswith(f"Show {BETA}/") and item["title"].endswith(
            " in the Explorer"
        )
    assert beta["items"][0]["title"] == (
        f"Show {BETA}/Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc in the Explorer"
    )
    assert beta["items"][4]["title"] == f"Show {BETA}/{ORPHAN_FILES[0]} in the Explorer"


# ─── saved data wowlab could not read (M16-131) ──────────────────────────────

UNREAD_HEAD = "Saved data wowlab could not read"
# The CLI's sentences, split where the card says "(not yet confirmed)" once.
LOSS, COPY = cli._UNREADABLE_LOSS.split(" [verify]. ")
BACKUP = cli._UNREADABLE_BACKUP.removesuffix(" [verify].")
# Constructed (labelled): a file cut off inside its table, and one holding a
# function, written into the copy of the beta flavor's account folder.
BROKEN = {
    f"{ACCOUNT_SV}/Cutoff.lua": b'Cutoff = {\n  ["a"] = 1,\n',
    f"{ACCOUNT_SV}/Runs.lua": b"Runs = function() end\n",
}


def _write_broken(root: Path) -> None:
    for rel, data in BROKEN.items():
        (root / BETA / rel).write_bytes(data)


def test_the_real_saved_data_all_reads_so_there_is_no_such_list(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Every SavedVariables file of both captures reads as data (L8)."""
    scenario = _scenario(server, [{"patch_day": True}])
    for flavor in (BETA, FOREVER):
        report = json.loads(_run(scenario, SV_LIST, flavor, UNREADABLE)["stdout"])
        assert report["unreadable"] is True and report["orphans"] is False
        assert report["files"] == [] and report["read_failures"] == []
    plain = json.loads(hub_tab._run_of(server, SV_LIST, {"flavor": BETA})["stdout"])
    assert len(plain["files"]) >= 10, "the capture's files were all read"
    blocks = _blocks(_drive(tmp_path / "node", scenario))
    for block in blocks.values():
        assert UNREAD_HEAD not in block["what"] and block["errors"] == []


def test_files_wowlab_could_not_read_are_listed_with_why_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _write_broken(both)
    scenario = _scenario(server, [{"patch_day": True}])
    report = json.loads(_run(scenario, SV_LIST, BETA, UNREADABLE)["stdout"])
    assert [f["path"] for f in report["read_failures"]] == list(BROKEN)
    blocks = _blocks(_drive(tmp_path / "node", scenario))
    beta = blocks[BETA]
    assert beta["errors"] == []
    # After the addon lists, before the orphans.
    assert beta["what"] == [
        "Made for another game version",
        "Needs an addon that will not load",
        UNREAD_HEAD,
        "Saved data no installed addon declares",
    ]
    unread = [i for i in beta["items"] if i["name"] in BROKEN]
    assert [i["text"] for i in unread[:2]] == [
        f"{ACCOUNT_SV}/Cutoff.lua: the file ends before the table that opens at line 1, "
        "column 10 is closed: it may have been cut off by a crash or a full disk during a "
        "save.",
        f"{ACCOUNT_SV}/Runs.lua: it holds a function, which saved data never does (wowlab "
        "reads the file as data and never runs it), at line 1, column 8.",
    ]
    assert unread[0]["title"] == f"Show {BETA}/{ACCOUNT_SV}/Cutoff.lua in the Explorer"
    # "(not yet confirmed)" once on the card: the first list said it.
    assert beta["notes"][1] == LOSS + ". " + COPY
    every = [n for b in blocks.values() for n in b["notes"]]
    assert sum("(not yet confirmed)" in n for n in every) == 1


def test_the_unreadable_list_alone_says_not_yet_confirmed_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _write_broken(both)
    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, lambda report: None)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["what"] == [UNREAD_HEAD]
    assert beta["notes"] == [LOSS + " (not yet confirmed). " + COPY]
    assert beta["clear"] == ["All 2 addons are current for this game version."]


def test_a_broken_backup_alone_gives_no_loss_caveat_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Domain review of M16-131: the game does not load a `.lua.bak`, so a
    broken one alone says what it is and not the possible loss."""
    rel = f"{ACCOUNT_SV}/Cutoff.lua.bak"
    (both / BETA / rel).write_bytes(b'Cutoff = {\n  ["a"] = 1,\n')
    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, lambda report: None)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["what"] == [UNREAD_HEAD]
    assert beta["notes"] == []
    [line] = beta["items"]
    assert line["name"] == rel
    assert line["text"].endswith(". " + BACKUP + " (not yet confirmed).")


def test_a_file_past_a_bound_gets_its_own_line_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Constructed: the beta flavor's report edited to list one capture file
    as past a bound, as `sv list --unreadable` reports one."""
    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, lambda report: None)
    run = _run(scenario, SV_LIST, BETA, UNREADABLE)
    report = json.loads(run["stdout"])
    report["read_failures"] = [
        {
            "path": ORPHAN_FILES[0],
            "backup": False,
            "kind": "too_large",
            "reason": "it is larger than wowlab reads (over 256 MiB), so wowlab did not check it",
            "line": None,
            "column": None,
            "detail": "file is 300 bytes, over the 256-byte bound",
        }
    ]
    run["stdout"] = json.dumps(report)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["notes"] == [cli._UNREADABLE_TOO_LARGE]
    assert [i["text"] for i in beta["items"]] == [
        f"{ORPHAN_FILES[0]}: it is larger than wowlab reads (over 256 MiB), so wowlab did not "
        "check it."
    ]


def test_a_failed_unreadable_read_is_said_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"patch_day": True}])
    scenario["runs"].remove(_run(scenario, SV_LIST, FOREVER, UNREADABLE))
    forever = _blocks(_drive(tmp_path / "node", scenario))[FOREVER]
    assert forever["errors"] == [f"harness: no reply for {' '.join(SV_LIST)}"]
    assert UNREAD_HEAD not in forever["what"]


UNKNOWN = (
    "wowlab cannot tell which addons are out of date: it cannot work out this game's "
    "interface version."
)


def _edited(
    scenario: dict[str, Any],
    edit: Callable[[dict[str, Any]], None],
    files: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] = lambda f: [],
) -> None:
    """Constructed: the beta flavor's `addons check` report with every addon
    current (its TOC matching, nothing missing), then `edit` applied to the
    report; its orphans replaced by `files(orphans)`."""
    run = _run(scenario, CHECK, BETA)
    report = json.loads(run["stdout"])
    for addon in report["check"]["addons"]:
        addon["out_of_date"] = False
        addon["no_toc_for_client"] = False
        addon["missing_dependencies"] = []
        addon["unloadable_dependencies"] = []
        for toc in addon["tocs"]:
            toc["interface_status"] = "matches"
    edit(report)
    run["stdout"] = json.dumps(report)
    listed_run = _run(scenario, SV_LIST, BETA)
    listed = json.loads(listed_run["stdout"])
    listed["files"] = files(listed["files"])
    listed_run["stdout"] = json.dumps(listed)


def test_with_nothing_found_the_card_says_all_are_current_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, lambda report: None)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["items"] == [] and beta["what"] == [] and beta["errors"] == []
    assert beta["notes"] == []
    assert beta["clear"] == ["All 2 addons are current for this game version."]


def test_addons_that_cannot_be_checked_are_listed_and_counted_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """A folder with no TOC named after it, and a TOC that could not be read
    (each as `addoncheck.check_addons` reports one), are not current."""

    def edit(report: dict[str, Any]) -> None:
        first, second = report["check"]["addons"]
        first.update(selection="no_toc", tocs=[], counted_tocs=[])
        second["tocs"][0].update(interface_status="unread", error="not valid UTF-8")

    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, edit)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["clear"] == [
        "0 of 2 addons are current for this game version; 2 could not be checked."
    ]
    assert beta["what"] == ["Could not be checked"]
    assert [i["text"] for i in beta["items"]] == [
        "DBM-Brawlers has no TOC file named after its folder, so the game does not load it.",
        "DBM-Challenges has a TOC file wowlab could not read: not valid UTF-8.",
    ]
    assert beta["items"][0]["title"] == f"Show {BETA}/Interface/AddOns/DBM-Brawlers in the Explorer"


def test_a_toc_with_no_interface_line_is_not_current_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    def edit(report: dict[str, Any]) -> None:
        report["check"]["addons"][1]["tocs"][0].update(
            interface_status="none", interface=None, interface_versions=[]
        )

    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, edit)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["clear"] == [
        "1 of 2 addons are current for this game version; 1 could not be checked."
    ]
    assert [i["text"] for i in beta["items"]] == [
        "DBM-Challenges has no Interface line in its TOC file."
    ]


def test_an_older_interface_is_one_the_game_lists_as_out_of_date_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    def edit(report: dict[str, Any]) -> None:
        addon = report["check"]["addons"][0]
        addon["out_of_date"] = True
        addon["tocs"][0].update(
            interface_status="differs", interface="11507", interface_versions=[11507]
        )

    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, edit)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["clear"] == ["1 of 2 addons are current for this game version."]
    assert beta["what"] == ["Made for another game version"]
    assert [i["text"] for i in beta["items"]] == [
        "DBM-Brawlers lists interface 11507; this game is 16001, so the game lists it as out "
        "of date."
    ]


def test_not_yet_confirmed_is_said_once_per_card_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """Both flavors list an addon made for another game version (the other
    flavor's first addon marked so, constructed): the caveat under each list,
    "(not yet confirmed)" only on the first."""
    scenario = _scenario(server, [{"patch_day": True}])
    run = _run(scenario, CHECK, FOREVER)
    report = json.loads(run["stdout"])
    assert report["check"]["addons"], "the other flavor has addons"
    report["check"]["addons"][0]["out_of_date"] = True
    run["stdout"] = json.dumps(report)
    blocks = _blocks(_drive(tmp_path / "node", scenario))
    skips = 'The game skips these unless "Load out of date AddOns" is ticked in its AddOns list'
    assert blocks[BETA]["notes"][0] == skips + " (not yet confirmed)."
    assert blocks[FOREVER]["notes"][0] == skips + "."
    every = [n for b in blocks.values() for n in b["notes"]]
    assert sum("(not yet confirmed)" in n for n in every) == 1


def test_the_check_caveat_about_characters_is_not_shown_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    """`addons check`'s caveat about unreadable AddOns.txt files concerns
    the characters it lists, which the card does not show."""
    caveat = "1 AddOns.txt file could not be read, so the characters shown may be incomplete."

    def edit(report: dict[str, Any]) -> None:
        report["notes"] = [caveat, *report["notes"]]

    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, edit)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["notes"] == []
    assert beta["clear"] == ["All 2 addons are current for this game version."]


def test_an_unknown_interface_comes_first_even_beside_an_orphan_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    def edit(report: dict[str, Any]) -> None:
        report["check"]["client_interface"] = None
        report["notes"] = ["wowlab cannot tell this client's interface.", *report["notes"]]
        for addon in report["check"]["addons"]:
            for toc in addon["tocs"]:
                toc["interface_status"] = "unknown"

    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, edit, lambda files: files[:1])
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    # The unknown interface first, in place of the command's own caveat;
    # no count, and no addon is called current.
    assert beta["notes"][0] == UNKNOWN
    assert beta["first"] == UNKNOWN
    assert beta["clear"] == []
    assert beta["what"] == ["Saved data no installed addon declares"]
    assert [i["name"] for i in beta["items"]] == ORPHAN_FILES[:1]


def test_an_unknown_interface_with_nothing_else_is_not_called_current_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    def edit(report: dict[str, Any]) -> None:
        report["check"]["client_interface"] = None
        for addon in report["check"]["addons"]:
            for toc in addon["tocs"]:
                toc["interface_status"] = "unknown"

    scenario = _scenario(server, [{"patch_day": True}])
    _edited(scenario, edit)
    beta = _blocks(_drive(tmp_path / "node", scenario))[BETA]
    assert beta["notes"] == [UNKNOWN] and beta["clear"] == [] and beta["items"] == []


def test_an_orphan_of_an_installed_addon_says_what_it_does_not_declare_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"patch_day": True}])
    listed_run = _run(scenario, SV_LIST, BETA)
    listed = json.loads(listed_run["stdout"])
    # Constructed: the file named after an installed addon, in another case.
    listed["files"] = [dict(listed["files"][0], addon="dbm-brawlers", scope="character")]
    listed_run["stdout"] = json.dumps(listed)
    out = _drive(tmp_path / "node", scenario)
    [orphan] = [i for i in _blocks(out)[BETA]["items"] if i["name"] == ORPHAN_FILES[0]]
    assert orphan["text"].endswith(
        ": DBM-Brawlers is installed but does not declare per-character saved data."
    )


def test_a_failed_read_is_said_and_not_called_current_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"patch_day": True}])
    scenario["runs"].remove(_run(scenario, SV_LIST, FOREVER))
    out = _drive(tmp_path / "node", scenario)
    forever = _blocks(out)[FOREVER]
    assert forever["errors"] == [f"harness: no reply for {' '.join(SV_LIST)}"]
    # Nothing is said about saved data it could not read.
    assert "Saved data no installed addon declares" not in forever["what"]


def test_hostile_names_are_shown_not_applied_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"patch_day": True}])
    run = _run(scenario, CHECK, BETA)
    report = json.loads(run["stdout"])
    report["check"]["addons"][0]["name"] = "<b>DBM" + RLO
    report["check"]["addons"][0]["missing_dependencies"] = ["Core" + RLO]
    run["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node", scenario)
    items = _blocks(out)[BETA]["items"]
    assert items[0]["name"] == "<b>DBM⟨U+202E⟩"
    assert items[2]["text"] == "<b>DBM⟨U+202E⟩ needs Core⟨U+202E⟩, which is not installed."
    assert not any(RLO in i["text"] for i in items)


def test_the_flavor_filter_keeps_one_flavor_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    steps = [{"hub_filter": "flavor", "value": "f:" + FOREVER}, {"patch_day": True}]
    out = _drive(tmp_path / "node", _scenario(server, steps))
    assert out["steps"][0]["newRequests"] == [], "the filter reads nothing again"
    view = out["steps"][1]["patchDay"]
    assert [b["flavor"] for b in view["blocks"]] == [FOREVER]


# ─── a line opens the Explorer on its file ───────────────────────────────────


def test_a_line_opens_the_explorer_on_its_file_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    steps = [{"patch_click": 4}]
    out = _drive(tmp_path / "node", _scenario(server, steps, tree=True))
    [clicked] = out["steps"]
    key = f"{BETA}/{ORPHAN_FILES[0]}"
    assert clicked["accountHidden"] is True and clicked["explorerHidden"] is False
    assert clicked["explainPath"] == key
    assert clicked["selected"] == [key]
    [explain] = [r["body"] for r in clicked["newRequests"] if r["body"]["command"] == ["explain"]]
    assert explain["params"]["flavor"] == BETA
    assert Path(explain["params"]["path"]).parts[-2:] == ("SavedVariables", "DBM-Party-Vanilla.lua")
    # Nothing but the explain of the file.
    assert [r["body"]["command"] for r in clicked["newRequests"]] == [["explain"]]


def test_an_addon_line_opens_its_toc_constructed(server: Any, both: Path, tmp_path: Path) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"patch_click": 0}], tree=True))
    [clicked] = out["steps"]
    assert clicked["explainPath"] == f"{BETA}/Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc"


def test_the_card_section_runs_only_its_three_reads() -> None:
    script = (hub_tab.first_stage._TEMPLATES / "page.js").read_text(encoding="utf-8")
    start = script.index("// ─── the patch-day card on the Account tab")
    body = script[start : script.index("// ─── backups on the Account tab", start)]
    runs = re.findall(r"runJson\(([^)]*)\)", body)
    assert runs == [
        '["addons", "check"], { flavor: folder }, false, true',
        '["sv", "list"], { orphans: true, flavor: folder }, false, true',
        '["sv", "list"], { unreadable: true, flavor: folder }, false, true',
    ]
    # Reads only: no confirm token, no --yes, no HTML sink, no request of its own.
    # ("(not yet confirmed)" is player copy, not the confirm step.)
    for word in (r"\bconfirm\b", r"\byes\b", "innerHTML", "setAttribute", r"fetch\("):
        assert not re.search(word, body), word
    # The hub starts the card and waits for none of it, as for its backups;
    # a failure in it is the card's own (the hub-filter crash, 2026-11-13).
    assert "alone(patchDaySay, () => takePatchDay(report));" in script
    assert "await takePatchDay" not in script
    # The orphan list's sentence is the CLI's own; so is the unreadable
    # list's, its unconfirmed part said as the card says it.
    assert json.dumps(cli._ORPHAN_REINSTALL) in body
    for name, text in (
        ("LOSS", LOSS),
        ("COPY", COPY),
        ("TOO_LARGE", cli._UNREADABLE_TOO_LARGE),
        ("BACKUP", BACKUP),
    ):
        assert f"const PATCH_DAY_UNREAD_{name} =\n    {json.dumps(text)};" in body, name
