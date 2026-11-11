"""`wowlab ui`: the local exposure (M16-42; security:F3 and F4, EV-41; M16-113).

F4: the URL `wowlab ui` opens is in the browser's argv, which other local
accounts can read, and it stayed in the address bar. The opened URL carries
a boot key (`/?boot=<key>`), not the session key, and `page.js` takes the
query out of the address bar with `history.replaceState` before its first
request. M16-113 (owner, 2026-11-11) replaced the one-time exchange: the
shell served for the boot key sets an `HttpOnly`, `SameSite=Strict`,
`Path=/` session cookie, accepted in place of `token`, so the link, a reload
and a second tab keep working until the server stops. Host and Origin are
still checked first, and a request with neither the cookie nor the token is
refused.

F3: `sv dump` reads any file it is named. From `/api/run` it always runs with
`--install-only`, so a file whose real path is not in the resolved install is
refused before a byte of it is read, and the refusal quotes none.

Inputs are constructed (labelled): the request targets and headers, the
outside "secret" files, the link out of the install, and the planted Typer
tree. The install is the real macOS capture, copied to `tmp_path`. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import cli, ui

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SV_REL = "WTF/Account/90000001#6/SavedVariables/RareScanner.lua"
TEMPLATES = Path(ui.__file__).resolve().parent / "templates"

SECRET = "sk-THIS-IS-A-SECRET-constructed"
# Not Lua: luadata's error would quote the bytes at its position.
NOT_LUA = "root:x:0:0:constructed-passwd-line:/root:/bin/sh\n"


# ─── serving ─────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(folder))
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(folder / name.lower()))
    return folder


@pytest.fixture
def install(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = home / "Games" / "World of Warcraft"
    root.mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv("WOWLAB_WOW_ROOT", str(root))
    return root


@pytest.fixture
def outside(tmp_path: Path) -> dict[str, Path]:
    """Two files outside the install (constructed): one that parses as Lua
    and holds a secret, one that does not parse."""
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    secret = folder / "fake_secret.lua"
    secret.write_text(f'secret_api_key = "{SECRET}"\n', encoding="utf-8")
    passwd = folder / "passwd"
    passwd.write_text(NOT_LUA, encoding="utf-8")
    return {"secret": secret, "passwd": passwd}


def _serve(served: Any) -> Iterator[Any]:
    thread = threading.Thread(target=served.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.shutdown()
        served.server_close()
        thread.join(10)


@pytest.fixture
def server(home: Path) -> Iterator[Any]:
    """The real server over the real CLI."""
    yield from _serve(ui.make_server())


def _planted_tree() -> Any:
    """A one-leaf tree (constructed) whose leaf prints nothing: the boot
    tests never run a command."""
    app = typer.Typer()

    @app.command("hello")
    def hello(json_out: bool = typer.Option(False, "--json")) -> None:
        """Say nothing."""

    @app.command("other")
    def other() -> None:
        """A second leaf, so the tree is a group."""

    return typer.main.get_command(app)


@pytest.fixture
def planted(home: Path) -> Iterator[Any]:
    yield from _serve(ui.make_server(command=_planted_tree(), wowlab_argv=["/nonexistent"]))


class _Reply:
    def __init__(self, status: int, headers: http.client.HTTPMessage, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)

    def error(self) -> Any:
        return self.json().get("error")


def _send(
    server: Any,
    method: str,
    target: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> _Reply:
    port = int(server.port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        sent = {"Host": f"127.0.0.1:{port}"}
        if body is not None:
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        connection.request(method, target, body=body, headers=sent)
        response = connection.getresponse()
        return _Reply(response.status, response.headers, response.read())
    finally:
        connection.close()


def _run(server: Any, command: list[str], params: dict[str, Any]) -> _Reply:
    body = json.dumps({"command": command, "params": params}).encode("utf-8")
    return _send(server, "POST", f"/api/run?token={server.token}", body=body)


def _open_link(server: Any, **kwargs: Any) -> _Reply:
    """What the browser does with the link `wowlab ui` opened."""
    return _send(server, "GET", f"/?boot={server.boot}", **kwargs)


def _session(server: Any) -> dict[str, str]:
    """The Cookie header the browser sends back after it opened the link."""
    reply = _open_link(server)
    assert reply.status == 200
    (cookie,) = reply.headers.get_all("Set-Cookie")
    pair = cookie.split(";")[0].strip()
    return {"Cookie": pair}


# ─── F4 and M16-113: the link and the session ────────────────────────────────


def test_the_opened_url_carries_the_boot_key_and_no_session_key(planted: Any) -> None:
    assert planted.url == f"http://127.0.0.1:{planted.port}/?boot={planted.boot}"
    for key in (planted.boot, planted.token, planted.cookie_key):
        assert re.fullmatch(r"[A-Za-z0-9_-]{43,}", key), "token_urlsafe(32)"
    assert len({planted.boot, planted.token, planted.cookie_key}) == 3
    assert planted.token not in planted.url and planted.cookie_key not in planted.url


def test_the_link_sets_a_strict_http_only_session_cookie(planted: Any) -> None:
    reply = _open_link(planted)
    assert reply.status == 200
    assert reply.body == ui.static_files()["/"]
    (cookie,) = reply.headers.get_all("Set-Cookie")
    parts = [part.strip() for part in cookie.split(";")]
    assert parts[0] == f"wowlab_ui_{planted.port}={planted.cookie_key}"
    assert sorted(parts[1:]) == ["HttpOnly", "Path=/", "SameSite=Strict"]
    # A session cookie: it is dropped with the browser session, never stored.
    assert "expires" not in cookie.lower() and "max-age" not in cookie.lower()
    assert reply.headers.get("Cache-Control") == "no-store"


def test_the_link_keeps_working(planted: Any) -> None:
    """M16-113: the link is not spent; it opens the shell every time."""
    shell = ui.static_files()["/"]
    for method in ("GET", "GET", "HEAD", "GET"):
        reply = _send(planted, method, f"/?boot={planted.boot}")
        assert reply.status == 200, (method, reply.body[:200])
        if method == "GET":
            assert reply.body == shell


def test_a_reload_works(planted: Any) -> None:
    """A reload asks for `/` with no query, and the cookie carries the
    session; so do the page's own requests."""
    cookie = _session(planted)
    for method in ("GET", "HEAD", "GET"):
        reply = _send(planted, method, "/", headers=cookie)
        assert reply.status == 200, (method, reply.body[:200])
        assert reply.headers.get_all("Set-Cookie") is None
    assert _send(planted, "GET", "/", headers=cookie).body == ui.static_files()["/"]
    reply = _send(planted, "GET", "/api/commands", headers=cookie)
    assert reply.status == 200
    assert [c["path"] for c in reply.json()["commands"]] == [["hello"], ["other"]]
    body = json.dumps({"command": ["nope"], "params": {}}).encode()
    reply = _send(planted, "POST", "/api/run", body=body, headers=cookie)
    assert (reply.status, reply.error()) == (403, "not_a_command"), "past the session check"


