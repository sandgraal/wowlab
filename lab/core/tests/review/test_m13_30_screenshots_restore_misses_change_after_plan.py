# Probe from review of m13/30-restore-screenshots; reproduces a whole restore of a --screenshots snapshot that reports success after a file the plan called unchanged was changed before the confirm.
"""A whole `snap restore` with nothing left out restores with `paths=None`,
and the CLI compares the gate's plan with the plan it showed, so a file that
changes between the plan and the confirm makes the restore roll back ("the
files changed after the plan was shown"). With screenshots in the snapshot
(M13-30) the CLI restores by name only the paths its dry run found
changed. A file the dry run found unchanged is then neither named nor
checked: if it changes before the confirm, the restore commits, says it
restored the snapshot, and leaves that file differing from it. The positive
control is the same sequence on a snapshot taken without `--screenshots`.

Constructed (L8): a screenshot file under `Screenshots/`, which the capture
does not have, an edited copy of a captured SavedVariables file standing for
a later session, and an edit to a captured `.toc` made inside a patched
`cli._confirm`, standing for another program writing between the plan and
the answer. The install is the captured tree copied into `tmp_path`, the
user data directory redirected, the process table a fake. Nothing reads or
writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    _flip_syndicator,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard
from wowlab_core.snapshot import Manifest

TOC = "Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc"
SHOT = "Screenshots/WoWScrnShot_101626_120000.jpg"
EDIT = b"## constructed edit between the plan and the confirm\n"


def _restore_with_edit_at_confirm(
    flavor: Path, monkeypatch: pytest.MonkeyPatch, *, shots: bool
) -> tuple[int, str, bytes]:
    if shots:
        (flavor / SHOT).parent.mkdir(parents=True, exist_ok=True)
        (flavor / SHOT).write_bytes(b"\xff\xd8constructed jpeg\xff\xd9")
    args = ["snap", "create", "--json"] + (["--screenshots"] if shots else [])
    manifest = Manifest.model_validate_json(ok(*args).stdout)
    toc_then = (flavor / TOC).read_bytes()
    _flip_syndicator(flavor)  # one file to restore, so the plan is not empty
    real = cli._confirm

    def confirm(question: str, yes: bool) -> None:
        (flavor / TOC).write_bytes(EDIT)
        real(question, yes)

    monkeypatch.setattr(cli, "_confirm", confirm)
    result = run("snap", "restore", manifest.id, "--yes")
    return result.exit_code, _plain(result.stderr), toc_then


def test_positive_control_without_screenshots_a_change_after_the_plan_rolls_back_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, err, _ = _restore_with_edit_at_confirm(flavor, monkeypatch, shots=False)
    assert code == 1
    assert "the files changed after the plan was shown" in err
    (record,) = guard.history()
    assert record.state != "committed"


def test_with_screenshots_a_change_after_the_plan_is_not_left_behind_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, err, toc_then = _restore_with_edit_at_confirm(flavor, monkeypatch, shots=True)
    if code != 0:
        assert "the files changed after the plan was shown" in err
        return  # refused and rolled back, as without screenshots: acceptable
    # It committed and said it restored the snapshot: the toc must then be the snapshot's.
    assert (flavor / TOC).read_bytes() == toc_then, (
        "the restore committed while a file the plan called unchanged differs from the snapshot"
    )
