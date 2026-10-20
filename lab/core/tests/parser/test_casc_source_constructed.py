"""`casc`'s encoding file, root file and `CascSource` on constructed files
(M15-05; ADR-0033 layer a, every test labelled `constructed`): a two-page
encoding with a content key of two encoding keys; roots in versions 1 and 2
with `NoNameHash`, `Encrypted`, a skipped locale, the platform masks and
`LowViolence`; hostile headers (a block count of 2**32 - 1, a page size of
zero, an offset past the file) refused with typed errors; the locale from
`Tags` and the platform from the layout; and a whole source: `stat`'s
states, `read` checked by the content key with the one re-read of the index,
an encrypted copy passed over for a plain one, and nothing written.

The real install is graded by the derived fixture
(`test_casc_source_derived.py`) and by hand (`test_casc_source_live.py`).
"""

from __future__ import annotations

import hashlib
import mmap
import os
import random
import struct
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from _casc_build import (
    FLAVOR,
    EncodingRow,
    blte,
    ckey_of,
    e_block,
    encoding_bytes,
    key_in,
    make_source_install,
    n_block,
    root_bytes,
    tree_state,
    z_block,
)

from wowlab_core import casc
from wowlab_core.install import read_install

pytestmark = pytest.mark.parser

ENUS = 0x2
FRFR = 0x10
ALL = 0xFFFFFFFF


def _ck(i: int) -> bytes:
    """A content key that sorts by `i`."""
    return bytes([i]) + hashlib.md5(bytes([i]), usedforsecurity=False).digest()[1:]


def _ek(i: int) -> bytes:
    return b"\xe0" + bytes([i]) * 15


# ─── encoding ────────────────────────────────────────────────────────────────


def _two_pages() -> tuple[bytes, list[EncodingRow]]:
    rows: list[EncodingRow] = [(_ck(i), 1000 + i, [_ek(i)]) for i in range(10, 70, 3)]
    rows[5] = (rows[5][0], rows[5][1], [_ek(200), _ek(201)])  # one content key, two copies
    return encoding_bytes([rows[:10], rows[10:]]), rows


def test_a_two_page_encoding_finds_every_row_constructed() -> None:
    data, rows = _two_pages()
    table = casc.parse_encoding(data)
    assert len(table) == 2
    assert table.header.ckey_page_size == 1024 and table.header.ckey_page_count == 2
    for ckey, size, ekeys in rows:
        found = table.lookup(ckey)
        assert found is not None
        assert (found.ckey, found.size, found.ekeys) == (ckey, size, tuple(ekeys))
    assert table.key_counts() == {1: len(rows) - 1, 2: 1}


def test_a_content_key_not_listed_is_none_constructed() -> None:
    table = casc.parse_encoding(_two_pages()[0])
    assert table.lookup(_ck(11)) is None  # between rows of page 0
    assert table.lookup(_ck(1)) is None  # before the first page
    assert table.lookup(b"\xff" * 16) is None  # past the last row
    with pytest.raises(ValueError, match="16 bytes"):
        table.lookup(b"\x01" * 9)


def test_a_bytearray_is_cut_to_the_content_key_pages_constructed() -> None:
    data, rows = _two_pages()
    buffer = bytearray(data)
    table = casc.parse_encoding(buffer)
    assert len(buffer) == len(data) - 40  # the tail standing for the spec pages
    assert table.lookup(rows[-1][0]) is not None


def test_a_page_size_of_zero_is_refused_constructed() -> None:
    data = bytearray(_two_pages()[0])
    data[5:7] = b"\x00\x00"
    with pytest.raises(casc.CascMalformedError, match="page size is zero"):
        casc.parse_encoding(bytes(data))


def test_pages_past_the_end_are_refused_before_they_are_read_constructed() -> None:
    _, rows = _two_pages()
    data = encoding_bytes([rows[:10], rows[10:]], page_count=0xFFFFFFFF, tail=b"")
    with pytest.raises(casc.CascMalformedError, match="past its"):
        casc.parse_encoding(data)
    whole = _two_pages()[0]
    with pytest.raises(casc.CascMalformedError, match="past its"):
        casc.parse_encoding(whole[: len(whole) - 41])


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"magic": b"NE"}, casc.CascMalformedError, "not an encoding file"),
        ({"version": 2}, casc.CascUnsupportedError, "encoding version 2"),
        ({"hash_sizes": (16, 9)}, casc.CascUnsupportedError, "hash sizes"),
    ],
)
def test_an_unknown_header_is_refused_constructed(
    kwargs: dict[str, object], error: type[Exception], match: str
) -> None:
    _, rows = _two_pages()
    with pytest.raises(error, match=match):
        casc.parse_encoding(encoding_bytes([rows[:10], rows[10:]], **kwargs))  # type: ignore[arg-type]


def test_a_short_encoding_is_refused_constructed() -> None:
    with pytest.raises(casc.CascMalformedError, match="no room"):
        casc.parse_encoding(b"EN\x01")


