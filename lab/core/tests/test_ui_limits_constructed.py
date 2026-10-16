"""`wowlab ui`: how many runs at once, and the cap on the encoded response
(M13-24, fix round 1 after the security review of #201).

Through the seam the M13-24T graders use (`make_server(wowlab_argv=...)`),
with constructed stand-ins for `wowlab`: no real command runs and nothing
reads or writes an install. These sit beside the graders and do not change
them.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer

from wowlab_core import ui

_WAITER = """\
import os, sys, time
started, go = {started!r}, {go!r}
with open(os.path.join(started, str(os.getpid())), "w", encoding="utf-8"):
    pass
deadline = time.monotonic() + 60
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.02)
"""

# Bytes that are not UTF-8: each becomes U+FFFD, 3 bytes in the response
# body, and was 6 (an escape) before the fix.
_INVALID = "import sys; sys.stdout.buffer.write(b'\\xff' * (1024 * 1024)); sys.stdout.flush()\n"


def _tree() -> Any:
    app = typer.Typer()

    @app.command("wait")
    def wait(json_out: bool = typer.Option(False, "--json")) -> None:
        """Waits for the test's go file."""

    @app.command("other")
    def other(json_out: bool = typer.Option(False, "--json")) -> None:
        """Keeps `wait` a subcommand."""

    return typer.main.get_command(app)


@contextmanager
def _serving(**kwargs: Any) -> Iterator[Any]:
    server = ui.make_server(command=_tree(), **kwargs)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def _post_run(server: Any) -> tuple[int, bytes]:
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        body = json.dumps({"command": ["wait"], "params": {}}).encode()
        conn.request(
            "POST",
            f"/api/run?token={server.token}",
            body=body,
            headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"},
        )
        reply = conn.getresponse()
        return reply.status, reply.read()
    finally:
        conn.close()


def test_a_run_past_the_concurrent_limit_is_busy_constructed(tmp_path: Path) -> None:
    started = tmp_path / "started"
    started.mkdir()
    go = tmp_path / "go"
    script = tmp_path / "waiter_constructed.py"
    script.write_text(_WAITER.format(started=str(started), go=str(go)), encoding="utf-8")
    assert ui.MAX_CONCURRENT_RUNS == 4
    with _serving(wowlab_argv=[sys.executable, str(script)]) as server:
        replies: list[tuple[int, bytes]] = []
        runners = [
            threading.Thread(target=lambda: replies.append(_post_run(server)), daemon=True)
            for _ in range(ui.MAX_CONCURRENT_RUNS)
        ]
        for runner in runners:
            runner.start()
        deadline = time.monotonic() + 30
        while len(list(started.iterdir())) < ui.MAX_CONCURRENT_RUNS:
            assert time.monotonic() < deadline, "the first runs never started"
            time.sleep(0.05)
        status, body = _post_run(server)
        assert (status, json.loads(body)) == (503, {"error": "busy"})
        assert len(list(started.iterdir())) == ui.MAX_CONCURRENT_RUNS, "the fifth never ran"
        go.write_text("", encoding="utf-8")
        for runner in runners:
            runner.join(30)
        assert sorted(status for status, _ in replies) == [200] * ui.MAX_CONCURRENT_RUNS
        # A slot is free again once a run ends.
        assert _post_run(server)[0] == 200


def test_the_cap_bounds_the_encoded_response_constructed(tmp_path: Path) -> None:
    script = tmp_path / "invalid_utf8_constructed.py"
    script.write_text(_INVALID, encoding="utf-8")
    cap = 64 * 1024
    with _serving(wowlab_argv=[sys.executable, str(script)], output_cap_bytes=cap) as server:
        status, body = _post_run(server)
    assert status == 200
    result = json.loads(body)
    assert result["truncated"] is True
    assert set(result["stdout"]) == {"�"}
    # The whole body: the cap plus the fixed JSON around the two strings.
    assert len(body) <= cap + 200, len(body)
    assert len(body) > cap - 200, "filled up to the cap"
