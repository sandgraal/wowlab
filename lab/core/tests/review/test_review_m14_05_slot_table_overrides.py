# Probe from review of m14/05-char-gear; reproduces a user gear_slots.toml with a slot key past CPython's 4300-digit limit escaping as a bare ValueError (traceback, not exit 1), and a case-variant colour key in the user file not replacing the shipped value it folds to.
"""M14-05: the user slot table, `gear.parse_slot_table` and `wowlab char gear`.

1. `parse_slot_table` checks a `[slots]` key with `isascii()` and
   `isdigit()`, then calls `int(key)`. A key of more than 4300 digits makes
   `int` raise a plain `ValueError` ("Exceeds the limit (4300 digits) for
   integer string conversion"), which is neither `SlotTableError` nor any
   type `load_slot_table` or `cli._handled` catches. Expected (the module's
   contract, "Raises `SlotTableError`", and the precedent of every other bad
   user table): exit 1 with "the slot table cannot be used: <file>: ...".
   Positive control: a key of four digits out of range is refused that way.

2. Within one file `parse_slot_table` refuses `"ff1eff00"` beside
   `"FF1EFF00"` as "one code", and `quality_word` matches case-folded, but
   a user file that sets `"FF1EFF00" = "Green"` over the shipped
   `"ff1eff00" = "Uncommon"` keeps both keys, and a link written
   `|cff1eff00` still reads "Uncommon": the user's value does not replace
   the one it names, against the shipped file's own comment ("Each value
   you set replaces the one here"). Positive control: the same override
   written in the shipped case does replace it.

CONSTRUCTED INPUT: the captured `fixtures/macos-70425/` tree copied into
`tmp_path` (as `test_cli_char_gear.py` builds it) and constructed user
slot tables. No real install, no network (ADR-0012).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gear, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance rows' flavor folder; tests may name it (L6)

runner = CliRunner()


@pytest.fixture
def user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    config.mkdir(parents=True)
    return config


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    flavor = root / FLAVOR
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70425" / "forever", flavor)
    shutil.copy2(FIXTURES / "macos" / "forever" / ".flavor.info", flavor / ".flavor.info")
    for p in flavor.rglob("WowLab.lua"):
        os.utime(p, ns=(1_700_000_000_000_000_000,) * 2)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "key",
    ["1001", "1" * 5000],
    ids=["constructed-control-four-digits", "constructed-5000-digit-slot-key"],
)
def test_constructed_a_huge_slot_key_exits_1_with_the_reason(
    key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_config: Path
) -> None:
    _install(tmp_path, monkeypatch)
    (user_config / gear.SLOTS_FILE).write_text(f'[slots]\n"{key}" = "x"\n', encoding="utf-8")
    result = runner.invoke(cli.app, ["char", "gear"])
    assert not isinstance(result.exception, ValueError), f"uncaught {result.exception!r}"
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert "the slot table cannot be used" in " ".join(result.stderr.split())


def test_constructed_huge_slot_key_raises_slot_table_error() -> None:
    with pytest.raises(gear.SlotTableError):
        gear.parse_slot_table(f'[slots]\n"{"1" * 5000}" = "x"\n')


def _shipped() -> gear.SlotTable:
    path = Path(gear.shipped_slots_path())
    return gear.parse_slot_table(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "key",
    ["ff1eff00", "FF1EFF00"],
    ids=["constructed-control-same-case", "constructed-other-case"],
)
def test_constructed_a_user_colour_key_replaces_the_code_it_folds_to(key: str) -> None:
    table = gear.parse_slot_table(f'[quality]\n"{key}" = "Green"\n', _shipped())
    assert gear.quality_word("ff1eff00", table) == "Green"
    assert gear.quality_word("FF1EFF00", table) == "Green"
