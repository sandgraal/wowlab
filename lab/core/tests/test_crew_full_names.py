"""Full names and region for every player met (M21-12), at the desk.

The lab-addon's crew book at schema 3 holds the region the game gave
(`region`, from `GetCurrentRegionName()`) and, per player, the name the game
gave for their GUID (`resolved_name`, `<First> <Second>` on Forever, with
`resolved_realm`, `resolved_eighth` and `resolved_at`). `crewbook` reads
them, `crewdb` keeps them and fills each row's first and second name and
region with where each came from (`names_from`, `region_from`), and a player
only a combat log named borrows them from the row with the same GUID that
the game's list gave (`borrowed_from`).

CONSTRUCTED INPUT, every byte, as in `test_crew_log_names.py`: crew books
from `parser/_crew_build.py`, combat logs from `parser/_world_lines.py`,
inside a copy of the captured install `fixtures/macos-70888/`, whose account
holds a `1/<First>-<Second>` character folder (a second-name flavor) and
whose Config.wtf names the region US. The values the game gives are the
owner's check of 2026-11-23 (Forever launch client): `GetPlayerInfoByGUID`
gave "Tay Soulrend", "" and 22, and `GetCurrentRegionName()` gave "US".
"""

# ruff: noqa: F811 - `make` and `owner` are imported fixtures, requested by name

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import crewbook, crewdb

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, KEY_A, KEY_B, T1, book, match, player
from _world_lines import log, you_hit
from test_crew_world import _scan, _write_log, owner  # noqa: F401 - the fixture
from test_crewdb import Env, before_schema_7, make  # noqa: F401 - the fixture

TAY = "Tay-Soulrend"
TAY_GUID = "Player-9998-0000000E"
ASKED = T1 + 100  # when the lab-addon asked the game


def _tay(**more: Any) -> dict[str, Any]:
    """Tay as the schema 3 book keeps them once the game named them."""
    fields: dict[str, Any] = {
        "guid": TAY_GUID,
        "resolved_name": "Tay Soulrend",
        "resolved_realm": "",
        "resolved_eighth": 22,
        "resolved_at": ASKED,
    }
    fields.update(more)
    return player(**fields)


REGION_AT = T1 + 50  # when the lab-addon recorded the region


def _book3(players: dict[str, Any], **more: Any) -> bytes:
    """A schema 3 book: schema 2's fields and, unless given, region "US",
    the captured settings' own (`region_from` tells the two apart),
    recorded at REGION_AT."""
    more.setdefault("region", "US")
    if more["region"] is not None:
        more.setdefault("region_at", REGION_AT)
    more.setdefault("matches", [])
    return book(players, schema=3, **more)


def _day(seconds: int) -> str:
    """A time as `crew show` prints it (the computer's local date)."""
    return time.strftime("%Y-%m-%d", time.localtime(seconds))


def _rows(env: Env) -> dict[str, dict[str, Any]]:
    result = env.run("crew", "list", "--json")
    assert result.exit_code == 0, result.output
    return {p["key"]: p for p in json.loads(result.stdout)["players"]}


# ─── the reader ──────────────────────────────────────────────────────────────


def test_the_reader_reads_schema_3_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay(), KEY_A: player(guid="Player-9998-0000000A")}))
    read = env.book()
    assert read.schema_number == 3 and (read.region, read.region_at) == ("US", REGION_AT)
    players = {p.key: p for p in read.players}
    tay = players[TAY]
    assert (tay.resolved_name, tay.resolved_realm) == ("Tay Soulrend", "")
    assert (tay.resolved_eighth, tay.resolved_at) == (22, ASKED)
    assert tay.other_keys == []
    a = players[KEY_A]
    assert a.resolved_name is None and a.resolved_at is None
    assert read.unread == []


def test_a_schema_2_book_keeps_the_new_fields_as_unknown_keys_constructed(make: Any) -> None:
    env = make(book({TAY: _tay()}, schema=2, matches=[], region="US"))
    read = env.book()
    assert read.region is None
    tay = read.players[0]
    assert tay.resolved_name is None
    assert set(tay.other_keys) == {
        "resolved_name",
        "resolved_realm",
        "resolved_eighth",
        "resolved_at",
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("resolved_name", 5, "resolved_name is not text"),
        ("resolved_realm", True, "resolved_realm is not text"),
        ("resolved_eighth", "22", "resolved_eighth is not a whole number"),
        ("resolved_eighth", 1.5, "resolved_eighth is not a whole number"),
        ("resolved_at", -1, "resolved_at is not a time"),
    ],
    ids=[
        "constructed-name",
        "constructed-realm",
        "constructed-text",
        "constructed-half",
        "constructed-time",
    ],
)
def test_a_bad_field_makes_only_that_entry_unread_constructed(
    make: Any, field: str, value: Any, reason: str
) -> None:
    env = make(_book3({TAY: _tay(**{field: value}), KEY_A: player()}))
    read = env.book()
    assert [u.key for u in read.unread] == [TAY]
    assert read.unread[0].reason == reason
    assert [p.key for p in read.players] == [KEY_A]


