"""The Live view of `wowlab ui` (M22-05): the server's `GET /api/live` and
the page that polls it.

The install is the `macos-70888/` capture copied into `tmp_path`
(`parser/_crew_build.install`), found through `WOWLAB_WOW_ROOT` as the CLI
finds it with no `--root`; its combat logs are CONSTRUCTED
(`parser/_world_lines.py`), one with an invented zone name holding markup.
The `wowlab` the server would start is test_ui_server's recorder: no route
here runs a command. Nothing here reads a real install or writes outside
`tmp_path`.
"""

# ruff: noqa: F811 - `recorder` is test_ui_server's fixture, imported and requested by name

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from test_ui_server import Recorder, _send, _serving, _target, recorder  # noqa: F401

from wowlab_core import install, live, ui

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _crew_build import FLAVOR
from _crew_build import install as build_install
from _world_lines import FOE_A, log, you_hit, zone

TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
LOG = "WoWCombatLog-040126_021630.txt"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    found = build_install(tmp_path, None)
    logs = found / FLAVOR / "Logs"
    logs.mkdir()
    (logs / LOG).write_bytes(log(you_hit(FOE_A, 10)))
    monkeypatch.setenv(install.ENV_ROOT, str(found))
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    return found


@pytest.fixture
def server(root: Path, recorder: Recorder) -> Any:
    with _serving(command=None, wowlab_argv=recorder.argv) as made:
        yield made


