"""lookspage: the looks page, one self-contained HTML file (docs/LAB_PLAN.md §13.2, M11-07).

ADR-0027: a page is one ``.html`` file with inline CSS and plain JavaScript
kept here as template text, and the data as one embedded JSON block. No
build step, no server, no external request of any kind: a
Content-Security-Policy meta tag allows only the page's own inline style and
script, each pinned by its SHA-256, and nothing else (no fetch, no fonts, no
images, no forms). The JavaScript is a viewer: every verdict, finding and
remark on the page was computed in Python (``wowlab looks page`` in
``cli``, from ``looks`` and ``lookstore``) and is only displayed here, with
``textContent``, never parsed as markup.

This module knows nothing about builds or flavors (L6): the template is
static and the data block is whatever the caller hands over. ``render`` is
deterministic, so the same data gives the same bytes.

The page is written only at the path the caller names, or under the user
data directory (``default_page_path``); ``write_page`` refuses a path that
is, or is inside, an install, with ``lookstore.refuse_install`` before and
after creating the folder (§6.9, L1). The file appears whole or not at all.
"""

from __future__ import annotations

import base64
import hashlib
import stat
import uuid
from pathlib import Path

import platformdirs

from wowlab_core.lookstore import refuse_install

__all__ = [
    "DATA_ELEMENT_ID",
    "PageError",
    "content_security_policy",
    "default_page_path",
    "embedded_json",
    "render",
    "write_page",
]

DATA_ELEMENT_ID = "wowlab-data"
_DATA_OPEN = f'<script type="application/json" id="{DATA_ELEMENT_ID}">'
_DATA_CLOSE = "</script>"
_WHAT = "generated pages"


class PageError(Exception):
    """The page cannot be written where it was asked to go."""


def default_page_path() -> Path:
    """``<user data dir>/wowlab/pages/looks.html``."""
    return platformdirs.user_data_path("wowlab") / "pages" / "looks.html"


# ─── template ────────────────────────────────────────────────────────────────

_STYLE = """
:root {
  --gold: #ffd100; --gold-soft: #e0b54a; --gold-dim: #8c6d2c;
  --bg: #0c0a08; --panel: #17120c; --panel-hi: #211910; --edge: #4d3b1d;
  --text: #eadfc4; --muted: #a39679; --refused: #ff6450; --note: #9cc3ff;
  --unlock: #cf9bff; --ok: #7fd18b;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0; color: var(--text);
  background: radial-gradient(ellipse at top, #2a1f10 0%, #120d08 45%, var(--bg) 100%) fixed;
  font: 15px/1.5 -apple-system, "Segoe UI", system-ui, "Helvetica Neue", Arial, sans-serif;
}
h1, h2, h3, .serif {
  font-family: "Palatino Linotype", Palatino, "Book Antiqua", Georgia, "Times New Roman", serif;
  color: var(--gold); text-shadow: 0 1px 2px #000, 0 0 12px rgba(255, 209, 0, 0.15);
  letter-spacing: 0.02em; font-weight: 600;
}
h1 { margin: 0; font-size: 30px; }
h2 { margin: 0 0 10px; font-size: 21px; }
h3 { margin: 0; font-size: 17px; }
header, main, footer { max-width: 1280px; margin: 0 auto; padding: 16px 20px; }
header { border-bottom: 1px solid var(--edge); }
.sub { color: var(--muted); margin-top: 4px; }
.panel {
  background: linear-gradient(180deg, var(--panel-hi), var(--panel));
  border: 1px solid var(--edge); border-radius: 6px; padding: 14px 16px; margin: 14px 0;
  box-shadow: inset 0 0 0 1px #000, 0 3px 12px rgba(0, 0, 0, 0.55);
}
.caveats p { margin: 6px 0; }
.caveats .legend { color: var(--muted); }
.remark { color: var(--gold-soft); }
nav.tabs { display: flex; gap: 8px; margin-top: 14px; }
button, select, input {
  font: inherit; color: var(--text); background: #0f0c08;
  border: 1px solid var(--edge); border-radius: 4px; padding: 6px 10px;
}
button { cursor: pointer; }
button:hover, select:hover, input:hover { border-color: var(--gold-dim); }
button.on {
  color: #1a1206; border-color: var(--gold);
  background: linear-gradient(180deg, #ffe066, #d9a800);
}
.browse { display: grid; grid-template-columns: 240px 1fr; gap: 16px; }
aside h2 { font-size: 16px; line-height: 1.3; }
#view-heading { margin: 14px 0 0; font-size: 19px; }
.races { display: flex; flex-direction: column; gap: 6px; }
.races button { text-align: left; }
.races .meta { display: block; color: var(--muted); font-size: 12px; }
.races button.on .meta { color: #3a2a08; }
.controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
.controls label { display: flex; flex-direction: column; gap: 4px; color: var(--muted); }
.controls input { min-width: 220px; }
.viewnotes { margin: 10px 0 0; padding-left: 18px; color: var(--gold-soft); }
details.option {
  border: 1px solid var(--edge); border-radius: 5px; margin: 8px 0;
  background: rgba(0, 0, 0, 0.25);
}
details.option > summary {
  cursor: pointer; padding: 8px 12px; list-style-position: inside;
}
details.option[open] > summary { border-bottom: 1px solid var(--edge); }
.oname { color: var(--gold); font-weight: 600; }
.oid, .cid { color: var(--muted); font-variant-numeric: tabular-nums; }
.tag {
  display: inline-block; font-size: 12px; border: 1px solid var(--edge); border-radius: 10px;
  padding: 0 8px; margin-left: 6px; color: var(--muted);
}
.tag.form { color: var(--unlock); border-color: #5b3f78; }
.tag.count-refused { color: var(--refused); border-color: #6d2a22; }
.tag.count-note { color: var(--note); border-color: #2c4468; }
.obody { padding: 8px 12px 12px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; vertical-align: top; padding: 5px 8px; border-top: 1px solid #2b2114; }
th { color: var(--gold-soft); font-weight: 600; border-top: none; }
td.cid { width: 90px; }
td.cname { width: 30%; }
ul.findings { list-style: none; margin: 0; padding: 0; }
ul.findings li { margin: 2px 0; }
.label { font-weight: 600; margin-right: 6px; }
li.refusal .label { color: var(--refused); }
li.note .label { color: var(--note); }
li.k-needs_unlock .label { color: var(--unlock); }
.none { color: var(--muted); }
.look h3 { display: flex; align-items: center; gap: 8px; }
.verdict { font-size: 13px; padding: 1px 10px; border-radius: 10px; border: 1px solid; }
.verdict.refused { color: var(--refused); border-color: #6d2a22; }
.verdict.fine { color: var(--ok); border-color: #2d5a34; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: 2px 14px; margin: 10px 0; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; overflow-wrap: anywhere; }
ul.choices { margin: 4px 0 8px; padding-left: 18px; }
.damaged li { color: var(--refused); }
footer { color: var(--muted); font-size: 13px; border-top: 1px solid var(--edge); }
[hidden] { display: none !important; }
@media (max-width: 800px) { .browse { grid-template-columns: 1fr; } }
"""