def test_a_region_that_is_not_text_refuses_the_book_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay()}, region=1))
    with pytest.raises(crewbook.CrewBookError) as caught:
        env.book()
    assert "region" in str(caught.value)


# ─── the database and the rows ───────────────────────────────────────────────


def test_a_named_player_has_the_games_names_and_the_books_region_constructed(
    make: Any,
) -> None:
    env = make(_book3({TAY: _tay(), KEY_A: player(guid="Player-9998-0000000A")}))
    rows = _rows(env)
    tay = rows[TAY]
    assert (tay["first_name"], tay["second_name"], tay["realm"]) == ("Tay", "Soulrend", None)
    assert tay["game_name"] == "Tay Soulrend" and tay["names_from"] == "game"
    assert (tay["region"], tay["region_from"]) == ("us", "book")
    assert tay["region_recorded_at"] == REGION_AT and tay["region_conflict"] == []
    assert (tay["resolved_name"], tay["resolved_eighth"], tay["resolved_at"]) == (
        "Tay Soulrend",
        22,
        ASKED,
    )
    assert tay["borrowed_from"] is None
    # Not named by the game yet: the key's two parts, the book's region.
    a = rows[KEY_A]
    assert (a["first_name"], a["second_name"], a["names_from"]) == (
        "Inventeda",
        "Fakerealm",
        "key",
    )
    assert (a["region"], a["region_from"]) == ("us", "book")


def test_a_book_with_no_region_leaves_the_settings_region_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay()}, region=None))
    tay = _rows(env)[TAY]
    assert (tay["region"], tay["region_from"]) == ("us", "settings")


def test_the_book_region_is_kept_when_a_later_book_has_none_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay()}))
    assert _rows(env)[TAY]["region_from"] == "book"
    env.save(_book3({TAY: _tay(met=4)}, region=None))
    tay = _rows(env)[TAY]
    assert (tay["region"], tay["region_from"], tay["region_recorded_at"]) == (
        "us",
        "book",
        REGION_AT,
    )


def test_an_unknown_region_is_not_kept_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay()}, region="Invented"))
    tay = _rows(env)[TAY]
    assert (tay["region"], tay["region_from"]) == ("us", "settings")


def test_an_older_answer_never_replaces_a_newer_one_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay()}))
    env.sync()
    env.save(_book3({TAY: _tay(resolved_name="Tay Older", resolved_at=ASKED - 50)}))
    env.sync()
    assert env.rows()[TAY].resolved_name == "Tay Soulrend"
    env.save(_book3({TAY: _tay(resolved_name="Tay Renamed", resolved_at=ASKED + 50)}))
    env.sync()
    row = env.rows()[TAY]
    assert (row.resolved_name, row.first_name, row.second_name) == (
        "Tay Renamed",
        "Tay",
        "Renamed",
    )


def test_the_game_name_stays_when_the_game_list_drops_the_player_constructed(
    make: Any,
) -> None:
    env = make(_book3({TAY: _tay(), KEY_A: player()}))
    env.sync()
    env.save(_book3({KEY_A: player()}))
    env.sync()
    row = env.rows()[TAY]
    assert not row.in_game_list
    assert (row.resolved_name, row.names_from, row.region_from) == ("Tay Soulrend", "game", "book")


# ─── borrowing through the GUID ──────────────────────────────────────────────

# The combat log names Tay in the beta's three parts, so their key is
# `Tay-Otherfakerealm`, not the book's `Tay-Soulrend`: only the GUID joins them.
LOG_TAY = (TAY_GUID, "Tay-Otherfakerealm-")
LOG_KEY = "Tay-Otherfakerealm"


def test_a_log_only_player_borrows_from_the_book_row_with_the_same_guid_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book3({TAY: _tay()}))
    _write_log(env, log(you_hit(LOG_TAY, 10)))
    assert _scan(env).exit_code == 0
    rows = _rows(env)
    met = rows[LOG_KEY]
    assert met["in_game_list"] is False
    assert met["also_known_as"] == [TAY]
    assert (met["first_name"], met["second_name"]) == ("Tay", "Soulrend")
    assert met["game_name"] == "Tay Soulrend"
    assert met["realm"] == "Otherfakerealm"  # its own key's, as the log gave it
    assert (met["names_from"], met["borrowed_from"]) == ("borrowed", TAY)
    assert (met["region"], met["region_from"]) == ("us", "borrowed")
    # The book row is as it was.
    assert (rows[TAY]["names_from"], rows[TAY]["borrowed_from"]) == ("game", None)


