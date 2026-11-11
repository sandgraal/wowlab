"""`wowlab ui`: the confirm flow, presented (M16-21; §18.1 item 7, EV-20).

What is graded, item by item:

- the plan's lines (`plan_lines`) sit above Confirm in DOM order, and the
  report is behind a "Details" that is closed for a run of the confirm step
  (open for any other run);
- Confirm's text, which is its accessible name, names the action; focus
  moves to the plan's heading when a plan is offered, and after Confirm to
  the sentence that says how it ended;
- a run that is not a plan ended with its marker is never called a plan,
  and exit codes are shown as words;
- a field the command declares required is marked and checked before Run;
- `GcReport.dry_run` is false under `--yes`, and Confirm stays offered on a
  `snap gc` with nothing to remove;
- the page derives none of these words: the server sends them (§14.5 D3).

Three kinds of run, none touching a real install: the recorder of
`test_ui_confirm` (constructed) for what the server makes of an outcome; the
real CLI through the shim of `test_ui_confirm_writes`, on the captured tree
copied into `tmp_path`; and the page under Node in the stand-in DOM of
`test_ui_confirm`, fed held replies (constructed).
"""

from __future__ import annotations

import http.client
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from test_ui_confirm import (
    _HARNESS,
    _TEMPLATES,
    GC,
    Recorder,
    _confirm,
    _node,
    _page_ids,
    _plan,
    _token,
)
from test_ui_confirm_writes import (
    _SYNDICATOR_NEW,
    _SYNDICATOR_OLD,
    CHAR_A,
    DBM,
    DBM_NAME,
    INSTALL,
    KEY_COPY,
    MERGE,
    MERGED,
    PINS,
    RESTORE,
    SHA,
    SYNDICATOR,
    UNDO,
    Real,
    _declined,
    _edit,
    _serving,
    real,
    recorder,
    server,
)
from typer.testing import CliRunner

from wowlab_core import cli, snapshot, ui

__all__ = ["real", "recorder", "server"]  # fixtures, used by name

# ─── the server: plan lines, Confirm's name, how a run ended (recorder) ──────


def test_a_marked_plan_carries_its_reports_lines_and_confirms_name(
    server: ui.UiServer, recorder: Recorder
) -> None:
    lines = ["Install the lab-addon: 2 change(s)", "  add  Interface/AddOns/WowLab/WowLab.toc"]
    recorder.reply({"dry_run": True, "plan_lines": lines})
    plan = _plan(server, INSTALL, {"name": "lab"})
    offer = plan["confirm"]
    assert offer["token"], offer
    assert offer["plan_lines"] == lines
    assert offer["label"] == "Confirm: run wowlab addon install --yes"
    assert plan["outcome"] == {"text": "Plan only: nothing was changed.", "alert": False}


def test_a_marked_plan_without_lines_points_at_details_constructed(
    server: ui.UiServer, recorder: Recorder
) -> None:
    recorder.reply({"dry_run": True})
    offer = _plan(server, INSTALL, {"name": "lab"})["confirm"]
    assert offer["token"] and offer["plan_lines"] == []
    assert offer["note"] == ui._DETAILS_NOTE
    assert offer["note"] == "Open Details, below, to see what Confirm would change."


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_declined_plan_without_lines_shows_what_it_printed_before_it_asked_constructed(
    server: ui.UiServer, recorder: Recorder, eol: str
) -> None:
    """`sv merge` names no `plan_lines`: the plan is what stderr held before
    `_confirm`'s prompt, without the prompt and the declined line. Its
    marker is exit 1, and it is still a plan, not an alert."""
    stderr = (
        eol.join(["Merge into x.lua", f"  replace  {DBM}", "", "Write the merge? [y/N]:", ""])
        + f"{cli.CONFIRM_DECLINED}{eol}"
    )
    recorder.reply(PINS, 1, stderr=stderr)
    plan = _plan(server, MERGE, {"file": "x.lua", "into": CHAR_A})
    assert plan["exit_code"] == 1
    offer = plan["confirm"]
    assert offer["token"], offer
    assert offer["plan_lines"] == ["Merge into x.lua", f"  replace  {DBM}"]
    assert offer["label"] == "Confirm: run wowlab sv merge --yes"
    assert plan["outcome"] == {"text": "Plan only: nothing was changed.", "alert": False}
    check = ui._PIN_CHECKS[("target_sha256", "merged_sha256")]
    assert offer["note"] == (
        "The plan above is what the command shows before it asks you to say yes. "
        f"Confirm runs it with --yes and --expect {SHA}:{MERGED}: {check}."
    )


