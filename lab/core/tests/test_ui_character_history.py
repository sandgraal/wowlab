"""The Character tab's history panel (M17-03, docs/LAB_PLAN.md §19 CW-2).

Driven under Node on the harness of `test_ui_character.py` (the stand-in DOM
of `test_ui_explorer.py`: HTML sinks throw; navigation, attributes outside
the closed set, SVG outside its closed set of elements and attributes, and
forbidden elements are trapped), fed what the real CLI printed. The install
is the captured tree (`fixtures/macos/`) copied into `tmp_path` with its
flavor folder named as its provenance row says.

The history with several saves is `wowlab char history --json` run here,
over a snapshot store in `tmp_path` (the user data directory redirected, as
`test_cli_char_history.py` does): the picked character's WowLab.lua is given
three real lab-addon records, one per snapshot, each with its own save time,
then the first record again as the file in the install (a restore):

1. `macos/`'s record of this character (schema 1, client 70009);
2. `macos-70058/`'s record of this character (schema 1, client 70058);
3. `macos-70425/`'s record of `Labchard-Labrealme` (schema 3);
4. live: the bytes of 1 again, at a later time.

Putting another character folder's record in this one is the constructed
part of the sequence (`cross-capture` in the test ids). Cases labelled
`constructed` are boundary or hostile inputs (L8): a file that cannot be
read, a refused or failed run, and report text replaced with markup and
control characters. Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import platformdirs
import pytest
import test_ui_character as gear_tab
import test_ui_explorer as first_stage
from test_ui_character import ACCOUNT, NEWER, NEWER_NS, _gear_params, _lab_file, _local
from test_ui_explorer import FLAVOR, IMG, RLO, _node
from typer.testing import CliRunner

from wowlab_core import cli, snapshot

home = first_stage.home
install = first_stage.install
server = first_stage.server
timed = gear_tab.timed

_TEMPLATES = gear_tab._TEMPLATES
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SV = "SavedVariables/WowLab.lua"
HISTORY = ["char", "history"]

DAY = 86_400 * 10**9
T1 = NEWER_NS
T2 = T1 + DAY
T3 = T2 + DAY
T_LIVE = T3 + DAY


def _record(tree: str, character: str) -> bytes:
    return (FIXTURES / tree / "forever" / "WTF" / "Account" / ACCOUNT / character / SV).read_bytes()


R1 = _record("macos", NEWER)
R2 = _record("macos-70058", NEWER)
R3 = _record("macos-70425", "1/Labchard-Labrealme")


# ─── the harness: the gear tab's, with a step that reads this panel ──────────

_VIEW = r"""
function historyView() {
  const box = ids["char-history"];
  const all = box.all();
  const cls = (c) => all.filter((e) => has(e, c));
  const table = cls("history-table")[0] || null;
  const list = cls("history-list")[0] || null;
  return {
    text: box.textContent,
    lines: all.filter((e) => e.tagName === "p").map((e) => ({ cls: e.className, text: e.textContent })),
    errors: cls("char-error").map((e) => e.textContent),
    captions: cls("history-caption").map((e) => e.textContent),
    charts: all.filter((e) => e.tagName === "svg").map((s) => ({
      ns: s.ns, viewBox: s.getAttribute("viewBox"), hidden: s.getAttribute("aria-hidden"),
      lines: s.all().filter((e) => e.tagName === "polyline" && has(e, "history-line")).map((e) => e.getAttribute("points")),
      dots: s.all().filter((e) => e.tagName === "circle").map((e) => [Number(e.getAttribute("cx")), Number(e.getAttribute("cy"))]),
      labels: s.all().filter((e) => e.tagName === "text").map((e) => e.textContent),
      tags: s.all().map((e) => e.tagName) })),
    table: table && {
      role: table.getAttribute("role"), label: table.getAttribute("aria-label"), tabIndex: table.tabIndex,
      caption: (table.all().find((e) => e.tagName === "caption") || { textContent: null }).textContent,
      head: table.all().filter((e) => e.tagName === "th" && e.scope === "col").map((e) => e.textContent),
      rows: table.all().filter((e) => e.tagName === "tr").slice(1).map((tr) => ({
        cells: tr.children.map((c) => c._text), restored: tr.children[0].textContent, title: tr.children[0].title,
        ilvlTitle: tr.children[2].title,
        scope: tr.children[0].scope })) },
    changes: list ? list.children.map((li) => ({ when: li.children[0].textContent,
      items: li.children[1].children.map((c) => c.textContent) })) : [],
    headings: all.filter((e) => e.tagName === "h4").map((e) => e.textContent),
    missing: cls("char-slots").map((ul) => ul.children.map((li) => li.textContent)),
    notes: cls("char-notes").flatMap((ul) => ul.children.map((li) => li.textContent)),
    summary: all.filter((e) => e.tagName === "summary").map((e) => e.textContent),
    status: ids["char-status"].textContent,
    gearRows: ids["char-gear"].all().filter((e) => e.tagName === "tr" && has(e, "gear-row")).length,
  };
}
"""

_STEPS = r"""
      } else if (step.history !== undefined) {
        out.steps.push(historyView());
"""


def _harness() -> str:
    text = gear_tab._harness()
    assert text.count(gear_tab._STEP_ANCHOR) == 1 and text.count(gear_tab._VIEW_ANCHOR) == 1
    text = text.replace(gear_tab._VIEW_ANCHOR, _VIEW + gear_tab._VIEW_ANCHOR, 1)
    return text.replace(gear_tab._STEP_ANCHOR, _STEPS.lstrip("\n") + gear_tab._STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    import subprocess

    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m17-03-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m17-03-scenario.json"
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


# ─── the store, and the report the CLI prints for it ─────────────────────────


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user data and config directories for this process, in `tmp_path`
    (on Windows platformdirs does not follow the environment, so the server's
    child cannot be pointed at them; the report is run here instead)."""
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: tmp_path / "config")
    return data