def test_a_second_tab_works(planted: Any) -> None:
    """A second tab in the same browser: the link again, or the bare address,
    both with the cookie the first tab got."""
    cookie = _session(planted)
    again = _session(planted)
    assert again == cookie, "the same session"
    assert _send(planted, "GET", "/", headers=cookie).status == 200
    assert _send(planted, "GET", f"/?boot={planted.boot}", headers=cookie).status == 200
    assert _send(planted, "GET", "/api/commands", headers=cookie).status == 200


def test_the_cookie_among_others_is_found(planted: Any) -> None:
    (pair,) = _session(planted).values()
    for header in (f"a=1; {pair}; b=2", f"{pair};other=x", f"  {pair}  "):
        reply = _send(planted, "GET", "/api/commands", headers={"Cookie": header})
        assert reply.status == 200, header


def test_a_request_with_no_session_is_refused(planted: Any) -> None:
    for method in ("GET", "HEAD"):
        reply = _send(planted, method, "/")
        assert reply.status == 403
        assert reply.headers.get("Content-Type") == "text/plain; charset=utf-8"
    _assert_no_session_sentence(planted, _send(planted, "GET", "/"))
    for target in ("/api/commands", "/api/looks-page", "/?token=wrong", "/?tok=x", "/?boot="):
        reply = _send(planted, "GET", target)
        assert reply.status == 403, target
    for target in ("/api/commands", "/api/looks-page"):
        assert _send(planted, "GET", target).error() == "token", target
    # The page with no query or a link's query answers the player in words;
    # any other query stays JSON.
    for target in ("/?boot=", f"/?boot={planted.token}"):
        _assert_no_session_sentence(planted, _send(planted, "GET", target))
    for target in ("/?token=wrong", "/?tok=x"):
        assert _send(planted, "GET", target).error() == "token", target
    body = json.dumps({"command": ["hello"], "params": {}}).encode()
    reply = _send(planted, "POST", "/api/run", body=body)
    assert (reply.status, reply.error()) == (403, "token")


