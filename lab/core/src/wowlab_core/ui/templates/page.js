"use strict";
// The page of `wowlab ui` (ADR-0029, §15.6). Three views:
// - the explorer (M13-25, M13-27): the tree of the whole install from
//   `tree --install --explain --json`, and the detail of one path from
//   `explain --json`: its verdict, its snapshot facts, the hints to copy,
//   and a Preview that runs its first view and draws the JSON;
// - the gaps (M13-27): what `filemap gaps --json` reports;
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
    // A link keeps its row's level class; `badge-link` overrides its colour.
    if (verdict.link === true) node.classList.add("badge-link");
    // A placeholder (a path under a reparse point that is no link) keeps its
    // level class too; `badge-placeholder` paints it like the label it says.
    if (verdict.placeholder === true) node.classList.add("badge-placeholder");
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
    report: null, // the InstallTreeReport
    root: null, // the node of the install root
    flavors: [], // the flavor folders, as discovery reported them
    open: new Set(),
    closedWhileFiltering: new Set(),
    selected: null,
    treeSeq: 0,
    explainSeq: 0,
    gaps: null, // the GapReport, once read
    gapsAsked: null, // the one `filemap gaps` run, shared by the tab and the filter
    gapsError: "", // why the last one failed; the next need asks again
    gapHits: null, // the paths "gaps only" shows, from the install root
    choices: new Map(), // a Verdict choice's value: the filter keys it shows
  };
  // The commands the server offers (`/api/commands`): a Preview is offered
  // only for a view this list allows.
  let offeredCommands = [];

  function explorerSay(text, isError) {
    const node = byId("explorer-status");
    node.textContent = text;
    node.classList.toggle("error", Boolean(isError));
  }

  // A path from the install root, `/`-separated: "" is the install root, a
  // flavor folder its own name, a file in it `<flavor>/<path>`.
  function keyOf(base, flavor, path) {
    const rel = typeof path === "string" ? path : "";
    if (base === "root") return rel;
    const folder = typeof flavor === "string" ? flavor : "";
    return rel ? folder + "/" + rel : folder;
  }

  function itemKey(item) {
    return keyOf(item.base, item.flavor_folder, item.path);
  }

  // The listed paths of the report: its items and the folders above them
  // (`ancestors`), each with its verdict from Python.
  function listedOf(report) {
    const ancestors = Array.isArray(report.ancestors) ? report.ancestors : [];
    return report.items.concat(ancestors);
  }

  // The first view: the install root open, and the first flavor folder with
  // WTF/ and Interface/AddOns/ open (owner, 2026-10-16).
  function firstOpen(flavor) {
    const open = [""];
    if (flavor) {
      for (const under of ["", "/WTF", "/Interface", "/Interface/AddOns"]) open.push(flavor + under);
    }
    return open;
  }

  // The listed paths as a tree of path segments under the install root. A
  // node no listed path names has no item and no badge; `explain` gives its
  // verdict when it is picked. Each node knows its flavor folder (none for
  // the install root's own entries) and its path inside it, for `explain`.
  function buildTree(report) {
    const rootName = String(report.install_root).split(/[\\/]/).filter(Boolean).pop() || "";
    const flavors = new Set(explorer.flavors);
    const root = { name: rootName, path: "", item: null, children: [], index: new Map(), flavor: null, rel: "" };
    const listed = listedOf(report).map((item) => [itemKey(item), item]);
    listed.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    for (const [key, item] of listed) {
      if (typeof key !== "string" || key === "") continue;
      let node = root;
      let path = "";
      for (const part of key.split("/")) {
        path = path ? path + "/" + part : part;
        let child = node.index.get(part);
        if (!child) {
          const top = path.split("/")[0];
          const inFlavor = flavors.has(top);
          child = {
            name: part,
            path: path,
            item: null,
            children: [],
            index: new Map(),
            flavor: inFlavor ? top : null,
            rel: inFlavor ? path.slice(top.length + 1) : path,
          };
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
      gaps: byId("gaps-only").checked === true,
    };
  }

  // What the Verdict filter groups by: the level, except that links (any
  // level) form their own group, as their badge does.
  function filterKey(verdict) {
    if (!verdict) return "";
    if (verdict.link === true) return "link";
    // A placeholder says another level's label (Python's) and is painted
    // as it: its own choice, not hidden under its row's level.
    if (verdict.placeholder === true) return "placeholder";
    return typeof verdict.level === "string" ? verdict.level : "";
  }

  function matches(node, filter) {
    if (filter.level) {
      const keys = explorer.choices.get(filter.level) || new Set([filter.level]);
      if (!node.item || !keys.has(filterKey(node.item.verdict))) return false;
    }
    if (filter.text && !node.path.toLowerCase().includes(filter.text)) return false;
    if (filter.gaps && !(explorer.gapHits && explorer.gapHits.has(node.path))) return false;
    return true;
  }

  // Which nodes are drawn: all of them without a filter; with one, the
  // matches and the folders that lead to them.
  function shownNodes(root, filter) {
    const keep = new Set();
    const active = Boolean(filter.level || filter.text || filter.gaps);
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
    name.title = visible(node.path || node.name);
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
    if (filter.gaps && !explorer.gapHits) {
      if (explorer.gapsAsked) explorerSay("Reading the file-map gaps…", false);
      else explorerSay(explorer.gapsError, Boolean(explorer.gapsError));
      return;
    }
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

  // The Verdict filter's choices, from the data: one per distinct label
  // Python gives, so paths that read the same share a choice (a placeholder
  // reads as `no` does, and is shown with it). This groups on Python's
  // words, not on a rule here. A choice's value is one of its filter keys,
  // the last in sort order; `explorer.choices` maps it to all of them.
  function fillLevels(report) {
    const select = byId("level-filter");
    const chosen = select.value;
    const byLabel = new Map();
    for (const item of listedOf(report)) {
      const key = filterKey(item.verdict);
      if (!key) continue;
      const label = labelOf(item.verdict);
      if (!byLabel.has(label)) byLabel.set(label, new Set());
      byLabel.get(label).add(key);
    }
    explorer.choices = new Map();
    while (select.options.length > 1) select.remove(1);
    for (const [label, keys] of byLabel) {
      const value = [...keys].sort().pop();
      explorer.choices.set(value, keys);
      const option = make("option", label);
      option.value = value;
      select.appendChild(option);
    }
    select.value = explorer.choices.has(chosen) ? chosen : "";
  }

  // With several flavor folders, a picker that opens one of them as the
  // first view opens the first.
  function fillFlavors(flavors) {
    const picker = byId("flavor");
    picker.replaceChildren();
    const none = make("option", "choose one");
    none.value = "";
    picker.appendChild(none);
    for (const folder of flavors) {
      const option = make("option", visible(folder));
      option.value = folder;
      picker.appendChild(option);
    }
    byId("flavor-box").hidden = flavors.length < 2;
  }

  function clearDetail() {
    explorer.selected = null;
    // A reply to an earlier pick is dropped.
    explorer.explainSeq += 1;
    byId("explain-title").textContent = "Pick a file or folder";
    byId("explain-path").textContent = "";
    byId("explain-verdict").replaceChildren();
    byId("explain-facts").replaceChildren();
    byId("explain-snapshot").replaceChildren();
    byId("explain-hints").replaceChildren();
    byId("explain-preview").replaceChildren();
    explainSay("", false);
  }

  function explainSay(text, isError) {
    const node = byId("explain-status");
    node.textContent = text;
    node.classList.toggle("error", Boolean(isError));
  }

  async function loadTree() {
    const seq = ++explorer.treeSeq;
    explorer.root = null;
    explorer.report = null;
    byId("tree").replaceChildren();
    byId("tree-root").textContent = "";
    clearDetail();
    explorerSay("Reading the install…", false);
    const reply = await runJson(["tree"], { whole_install: true, explain: true });
    if (seq !== explorer.treeSeq) return;
    if (reply.error) {
      explorerSay(shown(reply.error), true);
      return;
    }
    const report = reply.data;
    if (!report || !Array.isArray(report.items) || !Array.isArray(report.flavor_folders)) {
      explorerSay("wowlab tree printed something this page does not know.", true);
      return;
    }
    explorer.report = report;
    explorer.flavors = report.flavor_folders.filter((f) => typeof f === "string" && f !== "");
    explorer.root = buildTree(report);
    explorer.open = new Set(firstOpen(explorer.flavors[0]));
    explorer.closedWhileFiltering = new Set();
    byId("tree-root").textContent = shown(report.install_root);
    fillFlavors(explorer.flavors);
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

  // ─── the detail pane: hints and Preview ───

  // A hint: Python's command line, quoted for its own shell, shown with the
  // home folder as "~" and copied exactly as Python wrote it. Nothing here
  // runs it.
  function hintNode(hint) {
    const box = make("div");
    box.className = "hint";
    const line = make("code", shown(hint.command));
    line.className = "hint-command";
    box.appendChild(line);
    // Which shell's quoting it is in: Python's word, and its words for it.
    const label =
      typeof hint.shell_label === "string" && hint.shell_label ? " (" + visible(hint.shell_label) + ")" : "";
    const shell = make("span", "Shell: " + visible(String(hint.shell || "")) + label);
    shell.className = "hint-shell";
    box.appendChild(shell);
    const copy = make("button", "Copy");
    copy.type = "button";
    copy.className = "copy";
    copy.setAttribute("aria-label", "Copy this command");
    const text = hint.command;
    copy.addEventListener("click", async () => {
      let done = false;
      try {
        if (window.navigator && window.navigator.clipboard) {
          await window.navigator.clipboard.writeText(text);
          done = true;
        }
      } catch (_) {
        done = false;
      }
      copy.textContent = done ? "Copied" : "Copy did not work in this browser; use Copy in another browser";
      setTimeout(() => {
        copy.textContent = "Copy";
      }, 2000);
    });
    box.appendChild(copy);
    if (typeof hint.does === "string" && hint.does) {
      const does = make("p", shown(hint.does));
      does.className = "hint-does";
      box.appendChild(does);
    }
    return box;
  }

  function drawHints(hints) {
    const box = byId("explain-hints");
    box.replaceChildren();
    const usable = (Array.isArray(hints) ? hints : []).filter(
      (h) => h && typeof h.command === "string" && h.command !== "",
    );
    if (!usable.length) return;
    box.appendChild(make("h3", "Commands to copy"));
    const note = make("p", "Run them yourself in a terminal; this page never runs them.");
    note.className = "hint-note";
    box.appendChild(note);
    // The line on screen writes the home folder as "~"; Copy gives Python's.
    if (usable.some((h) => shortHome(h.command) !== h.command)) {
      const tilde = make("p", "On screen, ~ stands for your home folder; Copy gives the full line.");
      tilde.className = "hint-note";
      box.appendChild(tilde);
    }
    for (const hint of usable) box.appendChild(hintNode(hint));
  }

  function samePath(a, b) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => x === b[i]);
  }

  // Whether the server offers a view's run, by the command list it sent:
  // the command is listed and not excluded, and every parameter the view
  // sets is one the command declares and does not refuse.
  function offered(view) {
    if (!view || !Array.isArray(view.command) || !view.params || typeof view.params !== "object") return false;
    const command = offeredCommands.find((c) => samePath(c.path, view.command));
    if (!command || command.excluded) return false;
    const params = Array.isArray(command.params) ? command.params : [];
    for (const name of Object.keys(view.params)) {
      const param = params.find((p) => p.name === name);
      if (!param || param.refused) return false;
      if (typeof view.params[name] !== "string") return false;
    }
    return true;
  }

  // The Preview of an entry is its first view or nothing: a first view the
  // server refuses is not replaced by the next one.
  function drawPreview(data, seq) {
    const box = byId("explain-preview");
    box.replaceChildren();
    const views = Array.isArray(data.views) ? data.views : [];
    const view = views[0];
    if (!offered(view)) {
      if (!views.length && typeof data.views_note === "string" && data.views_note) {
        const note = make("p", shown(data.views_note));
        note.className = "preview-note";
        box.appendChild(note);
      }
      return;
    }
    const head = make("div");
    head.className = "preview-head";
    const button = make("button", "Preview");
    button.type = "button";
    button.className = "preview";
    head.appendChild(button);
    const what = make("span", "wowlab " + visible(view.command.join(" ")) + ": " + shown(String(view.does || "")));
    what.className = "preview-does";
    head.appendChild(what);
    box.appendChild(head);
    const out = make("div");
    out.className = "preview-out";
    box.appendChild(out);
    const command = view.command.slice();
    const params = Object.assign({}, view.params);
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      button.disabled = true;
      out.replaceChildren(make("p", "Running wowlab " + visible(command.join(" ")) + "…"));
      const reply = await runJson(command, params);
      if (seq !== explorer.explainSeq) return;
      button.disabled = false;
      out.replaceChildren();
      if (reply.error) {
        const error = make("p", shown(reply.error));
        error.className = "preview-error";
        out.appendChild(error);
        return;
      }
      out.appendChild(previewTree(reply.data));
    });
  }

  async function select(node) {
    const report = explorer.report;
    if (!report) return;
    explorer.selected = node.path;
    markSelected();
    const seq = ++explorer.explainSeq;
    byId("explain-title").textContent = visible(node.name) + (isFolder(node) ? "/" : "");
    byId("explain-path").textContent = visible(node.path);
    byId("explain-verdict").replaceChildren();
    byId("explain-facts").replaceChildren();
    byId("explain-snapshot").replaceChildren();
    byId("explain-hints").replaceChildren();
    byId("explain-preview").replaceChildren();
    explainSay("Asking wowlab explain…", false);
    const target = node.path ? report.install_root + sep + node.path.split("/").join(sep) : report.install_root;
    const params = { path: target, root: report.install_root };
    if (node.flavor) params.flavor = node.flavor;
    const reply = await runJson(["explain"], params);
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
    // The snapshot facts, in Python's words (`snapshot.words`), verbatim.
    const words = data.snapshot ? data.snapshot.words : null;
    if (typeof words === "string" && words) {
      const snap = byId("explain-snapshot");
      const label = make("span", "Snapshots");
      label.className = "snapshot-label";
      snap.appendChild(label);
      snap.appendChild(make("span", shown(words)));
    }
    drawHints(data.hints);
    drawPreview(data, seq);
    explainSay("", false);
  }

  byId("flavor").addEventListener("change", () => {
    const folder = byId("flavor").value;
    if (!explorer.root || !explorer.flavors.includes(folder)) return;
    for (const path of firstOpen(folder)) explorer.open.add(path);
    // Back to the blank choice, so picking the same folder again reopens it.
    byId("flavor").value = "";
    drawTree();
  });
  byId("level-filter").addEventListener("change", () => {
    explorer.closedWhileFiltering = new Set();
    drawTree();
  });
  byId("text-filter").addEventListener("input", () => {
    explorer.closedWhileFiltering = new Set();
    drawTree();
  });
  byId("gaps-only").addEventListener("change", async () => {
    explorer.closedWhileFiltering = new Set();
    const pending = byId("gaps-only").checked && !explorer.gaps ? loadGaps() : null;
    drawTree();
    if (pending) {
      await pending;
      drawTree();
    }
  });

  // ─── the preview: the JSON a view printed, as a read-only tree ───
  // Drawn at once, with no listener, up to PREVIEW_NODES keys and values;
  // an object or array is a <details> the player opens. Every key and value
  // goes in through textContent, quoted as JSON.

  const PREVIEW_NODES = 3000;

  function previewTree(value) {
    const box = make("div");
    box.className = "jt preview-tree";
    const budget = { left: PREVIEW_NODES, skipped: 0 };
    const root = previewNode(null, value, budget);
    const kind = jtKind(value);
    if (kind === "object" || kind === "array") root.open = true;
    box.appendChild(root);
    if (budget.skipped) {
      const more = make("p", budget.skipped + " more keys and values are not drawn here; the Commands tab shows the whole output.");
      more.className = "jt-count";
      box.appendChild(more);
    }
    return box;
  }

  // How many keys and values `value` holds, itself included: what a cut
  // leaves out.
  function previewCount(value) {
    const kind = jtKind(value);
    if (kind !== "object" && kind !== "array") return 1;
    let n = 1;
    for (const child of kind === "array" ? value : Object.values(value)) n += previewCount(child);
    return n;
  }

  function previewNode(key, value, budget) {
    budget.left -= 1;
    const kind = jtKind(value);
    if (kind !== "object" && kind !== "array") {
      const row = make("div");
      row.className = "jt-leaf";
      if (key !== null) row.appendChild(jtKey(key));
      // A path in a value shows the home folder as "~", as the pane does.
      row.appendChild(jtScalar(kind === "string" ? shortHome(value) : value));
      return row;
    }
    const entries = kind === "array" ? value.map((item, i) => [i, item]) : Object.entries(value);
    const node = make("details");
    node.className = "jt-node";
    const summary = make("summary");
    if (key !== null) summary.appendChild(jtKey(key));
    const count = make("span", (kind === "array" ? "[" : "{") + entries.length + (kind === "array" ? "]" : "}"));
    count.className = "jt-count";
    summary.appendChild(count);
    node.appendChild(summary);
    const box = make("div");
    box.className = "jt-children";
    node.appendChild(box);
    for (let i = 0; i < entries.length; i++) {
      if (budget.left <= 0) {
        for (let j = i; j < entries.length; j++) budget.skipped += previewCount(entries[j][1]);
        break;
      }
      box.appendChild(previewNode(entries[i][0], entries[i][1], budget));
    }
    return node;
  }

  // ─── the gaps tab: `filemap gaps --json` ───

  // The one `filemap gaps` run, read when the tab or "gaps only" first
  // needs it.
  function loadGaps() {
    if (!explorer.gapsAsked) {
      explorer.gapsAsked = (async () => {
        explorer.gapsError = "";
        gapsSay("Looking for paths the file map does not describe…", false);
        const reply = await runJson(["filemap", "gaps"], {});
        const report = reply.data;
        if (reply.error || !report || !Array.isArray(report.groups)) {
          explorer.gapsAsked = null;
          explorer.gapsError = reply.error
            ? shown(reply.error)
            : "wowlab filemap gaps printed something this page does not know.";
          gapsSay(explorer.gapsError, true);
          return;
        }
        explorer.gaps = report;
        explorer.gapHits = gapHits(report);
        drawGaps(report);
      })();
    }
    return explorer.gapsAsked;
  }

  function gapsSay(text, isError) {
    const node = byId("gaps-status");
    node.textContent = text;
    node.classList.toggle("error", Boolean(isError));
  }

  function listOf(value) {
    return Array.isArray(value) ? value : [];
  }

  // What "gaps only" shows: the places in `groups` and `catch_all`, and the
  // links, by their path from the install root (with their ancestors, as
  // every filter draws them).
  function gapHits(report) {
    const hits = new Set();
    for (const group of listOf(report.groups).concat(listOf(report.catch_all))) {
      for (const place of Array.isArray(group.gaps) ? group.gaps : []) {
        hits.add(keyOf(group.base, place.flavor_folder, place.path));
      }
    }
    for (const link of listOf(report.links)) {
      if (typeof link === "string") hits.add(link);
    }
    return hits;
  }

  // A count with a comma every three digits, as the CLI writes it
  // (`f"{n:,}"`), whatever the browser's locale.
  function grouped(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // "1 file", "1,024 bytes": `word` with its leading space.
  function plural(n, word) {
    return grouped(n) + word + (n === 1 ? "" : "s");
  }

  // A group's counts, from its numbers; no path in them.
  function groupCounts(group) {
    const parts = [];
    if (group.kind === "file") {
      parts.push(plural(group.bytes, " byte"));
    } else if (group.kind === "link") {
      parts.push("link, not followed");
    } else if (group.kind === "folder") {
      parts.push(plural(group.files, " file"), plural(group.bytes, " byte"));
      if (group.folders) parts.push(plural(group.folders, " folder"));
      if (group.links) parts.push(plural(group.links, " link") + " not followed");
      if (group.other) parts.push(grouped(group.other) + " other");
    } else {
      parts.push("not a file, folder or link; never opened");
    }
    if (group.places > 1) parts.push("in " + grouped(group.places) + " places");
    if (group.entry_id) parts.push("row " + visible(group.entry_id));
    if (group.note) parts.push(visible(group.note));
    return parts.join(", ");
  }

  function placeCounts(place) {
    const parts = [];
    if (place.files) parts.push(plural(place.files, " file"));
    parts.push(plural(place.bytes, " byte"));
    if (place.folders) parts.push(plural(place.folders, " folder"));
    if (place.links) parts.push(plural(place.links, " link") + " not followed");
    if (place.other) parts.push(grouped(place.other) + " other");
    return parts.join(", ");
  }

  // One group: its header names the parent folder's row and the place's
  // own name, never the parent's path (an account, realm or character
  // folder), as `filemap gaps` prints it. Its places are inside.
  function gapGroup(group, catchAll) {
    const box = make("details");
    box.className = catchAll ? "gap-group gap-catch-all" : "gap-group";
    const head = make("summary");
    head.className = "gap-head";
    const parent = group.parent_entry_id ? "[" + visible(group.parent_entry_id) + "]" : "(install root)";
    const slash = group.kind === "folder" ? "/" : "";
    head.textContent = parent + " " + visible(group.name) + slash + "  " + groupCounts(group);
    box.appendChild(head);
    const list = make("ul");
    list.className = "gap-places";
    for (const place of Array.isArray(group.gaps) ? group.gaps : []) {
      const key = keyOf(group.base, place.flavor_folder, place.path);
      const item = make("li");
      item.className = "gap-place";
      item.dataset.path = key;
      const where = make("code", visible(key));
      item.appendChild(where);
      item.appendChild(make("span", "  " + placeCounts(place)));
      const examples = Array.isArray(place.examples) ? place.examples : [];
      if (examples.length) {
        const inside = make("ul");
        inside.className = "gap-examples";
        for (const example of examples) inside.appendChild(make("li", visible(String(example))));
        item.appendChild(inside);
      }
      list.appendChild(item);
    }
    box.appendChild(list);
    return box;
  }

  function gapSection(body, title, intro) {
    const section = make("section");
    section.className = "gap-section panel";
    section.appendChild(make("h3", title));
    if (intro) {
      const p = make("p", intro);
      p.className = "gap-intro";
      section.appendChild(p);
    }
    body.appendChild(section);
    return section;
  }

  function drawGaps(report) {
    const body = byId("gaps-body");
    body.replaceChildren();
    const groups = listOf(report.groups);
    const catchAll = listOf(report.catch_all);
    const notEntered = listOf(report.not_entered);
    const links = listOf(report.links);
    const errors = listOf(report.errors);
    const notes = [];
    if (report.truncated) notes.push("The walk stopped at a bound: there may be thin spots it did not reach.");
    if (errors.length) notes.push(plural(errors.length, " path") + " could not be read (listed below).");
    gapsSay(notes.join(" "), false);

    // With no groups, the links the walk did not follow are said, as
    // `filemap gaps` says them: what they point at was not checked.
    let noneText = "None: every path the walk reached has a row";
    if (links.length) {
      noneText +=
        "; " + plural(links.length, " link") + " met and not followed (listed below), " +
        "so what they point at, a linked WTF/ for one, was not checked";
    }
    const none = gapSection(body, "Paths with no file-map row",
      groups.length
        ? plural(report.files, " file") + ", " + plural(report.bytes, " byte") + ", in " + plural(groups.length, " group") + "."
        : noneText + ".");
    for (const group of groups) none.appendChild(gapGroup(group, false));
    if (catchAll.length) {
      const only = gapSection(body, "Described only by a catch-all row",
        plural(report.catch_all_files, " file") + ", " + plural(report.catch_all_bytes, " byte") + ".");
      for (const group of catchAll) only.appendChild(gapGroup(group, true));
    }
    if (notEntered.length) {
      const section = gapSection(body, "Not entered", "Folders a row labels whole; the walk did not look inside.");
      // By row, as `filemap gaps` prints them: one folder on its line, more
      // as a count that opens to the list.
      const byRow = new Map();
      for (const item of notEntered) {
        const row = String(item.entry_id || "");
        if (!byRow.has(row)) byRow.set(row, []);
        byRow.get(row).push(item);
      }
      const list = make("ul");
      const folderLine = (item, prefix) => {
        const key = keyOf(item.base, item.flavor_folder, item.path);
        const li = make("li");
        li.className = "gap-not-entered";
        li.dataset.path = key;
        if (prefix) li.appendChild(make("span", prefix));
        li.appendChild(make("code", visible(key) + "/"));
        return li;
      };
      for (const [row, items] of byRow) {
        const tag = "[" + visible(row) + "] ";
        if (items.length === 1) {
          list.appendChild(folderLine(items[0], tag));
          continue;
        }
        const li = make("li");
        const box = make("details");
        box.appendChild(make("summary", tag + plural(items.length, " folder")));
        const inner = make("ul");
        for (const item of items) inner.appendChild(folderLine(item, ""));
        box.appendChild(inner);
        li.appendChild(box);
        list.appendChild(li);
      }
      section.appendChild(list);
    }
    if (links.length) {
      const section = gapSection(body, "Links met and not followed", "What they point at was not checked.");
      const list = make("ul");
      for (const link of links) {
        if (typeof link !== "string") continue;
        const li = make("li");
        li.className = "gap-link";
        li.dataset.path = link;
        li.appendChild(make("code", visible(link)));
        list.appendChild(li);
      }
      section.appendChild(list);
    }
    if (errors.length) {
      const section = gapSection(body, "Could not be read", null);
      const list = make("ul");
      for (const error of errors) {
        list.appendChild(make("li", visible(String(error.path)) + ": " + shown(String(error.error))));
      }
      section.appendChild(list);
    }
  }

  // ─── tabs ───

  function showTab(name) {
    for (const which of ["explorer", "gaps", "commands"]) {
      const on = which === name;
      byId("tab-" + which).setAttribute("aria-selected", on ? "true" : "false");
      byId(which + "-view").hidden = !on;
    }
    if (name === "gaps") loadGaps();
  }
  byId("tab-explorer").addEventListener("click", () => showTab("explorer"));
  byId("tab-gaps").addEventListener("click", () => showTab("gaps"));
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
    offeredCommands = Array.isArray(data.commands) ? data.commands : [];
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
    await loadTree();
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

  // Shown escaped as \uXXXX, because they could reorder, hide or break what
  // a key or value says: the class `visible` escapes, every control (C0,
  // DEL, C1), format character (bidi marks and overrides, zero-width
  // characters, soft hyphen, invisible operators, interlinear annotation,
  // tag characters) and line or paragraph separator. A character past
  // U+FFFF is written as its surrogate pair, as JSON spells it.
  // JSON.stringify already escapes C0 controls and lone surrogates.
  const JT_HIDDEN = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu;

  function jtEscape(ch) {
    let out = "";
    for (let i = 0; i < ch.length; i++) {
      out += "\\u" + ch.charCodeAt(i).toString(16).toUpperCase().padStart(4, "0");
    }
    return out;
  }

  function jtQuote(text) {
    return JSON.stringify(text).replace(JT_HIDDEN, jtEscape);
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
