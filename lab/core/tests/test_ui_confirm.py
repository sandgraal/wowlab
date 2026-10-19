"""`wowlab ui`: the confirm step on `snap gc` and `doctor --yes` (M14-02).

ADR-0029 "Writes" and `docs/LAB_PLAN.md` §16.1. A `--yes` leaf of the
confirm step runs first as its plan, never with `--yes`, with stdin closed:
`snap gc` with `--dry-run --json`, `doctor` with `--json`. Only a plan that
exited 0 with its shape's marker gets a one-time token; a request carrying
it runs the bound confirmed argv (the plan's, `--dry-run` taken out, `--yes`
added as its own element) once, and only for the same plan argv.

Three kinds of run here, none touching a real install:
- a recorder (constructed) stands in for `wowlab`: it records its argv, its
  environment and what stdin gave it, and prints a reply the test chose for
  a plan (no `--yes` in its argv) or a confirmed run;
- the real CLI, through a shim that redirects the user data directory into
  `tmp_path` and empties the process table, on an install copied from the
  captured tree (`fixtures/macos/`, as `test_cli` does) or on a constructed
  second flavor beside it; `--offline`, so nothing reaches the network;
- the page under Node with a small DOM stub (skipped when `node` is not on
  PATH): Confirm appears only with a token and sends the plan's own
  parameters with it.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import install, ui

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SECOND = "_constructed_"  # a second flavor folder (constructed)

GC = ("snap", "gc")
DOCTOR = ("doctor",)

# ─── the recorder (constructed) ──────────────────────────────────────────────

_RECORDER = """\
import json, os, sys
argv = sys.argv[1:]
stdin = sys.stdin.read()
with open({calls!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": argv, "env": dict(os.environ), "stdin": stdin,
                        "tty": sys.stdin.isatty()}}) + "\\n")
with open({replies!r}, encoding="utf-8") as f:
    replies = json.load(f)
reply = replies["confirmed" if "--yes" in argv else "plan"]
sys.stdout.write(reply["stdout"])
sys.stdout.flush()
sys.stderr.write(reply.get("stderr", ""))
sys.stderr.flush()
if reply.get("sleep"):
    import time
    time.sleep(reply["sleep"])
