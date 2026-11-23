"""The Look up tab of `wowlab ui` (M22-08): a search box over `lookup
--json`.

The tab runs `lookup` with the search text as its argument, the kind as its
option and `fetch` from the download offer, through the server's fixed-argv
CLI path like every other leaf (no new route). Each hit's heading and lines
are the command's own text, drawn as text through `visible()`.

The install is the captured tree `fixtures/macos-70888/` copied into
`tmp_path`, CONSTRUCTED as in `test_cli_lookup.py`: its version moved to
1.60.1.71037 and a constructed QuestieDB slice (`_questie_build.py`; no
byte of the third-party data is committed) under its AddOns. The recorded
game-table subsets are placed in the redirected user data directory. The
page is driven under Node with the stand-in DOM of `test_ui_explorer.py`
(HTML sinks throw; navigation, attributes outside a closed set and elements
such as `a`, `img`, `form` and `script` are trapped); every lookup the page
asks for goes through the real server to the real CLI (the shim of
`test_ui_confirm_writes.py`), but a run with `fetch`, which would reach
wago.tools, is answered from the scenario. Nothing reads or writes a real
install and no request leaves the process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_ui_character as char_tab
from _questie_build import FLAVOR, SLICE, VERSION, place, slice_toc, toc_text
from test_ui_confirm import _commands, _post
from test_ui_confirm_writes import _SHIM, _serving
from test_ui_explorer import _node, _page_ids

from wowlab_core import ui

LAUNCH = Path(__file__).resolve().parent / "fixtures" / "macos-70888"
WAGO = Path(__file__).resolve().parent / "fixtures" / "wago"
_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"


class Served:
    def __init__(self, server: ui.UiServer, root: Path, data: Path, log: Path) -> None:
        self.server = server
        self.root = root
        self.data = data
        self.log = log

    def reply(self, params: dict[str, object]) -> dict[str, Any]:
        status, payload = _post(self.server, {"command": ["lookup"], "params": params})
        assert status == 200, payload
        value: dict[str, Any] = payload
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

    def build(toc: bytes | None, tables: tuple[str, ...] = ("ItemSparse", "AreaTable")) -> Served:
        folder = tmp_path / f"m22-08-{len(made)}"
        root = folder / "World of Warcraft"
        root.mkdir(parents=True)
        info = (LAUNCH / ".build.info").read_bytes().replace(b"1.60.1.70888", VERSION.encode())
        (root / ".build.info").write_bytes(info)
        shutil.copytree(LAUNCH / "forever", root / FLAVOR)
        if toc is not None:
            place(root / FLAVOR, toc)
        data = folder / "userdata"
        cache = data / "gamedata" / "tables" / VERSION
        cache.mkdir(parents=True)
        for table in tables:
            shutil.copy2(WAGO / f"{table}.{VERSION}.subset.csv", cache / f"{table}.csv")
        monkeypatch.setenv(discovery.ENV_ROOT, str(root))
        log = folder / "argv.jsonl"
        context = _serving(wowlab_argv=[sys.executable, "-c", _SHIM, str(data), str(log)])
        server = context.__enter__()
        made.append(context)
        return Served(server, root, data, log)

    yield build
    for context in made:
        context.__exit__(None, None, None)


# ─── the page, under Node, over the real server ──────────────────────────────

_VIEW = r"""
const realFetch = fetch;
let inflight = 0;
let maxInflight = 0;
const standIn = ctx.fetch;
ctx.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  const ours = body && String(url).startsWith("/api/run") && Array.isArray(body.command) && body.command[0] === "lookup";
  const held = ours && (scenario.held || []).some((h) => canon(h.command) === canon(body.command) && canon(h.params) === canon(body.params || {}));
  if (!ours || held || (body.params && body.params.fetch === true)) return standIn(url, opts);
  requests.push({ url: String(url), body: body });
  inflight += 1;
  maxInflight = Math.max(maxInflight, inflight);
  try {
    const headers = Object.assign({}, opts.headers || {}, { "X-Wowlab-Session": scenario.lookup_token });
    const reply = await realFetch(scenario.lookup_server + String(url), { method: "POST", headers: headers, body: opts.body });
    const obj = await reply.json();
    return { ok: reply.ok, status: reply.status, json: async () => obj };
  } finally {
    inflight -= 1;
  }
};
async function settle() {
  for (let i = 0; i < 3000; i++) {
    await tick();
    if (inflight === 0) {
      await tick();
      if (inflight === 0) return;
    }
    await new Promise((r) => setTimeout(r, 20));
  }
}
function lookupEls() {
  const out = [];
  for (const [id, e] of Object.entries(ids)) if (id === "lookup-view" || id.startsWith("lookup-")) out.push(e, ...e.all());
  return out;
}
function lookupView() {
  const t = (id) => ids[id].textContent;
  const hits = ids["lookup-hits"].children.map((box) => ({
    cls: box.className,
    heading: (box.children.find((c) => c.tagName === "h3") || { textContent: null }).textContent,
    lines: (box.children.find((c) => c.tagName === "ul") || { children: [] }).children.map((li) => li.textContent),
  }));
  return {
    hidden: ids["lookup-view"].hidden,
    tabSelected: ids["tab-lookup"].getAttribute("aria-selected"),
    shownViews: Object.keys(ids).filter((id) => id.endsWith("-view") && !ids[id].hidden),
    status: t("lookup-status"), statusRole: ids["lookup-status"].getAttribute("role"),
    kinds: Array.from(ids["lookup-kind"].options, (o) => [o.value, o.textContent]),
    hits: hits,
    offerHidden: ids["lookup-offer"].hidden, fetchDisabled: ids["lookup-fetch"].disabled,
    caveat: t("lookup-caveat"), caveatHidden: ids["lookup-caveat"].hidden,
    leftOut: ids["lookup-left-out"].children.map((c) => c.textContent), leftOutHidden: ids["lookup-left-out"].hidden,
    active: documentStub.activeElement ? documentStub.activeElement.id : null,
    maxInflight: maxInflight,
    drawn: lookupEls().filter((e) => !(e.id in ids)).map((e) => ({ tag: e.tagName, cls: e.className, id: e.id,
      title: e.title, attrs: Object.keys(e.attrs) })),
  };
}
"""

_STEPS = r"""
      } else if (step.lookup_tab !== undefined) {
        const before = requests.length;
        ids["tab-lookup"].fire("click"); await settle();
        out.steps.push(Object.assign(lookupView(), { newRequests: requests.slice(before) }));
      } else if (step.lookup_type !== undefined) {
        const before = requests.length;
        ids["lookup-query"].value = step.lookup_type; ids["lookup-query"].fire("input"); await settle();
        out.steps.push(Object.assign(lookupView(), { newRequests: requests.slice(before) }));
      } else if (step.lookup_burst !== undefined) {
        // Several pauses in typing while the first run is still going: each
        // pause's timer fires (one tick), no wait for the server between.
        const before = requests.length;
        for (const text of step.lookup_burst) {
          ids["lookup-query"].value = text; ids["lookup-query"].fire("input"); await tick();
        }
        await settle();
        out.steps.push(Object.assign(lookupView(), { newRequests: requests.slice(before) }));
      } else if (step.lookup_kind !== undefined) {
        const before = requests.length;
        ids["lookup-kind"].value = step.lookup_kind; ids["lookup-kind"].fire("change"); await settle();
        out.steps.push(Object.assign(lookupView(), { newRequests: requests.slice(before) }));
      } else if (step.lookup_click !== undefined) {
        const before = requests.length;
        ids[step.lookup_click].fire("click"); await settle();
        out.steps.push(Object.assign(lookupView(), { newRequests: requests.slice(before) }));
"""

_ANCHOR = "      } else if (step.preview !== undefined) {\n"
_VIEW_ANCHOR = "vm.createContext(ctx);\n"


def _harness() -> str:
    text = char_tab._harness()
    assert text.count(_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_ANCHOR, _STEPS.lstrip("\n") + _ANCHOR, 1)


def _drive(
    s: Served, folder: Path, steps: list[dict[str, Any]], held: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    scenario = {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": {"commands": _commands(s.server), "home": "", "sep": os.sep},
        "first_flavor": FLAVOR,
        "trees": {},
        "explains": {},
        "runs": [],
        "held": held or [],
        "steps": steps,
        "lookup_server": f"http://127.0.0.1:{s.server.port}",
        "lookup_token": s.server.token,
        "outcomes": {
            str(code): ui._outcome(ui._Run(code, [], [], False, False), None) for code in range(4)
        },
    }
    harness = folder / "m22-08-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m22-08-scenario.json"
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
    return out


def _bodies(step: dict[str, Any]) -> list[dict[str, Any]]:
    return [r["body"] for r in step["newRequests"]]


def _inert(view: dict[str, Any]) -> None:
    """The tab drew sections, headings, lists and items, with fixed classes,
    and no id, title or attribute made from what it shows."""
    for e in view["drawn"]:
        assert e["tag"] in {"section", "h3", "ul", "li", "option", "#text"}, e
        assert e["cls"] in {"", "panel lookup-hit", "crew-lines"}, e
        assert e["id"] == "" and e["title"] == "" and e["attrs"] == [], e


# ─── the server offers the command ───────────────────────────────────────────


def test_the_server_offers_lookup_as_a_leaf_that_runs_directly(served: Any) -> None:
    s = served(slice_toc())
    offered = {tuple(c["path"]): c for c in _commands(s.server)}
    leaf = offered[("lookup",)]
    assert leaf["excluded"] is None and leaf.get("confirm") is None
    assert {p["name"] for p in leaf["params"]} >= {"query", "kind", "limit", "fetch", "flavor"}


def test_the_shell_has_the_tab_in_the_characters_section() -> None:
    html = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert 'aria-controls="account-view character-view crew-view lookup-view"' in html
    assert '<button id="tab-lookup" class="tab" type="button" role="tab"' in html
    assert '<section id="lookup-view" role="tabpanel" aria-labelledby="tab-lookup" hidden>' in html


# ─── the page ────────────────────────────────────────────────────────────────


def test_typing_searches_and_draws_the_commands_lines_as_text_constructed(
    served: Any, tmp_path: Path
) -> None:
    s = served(slice_toc())
    out = _drive(
        s,
        tmp_path / "node",
        [{"lookup_tab": True}, {"lookup_type": "sharptalon"}, {"lookup_kind": "quest"}],
    )
    opened, typed, quests = out["steps"]
    assert opened["hidden"] is False and opened["tabSelected"] == "true"
    assert opened["shownViews"] == ["lookup-view"] and opened["active"] == "lookup-query"
    assert opened["kinds"] == [
        ["", "Everything"],
        ["item", "Items"],
        ["npc", "NPCs"],
        ["quest", "Quests"],
        ["object", "Objects"],
    ]
    assert _bodies(opened) == []
    # One run, after the pause, with the text as the argument.
    assert _bodies(typed) == [
        {"command": ["lookup"], "params": {"query": "sharptalon", "limit": "10"}}
    ]
    report = json.loads(s.reply({"query": "sharptalon", "limit": "10"})["stdout"])
    assert typed["status"] == report["words"] == '3 matches for "sharptalon".'
    assert typed["statusRole"] == "status"
    assert [h["heading"] for h in typed["hits"]] == [h["heading"] for h in report["hits"]]
    assert [h["lines"] for h in typed["hits"]] == [h["lines"] for h in report["hits"]]
    assert typed["hits"][0]["heading"] == "NPC: Sharptalon (12676)"
    assert typed["offerHidden"] is True and typed["caveatHidden"] is True
    # The About list is the command's own notes, the TOC read first.
    assert typed["leftOut"] == report["notes"]
    assert typed["leftOut"][0] == "Read: QuestieDB 1.1.4 (QuestieDB_Forever.toc)."
    assert typed["caveat"] == "" and report["caveat"] is None
    _inert(typed)
    assert _bodies(quests) == [
        {"command": ["lookup"], "params": {"query": "sharptalon", "limit": "10", "kind": "quest"}}
    ]
    assert [h["heading"] for h in quests["hits"]] == ["Quest: Sharptalon's Claw (2)"]


def test_without_the_tables_the_offer_downloads_with_fetch_constructed(
    served: Any, tmp_path: Path
) -> None:
    s = served(slice_toc(), tables=())
    plain = {"query": "claw", "limit": "10"}
    after = json.loads(s.reply(plain)["stdout"])
    for table in after["game_tables"]:
        table["state"] = "fetched"
    reply = s.reply(plain) | {"stdout": json.dumps(after)}
    held = [{"command": ["lookup"], "params": plain | {"fetch": True}, "reply": reply}]
    out = _drive(
        s,
        tmp_path / "node",
        [{"lookup_tab": True}, {"lookup_type": "claw"}, {"lookup_click": "lookup-fetch"}],
        held,
    )
    _, typed, fetched = out["steps"]
    assert typed["offerHidden"] is False and typed["fetchDisabled"] is False
    assert "zone 331" in "\n".join(typed["hits"][0]["lines"] + typed["hits"][1]["lines"])
    assert _bodies(fetched) == [{"command": ["lookup"], "params": plain | {"fetch": True}}]
    assert fetched["offerHidden"] is True and fetched["fetchDisabled"] is False
    # The page never ran `fetch` against the real server.
    assert not any("--fetch" in argv for argv in s.argvs())


def test_no_questiedb_says_so_and_still_finds_the_item_table_constructed(
    served: Any, tmp_path: Path
) -> None:
    s = served(None)
    out = _drive(s, tmp_path / "node", [{"lookup_tab": True}, {"lookup_type": "linen"}])
    typed = out["steps"][1]
    assert typed["caveat"] == (
        "QuestieDB is not installed for this game version, so only the game's item table was "
        "searched."
    )
    assert [h["heading"] for h in typed["hits"]] == ["Item: Linen Cloth (2589), item level 5"]
    report = json.loads(s.reply({"query": "linen", "limit": "10"})["stdout"])
    assert typed["caveat"] == report["caveat"]
    assert typed["hits"][0]["lines"] == [
        "QuestieDB is not read, so where it comes from is unknown."
    ]


def test_clearing_the_box_clears_the_hits_and_runs_nothing_constructed(
    served: Any, tmp_path: Path
) -> None:
    s = served(slice_toc())
    out = _drive(
        s,
        tmp_path / "node",
        [{"lookup_tab": True}, {"lookup_type": "claw"}, {"lookup_type": "   "}],
    )
    cleared = out["steps"][2]
    assert _bodies(cleared) == [] and cleared["hits"] == [] and cleared["status"] == ""


def test_hostile_names_are_shown_as_text_constructed(served: Any, tmp_path: Path) -> None:
    hostile = (
        'Kobold<img src=x onerror="alert(1)"><script>alert(2)</script>javascript:alert(3)'
        "\x1b[31m" + chr(0x9B) + "2J" + chr(0x202E) + "Boss"
    )
    entities = {k: dict(v) for k, v in SLICE.items()}
    entities["Npc"] = {**SLICE["Npc"], 4242: {1: hostile, 4: 10, 5: 10, 7: {12: [[1.0, 2.0]]}}}
    s = served(toc_text(entities))
    out = _drive(s, tmp_path / "node", [{"lookup_tab": True}, {"lookup_type": "kobold"}])
    typed = out["steps"][1]
    (hit,) = typed["hits"]
    shown = hit["heading"]
    assert "<script>alert(2)</script>" in shown  # as text, never as markup
    assert "\x1b" not in shown and chr(0x9B) not in shown and chr(0x202E) not in shown
    assert "⟨U+202E⟩" in shown and "⟨U+009B⟩" in shown and "⟨U+001B⟩" in shown
    _inert(typed)


def test_the_tab_builds_nothing_from_the_data() -> None:
    code = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    start = code.index("// ─── look up (M22-08) ───")
    end = code.index("// ─── the Characters home", start)
    block = code[start:end]
    assert "innerHTML" not in block and "insertAdjacentHTML" not in block
    assert ".id =" not in block and "dataset" not in block and ".title =" not in block
    assert "setAttribute" not in block and "href" not in block and "src" not in block
    assert "--yes" not in block
    classes = [
        line.split("=", 1)[1].strip() for line in block.splitlines() if "className =" in line
    ]
    assert classes == ['"panel lookup-hit";', '"crew-lines";']


def test_typing_runs_one_search_at_a_time_and_the_last_text_wins_constructed(
    served: Any, tmp_path: Path
) -> None:
    s = served(slice_toc())
    out = _drive(
        s,
        tmp_path / "node",
        [{"lookup_tab": True}, {"lookup_burst": ["sha", "shar", "sharp", "sharptalon"]}],
    )
    burst = out["steps"][1]
    # The first pause runs; the ones while it runs wait, and only the last
    # text runs after it.
    assert burst["maxInflight"] == 1
    assert [b["params"]["query"] for b in _bodies(burst)] == ["sha", "sharptalon"]
    assert burst["status"] == '3 matches for "sharptalon".'


def test_an_error_reply_shows_the_error_and_no_stale_caveat_constructed(
    served: Any, tmp_path: Path
) -> None:
    s = served(None)
    params = {"query": "boom", "limit": "10"}
    failed = s.reply(params) | {"exit_code": 1, "stdout": "", "stderr": "wowlab: it broke"}
    out = _drive(
        s,
        tmp_path / "node",
        [{"lookup_tab": True}, {"lookup_type": "linen"}, {"lookup_type": "boom"}],
        [{"command": ["lookup"], "params": params, "reply": failed}],
    )
    shown, broke = out["steps"][1], out["steps"][2]
    assert shown["caveatHidden"] is False
    assert broke["status"] == "wowlab: it broke" and broke["statusRole"] == "alert"
    assert broke["caveatHidden"] is True and broke["hits"] == []
