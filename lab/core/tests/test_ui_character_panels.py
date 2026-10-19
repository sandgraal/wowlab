"""The Character tab's keys and macros panels (M14-09, §16.3).

Driven under Node on the harness of `test_ui_character.py` (the stand-in DOM
of `test_ui_explorer.py`: HTML sinks throw; navigation, attributes outside
the closed set and forbidden elements are trapped), fed what the real CLI
printed through the real server. The install is the captured tree
(`fixtures/macos/`) copied into `tmp_path` with its flavor folder named as
its provenance row says, with the real schema-3 record of
`fixtures/macos-70425/` (M13-05) copied into the character folder that holds
the captured character `macros-cache.txt`, as `test_cli_char_binds.py` and
`test_cli_char_macros.py` do: a `cross-capture` combination, said in the
test ids. That character is written last, so the tab picks it first.

Cases labelled `constructed` are boundary or hostile inputs (L8): a
character `bindings-cache.wtf` that puts key 1 on another command (so two
rows disagree), a `macros-cache.txt` of made-up macros (no player's macro
name or body), macro bodies holding `<script>`, `|c` escapes and control
characters, a refusal, a failed run, and report text replaced with markup.
Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import test_ui_character as gear_tab
import test_ui_explorer as first_stage
from test_ui_character import (
    ACCOUNT,
    NEWER,
    READS,
    _gear_params,
    _local,
    _pick_requests,
    _scenario,
    _tab_requests,
)
from test_ui_explorer import FLAVOR, IMG, LSEP, RLO, _node, _page, _run

home = first_stage.home
install = first_stage.install
server = first_stage.server
timed = gear_tab.timed  # the two captured lab files at fixed times

_TEMPLATES = gear_tab._TEMPLATES
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_3 = (
    FIXTURES
    / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)
PICKED = "1/Labcharb-Labrealmd"  # holds the captured character macros-cache.txt
PICKED_NS = 1_770_000_000_000_000_000  # later than the two captured lab files
CHAR_DIR = f"WTF/Account/{ACCOUNT}/{PICKED}"


# ─── the harness: the gear tab's, with this ticket's steps ───────────────────

_VIEW = r"""
function panelsView() {
  const keys = ids["char-keys"];
  const macros = ids["char-macros"];
  const find = (box, c) => box.all().filter((e) => has(e, c));
  const one = (box, c) => { const e = box.all().find((x) => has(x, c)); return e ? e.textContent : null; };
  const facts = (box) => find(box, "char-sources").flatMap((dl) => dl.children.map((c) => c.textContent));
  return {
    keysFacts: facts(keys),
    keysCannot: find(keys, "char-cannot").map((e) => e.textContent),
    keysCount: one(keys, "char-count"),
    keyRows: keys.all().filter((e) => e.tagName === "tr" && has(e, "key-row")).map((tr) => ({
      own: tr.children.map((c) => c._text), full: tr.children.map((c) => c.textContent),
      disagrees: has(tr, "key-disagrees"), slotTitle: tr.children[2].title, sourceTitle: tr.children[4].title })),
    keysNone: find(keys, "char-none").map((e) => e.textContent),
    keysHeads: keys.all().filter((e) => e.tagName === "h4").map((e) => e.textContent),
    unreached: find(keys, "char-slots").map((ul) => ul.children.map((li) => li.textContent)),
    keysErrors: find(keys, "char-error").map((e) => e.textContent),
    keysNotes: find(keys, "char-notes").flatMap((ul) => ul.children.map((li) => li.textContent)),
    keysText: keys.textContent,
    macrosFacts: facts(macros),
    macrosCount: one(macros, "char-count"),
    macros: find(macros, "macro").map((card) => ({
      title: card.children[0].textContent,
      name: one(card, "macro-name"),
      facts: one(card, "macro-facts"),
      lines: find(card, "macro-line").map((e) => e.textContent),
      reason: one(card, "macro-reason"),
      slots: find(card, "macro-slots").flatMap((ul) => ul.children.map((li) => ({
        text: li._text, keys: li.all().filter((x) => x.tagName === "li").map((x) => x.textContent) }))),
      findings: find(card, "macro-finding").map((e) => e.textContent),
      cannot: find(card, "macro-cannot").map((e) => e.textContent),
      clean: one(card, "macro-clean") })),
    macrosNone: find(macros, "char-none").map((e) => e.textContent),
    macrosJudged: find(macros, "char-slots").flatMap((ul) => ul.children.map((li) => li.textContent)),
    macrosErrors: find(macros, "char-error").map((e) => e.textContent),
    macrosNotes: find(macros, "char-notes").flatMap((ul) => ul.children.map((li) => li.textContent)),
    macrosText: macros.textContent,
    gearRows: ids["char-gear"].all().filter((e) => e.tagName === "tr" && has(e, "gear-row")).length,
    gearNames: ids["char-gear"].all().filter((e) => has(e, "gear-name")).map((e) => e._text),
    header: ids["char-header"].all().filter((e) => e.tagName === "h2").map((e) => e.textContent),
    status: ids["char-status"].textContent,
    buttons: Object.entries(ids).filter(([id]) => id.startsWith("char-")).flatMap(([, e]) => e.all())
      .filter((e) => ["button", "input", "textarea", "select"].includes(e.tagName)).map((e) => e.tagName + "." + e.className),
  };
}
// Replies the scenario slows down (`slow`): answered after `ms` on the
// harness's virtual clock, so a later pick's replies come first.
const quickFetch = ctx.fetch;
ctx.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  for (const slow of (body && scenario.slow) || []) {
    if (canon(slow.command) === canon(body.command) && canon(slow.params) === canon(body.params || {})) {
      await new Promise((done) => addTimer(done, slow.ms, false));
    }
  }
  return quickFetch(url, opts);
};
"""

_STEPS = r"""
      } else if (step.panels !== undefined) {
        out.steps.push(panelsView());
      } else if (step.picks !== undefined) {
        // Several picks with no wait between them, as a quick player makes.
        const before = requests.length;
        for (const at of step.picks) { ids["char-picker"].value = String(at); ids["char-picker"].fire("change"); }
        await tick();
        out.steps.push(Object.assign(panelsView(), { newRequests: requests.slice(before) }));
      } else if (step.keys_find !== undefined) {
        const before = requests.length;
        ids["char-keys-filter"].value = step.keys_find; ids["char-keys-filter"].fire("input"); await tick();
        out.steps.push(Object.assign(panelsView(), { newRequests: requests.slice(before) }));
      } else if (step.disagree !== undefined) {
        const before = requests.length;
        ids["char-keys-disagree"].checked = step.disagree; ids["char-keys-disagree"].fire("change"); await tick();
        out.steps.push(Object.assign(panelsView(), { newRequests: requests.slice(before) }));
      } else if (step.findings !== undefined) {
        const before = requests.length;
        ids["char-macros-findings"].checked = step.findings; ids["char-macros-findings"].fire("change"); await tick();
        out.steps.push(Object.assign(panelsView(), { newRequests: requests.slice(before) }));
"""


def _harness() -> str:
    text = gear_tab._harness()
    assert text.count(gear_tab._STEP_ANCHOR) == 1 and text.count(gear_tab._VIEW_ANCHOR) == 1
    text = text.replace(gear_tab._VIEW_ANCHOR, _VIEW + gear_tab._VIEW_ANCHOR, 1)
    return text.replace(gear_tab._STEP_ANCHOR, _STEPS.lstrip("\n") + gear_tab._STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    import subprocess

    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m14-09-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m14-09-scenario.json"
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


# ─── the install ─────────────────────────────────────────────────────────────


@pytest.fixture
def picked(timed: Path) -> Path:
    """The real schema-3 record as PICKED's WowLab.lua, written last."""
    target = timed / FLAVOR / CHAR_DIR / "SavedVariables" / "WowLab.lua"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCHEMA_3, target)
    os.utime(target, ns=(PICKED_NS, PICKED_NS))
    return timed


