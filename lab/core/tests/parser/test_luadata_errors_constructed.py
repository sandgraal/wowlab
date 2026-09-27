"""CONSTRUCTED inputs (L8: boundary cases for error positions and messages).

`wowlab_core.luadata` positions a refusal by counting line breaks before it
(CRLF, LFCR, LF and CR each end one line, pairs taken greedily from the
left). Once the prefix mixes pair orders, it counts in slices of
`_POSITION_CHUNK` bytes. These tests shrink the slice so that a cut lands at
every offset, including inside a CR/LF pair and inside long alternating
runs, and compare the result with a reference counter (the leftmost-first
regex matching the parser used before, one match at a time). None of them
measures time.
"""

from __future__ import annotations

import itertools
import random
import re

import pytest

from wowlab_core import luadata

pytestmark = pytest.mark.parser

_PAIR = re.compile(rb"\r\n|\n\r")


def _reference(data: bytes, offset: int) -> tuple[int, int]:
    line_start = max(data.rfind(b"\n", 0, offset), data.rfind(b"\r", 0, offset)) + 1
    breaks = data.count(b"\n", 0, line_start) + data.count(b"\r", 0, line_start)
    pairs = sum(1 for _ in _PAIR.finditer(data, 0, line_start))
    return breaks - pairs + 1, offset - line_start + 1


def _check_every_offset(data: bytes) -> None:
    for offset in range(len(data) + 1):
        assert luadata._position(data, offset) == _reference(data, offset), (data, offset)


CHUNKS = [1, 2, 3, 4, 5, 7, 8, 64]


@pytest.mark.parametrize("chunk", CHUNKS, ids=[f"constructed-chunk-{c}" for c in CHUNKS])
def test_constructed_every_short_break_sequence_positions_like_the_reference(
    monkeypatch: pytest.MonkeyPatch, chunk: int
) -> None:
    """Every string of up to seven bytes over CR, LF and `x`, followed by a
    refused byte: all pair orders, every run length, every cut."""
    monkeypatch.setattr(luadata, "_POSITION_CHUNK", chunk)
    for length in range(8):
        for combo in itertools.product(b"\r\nx", repeat=length):
            _check_every_offset(bytes(combo) + b"@")


@pytest.mark.parametrize("chunk", CHUNKS, ids=[f"constructed-chunk-{c}" for c in CHUNKS])
def test_constructed_random_documents_position_like_the_reference(
    monkeypatch: pytest.MonkeyPatch, chunk: int
) -> None:
    """Random mixes, some after a pure-CRLF prefix (the part counted with
    `bytes.count` alone), and long runs of line breaks only."""
    monkeypatch.setattr(luadata, "_POSITION_CHUNK", chunk)
    rng = random.Random(chunk)
    alphabets = [b"\r\n", b"\r\nx", b"\r\n\r\n\r\nxy", b"\r\r\nx", b"\n\n\rx"]
    for _ in range(300):
        alphabet = rng.choice(alphabets)
        body = bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        prefix = b"ab\r\n" * rng.randint(0, 6) if rng.random() < 0.4 else b""
        _check_every_offset(prefix + body + b"@")


@pytest.mark.parametrize(
    "data",
    [
        b"a\r\n" * 40 + b"\n" + b"b\r\n" * 40 + b"@",  # one stray LF in CRLF
        b"a\r\n" * 40 + b"\r" + b"b\r\n" * 40 + b"@",  # one stray CR in CRLF
        b"\n\r" * 50 + b"@",  # LFCR throughout: one run, no safe cut anywhere
        b"\n" + b"\r\n" * 50 + b"@",  # alternating run starting with LF
        b"\r\n\n" * 40 + b"@",  # the round-4 memory shape
        b"0,\r\n0,\n" * 30 + b"@",  # CRLF and LF lines, no LFCR
        b"\r\r\n\n" * 30 + b"@",
    ],
    ids=[
        "constructed-stray-lf",
        "constructed-stray-cr",
        "constructed-lfcr-run",
        "constructed-lf-then-crlf-run",
        "constructed-cr-lf-lf",
        "constructed-crlf-and-lf-lines",
        "constructed-doubled-breaks",
    ],
)
def test_constructed_boundary_documents_position_like_the_reference_at_every_cut(
    monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    for chunk in range(1, 12):
        monkeypatch.setattr(luadata, "_POSITION_CHUNK", chunk)
        _check_every_offset(data)


def test_constructed_a_cut_inside_a_pair_counts_the_pair_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`x\\n\\r\\n\\r@` in slices of two bytes is `x\\n` | `\\r\\n` | `\\r@`:
    every cut splits an LFCR pair. Lua pairs `\\n\\r` twice, so the refused
    byte is on line 3; counting each slice on its own would pair `\\r\\n`
    once and give line 4."""
    monkeypatch.setattr(luadata, "_POSITION_CHUNK", 2)
    data = b"x\n\r\n\r@"
    assert _reference(data, 5) == (3, 1)
    assert luadata._position(data, 5) == (3, 1)


def test_constructed_refusal_in_a_mixed_document_names_the_line() -> None:
    """End to end, at the default slice size: a refused byte after a long
    run of `\\n\\r` (one alternating run, so no cut is at a safe byte)."""
    data = b"X = 1\n" + b"\n\r" * 700_000 + b"@\n"
    with pytest.raises(luadata.LuaDataError) as caught:
        luadata.parse(data)
    assert (caught.value.line, caught.value.column) == _reference(data, len(data) - 2)
    assert (caught.value.line, caught.value.column) == (700_002, 1)


# ── a backslash before a NUL byte ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "offset"),
    [
        (b'X = "a\\\x00b"', 7),
        (b"X = 'a\\\x00b'", 7),
        (b'X = { ["\\\x00"] = 1 }', 9),
    ],
    ids=["constructed-double-quoted", "constructed-single-quoted", "constructed-key"],
)
def test_constructed_backslash_before_nul_is_refused_as_a_nul(data: bytes, offset: int) -> None:
    """A raw NUL is refused with the §4.3 NUL message at the NUL; a NUL
    after a backslash is the same byte, and gets the same refusal rather
    than "not a Lua 5.1 escape"."""
    with pytest.raises(luadata.LuaDataError) as caught:
        luadata.parse(data)
    err = caught.value
    assert err.message == "a NUL byte is rejected (§4.3)"
    assert err.token == b"\x00"
    assert err.offset == offset
    assert (err.line, err.column) == (1, offset + 1)


def test_constructed_raw_nul_in_a_string_has_the_same_message() -> None:
    """Positive control: the message the escaped NUL is now given."""
    with pytest.raises(luadata.LuaDataError) as caught:
        luadata.parse(b'X = "a\x00b"')
    assert caught.value.message == "a NUL byte is rejected (§4.3)"
    assert caught.value.token == b"\x00"