def test_a_declined_plan_with_its_own_lines_uses_them(
    server: ui.UiServer, recorder: Recorder
) -> None:
    report, stderr = _declined({"undone": "20261019-0123abcd", "plan_lines": ["Undo it all"]})
    recorder.reply(report, 1, stderr=stderr)
    offer = _plan(server, UNDO)["confirm"]
    assert offer["token"] and offer["plan_lines"] == ["Undo it all"]


@pytest.mark.parametrize(
    ("path", "plan", "code", "stderr", "said"),
    [
        (INSTALL, {"dry_run": True}, 3, "", "Refused (exit code 3)."),
        (INSTALL, {"dry_run": False}, 0, "", "Done (exit code 0)."),
        (INSTALL, "not json", 0, "", "Done (exit code 0)."),
        (INSTALL, {"dry_run": True}, 2, "", "Missing or invalid input (exit code 2)."),
        (UNDO, {"undone": "20261019-0123abcd"}, 1, "no line\n", "Failed (exit code 1)."),
        (INSTALL, {"dry_run": True}, 7, "", "Ended with exit code 7."),
    ],
    ids=["refused", "not-a-dry-run", "not-json", "usage", "declined-without-line", "other"],
)
def test_a_run_that_is_not_a_marked_plan_is_never_called_a_plan_constructed(
    server: ui.UiServer,
    recorder: Recorder,
    path: tuple[str, ...],
    plan: object,
    code: int,
    stderr: str,
    said: str,
) -> None:
    recorder.reply(plan, code, stderr=stderr)
    payload = _plan(server, path, {"name": "lab"} if path == INSTALL else {})
    offer = payload["confirm"]
    assert offer["stage"] == "plan" and offer["token"] is None
    assert offer["plan_lines"] == [] and offer["label"] is None
    assert payload["outcome"] == {"text": said, "alert": code != 0}
    assert "Plan" not in payload["outcome"]["text"]


def test_a_confirmed_run_and_a_plain_run_say_how_they_ended_in_words(
    server: ui.UiServer, recorder: Recorder
) -> None:
    recorder.reply({"dry_run": True}, 0, {"applied": False}, 3)
    token = _token(_plan(server, INSTALL, {"name": "lab"}))
    assert token is not None
    status, done = _confirm(server, INSTALL, token, {"name": "lab"})
    assert status == 200
    assert done["outcome"] == {"text": "After Confirm: Refused (exit code 3).", "alert": True}
    # A leaf outside the step: its outcome too, and no `confirm`.
    recorder.reply({"x": 1}, 0)
    status, plain = _post_plain(server, ["install", "show"])
    assert status == 200 and "confirm" not in plain
    assert plain["outcome"] == {"text": "Done (exit code 0).", "alert": False}


def _post_plain(server: ui.UiServer, command: list[str]) -> tuple[int, dict[str, Any]]:
    from test_ui_confirm import _post

    return _post(server, {"command": command, "params": {}})


@pytest.mark.parametrize("code", [-9, 1, 0], ids=["posix-kill", "windows-kill", "zero"])
def test_a_stopped_run_is_named_by_its_stop_never_by_its_exit_code(code: int) -> None:
    """The exit code of a run the server killed is the kill's, not the
    CLI's: no exit-code words, whatever it is."""
    timed = ui._Run(code, [], [], True, False)
    assert ui._outcome(timed, None) == {
        "text": "Stopped at its time limit before it finished.",
        "alert": True,
    }
    assert ui._outcome(timed, "plan", True)["text"] == (
        "Stopped at its time limit before it finished."
    )
    cut = ui._Run(code, [], [], False, True)
    assert ui._outcome(cut, "confirmed") == {
        "text": "After Confirm: Stopped when its output reached the size limit, before it finished.",
        "alert": True,
    }
    for run in (timed, cut):
        text = str(ui._outcome(run, "confirmed")["text"])
        assert "exit code" not in text and "Done" not in text and "Failed" not in text


