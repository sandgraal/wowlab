"""`casc` on constructed files (M15-03; ADR-0033 layer a, labelled
`constructed`): the build row, the build config, the `.idx` layout and its
two hashes, the newest-version rule, the typed refusals, and L1 (nothing
opened but what the reader needs, read-only, closed; nothing written).

The real install is graded by the derived fixture
(`test_casc_derived.py`) and by hand (`test_casc_live.py`, `@live`).
"""

from __future__ import annotations

import os
import random
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from _casc_build import (
    FLAVOR,
    PRODUCT,
    build_config_text,
    chained,
    entry_bytes,
    idx_bytes,
    idx_name,
    key_in,
    make_install,
    md5,
    tree_state,
)

from wowlab_core import casc
from wowlab_core.install import read_install

pytestmark = pytest.mark.parser

FOUR_SCORE = b"Four score and seven years ago"


# ─── lookup3 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "pc", "pb", "want"),
    [
        (b"", 0, 0, (0xDEADBEEF, 0xDEADBEEF)),
        (b"", 0, 0xDEADBEEF, (0xBD5B7DDE, 0xDEADBEEF)),
        (b"", 0xDEADBEEF, 0xDEADBEEF, (0x9C093CCD, 0xBD5B7DDE)),
        (FOUR_SCORE, 0, 0, (0x17770551, 0xCE7226E6)),
        (FOUR_SCORE, 0, 1, (0xE3607CAE, 0xBD371DE4)),
        (FOUR_SCORE, 1, 0, (0xCD628161, 0x6CBEA4B3)),
    ],
    ids=["empty", "empty-pb", "empty-both", "four-score", "four-score-pb1", "four-score-pc1"],
)
def test_hashlittle2_gives_lookup3s_published_values_constructed(
    data: bytes, pc: int, pb: int, want: tuple[int, int]
) -> None:
    """The values lookup3.c's own self-test prints (`driver5`)."""
    assert casc.hashlittle2(data, pc, pb) == want


def test_hashlittle_is_the_first_value_constructed() -> None:
    assert casc.hashlittle(FOUR_SCORE) == 0x17770551
    assert casc.hashlittle(FOUR_SCORE, 1) == 0xCD628161


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_side_by_side_entries_hash_equals_entry_by_entry_constructed(seed: int) -> None:
    """The lanes give the chain `hashlittle2` gives one entry at a time, for
    blocks of every length (none, one, unequal, the longest last or first)."""
    rng = random.Random(seed)
    counts = [0, 1, 2, 7, 33, 5, 1, 0, 64, 3, 12, 40, 9, 2, 17, 26]
    rng.shuffle(counts)
    blocks = [rng.randbytes(18 * n) for n in counts]
    assert casc._entries_hashes(blocks) == [chained(b) for b in blocks]


def test_side_by_side_entries_hash_with_extreme_bytes_constructed() -> None:
    """All-ones and all-zero entries: no carry or borrow crosses a lane."""
    blocks = [b"\xff" * 18 * 9, b"\x00" * 18 * 4, b"\xff\x00" * 9 * 6, b"\x80" * 18]
    assert casc._entries_hashes(blocks) == [chained(b) for b in blocks]


# ─── bucket and lookup ───────────────────────────────────────────────────────


def test_bucket_is_the_folded_xor_of_nine_bytes_constructed() -> None:
    # x = 0x12 ^ 0x34 = 0x26 -> 0x6 ^ 0x2 = 4; bytes past nine do not count.
    assert casc.bucket_of(bytes([0x12, 0x34]) + bytes(7) + b"\xff" * 7) == 4
    assert casc.bucket_of(bytes(9)) == 0
    assert casc.bucket_of(bytes([0xF0]) + bytes(8)) == 0xF


def _write(tmp_path: Path, files: dict[str, bytes]) -> Path:
    data = tmp_path / "Data" / "data"
    data.mkdir(parents=True)
    for name, blob in files.items():
        (data / name).write_bytes(blob)
    return tmp_path


