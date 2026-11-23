"""`simexport` (M21-08): one character as the JSON WoWSims' Addon Import reads.

Graded on the real lab-addon record of `macos-70888` (Sandgraal-Rizzen,
schema 4, an Undead warlock at level 13 with two talents ranked) against
the `TraitNodeEntry` table recorded for its build (1.60.1.70888), and on
the two `macos-70450` records for the gear and identity. The expected
export, `snapshots/sim_export/macos-70888-Sandgraal-Rizzen.json`, is
wowlab's best reading of the format at the pinned wowsims/forever commit;
whether WoWSims imports it is the owner's check (M21-O2), so it is
**[verify]** until the owner confirms an import.

Tests labelled `constructed` change one value of a real record or slot
(a class the sim lacks, gems, more than one bonus id, a rank over 9) or
give hostile `--out` names and format files. Nothing reads or writes an
install; files are written only under a user data directory redirected
into `tmp_path`.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from wowlab_core import dressing, gear, labaddon, simexport

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots" / "sim_export"
RIZZEN = (
    FIXTURES
    / "macos-70888/forever/WTF/Account/LABACCOUNTA/1/Sandgraal-Rizzen/SavedVariables/WowLab.lua"
)
SETT = (
    FIXTURES
    / "macos-70450/forever/WTF/Account/90000001#6/1/Sangraal-Sett/SavedVariables/WowLab.lua"
)
PARTICIPLE = (
    FIXTURES
    / "macos-70450/forever/WTF/Account/90000001#6/1/Dangling-Participle/SavedVariables/WowLab.lua"
)
SCHEMA_1 = (
    FIXTURES / "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
)
TABLE_70888 = FIXTURES / "wago" / "TraitNodeEntry.1.60.1.70888.csv"
BUILD_70888 = "1.60.1.70888"
COMMIT = "57df2a7a5a2bb085bfce15890a33d655bb7fd66f"


def _table(path: Path = TABLE_70888) -> dict[int, int]:
    with path.open(encoding="utf-8", newline="") as handle:
        return simexport.definitions(csv.DictReader(handle))


def _parts(path: Path) -> tuple[labaddon.CharDB, labaddon.CharIdentity, list[gear.GearSlot]]:
    record = labaddon.read_char(path)
    assert isinstance(record.gear, labaddon.Gear)
    slots, _ = gear.rows(record.gear, gear.shipped_slot_table())
    return record, labaddon.identity_of(record), slots


def _export(
    path: Path, table: dict[int, int] | None = None, build: str | None = BUILD_70888
) -> simexport.SimExport:
    record, identity, slots = _parts(path)
    klass = simexport.class_name(identity, simexport.load_format())
    talents = simexport.talents_of(
        simexport.purchased(record), klass, table, table_build=build if table else None
    )
    return simexport.build(identity, slots, level=dressing.level_of(record), talents=talents)


def _whats(sim: simexport.SimExport) -> list[str]:
    return [f"{x.what}: {x.why}" for x in sim.left_out]


# ─── the shipped format ──────────────────────────────────────────────────────


def test_the_shipped_format_names_the_pinned_commit() -> None:
    fmt = simexport.load_format()
    assert fmt.source.commit == COMMIT
    assert fmt.source.repository == "https://github.com/wowsims/forever"
    assert fmt.format.flavor == "forever"
    doc = simexport.__doc__ or ""
    assert COMMIT in doc
    text = (Path(simexport.__file__).parent / simexport.FORMAT_FILE).read_text(encoding="utf-8")
    assert "MIT License, Copyright (c) 2022 wowsims team" in text


def test_the_sim_slots_are_its_item_slot_order() -> None:
    """proto/common.proto's ItemSlot 0..16 as the client's slot numbers:
    every slot of `gear_slots.toml` but the shirt (4) and the tabard (19)."""
    fmt = simexport.load_format()
    names = gear.shipped_slot_table().slots
    assert [names[s] for s in fmt.format.slots] == [
        "Head", "Neck", "Shoulder", "Back", "Chest", "Wrist", "Hands", "Waist", "Legs", "Feet",
        "Finger 1", "Finger 2", "Trinket 1", "Trinket 2", "Main Hand", "Off Hand", "Ranged",
    ]  # fmt: skip
    assert set(names) - set(fmt.format.slots) == {4, 19}


def test_every_class_has_three_trees_and_the_races_are_the_sims() -> None:
    fmt = simexport.load_format()
    assert sorted(fmt.classes) == [1, 2, 3, 4, 5, 7, 8, 9, 11]
    assert all(len(fmt.talents[c].trees) == 3 for c in fmt.classes.values())
    assert set(fmt.races.values()) == {
        "Human", "Orc", "Dwarf", "Night Elf", "Undead", "Tauren", "Gnome", "Troll", "Blood Elf",
        "High Order Skyborne", "Windshaper Skyborne",
    }  # fmt: skip


def test_the_race_ids_are_the_recorded_chr_races_rows() -> None:
    """Each race id names the ChrRaces row whose `Name_lang` is the sim's
    name, in the recorded 70058 table."""
    fmt = simexport.load_format()
    path = FIXTURES / "wago" / "ChrRaces.1.60.1.70058.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        names = {int(r["ID"]): r["Name_lang"] for r in csv.DictReader(handle)}
    assert {i: names[i] for i in fmt.races} == fmt.races


def test_every_ranked_talent_of_the_records_is_in_the_trees() -> None:
    """The definitions the committed records rank, through the recorded
    table, are each at one place in the sim's trees of their class."""
    fmt = simexport.load_format()
    table = _table()
    for path in (RIZZEN, SETT, PARTICIPLE):
        record, identity, _ = _parts(path)
        ranks = simexport.purchased(record)
        assert isinstance(ranks, list) and ranks
        trees = fmt.talents[simexport.class_name(identity, fmt)].trees
        for entry, _rank in ranks:
            assert sum(tree.count(table[entry]) for tree in trees) == 1


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("[source]\n", "must be a table"),
        ("x = [", "TOMLDecodeError"),
        ("a" * 70_000, "larger than"),
    ],
    ids=["constructed: no tables", "constructed: not TOML", "constructed: too long"],
)
def test_a_damaged_format_file_is_refused_constructed(text: str, words: str) -> None:
    with pytest.raises(simexport.FormatError, match=words):
        simexport.parse_format(text)


