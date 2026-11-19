"""A bad option value is worded as wowlab printed it up to typer 0.28 (M15-20).

Typer 0.29 converts values through Pydantic and words a bad one as Pydantic
does ("Input should be a valid integer, ..."). `_click_wording` rebuilds the
click wording: the expected lines below are what typer 0.28 printed for the
same arguments, captured on 2026-11-18. Parsing fails before any command runs,
so no install is read.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from wowlab_core import cli

runner = CliRunner()


@pytest.mark.parametrize(
    ("args", "error"),
    [
        pytest.param(
            ["log", "tail", "-n", "1.5"],
            "Invalid value for '--lines' / '-n': '1.5' is not a valid int range.",
            id="ranged-int-unreadable",
        ),
        pytest.param(
            ["log", "tail", "-n", ""],
            "Invalid value for '--lines' / '-n': '' is not a valid int range.",
            id="ranged-int-empty",
        ),
        pytest.param(
            ["log", "tail", "-n", "-1"],
            "Invalid value for '--lines' / '-n': -1 is not in the range 0<=x<=100000.",
            id="below-a-closed-range",
        ),
        pytest.param(
            ["log", "tail", "-n", "99999999999"],
            "Invalid value for '--lines' / '-n': 99999999999 is not in the range 0<=x<=100000.",
            id="above-a-closed-range",
        ),
        pytest.param(
            ["db2", "head", "T", "-n", "-3"],
            "Invalid value for '-n' / '--rows': -3 is not in the range x>=0.",
            id="below-a-minimum",
        ),
        pytest.param(
            ["casc", "stat", "--fdid", "x"],
            "Invalid value for '--fdid': 'x' is not a valid int.",
            id="plain-int-unreadable",
        ),
        # A command-line byte that is not UTF-8 reaches Python as a lone
        # surrogate; Pydantic calls it `string_unicode`, click could not read it.
        pytest.param(
            ["log", "tail", "-n", "\udcff"],
            "Invalid value for '--lines' / '-n': '\\udcff' is not a valid int range.",
            id="ranged-int-undecodable-constructed",
        ),
        pytest.param(
            ["casc", "stat", "--fdid", "1\udcff"],
            "Invalid value for '--fdid': '1\\udcff' is not a valid int.",
            id="plain-int-undecodable-constructed",
        ),
        pytest.param(
            ["cvar", "list", "--scope", "\udcff"],
            "Invalid value for '--scope': '\\udcff' is not one of 'global', 'account', 'character'.",
            id="choice-undecodable-constructed",
        ),
        pytest.param(
            ["sv", "merge", "--take", "mine", "a", "b"],
            "Invalid value for '--take': 'mine' is not one of 'ours', 'theirs'.",
            id="not-a-choice",
        ),
        pytest.param(
            ["cvar", "list", "--scope", "Global"],
            "Invalid value for '--scope': 'Global' is not one of 'global', 'account', 'character'.",
            id="choice-case-differs",
        ),
        pytest.param(
            ["cvar", "list", "--scope", "a\x1bb"],
            "Invalid value for '--scope': 'a\\x1bb' is not one of 'global', 'account', 'character'.",
            id="control-character-escaped-constructed",
        ),
    ],
)
def test_a_bad_value_is_worded_as_before(args: list[str], error: str) -> None:
    result = runner.invoke(cli.app, args)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr)
    assert result.stdout == ""
    lines = result.stderr.split("\n")
    assert lines[-2:] == [f"Error: {error}", ""], result.stderr
    assert lines[0].startswith("Usage: wowlab "), result.stderr
    assert "Input should" not in result.stderr
    assert "Value error" not in result.stderr