def _run_report(scenario: dict[str, Any], command: str, character: str) -> dict[str, Any]:
    for run in scenario["runs"]:
        if run["command"] == ["char", command] and run["params"]["character"] == character:
            report: dict[str, Any] = json.loads(run["stdout"])
            return report
    raise AssertionError((command, character))


def _set_run_report(
    scenario: dict[str, Any], command: str, character: str, report: dict[str, Any]
) -> None:
    for run in scenario["runs"]:
        if run["command"] == ["char", command] and run["params"]["character"] == character:
            run["stdout"] = json.dumps(report)
            return
    raise AssertionError((command, character))


def _macro(n: int, name: str, *body: str) -> bytes:
    """One constructed record in the captured grammar (CRLF, VER 3, hex id)."""
    lines = [f'VER 3 {0x0100000000000000 + n:016X} "{name}" "134400"', *body, "END"]
    return "".join(f"{line}\r\n" for line in lines).encode("utf-8")


def _put_macros(root: Path, *records: bytes) -> None:
    (root / FLAVOR / CHAR_DIR / "macros-cache.txt").write_bytes(b"".join(records))


def _sources(row: dict[str, Any]) -> str:
    return ", ".join(row["sources"])


def _key_cells(row: dict[str, Any]) -> list[str]:
    """The text each cell of a key row holds itself (not its small notes)."""
    slot = str(row["slot"]) if row["slot"] is not None else "-"
    return [row["key"], row["command"], slot, row["does"], _sources(row)]


