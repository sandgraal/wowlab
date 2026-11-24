"""`wowlab_core.hotfix` on constructed `XFTH` files (M22-23; ADR-0033 note
of 2026-11-23, layer a). Every input here is built by `_hotfix_build` and
labelled `constructed` (L8): the header, the table hashes, the statuses,
an `ItemSparse` row decoded field by field, and hostile files (short,
wrong magic, sizes that lie, caps). The real cache is graded by
`test_hotfix_derived.py` and, by hand, `test_hotfix_live.py`. Nothing here
reads an install.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

from wowlab_core import hotfix, snapshot

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hotfix_build import Rec, blank_row, cache, encode_row, header, record

pytestmark = pytest.mark.parser

TABLES = ("Item", "ItemSparse")
SPARSE = hotfix.layouts()["ItemSparse"]
ITEM = hotfix.layouts()["Item"]


# ─── the table hash ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # The two the owner's cache keys Item and ItemSparse on (derived
        # fixture), and the two wowsims/forever's hotfix.go names.
        ("Item", 0x50238EC2),
        ("ItemSparse", 0x919BE54E),
        ("TactKey", 0xDF2F53CF),
        ("BroadcastText", 0x021826BB),
    ],
)
def test_sstrhash_of_known_tables(name: str, expected: int) -> None:
    assert hotfix.sstrhash(name) == expected
    assert hotfix.sstrhash(name.upper()) == hotfix.sstrhash(name.lower()) == expected


# ─── the header ──────────────────────────────────────────────────────────────


def test_header_and_an_empty_cache_constructed() -> None:
    got = hotfix.parse(header(71037), TABLES)
    assert (got.version, got.build, got.records_in_file, got.records) == (9, 71037, 0, [])


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (b"", "does not start with XFTH"),
        (b"XFT", "does not start with XFTH"),
        (b"HTFX" + bytes(40), "does not start with XFTH"),
        (header(1, version=8), "format version 8"),
        (header(1)[:20], "ends inside its 44-byte header"),
    ],
    ids=["empty", "short", "magic", "version", "short_header"],
)
def test_a_bad_header_is_refused_constructed(data: bytes, words: str) -> None:
    with pytest.raises(hotfix.HotfixError, match=words):
        hotfix.parse(data, TABLES)


# ─── records and statuses ────────────────────────────────────────────────────


def test_records_of_the_tables_asked_for_are_kept_in_file_order_constructed() -> None:
    data = cache(
        71037,
        [
            Rec("ItemSparse", 7, b"\1\2", status=1, push_id=5, unique_id=9),
            Rec("TactKey", 1, b"KEYBYTES" * 2),
            Rec("Item", 7, b"", status=3, push_id=-1),
            Rec(0x12345678, 2, b"other"),
            Rec("Item", 8, b"", status=2),
            Rec("Item", 9, b"", status=4),
        ],
    )
    got = hotfix.parse(data, TABLES)
    assert got.records_in_file == 6
    assert [(r.table, r.record_id, r.status, r.push_id, r.data) for r in got.records] == [
        ("ItemSparse", 7, 1, 5, b"\1\2"),
        ("Item", 7, 3, -1, b""),
        ("Item", 8, 2, 1, b""),
        ("Item", 9, 4, 1, b""),
    ]
    first = got.records[0]
    assert (first.unique_id, first.region_id, first.offset) == (9, 91, hotfix.HEADER_BYTES)
    # No record of another table is kept: the key's bytes are nowhere.
    assert all(b"KEYBYTES" not in r.data for r in got.records)
    assert "KEYBYTES" not in got.model_dump_json()


@pytest.mark.parametrize(
    ("tail", "words"),
    [
        (record(Rec("Item", 1, b"abc"))[:20], "ends inside a record header"),
        (record(Rec("Item", 1, b"abc", magic=b"XFTX")), "does not start with XFTH"),
        (record(Rec("Item", 1, b"abc", size=-1)), "holds -1 bytes"),
        (record(Rec("Item", 1, b"abc", size=4)), "holds 4 bytes; 3 are left"),
        (record(Rec("Item", 1, b"abc", size=2**31 - 1)), "2147483647 bytes"),
    ],
    ids=["short_record", "record_magic", "negative_size", "size_past_end", "huge_size"],
)
def test_a_record_that_does_not_fit_is_refused_with_its_offset_constructed(
    tail: bytes, words: str
) -> None:
    data = cache(1, [Rec("ItemSparse", 1, b"x")]) + tail
    second = hotfix.HEADER_BYTES + hotfix.RECORD_HEADER_BYTES + 1
    with pytest.raises(hotfix.HotfixError, match=words) as caught:
        hotfix.parse(data, TABLES)
    assert f"byte {second}" in str(caught.value)


def test_the_record_caps_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    data = cache(1, [Rec("Item", n) for n in range(3)] + [Rec("Spell", 9)])
    monkeypatch.setattr(hotfix, "MAX_KEPT", 2)
    with pytest.raises(hotfix.HotfixError, match="more than 2 rows"):
        hotfix.parse(data, TABLES)
    monkeypatch.setattr(hotfix, "MAX_KEPT", 10)
    monkeypatch.setattr(hotfix, "MAX_RECORDS", 3)
    with pytest.raises(hotfix.HotfixError, match="more than 3 records"):
        hotfix.parse(data, TABLES)


def test_a_table_name_must_be_a_word_constructed() -> None:
    with pytest.raises(ValueError, match="not a table name"):
        hotfix.parse(header(1), ["Item;"])


# ─── decoding a row ──────────────────────────────────────────────────────────


def _sparse(**values: str) -> dict[str, str]:
    return blank_row(
        SPARSE,
        Display_lang="Constructed Boots",
        Description_lang="Ünïcode",
        DmgVariance="0.20000000298",
        StatPercentEditor_0="6667",
        StatModifier_bonusStat_0="7",
        AllowableRace_0="-1",
        AllowableRace_1="-1",
        Flags_4="512",
        ItemLevel="15",
        AllowableClass="-1",
        RequiredLevel="10",
        InventoryType="8",
        OverallQualityID="2",
        Bonding="2",
        MinFactionID="65535",
        **values,
    )


def test_an_itemsparse_row_decodes_field_by_field_constructed() -> None:
    row = _sparse()
    data = encode_row(SPARSE, row)
    [rec] = hotfix.parse(cache(71037, [Rec("ItemSparse", 6537, data)]), TABLES).records
    got = hotfix.decode_row(SPARSE, rec)
    assert list(got) == [*SPARSE.columns, hotfix.SOURCE_KEY]
    assert got["ID"] == "6537"
    assert got[hotfix.SOURCE_KEY] == hotfix.SOURCE_HOTFIX
    assert {k: got[k] for k in row} == row
    # Widths as the layout gives them: 5 strings, 62 four-byte fields, 18
    # two-byte and 18 one-byte ones.
    assert len(data) == sum(len(row[c].encode()) + 1 for c in SPARSE.columns[1:6]) + 302


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (b"no end", "has no end to its Description_lang text"),
        (b"\xff\xfe\0", "Description_lang text that is not UTF-8"),
        (b"\0" * 5 + b"\1\2", "ends before its ExpansionID value"),
    ],
    ids=["unterminated", "not_utf8", "short"],
)
def test_a_row_that_is_not_one_row_is_refused_constructed(data: bytes, words: str) -> None:
    [rec] = hotfix.parse(cache(1, [Rec("ItemSparse", 3, data)]), TABLES).records
    with pytest.raises(hotfix.HotfixError, match=words) as caught:
        hotfix.decode_row(SPARSE, rec)
    assert "the ItemSparse row 3" in str(caught.value)


def test_a_row_with_bytes_left_over_is_refused_constructed() -> None:
    data = encode_row(SPARSE, _sparse()) + b"\0"
    [rec] = hotfix.parse(cache(1, [Rec("ItemSparse", 3, data)]), TABLES).records
    with pytest.raises(hotfix.HotfixError, match="holds 1 bytes more than one row"):
        hotfix.decode_row(SPARSE, rec)


def test_an_item_row_is_43_bytes_constructed() -> None:
    row = blank_row(ITEM, ClassID="4", SubclassID="1", InventoryType="8")
    data = encode_row(ITEM, row)
    assert len(data) == 43
    [rec] = hotfix.parse(cache(1, [Rec("Item", 6537, data)]), TABLES).records
    assert hotfix.decode_row(ITEM, rec)["InventoryType"] == "8"


@pytest.mark.parametrize(
    ("value", "text"),
    [(0.2, "0.20000000298"), (1.0, "1"), (1.0061, "1.00610005856"), (-0.0, "0"), (0.0, "0")],
)
def test_floats_are_written_as_the_table_exports_write_them_constructed(
    value: float, text: str
) -> None:
    packed = struct.unpack("<f", struct.pack("<f", value))[0]
    assert hotfix._float_text(packed) == text


# ─── the layouts ─────────────────────────────────────────────────────────────


def test_the_shipped_layouts_name_no_build() -> None:
    text = (Path(hotfix.__file__).parent / hotfix.LAYOUTS_FILE).read_text(encoding="utf-8")
    assert "71037" not in text and "1.60" not in text and "_forever_" not in text
    assert set(hotfix.layouts()) == {"Item", "ItemSparse", "TraitNodeEntry"}
    assert hotfix.layouts()["TraitNodeEntry"].inline_id and not SPARSE.inline_id
    assert hotfix.layouts()["TraitNodeEntry"].columns[:2] == ["ID", "TraitDefinitionID"]
    assert len(SPARSE.columns) == 104 and len(ITEM.columns) == 17


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("x = 1", "nothing else"),
        ("[tables.Item]\nfields = []", "one key, fields, a list"),
        ('[tables.Item]\nfields = ["A:i64"]', "is not <name>:<type>"),
        ('[tables.Item]\nfields = ["A:i32:0"]', "1 to 64"),
        ('[tables.Item]\nfields = ["A:i32", "A:u8"]', "names a column twice"),
        ('[tables."It em"]\nfields = ["A:i32"]', "is not a table name"),
        ("[tables", "hotfix_layouts.toml"),
    ],
    ids=["other_key", "empty", "type", "count", "twice", "name", "toml"],
)
def test_a_bad_layouts_file_is_refused_constructed(text: str, words: str) -> None:
    with pytest.raises(hotfix.LayoutError, match=words):
        hotfix.parse_layouts(text)


# ─── reading a file ──────────────────────────────────────────────────────────


def _flavor(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "install"
    flavor = root / "_flavor_"
    (flavor / "Cache" / "ADB" / "enUS").mkdir(parents=True)
    return root, flavor


def test_read_opens_the_file_read_only_and_leaves_it_unchanged_constructed(
    tmp_path: Path,
) -> None:
    root, flavor = _flavor(tmp_path)
    path = flavor / "Cache" / "ADB" / "enUS" / hotfix.CACHE_FILE
    data = cache(7, [Rec("Item", 1, b"x")])
    path.write_bytes(data)
    before = path.stat()
    got = hotfix.read(path, TABLES, root=root)
    assert got.build == 7 and len(got.records) == 1
    after = path.stat()
    assert path.read_bytes() == data and after.st_mtime_ns == before.st_mtime_ns
    assert sorted(p.name for p in path.parent.iterdir()) == [hotfix.CACHE_FILE]


def test_read_refuses_a_link_and_a_file_over_the_cap_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, flavor = _flavor(tmp_path)
    real = tmp_path / "elsewhere.bin"
    real.write_bytes(cache(7, []))
    link = flavor / "Cache" / "ADB" / "enUS" / hotfix.CACHE_FILE
    link.symlink_to(real)
    with pytest.raises(snapshot.UnsafeReadError):
        hotfix.read(link, TABLES, root=root)
    link.unlink()
    link.write_bytes(cache(7, [Rec("Item", 1, b"x" * 100)]))
    monkeypatch.setattr(hotfix, "MAX_FILE_BYTES", 64)
    with pytest.raises(snapshot.UnsafeReadError, match="more than 64"):
        hotfix.read(link, TABLES, root=root)


def test_find_caches_newest_first_and_no_link_constructed(tmp_path: Path) -> None:
    _, flavor = _flavor(tmp_path)
    adb = flavor / "Cache" / "ADB"
    (adb / "enGB").mkdir()
    (adb / "frFR").mkdir()
    old = adb / "enUS" / hotfix.CACHE_FILE
    new = adb / "enGB" / hotfix.CACHE_FILE
    old.write_bytes(header(1))
    new.write_bytes(header(1))
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new, ns=(2_000_000_000, 2_000_000_000))
    (adb / "frFR" / hotfix.CACHE_FILE).symlink_to(old)
    assert hotfix.find_caches(flavor) == [new, old]
    assert hotfix.find_caches(tmp_path / "nowhere") == []


def test_an_inline_id_must_be_the_records_row_id_constructed() -> None:
    layout = hotfix.layouts()["TraitNodeEntry"]
    row = blank_row(layout, ID="130507", TraitDefinitionID="135308", MaxRanks="3")
    data = encode_row(layout, row)
    assert len(data) == 17
    good, bad = hotfix.parse(
        cache(1, [Rec("TraitNodeEntry", 130507, data), Rec("TraitNodeEntry", 7, data)]),
        ["TraitNodeEntry"],
    ).records
    assert hotfix.decode_row(layout, good)["TraitDefinitionID"] == "135308"
    assert list(hotfix.decode_row(layout, good))[:2] == ["ID", "TraitDefinitionID"]
    with pytest.raises(hotfix.HotfixError, match="the TraitNodeEntry row 7 holds the ID 130507"):
        hotfix.decode_row(layout, bad)
    with pytest.raises(hotfix.LayoutError, match="an inline ID is first"):
        hotfix.parse_layouts('[tables.T]\nfields = ["A:i32", "ID:i32"]')
