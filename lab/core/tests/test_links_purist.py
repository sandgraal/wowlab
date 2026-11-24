"""`links.purist_look`: a character's look on Purist (M21-06).

Graded on the `[look]` example of the shipped `link_sites.toml`, built from
the owner's Sandgraal Rizzen in the `macos-70888` fixture (its identity,
barber-shop record and gear, read here through the real record reader as
well), rebuilt byte for byte; and on constructed records (labelled
`constructed`, L8) for the boundaries: no customization or gear, hostile
names, values that are not ids. Each gives a safe address or no link with a
reason, never a malformed address. Purist's format was read from its
`/lib/look.js` on 2026-11-24 (the file's comments cite each line); whether
Purist opens the example is the owner's check (M21-O2). No network.
"""

from __future__ import annotations

import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gear, install, labaddon, links

FOREVER = "1.60.1.70888"  # the macos-70888 fixture's client
SITES = links.load_link_sites()
EXAMPLE = SITES.look.example[0]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECORD = (
    FIXTURES
    / "macos-70888"
    / "forever"
    / "WTF"
    / "Account"
    / "LABACCOUNTA"
    / "1"
    / "Sandgraal-Rizzen"
    / "SavedVariables"
    / "WowLab.lua"
)
# What a hash may hold, as the page's URL parser leaves it.
_FRAGMENT = re.compile(r"[A-Za-z0-9|.:,%]*")


def _look(example: links.LookExample) -> links.LookLink | links.NoLink:
    return links.purist_look(
        example.name,
        example.race_id,
        example.sex,
        example.class_id,
        [(o, c) for o, c in example.choices],
        [(s, i) for s, i in example.items],
        version=example.version,
    )


def _hash(url: str) -> list[str]:
    parts = urllib.parse.urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "purist.pages.dev" and parts.path == "/"
    assert _FRAGMENT.fullmatch(parts.fragment), parts.fragment
    return parts.fragment.split("|")


# ─── the shipped example ─────────────────────────────────────────────────────


@pytest.mark.parametrize("example", SITES.look.example, ids=lambda e: e.address)
def test_every_look_example_is_rebuilt_byte_for_byte(example: links.LookExample) -> None:
    made = _look(example)
    assert isinstance(made, links.LookLink), made
    assert made.url == example.address and made.left_out == []
    assert made.site == "purist" and made.site_name == "Purist"


def test_the_example_is_the_owners_record_as_the_reader_gives_it() -> None:
    """The example's values are the macos-70888 record's: identity, the
    barber-shop pairs in the record's order, and the gear's slots."""
    record = labaddon.read_char(RECORD, root=FIXTURES / "macos-70888")
    identity = labaddon.identity_of(record)
    assert (identity.race_id, identity.sex, identity.class_id) == (
        EXAMPLE.race_id,
        EXAMPLE.sex,
        EXAMPLE.class_id,
    )
    made = labaddon.customization_import(record)
    assert [[o, c] for o, c in made.choices.items()] == EXAMPLE.choices
    assert (made.race_id, made.body_type) == (identity.race_id, identity.sex)
    assert isinstance(record.gear, labaddon.Gear)
    filled, _ = gear.rows(record.gear, gear.shipped_slot_table())
    assert [[s.slot, s.item_id] for s in filled] == EXAMPLE.items


def test_the_hash_is_purists_format_two() -> None:
    fields = _hash(EXAMPLE.address)
    assert fields[0] == "2" and len(fields) == 6
    assert urllib.parse.unquote(fields[1]) == "Sandgraal Rizzen"
    # race 5 (Undead), sex 0, classic models, sheathed, class 9 (Warlock), Forever
    assert fields[2] == "5.0.1.1.9.forever" and fields[3] == "0.0.0"
    pairs = [tuple(map(int, p.split(":"))) for p in fields[4].split(",")]
    assert pairs == sorted(pairs) and len(pairs) == len(EXAMPLE.choices)
    # The neck, rings and trinkets have no look; the rest by Purist's display
    # slot: ranged (15) before the back (16), the hands (21, 22) last.
    assert fields[5].split(",")[-4:] == ["11287", "2580", "2632", "15970"]


def test_the_purist_origin_is_on_the_allowlist() -> None:
    assert links.origins()["purist"] == "https://purist.pages.dev"


def test_the_report_carries_the_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`char gear --json` carries the address the example rebuilds, read
    from a copy of the fixture's install (`_forever_`, its own install
    root) through the real CLI, the user data directory redirected."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos-70888" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70888" / "forever", root / "_forever_")
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    done = CliRunner().invoke(cli.app, ["char", "gear", "--json"])
    assert done.exit_code == 0, (done.stdout, done.stderr, done.exception)
    report = cli.CharGearReport.model_validate_json(done.stdout)
    assert isinstance(report.purist, links.LookLink)
    assert report.purist.url == EXAMPLE.address and report.purist.left_out == []


# ─── constructed records ─────────────────────────────────────────────────────