def test_two_buckets_two_versions_of_one_take_the_newest_constructed(tmp_path: Path) -> None:
    rng = random.Random(7)
    old = [(key_in(4, rng), 1, 0, 10), (key_in(4, rng), 1, 30, 11)]
    new = [(old[0][0], 5, 90, 12), (key_in(4, rng), 6, 120, 13)]
    other = [(key_in(11, rng), 2, 60, 20)]
    root = _write(
        tmp_path,
        {
            idx_name(4, 0x1F): idx_bytes(4, old),
            idx_name(4, 0x20): idx_bytes(4, new),
            idx_name(4, 0x0A): idx_bytes(4, old[:1]),
            idx_name(11, 3): idx_bytes(11, other),
        },
    )
    index, versions = casc.load_index(root)
    assert [(v.bucket, v.version, v.file, v.entries, v.not_taken) for v in versions] == [
        (4, 0x20, "0400000020.idx", 2, (0x1F, 0x0A)),
        (11, 3, "0b00000003.idx", 1, ()),
    ]
    assert len(index) == 3 and index.buckets == (4, 11)
    assert index.find(new[0][0]) == casc.IndexEntry(archive=5, offset=90, size=12)
    assert index.find(new[1][0] + b"rest of the key") == casc.IndexEntry(
        archive=6, offset=120, size=13
    )
    assert index.find(old[1][0]) is None  # only in the older version
    assert index.find(other[0][0]) == casc.IndexEntry(archive=2, offset=60, size=20)
    assert index.find(key_in(7, rng)) is None  # no file for that bucket
    with pytest.raises(ValueError, match="9 bytes"):
        index.find(b"short")


@pytest.mark.parametrize(
    ("header_size", "block_at"),
    [(16, 32), (20, 32), (24, 32), (40, 48)],
    ids=["real-shape-padded", "padded-4", "on-boundary-24", "on-boundary-40"],
)
def test_entries_block_at_the_next_16_byte_boundary_constructed(
    tmp_path: Path, header_size: int, block_at: int
) -> None:
    """The entries block starts at the first 16-byte boundary at or after the
    header's end, whatever the header size: 8 + 24 and 8 + 40 end on one."""
    rng = random.Random(header_size)
    blob = idx_bytes(2, [(key_in(2, rng), 3, 4, 5)], header_size=header_size)
    assert int.from_bytes(blob[block_at : block_at + 4], "little") == 18
    index, _ = casc.load_index(_write(tmp_path, {idx_name(2, 1): blob}))
    assert len(index) == 1


def test_entry_fields_at_their_bit_boundaries_constructed(tmp_path: Path) -> None:
    """Ten bits of archive and thirty of offset in the big-endian field, the
    size little-endian: the largest of each, and the smallest."""
    rng = random.Random(30)
    top, bottom = key_in(5, rng), key_in(5, rng)
    blob = idx_bytes(5, [(top, 1023, (1 << 30) - 1, 0xFFFFFFFF), (bottom, 0, 0, 0)])
    index, _ = casc.load_index(_write(tmp_path, {idx_name(5, 1): blob}))
    assert index.find(top) == casc.IndexEntry(archive=1023, offset=(1 << 30) - 1, size=0xFFFFFFFF)
    assert index.find(bottom) == casc.IndexEntry(archive=0, offset=0, size=0)


def test_an_empty_bucket_file_is_read_constructed(tmp_path: Path) -> None:
    index, versions = casc.load_index(_write(tmp_path, {idx_name(0, 1): idx_bytes(0, [])}))
    assert len(index) == 0 and versions[0].entries == 0


def test_names_that_are_not_idx_files_are_not_opened_constructed(tmp_path: Path) -> None:
    rng = random.Random(1)
    root = _write(
        tmp_path,
        {
            idx_name(1, 1): idx_bytes(1, [(key_in(1, rng), 0, 0, 1)]),
            "0100000002.idx.bak": b"not an index",
            "0100000003.IDX": b"not an index",
            "01000003.idx": b"not an index",
            "shmem": b"not an index",
        },
    )
    _, versions = casc.load_index(root)
    assert [(v.bucket, v.version) for v in versions] == [(1, 1)]


# ─── refusals ────────────────────────────────────────────────────────────────

_RNG = random.Random(99)
_GOOD = [(key_in(6, _RNG), 1, 30 * i, 40 + i) for i in range(4)]


def _flip(blob: bytes, at: int) -> bytes:
    return blob[:at] + bytes([blob[at] ^ 0x01]) + blob[at + 1 :]


