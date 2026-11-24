"""`wowlab spy list` and `spy show` (M22-01).

The real Spy capture (the three `Spy.lua` files under
`fixtures/macos-71037/forever/WTF/Account/`) dropped into a copy of the
captured install `fixtures/macos-70888/` in `tmp_path`. Only `WTF/Account`
is copied from `macos-71037/`, a whole run since M21-02, so its
`Config.wtf` never replaces the root's and the install stays
`macos-70888`'s. Tests named `constructed` replace or add a file built in the
test (invented names) to reach what the capture does not hold: a kill-on-
sight entry, a fight, a note and tag, an unknown version, a file that is not
data. No test reads a real install; each checks that the copy is unchanged
after every run (L1).
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wowlab_core import cli

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _casc_build import tree_state
from _user_dirs import redirect_user_dirs

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_forever_"
ACCOUNT = "LABACCOUNTA"
SANDGRAAL = "WTF/Account/LABACCOUNTA/1/Sandgraal-Rizzen/SavedVariables/Spy.lua"
TYRONE = "WTF/Account/LABACCOUNTA/1/Tyrone-Hamberglar/SavedVariables/Spy.lua"
ACCOUNT_FILE = "WTF/Account/LABACCOUNTA/SavedVariables/Spy.lua"
NL = "\r\n"
KEY = "Inventa-Fakerealm"  # invented


class Env:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.before = tree_state(root)

    def put(self, rel: str, data: bytes) -> None:
        """A constructed file, written by the test before any run."""
        path = self.root / FLAVOR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.before = tree_state(self.root)

    def run(self, *args: str) -> Any:
        return runner.invoke(cli.app, [*args, "--root", str(self.root)], terminal_width=200)

    def json(self, *args: str, code: int = 0) -> dict[str, Any]:
        result = self.run(*args, "--json")
        assert result.exit_code == code, result.output
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    capture = FIXTURES / "macos-70888"
    shutil.copy2(capture / ".build.info", root / ".build.info")
    shutil.copytree(capture / "forever", root / FLAVOR)
    # Only the Spy files' account tree (M22-01), never macos-71037's Config.wtf.
    shutil.copytree(
        FIXTURES / "macos-71037" / "forever" / "WTF" / "Account",
        root / FLAVOR / "WTF" / "Account",
        dirs_exist_ok=True,
    )
    made = Env(root)
    yield made
    assert tree_state(made.root) == made.before, "the install copy changed (L1)"


def _char_file(body: str, version: str = '"1.1"') -> bytes:
    """A constructed `SpyPerCharDB` in the capture's layout."""
    return (NL.join(["", "SpyPerCharDB = {", f'["version"] = {version},', body, "}"]) + NL).encode()


def _constructed_rows() -> bytes:
    player = NL.join(
        [
            f'["{KEY}"] = {{',
            f'["name"] = "{KEY}",',
            '["class"] = "ROGUE",',
            '["race"] = "Gnome",',
            '["level"] = 29,',
            '["faction"] = "Alliance",',
            '["isEnemy"] = true,',
            '["isGuess"] = true,',
            '["zone"] = "Invented Vale",',
            '["subZone"] = "Invented Hill",',
            '["time"] = 1795400000,',
            '["wins"] = 2,',
            '["loses"] = 1,',
            "},",
        ]
    )
    fight = NL.join(
        ["{", '["t"] = 1795400100,', f'["n"] = "{KEY}",', '["r"] = "W",', '["k"] = "kill",', "},"]
    )
    return _char_file(
        f'["PlayerData"] = {{{NL}{player}{NL}}},{NL}'
        f'["KOSData"] = {{{NL}["{KEY}"] = 1795400050,{NL}}},{NL}'
        f'["Encounters"] = {{{NL}{fight}{NL}}},'
    )


