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


def _dump(*args: str) -> str:
    result = runner.invoke(cli.app, ["sv", "dump", "--json", *args])
    assert result.exit_code == 0, (result.stderr, repr(result.exception))
    return result.stdout


def test_there_are_fixtures_to_compare() -> None:
    assert len(SV_FILES) >= 10


@pytest.mark.parametrize("sv", SV_FILES, ids=lambda p: str(p.relative_to(FIXTURES)))
def test_dump_json_is_byte_identical_to_the_iterencode_output(sv: Path) -> None:
    doc = luadata.read(sv)
    tops = [("variable", a.name, a.value) for a in doc.assignments]
    out = _dump(str(sv))
    assert out == _before(str(sv), None, tops)
    cli.SvDumpReport.model_validate_json(out)


def test_dump_json_with_path_is_byte_identical_to_the_iterencode_output() -> None:
    sv = next(p for p in SV_FILES if p.name == "Syndicator.lua")
    doc = luadata.read(sv)
    name = doc.assignments[0].name
    value = cli._resolve_path(doc, name)
    out = _dump(str(sv), "--path", name)
    assert out == _before(str(sv), name, [("path", name, value)])


def test_dump_json_at_max_depth_constructed(tmp_path: Path) -> None:
    depth = luadata.MAX_DEPTH
    f = tmp_path / "Deep.lua"
    f.write_bytes(b"Deep = " + b"{" * depth + b'"\\255\\0x"' + b"}" * depth + b"\n")
    doc = luadata.read(f)
    out = _dump(str(f))
    assert out == _before(str(f), None, [("variable", "Deep", doc.assignments[0].value)])
    assert len(json.loads(out)["values"]) == depth + 1
