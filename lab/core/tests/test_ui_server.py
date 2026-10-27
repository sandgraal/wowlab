"""`wowlab ui`: the local server's request checks and command runner (M13-24T).

Graders for M13-24, written before it (ADR-0013; ADR-0029 "the server module
is written graders-first by `test-writer`"). Every grader carries one
`xfail(strict=True, reason="M13-24 not implemented")` marker line, which
M13-24 deletes and does not otherwise edit. Two tests have no marker and
pass on main today; they must stay green. One reads the Click tree: the
`--yes` set the graders derive is not empty and holds `doctor`. The other
checks the graders' own reading of a Content-Security-Policy on both sides.

The graders were checked against a throwaway sketch of the seam below (never
committed): each passes with it, and each fails when the sketch is broken in
the way the grader names.

Every expectation comes from ADR-0029, its amendment of 2026-10-15 and the
owner rulings of 2026-10-15 under it, `docs/LAB_PLAN.md` §15.6, and the
M13-24T (as amended 2026-10-15 after the security review of #197) and M13-24
tickets in `docs/BACKLOG.md`. Nothing here was read from an implementation.

The seam (the docs leave these names open; M13-24 builds to them)
-----------------------------------------------------------------

``wowlab_core.ui`` exposes:

- ``make_server(*, command=None, wowlab_argv=None, timeout_s=DEFAULT_TIMEOUT_S,
  output_cap_bytes=DEFAULT_OUTPUT_CAP_BYTES)`` returning a
  ``http.server.ThreadingHTTPServer`` that is bound and not yet serving. The
  caller runs ``serve_forever()`` and stops it with ``shutdown()`` and
  ``server_close()``. It takes no host, address or port argument.

  - ``command``: the root Click command whose leaves may run. ``None`` means
    the CLI's own tree, ``typer.main.get_command(wowlab_core.cli.app)``.
    Tests pass a planted Typer tree here, which is how "a new command appears
    with no UI code" and "a dummy ``--yes`` command is refused with no change
    to the server" are graded.
  - ``wowlab_argv``: the argv prefix that starts "the same ``wowlab``".
    ``None`` means the running interpreter: ``server.wowlab_argv[0]`` is
    ``sys.executable`` (absolute, never a name looked up on PATH), and
    ``[*server.wowlab_argv, "--version"]`` prints ``wowlab <version>``
    (for example ``[sys.executable, "-P", "-m", "wowlab_core.cli"]``), and
    its working directory is not on its import path (``-P``, or ``-I``), so a
    module planted in the current directory is never imported. Every test
    that runs a command passes a recorder script instead, so no test runs a
    real command through the server or touches an install.
  - ``timeout_s`` (finite, > 0) and ``output_cap_bytes`` (> 0): per run.
    ``None``, zero, negative or non-finite values raise ``ValueError`` or
    ``TypeError``: there is no way to turn either off.

- The server object has ``token: str`` (``secrets.token_urlsafe(32)``),
  ``url: str``, exactly ``http://127.0.0.1:<port>/?token=<token>``, the URL
  ``wowlab ui`` opens, and ``wowlab_argv: Sequence[str]``, the resolved prefix.
  *Amended by M16-42:* ``url`` is ``http://127.0.0.1:<port>/?boot=<boot>``,
  ``boot`` a one-time key the page exchanges once, at ``POST /api/session``,
  for ``token``, which is in that one response body and nowhere else
  (``test_ui_local_exposure.py``).
- A run is stopped with its whole process tree (grandchildren included) when
  it passes ``timeout_s``, when its output passes ``output_cap_bytes`` (read
  as a stream: the cap bounds memory, and the process is killed there), and
  when ``server_close()`` is called while it is in progress.
- ``DEFAULT_TIMEOUT_S: float``, ``DEFAULT_OUTPUT_CAP_BYTES: int`` and
  ``MAX_BODY_BYTES: int`` (at most 1 MiB), the largest POST body read.
- ``static_files() -> Mapping[str, bytes]``: every URL path the server serves
  as a static file, with its exact bytes, all template text inside the
  package. Its keys are ``"/"`` (the page shell) and paths ending ``.html``,
  ``.css`` or ``.js``; it does not depend on the working directory or HOME.

``wowlab ui`` (in ``wowlab_core.cli``) calls ``wowlab_core.ui.make_server()``
with no arguments, looked up on the module at run time; calls
``webbrowser.open(server.url)``, looked up on the ``webbrowser`` module at
run time; then ``server.serve_forever()``. When ``webbrowser.open`` returns
False or raises ``webbrowser.Error``, it prints ``server.url`` once on its
own stdout (owner ruling 2026-10-15), and never through logging, a file, or
a child's argv or environment; when the browser opens, the token is printed
nowhere. Ctrl-C (``KeyboardInterrupt`` from ``serve_forever``) ends it with
exit 0, as ``log tail --follow`` does, after ``server_close()``.

The HTTP surface:

- The token travels as the query parameter ``token`` on every request. It
  appears in no response body or header, no response sets a cookie, and no
  run gets it in its argv or environment.
- ``Host`` must be ``127.0.0.1:<port>`` or ``localhost:<port>``. An
  ``Origin``, when present, must equal ``"http://" + Host``.
- ``GET /api/commands`` returns ``{"commands": [{"path": [str, ...], ...}]}``.
- ``POST /api/run`` takes the JSON body
  ``{"command": [str, ...], "params": {<Click parameter name>: value}}``.
  Host, Origin and the token are checked before the body is read, and a
  ``Content-Length`` over ``MAX_BODY_BYTES`` answers 413
  ``{"error": "too_large"}`` without reading it. A value is a ``str`` or an
  ``int`` for a valued parameter, a ``bool`` for a flag (true: the flag is
  given; false: it is not), or a list of ``str``/``int`` for a repeatable
  option, each element passed as a value. The server adds ``--json`` itself.
  A run that happened answers 200 with
  ``{"exit_code": int, "stdout": str, "stderr": str, "timed_out": bool,
  "truncated": bool}``, whatever the exit code.
- A refusal answers 403 with the JSON body ``{"error": <code>}``:
  ``"token"``, ``"host"``, ``"origin"`` for the request checks;
  ``"not_a_command"`` when ``command`` is not the path of a leaf of the tree;
  ``"excluded"`` for a leaf that declares ``--yes`` (since M14-02, one not
  in the confirm step; see test_ui_confirm.py), for ``ui``, for
  ``log tail`` with ``follow`` true (a run must end), and for ``looks page``
  with an ``out`` parameter of any value (it can create a file anywhere
  outside an install; review of #198);
  ``"unknown_parameter"`` for a key the leaf does not declare;
  ``"bad_value"`` for a value of another type (null, float, object, a list
  for a single-valued parameter, a bool for a valued one, a non-bool for a
  flag, a list holding anything but str or int).
- A path that is neither ``/api/...`` nor a key of ``static_files()`` answers
  404; a traversal path answers 400, 403 or 404, and never with a file.
- Every response, refusals and 404s included, carries one or more
  ``Content-Security-Policy`` headers. In each, no directive appears twice
  (a browser enforces the first); ``default-src`` is ``'self'`` or
  ``'none'``; ``frame-ancestors`` is ``'none'``; ``base-uri`` and
  ``form-action`` are each ``'self'`` or ``'none'``; and every source, in
  every directive, is ``'self'``, ``'none'`` or a
  ``'sha256-…'``/``'sha384-…'``/``'sha512-…'`` hash. No host, scheme, ``*``,
  ``'unsafe-…'`` keyword, nonce or report endpoint.
- No response carries an ``Access-Control-*`` header.

Inputs are constructed (labelled): the planted Typer tree, the recorder
script, and hostile request targets and headers. This is our own HTTP
surface, not an external format (L8). Nothing reads or writes an install.
"""

from __future__ import annotations

import contextlib
import http.client
import http.server
import importlib
import inspect
import json
import logging
import math
import re
import socket
import subprocess
import sys
import threading
import time
import tracemalloc
import urllib.parse
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import psutil
import pytest
import typer
from typer._click import Context

