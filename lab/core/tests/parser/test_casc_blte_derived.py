"""`casc`'s archive records and BLTE against the derived fixture of the
owner's install (M15-04; ADR-0033 layer b): what `scripts/casc_facts.py
--blte` read from the real archives at 1.60.1.70450, never their bytes.
Provenance: `fixtures/README.md`, row `macos-70450/derived/casc-blte.json`.

Each check here is one the real files answer independently of the reader:
the record header's checksum A recomputed from its recorded fields; the
BLTE header rebuilt from its recorded chunk table and hashed, against the
encoding key the build config names (in `casc-index.json`, written by
another run); the decoded encoding file's MD5 against the content key the
build config names; the encoding record's place against the index row the
M15-03 run recorded.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import casc

pytestmark = pytest.mark.parser

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos-70450" / "derived"
FACTS: dict[str, Any] = json.loads((FIXTURE / "casc-blte.json").read_text(encoding="utf-8"))
INDEX: dict[str, Any] = json.loads((FIXTURE / "casc-index.json").read_text(encoding="utf-8"))
SEED_A = 0x3D6BE971  # LAB_FORMATS §10, amendment of 2026-10-19


def _record(why: str) -> dict[str, Any]:
    (row,) = [r for r in FACTS["records"] if r["why"].startswith(why)]
    return row


def _header_bytes(blte: dict[str, Any]) -> bytes:
    """The BLTE header as the recorded chunk table lays it out."""
    blocks = blte["blocks"]
    rows = b"".join(
        struct.pack(">II16s", b["encoded_size"], b["decoded_size"], bytes.fromhex(b["md5"]))
        for b in blocks
    )
    head = b"BLTE" + struct.pack(">I", blte["header_size"])
    return head + b"\x0f" + len(blocks).to_bytes(3, "big") + rows


def _headers() -> list[dict[str, Any]]:
    return [*FACTS["records"], *FACTS["segment_headers"]]


def test_the_record_is_the_build_it_names() -> None:
    assert FACTS["kind"] == "casc-blte-derived" and FACTS["schema"] == 1
    assert (FACTS["product"], FACTS["version"], FACTS["build"]) == (
        INDEX["product"],
        INDEX["version"],
        INDEX["build"],
    )
    assert FACTS["decode_limit"] == casc.DECODE_LIMIT
    assert len(FACTS["records"]) == 3 and len(FACTS["segment_headers"]) == 2


@pytest.mark.parametrize("i", range(5))
def test_checksum_a_is_lookup3_of_the_22_bytes_before_it(i: int) -> None:
    """Rebuilt from the recorded fields: the hash field (stored back to
    front), the size, the two flag bytes."""
    row = _headers()[i]
    h = row["record_header"]
    head = bytes.fromhex(h["hash_field"])[::-1] + struct.pack("<I", h["size"])
    head += bytes.fromhex(h["flags"])
    assert len(head) == 22
    assert casc.hashlittle(head, SEED_A) == h["checksum_a"]


@pytest.mark.parametrize("i", range(5))
def test_the_hash_field_starts_with_the_index_key_and_the_size_is_the_entrys(i: int) -> None:
    row = _headers()[i]
    h = row["record_header"]
    assert h["hash_field"][: casc.KEY_BYTES * 2] == row["key"]
    assert h["size"] == row["size"]


def test_the_encoding_record_is_where_the_index_run_found_it() -> None:
    enc = _record("the build config's encoding file")
    (row,) = [r for r in INDEX["key_rows"] if r["why"] == "the build config's encoding key"]
    assert (enc["key"], enc["archive"], enc["offset"], enc["size"]) == (
        row["key"],
        row["archive"],
        row["offset"],
        row["size"],
    )
    # Its header holds only the nine index bytes; the other seven are zero.
    assert enc["record_header"]["hash_field"][casc.KEY_BYTES * 2 :] == "00" * 7
    assert enc["record_header"]["flags"] == "0000"


def test_the_encoding_key_is_the_md5_of_the_blte_header_and_chunk_table() -> None:
    """Settles the glossary's [verify] for a chunked file: the encoding key
    the build config names is the MD5 of the BLTE header (magic, size, flag,
    count, rows), not of the whole stored file."""
    enc = _record("the build config's encoding file")
    header = _header_bytes(enc["blte"])
    assert len(header) == enc["blte"]["header_size"] == 12 + 24 * len(enc["blte"]["blocks"])
    assert hashlib.md5(header).hexdigest() == INDEX["encoding_ekey"]


def test_a_whole_key_hash_field_is_the_md5_of_its_blte_header() -> None:
    full = _record("first own-bucket record of bucket 0 whose header holds the whole")
    assert full["record_header"]["hash_field"][casc.KEY_BYTES * 2 :].strip("0")
    assert (
        hashlib.md5(_header_bytes(full["blte"])).hexdigest() == full["record_header"]["hash_field"]
    )


@pytest.mark.parametrize("i", range(3))
def test_the_chunk_table_adds_up_to_the_record(i: int) -> None:
    row = FACTS["records"][i]
    blte = row["blte"]
    blocks = blte["blocks"]
    assert blte["header_size"] == 12 + 24 * len(blocks)
    stored = blte["header_size"] + sum(b["encoded_size"] for b in blocks)
    assert stored == blte["encoded_size"] == row["size"] - casc.RECORD_HEADER_SIZE
    assert blte["decoded_size"] == sum(b["decoded_size"] for b in blocks)
    for b in blocks:
        if b["mode"] == "N":
            assert b["encoded_size"] == b["decoded_size"] + 1


def test_the_encoding_file_decodes_to_its_content_key() -> None:
    enc = _record("the build config's encoding file")
    assert [b["mode"] for b in enc["blte"]["blocks"]] == ["N", "Z", "N", "N", "N", "N", "Z"]
    assert enc["outcome"] == {
        "decoded_size": INDEX["encoding_size"]["decoded"],
        "decoded_md5": INDEX["encoding_ckey"],
    }
    assert enc["blte"]["encoded_size"] == INDEX["encoding_size"]["encoded"]


def test_the_encrypted_record_is_refused_with_its_key_name() -> None:
    row = _record("first own-bucket record of bucket 0 with an encrypted block")
    outcome = row["outcome"]
    assert outcome["error"] == "EncryptedContent" and outcome["cipher"] == "S"
    assert row["blte"]["blocks"][outcome["block"]]["mode"] == "E"
    name = outcome["key_name"]
    assert len(name) == 16 and name == name.upper() and int(name, 16) >= 0
    assert name in FACTS["census"]["encrypted_key_names"]


def test_the_segment_headers_are_the_shifted_index_rows() -> None:
    """The index's shifted entries (one bucket on from their key's) are
    30-byte record headers with no BLTE, flags `0100`, a whole hash field
    ending in the bytes every one shares."""
    first, last = FACTS["segment_headers"]
    (row,) = [r for r in INDEX["key_rows"] if "whose key is not" in r["why"]]
    assert (first["key"], first["archive"], first["offset"], first["size"]) == (
        row["key"],
        row["archive"],
        row["offset"],
        row["size"],
    )
    for seg in (first, last):
        assert seg["size"] == seg["record_header"]["size"] == casc.RECORD_HEADER_SIZE
        assert seg["offset"] == casc.RECORD_HEADER_SIZE * seg["file_bucket"]
        assert seg["record_header"]["flags"] == "0100"
        assert seg["record_header"]["hash_field"].endswith("00552169b0198954679edc74b430")
        key = bytes.fromhex(seg["key"])
        assert (casc.bucket_of(key) + 1) % casc.BUCKETS == seg["file_bucket"]


def test_the_census_met_n_z_and_e_only() -> None:
    """Every 256th own-bucket record of every bucket: the block modes the
    client's archives hold at 70450. No `4`, no `F`; `E` in a few."""
    census = FACTS["census"]
    assert census["step"] == 256
    assert set(census["blocks_by_mode"]) == {"N", "Z", "E"}
    assert census["records"] == census["decoded"] + sum(census["refused"].values())
    assert set(census["refused"]) == {"EncryptedContent"}
    assert sum(census["encrypted_key_names"].values()) == census["refused"]["EncryptedContent"]
    assert census["over_cap"] == 0 and census["chunkless"] == 0
    assert census["flags"] == {"0000": census["records"]}
    assert sum(census["records_by_block_count"].values()) == census["records"]
    assert 0 < census["hash_field_full"] < census["records"]


def test_every_e_block_has_an_eight_byte_key_name_an_eight_byte_iv_and_salsa20() -> None:
    """All 19 `E` blocks, read from each block's own prefix (not only the
    first one of a record, where the decoder stops)."""
    census = FACTS["census"]
    assert census["e_block_shapes"] == {
        "key_name_bytes=8 iv_bytes=8 cipher=S": census["blocks_by_mode"]["E"]
    }


def test_every_sampled_records_index_key_starts_the_md5_of_its_blte_header() -> None:
    """The EKey definition on every record sampled, not only the three with
    a recorded chunk table."""
    census = FACTS["census"]
    assert census["header_md5_starts_with_key"] == census["records"] == 4079
