"""`wowlab_core.bnet`: public profiles from Blizzard's API (M22-11, ADR-0040 (2)).

No test asks Blizzard anything (ADR-0012): the client is graded on recorded
replies handed to it through its `send` transport, and every socket is
refused while these tests run. The owner has not made an API client yet
(M22-09; owner decision of 2026-11-24, "Build it, key later"), so no reply
was recorded from the live service. The replies below are **constructed**
(L8), each labelled so in its test id.

Provenance of the constructed replies (no file under `fixtures/`: the
corpus holds real captures only, and constructed inputs live in the test
that uses them, as `parser/test_crewbook_constructed.py` does):
- The token answer (`access_token`, `token_type` "bearer", `expires_in`,
  `sub`), the profile summary (`name`, `gender`, `faction`, `race`,
  `character_class`, `active_spec`, `guild` with `key.href`, `level`,
  `achievement_points`, `last_login_timestamp`, `average_item_level`,
  `equipped_item_level`), the equipment (`equipped_items` rows with `item.id`,
  `slot`, `quality`, `name`, `level.value`, `enchantments`), the character
  media (`assets` of `key` and `value`) and the guild (`name`, `faction`,
  `member_count`, `achievement_points`, `created_timestamp`) follow the
  shapes of Blizzard's documented World of Warcraft Profile API and OAuth
  client-credentials flow, as written from the documentation's field names,
  not from a captured answer.
- The Forever parts follow the community forum notes in the ticket
  (threads 59774 and 60334, read 2026-11-23): the namespace
  `profile-forever-us`, the path `/profile/wow/character/{first}-{second}`,
  `surname` and `ruleset` in the profile, and no realm. The armory's
  `"ruleset":{"slug":…}` was seen by the M21-03 domain review on 2026-11-24
  (`link_sites.toml`, `[rulesets]`); whether Forever's profile carries it
  the same way is unknown, so an object with `name` and `slug`, and a
  string, are both read. The guild's address for a Forever character is unknown; a
  one-segment `/data/wow/guild/<slug>` is used here.
- The render-host addresses are shaped like Blizzard's
  (`https://render.worldofwarcraft.com/<region>/character/.../<id>-avatar.jpg`);
  the Forever form is unknown.

`test_bnet_live.py` (`@live`, run by the owner only) checks all of it
against the service once an API client exists.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import stat
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any, cast

import platformdirs
import pytest

from wowlab_core import bnet

CLIENT_ID = "constructedclientid000000000001"
SECRET = "constructedSECRETvalue0000000099"
TOKEN = "USconstructedTOKENvalueAAAAAAAAAAAA"
API = "https://us.api.blizzard.com"
NS = "namespace=profile-forever-us&locale=en_US"
CHAR = "/profile/wow/character/sandgraal-rizzen"
GUILD_PATH = "/data/wow/guild/the-crew"
RENDER = "https://render.worldofwarcraft.com/forever-us/character/sandgraal-rizzen/112/1234567"

TOKEN_BODY = {"access_token": TOKEN, "token_type": "bearer", "expires_in": 86399, "sub": CLIENT_ID}
PROFILE_BODY: dict[str, Any] = {
    "_links": {"self": {"href": f"{API}{CHAR}?namespace=profile-forever-us"}},
    "id": 12345678,
    "name": "Sandgraal",
    "surname": "Rizzen",
    "gender": {"type": "MALE", "name": "Male"},
    "faction": {"type": "HORDE", "name": "Horde"},
    "race": {"key": {"href": f"{API}/data/wow/playable-race/5"}, "name": "Undead", "id": 5},
    "character_class": {
        "key": {"href": f"{API}/data/wow/playable-class/9"},
        "name": "Warlock",
        "id": 9,
    },
    "active_spec": {
        "key": {"href": f"{API}/data/wow/playable-specialization/265"},
        "name": "Affliction",
        "id": 265,
    },
    "ruleset": {"type": "PVP", "name": "PvP", "slug": "pvp"},
    "guild": {
        "key": {"href": f"{API}{GUILD_PATH}?namespace=profile-forever-us"},
        "name": "The Crew",
        "id": 42,
        "faction": {"type": "HORDE", "name": "Horde"},
    },
    "level": 29,
    "experience": 0,
    "achievement_points": 150,
    "equipment": {"href": f"{API}{CHAR}/equipment?namespace=profile-forever-us"},
    "media": {"href": f"{API}{CHAR}/character-media?namespace=profile-forever-us"},
    "last_login_timestamp": 1763900000000,
    "average_item_level": 31,
    "equipped_item_level": 30,
}
EQUIPMENT_BODY: dict[str, Any] = {
    "_links": {"self": {"href": f"{API}{CHAR}/equipment"}},
    "character": {"name": "Sandgraal", "id": 12345678},
    "equipped_items": [
        {
            "item": {"key": {"href": f"{API}/data/wow/item/9749"}, "id": 9749},
            "slot": {"type": "HEAD", "name": "Head"},
            "quantity": 1,
            "quality": {"type": "UNCOMMON", "name": "Uncommon"},
            "name": "Simple Blouse",
            "level": {"value": 30, "display_string": "Item Level 30"},
            "enchantments": [{"display_string": "Enchanted: +3 Stamina", "enchantment_id": 724}],
        },
        {
            "item": {"key": {"href": f"{API}/data/wow/item/5071"}, "id": 5071},
            "slot": {"type": "RANGED", "name": "Ranged"},
            "quality": {"type": "RARE", "name": "Rare"},
            "name": "Shadow Wand",
            "level": {"value": 32, "display_string": "Item Level 32"},
        },
    ],
}
MEDIA_BODY: dict[str, Any] = {
    "_links": {"self": {"href": f"{API}{CHAR}/character-media"}},
    "character": {"name": "Sandgraal", "id": 12345678},
    "assets": [
        {"key": "avatar", "value": f"{RENDER}-avatar.jpg"},
        {"key": "inset", "value": f"{RENDER}-inset.jpg"},
        {"key": "main-raw", "value": f"{RENDER}-main-raw.png"},
    ],
}
GUILD_BODY: dict[str, Any] = {
    "_links": {"self": {"href": f"{API}{GUILD_PATH}"}},
    "id": 42,
    "name": "The Crew",
    "faction": {"type": "HORDE", "name": "Horde"},
    "achievement_points": 200,
    "member_count": 12,
    "created_timestamp": 1763000000000,
}


def reply(status: int, body: object = None) -> bnet.Reply:
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    return bnet.Reply(status, raw)


class Recorded:
    """A transport that answers from a table keyed by method and path, and
    records every request."""

    def __init__(self, answers: dict[tuple[str, str], bnet.Reply]) -> None:
        self.answers = answers
        self.requests: list[bnet.Request] = []

    def __call__(self, request: bnet.Request) -> bnet.Reply:
        self.requests.append(request)
        path = urllib.parse.urlsplit(request.url).path
        return self.answers.get((request.method, path), reply(404))


def answers(**over: bnet.Reply) -> dict[tuple[str, str], bnet.Reply]:
    table = {
        ("POST", "/token"): reply(200, TOKEN_BODY),
        ("GET", CHAR): reply(200, PROFILE_BODY),
        ("GET", CHAR + "/equipment"): reply(200, EQUIPMENT_BODY),
        ("GET", CHAR + "/character-media"): reply(200, MEDIA_BODY),
        ("GET", GUILD_PATH): reply(200, GUILD_BODY),
    }
    keys = {
        "token": ("POST", "/token"),
        "profile": ("GET", CHAR),
        "equipment": ("GET", CHAR + "/equipment"),
        "media": ("GET", CHAR + "/character-media"),
        "guild": ("GET", GUILD_PATH),
    }
    for name, value in over.items():
        table[keys[name]] = value
    return table


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every socket that would open is refused and recorded (constructed)."""
    tried: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        tried.append(args)
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    return tried


