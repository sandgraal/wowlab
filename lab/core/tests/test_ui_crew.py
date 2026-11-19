"""The Crew tab of `wowlab ui` (M19-06, docs/LAB_PLAN.md §20.4, ADR-0029).

The tab lists the crew book from `crew list --json`, with its search, its
rating filter and its order as that command's own options; a row opens what
`crew show --json` prints; rating, note and forget go through the server's
confirm step with their `--expect` pins. CONSTRUCTED INPUT: ADR-0036 term 3
keeps every real crew record out of the repository, so every book here is
built at run time by `parser/_crew_build.py` inside a copy of the captured
install `fixtures/macos-70888/` in `tmp_path`; every key is one of that
file's invented keys, or assembled from one at run time (a hostile name, a
name starting with `-`). Notes, guilds and instance names are invented.

The page is driven under Node with the stand-in DOM of `test_ui_explorer.py`
and the steps of `test_ui_character.py` (HTML sinks throw; navigation,
attributes outside a closed set and elements such as `a`, `img` and
`script` are trapped). Every crew run the page asks for goes through the
real server to the real CLI, through the shim of `test_ui_confirm_writes.py`:
the user data directory is redirected into `tmp_path`, the process table is
empty (no client running) and each argv is logged. Nothing reads or writes
a real install.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_ui_character as char_tab
from test_ui_confirm import _commands, _confirm, _plan, _post, _token
from test_ui_confirm_writes import _SHIM, _serving
from test_ui_explorer import _node, _page_ids

from wowlab_core import cli, crewbook, guard, ui

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _crew_build import (
    ACCOUNT,
    FLAVOR,
    GUILD,
    KEY_A,
    KEY_B,
    KEY_C,
    NOTE,
    WHERE,
    account_file,
    book,
    install,
    player,
)

LIST = ("crew", "list")
SHOW = ("crew", "show")
FORGET = ("crew", "forget")
RATE = ("crew", "rate")
NOTE_CMD = ("crew", "note")
WRITES = (FORGET, RATE, NOTE_CMD)
LIVE = f"WTF/Account/{ACCOUNT}/SavedVariables/WowLab.lua"
_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"

# The characters `visible()` shows as a code (Cc, Cf, Zl, Zp), as Python
# reads them: none may reach the page raw.
_RAW = re.compile(
    "[\\x00-\\x09\\x0b-\\x1f\\x7f-\\x9f"
    + "".join(
        f"{chr(a)}-{chr(b)}"
        for a, b in (
            (0x061C, 0x061C),
            (0x200B, 0x200F),
            (0x2028, 0x202E),
            (0x2060, 0x206F),
            (0xFEFF, 0xFEFF),
        )
    )
    + "]"
)


def _hostile(base: str) -> str:
    """`base` with markup, a script tag, an event attribute, a `javascript:`
    link, an ESC sequence, the C1 CSI U+009B and U+202E, assembled at run
    time (constructed)."""
    name, realm = base.split("-")
    return (
        name
        + '<img src=x onerror="alert(1)">'
        + "<script>alert(2)</script>"
        + "javascript:alert(3)"
        + "\x1b[31m"
        + chr(0x9B)
        + "2J"
        + chr(0x202E)
        + "-"
        + realm
    )


# What `visible()` shows as a code, for the characters these tests use: C0,
# C1 and the format and separator characters of Unicode (Cc, Cf, Zl, Zp).
_UNSAFE = re.compile(
    "[\\x00-\\x1f\\x7f-\\x9f"
    + "".join(
        f"{chr(a)}-{chr(b)}"
        for a, b in (
            (0x00AD, 0x00AD),
            (0x0600, 0x0605),
            (0x061C, 0x061C),
            (0x06DD, 0x06DD),
            (0x070F, 0x070F),
            (0x180E, 0x180E),
            (0x200B, 0x200F),
            (0x2028, 0x202E),
            (0x2060, 0x2064),
            (0x2066, 0x206F),
            (0xFEFF, 0xFEFF),
            (0xFFF9, 0xFFFB),
        )
    )
    + "]"
)


def _visible(text: str) -> str:
    """What the page's `visible()` makes of `text`."""
    return re.sub(
        _UNSAFE,
        lambda m: "⟨U+" + format(ord(m.group(0)), "04X") + "⟩",
        text,
    )


