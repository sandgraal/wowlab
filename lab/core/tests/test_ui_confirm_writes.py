"""`wowlab ui`: the confirm step on the six install writes, and `--expect` (M14-03).

`docs/LAB_PLAN.md` §16.1 and ADR-0029 "Writes". `snap restore`, `profile
apply`, `addon install` and `addon remove` declare `--dry-run` and use
M14-02's first shape unchanged. `undo` and `sv merge` declare none: their
plan run is the leaf with `--json` alone and stdin closed, `_confirm` gets
no answer, and the run ends with `cli.CONFIRM_DECLINED` on stderr and exit
1 (the *declined* shape). Their confirmed run carries `--expect=<pin>`, the
pin read by the server from the plan's own report (`undone`,
`target_sha256`), so it applies the plan that was shown or is refused with
exit 3.

Two kinds of run, none touching a real install:
- the recorder of `test_ui_confirm` (constructed) for what the server makes
  of an outcome: which ones mint a token, what the confirmed argv is;
- the real CLI, through a shim that redirects the user data directory into
  `tmp_path`, empties the process table (constructed: no client is running)
  and logs the argv it was given, on the captured tree (`fixtures/macos/`)
  copied into `tmp_path` (§9's synthetic install, as `test_ui_confirm`
  does), `--offline` where a command reaches game data.

A `guard` refusal is made the way a child process can see it: by holding
`guard.store_lock` on the child's store here, never by a switch that sets
its client probe (there is none; L2, L7).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from test_ui_confirm import (
    FLAVOR,
    GC,
    Recorder,
    _commands,
    _confirm,
    _drive,
    _install,
    _plan,
    _post,
    _result,
    _state,
    _token,
)

from wowlab_core import cli, guard, install, snapshot, ui

RESTORE = ("snap", "restore")
UNDO = ("undo",)
APPLY = ("profile", "apply")
MERGE = ("sv", "merge")
INSTALL = ("addon", "install")
REMOVE = ("addon", "remove")
SIX = [RESTORE, UNDO, APPLY, MERGE, INSTALL, REMOVE]

ACCOUNT = "90000001#6"
SYNDICATOR = f"WTF/Account/{ACCOUNT}/SavedVariables/Syndicator.lua"
DBM_NAME = "DBM-Party-Vanilla.lua"
DBM = f"WTF/Account/{ACCOUNT}/SavedVariables/{DBM_NAME}"
DBM_VAR = "DBMPartyVanilla_AllSavedVars"
CHAR_A = "Labchard-Labrealmg"
KEY_COPY = f'{DBM_VAR}["Labchard Labrealmg"]={DBM_VAR}["Labcharb Labrealmf"]'

RECORD = "20261019-0123abcd"  # a journal record id's shape (constructed)
SHA = "c" * 64  # a SHA-256's shape (constructed)
PROMPT = "Undo it? [y/N]:\n"


def _declined(report: object, *, prompt: str = PROMPT) -> tuple[object, str]:
    """A declined plan as `undo --json` prints it (constructed): the report on
    stdout; the text plan, the prompt and `_confirm`'s line on stderr."""
    return report, f"Undo 1 (a plan)\n{prompt}{cli.CONFIRM_DECLINED}\n"


# ─── the recorder (constructed) ──────────────────────────────────────────────


@contextmanager
def _serving(**kwargs: Any) -> Iterator[ui.UiServer]:
    kwargs.setdefault("timeout_s", 120)
    server = ui.make_server(**kwargs)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


@pytest.fixture
def server(recorder: Recorder) -> Iterator[ui.UiServer]:
    with _serving(wowlab_argv=recorder.argv) as served:
        yield served


@pytest.fixture
def recorder(tmp_path: Path) -> Recorder:
    from test_ui_confirm import _RECORDER

    calls = tmp_path / "m14-03-calls.jsonl"
    replies = tmp_path / "m14-03-replies.json"
    script = tmp_path / "m14_03_fake_wowlab_constructed.py"
    script.write_text(_RECORDER.format(calls=str(calls), replies=str(replies)), encoding="utf-8")
    rec = Recorder([sys.executable, str(script)], calls, replies)
    rec.reply({"dry_run": True})
    return rec