def _put(root: Path, data: bytes, mtime_ns: int) -> None:
    path = _lab_file(root, NEWER)
    path.write_bytes(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _snap(root: Path, day: int) -> None:
    snapshot.SnapshotStore().create(
        root,
        [f"{FLAVOR}/WTF"],
        flavor_folder=FLAVOR,
        now=datetime(2026, 11, day, 12, 0, 0, tzinfo=UTC),
    )


def _history_json(*, exit_code: int = 0) -> str:
    args = ["char", "history", "--character", NEWER, "--account", ACCOUNT, "--flavor", FLAVOR]
    done = CliRunner().invoke(cli.app, [*args, "--json"])
    assert done.exit_code == exit_code, done.output
    return done.stdout


@pytest.fixture
def several(timed: Path, store: Path) -> dict[str, Any]:
    """Three saves in three snapshots, then the first restored as the live file."""
    for day, (data, ns) in enumerate(((R1, T1), (R2, T2), (R3, T3)), start=1):
        _put(timed, data, ns)
        _snap(timed, day)
    _put(timed, R1, T_LIVE)
    report: dict[str, Any] = json.loads(_history_json())
    assert [r["saved_at_ns"] for r in report["rows"]] == [T1, T2, T3, T_LIVE]
    assert report["rows"][3]["same_bytes_as_ns"] == T1 and report["rows"][3]["live"]
    return report


def _set_history(scenario: dict[str, Any], report: dict[str, Any] | str) -> None:
    text = report if isinstance(report, str) else json.dumps(report)
    for run in scenario["runs"]:
        if run["command"] == HISTORY and run["params"] == _gear_params(NEWER):
            run["stdout"] = text
            return
    raise AssertionError("no history run")


def _scenario(server: Any, report: dict[str, Any] | str | None = None) -> dict[str, Any]:
    scenario = gear_tab._scenario(server, [{"char_tab": True}, {"history": True}], [NEWER])
    if report is not None:
        _set_history(scenario, report)
    return scenario


def _spec(row: dict[str, Any]) -> str:
    if row["spec"]["id"] is None:
        return "not recorded"
    return row["spec_name"] or f"spec id {row['spec']['id']}"


def _num(value: Any) -> str:
    return "not recorded" if value is None else str(value)


# ─── several saves ───────────────────────────────────────────────────────────


def test_several_saves_draw_two_charts_and_a_table_cross_capture(
    server: Any, several: dict[str, Any], tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, several))
    view = out["steps"][1]
    rows = several["rows"]
    assert view["errors"] == []
    day = lambda ns: _local(ns).split(" ")[0]  # noqa: E731
    assert view["lines"][0] == {
        "cls": "char-count",
        "text": f"4 saves, {day(T1)} to {day(T_LIVE)}. A time is the file's modification time: "
        "when the game wrote it at logout or /reload, or when a restore put it back.",
    }
    # Two charts, SVG in the SVG namespace, hidden from screen readers.
    assert len(view["charts"]) == 2
    for chart in view["charts"]:
        assert chart["ns"] == "http://www.w3.org/2000/svg"
        assert chart["hidden"] == "true" and chart["viewBox"] == "0 0 640 150"
        assert set(chart["tags"]) <= {"polyline", "circle", "text"}
        assert chart["labels"][-2:] == [day(T1), day(T_LIVE)]
    ilvl, level = view["charts"]
    for chart, field in ((ilvl, "item_level"), (level, "level")):
        values = [r[field] for r in rows]
        known = [v for v in values if v is not None]
        assert len(chart["dots"]) == len(known) >= 2, field
        # One step per save, oldest left; a higher value is drawn higher.
        xs = [x for x, _ in chart["dots"]]
        assert xs == sorted(xs) and len(set(xs)) == len(xs)
        top = max(known)
        for (_, y), v in zip(chart["dots"], known, strict=True):
            assert (y == min(y for _, y in chart["dots"])) == (v == top), (field, v)
        assert chart["labels"][0] == (f"{top:.1f}" if field == "item_level" else str(top))
    known = [r["item_level"] for r in rows if r["item_level"] is not None]
    levels = [r["level"] for r in rows if r["level"] is not None]
    # Lowest and highest, never "from ... to" (which reads as a gain), and
    # the steps are said not to be time.
    assert view["captions"] == [
        "Equipped item level (the client's average, as the addon recorded it): "
        f"lowest {min(known):.1f}, highest {max(known):.1f}, one step per save (not spaced by "
        "time).",
        f"Level: lowest {min(levels)}, highest {max(levels)}, one step per save (not spaced by "
        "time).",
    ]
    # The table: the charts' text alternative, reachable and labelled.
    table = view["table"]
    assert table["role"] == "region" and table["label"] == "Each save as a table"
    assert table["tabIndex"] == 0
    assert table["caption"] == "Each save, oldest first: the values the charts draw"
    assert table["head"] == ["Saved", "Level", "Equipped item level", "Spec", "Held in"]
    assert len(table["rows"]) == 4
    for drawn, row in zip(table["rows"], rows, strict=True):
        assert drawn["scope"] == "row"
        cells = drawn["cells"]
        assert cells[0] == _local(row["saved_at_ns"])
        assert cells[1] == _num(row["level"]) or cells[1].startswith("not recorded")
        if row["item_level"] is not None:
            # One decimal shown; the exact figure in the title.
            assert cells[2] == f"{row['item_level']:.1f}"
            assert drawn["ilvlTitle"] == f"exact: {row['item_level']:g}"
        assert cells[3] == _spec(row) or (row["spec"]["id"] is None and "not recorded" in cells[3])
    assert [r["cells"][4] for r in table["rows"]] == [
        "1 snapshot",
        "1 snapshot",
        "1 snapshot",
        "the game folder",
    ]
    # The restored save is flagged, with the save whose contents it has.
    restored = table["rows"][3]
    assert restored["restored"] == f"{_local(T_LIVE)}, likely restored (same as {_local(T1)})"
    assert restored["title"] == (
        f"Same contents as the save of {_local(T1)}: likely restored, so this time is when it "
        "was put back."
    )
    assert all(not r["title"] for r in table["rows"][:3])


