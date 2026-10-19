# Probe from review of m14/09-character-keys-macros; reproduces the keys panel
# showing why 128 of 131 rows say "not in a file" only in a tooltip.
"""`keyRow` in page.js puts any note of a row that does not disagree in the
Source cell's `title` only, with the comment "Any other note is the same
words on many rows: on the pointer only, and once in the report's notes".
The second half is not so: `keymap.WHY_NOT_IN_A_FILE` ("the file may hold
only the changes from the client's defaults, or may not have been rewritten
since the key was set [verify]") is in no entry of `CharBindsReport.notes`.
`char binds`'s text form says it once ("Only in the record, no file line for
the key: ..."); the panel never shows it as text, so on the captured record
128 of the 131 rows carry the word "not in a file" with its reason and its
[verify] mark reachable only by hovering a pointer (no keyboard, no touch,
no screen-reader text: `title` is not a reliable accessible name).

Positive control: the report's rows carry the note, and the CLI's text form
prints the sentence once. The probe then asks the drawn panel's text, as
`test_ui_character_panels.py` reads it, to hold that sentence once.
`cross-capture`, the install of that file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_character_panels as panels
from test_ui_character import _gear_params

from wowlab_core import cli, keymap

home = panels.home
install = panels.install
server = panels.server
timed = panels.timed
picked = panels.picked


def test_the_reason_is_the_reports_and_the_cli_says_it_once(server: Any, picked: Path) -> None:
    """Positive control: the model carries the reason, the CLI text shows it."""
    scenario = panels._scenario(server, [{"char_tab": True}], [panels.PICKED])
    report = panels._run_report(scenario, "binds", panels.PICKED)
    rows = [k for k in report["keys"] if k["note"] == keymap.NOT_IN_A_FILE_NOTE]
    assert len(rows) == 128 and all(k["agreement"] == "not in a file" for k in rows)
    assert not any(keymap.WHY_NOT_IN_A_FILE in note for note in report["notes"])
    params = _gear_params(panels.PICKED)
    args = ["char", "binds", "--character", params["character"], "--account", params["account"]]
    text = CliRunner().invoke(cli.app, [*args, "--flavor", params["flavor"]])
    assert text.exit_code == 0, text.stdout
    assert text.stdout.count(keymap.WHY_NOT_IN_A_FILE) == 1


def test_the_keys_panel_shows_why_a_row_is_not_in_a_file(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    scenario = panels._scenario(server, [{"char_tab": True}, {"panels": True}], [panels.PICKED])
    out = panels._drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    assert sum(r["full"][4].endswith("not in a file") for r in view["keyRows"]) == 128
    # Expected: the reason, as text the player can read, at least once.
    # Observed: only in 128 `title` attributes; `keysText` never holds it.
    assert keymap.WHY_NOT_IN_A_FILE in view["keysText"]