def test_a_page_index_out_of_order_is_refused_constructed() -> None:
    _, rows = _two_pages()
    data = encoding_bytes([rows[:10], rows[10:]], firsts=[None, bytes(16)])
    with pytest.raises(casc.CascMalformedError, match="rising"):
        casc.parse_encoding(data)


def test_a_page_whose_md5_is_wrong_is_refused_on_lookup_constructed() -> None:
    _, rows = _two_pages()
    table = casc.parse_encoding(encoding_bytes([rows[:10], rows[10:]], page_md5s=[None, bytes(16)]))
    assert table.lookup(rows[0][0]) is not None
    with pytest.raises(casc.CascMalformedError, match="page 1 does not match its MD5"):
        table.lookup(rows[-1][0])
    with pytest.raises(casc.CascMalformedError, match="page 1"):
        table.key_counts()


def test_an_entry_running_past_its_page_is_refused_constructed() -> None:
    """A key count of 255 on the page's last row: its keys would run past."""
    rows: list[EncodingRow] = [(_ck(i), 5, [_ek(i)]) for i in range(10, 20)]
    data = bytearray(encoding_bytes([rows]))
    pages_at = 22 + 4 + 32
    last = pages_at + 9 * 38
    data[last] = 255
    index_md5 = 22 + 4 + 16
    data[index_md5 : index_md5 + 16] = hashlib.md5(
        bytes(data[pages_at : pages_at + 1024]), usedforsecurity=False
    ).digest()
    table = casc.parse_encoding(bytes(data))
    with pytest.raises(casc.CascMalformedError, match="runs past the page"):
        table.lookup(rows[-1][0])
    with pytest.raises(casc.CascMalformedError, match="runs past the page"):
        table.key_counts()


# ─── root ────────────────────────────────────────────────────────────────────


def _keys(ids: list[int], salt: int = 0) -> list[bytes]:
    return [hashlib.md5(f"{salt}:{i}".encode(), usedforsecurity=False).digest() for i in ids]


@pytest.mark.parametrize("version", [1, 2])
def test_a_root_is_read_and_filtered_constructed(version: int) -> None:
    """Version 1 and 2 with the same blocks: one for every locale, one for
    enUS only, one for frFR only (skipped), one Windows-only (skipped on
    macOS), one LowViolence (skipped), one Encrypted, one NoNameHash."""
    plain = [5, 6, 9, 100]
    blocks = [
        (plain, _keys(plain), 0, ALL),
        ([7, 8], _keys([7, 8]), 0, ENUS),
        ([7, 300], _keys([7, 300], 1), 0, FRFR),
        ([400], _keys([400]), casc.ROOT_LOAD_ON_WINDOWS, ALL),
        ([500], _keys([500]), casc.ROOT_LOW_VIOLENCE, ALL),
        ([600, 601], _keys([600, 601]), casc.ROOT_ENCRYPTED, ALL),
        ([700, 900], _keys([700, 900]), casc.ROOT_NO_NAME_HASH, ALL),
    ]
    data = root_bytes(blocks, version=version, named=9)  # 14 rows, 9 named: NoNameHash applies
    table = casc.parse_root(data, locale="enUS", platform="macos")
    r = table.report
    assert (r.version, r.header_size, r.total_files, r.named_files) == (version, 24, 14, 9)
    assert (r.blocks, r.records, r.blocks_taken, r.records_taken) == (7, 14, 4, 10)
    assert (r.encrypted_blocks, r.encrypted_blocks_taken) == (1, 1)
    assert r.skipped_blocks == (("locale", 1), ("low_violence", 1), ("platform", 1))
    assert (r.locale, r.locale_flag, r.platform) == ("enUS", ENUS, "macos")
    assert list(table.ids()) == [5, 6, 9, 100, 7, 8, 600, 601, 700, 900]
    (hit,) = table.find(9)
    assert hit.ckey == _keys([9])[0] and hit.content_flags == 0 and hit.locale_flags == ALL
    assert table.find(300) == [] and table.skipped_flags(300) == [(0, FRFR)]
    assert table.find(400) == [] and table.skipped_flags(400) == [(casc.ROOT_LOAD_ON_WINDOWS, ALL)]
    assert table.find(901) == [] and table.skipped_flags(901) == []
    assert [h.ckey for h in table.find(7)] == _keys([7])
    assert table.find(900)[0].ckey == _keys([900])[0]  # read past a block with no name hashes
    assert sorted(f for f, _, _ in table.skipped_rows()) == [7, 300, 400, 500]
    assert [f for f, _, _, _ in table.rows()] == list(table.ids())
    assert r.empty_blocks == 0


