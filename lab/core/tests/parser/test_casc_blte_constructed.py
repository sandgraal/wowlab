"""`casc`'s archive records and BLTE on constructed files (M15-04; ADR-0033
layer a, every test labelled `constructed`): blocks `N` and `Z` with known
decoded bytes, `E`, `4`, `F` and unknown modes refused with their typed
errors, a wrong chunk MD5, the cap, every truncation, chunk tables that
disagree with the bytes, the 30-byte record header, and the archive read
(one bounded, ranged, read-only read; the descriptor closed; no `mmap`).

The real install is graded by the derived fixture
(`test_casc_blte_derived.py`) and by hand (`test_casc_blte_live.py`, `@live`).
"""

from __future__ import annotations

import hashlib
import mmap
import os
import random
import sys
import tracemalloc
import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from _casc_build import (
    FLAVOR,
    RECORD_HEADER,
    blte,
    e_block,
    idx_bytes,
    idx_name,
    key_in,
    make_install,
    n_block,
    record,
    tree_state,
    z_block,
)

from wowlab_core import casc
from wowlab_core.install import read_install

pytestmark = pytest.mark.parser

TEXT = b"The quick brown fox jumps over the lazy dog. " * 40
ZEROS = bytes(5000)


# ─── N and Z, known bytes ────────────────────────────────────────────────────


def test_n_and_z_blocks_decode_to_their_known_bytes_constructed() -> None:
    data = blte([n_block(b"head:"), z_block(TEXT), n_block(b""), z_block(ZEROS, 9)])
    assert casc.decode_blte(data) == b"head:" + TEXT + ZEROS
    layout = casc.parse_blte(data)
    assert [b.mode for b in layout.blocks] == ["N", "Z", "N", "Z"]
    assert [b.decoded_size for b in layout.blocks] == [5, len(TEXT), 0, len(ZEROS)]
    assert layout.header_size == 12 + 24 * 4
    assert layout.encoded_size == len(data)
    assert layout.decoded_size == 5 + len(TEXT) + len(ZEROS)
    assert (
        layout.blocks[1].md5
        == hashlib.md5(data[layout.header_size + 6 :][: layout.blocks[1].encoded_size]).hexdigest()
    )


def test_a_blte_without_a_chunk_table_is_one_block_constructed() -> None:
    plain = blte([n_block(TEXT)], table=False)
    packed = blte([z_block(TEXT)], table=False)
    assert casc.decode_blte(plain) == casc.decode_blte(packed) == TEXT
    layout = casc.parse_blte(packed)
    assert layout.header_size == 0 and layout.decoded_size is None
    (only,) = layout.blocks
    assert (only.mode, only.md5, only.decoded_size) == ("Z", None, None)


def test_decode_takes_any_byte_buffer_constructed() -> None:
    data = blte([n_block(b"abc"), z_block(TEXT)])
    want = b"abc" + TEXT
    assert casc.decode_blte(bytearray(data)) == want
    assert casc.decode_blte(memoryview(data)) == want
    padded = b"xx" + data
    assert casc.decode_blte(memoryview(padded)[2:]) == want


# ─── refused modes ───────────────────────────────────────────────────────────


def test_an_e_block_is_refused_with_its_key_name_constructed() -> None:
    data = blte([n_block(b"clear"), e_block()])
    with pytest.raises(casc.EncryptedContent) as caught:
        casc.decode_blte(data)
    # The key name is stored little-endian; key lists print it as a number.
    assert caught.value.key_name == "50505078126ACB3E"
    assert (caught.value.block, caught.value.cipher) == (1, "S")
    assert "ADR-0033" in str(caught.value)
    assert isinstance(caught.value, casc.CascError)


def test_an_e_block_with_an_arc4_cipher_and_a_four_byte_iv_constructed() -> None:
    data = blte([e_block(iv=b"\x00\x01\x02\x03", cipher=b"A")])
    with pytest.raises(casc.EncryptedContent) as caught:
        casc.decode_blte(data)
    assert (caught.value.block, caught.value.cipher) == (0, "A")


@pytest.mark.parametrize(
    "body",
    [b"", b"\x00", b"\x08" + bytes(7), b"\x08" + bytes(8), b"\x08" + bytes(8) + b"\x08" + bytes(8)],
    ids=["empty", "no-key", "short-key", "no-iv", "no-cipher"],
)
def test_a_cut_short_e_block_is_malformed_constructed(body: bytes) -> None:
    data = blte([(b"E" + body, 10)])
    with pytest.raises(casc.CascMalformedError, match="encrypted"):
        casc.decode_blte(data)


