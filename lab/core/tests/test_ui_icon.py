"""The page's tab icon (M18-11): the wowlab mark, served on one fixed path
that needs no key, so the browser's icon request is never refused.

A browser asks for a page's icon with none of the page's headers, so a
keyed path would be refused (it was: `/favicon.ico` answered 403, an error
in the console on every load). The page names `/favicon.svg`; that one
path, with no query, answers `GET` and `HEAD` with the template file and
nothing else; every other check (Host, Origin, the method) still applies.
The servers here run on a planted Click tree or, for the browser, on the
two real captures side by side (`test_ui_account.py`'s `constructed`
install). Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import test_ui_account as hub_tab
import test_ui_explorer as first_stage
import test_ui_server as srv

recorder = srv.recorder
planted = srv.planted
both = hub_tab.both
home = hub_tab.home
server = hub_tab.server
no_env_flavor = hub_tab.no_env_flavor

ICON = "/favicon.svg"
_TEMPLATES = first_stage._TEMPLATES


def _icon_bytes() -> bytes:
    return (_TEMPLATES / "favicon.svg").read_bytes()


# ─── the page names it ───────────────────────────────────────────────────────


def test_the_page_names_one_icon_on_the_keyless_path() -> None:
    html = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    links = re.findall(r"<link\b[^>]*>", html)
    assert links == ['<link rel="icon" href="/favicon.svg" type="image/svg+xml">']
    # Nothing else in the page is fetched by the browser on its own: no src,
    # and no other href that is not a fragment.
    assert re.findall(r"\bsrc=", html) == []
    hrefs = re.findall(r'\bhref="([^"]*)"', html)
    assert [h for h in hrefs if not h.startswith("#")] == [ICON]


def test_the_icon_is_the_mark_and_holds_nothing_that_runs() -> None:
    text = _icon_bytes().decode("utf-8")
    assert text.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    # The header's mark: the same path.
    html = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    (mark,) = re.findall(r'<svg class="mark"[^>]*><path d="([^"]+)"/></svg>', html)
    assert f'd="{mark}"' in text
    for word in ("<script", "<style", "href", "<foreignObject", "<image", "url(", " on"):
        assert word not in text, word
    assert set(re.findall(r"<([a-zA-Z]+)", text)) == {"svg", "rect", "path"}


# ─── the server serves it, and only it, with no key ──────────────────────────


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_the_icon_needs_no_key(planted: Any, recorder: srv.Recorder, method: str) -> None:
    reply = srv._send(planted, method, ICON)
    assert reply.status == 200
    assert reply.all("Content-Type") == ["image/svg+xml"]
    assert reply.body == (_icon_bytes() if method == "GET" else b"")
    # The same headers as every response: the page's CSP, no cookie, no CORS.
    assert len(reply.all("Content-Security-Policy")) == 1
    assert reply.all("X-Content-Type-Options") == ["nosniff"]
    assert reply.all("Set-Cookie") == []
    assert [k for k, _ in reply.headers if k.lower().startswith("access-control-")] == []
    assert planted.token.encode() not in reply.body
    assert recorder.calls() == []


def test_the_icon_with_a_query_needs_the_key(planted: Any) -> None:
    for target in (f"{ICON}?", f"{ICON}?x=1", f"{ICON}?boot={planted.boot}", f"{ICON}?token=x"):
        reply = srv._send(planted, "GET", target)
        assert (reply.status, reply.error()) == (403, "token"), target
    # With the key it is the same file.
    reply = srv._send(planted, "GET", srv._target(planted, ICON))
    assert reply.status == 200 and reply.body == _icon_bytes()


def test_the_icon_still_checks_host_and_origin(planted: Any) -> None:
    port = srv._port(planted)
    for host in srv._bad_hosts(port):
        reply = srv._send(planted, "GET", ICON, host=host)
        assert (reply.status, reply.error()) == (403, "host"), host
    reply = srv._send(planted, "GET", ICON, origin="http://evil.example")
    assert (reply.status, reply.error()) == (403, "origin")
    reply = srv._send(planted, "GET", ICON, origin=f"http://127.0.0.1:{port}")
    assert reply.status == 200


def test_only_reading_the_icon_needs_no_key(planted: Any, recorder: srv.Recorder) -> None:
    for method in ("POST", "PUT", "DELETE", "OPTIONS"):
        reply = srv._send(planted, method, ICON, body=b"{}" if method == "POST" else None)
        assert reply.status in (403, 501), method
        assert reply.body != _icon_bytes()
    reply = srv._send(planted, "POST", srv._target(planted, ICON), body=b"{}")
    assert (reply.status, reply.error()) == (405, "method_not_allowed")
    assert recorder.calls() == []


@pytest.mark.parametrize(
    "path",
    ["/favicon.ico", "/favicon.svg/", "/FAVICON.SVG", "/favicon.svg%00", "/./favicon.svg",
     "/templates/favicon.svg", "/page.css", "/api/commands", "/api/looks-page"],
)  # fmt: skip
def test_no_other_path_opens_without_the_key(planted: Any, path: str) -> None:
    reply = srv._send(planted, "GET", path)
    assert reply.status in (400, 403, 404), path
    assert reply.body != _icon_bytes()


# ─── in a browser: no request is refused, no console error ───────────────────

_BROWSER_JS = r"""
"use strict";
let playwright;
try {
  playwright = require("playwright");
} catch (error) {
  console.log(JSON.stringify({ skip: "Playwright is not installed for Node: " + String(error.message).split("\n")[0] }));
  process.exit(0);
}
(async () => {
  const url = process.argv[2];
  let browser;
  try {
    browser = await playwright.chromium.launch();
  } catch (error) {
    try {
      browser = await playwright.chromium.launch({ channel: "chrome" });
    } catch (again) {
      console.log(JSON.stringify({ skip: "Chromium does not launch: " + String(error.message).split("\n")[0] }));
      return;
    }
  }
  const out = { console: [], refused: [], failed: [], icon: [] };
  try {
    const page = await (await browser.newContext()).newPage();
    page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") out.console.push(m.type() + ": " + m.text()); });
    page.on("pageerror", (e) => out.console.push("pageerror: " + String(e)));
    // A reload cancels the runs still in flight (net::ERR_ABORTED): the
    // page's own doing, not a refusal.
    page.on("requestfailed", (r) => {
      const why = r.failure() ? r.failure().errorText : "";
      if (!why.includes("ERR_ABORTED")) out.failed.push(new URL(r.url()).pathname + " " + why);
    });
    page.on("response", (r) => {
      const path = new URL(r.url()).pathname;
      if (path.startsWith("/favicon")) out.icon.push(path + " " + r.status());
      if (r.status() >= 400) out.refused.push(r.request().method() + " " + path + " " + r.status());
    });
    out.first = (await page.goto(url)).status();
    await page.waitForSelector("#char-cards .char-card", { state: "attached", timeout: 120000 });
    // The browser asks for the icon on its own time: give it a moment.
    await page.waitForTimeout(1500);
    out.reload = (await page.reload()).status();
    await page.waitForSelector("#char-cards .char-card", { state: "attached", timeout: 120000 });
    await page.waitForTimeout(1500);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((error) => console.log(JSON.stringify({ error: String(error) })));
"""


def test_in_a_browser_the_page_loads_with_no_refused_request_and_no_console_error(
    both: Path, server: Any, tmp_path: Path
) -> None:
    """In Chromium through Playwright, on the two real captures: the link
    and a reload load the Characters home, no response is a 4xx or 5xx (the
    icon included), no request fails, and the console holds no error or
    warning. Skipped unless Node can load Playwright and launch Chromium."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    script = tmp_path / "m18-11-icon.js"
    script.write_text(_BROWSER_JS, encoding="utf-8")
    done = subprocess.run(
        [node, str(script), server.url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    if "skip" in out:
        pytest.skip(out["skip"])
    assert "error" not in out, out["error"]
    assert out["first"] == 200 and out["reload"] == 200
    assert out["refused"] == [], out["refused"]
    assert out["failed"] == [], out["failed"]
    assert out["console"] == [], out["console"]
    # A headed Chrome asks for the icon; a headless shell may not ask at all.
    assert all(icon == "/favicon.svg 200" for icon in out["icon"]), out["icon"]
