"""`scripts/lab_capture.py` refuses a symlinked `WTF/` or `WTF/Account/` (M13-61).

Discovery skipped a symlinked folder, but the planner followed it, so with
`_retail_/WTF` a link to a folder outside the install the account folder and
the `accountName` value were captured unscrubbed with exit 0. The capture now
stops before anything is written, and says how to capture such a tree.

CONSTRUCTED INPUT. Every tree is synthetic, built in `tmp_path` with invented
names. No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_lab_capture import BUILD_INFO, _write, capture, outputs

ACCOUNT = "123456789#1"
ACCOUNT_NAME = "SECRETACCT"
OWN_GUID = "Player-1234-0ABCDEF0"
REALM = "Area 52"
CHARACTER = "Brakkazul"
SV = "Ledger.lua"

pytestmark = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks"
)


def _wtf(wtf: Path) -> None:
    _write(wtf / "Config.wtf", f'SET portal "US"\r\nSET accountName "{ACCOUNT_NAME}"\r\n')
    account = wtf / "Account" / ACCOUNT
    _write(account / "config-cache.wtf", f'SET lastCharacterGuid "{OWN_GUID}"\n')
    _write(
        account / REALM / CHARACTER / "SavedVariables" / SV,
        f'\nLedgerDB = {{\n\t["login"] = "{ACCOUNT_NAME}",\n\t["account"] = "{ACCOUNT}",\n}}\n',
    )


def _install(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "World of Warcraft"
    _write(root / ".build.info", BUILD_INFO)
    _write(root / "_retail_" / ".flavor.info", "Product Flavor!STRING:0\nwow\n")
    elsewhere = tmp_path / "OUTSIDETARGET" / "WTF"
    _wtf(elsewhere)
    return root, elsewhere


def _assert_refused(code: int, out: Path, capsys: pytest.CaptureFixture[str], where: str) -> None:
    printed = capsys.readouterr()
    text = printed.out + printed.err
    assert code == 2
    assert not out.exists() or outputs(out) == {}
    assert f"{where} is a symbolic link" in printed.err
    assert "nothing is captured" in printed.err
    # Says how to capture it instead: `.build.info` too, or every provenance
    # row reads `client_version = unknown`.
    err = " ".join(printed.err.split())
    assert "copy `.build.info` and the flavor folders into a new folder" in err
    assert "`cp -RL`" in err and "pass that folder as --root" in err
    assert "staging" not in err  # the help text's word for --out
    assert ACCOUNT_NAME not in text and ACCOUNT not in text
    assert "OUTSIDETARGET" not in text  # the link's target is never printed


def test_constructed_symlinked_wtf_stops_the_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, elsewhere = _install(tmp_path)
    (root / "_retail_" / "WTF").symlink_to(elsewhere, target_is_directory=True)
    out = tmp_path / "incoming"
    code = capture(root, out, "--flavor", "_retail_", "--sv", SV)
    _assert_refused(code, out, capsys, "_retail_/WTF")


def test_constructed_symlinked_wtf_in_another_case_stops_the_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real `wtf/` beside a linked `WTF/`: the planner would take the link."""
    root, elsewhere = _install(tmp_path)
    (root / "_retail_" / "wtf").mkdir()
    (root / "_retail_" / "WTF").symlink_to(elsewhere, target_is_directory=True)
    out = tmp_path / "incoming"
    code = capture(root, out, "--flavor", "_retail_", "--sv", SV)
    _assert_refused(code, out, capsys, "_retail_/WTF")


def test_constructed_symlinked_wtf_account_stops_the_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, elsewhere = _install(tmp_path)
    wtf = root / "_retail_" / "WTF"
    _write(wtf / "Config.wtf", f'SET accountName "{ACCOUNT_NAME}"\r\n')
    (wtf / "Account").symlink_to(elsewhere / "Account", target_is_directory=True)
    out = tmp_path / "incoming"
    code = capture(root, out, "--flavor", "_retail_", "--sv", SV)
    _assert_refused(code, out, capsys, "_retail_/WTF/Account")


def test_constructed_symlinked_wtf_in_an_unselected_folder_also_stops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The identity map spans every folder; a link in one leaves it incomplete."""
    root, elsewhere = _install(tmp_path)
    _wtf(root / "_retail_" / "WTF")
    _write(root / "_classic_" / ".flavor.info", "Product Flavor!STRING:0\nwow_classic\n")
    (root / "_classic_" / "WTF").symlink_to(elsewhere, target_is_directory=True)
    out = tmp_path / "incoming"
    code = capture(root, out, "--flavor", "_retail_", "--sv", SV)
    _assert_refused(code, out, capsys, "_classic_/WTF")


def test_constructed_real_wtf_still_captures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same tree with a real WTF folder captures, scrubbed (the control)."""
    root, _ = _install(tmp_path)
    _wtf(root / "_retail_" / "WTF")
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--sv", SV) == 0
    written = outputs(out)
    assert written
    for dest, data in written.items():
        assert ACCOUNT_NAME not in dest and ACCOUNT_NAME.encode() not in data, dest
        assert ACCOUNT not in dest and ACCOUNT.encode() not in data, dest
    capsys.readouterr()
