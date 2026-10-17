"""The explorer view of `wowlab ui`, first stage (M13-25, §15.6, ADR-0029).

The page is served by the real server over the real CLI, and the explorer's
data comes from the runs it makes: `install show`, `tree --explain` and
`explain`, each with `--json`. The install is the captured tree
(`fixtures/macos/`) copied into `tmp_path` under a stand-in home folder, with
its flavor folder named as its provenance row says (`_classic_beta_`), and
`WOWLAB_WOW_ROOT` pointing at the copy. HOME and the user data folders point
into `tmp_path`. Nothing reads or writes a real install.

The hostile-name cases are `constructed` (L8, hostile input): no real capture
holds a file named `</script>`, `<img onerror=…>`, or one with U+2028 or
U+202E. Characters outside printable ASCII are spelled with `chr`.

What a browser does with the page is checked by reading the page: the CSP
hashes, no external URL, text written through `textContent` only, and no
file-map facts in the script (§14.5 D3). The owner's end-to-end check in a
real browser is reported in the PR.

Owner of this file: test-writer, since the audit of 2026-10-16 (M13-25 was
exempted from graders-first; the terms were a test-writer audit and
takeover). The implementer does not edit it; a grader that is wrong is
reported.

What the audit added:
- The page's behaviour, driven under Node with a stand-in DOM whose HTML
  sinks throw, fed the real CLI's JSON for the captured install (and for the
  constructed hostile one): the first view, a badge on every item, the
  filters, the detail pane, the flavor picker, `~` for home, hostile names
  drawn as inert text. Skipped when `node` is not on PATH. The stale-reply
  case after a flavor change is the code-reviewer's probe in `review/` and
  is not repeated here.
- The output cap counts the extra escapes (each `< > & U+2028 U+2029` costs
  six bytes), exactly at the boundary, on stdout and stderr; stand-in
  `wowlab` scripts, constructed.
- `/api/commands`: `home` empty when it would turn every path into `~`, and
  escaped like a run's output.
- The look the owner set: a light scheme, a serif system stack, badge
  colours that read like item quality, and WCAG AA contrast for the text.
  Badge words are never written here: a badge says `Verdict.label` (and
  `server_label`), whatever core makes them.
- Round 1 (2026-10-16): badges from `label` and `server_label`, a badge on
  every folder (`ancestors`, `flavor_folder_verdict`), the detail pane's
  file-map text from `plain` with no Markdown, and the whole
  `/api/commands` body escaped.
- Round 2 (2026-10-16): a symlinked addon (constructed) has the link label,
  `badge-link`, its own filter choice "link" (never under the gate label),
  and the link's words in the detail pane.
- M13-27T (2026-10-16), so this file grades the first stage's behaviour
  whichever stage the page is at; the second stage's graders are in
  `test_ui_explorer_stage2.py`. The stand-in DOM has exactly the ids
  page.html declares (null for any other id, as in a browser), a recorded
  clipboard, and `<details>` that fire `toggle`. It answers `tree
  --install`, `filemap gaps` and any run a scenario holds, and a click
  first opens the folders above its row. Rows are read through
  `_in_flavor`, so a page drawing the whole install (row paths from the
  install root) is graded on the same flavor-folder behaviour. The
  flavor-picker grader skips once the page reads the whole install. The
  static checks also allow `.kind === "folder"` on a gap group, and
  `filemap gaps` among the explorer's literal runs.
- Round 1 on #226 (2026-10-16): the harness has timers on a virtual clock
  (a string given to a timer is trapped), and traps navigation (`location`,
  `window.open`), attributes outside the closed set, and link, form, frame,
  script and media elements. The static checks ban `location.replace`,
  `.action`, `.submit(`, URL and handler attributes in either quote style,
  `javascript:`, a hint's `argv` and the CLI's snapshot wording; the CSP
  check pins `form-action` and `base-uri`. Timers given a function are no
  longer banned statically (the CSP forbids string evaluation, and the
  harness traps it).
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import typer

from wowlab_core import filemap, ui

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SV_FILE = "WTF/Account/90000001#6/SavedVariables/RareScanner.lua"

LSEP = chr(0x2028)  # LINE SEPARATOR
RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
SCRIPT_CLOSE = "</script>"
IMG = "<img src=x onerror=alert(1)>"
HOSTILE_SV = [
    # `</script>` holds a slash, so it spans a folder `<` and a file `script>.lua`.
    "<",
    IMG + ".lua",
    "line" + LSEP + "sep.lua",
    RLO + "aul.exe.lua",
]
HOSTILE_ADDONS = [IMG, "line" + LSEP + "sep", RLO + "aul.exe"]

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"


# ─── serving ─────────────────────────────────────────────────────────────────


def _copy_install(root: Path) -> Path:
    root.mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    return root


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
    root = _copy_install(home / "Games" / "World of Warcraft")
    monkeypatch.setenv("WOWLAB_WOW_ROOT", str(root))
    return root


@pytest.fixture
def hostile(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if os.name == "nt":
        pytest.skip("Windows forbids < and > in file names")
    root = _copy_install(home / "hostile_constructed")
    flavor = root / FLAVOR
    saved = flavor / "WTF" / "Account" / "90000001#6" / "SavedVariables"
    for name in HOSTILE_SV:
        if name == "<":
            (saved / "<").mkdir()
            (saved / "<" / "script>.lua").write_text("X = 1\n", encoding="utf-8")
        else:
            (saved / name).write_text("X = 1\n", encoding="utf-8")
    for name in HOSTILE_ADDONS:
        folder = flavor / "Interface" / "AddOns" / name
        folder.mkdir()
        (folder / (name + ".toc")).write_text("## Interface: 1\n", encoding="utf-8")
    monkeypatch.setenv("WOWLAB_WOW_ROOT", str(root))
    return root


@pytest.fixture
def server(home: Path) -> Iterator[Any]:
    served = ui.make_server()
    thread = threading.Thread(target=served.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.shutdown()
        served.server_close()
        thread.join(10)


class _Reply:
    def __init__(self, status: int, headers: http.client.HTTPMessage, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _send(server: Any, method: str, path: str, body: bytes | None = None) -> _Reply:
    port = int(server.port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        headers = {"Host": f"127.0.0.1:{port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, f"{path}?token={server.token}", body=body, headers=headers)
        response = connection.getresponse()
        return _Reply(response.status, response.headers, response.read())
    finally:
        connection.close()


def _run(server: Any, command: list[str], params: dict[str, Any]) -> _Reply:
    body = json.dumps({"command": command, "params": params}).encode("utf-8")
    return _send(server, "POST", "/api/run", body)


def _run_json(server: Any, command: list[str], params: dict[str, Any]) -> Any:
    reply = _run(server, command, params)
    assert reply.status == 200, reply.body[:300]
    result = reply.json()
    assert result["exit_code"] == 0, result["stderr"]
    assert not result["timed_out"] and not result["truncated"]
    return json.loads(result["stdout"])


def _page(server: Any) -> _Reply:
    reply = _send(server, "GET", "/")
    assert reply.status == 200
    return reply


def _script() -> str:
    return (_TEMPLATES / "page.js").read_text(encoding="utf-8")


def _inline(body: str, tag: str) -> list[str]:
    return re.findall(rf"<{tag}>(.*?)</{tag}>", body, flags=re.DOTALL)


def _sha(text: str) -> str:
    return (
        "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"
    )


def _directives(csp: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out[words[0].lower()] = words[1:]
    return out


# ─── the page: CSP, no external URL, text only ───────────────────────────────


def test_the_explorer_page_is_served_with_a_csp_that_hashes_it(server: Any) -> None:
    reply = _page(server)
    assert reply.headers["Content-Type"].startswith("text/html")
    body = reply.body.decode("utf-8")
    assert 'id="explorer-view"' in body and 'id="tree"' in body and 'id="explain"' in body
    scripts = _inline(body, "script")
    styles = _inline(body, "style")
    assert len(scripts) == 1 and len(styles) == 1
    assert body.count("<script") == 1 and body.count("<style") == 1
    directives = _directives(reply.headers["Content-Security-Policy"])
    assert directives["default-src"] == ["'none'"]
    assert directives["script-src"] == [_sha(scripts[0])]
    assert directives["style-src"] == [_sha(styles[0])]
    assert directives["connect-src"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert directives["form-action"] == ["'none'"]  # round 1 on #226
    assert directives["base-uri"] == ["'none'"]
    assert "font-src" not in directives
    flat = " ".join(" ".join(v) for v in directives.values())
    assert "unsafe" not in flat and "data:" not in flat and "http" not in flat
    # The page is the explorer first: its view is not hidden, the commands are.
    assert re.search(r'<section id="explorer-view"[^>]*\bhidden\b', body) is None
    assert re.search(r'<section id="commands-view"[^>]*\bhidden\b', body) is not None


_EXTERNAL = [
    "://",
    "url(",
    "@import",
    "@font-face",
    "<link",
    "<img",
    "<iframe",
    "<object",
    "<embed",
    "src=",
    "href=",
    "srcset",
    "xmlns",
    "xlink",
    "//cdn",
    "fonts.",
    "data:image",
    "data:font",
    "data:text",
    "data:application",
]


def test_no_external_url_appears_in_the_page(server: Any) -> None:
    body = _page(server).body.decode("utf-8").lower()
    for marker in _EXTERNAL:
        assert marker not in body, marker
    # Every request the script makes goes to the server's own API, by path.
    script = _script()
    assert script.count("fetch(") == script.count('fetch(api("/api/')
    assert set(re.findall(r'fetch\(api\("([^"]+)"\)', script)) == {"/api/commands", "/api/run"}
    for sink in (
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "import(",
        "new Image",
    ):
        assert sink not in script, sink


_HTML_SINKS = [
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "createContextualFragment",
    "DOMParser",
    "srcdoc",
    "eval(",
    "Function(",
    # A timer given a string evaluates it; one given a function is ordinary
    # browser code, and the Node harness provides timers (round 1 on #226;
    # the harness also traps a string at run time).
    'setTimeout("',
    "setTimeout('",
    "setTimeout(`",
    'setInterval("',
    "setInterval('",
    "setInterval(`",
    ".src",
    ".href",
    "location.assign",
    "location.replace",
    "location.reload",
    "location =",
    "location=",
    "window.open",
    ".action",
    ".submit(",
    "javascript:",
    'setAttribute("on',
    "setAttribute('on",
    'setAttribute("style',
    "setAttribute('style",
    'setAttribute("href',
    "setAttribute('href",
    'setAttribute("src',
    "setAttribute('src",
    ".style.",
]


def test_the_script_writes_text_only(server: Any) -> None:
    script = _script()
    for sink in _HTML_SINKS:
        assert sink not in script, sink
    # Attributes the script sets are a closed set, none of them a URL or a
    # handler, each named by a literal in either quote style (round 1 on #226).
    assert script.count("setAttribute(") == len(re.findall(r"setAttribute\([\"']", script))
    names = set(re.findall(r"setAttribute\([\"']([^\"']+)[\"']", script))
    assert names <= {
        "aria-pressed",
        "aria-selected",
        "aria-expanded",
        "aria-level",
        "aria-label",
        "role",
    }


def test_the_script_holds_no_file_map_logic() -> None:
    """§14.5 D3: no file-map row, Edit cell or verdict wording in the script;
    the level of a badge and its words come from the CLI's JSON."""
    script = _script()
    loaded = filemap.load()
    for entry in loaded.entries:
        assert f'"{entry.id}"' not in script, entry.id
        assert entry.what not in script, entry.id
        if len(entry.written_by) >= 12:
            assert entry.written_by not in script, entry.id
        for pattern in entry.match:
            if "*" in pattern:
                assert pattern not in script, pattern
    # `kind` is a tree item's or a gap group's file-or-folder field, not a
    # verdict level (widened for M13-27's gaps tab, 2026-10-16).
    levels_free = re.sub(r'\.kind\s*===\s*"folder"', "", script)
    for level in ("gate", "no", "folder", "not_applicable", "unclassified"):
        assert f'"{level}"' not in levels_free, level
        assert f"'{level}'" not in script, level
    for words in ("write gate", "server may replace", "Blizzard", "snapshot restore"):
        assert words not in script, words
    # A hint is copied as `command`, quoted by Python for its shell; the page
    # never rebuilds one from `argv` (round 1 on #226).
    assert ".argv" not in script and '["argv"]' not in script
    # Snapshot facts are worded by Python (`snapshot.words`, conductor ruling
    # of 2026-10-16); the CLI's phrases are not repeated here.
    for words in ("could not be confirmed", "the file on disk", "not held"):
        assert words not in script, words