def test_list_reads_every_player_newest_first_with_who_saw_them(env: Env) -> None:
    result = env.run("spy", "list")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == (
        "24 enemy players Spy still keeps, as of your last logout or /reload "
        "(Sandgraal-Rizzen 24, Tyrone-Hamberglar 0)."
    )
    assert lines[1].startswith(
        "Nefzik-Spoonz: level 24 Gnome WARLOCK, Alliance, hostile when Spy last looked, "
    )
    assert lines[1].endswith(
        "; place Spy last recorded: The Barrens: Ratchet (by Sandgraal-Rizzen)"
    )
    assert "; last seen 20" in lines[1]
    assert len(lines) == 1 + 24 + 1 and lines[-1] == "9 notes: --notes shows them"
    sonja = next(line for line in lines if line.startswith("Sonja-Frayle"))
    assert sonja.endswith("place Spy last recorded: Wailing Caverns (by Sandgraal-Rizzen)")


def test_list_json_carries_each_row_with_its_file_and_character(env: Env) -> None:
    report = env.json("spy", "list")
    assert report["total"] == 24 and report["distinct"] == 24 and len(report["players"]) == 24
    assert report["caveat"] is None
    # The capture's profiles set no purge option: Spy's defaults, said.
    assert report["notes"][-1] == (
        "Spy's purge setting for each character: Sandgraal-Rizzen drops players when not "
        "seen for 90 days; Tyrone-Hamberglar drops players when not seen for 90 days."
    )
    assert report["warnings"] == [] and report["unread"] == []
    assert [(f["scope"], f["character"], f["players"]) for f in report["files"]] == [
        ("account", None, 0),
        ("character", "1/Sandgraal-Rizzen", 24),
        ("character", "1/Tyrone-Hamberglar", 0),
    ]
    first = report["players"][0]
    assert first["source"] == {
        "file": SANDGRAAL,
        "account": ACCOUNT,
        "character": "1/Sandgraal-Rizzen",
        "flavor_folder": FLAVOR,
    }
    assert first["key"] == "Nefzik-Spoonz" and first["other"] == {}
    assert cli.SpyListReport.model_validate(report).total == 24


def test_list_filters_and_orders_through_its_options(env: Env) -> None:
    found = env.json("spy", "list", "--search", "living legends", "--sort", "name")
    keys = [p["key"] for p in found["players"]]
    assert keys == sorted(keys, key=str.casefold) and len(keys) == 5
    assert all(p["guild"] == "The Living Legends" for p in found["players"])
    assert env.json("spy", "list", "--kos")["players"] == []
    assert len(env.json("spy", "list", "--enemies")["players"]) == 24
    levels = [p["level"] for p in env.json("spy", "list", "--sort", "level")["players"]]
    assert levels == sorted(levels, reverse=True)
    tyrone = env.json("spy", "list", "--character", "tyrone-hamberglar")
    assert tyrone["players"] == [] and tyrone["total"] == 0 and tyrone["distinct"] == 0
    # The headline counts the characters listed, and says no "0 of 0".
    text = env.run("spy", "list", "--character", "tyrone-hamberglar").stdout.splitlines()
    assert text[0] == (
        "0 enemy players Spy still keeps, as of your last logout or /reload (Tyrone-Hamberglar 0)."
    )
    assert not any("match" in line for line in text)
    text = env.run("spy", "list", "--search", "Tala").stdout.splitlines()
    assert text[1] == "1 of 24 rows match."


def test_list_refuses_an_empty_search_and_an_unknown_character(env: Env) -> None:
    result = env.run("spy", "list", "--search", " - ")
    assert result.exit_code == 2
    result = env.run("spy", "list", "--character", "Nobody-Here")
    assert result.exit_code == 2
    assert "Sandgraal-Rizzen, Tyrone-Hamberglar" in result.output


def test_show_prints_what_each_character_saw(env: Env) -> None:
    result = env.run("spy", "show", "sonja frayle")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[:2] == [
        "Sonja-Frayle, from Spy's saved data, as of your last logout or /reload:",
        "  Seen by Sandgraal-Rizzen: level 26 Undead PALADIN, Horde, hostile to your character "
        "when Spy last looked, guild Doomhammer",
    ]
    assert lines[2].startswith("    Last seen: 20")
    assert lines[3] == "    Place Spy last recorded: Wailing Caverns"
    # No map position was ever recorded for her: no map line.
    assert not any("map position" in line for line in lines)
    report = env.json("spy", "show", "Goruthuz-Mugstout")
    assert report["typed"] == "Goruthuz-Mugstout"
    assert [p["key"] for p in report["players"]] == ["Goruthuz-Mugstout"]
    assert report["lines"][2].startswith("    Last seen: ")
    assert report["lines"][3] == "    Place Spy last recorded: The Barrens: Ratchet"
    assert report["lines"][4] == (
        "    The last map position Spy recorded: map 1413 at 0.62, 0.37 (where your character "
        "stood; Spy does not update it inside an instance, so it can be older than the date "
        "above)"
    )