def _state(root: Path) -> dict[str, tuple[str, int]]:
    return {
        str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_a_poll_answers_the_watch_and_writes_nothing(
    server: Any, root: Path, recorder: Recorder
) -> None:
    before = _state(root)
    reply = _send(server, "GET", _target(server, "/api/live"))
    assert reply.status == 200, reply.body[:300]
    assert reply.all("Content-Type") == ["application/json; charset=utf-8"]
    body = reply.json()
    assert body["found"] is True and body["poll_seconds"] == live.POLL_SECONDS
    watch = live.LiveWatch.model_validate(body["watch"])
    assert watch.flavor_folder == FLAVOR
    assert watch.log is not None and watch.log.name == LOG
    assert watch.log.zone == "Tirisfal Glades" and watch.log.zone_changes == 1
    assert watch.saved.files > 0 and watch.cost_us >= 0
    with (root / FLAVOR / "Logs" / LOG).open("ab") as f:
        f.write(
            (zone(100, instance=489, name="Invented Test Valley", difficulty=34) + "\r\n").encode()
        )
    again = live.LiveWatch.model_validate(
        _send(server, "GET", _target(server, "/api/live") + "&flavor=" + FLAVOR).json()["watch"]
    )
    assert again.log is not None and again.log.zone_changes == 2
    assert again.log.zone == "Invented Test Valley" and again.log.in_world is False
    _send(server, "HEAD", _target(server, "/api/live"))
    # Nothing in the install changed, and no command ran.
    root_log = root / FLAVOR / "Logs" / LOG
    after = _state(root)
    after.pop(str(root_log.relative_to(root)))
    before.pop(str(root_log.relative_to(root)))
    assert after == before
    assert recorder.calls() == []


def test_every_response_carries_the_same_csp_and_no_cors(server: Any) -> None:
    page = _send(server, "GET", _target(server, "/"))
    reply = _send(server, "GET", _target(server, "/api/live"))
    assert reply.all("Content-Security-Policy") == page.all("Content-Security-Policy")
    (csp,) = reply.all("Content-Security-Policy")
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp
    assert not [k for k, _ in reply.headers if k.lower().startswith("access-control-")]


def test_the_poll_needs_the_session_host_and_origin(server: Any, recorder: Recorder) -> None:
    port = int(server.server_address[1])
    assert (_send(server, "GET", "/api/live").status, "token") == (403, "token")
    assert _send(server, "GET", "/api/live").json() == {"error": "token"}
    wrong = _send(server, "GET", _target(server, "/api/live", "x"))
    assert (wrong.status, wrong.json()) == (403, {"error": "token"})
    host = _send(server, "GET", _target(server, "/api/live"), host="evil.example")
    assert (host.status, host.json()) == (403, {"error": "host"})
    origin = _send(server, "GET", _target(server, "/api/live"), origin="http://evil.example")
    assert (origin.status, origin.json()) == (403, {"error": "origin"})
    same = _send(server, "GET", _target(server, "/api/live"), origin=f"http://127.0.0.1:{port}")
    assert same.status == 200
    header = _send(server, "GET", "/api/live", headers=[("X-Wowlab-Session", server.token)])
    assert header.status == 200
    assert recorder.calls() == []


@pytest.mark.parametrize(
    "query",
    [
        "&flavor=../../etc",
        "&flavor=_no_such_",
        "&flavor=" + FLAVOR + "&flavor=" + FLAVOR,
        "&path=/etc/passwd",
        "&flavor=" + FLAVOR + "&x=1",
        "&a=1&b=2&c=3&d=4",
    ],
)
def test_a_query_that_names_anything_but_a_flavor_folder_is_refused(
    server: Any, query: str
) -> None:
    reply = _send(server, "GET", _target(server, "/api/live") + query)
    assert (reply.status, reply.json()) == (400, {"error": "bad_request"})


def test_other_methods_are_refused(server: Any) -> None:
    reply = _send(server, "POST", _target(server, "/api/live"), body=b"{}")
    assert (reply.status, reply.json()) == (405, {"error": "method_not_allowed"})


def test_no_install_answers_a_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    empty = tmp_path / "nothing here"
    empty.mkdir()
    monkeypatch.setenv(install.ENV_ROOT, str(empty))
    with _serving(command=None, wowlab_argv=recorder.argv) as made:
        reply = _send(made, "GET", _target(made, "/api/live"))
    assert reply.status == 200
    body = reply.json()
    assert body["found"] is False and "no game folder" in body["words"]
    assert "watch" not in body


def test_text_from_the_log_is_escaped_in_the_body_constructed(server: Any, root: Path) -> None:
    (root / FLAVOR / "Logs" / LOG).write_bytes(
        log(zone(5, instance=1, name="<img src=x onerror=1>&", difficulty=0))
    )
    reply = _send(server, "GET", _target(server, "/api/live"))
    assert b"<" not in reply.body and b">" not in reply.body and b"&" not in reply.body
    assert reply.json()["watch"]["log"]["zone"] == "<img src=x onerror=1>&"


# ─── the page ────────────────────────────────────────────────────────────────


def _script() -> str:
    return (TEMPLATES / "page.js").read_text(encoding="utf-8")


def test_the_page_has_a_live_section_and_view() -> None:
    html = (TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert '<button id="sec-live" class="sec"' in html and 'aria-controls="live-view"' in html
    assert '<section id="live-view" role="tabpanel"' in html
    for needed in ("live-watch", "live-match-lines", "live-session-lines", "live-status"):
        assert f'id="{needed}"' in html, needed


def test_the_page_polls_only_the_server_and_reads_with_live_recap() -> None:
    script = _script()
    block = script[script.index("// ─── the Live view") : script.index("// ─── tabs ───")]
    assert re.findall(r'fetch\(api\("([^"]*)"\)', block) == ["/api/live"]
    assert re.findall(r"runJson\(\[([^\]]*)\]", block) == ['"live", "recap"']
    assert 'credentials: "omit"' in block and "headers: keyed()" in block
    # Polling stops when the view is left and waits while the tab is hidden.
    assert "document.hidden" in block and "clearTimeout" in block
    assert 'if (name === "live") startLive();' in script
    # Text only: no HTML sink in the block.
    assert "innerHTML" not in block and "insertAdjacentHTML" not in block
