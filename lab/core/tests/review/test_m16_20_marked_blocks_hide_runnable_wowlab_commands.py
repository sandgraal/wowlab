# Probe from review of m16/20-readme-examples; reproduces runnable `wowlab` commands in a not-run block (doctor, snap create, --help) never running.
"""M16-20: "Every fenced `wowlab` command in README and INSTALL runs against
the fixture sandbox in CI." The skips the ticket allows are installs, the
network, and commands that do not exit (`wowlab ui`, `log tail --follow`).

`test_doc_examples` skips a marked block whole. The README's "first three
commands" block is marked because of `wowlab ui`, so `wowlab doctor` and
`wowlab snap create` beside it never run; the source-checkout blocks are
marked because of `git clone`, so `uv run wowlab doctor` and
`uv run wowlab --help` never run either. Each of them exits 0 on the
fixture sandbox. A typo in one of them (`wowlab snap creat`) passes the
test, because `SKIPS` pins only a block's first command.

The probe reads the real README.md and docs/INSTALL.md. The positive
control shows the collector agrees with the branch's own `RUNS` on the
unmarked blocks, so the failure is about the marked blocks only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_doc_examples as t
from test_cli_player_copy import sandbox
from typer.testing import CliRunner

from wowlab_core import cli

# The commands the ticket lets a document skip because they do not exit.
NEVER_EXIT = (["wowlab", "ui"], ["wowlab", "log", "tail", "--follow"])


def _wowlab_commands(name: str, *, marked: bool) -> list[str]:
    return [
        c.text
        for b in t.read_blocks(t._doc(name)).blocks
        if (b.skip is not None) is marked
        for c in b.commands
        if t._is_wowlab(c.text)
    ]


def _runnable(text: str) -> bool:
    words = t._argv(text)
    return not any(words[: len(n)] == n for n in NEVER_EXIT)


@pytest.mark.parametrize("name", t.DOCS)
def test_positive_control_unmarked_blocks_are_what_runs(name: str) -> None:
    assert tuple(_wowlab_commands(name, marked=False)) == t.RUNS[name]


def test_the_hidden_commands_exit_zero_on_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """They are runnable: none needs the network, an install step or a TTY."""
    sandbox(tmp_path, monkeypatch, "")
    runner = CliRunner()
    for args in (["doctor"], ["snap", "create"], ["--help"]):
        result = runner.invoke(cli.app, args, terminal_width=80)
        assert result.exit_code == 0, (args, result.output)


@pytest.mark.parametrize("name", t.DOCS)
def test_no_runnable_wowlab_command_is_skipped(name: str) -> None:
    hidden = [c for c in _wowlab_commands(name, marked=True) if _runnable(c)]
    assert hidden == [], f"{name}: runnable wowlab commands skipped with their block: {hidden}"
