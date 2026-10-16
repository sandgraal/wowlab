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
      if (!param.refused) fields.appendChild(field(param));
    }
    byId("run").disabled = false;
    byId("stdout").textContent = "";
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
    byId("stdout").textContent = "";
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
      let out = result.stdout;
      try {
        out = JSON.stringify(JSON.parse(out), null, 2);
      } catch (_) {
        // Not one JSON document: shown as printed.
      }
      byId("stdout").textContent = out;
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
        button.title = "Not offered: " + command.excluded;
      } else {
        button.title = command.help || "";
        button.addEventListener("click", () => pick(command, button));
      }
      list.appendChild(button);
    }
  }

  byId("run").addEventListener("click", run);
  load();
})();