# ─── the plan lines count against the output cap (constructed) ───────────────

_CAP = 200_000
# Room for the response's own keys, the outcome and the offer's fields.
_FRAME = 4_096
# Non-ASCII plan text: printed raw, 2 bytes a character in stdout and
# stderr; 6 (`é`) in the plan lines as they are sent.
_WIDE = ["é" * 1_000 + f" {n}" for n in range(40)]


def _raw(server: ui.UiServer, body: object) -> bytes:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=120)
    try:
        conn.request(
            "POST",
            f"/api/run?token={server.token}",
            body=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        reply = conn.getresponse()
        assert reply.status == 200
        return reply.read()
    finally:
        conn.close()


def test_plan_lines_that_would_pass_the_cap_are_not_sent_constructed(recorder: Recorder) -> None:
    report = json.dumps({"dry_run": True, "plan_lines": _WIDE}, ensure_ascii=False)
    recorder.reply(report)
    with _serving(wowlab_argv=recorder.argv, output_cap_bytes=_CAP) as served:
        body = _raw(served, {"command": list(INSTALL), "params": {"name": "lab"}})
    payload = json.loads(body)
    assert not payload["truncated"] and payload["stdout"] == report
    assert len(body) <= _CAP + _FRAME, len(body)
    offer = payload["confirm"]
    assert offer["token"] and offer["plan_lines"] == []
    assert offer["note"] == "Open Details, below, to see what Confirm would change."


def test_a_declined_plan_too_big_to_repeat_points_at_its_text_below_constructed(
    recorder: Recorder,
) -> None:
    stderr = "\n".join([*_WIDE, "Write the merge? [y/N]:", f"{cli.CONFIRM_DECLINED}", ""])
    recorder.reply(PINS, 1, stderr=stderr)
    with _serving(wowlab_argv=recorder.argv, output_cap_bytes=_CAP) as served:
        body = _raw(served, {"command": list(MERGE), "params": {"file": "x.lua", "into": CHAR_A}})
    payload = json.loads(body)
    assert not payload["truncated"] and payload["stderr"] == stderr
    assert len(body) <= _CAP + _FRAME, len(body)
    offer = payload["confirm"]
    assert offer["token"] and offer["plan_lines"] == []
    assert offer["note"].startswith(
        "The plan is the text below, which the command shows before it asks you to say yes. "
        "Confirm runs it with --yes and --expect "
    )


def test_plan_lines_within_the_cap_are_sent_constructed(recorder: Recorder) -> None:
    report = json.dumps({"dry_run": True, "plan_lines": _WIDE}, ensure_ascii=False)
    recorder.reply(report)
    with _serving(wowlab_argv=recorder.argv, output_cap_bytes=4 * _CAP) as served:
        body = _raw(served, {"command": list(INSTALL), "params": {"name": "lab"}})
    payload = json.loads(body)
    assert payload["confirm"]["plan_lines"] == _WIDE
    assert len(body) <= 4 * _CAP + _FRAME


# ─── the server over the real CLI ────────────────────────────────────────────


def test_snap_restore_plan_lines_reach_the_page_as_the_cli_wrote_them(real: Real) -> None:
    root = {"root": str(real.root)}
    created = real.run(("snap", "create"), {**root, "message": "m16-21"})
    snap_id = json.loads(created["stdout"])["id"]
    _edit(real.flavor / SYNDICATOR, _SYNDICATOR_OLD, _SYNDICATOR_NEW)
    plan = real.plan(RESTORE, {**root, "snapshot_id": snap_id})
    offer = plan["confirm"]
    assert offer["token"], plan["stderr"]
    report = json.loads(plan["stdout"])
    assert report["plan_lines"], report
    assert offer["plan_lines"] == report["plan_lines"]
    assert any(SYNDICATOR in line for line in offer["plan_lines"])
    assert offer["label"] == "Confirm: run wowlab snap restore --yes"
    assert plan["outcome"]["text"] == "Plan only: nothing was changed."


def test_sv_merge_plan_lines_are_its_text_plan_without_the_prompt(real: Real) -> None:
    params: dict[str, object] = {
        "root": str(real.root),
        "file": DBM_NAME,
        "into": CHAR_A,
        "key": [KEY_COPY],
    }
    plan = real.plan(MERGE, params)
    assert plan["exit_code"] == 1
    offer = plan["confirm"]
    assert offer["token"], plan["stderr"]
    lines = offer["plan_lines"]
    assert any(f"replace  {DBM}" in line for line in lines), lines
    assert not any("[y/N]" in line or line == cli.CONFIRM_DECLINED for line in lines), lines
    printed = plan["stderr"].replace("\r\n", "\n").split("\n")
    assert printed[: len(lines)] == lines
    assert plan["outcome"] == {"text": "Plan only: nothing was changed.", "alert": False}


# ─── `snap gc`: dry_run says whether it was one ──────────────────────────────


@pytest.fixture
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "m16-21-userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    return redirected


@pytest.mark.parametrize("store", [False, True], ids=["no-store", "empty-store"])
def test_snap_gc_under_yes_is_not_a_dry_run(user_data: Path, store: bool) -> None:
    if store:
        (user_data / "store").mkdir(parents=True)
    runner = CliRunner()
    done = runner.invoke(cli.app, ["snap", "gc", "--yes", "--json"])
    assert done.exit_code == 0, done.output
    assert snapshot.GcReport.model_validate_json(done.stdout).dry_run is False
    dry = runner.invoke(cli.app, ["snap", "gc", "--dry-run", "--json"])
    assert dry.exit_code == 0, dry.output
    assert snapshot.GcReport.model_validate_json(dry.stdout).dry_run is True


def test_a_no_op_snap_gc_keeps_its_confirm(real: Real) -> None:
    """Nothing to remove: the plan is still offered Confirm (M14-02), and the
    confirmed run's report is not a dry run."""
    plan = real.plan(GC, {})
    token = _token(plan)
    assert token is not None and plan["confirm"]["note"].startswith("Nothing to remove.")
    done = real.confirm(GC, token, {})
    assert done["exit_code"] == 0
    assert json.loads(done["stdout"])["dry_run"] is False


# ─── the page, under Node (held replies, constructed) ────────────────────────

_VIEW_OLD = "    stdout: ids.stdout.textContent };"
_VIEW_NEW = """    stdout: ids.stdout.textContent,
    statusRole: ids.status.getAttribute("role"),
    planBoxHidden: ids["plan-box"].hidden, planLines: ids["plan-lines"].textContent,
    planLinesHidden: ids["plan-lines"].hidden, confirmText: ids.confirm.textContent,
    headingFocus: ids["plan-heading"].focused, statusFocus: ids.status.focused,
    detailsHidden: ids["result-details"].hidden, detailsOpen: ids["result-details"].open,
    labels: ids.fields.all().filter((e) => e.tagName === "label").map((e) => e.textContent),
    required: ids.fields.all().filter((e) => e.required === true).map((e) => e.id),
    fieldFocus: ids.fields.all().filter((e) => e.focused > 0).map((e) => e.id) };"""

_DUMP_ITEM = {
    "path": ["sv", "dump"],
    "help": "",
    "excluded": None,
    "confirm": None,
    "params": [
        {
            "name": "file",
            "label": "file",
            "kind": "argument",
            "multiple": False,
            "required": True,
            "help": "",
            "refused": None,
        },
        {
            "name": "flavor",
            "label": "--flavor",
            "kind": "option",
            "multiple": False,
            "required": False,
            "help": "",
            "refused": None,
        },
    ],
}
_INSTALL_ITEM = {
    "path": ["addon", "install"],
    "help": "",
    "excluded": None,
    "confirm": "dry_run",
    "params": [],
}


def _reply(
    stdout: object, code: int, offer: dict[str, Any] | None, outcome: dict[str, Any]
) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "exit_code": code,
        "timed_out": False,
        "truncated": False,
        "outcome": outcome,
        "stdout": json.dumps(stdout),
        "stderr": "",
    }
    if offer is not None:
        reply["confirm"] = offer
    return reply


