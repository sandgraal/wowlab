"""The Crew tab's "Send records to the game" (M22-04): `crew send`
through the server's confirm step, its plan shown before Confirm.

CONSTRUCTED INPUT: every crew book is built at run time by
`parser/_crew_build.py` inside a copy of the captured install
`fixtures/macos-70888/` in `tmp_path` (the invented keys only). The server
runs the real CLI through the shim of `test_ui_confirm_writes.py` (user
data directory in `tmp_path`, no client running, each argv logged), and
the page is driven under Node with the stand-in DOM of `test_ui_crew.py`.
Nothing reads or writes a real install.
"""

# ruff: noqa: F811 - `crew_at` is test_ui_crew's fixture, imported and requested by name

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from test_ui_confirm import _commands, _confirm, _plan, _token
from test_ui_crew import LIST, Crew, _bodies, _drive, crew_at  # noqa: F401 - the fixture

from wowlab_core import cli, desktable, luadata, ui

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _crew_build import KEY_A, KEY_C, T2, book, match, player

SEND = ("crew", "send")
_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"


def _book() -> bytes:
    """A schema 2 book (constructed): KEY_C's side beat yours three times."""
    return book(
        {KEY_A: player(), KEY_C: player(faced=3)},
        schema=2,
        matches=[match([KEY_A], [KEY_C], at=T2 + 60 * n, won=False) for n in range(3)],
    )


def _table(crew: Crew) -> Any:
    return luadata.parse(crew.live.read_bytes()).to_python().get(desktable.DESK_VARIABLE)


def test_send_records_plans_then_confirms_with_its_pin_constructed(crew_at: Any) -> None:
    crew = crew_at(_book())
    listed = {tuple(c["path"]): c for c in _commands(crew.server)}
    assert listed[SEND]["confirm"] == "declined" and listed[SEND]["excluded"] is None
    before = crew.live.read_bytes()
    plan = _plan(crew.server, SEND, {})
    assert plan["exit_code"] == 1, plan["stderr"]
    token = _token(plan)
    assert token is not None, plan["stderr"]
    shown = json.loads(plan["stdout"])
    fields = cli.EXPECT_FIELDS[SEND]
    pin = ":".join(shown[f] for f in fields)
    note = plan["confirm"]["note"]
    assert f"--expect {pin}:" in note and ui._PIN_CHECKS[fields] in note
    lines = plan["confirm"]["plan_lines"]
    assert any(line.startswith("Write the records of 1 player into WowLab.lua") for line in lines)
    assert crew.live.read_bytes() == before  # the plan changed nothing
    status, done = _confirm(crew.server, SEND, token, {})
    assert status == 200 and done["exit_code"] == 0, done["stderr"]
    confirmed = crew.argvs()[-1]
    assert confirmed.count("--yes") == 1 and f"--expect={pin}" in confirmed
    report = json.loads(done["stdout"])
    assert report["written"] is True
    assert done["confirm"]["note"] == (
        f"Journaled as {report['transaction']}. `wowlab undo` reverses it."
    )
    assert crew.history()[-1].label == "crew send (1 player)"
    assert hashlib.sha256(crew.live.read_bytes()).hexdigest() == shown["written_sha256"]
    assert _table(crew)["players"][KEY_C]["matches"] == 3
    # The same token again runs nothing.
    status, again = _confirm(crew.server, SEND, token, {})
    assert status == 403 and again == {"error": "confirm"}


def test_the_button_shows_the_plan_and_confirm_writes_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    crew = crew_at(_book())
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_click": "crew-send-plan"},
        {"crew_click": "crew-send-confirm"},
        {"crew_click": "crew-send-plan"},
    ]
    out = _drive(crew, tmp_path / "node", steps)
    tab, planned, confirmed, again = out["steps"]
    assert tab["toolsHidden"] is False  # a book: the send panel is shown with the tools
    # The plan run: the command and the book's own account and flavor, no token.
    (body,) = _bodies(planned)
    assert body["command"] == list(SEND) and "confirm" not in body
    assert set(body["params"]) <= {"account", "flavor"}
    assert "Write the records of 1 player into WowLab.lua" in planned["text"]
    assert "Confirm" in planned["text"]  # offered, with the server's label
    # Confirm: the same parameters with the server's token; one run.
    (body,) = _bodies(confirmed)
    assert body["command"] == list(SEND) and isinstance(body.get("confirm"), str)
    table = _table(crew)
    assert table["players"][KEY_C]["matches"] == 3 and table["count"] == 1
    assert "Journaled as " in confirmed["text"]
    # Sent again the same day: nothing to send, and no Confirm.
    (body,) = _bodies(again)
    assert "confirm" not in body
    assert "Nothing to send: the game already holds these records." in again["text"]
    assert [r.label for r in crew.history()] == ["crew send (1 player)"]


def test_the_page_runs_send_records_only_through_the_step() -> None:
    """Labelled source scan: the page names the command in one constant and
    posts it only in its plan run and its confirmed run."""
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert script.count('const SEND = ["crew", "send"];') == 1
    assert script.count('"send"') == 1
    assert script.count("postRun({ command: SEND, params: params })") == 1
    assert script.count("postRun({ command: SEND, params: plan.params, confirm: plan.token })") == 1
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert (
        page.count('<button id="crew-send-plan" type="button">Send records to the game</button>')
        == 1
    )
    assert LIST == ("crew", "list")