sys.exit(reply["code"])
"""


@dataclass
class Recorder:
    argv: list[str]
    calls_file: Path
    replies_file: Path

    def reply(
        self,
        plan: object,
        code: int = 0,
        confirmed: object = None,
        ccode: int = 0,
        *,
        stderr: str = "",
        csleep: float = 0,
    ) -> None:
        def text(value: object) -> str:
            return value if isinstance(value, str) else json.dumps(value)

        replies = {
            "plan": {"stdout": text(plan), "code": code, "stderr": stderr},
            "confirmed": {
                "stdout": text({"done": True} if confirmed is None else confirmed),
                "code": ccode,
                "sleep": csleep,
            },
        }
        self.replies_file.write_text(json.dumps(replies), encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        if not self.calls_file.exists():
            return []
        lines = self.calls_file.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]

    def argvs(self) -> list[list[str]]:
        return [call["argv"] for call in self.calls()]


@pytest.fixture
def recorder(tmp_path: Path) -> Recorder:
    calls = tmp_path / "m14-02-calls.jsonl"
    replies = tmp_path / "m14-02-replies.json"
    script = tmp_path / "m14_02_fake_wowlab_constructed.py"
    script.write_text(_RECORDER.format(calls=str(calls), replies=str(replies)), encoding="utf-8")
    rec = Recorder([sys.executable, str(script)], calls, replies)
    rec.reply({"dry_run": True, "unreferenced": ["a" * 64], "unreferenced_bytes": 9})
    return rec


@contextmanager
def _serving(**kwargs: Any) -> Iterator[ui.UiServer]:
    server = ui.make_server(timeout_s=120, **kwargs)
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


def _post(server: ui.UiServer, body: object) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=120)
    try:
        data = json.dumps(body).encode("utf-8")
        conn.request(
            "POST",
            f"/api/run?token={server.token}",
            body=data,
            headers={"Content-Type": "application/json"},
        )
        reply = conn.getresponse()
        payload: dict[str, Any] = json.loads(reply.read())
        return reply.status, payload
    finally:
        conn.close()


def _plan(
    server: ui.UiServer, path: tuple[str, ...], params: dict[str, object] | None = None
) -> dict[str, Any]:
    status, payload = _post(server, {"command": list(path), "params": params or {}})
    assert status == 200, payload
    return payload


def _confirm(
    server: ui.UiServer, path: tuple[str, ...], token: str, params: dict[str, object] | None = None
) -> tuple[int, dict[str, Any]]:
    return _post(server, {"command": list(path), "params": params or {}, "confirm": token})


def _token(payload: dict[str, Any]) -> str | None:
    offer = payload["confirm"]
    assert offer["stage"] == "plan", offer
    token = offer["token"]
    assert token is None or isinstance(token, str)
    return token


# ─── the plan run ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [GC, DOCTOR], ids=" ".join)
def test_a_plan_run_never_carries_yes_and_runs_with_stdin_closed(
    server: ui.UiServer, recorder: Recorder, path: tuple[str, ...]
) -> None:
    _plan(server, path)
    (call,) = recorder.calls()
    assert "--yes" not in call["argv"] and "-y" not in call["argv"], call["argv"]
    # End of file at once, and no terminal on any platform (on Windows the
    # NUL device would report a terminal, so the server gives a closed pipe).
    assert call["stdin"] == "" and call["tty"] is False
    # A form cannot set `--yes` (or `--dry-run`) itself: they are the server's.
    for params in ({"yes": True}, {"yes": False}, {"dry_run": True}):
        status, payload = _post(server, {"command": list(path), "params": params})
        assert (status, payload) == (403, {"error": "unknown_parameter"}), params
    assert len(recorder.calls()) == 1


def test_snap_gc_plan_runs_with_dry_run_and_json(server: ui.UiServer, recorder: Recorder) -> None:
    payload = _plan(server, GC)
    assert recorder.argvs() == [["snap", "gc", "--dry-run", "--json"]]
    assert _token(payload) is not None


@pytest.mark.parametrize(
    ("stdout", "code", "minted"),
    [
        ({"dry_run": True, "unreferenced": [], "unreferenced_bytes": 0, "removed": []}, 0, True),
        ({"dry_run": True, "unreferenced": ["b" * 64], "unreferenced_bytes": 5}, 0, True),
        ({"dry_run": False, "unreferenced": [], "removed": []}, 0, False),
        ({"unreferenced": []}, 0, False),
        ({"dry_run": "true"}, 0, False),
        ({"dry_run": 1}, 0, False),
        ([{"dry_run": True}], 0, False),
        ("Would remove 1 object(s) (dry run).", 0, False),
        ('{"dry_run": true} {"dry_run": true}', 0, False),
        ({"dry_run": True}, 1, False),
        ({"dry_run": True}, 3, False),
        ("", 3, False),
    ],
    ids=[
        "constructed: dry run, nothing to remove",
        "constructed: dry run, one object",
        "constructed: dry_run false",
        "constructed: no dry_run",
        "constructed: dry_run a string",
        "constructed: dry_run a number",
        "constructed: a list",
        "constructed: text",
        "constructed: two documents",
        "constructed: exit 1",
        "constructed: exit 3 with the marker",
        "constructed: exit 3, a refusal",
    ],
)
def test_snap_gc_mints_a_token_only_for_a_dry_run_report(
    server: ui.UiServer, recorder: Recorder, stdout: object, code: int, minted: bool
) -> None:
    recorder.reply(stdout, code)
    payload = _plan(server, GC)
    assert payload["exit_code"] == code
    # Whatever the outcome, it is returned as the CLI printed it.
    assert payload["stdout"] == (stdout if isinstance(stdout, str) else json.dumps(stdout))
    assert (_token(payload) is not None) is minted, payload["confirm"]


def test_snap_gc_with_nothing_to_remove_is_still_offered_and_says_so(
    server: ui.UiServer, recorder: Recorder
) -> None:
    recorder.reply({"dry_run": True, "unreferenced": [], "unreferenced_bytes": 0, "removed": []})
    offer = _plan(server, GC)["confirm"]
    assert offer["token"]
    assert offer["note"].startswith("Nothing to remove.")
    assert offer["needs"] is None


def _doctor_report(first_run: object, problems: list[str] | None = None) -> dict[str, Any]:
    return {"problems": problems or [], "first_run": first_run}


def _first_run(outcome: str, chosen: str | None) -> dict[str, Any]:
    return {"outcome": outcome, "chosen": chosen, "refused": outcome == "failed"}


def test_doctor_plan_runs_with_json_alone(server: ui.UiServer, recorder: Recorder) -> None:
    recorder.reply(_doctor_report(None))
    _plan(server, DOCTOR)
    _plan(server, DOCTOR, {"offline": True, "flavor": "--yes", "root": "/r"})
    first, second = recorder.argvs()
    assert first == ["doctor", "--json"]
    assert second[0] == "doctor" and second[-1] == "--json"
    assert sorted(second[1:-1]) == sorted(["--root=/r", "--offline", "--flavor=--yes"])


@pytest.mark.parametrize(
    ("report", "code", "minted", "needs"),
    [
        (_doctor_report(_first_run("not_asked", FLAVOR)), 0, True, None),
        (_doctor_report(_first_run("not_asked", None)), 0, False, "flavor"),
        (_doctor_report(_first_run("not_asked", "")), 0, False, None),
        (_doctor_report(_first_run("choose_flavor", None)), 2, False, None),
        (_doctor_report(_first_run("taken", FLAVOR)), 0, False, None),
        (_doctor_report(_first_run("declined", FLAVOR)), 0, False, None),
        (_doctor_report(_first_run("no_flavors", None)), 0, False, None),
        (_doctor_report(None), 0, False, None),
        (_doctor_report(_first_run("not_asked", FLAVOR), ["store: damaged"]), 1, False, None),
        (_doctor_report(_first_run("failed", FLAVOR)), 3, False, None),
        (_doctor_report(_first_run("not_asked", FLAVOR)), 3, False, None),
        ({"first_run": "not_asked"}, 0, False, None),
    ],
    ids=[
        "constructed: not_asked with a flavor",
        "constructed: not_asked with no flavor",
        "constructed: not_asked with an empty flavor",
        "constructed: choose_flavor",
        "constructed: taken",
        "constructed: declined",
        "constructed: no flavors",
        "constructed: not a first run",
        "constructed: a problems exit",
        "constructed: exit 3",
        "constructed: exit 3 with the marker",
        "constructed: first_run not an object",
    ],
)
def test_doctor_mints_a_token_only_for_not_asked_with_a_flavor(
    server: ui.UiServer,
    recorder: Recorder,
    report: dict[str, Any],
    code: int,
    minted: bool,
    needs: str | None,
) -> None:
    recorder.reply(report, code)
    payload = _plan(server, DOCTOR)
    assert payload["exit_code"] == code
    assert payload["stdout"] == json.dumps(report)
    offer = payload["confirm"]
    assert (offer["token"] is not None) is minted, offer
    assert offer["needs"] == needs, offer
    if needs is not None:
        assert "--flavor" in offer["note"]


# ─── the token ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "params", "plan_argv", "confirmed_argv"),
    [
        (GC, {}, ["snap", "gc", "--dry-run", "--json"], ["snap", "gc", "--json", "--yes"]),
        (DOCTOR, {}, ["doctor", "--json"], ["doctor", "--json", "--yes"]),
        (
            DOCTOR,
            {"root": "/a b", "offline": True, "flavor": "--yes"},
            None,
            None,
        ),
    ],
    ids=["snap gc", "doctor", "doctor with options"],
)
def test_the_confirmed_argv_is_the_plan_without_dry_run_and_with_yes_appended(
    server: ui.UiServer,
    recorder: Recorder,
    path: tuple[str, ...],
    params: dict[str, object],
    plan_argv: list[str] | None,
    confirmed_argv: list[str] | None,
) -> None:
    if path == DOCTOR:
        recorder.reply(_doctor_report(_first_run("not_asked", FLAVOR)))
    token = _token(_plan(server, path, params))
    assert token is not None
    status, payload = _confirm(server, path, token, params)
    assert status == 200, payload
    assert payload["confirm"] == {"stage": "confirmed", "token": None, "note": None, "needs": None}
    plan, confirmed = recorder.argvs()
    if plan_argv is not None:
        assert (plan, confirmed) == (plan_argv, confirmed_argv)
    without = [a for a in plan if a != "--dry-run"]
    assert confirmed == [*without, "--yes"]
    assert confirmed.count("--yes") == 1 and "--dry-run" not in confirmed
    # The confirmed run, too, has stdin closed.
    assert recorder.calls()[1]["stdin"] == ""


def test_a_token_confirms_exactly_once(server: ui.UiServer, recorder: Recorder) -> None:
    token = _token(_plan(server, GC))
    assert token is not None
    assert _confirm(server, GC, token)[0] == 200
    assert _confirm(server, GC, token) == (403, {"error": "confirm"})
    assert len(recorder.calls()) == 2


def test_a_token_confirms_only_its_own_argv(server: ui.UiServer, recorder: Recorder) -> None:
    recorder.reply(_doctor_report(_first_run("not_asked", FLAVOR)))
    token = _token(_plan(server, DOCTOR, {"offline": True}))
    assert token is not None
    # Another argv: other parameters, or another leaf of the step.
    assert _confirm(server, DOCTOR, token, {"offline": True, "flavor": "x"}) == (
        403,
        {"error": "confirm"},
    )
    # Presented once with the wrong argv, it is spent.
    assert _confirm(server, DOCTOR, token, {"offline": True}) == (403, {"error": "confirm"})
    token = _token(_plan(server, DOCTOR, {"offline": True}))
    assert token is not None
    assert _confirm(server, GC, token) == (403, {"error": "confirm"})
    assert _confirm(server, DOCTOR, token, {}) == (403, {"error": "confirm"})
    assert len(recorder.calls()) == 2  # the two plans, and nothing confirmed


def test_an_unknown_token_runs_nothing(server: ui.UiServer, recorder: Recorder) -> None:
    for token in ["x" * 43, server.token, "a"]:
        assert _confirm(server, GC, token) == (403, {"error": "confirm"}), token
    for bad in [None, 1, True, [], {}, "", "x" * 129]:
        status, payload = _confirm(server, GC, bad)  # type: ignore[arg-type]
        assert (status, payload) == (400, {"error": "bad_request"}), bad
    assert recorder.calls() == []


def test_a_token_is_not_the_servers_and_never_reaches_a_run(
    server: ui.UiServer, recorder: Recorder
) -> None:
    token = _token(_plan(server, GC))
    assert token is not None and token != server.token
    assert len(token) >= 43  # secrets.token_urlsafe(32)
    _confirm(server, GC, token)
    for call in recorder.calls():
        seen = json.dumps(call["argv"]) + json.dumps(call["env"])
        assert token not in seen and server.token not in seen


def test_a_token_from_a_plan_with_nothing_to_remove_confirms(
    server: ui.UiServer, recorder: Recorder
) -> None:
    recorder.reply({"dry_run": True, "unreferenced": [], "unreferenced_bytes": 0, "removed": []})
    token = _token(_plan(server, GC))
    assert token is not None
    assert _confirm(server, GC, token)[0] == 200


def test_only_the_newest_tokens_are_kept(server: ui.UiServer, recorder: Recorder) -> None:
    tokens = [_token(_plan(server, GC)) for _ in range(ui.MAX_PENDING_CONFIRMS + 1)]
    assert all(tokens) and len(set(tokens)) == len(tokens)
    assert _confirm(server, GC, str(tokens[0])) == (403, {"error": "confirm"})
    assert _confirm(server, GC, str(tokens[-1]))[0] == 200


def test_tokens_expire_with_the_server(recorder: Recorder) -> None:
    with _serving(wowlab_argv=recorder.argv) as first:
        token = _token(_plan(first, GC))
    assert token is not None
    with _serving(wowlab_argv=recorder.argv) as second:
        assert _confirm(second, GC, token) == (403, {"error": "confirm"})


# ─── an excluded leaf ────────────────────────────────────────────────────────
#
# Since M14-03 every `--yes` leaf is in the step (test_ui_confirm_writes.py);
# `ui` is the one excluded leaf left, and a token does not open it either.


@pytest.mark.parametrize("path", [("ui",)], ids=" ".join)
def test_an_excluded_leaf_is_refused_before_anything_runs(
    server: ui.UiServer, recorder: Recorder, path: tuple[str, ...]
) -> None:
    listed = {tuple(c["path"]): c for c in _commands(server)}
    assert listed[path]["excluded"] == "the UI itself"
    assert listed[path]["confirm"] is None
    for params in ({}, {"yes": True}, {"dry_run": True}):
        status, payload = _post(server, {"command": list(path), "params": params})
        assert (status, payload) == (403, {"error": "excluded"}), params
    # Nor with a real token minted for another plan, which it spends.
    token = _token(_plan(server, GC))
    assert token is not None
    calls = len(recorder.calls())
    assert _confirm(server, path, token) == (403, {"error": "excluded"})
    assert _confirm(server, GC, token) == (403, {"error": "confirm"})
    assert len(recorder.calls()) == calls


def test_a_token_on_a_read_leaf_runs_nothing(server: ui.UiServer, recorder: Recorder) -> None:
    token = _token(_plan(server, GC))
    assert token is not None
    calls = len(recorder.calls())
    assert _confirm(server, ("snap", "list"), token) == (403, {"error": "confirm"})
    assert _confirm(server, ("no", "such"), "x" * 43) == (403, {"error": "not_a_command"})
    assert len(recorder.calls()) == calls


def _commands(server: ui.UiServer) -> list[dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=60)
    try:
        conn.request("GET", f"/api/commands?token={server.token}")
        listed: list[dict[str, Any]] = json.loads(conn.getresponse().read())["commands"]
        return listed
    finally:
        conn.close()


def test_the_command_list_names_the_shape_and_hides_yes(server: ui.UiServer) -> None:
    listed = {tuple(c["path"]): c for c in _commands(server)}
    assert listed[GC]["confirm"] == "dry_run" and listed[GC]["excluded"] is None
    assert listed[DOCTOR]["confirm"] == "doctor" and listed[DOCTOR]["excluded"] is None
    assert [p["name"] for p in listed[GC]["params"]] == []
    names = {p["name"] for p in listed[DOCTOR]["params"]}
    assert "yes" not in names and "flavor" in names


# ─── the real CLI, on constructed installs ───────────────────────────────────

# Runs the real `wowlab` with the user data directory in `tmp_path` and an
# empty process table (constructed: no client is running).
_SHIM = """\
import sys, pathlib
data = pathlib.Path(sys.argv.pop(1))
import platformdirs, psutil
platformdirs.user_data_path = lambda *a, **k: data
psutil.process_iter = lambda *a, **k: iter(())
from wowlab_core.cli import app
app(prog_name="wowlab", args=sys.argv[1:])
"""


def _install(tmp_path: Path, second: bool) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    if second:
        copy = root / SECOND
        shutil.copytree(root / FLAVOR, copy)
        info = copy / install.FLAVOR_INFO
        info.write_bytes(info.read_bytes().replace(b"wow_classic_beta", b"wow_constructed"))
    return root


def _state(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def real_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[ui.UiServer, Path]]:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    data = tmp_path / "m14-02-userdata"
    with _serving(wowlab_argv=[sys.executable, "-c", _SHIM, str(data)]) as served:
        yield served, data


def test_doctor_confirms_a_first_snapshot_into_the_store_and_not_the_install(
    real_cli: tuple[ui.UiServer, Path], tmp_path: Path
) -> None:
    server, data = real_cli
    root = _install(tmp_path, second=False)
    before = _state(root)
    params: dict[str, object] = {"root": str(root), "offline": True}
    plan = _plan(server, DOCTOR, params)
    assert plan["exit_code"] == 0, plan["stderr"]
    first = json.loads(plan["stdout"])["first_run"]
    assert (first["outcome"], first["chosen"]) == ("not_asked", FLAVOR)
    assert not (data / "snapshots").exists() or not any((data / "snapshots").iterdir())
    token = _token(plan)
    assert token is not None
    status, done = _confirm(server, DOCTOR, token, params)
    assert status == 200, done
    assert done["exit_code"] == 0, done["stderr"]
    report = json.loads(done["stdout"])
    assert report["first_run"]["outcome"] == "taken"
    assert report["first_run"]["snapshot"]["flavor_folder"] == FLAVOR
    assert data.is_dir() and any(data.rglob("*.json"))
    assert _state(root) == before  # the store, never the install
    # Not a first run any more: the next plan offers nothing.
    again = _plan(server, DOCTOR, params)
    assert json.loads(again["stdout"])["first_run"] is None
    assert _token(again) is None


def test_doctor_on_a_two_flavor_install_asks_for_a_flavor_and_offers_no_token(
    real_cli: tuple[ui.UiServer, Path], tmp_path: Path
) -> None:
    server, _ = real_cli
    root = _install(tmp_path, second=True)
    params: dict[str, object] = {"root": str(root), "offline": True}
    plan = _plan(server, DOCTOR, params)
    assert plan["exit_code"] == 0, plan["stderr"]
    first = json.loads(plan["stdout"])["first_run"]
    assert (first["outcome"], first["chosen"]) == ("not_asked", None)
    offer = plan["confirm"]
    assert offer["token"] is None and offer["needs"] == "flavor"
    # Named, the same install is offered Confirm for that flavor.
    named = _plan(server, DOCTOR, {**params, "flavor": SECOND})
    first = json.loads(named["stdout"])["first_run"]
    assert (first["outcome"], first["chosen"]) == ("not_asked", SECOND)
    assert _token(named) is not None


def test_snap_gc_plan_and_confirm_on_an_empty_store(real_cli: tuple[ui.UiServer, Path]) -> None:
    server, _ = real_cli
    plan = _plan(server, GC)
    assert plan["exit_code"] == 0, plan["stderr"]
    assert json.loads(plan["stdout"])["dry_run"] is True
    offer = plan["confirm"]
    assert offer["token"] and offer["note"].startswith("Nothing to remove.")
    status, done = _confirm(server, GC, offer["token"])
    assert status == 200 and done["exit_code"] == 0, done
    # `snap gc` prints no undo hint, and the server adds none.
    assert "undo" not in done["stdout"].lower() and "undo" not in json.dumps(done["confirm"])


# ─── the page, under Node ────────────────────────────────────────────────────

_TEMPLATES = resources.files(ui) / "templates"

# A small DOM stub (constructed): the page's own ids from page.html, text
# only, and a fetch that answers from the scenario.
_HARNESS = r"""
"use strict";
const fs = require("fs");
const vm = require("vm");
const scenario = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.parentElement = null; this._text = "";
    this.attrs = {}; this.dataset = {}; this.listeners = {}; this.className = "";
    this.hidden = false; this.disabled = false; this.value = ""; this.title = "";
    this.checked = false; this.type = ""; this.id = ""; this.open = false; this.focused = 0;
    const self = this;
    this.classList = {
      add(...cs) { const s = new Set(self.className.split(" ").filter(Boolean)); for (const c of cs) s.add(c); self.className = [...s].join(" "); },
      remove(...cs) { self.className = self.className.split(" ").filter((x) => x && !cs.includes(x)).join(" "); },
      contains(c) { return self.className.split(" ").includes(c); },
      toggle(c, on) { if (on === undefined) on = !this.contains(c); if (on) this.add(c); else this.remove(c); return on; },
    };
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(t) { this._text = String(t); this.children = []; }
  set innerHTML(_) { throw new Error("HTML sink"); }
  appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  replaceChildren(...cs) { this.children = []; this._text = ""; for (const c of cs) this.appendChild(c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  fire(ev) { for (let at = this; at; at = at.parentElement) for (const fn of at.listeners[ev] || []) fn({ type: ev, target: this, preventDefault() {}, stopPropagation() {} }); }
  focus() { this.focused += 1; }
  all() { const o = []; for (const c of this.children) o.push(c, ...c.all()); return o; }
  querySelectorAll(sel) { return this.all().filter((e) => e.tagName === sel); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  get options() { return this.children.filter((c) => c.tagName === "option"); }
}
const ids = {};
for (const id of scenario.ids) { const el = new El("div"); el.id = id; ids[id] = el; }
for (const id of scenario.hidden) ids[id].hidden = true;
const requests = [];
const fetchStub = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  requests.push({ url: String(url), body: body });
  const answer = (obj, status) => ({ ok: status === 200, status: status, json: async () => obj });
  if (String(url).startsWith("/api/commands?")) return answer(scenario.commands, 200);
  const key = body.command.join(" ") + (body.confirm ? " confirm" : "");
  const reply = scenario.replies[key];
  if (!reply) return answer({ error: "excluded" }, 403);
  return answer(reply, 200);
};
const doc = {
  getElementById: (id) => (id in ids ? ids[id] : null),
  createElement: (t) => new El(String(t).toLowerCase()),
  createTextNode: (t) => Object.assign(new El("#text"), { _text: String(t) }),
  createDocumentFragment: () => new El("#fragment"),
  querySelectorAll: () => [],
  addEventListener() {},
};
const win = { location: { search: "?token=t" }, addEventListener() {} };
const ctx = { window: win, document: doc, navigator: {}, URLSearchParams, fetch: fetchStub, console,
  Set, Map, Promise, JSON, Error, setTimeout: (fn) => setImmediate(fn), clearTimeout() {},
  requestAnimationFrame: (fn) => setImmediate(fn), queueMicrotask };
vm.createContext(ctx);
const tick = async () => { for (let i = 0; i < 50; i++) await new Promise((r) => setImmediate(r)); };
const button = (label) => ids.commands.all().find((e) => e.tagName === "button" && e.textContent === label);
const field = (name) => ids.fields.all().find((e) => e.id === "param-" + name);
function view() {
  return { status: ids.status.textContent, note: ids["confirm-note"].textContent,
    boxHidden: ids["confirm-box"].hidden, confirmHidden: ids.confirm.hidden,
    confirmDisabled: ids.confirm.disabled, run: ids.run.textContent,
    needed: ids.fields.all().filter((e) => e.className.split(" ").includes("needed")).length,
    stdout: ids.stdout.textContent };
}
(async () => {
  const out = { steps: [] };
  try {
    vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
    await tick();
    for (const step of scenario.steps) {
      if (step.pick) button(step.pick).fire("click");
      if (step.set) { const f = field(step.set[0]); f.value = step.set[1]; f.fire("input"); }
      if (step.click) ids[step.click].fire("click");
      await tick();
      out.steps.push(view());
    }
  } catch (error) { out.error = String((error && error.stack) || error); }
  out.requests = requests.filter((r) => r.url.startsWith("/api/run?"));
  process.stdout.write(JSON.stringify(out));
})();
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH: the page's behaviour is graded under Node")
    return node


def _page_ids() -> tuple[list[str], list[str]]:
    import re

    page = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    tags = re.findall(r"<[a-z][a-z0-9]*\b[^>]*\bid=\"([^\"]+)\"[^>]*>", page)
    hidden = re.findall(r"<[a-z][a-z0-9]*\b[^>]*\bid=\"([^\"]+)\"[^>]*\bhidden\b[^>]*>", page)
    return tags, hidden


_GC_ITEM = {
    "path": ["snap", "gc"],
    "help": "",
    "excluded": None,
    "confirm": "dry_run",
    "params": [],
}
_DOCTOR_ITEM = {
    "path": ["doctor"],
    "help": "",
    "excluded": None,
    "confirm": "doctor",
    "params": [
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


def _result(stdout: object, offer: dict[str, Any] | None) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "exit_code": 0,
        "timed_out": False,
        "truncated": False,
        "stdout": json.dumps(stdout),
        "stderr": "",
    }
    if offer is not None:
        reply["confirm"] = offer
    return reply


def _drive(tmp_path: Path, replies: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    ids, hidden = _page_ids()
    for needed in ("confirm", "confirm-note", "confirm-box"):
        assert needed in ids, needed
    scenario = {
        "ids": ids,
        "hidden": hidden,
        "commands": {"commands": [_GC_ITEM, _DOCTOR_ITEM], "home": "", "sep": "/"},
        "replies": replies,
        "steps": steps,
    }
    harness = tmp_path / "m14-02-harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    data = tmp_path / "m14-02-scenario.json"
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
    # The explorer's own runs at load (`tree`, `install show`) aside.
    out["requests"] = [
        r for r in out["requests"] if r["body"]["command"] in (["snap", "gc"], ["doctor"])
    ]
    return out


def _offer(token: str | None, note: str | None = None, needs: str | None = None) -> dict[str, Any]:
    return {"stage": "plan", "token": token, "note": note, "needs": needs}


def test_the_page_offers_confirm_only_with_a_token_and_sends_it_with_the_plans_params(
    tmp_path: Path,
) -> None:
    nothing = {"dry_run": True, "unreferenced": [], "removed": []}
    replies = {
        "snap gc": _result(nothing, _offer("tok-1", "Nothing to remove. Confirm runs it anyway.")),
        "snap gc confirm": _result(
            {"dry_run": False, "removed": []},
            {"stage": "confirmed", "token": None, "note": None, "needs": None},
        ),
    }
    steps = [{"pick": "snap gc"}, {"click": "run"}, {"click": "confirm"}, {"click": "confirm"}]
    out = _drive(tmp_path, replies, steps)
    picked, planned, confirmed, again = out["steps"]
    assert picked["run"] == "Show the plan" and picked["boxHidden"] and picked["confirmHidden"]
    assert planned["status"].startswith("Plan, nothing was changed")
    assert planned["note"] == "Nothing to remove. Confirm runs it anyway."
    assert not planned["boxHidden"] and not planned["confirmHidden"]
    assert not planned["confirmDisabled"]
    assert confirmed["status"].startswith("Confirmed: exit code 0")
    assert confirmed["confirmHidden"] and confirmed["boxHidden"]
    assert again == confirmed  # one click, one confirmed run
    bodies = [r["body"] for r in out["requests"]]
    assert bodies == [
        {"command": ["snap", "gc"], "params": {}},
        {"command": ["snap", "gc"], "params": {}, "confirm": "tok-1"},
    ]


def test_the_page_offers_nothing_without_a_token_and_marks_a_needed_field(tmp_path: Path) -> None:
    report = {"first_run": {"outcome": "not_asked", "chosen": None}}
    replies = {"doctor": _result(report, _offer(None, "Name one in --flavor.", "flavor"))}
    out = _drive(tmp_path, replies, [{"pick": "doctor"}, {"click": "run"}, {"click": "confirm"}])
    _, planned, clicked = out["steps"]
    assert planned["confirmHidden"] and planned["note"] == "Name one in --flavor."
    assert planned["needed"] == 1
    assert clicked == planned
    assert len(out["requests"]) == 1 and "confirm" not in out["requests"][0]["body"]


def test_the_page_sends_the_plans_params_and_drops_confirm_when_a_field_changes(
    tmp_path: Path,
) -> None:
    report = {"first_run": {"outcome": "not_asked", "chosen": "_x_"}}
    replies = {
        "doctor": _result(report, _offer("tok-2", "Confirm takes a first snapshot of _x_.")),
        "doctor confirm": _result(
            {"first_run": {"outcome": "taken"}},
            {"stage": "confirmed", "token": None, "note": None, "needs": None},
        ),
    }
    steps = [
        {"pick": "doctor"},
        {"set": ["flavor", "_x_"]},
        {"click": "run"},
        {"set": ["flavor", "_y_"]},
        {"click": "confirm"},
        {"set": ["flavor", "_x_"]},
        {"click": "run"},
        {"click": "confirm"},
    ]
    out = _drive(tmp_path, replies, steps)
    planned, changed = out["steps"][2], out["steps"][3]
    assert not planned["confirmHidden"]
    assert changed["confirmHidden"], "a changed field takes Confirm away"
    bodies = [r["body"] for r in out["requests"]]
    assert bodies == [
        {"command": ["doctor"], "params": {"flavor": "_x_"}},
        {"command": ["doctor"], "params": {"flavor": "_x_"}},
        {"command": ["doctor"], "params": {"flavor": "_x_"}, "confirm": "tok-2"},
    ]
    assert out["steps"][-1]["status"].startswith("Confirmed")