def test_windows_skips_the_mac_only_block_not_the_windows_one_constructed() -> None:
    blocks = [
        ([1], _keys([1]), casc.ROOT_LOAD_ON_WINDOWS, ALL),
        ([2], _keys([2]), casc.ROOT_LOAD_ON_MACOS, ALL),
        ([3], _keys([3]), casc.ROOT_DO_NOT_LOAD, ALL),
    ]
    data = root_bytes(blocks)
    assert list(casc.parse_root(data, locale="enUS", platform="windows").ids()) == [1]
    assert list(casc.parse_root(data, locale="enUS", platform="macos").ids()) == [2]
    assert casc.PLATFORM_SKIP == {"macos": 0x108, "windows": 0x110}


def test_version_2_flags_are_the_two_words_and_the_byte_constructed() -> None:
    """`f1 | f2 | (f3 << 17)`: an Encrypted bit in the second word, a
    LowViolence bit in the first and a platform bit in neither."""
    blocks = [([1], _keys([1]), 0, ALL), ([2], _keys([2]), 0, ALL), ([3], _keys([3]), 0, ALL)]
    parts = [(0, casc.ROOT_ENCRYPTED, 0), (casc.ROOT_LOW_VIOLENCE, 0, 0), (0, 0, 4)]
    table = casc.parse_root(root_bytes(blocks, flag_parts=parts), locale="enUS", platform="macos")
    assert [h.content_flags for h in table.find(1)] == [casc.ROOT_ENCRYPTED]
    assert table.find(2) == []
    assert [h.content_flags for h in table.find(3)] == [4 << 17]


def test_another_locale_takes_its_own_blocks_constructed() -> None:
    blocks = [([1], _keys([1]), 0, ENUS), ([2], _keys([2]), 0, FRFR)]
    assert list(casc.parse_root(root_bytes(blocks), locale="frFR", platform="macos").ids()) == [2]


def test_ids_rise_from_their_deltas_and_a_block_may_be_empty_constructed() -> None:
    ids = [0, 1, 2, 10, 1_000_000, 2**31 - 1]
    blocks = [(ids, _keys(ids), 0, ALL), ([], [], 0, ALL)]
    table = casc.parse_root(root_bytes(blocks), locale="enUS", platform="macos")
    assert list(table.ids()) == ids
    assert table.find(2**31 - 1)[0].ckey == _keys(ids)[-1]


def test_a_block_count_of_two_to_the_32_is_refused_before_allocating_constructed() -> None:
    blocks = [([1, 2], _keys([1, 2]), 0, ALL)]
    data = root_bytes(blocks, counts=[0xFFFFFFFF])
    with pytest.raises(casc.CascMalformedError, match="lists 4294967295 files"):
        casc.parse_root(data, locale="enUS", platform="macos")


@pytest.mark.parametrize("cut", [1, 5, 17, 30])
def test_a_root_cut_short_is_refused_constructed(cut: int) -> None:
    data = root_bytes([([1, 2], _keys([1, 2]), 0, ALL), ([3], _keys([3]), 0, ALL)])
    with pytest.raises(casc.CascMalformedError):
        casc.parse_root(data[:-cut], locale="enUS", platform="macos")


def test_a_negative_delta_is_refused_constructed() -> None:
    data = bytearray(root_bytes([([1, 2], _keys([1, 2]), 0, ALL)]))
    struct.pack_into("<i", data, 24 + 17 + 4, -5)
    with pytest.raises(casc.CascMalformedError, match="negative"):
        casc.parse_root(bytes(data), locale="enUS", platform="macos")


def test_an_id_past_int32_is_refused_constructed() -> None:
    data = bytearray(root_bytes([([1, 2], _keys([1, 2]), 0, ALL)]))
    struct.pack_into("<i", data, 24 + 17 + 4, 2**31 - 1)
    with pytest.raises(casc.CascMalformedError, match="past int32"):
        casc.parse_root(bytes(data), locale="enUS", platform="macos")


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"magic": b"\x00\x00\x00\x00"}, casc.CascUnsupportedError, "not the MFST"),
        ({"header_size": 12}, casc.CascUnsupportedError, "before version 1"),
        ({"header_size": 1 << 30}, casc.CascMalformedError, "past the end"),
        ({"version": 3}, casc.CascUnsupportedError, "root version 3"),
    ],
)
def test_an_unknown_root_header_is_refused_constructed(
    kwargs: dict[str, object], error: type[Exception], match: str
) -> None:
    data = root_bytes([([1], _keys([1]), 0, ALL)], **kwargs)  # type: ignore[arg-type]
    with pytest.raises(error, match=match):
        casc.parse_root(data, locale="enUS", platform="macos")


def test_a_short_root_and_an_unknown_locale_or_platform_are_refused_constructed() -> None:
    with pytest.raises(casc.CascMalformedError, match="no room"):
        casc.parse_root(b"TSFM", locale="enUS", platform="macos")
    data = root_bytes([([1], _keys([1]), 0, ALL)])
    with pytest.raises(casc.CascLocaleError):
        casc.parse_root(data, locale="xxYY", platform="macos")
    with pytest.raises(casc.CascPlatformError):
        casc.parse_root(data, locale="enUS", platform="linux")  # type: ignore[arg-type]


# ─── locale and platform from the install ───────────────────────────────────

