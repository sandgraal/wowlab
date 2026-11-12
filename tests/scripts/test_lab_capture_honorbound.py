"""`scripts/lab_capture.py`: the HonorBound `#HB3` hash in `macros-cache.txt` (M16-125).

The HonorBound addon writes a `#HB3 l=…;s=…;p=…;u=…;n=…;h=<hex>` line into a
macro body. How `h=` is made is unknown and it may be derived from the
character's GUID, so the scrub replaces its value with `0`s of the same
length, byte-level, and touches nothing else.

CONSTRUCTED INPUT (L8: hostile-input and boundary tests). Every macro file
below is written for the test in the shape of docs/LAB_FORMATS.md section 7:
invented macro ids, names, numbers and hashes, none taken from a capture.
No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import lab_capture

Identity = lab_capture.Identity

HASH = b"a1b2c3d4e5f60718"
ZEROS = b"0" * len(HASH)


def _macros(hb_line: bytes, eol: bytes = b"\r\n") -> bytes:
    lines = [
        b'VER 3 0000000000000001 "pvp" "134400"',
        b"#showtooltip",
        hb_line,
        b"/cast Frost Nova",
        b"END",
    ]
    return eol.join(lines) + eol


def _process(data: bytes, tmp_path: Path, name: str = "macros-cache.txt") -> object:
    folder = tmp_path / "in"
    folder.mkdir(exist_ok=True)
    source = folder / name
    source.write_bytes(data)
    item = lab_capture.Item(source, PurePosixPath(name), "", "", lab_capture.kind_of(name))
    return lab_capture.process(item, Identity(characters=["Orlavin"], realms=["Kestrel"]))


def test_constructed_hash_becomes_zeros_and_nothing_else_changes(tmp_path: Path) -> None:
    original = _macros(b"#HB3 l=1;s=12;p=345;u=67;n=89;h=" + HASH)
    outcome = _process(original, tmp_path)
    assert list(outcome.problems) == []  # type: ignore[attr-defined]
    data = outcome.result.data  # type: ignore[attr-defined]
    assert data == original.replace(HASH, ZEROS)
    assert len(data) == len(original)
    assert HASH not in data
    result = outcome.result  # type: ignore[attr-defined]
    assert result.count(lab_capture.HONORBOUND_REASON) == 1
    (edit,) = result.edits
    assert (edit.old, edit.new) == (HASH, ZEROS)
    assert original[edit.offset - 2 : edit.offset] == b"h="


@pytest.mark.parametrize(
    ("line", "eol"),
    [
        (b"#HB3 h=" + HASH + b";l=1", b"\r\n"),  # first field
        (b"#HB3 l=1;h=" + HASH, b"\n"),  # last field, LF
        (b"#HB3 l=1;h=" + HASH + b";n=2", b"\r\n"),  # middle field
    ],
    ids=["constructed-first", "constructed-last-lf", "constructed-middle"],
)
def test_constructed_hash_replaced_wherever_it_is_a_field(
    tmp_path: Path, line: bytes, eol: bytes
) -> None:
    original = _macros(line, eol)
    outcome = _process(original, tmp_path)
    assert outcome.result.data == original.replace(HASH, ZEROS)  # type: ignore[attr-defined]


def test_constructed_two_macros_each_counted(tmp_path: Path) -> None:
    other = b"0f1e2d3c4b5a6978"
    original = _macros(b"#HB3 l=1;h=" + HASH) + _macros(b"#HB3 l=2;h=" + other)
    outcome = _process(original, tmp_path)
    data = outcome.result.data  # type: ignore[attr-defined]
    assert HASH not in data and other not in data
    assert outcome.result.count(lab_capture.HONORBOUND_REASON) == 2  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "line",
    [
        b"/say h=" + HASH,  # not an #HB3 line
        b"/say #HB3 l=1;h=" + HASH,  # #HB3 not at the line start
        b"#HB l=1;h=" + HASH,  # no version number
        b"#HB3x l=1;h=" + HASH,  # the tag runs on
    ],
    # M16-128 moved the indented and `#HB2` cases out of this list: they are
    # HonorBound lines now (test_lab_capture_own_guids.py).
    ids=[
        "constructed-say",
        "constructed-mid-line",
        "constructed-no-version",
        "constructed-tag-run-on",
    ],
)
def test_constructed_lookalikes_are_untouched(tmp_path: Path, line: bytes) -> None:
    original = _macros(line)
    outcome = _process(original, tmp_path)
    assert outcome.result.data == original  # type: ignore[attr-defined]


def test_constructed_hb3_outside_macros_is_untouched(tmp_path: Path) -> None:
    original = b"bind CTRL-1 ACTIONBUTTON1\r\n#HB3 l=1;h=" + HASH + b"\r\n"
    outcome = _process(original, tmp_path, name="bindings-cache.wtf")
    assert HASH in outcome.result.data  # type: ignore[attr-defined]


def test_constructed_zero_hash_needs_no_edit(tmp_path: Path) -> None:
    original = _macros(b"#HB3 l=1;h=" + ZEROS)
    outcome = _process(original, tmp_path)
    assert outcome.result.data == original  # type: ignore[attr-defined]
    assert outcome.result.count(lab_capture.HONORBOUND_REASON) == 0  # type: ignore[attr-defined]


def test_constructed_provenance_row_names_the_replacement(tmp_path: Path) -> None:
    original = _macros(b"#HB3 l=1;h=" + HASH)
    outcome = _process(original, tmp_path)
    row = lab_capture.provenance_row(outcome, "macos", "owner", "owner")  # type: ignore[arg-type]
    assert "honorbound-hash-replaced: 1" in row
    assert HASH.decode() not in row
