"""`undo` names a file it put back that the plan did not list (M16-105).

Since M16-102 the undo result gives the login caveat for every `Config.wtf`
or `*-cache` file the undo wrote. When such a file changes while the prompt
waits, after the plan was shown, the undo puts it back too; the text result
then names that file once, after "Undone.", so the caveat has a file on
screen to point at. `--json` gains `written_paths`, the paths the undo wrote
(its own journal record), so a reader need not look the record up.

Constructed (L8 boundary case): the M16-102 repro (one appended line in
each captured file), plus one more line in `config-cache.wtf` while the
prompt waits. Everything runs in the `tmp_path` tree of the CLI fixtures;
nothing touches a real install.
"""

# ruff: noqa: F811
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cli import (
    SYNDICATOR,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_undo_login_note_agrees import CONFIG_CACHE, _cache_changes_at_the_prompt, _restored

from wowlab_core import cli, guard


def _undone_at(out: list[str]) -> int:
    return next(i for i, line in enumerate(out) if line.startswith("Undone."))


def test_a_cache_file_changed_at_the_prompt_is_named_once_after_undone_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restored(flavor, put_cache_back=True)
    _cache_changes_at_the_prompt(flavor, monkeypatch)
    result = run("undo", input="y\n")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert CONFIG_CACHE in [p.path for p in guard.history()[-1].paths]
    out = _plain(result.stdout).splitlines()
    done = _undone_at(out)
    named = [line for line in out if CONFIG_CACHE in line]
    assert len(named) == 1, out  # once, and not in the plan
    after = out[done + 1 :]
    assert f"  also put back  {CONFIG_CACHE}  (it changed after the plan was shown)" in after
    # The file is named before the caveat it explains.
    assert after.index(cli._RELOGIN) > next(i for i, x in enumerate(after) if CONFIG_CACHE in x)
    # The listed SavedVariables file is in the plan only, not repeated.
    assert not any(SYNDICATOR in line for line in after), out


def test_a_cache_file_changed_at_the_prompt_is_in_json_written_paths_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restored(flavor, put_cache_back=True)
    _cache_changes_at_the_prompt(flavor, monkeypatch)
    done = cli.UndoReport.model_validate_json(ok("undo", "--yes", "--json").stdout)
    record = guard.history()[-1]
    assert done.applied and done.transaction == record.id
    assert done.written_paths == [p.path for p in record.paths]
    assert sorted(done.written_paths) == sorted([CONFIG_CACHE, SYNDICATOR])
    assert not any(CONFIG_CACHE in line for line in done.plan_lines)
    assert done.notes == [cli._RELOGIN]


def test_control_no_change_at_the_prompt_names_nothing_extra_constructed(
    root: Path, flavor: Path
) -> None:
    _restored(flavor, put_cache_back=True)
    out = _plain(ok("undo", "--yes").stdout).splitlines()
    assert not any("also " in line for line in out[_undone_at(out) + 1 :]), out
    assert not any(CONFIG_CACHE in line for line in out), out


def test_control_json_written_paths_are_the_listed_ones_constructed(
    root: Path, flavor: Path
) -> None:
    _restored(flavor, put_cache_back=True)
    done = cli.UndoReport.model_validate_json(ok("undo", "--yes", "--json").stdout)
    assert done.written_paths == [SYNDICATOR]
    assert done.written_paths == [p.path for p in guard.history()[-1].paths]


def test_control_a_listed_cache_file_is_not_named_again_constructed(
    root: Path, flavor: Path
) -> None:
    _restored(flavor, put_cache_back=False)
    out = _plain(ok("undo", "--yes").stdout).splitlines()
    assert not any(CONFIG_CACHE in line for line in out[_undone_at(out) + 1 :]), out


def test_a_declined_undo_wrote_nothing_constructed(root: Path, flavor: Path) -> None:
    _restored(flavor, put_cache_back=True)
    result = run("undo", "--json", input="n\n")
    shown = cli.UndoReport.model_validate_json(result.stdout[result.stdout.index("{") :])
    assert not shown.applied
    assert shown.written_paths == []
