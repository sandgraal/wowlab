"""`sv dump --json` encodes each row with one `encode` call (M16-24).

`JSONEncoder.encode` takes the C encoder; `iterencode` takes the pure-Python
one. The change must not move a byte: every real SavedVariables fixture is
dumped through the CLI and compared with the output the `iterencode` path
printed before the change, rebuilt here from the same rows. One constructed
input (labelled) checks that a table nested to `luadata.MAX_DEPTH` still
dumps: the rows are flat, so the C encoder's recursion stays shallow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wowlab_core import cli, luadata

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SV_FILES = sorted(p for p in FIXTURES.rglob("*.lua*") if "SavedVariables" in p.parts)

# M18-01T: the captures holding a positional `nil` hole (docs/LAB_FORMATS.md
# §4.1, amendment of 2026-11-14) do not read until M18-01, which deletes the
# one marker line below (`_PENDING.update(...)`) and nothing else here.
NIL_HOLE_FIXTURES = (
    "macos-70938/forever/WTF/Account/LABACCOUNTA/1/Sandgraal-Rizzen/SavedVariables/"
    "Blizzard_AuctionHouseUI.lua",
    "macos-70938/forever/WTF/Account/LABACCOUNTA/SavedVariables/Blizzard_CombatLog.lua",
)
_M18_01 = pytest.mark.xfail(strict=True, reason="M18-01 not implemented")
_PENDING: dict[str, pytest.MarkDecorator] = {}
_PENDING.update(dict.fromkeys(NIL_HOLE_FIXTURES, _M18_01))
SV_CASES = [
    pytest.param(
        p,
        id=p.relative_to(FIXTURES).as_posix(),
        marks=_PENDING.get(p.relative_to(FIXTURES).as_posix(), ()),
    )
    for p in SV_FILES
]

runner = CliRunner()


def _before(file: str, path: str | None, doc_rows: list[tuple[str, str, luadata.LuaValue]]) -> str:
    """What `_stream_dump` printed before M16-24: each row through `iterencode`."""
    encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"))
    head = {"file": cli._json_text(file), "path": None if path is None else cli._json_text(path)}
    parts = [encoder.encode(head)[:-1] + ',"values":[']
    for n, row in enumerate(cli._rows(doc_rows)):
        if n:
            parts.append(",")
        parts.extend(encoder.iterencode(cli._map_strings(row, cli._json_text)))
    parts.append("]}\n")
    return "".join(parts)


def _same(out: str, expected: str) -> None:
    """`out == expected`, failing with the first differing offset and a short
    window: pytest's own diff of a one-line megabyte string takes minutes."""
    if out == expected:
        return
    i = next((n for n, (a, b) in enumerate(zip(out, expected, strict=False)) if a != b), None)
    if i is None:
        i = min(len(out), len(expected))
    lo = max(0, i - 40)
    pytest.fail(
        f"first difference at {i} (lengths {len(out)} vs {len(expected)}): "
        f"{out[lo : i + 40]!r} vs {expected[lo : i + 40]!r}"
    )


def _dump(*args: str) -> str:
    result = runner.invoke(cli.app, ["sv", "dump", "--json", *args])
    assert result.exit_code == 0, (result.stderr, repr(result.exception))
    return result.stdout


def test_there_are_fixtures_to_compare() -> None:
    assert len(SV_FILES) >= 10


@pytest.mark.parametrize("sv", SV_CASES)
def test_dump_json_is_byte_identical_to_the_iterencode_output(sv: Path) -> None:
    doc = luadata.read(sv)
    tops = [("variable", a.name, a.value) for a in doc.assignments]
    out = _dump(str(sv))
    _same(out, _before(str(sv), None, tops))
    cli.SvDumpReport.model_validate_json(out)


def test_dump_json_with_path_is_byte_identical_to_the_iterencode_output() -> None:
    sv = next(p for p in SV_FILES if p.name == "Syndicator.lua")
    doc = luadata.read(sv)
    name = doc.assignments[0].name
    value = cli._resolve_path(doc, name)
    out = _dump(str(sv), "--path", name)
    _same(out, _before(str(sv), name, [("path", name, value)]))


def test_dump_json_at_max_depth_constructed(tmp_path: Path) -> None:
    depth = luadata.MAX_DEPTH
    f = tmp_path / "Deep.lua"
    f.write_bytes(b"Deep = " + b"{" * depth + b'"\\255\\0x"' + b"}" * depth + b"\n")
    doc = luadata.read(f)
    out = _dump(str(f))
    _same(out, _before(str(f), None, [("variable", "Deep", doc.assignments[0].value)]))
    assert len(json.loads(out)["values"]) == depth + 1


# Constructed (L8, hostile/boundary): the real SavedVariables fixtures are pure
# ASCII and carry no lone surrogate, so on their own they cannot tell
# `ensure_ascii=True` from False, nor a row encoded without `_json_text`. Raw
# UTF-8 (é and an astral U+1F600), bytes that are not UTF-8 (raw and as `\ddd`),
# a NUL escape, and a key of one non-UTF-8 byte that `--path` names with a lone
# surrogate (U+DCFF, what `surrogateescape` gives for 0xFF).
HOSTILE = (
    b"Hostile = {\n"
    b'\t["\\255"] = {\n'
    b'\t\t"caf\xc3\xa9 \xf0\x9f\x98\x80",\n'
    b'\t\t"caf\\195\\169 \\240\\159\\152\\128",\n'
    b'\t\t"raw \xff\xfe and \\233",\n'
    b'\t\t"nul \\0 here",\n'
    b"\t\t[\"k\xc3\xa9\"] = 'v\xf0\x9f\x98\x80',\n"
    b"\t},\n"
    b"}\n"
)
HOSTILE_PATH = 'Hostile["\udcff"]'


def _hostile(tmp_path: Path) -> Path:
    f = tmp_path / "Hostile.lua"
    f.write_bytes(HOSTILE)
    return f


def test_dump_json_hostile_text_constructed(tmp_path: Path) -> None:
    f = _hostile(tmp_path)
    doc = luadata.read(f)
    out = _dump(str(f))
    _same(out, _before(str(f), None, [("variable", "Hostile", doc.assignments[0].value)]))
    assert out.isascii()
    cli.SvDumpReport.model_validate_json(out)


def test_dump_json_hostile_path_with_a_lone_surrogate_constructed(tmp_path: Path) -> None:
    f = _hostile(tmp_path)
    doc = luadata.read(f)
    value = cli._resolve_path(doc, HOSTILE_PATH)
    out = _dump(str(f), "--path", HOSTILE_PATH)
    _same(out, _before(str(f), HOSTILE_PATH, [("path", HOSTILE_PATH, value)]))
    assert '"name":"Hostile[\\"\\u0000DCFF\\"]"' in out
    cli.SvDumpReport.model_validate_json(out)
