# Probe from review of m22/05-live-companion; reproduces the Live view drawing nothing, for good, after the player leaves it and comes back while a recap runs
"""M22-05: the Live view "refreshes on a change and needs no click".

`liveRecap` sets `liveState.again` when a poll asks for a recap while one
runs, and runs it once more in its `finally` only when the view was not
left meanwhile (`seq === liveState.seq`). `stopLive` bumps `seq` and clears
`seen` but not `again` or `running`. So: open Live, a recap starts (a
subprocess that may read 256 MiB of logs), the player clicks another tab
and back before it returns. The new poll sees a new key (`seen` was
cleared), calls `liveRecap`, finds `running` and sets `again`. The old run
returns, draws nothing (its `seq` is stale) and does not rerun (same
reason). `again` stays true, `seen` already holds the key, so no later poll
asks again: the match and session panels stay empty until a file changes.

The Live block of `page.js` is run under Node with stubbed DOM, fetch and
`runJson` (the block only, sliced by its section comments). Positive
control: the same run without leaving the view draws the recap.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from wowlab_core import ui

PAGE = Path(ui.__file__).resolve().parent / "templates" / "page.js"

_SCRIPT = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const block = src.slice(src.indexOf("// ─── the Live view"), src.indexOf("// ─── the crew book"));
const els = {};
function byId(id) { return (els[id] = els[id] || { text: "", kids: [], replaceChildren(...k) { this.kids = k; } }); }
function announce(el, text) { el.text = text; }
function make(tag, text) { return text; }
function visible(s) { return s; }
function api(p) { return p; }
function keyed() { return {}; }
const runFlavor = { ready: Promise.resolve(), picked: "" };
const document = { hidden: false };
const pendingRecap = [];
let recapCalls = 0;
function runJson() { recapCalls += 1; return new Promise((resolve) => pendingRecap.push(resolve)); }
async function fetch() {
  const body = { found: true, poll_seconds: 3, watch: { flavor_folder: "f", saved: { newest_ns: 1 }, log: null, cost_us: 10 } };
  return { ok: true, json: async () => body };
}
const timers = [];
function setTimeout(fn) { timers.push(fn); return timers.length; }
function clearTimeout() {}
const tick = () => new Promise((r) => global.setImmediate(r));
eval(block + "\nglobalThis.L = { startLive, stopLive, liveState };");
(async () => {
  const { startLive, stopLive } = globalThis.L;
  startLive();
  for (let i = 0; i < 5; i++) await tick();
  if (process.argv[3] === "leave") { stopLive(); startLive(); for (let i = 0; i < 5; i++) await tick(); }
  pendingRecap.shift()({ data: { lines: ["match line", "", "session line"] } });
  for (let i = 0; i < 5; i++) await tick();
  for (let n = 0; n < 3; n++) {   // three more polls, nothing changed
    const t = timers.pop(); if (t) t();
    for (let i = 0; i < 5; i++) await tick();
    while (pendingRecap.length) { pendingRecap.shift()({ data: { lines: ["match line", "", "session line"] } }); for (let i = 0; i < 5; i++) await tick(); }
  }
  console.log(JSON.stringify({ recapCalls, match: byId("live-match-lines").kids }));
})();
"""


def _run(tmp_path: Path, how: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    script = tmp_path / "m22-05-live-probe.js"
    script.write_text(_SCRIPT, encoding="utf-8")
    done = subprocess.run(
        [node, str(script), str(PAGE), how],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    got: dict[str, object] = json.loads(done.stdout)
    return got


def test_positive_control_staying_on_the_view_draws_the_recap(tmp_path: Path) -> None:
    assert _run(tmp_path, "stay")["match"] == ["match line"]


def test_leaving_and_coming_back_during_a_recap_still_draws_it(tmp_path: Path) -> None:
    got = _run(tmp_path, "leave")
    assert got["match"] == ["match line"], got
