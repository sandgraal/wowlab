"""`scripts/lab_capture.py`: third follow-up to M10-02.

1. A combat log is public game text. An identity match is replaced only in the
   quoted unit name right after one of the owner's own GUIDs; anywhere else
   (a creature, spell or NPC name) it refuses the file, with a count only.
2. SavedVariables nesting depth counts `{` outside strings and comments, so an
   unindented (Forever-style) file reports its real depth.
3. Positional array entries are found from the table structure, not from the
   `-- [n]` comments Forever does not write; the named colour form `|cn…:`
   counts as an escape.

CONSTRUCTED INPUT. Every log line and Lua body below is written for the test in
the shape of docs/LAB_FORMATS.md §4 and §8, with invented names only; trees
are synthetic, under `tmp_path`. No test needs or touches a real install
(ADR-0012).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import build_install, capture, lab_capture, outputs

Identity = lab_capture.Identity
GAME_TEXT = lab_capture.COMBAT_LOG_TEXT_LABEL
EMBEDDED = lab_capture.EMBEDDED_LABEL

OWN = b"Player-1-0000ABCD"
OTHER_OWN = b"Player-1-0000ABCE"  # an alt
TS = b"1/1/2026 00:00:00.000-4  "
HEADER = TS + b"COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
REAL = (b"Orlavin", b"Kestrel Hollow", b"KestrelHollow", b"Qorv")


def _identity(*guids: bytes) -> object:
    return Identity(
        characters=["Orlavin"], realms=["Kestrel Hollow"], extras=["Qorv"], guids=list(guids)
    )


def _process(identity: object, log: bytes, tmp_path: Path) -> tuple[list[str], bytes]:
    source = tmp_path / "WoWCombatLog-010126_000000.txt"
    source.write_bytes(log)
    rel = PurePosixPath("_classic_beta_/Logs/WoWCombatLog-010126_000000.txt")
    item = lab_capture.Item(source, rel, "_classic_beta_", "1", "combatlog")
    outcome = lab_capture.process(item, identity)
    return list(outcome.problems), outcome.result.data


# ─── 1. combat log: identity only in an own unit name ────────────────────────


def test_constructed_own_unit_names_after_own_guids_are_rewritten(tmp_path: Path) -> None:
    log = (
        HEADER
        + TS
        + b'SPELL_DAMAGE,Player-1-0000ABCD,"Orlavin-KestrelHollow-US",0x511,0x0,'
        + b'Creature-0-1-2-3-4-0000000000,"Training Dummy",0x10a48,0x0,585,"Smite",0x2\n'
        + TS
        + b'SPELL_HEAL,player-1-0000abcd,"Orlavin-KestrelHollow",0x511,0x0,'
        + b'Player-1-0000ABCE,"Qorv-KestrelHollow",0x511,0x0,2061,"Flash Heal",0x2\n'
        + TS
        + b"COMBATANT_INFO,Player-1-0000ABCD,1,2,3,[(1,2),(3,4)]\n"
    )
    problems, data = _process(_identity(OWN, OTHER_OWN), log, tmp_path)
    assert problems == []
    assert b'Player-9999-00000001,"Labchara-LabrealmaPartb-US"' in data
    assert b'Player-9999-00000001,"Labchara-LabrealmaPartb"' in data
    assert b'Player-9999-00000002,"Labnamea-LabrealmaPartb"' in data
    assert b'"Training Dummy"' in data and b'"Smite"' in data
    assert not [r for r in REAL if r in data]


@pytest.mark.parametrize(
    "line",
    [
        # a creature named with an own character name as a whole word
        b'SPELL_DAMAGE,Player-1-0000ABCD,"Orlavin-KestrelHollow",0x511,0x0,'
        b'Creature-0-1-2-3-4-0000000000,"Orlavin the Lost",0xa48,0x0,1,"Smite",0x2\n',
        # a spell named with an --extra-name before an apostrophe
        b'SPELL_AURA_APPLIED,Creature-0-1-2-3-4-0000000000,"Grave Warden",0xa48,0x0,'
        b'Player-1-0000ABCD,"Orlavin-KestrelHollow",0x511,0x0,4242,"Qorv\'s Spirit",0x20,BUFF\n',
        # an NPC named with an own realm (second name) in it
        b'SWING_DAMAGE,Creature-0-1-2-3-4-0000000001,"Kestrel Hollow Sentinel",0xa48,0x0,'
        b'Player-1-0000ABCD,"Orlavin-KestrelHollow",0x511,0x0\n',
        # a unit name in quotes, but not after a GUID of the owner's
        b'SPELL_CAST_SUCCESS,Vehicle-0-1-2-3-4-0000000002,"Qorv",0xa48,0x0,0000000000000000,nil\n',
    ],
)
def test_constructed_identity_in_combat_log_game_text_refuses(line: bytes, tmp_path: Path) -> None:
    problems, _data = _process(_identity(OWN), HEADER + TS + line, tmp_path)
    assert problems == [f"{GAME_TEXT} x1"]  # a count only: no offset, no line, no text


def test_constructed_game_text_label_counts_only_matches_outside_own_units(
    tmp_path: Path,
) -> None:
    log = (
        HEADER
        + (
            TS + b'SPELL_DAMAGE,Player-1-0000ABCD,"Orlavin-KestrelHollow",0x511,0x0,'
            b'Creature-0-1-2-3-4-0000000000,"Orlavin the Lost",0xa48,0x0,1,"Qorv\'s Spirit",0x2\n'
        )
        * 3
    )
    problems, _data = _process(_identity(OWN), log, tmp_path)
    assert problems == [f"{GAME_TEXT} x6"]


def test_constructed_combat_log_without_own_guids_refuses_rather_than_rewrites(
    tmp_path: Path,
) -> None:
    """Forever: no config file carries `lastCharacterGuid`, so without `--own-guid`
    the owner's own unit names are not known to be the owner's."""
    log = HEADER + TS + b'SPELL_DAMAGE,Player-1-0000ABCD,"Orlavin-KestrelHollow",0x511,0x0\n'
    problems, _data = _process(_identity(), log, tmp_path)
    assert f"{GAME_TEXT} x2" in problems
    assert any(p.startswith("unmapped player GUID") for p in problems)


def test_constructed_glued_name_in_combat_log_keeps_the_longer_word_label(
    tmp_path: Path,
) -> None:
    log = (
        HEADER
        + TS
        + b'SPELL_DAMAGE,Creature-0-1-2-3-4-0000000000,"Orlavinsworn Guard",0xa48,0x0,'
        + b'Player-1-0000ABCD,"Orlavin-KestrelHollow",0x511,0x0,1,"Qorvath Strike",0x1\n'
    )
    problems, _data = _process(_identity(OWN), log, tmp_path)
    assert problems == [f"{EMBEDDED} x2"]


@pytest.mark.parametrize(
    "body",
    [
        TS + b'SPELL_DAMAGE,Creature-0-1-2-3-4-0000000000,"Grave Warden",0xa48,0x0,'
        b'Creature-0-1-2-3-4-0000000001,"Hollow Sentinel",0xa48,0x0,1,"Smite",0x2\n',
        TS + b'ENCOUNTER_START,1234,"Warden of the Deep",1,5,36\n',
    ],
)
def test_constructed_control_combat_log_without_matches_passes_unchanged(
    body: bytes, tmp_path: Path
) -> None:
    log = HEADER + body
    problems, data = _process(_identity(OWN), log, tmp_path)
    assert problems == [] and data == log


def test_constructed_combat_log_own_guid_from_the_command_line_allows_the_rewrite(
    tmp_path: Path,
) -> None:
    """A unit named without a realm is never adopted from the log itself (see the
    leaks tests), so only `--own-guid` makes it the owner's."""
    root = tmp_path / "World of Warcraft"
    build_install(root)
    log = root / "_retail_" / "Logs" / "WoWCombatLog-092026_211403.txt"
    log.write_bytes(log.read_bytes() + TS + b'SPELL_HEAL,Player-5-0000BEEF,"Thrallmar",0x511\n')
    dest = "macos/_retail_/Logs/WoWCombatLog-092026_211403.txt"

    refused = tmp_path / "refused"
    assert capture(root, refused, "--flavor", "_retail_") == 1
    assert dest not in outputs(refused)

    allowed = tmp_path / "allowed"
    assert capture(root, allowed, "--flavor", "_retail_", "--own-guid", "Player-5-0000BEEF") == 0
    assert b',"Labchara",0x511\n' in outputs(allowed)[dest]


