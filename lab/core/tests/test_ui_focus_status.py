"""Focus and announcements in `wowlab ui` (M16-29; EV-27, from a11y:F1, F2,
F3 and F11 of the October 2026 outside evaluation).

What is graded:
- F1: a tree redraw (opening or closing a folder, a filter) puts focus back
  on the same button, twisty or name, of the row with the same `data-path`,
  instead of letting it fall to the page.
- F2: no pane is a live region. The status lines are `role="status"`; a
  refusal or an error turns its line into `role="alert"`, and the next plain
  message turns it back.
- F3: the tree is plain nested lists of buttons (no `tree`, `treeitem` or
  `group` role and no `aria-level`), each folder's twisty carries
  `aria-expanded`, and picking a row moves focus to the detail title.
- F11: every Copy reports to one shared status line, `#copy-status`, whose
  words stay after every timer has run; a failed Copy keeps its words on
  the button too.

The page runs under Node in the stand-in DOM of `test_ui_explorer.py`, fed
what the real CLI printed for the captured install; this file adds its own
steps and, as `test_ui_character.py` does, replies the scenario holds (a
refusal). A held refusal is constructed and says so in the test id.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import test_ui_explorer as first_stage
from test_ui_explorer import _HARNESS, _TEMPLATES, _node, _scenario, _send

home = first_stage.home
install = first_stage.install
server = first_stage.server

CONFIG = "WTF/Config.wtf"

# ─── the harness: the first stage's, with this file's steps ──────────────────

_VIEW = r"""
function describe(e) {
  if (!e) return null;
  const row = e.closest ? e.closest(".row") : null;
  return { id: e.id || "", tag: e.tagName, cls: e.className, inTree: ids.tree.contains(e),
    path: row ? row.dataset.path : null, expanded: e.getAttribute("aria-expanded") };
}
function line(id) {
  return { text: ids[id].textContent, role: ids[id].getAttribute("role") };
}
function focusView() {
  return { active: describe(documentStub.activeElement), explain: line("explain-status"),
    status: line("status"), copy: line("copy-status") };
}
// Replies the scenario holds that are not a clean exit: a refusal (403),
// matched on the command and, when it names them, the end of its `path` and
// whether the run is a confirmed one.
// Every non-empty write to `#confirm-note`, with whether `#confirm-box` was
// hidden at that moment: a status line written while hidden is not read.
const noteWrites = [];
{
  const own = Object.getOwnPropertyDescriptor(El.prototype, "textContent");
  Object.defineProperty(ids["confirm-note"], "textContent", { configurable: true,
    get() { return own.get.call(this); },
    set(v) {
      if (String(v) !== "") noteWrites.push({ text: String(v), boxHidden: ids["confirm-box"].hidden });
      own.set.call(this, v);
    } });
}
const plainFetch = ctx.fetch;
ctx.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  if (body && String(url).startsWith("/api/run?")) {
    for (const held of scenario.held || []) {
      const path = String((body.params || {}).path || "").split(scenario.sep).join("/");
      if (canon(held.command) === canon(body.command)
        && (held.path_end === undefined || path.endsWith(held.path_end))
        && (held.confirmed === undefined || held.confirmed === Boolean(body.confirm))) {
        requests.push({ url: String(url), body: body });
        return answer(held.reply, held.refused ? false : true);
      }
    }
  }
  return plainFetch(url, opts);
};
"""

_STEPS = r"""
      } else if (step.toggle_focused !== undefined) {
        const li = liOf(step.toggle_focused);
        if (!li) { out.steps.push({ missing: step.toggle_focused }); continue; }
        const twisty = twistyOf(li);
        const before = expandedOf(li);
        twisty.focus(); twisty.fire("click"); await tick();
        out.steps.push(Object.assign(focusView(), { before: before }));
      } else if (step.filter_from_name !== undefined) {
        const li = liOf(step.filter_from_name.path);
        if (!li) { out.steps.push({ missing: step.filter_from_name.path }); continue; }
        rowOf(li).children.find((c) => has(c, "name")).focus();
        ids["text-filter"].value = step.filter_from_name.text; ids["text-filter"].fire("input"); await tick();
        out.steps.push(focusView());
      } else if (step.pick_row !== undefined) {
        const li = liOf(step.pick_row);
        if (!li) { out.steps.push({ missing: step.pick_row }); continue; }
        const name = rowOf(li).children.find((c) => has(c, "name"));
        name.focus(); name.fire("click"); await tick();
        out.steps.push(Object.assign(focusView(), { rowRoles: items().map((e) => e.getAttribute("role")) }));
      } else if (step.copy_at !== undefined) {
        const buttons = inPane().filter((e) => e.tagName === "button" && has(e, "copy"));
        if (!buttons[step.copy_at]) { out.steps.push({ missing: "copy " + step.copy_at }); continue; }
        navigator.clipboard.writeText = step.fail
          ? async () => { throw new Error("denied"); }
          : async (t) => { clipboard.push(String(t)); };
        buttons[step.copy_at].fire("click"); await tick();
        out.steps.push(Object.assign(focusView(), { button: buttons[step.copy_at].textContent,
          pending: timers.length }));
      } else if (step.run_command !== undefined) {
        const button = ids.commands.all().find((e) => e.tagName === "button" && e.textContent === step.run_command);
        if (!button) { out.steps.push({ missing: step.run_command }); continue; }
        button.fire("click"); await tick();
        ids.run.fire("click"); await tick();
        out.steps.push(focusView());
      } else if (step.confirm_click !== undefined) {
        ids.confirm.fire("click"); await tick();
        out.steps.push(Object.assign(focusView(), { noteWrites: noteWrites.slice(),
          note: ids["confirm-note"].textContent, boxHidden: ids["confirm-box"].hidden }));
"""

_STEP_ANCHOR = "      } else if (step.preview !== undefined) {\n"
_VIEW_ANCHOR = "vm.createContext(ctx);\n"


def _harness() -> str:
    assert _HARNESS.count(_STEP_ANCHOR) == 1 and _HARNESS.count(_VIEW_ANCHOR) == 1
    text = _HARNESS.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_STEP_ANCHOR, _STEPS.lstrip("\n") + _STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m16-29-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m16-29-scenario.json"
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


def _html() -> str:
    return (_TEMPLATES / "page.html").read_text(encoding="utf-8")


def _tag(html: str, element_id: str) -> str:
    found = re.search(r'<[a-z0-9]+\b[^>]*\bid="' + re.escape(element_id) + r'"[^>]*>', html)
    assert found, element_id
    return found.group(0)


# ─── the page as written ─────────────────────────────────────────────────────


def test_no_pane_is_a_live_region_and_each_status_line_is_a_status() -> None:
    """F2: `#explain` and `#detail` lose `aria-live` (no element keeps one);
    the status lines are `role="status"`, and the shared Copy line exists
    once, visually hidden."""
    html = _html()
    assert "aria-live" not in html
    for element_id in ("explain", "detail"):
        assert "role=" not in _tag(html, element_id), element_id
    for element_id in (
        "status",
        "explain-status",
        "confirm-note",
        "explorer-status",
        "gaps-status",
        "char-status",
        "looks-status",
        "copy-status",
    ):
        assert 'role="status"' in _tag(html, element_id), element_id
    assert html.count('id="copy-status"') == 1
    assert 'class="unseen"' in _tag(html, "copy-status")
    assert 'tabindex="-1"' in _tag(html, "explain-title")


def test_the_tree_promises_no_tree_keyboard() -> None:
    """F3, option (b): no `tree`, `treeitem` or `group` role and no
    `aria-level`, in the page or the script; the class names the restyle
    keeps are still drawn."""
    html = _html()
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert 'role="tree"' not in html
    tree = _tag(html, "tree")
    assert 'class="tree"' in tree and 'aria-label="Files in the install"' in tree
    for word in ('"treeitem"', '"group"', "aria-level", "aria-selected"):
        assert word not in script.replace('"aria-selected", on ? "true"', ""), word
    # The one role the script sets is a status line's: status or alert.
    roles = re.findall(r'setAttribute\("role", ([^)]*)\)', script)
    assert roles == ['isAlert ? "alert" : "status"'], roles
    for cls in ('"row"', '"twisty"', '"twisty-gap"', '"note"'):
        assert cls in script, cls
    assert 'folder ? "name folder" : "name"' in script


# ─── the page's behaviour, under Node ────────────────────────────────────────


def test_focus_stays_on_the_twisty_across_a_redraw(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """F1: closing and opening a folder from the keyboard keeps focus on that
    folder's twisty (same `data-path`, still in the tree), and the twisty's
    `aria-expanded` follows."""
    steps: list[dict[str, Any]] = [{"toggle_focused": "WTF"}, {"toggle_focused": "WTF"}]
    scenario, _ = _scenario(server, [], steps)
    out = _drive(tmp_path / "node", scenario)
    closed, opened = out["steps"]
    for step, was, now in ((closed, "true", "false"), (opened, "false", "true")):
        assert "missing" not in step, step
        assert step["before"] == was, step
        active = step["active"]
        assert active is not None and active["inTree"], active
        assert active["cls"] == "twisty", active
        assert active["path"] == _row_path(scenario, "WTF"), active
        assert active["expanded"] == now, active


def _row_path(scenario: dict[str, Any], rel: str) -> str:
    """A row's `data-path` with the whole install drawn: from the root."""
    return str(scenario["first_flavor"]) + "/" + rel


def test_focus_stays_on_the_name_across_a_filter_redraw(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """F1: a redraw while a row's name has focus (here a filter that still
    shows the row) puts focus back on that row's name."""
    steps: list[dict[str, Any]] = [{"filter_from_name": {"path": CONFIG, "text": "config"}}]
    scenario, _ = _scenario(server, [], steps)
    out = _drive(tmp_path / "node", scenario)
    active = out["steps"][0]["active"]
    assert active is not None and active["inTree"], out["steps"][0]
    assert active["cls"] == "name", active
    assert active["path"] == _row_path(scenario, CONFIG), active


def test_a_pick_moves_focus_to_the_detail_title(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """F3: after a pick, focus is on `#explain-title`, so the details are the
    next Tab stops; the rows carry no role; the explain status is a status."""
    scenario, _ = _scenario(server, [CONFIG], [{"pick_row": CONFIG}])
    out = _drive(tmp_path / "node", scenario)
    step = out["steps"][0]
    assert step["active"]["id"] == "explain-title", step
    assert set(step["rowRoles"]) == {None}, step["rowRoles"]
    assert step["explain"]["role"] == "status", step


def test_a_refused_explain_is_an_alert_then_a_status_again_constructed(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """F2: a refusal turns `#explain-status` into an alert with the refusal's
    sentence; the next pick, answered, turns it back into a status. The
    refusal is a held reply (constructed); the second pick is the real
    `explain`."""
    other = "WTF/Account"
    scenario, _ = _scenario(server, [CONFIG], [{"pick_row": other}, {"pick_row": CONFIG}])
    scenario["held"] = [
        {
            "command": ["explain"],
            "path_end": "/" + other,
            "refused": True,
            "reply": {"error": "the server refused this run"},
        }
    ]
    out = _drive(tmp_path / "node", scenario)
    refused, answered = out["steps"]
    assert refused["explain"] == {
        "text": "Refused: the server refused this run",
        "role": "alert",
    }, refused
    assert answered["explain"]["role"] == "status", answered


def test_every_copy_reports_to_one_status_line_that_stays(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """F11: Copy (worked or not) writes its words to `#copy-status`, and the
    words are still there after every pending timer has run. "Copied" goes
    back to "Copy" on the button; a failure stays on the button."""
    steps: list[dict[str, Any]] = [
        {"pick_row": CONFIG},
        {"copy_at": 0},
        {"copy_at": 1, "fail": True},
        {"copy_at": 0},
    ]
    scenario, _ = _scenario(server, [CONFIG], steps)
    out = _drive(tmp_path / "node", scenario)
    worked, failed, again = out["steps"][1:]
    failure = "Copy did not work in this browser; use Copy in another browser"
    for step in (worked, failed, again):
        assert "missing" not in step, step
        assert step["pending"] == 0, "every timer has run"
    assert worked["copy"]["text"] == "Copied" and worked["button"] == "Copy", worked
    assert failed["copy"]["text"] == failure and failed["button"] == failure, failed
    assert again["copy"]["text"] == "Copied", again
    # Nothing else announces a Copy: the explain status stays as it was.
    assert worked["explain"]["text"] == failed["explain"]["text"] == ""


def test_a_refused_run_is_an_alert_and_a_clean_run_a_status_constructed(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """F2 on the Commands tab: a refused run's line is an alert, a clean run's
    line a status. The refusal is a held reply (constructed)."""
    listed = _send(server, "GET", "/api/commands").json()
    reads = [
        " ".join(c["path"])
        for c in listed["commands"]
        if not c.get("excluded") and not c.get("confirm") and c["path"] != ["install", "show"]
    ]
    assert reads, "the server offers read commands"
    refused = reads[0]
    steps: list[dict[str, Any]] = [{"run_command": refused}, {"run_command": "install show"}]
    scenario, _ = _scenario(server, [], steps)
    scenario["held"] = [
        {"command": refused.split(" "), "refused": True, "reply": {"error": "not offered"}}
    ]
    out = _drive(tmp_path / "node", scenario)
    no, yes = out["steps"]
    assert no["status"] == {"text": "Refused: not offered", "role": "alert"}, no
    assert yes["status"] == {"text": "Exit code 0", "role": "status"}, yes


def test_the_confirm_note_is_written_only_while_its_box_shows_constructed(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """F2 for the confirm step: `#confirm-note` is a status line inside
    `#confirm-box`, and a status line that is hidden when its words change
    is not read aloud. So the plan's note and the confirmed run's note are
    written only once the box shows. The plan and confirmed replies are held
    (constructed)."""
    listed = _send(server, "GET", "/api/commands").json()
    confirms = [
        " ".join(c["path"])
        for c in listed["commands"]
        if c.get("confirm") and not c.get("excluded")
    ]
    assert confirms, "the server offers a command of the confirm step"
    name = confirms[0]
    plan_note = "Plan ready: press Confirm to run it."
    done_note = "Journaled as 00000001."
    steps: list[dict[str, Any]] = [{"run_command": name}, {"confirm_click": True}]
    scenario, _ = _scenario(server, [], steps)

    def reply(stage: str, note: str) -> dict[str, Any]:
        confirm: dict[str, Any] = {"stage": stage, "note": note}
        if stage == "plan":
            confirm["token"] = "t1"
        return {
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
            "stdout": "{}",
            "stderr": "",
            "confirm": confirm,
        }

    scenario["held"] = [
        {"command": name.split(" "), "confirmed": False, "reply": reply("plan", plan_note)},
        {"command": name.split(" "), "confirmed": True, "reply": reply("confirmed", done_note)},
    ]
    out = _drive(tmp_path / "node", scenario)
    done = out["steps"][1]
    assert "missing" not in done, done
    assert done["noteWrites"] == [
        {"text": plan_note, "boxHidden": False},
        {"text": done_note, "boxHidden": False},
    ], done["noteWrites"]
    assert done["note"] == done_note and done["boxHidden"] is False, done