_SCRIPT = """
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("wowlab-data").textContent);

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function byId(id) { return document.getElementById(id); }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  function findingList(refusals, notes) {
    var list = el("ul", "findings");
    refusals.forEach(function (f) {
      var item = el("li", "refusal k-" + f.kind);
      item.appendChild(el("span", "label", "refused:"));
      item.appendChild(document.createTextNode(f.message));
      list.appendChild(item);
    });
    notes.forEach(function (f) {
      var item = el("li", "note k-" + f.kind);
      item.appendChild(el("span", "label", "note:"));
      item.appendChild(document.createTextNode(f.message));
      list.appendChild(item);
    });
    return list;
  }

  function paragraphs(node, lines, cls) {
    lines.forEach(function (line) { node.appendChild(el("p", cls, line)); });
  }

  /* header and caveats */
  byId("build").textContent = data.build;
  paragraphs(byId("remarks"), data.remarks, "remark");
  paragraphs(byId("caveats"), data.notes, "");
  paragraphs(byId("caveats"), data.legend, "legend");
  byId("footer-build").textContent = data.build;

  /* tabs */
  var tabs = { browse: byId("tab-browse"), looks: byId("tab-looks") };
  var sections = { browse: byId("browse"), looks: byId("looks") };
  function show(which) {
    Object.keys(tabs).forEach(function (k) {
      tabs[k].classList.toggle("on", k === which);
      sections[k].hidden = k !== which;
    });
  }
  tabs.browse.addEventListener("click", function () { show("browse"); });
  tabs.looks.addEventListener("click", function () { show("looks"); });
  tabs.looks.textContent = "Saved looks (" + data.looks.length + ")";

  /* browse: race, body type, class */
  var views = {};
  data.views.forEach(function (v) {
    views[v.race_id + ":" + v.body_type + ":" + (v.class_id === null ? "" : v.class_id)] = v;
  });
  var state = { race: null, body: null, cls: "" };
  var raceButtons = {};
  var raceList = byId("races");
  var bodySelect = byId("body");
  var classSelect = byId("class");
  var filter = byId("filter");

  byId("races-heading").textContent = data.races_heading;
  data.races.forEach(function (entry) {
    var button = el("button", "", entry.race.name);
    button.type = "button";
    button.appendChild(el("span", "meta", entry.label));
    button.addEventListener("click", function () { pickRace(entry); });
    raceButtons[entry.race.id] = button;
    raceList.appendChild(button);
  });

  classSelect.appendChild(el("option", "", data.no_class_label)).value = "";
  data.classes.forEach(function (c) {
    classSelect.appendChild(el("option", "", c.name + " (" + c.id + ")")).value = String(c.id);
  });
  classSelect.addEventListener("change", function () { state.cls = classSelect.value; draw(); });
  bodySelect.addEventListener("change", function () { state.body = bodySelect.value; draw(); });
  filter.addEventListener("input", function () { applyFilter(); });

  function pickRace(entry) {
    state.race = entry;
    Object.keys(raceButtons).forEach(function (id) {
      raceButtons[id].classList.toggle("on", Number(id) === entry.race.id);
    });
    clear(bodySelect);
    entry.race.body_types.forEach(function (b) {
      var label = "body type " + b.body_type + " (model " + b.chr_model_id + ")";
      bodySelect.appendChild(el("option", "", label)).value = String(b.body_type);
    });
    state.body = entry.race.body_types.length ? String(entry.race.body_types[0].body_type) : null;
    draw();
  }

  function optionCard(option) {
    var card = el("details", "option");
    var refusals = option.refusals.length;
    var notes = option.notes.length;
    option.choices.forEach(function (c) { refusals += c.refusals.length; notes += c.notes.length; });
    var summary = el("summary");
    summary.appendChild(el("span", "oname", option.name));
    summary.appendChild(el("span", "oid", " option " + option.id));
    if (option.category !== null) { summary.appendChild(el("span", "tag", option.category)); }
    if (option.form_or_pet) { summary.appendChild(el("span", "tag form", "form or pet option")); }
    summary.appendChild(el("span", "tag", plural(option.choices.length, "choice")));
    if (refusals) { summary.appendChild(el("span", "tag count-refused", refusals + " refused")); }
    if (notes) { summary.appendChild(el("span", "tag count-note", plural(notes, "note"))); }
    card.appendChild(summary);
    var body = el("div", "obody");
    if (option.refusals.length || option.notes.length) {
      body.appendChild(findingList(option.refusals, option.notes));
    }
    var table = el("table");
    var head = el("tr");
    ["choice", "name", "what the tables say"].forEach(function (t) { head.appendChild(el("th", "", t)); });
    table.appendChild(head);
    option.choices.forEach(function (c) {
      var row = el("tr");
      row.appendChild(el("td", "cid", c.id));
      row.appendChild(el("td", "cname", c.name));
      var cell = el("td");
      if (c.refusals.length || c.notes.length) {
        cell.appendChild(findingList(c.refusals, c.notes));
      } else {
        cell.appendChild(el("span", "none", data.no_findings_label));
      }
      row.appendChild(cell);
      table.appendChild(row);
    });
    body.appendChild(table);
    card.appendChild(body);
    card.dataset.search = (option.name + " " + (option.category || "") + " " + option.id).toLowerCase();
    return card;
  }

  function applyFilter() {
    var text = filter.value.trim().toLowerCase();
    var cards = byId("options").children;
    for (var i = 0; i < cards.length; i += 1) {
      cards[i].hidden = text !== "" && cards[i].dataset.search.indexOf(text) < 0;
    }
  }

  function draw() {
    var heading = byId("view-heading");
    var notes = byId("view-notes");
    var list = byId("options");
    clear(notes);
    clear(list);
    if (!state.race || state.body === null) { heading.textContent = data.no_race_label; return; }
    var view = views[state.race.race.id + ":" + state.body + ":" + state.cls];
    if (!view) { heading.textContent = data.no_race_label; return; }
    heading.textContent = view.heading;
    view.notes.forEach(function (n) { notes.appendChild(el("li", "", n)); });
    if (!view.options.length) { list.appendChild(el("p", "none", data.no_options_label)); }
    view.options.forEach(function (index) { list.appendChild(optionCard(data.options[index])); });
    applyFilter();
  }

  /* saved looks */
  var looks = byId("looks-list");
  byId("looks-directory").textContent = data.looks_directory;
  if (!data.looks.length && !data.damaged.length) {
    looks.appendChild(el("p", "none", data.no_looks_label));
  }
  data.looks.forEach(function (entry) {
    var r = entry.report;
    var card = el("section", "panel look");
    var title = el("h3", "", r.name);
    title.appendChild(el("span", "verdict " + (r.refused ? "refused" : "fine"), entry.verdict));
    card.appendChild(title);
    var facts = el("dl", "facts");
    entry.facts.forEach(function (pair) {
      facts.appendChild(el("dt", "", pair[0]));
      facts.appendChild(el("dd", "", pair[1]));
    });
    card.appendChild(facts);
    var choices = el("ul", "choices");
    entry.choice_lines.forEach(function (line) { choices.appendChild(el("li", "", line)); });
    if (entry.choice_lines.length) { card.appendChild(choices); }
    if (r.refusals.length || r.notes.length) { card.appendChild(findingList(r.refusals, r.notes)); }
    paragraphs(card, r.remarks, "remark");
    looks.appendChild(card);
  });
  if (data.damaged.length) {
    var damaged = el("section", "panel damaged");
    damaged.appendChild(el("h3", "", data.damaged_heading));
    var items = el("ul");
    data.damaged.forEach(function (d) { items.appendChild(el("li", "", d.file + ": " + d.error)); });
    damaged.appendChild(items);
    looks.appendChild(damaged);
  }

  show("browse");
  if (data.races.length) { pickRace(data.races[0]); } else { draw(); }
})();
"""

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="@CSP@">
<meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wowlab looks</title>
<style>@STYLE@</style>
</head>
<body>
<header>
<h1>wowlab &middot; Looks</h1>
<div class="sub">Character customization from build <span id="build"></span>'s tables</div>
<div id="remarks"></div>
<nav class="tabs">
<button type="button" id="tab-browse">Races and options</button>
<button type="button" id="tab-looks">Saved looks</button>
</nav>
</header>
<main>
<noscript><div class="panel">This page shows its data with JavaScript, which is off.
The data is the JSON block in this file.</div></noscript>
<section class="panel caveats" id="caveats"></section>
<section id="browse">
<div class="browse">
<aside class="panel"><h2 id="races-heading"></h2><div class="races" id="races"></div></aside>
<div>
<div class="panel">
<div class="controls">
<label>Body type <select id="body"></select></label>
<label>Class <select id="class"></select></label>
<label>Filter options <input id="filter" type="search" placeholder="name, category or id"></label>
</div>
<h2 id="view-heading" class="serif"></h2>
<ul class="viewnotes" id="view-notes"></ul>
</div>
<div id="options"></div>
</div>
</div>
</section>
<section id="looks" hidden>
<div class="panel"><h2>Saved looks</h2><div class="sub">In <span id="looks-directory"></span></div></div>
<div id="looks-list"></div>
</section>
</main>
<footer>Generated by wowlab from build <span id="footer-build"></span>'s tables.
One self-contained file: no network requests (its Content-Security-Policy forbids them).</footer>
@DATA@
<script>@SCRIPT@</script>
</body>
</html>
"""


def _sha256(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


def content_security_policy() -> str:
    """Nothing but the page's own inline style and script, each by its hash."""
    return "; ".join(
        (
            "default-src 'none'",
            f"script-src '{_sha256(_SCRIPT)}'",
            f"style-src '{_sha256(_STYLE)}'",
            "img-src 'none'",
            "font-src 'none'",
            "connect-src 'none'",
            "media-src 'none'",
            "object-src 'none'",
            "frame-src 'none'",
            "worker-src 'none'",
            "manifest-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
        )
    )