@pytest.fixture(autouse=True, params=["apart", "same"])
def user_data(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """The user data and config directories, both under `tmp_path`: apart
    (Linux) and, as on macOS and Windows, one folder (`same`)."""
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    config = redirected if request.param == "same" else tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    return redirected


def folders_apart() -> bool:
    """Whether the config and data folders differ (Linux's layout)."""
    return bnet.config_dir() != bnet.blizzard_dir()


@pytest.fixture
def game() -> bnet.Game:
    found = bnet.game_for_version("1.60.1.70888")
    assert found is not None
    return found


def creds() -> bnet.Credentials:
    return bnet.Credentials(CLIENT_ID, SECRET)


def clock(start: datetime) -> Callable[[], datetime]:
    ticks = iter(start + timedelta(seconds=n) for n in range(1000))
    return lambda: next(ticks)


T0 = datetime(2026, 11, 24, 18, 0, 0, tzinfo=UTC)
NAME = bnet.Name("Sandgraal", "Rizzen")


# ─── the games and names ─────────────────────────────────────────────────────


def test_the_game_is_chosen_by_the_client_version(game: bnet.Game) -> None:
    assert game.label == "forever"
    assert game.namespace == "profile-forever-{region}"
    assert bnet.game_for_version("1.60.0.1") is not None
    for version in ("1.15.7.61582", "11.2.5.63906", "5.5.1.1", "2.0", None, "x.y", "1.60.a"):
        assert bnet.game_for_version(version) is None, version


@pytest.mark.parametrize(
    "text",
    [
        '[[game]]\nlabel = "x"\nnamespace = "profile-x"\ncharacter = "/profile/wow/character/{first}"\nfrom = "1"\nbelow = "2"\n',
        '[[game]]\nlabel = "x"\nnamespace = "p-{region}"\ncharacter = "/profile/wow/character/{realm}/{first}"\nfrom = "1"\nbelow = "2"\n',
        '[[game]]\nlabel = "x"\nnamespace = "p-{region}"\ncharacter = "/profile/wow/character/{second}"\nfrom = "1"\nbelow = "2"\n',
        '[[game]]\nlabel = "x"\nnamespace = "p-{region}"\ncharacter = "https://evil.example/{first}"\nfrom = "1"\nbelow = "2"\n',
        '[[game]]\nlabel = "x"\nnamespace = "p-{region}"\ncharacter = "/profile/wow/character/{first}"\nfrom = "1"\nbelow = "2"\nextra = 1\n',
    ],
    ids=[
        "constructed: no region in the namespace",
        "constructed: an unknown field",
        "constructed: no first name",
        "constructed: another host",
        "constructed: an unknown key",
    ],
)
def test_a_games_file_out_of_shape_is_refused(text: str) -> None:
    with pytest.raises(ValueError):  # pydantic's ValidationError is a ValueError
        bnet.load_games(text)


@pytest.mark.parametrize(
    ("text", "first", "second"),
    [
        ("Sandgraal-Rizzen", "Sandgraal", "Rizzen"),
        ("Sandgraal Rizzen", "Sandgraal", "Rizzen"),
        ("  Sandgraal Rizzen ", "Sandgraal", "Rizzen"),
        ("Sandgraal", "Sandgraal", None),
        ("Ærøskøbing-Ñandú", "Ærøskøbing", "Ñandú"),
    ],
    ids=["folder form", "in-game form", "spaces around", "first name only", "letters past ASCII"],
)
def test_a_name_is_read_in_both_forms(text: str, first: str, second: str | None) -> None:
    assert bnet.parse_name(text) == (first, second)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "-Rizzen",
        "Sandgraal-",
        "Sandgraal-Rizzen-Extra",
        "Sandgraal Rizzen Extra",
        "../etc",
        "Sand/graal",
        "Sand?graal",
        "Sand%2Fgraal",
        "Sand1graal",
        "Sandgraal" + chr(0x202E) + "Rizzen",
        "Sandgraal\x00",
        "A" * 25,
        "Sandgraal#x",
    ],
    ids=lambda t: "constructed: " + repr(t)[:30],
)
def test_a_hostile_name_is_refused_before_any_address_is_built(text: str) -> None:
    with pytest.raises(bnet.BnetNameError):
        bnet.parse_name(text)