def test_the_explorer_runs_only_listed_read_commands(server: Any) -> None:
    listed = _send(server, "GET", "/api/commands").json()
    offered = {tuple(c["path"]): c for c in listed["commands"]}
    used = {
        tuple(json.loads("[" + inside + "]"))
        for inside in re.findall(r"runJson\(\[([^\]]*)\]", _script())
    }
    # M13-25 runs the first three; M13-27 adds `filemap gaps` and may drop
    # `install show`. A Preview runs a view's command, which is data, not a
    # literal here; its own graders are in test_ui_explorer_stage2.py.
    assert {("tree",), ("explain",)} <= used, used
    assert used <= {("install", "show"), ("tree",), ("explain",), ("filemap", "gaps")}, used
    for path in used:
        assert path in offered, path
        assert offered[path]["excluded"] is None, path


def test_the_command_list_carries_the_home_folder_for_tilde(server: Any, home: Path) -> None:
    listed = _send(server, "GET", "/api/commands").json()
    assert listed["home"] == str(Path.home()) == str(home)
    assert listed["sep"] == os.sep


# ─── the data the explorer draws ─────────────────────────────────────────────


def test_the_tree_and_details_the_explorer_draws(server: Any, install: Path, home: Path) -> None:
    shown = _run_json(server, ["install", "show"], {})
    assert [f["folder"] for f in shown["flavors"]] == [FLAVOR]

    tree = _run_json(server, ["tree"], {"explain": True, "flavor": FLAVOR})
    assert tree["flavor_folder"] == FLAVOR
    assert tree["install_root"].startswith(str(home) + os.sep)  # shown as "~"
    paths = {item["path"]: item for item in tree["items"]}
    # The first view's two open folders hold listed paths.
    assert any(p.startswith("WTF/") for p in paths)
    assert any(p.startswith("Interface/AddOns/") for p in paths)
    for item in tree["items"]:
        verdict = item["verdict"]
        assert verdict["level"] in ("gate", "no", "folder", "not_applicable", "unclassified")
        assert verdict["words"]
    assert any(i["verdict"]["level"] == "no" for i in tree["items"])  # "filter to no"

    # Detail, asked the way the page asks: the absolute path, root and flavor.
    for rel, entry_id in (("WTF/Config.wtf", "config-wtf"), (SV_FILE, "account-savedvariables")):
        assert rel in paths
        target = tree["install_root"] + os.sep + FLAVOR + "/" + rel
        report = _run_json(
            server, ["explain"], {"path": target, "root": tree["install_root"], "flavor": FLAVOR}
        )
        result = report["result"]
        assert result["status"] == "classified"
        assert result["path"] == rel
        assert result["entry"]["id"] == entry_id
        assert result["entry"]["what"] and result["entry"]["written_by"]
        assert report["verdict"] == paths[rel]["verdict"]


