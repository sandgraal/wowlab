"""`wowlab_core.dressing`: the dressing-room address (M15-02, §16.3).

The builder is graded on the derived fixture
`fixtures/macos-70450/derived/dressing-room.json`: two addresses the site's
own encoder made on 2026-10-19 for this run's two characters, and what the
site showed when it opened them. Each is rebuilt here from the real
schema-4 record it names (`macos-70450/`, M15-01) and must equal the
recorded address byte for byte.

Cases labelled `constructed` are hostile or boundary inputs at the
builder's typed input (L8): an item id of 0 or below, a thousand bonus ids,
a race id of 10^9, a class id the hash cannot hold, a character holding
only a weapon, broken copies of the sites file. Each must give the item-id
fallback, never a malformed address. Nothing here touches a network or an
install.
"""

from __future__ import annotations

import json
import random
import re
import time
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import dressing, gear, labaddon, ui

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DERIVED = FIXTURES / "macos-70450" / "derived" / "dressing-room.json"
SCHEMA_3 = FIXTURES / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme"
VERSION = "1.60.1.70450"  # the run's client version (fixture index); tests may name it (L6)
ADDRESS = re.compile(r"https://www\.wowhead\.com/forever/dressing-room#[0-9A-Za-z]+")


def _derived() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DERIVED.read_text(encoding="utf-8"))
    return data


EXAMPLES = _derived()["examples"]


def _record(path: str) -> labaddon.CharDB:
    return labaddon.read_char(FIXTURES / path)


def _slots(record: labaddon.CharDB) -> list[gear.GearSlot]:
    assert isinstance(record.gear, labaddon.Gear)
    filled, _ = gear.rows(record.gear, gear.shipped_slot_table())
    return filled


def _example_id(example: dict[str, Any]) -> str:
    return example["record"].split("/")[-3]


def _real() -> tuple[labaddon.CharIdentity, list[gear.GearSlot], int | None]:
    record = _record(EXAMPLES[1]["record"])
    return labaddon.identity_of(record), _slots(record), dressing.level_of(record)


# ─── the recorded examples ───────────────────────────────────────────────────


@pytest.mark.parametrize("example", EXAMPLES, ids=_example_id)
def test_the_builder_makes_the_recorded_address(example: dict[str, Any]) -> None:
    record = _record(example["record"])
    identity = labaddon.identity_of(record)
    slots = _slots(record)
    # The derived record's inputs are what the real record holds.
    assert (identity.race_id, identity.sex, identity.class_id) == (
        example["race_id"],
        example["gender"],
        example["class_id"],
    )
    assert dressing.level_of(record) == example["level"]
    by_slot = {s.slot: s for s in slots}
    for placed in example["slots"]:
        s = by_slot[placed["game"]]
        assert s.item_id == placed["item_id"]
        assert (s.bonus_ids[0] if s.bonus_ids else 0) == placed["bonus_id"]
        assert len(s.bonus_ids) <= 1
    placed_games = {p["game"] for p in example["slots"]}
    assert sorted(set(by_slot) - placed_games) == example["left_out_game_slots"]
    built = dressing.build(identity, slots, version=VERSION, level=dressing.level_of(record))
    assert isinstance(built, dressing.DressingLink)
    assert built.url == example["address"]
    assert built.site == "Wowhead"
    assert "[verify]" in built.note
    for game in example["left_out_game_slots"]:
        assert f"slot {game} " in built.note


def test_the_shipped_examples_are_the_derived_fixtures() -> None:
    sites = dressing.load_sites()
    assert [e.address for e in sites.example] == [e["address"] for e in EXAMPLES]
    for shipped in sites.example:
        assert (shipped.site, shipped.room, shipped.recorded) == (
            "Wowhead",
            "forever",
            "2026-10-19",
        )
        assert VERSION.startswith(shipped.client + ".")


@pytest.mark.parametrize("example", EXAMPLES, ids=_example_id)
def test_the_decoder_reads_the_recorded_address(example: dict[str, Any]) -> None:
    codec = dressing.load_sites().codec
    values = dressing.decode(codec, example["address"].split("#", 1)[1])
    assert values["version"] == 15
    assert (values["race"], values["gender"], values["class"], values["level"]) == (
        example["race_id"],
        example["gender"],
        example["class_id"],
        example["level"],
    )
    for room in range(1, 15):
        placed = [p for p in example["slots"] if p["room"] == room]
        item = placed[0]["item_id"] if placed else 0
        bonus = placed[0]["bonus_id"] if placed else 0
        assert (values[f"item.{room}"], values[f"bonus.{room}"]) == (item, bonus), room