@pytest.mark.parametrize(
    ("mode", "text"),
    [(b"4", "4"), (b"F", "F"), (b"X", "X"), (b"\x00", "\\x00"), (b"n", "n"), (b"\xff", "\\xff")],
    ids=["lz4", "nested", "unknown", "nul", "lowercase-n", "ff"],
)
def test_other_modes_are_unsupported_constructed(mode: bytes, text: str) -> None:
    data = blte([n_block(b"ok"), (mode + b"payload", 7)])
    with pytest.raises(casc.UnsupportedBlockMode) as caught:
        casc.decode_blte(data)
    assert (caught.value.mode, caught.value.block) == (text, 1)
    assert isinstance(caught.value, casc.CascUnsupportedError)


def test_an_f_block_is_never_recursed_into_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nested BLTE inside an `F` block, itself declaring a decoded size
    far past the cap and nesting a thousand deep: refused at the first
    level, the nested header never parsed."""
    inner = blte([n_block(b"x")], decoded=[2**32 - 1])
    for _ in range(1000):
        inner = blte([(b"F" + inner, 1)])
    calls: list[int] = []
    real = casc.parse_blte

    def spy(data: object, path: object = None) -> casc.BlteLayout:
        calls.append(1)
        return real(data, path)  # type: ignore[arg-type]

    monkeypatch.setattr(casc, "parse_blte", spy)
    with pytest.raises(casc.UnsupportedBlockMode) as caught:
        casc.decode_blte(inner)
    assert caught.value.mode == "F" and "never recursed" in str(caught.value)
    assert calls == [1]


def test_a_refused_block_stops_the_read_before_anything_is_decoded_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every block's MD5 and mode are checked before any block is decoded:
    a `Z` block ahead of an `E` block is never inflated."""
    inflated: list[int] = []
    real = zlib.decompressobj

    def spy(*args: object, **kwargs: object) -> object:
        inflated.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(zlib, "decompressobj", spy)
    with pytest.raises(casc.EncryptedContent):
        casc.decode_blte(blte([z_block(TEXT), e_block()]))
    assert inflated == []


# ─── MD5 and the chunk table against the bytes ───────────────────────────────


def test_a_wrong_chunk_md5_is_refused_constructed() -> None:
    blocks = [n_block(b"abc"), z_block(TEXT)]
    data = blte(blocks, md5s=[None, bytes(16)])
    with pytest.raises(casc.CascMalformedError, match="block 1's MD5"):
        casc.decode_blte(data)


def test_one_flipped_payload_byte_is_caught_by_the_md5_constructed() -> None:
    data = bytearray(blte([n_block(TEXT)]))
    data[-1] ^= 0x01
    with pytest.raises(casc.CascMalformedError, match="MD5"):
        casc.decode_blte(data)


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"encoded": [None, 10_000]}, "add up to"),
        ({"encoded": [None, 3]}, "add up to"),
        ({"header_size": 10_000}, "runs past the end"),
        ({"header_size": 11}, "holds no chunk table"),
        ({"header_size": 12 + 24 * 3}, "cannot hold 2 chunk rows"),
        ({"count": 3}, "cannot hold 3 chunk rows"),
        ({"count": 0}, "no blocks"),
    ],
    ids=[
        "sizes-exceed-the-file",
        "sizes-short-of-the-file",
        "header-past-the-end",
        "header-too-small",
        "header-size-not-the-rows",
        "count-not-the-rows",
        "count-zero",
    ],
)
def test_a_chunk_table_that_disagrees_with_the_bytes_is_refused_constructed(
    kwargs: dict[str, object], words: str
) -> None:
    data = blte([n_block(b"abc"), z_block(TEXT)], **kwargs)  # type: ignore[arg-type]
    with pytest.raises(casc.CascMalformedError, match=words):
        casc.parse_blte(data)
    with pytest.raises(casc.CascMalformedError):
        casc.decode_blte(data)


