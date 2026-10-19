"""casc: the build config, the local index and the archives' BLTE records
of the install's game data (docs/LAB_PLAN.md §17.1 steps 1 to 3, ADR-0033,
M15-03 and M15-04).

Formats: `docs/LAB_FORMATS.md` §10. The install's `Data/` is shared by every
product in the install; only the build config is per product.

1. **The build.** The flavor's product (`.flavor.info`, read by `install`)
   picks its `Active` row of `.build.info`. Exactly one such row is used; any
   other count is refused with `BuildRowError`, never guessed. That row's
   `Build Key` names the build config `Data/config/<k[0:2]>/<k[2:4]>/<k>`,
   `key = value` text whose MD5 is its own name (checked), with `root` (a
   content key) and `encoding` (a content key and an encoding key).
2. **The local index.** Every `Data/data/<bucket:2><version:8>.idx`, the
   newest version number per bucket (the client's `shmem`, said to name the
   current one **[verify]**, is not read; whether the newest always wins is
   **[verify]**, §17.7), so each bucket's versions not taken are reported,
   as are a bucket with no file and an `.idx`-like name not taken. Each file
   is a header (its size, a Jenkins `hashlittle` of it, version 7, the
   bucket, an extra byte held to 0, and the field-size spec `4/5/9/30`),
   then, at the next 16-byte boundary,
   the entries block (its size, a hash chained entry by entry with
   `hashlittle2`) of 18-byte entries: the first nine bytes of an encoding
   key, a five-byte big-endian field (ten bits of archive number, thirty of
   offset) and a four-byte little-endian size. Both hashes are checked and
   the spec is read and held to `4/5/9/30`, so a half-written or unknown
   file is refused (`CascMalformedError`, `CascUnsupportedError`), never
   read around. Each file also holds one entry per archive whose key's
   bucket is the one before the file's (LAB_FORMATS §10, amendment of
   2026-10-19); they are counted per bucket, with any other entry outside its
   key's bucket, and a lookup searches only the key's own bucket.
3. **The archive record and BLTE.** At an entry's offset in
   `Data/data/data.<archive:03d>` is a 30-byte record header (the encoding
   key read back to front, the record's size, two flag bytes, two
   checksums; `parse_record_header` checks the first and the size against
   the index entry and the first checksum against lookup3), then BLTE: a
   chunk table and blocks, each led by a mode byte. `N` (plain) and `Z`
   (zlib) are decoded, each block's MD5 checked first; `4` and `F` are
   refused with `UnsupportedBlockMode` (an `F` block is never decoded, so
   never recursed into), and `E` with `EncryptedContent` and its key name:
   nothing is decrypted. Every size is checked against the bytes there and
   the stored and decoded sizes against a stated cap (`DECODE_LIMIT`, 256
   MiB) and the block count against another (`BLOCK_LIMIT`) before
   anything is decoded or allocated (`decode_blte`); a `Z` block is never
   inflated a byte past its declared size. The archive is opened for one
   bounded, ranged read and closed (`read_record`).

Reads only (L1, ADR-0033): every file is opened read-only without following
a final link (`snapshot.open_regular_file`, the open under
`snapshot.read_regular_file`), read into memory with a size bound (an index
file or the build config whole; an archive only the one record's range,
`read_record`), and closed before the next is opened, so no handle or
mapping outlives the call (no `mmap` anywhere); nothing is created, locked
or written anywhere, and `shmem` and `index.lock.0` are never opened. A
folder on the way (`Data`, `Data/data`, `Data/config` and the key's two
levels) that is a link is refused rather than followed. Whether a handle
held for one read can still block the agent or the client on Windows is a
§17.7 **[verify]**.

Nothing about a flavor is known here (L6): the product, the version and the
build key come from the install's own files.

The entries hash is the one cost that grows with the index (about a million
entries for an install). It is a chain, entry after entry, within a file,
but the sixteen files are independent, so `_entries_hashes` runs the sixteen
chains side by side, one 64-bit lane each, inside Python integers: one pass
of about 66,000 steps instead of a million (§17.1's target: the index in at
most 2 s). The steps run in windows of `_WINDOW`, so the lane buffers stay
the same small size whatever a file's length.
"""

from __future__ import annotations

import bisect
import hashlib
import operator
import os
import re
import stat
import struct
import time
import zlib
from collections.abc import Buffer, Sequence
from pathlib import Path
from typing import NoReturn

from pydantic import BaseModel, ConfigDict

from wowlab_core import longpath, snapshot
from wowlab_core.install import BuildInfoRow, Flavor, Install, is_link

__all__ = [
    "BLOCK_LIMIT",
    "BUCKETS",
    "DECODE_LIMIT",
    "KEY_BYTES",
    "RECORD_HEADER_SIZE",
    "BlteBlock",
    "BlteLayout",
    "BucketMissingError",
    "BucketVersion",
    "BuildConfig",
    "BuildRowError",
    "CascError",
    "CascIndex",
    "CascIndexReport",
    "CascMalformedError",
    "CascMissingError",
    "CascStorage",
    "CascTooLargeError",
    "CascUnreadableError",
    "CascUnsupportedError",
    "EncryptedContent",
    "IndexEntry",
    "IndexFile",
    "RecordHeader",
    "UnsupportedBlockMode",
    "archive_path",
    "bucket_of",
    "build_config_path",
    "decode_blte",
    "hashlittle",
    "hashlittle2",
    "load_index",
    "open_storage",
    "parse_blte",
    "parse_build_config",
    "parse_index_file",
    "parse_record_header",
    "read_build_config",
    "read_encoded",
    "read_record",
    "select_build_row",
]

# The install layout (docs/LAB_FILE_MAP.md); these name no flavor.
_DATA = "Data"
_CONFIG = "config"
_DATA_DATA = "data"

BUCKETS = 16
KEY_BYTES = 9  # the first nine bytes of an encoding key
_INDEX_VERSION = 7
# (size bytes, offset bytes, key bytes, offset bits) as the header states them.
_SPEC = (4, 5, KEY_BYTES, 30)
_ENTRY = _SPEC[0] + _SPEC[1] + _SPEC[2]  # 18
_OFFSET_BITS = _SPEC[3]
_OFFSET_MASK = (1 << _OFFSET_BITS) - 1
_HEADER_FIELDS = struct.Struct("<HBBBBBBQ")  # version, bucket, extra, spec x4, a u64
_MAX_HEADER = 0x100  # a header hash size past this is refused as malformed
_INDEX_NAME = re.compile(r"([0-9a-f]{2})([0-9a-f]{8})\.idx")
# A name that looks like an index file but is not one the reader takes
# (another letter case, a suffix after `.idx`): listed in the report, unread.
_INDEX_LIKE = re.compile(r".*\.idx.*", re.IGNORECASE)
_HEX_KEY = re.compile(r"[0-9a-f]{32}")
# The archive record header before each BLTE (LAB_FORMATS §10); also the
# size, and the offset step, of the entries found one bucket on (amendment
# of 2026-10-19): a record header alone.
RECORD_HEADER_SIZE = 30
_RECORD_HEADER = RECORD_HEADER_SIZE
# The record header's fields: the hash field (16 bytes), the record's size
# (header included, little-endian), two flag bytes, checksum A, checksum B.
_RECORD_FIELDS = struct.Struct("<16sI2sII")
# Checksum A is lookup3 `hashlittle` of the 22 bytes before it from this
# seed (every record sampled at 70450 matched; LAB_FORMATS §10 amendment of
# 2026-10-19). Checksum B depends on where the record is; its algorithm is
# [verify], so it is reported and not checked.
_CHECKSUM_A_SEED = 0x3D6BE971
_CHECKSUM_A_SPAN = 22

