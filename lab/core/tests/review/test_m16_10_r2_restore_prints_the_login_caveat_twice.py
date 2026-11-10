# Probe from review of m16/10-plan-diff-names; reproduces `snap restore --yes` printing the login caveat twice (plan and result)
"""Round 2 puts `_RELOGIN` in the plan before the prompt. After the write,
`snap restore` still prints `_relogin_notes(...)`, the same sentence, so a
committed restore of a bindings file shows it twice in one output
(player-copy rule: one fact in one place, at most one caveat).

Constructed (L8 boundary case): one `bind` line removed from the capture.
Nothing touches a real install.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    ACCOUNT,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import cli


def _restore(flavor: Path, *args: str) -> list[str]:
    snap = str(json.loads(ok("snap", "create", "-m", "constructed", "--json").stdout)["id"])
    b = flavor / f"WTF/Account/{ACCOUNT}/bindings-cache.wtf"
    b.write_bytes(b.read_bytes().replace(b"bind Q MULTIACTIONBAR1BUTTON1\r\n", b""))
    return _plain(ok("snap", "restore", snap, *args).stdout).splitlines()


def test_control_the_dry_run_plan_says_it_once(root: Path, flavor: Path) -> None:
    assert _restore(flavor, "--dry-run").count(cli._RELOGIN) == 1


def test_a_committed_restore_says_it_once(root: Path, flavor: Path) -> None:
    out = _restore(flavor, "--yes")
    assert out.count(cli._RELOGIN) == 1, out
