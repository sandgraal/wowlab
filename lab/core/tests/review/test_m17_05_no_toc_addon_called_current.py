# Probe from review of m17/05-patch-day-card; reproduces the patch-day card
# calling an addon folder the client does not load "current for this client".
"""`addons check` marks an addon folder with no TOC named after it
`selection: "no_toc"` (the client does not load it; the CLI's text summary
counts it as "N with no TOC the client loads"). The card's "Out of date"
list takes only `out_of_date` and `no_toc_for_client`, both false for such a
folder, so with nothing else found the card says "All 2 addons are current
for this client" about an install where one of the two never loads. The
card's own intro promises "Addons this client may not load".

Constructed (labelled, L8): the beta capture's `addons check` report with
every addon made current, then one turned into a no-TOC folder, as
`addoncheck.check_addons` reports one. Positive control: the same edit with
`out_of_date` set is listed, so the harness reads the edited report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_account_patch_day as card

both = card.both
home = card.home
server = card.server
no_env_flavor = card.no_env_flavor


def _beta(server: Any, tmp_path: Path, out_of_date: bool) -> dict[str, Any]:
    scenario = card._scenario(server, [{"patch_day": True}])
    run = card._run(scenario, card.CHECK, card.BETA)
    report = json.loads(run["stdout"])
    for addon in report["check"]["addons"]:
        addon["out_of_date"] = False
        addon["no_toc_for_client"] = False
        addon["missing_dependencies"] = []
        addon["unloadable_dependencies"] = []
    first = report["check"]["addons"][0]
    first["selection"] = "no_toc"
    first["tocs"] = []
    first["counted_tocs"] = []
    first["out_of_date"] = out_of_date
    run["stdout"] = json.dumps(report)
    listed_run = card._run(scenario, card.SV_LIST, card.BETA)
    listed = json.loads(listed_run["stdout"])
    listed["files"] = []
    listed_run["stdout"] = json.dumps(listed)
    return card._blocks(card._drive(tmp_path / "node", scenario))[card.BETA]


def test_positive_control_an_edited_addon_is_listed_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    beta = _beta(server, tmp_path, out_of_date=True)
    assert beta["what"] == ["Out of date for this client"]


def test_a_folder_the_client_does_not_load_is_not_called_current_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    beta = _beta(server, tmp_path, out_of_date=False)
    assert beta["clear"] != ["All 2 addons are current for this client."], beta
    assert beta["items"], "the no-TOC folder is on no line of the card"