# ─── the server over the real CLI, on a constructed crew book ────────────────


class Crew:
    """The server over the real CLI, the constructed install, the user data
    directory and the argv log the shim keeps."""

    def __init__(self, server: ui.UiServer, root: Path, data: Path, log: Path) -> None:
        self.server = server
        self.root = root
        self.data = data
        self.log = log

    @property
    def live(self) -> Path:
        return self.root / FLAVOR / LIVE

    def history(self) -> tuple[guard.HistoryRecord, ...]:
        store = self.data / "store"
        return guard.history(store=store) if store.exists() else ()

    def argvs(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def report(self, path: tuple[str, ...], params: dict[str, object]) -> dict[str, Any]:
        status, payload = _post(self.server, {"command": list(path), "params": params})
        assert status == 200, payload
        assert payload["exit_code"] == 0, payload["stderr"]
        value: dict[str, Any] = json.loads(payload["stdout"])
        return value


@pytest.fixture
def crew_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A server over the real CLI on an install whose account `WowLab.lua`
    holds the crew book given (constructed); None leaves the captured file,
    which holds no crew book."""
    from wowlab_core import install as discovery

    monkeypatch.delenv(discovery.ENV_FLAVOR, raising=False)
    made: list[Any] = []

    def build(crew: bytes | None, accounts: tuple[str, ...] = (ACCOUNT,)) -> Crew:
        folder = tmp_path / f"m19-06-{len(made)}"
        folder.mkdir()
        root = install(folder, crew, accounts=accounts)
        monkeypatch.setenv(discovery.ENV_ROOT, str(root))
        data = folder / "userdata"
        log = folder / "argv.jsonl"
        context = _serving(wowlab_argv=[sys.executable, "-c", _SHIM, str(data), str(log)])
        server = context.__enter__()
        made.append(context)
        return Crew(server, root, data, log)

    yield build
    for context in made:
        context.__exit__(None, None, None)


def _three(**a: Any) -> bytes:
    """A book of three players (constructed); `a` changes KEY_A's entry."""
    return book(
        {
            KEY_A: player(**a),
            KEY_B: player(rating="good", met=7, last=1_795_000_000),
            KEY_C: player(),
        },
        current=[KEY_A],
    )


# ─── the server: the crew commands, both ways ────────────────────────────────


def test_the_reads_run_directly_and_the_writes_only_through_the_step(crew_at: Any) -> None:
    crew = crew_at(_three())
    listed = {tuple(c["path"]): c for c in _commands(crew.server)}
    for path in (LIST, SHOW):
        assert listed[path]["confirm"] is None and listed[path]["excluded"] is None, path
    for path in WRITES:
        assert listed[path]["confirm"] == "declined" and listed[path]["excluded"] is None, path
        names = {p["name"] for p in listed[path]["params"]}
        assert not names & {"yes", "expect", "dry_run"}, (path, names)
        for params in ({"yes": True}, {"expect": "a" * 64}):
            status, payload = _post(crew.server, {"command": list(path), "params": params})
            assert (status, payload) == (403, {"error": "unknown_parameter"}), (path, params)
    # A read leaf runs as it is asked, with no plan and no token.
    status, payload = _post(crew.server, {"command": list(LIST), "params": {}})
    assert status == 200 and payload["exit_code"] == 0 and "confirm" not in payload
    assert json.loads(payload["stdout"])["total"] == 3
    status, payload = _post(crew.server, {"command": list(LIST), "params": {"yes": True}})
    assert (status, payload) == (403, {"error": "unknown_parameter"})
    # A read leaf given a token is refused and runs nothing.
    logged = len(crew.argvs())
    status, payload = _confirm(crew.server, LIST, "not-a-token", {})
    assert status == 403 and payload == {"error": "confirm"}
    assert len(crew.argvs()) == logged


@pytest.mark.parametrize(
    ("path", "extra", "fields"),
    [
        (FORGET, {}, cli.EXPECT_FIELDS[FORGET]),
        (RATE, {"rating": "bad"}, cli.EXPECT_FIELDS[RATE]),
        (NOTE_CMD, {"text": "invented: steady tank"}, cli.EXPECT_FIELDS[NOTE_CMD]),
    ],
    ids=" ".join,
)
def test_each_write_plans_then_confirms_with_its_pin_constructed(
    crew_at: Any, path: tuple[str, ...], extra: dict[str, object], fields: tuple[str, ...]
) -> None:
    crew = crew_at(_three())
    before = crew.live.read_bytes()
    params: dict[str, object] = {"name": KEY_A, **extra}
    plan = _plan(crew.server, path, params)
    assert plan["exit_code"] == 1, plan["stderr"]
    token = _token(plan)
    assert token is not None, plan["stderr"]
    shown = json.loads(plan["stdout"])
    pin = ":".join(shown[f] for f in fields)
    assert len(pin.split(":")) == len(fields)
    note = plan["confirm"]["note"]
    assert f"--expect {pin}:" in note and ui._PIN_CHECKS[fields] in note
    assert "crew book's file" in note
    if path == FORGET:
        assert "backup" in note
    assert crew.live.read_bytes() == before  # the plan changed nothing
    plan_argv = crew.argvs()[-1]
    assert plan_argv[-len(extra) - 2 :] == ["--", KEY_A, *extra.values()]
    status, done = _confirm(crew.server, path, token, params)
    assert status == 200 and done["exit_code"] == 0, done["stderr"]
    confirmed = crew.argvs()[-1]
    assert confirmed.count("--yes") == 1 and f"--expect={pin}" in confirmed
    assert confirmed.index(f"--expect={pin}") < confirmed.index("--")
    report = json.loads(done["stdout"])
    assert report["written"] is True
    assert (
        done["confirm"]["note"]
        == f"Journaled as {report['transaction']}. `wowlab undo` reverses it."
    )
    assert crew.history()[-1].label == f"crew {path[1]} (1 player)"
    assert hashlib.sha256(crew.live.read_bytes()).hexdigest() == shown["written_sha256"]


def test_the_pin_check_sentences_say_what_the_cli_compares() -> None:
    assert set(cli.EXPECT_FIELDS[RATE]) == set(cli.EXPECT_FIELDS[NOTE_CMD])
    for path in WRITES:
        assert cli.EXPECT_FIELDS[path] in ui._PIN_CHECKS, path
    forget = ui._PIN_CHECKS[cli.EXPECT_FIELDS[FORGET]]
    assert "the game's backup" in forget and "refused (exit 3)" in forget
    one = ui._PIN_CHECKS[cli.EXPECT_FIELDS[RATE]]
    assert "backup" not in one and "refused (exit 3)" in one


def test_a_file_changed_between_plan_and_confirm_is_refused_by_the_pin_constructed(
    crew_at: Any,
) -> None:
    crew = crew_at(_three())
    params: dict[str, object] = {"name": KEY_A, "text": "invented: kind"}
    token = _token(_plan(crew.server, NOTE_CMD, params))
    assert token is not None
    # Another player's note changes after the plan (constructed edit): the
    # entry the plan named is the same, the file is not.
    changed = crew.live.read_bytes().replace(GUILD.encode(), b"Invented Other Guild", 1)
    crew.live.write_bytes(changed)
    records = crew.history()
    status, done = _confirm(crew.server, NOTE_CMD, token, params)
    assert status == 200 and done["exit_code"] == 3, done["stderr"]
    assert "changed after the plan was made" in done["stderr"]
    assert done["confirm"]["note"] is None and done["confirm"]["token"] is None
    assert crew.live.read_bytes() == changed  # nothing written
    assert crew.history() == records  # nothing journaled


# ─── no new endpoint ─────────────────────────────────────────────────────────

_ROUTES = {"/", "/favicon.svg", "/api/commands", "/api/run", "/api/looks-page"}


def test_no_new_endpoint(crew_at: Any) -> None:
    """The server answers the same five paths it did before the Crew tab,
    and the page asks only for the three it reads."""
    source = (Path(ui.__file__)).read_text(encoding="utf-8")
    paths = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(r"/[a-z/._-]*", node.value)
    }
    assert paths == _ROUTES
    assert set(ui.static_files()) == {"/"}
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    assert set(re.findall(r'api\("([^"]*)"\)', script)) == {
        "/api/run",
        "/api/commands",
        "/api/looks-page",
    }
    assert len(re.findall(r"\bfetch\(", script)) == len(re.findall(r'\bfetch\(api\("', script))
    crew = crew_at(_three())
    for path in ("/api/crew", "/api/crew/list", "/api/run/crew", "/crew"):
        for method in ("GET", "POST"):
            status, payload = _request(crew.server, method, path)
            assert status == 404 and payload == {"error": "not_found"}, (method, path)


