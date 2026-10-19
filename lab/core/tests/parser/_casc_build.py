"""Constructed CASC files for the `casc` tests (M15-03, M15-04). Everything here is
`constructed` (L8, ADR-0033 layer a): no byte of a real install file. The
shapes follow `docs/LAB_FORMATS.md` §10, and the derived fixture
(`fixtures/macos-70450/derived/casc-index.json`) for the values a real
install has (header size 16, `Unk0` 7, spec `4/5/9/30`).

The entries hash here is computed entry by entry with `casc.hashlittle2`,
the scalar function, never with the reader's side-by-side lanes, so it is an
independent oracle for them; `hashlittle2` itself is graded on lookup3's
published values.
"""

from __future__ import annotations

import hashlib
import random
import struct
import zlib
from collections.abc import Iterable, Sequence
from pathlib import Path

from wowlab_core import casc

PRODUCT = "wow_test_product"  # invented; nothing in the library knows it
FLAVOR = "_test_flavor_"
VERSION = "9.8.7.65432"
BUILD_INFO_HEADER = (
    "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|CDN Key!HEX:16|Install Key!HEX:16|"
    "IM Size!DEC:4|CDN Path!STRING:0|CDN Hosts!STRING:0|CDN Servers!STRING:0|Tags!STRING:0|"
    "Armadillo!STRING:0|Last Activated!STRING:0|Version!STRING:0|KeyRing!HEX:16|Product!STRING:0"
)

Entry = tuple[bytes, int, int, int]  # key (9+ bytes), archive, offset, size


def chained(body: bytes) -> int:
    """The entries hash: `hashlittle2` over each 18-byte entry in turn."""
    pc = pb = 0
    for i in range(0, len(body), 18):
        pc, pb = casc.hashlittle2(body[i : i + 18], pc, pb)
    return pc


def key_in(bucket: int, rng: random.Random) -> bytes:
    """A random nine-byte key whose bucket is `bucket`."""
    head = bytes(rng.randrange(256) for _ in range(8))
    for last in range(256):
        key = head + bytes([last])
        if casc.bucket_of(key) == bucket:
            return key
    raise AssertionError("unreachable: every bucket has 16 last bytes")


def entry_bytes(entries: Iterable[Entry]) -> bytes:
    return b"".join(
        key[:9] + ((archive << 30) | offset).to_bytes(5, "big") + size.to_bytes(4, "little")
        for key, archive, offset, size in entries
    )


def idx_bytes(
    bucket: int,
    entries: Sequence[Entry],
    *,
    header_size: int = 16,
    version: int = 7,
    spec: tuple[int, int, int, int] = (4, 5, 9, 30),
    extra: int = 0,
    max_size: int = 0xFFC0000000,
    sort: bool = True,
    header_hash: int | None = None,
    entries_hash: int | None = None,
    entries_size: int | None = None,
    pad_to: int = 0,
) -> bytes:
    """An `.idx` file: header (hashed), padding to 16, the entries block."""
    header = struct.pack("<HBBBBBBQ", version, bucket, extra, *spec, max_size)
    header += bytes(header_size - len(header))
    hh = casc.hashlittle(header) if header_hash is None else header_hash
    out = struct.pack("<II", header_size, hh) + header
    out += bytes(-len(out) % 16)
    rows = sorted(entries, key=lambda e: e[0][:9]) if sort else list(entries)
    body = entry_bytes(rows)
    eh = chained(body) if entries_hash is None else entries_hash
    size = len(body) if entries_size is None else entries_size
    out += struct.pack("<II", size, eh) + body
    return out + bytes(max(0, pad_to - len(out)))


def idx_name(bucket: int, version: int) -> str:
    return f"{bucket:02x}{version:08x}.idx"


def build_config_text(root: str, ckey: str, ekey: str, *, extra: str = "") -> bytes:
    return (
        "# Build Configuration\n\n"
        f"root = {root}\n"
        f"encoding = {ckey} {ekey}\n"
        "encoding-size = 1000 970\n"
        f"build-name = TEST-{VERSION}\n"
        f"{extra}"
    ).encode()