def test_the_six_are_listed_in_the_step_with_their_shapes(server: ui.UiServer) -> None:
    listed = {tuple(c["path"]): c for c in _commands(server)}
    shapes = {path: listed[path]["confirm"] for path in SIX}
    assert shapes == {
        RESTORE: "dry_run",
        UNDO: "declined",
        APPLY: "dry_run",
        MERGE: "declined",
        INSTALL: "dry_run",
        REMOVE: "dry_run",
    }
    for path in SIX:
        assert listed[path]["excluded"] is None, path
        names = {p["name"] for p in listed[path]["params"]}
        # The server's own: never a field of the form.
        assert not names & {"yes", "dry_run", "expect"}, (path, names)


@pytest.mark.parametrize("path", SIX, ids=" ".join)
def test_a_form_cannot_set_yes_dry_run_or_expect(
    server: ui.UiServer, recorder: Recorder, path: tuple[str, ...]
) -> None:
    for params in ({"yes": True}, {"dry_run": True}, {"expect": RECORD}):
        status, payload = _post(server, {"command": list(path), "params": params})
        assert (status, payload) == (403, {"error": "unknown_parameter"}), params
    assert recorder.calls() == []


@pytest.mark.parametrize(
    ("path", "params", "plan_argv"),
    [
        (RESTORE, {"snapshot_id": "abc"}, ["snap", "restore", "--dry-run", "--json", "--", "abc"]),
        (APPLY, {"name": "p"}, ["profile", "apply", "--dry-run", "--json", "--", "p"]),
        (INSTALL, {"name": "lab"}, ["addon", "install", "--dry-run", "--json", "--", "lab"]),
        (REMOVE, {"name": "lab"}, ["addon", "remove", "--dry-run", "--json", "--", "lab"]),
        (UNDO, {}, ["undo", "--json"]),
        (
            MERGE,
            {"file": "WowLab.lua", "into": CHAR_A},
            ["sv", "merge", f"--into={CHAR_A}", "--json", "--", "WowLab.lua"],
        ),
    ],
    ids=lambda v: " ".join(v) if isinstance(v, tuple) else "",
)
def test_each_plan_run_carries_no_yes_and_no_expect_with_stdin_closed(
    server: ui.UiServer,
    recorder: Recorder,
    path: tuple[str, ...],
    params: dict[str, object],
    plan_argv: list[str],
) -> None:
    _plan(server, path, params)
    (call,) = recorder.calls()
    assert call["argv"] == plan_argv
    assert call["stdin"] == "" and call["tty"] is False


def test_the_ui_sends_expect_on_undo_from_the_plans_own_report(
    server: ui.UiServer, recorder: Recorder
) -> None:
    report, stderr = _declined({"undone": RECORD, "applied": False})
    recorder.reply(report, 1, {"undone": RECORD, "applied": True}, stderr=stderr)
    plan = _plan(server, UNDO)
    assert plan["exit_code"] == 1 and plan["stderr"] == stderr
    token = _token(plan)
    assert token is not None
    assert RECORD in plan["confirm"]["note"]
    status, done = _confirm(server, UNDO, token)
    assert status == 200, done
    assert recorder.argvs() == [
        ["undo", "--json"],
        ["undo", "--json", "--yes", f"--expect={RECORD}"],
    ]