# ─── 2. SavedVariables nesting depth ─────────────────────────────────────────


def _nested(levels: int, indent: bool) -> bytes:
    lines = [b"", b"FlatDB = {"]
    for level in range(1, levels):
        lines.append(b"\t" * level * indent + b'["k%d"] = {' % level)
    lines.append(b"\t" * levels * indent + b'["leaf"] = 1,')
    for level in range(levels - 1, 0, -1):
        lines.append(b"\t" * level * indent + b"},")
    lines.append(b"}")
    return b"\n".join(lines) + b"\n"


@pytest.mark.parametrize("indent", [False, True])
def test_constructed_six_level_file_reports_depth_six_indented_or_not(indent: bool) -> None:
    data = _nested(6, indent)
    assert (b"\t" in data) is indent
    assert lab_capture.table_shape(data)[0] == 6
    assert lab_capture.sv_stats(data).depth == 6


@pytest.mark.parametrize(
    ("body", "depth"),
    [
        (b'X = {\n["a"] = "{{{{",\n["b"] = "}}}}",\n}\n', 1),
        (b'X = {\n["a"] = "esc \\" {{{ \\\\",\n["b"] = {},\n}\n', 2),
        (b"X = {\n['a'] = 'it\\'s {{{',\n}\n", 1),
        (b'X = {\n["a"] = [[ {{{ " ]],\n["b"] = [==[ ]] {{{ ]=] ]==],\n}\n', 1),
        (b'X = {\n"first", -- [1] {{{\n--[[ {{{\n]] --[=[ {{ ]=]\n}\n', 1),
        (b"X = }}}\nY = { { } }\n", 2),  # a stray `}` never goes below zero
        (b"X = 5\n", 0),
        (b"", 0),
    ],
)
def test_constructed_braces_inside_strings_and_comments_are_not_nesting(
    body: bytes, depth: int
) -> None:
    assert lab_capture.table_shape(body)[0] == depth


