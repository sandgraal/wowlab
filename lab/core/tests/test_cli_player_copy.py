"""The player-output lint (M16-15, docs/LAB_PLAN.md §18.1 item 4, §6.11).

Everything a player reads from `wowlab` is checked here: every `--help`
screen (and its `-h` twin), and the text output, stdout and stderr, of the
read commands run on the fixture sandbox. That text carries no ticket id,
ADR id, invariant label (`L1`..`L8`), doc path or section sign, `[verify]`
marker, module path, constant name or model class name
(`.claude/rules/player-copy.md`). "(not yet confirmed)", which stands in for
`[verify]`, appears at most once in one report. `--json` is an interface and
is not linted.

The sandbox is the one `docs/handoffs/outside-evaluation-2026-10/_make_sandbox.sh`
builds: the captured tree (`fixtures/macos/`) copied into `tmp_path` with its
flavor folder named as its provenance row says (`_classic_beta_`), optionally
with one capture overlaid on it (`macos-70450`, `macos-70058`). Nothing here
reads or writes a real install: `WOWLAB_WOW_ROOT` names the copy, the user
data directory is redirected into `tmp_path`, `psutil.process_iter` is a fake
table with no client in it, and wago.tools is replayed from `fixtures/wago/`
(ADR-0012).

The allowlist: environment variable names the player sets, and the game's
own words. A word in UPPER_SNAKE case that occurs in the sandbox's own files
or in the recorded tables (a combat-log event, a binding name) is the game's,
not ours, and is admitted.
"""

from __future__ import annotations

import gzip
import importlib
import inspect
import pkgutil
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import platformdirs
import psutil
import pytest
import typer
from pydantic import BaseModel
from typer.testing import CliRunner

import wowlab_core
from wowlab_core import cli, install
from wowlab_core.gamedata import GameData, TableNotPublished

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WAGO = FIXTURES / "wago"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
TABLE_BUILD = "1.60.1.70009"  # the recorded customization tables
OVERLAYS = ("", "macos-70450", "macos-70058")

runner = CliRunner()

# Environment variables the player sets, named in help on purpose.
ENV_NAMES = frozenset({"WOWLAB_WOW_ROOT", "NO_COLOR"})


# ─── the patterns ────────────────────────────────────────────────────────────


def _modules() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules(wowlab_core.__path__))


def _model_names() -> frozenset[str]:
    """Every Pydantic model class defined in the package, by name, when the
    name is CamelCase with two humps or more (`DoctorReport`): a one-word
    name (`Install`, `Manifest`) is an ordinary English word."""
    names: set[str] = set()
    for mod in _modules():
        module = importlib.import_module(f"wowlab_core.{mod}")
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj.__module__.startswith("wowlab_core")
                and re.fullmatch(r"_?[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+", name)
            ):
                names.add(name.lstrip("_"))
    return frozenset(names)


UPPER_SNAKE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
CONFIRM = "(not yet confirmed)"


def _patterns() -> dict[str, re.Pattern[str]]:
    modules = "|".join(re.escape(m) for m in _modules())
    models = "|".join(sorted(_model_names(), key=len, reverse=True))
    return {
        "ticket id": re.compile(r"\bM1\d-\d+"),
        "ADR id": re.compile(r"\bADR-\d+"),
        "[verify]": re.compile(r"\[verify\]", re.IGNORECASE),
        "doc path": re.compile(r"LAB_PLAN|LAB_FORMATS|LAB_FILE_MAP|LAB_IDEAS|docs/|§\s?\d"),
        "invariant": re.compile(r"\bL[1-8]\b"),
        "module path": re.compile(
            rf"\bwowlab_core\.\w|\b(?:{modules})\.[A-Za-z_]\w*\(|`(?:{modules})`"
        ),
        "qualified name": re.compile(rf"\b(?:{modules})\.[A-Z]\w*"),
        "model class": re.compile(rf"\b(?:{models})\b"),
    }


PATTERNS = _patterns()