def _request(server: ui.UiServer, method: str, path: str) -> tuple[int, Any]:
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=60)
    try:
        body = b"{}" if method == "POST" else None
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, f"{path}?token={server.token}", body=body, headers=headers)
        reply = conn.getresponse()
        return reply.status, json.loads(reply.read())
    finally:
        conn.close()


def test_the_templates_never_name_the_book_and_build_nothing_from_its_data() -> None:
    script = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    html = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    for text in (script, html):
        assert crewbook.CREW_VARIABLE.lower() not in text.lower()
    body = script[
        script.index("// ─── the crew book (M19-06") : script.index("// ─── the Characters home")
    ]
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))
    # No markup, no link, no attribute, no class or id from crew data.
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "setAttribute", ".href", ".src"):
        assert sink not in code, sink
    classes = re.findall(r"className\s*=\s*([^;]+);", code)
    assert classes and all(re.fullmatch(r'"[a-z -]*"', c) for c in classes), classes
    assert all(
        re.fullmatch(r'"[a-z-]+"(, .*)?', c) for c in re.findall(r"classList\.\w+\(([^)]*)\)", code)
    )
    assert ".id =" not in code and "dataset" not in code and ".title = " in code
    assert re.findall(r"\.title = ([^;]+);", code) == [
        '"Show everything the crew book keeps about this player"'
    ]
    # Every write is the confirm step's: no `--yes`, no pin of the page's own.
    assert "--yes" not in code and "expect" not in code