def _kept(report: dict[str, Any], needle: str, disagreeing: bool) -> list[dict[str, Any]]:
    """The rows the model says the two filters keep: `disagrees` for the
    one, the text in the key, command, words or sources for the other."""
    out = []
    for row in report["keys"]:
        if disagreeing and not row["disagrees"]:
            continue
        hay = "\n".join([row["key"], row["command"], row["does"], *row["sources"]]).lower()
        if needle.strip().lower() in hay:
            out.append(row)
    return out


def _facts(flat: list[str]) -> dict[str, str]:
    return dict(zip(flat[::2], flat[1::2], strict=True))


# ─── the keys panel ──────────────────────────────────────────────────────────


def test_the_keys_panel_draws_the_rows_of_char_binds_cross_capture(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"panels": True}], [PICKED])
    out = _drive(tmp_path / "node", scenario)
    first = out["steps"][0]
    # The picked one is the latest-written, and its three reads are asked for.
    assert [r["body"] for r in first["newRequests"]][1:] == _pick_requests(PICKED)
    view = out["steps"][1]
    report = _run_report(scenario, "binds", PICKED)
    assert len(report["keys"]) == 131 and report["cannot_place"] is None
    assert [r["own"] for r in view["keyRows"]] == [_key_cells(k) for k in report["keys"]]
    for drawn, row in zip(view["keyRows"], report["keys"], strict=True):
        assert drawn["disagrees"] is row["disagrees"] is False
        assert drawn["slotTitle"] == (row["page_note"] or "")
        # The agreement in the model's word, under the sources.
        assert drawn["full"][4] == _sources(row) + row["agreement"]
        assert drawn["sourceTitle"] == (row["note"] or "")
    assert view["keysCount"] == "131 keys."
    assert view["keysCannot"] == []
    # The files and the record, with their times.
    facts = _facts(view["keysFacts"])
    assert facts["Record"] == (
        f"{report['record_file']} (schema 3), written {_local(PICKED_NS)} "
        "(the file's modification time)"
    )
    account, character = report["files"]
    assert facts["Account bindings"] == (
        f"{account['path']} (3 bind lines), written {_local(account['mtime_ns'])} {account['note']}"
    )
    assert facts["Character bindings"] == f"no bindings-cache.wtf {character['note']}"
    # The unreached slots, split as the model splits them.
    reached = [u for u in report["unreached_slots"] if not u["unfollowed_page"]]
    paged = [u for u in report["unreached_slots"] if u["unfollowed_page"]]
    assert len(reached) + len(paged) == 11
    heads = [h for h in view["keysHeads"] if h.startswith("Filled slots")]
    assert heads[0] == (
        f"Filled slots no key reaches on the page its bar shows by default: {len(reached)}"
    )
    assert reached and not paged, "the capture fills no slot on an unfollowed page"
    assert view["unreached"] == [
        [f"slot {u['slot']}: {u['does']} ({u['page_note']})" for u in reached]
    ]
    assert view["keysNotes"] == report["notes"]
    assert "Left out of the bindings record, counted with no list: 1 command" in view["keysText"]


