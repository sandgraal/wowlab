# Probe from review of m13/24-ui-server; reproduces `wowlab ui` never answering a browser that `webbrowser.open` runs in the foreground.
"""`wowlab ui` calls `webbrowser.open(server.url)` and only then
`server.serve_forever()`. `webbrowser.open` returns only when the browser
command it ran exits for the browsers it runs synchronously: on a Linux
machine with no display it tries `www-browser`, `links`, `elinks`, `lynx`
and `w3m` in the foreground, and a `BROWSER` variable naming a program is
run the same way (`webbrowser.GenericBrowser.open` waits for it). Such a
browser connects, its request sits in the listen backlog, and nothing
answers it until the browser gives up, so the owner never sees the page
(ADR-0029: "starts an HTTP server ... and opens the browser at it").

Reproduced by hand on macOS with
`BROWSER='curl -s -o /dev/null -w CODE%{http_code} -m 8 %s' python -m
wowlab_core ui`: curl printed `CODE000` (no response in 8 s).

Here the browser is a recorder that fetches the URL synchronously with a
5 s timeout, as such a browser does. The positive control makes the same
fetch against `make_server()` once `serve_forever()` is running, and gets
200.

Constructed: no install is read or written; no command is run.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any

import pytest
from typer.testing import CliRunner

from wowlab_core import cli, ui

_FETCH_TIMEOUT_S = 5.0


def _fetch(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as reply:
            reply.read()
            return str(reply.status)
    except urllib.error.HTTPError as error:
        return str(error.code)
    except (TimeoutError, OSError) as error:
        return f"no response ({type(error).__name__})"


def test_positive_control_a_synchronous_fetch_of_a_serving_server_gets_the_page() -> None:
    server = ui.make_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _fetch(server.url) == "200"
    finally:
        server.shutdown()
        server.server_close()


def test_wowlab_ui_answers_a_browser_that_webbrowser_runs_in_the_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []
    real_make = ui.make_server

    def make(*args: Any, **kwargs: Any) -> Any:
        server = real_make(*args, **kwargs)
        deadline = time.monotonic() + 3 * _FETCH_TIMEOUT_S

        def ctrl_c_once_the_browser_is_done(*_: Any, **__: Any) -> None:
            if fetched or time.monotonic() > deadline:
                raise KeyboardInterrupt

        server.service_actions = ctrl_c_once_the_browser_is_done
        return server

    def blocking_browser(url: str, *args: Any, **kwargs: Any) -> bool:
        fetched.append(_fetch(url))
        return True

    monkeypatch.setattr(ui, "make_server", make)
    monkeypatch.setattr(webbrowser, "open", blocking_browser)
    result = CliRunner().invoke(cli.app, ["ui"], prog_name="wowlab")
    assert result.exit_code == 0, (result.output, result.exception)
    assert fetched == ["200"], (
        "the browser `webbrowser.open` ran got no page: the server starts "
        "serving only after `webbrowser.open` returns"
    )