def _format_text() -> str:
    return (Path(simexport.__file__).parent / simexport.FORMAT_FILE).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f'commit = "{COMMIT}"', 'commit = "main"'),
        (f'commit = "{COMMIT}"', f'commit = "{COMMIT}x"'),
        ('flavor = "forever"', 'flavor = "forever\\n"'),
        ('"5" = "Undead"', '"5" = "<b>Undead</b>"'),
        ('"9" = "warlock"', '"9" = "Warlock"'),
        ('"9" = "warlock"', '"x9" = "warlock"'),
        ("slots = [1, 2, 3,", "slots = [1, 1, 3,"),
        ("[talents.warrior]", "[talents.monk]"),
    ],
    ids=lambda v: f"constructed: {v!r}",
)
def test_a_format_file_out_of_shape_is_refused_constructed(old: str, new: str) -> None:
    text = _format_text()
    assert text.count(old) == 1
    with pytest.raises(simexport.FormatError):
        simexport.parse_format(text.replace(old, new))


# ─── the export ──────────────────────────────────────────────────────────────


def test_the_recorded_export_of_a_real_character_verify() -> None:
    """[verify] until the owner imports it into WoWSims (M21-O2)."""
    sim = _export(RIZZEN, _table())
    expected = (SNAPSHOTS / "macos-70888-Sandgraal-Rizzen.json").read_text(encoding="ascii")
    assert sim.text + "\n" == expected
    assert json.loads(sim.text) == sim.export
    assert sim.talents_table_build == BUILD_70888
    assert sim.source_commit == COMMIT
    # The shirt is the one thing left out: the sim has no shirt slot.
    assert _whats(sim) == ["slot 4 Shirt: Brown Linen Shirt: the sim has no such slot"]


def test_the_export_reads_back_as_the_record_says() -> None:
    sim = _export(RIZZEN, _table())
    export = sim.export
    assert (export["class"], export["race"], export["level"]) == ("warlock", "Undead", 13)
    # Entries 130655 and 130654 are definitions 135456 and 135455: the
    # second and third talents of Affliction, rank 2 each.
    assert export["talents"] == "022"
    items = export["gear"]["items"]
    assert len(items) == 17
    _, _, slots = _parts(RIZZEN)
    by_slot = {s.slot: s for s in slots}
    for at, game in enumerate(simexport.load_format().format.slots):
        item = items[at]
        if game not in by_slot:
            assert item is None
            continue
        s = by_slot[game]
        assert item["id"] == s.item_id
        assert item.get("random_suffix") == (s.bonus_ids[0] if s.bonus_ids else None)
        assert "enchant" not in item and "gems" not in item


def test_without_the_table_the_talents_are_left_out_and_the_rest_stays() -> None:
    sim = _export(RIZZEN, None)
    assert "talents" not in sim.export
    assert "talents: the game table TraitNodeEntry is not cached" in _whats(sim)
    assert sim.export["gear"] == _export(RIZZEN, _table()).export["gear"]
    assert sim.talents_table_build is None


