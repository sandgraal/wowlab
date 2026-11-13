"""Every fenced `wowlab` command in README.md and docs/INSTALL.md runs (M16-20).

docs/LAB_PLAN.md §18.1 item 5: every command shown in the README and
INSTALL.md runs in CI against the fixture sandbox. The sandbox is
`test_cli_player_copy.sandbox`: the captured tree (`fixtures/macos/`) copied
into `tmp_path`, the user data directory redirected, no client running, and
wago.tools replayed from `fixtures/wago/` (ADR-0012). Nothing here reads or
writes a real install.

What the test reads from a document:

- A fenced block is a *shell block* when its language is a shell (`bash`,
  `sh`, `console`, `powershell`, ...), or when it is `text` or untagged and
  has a `$ ` prompt line. Every other block (`mermaid`, a folder layout) is
  not a command block.
- In a shell block without prompts every non-blank, non-comment line is a
  command (a trailing `\\` continues it). In a block with prompts the `$ `
  lines are commands and the lines under each are its shown output.
- A command *runs* when it is a `wowlab` command (`uv run wowlab` is the same
  CLI on the sandbox) and it exits on its own: `wowlab ui` and
  `wowlab log tail --follow` do not. Every command that runs must exit 0, and
  its shown output, when there is any, must match what it prints (stdout and
  stderr as a terminal shows them): a line that is only `…` stands for any
  number of lines the document trims, a `…` inside a line for any text, and
  every other line must be there as shown, in order, with no line in between
  that the document does not trim. The commands of one fenced block run in
  order in one fresh sandbox, as a player would type them.
- A block that holds a command that does not run (an install, the network, a
  command of another program, one that does not exit) is preceded by
  `<!-- not-run: <reason> -->`, the nearest non-blank line above its fence.
  The reason covers those commands only; the block's other `wowlab` commands
  still run. Every skipped command is listed in `SKIPS` below. A skip without
  a reason, a marker that no block follows, a marker on a block whose every
  command runs, a skipped command in a block with no marker, and a `wowlab`
  line in a block that is not a command block all fail.

Two placeholders stand for what differs between machines: `<ACCOUNT>` is the
account folder, and `/path/to/World of Warcraft` is the install folder. Both
are replaced by the sandbox's own before a command runs or its output is
compared. The sandbox's install is found as most players' is, at a default
location: `WOWLAB_WOW_ROOT` is unset and the default locations are the
sandbox's install folder (as `test_install.py` injects them).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from test_cli_player_copy import ACCOUNT, sandbox
from typer.testing import CliRunner

from wowlab_core import cli, install

ROOT = Path(__file__).resolve().parents[3]
DOCS = ("README.md", "docs/INSTALL.md")
TRIM = "…"
INSTALL_PLACEHOLDER = "/path/to/World of Warcraft"
SHELLS = frozenset({"bash", "sh", "shell", "zsh", "console", "powershell", "pwsh", "ps1"})
TEXTS = frozenset({"", "text"})
MARKER = re.compile(r"^<!--\s*not-run\b(?P<rest>.*?)-->$")
PROMPT = "$ "
WOWLAB_LINE = re.compile(r"^(uv run )?wowlab(\s|$)")

# Every command that does not run, by document, with the reason its block
# gives. A new skip is added here on purpose.
_FROM_SOURCE = "a source checkout, from the network"
_NO_EXIT = "`wowlab ui` does not exit"
_OWN_SHELL = "sets a variable on your own machine"
SKIPS: tuple[tuple[str, str, str], ...] = (
    (
        "README.md",
        "uv tool install --python 3.12 https://github.com/sandgraal/wowlab/releases/download/"
        "v0.1.0/wowlab_core-0.1.0-py3-none-any.whl#sha256="
        "a3c3bd86f98600f26db4cb10f69486cca7e1a62d032d0fe35b09c53fcc9be639",
        "an install, from the network",
    ),
    (
        "README.md",
        "wowlab ui            # the explorer in your browser: every file in your install, "
        "and whether it is safe to touch",
        _NO_EXIT,
    ),
    ("README.md", "wowlab ui", _NO_EXIT),
    ("README.md", "git clone https://github.com/sandgraal/wowlab && cd wowlab", _FROM_SOURCE),
    ("README.md", "make setup", _FROM_SOURCE),
    (
        "docs/INSTALL.md",
        "gh attestation verify wowlab_core-<version>-py3-none-any.whl "
        "--repo sandgraal/wowlab "
        "--signer-workflow sandgraal/wowlab/.github/workflows/release.yml "
        "--source-ref refs/tags/v<version>",
        "needs the network",
    ),
    (
        "docs/INSTALL.md",
        "uv tool install --python 3.12 ./wowlab_core-<version>-py3-none-any.whl",
        "an install, from the network",
    ),
    (  # M17-08: the Mac app's own attestation
        "docs/INSTALL.md",
        "gh attestation verify wowlab-<version>-macos-arm64.zip "
        "--repo sandgraal/wowlab "
        "--signer-workflow sandgraal/wowlab/.github/workflows/release.yml "
        "--source-ref refs/tags/v<version>",
        "needs the network",
    ),
    ("docs/INSTALL.md", "git clone https://github.com/sandgraal/wowlab", _FROM_SOURCE),
    ("docs/INSTALL.md", "cd wowlab", _FROM_SOURCE),
    ("docs/INSTALL.md", "uv sync --frozen", _FROM_SOURCE),
    (
        "docs/INSTALL.md",
        "find ~ -name .build.info -path '*World of Warcraft*' 2>/dev/null",
        "searches your own folders, not a wowlab command",
    ),
    ("docs/INSTALL.md", 'export WOWLAB_WOW_ROOT="/path/to/World of Warcraft"', _OWN_SHELL),
    ("docs/INSTALL.md", '$env:WOWLAB_WOW_ROOT = "D:\\Games\\World of Warcraft"', _OWN_SHELL),
    ("docs/INSTALL.md", 'setx WOWLAB_WOW_ROOT "D:\\Games\\World of Warcraft"', _OWN_SHELL),
    ("docs/INSTALL.md", "wowlab ui", _NO_EXIT),
)

# The commands that run, by document, as the document writes them.
RUNS: dict[str, tuple[str, ...]] = {
    "README.md": (
        "wowlab doctor        # finds your install; on a first run, says what wowlab will do "
        "and offers a first snapshot",
        "wowlab snap create   # snapshot your setup before you experiment",
        "wowlab explain WTF/Account/<ACCOUNT>/SavedVariables/DBM-Party-Vanilla.lua",
        "wowlab sv dump SavedVariables/DBM-StatusBarTimers.lua",
        "wowlab doctor",
        "uv run wowlab doctor        # finds your install and checks everything",
        "uv run wowlab --help        # every command",
    ),
    "docs/INSTALL.md": (
        "uv run wowlab doctor",
        'wowlab doctor --root "/path/to/World of Warcraft"',
    ),
}


@dataclass
class Command:
    line: int  # 1-based line of the command in the document
    text: str
    shown: list[str] | None = None  # the output the document shows; None when it shows none


@dataclass
class Block:
    """Commands of one fenced block that share a fate: all run (`skip` is
    None), or none does (`skip` is the marker's reason). A marked fence with
    commands of both kinds gives two of these, with the same `line`."""

    line: int  # 1-based line of the opening fence
    lang: str
    commands: list[Command] = field(default_factory=list)
    skip: str | None = None


@dataclass
class Read:
    blocks: list[Block]
    problems: list[str]


def _fence(stripped: str) -> str | None:
    m = re.match(r"^(`{3,}|~{3,})", stripped)
    return m.group(1) if m else None


def _commands(lang: str, start: int, body: list[str]) -> list[Command]:
    """The commands of a block's body (`start` is the body's first line)."""
    if any(line.startswith(PROMPT) for line in body):
        commands: list[Command] = []
        for i, line in enumerate(body):
            if line.startswith(PROMPT):
                commands.append(Command(start + i, line[len(PROMPT) :].strip(), []))
            elif commands:
                shown = commands[-1].shown
                assert shown is not None
                shown.append(line.rstrip())
        for command in commands:
            assert command.shown is not None
            while command.shown and not command.shown[-1]:
                command.shown.pop()
        return commands
    if lang not in SHELLS:
        return []
    commands = []
    pending: Command | None = None
    for i, line in enumerate(body):
        text = line.strip()
        if pending is not None:
            pending.text += " " + text.removesuffix("\\").strip()
            if not text.endswith("\\"):
                commands.append(pending)
                pending = None
            continue
        if not text or text.startswith("#"):
            continue
        if text.endswith("\\"):
            pending = Command(start + i, text.removesuffix("\\").strip())
        else:
            commands.append(Command(start + i, text))
    if pending is not None:
        commands.append(pending)
    return commands


def read_blocks(text: str) -> Read:
    """The command blocks of a Markdown document, and what is wrong with its markers."""
    lines = text.splitlines()
    blocks: list[Block] = []
    problems: list[str] = []
    claimed: set[int] = set()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        opener = _fence(stripped)
        if opener is None:
            i += 1
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        lang = stripped[len(opener) :].strip().split(" ")[0].lower()
        end = i + 1
        while end < len(lines) and not (
            lines[end].strip().startswith(opener) and lines[end].strip().strip(opener[0]) == ""
        ):
            end += 1
        body = [
            line[indent:] if not line[:indent].strip() else line.lstrip()
            for line in lines[i + 1 : end]
        ]
        commands = _commands(lang, i + 2, body)
        above = i - 1
        while above >= 0 and not lines[above].strip():
            above -= 1
        marker = MARKER.match(lines[above].strip()) if above >= 0 else None
        skip = None
        if marker:
            claimed.add(above)
            rest = marker.group("rest").strip()
            if not rest.startswith(":") or not rest[1:].strip():
                problems.append(f"line {above + 1}: a not-run marker without a reason")
            skip = rest[1:].strip() if rest.startswith(":") else ""
        if not commands and lang in TEXTS:
            strays = [n for n, line in enumerate(body) if WOWLAB_LINE.match(line.strip())]
            if strays:
                problems.append(
                    f"line {i + 2 + strays[0]}: a wowlab command in a block that is not read "
                    "as commands; fence it as bash, or prompt it with `$ `"
                )
        runs = [c for c in commands if _runs(c.text)]
        skipped = [c for c in commands if not _runs(c.text)]
        if runs:
            blocks.append(Block(i + 1, lang, runs, None))
        if skipped and skip is not None:
            blocks.append(Block(i + 1, lang, skipped, skip))
        elif skipped:
            problems.append(
                f"line {skipped[0].line}: {skipped[0].text!r} does not run here (an install, "
                "the network, another program, or a command that does not exit); "
                "mark the block <!-- not-run: <reason> -->"
            )
        elif marker and commands:
            problems.append(
                f"line {above + 1}: a not-run marker on a block whose every command runs"
            )
        elif marker:
            problems.append(f"line {above + 1}: a not-run marker above a block with no command")
        i = end + 1
    for n, line in enumerate(lines):
        if MARKER.match(line.strip()) and n not in claimed:
            problems.append(f"line {n + 1}: a not-run marker that no fenced block follows")
    return Read(blocks, problems)


def _uncomment(command: str) -> str:
    """`command` without its shell comment: a `#` that starts a word outside
    quotes, as the shell reads it. (`shlex.split(..., comments=True)` also
    cuts at a `#` inside a word, such as an account folder `90000001#6`, which
    the shell keeps.)"""
    quote = ""
    escaped = False
    word_start = True  # the next character starts a word
    for n, char in enumerate(command):
        at_start, word_start = word_start, False
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "#" and at_start:
            return command[:n]
        elif char.isspace():
            word_start = True
    return command


def _argv(command: str) -> list[str]:
    """A command's words, without its comment; `uv run wowlab` is `wowlab`."""
    words = shlex.split(_uncomment(command))
    if words[:2] == ["uv", "run"]:
        words = words[2:]
    return words


