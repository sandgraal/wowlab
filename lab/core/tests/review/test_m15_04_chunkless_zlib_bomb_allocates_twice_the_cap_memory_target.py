# Probe from review of m15/04-casc-blte; reproduces a chunkless BLTE zlib bomb making decode_blte allocate about twice its cap before it refuses
"""Constructed (not a real capture): a BLTE with no chunk table whose one
`Z` block is a zlib stream of zeros far past the cap, about 130 KB stored.
M15-04's ticket asks that the decoder "never allocates more than the cap"
(the security-review clause); `_inflate` asks zlib for `limit + 1` bytes in
one call, and CPython's zlib grows its output in blocks and then copies them
into one bytes object, so the peak is about `2 * limit` before
`CascTooLargeError` is raised. At the default `DECODE_LIMIT` (256 MiB) that
is about 514 MiB from a record of about 1 MB (measured in review: peak /
cap = 2.008). A 32 MiB cap is used here to keep the probe light; the ratio
does not depend on it. The positive control is the same bomb behind a chunk
table that declares its size, which never inflates past it. Nothing reads or
writes a real install.
"""

from __future__ import annotations

import hashlib
import struct
import tracemalloc
import zlib

import pytest

from wowlab_core import casc

CAP = 32 * 1024 * 1024
SLACK = CAP // 4  # the cap plus a quarter: room for the input and the bookkeeping

BOMB = zlib.compress(bytes(4 * CAP), 9)  # constructed: zeros, about 130 KB stored


def _chunkless(block: bytes) -> bytes:
    return b"BLTE" + struct.pack(">I", 0) + block


def _peak(data: bytes) -> int:
    tracemalloc.start()
    try:
        with pytest.raises(casc.CascError):
            casc.decode_blte(data, limit=CAP)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def test_a_chunkless_zlib_bomb_stays_within_the_cap_constructed() -> None:
    peak = _peak(_chunkless(b"Z" + BOMB))
    assert peak <= CAP + SLACK, f"peak {peak} bytes is {peak / CAP:.2f} x the {CAP}-byte cap"


def test_positive_control_a_declared_bomb_stays_small_constructed() -> None:
    block = b"Z" + BOMB
    row = struct.pack(">II16s", len(block), 100, hashlib.md5(block).digest())
    data = b"BLTE" + struct.pack(">I", 12 + 24) + b"\x0f" + (1).to_bytes(3, "big") + row + block
    assert _peak(data) < 4 * 1024 * 1024