def test_the_address_is_lowercase_and_encoded(game: bnet.Game) -> None:
    assert bnet.character_path(game, NAME) == CHAR
    accented = bnet.parse_name("Ærøskøbing-Ñandú")
    path = bnet.character_path(game, accented)
    assert path == "/profile/wow/character/%C3%A6r%C3%B8sk%C3%B8bing-%C3%B1and%C3%BA"
    with pytest.raises(bnet.BnetNameError, match="first and second name"):
        bnet.character_path(game, bnet.Name("Sandgraal", None))


@pytest.mark.parametrize("value", ["us", "eu", "kr", "tw", "cn"])
def test_each_region_is_taken(value: str) -> None:
    assert bnet.check_region(value) == value


@pytest.mark.parametrize(
    "value", ["US", "test", "", "us/../eu", "xx"], ids=lambda v: f"constructed: {v!r}"
)
def test_another_region_is_refused(value: str) -> None:
    with pytest.raises(bnet.BnetNameError):
        bnet.check_region(value)


# ─── a fetch ─────────────────────────────────────────────────────────────────


def test_a_fetch_asks_for_a_token_then_the_four_parts_constructed(game: bnet.Game) -> None:
    send = Recorded(answers())
    profile, raw = bnet.fetch_profile(creds(), game, "us", NAME, send=send, now=clock(T0))
    methods = [(r.method, r.url) for r in send.requests]
    assert methods == [
        ("POST", "https://oauth.battle.net/token"),
        ("GET", f"{API}{CHAR}?{NS}"),
        ("GET", f"{API}{CHAR}/equipment?{NS}"),
        ("GET", f"{API}{CHAR}/character-media?{NS}"),
        ("GET", f"{API}{GUILD_PATH}?{NS}"),
    ]
    token_request = send.requests[0]
    expected = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    assert token_request.headers["Authorization"] == f"Basic {expected}"
    assert token_request.body == b"grant_type=client_credentials"
    for r in send.requests[1:]:
        assert r.headers["Authorization"] == f"Bearer {TOKEN}"
        assert r.body is None
        assert SECRET not in r.url and TOKEN not in r.url
    c = profile.character
    assert c is not None
    assert (c.name, c.surname, c.level, c.race, c.character_class) == (
        "Sandgraal",
        "Rizzen",
        29,
        "Undead",
        "Warlock",
    )
    assert (c.active_spec, c.faction, c.ruleset, c.guild) == (
        "Affliction",
        "Horde",
        "PvP",
        "The Crew",
    )
    assert (c.average_item_level, c.equipped_item_level) == (31, 30)
    assert c.last_login == "2025-11-23T12:13:20+00:00"
    assert c.realm is None
    # The slug the Raider.IO link's [rulesets] table takes (link_sites.toml).
    assert c.ruleset_slug == "pvp"
    assert [
        (i.slot, i.slot_name, i.item_id, i.name, i.quality, i.item_level) for i in profile.equipment
    ] == [
        ("HEAD", "Head", 9749, "Simple Blouse", "Uncommon", 30),
        ("RANGED", "Ranged", 5071, "Shadow Wand", "Rare", 32),
    ]
    assert profile.equipment[0].enchantments == ["Enchanted: +3 Stamina"]
    assert [(a.key, a.url) for a in profile.portrait] == [
        ("avatar", f"{RENDER}-avatar.jpg"),
        ("inset", f"{RENDER}-inset.jpg"),
        ("main-raw", f"{RENDER}-main-raw.png"),
    ]
    assert profile.guild is not None
    assert (profile.guild.name, profile.guild.member_count) == ("The Crew", 12)
    states = [
        profile.character_state,
        profile.equipment_state,
        profile.portrait_state,
        profile.guild_state,
    ]
    assert [s.status for s in states] == ["ok"] * 4
    # Each part carries the time it was asked.
    assert [s.fetched_at for s in states] == [
        "2026-11-24T18:00:01+00:00",
        "2026-11-24T18:00:02+00:00",
        "2026-11-24T18:00:03+00:00",
        "2026-11-24T18:00:04+00:00",
    ]
    assert profile.fetched_at == "2026-11-24T18:00:00+00:00"
    assert set(raw) == {"character", "equipment", "portrait", "guild"}
    assert (profile.name, profile.region, profile.game) == ("Sandgraal Rizzen", "us", "forever")


