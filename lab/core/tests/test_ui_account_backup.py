"""The Account tab's backups (M17-04, docs/LAB_PLAN.md §19 CW-3): a line
per flavor with the newest backup's age, the "game updated" prompt, Back up
now (`snap create --flavor`, then `snap diff` against the backup the line
named, shown as its plan lines) and Restore, which opens the Commands tab on
`snap restore` with the id filled in and runs nothing.

The page is driven under Node with the harness of `test_ui_account.py`
(itself `test_ui_character.py`'s and `test_ui_explorer.py`'s), fed what the
real CLI printed through the real server. The install is that module's two
captures side by side (`constructed`, L8). Constructed too, as labelled: a
client "updated" by a byte replacement of the beta flavor's version cell in
`.build.info`, a CVar line added to its `Config.wtf` so the diff names one,
and report fields edited before the page reads them (an old backup, a
hostile flavor name). Every snapshot goes into the redirected home's data
folder; nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import test_ui_account as hub_tab
import test_ui_explorer as first_stage
from test_ui_explorer import RLO

both = hub_tab.both
home = hub_tab.home
server = hub_tab.server
no_env_flavor = hub_tab.no_env_flavor

BETA = hub_tab.BETA
FOREVER = hub_tab.FOREVER
BETA_VERSION = "1.60.1.69913"
NEWER = "1.60.2.70500"
CVAR = "labBackupProbe"
CREATE = ["snap", "create"]
DIFF = ["snap", "diff"]
UPDATED = "The game updated since your last backup"

_VIEW = r"""
function backupView() {
  const lines = ids["backup-lines"].children.map((li) => ({
    text: li.textContent,
    updated: (li.all().find((e) => has(e, "backup-updated")) || { textContent: null }).textContent,
    buttons: li.all().filter((e) => e.tagName === "button").map((b) => ({
      text: b.textContent, cls: b.className, title: b.title, disabled: b.disabled })),
  }));
  return {
    lines: lines,
    status: ids["backup-status"].textContent,
    statusRole: ids["backup-status"].getAttribute("role"),
    diff: ids["backup-diff"].textContent,
    diffHidden: ids["backup-diff"].hidden,
  };
}
"""

_STEPS = r"""
      } else if (step.backup !== undefined) {
        out.steps.push({ backup: backupView() });
      } else if (step.backup_click !== undefined) {
        const before = requests.length;
        const li = ids["backup-lines"].children[step.backup_click];
        const button = li && li.all().find((e) => e.tagName === "button" && has(e, step.which));
        if (!button) { out.steps.push({ missing: step.which + " " + step.backup_click }); continue; }
        button.fire("click"); await tick();
        const active = documentStub.activeElement;
        out.steps.push({ backup: backupView(), newRequests: requests.slice(before),
          accountHidden: ids["account-view"].hidden, commandsHidden: ids["commands-view"].hidden,
          title: ids.title.textContent, runText: ids.run.textContent,
          fields: ids.fields.all().filter((e) => e.tagName === "input").map((e) => ({ id: e.id, value: e.value })),
          active: active ? active.id : null });
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


def _update_beta(root: Path) -> None:
    """Constructed: the beta client "updated" to NEWER, and one CVar added
    to its Config.wtf, so the next backup differs from the last by name."""
    info = root / ".build.info"
    data = info.read_bytes()
    assert data.count(BETA_VERSION.encode()) == 1
    info.write_bytes(data.replace(BETA_VERSION.encode(), NEWER.encode()))
    config = root / BETA / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + f'SET {CVAR} "1"\n'.encode())


def _created(server: Any) -> dict[str, Any]:
    run = hub_tab._run_of(server, CREATE, {"flavor": BETA})
    assert run["exit_code"] == 0, run["stderr"]
    return run