def _script_safe(json_text: str) -> str:
    """JSON text that cannot end its ``<script>`` element or open a comment.
    ``<``, ``>`` and ``&`` only occur inside JSON strings, where their
    ``\\u`` escapes decode to the same text."""
    return (
        json_text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(chr(0x2028), "\\u2028")
        .replace(chr(0x2029), "\\u2029")
    )


def render(data_json: str) -> str:
    """The page for ``data_json``, a JSON document the caller built.

    The JSON is embedded as the one ``application/json`` block, escaped so it
    cannot close its element; ``embedded_json`` reads it back."""
    return (
        _TEMPLATE.replace("@CSP@", content_security_policy())
        .replace("@STYLE@", _STYLE)
        .replace("@SCRIPT@", _SCRIPT)
        .replace("@DATA@", _DATA_OPEN + _script_safe(data_json) + _DATA_CLOSE)
    )


def embedded_json(page: str) -> str:
    """The JSON block of a page ``render`` made."""
    start = page.index(_DATA_OPEN) + len(_DATA_OPEN)
    return page[start : page.index(_DATA_CLOSE, start)]


def write_page(page: str, out: Path) -> Path:
    """Write ``page`` to ``out`` (resolved, so a link is followed to its file)
    and return where it went. Refused, with nothing written, when ``out`` is
    an install or inside one, is a directory, or is something other than a
    regular file. An existing page there is replaced whole."""
    target = Path(out).absolute()
    refuse_install(target, _WHAT)
    final = target.resolve()
    if final.is_dir():
        raise PageError(f"{target} is a directory; --out names the page file to write")
    try:
        mode = final.stat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None and not stat.S_ISREG(mode):
        raise PageError(f"{target} exists and is not a regular file")
    folder = final.parent
    refuse_install(folder, _WHAT)
    folder.mkdir(parents=True, exist_ok=True)
    refuse_install(folder, _WHAT)
    tmp = folder / f".{final.name}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp.open("xb") as handle:
            handle.write(page.encode("utf-8"))
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)
    return final
