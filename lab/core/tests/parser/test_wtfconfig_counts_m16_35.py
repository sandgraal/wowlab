"""`wtfconfig.count_cvar_names` and `count_macro_records` (M16-35).

`char list` counts what a character folder without a WowLab.lua holds in
one pass over the bytes, building no document (the security review of
M16-35). The counts must equal what the full parse gives, so they cannot
drift from `cvar list` and `macros list`: graded on every `config-wtf` and
`macros` row of the fixture index (real captures, L8), and on constructed
boundary inputs (labelled `constructed`) for what the corpus lacks:
duplicate names that differ in case, a record cut short, `END` and `VER`
lines inside a body, CRLF endings.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_wtfconfig_fixtures import FIXTURES, _indexed

from wowlab_core.wtfconfig import (
    count_cvar_names,
    count_macro_records,
    parse_config,
    parse_macros,
)

CONFIGS = _indexed("config-wtf")
MACROS = _indexed("macros")


@pytest.mark.parser
def test_the_index_has_files_of_both_kinds() -> None:
    assert CONFIGS and MACROS


@pytest.mark.parser
@pytest.mark.parametrize("name", CONFIGS)
def test_cvar_count_equals_the_full_parse(name: str) -> None:
    data = (FIXTURES / name).read_bytes()
    assert count_cvar_names(data) == len(parse_config(data).effective())


@pytest.mark.parser
@pytest.mark.parametrize("name", MACROS)
def test_macro_count_equals_the_full_parse(name: str) -> None:
    data = (FIXTURES / name).read_bytes()
    assert count_macro_records(data) == len(parse_macros(data).macros)


@pytest.mark.parser
def test_the_capture_character_counts() -> None:
    folder = Path(FIXTURES, "macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd")
    assert count_cvar_names((folder / "config-cache.wtf").read_bytes()) == 21
    assert count_macro_records((folder / "macros-cache.txt").read_bytes()) == 1


CONSTRUCTED_CONFIGS = [
    b"",
    b"\n\n\r\n",
    b'SET a "1"\nSET A "2"\r\nSET b "3"',
    b'SET a "1" \nSET  a "1"\nset a "1"\nSET a 1\nSET \xff "1"\n',
]
CONSTRUCTED_MACROS = [
    b"",
    b'VER 3 0100000000000001 "A" "1"\n/cast x\nEND\n',
    b'VER 3 0100000000000001 "A" "1"\n/cast x\n',
    b'VER 3 01 "A" "1"\r\nVER 3 02 "B" "2"\r\nEND\r\nVER 3 03 "C" "3"\r\nEND\r\nEND\r\n',
    b'END\nVER x\nVER 3 01 "A" "1"\nEND',
]


@pytest.mark.parametrize(
    "data", CONSTRUCTED_CONFIGS, ids=[f"constructed-{i}" for i in range(len(CONSTRUCTED_CONFIGS))]
)
def test_constructed_cvar_count_equals_the_full_parse(data: bytes) -> None:
    assert count_cvar_names(data) == len(parse_config(data).effective())


@pytest.mark.parametrize(
    "data", CONSTRUCTED_MACROS, ids=[f"constructed-{i}" for i in range(len(CONSTRUCTED_MACROS))]
)
def test_constructed_macro_count_equals_the_full_parse(data: bytes) -> None:
    assert count_macro_records(data) == len(parse_macros(data).macros)
