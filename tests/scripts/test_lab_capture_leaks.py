"""`scripts/lab_capture.py`: the ways identity could slip through, one test each (M10-02).

Every input here is constructed: hostile or boundary shapes written for the
test, with invented identity strings, in a synthetic tree under `tmp_path`.
Test names say so. No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

import os
import sys
import unicodedata
from pathlib import Path

import pytest
from test_lab_capture import (
    ACCOUNT,
    ALT,
    EMAIL,
    MAIN,
    OWN_GUID,
    PSEUDO_REALM,
    REALM,
    _write,
    build_install,
    capture,
    lab_capture,
    outputs,
)

Identity = lab_capture.Identity
RETAIL_ACCOUNT = ("_retail_", "WTF", "Account", ACCOUNT)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    return root


def saved_variables(root: Path) -> Path:
    return root.joinpath(*RETAIL_ACCOUNT, "SavedVariables")


# ─── 1. Unicode normal forms ─────────────────────────────────────────────────


@pytest.mark.parametrize("folder_form, content_form", [("NFD", "NFC"), ("NFC", "NFD")])
def test_constructed_name_is_found_in_either_normal_form(
    folder_form: str, content_form: str
) -> None:
    folder = unicodedata.normalize(folder_form, "Thrâllmar")
    written = unicodedata.normalize(content_form, "Thrâllmar")
    result = Identity(characters=[folder]).scrub(
        f'["{written} - x"] = 1, -- {written.upper()}\n'.encode()
    )
    assert not result.problems
    assert b"Thr" not in result.data and b"THR" not in result.data
    # The pseudonym is ASCII whatever the name was (byte-level detectors fold ASCII only).
    assert result.data == b'["Labchara - x"] = 1, -- LABCHARA\n'


# ─── 2. a folder that is no longer an installed flavor ───────────────────────


def test_constructed_names_under_a_flavorless_wtf_folder_are_still_identity(
    install: Path, tmp_path: Path
) -> None:
    orphan = install / "_gone_" / "WTF" / "Account" / "987654321#2" / "Old Realm" / "Forgottenalt"
    _write(orphan / "AddOns.txt", "Solo: enabled\n")
    _write(
        saved_variables(install) / "AltTracker.lua",
        '\nAlts = {\n\t["Forgottenalt - Old Realm"] = 1,\n}\n',
    )
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_", "--sv", "AltTracker.lua") == 0
    written = outputs(out)
    tracker = next(data for dest, data in written.items() if dest.endswith("AltTracker.lua"))
    assert b"Forgottenalt" not in tracker and b"Old Realm" not in tracker
    assert not [dest for dest in written if "_gone_" in dest], "capture stays gated on .flavor.info"


# ─── 3 and 4. identity CVars ─────────────────────────────────────────────────


def test_constructed_identity_cvars_blank_wherever_the_line_starts() -> None:
    identity = Identity(accounts=[ACCOUNT])
    config = (
        b'\xef\xbb\xbfSET accountName "LEGACY"\r'
        b'set ACCOUNTLIST "!LEGACY|"\r'
        b'SET Sound_OutputDriverName "Someone Real\'s AirPods"\r'
        b'SET Sound_VoiceChatInputDriverName "Someone Real\'s Mic"\r'
        b'SET Sound_VoiceChatOutputDriverName "Someone Real\'s AirPods"\r'
        b'SET lastSelectedClubId "123456"\r'
        b'SET realmName "Gone Realm"\r'
        b'SET portal "US"\r'
    )
    result = identity.scrub(config, blank_cvars=True)
    assert not result.problems and result.count("cvar") == 7
    assert b"LEGACY" not in result.data and b"Someone" not in result.data
    assert result.data.endswith(b'SET realmName ""\rSET portal "US"\r')
    assert result.data.startswith(b'\xef\xbb\xbfSET accountName ""\r')


@pytest.mark.parametrize(
    "line",
    [b"SET accountName LEGACY\n", b'x SET accountName "LEGACY"\n', b'SET accountName "A" "B"\n'],
)
def test_constructed_identity_cvar_the_blanker_cannot_handle_is_refused(line: bytes) -> None:
    result = Identity().scrub(line, blank_cvars=True)
    assert any("identity CVar" in problem for problem in result.problems)


def test_constructed_realm_known_only_from_realmname_is_replaced_everywhere(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = install / "_retail_" / "WTF" / "Config.wtf"
    config.write_bytes(
        config.read_bytes().replace(b'"Area 52"', b'"Gone Realm"')
        + b'SET lastCharacterIndex "2"\r\nSET guildRosterView "x"\r\n'
    )
    _write(saved_variables(install) / "Seen.lua", f'\nSeen = "{MAIN}-GoneRealm"\n')
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_", "--sv", "Seen.lua") == 0
    seen = next(data for dest, data in outputs(out).items() if dest.endswith("Seen.lua"))
    assert b"Gone" not in seen and b'"Labchara-Labrealm' in seen
    # The by-eye list names CVars and never shows a value.
    stdout = capsys.readouterr().out
    assert "CVars to review by eye: lastCharacterIndex, guildRosterView" in stdout
    assert "accountName" not in stdout.split("CVars to review by eye:")[1].splitlines()[0]


# ─── 5. GUIDs ────────────────────────────────────────────────────────────────


def test_constructed_guid_case_and_other_guid_kinds() -> None:
    identity = Identity(guids=[OWN_GUID.encode()])
    own = identity.scrub(f'"{OWN_GUID.lower()}" "{OWN_GUID}"'.encode())
    assert own.data == b'"Player-9999-00000001" "Player-9999-00000001"' and not own.problems
    for foreign in (b"player-77-0badbeef", b"PLAYER-77-0BADBEEF"):
        assert any("unmapped player GUID" in p for p in identity.scrub(foreign).problems)
    assert any(
        "account GUID" in p for p in identity.scrub(b'"BNetAccount-0-00000ABC1234"').problems
    )
    assert any("guild GUID" in p for p in identity.scrub(b'"Guild-1234-00000ABCDEF0"').problems)


@pytest.mark.parametrize("unit", ["Thrallmar-Illidan-US", "Thrallmar-Area 5 2-US", "Thrallmar"])
def test_constructed_combat_log_guid_needs_name_and_realm_to_be_adopted(
    unit: str, install: Path, tmp_path: Path
) -> None:
    """Someone else's character with the owner's character name is not the owner."""
    log = install / "_retail_" / "Logs" / "WoWCombatLog-092026_211403.txt"
    log.write_bytes(
        log.read_bytes()
        + f'9/20/2026 21:15:00.000-4  SPELL_HEAL,Player-77-0BADBEEF,"{unit}",0x5\n'.encode()
    )
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_") == 1
    assert not [dest for dest in outputs(out) if "WoWCombatLog" in dest]


# ─── 6. other people on the owner's realm ────────────────────────────────────


@pytest.mark.parametrize(
    "text, refused",
    [
        (f'"Otherguy-{REALM}"', True),
        ('"Otherguy - Area52"', True),
        ('"otherguy-area-52"', True),
        (f'"{MAIN}-{REALM}" "{ALT} - Area52"', False),
        (f'"Horde - {REALM}" "Alliance - {REALM} - US"', False),  # AceDB factionrealm keys
    ],
)
def test_constructed_someone_else_on_an_own_realm_is_refused(text: str, refused: bool) -> None:
    identity = Identity(realms=[REALM], characters=[MAIN, ALT])
    result = identity.scrub(text.encode())
    assert any("someone else's name" in p for p in result.problems) is refused
    if not refused:
        assert not result.problems


def test_constructed_review_notes_count_without_quoting() -> None:
    identity = Identity(realms=[REALM], characters=[MAIN])
    macros = (
        b'VER 3 0000000000000001 "Hi" "INV_MISC_QUESTIONMARK"\n'
        b"/w Privatefriend hello\n/cast Smite\n/invite Privatefriend\n/target Privatefriend\n"
        b"  /tell Privatefriend x\n\t/t Privatefriend x\n/friend Privatefriend\n/ignore Privatefriend\n"
        b"/focus Privatefriend\n/assist Privatefriend\n/follow Privatefriend\n/ginvite Privatefriend\n"
        b"/cast [@Privatefriend] Heal\n/cast [target=Privatefriend,help] Heal\n"
        # None of these is a person: unit tokens, and commands that merely start alike.
        b"/cast [@mouseover,help][@player][@party1][@raid25target][target=focus][@arena2pet] Heal\n"
        b"/targetenemy\n/tarnish\nEND\n"
    )
    result = identity.scrub(macros)
    assert not result.problems
    label = "whisper/invite/target macro line or @Name x13"
    assert [n for n in result.notes if n.startswith(label)], result.notes
    assert "Privatefriend" not in " ".join(result.notes)
    assert not identity.scrub(b'"thrallmar-area52" "Thrallmar - Area 52"').notes
    shaped = identity.scrub(b'["Privatefriend-Illidan"] = 1,\n["Left-Click"] = 2,\n')
    assert [n for n in shaped.notes if n.startswith("Name-Realm-shaped string") and " x2 " in n]


# ─── 7. embedded and variant forms ───────────────────────────────────────────


def test_constructed_embedded_and_variant_forms_never_pass_silently() -> None:
    identity = Identity(realms=[REALM, "Mal'Ganis", "Azjol-Nerub"], characters=[MAIN])
    replaced = identity.scrub(
        b"xthrallmarx thrallmararea52 onarea52x area-52 Area_52 Mal\\'Ganis malganis azjol-nerub AZJOLNERUB"
    )
    assert not replaced.problems
    for real in (b"thrall", b"area", b"52", b"mal", b"ganis", b"azjol", b"nerub"):
        assert real not in replaced.data.lower()
    assert b"labrealma-partb Labrealma_Partb" in replaced.data  # slug and underscore shapes
    assert b"Labrealmc\\'Partb labrealmcpartb" in replaced.data  # Lua-escaped apostrophe, slug
    assert replaced.embedded >= 3

    for mixed in (b"xThRaLlMaRx", b"thrallMar", b"AREA52x and aReA 52"):
        assert any("other casing or embedded" in p for p in identity.scrub(mixed).problems), mixed


# ─── 8. a stale staging folder ───────────────────────────────────────────────


def test_constructed_nonempty_staging_folder_stops_the_run_or_is_listed(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_") == 0
    before = outputs(out)
    assert capture(install, out, "--flavor", "_retail_") == 2
    assert capture(install, out, "--flavor", "_retail_", "--dry-run") == 2
    assert outputs(out) == before

    # With the explicit flag, a full re-run rewrites everything: no leftovers.
    assert capture(install, out, "--flavor", "_retail_", "--reuse-out") == 0
    # A file this run does not write is listed and fails the run. Never deleted.
    stale = (
        out / "macos" / "_retail_" / "WTF" / "Account" / "90000001#1" / "SavedVariables" / "Old.lua"
    )
    _write(stale, "\nOld = 1\n")
    capsys.readouterr()
    assert capture(install, out, "--flavor", "_retail_", "--reuse-out") == 1
    assert "SavedVariables/Old.lua" in capsys.readouterr().err and stale.is_file()


# ─── 9. output never echoes identity ─────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_constructed_unreadable_file_is_refused_without_naming_the_real_path(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    locked = install.joinpath(*RETAIL_ACCOUNT, REALM, MAIN, "chat-cache.txt")
    locked.chmod(0)
    try:
        code = capture(install, tmp_path / "incoming", "--flavor", "_retail_")
    finally:
        locked.chmod(0o644)
    text = capsys.readouterr()
    assert code == 1
    expected = (
        f"REFUSED  macos/_retail_/WTF/Account/90000001#1/{PSEUDO_REALM}/Labchara/chat-cache.txt"
    )
    assert f"{expected}: unreadable (PermissionError)" in text.out
    for real in (ACCOUNT, REALM, MAIN, "Traceback"):
        assert real not in text.out + text.err


def test_constructed_unusable_identity_string_is_not_echoed(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(install.joinpath(*RETAIL_ACCOUNT, REALM, "Nil", "AddOns.txt"), "x\n")
    assert capture(install, tmp_path / "a") == 2
    assert "Nil" not in capsys.readouterr().err
    assert capture(install, tmp_path / "b", "--show-map") == 2
    assert "'Nil'" in capsys.readouterr().err


# ─── 10. client vocabulary is never rewritten ────────────────────────────────


def test_constructed_name_that_is_a_game_word_leaves_cvar_names_and_toc_keys_alone() -> None:
    identity = Identity(characters=["Al", "Interface", "Title"])
    config = identity.scrub(b'SET AlwaysCompareItems "1"\nSET lastPlayed "Al"\n', blank_cvars=True)
    assert config.data == b'SET AlwaysCompareItems "1"\nSET lastPlayed "Labchara"\n'
    assert not config.problems

    toc = identity.scrub(
        b"## Interface: 120105\n## Title: Interface Helper by Al\n## X-Al-Notes: x\nInterface.lua\n",
        toc=True,
    )
    assert toc.data == (
        b"## Interface: 120105\n## Title: Labcharb Helper by Labchara\n## X-Labchara-Notes: x\nLabcharb.lua\n"
    )
    assert not toc.problems

    # Elsewhere it keeps over-replacing, and says how often so the owner looks.
    lua = identity.scrub(b'["AlwaysShow"] = "Al",\n')
    assert lua.data == b'["LabcharawaysShow"] = "Labchara",\n' and lua.embedded == 1


def test_constructed_embedded_count_reaches_the_row_and_the_summary(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        saved_variables(install) / "Words.lua", f'\nWords = {{\n\t["{MAIN}sBank"] = "{MAIN}",\n}}\n'
    )
    assert capture(install, tmp_path / "incoming", "--flavor", "_retail_", "--sv", "Words.lua") == 0
    stdout = capsys.readouterr().out
    row = next(
        line for line in stdout.splitlines() if line.startswith("| `") and "Words.lua" in line
    )
    assert "identity-rewritten: 2; embedded: 1" in row
    assert "edits, 1 embedded)" in stdout
    summary = stdout.split("replacements per pseudonym")[1]
    assert "  Labchara: " in summary and f"  {PSEUDO_REALM}: " in summary


# ─── 11. selection notes describe the scrubbed bytes ─────────────────────────


def test_constructed_selection_note_is_recomputed_on_the_scrubbed_bytes(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The category's statistic must still hold once identity is gone.

    Pseudonyms are ASCII, so a non-ASCII name is the obvious case (covered by
    the test after this one). The other honest case is an identity string that
    carries the feature itself: here a coloured guild-mate name given with
    --extra-name.
    """
    coloured = "|cffff8000Guildmate|r"
    _write(
        saved_variables(install) / "OnlyEsc.lua", f'\nOnlyEsc = {{\n\t["x"] = "{coloured}",\n}}\n'
    )
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_", "--extra-name", coloured) == 0
    stdout = capsys.readouterr().out
    assert "OnlyEsc.lua: no longer 'colour and link escapes' once scrubbed" in stdout
    rows = [line for line in stdout.splitlines() if line.startswith("| `")]
    assert not [row for row in rows if "OnlyEsc.lua" in row and "escapes" in row]
    for row in (row for row in rows if "colour and link escapes" in row):
        assert b"|c" in (out / row.split("`")[1]).read_bytes()