def test_a_ruleset_given_as_a_string_is_read_too_constructed(game: bnet.Game) -> None:
    send = Recorded(answers(profile=reply(200, {**PROFILE_BODY, "ruleset": "PVP"})))
    profile, _ = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    assert profile.character is not None and profile.character.ruleset == "PVP"
    assert profile.character.ruleset_slug is None


def test_another_region_asks_its_own_host_and_locale_constructed(game: bnet.Game) -> None:
    send = Recorded({})

    def answer(request: bnet.Request) -> bnet.Reply:
        send.requests.append(request)
        if request.method == "POST":
            return reply(200, TOKEN_BODY)
        return reply(404)

    bnet.fetch_profile(creds(), game, "eu", NAME, send=answer)
    assert send.requests[1].url == (
        f"https://eu.api.blizzard.com{CHAR}?namespace=profile-forever-eu&locale=en_GB"
    )


def test_no_profile_asks_nothing_more_constructed(game: bnet.Game) -> None:
    send = Recorded(answers(profile=reply(404)))
    profile, raw = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    assert len(send.requests) == 2
    assert profile.character is None and profile.character_state.status == "not_found"
    assert "below level 10" in (profile.character_state.detail or "")
    for state in (profile.equipment_state, profile.portrait_state, profile.guild_state):
        assert state.status == "not_asked"
    assert raw == {}


def test_a_portrait_not_made_yet_is_said_and_the_rest_is_kept_constructed(game: bnet.Game) -> None:
    send = Recorded(answers(media=reply(404)))
    profile, _ = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    assert profile.portrait == [] and profile.portrait_state.status == "not_found"
    assert "hours" in (profile.portrait_state.detail or "")
    assert profile.equipment and profile.guild is not None


def test_a_profile_with_no_guild_asks_for_none_constructed(game: bnet.Game) -> None:
    body = {k: v for k, v in PROFILE_BODY.items() if k != "guild"}
    send = Recorded(answers(profile=reply(200, body)))
    profile, _ = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    assert len(send.requests) == 4
    assert profile.guild_state.status == "not_asked"
    assert profile.guild_state.detail == "the profile names no guild"


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/data/wow/guild/the-crew",
        "http://us.api.blizzard.com/data/wow/guild/the-crew",
        "https://us.api.blizzard.com/profile/user/wow",
        "https://us.api.blizzard.com/data/wow/guild/../../oauth",
        "https://eu.api.blizzard.com/data/wow/guild/the-crew",
        "https://user:pw@us.api.blizzard.com/data/wow/guild/the-crew",
        "https://us.api.blizzard.com:8443/data/wow/guild/the-crew",
        "https://us.api.blizzard.com/data/wow/guild/..",
        "https://us.api.blizzard.com/data/wow/guild/the-crew/.",
        "https://us.api.blizzard.com/data/wow/guild/%2e%2e/oauth",
        "https://us.api.blizzard.com/data/wow/guild/the%2Fcrew",
        "https://us.api.blizzard.com/data/wow/guild/the%5Ccrew",
    ],
    ids=lambda h: "constructed: " + h[25:75],
)
def test_a_guild_address_elsewhere_is_not_asked_constructed(game: bnet.Game, href: str) -> None:
    body = {**PROFILE_BODY, "guild": {"key": {"href": href}, "name": "The Crew"}}
    send = Recorded(answers(profile=reply(200, body)))
    profile, _ = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    assert len(send.requests) == 4
    assert all(
        urllib.parse.urlsplit(r.url).hostname in ("oauth.battle.net", "us.api.blizzard.com")
        for r in send.requests
    )
    assert profile.guild_state.status == "not_asked"
    assert profile.character is not None and profile.character.guild == "The Crew"


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/forever-us/character/x-avatar.jpg",
        "http://render.worldofwarcraft.com/x-avatar.jpg",
        "https://render.worldofwarcraft.com.evil.example/x.jpg",
        "https://render.worldofwarcraft.com:444/x.jpg",
        "https://u@render.worldofwarcraft.com/x.jpg",
        "https://render.worldofwarcraft.com/x.jpg?track=1",
        "https://render.worldofwarcraft.com/x.jpg#f",
        "https://render.worldofwarcraft.com/../x.jpg",
        "https://render.worldofwarcraft.com/a b.jpg",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "//render.worldofwarcraft.com/x.jpg",
        "https://render.worldofwarcraft.com/" + "a" * 600,
        42,
        None,
    ],
    ids=lambda v: "constructed: " + repr(v)[:40],
)
def test_a_portrait_address_elsewhere_is_dropped_constructed(
    game: bnet.Game, value: object
) -> None:
    media = {
        "assets": [
            {"key": "avatar", "value": value},
            {"key": "inset", "value": f"{RENDER}-inset.jpg"},
        ]
    }
    send = Recorded(answers(media=reply(200, media)))
    profile, _ = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    assert [a.key for a in profile.portrait] == ["inset"]
    assert bnet.render_url(value) is None