GEO = "geoip-ZZ?"  # an invented geo-IP token: never in a message


@pytest.mark.parametrize(
    ("tags", "locale"),
    [
        (f"OSX x86_64 ZZ? acct-ZZZ? {GEO} deDE speech?:OSX x86_64 ZZ? {GEO} frFR text?", "frFR"),
        ("Windows x86_64 enGB speech?:", "enGB"),
        ("enUS text?", "enUS"),
    ],
)
def test_the_locale_is_the_text_locale_else_the_speech_one_constructed(
    tags: str, locale: str
) -> None:
    assert casc.locale_of(tags) == locale


@pytest.mark.parametrize("tags", [None, "", f"OSX {GEO} text?", "OSX x86_64 enUS"])
def test_no_locale_is_refused_without_repeating_the_cell_constructed(tags: str | None) -> None:
    with pytest.raises(casc.CascLocaleError) as caught:
        casc.locale_of(tags)
    assert GEO not in str(caught.value) and "OSX" not in str(caught.value)


def test_an_unknown_locale_is_named_not_the_cell_constructed() -> None:
    with pytest.raises(casc.CascLocaleError, match="'xxYY'") as caught:
        casc.locale_of(f"OSX {GEO} xxYY text?")
    assert GEO not in str(caught.value)


def test_the_platform_is_the_layouts_constructed(tmp_path: Path) -> None:
    mac, win, both, none = (tmp_path / n for n in ("mac", "win", "both", "none"))
    for folder in (mac, win, both, none):
        folder.mkdir()
    (mac / "Some Client.app").mkdir()
    (win / "Client.EXE").write_bytes(b"MZ")
    (both / "A.app").mkdir()
    (both / "A.exe").write_bytes(b"MZ")
    (none / "readme.txt").write_text("x")
    (none / "fake.app").write_text("a file, not a bundle")
    (none / "fake.exe").mkdir()
    assert casc.platform_of(mac) == "macos"
    assert casc.platform_of(win) == "windows"
    with pytest.raises(casc.CascPlatformError, match="both"):
        casc.platform_of(both)
    with pytest.raises(casc.CascPlatformError, match="neither"):
        casc.platform_of(none)
    with pytest.raises(casc.CascUnreadableError):
        casc.platform_of(tmp_path / "missing")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symbolic links here")
def test_a_linked_bundle_is_not_followed_constructed(tmp_path: Path) -> None:
    (tmp_path / "real.app").mkdir()
    flavor = tmp_path / "flavor"
    flavor.mkdir()
    (flavor / "Client.app").symlink_to(tmp_path / "real.app", target_is_directory=True)
    with pytest.raises(casc.CascPlatformError, match="neither"):
        casc.platform_of(flavor)


# ─── a whole source ──────────────────────────────────────────────────────────

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
    """Every open while the test runs; every descriptor `os.open` returns
    closed by the end; `mmap` never called."""
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


PLAIN = b"plain file " * 300
TWO_COPIES = b"a file with an encrypted copy and a plain one " * 20
LOCKED = b"only an encrypted copy"
FLAG_1 = b"the copy in a block with bit 0x1"
FLAG_0 = b"the copy in a block without it"
UNFETCHED = b"in the root, its record not in the index"
SHADOWED = b"listed in an Encrypted block and in a plain one"


