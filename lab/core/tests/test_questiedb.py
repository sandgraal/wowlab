"""`questiedb`: QuestieDB's data read as data (M22-08, `docs/LAB_FORMATS.md`
§13).

CONSTRUCTED INPUT throughout, as labelled: QuestieDB is a third-party
addon's data and no byte of it is committed (names and shapes only, as
M21-09 did with Ketho's lists). The slices are built at run time by
`_questie_build.py` in the store's encoding; `test_questiedb_live.py`, run
by the owner, reads the installed copy. Hostile inputs (malformed CBOR,
missing chunks, code where a literal belongs) are constructed too. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from _questie_build import (
    CORRECTIONS_FILE,
    DROPS_FILE,
    SLICE,
    b64,
    cbor,
    chunked,
    place,
    slice_toc,
    toc_text,
)

from wowlab_core import questiedb
from wowlab_core.questiedb import CborError, QuestieDBError, decode_cbor

# ─── CBOR ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        (b"\x00", 0),
        (b"\x17", 23),
        (b"\x18\x18", 24),
        (b"\x19\x01\x00", 256),
        (b"\x1a\x00\x01\x00\x00", 65536),
        (b"\x1b\x00\x00\x00\x02\x00\x00\x00\xb2", 8589934770),
        (b"\x20", -1),
        (b"\x38\x63", -100),
        (b"\x43abc", b"abc"),
        (b"\x63\xc3\xa9t", "ét"),
        (b"\x82\x01\x02", [1, 2]),
        (b"\xa2\x01\x41a\x41p\x0e", {1: b"a", b"p": 14}),
        (b"\xf4", False),
        (b"\xf5", True),
        (b"\xf6", None),
        (b"\xf9\x3c\x00", 1.0),
        (b"\xfa" + struct.pack(">f", 0.5), 0.5),
        (b"\xfb" + struct.pack(">d", 74.98), 74.98),
        (b"\x80", []),
        (b"\xa0", {}),
    ],
)
def test_cbor_reads_the_subset_the_store_uses_constructed(raw: bytes, value: object) -> None:
    assert decode_cbor(raw) == value
    assert type(decode_cbor(raw)) is type(value)


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        (b"", "needs 1 bytes"),
        (b"\x19\x01", "needs 2 bytes"),
        (b"\x45ab", "needs 5 bytes"),
        (b"\x9f\x01\xff", "indefinite"),
        (b"\x5f\x41a\xff", "indefinite"),
        (b"\x1c", "reserved"),
        (b"\xc1\x00", "tag"),
        (b"\xf7", "undefined"),
        (b"\xf0", "simple value"),
        (b"\xf8\x20", "simple value"),
        (b"\x9b\xff\xff\xff\xff\xff\xff\xff\xff", "declares"),
        (b"\xba\x00\x10\x00\x00\x01", "declares"),
        (b"\xa1\x80\x01", "map key"),
        (b"\xa1\xf6\x01", "map key"),
        (b"\xa2\x01\x01\x01\x02", "given twice"),
        (b"\x01\x02", "after the value"),
        (b"\x62\xff\xfe", "not UTF-8"),
    ],
)
def test_cbor_refuses_what_is_outside_the_subset_constructed(raw: bytes, words: str) -> None:
    with pytest.raises(CborError, match=words):
        decode_cbor(raw)


def test_cbor_refuses_nesting_past_the_bound_constructed() -> None:
    deep = b"\x81" * (questiedb.MAX_CBOR_DEPTH + 1) + b"\x00"
    with pytest.raises(CborError, match="nested deeper"):
        decode_cbor(deep)
    fits = b"\x81" * questiedb.MAX_CBOR_DEPTH + b"\x00"
    assert decode_cbor(fits) is not None


def test_cbor_error_carries_the_offset_constructed() -> None:
    with pytest.raises(CborError) as caught:
        decode_cbor(b"\x82\x01\xc0\x00")
    assert caught.value.offset == 2


def test_the_test_encoder_round_trips_through_the_reader_constructed() -> None:
    for kind in SLICE.values():
        for row in kind.values():
            for value in row.values():
                expected = value.encode() if isinstance(value, str) else value
                got = decode_cbor(cbor(value))
                if isinstance(value, (list, dict)):
                    assert got is not None
                else:
                    assert got == expected


# ─── the store ───────────────────────────────────────────────────────────────


def _store(tmp_path: Path, toc: bytes) -> questiedb.QuestieStore:
    path = place(tmp_path / "flavor", toc)
    return questiedb.open_store(path)


def test_finds_the_folder_with_case_folded_and_only_tocs_named_after_it_constructed(
    tmp_path: Path,
) -> None:
    addons = tmp_path / "Interface" / "AddOns"
    folder = addons / "questiedb"
    folder.mkdir(parents=True)
    for name in ("questiedb_Forever.toc", "QuestieDB-Mainline.toc", "Other.toc", "QuestieDB_.toc"):
        (folder / name).write_bytes(b"## Title: x\n")
    (addons / "Questie").mkdir()
    (addons / "Questie" / "Questie.toc").write_bytes(b"## Title: x\n")
    found, tocs = questiedb.find_toc(addons)
    assert found == folder
    assert [t.name for t in tocs] == ["QuestieDB-Mainline.toc", "questiedb_Forever.toc"]


def test_no_folder_is_none_constructed(tmp_path: Path) -> None:
    assert questiedb.find_toc(tmp_path / "nowhere") == (None, [])
    (tmp_path / "AddOns" / "Questie").mkdir(parents=True)
    assert questiedb.find_toc(tmp_path / "AddOns") == (None, [])


def test_reads_info_ids_names_and_tables_constructed(tmp_path: Path) -> None:
    store = _store(tmp_path, slice_toc())
    assert store.baked
    info = store.info()
    assert (info.folder, info.toc, info.version, info.flavor, info.mode) == (
        "QuestieDB",
        "QuestieDB_Forever.toc",
        "1.1.4",
        "Forever",
        "baked",
    )
    assert store.ids("Item") == sorted(SLICE["Item"])
    names = {entity: name for entity, name, _ in store.names("Npc")}
    assert names[12696] == "Senani Thunderheart"
    row = store.row("Item", 16305)
    assert row is not None and row[1] == b"Sharptalon's Claw" and row[9] == 20
    assert store.table("Item", 16305, 2, row) == [12676]
    # Field 6 is not stored for this item: its presence bit is clear.
    assert store.table("Item", 16305, 6, row) is None
    assert store.unreadable == {}


def test_a_row_written_as_an_array_is_keyed_one_to_n_constructed(tmp_path: Path) -> None:
    toc = toc_text({"Npc": {7: {1: "Kobold Vermin", 4: 1, 5: 2}}}, arrays=[("Npc", 7)])
    store = _store(tmp_path, toc)
    assert store.row("Npc", 7) == {1: b"Kobold Vermin", 4: 1, 5: 2}


def test_chunked_values_are_joined_constructed(tmp_path: Path) -> None:
    store = _store(tmp_path, slice_toc(chunk_ids=3))
    assert store.ids("Npc") == sorted(SLICE["Npc"])


def test_a_set_presence_bit_with_no_value_reads_none_constructed(tmp_path: Path) -> None:
    row = {1: b"x", b"p": 1 << 1}
    toc = toc_text({}) + f"## X-Item-77-S: {b64(cbor(row))}\n".encode()
    store = _store(tmp_path, toc)
    got = store.row("Item", 77)
    assert got is not None and store.table("Item", 77, 2, got) is None


def test_a_missing_chunk_or_bad_value_makes_that_row_unreadable_only_constructed(
    tmp_path: Path,
) -> None:
    good = b64(cbor({1: b"Fine"}))
    lines = [
        *chunked("X-Item-5-S", good, 2)[:2],  # part 2 of 2 is missing
        "## X-Item-6-S: not base64!",
        f"## X-Item-7-S: {b64(b'\\xc1\\x00')}",  # a tag
        f"## X-Item-8-S: {good}",
        f"## X-Item-9-S: {b64(cbor(5))}",  # a row that is not a table
    ]
    ids = b64(zlib.compress(cbor([5, 6, 7, 8, 9])))
    toc = toc_text({}).replace(
        b"## X-Item-IDS: " + b64(zlib.compress(cbor([]))).encode(),
        f"## X-Item-IDS: {ids}".encode(),
    )
    store = _store(tmp_path, toc + ("\n".join(lines) + "\n").encode())
    assert [(e, n) for e, n, _ in store.names("Item")] == [(8, "Fine")]
    assert store.unreadable == {"Item": 4}


@pytest.mark.parametrize(
    "header",
    [
        "not base64!",
        b64(b"not zlib"),
        b64(zlib.compress(cbor({1: 2}))),
        b64(zlib.compress(cbor([1, -2]))),
        b64(zlib.compress(cbor([1, 2]))[:-3]),
    ],
)
def test_a_bad_id_header_is_an_error_constructed(tmp_path: Path, header: str) -> None:
    toc = toc_text({}).replace(
        b"## X-Npc-IDS: " + b64(zlib.compress(cbor([]))).encode(),
        f"## X-Npc-IDS: {header}".encode(),
    )
    store = _store(tmp_path, toc)
    with pytest.raises(QuestieDBError):
        store.ids("Npc")


def test_an_id_header_declaring_too_many_ids_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(questiedb, "MAX_IDS", 3)
    store = _store(tmp_path, toc_text({"Npc": {i: {1: "n"} for i in range(1, 5)}}))
    with pytest.raises(QuestieDBError, match="declares 4 ids, more than 3"):
        store.ids("Npc")


def test_the_id_caps_leave_room_over_the_real_size() -> None:
    # The owner's largest header: 23,978 ids, about 120 KB inflated.
    assert questiedb.MAX_IDS >= 40 * 23_978
    assert questiedb.MAX_IDS_BYTES >= 30 * 120_000


def test_an_id_header_inflating_past_the_cap_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(questiedb, "MAX_IDS_BYTES", 16)
    store = _store(tmp_path, toc_text({"Npc": {i: {1: "n"} for i in range(1, 40)}}))
    with pytest.raises(QuestieDBError, match="inflates past"):
        store.ids("Npc")


def test_a_chunk_count_out_of_range_is_an_error_constructed(tmp_path: Path) -> None:
    store = _store(tmp_path, toc_text({}) + b"## X-Big: ~0~\n## X-Huge: ~999999~\n")
    for key in ("X-Big", "X-Huge"):
        with pytest.raises(QuestieDBError, match="declares"):
            store.stored(key)
    assert store.stored("X-Contract-Version") == "4"


def test_a_toc_with_no_store_is_not_baked_constructed(tmp_path: Path) -> None:
    store = _store(tmp_path, b"## Title: QuestieDB\n## Version: 1\nsrc\\api.lua\n")
    assert not store.baked


def test_a_toc_past_the_size_cap_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(questiedb, "MAX_STORE_BYTES", 64)
    path = place(tmp_path / "flavor", slice_toc())
    with pytest.raises(QuestieDBError, match="larger than 64 bytes"):
        questiedb.open_store(path)


def test_lists_what_it_leaves_out_constructed(tmp_path: Path) -> None:
    store = _store(tmp_path, slice_toc())
    parts = [left.part for left in store.left_out()]
    assert parts == [
        "the corrections Questie picks by the character's faction and class",
        "names in other languages",
    ]
    why = store.left_out()[0].why
    assert "lists both factions' givers" in why and "some quests show no zone" in why


# ─── the drop tables ─────────────────────────────────────────────────────────


def test_reads_the_literal_drop_tables_and_leaves_the_code_out_constructed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, slice_toc())
    data = questiedb.read_drop_tables(store)
    assert [(t.name, t.file, t.chances) for t in data.tables] == [
        ("wowheadData", DROPS_FILE, {2589: {3: 12.5}, 725: {3: 45.04, 12676: 37.6}}),
        ("cmangosData", DROPS_FILE, {2589: {3: 10.0}, 725: {12676: 80.0}}),
    ]
    # The first section's corrections, the table references read as their
    # tokens' negative numbers, never evaluated.
    assert data.corrections is not None
    assert (data.corrections.name, data.corrections.file) == ("Era", CORRECTIONS_FILE)
    assert data.corrections.chances == {
        2589: {3: questiedb.REFERENCE_WOWHEAD},
        725: {3: 50.0, 12676: questiedb.REFERENCE_PSERVER},
    }
    # A later section with figures, and one holding a call, are left out by
    # name; an empty section says nothing.
    assert [(x.part, x.why) for x in data.left_out] == [
        (
            f"the drop corrections Tbc in {CORRECTIONS_FILE}",
            "Questie applies them only on later game versions",
        ),
        (
            f"the drop corrections Cata in {CORRECTIONS_FILE}",
            "they hold more than figures and table references, so reading them would mean "
            "running them",
        ),
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"Q.Era = {\n  [1] = { [2] = DropKeys.OTHER },\n}\n",
        b"Q.Era = {\n  [1] = { [2] = DropKeys.WOWHEAD() },\n}\n",
        b"Q.Era = {\n  [1] = { [2] = -DropKeys.WOWHEAD },\n}\n",
        b"Q.Era = {\n  [1] = { [2] = setmetatable({}, {}) },\n}\n",
        b"Q.Era = {\n  [1] = { [2] = 5 } --[[ a long comment ]]\n}\n",
        b'Q.Era = {\n  [1] = { [2] = "DropKeys.WOWHEAD" },\n}\n',
        b"Q.Era = {\n  [1] = { [2] = 5 },\n",
    ],
)
def test_corrections_that_are_not_figures_or_references_are_left_out_constructed(
    tmp_path: Path, body: bytes
) -> None:
    store = _store(tmp_path, slice_toc())
    path = store.path.parent / "support" / "Forever" / "DropTables" / "itemDropCorrections.lua"
    path.write_bytes(body)
    data = questiedb.read_drop_tables(store)
    assert data.corrections is None
    assert [x.part for x in data.left_out] == [f"the drop corrections Era in {CORRECTIONS_FILE}"]


def test_a_drop_file_with_no_table_is_left_out_as_code_constructed(tmp_path: Path) -> None:
    store = _store(tmp_path, slice_toc())
    path = store.path.parent / "support" / "Forever" / "DropTables" / "itemDropCorrections.lua"
    path.write_bytes(b"local x = 1\nprint(x)\n")
    data = questiedb.read_drop_tables(store)
    assert [(x.part, x.why) for x in data.left_out] == [
        (f"the drop file {CORRECTIONS_FILE}", "it is Lua code, which wowlab never runs")
    ]


def test_reads_questiedbs_own_zone_names_constructed(tmp_path: Path) -> None:
    store = _store(tmp_path, slice_toc())
    names, left = questiedb.read_zone_names(store)
    assert names == {12: "Elwynn Forest", 10022: "Dire Maul Gordok Commons"} and left == []
    path = store.path.parent / "src" / "corrections" / "enum" / "zones.lua"
    path.write_bytes(b"constants.zoneIDs = {\n  A = 1,\n  B = getId(),\n}\n")
    names, left = questiedb.read_zone_names(store)
    assert names == {} and [x.part for x in left] == [
        "QuestieDB's own zone names in src\\corrections\\enum\\zones.lua"
    ]


@pytest.mark.parametrize(
    ("body", "refused"),
    [
        # A call, an identifier and an operator: code, never run, never read.
        (b"T.a = [[return { [1] = { [2] = os.exit() } }]]\n", True),
        (b"T.a = [[return { [1] = { [2] = DropKeys.WOWHEAD } }]]\n", True),
        (b"T.a = [[return { [1] = { [2] = 1 + 1 } }]]\n", True),
        # A literal of another shape: not a drop table.
        (b'T.a = [[return { [1] = "x" }]]\n', True),
        # Not closed.
        (b"T.a = [[return { [1] = { [2] = 5 } }\n", True),
        # A deeper long bracket closes at its own level.
        (b"T.a = [==[return { [7] = { [8] = 50 } } -- ]] inside\n]==]\n", False),
    ],
)
def test_a_long_string_that_is_not_one_literal_is_left_out_by_name_constructed(
    tmp_path: Path, body: bytes, refused: bool
) -> None:
    store = _store(tmp_path, slice_toc())
    path = store.path.parent / "support" / "Forever" / "DropTables" / "classicItemDrops.lua"
    path.write_bytes(body)
    data = questiedb.read_drop_tables(store)
    if refused:
        assert data.tables == []
        assert data.left_out[0].part == f"the drop table a in {DROPS_FILE}"
        assert "running it" in data.left_out[0].why
    else:
        assert [t.chances for t in data.tables] == [{7: {8: 50.0}}]


def test_a_listed_path_that_climbs_out_is_not_read_constructed(tmp_path: Path) -> None:
    outside = tmp_path / "flavor" / "Interface" / "AddOns" / "Secret.lua"
    toc = toc_text({}, files=["..\\DropTables\\..\\Secret.lua", "DropTables\\..\\..\\x.lua"])
    store = _store(tmp_path, toc)
    outside.write_bytes(b"T.wowheadData = [[return { [1] = { [2] = 3 } }]]\n")
    assert questiedb.read_drop_tables(store) == questiedb.DropData(tables=[])


def test_reading_writes_nothing_constructed(tmp_path: Path) -> None:
    flavor = tmp_path / "flavor"
    path = place(flavor, slice_toc())

    def state() -> dict[str, tuple[bytes, int]]:
        return {
            str(p.relative_to(flavor)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(flavor.rglob("*"))
            if p.is_file()
        } | {str(p.relative_to(flavor)): (b"", 0) for p in flavor.rglob("*") if p.is_dir()}

    before = state()
    store = questiedb.open_store(path)
    for kind in questiedb.KINDS:
        list(store.names(kind))
    questiedb.read_drop_tables(store)
    assert state() == before


def test_the_reader_never_imports_an_interpreter() -> None:
    source = Path(questiedb.__file__).read_text(encoding="utf-8")
    for word in ("lupa", "subprocess", "eval(", "exec(", "loadstring", "ctypes"):
        if word == "loadstring":
            # Named in the docstring as what Questie does in game, never called.
            assert "loadstring(" not in source
            continue
        assert word not in source