def test_the_ui_sends_expect_on_sv_merge_before_its_arguments(
    server: ui.UiServer, recorder: Recorder
) -> None:
    stderr = f"  replace  x\nWrite the merge? [y/N]:\n{cli.CONFIRM_DECLINED}\n"
    recorder.reply({"target_sha256": SHA, "written": False}, 1, stderr=stderr)
    params: dict[str, object] = {"file": "--expect=0", "into": CHAR_A, "key": [KEY_COPY]}
    token = _token(_plan(server, MERGE, params))
    assert token is not None
    assert _confirm(server, MERGE, token, params)[0] == 200
    plan, confirmed = recorder.argvs()
    head = ["sv", "merge", f"--into={CHAR_A}", f"--key={KEY_COPY}"]
    assert plan == [*head, "--json", "--", "--expect=0"]
    assert confirmed == [*head, "--json", "--yes", f"--expect={SHA}", "--", "--expect=0"]


def test_a_confirm_request_cannot_bring_its_own_expect(
    server: ui.UiServer, recorder: Recorder
) -> None:
    report, stderr = _declined({"undone": RECORD})
    recorder.reply(report, 1, stderr=stderr)
    token = _token(_plan(server, UNDO))
    assert token is not None
    assert _confirm(server, UNDO, token, {"expect": "20261019-ffffffff"}) == (
        403,
        {"error": "unknown_parameter"},
    )
    # Presented, so spent; nothing confirmed ran.
    assert _confirm(server, UNDO, token) == (403, {"error": "confirm"})
    assert recorder.argvs() == [["undo", "--json"]]


_MARKER = f"{cli.CONFIRM_DECLINED}\n"


@pytest.mark.parametrize(
    ("stdout", "stderr", "code", "minted"),
    [
        ({"undone": RECORD}, f"plan\n{PROMPT}{_MARKER}", 1, True),
        ({"undone": RECORD}, f"plan\r\nUndo it? [y/N]:\r\n{cli.CONFIRM_DECLINED}\r\n", 1, True),
        ({"undone": RECORD}, _MARKER, 1, True),
        ({"undone": RECORD}, f"plan\n{PROMPT}{_MARKER}", 0, False),
        ({"undone": RECORD}, f"plan\n{PROMPT}{_MARKER}", 2, False),
        ({"undone": RECORD}, f"plan\n{PROMPT}{_MARKER}", 3, False),
        ({"undone": RECORD}, "wowlab: refused by the write gate: busy\n", 3, False),
        ({"undone": RECORD}, "wowlab: nothing to undo: the journal is empty\n", 1, False),
        ({"undone": RECORD}, f"plan\n{PROMPT}{_MARKER}more\n", 1, False),
        ({"undone": RECORD}, f"plan\nx {_MARKER}", 1, False),
        ({"undone": RECORD}, f"plan\n{PROMPT}{cli.CONFIRM_DECLINED}", 1, False),
        ({"undone": RECORD}, "", 1, False),
        ("not json", f"plan\n{PROMPT}{_MARKER}", 1, False),
        (f"{json.dumps({'undone': RECORD})} {{}}", f"plan\n{PROMPT}{_MARKER}", 1, False),
        ([{"undone": RECORD}], f"plan\n{PROMPT}{_MARKER}", 1, False),
        ({"applied": False}, f"plan\n{PROMPT}{_MARKER}", 1, False),
        ({"undone": None}, f"plan\n{PROMPT}{_MARKER}", 1, False),
        ({"undone": 20261019}, f"plan\n{PROMPT}{_MARKER}", 1, False),
        ({"undone": "-rf"}, f"plan\n{PROMPT}{_MARKER}", 1, False),
        ({"undone": "a b"}, f"plan\n{PROMPT}{_MARKER}", 1, False),
        ({"undone": "x" * 129}, f"plan\n{PROMPT}{_MARKER}", 1, False),
        ({"target_sha256": SHA}, f"plan\n{PROMPT}{_MARKER}", 1, False),
    ],
    ids=[
        "constructed: declined, exit 1",
        "constructed: declined, CRLF line ends",
        "constructed: declined, the line alone",
        "constructed: the line, exit 0",
        "constructed: the line, exit 2",
        "constructed: the line, exit 3",
        "constructed: a refusal, exit 3",
        "constructed: exit 1 without the line",
        "constructed: the line not last",
        "constructed: the line inside another",
        "constructed: the line with no line end",
        "constructed: exit 1, nothing on stderr",
        "constructed: stdout not JSON",
        "constructed: two documents",
        "constructed: a list",
        "constructed: no pin field",
        "constructed: pin null",
        "constructed: pin a number",
        "constructed: pin an option",
        "constructed: pin with a space",
        "constructed: pin too long",
        "constructed: another command's pin field",
    ],
)
def test_undo_mints_a_token_only_for_exit_1_ended_by_the_declined_line(
    server: ui.UiServer,
    recorder: Recorder,
    stdout: object,
    stderr: str,
    code: int,
    minted: bool,
) -> None:
    recorder.reply(stdout, code, stderr=stderr)
    payload = _plan(server, UNDO)
    assert payload["exit_code"] == code
    # Whatever the outcome, it is returned as the CLI printed it.
    assert payload["stdout"] == (stdout if isinstance(stdout, str) else json.dumps(stdout))
    assert payload["stderr"] == stderr
    assert (_token(payload) is not None) is minted, payload["confirm"]


