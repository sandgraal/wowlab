"""The region and the names in `char list --summary`, `char gear`,
`char kept` and the crew commands (M21-02).

The installs are those of the tests they extend: `test_cli_char_summary`'s
two captures side by side (`fixtures/macos/` as `_classic_beta_`, its real
`Config.wtf` saying `portal "test"`; `fixtures/macos-70888/` as `_forever_`,
its real `Config.wtf` saying `portal "US"`), `test_cli_char_kept`'s store
(constructed), and `test_cli_crew`'s constructed crew books in a copy of
`fixtures/macos-70888/`. A changed `Config.wtf` is `constructed` and its test id says so. Nothing
reads or writes a real install; each install is a copy in `tmp_path`.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import test_cli_char_kept as kept_cli
import test_cli_char_summary as hub_cli
from typer.testing import CliRunner

from wowlab_core import cli

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _casc_build import tree_state
from _crew_build import (
    FLAVOR,
    KEY_A,
    KEY_C,
    book,
    install,
    player,
)
from _user_dirs import redirect_user_dirs

user_data = hub_cli.user_data
both = hub_cli.both
kept = kept_cli.kept

runner = CliRunner()
BETA_REASON = 'the portal setting in WTF/Config.wtf is "test", not one of us, eu, kr, tw, cn'
CREW_SOURCE = (
    "from your game's settings at its last exit; wowlab assumes every player is in your "
    "region, not yet confirmed"
)


def _json(*args: str) -> dict[str, Any]:
    done = runner.invoke(cli.app, [*args, "--json"], terminal_width=200)
    assert done.exit_code == 0, done.output
    value = json.loads(done.stdout)
    assert isinstance(value, dict)
    return value


# ─── char list --summary ─────────────────────────────────────────────────────


def test_summary_rows_carry_the_region_and_both_name_forms_constructed(both: Path) -> None:
    before = tree_state(both)
    rows = hub_cli.rows_by_path(_json("char", "list", "--summary"))
    warlock = rows[hub_cli.WARLOCK]
    assert warlock["region"] == "us" and warlock["region_absent"] is None
    assert (warlock["first_name"], warlock["second_name"]) == ("Sandgraal", "Rizzen")
    assert warlock["folder_name"] == "Sandgraal-Rizzen" == warlock["label"]
    assert warlock["game_name"] == "Sandgraal Rizzen"
    for path in (hub_cli.FIRST, hub_cli.SECOND, hub_cli.BARE):
        row = rows[path]
        # Every state carries them: recorded and no lab record alike.
        assert row["region"] is None and row["region_absent"] == BETA_REASON, path
        first, second = row["label"].split("-")
        assert (row["first_name"], row["second_name"]) == (first, second)
        assert row["game_name"] == f"{first} {second}"
    assert tree_state(both) == before  # read-only (L1)


def test_summary_text_says_each_flavors_region_on_one_line_constructed(
    both: Path,
) -> None:
    done = runner.invoke(cli.app, ["char", "list", "--summary"], terminal_width=200)
    assert done.exit_code == 0, done.output
    lines = done.stdout.splitlines()
    # One line per flavor, known or not, before its accounts; as the JSON says.
    beta = lines.index(f"Flavor {hub_cli.BETA} ({hub_cli.BETA_VERSION})")
    assert lines[beta + 1] == f"Region: not known: {BETA_REASON}"
    assert lines[beta + 2].startswith(f"Account {hub_cli.ACCOUNT}: ")
    launch = lines.index(f"Flavor {hub_cli.FOREVER} ({hub_cli.LAUNCH_VERSION})")
    assert lines[launch + 1] == "Region: US"
    assert lines[launch + 2] == "Account LABACCOUNTA: 1 character folder"
    assert sum(line.startswith("Region: ") for line in lines) == 2


def test_a_flavor_whose_config_is_gone_says_so_constructed(both: Path) -> None:
    (both / hub_cli.FOREVER / "WTF" / "Config.wtf").unlink()
    row = hub_cli.rows_by_path(_json("char", "list", "--summary"))[hub_cli.WARLOCK]
    assert row["region"] is None and row["region_absent"] == "there is no WTF/Config.wtf"


@pytest.mark.parametrize(
    ("line", "expected"),
    [(b'SET portal "EU"\n', "eu"), (b'SET portal "kr"\n', "kr"), (b'SET portal "tw"\n', "tw")],
    ids=["eu", "kr", "tw"],
)
@pytest.mark.parametrize(
    "extra",
    [b"", b'SET portal "cn"\n'],
    ids=["one-line", "cn-last"],
)
def test_each_region_reaches_the_rows_constructed(
    both: Path, line: bytes, expected: str, extra: bytes
) -> None:
    config = both / hub_cli.FOREVER / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes().replace(b'SET portal "US"\n', line) + extra)
    row = hub_cli.rows_by_path(_json("char", "list", "--summary"))[hub_cli.WARLOCK]
    # The last portal line wins.
    assert row["region"] == ("cn" if extra else expected)


# ─── char gear ───────────────────────────────────────────────────────────────


def test_char_gear_carries_the_region_and_names_constructed(both: Path) -> None:
    report = _json("char", "gear", "--flavor", hub_cli.FOREVER)
    assert report["region"] == "us" and report["region_absent"] is None
    assert report["first_name"] == "Sandgraal" and report["second_name"] == "Rizzen"
    assert report["folder_name"] == "Sandgraal-Rizzen"
    assert report["game_name"] == "Sandgraal Rizzen"
    assert list(report)[-1] == "dressing"
    text = runner.invoke(cli.app, ["char", "gear", "--flavor", hub_cli.FOREVER])
    assert "Region: US" in text.stdout.splitlines()
    beta = _json("char", "gear", "--flavor", hub_cli.BETA, "--character", hub_cli.FIRST)
    assert beta["region"] is None and beta["region_absent"] == BETA_REASON
    beta_text = runner.invoke(
        cli.app, ["char", "gear", "--flavor", hub_cli.BETA, "--character", hub_cli.FIRST]
    )
    assert f"Region: not known: {BETA_REASON}" in beta_text.stdout.splitlines()


# ─── char kept ───────────────────────────────────────────────────────────────


def test_kept_rows_take_the_region_of_their_flavor_folder_constructed(
    kept: dict[str, Any],
) -> None:
    report = _json("char", "list", "--summary")
    sett = kept_cli.by_path(report["kept_in_backups"])[kept_cli.SETT]
    assert sett["region"] is None and sett["region_absent"] == BETA_REASON
    assert (sett["first_name"], sett["second_name"]) == ("Sangraal", "Sett")
    assert sett["game_name"] == "Sangraal Sett"
    one = _json("char", "kept", "--character", kept_cli.SETT)
    assert one["gear"]["region_absent"] == BETA_REASON
    assert one["gear"]["game_name"] == "Sangraal Sett"
    # The text says the region is the game folder's now, not the backup's.
    text = runner.invoke(cli.app, ["char", "kept"], terminal_width=200).stdout
    assert f"Region: not known: {BETA_REASON}" in text
    (both_cfg,) = [kept["root"] / hub_cli.BETA / "WTF" / "Config.wtf"]
    both_cfg.write_bytes(both_cfg.read_bytes().replace(b'"test"', b'"eu"'))
    text = runner.invoke(cli.app, ["char", "kept", "--character", kept_cli.SETT]).stdout
    assert "  Region: EU (from the game's settings now)" in text.splitlines()


def test_a_kept_row_whose_flavor_folder_is_gone_says_so_constructed(
    kept: dict[str, Any],
) -> None:
    shutil.rmtree(kept["root"] / hub_cli.BETA)
    report = _json("char", "kept")
    (sett,) = [r for r in report["kept"] if r["label"] == kept_cli.SETT.split("/")[1]]
    assert sett["region"] is None
    assert sett["region_absent"] == "its flavor folder is not in the game folder"


# ─── crew ────────────────────────────────────────────────────────────────────


@pytest.fixture
def crew_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    folder = tmp_path / "crew"
    folder.mkdir()
    return install(folder, book({KEY_A: player(rating="good"), KEY_C: player(), TAY: player()}))


# A Forever player as the owner's launch client named one (2026-11-23):
# `UnitName` gave "Tay" and "Soulrend", so the lab-addon keys them
# `<First>-<Second>`. Constructed: a key typed here, not a captured book.
TAY = "Tay-Soulrend"


def _crew(root: Path, *args: str) -> dict[str, Any]:
    return _json(*args, "--root", str(root), "--flavor", FLAVOR)


def test_crew_rows_carry_the_books_region_and_the_keys_names_constructed(crew_root: Path) -> None:
    before = tree_state(crew_root)
    report = _crew(crew_root, "crew", "list")
    assert report["region"] == "us" and report["region_absent"] is None
    rows = {p["key"]: p for p in report["players"]}
    # The capture's account holds a `<digits>/<First>-<Second>` folder, so
    # a key's second part is the second name, and there is no realm.
    tay = rows[TAY]
    assert tay["region"] == "us"
    assert (tay["first_name"], tay["second_name"]) == ("Tay", "Soulrend")
    assert tay["game_name"] == "Tay Soulrend" and tay["realm"] is None
    a = rows[KEY_A]
    assert (a["first_name"], a["second_name"], a["realm"]) == ("Inventeda", "Fakerealm", None)
    assert a["game_name"] == "Inventeda Fakerealm"
    assert rows[KEY_C]["second_name"] == "Otherfakerealm"
    shown = _crew(crew_root, "crew", "show", KEY_A)
    assert shown["region"] == "us" and shown["player"]["region"] == "us"
    assert f"  Region: US ({CREW_SOURCE})" in shown["lines"]
    listed = runner.invoke(
        cli.app, ["crew", "list", "--root", str(crew_root), "--flavor", FLAVOR], terminal_width=200
    ).stdout.splitlines()
    assert [line for line in listed if line.startswith("Region")] == [f"Region: US ({CREW_SOURCE})"]
    matches_text = runner.invoke(
        cli.app,
        ["crew", "matches", "--root", str(crew_root), "--flavor", FLAVOR],
        terminal_width=200,
    ).stdout.splitlines()
    assert f"Region: US ({CREW_SOURCE})" in matches_text
    matches = _crew(crew_root, "crew", "matches")
    assert matches["region"] == "us" and matches["region_absent"] is None
    world = _crew(crew_root, "crew", "matches", "--world")
    assert world["region"] == "us"
    assert tree_state(crew_root) == before  # read-only (L1)


@pytest.mark.parametrize("command", [["show", KEY_A], ["list"], ["matches"]])
def test_with_notes_the_region_line_leaves_the_caveat_to_them_constructed(
    crew_root: Path, command: list[str]
) -> None:
    """A report says "not yet confirmed" at most once: the standing notes
    already do (the role), so the region line leaves it out there."""
    args = ["crew", *command, "--root", str(crew_root), "--flavor", FLAVOR]
    plain = runner.invoke(cli.app, args, terminal_width=200).stdout
    noted = runner.invoke(cli.app, [*args, "--notes"], terminal_width=200).stdout
    assert f"Region: US ({CREW_SOURCE})" in plain
    unconfirmed_dropped = CREW_SOURCE.removesuffix(", not yet confirmed")
    if "Notes:" in noted:
        assert f"Region: US ({unconfirmed_dropped})\n" in noted
    else:  # no notes printed (no match listed): the line keeps its caveat
        assert f"Region: US ({CREW_SOURCE})\n" in noted
    assert noted.count("not yet confirmed") <= 1


def test_a_flavor_without_second_names_keeps_name_and_realm_constructed(
    crew_root: Path,
) -> None:
    """The capture's character folder moved to the retail shape
    (`<Realm>/<Name>/`, constructed): the key is `<Name>-<Realm>` again."""
    account = crew_root / FLAVOR / "WTF" / "Account" / "LABACCOUNTA"
    (account / "Somerealm").mkdir()
    (account / "1" / "Sandgraal-Rizzen").rename(account / "Somerealm" / "Sandgraal")
    (account / "1").rmdir()
    rows = {p["key"]: p for p in _crew(crew_root, "crew", "list")["players"]}
    tay = rows[TAY]
    assert (tay["first_name"], tay["second_name"], tay["realm"]) == ("Tay", None, "Soulrend")
    assert tay["game_name"] == "Tay"


def test_a_test_portal_leaves_every_crew_row_without_a_region_constructed(
    crew_root: Path,
) -> None:
    config = crew_root / FLAVOR / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes().replace(b'SET portal "US"', b'SET portal "test"'))
    report = _crew(crew_root, "crew", "list")
    assert report["region"] is None and report["region_absent"] == BETA_REASON
    assert all(p["region"] is None for p in report["players"])
    shown = _crew(crew_root, "crew", "show", KEY_A)
    assert f"  Region: not known: {BETA_REASON}" in shown["lines"]
