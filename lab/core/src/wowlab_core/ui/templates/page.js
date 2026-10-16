"use strict";
// The page shell of `wowlab ui` (ADR-0029). It lists the commands the server
// read from the CLI's Click tree, draws a field for each declared parameter,
// and shows what one run printed. It holds no game rules and no file-map
// logic; every fact comes from the CLI's JSON. Nothing read from a run is
// ever parsed as HTML: text goes in through textContent only.
(function () {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const api = (path) => path + "?token=" + encodeURIComponent(token);
  const byId = (id) => document.getElementById(id);
  let current = null;
  let inputs = [];

  function make(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function say(text) {
    byId("status").textContent = text;
  }

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
      return;
    }
    if (!reply.ok) {
      say("The server refused the request (" + reply.status + ").");
      return;
    }
    const data = await reply.json();
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

  // Bidi controls (U+202A-U+202E, U+2066-U+2069) and zero-width characters
  // (U+200B-U+200D, U+2060, U+FEFF) could reorder or hide what a key or
  // value says; they are shown escaped. JSON.stringify already escapes
  // control characters and lone surrogates.
  const JT_HIDDEN = /[​-‍‪-‮⁠⁦-⁩﻿]/g;

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