def test_a_book_row_named_only_by_its_key_still_lends_its_names_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book3({TAY: player(guid=TAY_GUID)}))
    _write_log(env, log(you_hit(LOG_TAY, 10)))
    assert _scan(env).exit_code == 0
    met = _rows(env)[LOG_KEY]
    assert (met["first_name"], met["second_name"], met["names_from"]) == (
        "Tay",
        "Soulrend",
        "borrowed",
    )


def test_a_log_only_player_with_no_book_row_keeps_its_own_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book3({TAY: _tay(guid="Player-9998-0000000F")}))
    _write_log(env, log(you_hit(LOG_TAY, 10)))
    assert _scan(env).exit_code == 0
    met = _rows(env)[LOG_KEY]
    assert met["also_known_as"] == []
    assert (met["first_name"], met["second_name"], met["realm"]) == (
        "Tay",
        None,
        "Otherfakerealm",
    )
    assert (met["names_from"], met["borrowed_from"]) == ("key", None)
    assert (met["region"], met["region_from"]) == ("us", "settings")


def test_a_four_part_log_name_keeps_its_own_names_and_region_constructed(
    make: Any, owner: Any
) -> None:
    """The launch client's `<First>-<Second>-<Realm>-<Region>`: the row's
    own names come from the log, and the log's region wins over the book's."""
    env = make(_book3({KEY_B: player(guid=TAY_GUID)}))
    _write_log(env, log(you_hit((TAY_GUID, "Tay-Soulrend-Roleplay PvP-US"), 10)))
    assert _scan(env).exit_code == 0
    tay = _rows(env)[TAY]
    assert (tay["names_from"], tay["region"], tay["region_from"]) == ("log", "us", "log")
    assert tay["borrowed_from"] is None


def test_the_lender_named_by_the_game_is_chosen_first_constructed(make: Any, owner: Any) -> None:
    """Two book rows with the GUID (a rename): the one the game named lends."""
    env = make(
        _book3(
            {
                KEY_A: player(guid=TAY_GUID, last=T1 + 500),
                TAY: _tay(last=T1),
            },
            matches=[match([KEY_A], [])],
        )
    )
    _write_log(env, log(you_hit(LOG_TAY, 10)))
    assert _scan(env).exit_code == 0
    met = _rows(env)[LOG_KEY]
    assert met["borrowed_from"] == TAY and met["names_from"] == "borrowed"


# ─── crew show and the database's schema ─────────────────────────────────────


def test_crew_show_says_where_the_names_and_region_came_from_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book3({TAY: _tay(), KEY_A: player()}))
    _write_log(env, log(you_hit(LOG_TAY, 10)))
    assert _scan(env).exit_code == 0
    shown = json.loads(env.run("crew", "show", TAY, "--json").stdout)
    lines = shown["lines"]
    assert (
        "  Name in game: Tay Soulrend (first name Tay, second name Soulrend; "
        f"as the game gave it for the character's id on {_day(ASKED)})"
    ) in lines
    assert (
        f"  Region: US (your region as the game gave it to the crew book on {_day(REGION_AT)}; "
        "wowlab assumes every player is in your region, not yet confirmed)"
    ) in lines
    assert shown["player"]["names_from"] == "game"
    met = json.loads(env.run("crew", "show", LOG_KEY, "--json").stdout)["lines"]
    assert (
        "  Name in game: Tay Soulrend (first name Tay, second name Soulrend, realm "
        f"Otherfakerealm; from {TAY}, the same character by the game's id)"
    ) in met
    assert (
        f"  Region: US (from {TAY}, the same character by the game's id: your region as the "
        f"game gave it to the crew book on {_day(REGION_AT)}; wowlab assumes every player is "
        "in your region, not yet confirmed)"
    ) in met
    text = env.run("crew", "show", KEY_A)
    assert text.exit_code == 0
    assert (
        "  Name in game: Inventeda Fakerealm (first name Inventeda, second name Fakerealm; "
        "from the name the crew book keeps)"
    ) in text.stdout.splitlines()


def test_a_schema_6_database_gains_the_columns_after_a_copy_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay()}))
    env.sync()
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        before_schema_7(conn)
        conn.execute("DELETE FROM meta WHERE name = 'book_region'")
        conn.execute("PRAGMA user_version = 6")
    finally:
        conn.close()
    env.save(_book3({TAY: _tay(met=5)}))
    rows = env.rows()  # opened and migrated
    copy = env.db().with_name(env.db().name + ".before-schema-6")
    assert copy.exists()
    conn = sqlite3.connect(env.db())
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == crewdb.SCHEMA == 7
        columns = {r[1] for r in conn.execute("PRAGMA table_info(players)")}
    finally:
        conn.close()
    assert {"resolved_name", "resolved_realm", "resolved_eighth", "resolved_at"} <= columns
    assert rows[TAY].resolved_name is None  # nothing until the next sync
    env.sync()
    assert env.rows()[TAY].resolved_name == "Tay Soulrend"


