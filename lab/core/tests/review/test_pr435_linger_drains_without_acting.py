# Probe from security review of PR #435 (fix/ui-foreign-host-windows): the lingering close.
"""`UiServer.shutdown_request` now sends FIN after the reply and reads and
discards what the client still sends (bounded by `_LINGER_S` and
`_LINGER_BYTES`) before closing. These probes check that what is drained is
never acted on, that the linger is bounded in time and bytes, that lingering
connections do not stop the server answering, and that a socket that is
already closed is handled.

Constructed: loopback sockets only; no install is read or written. The
`wowlab` the server runs is a recorder script that appends one line per run.
"""

from __future__ import annotations

import contextlib
import json
import socket
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import ui


def _recorder_source(calls: Path) -> str:
    lines = [
        "import pathlib",
        "import sys",
        "p = pathlib.Path(sys.argv[0]).with_name('calls.txt')",
        "f = p.open('a', encoding='utf-8')",
        "f.write('run' + chr(10))",
        "f.close()",
        "print('[]')",
    ]
    return chr(10).join(lines) + chr(10)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Any, Path]]:
    calls = tmp_path / "calls.txt"
    script = tmp_path / "recorder_constructed.py"
    script.write_text(_recorder_source(calls), encoding="utf-8")
    server = ui.make_server(wowlab_argv=[sys.executable, str(script)])
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def _runs(calls: Path) -> int:
    return len(calls.read_text().splitlines()) if calls.exists() else 0


def _port(server: Any) -> int:
    return int(server.server_address[1])


CRLF = chr(13) + chr(10)


def _post_run(server: Any, host: str) -> bytes:
    body = json.dumps({"command": ["snap", "list"], "params": {}}).encode()
    target = "/api/run?" + urllib.parse.urlencode({"token": server.token})
    head = [
        "POST " + target + " HTTP/1.1",
        "Host: " + host,
        "Content-Type: application/json",
        "Content-Length: " + str(len(body)),
        "",
        "",
    ]
    return CRLF.join(head).encode() + body


def _get(target: str, host: str) -> bytes:
    return CRLF.join(["GET " + target + " HTTP/1.1", "Host: " + host, "", ""]).encode()


def _read_all(sock: socket.socket) -> bytes:
    out = b""
    while chunk := sock.recv(65536):
        out += chunk
    return out


def _ok_host(server: Any) -> str:
    return "127.0.0.1:" + str(_port(server))


def _connect(server: Any) -> socket.socket:
    return socket.create_connection(("127.0.0.1", _port(server)), timeout=30)


def test_positive_control_a_valid_run_is_recorded(served: tuple[Any, Path]) -> None:
    server, calls = served
    with _connect(server) as sock:
        sock.sendall(_post_run(server, _ok_host(server)))
        reply = _read_all(sock)
    assert reply.split()[1] == b"200", reply[:200]
    assert _runs(calls) == 1


@pytest.mark.parametrize("first", ["refused", "answered"])
def test_a_request_pipelined_behind_a_reply_is_never_run(
    served: tuple[Any, Path], first: str
) -> None:
    """A refused request (foreign Host) and an answered one (GET of the
    command list), each followed on the same connection by a fully valid run
    request, both in the first write and again after the reply."""
    server, calls = served
    good = _post_run(server, _ok_host(server))
    if first == "answered":
        target = "/api/commands?" + urllib.parse.urlencode({"token": server.token})
        lead = _get(target, _ok_host(server))
    else:
        lead = _post_run(server, "evil.example")
    whole = b""
    with _connect(server) as sock:
        sock.sendall(lead + good + good)
        time.sleep(0.3)
        whole += sock.recv(65536)
        with contextlib.suppress(OSError):
            sock.sendall(good)
        with contextlib.suppress(OSError):
            whole += _read_all(sock)
    assert whole.count(b"HTTP/1.0 ") == 1, whole[:300]
    time.sleep(0.5)
    assert _runs(calls) == 0


def test_the_linger_ends_within_its_deadline_against_a_silent_client(
    served: tuple[Any, Path],
) -> None:
    """A client that reads the reply and then neither sends nor closes."""
    server, _ = served
    with _connect(server) as sock:
        sock.sendall(_get("/", "evil.example"))
        _read_all(sock)
        start = time.monotonic()
        while time.monotonic() - start < 10:
            try:
                sock.sendall(b"x")
            except OSError:
                break
            time.sleep(0.1)
        elapsed = time.monotonic() - start
    assert elapsed < 4.0, elapsed


def test_the_linger_stops_reading_after_its_byte_bound(served: tuple[Any, Path]) -> None:
    """A client that streams without end after a refusal: the server stops
    reading well before the deadline once the byte bound was drained."""
    server, _ = served
    with _connect(server) as sock:
        sock.sendall(_get("/", "evil.example"))
        _read_all(sock)
        start = time.monotonic()
        with contextlib.suppress(OSError):
            while time.monotonic() - start < 10:
                sock.sendall(b"y" * 65536)
        elapsed = time.monotonic() - start
    assert elapsed < 1.5, elapsed


def test_lingering_connections_do_not_stop_a_new_request(served: tuple[Any, Path]) -> None:
    server, _ = served
    held = []
    try:
        for _ in range(64):
            held.append(_connect(server))
            held[-1].sendall(_get("/", "evil.example"))
        start = time.monotonic()
        with _connect(server) as sock:
            sock.sendall(_get("/", _ok_host(server)))
            reply = _read_all(sock)
        assert reply.split()[1] == b"403"
        assert time.monotonic() - start < 2.0
    finally:
        for held_sock in held:
            held_sock.close()


def test_shutdown_request_on_a_closed_socket_does_not_raise(served: tuple[Any, Path]) -> None:
    server, _ = served
    a, b = socket.socketpair()
    a.close()
    server.shutdown_request(a)
    c, d = socket.socketpair()
    d.close()
    start = time.monotonic()
    server.shutdown_request(c)
    assert time.monotonic() - start < 3.0
    b.close()
