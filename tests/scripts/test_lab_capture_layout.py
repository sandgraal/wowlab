"""`scripts/lab_capture.py`: the Forever beta account layout (M10-02, found by M10-03).

The tree is built here, in `tmp_path`, in the shape the owner's first capture
reported: characters under `<account>/<digits>/<First>-<Second>/` (a first and
a second name, both player-chosen), retail-style
`<account>/<Realm>/<First>/AddOns.txt` twins beside them, and SavedVariables
folders outside any account. Every name is invented and every input is
constructed. No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_lab_capture import ACCOUNT, BUILD_INFO, OWN_GUID, _write, capture, lab_capture, outputs

Identity = lab_capture.Identity

FLAVOR = "_classic_beta_"
OUT_ACCOUNT = f"macos/{FLAVOR}/WTF/Account/90000001#1"
MAIN_DIR = f"{OUT_ACCOUNT}/1/Labchara-Labrealmb"  # 70/Alyra-Bloodfist: the group gets "1"
TWIN_DIR = f"{OUT_ACCOUNT}/Labrealmb/Labchara"  # Bloodfist/Alyra
REAL = ("Alyra", "Bloodfist", "Glade", "Moon", "Area 52", "Area52", "area52", "alyra", ACCOUNT)

CHAT_CACHE = "COLORS\nGUILD 64 255 64\nSAY 255 255 255\nWHISPER 170 170 255\nEND\n"
EDIT_MODE = "anchor BOTTOM -270.0 170.5\n"
DBM = (
    "\nDBM_Settings = {\n"
    '\t["1703"] = true,\n'
    '\t["mapIds"] = {\n\t\t1703, -- [1]\n\t\t170, -- [2]\n\t\t70, -- [3]\n\t},\n'
    "}\n"
    "DBM_Chars = {\n"
    '\t["Alyra - Bloodfist"] = 1,\n'
    '\t["Alyra - Area 52"] = 2,\n'
    "}\n"
)
OLD = 1_000_000_000


def _age(*paths: Path) -> None:
    for path in paths:
        for entry in [*path.rglob("*"), path]:
            os.utime(entry, (OLD, OLD))


def build_forever(root: Path) -> Path:
    """One flavor, one account, both character shapes. Returns the account folder."""
    _write(root / ".build.info", BUILD_INFO)
    base = root / FLAVOR
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow_classic_beta\n")
    wtf = base / "WTF"
    # A realm id that is only digits is no pseudonym source (and is blanked anyway).
    _write(wtf / "Config.wtf", 'SET portal "us"\nSET realmName "70"\n')
    _write(wtf / "SavedVariables" / "Machine.lua", "\nMachineFlag = true\n")
    _write(wtf / "GamePadConfig_Default.json", "{}\n")
    _write(wtf / "gamecontrollerdb.txt", "# none\n")
    _write(wtf / "Account" / "SavedVariables" / "AllAccounts.lua", "\nAllFlag = true\n")

    account = wtf / "Account" / ACCOUNT
    _write(account / "config-cache.wtf", f'SET lastCharacterGuid "{OWN_GUID}"\n')
    _write(account / "bindings-cache.wtf", "bind CTRL-1 ACTIONBUTTON1\n")
    _write(account / "macros-cache.txt", "")
    _write(account / "character-list-order.txt", "Alyra-Bloodfist\nAlyra-Area52\nMoon-Glade\n")
    _write(account / "chat-frontend-cache.txt", "GUILD 1\nWHISPER 170 170 255\n")
    _write(account / "flagged-cache-account.txt", "1\n")
    _write(account / "tts-cache-account.txt", "voice 1\n")
    _write(account / "edit-mode-cache-account.txt", EDIT_MODE)
    for old in ("bindings-cache.old", "config-cache.old", "macros-cache.old"):
        _write(account / old, "old\n")
    for old in ("flagged-cache-account.txt.old", "edit-mode-cache-account.old", "cache.md5"):
        _write(account / old, "old\n")
    _write(account / "edit-mode-cache-account.txt.old", "old\n")
    _write(account / "SavedVariables" / "Tiny.lua", "\nTinyFlag = true\n")

    group = account / "70"
    for folder in ("Alyra-Bloodfist", "Alyra-Area52", "Moon-Glade"):
        character = group / folder
        _write(character / "config-cache.wtf", 'SET chatBubbles "1"\n')
        _write(character / "config-cache.old", "old\n")
        _write(character / "chat-cache.txt", CHAT_CACHE)
        _write(character / "cache.md5", "0123\n")
        _write(character / "click-bindings-cache.txt", "BUTTON1 target\n")
        _write(character / "click-bindings-cache.txt.old", "old\n")
        _write(character / "edit-mode-cache-character.txt", EDIT_MODE)
        _write(character / "edit-mode-cache-character.old", "old\n")
        _write(character / "flagged-cache-character.txt", "0\n")
        _write(character / "flagged-cache-character.old", "old\n")
        _write(character / "tts-cache-character.txt", "rate 0\n")
        _write(character / "layout-local.txt", "Frame: x\n")
        _write(character / "macros-cache.txt", "")
        _write(character / "SavedVariables" / "DBM-Core.lua", DBM)
    _write(group / "Alyra-Bloodfist" / "bindings-cache.wtf", "bind BUTTON4 TOGGLEAUTORUN\n")
    # The retail-shaped twins hold only AddOns.txt.
    _write(account / "Bloodfist" / "Alyra" / "AddOns.txt", "Solo: enabled\n")
    _write(account / "Area 52" / "Alyra" / "AddOns.txt", "Solo: disabled\n")
    _age(group / "Alyra-Area52", group / "Moon-Glade", account / "Area 52")
    return account


@pytest.fixture
def forever(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_forever(root)
    return root


def _rows(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("| `")]


# ─── A and B: discovery, pseudonyms, paths ───────────────────────────────────


def test_constructed_forever_layout_end_to_end(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out, "--sv", "DBM-Core.lua") == 0
    stdout = capsys.readouterr().out
    written = outputs(out)

    # The digits-only group is a grouping level: not a name, not a content token,
    # and a path-only pseudonym ("1") in case it is a realm id.
    assert (
        "identity map: 6 names (1 accounts, 3 realms, 2 characters, 0 extra), "
        "1 own GUIDs, 8 CVars blanked"
    ) in stdout
    expected = {
        f"{MAIN_DIR}/{name}"
        for name in (
            "config-cache.wtf",
            "bindings-cache.wtf",
            "macros-cache.txt",
            "layout-local.txt",
            "chat-cache.txt",
            "click-bindings-cache.txt",
            "flagged-cache-character.txt",
            "tts-cache-character.txt",
            "edit-mode-cache-character.txt",
            "SavedVariables/DBM-Core.lua",
        )
    } | {
        f"{TWIN_DIR}/AddOns.txt",
        f"{OUT_ACCOUNT}/character-list-order.txt",
        f"{OUT_ACCOUNT}/chat-frontend-cache.txt",
        f"{OUT_ACCOUNT}/flagged-cache-account.txt",
        f"{OUT_ACCOUNT}/tts-cache-account.txt",
        f"{OUT_ACCOUNT}/edit-mode-cache-account.txt",
    }
    assert expected <= set(written), sorted(expected - set(written))
    assert not [d for d in written if d.endswith((".old", ".md5"))], "no .old twin, no cache.md5"
    assert not [d for d in written if "Labchara-LabrealmaPartb" in d or "Labcharb" in d]

    # Numbers that contain the group id survive byte for byte.
    assert written[f"{MAIN_DIR}/chat-cache.txt"] == CHAT_CACHE.encode()
    assert written[f"{MAIN_DIR}/edit-mode-cache-character.txt"] == EDIT_MODE.encode()
    assert written[f"{OUT_ACCOUNT}/edit-mode-cache-account.txt"] == EDIT_MODE.encode()
    assert written[f"{OUT_ACCOUNT}/chat-frontend-cache.txt"] == b"GUILD 1\nWHISPER 170 170 255\n"
    assert written[f"macos/{FLAVOR}/WTF/Config.wtf"] == b'SET portal "us"\nSET realmName ""\n'

    # "Name - Realm" inside a file is scrubbed through both halves, exactly as
    # the `<First>-<Second>` folder is, and the numbers beside it are untouched.
    assert written[f"{MAIN_DIR}/SavedVariables/DBM-Core.lua"] == (
        DBM.replace("Alyra - Bloodfist", "Labchara - Labrealmb")
        .replace("Alyra - Area 52", "Labchara - Labrealma Partb")
        .encode()
    )
    # The list of characters is scrubbed the same way, spaces-dropped realm included.
    assert written[f"{OUT_ACCOUNT}/character-list-order.txt"] == (
        b"Labchara-Labrealmb\nLabchara-LabrealmaPartb\nLabcharb-Labrealmc\n"
    )
    for dest, data in written.items():
        for real in REAL:
            assert real.encode() not in data and real not in dest, (real, dest)

    kinds = {row.split(" | ")[1] for row in _rows(stdout)}
    assert {
        "character-list-order",
        "chat-frontend-cache",
        "flagged-cache",
        "tts-cache",
        "click-bindings-cache",
        "edit-mode-cache",
    } <= kinds


def test_constructed_group_id_is_never_a_token_and_names_split_on_the_first_hyphen() -> None:
    assert lab_capture.is_group("70") and not lab_capture.is_group("7O")
    assert lab_capture.split_character_folder("Alyra-Azjol-Nerub") == ("Alyra", "Azjol-Nerub")
    assert lab_capture.split_character_folder("Alyra") is None
    identity = Identity(characters=["Alyra"], realms=["Azjol-Nerub"])
    result = identity.scrub(b'"Alyra-Azjol-Nerub" 170 -270.0 1703 70')
    assert result.data == b'"Labchara-Labrealma-Partb" 170 -270.0 1703 70'
    assert not result.problems


def test_constructed_account_savedvariables_folder_is_not_an_account(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out, "--dry-run") == 0
    stdout = capsys.readouterr().out
    assert "(1 accounts," in stdout
    assert f"{OUT_ACCOUNT}/SavedVariables/Tiny.lua" in stdout, "the word SavedVariables survives"


@pytest.mark.parametrize(
    "folder",
    ["70/Alyra-123", "70/123", "Bloodfist/123", "70/Alyra-1-2"],
)
def test_constructed_letterless_character_or_realm_folder_stops_the_run(
    folder: str, forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    account = forever / FLAVOR / "WTF" / "Account" / ACCOUNT
    _write(account.joinpath(*folder.split("/")) / "layout-local.txt", "x\n")
    out = tmp_path / "incoming"
    assert capture(forever, out) == 2
    text = capsys.readouterr()
    assert "has no letters" in text.err and "nothing is captured" in text.err
    assert not out.exists()
    for real in ("123", "1-2", "Alyra"):
        assert real not in text.err


def test_constructed_letterless_names_are_refused_as_pseudonym_sources() -> None:
    for kwargs in ({"realms": ["70"]}, {"characters": ["1234"]}, {"realms": ["52 - 70"]}):
        with pytest.raises(lab_capture.CaptureError, match="has no letters"):
            Identity(**kwargs)
    # A realm with a digit AND letters is fine, as before.
    assert Identity(realms=["Area 52"]).names == {"Area 52": "Labrealma Partb"}


# ─── C: choosing the character ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "wanted", ["Alyra-Area52", "alyra-area52", "Area 52/Alyra", "70/Alyra-Area52"]
)
def test_constructed_character_can_be_named_in_either_shape(
    wanted: str, forever: Path, tmp_path: Path
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out, "--character", wanted) == 0
    written = set(outputs(out))
    chosen = f"{OUT_ACCOUNT}/1/Labchara-LabrealmaPartb"
    assert f"{chosen}/chat-cache.txt" in written
    assert f"{chosen}/SavedVariables/DBM-Core.lua" in written or any(
        d.startswith(f"{chosen}/SavedVariables/") for d in written
    )
    assert f"{OUT_ACCOUNT}/Labrealma Partb/Labchara/AddOns.txt" in written  # the twin
    assert not [d for d in written if "Labchara-Labrealmb" in d or d.startswith(TWIN_DIR)]


def test_constructed_most_recent_character_is_picked_among_both_shapes(
    forever: Path, tmp_path: Path
) -> None:
    account = forever / FLAVOR / "WTF" / "Account" / ACCOUNT
    _age(account / "70", account / "Bloodfist")
    # A retail-shaped character with no twin, played last.
    _write(account / "Glade" / "Soloist" / "config-cache.wtf", 'SET x "1"\n')
    _write(account / "Glade" / "Soloist" / "AddOns.txt", "Solo: enabled\n")
    out = tmp_path / "incoming"
    assert capture(forever, out) == 0
    written = set(outputs(out))
    assert f"{OUT_ACCOUNT}/Labrealmc/Labcharc/config-cache.wtf" in written
    assert not [d for d in written if "/1/" in d or "/70/" in d]


# ─── E: --extra-name equal to the client's own vocabulary ────────────────────


@pytest.mark.parametrize(
    "word",
    [
        *[
            "SAY",
            "PARTY",
            "PARTY_LEADER",
            "RAID",
            "RAID_LEADER",
            "RAID_WARNING",
            "GUILD",
            "OFFICER",
            "WHISPER",
            "WHISPER_INFORM",
            "YELL",
            "EMOTE",
            "TEXT_EMOTE",
            "CHANNEL",
            "SYSTEM",
            "LOOT",
            "MONEY",
            "INSTANCE_CHAT",
            "INSTANCE_CHAT_LEADER",
            "BN_WHISPER",
            "ACHIEVEMENT",
            "GUILD_ACHIEVEMENT",
            "COMBAT_XP_GAIN",
            "COMBAT_HONOR_GAIN",
            "COMBAT_FACTION_CHANGE",
            "SKILL",
            "BG_SYSTEM_NEUTRAL",
            "OPENING",
            "TRADESKILLS",
            "PET_INFO",
            "COMBAT_MISC_INFO",
        ],
        "guild",
        "Party_Leader",
    ],
)
def test_constructed_extra_name_equal_to_client_vocabulary_stops_the_run(
    word: str, forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out, "--extra-name", word) == 2
    err = capsys.readouterr().err
    assert "--extra-name" in err and "the client writes itself" in err
    assert word not in err and not out.exists()


def test_constructed_extra_name_vocabulary_message_shows_the_value_only_with_show_map(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert capture(forever, tmp_path / "incoming", "--extra-name", "GUILD", "--show-map") == 2
    assert "'GUILD'" in capsys.readouterr().err
    # Reserved words keep their existing message, and an ordinary guild name still works.
    assert capture(forever, tmp_path / "incoming", "--extra-name", "nil") == 2
    assert "is a format keyword" in capsys.readouterr().err
    assert capture(forever, tmp_path / "incoming", "--dry-run", "--extra-name", "Guild Of X") == 0


# ─── round 2 of #33 ──────────────────────────────────────────────────────────


def test_constructed_grouped_spelling_in_another_casing_shares_the_realm_pseudonym(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Code review 3: folder `Vex-Stormrage` beside the twin `Storm Rage/Vex/`."""
    root = tmp_path / "World of Warcraft"
    _write(root / ".build.info", BUILD_INFO)
    base = root / FLAVOR
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow_classic_beta\n")
    account = base / "WTF" / "Account" / ACCOUNT
    _write(account / "config-cache.wtf", 'SET chatBubbles "1"\n')
    _write(account / "70" / "Vex-Stormrage" / "chat-cache.txt", "SAY 255 255 255\n")
    _write(account / "Storm Rage" / "Vex" / "AddOns.txt", "Solo: enabled\n")
    out = tmp_path / "incoming"
    assert capture(root, out) == 0
    stdout = capsys.readouterr().out
    assert "<path withheld>" not in stdout and "(1 accounts, 1 realms, 1 characters" in stdout
    written = set(outputs(out))
    assert f"{OUT_ACCOUNT}/1/Labchara-LabrealmaPartb/chat-cache.txt" in written
    assert f"{OUT_ACCOUNT}/Labrealma Partb/Labchara/AddOns.txt" in written