def _is_wowlab(command: str) -> bool:
    try:
        words = _argv(command)
    except ValueError:
        return False
    return bool(words) and words[0] == "wowlab"


def _runs(command: str) -> bool:
    """Whether a command runs on the sandbox: a wowlab command that exits."""
    if not _is_wowlab(command):
        return False
    words = _argv(command)[1:]
    never_exits = words[:1] == ["ui"] or (
        words[:2] == ["log", "tail"] and bool({"--follow", "-f"} & set(words))
    )
    return not never_exits


def _line_matches(shown: str, printed: str) -> bool:
    if TRIM not in shown:
        return shown == printed
    pattern = ".*".join(re.escape(part) for part in shown.split(TRIM))
    return re.fullmatch(pattern, printed) is not None


def _at(segment: list[str], printed: list[str], start: int) -> bool:
    return start + len(segment) <= len(printed) and all(
        _line_matches(s, p) for s, p in zip(segment, printed[start:], strict=False)
    )


def output_matches(shown: list[str], printed: list[str]) -> bool:
    """Whether `printed` is what `shown` shows, with `…` lines trimming any lines."""
    segments: list[list[str]] = [[]]
    for line in shown:
        if line == TRIM:
            segments.append([])
        else:
            segments[-1].append(line)
    if len(segments) == 1:
        return len(shown) == len(printed) and _at(shown, printed, 0)
    first, *middle, last = segments
    if not _at(first, printed, 0):
        return False
    cursor = len(first)
    for segment in middle:
        if not segment:
            continue
        found = next(
            (s for s in range(cursor, len(printed) - len(segment) + 1) if _at(segment, printed, s)),
            None,
        )
        if found is None:
            return False
        cursor = found + len(segment)
    tail = len(printed) - len(last)
    return tail >= cursor and _at(last, printed, tail)


