"""`wtfconfig` against the real WTF captures (L4, L8; LAB_FORMATS §5-§7).

Every `config-wtf`, `bindings` and `macros` row in the fixture index is
round-tripped byte for byte, so a future capture is graded the day its row
lands. The named tests pin what the Forever beta capture of 2026-09-22 shows
(LAB_FORMATS amendments of that date). Facts the corpus does not contain
(duplicate CVars, multi-line macro bodies, the quoted CLICK bind form) are
graded with constructed inputs in `test_wtfconfig_constructed.py`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from wowlab_core.wtfconfig import (
    BindingsDocument,
    BindLine,
    ConfigDocument,
    MacroBodyLine,
    MacroEndLine,
    MacroHeaderLine,
    MacrosDocument,
    Unknown,
    parse_bindings,
    parse_config,
    parse_macros,
    read_config,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"
WTF = FIXTURES / "macos" / "forever" / "WTF"
ACCOUNT = WTF / "Account" / "90000001#6"
CHARACTER = ACCOUNT / "1" / "Labcharb-Labrealmd"

Parser = Callable[[bytes], ConfigDocument | BindingsDocument | MacrosDocument]
PARSERS: dict[str, Parser] = {
    "config-wtf": parse_config,
    "bindings": parse_bindings,
    "macros": parse_macros,
}


def _indexed(kind: str) -> list[str]:
    """Fixture paths whose index row has this `kind`."""
    found = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) > 1 and cells[1] == kind:
            found.append(cells[0].strip("`"))
    return found


ROWS = [(kind, name) for kind in PARSERS for name in _indexed(kind)]


@pytest.mark.parser
def test_index_has_every_wtf_text_kind() -> None:
    kinds = {kind for kind, _ in ROWS}
    assert kinds == set(PARSERS), f"no fixture row for {set(PARSERS) - kinds}"
    assert len(ROWS) >= 6, ROWS


@pytest.mark.parser
@pytest.mark.parametrize(("kind", "name"), ROWS, ids=[name for _, name in ROWS])
def test_joining_lines_reproduces_the_file(kind: str, name: str) -> None:
    data = (FIXTURES / name).read_bytes()
    doc = PARSERS[kind](data)
    assert b"".join(line.raw for line in doc.lines) == data
    assert doc.to_bytes() == data


@pytest.mark.parser
@pytest.mark.parametrize("name", _indexed("config-wtf"))
def test_every_real_config_line_is_a_set_line(name: str) -> None:
    doc = parse_config((FIXTURES / name).read_bytes())
    unknown = [line for line in doc.lines if isinstance(line, Unknown)]
    assert unknown == [], "the capture has no line outside the SET grammar"
    assert {line.ending for line in doc.lines} == {b"\n"}, "LF files (index rows)"


@pytest.mark.parser
def test_the_capture_holds_181_set_lines() -> None:
    # LAB_FORMATS amendment 2026-09-22, §5: "No embedded quote in 181 SET lines."
    total = sum(
        len(parse_config((FIXTURES / name).read_bytes()).cvars) for name in _indexed("config-wtf")
    )
    assert total == 181


@pytest.mark.parser
@pytest.mark.parametrize("name", _indexed("config-wtf"))
def test_real_configs_have_no_duplicate_cvars(name: str) -> None:
    # Corpus fact. Duplicate reporting itself is graded on constructed input.
    assert parse_config((FIXTURES / name).read_bytes()).duplicates() == ()


@pytest.mark.parser
def test_cvar_names_with_hyphens_are_typed() -> None:
    doc = read_config(WTF / "Config.wtf")
    hyphenated = [c for c in doc.cvars if "-" in c.name]
    assert len(hyphenated) == 8
    assert all(c.name.startswith("CACHE-W") for c in hyphenated)
    assert doc["CACHE-WQST-QuestV2RecordCount"] == "7760"


@pytest.mark.parser
def test_lookup_is_case_insensitive_and_keeps_the_original_case() -> None:
    doc = read_config(WTF / "Config.wtf")
    for spelling in ("agentUID", "AGENTUID", "agentuid", "AgEnTuId"):
        cvar = doc.get(spelling)
        assert cvar is not None, spelling
        assert cvar.name == "agentUID", "the name as the client wrote it"
        assert cvar.value == "wow_classic_beta"
        assert spelling in doc
    assert doc.get("cache-wqst-questv2recordcount") is not None
    assert doc.get("no such cvar") is None
    assert "no such cvar" not in doc
    with pytest.raises(KeyError):
        doc["no such cvar"]


@pytest.mark.parser
def test_values_keep_raw_control_bytes() -> None:
    account = read_config(ACCOUNT / "config-cache.wtf")
    character = read_config(CHARACTER / "config-cache.wtf")
    controlled = [
        c for doc in (account, character) for c in doc.cvars if re.search(r"[\x00-\x1f]", c.value)
    ]
    assert len(controlled) == 5, "0x02 in five config-cache lines (amendment §5)"
    assert all(c.value.startswith("\x02") for c in controlled)
    assert account["nameplateInfoDisplay"] == "\x02A"
    assert account["closedInfoFramesAccountWide"] == "\x02M`"


@pytest.mark.parser
def test_blanked_identity_cvars_are_set_lines_with_empty_values() -> None:
    config = read_config(WTF / "Config.wtf")
    for name in ("accountName", "accountList", "Sound_OutputDriverName"):
        assert config[name] == "", name
    character = read_config(CHARACTER / "config-cache.wtf")
    assert character["lastSelectedClubId"] == ""


@pytest.mark.parser
def test_real_bindings() -> None:
    data = (ACCOUNT / "bindings-cache.wtf").read_bytes()
    doc = parse_bindings(data)
    assert all(isinstance(line, BindLine) for line in doc.lines), "no header or mode line"
    assert [line.ending for line in doc.lines] == [b"\r\n"] * 3
    assert [(b.key, b.action, b.quoted) for b in doc.bindings] == [
        ("BUTTON3", "MULTIACTIONBAR1BUTTON2", False),
        ("Q", "MULTIACTIONBAR1BUTTON1", False),
        ("E", "MULTIACTIONBAR1BUTTON3", False),
    ]


@pytest.mark.parser
def test_real_character_macro() -> None:
    data = (CHARACTER / "macros-cache.txt").read_bytes()
    doc = parse_macros(data)
    assert [type(line) for line in doc.lines] == [MacroHeaderLine, MacroBodyLine, MacroEndLine]
    assert [line.ending for line in doc.lines] == [b"\r\n"] * 3, "CRLF, also after END"
    (macro,) = doc.macros
    assert macro.version == "3"
    assert macro.macro_id == "0100000000000001"
    assert macro.name == "PolyArc"
    assert macro.icon == "134400"
    assert macro.body == "/castsequence Arcane Intellect, Polymorph\r\n"
    assert macro.body_lines == ("/castsequence Arcane Intellect, Polymorph",)
    assert macro.complete


@pytest.mark.parser
def test_empty_account_macros_file_round_trips() -> None:
    data = (ACCOUNT / "macros-cache.txt").read_bytes()
    assert data == b"", "0 bytes in the capture"
    doc = parse_macros(data)
    assert doc.lines == ()
    assert doc.macros == ()
    assert doc.to_bytes() == b""
