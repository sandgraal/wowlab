# Probe from review of m13/24-ui-server-tests; reproduces the `wowlab ui` Ctrl-C
# fake in `_invoke_ui` hanging forever on an implementation that calls
# `server.shutdown()` after `serve_forever()` raises KeyboardInterrupt.
"""CONSTRUCTED `wowlab_core.ui` stand-in and `ui` command (L8: our own seam).

`tests/test_ui_server.py::_invoke_ui` fakes Ctrl-C by replacing
`server.serve_forever` with a function that raises KeyboardInterrupt. The real
`socketserver.BaseServer.serve_forever` sets its internal "is shut down" event
in a `finally:`, so a `shutdown()` called after it returns (a common
`finally: server.shutdown(); server.server_close()`) returns at once. The fake
never sets that event, so the same `shutdown()` waits forever. The graders
have no per-test timeout (no pytest-timeout), so the run hangs rather than
fails; the seam in the module docstring does not forbid `shutdown()`.

Cases:
- control 1: a `ui` that does not call `shutdown()` finishes through
  `_invoke_ui` (the harness here works);
- control 2: with the real `serve_forever`, interrupted from inside its loop
  (`service_actions` raises KeyboardInterrupt), a `ui` that calls
  `shutdown()` finishes: the pattern is correct in real use;
- the defect: the same `ui` through `_invoke_ui` does not finish.

The fix is in the grader: raise KeyboardInterrupt from
`server.service_actions` (inside the real loop) instead of replacing
`serve_forever`.
"""

from __future__ import annotations

import http.server
import importlib.util
import secrets
import sys
import threading
import types
import webbrowser
from pathlib import Path
from typing import Any

import pytest
import typer

GRADERS = Path(__file__).resolve().parents[1] / "test_ui_server.py"


def _graders() -> Any:
    spec = importlib.util.spec_from_file_location("_m13_24t_graders", GRADERS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves annotations here
    spec.loader.exec_module(module)
    return module


def _fake_ui(made: list[Any]) -> types.ModuleType:
    module = types.ModuleType("wowlab_core.ui")

    def make_server() -> Any:
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler
        )
        token = secrets.token_urlsafe(32)
        server.token = token  # type: ignore[attr-defined]
        port = server.server_address[1]
        server.url = f"http://127.0.0.1:{port}/?token={token}"  # type: ignore[attr-defined]
        made.append(server)
        return server

    module.make_server = make_server  # type: ignore[attr-defined]
    return module


def _ui_body(*, call_shutdown: bool) -> None:
    server = sys.modules["wowlab_core.ui"].make_server()
    if not webbrowser.open(server.url):
        print(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if call_shutdown:
            server.shutdown()
        server.server_close()


def _app(*, call_shutdown: bool) -> typer.Typer:
    app = typer.Typer()

    @app.command("ui")
    def ui() -> None:
        _ui_body(call_shutdown=call_shutdown)

    @app.command("other")
    def other() -> None:
        """Keeps `ui` a subcommand."""

    return app


def _through_invoke_ui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, call_shutdown: bool
) -> tuple[bool, list[Any]]:
    graders = _graders()
    made: list[Any] = []
    monkeypatch.setitem(sys.modules, "wowlab_core.ui", _fake_ui(made))
    from wowlab_core import cli

    monkeypatch.setattr(cli, "app", _app(call_shutdown=call_shutdown))
    done = threading.Event()

    def go() -> None:
        graders._invoke_ui(monkeypatch, tmp_path, "opens")
        done.set()

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    finished = done.wait(10)
    if not finished:
        # Release the stuck shutdown() so no thread is left behind.
        for server in made:
            server._BaseServer__is_shut_down.set()
        thread.join(10)
    return finished, made


def test_control_ui_without_shutdown_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    finished, made = _through_invoke_ui(monkeypatch, tmp_path, call_shutdown=False)
    assert finished
    assert len(made) == 1


def test_control_shutdown_after_a_real_interrupted_serve_forever_returns() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)

    def ctrl_c() -> None:
        raise KeyboardInterrupt

    server.service_actions = ctrl_c  # type: ignore[method-assign]
    done = threading.Event()

    def go() -> None:
        try:
            server.serve_forever(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()
        done.set()

    threading.Thread(target=go, daemon=True).start()
    assert done.wait(10), "shutdown() after a real serve_forever returned at once"


def test_ui_that_calls_shutdown_after_ctrl_c_finishes_under_the_grader_fake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    finished, _ = _through_invoke_ui(monkeypatch, tmp_path, call_shutdown=True)
    assert finished, (
        "_invoke_ui's serve_forever replacement never sets the shut-down event, "
        "so a correct `ui` that calls shutdown() after Ctrl-C hangs forever"
    )