# How long a request may take before the test calls it a hang. A run with
# `timeout_s=5` must answer well inside this.
_HANG = 30.0


def _ui() -> Any:
    """The module under test, imported per test so that today each grader
    fails with ModuleNotFoundError rather than the file failing to collect."""
    return importlib.import_module("wowlab_core.ui")


# ─── the Click tree ──────────────────────────────────────────────────────────


def _leaves(cmd: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every leaf command under `cmd`, with its path."""
    if hasattr(cmd, "list_commands"):
        ctx = Context(cmd)
        for name in cmd.list_commands(ctx):
            yield from _leaves(cmd.get_command(ctx, name), (*path, name))
    else:
        yield path, cmd


def _declares_yes(cmd: Any) -> bool:
    return any("--yes" in getattr(param, "opts", ()) for param in cmd.params)


def _real_tree() -> Any:
    from wowlab_core import cli

    return typer.main.get_command(cli.app)


def _leaf(tree: Any, path: Sequence[str]) -> Any:
    return dict(_leaves(tree))[tuple(path)]


# Module level: annotations are strings here, and Typer resolves them in the
# module's globals.
_JsonOpt = Annotated[bool, typer.Option("--json")]


def _planted_tree() -> Any:
    """A Typer app built in the test: commands the server has never seen."""
    app = typer.Typer()
    grp = typer.Typer()
    app.add_typer(grp, name="grp")

    @app.command("echo")
    def echo(
        path: Annotated[str, typer.Argument()],
        name: Annotated[str | None, typer.Option("--name")] = None,
        count: Annotated[int, typer.Option("--count")] = 1,
        dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
        tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
        json_out: _JsonOpt = False,
    ) -> None:
        """Takes an argument, two valued options, a flag and a repeatable option."""

    @app.command("sleep")
    def sleep(json_out: _JsonOpt = False) -> None:
        """The recorder starts a grandchild that shares its pipes, then sleeps."""

    @app.command("nap")
    def nap(json_out: _JsonOpt = False) -> None:
        """The recorder starts a grandchild on DEVNULL, then sleeps."""

    @app.command("flood")
    def flood(json_out: _JsonOpt = False) -> None:
        """The recorder writes to stdout without end."""

    @app.command("spew")
    def spew(json_out: _JsonOpt = False) -> None:
        """The recorder writes to stderr without end."""

    @app.command("danger")
    def danger(
        yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
        json_out: _JsonOpt = False,
    ) -> None:
        """A dummy command that asks before changing files."""

    @grp.command("inner")
    def inner(json_out: _JsonOpt = False) -> None:
        """A leaf under a group."""

    @grp.command("danger2")
    def danger2(
        confirm: Annotated[bool, typer.Option("--confirm", "--yes")] = False,
        json_out: _JsonOpt = False,
    ) -> None:
        """`--yes` as the second name of an option still declares `--yes`."""

    return typer.main.get_command(app)


_PLANTED_YES = [("danger",), ("grp", "danger2")]
_PLANTED_RUNNABLE = [("echo",), ("sleep",), ("nap",), ("flood",), ("spew",), ("grp", "inner")]


# ─── the recorder: the `wowlab` the server starts in these tests ─────────────

_RECORDER = """\
import json, os, subprocess, sys, time
argv = sys.argv[1:]
with open({calls!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": argv, "env": dict(os.environ)}}) + "\\n")

def pids(*more):
    tmp = {pid!r} + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(" ".join(str(p) for p in (os.getpid(), *more)))
    os.replace(tmp, {pid!r})

if argv[:1] in (["sleep"], ["nap"]):
    shared = argv[:1] == ["sleep"]
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL,
        stdout=None if shared else subprocess.DEVNULL,
        stderr=None if shared else subprocess.DEVNULL,
    )
    pids(grandchild.pid)
    time.sleep(120)
elif argv[:1] in (["flood"], ["spew"]):
    pids()
    stream = sys.stdout if argv[:1] == ["flood"] else sys.stderr
    chunk = "x" * 65536
    while True:
        stream.write(chunk)
        stream.flush()
else:
    sys.stdout.write(json.dumps({{"argv": argv}}))
"""


@dataclass
class Recorder:
    argv: list[str]
    calls_file: Path
    pid_file: Path

    def pids(self, within: float = 20.0) -> list[int]:
        """The pids the recorder wrote: its own, then any grandchild's."""
        deadline = time.monotonic() + within
        while not self.pid_file.exists():
            assert time.monotonic() < deadline, "the recorder never started"
            time.sleep(0.05)
        return [int(p) for p in self.pid_file.read_text(encoding="utf-8").split()]

    def calls(self) -> list[dict[str, Any]]:
        if not self.calls_file.exists():
            return []
        lines = self.calls_file.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]


@pytest.fixture
def recorder(tmp_path: Path) -> Recorder:
    calls = tmp_path / "calls.jsonl"
    pid = tmp_path / "recorder.pid"
    script = tmp_path / "fake_wowlab_constructed.py"
    script.write_text(_RECORDER.format(calls=str(calls), pid=str(pid)), encoding="utf-8")
    return Recorder([sys.executable, str(script)], calls, pid)


# ─── serving and talking to it ───────────────────────────────────────────────


@contextmanager
def _serving(**kwargs: Any) -> Iterator[Any]:
    server = _ui().make_server(**kwargs)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


@pytest.fixture
def planted(recorder: Recorder) -> Iterator[Any]:
    with _serving(command=_planted_tree(), wowlab_argv=recorder.argv) as server:
        yield server


@pytest.fixture
def real(recorder: Recorder) -> Iterator[Any]:
    with _serving(command=None, wowlab_argv=recorder.argv) as server:
        yield server


def _port(server: Any) -> int:
    return int(server.server_address[1])


@dataclass
class Reply:
    status: int
    headers: list[tuple[str, str]]
    body: bytes = field(repr=False)

    def all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]

    def json(self) -> Any:
        return json.loads(self.body)

    def error(self) -> Any:
        return self.json().get("error")


_DEFAULT = object()


def _target(server: Any, path: str, token: object = _DEFAULT) -> str:
    """`path` with `?token=` appended (the server's own token by default)."""
    value = server.token if token is _DEFAULT else token
    if value is None:
        return path
    return f"{path}?{urllib.parse.urlencode({'token': value})}"


def _send(
    server: Any,
    method: str,
    target: str,
    *,
    host: object = _DEFAULT,
    origin: str | None = None,
    body: bytes | None = None,
    headers: Sequence[tuple[str, str]] = (),
) -> Reply:
    """One raw request: the target, Host and Origin go out exactly as given."""
    conn = http.client.HTTPConnection("127.0.0.1", _port(server), timeout=_HANG)
    try:
        conn.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
        if host is _DEFAULT:
            conn.putheader("Host", f"127.0.0.1:{_port(server)}")
        elif host is not None:
            conn.putheader("Host", str(host))
        if origin is not None:
            conn.putheader("Origin", origin)
        for name, value in headers:
            conn.putheader(name, value)
        if body is not None:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        return Reply(response.status, response.getheaders(), response.read())
    finally:
        conn.close()