def test_sv_merge_reads_its_own_pin_field(server: ui.UiServer, recorder: Recorder) -> None:
    stderr = f"plan\nWrite the merge? [y/N]:\n{_MARKER}"
    params: dict[str, object] = {"file": "x.lua", "into": CHAR_A}
    recorder.reply({"undone": RECORD}, 1, stderr=stderr)
    assert _token(_plan(server, MERGE, params)) is None
    recorder.reply({"target_sha256": SHA}, 1, stderr=stderr)
    assert _token(_plan(server, MERGE, params)) is not None
    # Unresolved conflicts: exit 1 with the report and no line from `_confirm`.
    recorder.reply({"target_sha256": SHA, "written": False}, 1, stderr="")
    assert _token(_plan(server, MERGE, params)) is None


def test_the_confirmed_result_names_the_journal_record_and_the_undo_hint(
    server: ui.UiServer, recorder: Recorder
) -> None:
    recorder.reply({"dry_run": True}, 0, {"applied": True, "transaction": RECORD})
    token = _token(_plan(server, INSTALL, {"name": "lab"}))
    assert token is not None
    status, done = _confirm(server, INSTALL, token, {"name": "lab"})
    assert status == 200
    assert done["confirm"] == {
        "stage": "confirmed",
        "token": None,
        "note": f"Journaled as {RECORD}. `wowlab undo` reverses it.",
        "needs": None,
    }


@pytest.mark.parametrize(
    ("confirmed", "code"),
    [({"applied": True, "transaction": None}, 0), ({"transaction": RECORD}, 3), ("", 3)],
    ids=["constructed: no record", "constructed: exit 3", "constructed: a refusal"],
)
def test_a_confirmed_run_without_a_record_has_no_note(
    server: ui.UiServer, recorder: Recorder, confirmed: object, code: int
) -> None:
    recorder.reply({"dry_run": True}, 0, confirmed, code)
    token = _token(_plan(server, INSTALL, {"name": "lab"}))
    assert token is not None
    status, done = _confirm(server, INSTALL, token, {"name": "lab"})
    assert status == 200 and done["exit_code"] == code
    assert done["confirm"]["note"] is None and done["confirm"]["token"] is None


def test_a_confirmed_run_killed_at_its_limit_says_what_to_do(recorder: Recorder) -> None:
    """M14-03 decides it: the transaction cannot roll back in a killed
    process; its lock goes with the process and its snapshot holds every
    file, and the result says so (constructed: a run that outlives a short
    limit)."""
    recorder.reply({"dry_run": True}, 0, {"transaction": RECORD}, 0, csleep=60)
    with _serving(wowlab_argv=recorder.argv, timeout_s=8) as served:
        token = _token(_plan(served, RESTORE, {"snapshot_id": "abc"}))
        assert token is not None
        status, done = _confirm(served, RESTORE, token, {"snapshot_id": "abc"})
    assert status == 200 and done["timed_out"] is True
    note = done["confirm"]["note"]
    assert note == ui._KILLED_NOTE
    for words in ("lock", "snapshot", "undo", "state open"):
        assert words in note, words