def test_an_answer_of_another_shape_is_read_as_far_as_it_goes_constructed(game: bnet.Game) -> None:
    hostile = {
        "name": ["not", "text"],
        "level": "29",
        "race": {"name": 5},
        "average_item_level": True,
        "equipped_item_level": 2**60,
        "last_login_timestamp": -5,
        "guild": "The Crew",
        "surname": "x" * 1000,
    }
    equipment = {
        "equipped_items": [1, "x", {"slot": "HEAD", "item": [], "level": {"value": "30"}}] * 40
    }
    send = Recorded(
        answers(
            profile=reply(200, hostile), equipment=reply(200, equipment), media=reply(200, ["x"])
        )
    )
    profile, _ = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    c = profile.character
    assert c is not None
    assert (c.name, c.level, c.race, c.average_item_level, c.equipped_item_level) == (None,) * 5
    assert c.last_login is None and c.guild == "The Crew"
    assert c.surname is not None and len(c.surname) == 200
    assert len(profile.equipment) == 40
    # A slot given as a bare string is its name; the rest is not read.
    assert profile.equipment[0] == bnet.BnetItem(slot_name="HEAD")
    assert profile.portrait == [] and profile.portrait_state.status == "error"
    assert profile.guild_state.status == "not_asked"


@pytest.mark.parametrize(
    "answer",
    [
        bnet.Reply(200, b"<html>"),
        bnet.Reply(200, b"\xff\xfe"),
        bnet.Reply(200, b"[" * 100000),
        bnet.Reply(200, b"{}", too_large=True),
        bnet.Reply(200, b'{"name": "\\ud800x"}'),
        bnet.Reply(200, b'{"a": ' + b"[" * 900 + b"]" * 900 + b"}"),
    ],
    ids=[
        "constructed: html",
        "constructed: not utf-8",
        "constructed: deep",
        "constructed: too large",
        "constructed: a lone surrogate",
        "constructed: nested 900 deep",
    ],
)
def test_an_unreadable_part_is_an_error_not_a_crash_constructed(
    game: bnet.Game, answer: bnet.Reply
) -> None:
    send = Recorded(answers(equipment=answer))
    profile, _ = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    assert profile.equipment_state.status == "error"
    assert profile.equipment_state.detail == "the answer was not JSON wowlab reads"


@pytest.mark.parametrize(
    ("over", "error", "words"),
    [
        ({"token": reply(401)}, bnet.BnetRefusedError, "refused the client ID and secret"),
        ({"token": reply(400)}, bnet.BnetRefusedError, "refused the client ID and secret"),
        ({"token": reply(429)}, bnet.BnetUnavailableError, "slow down"),
        ({"token": reply(503)}, bnet.BnetUnavailableError, "HTTP 503"),
        (
            {"token": reply(200, {"token_type": "bearer"})},
            bnet.BnetUnavailableError,
            "token answer",
        ),
        (
            {"token": reply(200, {"access_token": "a b\nc"})},
            bnet.BnetUnavailableError,
            "token answer",
        ),
        ({"profile": reply(401)}, bnet.BnetRefusedError, "did not accept the token"),
        ({"profile": reply(429)}, bnet.BnetRefusedError, "slow down"),
        ({"equipment": reply(429)}, bnet.BnetRefusedError, "slow down"),
    ],
    ids=[
        "constructed: bad client",
        "constructed: bad request",
        "constructed: token rate limited",
        "constructed: token server error",
        "constructed: no token",
        "constructed: hostile token",
        "constructed: token not accepted",
        "constructed: profile rate limited",
        "constructed: equipment rate limited",
    ],
)
def test_a_refusal_raises_in_words_with_no_secret_constructed(
    game: bnet.Game, over: dict[str, bnet.Reply], error: type[Exception], words: str
) -> None:
    send = Recorded(answers(**over))
    with pytest.raises(error, match=words) as raised:
        bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    text = str(raised.value) + repr(raised.value)
    assert SECRET not in text and TOKEN not in text and CLIENT_ID not in text


@pytest.mark.parametrize("status", [403, 500, 502], ids=lambda s: f"constructed: HTTP {s}")
def test_a_part_that_fails_is_said_and_the_rest_fetched_constructed(
    game: bnet.Game, status: int
) -> None:
    send = Recorded(answers(guild=reply(status)))
    profile, _ = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    assert profile.guild_state.status == "error"
    assert f"HTTP {status}" in (profile.guild_state.detail or "")
    assert profile.equipment_state.status == "ok"


