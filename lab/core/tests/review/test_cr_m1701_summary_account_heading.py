# Probe from review of m17/01-account-hub; reproduces `char list --summary --account X` saying "no character folder" for a flavor that has no account X.
"""Constructed (the two-flavor install of `test_cli_char_summary.py`).

With several flavors and `--account` naming an account only one flavor
holds, the plain `char list` heads the other flavor "no account X", which is
what happened. The `--summary` text heads it "no character folder", which
says the account was found there and is empty. The positive control is the
plain list's heading on the same install."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install


def _load(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().parent.parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


summary_tests = _load("_cr_m1701_summary", "test_cli_char_summary.py")
runner = CliRunner()


@pytest.fixture
def both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    root = summary_tests.two_flavors(tmp_path / "World of Warcraft")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _heading(args: list[str]) -> str:
    done = runner.invoke(cli.app, ["char", "list", *args])
    beta = summary_tests.BETA
    return next(line for line in done.stdout.splitlines() if line.startswith(f"Flavor {beta}"))


def test_positive_control_plain_list_says_no_account(both: Path) -> None:
    account = summary_tests.LAUNCH_ACCOUNT
    assert _heading(["--account", account]).endswith(f": no account {account}")


def test_summary_says_no_account_not_no_character_folder(both: Path) -> None:
    account = summary_tests.LAUNCH_ACCOUNT
    heading = _heading(["--summary", "--account", account])
    assert "no character folder" not in heading, heading
    assert heading.endswith(f": no account {account}"), heading