def test_no_customization_and_no_gear_give_empty_parts_constructed() -> None:
    """Purist reads an empty customization or item part as none (look.js
    lines 76 and 81), so the link is drawn with those parts empty."""
    made = links.purist_look("Tay Soulrend", 7, 1, 4, [], [], version=FOREVER)
    assert isinstance(made, links.LookLink)
    assert made.url == "https://purist.pages.dev/#2|Tay%20Soulrend|7.1.1.1.4.forever|0.0.0||"


def test_jewellery_has_no_place_and_a_slot_keeps_its_first_item_constructed() -> None:
    items: list[tuple[int, int | None]] = [(2, 100), (11, 101), (13, 102), (5, 2578), (5, 999)]
    made = links.purist_look("Tay Soulrend", 7, 1, 4, [], items, version=FOREVER)
    assert isinstance(made, links.LookLink) and made.url.endswith("||2578")


def test_an_item_without_an_id_is_left_out_and_said_constructed() -> None:
    made = links.purist_look("Tay Soulrend", 7, 1, 4, [], [(5, None), (7, 4309)], version=FOREVER)
    assert isinstance(made, links.LookLink) and made.url.endswith("||4309")
    assert made.left_out == ["the item in slot 5: its link has no item id"]


def test_what_is_left_out_names_the_slot_constructed() -> None:
    """As the dressing room says it: the slot's number and its name."""
    made = links.purist_look(
        "Tay Soulrend",
        7,
        1,
        4,
        [],
        [(5, None), (7, -3)],
        version=FOREVER,
        slot_names={5: "Chest", 7: "Legs"},
    )
    assert isinstance(made, links.LookLink) and made.url.endswith("||")
    assert made.left_out == [
        "the item in slot 5 Chest: its link has no item id",
        "the item in slot 7 Legs: its id is not one the game uses",
    ]


@pytest.mark.parametrize(
    ("choices", "items"),
    [
        ([(58,)], [(5, 2578)]),
        ([(58, 918, 1)], [(5, 2578)]),
        ([58], [(5, 2578)]),
        (None, [(5, 2578)]),
        ("58:918", [(5, 2578)]),
        ([(58, "918")], [(5, 2578)]),
        ([(58, 918)], [(5,)]),
        ([(58, 918)], None),
        ([(58, 918)], [5, 2578]),
        ([(58, 918)], [(5, 2578.0)]),
        ([(58, 918)], [(5, 1)] * 300),
    ],
    ids=lambda v: f"constructed: {v!r}"[:60],
)
def test_arguments_that_are_not_pairs_never_raise_constructed(choices: Any, items: Any) -> None:
    made = links.purist_look("Tay Soulrend", 7, 1, 4, choices, items, version=FOREVER)
    assert isinstance(made, links.LookLink)
    assert made.left_out and _hash(made.url)[2] == "7.1.1.1.4.forever"


@pytest.mark.parametrize(
    "value",
    [None, 5, "x", [None], [(1, 2)], {"x": 1}],
    ids=lambda v: f"constructed: {v!r}",
)
def test_slot_names_and_left_out_that_are_not_text_never_raise_constructed(value: Any) -> None:
    made = links.purist_look(
        "Tay Soulrend",
        7,
        1,
        4,
        [],
        [(5, None)],
        version=FOREVER,
        slot_names=value,
        left_out=value,
    )
    assert isinstance(made, links.LookLink)
    assert made.left_out[-1] == "the item in slot 5: its link has no item id"


def test_the_owners_bookmark_prefix_is_rebuilt() -> None:
    """The part of the owner's bookmark the ticket quotes (`#2|Nymbil%20
    Stinkfinger|7.0.1.1.4.forever|0.0.0|9430:78207,…|49,2108,…`), rebuilt
    from its first pair and first two items: a gnome (7) male (0) rogue (4),
    shirt (slot 4) then chest (slot 5)."""
    made = links.purist_look(
        "Nymbil Stinkfinger", 7, 0, 4, [(9430, 78207)], [(4, 49), (5, 2108)], version=FOREVER
    )
    assert isinstance(made, links.LookLink)
    assert made.url == (
        "https://purist.pages.dev/#2|Nymbil%20Stinkfinger|7.0.1.1.4.forever|0.0.0|9430:78207|49,2108"
    )


@pytest.mark.parametrize(
    "choices",
    [
        [(58, 918), (58, 919)],
        [(58, 0)],
        [(-1, 918)],
        [(58, 2**40)],
        [(True, 918)],
    ],
    ids=lambda c: f"constructed: {c!r}",
)
def test_choices_that_are_not_ids_leave_the_customization_out_constructed(
    choices: list[tuple[int, int]],
) -> None:
    made = links.purist_look("Tay Soulrend", 7, 1, 4, choices, [(5, 2578)], version=FOREVER)
    assert isinstance(made, links.LookLink)
    assert _hash(made.url)[4] == "" and _hash(made.url)[5] == "2578"
    assert len(made.left_out) == 1 and made.left_out[0].startswith("the face and hair")


