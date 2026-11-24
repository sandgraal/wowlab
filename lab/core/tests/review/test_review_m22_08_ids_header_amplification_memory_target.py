# Probe from review of m22/08-addon-lookups; reproduces a ~22 KB `X-Item-IDS` directive making the reader build a 16-million-id list (64 MB cap: 87 KB -> 77 s, 1.2 GB RSS)
"""CONSTRUCTED INPUT (L8, hostile case). `questiedb.MAX_IDS_BYTES` is 64 MiB,
about 500 times what the owner's largest id header can inflate to (23,978
item ids, at most five CBOR bytes each, about 120 KB). The cap bounds the
bytes inflated, not what they cost: zlib of a CBOR array of one-byte ids
compresses about 1000:1, so a directive of a few dozen KB becomes tens of
millions of Python ints, then one row lookup per id. Measured in review on
a constructed TOC whose `X-Item-IDS` was 86,996 base64 bytes inflating to
the cap: `LookupIndex.for_flavor(...).search("x", kinds=["item"])` took
77.3 s and 1,181 MB peak RSS, and the UI runs one `lookup` per pause in
typing (up to four at once). This probe uses 16 MiB (a quarter of the cap,
still about 130 times the real header), so it fails in about 20 s and
passes at once when the header is refused. The positive control is a
header of the real size (30,000 ids), which reads."""

from __future__ import annotations

import base64
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _questie_build import place, toc_text

from wowlab_core import questiedb


def _store(tmp_path: Path, ids_cbor: bytes) -> questiedb.QuestieStore:
    header = base64.b64encode(zlib.compress(ids_cbor, 9))
    toc = toc_text({k: {} for k in questiedb.KINDS}).replace(
        b"## X-Item-IDS: ", b"## X-Item-IDS: " + header + b"\n## X-Was-Item-IDS: ", 1
    )
    return questiedb.open_store(place(tmp_path / "flavor", toc))


def _array(ids: bytes, n: int) -> bytes:
    return b"\x9a" + struct.pack(">I", n) + ids


def test_a_header_of_the_real_size_reads(tmp_path: Path) -> None:
    n = 30_000
    ids = b"".join(b"\x19" + struct.pack(">H", 1 + i % 60_000) for i in range(n))
    assert len(questiedb.QuestieStore.ids(_store(tmp_path, _array(ids, n)), "Item")) == n


def test_a_small_directive_cannot_make_sixteen_million_ids(tmp_path: Path) -> None:
    n = 16 * 1024 * 1024 - 5
    store = _store(tmp_path, _array(b"\x01" * n, n))
    with pytest.raises(questiedb.QuestieDBError):
        store.ids("Item")
