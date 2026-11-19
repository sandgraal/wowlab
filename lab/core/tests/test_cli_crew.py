"""`wowlab crew list` and `crew show` (M19-02, docs/LAB_PLAN.md §20.2) on
constructed crew books. CONSTRUCTED INPUT: every book is built at run time
by `parser/_crew_build.py` (ADR-0036 term 3: no real crew record enters the
repository) inside a copy of the captured install `fixtures/macos-70888/`
in `tmp_path`. No test reads a real install; each checks that the copy is
unchanged after every run (L1).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import cli, crewbook, ui

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _casc_build import tree_state
from _crew_build import (
    ACCOUNT,
    FLAVOR,
    KEY_A,
    KEY_B,
    KEY_C,
    NOTE,
    T0,
    T1,
    account_file,
    book,
    install,
    player,
)
from _user_dirs import redirect_user_dirs

runner = CliRunner()
AS_OF = "as of your last logout or /reload"
SECOND = "LABACCOUNTB"  # a constructed second account folder


class Env:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.before = tree_state(root)

    def settle(self) -> None:
        """After the test's own change to the copy: what is compared at the end."""
        self.before = tree_state(self.root)

    def run(self, *args: str, flavor: bool = True) -> Any:
        extra = ["--root", str(self.root), *(["--flavor", FLAVOR] if flavor else [])]
        return runner.invoke(cli.app, [*args, *extra], terminal_width=200)

    def json(self, *args: str) -> dict[str, Any]:
        result = self.run(*args, "--json")
        assert result.exit_code == 0, result.output
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value

    def account_file(self, account: str = ACCOUNT) -> Path:
        return self.root / FLAVOR / "WTF" / "Account" / account / "SavedVariables" / "WowLab.lua"


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    made: list[Env] = []

    def build(crew: bytes | None, accounts: tuple[str, ...] = (ACCOUNT,)) -> Env:
        folder = tmp_path / f"i{len(made)}"
        folder.mkdir()
        made.append(Env(install(folder, crew, accounts=accounts)))
        return made[-1]

    yield build
    for env in made:
        assert tree_state(env.root) == env.before, "the constructed install changed (L1)"


def _crew(make: Any) -> Env:
    env: Env = make(
        book(
            {
                KEY_A: player(rating="bad", note=NOTE, last=T1),
                KEY_B: player(rating="good", last=T0 + 10, met=9, where=None),
                KEY_C: player(last=T0, met=1),
            },
            current=[KEY_A],
        )
    )
    return env


# ─── the outcomes ────────────────────────────────────────────────────────────


def test_never_turned_on_is_one_sentence_and_exit_0_constructed(make: Any) -> None:
    env = make(None)
    result = env.run("crew", "list")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines == [
        f"The crew book is not turned on for game account {ACCOUNT} in {FLAVOR} ({AS_OF}). "
        "Turn it on in game with /wowlab crew on."
    ]
    report = cli.CrewReport.model_validate(env.json("crew", "list"))
    assert report.state == "not_turned_on" and report.account == ACCOUNT
    shown = env.run("crew", "show", KEY_A)
    assert shown.exit_code == 0 and shown.stdout.splitlines() == lines


def test_no_wowlab_file_at_all_is_not_turned_on_constructed(make: Any) -> None:
    env = make(None)
    env.account_file().unlink()
    env.settle()
    result = env.run("crew", "list")
    assert result.exit_code == 0 and "is not turned on for game account" in result.stdout
    assert cli.CrewReport.model_validate(env.json("crew", "list")).file is None


def test_turned_off_keeps_its_players_constructed(make: Any) -> None:
    env = make(book({KEY_A: player(), KEY_B: player()}, on=False))
    result = env.run("crew", "list")
    assert result.exit_code == 0, result.output
    first = result.stdout.splitlines()[0]
    assert "is turned off" in first and "keeps 2 players" in first and AS_OF in first
    assert cli.CrewReport.model_validate(env.json("crew", "list")).state == "off"