def _said(code: int, stage: str | None, marked: bool = False) -> dict[str, object]:
    """The server's own outcome for such a run."""
    return ui._outcome(ui._Run(code, [], [], False, False), stage, marked)


def _drive(tmp_path: Path, replies: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    ids, hidden = _page_ids()
    assert _HARNESS.count(_VIEW_OLD) == 1
    harness_text = _HARNESS.replace(_VIEW_OLD, _VIEW_NEW, 1)
    scenario = {
        "ids": ids,
        "hidden": hidden,
        "commands": {"commands": [_INSTALL_ITEM, _DUMP_ITEM], "home": "", "sep": "/"},
        "replies": replies,
        "steps": steps,
    }
    harness = tmp_path / "m16-21-harness.js"
    harness.write_text(harness_text, encoding="utf-8")
    data = tmp_path / "m16-21-scenario.json"
    data.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run(
        [_node(), str(harness), str(_TEMPLATES / "page.js"), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert "error" not in out, out["error"]
    out["requests"] = [
        r for r in out["requests"] if r["body"]["command"] in (["addon", "install"], ["sv", "dump"])
    ]
    return out


def test_the_plan_sits_above_confirm_and_the_report_behind_details_in_the_dom() -> None:
    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")

    def at(element_id: str) -> int:
        found = re.search(rf'\bid="{re.escape(element_id)}"', page)
        assert found is not None, element_id
        return found.start()

    order = ["run", "status", "plan-heading", "plan-lines", "confirm-note", "confirm"]
    order += ["result-details", "result-tree", "result-raw", "stderr"]
    assert [at(i) for i in order] == sorted(at(i) for i in order)
    details = page[at("result-details") :]
    assert re.match(r'id="result-details"[^>]*\bhidden\b[^>]*><summary>Details</summary>', details)
    # The tree and the output as printed are inside "Details": its end comes
    # after the inner one's.
    inner_end = details.index("</details>")
    assert details.index('id="result-tree"') < inner_end
    assert details.index("</details>", inner_end + 1) < details.index('id="stderr"')
    assert re.search(r'<h3 id="plan-heading" tabindex="-1">Plan</h3>', page)
    assert re.search(r'<p id="status" role="status" tabindex="-1">', page)


def test_the_page_shows_the_plan_above_a_named_confirm_and_moves_focus_constructed(
    tmp_path: Path,
) -> None:
    lines = ["Install the lab-addon: 1 change(s)", "  add  a" + chr(0x202E) + "b"]
    label = "Confirm: run wowlab addon install --yes"
    offer = {
        "stage": "plan",
        "token": "tok-21",
        "note": None,
        "needs": None,
        "plan_lines": lines,
        "label": label,
    }
    confirmed = {
        "stage": "confirmed",
        "token": None,
        "note": "Journaled as 20261019-0123abcd. `wowlab undo` reverses it.",
        "needs": None,
        "plan_lines": [],
        "label": None,
    }
    replies = {
        "addon install": _reply({"dry_run": True}, 0, offer, _said(0, "plan", True)),
        "addon install confirm": _reply({"applied": True}, 0, confirmed, _said(0, "confirmed")),
    }
    steps = [{"pick": "addon install"}, {"click": "run"}, {"click": "confirm"}]
    picked, planned, done = _drive(tmp_path, replies, steps)["steps"]
    assert picked["planBoxHidden"] and picked["detailsHidden"]
    # The plan, its lines (a control character shown as a code), Confirm.
    assert not planned["planBoxHidden"] and not planned["planLinesHidden"]
    assert planned["planLines"] == "Install the lab-addon: 1 change(s)\n  add  a⟨U+202E⟩b"
    assert planned["confirmText"] == label and not planned["confirmHidden"]
    assert planned["headingFocus"] == 1, "focus goes to the plan's heading"
    assert (
        planned["status"] == "Plan only: nothing was changed." and planned["statusRole"] == "status"
    )
    # The report is there, behind a closed "Details".
    assert not planned["detailsHidden"] and planned["detailsOpen"] is False
    # After Confirm: the plan and Confirm are gone, focus is on the sentence
    # that says how it ended.
    assert done["planBoxHidden"] and done["confirmHidden"] and done["confirmText"] == "Confirm"
    assert done["status"] == "After Confirm: Done (exit code 0)."
    assert done["statusFocus"] == 1 and done["headingFocus"] == 1
    assert done["detailsOpen"] is False


def test_a_plan_run_without_its_marker_shows_no_plan_and_an_alert_constructed(
    tmp_path: Path,
) -> None:
    offer = {
        "stage": "plan",
        "token": None,
        "note": None,
        "needs": None,
        "plan_lines": [],
        "label": None,
    }
    replies = {"addon install": _reply({"dry_run": True}, 3, offer, _said(3, "plan"))}
    _, planned = _drive(tmp_path, replies, [{"pick": "addon install"}, {"click": "run"}])["steps"]
    assert planned["status"] == "Refused (exit code 3)." and planned["statusRole"] == "alert"
    assert planned["planBoxHidden"] and planned["confirmHidden"] and planned["boxHidden"]
    assert planned["headingFocus"] == 0


def test_a_required_field_is_marked_and_checked_before_run_constructed(tmp_path: Path) -> None:
    replies = {"sv dump": _reply({"file": "x"}, 0, None, _said(0, None))}
    steps = [
        {"pick": "sv dump"},
        {"click": "run"},
        {"set": ["file", "Some.lua"]},
        {"click": "run"},
    ]
    out = _drive(tmp_path, replies, steps)
    picked, empty, _, ran = out["steps"]
    assert picked["labels"] == ["file (required)", "--flavor"]
    assert picked["required"] == ["param-file"]
    # Run with the required field empty: nothing is sent, the field is
    # marked and focused, and the status line says which, as an alert.
    assert empty["status"] == "file is required: fill it in first."
    assert empty["statusRole"] == "alert"
    assert empty["needed"] == 1 and empty["fieldFocus"] == ["param-file"]
    # Filled, it runs; a plain run's report is shown open.
    assert [r["body"] for r in out["requests"]] == [
        {"command": ["sv", "dump"], "params": {"file": "Some.lua"}}
    ]
    assert ran["status"] == "Done (exit code 0)." and ran["statusRole"] == "status"
    assert ran["needed"] == 0
    assert not ran["detailsHidden"] and ran["detailsOpen"] is True


# ─── the page derives nothing (§14.5 D3) ─────────────────────────────────────


def test_the_page_holds_none_of_the_servers_words() -> None:
    """The words for an exit code, "Plan" for a marked plan and Confirm's
    name are the server's: none is in the script, which shows what it is
    sent."""
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    for word in ui._exit_words().values():
        assert f'"{word}"' not in script and f'"{word} (' not in script, word
    for text in ('"Plan only', '"After Confirm', '"Stopped', "Exit code", "Confirm: run", "[y/N]"):
        assert text not in script, text
    assert ui._DETAILS_NOTE not in script
    # The shared reader's error line for a failed run that printed nothing on
    # stderr: the server's outcome, else only what is true of any such run.
    assert '(ended ? ": " + ended : " ended without a message.")' in script
    assert "did not finish" not in script
    # A refused request is the server's refusal, never "Refused" (exit code
    # 3): in the shared reader and on the Commands tab alike.
    assert script.count('"The server refused this request: " + (result.error || reply.status)') == 2
    assert '"Refused: "' not in script
    # Nothing in the script compares an exit code with 1, 2 or 3.
    assert not re.search(r"exit_code\s*[!=<>]=*\s*[123]\b", script)


def test_the_cli_exit_codes_are_the_ones_named() -> None:
    assert ui._exit_words() == {
        cli.EXIT_OK: "Done",
        cli.EXIT_ERROR: "Failed",
        cli.EXIT_USAGE: "Missing or invalid input",
        cli.EXIT_REFUSED: "Refused",
    }
    assert (cli.EXIT_OK, cli.EXIT_ERROR, cli.EXIT_USAGE, cli.EXIT_REFUSED) == (0, 1, 2, 3)