# ─── the secret ──────────────────────────────────────────────────────────────


def test_credentials_hide_the_secret_and_refuse_odd_values() -> None:
    c = creds()
    assert SECRET not in repr(c) and SECRET not in str(c) and SECRET not in f"{c}"
    assert c.secret() == SECRET
    request = bnet.Request("POST", "https://oauth.battle.net/token", {"Authorization": SECRET})
    assert SECRET not in repr(request)
    for bad in (
        "",
        "short",
        "has space inside it",
        'quote"inside1234',
        "line\nbreak12345",
        "é" * 20,
    ):
        with pytest.raises(bnet.BnetNameError) as raised:
            bnet.Credentials(CLIENT_ID, bad)
        assert bad not in str(raised.value) or bad == ""
        with pytest.raises(bnet.BnetNameError):
            bnet.Credentials(bad, SECRET)


def test_the_secret_is_kept_owner_only_and_read_back(user_data: Path) -> None:
    assert bnet.read_credentials() is None
    path = bnet.save_credentials(creds())
    # ADR-0040 (2): the user config directory, not the data folder (one
    # folder on macOS and Windows).
    assert path == bnet.config_dir() / "client.json"
    if folders_apart():
        assert (
            path == user_data.parent.parent / "userconfig" / "wowlab" / "blizzard" / "client.json"
        )
        assert not (user_data / "blizzard").exists()
    got = bnet.read_credentials()
    assert got is not None and (got.client_id, got.secret()) == (CLIENT_ID, SECRET)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == ["client.json"], "no temporary file left"
    assert bnet.clear_credentials() is True
    assert bnet.read_credentials() is None
    assert bnet.clear_credentials() is False


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
def test_a_secret_file_others_can_read_is_not_used_constructed() -> None:
    path = bnet.save_credentials(creds())
    path.chmod(0o644)
    with pytest.raises(bnet.BnetError, match="other users") as raised:
        bnet.read_credentials()
    assert SECRET not in str(raised.value)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_linked_secret_file_is_neither_read_nor_written_constructed(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(
        json.dumps({"client_id": CLIENT_ID, "client_secret": SECRET}), encoding="utf-8"
    )
    elsewhere.chmod(0o600)
    folder = bnet.credentials_path().parent
    folder.mkdir(parents=True)
    bnet.credentials_path().symlink_to(elsewhere)
    with pytest.raises(bnet.BnetError):
        bnet.read_credentials()
    with pytest.raises(bnet.BnetError, match="not a plain file"):
        bnet.save_credentials(creds())
    with pytest.raises(bnet.BnetError, match="not a plain file"):
        bnet.clear_credentials()
    assert elsewhere.read_text(encoding="utf-8").count(SECRET) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"client_id": 1, "client_secret": "x"}',
        b'{"client_id": "constructed0001"}',
    ],
    ids=[
        "constructed: not json",
        "constructed: a list",
        "constructed: a number",
        "constructed: no secret",
    ],
)
def test_a_damaged_secret_file_is_refused_without_quoting_it(body: bytes) -> None:
    path = bnet.save_credentials(creds())
    path.write_bytes(body)
    path.chmod(0o600)
    with pytest.raises(bnet.BnetError, match="login") as raised:
        bnet.read_credentials()
    assert body.decode("utf-8", "replace") not in str(raised.value)


def test_nothing_is_kept_inside_a_game_install_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "World of Warcraft"
    install.mkdir()
    (install / ".build.info").write_bytes(b"constructed\n")
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: install / "wowlab")
    with pytest.raises(bnet.BnetError, match="inside a game install"):
        bnet.save_credentials(creds())
    assert sorted(p.name for p in install.iterdir()) == [".build.info"]
    # The remover refuses the same place, before it removes anything.
    planted = install / "wowlab" / "blizzard"
    planted.mkdir(parents=True)
    (planted / "client.json").write_bytes(b"the install's own file\n")
    with pytest.raises(bnet.BnetError, match="inside a game install"):
        bnet.clear_credentials()
    with pytest.raises(bnet.BnetError, match="inside a game install"):
        bnet.read_credentials()
    assert (planted / "client.json").read_bytes() == b"the install's own file\n"


def test_one_folder_for_config_and_data_keeps_the_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS and Windows: platformdirs gives one folder for config and data.
    Login, then reads, keep the client; logout removes it."""
    one = tmp_path / "Application Support" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: one)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: one)
    path = bnet.save_credentials(creds())
    assert path == one / "blizzard" / "client.json"
    for _ in range(3):
        got = bnet.read_credentials()
        assert got is not None and got.secret() == SECRET
        assert path.is_file()
    bnet.save_credentials(creds())
    assert bnet.read_credentials() is not None and path.is_file()
    assert bnet.clear_credentials() is True and not path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_linked_blizzard_data_folder_refuses_every_write_constructed(
    user_data: Path, tmp_path: Path
) -> None:
    """The saved profiles' folder is a link: saving or removing the API
    client is refused before anything is written or removed."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "client.json").write_bytes(b"left alone\n")
    user_data.mkdir(parents=True)
    (user_data / "blizzard").symlink_to(elsewhere, target_is_directory=True)
    for call in (bnet.clear_credentials, lambda: bnet.save_credentials(creds())):
        with pytest.raises(bnet.BnetError, match="is a link"):
            call()
    assert (elsewhere / "client.json").read_bytes() == b"left alone\n"
    if folders_apart():
        assert not bnet.credentials_path().exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are POSIX")