def md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def make_install(
    tmp: Path,
    *,
    indexes: dict[str, bytes] | None = None,
    config: bytes | None = None,
    rows: Sequence[tuple[str, str]] | None = None,
    build_key: str | None = None,
    write_config: bool = True,
) -> tuple[Path, dict[str, bytes]]:
    """A synthetic install root under `tmp`: `.build.info`, one flavor
    folder, the build config, the `.idx` files (by name), and the files the
    reader must never open: `shmem`, `index.lock.0`, `data.000` and an
    `.lru` file. Returns the root and the index files written. `rows` are
    `(Active, Product)` pairs for `.build.info` (default: one Active row for
    the flavor's product)."""
    root = tmp / "World of Warcraft"
    rng = random.Random(1503)
    ekey = key_in(3, rng) + bytes(7)
    config = config or build_config_text("ab" * 16, "cd" * 16, ekey.hex())
    key = build_key if build_key is not None else md5(config)
    lines = [BUILD_INFO_HEADER]
    for active, product in rows if rows is not None else [("1", PRODUCT)]:
        cells = ["us", active, key, "ef" * 16, "", "", "tpr/wow", "", "", "enUS speech?:", "", ""]
        lines.append("|".join([*cells, VERSION, "", product]))
    root.mkdir(parents=True)
    (root / ".build.info").write_text("\n".join(lines) + "\n", encoding="utf-8")
    flavor = root / FLAVOR
    flavor.mkdir()
    (flavor / ".flavor.info").write_text(f"Product Flavor!STRING:0\n{PRODUCT}\n", encoding="utf-8")
    if write_config and len(key) == 32:
        config_dir = root / "Data" / "config" / key[0:2] / key[2:4]
        config_dir.mkdir(parents=True)
        (config_dir / key).write_bytes(config)
    data = root / "Data" / "data"
    data.mkdir(parents=True)
    if indexes is None:
        indexes = {
            idx_name(3, 2): idx_bytes(
                3, [(ekey, 0, 64, 1000)] + [(key_in(3, rng), 1, 30 * i, 100 + i) for i in range(5)]
            ),
            idx_name(9, 1): idx_bytes(9, [(key_in(9, rng), 2, 30 * i, 50) for i in range(4)]),
        }
    for name, blob in indexes.items():
        (data / name).write_bytes(blob)
    (data / "shmem").write_bytes(b"\x01" * 64)
    (data / "index.lock.0").write_bytes(b"")
    (data / "data.000").write_bytes(b"\x00" * 128)
    (data / "0000000000000001.lru").write_bytes(b"\x00" * 16)
    return root, indexes


def tree_state(root: Path) -> dict[str, tuple[int, int]]:
    """Every path under `root` with its size and modification time."""
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
    }


# ─── archive records and BLTE (M15-04) ──────────────────────────────────────

CHECKSUM_A_SEED = 0x3D6BE971  # LAB_FORMATS §10, amendment of 2026-10-19
RECORD_HEADER = 30

Block = tuple[bytes, int]  # a block's stored bytes (mode byte first), its decoded size


def n_block(payload: bytes) -> Block:
    return b"N" + payload, len(payload)


def z_block(payload: bytes, level: int = 6) -> Block:
    return b"Z" + zlib.compress(payload, level), len(payload)


def e_block(
    key_name: bytes = bytes.fromhex("3ecb6a1278505050"),
    iv: bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08",
    cipher: bytes = b"S",
    data: bytes = b"\xa5" * 40,
    decoded: int = 39,
) -> Block:
    """An `E` block as 70450 has them: key-name length 8, the key name
    (stored little-endian), IV length 8, the IV, the cipher byte, then the
    encrypted bytes (random-looking filler here)."""
    body = bytes([len(key_name)]) + key_name + bytes([len(iv)]) + iv + cipher + data
    return b"E" + body, decoded


def blte(
    blocks: Sequence[Block],
    *,
    table: bool = True,
    flag: int = 0x0F,
    md5s: Sequence[bytes | None] | None = None,
    decoded: Sequence[int | None] | None = None,
    encoded: Sequence[int | None] | None = None,
    header_size: int | None = None,
    count: int | None = None,
) -> bytes:
    """A BLTE: magic, header size, then either one block with no chunk
    table (`table=False`), or the chunk table (flag, 24-bit count, per block
    stored size, decoded size, MD5 of the stored bytes) and the blocks.
    Any row field may be overridden (None keeps the true value)."""
    body = b"".join(b for b, _ in blocks)
    if not table:
        (only,) = blocks
        return b"BLTE" + struct.pack(">I", 0) + only[0]
    rows = b""
    for i, (stored, size) in enumerate(blocks):
        enc = len(stored) if encoded is None or encoded[i] is None else encoded[i]
        dec = size if decoded is None or decoded[i] is None else decoded[i]
        digest = hashlib.md5(stored, usedforsecurity=False).digest()
        if md5s is not None and md5s[i] is not None:
            digest = md5s[i]  # type: ignore[assignment]
        rows += struct.pack(">II16s", enc, dec, digest)
    n = len(blocks) if count is None else count
    hs = 12 + 24 * len(blocks) if header_size is None else header_size
    return b"BLTE" + struct.pack(">I", hs) + bytes([flag]) + n.to_bytes(3, "big") + rows + body


def record(
    key: bytes,
    data: bytes,
    *,
    size: int | None = None,
    flags: bytes = b"\x00\x00",
    checksum_a: int | None = None,
    checksum_b: int = 0x12345678,
    full_key: bool = False,
) -> bytes:
    """An archive record: the 30-byte header (the key read back to front in
    the 16-byte hash field: nine bytes and seven zeros, or all sixteen with
    `full_key`, the two forms met at 70450; the size with the header; the
    flags; checksum A, lookup3 of the 22 bytes before it seeded 0x3D6BE971;
    checksum B, not checked), then `data`."""
    field = (key[:16] if full_key else key[:9] + bytes(7))[::-1]
    total = RECORD_HEADER + len(data) if size is None else size
    head = field + struct.pack("<I", total) + flags
    a = casc.hashlittle(head, CHECKSUM_A_SEED) if checksum_a is None else checksum_a
    return head + struct.pack("<II", a, checksum_b) + data