def test_the_decoder_reads_the_sites_own_rewrite() -> None:
    """Once loaded, the room rewrote the first address with its default
    customization choices: the same character and items, choices set."""
    codec = dressing.load_sites().codec
    first = EXAMPLES[0]
    ours = dressing.decode(codec, first["address"].split("#", 1)[1])
    theirs = dressing.decode(codec, first["site_rewrote_to"].split("#", 1)[1])
    choices = {k: v for k, v in theirs.items() if k.startswith(("option#", "choice#"))}
    assert any(choices.values())
    assert {k: v for k, v in theirs.items() if k not in choices} == {
        k: v for k, v in ours.items() if k not in choices
    }


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0"), (1, "z"), (58, "6"), (59, "zz"), (3364, "60"), (6520, "z2L"), (195112, "600")],
)
def test_long_values_as_the_sites_encoder_writes_them(value: int, text: str) -> None:
    """A base-58 digit may equal 58 (3364 is "60"): the site's encoder
    divides only while the value is above 58."""
    codec = dressing.load_sites().codec
    assert dressing._long(codec, value) == text


def test_random_values_read_back_constructed() -> None:
    codec = dressing.load_sites().codec
    rng = random.Random(1502)
    for _ in range(300):
        values = {"version": 15, "race": rng.randint(1, 1000), "gender": rng.randint(0, 1)}
        values["class"] = rng.randint(1, 58)
        values["level"] = rng.randint(1, 1000)
        for room in rng.sample(range(1, 15), rng.randint(5, 14)):
            values[f"item.{room}"] = rng.randint(1, 2**31 - 1)
            values[f"bonus.{room}"] = rng.choice([0, rng.randint(1, 2**31 - 1)])
        text = dressing.encode(codec, values)
        assert re.fullmatch(r"[0-9A-Za-z]+", text)
        assert dressing.decode(codec, text) == dressing._meant(codec, values)


# ─── the fallback ────────────────────────────────────────────────────────────


def test_no_identity_gives_the_item_ids() -> None:
    """The schema-3 capture (macos-70425) has no identity record."""
    record = labaddon.read_char(SCHEMA_3 / "SavedVariables" / "WowLab.lua")
    slots = _slots(record)
    built = dressing.build(
        labaddon.identity_of(record), slots, version=VERSION, level=dressing.level_of(record)
    )
    assert isinstance(built, dressing.DressingItems)
    assert built.items == [str(s.item_id) for s in slots]
    assert built.note.startswith("No dressing-room link: the room needs the race")
    assert "schema 3" in built.note


@pytest.mark.parametrize(
    "version", ["1.15.7.61582", "11.2.5.63906", "2.0.0.1", "1.59.9.1", "", "x.y", None]
)
def test_an_unlisted_version_gives_the_item_ids_constructed(version: str | None) -> None:
    identity, slots, level = _real()
    built = dressing.build(identity, slots, version=version, level=level)
    assert isinstance(built, dressing.DressingItems)
    assert built.items == [str(s.item_id) for s in slots]
    assert "no dressing room is listed" in built.note


@pytest.mark.parametrize("version", ["1.60", "1.60.1.70450", "1.61.0.1", "1.99.9.99999"])
def test_the_band_covers_the_forever_versions(version: str) -> None:
    assert dressing.room_for(dressing.load_sites(), version) == "forever"


def test_no_placeable_item_gives_the_item_ids() -> None:
    """The real record's ring alone (game slot 11): the room has no ring slot."""
    identity, slots, level = _real()
    ring = [s for s in slots if s.slot == 11]
    assert len(ring) == 1
    built = dressing.build(identity, ring, version=VERSION, level=level)
    assert isinstance(built, dressing.DressingItems)
    assert built.items == ["5351"]
    assert "no equipped item goes in a slot the room has" in built.note