def test_a_killed_store_only_run_does_not_point_at_undo(recorder: Recorder) -> None:
    """`snap gc` (and `doctor`) take no pre-write snapshot and journal
    nothing: a killed run of one says it changed only the store, and nothing
    about a snapshot or `undo` (review S3; constructed: a run that outlives
    a short limit)."""
    recorder.reply({"dry_run": True, "unreferenced": []}, 0, {"removed": []}, 0, csleep=60)
    with _serving(wowlab_argv=recorder.argv, timeout_s=8) as served:
        token = _token(_plan(served, GC))
        assert token is not None
        status, done = _confirm(served, GC, token)
    assert status == 200 and done["timed_out"] is True
    note = done["confirm"]["note"]
    assert note == ui._KILLED_STORE_NOTE
    assert "store" in note and "undo" not in note and "snapshot" not in note


def test_each_declined_note_says_exactly_what_its_pin_checks(
    server: ui.UiServer, recorder: Recorder
) -> None:
    """`undo` is refused over a newer record and `sv merge` over a changed
    target, and neither note claims more (review S1: the source of `sv
    merge` is not pinned; M13-74)."""
    report, stderr = _declined({"undone": RECORD})
    recorder.reply(report, 1, stderr=stderr)
    note = _plan(server, UNDO)["confirm"]["note"]
    assert f"--expect {RECORD}" in note and "another change has been journaled" in note
    recorder.reply({"target_sha256": SHA}, 1, stderr=f"plan\n{_MARKER}")
    note = _plan(server, MERGE, {"file": "x.lua", "into": CHAR_A})["confirm"]["note"]
    assert f"--expect {SHA}" in note and "if the target file has changed" in note
    for note in ui._PIN_CHECKS.values():
        assert "what the plan read" not in note


def test_the_page_shows_the_confirmed_note_and_no_confirm(tmp_path: Path) -> None:
    note = f"Journaled as {RECORD}. `wowlab undo` reverses it."
    replies = {
        "snap gc": _result(
            {"dry_run": True}, {"stage": "plan", "token": "tok-3", "note": None, "needs": None}
        ),
        "snap gc confirm": _result(
            {"dry_run": False}, {"stage": "confirmed", "token": None, "note": note, "needs": None}
        ),
    }
    out = _drive(tmp_path, replies, [{"pick": "snap gc"}, {"click": "run"}, {"click": "confirm"}])
    _, planned, confirmed = out["steps"]
    assert not planned["confirmHidden"]
    assert confirmed["status"].startswith("Confirmed: exit code 0")
    assert confirmed["note"] == note and not confirmed["boxHidden"]
    assert confirmed["confirmHidden"]


# ─── the real CLI, on the captured install copied into tmp_path ──────────────

_SHIM = """\
import json, sys, pathlib
data = pathlib.Path(sys.argv.pop(1))
log = pathlib.Path(sys.argv.pop(1))
with log.open("a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
import platformdirs, psutil
platformdirs.user_data_path = lambda *a, **k: data
psutil.process_iter = lambda *a, **k: iter(())
from wowlab_core.cli import app
app(prog_name="wowlab", args=sys.argv[1:])
"""