# The cap on one read (`decode_blte`, `read_record`): a record whose stored
# BLTE or whose declared decoded size is over it is refused before it is read
# or decoded. The largest file the Lab reads at 70450 is the encoding file,
# 141.6 MB decoded (LAB_FORMATS §10); the cap leaves room above it.
DECODE_LIMIT = 256 * 1024 * 1024
_BLTE_MAGIC = b"BLTE"
_BLTE_PREAMBLE = 8  # the magic and the big-endian header size
_CHUNK_TABLE_FLAG = 0x0F
_CHUNK_TABLE_HEAD = 4  # the flag byte and a 24-bit big-endian block count
_CHUNK_ENTRY = struct.Struct(">II16s")  # encoded size, decoded size, MD5
# The most blocks one chunk table may list. Every row becomes a small object,
# so the count bounds what the table costs before any block is read: at most
# a few tens of MB here. The most in any of the 1,041,954 records at 70450 is
# 913 (a survey of every record's header, M15-04).
BLOCK_LIMIT = 65_536
_MODE_PLAIN = ord("N")
_MODE_ZLIB = ord("Z")

# Read bounds: the files at 70450 are 1,245,184 bytes (each index) and 59,537
# bytes (the build config); a file far past these is refused, not read.
_INDEX_LIMIT = 8 * 1024 * 1024
_CONFIG_LIMIT = 4 * 1024 * 1024
# Steps of the side-by-side entries hash per pass (`_entries_hashes`): its
# lane buffers are 5 x _WINDOW x lanes x 8 bytes, 2.6 MB for sixteen lanes.
_WINDOW = 4096
# A `Version` tail longer than this is no build number (CPython refuses an
# `int()` of thousands of digits).
_BUILD_DIGITS = 10
_RETRY = "; the Battle.net app or the client may be writing it: close both and try again"


# ─── errors ──────────────────────────────────────────────────────────────────


class CascError(Exception):
    """Base class for everything this module raises on purpose. `path` is
    the file or folder concerned, when there is one."""

    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


class BuildRowError(CascError):
    """The flavor's product does not have exactly one `Active` row in
    `.build.info` (`count` says how many it has), or the flavor is not
    there, names no product, or its row has no usable build key."""

    def __init__(self, message: str, product: str, count: int, path: Path | None = None) -> None:
        super().__init__(message, path)
        self.product = product
        self.count = count


class CascMissingError(CascError):
    """A file or folder the read needs is not there."""


class BucketMissingError(CascMissingError):
    """A lookup's key falls in a bucket the local index has no file for, so
    whether the key is local cannot be said (not the same as "not listed")."""

    def __init__(self, message: str, bucket: int) -> None:
        super().__init__(message)
        self.bucket = bucket


class CascUnreadableError(CascError):
    """A file or folder is there but cannot be read as one: the operating
    system refused, it is a link, it is not a regular file (or folder), or it
    is larger than the bound for its kind."""


class CascMalformedError(CascError):
    """A file's content is not what its format says: a hash that does not
    match, a size past the end, a key that is not hex, a missing field."""


class CascUnsupportedError(CascError):
    """A well-formed file in a version or layout this reader does not know
    (an index version other than 7, a spec other than `4/5/9/30`, a
    non-zero extra byte, a bucket past sixteen, a BLTE chunk table flag
    other than `0x0F`)."""


class UnsupportedBlockMode(CascUnsupportedError):  # noqa: N818 - name fixed by LAB_PLAN §17.1
    """A BLTE block whose mode this reader does not decode: `4` (LZ4), `F`
    (a BLTE nested in the block; never recursed into) or a mode byte the
    format does not name. `mode` is the byte as one character (or `\\xNN`),
    `block` the block's place in the chunk table, from 0."""

    def __init__(self, message: str, mode: str, block: int, path: Path | None = None) -> None:
        super().__init__(message, path)
        self.mode = mode
        self.block = block


class EncryptedContent(CascError):  # noqa: N818 - name fixed by docs/LAB_PLAN.md §17.1
    """A BLTE block in mode `E`: encrypted with the key `key_name` (the
    eight key-name bytes as a little-endian number in sixteen hex digits,
    the form the published key lists use). Nothing is decrypted (ADR-0033):
    no key list is held and none is looked for. `cipher` is the block's
    cipher byte (`S` Salsa20, `A` ARC4) as one character."""

    def __init__(
        self, message: str, key_name: str, block: int, cipher: str, path: Path | None = None
    ) -> None:
        super().__init__(message, path)
        self.key_name = key_name
        self.block = block
        self.cipher = cipher


class CascTooLargeError(CascError):
    """A record over one of the read's caps: its stored size or the decoded
    size its chunk table declares (`limit`, in bytes), or its chunk table's
    block count (`BLOCK_LIMIT`). `size` is what the record has. Refused
    before the bytes are read, the rows are unpacked or the output is
    allocated."""

    def __init__(self, message: str, size: int, limit: int, path: Path | None = None) -> None:
        super().__init__(message, path)
        self.size = size
        self.limit = limit


# ─── models ──────────────────────────────────────────────────────────────────


class BuildConfig(BaseModel):
    """The build config named by the build key. `pairs` holds every
    `key = value` line in file order, as written after trimming; `root` and
    `encoding_*` are its two keys this reader needs, lowercase hex."""

    model_config = ConfigDict(frozen=True)

    key: str
    root: str
    encoding_ckey: str
    encoding_ekey: str
    pairs: tuple[tuple[str, str], ...]


class IndexEntry(BaseModel):
    """Where an encoding key's data is: archive `data.<archive:03d>`, the
    byte `offset` in it, and `size` bytes (the record header included)."""

    model_config = ConfigDict(frozen=True)

    archive: int
    offset: int
    size: int


class BucketVersion(BaseModel):
    """The `.idx` file taken for one bucket, and the older ones not taken."""

    model_config = ConfigDict(frozen=True)

    bucket: int
    version: int
    file: str
    entries: int
    header_size: int
    header_hash: int
    extra_bytes: int  # the header's byte after the bucket; held to 0 (0 at 70450)
    entries_hash: int
    header_u64: int  # the header's last field: 1023 x 2**30 at 70450; meaning [verify]
    # Entries whose key's bucket is the one before this file's, each a
    # 30-byte record header at offset 30 x this bucket (one per archive at
    # 70450; LAB_FORMATS §10, amendment of 2026-10-19), and any other entry
    # outside its key's bucket (none at 70450). `find` finds neither.
    entries_from_previous_bucket: int
    entries_out_of_place: int
    not_taken: tuple[int, ...]  # older version numbers of this bucket, newest first


