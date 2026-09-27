# Probe from review of m10/14-wowlab-cli (afda260); reproduces: `sv dump --json` of a file luadata accepts (nesting 70 of MAX_DEPTH 200) prints JSON that SvDumpReport.model_validate_json refuses (recursion limit exceeded).
"""M10-14 acceptance: "every data command's `--json` output validates against
its Pydantic model". `sv dump --json` nests three JSON levels per Lua table
level (table -> entries list -> entry object), and Pydantic's JSON parser
stops at a fixed nesting depth. Measured in review: Lua depth 60 validates,
depth 70 does not, while `luadata.MAX_DEPTH` is 200, so a file the parser
accepts and the command prints (exit 0) produces output that does not
validate against the model the command's help names.

The input is constructed (L8, boundary case): one table nested to a depth
the parser accepts. Depth 20 is the positive control.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wowlab_core import cli, luadata


@pytest.mark.parametrize("depth", [20, 70, luadata.MAX_DEPTH])
def test_sv_dump_json_validates_at_accepted_depths_constructed(tmp_path: Path, depth: int) -> None:
    f = tmp_path / "Deep.lua"
    f.write_bytes(b"Deep = " + b"{" * depth + b"1" + b"}" * depth + b"\n")
    luadata.read(f)  # the parser accepts this file
    result = CliRunner().invoke(cli.app, ["sv", "dump", str(f), "--json"])
    assert result.exit_code == 0, (result.stderr, repr(result.exception))
    cli.SvDumpReport.model_validate_json(result.stdout)
