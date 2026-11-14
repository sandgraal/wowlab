"""The README's first screen says what M13-10 requires, and nothing in it is stale.

The first screen is everything above the marker comment the README carries
(`FIRST_SCREEN_END`). These tests pin, against the repository as it is:

- the one install line is byte for byte the v0.2.1 line `test_release.py`
  pins as `INSTALL_V021`, the line `scripts/release_check.py` prints for
  v0.2.1 with the published wheel's hash, as the release notes carry it
  (M16-32; v0.1.0's line, `INSTALL_V010`, is history);
- every `wowlab ...` invocation anywhere in the README names a command, and
  options, that exist in the Click tree (the wiki checker's own walk), and the
  three first commands are leaves of it;
- the platform claims match the CI workflows, read from the runners of the
  jobs that run pytest (macOS and Linux in `ci.yml`, Windows in
  `windows.yml`), and macOS is called "Supported" only while its recorded
  first run on a real install (`MACOS_RECORD`) exists and is linked;
- the statements ADR-0029's and ADR-0031's owner rulings ask for are there.
- the first screen leads with what a player gets, names no ticket or ADR,
  and the roadmap is a link to the wiki's Roadmap page (M16-33).

Reads README.md and the workflow files; writes only a copy of the README
into `tmp_path` for the wiki checker.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# The modules those test files already loaded: loading them again would replace
# them in sys.modules and break identity checks elsewhere.
from test_check_wiki import cw, gen
from test_release import INSTALL_V021, WHEEL_V021_SHA256, rc

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
WORKFLOWS = ROOT / ".github" / "workflows"
FIRST_SCREEN_END = "<!-- The first screen ends here"
WIKI = "https://github.com/sandgraal/wowlab/wiki"
FIRST_COMMANDS = ("wowlab doctor", "wowlab ui", "wowlab snap create")
# ADR-0031's human half for macOS: the owner's recorded first run on a real install.
MACOS_RECORD = ROOT / "docs" / "handoffs" / "M13-10-macos.md"
# What `wowlab ui` runs (ui/__init__.py, ADR-0029's owner rulings of 2026-10-15,
# and the confirm step on every `--yes` command, M14-02 and M14-03).
UI_RUNS = (
    "It runs every wowlab command except `ui` itself, `log tail --follow`, "
    "`looks page --out` and `snap export`. The commands that read, and the ones that save only to "
    "wowlab's own data (snapshots, profiles, saved looks, the game-table cache, "
    "pages), run directly. A command that asks before changing anything (`--yes`) "
    "runs first as a plan that changes nothing, and runs for real only when you "
    "confirm the plan it shows: `snap gc` and `doctor --yes` change only wowlab's "
    "own store, and `snap restore`, `undo`, `profile apply`, `sv merge`, `addon "
    "install` and `addon remove` change your install through the same write gate as "
    "on the command line, which takes a snapshot first, so `wowlab undo` reverses "
    "them."
)


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


def test_one_install_line_and_it_is_the_v021_line() -> None:
    release = rc.parse_version("0.2.1")
    assert rc.install_line("sandgraal/wowlab", release, WHEEL_V021_SHA256) == INSTALL_V021
    assert _install_lines(_readme()) == [INSTALL_V021], "exactly one install line, the v0.2.1 one"
    assert INSTALL_V021 + "\n" in _first_screen(), "on the first screen, on a line of its own"


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


# ADR-0019's and ADR-0031's amendments of 2026-10-19 (M15-02): a link the
# player clicks (the Character tab's dressing-room link) is their browser's
# traffic, not wowlab's; each trust-model sentence says so.
# The clause says what the click carries: the address's public game ids. It
# promises nothing about what the site does with them (its own script reads
# the address's fragment).
LINK_CLAUSE = (
    "and a link you click opens in your own browser, carrying your character's race, "
    "body type, class, level and equipped item and bonus ids to that site"
)
SECURITY = ROOT / "SECURITY.md"


# Since M16-07 the README says what leaves the machine once, in the generated
# block (`wowlab_core.disclosure`, `lab/core/tests/test_disclosure.py` is its
# drift test); its link row carries the same payload LINK_CLAUSE names.
LINK_PAYLOAD = "your character's race, body type, class, level and equipped item and bonus ids"


def test_the_trust_model_sentences_name_the_clicked_link() -> None:
    from wowlab_core import disclosure

    screen = _first_screen()
    assert disclosure.render_markdown() in screen, "the network block is on the first screen"
    (link,) = [row for row in disclosure.ROWS if "dressing-room link" in row]
    assert "opens in your own browser, and only when you click it" in link
    assert LINK_PAYLOAD + " to that site" in link and LINK_PAYLOAD in LINK_CLAUSE
    row = next(line for line in _readme().splitlines() if "**Stays on your machine.**" in line)
    assert '"What leaves your machine"' in row, "the promise row points at the block"
    assert "wago.tools" not in row, "and does not say it a second time"
    security = " ".join(SECURITY.read_text(encoding="utf-8").split())
    assert (
        "**Nothing leaves the machine.** The only network traffic is downloading public game "
        "tables, " + LINK_CLAUSE + "." in security
    )
    # The rule's one exception is named where the rule is.
    assert (
        "Anything that transmits data read from an install is a vulnerability, with one "
        "exception: that link." in security
    )


def _jobs(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _runs_the_lab_suite(job: dict[str, Any]) -> bool:
    """A step runs pytest over the Lab suite: no path (the whole suite) or lab/core/tests."""
    for step in job.get("steps", []):
        run = str(step.get("run", ""))
        if re.search(r"\bpytest\b", run) and "tests/harness" not in run:
            return True
    return False


def _suite_runners(name: str) -> set[str]:
    """The runners of the jobs in workflow `name` that run the Lab suite, not of every job."""
    found: set[str] = set()
    for job in _jobs(name).values():
        if not _runs_the_lab_suite(job):
            continue
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
    assert _suite_runners("ci.yml") == {"macos-latest", "ubuntu-latest"}
    assert _suite_runners("windows.yml") == {"windows-latest"}


def test_macos_is_supported_only_with_a_recorded_first_run() -> None:
    """ADR-0031: supported needs CI and a recorded human first run on a real install."""
    screen = _first_screen()
    if "| macOS | **Supported.**" in screen:
        assert MACOS_RECORD.is_file(), "macOS is called supported with no recorded first run"
        assert f"]({MACOS_RECORD.relative_to(ROOT).as_posix()})" in screen


def test_the_ui_statements_the_owner_rulings_require() -> None:
    screen = _first_screen()
    assert "`wowlab ui` changes game files only when you confirm a plan it showed you." in screen
    assert UI_RUNS in screen
    # One description of what the UI runs, said once (M16-08 dropped the copy).
    assert _readme().count(UI_RUNS) == 1
    assert "What the browser explorer runs, exactly" not in _readme()
    # ADR-0029, owner ruling of 2026-10-16: the token URL on shared Linux.
    assert "On a shared Linux machine" in screen
    assert (
        "in their process listing for a while "
        "(a newly started browser can keep it in its command line)" in screen
    )


# M16-08 (owner Q11): what the captures behind the parsers cover, said plainly.
TESTED_ON = (
    "wowlab is built and tested on Forever on macOS; retail and Windows SavedVariables "
    "are untested."
)


def test_the_first_screen_says_what_it_was_tested_on() -> None:
    assert TESTED_ON in _first_screen()


# M16-33: the first screen leads with what a player gets (every character in
# one place, a UI that can be put back), and the roadmap lives on the wiki.
ROADMAP = WIKI + "/Roadmap"
LEADS = (
    "**All your characters in one place.**",
    "**A UI you can put back.**",
    "`wowlab char history`",
    "`wowlab addons check`",
    "`wowlab binds coach`",
)


def test_the_first_screen_leads_with_the_player_features() -> None:
    screen = _first_screen()
    install = screen.index(INSTALL_V021)
    for phrase in LEADS:
        assert phrase in screen, phrase
        assert screen.index(phrase) < install, f"{phrase} comes before the install line"
    assert "opens on the Account tab" in screen
    assert "`wowlab addon install lab`, then log out or `/reload` once" in screen
    # What a snapshot cannot hold is said where the promise is made.
    assert "Action bars and talents are kept on Blizzard's servers" in screen


def test_the_roadmap_lives_on_the_wiki() -> None:
    """The waves, the plans and the full vision moved to the wiki's Roadmap page."""
    readme = _readme()
    assert f"]({ROADMAP})" in readme or f'href="{ROADMAP}"' in readme
    for gone in ("The full vision", "What's planned next", "Where it is now", "Wave 6"):
        assert gone not in readme, gone


def test_the_player_part_names_no_ticket_or_decision() -> None:
    """Player copy names no ticket, ADR or invariant id (.claude/rules/player-copy.md).

    A link's target may be a document path; what a player reads may not."""
    screen = _first_screen()
    shown = re.sub(r"\]\([^)]*\)", "]", re.sub(r"<!--.*?-->", "", screen, flags=re.DOTALL))
    for pattern in (r"\bM\d{2}-\d+", r"\bADR-\d{4}", r"\bL[1-8]\b"):
        assert not re.search(pattern, shown), pattern


def test_redacted_output_is_not_promised_for_later() -> None:
    for name in ("SECURITY.md", "CONTRIBUTING.md"):
        text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
        assert "`wowlab doctor --redact`" in text, name
        assert "once it ships" not in text, name
