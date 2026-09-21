"""`scripts/lab_capture.py`: review round 2 (M10-02).

Every input here is constructed: hostile or boundary shapes written for the
test, with invented identity strings, in a synthetic tree under `tmp_path`.
No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from test_lab_capture import ACCOUNT, MAIN, REALM, build_install, capture, lab_capture

Identity = lab_capture.Identity


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    return root


# ─── 1. only vocabulary-shaped spans are left alone ──────────────────────────


def test_constructed_toc_key_is_vocabulary_only_when_the_client_defines_it() -> None:
    identity = Identity(characters=["Thrall", "Mara", "Interface"], realms=[REALM])
    toc = identity.scrub(
        b"## Interface: 120105\n"
        b"## Title-deDE: Thrall UI\n"
        b"## Thrall-Notes: x\n"
        b"## Notes-Mara: x\n"
        b"## Made by Thrall of Area 52 see http://x/\n"
        b"## X-Thrall: x\n",
        toc=True,
    )
    assert toc.data == (
        b"## Interface: 120105\n"
        b"## Title-deDE: Labcharc UI\n"
        b"## Labcharc-Notes: x\n"
        b"## Notes-Labcharb: x\n"
        b"## Made by Labcharc of Labrealma Partb see http://x/\n"
        b"## X-Labcharc: x\n"
    )
    assert not toc.problems


@pytest.mark.parametrize(
    "line",
    [b'SET Thrallmar_pos "1"\n', b'SET thrallmarArea52Setting "1"\n', b'SET Bo_pos "1"\n'],
)
def test_constructed_identity_inside_a_cvar_name_refuses(line: bytes) -> None:
    identity = Identity(characters=[MAIN, "Bo"], realms=[REALM])
    result = identity.scrub(line, blank_cvars=True)
    assert result.data == line, "a CVar name is never edited"
    assert result.problems


def test_constructed_directives_recalled_by_the_domain_reviewer_are_vocabulary() -> None:
    """A character named like the start of a directive must not rewrite the directive."""
    toc = (
        b"## LoadSavedVariablesFirst: 1\n"
        b"## LoadFirst: 1\n"
        b"## UseSecureEnvironment: 1\n"
        b"## AllowAddOnTableAccess: 1\n"
        b"## OptionalDep: Ace3\n"
        b"## RequiredDep: Ace3\n"
        b"## Interface-BCC: 20504\n"
    )
    identity = Identity(characters=["Load", "Use", "Allow", "Optional", "Required", "Interface"])
    result = identity.scrub(toc, toc=True)
    assert result.data == toc and not result.problems
    # Still scrubbed on the value side, and an unlisted suffix is ordinary text.
    other = identity.scrub(b"## LoadFirst: Load\n## Interface-Load: 1\n", toc=True)
    assert other.data == b"## LoadFirst: Labcharc\n## Labcharb-Labcharc: 1\n"


def test_constructed_cvar_name_with_stray_punctuation_is_ordinary_text() -> None:
    result = Identity(characters=[MAIN]).scrub(b'SET Thrallmar\'s-pos "1"\n', blank_cvars=True)
    assert result.data == b'SET Labchara\'s-pos "1"\n' and not result.problems


def test_constructed_game_word_cvar_names_still_pass() -> None:
    result = Identity(characters=["Al"]).scrub(
        b'SET AlwaysCompareItems "1"\nSET scale "Al"\n', blank_cvars=True
    )
    assert result.data == b'SET AlwaysCompareItems "1"\nSET scale "Labchara"\n'
    assert not result.problems
    # ...but the owner is told the short name sits inside longer words.
    assert [
        n for n in result.notes if n.startswith("short identity string inside a longer word x2")
    ]


# ─── 3. short names, and names split by a shorter replacement ────────────────


@pytest.mark.parametrize(
    "text, refused",
    [
        (b'"MARAAREA52"', True),  # glued to a replaced realm
        (b'"maraarea52"', True),
        (b'"AREA52mara"', True),
        (b'"maraMARA"', True),  # glued to another identity hit
        (b'"mArA"', True),  # a whole word in another casing
        (b'"xmarax" "marathon" "Samara"', False),  # counted, not refused
    ],
)
def test_constructed_short_name_glued_to_identity_refuses(text: bytes, refused: bool) -> None:
    result = Identity(characters=["Mara"], realms=[REALM]).scrub(text)
    assert bool(result.problems) is refused, result.problems
    if not refused:
        assert [
            n for n in result.notes if n.startswith("short identity string inside a longer word x3")
        ]
        assert b"mara" in result.data  # untouched: it is not the name


def test_constructed_long_name_split_by_a_short_exact_name_refuses() -> None:
    """The long forms are hunted in the ORIGINAL bytes too, not only in the scrubbed ones."""
    result = Identity(characters=["Al", "Thrall"]).scrub(b'"xtHrAlLx"')
    assert result.data == b'"xtHrLabcharaLx"'
    assert any("not replaced whole" in p for p in result.problems)
    # A long name that was replaced whole is not reported by that scan.
    assert not Identity(characters=["Al", "Thrall"]).scrub(b'"Thrall" "thrall" "Al"').problems


# ─── 4. who stands next to an own realm ──────────────────────────────────────


@pytest.mark.parametrize(
    "text, refused",
    [
        ('"Jaina - Horde - Area 52"', True),
        ("[[Jaina-Horde-Area52]]", True),
        ('"Area 52 - Horde - Jaina"', True),
        ('"Default.Area 52.Jaina"', True),  # DataStore/Altoholic keys
        ('"Jaina.Area52" "Jaina|Area52" "Jaina:Area52" "Jaina/Area52" "Jaina_Area52"', True),
        ('"Area 52 - Jaina"', True),
        ('"Jaina (Area 52)" "Jaina(Area52)"', True),
        ('"JAINA-AREA52"', True),
        (f'"Default.Area 52.{MAIN}" "{MAIN} (Area 52)" "Area 52 - {MAIN}"', False),
        (f'"{MAIN} - Horde - Area 52" "Horde - Area 52" "Area52-US" "EU-Area52"', False),
        (f'"{MAIN.upper()} - AREA 52" "{MAIN.lower()}.area52"', False),
    ],
)
def test_constructed_partner_of_an_own_realm(text: str, refused: bool) -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(text.encode())
    assert any("someone else's name" in p for p in result.problems) is refused, result.problems
    if not refused:
        assert not result.problems


def test_constructed_tolerated_partners_are_counted_and_faction_pairs_are_not_noted() -> None:
    result = Identity(characters=[MAIN], realms=[REALM]).scrub(
        b'["Horde - Area 52"] = 1,\n["Area52-US"] = 2,\n["Default.Area 52.Thrallmar"] = 3,\n'
    )
    assert not result.problems
    assert [
        n
        for n in result.notes
        if n.startswith("own realm next to a faction, region") and " x3 " in n
    ]
    assert not [n for n in result.notes if n.startswith("Name-Realm-shaped")]


def test_constructed_upper_cased_non_ascii_realm_still_catches_a_foreign_name() -> None:
    result = Identity(realms=["Échó"]).scrub('"JAINA-ÉCHÓ" "jaina-échó"'.encode())
    assert "ÉCHÓ".encode() not in result.data and "échó".encode() not in result.data
    assert any("someone else's name" in p and " x2 " in p for p in result.problems)


# ─── 5. an identity map that would be short is not used ──────────────────────


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions")
@pytest.mark.parametrize("level", ["account", "realm", "config"])
def test_constructed_unlistable_identity_folder_stops_the_run(
    level: str, install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    account = install / "_classic_beta_" / "WTF" / "Account" / ACCOUNT
    locked = {"account": account, "realm": account / REALM, "config": account / "config-cache.wtf"}[
        level
    ]
    locked.chmod(0)
    try:
        code = capture(install, tmp_path / "incoming", "--flavor", "_retail_")
    finally:
        locked.chmod(0o755)
    text = capsys.readouterr()
    assert code == 2 and not (tmp_path / "incoming").exists()
    assert "PermissionError" in text.err and "nothing is captured" in text.err
    for real in (ACCOUNT, REALM, MAIN, "Traceback", str(install)):
        assert real not in text.out + text.err


def test_constructed_identity_map_is_broken_down_for_a_sanity_check(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert capture(install, tmp_path / "incoming", "--dry-run", "--extra-name", "Some Guild") == 0
    assert (
        "identity map: 5 names (1 accounts, 1 realms, 2 characters, 1 extra), "
        "1 own GUIDs, 8 CVars blanked"
    ) in capsys.readouterr().out


# ─── 7. long unbroken runs ───────────────────────────────────────────────────


@pytest.mark.parametrize("run", [b"a", b"ab.", "é".encode(), b"Horde-"])
def test_constructed_one_megabyte_run_scrubs_in_bounded_time(run: bytes) -> None:
    """Every detector walks a long run once. Measured: well under a second each."""
    identity = Identity(accounts=[ACCOUNT], characters=[MAIN, "Mara"], realms=[REALM])
    data = b'Blob = "' + run * (1_000_000 // len(run)) + b'"\n'
    started = time.perf_counter()
    result = identity.scrub(data)
    elapsed = time.perf_counter() - started
    assert result.data == data
    assert elapsed < 20, f"{elapsed:.1f} s for a 1 MB run of {run!r}"


# ─── 8. one more GUID family ─────────────────────────────────────────────────


def test_constructed_club_finder_guid_refuses() -> None:
    result = Identity().scrub(b'"ClubFinder-1-23456-789-0000ABCD"')
    assert any("community GUID" in p for p in result.problems)
