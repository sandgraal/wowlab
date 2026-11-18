# Probe from review of m15/20-typer-0-29; reproduces a Pydantic message ("Input should be a valid string, unable to parse raw data as a unicode string") printed for an int option given a byte that is not UTF-8.
"""On macOS and Linux a command-line byte that is not UTF-8 reaches Python as
a lone surrogate (`wowlab log tail -n $'\\xff'` gives `'\\udcff'`). Typer 0.28
printed click's wording for it ("'\\udcff' is not a valid int range.");
typer 0.29 validates through Pydantic, whose `string_unicode` error
`_common._click_wording` does not rebuild, so Pydantic's text reaches the
player. Constructed input: a hostile/boundary argv value, no fixture.
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
            ["log", "tail", "-n", "\udcff"],
            "Invalid value for '--lines' / '-n': '\\udcff' is not a valid int range.",
            id="ranged-int-undecodable-constructed",
        ),
        pytest.param(
            ["casc", "stat", "--fdid", "\udcff"],
            "Invalid value for '--fdid': '\\udcff' is not a valid int.",
            id="plain-int-undecodable-constructed",
        ),
        # Positive control: an ordinary unreadable value is already reworded.
        pytest.param(
            ["log", "tail", "-n", "x"],
            "Invalid value for '--lines' / '-n': 'x' is not a valid int range.",
            id="control-ranged-int-unreadable",
        ),
    ],
)
def test_an_undecodable_int_value_is_worded_as_typer_0_28_did(args: list[str], error: str) -> None:
    result = runner.invoke(cli.app, args)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr)
    assert "Input should" not in result.stderr
    assert result.stderr.split("\n")[-2] == f"Error: {error}", result.stderr
