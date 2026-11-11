"""`cvar get` and friends on a BOM-prefixed or over-budget WTF file (M16-25).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`
by `test_cli`'s fixtures; its `Config.wtf` is then rewritten into a
CONSTRUCTED variant (L8: boundary and hostile inputs, labelled in each test
id). No real capture has a BOM: whether the client writes one is not
observed **[verify]**. Nothing here touches a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from pathlib import Path

from test_cli import (
    _json,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, wtfconfig

CONFIG = "WTF/Config.wtf"


def _first_set_line(config: Path) -> tuple[str, str]:
    doc = wtfconfig.read_config(config)
    first = doc.lines[0]
    assert isinstance(first, wtfconfig.SetLine), "the capture starts with a SET line"
    return first.name, first.value


def test_constructed_bom_config_cvar_get_finds_line_one(flavor: Path) -> None:
    config = flavor / CONFIG
    name, value = _first_set_line(config)
    original = config.read_bytes()
    config.write_bytes(wtfconfig.BOM + original)

    got = _json(cli.CVarGetReport, "cvar", "get", name)
    assert (got.name, got.value, got.line, got.unknown_lines) == (name, value, 1, 0)
    text = ok("cvar", "get", name).stdout
    assert f'{name} = "{value}"  ({CONFIG}, line 1)' in text
    listed = _json(cli.CVarListReport, "cvar", "list")
    assert listed.cvars[0].line == 1
    assert listed.unknown_lines == []
    assert config.read_bytes() == wtfconfig.BOM + original, "read only (L1)"


def test_constructed_over_budget_config_is_refused_with_a_position(flavor: Path) -> None:
    config = flavor / CONFIG
    budget = wtfconfig.CONFIG_BUDGET
    config.write_bytes(b'SET a "1"\n' * (budget.max_lines + 1))

    result = run("cvar", "get", "a")
    assert result.exit_code == cli.EXIT_ERROR, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    assert f"line {budget.max_lines + 1}, column 1" in result.stderr
    assert "refused" in result.stderr
    assert "WtfLimitError" not in result.stderr and "CONFIG_BUDGET" not in result.stderr

    as_json = run("cvar", "get", "a", "--json")
    assert as_json.exit_code == cli.EXIT_ERROR


def test_constructed_over_budget_config_is_a_doctor_problem_not_a_failure(flavor: Path) -> None:
    config = flavor / CONFIG
    config.write_bytes(b"x" * (wtfconfig.CONFIG_BUDGET.max_bytes + 1))
    result = run("doctor", "--offline", "--json")
    assert "Traceback" not in result.stderr
    report = json.loads(result.stdout)
    assert any("cannot read the config files" in p for p in report["problems"]), report
