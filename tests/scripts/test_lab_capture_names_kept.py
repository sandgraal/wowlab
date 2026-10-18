"""`scripts/lab_capture.py` after the ADR-0019 amendment of 2026-10-17 (M13-49).

Character names, realm names and the Forever second name are visible to anyone
in the game, so the scrub keeps them as they are, in paths and in contents,
and no longer refuses a file because one of them sits inside a longer word or
a SavedVariables file's own name. What identifies the account stays scrubbed
and refused: the account folder, an email address, a BattleTag, and the
owner's own player GUIDs (an unmapped player GUID refuses the file).

CONSTRUCTED INPUT. Every tree is synthetic, built in `tmp_path` in both
account layouts, with invented names. No test needs or touches a real install
(ADR-0012).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_lab_capture import BUILD_INFO, _write, capture, outputs

ACCOUNT = "123456789#1"
OWN_GUID = "Player-1234-0ABCDEF0"
CHARACTER = "Brakkazul"
REALM = "Area 52"
FIRST, SECOND = "Alyra", "Qorvenhollow"  # a Forever `<First>-<Second>` folder
EMAIL = "someone.real@example.com"
BATTLETAG = "Stranger#12345"
FOREIGN_GUID = "Player-77-0BADBEEF"


def _tree(root: Path) -> tuple[Path, Path]:
    """A retail flavor (`<account>/<Realm>/<Name>/`) and a Forever flavor
    (`<account>/<digits>/<First>-<Second>/`). Returns both account folders."""
    _write(root / ".build.info", BUILD_INFO)
    accounts = []
    for flavor, product in (("_retail_", "wow"), ("_classic_beta_", "wow_classic_beta")):
        base = root / flavor
        _write(base / ".flavor.info", f"Product Flavor!STRING:0\n{product}\n")
        _write(base / "WTF" / "Config.wtf", f'SET realmName "{REALM}"\r\nSET portal "US"\r\n')
        account = base / "WTF" / "Account" / ACCOUNT
        _write(account / "config-cache.wtf", f'SET lastCharacterGuid "{OWN_GUID}"\n')
        accounts.append(account)
    retail, forever = accounts
    _write(
        retail / REALM / CHARACTER / "SavedVariables" / f"{CHARACTER}Tracker.lua",
        f'\n{CHARACTER}TrackerDB = {{\n\t["{CHARACTER} - {REALM}"] = "{CHARACTER}sBank",\n'
        f'\t["guid"] = "{OWN_GUID}",\n\t["account"] = "{ACCOUNT}",\n}}\n',
    )
    _write(retail / REALM / CHARACTER / "chat-cache.txt", f"CHANNEL {CHARACTER}\n")
    character = forever / "70" / f"{FIRST}-{SECOND}"
    _write(
        character / "layout-local.txt",
        f"Frame: {FIRST} - {SECOND} {FIRST}-{SECOND} {SECOND}Bars Qorven Hollow\n",
    )
    _write(forever / "character-list-order.txt", f"{FIRST}-{SECOND}\nZedalt-Faerlina\n")
    return retail, forever


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "World of Warcraft"
    retail, forever = _tree(root)
    return root, retail, forever


def test_constructed_character_realm_and_second_name_pass_through_untouched(
    tree: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _retail, forever = tree
    out = tmp_path / "incoming"
    sv = f"{CHARACTER}Tracker.lua"
    assert capture(root, out, "--sv", sv) == 0
    written = outputs(out)
    retail_out = f"macos/_retail_/WTF/Account/90000001#1/{REALM}/{CHARACTER}"
    forever_out = f"macos/_classic_beta_/WTF/Account/90000001#1/1/{FIRST}-{SECOND}"

    # Paths: the realm, character and <First>-<Second> folders keep their names;
    # the account folder still becomes its pseudonym, the digits group "1".
    assert f"{retail_out}/chat-cache.txt" in written
    assert f"{retail_out}/SavedVariables/{sv}" in written  # a name in its own file name
    assert f"{forever_out}/layout-local.txt" in written

    # Contents: every name byte for byte, inside longer words included; the
    # account and the own GUID inside the same file are still rewritten.
    assert written[f"{retail_out}/chat-cache.txt"] == f"CHANNEL {CHARACTER}\n".encode()
    tracker = written[f"{retail_out}/SavedVariables/{sv}"]
    assert (
        tracker
        == (
            f'\n{CHARACTER}TrackerDB = {{\n\t["{CHARACTER} - {REALM}"] = "{CHARACTER}sBank",\n'
            '\t["guid"] = "Player-9999-00000001",\n\t["account"] = "90000001#1",\n}\n'
        ).encode()
    )
    source = forever / "70" / f"{FIRST}-{SECOND}" / "layout-local.txt"
    assert written[f"{forever_out}/layout-local.txt"] == source.read_bytes()
    listing = written["macos/_classic_beta_/WTF/Account/90000001#1/character-list-order.txt"]
    assert listing == (forever / "character-list-order.txt").read_bytes()

    # realmName stays on the CVar deny-list and is still blanked.
    assert written["macos/_retail_/WTF/Config.wtf"] == b'SET realmName ""\r\nSET portal "US"\r\n'

    # Nothing that identifies the account survives, in any file, path or line printed.
    printed = capsys.readouterr()
    for dest, data in written.items():
        for secret in (ACCOUNT, OWN_GUID):
            assert secret not in dest and secret.encode() not in data, (dest, secret)
    assert ACCOUNT not in printed.out + printed.err
    rows = [line for line in printed.out.splitlines() if line.startswith("| `")]
    tracker_row = next(row for row in rows if sv in row)
    assert "identity-rewritten: 1" in tracker_row and "guids-rewritten: 1" in tracker_row


@pytest.mark.parametrize(
    ("body", "label"),
    [
        (f'\nLeakDB = {{\n\t["contact"] = "{EMAIL}",\n}}\n', "email address"),
        (f'\nLeakDB = {{\n\t["friend"] = "{BATTLETAG}",\n}}\n', "BattleTag"),
        (f'\nLeakDB = {{\n\t["seen"] = "{FOREIGN_GUID}",\n}}\n', "unmapped player GUID"),
        (
            '\nLeakDB = {\n\t["acct"] = "123456789#1x",\n}\n',
            "identity string inside a longer word",
        ),
    ],
    ids=["email", "battletag", "unmapped-guid", "account-folder-glued"],
)
def test_constructed_kept_identifiers_still_refuse_beside_a_kept_name(
    body: str,
    label: str,
    tree: tuple[Path, Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refusals for what identifies the account are unchanged by M13-49,
    in a file whose character and realm names are now kept."""
    root, retail, _forever = tree
    saved = retail / REALM / CHARACTER / "SavedVariables"
    _write(
        saved / "Leak.lua",
        body.replace("LeakDB = {\n", f'LeakDB = {{\n\t["who"] = "{CHARACTER}",\n'),
    )
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--sv", "Leak.lua") == 1
    assert not [d for d in outputs(out) if d.endswith("Leak.lua")]
    printed = capsys.readouterr()
    refused = [line for line in printed.out.splitlines() if line.startswith("REFUSED")]
    assert len(refused) == 1 and "Leak.lua" in refused[0] and label in refused[0], refused
    for secret in (EMAIL, BATTLETAG, FOREIGN_GUID, ACCOUNT):
        assert secret not in printed.out + printed.err


