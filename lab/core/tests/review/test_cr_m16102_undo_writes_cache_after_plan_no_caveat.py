# Probe from review of m16/102-undo-login-caveat; reproduces an undo that writes config-cache.wtf (changed while the prompt waited) and gives no login caveat in plan or result.
"""M16-102 makes `undo`'s result decide its login note from the paths the
plan listed. A `*-cache` file the plan did not list (its disk held the
pre-transaction bytes) but that changes while the prompt waits is still put
back by `guard.undo`: the undo's own journal record names it. The plan had
no caveat, and now neither has the result, in text or `--json` `notes`, so
the player is told nothing about a login-read file wowlab just wrote. On
main the result carried the caveat. Deciding from the paths the undo
actually wrote (its own record, or that joined with the listed paths)
keeps the ticket's repro silent and this case covered.

Constructed (L8 boundary case): the M16-102 repro, plus one appended line
in `config-cache.wtf` between the plan and the confirm. Everything runs in
the `tmp_path` tree of the CLI fixtures; nothing touches a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)
from test_undo_login_note_agrees import CONFIG_CACHE, _restored

from wowlab_core import cli, guard


def _cache_changes_at_the_prompt(flavor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = flavor / CONFIG_CACHE
    confirm = cli._confirm

    def changed_while_waiting(question: str, yes: bool) -> None:
        cache.write_bytes(cache.read_bytes() + b'SET constructedLater "2"\r\n')
        confirm(question, yes)

    monkeypatch.setattr(cli, "_confirm", changed_while_waiting)


def test_constructed_cache_written_after_the_plan_gets_the_caveat_in_json(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restored(flavor, put_cache_back=True)
    _cache_changes_at_the_prompt(flavor, monkeypatch)
    done = cli.UndoReport.model_validate_json(ok("undo", "--yes", "--json").stdout)
    written = [p.path for p in guard.history()[-1].paths]
    assert CONFIG_CACHE in written  # the undo did write it
    assert cli._RELOGIN not in done.plan_lines  # the plan had no reason to say it
    assert done.notes == [cli._RELOGIN], (done.notes, written)


def test_constructed_cache_written_after_the_plan_gets_the_caveat_in_text(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restored(flavor, put_cache_back=True)
    _cache_changes_at_the_prompt(flavor, monkeypatch)
    out = ok("undo", "--yes").stdout
    assert CONFIG_CACHE in [p.path for p in guard.history()[-1].paths]
    assert cli._RELOGIN in out, out


def test_control_unchanged_repro_stays_silent(root: Path, flavor: Path) -> None:
    """Positive control: without the change at the prompt, the undo writes
    only the SavedVariables file and no caveat is due (the ticket's case)."""
    _restored(flavor, put_cache_back=True)
    done = cli.UndoReport.model_validate_json(ok("undo", "--yes", "--json").stdout)
    assert CONFIG_CACHE not in [p.path for p in guard.history()[-1].paths]
    assert done.notes == []
