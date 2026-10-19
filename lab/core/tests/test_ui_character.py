"""The Character tab of `wowlab ui`: picker, header, gear (M14-08, §16.3).

The keys and macros panels (M14-09) are graded in
`test_ui_character_panels.py`, on this harness; a pick here asks for all
three of the character's reads.

The page is driven under Node with the stand-in DOM of `test_ui_explorer.py`
(HTML sinks throw; navigation, attributes outside the closed set and
forbidden elements are trapped), fed what the real CLI printed through the
real server for the captured install (`fixtures/macos/`, two characters with
a gear record), copied into `tmp_path` under a stand-in home folder with its
flavor folder named as its provenance row says. The harness gains steps for
this tab (open it, pick a character, click an item, copy its link, use every
control in the tab) and held replies that are not a clean exit (a refusal,
an exit 1). Nothing reads or writes a real install.

Cases labelled `constructed` are hostile or boundary inputs (L8): a record
without `gear`, a character file that cannot be read, a refusal, item names
that try to be markup or carry control characters.

What the tab may not hold is checked by reading the templates: no slot name
and no colour code of `gear_slots.toml` (the colours are rules the server
makes from the shipped table, `ui._colour_rules`).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import test_ui_explorer as first_stage
from test_ui_explorer import (
    _HARNESS,
    FLAVOR,
    IMG,
    LSEP,
    RLO,
    _inline,
    _node,
    _page,
    _page_ids,
    _run,
    _send,
    _stdout,
)

from wowlab_core import cli, gear, ui

home = first_stage.home
install = first_stage.install
server = first_stage.server

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
ACCOUNT = "90000001#6"
NEWER = "1/Labchard-Labrealmg"  # written later here, though it sorts second by name
OLDER = "1/Labcharb-Labrealmf"
NEWER_NS = 1_760_000_000_000_000_000
OLDER_NS = 1_750_000_000_000_000_000
# The four read commands the tab runs (M14-09 added the last two).
READS = {("char", "list"), ("char", "gear"), ("char", "binds"), ("char", "macros")}
PICK_READS = (["char", "gear"], ["char", "binds"], ["char", "macros"])


# ─── the harness: the first stage's, with this tab's steps ───────────────────

_VIEW = r"""
function charView() {
  const gearBox = ids["char-gear"];
  const picker = ids["char-picker"];
  const named = (tr) => tr.all().find((e) => has(e, "gear-name")) || null;
  return {
    hidden: ids["character-view"].hidden,
    tabSelected: ids["tab-character"].getAttribute("aria-selected"),
    status: ids["char-status"].textContent,
    statusError: has(ids["char-status"], "error"),
    options: picker.options.map((o) => ({ text: o.textContent, value: o.value, disabled: o.disabled, title: o.title })),
    pickerDisabled: picker.disabled,
    header: ids["char-header"].textContent,
    headerFacts: ids["char-header"].all().filter((e) => e.tagName === "dt" || e.tagName === "dd").map((e) => e.textContent),
    notes: ids["char-header"].all().filter((e) => e.tagName === "li").map((e) => e.textContent),
    gearText: gearBox.textContent,
    errors: gearBox.all().filter((e) => has(e, "char-error")).map((e) => e.textContent),
    rows: gearBox.all().filter((e) => e.tagName === "tr" && has(e, "gear-row")).map((tr) => {
      const name = named(tr);
      return { cells: tr.children.map((c) => c.textContent), empty: has(tr, "gear-row-empty"),
        name: name ? name._text : null, nameText: name ? name.textContent : null,
        nameClass: name ? name.className : null, nameTitle: name ? name.title : null,
        expanded: name ? name.getAttribute("aria-expanded") : null };
    }),
    details: gearBox.all().filter((e) => e.tagName === "tr" && has(e, "gear-detail")).map((tr) => ({
      hidden: tr.hidden, text: tr.textContent,
      facts: tr.all().filter((e) => e.tagName === "dt" || e.tagName === "dd").map((e) => e._text),
      link: (tr.all().find((e) => has(e, "gear-link")) || { textContent: null }).textContent,
      copies: tr.all().filter((e) => e.tagName === "button" && has(e, "copy")).length })),
  };
}
// Replies the scenario holds that are not a clean exit: a refusal (403) or
// a run that ended with an error, matched on the command and its parameters.
const plainFetch = ctx.fetch;
ctx.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  if (body && String(url).startsWith("/api/run?")) {
    for (const held of scenario.held || []) {
      if (canon(held.command) === canon(body.command) && canon(held.params) === canon(body.params || {})) {
        requests.push({ url: String(url), body: body });
        return answer(held.reply, held.refused ? false : true);
      }
    }
  }
  return plainFetch(url, opts);
};
"""

_STEPS = r"""
      } else if (step.char_tab !== undefined) {
        const before = requests.length;
        ids["tab-character"].fire("click"); await tick();
        out.steps.push(Object.assign(charView(), { newRequests: requests.slice(before) }));
      } else if (step.pick !== undefined) {
        const before = requests.length;
        ids["char-picker"].value = String(step.pick); ids["char-picker"].fire("change"); await tick();
        out.steps.push(Object.assign(charView(), { newRequests: requests.slice(before) }));
      } else if (step.item !== undefined) {
        const names = ids["char-gear"].all().filter((e) => has(e, "gear-name"));
        if (!names[step.item]) { out.steps.push({ missing: "item " + step.item }); continue; }
        const before = requests.length;
        names[step.item].fire("click"); await tick();
        out.steps.push(Object.assign(charView(), { newRequests: requests.slice(before) }));
      } else if (step.copy_link !== undefined) {
        const buttons = ids["char-gear"].all().filter((e) => e.tagName === "button" && has(e, "copy"));
        if (!buttons[step.copy_link]) { out.steps.push({ missing: "copy " + step.copy_link }); continue; }
        const before = requests.length;
        buttons[step.copy_link].fire("click"); await tick();
        out.steps.push({ clipboard: clipboard.slice(), newRequests: requests.slice(before), label: buttons[step.copy_link].textContent });
      } else if (step.char_activate !== undefined) {
        // Every control in the tab, used: each listener fired, until nothing new appears.
        const before = requests.length;
        const done = new Set();
        // The stand-in DOM keeps page.html's elements as separate roots, so
        // the tab's own controls (the picker, the M14-09 filters) are reached
        // by their ids, with everything the script drew inside them.
        const scope = () => {
          const out = [];
          for (const [id, e] of Object.entries(ids)) {
            if (id === "character-view" || id.startsWith("char-")) out.push(e, ...e.all());
          }
          return out;
        };
        for (let round = 0; round < 10; round++) {
          const fresh = scope().filter((e) => !done.has(e));
          if (!fresh.length) break;
          for (const e of fresh) {
            done.add(e);
            for (const ev of Object.keys(e.listeners)) e.fire(ev);
            await tick();
          }
        }
        out.steps.push(Object.assign(charView(), { newRequests: requests.slice(before) }));
"""

_STEP_ANCHOR = "      } else if (step.preview !== undefined) {\n"
_VIEW_ANCHOR = "vm.createContext(ctx);\n"


def _harness() -> str:
    assert _HARNESS.count(_STEP_ANCHOR) == 1 and _HARNESS.count(_VIEW_ANCHOR) == 1
    text = _HARNESS.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_STEP_ANCHOR, _STEPS.lstrip("\n") + _STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m14-08-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m14-08-scenario.json"
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


# ─── the install and the scenario ────────────────────────────────────────────


def _lab_file(root: Path, character: str) -> Path:
    return root / FLAVOR / "WTF" / "Account" / ACCOUNT / character / "SavedVariables" / "WowLab.lua"


@pytest.fixture
def timed(install: Path) -> Path:
    """The capture with its two lab files at fixed times, the later one on
    the folder that sorts second by name."""
    for character, ns in ((NEWER, NEWER_NS), (OLDER, OLDER_NS)):
        os.utime(_lab_file(install, character), ns=(ns, ns))
    return install


def _gear_params(character: str) -> dict[str, Any]:
    """What the page sends `char gear`, `char binds` and `char macros` for a
    row of `char list`."""
    return {"character": character, "account": ACCOUNT, "flavor": FLAVOR}


def _pick_requests(character: str) -> list[dict[str, Any]]:
    """The three runs one pick asks for, in the order the page sends them."""
    return [{"command": c, "params": _gear_params(character)} for c in PICK_READS]


def _scenario(
    server: Any, steps: list[dict[str, Any]], gear_for: list[str], *, listed: bool = True
) -> dict[str, Any]:
    """The command list, `char list` (unless not `listed`) and `char gear`,
    `char binds` and `char macros` for each character in `gear_for`, as the
    real CLI printed them through the real server. The explorer's runs are
    not held: its tree says so and the tab is unaffected."""
    runs = []
    if listed:
        stdout = _stdout(server, ["char", "list"], {})
        runs.append({"command": ["char", "list"], "params": {}, "stdout": stdout})
    for character in gear_for:
        params = _gear_params(character)
        for command in PICK_READS:
            runs.append(
                {
                    "command": list(command),
                    "params": params,
                    "stdout": _stdout(server, list(command), params),
                }
            )
    return {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": _send(server, "GET", "/api/commands").json(),
        "first_flavor": FLAVOR,
        "trees": {},
        "explains": {},
        "runs": runs,
        "held": [],
        "steps": steps,
    }


def _report(scenario: dict[str, Any], character: str) -> dict[str, Any]:
    for run in scenario["runs"]:
        if run["command"] == ["char", "gear"] and run["params"]["character"] == character:
            report: dict[str, Any] = json.loads(run["stdout"])
            return report
    raise AssertionError(character)


def _set_report(scenario: dict[str, Any], character: str, report: dict[str, Any]) -> None:
    for run in scenario["runs"]:
        if run["command"] == ["char", "gear"] and run["params"]["character"] == character:
            run["stdout"] = json.dumps(report)
            return
    raise AssertionError(character)


def _local(ns: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ns // 1_000_000_000))


def _in_slot_order(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(s, filled=True) for s in report["slots"]]
    rows += [dict(s, filled=False) for s in report["empty_slots"]]
    return sorted(rows, key=lambda r: r["slot"])


def _tab_requests(out: dict[str, Any], start: int) -> list[dict[str, Any]]:
    """Every run the page asked for from step `start` on."""
    asked: list[dict[str, Any]] = []
    for step in out["steps"][start:]:
        asked += [r["body"] for r in step.get("newRequests", []) if r["body"] is not None]
    return asked


# ─── the tab ─────────────────────────────────────────────────────────────────


def test_the_character_tab_is_the_fourth_and_starts_hidden(server: Any) -> None:
    body = _page(server).body.decode("utf-8")
    tabs = re.findall(r'<button id="tab-([a-z]+)" class="tab"[^>]*role="tab"', body)
    # The Looks tab (M14-04) comes after it.
    assert tabs[:4] == ["explorer", "gaps", "commands", "character"]
    assert re.search(r'<section id="character-view"[^>]*\bhidden\b', body)
    for name in ("char-picker", "char-status", "char-header", "char-gear", "char-panels"):
        assert f'id="{name}"' in body, name


def test_the_picker_lists_the_characters_latest_written_first(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}], [NEWER])
    listed = json.loads(scenario["runs"][0]["stdout"])
    assert {f"{c['realm_folder']}/{c['label']}" for c in listed["characters"]} == {NEWER, OLDER}
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][0]
    assert view["hidden"] is False and view["tabSelected"] == "true"
    assert view["pickerDisabled"] is False
    labels = [NEWER.split("/")[1], OLDER.split("/")[1]]
    assert [o["text"] for o in view["options"]] == [
        f"{labels[0]}, written {_local(NEWER_NS)}",
        f"{labels[1]}, written {_local(OLDER_NS)}",
    ]
    assert not any(o["disabled"] for o in view["options"])
    # The first (latest) is read at once, with the folders the list gave.
    assert [r["body"] for r in view["newRequests"]] == [
        {"command": ["char", "list"], "params": {}},
        *_pick_requests(NEWER),
    ]
    assert view["status"] == "" and not view["statusError"]


def test_the_gear_rows_match_char_gear_for_the_picked_one(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"pick": 1}], [NEWER, OLDER])
    out = _drive(tmp_path / "node", scenario)
    for step, character in ((out["steps"][0], NEWER), (out["steps"][1], OLDER)):
        report = _report(scenario, character)
        expected = _in_slot_order(report)
        assert len(step["rows"]) == len(expected) == 19, character
        for row, slot in zip(step["rows"], expected, strict=True):
            assert row["cells"][0] == slot["slot_name"], (character, slot["slot"])
            if not slot["filled"]:
                assert row["empty"] and row["name"] is None
                assert row["cells"][1:] == ["empty", ""], (character, slot["slot"])
                continue
            assert not row["empty"]
            assert row["name"] == slot["name"]
            assert row["cells"][2] == str(slot["item_level"])
            # The quality colour: the class the server's rules paint, and the
            # word for a screen reader and a pointer.
            assert gear.colour_class(slot["colour"]) in row["nameClass"].split()
            assert row["nameTitle"] == slot["quality"]
            assert row["nameText"] == f"{slot['name']} ({slot['quality']})"
            assert row["expanded"] == "false"
        assert all(d["hidden"] for d in step["details"])
    # The second pick asked for the second character, and only for it.
    assert [r["body"] for r in out["steps"][1]["newRequests"]] == _pick_requests(OLDER)


def test_the_header_carries_spec_averages_and_the_notes(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}], [NEWER])
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][0]
    report = _report(scenario, NEWER)
    facts = dict(zip(view["headerFacts"][::2], view["headerFacts"][1::2], strict=True))
    assert facts["Character folder"] == report["character"] == NEWER
    assert facts["Spec id"] == str(report["spec"]["id"])
    assert facts["Average item level, equipped"] == str(report["average"]["equipped"])
    assert facts["Average item level, overall"] == str(report["average"]["overall"])
    assert facts["File"] == report["file"]
    assert facts["Written"] == f"{_local(NEWER_NS)} (the file's modification time)"
    # The two notes `char show` prints about what the file is, and the rest.
    for note in cli._CHAR_NOTES:
        assert note in view["notes"]
    assert view["notes"] == report["notes"]


def test_the_detail_row_carries_the_raw_link_and_copies_it(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    steps = [{"char_tab": True}, {"item": 0}, {"copy_link": 0}, {"item": 0}]
    scenario = _scenario(server, steps, [NEWER])
    out = _drive(tmp_path / "node", scenario)
    first = _report(scenario, NEWER)["slots"][0]
    opened = out["steps"][1]
    assert (
        opened["rows"][[r["name"] for r in opened["rows"]].index(first["name"])]["expanded"]
        == "true"
    )
    shown = [d for d in opened["details"] if not d["hidden"]]
    assert len(shown) == 1
    detail = shown[0]
    assert detail["link"] == first["raw_link"]
    assert detail["copies"] == 1
    facts = dict(zip(detail["facts"][::2], detail["facts"][1::2], strict=True))
    assert facts["Item id"] == str(first["item_id"])
    assert facts["Quality"] == f"{first['quality']} (colour code {first['colour']})"
    assert facts["Enchant"] == ("none" if first["enchant_id"] is None else str(first["enchant_id"]))
    assert facts["Gems"] == (", ".join(map(str, first["gem_ids"])) or "none")
    assert facts["Bonus ids"] == (", ".join(map(str, first["bonus_ids"])) or "none")
    assert first["bonus_ids"], "the capture's first item has bonus ids"
    copied = out["steps"][2]
    assert copied["clipboard"] == [first["raw_link"]]
    assert copied["newRequests"] == [], "copying runs nothing"
    closed = out["steps"][3]
    assert all(d["hidden"] for d in closed["details"])
    assert all(r["expanded"] in ("false", None) for r in closed["rows"])


def test_the_tab_runs_only_the_four_char_reads(server: Any, timed: Path, tmp_path: Path) -> None:
    steps = [{"char_tab": True}, {"pick": 1}, {"pick": 0}, {"char_activate": True}]
    scenario = _scenario(server, steps, [NEWER, OLDER])
    out = _drive(tmp_path / "node", scenario)
    asked = _tab_requests(out, 0)
    assert asked, "the tab ran something"
    for body in asked:
        assert tuple(body["command"]) in READS, body
        if body["command"] != ["char", "list"]:
            assert body["params"] in (_gear_params(NEWER), _gear_params(OLDER)), body
    # `char list` is read once, however often the tab is used.
    assert sum(1 for b in asked if b["command"] == ["char", "list"]) == 1
    # Every request the page made in the whole run is one the page makes
    # anyway (the command list, the explorer's tree) or one of the four.
    for request in out["requests"]:
        if request["body"] is None:
            assert request["url"].startswith("/api/commands?"), request
        else:
            assert tuple(request["body"]["command"]) in READS | {("tree",)}, request
    # All four are read commands the server offers, not excluded and not a
    # confirm-step command.
    offered = {tuple(c["path"]): c for c in scenario["commands"]["commands"]}
    for path in READS:
        assert offered[path]["excluded"] is None and not offered[path].get("confirm"), path
        assert "yes" not in {p["name"] for p in offered[path]["params"]}, path


# ─── errors: the CLI's text ──────────────────────────────────────────────────


def test_a_record_without_gear_shows_the_commands_reason_constructed(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    path = _lab_file(timed, NEWER)
    path.write_bytes(b'\nWowLabCharDB = {\n["schema"] = 1,\n}\n')
    os.utime(path, ns=(NEWER_NS, NEWER_NS))
    reply = _run(server, ["char", "gear"], _gear_params(NEWER)).json()
    assert reply["exit_code"] == 1 and reply["stdout"] == ""
    assert "no gear in" in reply["stderr"]
    scenario = _scenario(server, [{"char_tab": True}], [])
    scenario["held"].append(
        {"command": ["char", "gear"], "params": _gear_params(NEWER), "reply": reply}
    )
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][0]
    assert view["errors"] == [reply["stderr"].strip()]
    assert view["rows"] == [] and view["details"] == []


def test_a_refusal_shows_the_servers_word_constructed(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}], [])
    scenario["held"].append(
        {
            "command": ["char", "gear"],
            "params": _gear_params(NEWER),
            "reply": {"error": "excluded"},
            "refused": True,
        }
    )
    out = _drive(tmp_path / "node", scenario)
    assert out["steps"][0]["errors"] == ["Refused: excluded"]
    assert out["steps"][0]["rows"] == []


def test_a_list_with_an_unreadable_file_lists_the_rest_constructed(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    broken = _lab_file(timed, NEWER)
    broken.write_bytes(b"WowLabCharDB = function() end\n")
    os.utime(broken, ns=(NEWER_NS, NEWER_NS))
    reply = _run(server, ["char", "list"], {}).json()
    assert reply["exit_code"] == 1 and reply["stdout"], reply["stderr"]
    scenario = _scenario(server, [{"char_tab": True}], [OLDER], listed=False)
    scenario["held"].append({"command": ["char", "list"], "params": {}, "reply": reply})
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][0]
    bad, good = view["options"]
    assert bad["text"].startswith(NEWER.split("/")[1]) and bad["disabled"] is True
    assert bad["text"].endswith(": not read") and bad["title"]
    assert good["disabled"] is False and good["text"].startswith(OLDER.split("/")[1])
    # The readable one is read; the CLI's stderr is the status.
    picked = [r["body"] for r in view["newRequests"] if r["body"]["command"] != ["char", "list"]]
    assert picked == _pick_requests(OLDER)
    assert view["statusError"] and view["status"] == reply["stderr"].strip()
    assert len(view["rows"]) == 19


def test_a_failed_list_shows_the_cli_text_and_is_asked_again_constructed(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"char_tab": True}], [], listed=False)
    failed = {"exit_code": 2, "timed_out": False, "truncated": False, "stdout": "",
              "stderr": "wowlab: m14-08 constructed failure\n"}  # fmt: skip
    scenario["held"].append({"command": ["char", "list"], "params": {}, "reply": failed})
    out = _drive(tmp_path / "node", scenario)
    for view in out["steps"]:
        assert view["status"] == "wowlab: m14-08 constructed failure" and view["statusError"]
        # The picker stays as page.html draws it, `disabled` (the stand-in
        # DOM does not read that attribute), with no choice in it.
        assert view["options"] == [] and view["rows"] == []
    assert [r["body"]["command"] for r in out["steps"][1]["newRequests"]] == [["char", "list"]]


def test_hostile_item_text_draws_as_inert_text_constructed(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}, {"item": 0}], [NEWER])
    report = _report(scenario, NEWER)
    hostile = IMG + RLO + "x" + LSEP + "</script>"
    report["slots"][0]["name"] = hostile
    report["slots"][0]["raw_link"] = "|cnIQ2:|Hitem:1|h[" + hostile + "]|h|r"
    report["slots"][0]["slot_name"] = "<b>" + RLO
    report["slots"][0]["colour"] = 'x" onclick="y'
    report["slots"][0]["quality"] = "<i>q</i>"
    _set_report(scenario, NEWER, report)
    out = _drive(tmp_path / "node", scenario)
    row = out["steps"][1]["rows"][
        [r["cells"][0] for r in out["steps"][1]["rows"]].index("<b>⟨U+202E⟩")
    ]
    assert row["name"] == IMG + "⟨U+202E⟩x⟨U+2028⟩</script>"
    # A colour code no slot table could hold gets no class at all.
    assert row["nameClass"] == "gear-name"
    detail = next(d for d in out["steps"][1]["details"] if not d["hidden"])
    assert detail["link"] == "|cnIQ2:|Hitem:1|h[" + IMG + "⟨U+202E⟩x⟨U+2028⟩</script>]|h|r"
    drawn = json.dumps(out["steps"], ensure_ascii=False)
    assert RLO not in drawn and LSEP not in drawn


# ─── what the templates may not hold ─────────────────────────────────────────


def _shipped() -> dict[str, Any]:
    text = resources.files("wowlab_core").joinpath(gear.SLOTS_FILE).read_text(encoding="utf-8")
    return tomllib.loads(text)


def _templates() -> dict[str, str]:
    return {
        n: (_TEMPLATES / n).read_text(encoding="utf-8")
        for n in ("page.html", "page.js", "page.css")
    }


def test_the_templates_hold_no_slot_name_and_no_colour_code() -> None:
    """§16.3: the JavaScript holds no slot table and no quality table; the
    names and colours come from the model and the server's rules."""
    shipped = _shipped()
    names = sorted(set(shipped["slots"].values()))
    codes = sorted(shipped["quality"])
    words = sorted(set(shipped["quality"].values()))
    assert len(names) == 19 and "Main Hand" in names and len(codes) >= 9
    rgbs = [c[2:] for c in codes if re.fullmatch(r"[0-9a-fA-F]{8}", c)]
    for file, text in _templates().items():
        for name in names:
            assert re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text) is None, (
                file,
                name,
            )
        for code in codes:
            assert code.lower() not in text.lower(), (file, code)
        if file != "page.css":
            for rgb in rgbs:
                assert rgb.lower() not in text.lower(), (file, rgb)
    script = _templates()["page.js"]
    for word in words:
        assert re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", script) is None, word
    # The stylesheet in the package paints no colour code: the rules are the
    # server's, made from the slot table.
    assert ".colour-" not in _templates()["page.css"]
    assert re.search(r"\.colour-", script) is None


