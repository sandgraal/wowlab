# Probe from review of m16/38-live-l1-sweep; reproduces the sweep's printed record, which the release checklist says to paste into docs/handoffs/, naming the Battle.net account folder
"""The release checklist (docs/AGENT_WORKFLOW.md, "Release checklist
(owner)") says to paste the live sweep's tail, "the exit-code list and the
`passed` line", into the release record, "the release's handoff under
`docs/handoffs/`": a file in the public repository. Each line of that list is
the command's argv, and the account commands carry `--account <folder>`. On
a real install the folder is the Battle.net account folder (`<digits>#<n>`),
which ADR-0019's amendment of 2026-10-17 keeps out of the repository.

Constructed: the captured flavor copied into `tmp_path`, with the commands
and the UI replaced by stand-ins that only record their argv, so the probe
reads what `sweep_and_compare` prints without starting a process.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_l1_sweep_live as sweep


def test_cr581_the_printed_record_does_not_name_the_account_folder_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(sweep.CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(sweep.CAPTURE / "forever", root / sweep.FLAVOR)
    home = tmp_path / "home"
    home.mkdir()

    def stand_in(*args: str) -> sweep.Ran:
        return sweep.Ran("wowlab " + " ".join(args), 0, json_out=False)

    monkeypatch.setattr(sweep, "_cli", stand_in)
    monkeypatch.setattr(sweep, "run_ui", lambda inst, s: None)

    sweep.sweep_and_compare(root, home)
    printed = capsys.readouterr().out

    # Positive control: the install has the account folder, and the record
    # was printed with the account commands in it.
    assert (root / sweep.FLAVOR / "WTF" / "Account" / sweep.ACCOUNT).is_dir()
    assert "exit   0  wowlab binds list" in printed
    # The defect: the record meant for a public file names the folder.
    leaking = [line for line in printed.splitlines() if sweep.ACCOUNT in line]
    assert leaking == [], f"{len(leaking)} printed lines name the account folder:\n" + "\n".join(
        leaking[:5]
    )