def test_constructed_character_list_naming_an_alt_without_a_folder_is_refused(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Security S2, end to end: the alt is in no folder, so nothing maps it."""
    listing = forever / FLAVOR / "WTF" / "Account" / ACCOUNT / "character-list-order.txt"
    listing.write_bytes(listing.read_bytes() + b"Zedalt-Faerlina\n")
    out = tmp_path / "incoming"
    assert capture(forever, out) == 1
    stdout = capsys.readouterr().out
    assert (
        f"REFUSED  {OUT_ACCOUNT}/character-list-order.txt: character list names a character "
        "this install has no folder for x1 (first at byte "
    ) in stdout
    assert "Zedalt" not in stdout and "Faerlina" not in stdout
    assert f"{OUT_ACCOUNT}/character-list-order.txt" not in outputs(out)
    # Named with --extra-name, the alt is mapped and the list is written.
    again = tmp_path / "again"
    assert capture(forever, again, "--extra-name", "Zedalt", "--extra-name", "Faerlina") == 0
    assert f"{OUT_ACCOUNT}/character-list-order.txt" in outputs(again)


def test_constructed_folder_only_second_part_in_an_apostrophe_spelling_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Security S1, end to end: `Kael-QuelThalas` with no twin, `Quel'Thalas` inside a file."""
    root = tmp_path / "World of Warcraft"
    _write(root / ".build.info", BUILD_INFO)
    base = root / FLAVOR
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow_classic_beta\n")
    account = base / "WTF" / "Account" / ACCOUNT
    character = account / "70" / "Kael-QuelThalas"
    _write(character / "layout-local.txt", "Frame: Kael - Quel'Thalas\n")
    _write(character / "chat-cache.txt", "Kael - Quel Thalas\n")
    out = tmp_path / "incoming"
    assert capture(root, out) == 1
    stdout = capsys.readouterr().out
    assert (
        "layout-local.txt: surviving identity string (spaced or apostrophe spelling) x1" in stdout
    )
    written = outputs(out)
    assert written[f"{OUT_ACCOUNT}/1/Labchara-Labrealma/chat-cache.txt"] == (
        b"Labchara - Labrealma\n"
    )
