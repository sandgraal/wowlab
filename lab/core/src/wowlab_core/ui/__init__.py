"""ui: the `wowlab ui` server, a local UI over the CLI (ADR-0029, §15.6, M13-24).

One `http.server.ThreadingHTTPServer`, bound to `127.0.0.1` on a port the
OS picks, serving one page shell and two JSON endpoints for as long as
`wowlab ui` runs. It runs CLI commands as subprocesses of the same `wowlab`,
with `--json`, and writes nothing itself: no file, no log line.

Every request is checked, in this order, before anything else is done with
it (a POST body included):
- `Host` is exactly `127.0.0.1:<port>` or `localhost:<port>`;
- an `Origin`, when present, is exactly `http://` plus that `Host`;
- the query parameter `token` is the server's token
  (`secrets.token_urlsafe(32)`), which appears only in the URL `wowlab ui`
  opens: never in a response, a header, a cookie, a log, or a child's argv
  or environment.
A refusal answers 403 `{"error": "host" | "origin" | "token"}`. Every
response, refusals and errors included, carries the same
Content-Security-Policy, which allows the server itself and the page's own
inline script and style by hash, and nothing else, and no CORS header. The
page shell is template text in this package (`templates/`), assembled into
one document: a `<script src>` would be a request without the token.

`GET /api/commands` lists the leaves of the Click tree read at start. `POST
/api/run` runs one leaf. Its argv is built only from the parameters the leaf
declares: options as `--name=value`, so a value is never read as an option,
arguments after `--`, flags given or not, never through a shell. Refused
(403 `excluded`, ADR-0029 amendment and owner rulings of 2026-10-15): every
leaf that declares `--yes` (read from the tree), `ui` itself, `log tail` with
`follow` (a run must end) and `looks page` with `out` (it can create a file
anywhere outside an install). At most `MAX_CONCURRENT_RUNS` run at once (503
`busy` past that). Each run has a timeout and an output cap, read as a stream
and counted on the encoded response. The run's whole process tree,
grandchildren included, is killed at exactly three points: when the run
passes its timeout, when its output reaches the cap, and when the server
closes while the run is in progress. The kill uses the OS and lists no
processes: the run's process group on POSIX, its Job Object on Windows
(`_Tree`). No process other than a run is ever opened or signalled. A run
that exits on its own is not killed. On POSIX, anything it started and left
running outlives it. On Windows, its job is closed after every run, which
kills whatever is left. None of the commands the server offers starts a
process.

The run is `[sys.executable, "-P", "-m", "wowlab_core", ...]`: the running
interpreter by absolute path, never a PATH lookup, with the working
directory kept off its import path.
"""

from __future__ import annotations

import base64
import codecs
import contextlib
import functools
import hashlib
import http.server
import importlib.resources
import json
import math
import os
import re
import secrets
import signal
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, override

from wowlab_core.ui import _winjob

__all__ = [
    "DEFAULT_OUTPUT_CAP_BYTES",
    "DEFAULT_TIMEOUT_S",
    "MAX_BODY_BYTES",
    "MAX_CONCURRENT_RUNS",
    "UiServer",
    "make_server",
    "static_files",
]

DEFAULT_TIMEOUT_S: float = 120.0
"""How long one run may take before its process tree is killed."""

DEFAULT_OUTPUT_CAP_BYTES: int = 16 * 1024 * 1024
"""How many bytes one run may print, stdout and stderr together."""

MAX_BODY_BYTES: int = 64 * 1024
"""The largest POST body the server reads."""

MAX_CONCURRENT_RUNS: int = 4
"""Runs in progress at once; one more is refused with 503 `busy`."""

_LOOPBACK = "127.0.0.1"
_READ_CHUNK = 64 * 1024
# How long a request may sit on a socket operation; an idle connection
# (a browser's speculative one) is dropped after this.
_SOCKET_TIMEOUT_S = 30.0
# How long the readers get to reach end of file once the run has ended.
_DRAIN_GRACE_S = 2.0
# How long server_close() waits for runs in progress to be killed.
_CLOSE_WAIT_S = 10.0
_POLL_S = 0.05

