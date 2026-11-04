# Probe from review of m16/15-player-output-lint; reproduces text output rewriting a player's own macro name and body as if they were spec markers.
"""Constructed (boundary input, L8): a macro whose name and body hold text
that looks like the spec's markers. `_say` runs every line through
`_player_copy`, so `macros list` and `char macros` print the macro as it is
not: "(L3)" and "(M12-3)" are cut, "[verify]" becomes "(not yet
confirmed)". Text read from the install is the player's data, and the
rewriting applies to it as to wowlab's own words (§18.1 item 4 asks the
output to stop saying the spec's words, not to edit what the install holds;
`sv dump` already prints through `_say_raw` for that reason).

Positive control: `--json` prints the body as the file holds it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_cli_player_copy as pc

from wowlab_core import cli

runner = CliRunner()
CHAR = "1/Labcharb-Labrealmd"
NAME = "Note (L3)"
BODY = "/say heal me (L1) [verify] (M12-3)"


def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = pc.sandbox(tmp_path, monkeypatch, "")
    folder = root / pc.FLAVOR / "WTF" / "Account" / pc.ACCOUNT / CHAR
    (folder / "macros-cache.txt").write_bytes(
        f'VER 3 0100000000000001 "{NAME}" "134400"\n{BODY}\nEND\n'.encode()
    )


def test_json_keeps_the_macro_positive_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sandbox(tmp_path, monkeypatch)
    out = runner.invoke(cli.app, ["macros", "list", "--character", CHAR, "--json"])
    assert out.exit_code == 0, out.stderr
    assert BODY in json.dumps(json.loads(out.stdout))
    assert NAME in out.stdout


@pytest.mark.parametrize("command", [["macros", "list"], ["char", "macros"]])
def test_text_output_prints_the_macro_as_the_file_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    _sandbox(tmp_path, monkeypatch)
    out = runner.invoke(cli.app, [*command, "--character", CHAR])
    assert out.exit_code == 0, out.stderr
    assert BODY in out.stdout, out.stdout
    assert NAME in out.stdout, out.stdout
