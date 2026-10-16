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
  Badge words are not pinned: the owner's player words will come from a new
  `label` on `Verdict` in core.
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
    "setTimeout(",
    "setInterval(",
    ".src",
    ".href",
    "location.assign",
    "window.open",
    'setAttribute("on',
    'setAttribute("style',
    ".style.",
]


def test_the_script_writes_text_only(server: Any) -> None:
    script = _script()
    for sink in _HTML_SINKS:
        assert sink not in script, sink
    # Attributes the script sets are a closed set, none of them a URL or a handler.
    names = set(re.findall(r'setAttribute\("([^"]+)"', script))
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
    # `kind` is the tree item's file-or-folder field, not a verdict level.
    levels_free = script.replace('item.kind === "folder"', "")
    for level in ("gate", "no", "folder", "not_applicable", "unclassified"):
        assert f'"{level}"' not in levels_free, level
        assert f"'{level}'" not in script, level
    for words in ("write gate", "server may replace", "Blizzard", "snapshot restore"):
        assert words not in script, words


def test_the_explorer_runs_only_listed_read_commands(server: Any) -> None:
    listed = _send(server, "GET", "/api/commands").json()
    offered = {tuple(c["path"]): c for c in listed["commands"]}
    used = {
        tuple(json.loads("[" + inside + "]"))
        for inside in re.findall(r"runJson\(\[([^\]]*)\]", _script())
    }
    assert used == {("install", "show"), ("tree",), ("explain",)}
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
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.parentElement = null; this._text = "";
    this.attrs = {}; this.dataset = {}; this.listeners = {}; this.className = "";
    this.hidden = false; this.disabled = false; this.value = ""; this.title = "";
    const self = this;
    this.classList = {
      add(c) { const s = new Set(self.className.split(" ").filter(Boolean)); s.add(c); self.className = [...s].join(" "); },
      remove(c) { self.className = self.className.split(" ").filter((x) => x && x !== c).join(" "); },
      contains(c) { return self.className.split(" ").includes(c); },
      toggle(c, on) { if (on === undefined) on = !this.contains(c); if (on) this.add(c); else this.remove(c); return on; },
    };
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(t) { this._text = String(t); this.children = []; }
  set innerHTML(_) { throw new Error("HTML sink: innerHTML"); }
  set outerHTML(_) { throw new Error("HTML sink: outerHTML"); }
  insertAdjacentHTML() { throw new Error("HTML sink: insertAdjacentHTML"); }
  appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(typeof c === "string" ? Object.assign(new El("#text"), { _text: c }) : c); }
  replaceChildren(...cs) { this.children = []; this._text = ""; for (const c of cs) this.appendChild(c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  fire(ev) { for (const fn of this.listeners[ev] || []) fn({ target: this, preventDefault() {} }); }
  all() { const o = []; for (const c of this.children) { o.push(c, ...c.all()); } return o; }
  querySelectorAll(sel) {
    if (sel.startsWith(".")) return this.all().filter((e) => e.className.split(" ").includes(sel.slice(1)));
    return this.all().filter((e) => e.tagName === sel);
  }
  get options() { return this.children.filter((c) => c.tagName === "option"); }
  remove(i) { if (i === undefined) return; const o = this.options[i]; this.children.splice(this.children.indexOf(o), 1); }
}
const ids = {};
for (const id of ["status", "flavor-box", "flavor", "level-filter", "text-filter",
  "explorer-status", "tree-root", "tree", "explain-title", "explain-path", "explain-verdict",
  "explain-facts", "explain-status", "tab-explorer", "tab-commands", "explorer-view",
  "commands-view", "commands", "title", "help", "fields", "run", "stdout", "stderr"]) ids[id] = new El("x");
ids["flavor-box"].hidden = true;
ids["commands-view"].hidden = true;
const all = new El("option"); all.value = ""; ids["level-filter"].appendChild(all);
const requests = [];
const answer = (obj, ok) => ({ ok: ok !== false, status: ok === false ? 403 : 200, json: async () => obj });
const fetchStub = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  requests.push({ url: String(url), body: body });
  if (String(url).startsWith("/api/commands?")) return answer(scenario.commands);
  if (!String(url).startsWith("/api/run?")) return answer({ error: "unknown" }, false);
  const key = body.command.join(" ");
  let stdout = null;
  if (key === "install show") stdout = scenario.install_show;
  else if (key === "tree") stdout = scenario.trees[body.params.flavor || scenario.first_flavor];
  else if (key === "explain") stdout = scenario.explains[body.params.path];
  if (typeof stdout !== "string") {
    return answer({ exit_code: 2, timed_out: false, truncated: false, stdout: "", stderr: "harness: no reply for " + key });
  }
  return answer({ exit_code: 0, timed_out: false, truncated: false, stdout: stdout, stderr: "" });
};
const ctx = { window: { location: { search: "?token=t" } },
  document: { getElementById: (id) => ids[id], createElement: (t) => new El(t) },
  URLSearchParams, fetch: fetchStub, console, Set, Map, Promise, JSON, Error };
