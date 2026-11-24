"""Profile links on the crew rows (M21-05): `crew list`, `crew show` and
both sides of `crew matches` and `crew matches --world` carry each player's
profile links (the Blizzard armory, the Journey Companion, Raider.IO), each
an address or why there is none; the plain `crew show` prints them.

CONSTRUCTED INPUT, every byte, as in `test_crew_matches.py` and
`test_crew_log_names.py`: crew books from `parser/_crew_build.py`, combat
logs from `parser/_world_lines.py` (the real beta log's lines with an
invented player swapped in), inside a copy of the captured install
`fixtures/macos-70888/` (a Forever client, `portal "US"`, whose account
holds a `1/<First>-<Second>` character folder, so a crew key is
`<First>-<Second>`). The four-part unit name is the launch client's shape
(`fixtures/macos-71037/`, graded in `parser/test_worldpvp_launch_names.py`);
its region part `EU` is invented here to differ from the settings' `US`.
The user data directory is redirected into `tmp_path`; nothing reads or
writes a real install, and no address is requested.
"""

# ruff: noqa: F811 - `make` and `owner` are imported fixtures, requested by name

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from wowlab_core import links

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _casc_build import tree_state
from _crew_build import FLAVOR, KEY_A, KEY_B, KEY_C, KEY_D, book, match, player
from _world_lines import ALLY_C, FOE_A, ally_hits, hits_you, log, you_hit
from test_crew_world import _scan, _write_log, owner  # noqa: F401 - the fixture
from test_crewdb import Env, make  # noqa: F401 - the fixture

ARMORY = "https://worldofwarcraft.blizzard.com/en-us/forever/us/armory/character/"
JOURNEY = "https://wofwforever.com/en/tools/one-shot-manager/?region=us&"
RULESET = "the character's ruleset (normal, PvP, RP or RP PvP) is not known"
NO_SECOND = "the second name is not known"
NO_REGION = "the character's region is not known"
SITES = ["armory_character", "journey", "raiderio"]

# A launch-client player, the real log's four-part shape, with a region
# part other than the settings' (constructed name and region).
FOE_EU = ("Player-9998-0000000F", "Ayla-Moonfall-Roleplay PvP-EU")
AYLA = "Ayla-Moonfall"


def _json(env: Env, *args: str) -> dict[str, Any]:
    result = env.run(*args, "--json")
    assert result.exit_code == 0, result.output
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def _rows(env: Env) -> dict[str, dict[str, Any]]:
    return {p["key"]: p for p in _json(env, "crew", "list")["players"]}


