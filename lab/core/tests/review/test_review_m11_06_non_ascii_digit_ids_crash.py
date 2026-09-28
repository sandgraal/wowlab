# Probe from review of m11/06-looks-cli (5164aff); reproduces: an id argument that str.isdigit() accepts but int() rejects ("²", or more than 4300 digits) escapes as an uncaught ValueError (exit 1 with a traceback) instead of the usage error, exit 2, that docs/LAB_PLAN.md §6.11 gives a malformed argument.
"""`cli._parse_choices`, `_parse_sex`, `_resolve_race` and `_resolve_class`
guard `int(text)` with `text.strip().isdigit()`. `isdigit()` is true for
superscripts and other Unicode digits that `int()` refuses, and `int()` also
refuses a decimal string over `sys.get_int_max_str_digits()` (4300) digits.
Both reach `int()` and raise `ValueError`, which `_handled` does not catch.

Constructed inputs (hostile command-line arguments, L8). The tables are the
recorded 1.60.1.70009 wago.tools files, served from `fixtures/wago/`, so
nothing reaches the network; the user data directory is in `tmp_path` and no
install is discovered. Positive control: the ASCII non-digit forms the
implementer's tests grade (`9=x`, `--sex tall`, race `no-such-race`, class
`bard`) exit 2.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install
from wowlab_core.gamedata import GameData, TableNotPublished

WAGO = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
BUILD = "1.60.1.70009"
SUPERSCRIPT_TWO = "²"  # str.isdigit() is True, int() raises
TOO_LONG = "9" * 5000  # over int()'s default 4300-digit limit

runner = CliRunner()


class _Source:
    name = "fixtures"

    def fetch_builds(self, dest: BinaryIO) -> str:
        dest.write(gzip.decompress((WAGO / "builds.2026-09-28.json.gz").read_bytes()))
        return "fixture:builds"

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        if build != BUILD:
            raise TableNotPublished(table, build)
        plain = WAGO / f"{table}.{build}.csv"
        if plain.exists():
            dest.write(plain.read_bytes())
        else:
            dest.write(gzip.decompress((WAGO / f"{table}.{build}.csv.gz").read_bytes()))
        return f"fixture:{table}"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data_dir)
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    empty = tmp_path / "defaults" / "World of Warcraft"
    empty.mkdir(parents=True)
    monkeypatch.setattr(install, "default_roots", lambda **k: (empty,))

    @contextmanager
    def fake() -> Iterator[GameData]:
        yield GameData(_Source(), cache_dir=data_dir / "gamedata")

    monkeypatch.setattr(cli, "_open_gamedata", fake)


SAVE = ("looks", "save", "m", "--race", "1", "--sex", "0", "--build", BUILD)
OPTIONS = ("looks", "options", "--build", BUILD)


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((*SAVE, "--choice", "9=x"), id="control-choice-ascii"),
        pytest.param((*OPTIONS, "1", "--sex", "tall"), id="control-sex-ascii"),
        pytest.param((*OPTIONS, "no-such-race"), id="control-race-ascii"),
        pytest.param((*OPTIONS, "1", "--class", "bard"), id="control-class-ascii"),
    ],
)
def test_control_ascii_non_digits_are_usage_errors(args: tuple[str, ...]) -> None:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 2, (result.stdout, result.stderr, result.exception)


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((*SAVE, "--choice", f"9={SUPERSCRIPT_TWO}"), id="choice-superscript"),
        pytest.param((*SAVE, "--choice", f"9={TOO_LONG}"), id="choice-too-long"),
        pytest.param((*OPTIONS, "1", "--sex", SUPERSCRIPT_TWO), id="sex-superscript"),
        pytest.param((*OPTIONS, SUPERSCRIPT_TWO), id="race-superscript"),
        pytest.param((*OPTIONS, "1", "--class", SUPERSCRIPT_TWO), id="class-superscript"),
    ],
)
def test_constructed_non_ascii_digit_ids_are_usage_errors(args: tuple[str, ...]) -> None:
    result = runner.invoke(cli.app, list(args))
    assert not isinstance(result.exception, ValueError), repr(result.exception)
    assert result.exit_code == 2, (result.stdout, result.stderr, result.exception)
