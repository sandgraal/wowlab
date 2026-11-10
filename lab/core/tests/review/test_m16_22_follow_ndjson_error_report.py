# Probe from review of m16/22-json-errors; reproduces an ErrorReport printed across five lines inside the `log tail --follow --json` NDJSON stream.
"""`log tail --follow --json` prints "one LogTailLine per line of output"
(its own help, `JSON: LogTailReport; with --follow, one LogTailLine per
line of output`). M16-22 adds an `ErrorReport` on stdout when a `--json`
run fails before printing JSON, and the PR says it keeps "NDJSON streams
parseable". `_handled` prints it through `_emit`, which indents by two, so
a `--follow` run that fails before its first line prints a five-line
document into a line-delimited stream: no line of it parses alone.

Constructed (boundary): the install root named with `--root` does not
exist; the user data directory is redirected into `tmp_path`. Nothing reads
or writes a real install.
"""

from __future__ import annotations

import json
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "userdata")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)


def test_positive_control_without_follow_the_error_is_one_document_constructed(
    tmp_path: Path,
) -> None:
    result = runner.invoke(cli.app, ["log", "tail", "--json", "--root", str(tmp_path / "nowhere")])
    assert result.exit_code == cli.EXIT_ERROR
    report = cli.ErrorReport.model_validate_json(result.stdout)
    assert report.exit_code == cli.EXIT_ERROR


def test_follow_json_error_is_one_ndjson_line_constructed(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app, ["log", "tail", "--follow", "--json", "--root", str(tmp_path / "nowhere")]
    )
    assert result.exit_code == cli.EXIT_ERROR
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines, result.stdout
    for line in lines:
        json.loads(line)  # every line of a --follow stream is one JSON value