class _Source:
    """A constructed install with one file per state, by name: its id."""

    def __init__(self, tmp: Path, *, version: int = 2, client: str = "Test Client.app") -> None:
        rng = random.Random(1505)

        def ek(bucket: int) -> bytes:
            return key_in(bucket, rng) + rng.randbytes(7)

        self.ek = {
            "plain": ek(1),
            "two_secret": ek(2),
            "two_plain": ek(3),
            "locked": ek(4),
            "flag_1": ek(7),
            "flag_0": ek(8),
            "unfetched": ek(1),  # a bucket with an index file, the key not in it
            "shadowed_secret": ek(10),
            "shadowed_plain": ek(11),
        }
        self.ids = {
            "plain": 10,
            "two": 20,
            "locked": 30,
            "flagged": 40,
            "unfetched": 50,
            "shadowed": 60,
            "french": 70,
        }
        stored = [
            (self.ek["plain"], blte([n_block(PLAIN[:7]), z_block(PLAIN[7:])])),
            (self.ek["two_secret"], blte([e_block(decoded=len(TWO_COPIES))])),
            (self.ek["two_plain"], blte([z_block(TWO_COPIES)])),
            (self.ek["locked"], blte([n_block(LOCKED[:3]), e_block(decoded=len(LOCKED) - 3)])),
            (self.ek["flag_1"], blte([n_block(FLAG_1)])),
            (self.ek["flag_0"], blte([n_block(FLAG_0)])),
            (self.ek["shadowed_secret"], blte([e_block(decoded=len(SHADOWED))])),
            (self.ek["shadowed_plain"], blte([n_block(SHADOWED)])),
        ]
        self.ckey_shadow_secret = ckey_of(b"the encrypted row's content")
        rows: list[EncodingRow] = [
            (ckey_of(PLAIN), len(PLAIN), [self.ek["plain"]]),
            (ckey_of(TWO_COPIES), len(TWO_COPIES), [self.ek["two_secret"], self.ek["two_plain"]]),
            (ckey_of(LOCKED), len(LOCKED), [self.ek["locked"]]),
            (ckey_of(FLAG_1), len(FLAG_1), [self.ek["flag_1"]]),
            (ckey_of(FLAG_0), len(FLAG_0), [self.ek["flag_0"]]),
            (ckey_of(UNFETCHED), len(UNFETCHED), [self.ek["unfetched"]]),
            (self.ckey_shadow_secret, len(SHADOWED), [self.ek["shadowed_secret"]]),
            (ckey_of(SHADOWED), len(SHADOWED), [self.ek["shadowed_plain"]]),
        ]
        ids = self.ids
        blocks = [
            # The Encrypted block comes first in the file: the plain row of
            # `shadowed` is still tried before it.
            ([ids["shadowed"]], [self.ckey_shadow_secret], casc.ROOT_ENCRYPTED, ALL),
            ([ids["flagged"]], [ckey_of(FLAG_1)], casc.ROOT_FLAG_1, ALL),
            (
                [ids["plain"], ids["two"], ids["locked"], ids["flagged"], ids["unfetched"]],
                [
                    ckey_of(PLAIN),
                    ckey_of(TWO_COPIES),
                    ckey_of(LOCKED),
                    ckey_of(FLAG_0),
                    ckey_of(UNFETCHED),
                ],
                0,
                ENUS,
            ),
            ([ids["shadowed"]], [ckey_of(SHADOWED)], 0, ALL),
            ([ids["french"]], [ckey_of(PLAIN)], 0, FRFR),
        ]
        self.root = make_source_install(
            tmp,
            stored=stored,
            encoding_rows=rows,
            root_blocks=blocks,
            rng=rng,
            root_version=version,
            client=client,
        )

    def open(self) -> casc.CascSource:
        return casc.open_source(read_install(self.root), FLAVOR)


@pytest.mark.parametrize("version", [1, 2])
def test_open_source_reads_the_encoding_and_the_root_constructed(
    tmp_path: Path, opened: list[tuple[str, int]], version: int
) -> None:
    built = _Source(tmp_path, version=version)
    before = tree_state(built.root)
    opened.clear()
    source = built.open()
    r = source.report
    assert (r.locale, r.locale_from, r.platform, r.platform_from) == (
        "enUS",
        "install",
        "macos",
        "install",
    )
    assert r.encoding.ckey_page_count == 2 and r.root.version == version
    assert (r.root.blocks, r.root.blocks_taken, r.root.skipped_blocks) == (5, 4, (("locale", 1),))
    assert min(r.open_seconds, r.encoding_seconds, r.root_seconds) >= 0
    assert tree_state(built.root) == before
    names = [Path(p).name for p, _ in opened]
    assert all(flags & _WRITING == 0 for _, flags in opened)
    assert "shmem" not in names and "index.lock.0" not in names
    assert names.count("data.001") == 2  # the encoding file, then the root


def test_read_returns_the_bytes_checked_by_their_content_key_constructed(
    tmp_path: Path, opened: list[tuple[str, int]]
) -> None:
    built = _Source(tmp_path)
    source = built.open()
    before = tree_state(built.root)
    opened.clear()
    data = source.read(built.ids["plain"])
    assert isinstance(data, bytearray) and data == PLAIN
    assert [Path(p).name for p, _ in opened] == ["data.001"]
    assert all(flags & _WRITING == 0 for _, flags in opened)
    assert tree_state(built.root) == before
    stat = source.stat(built.ids["plain"])
    assert (stat.state, stat.words, stat.size) == ("local", "local", len(PLAIN))
    assert stat.content_key == ckey_of(PLAIN).hex()
    assert stat.encoding_key == built.ek["plain"].hex()


def test_an_encrypted_copy_is_passed_over_for_a_plain_one_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path)
    source = built.open()
    assert source.read(built.ids["two"]) == TWO_COPIES
    stat = source.stat(built.ids["two"])
    assert stat.state == "local" and stat.encoding_key == built.ek["two_plain"].hex()
    assert stat.key_names == ("50505078126ACB3E",)
    assert stat.encoding_keys == (built.ek["two_secret"].hex(), built.ek["two_plain"].hex())


def test_a_row_in_an_encrypted_block_is_tried_after_the_plain_row_constructed(
    tmp_path: Path,
) -> None:
    built = _Source(tmp_path)
    source = built.open()
    assert source.read(built.ids["shadowed"]) == SHADOWED
    stat = source.stat(built.ids["shadowed"])
    assert stat.state == "local" and stat.content_key == ckey_of(SHADOWED).hex()
    assert stat.content_keys == (ckey_of(SHADOWED).hex(), built.ckey_shadow_secret.hex())