# ─── the page, under Node, over the real server ──────────────────────────────

_VIEW = r"""
const realFetch = fetch;
let inflight = 0;
// A crew run the page asks for goes to the real server; anything else is the
// harness's own stand-in.
const standIn = ctx.fetch;
ctx.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  if (!(body && String(url).startsWith("/api/run") && Array.isArray(body.command) && body.command[0] === "crew")) {
    return standIn(url, opts);
  }
  requests.push({ url: String(url), body: body });
  inflight += 1;
  try {
    const headers = Object.assign({}, opts.headers || {}, { "X-Wowlab-Session": scenario.crew_token });
    const reply = await realFetch(scenario.crew_server + String(url), { method: "POST", headers: headers, body: opts.body });
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
function crewEls() {
  const out = [];
  for (const [id, e] of Object.entries(ids)) if (id === "crew-view" || id.startsWith("crew-") || id === "tab-crew") out.push(e, ...e.all());
  return out;
}
function crewView() {
  const table = ids["crew-table"];
  const rows = table.all().filter((e) => e.tagName === "tr" && has(e, "hub-row"));
  const t = (id) => ids[id].textContent;
  const active = documentStub.activeElement;
  return {
    hidden: ids["crew-view"].hidden,
    tabSelected: ids["tab-crew"].getAttribute("aria-selected"),
    shownViews: Object.keys(ids).filter((id) => id.endsWith("-view") && !ids[id].hidden),
    words: t("crew-words"), offer: t("crew-offer"), offerHidden: ids["crew-offer"].hidden,
    toolsHidden: ids["crew-tools"].hidden, status: t("crew-status"), statusRole: ids["crew-status"].getAttribute("role"),
    tableHidden: table.hidden,
    heads: table.all().filter((e) => e.tagName === "th").map((e) => e.textContent),
    rows: rows.map((tr) => tr.children.map((c) => c.textContent)),
    unread: ids["crew-unread"].children.map((c) => c.textContent),
    player: { hidden: ids["crew-player"].hidden, title: t("crew-player-title"),
      lines: ids["crew-player-lines"].children.map((c) => c.textContent), actionsHidden: ids["crew-actions"].hidden,
      rate: ids["crew-rate-value"].value, note: ids["crew-note-text"].value,
      codes: !ids["crew-note-codes"].hidden },
    act: { status: t("crew-act-status"), role: ids["crew-act-status"].getAttribute("role"),
      said: t("crew-act-said"), saidHidden: ids["crew-act-said"].hidden },
    confirm: { boxHidden: ids["crew-confirm-box"].hidden, planHidden: ids["crew-plan-box"].hidden,
      plan: t("crew-plan-lines"), note: t("crew-confirm-note"), buttonHidden: ids["crew-confirm"].hidden,
      button: t("crew-confirm"), disabled: ids["crew-confirm"].disabled },
    about: { keeps: t("crew-keeps"), notes: ids["crew-notes"].children.map((c) => c.textContent) },
    active: active ? active.id : null,
    // Every element the tab drew: its tag, class, id, title and attributes.
    drawn: crewEls().filter((e) => !(e.id in ids)).map((e) => ({ tag: e.tagName, cls: e.className, id: e.id,
      title: e.title, attrs: Object.keys(e.attrs), href: e.href === undefined ? null : String(e.href),
      src: e.src === undefined ? null : String(e.src) })),
    text: crewEls().map((e) => e._text + "\n" + (e.title || "") + "\n" + (typeof e.value === "string" ? e.value : "")).join("\n"),
  };
}
"""