def _with(slots: list[gear.GearSlot], game: int, **change: Any) -> list[gear.GearSlot]:
    return [s.model_copy(update=change) if s.slot == game else s for s in slots]


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"item_id": 0}, "item id 0"),
        ({"item_id": -1}, "item id -1"),
        ({"item_id": 2**31}, "item id 2147483648"),
        ({"bonus_ids": list(range(1, 1001))}, "1000 bonus ids"),
        ({"bonus_ids": [-5]}, "a bonus id the room cannot take"),
        ({"bonus_ids": [0]}, "a bonus id the room cannot take"),
    ],
    ids=lambda v: f"constructed: {v}" if isinstance(v, str) else "",
)
def test_a_hostile_item_gives_the_item_ids_constructed(change: dict[str, Any], words: str) -> None:
    identity, slots, level = _real()
    built = dressing.build(identity, _with(slots, 5, **change), version=VERSION, level=level)
    assert isinstance(built, dressing.DressingItems), built
    assert words in built.note


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"race_id": 10**9}, "race id 1000000000"),
        ({"race_id": 0}, "race id 0"),
        ({"race_id": -2}, "race id -2"),
        ({"class_id": 0}, "class id 0"),
        ({"class_id": 59}, "cannot hold this character"),
        ({"class_id": 10**9}, "cannot hold this character"),
    ],
    ids=lambda v: f"constructed: {v}" if isinstance(v, str) else "",
)
def test_a_hostile_identity_gives_the_item_ids_constructed(
    change: dict[str, Any], words: str
) -> None:
    identity, slots, level = _real()
    built = dressing.build(identity.model_copy(update=change), slots, version=VERSION, level=level)
    assert isinstance(built, dressing.DressingItems), built
    assert words in built.note


def test_a_lone_weapon_is_more_than_the_hash_can_write_constructed() -> None:
    """Only the main hand (game slot 16): 122 empty fields in a row before
    it, more than one run of the site's codec holds (116), so no address."""
    identity, slots, level = _real()
    weapon = [s for s in slots if s.slot == 16]
    built = dressing.build(identity, weapon, version=VERSION, level=level)
    assert isinstance(built, dressing.DressingItems)
    assert "cannot hold this character" in built.note


def test_a_slot_listed_twice_gives_the_item_ids_constructed() -> None:
    identity, slots, level = _real()
    built = dressing.build(identity, slots + slots[:1], version=VERSION, level=level)
    assert isinstance(built, dressing.DressingItems)
    assert "listed twice" in built.note


def test_a_broken_shipped_file_gives_the_item_ids_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> dressing.Sites:
        raise dressing.SitesError("constructed: unreadable")

    monkeypatch.setattr(dressing, "load_sites", broken)
    identity, slots, level = _real()
    built = dressing.build(identity, slots, version=VERSION, level=level)
    assert isinstance(built, dressing.DressingItems)
    assert "cannot be used" in built.note


# ─── what is passed, and the note ────────────────────────────────────────────


def test_several_bonus_ids_pass_the_first_and_say_so_constructed() -> None:
    identity, slots, level = _real()
    built = dressing.build(
        identity, _with(slots, 5, bonus_ids=[12720, 4242]), version=VERSION, level=level
    )
    assert isinstance(built, dressing.DressingLink)
    assert "Slot 5 Chest carries 2 bonus ids and only the first, 12720, is passed" in built.note
    real = dressing.build(identity, slots, version=VERSION, level=level)
    assert isinstance(real, dressing.DressingLink)
    assert built.url == real.url


@pytest.mark.parametrize("level", [None, 0, 1001])
def test_no_level_gives_the_sites_default_constructed(level: int | None) -> None:
    identity, slots, _ = _real()
    built = dressing.build(identity, slots, version=VERSION, level=level)
    assert isinstance(built, dressing.DressingLink)
    values = dressing.decode(dressing.load_sites().codec, built.url.split("#", 1)[1])
    assert values["level"] == 100
    assert "level 100, the room's default" in built.note


def test_every_link_is_the_rooms_origin_and_a_plain_hash() -> None:
    identity, slots, level = _real()
    rng = random.Random(15)
    for _ in range(50):
        some = rng.sample(slots, rng.randint(1, len(slots)))
        built = dressing.build(identity, some, version=VERSION, level=level)
        if isinstance(built, dressing.DressingLink):
            assert ADDRESS.fullmatch(built.url), built.url


def test_the_address_holds_no_name() -> None:
    for example in EXAMPLES:
        record = _record(example["record"])
        built = dressing.build(
            labaddon.identity_of(record), _slots(record), version=VERSION, level=10
        )
        assert isinstance(built, dressing.DressingLink)
        for word in ("Dangling", "Participle", "Sangraal", "Sett", "90000001"):
            assert word not in built.url and word not in built.note