def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ─── the documents ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", DOCS)
def test_markers_are_well_formed(name: str) -> None:
    assert read_blocks(_doc(name)).problems == []


def test_the_skips_are_listed() -> None:
    """Every command that does not run, with its reason; a new skip is a change here."""
    skips = [
        (name, command.text, block.skip or "")
        for name in DOCS
        for block in read_blocks(_doc(name)).blocks
        if block.skip is not None
        for command in block.commands
    ]
    assert skips == list(SKIPS)


@pytest.mark.parametrize("name", DOCS)
def test_every_example_runs_on_the_fixture_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    runner = CliRunner()
    ran: list[str] = []
    problems: list[str] = []
    for block in read_blocks(_doc(name)).blocks:
        if block.skip is not None:
            continue
        # Each block in a fresh sandbox, its install found at a default location.
        home = tmp_path / f"block-{block.line}"
        home.mkdir()
        root = sandbox(home, monkeypatch, "")
        monkeypatch.delenv(install.ENV_ROOT)
        monkeypatch.setattr(install, "default_roots", lambda *a, _r=root, **k: (_r,))
        places = {"<ACCOUNT>": ACCOUNT, INSTALL_PLACEHOLDER: str(root)}

        def fill(text: str, places: dict[str, str] = places) -> str:
            for placeholder, value in places.items():
                text = text.replace(placeholder, value)
            return text

        for command in block.commands:
            ran.append(command.text)
            args = [fill(word) for word in _argv(command.text)[1:]]
            result = runner.invoke(cli.app, args, terminal_width=80)
            where = f"{name}:{command.line}: {command.text}"
            if result.exit_code != 0:
                problems.append(f"{where}: exit {result.exit_code}\n{result.output}")
                continue
            if command.shown:
                printed = [line.rstrip() for line in result.output.splitlines()]
                while printed and not printed[-1]:
                    printed.pop()
                shown = [fill(line) for line in command.shown]
                if not output_matches(shown, printed):
                    problems.append(
                        f"{where}: the output no longer matches the shown lines\n"
                        + "shown:\n  "
                        + "\n  ".join(shown)
                        + "\nprinted:\n  "
                        + "\n  ".join(printed)
                    )
    assert problems == [], "\n\n".join(problems)
    assert tuple(ran) == RUNS[name]


