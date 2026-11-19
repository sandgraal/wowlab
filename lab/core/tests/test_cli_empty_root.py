"""`--root ""` is refused (M15-25): click turns an empty `--root` into `.`,
the current folder, which no player means, so every command that takes
`--root` refuses it as a usage error (exit 2) before it reads anything, in
text and in `--json`. Constructed: no install; the current folder is a
fresh empty one, and the user data directory is redirected."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import cli

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _user_dirs import redirect_user_dirs

runner = CliRunner()
WORDS = "--root is empty; name the install folder"


def _leaves() -> list[tuple[tuple[str, ...], bool]]:
    """Every command with `--root` and no required parameter: its path and
    whether it takes `--json`."""
    out: list[tuple[tuple[str, ...], bool]] = []

    def walk(cmd: Any, path: tuple[str, ...]) -> None:
        if hasattr(cmd, "commands"):  # a group
            for name, sub in sorted(cmd.commands.items()):
                walk(sub, (*path, name))
            return
        opts = {o for p in cmd.params for o in getattr(p, "opts", [])}
        if "--root" in opts and not any(p.required for p in cmd.params):
            out.append((path, "--json" in opts))

    walk(typer.main.get_command(cli.app), ())
    return out


LEAVES = _leaves()


@pytest.fixture(autouse=True)
def _nowhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)


def test_the_walk_finds_the_commands_named_in_the_ticket() -> None:
    paths = {p for p, _ in LEAVES}
    assert len(paths) > 20
    for wanted in [("doctor",), ("install", "show"), ("char", "list"), ("char", "model")]:
        assert wanted in paths


@pytest.mark.parametrize(("path", "has_json"), LEAVES, ids=[" ".join(p) for p, _ in LEAVES])
@pytest.mark.parametrize("spelling", ["separate", "equals"])
def test_constructed_empty_root_is_a_usage_error(
    path: tuple[str, ...], has_json: bool, spelling: str
) -> None:
    root = ["--root", ""] if spelling == "separate" else ["--root="]
    result = runner.invoke(cli.app, [*path, *root])
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert WORDS in " ".join(result.stderr.split())
    if has_json:
        result = runner.invoke(cli.app, [*path, *root, "--json"])
        assert result.exit_code == 2, result.output
        report = json.loads(result.stdout)
        assert report["kind"] == "CliError" and report["exit_code"] == 2
        assert report["message"].startswith(WORDS)


def test_a_root_of_dot_is_still_the_current_folder() -> None:
    """Positive control: `--root .` is a folder named, not refused."""
    result = runner.invoke(cli.app, ["install", "show", "--root", ".", "--json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["kind"] == "NotAnInstallError"
    assert WORDS not in result.stderr