class Real:
    """The server over the real CLI, its user data directory, and the argv
    log the shim keeps."""

    def __init__(self, server: ui.UiServer, data: Path, log: Path, root: Path) -> None:
        self.server = server
        self.data = data
        self.log = log
        self.root = root
        self.flavor = root / FLAVOR

    @property
    def store(self) -> Path:
        return self.data / "store"

    def history(self) -> tuple[guard.HistoryRecord, ...]:
        if not self.store.exists():
            return ()
        return guard.history(store=self.store)

    def argvs(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def run(self, path: tuple[str, ...], params: dict[str, object]) -> dict[str, Any]:
        """A leaf that is not in the step: one run, which must exit 0."""
        status, payload = _post(self.server, {"command": list(path), "params": params})
        assert status == 200, payload
        assert payload["exit_code"] == 0, payload["stderr"]
        assert "confirm" not in payload
        return payload

    def plan(self, path: tuple[str, ...], params: dict[str, object]) -> dict[str, Any]:
        return _plan(self.server, path, params)

    def confirm(
        self, path: tuple[str, ...], token: str, params: dict[str, object]
    ) -> dict[str, Any]:
        status, payload = _confirm(self.server, path, token, params)
        assert status == 200, payload
        return payload

    def through(self, path: tuple[str, ...], params: dict[str, object]) -> dict[str, Any]:
        """Plan, then confirm; the confirmed run's report. The plan run's
        argv carries no `--yes` and the confirmed one exactly one."""
        logged = len(self.argvs())
        plan = self.plan(path, params)
        token = _token(plan)
        assert token is not None, (plan["exit_code"], plan["stderr"])
        done = self.confirm(path, token, params)
        assert done["exit_code"] == 0, done["stderr"]
        plan_argv, confirmed_argv = self.argvs()[logged:]
        assert plan_argv[: len(path)] == list(path)
        assert "--yes" not in plan_argv and "-y" not in plan_argv, plan_argv
        assert not any(a.startswith("--expect") for a in plan_argv), plan_argv
        assert confirmed_argv.count("--yes") == 1 and "--dry-run" not in confirmed_argv
        report: dict[str, Any] = json.loads(done["stdout"])
        record = report["transaction"]
        assert done["confirm"]["note"] == f"Journaled as {record}. `wowlab undo` reverses it."
        return report

    def undo(self) -> dict[str, Any]:
        """`undo` through the UI: its plan names the newest record, and the
        confirmed run carries it as `--expect`."""
        newest = self.history()[-1].id
        report = self.through(UNDO, {})
        assert report["undone"] == newest
        assert self.argvs()[-1] == ["undo", "--json", "--yes", f"--expect={newest}"]
        return report


@pytest.fixture
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Real]:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    data = tmp_path / "m14-03-userdata"
    log = tmp_path / "m14-03-argv.jsonl"
    root = _install(tmp_path, second=False)
    with _serving(wowlab_argv=[sys.executable, "-c", _SHIM, str(data), str(log)]) as served:
        yield Real(served, data, log, root)


def _edit(path: Path, old: bytes, new: bytes) -> bytes:
    """A constructed edit of a real file; the bytes it now holds."""
    data = path.read_bytes()
    assert old in data
    edited = data.replace(old, new, 1)
    path.write_bytes(edited)
    return edited


_SYNDICATOR_OLD = b'"show_tooltips_on_shift"] = false'
_SYNDICATOR_NEW = b'"show_tooltips_on_shift"] = true'


def _went_through_guard(real: Real, report: dict[str, Any], label_starts: str) -> None:
    """A snapshot was taken first, and the journal has the entry."""
    record = real.history()[-1]
    assert record.id == report["transaction"]
    assert record.state == "committed"
    assert record.label.startswith(label_starts), record.label
    manifest = snapshot.SnapshotStore(real.store).show(record.snapshot_id)
    assert manifest.flavor_folder == FLAVOR


def test_snap_restore_through_the_ui(real: Real) -> None:
    root = {"root": str(real.root)}
    snap_id = json.loads(real.run(("snap", "create"), {**root, "message": "m14-03"})["stdout"])[
        "id"
    ]
    original = (real.flavor / SYNDICATOR).read_bytes()
    _edit(real.flavor / SYNDICATOR, _SYNDICATOR_OLD, _SYNDICATOR_NEW)
    edited = _state(real.root)
    report = real.through(RESTORE, {**root, "snapshot_id": snap_id})
    assert report["applied"] is True and report["dry_run"] is False
    assert (real.flavor / SYNDICATOR).read_bytes() == original
    _went_through_guard(real, report, f"restore {snap_id}")
    real.undo()
    assert _state(real.root) == edited