def test_more_choices_than_a_barber_shop_has_are_left_out_constructed() -> None:
    many = [(n, n) for n in range(1, SITES.look.max_choices + 2)]
    made = links.purist_look("Tay Soulrend", 7, 1, 4, many, [], version=FOREVER)
    assert isinstance(made, links.LookLink) and _hash(made.url)[4] == ""


_HOSTILE = [
    "Tay|Soulrend",
    "Tay#Soulrend",
    "Tay%20Soulrend",
    "Tay/../x",
    "<b>Tay</b>",
    'Tay"Soulrend',
    "Tay'Soulrend",
    "Tay-Soulrend",
    "Tay?x=1",
    "Tay" + chr(0x200F) + "Soulrend",  # an RTL mark
    "Tay\nSoulrend",
    " Tay",
    "Tay  Soulrend",
    "x" * 300,
    "",
]


@pytest.mark.parametrize("name", _HOSTILE, ids=lambda n: f"constructed: {n[:24]!r}")
def test_a_hostile_name_leaves_the_name_out_constructed(name: str) -> None:
    made = links.purist_look(name, 7, 1, 4, [(58, 918)], [(5, 2578)], version=FOREVER)
    assert isinstance(made, links.LookLink)
    fields = _hash(made.url)
    assert fields[1] == "" and fields[2:] == ["7.1.1.1.4.forever", "0.0.0", "58:918", "2578"]
    assert len(made.left_out) == 1 and made.left_out[0].startswith(
        "the character's name, because the name "
    )


def test_an_accented_name_is_percent_encoded_and_reads_back() -> None:
    made = links.purist_look("Zoë Brûlé", 7, 1, 4, [], [], version=FOREVER)
    assert isinstance(made, links.LookLink)
    assert urllib.parse.unquote(_hash(made.url)[1]) == "Zoë Brûlé"


def test_no_name_leaves_the_name_part_empty() -> None:
    made = links.purist_look(None, 7, 1, 4, [], [], version=FOREVER)
    assert isinstance(made, links.LookLink) and _hash(made.url)[1] == ""
    assert made.left_out == ["the character's name, because it is not known"]


def test_the_callers_left_out_comes_first() -> None:
    made = links.purist_look(
        "Tay Soulrend", 7, 1, 4, [], [], version=FOREVER, left_out=["the face and hair: x"]
    )
    assert isinstance(made, links.LookLink) and made.left_out == ["the face and hair: x"]


@pytest.mark.parametrize(
    ("race", "sex", "klass"),
    [
        (None, 0, 4),
        (7, None, 4),
        (7, 0, None),
        (0, 0, 4),
        (7, 2, 4),
        (7, 0, -1),
        (7, 0, 2**40),
        (7, True, 4),
    ],
    ids=lambda v: f"constructed: {v!r}",
)
def test_no_identity_gives_no_link_constructed(race: Any, sex: Any, klass: Any) -> None:
    made = links.purist_look("Tay Soulrend", race, sex, klass, [], [], version=FOREVER)
    assert isinstance(made, links.NoLink) and made.site == "purist"


@pytest.mark.parametrize("version", [None, "11.0.2.56000", "garbage"])
def test_a_version_with_no_room_gets_no_link(version: str | None) -> None:
    made = links.purist_look("Tay Soulrend", 7, 1, 4, [], [], version=version)
    assert isinstance(made, links.NoLink) and "game version" in made.why


def test_build_never_raises_with_an_unusable_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> links.LinkSites:
        raise links.LinkSitesError("constructed")

    monkeypatch.setattr(links, "load_link_sites", broken)
    made = links.purist_look("Tay Soulrend", 7, 1, 4, [], [], version=FOREVER)
    assert isinstance(made, links.NoLink) and "cannot be used" in made.why


# ─── the shipped file's [look] ───────────────────────────────────────────────


def _shipped() -> str:
    from importlib import resources

    return resources.files("wowlab_core").joinpath(links.SITES_FILE).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('site = "purist"\nlabel', 'site = "nowhere"\nlabel'),
        ('path = "/"\nformat', 'path = "/#x"\nformat'),
        ('path = "/"\nformat', 'path = "x"\nformat'),
        ('rooms = { forever = "forever" }\nclassic', 'rooms = { forever = "for|ever" }\nclassic'),
        ('pets = "0.0.0"', 'pets = "0|0"'),
        ('name = "purist_name"', 'name = "nope"'),
        ('"18" = 15,', '"18" = 16,'),
        ('origin = "https://purist.pages.dev"', 'origin = "http://purist.pages.dev"'),
        ("sheathed = 1\n", "sheathed = 3\n"),
    ],
    ids=lambda v: f"constructed: {v[:32]!r}",
)
def test_a_damaged_look_is_refused_constructed(old: str, new: str) -> None:
    text = _shipped()
    assert old in text
    with pytest.raises(links.LinkSitesError):
        links.parse_link_sites(text.replace(old, new, 1))


def test_a_look_without_an_example_is_refused_constructed() -> None:
    text = _shipped()
    start = text.index("[[look.example]]")
    end = text.index("# ─── addresses the player pastes")
    with pytest.raises(links.LinkSitesError):
        links.parse_link_sites(text[:start] + text[end:])
