"""Grades for `scripts/check_wiki.py`, the daily wiki job's gate (M12-14).

Two kinds of input:

- `constructed`: a small wiki built in `tmp_path` from the current Click tree
  (a page naming every leaf command and every public module, plus the
  generated `CLI-Reference.md`), which passes; each test breaks one thing and
  asserts the matching failure. The names in them (`Brakka-Stonewhisper`,
  `/Users/someone`) are invented.
- `real`: `fixtures/wiki-d4344c8/`, a byte-for-byte copy of every page of
  https://github.com/sandgraal/wowlab.wiki at commit d4344c8 (2026-09-29, the
  wiki before this job existed), cloned read-only on 2026-09-30. It is public
  text the owner wrote; the checks it proves are the ones that must not fire
  on real prose (links, personal data, files, commands).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
REAL_WIKI = Path(__file__).resolve().parent / "fixtures" / "wiki-d4344c8"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cw = _load("check_wiki")
gen = _load("gen_command_reference")


# ─── a constructed wiki that passes ──────────────────────────────────────────


def _leaves() -> list[str]:
    return [name for name, _ in gen.iter_leaves(gen.root_command())]


def _index_page(skip_command: str | None = None, skip_module: str | None = None) -> str:
    lines = ["# Index", "", "Every command:", ""]
    lines += [f"- `{name}`" for name in _leaves() if name != skip_command]
    lines += ["", "Every module:", ""]
    lines += [f"- `wowlab_core.{m}`" for m in cw.public_modules() if m != skip_module]
    return "\n".join(lines) + "\n"


def _build(root: Path, **pages: str) -> Path:
    """constructed: Home, Getting-Started, Command-Reference, Index, _Sidebar, the generated page."""
    root.mkdir(parents=True, exist_ok=True)
    base = {
        "Home.md": "# Home\n\nStart at [the index](Index) or [Getting Started](Getting-Started#install).\n",
        "Getting-Started.md": "# Getting Started\n\n## Install\n\nRun `uv run wowlab doctor --offline`.\n",
        "_Sidebar.md": "- [Home](Home)\n- [Index](Index)\n- [CLI Reference](CLI-Reference)\n",
        "Command-Reference.md": "# Command Reference\n\nThe overview; [CLI Reference](CLI-Reference).\n",
        "Index.md": _index_page(),
        "CLI-Reference.md": gen.render(),
    }
    base.update({k.replace("__", "-") + ".md": v for k, v in pages.items()})
    for name, text in base.items():
        (root / name).write_bytes(text.encode("utf-8"))
    return root


def _failures(root: Path, base: str | None = None) -> list[str]:
    return [str(f) for f in cw.check(root, base).failures]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_all(root: Path) -> str:
    if not (root / ".git").exists():
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "test@invalid")
        _git(root, "config", "user.name", "test")
        _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "HEAD")


def test_constructed_wiki_passes(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    assert _failures(root) == []
    assert cw.main([str(root)]) == 0


# ─── links ───────────────────────────────────────────────────────────────────


def test_constructed_broken_link_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra="# Extra\n\nSee [the guide](GettingStartd).\n")
    assert _failures(root) == ["Extra.md:3: link to a page that does not exist: GettingStartd"]


def test_constructed_broken_anchor_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra="# Extra\n\n[x](Getting-Started#uninstall)\n")
    assert _failures(root) == [
        "Extra.md:3: link to an anchor that does not exist: Getting-Started#uninstall"
    ]


def test_constructed_broken_sidebar_entry_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    (root / "_Sidebar.md").write_text("- [Home](Home)\n- [Gone](Gone-page-2)\n", encoding="utf-8")
    assert _failures(root) == ["_Sidebar.md:2: link to a page that does not exist: Gone-page-2"]


def test_constructed_wiki_links_and_same_page_anchors_resolve(tmp_path: Path) -> None:
    page = (
        "# Extra\n\n## Where it keeps data\n\n[[Home]] · [[the index|Index]] · "
        "[up](#where-it-keeps-data) · [ref][r] · [cli](CLI-Reference#wowlab-snap-gc) · "
        "[out](https://github.com/example)\n\n[r]: Getting-Started\n"
    )
    root = _build(tmp_path / "wiki", Extra=page)
    assert _failures(root) == []


def test_constructed_link_inside_code_is_not_a_link(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra="# Extra\n\n`[x](Nowhere)`\n\n```\n[y](Nowhere)\n```\n")
    assert _failures(root) == []


# ─── commands ────────────────────────────────────────────────────────────────


def test_constructed_unknown_command_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra="# Extra\n\nRun `wowlab snapshot create`.\n")
    assert _failures(root) == ["Extra.md:3: unknown command `wowlab snapshot`"]


def test_constructed_unknown_leaf_under_a_group_fails(tmp_path: Path) -> None:
    root = _build(
        tmp_path / "wiki", Extra="# Extra\n\n```bash\nuv run wowlab snap rollback ID\n```\n"
    )
    assert _failures(root) == ["Extra.md:4: unknown command `wowlab snap rollback`"]


def test_constructed_unknown_option_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra="# Extra\n\n`wowlab doctor [--frobnicate]`\n")
    assert _failures(root) == ["Extra.md:3: `wowlab doctor` has no option --frobnicate"]


def test_constructed_placeholders_and_usage_lines_are_accepted(tmp_path: Path) -> None:
    page = (
        "# Extra\n\n"
        "`wowlab <group> <command> --help` · `wowlab snap …` · `wowlab --help` · "
        "`wowlab --version` · `wowlab [OPTIONS] COMMAND [ARGS]...` · `wowlab`\n\n"
        "```text\nUsage: wowlab snap create [OPTIONS]\n$ wowlab snap restore ID --paths P --yes\n```\n\n"
        "| `wowlab cvar list [--scope global\\|account\\|character]` | x |\n"
    )
    root = _build(tmp_path / "wiki", Extra=page)
    assert _failures(root) == []


def test_constructed_prose_and_paths_named_wowlab_are_not_invocations(tmp_path: Path) -> None:
    page = (
        "# Extra\n\nwowlab frobnicates nothing. `~/.local/share/wowlab/` · "
        "`sandgraal/wowlab` · `wowlab_core`\n"
    )
    root = _build(tmp_path / "wiki", Extra=page)
    assert _failures(root) == []


# ─── coverage ────────────────────────────────────────────────────────────────


def test_constructed_missing_command_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    (root / "Index.md").write_text(_index_page(skip_command="wowlab snap gc"), encoding="utf-8")
    assert _failures(root) == [
        "command `wowlab snap gc` is not named or linked on any hand-written page"
    ]


def test_constructed_the_generated_page_does_not_count_as_coverage(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    (root / "Index.md").write_text(_index_page(skip_command="wowlab undo"), encoding="utf-8")
    assert "### wowlab undo" in (root / "CLI-Reference.md").read_text(encoding="utf-8")
    assert _failures(root) == [
        "command `wowlab undo` is not named or linked on any hand-written page"
    ]


def test_constructed_a_link_to_the_command_section_counts(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra="# Extra\n\n[gc](CLI-Reference#wowlab-snap-gc)\n")
    (root / "Index.md").write_text(_index_page(skip_command="wowlab snap gc"), encoding="utf-8")
    assert _failures(root) == []


def test_constructed_missing_module_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    (root / "Index.md").write_text(_index_page(skip_module="guard"), encoding="utf-8")
    assert _failures(root) == [
        "module `wowlab_core.guard` is not named or linked on any hand-written page"
    ]


def test_constructed_a_module_named_by_its_file_counts(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra="# Extra\n\nThe write gate is `guard.py`.\n")
    (root / "Index.md").write_text(_index_page(skip_module="guard"), encoding="utf-8")
    assert _failures(root) == []


# ─── the generated page and the file set ─────────────────────────────────────


def test_constructed_missing_generated_page_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    (root / "CLI-Reference.md").unlink()
    (root / "_Sidebar.md").write_text("- [Home](Home)\n", encoding="utf-8")
    (root / "Command-Reference.md").write_text("# Command Reference\n", encoding="utf-8")
    assert _failures(root) == [
        "CLI-Reference.md: missing; the job writes it with scripts/gen_command_reference.py"
    ]


def test_constructed_hand_edit_of_the_generated_page_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    page = root / "CLI-Reference.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nOne more line.\n", encoding="utf-8")
    assert _failures(root) == [
        "CLI-Reference.md: differs from what its generator renders now; a generated page is "
        "never edited by hand"
    ]


def test_constructed_unexpected_files_fail(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    (root / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "Page.md").write_text("# x\n", encoding="utf-8")
    (root / ".lab").mkdir()
    (root / ".lab" / "state.json").write_text("{}\n", encoding="utf-8")
    assert _failures(root) == [
        "run.sh: unexpected file; the wiki holds only top-level .md pages",
        "sub/Page.md: unexpected file; the wiki holds only top-level .md pages",
    ]


# ─── personal data ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("Mine is in `/Users/someone/Games`.", "an absolute home path"),
        ("See C:\\Users\\someone\\Desktop.", "an absolute home path"),
        ("It lives on /Volumes/External Disk/stuff.", "an absolute home path"),
        (
            "`D:\\Games\\World of Warcraft`",
            "an install path other than a documented default location",
        ),
        ("`WTF/Account/123456789#1/`", "a numeric account folder"),
        ("Brakka-Stonewhisper logged in.", "a Name-Realm pair"),
        ('The log said "Brakka-Area52-US".', "a Name-Realm pair"),
        ("Write to someone@example.org.", "an email address"),
        ("Add me: Brakka#12345.", "a BattleTag"),
        ("key sk-ant-api03-abcdefghijklmnop", "a credential-shaped string"),
    ],
)
def test_constructed_personal_data_fails_without_quoting_it(
    tmp_path: Path, line: str, kind: str
) -> None:
    root = _build(tmp_path / "wiki", Extra=f"# Extra\n\n{line}\n")
    out = _failures(root)
    assert out == [f"Extra.md:3: {kind} (not quoted here; the log is public)"]
    for word in ("someone", "Brakka", "123456789", "External", "sk-ant", "Games"):
        assert all(word not in f for f in out)


def test_constructed_documented_placeholders_and_defaults_pass(tmp_path: Path) -> None:
    page = (
        "# Extra\n\n"
        "A character is `Name-Realm`, on Forever `<First>-<Second>` (First-Second), "
        "in the log `Name-Realm-US`. See the Getting-Started and Command-Reference pages.\n\n"
        "Defaults: `/Applications/World of Warcraft`, `C:\\Program Files (x86)\\World of Warcraft`, "
        "`E:\\World of Warcraft`, `<drive>\\Program Files\\World of Warcraft`, "
        '`export WOWLAB_WOW_ROOT="/path/to/World of Warcraft"`, `~/.local/share/wowlab/`, '
        "`WTF/Account/<ACCOUNT>/<digits>/`.\n\n"
        "Links: https://github.com/sandgraal/wowlab/blob/main/docs/LAB_PLAN.md\n"
    )
    root = _build(tmp_path / "wiki", Extra=page)
    assert _failures(root) == []


# ─── the change against --base ───────────────────────────────────────────────


def test_constructed_oversized_diff_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    base = _commit_all(root)
    body = "".join(f"Line {i} of a rewrite.\n" for i in range(cw.MAX_CHANGED_LINES + 1))
    (root / "Rewrite.md").write_text(body, encoding="utf-8")
    out = _failures(root, base)
    assert len(out) == 1
    assert out[0].startswith(f"the change against {base} is {cw.MAX_CHANGED_LINES + 1} lines")
    assert cw.main([str(root), "--base", base]) == 1


def test_constructed_oversized_diff_in_bytes_fails(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    base = _commit_all(root)
    (root / "Long.md").write_text("x" * (cw.MAX_CHANGED_BYTES + 1) + "\n", encoding="utf-8")
    out = _failures(root, base)
    assert len(out) == 1 and "the cap is" in out[0]


def test_constructed_diff_under_the_cap_passes_and_counts_changed_lines_twice(
    tmp_path: Path,
) -> None:
    root = _build(tmp_path / "wiki")
    base = _commit_all(root)
    page = root / "Getting-Started.md"
    page.write_text(page.read_text(encoding="utf-8").replace("Run", "Then run"), encoding="utf-8")
    checker = cw.check(root, base)
    assert checker.failures == []
    assert checker.changed_lines == 2


def test_constructed_generated_page_and_state_do_not_count_toward_the_cap(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    (root / "CLI-Reference.md").unlink()
    (root / "_Sidebar.md").write_text("- [Home](Home)\n", encoding="utf-8")
    (root / "Command-Reference.md").write_text("# Command Reference\n", encoding="utf-8")
    base = _commit_all(root)
    (root / "CLI-Reference.md").write_text(gen.render(), encoding="utf-8")
    (root / ".lab").mkdir()
    (root / ".lab" / "state.json").write_text("{}\n" * 1000, encoding="utf-8")
    checker = cw.check(root, base)
    assert checker.failures == []
    assert checker.changed_lines == 0


def test_constructed_deleting_the_wiki_is_over_the_cap(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Big="# Big\n\n" + "A line.\n" * (cw.MAX_CHANGED_LINES + 5))
    base = _commit_all(root)
    (root / "Big.md").unlink()
    assert any("the cap is" in f for f in _failures(root, base))


def test_constructed_a_new_external_host_fails_against_the_base(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra="# Extra\n\n[repo](https://github.com/x/y)\n")
    base = _commit_all(root)
    page = root / "Extra.md"
    page.write_text(
        page.read_text(encoding="utf-8")
        + "\n[more](https://github.com/x/z) [bad](https://evil.example/x)\n",
        encoding="utf-8",
    )
    assert _failures(root, base) == [
        "Extra.md: links to evil.example, which the wiki at the base does not link to; "
        "a new external site is added by hand"
    ]


# ─── command line ────────────────────────────────────────────────────────────


def test_constructed_cli_prints_one_line_per_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _build(
        tmp_path / "wiki",
        Extra="# Extra\n\n[x](Nope) `wowlab nope`\n\nBrakka-Stonewhisper\n",
    )
    assert cw.main([str(root)]) == cw.EXIT_PERSONAL
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "Extra.md:3: link to a page that does not exist: Nope",
        "Extra.md:3: unknown command `wowlab nope`",
        "Extra.md:5: a Name-Realm pair (not quoted here; the log is public)",
    ]


def test_constructed_cli_ok_line_and_bad_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _build(tmp_path / "wiki")
    base = _commit_all(root)
    assert cw.main([str(root), "--base", base]) == 0
    assert capsys.readouterr().out.startswith("check_wiki: ok (6 pages; change 0 lines")
    assert cw.main([str(tmp_path / "missing")]) == 2
    assert cw.main([str(root), "--base", "no-such-ref"]) == 2


def test_script_runs_as_a_program(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_wiki.py"), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("check_wiki: ok")


# ─── the real wiki ───────────────────────────────────────────────────────────


def _real_copy(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    shutil.copytree(REAL_WIKI, root)
    return root


def test_real_wiki_links_personal_data_files_and_commands_are_clean(tmp_path: Path) -> None:
    """real: on the wiki as it was, only coverage and the missing generated page fail.

    The copy predates this job: it has no CLI-Reference and names no module
    of the library, which the first run fixes. Everything else must not fire
    on real prose.
    """
    out = _failures(_real_copy(tmp_path))
    rest = [
        f
        for f in out
        if not f.startswith("module `wowlab_core.")
        and not (f.startswith("command `") and "is not named or linked" in f)
        and f
        != "CLI-Reference.md: missing; the job writes it with scripts/gen_command_reference.py"
    ]
    assert rest == []
    assert (
        "CLI-Reference.md: missing; the job writes it with scripts/gen_command_reference.py" in out
    )


def test_real_wiki_passes_once_the_first_run_adds_what_it_lacks(tmp_path: Path) -> None:
    """real, plus what the first run adds: the generated page and a page naming the modules."""
    root = _real_copy(tmp_path)
    base = _commit_all(root)
    (root / "CLI-Reference.md").write_text(gen.render(), encoding="utf-8")
    modules = "".join(f"- `wowlab_core.{m}`\n" for m in cw.public_modules())
    commands = "".join(f"- `{c}`\n" for c in _leaves())
    (root / "Library.md").write_text(f"# Library\n\n{modules}\n{commands}", encoding="utf-8")
    checker = cw.check(root, base)
    assert [str(f) for f in checker.failures] == []
    assert 0 < checker.changed_lines <= cw.MAX_CHANGED_LINES