def _summary(scenario: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(hub_tab._summary_run(scenario)["stdout"])
    return report


def _edit_summary(scenario: dict[str, Any], report: dict[str, Any]) -> None:
    hub_tab._summary_run(scenario)["stdout"] = json.dumps(report)


def _updated_scenario(server: Any, root: Path, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """A backup of the beta flavor, the client updated since, the hub's
    report read then, and the next backup with its diff, all recorded."""
    first = json.loads(_created(server)["stdout"])["id"]
    _update_beta(root)
    scenario = hub_tab._scenario(server, steps)
    made = _created(server)
    second = json.loads(made["stdout"])["id"]
    scenario["runs"] += [made, hub_tab._run_of(server, DIFF, {"a": first, "b": second})]
    scenario["first_id"] = first
    scenario["second_id"] = second
    return scenario


_AGE = r"Last backup (just now|\d+ minutes? ago) \(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\)"


# ─── the lines ───────────────────────────────────────────────────────────────


def test_each_flavor_has_a_line_and_no_backup_yet_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", hub_tab._scenario(server, [{"backup": True}]))
    [step] = out["steps"]
    lines = step["backup"]["lines"]
    assert [line["text"].split("Back up now")[0] for line in lines] == [
        f"{BETA}: No backup yet.",
        f"{FOREVER}: No backup yet.",
    ]
    for line in lines:
        assert line["updated"] is None
        # No backup: nothing to restore.
        assert [b["cls"] for b in line["buttons"]] == ["backup-now"]
        assert line["buttons"][0]["disabled"] is False
    assert step["backup"]["diffHidden"] is True


def test_a_backup_on_another_build_prompts_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = _updated_scenario(server, both, [{"backup": True}])
    [line] = [b for b in _summary(scenario)["backups"] if b["flavor"] == BETA]
    assert line["game_updated"] is True and line["snapshot_id"] == scenario["first_id"]
    out = _drive(tmp_path / "node", scenario)
    [step] = out["steps"]
    beta, forever = step["backup"]["lines"]
    assert re.match(f"{BETA}: {_AGE}, taken on version {BETA_VERSION}\\.", beta["text"])
    assert beta["updated"] == f" {UPDATED}: it is now version {NEWER}."
    assert [b["cls"] for b in beta["buttons"]] == ["backup-now", "backup-restore"]
    assert [b["text"] for b in beta["buttons"]] == ["Back up now", "Restore this backup…"]
    # The other flavor has no backup and no prompt.
    assert forever["updated"] is None
    assert forever["text"].startswith(f"{FOREVER}: No backup yet.")


def test_an_old_backup_says_its_age_in_days_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    hub_tab._run_of(server, CREATE, {"flavor": BETA})
    scenario = hub_tab._scenario(server, [{"backup": True}])
    report = _summary(scenario)
    for line in report["backups"]:
        if line["flavor"] == BETA:
            line["created_at"] = "2020-01-01T00:00:00.000000Z"
    _edit_summary(scenario, report)
    out = _drive(tmp_path / "node", scenario)
    beta = out["steps"][0]["backup"]["lines"][0]
    # The local time of 2020-01-01T00:00Z falls on one of these two days.
    assert re.match(f"{BETA}: Last backup \\d+ days ago \\((2020-01-01|2019-12-31) ", beta["text"])


# ─── Back up now ─────────────────────────────────────────────────────────────


def test_back_up_now_runs_snap_create_for_the_flavor_then_the_diff_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    steps = [{"backup_click": 0, "which": "backup-now"}]
    scenario = _updated_scenario(server, both, steps)
    first, second = scenario["first_id"], scenario["second_id"]
    out = _drive(tmp_path / "node", scenario)
    [clicked] = out["steps"]
    assert hub_tab._bodies(clicked["newRequests"]) == [
        {"command": CREATE, "params": {"flavor": BETA}},
        {"command": DIFF, "params": {"a": first, "b": second}},
    ]
    view = clicked["backup"]
    assert (
        view["status"] == f"Backed up {BETA} as {second}. What changed since the previous backup:"
    )
    assert view["statusRole"] == "status"
    diff = next(r for r in scenario["runs"] if r["command"] == DIFF)
    assert view["diff"] == "\n".join(json.loads(diff["stdout"])["plan_lines"])
    assert view["diffHidden"] is False
    # The CVar is named, as the CLI's plan lines name it.
    assert CVAR in view["diff"] and f"From {first} to {second}:" in view["diff"]
    # The line now names the backup just taken, and the prompt is gone.
    beta = view["lines"][0]
    assert re.match(f"{BETA}: {_AGE}, taken on version {NEWER}\\.", beta["text"])
    assert beta["updated"] is None
    assert all(not b["disabled"] for line in view["lines"] for b in line["buttons"])


def test_the_first_backup_says_there_is_nothing_to_compare_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    steps = [{"backup_click": 0, "which": "backup-now"}]
    scenario = hub_tab._scenario(server, steps)
    made = _created(server)
    scenario["runs"].append(made)
    out = _drive(tmp_path / "node", scenario)
    [clicked] = out["steps"]
    new = json.loads(made["stdout"])["id"]
    assert hub_tab._bodies(clicked["newRequests"]) == [
        {"command": CREATE, "params": {"flavor": BETA}}
    ]
    assert clicked["backup"]["status"] == (
        f"Backed up {BETA} as {new}. It is the first backup of {BETA} here, so there is "
        "nothing to compare it with."
    )
    assert clicked["backup"]["diffHidden"] is True


def test_a_failed_backup_says_why_and_keeps_the_line_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    steps = [{"backup_click": 1, "which": "backup-now"}]
    scenario = hub_tab._scenario(server, steps)
    scenario["runs"].append(
        {
            "command": CREATE,
            "params": {"flavor": FOREVER},
            "stdout": "",
            "exit_code": 1,
            "stderr": "error: the store is locked",
        }
    )
    out = _drive(tmp_path / "node", scenario)
    [clicked] = out["steps"]
    assert hub_tab._bodies(clicked["newRequests"]) == [
        {"command": CREATE, "params": {"flavor": FOREVER}}
    ]
    assert clicked["backup"]["status"] == "error: the store is locked"
    assert clicked["backup"]["statusRole"] == "alert"
    assert clicked["backup"]["lines"][1]["text"].startswith(f"{FOREVER}: No backup yet.")


# ─── Restore ─────────────────────────────────────────────────────────────────


def test_restore_opens_the_commands_tab_on_snap_restore_and_runs_nothing_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    hub_tab._run_of(server, CREATE, {"flavor": BETA})
    steps = [{"backup_click": 0, "which": "backup-restore"}]
    scenario = hub_tab._scenario(server, steps)
    [line] = [b for b in _summary(scenario)["backups"] if b["flavor"] == BETA]
    out = _drive(tmp_path / "node", scenario)
    [clicked] = out["steps"]
    assert clicked["newRequests"] == [], "opening the plan's form runs nothing"
    assert clicked["accountHidden"] is True and clicked["commandsHidden"] is False
    assert clicked["title"] == "wowlab snap restore"
    # The confirm step's own button: the plan comes first, as for any change.
    assert clicked["runText"] == "Show the plan"
    assert {"id": "param-snapshot_id", "value": line["snapshot_id"]} in clicked["fields"]
    assert clicked["active"] == "run"


# ─── what the page holds ─────────────────────────────────────────────────────


def test_the_flavor_filter_shows_that_flavors_line_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    steps = [{"hub_filter": "flavor", "value": "f:" + FOREVER}, {"backup": True}]
    out = _drive(tmp_path / "node", hub_tab._scenario(server, steps))
    lines = out["steps"][1]["backup"]["lines"]
    assert len(lines) == 1 and lines[0]["text"].startswith(f"{FOREVER}: No backup yet.")


def test_a_store_that_cannot_be_read_is_said_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = hub_tab._scenario(server, [{"backup": True}])
    report = _summary(scenario)
    report["backups"] = []
    report["backup_error"] = "wowlab could not read its snapshot store: denied"
    _edit_summary(scenario, report)
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][0]["backup"]
    assert view["lines"] == []
    assert view["status"] == "wowlab could not read its snapshot store: denied"
    assert view["statusRole"] == "alert"


def test_hostile_report_values_draw_as_inert_text_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = hub_tab._scenario(server, [{"backup": True}])
    report = _summary(scenario)
    report["backups"] = [
        {
            "flavor": f"_x{RLO}_",
            "version": "<b>2</b>",
            "snapshot_id": "<img src=x>",
            "created_at": "not a time",
            "snapshot_version": f"1{RLO}",
            "game_updated": True,
        }
    ]
    _edit_summary(scenario, report)
    out = _drive(tmp_path / "node", scenario)
    [line] = out["steps"][0]["backup"]["lines"]
    assert line["text"].startswith(
        "Last backup at a time wowlab cannot read, taken on version 1⟨U+202E⟩."
    )
    assert line["updated"] == f" {UPDATED}: it is now version <b>2</b>."


def test_the_backup_section_runs_only_snap_create_and_snap_diff() -> None:
    script = (first_stage._TEMPLATES / "page.js").read_text(encoding="utf-8")
    start = script.index("// ─── backups on the Account tab")
    body = script[start : script.index("// ─── the command list ───", start)]
    runs = re.findall(r"runJson\(([^)]*)\)", body)
    assert runs == [
        '["snap", "create"], { flavor: line.flavor }, false, true',
        '["snap", "diff"], { a: before, b: manifest.id }, false, true',
    ]
    # No write of its own, no confirm token, no --yes: a restore is the
    # Commands tab's plan and Confirm.
    for word in ("confirm", "yes", "innerHTML", "setAttribute", "fetch("):
        assert word not in body, word


def test_one_flavor_names_no_flavor_on_its_line_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    scenario = hub_tab._scenario(server, [{"backup": True}])
    report = _summary(scenario)
    report["backups"] = [b for b in report["backups"] if b["flavor"] == BETA]
    _edit_summary(scenario, report)
    out = _drive(tmp_path / "node", scenario)
    [line] = out["steps"][0]["backup"]["lines"]
    assert line["text"].startswith("No backup yet.")
    assert line["buttons"][0]["title"] == ""
