# Probe from review of m15/25-review-leftovers; reproduces every command's --help showing `--root <Path>` where it showed `--root <path>`.
"""`RootOpt` gained `parser=_RootArg.parse` (M15-25). An option with its own
parser loses click's Path type, so the help's metavar comes from the
annotation's class name instead: `--root <Path>` on all 44 commands that
take `--root`, a user-visible change the branch says it does not make
("`--help` text for `--root` unchanged"). A `metavar="<path>"` on the
option restores it. Constructed: help text only, no install is read."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from wowlab_core import cli

runner = CliRunner()


def _help(*command: str) -> list[str]:
    result = runner.invoke(cli.app, [*command, "--help"], terminal_width=200)
    assert result.exit_code == 0, result.output
    return result.output.split()


def test_positive_control_another_path_option_says_path() -> None:
    # `char model --out` sets metavar="<path>" itself; the word the help
    # uses for a path is `<path>`.
    words = _help("char", "model")
    assert words[words.index("--out") + 1] == "<path>"


@pytest.mark.parametrize("command", [("doctor",), ("sv", "list"), ("char", "model")])
def test_root_is_shown_as_a_path(command: tuple[str, ...]) -> None:
    words = _help(*command)
    # Fails on 2c4660c: the word after --root is "<Path>".
    assert words[words.index("--root") + 1] == "<path>"