def test_a_name_longer_than_the_bound_is_left_out_of_the_sync_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay(resolved_name="x" * 300), KEY_A: player()}))
    outcome = env.sync()
    assert outcome.state == "synced" and outcome.left_out == 1
    assert TAY not in env.rows()


def test_a_forget_keeps_the_schema_3_book_and_its_region_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay(), KEY_A: player()}))
    result = env.run("crew", "forget", TAY, "--yes")
    assert result.exit_code == 0, result.output
    read = env.book()
    assert read.schema_number == 3 and read.region == "US"
    assert [p.key for p in read.players] == [KEY_A]
    assert b"Tay Soulrend" not in env.data()


# ─── fix round 1 ─────────────────────────────────────────────────────────────


def test_a_book_and_settings_naming_different_regions_say_both_constructed(
    make: Any, owner: Any
) -> None:
    """The book recorded EU, the settings name US: no region for a player
    whose region would come from either, and both said; a region a combat
    log's four-part name gave is kept."""
    env = make(_book3({TAY: _tay(), KEY_A: player()}, region="EU"))
    _write_log(env, log(you_hit(("Player-9998-0000000F", "Rizz-Other-Roleplay PvP-US"), 10)))
    assert _scan(env).exit_code == 0
    rows = _rows(env)
    tay = rows[TAY]
    assert (tay["region"], tay["region_from"], tay["region_conflict"]) == (None, None, ["eu", "us"])
    assert (rows["Rizz-Other"]["region"], rows["Rizz-Other"]["region_from"]) == ("us", "log")
    lines = json.loads(env.run("crew", "show", TAY, "--json").stdout)["lines"]
    assert (
        f"  Region: not known: the crew book recorded EU as your region on {_day(REGION_AT)}, "
        "but your game's settings name US"
    ) in lines


def test_a_book_region_with_no_time_is_dated_by_its_first_sync_constructed(make: Any) -> None:
    env = make(_book3({TAY: _tay()}, region_at=None))
    before = int(time.time())
    first = _rows(env)[TAY]["region_recorded_at"]
    assert first is not None and before - 5 <= first <= int(time.time()) + 5
    env.save(_book3({TAY: _tay(met=7)}, region_at=None))
    assert _rows(env)[TAY]["region_recorded_at"] == first


def test_resolve_tried_at_is_read_and_shown_constructed(make: Any) -> None:
    missed = player(guid="Player-9998-0000000A", resolve_tried_at=T1 + 300)
    env = make(_book3({KEY_A: missed}))
    read = env.book()
    assert read.players[0].resolve_tried_at == T1 + 300 and read.players[0].other_keys == []
    assert _rows(env)[KEY_A]["resolve_tried_at"] == T1 + 300
    bad = make(_book3({KEY_A: player(resolve_tried_at="soon")}))
    assert bad.book().unread[0].reason == "resolve_tried_at is not a time"


def test_a_flavor_without_second_names_says_name_not_first_name_constructed(
    make: Any,
) -> None:
    """The captured character folder moved to the retail shape
    (`<Realm>/<Name>/`, constructed): a name the game gave with no second
    part is the name, not a first name."""
    env = make(_book3({TAY: _tay(resolved_name="Tay", resolved_realm="Invented Realm")}))
    account = env.flavor / "WTF" / "Account" / ACCOUNT
    (account / "Somerealm").mkdir()
    (account / "1" / "Sandgraal-Rizzen").rename(account / "Somerealm" / "Sandgraal")
    (account / "1").rmdir()
    lines = json.loads(env.run("crew", "show", TAY, "--json").stdout)["lines"]
    assert (
        "  Name in game: Tay (name Tay, realm Invented Realm; as the game gave it for the "
        f"character's id on {_day(ASKED)})"
    ) in lines


def test_control_characters_in_a_name_the_game_gave_are_escaped_constructed(
    make: Any,
) -> None:
    """Hostile input, constructed: an escape sequence, a C1 control and a
    line break in `resolved_name` reach no terminal raw."""
    hostile = "Tay\x1b[31m\x9b2J\nSoulrend"
    env = make(_book3({TAY: _tay(resolved_name=hostile)}))
    text = env.run("crew", "show", TAY)
    assert text.exit_code == 0, text.output
    assert "\x1b" not in text.stdout and "\x9b" not in text.stdout
    name_lines = [line for line in text.stdout.splitlines() if "Name in game" in line]
    assert len(name_lines) == 1 and "Soulrend" in name_lines[0]
