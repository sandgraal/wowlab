"""`scripts/lab_capture.py` hunts the value of `accountName` in every file (M13-55).

The scrub blanks `accountName` in `Config.wtf`; before M13-55 the same value
in a SavedVariables file was written out unchanged with exit 0. The value now
joins the identity map as an account, the way the account folder does:
rewritten in contents, refused glued into a longer word. An email address is
left to the email refusal. `accountList` is still blanked but not hunted: its
separator is not in the format reference and no unblanked capture shows it
([verify]).

CONSTRUCTED INPUT. Every tree is synthetic, built in `tmp_path` with invented
names. No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_lab_capture import BUILD_INFO, _write, capture, lab_capture, outputs

ACCOUNT = "123456789#1"
ACCOUNT_NAME = "SECRETACCT"
OWN_GUID = "Player-1234-0ABCDEF0"
REALM = "Area 52"
CHARACTER = "Brakkazul"
SV = "Ledger.lua"
SV_OUT = f"macos/_retail_/WTF/Account/90000001#1/{REALM}/{CHARACTER}/SavedVariables/{SV}"


def _tree(root: Path, config: str, value: str) -> None:
    _write(root / ".build.info", BUILD_INFO)
    base = root / "_retail_"
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow\n")
    _write(base / "WTF" / "Config.wtf", config)
    account = base / "WTF" / "Account" / ACCOUNT
    _write(account / "config-cache.wtf", f'SET lastCharacterGuid "{OWN_GUID}"\n')
    _write(
        account / REALM / CHARACTER / "SavedVariables" / SV,
        f'\nLedgerDB = {{\n\t["login"] = "{value}",\n\t["account"] = "{ACCOUNT}",\n}}\n',
    )


def _run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], config: str, value: str
) -> tuple[int, dict[str, bytes], str]:
    root = tmp_path / "World of Warcraft"
    _tree(root, config, value)
    out = tmp_path / "incoming"
    code = capture(root, out, "--flavor", "_retail_", "--sv", SV)
    printed = capsys.readouterr()
    return code, outputs(out), printed.out + printed.err


def _row(printed: str) -> str:
    return next(line for line in printed.splitlines() if line.startswith("| `") and SV in line)


CONFIG = f'SET portal "US"\r\nSET accountName "{ACCOUNT_NAME}"\r\nSET gxMaximize "1"\r\n'


def test_constructed_account_name_is_blanked_in_config_and_rewritten_in_savedvariables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, written, printed = _run(tmp_path, capsys, CONFIG, ACCOUNT_NAME)
    assert code == 0
    # Blanked in Config.wtf, every other byte kept.
    assert written["macos/_retail_/WTF/Config.wtf"] == (
        b'SET portal "US"\r\nSET accountName ""\r\nSET gxMaximize "1"\r\n'
    )
    # Rewritten in the SavedVariables value, as the account folder is beside it.
    assert written[SV_OUT] == (
        b'\nLedgerDB = {\n\t["login"] = "LABACCOUNTB",\n\t["account"] = "90000001#1",\n}\n'
    )
    for dest, data in written.items():
        assert ACCOUNT_NAME not in dest and ACCOUNT_NAME.encode() not in data, dest
    assert ACCOUNT_NAME not in printed
    assert "(2 accounts," in printed
    assert "identity-rewritten: 2" in _row(printed)


def test_constructed_without_the_cvar_the_count_is_one_lower(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The baseline for the test above: the same tree with `accountName` blank.
    The provenance row counts one identity rewrite fewer."""
    config = CONFIG.replace(f'"{ACCOUNT_NAME}"', '""')
    code, written, printed = _run(tmp_path, capsys, config, "Brakkazul")
    assert code == 0
    assert b'["login"] = "Brakkazul"' in written[SV_OUT]
    assert "(1 accounts," in printed
    assert "identity-rewritten: 1" in _row(printed)


def test_constructed_account_name_glued_into_a_longer_word_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, written, printed = _run(tmp_path, capsys, CONFIG, f"{ACCOUNT_NAME}x")
    assert code == 1
    assert SV_OUT not in written
    refused = [line for line in printed.splitlines() if line.startswith("REFUSED")]
    assert len(refused) == 1 and SV in refused[0], refused
    assert lab_capture.EMBEDDED_LABEL in refused[0], refused
    assert ACCOUNT_NAME not in printed


def test_constructed_account_name_in_config_cache_is_hunted_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Any config file the harvest reads, not only the machine-wide one."""
    root = tmp_path / "World of Warcraft"
    _tree(root, 'SET portal "US"\r\n', ACCOUNT_NAME)
    cache = root / "_retail_" / "WTF" / "Account" / ACCOUNT / "config-cache.wtf"
    _write(cache, f'SET lastCharacterGuid "{OWN_GUID}"\nSET accountName "{ACCOUNT_NAME}"\n')
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--sv", SV) == 0
    assert ACCOUNT_NAME.encode() not in outputs(out)[SV_OUT]


def test_constructed_an_email_account_name_is_left_to_the_email_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    email = "someone.real@example.com"
    config = CONFIG.replace(ACCOUNT_NAME, email)
    code, written, printed = _run(tmp_path, capsys, config, email)
    assert code == 1
    assert SV_OUT not in written
    refused = [line for line in printed.splitlines() if line.startswith("REFUSED")]
    assert len(refused) == 1 and "email address" in refused[0], refused
    assert written["macos/_retail_/WTF/Config.wtf"].count(b'SET accountName ""') == 1
    assert email not in printed and "(1 accounts," in printed


def test_constructed_account_name_shares_the_pseudonym_of_its_folder() -> None:
    """An older account-name folder and its `accountName` in another casing are
    one account; adding a CVar value never renumbers a folder's pseudonym."""
    same = lab_capture.Identity(accounts=["SECRETACCT"], account_names=["SecretAcct"])
    assert same.counts["account"] == 1 and len(same.names) == 1
    assert same.scrub(b'"SecretAcct"').data == b'"LABACCOUNTA"'
    assert same.scrub(b'"secretacct"').data == b'"labaccounta"'
    ordered = lab_capture.Identity(accounts=[ACCOUNT], account_names=["0first"])
    assert ordered.names[ACCOUNT] == "90000001#1"
    assert ordered.counts["account"] == 2