# ─── the reader and the matcher, on constructed text ─────────────────────────
# Constructed inputs (L8 allows them for boundary tests): small Markdown and
# output written here to pin the rules, not captures.


def test_a_skip_without_a_reason_fails_constructed() -> None:
    for marker in ("<!-- not-run -->", "<!-- not-run: -->", "<!-- not-run:   -->"):
        doc = f"{marker}\n```bash\nwowlab ui\n```\n"
        assert read_blocks(doc).problems == ["line 1: a not-run marker without a reason"]


def test_a_marker_must_sit_on_a_block_with_a_skip_constructed() -> None:
    doc = (
        "<!-- not-run: stray -->\n\ntext\n\n"
        "<!-- not-run: no command -->\n```mermaid\nA --> B\n```\n"
        "<!-- not-run: all run -->\n```bash\nwowlab doctor\n```\n"
    )
    assert read_blocks(doc).problems == [
        "line 5: a not-run marker above a block with no command",
        "line 9: a not-run marker on a block whose every command runs",
        "line 1: a not-run marker that no fenced block follows",
    ]


def test_an_unmarked_skip_fails_constructed() -> None:
    for line in (
        "uv tool install wowlab",
        "wowlab ui",
        "wowlab log tail --follow",
        "wowlab log tail -f",
        "uv run wowlab log tail --json -f",
    ):
        doc = f"```bash\nwowlab doctor\n{line}\n```\n"
        (problem,) = read_blocks(doc).problems
        assert problem.startswith(f"line 3: {line!r} does not run here"), problem