def test_the_text_filter_narrows_as_the_model_says_cross_capture(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    steps = [
        {"char_tab": True},
        {"keys_find": "shadow bolt"},
        {"keys_find": "  MULTIACTIONBAR1 "},
        {"keys_find": "account file"},
        {"keys_find": "no such key text"},
        {"keys_find": ""},
    ]
    scenario = _scenario(server, steps, [PICKED])
    out = _drive(tmp_path / "node", scenario)
    report = _run_report(scenario, "binds", PICKED)
    for step, needle in zip(out["steps"][1:], [s["keys_find"] for s in steps[1:]], strict=True):
        kept = _kept(report, needle, False)
        assert [r["own"] for r in step["keyRows"]] == [_key_cells(k) for k in kept], needle
        assert step["newRequests"] == [], "a filter runs nothing"
        if needle == "":
            assert step["keysCount"] == "131 keys."
        else:
            assert step["keysCount"] == f"Showing {len(kept)} of 131 keys."
    # Each narrows to what the model's words hold.
    sizes = [len(s["keyRows"]) for s in out["steps"][1:]]
    assert all(0 < size < 131 for size in sizes[:3]) and sizes[3:] == [0, 131], sizes
    # The three keys of the account file, found by their source.
    assert [r["own"][0] for r in out["steps"][3]["keyRows"]] == ["Q", "BUTTON3", "E"]
    assert all("Shadow Bolt" in r["own"][3] for r in out["steps"][1]["keyRows"])
    assert out["steps"][4]["keysNone"] == ["No key matches the filters."]


def test_disagreements_only_keeps_none_on_the_capture_cross_capture(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"disagree": True}], [PICKED])
    out = _drive(tmp_path / "node", scenario)
    report = _run_report(scenario, "binds", PICKED)
    assert not any(k["disagrees"] for k in report["keys"])
    view = out["steps"][1]
    assert view["keyRows"] == [] and view["keysCount"] == "Showing 0 of 131 keys."
    assert view["keysNone"] == ["No key matches the filters."]


def test_disagreements_only_keeps_the_rows_the_model_marks_constructed(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    # Constructed: a character file putting key 1 on bar 5 (the record has it
    # on ACTIONBUTTON1) and a key the record has no command on.
    (picked / FLAVOR / CHAR_DIR / "bindings-cache.wtf").write_bytes(
        b"bind Q MULTIACTIONBAR1BUTTON1\r\nbind CTRL-F11 TOGGLEBACKPACK\r\n"
        b"bind 1 MULTIACTIONBAR5BUTTON1\r\n"
    )
    steps = [
        {"char_tab": True},
        {"disagree": True},
        {"keys_find": "bar5"},
        {"disagree": False},
        {"keys_find": ""},
    ]
    scenario = _scenario(server, steps, [PICKED])
    out = _drive(tmp_path / "node", scenario)
    report = _run_report(scenario, "binds", PICKED)
    disagreeing = [k for k in report["keys"] if k["disagrees"]]
    assert {(k["key"], k["command"]) for k in disagreeing} == {
        ("1", "ACTIONBUTTON1"),
        ("1", "MULTIACTIONBAR5BUTTON1"),
        ("CTRL-F11", "TOGGLEBACKPACK"),
    }
    only = out["steps"][1]
    assert [r["own"] for r in only["keyRows"]] == [_key_cells(k) for k in disagreeing]
    assert all(r["disagrees"] for r in only["keyRows"])
    total = len(report["keys"])
    assert only["keysCount"] == f"Showing 3 of {total} keys."
    # A disagreeing row shows the model's agreement word, the mark and the
    # note that names the other row.
    for drawn, row in zip(only["keyRows"], disagreeing, strict=True):
        assert drawn["full"][4] == _sources(row) + row["agreement"] + "disagrees" + row["note"]
    both = out["steps"][2]
    assert [r["own"] for r in both["keyRows"]] == [
        _key_cells(k) for k in _kept(report, "bar5", True)
    ]
    assert len(both["keyRows"]) == 1
    loose = out["steps"][3]
    assert [r["own"] for r in loose["keyRows"]] == [
        _key_cells(k) for k in _kept(report, "bar5", False)
    ]
    assert [r["own"] for r in out["steps"][4]["keyRows"]] == [_key_cells(k) for k in report["keys"]]
    facts = _facts(out["steps"][4]["keysFacts"])
    assert facts["Character bindings"].startswith(f"{CHAR_DIR}/bindings-cache.wtf (3 bind lines)")


def test_a_schema_1_record_shows_the_file_rows_and_why_not_more(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    """The captured schema-1 record (M11-03): the file rows alone, why they
    cannot be placed or compared, and no unreached list."""
    scenario = _scenario(
        server, [{"char_tab": True}, {"pick": 1}, {"panels": True}], [PICKED, NEWER]
    )
    out = _drive(tmp_path / "node", scenario)
    assert [r["body"] for r in out["steps"][1]["newRequests"]] == _pick_requests(NEWER)
    view = out["steps"][2]
    report = _run_report(scenario, "binds", NEWER)
    assert [r["own"] for r in view["keyRows"]] == [_key_cells(k) for k in report["keys"]]
    assert len(view["keyRows"]) == 3
    assert view["keysCannot"] == [
        f"Cannot place keys on action slots: {report['cannot_place']}",
        f"Cannot compare the files with the record: {report['cannot_compare']}",
    ]
    assert view["unreached"] == []
    assert _facts(view["keysFacts"])["Record"].startswith(f"{report['record_file']} (schema 1)")
    macros = _run_report(scenario, "macros", NEWER)
    assert view["macros"] == [] and view["macrosNone"] == ["No macro in these files."]
    assert view["macrosJudged"] == [f"{c['rule']}: {c['reason']}" for c in macros["cannot_judge"]]
    # The first pick's rows are gone: nothing of PICKED is left drawn.
    assert "PolyArc" not in view["macrosText"]


# ─── the macros panel ────────────────────────────────────────────────────────


def test_the_macros_panel_draws_the_captured_macro_cross_capture(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"panels": True}], [PICKED])
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    report = _run_report(scenario, "macros", PICKED)
    assert len(report["macros"]) == 1
    macro = report["macros"][0]
    assert len(view["macros"]) == 1
    card = view["macros"][0]
    assert card["title"] == f"character macro 1 {macro['name']}"
    assert card["name"] == macro["name"] == "PolyArc"
    assert card["lines"] == macro["body_lines"]
    assert card["facts"] == (
        f"id {macro['id']}, icon {macro['icon']}, line 1 of {macro['file']}, "
        f"{macro['characters']} characters, {macro['bytes']} bytes"
    )
    placement = macro["placement"]
    assert placement["status"] == "none" and placement["slots"] == []
    assert card["reason"] == f"Placement: none: {placement['reason']}"
    assert card["slots"] == []
    # The findings in the checker's words.
    assert len(macro["findings"]) == 2
    assert card["findings"] == [
        f"finding {f['rule']} (line {f['line']}): {f['detail']}" for f in macro["findings"]
    ]
    assert card["cannot"] == [] and card["clean"] is None
    assert view["macrosCount"] == "1 macro."
    assert view["macrosJudged"] == [f"{c['rule']}: {c['reason']}" for c in report["cannot_judge"]]
    assert view["macrosNotes"] == report["notes"]
    # The files and the record it came from, with their times.
    facts = _facts(view["macrosFacts"])
    assert facts["Record"].startswith(f"{report['record_file']} (schema 3), written ")
    account, character = report["files"]
    assert facts["Account macros"] == (
        f"0 macros in {account['path']}, written {_local(account['mtime_ns'])}"
    )
    assert facts["Character macros"] == (
        f"1 macro in {character['path']}, written {_local(character['mtime_ns'])}"
    )
    bindings = report["bindings_files"]
    assert facts["Account bindings"].startswith(f"{bindings[0]['path']}, written ")
    assert facts["Character bindings"].startswith("no bindings-cache.wtf")


