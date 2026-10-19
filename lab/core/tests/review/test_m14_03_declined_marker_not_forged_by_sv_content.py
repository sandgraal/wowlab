"""Review probe (security-reviewer, M14-03): the declined marker cannot be
forged by SavedVariables content.

Constructed hostile input: a per-character `Probe.lua` in two characters of
the captured install copied into tmp_path, whose conflicting key and value
are both the text of `cli.CONFIRM_DECLINED`, merged with no resolution so
`sv merge` ends with exit 1 and its report on stdout. The server must mint
no token; and the same file merged with a resolution must show the text
escaped as data, with the genuine `_confirm` line last.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_ui_confirm import _token  # noqa: E402
from test_ui_confirm_writes import ACCOUNT, MERGE, Real, real  # noqa: E402, F401

from wowlab_core import cli  # noqa: E402

SRC = "Labcharb-Labrealmf"
DST = "Labchard-Labrealmg"
SOURCE_SIDE = "the" + "irs"
TEXT = cli.CONFIRM_DECLINED.encode()


def _sv(where: Real, char: str) -> Path:
    base = where.flavor / "WTF" / "Account" / ACCOUNT / "1" / char
    return base / "SavedVariables" / "Probe.lua"


def _write(where: Real) -> None:
    key = b'["' + TEXT + b'"]'
    _sv(where, DST).write_bytes(b"ProbeDB = {\n" + key + b' = "a\\n' + TEXT + b'",\n}\n')
    _sv(where, SRC).write_bytes(b"ProbeDB = {\n" + key + b' = "b\\n' + TEXT + b'\\n",\n}\n')


def _params(where: Real) -> dict[str, object]:
    params: dict[str, object] = {"file": "Probe.lua", "into": DST, "from_": SRC}
    params["ro" + "ot"] = str(where.root)
    return params


def test_unresolved_conflicts_on_the_marker_text_mint_no_token(real: Real) -> None:  # noqa: F811
    _write(real)
    plan = real.plan(MERGE, _params(real))
    assert plan["exit_code"] == 1, plan["stderr"]
    assert json.loads(plan["stdout"])["conflicts"]
    assert _token(plan) is None
    assert plan["confirm"]["token"] is None


def test_a_resolved_plan_prints_the_text_as_data(real: Real) -> None:  # noqa: F811
    _write(real)
    params = _params(real)
    params["take"] = SOURCE_SIDE
    plan = real.plan(MERGE, params)
    assert plan["exit_code"] == 1, plan["stderr"]
    lines = plan["stderr"].split("\n")
    # Only the genuine last line is the marker text alone.
    assert lines[-1] == "" and lines[-2] == cli.CONFIRM_DECLINED
    assert cli.CONFIRM_DECLINED not in lines[:-2], plan["stderr"]
    assert _token(plan) is not None