@dataclass(frozen=True)
class Install:
    """What the sandbox itself says: text printed as it was read from it is
    the install's, not wowlab's, and is not linted.

    `words`: the UPPER_SNAKE words of its files and of the recorded tables (a
    combat-log event, a table's column). `text`: its files, decoded, so that
    a hit whose surrounding text occurs verbatim in a file (a value `sv dump`
    prints, a reason the lab-addon wrote) is known to be the file's."""

    words: frozenset[str] = frozenset()
    text: str = ""

    @classmethod
    def of(cls, root: Path) -> Install:
        words: set[str] = set()
        texts: list[str] = []
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            text = path.read_bytes().decode("utf-8", "replace")
            texts.append(text)
            words.update(UPPER_SNAKE.findall(text))
        for path in sorted(p for p in WAGO.iterdir() if p.is_file()):
            data = path.read_bytes()
            if path.suffix == ".gz":
                data = gzip.decompress(data)
            words.update(UPPER_SNAKE.findall(data.decode("utf-8", "replace")))
        return cls(frozenset(words), "\n".join(texts))

    def says(self, line: str, start: int, end: int) -> bool:
        """Whether the text around `line[start:end]` occurs in a file."""
        context = line[max(0, start - 16) : end + 16]
        return bool(self.text) and context in self.text


def hits(text: str, install: Install | None = None) -> list[str]:
    """Every rule `text` breaks, as "<rule>: <the match in its line>"."""
    install = install or Install()
    found: list[str] = []
    for line in text.splitlines():
        shown = repr(line.strip()[:160])
        for rule, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                if not install.says(line, m.start(), m.end()):
                    found.append(f"{rule}: {m.group()!r} in {shown}")
        for m in UPPER_SNAKE.finditer(line):
            word = m.group()
            if word in ENV_NAMES or word in install.words:
                continue
            if not install.says(line, m.start(), m.end()):
                found.append(f"UPPER_SNAKE: {word!r} in {shown}")
    if text.count(CONFIRM) > 1:
        found.append(f"{CONFIRM!r} printed {text.count(CONFIRM)} times")
    return found


# ─── the help screens ────────────────────────────────────────────────────────


def _root() -> Any:
    return typer.main.get_command(cli.app)