def _run(
    server: Any,
    command: Sequence[str],
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Reply:
    body = json.dumps({"command": list(command), "params": params or {}}).encode()
    token = kwargs.pop("token", _DEFAULT)
    return _send(server, "POST", _target(server, "/api/run", token), body=body, **kwargs)


def _ran(reply: Reply) -> dict[str, Any]:
    assert reply.status == 200, (reply.status, reply.body[:500])
    result: dict[str, Any] = reply.json()
    return result


def _argv_of(reply: Reply) -> list[str]:
    """The argv the recorder received, from its stdout."""
    result = _ran(reply)
    assert result["exit_code"] == 0, result
    argv: list[str] = json.loads(result["stdout"])["argv"]
    return argv


def _parsed(tree: Any, path: Sequence[str], argv: list[str]) -> dict[str, Any]:
    """What Click makes of `argv` for the leaf at `path`: the values as the
    command itself would receive them."""
    assert argv[: len(path)] == list(path), argv
    leaf = _leaf(tree, path)
    ctx = leaf.make_context(path[-1], list(argv[len(path) :]), resilient_parsing=False)
    params: dict[str, Any] = dict(ctx.params)
    return params


# ─── control (passes on main today) ──────────────────────────────────────────


def test_the_tree_has_yes_leaves_and_doctor_is_one() -> None:
    """The set the exclusion graders walk is read from the tree, not counted;
    this only checks it is not empty and holds `doctor` (amendment
    2026-10-15: its `--yes` takes a first snapshot)."""
    yes = {path for path, cmd in _leaves(_real_tree()) if _declares_yes(cmd)}
    assert ("doctor",) in yes
    assert ("snap", "restore") in yes


# ─── binding ─────────────────────────────────────────────────────────────────


def test_binds_loopback_only_on_a_free_port(recorder: Recorder) -> None:
    ui = _ui()
    with (
        _serving(wowlab_argv=recorder.argv) as one,
        _serving(wowlab_argv=recorder.argv) as two,
    ):
        for server in (one, two):
            assert isinstance(server, http.server.ThreadingHTTPServer)
            host, port = server.server_address[:2]
            assert host == "127.0.0.1"
            assert server.socket.family == socket.AF_INET
            assert server.socket.getsockname()[0] == "127.0.0.1"
            assert 0 < port < 65536
        assert _port(one) != _port(two), "each server asks the OS for a free port"
    params = set(inspect.signature(ui.make_server).parameters)
    assert not params & {"host", "address", "bind", "port", "interface", "server_address"}


def test_the_default_wowlab_is_this_interpreter_by_absolute_path(tmp_path: Path) -> None:
    """Review of #197, M1, and the owner rulings of 2026-10-15: never a name
    looked up on PATH (on Windows a `.cmd` shim would re-parse the argv)."""
    server = _ui().make_server()
    try:
        argv = list(server.wowlab_argv)
        assert argv[0] == sys.executable
        assert Path(argv[0]).is_absolute()
        assert Path(argv[0]).suffix.lower() not in (".bat", ".cmd", ".ps1", ".sh")
        version = subprocess.run(
            [*argv, "--version"], capture_output=True, text=True, timeout=60, check=False
        )
        assert version.returncode == 0, version.stderr
        assert version.stdout.startswith("wowlab "), version.stdout
        # The run's working directory is not on its import path: a planted
        # module there is never imported (security re-review of #197).
        planted = tmp_path / "planted"
        (planted / "wowlab_core").mkdir(parents=True)
        bomb = "import sys; print('PLANTED'); sys.exit(7)\n"
        for name in ("typer.py", "pydantic.py", "platformdirs.py", "wowlab_core/__init__.py"):
            (planted / name).write_text(bomb, encoding="utf-8")
        version = subprocess.run(
            [*argv, "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=planted,
        )
        assert "PLANTED" not in version.stdout + version.stderr
        assert version.returncode == 0, version.stderr
        assert version.stdout.startswith("wowlab "), version.stdout
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def _non_loopback_ipv4() -> str | None:
    """This machine's address on its default route, if it has one. A UDP
    `connect` sends nothing."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere
        address = str(probe.getsockname()[0])
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def test_not_reachable_on_another_interface(planted: Any) -> None:
    address = _non_loopback_ipv4()
    if address is not None:
        with pytest.raises(OSError):
            socket.create_connection((address, _port(planted)), timeout=2).close()
    assert planted.server_address[0] == "127.0.0.1"


def test_ui_is_a_leaf_with_no_way_to_bind_elsewhere() -> None:
    leaves = dict(_leaves(_real_tree()))
    assert ("ui",) in leaves, "`wowlab ui` is a command of the CLI"
    opts = {opt for param in leaves[("ui",)].params for opt in getattr(param, "opts", ())}
    assert not opts & {"--host", "--bind", "--address", "--port", "--listen", "--yes"}


# ─── the token ───────────────────────────────────────────────────────────────


def test_token_is_random_urlsafe_and_only_in_the_url(recorder: Recorder) -> None:
    with (
        _serving(wowlab_argv=recorder.argv) as one,
        _serving(wowlab_argv=recorder.argv) as two,
    ):
        for server in (one, two):
            assert re.fullmatch(r"[A-Za-z0-9_-]{43,}", server.token), "token_urlsafe(32)"
            # M16-42: the opened URL carries the one-time boot key, never
            # the session key (test_ui_local_exposure.py grades the exchange).
            assert re.fullmatch(r"[A-Za-z0-9_-]{43,}", server.boot), "token_urlsafe(32)"
            assert server.url == f"http://127.0.0.1:{_port(server)}/?boot={server.boot}"
            assert server.token not in server.url
        assert one.token != two.token
        assert one.boot != two.boot


_NO_TOKEN_PATHS = [
    ("GET", "/"),
    ("GET", "/api/commands"),
    ("POST", "/api/run"),
    ("GET", "/no-such-page"),
    ("HEAD", "/"),
]


@pytest.mark.parametrize(("method", "path"), _NO_TOKEN_PATHS)
def test_a_request_without_the_token_is_refused(
    planted: Any, recorder: Recorder, method: str, path: str
) -> None:
    body = json.dumps({"command": ["echo"], "params": {"path": "x"}}).encode()
    reply = _send(planted, method, path, body=body if method == "POST" else None)
    assert reply.status == 403
    if path == "/":
        # M16-42: the page a reload asks for answers the player a sentence,
        # as plain text (test_ui_local_exposure.py); every other path JSON.
        assert reply.all("Content-Type") == ["text/plain; charset=utf-8"]
    elif method != "HEAD":
        assert reply.error() == "token"
    assert recorder.calls() == []


def _wrong_tokens(token: str) -> list[str]:
    flipped = ("A" if token[0] != "A" else "B") + token[1:]
    return ["", "x", flipped, token[:-1], token + "x", token.upper(), f" {token}"]


def test_a_wrong_token_is_refused_on_every_path(planted: Any, recorder: Recorder) -> None:
    paths = ["/", "/api/commands", *_ui().static_files()]
    for wrong in _wrong_tokens(planted.token):
        for path in paths:
            reply = _send(planted, "GET", _target(planted, path, wrong))
            assert (reply.status, reply.error()) == (403, "token"), (path, wrong)
        reply = _run(planted, ["echo"], {"path": "x"}, token=wrong)
        assert (reply.status, reply.error()) == (403, "token"), wrong
    # The token under another parameter name is not the token.
    reply = _send(planted, "GET", f"/?tok={planted.token}")
    assert (reply.status, reply.error()) == (403, "token")
    assert recorder.calls() == []
    # Positive control: the real token opens the page.
    assert _send(planted, "GET", _target(planted, "/")).status == 200


def test_the_token_never_appears_in_a_log_line(
    planted: Any,
    recorder: Recorder,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    token = planted.token
    _send(planted, "GET", _target(planted, "/"))
    _send(planted, "GET", _target(planted, "/api/commands"))
    _send(planted, "GET", _target(planted, "/no-such-page"))
    _send(planted, "GET", _target(planted, "/../cli.py"))
    _send(planted, "GET", _target(planted, "/"), host="evil.example")
    _send(planted, "GET", _target(planted, "/"), origin="http://evil.example")
    _send(planted, "POST", _target(planted, "/api/run"), body=b"{not json")
    _ran(_run(planted, ["echo"], {"path": "x"}))
    _run(planted, ["danger"], {"yes": True})
    _run(planted, ["echo"], {"bogus": "x"})
    out, err = capfd.readouterr()
    assert token not in out
    assert token not in err
    for record in caplog.records:
        assert token not in record.getMessage()
        assert token not in str(record.args)
    # Nor is it handed to the command it runs.
    (call,) = recorder.calls()
    assert not any(token in arg for arg in call["argv"])
    assert not any(token in key or token in value for key, value in call["env"].items())


def test_the_token_reaches_no_child(planted: Any, real: Any, recorder: Recorder) -> None:
    """ADR-0029 owner rulings 2026-10-15: the token is never in a child's argv
    or environment. The recorder writes both down for every run."""
    for server in (planted, real):
        token = server.token
        before = len(recorder.calls())
        runs: list[tuple[list[str], dict[str, Any]]] = [
            (["snap", "list"], {}),
            (["explain"], {"path": "x"}),
            (["profile", "save"], {"name": "n", "preset": ["a"]}),
        ]
        if server is planted:
            runs = [
                (["echo"], {"path": "x", "name": "y", "tag": ["a", "b"], "dry_run": True}),
                (["grp", "inner"], {}),
            ]
        for command, params in runs:
            _ran(_run(server, command, params))
        calls = recorder.calls()[before:]
        assert len(calls) == len(runs)
        for call in calls:
            assert not any(token in arg for arg in call["argv"]), call["argv"]
            for key, value in call["env"].items():
                assert token not in key and token not in value, key


# ─── Host and Origin ─────────────────────────────────────────────────────────


def _bad_hosts(port: int) -> list[str | None]:
    return [
        None,
        "",
        "evil.example",
        f"evil.example:{port}",
        "127.0.0.1",
        "localhost",
        f"127.0.0.1:{port + 1 if port < 65535 else port - 1}",
        f"localhost:{port}0",
        f"127.0.0.1:{port}.evil.example",
        f"localhost.evil.example:{port}",
        f"127.0.0.1.evil.example:{port}",
        f"evil.example@127.0.0.1:{port}",
        f"0.0.0.0:{port}",
        f"[::1]:{port}",
        f"127.0.0.2:{port}",
        f"2130706433:{port}",
        f"127.0.0.1:{port} evil.example",
    ]


def test_a_foreign_host_is_refused(planted: Any, recorder: Recorder) -> None:
    port = _port(planted)
    for host in _bad_hosts(port):
        for path in ("/", "/api/commands", *_ui().static_files()):
            reply = _send(planted, "GET", _target(planted, path), host=host)
            assert (reply.status, reply.error()) == (403, "host"), (host, path)
        reply = _run(planted, ["echo"], {"path": "x"}, host=host)
        assert (reply.status, reply.error()) == (403, "host"), host
    assert recorder.calls() == []
    # Positive controls: the two names the ADR allows.
    for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        assert _send(planted, "GET", _target(planted, "/"), host=host).status == 200
        assert _argv_of(_run(planted, ["echo"], {"path": "x"}, host=host))[0] == "echo"


def _bad_origins(port: int) -> list[str]:
    return [
        "http://evil.example",
        f"http://evil.example:{port}",
        "null",
        "",
        f"https://127.0.0.1:{port}",
        f"http://127.0.0.1:{port + 1 if port < 65535 else port - 1}",
        "http://127.0.0.1",
        f"http://127.0.0.1:{port}.evil.example",
        f"http://localhost.evil.example:{port}",
        f"file://127.0.0.1:{port}",
        f"http://[::1]:{port}",
    ]


def test_a_cross_origin_request_is_refused(planted: Any, recorder: Recorder) -> None:
    port = _port(planted)
    for origin in _bad_origins(port):
        for path in ("/", "/api/commands"):
            reply = _send(planted, "GET", _target(planted, path), origin=origin)
            assert (reply.status, reply.error()) == (403, "origin"), (origin, path)
        reply = _run(planted, ["echo"], {"path": "x"}, origin=origin)
        assert (reply.status, reply.error()) == (403, "origin"), origin
    assert recorder.calls() == []
    # Positive controls: no Origin, and the server's own origin.
    assert _send(planted, "GET", _target(planted, "/")).status == 200
    same = f"http://127.0.0.1:{port}"
    assert _send(planted, "GET", _target(planted, "/"), origin=same).status == 200
    assert _argv_of(_run(planted, ["echo"], {"path": "x"}, origin=same))[0] == "echo"
    local = f"localhost:{port}"
    reply = _send(planted, "GET", _target(planted, "/"), host=local, origin=f"http://{local}")
    assert reply.status == 200


def test_an_origin_must_name_the_host_the_request_used(planted: Any, recorder: Recorder) -> None:
    """Owner rulings 2026-10-15: an Origin, when present, equals "http://" + Host.
    Both loopback names are accepted as Host, but not mixed with the other."""
    port = _port(planted)
    loop, local = f"127.0.0.1:{port}", f"localhost:{port}"
    for host, origin in ((loop, f"http://{local}"), (local, f"http://{loop}")):
        reply = _send(planted, "GET", _target(planted, "/"), host=host, origin=origin)
        assert (reply.status, reply.error()) == (403, "origin"), (host, origin)
        reply = _run(planted, ["echo"], {"path": "x"}, host=host, origin=origin)
        assert (reply.status, reply.error()) == (403, "origin"), (host, origin)
    assert recorder.calls() == []
    for host in (loop, local):
        reply = _run(planted, ["echo"], {"path": "x"}, host=host, origin=f"http://{host}")
        assert _argv_of(reply)[0] == "echo"


# ─── headers on every response ───────────────────────────────────────────────


def _assorted_replies(server: Any) -> list[tuple[str, Reply]]:
    """A 200 for every kind of thing served, and every kind of refusal."""
    port = _port(server)
    replies = [
        ("page", _send(server, "GET", _target(server, "/"))),
        ("commands", _send(server, "GET", _target(server, "/api/commands"))),
        ("run", _run(server, ["echo"], {"path": "x"})),
        ("no token", _send(server, "GET", "/")),
        ("bad host", _send(server, "GET", _target(server, "/"), host="evil.example")),
        ("bad origin", _send(server, "GET", _target(server, "/"), origin="http://evil.example")),
        ("not found", _send(server, "GET", _target(server, "/no-such-page"))),
        ("traversal", _send(server, "GET", _target(server, "/../cli.py"))),
        ("excluded", _run(server, ["danger"], {})),
        ("not a command", _run(server, ["grp"], {})),
        ("unknown parameter", _run(server, ["echo"], {"bogus": "x"})),
        ("bad value", _run(server, ["echo"], {"path": None})),
        ("bad body", _send(server, "POST", _target(server, "/api/run"), body=b"{not json")),
        (
            "preflight",
            _send(
                server,
                "OPTIONS",
                _target(server, "/api/run"),
                origin="http://evil.example",
                headers=[
                    ("Access-Control-Request-Method", "POST"),
                    ("Access-Control-Request-Headers", "content-type"),
                ],
            ),
        ),
        (
            "same-origin preflight",
            _send(
                server,
                "OPTIONS",
                _target(server, "/api/run"),
                origin=f"http://127.0.0.1:{port}",
                headers=[("Access-Control-Request-Method", "POST")],
            ),
        ),
    ]
    replies += [
        (f"static {path}", _send(server, "GET", _target(server, path)))
        for path in _ui().static_files()
    ]
    return replies


def test_no_cors_header_on_any_response(planted: Any) -> None:
    for what, reply in _assorted_replies(planted):
        cors = [k for k, _ in reply.headers if k.lower().startswith("access-control-")]
        assert cors == [], (what, cors)


def test_the_token_is_in_no_response(planted: Any) -> None:
    """A DNS-rebinding page is same-origin with a refusal it provokes, so a
    refusal that echoes the request would hand it the token (security review
    of #197, M4). A cookie would make the token ambient."""
    token = planted.token
    port = _port(planted)
    replies = _assorted_replies(planted)
    for host in _bad_hosts(port):
        replies.append((f"host {host!r}", _send(planted, "GET", _target(planted, "/"), host=host)))
    for origin in _bad_origins(port):
        reply = _send(planted, "GET", _target(planted, "/"), origin=origin)
        replies.append((f"origin {origin!r}", reply))
    for wrong in _wrong_tokens(token):
        reply = _send(planted, "GET", _target(planted, "/api/commands", wrong))
        replies.append((f"token {wrong!r}", reply))
        replies.append(
            (
                f"token {wrong!r} + real",
                _send(planted, "GET", f"/?{urllib.parse.urlencode({'t': token, 'token': wrong})}"),
            )
        )
    for what, reply in replies:
        assert token.encode() not in reply.body, what
        for name, value in reply.headers:
            assert token not in name and token not in value, (what, name)
        assert reply.all("Set-Cookie") == [], what


def _post_headers_only(
    server: Any, target: str, length: int, *, host: object = _DEFAULT, origin: str | None = None
) -> Reply:
    """A POST that announces `length` body bytes and sends none: a server
    that reads the body before refusing never answers."""
    conn = http.client.HTTPConnection("127.0.0.1", _port(server), timeout=10)
    try:
        conn.putrequest("POST", target, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", f"127.0.0.1:{_port(server)}" if host is _DEFAULT else str(host))
        if origin is not None:
            conn.putheader("Origin", origin)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(length))
        conn.endheaders()
        response = conn.getresponse()
        return Reply(response.status, response.getheaders(), response.read())
    finally:
        conn.close()


def test_a_refused_post_is_refused_before_its_body_is_read(
    planted: Any, recorder: Recorder
) -> None:
    """Review of #197, M4: Host, Origin and the token come first, and a body
    over MAX_BODY_BYTES is refused unread."""
    ui = _ui()
    assert isinstance(ui.MAX_BODY_BYTES, int)
    assert 0 < ui.MAX_BODY_BYTES <= 1024 * 1024
    run = "/api/run"
    cases = [
        ("token", _post_headers_only(planted, _target(planted, run, None), 1000)),
        ("token", _post_headers_only(planted, _target(planted, run, "wrong"), 1000)),
        ("host", _post_headers_only(planted, _target(planted, run), 1000, host="evil.example")),
        (
            "origin",
            _post_headers_only(planted, _target(planted, run), 1000, origin="http://evil.example"),
        ),
    ]
    for expected, reply in cases:
        assert (reply.status, reply.error()) == (403, expected)
    reply = _post_headers_only(planted, _target(planted, run), ui.MAX_BODY_BYTES + 1)
    assert (reply.status, reply.error()) == (413, "too_large")
    reply = _post_headers_only(planted, _target(planted, run), 2**40)
    assert (reply.status, reply.error()) == (413, "too_large")
    assert recorder.calls() == []
    # Positive control: a body under the cap is read and run.
    assert _argv_of(_run(planted, ["echo"], {"path": "x"}))[0] == "echo"


_HASH_SOURCE = re.compile(r"'sha(256|384|512)-[a-z0-9+/]+={0,2}'")


def _csp_problems(value: str) -> list[str]:
    problems: list[str] = []
    directives: dict[str, list[str]] = {}
    for part in value.split(";"):
        words = part.split()
        if not words:
            continue
        name = words[0].lower()
        if name in directives:
            # A browser enforces the first and ignores the rest.
            problems.append(f"{name} appears twice")
            continue
        directives[name] = [w.lower() for w in words[1:]]
    if directives.get("default-src") not in (["'self'"], ["'none'"]):
        problems.append(f"default-src is {directives.get('default-src')}")
    # These three do not fall back to default-src.
    if directives.get("frame-ancestors") != ["'none'"]:
        problems.append(f"frame-ancestors is {directives.get('frame-ancestors')}")
    for name in ("base-uri", "form-action"):
        if directives.get(name) not in (["'self'"], ["'none'"]):
            problems.append(f"{name} is {directives.get(name)}")
    for name, sources in directives.items():
        if name == "font-src" and sources == ["data:"]:
            continue  # M16-51 (ADR-0035): `data:` only as the whole of font-src
        for source in sources:
            if source not in ("'self'", "'none'") and not _HASH_SOURCE.fullmatch(source):
                problems.append(f"{name} allows {source}")
    return problems


_FRAME = "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        (f"default-src 'self'; {_FRAME}", True),
        (f"default-src 'none'; script-src 'self'; connect-src 'self'; {_FRAME}", True),
        (f"default-src 'none'; style-src 'sha256-AbC+/9=';{_FRAME};", True),
        ("DEFAULT-SRC 'SELF'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'", True),
        (f"default-src 'self' https://cdn.example; {_FRAME}", False),
        (f"default-src 'self'; script-src 'self' 'unsafe-inline'; {_FRAME}", False),
        (f"default-src 'self'; img-src data:; {_FRAME}", False),
        (f"default-src *; {_FRAME}", False),
        (f"script-src 'self'; {_FRAME}", False),
        (f"default-src 'self'; report-uri /csp; {_FRAME}", False),
        (f"default-src 'self'; connect-src http://127.0.0.1:1; {_FRAME}", False),
        (f"default-src *; default-src 'self'; {_FRAME}", False),
        (f"default-src 'self'; frame-ancestors *; {_FRAME}", False),
        ("default-src 'self'", False),
        ("default-src 'self'; base-uri 'none'; form-action 'none'", False),
        ("default-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'", False),
        ("default-src 'self'; frame-ancestors 'none'; form-action 'none'", False),
        ("default-src 'self'; frame-ancestors 'none'; base-uri 'none'", False),
        ("default-src 'self'; frame-ancestors 'none'; base-uri *; form-action 'none'", False),
    ],
)
def test_the_csp_reading_used_by_the_graders_constructed(value: str, ok: bool) -> None:
    """The grader's own CSP reading, checked on both sides (no marker)."""
    assert (_csp_problems(value) == []) is ok


def test_every_response_has_a_csp_allowing_only_the_server(planted: Any) -> None:
    for what, reply in _assorted_replies(planted):
        values = reply.all("Content-Security-Policy")
        assert values, f"{what}: no Content-Security-Policy"
        for value in values:
            assert _csp_problems(value) == [], (what, value)


# ─── what runs: leaves of the tree only ──────────────────────────────────────


def test_the_command_list_is_the_planted_tree(planted: Any) -> None:
    reply = _send(planted, "GET", _target(planted, "/api/commands"))
    assert reply.status == 200
    listed = {tuple(item["path"]) for item in reply.json()["commands"]}
    leaves = {path for path, _ in _leaves(_planted_tree())}
    assert listed <= leaves, f"listed but not a leaf: {listed - leaves}"
    assert set(_PLANTED_RUNNABLE) <= listed, "a new leaf appears with no UI code"
    assert ("grp",) not in listed


@pytest.mark.parametrize(
    "command",
    [
        [],
        ["grp"],
        ["nope"],
        ["grp", "nope"],
        ["echo", "extra"],
        ["ECHO"],
        ["--help"],
        ["echo", "--help"],
        ["../echo"],
        ["grp/inner"],
        ["grp inner"],
        ["inner"],
    ],
    ids=repr,
)
def test_only_a_leaf_of_the_tree_runs(planted: Any, recorder: Recorder, command: list[str]) -> None:
    reply = _run(planted, command, {})
    assert (reply.status, reply.error()) == (403, "not_a_command"), reply.body[:300]
    assert recorder.calls() == []


def test_a_leaf_runs_with_json_and_nothing_else(planted: Any, recorder: Recorder) -> None:
    for path in [("grp", "inner"), ("echo",)]:
        params = {"path": "x"} if path == ("echo",) else {}
        argv = _argv_of(_run(planted, path, params))
        expected = [*path, "x", "--json"] if path == ("echo",) else [*path, "--json"]
        # `--` may end the options before an argument; nothing else is added.
        assert sorted(a for a in argv if a != "--") == sorted(expected), argv
        assert argv[: len(path)] == list(path)
        assert _parsed(_planted_tree(), path, argv).get("json_out") is True
    result = _ran(_run(planted, ["echo"], {"path": "x"}))
    assert (result["exit_code"], result["timed_out"], result["truncated"]) == (0, False, False)
    # The server ran its configured `wowlab`, once per run, nothing else.
    assert len(recorder.calls()) == 3


def test_a_real_read_command_runs_with_json(real: Any, recorder: Recorder) -> None:
    argv = _argv_of(_run(real, ["snap", "list"], {}))
    assert [a for a in argv if a != "--"] == ["snap", "list", "--json"], argv
    argv = _argv_of(_run(real, ["explain"], {"path": "--root"}))
    assert str(_parsed(_real_tree(), ["explain"], argv)["path"]) == "--root"
    reply = _run(real, ["snap"], {})
    assert (reply.status, reply.error()) == (403, "not_a_command")


# ─── the argument vector ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "params",
    [
        {"bogus": "x"},
        {"path": "x", "bogus": "x"},
        {"path": "x", "--name": "y"},
        {"path": "x", "yes": True},
        {"path": "x", "json": True},
        {"path": "x", "name; rm": "y"},
        {"path": "x", "Name": "y"},
        {"path": "x", "": "y"},
    ],
    ids=repr,
)
def test_an_undeclared_parameter_is_refused(
    planted: Any, recorder: Recorder, params: dict[str, Any]
) -> None:
    reply = _run(planted, ["echo"], params)
    assert (reply.status, reply.error()) == (403, "unknown_parameter"), reply.body[:300]
    assert recorder.calls() == []