def test_an_empty_book_names_the_account_and_flavor_constructed(make: Any) -> None:
    env = make(book({}))
    result = env.run("crew", "list")
    assert result.exit_code == 0
    first = result.stdout.splitlines()[0]
    assert f"game account {ACCOUNT} in {FLAVOR}" in first and "holds no player yet" in first
    assert "started " in first


def test_an_unknown_schema_is_refused_with_its_number_constructed(make: Any) -> None:
    env = make(book({KEY_A: player()}, schema=7))
    result = env.run("crew", "list")
    assert result.exit_code == 1
    assert "is schema 7" in result.stderr and "knows schema 1 only" in result.stderr


def test_a_malformed_entry_is_listed_as_unread_constructed(make: Any) -> None:
    env = make(book({KEY_A: player(level="x"), KEY_B: player()}))
    result = env.run("crew", "list")
    assert result.exit_code == 0, result.output
    assert "1 entry could not be read:" in result.stdout
    assert f"  {KEY_A}: level is not a whole number of 1 or more" in result.stdout
    report = cli.CrewReport.model_validate(env.json("crew", "list"))
    assert [p.key for p in report.players] == [KEY_B] and report.total == 2
    assert [u.key for u in report.unread] == [KEY_A]


# ─── list and show ───────────────────────────────────────────────────────────


def _keys_in_text(stdout: str) -> list[str]:
    return [line.split(": ", 1)[0].strip() for line in stdout.splitlines() if line.startswith("  ")]


def test_list_text_and_json_say_the_same_constructed(make: Any) -> None:
    env = _crew(make)
    for args in (
        (),
        ("--sort", "met"),
        ("--sort", "name"),
        ("--rated", "any"),
        ("--rated", "good"),
        ("--noted",),
        ("--search", "inventedc"),
    ):
        text = env.run("crew", "list", *args)
        assert text.exit_code == 0, text.output
        report = cli.CrewReport.model_validate(env.json("crew", "list", *args))
        assert _keys_in_text(text.stdout) == [p.key for p in report.players], args
        assert report.total == 3
        for p in report.players:
            (line,) = [x for x in text.stdout.splitlines() if x.startswith(f"  {p.key}:")]
            assert ("rated " + p.rating in line) if p.rating else ("rated" not in line)
            assert ("has a note" in line) == bool(p.note)
            assert f"grouped {p.met} time" in line
    header = env.run("crew", "list").stdout.splitlines()[0]
    assert AS_OF in header and "3 players" in header and "started " in header


def test_list_default_order_and_filters_constructed(make: Any) -> None:
    env = _crew(make)
    assert _keys_in_text(env.run("crew", "list").stdout) == [KEY_A, KEY_B, KEY_C]
    assert _keys_in_text(env.run("crew", "list", "--sort", "met").stdout) == [KEY_B, KEY_A, KEY_C]
    assert _keys_in_text(env.run("crew", "list", "--rated", "bad").stdout) == [KEY_A]
    out = env.run("crew", "list", "--noted").stdout
    assert _keys_in_text(out) == [KEY_A] and "1 of 3 players match." in out


def test_show_prints_every_field_constructed(make: Any) -> None:
    env = _crew(make)
    result = env.run("crew", "show", KEY_A)
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert out.splitlines()[0].startswith(f"{KEY_A}, from the crew book of game account")
    for words in (
        "Class: MAGE",
        "Level: 60",
        "Role (if the group set one): HEALER",
        "Groups shared: 3",
        "Your rating: bad",
        f"Your note: {NOTE}",
        "In your group at the last save: yes",
        "(instance 2291)",
    ):
        assert words in out, words
    report = cli.CrewShowReport.model_validate(env.json("crew", "show", KEY_A))
    assert report.player is not None and report.player.key == KEY_A and report.player.note == NOTE
    other = env.run("crew", "show", KEY_B).stdout
    assert "Where you last grouped: not in an instance, or not recorded" in other


def test_show_matches_a_typed_name_loosely_constructed(make: Any) -> None:
    env = _crew(make)
    name, realm = KEY_C.split("-")
    typed = f"{name.lower()}-{realm[:5]} {realm[5:].upper()}"
    report = cli.CrewShowReport.model_validate(env.json("crew", "show", typed))
    assert report.player is not None and report.player.key == KEY_C and report.typed == typed