def test_only_encrypted_copies_are_encrypted_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path)
    source = built.open()
    stat = source.stat(built.ids["locked"])
    assert (stat.state, stat.words) == ("encrypted", "encrypted")
    assert stat.key_names == ("50505078126ACB3E",)
    with pytest.raises(casc.EncryptedContent) as caught:
        source.read(built.ids["locked"])
    assert caught.value.key_name == "50505078126ACB3E" and caught.value.block == 1


def test_a_row_without_flag_1_is_tried_first_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path)
    source = built.open()
    assert source.read(built.ids["flagged"]) == FLAG_0
    assert source.stat(built.ids["flagged"]).content_key == ckey_of(FLAG_0).hex()


def test_the_states_of_files_not_read_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path)
    source = built.open()
    unfetched = source.stat(built.ids["unfetched"])
    assert (unfetched.state, unfetched.words) == ("not_local", "not in your client's local files")
    assert unfetched.encoding_keys == (built.ek["unfetched"].hex(),)
    french = source.stat(built.ids["french"])
    assert (french.state, french.words) == (
        "not_in_build",
        "not part of this game version's files",
    )
    assert french.note is not None
    assert french.note.startswith("the root lists it only for another language or platform")
    assert "so this client does not load it" in french.note
    absent = source.stat(12345)
    assert absent.state == "not_in_build"
    assert absent.note == "the root does not list it; whether a game table names it was not checked"
    vouched = source.stat(12345, in_table=True)
    assert (vouched.state, vouched.note) == ("not_in_build", "the root does not list it")
    unknown = source.stat(12345, in_table=False)
    assert unknown.state == "unknown_id"
    assert unknown.note == "the root does not list it, and the caller says no game table names it"
    assert source.stat(2_000_000_000).state == "not_in_build"
    assert source.stat(built.ids["french"], in_table=False).state == "not_in_build"
    assert source.stat(built.ids["plain"], in_table=False).state == "local"
    for fdid in (-1, 2**31, 2**40):
        stat = source.stat(fdid)
        assert (stat.state, stat.words) == ("unknown_id", "unknown id")
    for fdid, state in (
        (built.ids["unfetched"], "not_local"),
        (built.ids["french"], "not_in_build"),
    ):
        with pytest.raises(casc.FileUnavailable) as caught:
            source.read(fdid)
        assert (caught.value.state, caught.value.fdid) == (state, fdid)
    with pytest.raises(casc.FileUnavailable) as caught:
        source.read(-5)
    assert caught.value.state == "unknown_id"


def test_a_content_key_not_in_the_encoding_is_not_local_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path)
    source = built.open()
    at = list(source.root.ids()).index(built.ids["unfetched"])
    source.root._ckeys[at * 16 : at * 16 + 16] = b"\x01" * 16
    stat = source.stat(built.ids["unfetched"])
    assert stat.state == "not_local"
    assert stat.note == "1 of its content keys are not in the encoding file"
    with pytest.raises(casc.FileUnavailable):
        source.read(built.ids["unfetched"])


def test_a_mismatch_reads_the_index_again_once_then_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _Source(tmp_path)
    source = built.open()
    real = casc.read_encoded
    loads: list[Path] = []
    real_load = casc.load_index

    def counting_load(root: Path) -> tuple[casc.CascIndex, tuple[casc.BucketVersion, ...]]:
        loads.append(root)
        return real_load(root)

    def wrong(
        storage: casc.CascStorage, ekey: bytes, *, limit: int = casc.DECODE_LIMIT
    ) -> bytearray:
        data = real(storage, ekey, limit=limit)
        assert data is not None
        data[0] ^= 1
        return data

    monkeypatch.setattr(casc, "load_index", counting_load)
    monkeypatch.setattr(casc, "read_encoded", wrong)
    with pytest.raises(casc.ContentMismatch) as caught:
        source.read(built.ids["plain"])
    assert caught.value.content_key == ckey_of(PLAIN).hex()
    assert len(loads) == 1 and source.reopens == 1
    with pytest.raises(casc.ContentMismatch):
        source.read(built.ids["plain"])  # once on every read
    assert len(loads) == 2 and source.reopens == 2


def test_a_mismatch_that_the_re_read_settles_is_read_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _Source(tmp_path)
    source = built.open()
    real = casc.read_encoded
    calls: list[bytes] = []

    def flaky(
        storage: casc.CascStorage, ekey: bytes, *, limit: int = casc.DECODE_LIMIT
    ) -> bytearray:
        data = real(storage, ekey, limit=limit)
        assert data is not None
        calls.append(ekey)
        if len(calls) == 1:
            data[0] ^= 1
        return data

    monkeypatch.setattr(casc, "read_encoded", flaky)
    assert source.read(built.ids["plain"]) == PLAIN
    assert len(calls) == 2 and source.reopens == 1


