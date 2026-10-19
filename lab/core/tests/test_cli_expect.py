"""`--expect` on `undo` and `sv merge`, and `_confirm`'s declined line (M14-03).

`docs/LAB_PLAN.md` §16.1, "applies the plan that was shown": `undo` undoes
the newest journal record at run time and `sv merge` hashes its target at
its own start, so across two processes either could apply another plan than
the one shown. `undo --expect <journal id>` and `sv merge --expect <sha256>`
refuse (exit 3) unless the run would apply that plan; the compare is made
again under the write gate's lock (`guard.undo(expected_id=...)`, and
`tx.plan[0].before` inside the `sv merge` transaction). Without `--expect`
both behave as before; `--json` gains `target_sha256` on `SvMergeReport`
and nothing on `UndoReport`, which already names its record in `undone`.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one (constructed: no client is running). Nothing reads or writes a real
install. The edits of a real file are constructed.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    ACCOUNT,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard

DBM_NAME = "DBM-Party-Vanilla.lua"
DBM = f"WTF/Account/{ACCOUNT}/SavedVariables/{DBM_NAME}"
DBM_VAR = "DBMPartyVanilla_AllSavedVars"
CHAR_A = "Labchard-Labrealmg"
KEY_COPY = f'{DBM_VAR}["Labchard Labrealmg"]={DBM_VAR}["Labcharb Labrealmf"]'
MERGE = ("sv", "merge", DBM_NAME, "--into", CHAR_A, "--key", KEY_COPY)


def _out(result: Any) -> str:
    return f"{result.stdout}\n{result.stderr}\n{result.exception!r}"


def _change_unknown(target: Path) -> bytes:
    """One value inside `["Unknown"]` changed (constructed edit); the new bytes."""
    data = target.read_bytes()
    at = data.index(b'["Enabled"] = true,', data.index(b'\r\n["Unknown"] = {'))
    changed = data[:at] + b'["Enabled"] = false,' + data[at + len(b'["Enabled"] = true,') :]
    target.write_bytes(changed)
    return changed


# ─── the declined line ───────────────────────────────────────────────────────


def test_confirm_prints_the_exported_line_when_it_gets_no_yes(root: Path) -> None:
    assert cli.CONFIRM_DECLINED == "Nothing was changed."
    ok("addon", "install", "lab", "--yes")
    result = run("undo", "--json", input="")
    assert result.exit_code == 1, _out(result)
    assert result.stderr.endswith(f"\n{cli.CONFIRM_DECLINED}\n")
    report = json.loads(result.stdout.lstrip())
    assert report["undone"] == guard.history()[-1].id and report["applied"] is False


def test_expect_fields_name_fields_of_the_reports() -> None:
    assert cli.EXPECT_FIELDS == {("undo",): "undone", ("sv", "merge"): "target_sha256"}
    assert "undone" in cli.UndoReport.model_fields
    assert "target_sha256" in cli.SvMergeReport.model_fields


def test_json_gains_exactly_one_field_on_the_merge_report_and_none_on_undo() -> None:
    """The models as on `main` before M14-03, field by field (ADR-0031)."""
    undo_before = [
        "undone",
        "label",
        "state",
        "created_at",
        "flavor_path",
        "plan",
        "created_dirs",
        "applied",
        "transaction",
        "notes",
    ]
    merge_before = [
        "file",
        "scope",
        "into",
        "source",
        "base",
        "mode",
        "keys",
        "take",
        "conflicts",
        "absent",
        "taken",
        "written",
        "transaction",
        "notes",
    ]
    assert list(cli.UndoReport.model_fields) == undo_before
    assert list(cli.SvMergeReport.model_fields) == [*merge_before, "target_sha256"]


# ─── undo --expect ───────────────────────────────────────────────────────────


def test_undo_expect_undoes_only_the_record_named(root: Path, flavor: Path) -> None:
    before = _state(root)
    ok("addon", "install", "lab", "--yes")
    shown = guard.history()[-1].id
    result = run("undo", "--yes", "--expect", shown, "--json")
    assert result.exit_code == 0, _out(result)
    assert json.loads(result.stdout)["undone"] == shown
    assert {k: v[0] for k, v in _state(root).items() if v[0] is not None} == {
        k: v[0] for k, v in before.items() if v[0] is not None
    }


def test_undo_expect_refuses_when_a_newer_record_was_journaled(root: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    shown = guard.history()[-1].id
    ok("addon", "remove", "lab", "--yes")
    records = guard.history()
    state = _state(root)
    for args in (["--yes"], ["--yes", "--json"], []):
        result = run("undo", "--expect", shown, *args)
        assert result.exit_code == 3, _out(result)
        assert shown in result.stderr and records[-1].id in result.stderr
        assert "nothing was undone" in result.stderr
    assert guard.history() == records
    assert _state(root) == state


def test_undo_expect_refuses_on_an_empty_journal(root: Path) -> None:
    result = run("undo", "--yes", "--expect", "00000001-00000000")
    assert result.exit_code == 3, _out(result)
    assert "the journal is empty" in result.stderr


def test_undo_expect_is_compared_again_under_the_gates_lock(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record journaled after the CLI's own reads is refused by `guard`,
    with the id the CLI was given (constructed: the newer record lands
    between the CLI's last read and `guard.undo`)."""
    ok("addon", "install", "lab", "--yes")
    shown = guard.history()[-1].id
    seen: list[str | None] = []
    real_undo = guard.undo

    def late(**kwargs: Any) -> None:
        seen.append(kwargs.get("expected_id"))
        with guard.transaction(_flavor(root), label="constructed: a later change") as tx:
            tx.write(f"WTF/Account/{ACCOUNT}/m14-03-later.txt", b"later\n")
        real_undo(**kwargs)

    monkeypatch.setattr(guard, "undo", late)
    result = run("undo", "--yes", "--expect", shown)
    assert result.exit_code == 3, _out(result)
    assert "refused by the write gate" in result.stderr
    assert seen == [shown]
    assert guard.history()[-1].label == "constructed: a later change"