def test_profile_apply_through_the_ui(real: Real) -> None:
    root = {"root": str(real.root)}
    real.run(("profile", "save"), {**root, "name": "m14-03", "preset": ["addons"]})
    original = (real.flavor / SYNDICATOR).read_bytes()
    _edit(real.flavor / SYNDICATOR, _SYNDICATOR_OLD, _SYNDICATOR_NEW)
    edited = _state(real.root)
    report = real.through(APPLY, {**root, "name": "m14-03"})
    assert report["applied"] is True and report["profile"] == "m14-03"
    assert (real.flavor / SYNDICATOR).read_bytes() == original
    _went_through_guard(real, report, "profile apply m14-03")
    real.undo()
    assert _state(real.root) == edited


def test_addon_install_and_remove_through_the_ui(real: Real) -> None:
    root = {"root": str(real.root)}
    before = _state(real.root)
    report = real.through(INSTALL, {**root, "name": "lab"})
    assert report["applied"] is True and report["plan"]
    _went_through_guard(real, report, "addon install lab")
    installed = _state(real.root)
    assert installed != before
    assert (real.flavor / "Interface" / "AddOns" / "WowLab" / "WowLab.toc").is_file()
    report = real.through(REMOVE, {**root, "name": "lab"})
    assert report["applied"] is True
    _went_through_guard(real, report, "addon remove lab")
    assert _state(real.root) == before
    real.undo()  # the removal
    assert _state(real.root) == installed
    real.undo()  # the undo of the removal: `wowlab undo` reverses an undo too
    assert _state(real.root) == before


def test_sv_merge_through_the_ui(real: Real) -> None:
    params: dict[str, object] = {
        "root": str(real.root),
        "file": DBM_NAME,
        "into": CHAR_A,
        "key": [KEY_COPY],
    }
    target = real.flavor / DBM
    before = target.read_bytes()
    plan = real.plan(MERGE, params)
    assert plan["exit_code"] == 1
    # The plan is the stderr text before `_confirm`'s line.
    assert plan["stderr"].endswith(f"Write the merge? [y/N]:\n{cli.CONFIRM_DECLINED}\n")
    assert f"replace  {DBM}" in plan["stderr"]
    shown = json.loads(plan["stdout"])
    assert shown["written"] is False
    assert shown["target_sha256"] == hashlib.sha256(before).hexdigest()
    token = _token(plan)
    assert token is not None
    done = real.confirm(MERGE, token, params)
    assert done["exit_code"] == 0, done["stderr"]
    assert f"--expect={shown['target_sha256']}" in real.argvs()[-1]
    report = json.loads(done["stdout"])
    assert report["written"] is True
    assert target.read_bytes() != before
    _went_through_guard(real, report, "sv merge")
    real.undo()
    assert target.read_bytes() == before


def test_undo_through_the_ui(real: Real) -> None:
    root = {"root": str(real.root)}
    before = _state(real.root)
    real.through(INSTALL, {**root, "name": "lab"})
    installed = _state(real.root)
    plan = real.plan(UNDO, {})
    assert plan["exit_code"] == 1
    assert plan["stderr"].endswith(f"Undo it? [y/N]:\n{cli.CONFIRM_DECLINED}\n")
    assert plan["stderr"].startswith(f"Undo {real.history()[-1].id} (")
    assert _state(real.root) == installed  # the plan changed nothing
    token = _token(plan)
    assert token is not None
    done = real.confirm(UNDO, token, {})
    assert done["exit_code"] == 0, done["stderr"]
    report = json.loads(done["stdout"])
    assert report["applied"] is True
    assert _state(real.root) == before
    # The undo is a transaction of its own: snapshot first, journaled.
    _went_through_guard(real, report, "undo: addon install lab")
    real.undo()
    assert _state(real.root) == installed