def test_a_marked_block_still_runs_its_wowlab_commands_constructed() -> None:
    doc = (
        "<!-- not-run: a source checkout -->\n```bash\n"
        "git clone x\nuv run wowlab doctor\nwowlab ui\nwowlab snap creat\n```\n"
    )
    read = read_blocks(doc)
    assert read.problems == []
    runs, skipped = read.blocks
    assert (runs.skip, [c.text for c in runs.commands]) == (
        None,
        ["uv run wowlab doctor", "wowlab snap creat"],  # a typo runs, and fails there
    )
    assert (skipped.skip, [c.text for c in skipped.commands]) == (
        "a source checkout",
        ["git clone x", "wowlab ui"],
    )


def test_a_wowlab_line_outside_a_command_block_fails_constructed() -> None:
    for fence in ("```", "```text"):
        for line in ("wowlab doctor", "uv run wowlab --help"):
            doc = f"{fence}\nsome words\n{line}\n```\n"
            (problem,) = read_blocks(doc).problems
            assert problem.startswith("line 3: a wowlab command in a block that is not read")
    assert read_blocks("```\nlab/core/   wowlab-core: the library\n```\n").problems == []


def test_commands_and_shown_output_are_read_constructed() -> None:
    doc = (
        "  <!-- not-run: a reason -->\n"
        "  ```bash\n"
        "  wowlab ui   # a comment\n"
        "  gh x \\\n"
        "    --flag\n"
        "  ```\n"
        "```text\n"
        "$ wowlab explain 'a b#1'\n"
        "first\n"
        "…\n"
        "\n"
        "$ uv run wowlab doctor\n"
        "```\n"
        "```\n"
        "lab/   no prompt, not a command\n"
        "```\n"
    )
    read = read_blocks(doc)
    assert read.problems == []
    marked, shown = read.blocks
    assert marked.skip == "a reason"
    assert [c.text for c in marked.commands] == ["wowlab ui   # a comment", "gh x --flag"]
    assert [(c.line, c.text, c.shown) for c in shown.commands] == [
        (8, "wowlab explain 'a b#1'", ["first", TRIM]),
        (12, "uv run wowlab doctor", []),
    ]
    assert _argv("uv run wowlab doctor") == ["wowlab", "doctor"]


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("wowlab explain 'a b#1'  # note", ["wowlab", "explain", "a b#1"]),
        ('wowlab explain "#weird"', ["wowlab", "explain", "#weird"]),
        (
            "wowlab explain WTF/Account/90000001#6/x.lua",
            ["wowlab", "explain", "WTF/Account/90000001#6/x.lua"],
        ),
        ("wowlab doctor   # it's the first run", ["wowlab", "doctor"]),
        ("wowlab explain a\\ #b", ["wowlab", "explain", "a #b"]),
        ("# only a comment", []),
    ],
)
def test_comments_are_cut_as_the_shell_cuts_them_constructed(command: str, argv: list[str]) -> None:
    assert _argv(command) == argv


@pytest.mark.parametrize(
    ("shown", "printed", "matches"),
    [
        (["a", "b"], ["a", "b"], True),
        (["a", "b"], ["a", "b", "c"], False),  # an untrimmed extra line
        (["a", TRIM], ["a", "b", "c"], True),
        ([TRIM, "c"], ["a", "b", "c"], True),
        ([TRIM, "b", TRIM], ["a", "b", "c"], True),
        (["a", TRIM, "c"], ["a", "x", "y", "c"], True),
        (["a", "c", TRIM], ["a", "b", "c"], False),  # shown lines are contiguous
        (["b", TRIM], ["a", "b"], False),  # no leading … anchors the start
        ([TRIM, "c", TRIM, "a"], ["a", "c"], False),  # order counts
        (["Install: … (found by …)"], ["Install: /x y (found by --root)"], True),
        (["Install: … (found by …)"], ["Install: /x y"], False),
        (["[x] … ."], ["[x] anything ."], True),  # the rest of the line is literal
    ],
)
def test_trimmed_output_matching_constructed(
    shown: list[str], printed: list[str], matches: bool
) -> None:
    assert output_matches(shown, printed) is matches
