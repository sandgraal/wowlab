"""The README's first screen says what M13-10 requires, and nothing in it is stale.

The first screen is everything above the marker comment the README carries
(`FIRST_SCREEN_END`). These tests pin, against the repository as it is:

- the one install line is byte for byte the line `scripts/release_check.py`
  prints for v0.1.0, which `test_release.py` pins as `INSTALL_V010` and
  M13-03's dry run installed (for v0.1.0rc1);
- every `wowlab ...` invocation anywhere in the README names a command, and
  options, that exist in the Click tree (the wiki checker's own walk), and the
  three first commands are leaves of it;
- the platform claims match the CI workflows: a macOS and a Windows job run
  the Lab suite, and Linux runs it in `ci.yml`;
- the statements ADR-0029's and ADR-0031's owner rulings ask for are there.

Reads README.md and the workflow files; writes only a copy of the README
into `tmp_path` for the wiki checker.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from test_check_wiki import _load
from test_release import INSTALL_V010

cw = _load("check_wiki")
gen = _load("gen_command_reference")
rc = _load("release_check")

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
WORKFLOWS = ROOT / ".github" / "workflows"
FIRST_SCREEN_END = "<!-- The first screen ends here"
WIKI = "https://github.com/sandgraal/wowlab/wiki"
FIRST_COMMANDS = ("wowlab doctor", "wowlab ui", "wowlab snap create")


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _first_screen() -> str:
    text = _readme()
    assert text.count(FIRST_SCREEN_END) == 1, "the README marks where its first screen ends, once"
    return text.split(FIRST_SCREEN_END, 1)[0]


def _install_lines(text: str) -> list[str]:
    return [
        line.strip() for line in text.splitlines() if line.strip().startswith("uv tool install")
    ]


def test_one_install_line_and_it_is_the_v010_line() -> None:
    assert rc.install_line("sandgraal/wowlab", rc.parse_version("0.1.0")) == INSTALL_V010
    assert _install_lines(_readme()) == [INSTALL_V010], "exactly one install line, the v0.1.0 one"
    assert INSTALL_V010 + "\n" in _first_screen(), "on the first screen, on a line of its own"


def test_every_wowlab_invocation_exists_in_the_click_tree(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(_readme(), encoding="utf-8")
    checker = cw.Checker(root_dir=tmp_path)
    checker.load()
    checker.check_commands(gen.root_command())
    assert [str(f) for f in checker.failures] == []
    for command in FIRST_COMMANDS:
        assert command in checker.covered_leaves, f"{command} is a leaf the README reaches"


def test_the_first_screen_names_the_three_first_commands_and_the_wiki() -> None:
    screen = _first_screen()
    for command in FIRST_COMMANDS:
        assert re.search(rf"^{re.escape(command)}(\s|$)", screen, re.MULTILINE), command
    assert f"]({WIKI})" in screen


def test_the_first_screen_states_the_trust_model() -> None:
    screen = _first_screen()
    for phrase in (
        "Read-only by default",
        "asks first",
        "snapshot before it writes",
        "`wowlab undo` reverses",
        "Nothing is uploaded",
        "wago.tools",
    ):
        assert phrase in screen, phrase


def _jobs(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _runners(name: str) -> set[str]:
    found: set[str] = set()
    for job in _jobs(name).values():
        runs_on = job.get("runs-on", "")
        if "matrix.os" in str(runs_on):
            found |= {row["os"] for row in job["strategy"]["matrix"]["include"]}
        else:
            found.add(str(runs_on))
    return found


def test_the_platform_claims_match_ci() -> None:
    screen = _first_screen()
    assert "| macOS | **Supported.** CI runs the test suite on macOS" in screen
    assert "| Windows | **Untested.** CI runs the test suite on Windows" in screen
    assert "| Linux | **Untested.** CI runs the test suite on Linux" in screen
    assert {"macos-latest", "ubuntu-latest"} <= _runners("ci.yml")
    assert _runners("windows.yml") == {"windows-latest"}


def test_the_ui_statements_the_owner_rulings_require() -> None:
    screen = _first_screen()
    assert "`wowlab ui` never changes game files." in screen
    assert "no command that asks before changing anything (`--yes`)" in screen
    # ADR-0029, owner ruling of 2026-10-16: the token URL on shared Linux.
    assert "On a shared Linux machine" in screen
    assert "process listing while the browser opens" in screen
