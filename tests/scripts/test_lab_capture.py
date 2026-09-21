"""`scripts/lab_capture.py` against a synthetic install tree (M10-02).

The tree is built here, in `tmp_path`, in our own layout, so constructed input
is legitimate (docs/LAB_PLAN.md §9). Every identity string below is invented.
No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lab_capture.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lab_capture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()

# ─── the synthetic install ───────────────────────────────────────────────────

ACCOUNT = "123456789#1"
ACCOUNT_NUMBER = "123456789"
ACCOUNT_NAME = "SECRETACCT"
REALM = "Area 52"
REALM_NORMALISED = "Area52"
MAIN = "Thrallmar"
ALT = "Zugzug"
OWN_GUID = "Player-1234-0ABCDEF0"
FOREIGN_GUID = "Player-77-0BADBEEF"
EMAIL = "someone.real@example.com"
BATTLETAG = "Stranger#12345"

# Every byte string the scrubber is allowed to touch in this tree.
SENSITIVE = [
    s.encode()
    for s in (
        ACCOUNT,
        ACCOUNT_NUMBER,
        ACCOUNT_NAME,
        "!SECRETACCT|",
        REALM,
        REALM_NORMALISED,
        REALM.lower(),
        REALM_NORMALISED.lower(),
        MAIN,
        MAIN.lower(),
        MAIN.upper(),
        ALT,
        ALT.lower(),
        OWN_GUID,
    )
]
REAL_NAMES = [ACCOUNT, ACCOUNT_NUMBER, ACCOUNT_NAME, REALM, REALM_NORMALISED, MAIN, ALT, OWN_GUID]

BUILD_INFO = (
    "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|Version!STRING:0|Product!STRING:0\n"
    "us|1|0123456789abcdef0123456789abcdef|12.1.5.65432|wow\n"
    "us|1|fedcba9876543210fedcba9876543210|1.60.1.60001|wow_classic_beta\n"
)

CONFIG_WTF = (
    'SET portal "US"\r\n'
    f'SET accountName "{ACCOUNT_NAME}"\r\n'
    f'SET accountList "!{ACCOUNT_NAME}|"\r\n'
    f'SET lastCharacterGuid "{OWN_GUID}"\r\n'
    f'SET realmName "{REALM}"\r\n'
    'SET gxMaximize "1"\r\n'
)

DEEP_LUA = (
    "\nDeepAddonDB = {\n"
    '\t["profileKeys"] = {\n'
    f'\t\t["{MAIN} - {REALM}"] = "Default",\n'
    f'\t\t["{ALT} - {REALM}"] = "Default",\n'
    f'\t\t["{MAIN.lower()}-{REALM_NORMALISED.lower()}"] = "lowered",\n'
    "\t},\n"
    '\t["chars"] = {\n'
    '\t\t["a"] = {\n'
    '\t\t\t["b"] = {\n'
    '\t\t\t\t["c"] = {\n'
    f'\t\t\t\t\t["guid"] = "{OWN_GUID}",\n'
    f'\t\t\t\t\t["owner"] = "{MAIN.upper()}",\n'
    '\t\t\t\t\t["scale"] = 0.8500000238418579,\n'
    f'\t\t\t\t\t["noise"] = 0.{ACCOUNT_NUMBER}1,\n'
    '\t\t\t\t\t["offset"] = -12.5,\n'
    "\t\t\t\t},\n"
    "\t\t\t},\n"
    "\t\t},\n"
    "\t},\n"
    '\t["list"] = {\n'
    '\t\t"first", -- [1]\n'
    '\t\t"|cffff8000Café|r", -- [2]\n'
    "\t},\n"
    "}\n"
)

COMBAT_LOG = (
    "9/20/2026 21:14:03.123-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
    f'9/20/2026 21:14:05.871-4  SPELL_DAMAGE,{OWN_GUID},"{MAIN}-{REALM_NORMALISED}-US",0x511,0x0,'
    'Creature-0-1-2-3-4-0000000000,"Training Dummy",0x10a48,0x0,12345,"Smite",0x2\n'
    f"9/20/2026 21:14:06.001-4  COMBATANT_INFO,{OWN_GUID},1,2,3,[(1,2),(3,4)]\n"
    + "".join(f"9/20/2026 21:14:07.{i:03d}-4  SWING_DAMAGE,filler,{i}\n" for i in range(50))
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def build_install(root: Path) -> None:
    _write(root / ".build.info", BUILD_INFO)
    for flavor, product in (("_retail_", "wow"), ("_classic_beta_", "wow_classic_beta")):
        base = root / flavor
        _write(base / ".flavor.info", f"Product Flavor!STRING:0\n{product}\n")
        _write(base / "WTF" / "Config.wtf", CONFIG_WTF)
        account = base / "WTF" / "Account" / ACCOUNT
        _write(account / "SavedVariables.lua", f'\nBlizzardDB = {{\n\t["last"] = "{MAIN}",\n}}\n')
        _write(account / "config-cache.wtf", f'SET lastCharacterGuid "{OWN_GUID}"\nSET x "1"\n')
        _write(account / "bindings-cache.wtf", "bind CTRL-1 ACTIONBUTTON1\n")
        _write(
            account / "macros-cache.txt",
            f'VER 3 0000000000000001 "Heal" "INV_MISC_QUESTIONMARK"\n'
            f"/cast [@mouseover,help][@player] Flash Heal\n/w {ALT} hi\nEND\n",
        )
        _write(account / "edit-mode-cache-account.txt", "layout 1\n")
        saved = account / "SavedVariables"
        _write(saved / "Tiny.lua", "\nTinyFlag = true\n")
        _write(saved / "Deep.lua", DEEP_LUA)
        _write(saved / "Deep.lua.bak", DEEP_LUA.replace("Default", "Older"))
        character = account / REALM / MAIN
        _write(character / "config-cache.wtf", 'SET autoLootDefault "1"\n')
        _write(character / "bindings-cache.wtf", "bind BUTTON4 TOGGLEAUTORUN\n")
        _write(character / "macros-cache.txt", "")
        _write(character / "AddOns.txt", "Solo: enabled\nMulti: enabled\n")
        _write(character / "layout-local.txt", "Frame: x\n")
        _write(character / "chat-cache.txt", f"CHANNEL {MAIN}\n")
        _write(character / "SavedVariables" / "PerChar.lua", f'\nPerCharDB = "{MAIN}-{REALM}"\n')
        # The alt last logged in long ago: the tool captures the most recent character.
        _write(account / REALM / ALT / "AddOns.txt", "Solo: enabled\n")
        for stale in (account / REALM / ALT / "AddOns.txt", account / REALM / ALT):
            os.utime(stale, (1_000_000_000, 1_000_000_000))
        addons = base / "Interface" / "AddOns"
        _write(addons / "Solo" / "Solo.toc", "## Interface: 120105\n## Title: Solo\nSolo.lua\n")
        _write(addons / "Multi" / "Multi.toc", "## Interface: 120105\nMulti.lua\n")
        _write(addons / "Multi" / "Multi_Mainline.toc", "## Interface: 120105\nMulti.lua\n")
        _write(
            addons / "Cond" / "Cond.toc",
            "## Interface: 120105\n[AllowLoadGameType mainline] Cond.lua\n",
        )
        _write(addons / "Blizzard_Exported" / "Blizzard_Exported.toc", "## Title: Blizzard\n")
        _write(base / "Logs" / "WoWCombatLog-092026_211403.txt", COMBAT_LOG)


def tree_state(root: Path) -> dict[str, tuple[str, int]]:
    """Every path under root with its content hash and mtime; directories too."""
    state: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
        state[path.relative_to(root).as_posix()] = (digest, path.stat().st_mtime_ns)
    return state


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    return root


def capture(root: Path, out: Path, *extra: str) -> int:
    return int(
        lab_capture.main(["--root", str(root), "--out", str(out), "--platform", "macos", *extra])
    )


def outputs(out: Path) -> dict[str, bytes]:
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def source_of(root: Path, dest: str) -> Path:
    """Map a pseudonymised output path back to the file it came from."""
    parts = dest.split("/")[1:]  # drop the platform folder
    real = {"90000001#1": ACCOUNT, "Labrealma": REALM, "Labchara": MAIN, "Labcharb": ALT}
    return root.joinpath(*[real.get(p, p) for p in parts])


# ─── acceptance: stable pseudonyms ───────────────────────────────────────────


def test_pseudonyms_are_stable_across_files_paths_and_flavors(
    install: Path, tmp_path: Path
) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out) == 0
    written = outputs(out)

    # Both flavors, the account, the realm and the character all landed on the
    # same pseudonyms, in paths...
    for flavor in ("_retail_", "_classic_beta_"):
        assert f"macos/{flavor}/WTF/Account/90000001#1/Labrealma/Labchara/AddOns.txt" in written
    # ...and in contents: wherever the original named a character, the output
    # names that character's one pseudonym and never the other's.
    seen_main = seen_alt = 0
    for dest, data in written.items():
        original = source_of(install, dest).read_bytes()
        assert data.count(b"Labchara") == original.count(MAIN.encode()), dest
        assert data.count(b"Labcharb") == original.count(ALT.encode()), dest
        assert data.count(b"Labrealma") == original.count(REALM.encode()) + original.count(
            REALM_NORMALISED.encode()
        ), dest
        seen_main += data.count(b"Labchara")
        seen_alt += data.count(b"Labcharb")
    assert seen_main >= 6 and seen_alt >= 2, "the tree must exercise cross-file references"

    # Nothing real survives anywhere, in any casing.
    for dest, data in written.items():
        for name in REAL_NAMES:
            assert name.casefold() not in dest.casefold(), (dest, name)
            if name != ACCOUNT_NUMBER:  # survives only inside the longer float, see below
                assert name.casefold().encode() not in data.lower(), (dest, name)


def test_two_runs_agree_with_each_other(install: Path, tmp_path: Path) -> None:
    assert capture(install, tmp_path / "one") == 0
    assert capture(install, tmp_path / "two") == 0
    assert outputs(tmp_path / "one") == outputs(tmp_path / "two")


# ─── acceptance: untouched bytes are identical ───────────────────────────────


def _sensitive_spans(original: bytes) -> list[tuple[int, int]]:
    """Merged, ordered spans of `original` that this test knows to be identity."""
    found = sorted(
        (m.start(), m.end())
        for needle in SENSITIVE
        for m in re.finditer(re.escape(needle), original)
    )
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


# What may stand where an identity span stood: nothing (a blanked CVar), one or
# more pseudonyms, or the span itself (a digit run inside a longer number).
_PSEUDONYM = (
    rb"(?:(?i:labchar[a-z]+|labrealm[a-z]+)|9000000[0-9](?:#[0-9])?|Player-9999-[0-9A-F]{8}|[ -])*"
)


def test_bytes_outside_identity_spans_are_identical_by_offset(
    install: Path, tmp_path: Path
) -> None:
    """The independent oracle: a diff of offsets that never asks the tool what it edited."""
    out = tmp_path / "incoming"
    assert capture(install, out) == 0
    rewritten = 0
    for dest, data in outputs(out).items():
        original = source_of(install, dest).read_bytes()
        if "WoWCombatLog" in dest:
            original = b"".join(original.splitlines(keepends=True)[:2000])
        spans = _sensitive_spans(original)

        # Cut the original at the identity spans. Every piece between them
        # must reappear in the output, whole, in order...
        cuts = [0, *(offset for span in spans for offset in span), len(original)]
        pieces = [original[a:b] for a, b in zip(cuts[0::2], cuts[1::2], strict=True)]
        gaps = [rb"(%s|%s)" % (_PSEUDONYM, re.escape(original[s:e])) for s, e in spans]
        pattern = b"".join(re.escape(p) + g for p, g in zip(pieces, [*gaps, b""], strict=True))
        match = re.fullmatch(pattern, data, re.DOTALL)
        assert match is not None, f"{dest}: bytes outside the identity spans differ"

        # ...and at exactly the offset the replacements before it predict.
        shift = 0
        for index, ((start, end), piece) in enumerate(zip(spans, pieces[1:], strict=True), 1):
            shift += len(match.group(index)) - (end - start)
            assert data[end + shift : end + shift + len(piece)] == piece, (dest, end)
            rewritten += match.group(index) != original[start:end]
        assert len(data) == len(original) + shift
        assert data.count(b"\r\n") == original.count(b"\r\n"), f"{dest}: line endings changed"

        # A plain diff agrees: no changed region touches a line with no identity on it.
        before = original.splitlines(keepends=True)
        after = data.splitlines(keepends=True)
        assert len(before) == len(after), f"{dest}: line count changed"
        for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(
            None, before, after, autojunk=False
        ).get_opcodes():
            if tag != "equal":
                assert all(_sensitive_spans(line) for line in before[i1:i2]), (dest, i1, i2)
    assert rewritten >= 20, "the tree must contain identity to rewrite"


def test_edit_list_accounts_for_every_changed_offset() -> None:
    identity = lab_capture.Identity(
        accounts=[ACCOUNT], realms=[REALM], characters=[MAIN, ALT], guids=[OWN_GUID.encode()]
    )
    original = DEEP_LUA.encode("utf-8")
    result = identity.scrub(original)
    assert not result.problems

    cursor_in = cursor_out = 0
    for edit in result.edits:
        assert original[edit.offset : edit.end] == edit.old
        untouched = original[cursor_in : edit.offset]
        assert result.data[cursor_out : cursor_out + len(untouched)] == untouched
        cursor_out += len(untouched)
        assert result.data[cursor_out : cursor_out + len(edit.new)] == edit.new
        cursor_out += len(edit.new)
        cursor_in = edit.end
        assert edit.old in SENSITIVE, f"edited something that is not identity: {edit.old!r}"
    assert result.data[cursor_out:] == original[cursor_in:]

    # Number text is never touched: the account number inside a longer float stays.
    assert f"0.{ACCOUNT_NUMBER}1".encode() in result.data
    assert b"0.8500000238418579" in result.data
    assert "|cffff8000Café|r".encode() in result.data


def test_identity_cvars_are_blanked_and_portal_stays(install: Path, tmp_path: Path) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out) == 0
    config = (out / "macos" / "_retail_" / "WTF" / "Config.wtf").read_bytes()
    assert config == (
        b'SET portal "US"\r\n'
        b'SET accountName ""\r\n'
        b'SET accountList ""\r\n'
        b'SET lastCharacterGuid ""\r\n'
        b'SET realmName "Labrealma"\r\n'
        b'SET gxMaximize "1"\r\n'
    )


# ─── acceptance: the three refusals ──────────────────────────────────────────

REFUSALS = {
    "email": (f'\nLeakDB = {{\n\t["contact"] = "{EMAIL}",\n}}\n', "email address"),
    "battletag": (f'\nLeakDB = {{\n\t["friend"] = "{BATTLETAG}",\n}}\n', "BattleTag"),
    "unmapped-guid": (
        f'\nLeakDB = {{\n\t["seen"] = "{FOREIGN_GUID}",\n}}\n',
        "unmapped player GUID",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_refuses_and_writes_nothing_for_that_file(
    case: str, install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body, label = REFUSALS[case]
    saved = install / "_retail_" / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    _write(saved / "Leak.lua", body)
    out = tmp_path / "incoming"

    assert capture(install, out, "--flavor", "_retail_", "--sv", "Leak.lua") == 1

    written = outputs(out)
    assert not [d for d in written if d.endswith("Leak.lua")], "a refused file was written"
    assert "macos/_retail_/WTF/Config.wtf" in written, "the clean files are still captured"
    text = capsys.readouterr()
    refused = [line for line in text.out.splitlines() if line.startswith("REFUSED")]
    assert len(refused) == 1 and "Leak.lua" in refused[0] and label in refused[0]
    # The report never repeats what it found.
    for secret in (EMAIL, BATTLETAG, FOREIGN_GUID):
        assert secret not in text.out and secret not in text.err
    assert not [row for row in text.out.splitlines() if row.startswith("|") and "Leak.lua" in row]


def test_automatic_selection_passes_over_a_candidate_it_would_refuse(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    addons = install / "_retail_" / "Interface" / "AddOns"
    _write(addons / "AAAFirst" / "AAAFirst.toc", f"## Author: x <{EMAIL}>\nmain.lua\n")
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_") == 0
    written = outputs(out)
    assert "macos/_retail_/Interface/AddOns/Solo/Solo.toc" in written
    assert not [d for d in written if "AAAFirst" in d]
    assert "AAAFirst.toc: email address" in capsys.readouterr().out


def test_combat_log_with_another_player_is_refused(install: Path, tmp_path: Path) -> None:
    log = install / "_retail_" / "Logs" / "WoWCombatLog-092026_211403.txt"
    foreign = f'9/20/2026 21:15:00.000-4  SPELL_HEAL,{FOREIGN_GUID},"Bystander-Illidan-US",0x514\n'
    log.write_bytes(log.read_bytes() + foreign.encode())
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_") == 1
    assert not [d for d in outputs(out) if "WoWCombatLog" in d]


def test_unblanked_identity_cvar_in_an_unexpected_shape_is_refused() -> None:
    identity = lab_capture.Identity(accounts=[ACCOUNT])
    result = identity.scrub(b"SET accountName unquoted-value\n", blank_cvars=True)
    assert any("identity CVar" in p for p in result.problems)


def test_mixed_case_identity_survivor_is_refused() -> None:
    identity = lab_capture.Identity(characters=[MAIN])
    result = identity.scrub(b'["who"] = "tHRALLMAR",\n')
    assert any("surviving identity" in p for p in result.problems)


# ─── acceptance: --dry-run writes nothing; the install is never written ──────


def test_dry_run_writes_nothing(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    before_install = tree_state(install)
    before_tmp = sorted(p.name for p in tmp_path.iterdir())

    assert capture(install, out, "--dry-run") == 0

    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == before_tmp
    assert tree_state(install) == before_install
    stdout = capsys.readouterr().out
    assert "would write" in stdout and "| `macos/_retail_/WTF/Config.wtf` |" in stdout


def test_install_is_untouched_by_a_real_run(install: Path, tmp_path: Path) -> None:
    before = tree_state(install)
    assert capture(install, tmp_path / "incoming") == 0
    assert tree_state(install) == before


@pytest.mark.parametrize("where", ["inside", "is-root"])
def test_out_inside_the_install_is_rejected(where: str, install: Path) -> None:
    before = tree_state(install)
    out = install / "_retail_" / "capture" if where == "inside" else install
    assert capture(install, out) == 2
    assert tree_state(install) == before


# ─── capture set, provenance, hygiene ────────────────────────────────────────


def test_capture_set_matches_the_runbook(install: Path, tmp_path: Path) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out, "--flavor", "_retail_", "--log-lines", "10") == 0
    written = outputs(out)
    account = "macos/_retail_/WTF/Account/90000001#1"
    character = f"{account}/Labrealma/Labchara"
    expected = {
        "macos/.build.info",
        "macos/_retail_/.flavor.info",
        "macos/_retail_/WTF/Config.wtf",
        *(f"{account}/{n}" for n in lab_capture.ACCOUNT_FILES),
        f"{account}/edit-mode-cache-account.txt",
        *(f"{character}/{n}" for n in lab_capture.CHARACTER_FILES),
        f"{account}/SavedVariables/Tiny.lua",
        f"{account}/SavedVariables/Deep.lua",
        f"{account}/SavedVariables/Deep.lua.bak",
        "macos/_retail_/Interface/AddOns/Solo/Solo.toc",
        "macos/_retail_/Interface/AddOns/Multi/Multi.toc",
        "macos/_retail_/Interface/AddOns/Multi/Multi_Mainline.toc",
        "macos/_retail_/Interface/AddOns/Cond/Cond.toc",
        "macos/_retail_/Logs/WoWCombatLog-092026_211403.txt",
    }
    assert expected <= set(written)
    assert not [d for d in written if "Blizzard_" in d], "exported Blizzard code is not a fixture"
    log = written["macos/_retail_/Logs/WoWCombatLog-092026_211403.txt"]
    assert log.count(b"\n") == 10 and log.startswith(
        b"9/20/2026 21:14:03.123-4  COMBAT_LOG_VERSION"
    )
    assert b'Player-9999-00000001,"Labchara-Labrealma-US"' in log


def test_prints_one_pasteable_provenance_row_per_file(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert capture(install, out) == 0
    stdout = capsys.readouterr().out
    rows = [line for line in stdout.splitlines() if line.startswith("| `")]
    cells = [[c.strip() for c in re.split(r"(?<!\\)\|", row.strip().strip("|"))] for row in rows]
    assert {c[0].strip("`") for c in cells} == set(outputs(out))
    by_file = {c[0].strip("`"): c for c in cells}
    for row in cells:
        assert len(row) == 9  # the index's column count
        assert all(row[i] and row[i] != "-" for i in (1, 2, 3, 4, 5, 7))
        assert row[6] == "owner"
    config = by_file["macos/_classic_beta_/WTF/Config.wtf"]
    assert config[1:5] == ["config-wtf", "_classic_beta_", "1.60.1.60001", "macos"]
    assert config[7] == "identity-rewritten: 1; cvars-blanked: 3" and "CRLF" in config[8]
    assert by_file["macos/_retail_/WTF/Config.wtf"][3] == "12.1.5.65432"
    assert by_file["macos/_retail_/.flavor.info"][7] == "none"
    # Without --show-map the terminal output is as clean as the files.
    for name in REAL_NAMES:
        assert name not in stdout


def test_kind_renames_the_output_folder_but_not_the_flavor_column(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "incoming"
    assert (
        capture(install, out, "--kind", "_classic_beta_=forever", "--kind", "_retail_=retail") == 0
    )
    written = set(outputs(out))
    assert "macos/forever/WTF/Config.wtf" in written and "macos/retail/.flavor.info" in written
    assert not [d for d in written if "_classic_beta_" in d or "_retail_" in d]
    assert (
        "| `macos/forever/.flavor.info` | flavor-info | _classic_beta_ | 1.60.1.60001 | macos |"
        in (capsys.readouterr().out)
    )
    assert capture(install, out, "--kind", "_retail_=../escape") == 2


def test_identity_edge_cases() -> None:
    identity = lab_capture.Identity(realms=["Azjol-Nerub"], characters=["Thrall", MAIN])
    result = identity.scrub(b'"Thrallmar-AzjolNerub" "Thrall-Azjol-Nerub" "thrall" "enthrall"')
    # Longest name first; normalised realm form; lower-case only as a whole word.
    assert result.data == b'"Labcharb-Labrealma" "Labchara-Labrealma" "labchara" "enthrall"'

    with pytest.raises(lab_capture.CaptureError):
        lab_capture.Identity(characters=["True"])
    with pytest.raises(lab_capture.CaptureError):
        lab_capture.Identity(characters=["X"])


def test_script_is_standard_library_only_and_names_no_flavor() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names
    for constant in ("_retail_", "_classic", "wow_classic", "wowt"):  # L6
        assert constant not in source
    # Every open is read-only; the single write and the single mkdir are the
    # ones under --out.
    assert re.findall(r"\bopen\(([^)]*)\)", source) == ['"rb"']
    for forbidden in ("write_text", "unlink", "rmtree", "rename", "replace(", "touch(", "shutil"):
        assert forbidden not in source.replace('cell.replace("|"', ""), forbidden
    assert source.count(".write_bytes(") == 1 and source.count(".mkdir(") == 1
