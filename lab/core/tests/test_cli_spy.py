"""`wowlab spy list` and `spy show` (M22-01).

The real Spy capture (`fixtures/macos-71037/`, three `Spy.lua` files)
dropped into a copy of the captured install `fixtures/macos-70888/` in
`tmp_path`. Tests named `constructed` replace or add a file built in the
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
    shutil.copytree(
        FIXTURES / "macos-71037" / "forever" / "WTF", root / FLAVOR / "WTF", dirs_exist_ok=True
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
        "Spy saw 24 players, as of your last logout or /reload "
        "(Sandgraal-Rizzen 24, Tyrone-Hamberglar 0)."
    )
    assert lines[1].startswith("Nefzik-Spoonz: level 24 Gnome WARLOCK, Alliance, enemy, ")
    assert lines[1].endswith("in The Barrens: Ratchet (by Sandgraal-Rizzen)")
    assert len(lines) == 1 + 24 + 1 and lines[-1] == "6 notes: --notes shows them"
    sonja = next(line for line in lines if line.startswith("Sonja-Frayle"))
    assert "in Wailing Caverns (by Sandgraal-Rizzen)" in sonja


def test_list_json_carries_each_row_with_its_file_and_character(env: Env) -> None:
    report = env.json("spy", "list")
    assert report["total"] == 24 and len(report["players"]) == 24
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
    assert env.json("spy", "list", "--character", "tyrone-hamberglar")["players"] == []
    text = env.run("spy", "list", "--search", "Tala").stdout.splitlines()
    assert text[1] == "1 of 24 players match."


def test_list_refuses_an_empty_search_and_an_unknown_character(env: Env) -> None:
    result = env.run("spy", "list", "--search", " - ")
    assert result.exit_code == 2
    result = env.run("spy", "list", "--character", "Nobody-Here")
    assert result.exit_code == 2
    assert "Sandgraal-Rizzen, Tyrone-Hamberglar" in result.output


def test_show_prints_what_each_character_saw(env: Env) -> None:
    result = env.run("spy", "show", "sonja frayle")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[:3] == [
        "Sonja-Frayle, from Spy's saved data, as of your last logout or /reload:",
        "  Seen by Sandgraal-Rizzen: level 26 Undead PALADIN, Horde, enemy, guild Doomhammer",
        result.stdout.splitlines()[2],
    ]
    assert result.stdout.splitlines()[2].endswith("in Wailing Caverns")
    report = env.json("spy", "show", "Goruthuz-Mugstout")
    assert report["typed"] == "Goruthuz-Mugstout"
    assert [p["key"] for p in report["players"]] == ["Goruthuz-Mugstout"]
    assert report["lines"][2].startswith("    Last seen: ")
    assert report["lines"][3] == "    Map 1413 at 0.62, 0.37 (where your character stood)"


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
    assert text[2].startswith(f"{KEY} [kill on sight]: level 29? Gnome ROGUE, Alliance, enemy;")
    shown = env.json("spy", "show", KEY)
    assert [e["result"] for e in shown["encounters"]] == ["win"]
    lines = shown["lines"]
    assert "    Fights Spy counted: 2 won, 1 lost" in lines
    assert any(line.startswith("    Kill on sight: yes, added ") for line in lines)
    assert "  Fights Spy counted against them: 1" in lines
    assert any(line.endswith(": won (kill) (by Tyrone-Hamberglar)") for line in lines)


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