def test_the_changes_are_newest_first_in_player_words_cross_capture(
    server: Any, several: dict[str, Any], tmp_path: Path
) -> None:
    out = _drive(tmp_path / "node", _scenario(server, several))
    view = out["steps"][1]
    rows = several["rows"]
    assert "Changes, newest first" in view["headings"]
    assert [c["when"] for c in view["changes"]] == [_local(r["saved_at_ns"]) for r in rows[::-1]]
    newest, *_, oldest = view["changes"]
    assert newest["items"][0].startswith(f"Same contents as the save of {_local(T1)}")
    assert oldest["items"] == ["The oldest save wowlab has."]
    for drawn, row in zip(view["changes"][:-1], rows[:0:-1], strict=True):
        text = "\n".join(drawn["items"])
        # Every gear change names its slot from the report, never from the page.
        for g in row["gear_changes"]:
            slot = g["slot_name"] or f"slot {g['slot']}"
            assert any(item.startswith(slot + ": ") for item in drawn["items"]), (slot, text)
            for side in (g["before"], g["after"]):
                if side is not None:
                    assert side["name"] in text
        for c in row["currency_changes"]:
            name = c["name"] or f"currency {c['id']}"
            before = "not listed" if c["before"] is None else str(c["before"])
            after = "not listed" if c["after"] is None else str(c["after"])
            assert f"{name}: {before} to {after}" in drawn["items"]
        for reason in (row["gear_not_compared"], row["currencies_not_compared"]):
            if reason is not None:
                assert reason in text
        if row["currency_panel_changed"] is not None:
            assert row["currency_panel_changed"] in drawn["items"]
    # The capture's sequence has gear and level changes to show.
    assert any(r["gear_changes"] for r in rows[1:])
    levels = [r["level"] for r in rows]
    for i in range(1, 4):
        was, now = levels[i - 1], levels[i]
        drawn = view["changes"][3 - i]["items"]
        if was is not None and now is not None and was != now:
            assert f"Level {was} to {now}" in drawn
        else:
            assert not any(item.startswith("Level ") for item in drawn)
    # Saves 1 and 2 record the same values and gear: nothing changed in
    # what both record.
    assert view["changes"][2]["items"] == [
        "No change in what both saves record (level, equipped item level, spec, gear, currencies)."
    ]
    # The report's notes, behind a closed summary.
    assert view["notes"] == several["notes"]
    assert view["summary"] == [f"{len(several['notes'])} notes on what these figures are"]


