"""An interrupted change is visible, and its debris named (M16-13).

- A most recent journal record still `open` is a `doctor` problem (exit 1)
  that prints the exact `wowlab undo --expect <id>` line, and running that
  line puts the files back and removes the change's leftover temp file.
- `.wowlab-<32 hex>.tmp` has a file-map row, which `explain` prints.
- With no `.build.info`, `doctor` says the folder is not an install, and
  names the Battle.net app's Scan and Repair, before the first-run text.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one (constructed: no client is running). Nothing reads or writes a real
install.

Constructed (L8, boundary): the interruption. A real `addon install` runs
through the write gate, then its journal record is rewritten to `open` and
made to name a temp file, and that temp file is created beside the files
the change wrote, as a change killed mid-write leaves them. Also the folder
without `.build.info`.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from test_cli import (
    FLAVOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, filemap, firstrun, guard, install, snapshot

TEMP_NAME = ".wowlab-0123456789abcdef0123456789abcdef.tmp"  # constructed
TEMP = f"Interface/AddOns/{TEMP_NAME}"


def _interrupt(flavor: Path) -> str:
    """A committed `addon install` turned into one that was killed before it
    finished (constructed): its record `open` and naming a temp file that is
    on disk. The record's id."""
    ok("addon", "install", "lab", "--yes")
    last = guard.history()[-1]
    path = snapshot.default_store_path() / "journal" / f"{last.id}.json"
    record = json.loads(path.read_text(encoding="ascii"))
    record["state"] = "open"
    record["temps"] = [TEMP]
    path.write_text(json.dumps(record, sort_keys=True, indent=1) + "\n", encoding="ascii")
    (flavor / TEMP).write_bytes(b"new bytes, not yet renamed over the target\n")
    return last.id


# ─── an open record is a problem ─────────────────────────────────────────────


def test_an_open_record_is_a_problem_with_the_exact_undo_line_constructed(flavor: Path) -> None:
    record_id = _interrupt(flavor)
    line = f"wowlab undo --expect {record_id}"

    text = run("doctor")
    assert text.exit_code == 1, text.output
    assert line in text.stdout
    assert "Problems:\n" in text.stdout
    problems = text.stdout.split("Problems:\n", 1)[1]
    assert "did not finish" in problems and line in problems

    data = run("doctor", "--json")
    assert data.exit_code == 1
    report = json.loads(data.stdout)
    assert report["store"]["last_transaction"]["state"] == "open"
    assert [p for p in report["problems"] if line in p] == [cli._interrupted(record_id)]


def test_the_printed_line_undoes_the_change_and_removes_its_temp_file_constructed(
    flavor: Path,
) -> None:
    record_id = _interrupt(flavor)
    added = flavor / "Interface" / "AddOns" / "WowLab"
    assert added.is_dir() and (flavor / TEMP).is_file()
    printed = next(p for p in json.loads(run("doctor", "--json").stdout)["problems"] if "undo" in p)
    argv = shlex.split(printed.rsplit("run: ", 1)[1])
    assert argv == ["wowlab", "undo", "--expect", record_id]

    ok(*argv[1:], "--yes")

    assert not added.exists(), "the undo put back what was there before the change"
    assert not (flavor / TEMP).exists(), "the undo removed the temp file the journal names"
    assert TEMP in guard.history()[-1].temps_removed
    after = run("doctor")
    assert after.exit_code == 0, after.output
    assert "Problems:" not in after.stdout


def test_an_older_open_record_is_not_a_problem_once_a_later_change_ran_constructed(
    flavor: Path,
) -> None:
    """The journal only grows: once a later change (the undo here) has run
    over an interrupted one, doctor does not flag it for ever."""
    record_id = _interrupt(flavor)
    ok("undo", "--expect", record_id, "--yes")
    assert [r.state for r in guard.history()] == ["open", "committed"]
    result = run("doctor", "--json")
    assert result.exit_code == 0, result.output
    assert not json.loads(result.stdout)["problems"]


# ─── the temp file's row ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rel",
    [
        TEMP,
        f"WTF/Account/ACC/SavedVariables/{TEMP_NAME}",
        f"WTF/{TEMP_NAME}",
        f"Fonts/{TEMP_NAME}",
    ],
    ids=lambda v: f"constructed-{v}",
)
def test_the_temp_file_has_a_row_wherever_the_write_gate_writes(rel: str) -> None:
    found = filemap.load().classify(rel.split("/"), "flavor", "file")
    assert found is not None and found.id == "wowlab-temp"


@pytest.mark.parametrize(
    "rel",
    [
        TEMP_NAME,  # the flavor folder itself: the gate never writes there
        "WTF/.wowlab-0123.tmp",  # not 32 hex digits
        "WTF/.wowlab-0123456789abcdef0123456789abcdefg.tmp",
        "WTF/wowlab-0123456789abcdef0123456789abcdef.tmp",
    ],
    ids=lambda v: f"constructed-{v}",
)
def test_other_names_are_not_the_temp_file(rel: str) -> None:
    found = filemap.load().classify(rel.split("/"), "flavor", "file")
    assert found is None or found.id != "wowlab-temp"


def test_explain_names_the_temp_file_constructed(flavor: Path) -> None:
    (flavor / TEMP).write_bytes(b"constructed\n")
    row = filemap.load().entry("wowlab-temp")

    text = ok("explain", f"{FLAVOR}/{TEMP}").stdout
    report = json.loads(ok("explain", f"{FLAVOR}/{TEMP}", "--json").stdout)

    assert "interrupted" in text and "wowlab undo" in text
    assert report["result"]["entry"]["id"] == "wowlab-temp"
    assert report["result"]["entry"]["what"] == row.what


# ─── no .build.info ──────────────────────────────────────────────────────────


def _not_an_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "Games" / "World of Warcraft"
    (folder / "_retail_").mkdir(parents=True)  # constructed: .build.info is gone
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)
    return folder


def test_without_build_info_the_verdict_comes_before_the_first_run_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _not_an_install(tmp_path, monkeypatch)

    result = run("doctor", "--root", str(folder))

    assert result.exit_code == 1, result.output
    out = result.stdout
    verdict = out.index("is not a WoW install: it has no .build.info")
    repair = out.index("Scan and Repair")
    intro = out.index(firstrun.intro_lines(store_path=snapshot.default_store_path())[0])
    assert verdict < repair < intro
    assert out.startswith("Install: ")
    assert "Battle.net app" in out[verdict:intro]


def test_the_scan_and_repair_flag_is_in_the_json_only_when_it_applies_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _not_an_install(tmp_path, monkeypatch)
    missing = json.loads(run("doctor", "--root", str(folder), "--json").stdout)
    assert missing["build_info_missing"] is True

    # A folder that does not exist is a typo, not a damaged install.
    gone = json.loads(run("doctor", "--root", str(tmp_path / "nowhere"), "--json").stdout)
    assert "build_info_missing" not in gone
    assert "Scan and Repair" not in run("doctor", "--root", str(tmp_path / "nowhere")).stdout


def test_a_flavor_folder_given_as_the_root_is_pointed_at_its_parent_not_repaired_constructed(
    root: Path,
) -> None:
    result = run("doctor", "--root", str(root / FLAVOR))
    assert result.exit_code == 1
    assert "pass its parent" in result.stdout
    assert "Scan and Repair" not in result.stdout
