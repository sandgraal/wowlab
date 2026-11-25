"""The practice summary (M22-20): segments of your activity in a combat log,
and for one of them casts per spell, global-cooldown use, cooldown use,
buff uptime and damage, through the library and `wowlab log practice`.

Graded on a constructed training-dummy log (labelled `constructed`: every
number is known in advance), on the owner's real beta fight
(`fixtures/macos/`, build 1.60.1.70009) and on the real launch-client slice
(`fixtures/macos-71037/`, build 1.60.1.71037), with the recorded
`SpellCooldowns` subset of 1.60.1.71037 as the spell timing table. No test
reads a real install; the CLI runs on a tree copied into `tmp_path`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, combatlog, gamedata, install, practice
from wowlab_core.cli import PracticeReport

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAUNCH = FIXTURES / "macos-71037" / "forever" / "Logs" / "WoWCombatLog-053126_104937.txt"
BETA = FIXTURES / "macos" / "forever" / "Logs" / "WoWCombatLog-040126_021630.txt"
TIMING_CSV = FIXTURES / "wago" / "SpellCooldowns.1.60.1.71037.subset.csv"
VERSION = "1.60.1.71037"  # the subset's build and the 71037 capture's; tests may name it (L6)
FLAVOR = "_forever_"

runner = CliRunner()

# ─── a constructed training-dummy log ────────────────────────────────────────

ME = "Player-1-0000000A"
OTHER = "Player-1-0000000B"
PET = "Pet-0-1-1-1-416-0000000001"
DUMMY = "Creature-0-1-1-1-31146-0000000001"
_UNITS = {
    ME: ('"Me-Realm-"', "0x511"),
    OTHER: ('"Other-Realm-"', "0x548"),
    PET: ('"Imp"', "0x1111"),
    DUMMY: ('"Training Dummy"', "0xa28"),
}


def _unit(guid: str) -> str:
    name, flags = _UNITS[guid]
    return f"{guid},{name},{flags},0x80000000"


def _adv(unit: str, owner: str = "0000000000000000") -> str:
    return ",".join([unit, owner, *["0"] * 17])


def _at(seconds: float) -> str:
    whole = int(seconds)
    ms = round((seconds - whole) * 1000)
    return f"1/2/2026 10:{whole // 60:02d}:{whole % 60:02d}.{ms:03d}-4"


def _rec(seconds: float, body: str) -> str:
    return f"{_at(seconds)}  {body}\r\n"


def _cast(t: float, spell: int, name: str, event: str = "SPELL_CAST_SUCCESS") -> str:
    if event == "SPELL_CAST_START":
        return _rec(
            t,
            f'{event},{_unit(ME)},0000000000000000,nil,0x80000000,0x80000000,{spell},"{name}",0x20',
        )
    return _rec(t, f'{event},{_unit(ME)},{_unit(DUMMY)},{spell},"{name}",0x20,{_adv(ME)}')


def _aura(t: float, event: str, spell: int, name: str) -> str:
    return _rec(t, f'{event},{_unit(ME)},{_unit(ME)},{spell},"{name}",0x20,BUFF')


def _spell_hit(t: float, src: str, spell: int, amount: int, event: str = "SPELL_DAMAGE") -> str:
    return _rec(
        t,
        f'{event},{_unit(src)},{_unit(DUMMY)},{spell},"Hit",0x20,{_adv(DUMMY)},'
        f"{amount},{amount},-1,32,0,0,0,nil,nil,nil,ST",
    )


def _swing(t: float, src: str, amount: int, owner: str = "0000000000000000") -> str:
    return _rec(
        t,
        f"SWING_DAMAGE,{_unit(src)},{_unit(DUMMY)},{_adv(src, owner)},"
        f"{amount},{amount},-1,1,0,0,0,nil,nil,nil",
    )


_HEADER = "COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,1.60.1,PROJECT_ID,18"
CONSTRUCTED = "".join(
    [
        _rec(0.0, _HEADER),
        _aura(1.0, "SPELL_AURA_APPLIED", 706, "Demon Armor"),
        _rec(5.0, f'SPELL_SUMMON,{_unit(ME)},{_unit(PET)},688,"Summon Imp",0x20'),
        # Segment 1, 10.0 to 25.0.
        _cast(10.0, 686, "Shadow Bolt", "SPELL_CAST_START"),
        _aura(11.0, "SPELL_AURA_REMOVED", 706, "Demon Armor"),
        _cast(12.0, 686, "Shadow Bolt"),
        _spell_hit(12.5, ME, 686, 100),
        _aura(13.0, "SPELL_AURA_REMOVED", 1459, "Arcane Intellect"),
        _cast(13.0, 172, "Corruption"),
        _swing(13.2, PET, 30, owner=ME),
        _spell_hit(14.0, OTHER, 999, 500),
        _aura(15.0, "SPELL_AURA_APPLIED", 18288, "Amplify Curse"),
        _cast(15.0, 18288, "Amplify Curse"),
        _spell_hit(16.0, ME, 172, 20, "SPELL_PERIODIC_DAMAGE"),
        _cast(17.0, 5019, "Shoot"),
        _cast(21.0, 172, "Corruption"),
        _swing(22.0, ME, 40),
        _spell_hit(25.0, ME, 172, 20, "SPELL_PERIODIC_DAMAGE"),
        # Segment 2: after a 15 s pause; ended by logging switched off and on.
        _cast(40.0, 686, "Shadow Bolt"),
        _rec(41.0, _HEADER),
        # Segment 3.
        _cast(42.0, 6789, "Death Coil"),
    ]
)
TIMINGS = {
    686: practice.SpellTiming(gcd_ms=1500, cooldown_ms=0),
    172: practice.SpellTiming(gcd_ms=1500, cooldown_ms=0),
    18288: practice.SpellTiming(gcd_ms=0, cooldown_ms=180000),
    6789: practice.SpellTiming(gcd_ms=1500, cooldown_ms=120000),
}


def _entries(text: str = CONSTRUCTED) -> list[combatlog.Entry]:
    return list(combatlog.tokenize(text.encode()))


def _scan() -> practice.SegmentScan:
    return practice.find_segments(_entries())


def test_segments_constructed() -> None:
    scan = _scan()
    assert [(s.start, s.end, s.casts, s.damage) for s in scan.segments] == [
        (_at(10.0), _at(25.0), 5, 210),
        (_at(40.0), _at(40.0), 1, 0),
        (_at(42.0), _at(42.0), 1, 0),
    ]
    first = scan.segments[0]
    assert (first.index, first.guid, first.name) == (1, ME, "Me-Realm-")
    assert first.seconds == 15.0
    assert first.targets == ("Training Dummy",)
    assert (first.first_line, first.last_line) == (4, 18)


def test_a_longer_gap_joins_the_first_two_segments_constructed() -> None:
    scan = practice.find_segments(_entries(), gap=20.0)
    assert [s.casts for s in scan.segments] == [6, 1]


def test_summary_with_the_timing_table_constructed() -> None:
    s = practice.summarize(_entries(), _scan().segments[0], TIMINGS)
    assert (s.seconds, s.casts, s.timings) == (15.0, 5, "table")
    assert [(u.spell_name, u.casts, u.per_minute, u.median_gap) for u in s.spells] == [
        ("Corruption", 2, 8.0, 8.0),
        ("Shadow Bolt", 1, 4.0, None),
        ("Shoot", 1, 4.0, None),
        ("Amplify Curse", 1, 4.0, None),
    ]
    shoot = next(u for u in s.spells if u.spell_name == "Shoot")
    assert shoot.gcd_ms is None
    # Busy: the Shadow Bolt's 2 s cast, then two 1.5 s global cooldowns.
    assert s.gcd.model_dump() == {
        "busy_seconds": 5.0,
        "share": 0.3333,
        "on_gcd": 3,
        "off_gcd": 1,
        "no_gcd_data": 1,
        "pauses": 1,
        "longest_pause": 6.5,
        "assumed": False,
    }
    assert [(c.spell_name, c.casts, c.possible, c.share) for c in s.cooldowns] == [
        ("Death Coil", 0, 1, 0.0),
        ("Amplify Curse", 1, 1, 1.0),
    ]
    assert [(b.spell_name, b.seconds, b.share, b.on_before_log) for b in s.buffs] == [
        ("Amplify Curse", 10.0, 0.6667, False),
        ("Arcane Intellect", 3.0, 0.2, True),
        ("Demon Armor", 1.0, 0.0667, False),
    ]
    assert s.damage.total == 210
    assert [(d.guid, d.you, d.amount) for d in s.damage.by_source] == [
        (ME, True, 180),
        (PET, False, 30),
    ]
    assert (s.damage.others_on_targets, s.damage.others_lines) == (500, 1)
    assert s.dps == 14.0


def test_summary_without_the_table_assumes_a_global_cooldown_constructed() -> None:
    s = practice.summarize(_entries(), _scan().segments[0], None)
    assert s.timings == "assumed"
    assert (s.gcd.busy_seconds, s.gcd.on_gcd, s.gcd.assumed) == (8.0, 5, True)
    assert s.cooldowns == ()
    assert {u.gcd_ms for u in s.spells} == {practice.ASSUMED_GCD_MS}


def test_summary_reads_the_same_with_the_event_filter_constructed() -> None:
    whole = practice.summarize(_entries(), _scan().segments[0], TIMINGS)
    filtered_entries = list(
        combatlog.tokenize(CONSTRUCTED.encode(), events=practice.PRACTICE_EVENTS)
    )
    assert practice.summarize(filtered_entries, _scan().segments[0], TIMINGS) == whole


def test_a_segment_of_another_log_is_refused_constructed() -> None:
    segment = _scan().segments[0].model_copy(update={"start": "not a time"})
    with pytest.raises(ValueError):
        practice.summarize(_entries(), segment, TIMINGS)


def test_moment_reads_milliseconds_and_the_offset() -> None:
    assert practice.moment("1/2/2026 10:00:01.250-4") == pytest.approx(
        practice.moment("1/2/2026 14:00:01.000+0") + 0.25  # type: ignore[operator]
    )
    assert practice.moment("1/2 10:00:01.250") is None


def test_timings_from_the_recorded_table() -> None:
    timings = practice.timings_from_rows(gamedata._iter_rows(TIMING_CSV))
    assert timings[100] == practice.SpellTiming(gcd_ms=0, cooldown_ms=15000)  # Charge
    assert timings[2687] == practice.SpellTiming(gcd_ms=0, cooldown_ms=60000)  # Bloodrage
    assert timings[7386] == practice.SpellTiming(gcd_ms=1500, cooldown_ms=0)  # Sunder Armor
    assert 75 not in timings  # Auto Shot has no row


# ─── real segments ───────────────────────────────────────────────────────────


def test_real_beta_fight_is_one_segment_and_summarizes() -> None:
    scan = practice.find_segments(combatlog.read_log(BETA, events=practice.PRACTICE_EVENTS))
    (seg,) = scan.segments
    assert (seg.casts, seg.damage, seg.targets) == (6, 180, ("Vampiric Duskbat",))
    timings = practice.timings_from_rows(gamedata._iter_rows(TIMING_CSV))
    s = practice.summarize(combatlog.read_log(BETA), seg, timings)
    assert [(u.spell_name, u.casts) for u in s.spells] == [
        ("Corruption", 2),
        ("Bane of Agony", 2),
        ("Immolate", 1),
        ("Shadow Bolt", 1),
    ]
    assert s.damage.total == 180 and s.damage.others_on_targets == 0
    assert (s.gcd.on_gcd, s.gcd.no_gcd_data) == (6, 0)
    assert 0 < s.gcd.share < 1


def test_real_launch_slice_warriors_fight() -> None:
    lines = list(combatlog.read_log(LAUNCH))
    scan = practice.find_segments(lines, guids=["Player-9998-00000004"])
    (seg,) = scan.segments
    timings = practice.timings_from_rows(gamedata._iter_rows(TIMING_CSV))
    s = practice.summarize(lines, seg, timings)
    assert s.casts == 17 and s.damage.total == 681
    # Swings only, as logged: the hunter's pet's swings on the same targets.
    assert s.damage.others_on_targets == 335
    by_name = {c.spell_name: (c.casts, c.possible) for c in s.cooldowns}
    assert by_name["Bloodrage"] == (1, 1)
    assert by_name["Overpower"][0] == 1
    assert (s.gcd.on_gcd, s.gcd.off_gcd) == (14, 3)
    assert {b.spell_name for b in s.buffs} >= {"Battle Shout", "Blood Fury"}


def test_the_owner_has_no_segment_in_the_launch_slice() -> None:
    """The owner (flags 0x511) only gains buffs there."""
    assert practice.find_segments(combatlog.read_log(LAUNCH)).segments == ()


# ─── fix round 1: carried-in buffs, bounds ───────────────────────────────────


def _one_segment(text: str) -> practice.PracticeSummary:
    entries = _entries(text)
    (segment,) = practice.find_segments(entries).segments
    return practice.summarize(entries, segment, TIMINGS)


def _dummy_session(*extra: str) -> str:
    """Casts at 10, 15 and 20 s, then `extra` lines (constructed)."""
    return "".join(
        [
            _rec(0.0, _HEADER),
            _cast(10.0, 686, "Shadow Bolt"),
            _cast(15.0, 172, "Corruption"),
            _cast(20.0, 686, "Shadow Bolt"),
            *extra,
        ]
    )


def test_a_carried_in_buff_removed_after_the_segment_counts_whole_constructed() -> None:
    s = _one_segment(_dummy_session(_aura(50.0, "SPELL_AURA_REMOVED", 1459, "Arcane Intellect")))
    assert [(b.spell_name, b.seconds, b.share, b.on_before_log) for b in s.buffs] == [
        ("Arcane Intellect", 10.0, 1.0, True)
    ]


def test_a_carried_in_buff_refreshed_inside_counts_from_the_start_constructed() -> None:
    text = "".join(
        [
            _rec(0.0, _HEADER),
            _cast(10.0, 686, "Shadow Bolt"),
            _aura(12.0, "SPELL_AURA_REFRESH", 1459, "Arcane Intellect"),
            _cast(15.0, 172, "Corruption"),
            _cast(20.0, 686, "Shadow Bolt"),
        ]
    )
    s = _one_segment(text)
    assert [(b.spell_name, b.seconds, b.on_before_log) for b in s.buffs] == [
        ("Arcane Intellect", 10.0, True)
    ]


def test_a_buff_applied_after_the_segment_counts_nothing_constructed() -> None:
    s = _one_segment(_dummy_session(_aura(30.0, "SPELL_AURA_APPLIED", 1459, "Arcane Intellect")))
    assert s.buffs == ()


def test_a_new_header_after_the_segment_ends_the_buff_read_constructed() -> None:
    """After a new log header, a removal says nothing about the segment."""
    s = _one_segment(
        _dummy_session(
            _rec(40.0, _HEADER), _aura(50.0, "SPELL_AURA_REMOVED", 1459, "Arcane Intellect")
        )
    )
    assert s.buffs == ()


def test_only_the_latest_cast_start_is_pending_constructed() -> None:
    """A start never finished is dropped by the next one, so a cast time is
    read only from the start just before the success."""
    text = "".join(
        [
            _rec(0.0, _HEADER),
            _cast(10.0, 686, "Shadow Bolt", "SPELL_CAST_START"),
            _cast(11.0, 348, "Immolate", "SPELL_CAST_START"),
            _cast(12.0, 348, "Immolate"),
            _cast(13.0, 686, "Shadow Bolt"),
        ]
    )
    s = _one_segment(text)
    # Immolate (no row in this test's table): busy 11 to 12, its cast time.
    # Shadow Bolt has no pending start, so no cast time: 13 to 14.5, cut at
    # the segment's end (13). Had its dropped start at 10 been kept, 10 to 13.
    assert s.gcd.busy_seconds == 1.0


def test_the_players_of_a_crafted_log_are_bounded_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(practice, "MAX_COMPANIONS", 2)
    lines = [_rec(0.0, _HEADER)]
    for i in range(5):
        guid = f"Player-1-000000{i:02X}"
        lines.append(
            _rec(
                10.0 + i,
                f'SPELL_CAST_SUCCESS,{guid},"P{i}",0x511,0x80000000,{_unit(DUMMY)},'
                f'686,"Shadow Bolt",0x20,{_adv(guid)}',
            )
        )
    scan = practice.find_segments(_entries("".join(lines)))
    assert len(scan.segments) == 2
    assert scan.players_left_out == 3


def test_the_casts_kept_for_a_summary_are_bounded_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(practice, "MAX_SEGMENT_CASTS", 2)
    s = _one_segment(_dummy_session())
    assert (s.casts, s.casts_left_out) == (2, 1)


def test_the_notes_say_the_gap_and_the_fallback() -> None:
    with_table = cli._practice_notes(10.0, True)
    assert any("longer than 10 s" in n for n in with_table)
    assert any(n.startswith("Global cooldowns and cooldowns come from") for n in with_table)
    without = cli._practice_notes(25.0, False)
    assert any("longer than 25 s" in n for n in without)
    assert cli._PRACTICE_FALLBACK in without
    assert "auto-repeat shots included" in cli._PRACTICE_FALLBACK
    assert not any(n.startswith("Global cooldowns and cooldowns come from") for n in without)
    assert not any(n.startswith("Cooldown use counts at most") for n in without)


# ─── wowlab log practice ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    flavor = root / FLAVOR
    (flavor / "Logs").mkdir(parents=True)
    shutil.copy2(FIXTURES / "macos-71037" / ".build.info", root / ".build.info")
    shutil.copy2(FIXTURES / "macos-71037" / "forever" / ".flavor.info", flavor / ".flavor.info")
    shutil.copy2(BETA, flavor / "Logs" / BETA.name)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _cache_timings(user_data: Path) -> None:
    table = user_data / "gamedata" / "tables" / VERSION / "SpellCooldowns.csv"
    table.parent.mkdir(parents=True)
    shutil.copy2(TIMING_CSV, table)


def run(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    return result


def test_cli_lists_segments(root: Path) -> None:
    result = run("log", "practice")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "Segments in Logs/WoWCombatLog-040126_021630.txt:" in result.stdout
    # The first cast start (02:16:35) opens it; the last hit (02:17:01) ends it.
    assert "1. 02:16:35 to 02:17:01, 26 s, 6 casts, 180 damage, Vampiric Duskbat" in result.stdout
    assert "notes: --notes shows them" in result.stdout


def test_cli_json_summary_with_the_cached_table(root: Path, user_data: Path) -> None:
    _cache_timings(user_data)
    result = run("log", "practice", "--segment", "1", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    report = PracticeReport.model_validate_json(result.stdout)
    assert report.file == "Logs/WoWCombatLog-040126_021630.txt"
    assert report.logs == ["WoWCombatLog-040126_021630.txt"]
    assert (report.timings, report.timing_build, report.fetch) == ("table", VERSION, None)
    assert report.summary is not None and report.summary.damage.total == 180
    assert report.summary.timings == "table"


def test_cli_without_the_table_says_how_to_get_it(root: Path) -> None:
    result = run("log", "practice", "--segment", "1")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "Casts: 6" in result.stdout
    assert f"wowlab db2 fetch SpellCooldowns --build {VERSION}" in " ".join(result.stdout.split())
    data = json.loads(run("log", "practice", "--json").stdout)
    assert data["timings"] == "missing"
    assert data["fetch"] == ["db2", "fetch", "SpellCooldowns", "--build", VERSION]
    assert data["summary"] is None


def test_cli_refuses_an_unknown_log_and_segment(root: Path) -> None:
    result = run("log", "practice", "--file", "../Config.wtf")
    assert result.exit_code == 2
    assert "no combat log" in result.stderr
    result = run("log", "practice", "--segment", "2")
    assert result.exit_code == 2
    assert "no segment 2" in result.stderr


def test_cli_with_no_log(root: Path) -> None:
    (root / FLAVOR / "Logs" / BETA.name).unlink()
    data = json.loads(run("log", "practice", "--json").stdout)
    assert data["file"] is None and data["segments"] == []


def test_cli_reads_only(root: Path) -> None:
    """L1: the install's bytes and times are what they were."""
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert run("log", "practice", "--segment", "1").exit_code == 0
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert after == before
