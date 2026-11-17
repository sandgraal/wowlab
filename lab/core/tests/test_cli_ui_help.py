"""`wowlab ui --help` (M18-11): it says that Keybinds holds the keys and
the macros, and it names every command the page will not run, as the
server refuses them."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from wowlab_core import cli, ui

runner = CliRunner()


def _help() -> str:
    result = runner.invoke(cli.app, ["ui", "--help"], terminal_width=200)
    assert result.exit_code == 0, result.output
    # Rich's box and wrapping removed, so a sentence reads as one line.
    text = re.sub(r"[│╭╮╰╯─]", " ", result.output)
    return re.sub(r"\s+", " ", text)


def test_keybinds_holds_the_keys_and_the_macros() -> None:
    assert "Keybinds shows a character's key bindings and macros." in _help()


def test_every_command_the_page_refuses_is_named() -> None:
    text = _help()
    sentence = "ui itself, log tail --follow, looks page --out and snap export are not offered."
    assert sentence in text
    # Each leaf the server refuses whole is in the sentence.
    for path in ui._EXCLUDED_LEAVES:
        words = "ui itself" if path == ("ui",) else " ".join(path)
        assert words in sentence, path