_STEPS = r"""
      } else if (step.crew_tab !== undefined) {
        const before = requests.length;
        ids["tab-crew"].fire("click"); await settle();
        out.steps.push(Object.assign(crewView(), { newRequests: requests.slice(before) }));
      } else if (step.crew_open !== undefined) {
        const before = requests.length;
        const opens = ids["crew-table"].all().filter((e) => e.tagName === "button" && has(e, "hub-open"));
        if (!opens[step.crew_open]) { out.steps.push({ missing: "open " + step.crew_open }); continue; }
        opens[step.crew_open].fire("click"); await settle();
        out.steps.push(Object.assign(crewView(), { newRequests: requests.slice(before) }));
      } else if (step.crew_set !== undefined) {
        const before = requests.length;
        const el = ids[step.crew_set];
        el.value = step.value; el.fire(step.event || "change"); await settle();
        out.steps.push(Object.assign(crewView(), { newRequests: requests.slice(before) }));
      } else if (step.crew_click !== undefined) {
        const before = requests.length;
        ids[step.crew_click].fire("click"); await settle();
        out.steps.push(Object.assign(crewView(), { newRequests: requests.slice(before) }));
      } else if (step.crew_write !== undefined) {
        fs.writeFileSync(step.crew_write, Buffer.from(step.b64, "base64"));
        out.steps.push({ written: step.crew_write });
"""

_ANCHOR = "      } else if (step.preview !== undefined) {\n"
_VIEW_ANCHOR = "vm.createContext(ctx);\n"


def _harness() -> str:
    text = char_tab._harness()
    assert text.count(_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_ANCHOR, _STEPS.lstrip("\n") + _ANCHOR, 1)