def _assert_no_session_sentence(server: Any, reply: _Reply) -> None:
    """The player's sentence, as plain text, under the same headers as every
    response, and with no key in it."""
    assert reply.status == 403
    assert reply.headers.get("Content-Type") == "text/plain; charset=utf-8"
    assert reply.headers.get("Cache-Control") == "no-store"
    assert reply.headers.get("Content-Security-Policy") == ui._shell()[1]
    assert reply.headers.get_all("Set-Cookie") is None
    text = reply.body.decode("utf-8")
    assert "link wowlab ui opened" in text and "start it again" in text, text
    assert "already used" not in text
    for key in (server.boot, server.token, server.cookie_key):
        assert key not in text
    # Player copy: no JSON, no code, no ticket.
    assert "{" not in text and "M16" not in text and "cookie" not in text.lower()


def _wrong(key: str) -> list[str]:
    flipped = ("A" if key[0] != "A" else "B") + key[1:]
    return ["", "x", flipped, key[:-1], key + "x", key.upper()]


def test_a_wrong_session_is_refused(planted: Any) -> None:
    name = planted.cookie_name
    for wrong in _wrong(planted.cookie_key):
        reply = _send(planted, "GET", "/api/commands", headers={"Cookie": f"{name}={wrong}"})
        assert (reply.status, reply.error()) == (403, "token"), wrong
    # The right key under another name, or with no name, opens nothing.
    for header in (
        f"wowlab_ui_{planted.port + 1}={planted.cookie_key}",
        f"x{name}={planted.cookie_key}",
        planted.cookie_key,
        f"{name}",
    ):
        reply = _send(planted, "GET", "/api/commands", headers={"Cookie": header})
        assert (reply.status, reply.error()) == (403, "token"), header
    # The session key is not a cookie key, nor the reverse.
    reply = _send(planted, "GET", "/api/commands", headers={"Cookie": f"{name}={planted.token}"})
    assert reply.status == 403
    reply = _send(planted, "GET", f"/api/commands?token={planted.cookie_key}")
    assert reply.status == 403


def test_a_token_that_is_given_must_be_right_even_with_the_cookie(planted: Any) -> None:
    cookie = _session(planted)
    reply = _send(planted, "GET", "/api/commands?token=wrong", headers=cookie)
    assert (reply.status, reply.error()) == (403, "token")
    reply = _send(planted, "GET", f"/api/commands?token={planted.token}", headers=cookie)
    assert reply.status == 200


def test_a_wrong_boot_key_opens_nothing_and_sets_no_cookie(planted: Any) -> None:
    for wrong in _wrong(planted.boot):
        reply = _send(planted, "GET", f"/?boot={wrong}")
        assert reply.status == 403, wrong
        assert reply.headers.get_all("Set-Cookie") is None
    # Positive control.
    assert _open_link(planted).status == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/commands"),
        ("GET", "/api/looks-page"),
        ("POST", "/api/run"),
        ("POST", "/"),
        ("GET", "/no-such-page"),
    ],
)
def test_the_boot_key_opens_only_the_shell(planted: Any, method: str, path: str) -> None:
    body = json.dumps({"command": ["hello"], "params": {}}).encode() if method == "POST" else None
    reply = _send(planted, method, f"{path}?boot={planted.boot}", body=body)
    assert (reply.status, reply.error()) == (403, "token")
    assert reply.headers.get_all("Set-Cookie") is None


