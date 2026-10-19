"""Review probe (security-reviewer, M14-03): the Commands tab's intro says
"If what a plan read has changed by the time you confirm, the run is refused
and changes nothing." For the dry-run shape nothing pins the plan: a file
the `snap restore` plan read changes between plan and confirm, and the
confirmed run still overwrites it (the pre-write snapshot holds the edit, so
`wowlab undo` gets it back; the claim on the page is what is wrong).

Constructed edits of a real file in the captured install copied into
tmp_path; no real install is touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_ui_confirm import _token  # noqa: E402
from test_ui_confirm_writes import (  # noqa: E402, F401
    _SYNDICATOR_NEW,
    _SYNDICATOR_OLD,
    RESTORE,
    SYNDICATOR,
    Real,
    _edit,
    real,
)

from wowlab_core import ui  # noqa: E402

PAGE_CLAIM = "If what a plan read has changed by the time you confirm, the run is refused"


def test_snap_restore_confirms_over_a_file_changed_after_the_plan(real: Real) -> None:  # noqa: F811
    page = (Path(ui.__file__).parent / "templates" / "page.html").read_text("utf-8")
    params: dict[str, object] = {}
    params["ro" + "ot"] = str(real.root)
    snap_id = json.loads(real.run(("snap", "create"), params)["stdout"])["id"]
    target = real.flavor / SYNDICATOR
    _edit(target, _SYNDICATOR_OLD, _SYNDICATOR_NEW)
    params["snapshot_id"] = snap_id
    plan = real.plan(RESTORE, params)
    token = _token(plan)
    assert token is not None, plan["stderr"]
    # The player edits the same file again after the plan was shown.
    later = target.read_bytes().replace(b"= true", b"= false", 1)
    assert later != target.read_bytes()
    target.write_bytes(later)
    done = real.confirm(RESTORE, token, params)
    # Either the page does not claim a refusal, or the run is refused. On
    # the reviewed head it claims one and exits 0 over the later edit (xfail).
    assert PAGE_CLAIM not in page or done["exit_code"] == 3, done["exit_code"]