def test_constructed_one_account_in_two_config_files_and_two_casings_counts_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    _tree(root, 'SET accountName "SecretAcct"\r\n', "SECRETACCT secretacct SecretAcct")
    cache = root / "_retail_" / "WTF" / "Account" / ACCOUNT / "config-cache.wtf"
    _write(cache, f'SET lastCharacterGuid "{OWN_GUID}"\nSET accountName "secretacct"\n')
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--sv", SV) == 0
    printed = capsys.readouterr().out
    assert "identity map: 2 names (2 accounts," in printed
    data = outputs(out)[SV_OUT]
    assert b'"LABACCOUNTB labaccountb LABACCOUNTb"' in data, data


def test_constructed_a_non_utf8_account_name_stops_the_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    _tree(root, 'SET portal "US"\r\n', "x")
    config = root / "_retail_" / "WTF" / "Config.wtf"
    config.write_bytes(b'SET accountName "SECRET\xffACCT"\r\n')
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--sv", SV) == 2
    printed = capsys.readouterr()
    assert "accountName value in a config file is not UTF-8" in printed.err
    assert "nothing is captured" in printed.err
    assert "SECRET" not in printed.out + printed.err
    assert not out.exists()


@pytest.mark.parametrize("spelling", ["Jo-Al", "JoAl", "jo_al"])
def test_constructed_a_short_account_name_with_a_separator_stops_the_capture(
    spelling: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under EMBEDDED_MIN_CHARS letters only the spelling as written is hunted,
    so `Jo Al` would let `Jo-Al` and `JoAl` through: the capture stops instead."""
    code, written, printed = _run(tmp_path, capsys, 'SET accountName "Jo Al"\r\n', spelling)
    assert code == 2 and written == {}
    assert "short and has a separator in it" in printed and "nothing is captured" in printed
    assert "Jo Al" not in printed


@pytest.mark.parametrize("spelling", ["Real-Name", "RealName", "real_name"])
def test_constructed_a_long_account_name_in_another_spelling_refuses(
    spelling: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Five letters or more are hunted with their separators dropped."""
    code, written, printed = _run(tmp_path, capsys, 'SET accountName "Real Name"\r\n', spelling)
    assert code == 1 and SV_OUT not in written
    refused = [line for line in printed.splitlines() if line.startswith("REFUSED")]
    assert len(refused) == 1 and SV in refused[0], refused


def test_constructed_the_name_beside_an_email_in_account_name_is_hunted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`Real Name <a@b.c>`: the address is left to the email refusal, the name is
    rewritten like any account string."""
    config = 'SET accountName "Secret Owner <someone.real@example.com>"\r\n'
    code, written, printed = _run(tmp_path, capsys, config, "Secret Owner")
    assert code == 0
    assert b'["login"] = "LABACCOUNTb Partb"' in written[SV_OUT], written[SV_OUT]
    assert "Secret Owner" not in printed and "someone.real" not in printed
    assert written["macos/_retail_/WTF/Config.wtf"] == b'SET accountName ""\r\n'


@pytest.mark.parametrize(
    ("value", "name"),
    [
        ("«Secret Owner» someone.real@example.com", "Secret Owner"),
        ("“Secret Owner” <someone.real@example.com>", "Secret Owner"),
        ("Secret Owner | someone.real@example.com", "Secret Owner"),
        ("Jo. someone.real@example.com", "Jo"),
        ("Jo» someone.real@example.com", "Jo"),
        ("Jo! someone.real@example.com", "Jo"),
    ],
    ids=["guillemets", "curly-quotes", "pipe", "full-stop", "closing-guillemet", "bang"],
)
def test_constructed_any_punctuation_around_the_name_beside_an_email_is_trimmed(
    value: str, name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, written, printed = _run(tmp_path, capsys, f'SET accountName "{value}"\r\n', name)
    assert code == 0
    assert name.encode() not in written[SV_OUT], written[SV_OUT]
    assert b"LABACCOUNT" in written[SV_OUT].upper()
    assert name not in printed and "someone.real" not in printed


@pytest.mark.parametrize("value", ["Jo.Al", "Secret|Owner", "Se·cret"])
def test_constructed_punctuation_inside_an_account_name_stops_the_capture(
    value: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed: what stays inside the hunted token would hide the bare name."""
    code, written, printed = _run(tmp_path, capsys, f'SET accountName "{value}"\r\n', "x")
    assert code == 2 and written == {}
    assert "inside a name" in printed and "nothing is captured" in printed
    assert value not in printed