def _flavor(root: Path) -> Any:
    from wowlab_core import install

    return install.discover(root).flavors[0]


# ─── sv merge --expect ───────────────────────────────────────────────────────


def test_sv_merge_json_names_the_targets_sha256(flavor: Path) -> None:
    before = (flavor / DBM).read_bytes()
    result = run(*MERGE, "--json", input="")
    assert result.exit_code == 1, _out(result)
    assert result.stderr.endswith(f"\n{cli.CONFIRM_DECLINED}\n")
    report = json.loads(result.stdout.lstrip())
    assert report["target_sha256"] == hashlib.sha256(before).hexdigest()
    assert report["written"] is False
    assert (flavor / DBM).read_bytes() == before


def test_sv_merge_expect_writes_when_the_target_is_unchanged(flavor: Path) -> None:
    sha = hashlib.sha256((flavor / DBM).read_bytes()).hexdigest()
    result = run(*MERGE, "--expect", sha, "--yes", "--json")
    assert result.exit_code == 0, _out(result)
    report = json.loads(result.stdout)
    assert report["written"] is True and report["target_sha256"] == sha
    (record,) = guard.history()
    assert record.id == report["transaction"] and [p.before for p in record.paths] == [sha]


def test_sv_merge_expect_refuses_when_the_target_changed(flavor: Path) -> None:
    target = flavor / DBM
    shown = hashlib.sha256(target.read_bytes()).hexdigest()
    changed = _change_unknown(target)
    for args in (["--yes"], ["--yes", "--json"], []):
        result = run(*MERGE, "--expect", shown, *args)
        assert result.exit_code == 3, _out(result)
        assert shown in result.stderr and "nothing was written" in result.stderr
    assert target.read_bytes() == changed
    assert guard.history() == ()


def test_sv_merge_expect_is_compared_inside_the_transaction(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The target changes after the CLI read it and before the gate takes
    its snapshot (constructed): the compare against `tx.plan[0].before`,
    under the gate's locks, refuses with exit 3 and the write is rolled
    back."""
    target = flavor / DBM
    shown = hashlib.sha256(target.read_bytes()).hexdigest()
    real_transaction = guard.transaction
    changed: list[bytes] = []

    def racing(*args: Any, **kwargs: Any) -> Any:
        changed.append(_change_unknown(target))
        return real_transaction(*args, **kwargs)

    monkeypatch.setattr(guard, "transaction", racing)
    result = run(*MERGE, "--expect", shown, "--yes")
    assert result.exit_code == 3, _out(result)
    assert "rolled back" in result.stderr and shown in result.stderr
    assert target.read_bytes() == changed[0]
    (record,) = guard.history()
    assert record.state == "rolled_back"


def test_sv_merge_without_expect_keeps_its_own_check_and_exit_code(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `--expect` the same race is caught by the check that was
    there before, with its exit code (1), as on `main`."""
    target = flavor / DBM
    real_transaction = guard.transaction

    def racing(*args: Any, **kwargs: Any) -> Any:
        _change_unknown(target)
        return real_transaction(*args, **kwargs)

    monkeypatch.setattr(guard, "transaction", racing)
    result = run(*MERGE, "--yes")
    assert result.exit_code == 1, _out(result)
    assert "changed after it was read" in result.stderr