def test_constructed_file_whose_only_non_ascii_bytes_were_a_name_is_not_called_non_ascii(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pseudonyms are ASCII, so a file like this comes out ASCII: it must not carry the note."""
    alt = install.joinpath(*RETAIL_ACCOUNT, REALM, "Zoë")
    _write(alt / "AddOns.txt", "Solo: enabled\n")
    for stale in (alt / "AddOns.txt", alt):
        os.utime(stale, (1_000_000_000, 1_000_000_000))
    _write(saved_variables(install) / "OnlyName.lua", '\nOnlyName = {\n\t["Zoë"] = 1,\n}\n')
    out = tmp_path / "incoming"

    assert capture(install, out, "--flavor", "_retail_", "--sv", "OnlyName.lua") == 0

    stdout = capsys.readouterr().out
    assert "OnlyName.lua: no longer 'non-ASCII strings' once scrubbed" in stdout
    rows = [line for line in stdout.splitlines() if line.startswith("| `")]
    only_name = next(row for row in rows if "OnlyName.lua" in row)
    assert "non-ASCII" not in only_name
    assert (out / only_name.split("`")[1]).read_bytes().isascii()
    # The note went to a file that still has non-ASCII bytes after the scrub, or to none.
    for row in (row for row in rows if "non-ASCII strings" in row):
        assert not (out / row.split("`")[1]).read_bytes().isascii(), row


# ─── 12 and 13. platform and kind ────────────────────────────────────────────


def test_constructed_platform_must_be_stated_off_macos_and_windows(
    install: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = ["--root", str(install), "--out", str(tmp_path / "incoming"), "--dry-run"]
    monkeypatch.setattr(sys, "platform", "linux")
    assert lab_capture.main(argv) == 2
    assert lab_capture.main([*argv, "--platform", "windows"]) == 0
    assert lab_capture.main([*argv, "--platform", "../up"]) == 2
    monkeypatch.setattr(sys, "platform", "win32")
    assert lab_capture.main(argv) == 0


def test_constructed_kind_colliding_with_another_flavor_folder_is_rejected(
    install: Path, tmp_path: Path
) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out, "--kind", "_retail_=same", "--kind", "_classic_beta_=same") == 2
    assert capture(install, out, "--kind", "_retail_=_classic_beta_") == 2
    assert not out.exists()


# ─── 14. shape-preserving pseudonyms ─────────────────────────────────────────


def test_constructed_pseudonyms_keep_separators_and_are_always_ascii() -> None:
    identity = Identity(
        accounts=["OLDLOGIN"],
        realms=["Area 52", "Azjol-Nerub", "Mal'Ganis", "Pozzo dell'Eternità", "Hyjal"],
        characters=["Zoë", MAIN],
    )
    assert identity.names == {
        "Area 52": "Labrealma Partb",
        "Azjol-Nerub": "Labrealmb-Partb",
        "Hyjal": "Labrealmc",
        "Mal'Ganis": "Labrealmd'Partb",
        "Pozzo dell'Eternità": "Labrealme Partb'Partc",
        MAIN: "Labchara",
        "Zoë": "Labcharb",
        "OLDLOGIN": "LABACCOUNTA",
    }
    assert all(pseudonym.isascii() for pseudonym in identity.names.values())
    result = identity.scrub("Zoë-PozzodellEternità zoë ZOË pozzo-delleternita".encode())
    assert result.data == b"Labcharb-LabrealmePartbPartc labcharb LABCHARB labrealme-partbpartc"
    assert not result.problems


# ─── 16. detector edges ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, label",
    [
        (b'"Bob#1234567"', "BattleTag"),
        (b'"Bob#1234abc"', "BattleTag"),
        ('"Zoë#12345"'.encode(), "BattleTag"),
        (b'"user@localhost"', "email address"),
        ('"jürgen@bücher.example"'.encode(), "email address"),
        (EMAIL.encode(), "email address"),
    ],
)
def test_constructed_detector_edges_refuse(text: bytes, label: str) -> None:
    assert any(label in problem for problem in Identity().scrub(text).problems)


def test_constructed_macro_conditionals_and_account_pseudonym_are_not_false_alarms() -> None:
    clean = (
        b"/cast [@mouseover,help][mod:alt,@player][target=@focus] Heal\n#showtooltip\n90000001#1\n"
    )
    assert not Identity().scrub(clean).problems


# ─── 17. one trial scrub per candidate ───────────────────────────────────────


def test_constructed_refused_candidate_is_trial_scrubbed_once(
    install: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        f'\nLeak = {{\n\t{{\n\t\t{{\n\t\t\t{{\n\t\t\t\t["a"] = "{EMAIL} |cffff8000x|r é", -- [1]\n'
    )
    _write(
        saved_variables(install) / "Leaky.lua",
        body + "\t\t\t\t-1.25,\n\t\t\t}\n\t\t}\n\t}\n}\n" * 1,
    )
    calls: list[str] = []
    real = lab_capture.process

    def counting(item: object, *args: object, **kwargs: object) -> object:
        calls.append(item.src.name)  # type: ignore[attr-defined]
        return real(item, *args, **kwargs)

    monkeypatch.setattr(lab_capture, "process", counting)
    assert capture(install, tmp_path / "incoming", "--flavor", "_retail_") == 0
    assert calls.count("Leaky.lua") == 1, "ranked in several categories, scrubbed once"
