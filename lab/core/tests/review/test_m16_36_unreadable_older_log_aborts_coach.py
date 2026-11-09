# Probe from review of m16/36-coach-inputs; reproduces `binds coach` failing on an older combat log it did not need.
"""`binds coach` without --log now reads up to the 10 newest logs looking
for the judged character's casts, then falls back to the newest. On `main`
it read the newest log only. An older log that cannot be read (permission
denied, or deleted between the listing and the read) now aborts the whole
command with exit 1, although the newest log was read and the fallback
would have used it. The tests run as uid 0 in CI, so the unreadable file is
simulated by making `combatlog.read_log` raise `PermissionError` for every
log but the newest (constructed). Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from the coach tests are requested by name)
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli_binds_coach import (
    cast,
    flavor,  # noqa: F401
    record,  # noqa: F401
    root,  # noqa: F401
    run,
    user_dirs,  # noqa: F401
)
from test_cli_binds_coach_inputs import caster_log

from wowlab_core import combatlog


def _newest_by_someone_else(flavor: Path) -> Path:
    # Newer than the captured tree's real log; casts by a caster who is not
    # the character judged, so the scan goes on to the older (real) log.
    return caster_log(
        flavor,
        "Someone-Realm-",
        cast(686, "Shadow Bolt"),
        name="WoWCombatLog-060126_000000.txt",
        offset=5,
    )


def _deny_all_but(monkeypatch: pytest.MonkeyPatch, readable: Path) -> None:
    real = combatlog.read_log

    def read_log(path: Any, **kw: Any) -> Any:
        if Path(path).name != readable.name:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, **kw)

    monkeypatch.setattr(combatlog, "read_log", read_log)


def test_positive_control_with_every_log_readable(flavor: Path, record: Path) -> None:
    _newest_by_someone_else(flavor)
    result = run("binds", "coach")
    assert result.exit_code == 0, result.stderr


def test_an_unreadable_older_log_does_not_stop_the_coach_constructed(
    flavor: Path, record: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    newest = _newest_by_someone_else(flavor)
    _deny_all_but(monkeypatch, newest)
    result = run("binds", "coach")
    # Expected: the older log is skipped (or said), and the newest is read as
    # the fallback, as on main. Got: exit 1, "[Errno 13] Permission denied".
    assert result.exit_code == 0, result.stderr