def test_an_index_that_disagrees_with_the_archive_is_read_again_once_constructed(
    tmp_path: Path,
) -> None:
    """The archive is rewritten after the open (its records moved by a few
    bytes, as an agent's repair might): the stale index disagrees, the
    re-read index does not."""
    built = _Source(tmp_path)
    source = built.open()
    archive = built.root / "Data" / "data" / "data.001"
    data = archive.read_bytes()
    archive.write_bytes(b"\x00" * 7 + data)
    with pytest.raises(casc.CascMalformedError):
        source.read(built.ids["plain"])  # the index on disk is as stale as the one held
    assert source.reopens == 1
    with pytest.raises(casc.CascMalformedError):
        source.stat(built.ids["plain"])
    assert source.reopens == 2


def test_a_stale_index_is_replaced_by_the_re_read_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path)
    source = built.open()
    fresh = casc.load_index(built.root)[0]
    source.storage.index = casc.CascIndex([])  # as if every bucket were gone...
    with pytest.raises(casc.BucketMissingError):
        source.stat(built.ids["plain"])  # ...which is not a disagreement: no re-read
    source.storage.index = fresh
    assert source.read(built.ids["plain"]) == PLAIN and source.reopens == 0


def test_read_refuses_a_negative_cap_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path)
    source = built.open()
    with pytest.raises(ValueError, match="cap"):
        source.read(built.ids["plain"], limit=-1)
    with pytest.raises(ValueError, match="cap"):
        source.stat(built.ids["plain"], limit=-1)
    with pytest.raises(casc.CascTooLargeError):
        source.read(built.ids["plain"], limit=10)


def test_a_windows_layout_filters_for_windows_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path, client="Wow.exe")
    source = built.open()
    assert source.report.platform == "windows" and source.report.platform_from == "install"


def test_a_given_locale_and_platform_win_constructed(tmp_path: Path) -> None:
    built = _Source(tmp_path)
    source = casc.open_source(read_install(built.root), FLAVOR, locale="frFR", platform="windows")
    r = source.report
    assert (r.locale, r.locale_from, r.platform, r.platform_from) == (
        "frFR",
        "given",
        "windows",
        "given",
    )
    assert source.read(built.ids["french"]) == PLAIN
    assert source.stat(built.ids["plain"]).state == "not_in_build"


def test_open_refuses_an_install_whose_layout_names_no_platform_constructed(
    tmp_path: Path,
) -> None:
    built = _Source(tmp_path)
    (built.root / FLAVOR / "Test Client.app").rmdir()
    with pytest.raises(casc.CascPlatformError):
        built.open()


def test_open_refuses_an_encoding_file_that_is_not_its_content_key_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _Source(tmp_path)
    real = casc.read_encoded

    def wrong(
        storage: casc.CascStorage, ekey: bytes, *, limit: int = casc.DECODE_LIMIT
    ) -> bytearray:
        data = real(storage, ekey, limit=limit)
        assert data is not None
        data[-1] ^= 1
        return data

    monkeypatch.setattr(casc, "read_encoded", wrong)
    with pytest.raises(casc.ContentMismatch, match="encoding file"):
        built.open()


def test_open_says_when_the_encoding_file_is_not_local_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _Source(tmp_path)
    monkeypatch.setattr(casc, "read_encoded", lambda *a, **k: None)
    with pytest.raises(casc.CascMissingError, match="encoding file"):
        built.open()


# ─── fix round 1: a failing copy passed over, notes, empty blocks, speech ───

WANTED = b"the right bytes " * 30
WRONG = b"bytes of another file " * 10


def _fallback_install(tmp: Path, tags: str | None = None) -> tuple[Path, dict[str, int]]:
    """Ids whose first copy fails and a later one is good: `corrupt` (its
    first encoding key's record decodes to other bytes), `rows` (its first
    root row's only copy is corrupt, its second row good); `odd`, whose one
    copy has an LZ4 block; ids with no local copy: `flag1` (only a 0x1
    row), `install` (an Install-flagged row); `deDE` (only in a deDE
    block)."""
    rng = random.Random(1507)

    def ek(bucket: int) -> bytes:
        return key_in(bucket, rng) + rng.randbytes(7)

    bad, good, lz4, bad3 = ek(1), ek(2), ek(3), ek(7)
    missing = ek(1)
    other = ckey_of(b"another row's content")
    stored = [
        (bad, blte([n_block(WRONG)])),
        (good, blte([z_block(WANTED)])),
        (lz4, blte([n_block(b"x"), (b"4" + b"\x00" * 9, 9)])),
        (bad3, blte([n_block(WRONG)])),
    ]
    ids = {"corrupt": 1, "odd": 2, "rows": 3, "flag1": 4, "install": 5, "deDE": 6}
    rows: list[EncodingRow] = [
        (ckey_of(WANTED), len(WANTED), [bad, good]),
        (ckey_of(b"odd"), 10, [lz4]),
        (other, len(WRONG), [bad3]),
        (ckey_of(b"unfetched"), 9, [missing]),
    ]
    blocks = [
        ([ids["rows"]], [other], 0, ALL),
        (
            [ids["corrupt"], ids["odd"], ids["rows"]],
            [ckey_of(WANTED), ckey_of(b"odd"), ckey_of(WANTED)],
            0,
            ENUS,
        ),
        ([ids["flag1"]], [ckey_of(b"unfetched")], casc.ROOT_FLAG_1, ALL),
        ([ids["install"]], [ckey_of(b"unfetched")], casc.ROOT_INSTALL, ALL),
        ([ids["deDE"]], [ckey_of(WANTED)], 0, 0x20),
    ]
    root = make_source_install(
        tmp, stored=stored, encoding_rows=rows, root_blocks=blocks, rng=rng, tags=tags
    )
    return root, ids


