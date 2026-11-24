"""The Addons view's "Where they came from" panel (M22-16): per flavor, what
`addons list --json` joins from WowUp's records, read the first time the
Addons view is shown.

The page is driven under Node with the harness of `test_ui_account.py`,
fed what the real CLI printed through the real server. The install is that
module's two captures side by side and the WowUp list is the owner's real
one (`fixtures/wowup/macos/addons.json`), put where WowUp keeps it in the
stand-in home; the pairing is `constructed` (L8), as is every test
labelled so (report fields edited before the page reads them, a run that
fails, no list at all). Nothing reads or writes a real install or a real
WowUp folder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import test_ui_account as hub_tab
from test_ui_explorer import RLO

from wowlab_core import addonsource

both = hub_tab.both
home = hub_tab.home
server = hub_tab.server
no_env_flavor = hub_tab.no_env_flavor

BETA = hub_tab.BETA
FOREVER = hub_tab.FOREVER
LIST = ["addons", "list"]
WOWUP = Path(__file__).resolve().parent / "fixtures" / "wowup" / "macos" / "addons.json"
PREFERENCES = WOWUP.with_name("preferences.json")
LOCATION = (
    b"/Volumes/Labdisk/Games/World of Warcraft/_classic_beta_/World of Warcraft Classic Beta.app"
)
WRITTEN_NS = 1_790_000_000_000_000_000

_VIEW = r"""
function sourcesView() {
  const blocks = ids["sources-lines"].children.map((li) => ({
    flavor: (li.children.find((e) => e.tagName === "h4") || { textContent: null }).textContent,
    what: li.all().filter((e) => has(e, "sources-what")).map((e) => e.textContent),
    items: li.all().filter((e) => has(e, "sources-item")).map((e) => e.textContent),
    clear: li.all().filter((e) => has(e, "sources-clear")).map((e) => e.textContent),
    loose: li.all().filter((e) => has(e, "sources-loose")).map((e) => e.textContent),
    notes: li.all().filter((e) => has(e, "sources-note")).map((e) => e.textContent),
    errors: li.all().filter((e) => has(e, "sources-error")).map((e) => e.textContent),
  }));
  return {
    blocks: blocks,
    status: ids["sources-status"].textContent,
    statusRole: ids["sources-status"].getAttribute("role"),
    addonsHidden: ids["addons-view"].hidden,
  };
}
"""

_STEPS = r"""
      } else if (step.sources_open !== undefined) {
        const before = requests.length;
        ids["sec-addons"].fire("click");
        for (let n = 0; n < 5; n++) await tick();
        out.steps.push({ sources: sourcesView(), newRequests: requests.slice(before) });
      } else if (step.sources !== undefined) {
        out.steps.push({ sources: sourcesView() });
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


def _folder() -> Path:
    folder = addonsource.wowup_folder()
    tmp = Path(os.environ["HOME"]).parent
    assert tmp in folder.parents, f"{folder} is not under this test's tmp_path"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _wowup(body: bytes | None = None) -> None:
    path = _folder() / "addons.json"
    if body is None:
        shutil.copyfile(WOWUP, path)
    else:
        path.write_bytes(body)
    os.utime(path, ns=(WRITTEN_NS, WRITTEN_NS))


def _settings(flavor: Path | None = None) -> None:
    """WowUp's settings: the real file, or with its one location moved into
    `flavor` (`constructed`)."""
    data = PREFERENCES.read_bytes()
    if flavor is not None:
        assert data.count(LOCATION) == 1
        moved = json.dumps(str(flavor / "World of Warcraft Classic Beta.app"))[1:-1]
        data = data.replace(LOCATION, moved.encode("utf-8"))
    (_folder() / "preferences.json").write_bytes(data)


def _scenario(server: Any, steps: list[dict[str, Any]]) -> dict[str, Any]:
    scenario = hub_tab._scenario(server, steps)
    for flavor in (BETA, FOREVER):
        run = hub_tab._run_of(server, LIST, {"flavor": flavor})
        assert run["exit_code"] == 0, run["stderr"]
        scenario["runs"].append(run)
    return scenario


def _list_run(scenario: dict[str, Any], flavor: str) -> dict[str, Any]:
    [run] = [
        r for r in scenario["runs"] if r["command"] == LIST and r["params"] == {"flavor": flavor}
    ]
    return run


def _blocks(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {b["flavor"]: b for b in view["blocks"]}


# ─── the panel ───────────────────────────────────────────────────────────────


def test_the_page_holds_the_panel_in_the_addons_section(server: Any) -> None:
    body = hub_tab._page(server).body.decode("utf-8")
    view = body[body.index('<section id="addons-view"') : body.index('<section id="explorer-view"')]
    assert (
        '<div id="sources" class="panel sources" role="region" aria-labelledby="sources-heading">'
        in view
    )
    assert '<h3 id="sources-heading">Where they came from</h3>' in view
    assert '<p id="sources-status" role="status"></p>' in view
    assert view.index('id="patch-day"') < view.index('id="sources"') < view.index('class="about"')
    assert "<code>wowlab addons list</code>" in view


def test_it_reads_each_flavor_once_when_the_addons_view_is_first_shown_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _wowup()
    steps = [{"sources": True}, {"sources_open": True}, {"sources_open": True}]
    out = _drive(tmp_path / "node", _scenario(server, steps))
    before, shown, again = out["steps"]
    # Nothing is read for it until the view is shown.
    assert before["sources"]["blocks"] == []
    assert shown["sources"]["addonsHidden"] is False
    reads = [
        {"command": LIST, "params": {"flavor": BETA}},
        {"command": LIST, "params": {"flavor": FOREVER}},
    ]
    # The page's only two reads of it, both made after the view was shown.
    assert [r for r in hub_tab._bodies(out["requests"]) if r["command"] == LIST] == reads
    assert [r for r in hub_tab._bodies(shown["newRequests"]) if r["command"] == LIST] == reads
    assert shown["sources"]["status"] == "" and shown["sources"]["statusRole"] == "status"
    assert [b["flavor"] for b in shown["sources"]["blocks"]] == [BETA, FOREVER]
    # Shown again, nothing is read again.
    assert [r for r in hub_tab._bodies(again["newRequests"]) if r["command"] == LIST] == []


def test_the_records_the_stale_ones_and_the_folders_no_one_claims_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    # WowUp's one game folder moved to the beta's copy: the beta's folders
    # are its own, the launch client's are another game folder's.
    _wowup()
    _settings(both / BETA)
    scenario = _scenario(server, [{"sources_open": True}])
    out = _drive(tmp_path / "node", scenario)
    blocks = _blocks(out["steps"][0]["sources"])
    beta = blocks[BETA]
    assert beta["errors"] == []
    assert len(beta["clear"]) == 1
    assert re.fullmatch(
        r"WowUp's list, last written \d{4}-\d\d-\d\d \d\d:\d\d:\d\d: 6 addons\.", beta["clear"][0]
    )
    assert beta["what"] == ["In WowUp's list", "In WowUp's list, with none of their folders here"]
    dbm, dungeons, *away = beta["items"]
    assert dbm.startswith(
        "Deadly Boss Mods (DBM): from Wago, WowUp installed 12.1.11, WowUp's record is out of "
        "date (not here: DBM-Core, DBM-GUI,"
    )
    assert dbm.endswith(" Folders: DBM-Brawlers.")
    assert "the TOC" not in dungeons  # r264 agrees with DBM-Dungeons-r264
    assert dungeons.endswith(" Folders: DBM-Challenges.")
    assert [a.split(":")[0] for a in away] == ["RareScanner", "Pawn", "Baganator", "Syndicator"]
    assert beta["loose"] == []
    assert beta["notes"] == [
        "An out-of-date record means the addon changed after WowUp last wrote its list, by "
        "hand or by another app."
    ]
    forever = blocks[FOREVER]
    assert forever["items"] == [] and forever["loose"] == [] and forever["notes"] == []
    hinted = [
        f
        for f in json.loads(_list_run(scenario, FOREVER)["stdout"])["sources"]["folders"]
        if f["same_name_as"]
    ]
    assert forever["clear"][0] == (
        "WowUp does not manage this game folder; its list is for ~/Games/World of Warcraft/"
        f"{BETA} (6 addons), and {len(hinted)} folder{'' if len(hinted) == 1 else 's'} here "
        f"{'shares' if len(hinted) == 1 else 'share'} a name with one of them."
    )


def test_a_stale_record_and_folders_no_one_claims_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _wowup()
    _settings(both / FOREVER)
    scenario = _scenario(server, [{"sources_open": True}])
    out = _drive(tmp_path / "node", scenario)
    forever = _blocks(out["steps"][0]["sources"])[FOREVER]
    [baganator] = [i for i in forever["items"] if i.startswith("Baganator:")]
    assert baganator == (
        "Baganator: from Wago, WowUp installed 829, WowUp's record is out of date "
        "(the TOC here says 850). Folders: Baganator."
    )
    listed = json.loads(_list_run(scenario, FOREVER)["stdout"])["sources"]["folders"]
    loose = [f["folder"] for f in listed if f["unclaimed"] is True]
    assert "Auctionator" in loose and "BanterBlocker" in loose
    assert forever["what"][-1] == (
        f"Not in WowUp's list, the only addon manager's list wowlab reads ({len(loose)})"
    )
    assert forever["loose"] == [", ".join(loose)]


def test_without_settings_the_panel_says_it_matched_by_name_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _wowup()
    out = _drive(tmp_path / "node", _scenario(server, [{"sources_open": True}]))
    for block in out["steps"][0]["sources"]["blocks"]:
        assert block["notes"][0] == (
            "wowlab cannot tell which game folder WowUp's list is for (its settings file is "
            "not there), so its records are matched to these addons by folder name."
        )


def test_with_no_wowup_list_the_panel_says_so_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"sources_open": True}]))
    for block in out["steps"][0]["sources"]["blocks"]:
        assert block["clear"] == [
            "wowlab found no WowUp list on this computer, so it cannot tell where these "
            "addons came from."
        ]
        assert block["items"] == [] and block["loose"] == [] and block["notes"] == []