# ─── zero and one save ───────────────────────────────────────────────────────

_FEW = (
    "History appears once there are two different saves of this character (the file in the game "
    "folder counts as one). The game saves it when you log out or type /reload; take a backup "
    "after you log out or /reload (Take a backup now on the Account tab, or wowlab snap create); "
    "a backup taken while you are still playing keeps the save from before."
)


def test_one_save_says_what_makes_history_appear(
    server: Any, timed: Path, store: Path, tmp_path: Path
) -> None:
    """An empty store: the file in the install alone."""
    report = json.loads(_history_json())
    assert len(report["rows"]) == 1
    scenario = _scenario(server, report)
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    assert [line["text"] for line in view["lines"]] == [
        f"One save of this character so far, written {_local(NEWER_NS)}.",
        _FEW,
    ]
    assert view["charts"] == [] and view["changes"] == []
    assert len(view["table"]["rows"]) == 1
    assert view["table"]["rows"][0]["cells"][4] == "the game folder"
    assert view["errors"] == [] and view["status"] == ""


def test_no_save_that_can_be_read_shows_why_constructed(
    server: Any, timed: Path, store: Path, tmp_path: Path
) -> None:
    """Constructed: the picked character's file replaced, after the list was
    read, by text that is not a lab record. `char history` exits 1 and still
    prints its report, which the panel draws: no save, the advice, and the
    copy it could not read with the reason."""
    scenario = gear_tab._scenario(server, [{"char_tab": True}, {"history": True}], [NEWER])
    path = _lab_file(timed, NEWER)
    path.write_bytes(b"m17-03 constructed: not a lab record\n")
    os.utime(path, ns=(NEWER_NS, NEWER_NS))
    text = _history_json(exit_code=1)
    report = json.loads(text)
    assert report["rows"] == [] and len(report["unreadable"]) == 1
    scenario["held"].append(
        {
            "command": HISTORY,
            "params": _gear_params(NEWER),
            "reply": {
                "exit_code": 1,
                "timed_out": False,
                "truncated": False,
                "stdout": text,
                "stderr": "",
            },
        }
    )
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    assert [line["text"] for line in view["lines"]] == [
        "No save of this character could be read.",
        _FEW,
    ]
    assert view["table"] is None and view["charts"] == []
    assert view["headings"] == ["Could not read 1 copy"]
    assert view["missing"] == [
        [f"the file in the game folder: {report['unreadable'][0]['reason']}"]
    ]


# ─── errors and hostile text ─────────────────────────────────────────────────


def test_a_refused_or_failed_history_run_shows_the_cli_text_constructed(
    server: Any, timed: Path, tmp_path: Path
) -> None:
    for reply, refused, shown in (
        ({"error": "busy"}, True, "The server refused this request: busy"),
        (
            {
                "exit_code": 2,
                "timed_out": False,
                "truncated": False,
                "stdout": "",
                "stderr": "wowlab: m17-03 constructed failure\n",
            },
            False,
            "wowlab: m17-03 constructed failure",
        ),
    ):
        scenario = _scenario(server)
        scenario["held"].append(
            {"command": HISTORY, "params": _gear_params(NEWER), "reply": reply, "refused": refused}
        )
        out = _drive(tmp_path / f"node-{refused}", scenario)
        view = out["steps"][1]
        assert view["errors"] == [shown]
        # The other panels are drawn all the same.
        assert view["gearRows"] == 19 and view["status"] == ""