def test_show_of_a_player_spy_never_saw_exits_1(env: Env) -> None:
    result = env.run("spy", "show", "Nobody-Here")
    assert result.exit_code == 1
    assert "no player 'Nobody-Here' in Spy's saved data" in result.output


def test_constructed_kill_on_sight_fights_and_counts_show_in_list_and_show(env: Env) -> None:
    env.put(TYRONE, _constructed_rows())
    listed = env.json("spy", "list", "--kos")
    (p,) = listed["players"]
    assert (p["key"], p["kos"], p["kos_added"], p["wins"], p["loses"]) == (
        KEY,
        True,
        1795400050,
        2,
        1,
    )
    assert [k["key"] for k in listed["kos"]] == [KEY]
    text = env.run("spy", "list", "--kos").stdout.splitlines()
    assert text[2].startswith(
        f"{KEY} [kill on sight]: level 29? Gnome ROGUE, Alliance, hostile when Spy last looked;"
    )
    shown = env.json("spy", "show", KEY)
    assert [e["result"] for e in shown["encounters"]] == ["win"]
    lines = shown["lines"]
    assert "    Your killing blows on them (Spy's count): 2" in lines
    assert "    Your deaths Spy put on them: 1 (some from who had you targeted)" in lines
    assert any(line.startswith("    Kill on sight: yes, added ") for line in lines)
    assert "  Fights Spy counted against them: 1" in lines
    assert any(line.endswith(": won (your killing blow) (by Tyrone-Hamberglar)") for line in lines)


def test_constructed_notes_and_tags_from_the_account_file(env: Env) -> None:
    account = (
        NL.join(
            [
                "",
                "SpyDB = {",
                '["playerNotes"] = {',
                '["Region RP-PvP"] = {',
                '["Goruthuz-Mugstout"] = "camps Ratchet",',
                "},",
                "},",
                '["playerTags"] = {',
                '["Region RP-PvP"] = {',
                '["Goruthuz-Mugstout"] = "rival",',
                "},",
                "},",
                "}",
            ]
        )
        + NL
    ).encode()
    env.put(ACCOUNT_FILE, account)
    lines = env.json("spy", "show", "Goruthuz-Mugstout")["lines"]
    assert lines[-2:] == [
        "  Your note in Spy: camps Ratchet",
        "  Your tag in Spy: rival (a fair fight)",
    ]


def test_constructed_an_unknown_version_is_read_with_a_warning(env: Env) -> None:
    env.put(TYRONE, _char_file(f'["PlayerData"] = {{{NL}}},', version='"9.9"'))
    report = env.json("spy", "list")
    (warning,) = report["warnings"]
    assert "9.9" in warning and "1.1" in warning
    assert report["total"] == 24
    text = env.run("spy", "list").stdout.splitlines()
    assert text[1] == warning


def test_constructed_a_file_that_is_not_data_is_named_and_the_rest_read(env: Env) -> None:
    env.put(TYRONE, f"{NL}SpyPerCharDB = loadstring('x')(){NL}".encode())
    result = env.run("spy", "list")
    assert result.exit_code == 1
    assert "Sandgraal-Rizzen 24" in result.stdout
    assert f"could not read {TYRONE}" in result.stderr
    report = env.json("spy", "list", code=1)
    (bad,) = [f for f in report["files"] if f["error"] is not None]
    assert bad["character"] == "1/Tyrone-Hamberglar" and bad["present"] is None