def _drive(crew: Crew, folder: Path, steps: list[dict[str, Any]]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    scenario = {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": {"commands": _commands(crew.server), "home": "", "sep": os.sep},
        "first_flavor": FLAVOR,
        "trees": {},
        "explains": {},
        "runs": [],
        "held": [],
        "steps": steps,
        "crew_server": f"http://127.0.0.1:{crew.server.port}",
        "crew_token": crew.server.token,
        "outcomes": {
            str(code): ui._outcome(ui._Run(code, [], [], False, False), None) for code in range(4)
        },
    }
    harness = folder / "m19-06-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m19-06-scenario.json"
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
    out["crew"] = [
        r["body"] for r in out["requests"] if r["body"] and r["body"]["command"][0] == "crew"
    ]
    return out


def _bodies(step: dict[str, Any]) -> list[dict[str, Any]]:
    return [r["body"] for r in step["newRequests"]]


_DRAWN_CLASSES = {"", "char-table hub crew", "unseen", "hub-row", "hub-open"}


def _inert(view: dict[str, Any], *pieces: str) -> None:
    """Nothing the tab drew carries crew data outside text: no class, id,
    title, link or source made from it, and no attribute but the closed set
    the stand-in allows."""
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
        assert e["title"] in ("", "Show everything the crew book keeps about this player"), e
        for piece in pieces:
            assert piece not in e["cls"] and piece not in e["title"], e
    assert not _RAW.search(view["text"])


def test_the_tab_with_the_book_never_turned_on_says_so_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    crew = crew_at(None)
    words = crew.report(LIST, {})["words"]
    assert "not turned on" in words and "Turn it on in game with /wowlab crew on." in words
    out = _drive(crew, tmp_path / "node", [{"crew_tab": True}])
    (view,) = out["steps"]
    assert view["hidden"] is False and view["tabSelected"] == "true"
    assert view["shownViews"] == ["crew-view"]
    assert view["words"] == words
    # What turning it on would keep, from the CLI at run time.
    assert view["offer"] == cli.CREW_BOOK_SENTENCE and view["offerHidden"] is False
    assert view["about"]["keeps"] == cli.CREW_BOOK_SENTENCE
    assert view["toolsHidden"] is True and view["tableHidden"] is True and view["rows"] == []
    assert view["player"]["hidden"] is True
    assert _bodies(view) == [{"command": ["crew", "list"], "params": {"sort": "last"}}]


def test_the_tab_with_the_book_turned_off_lists_what_it_keeps_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    crew = crew_at(_three().replace(b'["on"] = true', b'["on"] = false'))
    words = crew.report(LIST, {})["words"]
    assert "turned off" in words and "keeps 3 players" in words
    out = _drive(crew, tmp_path / "node", [{"crew_tab": True}])
    (view,) = out["steps"]
    assert view["words"] == words and view["offerHidden"] is True
    assert len(view["rows"]) == 3 and view["status"] == "3 players."


def test_the_list_searches_filters_and_orders_through_the_command_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    crew = crew_at(_three(note=NOTE))
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_set": "crew-sort", "value": "met"},
        {"crew_set": "crew-rated", "value": "good"},
        {"crew_set": "crew-rated", "value": ""},
        {"crew_set": "crew-search", "value": "kept the GROUP", "event": "input"},
    ]
    out = _drive(crew, tmp_path / "node", steps)
    tab, by_met, good, everyone, searched = out["steps"]
    assert tab["heads"] == [
        "Player",
        "Class",
        "Groups",
        "Last grouped",
        "Last instance",
        "Your rating",
        "Your note",
    ]
    # The CLI's order: newest group first.
    assert [r[0] for r in tab["rows"]] == [KEY_A, KEY_C, KEY_B]
    assert tab["rows"][0][1:] == ["Mage, level 60", "3", tab["rows"][0][3], WHERE, "none", NOTE]
    assert tab["status"] == "3 players." and tab["toolsHidden"] is False
    assert _bodies(by_met) == [{"command": ["crew", "list"], "params": {"sort": "met"}}]
    assert by_met["rows"][0][0] == KEY_B
    assert _bodies(good) == [
        {"command": ["crew", "list"], "params": {"sort": "met", "rated": "good"}}
    ]
    assert [r[0] for r in good["rows"]] == [KEY_B] and good["status"] == "Showing 1 of 3 players."
    assert len(everyone["rows"]) == 3
    # The search is the command's: case and spaces folded by Python.
    assert _bodies(searched) == [
        {"command": ["crew", "list"], "params": {"sort": "met", "search": "kept the GROUP"}}
    ]
    assert [r[0] for r in searched["rows"]] == [KEY_A]