# ─── the shipped file ────────────────────────────────────────────────────────


def _shipped_text() -> str:
    return resources.files("wowlab_core").joinpath(dressing.SITES_FILE).read_text("utf-8")


def test_the_shipped_file_names_no_flavor_product_or_build() -> None:
    """L6: the room is chosen by the discovered version, never a constant."""
    text = _shipped_text()
    assert re.search(r"(?<![A-Za-z0-9])_[a-z]+(?:_[a-z]+)*_(?![A-Za-z0-9])", text) is None
    assert "wow_" not in text and "wowt" not in text
    for build in ("69876", "69913", "69977", "70009", "70058", "70425", "70450"):
        assert build not in text, build


def test_the_page_checks_the_shipped_origin() -> None:
    script = (Path(ui.__file__).resolve().parent / "templates" / "page.js").read_text("utf-8")
    origin = dressing.load_sites().site.origin
    assert script.count(f'const DRESSING_ORIGIN = "{origin}";') == 1


def _broken(edit: str, to: str) -> str:
    text = _shipped_text()
    assert text.count(edit) == 1, edit
    return text.replace(edit, to)


@pytest.mark.parametrize(
    ("edit", "to"),
    [
        ('origin = "https://www.wowhead.com"', 'origin = "http://www.wowhead.com"'),
        ('origin = "https://www.wowhead.com"', 'origin = "https://a.example/x"'),
        ('below = "2"\nroom = "forever"', 'below = "2"\nroom = "nowhere"'),
        ('below = "2"', 'below = "1.5"'),
        ("segments = 277", "segments = 276"),
        ("repeat = 50", "repeat = 49"),
        ("largest_value = 58", "largest_value = 61"),
        ('zero_run = "9"', 'zero_run = "8"'),
        (
            '"artifact_off", "|", "separate_shoulders",',
            '"artifact_off", "|", "separate_shoulders", "|",',
        ),
        ("[site]", "[site]\nextra = 1"),
    ],
    ids=lambda v: f"constructed: {v[:40]}",
)
def test_a_broken_sites_file_is_refused_constructed(edit: str, to: str) -> None:
    with pytest.raises(dressing.SitesError):
        dressing.parse_sites(_broken(edit, to))


# Damaged files that reached past the validation (security review of M15-02):
# each must be refused as `SitesError`, quickly, and so cannot escape `build`.
_DAMAGED = [
    ("band not an array", "[[band]]\nfrom", "[unused]\nfrom"),
    ("arrays 20,000 deep", "[site]", "deep = " + "[" * 20000 + "]" * 20000 + "\n[site]"),
    ("repeat 10**13", "repeat = 50", "repeat = 10000000000000"),
    ("repeat 10**8", "repeat = 50", "repeat = 100000000"),
    ("repeat 0", "repeat = 50", "repeat = 0"),
]


@pytest.mark.parametrize(
    ("edit", "to"),
    [case[1:] for case in _DAMAGED],
    ids=[f"constructed: {case[0]}" for case in _DAMAGED],
)
def test_a_damaged_sites_file_is_refused_quickly_constructed(
    edit: str, to: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _broken(edit, to)
    if to.startswith("[unused]"):
        text = "band = 5\n" + text  # a top-level key, before the first table
    assert len(text.encode("utf-8")) < 64 * 1024
    started = time.monotonic()
    with pytest.raises(dressing.SitesError):
        dressing.parse_sites(text)
    assert time.monotonic() - started < 2.0
    # And `build` turns the same damage into the fallback.
    monkeypatch.setattr(dressing, "load_sites", lambda: dressing.parse_sites(text))
    identity, slots, level = _real()
    built = dressing.build(identity, slots, version=VERSION, level=level)
    assert isinstance(built, dressing.DressingItems)
    assert "cannot be used" in built.note


def test_the_shipped_file_parses_and_lists_one_room_per_label() -> None:
    sites = dressing.parse_sites(_shipped_text())
    data = tomllib.loads(_shipped_text())
    assert set(sites.rooms) == set(data["rooms"])
    assert [b.room for b in sites.band] == ["forever"]
    assert sorted(s.room for s in sites.slot) == list(range(1, 15))
    assert {s.game for s in sites.slot}.isdisjoint({2, 11, 12, 13, 14})
