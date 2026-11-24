"""`links`' talent calculator link (M21-07): WoW Forever Builds' `/talents/`.

The two `link_sites.toml` examples (the owner's warlock record, with and
without a race the site names) are rebuilt byte for byte by
`test_links.py`; here are the class and race words, the optional race pair,
the talent code check (the site's own pattern, read from its script on
2026-11-24) and the file checks. Hostile codes and ids are labelled
`constructed` (L8). No network.
"""

from __future__ import annotations

from importlib import resources

import pytest

from wowlab_core import links

FOREVER = "1.60.1.70888"  # a launch client's version (the macos-70888 fixture's)
SITES = links.load_link_sites()
WARLOCK, UNDEAD, GOBLIN = 9, 5, 9  # ChrClasses and ChrRaces ids (tests may name them)


def _shipped() -> str:
    return resources.files("wowlab_core").joinpath(links.SITES_FILE).read_text(encoding="utf-8")


def _url(made: links.Link | links.NoLink) -> str:
    assert isinstance(made, links.Link), made
    return made.url


def _why(made: links.Link | links.NoLink) -> str:
    assert isinstance(made, links.NoLink), made
    assert made.why and "[verify]" not in made.why
    return made.why


def test_the_site_is_on_the_allowlist() -> None:
    assert links.origins()["wowforeverbuilds"] == "https://wowforeverbuilds.com"
    assert SITES.links["talents"].kind == "talents"


def test_every_class_and_race_word_is_the_sites() -> None:
    assert SITES.talents is not None
    assert set(SITES.talents.classes.values()) == {
        "warrior", "paladin", "hunter", "rogue", "priest", "shaman", "mage", "warlock", "druid",
    }  # fmt: skip
    assert SITES.talents.races["4"] == "night-elf" and SITES.talents.races["5"] == "undead"
    assert SITES.talents.races["95"] == "skyborne-alliance"
    assert SITES.talents.races["96"] == "skyborne-horde"


def test_the_address_carries_class_code_and_race() -> None:
    made = links.talents(WARLOCK, UNDEAD, "05-3-1", version=FOREVER)
    assert _url(made) == "https://wowforeverbuilds.com/talents/warlock?b=05-3-1&race=undead"
    assert isinstance(made, links.Link) and made.label == "Open my talents"


@pytest.mark.parametrize("race", [None, GOBLIN, 0, -1, 10**12], ids=lambda r: f"constructed: {r!r}")
def test_a_race_the_site_does_not_name_is_left_out_constructed(race: int | None) -> None:
    made = links.talents(WARLOCK, race, "022", version=FOREVER)
    assert _url(made) == "https://wowforeverbuilds.com/talents/warlock?b=022"


@pytest.mark.parametrize("klass", [None, 6, 12, 0, True], ids=lambda c: f"constructed: {c!r}")
def test_a_class_the_site_does_not_name_gives_no_link_constructed(klass: int | None) -> None:
    why = _why(links.talents(klass, UNDEAD, "022", version=FOREVER))
    assert "class" in why


@pytest.mark.parametrize(
    "code",
    [
        "0" * 41,
        "1-2-3-4",
        "022&race=human",
        "022#x",
        "a",
        "1 2",
        "../",
        "%30",
        "-".join(["0"] * 4),
        chr(0x661),  # an Arabic-Indic digit one, which `\d` would take
        None,
    ],
    ids=lambda c: f"constructed: {c!r}",
)
def test_a_code_the_site_refuses_gives_no_link_constructed(code: str | None) -> None:
    why = _why(links.talents(WARLOCK, UNDEAD, code, version=FOREVER))
    assert "talent code" in why


def test_the_longest_code_the_site_takes_is_built() -> None:
    code = "-".join(["5" * 40] * 3)
    assert _url(links.talents(WARLOCK, UNDEAD, code, version=FOREVER)).endswith(
        f"?b={code}&race=undead"
    )


def test_another_game_version_gets_no_link() -> None:
    why = _why(links.talents(WARLOCK, UNDEAD, "022", version="11.0.2.56000"))
    assert "no pages wowlab knows" in why


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # An optional placeholder that is not one query pair after the first.
        (
            'rooms = { forever = "/talents/{class}?b={ranks}&race={race}" }',
            'rooms = { forever = "/talents/{class}?race={race}&b={ranks}" }',
        ),
        (
            'rooms = { forever = "/talents/{class}?b={ranks}&race={race}" }',
            'rooms = { forever = "/talents/{race}/{class}?b={ranks}&race={race}" }',
        ),
        ('optional = ["race"]', 'optional = ["class"]'),
        ('optional = ["race"]', 'optional = ["guild"]'),
        # Words a URL parser would rewrite, or not a game id.
        ('9 = "warlock"', '9 = "War Lock"'),
        ('5 = "undead"', '5 = "un/dead"'),
        ('5 = "undead"', '05 = "undead"'),
        # A code check that is not a regular expression.
        ("ranks = '[0-9]{0,40}(?:-[0-9]{0,40}){0,2}'", "ranks = '[0-9'"),
    ],
    ids=lambda v: f"constructed: {v[:40]!r}",
)
def test_a_damaged_talents_entry_is_refused_constructed(old: str, new: str) -> None:
    text = _shipped()
    assert old in text
    with pytest.raises(links.LinkSitesError):
        links.parse_link_sites(text.replace(old, new, 1))


def test_a_class_in_an_address_needs_the_talents_table_constructed() -> None:
    text = _shipped()
    start = text.index("[talents]\n")
    end = text.index("[pasted.", start)
    with pytest.raises(links.LinkSitesError, match="no \\[talents\\]"):
        links.parse_link_sites(text[:start] + text[end:])
