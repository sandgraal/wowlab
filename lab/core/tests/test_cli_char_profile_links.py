"""`char gear --json` and `char list --summary --json` carry the links out (M21-04).

On the Forever launch client's capture (`macos-70888/`, flavor folder
`_forever_` as its provenance row names it, one character
`1/Sandgraal-Rizzen`, region `US` in its Config.wtf) copied into
`tmp_path`, with the user data directory redirected there: the character's
armory page and Journey Companion lookup are the addresses
`link_sites.toml` records for this very character; Raider.IO is left out
while the ruleset is unknown; each filled slot's Wowhead page is built from
its item id. The capture's record is schema 4, which holds no guild, and its
own words say so.

Cases labelled `constructed`: the Config.wtf with its `portal` line taken out
(no region), a schema-5 record built from the real 70450 record with a guild
added (`test_m20_13_schema_5_views._v5`; the guild is the one on the owner's
recorded armory example), and hostile names given to the builder. Nothing
reads or writes a real install, and the capture's bytes are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from test_m20_13_schema_5_views import _v5
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon, links

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_forever_"  # macos-70888's provenance row; tests may name it (L6)
VERSION = "1.60.1.70888"  # the capture's client version (its .build.info)
ARMORY = "https://worldofwarcraft.blizzard.com/en-us/forever/us/armory/character/sandgraal-rizzen"
JOURNEY = (
    "https://wofwforever.com/en/tools/one-shot-manager/?region=us&name=Sandgraal&surname=Rizzen"
)
GUILD = "https://worldofwarcraft.blizzard.com/en-us/forever/us/armory/guild/booty-bay-cartel"

runner = CliRunner()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(
        platformdirs, "user_config_path", lambda *a, **k: tmp_path / "userconfig" / "wowlab"
    )
    wow = tmp_path / "World of Warcraft"
    wow.mkdir()
    shutil.copy2(FIXTURES / "macos-70888" / ".build.info", wow / ".build.info")
    shutil.copytree(FIXTURES / "macos-70888" / "forever", wow / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(wow))
    monkeypatch.chdir(tmp_path)
    return wow


def _state(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def ok(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _gear() -> dict[str, Any]:
    report: dict[str, Any] = json.loads(ok("char", "gear", "--json").stdout)
    return report


def test_gear_json_carries_the_profile_links(root: Path) -> None:
    before = _state(root)
    report = _gear()
    made = report["links"]
    assert [p["link"] for p in made["profiles"]] == ["armory_character", "journey"]
    assert [p["url"] for p in made["profiles"]] == [ARMORY, JOURNEY]
    assert [p["label"] for p in made["profiles"]] == ["Armory", "Journey Companion"]
    # Schema 4 holds no guild, and says so.
    assert made["guild"] is None and made["guild_link"] is None
    assert made["guild_absent"] == cli._NO_GUILD_FIELD
    # The links sit after the saved ones, before the flavor folder and the
    # dressing room, which stays last (M16-114).
    keys = list(report)
    assert keys.index("saved_links") < keys.index("links") < keys.index("item_links")
    assert keys[-2:] == ["flavor_folder", "dressing"]
    assert _state(root) == before


def test_gear_json_carries_each_slots_wowhead_page(root: Path) -> None:
    report = _gear()
    assert [i["slot"] for i in report["item_links"]] == [s["slot"] for s in report["slots"]]
    assert report["item_links"], "the capture has gear"
    for slot, item in zip(report["slots"], report["item_links"], strict=True):
        assert item["link"]["url"] == f"https://www.wowhead.com/forever/item={slot['item_id']}"
        assert item["link"]["site"] == "wowhead"


def test_gear_text_prints_the_addresses(root: Path) -> None:
    out = ok("char", "gear").stdout
    lines = out.splitlines()
    assert f"Armory: {ARMORY}" in lines
    assert f"Journey Companion: {JOURNEY}" in lines
    assert not any("Raider.IO" in line for line in lines)
    assert lines.index("Region: US") < lines.index(f"Armory: {ARMORY}")


def test_summary_rows_carry_the_profile_links(root: Path) -> None:
    report = json.loads(ok("char", "list", "--summary", "--json").stdout)
    (row,) = report["rows"]
    assert [p["url"] for p in row["links"]["profiles"]] == [ARMORY, JOURNEY]
    assert row["links"]["guild"] is None
    assert row["links"]["guild_absent"] == cli._NO_GUILD_FIELD


def test_no_region_gives_no_profile_link_and_says_why_constructed(root: Path) -> None:
    config = root / FLAVOR / "WTF" / "Config.wtf"
    text = config.read_text(encoding="utf-8")
    assert 'SET portal "US"\n' in text
    config.write_text(text.replace('SET portal "US"\n', ""), encoding="utf-8")
    report = _gear()
    assert report["region"] is None
    profiles = report["links"]["profiles"]
    assert [p["link"] for p in profiles] == ["armory_character", "journey"]
    assert all("url" not in p for p in profiles)
    assert {p["why"] for p in profiles} == {"the character's region is not known"}
    # Item pages need no region.
    assert all("url" in i["link"] for i in report["item_links"])
    lines = ok("char", "gear").stdout.splitlines()
    assert "Profile links: none (the character's region is not known)" in lines
    summary = json.loads(ok("char", "list", "--summary", "--json").stdout)
    assert {p["why"] for p in summary["rows"][0]["links"]["profiles"]} == {
        "the character's region is not known"
    }


# ─── the builder's use, on records and names made for the test ──────────────


def _with_guild(guild: str | None, absent: str | None = None) -> labaddon.CharDBV5:
    char = _v5()
    identity = char.identity
    assert isinstance(identity, labaddon.IdentityV5)
    changed = identity.model_copy(update={"guild": guild, "guild_absent": absent})
    return char.model_copy(update={"identity": changed})


def test_a_recorded_guild_links_to_its_armory_page_constructed() -> None:
    made = cli._profile_links(
        "Sandgraal", "Rizzen", "us", version=VERSION, record=_with_guild("Booty Bay Cartel")
    )
    assert made.guild == "Booty Bay Cartel" and made.guild_absent is None
    assert isinstance(made.guild_link, links.Link) and made.guild_link.url == GUILD


def test_a_guild_with_no_region_says_why_constructed() -> None:
    made = cli._profile_links(
        "Sandgraal", "Rizzen", None, version=VERSION, record=_with_guild("Booty Bay Cartel")
    )
    assert isinstance(made.guild_link, links.NoLink)
    assert made.guild_link.why == "the character's region is not known"


def test_no_guild_says_so_in_the_players_words_constructed() -> None:
    made = cli._profile_links(
        "Sandgraal",
        "Rizzen",
        "us",
        version=VERSION,
        record=_with_guild(None, labaddon.NO_GUILD_REASON),
    )
    assert made.guild is None and made.guild_link is None
    assert made.guild_absent == "no guild, or the guild was not loaded yet when the game saved"


def test_cn_gets_no_profile_link_constructed() -> None:
    made = cli._profile_links("Sandgraal", "Rizzen", "cn", version=VERSION, record=None)
    whys = [p.why for p in made.profiles if isinstance(p, links.NoLink)]
    assert whys == [
        "Armory has no pages for the cn region",
        "Journey Companion has no pages for the cn region",
    ]


def test_no_second_name_gets_no_profile_link_constructed() -> None:
    made = cli._profile_links("Sandgraal", None, "us", version=VERSION, record=None)
    assert [p.why for p in made.profiles if isinstance(p, links.NoLink)] == [
        "the second name is not known",
        "the second name is not known",
    ]


def test_a_client_with_no_room_gets_no_link_constructed() -> None:
    made = cli._profile_links("Sandgraal", "Rizzen", "us", version="1.13.2.30000", record=None)
    whys = [p.why for p in made.profiles if isinstance(p, links.NoLink)]
    assert len(whys) == len(made.profiles) == 2
    assert all("no pages wowlab knows for this game version" in why for why in whys)


@pytest.mark.parametrize(
    "first",
    ['Sand"graal', "Sand<graal", "Sand#graal", "Sand?graal", "Sand/graal", "..", "S" * 300],
    ids=lambda v: f"constructed: {v[:20]!r}",
)
def test_a_hostile_name_gives_no_link_constructed(first: str) -> None:
    made = cli._profile_links(first, "Rizzen", "us", version=VERSION, record=None)
    for p in made.profiles:
        assert isinstance(p, links.NoLink), p
        assert re.search(r"the first name ", p.why), p.why
    text = "\n".join(cli._profile_link_lines(made))
    assert text.startswith("Profile links: none (")