def _walk(cmd: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    yield path, cmd
    for name in sorted(getattr(cmd, "commands", None) or {}):
        yield from _walk(cmd.commands[name], (*path, name))


COMMANDS = list(_walk(_root()))


@pytest.mark.parametrize(
    ("path", "cmd"), COMMANDS, ids=[" ".join(p) or "wowlab" for p, _ in COMMANDS]
)
def test_help_screen_is_player_copy(path: tuple[str, ...], cmd: Any) -> None:
    long = runner.invoke(cli.app, [*path, "--help"], terminal_width=80)
    short = runner.invoke(cli.app, [*path, "-h"], terminal_width=80)
    assert long.exit_code == 0, (long.stdout, long.stderr)
    assert short.exit_code == 0, (short.stdout, short.stderr)
    assert short.stdout == long.stdout
    assert hits(long.stdout) == []


def test_lint_catches_what_it_is_for_constructed() -> None:
    """Constructed lines, one per rule: the lint is not vacuous."""
    for line in (
        "see M13-09",
        "(ADR-0029)",
        "a guess [verify]",
        "docs/LAB_PLAN.md",
        "§13.4",
        "out of scope (L7)",
        "wowlab_core.guard",
        "`guard.undo(expected_id=...)`",
        "JSON: snapshot.Manifest.",
        "JSON: DoctorReport.",
        "(GC_GRACE_SECONDS)",
        f"{CONFIRM} and again {CONFIRM}",
    ):
        assert hits(line), line
    for line in (
        "Set WOWLAB_WOW_ROOT or NO_COLOR.",
        "Config.wtf, WowLab.lua and SavedVariables",
        "Level 15, version 1.60.1.70450",
        f"one {CONFIRM}",
    ):
        assert hits(line) == [], line


@pytest.mark.parametrize(("path", "cmd"), [(p, c) for p, c in COMMANDS if p])
def test_every_command_has_a_short_help_that_fits(path: tuple[str, ...], cmd: Any) -> None:
    """Each command's line in its group's listing fits in 80 columns, uncut."""
    assert cmd.short_help, f"{' '.join(path)}: no short_help"
    parent = _root()
    for name in path[:-1]:
        parent = parent.commands[name]
    widest = min(max(len(n) for n in parent.commands), 30)
    assert 2 + widest + 2 + len(cmd.short_help) <= 80, (path, cmd.short_help)
    assert not cmd.short_help.endswith("...")
    assert hits(cmd.short_help) == []


# ─── the read commands on the sandbox ────────────────────────────────────────


class _Source:
    """wago.tools as recorded: the builds listing and the tables of fixtures/wago."""

    name = "fixtures"

    def fetch_builds(self, dest: BinaryIO) -> str:
        dest.write(gzip.decompress((WAGO / "builds.json.gz").read_bytes()))
        return "fixture:builds.json.gz"

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        plain = WAGO / f"{table}.{build}.csv"
        packed = WAGO / f"{table}.{build}.csv.gz"
        if plain.exists():
            dest.write(plain.read_bytes())
        elif packed.exists():
            dest.write(gzip.decompress(packed.read_bytes()))
        else:
            raise TableNotPublished(table, build)
        return f"fixture:{table}.{build}.csv"


class _NoClient:
    """A process that is not the client."""

    def __init__(self, exe: str) -> None:
        self._exe = exe

    pid = 101

    def name(self) -> str:
        return "python3"

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overlay: str) -> Path:
    """The fixture sandbox, as `_make_sandbox.sh <name> [overlay]` builds it."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    if overlay:
        if (FIXTURES / overlay / ".build.info").is_file():
            shutil.copy2(FIXTURES / overlay / ".build.info", root / ".build.info")
        shutil.copytree(FIXTURES / overlay / "forever", root / FLAVOR, dirs_exist_ok=True)
    user = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: user)
    process = _NoClient(str(tmp_path / "elsewhere" / "python3"))
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([process]))

    @contextmanager
    def replayed() -> Iterator[GameData]:
        yield GameData(_Source(), cache_dir=user / "gamedata")

    monkeypatch.setattr(cli, "_open_gamedata", replayed)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.chdir(tmp_path)
    return root


def _characters(flavor: Path) -> list[str]:
    account = flavor / "WTF" / "Account" / ACCOUNT
    return sorted(
        f"{realm.name}/{char.name}"
        for realm in account.iterdir()
        if realm.is_dir() and realm.name != "SavedVariables"
        for char in realm.iterdir()
        if char.is_dir()
    )


def read_commands(root: Path, tmp_path: Path) -> list[list[str]]:
    """The read commands of every leaf, with the arguments the sandbox offers.

    Commands that change the snapshot store or the user data directory
    (`snap create`, `profile save`, `looks save`) run first, so that the read
    commands after them have something to show; none of them touches the
    install."""
    flavor = root / FLAVOR
    sv_files = sorted(
        p.relative_to(flavor).as_posix() for p in (flavor / "WTF").rglob("*.lua") if p.is_file()
    )
    every_path = sorted(
        p.relative_to(flavor).as_posix() for p in flavor.rglob("*") if not p.is_symlink()
    )
    chars = _characters(flavor)
    cmds: list[list[str]] = [
        ["doctor", "--offline"],
        ["doctor"],
        ["install", "show"],
        ["snap", "create", "-m", "first"],
        ["snap", "create", "-m", "second"],
        ["snap", "list"],
        ["snap", "show", "{A}"],
        ["snap", "verify"],
        ["profile", "save", "mine", "--preset", "ui"],
        ["profile", "list"],
        ["profile", "show", "mine"],
        ["tree"],
        ["tree", "--explain"],
        ["tree", "--install"],
        ["tree", "WTF", "--explain"],
        ["filemap", "gaps"],
        ["explain", "."],
        *[["explain", p] for p in every_path],
        ["explain", "Data"],
        ["explain", "Cache"],
        ["explain", "Screenshots"],
        ["sv", "list"],
        *[["sv", "dump", p] for p in sv_files],
        ["cvar", "list"],
        ["cvar", "list", "--scope", "account"],
        ["cvar", "get", "synchronizeBindings"],
        ["binds", "list"],
        ["binds", "coach"],
        ["macros", "list"],
        ["macros", "check"],
        ["addons", "list"],
        ["log", "tail"],
        ["char", "list"],
        ["char", "show"],
        ["char", "gear"],
        ["char", "binds"],
        ["char", "macros"],
        *[
            [*cmd, "--character", c]
            for c in chars
            for cmd in (
                ["char", "show"],
                ["char", "gear"],
                ["char", "binds"],
                ["char", "macros"],
                ["binds", "list"],
                ["macros", "list"],
                ["macros", "check"],
                ["binds", "coach"],
            )
        ],
        ["db2", "builds"],
        ["db2", "fetch", "ChrClasses", "--build", TABLE_BUILD],
        ["db2", "head", "ChrClasses", "--build", TABLE_BUILD],
        ["looks", "races", "--build", TABLE_BUILD],
        ["looks", "options", "human", "--build", TABLE_BUILD],
        ["looks", "options", "human", "--sex", "0", "--build", TABLE_BUILD],
        ["looks", "save", "plain", "--race", "human", "--sex", "0", "--build", TABLE_BUILD],
        ["looks", "save", "other", "--race", "dwarf", "--sex", "1", "--build", TABLE_BUILD],
        ["looks", "show", "--build", TABLE_BUILD],
        ["looks", "show", "plain", "--build", TABLE_BUILD],
        ["looks", "compare", "plain", "other", "--build", TABLE_BUILD],
        ["looks", "page", "--out", str(tmp_path / "looks.html"), "--build", TABLE_BUILD],
        *[
            ["looks", "import-char", f"char{i}", "--character", c, "--build", TABLE_BUILD]
            for i, c in enumerate(chars)
        ],
        ["snap", "diff", "{A}", "{B}"],
    ]
    return cmds


# Leaves that are not run here, and why: each changes an install (through the
# write gate) or runs a server, or removes from the store.
NOT_RUN = {
    ("addon", "install"): "writes into the install",
    ("addon", "remove"): "writes into the install",
    ("profile", "apply"): "writes into the install",
    ("profile", "delete"): "removes from the store",
    ("snap", "restore"): "writes into the install",
    ("snap", "gc"): "removes from the store",
    ("sv", "merge"): "writes into the install",
    ("undo",): "writes into the install",
    ("ui",): "runs a local server",
}


def _leaf(args: list[str]) -> tuple[str, ...]:
    """The command path `args` names (its leading words that are commands)."""
    cmd, path = _root(), []
    for word in args:
        commands = getattr(cmd, "commands", None)
        if not commands or word not in commands:
            break
        cmd = commands[word]
        path.append(word)
    return tuple(path)


def _snapshot_ids() -> list[str]:
    result = runner.invoke(cli.app, ["snap", "list", "--json"])
    report = cli.SnapListReport.model_validate_json(result.stdout)
    return sorted(s.id for s in report.snapshots)


def run_read_commands(root: Path, tmp_path: Path) -> Iterator[tuple[list[str], Any]]:
    """Each read command, run, with its result; snapshot ids filled in."""
    ids: dict[str, str] = {}
    for args in read_commands(root, tmp_path):
        if any(a in ("{A}", "{B}") for a in args):
            if not ids:
                first, *_, last = _snapshot_ids()
                ids = {"{A}": first, "{B}": last}
            args = [ids.get(a, a) for a in args]
        yield args, runner.invoke(cli.app, args, terminal_width=80)


@pytest.mark.parametrize("overlay", OVERLAYS, ids=[o or "macos" for o in OVERLAYS])
def test_read_output_is_player_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overlay: str
) -> None:
    root = sandbox(tmp_path, monkeypatch, overlay)
    install_says = Install.of(root)
    problems: list[str] = []
    ran: set[tuple[str, ...]] = set()
    for args, result in run_read_commands(root, tmp_path):
        ran.add(_leaf(args))
        if result.exception is not None and not isinstance(result.exception, SystemExit):
            problems.append(f"wowlab {' '.join(args)}: raised {result.exception!r}")
        for stream, text in (("stdout", result.stdout), ("stderr", result.stderr)):
            problems += [
                f"wowlab {' '.join(args)} ({stream}): {h}" for h in hits(text, install_says)
            ]
    assert problems == [], "\n".join(problems)
    leaves = {p for p, c in COMMANDS if p and not hasattr(c, "commands")}
    assert leaves - ran - set(NOT_RUN) == set(), "a leaf is neither run nor named in NOT_RUN"


# ─── the renderings ──────────────────────────────────────────────────────────


def test_verify_reads_not_yet_confirmed_once_per_run_constructed() -> None:
    """Constructed lines: the first marker of a run is said in words, any
    later one is left out, and pointers into the spec are left out."""
    cli._new_report()
    assert cli._player_copy("a guess [verify]; another **[verify]**.") == (
        f"a guess {CONFIRM}; another."
    )
    assert cli._player_copy("(the realm's id ([verify]))") == "(the realm's id)"
    assert cli._player_copy("crashed once (M11-03); out of scope (LAB_PLAN L7)") == (
        "crashed once; out of scope"
    )
    assert cli._player_copy("moves (GLOSSARY) and (docs/LAB_FORMATS.md, 2026-10-16)") == (
        "moves and"
    )
    assert cli._player_copy("/run print(1) (keeps ()) [x]") == "/run print(1) (keeps ()) [x]"
    cli._new_report()
    assert cli._player_copy("[verify]") == CONFIRM
    # A later marker the sentence needs reads in words; a quoted value is kept.
    assert cli._player_copy("whether it counts bytes is [verify].") == (
        "whether it counts bytes is not yet confirmed."
    )
    assert cli._player_copy("for Retail, [verify] on Forever") == (
        "for Retail, not yet confirmed on Forever"
    )
    assert cli._player_copy("the record has 'Spell [verify] (L3)' [verify]") == (
        "the record has 'Spell [verify] (L3)'"
    )
    cli._new_report(json_out=True)
    assert cli._player_copy("(L3) [verify]") == "(L3) [verify]"
    cli._new_report()


REFUSED = {
    "bom": (b"\xef\xbb\xbfX = 1\n", "starts with a UTF-8 byte-order mark"),
    "cut": (b'X = {\n  ["a"] = ', "the file ends early, at line 2"),
    "function": (b"X = function() end\n", "it holds a function, which saved data never does"),
    "call": (b"X = print(1)\n", "it holds a function call"),
}


@pytest.mark.parametrize("case", sorted(REFUSED), ids=[f"{c}-constructed" for c in sorted(REFUSED)])
def test_lua_refusals_are_plain_words_and_json_keeps_the_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Constructed (hostile input, L8): a refused SavedVariables file is
    explained in words, with no rule id, no spec section and no Python byte
    literal; with --json, stderr keeps the parser's own message, rule and
    offending bytes included."""
    root = sandbox(tmp_path, monkeypatch, "")
    data, words = REFUSED[case]
    target = root / FLAVOR / "WTF" / "Account" / ACCOUNT / "SavedVariables" / "Refused.lua"
    target.write_bytes(data)
    rel = target.relative_to(root / FLAVOR).as_posix()
    text = runner.invoke(cli.app, ["sv", "dump", rel])
    assert text.exit_code == 1
    assert words in text.stderr
    assert "(at b'" not in text.stderr
    assert hits(text.stderr) == []
    as_json = runner.invoke(cli.app, ["sv", "dump", rel, "--json"])
    assert as_json.exit_code == 1
    assert "not SavedVariables data the parser accepts: line " in as_json.stderr
    assert "(at b'" in as_json.stderr


def test_rule_ids_survive_in_json_runs_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: the parser's rule id is in the --json run's message only."""
    root = sandbox(tmp_path, monkeypatch, "")
    target = root / FLAVOR / "WTF" / "Account" / ACCOUNT / "SavedVariables" / "Refused.lua"
    target.write_bytes(b"X = function() end\n")
    rel = target.relative_to(root / FLAVOR).as_posix()
    assert "(L3)" in runner.invoke(cli.app, ["sv", "dump", rel, "--json"]).stderr
    assert "(L3)" not in runner.invoke(cli.app, ["sv", "dump", rel]).stderr


def test_json_commands_end_their_help_with_the_one_footer() -> None:
    """ "JSON: <model>" left the help screens for one shared footer."""
    for path, cmd in COMMANDS:
        if any("--json" in getattr(p, "opts", ()) for p in cmd.params):
            out = runner.invoke(cli.app, [*path, "--help"], terminal_width=200).stdout
            assert out.rstrip().endswith(cli.JSON_FOOTER), path
            assert "JSON:" not in out, path
