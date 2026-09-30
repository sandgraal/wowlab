"""Grades for `scripts/gen_command_reference.py` (M12-13).

The page is built from the current `wowlab` app. The Click tree is walked
here independently of the script, so a leaf the script skips or repeats is
caught rather than agreed with. Inputs named `constructed` are invented
parameter objects for the default-rendering boundary cases; everything else
is the real app.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import typer
from pydantic import BaseModel

from wowlab_core import cli

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_command_reference.py"

# The commands with a `--yes` option (ticket M12-13, amended 2026-09-30).
ASKS_FIRST = {
    "wowlab snap restore",
    "wowlab undo",
    "wowlab profile apply",
    "wowlab addon install",
    "wowlab addon remove",
    "wowlab sv merge",
    "wowlab snap gc",
}

ASKS_MARKER = "**Asks before changing files.**"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_command_reference", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load()


def _tree_leaves() -> dict[str, Any]:
    """Every leaf of the Click tree by full name, walked without the script."""
    leaves: dict[str, Any] = {}

    def walk(cmd: Any, path: str) -> None:
        subs = getattr(cmd, "commands", None)
        if isinstance(subs, dict):
            for name, sub in subs.items():
                walk(sub, f"{path} {name}")
        else:
            leaves[path] = cmd

    walk(typer.main.get_command(cli.app), "wowlab")
    return leaves


def _sections(page: str) -> dict[str, str]:
    """Each `### ` section of the page by its heading, up to the next heading."""
    out: dict[str, str] = {}
    for chunk in re.split(r"^(?=#{2,3} )", page, flags=re.MULTILINE):
        if chunk.startswith("### "):
            heading, _, body = chunk.partition("\n")
            out[heading[4:].strip()] = body
    return out


@pytest.fixture(scope="module")
def page() -> str:
    rendered: str = gen.render()
    return rendered


def test_every_leaf_appears_exactly_once_under_its_full_name(page: str) -> None:
    leaves = _tree_leaves()
    # The tree is really walked: top-level leaves and group leaves are both there.
    assert {"wowlab doctor", "wowlab undo", "wowlab looks import-char"} | ASKS_FIRST <= set(leaves)
    assert len(leaves) >= 30
    headings = re.findall(r"^### (.+)$", page, flags=re.MULTILINE)
    for name in leaves:
        assert headings.count(name) == 1, name
    assert sorted(headings) == sorted(leaves)


def test_every_group_has_a_section(page: str) -> None:
    root = typer.main.get_command(cli.app)
    groups = ["wowlab"] + [
        f"wowlab {n}"
        for n, c in root.commands.items()
        if isinstance(getattr(c, "commands", None), dict)
    ]
    headings = re.findall(r"^## (.+)$", page, flags=re.MULTILINE)
    assert sorted(headings) == sorted(groups)


def test_exactly_the_yes_commands_are_marked_as_asking_first(page: str) -> None:
    with_yes = {
        name for name, cmd in _tree_leaves().items() if any("--yes" in p.opts for p in cmd.params)
    }
    assert with_yes == ASKS_FIRST
    marked = {name for name, body in _sections(page).items() if ASKS_MARKER in body}
    assert marked == ASKS_FIRST
    assert page.count(ASKS_MARKER) == len(ASKS_FIRST)


def test_asking_first_is_not_described_as_writing_into_an_install(page: str) -> None:
    # `snap gc` asks first but changes the Lab's snapshot store, not an install.
    marker_lines = {ln for ln in page.splitlines() if ln.startswith(ASKS_MARKER)}
    assert len(marker_lines) == 1
    (marker_line,) = marker_lines
    assert "install" not in marker_line
    assert "write gate" not in marker_line
    assert ASKS_MARKER in _sections(page)["wowlab snap gc"]
    intro = page.split("\n## ", 1)[0]
    assert "snapshot store" in intro
    assert "writes into an install" not in intro
    assert not re.search(r"--yes`?[^.\n]*(writes|write) into an install", page)


def test_a_second_run_gives_identical_bytes(tmp_path: Path) -> None:
    first, second = tmp_path / "one.md", tmp_path / "two.md"
    assert gen.main(["--out", str(first)]) == 0
    assert gen.main(["--out", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == gen.render().encode("utf-8")
    assert b"\r" not in first.read_bytes()


@pytest.mark.parametrize("columns", ["20", "80", "400"])
def test_terminal_width_does_not_change_the_page(
    page: str, columns: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", columns)
    monkeypatch.setenv("TERM", "dumb")
    assert gen.render() == page


def test_check_passes_on_a_fresh_page(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "Command-Reference.md"
    assert gen.main(["--out", str(out)]) == 0
    capsys.readouterr()
    assert gen.main(["--check", str(out)]) == 0


def test_check_fails_with_a_diff_on_a_page_with_one_command_removed(
    page: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    removed = re.sub(
        r"^### wowlab snap gc\n.*?(?=^#{2,3} )", "", page, count=1, flags=re.MULTILINE | re.DOTALL
    )
    assert removed != page and "### wowlab snap gc" not in removed
    stale = tmp_path / "Command-Reference.md"
    stale.write_bytes(removed.encode("utf-8"))
    capsys.readouterr()
    assert gen.main(["--check", str(stale)]) == 1
    out = capsys.readouterr().out
    assert "+### wowlab snap gc" in out
    assert out.startswith("--- ")


def test_check_fails_on_a_missing_page(tmp_path: Path) -> None:
    assert gen.main(["--check", str(tmp_path / "absent.md")]) == 1


def test_every_json_command_names_a_model_that_resolves(page: str) -> None:
    for name, cmd in _tree_leaves().items():
        if not any("--json" in p.opts for p in cmd.params):
            continue
        models = gen.json_models(cmd)
        assert models, name
        for model in models:
            resolved = gen._resolve_model(model)
            assert isinstance(resolved, type) and issubclass(resolved, BaseModel), (name, model)
        assert (
            "**`--json` output:** " + ", ".join(f"`{m}`" for m in models) + "."
            in (_sections(page)[name])
        )


def test_json_models_follow_the_help_sentence() -> None:
    leaves = _tree_leaves()
    assert gen.json_models(leaves["wowlab snap gc"]) == ["snapshot.GcReport"]
    assert gen.json_models(leaves["wowlab looks show"]) == ["LookReport", "LooksListReport"]
    assert gen.json_models(leaves["wowlab log tail"]) == ["LogTailReport", "LogTailLine"]
    assert gen.json_models(leaves["wowlab snap show"]) == ["snapshot.Manifest"]
    assert gen.json_models(leaves["wowlab install show"]) == ["install.Install"]


def test_arguments_and_options_are_listed_with_type_default_and_help(page: str) -> None:
    sections = _sections(page)
    tail = sections["wowlab log tail"]
    assert "| `--lines`, `-n` | integer, 0 to 100000 | `10` | How many of the last lines" in tail
    assert "| `--follow`, `-f` | flag | off |" in tail
    cvar = sections["wowlab cvar get"]
    assert "| `NAME` | text | required | CVar name; compared without case. |" in cvar
    assert "| `--scope` | one of `global`, `account`, `character` | `global` |" in cvar
    restore = sections["wowlab snap restore"]
    assert "Usage: `wowlab snap restore [OPTIONS] ID`" in restore
    assert "| `--paths` | text, repeatable | none |" in restore
    assert "| `--root` | path | none |" in restore
    looks = sections["wowlab looks save"]
    assert "| `--race` | text | required |" in looks


def test_help_text_is_reflowed_one_paragraph_per_line(page: str) -> None:
    restore = _sections(page)["wowlab snap restore"]
    assert (
        "Put back files from a snapshot, through the write gate: the client must be "
        "closed, a pre-write snapshot is taken first, and `wowlab undo` reverses it.\n"
    ) in restore


def test_page_names_no_path_from_this_machine(page: str) -> None:
    assert str(Path.home()) not in page
    assert str(Path(__file__).resolve().parents[2]) not in page
    assert not re.search(r"(/Users/|/home/|[A-Za-z]:\\\\)", page)


# ─── constructed: default rendering boundary cases ──────────────────────────


def _param(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "required": False,
        "envvar": None,
        "show_default": None,
        "is_flag": False,
        "count": False,
        "default": None,
        "multiple": False,
        "nargs": 1,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_constructed_machine_specific_defaults_are_rendered_generically() -> None:
    home = Path.home()
    assert gen._render_default(_param(default=home / "wow")) == "depends on the machine"
    assert gen._render_default(_param(default=str(home))) == "depends on the machine"
    assert gen._render_default(_param(default="/opt/wow")) == "depends on the machine"
    assert gen._render_default(_param(default="C:\\Games\\wow")) == "depends on the machine"
    assert gen._render_default(_param(default="~/wow")) == "depends on the machine"
    assert gen._render_default(_param(default=lambda: "x")) == "computed at run time"
    assert gen._render_default(_param(default=Path("pages"))) == "`pages`"


def test_constructed_env_defaults_name_the_variable_not_its_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WOWLAB_CONSTRUCTED", "/somewhere/real")
    rendered = gen._render_default(_param(envvar="WOWLAB_CONSTRUCTED"))
    assert rendered == "`$WOWLAB_CONSTRUCTED`, else none"
    assert "/somewhere" not in rendered


def test_constructed_markup_in_help_is_escaped_outside_code_spans() -> None:
    assert gen._prose("a *-cache* file and <Character>") == (
        "a \\*-cache\\* file and \\<Character\\>"
    )
    assert gen._prose("keep `a|b *c*` as code") == "keep `a|b *c*` as code"
    assert gen._cell("x | `a|b`") == "x \\| `a\\|b`"
    assert gen._prose("# not a heading") == "\\# not a heading"


def test_script_reads_only_the_package() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"typer", "pydantic", "wowlab_core"}
    assert not imported & {
        "os",
        "shutil",
        "tempfile",
        "subprocess",
        "socket",
        "urllib",
        "http",
        "httpx",
        "platformdirs",
    }