class CascIndexReport(BaseModel):
    """What opening one flavor's build found."""

    model_config = ConfigDict(frozen=True)

    product: str
    version: str
    build: int | None
    build_key: str
    root: str
    encoding_ckey: str
    encoding_ekey: str
    bucket_count: int
    entry_count: int
    versions: tuple[BucketVersion, ...]  # by bucket
    # Buckets with more than one version on disk, where the newest was taken
    # (**[verify]**: `shmem` is not read).
    newest_taken: tuple[int, ...]
    # Buckets with no `.idx` file at all: a lookup there raises
    # `BucketMissingError` rather than answering "not listed".
    missing_buckets: tuple[int, ...]
    # `.idx`-like names in `Data/data` that are not `<bucket:2><version:8>.idx`
    # in lowercase hex (another letter case, a suffix): not opened.
    skipped_files: tuple[str, ...]
    index_seconds: float  # reading and checking every `.idx`
    open_seconds: float  # the whole open: the build config and the index


class RecordHeader(BaseModel):
    """The 30 bytes before a record's BLTE in an archive (LAB_FORMATS §10).
    `hash_field` is the field read back to front, lowercase hex: it starts
    with the nine bytes the index holds; at 70450 most records hold only
    those, the other seven zero, and some the whole encoding key (its MD5
    of the BLTE header, a 2026-10-19 amendment). `size` includes these 30
    bytes. `flags` is the two flag bytes in hex (`0000` on the data records
    met at 70450, `0100` on the archive's own segment headers; meaning
    [verify]). `checksum_a` is checked; `checksum_b` is not (its algorithm
    is [verify])."""

    model_config = ConfigDict(frozen=True)

    hash_field: str
    size: int
    flags: str
    checksum_a: int
    checksum_b: int


class BlteBlock(BaseModel):
    """One chunk-table row: the block's stored size (its mode byte
    included), its decoded size, the MD5 of its stored bytes (lowercase
    hex) and its mode byte (one character, or `\\xNN` for a byte that is not
    printable ASCII). In a BLTE without a chunk table the one block has no
    declared size and no MD5 (both None)."""

    model_config = ConfigDict(frozen=True)

    encoded_size: int
    decoded_size: int | None
    md5: str | None
    mode: str


class BlteLayout(BaseModel):
    """A BLTE's header: its size (0 when there is no chunk table, in which
    case the one block is the rest of the data, its decoded size unknown
    until decoded, `decoded_size` None) and its blocks in order."""

    model_config = ConfigDict(frozen=True)

    header_size: int
    blocks: tuple[BlteBlock, ...]
    encoded_size: int  # the whole BLTE, magic to the last block
    decoded_size: int | None


class IndexFile:
    """One parsed, checked `.idx` file: its facts and its entries."""

    __slots__ = (
        "bucket",
        "entries",
        "entries_hash",
        "extra",
        "from_previous",
        "header_hash",
        "header_size",
        "header_u64",
        "keys",
        "out_of_place",
        "version",
    )

    def __init__(
        self,
        *,
        bucket: int,
        version: int,
        header_size: int,
        header_hash: int,
        extra: int,
        entries_hash: int,
        header_u64: int,
        entries: bytes,
    ) -> None:
        self.bucket = bucket
        self.version = version
        self.header_size = header_size
        self.extra = extra
        self.header_hash = header_hash
        self.entries_hash = entries_hash
        self.header_u64 = header_u64
        self.entries = entries
        # Filled when the file is checked (`_check_entries`).
        self.keys: list[bytes] = []
        self.from_previous = 0
        self.out_of_place = 0


class CascIndex:
    """The lookup from the first nine bytes of an encoding key to where its
    data is, over every bucket taken. Holds bytes only; no file is open.
    `skipped` lists the `.idx`-like names in `Data/data` not taken."""

    def __init__(self, files: Sequence[IndexFile], skipped: Sequence[str] = ()) -> None:
        self._files: dict[int, IndexFile] = {f.bucket: f for f in files}
        self.skipped: tuple[str, ...] = tuple(sorted(skipped))

    def __len__(self) -> int:
        return sum(len(f.keys) for f in self._files.values())

    @property
    def buckets(self) -> tuple[int, ...]:
        return tuple(sorted(self._files))

    @property
    def missing_buckets(self) -> tuple[int, ...]:
        """The buckets with no `.idx` file."""
        return tuple(b for b in range(BUCKETS) if b not in self._files)

    def find(self, ekey: bytes) -> IndexEntry | None:
        """Where `ekey` (an encoding key, or at least its first nine bytes)
        is, or `None` when the local index does not list it. The file
        searched is the one of `bucket_of(ekey)`; when the index has no file
        for that bucket, `BucketMissingError` is raised, as "not listed"
        could not be said. (Each file at 70450 also holds 65 entries whose
        key's bucket is the one before it, each of size 30 at offset 30
        times the file's bucket in one archive: the archives' segment headers
        by their look, **[verify]**. None holds a file's data: size 30 is the
        record header alone. This lookup does not find them.)"""
        if len(ekey) < KEY_BYTES:
            raise ValueError(f"an index key is {KEY_BYTES} bytes; got {len(ekey)}")
        key = bytes(ekey[:KEY_BYTES])
        bucket = bucket_of(key)
        file = self._files.get(bucket)
        if file is None:
            raise BucketMissingError(
                f"the local index has no file for bucket {bucket}, so whether the key "
                "is local cannot be said",
                bucket,
            )
        i = bisect.bisect_left(file.keys, key)
        if i == len(file.keys) or file.keys[i] != key:
            return None
        return _entry_at(file.entries, i)

    def entry_at(self, bucket: int, i: int) -> tuple[bytes, IndexEntry]:
        """The `i`-th entry of `bucket` in key order (its key and where it
        is); for derived fixtures and tests."""
        file = self._files[bucket]
        return file.keys[i], _entry_at(file.entries, i)

    def count(self, bucket: int) -> int:
        """How many entries the file taken for `bucket` holds."""
        file = self._files.get(bucket)
        return 0 if file is None else len(file.keys)

    def keys(self, bucket: int) -> tuple[bytes, ...]:
        """The nine-byte keys of `bucket`'s file, in key order."""
        file = self._files.get(bucket)
        return () if file is None else tuple(file.keys)


class CascStorage:
    """One flavor's build opened: its `.build.info` row, its build config,
    the local index and the report. Nothing in it holds a file open."""

    def __init__(
        self,
        root: Path,
        row: BuildInfoRow,
        config: BuildConfig,
        index: CascIndex,
        report: CascIndexReport,
    ) -> None:
        self.root = root
        self.row = row
        self.config = config
        self.index = index
        self.report = report


# ─── hashing ─────────────────────────────────────────────────────────────────

_M32 = 0xFFFFFFFF


def _rot(x: int, k: int) -> int:
    return ((x << k) | (x >> (32 - k))) & _M32


