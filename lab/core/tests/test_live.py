"""`wowlab_core.live` (M22-05): the Live view's watcher and its two recaps.

Real input: the repository's one real combat log
(`fixtures/macos/forever/Logs/WoWCombatLog-040126_021630.txt`, one solo
open-world fight against an NPC) and the real `Syndicator.lua` of the same
capture; the watcher and the recap are checked against both in place, read
only. Every other input is CONSTRUCTED, labelled `constructed` in the test
name: combat logs from `parser/_world_lines.py` (the real log's lines with
invented players swapped in), a battleground's and an arena's lines (their
instance ids, difficulties and the arena lines' fields are invented: no
fixture shows one, not yet confirmed), SavedVariables trees written in
`tmp_path`, and one item count changed in the real Syndicator bytes. Nothing
here reads a real install or writes outside `tmp_path`.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

from wowlab_core import crewbook, live, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _world_lines import (
    FOE_A,
    FOE_B,
    FOE_D,
    HEADER,
    OWNER_GUID,
    hits_you,
    log,
    stamp,
    you_die,
    you_hit,
    you_kill,
    zone,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL_FLAVOR = FIXTURES / "macos" / "forever"
REAL_LOG = REAL_FLAVOR / "Logs" / "WoWCombatLog-040126_021630.txt"
REAL_SYNDICATOR = (
    REAL_FLAVOR / "WTF" / "Account" / "90000001#6" / "SavedVariables" / "Syndicator.lua"
)
OWNERS: dict[str, str | None] = {OWNER_GUID: None}
BG = 489  # an invented battleground instance id
BG_NAME = "Invented Test Valley"
BASE = worldpvp.seconds_of(stamp(0))
assert BASE is not None


def _tree_state(root: Path) -> dict[str, tuple[str, int]]:
    return {
        str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _flavor(tmp_path: Path) -> Path:
    """A constructed flavor folder: one account's file and one character's."""
    flavor = tmp_path / "_flavor_"
    acct = flavor / "WTF" / "Account" / "ACCOUNTA"
    (acct / "SavedVariables").mkdir(parents=True)
    (acct / "1" / "First-Second" / "SavedVariables").mkdir(parents=True)
    (acct / "SavedVariables" / "A.lua").write_bytes(b"\r\nA = 1\r\n")
    (acct / "SavedVariables" / "A.lua.bak").write_bytes(b"\r\nA = 0\r\n")
    (acct / "1" / "First-Second" / "SavedVariables" / "B.lua").write_bytes(b"\r\nB = 1\r\n")
    os.utime(acct / "SavedVariables" / "A.lua", ns=(10**18, 10**18))
    os.utime(acct / "SavedVariables" / "A.lua.bak", ns=(3 * 10**18, 3 * 10**18))
    os.utime(acct / "1" / "First-Second" / "SavedVariables" / "B.lua", ns=(10**18, 10**18))
    (flavor / "Logs").mkdir()
    return flavor


def _write(flavor: Path, data: bytes, name: str = "WoWCombatLog-040126_021630.txt") -> Path:
    path = flavor / "Logs" / name
    path.write_bytes(data)
    return path


# ─── the watcher ─────────────────────────────────────────────────────────────


def test_the_watcher_reads_the_real_log_and_writes_nothing() -> None:
    before = _tree_state(REAL_FLAVOR)
    watcher = live.Watcher()
    first = watcher.poll(REAL_FLAVOR, "forever")
    assert first.log is not None
    assert first.log.name == REAL_LOG.name
    assert first.log.size == REAL_LOG.stat().st_size
    assert (first.log.zone, first.log.instance_id, first.log.difficulty) == (
        "Tirisfal Glades",
        0,
        0,
    )
    assert first.log.in_world is True and first.log.zone_changes == 1
    assert first.log_bytes_read == REAL_LOG.stat().st_size
    assert first.saved.files == 9 and first.saved.newest is not None
    assert first.saved.newest.endswith(".lua")
    again = watcher.poll(REAL_FLAVOR, "forever")
    assert again.log_bytes_read == 0, "nothing new: nothing read"
    assert again.log == first.log
    assert _tree_state(REAL_FLAVOR) == before


def test_a_growing_log_is_read_only_where_it_grew_constructed(tmp_path: Path) -> None:
    flavor = _flavor(tmp_path)
    path = _write(flavor, log(you_hit(FOE_A, 10)))
    watcher = live.Watcher()
    first = watcher.poll(flavor, "_flavor_")
    assert first.log is not None and first.log.zone_changes == 1 and first.log.in_world
    added = (zone(100, instance=BG, name=BG_NAME, difficulty=34) + "\r\n").encode()
    with path.open("ab") as f:
        f.write(added)
    grown = watcher.poll(flavor, "_flavor_")
    assert grown.log_bytes_read == len(added)
    assert grown.log is not None
    assert (grown.log.zone, grown.log.instance_id, grown.log.difficulty) == (BG_NAME, BG, 34)
    assert grown.log.zone_changes == 2 and grown.log.in_world is False
    # A line still being written waits for the next poll.
    partial = zone(200).encode()
    with path.open("ab") as f:
        f.write(partial)
    waiting = watcher.poll(flavor, "_flavor_")
    assert waiting.log_bytes_read == 0 and waiting.log is not None
    assert waiting.log.zone_changes == 2
    with path.open("ab") as f:
        f.write(b"\r\n")
    done = watcher.poll(flavor, "_flavor_")
    assert done.log_bytes_read == len(partial) + 2
    assert done.log is not None and done.log.zone == "Tirisfal Glades"
    assert done.log.zone_changes == 3


def test_arena_lines_are_counted_constructed(tmp_path: Path) -> None:
    flavor = _flavor(tmp_path)
    path = _write(flavor, log())
    watcher = live.Watcher()
    watcher.poll(flavor, "_flavor_")
    with path.open("ab") as f:
        f.write(f"{stamp(5)}  ARENA_MATCH_START,572,33,3v3,1\r\n".encode())
    started = watcher.poll(flavor, "_flavor_")
    assert started.log is not None and started.log.arena and started.log.match_ends == 0
    with path.open("ab") as f:
        f.write(f"{stamp(9)}  ARENA_MATCH_END,1,300,1500,1500\r\n".encode())
    ended = watcher.poll(flavor, "_flavor_")
    assert ended.log is not None and not ended.log.arena and ended.log.match_ends == 1


def test_a_newer_log_or_a_rewritten_one_starts_over_constructed(tmp_path: Path) -> None:
    flavor = _flavor(tmp_path)
    path = _write(flavor, log(zone(50, instance=BG, name=BG_NAME)))
    watcher = live.Watcher()
    first = watcher.poll(flavor, "_flavor_")
    assert first.log is not None and first.log.zone_changes == 2
    later = HEADER.replace("4/1/2026", "4/2/2026")
    newer = _write(flavor, (later + "\r\n" + zone(0) + "\r\n").encode(), "WoWCombatLog-040226.txt")
    os.utime(newer, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
    second = watcher.poll(flavor, "_flavor_")
    assert second.log is not None and second.log.name == newer.name
    assert second.log.zone_changes == 1 and second.log.zone == "Tirisfal Glades"
    # The same name, another first line: a new log, read from its start.
    newer.write_bytes(log(zone(9, instance=BG, name=BG_NAME)))
    os.utime(newer, ns=(path.stat().st_mtime_ns + 2 * 10**9,) * 2)
    third = watcher.poll(flavor, "_flavor_")
    assert third.log is not None and third.log.zone == BG_NAME and third.log.zone_changes == 2


def test_a_log_grown_past_the_tail_is_read_from_the_tail_only_constructed(tmp_path: Path) -> None:
    flavor = _flavor(tmp_path)
    path = _write(flavor, log())
    watcher = live.Watcher(tail_bytes=4096)
    watcher.poll(flavor, "_flavor_")
    lines = [you_hit(FOE_A, 10 + i) for i in range(200)] + [zone(900, instance=BG, name=BG_NAME)]
    with path.open("ab") as f:
        f.write(("\r\n".join(lines) + "\r\n").encode())
    grown = watcher.poll(flavor, "_flavor_")
    assert 0 < grown.log_bytes_read <= 4096
    assert grown.log is not None and grown.log.zone == BG_NAME


def test_saved_variables_times_change_at_a_save_constructed(tmp_path: Path) -> None:
    flavor = _flavor(tmp_path)
    watcher = live.Watcher()
    first = watcher.poll(flavor, "_flavor_")
    assert first.saved.files == 2, "the .bak copy is not counted"
    assert first.log is None
    char = (
        flavor / "WTF" / "Account" / "ACCOUNTA" / "1" / "First-Second" / "SavedVariables" / "B.lua"
    )
    os.utime(char, ns=(2 * 10**18, 2 * 10**18))
    second = watcher.poll(flavor, "_flavor_")
    assert second.saved.newest_ns == 2 * 10**18
    assert second.saved.newest == "WTF/Account/ACCOUNTA/1/First-Second/SavedVariables/B.lua"
    bounded = live.saved_watch(flavor, max_files=1)
    assert bounded.files == 1 and bounded.left_out == 1


def test_a_linked_folder_is_not_followed_constructed(tmp_path: Path) -> None:
    flavor = _flavor(tmp_path)
    outside = tmp_path / "outside" / "SavedVariables"
    outside.mkdir(parents=True)
    (outside / "C.lua").write_bytes(b"\r\nC = 1\r\n")
    os.utime(outside / "C.lua", ns=(9 * 10**18, 9 * 10**18))
    link = flavor / "WTF" / "Account" / "ACCOUNTA" / "2"
    try:
        link.symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("this system cannot make a link")
    assert live.saved_watch(flavor).newest_ns == 10**18


def test_a_flavor_with_no_wtf_or_logs_answers_empty_constructed(tmp_path: Path) -> None:
    flavor = tmp_path / "_empty_"
    flavor.mkdir()
    watch = live.Watcher().poll(flavor, "_empty_")
    assert watch.saved == live.SavedWatch() and watch.log is None


# ─── the match recap ─────────────────────────────────────────────────────────


def _battleground(*, leave: bool = True, difficulty: int = 0) -> bytes:
    """Open world: you fight A; then a battleground: you hit and kill D, D
    kills you; then (with `leave`) back to the open world."""
    lines = [
        you_hit(FOE_A, 10),
        zone(100, instance=BG, name=BG_NAME, difficulty=difficulty),
        you_hit(FOE_D, 105),
        you_kill(FOE_D, 106),
        hits_you(FOE_B, 110),
        hits_you(FOE_D, 111, overkill=0),
        you_die(112),
    ]
    if leave:
        lines.append(zone(200))
    return log(*lines)


def _book_match(at: int, won: bool | None = True) -> crewbook.CrewMatch:
    return crewbook.CrewMatch(
        position=1,
        at=at,
        map=BG_NAME,
        map_id=BG,
        kind="bg",
        won=won,
        team=[],
        foes=["Inventedd-Otherfakerealm"],
    )


def test_a_battleground_left_is_recapped_with_the_books_result_constructed(tmp_path: Path) -> None:
    path = _write(_flavor(tmp_path), _battleground())
    recap = live.match_recap([path], OWNERS, pvp_maps=[BG], book=[_book_match(BASE + 205)])
    assert recap is not None
    assert (recap.zone, recap.instance_id, recap.arena) == (BG_NAME, BG, False)
    assert recap.over and recap.ended_by == "left"
    assert recap.start == BASE + 100 and recap.end == BASE + 112
    assert [o.key for o in recap.opponents] == ["Inventedd-Otherfakerealm", "Inventedb-Fakerealm"]
    d = recap.opponents[0]
    assert d.killed_by_you and d.killed_you and d.unit_name == FOE_D[1]
    assert recap.killing_blows == 1 and recap.deaths == 1
    assert (recap.result, recap.result_from) == ("won", "crew_book")
    assert recap.book_foes == ["Inventedd-Otherfakerealm"] and recap.book_map == BG_NAME
    assert "Inventeda-Fakerealm" not in {o.key for o in recap.opponents}, "the open world is not"


def test_a_battleground_under_way_has_no_result_yet_constructed(tmp_path: Path) -> None:
    path = _write(_flavor(tmp_path), _battleground(leave=False))
    recap = live.match_recap([path], OWNERS, pvp_maps=[BG], book=[_book_match(BASE + 205)])
    assert recap is not None
    assert not recap.over and recap.ended_by is None
    assert recap.result is None and recap.book_foes == []


def test_a_book_match_far_from_the_stay_gives_no_result_constructed(tmp_path: Path) -> None:
    path = _write(_flavor(tmp_path), _battleground())
    recap = live.match_recap([path], OWNERS, pvp_maps=[BG], book=[_book_match(BASE + 5000)])
    assert recap is not None and recap.over and recap.result is None


def test_a_battleground_with_a_difficulty_is_a_match_without_a_kept_map_constructed(
    tmp_path: Path,
) -> None:
    path = _write(_flavor(tmp_path), _battleground(difficulty=34))
    recap = live.match_recap([path], OWNERS)
    assert recap is not None and recap.instance_id == BG and recap.result is None
    # With difficulty 0 and no map a match was kept on, the place reads as
    # the open world, by the one rule `worldpvp` keeps.
    plain = _write(_flavor(tmp_path / "b"), _battleground(difficulty=0))
    assert live.match_recap([plain], OWNERS) is None


def test_a_newer_log_ends_the_match_constructed(tmp_path: Path) -> None:
    flavor = _flavor(tmp_path)
    old = _write(flavor, _battleground(leave=False, difficulty=34))
    later = HEADER.replace("4/1/2026", "4/2/2026")
    new = _write(flavor, (later + "\r\n" + zone(0) + "\r\n").encode(), "WoWCombatLog-040226.txt")
    recap = live.match_recap([old, new], OWNERS)
    assert recap is not None and recap.over and recap.ended_by == "new_log"


def test_an_arena_result_is_read_from_its_lines_constructed(tmp_path: Path) -> None:
    for winner, word in ((1, "won"), (0, "lost")):
        flavor = _flavor(tmp_path / word)
        data = log(
            zone(100, instance=572, name="Invented Ring", difficulty=0),
            f"{stamp(101)}  ARENA_MATCH_START,572,33,3v3,1",
            you_hit(FOE_D, 105),
            f"{stamp(130)}  ARENA_MATCH_END,{winner},30,1500,1500",
        )
        recap = live.match_recap([_write(flavor, data)], OWNERS)
        assert recap is not None and recap.arena
        assert recap.over and recap.ended_by == "match_end"
        assert (recap.result, recap.result_from) == (word, "log")


def test_a_dungeon_with_no_player_is_no_match_constructed(tmp_path: Path) -> None:
    lines = log(you_hit(FOE_A, 10), zone(100, instance=33, name="Invented Keep", difficulty=1))
    assert live.match_recap([_write(_flavor(tmp_path), lines)], OWNERS) is None


def test_the_real_log_shows_no_match_and_no_open_world_player() -> None:
    assert live.match_recap([REAL_LOG], {"Player-9999-00000001": None}) is None
    found, note = live.world_since([REAL_LOG], {"Player-9999-00000001": None}, 0)
    assert found == [] and note is None


def test_world_since_reads_only_logs_written_since_and_within_budget_constructed(
    tmp_path: Path,
) -> None:
    flavor = _flavor(tmp_path)
    old = _write(flavor, log(you_hit(FOE_A, 10)), "WoWCombatLog-old.txt")
    new = _write(flavor, log(you_hit(FOE_B, 10)), "WoWCombatLog-new.txt")
    os.utime(old, ns=(10**18, 10**18))
    os.utime(new, ns=(3 * 10**18, 3 * 10**18))
    found, note = live.world_since([old, new], OWNERS, 2 * 10**18)
    assert [p.key for e in found for p in e.players] == ["Inventedb-Fakerealm"] and note is None
    found, note = live.world_since([old, new], OWNERS, 0, max_bytes=new.stat().st_size)
    assert [p.key for e in found for p in e.players] == ["Inventedb-Fakerealm"]
    assert note == "1 larger combat log not read"


# ─── Syndicator ──────────────────────────────────────────────────────────────


def test_syndicator_items_of_the_real_capture() -> None:
    items = live.syndicator_items(REAL_SYNDICATOR.read_bytes(), ["Labchard Labrealme"])
    assert isinstance(items, list)
    by_id = {i.item_id: i for i in items}
    assert by_id[6948].name == "Hearthstone" and by_id[6948].count == 1
    assert by_id[12223].name == "Meaty Bat Wing" and by_id[12223].count >= 2
    assert by_id[277114].count >= 3, "each bag slot is a stack"
    assert live.syndicator_items(REAL_SYNDICATOR.read_bytes(), ["Nobody Here"]) == (
        "Syndicator lists no character of this name"
    )
    assert live.syndicator_items(b"\r\nX = {\r\n", ["Labchard Labrealme"]).startswith(
        "Syndicator's file could not be read"
    )


def test_new_items_are_the_counts_that_grew_constructed() -> None:
    real = REAL_SYNDICATOR.read_bytes()
    before = live.syndicator_items(real, ["labchard labrealme"])
    marker = b'["itemCount"] = 2,\r\n["itemID"] = 12223,'
    assert real.count(marker) == 1
    after = live.syndicator_items(
        real.replace(marker, b'["itemCount"] = 5,\r\n["itemID"] = 12223,'), ["Labchard Labrealme"]
    )
    assert isinstance(before, list) and isinstance(after, list)
    gained = live.new_items(before, after)
    assert [(n.item_id, n.name, n.after - n.before) for n in gained] == [
        (12223, "Meaty Bat Wing", 3)
    ]
    assert live.new_items(after, before) == []


# ─── the cost of a poll ──────────────────────────────────────────────────────


def test_the_cost_of_a_poll_constructed(tmp_path: Path) -> None:
    """A flavor with 400 SavedVariables files in 40 character folders and a
    4 MiB log growing by one fight a poll. Measured (Apple silicon,
    2026-11-23): see the report of M22-05; the bound here only catches a
    poll that reads far more than it should."""
    flavor = _flavor(tmp_path)
    acct = flavor / "WTF" / "Account" / "ACCOUNTA"
    for c in range(40):
        sv = acct / "1" / f"Char{c}-Second" / "SavedVariables"
        sv.mkdir(parents=True)
        for n in range(10):
            (sv / f"Addon{n}.lua").write_bytes(b"\r\nX = 1\r\n")
    body = "\r\n".join(you_hit(FOE_A, 10 + i % 3000) for i in range(14000)) + "\r\n"
    path = _write(flavor, log() + body.encode())
    assert path.stat().st_size > 4 * 1024 * 1024
    watcher = live.Watcher()
    began = time.perf_counter()
    first = watcher.poll(flavor, "_flavor_")
    first_s = time.perf_counter() - began
    assert first.log_bytes_read <= live.WATCH_TAIL_BYTES
    costs = []
    for i in range(20):
        with path.open("ab") as f:
            f.write((you_hit(FOE_B, 4000 + i) + "\r\n").encode())
        began = time.perf_counter()
        polled = watcher.poll(flavor, "_flavor_")
        costs.append(time.perf_counter() - began)
        assert polled.log_bytes_read < 1024
    steady = sorted(costs)[len(costs) // 2]
    print(f"live poll: first {first_s * 1000:.1f} ms, steady median {steady * 1000:.2f} ms")
    assert first.saved.files == 402
    assert steady < 0.5 and first_s < 5.0


def test_the_open_world_rule_lives_in_one_place() -> None:
    world = worldpvp.ZoneState(zone="Tirisfal Glades", instance_id=0, difficulty=0)
    assert worldpvp.in_open_world(world)
    assert not worldpvp.in_open_world(world.model_copy(update={"arena": True}))
    assert not worldpvp.in_open_world(world.model_copy(update={"difficulty": 34}))
    bg = worldpvp.ZoneState(zone=BG_NAME, instance_id=BG, difficulty=0)
    assert worldpvp.in_open_world(bg) and not worldpvp.in_open_world(bg, [BG])


def test_place_counts_fights_where_the_caller_says_constructed() -> None:
    from wowlab_core import combatlog

    data = log(zone(100, instance=BG, name=BG_NAME, difficulty=34), you_hit(FOE_D, 105))
    entries = list(combatlog.tokenize(data))
    plain = worldpvp.scan_entries(entries, OWNERS)
    assert (plain.fights, plain.outside_world) == (0, 1)
    inside = worldpvp.scan_entries(entries, OWNERS, place=lambda s: s.instance_id == BG)
    assert (inside.fights, inside.outside_world) == (1, 0)