def test_constructed_unindented_deep_file_is_picked_as_deepest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    saved = root / "_retail_" / "WTF" / "Account" / "123456789#1" / "SavedVariables"
    (saved / "Flat.lua").write_bytes(_nested(6, indent=False))
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_") == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if "/Flat.lua`" in line]
    assert len(rows) == 1
    assert re.search(r"deepest nesting \([0-9]+ bytes, nesting depth 6\)", rows[0]), rows[0]
    assert any(d.endswith("/SavedVariables/Flat.lua") for d in outputs(out))


# ─── 3. positional entries and named colour codes (domain review of #51) ─────


@pytest.mark.parametrize(
    ("body", "positional"),
    [
        (b'\nListDB = {\n"first",\n"second",\n{\n1,\n-2.5,\n},\n}\n', 5),  # no `-- [n]`
        (b'\nListDB = {\n"first", -- [1]\n"second", -- [2]\n}\n', 2),  # the retail writer
        (b'\nKeyDB = {\n["a"] = 1,\n["b"] = {\n["c"] = true,\n},\nname = "x",\n}\n', 0),
        (b"\nX = { true; nil; false }\n", 3),
        (b'\nX = {\n["k"] = "{ 1, 2, 3 }",\n[1] = "a, b",\n}\n', 0),  # commas in strings
        (b"\nX = {\n[[long, string]],\n[==[ also , one ]==],\n[ [[key]] ] = 1,\n}\n", 2),
        (b"\nX = {\n-- a comment, with commas\n--[[ a long\n, one ]]\n}\n", 0),
        (b"\nX = {}\nY = 5\n", 0),
    ],
)
def test_constructed_positional_entries_are_found_without_comments(
    body: bytes, positional: int
) -> None:
    assert lab_capture.table_shape(body)[1] == positional
    assert lab_capture.sv_stats(body).positional == positional


@pytest.mark.parametrize(
    ("body", "escapes"),
    [
        (b'\nX = "|cnIQ0:Plain Item|r"\n', 1),  # the named form, no hyperlink
        (b'\nX = "|cnNORMAL_FONT_COLOR:text|r and |cff1eff00green|r"\n', 2),
        (b'\nX = "|cn: not a colour name|r, |c1234 short"\n', 0),
        (b'\nX = "|Hitem:1|h[x]|h"\n', 1),
    ],
)
def test_constructed_named_colour_codes_count_as_escapes(body: bytes, escapes: int) -> None:
    assert lab_capture.sv_stats(body).escapes == escapes


def test_constructed_unindented_positional_file_is_picked_as_positional_array(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    saved = root / "_retail_" / "WTF" / "Account" / "123456789#1" / "SavedVariables"
    items = b"".join(b'"entry %d",\n' % i for i in range(12))
    (saved / "List.lua").write_bytes(b"\nListDB = {\n" + items + b"}\n")
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_") == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if "/List.lua`" in line]
    assert len(rows) == 1
    assert "positional array (" in rows[0], rows[0]


# ─── 4. hostile input: unclosed openers stay linear (security review of #52) ─


@pytest.mark.parametrize(
    "opener",
    [b"--[[ open\n", b"--[==[ open ]=]\n", b"[[ open\n", b"[=[ open ]]\n", b",--[[\n", b'"\\'],
)
def test_constructed_unclosed_openers_do_not_rescan_the_file(opener: bytes) -> None:
    """10^4 openers with no closer: a closer search per opener would take seconds."""
    import time

    data = b"\nX = {\n" + opener * 10_000 + b"{ { } }\n"
    started = time.perf_counter()
    depth, _positional = lab_capture.table_shape(data)
    assert time.perf_counter() - started < 1.0
    assert depth == 1  # the first unclosed opener runs to the end, as in Lua


def test_constructed_unterminated_quote_stops_at_its_line_break() -> None:
    data = b'\nX = {\n"open, {\n{ 1 },\n}\n' + b"'x\n" * 10_000
    assert lab_capture.table_shape(data) == (2, 2)  # `"open` and `1`