def test_the_boot_key_must_be_the_whole_query(planted: Any) -> None:
    for target in (
        f"/?boot={planted.boot}&boot={planted.boot}",
        f"/?boot={planted.boot}&x=1",
        f"/?bootx={planted.boot}",
    ):
        reply = _send(planted, "GET", target)
        assert reply.status == 403, target
        assert reply.headers.get_all("Set-Cookie") is None


def test_the_one_time_exchange_is_gone(planted: Any) -> None:
    """M16-113 removed `POST /api/session`: nothing answers a key in a body."""
    cookie = _session(planted)
    reply = _send(planted, "POST", f"/api/session?boot={planted.boot}")
    assert (reply.status, reply.error()) == (403, "token")
    reply = _send(planted, "POST", "/api/session", headers=cookie)
    assert (reply.status, reply.error()) == (404, "not_found")


_CROSS = [
    ({"Host": "evil.example"}, "host"),
    ({"Host": "evil.example:{port}"}, "host"),
    ({"Origin": "http://evil.example"}, "origin"),
    ({"Origin": "http://127.0.0.1:{other}"}, "origin"),
    ({"Origin": "null"}, "origin"),
]


def _cross(server: Any) -> list[tuple[dict[str, str], str]]:
    port = server.port
    return [
        ({k: v.format(port=port, other=port + 1) for k, v in headers.items()}, code)
        for headers, code in _CROSS
    ]


def test_a_cross_origin_request_is_refused_even_with_the_session(planted: Any) -> None:
    """Another site's page (CSRF) or a DNS-rebinding page: its Origin or Host
    is not the server's own, and the session does not help it."""
    cookie = _session(planted)
    body = json.dumps({"command": ["hello"], "params": {}}).encode()
    for headers, code in _cross(planted):
        sent = {**cookie, **headers}
        for method, target, payload in (
            ("GET", "/", None),
            ("GET", "/api/commands", None),
            ("GET", "/api/looks-page", None),
            ("POST", "/api/run", body),
            ("GET", f"/?boot={planted.boot}", None),
        ):
            reply = _send(planted, method, target, body=payload, headers=sent)
            assert (reply.status, reply.error()) == (403, code), (headers, target)
            assert reply.headers.get_all("Set-Cookie") is None, (headers, target)
    # The page's own request carries its own Origin.
    reply = _send(
        planted,
        "GET",
        "/api/commands",
        headers={**cookie, "Origin": f"http://127.0.0.1:{planted.port}"},
    )
    assert reply.status == 200


def test_the_session_of_an_earlier_server_is_refused(home: Path) -> None:
    """Ctrl-C ends the session: a new `wowlab ui` makes new keys, and the old
    link, cookie and token open nothing."""
    with _serving_planted() as old:
        old_url, old_boot, old_token = old.url, old.boot, old.token
        old_name, old_key = old.cookie_name, old.cookie_key
        old_cookie = _session(old)
        assert _send(old, "GET", "/", headers=old_cookie).status == 200, "positive control"
    with _serving_planted() as new:
        assert new.url != old_url
        assert {new.boot, new.token, new.cookie_key}.isdisjoint({old_boot, old_token, old_key})
        name = new.cookie_name
        for header in (
            f"{name}={old_key}",
            f"{old_name}={old_key}",
            f"{name}={old_key}; {old_name}={old_key}",
        ):
            reply = _send(new, "GET", "/", headers={"Cookie": header})
            _assert_no_session_sentence(new, reply)
            reply = _send(new, "GET", "/api/commands", headers={"Cookie": header})
            assert (reply.status, reply.error()) == (403, "token"), header
        reply = _send(new, "GET", f"/?boot={old_boot}")
        assert reply.status == 403
        assert reply.headers.get_all("Set-Cookie") is None
        reply = _send(new, "GET", f"/api/commands?token={old_token}")
        assert (reply.status, reply.error()) == (403, "token")
        # Positive control: the new link works.
        assert _send(new, "GET", "/", headers=_session(new)).status == 200


