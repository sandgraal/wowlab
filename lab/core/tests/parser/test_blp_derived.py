"""`blp` against the derived fixture of the owner's install (M15-07;
ADR-0033 layer b): `fixtures/macos-70959/derived/blp.json`, written by
`scripts/casc_facts.py --blp` from real textures (header facts, counts and
MD5s of the decoded pixels; never a byte of a file).

What can be checked without the install: that `blp`'s rules give, for
every header recorded, the format or the refusal recorded; that the census
by header class adds up to the census by outcome under those rules; that
the computed mip 0 size is what the files hold; that no texture met falls
where the two DXT rules disagree; and that the record holds no bytes. The
pixels themselves are checked against the recorded MD5s by
`test_blp_live.py` (`@live`).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import blp

pytestmark = pytest.mark.parser

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos-70959" / "derived" / "blp.json"
FACTS: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
CLASS = re.compile(r"encoding (\d+), alpha depth (\d+), alpha type (\d+)")
MD5 = re.compile(r"[0-9a-f]{32}")


def _header(encoding: int, depth: int, kind: int, **fields: int) -> blp.BlpHeader:
    offsets = [fields.get("mip0_offset", 1172)] + [0] * 15
    sizes = [fields.get("stored_mip0_size", 1)] + [0] * 15
    return blp.BlpHeader(
        version=fields.get("version", 1),
        encoding=encoding,
        alpha_depth=depth,
        alpha_type=kind,
        has_mips=fields.get("has_mips", 1),
        width=fields.get("width", 4),
        height=fields.get("height", 4),
        mip_offsets=tuple(offsets),
        mip_sizes=tuple(sizes),
    )


def _outcome(header: blp.BlpHeader) -> str:
    try:
        return blp.format_of(header)
    except blp.BlpError as exc:
        return type(exc).__name__


def _rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = list(FACTS["textures"])
    if FACTS["timing"] is not None:
        rows.append(FACTS["timing"])
    return rows


def test_the_record_is_the_derived_blp_record_of_its_build() -> None:
    assert FACTS["kind"] == "blp-derived"
    assert FACTS["schema"] == 1
    assert FACTS["made_by"] == "scripts/casc_facts.py --blp"
    assert FACTS["version"].endswith(".70959")
    assert FACTS["limits"] == {"max_dimension": blp.MAX_DIMENSION, "max_pixels": blp.MAX_PIXELS}


@pytest.mark.parametrize("row", _rows(), ids=lambda r: str(r["fdid"]))
def test_each_recorded_texture_reads_as_recorded(row: dict[str, Any]) -> None:
    """The rules give the recorded format or refusal; the computed mip 0
    size is the recorded one and the one the file stores; both alpha
    fields are recorded."""
    h = row["header"]
    sizes = {k: h[k] for k in ("version", "has_mips", "width", "height", "mip0_offset")}
    header = _header(
        h["encoding"],
        h["alpha_depth"],
        h["alpha_type"],
        stored_mip0_size=h["stored_mip0_size"],
        **sizes,
    )
    assert header.mip_count == 1  # only slot 0 is rebuilt here
    outcome = _outcome(header)
    if "refused" in row:
        assert outcome == row["refused"]
        assert "rgba_md5" not in row
        return
    assert outcome == row["format"]
    assert MD5.fullmatch(row["rgba_md5"])
    size = blp.mip0_size(outcome, h["width"], h["height"], h["alpha_depth"])
    assert size == row["mip0_size"] == h["stored_mip0_size"]
    assert h["mip0_offset"] + size <= row["file_size"]
    if h["encoding"] == blp.ENCODING_DXT:
        assert row["wiki_rule"] == row["wow_export_rule"] == outcome
    else:
        assert row["wiki_rule"] is None and row["wow_export_rule"] is None


def test_at_least_three_textures_decoded_of_three_encodings() -> None:
    decoded = [r for r in _rows() if "rgba_md5" in r]
    assert len(decoded) >= 3
    assert {"dxt1", "dxt5", "palette"} <= {r["format"] for r in decoded}


@pytest.mark.parametrize("sample", ["root_sample", "character_sample"])
def test_the_census_by_header_adds_up_to_the_census_by_outcome(sample: str) -> None:
    census = FACTS[sample]
    expected: Counter[str] = Counter()
    disagree = 0
    for label, count in census["by_header"].items():
        match = CLASS.fullmatch(label)
        assert match, label
        encoding, depth, kind = (int(g) for g in match.groups())
        expected[_outcome(_header(encoding, depth, kind))] += count
        wiki = blp.wiki_rule(kind)
        if encoding == blp.ENCODING_DXT and wiki is not None:
            disagree += count * (wiki != blp.wow_export_rule(depth, kind))
    assert dict(expected) == census["by_outcome"]
    assert census["dxt_rules_disagree"] == disagree == 0
    assert census["stored_mip0_size_differs"] == 0
    assert sum(census["by_header"].values()) == census["blp2"]


def test_the_timing_row_is_a_1024_square_dxt1_within_the_target() -> None:
    timing = FACTS["timing"]
    assert timing is not None
    assert (timing["header"]["width"], timing["header"]["height"]) == (1024, 1024)
    assert timing["format"] == "dxt1"
    assert timing["decode_seconds"] <= 0.5


def test_the_record_holds_no_bytes() -> None:
    """ADR-0033: facts, never a file's or the pixels' bytes. Every string
    is short; the only hex strings are 32-digit MD5s under `hash`/`md5`
    names (gitleaks' allowlist does not need them)."""

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str):
            assert len(value) < 200, key
            if re.fullmatch(r"[0-9a-f]{16,}", value):
                assert key in {"content_hash", "rgba_md5"} and MD5.fullmatch(value), key

    walk(FACTS)