def _twenty_nine(root: Path) -> None:
    """Constructed: 29 character macros, as many as the 70425 capture's file
    had, so the candidate numbering reads slots 64 (index 149) and 65 (140)
    as the 29th and the 20th. Macro 3 casts a spell not in the record's
    spellbook, so it alone has a finding."""
    records = []
    for n in range(1, 30):
        body = "/cast Lab Constructed Spell" if n == 3 else f"/say lab test {n}"
        records.append(_macro(n, f"Lab {n:02d}", body))
    _put_macros(root, *records)


def test_placement_and_the_findings_filter_constructed(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    _twenty_nine(picked)
    steps = [{"char_tab": True}, {"panels": True}, {"findings": True}, {"findings": False}]
    scenario = _scenario(server, steps, [PICKED])
    out = _drive(tmp_path / "node", scenario)
    report = _run_report(scenario, "macros", PICKED)
    assert len(report["macros"]) == 29
    view = out["steps"][1]
    assert view["macrosCount"] == "29 macros."
    assert [c["name"] for c in view["macros"]] == [m["name"] for m in report["macros"]]
    placed = 0
    for card, macro in zip(view["macros"], report["macros"], strict=True):
        p = macro["placement"]
        assert card["reason"] == f"Placement: {p['status']}: {p['reason']}"
        expected = []
        for s in p["slots"]:
            reach = [k for k in p["keys"] if k["slot"] == s["slot"]]
            keys = [f"key {k['key']} ({k['command']}; {_sources(k)})" for k in reach] or [
                "no key reaches it on the page its bar shows by default"
            ]
            expected.append(
                {
                    "text": f"may sit at slot {s['slot']} ({p['status']}): {s['page_note']}",
                    "keys": keys,
                }
            )
        assert card["slots"] == expected, macro["name"]
        placed += bool(expected)
        clean = not macro["findings"] and not macro["cannot_judge"]
        assert (card["clean"] == "No finding.") is clean, macro["name"]
    assert placed == 2
    slots = {
        m["position"]: [s["slot"] for s in m["placement"]["slots"]]
        for m in report["macros"]
        if m["placement"]["slots"]
    }
    assert slots == {20: [65], 29: [64]}
    assert all(m["placement"]["status"] in ("unconfirmed", "none") for m in report["macros"])
    # "Findings only" keeps the macros the checker found something in.
    found = [m for m in report["macros"] if m["findings"]]
    assert [m["position"] for m in found] == [3]
    only = out["steps"][2]
    assert [c["name"] for c in only["macros"]] == [m["name"] for m in found]
    assert only["macrosCount"] == "Showing 1 of 29 macros."
    assert only["macros"][0]["findings"] == [
        f"finding {f['rule']} (line {f['line']}): {f['detail']}" for f in found[0]["findings"]
    ]
    assert only["newRequests"] == [], "a filter runs nothing"
    assert len(out["steps"][3]["macros"]) == 29


def test_findings_only_with_no_finding_says_so_constructed(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    _put_macros(picked, _macro(1, "Lab one", "/say lab test"))
    scenario = _scenario(server, [{"char_tab": True}, {"findings": True}], [PICKED])
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    assert view["macros"] == [] and view["macrosCount"] == "Showing 0 of 1 macro."
    assert view["macrosNone"] == ["No macro here has a finding."]


# ─── player text is text ─────────────────────────────────────────────────────


HOSTILE_BODY = [
    "<script>alert(1)</script>",
    '/run print("|cffff0000red|r |cnIQ4:epic|r")',
    "/use |cff9d9d9d|Hitem:6948::::::::1:::::::|h[Lab Stone]|h|r",
    IMG + " " + RLO + "x" + LSEP + "y",
    '"><b onmouseover=alert(1)>',
]


def test_a_hostile_macro_body_renders_as_text_constructed(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    _put_macros(picked, _macro(1, IMG, *HOSTILE_BODY), _macro(2, "</script>", "|c"))
    scenario = _scenario(server, [{"char_tab": True}, {"panels": True}], [PICKED])
    out = _drive(tmp_path / "node", scenario)
    report = _run_report(scenario, "macros", PICKED)
    assert report["macros"][0]["body_lines"] == HOSTILE_BODY  # the CLI kept them as written
    first, second = out["steps"][1]["macros"]
    # Each line as written; the two invisible characters shown as codes.
    shown = [line.replace(RLO, "⟨U+202E⟩").replace(LSEP, "⟨U+2028⟩") for line in HOSTILE_BODY]
    assert first["lines"] == shown
    assert first["name"] == IMG
    assert second["name"] == "</script>" and second["lines"] == ["|c"]
    drawn = json.dumps(out["steps"], ensure_ascii=False)
    assert RLO not in drawn and LSEP not in drawn


def test_hostile_key_and_spell_text_renders_as_text_constructed(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"panels": True}], [PICKED])
    binds = _run_report(scenario, "binds", PICKED)
    row = binds["keys"][0]
    row["key"] = "<i>K</i>"
    row["command"] = "</script>"
    row["does"] = "casts " + IMG + RLO + " |cffff0000x|r"
    row["holds"]["name"] = IMG
    row["sources"] = ["record", "<b>file</b>"]
    row["agreement"] = "<u>both</u>"
    binds["unreached_slots"][0]["does"] = "casts |cnIQ4:" + IMG
    _set_run_report(scenario, "binds", PICKED, binds)
    macros = _run_report(scenario, "macros", PICKED)
    macros["macros"][0]["findings"][0]["detail"] = "'" + IMG + "' is not in the spellbook record"
    macros["macros"][0]["placement"]["reason"] = "<script>x</script>"
    _set_run_report(scenario, "macros", PICKED, macros)
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    first = view["keyRows"][0]
    assert first["own"] == [
        "<i>K</i>",
        "</script>",
        "1",
        "casts " + IMG + "⟨U+202E⟩ |cffff0000x|r",
        "record, <b>file</b>",
    ]
    assert first["full"][4] == "record, <b>file</b><u>both</u>"
    assert view["unreached"][0][0].startswith("slot 49: casts |cnIQ4:" + IMG)
    card = view["macros"][0]
    assert card["findings"][0].endswith("'" + IMG + "' is not in the spellbook record")
    assert card["reason"] == "Placement: none: <script>x</script>"
    assert RLO not in json.dumps(out["steps"], ensure_ascii=False)


# ─── one pick handler ────────────────────────────────────────────────────────


def test_a_late_reply_from_an_earlier_pick_draws_nothing_cross_capture(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    """The one pick handler holds the `seq` guard for all three panels: the
    replies for a character picked and then left arrive after those of the
    one picked next, and none of them is drawn."""
    steps = [{"char_tab": True}, {"picks": [1, 0]}]
    scenario = _scenario(server, steps, [PICKED, NEWER])
    scenario["slow"] = [
        {"command": command, "params": _gear_params(NEWER), "ms": 500}
        for command in (["char", "gear"], ["char", "binds"], ["char", "macros"])
    ]
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    # Both picks asked; the earlier one's replies came last.
    asked = [r["body"] for r in view["newRequests"]]
    assert sorted(map(json.dumps, asked)) == sorted(
        map(json.dumps, _pick_requests(NEWER) + _pick_requests(PICKED))
    )
    assert asked[-3:] == _pick_requests(NEWER)
    binds = _run_report(scenario, "binds", PICKED)
    gear = _run_report(scenario, "gear", PICKED)
    assert view["header"] == [PICKED.split("/")[1]]
    assert view["gearNames"] == [s["name"] for s in sorted(gear["slots"], key=lambda s: s["slot"])]
    assert [r["own"] for r in view["keyRows"]] == [_key_cells(k) for k in binds["keys"]]
    assert [c["name"] for c in view["macros"]] == ["PolyArc"]
    assert view["status"] == ""


# ─── errors: the CLI's text, in the panel it belongs to ──────────────────────


def test_a_refused_or_failed_panel_shows_the_cli_text_constructed(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"panels": True}], [PICKED])
    params = _gear_params(PICKED)
    scenario["runs"] = [r for r in scenario["runs"] if r["command"][1] in ("list", "gear")]
    scenario["held"] += [
        {
            "command": ["char", "binds"],
            "params": params,
            "reply": {"error": "excluded"},
            "refused": True,
        },
        {
            "command": ["char", "macros"],
            "params": params,
            "reply": {
                "exit_code": 2,
                "timed_out": False,
                "truncated": False,
                "stdout": "",
                "stderr": "wowlab: m14-09 constructed failure\n",
            },
        },
    ]
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    assert view["keysErrors"] == ["Refused: excluded"] and view["keyRows"] == []
    assert view["macrosErrors"] == ["wowlab: m14-09 constructed failure"]
    assert view["macros"] == []
    # The gear is drawn all the same, and the status line is clear.
    assert view["gearRows"] == 19 and view["status"] == ""


