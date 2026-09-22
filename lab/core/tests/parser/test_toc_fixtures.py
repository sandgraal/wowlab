"""`toc` against the real TOC captures (L4, L8; LAB_FORMATS §3).

Every `toc` row in the fixture index is round-tripped byte for byte and has
its directive order checked against the file's own lines, so a future
capture is graded the day its row lands. The named tests pin what the
DBM-Challenges TOC of the Forever beta capture of 2026-09-22 shows
(LAB_FORMATS §3 amendment of that date). Facts no real TOC carries yet (a
BOM, a leading condition, path variables, an unparseable `## Interface:`,
an unknown directive) are graded with constructed inputs in
`test_toc_constructed.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wowlab_core.toc import (
    Blank,
    Directive,
    FileLine,
    TocDocument,
    parse_toc,
    read_toc,
    serialize_toc,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"
DBM = FIXTURES / "macos/forever/Interface/AddOns/DBM-Challenges/DBM-Challenges.toc"


def _indexed(kind: str) -> list[str]:
    found = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) > 1 and cells[1] == kind:
            found.append(cells[0].strip("`"))
    return found


TOCS = _indexed("toc")


@pytest.mark.parser
def test_index_has_a_toc_row() -> None:
    assert DBM.relative_to(FIXTURES).as_posix() in TOCS


@pytest.mark.parser
@pytest.mark.parametrize("name", TOCS)
def test_real_toc_round_trips_byte_for_byte(name: str) -> None:
    data = (FIXTURES / name).read_bytes()
    doc = parse_toc(data)
    assert serialize_toc(doc) == data
    assert doc.to_bytes() == data
    assert read_toc(FIXTURES / name) == doc


@pytest.mark.parser
@pytest.mark.parametrize("name", TOCS)
def test_real_toc_keeps_every_directive_in_file_order(name: str) -> None:
    """The directives are exactly the file's `## key:` lines, in order, each
    keyed as written; nothing is dropped, merged or reordered, and no
    directive is lost for not being on the known list."""
    data = (FIXTURES / name).read_bytes()
    doc = parse_toc(data)
    expected = [
        line.split(b":", 1)[0].removeprefix(b"##").strip().decode("utf-8")
        for line in data.removeprefix(b"\xef\xbb\xbf").splitlines()
        if re.match(rb"##[ \t]*[^\s:]+[ \t]*:", line)
    ]
    assert [d.key for d in doc.directives] == expected
    assert len(doc.lines) == data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


@pytest.mark.parser
@pytest.mark.parametrize("name", TOCS)
def test_real_toc_survives_json(name: str) -> None:
    doc = parse_toc((FIXTURES / name).read_bytes())
    assert TocDocument.model_validate_json(doc.model_dump_json()) == doc


# ─── the DBM-Challenges TOC (Forever beta, macOS, 2026-09-22) ────────────────


@pytest.fixture(scope="module")
def dbm() -> TocDocument:
    return parse_toc(DBM.read_bytes())


@pytest.mark.parser
def test_dbm_line_endings_bom_and_counts(dbm: TocDocument) -> None:
    assert dbm.bom is False
    assert {line.ending for line in dbm.lines} == {"\r\n"}, "CRLF throughout, final CRLF"
    assert len(dbm.directives) == 38
    assert len(dbm.files) == 67 - 38 - 1
    assert [type(line) for line in dbm.lines].count(Blank) == 1
    assert dbm.comments == ()
    assert dbm.decode_errors is False


@pytest.mark.parser
def test_dbm_interface_is_a_list_of_ints(dbm: TocDocument) -> None:
    interface = dbm.interface
    assert interface is not None
    assert interface.versions == (50504, 120100)
    assert interface.unparsed == ()
    assert interface.text == "50504, 120100"


@pytest.mark.parser
def test_dbm_directives_as_found(dbm: TocDocument) -> None:
    assert dbm.get("allowloadgametype") == "mists, standard", "keys fold ASCII case"
    assert dbm.list_value("AllowLoadGameType") == ("mists", "standard")
    title = dbm.get_all("Title")[0]
    assert title.raw.startswith(b"## Title:|cff"), "no space after the colon"
    assert title.value.startswith("|cffffe00a<|r")
    assert dbm.saved_variables == ("DBMChallenges_AllSavedVars",)
    assert dbm.saved_variables_per_character == ("DBMChallenges_SavedStats",)
    assert dbm.get("LoadOnDemand") == "1"
    assert dbm.get("RequiredDeps") == "DBM-Core"
    assert dbm.get("IconTexture") == r"Interface\AddOns\DBM-Core\textures\dbm_airhorn"
    # X- directives are kept verbatim, including the long comma list.
    assert dbm.list_value("X-DBM-Mod-MapID")[:3] == ("1148", "1698", "1710")
    assert dbm.get("X-Wago-ID") == "Rn6VLLGd"


@pytest.mark.parser
def test_dbm_localized_keys_keep_raw_utf8(dbm: TocDocument) -> None:
    localized = [d for d in dbm.directives if d.locale is not None]
    assert {d.key for d in localized} >= {
        "Title-deDE",
        "Title-koKR",
        "Title-ruRU",
        "X-DBM-Mod-Name-zhCN",
    }
    ko = dbm.get_all("Title-koKR")[0]
    assert ko.base_key == "Title"
    assert ko.locale == "koKR"
    assert any(b >= 0x80 for b in ko.raw), "raw UTF-8 bytes kept"
    assert ko.value.encode("utf-8") in ko.raw
    assert all(d.known for d in dbm.directives), "every directive is on the §3 list"


@pytest.mark.parser
def test_dbm_trailing_load_conditions_kept_as_text(dbm: TocDocument) -> None:
    conditional = [f for f in dbm.files if f.conditions]
    assert len(conditional) == 20
    for f in conditional:
        assert [(c.text, c.position) for c in f.conditions] == [
            ("AllowLoadGameType standard", "after")
        ]
        assert not f.path.endswith(" "), "path text separated from the condition"
        assert "\\" in f.path, "backslash separators kept as written"
    first = conditional[0]
    assert isinstance(first, FileLine)
    assert first.path == r"Shadowlands\Torghast.lua"
    assert first.raw == rb"Shadowlands\Torghast.lua [AllowLoadGameType standard]"
    plain = [f.path for f in dbm.files if not f.conditions]
    assert plain[:2] == ["localization.en.lua", "localization.de.lua"]
    assert all(f.variables == () for f in dbm.files)


@pytest.mark.parser
def test_dbm_order_of_line_kinds(dbm: TocDocument) -> None:
    kinds = [line.kind for line in dbm.lines]
    assert kinds[:38] == ["directive"] * 38
    assert kinds[38] == "blank"
    assert set(kinds[39:]) == {"file"}
    assert isinstance(dbm.lines[0], Directive)
    assert dbm.lines[0].key == "Interface"
