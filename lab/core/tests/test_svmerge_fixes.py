"""`wowlab sv merge` fix round 1 (M11-09 reviews, 2026-09-29).

- The loader check and the `--from CHARACTER` lookup read only the selected
  account's files: another account folder holding a character folder of
  the same name neither hides the target's `WowLab.lua` nor stands in for it.
- One leaf is one conflict, however many overlapping `--key` passes meet it.
- A same-path `--key` with no `--from` can change nothing: usage, exit 2.

Constructed (labelled in the test names): the captured tree copied into
`tmp_path` as `test_cli.py` builds it, with a second, invented account
folder; small documents for the three-way cases. Nothing reads or writes a
real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_cli import (
    ACCOUNT,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_svmerge import CHAR_A, DBM, DBM_NAME, DBM_VAR, LAB, LAB_A, REAL_A, REAL_DBM, _lost

from wowlab_core import guard, luadata, svmerge

OTHER_ACCOUNT = "90000002#1"
COPY = f'{DBM_VAR}["Labchard Labrealmg"]={DBM_VAR}["Labcharb Labrealmf"]'


def _other_account_lab(flavor: Path, data: bytes) -> Path:
    folder = flavor / "WTF" / "Account" / OTHER_ACCOUNT / "1" / CHAR_A / "SavedVariables"
    folder.mkdir(parents=True)
    path = folder / LAB
    path.write_bytes(data)
    return path


def _copy_within(*extra: str) -> Any:
    return run(
        "sv", "merge", DBM_NAME, "--into", CHAR_A, "--account", ACCOUNT,
        "--key", COPY, "--yes", *extra,
    )  # fmt: skip


def test_another_accounts_lost_probe_does_not_refuse_the_target_constructed(
    flavor: Path,
) -> None:
    # The other account's copy says probe.lost; the target account's file is
    # sound. Only the target's is read, so the merge goes ahead.
    _other_account_lab(flavor, _lost(REAL_A))
    result = _copy_within("--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["written"] is True
    assert not any(f"No {LAB}" in note for note in report["notes"]), report["notes"]
    assert (flavor / DBM).read_bytes() != REAL_DBM


def test_the_targets_lost_probe_refuses_despite_a_sound_copy_elsewhere_constructed(
    root: Path, flavor: Path
) -> None:
    _other_account_lab(flavor, REAL_A)
    (flavor / LAB_A).write_bytes(_lost(REAL_A))
    before = _state(root)
    result = _copy_within()
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "probe.lost" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


# ─── one leaf, one conflict ──────────────────────────────────────────────────

_BASE = b'\nA = {\n["p"] = {\n["x"] = 1,\n["y"] = 1,\n},\n}\n'
_OURS = b'\nA = {\n["p"] = {\n["x"] = 2,\n["y"] = 1,\n},\n}\n'
_THEIRS = b'\nA = {\n["p"] = {\n["x"] = 3,\n["y"] = 4,\n},\n["q"] = 1,\n}\n'


def _merge(keys: list[str], take: svmerge.Side | None = None) -> svmerge.MergeResult:
    return svmerge.merge(
        luadata.parse(_OURS),
        luadata.parse(_THEIRS),
        base=luadata.parse(_BASE),
        keys=keys,
        take=take,
    )


def test_overlapping_keys_list_each_item_once_constructed() -> None:
    for keys in (["A", "A.p"], ["A.p", 'A["p"]'], ["A", "A"]):
        result = _merge(keys)
        assert [c.path for c in result.conflicts] == ['A["p"]["x"]'], keys
        taken = [t.path for t in result.taken]
        assert len(taken) == len(set(taken)), keys
    assert [t.path for t in _merge(["A", "A.p"]).taken] == ['A["p"]["y"]', 'A["q"]']


def test_overlapping_keys_with_take_theirs_list_the_conflict_once_constructed() -> None:
    result = _merge(["A.p", "A"], take="theirs")
    assert [(c.path, c.resolved) for c in result.conflicts] == [('A["p"]["x"]', "theirs")]
    assert luadata.parse(luadata.serialize(result.document)).to_python() == {
        "A": {"p": {"x": 3, "y": 4}, "q": 1}
    }


# ─── a same-path --key needs --from ──────────────────────────────────────────


def test_same_path_key_without_from_is_a_usage_error(root: Path) -> None:
    before = _state(root)
    for key in (f'{DBM_VAR}["Unknown"]', f'{DBM_VAR}["Unknown"]={DBM_VAR}.Unknown'):
        result = run("sv", "merge", DBM_NAME, "--into", CHAR_A, "--key", key, "--yes")
        assert result.exit_code == 2, (key, result.stdout, result.stderr)
        assert "--from" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


def test_check_keys_says_which_keys_name_one_path() -> None:
    assert svmerge.check_keys(["A.p", 'A.p=A["p"]', "A.p=A.q", "A[1]=A[1.0]"]) == [
        True,
        True,
        False,
        True,
    ]


# ─── the disk against the newest snapshot (ruling of 2026-09-29) ─────────────

_LOADS_1 = (b'["loads"] = 4,', b'["loads"] = 1,')
_LOADS_5 = (b'["loads"] = 4,', b'["loads"] = 5,')
_FILTER = (b'["filter"] = 1,', b'["filter"] = 2,')


def _edit(data: bytes, old: bytes, new: bytes) -> bytes:
    assert data.count(old) == 1, old
    return data.replace(old, new)


def _snap_json(label: str) -> None:
    result = run("snap", "create", "-m", label, "--json")
    assert result.exit_code == 0, result.stderr


def test_disk_loads_reset_below_the_newest_snapshot_is_refused_constructed(
    root: Path, flavor: Path
) -> None:
    _snap_json("loads 4")  # one snapshot: the two-snapshot rule cannot run
    (flavor / LAB_A).write_bytes(_edit(REAL_A, *_LOADS_1))  # a session that loaded nothing
    before = _state(root)
    result = _copy_within()
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "loads on disk (1) went down from the newest snapshot (4)" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


def test_force_loader_check_overrides_the_disk_refusal_constructed(flavor: Path) -> None:
    _snap_json("loads 4")
    (flavor / LAB_A).write_bytes(_edit(REAL_A, *_LOADS_1))
    result = _copy_within("--force-loader-check", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["written"] is True
    assert any("went down" in note for note in report["notes"])
    (record,) = guard.history()
    assert [p.path for p in record.paths] == [DBM]


def test_disk_loads_higher_than_the_newest_snapshot_passes_constructed(flavor: Path) -> None:
    _snap_json("loads 4")
    (flavor / LAB_A).write_bytes(_edit(REAL_A, *_LOADS_5))
    result = _copy_within()
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert (flavor / DBM).read_bytes() != REAL_DBM


def test_disk_loads_equal_with_other_bytes_passes_constructed(flavor: Path) -> None:
    _snap_json("loads 4")
    (flavor / LAB_A).write_bytes(_edit(REAL_A, *_FILTER))  # same session, edited since
    result = _copy_within("--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["written"] is True
    assert not any("went down" in note for note in report["notes"])


def test_disk_identical_to_the_newest_snapshot_passes_constructed(flavor: Path) -> None:
    _snap_json("loads 4")
    result = _copy_within()
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert (flavor / DBM).read_bytes() != REAL_DBM