def _by_link(made: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    assert [m["link"] for m in made] == SITES
    return {m["link"]: m for m in made}


def _second_name_links(made: list[dict[str, Any]], slug: str, first: str, second: str) -> None:
    """A Forever player in the US region: armory and Journey Companion
    links, and Raider.IO's reason (no ruleset is known yet)."""
    got = _by_link(made)
    assert got["armory_character"]["url"] == ARMORY + slug
    assert got["armory_character"]["site"] == "blizzard"
    assert got["journey"]["url"] == f"{JOURNEY}name={first}&surname={second}"
    assert "url" not in got["raiderio"] and got["raiderio"]["why"] == RULESET


def test_a_battleground_opponent_and_ally_carry_their_links_constructed(make: Any) -> None:
    """Both teams of a match: `crew list`, `crew show` and `crew matches`
    give each player the same links, built from the key's first and second
    name and the settings' region."""
    env = make(
        book(
            {KEY_A: player(), KEY_B: player(), KEY_C: player(), KEY_D: player()},
            schema=2,
            matches=[match([KEY_A, KEY_B], [KEY_C, KEY_D])],
        )
    )
    before = tree_state(env.root)
    rows = _rows(env)
    _second_name_links(rows[KEY_A]["links"], "inventeda-fakerealm", "Inventeda", "Fakerealm")
    _second_name_links(
        rows[KEY_C]["links"], "inventedc-otherfakerealm", "Inventedc", "Otherfakerealm"
    )
    shown = _json(env, "crew", "show", KEY_C)
    assert shown["player"]["links"] == rows[KEY_C]["links"]
    matches = _json(env, "crew", "matches")
    (game,) = matches["matches"]
    assert (game["team"], game["foes"]) == ([KEY_A, KEY_B], [KEY_C, KEY_D])
    profiles = matches["profiles"]
    assert [p["key"] for p in profiles] == [KEY_A, KEY_B, KEY_C, KEY_D]
    for p in profiles:
        assert p["links"] == rows[p["key"]]["links"]
        assert p["game_name"] == rows[p["key"]]["game_name"] and p["region"] == "us"
    # One player's matches name only the players in them.
    assert [p["key"] for p in _json(env, "crew", "matches", KEY_D)["profiles"]] == [
        KEY_A,
        KEY_B,
        KEY_C,
        KEY_D,
    ]
    assert tree_state(env.root) == before  # read-only (L1)


def test_plain_crew_show_prints_each_link_or_why_constructed(make: Any) -> None:
    env = make(book({KEY_A: player()}))
    out = env.run("crew", "show", KEY_A).stdout.splitlines()
    at = out.index("Profiles (addresses to open in your browser; wowlab itself requests nothing):")
    assert out[at + 1 : at + 4] == [
        f"  Armory: {ARMORY}inventeda-fakerealm",
        f"  Journey Companion: {JOURNEY}name=Inventeda&surname=Fakerealm",
        f"  Raider.IO: no link: {RULESET}",
    ]
    # The JSON's text lines are the panel's; the links are drawn apart.
    assert not any("https://" in line for line in _json(env, "crew", "show", KEY_A)["lines"])


def _world(env: Env) -> None:
    """You hit A (in the book) and the EU player (four parts); C (three
    parts, only the log names them) fights beside you; B hits you."""
    _write_log(
        env,
        log(
            you_hit(FOE_A, 10),
            you_hit(FOE_EU, 11),
            ally_hits(ALLY_C, FOE_A, 12),
            hits_you(("Player-9998-0000000B", "Inventedb-Fakerealm-"), 13),
        ),
    )
    assert _scan(env).exit_code == 0


def test_open_world_foes_with_and_without_a_region_part_constructed(make: Any, owner: Any) -> None:
    """A four-part unit name gives its own region (`eu`), which the links
    use; a three-part one (the beta's) gives a realm and no second name, so
    no armory or Journey Companion link, and says why."""
    env = make(book({KEY_A: player(met=2), KEY_B: player(met=1)}))
    _world(env)
    rows = _rows(env)
    ayla = rows[AYLA]
    assert (ayla["region"], ayla["region_from"]) == ("eu", "log")
    got = _by_link(ayla["links"])
    assert got["armory_character"]["url"] == (
        "https://worldofwarcraft.blizzard.com/en-gb/forever/eu/armory/character/ayla-moonfall"
    )
    assert got["journey"]["url"] == (
        "https://wofwforever.com/en/tools/one-shot-manager/?region=eu&name=Ayla&surname=Moonfall"
    )
    c = rows[KEY_C]
    assert (c["second_name"], c["realm"], c["region"]) == (None, "Otherfakerealm", "us")
    got = _by_link(c["links"])
    assert all("url" not in m for m in got.values())
    assert got["armory_character"]["why"] == NO_SECOND
    assert got["journey"]["why"] == NO_SECOND
    world = _json(env, "crew", "matches", "--world")
    (fight,) = world["encounters"]
    assert set(fight["foes"]) == {KEY_A, AYLA, KEY_B} and fight["allies"] == [KEY_C]
    profiles = {p["key"]: p for p in world["profiles"]}
    assert set(profiles) == {KEY_A, AYLA, KEY_B, KEY_C}  # foes and allies alike
    for key, p in profiles.items():
        assert p["links"] == rows[key]["links"], key
    assert profiles[AYLA]["region"] == "eu"


def test_a_player_with_a_realm_part_gets_no_armory_link_constructed(make: Any) -> None:
    """The capture's character folder moved to the retail shape
    (`<Realm>/<Name>/`, constructed): a key's second part is a realm, so
    there is no second name to build the armory address from."""
    env = make(book({KEY_A: player()}))
    account = env.root / FLAVOR / "WTF" / "Account" / "LABACCOUNTA"
    (account / "Somerealm").mkdir()
    (account / "1" / "Sandgraal-Rizzen").rename(account / "Somerealm" / "Sandgraal")
    (account / "1").rmdir()
    a = _rows(env)[KEY_A]
    assert (a["second_name"], a["realm"]) == (None, "Fakerealm")
    got = _by_link(a["links"])
    assert got["armory_character"] == {
        "link": "armory_character",
        "site": "blizzard",
        "site_name": "Armory",
        "label": "Armory",
        "why": NO_SECOND,
    }
    out = env.run("crew", "show", KEY_A).stdout.splitlines()
    assert f"  Armory: no link: {NO_SECOND}" in out


def test_no_region_gives_no_link_and_says_why_constructed(make: Any) -> None:
    env = make(book({KEY_A: player()}))
    config = env.root / FLAVOR / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes().replace(b'SET portal "US"', b'SET portal "test"'))
    got = _by_link(_rows(env)[KEY_A]["links"])
    assert got["armory_character"]["why"] == NO_REGION
    assert got["journey"]["why"] == NO_REGION


def test_a_hostile_key_gives_no_link_constructed(make: Any) -> None:
    """A key holding markup and a `/` (constructed) never reaches an
    address: each link says why there is none."""
    key = 'Inv<a href="x">-Fake/../realm'
    env = make(book({key: player()}))
    got = _by_link(_rows(env)[key]["links"])
    assert all("url" not in m for m in got.values())
    assert got["armory_character"]["why"].startswith("the first name holds")


def test_the_links_are_the_builders_own(make: Any) -> None:
    """The rows carry exactly what `links.profiles` builds for the flavor's
    game version, so the addresses have one source."""
    env = make(book({KEY_A: player()}))
    version = "1.60.1.70888"  # the capture's .build.info (tests may name it, L6)
    made = links.profiles("Inventeda", "Fakerealm", "us", version=version)
    assert _rows(env)[KEY_A]["links"] == [m.model_dump(mode="json") for m in made]