vm.createContext(ctx);
const tick = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };
const rowOf = (li) => li.children.find((c) => c.className.split(" ").includes("row"));
function rows() {
  return ids.tree.all().filter((e) => e.attrs.role === "treeitem").map((li) => {
    const row = rowOf(li) || li;
    const name = row.children.find((c) => c.className.split(" ").includes("name"));
    const badge = row.children.find((c) => c.className.split(" ").includes("badge"));
    return { path: row.dataset.path, name: name ? name.textContent : null,
      nameTitle: name ? name.title : null, expanded: li.getAttribute("aria-expanded"),
      text: row.textContent,
      badge: badge ? { text: badge.textContent, title: badge.title, cls: badge.className } : null };
  });
}
function liOf(path) {
  return ids.tree.all().find((e) => e.attrs.role === "treeitem" && rowOf(e) && rowOf(e).dataset.path === path);
}
function snap() {
  return { rows: rows(), treeRoot: ids["tree-root"].textContent, status: ids["explorer-status"].textContent,
    flavorBoxHidden: ids["flavor-box"].hidden, explorerHidden: ids["explorer-view"].hidden,
    levels: ids["level-filter"].options.map((o) => o.value) };
}
function detail() {
  const badge = ids["explain-verdict"].all().find((e) => e.className.split(" ").includes("badge"));
  return { title: ids["explain-title"].textContent, path: ids["explain-path"].textContent,
    verdict: ids["explain-verdict"].textContent, verdictBadge: badge ? badge.className : "",
    facts: ids["explain-facts"].textContent, status: ids["explain-status"].textContent };
}
(async () => {
  const out = { steps: [] };
  try {
    vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
    await tick();
    out.first = snap();
    for (const step of scenario.steps) {
      if (step.open_all) {
        for (let n = 0; n < 500; n++) {
          const closed = ids.tree.all().find((e) => e.attrs.role === "treeitem" && e.getAttribute("aria-expanded") === "false");
          if (!closed) break;
          const row = rowOf(closed);
          const twisty = row.children.find((c) => c.tagName === "button" && !c.className.split(" ").includes("name"));
          twisty.fire("click");
          await tick();
        }
        out.steps.push(snap());
      } else if (step.click !== undefined) {
        const li = liOf(step.click);
        if (!li) { out.steps.push({ missing: step.click }); continue; }
        rowOf(li).children.find((c) => c.className.split(" ").includes("name")).fire("click");
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
      }
    }
  } catch (error) {
    out.error = String((error && error.stack) || error);
  }
  out.requests = requests;
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
    return out


def _stdout(server: Any, command: list[str], params: dict[str, Any]) -> str:
    reply = _run(server, command, params)
    assert reply.status == 200, reply.body[:300]
    result = reply.json()
    assert result["exit_code"] == 0, result["stderr"]
    text: str = result["stdout"]
    return text


def _scenario(
    server: Any, explain: list[str], steps: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """What the real CLI prints for the install under test, in the shape the
    harness serves: the command list, `install show`, a tree per flavor
    folder, and an explain for each path in `explain`, keyed by the absolute
    path the page asks for. Returns the scenario and the parsed first tree."""
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
        explains[target] = _stdout(server, ["explain"], params)
    scenario = {
        "commands": listed,
        "install_show": shown_text,
        "first_flavor": flavors[0],
        "trees": trees,
        "explains": explains,
        "steps": steps,
    }
    return scenario, first


def _asked(out: dict[str, Any], command: list[str]) -> list[dict[str, Any]]:
    return [
        r["body"]["params"]
        for r in out["requests"]
        if r["body"] is not None and r["body"]["command"] == command
    ]


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
    rows = {r["path"]: r for r in view["rows"]}
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
    """A badge on every listed item, carrying the verdict Python computed.
    The badge's own text is not pinned: the owner's player words will come
    from a `label` on `Verdict`. The verdict's words reach the player on the
    row, and each level gets its own class, which the colours hang on."""
    scenario, first = _scenario(server, [], [{"open_all": True}])
    out = _drive(tmp_path / "node", scenario)
    drawn = {r["path"]: r for r in out["steps"][0]["rows"]}
    items = {i["path"]: i for i in first["items"]}
    assert set(items) <= set(drawn), sorted(set(items) - set(drawn))[:5]
    by_level: dict[str, set[str]] = {}
    for path, item in items.items():
        badge = drawn[path]["badge"]
        assert badge is not None, path
        words = item["verdict"]["words"]
        assert words in badge["text"] + "\n" + badge["title"], (path, badge)
        by_level.setdefault(item["verdict"]["level"], set()).add(badge["cls"])
    for level, classes in by_level.items():
        assert len(classes) == 1, (level, classes)
    assert len({min(c) for c in by_level.values()}) == len(by_level), by_level


def test_filter_to_no_and_by_text(server: Any, install: Path, tmp_path: Path) -> None:
    """The owner's check, "filter to no": only `no` items stay, with the
    folders that lead to them. The level choices come from the data (D3)."""
    steps: list[dict[str, Any]] = [{"level": "no"}, {"level": ""}, {"text": "config.wtf"}]
    scenario, first = _scenario(server, [], steps)
    out = _drive(tmp_path / "node", scenario)
    items = {i["path"]: i for i in first["items"]}
    levels = {i["verdict"]["level"] for i in items.values()}
    assert out["first"]["levels"][0] == ""
    assert set(out["first"]["levels"][1:]) == levels

    def check(view: dict[str, Any], keep: Any) -> set[str]:
        hits = {r["path"] for r in view["rows"] if r["path"] in items and keep(items[r["path"]])}
        assert hits, view["status"]
        allowed = hits.union(*(_ancestors(p) for p in hits))
        for row in view["rows"]:
            assert row["path"] in allowed, row["path"]
        return hits

    no_rows = check(out["steps"][0], lambda i: i["verdict"]["level"] == "no")
    assert no_rows == {p for p, i in items.items() if i["verdict"]["level"] == "no"}
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
    words, all from `explain --json`; the home folder never appears."""
    rels = ["WTF/Config.wtf", SV_FILE]
    steps: list[dict[str, Any]] = [{"open_all": True}, *({"click": r} for r in rels)]
    scenario, first = _scenario(server, rels, steps)
    out = _drive(tmp_path / "node", scenario)
    explains = _asked(out, ["explain"])
    assert len(explains) == 2
    for rel, pane, params in zip(rels, out["steps"][1:], explains, strict=True):
        assert "missing" not in pane, pane
        target = first["install_root"] + os.sep + first["flavor_folder"] + "/" + rel
        assert params == {
            "path": target,
            "root": first["install_root"],
            "flavor": first["flavor_folder"],
        }
        report = json.loads(scenario["explains"][target])
        entry = report["result"]["entry"]
        assert entry["what"] in pane["facts"], rel
        assert entry["written_by"] in pane["facts"], rel
        assert report["verdict"]["words"] in pane["verdict"], rel
        assert pane["verdictBadge"], "the verdict level is drawn as a badge"
        assert rel.rsplit("/", 1)[-1] in pane["title"]
        assert pane["status"] == ""
        for text in pane.values():
            assert str(home) not in text, rel


def test_home_is_shown_as_tilde(server: Any, install: Path, home: Path, tmp_path: Path) -> None:
    scenario, first = _scenario(server, [], [])
    assert first["install_root"].startswith(str(home) + os.sep)
    root = _drive(tmp_path / "node", scenario)["first"]["treeRoot"]
    assert root.startswith("~"), root
    assert str(home) not in root
    assert root.endswith(first["flavor_folder"])


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
    rows = {r["path"]: r for r in out["steps"][0]["rows"]}
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
        for text in pane.values():
            assert LSEP not in text and RLO not in text, repr(text)
    assert "script>.lua" in out["steps"][1]["title"]
    assert "202E" in out["steps"][2]["title"].upper()


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


@pytest.mark.xfail(strict=True, reason="M13-25 fix round")
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