def test_another_commands_parameter_is_unknown_here(planted: Any, recorder: Recorder) -> None:
    reply = _run(planted, ["grp", "inner"], {"name": "y"})
    assert (reply.status, reply.error()) == (403, "unknown_parameter")
    assert recorder.calls() == []


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"path": "--dry-run"}, {"path": "--dry-run", "dry_run": False}),
        ({"path": "--help"}, {"path": "--help"}),
        ({"path": "-"}, {"path": "-"}),
        ({"path": "--"}, {"path": "--"}),
        ({"path": "--count=9"}, {"path": "--count=9", "count": 1}),
        ({"path": "x", "name": "--dry-run"}, {"name": "--dry-run", "dry_run": False}),
        ({"path": "x", "name": "--yes"}, {"name": "--yes"}),
        ({"path": "x", "name": "-h"}, {"name": "-h"}),
        ({"path": "x", "name": "--count=9"}, {"name": "--count=9", "count": 1}),
        ({"path": "-x", "name": "-x"}, {"path": "-x", "name": "-x"}),
        ({"path": "x", "count": 5}, {"count": 5}),
        ({"path": "x", "count": -3}, {"count": -3}),
        ({"path": "x", "dry_run": True}, {"dry_run": True}),
        ({"path": "x", "dry_run": False}, {"dry_run": False}),
        ({"path": "a b\tc"}, {"path": "a b\tc"}),
    ],
    ids=repr,
)
def test_a_value_is_passed_as_a_value(
    planted: Any, params: dict[str, Any], expected: dict[str, Any]
) -> None:
    argv = _argv_of(_run(planted, ["echo"], params))
    parsed = _parsed(_planted_tree(), ["echo"], argv)
    for key, value in expected.items():
        assert parsed[key] == value, (argv, parsed)
    assert parsed["json_out"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"path": None},
        {"path": 1.5},
        {"path": {"a": "b"}},
        {"path": ["a", "--dry-run"]},
        {"path": True},
        {"path": "x", "name": None},
        {"path": "x", "name": 1.5},
        {"path": "x", "name": {"a": "b"}},
        {"path": "x", "name": ["a", "--dry-run"]},
        {"path": "x", "name": []},
        {"path": "x", "name": False},
        {"path": "x", "count": 1.5},
        {"path": "x", "count": True},
        {"path": "x", "count": [1]},
        {"path": "x", "dry_run": "yes"},
        {"path": "x", "dry_run": "true"},
        {"path": "x", "dry_run": 1},
        {"path": "x", "dry_run": None},
        {"path": "x", "dry_run": [True]},
        {"path": "x", "tag": [1.5]},
        {"path": "x", "tag": [None]},
        {"path": "x", "tag": [["a"]]},
        {"path": "x", "tag": [{"a": "b"}]},
        {"path": "x", "tag": {"a": "b"}},
        {"path": "x", "tag": None},
    ],
    ids=repr,
)
def test_a_value_of_another_type_is_refused(
    planted: Any, recorder: Recorder, params: dict[str, Any]
) -> None:
    """Security review of #197, M5: a list spread into the argv, or a value
    whose type the parameter does not take, is refused before anything runs."""
    reply = _run(planted, ["echo"], params)
    assert (reply.status, reply.error()) == (403, "bad_value"), reply.body[:300]
    assert recorder.calls() == []


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["a", "--dry-run"], ["a", "--dry-run"]),
        (["--yes", "-x", "--", "--count=9"], ["--yes", "-x", "--", "--count=9"]),
        (["-"], ["-"]),
        ([1, "b"], ["1", "b"]),
    ],
    ids=repr,
)
def test_every_list_element_is_passed_as_a_value(
    planted: Any, tags: list[Any], expected: list[str]
) -> None:
    argv = _argv_of(_run(planted, ["echo"], {"path": "x", "tag": tags}))
    parsed = _parsed(_planted_tree(), ["echo"], argv)
    assert list(parsed["tag"]) == expected, argv
    assert (parsed["dry_run"], parsed["count"], parsed["path"]) == (False, 1, "x"), argv


