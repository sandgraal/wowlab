# Probe from review of m11/04-labaddon-reader; reproduces a schema-1 WowLab.lua refused whole when talents.class.export is "" (the addon on main writes that when GenerateImportString returns an empty string)
"""The addon on main (`lab/addon/WowLab/Talents.lua`) writes
`record.export = ns.String(ns.Call(export, configID))`. `ns.String("")` is
`""`, so a client whose `C_Traits.GenerateImportString` returns an empty
string produces `["export"] = ""` in a schema-1 file. M11-22 is being revised
to write `export_absent = "C_Traits.GenerateImportString returned an empty
string"` in that case, which is evidence the empty return is expected; files
written before that change still hold `""`.

The M11-04 models require the export to match `^[A-Za-z0-9+/=]{1,4096}$`, so
the whole document is refused and `wowlab char show` prints nothing for the
character. The ticket asks for "a string value accepted only in the fields
the addon writes as strings"; `export` is one of them and `""` is a value the
addon writes. Refusing it makes one unremarkable client return hide every
other section.

Constructed (L8, boundary): the real first-character capture (M11-03, index
row in `fixtures/README.md`) with its one export string replaced by `""` at
the byte level. Positive control: the same replacement with another
base64-shaped string reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import labaddon

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIRST = "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
REAL = b'["export"] = "CIdBa0K/6fjlwoPQnhZ43PxtUAAAAAAAA4EA",'


def _with_export(literal: bytes) -> bytes:
    data = (FIXTURES / FIRST).read_bytes()
    assert data.count(REAL) == 1
    return data.replace(REAL, b'["export"] = ' + literal + b",")


def test_positive_control_another_export_string_reads() -> None:
    char = labaddon.parse_char(_with_export(b'"AAAAB"'))
    assert char.talents is not None
    assert isinstance(char.talents.class_, labaddon.ClassTalents)


def test_constructed_empty_export_string_does_not_refuse_the_file() -> None:
    char = labaddon.parse_char(_with_export(b'""'))
    assert char.gear is not None  # every other section is still read
    lines = labaddon.describe(char)
    assert any("export string" in line for line in lines)
