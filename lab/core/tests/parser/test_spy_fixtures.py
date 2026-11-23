"""`wowlab_core.spy` on the M22-01 capture of the Spy addon's files.

Three real `Spy.lua` files from the owner's Forever install (1.60.1.71037,
macOS), index rows in `fixtures/README.md`: the account file (`SpyDB`) and
two characters' (`SpyPerCharDB`, version "1.1"), one holding 24 players and
one empty. Names are kept as captured (ADR-0036, note of 2026-11-23). The
committed tree is never handed to a reader that opens files here: bytes are
read and parsed; `survey` is graded on a copy in `tmp_path`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from wowlab_core import layout, luadata, spy

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUN = "macos-71037"
ACCOUNT_DIR = f"{RUN}/forever/WTF/Account/LABACCOUNTA"
ACCOUNT = f"{ACCOUNT_DIR}/SavedVariables/Spy.lua"
SANDGRAAL = f"{ACCOUNT_DIR}/1/Sandgraal-Rizzen/SavedVariables/Spy.lua"
TYRONE = f"{ACCOUNT_DIR}/1/Tyrone-Hamberglar/SavedVariables/Spy.lua"
FILES = [ACCOUNT, SANDGRAAL, TYRONE]


def _bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _source(name: str, character: str | None) -> spy.SpySource:
    return spy.SpySource(file=name, account="LABACCOUNTA", character=character)


def _sandgraal() -> spy.SpyCharacterData:
    return spy.parse_character(_bytes(SANDGRAAL), _source(SANDGRAAL, "1/Sandgraal-Rizzen"))


@pytest.mark.parametrize("name", FILES)
def test_every_capture_round_trips_byte_for_byte(name: str) -> None:
    data = _bytes(name)
    assert luadata.serialize(luadata.parse(data)) == data


def test_the_character_file_reads_every_player_row_with_its_source() -> None:
    got = _sandgraal()
    assert got.present and got.version == "1.1" and got.warning is None
    assert len(got.players) == 24
    assert got.kos == [] and got.ignored == [] and got.encounters == []
    assert got.unread == [] and got.other_keys == []
    for p in got.players:
        assert p.source == _source(SANDGRAAL, "1/Sandgraal-Rizzen")
        # The row's own name is its key, in the `<First>-<Second>` form.
        assert p.name == p.key and "-" in p.key
        assert p.class_token and p.race and p.faction and p.time is not None
        assert p.is_enemy is True and p.is_guess is False
        assert p.wins is None and p.loses is None and not p.reasons
        assert not p.kos and not p.ignored
        assert p.other == {}, (p.key, p.other)
    # Key order is the file's.
    assert got.players[0].key == "Goruthuz-Mugstout"
    assert got.players[-1].key == "Navyor-Sregdod"


def test_a_row_reads_each_field_as_the_file_holds_it() -> None:
    rows = {p.key: p for p in _sandgraal().players}
    p = rows["Goruthuz-Mugstout"]
    assert (p.class_token, p.race, p.level, p.guild, p.faction) == (
        "PALADIN",
        "Dwarf",
        43,
        "Untamed Legacy",
        "Alliance",
    )
    assert (p.zone, p.sub_zone, p.map_id, p.map_x, p.map_y, p.time) == (
        "The Barrens",
        "Ratchet",
        1413,
        0.62,
        0.37,
        1795410775,
    )
    # A player seen in an instance: its name as the zone, no sub zone, no map.
    s = rows["Sonja-Frayle"]
    assert (s.zone, s.sub_zone, s.map_id, s.map_x, s.map_y) == (
        "Wailing Caverns",
        "",
        None,
        None,
        None,
    )
    # A name with a letter outside ASCII.
    assert rows["Tala-Kattéa"].name == "Tala-Kattéa"
    # A row with no guild leaves it None.
    assert rows["Sidney-Goldcrow"].guild is None


def test_the_empty_character_file_reads_as_present_and_empty() -> None:
    got = spy.parse_character(_bytes(TYRONE), _source(TYRONE, "1/Tyrone-Hamberglar"))
    assert got.present and got.version == "1.1" and got.warning is None
    assert got.players == [] and got.kos == [] and got.encounters == [] and got.unread == []


def test_the_account_file_reads_its_lists_and_names_the_rest() -> None:
    got = spy.parse_account(_bytes(ACCOUNT), _source(ACCOUNT, None))
    assert got.present
    # Each character's shared kill-on-sight list is there, and empty.
    assert got.shared_kos == [] and got.notes == [] and got.tags == [] and got.unread == []
    assert got.other_keys == [
        "guildWatch",
        "profileKeys",
        "profiles",
        "ForeverDebug",
        "removeKOSData",
        "watchData",
    ]


def test_a_character_file_has_no_account_table_and_the_other_way_round() -> None:
    assert not spy.parse_account(_bytes(SANDGRAAL), _source(SANDGRAAL, None)).present
    assert not spy.parse_character(_bytes(ACCOUNT), _source(ACCOUNT, None)).present


def test_survey_reads_every_spy_file_of_a_copied_install(tmp_path: Path) -> None:
    """The three files dropped into a copy of a captured install: `survey`
    finds them through the layout, reads each from the install root, and
    passes over the game's `.bak` sibling."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    capture = FIXTURES / "macos-70888"
    shutil.copy2(capture / ".build.info", root / ".build.info")
    shutil.copytree(capture / "forever", root / "_forever_")
    shutil.copytree(
        FIXTURES / RUN / "forever" / "WTF", root / "_forever_" / "WTF", dirs_exist_ok=True
    )
    bak = root / "_forever_" / "WTF/Account/LABACCOUNTA/1/Sandgraal-Rizzen/SavedVariables"
    (bak / "Spy.lua.bak").write_bytes(b"\r\nSpyPerCharDB = function() end\r\n")
    got = spy.survey(layout.Layout(root / "_forever_", install_root=root))
    assert got.errors == []
    assert [a.source.file for a in got.accounts] == [
        "WTF/Account/LABACCOUNTA/SavedVariables/Spy.lua"
    ]
    assert [c.source.character for c in got.characters] == [
        "1/Sandgraal-Rizzen",
        "1/Tyrone-Hamberglar",
    ]
    assert all(c.source.flavor_folder == "_forever_" for c in got.characters)
    assert len(got.players) == 24 and got.warnings == []
