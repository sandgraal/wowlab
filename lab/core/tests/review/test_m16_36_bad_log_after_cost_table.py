# Probe from review of m16/36-coach-inputs; reproduces a bad --log no longer being a usage error when the cost table is also bad.
"""On `main`, `binds coach` resolved `--log` before loading the key-cost
table, so a `--log` naming no combat log was a usage error (exit 2) whatever
the player's table held. The branch moved the `--log` check after
`load_costs()` (its comment says "a bad --log is a usage error before any
log is read", and the PR says it is validated before the cost table loads):
with a player table that is also invalid, the same command now exits 1 with
the cost-table message. Constructed: a one-line user table out of range,
in the test's own user config folder. Nothing reads or writes a real
install.
"""

# ruff: noqa: F811  (fixtures imported from the coach tests are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli_binds_coach import (
    flavor,  # noqa: F401
    record,  # noqa: F401
    root,  # noqa: F401
    run,
    user_dirs,  # noqa: F401
)

from wowlab_core import bindscoach, cli


def _bad_table() -> None:
    path = bindscoach.user_costs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[coach]\nnever_cast_min_casts = 0\n", encoding="utf-8")


def test_positive_control_bad_log_with_the_shipped_table(flavor: Path, record: Path) -> None:
    result = run("binds", "coach", "--log", "WoWCombatLog-nope.txt")
    assert result.exit_code == cli.EXIT_USAGE


def test_bad_log_is_a_usage_error_before_the_cost_table_constructed(
    flavor: Path, record: Path
) -> None:
    _bad_table()
    result = run("binds", "coach", "--log", "WoWCombatLog-nope.txt")
    # Got: exit 1, "the key-cost table cannot be used: ...".
    assert result.exit_code == cli.EXIT_USAGE, result.stderr