def test_an_unreadable_list_is_said_constructed(server: Any, both: Path, tmp_path: Path) -> None:
    _wowup(b"[]")
    out = _drive(tmp_path / "node", _scenario(server, [{"sources_open": True}]))
    for block in out["steps"][0]["sources"]["blocks"]:
        assert block["errors"] == [
            "wowlab could not read WowUp's list: not a JSON object of addon records."
        ]
        assert block["clear"] == [] and block["loose"] == []


def test_a_failed_run_is_said_in_its_block_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _wowup()
    scenario = _scenario(server, [{"sources_open": True}])
    run = _list_run(scenario, BETA)
    run["exit_code"], run["stdout"], run["stderr"] = 1, "", "wowlab: something went wrong"
    out = _drive(tmp_path / "node", scenario)
    blocks = _blocks(out["steps"][0]["sources"])
    assert blocks[BETA]["errors"] == ["wowlab: something went wrong"]
    assert blocks[BETA]["items"] == []
    assert blocks[FOREVER]["errors"] == [] and blocks[FOREVER]["items"]


def test_hostile_record_text_is_shown_not_applied_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _wowup()
    _settings(both / FOREVER)
    scenario = _scenario(server, [{"sources_open": True}])
    run = _list_run(scenario, FOREVER)
    report = json.loads(run["stdout"])
    for record in report["sources"]["records"]:
        record["name"] = f"<img src=x>{RLO}Evil"
        record["provider"] = f"Wa{RLO}go"
    run["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node", scenario)
    items = _blocks(out["steps"][0]["sources"])[FOREVER]["items"]
    assert items and all(i.startswith("<img src=x>⟨U+202E⟩Evil: from Wa⟨U+202E⟩go") for i in items)


# ─── what the section may do ─────────────────────────────────────────────────


def test_the_section_runs_only_addons_list_and_changes_nothing() -> None:
    script = (hub_tab.first_stage._TEMPLATES / "page.js").read_text(encoding="utf-8")
    start = script.index("// ─── where each addon came from (M22-16)")
    body = script[start : script.index("// ─── the confirm step (M14-02)", start)]
    assert re.findall(r"runJson\(([^)]*)\)", body) == [
        '["addons", "list"], { flavor: folder }, false, true'
    ]
    for word in (r"\bconfirm\b", r"\byes\b", "innerHTML", "setAttribute", r"fetch\(", "href"):
        assert not re.search(word, body), word
    # Started from the Addons view only, as one part of the page that fails alone.
    assert script.count("loadSources()") - script.count("function loadSources()") == 1
    assert 'if (name === "addons") alone(sourcesSay, () => loadSources());' in script