def test_constructed_an_install_without_spy_says_so(env: Env) -> None:
    for rel in (SANDGRAAL, TYRONE, ACCOUNT_FILE):
        (env.root / FLAVOR / rel).unlink()
    env.before = tree_state(env.root)
    result = env.run("spy", "list")
    assert result.exit_code == 0
    assert result.stdout.startswith(f"No Spy saved data in {FLAVOR}: Spy is not installed")


def test_constructed_a_hostile_name_is_escaped_in_the_text(env: Env) -> None:
    hostile = "Evil\x1b[31m" + chr(0x202E) + "-Fakerealm"
    body = f'["PlayerData"] = {{{NL}["{hostile}"] = {{{NL}["level"] = 3,{NL}}},{NL}}},'
    env.put(TYRONE, _char_file(body))
    out = env.run("spy", "list", "--character", "Tyrone-Hamberglar").stdout
    assert "\x1b" not in out and chr(0x202E) not in out
    assert "Evil\\x1b[31m" in out and "-Fakerealm: level 3" in out


def test_the_text_on_the_capture_is_player_copy(env: Env) -> None:
    """The real capture's text, through the player-copy lint of
    `test_cli_player_copy.py` (it runs both commands only on installs
    without a Spy file)."""
    from test_cli_player_copy import Install, hits

    says = Install.of(env.root)
    for args in (
        ("spy", "list", "--notes"),
        ("spy", "show", "Sonja-Frayle", "--notes"),
        ("spy", "list", "--kos"),
    ):
        result = env.run(*args)
        assert result.exit_code == 0, result.output
        assert hits(result.stdout, says) == [], args


def test_constructed_an_entry_that_is_not_a_row_is_named_and_the_rest_listed(env: Env) -> None:
    body = f'["PlayerData"] = {{{NL}["{KEY}"] = true,{NL}}},'
    env.put(TYRONE, _char_file(body))
    result = env.run("spy", "list")
    assert result.exit_code == 0, result.output
    assert f"Could not be read: the player {KEY} (Tyrone-Hamberglar): not a table" in result.stdout
    assert "Sandgraal-Rizzen 24" in result.stdout
    (unread,) = env.json("spy", "list")["unread"]
    assert (unread["table"], unread["key"]) == ("PlayerData", KEY)


def test_constructed_rows_and_players_are_counted_apart(env: Env) -> None:
    """A player two characters saw is one player with two rows."""
    row = NL.join(
        [
            '["Goruthuz-Mugstout"] = {',
            '["name"] = "Goruthuz-Mugstout",',
            '["level"] = 44,',
            '["time"] = 1795500000,',
            "},",
        ]
    )
    env.put(TYRONE, _char_file(f'["PlayerData"] = {{{NL}{row}{NL}}},'))
    report = env.json("spy", "list")
    assert report["total"] == 25 and report["distinct"] == 24
    assert report["words"] == (
        "25 rows for 24 enemy players Spy still keeps (one per character that saw them), as "
        "of your last logout or /reload (Sandgraal-Rizzen 24, Tyrone-Hamberglar 1)."
    )


def test_constructed_a_character_that_takes_shared_rows_gets_the_caveat(env: Env) -> None:
    account = (
        NL.join(
            [
                "",
                "SpyDB = {",
                '["profileKeys"] = {',
                '["Sandgraal Rizzen"] = "Sandgraal Rizzen",',
                "},",
                '["profiles"] = {',
                '["Sandgraal Rizzen"] = {',
                '["UseData"] = true,',
                "},",
                "},",
                "}",
            ]
        )
        + NL
    ).encode()
    env.put(ACCOUNT_FILE, account)
    caveat = (
        "Spy takes the players other Spy users share for Sandgraal-Rizzen, so some places and "
        "times may come from other Spy users."
    )
    listed = env.json("spy", "list")
    assert listed["caveat"] == caveat
    assert listed["notes"][-1] == (
        "Spy's purge setting for each character: Sandgraal-Rizzen drops players when not "
        "seen for 90 days."
    )
    assert env.json("spy", "show", "Goruthuz-Mugstout")["caveat"] == caveat
    out = env.run("spy", "list").stdout
    assert " ".join(out.split()).count(caveat) == 1