@pytest.mark.parametrize("path", [SETT, PARTICIPLE], ids=["Sangraal-Sett", "Dangling-Participle"])
def test_the_70450_records_export_their_identity_and_gear(path: Path) -> None:
    record, identity, slots = _parts(path)
    sim = _export(path, None)
    fmt = simexport.load_format()
    assert sim.export["class"] == fmt.classes[identity.class_id or 0]
    assert sim.export["race"] == fmt.races[identity.race_id or 0]
    assert sim.export["level"] == dressing.level_of(record)
    placed = [i["id"] for i in sim.export["gear"]["items"] if i is not None]
    kept = [s.item_id for s in slots if s.slot in fmt.format.slots]
    assert sorted(placed) == sorted(kept)
    assert sim.text == json.dumps(sim.export, separators=(",", ":"))


def test_a_record_without_identity_cannot_be_exported() -> None:
    """Schema 1: no class to give the import, which refuses a text without one."""
    record = labaddon.read_char(SCHEMA_1)
    identity = labaddon.identity_of(record)
    with pytest.raises(simexport.SimExportError, match="the sim needs the class"):
        simexport.class_name(identity, simexport.load_format())


def _identity(**changes: Any) -> labaddon.CharIdentity:
    _, identity, _ = _parts(RIZZEN)
    return identity.model_copy(update=changes)


def _talents(string: str | None = "", absent: str | None = None) -> simexport.Talents:
    return simexport.Talents(string=string, absent=absent, left_out=[], table_build=None)


def test_a_class_the_sim_lacks_is_refused_constructed() -> None:
    with pytest.raises(simexport.SimExportError, match="class id 6 is not one of the sim's"):
        simexport.build(_identity(class_id=6), [], level=10, talents=_talents())


def test_the_class_file_stands_in_for_a_missing_class_id_constructed() -> None:
    identity = _identity(class_id=None, class_id_absent="constructed")
    sim = simexport.build(identity, [], level=10, talents=_talents())
    assert sim.export["class"] == "warlock"


def test_a_race_the_sim_lacks_is_left_out_constructed() -> None:
    sim = simexport.build(_identity(race_id=22), [], level=None, talents=_talents(None, "x"))
    assert "race" not in sim.export and "level" not in sim.export
    assert _whats(sim) == [
        "race: race id 22 is not one of the sim's races",
        "level: the record holds no level",
        "talents: x",
    ]
    assert sim.export["gear"] == {"items": []}


def _slot(**changes: Any) -> gear.GearSlot:
    _, _, slots = _parts(RIZZEN)
    [boots] = [s for s in slots if s.slot == 8]
    return boots.model_copy(update=changes)


def test_gems_a_link_suffix_and_several_bonus_ids_are_listed_constructed() -> None:
    slot = _slot(gem_ids=[23, 24], suffix_id=-5, bonus_ids=[12720, 99], enchant_id=1843)
    sim = simexport.build(_identity(), [slot], level=13, talents=_talents())
    assert sim.export["gear"]["items"][9] == {"id": 6537, "enchant": 1843}
    what = "slot 8 Feet: Willow Boots of the Owl"
    assert _whats(sim) == [
        f"{what}: its 2 bonus ids (12720, 99): which one is its random suffix is not known, "
        "so none is given",
        f"{what}: its link's suffix field (-5): the sim's random suffixes are bonus lists, not "
        "this field",
        f"{what}: its gems (23, 24): the sim's items carry no gems",
    ]


def test_a_slot_with_no_item_id_is_listed_constructed() -> None:
    sim = simexport.build(_identity(), [_slot(item_id=None)], level=13, talents=_talents())
    assert sim.export["gear"]["items"] == []
    assert _whats(sim) == ["slot 8 Feet: Willow Boots of the Owl: its link holds no item id"]


def test_gear_absent_is_left_out_and_said_constructed() -> None:
    sim = simexport.build(_identity(), [], level=13, talents=_talents(), gear_absent="switched off")
    assert "gear" not in sim.export
    assert _whats(sim) == ["gear: switched off"]


# ─── the talent string ───────────────────────────────────────────────────────


def _string(ranks: list[tuple[int, int]], table: dict[int, int]) -> simexport.Talents:
    return simexport.talents_of(ranks, "warlock", table, table_build="b")