# The reason ADR-0029's 2026-10-15 amendment gives, word for word; the parity
# test (M13-26) holds it to that.
_EXCLUDED_YES = "confirm flow: M14"
_EXCLUDED_UI = "the UI itself"
# Parameters refused on a leaf that otherwise runs, by leaf path: whether
# only a true value is refused (else any value is), and why. Owner rulings
# of 2026-10-15.
_REFUSED_PARAMS: Mapping[tuple[str, ...], Mapping[str, tuple[bool, str]]] = {
    ("log", "tail"): {"follow": (True, "a run must end")},
    ("looks", "page"): {"out": (False, "it can create a file anywhere outside an install")},
}


# ─── the page shell ──────────────────────────────────────────────────────────


def _template(name: str) -> str:
    return (importlib.resources.files(__name__) / "templates" / name).read_text(encoding="utf-8")


def _hash_source(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


@functools.cache
def _shell() -> tuple[bytes, str]:
    """The page (its script and style inline) and the CSP that allows them."""
    style = _template("page.css")
    script = _template("page.js")
    page = (
        _template("page.html")
        .replace("<!--wowlab:style-->", style)
        .replace("<!--wowlab:script-->", script)
    )
    csp = "; ".join(
        [
            "default-src 'none'",
            f"script-src {_hash_source(script)}",
            f"style-src {_hash_source(style)}",
            "connect-src 'self'",
            "img-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
        ]
    )
    return page.encode("utf-8"), csp


def static_files() -> Mapping[str, bytes]:
    """Every URL path served as a static file, with its exact bytes. All of
    it is template text from this package's `templates/` folder."""
    return {"/": _shell()[0]}


# ─── the Click tree ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Param:
    """One declared parameter of a leaf, as the argv builder reads it."""

    name: str
    kind: str  # "argument", "option" or "flag"
    multiple: bool
    opt: str  # the option text a value is attached to, or the flag's; "" for an argument
    required: bool
    help: str
    supported: bool  # False: a shape this builder does not pass (any value is refused)


@dataclass(frozen=True)
class _Leaf:
    path: tuple[str, ...]
    help: str
    params: Mapping[str, _Param]
    json_opt: str | None
    excluded: str | None


def _opts(param: Any) -> list[str]:
    return [*getattr(param, "opts", ()), *getattr(param, "secondary_opts", ())]


def _read_param(param: Any) -> _Param:
    name = str(param.name)
    help_text = str(getattr(param, "help", None) or "")
    required = bool(getattr(param, "required", False))
    nargs = getattr(param, "nargs", 1)
    if getattr(param, "param_type_name", "") == "argument":
        return _Param(name, "argument", nargs == -1, "", required, help_text, nargs in (1, -1))
    opts = [str(o) for o in getattr(param, "opts", ())]
    long = [o for o in opts if o.startswith("--")]
    opt = long[0] if long else (opts[0] if opts else "")
    if getattr(param, "is_flag", False):
        supported = (
            not getattr(param, "secondary_opts", ())
            and getattr(param, "flag_value", True) is True
            and not getattr(param, "count", False)
            and bool(opt)
        )
        return _Param(name, "flag", False, opt, required, help_text, supported)
    supported = nargs == 1 and not getattr(param, "count", False) and bool(long)
    multiple = bool(getattr(param, "multiple", False))
    return _Param(name, "option", multiple, opt, required, help_text, supported)


def _leaves(command: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if hasattr(command, "list_commands"):
        from typer._click import Context

        ctx = Context(command)
        for name in command.list_commands(ctx):
            sub = command.get_command(ctx, name)
            if sub is not None:
                yield from _leaves(sub, (*path, str(name)))
    else:
        yield path, command


def _read_tree(root: Any) -> dict[tuple[str, ...], _Leaf]:
    leaves: dict[tuple[str, ...], _Leaf] = {}
    for path, command in _leaves(root):
        params: dict[str, _Param] = {}
        json_opt: str | None = None
        declares_yes = False
        for param in getattr(command, "params", ()):
            opts = _opts(param)
            if "--yes" in opts:
                declares_yes = True
            if "--json" in opts:
                json_opt = "--json"
                continue  # the server adds it itself; it is not a parameter here
            params[str(param.name)] = _read_param(param)
        excluded = _EXCLUDED_YES if declares_yes else None
        if path == ("ui",):
            excluded = _EXCLUDED_UI
        help_text = str(getattr(command, "help", None) or "").strip()
        leaves[path] = _Leaf(path, help_text, params, json_opt, excluded)
    return leaves


def _describe(leaf: _Leaf) -> dict[str, Any]:
    refused = _REFUSED_PARAMS.get(leaf.path, {})
    return {
        "path": list(leaf.path),
        "help": leaf.help,
        "excluded": leaf.excluded,
        "params": [
            {
                "name": p.name,
                "label": p.opt or p.name,
                "kind": p.kind,
                "multiple": p.multiple,
                "required": p.required,
                "help": p.help,
                "refused": refused[p.name][1] if p.name in refused else None,
            }
            for p in leaf.params.values()
            if p.supported
        ],
    }


# ─── building the argv ───────────────────────────────────────────────────────


class _RefusedError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _text(value: object) -> str:
    """A single value as argv text: a str or an int (never a bool)."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _RefusedError(403, "bad_value")
    text = str(value)
    if "\x00" in text:
        raise _RefusedError(403, "bad_value")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate from JSON
        raise _RefusedError(403, "bad_value") from None
    return text


def _build_argv(leaf: _Leaf, params: Mapping[str, object]) -> list[str]:
    """The argv after the `wowlab` prefix, from declared parameters only."""
    if leaf.excluded is not None:
        raise _RefusedError(403, "excluded")
    for key in params:
        if key not in leaf.params:
            raise _RefusedError(403, "unknown_parameter")
    refused = _REFUSED_PARAMS.get(leaf.path, {})
    for key, (only_true, _) in refused.items():
        if key in params and (not only_true or params[key] is True):
            raise _RefusedError(403, "excluded")
    options: list[str] = []
    arguments: list[str] = []
    skipped_argument = False
    for name, param in leaf.params.items():
        if name not in params:
            if param.kind == "argument":
                skipped_argument = True
            continue
        value = params[name]
        if not param.supported:
            raise _RefusedError(403, "bad_value")
        if param.kind == "flag":
            if not isinstance(value, bool):
                raise _RefusedError(403, "bad_value")
            if value:
                options.append(param.opt)
            continue
        if param.multiple:
            if not isinstance(value, list):
                raise _RefusedError(403, "bad_value")
            values = [_text(v) for v in value]
        else:
            values = [_text(value)]
        if param.kind == "argument":
            if skipped_argument:
                # An earlier argument left out would shift this one into its place.
                raise _RefusedError(403, "bad_value")
            arguments.extend(values)
        else:
            options.extend(f"{param.opt}={v}" for v in values)
    argv = [*leaf.path, *options]
    if leaf.json_opt is not None:
        argv.append(leaf.json_opt)
    if arguments:
        argv += ["--", *arguments]
    return argv


# ─── running one command ─────────────────────────────────────────────────────


def _json_text(text: str) -> bytes:
    """`text` as the inside of a JSON string, UTF-8 (`ensure_ascii=False`):
    what it costs in the response body."""
    return json.dumps(text, ensure_ascii=False)[1:-1].encode("utf-8")


class _Capture:
    """stdout and stderr of one run, read as streams and kept already encoded
    as the inside of JSON strings: at most `cap` bytes of response body,
    both together. A byte that is not UTF-8 becomes U+FFFD (3 bytes), and an
    escaped character costs its escape. Past the cap, reading stops and
    `full` is set; memory stays near the cap."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.lock = threading.Lock()
        self.pieces: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        self.used = 0
        self.truncated = False
        self.full = threading.Event()

    def _keep(self, name: str, text: str) -> bool:
        """Keep what fits of `text`; False once the cap is reached."""
        if not text:
            return True
        encoded = _json_text(text)
        with self.lock:
            room = self.cap - self.used
            if len(encoded) <= room:
                self.pieces[name].append(encoded)
                self.used += len(encoded)
                return True
            # The longest prefix that fits, cut between characters.
            low, high = 0, len(text)
            while low < high:
                mid = (low + high + 1) // 2
                if len(_json_text(text[:mid])) <= room:
                    low = mid
                else:
                    high = mid - 1
            fitted = _json_text(text[:low])
            self.pieces[name].append(fitted)
            self.used += len(fitted)
            self.truncated = True
            self.full.set()
            return False

    def pump(self, stream: IO[bytes], name: str) -> None:
        read = getattr(stream, "read1", stream.read)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = read(_READ_CHUNK)
                if not self._keep(name, decoder.decode(chunk, final=not chunk)):
                    return
                if not chunk:
                    return
        except (OSError, ValueError):
            return

    def body(self, name: str) -> list[bytes]:
        with self.lock:
            return list(self.pieces[name])


@dataclass
class _Run:
    exit_code: int
    stdout: list[bytes]  # the inside of a JSON string, UTF-8
    stderr: list[bytes]
    timed_out: bool
    truncated: bool

    def response(self) -> list[bytes]:
        """The JSON body, in pieces, never joined into one copy."""
        head = (
            f'{{"exit_code": {self.exit_code}, "timed_out": {json.dumps(self.timed_out)}, '
            f'"truncated": {json.dumps(self.truncated)}, "stdout": "'
        )
        return [head.encode("ascii"), *self.stdout, b'", "stderr": "', *self.stderr, b'"}']


_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class _Tree:
    """How a run is started so that it and everything it starts can be
    killed together, without listing processes.

    `kill` is called at exactly three points: when the run passes its
    timeout, when its output reaches the cap, and when the server closes
    while the run is in progress. It is always called before the run is
    reaped.

    POSIX: the run leads a new session and process group
    (`start_new_session`). `kill` signals that group while the run's
    unreaped pid still holds the group id. A descendant that calls `setsid`
    itself leaves the group and is not killed. After a run exits on its own,
    nothing is signalled, because the group id is no longer reserved. Any
    descendant still running then outlives the run, and that is a known
    residual. The `wowlab` CLI never forks, and none of the offered commands
    starts a process.

    Windows: the run is created suspended and resumed inside a Job Object
    (`_winjob`) that kills everything in it when terminated or closed;
    `end` closes it after every run, so nothing the run started outlives
    it."""

    def __init__(self) -> None:
        self.job: _winjob.Job | None = None

    def popen_kwargs(self) -> dict[str, Any]:
        if os.name == "posix":
            return {"start_new_session": True}
        self.job = _winjob.Job()
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | _winjob.CREATE_SUSPENDED
        return {"creationflags": flags}

    def started(self, process: subprocess.Popen[bytes]) -> None:
        if self.job is not None:
            self.job.adopt(process)

    def kill(self, process: subprocess.Popen[bytes]) -> None:
        """Kill the run and its descendants. Call only before it is reaped."""
        if self.job is not None:
            self.job.terminate()
        elif sys.platform != "win32" and process.returncode is None:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, _KILL)
        with contextlib.suppress(OSError):
            process.kill()

    def end(self) -> None:
        if self.job is not None:
            self.job.close()
            self.job = None


# ─── the server ──────────────────────────────────────────────────────────────


def _positive_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_s must be a number of seconds")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("timeout_s must be finite and above zero")
    return seconds


def _positive_bytes(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("output_cap_bytes must be an int")
    if value <= 0:
        raise ValueError("output_cap_bytes must be above zero")
    return value


class UiServer(http.server.ThreadingHTTPServer):
    """The `wowlab ui` server. Built by `make_server`, bound and not yet
    serving."""

    daemon_threads = True
    token: str
    url: str
    wowlab_argv: Sequence[str]

    def __init__(
        self,
        leaves: Mapping[tuple[str, ...], _Leaf],
        wowlab_argv: Sequence[str],
        timeout_s: float,
        output_cap_bytes: int,
    ) -> None:
        self.leaves = leaves
        self.wowlab_argv = tuple(wowlab_argv)
        self.timeout_s = timeout_s
        self.output_cap_bytes = output_cap_bytes
        self.token = secrets.token_urlsafe(32)
        self._runs_lock = threading.Lock()
        self._runs: set[int] = set()
        self._closing = threading.Event()
        self._idle = threading.Condition(self._runs_lock)
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)
        super().__init__((_LOOPBACK, 0), _Handler)
        self.port = int(self.server_address[1])
        self.url = f"http://{_LOOPBACK}:{self.port}/?token={self.token}"
        self.hosts = frozenset({f"{_LOOPBACK}:{self.port}", f"localhost:{self.port}"})
        self.commands_body = json.dumps(
            {"commands": [_describe(leaf) for leaf in leaves.values()]}, ensure_ascii=True
        ).encode("ascii")

    @override
    def server_bind(self) -> None:
        """Bind without `HTTPServer.server_bind`'s `socket.getfqdn()`, a
        name lookup that can stall startup."""
        socketserver.TCPServer.server_bind(self)
        self.server_name = _LOOPBACK
        self.server_port = int(self.server_address[1])

    @override
    def handle_error(self, request: Any, client_address: Any) -> None:
        """Print nothing: the default prints a traceback that can hold the
        request line, and with it the token."""

    @override
    def server_close(self) -> None:
        """Stop accepting, then kill every run in progress with its whole
        tree and wait (bounded) for them to end."""
        self._closing.set()
        super().server_close()
        deadline = time.monotonic() + _CLOSE_WAIT_S
        with self._idle:
            while self._runs and time.monotonic() < deadline:
                self._idle.wait(_POLL_S)

    def run(self, argv: list[str]) -> _Run:
        """One run of `wowlab` with `argv`, bounded in time and output."""
        if not self._slots.acquire(blocking=False):
            raise _RefusedError(503, "busy")
        try:
            return self._start(argv)
        finally:
            self._slots.release()

    def _start(self, argv: list[str]) -> _Run:
        capture = _Capture(self.output_cap_bytes)
        tree = _Tree()
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            with self._runs_lock:
                if self._closing.is_set():
                    raise _RefusedError(503, "closing")
                process = subprocess.Popen(
                    [*self.wowlab_argv, *argv],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    shell=False,
                    **tree.popen_kwargs(),
                )
                self._runs.add(process.pid)
            try:
                tree.started(process)
                return self._watch(process, tree, capture)
            finally:
                with self._idle:
                    self._runs.discard(process.pid)
                    self._idle.notify_all()
        finally:
            tree.end()

    def _watch(self, process: subprocess.Popen[bytes], tree: _Tree, capture: _Capture) -> _Run:
        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(target=capture.pump, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=capture.pump, args=(process.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + self.timeout_s
        timed_out = False
        ended = False
        while True:
            try:
                process.wait(_POLL_S)
                ended = True
                break
            except subprocess.TimeoutExpired:
                pass
            if capture.full.is_set() or self._closing.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
        if not ended:
            # Not reaped yet, so its pid and process group are still its own.
            tree.kill(process)
            process.wait()
        # Windows: whatever the run left behind dies with its job here.
        tree.end()
        for reader in readers:
            reader.join(_DRAIN_GRACE_S)
        if not any(reader.is_alive() for reader in readers):
            process.stdout.close()
            process.stderr.close()
        # A reader still alive here means a descendant still holds the pipe
        # (POSIX: one left behind by a run that exited on its own, or one
        # that called `setsid`). The reader is abandoned and stops reading
        # at the cap.
        return _Run(
            exit_code=int(process.returncode),
            stdout=capture.body("stdout"),
            stderr=capture.body("stderr"),
            timed_out=timed_out,
            truncated=capture.truncated,
        )


# ─── requests ────────────────────────────────────────────────────────────────

_DIGITS = re.compile(r"[0-9]+")


class _Handler(http.server.BaseHTTPRequestHandler):
    """One request. Nothing here logs, and no response echoes the request."""

    server: UiServer
    server_version = "wowlab"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = _SOCKET_TIMEOUT_S

    # ── output ──

    @override
    def version_string(self) -> str:
        return self.server_version

    @override
    def log_message(self, format: str, *args: Any) -> None:
        """Nothing is logged: a request line holds the token."""

    @override
    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", _shell()[1])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("ascii")
        self._reply(status, body, "application/json")

    def _error(self, status: int, code: str) -> None:
        self._json(status, {"error": code})

    @override
    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """A bare JSON error. The default page quotes the request line,
        which holds the token."""
        names = {400: "bad_request", 404: "not_found", 408: "timeout", 414: "too_long"}
        self._error(code, names.get(code, "error"))

    # ── checks ──

    def _check(self) -> str | None:
        """Host, Origin and the token, in that order; the refusal code, or None."""
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or hosts[0] not in self.server.hosts:
            return "host"
        origins = self.headers.get_all("Origin")
        if origins is not None and (len(origins) != 1 or origins[0] != "http://" + hosts[0]):
            return "origin"
        query = self.path.partition("?")[2].partition("#")[0]
        try:
            fields = urllib.parse.parse_qs(query, keep_blank_values=True, max_num_fields=32)
        except ValueError:
            return "token"
        given = fields.get("token", [])
        if len(given) != 1:
            return "token"
        expected = self.server.token.encode("ascii")
        if not secrets.compare_digest(given[0].encode("utf-8", "surrogatepass"), expected):
            return "token"
        return None

    def _route(self) -> str:
        return self.path.partition("?")[0].partition("#")[0]

    def _checked(self) -> str | None:
        """The request's path once it passed the checks, else None (refused)."""
        refusal = self._check()
        if refusal is not None:
            self._error(403, refusal)
            return None
        return self._route()

    # ── methods ──

    def do_GET(self) -> None:
        path = self._checked()
        if path is None:
            return
        files = static_files()
        if path in files:
            self._reply(200, files[path], "text/html; charset=utf-8")
        elif path == "/api/commands":
            self._reply(200, self.server.commands_body, "application/json")
        elif path == "/api/run":
            self._error(405, "method_not_allowed")
        else:
            self._error(404, "not_found")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_OPTIONS(self) -> None:
        if self._checked() is not None:
            self._error(405, "method_not_allowed")

    def do_POST(self) -> None:
        path = self._checked()
        if path is None:
            return
        if path != "/api/run":
            if path in static_files():
                self._error(405, "method_not_allowed")
            else:
                self._error(404, "not_found")
            return
        body = self._body()
        if body is None:
            return
        try:
            request = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            self._error(400, "bad_request")
            return
        try:
            run = self._run(request)
        except _RefusedError as refused:
            self._error(refused.status, refused.code)
            return
        except OSError:
            self._error(500, "run_failed")
            return
        pieces = run.response()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(sum(len(piece) for piece in pieces)))
        self.send_header("Connection", "close")
        self.end_headers()
        for piece in pieces:
            self.wfile.write(piece)

    def _body(self) -> bytes | None:
        """The POST body, read only once it is known to be small enough."""
        if self.headers.get_all("Transfer-Encoding"):
            self._error(400, "bad_request")
            return None
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not _DIGITS.fullmatch(lengths[0].strip()):
            self._error(411 if not lengths else 400, "bad_request")
            return None
        digits = lengths[0].strip()
        if len(digits) > 12 or int(digits) > MAX_BODY_BYTES:
            self._error(413, "too_large")
            return None
        length = int(digits)
        body = self.rfile.read(length)
        if len(body) != length:
            self._error(400, "bad_request")
            return None
        return body

    def _run(self, request: object) -> _Run:
        if not isinstance(request, dict):
            raise _RefusedError(400, "bad_request")
        command = request.get("command")
        params = request.get("params", {})
        if not isinstance(command, list) or not all(isinstance(p, str) for p in command):
            raise _RefusedError(400, "bad_request")
        if not isinstance(params, dict):
            raise _RefusedError(400, "bad_request")
        leaf = self.server.leaves.get(tuple(command))
        if leaf is None:
            raise _RefusedError(403, "not_a_command")
        return self.server.run(_build_argv(leaf, params))


# ─── the entry point ─────────────────────────────────────────────────────────


def _default_wowlab_argv() -> list[str]:
    """The running interpreter by absolute path, never a name on PATH, with
    the working directory kept off the import path (`-P`)."""
    if not sys.executable or not Path(sys.executable).is_absolute():
        raise RuntimeError("the running interpreter has no absolute path to start runs with")
    return [sys.executable, "-P", "-m", "wowlab_core"]


def make_server(
    *,
    command: Any = None,
    wowlab_argv: Sequence[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES,
) -> UiServer:
    """The `wowlab ui` server on `127.0.0.1` at a free port, bound and not
    yet serving: the caller runs `serve_forever()`, then `shutdown()` and
    `server_close()`.

    `command` is the root Click command whose leaves may run (`None`: the
    CLI's own). `wowlab_argv` starts "the same `wowlab`" (`None`: this
    interpreter, `-P -m wowlab_core`). `timeout_s` and `output_cap_bytes`
    bound every run and cannot be turned off."""
    seconds = _positive_seconds(timeout_s)
    cap = _positive_bytes(output_cap_bytes)
    if command is None:
        import typer

        from wowlab_core import cli

        command = typer.main.get_command(cli.app)
    if wowlab_argv is None:
        wowlab_argv = _default_wowlab_argv()
    if isinstance(wowlab_argv, str) or not wowlab_argv:
        raise TypeError("wowlab_argv must be a non-empty sequence of strings")
    return UiServer(_read_tree(command), [str(a) for a in wowlab_argv], seconds, cap)
