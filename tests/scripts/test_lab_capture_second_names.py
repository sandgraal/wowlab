"""`scripts/lab_capture.py`: Forever second names, third-party files, NUL-ended caches.

Second follow-up to M10-02. On the Forever beta a character has a
player-chosen first and second name: `<account>/<digits>/<First>-<Second>/`
holds everything, and a retail-style twin `<account>/<Realm>/<First>/` holds
only AddOns.txt; one first name can carry several second names that all share
one twin (owner's directory listing, docs/LAB_FILE_MAP.md on the M10-03
branch).

CONSTRUCTED INPUT. Every tree below is synthetic, built in `tmp_path` in the
shape the owner reported; every name is invented, and the edit-mode and
flagged cache bodies are constructed from the one-line descriptions in
docs/LAB_FILE_MAP.md (space-separated tokens, length-prefixed layout names,
a final NUL; `2` then a NUL). No test needs or touches a real install
(ADR-0012).
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import ACCOUNT, BUILD_INFO, _write, capture, lab_capture, outputs

Identity = lab_capture.Identity

FLAVOR = "_classic_beta_"
OUT_ACCOUNT = f"macos/{FLAVOR}/WTF/Account/90000001#1"
# Realm-category names sort bloodfist, moon, qorv, some realm: a, b, c, d.
QORV_DIR = f"{OUT_ACCOUNT}/1/Labchara-Labrealmc"
MOON_DIR = f"{OUT_ACCOUNT}/1/Labchara-Labrealmb"
TWIN_ADDONS = f"{OUT_ACCOUNT}/Labrealmd Partb/Labchara/AddOns.txt"
REAL = ("Alyra", "alyra", "Bloodfist", "Qorv", "Moon", "Some Realm", "SomeRealm", ACCOUNT)

FLAGGED = b"2\x00"
EDIT_MODE = b"1 2 0 3 def 0 6 Priest 0 1 1 0\x00"
EDIT_MODE_OWNED = b"1 2 0 3 def 0 5 Alyra 0 1 1 0\x00"  # "Alyra" at byte 16
ANCHOR_TOC = "## Interface: 11507\n## Title: Anchor\nAnchorQorvey.lua\n"  # "Qorv" at byte 43
PLAIN_TOC = "## Interface: 11507\n## Title: Plain\nPlain.lua\n"
OLD = 1_000_000_000


def _stamp(when: int, *paths: Path) -> None:
    for path in paths:
        for entry in [*path.rglob("*"), path]:
            os.utime(entry, (when, when))


def build(root: Path) -> Path:
    """Three second names on one first name, one twin. Returns the account folder."""
    _write(root / ".build.info", BUILD_INFO)
    base = root / FLAVOR
    _write(base / ".flavor.info", "Product Flavor!STRING:0\nwow_classic_beta\n")
    _write(base / "WTF" / "Config.wtf", 'SET portal "us"\n')
    account = base / "WTF" / "Account" / ACCOUNT
    _write(account / "config-cache.wtf", 'SET chatBubbles "1"\n')
    (account / "flagged-cache-account.txt").write_bytes(FLAGGED)
    (account / "edit-mode-cache-account.txt").write_bytes(EDIT_MODE)
    group = account / "70"
    for folder in ("Alyra-Bloodfist", "Alyra-Qorv", "Alyra-Moon"):
        character = group / folder
        _write(character / "chat-cache.txt", "SAY 255 255 255\n")
        _write(character / "layout-local.txt", "Version: 1\n")
        (character / "flagged-cache-character.txt").write_bytes(FLAGGED)
        (character / "edit-mode-cache-character.txt").write_bytes(EDIT_MODE)
    _write(account / "Some Realm" / "Alyra" / "AddOns.txt", "Plain: enabled\n")
    addons = base / "Interface" / "AddOns"
    _write(addons / "Anchor" / "Anchor.toc", ANCHOR_TOC)
    _write(addons / "Plain" / "Plain.toc", PLAIN_TOC)
    # Alyra-Qorv was played last; the shared twin is written at every logout,
    # so it is newer still and must not decide which character is "last".
    _stamp(OLD, root)
    _stamp(OLD + 100, group / "Alyra-Qorv")
    _stamp(OLD + 200, account / "Some Realm")
    return account


@pytest.fixture
def forever(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build(root)
    return root


# ─── 1. the twin is paired by first name ─────────────────────────────────────


def test_constructed_twin_is_paired_by_first_name_and_shared(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out) == 0
    stdout = capsys.readouterr().out
    written = outputs(out)

    assert (
        "identity map: 6 names (1 accounts, 4 realms, 1 characters, 0 extra), "
        "0 own GUIDs, 8 CVars blanked"
    ) in stdout
    # The most recently played <First>-<Second> folder, and its twin's AddOns.txt.
    assert f"{QORV_DIR}/chat-cache.txt" in written
    assert written[TWIN_ADDONS] == b"Plain: enabled\n"
    assert [d for d in written if d.endswith("AddOns.txt")] == [TWIN_ADDONS]
    assert not [d for d in written if "Labrealma" in d or "Labrealmb" in d]
    for dest, data in written.items():
        for real in REAL:
            assert real.encode() not in data and real not in dest, (real, dest)
    assert "<path withheld>" not in stdout


@pytest.mark.parametrize("wanted", ["Alyra-Moon", "70/Alyra-Moon"])
def test_constructed_every_second_name_shares_the_one_twin(
    wanted: str, forever: Path, tmp_path: Path
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out, "--character", wanted) == 0
    written = set(outputs(out))
    assert f"{MOON_DIR}/chat-cache.txt" in written and TWIN_ADDONS in written
    assert not [d for d in written if d.startswith(QORV_DIR)]


def test_constructed_twin_label_picks_the_most_recent_of_its_characters(
    forever: Path, tmp_path: Path
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out, "--character", "Some Realm/Alyra") == 0
    written = set(outputs(out))
    assert f"{QORV_DIR}/chat-cache.txt" in written and TWIN_ADDONS in written


def test_constructed_twin_is_never_a_character_of_its_own(forever: Path) -> None:
    account = forever / FLAVOR / "WTF" / "Account" / ACCOUNT
    units = lab_capture.character_units(account)
    twin = account / "Some Realm" / "Alyra"
    assert sorted(u[0].name for u in units) == ["Alyra-Bloodfist", "Alyra-Moon", "Alyra-Qorv"]
    assert all(u[1:] == (twin,) for u in units)


def test_constructed_two_groups_each_claim_their_own_realm(tmp_path: Path) -> None:
    account = tmp_path / "account"
    for folder in ("70/Alyra-Qorv", "70/Bren-Moon", "71/Alyra-Glade", "Glade/Loner"):
        (account / folder).mkdir(parents=True)
    for twin in ("Some Realm/Alyra", "Some Realm/Bren", "Other Realm/Alyra"):
        _write(account / twin / "AddOns.txt", "x\n")
    units = {u[0].name: u[1:] for u in lab_capture.character_units(account)}
    assert units == {
        "Alyra-Qorv": (account / "Some Realm" / "Alyra",),
        "Bren-Moon": (account / "Some Realm" / "Bren",),
        "Alyra-Glade": (account / "Other Realm" / "Alyra",),
        "Loner": (),  # a retail-shaped character that is nobody's twin
    }


# ─── 2. an identity match in a third-party addon file refuses it ─────────────

THIRD_PARTY = "identity string inside a third-party addon file"


def test_constructed_automatic_toc_selection_passes_over_an_identity_match(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Constructed case: a four-letter second name inside a longer addon file name."""
    out = tmp_path / "incoming"
    assert capture(forever, out) == 0
    stdout = capsys.readouterr().out
    written = outputs(out)
    assert written[f"macos/{FLAVOR}/Interface/AddOns/Plain/Plain.toc"] == PLAIN_TOC.encode()
    assert not [d for d in written if "Anchor" in d]
    # A count only: an offset into public text would point at the name.
    assert f"  {FLAVOR}/Interface/AddOns/Anchor/Anchor.toc: {THIRD_PARTY} x1" in (
        stdout.splitlines()
    )
    assert "AnchorLabrealm" not in stdout