@pytest.mark.parametrize(
    ("blob", "error", "words"),
    [
        (b"\x10\x00\x00", casc.CascMalformedError, "too short"),
        (idx_bytes(6, _GOOD)[:20], casc.CascMalformedError, "past the end"),
        (_flip(idx_bytes(6, _GOOD), 4), casc.CascMalformedError, "header hash"),
        (_flip(idx_bytes(6, _GOOD), 16), casc.CascMalformedError, "header hash"),
        (idx_bytes(6, _GOOD, header_hash=0), casc.CascMalformedError, "header hash"),
        (b"\x08\x00\x00\x00" + bytes(60), casc.CascMalformedError, "header of 8 bytes"),
        (b"\x00\x10\x00\x00" + bytes(60), casc.CascMalformedError, "header of 4096 bytes"),
        (idx_bytes(6, _GOOD, version=6), casc.CascUnsupportedError, "index version 6"),
        (idx_bytes(6, _GOOD, spec=(4, 5, 9, 31)), casc.CascUnsupportedError, "4/5/9/31"),
        (idx_bytes(6, _GOOD, spec=(4, 6, 9, 30)), casc.CascUnsupportedError, "4/6/9/30"),
        (idx_bytes(7, _GOOD), casc.CascMalformedError, "says bucket 7"),
        (idx_bytes(6, _GOOD)[:40], casc.CascMalformedError, "runs past the end"),
        (idx_bytes(6, _GOOD)[:32], casc.CascMalformedError, "no entries block"),
        (idx_bytes(6, _GOOD, entries_size=19), casc.CascMalformedError, "not whole"),
        (idx_bytes(6, _GOOD, entries_hash=1), casc.CascMalformedError, "entries hash"),
        (_flip(idx_bytes(6, _GOOD), 50), casc.CascMalformedError, "entries hash"),
        (idx_bytes(6, _GOOD[::-1], sort=False), casc.CascMalformedError, "rising key order"),
        (idx_bytes(6, [_GOOD[0], _GOOD[0]]), casc.CascMalformedError, "rising key order"),
    ],
    ids=[
        "shorter-than-a-header",
        "header-past-end",
        "header-hash-field-flipped",
        "header-byte-flipped",
        "header-hash-zero",
        "header-too-small",
        "header-too-large",
        "version-6",
        "offset-bits-31",
        "offset-bytes-6",
        "bucket-not-the-names",
        "entries-past-end",
        "no-entries-block",
        "entries-not-whole",
        "entries-hash-wrong",
        "entry-byte-flipped",
        "entries-unsorted",
        "entries-duplicated",
    ],
)
def test_a_malformed_index_file_is_refused_constructed(
    tmp_path: Path, blob: bytes, error: type[casc.CascError], words: str
) -> None:
    root = _write(tmp_path, {idx_name(6, 0x41): blob})
    with pytest.raises(error, match=words) as caught:
        casc.load_index(root)
    assert caught.value.path is not None and caught.value.path.name == idx_name(6, 0x41)


def test_a_malformed_newest_version_is_refused_not_passed_over_constructed(
    tmp_path: Path,
) -> None:
    """A half-written newest file is refused; the older one is not read
    in its place (which version wins is **[verify]**; this never guesses)."""
    root = _write(
        tmp_path,
        {
            idx_name(6, 1): idx_bytes(6, _GOOD),
            idx_name(6, 2): idx_bytes(6, _GOOD, entries_hash=7),
        },
    )
    with pytest.raises(casc.CascMalformedError, match=r"0600000002\.idx: entries hash"):
        casc.load_index(root)


def test_a_bucket_past_sixteen_is_refused_constructed(tmp_path: Path) -> None:
    root = _write(tmp_path, {"1000000001.idx": idx_bytes(0, [])})
    with pytest.raises(casc.CascUnsupportedError, match="bucket 16"):
        casc.load_index(root)


def test_no_index_file_is_missing_constructed(tmp_path: Path) -> None:
    root = _write(tmp_path, {"shmem": b"x"})
    with pytest.raises(casc.CascMissingError, match=r"no \.idx file"):
        casc.load_index(root)


def test_no_data_folder_is_missing_constructed(tmp_path: Path) -> None:
    with pytest.raises(casc.CascMissingError, match="is not there"):
        casc.load_index(tmp_path)


def test_an_oversized_index_file_is_refused_unread_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(casc, "_INDEX_LIMIT", 100)
    root = _write(tmp_path, {idx_name(6, 1): idx_bytes(6, _GOOD, pad_to=101)})
    with pytest.raises(casc.CascUnreadableError, match="more than 100"):
        casc.load_index(root)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_a_linked_data_folder_is_not_followed_constructed(tmp_path: Path) -> None:
    elsewhere = _write(tmp_path / "elsewhere", {idx_name(6, 1): idx_bytes(6, _GOOD)})
    root = tmp_path / "install"
    (root / "Data").mkdir(parents=True)
    (root / "Data" / "data").symlink_to(elsewhere / "Data" / "data", target_is_directory=True)
    with pytest.raises(casc.CascUnreadableError, match="is a link"):
        casc.load_index(root)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_a_linked_index_file_is_not_followed_constructed(tmp_path: Path) -> None:
    root = _write(tmp_path, {})
    (tmp_path / "real.idx").write_bytes(idx_bytes(6, _GOOD))
    (root / "Data" / "data" / idx_name(6, 1)).symlink_to(tmp_path / "real.idx")
    with pytest.raises(casc.CascUnreadableError, match=idx_name(6, 1)):
        casc.load_index(root)


