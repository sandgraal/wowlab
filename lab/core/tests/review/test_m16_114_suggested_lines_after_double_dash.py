# Probe from review of m16/114-flavor-picker; reproduces the several-flavors error offering command lines that put --flavor after `--`, where it is an extra argument
"""LAB_PLAN §16 CLI conventions, amendment of 2026-11-11: with several
flavors and none named, the error lists "each flavor with the exact
command line to copy". `_with_flavor_lines` appends `--flavor <folder>` to
the arguments as typed; after a `--` (which `wowlab ui` puts before every
positional argument, `ui._build`) that is read as an argument, and the
offered line exits 2 with "Got unexpected extra argument(s)".

The install is `test_cli_flavors.py`'s: the committed capture plus one
`constructed` flavor folder (L8 boundary input).
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli_flavors import run, two_flavors  # noqa: F401


def _offered(stderr: str) -> list[list[str]]:
    return [shlex.split(line)[1:] for line in re.findall(r"`(wowlab [^`]*)`", stderr)]


def test_positive_control_lines_without_double_dash_run() -> None:
    first = run("cvar", "get", "x")
    assert first.exit_code == 2
    offered = _offered(first.stderr)
    assert len(offered) == 2
    for argv in offered:
        assert "unexpected extra argument" not in run(*argv).stderr


@pytest.mark.parametrize(
    "args",
    [("cvar", "get", "--", "x"), ("tree", "--", "WTF"), ("sv", "dump", "--", "WTF/Config.wtf")],
)
def test_lines_offered_after_double_dash_run(args: tuple[str, ...]) -> None:
    first = run(*args)
    assert first.exit_code == 2
    offered = _offered(first.stderr)
    assert len(offered) == 2, first.stderr
    for argv in offered:
        again = run(*argv)
        assert "unexpected extra argument" not in again.stderr, (argv, again.stderr)
