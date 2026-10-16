"use strict";
// The page of `wowlab ui` (ADR-0029, §15.6). Two views:
// - the explorer (M13-25): the tree of one flavor folder from
//   `tree --explain --json`, and the detail of one path from
//   `explain --json`;
// - the commands the server read from the CLI's Click tree, a field for each
//   declared parameter, and what one run printed.
// It holds no game rules and no file-map logic (§14.5 D3): every fact, the
// verdict's level and words included, comes from the CLI's JSON, and this
// script only lays it out. Nothing read from a run is ever parsed as HTML:
// text goes in through textContent only. The home folder is shown as "~".
(function () {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const api = (path) => path + "?token=" + encodeURIComponent(token);
  const byId = (id) => document.getElementById(id);
  let current = null;
  let inputs = [];
  let home = "";
  let sep = "/";

  function make(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function say(text) {
    byId("status").textContent = text;
  }

  // ─── display helpers: layout only ───

  // The home folder written as "~", the way the CLI's text output does.
  function shortHome(text) {
    if (!home || typeof text !== "string") return text;
    if (text === home) return "~";
    let out = text;
    for (const mark of new Set([sep, "/"])) {
      out = out.split(home + mark).join("~" + mark);
    }
    return out;
  }

  // Control, format and separator characters (U+202E reverses the text after
  // it, U+2028 breaks a line) shown as a visible code, never applied.
  const UNSAFE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu;
  function visible(text) {
    if (typeof text !== "string") return "";
    return text.replace(UNSAFE, (ch) => {
      const code = ch.codePointAt(0).toString(16).toUpperCase().padStart(4, "0");
      return "⟨U+" + code + "⟩";
    });
  }

  function shown(text) {
    return visible(shortHome(text));
  }

  // The words of a verdict's badge: Python's label, or the level when an
  // older wowlab sent none.
  function labelOf(verdict) {
    if (verdict && typeof verdict.label === "string" && verdict.label) return verdict.label;
    return verdict && typeof verdict.level === "string" ? verdict.level : "";
  }

  // Text a screen reader reads and the eye does not see.
  function unseen(text) {
    const node = make("span", text);
    node.className = "unseen";
    return node;
  }

  // The verdict as a badge, plus a second marker when `server_label` is set
  // (the server may overwrite the file). The label, the words and the
  // marker's text are Python's; the level only picks a colour. In the tree
  // (`describe`), the words are in the badge for a screen reader and in
  // `title` for a pointer; the detail pane shows them as text.
  function badges(verdict, describe) {
    const out = [];
    if (!verdict) return out;
    const level = typeof verdict.level === "string" ? verdict.level : "";
    const node = make("span", labelOf(verdict));
    node.className = "badge";
    if (/^[a-z_]+$/.test(level)) node.classList.add("badge-" + level);
    if (describe && typeof verdict.words === "string" && verdict.words) {
      node.title = verdict.words;
      node.appendChild(unseen(": " + verdict.words));
    }
    out.push(node);
    if (typeof verdict.server_label === "string" && verdict.server_label) {
      const server = make("span", verdict.server_label);
      server.className = "badge badge-server";
      out.push(server);
    }
    return out;
  }

  // ─── running one command ───

  // One run through /api/run: {data} with the parsed --json output, or
  // {error} with what to tell the player.
  async function runJson(command, params) {
    let reply;
    let result;
    try {
      reply = await fetch(api("/api/run"), {
        method: "POST",
        cache: "no-store",
        credentials: "omit",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: command, params: params }),
      });
      result = await reply.json();
    } catch (error) {
      return { error: "The request failed: " + error };
    }
    if (!reply.ok) return { error: "Refused: " + (result.error || reply.status) };
    if (result.timed_out) return { error: "wowlab " + command.join(" ") + " stopped at its time limit." };
    if (result.truncated) return { error: "wowlab " + command.join(" ") + " printed more than the page can hold." };
    if (result.exit_code !== 0) {
      const text = String(result.stderr || "").trim();
      return { error: text || "wowlab " + command.join(" ") + " ended with exit code " + result.exit_code + "." };
    }
    try {
      return { data: JSON.parse(result.stdout) };
    } catch (_) {
      return { error: "wowlab " + command.join(" ") + " did not print JSON." };
    }
  }

  // ─── the explorer ───

  const explorer = {
    report: null, // the TreeReport
    root: null, // the node of the flavor folder
    flavor: "",
    open: new Set(),
    closedWhileFiltering: new Set(),
    selected: null,
    treeSeq: 0,
    explainSeq: 0,
  };
  // The first view: the flavor folder, with WTF/ and Interface/AddOns/ open
  // (owner, 2026-10-16).
  const FIRST_OPEN = ["", "WTF", "Interface", "Interface/AddOns"];

  function explorerSay(text, isError) {
    const node = byId("explorer-status");
    node.textContent = text;
    node.classList.toggle("error", Boolean(isError));
  }

  // The listed paths of a TreeReport: its items and the folders above them
  // (`ancestors`), each with its verdict from Python.
  function listedOf(report) {
    const ancestors = Array.isArray(report.ancestors) ? report.ancestors : [];
    return report.items.concat(ancestors);
  }

  // The listed paths as a tree of path segments. The flavor folder itself
  // takes `flavor_folder_verdict`. A node no listed path names (only an
  // older wowlab leaves one) has no item and no badge; `explain` gives its
  // verdict when it is picked.
  function buildTree(report) {
    const top = report.flavor_folder_verdict
      ? { note: null, verdict: report.flavor_folder_verdict }
      : null;
    const root = { name: report.flavor_folder, path: "", item: top, children: [], index: new Map() };
    const listed = listedOf(report).slice();
    listed.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
    for (const item of listed) {
      if (typeof item.path !== "string" || item.path === "") continue;
      let node = root;
      let path = "";
      for (const part of item.path.split("/")) {
        path = path ? path + "/" + part : part;
        let child = node.index.get(part);
        if (!child) {
          child = { name: part, path: path, item: null, children: [], index: new Map() };
          node.index.set(part, child);
          node.children.push(child);
        }
        node = child;
      }
      node.item = item;
    }
    return root;
  }

  function isFolder(node) {
    return node.children.length > 0 || (node.item !== null && node.item.kind === "folder");
  }

  function filters() {
    return {
      level: byId("level-filter").value,
      text: byId("text-filter").value.trim().toLowerCase(),
    };
  }

  function matches(node, filter) {
    if (filter.level) {
      if (!node.item || !node.item.verdict || node.item.verdict.level !== filter.level) return false;
    }
    if (filter.text && !node.path.toLowerCase().includes(filter.text)) return false;
    return true;
  }

  // Which nodes are drawn: all of them without a filter; with one, the
  // matches and the folders that lead to them.
  function shownNodes(root, filter) {
    const keep = new Set();
    const active = Boolean(filter.level || filter.text);
    let matched = 0;
    function walk(node) {
      const hit = !active || matches(node, filter);
      if (hit && node.item && node.path !== "") matched += 1;
      let any = hit;
      for (const child of node.children) {
        if (walk(child)) any = true;
      }
      if (any) keep.add(node);
      return any;
    }
    walk(root);
    return { keep: keep, active: active, matched: matched };
  }

  function isOpen(node, active) {
    return active ? !explorer.closedWhileFiltering.has(node.path) : explorer.open.has(node.path);
  }

  function drawNode(node, view, depth) {
    const li = make("li");
    li.setAttribute("role", "treeitem");
    li.setAttribute("aria-level", String(depth));
    li.setAttribute("aria-selected", explorer.selected === node.path ? "true" : "false");
    const row = make("div");
    row.className = "row";
    row.dataset.path = node.path;
    if (explorer.selected === node.path) row.classList.add("selected");
    const children = node.children.filter((child) => view.keep.has(child));
    const open = isOpen(node, view.active);
    if (children.length) {
      li.setAttribute("aria-expanded", open ? "true" : "false");
      const twisty = make("button", open ? "▾" : "▸");
      twisty.type = "button";
      twisty.className = "twisty";
      twisty.setAttribute("aria-label", (open ? "Close " : "Open ") + visible(node.name));
      twisty.addEventListener("click", () => toggle(node, view.active));
      row.appendChild(twisty);
    } else {
      const gap = make("span");
      gap.className = "twisty-gap";
      row.appendChild(gap);
    }
    const folder = isFolder(node);
    const name = make("button", visible(node.name) + (folder ? "/" : ""));
    name.type = "button";
    name.className = folder ? "name folder" : "name";
    name.title = visible(node.path);
    name.addEventListener("click", () => select(node));
    row.appendChild(name);
    if (node.item) {
      for (const mark of badges(node.item.verdict, true)) row.appendChild(mark);
      if (node.item.note) {
        const note = make("span", shown(node.item.note));
        note.className = "note";
        row.appendChild(note);
      }
    }
    li.appendChild(row);
    if (children.length && open) {
      const list = make("ul");
      list.setAttribute("role", "group");
      for (const child of children) list.appendChild(drawNode(child, view, depth + 1));
      li.appendChild(list);
    }
    return li;
  }

  function drawTree() {
    const tree = byId("tree");
    tree.replaceChildren();
    if (!explorer.root) return;
    const filter = filters();
    const view = shownNodes(explorer.root, filter);
    if (view.active && !view.keep.has(explorer.root)) {
      explorerSay("Nothing matches.", false);
      return;
    }
    tree.appendChild(drawNode(explorer.root, view, 1));
    const total = listedOf(explorer.report).length;
    explorerSay(view.active ? view.matched + " of " + total + " paths match." : total + " paths.", false);
  }

  function toggle(node, active) {
    const set = active ? explorer.closedWhileFiltering : explorer.open;
    const has = set.has(node.path);
    if (has) set.delete(node.path);
    else set.add(node.path);
    drawTree();
  }

  function fillLevels(report) {
    const select = byId("level-filter");
    const chosen = select.value;
    const levels = [];
    const labels = new Map();
    for (const item of listedOf(report)) {
      const level = item.verdict && item.verdict.level;
      if (typeof level === "string" && !levels.includes(level)) {
        levels.push(level);
        labels.set(level, labelOf(item.verdict));
      }
    }
    while (select.options.length > 1) select.remove(1);
    for (const level of levels) {
      const option = make("option", labels.get(level));
      option.value = level;
      select.appendChild(option);
    }
    select.value = levels.includes(chosen) ? chosen : "";
  }

  function clearDetail() {
    explorer.selected = null;
    // A reply to an earlier pick (another flavor folder's file) is dropped.
    explorer.explainSeq += 1;
    byId("explain-title").textContent = "Pick a file or folder";
    byId("explain-path").textContent = "";
    byId("explain-verdict").replaceChildren();
    byId("explain-facts").replaceChildren();
    explainSay("", false);
  }

  function explainSay(text, isError) {
    const node = byId("explain-status");
    node.textContent = text;
    node.classList.toggle("error", Boolean(isError));
  }

  async function loadTree(flavor) {
    const seq = ++explorer.treeSeq;
    explorer.root = null;
    explorer.report = null;
    byId("tree").replaceChildren();
    byId("tree-root").textContent = "";
    clearDetail();
    explorerSay("Reading the flavor folder…", false);
    const params = { explain: true };
    if (flavor) params.flavor = flavor;
    const reply = await runJson(["tree"], params);
    if (seq !== explorer.treeSeq) return;
    if (reply.error) {
      explorerSay(shown(reply.error), true);
      return;
    }
    const report = reply.data;
    if (!report || !Array.isArray(report.items)) {
      explorerSay("wowlab tree printed something this page does not know.", true);
      return;
    }
    explorer.report = report;
    explorer.flavor = report.flavor_folder;
    explorer.root = buildTree(report);
    explorer.open = new Set(FIRST_OPEN);
    explorer.closedWhileFiltering = new Set();
    byId("tree-root").textContent = shown(report.install_root + sep + report.flavor_folder);
    fillLevels(report);
    drawTree();
    const notes = [];
    if (report.truncated) notes.push("The list stopped at its size limit.");
    if (Array.isArray(report.errors) && report.errors.length) {
      notes.push(report.errors.length + " path(s) could not be read.");
    }
    if (notes.length) explorerSay(byId("explorer-status").textContent + " " + notes.join(" "), false);
  }

  function markSelected() {
    for (const row of byId("tree").querySelectorAll(".row")) {
      const on = row.dataset.path === explorer.selected;
      row.classList.toggle("selected", on);
      row.parentElement.setAttribute("aria-selected", on ? "true" : "false");
    }
  }

  function fact(list, label, value) {
    if (typeof value !== "string" || value === "") return;
    list.appendChild(make("dt", label));
    list.appendChild(make("dd", shown(value)));
  }

  async function select(node) {
    const report = explorer.report;
    if (!report) return;
    explorer.selected = node.path;
    markSelected();
    const seq = ++explorer.explainSeq;
    const where = report.flavor_folder + (node.path ? "/" + node.path : "");
    byId("explain-title").textContent = visible(node.name) + (isFolder(node) ? "/" : "");
    byId("explain-path").textContent = visible(where);
    byId("explain-verdict").replaceChildren();
    byId("explain-facts").replaceChildren();
    explainSay("Asking wowlab explain…", false);
    const target = report.install_root + sep + report.flavor_folder + (node.path ? "/" + node.path : "");
    const reply = await runJson(["explain"], { path: target, root: report.install_root, flavor: report.flavor_folder });
    if (seq !== explorer.explainSeq) return;
    if (reply.error) {
      explainSay(shown(reply.error), true);
      return;
    }
    const data = reply.data || {};
    const result = data.result || {};
    const verdict = byId("explain-verdict");
    if (data.verdict) {
      const marks = make("div");
      marks.className = "marks";
      for (const mark of badges(data.verdict, false)) marks.appendChild(mark);
      verdict.appendChild(marks);
      verdict.appendChild(make("p", shown(data.verdict.words)));
    }
    const facts = byId("explain-facts");
    const entry = result.entry;
    // `plain` is the row's text without Markdown; an older wowlab sends none.
    const plain = data.plain || entry || {};
    if (entry) {
      fact(facts, "What it is", plain.what);
      fact(facts, "Who writes it, and when", plain.written_by);
      fact(facts, "File-map row", entry.id);
    } else {
      fact(facts, "Why it is unknown", result.reason);
    }
    explainSay("", false);
  }

  async function loadExplorer() {
    explorerSay("Looking for the install…", false);
    const reply = await runJson(["install", "show"], {});
    if (reply.error) {
      explorerSay(shown(reply.error), true);
      return;
    }
    const flavors = Array.isArray(reply.data.flavors) ? reply.data.flavors : [];
    const picker = byId("flavor");
    picker.replaceChildren();
    for (const flavor of flavors) {
      const option = make("option", visible(flavor.folder) + (flavor.version ? " (" + visible(flavor.version) + ")" : ""));
      option.value = flavor.folder;
      picker.appendChild(option);
    }
    byId("flavor-box").hidden = flavors.length < 2;
    await loadTree(flavors.length ? flavors[0].folder : "");
  }

  byId("flavor").addEventListener("change", () => loadTree(byId("flavor").value));
  byId("level-filter").addEventListener("change", () => {
    explorer.closedWhileFiltering = new Set();
    drawTree();
  });
  byId("text-filter").addEventListener("input", () => {
    explorer.closedWhileFiltering = new Set();
    drawTree();
  });

  // ─── tabs ───

  function showTab(name) {
    for (const which of ["explorer", "commands"]) {
      const on = which === name;
      byId("tab-" + which).setAttribute("aria-selected", on ? "true" : "false");
      byId(which + "-view").hidden = !on;
    }
  }
  byId("tab-explorer").addEventListener("click", () => showTab("explorer"));
  byId("tab-commands").addEventListener("click", () => showTab("commands"));

  // ─── the command list ───

  function field(param) {
    const box = make("div");
    box.className = "field";
    const id = "param-" + param.name;
    const label = make("label", param.label);
    label.htmlFor = id;
    let input;
    if (param.kind === "flag") {
      input = make("input");
      input.type = "checkbox";
    } else if (param.multiple) {
      input = make("textarea");
      input.rows = 3;
      input.placeholder = "one value per line";
    } else {
      input = make("input");
      input.type = "text";
      if (param.required) input.required = true;
    }
    input.id = id;
    box.appendChild(label);
    box.appendChild(input);
    if (param.help) box.appendChild(make("small", param.help));
    inputs.push({ param: param, input: input });
    return box;
  }

  function pick(command, button) {
    current = command;
    inputs = [];
    for (const other of byId("commands").querySelectorAll("button")) {
      other.setAttribute("aria-pressed", "false");
    }
    button.setAttribute("aria-pressed", "true");
    byId("title").textContent = "wowlab " + command.path.join(" ");
    byId("help").textContent = command.help || "";
    const fields = byId("fields");
    fields.replaceChildren();
    for (const param of command.params) {
      if (!param.refused) {
        fields.appendChild(field(param));
      } else {
        const note = make("p", param.label + " is not offered here: " + param.refused);
        note.className = "field-refused";
        fields.appendChild(note);
      }
    }
    byId("run").disabled = false;
    showResult(null);
    byId("stderr").textContent = "";
    say("");
  }

  function collect() {
    const params = {};
    for (const { param, input } of inputs) {
      if (param.kind === "flag") {
        if (input.checked) params[param.name] = true;
      } else if (param.multiple) {
        const values = input.value.split("\n").filter((line) => line !== "");
        if (values.length) params[param.name] = values;
      } else if (input.value !== "") {
        params[param.name] = input.value;
      }
    }
    return params;
  }

  async function run() {
    if (current === null) return;
    const button = byId("run");
    button.disabled = true;
    say("Running wowlab " + current.path.join(" ") + "…");
    showResult(null);
    byId("stderr").textContent = "";
    try {
      const reply = await fetch(api("/api/run"), {
        method: "POST",
        cache: "no-store",
        credentials: "omit",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: current.path, params: collect() }),
      });
      const result = await reply.json();
      if (!reply.ok) {
        say("Refused: " + (result.error || reply.status));
        return;
      }
      showResult(result.stdout);
      byId("stderr").textContent = result.stderr;
      const notes = [];
      if (result.timed_out) notes.push("stopped at its time limit");
      if (result.truncated) notes.push("output cut at its size limit");
      say("Exit code " + result.exit_code + (notes.length ? " (" + notes.join(", ") + ")" : ""));
    } catch (error) {
      say("The request failed: " + error);
    } finally {
      button.disabled = false;
    }
  }

  async function load() {
    let reply;
    try {
      reply = await fetch(api("/api/commands"), { cache: "no-store", credentials: "omit" });
    } catch (error) {
      say("The server could not be reached: " + error);
      explorerSay("The server could not be reached: " + error, true);
      return;
    }
    if (!reply.ok) {
      say("The server refused the request (" + reply.status + ").");
      explorerSay("The server refused the request (" + reply.status + ").", true);
      return;
    }
    const data = await reply.json();
    if (typeof data.home === "string") home = data.home;
    if (typeof data.sep === "string" && data.sep) sep = data.sep;
    const list = byId("commands");
    for (const command of data.commands) {
      const button = make("button", command.path.join(" "));
      button.type = "button";
      button.setAttribute("aria-pressed", "false");
      if (command.excluded) {
        button.disabled = true;
        button.title = "Not offered in this version: " + command.excluded;
      } else {
        button.title = command.help || "";
        button.addEventListener("click", () => pick(command, button));
      }
      list.appendChild(button);
    }
    await loadExplorer();
  }

  // ─── the result: a generic JSON tree (M13-26) ─────────────────────────────
  // One run's stdout, when it is one JSON document, drawn as a collapsible,
  // read-only tree: an object or array is a <details> whose children are
  // drawn the first time it opens, at most JT_BATCH at a time. Every key and
  // value goes in through textContent. The output as printed stays one click
  // away, exact, because JSON.parse rounds integers past 2^53.

  const JT_BATCH = 200;

  function jtKind(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    return typeof value; // "object", "string", "number" or "boolean"
  }

  // Shown escaped, because they could reorder or hide what a key or value
  // says: the bidi marks and controls U+061C, U+200E, U+200F, U+202A-U+202E
  // and U+2066-U+2069; the zero-width characters U+200B-U+200D, U+2060 and
  // U+FEFF; and the separators U+2028 and U+2029. JSON.stringify already
  // escapes control characters and lone surrogates.
  const JT_HIDDEN =
    /[\u061C\u200B-\u200F\u2028-\u202E\u2060\u2066-\u2069\uFEFF]/g;

  function jtQuote(text) {
    return JSON.stringify(text).replace(
      JT_HIDDEN,
      (ch) => "\\u" + ch.charCodeAt(0).toString(16).toUpperCase().padStart(4, "0"),
    );
  }

  // An object's key in JSON form; an array's index (a number) as it is.
  function jtKey(key) {
    const span = make("span", typeof key === "number" ? String(key) : jtQuote(key));
    span.className = "jt-key";
    return span;
  }

  function jtScalar(value) {
    const kind = jtKind(value);
    const span = make("span", kind === "string" ? jtQuote(value) : String(value));
    span.className = "jt-value jt-" + kind;
    return span;
  }

  function jtFill(box, entries, from) {
    const end = Math.min(entries.length, from + JT_BATCH);
    for (let i = from; i < end; i++) {
      box.appendChild(jtNode(entries[i][0], entries[i][1]));
    }
    if (end < entries.length) {
      const more = make("button", "Show more (" + (entries.length - end) + " left)");
      more.type = "button";
      more.className = "jt-more";
      more.addEventListener("click", () => {
        more.remove();
        jtFill(box, entries, end);
      });
      box.appendChild(more);
    }
  }

  function jtNode(key, value) {
    const kind = jtKind(value);
    if (kind !== "object" && kind !== "array") {
      const row = make("div");
      row.className = "jt-leaf";
      if (key !== null) row.appendChild(jtKey(key));
      row.appendChild(jtScalar(value));
      return row;
    }
    const entries =
      kind === "array" ? value.map((item, i) => [i, item]) : Object.entries(value);
    const node = make("details");
    node.className = "jt-node";
    const summary = make("summary");
    if (key !== null) summary.appendChild(jtKey(key));
    const open = kind === "array" ? "[" : "{";
    const close = kind === "array" ? "]" : "}";
    const count = make("span", open + entries.length + close);
    count.className = "jt-count";
    summary.appendChild(count);
    node.appendChild(summary);
    const box = make("div");
    box.className = "jt-children";
    node.appendChild(box);
    let drawn = false;
    node.addEventListener("toggle", () => {
      if (node.open && !drawn) {
        drawn = true;
        jtFill(box, entries, 0);
      }
    });
    return node;
  }

  // Shows one run's stdout: a tree when it is one JSON document, else the
  // text as printed. null clears the result.
  function showResult(stdout) {
    const tree = byId("result-tree");
    const raw = byId("result-raw");
    tree.replaceChildren();
    tree.hidden = true;
    raw.hidden = true;
    raw.open = false;
    byId("stdout").textContent = stdout || "";
    if (!stdout) return;
    let value;
    try {
      value = JSON.parse(stdout);
    } catch (_) {
      raw.hidden = false;
      raw.open = true;
      return;
    }
    const root = jtNode(null, value);
    tree.appendChild(root);
    if (root.tagName === "DETAILS") root.open = true;
    tree.hidden = false;
    raw.hidden = false;
  }

  byId("run").addEventListener("click", run);
  load();
})();
