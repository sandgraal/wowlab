# Probe from review of m11/14-cli-followups; reproduces `looks save/show/compare` calling a choice the build has "unknown to this build" once `_choice_ref` gives it no name under another option
"""The tables are the wago.tools recordings for 1.60.1.70009 under
`fixtures/wago/` (README rows), served through `GameData` from files, so
nothing reaches the network (ADR-0012). Constructed (L8): a look that sets
choice 1 (a Skin Color choice of option 9, in the recorded ChrCustomizationChoice)
under option 10 (Face), typed at `save` and written into a saved look for
`show` and `compare`. The user data directory is redirected into `tmp_path`
and discovery finds no install.

Since M11-14, `_choice_ref` leaves `choice_name` None when the choice is not
the option's. `LooksChoiceRef.choice_name` documents None as "unknown to the
build", and `_ref_text` and `compare`'s `side()` print "(unknown to this
build)" for None. Choice 1 is in the build, so that wording is false. The
positive control is the same choice under its own option, which is named.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install
from wowlab_core.gamedata import GameData, TableNotPublished

WAGO = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
BUILD = "1.60.1.70009"
SKIN, FACE = 9, 10  # Human body type 0 options in the recorded build
PLAIN_SKIN = 1  # a choice of option 9
WRONG = "1 (unknown to this build)"

runner = CliRunner()


class _Source:
    name = "fixtures"

    def fetch_builds(self, dest: BinaryIO) -> str:
        dest.write(gzip.decompress((WAGO / "builds.2026-09-28.json.gz").read_bytes()))
        return "fixture:builds"

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        if build != BUILD:
            raise TableNotPublished(table, build)
        plain = WAGO / f"{table}.{BUILD}.csv"
        body = (
            plain.read_bytes()
            if plain.exists()
            else gzip.decompress((WAGO / f"{table}.{BUILD}.csv.gz").read_bytes())
        )
        dest.write(body)
        return f"fixture:{table}"


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    empty = tmp_path / "defaults" / "World of Warcraft"
    empty.mkdir(parents=True)
    monkeypatch.setattr(install, "default_roots", lambda **k: (empty,))

    @contextmanager
    def fake() -> Iterator[GameData]:
        yield GameData(_Source(), cache_dir=redirected / "gamedata")

    monkeypatch.setattr(cli, "_open_gamedata", fake)
    return redirected


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def save(name: str, option: int) -> Any:
    return run(
        "looks", "save", name, "--race", "human", "--sex", "0", "--build", BUILD,
        "--choice", f"{option}={PLAIN_SKIN}",
    )  # fmt: skip


def test_positive_control_a_choice_under_its_own_option_is_named() -> None:
    result = save("right", SKIN)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert WRONG not in result.stdout


def test_save_does_not_call_a_known_choice_unknown_to_the_build() -> None:
    result = save("wrong", FACE)
    assert result.exit_code == 1
    assert f"belongs to option {SKIN}" in result.stdout + result.stderr
    assert WRONG not in result.stdout + result.stderr, result.stdout


def _misfiled(user_data: Path) -> None:
    """Save a and b with choice 1 under Skin Color, then move b's choice under Face."""
    assert save("a", SKIN).exit_code == 0
    assert save("b", SKIN).exit_code == 0
    path = user_data / "looks" / "b.json"
    text = json.dumps(json.loads(path.read_text(encoding="utf-8")))
    assert f'"{SKIN}": {PLAIN_SKIN}' in text, text  # the stored choices mapping
    path.write_text(
        text.replace(f'"{SKIN}": {PLAIN_SKIN}', f'"{FACE}": {PLAIN_SKIN}'), encoding="utf-8"
    )


def test_show_does_not_call_a_known_choice_unknown(user_data: Path) -> None:
    _misfiled(user_data)
    show = run("looks", "show", "b", "--build", BUILD)
    assert f"belongs to option {SKIN}" in show.stdout, show.stdout
    assert WRONG not in show.stdout, show.stdout


def test_compare_does_not_call_a_known_choice_unknown(user_data: Path) -> None:
    _misfiled(user_data)
    compare = run("looks", "compare", "a", "b", "--build", BUILD)
    assert f"belongs to option {SKIN}" in compare.stdout, compare.stdout
    assert WRONG not in compare.stdout, compare.stdout