def test_show_lists_the_candidates_when_several_keys_match_constructed(make: Any) -> None:
    env = make(book({KEY_A: player(), KEY_A.lower(): player()}))
    result = env.run("crew", "show", KEY_A.upper())
    assert result.exit_code == 2
    assert "matches 2 players" in result.stderr
    assert KEY_A in result.stderr and KEY_A.lower() in result.stderr
    # The exact stored key still picks one.
    assert env.run("crew", "show", KEY_A).exit_code == 0


def test_show_of_a_player_not_in_the_book_is_an_error_constructed(make: Any) -> None:
    env = _crew(make)
    result = env.run("crew", "show", KEY_A + "x")
    assert result.exit_code == 1 and "no player" in result.stderr


# ─── which book ──────────────────────────────────────────────────────────────


def test_several_books_list_the_candidates_and_exit_2_constructed(make: Any) -> None:
    env = make(book({KEY_A: player()}), accounts=(ACCOUNT, SECOND))
    result = env.run("crew", "list")
    assert result.exit_code == 2
    for account in (ACCOUNT, SECOND):
        assert f"game account {account} in {FLAVOR}" in result.stderr
    assert "--account" in result.stderr
    picked = cli.CrewReport.model_validate(env.json("crew", "list", "--account", SECOND))
    assert picked.account == SECOND and [p.key for p in picked.players] == [KEY_A]


def test_the_one_account_with_a_book_is_used_constructed(make: Any) -> None:
    env = make(book({KEY_A: player()}), accounts=(ACCOUNT, SECOND))
    env.account_file(SECOND).write_bytes(account_file(None))  # the captured file: no book
    env.settle()
    report = cli.CrewReport.model_validate(env.json("crew", "list"))
    assert report.account == ACCOUNT and len(report.places) == 2
    assert [p.holds_book for p in report.places] in ([True, False], [False, True])


def test_no_book_in_any_account_names_every_one_constructed(make: Any) -> None:
    env = make(None, accounts=(ACCOUNT, SECOND))
    result = env.run("crew", "list")
    assert result.exit_code == 0
    assert "not turned on in any game account wowlab read" in result.stdout
    assert ACCOUNT in result.stdout and SECOND in result.stdout


def test_an_account_not_there_is_a_usage_error_constructed(make: Any) -> None:
    env = _crew(make)
    assert env.run("crew", "list", "--account", "NOSUCHACCOUNT").exit_code == 2


def test_an_empty_root_is_a_usage_error_constructed(make: Any) -> None:
    _crew(make)
    result = runner.invoke(cli.app, ["crew", "list", "--root", ""])
    assert result.exit_code == 2 and "--root is empty" in result.stderr


# ─── other people's text ─────────────────────────────────────────────────────


def _hostile(base: str, size: int = 0) -> str:
    """`base` with ESC, CSI, U+009B, U+202E, `|`, markup and a line break,
    assembled at run time, padded to `size` bytes."""
    name, realm = base.split("-")
    text = name + "\x1b[31m" + chr(0x9B) + "2J" + chr(0x202E) + "|cff|r<script>x</script>\n-"
    text += realm
    pad = size - len(text.encode("utf-8"))
    return text + "x" * pad if pad > 0 else text


_RAW = re.compile(
    "[\x00-\x09\x0b-\x1f\x7f-\x9f"
    + "".join(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))
    + "]"
)


def test_hostile_text_reaches_the_terminal_escaped_constructed(make: Any) -> None:
    key = _hostile(KEY_A, 10_000)
    guild, note, where = _hostile(KEY_B), _hostile(KEY_C), _hostile(KEY_B)
    env = make(book({key: player(guild=guild, note=note, where=where, rating="bad")}))
    for args in (("crew", "list"), ("crew", "show", key), ("crew", "list", "--notes")):
        result = env.run(*args)
        assert result.exit_code == 0, result.output
        assert not _RAW.search(result.stdout), args
        assert "\\x1b[31m" in result.stdout and "\\xc2\\x9b" in result.stdout
        assert "\\xe2\\x80\\xae" in result.stdout and "|cff|r<script>" in result.stdout
    shown = env.run("crew", "show", key).stdout
    assert "\\x1b[31m" in shown.split("Your note: ", 1)[1]
    for args in (("crew", "list", "--json"), ("crew", "show", "--json", key)):
        result = env.run(*args)
        assert result.stdout.isascii()
        value = json.loads(result.stdout)
        players = value.get("players") or [value.get("player")]
        assert players[0].get("key") == key and players[0].get("note") == note


