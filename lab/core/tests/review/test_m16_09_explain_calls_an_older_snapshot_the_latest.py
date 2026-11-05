# Probe from review of m16/09-snapshot-kinds; reproduces `explain` calling the player's snapshot "the latest snapshot" in the same line that names a newer one
"""Constructed sequence (not a real capture): the panel's wipe, recover,
`explain`. After `snap restore --yes` the newest snapshot is the restore's
pre-write copy; `explain` now compares against the older `user` snapshot,
which is right (§18.1 item 3), but its line still opens "In the latest
snapshot (<user id>)" and then says "a newer snapshot (<pre-write id>) was
taken ...". The Explorer shows that line verbatim. The line should not call
a snapshot the latest while naming a newer one.

Positive control: with no pre-write copy in the store, the user snapshot is
the latest, and the line says so.

The install is the captured tree as `test_cli.py` builds it under
`tmp_path`, with the user data directory redirected and the process table
a fake. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    _json,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import cli

CONFIG = "WTF/Config.wtf"


def _create() -> str:
    snapshot_id: str = json.loads(ok("snap", "create", "-m", "constructed", "--json").stdout)["id"]
    return snapshot_id


def test_positive_control_the_only_snapshot_is_the_latest(
    root: Path, flavor: Path, user_data: Path
) -> None:
    taken = _create()
    words = _json(cli.ExplainReport, "explain", CONFIG).snapshot
    assert words is not None and words.newer_pre_write is None
    assert (words.words or "").startswith(f"In the latest snapshot ({taken}")


def test_constructed_line_does_not_call_an_older_snapshot_the_latest(
    root: Path, flavor: Path, user_data: Path
) -> None:
    taken = _create()
    (flavor / CONFIG).write_bytes(b"")  # wiped
    done = _json(cli.RestoreReport, "snap", "restore", taken, "--yes")
    assert done.applied

    facts = _json(cli.ExplainReport, "explain", CONFIG).snapshot
    assert facts is not None and facts.snapshot_id == taken
    assert facts.newer_pre_write is not None
    words = facts.words or ""
    assert "a newer snapshot" in words
    # Fails today: "In the latest snapshot (<taken>, ...): ...; a newer snapshot (...)".
    assert not words.startswith("In the latest snapshot ("), words
    assert not words.startswith("In the latest readable snapshot ("), words
