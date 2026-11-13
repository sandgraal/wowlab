# Probe from review of m16/112-wider-install-search; reproduces `sv dump --install-only` with no install offering `--root "<path>"` in its notes, while its own message says --root does not choose the install there.
"""Constructed (no game data): one empty default location, no variable.

`_keep_notes` copies every note of `InstallNotFoundError` onto the CLI
error, including `name_the_install_lines()`, which offers `--root "<path>"`
"for one command". With `--install-only` the message says the opposite
(M16-19, `_NAME_THE_INSTALL_BY_ITSELF`): only `WOWLAB_WOW_ROOT` can name the
install. The positive control shows the same line is right for
`install show`, where --root does choose the install.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

ROOT_LINE = '  --root "<path>"'


@pytest.fixture
def no_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    user = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: user)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    empty = tmp_path / "defaults" / "World of Warcraft"
    empty.mkdir(parents=True)
    monkeypatch.setattr(install, "default_roots", lambda **k: (empty,))
    return empty


def _run(*args: str) -> Any:
    return CliRunner().invoke(cli.app, list(args))


def test_constructed_install_show_offers_root_positive_control(no_install: Path) -> None:
    result = _run("install", "show")
    assert result.exit_code == 1
    assert ROOT_LINE in result.stderr.splitlines()


def test_constructed_install_only_does_not_offer_root(no_install: Path) -> None:
    result = _run("sv", "dump", "--install-only", "x.lua")
    assert result.exit_code == cli.EXIT_REFUSED
    lines = result.stderr.splitlines()
    assert "--root does not choose the install" in lines[0]
    # The notes must not then tell the player to use --root.
    assert ROOT_LINE not in lines, lines
