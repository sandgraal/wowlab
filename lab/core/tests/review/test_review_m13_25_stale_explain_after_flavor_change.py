# Probe from review of m13/25-explorer-first-stage; reproduces an explain reply for
# a file of the previous flavor folder filling the detail pane after the flavor
# picker loaded another folder (loadTree clears the detail but leaves explainSeq).
"""Drives `templates/page.js` under Node with a minimal stand-in DOM and a fetch
whose replies the test releases in a chosen order. Constructed (no install, no
browser). Skipped when `node` is not on PATH."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from wowlab_core import ui

PAGE_JS = Path(ui.__file__).resolve().parent / "templates" / "page.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.parentElement = null; this._text = "";
    this.attrs = {}; this.dataset = {}; this.listeners = {}; this.className = "";
    this.hidden = false; this.disabled = false; this.value = ""; this.title = "";
    this.classList = { set: new Set(), add(c) { this.set.add(c); },
      toggle(c, on) { if (on) this.set.add(c); else this.set.delete(c); } };
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(t) { this._text = String(t); this.children = []; }
  appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
  replaceChildren() { this.children = []; this._text = ""; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  fire(ev) { for (const fn of this.listeners[ev] || []) fn(); }
  all() { const o = []; for (const c of this.children) { o.push(c, ...c.all()); } return o; }
  querySelectorAll(sel) {
    if (sel === ".row") return this.all().filter((e) => e.className.split(" ").includes("row"));
    return this.all().filter((e) => e.tagName === sel);
  }
  get options() { return this.children; }
  remove(i) { this.children.splice(i, 1); }
}
const ids = {};
for (const id of ["status", "flavor-box", "flavor", "level-filter", "text-filter",
  "explorer-status", "tree-root", "tree", "explain-title", "explain-path", "explain-verdict",
  "explain-facts", "explain-status", "tab-explorer", "tab-commands", "explorer-view",
  "commands-view", "commands", "title", "help", "fields", "run", "stdout", "stderr"]) ids[id] = new El("x");
ids["level-filter"].appendChild(new El("option"));
const pending = [];
const fetchMock = (url, opts) => new Promise((resolve) =>
  pending.push({ body: opts && opts.body ? JSON.parse(opts.body) : null, resolve }));
const reply = (p, obj) => p.resolve({ ok: true, status: 200, json: async () => obj });
const runReply = (p, data) => reply(p, { exit_code: 0, timed_out: false, truncated: false,
  stdout: JSON.stringify(data), stderr: "" });
const ctx = { window: { location: { search: "?token=t" } },
  document: { getElementById: (id) => ids[id], createElement: (t) => new El(t) },
  URLSearchParams, fetch: fetchMock, console, Set, Map };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const tick = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); };
const v = (lvl) => ({ level: lvl, words: lvl + " words" });
const report = (flavor) => ({ install_root: "/home/u/WoW", flavor_folder: flavor, truncated: false,
  errors: [], items: [
    { path: "WTF", kind: "folder", size: null, note: null, verdict: v("folder"), explained: null },
    { path: "WTF/Config.wtf", kind: "file", size: 1, note: null, verdict: v("gate"), explained: null },
  ] });
const find = (text) => ids.tree.all().find((e) => (e.className || "").startsWith("name") && e.textContent === text);
(async () => {
  const swap = process.argv[3] === "swap";
  await tick();
  reply(pending.shift(), { commands: [], home: "/home/u", sep: "/" });
  await tick();
  runReply(pending.shift(), { flavors: [{ folder: "_a_" }, { folder: "_b_" }] });
  await tick();
  runReply(pending.shift(), report("_a_"));
  await tick();
  find("Config.wtf").fire("click");
  await tick();
  const explainReq = pending.shift();
  if (swap) {
    ids.flavor.value = "_b_";
    ids.flavor.fire("change");
    await tick();
    runReply(pending.shift(), report("_b_"));
    await tick();
  }
  runReply(explainReq, { query: "q", verdict: v("gate"), result: { status: "classified",
    path: "WTF/Config.wtf", entry: { id: "config-wtf", what: "STALE-A", written_by: "client" } } });
  await tick();
  console.log(JSON.stringify({ title: ids["explain-title"].textContent,
    facts: ids["explain-facts"].textContent, flavor: explainReq.body.params.flavor,
    treeRoot: ids["tree-root"].textContent }));
})();
"""


def _drive(tmp_path: Path, mode: str) -> dict[str, str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    script = tmp_path / "harness.js"
    script.write_text(HARNESS, encoding="utf-8")
    done = subprocess.run(
        [node, str(script), str(PAGE_JS), mode], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, str] = json.loads(done.stdout.strip().splitlines()[-1])
    return out


def test_positive_control_explain_fills_the_detail_constructed(tmp_path: Path) -> None:
    seen = _drive(tmp_path, "same")
    assert "STALE-A" in seen["facts"]  # the harness reaches the detail pane


def test_explain_of_the_old_flavor_does_not_fill_the_new_view_constructed(
    tmp_path: Path,
) -> None:
    seen = _drive(tmp_path, "swap")
    assert seen["flavor"] == "_a_"
    assert seen["treeRoot"].endswith("_b_")
    # The tree now shows _b_ and nothing is selected; the reply for _a_'s file
    # must be dropped, not drawn as if it described the view on screen.
    assert "STALE-A" not in seen["facts"], seen