def test_hostile_keys_in_an_error_are_escaped_constructed(make: Any) -> None:
    env = make(book({_hostile(KEY_A): player(), _hostile(KEY_A).lower(): player()}))
    result = env.run("crew", "show", _hostile(KEY_A).upper())
    assert result.exit_code == 2 and "matches 2 players" in result.stderr
    assert "\\x1b[31m" in result.stderr
    assert not _RAW.search(result.stderr.replace("\n", ""))


# ─── help, the UI, the command tree ──────────────────────────────────────────


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_crew_help_carries_the_sentence_of_term_2() -> None:
    result = runner.invoke(cli.app, ["crew", "--help"], terminal_width=80)
    assert result.exit_code == 0
    assert _flat(cli.CREW_BOOK_SENTENCE) in _flat(result.stdout)
    assert _flat(cli.CREW_BOOK_SENTENCE).startswith(
        "The crew book keeps, for each player you group with, their name, realm, guild, class,"
    )
    assert _flat(cli.CREW_BOOK_SENTENCE).endswith(
        "and someone else may later take a name you rated."
    )


@pytest.mark.parametrize("leaf", ["list", "show"])
def test_help_names_no_project_vocabulary(leaf: str) -> None:
    result = runner.invoke(cli.app, ["crew", leaf, "--help"], terminal_width=200)
    assert result.exit_code == 0
    assert not re.search(
        r"M1\d-\d+|ADR-\d+|\bL[1-8]\b|\[verify\]|docs/|Crew\w*Report", result.stdout
    )


def test_the_crew_commands_are_read_commands_in_the_ui() -> None:
    leaves = ui._read_tree(typer.main.get_command(cli.app))
    for path in (("crew", "list"), ("crew", "show")):
        leaf = leaves[path]
        assert leaf.excluded is None and leaf.confirm is None, path
        assert leaf.json_opt == "--json"


def test_the_command_reference_lists_both_with_their_models() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import gen_command_reference as gen

    named = {n: gen.json_models(c) for n, c in gen.iter_leaves(gen.root_command())}
    for leaf, model in (("list", cli.CrewReport), ("show", cli.CrewShowReport)):
        assert named.get(f"wowlab crew {leaf}") == [model.__name__]


def _child(code: str) -> Any:
    done = subprocess.run(
        [sys.executable, "-P", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


_LOADED = """
import json, sys
import typer
from wowlab_core import cli
from wowlab_core.cli import _common
root = typer.main.get_command(cli.app)
try:
    root.main({argv!r}, prog_name="wowlab", standalone_mode=False)
except SystemExit:
    pass
print(json.dumps([[n for n in _common._PART_NAMES if f"wowlab_core.cli.{{n}}" in sys.modules],
                  "wowlab_core.crewbook" in sys.modules]))
"""


def test_crew_loads_its_own_module_and_no_other_command_loads_the_reader() -> None:
    assert _child(_LOADED.format(argv=["crew", "list", "--help"])) == [
        ["_common", "_cmd_crew"],
        True,
    ]
    for argv in (
        ("install", "show", "--help"),
        ("char", "show", "--help"),
        ("--version",),
    ):
        assert _child(_LOADED.format(argv=argv))[1] is False, argv


def test_crewbook_reads_through_labaddon_root_anchored_read() -> None:
    assert crewbook.read_book.__module__ == "wowlab_core.crewbook"
    source = Path(crewbook.__file__).read_text(encoding="utf-8")
    assert "labaddon.read_file_bytes(path, root=root)" in source
    assert "open(" not in source