# ─── drift: the confirmed run applies the plan shown, or nothing ─────────────


def test_undo_is_refused_when_a_newer_record_was_journaled_after_the_plan(real: Real) -> None:
    root = {"root": str(real.root)}
    real.through(INSTALL, {**root, "name": "lab"})
    shown = real.history()[-1].id
    plan = real.plan(UNDO, {})
    token = _token(plan)
    assert token is not None and json.loads(plan["stdout"])["undone"] == shown
    # A second transaction between plan and confirm.
    newer = real.through(REMOVE, {**root, "name": "lab"})["transaction"]
    state = _state(real.root)
    done = real.confirm(UNDO, token, {})
    assert done["exit_code"] == 3, done["stderr"]
    assert real.argvs()[-1] == ["undo", "--json", "--yes", f"--expect={shown}"]
    assert shown in done["stderr"] and newer in done["stderr"]
    assert done["confirm"]["note"] is None and done["confirm"]["token"] is None
    # The newer record is untouched: still the newest, still committed.
    assert [r.id for r in real.history()][-1] == newer
    assert real.history()[-1].state == "committed"
    assert _state(real.root) == state


def test_sv_merge_is_refused_when_the_target_changed_after_the_plan(real: Real) -> None:
    params: dict[str, object] = {
        "root": str(real.root),
        "file": DBM_NAME,
        "into": CHAR_A,
        "key": [KEY_COPY],
    }
    target = real.flavor / DBM
    plan = real.plan(MERGE, params)
    token = _token(plan)
    assert token is not None
    shown = json.loads(plan["stdout"])["target_sha256"]
    # The target's bytes change between plan and confirm (constructed edit).
    original = target.read_bytes()
    at = original.index(b'["Enabled"] = true,', original.index(b'\r\n["Unknown"] = {'))
    changed = original[:at] + b'["Enabled"] = false,' + original[at + 19 :]
    target.write_bytes(changed)
    records = real.history()
    done = real.confirm(MERGE, token, params)
    assert done["exit_code"] == 3, done["stderr"]
    assert f"--expect={shown}" in real.argvs()[-1]
    assert target.read_bytes() == changed  # nothing written
    assert real.history() == records  # nothing journaled


# ─── refusals: shown as the CLI printed them, with no Confirm ────────────────


def test_a_guard_refusal_reaches_the_page_as_exit_3_with_no_token(real: Real) -> None:
    root = {"root": str(real.root)}
    snap_id = json.loads(real.run(("snap", "create"), root)["stdout"])["id"]
    _edit(real.flavor / SYNDICATOR, _SYNDICATOR_OLD, _SYNDICATOR_NEW)
    state = _state(real.root)
    # The store is busy: held here, as another wowlab would hold it.
    with guard.store_lock(real.store):
        plan = real.plan(RESTORE, {**root, "snapshot_id": snap_id})
    assert plan["exit_code"] == 3
    assert "refused by the write gate" in plan["stderr"]
    assert plan["confirm"] == {"stage": "plan", "token": None, "note": None, "needs": None}
    assert _state(real.root) == state
    # A confirmed run refused the same way offers nothing either.
    real.through(INSTALL, {**root, "name": "lab"})
    installed = _state(real.root)
    token = _token(real.plan(UNDO, {}))
    assert token is not None
    with guard.store_lock(real.store):
        done = real.confirm(UNDO, token, {})
    assert done["exit_code"] == 3
    assert "refused by the write gate" in done["stderr"]
    assert done["confirm"] == {"stage": "confirmed", "token": None, "note": None, "needs": None}
    assert _state(real.root) == installed


def test_nothing_to_undo_offers_no_confirm(real: Real) -> None:
    plan = real.plan(UNDO, {})
    assert plan["exit_code"] == 1
    assert not plan["stderr"].endswith(f"{cli.CONFIRM_DECLINED}\n")
    assert _token(plan) is None