def test_a_pipe_in_place_of_the_file_is_refused_not_waited_on_constructed() -> None:
    folder = bnet.credentials_path().parent
    folder.mkdir(parents=True, mode=0o700)
    os.mkfifo(bnet.credentials_path(), 0o600)
    with pytest.raises(bnet.BnetError, match="not a plain file"):
        bnet.read_credentials()


def test_credentials_and_requests_are_never_serialized() -> None:
    import copy
    import dataclasses
    import pickle

    request = bnet.Request("POST", "https://oauth.battle.net/token", {"Authorization": SECRET})
    for value in (creds(), request):
        with pytest.raises(TypeError):
            pickle.dumps(value)
        with pytest.raises(TypeError):
            copy.copy(value)
    assert not dataclasses.is_dataclass(request)
    with pytest.raises(TypeError):
        dataclasses.asdict(request)  # type: ignore[call-overload]


def test_a_name_with_combining_accents_is_the_same_name() -> None:
    composed = bnet.parse_name("\u00c6r\u00f8sk\u00f8bing-\u00d1and\u00fa")
    decomposed = bnet.parse_name("\u00c6r\u00f8sk\u00f8bing-N\u0303andu\u0301")
    assert decomposed == composed


def test_a_hostile_raw_answer_is_dropped_and_the_fetch_still_saved_constructed(
    game: bnet.Game,
) -> None:
    profile, raw = bnet.fetch_profile(creds(), game, "us", NAME, send=Recorded(answers()))
    hostile = {**raw, "character": {"name": "\ud800x"}, "equipment": {"x": "y" * (7 * 1024 * 1024)}}
    path = bnet.save_profile(profile, hostile)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved["raw"]) == {"portrait", "guild"}
    assert path.stat().st_size <= bnet._MAX_SAVED_BYTES
    shown, kept = bnet.load_profile("forever", "us", NAME)
    assert kept == 1 and shown is not None and shown.character == profile.character