def test_the_colour_rules_are_made_from_the_shipped_table(server: Any) -> None:
    body = _page(server).body.decode("utf-8")
    styles = _inline(body, "style")
    assert len(styles) == 1
    style = styles[0]
    assert style.startswith(_templates()["page.css"])
    table = gear.shipped_slot_table()
    rules = dict(
        re.findall(r"\.gear-name\.(colour-[0-9a-z_]+) \{ color: #([0-9a-f]{6}); \}", style)
    )
    expected = {}
    for code in table.quality:
        rgb = gear.colour_rgb(code, table)
        if rgb is not None:
            expected[gear.colour_class(code)] = rgb
    assert rules == expected
    assert rules["colour-niq4_"] == rules["colour-ffa335ee"] == "a335ee"  # Epic
    assert "colour-niq8_" not in rules  # WoW Token: no hex code in the table
    # Every colour the captures hold has a rule.
    for code in ("nIQ0:", "nIQ1:", "nIQ2:"):
        assert gear.colour_class(code) in rules, code


def test_the_colour_rules_read_on_the_gear_backing() -> None:
    """The names are bold and large (WCAG AA for large text, 3:1) on the
    gear table's own dark backing, in both schemes."""
    css = first_stage._css()
    table = re.search(r"\.gear-table \{([^}]*)\}", css)
    assert table is not None
    backing = re.search(r"background:\s*(#[0-9a-fA-F]{6})", table.group(1))
    assert backing is not None
    name = re.search(r"\.gear-name \{([^}]*)\}", css)
    assert name is not None
    assert "font-weight: 700" in name.group(1)
    size = re.search(r"font-size:\s*([0-9.]+)rem", name.group(1))
    assert size is not None and float(size.group(1)) * 16 >= 18.66
    shipped = gear.shipped_slot_table()
    for code in shipped.quality:
        rgb = gear.colour_rgb(code, shipped)
        if rgb is not None:
            ratio = first_stage._contrast("#" + rgb, backing.group(1))
            assert ratio >= 3, (code, rgb, round(ratio, 2))


