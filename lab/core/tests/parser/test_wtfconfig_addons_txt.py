"""`wtfconfig.parse_addons`: a character's `AddOns.txt` (M16-34; L4, L8).

The real file is the `addons-txt` row of the fixture index (part 2 of the
2026-09-22 capture, 1.60.1.69977): two `<addon>: enabled` lines, CRLF with a
final CRLF, no BOM (LAB_FORMATS, amendment of 2026-09-22). Every indexed
`addons-txt` row is round-tripped byte for byte. What the corpus does not
hold (a `disabled` line, an unknown word, a line of no known shape, LF, no
final break, a name repeated, a file over the read limit) is graded on
constructed inputs, whose ids say `constructed`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import snapshot
from wowlab_core.wtfconfig import (
    AddonsDocument,
    AddonStateLine,
    Unknown,
    parse_addons,
    read_addons,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"
REAL = (
    FIXTURES
    / "macos"
    / "forever"
    / "WTF"
    / "Account"
    / "90000001#6"
    / "Labrealmb Partb Partc Partd"
    / "Labchard"
    / "AddOns.txt"
)


def _indexed(kind: str) -> list[str]:
    found = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) > 1 and cells[1] == kind:
            found.append(cells[0].strip("`"))
    return found


ROWS = _indexed("addons-txt")


@pytest.mark.parser
def test_the_real_file_is_indexed() -> None:
    assert REAL.relative_to(FIXTURES).as_posix() in ROWS


@pytest.mark.parser
@pytest.mark.parametrize("name", ROWS, ids=ROWS)
def test_every_indexed_addons_txt_round_trips(name: str) -> None:
    data = (FIXTURES / name).read_bytes()
    doc = parse_addons(data)
    assert doc.to_bytes() == data
    assert b"".join(line.raw for line in doc.lines) == data


@pytest.mark.parser
def test_the_real_file_lists_two_enabled_blizzard_modules() -> None:
    doc = read_addons(REAL)
    assert [(s.name, s.state, s.enabled) for s in doc.states] == [
        ("Blizzard_ObjectiveTracker", "enabled", True),
        ("Blizzard_CompactRaidFrames", "enabled", True),
    ]
    assert doc.unknown_lines() == ()
    assert {line.ending for line in doc.lines} == {b"\r\n"}
    assert all(isinstance(line, AddonStateLine) for line in doc.lines)


@pytest.mark.parser
def test_lookup_folds_ascii_case_and_a_missing_name_is_none() -> None:
    doc = read_addons(REAL)
    found = doc.state_of("blizzard_objectivetracker")
    assert found is not None and found.name == "Blizzard_ObjectiveTracker"
    assert doc.state_of("DBM-Brawlers") is None, "not listed is not disabled"


CASES = {
    "empty": b"",
    "lf": b"A: enabled\nB: disabled\n",
    "no-final-break": b"A: enabled\r\nB: enabled",
    "unknown-word": b"A: maybe\r\n",
    "no-shape": b"# a comment\r\nA enabled\r\n: enabled\r\nA: \r\nA:enabled\r\n",
    "lone-cr": b"A: enabled\rB: enabled\n",
    "invalid-utf8": b"\xff\xfe: enabled\n",
    "repeated": b"A: enabled\na: disabled\n",
    "spaces-in-name": b"My Addon: enabled\n",
}


@pytest.mark.parser
@pytest.mark.parametrize("data", CASES.values(), ids=[f"constructed-{k}" for k in CASES])
def test_round_trip_constructed(data: bytes) -> None:
    doc = parse_addons(data)
    assert doc.to_bytes() == data
    assert AddonsDocument(lines=doc.lines) == doc


@pytest.mark.parser
def test_states_and_words_constructed() -> None:
    doc = parse_addons(CASES["lf"] + CASES["unknown-word"])
    assert [(s.name, s.enabled) for s in doc.states] == [
        ("A", True),
        ("B", False),
        ("A", None),
    ]
    assert doc.state_of("a") == doc.states[-1], "the last line naming it wins"


@pytest.mark.parser
def test_lines_of_no_known_shape_are_kept_constructed() -> None:
    doc = parse_addons(CASES["no-shape"])
    assert doc.states == ()
    assert len(doc.unknown_lines()) == 5
    assert all(isinstance(line, Unknown) for line in doc.lines)


@pytest.mark.parser
def test_a_lone_cr_is_not_a_break_constructed() -> None:
    doc = parse_addons(CASES["lone-cr"])
    assert len(doc.lines) == 1 and doc.states == ()


@pytest.mark.parser
def test_a_typed_line_cannot_hide_as_unknown_constructed() -> None:
    with pytest.raises(ValueError):
        AddonsDocument(lines=(Unknown(text=b"A: enabled", ending=b"\n"),))


@pytest.mark.parser
def test_a_file_over_the_limit_is_refused_constructed(tmp_path: Path) -> None:
    path = tmp_path / "AddOns.txt"
    path.write_bytes(b"A: enabled\n" * 4)
    with pytest.raises(snapshot.UnsafeReadError):
        read_addons(path, limit=8)
    assert read_addons(path).to_bytes() == path.read_bytes()
