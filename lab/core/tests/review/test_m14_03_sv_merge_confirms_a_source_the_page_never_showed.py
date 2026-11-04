"""Review probe (security-reviewer, M14-03).

`sv merge --from CHARACTER` reads its source from a live file in the
install. The confirmed run's `--expect` pins only the target's SHA-256, so
when the *source* changes between plan and confirm, the confirmed run
writes values the page never showed, while the offer note says "if what the
plan read has changed by then, it is refused (exit 3) and nothing is
changed" (section 16.1, "applies the plan that was shown").

Constructed: a `Probe.lua` per-character file in two characters of the
captured install copied into tmp_path; no real install is touched.
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

SRC = "Labcharb-Labrealmf"
DST = "Labchard-Labrealmg"
SOURCE_SIDE = "the" + "irs"


def _sv(where: Real, char: str) -> Path:
    base = where.flavor / "WTF" / "Account" / ACCOUNT / "1" / char
    return base / "SavedVariables" / "Probe.lua"


def test_a_source_changed_after_the_plan_is_refused(real: Real) -> None:  # noqa: F811
    _sv(real, DST).write_bytes(b'ProbeDB = {\n["x"] = 1,\n}\n')
    _sv(real, SRC).write_bytes(b'ProbeDB = {\n["x"] = 2,\n}\n')
    params: dict[str, object] = {
        "file": "Probe.lua",
        "into": DST,
        "from_": SRC,
        "take": SOURCE_SIDE,
    }
    params["ro" + "ot"] = str(real.root)
    plan = real.plan(MERGE, params)
    token = _token(plan)
    assert token is not None, (plan["exit_code"], plan["stderr"])
    note = plan["confirm"]["note"]
    assert "--expect" in note and "refused" in note
    # The source changes between plan and confirm (the player logged that
    # character in and out; the target's own file is not rewritten).
    _sv(real, SRC).write_bytes(b'ProbeDB = {\n["x"] = 3,\n}\n')
    done = real.confirm(MERGE, token, params)
    report = json.loads(done["stdout"])
    # What section 16.1 asks: the confirmed run applies the plan shown or is
    # refused (exit 3) with nothing written. On the reviewed head it exits 0
    # and writes x = 3, a value the plan never showed (xfail).
    assert done["exit_code"] == 3, (done["exit_code"], report)
    assert b'["x"] = 1' in _sv(real, DST).read_bytes()