def test_hostile_names_guilds_notes_and_instances_are_shown_as_text_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    key = _hostile(KEY_A)
    guild, note, where = _hostile(KEY_B), _hostile(KEY_C), _hostile(KEY_B)
    crew = crew_at(
        book({key: player(guild=guild, note=note, where=where, rating="bad"), KEY_B: player()})
    )
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_open": 0},
        {"crew_set": "crew-rate-value", "value": "good"},
        {"crew_click": "crew-rate"},
        {"crew_click": "crew-confirm"},
    ]
    out = _drive(crew, tmp_path / "node", steps)
    tab, opened, _, planned, confirmed = out["steps"]
    pieces = ("<img", "onerror", "<script>", "javascript:", "\x1b", chr(0x9B), chr(0x202E))
    for view in (tab, opened, planned, confirmed):
        _inert(view, *pieces)
    row = next(r for r in tab["rows"] if r[0] != KEY_B)
    assert row[0] == _visible(key)
    assert "⟨U+001B⟩[31m⟨U+009B⟩2J⟨U+202E⟩" in row[0]
    assert '<img src=x onerror="alert(1)">' in row[0] and "javascript:alert(3)" in row[0]
    assert row[4] == _visible(where) and row[6] == _visible(note)
    assert opened["player"]["title"] == _visible(key)
    lines = opened["player"]["lines"]
    assert f"Guild last seen (kept if they left it): {_visible(guild)}" in lines
    assert f"Your note: {_visible(note)}" in lines
    assert any(_visible(where) in line for line in lines)
    assert opened["player"]["rate"] == "bad" and opened["player"]["note"] == _visible(note)
    # The write is sent the stored key exactly, as an argument.
    plan_body = _bodies(planned)[0]
    assert plan_body["command"] == ["crew", "rate"]
    assert plan_body["params"]["name"] == key and plan_body["params"]["rating"] == "good"
    assert planned["confirm"]["buttonHidden"] is False
    # The plan is the CLI's text, its control characters already escaped.
    assert "<script>alert(2)</script>" in planned["confirm"]["plan"]
    confirm_body = _bodies(confirmed)[0]
    assert confirm_body["params"] == plan_body["params"] and confirm_body["confirm"]
    assert confirmed["act"]["status"] == "After Confirm: Done (exit code 0)."
    assert confirmed["player"]["rate"] == "good"
    entry = next(p for p in crew.report(LIST, {})["players"] if p["key"] == key)
    assert entry["rating"] == "good" and entry["note"] == note and entry["guild"] == guild


def test_a_name_starting_with_a_dash_is_an_argument_not_an_option_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    dashed = "-" + KEY_A  # assembled at run time
    crew = crew_at(book({dashed: player(), KEY_B: player(last=1_700_000_000)}))
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_open": 0},
        {"crew_click": "crew-forget"},
        {"crew_click": "crew-confirm"},
    ]
    out = _drive(crew, tmp_path / "node", steps)
    tab, opened, planned, confirmed = out["steps"]
    assert tab["rows"][0][0] == dashed
    assert opened["player"]["title"] == dashed and opened["player"]["actionsHidden"] is False
    show, plan = out["crew"][1:3]
    assert show == {
        "command": ["crew", "show"],
        "params": {"account": ACCOUNT, "flavor": FLAVOR, "name": dashed},
    }
    assert plan["command"] == ["crew", "forget"] and plan["params"]["name"] == dashed
    argvs = crew.argvs()
    show_argv = next(a for a in argvs if a[:2] == ["crew", "show"])
    assert show_argv[-2:] == ["--", dashed]
    plan_argv, confirm_argv = [a for a in argvs if a[:2] == ["crew", "forget"]]
    assert plan_argv[-2:] == ["--", dashed] and "--yes" not in plan_argv
    assert confirm_argv[-2:] == ["--", dashed] and "--yes" in confirm_argv
    pin = next(a for a in confirm_argv if a.startswith("--expect="))
    assert len(pin.split("=", 1)[1].split(":")) == 4  # forget's pin always has four parts
    assert (
        "--expect" in planned["confirm"]["note"]
        and "the game's backup" in planned["confirm"]["note"]
    )
    # Forgotten from the file. M20-01: the list is read from wowlab's crew
    # database, which keeps a player the saved file no longer holds
    # (docs/LAB_PLAN.md §21.3, sync rule 1); a forget reaches the database
    # with M20-02, which brings back "gone from the list and the panel" here.
    assert confirmed["act"]["status"] == "After Confirm: Done (exit code 0)."
    assert "wowlab cannot remove a snapshot yet" in confirmed["confirm"]["plan"]
    assert "Journaled as " in confirmed["confirm"]["note"]
    assert dashed.encode() not in crew.live.read_bytes()
    assert [r[0] for r in confirmed["rows"]] == [dashed, KEY_B]


