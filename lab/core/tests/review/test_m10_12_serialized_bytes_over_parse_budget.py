# Probe from review of m10/12-luadata-serializer; reproduces serialize returning
# bytes that parse refuses as over the MAX_COST budget.
"""`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27, item 2: `serialize`'s
"output always parses as data". `serialize` mirrors the parser's other
bounds (depth, string length, number length, file size) but not the
`MAX_COST` parse budget, so a document grown past the budget serializes to
bytes `parse` refuses with `LuaLimitError`.

At the real budget (reviewer's run on 3259dc7, M1): 7,000,000 shared `0,`
entries serialize to 28,000,012 bytes (under `MAX_FILE_BYTES`) in 5.0 s,
and `parse` refuses them at line 6,599,998, "document over the parse
budget". That takes about 13 s, so this probe lowers `MAX_COST`, which the
parser reads at call time, and appends entries to a real fixture the way
item 1 prescribes (the table's `close_lead` set to `None`). The property
holds for any budget: either `serialize` refuses, or `parse` accepts what it
wrote.

Positive control: under the same lowered budget the unedited fixture
serializes and parses (its own cost is about 231,361).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import luadata
from wowlab_core.luadata import (
    Entry,
    KeyStyle,
    LuaDataError,
    LuaNumber,
    LuaTable,
    parse,
    serialize,
)

pytestmark = pytest.mark.parser

SYNDICATOR = (
    Path(__file__).resolve().parents[1]
    / "fixtures/macos/forever/WTF/Account/90000001#6/SavedVariables/Syndicator.lua"
)
LOWERED_BUDGET = 300_000


def _grown(extra: int) -> luadata.LuaDocument:
    document = parse(SYNDICATOR.read_bytes())
    first, *rest = document.assignments
    table = first.value
    assert isinstance(table, LuaTable)
    new = Entry(
        None, KeyStyle.POSITIONAL, None, None, None, LuaNumber(None, "0"), None, None, None, False
    )
    grown = table._replace(entries=(*table.entries, *((new,) * extra)), close_lead=None)
    return document._replace(assignments=(first._replace(value=grown), *rest))


def test_positive_control_unedited_fixture_fits_the_lowered_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(luadata, "MAX_COST", LOWERED_BUDGET)
    data = SYNDICATOR.read_bytes()
    assert serialize(parse(data)) == data


def test_serialize_output_always_parses_under_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    document = _grown(2000)
    monkeypatch.setattr(luadata, "MAX_COST", LOWERED_BUDGET)
    try:
        out = serialize(document)
    except LuaDataError:
        return  # refusing is allowed; returning bytes parse refuses is not
    parse(out)  # raises LuaLimitError on 3259dc7: over the parse budget