@pytest.mark.parametrize("count", [casc.BLOCK_LIMIT + 1, 100_000], ids=["one-over", "100k"])
def test_a_block_count_over_the_limit_is_refused_before_the_rows_are_read_constructed(
    count: int,
) -> None:
    """A header whose size does hold `count` rows (zero rows, there in the
    data) is refused on the count before a row is unpacked: the peak
    allocation stays far below what the rows would cost."""
    header_size = 12 + 24 * count
    data = b"BLTE" + header_size.to_bytes(4, "big") + b"\x0f" + count.to_bytes(3, "big")
    data += bytes(header_size - 12) + b"N" * count
    tracemalloc.start()
    try:
        with pytest.raises(casc.CascTooLargeError) as caught:
            casc.decode_blte(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert (caught.value.size, caught.value.limit) == (count, casc.BLOCK_LIMIT)
    assert peak < 1024 * 1024, peak


def test_the_block_limit_is_far_above_the_most_met_constructed() -> None:
    assert casc.BLOCK_LIMIT == 65_536 > 913  # the most in any record at 70450


def test_a_table_at_the_block_limit_is_read_constructed() -> None:
    blocks = [n_block(bytes([i % 256])) for i in range(casc.BLOCK_LIMIT)]
    data = blte(blocks)
    assert casc.decode_blte(data) == bytes(i % 256 for i in range(casc.BLOCK_LIMIT))


def test_trailing_bytes_after_the_last_block_are_refused_constructed() -> None:
    data = blte([n_block(b"abc")]) + b"tail"
    with pytest.raises(casc.CascMalformedError, match="add up to"):
        casc.decode_blte(data)


def test_a_block_of_zero_bytes_is_refused_constructed() -> None:
    data = blte([n_block(b"abc"), (b"", 0)])
    with pytest.raises(casc.CascMalformedError, match="no room for its mode byte"):
        casc.parse_blte(data)


def test_an_unknown_chunk_table_flag_is_unsupported_constructed() -> None:
    with pytest.raises(casc.CascUnsupportedError, match="flag 0x10"):
        casc.parse_blte(blte([n_block(b"abc")], flag=0x10))


@pytest.mark.parametrize(
    "data",
    [b"", b"BLT", b"BLTE", b"BLTE\x00\x00\x00", b"BLTE\x00\x00\x00\x00"],
    ids=["empty", "short-magic", "no-size", "short-size", "no-block"],
)
def test_no_room_is_malformed_constructed(data: bytes) -> None:
    with pytest.raises(casc.CascMalformedError):
        casc.decode_blte(data)


def test_a_record_that_is_not_blte_is_unsupported_constructed() -> None:
    """At 70450 one record is not BLTE: the patch manifest (magic `PA`)."""
    with pytest.raises(casc.CascUnsupportedError, match="not BLTE"):
        casc.decode_blte(b"PA\x02\x10" + bytes(60))


def test_a_header_truncated_at_every_boundary_is_malformed_constructed() -> None:
    """Every prefix of a valid BLTE, header and blocks alike, is refused
    with a typed error, never an `IndexError` or a `struct.error`."""
    data = blte([n_block(b"abc"), z_block(TEXT), n_block(b"end")])
    for cut in range(len(data)):
        with pytest.raises(casc.CascMalformedError):
            casc.decode_blte(data[:cut])
    assert casc.decode_blte(data) == b"abc" + TEXT + b"end"


def test_mutated_blte_is_decoded_or_refused_with_a_typed_error_constructed() -> None:
    """Two thousand seeded mutations of a valid BLTE (one to four bytes
    changed, or a cut): each decodes or raises a `CascError`, never
    anything else, and never decodes past the cap it was given."""
    data = blte([n_block(b"abc"), z_block(TEXT), e_block(), n_block(b"end")])
    rng = random.Random(1504)
    outcomes: set[str] = set()
    for _ in range(2000):
        mutant = bytearray(data)
        for _ in range(rng.randint(1, 4)):
            mutant[rng.randrange(len(mutant))] = rng.randrange(256)
        if rng.random() < 0.2:
            del mutant[rng.randrange(len(mutant)) :]
        try:
            out = casc.decode_blte(mutant, limit=4096)
        except casc.CascError as exc:
            outcomes.add(type(exc).__name__)
        else:
            assert len(out) <= 4096
            outcomes.add("decoded")
    assert {"CascMalformedError", "EncryptedContent"} <= outcomes


# ─── block contents against the table ────────────────────────────────────────


def test_a_plain_block_whose_size_is_not_the_tables_is_refused_constructed() -> None:
    with pytest.raises(casc.CascMalformedError, match="plain and holds 3 bytes"):
        casc.decode_blte(blte([n_block(b"abc")], decoded=[4]))


@pytest.mark.parametrize(
    ("block", "words"),
    [
        ((b"Z" + b"not zlib at all", 10), "not a zlib stream"),
        ((b"Z" + zlib.compress(TEXT)[:-6], len(TEXT)), "cut short|inflates to"),
        ((b"Z" + zlib.compress(TEXT) + b"junk", len(TEXT)), "after its zlib stream"),
        ((b"Z" + zlib.compress(TEXT), len(TEXT) - 1), "past its declared"),
        ((b"Z" + zlib.compress(TEXT), len(TEXT) + 1), "inflates to"),
    ],
    ids=["not-zlib", "truncated-stream", "trailing-bytes", "longer-than-declared", "shorter"],
)
def test_a_zlib_block_that_disagrees_with_the_table_is_refused_constructed(
    block: tuple[bytes, int], words: str
) -> None:
    with pytest.raises(casc.CascMalformedError, match=words):
        casc.decode_blte(blte([block]))


# ─── the cap ─────────────────────────────────────────────────────────────────


def test_the_cap_is_stated_and_above_the_largest_real_file_constructed() -> None:
    assert casc.DECODE_LIMIT == 256 * 1024 * 1024
    assert casc.DECODE_LIMIT > 141_574_945  # the encoding file at 70450, decoded


def test_a_declared_size_over_the_cap_is_refused_before_anything_is_allocated_constructed() -> None:
    """A chunk table declaring 2**32 - 1 decoded bytes in each of 4096
    blocks: refused from the table alone, with the peak allocation a small
    fraction of one declared block."""
    blocks = [(b"N", 0)] * 4096
    data = blte(blocks, decoded=[2**32 - 1] * 4096)
    tracemalloc.start()
    try:
        with pytest.raises(casc.CascTooLargeError) as caught:
            casc.decode_blte(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert caught.value.size == 4096 * (2**32 - 1) and caught.value.limit == casc.DECODE_LIMIT
    assert peak < 8 * 1024 * 1024, peak


def test_over_a_small_cap_and_at_it_constructed() -> None:
    data = blte([n_block(b"abc"), z_block(TEXT)])
    size = 3 + len(TEXT)
    assert casc.decode_blte(data, limit=size) == b"abc" + TEXT
    with pytest.raises(casc.CascTooLargeError):
        casc.decode_blte(data, limit=size - 1)


def test_a_zlib_bomb_never_inflates_past_its_declared_size_constructed() -> None:
    """64 MiB of zeros in about 64 KB of zlib, declared as 100 bytes: the
    inflate stops at 101 bytes and the block is refused."""
    bomb = zlib.compress(bytes(64 * 1024 * 1024), 9)
    tracemalloc.start()
    try:
        with pytest.raises(casc.CascMalformedError, match="past its declared 100"):
            casc.decode_blte(blte([(b"Z" + bomb, 100)]))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024, peak


def test_a_chunkless_zlib_bomb_stops_at_the_cap_constructed() -> None:
    bomb = zlib.compress(bytes(64 * 1024 * 1024), 9)
    tracemalloc.start()
    try:
        with pytest.raises(casc.CascTooLargeError) as caught:
            casc.decode_blte(blte([(b"Z" + bomb, 0)], table=False), limit=1 << 20)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert caught.value.limit == 1 << 20
    assert peak < 4 * 1024 * 1024, peak


def test_a_chunkless_plain_block_over_the_cap_is_refused_constructed() -> None:
    with pytest.raises(casc.CascTooLargeError):
        casc.decode_blte(blte([n_block(TEXT)], table=False), limit=len(TEXT) - 1)
    assert casc.decode_blte(blte([n_block(TEXT)], table=False), limit=len(TEXT)) == TEXT


# ─── the record header ───────────────────────────────────────────────────────

KEY = bytes.fromhex("0123456789abcdef0011223344556677")


def _entry(size: int, archive: int = 1, offset: int = 100) -> casc.IndexEntry:
    return casc.IndexEntry(archive=archive, offset=offset, size=size)


@pytest.mark.parametrize("full_key", [False, True], ids=["nine-bytes", "whole-key"])
def test_the_record_header_is_read_and_checked_constructed(full_key: bool) -> None:
    data = blte([n_block(b"abc")])
    rec = record(KEY, data, full_key=full_key)
    header = casc.parse_record_header(rec, KEY[:9], _entry(len(rec)))
    want_field = KEY.hex() if full_key else KEY[:9].hex() + "00" * 7
    assert header.hash_field == want_field
    assert (header.size, header.flags, header.checksum_b) == (len(rec), "0000", 0x12345678)
    assert header.checksum_a == casc.hashlittle(rec[:22], 0x3D6BE971)


@pytest.mark.parametrize(
    ("kwargs", "key", "words"),
    [
        ({}, bytes(9), "is for the key"),
        ({"size": 999}, KEY, "says 999 bytes"),
        ({"checksum_a": 0}, KEY, "checksum"),
    ],
    ids=["other-key", "other-size", "bad-checksum-a"],
)
def test_a_record_header_that_disagrees_with_the_index_is_refused_constructed(
    kwargs: dict[str, object], key: bytes, words: str
) -> None:
    data = blte([n_block(b"abc")])
    rec = record(KEY, data, **kwargs)  # type: ignore[arg-type]
    size = RECORD_HEADER + len(data)
    with pytest.raises(casc.CascMalformedError, match=words):
        casc.parse_record_header(rec, key, _entry(size))


def test_checksum_b_is_reported_not_checked_constructed() -> None:
    rec = record(KEY, b"", checksum_b=0)
    assert casc.parse_record_header(rec, KEY, _entry(RECORD_HEADER)).checksum_b == 0


def test_a_record_header_cut_short_at_every_length_is_malformed_constructed() -> None:
    rec = record(KEY, b"")
    for cut in range(RECORD_HEADER):
        with pytest.raises(casc.CascMalformedError, match="30 bytes"):
            casc.parse_record_header(rec[:cut], KEY, _entry(RECORD_HEADER))


# ─── the archive read ────────────────────────────────────────────────────────

_AUDIT: list[list[tuple[str, int]]] = []


def _audit(event: str, args: tuple[object, ...]) -> None:
    if event == "open" and _AUDIT:
        path, _mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            _AUDIT[-1].append((os.fsdecode(path), flags if isinstance(flags, int) else 0))


sys.addaudithook(_audit)
_WRITING = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, int]]]:
    """Every open while the test runs (path, flags), by any API; every
    descriptor `os.open` returns must be closed by the end; `mmap` is never
    called."""
    calls: list[tuple[str, int]] = []
    real_open, real_close = os.open, os.close
    still_open: set[int] = set()

    def spy_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        fd = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        still_open.add(fd)
        return fd

    def spy_close(fd: int) -> None:
        still_open.discard(fd)
        real_close(fd)

    def no_mmap(*args: object, **kwargs: object) -> None:
        raise AssertionError("mmap is never used")

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "close", spy_close)
    monkeypatch.setattr(mmap, "mmap", no_mmap)
    _AUDIT.append(calls)
    try:
        yield calls
    finally:
        _AUDIT.remove(calls)
    assert not still_open, f"descriptors left open: {still_open}"


