# Probe from review of m12/09-char-list; reproduces `char list --account A` answering "no account folder 'A'" (exit 2) when A is a link or WTF/Account/ cannot be listed, instead of naming what it could not look at (exit 1)
"""Round 2 of the M12-09 review.

The conductor's ruling for this ticket, written into the branch's §14.4
amendment: "Whatever wowlab could not look at is named and makes `char
list` exit 1", with a link worded "not followed". Without `--account` the
branch does this (positive control): a linked account folder is named on
stderr as "a link, not followed" and the command exits 1, and so is an
unlistable `WTF/Account/`.

With `--account`, `char list` first resolves the name through
`cli._select_account`, which sees only `Layout.accounts()`: a linked
account folder is not there (links are never followed), and nothing is when
`WTF/Account/` cannot be listed. So `char list --account 90000002#1`, with
that account folder a link, prints "no account folder '90000002#1'
(accounts: 90000001#6)" and exits 2; with `WTF/Account/` unreadable,
`--account 90000001#6` prints "no account folder '90000001#6' (accounts:
none)" and exits 2. Both say the folder does not exist; neither names the
link or the folder wowlab could not list, which is what the owner needs to
act on.

These tests ask only what the ruling asks: the place is named on stderr
and the command exits 1.

Constructed (L8): the `macos` capture copied into `tmp_path`, with a second
account folder added as a link to a copy outside the flavor folder, or
`WTF/Account/` made unreadable. Nothing reads or writes a real install.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)
ACCOUNT = "90000001#6"
LINKED = "90000002#1"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def _linked_account(flavor: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside-account"
    shutil.copytree(flavor / "WTF/Account" / ACCOUNT, outside)
    try:
        (flavor / "WTF/Account" / LINKED).symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def test_constructed_positive_control_linked_account_named_without_the_option(
    flavor: Path, tmp_path: Path
) -> None:
    _linked_account(flavor, tmp_path)
    got = run("char", "list")
    assert got.exit_code == 1, got.output
    assert f"WTF/Account/{LINKED}" in got.stderr and "not followed" in got.stderr


def test_constructed_account_option_naming_a_linked_account_names_the_link(
    flavor: Path, tmp_path: Path
) -> None:
    _linked_account(flavor, tmp_path)
    got = run("char", "list", "--account", LINKED)
    assert "not followed" in got.stderr, (got.exit_code, got.stderr)
    assert got.exit_code == 1, (got.exit_code, got.stderr)


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permissions, and not as root",
)
def test_constructed_account_option_with_accounts_folder_unlistable_names_it(
    flavor: Path,
) -> None:
    accounts = flavor / "WTF/Account"
    accounts.chmod(0)
    try:
        plain = run("char", "list")
        named = run("char", "list", "--account", ACCOUNT)
    finally:
        accounts.chmod(0o755)
    # Positive control: without the option the folder is named, exit 1.
    assert plain.exit_code == 1 and "could not look inside WTF/Account" in plain.stderr
    assert "could not look inside WTF/Account" in named.stderr, (named.exit_code, named.stderr)
    assert named.exit_code == 1, (named.exit_code, named.stderr)
