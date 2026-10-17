# Probe from review of m13/06-macros-check; reproduces `macros check` asserting
# definite findings from a schema-3 record whose own probes say values were dropped.
"""`macrocheck` ignores the record's `probes` (code-reviewer probe, M13-06).

The lab-addon lists an action slot only when `GetActionInfo` answered for it,
and keeps a spellbook item's `spell_name` only when the value was not secret
and the call did not raise (`lab/addon/README.md`, "actionbars", "spellbook";
docs/LAB_PLAN.md §15.2: each value is tested with `issecretvalue` and a
secret one is dropped and counted). So a record whose `GetActionInfo` probe
counts errors or secret values may be missing macro slots, and one whose
`GetSpellBookItemName` probe counts secret values may be missing names.

From such a record the check still reports every macro as `not-on-action-bar`
and every unmatched name as `spell-not-in-spellbook`, as findings, rather than
"cannot judge". Expected: the rule that depends on a dropped value is
"cannot judge" (or the finding says the record is incomplete).

Inputs: the real schema-3 record (`fixtures/macos-70425/`) with byte-level
edits to its probe counts (constructed: a boundary case of the record), and
made-up macros. The positive controls run the same macros against the same
record with the probe counts left as captured.
"""

from __future__ import annotations

from pathlib import Path

from wowlab_core import labaddon, macrocheck, wtfconfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SCHEMA_3 = (
    FIXTURES
    / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)

_BARS_PROBE = b'["api"] = "GetActionInfo",\r\n["calls"] = 180,\r\n["errors"] = 0,'
_BARS_ERRORS = b'["api"] = "GetActionInfo",\r\n["calls"] = 180,\r\n["errors"] = 20,'
_NAME_PROBE = (
    b'["answered"] = 28,\r\n["present"] = true,\r\n'
    b'["api"] = "C_SpellBook.GetSpellBookItemName",\r\n'
    b'["calls"] = 28,\r\n["errors"] = 0,\r\n["secret"] = 0,'
)
_NAME_SECRET = (
    b'["answered"] = 20,\r\n["present"] = true,\r\n'
    b'["api"] = "C_SpellBook.GetSpellBookItemName",\r\n'
    b'["calls"] = 28,\r\n["errors"] = 0,\r\n["secret"] = 8,'
)


def _macros() -> list[macrocheck.SourceMacro]:
    data = b'VER 3 0100000000000001 "Mac A" "134400"\r\n/cast Fireball\r\nEND\r\n'
    doc = wtfconfig.parse_macros(data)
    return [macrocheck.SourceMacro(scope="character", file="x", macro=m) for m in doc.macros]


def _record(data: bytes) -> labaddon.CharDB:
    return labaddon.parse_char(data)


def _no_macro_slots(data: bytes) -> bytes:
    assert data.count(b'["type"] = "macro",') == 2
    return data.replace(b'["type"] = "macro",', b'["type"] = "item",')


def test_positive_control_complete_record_gives_findings() -> None:
    data = _no_macro_slots(SCHEMA_3.read_bytes())
    result = macrocheck.check(_macros(), _record(data))
    rules = [f.rule for f in result.findings]
    assert "not-on-action-bar" in rules
    assert "spell-not-in-spellbook" in rules


def test_bars_finding_despite_getactioninfo_errors_constructed() -> None:
    data = _no_macro_slots(SCHEMA_3.read_bytes())
    assert data.count(_BARS_PROBE) == 1
    data = data.replace(_BARS_PROBE, _BARS_ERRORS)
    record = _record(data)
    assert isinstance(record, labaddon.CharDBV3)
    assert isinstance(record.actionbars, labaddon.ActionBars)
    [probe] = [p for p in record.actionbars.probes if p.api == "GetActionInfo"]
    assert probe.errors == 20  # 20 slots raised: none of them is listed
    result = macrocheck.check(_macros(), record)
    assert not [f for f in result.findings if f.rule == "not-on-action-bar"], (
        "a macro is reported on no action bar although 20 slots could not be read"
    )


def test_spell_finding_despite_secret_names_constructed() -> None:
    data = SCHEMA_3.read_bytes()
    assert data.count(_NAME_PROBE) == 1
    data = data.replace(_NAME_PROBE, _NAME_SECRET)
    record = _record(data)
    assert isinstance(record, labaddon.CharDBV3)
    assert isinstance(record.spellbook, labaddon.Spellbook)
    result = macrocheck.check(_macros(), record)
    assert not [f for f in result.findings if f.rule == "spell-not-in-spellbook"], (
        "a spell is reported not in the spellbook although 8 names were dropped as secret"
    )
