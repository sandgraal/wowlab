"""A backup's record of a character folder with no lab record, on the
Characters home of `wowlab ui` (M18-11): the card and the table row say so
in one line, which opens the read-only page a kept character opens.

The page is driven under Node with `test_ui_kept.py`'s harness, fed what
the real CLI printed through the real server. The install is that module's
two captures side by side, and the store is `constructed` (L8, a boundary
input): the owner's beta character `1/Sangraal-Sett` of
`fixtures/macos-70450/` is copied into `_classic_beta_`'s account, a
snapshot of its `WTF/` is taken into the stand-in home's data folder, and
then only its `WowLab.lua` is deleted, so the folder stays with no lab
record while the store holds one. Nothing reads or writes a real install.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import test_cli_char_kept as kept_cli
import test_ui_account as hub_tab
import test_ui_explorer as first_stage
import test_ui_home as home_tab
import test_ui_kept as kept_tab

both = hub_tab.both
home = hub_tab.home
server = hub_tab.server
no_env_flavor = hub_tab.no_env_flavor

BETA = hub_tab.BETA
BARE = hub_tab.BARE
ACCOUNT = hub_tab.ACCOUNT
SETT = kept_cli.SETT
KEPT = kept_tab.KEPT
READS = kept_tab.READS
HINT = re.compile(r"Your backup from Oct \d{1,2}(, \d{4})? has a record of this character")

_STEPS = r"""
      } else if (step.held_open !== undefined) {
        const before = requests.length;
        const where = step.held_open === "card" ? ids["char-cards"] : ids["hub-table"];
        const button = where.all().find((e) => e.tagName === "button" && has(e, "held-button"));
        if (!button) { out.steps.push({ missing: "held " + step.held_open }); continue; }
        button.fire("click"); await tick();
        out.steps.push({ char: charView(), characterHidden: ids["character-view"].hidden,
          kept: ids["char-kept"].textContent, keptHidden: ids["char-kept"].hidden,
          barHidden: ids["char-bar-panel"].hidden, newRequests: requests.slice(before) });
"""

_ANCHOR = hub_tab._ANCHOR
_KEPT_HARNESS = kept_tab._harness


def _harness() -> str:
    text = _KEPT_HARNESS()
    assert text.count(_ANCHOR) == 1
    return text.replace(_ANCHOR, _STEPS.lstrip("\n") + _ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = home_tab._harness
    home_tab._harness = _harness
    try:
        return home_tab._drive(folder, scenario)
    finally:
        home_tab._harness = original


@pytest.fixture
def held(both: Path) -> Path:
    from wowlab_core import snapshot

    account = kept_tab._account(both)
    shutil.copytree(kept_cli.OWNER / SETT, account / SETT)
    os.utime(account / SETT / kept_cli.SV, ns=(kept_cli.SETT_NS, kept_cli.SETT_NS))
    snapshot.SnapshotStore().create(
        both,
        [f"{BETA}/WTF"],
        flavor_folder=BETA,
        flavor_version=kept_cli.BETA_VERSION,
        now=kept_tab.SNAPPED,
    )
    (account / SETT / kept_cli.SV).unlink()
    return both


def _scenario(server: Any, steps: list[dict[str, Any]]) -> dict[str, Any]:
    scenario = home_tab._scenario(server, steps)
    run = hub_tab._run_of(server, KEPT, kept_tab._kept_params(SETT))
    assert run["exit_code"] == 0, run["stderr"]
    scenario["runs"].append(run)
    return scenario


def _hub_row(view: dict[str, Any], label: str) -> dict[str, Any]:
    [row] = [r for r in view["rows"] if label in r["cells"]]
    return row


def test_a_folder_with_no_record_names_the_backup_that_holds_one_constructed(
    server: Any, held: Path, tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"home": True}, {"hub": True}]))
    view = out["steps"][0]["home"]
    sett = home_tab._card(view, "Sangraal-Sett")
    assert "card-bare" in sett["cls"].split()
    (button,) = sett["buttons"]
    kind, _, words = button.partition(": ")
    assert kind == "link-button held-button"
    assert HINT.fullmatch(words), words
    # The capture's own folder with no record: no backup holds one, no line.
    assert home_tab._card(view, home_tab._names(BARE))["buttons"] == []
    # Its folder is in the game folder: no kept card for it.
    assert all("card-kept" not in c["cls"] for c in view["cards"])
    # The table row says the same, after what puts a record there.
    row = _hub_row(out["steps"][1]["hub"], "Sangraal-Sett")
    assert row["bare"] is True
    assert HINT.fullmatch(row["advice"][-1][1]) and row["advice"][-1][0] == "button"
    bare = _hub_row(out["steps"][1]["hub"], home_tab._names(BARE))
    assert not any(HINT.fullmatch(text) for _, text in bare["advice"])


@pytest.mark.parametrize("where", ["card", "row"])
def test_the_hint_opens_the_backups_page_and_restores_nothing_constructed(
    server: Any, held: Path, tmp_path: Path, where: str
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, [{"held_open": where}]))
    step = out["steps"][0]
    assert "missing" not in step, step
    assert step["characterHidden"] is False
    bodies = hub_tab._bodies(step["newRequests"])
    assert {"command": KEPT, "params": kept_tab._kept_params(SETT)} in bodies
    # Every run is a read; nothing restores, backs up or installs.
    assert {tuple(b["command"]) for b in bodies} <= READS
    assert step["keptHidden"] is False
    assert step["kept"].startswith("From your backup of Oct ")
    assert step["barHidden"] is True
    char = step["char"]
    assert char["hero"]["name"] == "Sangraal-Sett"
    assert char["hero"]["tile"] == "Level 15"
    assert char["rows"], "the backup's gear is drawn"
    assert char["errors"] == []
    # Nothing was restored: the folder still holds no record.
    assert not (kept_tab._account(held) / SETT / kept_cli.SV).exists()


def test_no_backup_record_no_hint_constructed(server: Any, both: Path, tmp_path: Path) -> None:
    out = _drive(tmp_path / "node", home_tab._scenario(server, [{"home": True}]))
    bare = home_tab._card(out["steps"][0]["home"], home_tab._names(BARE))
    assert bare["buttons"] == []


def test_a_hint_needs_a_readable_record_and_a_matching_folder_constructed(
    server: Any, held: Path, tmp_path: Path
) -> None:
    """Constructed: the report's record made unreadable, and one named for
    another folder; neither gives a line."""
    scenario = _scenario(server, [{"home": True}])
    report = home_tab._summary(scenario)
    (record,) = report["records_in_backups"]
    other = dict(record, label="Someone-Else")
    record["state"], record["error"] = "not_read", "its stored copy cannot be used"
    report["records_in_backups"] = [record, other]
    home_tab._edit_summary(scenario, report)
    view = _drive(tmp_path / "node", scenario)["steps"][0]["home"]
    assert home_tab._card(view, "Sangraal-Sett")["buttons"] == []


def test_the_hint_uses_text_nodes_only() -> None:
    script = (first_stage._TEMPLATES / "page.js").read_text(encoding="utf-8")
    body = script[
        script.index("// ─── a backup's record of a folder with no lab record") : script.index(
            "// Opens the character page on a kept character."
        )
    ]
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", ".style"):
        assert sink not in body, sink