def test_a_real_repeatable_option_takes_each_element_as_a_value(real: Any) -> None:
    """`profile save --preset` and `looks save --choice` repeat on main."""
    tree = _real_tree()
    presets = ["--yes", "-x", "--json"]
    argv = _argv_of(_run(real, ["profile", "save"], {"name": "n", "preset": presets}))
    parsed = _parsed(tree, ["profile", "save"], argv)
    assert (list(parsed["preset"]), parsed["name"]) == (presets, "n"), argv
    choices = ["1=2", "--race=3"]
    params = {"name": "-n", "race": "--choice", "sex": "-s", "choice": choices}
    argv = _argv_of(_run(real, ["looks", "save"], params))
    parsed = _parsed(tree, ["looks", "save"], argv)
    assert list(parsed["choice"]) == choices, argv
    assert (parsed["name"], parsed["race"], parsed["sex"]) == ("-n", "--choice", "-s"), argv


def test_log_tail_follow_is_refused(real: Any, recorder: Recorder) -> None:
    """Owner rulings 2026-10-15: a run must end, so `--follow` is refused."""
    reply = _run(real, ["log", "tail"], {"follow": True})
    assert (reply.status, reply.error()) == (403, "excluded")
    assert recorder.calls() == []
    for params in ({}, {"follow": False}, {"lines": 5}):
        argv = _argv_of(_run(real, ["log", "tail"], params))
        assert _parsed(_real_tree(), ["log", "tail"], argv)["follow"] is False, argv