def _open(root: Path) -> casc.CascSource:
    return casc.open_source(read_install(root), FLAVOR)


def test_a_copy_that_still_mismatches_is_passed_over_for_the_next_key_constructed(
    tmp_path: Path,
) -> None:
    root, ids = _fallback_install(tmp_path)
    source = _open(root)
    assert source.read(ids["corrupt"]) == WANTED
    assert source.reopens == 1  # one re-read for the call, then the next key


def test_a_copy_that_still_mismatches_is_passed_over_for_the_next_row_constructed(
    tmp_path: Path,
) -> None:
    root, ids = _fallback_install(tmp_path)
    source = _open(root)
    assert source.read(ids["rows"]) == WANTED
    assert source.reopens == 1


def test_content_mismatch_only_when_no_candidate_matches_constructed(tmp_path: Path) -> None:
    root, ids = _fallback_install(tmp_path)
    source = _open(root)
    found = source.encoding.lookup(ckey_of(WANTED))
    assert found is not None
    bad_only = encoding_bytes([[(ckey_of(WANTED), len(WANTED), [found.ekeys[0]])]])
    source.encoding = casc.parse_encoding(bad_only)
    with pytest.raises(casc.ContentMismatch) as caught:
        source.read(ids["corrupt"])
    assert caught.value.content_key == ckey_of(WANTED).hex()
    assert source.reopens == 1


def test_an_unsupported_copy_is_raised_when_it_is_the_only_one_constructed(
    tmp_path: Path,
) -> None:
    root, ids = _fallback_install(tmp_path)
    source = _open(root)
    with pytest.raises(casc.UnsupportedBlockMode):
        source.read(ids["odd"])
    assert source.reopens == 0
    assert source.stat(ids["odd"]).state == "local"  # stat decodes nothing


def test_not_local_notes_name_the_flags_constructed(tmp_path: Path) -> None:
    root, ids = _fallback_install(tmp_path)
    source = _open(root)
    flag1 = source.stat(ids["flag1"])
    assert flag1.state == "not_local" and flag1.note is not None
    assert "content bit 0x1" in flag1.note and "Install" not in flag1.note
    install = source.stat(ids["install"])
    assert install.state == "not_local" and install.note is not None
    assert "loose file in the flavor folder" in install.note and "0x1" not in install.note


def test_the_speech_locale_is_reported_and_named_in_the_note_constructed(
    tmp_path: Path,
) -> None:
    tags = f"OSX {GEO} deDE speech?:OSX {GEO} enUS text?"
    root, ids = _fallback_install(tmp_path, tags=tags)
    source = _open(root)
    assert (source.report.locale, source.report.speech_locale) == ("enUS", "deDE")
    stat = source.stat(ids["deDE"])
    assert stat.state == "not_in_build" and stat.note is not None
    assert "speech locale deDE" in stat.note and "text locale enUS alone" in stat.note
    assert GEO not in source.report.model_dump_json()


def test_locales_of_reads_both_groups_constructed() -> None:
    assert casc.locales_of(f"OSX {GEO} deDE speech?:OSX {GEO} enUS text?") == ("enUS", "deDE")
    assert casc.locales_of("enGB speech?:") == ("enGB", "enGB")
    assert casc.locales_of("frFR text?") == ("frFR", None)


def test_a_million_empty_blocks_keep_no_place_constructed() -> None:
    """Hostile: blocks of no rows get no span, so a lookup costs what the
    real blocks make it cost, and the parse stays linear."""
    head = struct.pack("<4sIIII", b"TSFM", 24, 2, 2, 2) + bytes(4)
    empty = struct.pack("<IIIIB", 0, ALL, 0, 0, 0)
    real = root_bytes([([1, 2], _keys([1, 2]), 0, ALL)])[24:]
    data = head + empty * 1_000_000 + real
    started = time.perf_counter()
    table = casc.parse_root(data, locale="enUS", platform="macos")
    seconds = time.perf_counter() - started
    r = table.report
    assert (r.blocks, r.empty_blocks, r.blocks_taken) == (1_000_001, 1_000_000, 1)
    assert len(table._taken) == 1 and table._skipped == []
    assert table.find(2)[0].ckey == _keys([1, 2])[1]
    assert seconds < 10, f"{seconds:.2f} s for a million empty blocks"