def _install(tmp_path: Path) -> tuple[Path, casc.CascStorage, dict[str, tuple[bytes, bytes]]]:
    """A constructed install whose `data.001` holds three records between
    filler bytes: a plain-and-zlib file, an encrypted one, and one whose
    header names another key. Returns the root, the opened storage and, by
    name, each record's key and decoded bytes (empty when not decodable)."""
    rng = random.Random(1504)
    good, secret, liar = key_in(3, rng) + bytes(7), key_in(3, rng) + bytes(7), key_in(3, rng)
    files = {
        "good": (good, blte([n_block(b"abc"), z_block(TEXT)]), b"abc" + TEXT),
        "secret": (secret, blte([e_block()]), b""),
        "liar": (liar, blte([n_block(b"x")]), b""),
    }
    archive = bytearray(rng.randbytes(100))
    rows = []
    for name, (key, data, _) in files.items():
        rec = record(bytes(9) if name == "liar" else key, data)
        rows.append((key, 1, len(archive), len(rec)))
        archive += rec + rng.randbytes(50)
    root, _ = make_install(tmp_path, indexes={idx_name(3, 1): idx_bytes(3, rows)})
    (root / "Data" / "data" / "data.001").write_bytes(bytes(archive))
    storage = casc.open_storage(read_install(root), FLAVOR)
    return root, storage, {name: (key, plain) for name, (key, _, plain) in files.items()}