def test_constructed_own_guid_is_still_rewritten_and_the_name_beside_it_kept(
    tree: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """A combat log: the owner's GUID (from lastCharacterGuid) is rewritten, the
    unit name next to it is public and stays; another player's GUID refuses."""
    root, _retail, _forever = tree
    logs = root / "_retail_" / "Logs"
    header = b"9/20/2026 21:14:03.123-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
    own = (
        f'9/20/2026 21:14:05.871-4  SPELL_DAMAGE,{OWN_GUID},"{CHARACTER}-Area52-US",0x511,0x0,'
        'Creature-0-1-2-3-4-0000000000,"Training Dummy",0x10a48,0x0,585,"Smite",0x2\n'
    ).encode()
    _write(logs / "WoWCombatLog-092026_211403.txt", (header + own).decode())
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_") == 0
    log = next(data for dest, data in outputs(out).items() if "WoWCombatLog" in dest)
    assert f'Player-9999-00000001,"{CHARACTER}-Area52-US"'.encode() in log
    assert OWN_GUID.encode() not in log

    foreign = (
        f'9/20/2026 21:15:00.000-4  SPELL_HEAL,{FOREIGN_GUID},"Bystander-Illidan-US",0x514\n'
    ).encode()
    _write(logs / "WoWCombatLog-092026_211403.txt", (header + own + foreign).decode())
    refused = tmp_path / "refused"
    assert capture(root, refused, "--flavor", "_retail_") == 1
    assert not [d for d in outputs(refused) if "WoWCombatLog" in d]