@contextlib.contextmanager
def _serving_planted() -> Iterator[Any]:
    served = _serve(ui.make_server(command=_planted_tree(), wowlab_argv=["/nonexistent"]))
    server = next(served)
    try:
        yield server
    finally:
        for _ in served:
            pass


def test_two_servers_keep_their_own_cookies(home: Path) -> None:
    """Cookies are kept by host, not port: each server's cookie has its own
    name, so opening one link does not end the other's session."""
    with _serving_planted() as one, _serving_planted() as two:
        assert one.cookie_name != two.cookie_name
        first, second = _session(one), _session(two)
        both = {"Cookie": first["Cookie"] + "; " + second["Cookie"]}
        assert _send(one, "GET", "/api/commands", headers=both).status == 200
        assert _send(two, "GET", "/api/commands", headers=both).status == 200
        assert _send(one, "GET", "/api/commands", headers=second).status == 403
        assert _send(two, "GET", "/api/commands", headers=first).status == 403


def test_no_key_is_in_any_response_but_the_one_cookie(planted: Any) -> None:
    """The boot and session keys are in no response; the cookie key only in
    the `Set-Cookie` of the shell served for the link."""
    cookie = _session(planted)
    replies = [
        _send(planted, "GET", f"/?token={planted.token}"),
        _send(planted, "GET", f"/api/commands?token={planted.token}"),
        _send(planted, "GET", "/api/commands", headers=cookie),
        _send(planted, "GET", "/", headers=cookie),
        _send(planted, "GET", f"/api/commands?boot={planted.boot}"),
        _send(planted, "GET", f"/nope?boot={planted.boot}"),
        _send(planted, "GET", "/nope", headers=cookie),
        _send(planted, "GET", "/"),
        _send(planted, "GET", f"/?boot={planted.boot}", headers={"Host": "evil.example"}),
    ]
    linked = _open_link(planted)
    for reply in [*replies, linked]:
        for key in (planted.boot, planted.token, planted.cookie_key):
            assert key.encode() not in reply.body
        for name, value in reply.headers.items():
            assert planted.boot not in value and planted.token not in value, name
            if name.lower() != "set-cookie":
                assert planted.cookie_key not in value, name
    for reply in replies:
        assert reply.headers.get_all("Set-Cookie") is None
    assert planted.cookie_key in linked.headers["Set-Cookie"]