def test_trailing_zeros_and_dashes_are_trimmed_as_the_sim_writes_them_constructed() -> None:
    trees = simexport.load_format().talents["warlock"].trees
    first, last = trees[0][4], trees[2][-1]
    table = {1: first, 2: last}
    assert _string([(1, 3)], table).string == "00003"
    assert _string([(2, 1)], table).string == "--" + "0" * (len(trees[2]) - 1) + "1"
    assert _string([], table).string == ""


def test_an_unknown_entry_or_definition_and_a_rank_over_nine_are_listed_constructed() -> None:
    trees = simexport.load_format().talents["warlock"].trees
    table = {1: 999_999, 2: trees[0][0], 3: trees[0][1]}
    found = _string([(7, 1), (1, 2), (2, 10), (3, 3)], table)
    assert found.string == "03"
    assert [f"{x.what}: {x.why}" for x in found.left_out] == [
        "talent entry 7 (rank 1): not in the TraitNodeEntry table of b",
        "talent entry 1 (rank 2): its definition 999999 is not in the sim's warlock trees",
        "talent entry 2 (rank 10): the sim's talent string holds one digit",
    ]


def test_a_record_with_no_talent_record_says_so_constructed() -> None:
    record, _, _ = _parts(RIZZEN)
    ranks = simexport.purchased(record.model_copy(update={"talents": None}))
    assert ranks == f"the class talent record is {labaddon.NOT_IN_FILE}"
    found = simexport.talents_of(ranks, "warlock", _table(), table_build="b")
    assert found.string is None and found.absent == ranks and found.table_build is None


def test_definitions_skip_rows_that_are_not_ids_constructed() -> None:
    rows = [
        {"ID": "1", "TraitDefinitionID": "2"},
        {"ID": "x", "TraitDefinitionID": "2"},
        {"ID": "3", "TraitDefinitionID": "-1"},
        {"ID": "4"},
    ]
    assert simexport.definitions(rows) == {1: 2}


# ─── the file ────────────────────────────────────────────────────────────────


@pytest.fixture
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    return redirected


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "../x.json", "a/b.json", "a\\b.json", ".hidden", "x.", "a..b", "x" * 101,
     "C:x", "x y", "é.json", "x.json\n", "NUL", "con.json", "Com1.txt"],
    ids=lambda n: f"constructed: {n!r}",
)  # fmt: skip
def test_out_takes_only_a_plain_file_name_constructed(user_data: Path, name: str) -> None:
    with pytest.raises(simexport.OutError, match="plain file name"):
        simexport.out_path(name)
    with pytest.raises(simexport.OutError):
        simexport.write_export("{}", name)
    assert not user_data.exists()


def test_write_export_writes_under_the_user_data_directory_and_replaces(user_data: Path) -> None:
    path = simexport.write_export('{"a":1}', "rizzen.json")
    assert path == user_data / "sim-exports" / "rizzen.json"
    assert path.read_bytes() == b'{"a":1}\n'
    assert simexport.write_export('{"a":2}', "rizzen.json") == path
    assert path.read_bytes() == b'{"a":2}\n'
    assert sorted(p.name for p in path.parent.iterdir()) == ["rizzen.json"]
    if os.name == "posix":
        assert path.parent.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_link_at_the_file_is_refused_constructed(user_data: Path, tmp_path: Path) -> None:
    folder = user_data / "sim-exports"
    folder.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("keep", encoding="utf-8")
    try:
        (folder / "x.json").symlink_to(elsewhere)
    except OSError:
        pytest.skip("symlinks not permitted here")
    with pytest.raises(simexport.OutError, match="not a plain file"):
        simexport.write_export("{}", "x.json")
    assert elsewhere.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_linked_folder_is_refused_constructed(user_data: Path, tmp_path: Path) -> None:
    user_data.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (user_data / "sim-exports").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted here")
    with pytest.raises(simexport.OutError, match="is a link"):
        simexport.write_export("{}", "x.json")
    assert list(elsewhere.iterdir()) == []


def test_a_user_data_directory_inside_an_install_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_root = tmp_path / "World of Warcraft"
    install_root.mkdir()
    (install_root / ".build.info").write_text("constructed", encoding="utf-8")
    inside = install_root / "data" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: inside)
    with pytest.raises(simexport.OutError, match="inside a game install"):
        simexport.write_export("{}", "x.json")
    assert not (install_root / "data").exists()


def test_the_module_fetches_nothing() -> None:
    """No network client in the module: the format is shipped, the table cached."""
    source = Path(simexport.__file__).read_text(encoding="utf-8")
    for word in ("httpx", "urllib", "socket", "http.client", "requests"):
        assert not re.search(rf"\bimport {re.escape(word)}\b|\bfrom {re.escape(word)}\b", source)
