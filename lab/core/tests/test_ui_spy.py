"""Seen by Spy, on the Crew tab of `wowlab ui` (M22-01, UI-first).

The panel lists what the Spy addon saved from `spy list --json`, with its
search, filter and order as that command's own options; a row opens what
`spy show --json` prints. Both only read. The install is a copy of the
captured `fixtures/macos-70888/` in `tmp_path` with the real Spy capture
(`fixtures/macos-71037/`) dropped in; tests named `constructed` add a file
built in the test, with invented names.

The page is driven under Node with the harness of `test_ui_crew.py` (the
stand-in DOM of `test_ui_explorer.py`; HTML sinks throw; navigation,
attributes outside a closed set and elements such as `a`, `img` and
`script` are trapped), routing every `crew` and `spy` run to the real
server and the real CLI, through the shim of `test_ui_confirm_writes.py`:
the user data directory is redirected into `tmp_path` and each argv is
logged. Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_ui_crew as crew_tab
from test_ui_confirm import _commands, _post
from test_ui_confirm_writes import _SHIM, _serving
from test_ui_explorer import _node, _page_ids

from wowlab_core import ui

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_forever_"
TYRONE = "WTF/Account/LABACCOUNTA/1/Tyrone-Hamberglar/SavedVariables/Spy.lua"
NL = "\r\n"
_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"

_ROUTE_OLD = 'body.command[0] === "crew")'
_ROUTE_NEW = '(body.command[0] === "crew" || body.command[0] === "spy"))'

_SPY_VIEW = r"""
function spyEls() {
  const out = [];
  for (const [id, e] of Object.entries(ids)) if (id.startsWith("spy-")) out.push(e, ...e.all());
  return out;
}
function spyView() {
  const table = ids["spy-table"];
  const rows = table.all().filter((e) => e.tagName === "tr" && has(e, "hub-row"));
  return {
    status: ids["spy-status"].textContent,
    tableHidden: table.hidden,
    heads: table.all().filter((e) => e.tagName === "th").map((e) => e.textContent),
    rows: rows.map((tr) => tr.children.map((c) => c.textContent)),
    warnings: ids["spy-warnings"].children.map((c) => c.textContent),
    notes: ids["spy-notes"].children.map((c) => c.textContent),
    warningsHidden: ids["spy-warnings"].hidden,
    player: { hidden: ids["spy-player"].hidden, title: ids["spy-player-title"].textContent,
      lines: ids["spy-player-lines"].children.map((c) => c.textContent) },
    drawn: spyEls().filter((e) => !(e.id in ids)).map((e) => ({ tag: e.tagName, cls: e.className, id: e.id,
      title: e.title, attrs: Object.keys(e.attrs), href: e.href === undefined ? null : String(e.href),
      src: e.src === undefined ? null : String(e.src) })),
    text: spyEls().map((e) => e._text + "\n" + (e.title || "")).join("\n"),
  };
}
"""

_SPY_STEPS = r"""
      } else if (step.spy_click !== undefined) {
        const before = requests.length;
        ids[step.spy_click].fire("click"); await settle();
        out.steps.push(Object.assign(spyView(), { newRequests: requests.slice(before) }));
      } else if (step.spy_set !== undefined) {
        const before = requests.length;
        const el = ids[step.spy_set];
        el.value = step.value; el.fire(step.event || "change"); await settle();
        if (step.wait) await new Promise((r) => setTimeout(r, step.wait));
        await settle();
        out.steps.push(Object.assign(spyView(), { newRequests: requests.slice(before) }));
      } else if (step.spy_open !== undefined) {
        const before = requests.length;
        const opens = ids["spy-table"].all().filter((e) => e.tagName === "button" && has(e, "hub-open"));
        if (!opens[step.spy_open]) { out.steps.push({ missing: "open " + step.spy_open }); continue; }
        opens[step.spy_open].fire("click"); await settle();
        out.steps.push(Object.assign(spyView(), { newRequests: requests.slice(before) }));
"""


def _harness() -> str:
    text = crew_tab._harness()
    assert text.count(_ROUTE_OLD) == 1
    text = text.replace(_ROUTE_OLD, _ROUTE_NEW, 1)
    text = text.replace(crew_tab._VIEW_ANCHOR, _SPY_VIEW + crew_tab._VIEW_ANCHOR, 1)
    return text.replace(crew_tab._ANCHOR, _SPY_STEPS.lstrip("\n") + crew_tab._ANCHOR, 1)


class Served:
    def __init__(self, server: ui.UiServer, root: Path, log: Path) -> None:
        self.server = server
        self.root = root
        self.log = log

    def report(self, path: tuple[str, ...], params: dict[str, object]) -> dict[str, Any]:
        status, payload = _post(self.server, {"command": list(path), "params": params})
        assert status == 200, payload
        assert payload["exit_code"] == 0, payload["stderr"]
        value: dict[str, Any] = json.loads(payload["stdout"])
        return value

    def argvs(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from wowlab_core import install as discovery

    monkeypatch.delenv(discovery.ENV_FLAVOR, raising=False)
    made: list[Any] = []

    def build(extra: dict[str, bytes] | None = None) -> Served:
        folder = tmp_path / f"m22-01-{len(made)}"
        root = folder / "World of Warcraft"
        root.mkdir(parents=True)
        capture = FIXTURES / "macos-70888"
        shutil.copy2(capture / ".build.info", root / ".build.info")
        shutil.copytree(capture / "forever", root / FLAVOR)
        shutil.copytree(
            FIXTURES / "macos-71037" / "forever" / "WTF", root / FLAVOR / "WTF", dirs_exist_ok=True
        )
        for rel, data in (extra or {}).items():
            (root / FLAVOR / rel).write_bytes(data)
        monkeypatch.setenv(discovery.ENV_ROOT, str(root))
        data_dir = folder / "userdata"
        log = folder / "argv.jsonl"
        context = _serving(wowlab_argv=[sys.executable, "-c", _SHIM, str(data_dir), str(log)])
        server = context.__enter__()
        made.append(context)
        return Served(server, root, log)

    yield build
    for context in made:
        context.__exit__(None, None, None)


def _drive(served: Served, folder: Path, steps: list[dict[str, Any]]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    scenario = {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": {"commands": _commands(served.server), "home": "", "sep": os.sep},
        "first_flavor": FLAVOR,
        "trees": {},
        "explains": {},
        "runs": [],
        "held": [],
        "steps": [{"crew_tab": True}, *steps],
        "crew_server": f"http://127.0.0.1:{served.server.port}",
        "crew_token": served.server.token,
        "outcomes": {
            str(code): ui._outcome(ui._Run(code, [], [], False, False), None) for code in range(4)
        },
    }
    harness = folder / "m22-01-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m22-01-scenario.json"
    data.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run(
        [_node(), str(harness), str(_TEMPLATES / "page.js"), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert "error" not in out, out["error"]
    assert not out["traps"], out["traps"]
    out["steps"] = out["steps"][1:]
    return out


def _bodies(step: dict[str, Any]) -> list[dict[str, Any]]:
    return [r["body"] for r in step["newRequests"]]


_DRAWN_CLASSES = {"", "char-table hub crew", "unseen", "hub-row", "hub-open"}


def _inert(view: dict[str, Any]) -> None:
    """Nothing the panel drew carries Spy's data outside text."""
    for e in view["drawn"]:
        assert e["cls"] in _DRAWN_CLASSES, e
        assert e["id"] == "" and e["href"] is None and e["src"] is None, e
        assert e["tag"] in {
            "table",
            "caption",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "button",
            "li",
            "option",
            "#text",
        }, e
        assert e["title"] in ("", "Show everything Spy saved about this player"), e
    assert not crew_tab._RAW.search(view["text"])


def test_the_panel_reads_only_when_asked_and_draws_the_list(served: Any, tmp_path: Path) -> None:
    run = served()
    report = run.report(("spy", "list"), {"sort": "last"})
    out = _drive(run, tmp_path / "node", [{"spy_click": "spy-show"}])
    (view,) = out["steps"]
    # Opening the tab reads the crew book only; the button reads Spy's data.
    spy_runs = [r for r in out["requests"] if r["body"] and r["body"]["command"][0] == "spy"]
    assert spy_runs == view["newRequests"]
    assert _bodies(view) == [{"command": ["spy", "list"], "params": {"sort": "last"}}]
    assert view["status"] == report["words"]
    assert view["heads"] == [
        "Player",
        "Who",
        "Guild",
        "Last seen",
        "Place Spy last recorded",
        "Seen by",
    ]
    # The standing notes, under the panel, as the command gives them.
    assert view["notes"] == report["notes"] and len(view["notes"]) == 8
    assert len(view["rows"]) == 24
    assert [r[0] for r in view["rows"]] == [p["key"] for p in report["players"]]
    first = view["rows"][0]
    assert first[1] == "level 24 Gnome Warlock, Alliance, hostile when Spy last looked"
    assert first[2] == "The Beef Covenant"
    assert first[4] == "The Barrens: Ratchet" and first[5] == "Sandgraal-Rizzen"
    sonja = next(r for r in view["rows"] if r[0] == "Sonja-Frayle")
    assert sonja[4] == "Wailing Caverns"
    assert view["warningsHidden"] is True
    _inert(view)
    assert all(argv[:2] in (["crew", "list"], ["spy", "list"]) for argv in run.argvs())


def test_the_filters_are_the_commands_options(served: Any, tmp_path: Path) -> None:
    run = served()
    steps: list[dict[str, Any]] = [
        {"spy_click": "spy-show"},
        {"spy_set": "spy-filter", "value": "kos"},
        {"spy_set": "spy-sort", "value": "level"},
        {"spy_set": "spy-filter", "value": "enemies"},
        {"spy_set": "spy-search", "value": "living", "event": "input", "wait": 400},
    ]
    out = _drive(run, tmp_path / "node", steps)
    _, kos, level, enemies, search = out["steps"]
    assert _bodies(kos) == [
        {"command": ["spy", "list"], "params": {"kos_only": True, "sort": "last"}}
    ]
    assert kos["rows"] == [] and kos["status"] == "No row matches." and kos["tableHidden"]
    assert _bodies(level) == [
        {"command": ["spy", "list"], "params": {"kos_only": True, "sort": "level"}}
    ]
    assert _bodies(enemies) == [
        {"command": ["spy", "list"], "params": {"enemies": True, "sort": "level"}}
    ]
    assert enemies["rows"][0][0] == "Goruthuz-Mugstout"  # level 43, the highest
    assert _bodies(search) == [
        {
            "command": ["spy", "list"],
            "params": {"search": "living", "enemies": True, "sort": "level"},
        }
    ]
    assert len(search["rows"]) == 5 and search["status"] == "Showing 5 of 24 rows."


def test_a_row_opens_what_spy_show_prints(served: Any, tmp_path: Path) -> None:
    run = served()
    out = _drive(run, tmp_path / "node", [{"spy_click": "spy-show"}, {"spy_open": 0}])
    _, opened = out["steps"]
    key = "Nefzik-Spoonz"
    assert _bodies(opened) == [{"command": ["spy", "show"], "params": {"name": key}}]
    report = run.report(("spy", "show"), {"name": key})
    assert opened["player"]["hidden"] is False and opened["player"]["title"] == key
    assert opened["player"]["lines"] == [line.strip() for line in report["lines"]]
    _inert(opened)


def test_constructed_a_hostile_name_and_a_version_warning_show_as_text(
    served: Any, tmp_path: Path
) -> None:
    hostile = "-Evil\x1b[31m" + chr(0x202E) + "x-Fakerealm"
    body = NL.join(
        [
            "",
            "SpyPerCharDB = {",
            '["version"] = "9.9",',
            '["PlayerData"] = {',
            f'["{hostile}"] = {{',
            '["guild"] = "<b>Invented</b>",',
            '["zone"] = "Invented' + chr(0x2028) + 'Vale",',
            '["time"] = 1795500000,',
            "},",
            "},",
            "}",
            "",
        ]
    ).encode()
    run = served({TYRONE: body})
    out = _drive(run, tmp_path / "node", [{"spy_click": "spy-show"}, {"spy_open": 0}])
    listed, opened = out["steps"]
    assert listed["warningsHidden"] is False and "9.9" in listed["warnings"][0]
    row = listed["rows"][0]
    assert row[0].startswith("-Evil⟨U+001B⟩[31m⟨U+202E⟩x-Fakerealm")
    assert row[2] == "<b>Invented</b>" and row[3] != "not recorded"
    assert row[4] == "Invented⟨U+2028⟩Vale"
    # The name starting with `-` is sent as the argument it is.
    assert _bodies(opened) == [{"command": ["spy", "show"], "params": {"name": hostile}}]
    assert opened["player"]["lines"][0].startswith("-Evil⟨U+001B⟩")
    for view in (listed, opened):
        _inert(view)
        assert re.search(r"<b>", view["text"])  # shown as text, never as markup