# ─── the colour helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("nIQ2:", "colour-niq2_"),
        ("ff1EFF00", "colour-ff1eff00"),
        ("nIQ2", "colour-niq2"),
        ("n|IQ2:", None),
        ("", None),
        ("x" * 17, None),
    ],
    ids=[
        "named",
        "hex-mixed-case",
        "no-colon",
        "constructed-bar",
        "constructed-empty",
        "constructed-long",
    ],
)
def test_colour_class(code: str, name: str | None) -> None:
    assert gear.colour_class(code) == name


def test_colour_rgb_from_the_table() -> None:
    table = gear.shipped_slot_table()
    assert gear.colour_rgb("nIQ2:", table) == "1eff00"
    assert gear.colour_rgb("NIQ3:", table) == "0070dd"  # case folded, as quality_word does
    assert gear.colour_rgb("FFA335EE", table) == "a335ee"
    assert gear.colour_rgb("ff123456", table) == "123456"  # a hex code is its own colour
    assert gear.colour_rgb("nIQ8:", table) is None  # WoW Token
    assert gear.colour_rgb("nIQ99:", table) is None
    mine = gear.parse_slot_table('[quality]\n"nIQ2:" = "Rare"\n', table)
    assert gear.colour_rgb("nIQ2:", mine) == "0070dd"
