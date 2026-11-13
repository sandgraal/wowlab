"use strict";
// The page of `wowlab ui` (ADR-0029, §15.6). Five views:
// - the explorer (M13-25, M13-27): the tree of the whole install from
//   `tree --install --explain --json`, and the detail of one path from
//   `explain --json`: its verdict, its snapshot facts, the hints to copy,
//   and a Preview that runs its first view and draws the JSON;
// - the gaps (M13-27): what `filemap gaps --json` reports;
// - the commands the server read from the CLI's Click tree, a field for each
//   declared parameter, and what one run printed. A command the server marks
//   `confirm` (M14-02, M14-03) runs first as its plan; the server alone
//   decides whether the plan may be confirmed, by handing back a one-time
//   token, and Confirm sends that token with the plan's own command and
//   parameters; a confirmed run shows the server's note (its journal record
//   and the `wowlab undo` hint) and never another Confirm. The plan's lines,
//   Confirm's words and how each run ended (its exit code in words) are the
//   server's too (M16-21): the page puts the plan above Confirm, keeps the
//   report behind a closed "Details", and moves focus to the plan's heading,
//   then after Confirm to the sentence that says how it ended;
// - the character (M14-08, M14-09): a picker from `char list --json`, and
//   for the one picked its gear from `char gear --json`, its keys from
//   `char binds --json` and its macros from `char macros --json`;
// - the looks page (M14-04): `looks page --json`, then the file it wrote,
//   read from /api/looks-page and shown in a sandboxed frame, as its document.
// It holds no game rules and no file-map logic (§14.5 D3): every fact, the
// verdict's level and words included, comes from the CLI's JSON, and this
// script only lays it out. Nothing read from a run is ever parsed as HTML:
// text goes in through textContent only. The home folder is shown as "~".
(function () {
  // The session key (M16-113). The page served for the link `wowlab ui`
  // opened holds it in one <meta> element, which is read and removed here;
  // it is kept in localStorage, which belongs to this origin alone (this
  // server's port included), so a reload and a second tab, which get the
  // page with no key in it, send it too. It is never read from the URL. The
  // link's query leaves the address bar, and so the history, before the
  // first request. Every request sends the key in a header, never a cookie.
  const STORE = "wowlab-session";
  let session = "";
  const given = document.getElementById("wowlab-session");
  if (given) {
    session = given.getAttribute("content") || "";
    given.remove();
  }
  try {
    if (session) window.localStorage.setItem(STORE, session);
    else session = window.localStorage.getItem(STORE) || "";
  } catch (error) {
    // No storage (a locked-down browser): this tab keeps the key it was given.
  }
  if (window.location.search && window.history && typeof window.history.replaceState === "function") {
    window.history.replaceState(null, "", "/");
  }
  const api = (path) => path;
  const keyed = (headers) => Object.assign({ "X-Wowlab-Session": session }, headers || {});
  const NO_SESSION =
    "This page needs the link wowlab ui opened. Open that link again, or, if wowlab ui was stopped, start it again.";
  const byId = (id) => document.getElementById(id);
  // The one outside origin the page may link to (M15-02; ADR-0019's and
  // ADR-0031's amendments of 2026-10-19): the dressing room `char gear --json`
  // builds an address for, as `dressing_sites.toml` names it. The page never
  // requests it; a link to it opens in the player's browser on a click.
  const DRESSING_ORIGIN = "https://www.wowhead.com";
  let current = null;
  let inputs = [];
  // The plan the server offered Confirm for: its command, the parameters it
  // ran with (sent back as they were, never read from the form again), and
  // its token. Cleared before any other run, and once used.
  let pending = null;
  // Bumped by every pick and run, so a reply that arrives late draws nothing.
  let runSeq = 0;
  let home = "";
  let sep = "/";

  function make(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // A status line holds one short sentence, never a result. A refusal or an
  // error is an alert; anything else is a status (EV-27).
  function announce(node, text, isAlert) {
    node.setAttribute("role", isAlert ? "alert" : "status");
    node.textContent = text;
  }

  function say(text, isAlert) {
    announce(byId("status"), text, Boolean(isAlert));
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
  // {error} with what to tell the player. With `partial`, a run that ended
  // with an error and still printed its JSON report gives both (`char list`
  // lists the characters it could read and exits 1 for the others). With
  // `everyFlavor`, the header's flavor is not added: the run reads every
  // flavor folder (the Account tab's `char list --summary`, M17-01).
  async function runJson(command, params, partial, everyFlavor) {
    let reply;
    let result;
    try {
      reply = await fetch(api("/api/run"), {
        method: "POST",
        cache: "no-store",
        credentials: "omit",
        headers: keyed({ "Content-Type": "application/json" }),
        body: JSON.stringify({ command: command, params: everyFlavor ? params || {} : withFlavor(command, params) }),
      });
      result = await reply.json();
    } catch (error) {
      return { error: "The request failed: " + error };
    }
    if (!reply.ok) return { error: "The server refused this request: " + (result.error || reply.status) };
    if (result.timed_out) return { error: "wowlab " + command.join(" ") + " stopped at its time limit." };
    if (result.truncated) return { error: "wowlab " + command.join(" ") + " printed more than the page can hold." };
    if (result.exit_code !== 0) {
      const text = String(result.stderr || "").trim();
      const ended = result.outcome && typeof result.outcome.text === "string" ? result.outcome.text : "";
      const error = text || "wowlab " + command.join(" ") + (ended ? ": " + ended : " ended without a message.");
      if (partial) {
        try {
          const data = JSON.parse(result.stdout);
          if (data && typeof data === "object") return { data: data, error: error };
        } catch (_) {
          // no report: the error alone
        }
      }
      return { error: error };
    }
    try {
      // A run that succeeded may still say something on stderr (a caveat).
      return { data: JSON.parse(result.stdout), note: String(result.stderr || "").trim() };
    } catch (_) {
      return { error: "wowlab " + command.join(" ") + " did not print JSON." };
    }
  }

  // ─── the flavor every run uses (M16-114) ───

  // With several flavor folders, the header's picker names the flavor every
  // run passes as --flavor: each command that declares --flavor gets it,
  // unless its own field names one or it reads every flavor folder already
  // (`tree --install`, `doctor --redact` or `--redact-name`). The picker
  // starts on the only flavor that has characters, else on the first whose
  // .build.info row has a version, else on the first, as `char list --json`
  // reports them (on the one it listed, when $WOWLAB_FLAVOR named one), and
  // keeps the choice while the page is open. With one flavor folder it is
  // not shown and no run gets --flavor from it.
  const runFlavor = {
    picked: "", // the flavor folder picked; "" with one flavor folder or before the start
    settle: null,
    ready: null, // settled once the start is known; the Character tab waits for it
    filled: false,
  };
  runFlavor.ready = new Promise((resolve) => {
    runFlavor.settle = resolve;
  });
  // A run with one of these set (a flag, or a list with a value) reads every
  // flavor folder and refuses --flavor: `tree --install`, `doctor --redact`
  // and `doctor --redact-name`, which implies `--redact`.
  const EVERY_FLAVOR = ["whole_install", "redact", "redact_name"];
  // The tab shown (`showTab`): a new flavor rereads the Character tab only
  // while it is shown.
  let tabShown = "account";

  function takesFlavor(path) {
    const key = JSON.stringify(path);
    const command = offeredCommands.find((c) => c && JSON.stringify(c.path) === key);
    return (
      Boolean(command) && Array.isArray(command.params) &&
      command.params.some((p) => p && p.name === "flavor" && !p.refused)
    );
  }

  // The parameters of one run, with the picked flavor added where it goes.
  function withFlavor(path, params) {
    const given = params || {};
    if (!runFlavor.picked || !takesFlavor(path)) return given;
    if (typeof given.flavor === "string" && given.flavor !== "") return given;
    const set = (value) => value === true || (Array.isArray(value) && value.length > 0);
    if (EVERY_FLAVOR.some((name) => set(given[name]))) return given;
    return Object.assign({}, given, { flavor: runFlavor.picked });
  }

  function startFlavor(flavors, report) {
    if (report && typeof report.flavor_folder === "string" && flavors.includes(report.flavor_folder)) {
      return report.flavor_folder;
    }
    const seen = report && Array.isArray(report.flavors)
      ? report.flavors.filter((f) => f && typeof f.folder === "string" && flavors.includes(f.folder))
      : [];
    const having = seen.filter((f) => typeof f.characters === "number" && f.characters > 0);
    // A place in another flavor that `char list` could not look inside may
    // hold characters too: then no flavor is "the only one with characters".
    const blind = report && Array.isArray(report.not_looked_at)
      ? report.not_looked_at.some((n) => n && n.flavor !== (having[0] && having[0].folder))
      : false;
    if (having.length === 1 && !blind) return having[0].folder;
    const versioned = flavors.find((folder) =>
      seen.some((f) => f.folder === folder && typeof f.version === "string" && f.version !== ""));
    return versioned || flavors[0];
  }

  async function fillRunFlavors(flavors) {
    if (runFlavor.filled) return;
    runFlavor.filled = true;
    const box = byId("run-flavor-box");
    if (flavors.length < 2) {
      box.hidden = true;
      runFlavor.settle();
      return;
    }
    // Asked before any flavor is picked: every flavor's characters.
    const reply = await runJson(["char", "list"], { brief: true }, true);
    const start = startFlavor(flavors, reply.data);
    const picker = byId("run-flavor");
    picker.replaceChildren();
    for (const folder of flavors) {
      const option = make("option", visible(folder));
      option.value = folder;
      picker.appendChild(option);
    }
    picker.value = start;
    runFlavor.picked = start;
    box.hidden = false;
    runFlavor.settle();
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
    announce(node, text, Boolean(isError));
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

  function drawNode(node, view) {
    // Plain nested lists of buttons (EV-27): no tree role, whose arrow keys
    // the page does not offer. A folder's twisty carries aria-expanded.
    const li = make("li");
    const row = make("div");
    row.className = "row";
    row.dataset.path = node.path;
    if (explorer.selected === node.path) row.classList.add("selected");
    const children = node.children.filter((child) => view.keep.has(child));
    const open = isOpen(node, view.active);
    if (children.length) {
      const twisty = make("button", open ? "▾" : "▸");
      twisty.type = "button";
      twisty.className = "twisty";
      twisty.setAttribute("aria-expanded", open ? "true" : "false");
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
      for (const child of children) list.appendChild(drawNode(child, view));
      li.appendChild(list);
    }
    return li;
  }

  // Redraws the tree. A button of the tree that had focus is replaced by
  // the redraw, so focus goes back to the same button (twisty or name) of
  // the row with the same data-path, or to that row's name when its twisty
  // is gone (EV-27).
  function drawTree() {
    const tree = byId("tree");
    const had = document.activeElement;
    let keep = null;
    if (had && had !== tree && tree.contains(had)) {
      const row = had.closest(".row");
      if (row) keep = { path: row.dataset.path, twisty: had.classList.contains("twisty") };
    }
    drawRows(tree);
    if (keep === null) return;
    for (const row of tree.querySelectorAll(".row")) {
      if (row.dataset.path !== keep.path) continue;
      const again = (keep.twisty && row.querySelector(".twisty")) || row.querySelector(".name");
      if (again) again.focus();
      return;
    }
  }

  function drawRows(tree) {
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
    tree.appendChild(drawNode(explorer.root, view));
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
    announce(node, text, Boolean(isError));
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
      runFlavor.settle();
      return;
    }
    const report = reply.data;
    if (!report || !Array.isArray(report.items) || !Array.isArray(report.flavor_folders)) {
      explorerSay("wowlab tree printed something this page does not know.", true);
      runFlavor.settle();
      return;
    }
    explorer.report = report;
    explorer.flavors = report.flavor_folders.filter((f) => typeof f === "string" && f !== "");
    explorer.root = buildTree(report);
    explorer.open = new Set(firstOpen(explorer.flavors[0]));
    explorer.closedWhileFiltering = new Set();
    byId("tree-root").textContent = shown(report.install_root);
    fillFlavors(explorer.flavors);
    fillRunFlavors(explorer.flavors);
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
    box.appendChild(copyButton(hint.command, "Copy this command"));
    if (typeof hint.does === "string" && hint.does) {
      const does = make("p", shown(hint.does));
      does.className = "hint-does";
      box.appendChild(does);
    }
    return box;
  }

  // A Copy button that puts `text` on the clipboard exactly as given.
  function copyButton(text, label) {
    const copy = make("button", "Copy");
    copy.type = "button";
    copy.className = "copy";
    copy.setAttribute("aria-label", label);
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
      const words = done ? "Copied" : "Copy did not work in this browser; use Copy in another browser";
      copy.textContent = words;
      // Every Copy reports to one shared status line, which keeps the words
      // until the next Copy (EV-27). It is emptied first, so the same words
      // twice are announced twice. Only "Copied" on the button goes back.
      const status = byId("copy-status");
      status.textContent = "";
      setTimeout(() => {
        status.textContent = words;
      }, 50);
      if (done) {
        setTimeout(() => {
          if (copy.textContent === words) copy.textContent = "Copy";
        }, 2000);
      }
    });
    return copy;
  }

  function drawHints(hints) {
    const box = byId("explain-hints");
    box.replaceChildren();
    const usable = (Array.isArray(hints) ? hints : []).filter(
      (h) => h && typeof h.command === "string" && h.command !== "",
    );
    if (!usable.length) return;
    box.appendChild(make("h3", "Commands to copy"));
    const note = make(
      "p",
      "Run them yourself in a terminal. Reading never changes anything. Six commands can change game files; each shows a plan and runs only when you confirm, after a snapshot.",
    );
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
    // A command of the confirm step runs from the Commands tab only.
    if (!command || command.excluded || command.confirm) return false;
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
    // Focus moves to what was picked, so the details are the next Tab stops
    // rather than the rest of the tree (EV-27).
    byId("explain-title").focus();
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
    // Partial: `explain` exits 1 for a path that matches nothing (M16-18),
    // and its report still says why. Any other error prints an error report
    // (no `result`) in place of it: the pane shows the error then.
    const reply = await runJson(["explain"], params, true);
    if (seq !== explorer.explainSeq) return;
    if (reply.error && !(reply.data && reply.data.result)) {
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
    // Return to the blank choice, so picking the same folder again reopens it.
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
    announce(node, text, Boolean(isError));
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
  function pluralWord(n, word) {
    return grouped(n) + word + (n === 1 ? "" : "s");
  }

  // A group's counts, from its numbers; no path in them.
  function groupCounts(group) {
    const parts = [];
    if (group.kind === "file") {
      parts.push(pluralWord(group.bytes, " byte"));
    } else if (group.kind === "link") {
      parts.push("link, not followed");
    } else if (group.kind === "folder") {
      parts.push(pluralWord(group.files, " file"), pluralWord(group.bytes, " byte"));
      if (group.folders) parts.push(pluralWord(group.folders, " folder"));
      if (group.links) parts.push(pluralWord(group.links, " link") + " not followed");
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
    if (place.files) parts.push(pluralWord(place.files, " file"));
    parts.push(pluralWord(place.bytes, " byte"));
    if (place.folders) parts.push(pluralWord(place.folders, " folder"));
    if (place.links) parts.push(pluralWord(place.links, " link") + " not followed");
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
    if (errors.length) notes.push(pluralWord(errors.length, " path") + " could not be read (listed below).");
    gapsSay(notes.join(" "), false);

    // With no groups, the links the walk did not follow are said, as
    // `filemap gaps` says them: what they point at was not checked.
    let noneText = "None: every path the walk reached has a row";
    if (links.length) {
      noneText +=
        "; " + pluralWord(links.length, " link") + " met and not followed (listed below), " +
        "so what they point at, a linked WTF/ for one, was not checked";
    }
    const none = gapSection(body, "Paths with no file-map row",
      groups.length
        ? pluralWord(report.files, " file") + ", " + pluralWord(report.bytes, " byte") + ", in " + pluralWord(groups.length, " group") + "."
        : noneText + ".");
    for (const group of groups) none.appendChild(gapGroup(group, false));
    if (catchAll.length) {
      const only = gapSection(body, "Described only by a catch-all row",
        pluralWord(report.catch_all_files, " file") + ", " + pluralWord(report.catch_all_bytes, " byte") + ".");
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
        box.appendChild(make("summary", tag + pluralWord(items.length, " folder")));
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

  // ─── the character tab (M14-08, §16.3) ───
  // A picker from `char list --json --brief` (no records: the picker needs
  // none), latest-written first, and for the
  // character picked, a header and its gear from `char gear --json`. The
  // header's class, race and spec names are the report's `names`, read by
  // the CLI from game tables already cached for the client; this script
  // holds none, and shows the id when a name is missing. Every
  // name and colour comes from the report: a slot's name is its `slot_name`,
  // an item's is its `name`, and its colour is a class built from its
  // `colour` code (`colourClass`) that the server's stylesheet paints from
  // the slot table. This script holds no slot table and no quality table.
  // The keys and macros panels (M14-09) lay out `char binds --json` and
  // `char macros --json` for the same character: every word about a key or
  // a macro (what a key does, whether the sources agree, where a macro may
  // sit, a finding) is the report's, and the two filters read the report's
  // own fields (`disagrees`, `findings`). The history panel (M17-03) lays
  // out `char history --json`. Those five read commands are the only ones
  // this tab runs, and nothing in it edits anything.

  const character = {
    asked: null, // the one `char list` run; asked again after a failure
    entries: [], // its characters, latest-written first
    flavor: null, // the flavor folder it read
    // The character the Account tab opened (M17-01): its account, realm or
    // digits folder and folder name, picked once the list is read.
    want: null,
    seq: 0, // bumped by every pick, so a reply that arrives late draws nothing
    binds: null, // the picked character's CharBindsReport, for the keys filters
    macros: null, // its CharMacrosReport, for the findings filter
    // What `char list` said on stderr when it exited with an error (a row it
    // could not read, a place it could not look inside): the status line
    // keeps it while characters are read.
    note: "",
  };

  function charSay(text, isError) {
    const node = byId("char-status");
    announce(node, text, Boolean(isError));
    node.classList.toggle("error", Boolean(isError));
  }

  function charNote() {
    charSay(character.note, Boolean(character.note));
  }

  // A file time (`mtime_ns`) in the browser's local time, written as the
  // CLI's text writes it, without the offset.
  function localTime(ns) {
    if (typeof ns !== "number" || !Number.isFinite(ns)) return "time unknown";
    const when = new Date(Math.floor(ns / 1e6));
    if (Number.isNaN(when.getTime())) return "time unknown";
    const two = (n) => String(n).padStart(2, "0");
    return (
      when.getFullYear() + "-" + two(when.getMonth() + 1) + "-" + two(when.getDate()) + " " +
      two(when.getHours()) + ":" + two(when.getMinutes()) + ":" + two(when.getSeconds())
    );
  }

  // A character folder with no WowLab.lua (`no_lab_record`, M16-35): listed,
  // never picked, so it gets no record panels.
  function bare(entry) {
    return Boolean(entry) && entry.no_lab_record !== null && entry.no_lab_record !== undefined;
  }

  // Whether `entry` is the character the Account tab asked to open.
  // The flavor counts when both name one: the same folders in another
  // flavor are another character.
  function isWanted(entry, want) {
    const asked = want === undefined ? character.want : want;
    if (!asked) return false;
    if (asked.flavor && typeof entry.flavor === "string" && entry.flavor && entry.flavor !== asked.flavor) {
      return false;
    }
    return entry.account === asked.account && entry.realm_folder === asked.realm_folder && entry.label === asked.label;
  }

  // A row `char gear` can be asked about: read, with its folders named.
  function readable(entry) {
    return (
      Boolean(entry) && !bare(entry) && (entry.error === null || entry.error === undefined) &&
      typeof entry.label === "string" && typeof entry.realm_folder === "string" && typeof entry.account === "string"
    );
  }

  // Latest-written first; rows with no time last; a tie in time by the
  // newest record schema (`record_schema`; rows without one after it), then
  // by account and folder.
  function latestFirst(entries) {
    const num = (v) => (typeof v === "number" ? v : null);
    const path = (e) => String(e.account) + "/" + String(e.realm_folder) + "/" + String(e.label);
    const newestFirst = (x, y) => {
      if (x === y) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return y - x;
    };
    return entries.slice().sort((a, b) => {
      const byTime = newestFirst(num(a.mtime_ns), num(b.mtime_ns));
      if (byTime) return byTime;
      const bySchema = newestFirst(num(a.record_schema), num(b.record_schema));
      if (bySchema) return bySchema;
      return path(a) < path(b) ? -1 : path(a) > path(b) ? 1 : 0;
    });
  }

  // The folder label, with its realm (or digits) folder and account when
  // another row has the same label, and the file's time.
  function pickLabel(entry, counts) {
    let text = visible(String(entry.label));
    if (counts.get(entry.label) > 1) {
      text += " (" + visible(String(entry.realm_folder)) + ", account " + visible(String(entry.account)) + ")";
    }
    // A <Realm>/<First> folder (`first_name_folder`): the lab-addon never
    // writes there, so the label names the folder's shape instead.
    if (bare(entry)) return text + (entry.no_lab_record.first_name_folder === true ? ": <Realm>/<First> folder" : ": no lab record");
    if (!readable(entry)) return text + ": not read";
    return text + ", written " + localTime(entry.mtime_ns);
  }

  function loadCharacters() {
    if (!character.asked) {
      const asked = (async () => {
        charSay("Reading the lab-addon's files…", false);
        // The header's flavor is known first, so the list is that flavor's.
        await runFlavor.ready;
        const reply = await runJson(["char", "list"], { brief: true }, true);
        // Another flavor was picked while this one was read.
        if (character.asked !== asked) return;
        const report = reply.data;
        if (!report || !Array.isArray(report.characters)) {
          character.asked = null;
          charSay(reply.error ? shown(reply.error) : "wowlab char list printed something this page does not know.", true);
          return;
        }
        character.note = reply.error ? shown(reply.error) : "";
        fillPicker(report);
      })();
      character.asked = asked;
    }
    return character.asked;
  }

  function fillPicker(report) {
    const picker = byId("char-picker");
    picker.replaceChildren();
    character.flavor = typeof report.flavor_folder === "string" ? report.flavor_folder : null;
    character.entries = latestFirst(report.characters.filter((e) => e && typeof e === "object"));
    const counts = new Map();
    for (const entry of character.entries) counts.set(entry.label, (counts.get(entry.label) || 0) + 1);
    character.entries.forEach((entry, at) => {
      const option = make("option", pickLabel(entry, counts));
      option.value = String(at);
      if (!readable(entry)) {
        option.disabled = true;
        if (typeof entry.error === "string") option.title = shown(entry.error);
      }
      picker.appendChild(option);
    });
    const asked = character.want;
    const wanted = character.entries.findIndex((e) => readable(e) && isWanted(e));
    character.want = null;
    const first = wanted >= 0 ? wanted : character.entries.findIndex(readable);
    picker.disabled = first < 0;
    // The Account tab asked for a character this list does not hold: no
    // other one is read in its place (`openCharacter` says why).
    if (asked && wanted < 0) return;
    if (first < 0 && character.note) {
      charNote();
      return;
    }
    if (first < 0) {
      const saved = character.entries.filter((e) => !bare(e));
      charSay(
        saved.length
          ? "No character's file here could be read."
          : "wowlab char list found no character folder with a lab-addon save. Run it in a terminal for what to do.",
        false,
      );
      return;
    }
    charNote();
    picker.value = String(first);
    pickCharacter(character.entries[first]);
  }

  // Another flavor in the header: what was read or planned for the last one
  // goes, and the Character tab, when it is shown, reads the new one.
  byId("run-flavor").addEventListener("change", () => switchRunFlavor(byId("run-flavor").value));

  function switchRunFlavor(folder) {
    if (!explorer.flavors.includes(folder) || folder === runFlavor.picked) return;
    byId("run-flavor").value = folder;
    runFlavor.picked = folder;
    clearConfirm();
    character.seq += 1;
    character.asked = null;
    character.entries = [];
    character.flavor = null;
    character.binds = null;
    character.macros = null;
    character.note = "";
    byId("char-picker").replaceChildren();
    byId("char-header").replaceChildren();
    byId("char-gear").replaceChildren();
    clearDressing();
    byId("char-keys").replaceChildren();
    byId("char-macros").replaceChildren();
    byId("char-history").replaceChildren();
    charSay("", false);
    if (tabShown === "character") loadCharacters();
  }

  byId("char-picker").addEventListener("change", () => {
    const at = byId("char-picker").value;
    if (!/^[0-9]+$/.test(at)) return;
    const entry = character.entries[Number(at)];
    if (readable(entry)) pickCharacter(entry);
  });

  // The one pick handler: it clears the panels, then asks for the gear,
  // the keys, the macros and the history of the character picked, one run after
  // the other, each drawn when its reply comes and only if no later pick was
  // made since (`seq`). One run at a time: a pick never holds more than one
  // of the server's few run slots (`MAX_CONCURRENT_RUNS`, 503 `busy` past
  // them), and a pick left behind stops after the run it is waiting for.
  async function pickCharacter(entry) {
    const seq = ++character.seq;
    byId("char-header").replaceChildren(make("h2", visible(entry.label)));
    byId("char-gear").replaceChildren();
    clearDressing();
    byId("char-keys").replaceChildren();
    byId("char-macros").replaceChildren();
    byId("char-history").replaceChildren();
    character.binds = null;
    character.macros = null;
    charSay("Reading the gear, keys, macros and history of " + visible(entry.label) + "…", false);
    // `--character` takes `<realm or digits folder>/<character folder>`.
    const params = { character: entry.realm_folder + "/" + entry.label, account: entry.account };
    // The flavor the list read, else (every flavor listed) the row's own.
    const flavor = character.flavor || (typeof entry.flavor === "string" && entry.flavor ? entry.flavor : null);
    if (flavor) params.flavor = flavor;
    for (const load of [loadGear, loadKeys, loadMacros, loadHistory]) {
      await load(entry, params, seq);
      if (seq !== character.seq) return;
    }
    charNote();
  }

  // A refusal or any other error, in the CLI's own words, in `box`.
  function charError(box, reply, command) {
    const text = reply.error ? shown(reply.error) : "wowlab " + command + " printed something this page does not know.";
    const error = make("p", text);
    error.className = "char-error";
    box.replaceChildren(error);
  }

  async function loadGear(entry, params, seq) {
    const reply = await runJson(["char", "gear"], params);
    if (seq !== character.seq) return;
    const report = reply.data;
    if (reply.error || !report || !Array.isArray(report.slots) || !Array.isArray(report.empty_slots)) {
      // A refusal, a record with no gear, or any other error: the CLI's text.
      charError(byId("char-gear"), reply, "char gear");
      return;
    }
    drawCharHeader(entry, report);
    drawGear(report);
    drawDressing(report.dressing);
  }

  // One fact; `title`, when given, is the exact value behind the one shown.
  function charFact(list, label, value, title) {
    list.appendChild(make("dt", label));
    const dd = make("dd", value);
    if (typeof title === "string" && title) dd.title = title;
    list.appendChild(dd);
  }

  function numberWords(value) {
    return typeof value === "number" ? String(value) : "not returned";
  }

  // An average item level to one decimal, the client's exact figure as the title.
  function averageFact(list, label, value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      charFact(list, label, value.toFixed(1), "rounded to one decimal; exact: " + String(value));
    } else {
      charFact(list, label, numberWords(value));
    }
  }

  // A value the record does not hold, with the record's reason when it
  // gives one, as `char gear` prints it.
  function notRecorded(absent) {
    return typeof absent === "string" && absent ? "not recorded: " + visible(absent) : "not recorded";
  }

  // A class, race or spec: its name from the game tables wowlab already
  // downloaded for this client (`names`), with the id as the title (and
  // `more` after it); else the id; with no id, the record's reason.
  function namedFact(list, label, name, id, absent, more) {
    const idWords = typeof id === "number" ? label.toLowerCase() + " id " + id : "";
    const title = idWords && more ? idWords + "; " + more : idWords;
    if (idWords && typeof name === "string" && name) charFact(list, label, visible(name), title);
    else if (idWords) charFact(list, label, idWords, more || "");
    else charFact(list, label, notRecorded(absent));
  }

  function drawCharHeader(entry, report) {
    const header = byId("char-header");
    header.replaceChildren(make("h2", visible(entry.label)));
    const facts = make("dl");
    facts.className = "char-facts";
    const obj = (v) => (v && typeof v === "object" ? v : {});
    const identity = obj(report.identity);
    const names = obj(report.names);
    const spec = obj(report.spec);
    charFact(facts, "Level", typeof report.level === "number" ? String(report.level) : notRecorded(report.level_absent));
    // Without a cached name, the class's file name as the client gave it ("MAGE").
    const classWords = typeof names.class_name === "string" && names.class_name ? names.class_name : identity.class_file;
    namedFact(facts, "Class", classWords, identity.class_id, identity.class_id_absent);
    // One reason said once: a race missing for the class's reason says so.
    const raceAbsent =
      typeof identity.class_id !== "number" && typeof identity.race_id_absent === "string" &&
      identity.race_id_absent === identity.class_id_absent
        ? "as for Class"
        : identity.race_id_absent;
    namedFact(facts, "Race", names.race, identity.race_id, raceAbsent);
    // What the client calls the specialization, whatever its name reads.
    namedFact(facts, "Spec", names.spec, spec.id, spec.absent, "the client's specialization, not the talent points spent");
    const average = obj(report.average);
    if (typeof average.absent === "string") {
      charFact(facts, "Average item level", "absent (" + visible(average.absent) + ")");
    } else {
      averageFact(facts, "Average item level, equipped", average.equipped);
      averageFact(facts, "Average item level, overall", average.overall);
    }
    charFact(facts, "Character folder", visible(String(report.character)));
    charFact(facts, "File", shown(String(report.file)));
    charFact(facts, "Written", localTime(report.mtime_ns) + " (the file's modification time)");
    header.appendChild(facts);
    const notes = (Array.isArray(report.notes) ? report.notes : []).filter((n) => typeof n === "string");
    if (notes.length) {
      const list = make("ul");
      list.className = "char-notes";
      for (const note of notes) list.appendChild(make("li", shown(note)));
      header.appendChild(list);
    }
  }

  // The class the server's stylesheet paints an item name with: the colour
  // code with its letters in lower case and ":" written "_", as
  // `gear.colour_class` builds it. A code with no rule keeps the text colour.
  function colourClass(code) {
    if (typeof code !== "string" || !/^[0-9A-Za-z:]{1,16}$/.test(code)) return "";
    return "colour-" + code.toLowerCase().replace(/:/g, "_");
  }

  function slotLabel(entry) {
    return typeof entry.slot_name === "string" && entry.slot_name ? visible(entry.slot_name) : "Slot " + entry.slot;
  }

  function idList(value) {
    const ids = (Array.isArray(value) ? value : []).filter((n) => typeof n === "number");
    return ids.length ? ids.map(String).join(", ") : "none";
  }

  function drawGear(report) {
    const body = byId("char-gear");
    body.replaceChildren();
    // The client's slot order: filled and empty slots by the slot number.
    const rows = [];
    for (const slot of report.slots) if (slot && typeof slot.slot === "number") rows.push({ slot: slot, filled: true });
    for (const slot of report.empty_slots) if (slot && typeof slot.slot === "number") rows.push({ slot: slot, filled: false });
    rows.sort((a, b) => a.slot.slot - b.slot.slot);
    body.appendChild(make("h3", "Gear"));
    const table = make("table");
    table.className = "gear-table";
    const head = make("thead");
    const titles = make("tr");
    for (const title of ["Slot", "Item", "Item level"]) titles.appendChild(make("th", title));
    head.appendChild(titles);
    const rowsBox = make("tbody");
    for (const row of rows) {
      if (row.filled) gearRow(rowsBox, row.slot);
      else emptyRow(rowsBox, row.slot);
    }
    table.append(head, rowsBox);
    body.appendChild(table);
  }

  function emptyRow(box, slot) {
    const tr = make("tr");
    tr.className = "gear-row gear-row-empty";
    const name = make("td", slotLabel(slot));
    name.className = "gear-slot";
    const item = make("td", "empty");
    item.className = "gear-empty";
    const level = make("td");
    level.className = "gear-level";
    tr.append(name, item, level);
    box.appendChild(tr);
  }

  // A filled slot, and its detail row, drawn hidden and shown by a click on
  // the item's name.
  function gearRow(box, slot) {
    const tr = make("tr");
    tr.className = "gear-row";
    const slotCell = make("td", slotLabel(slot));
    slotCell.className = "gear-slot";
    const itemCell = make("td");
    const name = make("button", visible(String(slot.name)));
    name.type = "button";
    name.className = "gear-name";
    const colour = colourClass(slot.colour);
    if (colour) name.classList.add(colour);
    name.setAttribute("aria-expanded", "false");
    // The quality in words too, shown beside the name so it does not rest
    // on colour alone; the word is the record's, from the slot table.
    if (typeof slot.quality === "string" && slot.quality) {
      name.title = visible(slot.quality);
      const quality = make("span", " (" + visible(slot.quality) + ")");
      quality.className = "gear-quality";
      name.appendChild(quality);
    }
    itemCell.appendChild(name);
    const level = make("td", numberWords(slot.item_level));
    level.className = "gear-level";
    tr.append(slotCell, itemCell, level);
    const detail = gearDetail(slot);
    name.addEventListener("click", () => {
      detail.hidden = !detail.hidden;
      name.setAttribute("aria-expanded", detail.hidden ? "false" : "true");
    });
    box.append(tr, detail);
  }

  function gearDetail(slot) {
    const tr = make("tr");
    tr.className = "gear-detail";
    tr.hidden = true;
    const cell = make("td");
    cell.colSpan = 3;
    const list = make("dl");
    charFact(list, "Item id", typeof slot.item_id === "number" ? String(slot.item_id) : "not in the link");
    const code = visible(String(slot.colour));
    charFact(
      list,
      "Quality",
      typeof slot.quality === "string" && slot.quality
        ? visible(slot.quality) + " (colour code " + code + ")"
        : "colour code " + code + " is not in the slot table",
    );
    charFact(list, "Enchant", typeof slot.enchant_id === "number" ? String(slot.enchant_id) : "none");
    charFact(list, "Gems", idList(slot.gem_ids));
    charFact(list, "Bonus ids", idList(slot.bonus_ids));
    if (slot.crafter_removed === true) charFact(list, "Crafter", "the crafter's GUID was removed from this link");
    list.appendChild(make("dt", "Link"));
    const link = make("dd");
    const raw = make("code", visible(String(slot.raw_link)));
    raw.className = "gear-link";
    link.append(raw, copyButton(String(slot.raw_link), "Copy this item link"));
    list.appendChild(link);
    cell.appendChild(list);
    tr.appendChild(cell);
    return tr;
  }

  // ─── the dressing-room link (M15-02, §16.3's amendment of 2026-10-19) ───
  // `dressing` is `{url, site, note}` or `{items, note}`. The address becomes
  // the static link's href only when it parses, is https: and its origin is
  // exactly DRESSING_ORIGIN, and only as the parser writes it back (no
  // userinfo, nothing the parser would change); anything else leaves the
  // link hidden with no href, and says so. The fallback is the item ids as
  // text, with a Copy button.
  function dressingAddress(value) {
    if (typeof value !== "string") return null;
    let parsed = null;
    try {
      parsed = new URL(value);
    } catch (_) {
      return null;
    }
    if (parsed.protocol !== "https:" || parsed.origin !== DRESSING_ORIGIN) return null;
    if (parsed.username !== "" || parsed.password !== "" || parsed.toString() !== value) return null;
    return value;
  }

  function clearDressing() {
    const link = byId("char-dressing-link");
    link.removeAttribute("href");
    link.hidden = true;
    link.title = "";
    byId("char-dressing-note").textContent = "";
    byId("char-dressing-items").replaceChildren();
    byId("char-dressing-items").hidden = true;
    byId("char-dressing").hidden = true;
  }

  function drawDressing(dressing) {
    clearDressing();
    if (!dressing || typeof dressing !== "object") return;
    const note = typeof dressing.note === "string" ? visible(dressing.note) : "";
    byId("char-dressing").hidden = false;
    if (typeof dressing.url === "string") {
      const checked = dressingAddress(dressing.url);
      if (checked === null) {
        byId("char-dressing-note").textContent =
          "The dressing-room address in the report is not one this page links to, so no link is shown.";
        return;
      }
      const link = byId("char-dressing-link");
      link.href = checked;
      link.title = "Opens " + visible(String(dressing.site)) + "'s dressing room in a new tab";
      link.hidden = false;
      byId("char-dressing-note").textContent = note;
      return;
    }
    byId("char-dressing-note").textContent = note;
    const items = (Array.isArray(dressing.items) ? dressing.items : []).filter((i) => typeof i === "string");
    if (!items.length) return;
    const box = byId("char-dressing-items");
    const text = items.map(visible).join(" ");
    const ids = make("code", text);
    ids.className = "dressing-ids";
    box.append(make("span", "Item ids: "), ids, copyButton(text, "Copy the item ids"));
    box.hidden = false;
  }

  // ─── the keys and macros panels (M14-09, §16.3) ───
  // Laid out from `char binds --json` and `char macros --json`. A key's
  // words (`does`, `agreement`, `note`, a slot's `page_note`), a macro's
  // placement `reason`, a finding's `detail` and every note are the
  // report's; this script decides nothing about a key or a macro. Macro
  // bodies, macro names and spell names are player text: textContent only,
  // control characters shown as codes, and a `|c` escape or a tag in them is
  // only text.

  function texts(value) {
    return (Array.isArray(value) ? value : []).filter((n) => typeof n === "string");
  }

  function objects(value) {
    return (Array.isArray(value) ? value : []).filter((n) => n && typeof n === "object");
  }

  function plural(n, one, many) {
    return typeof n === "number" ? count(n, one, many) : "? " + many;
  }

  // Where a panel's data came from: the record and each file, each with the
  // file's time, as the CLI's text names them.
  function recordFact(list, report) {
    let value;
    if (typeof report.record_file !== "string") {
      value = "none" + (typeof report.record_error === "string" ? " (" + shown(report.record_error) + ")" : "");
    } else if (typeof report.record_error === "string") {
      value = shown(report.record_file) + " not read (" + shown(report.record_error) + ")";
    } else {
      value =
        shown(report.record_file) + " (schema " + numberWords(report.record_schema) + "), written " +
        localTime(report.record_mtime_ns) + " (the file's modification time)";
    }
    charFact(list, "Record", value);
  }

  function scopeLabel(scope, what) {
    const word = typeof scope === "string" ? visible(scope) : "?";
    return word.charAt(0).toUpperCase() + word.slice(1) + " " + what;
  }

  function bindingsFacts(list, files, withCounts) {
    for (const file of objects(files)) {
      const label = scopeLabel(file.scope, "bindings");
      const note = typeof file.note === "string" && file.note ? " " + shown(file.note) : "";
      if (typeof file.path !== "string") {
        charFact(list, label, "no bindings-cache.wtf" + note);
        continue;
      }
      let value = shown(file.path);
      if (withCounts) {
        value += " (" + plural(file.bind_lines, "bind line", "bind lines");
        if (typeof file.unknown_lines === "number" && file.unknown_lines) {
          value += "; " + plural(file.unknown_lines, "other line", "other lines") + ", kept";
        }
        value += ")";
      }
      charFact(list, label, value + ", written " + localTime(file.mtime_ns) + note);
    }
  }

  function charSources(report, withMacros) {
    const list = make("dl");
    list.className = "char-facts char-sources";
    recordFact(list, report);
    if (withMacros) {
      for (const file of objects(report.files)) {
        const label = scopeLabel(file.scope, "macros");
        if (typeof file.path !== "string") {
          charFact(list, label, "no macros-cache.txt");
          continue;
        }
        let value = plural(file.macros, "macro", "macros") + " in " + shown(file.path);
        if (typeof file.unknown_lines === "number" && file.unknown_lines) {
          value += ", " + plural(file.unknown_lines, "line", "lines") + " outside any macro not read";
        }
        charFact(list, label, value + ", written " + localTime(file.mtime_ns));
      }
      bindingsFacts(list, report.bindings_files, false);
    } else {
      bindingsFacts(list, report.files, true);
    }
    return list;
  }

  function charNotes(notes) {
    const list = make("ul");
    list.className = "char-notes";
    for (const note of texts(notes)) list.appendChild(make("li", shown(note)));
    return list;
  }

  function charLine(text, className) {
    const line = make("p", text);
    line.className = className;
    return line;
  }

  // "n keys." or "Showing k of n keys."
  function shownOf(kept, all, one, many) {
    const total = plural(all, one, many);
    return kept === all ? total + "." : "Showing " + kept + " of " + total + ".";
  }

  // ─── keys ───

  async function loadKeys(entry, params, seq) {
    const reply = await runJson(["char", "binds"], params);
    if (seq !== character.seq) return;
    const report = reply.data;
    if (reply.error || !report || !Array.isArray(report.keys) || !Array.isArray(report.unreached_slots)) {
      charError(byId("char-keys"), reply, "char binds");
      return;
    }
    character.binds = { report: report, rows: null, count: null, shared: new Set() };
    drawKeys(report);
  }

  function drawKeys(report) {
    const box = byId("char-keys");
    box.replaceChildren(charSources(report, false));
    if (typeof report.cannot_place === "string") {
      box.appendChild(charLine("Cannot place keys on action slots: " + shown(report.cannot_place), "char-cannot"));
    }
    if (typeof report.cannot_compare === "string") {
      box.appendChild(charLine("Cannot compare the files with the record: " + shown(report.cannot_compare), "char-cannot"));
    }
    const counted = charLine("", "char-count");
    const table = make("table");
    table.className = "char-table";
    const head = make("thead");
    const titles = make("tr");
    for (const title of ["Key", "Command", "Slot", "What it does", "Source"]) titles.appendChild(make("th", title));
    head.appendChild(titles);
    const rows = make("tbody");
    table.append(head, rows);
    box.append(counted, table, sharedNotes(report), drawUnreached(report));
    if (typeof report.left_out_note === "string") {
      // The record's two left-out counts in the report's own sentence.
      box.appendChild(charLine(report.left_out_note, "char-line"));
    }
    box.appendChild(charNotes(report.notes));
    character.binds.rows = rows;
    character.binds.count = counted;
    filterKeys();
  }

  // The rows the two filters keep: "Disagreements only" keeps the rows the
  // report marks `disagrees`; Find keeps the rows whose key, command, words
  // or sources hold the text, in any case.
  function keptKeys(report) {
    const needle = byId("char-keys-filter").value.trim().toLowerCase();
    const onlyDisagreeing = byId("char-keys-disagree").checked === true;
    return objects(report.keys).filter((row) => {
      if (onlyDisagreeing && row.disagrees !== true) return false;
      if (!needle) return true;
      const hay = [row.key, row.command, row.does, ...texts(row.sources)]
        .filter((s) => typeof s === "string")
        .join("\n")
        .toLowerCase();
      return hay.includes(needle);
    });
  }

  function filterKeys() {
    const drawn = character.binds;
    if (!drawn || !drawn.rows) return;
    const all = objects(drawn.report.keys);
    const kept = keptKeys(drawn.report);
    drawn.rows.replaceChildren();
    for (const row of kept) drawn.rows.appendChild(keyRow(row, drawn.shared));
    drawn.count.textContent = shownOf(kept.length, all.length, "key", "keys");
    if (!all.length) drawn.rows.appendChild(noKeyRow("No key in the files or the record."));
    else if (!kept.length) drawn.rows.appendChild(noKeyRow("No key matches the filters."));
  }

  function noKeyRow(text) {
    const tr = make("tr");
    const cell = make("td", text);
    cell.colSpan = 5;
    cell.className = "char-none";
    tr.appendChild(cell);
    return tr;
  }

  // A row note the report gives to more than one row that does not
  // disagree (on the capture, 128 rows "not in a bindings file: …"): said
  // once under the table with how many rows carry it, and kept in those
  // rows' tooltips. `character.binds.shared` holds them for `keyRow`.
  function sharedNotes(report) {
    const counts = new Map();
    for (const row of objects(report.keys)) {
      if (row.disagrees === true || typeof row.note !== "string" || !row.note) continue;
      counts.set(row.note, (counts.get(row.note) || 0) + 1);
    }
    const list = make("ul");
    list.className = "char-notes char-row-notes";
    for (const [note, n] of counts) {
      if (n < 2) continue;
      character.binds.shared.add(note);
      list.appendChild(make("li", "On " + count(n, "key", "keys") + ": " + shown(note)));
    }
    return list;
  }

  function keyRow(row, shared) {
    const tr = make("tr");
    tr.className = "key-row";
    if (row.disagrees === true) tr.classList.add("key-disagrees");
    const key = make("td", visible(String(row.key)));
    key.className = "key-key";
    const command = make("td", visible(String(row.command)));
    command.className = "key-command";
    const slot = make("td", typeof row.slot === "number" ? String(row.slot) : "-");
    slot.className = "key-slot";
    if (typeof row.page_note === "string") slot.title = visible(row.page_note);
    const does = make("td", visible(String(row.does)));
    does.className = "key-does";
    const source = make("td", texts(row.sources).map(visible).join(", "));
    source.className = "key-source";
    if (typeof row.agreement === "string") source.appendChild(make("small", visible(row.agreement)));
    if (row.disagrees === true) {
      const mark = make("strong", "disagrees");
      mark.className = "key-disagree-mark";
      source.appendChild(mark);
    }
    if (typeof row.note === "string" && row.note) {
      // A note is shown in its row, unless the same words are on several
      // rows that do not disagree: then they are said once under the table
      // (`sharedNotes`) and the row keeps them in its tooltip. A
      // disagreement's note names the other row, so it is always shown.
      if (row.disagrees !== true && shared.has(row.note)) source.title = visible(row.note);
      else source.appendChild(make("small", visible(row.note)));
    }
    tr.append(key, command, slot, does, source);
    return tr;
  }

  function drawUnreached(report) {
    const box = make("div");
    box.className = "char-unreached";
    if (typeof report.cannot_place === "string") return box;
    const slots = objects(report.unreached_slots);
    const groups = [
      ["Filled slots no key reaches on the page its bar shows by default", slots.filter((u) => u.unfollowed_page !== true)],
      ["Filled slots on main-bar pages that are not followed", slots.filter((u) => u.unfollowed_page === true)],
    ];
    groups.forEach(([title, list], at) => {
      if (at > 0 && !list.length) return;
      box.appendChild(make("h4", title + ": " + list.length));
      const items = make("ul");
      items.className = "char-slots";
      for (const u of list) {
        const where = typeof u.page_note === "string" ? " (" + visible(u.page_note) + ")" : "";
        items.appendChild(make("li", "slot " + numberWords(u.slot) + ": " + visible(String(u.does)) + where));
      }
      box.appendChild(items);
    });
    return box;
  }

  byId("char-keys-filter").addEventListener("input", () => filterKeys());
  byId("char-keys-disagree").addEventListener("change", () => filterKeys());

  // ─── macros ───

  async function loadMacros(entry, params, seq) {
    const reply = await runJson(["char", "macros"], params);
    if (seq !== character.seq) return;
    const report = reply.data;
    if (reply.error || !report || !Array.isArray(report.macros) || !Array.isArray(report.cannot_judge)) {
      charError(byId("char-macros"), reply, "char macros");
      return;
    }
    character.macros = { report: report, list: null, count: null };
    drawMacros(report);
  }

  function drawMacros(report) {
    const box = byId("char-macros");
    box.replaceChildren(charSources(report, true));
    const counted = charLine("", "char-count");
    const list = make("div");
    list.className = "macro-list";
    box.append(counted, list);
    const judged = objects(report.cannot_judge);
    box.appendChild(make("h4", "Cannot judge for any macro: " + judged.length));
    const items = make("ul");
    items.className = "char-slots";
    for (const c of judged) items.appendChild(make("li", visible(String(c.rule)) + ": " + shown(String(c.reason))));
    box.append(items, charNotes(report.notes));
    character.macros.list = list;
    character.macros.count = counted;
    filterMacros();
  }

  // "Findings only" keeps the macros the checker found something in.
  function filterMacros() {
    const drawn = character.macros;
    if (!drawn || !drawn.list) return;
    const all = objects(drawn.report.macros);
    const onlyFindings = byId("char-macros-findings").checked === true;
    const kept = all.filter((m) => !onlyFindings || objects(m.findings).length > 0);
    drawn.list.replaceChildren();
    for (const m of kept) drawn.list.appendChild(macroCard(m, drawn.report.keys_incomplete));
    drawn.count.textContent = shownOf(kept.length, all.length, "macro", "macros");
    if (!all.length) drawn.list.appendChild(charLine("No macro in these files.", "char-none"));
    else if (!kept.length) drawn.list.appendChild(charLine("No macro here has a finding.", "char-none"));
  }

  function macroCard(m, keysIncomplete) {
    const card = make("article");
    card.className = "macro";
    const title = make("h4", visible(String(m.scope)) + " macro " + numberWords(m.position) + " ");
    const name = make("span", visible(String(m.name)));
    name.className = "macro-name";
    title.appendChild(name);
    card.appendChild(title);
    let facts =
      "id " + visible(String(m.id)) + ", icon " + visible(String(m.icon)) + ", line " + numberWords(m.line) +
      " of " + shown(String(m.file)) + ", " + plural(m.characters, "character", "characters") + ", " +
      plural(m.bytes, "byte", "bytes");
    if (m.complete === false) facts += " (the file ends before this macro's END)";
    card.appendChild(charLine(facts, "macro-facts"));
    // The body, one element a line, as text.
    const lines = texts(m.body_lines);
    const body = make("div");
    body.className = "macro-body";
    for (const text of lines) {
      const one = make("code", visible(text));
      one.className = "macro-line";
      body.appendChild(one);
    }
    if (!lines.length) body.appendChild(charLine("no body lines", "char-none"));
    card.append(body, placementBox(m.placement, keysIncomplete));
    const found = objects(m.findings);
    const judged = objects(m.cannot_judge);
    if (!found.length && !judged.length) {
      card.appendChild(charLine("No finding.", "macro-clean"));
      return card;
    }
    const list = make("ul");
    list.className = "macro-findings";
    for (const f of found) {
      const item = make("li", "finding " + visible(String(f.rule)) + " (line " + numberWords(f.line) + "): " + visible(String(f.detail)));
      item.className = "macro-finding";
      list.appendChild(item);
    }
    for (const c of judged) {
      const item = make("li", "cannot judge " + visible(String(c.rule)) + " (line " + numberWords(c.line) + "): " + visible(String(c.reason)));
      item.className = "macro-cannot";
      list.appendChild(item);
    }
    card.appendChild(list);
    return card;
  }

  // Where the macro may sit and the keys that reach each slot, as the
  // placement gives them: a slot's keys are the placement's own `keys`
  // whose `slot` is that slot, the way the CLI's text lists them.
  function placementBox(placement, keysIncomplete) {
    const box = make("div");
    box.className = "macro-placement";
    const p = placement && typeof placement === "object" ? placement : {};
    const status = visible(String(p.status));
    box.appendChild(charLine("Placement: " + status + ": " + visible(String(p.reason)), "macro-reason"));
    const keys = objects(p.keys);
    const slots = objects(p.slots);
    if (!slots.length) return box;
    const list = make("ul");
    list.className = "macro-slots";
    for (const s of slots) {
      const item = make("li", "may sit at slot " + numberWords(s.slot) + " (" + status + "): " + visible(String(s.page_note)));
      const under = make("ul");
      const reach = keys.filter((k) => k.slot === s.slot);
      if (!reach.length) {
        const among = typeof keysIncomplete === "string" ? ", among the keys read (" + shown(keysIncomplete) + ")" : "";
        under.appendChild(make("li", "no key reaches it on the page its bar shows by default" + among));
      }
      for (const k of reach) {
        const key = make("li", "key " + visible(String(k.key)) + " (" + visible(String(k.command)) + "; " + texts(k.sources).map(visible).join(", ") + ")");
        key.className = "macro-key";
        under.appendChild(key);
      }
      item.appendChild(under);
      list.appendChild(item);
    }
    box.appendChild(list);
    return box;
  }

  byId("char-macros-findings").addEventListener("change", () => filterMacros());

  // ─── the history panel (M17-03, §19 CW-2) ───
  // Laid out from `char history --json` for the character picked: one row
  // per save of its WowLab.lua, oldest first, from every snapshot in
  // wowlab's store and the file in the game folder. A save's time is the
  // file's modification time, so a restored file shows as a later save; the
  // report marks it (`same_bytes_as_ns`) and the panel says so. Every item,
  // slot, spec and currency name, and every reason, is the report's; this
  // script compares only the level, the equipped item level and the spec id
  // of two saves next to each other. The charts are inline SVG built here
  // from those numbers alone (no text of the report goes into them but a
  // date and a number), hidden from screen readers; the table under them
  // holds the same values and is what a screen reader reads.

  // The SVG namespace: a name the DOM needs to make SVG elements, not an
  // address. Nothing is fetched from it.
  const SVG_NS = "http://www.w3.org/2000/svg";
  // The chart's drawing box, in its own units: the SVG scales to the panel.
  const CHART = { width: 640, height: 150, left: 56, right: 16, top: 14, bottom: 26 };

  const HISTORY_FEW =
    "History appears once there are two different saves of this character (the file in the game " +
    "folder counts as one). The game saves it when you log out or type /reload; take a backup after " +
    "you log out or /reload (Take a backup now on the Account tab, or wowlab snap create); a backup " +
    "taken while you are still playing keeps the save from before.";

  async function loadHistory(entry, params, seq) {
    // A character with no save that could be read ends with an error and
    // still prints its report: the report says why, so it is drawn.
    const reply = await runJson(["char", "history"], params, true);
    if (seq !== character.seq) return;
    const report = reply.data;
    if (!report || !Array.isArray(report.rows)) {
      charError(byId("char-history"), reply, "char history");
      return;
    }
    drawHistory(report);
  }

  const historyNum = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

  // The date part of `localTime`, for a chart's axis.
  function dayOf(ns) {
    return localTime(ns).split(" ")[0];
  }

  function historySpec(row) {
    const spec = row.spec && typeof row.spec === "object" ? row.spec : {};
    if (typeof spec.id !== "number") return notRecorded(spec.absent);
    if (typeof row.spec_name === "string" && row.spec_name) return visible(row.spec_name);
    return "spec id " + spec.id;
  }

  function historyLevel(row) {
    return historyNum(row.level) === null ? notRecorded(row.level_absent) : String(row.level);
  }

  // An equipped item level to one decimal; the exact figure goes in a title.
  function oneDecimal(v) {
    return v.toFixed(1);
  }

  // Two different item levels that read the same to one decimal are shown
  // exactly, so a change never reads as "8.2 to 8.2".
  function twoDecimals(a, b) {
    return a !== b && oneDecimal(a) === oneDecimal(b) ? [String(a), String(b)] : [oneDecimal(a), oneDecimal(b)];
  }

  function historyItemLevel(row) {
    const v = historyNum(row.item_level);
    return v === null ? notRecorded(row.item_level_absent) : oneDecimal(v);
  }

  // Where a save was found: how many snapshots hold it, and the game folder.
  function historyHeld(row) {
    const held = [];
    const snaps = texts(row.snapshots).length;
    if (snaps) held.push(count(snaps, "snapshot", "snapshots"));
    if (row.live === true) held.push("the game folder");
    return held.length ? held.join(" and ") : "nowhere listed";
  }

  function historyRestored(row) {
    return historyNum(row.same_bytes_as_ns) === null
      ? ""
      : "Same contents as the save of " + localTime(row.same_bytes_as_ns) +
          ": likely restored, so this time is when it was put back.";
  }

  // One step per save, so the line's slope is not a rate.
  const STEPS = ", one step per save (not spaced by time).";

  // One chart: a line through each save's value (broken where a save does
  // not record it), a dot on each value, the lowest and highest value at the
  // left, and the first and last save's dates under it, one step per save.
  // The caption says the same in words; with fewer than two values it is the
  // caption alone.
  function historyChart(title, values, rows, shownAs) {
    const box = make("div");
    box.className = "history-chart";
    const known = values.filter((v) => v !== null);
    if (known.length < 2) {
      const few = title + ": recorded in " + known.length + " of " + count(values.length, "save", "saves");
      box.appendChild(charLine(few + ", too few to draw.", "char-none"));
      return box;
    }
    const low = Math.min(...known);
    const high = Math.max(...known);
    // Two values that read the same once rounded are written exactly.
    const exact = low !== high && shownAs(low) === shownAs(high);
    const lowText = exact ? String(low) : shownAs(low);
    const highText = exact ? String(high) : shownAs(high);
    const range =
      low === high
        ? ": " + lowText + " in every save that records it"
        : ": lowest " + lowText + ", highest " + highText;
    box.appendChild(charLine(title + range + STEPS, "history-caption"));
    const c = CHART;
    const across = c.width - c.left - c.right;
    const down = c.height - c.top - c.bottom;
    const xOf = (i) => c.left + (across * i) / (values.length - 1);
    const yOf = (v) => (high === low ? c.top + down / 2 : c.top + (down * (high - v)) / (high - low));
    const fixed = (n) => String(Math.round(n * 10) / 10);
    const chart = document.createElementNS(SVG_NS, "svg");
    chart.setAttribute("viewBox", "0 0 " + c.width + " " + c.height);
    chart.setAttribute("aria-hidden", "true");
    chart.classList.add("history-svg");
    const axis = document.createElementNS(SVG_NS, "polyline");
    const corner = [[c.left, c.top], [c.left, c.top + down], [c.left + across, c.top + down]];
    axis.setAttribute("points", corner.map(([x, y]) => fixed(x) + "," + fixed(y)).join(" "));
    axis.classList.add("history-axis");
    chart.appendChild(axis);
    let run = [];
    const lines = [];
    values.forEach((v, i) => {
      if (v === null) {
        if (run.length) lines.push(run);
        run = [];
      } else {
        run.push(fixed(xOf(i)) + "," + fixed(yOf(v)));
      }
    });
    if (run.length) lines.push(run);
    for (const points of lines) {
      if (points.length < 2) continue;
      const line = document.createElementNS(SVG_NS, "polyline");
      line.setAttribute("points", points.join(" "));
      line.classList.add("history-line");
      chart.appendChild(line);
    }
    values.forEach((v, i) => {
      if (v === null) return;
      const dot = document.createElementNS(SVG_NS, "circle");
      dot.setAttribute("cx", fixed(xOf(i)));
      dot.setAttribute("cy", fixed(yOf(v)));
      dot.setAttribute("r", "4");
      dot.classList.add("history-dot");
      chart.appendChild(dot);
    });
    const label = (text, x, y, atEnd) => {
      const node = document.createElementNS(SVG_NS, "text");
      node.setAttribute("x", fixed(x));
      node.setAttribute("y", fixed(y));
      node.classList.add("history-label");
      if (atEnd) node.classList.add("history-end");
      node.textContent = text;
      chart.appendChild(node);
    };
    label(highText, c.left - 8, c.top + 5, true);
    if (high !== low) label(lowText, c.left - 8, c.top + down + 5, true);
    label(dayOf(rows[0].saved_at_ns), c.left, c.height - 6, false);
    label(dayOf(rows[rows.length - 1].saved_at_ns), c.left + across, c.height - 6, true);
    box.appendChild(chart);
    return box;
  }

  // The charts' values, one row per save, oldest first: what a screen
  // reader reads in their place.
  function historyTable(rows) {
    const wrap = make("div");
    wrap.className = "history-table";
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", "Each save as a table");
    wrap.tabIndex = 0;
    const table = make("table");
    table.className = "char-table";
    table.appendChild(make("caption", "Each save, oldest first: the values the charts draw"));
    const head = make("tr");
    for (const word of ["Saved", "Level", "Equipped item level", "Spec", "Held in"]) {
      const th = make("th", word);
      th.scope = "col";
      head.appendChild(th);
    }
    const thead = make("thead");
    thead.appendChild(head);
    const body = make("tbody");
    for (const row of rows) {
      const tr = make("tr");
      const saved = make("th", localTime(row.saved_at_ns));
      saved.scope = "row";
      const restored = historyRestored(row);
      if (restored) {
        saved.appendChild(make("small", ", likely restored (same as " + localTime(row.same_bytes_as_ns) + ")"));
        saved.title = restored;
      }
      tr.appendChild(saved);
      for (const text of [historyLevel(row), historyItemLevel(row), historySpec(row), historyHeld(row)]) {
        tr.appendChild(make("td", text));
      }
      const exact = historyNum(row.item_level);
      if (exact !== null) tr.children[2].title = "exact: " + String(exact);
      body.appendChild(tr);
    }
    table.append(thead, body);
    wrap.appendChild(table);
    return wrap;
  }

  function historyItem(item) {
    if (!item || typeof item !== "object") return "empty";
    return visible(String(item.name)) + " (item level " + numberWords(item.item_level) + ")";
  }

  // One gear change, in `char history`'s words: another item; the same item
  // id as a different instance (the link's parts the report lists in
  // `differs`); or the same instance at another item level.
  function historyGear(g) {
    const slot =
      typeof g.slot_name === "string" && g.slot_name ? visible(g.slot_name) : "slot " + visible(String(g.slot));
    const before = g.before && typeof g.before === "object" ? g.before : null;
    const after = g.after && typeof g.after === "object" ? g.after : null;
    const differs = texts(g.differs);
    let what;
    if (before && after && !(differs.length === 1 && differs[0] === "item id")) {
      const parts = differs.filter((d) => d !== "item level").map(visible);
      const level = differs.includes("item level")
        ? "item level " + numberWords(before.item_level) + " to " + numberWords(after.item_level) +
          " (the client's figure)"
        : "";
      if (parts.length) {
        what =
          historyItem(after) + ", same item id, a different instance (" + parts.join(", ") + "); before: " +
          historyItem(before);
        if (level) what += "; " + level;
      } else if (level) {
        what = visible(String(after.name)) + ", same instance, " + level;
      } else {
        what = historyItem(before) + " to " + historyItem(after);
      }
    } else {
      what = historyItem(before) + " to " + historyItem(after);
    }
    return slot + ": " + what;
  }

  function historyCurrency(c) {
    const amount = (n) => (historyNum(n) === null ? "not listed" : String(n));
    const name = typeof c.name === "string" && c.name ? visible(c.name) : "currency " + visible(String(c.id));
    return name + ": " + amount(c.before) + " to " + amount(c.after);
  }

  // What changed at `row` since `prev`, the save before it, in words.
  function historyChanges(row, prev) {
    const out = [];
    const restored = historyRestored(row);
    if (restored) out.push(restored);
    if (!prev) {
      out.push("The oldest save wowlab has.");
      return out;
    }
    // A value both saves record is compared; one that only one save records
    // is said to be so, never counted as a change. `compared` names what was
    // compared, for the sentence when nothing changed.
    let changed = 0;
    const compared = [];
    const compare = (label, was, now, words) => {
      if (was === null && now === null) return;
      if (was === null || now === null) out.push(label + ": not recorded in one of the two saves.");
      else {
        compared.push(label.toLowerCase());
        if (was === now) return;
        changed += 1;
        out.push(words(was, now));
      }
    };
    compare("Level", historyNum(prev.level), historyNum(row.level), (a, b) => "Level " + a + " to " + b);
    compare(
      "Equipped item level",
      historyNum(prev.item_level),
      historyNum(row.item_level),
      (a, b) => {
        const [x, y] = twoDecimals(a, b);
        return "Equipped item level " + x + " to " + y + " (the client's figure)";
      },
    );
    const specOf = (r) => (r.spec && typeof r.spec.id === "number" ? r.spec.id : null);
    compare("Spec", specOf(prev), specOf(row), () => "Spec " + historySpec(prev) + " to " + historySpec(row));
    if (typeof row.gear_not_compared === "string") out.push("Gear not compared: " + visible(row.gear_not_compared));
    else compared.push("gear");
    for (const g of objects(row.gear_changes)) {
      changed += 1;
      out.push(historyGear(g));
    }
    if (typeof row.currencies_not_compared === "string") {
      out.push("Currencies not compared: " + visible(row.currencies_not_compared));
    } else {
      compared.push("currencies");
    }
    for (const c of objects(row.currency_changes)) {
      changed += 1;
      out.push(historyCurrency(c));
    }
    if (typeof row.currency_panel_changed === "string") out.push(visible(row.currency_panel_changed));
    if (!changed && compared.length) {
      out.push("No change in what both saves record (" + compared.join(", ") + ").");
    }
    return out;
  }

  // The saves, newest first, each with what changed since the one before.
  function historyList(rows) {
    const list = make("ol");
    list.className = "history-list";
    for (let i = rows.length - 1; i >= 0; i--) {
      const item = make("li");
      item.appendChild(make("strong", localTime(rows[i].saved_at_ns)));
      const changes = make("ul");
      for (const text of historyChanges(rows[i], i ? rows[i - 1] : null)) changes.appendChild(make("li", text));
      item.appendChild(changes);
      list.appendChild(item);
    }
    return list;
  }

  // The copies that could not be read, and the snapshots that did not load.
  function historyMissing(box, report) {
    const bad = objects(report.unreadable);
    if (bad.length) {
      box.appendChild(make("h4", "Could not read " + count(bad.length, "copy", "copies")));
      const list = make("ul");
      list.className = "char-slots";
      for (const copy of bad) {
        const where =
          copy.where === "install" ? "the file in the game folder" : "snapshot " + visible(String(copy.snapshot_id));
        list.appendChild(make("li", where + ": " + shown(String(copy.reason))));
      }
      box.appendChild(list);
    }
    const damaged = objects(report.damaged_snapshots);
    if (damaged.length) {
      box.appendChild(
        make(
          "h4",
          count(damaged.length, "snapshot", "snapshots") +
            " did not load, so wowlab cannot tell whether they hold this character",
        ),
      );
      const list = make("ul");
      list.className = "char-slots";
      for (const d of damaged) list.appendChild(make("li", visible(String(d.name)) + ": " + shown(String(d.reason))));
      box.appendChild(list);
    }
  }

  function drawHistory(report) {
    const box = byId("char-history");
    box.replaceChildren();
    const rows = objects(report.rows).filter((r) => historyNum(r.saved_at_ns) !== null);
    if (rows.length < 2) {
      const so = rows.length
        ? "One save of this character so far, written " + localTime(rows[0].saved_at_ns) + "."
        : "No save of this character could be read.";
      box.appendChild(charLine(so, "char-count"));
      box.appendChild(charLine(HISTORY_FEW, "char-line"));
    } else {
      const span =
        count(rows.length, "save", "saves") + ", " + dayOf(rows[0].saved_at_ns) + " to " +
        dayOf(rows[rows.length - 1].saved_at_ns) + ". A time is the file's modification time: when the game " +
        "wrote it at logout or /reload, or when a restore put it back.";
      box.appendChild(charLine(span, "char-count"));
      const charts = make("div");
      charts.className = "history-charts";
      charts.append(
        historyChart(
          "Equipped item level (the client's average, as the addon recorded it)",
          rows.map((r) => historyNum(r.item_level)),
          rows,
          oneDecimal,
        ),
        historyChart("Level", rows.map((r) => historyNum(r.level)), rows, String),
      );
      box.appendChild(charts);
    }
    if (rows.length) box.appendChild(historyTable(rows));
    if (rows.length >= 2) {
      box.appendChild(make("h4", "Changes, newest first"));
      box.appendChild(historyList(rows));
    }
    historyMissing(box, report);
    const notes = texts(report.notes);
    if (notes.length) {
      const more = make("details");
      more.className = "history-notes";
      more.appendChild(make("summary", count(notes.length, "note", "notes") + " on what these figures are"));
      more.appendChild(charNotes(notes));
      box.appendChild(more);
    }
  }

  // ─── the looks tab (M14-04, §16.3) ───
  // "Download tables and write the page" runs `looks page` (no parameters: its default place
  // under the data folder's pages/, never `--out`), then reads that file
  // from /api/looks-page, whose path the server builds itself, and puts the
  // text in #looks-frame as its document. The frame is sandboxed with
  // allow-scripts alone, so the page runs its own script (allowed by hash in
  // the server's CSP) in an origin of its own, and reaches nothing here.
  // The page's text goes nowhere else: never into this document.

  const looks = { seq: 0 };

  function looksSay(text, isError) {
    const node = byId("looks-status");
    announce(node, text, Boolean(isError));
    node.classList.toggle("error", Boolean(isError));
  }

  function count(n, one, many) {
    return String(n) + " " + (n === 1 ? one : many);
  }

  // Where the page is, as the CLI prints it, with a Copy button.
  function drawWhere(path) {
    const box = byId("looks-where");
    box.replaceChildren();
    if (typeof path !== "string" || !path) {
      box.hidden = true;
      return;
    }
    const text = make("code", shown(path));
    box.append("Written to ", text, copyButton(path, "Copy the page's path"));
    box.hidden = false;
  }

  // The page's text from the server, or {error}.
  async function readLooksPage() {
    let reply;
    let result;
    try {
      reply = await fetch(api("/api/looks-page"), { cache: "no-store", credentials: "omit", headers: keyed() });
      result = await reply.json();
    } catch (error) {
      return { error: "The request failed: " + error };
    }
    if (!reply.ok) {
      let error = "The server did not show the page: " + (result.error || reply.status);
      if (result.error === "not_a_page") {
        error += ". The server shows only a real file that wowlab wrote there, never one through a link (a symlink or junction).";
      }
      return { error: error };
    }
    if (!result || typeof result.page !== "string") return { error: "The server sent something this page does not know." };
    return { page: result.page };
  }

  async function writeLooks() {
    const seq = ++looks.seq;
    const button = byId("looks-write");
    const frame = byId("looks-frame");
    button.disabled = true;
    // A page an earlier press showed is hidden until this press shows its
    // own, so a failure is never drawn above an older page.
    frame.hidden = true;
    try {
      looksSay("Downloading the tables wowlab does not have yet and writing the looks page…", false);
      // Exit 1 with a report: the page was written, and some look files
      // are damaged (the CLI names them on stderr).
      const reply = await runJson(["looks", "page"], {}, true);
      if (seq !== looks.seq) return;
      const report = reply.data;
      if (!report || typeof report.path !== "string") {
        drawWhere(null);
        looksSay(reply.error ? shown(reply.error) : "wowlab looks page printed something this page does not know.", true);
        return;
      }
      drawWhere(report.path);
      const read = await readLooksPage();
      if (seq !== looks.seq) return;
      if (read.error) {
        looksSay(read.error, true);
        return;
      }
      frame.srcdoc = read.page;
      frame.hidden = false;
      const lines = [
        "Wrote the looks page for build " + visible(String(report.build)) + ": " +
          count(report.races, "race", "races") + ", " + count(report.views, "view", "views") + ", " +
          count(report.looks, "saved look", "saved looks") + ".",
      ];
      for (const remark of Array.isArray(report.remarks) ? report.remarks : []) lines.push(shown(String(remark)));
      if (reply.error) lines.push(shown(reply.error));
      looksSay(lines.join("\n"), Boolean(reply.error));
    } finally {
      if (seq === looks.seq) button.disabled = false;
    }
  }

  byId("looks-write").addEventListener("click", () => writeLooks());

  // ─── the account hub (M17-01, §19 CW-1) ───
  // The home tab: one row per character folder in every flavor, from one
  // run of `char list --summary --json`, which reads every flavor folder
  // whatever the header's flavor says. Every value, and every word about a
  // folder without a lab record (`advice`, `fix_command`), is the report's;
  // this script sorts, filters and lays the rows out, and says "not
  // recorded" for a value the record leaves out (the report's reason is the
  // cell's title). A recorded row opens the Character tab on that
  // character. Nothing here edits anything.

  const hub = {
    asked: null, // the one `char list --summary` run; asked again after a failure
    report: null, // its CharSummaryReport
    note: "", // what the run said on stderr when it exited with an error
    sort: { key: "saved", descending: true },
  };
  const NOT_RECORDED = "not recorded";

  function hubSay(text, isError) {
    const node = byId("hub-status");
    announce(node, text, Boolean(isError));
    node.classList.toggle("error", Boolean(isError));
  }

  const hubNum = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const hubStr = (v) => (typeof v === "string" && v !== "" ? v : null);

  function hubClass(row) {
    if (hubStr(row.class_name)) return row.class_name;
    if (hubStr(row.class_file)) return row.class_file;
    return hubNum(row.class_id) !== null ? "class " + row.class_id : null;
  }

  function hubNamed(name, id, word) {
    if (hubStr(name)) return name;
    return hubNum(id) !== null ? word + " " + id : null;
  }

  function hubProfession(p) {
    if (!p || typeof p !== "object") return "";
    const name = hubNamed(p.name, p.skill_line, "skill line") || "skill line " + NOT_RECORDED;
    const rank = hubNum(p.rank) === null ? NOT_RECORDED : String(p.rank);
    return name + " " + rank + (hubNum(p.max_rank) === null ? "" : "/" + p.max_rank);
  }

  function hubProfessions(row) {
    if (!Array.isArray(row.professions)) return null;
    return row.professions.length ? row.professions.map(hubProfession).join(", ") : "none";
  }

  // The columns: what each shows (null: not recorded, `why` gives the
  // report's reason) and the value it sorts by (null sorts last).
  const HUB_COLUMNS = [
    { key: "name", label: "Character", value: (r) => r.label },
    { key: "realm", label: "Realm folder", value: (r) => r.realm_folder },
    { key: "flavor", label: "Flavor", value: (r) => hubStr(r.flavor) || "" },
    { key: "level", label: "Level", value: (r) => hubNum(r.level), why: (r) => r.level_absent },
    { key: "class", label: "Class", value: hubClass, why: (r) => r.class_absent },
    { key: "race", label: "Race", value: (r) => hubNamed(r.race_name, r.race_id, "race"), why: (r) => r.race_absent },
    { key: "spec", label: "Spec", value: (r) => hubNamed(r.spec_name, r.spec_id, "spec"), why: (r) => r.spec_absent },
    {
      key: "ilvl", label: "Item level", value: (r) => hubNum(r.item_level), why: (r) => r.item_level_absent,
      text: (v) => v.toFixed(1), title: (v) => "equipped average, rounded to one decimal; exact: " + String(v),
    },
    { key: "professions", label: "Professions", value: hubProfessions, why: (r) => r.professions_absent },
    { key: "saved", label: "Last logout or /reload", value: (r) => hubNum(r.mtime_ns), text: localTime },
  ];

  const recorded = (row) => row.state === "recorded";

  function loadHub() {
    if (!hub.asked) {
      const asked = (async () => {
        hubSay("Reading every character's lab-addon file…", false);
        const reply = await runJson(["char", "list"], { summary: true }, true, true);
        const report = reply.data;
        if (!report || !Array.isArray(report.rows)) {
          hub.asked = null;
          hubSay(reply.error ? shown(reply.error) : "wowlab char list printed something this page does not know.", true);
          return;
        }
        hub.report = {
          rows: report.rows.filter(
            (r) => r && typeof r === "object" && typeof r.label === "string" && typeof r.account === "string" &&
              typeof r.realm_folder === "string",
          ),
        };
        hub.note = reply.error ? shown(reply.error) : "";
        fillHubFilters();
        drawHub();
        takeBackups(report);
      })();
      hub.asked = asked;
    }
    return hub.asked;
  }

  function hubOption(select, value, text) {
    const option = make("option", text);
    option.value = value;
    select.appendChild(option);
  }

  // The filters list only what the rows hold: each flavor with a row, and
  // each class a row names, plus "not recorded" when a row names none.
  function fillHubFilters() {
    const rows = hub.report.rows;
    // A report read again keeps the filters picked, when it still lists them.
    const keep = (picker, value) => {
      picker.value = picker.options.some((o) => o.value === value) ? value : "";
    };
    const pickedFlavor = byId("hub-flavor").value;
    const pickedClass = byId("hub-class").value;
    const flavors = [...new Set(rows.map((r) => hubStr(r.flavor)).filter(Boolean))];
    const flavorPicker = byId("hub-flavor");
    flavorPicker.replaceChildren();
    hubOption(flavorPicker, "", "All");
    for (const folder of flavors) hubOption(flavorPicker, "f:" + folder, visible(folder));
    byId("hub-flavor-box").hidden = flavors.length < 2;
    const classPicker = byId("hub-class");
    classPicker.replaceChildren();
    hubOption(classPicker, "", "All");
    const classes = [...new Set(rows.map(hubClass).filter((c) => c !== null))].sort();
    for (const name of classes) hubOption(classPicker, "c:" + name, visible(name));
    if (rows.some((r) => hubClass(r) === null)) hubOption(classPicker, "none", NOT_RECORDED);
    keep(flavorPicker, pickedFlavor);
    keep(classPicker, pickedClass);
  }

  function hubShown() {
    const flavor = byId("hub-flavor").value;
    const klass = byId("hub-class").value;
    return hub.report.rows.filter((r) => {
      if (flavor && "f:" + hubStr(r.flavor) !== flavor) return false;
      if (klass === "none") return hubClass(r) === null;
      return !klass || "c:" + hubClass(r) === klass;
    });
  }

  // Sorted by the chosen column; a value not recorded sorts last either
  // way, and a tie goes by the folder name.
  function hubSorted(rows) {
    const column = HUB_COLUMNS.find((c) => c.key === hub.sort.key) || HUB_COLUMNS[0];
    const key = (r, c) => {
      const v = recorded(r) || c.key === "name" || c.key === "realm" || c.key === "flavor" ? c.value(r) : null;
      return typeof v === "string" ? v.toLowerCase() : v;
    };
    const order = hub.sort.descending ? -1 : 1;
    const compare = (x, y) => (x < y ? -1 : x > y ? 1 : 0);
    return rows.slice().sort((a, b) => {
      const x = key(a, column);
      const y = key(b, column);
      if (x !== y) {
        if (x === null) return 1;
        if (y === null) return -1;
        const by = compare(x, y);
        if (by) return by * order;
      }
      return compare(key(a, HUB_COLUMNS[0]), key(b, HUB_COLUMNS[0]));
    });
  }

  function sortWords() {
    const column = HUB_COLUMNS.find((c) => c.key === hub.sort.key) || HUB_COLUMNS[0];
    return column.label.toLowerCase() + (hub.sort.descending ? ", descending" : ", ascending");
  }

  // The header: a sort button per column. The sorted column says so as
  // `aria-sort` (set as a property) and with an arrow the eye sees.
  function hubHead() {
    const head = make("thead");
    const tr = make("tr");
    for (const column of HUB_COLUMNS) {
      const th = make("th");
      th.scope = "col";
      const on = column.key === hub.sort.key;
      th.ariaSort = on ? (hub.sort.descending ? "descending" : "ascending") : null;
      const button = make("button", column.label);
      button.type = "button";
      button.className = "hub-sort";
      button.title = "Sort by " + column.label.toLowerCase();
      if (on) {
        const mark = make("span", hub.sort.descending ? " ▼" : " ▲");
        mark.ariaHidden = "true";
        button.appendChild(mark);
      }
      button.addEventListener("click", () => {
        if (hub.sort.key === column.key) hub.sort.descending = !hub.sort.descending;
        else hub.sort = { key: column.key, descending: column.key === "saved" };
        drawHub(column.key);
      });
      th.appendChild(button);
      tr.appendChild(th);
    }
    head.appendChild(tr);
    return head;
  }

  // One cell: the value, or "not recorded" with the report's reason as its title.
  function hubCell(column, row) {
    const td = make("td");
    const value = column.value(row);
    if (value === null || value === undefined) {
      td.textContent = NOT_RECORDED;
      td.className = "hub-none";
      const why = column.why ? column.why(row) : null;
      if (typeof why === "string" && why) td.title = shown(why);
    } else {
      td.textContent = column.text ? column.text(value) : visible(String(value));
      if (column.title) td.title = column.title(value);
    }
    return td;
  }

  // A folder with no lab record, or a file that could not be read: the
  // report's words across the columns after the folders.
  function hubSpan(row) {
    const td = make("td");
    td.colSpan = HUB_COLUMNS.length - 3;
    td.className = "hub-advice";
    if (row.state === "no_lab_record") {
      td.appendChild(make("span", "No lab record: " + (hubStr(row.advice) ? visible(row.advice) : "")));
      if (hubStr(row.fix_command)) {
        td.appendChild(make("span", " "));
        td.appendChild(make("code", visible(row.fix_command)));
        td.appendChild(make("span", " "));
        td.appendChild(copyButton(row.fix_command, "Copy " + row.fix_command));
      }
    } else {
      td.textContent = "Not read: " + (hubStr(row.error) ? shown(row.error) : "no reason given");
    }
    return td;
  }

  function hubRow(row) {
    const tr = make("tr");
    tr.className = "hub-row";
    if (!recorded(row)) {
      tr.classList.add("hub-row-bare");
      for (const column of HUB_COLUMNS.slice(0, 3)) tr.appendChild(hubCell(column, row));
      tr.appendChild(hubSpan(row));
      return tr;
    }
    // The name is a button, so a keyboard reaches the row; a click anywhere
    // on the row opens the character too.
    const name = make("td");
    const open = make("button", visible(row.label));
    open.type = "button";
    open.className = "hub-open";
    open.title = "Open in the Character tab";
    name.appendChild(open);
    tr.appendChild(name);
    for (const column of HUB_COLUMNS.slice(1)) tr.appendChild(hubCell(column, row));
    tr.addEventListener("click", () => openCharacter(row));
    return tr;
  }

  // Draws the table; `focusKey`, when given, puts focus back on that
  // column's sort button, which the redraw replaced.
  function drawHub(focusKey) {
    const box = byId("hub-table");
    const all = hub.report.rows.length;
    if (!all) {
      box.replaceChildren();
      hubSay(hub.note || "wowlab char list found no character folder. Run wowlab char list in a terminal for what to do.", Boolean(hub.note));
      return;
    }
    const rows = hubShown();
    const table = make("table");
    table.className = "char-table hub";
    const caption = make("caption", "Characters, sorted by " + sortWords());
    caption.className = "unseen";
    table.appendChild(caption);
    table.appendChild(hubHead());
    const body = make("tbody");
    for (const row of hubSorted(rows)) body.appendChild(hubRow(row));
    table.appendChild(body);
    box.replaceChildren(table);
    if (focusKey) {
      const at = HUB_COLUMNS.findIndex((c) => c.key === focusKey);
      const button = table.querySelectorAll("button.hub-sort")[at];
      if (button) button.focus();
    }
    const counted = rows.length === all
      ? (all === 1 ? "1 character folder." : all + " character folders.")
      : "Showing " + rows.length + " of " + all + " character folders.";
    hubSay(hub.note ? counted + " " + hub.note : counted, Boolean(hub.note));
  }

  byId("hub-flavor").addEventListener("change", () => {
    if (hub.report) drawHub();
  });
  byId("hub-class").addEventListener("change", () => {
    if (hub.report) drawHub();
  });

  // Opens the Character tab on `row`: the header's flavor moves to the
  // row's (with several flavors), and the picker takes that character once
  // its list is read. Focus goes to the picker, which names it. When the
  // tab's list does not hold that character (the header could not move to
  // its flavor), the status says so; no other character stands in for it.
  async function openCharacter(row) {
    await runFlavor.ready;
    const flavor = hubStr(row.flavor);
    const want = { flavor: flavor, account: row.account, realm_folder: row.realm_folder, label: row.label };
    character.want = want;
    if (flavor && runFlavor.picked && flavor !== runFlavor.picked) switchRunFlavor(flavor);
    showTab("character");
    await loadCharacters();
    character.want = null;
    const at = character.entries.findIndex((e) => readable(e) && isWanted(e, want));
    if (at < 0) {
      // A list that could not be read has said so already.
      if (character.asked) {
        character.note =
          visible(String(row.label)) + (flavor ? " (" + visible(flavor) + ")" : "") +
          " is not in this tab's list of characters, so it was not opened: the tab is reading another flavor.";
        charNote();
      }
    } else if (byId("char-picker").value !== String(at)) {
      // The list was read before: pick the character in it.
      byId("char-picker").value = String(at);
      pickCharacter(character.entries[at]);
    }
    byId("char-picker").focus();
  }

  // ─── tabs ───

  function showTab(name) {
    tabShown = name;
    for (const which of TABS) {
      const on = which === name;
      byId("tab-" + which).setAttribute("aria-selected", on ? "true" : "false");
      byId(which + "-view").hidden = !on;
    }
    if (name === "account") {
      // A run from the Commands tab may have changed the store or the
      // install since the report was read (M17-04): read it again.
      if (backup.stale && !backup.busy) {
        backup.stale = false;
        hub.asked = null;
      }
      loadHub();
    }
    if (name === "gaps") loadGaps();
    if (name === "character") loadCharacters();
  }
  // The tabs in their order; the first is shown when the page opens.
  const TABS = ["account", "character", "explorer", "gaps", "commands", "looks"];
  byId("tab-account").addEventListener("click", () => showTab("account"));
  byId("tab-character").addEventListener("click", () => showTab("character"));
  byId("tab-explorer").addEventListener("click", () => showTab("explorer"));
  byId("tab-gaps").addEventListener("click", () => showTab("gaps"));
  byId("tab-commands").addEventListener("click", () => showTab("commands"));
  byId("tab-looks").addEventListener("click", () => showTab("looks"));

  // ─── backups on the Account tab (M17-04, §19 CW-3) ───
  // One line per flavor the hub's report lists (`backups`): how old the
  // newest backup is, and, when the report says the game updated since it,
  // the prompt to take one. Take a backup now runs `snap create` for that flavor,
  // named with --flavor, which writes only into wowlab's own store, then
  // `snap diff` against the backup the line named, whose plan lines (each
  // key, macro and CVar by name) are shown as the CLI printed them. Restore
  // opens the Commands tab on `snap restore` with the backup's id filled
  // in: the plan and Confirm are that tab's, unchanged. Which snapshot is a
  // backup, and whether the game updated, are the report's; the page only
  // says how long ago a time was.

  const backup = {
    lines: [], // the report's `backups`, each line as the page last drew it
    error: "", // the report's `backup_error`
    busy: false, // a Take a backup now in progress; every button waits for it
    // A run from the Commands tab ended since the hub's report was read: it
    // may have taken a snapshot (snap create, or the one taken before a
    // change) or changed the install, so the Account tab reads it again.
    stale: false,
  };

  function backupSay(text, isError) {
    const node = byId("backup-status");
    announce(node, text, Boolean(isError));
    node.classList.toggle("error", Boolean(isError));
  }

  function takeBackups(report) {
    const lines = Array.isArray(report.backups) ? report.backups : [];
    backup.lines = lines.filter((b) => b && typeof b === "object" && typeof b.flavor === "string" && b.flavor !== "");
    backup.error = typeof report.backup_error === "string" ? report.backup_error : "";
    if (backup.error) backupSay(shown(backup.error), true);
    drawBackups();
  }

  // How long ago an ISO 8601 time was, in words, then the local time.
  function backupAge(iso) {
    const at = typeof iso === "string" ? Date.parse(iso) : NaN;
    if (Number.isNaN(at)) return null;
    const minutes = Math.floor(Math.max(0, Date.now() - at) / 60000);
    let words;
    if (minutes < 1) words = "just now";
    else if (minutes < 60) words = count(minutes, "minute", "minutes") + " ago";
    else if (minutes < 48 * 60) words = count(Math.floor(minutes / 60), "hour", "hours") + " ago";
    else words = count(Math.floor(minutes / 1440), "day", "days") + " ago";
    return words + " (" + localTime(at * 1e6) + ")";
  }

  function backupLine(line, several) {
    const item = make("li");
    item.className = "backup-line";
    const where = several ? visible(line.flavor) + ": " : "";
    let text;
    if (hubStr(line.snapshot_id)) {
      const age = backupAge(line.created_at);
      text = where + "Last backup " + (age || "at a time wowlab cannot read");
      if (hubStr(line.snapshot_version)) text += ", taken on version " + visible(line.snapshot_version);
      text += ".";
    } else {
      text = where + "No backup yet.";
    }
    item.appendChild(make("span", text));
    if (line.game_updated === true) {
      const now = hubStr(line.version) ? ": it is now version " + visible(line.version) : "";
      const prompt = make("strong", " The game updated since your last backup" + now + ".");
      prompt.className = "backup-updated";
      item.appendChild(prompt);
    }
    const actions = make("span");
    actions.className = "backup-actions";
    const take = make("button", "Take a backup now");
    take.type = "button";
    take.className = "backup-now";
    if (several) take.title = "Take a backup of " + visible(line.flavor);
    take.disabled = backup.busy;
    take.addEventListener("click", () => backUp(line));
    actions.appendChild(take);
    if (hubStr(line.snapshot_id)) {
      const restore = make("button", "Restore this backup…");
      restore.type = "button";
      restore.className = "backup-restore";
      restore.title = "Opens the Commands tab, where you see what restoring would change before anything does";
      restore.disabled = backup.busy;
      restore.addEventListener("click", () => restoreBackup(line));
      actions.appendChild(restore);
    }
    item.appendChild(actions);
    return item;
  }

  // The lines of the flavors the hub's flavor filter shows.
  function drawBackups() {
    const box = byId("backup-lines");
    const flavor = byId("hub-flavor").value;
    const several = backup.lines.length > 1;
    box.replaceChildren(
      ...backup.lines.filter((b) => !flavor || "f:" + b.flavor === flavor).map((b) => backupLine(b, several)),
    );
  }

  byId("hub-flavor").addEventListener("change", () => drawBackups());

  async function backUp(line) {
    if (backup.busy) return;
    backup.busy = true;
    drawBackups();
    const diff = byId("backup-diff");
    diff.hidden = true;
    diff.textContent = "";
    const name = visible(line.flavor);
    try {
      backupSay("Backing up " + name + "…", false);
      const made = await runJson(["snap", "create"], { flavor: line.flavor }, false, true);
      const manifest = made.data;
      if (!manifest || typeof manifest !== "object" || !hubStr(manifest.id)) {
        backupSay(made.error ? shown(made.error) : "wowlab snap create printed something this page does not know.", true);
        return;
      }
      const before = hubStr(line.snapshot_id);
      // The line names the backup just taken, as its manifest records it.
      line.snapshot_id = manifest.id;
      line.created_at = manifest.created_at;
      line.snapshot_version = hubStr(manifest.flavor_version);
      line.game_updated = false;
      // What snap create said beside its report: that wowlab could not tell
      // whether the client was closed, so saved data is as of its last
      // logout or /reload.
      const said = made.note ? " " + shown(made.note) : "";
      const done = "Backed up " + name + " as " + visible(manifest.id) + "." + said;
      if (!before) {
        backupSay(done + " It is the first backup of " + name + " here, so there is nothing to compare it with.", false);
        return;
      }
      const changes = await runJson(["snap", "diff"], { a: before, b: manifest.id }, false, true);
      const lines = changes.data && Array.isArray(changes.data.plan_lines)
        ? changes.data.plan_lines.filter((l) => typeof l === "string")
        : null;
      if (lines === null) {
        const why = changes.error ? shown(changes.error) : "wowlab snap diff printed something this page does not know.";
        backupSay(done + " What changed since the previous backup cannot be shown: " + why, true);
        return;
      }
      diff.textContent = lines.map(shown).join("\n");
      diff.hidden = lines.length === 0;
      backupSay(done + " What changed since the previous backup:", false);
    } finally {
      backup.busy = false;
      drawBackups();
    }
  }

  // Opens the Commands tab on `snap restore` with the backup's id: the
  // player asks for the plan there, and Confirm runs it, as for any change.
  function restoreBackup(line) {
    const button = Array.from(byId("commands").querySelectorAll("button"))
      .find((b) => b.textContent === "snap restore" && !b.disabled);
    if (!button) {
      backupSay("This page does not offer snap restore: run wowlab snap restore " + visible(line.snapshot_id) + " in a terminal.", true);
      return;
    }
    button.click();
    const field = inputs.find((entry) => entry.param.name === "snapshot_id");
    if (field) field.input.value = line.snapshot_id;
    showTab("commands");
    byId("run").focus();
  }

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
    }
    // A field the command declares required is marked in its label, which
    // is its accessible name, and as `required`, which a screen reader
    // announces; it is checked before Run (`missing`).
    if (param.required && param.kind !== "flag") {
      input.required = true;
      label.appendChild(make("span", " (required)"));
    }
    input.id = id;
    box.appendChild(label);
    box.appendChild(input);
    if (param.help) box.appendChild(make("small", param.help));
    inputs.push({ param: param, input: input, box: box });
    return box;
  }

  // ─── the confirm step (M14-02) ───

  // The note under a plan or a confirmed run. `#confirm-note` is a status
  // line inside `#confirm-box`: the box is shown first and the words set on
  // a later tick, because a status line that is hidden when its words change
  // is not read aloud. A note cleared in between is not written (EV-27).
  let confirmNoteSeq = 0;
  function confirmSay(text) {
    const seq = ++confirmNoteSeq;
    const note = byId("confirm-note");
    note.textContent = "";
    byId("confirm-box").hidden = false;
    setTimeout(() => {
      if (seq === confirmNoteSeq) note.textContent = text;
    }, 50);
  }

  function clearConfirm() {
    pending = null;
    confirmNoteSeq += 1;
    byId("confirm-box").hidden = true;
    byId("confirm-note").textContent = "";
    byId("plan-box").hidden = true;
    byId("plan-lines").textContent = "";
    byId("plan-lines").hidden = true;
    const button = byId("confirm");
    button.hidden = true;
    button.disabled = true;
    button.textContent = "Confirm";
    for (const { box } of inputs) box.classList.remove("needed");
  }

  // What the server said about a plan run: its note, and when it minted a
  // token, the plan under its heading, then Confirm named by the server's
  // label; the field to fill when it named one. The page adds no rule of its
  // own: no token, no plan heading and no Confirm. Focus goes to the plan's
  // heading, or to the field to fill.
  function showOffer(offer, command, params) {
    if (!offer || offer.stage !== "plan") return;
    const note = typeof offer.note === "string" ? offer.note : "";
    const token = typeof offer.token === "string" && offer.token ? offer.token : null;
    if (!note && !token) return;
    confirmSay(shown(note));
    if (token) {
      pending = { command: command, params: params, token: token };
      const lines = Array.isArray(offer.plan_lines)
        ? offer.plan_lines.filter((line) => typeof line === "string")
        : [];
      const planLines = byId("plan-lines");
      planLines.textContent = lines.map(shown).join("\n");
      planLines.hidden = lines.length === 0;
      byId("plan-box").hidden = false;
      const button = byId("confirm");
      if (typeof offer.label === "string" && offer.label) button.textContent = offer.label;
      button.hidden = false;
      button.disabled = false;
      byId("plan-heading").focus();
    }
    if (typeof offer.needs === "string") {
      for (const { param, input, box } of inputs) {
        if (param.name === offer.needs) {
          box.classList.add("needed");
          input.focus();
        }
      }
    }
  }

  function pick(command, button) {
    runSeq += 1;
    clearConfirm();
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
    byId("run").textContent = command.confirm ? "Show the plan" : "Run";
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

  // The first field the command declares required that is empty, or null.
  function missing() {
    for (const entry of inputs) {
      const { param, input } = entry;
      if (!param.required || param.kind === "flag") continue;
      const empty = param.multiple
        ? input.value.split("\n").every((line) => line === "")
        : input.value === "";
      if (empty) return entry;
    }
    return null;
  }

  // One run from the form, or, with `confirming`, the confirmed run of the
  // plan the server offered: the same command and parameters, and its token.
  // A form with a required field left empty is not run: the field is marked
  // and focused, and the status line says which.
  async function run(confirming) {
    if (current === null) return;
    const command = current;
    let body;
    if (confirming) {
      if (pending === null || pending.command !== command) return;
      body = { command: command.path, params: pending.params, confirm: pending.token };
    } else {
      const empty = missing();
      if (empty !== null) {
        for (const { box } of inputs) box.classList.remove("needed");
        empty.box.classList.add("needed");
        say(empty.param.label + " is required: fill it in first.", true);
        empty.input.focus();
        return;
      }
      body = { command: command.path, params: withFlavor(command.path, collect()) };
    }
    clearConfirm();
    const seq = ++runSeq;
    const button = byId("run");
    button.disabled = true;
    const what = "wowlab " + command.path.join(" ");
    say(confirming ? "Running " + what + " --yes…" : command.confirm ? "Planning " + what + "…" : "Running " + what + "…");
    showResult(null);
    byId("stderr").textContent = "";
    try {
      const reply = await fetch(api("/api/run"), {
        method: "POST",
        cache: "no-store",
        credentials: "omit",
        headers: keyed({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
      const result = await reply.json();
      if (reply.ok) backup.stale = true;
      if (seq !== runSeq) return;
      if (!reply.ok) {
        say("The server refused this request: " + (result.error || reply.status), true);
        return;
      }
      // A failed run's error report repeats the error line shown below it,
      // and names a class: it is not drawn.
      // A run of the confirm step keeps its report behind a closed
      // "Details": the plan above Confirm is what it shows first.
      showResult(errorReport(result) ? null : result.stdout, !result.confirm);
      byId("stderr").textContent = result.stderr;
      const stage = result.confirm && result.confirm.stage;
      // How the run ended, in the server's words (exit codes as words, and
      // "Plan" only for a plan that ended with its marker).
      const outcome = result.outcome && typeof result.outcome.text === "string" ? result.outcome : null;
      say(outcome ? outcome.text : "", Boolean(outcome && outcome.alert));
      // A field changed while the plan ran: the form no longer says what was
      // planned, so nothing is offered until the plan is shown again.
      if (stage === "plan" && JSON.stringify(withFlavor(command.path, collect())) === JSON.stringify(body.params)) {
        showOffer(result.confirm, command, body.params);
      } else if (stage === "plan") {
        confirmSay("A field changed while the plan ran: show the plan again to be offered Confirm.");
      } else if (stage === "confirmed" && typeof result.confirm.note === "string" && result.confirm.note) {
        // The journal record and the `wowlab undo` hint, or what to do after
        // a run stopped at its limit (M14-03); never a Confirm.
        confirmSay(shown(result.confirm.note));
      }
    } catch (error) {
      if (seq === runSeq) say("The request failed: " + error, true);
    } finally {
      if (seq === runSeq) {
        button.disabled = false;
        // After Confirm (which is gone now), focus goes to the sentence
        // that says how it ended, a refusal included.
        if (confirming) byId("status").focus();
      }
    }
  }

  async function load() {
    // With no key the server refuses, and the 403 below says what to do.
    let reply;
    try {
      reply = await fetch(api("/api/commands"), { cache: "no-store", credentials: "omit", headers: keyed() });
    } catch (error) {
      say("The server could not be reached: " + error, true);
      explorerSay("The server could not be reached: " + error, true);
      hubSay("The server could not be reached: " + error, true);
      return;
    }
    if (!reply.ok) {
      const refused = reply.status === 403 ? NO_SESSION : "The server refused the request (" + reply.status + ").";
      say(refused, true);
      explorerSay(refused, true);
      hubSay(refused, true);
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
    // The home tab first, then the whole install's tree, one run at a time.
    await loadHub();
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

  // Whether a failed run printed only its error report on stdout: an object
  // with exactly `kind`, `message` and `exit_code`, the run's own exit code,
  // and optionally `notes`, an array (the lines after the error).
  function errorReport(result) {
    if (result.exit_code === 0 || typeof result.stdout !== "string") return false;
    let value;
    try {
      value = JSON.parse(result.stdout);
    } catch (_) {
      return false;
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const keys = Object.keys(value).sort().join(",");
    const notes = keys === "exit_code,kind,message,notes" && Array.isArray(value.notes);
    return (keys === "exit_code,kind,message" || notes) && value.exit_code === result.exit_code;
  }

  // Shows one run's stdout under "Details": a tree when it is one JSON
  // document, else the text as printed. "Details" is open when `open`, else
  // closed (a run of the confirm step). null clears the result.
  function showResult(stdout, open) {
    const details = byId("result-details");
    const tree = byId("result-tree");
    const raw = byId("result-raw");
    tree.replaceChildren();
    tree.hidden = true;
    raw.hidden = true;
    raw.open = false;
    details.hidden = !stdout;
    details.open = Boolean(open);
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

  byId("run").addEventListener("click", () => run(false));
  byId("confirm").addEventListener("click", () => run(true));
  // A field changed after the plan: the plan shown is no longer the form's,
  // so its Confirm goes until the plan is shown again.
  for (const event of ["input", "change"]) {
    byId("fields").addEventListener(event, () => {
      if (pending !== null) clearConfirm();
    });
  }
  load();
})();
