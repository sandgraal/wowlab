"""`wowlab ui`: the local exposure (M16-42; security:F3 and F4, EV-41).

F4: the URL `wowlab ui` opens is in the browser's argv, which other local
accounts can read, and it stayed in the address bar. The opened URL now
carries a one-time boot key (`/?boot=<key>`). The key opens the shell and
the exchange, `POST /api/session`, which answers the session key once and
spends the boot key. Every other request carries the session key as `token`,
as before. `page.js` takes the query out of the address bar with
`history.replaceState` before its first request.

F3: `sv dump` reads any file it is named. From `/api/run` it always runs with
`--install-only`, so a file whose real path is not in the resolved install is
refused before a byte of it is read, and the refusal quotes none.

Inputs are constructed (labelled): the request targets and headers, the
outside "secret" files, the link out of the install, and the planted Typer
tree. The install is the real macOS capture, copied to `tmp_path`. Nothing
reads or writes a real install.
"""

from __future__ import annotations

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


def _exchange(server: Any, key: str | None = None, **kwargs: Any) -> _Reply:
    key = server.boot if key is None else key
    return _send(server, "POST", f"/api/session?boot={key}", **kwargs)


def _run(server: Any, command: list[str], params: dict[str, Any]) -> _Reply:
    body = json.dumps({"command": command, "params": params}).encode("utf-8")
    return _send(server, "POST", f"/api/run?token={server.token}", body=body)


# ─── F4: the boot key ────────────────────────────────────────────────────────


def test_the_opened_url_carries_the_boot_key_and_not_the_session_key(planted: Any) -> None:
    assert planted.url == f"http://127.0.0.1:{planted.port}/?boot={planted.boot}"
    assert re.fullmatch(r"[A-Za-z0-9_-]{43,}", planted.boot), "token_urlsafe(32)"
    assert planted.boot != planted.token
    assert planted.token not in planted.url


def test_the_boot_key_opens_the_shell_until_it_is_spent(planted: Any) -> None:
    shell = ui.static_files()["/"]
    for method in ("GET", "GET", "HEAD"):
        reply = _send(planted, method, f"/?boot={planted.boot}")
        assert reply.status == 200, (method, reply.body[:200])
        if method == "GET":
            assert reply.body == shell
    assert _exchange(planted).status == 200
    reply = _send(planted, "GET", f"/?boot={planted.boot}")
    assert (reply.status, reply.error()) == (403, "boot_spent")
    # The session key still opens the shell.
    assert _send(planted, "GET", f"/?token={planted.token}").status == 200


def test_the_exchange_answers_the_session_key_once(planted: Any) -> None:
    first = _exchange(planted)
    assert first.status == 200
    assert first.json() == {"token": planted.token}
    assert first.headers.get("Cache-Control") == "no-store"
    assert first.headers.get_all("Set-Cookie") is None
    for _ in range(2):
        again = _exchange(planted)
        assert (again.status, again.error()) == (403, "boot_spent")
        assert planted.token.encode() not in again.body


def test_two_exchanges_racing_get_one_key(planted: Any) -> None:
    replies: list[_Reply] = []
    lock = threading.Lock()
    gate = threading.Barrier(8)

    def go() -> None:
        gate.wait()
        reply = _exchange(planted)
        with lock:
            replies.append(reply)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(r.status for r in replies) == [200] + [403] * 7
    assert sum(planted.token.encode() in r.body for r in replies) == 1


def _wrong(key: str) -> list[str]:
    flipped = ("A" if key[0] != "A" else "B") + key[1:]
    return ["", "x", flipped, key[:-1], key + "x", key.upper()]


def test_a_wrong_boot_key_opens_nothing_and_spends_nothing(planted: Any) -> None:
    for wrong in _wrong(planted.boot):
        reply = _send(planted, "GET", f"/?boot={wrong}")
        assert (reply.status, reply.error()) == (403, "token"), wrong
        reply = _exchange(planted, wrong)
        assert reply.status == 403, wrong
        assert planted.token.encode() not in reply.body
    # Positive control: the real key still works, once.
    assert _exchange(planted).status == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/commands"),
        ("GET", "/api/looks-page"),
        ("POST", "/api/run"),
        ("GET", "/no-such-page"),
    ],
)
def test_the_boot_key_opens_only_the_shell_and_the_exchange(
    planted: Any, method: str, path: str
) -> None:
    body = json.dumps({"command": ["hello"], "params": {}}).encode() if method == "POST" else None
    reply = _send(planted, method, f"{path}?boot={planted.boot}", body=body)
    assert (reply.status, reply.error()) == (403, "token")
    # And spent, it opens them no more.
    assert _exchange(planted).status == 200
    reply = _send(planted, method, f"{path}?boot={planted.boot}", body=body)
    assert (reply.status, reply.error()) == (403, "token")