@pytest.mark.parametrize("out", ["page.html", "-", "/tmp/x.html", ""])
def test_looks_page_out_is_refused(real: Any, recorder: Recorder, out: str) -> None:
    """Review of #198: `--out` can create a file anywhere outside an install,
    so v0.1 refuses it; `looks page` without it runs."""
    reply = _run(real, ["looks", "page"], {"out": out})
    assert (reply.status, reply.error()) == (403, "excluded"), reply.body[:300]
    reply = _run(real, ["looks", "page"], {"out": out, "build": "1.2.3.4"})
    assert (reply.status, reply.error()) == (403, "excluded"), reply.body[:300]
    assert recorder.calls() == []
    for params in ({}, {"build": "1.2.3.4"}):
        argv = _argv_of(_run(real, ["looks", "page"], params))
        assert _parsed(_real_tree(), ["looks", "page"], argv)["out"] is None, argv
    assert len(recorder.calls()) == 2


def test_no_shell_is_involved(
    planted: Any, recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[tuple[Any, Any]] = []
    original = subprocess.Popen.__init__

    def spy(self: Any, args: Any, *rest: Any, **kwargs: Any) -> None:
        spawned.append((args, kwargs.get("shell", False)))
        original(self, args, *rest, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    canary = tmp_path / "canary"
    hostile = [
        f"$(touch {canary})",
        f"`touch {canary}`",
        f"x; touch {canary}",
        f"x && touch {canary}",
        f"x | touch {canary}",
        f'x" & type nul > "{canary}',
        f"x\ntouch {canary}",
        "%PATH%",
        "$HOME",
        "*",
    ]
    for value in hostile:
        argv = _argv_of(_run(planted, ["echo"], {"path": "x", "name": value}))
        assert _parsed(_planted_tree(), ["echo"], argv)["name"] == value
    assert not canary.exists()
    assert len(spawned) == len(hostile), "every run is one process, started by Popen"
    for args, shell in spawned:
        assert not shell
        assert isinstance(args, (list, tuple)), "an argv list, never a command string"
        assert list(args[: len(recorder.argv)]) == recorder.argv


# ─── timeout and output cap ──────────────────────────────────────────────────


def _gone(pid: int, within: float = 10.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("command", ["sleep", "nap"])
def test_a_run_past_its_timeout_is_stopped_with_its_whole_tree(
    recorder: Recorder, command: str
) -> None:
    """`sleep`'s grandchild holds the run's pipes open; `nap`'s does not.
    Either way the whole tree dies and the reply arrives (review of #197, M2)."""
    with _serving(command=_planted_tree(), wowlab_argv=recorder.argv, timeout_s=5.0) as server:
        started = time.monotonic()
        result = _ran(_run(server, [command], {}))
        assert time.monotonic() - started < _HANG / 2
        assert result["timed_out"] is True
        child, grandchild = recorder.pids()
        assert _gone(child), "the timed-out process is killed, not left running"
        assert _gone(grandchild), "and so is every process it started"
        # The server is still answering afterwards.
        assert _argv_of(_run(server, ["echo"], {"path": "x"}))[0] == "echo"


def test_closing_the_server_stops_a_run_in_progress(recorder: Recorder) -> None:
    """`wowlab ui` ending must not leave a run behind (review of #197, M2)."""
    server = _ui().make_server(command=_planted_tree(), wowlab_argv=recorder.argv, timeout_s=600.0)
    serving = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    serving.start()

    def request() -> None:
        with contextlib.suppress(OSError, http.client.HTTPException):
            _run(server, ["sleep"], {})

    running = threading.Thread(target=request, daemon=True)
    running.start()
    child, grandchild = recorder.pids()

    def close() -> None:
        server.shutdown()
        server.server_close()

    closing = threading.Thread(target=close, daemon=True)
    closing.start()
    closing.join(_HANG / 2)
    assert not closing.is_alive(), "shutdown() and server_close() return"
    assert _gone(child)
    assert _gone(grandchild)
    running.join(_HANG / 2)
    assert not running.is_alive()


@pytest.mark.parametrize(("command", "stream"), [("flood", "stdout"), ("spew", "stderr")])
def test_an_endless_writer_is_cut_at_the_cap(recorder: Recorder, command: str, stream: str) -> None:
    """The cap is read as a stream: the process is killed at the cap, long
    before its timeout, and memory stays near the cap (review of #197, M3).
    `tracemalloc` counts every Python allocation in this process, the
    server's threads included."""
    cap = 64 * 1024
    with _serving(
        command=_planted_tree(),
        wowlab_argv=recorder.argv,
        timeout_s=_HANG * 2,
        output_cap_bytes=cap,
    ) as server:
        tracemalloc.start()
        try:
            started = time.monotonic()
            result = _ran(_run(server, [command], {}))
            elapsed = time.monotonic() - started
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert elapsed < _HANG / 2, "cut at the cap, not at the timeout"
        assert result["truncated"] is True
        assert result["timed_out"] is False
        assert 0 < len(result[stream].encode("utf-8")) <= cap
        assert len(result["stdout"].encode("utf-8")) <= cap
        assert len(result["stderr"].encode("utf-8")) <= cap
        assert peak < 16 * 1024 * 1024, f"peak Python allocation {peak} bytes"
        (child,) = recorder.pids()
        assert _gone(child), "the writer is killed at the cap"
        small = _ran(_run(server, ["echo"], {"path": "x"}))
        assert small["truncated"] is False


def test_every_run_has_a_timeout_and_a_cap_that_cannot_be_turned_off(
    recorder: Recorder,
) -> None:
    ui = _ui()
    assert 0 < ui.DEFAULT_TIMEOUT_S < math.inf
    assert isinstance(ui.DEFAULT_OUTPUT_CAP_BYTES, int)
    assert ui.DEFAULT_OUTPUT_CAP_BYTES > 0
    for bad in (None, 0, -1, math.inf, math.nan):
        with pytest.raises((ValueError, TypeError)):
            ui.make_server(wowlab_argv=recorder.argv, timeout_s=bad)
    for bad in (None, 0, -1):
        with pytest.raises((ValueError, TypeError)):
            ui.make_server(wowlab_argv=recorder.argv, output_cap_bytes=bad)


# ─── refused commands: every `--yes` leaf, and `ui` ──────────────────────────


def test_every_yes_leaf_of_the_real_tree_refuses_yes_and_ui_is_refused(
    real: Any, recorder: Recorder
) -> None:
    """Every `--yes` leaf goes through the confirm step (M14-02, M14-03;
    graded in test_ui_confirm.py and test_ui_confirm_writes.py), so each
    refuses a `yes` from the form before anything runs; `ui` is refused."""
    leaves = dict(_leaves(_real_tree()))
    every = {path for path, cmd in leaves.items() if _declares_yes(cmd)}
    assert {("snap", "gc"), ("doctor",), ("snap", "restore"), ("undo",)} <= every
    assert ("ui",) in leaves
    for path in sorted(every):
        for params in ({"yes": True}, {"yes": False}):
            reply = _run(real, path, params)
            assert (reply.status, reply.error()) == (403, "unknown_parameter"), (path, params)
    reply = _run(real, ["ui"], {})
    assert (reply.status, reply.error()) == (403, "excluded")
    assert recorder.calls() == []
    # Positive control: a read leaf next to them runs.
    assert _argv_of(_run(real, ["snap", "list"], {}))[:2] == ["snap", "list"]


@pytest.mark.parametrize("path", _PLANTED_YES, ids=" ".join)
def test_a_planted_yes_command_is_refused_constructed(
    planted: Any, recorder: Recorder, path: tuple[str, ...]
) -> None:
    for params in ({}, {"yes": True}, {"confirm": True}):
        reply = _run(planted, path, params)
        assert reply.status == 403
        assert reply.error() in ("excluded", "unknown_parameter"), reply.body[:300]
    reply = _run(planted, path, {})
    assert (reply.status, reply.error()) == (403, "excluded")
    assert recorder.calls() == []
    # Positive control: the planted leaves without `--yes` run on the same server.
    assert _argv_of(_run(planted, ["grp", "inner"], {}))[:2] == ["grp", "inner"]


# ─── static files ────────────────────────────────────────────────────────────


def test_static_files_are_the_package_templates(planted: Any) -> None:
    files = _ui().static_files()
    assert "/" in files
    assert files["/"].lstrip().lower().startswith(b"<!doctype html")
    for path, content in files.items():
        assert path.startswith("/")
        assert ".." not in path
        assert "\\" not in path
        assert "%" not in path
        assert "?" not in path
        reply = _send(planted, "GET", _target(planted, path))
        assert reply.status == 200, path
        assert reply.body == content, path


def _package_files() -> list[Path]:
    package = Path(importlib.import_module("wowlab_core").__file__ or "").parent
    return sorted(p for p in package.iterdir() if p.is_file())


def test_static_files_vouch_only_for_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #197, L2: the mapping cannot list a package source file, and
    reads nothing from the working directory or the user's home."""
    files = dict(_ui().static_files())
    for path, content in files.items():
        assert path == "/" or path.endswith((".html", ".css", ".js")), path
        for file in _package_files():
            if file.suffix in (".py", ".toml"):
                head = file.read_bytes()[:64]
                if len(head) >= 32:
                    assert head not in content, (path, file.name)
    (tmp_path / "index.html").write_bytes(b"<!DOCTYPE html>planted")
    (tmp_path / "app.js").write_bytes(b"planted()")
    monkeypatch.chdir(tmp_path)
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(tmp_path))
    assert dict(_ui().static_files()) == files


def test_a_package_file_that_is_not_a_template_is_not_served(planted: Any) -> None:
    for file in _package_files():
        for path in (f"/{file.name}", f"/static/{file.name}", f"/templates/{file.name}"):
            reply = _send(planted, "GET", _target(planted, path))
            assert reply.status == 404, path
            head = file.read_bytes()[:200]
            if len(head) >= 32:
                assert head not in reply.body, path


_TRAVERSALS = [
    "/../cli.py",
    "/./cli.py",
    "/..%2fcli.py",
    "/%2e%2e/cli.py",
    "/%2e%2e%2fcli.py",
    "/%252e%252e%252fcli.py",
    "/static/../../cli.py",
    "/static/..%2f..%2fcli.py",
    "/static/%2e%2e/%2e%2e/cli.py",
    "/..\\cli.py",
    "/%5c..%5ccli.py",
    "/static/..%5c..%5ccli.py",
    "//etc/passwd",
    "/etc/passwd",
    "/../../../../../../../../etc/passwd",
    "/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
    "/C:/Windows/win.ini",
    "/C:%5cWindows%5cwin.ini",
    "/%00",
    "/cli.py%00.html",
    "/..",
    "/.",
    "/~/.ssh/id_rsa",
]


@pytest.mark.parametrize("path", _TRAVERSALS)
def test_a_traversal_path_is_refused_constructed(planted: Any, path: str) -> None:
    reply = _send(planted, "GET", _target(planted, path))
    assert reply.status in (400, 403, 404), (path, reply.status)
    for file in _package_files():
        head = file.read_bytes()[:200]
        if len(head) >= 32:
            assert head not in reply.body, (path, file.name)
    assert b"root:" not in reply.body
    assert b"[fonts]" not in reply.body
    assert b"PRIVATE KEY" not in reply.body
    assert reply.body not in list(_ui().static_files().values())


# ─── `wowlab ui` itself ──────────────────────────────────────────────────────


def _listing(root: Path) -> dict[str, tuple[int, int]]:
    """Every path under `root` with its size and modification time."""
    return {
        str(path.relative_to(root)): (path.lstat().st_size, path.lstat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
    }


def _stand_in_dirs(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """`root` stands in for the working directory, HOME, the temp folder and
    the user data directory."""
    import tempfile

    import platformdirs

    monkeypatch.chdir(root)
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(root))
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(root))
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: root / "userdata")


@dataclass
class UiRun:
    made: list[tuple[tuple[Any, ...], dict[str, Any], Any]]
    opened: list[str]
    result: Any
    before: dict[str, tuple[int, int]]
    after: dict[str, tuple[int, int]]


def _invoke_ui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, browser: str) -> UiRun:
    """`wowlab ui` through CliRunner. Ctrl-C is raised from inside the real
    `serve_forever` loop (its `service_actions` hook), so a `ui` that calls
    `shutdown()` afterwards returns as it would in real use; the browser is a
    recorder. `browser` is "opens", "false" or "raises". `tmp_path` stands in
    for the working directory, HOME, the temp folder and the user data
    directory, and its listing is taken before and after."""
    import webbrowser

    from typer.testing import CliRunner

    from wowlab_core import cli

    ui = _ui()
    real_make = ui.make_server
    made: list[tuple[tuple[Any, ...], dict[str, Any], Any]] = []
    opened: list[str] = []

    def make(*args: Any, **kwargs: Any) -> Any:
        server = real_make(*args, **kwargs)
        made.append((args, kwargs, server))

        def ctrl_c(*_: Any, **__: Any) -> None:
            raise KeyboardInterrupt

        server.service_actions = ctrl_c
        return server

    def open_(url: str, *args: Any, **kwargs: Any) -> bool:
        opened.append(url)
        if browser == "raises":
            raise webbrowser.Error("no browser")
        return browser == "opens"

    monkeypatch.setattr(ui, "make_server", make)
    monkeypatch.setattr(webbrowser, "open", open_)
    _stand_in_dirs(monkeypatch, tmp_path)
    before = _listing(tmp_path)
    try:
        result = CliRunner().invoke(cli.app, ["ui"], prog_name="wowlab")
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C escaped `wowlab ui`")
    return UiRun(made, opened, result, before, _listing(tmp_path))


