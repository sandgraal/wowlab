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
the way the grader names (a logged token, a missing Host or Origin check, a
CORS header, an `'unsafe-inline'` CSP, a `--yes` leaf or `ui` let through,
no `--` before an argument, a shell, no timeout, no cap, a file served from
the package directory, a bind to every interface, a group run as a leaf).

Every expectation comes from ADR-0029 and its amendment of 2026-10-15
(accepted), `docs/LAB_PLAN.md` §15.6 and the M13-24T / M13-24 tickets in
`docs/BACKLOG.md`. Nothing here was read from an implementation.

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
  - ``wowlab_argv``: the argv prefix that starts "the same ``wowlab``"
    (``None`` means the implementer's default). Every test here passes a
    recorder script, so no test runs the real CLI or touches an install.
  - ``timeout_s`` (finite, > 0) and ``output_cap_bytes`` (> 0): per run.
    ``None``, zero, negative or non-finite values raise ``ValueError`` or
    ``TypeError``: there is no way to turn either off.

- The server object has ``token: str`` (``secrets.token_urlsafe(32)``) and
  ``url: str``, exactly ``http://127.0.0.1:<port>/?token=<token>``, the URL
  ``wowlab ui`` opens.
- ``DEFAULT_TIMEOUT_S: float`` and ``DEFAULT_OUTPUT_CAP_BYTES: int``.
- ``static_files() -> Mapping[str, bytes]``: every URL path the server serves
  as a static file (``"/"``, the page shell, among them), with its exact
  bytes, all from template text inside the package.

The HTTP surface:

- The token travels as the query parameter ``token`` on every request.
- ``GET /api/commands`` returns ``{"commands": [{"path": [str, ...], ...}]}``.
- ``POST /api/run`` takes the JSON body
  ``{"command": [str, ...], "params": {<Click parameter name>: value}}``.
  Values are ``str``, ``int`` or, for a flag, ``bool`` (true: the flag is
  given; false: it is not). The server adds ``--json`` itself. A run that
  happened answers 200 with
  ``{"exit_code": int, "stdout": str, "stderr": str, "timed_out": bool,
  "truncated": bool}``, whatever the exit code.
- A refusal answers 403 with the JSON body ``{"error": <code>}``:
  ``"token"``, ``"host"``, ``"origin"`` for the request checks;
  ``"not_a_command"`` when ``command`` is not the path of a leaf of the tree;
  ``"excluded"`` for a leaf that declares ``--yes`` and for ``ui``;
  ``"unknown_parameter"`` for a key the leaf does not declare.
- A path that is neither ``/api/...`` nor a key of ``static_files()`` answers
  404; a traversal path answers 400, 403 or 404, and never with a file.
- Every response, refusals and 404s included, carries one or more
  ``Content-Security-Policy`` headers whose ``default-src`` is ``'self'`` or
  ``'none'`` and whose every source, in every directive, is ``'self'``,
  ``'none'`` or a ``'sha256-…'``/``'sha384-…'``/``'sha512-…'`` hash. No host,
  scheme, ``*``, ``'unsafe-…'`` keyword, nonce or report endpoint.
- No response carries an ``Access-Control-*`` header.

Inputs are constructed (labelled): the planted Typer tree, the recorder
script, and hostile request targets and headers. This is our own HTTP
surface, not an external format (L8). Nothing reads or writes an install.
"""

from __future__ import annotations

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
# `timeout_s=1` must answer well inside this.
_HANG = 30.0
_FLOOD_BYTES = 4 * 1024 * 1024


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
        json_out: _JsonOpt = False,
    ) -> None:
        """Takes an argument, two valued options and a flag."""

    @app.command("sleep")
    def sleep(json_out: _JsonOpt = False) -> None:
        """The recorder sleeps for two minutes."""

    @app.command("flood")
    def flood(json_out: _JsonOpt = False) -> None:
        """The recorder writes 4 MiB to stdout and to stderr."""

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
_PLANTED_RUNNABLE = [("echo",), ("sleep",), ("flood",), ("grp", "inner")]


# ─── the recorder: the `wowlab` the server starts in these tests ─────────────

_RECORDER = """\
import json, os, sys, time
argv = sys.argv[1:]
with open({calls!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": argv, "env": dict(os.environ)}}) + "\\n")
if argv[:1] == ["sleep"]:
    with open({pid!r}, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    time.sleep(120)
elif argv[:1] == ["flood"]:
    sys.stdout.write("x" * {flood})
    sys.stdout.flush()
    sys.stderr.write("y" * {flood})
    sys.stderr.flush()
else:
    sys.stdout.write(json.dumps({{"argv": argv}}))
"""


@dataclass
class Recorder:
    argv: list[str]
    calls_file: Path
    pid_file: Path

    def calls(self) -> list[dict[str, Any]]:
        if not self.calls_file.exists():
            return []
        lines = self.calls_file.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]


@pytest.fixture
def recorder(tmp_path: Path) -> Recorder:
    calls = tmp_path / "calls.jsonl"
    pid = tmp_path / "sleep.pid"
    script = tmp_path / "fake_wowlab_constructed.py"
    script.write_text(
        _RECORDER.format(calls=str(calls), pid=str(pid), flood=_FLOOD_BYTES), encoding="utf-8"
    )
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_not_reachable_on_another_interface(planted: Any) -> None:
    address = _non_loopback_ipv4()
    if address is not None:
        with pytest.raises(OSError):
            socket.create_connection((address, _port(planted)), timeout=2).close()
    assert planted.server_address[0] == "127.0.0.1"


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_ui_is_a_leaf_with_no_way_to_bind_elsewhere() -> None:
    leaves = dict(_leaves(_real_tree()))
    assert ("ui",) in leaves, "`wowlab ui` is a command of the CLI"
    opts = {opt for param in leaves[("ui",)].params for opt in getattr(param, "opts", ())}
    assert not opts & {"--host", "--bind", "--address", "--port", "--listen", "--yes"}


# ─── the token ───────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_token_is_random_urlsafe_and_only_in_the_url(recorder: Recorder) -> None:
    with (
        _serving(wowlab_argv=recorder.argv) as one,
        _serving(wowlab_argv=recorder.argv) as two,
    ):
        for server in (one, two):
            assert re.fullmatch(r"[A-Za-z0-9_-]{43,}", server.token), "token_urlsafe(32)"
            assert server.url == f"http://127.0.0.1:{_port(server)}/?token={server.token}"
        assert one.token != two.token


_NO_TOKEN_PATHS = [
    ("GET", "/"),
    ("GET", "/api/commands"),
    ("POST", "/api/run"),
    ("GET", "/no-such-page"),
    ("HEAD", "/"),
]


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
@pytest.mark.parametrize(("method", "path"), _NO_TOKEN_PATHS)
def test_a_request_without_the_token_is_refused(
    planted: Any, recorder: Recorder, method: str, path: str
) -> None:
    body = json.dumps({"command": ["echo"], "params": {"path": "x"}}).encode()
    reply = _send(planted, method, path, body=body if method == "POST" else None)
    assert reply.status == 403
    if method != "HEAD":
        assert reply.error() == "token"
    assert recorder.calls() == []


def _wrong_tokens(token: str) -> list[str]:
    flipped = ("A" if token[0] != "A" else "B") + token[1:]
    return ["", "x", flipped, token[:-1], token + "x", token.upper(), f" {token}"]


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_a_foreign_host_is_refused(planted: Any, recorder: Recorder) -> None:
    port = _port(planted)
    for host in _bad_hosts(port):
        for path in ("/", "/api/commands"):
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_no_cors_header_on_any_response(planted: Any) -> None:
    for what, reply in _assorted_replies(planted):
        cors = [k for k, _ in reply.headers if k.lower().startswith("access-control-")]
        assert cors == [], (what, cors)


_HASH_SOURCE = re.compile(r"'sha(256|384|512)-[A-Za-z0-9+/]+={0,2}'")


def _csp_problems(value: str) -> list[str]:
    problems: list[str] = []
    directives: dict[str, list[str]] = {}
    for part in value.split(";"):
        words = part.split()
        if words:
            directives[words[0].lower()] = words[1:]
    if directives.get("default-src") not in (["'self'"], ["'none'"]):
        problems.append(f"default-src is {directives.get('default-src')}")
    for name, sources in directives.items():
        for source in sources:
            if source.lower() not in ("'self'", "'none'") and not _HASH_SOURCE.fullmatch(source):
                problems.append(f"{name} allows {source}")
    return problems


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("default-src 'self'", True),
        ("default-src 'none'; script-src 'self'; connect-src 'self'", True),
        ("default-src 'none'; style-src 'sha256-AbC+/9=';", True),
        ("default-src 'self' https://cdn.example", False),
        ("default-src 'self'; script-src 'self' 'unsafe-inline'", False),
        ("default-src 'self'; img-src data:", False),
        ("default-src *", False),
        ("script-src 'self'", False),
        ("default-src 'self'; report-uri /csp", False),
        ("default-src 'self'; connect-src http://127.0.0.1:1", False),
    ],
)
def test_the_csp_reading_used_by_the_graders_constructed(value: str, ok: bool) -> None:
    """The grader's own CSP reading, checked on both sides (no marker)."""
    assert (_csp_problems(value) == []) is ok


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_every_response_has_a_csp_allowing_only_the_server(planted: Any) -> None:
    for what, reply in _assorted_replies(planted):
        values = reply.all("Content-Security-Policy")
        assert values, f"{what}: no Content-Security-Policy"
        for value in values:
            assert _csp_problems(value) == [], (what, value)


# ─── what runs: leaves of the tree only ──────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_the_command_list_is_the_planted_tree(planted: Any) -> None:
    reply = _send(planted, "GET", _target(planted, "/api/commands"))
    assert reply.status == 200
    listed = {tuple(item["path"]) for item in reply.json()["commands"]}
    leaves = {path for path, _ in _leaves(_planted_tree())}
    assert listed <= leaves, f"listed but not a leaf: {listed - leaves}"
    assert set(_PLANTED_RUNNABLE) <= listed, "a new leaf appears with no UI code"
    assert ("grp",) not in listed


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_a_real_read_command_runs_with_json(real: Any, recorder: Recorder) -> None:
    argv = _argv_of(_run(real, ["snap", "list"], {}))
    assert [a for a in argv if a != "--"] == ["snap", "list", "--json"], argv
    argv = _argv_of(_run(real, ["explain"], {"path": "--root"}))
    assert str(_parsed(_real_tree(), ["explain"], argv)["path"]) == "--root"
    reply = _run(real, ["snap"], {})
    assert (reply.status, reply.error()) == (403, "not_a_command")


# ─── the argument vector ─────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_another_commands_parameter_is_unknown_here(planted: Any, recorder: Recorder) -> None:
    reply = _run(planted, ["grp", "inner"], {"name": "y"})
    assert (reply.status, reply.error()) == (403, "unknown_parameter")
    assert recorder.calls() == []


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_a_run_past_its_timeout_is_stopped(recorder: Recorder) -> None:
    with _serving(command=_planted_tree(), wowlab_argv=recorder.argv, timeout_s=1.0) as server:
        started = time.monotonic()
        result = _ran(_run(server, ["sleep"], {}))
        assert time.monotonic() - started < _HANG / 2
        assert result["timed_out"] is True
        pid = int(recorder.pid_file.read_text(encoding="utf-8"))
        assert _gone(pid), "the timed-out process is killed, not left running"
        # The server is still answering afterwards.
        assert _argv_of(_run(server, ["echo"], {"path": "x"}))[0] == "echo"


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_output_past_the_cap_is_cut(recorder: Recorder) -> None:
    cap = 64 * 1024
    with _serving(
        command=_planted_tree(), wowlab_argv=recorder.argv, output_cap_bytes=cap
    ) as server:
        started = time.monotonic()
        result = _ran(_run(server, ["flood"], {}))
        assert time.monotonic() - started < _HANG / 2
        assert result["truncated"] is True
        assert 0 < len(result["stdout"].encode("utf-8")) <= cap
        assert len(result["stderr"].encode("utf-8")) <= cap
        small = _ran(_run(server, ["echo"], {"path": "x"}))
        assert small["truncated"] is False


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_every_yes_leaf_of_the_real_tree_and_ui_are_refused(real: Any, recorder: Recorder) -> None:
    leaves = dict(_leaves(_real_tree()))
    refused = sorted(path for path, cmd in leaves.items() if _declares_yes(cmd))
    assert ("doctor",) in refused
    assert ("ui",) in leaves
    for path in refused:
        for params in ({}, {"yes": True}, {"yes": False}):
            reply = _run(real, path, params)
            assert (reply.status, reply.error()) == (403, "excluded"), (path, params)
    reply = _run(real, ["ui"], {})
    assert (reply.status, reply.error()) == (403, "excluded")
    assert recorder.calls() == []
    # Positive control: a read leaf next to them runs.
    assert _argv_of(_run(real, ["snap", "list"], {}))[:2] == ["snap", "list"]


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
def test_a_package_file_that_is_not_a_template_is_not_served(planted: Any) -> None:
    served = _ui().static_files()
    for file in _package_files():
        for path in (f"/{file.name}", f"/static/{file.name}", f"/templates/{file.name}"):
            if path in served:
                continue
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


@pytest.mark.xfail(strict=True, reason="M13-24 not implemented")
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