# ─── the build config ────────────────────────────────────────────────────────

ROOT_HEX = "0123456789abcdef" * 2
CONTENT_HEX = "fedcba9876543210" * 2
ENCODED_HEX = "00112233445566778899aabbccddeeff"


def test_build_config_is_read_with_its_pairs_constructed() -> None:
    text = build_config_text(
        ROOT_HEX.upper(), CONTENT_HEX, ENCODED_HEX, extra="odd line\nvfs-1 = a b\n"
    )
    config = casc.parse_build_config(text, md5(text))
    assert (config.root, config.encoding_ckey, config.encoding_ekey) == (
        ROOT_HEX,
        CONTENT_HEX,
        ENCODED_HEX,
    )
    assert config.key == md5(text)
    assert config.pairs[0] == ("root", ROOT_HEX.upper())
    assert ("odd line", "") in config.pairs and ("vfs-1", "a b") in config.pairs
    assert not any(name.startswith("#") for name, _ in config.pairs)


def test_build_config_with_crlf_lines_constructed() -> None:
    text = build_config_text(ROOT_HEX, CONTENT_HEX, ENCODED_HEX).replace(b"\n", b"\r\n")
    assert casc.parse_build_config(text, md5(text)).encoding_ekey == ENCODED_HEX


@pytest.mark.parametrize(
    ("text", "words"),
    [
        (
            build_config_text(ROOT_HEX, CONTENT_HEX, ENCODED_HEX).replace(b"root = ", b"rot = "),
            "0 'root'",
        ),
        (
            build_config_text(ROOT_HEX, CONTENT_HEX, ENCODED_HEX, extra=f"root = {ROOT_HEX}\n"),
            "2 'root'",
        ),
        (build_config_text(ROOT_HEX, CONTENT_HEX, ""), "'encoding' has 1 values"),
        (build_config_text("xyz", CONTENT_HEX, ENCODED_HEX), "not a 32-digit hex key"),
        (build_config_text(ROOT_HEX[:30], CONTENT_HEX, ENCODED_HEX), "not a 32-digit hex key"),
        (build_config_text(ROOT_HEX, CONTENT_HEX, ENCODED_HEX) + b"\xff\xfe", "not UTF-8"),
    ],
    ids=["no-root", "two-roots", "one-encoding-key", "root-not-hex", "root-short", "not-utf8"],
)
def test_a_malformed_build_config_is_refused_constructed(text: bytes, words: str) -> None:
    with pytest.raises(casc.CascMalformedError, match=words):
        casc.parse_build_config(text, md5(text))


def test_a_build_config_that_is_not_its_md5_is_refused_constructed() -> None:
    text = build_config_text(ROOT_HEX, CONTENT_HEX, ENCODED_HEX)
    with pytest.raises(casc.CascMalformedError, match="MD5"):
        casc.parse_build_config(text[:-1], md5(text))


@pytest.mark.parametrize("key", ["../../../../etc/x", "AB" * 15, "", "g" * 32])
def test_a_build_key_that_is_not_hex_names_no_path_constructed(tmp_path: Path, key: str) -> None:
    with pytest.raises(casc.CascMalformedError, match="not a 32-digit hex key"):
        casc.read_build_config(tmp_path, key)


# ─── the build row ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rows", "count"),
    [
        ([("0", PRODUCT)], 0),
        ([("1", "wow_other_product")], 0),
        ([("1", PRODUCT), ("1", PRODUCT)], 2),
        ([("1", PRODUCT), ("2", PRODUCT), ("1", "wow_other_product")], 2),
    ],
    ids=["inactive", "other-product-only", "two-active", "two-active-and-another"],
)
def test_a_product_without_exactly_one_active_row_is_refused_constructed(
    tmp_path: Path, rows: list[tuple[str, str]], count: int
) -> None:
    root, _ = make_install(tmp_path, rows=rows)
    with pytest.raises(casc.BuildRowError, match=f"has {count} Active rows") as caught:
        casc.open_storage(read_install(root), FLAVOR)
    assert (caught.value.product, caught.value.count) == (PRODUCT, count)