def test_the_cli_sets_no_notice_and_prints_the_link_without_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M16-113: no "already used" notice, and the printed link is not
    called one-time."""
    from test_ui_server import _invoke_ui

    run = _invoke_ui(monkeypatch, tmp_path, "false")
    ((_, _, server),) = run.made
    assert not hasattr(server, "notice")
    assert "once" not in run.result.stderr
    assert run.result.stdout.count(server.url) == 1


# ─── F4: page.js ─────────────────────────────────────────────────────────────


def _script() -> str:
    return (TEMPLATES / "page.js").read_text(encoding="utf-8")


def test_the_page_drops_the_query_before_its_first_request() -> None:
    script = _script()
    replace = script.index('window.history.replaceState(null, "", "/")')
    assert script.count("replaceState(") == 1
    assert replace < script.index("fetch(")
    # The page holds no key: no storage, no script access to the cookie.
    for sink in ("sessionStorage", "localStorage", "document.cookie", "pushState"):
        assert sink not in script, sink
    # No key is read from the page's own URL, and none is asked for.
    for read in ('get("token")', "get('token')", 'get("boot")', "/api/session"):
        assert read not in script, read
    # Every request sends the cookie to the server's own origin only.
    assert 'credentials: "omit"' not in script
    assert script.count('credentials: "same-origin"') == script.count("fetch(")


_BOOT_JS = r"""
"use strict";
const playwright = require("playwright");
(async () => {
  const url = process.argv[2];
  let browser;
  try {
    browser = await playwright.chromium.launch();
  } catch (error) {
    console.log(JSON.stringify({ skip: "Chromium does not launch: " + String(error.message).split("\n")[0] }));
    return;
  }
  const out = {};
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const requests = [];
    page.on("request", (r) => { const p = new URL(r.url()).pathname; if (p !== "/favicon.ico") requests.push(r.method() + " " + p); });
    out.first = (await page.goto(url)).status();
    await page.waitForSelector("#commands button", { state: "attached", timeout: 60000 });
    out.href = await page.evaluate(() => location.href);
    out.cookieSeen = await page.evaluate(() => document.cookie);
    out.requests = requests.slice();
    const reloaded = await page.reload();
    out.reload = reloaded.status();
    await page.waitForSelector("#commands button", { state: "attached", timeout: 60000 });
    out.reloadButtons = await page.locator("#commands button").count();
    const tab = await context.newPage();
    const second = await tab.goto(new URL("/", url).href);
    out.tab = second.status();
    await tab.waitForSelector("#commands button", { state: "attached", timeout: 60000 });
    out.tabButtons = await tab.locator("#commands button").count();
    const linkAgain = await (await context.newPage()).goto(url);
    out.linkAgain = linkAgain.status();
    const other = await (await browser.newContext()).newPage();
    const stranger = await other.goto(new URL("/", url).href);
    out.stranger = stranger.status();
    out.strangerBody = await stranger.text();
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((error) => console.log(JSON.stringify({ error: String(error) })));
"""


def test_in_a_browser_the_link_a_reload_and_a_second_tab_work(tmp_path: Path, planted: Any) -> None:
    """In Chromium through Playwright: the link opens the page, which shows
    its commands, the address bar is `/`, a reload and a second tab work,
    the link works again, and another browser profile with no session is
    refused. Skipped unless Node can load Playwright and launch Chromium."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    script = tmp_path / "m16-113-session.js"
    script.write_text(_BOOT_JS, encoding="utf-8")
    done = subprocess.run(
        [node, str(script), planted.url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    if "Cannot find module 'playwright'" in done.stderr:
        pytest.skip("Playwright is not installed for Node")
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    if "skip" in out:
        pytest.skip(out["skip"])
    assert "error" not in out, out["error"]
    assert out["first"] == 200
    assert out["href"] == f"http://127.0.0.1:{planted.port}/"
    assert out["cookieSeen"] == "", "HttpOnly: the page cannot read it"
    assert out["requests"][:2] == ["GET /", "GET /api/commands"]
    assert out["reload"] == 200 and out["reloadButtons"] == 2
    assert out["tab"] == 200 and out["tabButtons"] == 2
    assert out["linkAgain"] == 200
    assert out["stranger"] == 403
    assert "start it again" in out["strangerBody"]


# ─── F3: sv dump from the UI ─────────────────────────────────────────────────


def _dump(server: Any, file: str) -> dict[str, Any]:
    reply = _run(server, ["sv", "dump"], {"file": file})
    assert reply.status == 200, reply.body[:300]
    result: dict[str, Any] = reply.json()
    assert not result["timed_out"] and not result["truncated"]
    return result


def test_sv_dump_offers_no_install_only_field(server: Any) -> None:
    reply = _send(server, "GET", f"/api/commands?token={server.token}")
    (dump,) = [c for c in reply.json()["commands"] if c["path"] == ["sv", "dump"]]
    assert dump["excluded"] is None
    assert "install_only" not in {p["name"] for p in dump["params"]}
    # Nor can a request switch it off.
    reply = _run(server, ["sv", "dump"], {"file": "x", "install_only": False})
    assert (reply.status, reply.error()) == (403, "unknown_parameter")


def test_sv_dump_reads_a_file_in_the_install(server: Any, install: Path) -> None:
    for file in (str(install / FLAVOR / SV_REL), SV_REL):
        result = _dump(server, file)
        assert result["exit_code"] == 0, result["stderr"]
        assert json.loads(result["stdout"])["file"]


@pytest.mark.parametrize("which", ["secret", "passwd"])
def test_sv_dump_refuses_a_file_outside_the_install_and_quotes_none_of_it(
    server: Any, install: Path, outside: dict[str, Path], which: str
) -> None:
    """security:F3: exit 3 before reading, and nothing of the file's bytes
    in stdout or stderr (constructed outside files)."""
    target = outside[which]
    result = _dump(server, str(target))
    assert result["exit_code"] == cli.EXIT_REFUSED, result
    # The UI runs --json: stdout holds the error line alone, as a report
    # (M16-22), and nothing of the file.
    report = cli.ErrorReport.model_validate_json(result["stdout"])
    assert f"wowlab: {report.message}" in result["stderr"].splitlines()
    assert SECRET not in result["stdout"]
    assert "not in the install" in result["stderr"]
    for text in (SECRET, "secret_api_key", "constructed-passwd-line", "root:x"):
        assert text not in result["stdout"] + result["stderr"], text


def test_sv_dump_refuses_a_link_out_of_the_install(
    server: Any, install: Path, outside: dict[str, Path]
) -> None:
    """A link in the install that points out of it is outside (constructed)."""
    link = install / FLAVOR / "WTF" / "Account" / "90000001#6" / "SavedVariables" / "Leak.lua"
    try:
        link.symlink_to(outside["secret"])
    except (OSError, NotImplementedError):
        pytest.skip("cannot make a symlink here")
    result = _dump(server, str(link))
    assert result["exit_code"] == cli.EXIT_REFUSED, result
    assert SECRET not in result["stdout"] + result["stderr"]


def test_sv_dump_refuses_a_traversal_out_of_the_install(
    server: Any, install: Path, outside: dict[str, Path]
) -> None:
    """`..` that climbs out of the flavor folder and the install
    (constructed)."""
    up = os.path.relpath(outside["secret"], install / FLAVOR)
    assert up.startswith("..")
    result = _dump(server, str(install / FLAVOR / up))
    assert result["exit_code"] == cli.EXIT_REFUSED, result
    assert SECRET not in result["stdout"] + result["stderr"]


def test_the_refusal_names_no_flag_the_player_never_gave(
    server: Any, install: Path, outside: dict[str, Path]
) -> None:
    result = _dump(server, str(outside["secret"]))
    assert "--install-only" not in result["stderr"], result["stderr"]
    assert "refused:" in result["stderr"]


def test_sv_dump_refuses_a_linked_folder_out_of_the_install(
    server: Any, install: Path, outside: dict[str, Path]
) -> None:
    """A folder of the install that is a link out of it (constructed): the
    file below it is outside."""
    folder = install / FLAVOR / "WTF" / "Account" / "90000001#6" / "Leaked"
    try:
        folder.symlink_to(outside["secret"].parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot make a symlink here")
    result = _dump(server, str(folder / outside["secret"].name))
    assert result["exit_code"] == cli.EXIT_REFUSED, result
    assert SECRET not in result["stdout"] + result["stderr"]


@pytest.mark.skipif(os.name == "nt", reason="Windows resolves another case to the stored one")
def test_sv_dump_refuses_the_install_named_in_another_case(server: Any, install: Path) -> None:
    """On POSIX, `realpath` keeps a spelling's case, so the install's own file
    named through another case of its folder is refused, never read: the
    check errs towards refusing (constructed spelling)."""
    variant = install.parent / install.name.swapcase()
    result = _dump(server, str(variant / FLAVOR / SV_REL))
    assert result["exit_code"] == cli.EXIT_REFUSED, result
    # The error alone, as a report (M16-22): nothing of the file.
    report = cli.ErrorReport.model_validate_json(result["stdout"])
    assert f"wowlab: {report.message}" in result["stderr"].splitlines()


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows link")
def test_sv_dump_refuses_a_junction_out_of_the_install(
    server: Any, install: Path, outside: dict[str, Path]
) -> None:
    import _winapi  # type: ignore[import-not-found,unused-ignore]

    junction = install / FLAVOR / "WTF" / "Account" / "90000001#6" / "Junction"
    _winapi.CreateJunction(str(outside["secret"].parent), str(junction))
    result = _dump(server, str(junction / outside["secret"].name))
    assert result["exit_code"] == cli.EXIT_REFUSED, result
    assert SECRET not in result["stdout"] + result["stderr"]


def test_the_page_cannot_name_the_install_sv_dump_is_measured_against(
    server: Any, install: Path, outside: dict[str, Path]
) -> None:
    """security round 1: with `--root` the page could name an install planted
    around the file (constructed: a copy of the capture beside the secret).
    `--install-only` measures against the install wowlab finds by itself and
    refuses a `--root` naming another; the page's own Preview, which passes
    the found install's root, still reads."""
    fake = outside["secret"].parent / "planted"
    fake.mkdir()
    shutil.copy2(CAPTURE / ".build.info", fake / ".build.info")
    shutil.copytree(CAPTURE / "forever", fake / FLAVOR)
    planted_file = fake / FLAVOR / "Secret.lua"
    shutil.copy2(outside["secret"], planted_file)
    reply = _run(server, ["sv", "dump"], {"file": str(planted_file), "root": str(fake)})
    assert reply.status == 200, reply.body[:300]
    result = reply.json()
    assert result["exit_code"] == cli.EXIT_REFUSED, result
    assert SECRET not in result["stdout"] + result["stderr"]
    # Without --root, the planted file is outside the install wowlab finds.
    result = _dump(server, str(planted_file))
    assert result["exit_code"] == cli.EXIT_REFUSED, result
    # Positive control: the found install's own root, as Preview passes it.
    params = {"file": str(install / FLAVOR / SV_REL), "root": str(install)}
    result = _run(server, ["sv", "dump"], params).json()
    assert result["exit_code"] == 0, result["stderr"]


# ─── F3: the CLI flag ────────────────────────────────────────────────────────


def test_the_cli_keeps_reading_an_outside_file_without_the_flag(
    install: Path, outside: dict[str, Path]
) -> None:
    """Only the UI passes `--install-only`; the CLI reads what it is named,
    as before."""
    runner = CliRunner()
    plain = runner.invoke(cli.app, ["sv", "dump", str(outside["secret"])], prog_name="wowlab")
    assert plain.exit_code == 0, plain.output
    assert SECRET in plain.stdout
    only = runner.invoke(
        cli.app, ["sv", "dump", "--install-only", str(outside["secret"])], prog_name="wowlab"
    )
    assert only.exit_code == cli.EXIT_REFUSED, only.output
    assert SECRET not in only.output


def test_install_only_refuses_when_no_install_is_found(
    home: Path, outside: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WOWLAB_WOW_ROOT", str(home / "no-install-here"))
    only = CliRunner().invoke(
        cli.app, ["sv", "dump", "--install-only", str(outside["secret"])], prog_name="wowlab"
    )
    assert only.exit_code == cli.EXIT_REFUSED, only.output
    assert "refused:" in only.output
    assert SECRET not in only.output


def test_install_only_reads_the_path_it_checked(
    install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """security round 1: the file read is the real path the check measured,
    not the spelling given again."""
    from wowlab_core import luadata

    read: list[Path] = []
    real_read = luadata.read

    def spy(path: Path, **kwargs: Any) -> Any:
        read.append(Path(path))
        return real_read(path, **kwargs)

    monkeypatch.setattr(luadata, "read", spy)
    given = install / FLAVOR / "WTF" / ".." / "WTF" / SV_REL.removeprefix("WTF/")
    done = CliRunner().invoke(
        cli.app, ["sv", "dump", "--install-only", "--json", str(given)], prog_name="wowlab"
    )
    assert done.exit_code == 0, done.output
    assert read == [Path(os.path.realpath(given))]
    assert ".." not in read[0].parts


def test_a_sv_dump_that_lost_its_flag_is_excluded_not_run_without_it(home: Path) -> None:
    """Fail closed (constructed tree): a `sv dump` leaf that does not declare
    `--install-only` as a plain flag is excluded, never run without it."""
    app = typer.Typer()
    sv = typer.Typer()
    app.add_typer(sv, name="sv")

    @sv.command("dump")
    def dump(file: str, json_out: bool = typer.Option(False, "--json")) -> None:
        """Dump."""

    @app.command("other")
    def other() -> None:
        """Another leaf."""

    for served in _serve(ui.make_server(command=typer.main.get_command(app), wowlab_argv=["/x"])):
        listed = _send(served, "GET", f"/api/commands?token={served.token}").json()
        (item,) = [c for c in listed["commands"] if c["path"] == ["sv", "dump"]]
        assert item["excluded"]
        reply = _run(served, ["sv", "dump"], {"file": "x"})
        assert (reply.status, reply.error()) == (403, "excluded")