def hashlittle2(data: bytes, pc: int = 0, pb: int = 0) -> tuple[int, int]:
    """Bob Jenkins' lookup3 `hashlittle2` (public domain): `(pc, pb)` after
    hashing `data` from the given seeds. Written from the published
    algorithm; `hashlittle(data, seed)` is its first value with `pb = 0`."""
    n = len(data)
    a = b = c = (0xDEADBEEF + n + pc) & _M32
    c = (c + pb) & _M32
    off = 0
    while n > 12:
        x, y, z = struct.unpack_from("<3I", data, off)
        a = (a + x) & _M32
        b = (b + y) & _M32
        c = (c + z) & _M32
        a = (a - c) & _M32
        a ^= _rot(c, 4)
        c = (c + b) & _M32
        b = (b - a) & _M32
        b ^= _rot(a, 6)
        a = (a + c) & _M32
        c = (c - b) & _M32
        c ^= _rot(b, 8)
        b = (b + a) & _M32
        a = (a - c) & _M32
        a ^= _rot(c, 16)
        c = (c + b) & _M32
        b = (b - a) & _M32
        b ^= _rot(a, 19)
        a = (a + c) & _M32
        c = (c - b) & _M32
        c ^= _rot(b, 4)
        b = (b + a) & _M32
        n -= 12
        off += 12
    if n == 0:
        return c, b
    x, y, z = struct.unpack("<3I", bytes(data[off : off + n]) + bytes(12 - n))
    a = (a + x) & _M32
    b = (b + y) & _M32
    c = (c + z) & _M32
    c ^= b
    c = (c - _rot(b, 14)) & _M32
    a ^= c
    a = (a - _rot(c, 11)) & _M32
    b ^= a
    b = (b - _rot(a, 25)) & _M32
    c ^= b
    c = (c - _rot(b, 16)) & _M32
    a ^= c
    a = (a - _rot(c, 4)) & _M32
    b ^= a
    b = (b - _rot(a, 14)) & _M32
    c ^= b
    c = (c - _rot(b, 24)) & _M32
    return c, b


def hashlittle(data: bytes, seed: int = 0) -> int:
    """lookup3 `hashlittle`: the first value of `hashlittle2(data, seed, 0)`."""
    return hashlittle2(data, seed, 0)[0]


_LANE_BYTES = 8  # each chain runs in a 64-bit lane; its values stay below 2**57


