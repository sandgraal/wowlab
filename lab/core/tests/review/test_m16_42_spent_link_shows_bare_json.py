# Probe from review of m16/42-ui-local-exposure; reproduces a player whose link was spent first seeing bare JSON, not the "already used" sentence the README, INSTALL.md and `ui --help` promise
"""Constructed requests (not a real capture). README.md, docs/INSTALL.md and
`wowlab ui --help` say that when another account opens the link first, "your
page says the link was already used". The sentence lives only in `page.js`,
which runs only once the shell is served. When the other party's exchange
lands before the player's browser fetches `/?boot=<key>` (the usual order: a
script that read the browser's argv beats a starting browser), the shell is
refused 403 `{"error": "boot_spent"}` and no page ever runs, so the player
reads bare JSON. A reload (`GET /`, the query already gone) is refused the
same way with `{"error": "token"}`. The positive control: the served shell
does carry the sentence. Nothing reads or writes a real install.
"""

from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import ui

SENTENCE = b"already used"


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path))
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    served = ui.make_server()
    thread = threading.Thread(target=served.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.shutdown()
        served.server_close()
        thread.join(10)


def _send(server: Any, method: str, target: str) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", int(server.port), timeout=60)
    try:
        connection.request(method, target, headers={"Host": f"127.0.0.1:{server.port}"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_positive_control_the_shell_carries_the_sentence(server: Any) -> None:
    status, body = _send(server, "GET", f"/?boot={server.boot}")
    assert status == 200
    assert SENTENCE in body


def test_a_player_whose_link_was_spent_first_is_told_it_was_already_used(server: Any) -> None:
    # Another local account read the link from the browser's argv and
    # exchanged it before the player's browser asked for the page.
    assert _send(server, "POST", f"/api/session?boot={server.boot}")[0] == 200
    status, body = _send(server, "GET", f"/?boot={server.boot}")
    # The docs promise the page says so; the player gets bare JSON instead.
    assert SENTENCE in body, (status, body)
