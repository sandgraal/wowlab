# Probe from review of m14/03-confirm-writes; reproduces the Commands tab's
# claim "If what a plan read has changed by the time you confirm, the run is
# refused and changes nothing" being false for `snap restore` (and, by
# §16.1, `profile apply`): a dry-run leaf's confirmed argv carries no pin,
# so its confirmed run applies a plan the page never showed, with exit 0.
# The probe passes once the page stops making the claim, or once it is true.
# Constructed: an edit to a real file between the plan and the confirm.
from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_ui_confirm import _install, _token
from test_ui_confirm_writes import _SHIM, SYNDICATOR, Real, _serving

from wowlab_core import install, ui

PAGE = Path(ui.__file__).parent / "templates" / "page.html"
CLAIM = "If what a plan read has changed by the time you confirm, the run is refused"


@pytest.fixture
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Real]:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    data = tmp_path / "m14-03-probe-userdata"
    log = tmp_path / "m14-03-probe-argv.jsonl"
    root = _install(tmp_path, second=False)
    with _serving(wowlab_argv=[sys.executable, "-c", _SHIM, str(data), str(log)]) as served:
        yield Real(served, data, log, root)


def test_the_page_does_not_promise_a_refusal_snap_restore_does_not_make(real: Real) -> None:
    root = {"root": str(real.root)}
    snap_id = json.loads(real.run(("snap", "create"), root)["stdout"])["id"]
    target = real.flavor / SYNDICATOR
    original = target.read_bytes()
    params = {**root, "snapshot_id": snap_id}
    plan = real.plan(("snap", "restore"), params)
    shown = [p["path"] for p in json.loads(plan["stdout"])["plan"]]
    # Positive control: the plan shown changes nothing in that file.
    assert SYNDICATOR not in shown
    token = _token(plan)
    assert token is not None
    # What the plan read changes before Confirm.
    target.write_bytes(original.replace(b"= false", b"= true", 1))
    done = real.confirm(("snap", "restore"), token, params)
    applied_unshown = done["exit_code"] == 0 and json.loads(done["stdout"])["plan"] != []
    promised = CLAIM in PAGE.read_text(encoding="utf-8")
    assert not (promised and applied_unshown), (
        "the page promises a refusal, but the confirmed snap restore applied a plan "
        f"never shown: {json.loads(done['stdout'])['plan']}"
    )