def test_hostile_report_text_draws_as_text_constructed(
    server: Any, several: dict[str, Any], tmp_path: Path
) -> None:
    """Constructed: names, reasons and values in the report replaced with
    markup, control characters and values of the wrong type."""
    report = json.loads(json.dumps(several))
    rows = report["rows"]
    changed = next(r for r in rows if r["gear_changes"])
    g = changed["gear_changes"][0]
    g["slot_name"] = "<script>x</script>"
    if g["after"] is not None:
        g["after"]["name"] = IMG + RLO
    rows[1]["spec_name"] = IMG
    rows[1]["spec"]["id"] = 7
    rows[2]["level"] = "<b>12</b>"
    rows[2]["level_absent"] = "<i>why</i>" + RLO
    rows[0]["item_level"] = "NaN"
    rows[1]["currency_panel_changed"] = "<a href=x>panel</a>"
    report["unreadable"] = [
        {
            "where": "snapshot",
            "snapshot_id": "<img>",
            "created_at": None,
            "saved_at_ns": None,
            "reason": RLO + "bad",
        }
    ]
    out = _drive(tmp_path / "node", _scenario(server, report))
    view = out["steps"][1]
    text = view["text"]
    assert "<script>x</script>: " in text
    assert IMG in text and RLO not in text and "⟨U+202E⟩" in text
    assert view["table"]["rows"][2]["cells"][1] == "not recorded: <i>why</i>⟨U+202E⟩"
    assert view["table"]["rows"][0]["cells"][2] == "not recorded"
    assert view["table"]["rows"][1]["cells"][3] == IMG
    assert "<a href=x>panel</a>" in view["changes"][2]["items"]
    assert view["missing"] == [["snapshot <img>: ⟨U+202E⟩bad"]]
    # The item level chart draws only the values that are numbers.
    known = [r["item_level"] for r in several["rows"][1:] if r["item_level"] is not None]
    assert len(view["charts"][0]["dots"]) == len(known)


# ─── the script ──────────────────────────────────────────────────────────────


def _section() -> str:
    whole = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    start = whole.index("// ─── the history panel (M17-03")
    return whole[start : whole.index("// ─── the looks tab (M14-04", start)]


def test_the_panel_runs_only_char_history_and_holds_no_game_table() -> None:
    script = _section()
    runs = re.findall(r"runJson\(\s*(\[[^\]]*\])", script)
    assert runs == ['["char", "history"]']
    # Every slot, item, spec and currency name is the report's.
    assert "slot_name" in script and "spec_name" in script
    for word in ("snap create", "restore", "undo"):
        assert f'runJson(["{word}' not in script


def test_gear_changes_are_worded_as_the_cli_words_them_cross_capture(
    server: Any, several: dict[str, Any], tmp_path: Path
) -> None:
    """Constructed from the real report: one change made another instance of
    the same item id (bonus ids, and its item level), one the same instance
    at another item level, and a level only one save records."""
    report = json.loads(json.dumps(several))
    row = next(r for r in report["rows"] if len(r["gear_changes"]) >= 2)
    at = report["rows"].index(row)
    first, second = row["gear_changes"][:2]
    for g in (first, second):
        assert g["before"] is not None
        if g["after"] is None:
            g["after"] = dict(g["before"])
    first["after"]["item_level"] = (first["before"]["item_level"] or 0) + 3
    first["differs"] = ["bonus ids", "item level"]
    second["after"]["name"] = second["before"]["name"]
    second["after"]["item_level"] = (second["before"]["item_level"] or 0) + 1
    second["differs"] = ["item level"]
    row["level"] = None
    out = _drive(tmp_path / "node", _scenario(server, report))
    items = out["steps"][1]["changes"][len(report["rows"]) - 1 - at]["items"]

    def item(side: dict[str, Any]) -> str:
        return f"{side['name']} (item level {side['item_level']})"

    b, a = first["before"], first["after"]
    assert (
        f"{first['slot_name']}: {item(a)}, same item id, a different instance (bonus ids); "
        f"before: {item(b)}; item level {b['item_level']} to {a['item_level']} (the client's "
        "figure)"
    ) in items
    b, a = second["before"], second["after"]
    assert (
        f"{second['slot_name']}: {a['name']}, same instance, item level {b['item_level']} to "
        f"{a['item_level']} (the client's figure)"
    ) in items
    assert "Level: not recorded in one of the two saves." in items
    assert not any("the same item with" in i for i in items)