def test_a_request_without_the_token_is_refused(server: Any) -> None:
    port = int(server.port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        assert response.status == 403
        assert json.loads(response.read()) == {"error": "token"}
    finally:
        connection.close()


# ─── hostile names (constructed) ─────────────────────────────────────────────


def test_hostile_names_pass_through_escaped_constructed(server: Any, hostile: Path) -> None:
    reply = _run(server, ["tree"], {"explain": True, "flavor": FLAVOR})
    assert reply.status == 200
    assert reply.headers["Content-Type"].startswith("application/json")
    assert reply.headers["X-Content-Type-Options"] == "nosniff"
    # Escaped in the body: no markup, no raw line separator or override.
    for raw in ("<", ">", "&", LSEP, RLO):
        assert raw.encode("utf-8") not in reply.body, repr(raw)
    result = reply.json()
    assert result["exit_code"] == 0, result["stderr"]
    paths = {item["path"]: item for item in json.loads(result["stdout"])["items"]}
    saved = "WTF/Account/90000001#6/SavedVariables/"
    expected = [saved + SCRIPT_CLOSE + ".lua"]
    expected += [saved + name for name in HOSTILE_SV if name != "<"]
    expected += ["Interface/AddOns/" + name for name in HOSTILE_ADDONS]
    for path in expected:
        assert path in paths, repr(path)
        assert paths[path]["verdict"]["words"]
    # The page never carries data: names reach it only as JSON read by the script.
    page = _page(server).body.decode("utf-8")
    for name in (SCRIPT_CLOSE + ".lua", IMG, LSEP, RLO):
        assert name not in page, repr(name)


def test_a_hostile_name_explains_constructed(server: Any, hostile: Path) -> None:
    root = str(hostile)
    rel = "WTF/Account/90000001#6/SavedVariables/" + SCRIPT_CLOSE + ".lua"
    reply = _run(
        server,
        ["explain"],
        {"path": root + os.sep + FLAVOR + "/" + rel, "root": root, "flavor": FLAVOR},
    )
    assert reply.status == 200
    assert b"</script" not in reply.body.lower()
    report = json.loads(reply.json()["stdout"])
    assert report["verdict"]["level"] == "unclassified"
    assert report["result"]["path"] == rel


def test_the_page_shows_control_characters_visibly() -> None:
    """U+202E and U+2028 in a name are shown as a code, not applied, and
    names are isolated from the text around them."""
    script = _script()
    assert r"\p{Cc}\p{Cf}\p{Zl}\p{Zp}" in script
    css = (_TEMPLATES / "page.css").read_text(encoding="utf-8")
    assert css.count("unicode-bidi: isolate") >= 3


def test_the_json_escape_keeps_the_text_constructed() -> None:
    """The extra escapes change bytes, never the decoded text."""
    text = SCRIPT_CLOSE + IMG + "&" + LSEP + chr(0x2029) + RLO + '"\\'
    encoded = ui._json_text(text)
    assert json.loads(b'"' + encoded + b'"') == text
    assert b"<" not in encoded and b">" not in encoded and b"&" not in encoded


# ═══ audit, 2026-10-16 (test-writer) ═════════════════════════════════════════
#
# ─── the page's behaviour, under Node ────────────────────────────────────────
#
# page.js runs in a `vm` context with a stand-in DOM. Writing `innerHTML`,
# `outerHTML` or `insertAdjacentHTML` throws, so a page that draws through
# an HTML sink fails here rather than drawing. `fetch` answers from what the
# real CLI printed for the install under test, collected through the real
# server first; every request is logged. The harness then performs the
# scenario's steps and prints what the panes hold.

_HARNESS = r"""
"use strict";
const fs = require("fs");
const vm = require("vm");
const scenario = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const has = (e, c) => typeof e.className === "string" && e.className.split(" ").includes(c);
// What the page may do and a browser would turn into a request or a
// navigation is trapped: recorded, then thrown (round 1 on #226).
const traps = [];
function trap(what) { traps.push(String(what)); throw new Error("harness trap: " + what); }
const ALLOWED_ATTRS = scenario.allowed_attrs || ["aria-pressed", "aria-selected", "aria-expanded",
  "aria-level", "aria-label", "role"];
const FORBIDDEN_TAGS = ["a", "area", "form", "iframe", "frame", "img", "image", "script", "link", "base",
  "meta", "object", "embed", "video", "audio", "source", "track", "svg", "use", "style"];
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.parentElement = null; this._text = "";
    this.attrs = {}; this.dataset = {}; this.listeners = {}; this.className = "";
    this.hidden = false; this.disabled = false; this.value = ""; this.title = "";
    this.checked = false; this.type = ""; this.id = ""; this._open = false;
    const self = this;
    this.classList = {
      add(...cs) { const s = new Set(self.className.split(" ").filter(Boolean)); for (const c of cs) s.add(c); self.className = [...s].join(" "); },
      remove(...cs) { self.className = self.className.split(" ").filter((x) => x && !cs.includes(x)).join(" "); },
      contains(c) { return has(self, c); },
      toggle(c, on) { if (on === undefined) on = !has(self, c); if (on) this.add(c); else this.remove(c); return on; },
    };
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(t) { this._text = String(t); this.children = []; }
  get innerText() { return this.textContent; }
  set innerText(t) { this.textContent = t; }
  set innerHTML(_) { throw new Error("HTML sink: innerHTML"); }
  set outerHTML(_) { throw new Error("HTML sink: outerHTML"); }
  insertAdjacentHTML() { throw new Error("HTML sink: insertAdjacentHTML"); }
  get open() { return this._open; }
  set open(v) { const was = this._open; this._open = Boolean(v); if (was !== this._open) this.fire("toggle"); }
  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  _adopt(c) {
    if (typeof c === "string") return [Object.assign(new El("#text"), { _text: c })];
    if (c.tagName === "#fragment") { const kids = c.children; c.children = []; return kids; }
    if (c.parentElement) c.parentElement.children = c.parentElement.children.filter((x) => x !== c);
    return [c];
  }
  appendChild(c) { for (const k of this._adopt(c)) { k.parentElement = this; this.children.push(k); } return c; }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  prepend(...cs) { const kids = []; for (const c of cs) kids.push(...this._adopt(c)); for (const k of kids) k.parentElement = this; this.children.unshift(...kids); }
  insertBefore(c, ref) { const kids = this._adopt(c); for (const k of kids) k.parentElement = this; const at = ref ? this.children.indexOf(ref) : -1; if (at < 0) this.children.push(...kids); else this.children.splice(at, 0, ...kids); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentElement = null; return c; }
  replaceChildren(...cs) { this.children = []; this._text = ""; for (const c of cs) this.appendChild(c); }
  replaceWith(...cs) { const p = this.parentElement; if (!p) return; const at = p.children.indexOf(this); const kids = []; for (const c of cs) kids.push(...this._adopt(c)); for (const k of kids) k.parentElement = p; p.children.splice(at, 1, ...kids); }
  setAttribute(k, v) { k = String(k); if (!ALLOWED_ATTRS.includes(k)) trap("setAttribute " + k); this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  toggleAttribute(k, on) { if (on === undefined) on = !(k in this.attrs); if (on) this.attrs[k] = ""; else delete this.attrs[k]; return on; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  removeEventListener(ev, fn) { this.listeners[ev] = (this.listeners[ev] || []).filter((f) => f !== fn); }
  fire(ev) { const e = { type: ev, target: this, currentTarget: this, preventDefault() {}, stopPropagation() {} }; for (const fn of this.listeners[ev] || []) fn(e); }
  dispatchEvent(e) { for (const fn of this.listeners[e.type] || []) fn(Object.assign(e, { target: this, currentTarget: this })); return true; }
  click() { this.fire("click"); }
  focus() {}
  blur() {}
  scrollIntoView() {}
  contains(o) { return o === this || this.all().includes(o); }
  closest(sel) { let e = this; while (e) { if (matchSel(e, sel)) return e; e = e.parentElement; } return null; }
  all() { const o = []; for (const c of this.children) { o.push(c, ...c.all()); } return o; }
  querySelectorAll(sel) { return this.all().filter((e) => matchSel(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  get options() { return this.children.filter((c) => c.tagName === "option"); }
  remove(i) {
    if (i === undefined) { if (this.parentElement) this.parentElement.removeChild(this); return; }
    const o = this.options[i]; this.children.splice(this.children.indexOf(o), 1);
  }
}
function matchSel(e, sel) {
  sel = sel.trim();
  if (sel.startsWith("#")) return e.id === sel.slice(1);
  if (sel.startsWith(".")) return sel.slice(1).split(".").every((c) => has(e, c));
  const dot = sel.indexOf(".");
  if (dot > 0) return e.tagName === sel.slice(0, dot) && sel.slice(dot + 1).split(".").every((c) => has(e, c));
  return e.tagName === sel;
}
// The page's own elements: the ids in page.html, nothing else. An id the
// page asks for that page.html does not have is null, as in a browser.
const ids = {};
for (const spec of scenario.ids) {
  const el = new El(spec.tag); el.id = spec.id; el.hidden = spec.hidden; ids[spec.id] = el;
}
if (ids["level-filter"]) { const all = new El("option"); all.value = ""; all.textContent = "All"; ids["level-filter"].appendChild(all); }
const roots = () => Object.values(ids);
const everywhere = () => { const o = []; for (const r of roots()) o.push(r, ...r.all()); return o; };
const requests = [];
const clipboard = [];
const answer = (obj, ok) => ({ ok: ok !== false, status: ok === false ? 403 : 200, json: async () => obj });
const norm = (p) => String(p).split(scenario.sep).join("/");
function canon(v) {
  if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
  if (v && typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
  return JSON.stringify(v);
}
const fetchStub = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  requests.push({ url: String(url), body: body });
  if (String(url).startsWith("/api/commands?")) return answer(scenario.commands);
  if (!String(url).startsWith("/api/run?")) return answer({ error: "unknown" }, false);
  const key = body.command.join(" ");
  const params = body.params || {};
  let stdout = null;
  for (const run of scenario.runs || []) {
    if (canon(run.command) === canon(body.command) && canon(run.params) === canon(params)) stdout = run.stdout;
  }
  if (stdout !== null) { /* an exact run the scenario holds */ }
  else if (key === "install show") stdout = scenario.install_show;
  else if (key === "tree" && params.whole_install === true) stdout = scenario.install_tree;
  else if (key === "tree") stdout = scenario.trees[params.flavor || scenario.first_flavor];
  else if (key === "filemap gaps") stdout = scenario.gaps;
  else if (key === "explain") stdout = scenario.explains[norm(params.path)];
  if (typeof stdout !== "string") {
    return answer({ exit_code: 2, timed_out: false, truncated: false, stdout: "", stderr: "harness: no reply for " + key });
  }
  return answer({ exit_code: 0, timed_out: false, truncated: false, stdout: stdout, stderr: "" });
};
const navigator = { clipboard: { writeText: async (t) => { clipboard.push(String(t)); } } };
const documentStub = {
  getElementById: (id) => (id in ids ? ids[id] : null),
  createElement: (t) => { const tag = String(t).toLowerCase(); if (FORBIDDEN_TAGS.includes(tag)) trap("createElement " + tag); return new El(tag); },
  createElementNS: (_, t) => trap("createElementNS " + t),
  createTextNode: (t) => Object.assign(new El("#text"), { _text: String(t) }),
  createDocumentFragment: () => new El("#fragment"),
  querySelectorAll: (sel) => everywhere().filter((e) => matchSel(e, sel)),
  querySelector: (sel) => everywhere().find((e) => matchSel(e, sel)) || null,
  addEventListener() {},
};
// Timers on a virtual clock: a tick runs every pending timer in time order
// (an interval at most a bounded number of times), with no wall-clock wait.
// A timer given a string instead of a function is trapped (it would eval).
const timers = [];
let timerSeq = 0;
let clock = 0;
function addTimer(fn, ms, every) {
  if (typeof fn !== "function") trap("a timer given a string");
  const id = ++timerSeq;
  const wait = Math.max(0, Number(ms) || 0);
  timers.push({ id: id, fn: fn, at: clock + wait, every: every ? Math.max(1, wait) : 0, seq: id });
  return id;
}
function clearTimer(id) { const at = timers.findIndex((t) => t.id === id); if (at >= 0) timers.splice(at, 1); }
const location = Object.freeze({
  get search() { return "?token=t"; },
  get href() { return "http://127.0.0.1:1/?token=t"; },
  set href(v) { trap("location.href = " + v); },
  get hash() { return ""; },
  set hash(v) { trap("location.hash = " + v); },
  assign(u) { trap("location.assign " + u); },
  replace(u) { trap("location.replace " + u); },
  reload() { trap("location.reload"); },
});
const win = { navigator: navigator, isSecureContext: true, open: (u) => trap("window.open " + u) };
Object.defineProperty(win, "location", { get: () => location, set: (v) => trap("window.location = " + v) });
const ctx = { window: win, document: documentStub, navigator: navigator,
  URLSearchParams, fetch: fetchStub, console, Set, Map, Promise, JSON, Error,
  setTimeout: (fn, ms) => addTimer(fn, ms, false), clearTimeout: clearTimer,
  setInterval: (fn, ms) => addTimer(fn, ms, true), clearInterval: clearTimer,
  requestAnimationFrame: (fn) => addTimer(() => fn(clock), 16, false), cancelAnimationFrame: clearTimer,
  queueMicrotask: (fn) => queueMicrotask(fn) };
Object.defineProperty(ctx, "location", { get: () => location, set: (v) => trap("location = " + v), enumerable: true });
win.window = win;
vm.createContext(ctx);
const tick = async () => {
  for (let round = 0; round < 50; round++) {
    for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
    if (!timers.length) return;
    timers.sort((a, b) => a.at - b.at || a.seq - b.seq);
    const due = timers.shift();
    clock = Math.max(clock, due.at);
    if (due.every) { due.at = clock + due.every; due.seq = ++timerSeq; timers.push(due); }
    due.fn();
  }
};
const rowOf = (li) => li.children.find((c) => has(c, "row"));
const treeEl = () => ids.tree;
function rows() {
  return treeEl().all().filter((e) => e.attrs.role === "treeitem").map((li) => {
    const row = rowOf(li) || li;
    const name = row.children.find((c) => has(c, "name"));
    const marks = row.children.filter((c) => has(c, "badge"))
      .map((b) => ({ text: b.textContent, title: b.title, cls: b.className }));
    return { path: row.dataset.path, name: name ? name.textContent : null,
      nameTitle: name ? name.title : null, expanded: li.getAttribute("aria-expanded"),
      text: row.textContent, badge: marks.length ? marks[0] : null, badges: marks };
  });
}
// A row by its data-path; for a page that draws the whole install, a path
// relative to the first flavor folder finds the same row under it.
function liOf(path) {
  const find = (p) => treeEl().all().find((e) => e.attrs.role === "treeitem" && rowOf(e) && rowOf(e).dataset.path === p);
  return find(path) || find(path === "" ? scenario.first_flavor : scenario.first_flavor + "/" + path);
}
function snap() {
  const pick = (id, f) => (ids[id] ? f(ids[id]) : null);
  return { rows: rows(), treeRoot: pick("tree-root", (e) => e.textContent), status: pick("explorer-status", (e) => e.textContent),
    flavorBoxHidden: pick("flavor-box", (e) => e.hidden), explorerHidden: pick("explorer-view", (e) => e.hidden),
    levels: ids["level-filter"].options.map((o) => o.value),
    levelTexts: ids["level-filter"].options.map((o) => o.textContent) };
}
// The detail pane: every element whose id starts with "explain", with what
// the page drew inside it.
function paneEls() { return roots().filter((e) => e.id.startsWith("explain")); }
function inPane() { const o = []; for (const r of paneEls()) o.push(r, ...r.all()); return o; }
function detail() {
  const badge = ids["explain-verdict"].all().find((e) => has(e, "badge"));
  const own = (id) => ids[id].textContent;
  const all = inPane();
  return { title: own("explain-title"), path: own("explain-path"),
    verdict: own("explain-verdict"), verdictBadge: badge ? badge.className : "",
    facts: own("explain-facts"), status: own("explain-status"),
    pane: paneEls().map((e) => e.textContent).join("\n"),
    rest: paneEls().filter((e) => e.id !== "explain-title" && e.id !== "explain-path")
      .map((e) => e.textContent).join("\n"),
    hints: all.filter((e) => has(e, "hint")).map((h) => ({ text: h.textContent,
      copies: h.all().filter((b) => b.tagName === "button" && has(b, "copy")).length })),
    copyButtons: all.filter((e) => e.tagName === "button" && has(e, "copy")).length,
    previewButtons: all.filter((e) => e.tagName === "button" && has(e, "preview")).length,
    details: all.filter((e) => e.tagName === "details").length,
    fields: all.filter((e) => ["input", "textarea", "select"].includes(e.tagName)).length,
    buttons: all.filter((e) => e.tagName === "button").map((e) => e.className),
    listening: all.filter((e) => e.tagName !== "button" && Object.keys(e.listeners).length > 0)
      .map((e) => e.tagName + "." + e.className) };
}
// A group's own text: everything but its places, not-entered folders and links.
function bareText(el) {
  if (["gap-place", "gap-not-entered", "gap-link"].some((c) => has(el, c))) return "";
  return el._text + el.children.map(bareText).join("") + (el.title ? "\n" + el.title : "");
}
function gapsView() {
  const view = ids["gaps-view"];
  const all = everywhere();
  const path = (e) => (e.dataset && typeof e.dataset.path === "string" ? e.dataset.path : null);
  return {
    hidden: view ? view.hidden : null,
    explorerHidden: ids["explorer-view"] ? ids["explorer-view"].hidden : null,
    text: roots().filter((e) => e.id.startsWith("gaps")).map((e) => e.textContent).join("\n"),
    groups: all.filter((e) => has(e, "gap-group")).map((g) => ({
      catchAll: has(g, "gap-catch-all"),
      heads: g.all().filter((e) => has(e, "gap-head")).map((e) => e.textContent),
      places: g.all().filter((e) => has(e, "gap-place")).map(path),
      bare: bareText(g),
      summaries: g.all().filter((e) => e.tagName === "summary")
        .map((e) => has(e, "gap-head") || e.all().some((c) => has(c, "gap-head"))),
      text: g.textContent })),
    places: all.filter((e) => has(e, "gap-place")).map(path),
    notEntered: all.filter((e) => has(e, "gap-not-entered")).map(path),
    links: all.filter((e) => has(e, "gap-link")).map(path),
  };
}
async function openAll() {
  for (let n = 0; n < 500; n++) {
    const closed = treeEl().all().find((e) => e.attrs.role === "treeitem" && e.getAttribute("aria-expanded") === "false");
    if (!closed) break;
    const row = rowOf(closed);
    const twisty = row.children.find((c) => c.tagName === "button" && !has(c, "name"));
    twisty.fire("click");
    await tick();
  }
}
(async () => {
  const out = { steps: [] };
  try {
    vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
    await tick();
    out.first = snap();
    for (const step of scenario.steps) {
      if (step.open_all) {
        await openAll();
        out.steps.push(snap());
      } else if (step.click !== undefined) {
        // A row not drawn yet is reached the way a player would: by opening
        // the folders above it.
        if (!liOf(step.click)) await openAll();
        const li = liOf(step.click);
        if (!li) { out.steps.push({ missing: step.click }); continue; }
        rowOf(li).children.find((c) => has(c, "name")).fire("click");
        await tick();
        out.steps.push(detail());
      } else if (step.level !== undefined) {
        ids["level-filter"].value = step.level; ids["level-filter"].fire("change"); await tick();
        out.steps.push(snap());
      } else if (step.text !== undefined) {
        ids["text-filter"].value = step.text; ids["text-filter"].fire("input"); await tick();
        out.steps.push(snap());
      } else if (step.flavor !== undefined) {
        ids.flavor.value = step.flavor; ids.flavor.fire("change"); await tick();
        out.steps.push(snap());
      } else if (step.gaps_only !== undefined) {
        const box = ids["gaps-only"];
        if (!box) { out.steps.push({ missing: "#gaps-only" }); continue; }
        box.checked = step.gaps_only; box.fire("change"); box.fire("input"); await tick();
        out.steps.push(snap());
      } else if (step.tab !== undefined) {
        const tab = ids["tab-" + step.tab];
        if (!tab) { out.steps.push({ missing: "#tab-" + step.tab }); continue; }
        tab.fire("click"); await tick();
        for (let n = 0; n < 500; n++) {
          const shut = everywhere().find((e) => e.tagName === "details" && !e.open);
          if (!shut) break;
          shut.open = true; await tick();
        }
        out.steps.push(gapsView());
      } else if (step.copy !== undefined) {
        const buttons = inPane().filter((e) => e.tagName === "button" && has(e, "copy"));
        if (!buttons[step.copy]) { out.steps.push({ missing: "copy " + step.copy }); continue; }
        const before = requests.length;
        buttons[step.copy].fire("click"); await tick();
        out.steps.push({ clipboard: clipboard.slice(), newRequests: requests.length - before });
      } else if (step.activate !== undefined) {
        // Every control in the detail pane, used: each listener fired, each
        // <details> opened, until nothing new appears.
        const before = requests.length;
        const done = new Set();
        for (let round = 0; round < 10; round++) {
          const fresh = inPane().filter((e) => !done.has(e));
          if (!fresh.length) break;
          for (const e of fresh) {
            done.add(e);
            if (e.tagName === "details") e.open = true;
            for (const ev of Object.keys(e.listeners)) e.fire(ev);
            await tick();
          }
        }
        out.steps.push(Object.assign(detail(), { newRequests: requests.slice(before) }));
      } else if (step.preview !== undefined) {
        const button = inPane().find((e) => e.tagName === "button" && has(e, "preview"));
        if (!button) { out.steps.push({ missing: "preview" }); continue; }
        const before = requests.length;
        button.fire("click"); await tick();
        for (let n = 0; n < 500; n++) {
          const shut = inPane().find((e) => e.tagName === "details" && !e.open);
          if (!shut) break;
          shut.open = true; await tick();
        }
        out.steps.push(Object.assign(detail(), { newRequests: requests.slice(before) }));
      }
    }
  } catch (error) {
    out.error = String((error && error.stack) || error);
  }
  out.requests = requests;
  out.clipboard = clipboard;
  out.traps = traps;
  process.stdout.write(JSON.stringify(out));
})();
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH: the page's behaviour is graded under Node")
    return node


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m13-25-harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    data = folder / "m13-25-scenario.json"
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


def _stdout(server: Any, command: list[str], params: dict[str, Any]) -> str:
    reply = _run(server, command, params)
    assert reply.status == 200, reply.body[:300]
    result = reply.json()
    assert result["exit_code"] == 0, result["stderr"]
    text: str = result["stdout"]
    return text


def _norm(path: str) -> str:
    """A path the page asks `explain` about, with `/` for the separator: the
    page may join with `sep` or with `/`."""
    return path.replace(os.sep, "/")


def _page_ids() -> list[dict[str, Any]]:
    """The elements page.html gives an id: the only ones the stand-in DOM
    has, so a page that asks for an id page.html lacks gets null."""
    html = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    out = []
    for tag, attrs in re.findall(r"<([a-z][a-z0-9]*)\b([^>]*)>", html):
        found = re.search(r'\bid="([^"]+)"', attrs)
        if found:
            hidden = re.search(r"(?:^|\s)hidden(?:\s|=|$)", attrs) is not None
            out.append({"id": found.group(1), "tag": tag, "hidden": hidden})
    return out


def _scenario(
    server: Any, explain: list[str], steps: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """What the real CLI prints for the install under test, in the shape the
    harness serves: the command list, `install show`, a tree per flavor
    folder, the whole install's tree (`tree --install`), `filemap gaps`, and
    an explain for each path in `explain` (relative to the first flavor
    folder), keyed by the absolute path the page asks for. Returns the
    scenario and the parsed first tree."""
    listed = _send(server, "GET", "/api/commands").json()
    shown_text = _stdout(server, ["install", "show"], {})
    flavors = [f["folder"] for f in json.loads(shown_text)["flavors"]]
    trees = {f: _stdout(server, ["tree"], {"explain": True, "flavor": f}) for f in flavors}
    first = json.loads(trees[flavors[0]])
    base = first["install_root"] + os.sep + first["flavor_folder"]
    explains = {}
    for rel in explain:
        target = base + "/" + rel
        params = {"path": target, "root": first["install_root"], "flavor": first["flavor_folder"]}
        explains[_norm(target)] = _stdout(server, ["explain"], params)
    scenario = {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": listed,
        "install_show": shown_text,
        "first_flavor": flavors[0],
        "trees": trees,
        "install_tree": _stdout(server, ["tree"], {"whole_install": True, "explain": True}),
        "gaps": _stdout(server, ["filemap", "gaps"], {}),
        "explains": explains,
        "runs": [],
        "steps": steps,
    }
    return scenario, first


def _install_mode(out: dict[str, Any]) -> bool:
    """Whether the page read the whole install (`tree --install`, M13-27)
    rather than one flavor folder (M13-25)."""
    return any(p.get("whole_install") is True for p in _asked(out, ["tree"]))


def _in_flavor(
    out: dict[str, Any], rows: list[dict[str, Any]], flavor: str
) -> list[dict[str, Any]]:
    """The rows under one flavor folder, with paths relative to it, whichever
    stage drew them: a page reading the whole install keys a row by its path
    from the install root, so the flavor folder is `flavor` and a file in it
    `flavor/...`; rows outside it are dropped."""
    if not _install_mode(out):
        return rows
    kept = []
    for row in rows:
        path = row["path"]
        if path == flavor:
            kept.append({**row, "path": ""})
        elif isinstance(path, str) and path.startswith(flavor + "/"):
            kept.append({**row, "path": path[len(flavor) + 1 :]})
    return kept


def _asked(out: dict[str, Any], command: list[str]) -> list[dict[str, Any]]:
    return [
        r["body"]["params"]
        for r in out["requests"]
        if r["body"] is not None and r["body"]["command"] == command
    ]


def _group(verdict: dict[str, Any]) -> str:
    """The Verdict filter's group: the level, except that a link, at any
    level, is its own group "link" (round 2 on #206)."""
    return "link" if verdict.get("link") is True else str(verdict["level"])


def _texts(pane: dict[str, Any]) -> list[str]:
    """Every string a detail-pane capture holds."""
    out: list[str] = []
    for value in pane.values():
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            out += [v["text"] for v in value if isinstance(v, dict) and "text" in v]
    return out


def _ancestors(path: str) -> set[str]:
    parts = path.split("/")
    return {""} | {"/".join(parts[:i]) for i in range(1, len(parts))}


def test_the_first_view_opens_wtf_and_addons(server: Any, install: Path, tmp_path: Path) -> None:
    """Owner, 2026-10-16: the tree opens on the flavor folder with `WTF/` and
    `Interface/AddOns/` expanded. The explorer, not the commands, is shown."""
    scenario, first = _scenario(server, [], [])
    out = _drive(tmp_path / "node", scenario)
    view = out["first"]
    assert view["explorerHidden"] is False
    rows = {r["path"]: r for r in _in_flavor(out, view["rows"], first["flavor_folder"])}
    assert rows[""]["expanded"] == "true", "the flavor folder is the open root"
    for open_folder in ("WTF", "Interface", "Interface/AddOns"):
        assert rows[open_folder]["expanded"] == "true", open_folder
    assert "WTF/Config.wtf" in rows
    assert "Interface/AddOns/DBM-Brawlers" in rows
    # Only those: every other folder with something under it starts closed,
    # so nothing below it is drawn.
    others = [p for p, r in rows.items() if r["expanded"] is not None]
    others = [p for p in others if p not in ("", "WTF", "Interface", "Interface/AddOns")]
    assert "WTF/Account" in others, "the capture has a folder to keep closed"
    for path in others:
        assert rows[path]["expanded"] == "false", path
        assert not any(p.startswith(path + "/") for p in rows), path
    # The page asked for the first flavor folder's tree, with the explain flag.
    trees = _asked(out, ["tree"])
    assert trees and trees[0]["explain"] is True
    assert trees[0].get("flavor", first["flavor_folder"]) == first["flavor_folder"]


def test_every_item_drawn_has_a_badge_from_python(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """A badge on every row drawn: each item, each folder above them
    (`ancestors`) and the flavor folder (`flavor_folder_verdict`). The badge
    says the verdict's `label`, Python's player words, and carries its
    `words`; a file the server may replace gets a second marker saying
    `server_label`, and no other row does. Each level gets its own class,
    which the colours hang on. No label is written into the script (D3)."""
    scenario, first = _scenario(server, [], [{"open_all": True}])
    out = _drive(tmp_path / "node", scenario)
    rows = _in_flavor(out, out["steps"][0]["rows"], first["flavor_folder"])
    drawn = {r["path"]: r for r in rows}
    listed = {i["path"]: i for i in [*first["items"], *first["ancestors"]]}
    assert first["ancestors"], "the capture has folders above its items"
    assert set(listed) <= set(drawn), sorted(set(listed) - set(drawn))[:5]
    verdicts = {p: i["verdict"] for p, i in listed.items()}
    verdicts[""] = first["flavor_folder_verdict"]
    assert set(drawn) <= set(verdicts), sorted(set(drawn) - set(verdicts))[:5]
    by_level: dict[str, set[str]] = {}
    for path, row in drawn.items():
        verdict = verdicts[path]
        badge = row["badge"]
        assert badge is not None, path
        assert verdict["label"], path
        assert badge["text"].startswith(verdict["label"]), (path, badge)
        assert verdict["words"] in badge["text"] + "\n" + badge["title"], (path, badge)
        by_level.setdefault(_group(verdict), set()).add(badge["cls"])
        extra = [b["text"] for b in row["badges"][1:]]
        assert extra == ([verdict["server_label"]] if verdict["server_label"] else []), path
    assert any(v["server_label"] for v in verdicts.values()), "the capture has one to mark"
    for level, classes in by_level.items():
        assert len(classes) == 1, (level, classes)
    assert len({min(c) for c in by_level.values()}) == len(by_level), by_level
    # As a string literal: "folder" is also a tree item's or gap group's `kind`.
    script = re.sub(r'\.kind\s*===\s*"folder"', "", _script())
    for verdict in verdicts.values():
        for said in (verdict["label"], verdict["server_label"]):
            if said:
                for quote in ('"', "'", "`"):
                    assert quote + said + quote not in script, said


def test_filter_to_no_and_by_text(server: Any, install: Path, tmp_path: Path) -> None:
    """The owner's check, "filter to no": only `no` items stay, with the
    folders that lead to them. The level choices come from the data (D3)."""
    steps: list[dict[str, Any]] = [{"level": "no"}, {"level": ""}, {"text": "config.wtf"}]
    scenario, first = _scenario(server, [], steps)
    out = _drive(tmp_path / "node", scenario)
    items = {i["path"]: i for i in [*first["items"], *first["ancestors"]]}
    levels = {_group(i["verdict"]): i["verdict"]["label"] for i in items.values()}
    assert out["first"]["levels"][0] == ""
    assert set(out["first"]["levels"][1:]) == set(levels)
    # Each choice reads as Python's label for its level.
    for value, text in zip(out["first"]["levels"], out["first"]["levelTexts"], strict=True):
        if value:
            assert text == levels[value], (value, text)

    def check(view: dict[str, Any], keep: Any) -> set[str]:
        drawn = _in_flavor(out, view["rows"], first["flavor_folder"])
        hits = {r["path"] for r in drawn if r["path"] in items and keep(items[r["path"]])}
        assert hits, view["status"]
        allowed = hits.union(*(_ancestors(p) for p in hits))
        for row in drawn:
            assert row["path"] in allowed, row["path"]
        return hits

    no_rows = check(out["steps"][0], lambda i: _group(i["verdict"]) == "no")
    assert no_rows == {p for p, i in items.items() if _group(i["verdict"]) == "no"}
    # Clearing the filter draws the first view again.
    first_paths = [r["path"] for r in out["first"]["rows"]]
    assert [r["path"] for r in out["steps"][1]["rows"]] == first_paths
    found = check(out["steps"][2], lambda i: "config.wtf" in i["path"].lower())
    assert "WTF/Config.wtf" in found


def test_the_detail_pane_shows_what_who_and_the_verdict(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """The owner's check: open `Config.wtf` and one SavedVariables file. The
    pane shows what the file is, who writes it and when, and the verdict's
    label and words, all from `explain --json`; the home folder never
    appears. The file-map text is the `plain` form Python makes (domain
    review, finding 5 on #206), so no Markdown reaches the player: a row
    whose cells hold backticks or `**` is opened too."""
    tree = json.loads(_stdout(server, ["tree"], {"explain": True, "flavor": FLAVOR}))
    marked = sorted(
        i["path"]
        for i in tree["items"]
        if i["kind"] == "file"
        and i["explained"]
        and any(m in i["explained"][k] for m in ("`", "**") for k in ("what", "written_by"))
    )
    assert marked, "the capture has a file whose file-map row holds Markdown"
    server_side = sorted(
        i["path"] for i in tree["items"] if i["kind"] == "file" and i["verdict"]["server_label"]
    )
    assert server_side, "the capture has a file the server may replace"
    rels = ["WTF/Config.wtf", SV_FILE, marked[0], server_side[0]]
    steps: list[dict[str, Any]] = [{"open_all": True}, *({"click": r} for r in rels)]
    scenario, first = _scenario(server, rels, steps)
    out = _drive(tmp_path / "node", scenario)
    explains = _asked(out, ["explain"])
    assert len(explains) == len(rels)
    for rel, pane, params in zip(rels, out["steps"][1:], explains, strict=True):
        assert "missing" not in pane, pane
        target = first["install_root"] + os.sep + first["flavor_folder"] + "/" + rel
        # The absolute path, the install root and the flavor folder; a page
        # reading the whole install (M13-27) may join with `sep` throughout.
        assert set(params) == {"path", "root", "flavor"}, params
        assert _norm(params["path"]) == _norm(target), params
        assert params["root"] == first["install_root"], params
        assert params["flavor"] == first["flavor_folder"], params
        report = json.loads(scenario["explains"][_norm(target)])
        entry = report["result"]["entry"]
        plain = report.get("plain") or entry
        assert plain["what"] in pane["facts"], rel
        assert plain["written_by"] in pane["facts"], rel
        for mark in ("`", "**", "[verify"):
            assert mark not in pane["facts"], (rel, mark)
        verdict = report["verdict"]
        assert verdict["words"] in pane["verdict"], rel
        assert verdict["label"] in pane["verdict"], rel
        if verdict["server_label"]:
            assert verdict["server_label"] in pane["verdict"], rel
        assert pane["verdictBadge"], "the verdict level is drawn as a badge"
        assert rel.rsplit("/", 1)[-1] in pane["title"]
        assert pane["status"] == ""
        for text in _texts(pane):
            assert str(home) not in text, rel


def test_home_is_shown_as_tilde(server: Any, install: Path, home: Path, tmp_path: Path) -> None:
    scenario, first = _scenario(server, [], [])
    assert first["install_root"].startswith(str(home) + os.sep)
    root = _drive(tmp_path / "node", scenario)["first"]["treeRoot"]
    assert root.startswith("~"), root
    assert str(home) not in root
    # The flavor folder (M13-25), or the install root when the page reads
    # the whole install (M13-27).
    assert root.endswith((first["flavor_folder"], Path(first["install_root"]).name)), root


def test_the_flavor_picker_shows_with_two_folders_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """Hidden with one flavor folder, shown with two; picking one loads its
    tree. The second folder is a copy of the capture's (constructed)."""
    single, _ = _scenario(server, [], [])
    assert _drive(tmp_path / "one", single)["first"]["flavorBoxHidden"] is True
    second = "_ptr_"  # tests may name a flavor (L6); the library discovers it
    shutil.copytree(install / FLAVOR, install / second)
    scenario, first = _scenario(server, [], [])
    flavors = [f["folder"] for f in json.loads(scenario["install_show"])["flavors"]]
    assert sorted(flavors) == sorted([FLAVOR, second])
    other = next(f for f in flavors if f != first["flavor_folder"])
    scenario["steps"] = [{"flavor": other}]
    out = _drive(tmp_path / "two", scenario)
    if _install_mode(out):
        # M13-27 draws every flavor folder in one tree; the picker belongs
        # to the one-folder-at-a-time view, and stage 2's graders cover both
        # folders (test_ui_explorer_stage2.py).
        pytest.skip("the page reads the whole install: no flavor picker to grade")
    assert out["first"]["flavorBoxHidden"] is False
    assert out["first"]["treeRoot"].endswith(first["flavor_folder"])
    assert out["steps"][0]["treeRoot"].endswith(other)
    assert _asked(out, ["tree"])[-1]["flavor"] == other


def test_hostile_names_draw_as_inert_text_constructed(
    server: Any, hostile: Path, tmp_path: Path
) -> None:
    """Names with `</script>`, `<img onerror=…>`, U+2028 and U+202E, drawn by
    the page: markup stays text (the stand-in DOM throws on an HTML sink),
    and the two invisible characters are shown, never applied."""
    saved = "WTF/Account/90000001#6/SavedVariables/"
    picked = [saved + SCRIPT_CLOSE + ".lua", saved + RLO + "aul.exe.lua"]
    steps: list[dict[str, Any]] = [{"open_all": True}, *({"click": p} for p in picked)]
    scenario, _ = _scenario(server, picked, steps)
    out = _drive(tmp_path / "node", scenario)
    rows = {r["path"]: r for r in _in_flavor(out, out["steps"][0]["rows"], FLAVOR)}
    hostile_paths = [saved + SCRIPT_CLOSE + ".lua"]
    hostile_paths += [saved + n for n in HOSTILE_SV if n != "<"]
    hostile_paths += ["Interface/AddOns/" + n for n in HOSTILE_ADDONS]
    for path in hostile_paths:
        assert path in rows, repr(path)
        row = rows[path]
        assert row["badge"] is not None, repr(path)
        for text in (row["name"], row["nameTitle"], row["text"]):
            assert LSEP not in text and RLO not in text, repr(text)
        if LSEP in path:
            assert "2028" in row["name"], row["name"]
        if RLO in path:
            assert "202E" in row["name"].upper(), row["name"]
    assert IMG in rows["Interface/AddOns/" + IMG]["name"], "markup is drawn as its own text"
    assert "script>.lua" in rows[saved + SCRIPT_CLOSE + ".lua"]["name"]
    for pane in out["steps"][1:]:
        assert "missing" not in pane, pane
        for text in _texts(pane):
            assert LSEP not in text and RLO not in text, repr(text)
    assert "script>.lua" in out["steps"][1]["title"]
    assert "202E" in out["steps"][2]["title"].upper()


@pytest.fixture
def linked(install: Path, tmp_path: Path) -> str:
    """An addon folder that is a symlink to a folder outside the install
    (constructed: no capture holds one). Returns its path in the flavor
    folder."""
    target = tmp_path / "elsewhere_constructed" / "LinkedAddon"
    target.mkdir(parents=True)
    (target / "LinkedAddon.toc").write_text("## Interface: 1\n", encoding="utf-8")
    link = install / FLAVOR / "Interface" / "AddOns" / "LinkedAddon"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:  # Windows without the symlink privilege
        pytest.skip(f"cannot make a symlink here: {error}")
    return "Interface/AddOns/LinkedAddon"


def test_a_linked_addon_has_its_own_badge_group_and_words_constructed(
    server: Any, install: Path, linked: str, tmp_path: Path
) -> None:
    """Round 2 on #206: a symlinked addon is marked as a link. Its badge says
    Python's link label with the `badge-link` class on top of its level's;
    the Verdict filter lists links as their own choice "link", never under
    the gate label, and the gate choice leaves the link out; the detail pane
    shows the link's label and words."""
    from wowlab_core import verdict as verdict_module

    steps: list[dict[str, Any]] = [
        {"open_all": True},
        {"level": "link"},
        {"level": "gate"},
        {"level": ""},
        {"click": linked},
    ]
    scenario, first = _scenario(server, [linked], steps)
    listed = {i["path"]: i for i in [*first["items"], *first["ancestors"]]}
    assert linked in listed, "tree lists the link"
    link_verdict = listed[linked]["verdict"]
    assert link_verdict["link"] is True
    assert link_verdict["label"] == verdict_module.LINK_LABEL
    gate_labels = {v["label"] for v in (i["verdict"] for i in listed.values()) if not v["link"]}
    assert link_verdict["label"] not in gate_labels
    out = _drive(tmp_path / "node", scenario)

    # The badge.
    row = {r["path"]: r for r in _in_flavor(out, out["steps"][0]["rows"], FLAVOR)}[linked]
    badge = row["badge"]
    assert badge is not None
    assert badge["text"].startswith(link_verdict["label"]), badge
    classes = badge["cls"].split()
    assert "badge-link" in classes and "badge" in classes, classes
    assert f"badge-{link_verdict['level']}" in classes, classes
    assert link_verdict["words"] in badge["text"] + "\n" + badge["title"]

    # The filter: "link" is its own choice, read as the link label.
    choices = dict(zip(out["first"]["levels"], out["first"]["levelTexts"], strict=True))
    assert choices.get("link") == link_verdict["label"], choices
    assert "gate" in choices and choices["gate"] == verdict_module.LABELS["gate"], choices
    links = {p for p, i in listed.items() if i["verdict"]["link"]}
    shown_link = {r["path"] for r in _in_flavor(out, out["steps"][1]["rows"], FLAVOR)} - {""}
    assert linked in shown_link
    for path in shown_link:
        assert path in links or any(q.startswith(path + "/") for q in links), path
    shown_gate = {r["path"] for r in _in_flavor(out, out["steps"][2]["rows"], FLAVOR)}
    assert shown_gate, "the capture has gate files"
    assert not shown_gate & links, sorted(shown_gate & links)
    for path in shown_gate & set(listed):
        verdict = listed[path]["verdict"]
        is_way_in = any(
            q.startswith(path + "/") and _group(listed[q]["verdict"]) == "gate" for q in listed
        )
        assert _group(verdict) == "gate" or is_way_in, path

    # The detail pane.
    pane = out["steps"][4]
    assert "missing" not in pane, pane
    report = json.loads(scenario["explains"][next(iter(scenario["explains"]))])
    assert report["verdict"]["link"] is True
    assert report["verdict"]["label"] in pane["verdict"], pane
    assert report["verdict"]["words"] in pane["verdict"], pane
    assert "badge-link" in pane["verdictBadge"].split(), pane


# ─── the cap counts the escapes (stand-in `wowlab`, constructed) ─────────────


def _stand_in_tree() -> Any:
    app = typer.Typer()

    @app.command("emit")
    def emit(json_out: bool = typer.Option(False, "--json")) -> None:
        """Prints what the test's script prints."""

    @app.command("other")
    def other(json_out: bool = typer.Option(False, "--json")) -> None:
        """Keeps `emit` a subcommand."""

    return typer.main.get_command(app)


@contextmanager
def _stand_in(tmp_path: Path, source: str, cap: int) -> Iterator[Any]:
    script = tmp_path / "m13-25-emit_constructed.py"
    script.write_text(source, encoding="utf-8")
    served = ui.make_server(
        command=_stand_in_tree(),
        wowlab_argv=[sys.executable, "-I", str(script)],
        output_cap_bytes=cap,
    )
    thread = threading.Thread(target=served.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.shutdown()
        served.server_close()
        thread.join(10)


_EMIT = "import sys\nsys.{stream}.buffer.write({data!r})\nsys.{stream}.flush()\n"
_ESCAPED = ["<", ">", "&", LSEP, chr(0x2029)]
_FIXED = 200  # the JSON around the two strings, generously


@pytest.mark.parametrize("char", _ESCAPED, ids=["lt", "gt", "amp", "u2028", "u2029"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_the_cap_counts_six_bytes_per_escape_constructed(
    tmp_path: Path, char: str, stream: str
) -> None:
    """Each of `< > & U+2028 U+2029` costs its six-byte escape against the
    cap: n of them fit a cap of 6n exactly, and a cap of 6n - 1 keeps n - 1,
    whole, with `truncated` set. The body never holds the raw character."""
    n = 1000
    source = _EMIT.format(stream=stream, data=(char * n).encode("utf-8"))
    for cap, kept, cut in ((6 * n, n, False), (6 * n - 1, n - 1, True)):
        with _stand_in(tmp_path, source, cap) as served:
            reply = _run(served, ["emit"], {})
        assert reply.status == 200
        assert char.encode("utf-8") not in reply.body
        result = reply.json()
        assert result["truncated"] is cut, (cap, result["truncated"])
        assert result[stream] == char * kept, (cap, len(result[stream]))
        assert len(reply.body) <= cap + _FIXED


def test_the_escapes_hold_on_stdout_and_stderr_constructed(tmp_path: Path) -> None:
    text = SCRIPT_CLOSE + IMG + "&" + LSEP + chr(0x2029) + RLO
    data = text.encode("utf-8")
    source = _EMIT.format(stream="stdout", data=data) + _EMIT.format(stream="stderr", data=data)
    with _stand_in(tmp_path, source, ui.DEFAULT_OUTPUT_CAP_BYTES) as served:
        reply = _run(served, ["emit"], {})
    for raw in ("<", ">", "&", LSEP, chr(0x2029)):
        assert raw.encode("utf-8") not in reply.body, repr(raw)
    result = reply.json()
    assert result["stdout"] == text and result["stderr"] == text
    assert result["truncated"] is False


def test_a_hostile_name_in_an_error_stays_escaped_constructed(server: Any, hostile: Path) -> None:
    """A run on a hostile path that is not there: whatever it prints, the
    body holds no markup and no raw separator."""
    root = str(hostile)
    missing = root + os.sep + FLAVOR + "/WTF/" + IMG + LSEP + "missing" + SCRIPT_CLOSE
    reply = _run(server, ["explain"], {"path": missing, "root": root, "flavor": FLAVOR})
    assert reply.status == 200
    for raw in ("<", ">", "&", LSEP, chr(0x2029)):
        assert raw.encode("utf-8") not in reply.body, repr(raw)
    result = reply.json()
    assert "missing" in result["stdout"] + result["stderr"]


# ─── /api/commands: home and sep ─────────────────────────────────────────────


def _commands_with_home(monkeypatch: pytest.MonkeyPatch, value: str) -> _Reply:
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, value)
    served = ui.make_server(command=_stand_in_tree())
    thread = threading.Thread(target=served.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        return _send(served, "GET", "/api/commands")
    finally:
        served.shutdown()
        served.server_close()
        thread.join(10)


@pytest.mark.skipif(os.name == "nt", reason="a POSIX root folder")
def test_a_root_home_is_not_offered_for_tilde_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Home at the file-system root would turn every path into `~`."""
    listed = _commands_with_home(monkeypatch, os.sep).json()
    assert listed["home"] == ""
    assert listed["sep"] == os.sep


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids < and > in file names")
def test_a_hostile_home_is_escaped_in_the_command_list_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The home folder is data from the machine, like a file name, and it
    reaches the page in `/api/commands`. It is escaped as a run's output is:
    no `< > &` and no raw U+2028 or U+2029 in the body, and the decoded value
    is unchanged."""
    folder = tmp_path / ("h<script>&" + LSEP + chr(0x2029))
    folder.mkdir()
    reply = _commands_with_home(monkeypatch, str(folder))
    assert reply.json()["home"] == str(folder)
    for raw in ("<", ">", "&", LSEP, chr(0x2029)):
        assert raw.encode("utf-8") not in reply.body, repr(raw)


def test_the_whole_command_list_is_escaped_constructed() -> None:
    """Every string in `/api/commands` is escaped, not only `home`: a help
    text with markup reaches the page decoded and unchanged, and the body
    holds no `< > &`."""
    app = typer.Typer()
    helped = "Shows </script><img src=x onerror=alert(1)> & more."

    @app.command("emit", help=helped)
    def emit(json_out: bool = typer.Option(False, "--json")) -> None:
        pass

    @app.command("other")
    def other(json_out: bool = typer.Option(False, "--json")) -> None:
        """Keeps `emit` a subcommand."""

    served = ui.make_server(command=typer.main.get_command(app))
    thread = threading.Thread(target=served.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        reply = _send(served, "GET", "/api/commands")
    finally:
        served.shutdown()
        served.server_close()
        thread.join(10)
    for raw in ("<", ">", "&"):
        assert raw.encode("utf-8") not in reply.body, repr(raw)
    helps = [c["help"] for c in reply.json()["commands"] if c["path"] == ["emit"]]
    assert helps and helped in helps[0], helps


# ─── the look the owner set (2026-10-16) ─────────────────────────────────────


def _css() -> str:
    return re.sub(
        r"/\*.*?\*/", "", (_TEMPLATES / "page.css").read_text(encoding="utf-8"), flags=re.DOTALL
    )


def _block(css: str, start: int) -> str:
    """The text inside the braces that open at or after `start`."""
    opened = css.index("{", start)
    depth = 0
    for at in range(opened, len(css)):
        if css[at] == "{":
            depth += 1
        elif css[at] == "}":
            depth -= 1
            if depth == 0:
                return css[opened + 1 : at]
    raise AssertionError("unbalanced CSS")


_VAR = r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b"
_COLOUR = r"(?<![-\w])color:\s*(#[0-9a-fA-F]{6})\b"


def _palettes() -> dict[str, dict[str, str]]:
    css = _css()
    dark = dict(re.findall(_VAR, _block(css, css.index(":root"))))
    media = re.search(r"@media\s*\(\s*prefers-color-scheme\s*:\s*light\s*\)", css)
    assert media is not None, "a light scheme follows prefers-color-scheme"
    light = dict(re.findall(_VAR, _block(css, media.start())))
    assert light, "the light scheme sets its own colours"
    return {"dark": dark, "light": {**dark, **light}}


def _luminance(colour: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Text colours on the backgrounds they are drawn on, by the palette's own
# names. If the palette is renamed, the test-writer updates this list.
_TEXT_PAIRS = [
    (fg, bg)
    for fg in ("--text", "--muted", "--gold", "--error", "--field-text")
    for bg in ("--page", "--panel", "--panel-edge", "--selected", "--hover", "--field")
] + [(fg, bg) for fg in ("--ink", "--ink-muted") for bg in ("--parchment-hi", "--parchment-lo")]


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_text_meets_wcag_aa_in_both_schemes(scheme: str) -> None:
    palette = _palettes()[scheme]
    for fg, bg in _TEXT_PAIRS:
        ratio = _contrast(palette[fg], palette[bg])
        assert ratio >= 4.5, (scheme, fg, bg, round(ratio, 2))
    # Literal text colours on the parchment.
    rules = re.findall(r"(?:\.parchment|#explain-status)[^{}]*\{[^}]*\}", _css())
    assert rules
    for rule in rules:
        for colour in re.findall(_COLOUR, rule):
            for bg in ("--parchment-hi", "--parchment-lo"):
                assert _contrast(colour, palette[bg]) >= 4.5, (colour, bg)


def _hue_sat(colour: str) -> tuple[float, float]:
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    high, low = max(r, g, b), min(r, g, b)
    light = (high + low) / 2
    if high == low:
        return 0.0, 0.0
    sat = (high - low) / (2 - high - low if light > 0.5 else high + low)
    if high == r:
        hue = ((g - b) / (high - low)) % 6
    elif high == g:
        hue = (b - r) / (high - low) + 2
    else:
        hue = (r - g) / (high - low) + 4
    return hue * 60, sat


def test_badges_read_like_item_quality() -> None:
    """Gold for `gate`, red for `no`, grey for `unclassified`, muted for
    `folder` and `not_applicable`; each badge's text meets AA on every stop
    of its own background."""
    css = _css()
    text: dict[str, str] = {}
    for level in ("gate", "no", "unclassified", "folder", "not_applicable"):
        match = re.search(rf"[^{{}}]*\.badge-{level}(?![\w-])[^{{}}]*\{{([^}}]*)\}}", css)
        assert match is not None, f"no badge colour for {level}"
        rule = match.group(1)
        colour = re.search(_COLOUR, rule)
        assert colour is not None, level
        text[level] = colour.group(1)
        background = re.search(r"background[^:]*:([^;]*)", rule)
        assert background is not None, level
        stops = re.findall(r"#[0-9a-fA-F]{6}\b", background.group(1))
        assert stops, level
        for stop in stops:
            assert _contrast(text[level], stop) >= 4.5, (level, stop)
    hue, sat = _hue_sat(text["gate"])
    assert 35 <= hue <= 60 and sat > 0.5, ("gate reads gold", hue, sat)
    hue, sat = _hue_sat(text["no"])
    assert (hue <= 15 or hue >= 345) and sat > 0.5, ("no reads red", hue, sat)
    assert _hue_sat(text["unclassified"])[1] < 0.1, "unclassified reads grey"
    for level in ("folder", "not_applicable"):
        assert _hue_sat(text[level])[1] < 0.35, f"{level} reads muted"
        assert text[level] not in (text["gate"], text["no"], text["unclassified"]), level


def test_a_serif_system_font_and_no_art_from_outside() -> None:
    css = _css()
    stacks = re.findall(r"(?:font-family|--serif)\s*:\s*([^;]+);", css)
    assert any(s.strip().endswith("serif") and "sans-serif" not in s for s in stacks), stacks
    for marker in ("@font-face", "url(", "@import", "image-set("):
        assert marker not in css, marker


def test_the_level_choices_are_not_written_into_the_page(server: Any) -> None:
    """The verdict filter starts with "All" only; the levels come from the
    data (§14.5 D3)."""
    body = _page(server).body.decode("utf-8")
    select = re.search(r'<select id="level-filter">(.*?)</select>', body, flags=re.DOTALL)
    assert select is not None
    assert re.findall(r"<option\b", select.group(1)) == ["<option"]
    assert 'value=""' in select.group(1)