def test_a_char_binds_failure_on_a_real_run_is_the_cli_text_constructed(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    """A real failed run: the character folder disappears after the list."""
    scenario = _scenario(server, [{"char_tab": True}, {"panels": True}], [])
    shutil.rmtree(picked / FLAVOR / CHAR_DIR)
    params = _gear_params(PICKED)
    for command in ("binds", "macros"):
        reply = _run(server, ["char", command], params).json()
        assert reply["exit_code"] != 0 and reply["stdout"] == ""
        scenario["held"].append({"command": ["char", command], "params": params, "reply": reply})
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    for errors, command in ((view["keysErrors"], "binds"), (view["macrosErrors"], "macros")):
        held = next(h for h in scenario["held"] if h["command"] == ["char", command])
        assert errors == [held["reply"]["stderr"].strip()]


# ─── only reads; no edit control; the read-only note ─────────────────────────


def test_the_panels_request_only_read_commands_cross_capture(
    server: Any, picked: Path, tmp_path: Path
) -> None:
    steps = [
        {"char_tab": True},
        {"keys_find": "q"},
        {"disagree": True},
        {"findings": True},
        {"pick": 1},
        {"pick": 0},
        {"char_activate": True},
        {"panels": True},
    ]
    scenario = _scenario(server, steps, [PICKED, NEWER])
    out = _drive(tmp_path / "node", scenario)
    asked = _tab_requests(out, 0)
    assert {tuple(b["command"]) for b in asked} == READS
    for body in asked:
        if body["command"] != ["char", "list"]:
            assert body["params"] in (_gear_params(PICKED), _gear_params(NEWER)), body
    for request in out["requests"]:
        if request["body"] is None:
            assert request["url"].startswith("/api/commands?"), request
        else:
            assert tuple(request["body"]["command"]) in READS | {("tree",)}, request
    offered = {tuple(c["path"]): c for c in scenario["commands"]["commands"]}
    for path in READS:
        assert offered[path]["excluded"] is None and not offered[path].get("confirm"), path
        assert "yes" not in {p["name"] for p in offered[path]["params"]}, path
    # The controls the tab draws: the item names and the link's copy button
    # of the gear; the keys and macros panels draw none.
    view = out["steps"][-1]
    kinds = {b.split(" ")[0] for b in view["buttons"]}
    assert kinds == {"button.gear-name", "button.copy"}, view["buttons"]


def _character_section() -> str:
    html = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    found = re.search(
        r'<section id="character-view".*?\n</section>\n(?=<section id="looks)', html, re.S
    )
    assert found is not None
    return found.group(0)


def test_the_tab_offers_no_edit_control_and_says_so() -> None:
    section = _character_section()
    # The four controls: the picker, the text filter and the two checkboxes.
    controls = re.findall(r"<(input|select|textarea|button)\b([^>]*)>", section)
    named = []
    for tag, attrs in controls:
        ident = re.search(r'id="([^"]+)"', attrs)
        kind = re.search(r'type="([^"]+)"', attrs)
        assert ident is not None, attrs
        named.append((tag, ident.group(1), kind.group(1) if kind else ""))
    named.sort()
    assert named == [
        ("input", "char-keys-disagree", "checkbox"),
        ("input", "char-keys-filter", "search"),
        ("input", "char-macros-findings", "checkbox"),
        ("select", "char-picker", ""),
    ]
    assert "<form" not in section and "contenteditable" not in section
    # A read-only note under each of the two panels (§16.5).
    for panel in ("char-keys-panel", "char-macros-panel"):
        body = re.search(rf'<section id="{panel}".*?</section>', section, re.S)
        assert body is not None, panel
        notes = re.findall(r'<p class="char-read-only">([^<]*)</p>\n</section>$', body.group(0))
        assert len(notes) == 1, panel
        assert notes[0].startswith("Read only in this version") and "later wave" in notes[0]
    # The intro names the four commands.
    intro = re.search(r'<p class="intro">(.*?)</p>', section, re.S)
    assert intro is not None
    for command in ("char list", "char gear", "char binds", "char macros"):
        assert f"<code>wowlab {command}</code>" in intro.group(1), command


def test_the_live_region_is_the_status_line_only(server: Any) -> None:
    """The M14-08 note: `aria-live` on the whole gear panel read nineteen
    rows on every pick. The status line (`role="status"`) is the only live
    region in the tab."""
    section = _character_section()
    assert "aria-live" not in section
    assert re.findall(r'role="status"', section) == ['role="status"']
    assert '<p id="char-status" role="status">' in section
    assert _page(server).status == 200


def test_the_script_holds_no_binding_or_macro_rule() -> None:
    """§14.5 D3: the words about a key or a macro come from the report. The
    script holds none of the model's words for what a key does, no agreement
    value it compares against, no placement status it branches on, and no
    rule name of the checker. (The Commands view's confirm step has a
    `"confirmed"` stage of its own; the panels' part of the script is read.)"""
    whole = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    start = whole.index("// ─── the keys and macros panels (M14-09")
    script = whole[start : whole.index("// ─── the looks tab (M14-04", start)]
    assert "function filterKeys" in script and "function placementBox" in script
    for agreement in ("both", "file only", "record only", "not in a file", "cleared"):
        assert f'"{agreement}"' not in script, agreement
    for status in ("unconfirmed", "confirmed"):
        assert f'"{status}"' not in script, status
    for rule in ("spell-not-in-spellbook", "not-on-action-bar", "duplicate-name", "too-long"):
        assert rule not in script, rule
    for words in ("casts ", "runs macro", "uses item", "ACTIONBUTTON", "MULTIACTIONBAR"):
        assert words not in whole, words
