"""The Looks view of `wowlab ui`: `looks page` embedded (M14-04, §16.3, ADR-0029).

The page is `wowlab looks page` written to its default place, the user data
directory's `pages/looks.html`, by the real CLI in this process from the
recorded 1.60.1.70009 tables (`test_cli_looks`'s fixtures: no network, no
install, user data redirected into `tmp_path`). The server is the real
`wowlab ui` server over the real Click tree; its runs start a stand-in that
records being started (constructed), and the read route's tests check that
it never is.

Graded here, as the ticket's acceptance names them:
- `GET /api/looks-page` reads only `pages/looks.html`, the path the server
  builds itself: a traversal request and a request naming another file are
  refused, and no response holds the other file's bytes;
- the frame carries exactly `sandbox="allow-scripts"` and no `src`;
- the UI's CSP names the hashes of the current `lookspage._SCRIPT` and
  `lookspage._STYLE`, and those are the hashes of the script and style of a
  page `looks page` writes today; nothing else is added and
  `frame-ancestors 'none'` stays on every response.
The tab's behaviour runs under Node with the first stage's stand-in DOM
(`test_ui_explorer._HARNESS`), fed the real `looks page --json` report and
the real page. Whether a browser loads the frame is checked by hand and
reported in the PR (ADR-0027).

Constructed inputs (L8, hostile and boundary): files planted beside the page
or in its place (another page, a link, a FIFO, a folder, a non-page, bytes
that are not UTF-8, a page past the cap), and the failed runs the tab is fed.
"""

# ruff: noqa: F811  (fixtures imported from test_cli_looks are requested by name)

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from test_cli_looks import (
    BUILD,
    no_install,  # noqa: F401  (autouse: discovery finds no install)
    ok,
    source,  # noqa: F401  (autouse: recorded tables only)
    user_data,  # noqa: F401  (autouse: user data in tmp_path)
)
from test_ui_explorer import _HARNESS, _node, _page_ids

from wowlab_core import lookspage, ui

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
ROUTE = "/api/looks-page"
MARKER = "m14-04-other-file-marker"


# ─── the server ──────────────────────────────────────────────────────────────


class _Reply:
    def __init__(self, status: int, headers: http.client.HTTPMessage, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)

    def error(self) -> str | None:
        try:
            value = json.loads(self.body)
        except ValueError:
            return None
        return value.get("error") if isinstance(value, dict) else None


def _make(tmp_path: Path, cap: int = ui.DEFAULT_OUTPUT_CAP_BYTES) -> Any:
    """The real server over the real tree; a run would start a stand-in that
    leaves `started` in `tmp_path` (constructed)."""
    started = tmp_path / "started"
    stand_in = f"open({str(started)!r}, 'a').write('run\\n')"
    return ui.make_server(wowlab_argv=[sys.executable, "-c", stand_in], output_cap_bytes=cap)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Any]:
    server = _make(tmp_path)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
    assert not (tmp_path / "started").exists(), "the read route started a run"


