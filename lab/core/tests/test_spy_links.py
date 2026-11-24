"""Profile links on the rows of `spy list` and `spy show` (M21-05).

The real Spy capture (`fixtures/macos-71037/forever/WTF/Account/`, M22-01)
in a copy of the captured install `fixtures/macos-70888/` (a Forever
client, `portal "US"`, whose account holds `1/<First>-<Second>` character
folders), through `test_cli_spy.py`'s fixture: Spy keys a Forever player
`<First>-<Second>` and saves no region, so a row's links use the region the
flavor folder's `Config.wtf` names. Tests named `constructed` change that
file, or the character folders' shape, to reach what the capture does not
hold. No test reads a real install, and no address is requested.
"""

# ruff: noqa: F811 - `env` is an imported fixture, requested by name

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _casc_build import tree_state
from test_cli_spy import FLAVOR, KEY, TYRONE, Env, _constructed_rows, env  # noqa: F401 - the fixture

from wowlab_core import cli, links

ARMORY = "https://worldofwarcraft.blizzard.com/en-us/forever/us/armory/character/"
JOURNEY = "https://wofwforever.com/en/tools/one-shot-manager/?region=us&"
SITES = ["armory_character", "journey", "raiderio"]
RULESET = "the character's ruleset (Normal, PvP, Roleplay or Roleplay PvP) is not known"


def _by_link(made: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    assert [m["link"] for m in made] == SITES
    return {m["link"]: m for m in made}


def test_every_row_of_the_capture_carries_its_links(env: Env) -> None:
    report = env.json("spy", "list")
    assert len(report["players"]) == 24
    for row in report["players"]:
        first, second = row["key"].split("-")
        assert (row["first_name"], row["second_name"], row["region"]) == (first, second, "us")
        got = _by_link(row["links"])
        assert got["raiderio"]["why"] == RULESET
        made = links.profiles(first, second, "us", version="1.60.1.70888")
        assert row["links"] == [m.model_dump(mode="json") for m in made], row["key"]
        assert got["armory_character"]["url"].startswith(ARMORY)
    nefzik = _by_link(report["players"][0]["links"])
    assert report["players"][0]["key"] == "Nefzik-Spoonz"
    assert nefzik["armory_character"]["url"] == ARMORY + "nefzik-spoonz"
    assert nefzik["journey"]["url"] == JOURNEY + "name=Nefzik&surname=Spoonz"
    # A second name with an accent, as the capture holds one: lower case
    # and percent-escaped for the armory, as typed for the form.
    tala = _by_link(next(p for p in report["players"] if p["key"] == "Tala-Kattéa")["links"])
    assert tala["armory_character"]["url"] == ARMORY + "tala-katt%C3%A9a"
    assert tala["journey"]["url"] == JOURNEY + "name=Tala&surname=Katt%C3%A9a"
    assert cli.SpyListReport.model_validate(report).players[0].links[0].link == "armory_character"


def test_show_carries_the_links_of_each_row(env: Env) -> None:
    report = env.json("spy", "show", "Nefzik-Spoonz")
    (row,) = report["players"]
    got = _by_link(row["links"])
    assert got["armory_character"]["url"] == ARMORY + "nefzik-spoonz"
    assert not any("https://" in line for line in report["lines"])


def test_constructed_no_region_gives_no_link_and_says_why(env: Env) -> None:
    config = env.root / FLAVOR / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes().replace(b'SET portal "US"', b'SET portal "test"'))
    env.before = tree_state(env.root)  # the test's own change, made before any run
    row = env.json("spy", "list")["players"][0]
    assert row["region"] is None
    got = _by_link(row["links"])
    assert got["armory_character"]["why"] == "the character's region is not known"
    assert "url" not in got["journey"]


def test_constructed_a_key_whose_second_part_is_a_realm_gets_no_armory_link(env: Env) -> None:
    """The account's character folders moved to the retail shape
    (`<Realm>/<Name>/`, constructed), and a Spy file put under it: the
    flavor has no second names, so a key's second part is a realm."""
    account = env.root / FLAVOR / "WTF" / "Account" / "LABACCOUNTA"
    for name in [p.name for p in (account / "1").iterdir()]:
        (account / "Somerealm").mkdir(exist_ok=True)
        (account / "1" / name).rename(account / "Somerealm" / name.split("-")[0])
    (account / "1").rmdir()
    env.put(TYRONE.replace("1/Tyrone-Hamberglar", "Somerealm/Tyrone"), _constructed_rows())
    rows = {p["key"]: p for p in env.json("spy", "list")["players"]}
    row = rows[KEY]
    assert (row["first_name"], row["second_name"]) == (KEY.split("-")[0], None)
    got = _by_link(row["links"])
    assert got["armory_character"]["why"] == "the second name is not known"