def test_read_encoded_decodes_a_file_from_its_archive_constructed(
    tmp_path: Path, opened: list[tuple[str, int]]
) -> None:
    root, storage, files = _install(tmp_path)
    before = tree_state(root)
    opened.clear()
    key, plain = files["good"]
    assert casc.read_encoded(storage, key) == plain
    assert [Path(p).name for p, _ in opened] == ["data.001"]
    assert all(flags & _WRITING == 0 for _, flags in opened)
    assert tree_state(root) == before


def test_read_encoded_refuses_encrypted_and_mismatched_records_constructed(
    tmp_path: Path, opened: list[tuple[str, int]]
) -> None:
    _, storage, files = _install(tmp_path)
    with pytest.raises(casc.EncryptedContent) as caught:
        casc.read_encoded(storage, files["secret"][0])
    assert caught.value.path is not None and caught.value.path.name == "data.001"
    with pytest.raises(casc.CascMalformedError, match="is for the key"):
        casc.read_encoded(storage, files["liar"][0])


def test_read_encoded_says_none_for_a_key_not_listed_constructed(tmp_path: Path) -> None:
    _, storage, _ = _install(tmp_path)
    rng = random.Random(9)
    assert casc.read_encoded(storage, key_in(3, rng)) is None
    with pytest.raises(casc.BucketMissingError):
        casc.read_encoded(storage, key_in(4, rng))