def test_the_boot_key_must_be_the_whole_query(planted: Any) -> None:
    for target in (
        f"/?boot={planted.boot}&boot={planted.boot}",
        f"/?boot={planted.boot}&x=1",
        f"/?bootx={planted.boot}",
    ):
        assert _send(planted, "GET", target).status == 403, target
    reply = _send(planted, "POST", f"/api/session?boot={planted.boot}&x=1")
    assert (reply.status, reply.error()) == (403, "token")
    # The session key does not open the exchange, which would answer it.
    reply = _send(planted, "POST", f"/api/session?token={planted.token}")
    assert (reply.status, reply.error()) == (403, "token")
    assert planted.token.encode() not in reply.body
    assert _exchange(planted).status == 200


def test_host_and_origin_come_before_the_boot_key(planted: Any) -> None:
    port = planted.port
    for headers, code in (
        ({"Host": "evil.example"}, "host"),
        ({"Host": f"evil.example:{port}"}, "host"),
        ({"Origin": "http://evil.example"}, "origin"),
        ({"Origin": "null"}, "origin"),
    ):
        reply = _exchange(planted, headers=headers)
        assert (reply.status, reply.error()) == (403, code), headers
        reply = _send(planted, "GET", f"/?boot={planted.boot}", headers=headers)
        assert (reply.status, reply.error()) == (403, code), headers
    # None of them spent it; the page's own request carries its Origin.
    reply = _exchange(planted, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert reply.status == 200


def test_get_on_the_exchange_is_not_allowed(planted: Any) -> None:
    reply = _send(planted, "GET", f"/api/session?token={planted.token}")
    assert (reply.status, reply.error()) == (405, "method_not_allowed")
    assert _exchange(planted).status == 200


def test_neither_key_is_in_any_other_response(planted: Any) -> None:
    """The boot key is in no response; the session key only in the one
    exchange that answered it."""
    replies = [
        _send(planted, "GET", f"/?boot={planted.boot}"),
        _send(planted, "GET", f"/?token={planted.token}"),
        _send(planted, "GET", f"/api/commands?token={planted.token}"),
        _send(planted, "GET", f"/api/commands?boot={planted.boot}"),
        _send(planted, "GET", f"/nope?boot={planted.boot}"),
        _exchange(planted, "wrong"),
    ]
    answered = _exchange(planted)
    replies += [_exchange(planted), _send(planted, "GET", f"/?boot={planted.boot}")]
    for reply in [*replies, answered]:
        assert planted.boot.encode() not in reply.body
        for name, value in reply.headers.items():
            assert planted.boot not in value and planted.token not in value, name
    for reply in replies:
        assert planted.token.encode() not in reply.body
    assert answered.json() == {"token": planted.token}


# ─── F4: page.js ─────────────────────────────────────────────────────────────


def _script() -> str:
    return (TEMPLATES / "page.js").read_text(encoding="utf-8")


def test_the_page_drops_the_query_before_its_first_request() -> None:
    script = _script()
    replace = script.index('window.history.replaceState(null, "", "/")')
    assert script.count("replaceState(") == 1
    assert replace < script.index("fetch(")
    # The session key lives in the closure: no storage, no cookie, no URL.
    for sink in ("sessionStorage", "localStorage", "document.cookie", "pushState"):
        assert sink not in script, sink


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
    const page = await (await browser.newContext()).newPage();
    const requests = [];
    page.on("request", (r) => { const p = new URL(r.url()).pathname; if (p !== "/favicon.ico") requests.push(r.method() + " " + p); });
    out.first = (await page.goto(url)).status();
    await page.waitForSelector("#commands button", { state: "attached", timeout: 60000 });
    out.href = await page.evaluate(() => location.href);
    out.requests = requests;
    const other = await (await browser.newContext()).newPage();
    const second = await other.goto(url);
    out.second = second.status();
    out.secondBody = await second.text();
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((error) => console.log(JSON.stringify({ error: String(error) })));
"""


def test_in_a_browser_the_link_works_once_and_leaves_the_address_bar(
    tmp_path: Path, planted: Any
) -> None:
    """In Chromium through Playwright: the boot link opens the page, which
    exchanges the key and shows its commands, the address bar is `/`, and the
    same link opened again is refused. Skipped unless Node can load
    Playwright and launch Chromium."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    script = tmp_path / "m16-42-boot.js"
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
    assert out["requests"][:3] == ["GET /", "POST /api/session", "GET /api/commands"]
    assert out["second"] == 403
    assert json.loads(out["secondBody"]) == {"error": "boot_spent"}


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
    assert result["stdout"] == ""
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
    assert only.exit_code != 0, only.output
    assert SECRET not in only.output


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