def test_constructed_explicit_toc_with_an_identity_match_is_refused(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out, "--toc", "Anchor") == 1
    stdout = capsys.readouterr().out
    assert f"REFUSED  macos/{FLAVOR}/Interface/AddOns/Anchor/Anchor.toc: {THIRD_PARTY} x1" in (
        stdout.splitlines()
    )
    assert "first at byte" not in stdout
    assert not [d for d in outputs(out) if "Anchor" in d]


def test_constructed_identity_in_an_addon_path_is_refused_with_the_path_withheld(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    addons = forever / FLAVOR / "Interface" / "AddOns"
    _write(addons / "AlyraBars" / "AlyraBars.toc", PLAIN_TOC)
    out = tmp_path / "incoming"
    assert capture(forever, out, "--toc", "AlyraBars") == 1
    stdout = capsys.readouterr().out
    assert f"REFUSED  macos/{FLAVOR}/<path withheld>: in path: {THIRD_PARTY}" in stdout
    assert f"  {FLAVOR}/<path withheld>: in path: {THIRD_PARTY}" in stdout.splitlines()
    assert "LabcharaBars" not in stdout and "Alyra" not in stdout
    assert not [d for d in outputs(out) if "Bars" in d]


def test_constructed_any_file_under_interface_addons_is_third_party(tmp_path: Path) -> None:
    identity = Identity(characters=["Alyra"], realms=["Qorv"])
    source = tmp_path / "Core.lua"

    def run(text: bytes, rel: str) -> list[str]:
        source.write_bytes(text)
        item = lab_capture.Item(source, PurePosixPath(rel), FLAVOR, "1", "savedvariables")
        return list(lab_capture.process(item, identity).problems)

    assert run(b"local Qorv = 1\n", "_f_/interface/ADDONS/Anchor/Core.lua") == [f"{THIRD_PARTY} x1"]
    assert run(b"local x = 1\n", "_f_/Interface/AddOns/Anchor/Core.lua") == []
    # The same text anywhere else is scrubbed as before.
    assert run(b"local Qorv = 1\n", "_f_/WTF/Account/A/SavedVariables/Core.lua") == []


def test_constructed_unreadable_addon_file_never_prints_its_rewritten_folder(
    forever: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security S3: the label of an unreadable file withholds a rewritten addon path."""
    addons = forever / FLAVOR / "Interface" / "AddOns"
    _write(addons / "AlyraBars" / "AlyraBars.toc", PLAIN_TOC)
    real_read = lab_capture.read_bytes

    def read(path: Path, max_lines: int | None = None) -> bytes:
        if path.name == "AlyraBars.toc":
            raise PermissionError("constructed")
        return real_read(path, max_lines)

    monkeypatch.setattr(lab_capture, "read_bytes", read)
    out = tmp_path / "incoming"
    assert capture(forever, out, "--toc", "AlyraBars") == 1
    stdout = capsys.readouterr().out
    assert f"REFUSED  macos/{FLAVOR}/<path withheld>: unreadable (PermissionError)" in stdout
    assert f"  {FLAVOR}/<path withheld>: unreadable (PermissionError)" in stdout.splitlines()
    assert "LabcharaBars" not in stdout and "Alyra" not in stdout


# ─── security S1: addon names in AddOns.txt and SavedVariables ───────────────

ADDON_LIST = "identity string inside an addon name"
EMBEDDED = "identity string inside a longer word"
ADDON_FILE_NAME = "identity string inside an addon's file name"


def test_constructed_addons_txt_naming_an_addon_after_a_name_is_refused(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    twin = forever / FLAVOR / "WTF" / "Account" / ACCOUNT / "Some Realm" / "Alyra" / "AddOns.txt"
    twin.write_bytes(b"AnchorQorvey: enabled\nPlain: enabled\n")
    os.utime(twin, (OLD + 200, OLD + 200))
    out = tmp_path / "incoming"
    assert capture(forever, out) == 1
    stdout = capsys.readouterr().out
    assert f"REFUSED  {TWIN_ADDONS}: {ADDON_LIST} x1" in stdout.splitlines()
    assert TWIN_ADDONS not in outputs(out) and "AnchorLabrealm" not in stdout


def test_constructed_savedvariables_with_a_glued_name_or_named_after_one_is_refused(
    tmp_path: Path,
) -> None:
    identity = Identity(characters=["Alyra"], realms=["Some Realm", "Qorv"], loose=["Qorv"])
    source = tmp_path / "x.lua"

    def run(text: bytes, rel: str) -> list[str]:
        source.write_bytes(text)
        item = lab_capture.Item(source, PurePosixPath(rel), FLAVOR, "1", "savedvariables")
        return list(lab_capture.process(item, identity).problems)

    saved = "_f_/WTF/Account/A/SavedVariables"
    glued = b'\nAnchorQorveyDB = {\n\t["Alyra"] = 1,\n}\n'
    assert run(glued, f"{saved}/Anchor.lua") == [f"{EMBEDDED} x1"]
    assert run(b"\nAnchorDB = 1\n", f"{saved}/AnchorQorvey.lua") == [f"in path: {ADDON_FILE_NAME}"]
    assert run(b"\nAnchorDB = 1\n", f"{saved}/AnchorQorvey.lua.bak") == [
        f"in path: {ADDON_FILE_NAME}"
    ]
    # A whole-word name is scrubbed as before.
    assert run(b'\nAnchorDB = {\n\t["Alyra - Qorv"] = 1,\n}\n', f"{saved}/Anchor.lua") == []


def test_constructed_savedvariables_file_named_after_a_name_withholds_its_path(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = forever / FLAVOR / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    _write(saved / "AnchorQorvey.lua", "\nAnchorDB = 1\n")
    out = tmp_path / "incoming"
    assert capture(forever, out, "--sv", "AnchorQorvey.lua") == 1
    stdout = capsys.readouterr().out
    assert f"REFUSED  macos/{FLAVOR}/<path withheld>: in path: {ADDON_FILE_NAME}" in stdout
    assert "AnchorLabrealm" not in stdout and not [d for d in outputs(out) if "Anchor" in d]


# ─── 3. an identity match in an edit-mode cache refuses it ───────────────────

LAYOUT_NAME = "identity string inside a length-prefixed layout name"


@pytest.mark.parametrize(
    "where",
    ["edit-mode-cache-account.txt", "70/Alyra-Qorv/edit-mode-cache-character.txt"],
)
def test_constructed_owner_named_layout_refuses_the_edit_mode_cache(
    where: str, forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    account = forever / FLAVOR / "WTF" / "Account" / ACCOUNT
    target = account.joinpath(*where.split("/"))
    target.write_bytes(EDIT_MODE_OWNED)
    os.utime(target, (OLD + 100, OLD + 100))
    out = tmp_path / "incoming"
    assert capture(forever, out) == 1
    stdout = capsys.readouterr().out
    refused = [line for line in stdout.splitlines() if line.startswith("REFUSED")]
    assert len(refused) == 1, refused
    assert refused[0].endswith(f"/{target.name}: {LAYOUT_NAME} x1 (first at byte 16, line 1)")
    assert "Alyra" not in stdout
    assert not [d for d in outputs(out) if d.endswith(target.name)]


# ─── 4. a final NUL byte survives ────────────────────────────────────────────


def test_constructed_nul_ended_caches_round_trip_byte_for_byte(
    forever: Path, tmp_path: Path
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out) == 0
    written = outputs(out)
    for dest in (
        f"{OUT_ACCOUNT}/flagged-cache-account.txt",
        f"{QORV_DIR}/flagged-cache-character.txt",
    ):
        assert written[dest] == FLAGGED, dest
    for dest in (
        f"{OUT_ACCOUNT}/edit-mode-cache-account.txt",
        f"{QORV_DIR}/edit-mode-cache-character.txt",
    ):
        assert written[dest] == EDIT_MODE, dest
    result = Identity(characters=["Alyra"], realms=["Qorv"]).scrub(EDIT_MODE + FLAGGED)
    assert result.data == EDIT_MODE + FLAGGED and not result.edits and not result.problems


# ─── code review C2: an ambiguous twin is never a character of its own ───────


def test_constructed_ambiguous_twin_leaves_the_character_captured_with_a_note(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    account = forever / FLAVOR / "WTF" / "Account" / ACCOUNT
    _write(account / "Other Realm" / "Alyra" / "AddOns.txt", "Plain: enabled\n")
    _stamp(OLD + 300, account / "Other Realm")  # newest of all, and still not a character
    out = tmp_path / "incoming"
    assert capture(forever, out) == 0
    stdout = capsys.readouterr().out
    written = outputs(out)
    assert any(d.endswith("-Labrealmd/chat-cache.txt") for d in written), sorted(written)
    assert not [d for d in written if d.endswith("AddOns.txt")]
    assert (
        f"note     {FLAVOR}: the chosen character's retail-style twin could not be paired "
        "(2 candidate folders); no AddOns.txt is captured for it"
    ) in stdout.splitlines()
    units = lab_capture.character_units(account)
    assert sorted(u[0].name for u in units) == ["Alyra-Bloodfist", "Alyra-Moon", "Alyra-Qorv"]
    assert all(len(u) == 1 for u in units)


# ─── round 2, security S1: a name glued to a word refuses every file kind ────


@pytest.mark.parametrize(
    ("name", "glued", "whole"),
    [
        (
            "bindings-cache.wtf",
            b"bind CTRL-2 CLICK AnchorQorveyButton:LeftButton\n",
            b"bind CTRL-3 MACRO Qorv\n",
        ),
        (
            "macros-cache.txt",
            b'MACRO 1 "Go" 134400\n/click AnchorQorveyButton\nEND\n',
            b'MACRO 1 "Qorv" 134400\n/say hi\nEND\n',
        ),
        ("layout-local.txt", b"AnchorQorveyFrame: 10 20\n", b"Qorv 10 20\n"),
        ("click-bindings-cache.txt", b"AnchorQorveyButton 1\nEND\n", b"Qorv 1\nEND\n"),
        (
            "WoWCombatLog-010126_000000.txt",
            b'1/1/2026 00:00:00.000-4  SPELL_DAMAGE,Creature-0-1-2-3-4-0,"Qorvath the Ancient",'
            b'0xa48,0x0,Creature-0-1-2-3-5-0,"Anchor Qorvey Strike",0x10a48\n',
            b'1/1/2026 00:00:00.000-4  SAY,"Qorv"\n',
        ),
        ("chat-cache.txt", b"CHANNEL AnchorQorvey\n", b"CHANNEL Qorv\n"),
        ("config-cache.wtf", b'SET lastAddon "AnchorQorvey"\n', b'SET lastAddon "Qorv"\n'),
    ],
)
def test_constructed_glued_identity_refuses_every_file_kind(
    name: str, glued: bytes, whole: bytes, tmp_path: Path
) -> None:
    identity = Identity(characters=["Alyra"], realms=["Some Realm", "Qorv"], loose=["Qorv"])
    source = tmp_path / name
    rel = f"{FLAVOR}/WTF/Account/A/Some Realm/Alyra/{name}"

    def run(text: bytes) -> tuple[list[str], bytes]:
        source.write_bytes(text)
        item = lab_capture.Item(source, PurePosixPath(rel), FLAVOR, "1", "x")
        outcome = lab_capture.process(item, identity)
        return list(outcome.problems), outcome.result.data

    count = glued.count(b"Qorv")
    assert run(glued)[0] == [f"{EMBEDDED} x{count}"]  # a count only, no offset
    problems, data = run(whole)
    assert problems == [] and b"Labrealma" in data and b"Qorv" not in data


# ─── round 2, code review C1: a candidate holds every first name ─────────────


@pytest.mark.parametrize(
    ("second", "expected"),
    [("Glade", None), ("OtherRealm", "Other Realm/Alyra")],
)
def test_constructed_two_realm_folders_holding_the_only_first_name_tie(
    second: str, expected: str | None, tmp_path: Path
) -> None:
    """`Some Realm/{Alyra, Dax}` and `Other Realm/{Alyra}` both hold the group's one
    first name: unpaired, unless the second name spells exactly one of them."""
    account = tmp_path / "account"
    (account / "70" / f"Alyra-{second}").mkdir(parents=True)
    for twin in ("Some Realm/Alyra", "Some Realm/Dax", "Other Realm/Alyra"):
        _write(account / twin / "AddOns.txt", "x\n")
    units = [u for u in lab_capture.character_units(account) if u[0].parent.name == "70"]
    assert [u[1].relative_to(account).as_posix() if len(u) > 1 else None for u in units] == [
        expected
    ]


# ─── round 2, code review C2: --character that matches nothing stops ─────────


def test_constructed_character_matching_nothing_stops_the_run(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(forever, out, "--character", "Alyra-Nowhere") == 2
    err = capsys.readouterr().err
    assert "lab_capture: --character: no such character in this flavor" in err
    assert "Nowhere" not in err and not out.exists()


def test_constructed_character_naming_an_unpaired_twin_says_so_without_real_names(
    forever: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    account = forever / FLAVOR / "WTF" / "Account" / ACCOUNT
    _write(account / "Other Realm" / "Alyra" / "AddOns.txt", "Plain: enabled\n")
    out = tmp_path / "incoming"
    assert capture(forever, out, "--character", "Other Realm/Alyra") == 2
    err = capsys.readouterr().err
    assert (
        "lab_capture: --character: the retail-style twin Labrealmc Partb/Labchara could not be "
        "paired with a character; choose its <First>-<Second> folder instead"
    ) in err
    assert "Other" not in err and "Alyra" not in err and not out.exists()


# ─── round 3, item 1: --character across flavors ─────────────────────────────


def _two_flavors(root: Path) -> Path:
    """The Forever tree above plus a retail flavor with one retail-shaped character."""
    build(root)
    _write(root / "_retail_" / ".flavor.info", "Product Flavor!STRING:0\nwow\n")
    _write(root / "_retail_" / "WTF" / "Config.wtf", 'SET portal "us"\n')
    retail = root / "_retail_" / "WTF" / "Account" / ACCOUNT
    _write(retail / "Area 52" / "Zugzug" / "chat-cache.txt", "SAY 255 255 255\n")
    return root / FLAVOR / "WTF" / "Account" / ACCOUNT


def test_constructed_character_in_one_flavor_is_captured_and_the_other_noted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    _two_flavors(root)
    out = tmp_path / "incoming"
    assert capture(root, out, "--character", "Alyra-Moon") == 0
    stdout = capsys.readouterr().out
    written = set(outputs(out))
    # The retail realm joins the map: area 52, bloodfist, moon, qorv, some realm.
    account = f"macos/{FLAVOR}/WTF/Account/90000001#1"
    assert f"{account}/1/Labchara-Labrealmc/chat-cache.txt" in written
    assert f"{account}/Labrealme Partb/Labchara/AddOns.txt" in written
    assert not [d for d in written if d.endswith("/chat-cache.txt") and "_retail_" in d]
    assert "note     _retail_: --character matched no character here; skipped" in (
        stdout.splitlines()
    )


def test_constructed_character_matching_no_flavor_still_stops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    account = _two_flavors(root)
    assert capture(root, tmp_path / "a", "--character", "Alyra-Nowhere") == 2
    assert "lab_capture: --character: no such character in this flavor" in capsys.readouterr().err
    # An unpaired twin's label keeps its own message, whichever flavor misses first.
    _write(account / "Other Realm" / "Alyra" / "AddOns.txt", "Plain: enabled\n")
    assert capture(root, tmp_path / "b", "--character", "Other Realm/Alyra") == 2
    assert "--character: the retail-style twin " in capsys.readouterr().err
    # --flavor keeps its behaviour: the other flavor's character is not there.
    assert capture(root, tmp_path / "c", "--character", "Alyra-Moon", "--flavor", "_retail_") == 2
    assert "lab_capture: --character: no such character in this flavor" in capsys.readouterr().err
    for folder in ("a", "b", "c"):
        assert not (tmp_path / folder).exists()


# ─── round 3, item 2: a candidate left only by a contested drop ──────────────


@pytest.mark.parametrize("groups", [("70", "71"), ("71", "70")])
def test_constructed_contested_drop_leaves_the_groups_unpaired_in_either_order(
    groups: tuple[str, str], tmp_path: Path
) -> None:
    account = tmp_path / "account"
    (account / groups[0] / "Eve-Qorv").mkdir(parents=True)
    (account / groups[1] / "Cara-Moon").mkdir(parents=True)
    for twin in ("Some Realm/Eve", "Some Realm/Cara", "Other Realm/Cara", "Old Realm/Eve"):
        _write(account / twin / "AddOns.txt", "x\n")
    units = {u[0].name: u[1:] for u in lab_capture.character_units(account)}
    assert units == {"Eve-Qorv": (), "Cara-Moon": ()}
    # Each is then captured with the existing "could not be paired" note.
    assert lab_capture.unpaired_twins((account / groups[0] / "Eve-Qorv",)) == 2
    assert lab_capture.unpaired_twins((account / groups[1] / "Cara-Moon",)) == 2


# ─── round 3, item 3: `_` joins a name to a longer word ──────────────────────


# A character name, so the own-realm joint check (`_` is a joint) stays out of it.
@pytest.mark.parametrize(
    ("name", "glued", "whole"),
    [
        (
            "bindings-cache.wtf",
            b"bind CTRL-4 CLICK Bar_Alyra_Frame:LeftButton\nbind CTRL-5 CLICK BAR_ALYRA_FRAME\n",
            b"bind CTRL-3 MACRO Alyra\n",
        ),
        ("layout-local.txt", b"Bar_Alyra_Frame: 10 20\n", b"Alyra 10 20\n"),
        ("layout-local.txt", b"BAR_ALYRA 10 20\n", b"ALYRA 10 20\n"),
    ],
)
def test_constructed_underscore_joined_identity_refuses_the_file(
    name: str, glued: bytes, whole: bytes, tmp_path: Path
) -> None:
    identity = Identity(characters=["Alyra"], realms=["Some Realm", "Qorv"], loose=["Qorv"])
    source = tmp_path / name
    rel = f"{FLAVOR}/WTF/Account/A/Some Realm/Alyra/{name}"

    def run(text: bytes) -> tuple[list[str], bytes]:
        source.write_bytes(text)
        item = lab_capture.Item(source, PurePosixPath(rel), FLAVOR, "1", "x")
        outcome = lab_capture.process(item, identity)
        return list(outcome.problems), outcome.result.data

    count = glued.lower().count(b"alyra")
    assert run(glued)[0] == [f"{EMBEDDED} x{count}"]
    problems, data = run(whole)
    assert problems == [] and b"alyra" not in data.lower() and b"labchara" in data.lower()