def _files_holding(root: Path, token: str) -> list[Path]:
    return [f for f in root.rglob("*") if f.is_file() and token.encode() in f.read_bytes()]


def test_ui_serves_make_server_defaults_opens_its_url_and_stops_on_ctrl_c(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review of #197, H1: the entry point the owner runs is tied to the
    server graded above."""
    caplog.set_level(logging.DEBUG)
    run = _invoke_ui(monkeypatch, tmp_path, "opens")
    assert len(run.made) == 1, "one server, from make_server"
    args, kwargs, server = run.made[0]
    assert (args, kwargs) == ((), {}), "make_server with its defaults"
    assert server.server_address[0] == "127.0.0.1"
    assert run.opened == [server.url]
    assert run.result.exit_code == 0, (run.result.output, run.result.exception)
    assert "Traceback" not in run.result.output
    assert server.socket.fileno() == -1, "server_close() ran"
    # The browser opened, so the token is printed nowhere.
    assert server.token not in run.result.stdout
    assert server.token not in run.result.stderr
    assert not any(server.token in r.getMessage() for r in caplog.records)
    assert _files_holding(tmp_path, server.token) == []
    assert run.after == run.before, "`wowlab ui` writes no file"


@pytest.mark.parametrize("browser", ["false", "raises"])
def test_ui_prints_the_url_once_when_no_browser_opens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    browser: str,
) -> None:
    """Owner ruling 2026-10-15: once, on stdout, and through no logger or file."""
    caplog.set_level(logging.DEBUG)
    run = _invoke_ui(monkeypatch, tmp_path, browser)
    ((_, _, server),) = run.made
    assert run.opened == [server.url]
    assert run.result.stdout.count(server.url) == 1, run.result.stdout
    # M16-42: the printed link is the boot link; the session key is printed nowhere.
    assert run.result.stdout.count(server.boot) == 1
    assert server.token not in run.result.stdout
    assert server.token not in run.result.stderr
    assert not any(server.token in r.getMessage() for r in caplog.records)
    assert not any(server.token in str(r.args) for r in caplog.records)
    assert _files_holding(tmp_path, server.token) == []
    assert run.result.exit_code == 0, (run.result.output, run.result.exception)
    assert server.socket.fileno() == -1, "server_close() ran"
    assert run.after == run.before, "`wowlab ui` writes no file"


def test_serving_and_running_write_no_file(
    recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0029: the server writes nothing itself (review of #197). The
    recorder's own files live outside the stand-in folder."""
    root = tmp_path / "stand-in"
    root.mkdir()
    (root / "existing.txt").write_text("kept", encoding="utf-8")
    _stand_in_dirs(monkeypatch, root)
    before = _listing(root)
    with _serving(command=_planted_tree(), wowlab_argv=recorder.argv) as server:
        assert _send(server, "GET", _target(server, "/")).status == 200
        assert _send(server, "GET", _target(server, "/api/commands")).status == 200
        assert _argv_of(_run(server, ["echo"], {"path": "x", "tag": ["a"]}))[0] == "echo"
        assert _run(server, ["danger"], {}).status == 403
        assert _send(server, "GET", "/").status == 403
        assert _send(server, "GET", _target(server, "/../cli.py")).status in (400, 403, 404)
    assert _listing(root) == before
