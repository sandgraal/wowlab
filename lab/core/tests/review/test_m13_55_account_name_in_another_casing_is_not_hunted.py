# Probe from review of m13/55-capture-hunts-identity-values; reproduces an accountName value that is an older account folder's name in another casing being dropped from the identity map, so a SavedVariables file holding it is refused instead of rewritten.
"""M13-55 adds each `accountName` value to the identity map as an account
string, "rewritten in contents, refused glued into a longer word". The branch
leaves out a value whose casefold equals an account folder's name, on the
reading that it "is that folder's" (the `Identity.__init__` docstring). But the
folder registers only its own spelling, its lower case and its upper case. An
older account-name folder is `SECRETACCT`; the value the client keeps in
`Config.wtf` can be `SecretAcct`. That spelling is then hunted by nobody: a
SavedVariables file that holds it is REFUSED (exit 1). The same value beside a
numbered folder is rewritten (the positive control), so the fold, not the
value, is what stops the rewrite.

A second, smaller symptom of the same dedup: two `accountName` values that are
one account in two casings (one in `Config.wtf`, one in a `config-cache.wtf`)
are counted as two accounts, since only CVar-against-folder is folded.

Expected: the value is rewritten with the folder's pseudonym in some casing,
the capture exits 0, and one account is one account in the count.

CONSTRUCTED INPUT, invented names, built in `tmp_path`. No real install
(ADR-0012).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests" / "scripts"))
from test_lab_capture import BUILD_INFO, _write, capture, lab_capture, outputs  # noqa: E402

SV = "Ledger.lua"
VALUE = "SecretAcct"


def _run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], folder: str
) -> tuple[int, dict[str, bytes]]:
    root = tmp_path / "World of Warcraft"
    _write(root / ".build.info", BUILD_INFO)
    base = root / "_retail_"
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow\n")
    _write(base / "WTF" / "Config.wtf", f'SET accountName "{VALUE}"\n')
    account = base / "WTF" / "Account" / folder
    _write(account / "config-cache.wtf", 'SET lastCharacterGuid "Player-1234-0ABCDEF0"\n')
    _write(
        account / "Area 52" / "Brakkazul" / "SavedVariables" / SV,
        f'\nLedgerDB = {{\n\t["login"] = "{VALUE}",\n}}\n',
    )
    out = tmp_path / "incoming"
    code = capture(root, out, "--flavor", "_retail_", "--sv", SV)
    capsys.readouterr()
    return code, {k: v for k, v in outputs(out).items() if k.endswith(SV)}


def test_constructed_control_value_beside_a_numbered_folder_is_rewritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, written = _run(tmp_path, capsys, "123456789#1")
    assert code == 0
    [data] = written.values()
    assert VALUE.encode() not in data and b"labaccount" in data.lower()


def test_constructed_value_in_another_casing_than_its_folder_is_rewritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, written = _run(tmp_path, capsys, VALUE.upper())
    assert code == 0, "the SavedVariables file was refused, not rewritten"
    [data] = written.values()
    assert VALUE.encode() not in data and b"labaccounta" in data.lower()


def test_constructed_one_account_in_two_casings_is_counted_once() -> None:
    identity = lab_capture.Identity(account_names=[VALUE, VALUE.lower()])
    assert identity.counts["account"] == 1