def test_one_active_row_among_others_is_taken_constructed(tmp_path: Path) -> None:
    root, _ = make_install(
        tmp_path, rows=[("0", PRODUCT), ("1", "wow_other_product"), ("1", PRODUCT)]
    )
    storage = casc.open_storage(read_install(root), FLAVOR)
    assert storage.row.is_active and storage.row.product == PRODUCT


def test_a_flavor_not_in_the_install_is_refused_constructed(tmp_path: Path) -> None:
    root, _ = make_install(tmp_path)
    with pytest.raises(casc.BuildRowError, match="no flavor folder '_nope_'"):
        casc.open_storage(read_install(root), "_nope_")


def test_a_flavor_naming_no_product_is_refused_constructed(tmp_path: Path) -> None:
    root, _ = make_install(tmp_path)
    (root / FLAVOR / ".flavor.info").write_text("Product Flavor!STRING:0\n", encoding="utf-8")
    with pytest.raises(casc.BuildRowError, match="names no product"):
        casc.open_storage(read_install(root), FLAVOR)


def test_a_missing_build_config_is_missing_constructed(tmp_path: Path) -> None:
    root, _ = make_install(tmp_path, build_key="0f" * 16, write_config=False)
    with pytest.raises(casc.CascMissingError, match="is not there"):
        casc.open_storage(read_install(root), FLAVOR)


def test_an_active_row_without_a_build_key_is_refused_constructed(tmp_path: Path) -> None:
    root, _ = make_install(tmp_path, build_key="")
    with pytest.raises(casc.CascMalformedError, match="Build Key"):
        casc.open_storage(read_install(root), FLAVOR)


# ─── the whole open, and L1 ──────────────────────────────────────────────────


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, int, int]]]:
    """Every `os.open` while the test runs: (path, flags, descriptor); and
    every descriptor closed is dropped from `still_open`."""
    calls: list[tuple[str, int, int]] = []
    real_open, real_close = os.open, os.close
    still_open: set[int] = set()

    def spy_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        fd = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        calls.append((os.fsdecode(path), flags, fd))  # type: ignore[arg-type]
        still_open.add(fd)
        return fd

    def spy_close(fd: int) -> None:
        still_open.discard(fd)
        real_close(fd)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "close", spy_close)
    yield calls
    assert not still_open, f"descriptors left open: {still_open}"


def test_open_storage_reads_only_what_it_needs_and_writes_nothing_constructed(
    tmp_path: Path, opened: list[tuple[str, int, int]]
) -> None:
    root, indexes = make_install(tmp_path)
    install = read_install(root)
    before = tree_state(root)
    opened.clear()

    storage = casc.open_storage(install, FLAVOR)

    assert tree_state(root) == before
    names = sorted(Path(p).name for p, _, _ in opened)
    assert names == sorted([storage.config.key, *indexes])
    writing = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    assert all(flags & writing == 0 for _, flags, _ in opened)
    report = storage.report
    assert (report.product, report.version, report.build) == (PRODUCT, "9.8.7.65432", 65432)
    assert report.build_key == storage.config.key == storage.row.build_key
    assert (report.root, report.encoding_ckey) == ("ab" * 16, "cd" * 16)
    assert (report.bucket_count, report.entry_count) == (2, 10)
    assert [v.bucket for v in report.versions] == [3, 9] and report.newest_taken == ()
    assert 0 <= report.index_seconds <= report.open_seconds
    ekey = bytes.fromhex(report.encoding_ekey)
    assert storage.index.find(ekey) == casc.IndexEntry(archive=0, offset=64, size=1000)
    assert casc.CascIndexReport.model_validate_json(report.model_dump_json()) == report


def test_the_report_names_buckets_where_the_newest_was_taken_constructed(tmp_path: Path) -> None:
    rng = random.Random(5)
    rows = [(key_in(3, rng), 0, 0, 30)]
    root, _ = make_install(
        tmp_path,
        indexes={idx_name(3, 1): idx_bytes(3, rows), idx_name(3, 9): idx_bytes(3, rows)},
    )
    report = casc.open_storage(read_install(root), FLAVOR).report
    assert report.newest_taken == (3,)
    assert report.versions[0].not_taken == (1,) and report.versions[0].version == 9


def test_entry_bytes_layout_is_the_formats_constructed() -> None:
    """Pins the helper the other tests build with to LAB_FORMATS §10."""
    blob = entry_bytes([(bytes(range(9)), 0x3FF, 0x3FFFFFFF, 0x01020304)])
    assert blob == bytes(range(9)) + b"\xff\xff\xff\xff\xff" + b"\x04\x03\x02\x01"
