"""`undo`'s plan and its result agree on the login caveat (M16-102).

The plan's footer decides from the paths it lists (those whose disk differs
from what was there before the transaction). The result, and `--json`
`notes`, decided from every path of the record undone, so a config-cache
file put back by hand before the undo was not listed and got no caveat in
the plan, yet the result printed it. The repro from the ticket:

1. take a snapshot;
2. change a SavedVariables file and the account's `config-cache.wtf`;
3. `snap restore --yes`;
4. write `config-cache.wtf` back to its pre-restore bytes;
5. the undo plan lists only the SavedVariables file and has no caveat; the
   result must not have it either.

The result decides from what the undo wrote (its own journal record): a
cache file changed while the prompt waits is rewritten by the undo, so the
result carries the caveat even though the plan did not.

Constructed (L8 boundary case): one appended line in each captured file.
Nothing touches a real install.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cli import (
    ACCOUNT,
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

from wowlab_core import cli, guard

CONFIG_CACHE = f"WTF/Account/{ACCOUNT}/config-cache.wtf"


def _restored(flavor: Path, *, put_cache_back: bool) -> None:
    """Steps 1 to 4 (step 4 only when `put_cache_back`)."""
    snap = str(json.loads(ok("snap", "create", "-m", "constructed", "--json").stdout)["id"])
    sv = flavor / SYNDICATOR
    sv.write_bytes(sv.read_bytes() + b"\n-- constructed\n")
    cache = flavor / CONFIG_CACHE
    changed = cache.read_bytes() + b'SET constructedCVar "1"\r\n'
    cache.write_bytes(changed)
    ok("snap", "restore", snap, "--yes")
    assert cache.read_bytes() != changed  # the restore put it back
    if put_cache_back:
        cache.write_bytes(changed)


def _plan(*args: str) -> list[str]:
    """The undo plan, declined at the prompt: nothing is undone."""
    result = run("undo", *args, input="n\n")
    assert result.exit_code != 0 or "Undone" not in result.stdout
    return _plain(result.stdout + result.stderr).splitlines()


def test_the_repro_plan_lists_only_the_saved_variables_file_and_no_caveat_constructed(
    root: Path, flavor: Path
) -> None:
    _restored(flavor, put_cache_back=True)
    plan = _plan()
    assert any(SYNDICATOR in line for line in plan), plan
    assert not any(CONFIG_CACHE in line for line in plan), plan
    assert cli._RELOGIN not in plan


def test_the_repro_text_result_has_no_caveat_either_constructed(root: Path, flavor: Path) -> None:
    _restored(flavor, put_cache_back=True)
    out = _plain(ok("undo", "--yes").stdout)
    assert "Undone." in out
    assert cli._RELOGIN not in out, out


def test_the_repro_json_notes_agree_with_the_plan_constructed(root: Path, flavor: Path) -> None:
    _restored(flavor, put_cache_back=True)
    done = cli.UndoReport.model_validate_json(ok("undo", "--yes", "--json").stdout)
    assert done.applied
    assert cli._RELOGIN not in done.plan_lines
    assert done.notes == []


def test_the_declined_json_report_has_no_notes_constructed(root: Path, flavor: Path) -> None:
    _restored(flavor, put_cache_back=True)
    result = run("undo", "--json", input="n\n")
    # The test runner echoes the prompt's answer to stdout ahead of the JSON.
    shown = cli.UndoReport.model_validate_json(result.stdout[result.stdout.index("{") :])
    assert not shown.applied
    assert shown.notes == []
    assert cli._RELOGIN not in shown.plan_lines


def test_control_a_listed_config_cache_gets_the_caveat_in_plan_and_result_constructed(
    root: Path, flavor: Path
) -> None:
    _restored(flavor, put_cache_back=False)
    plan = _plan()
    assert any(CONFIG_CACHE in line for line in plan), plan
    assert cli._RELOGIN in plan
    out = _plain(ok("undo", "--yes").stdout).splitlines()
    # The plan above already said it; the result does not repeat it.
    assert out.count(cli._RELOGIN) == 1, out


def test_control_json_notes_carry_the_caveat_the_plan_shows_constructed(
    root: Path, flavor: Path
) -> None:
    _restored(flavor, put_cache_back=False)
    done = cli.UndoReport.model_validate_json(ok("undo", "--yes", "--json").stdout)
    assert cli._RELOGIN in done.plan_lines
    assert done.notes == [cli._RELOGIN]


def _cache_changes_at_the_prompt(flavor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """While the prompt waits, something rewrites the cache file the plan
    skipped; the undo then puts it back too."""
    cache = flavor / CONFIG_CACHE
    real = cli._confirm

    def confirm(question: str, yes: bool) -> None:
        cache.write_bytes(cache.read_bytes() + b'SET otherCVar "2"\r\n')
        real(question, yes)

    monkeypatch.setattr(cli, "_confirm", confirm)


def test_a_cache_file_changed_at_the_prompt_gets_the_caveat_in_the_text_result_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restored(flavor, put_cache_back=True)
    _cache_changes_at_the_prompt(flavor, monkeypatch)
    result = run("undo", input="y\n")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert CONFIG_CACHE in [p.path for p in guard.history()[-1].paths]
    out = _plain(result.stdout).splitlines()
    done = next(i for i, line in enumerate(out) if line.startswith("Undone."))
    assert cli._RELOGIN not in out[:done]  # the plan did not list the file
    assert out[done + 1 :].count(cli._RELOGIN) == 1, out


def test_a_cache_file_changed_at_the_prompt_gets_the_caveat_in_json_notes_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restored(flavor, put_cache_back=True)
    _cache_changes_at_the_prompt(flavor, monkeypatch)
    done = cli.UndoReport.model_validate_json(ok("undo", "--yes", "--json").stdout)
    assert done.applied
    assert CONFIG_CACHE in [p.path for p in guard.history()[-1].paths]
    assert cli._RELOGIN not in done.plan_lines
    assert done.notes == [cli._RELOGIN]