def test_read_record_reads_only_the_range_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes asked of the operating system add up to the record's size:
    not the archive, not one byte past the range."""
    _, storage, files = _install(tmp_path)
    entry = storage.index.find(files["good"][0])
    assert entry is not None
    asked: list[int] = []
    real_read = os.read

    def spy_read(fd: int, n: int) -> bytes:
        asked.append(n)
        return real_read(fd, n)

    monkeypatch.setattr(os, "read", spy_read)
    rec = casc.read_record(storage.root, entry)
    assert len(rec) == entry.size and sum(asked) == entry.size


def test_read_record_refuses_over_the_cap_before_opening_constructed(
    tmp_path: Path, opened: list[tuple[str, int]]
) -> None:
    root, _, _ = _install(tmp_path)
    opened.clear()
    huge = casc.IndexEntry(archive=1, offset=0, size=casc.DECODE_LIMIT + RECORD_HEADER + 1)
    with pytest.raises(casc.CascTooLargeError):
        casc.read_record(root, huge)
    with pytest.raises(casc.CascTooLargeError):
        casc.read_record(root, casc.IndexEntry(archive=1, offset=0, size=1000), limit=100)
    assert opened == []


@pytest.mark.parametrize(
    ("entry", "error", "words"),
    [
        ((1, 0, 29), casc.CascMalformedError, "cannot hold a record header"),
        ((1, 10_000, 100), casc.CascMalformedError, "past the end of data.001"),
        ((1, 0, 10_000), casc.CascMalformedError, "past the end of data.001"),
        ((7, 0, 100), casc.CascMissingError, "not there"),
    ],
    ids=["entry-too-small", "offset-past-the-end", "size-past-the-end", "no-archive"],
)
def test_read_record_refusals_constructed(
    tmp_path: Path,
    opened: list[tuple[str, int]],
    entry: tuple[int, int, int],
    error: type[Exception],
    words: str,
) -> None:
    root, _, _ = _install(tmp_path)
    archive, offset, size = entry
    with pytest.raises(error, match=words):
        casc.read_record(root, casc.IndexEntry(archive=archive, offset=offset, size=size))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symbolic links here")
def test_a_linked_archive_is_not_followed_constructed(tmp_path: Path) -> None:
    root, storage, files = _install(tmp_path)
    data = root / "Data" / "data"
    target = tmp_path / "elsewhere.bin"
    (data / "data.001").rename(target)
    try:
        (data / "data.001").symlink_to(target)
    except OSError:
        pytest.skip("cannot create a symbolic link here")
    with pytest.raises(casc.CascUnreadableError):
        casc.read_encoded(storage, files["good"][0])


def test_an_archive_that_is_a_folder_is_unreadable_constructed(tmp_path: Path) -> None:
    root, storage, files = _install(tmp_path)
    data = root / "Data" / "data"
    (data / "data.001").unlink()
    (data / "data.001").mkdir()
    with pytest.raises(casc.CascUnreadableError):
        casc.read_encoded(storage, files["good"][0])


def test_the_archive_name_has_at_least_three_digits_constructed() -> None:
    root = Path("r")
    assert casc.archive_path(root, 7).name == "data.007"
    assert casc.archive_path(root, 1023).name == "data.1023"
