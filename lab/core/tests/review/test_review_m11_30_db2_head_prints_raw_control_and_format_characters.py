# Probe from review of m11/30-terminal-safe-errors; reproduces `wowlab db2 head` printing a table row's ESC, BEL, U+202E and U+2028 raw to stdout
"""`db2 head` text output bypasses `_safe` (constructed, hostile input).

M11-30's acceptance says no raw Cf, Zl, Zp or control character reaches
stdout or stderr, and the branch's own `docs/LAB_PLAN.md` §6.11 amendment
says "text output is terminal-safe". `db2 head` writes the CSV of rows from
wago.tools (a community-run service, ADR-0022) or its cache with a bare
`typer.echo`, so a string field holding an OSC sequence, BEL, a bidi
override or a line separator reaches the terminal as is.

The game-data client is replaced by a stub that yields one constructed row:
no network, no cache, no install (`--build` is given, so no install is
discovered). OSC and BEL are used rather than a CSI sequence because
click's `echo` strips CSI (only) when the stream is not a terminal, which
would hide the leak under `CliRunner` but not on a real terminal.
"""

from __future__ import annotations

import contextlib
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE, Cf
LSEP = chr(0x2028)  # LINE SEPARATOR, Zl
FIELD = "Mage\x1b]0;pwned\x07" + RLO + "x" + LSEP + "y"
BUILD = "1.60.1.70009"


class _StubData:
    def rows(self, table: str, version: str) -> Iterator[dict[str, str]]:
        yield {"ID": "1", "Name_lang": FIELD}


@contextlib.contextmanager
def _stub() -> Iterator[_StubData]:
    yield _StubData()


def _raw_unsafe(text: str) -> list[str]:
    """Characters a terminal must not get raw, one output line at a time."""
    return [
        hex(ord(c))
        for line in text.split("\n")
        for c in line
        if c != "\t" and unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp")
    ]


@pytest.fixture
def stubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "wowlab")
    monkeypatch.setenv(install.ENV_ROOT, str(tmp_path / "no install"))
    monkeypatch.setattr(cli, "_open_gamedata", _stub)
    monkeypatch.setattr(cli, "_check_table_key", lambda *a, **k: None)


def test_positive_control_the_row_reaches_the_output_and_json_is_clean(stubbed: None) -> None:
    assert _raw_unsafe(FIELD) != [], "the detector sees the raw characters"
    runner = CliRunner()
    text = runner.invoke(cli.app, ["db2", "head", "ChrClasses", "--build", BUILD])
    assert text.exit_code == 0, (text.stdout, text.stderr)
    assert "Mage" in text.stdout, "the stub's row is what db2 head prints"
    as_json = runner.invoke(cli.app, ["db2", "head", "ChrClasses", "--build", BUILD, "--json"])
    assert as_json.exit_code == 0, (as_json.stdout, as_json.stderr)
    assert _raw_unsafe(as_json.stdout) == []
    assert cli.HeadReport.model_validate_json(as_json.stdout).rows[0]["Name_lang"] == FIELD


def test_db2_head_text_output_is_terminal_safe_constructed(stubbed: None) -> None:
    result = CliRunner().invoke(cli.app, ["db2", "head", "ChrClasses", "--build", BUILD])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "Mage" in result.stdout
    assert _raw_unsafe(result.stdout) == [], result.stdout
    assert _raw_unsafe(result.stderr) == [], result.stderr