def _entries_hashes(blocks: Sequence[bytes]) -> list[int]:
    """For each block of 18-byte entries, the `pc` of `hashlittle2` chained
    over its entries in order (each entry hashed with the last one's
    `(pc, pb)` as seeds, from `(0, 0)`): the index's entries hash.

    The blocks are independent, so they run side by side: lane `i` of every
    integer below is the 32-bit state of block `i` (bits `64i` to `64i+31`),
    and each step hashes the next entry of every block at once. An
    addition is masked back to 32 bits per lane; a subtraction first sets
    bit 32 of each lane, so no borrow crosses a lane; a rotation's two
    halves stay inside the lane (shifts of at most 25 bits) and are masked.
    Lanes whose block has ended keep running on zero bytes; their value was
    taken when their last entry was hashed. This equals `hashlittle2`
    applied entry by entry (tested against it)."""
    lanes = len(blocks)
    counts = [len(b) // _ENTRY for b in blocks]
    steps = max(counts, default=0)
    results = [0] * lanes
    if steps == 0:
        return results
    stride = lanes * _LANE_BYTES
    m = sum(_M32 << (64 * i) for i in range(lanes))
    h = sum(1 << (32 + 64 * i) for i in range(lanes))
    init = sum((0xDEADBEEF + _ENTRY) << (64 * i) for i in range(lanes))
    ends: dict[int, list[int]] = {}
    for lane, n in enumerate(counts):
        ends.setdefault(n, []).append(lane)
    fb = int.from_bytes
    pc = pb = 0
    for first in range(0, steps, _WINDOW):
        span = min(_WINDOW, steps - first)
        # words[j]: step k of this window, lane i at byte (k * lanes + i) * 8,
        # the j-th little-endian word of that lane's entry (the fifth is the
        # last two bytes, zero-padded, as lookup3 reads an 18-byte key's
        # tail). Fresh each window, so a lane past its last entry reads zeros.
        words = [bytearray(span * stride) for _ in range(5)]
        for lane, block in enumerate(blocks):
            n = min(counts[lane] - first, span)
            if n <= 0:
                continue
            part = block[first * _ENTRY : (first + n) * _ENTRY]
            for j in range(5):
                for i in range(4 if j < 4 else 2):
                    start = lane * _LANE_BYTES + i
                    words[j][start : start + n * stride : stride] = part[j * 4 + i :: _ENTRY]
        w0, w1, w2, w3, w4 = (memoryview(w) for w in words)
        for k in range(span):
            s = k * stride
            e = s + stride
            a = (init + pc) & m
            c = (a + pb) & m
            b = (a + fb(w1[s:e], "little")) & m
            a = (a + fb(w0[s:e], "little")) & m
            c = (c + fb(w2[s:e], "little")) & m
            # mix
            a = ((a | h) - c) & m
            a ^= ((c << 4) | (c >> 28)) & m
            c = (c + b) & m
            b = ((b | h) - a) & m
            b ^= ((a << 6) | (a >> 26)) & m
            a = (a + c) & m
            c = ((c | h) - b) & m
            c ^= ((b << 8) | (b >> 24)) & m
            b = (b + a) & m
            a = ((a | h) - c) & m
            a ^= ((c << 16) | (c >> 16)) & m
            c = (c + b) & m
            b = ((b | h) - a) & m
            b ^= ((a << 19) | (a >> 13)) & m
            a = (a + c) & m
            c = ((c | h) - b) & m
            c ^= ((b << 4) | (b >> 28)) & m
            b = (b + a) & m
            # the six-byte tail, then final
            a = (a + fb(w3[s:e], "little")) & m
            b = (b + fb(w4[s:e], "little")) & m
            c ^= b
            c = ((c | h) - (((b << 14) | (b >> 18)) & m)) & m
            a ^= c
            a = ((a | h) - (((c << 11) | (c >> 21)) & m)) & m
            b ^= a
            b = ((b | h) - (((a << 25) | (a >> 7)) & m)) & m
            c ^= b
            c = ((c | h) - (((b << 16) | (b >> 16)) & m)) & m
            a ^= c
            a = ((a | h) - (((c << 4) | (c >> 28)) & m)) & m
            b ^= a
            b = ((b | h) - (((a << 14) | (a >> 18)) & m)) & m
            c ^= b
            c = ((c | h) - (((b << 24) | (b >> 8)) & m)) & m
            pc, pb = c, b
            done = ends.get(first + k + 1)
            if done is not None:
                for lane in done:
                    results[lane] = (pc >> (64 * lane)) & _M32
        for view in (w0, w1, w2, w3, w4):
            view.release()
    return results


# ─── reading ─────────────────────────────────────────────────────────────────


def _check_dir(path: Path) -> None:
    """`path` is a folder and not a link (a link is never followed)."""
    try:
        st = longpath.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        raise CascMissingError(f"{path} is not there", path) from None
    except OSError as exc:
        raise CascUnreadableError(f"cannot read {path} ({exc.strerror or exc})", path) from exc
    if is_link(path, st):
        raise CascUnreadableError(f"{path} is a link; links are not followed", path)
    if not stat.S_ISDIR(st.st_mode):
        raise CascUnreadableError(f"{path} is not a folder", path)


def _read(path: Path, limit: int) -> bytes:
    """The bytes of `path`, read-only, whole, bounded, the handle closed."""
    try:
        return snapshot.read_regular_file(path, limit=limit)
    except FileNotFoundError:
        raise CascMissingError(f"{path} is not there", path) from None
    except snapshot.UnsafeReadError as exc:
        raise CascUnreadableError(f"cannot read {path} ({exc})", path) from exc
    except OSError as exc:  # refused, or a link (ELOOP)
        raise CascUnreadableError(f"cannot read {path} ({exc.strerror or exc})", path) from exc


def _build_number(version: str) -> int | None:
    last = version.rsplit(".", 1)[-1].strip()
    ok = last.isascii() and last.isdecimal() and len(last) <= _BUILD_DIGITS
    return int(last) if ok else None


def select_build_row(install: Install, flavor: Flavor | str) -> tuple[Flavor, BuildInfoRow]:
    """The flavor (a `Flavor` of `install`, or its folder name) and its
    product's one `Active` row of `.build.info`. Refused with
    `BuildRowError` when the flavor is not in the install, names no
    product, or its product has no `Active` row or more than one."""
    if isinstance(flavor, str):
        found = next((f for f in install.flavors if f.folder == flavor), None)
        if found is None:
            raise BuildRowError(
                f"no flavor folder {flavor!r} in {install.root}", "", 0, install.root
            )
        flavor = found
    product = flavor.product
    if not product:
        raise BuildRowError(
            f"the flavor folder {flavor.folder!r} names no product in its .flavor.info",
            "",
            0,
            flavor.path,
        )
    mine = [r for r in install.products if (r.product or "").strip() == product]
    rows = [r for r in mine if r.is_active]
    if len(rows) != 1:
        if not mine:
            saw = f".build.info has no row for the product {product!r}"
        else:
            branches = ", ".join((r.branch or "").strip() or "(empty)" for r in mine)
            cells = ", ".join(repr(r.active or "") for r in mine)
            active = "none is" if not rows else f"{len(rows)} are"
            plural = "s" if len(mine) != 1 else ""
            saw = (
                f".build.info has {len(mine)} row{plural} for the product {product!r} "
                f"(branches {branches}; Active cells {cells}), and {active} Active"
            )
        raise BuildRowError(
            f"{saw}. wowlab reads the build of the one Active row and does not pick "
            "between rows (`wowlab install show` lists them).",
            product,
            len(rows),
            install.root / ".build.info",
        )
    return flavor, rows[0]


def _hex_key(text: str | None, what: str, path: Path | None) -> str:
    key = (text or "").strip().lower()
    if not _HEX_KEY.fullmatch(key):
        raise CascMalformedError(f"{what} is not a 32-digit hex key: {text!r}", path)
    return key


def build_config_path(root: Path, build_key: str) -> Path:
    """`Data/config/<k[0:2]>/<k[2:4]>/<k>` under the install `root`."""
    key = _hex_key(build_key, "the build key", None)
    return root / _DATA / _CONFIG / key[0:2] / key[2:4] / key


def parse_build_config(data: bytes, key: str, path: Path | None = None) -> BuildConfig:
    """A build config's text (LAB_FORMATS §10): its MD5 must be `key`, its
    name; `#` lines and blank lines are skipped, every other line is kept as
    a `key = value` pair (split at the first `=`; a line with none is kept
    with an empty value). `root` must be one hex key and `encoding` two,
    each given once."""
    key = _hex_key(key, "the build key", path)
    digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
    if digest != key:
        raise CascMalformedError(
            f"the build config's MD5 is {digest}, not its name {key}: "
            "a partial or changed file is not read",
            path,
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CascMalformedError(f"the build config is not UTF-8 ({exc})", path) from exc
    pairs: list[tuple[str, str]] = []
    for line in text.split("\n"):
        line = line.removesuffix("\r").strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        pairs.append((name.strip(), value.strip()))

    def one(name: str, parts: int) -> list[str]:
        values = [v for n, v in pairs if n == name]
        if len(values) != 1:
            raise CascMalformedError(
                f"the build config has {len(values)} {name!r} lines; exactly one is needed", path
            )
        cells = values[0].split()
        if len(cells) != parts:
            raise CascMalformedError(
                f"the build config's {name!r} has {len(cells)} values; {parts} expected", path
            )
        return [_hex_key(c, f"the build config's {name!r}", path) for c in cells]

    (root,) = one("root", 1)
    encoding_ckey, encoding_ekey = one("encoding", 2)
    return BuildConfig(
        key=key,
        root=root,
        encoding_ckey=encoding_ckey,
        encoding_ekey=encoding_ekey,
        pairs=tuple(pairs),
    )


def read_build_config(root: Path, build_key: str) -> BuildConfig:
    """Read and check the build config `build_key` names under `root`."""
    key = _hex_key(build_key, "the build key", root / ".build.info")
    path = build_config_path(root, key)
    for folder in (root / _DATA, root / _DATA / _CONFIG, path.parent.parent, path.parent):
        _check_dir(folder)
    return parse_build_config(_read(path, _CONFIG_LIMIT), key, path)


def bucket_of(key: bytes) -> int:
    """The bucket of an encoding key: its first nine bytes XORed together,
    the two nibbles of that XORed again (LAB_FORMATS §10)."""
    x = 0
    for byte in key[:KEY_BYTES]:
        x ^= byte
    return (x & 0xF) ^ (x >> 4)


def _entry_at(entries: bytes, i: int) -> IndexEntry:
    base = i * _ENTRY + KEY_BYTES
    field = int.from_bytes(entries[base : base + 5], "big")
    size = int.from_bytes(entries[base + 5 : base + 9], "little")
    return IndexEntry(archive=field >> _OFFSET_BITS, offset=field & _OFFSET_MASK, size=size)


def parse_index_file(data: bytes, bucket: int, version: int, name: str = "") -> IndexFile:
    """Parse one `.idx` file and check its header: the header hash, version
    7, the bucket its name gives, the spec `4/5/9/30`, the entries block
    inside the file and a whole number of entries. The entries hash is
    checked by `_check_entries` (all files at once); the key order there too."""
    path = Path(name) if name else None
    if len(data) < 8:
        raise CascMalformedError(f"{name}: {len(data)} bytes, too short for a header", path)
    header_size, header_hash = struct.unpack_from("<II", data, 0)
    if header_size < _HEADER_FIELDS.size or header_size > _MAX_HEADER:
        raise CascMalformedError(f"{name}: a header of {header_size} bytes is not read", path)
    if 8 + header_size > len(data):
        raise CascMalformedError(f"{name}: the header runs past the end of the file", path)
    header = data[8 : 8 + header_size]
    got = hashlittle(header)
    if got != header_hash:
        raise CascMalformedError(
            f"{name}: header hash {got:#010x}, the file says {header_hash:#010x}{_RETRY}", path
        )
    idx_version, idx_bucket, extra, *spec, header_u64 = _HEADER_FIELDS.unpack_from(header, 0)
    if idx_version != _INDEX_VERSION:
        raise CascUnsupportedError(
            f"{name}: index version {idx_version}; this reader knows {_INDEX_VERSION}", path
        )
    if tuple(spec) != _SPEC:
        raise CascUnsupportedError(
            f"{name}: entry spec {'/'.join(map(str, spec))}; this reader knows "
            f"{'/'.join(map(str, _SPEC))}",
            path,
        )
    if extra != 0:
        raise CascUnsupportedError(
            f"{name}: extra byte {extra}; this reader knows 0 (its meaning is [verify])", path
        )
    if idx_bucket != bucket:
        raise CascMalformedError(
            f"{name}: the header says bucket {idx_bucket}, the name {bucket}", path
        )
    block = (8 + header_size + 15) & ~15
    if block + 8 > len(data):
        raise CascMalformedError(f"{name}: no entries block after the header", path)
    entries_size, entries_hash = struct.unpack_from("<II", data, block)
    start = block + 8
    if start + entries_size > len(data):
        raise CascMalformedError(
            f"{name}: the entries block ({entries_size} bytes) runs past the end of the file",
            path,
        )
    if entries_size % _ENTRY:
        raise CascMalformedError(
            f"{name}: the entries block ({entries_size} bytes) is not whole {_ENTRY}-byte entries",
            path,
        )
    return IndexFile(
        bucket=bucket,
        version=version,
        header_size=header_size,
        header_hash=header_hash,
        extra=extra,
        entries_hash=entries_hash,
        header_u64=header_u64,
        entries=data[start : start + entries_size],
    )


_KEYS = struct.Struct(f"{KEY_BYTES}s{_ENTRY - KEY_BYTES}x")


def _key_buckets(entries: bytes) -> bytes:
    """`bucket_of` of every entry's key, one byte each, for the whole block
    at once: the nine key-byte columns XORed as big integers."""
    n = len(entries) // _ENTRY
    if n == 0:
        return b""
    x = 0
    for j in range(KEY_BYTES):
        x ^= int.from_bytes(entries[j::_ENTRY], "big")
    nibbles = int.from_bytes(b"\x0f" * n, "big")
    return ((x & nibbles) ^ ((x >> 4) & nibbles)).to_bytes(n, "big")


def _placement(file: IndexFile) -> tuple[int, int]:
    """(entries from the previous bucket, other entries out of place). An
    entry whose key's bucket is not the file's counts in the first when its
    key's bucket is the one before and it is a 30-byte record header at
    offset 30 x the file's bucket; any other counts in the second."""
    previous = (file.bucket - 1) % BUCKETS
    not_here = re.compile(b"[^%s]" % re.escape(bytes([file.bucket])))
    buckets = _key_buckets(file.entries)
    from_previous = out_of_place = 0
    for found in not_here.finditer(buckets):
        i = found.start()
        entry = _entry_at(file.entries, i)
        header = entry.size == _RECORD_HEADER and entry.offset == _RECORD_HEADER * file.bucket
        if buckets[i] == previous and header:
            from_previous += 1
        else:
            out_of_place += 1
    return from_previous, out_of_place


def _check_entries(files: Sequence[IndexFile], names: Sequence[str], data_dir: Path) -> None:
    """Every file's entries hash, and its keys in strictly rising order (the
    lookup's binary search relies on it; every file at 70450 is so); then
    the entries outside their key's bucket are counted (`_placement`)."""
    for file, got, name in zip(
        files, _entries_hashes([f.entries for f in files]), names, strict=True
    ):
        if got != file.entries_hash:
            raise CascMalformedError(
                f"{name}: entries hash {got:#010x}, the file says "
                f"{file.entries_hash:#010x}{_RETRY}",
                data_dir / name,
            )
        keys = list(map(operator.itemgetter(0), _KEYS.iter_unpack(file.entries)))
        if not all(map(operator.lt, keys, keys[1:])):
            raise CascMalformedError(
                f"{name}: the entries are not in strictly rising key order", data_dir / name
            )
        file.keys = keys
        file.from_previous, file.out_of_place = _placement(file)


def load_index(root: Path) -> tuple[CascIndex, tuple[BucketVersion, ...]]:
    """Read every bucket's newest `.idx` under `root/Data/data` into one
    lookup, and say which version each bucket took and which it did not."""
    data_dir = root / _DATA / _DATA_DATA
    for folder in (root / _DATA, data_dir):
        _check_dir(folder)
    try:
        names = [e.name for e in longpath.entries(data_dir)]
    except OSError as exc:
        raise CascUnreadableError(
            f"cannot list {data_dir} ({exc.strerror or exc})", data_dir
        ) from exc
    found: dict[int, list[int]] = {}
    skipped: list[str] = []
    for name in names:
        match = _INDEX_NAME.fullmatch(name)
        if match is None:
            # data.NNN, shmem, index.lock.0, the .lru file: not opened. An
            # `.idx`-like name is listed in the report, and not opened either.
            if _INDEX_LIKE.fullmatch(name):
                skipped.append(name)
            continue
        bucket, version = int(match[1], 16), int(match[2], 16)
        if bucket >= BUCKETS:
            raise CascUnsupportedError(
                f"{name}: bucket {bucket}; this reader knows {BUCKETS}", data_dir / name
            )
        found.setdefault(bucket, []).append(version)
    if not found:
        raise CascMissingError(f"no .idx file in {data_dir}", data_dir)
    files: list[IndexFile] = []
    taken_names: list[str] = []
    for bucket in sorted(found):
        version = max(found[bucket])
        name = f"{bucket:02x}{version:08x}.idx"
        path = data_dir / name
        files.append(parse_index_file(_read(path, _INDEX_LIMIT), bucket, version, name))
        taken_names.append(name)
    _check_entries(files, taken_names, data_dir)
    versions = tuple(
        BucketVersion(
            bucket=f.bucket,
            version=f.version,
            file=name,
            entries=len(f.keys),
            header_size=f.header_size,
            header_hash=f.header_hash,
            extra_bytes=f.extra,
            entries_hash=f.entries_hash,
            header_u64=f.header_u64,
            entries_from_previous_bucket=f.from_previous,
            entries_out_of_place=f.out_of_place,
            not_taken=tuple(sorted((v for v in found[f.bucket] if v != f.version), reverse=True)),
        )
        for f, name in zip(files, taken_names, strict=True)
    )
    return CascIndex(files, skipped), versions


def open_storage(install: Install, flavor: Flavor | str) -> CascStorage:
    """Open one flavor's build in the install's shared `Data/`: its
    `.build.info` row, its build config and the local index, with the
    report. Every file is read and closed before this returns."""
    started = time.perf_counter()
    flavor, row = select_build_row(install, flavor)
    root = install.root
    build_key = _hex_key(row.build_key, "the Active row's Build Key", root / ".build.info")
    config = read_build_config(root, build_key)
    index_started = time.perf_counter()
    index, versions = load_index(root)
    finished = time.perf_counter()
    version = (row.version or "").strip()
    report = CascIndexReport(
        product=flavor.product,
        version=version,
        build=_build_number(version) if version else None,
        build_key=build_key,
        root=config.root,
        encoding_ckey=config.encoding_ckey,
        encoding_ekey=config.encoding_ekey,
        bucket_count=len(versions),
        entry_count=sum(v.entries for v in versions),
        versions=versions,
        newest_taken=tuple(v.bucket for v in versions if v.not_taken),
        missing_buckets=index.missing_buckets,
        skipped_files=index.skipped,
        index_seconds=round(finished - index_started, 3),
        open_seconds=round(finished - started, 3),
    )
    return CascStorage(Path(root), row, config, index, report)


# ─── archives and BLTE (M15-04) ──────────────────────────────────────────────


def _octets(data: Buffer) -> memoryview:
    """`data` as a flat view of unsigned bytes, whatever buffer it is."""
    view = memoryview(data)
    return view if view.format == "B" and view.ndim == 1 else view.cast("B")


def _mode_text(byte: int) -> str:
    """A mode or cipher byte as one character, or `\\xNN` when it is not
    printable ASCII."""
    return chr(byte) if 0x20 < byte < 0x7F else f"\\x{byte:02x}"


def archive_path(root: Path, archive: int) -> Path:
    """`Data/data/data.<archive:03d>` under the install `root`."""
    return root / _DATA / _DATA_DATA / f"data.{archive:03d}"


def parse_record_header(
    data: Buffer, key: bytes, entry: IndexEntry, path: Path | None = None
) -> RecordHeader:
    """The 30-byte record header at the start of `data`, checked against
    the index: its hash field, read back to front, starts with the nine
    bytes of `key`; its size is the entry's; its checksum A is lookup3
    `hashlittle` of the 22 bytes before it, seeded `0x3D6BE971`. A
    mismatch means the index and the archive disagree (one of them was
    rewritten since the index was read) and is refused, never read around."""
    view = _octets(data)
    if len(view) < RECORD_HEADER_SIZE:
        raise CascMalformedError(
            f"a record header is {RECORD_HEADER_SIZE} bytes; {len(view)} are there", path
        )
    field, size, flags, checksum_a, checksum_b = _RECORD_FIELDS.unpack_from(view, 0)
    stored = field[::-1]
    want = bytes(key[:KEY_BYTES])
    if stored[:KEY_BYTES] != want:
        raise CascMalformedError(
            f"the record at offset {entry.offset} of archive {entry.archive} is for the key "
            f"{stored[:KEY_BYTES].hex()}, not {want.hex()}{_RETRY}",
            path,
        )
    if size != entry.size:
        raise CascMalformedError(
            f"the record header says {size} bytes, the index {entry.size}{_RETRY}", path
        )
    got = hashlittle(bytes(view[:_CHECKSUM_A_SPAN]), _CHECKSUM_A_SEED)
    if got != checksum_a:
        raise CascMalformedError(
            f"the record header's checksum is {checksum_a:#010x}, its bytes give "
            f"{got:#010x}{_RETRY}",
            path,
        )
    return RecordHeader(
        hash_field=stored.hex(),
        size=size,
        flags=flags.hex(),
        checksum_a=checksum_a,
        checksum_b=checksum_b,
    )


def parse_blte(data: Buffer, path: Path | None = None) -> BlteLayout:
    """A BLTE's header and chunk table (LAB_FORMATS §10), every size
    checked against the bytes there before any block is touched: the magic,
    a header size that is 0 (one block, the rest of the data) or exactly
    the flag byte `0x0F`, a 24-bit block count (at most `BLOCK_LIMIT`) and
    that many 24-byte rows (stored size, decoded size, MD5), inside the
    data; each block at least its mode byte; the stored sizes adding up to
    exactly the bytes after the header. Data that is not BLTE at all (one
    record at 70450) is refused as unsupported. Nothing is decoded and
    nothing is allocated past the rows."""
    view = _octets(data)
    n = len(view)
    if n < _BLTE_PREAMBLE:
        raise CascMalformedError(f"a BLTE of {n} bytes has no room for its header", path)
    if view[:4] != _BLTE_MAGIC:
        # One record at 70450 is not BLTE: the build config's `patch`
        # manifest, stored as it is (magic `PA`; LAB_FORMATS §10).
        raise CascUnsupportedError(
            f"the record is not BLTE (it starts {bytes(view[:4])!r}); this reader decodes "
            "BLTE only",
            path,
        )
    (header_size,) = struct.unpack_from(">I", view, 4)
    if header_size == 0:
        if n == _BLTE_PREAMBLE:
            raise CascMalformedError("a BLTE with no chunk table and no block", path)
        only = BlteBlock(
            encoded_size=n - _BLTE_PREAMBLE,
            decoded_size=None,
            md5=None,
            mode=_mode_text(view[_BLTE_PREAMBLE]),
        )
        return BlteLayout(header_size=0, blocks=(only,), encoded_size=n, decoded_size=None)
    table = _BLTE_PREAMBLE + _CHUNK_TABLE_HEAD
    if header_size < table:
        raise CascMalformedError(f"a BLTE header of {header_size} bytes holds no chunk table", path)
    if header_size > n:
        raise CascMalformedError(
            f"the BLTE header ({header_size} bytes) runs past the end of the data ({n} bytes)",
            path,
        )
    flag = view[_BLTE_PREAMBLE]
    if flag != _CHUNK_TABLE_FLAG:
        raise CascUnsupportedError(
            f"a BLTE chunk table flag {flag:#04x}; this reader knows {_CHUNK_TABLE_FLAG:#04x}", path
        )
    count = int.from_bytes(view[_BLTE_PREAMBLE + 1 : table], "big")
    if count == 0:
        raise CascMalformedError("a BLTE chunk table of no blocks", path)
    if count > BLOCK_LIMIT:
        raise CascTooLargeError(
            f"a BLTE chunk table of {count} blocks, over the reader's {BLOCK_LIMIT}",
            count,
            BLOCK_LIMIT,
            path,
        )
    if header_size != table + count * _CHUNK_ENTRY.size:
        raise CascMalformedError(
            f"a BLTE header of {header_size} bytes cannot hold {count} chunk rows "
            f"({table + count * _CHUNK_ENTRY.size} bytes)",
            path,
        )
    rows = [_CHUNK_ENTRY.unpack_from(view, table + i * _CHUNK_ENTRY.size) for i in range(count)]
    stored = sum(r[0] for r in rows)
    if header_size + stored != n:
        raise CascMalformedError(
            f"the chunk table's stored sizes add up to {stored} bytes; the BLTE holds "
            f"{n - header_size} after its header",
            path,
        )
    blocks: list[BlteBlock] = []
    at = header_size
    for i, (encoded, decoded, digest) in enumerate(rows):
        if encoded == 0:
            raise CascMalformedError(f"block {i} is 0 bytes: no room for its mode byte", path)
        blocks.append(
            BlteBlock(
                encoded_size=encoded,
                decoded_size=decoded,
                md5=digest.hex(),
                mode=_mode_text(view[at]),
            )
        )
        at += encoded
    return BlteLayout(
        header_size=header_size,
        blocks=tuple(blocks),
        encoded_size=n,
        decoded_size=sum(r[1] for r in rows),
    )


def _refuse_mode(block: memoryview, i: int, path: Path | None) -> NoReturn:
    """Raise for a block whose mode is not `N` or `Z`: `E` names its key,
    the rest are unsupported. The block's payload is never decoded."""
    mode = block[0]
    if mode != ord("E"):
        text = _mode_text(mode)
        what = {"4": "LZ4", "F": "a nested BLTE, which is never recursed into"}.get(text, "")
        raise UnsupportedBlockMode(
            f"block {i} is in mode {text!r}{f' ({what})' if what else ''}; this reader "
            "decodes 'N' and 'Z' only",
            text,
            i,
            path,
        )
    # E: key-name length, key name, IV length, IV, cipher byte, then the data.
    body = block[1:]
    key_len = body[0] if len(body) else 0
    iv_at = 1 + key_len
    if key_len == 0 or iv_at >= len(body):
        raise CascMalformedError(f"block {i} is encrypted, and its key name is cut short", path)
    cipher_at = iv_at + 1 + body[iv_at]
    if cipher_at >= len(body):
        raise CascMalformedError(f"block {i} is encrypted, and its header is cut short", path)
    key_name = bytes(body[1:iv_at])[::-1].hex().upper()
    cipher = _mode_text(body[cipher_at])
    raise EncryptedContent(
        f"block {i} is encrypted with the key {key_name} (cipher {cipher!r}); wowlab does "
        "not decrypt (ADR-0033)",
        key_name,
        i,
        cipher,
        path,
    )


def _inflate(block: memoryview, want: int | None, room: int, i: int, path: Path | None) -> bytes:
    """A `Z` block's payload inflated, never past `want + 1` bytes (or
    `room + 1` when the size is not declared): exactly `want` bytes, one
    whole zlib stream, nothing after it."""
    inflater = zlib.decompressobj()
    bound = (room if want is None else want) + 1
    try:
        out = inflater.decompress(block, bound)
    except zlib.error as exc:
        raise CascMalformedError(f"block {i} is not a zlib stream ({exc})", path) from None
    if want is None and len(out) > room:
        raise CascTooLargeError(
            f"block {i} inflates past the read's cap of {room} bytes", len(out), room, path
        )
    if want is not None and len(out) > want:
        raise CascMalformedError(f"block {i} inflates past its declared {want} bytes", path)
    if not inflater.eof:
        raise CascMalformedError(f"block {i}'s zlib stream is cut short", path)
    if inflater.unused_data:
        raise CascMalformedError(f"block {i} has bytes after its zlib stream", path)
    if want is not None and len(out) != want:
        raise CascMalformedError(
            f"block {i} inflates to {len(out)} bytes; the chunk table says {want}", path
        )
    return out


def decode_blte(data: Buffer, *, limit: int = DECODE_LIMIT, path: Path | None = None) -> bytes:
    """The decoded bytes of one BLTE (`parse_blte` for its layout).

    Before anything is decoded or allocated: the declared decoded size is
    held to `limit` (`CascTooLargeError`), and every block is checked: its
    stored bytes' MD5 against the chunk table (`CascMalformedError`), then
    its mode: `N` and `Z` pass; `E` raises `EncryptedContent` with its key
    name; `4`, `F` and any other byte raise `UnsupportedBlockMode` (an `F`
    block's payload is never parsed, so a nested BLTE is never recursed
    into). Then each block is decoded: `N` is its bytes after the mode byte,
    exactly its declared size; `Z` is one zlib stream inflated to exactly
    its declared size and never a byte past it. A BLTE without a chunk
    table has one block, no MD5 and no declared size; it is held to
    `limit` as it is decoded. A Lab read is checked by the file's content
    key (M15-05) and these MD5s, never by its encoding key."""
    view = _octets(data)
    layout = parse_blte(view, path)
    if layout.decoded_size is not None and layout.decoded_size > limit:
        raise CascTooLargeError(
            f"the BLTE decodes to {layout.decoded_size} bytes, over the read's cap of {limit}",
            layout.decoded_size,
            limit,
            path,
        )
    spans: list[memoryview] = []
    at = layout.header_size or _BLTE_PREAMBLE
    for i, row in enumerate(layout.blocks):
        block = view[at : at + row.encoded_size]
        at += row.encoded_size
        if row.md5 is not None:
            got = hashlib.md5(block, usedforsecurity=False).hexdigest()
            if got != row.md5:
                raise CascMalformedError(
                    f"block {i}'s MD5 is {got}; the chunk table says {row.md5}{_RETRY}", path
                )
        if block[0] not in (_MODE_PLAIN, _MODE_ZLIB):
            _refuse_mode(block, i, path)
        spans.append(block)
    parts: list[Buffer] = []
    for i, (row, block) in enumerate(zip(layout.blocks, spans, strict=True)):
        payload = block[1:]
        if block[0] == _MODE_PLAIN:
            if row.decoded_size is None and len(payload) > limit:
                raise CascTooLargeError(
                    f"block {i} holds {len(payload)} bytes, over the read's cap of {limit}",
                    len(payload),
                    limit,
                    path,
                )
            if row.decoded_size is not None and len(payload) != row.decoded_size:
                raise CascMalformedError(
                    f"block {i} is plain and holds {len(payload)} bytes; the chunk table "
                    f"says {row.decoded_size}",
                    path,
                )
            parts.append(payload)
        else:
            parts.append(_inflate(payload, row.decoded_size, limit, i, path))
    return b"".join(parts)


def read_record(root: Path, entry: IndexEntry, *, limit: int = DECODE_LIMIT) -> bytes:
    """The bytes of one archive record (its 30-byte header and its BLTE)
    that `entry` names, read from `Data/data/data.<archive:03d>` under the
    install `root`.

    A ranged read, as bounded as `snapshot.read_regular_file`: the archive
    is opened with `snapshot.open_regular_file` (read-only, a final link
    refused, a regular file only), the record's range is checked against
    the file's size, the range alone is read (never more than `entry.size`
    bytes, itself held to `limit` plus the header before the open), and the
    descriptor is closed on every path. No `mmap`; no handle outlives the
    call."""
    for folder in (root / _DATA, root / _DATA / _DATA_DATA):
        _check_dir(folder)
    path = archive_path(root, entry.archive)
    if entry.size < RECORD_HEADER_SIZE:
        raise CascMalformedError(
            f"an index entry of {entry.size} bytes cannot hold a record header", path
        )
    if entry.size - RECORD_HEADER_SIZE > limit:
        raise CascTooLargeError(
            f"the record holds {entry.size - RECORD_HEADER_SIZE} bytes of BLTE, over the "
            f"read's cap of {limit}",
            entry.size - RECORD_HEADER_SIZE,
            limit,
            path,
        )
    try:
        fd, st = snapshot.open_regular_file(path)
    except FileNotFoundError:
        raise CascMissingError(f"{path} is not there", path) from None
    except snapshot.UnsafeReadError as exc:
        raise CascUnreadableError(f"cannot read {path} ({exc})", path) from exc
    except OSError as exc:  # refused, or a link (ELOOP)
        raise CascUnreadableError(f"cannot read {path} ({exc.strerror or exc})", path) from exc
    chunks: list[bytes] = []
    total = 0
    try:
        end = entry.offset + entry.size
        if end > st.st_size:
            raise CascMalformedError(
                f"the record ends at byte {end}, past the end of {path.name} "
                f"({st.st_size} bytes){_RETRY}",
                path,
            )
        os.lseek(fd, entry.offset, os.SEEK_SET)
        while total < entry.size:
            chunk = os.read(fd, entry.size - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as exc:
        raise CascUnreadableError(f"cannot read {path} ({exc.strerror or exc})", path) from exc
    finally:
        os.close(fd)
    if total < entry.size:
        raise CascMalformedError(
            f"{path.name} ended {entry.size - total} bytes into the record{_RETRY}", path
        )
    return b"".join(chunks)


def read_encoded(storage: CascStorage, ekey: bytes, *, limit: int = DECODE_LIMIT) -> bytes | None:
    """The decoded bytes of the file whose encoding key is `ekey` (at least
    its first nine bytes), or `None` when the local index does not list it
    (`BucketMissingError` when its bucket has no index file). The record
    header is checked against the index (`parse_record_header`) and the
    BLTE decoded under `limit` (`decode_blte`)."""
    entry = storage.index.find(ekey)
    if entry is None:
        return None
    path = archive_path(storage.root, entry.archive)
    record = read_record(storage.root, entry, limit=limit)
    view = memoryview(record)
    parse_record_header(view, ekey, entry, path)
    return decode_blte(view[RECORD_HEADER_SIZE:], limit=limit, path=path)