def _get(server: Any, target: str, method: str = "GET") -> _Reply:
    """`target` as sent, query and all."""
    port = int(server.port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        connection.request(method, target, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return _Reply(response.status, response.headers, response.read())
    finally:
        connection.close()


def _route(server: Any, extra: str = "") -> _Reply:
    return _get(server, f"{ROUTE}?token={server.token}{extra}")


def _written() -> tuple[dict[str, Any], str]:
    """`looks page --json` as the CLI printed it, and the page it wrote."""
    stdout = ok("looks", "page", "--build", BUILD, "--json").stdout
    report: dict[str, Any] = json.loads(stdout)
    return report, Path(report["path"]).read_text(encoding="utf-8")


def _pages(user_data: Path) -> Path:
    return user_data / "pages"


def _plant_other(user_data: Path) -> list[Path]:
    """Pages that are not `pages/looks.html`, each holding MARKER: beside it,
    in a sub-folder, in the data folder outside `pages/`, and next to the
    data folder (constructed)."""
    data = lookspage.PAGE_HEADER + MARKER.encode() + b"</html>\n"
    planted = [
        _pages(user_data) / "other.html",
        _pages(user_data) / "sub" / "looks.html",
        user_data / "looks.html",
        user_data.parent / "looks.html",
    ]
    for path in planted:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return planted


# ─── the read route: confined to pages/looks.html ────────────────────────────


def test_the_route_answers_the_page_looks_page_wrote(served: Any, user_data: Path) -> None:
    report, page = _written()
    assert Path(report["path"]) == (_pages(user_data) / "looks.html").resolve()
    reply = _route(served)
    assert reply.status == 200, reply.body[:200]
    assert reply.headers["Content-Type"].startswith("application/json")
    assert reply.json() == {"page": page}
    # Escaped as a run's output is: no markup in the body, and no token.
    for raw in (b"<", b">", b"&"):
        assert raw not in reply.body
    assert served.token.encode() not in reply.body
    assert all(served.token not in value for _, value in reply.headers.items())


_NAMING = [
    "&file=other.html",
    "&path=other.html",
    "&path=..%2Fother.html",
    "&path=..%2F..%2Flooks.html",
    "&name=sub%2Flooks.html",
    "&page=other",
    "&file",
    "&token2=x",
]

_TRAVERSALS = [
    "/api/looks-page/other.html",
    "/api/looks-page/../other.html",
    "/api/looks-page/..%2fother.html",
    "/api/looks-page%2f..%2fother.html",
    "/api/looks-page/sub/looks.html",
    "/api/looks-page/",
    "/api/looks-page.html",
    "/api/looks-page;other.html",
    "/api/looks-page%00other.html",
    "/api/%2e%2e/pages/other.html",
    "/api/../pages/other.html",
    "/api/pages/other.html",
    "/api/pages/looks.html",
    "/pages/other.html",
    "/pages/looks.html",
    "/looks.html",
    "/other.html",
    "/api/looks-page/%2e%2e/%2e%2e/looks.html",
    "/api/looks-page\\..\\other.html",
    "/API/LOOKS-PAGE",
]


def test_a_request_naming_another_file_is_refused_constructed(served: Any, user_data: Path) -> None:
    """Any query field but the token is refused before anything is read, so
    no request names a file; the answer never holds another file's bytes."""
    _, page = _written()
    _plant_other(user_data)
    for extra in _NAMING:
        reply = _route(served, extra)
        assert (reply.status, reply.error()) == (400, "bad_request"), extra
        assert MARKER.encode() not in reply.body, extra
    # Positive control: the token alone gets the page, not the others.
    reply = _route(served)
    assert reply.status == 200 and reply.json() == {"page": page}
    assert MARKER.encode() not in reply.body


@pytest.mark.parametrize("path", _TRAVERSALS)
def test_a_traversal_request_is_refused_constructed(
    served: Any, user_data: Path, path: str
) -> None:
    _written()
    _plant_other(user_data)
    reply = _get(served, f"{path}?token={served.token}")
    assert reply.status in (400, 403, 404), (path, reply.status)
    assert MARKER.encode() not in reply.body, path
    assert b"<!DOCTYPE" not in reply.body and b"\\u003c!DOCTYPE" not in reply.body, path


def test_no_page_written_yet_is_404_even_with_others_planted_constructed(
    served: Any, user_data: Path
) -> None:
    _plant_other(user_data)
    assert not (_pages(user_data) / "looks.html").exists()
    reply = _route(served)
    assert (reply.status, reply.error()) == (404, "no_page")
    assert MARKER.encode() not in reply.body


def test_the_path_is_the_one_lookspage_names(
    served: Any, user_data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server asks `lookspage.default_page_path()`; when that names
    another place, it reads there and nowhere else (constructed place)."""
    _, page = _written()
    elsewhere = tmp_path / "moved" / "pages" / "looks.html"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(lookspage.PAGE_HEADER + MARKER.encode())
    monkeypatch.setattr(lookspage, "default_page_path", lambda: elsewhere)
    reply = _route(served)
    assert reply.status == 200 and MARKER in reply.json()["page"]
    assert reply.json()["page"] != page


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_link_is_not_followed_constructed(served: Any, user_data: Path, tmp_path: Path) -> None:
    """Neither `pages/looks.html` nor `pages/` is read through a link, even
    to a file that begins like a page."""
    _written()
    target = tmp_path / "linked.html"
    target.write_bytes(lookspage.PAGE_HEADER + MARKER.encode())
    page = _pages(user_data) / "looks.html"
    page.unlink()
    page.symlink_to(target)
    reply = _route(served)
    assert (reply.status, reply.error()) == (403, "not_a_page")
    assert MARKER.encode() not in reply.body
    # The folder itself a link, to a real folder holding a page.
    page.unlink()
    real = tmp_path / "real-pages"
    real.mkdir()
    (real / "looks.html").write_bytes(lookspage.PAGE_HEADER + MARKER.encode())
    _pages(user_data).rmdir()
    _pages(user_data).symlink_to(real, target_is_directory=True)
    reply = _route(served)
    assert (reply.status, reply.error()) == (403, "not_a_page")
    assert MARKER.encode() not in reply.body


def _replace_page(user_data: Path, content: bytes) -> None:
    page = _pages(user_data) / "looks.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(content)


@pytest.mark.parametrize(
    "content",
    [
        b"<!DOCTYPE html><html><body>" + MARKER.encode() + b"</body></html>",
        b"",
        lookspage.PAGE_HEADER[:-1],
        b"#!/bin/sh\n" + MARKER.encode(),
        lookspage.PAGE_HEADER + b"\xff\xfe" + MARKER.encode(),
    ],
    ids=["another-html", "empty", "short-header", "script", "not-utf8"],
)
def test_a_file_that_is_not_a_looks_page_is_refused_constructed(
    served: Any, user_data: Path, content: bytes
) -> None:
    _replace_page(user_data, content)
    reply = _route(served)
    assert (reply.status, reply.error()) == (403, "not_a_page")
    assert MARKER.encode() not in reply.body


def test_a_folder_in_its_place_is_refused_constructed(served: Any, user_data: Path) -> None:
    (_pages(user_data) / "looks.html").mkdir(parents=True)
    reply = _route(served)
    assert reply.status == 403 and reply.error() == "not_a_page"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_fifo_in_its_place_is_refused_without_blocking_constructed(
    served: Any, user_data: Path
) -> None:
    _pages(user_data).mkdir(parents=True)
    os.mkfifo(_pages(user_data) / "looks.html")
    reply = _route(served)  # http.client's timeout would fail a blocked read
    assert (reply.status, reply.error()) == (403, "not_a_page")


def test_a_page_past_the_output_cap_is_refused_constructed(user_data: Path, tmp_path: Path) -> None:
    """The body is bounded by the server's output cap, as a run's is."""
    text = lookspage.PAGE_HEADER + b"x" * 300
    _replace_page(user_data, text)
    body = len(b'{"page": "' + ui._json_text(text.decode("utf-8")) + b'"}')
    assert body > len(text)  # the header's markup is escaped
    for cap, ok_status in ((body, True), (body - 1, False), (len(text) - 1, False)):
        server = _make(tmp_path, cap)
        thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        try:
            reply = _route(server)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)
        if ok_status:
            assert reply.status == 200, cap
            assert len(reply.body) <= cap
        else:
            assert (reply.status, reply.error()) == (403, "too_large"), cap


def test_the_route_is_checked_like_every_other(served: Any, user_data: Path) -> None:
    """The token, Host and Origin come first; only GET and HEAD read it."""
    _written()
    for target in (ROUTE, f"{ROUTE}?token=wrong", f"{ROUTE}?file=x"):
        reply = _get(served, target)
        assert (reply.status, reply.error()) == (403, "token"), target
        assert b"DOCTYPE" not in reply.body
    reply = _get(served, f"{ROUTE}?token={served.token}", method="POST")
    assert reply.status in (405, 411), reply.status
    assert b"DOCTYPE" not in reply.body
    port = int(served.port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        connection.request("GET", f"{ROUTE}?token={served.token}", headers={"Host": "evil.example"})
        response = connection.getresponse()
        assert response.status == 403 and b"DOCTYPE" not in response.read()
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        connection.request(
            "GET",
            f"{ROUTE}?token={served.token}",
            headers={"Host": f"127.0.0.1:{port}", "Origin": "null"},
        )
        response = connection.getresponse()
        # A sandboxed frame's requests carry `Origin: null`: refused.
        assert response.status == 403 and b"DOCTYPE" not in response.read()
    finally:
        connection.close()
    assert _get(served, f"{ROUTE}?token={served.token}", method="HEAD").status == 200


# ─── the frame and the CSP ───────────────────────────────────────────────────


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self.styles: list[str] = []
        self._open: tuple[str, dict[str, str | None]] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag in ("script", "style"):
            self._open = (tag, dict(attrs))
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._open is not None and tag == self._open[0]:
            text = "".join(self._text)
            if tag == "script":
                self.scripts.append((self._open[1], text))
            else:
                self.styles.append(text)
            self._open = None


def _tags(text: str) -> _Tags:
    parsed = _Tags()
    parsed.feed(text)
    parsed.close()
    return parsed


def _shell(server: Any) -> _Reply:
    reply = _get(server, f"/?token={server.token}")
    assert reply.status == 200
    return reply


def test_the_frame_has_exactly_sandbox_allow_scripts_and_no_src(served: Any) -> None:
    body = _shell(served).body.decode("utf-8")
    frames = [attrs for tag, attrs in _tags(body).tags if tag in ("iframe", "frame", "embed")]
    frames += [attrs for tag, attrs in _tags(body).tags if tag == "object"]
    assert len(frames) == 1, frames
    [frame] = frames
    assert frame["sandbox"] == "allow-scripts"
    assert frame.get("id") == "looks-frame"
    for name in ("src", "srcdoc", "allow", "allowfullscreen", "csp", "name", "loading"):
        assert name not in frame, name
    assert set(frame) == {"id", "class", "sandbox", "title", "hidden"}
    assert body.count("<iframe") == 1
    # No other way to the same origin, a top navigation or a popup.
    page = body + (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    for token in ("allow-same-origin", "allow-top-navigation", "allow-popups", "allow-forms"):
        assert token not in page, token
    # The script never sets the frame's `src`, `sandbox` or any attribute
    # on it, and fills it only through `srcdoc`, with the route's text.
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    # Hidden at the start of a press (review of M14-04, C1), then filled and shown.
    assert re.findall(r"\bframe\.([a-zA-Z]+)\s*=(?!=)", script) == ["hidden", "srcdoc", "hidden"]
    assert "frame.srcdoc = read.page;" in script
    assert re.search(r"\.sandbox\b|[\"']sandbox[\"']", script) is None


def _directives(csp: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            assert words[0].lower() not in out, words[0]
            out[words[0].lower()] = words[1:]
    return out


def _sha(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode() + "'"


def test_the_csp_names_the_hashes_of_the_current_looks_templates(
    served: Any, user_data: Path
) -> None:
    """The two hashes added to the UI's CSP are those of `lookspage._SCRIPT`
    and `_STYLE` as they are now, and of the executable script and the style
    of a page `looks page` writes today; nothing else is added."""
    reply = _shell(served)
    body = reply.body.decode("utf-8")
    shell = _tags(body)
    [(_, own_script)] = shell.scripts
    [own_style] = shell.styles
    directives = _directives(reply.headers["Content-Security-Policy"])
    assert directives == {
        "default-src": ["'none'"],
        "script-src": [_sha(own_script), _sha(lookspage._SCRIPT)],
        "style-src": [_sha(own_style), _sha(lookspage._STYLE)],
        "connect-src": ["'self'"],
        "img-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "base-uri": ["'none'"],
        "form-action": ["'none'"],
    }
    # The page as written: its one executable script and its one style are
    # exactly the two the CSP names, and its own CSP names the same hashes.
    _, page = _written()
    written = _tags(page)
    executable = [text for attrs, text in written.scripts if attrs.get("type") is None]
    assert len(executable) == 1 and len(written.styles) == 1
    assert _sha(executable[0]) == directives["script-src"][1]
    assert _sha(written.styles[0]) == directives["style-src"][1]
    data = [attrs for attrs, _ in written.scripts if attrs.get("type") is not None]
    assert data == [{"type": "application/json", "id": lookspage.DATA_ELEMENT_ID}]
    own = lookspage.content_security_policy()
    assert directives["script-src"][1] in own and directives["style-src"][1] in own


_BASE_URL_READS = ("baseURI", "document.URL", "documentURI", "ancestorOrigins", "location.href")


def test_no_script_the_frame_may_run_reads_the_opened_url(served: Any) -> None:
    """A `srcdoc` document takes its base URL from the page that holds it, so
    inside the frame `document.baseURI` is the opened URL, token and all
    (security review of M14-04, S1). The frame may run only the two scripts
    the CSP hashes, `lookspage._SCRIPT` and the shell's own `page.js`; neither
    reads that URL by any of these names."""
    [(_, own_script)] = _tags(_shell(served).body.decode("utf-8")).scripts
    assert own_script == (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    for name, script in (("lookspage._SCRIPT", lookspage._SCRIPT), ("page.js", own_script)):
        for read in _BASE_URL_READS:
            assert read not in script, (name, read)


def test_every_route_response_keeps_frame_ancestors_none(served: Any, user_data: Path) -> None:
    _written()
    shell_csp = _shell(served).headers["Content-Security-Policy"]
    replies = [
        _route(served),
        _route(served, "&file=x"),
        _get(served, f"{ROUTE}/x?token={served.token}"),
        _get(served, f"{ROUTE}?token=wrong"),
    ]
    (_pages(user_data) / "looks.html").unlink()
    replies.append(_route(served))
    assert sorted(r.status for r in replies) == [200, 400, 403, 404, 404]
    for reply in replies:
        values = reply.headers.get_all("Content-Security-Policy")
        assert values == [shell_csp], reply.status
        assert _directives(values[0])["frame-ancestors"] == ["'none'"]
        cors = [k for k in reply.headers if k.lower().startswith("access-control-")]
        assert cors == []


# ─── the tab, under Node ─────────────────────────────────────────────────────

_VIEW = r"""
function looksView() {
  const frame = ids["looks-frame"];
  const where = ids["looks-where"];
  return {
    hidden: ids["looks-view"].hidden,
    tabSelected: ids["tab-looks"].getAttribute("aria-selected"),
    status: ids["looks-status"].textContent,
    statusError: has(ids["looks-status"], "error"),
    frameHidden: frame.hidden,
    srcdoc: typeof frame.srcdoc === "string" ? frame.srcdoc : null,
    frameSrc: frame.src === undefined ? null : frame.src,
    frameAttrs: frame.attrs,
    whereHidden: where.hidden,
    where: where.textContent,
    copies: where.all().filter((e) => e.tagName === "button" && has(e, "copy")).length,
    writeDisabled: ids["looks-write"].disabled,
  };
}
// The read route, as the scenario holds it; a run the scenario holds as
// not a clean exit, matched on the command and its parameters.
const plainFetch = ctx.fetch;
ctx.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  if (String(url).startsWith("/api/looks-page?")) {
    requests.push({ url: String(url), body: body });
    const held = scenario.looks_page;
    return answer(held.body, held.ok);
  }
  if (body && String(url).startsWith("/api/run?")) {
    for (const held of scenario.held || []) {
      if (canon(held.command) === canon(body.command) && canon(held.params) === canon(body.params || {})) {
        requests.push({ url: String(url), body: body });
        return answer(held.reply, true);
      }
    }
  }
  return plainFetch(url, opts);
};
"""

_STEPS = r"""
      } else if (step.looks_tab !== undefined) {
        const before = requests.length;
        ids["tab-looks"].fire("click"); await tick();
        out.steps.push(Object.assign(looksView(), { newRequests: requests.slice(before) }));
      } else if (step.looks_write !== undefined) {
        const before = requests.length;
        ids["looks-write"].fire("click"); await tick();
        out.steps.push(Object.assign(looksView(), { newRequests: requests.slice(before) }));
      } else if (step.looks_copy !== undefined) {
        const button = ids["looks-where"].all().find((e) => e.tagName === "button" && has(e, "copy"));
        if (!button) { out.steps.push({ missing: "looks copy" }); continue; }
        const before = requests.length;
        button.fire("click"); await tick();
        out.steps.push({ clipboard: clipboard.slice(), newRequests: requests.slice(before) });
"""

_STEP_ANCHOR = "      } else if (step.preview !== undefined) {\n"
_VIEW_ANCHOR = "vm.createContext(ctx);\n"


def _harness() -> str:
    assert _HARNESS.count(_STEP_ANCHOR) == 1 and _HARNESS.count(_VIEW_ANCHOR) == 1
    text = _HARNESS.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_STEP_ANCHOR, _STEPS.lstrip("\n") + _STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m14-04-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m14-04-scenario.json"
    data.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run(
        [_node(), str(harness), str(_TEMPLATES / "page.js"), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert "error" not in out, out["error"]
    assert not out["traps"], out["traps"]
    return out


def _scenario(served: Any, steps: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """The command list, `looks page --json` (the CLI's, given `--build`
    here; the page sends no parameter) and the page, as the route serves it."""
    stdout = ok("looks", "page", "--build", BUILD, "--json").stdout
    page = _route(served).json()["page"]
    scenario = {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": _get(served, f"/api/commands?token={served.token}").json(),
        "first_flavor": "",
        "trees": {},
        "explains": {},
        "runs": [{"command": ["looks", "page"], "params": {}, "stdout": stdout}],
        "held": [],
        "looks_page": {"ok": True, "body": {"page": page}},
        "steps": steps,
    }
    return scenario, page


def _asked(step: dict[str, Any]) -> list[str]:
    return [r["url"].partition("?")[0] for r in step["newRequests"]]


def test_the_looks_tab_starts_hidden_and_runs_nothing_until_asked(
    served: Any, tmp_path: Path
) -> None:
    body = _shell(served).body.decode("utf-8")
    tabs = re.findall(r'<button id="tab-([a-z]+)" class="tab"[^>]*role="tab"', body)
    assert tabs == ["explorer", "gaps", "commands", "character", "looks"]
    assert re.search(r'<section id="looks-view"[^>]*\bhidden\b', body)
    scenario, _ = _scenario(served, [{"looks_tab": True}])
    out = _drive(tmp_path / "node", scenario)
    [shown] = out["steps"]
    assert shown["hidden"] is False and shown["tabSelected"] == "true"
    assert shown["newRequests"] == [], "opening the tab writes nothing"
    assert shown["frameHidden"] is True and shown["srcdoc"] is None


def test_write_runs_looks_page_then_shows_the_route_text_in_the_frame(
    served: Any, user_data: Path, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [{"looks_tab": True}, {"looks_write": True}, {"looks_copy": 0}]
    scenario, page = _scenario(served, steps)
    report = json.loads(scenario["runs"][0]["stdout"])
    out = _drive(tmp_path / "node", scenario)
    _, written, copied = out["steps"]
    # One run, with no parameter (never `out`), then one read of the route.
    assert _asked(written) == ["/api/run", ROUTE]
    assert written["newRequests"][0]["body"] == {"command": ["looks", "page"], "params": {}}
    assert written["newRequests"][1]["body"] is None
    assert written["srcdoc"] == page and written["frameHidden"] is False
    assert written["frameSrc"] is None and written["frameAttrs"] == {}
    assert BUILD in written["status"] and not written["statusError"]
    assert f"{report['races']} race" in written["status"]
    # The path as the CLI printed it, with home as `~`, and a Copy button
    # that copies it exactly.
    assert written["whereHidden"] is False and written["copies"] == 1
    assert "looks.html" in written["where"]
    assert copied["clipboard"] == [report["path"]] and copied["newRequests"] == []
    assert written["writeDisabled"] is False
    # The page's text goes into the frame only.
    assert "DOCTYPE" not in written["status"] + written["where"]


def test_a_failed_run_shows_the_cli_text_and_reads_nothing_constructed(
    served: Any, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [{"looks_tab": True}, {"looks_write": True}]
    scenario, _ = _scenario(served, steps)
    stderr = "Error: no install found; pass --build <IMG src=x onerror=alert(1)>"
    scenario["held"] = [
        {
            "command": ["looks", "page"],
            "params": {},
            "reply": {
                "exit_code": 2,
                "timed_out": False,
                "truncated": False,
                "stdout": "",
                "stderr": stderr,
            },
        }
    ]
    scenario["runs"] = []
    out = _drive(tmp_path / "node", scenario)
    written = out["steps"][1]
    assert _asked(written) == ["/api/run"]
    assert written["status"] == stderr and written["statusError"] is True
    assert written["srcdoc"] is None and written["frameHidden"] is True
    assert written["whereHidden"] is True


def test_damaged_looks_still_show_the_page_with_the_cli_text_constructed(
    served: Any, tmp_path: Path
) -> None:
    """`looks page` exits 1 after writing when a look file is damaged; the
    page is shown and the CLI's words with it."""
    steps: list[dict[str, Any]] = [{"looks_write": True}]
    scenario, page = _scenario(served, steps)
    stdout = scenario["runs"][0]["stdout"]
    scenario["held"] = [
        {
            "command": ["looks", "page"],
            "params": {},
            "reply": {
                "exit_code": 1,
                "timed_out": False,
                "truncated": False,
                "stdout": stdout,
                "stderr": "m14-04 constructed: a damaged look file",
            },
        }
    ]
    out = _drive(tmp_path / "node", scenario)
    [written] = out["steps"]
    assert _asked(written) == ["/api/run", ROUTE]
    assert written["srcdoc"] == page and written["frameHidden"] is False
    assert "m14-04 constructed: a damaged look file" in written["status"]
    assert written["statusError"] is True


def test_a_refused_read_shows_the_servers_word_and_no_frame_constructed(
    served: Any, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [{"looks_write": True}]
    scenario, _ = _scenario(served, steps)
    scenario["looks_page"] = {"ok": False, "body": {"error": "not_a_page"}}
    out = _drive(tmp_path / "node", scenario)
    [written] = out["steps"]
    assert _asked(written) == ["/api/run", ROUTE]
    assert "not_a_page" in written["status"] and written["statusError"] is True
    assert "through a link" in written["status"], "a linked page says why"
    assert written["srcdoc"] is None and written["frameHidden"] is True