def test_a_file_changed_between_plan_and_confirm_is_refused_on_the_page_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    crew = crew_at(_three())
    changed = crew.live.read_bytes().replace(GUILD.encode(), b"Invented Other Guild", 1)
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_open": 0},
        {"crew_set": "crew-note-text", "value": "invented: calm", "event": "input"},
        {"crew_click": "crew-note"},
        {"crew_write": str(crew.live), "b64": base64.b64encode(changed).decode("ascii")},
        {"crew_click": "crew-confirm"},
    ]
    records_before = crew.history()
    out = _drive(crew, tmp_path / "node", steps)
    planned, refused = out["steps"][3], out["steps"][5]
    assert planned["confirm"]["buttonHidden"] is False
    assert "if the crew book's file has changed by then" in planned["confirm"]["note"]
    assert refused["act"]["status"] == "After Confirm: Refused (exit code 3)."
    assert refused["act"]["role"] == "alert"
    assert "changed after the plan was made" in refused["act"]["said"]
    assert refused["confirm"]["buttonHidden"] is True
    assert crew.live.read_bytes() == changed  # nothing written
    assert crew.history() == records_before  # nothing journaled


def test_a_rating_already_so_offers_no_confirm_constructed(crew_at: Any, tmp_path: Path) -> None:
    crew = crew_at(_three(rating="good"))
    before = crew.live.read_bytes()
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_open": 0},
        {"crew_set": "crew-rate-value", "value": "good"},
        {"crew_click": "crew-rate"},
    ]
    out = _drive(crew, tmp_path / "node", steps)
    planned = out["steps"][3]
    assert planned["act"]["status"] == "Nothing would change: it is already so."
    assert planned["confirm"]["buttonHidden"] is True
    assert crew.live.read_bytes() == before


def test_the_book_of_an_account_file_is_built_at_run_time() -> None:
    """The constructed books are built here, never stored (term 3)."""
    data = account_file(_three())
    assert crewbook.CREW_VARIABLE.encode() in data
    assert crewbook.parse_book(data).state == "on"


def test_a_note_with_characters_that_cannot_be_typed_says_so_before_an_edit_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    odd = "invented " + "\x1b" + "x" + chr(0x202E) + "y"  # assembled at run time
    crew = crew_at(_three(note=odd))
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_open": 0},
        {"crew_open": 1},
        {"crew_open": 0},
        {"crew_set": "crew-note-text", "value": _visible(odd) + " more", "event": "input"},
        {"crew_click": "crew-note"},
    ]
    out = _drive(crew, tmp_path / "node", steps)
    _, odd_one, plain, again, _, planned = out["steps"]
    # The sentence is page.html's, shown beside the field before any edit.
    html = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert (
        '<p id="crew-note-codes" class="crew-note-codes" hidden>This note holds characters '
        "that cannot be typed, shown here as codes. Saving it replaces them with the codes "
        "shown.</p>"
    ) in html
    assert odd_one["player"]["codes"] is True
    assert odd_one["player"]["note"] == "invented ⟨U+001B⟩x⟨U+202E⟩y"
    # A player whose note holds none: no sentence.
    assert plain["player"]["codes"] is False
    assert again["player"]["codes"] is True
    # The edit is not refused: the plan writes the codes as text.
    assert planned["confirm"]["buttonHidden"] is False
    assert _bodies(planned)[0]["params"]["text"] == "invented ⟨U+001B⟩x⟨U+202E⟩y more"


def test_a_list_that_cannot_be_read_hides_the_table_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    second = ACCOUNT + "B"
    crew = crew_at(_three(), accounts=(ACCOUNT, second))
    out = _drive(crew, tmp_path / "node", [{"crew_tab": True}])
    (view,) = out["steps"]
    assert "2 crew books to choose from" in view["status"] and view["statusRole"] == "alert"
    assert view["tableHidden"] is True and view["rows"] == [] and view["toolsHidden"] is True
