"""The `wowlab ui` server's listen backlog (M18-16).

`socketserver` listens with a backlog of 5. Connections the system completes
wait there until the serve loop accepts them; past the backlog the system
resets or refuses them (macOS: `ConnectionResetError` on connect), so a
browser's burst of connections, or the lingering-close probe's 64, failed
whenever the serve loop was slow to be scheduled on a loaded machine. The
server now asks for the system's maximum.

Constructed: loopback sockets only, against a server that is bound and
listening but deliberately not serving, which is the loaded machine's worst
case made exact. No install is read or written.
"""

from __future__ import annotations

import contextlib
import socket
import sys

from wowlab_core import ui

# Well past socketserver's 5 and the probe's 64, well under every system's
# own cap (macOS 128, Linux 4096).
_CONNECTIONS = 100


def test_constructed_the_backlog_is_the_systems_maximum() -> None:
    assert ui.UiServer.request_queue_size == socket.SOMAXCONN
    assert socket.SOMAXCONN > 5


def test_constructed_connections_wait_while_the_serve_loop_is_not_accepting() -> None:
    server = ui.make_server(wowlab_argv=[sys.executable, "-c", "pass"])
    port = int(server.server_address[1])
    with contextlib.ExitStack() as held:
        try:
            for _ in range(_CONNECTIONS):
                sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                held.enter_context(sock)
        finally:
            server.server_close()