def test_the_secret_and_token_reach_no_log_file_or_report_constructed(
    game: bnet.Game, user_data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    send = Recorded(answers())
    profile, raw = bnet.fetch_profile(creds(), game, "us", NAME, send=send)
    bnet.save_profile(profile, raw)
    loaded, kept = bnet.load_profile("forever", "us", NAME)
    assert kept == 1 and loaded is not None
    saved = [p for p in (user_data / "blizzard" / "profiles").rglob("*") if p.is_file()]
    assert len(saved) == 1
    for text in (
        caplog.text,
        saved[0].read_text(encoding="utf-8"),
        profile.model_dump_json(),
        loaded.model_dump_json(),
        json.dumps(raw),
    ):
        assert SECRET not in text and TOKEN not in text and CLIENT_ID not in text


# ─── saved fetches ───────────────────────────────────────────────────────────


def test_show_fills_a_part_the_last_fetch_missed_from_an_earlier_one_constructed(
    game: bnet.Game,
) -> None:
    first, raw = bnet.fetch_profile(
        creds(), game, "us", NAME, send=Recorded(answers()), now=clock(T0)
    )
    bnet.save_profile(first, raw, at=T0)
    later = T0 + timedelta(hours=1)
    second, raw2 = bnet.fetch_profile(
        creds(), game, "us", NAME, send=Recorded(answers(media=reply(404))), now=clock(later)
    )
    bnet.save_profile(second, raw2, at=later)
    shown, kept = bnet.load_profile("forever", "us", NAME)
    assert kept == 2 and shown is not None
    assert shown.fetched_at == second.fetched_at
    assert shown.character_state.fetched_at == "2026-11-24T19:00:01+00:00"
    assert shown.portrait_state.status == "ok" and shown.portrait_state.from_earlier
    assert shown.portrait_state.fetched_at == "2026-11-24T18:00:03+00:00"
    assert shown.portrait == first.portrait
    # Another name, region or game is another character.
    assert bnet.load_profile("forever", "eu", NAME) == (None, 0)
    assert bnet.load_profile("forever", "us", bnet.Name("Sandgraal", "Other")) == (None, 0)
    assert bnet.load_profile("other", "us", NAME) == (None, 0)
    # The name's case does not make another character.
    assert bnet.load_profile("forever", "us", bnet.Name("SANDGRAAL", "rizzen"))[1] == 2


def test_only_the_newest_fetches_are_kept_and_a_bad_file_is_passed_over_constructed(
    game: bnet.Game,
) -> None:
    profile, raw = bnet.fetch_profile(creds(), game, "us", NAME, send=Recorded(answers()))
    for n in range(bnet.KEEP_FETCHES + 3):
        path = bnet.save_profile(profile, raw, at=T0 + timedelta(minutes=n))
    files = sorted(path.parent.iterdir())
    assert len(files) == bnet.KEEP_FETCHES
    assert files[0].name.startswith("20261124T180300")
    path.write_bytes(b"{not json")  # the newest, damaged (constructed)
    shown, kept = bnet.load_profile("forever", "us", NAME)
    assert shown is not None and kept == bnet.KEEP_FETCHES - 1


# ─── the transport ───────────────────────────────────────────────────────────


def test_the_opener_speaks_https_only_and_takes_no_proxy() -> None:
    opener = bnet._opener()
    kinds = {type(h) for h in opener.handlers}
    assert urllib.request.HTTPSHandler in kinds
    for banned in (
        urllib.request.HTTPHandler,
        urllib.request.FileHandler,
        urllib.request.FTPHandler,
        urllib.request.DataHandler,
        urllib.request.ProxyHandler,
    ):
        assert banned not in kinds, banned
    https = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
    context = cast(Any, https)._context
    assert context.verify_mode.name == "CERT_REQUIRED" and context.check_hostname


def _redirect(old: str, new: str) -> urllib.request.Request | None:
    handler = bnet._SameHostRedirect()
    request = urllib.request.Request(old, headers={"Authorization": "Bearer x"})
    return handler.redirect_request(request, None, 302, "Found", cast(Any, Message()), new)


@pytest.mark.parametrize(
    "new",
    [
        "https://evil.example/profile",
        "http://us.api.blizzard.com/profile",
        "https://eu.api.blizzard.com/profile",
        "https://us.api.blizzard.com:8443/profile",
        "ftp://us.api.blizzard.com/profile",
        "file:///etc/passwd",
        "https://x@us.api.blizzard.com/profile",
        "https://x:y@us.api.blizzard.com/profile",
    ],
    ids=lambda u: "constructed: " + u,
)
def test_a_redirect_to_another_host_or_scheme_is_refused_constructed(new: str) -> None:
    with pytest.raises(bnet.BnetRefusedError, match="does not follow"):
        _redirect(f"{API}{CHAR}", new)


def test_a_redirect_on_the_same_host_is_followed_constructed() -> None:
    followed = _redirect(f"{API}{CHAR}", "/profile/wow/character/other")
    assert followed is not None and followed.full_url == f"{API}/profile/wow/character/other"


@pytest.mark.parametrize(
    "url",
    [
        f"http://us.api.blizzard.com{CHAR}",
        f"https://evil.example{CHAR}",
        f"https://us.api.blizzard.com:444{CHAR}",
        f"https://user:pw@us.api.blizzard.com{CHAR}",
        "file:///etc/passwd",
    ],
    ids=lambda u: "constructed: " + u[:40],
)
def test_the_transport_refuses_any_other_address_before_connecting_constructed(
    url: str, no_network: list[object]
) -> None:
    with pytest.raises(bnet.BnetRefusedError):
        bnet.urllib_send(bnet.Request("GET", url, {}))
    assert no_network == []


def test_an_unreachable_api_is_said_in_words_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Down:
        def open(self, *a: object, **k: object) -> None:
            raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(bnet, "_opener", Down)
    with pytest.raises(bnet.BnetUnavailableError, match="timed out"):
        bnet.urllib_send(bnet.Request("GET", f"{API}{CHAR}", {"Authorization": f"Bearer {TOKEN}"}))


def test_an_http_error_becomes_its_status_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    class NotFound:
        def open(self, *a: object, **k: object) -> None:
            raise urllib.error.HTTPError(f"{API}{CHAR}", 404, "Not Found", Message(), None)  # type: ignore[arg-type]

    monkeypatch.setattr(bnet, "_opener", NotFound)
    assert bnet.urllib_send(bnet.Request("GET", f"{API}{CHAR}", {})) == bnet.Reply(404, b"")


def test_a_large_answer_is_cut_and_flagged_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Big:
        status = 200

        def read(self, n: int) -> bytes:
            return b"x" * n

        def __enter__(self) -> Big:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    class Opens:
        def open(self, *a: object, **k: object) -> Big:
            return Big()

    monkeypatch.setattr(bnet, "_opener", Opens)
    got = bnet.urllib_send(bnet.Request("GET", f"{API}{CHAR}", {}))
    assert got.too_large and len(got.body) == bnet.MAX_REPLY_BYTES


def test_the_module_is_the_only_new_network_client() -> None:
    """crewdb and crewbook stay network-free (their own tests check them);
    `bnet` names no host but Blizzard's."""
    source = Path(bnet.__file__).read_text(encoding="utf-8")
    hosts = set(re_hosts(source))
    assert hosts <= {
        "oauth.battle.net",
        "oauth.battlenet.com.cn",
        "us.api.blizzard.com",
        "eu.api.blizzard.com",
        "kr.api.blizzard.com",
        "tw.api.blizzard.com",
        "gateway.battlenet.com.cn",
        "render.worldofwarcraft.com",
        "develop.battle.net",
    }, hosts


def re_hosts(text: str) -> Iterator[str]:
    import re

    for m in re.finditer(r'"([a-z0-9.-]+\.(?:net|com|cn))"', text):
        yield m.group(1)
