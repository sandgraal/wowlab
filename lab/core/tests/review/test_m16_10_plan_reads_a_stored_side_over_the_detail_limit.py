# Probe from review of m16/10-plan-diff-names; reproduces a plan comparing a stored side over 64 MiB, which the §18.1 item 3 amendment says gets "not compared"
"""docs/LAB_PLAN.md §18.1 item 3, amended 2026-11-09 (M16-10): "a side over
64 MiB or not readable back gets a 'not compared' line". `_on_disk` bounds
the install side by `_DETAIL_READ_LIMIT`, but `_stored` passes the plan
item's recorded size to `read_object` instead of the limit, so a stored
side over 64 MiB is read whole and compared by name.

Constructed (L8 boundary case): `Config.wtf` padded past 64 MiB with one
line the parser keeps as Unknown. Nothing touches a real install.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import cli

GLOBAL = "WTF/Config.wtf"
NOT_COMPARED = "      (not compared: wowlab could not read both versions back)"


def _detail(out: str) -> list[str]:
    lines = _plain(out).splitlines()
    (at,) = [i for i, ln in enumerate(lines) if f"  {GLOBAL}  (" in ln]
    rest = []
    for ln in lines[at + 1 :]:
        if not ln.startswith("      "):
            break
        rest.append(ln)
    return rest


def _restore_plan(flavor: Path, *, pad: int) -> list[str]:
    g = flavor / GLOBAL
    small = g.read_bytes()
    g.write_bytes(small + b"#" + b"x" * pad + b"\r\n")
    snap_id = json.loads(ok("snap", "create", "-m", "constructed", "--json").stdout)["id"]
    g.write_bytes(small.replace(b'SET useTargetFPS "0"', b'SET useTargetFPS "1"'))
    return _detail(ok("snap", "restore", snap_id, "--dry-run").stdout)


def test_control_a_stored_side_under_the_limit_is_compared(root: Path, flavor: Path) -> None:
    detail = _restore_plan(flavor, pad=1024)
    assert '      CVar useTargetFPS: "1" -> "0"' in detail


def test_a_stored_side_over_the_limit_is_not_compared(root: Path, flavor: Path) -> None:
    detail = _restore_plan(flavor, pad=cli._DETAIL_READ_LIMIT + 1)
    assert detail == [NOT_COMPARED], detail
