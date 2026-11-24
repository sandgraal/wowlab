"""`links`: one builder for every outside link (M21-03).

Graded on the recorded examples in the shipped `link_sites.toml` (each
address seen on the site, or marked [verify] where the site refused an
automated request; the file says which), rebuilt byte for byte, and on
constructed hostile names, ids, regions and pasted addresses (labelled
`constructed`, L8): each gives a safe address or no link with a reason,
never a malformed address. No network.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import pytest

from wowlab_core import dressing, links

FOREVER = "1.60.1.70888"  # a launch client's version (the macos-70888 fixture's)
SITES = links.load_link_sites()


def _rebuild(example: links.Example) -> str:
    given: dict[str, Any] = dict(example.given)
    if example.pasted is not None:
        pasted = links.check_pasted(str(given["text"]))
        assert pasted.shape == example.pasted
        return pasted.url
    assert example.link is not None
    version = given.pop("version")
    names = {k: given.pop(k) for k in list(given) if k in ("first", "second", "guild", "dungeon")}
    got = links.build(example.link, version=version, names=names, **given)
    assert isinstance(got, links.Link), got
    return got.url


# ─── the shipped file ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "example", SITES.example, ids=lambda e: f"{e.link or e.pasted}:{e.address}"
)
def test_every_example_is_rebuilt_byte_for_byte(example: links.Example) -> None:
    assert _rebuild(example) == example.address


def test_every_template_and_pasted_shape_has_an_example() -> None:
    shown = {e.link for e in SITES.example} | {e.pasted for e in SITES.example}
    assert set(SITES.links) | set(SITES.pasted) <= shown


def test_the_allowlist_is_every_site_and_holds_the_dressing_room() -> None:
    served = links.origins()
    assert served == {k: s.origin for k, s in SITES.sites.items()}
    assert served["wowhead"] == dressing.load_sites().site.origin
    for origin in served.values():
        parts = urllib.parse.urlsplit(origin)
        assert parts.scheme == "https" and parts.path == "" and "@" not in origin


def test_the_room_comes_from_the_dressing_band_table() -> None:
    assert links.room_of(FOREVER) == dressing.room_for(dressing.load_sites(), FOREVER)
    assert links.room_of(None) is None and links.room_of("11.0.2.56000") is None


def test_the_profiles_are_the_armory_journey_and_raiderio_never_forevertwinking() -> None:
    made = links.profiles("Sandgraal", "Rizzen", "us", version=FOREVER, ruleset="pvp-rp")
    assert [m.link for m in made] == ["armory_character", "journey", "raiderio"]
    assert all(isinstance(m, links.Link) for m in made)
    assert all("forevertwinking" not in getattr(m, "url", "") for m in made)


# ─── no link, and why ────────────────────────────────────────────────────────


def _why(made: links.Link | links.NoLink) -> str:
    assert isinstance(made, links.NoLink), made
    assert made.why and "[verify]" not in made.why
    return made.why


def test_no_second_name_gives_no_armory_link() -> None:
    assert "second name" in _why(links.armory_character("Tay", None, "us", version=FOREVER))


def test_no_region_gives_no_armory_link() -> None:
    assert "region" in _why(links.armory_character("Tay", "Soulrend", None, version=FOREVER))


@pytest.mark.parametrize("build", ["armory_character", "journey", "raiderio"])
def test_cn_gets_no_profile_link(build: str) -> None:
    made = links.build(
        build,
        version=FOREVER,
        region="cn",
        ruleset="normal",
        names={"first": "Tay", "second": "Soulrend"},
    )
    assert "cn" in _why(made)


def test_a_version_with_no_room_gets_no_link() -> None:
    assert "game version" in _why(links.wowhead_item(6520, version="11.0.2.56000"))
    assert _why(links.wowhead_item(6520, version=None)) == "the game version is not known"


def test_raiderio_needs_the_ruleset() -> None:
    assert "ruleset" in _why(links.raiderio("Tay", "Soulrend", "us", None, version=FOREVER))
    assert "ruleset" in _why(links.raiderio("Tay", "Soulrend", "us", "chaos", version=FOREVER))


@pytest.mark.parametrize("item", [0, -1, 2**31, True, None], ids=lambda v: f"constructed: {v!r}")
def test_an_id_out_of_bounds_gets_no_link_constructed(item: Any) -> None:
    _why(links.wowhead_item(item, version=FOREVER))


# ─── hostile names (constructed) ─────────────────────────────────────────────

_HOSTILE = [
    'Tay"',
    "Tay'",
    "<script>",
    "Tay#x",
    "Tay?x=1",
    "Tay/../x",
    "..",
    "T" * 300,
    "Ta" + chr(0x200F) + "y",  # a right-to-left mark
    "Ta" + chr(0x202E) + "y",  # a right-to-left override
    "Tay\n",
    "Tay x",
    " Tay",
    "Tay-x",
    "T4y",
    "",
    "Ta" + chr(0x02BC) + "y",  # a modifier letter apostrophe
    "Ta%2Fy",
    "Ta\\y",
]


@pytest.mark.parametrize("name", _HOSTILE, ids=lambda n: f"constructed: {n[:24]!r}")
@pytest.mark.parametrize("build", ["armory_character", "journey", "raiderio"])
def test_a_hostile_name_gives_no_link_constructed(build: str, name: str) -> None:
    for names in ({"first": name, "second": "Soulrend"}, {"first": "Tay", "second": name}):
        made = links.build(build, version=FOREVER, region="us", ruleset="pvp", names=names)
        _why(made)


@pytest.mark.parametrize("name", _HOSTILE, ids=lambda n: f"constructed: {n[:24]!r}")
@pytest.mark.parametrize("build", ["armory_guild", "keystone_dungeon"])
def test_a_hostile_guild_or_dungeon_gives_a_safe_address_or_none_constructed(
    build: str, name: str
) -> None:
    key = "guild" if build == "armory_guild" else "dungeon"
    made = links.build(build, version=FOREVER, region="us", names={key: name})
    if isinstance(made, links.Link):
        # Only a name the rule allows reaches an address: letters, and the
        # spaces, apostrophes or hyphens it rewrites.
        tail = made.url.split("/")[-1]
        assert tail.replace("-", "").isalpha() and tail == tail.lower(), made.url
        return
    _why(made)


def test_an_accented_name_is_percent_encoded_and_round_trips() -> None:
    made = links.armory_character("Zoë", "Rizzen", "us", version=FOREVER)
    assert isinstance(made, links.Link)
    assert made.url.endswith("/zo%C3%AB-rizzen")
    assert urllib.parse.unquote(made.url.rsplit("/", 1)[1]) == "zoë-rizzen"
    journey = links.journey("Zoë", "Rizzen", "us", version=FOREVER)
    assert isinstance(journey, links.Link) and "name=Zo%C3%AB&" in journey.url


def test_a_decomposed_accent_is_normalised_first_constructed() -> None:
    made = links.armory_character("Zoë", "Rizzen", "us", version=FOREVER)
    assert isinstance(made, links.Link) and made.url.endswith("/zo%C3%AB-rizzen")


def test_a_guild_name_takes_spaces_and_drops_apostrophes() -> None:
    made = links.armory_guild("Kin'Raxis Order", "eu", version=FOREVER)
    assert isinstance(made, links.Link)
    assert made.url == (
        "https://worldofwarcraft.blizzard.com/en-gb/forever/eu/armory/guild/kinraxis-order"
    )
    assert "two in a row" in _why(links.armory_guild("Booty  Bay", "us", version=FOREVER))


# ─── pasted addresses ────────────────────────────────────────────────────────

_REFUSED_PASTES = [
    "http://forevertwinking.com/armory/13-sandgraal-rizzen",
    "https://forevertwinking.com/armory/sandgraal-rizzen",
    "https://forevertwinking.com/armory/13-sandgraal-rizzen/",
    "https://forevertwinking.com/armory/13-sandgraal-rizzen?x=1",
    "https://forevertwinking.com/armory/13-sandgraal-rizzen#top",
    "https://forevertwinking.com:443/armory/13-sandgraal-rizzen",
    "https://u:p@forevertwinking.com/armory/13-sandgraal-rizzen",
    "https://forevertwinking.com@evil.example/armory/13-sandgraal-rizzen",
    "https://forevertwinking.com.evil.example/armory/13-sandgraal-rizzen",
    "https://FOREVERTWINKING.COM/armory/13-sandgraal-rizzen",
    "https://forevertwinking.com/armory/13-../../x",
    "https://forevertwinking.com/armory/13-sandgraal%2Frizzen",
    "https://forevertwinking.com/builds/",
    "https://forevertwinking.com/builds/dwhe 4ngw2p",
    "https://forevertwinking.com/",
    "https://forever.warcraftlogs.com/reports/short",
    "https://forever.warcraftlogs.com/reports/aBcD1234eFgH5678/x",
    "https://www.warcraftlogs.com/reports/aBcD1234eFgH5678",
    "aBcD1234eFgH567",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "//forevertwinking.com/armory/13-sandgraal-rizzen",
    "https://forevertwinking.com/armory/13-sandgraal-rizzén",
    "https://forevertwinking.com/armory/13-sandgraal-rizzen" + chr(0x202E),
    "",
    "x" * 600,
]


@pytest.mark.parametrize("text", _REFUSED_PASTES, ids=lambda t: f"constructed: {t[:48]!r}")
def test_a_pasted_address_of_another_shape_is_refused_constructed(text: str) -> None:
    with pytest.raises(links.LinkError):
        links.check_pasted(text)


def test_a_pasted_address_is_trimmed_and_checked_against_the_named_site() -> None:
    ft = "https://forevertwinking.com/armory/13-sandgraal-rizzen"
    assert links.check_pasted(f"  {ft}\n").url == ft
    assert links.check_pasted(ft, "forevertwinking").kind == "profile"
    with pytest.raises(links.LinkError):
        links.check_pasted(ft, "warcraftlogs")
    with pytest.raises(links.LinkError, match="these sites only"):
        links.check_pasted(ft, "blizzard")


# ─── a damaged sites file (constructed) ──────────────────────────────────────


def _shipped() -> str:
    from importlib import resources

    return resources.files("wowlab_core").joinpath(links.SITES_FILE).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('origin = "https://www.wowhead.com"', 'origin = "http://www.wowhead.com"'),
        ('origin = "https://www.wowhead.com"', 'origin = "https://www.wowhead.com/x"'),
        ('origin = "https://keystone.guru"', 'origin = "https://u@keystone.guru"'),
        (
            'rooms = { forever = "/forever/item={id}" }',
            'rooms = { forever = "/forever/item=<{id}>" }',
        ),
        ('rooms = { forever = "/forever/item={id}" }', 'rooms = { forever = "/forever/item={x}" }'),
        ('rooms = { forever = "/forever/item={id}" }', 'rooms = { forever = "forever/item={id}" }'),
        (
            'site = "wowhead"\nlabel = "Wowhead"\nkind = "item"',
            'site = "nope"\nlabel = "W"\nkind = "item"',
        ),
        (
            'spaces = "-"\napostrophes = ""\nhyphens = "-"',
            'spaces = "#"\napostrophes = ""\nhyphens = "-"',
        ),
        ("path = '/builds/[A-Za-z0-9]{4,32}'", "path = '/builds/[A-Za-z0-9'"),
        ('link = "keystone_dungeon"', 'link = "nowhere"'),
    ],
    ids=lambda v: f"constructed: {v[:40]!r}",
)
def test_a_damaged_sites_file_is_refused_constructed(old: str, new: str) -> None:
    text = _shipped()
    assert old in text
    with pytest.raises(links.LinkSitesError):
        links.parse_link_sites(text.replace(old, new, 1))


def test_a_file_without_an_example_for_a_template_is_refused_constructed() -> None:
    text = _shipped()
    start = text.index('[[example]]\nlink = "wowhead_spell"')
    end = text.index("[[example]]", start + 1)
    with pytest.raises(links.LinkSitesError, match="no example"):
        links.parse_link_sites(text[:start] + text[end:])


def test_build_never_raises_with_an_unusable_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> links.LinkSites:
        raise links.LinkSitesError("constructed")

    monkeypatch.setattr(links, "load_link_sites", broken)
    made = links.armory_character("Tay", "Soulrend", "us", version=FOREVER)
    assert isinstance(made, links.NoLink) and "cannot be used" in made.why


@pytest.mark.parametrize(
    ("name", "parts"),
    [("Scarlet Monastery", 4), ("scarlet monastery", 4), ("Blackrock Spire", 2), ("Dire Maul", 3)],
)
def test_a_dungeon_in_parts_gets_no_single_link(name: str, parts: int) -> None:
    why = _why(links.keystone_dungeon(name, version=FOREVER))
    assert f"has {parts} maps there" in why and "no single link" in why


def test_a_dungeons_part_follows_the_rule() -> None:
    made = links.keystone_dungeon("Lower Blackrock Spire", version=FOREVER)
    assert isinstance(made, links.Link)
    assert made.url == "https://keystone.guru/explore/forever/lower-blackrock-spire"


def test_the_ruleset_reason_uses_the_games_words() -> None:
    why = _why(links.raiderio("Tay", "Soulrend", "us", None, version=FOREVER))
    assert "Normal, PvP, Roleplay or Roleplay PvP" in why


@pytest.mark.parametrize(
    "names",
    [
        '"Dire Maul" = ["Dire-Maul"]',
        '"Dire Maul" = []',
        '"Dire Maul" = ["a--b"]',
        '"Dire Maul" = ["dire-maul-east"]\n"dire maul" = ["dire-maul-west"]',
    ],
    ids=lambda v: f"constructed: {v[:30]!r}",
)
def test_a_damaged_name_table_is_refused_constructed(names: str) -> None:
    text = _shipped()
    old = '"Dire Maul" = ["dire-maul-east", "dire-maul-west", "dire-maul-north"]'
    assert old in text
    with pytest.raises(links.LinkSitesError):
        links.parse_link_sites(text.replace(old, names, 1))
