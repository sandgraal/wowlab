"""The explorer view of `wowlab ui`, second stage: graders for M13-27
(M13-27T, §15.6, ADR-0029 and its rulings).

Written before the implementation, from the backlog (M13-27T, M13-27 and the
"Placeholder badge" bullet of 2026-10-16), §15.6, ADR-0029, and the JSON
that M13-22 (`tree --install`, `filemap gaps`), M13-23 (`explain`'s `stat`,
`snapshot`, `views`, `hints`) and M13-32 (`confirmed`, `last_held_*`,
`placeholder`) merged. Every grader here is `xfail(strict=True)` until
M13-27 deletes its marker line; nothing else in this file changes then.

How the page is graded: page.js runs under Node in the stand-in DOM of
`test_ui_explorer.py` (HTML sinks throw; `fetch` answers from what the real
CLI printed for the install under test, collected through the real server;
`navigator.clipboard.writeText` is recorded). The install is the captured
tree (`fixtures/macos/`) copied into `tmp_path`, as in the first stage.

What the page must give the graders to find (the seams; each is named in
page.html or set by the script, never inferred from layout):
- Tree rows: with the whole install drawn, a row's `data-path` is its path
  from the install root, `/`-separated: "" is the install root, a flavor
  folder is its own name, a file in it `<flavor>/<path>`, a root file its
  name.
- "gaps only": an `<input type="checkbox" id="gaps-only">` in page.html;
  the tree reacts to its `change` event.
- The gaps tab: a tab `id="tab-gaps"` and a view `id="gaps-view"` in
  page.html, hidden at first. Each group is an element with class
  `gap-group` (and `gap-catch-all` for a catch-all-only group) whose header
  is an element with class `gap-head`; each place in it an element with
  class `gap-place`; each not-entered folder `gap-not-entered`; each link
  `gap-link`. A place, a not-entered folder and a link carry `data-path`,
  their path from the install root as above. A group's places may be drawn
  inside a `<details>`; the harness opens every one.
- The detail pane is every element whose id starts with `explain` and what
  is drawn inside them. A hint is an element with class `hint` holding one
  `<button class="copy">`; the Preview button is `<button class="preview">`.
  The preview draws into the pane; `<details>` it draws are opened by the
  harness, as a player would.
- A placeholder verdict's badge has the class `badge-placeholder`, on top
  of `badge` and its level's class, as `badge-link` is for a link.

Conductor rulings of 2026-10-16 (round 1 on #226), graded here:
- `tree --install --explain --json` carries `ancestors` (M13-33), and every
  folder drawn takes its badge from there.
- "gaps only" shows the places in `groups` and `catch_all` and the `links`,
  with their ancestors; a folder not entered stays out.
- A copy is exactly `hint.command`; the page never reads `argv`.
- Snapshot facts are Python's words, `snapshot.words` (M13-27 adds it); the
  page shows them and words nothing itself.

The harness also traps what a browser would turn into a navigation or a
request (`location`, `window.open`, URL attributes, link and media
elements, any attribute outside a closed set); an `activate` step uses
every control in the detail pane, so a refused view has no way in at all.

Constructed inputs (L8: hostile input, boundary cases, or our own JSON
formats) carry `constructed` in the test id: unknown files added to the
captured tree to make gaps, a symlink, hostile names, and `explain` or
`tree` JSON edited to reach a case the capture cannot (a snapshot copy that
could not be confirmed, a refused view, a placeholder verdict, a PowerShell
hint).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import test_ui_explorer as first_stage
from test_ui_explorer import (
    FLAVOR,
    IMG,
    LSEP,
    RLO,
    SCRIPT_CLOSE,
    SV_FILE,
    _ancestors,
    _asked,
    _css,
    _drive,
    _norm,
    _page,
    _scenario,
    _send,
    _stdout,
)

from wowlab_core import filemap
from wowlab_core import verdict as verdict_module

# The first stage's fixtures: a stand-in home, the copied capture, its
# hostile twin, and the real server over the real CLI.
home = first_stage.home
install = first_stage.install
hostile = first_stage.hostile
server = first_stage.server

SECOND = (
    "_ptr_"  # a second flavor folder, a copy of the capture's (constructed); L6 lets tests name it
)
ACCOUNT = "WTF/Account/90000001#6"
NOTES = "m13-27t-notes.txt"
# Character folders of one kind (`<Character>-<Realm>` under a region
# folder) in the capture, so one unknown file in each is one gap group.
SAME_KIND_CHARACTERS = [
    ACCOUNT + "/1/Labcharb-Labrealmf",
    ACCOUNT + "/1/Labchard-Labrealmg",
]
# Every account, realm and character folder name in the capture's paths
# (scrubbed names; ADR-0012). None of them may appear in a group header.
IDENTIFYING = ["90000001", "Labchar", "Labrealm", "Partb Partc"]


# ─── helpers ─────────────────────────────────────────────────────────────────


def _rows(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["path"]: r for r in view["rows"]}


def _key(base: str, flavor: str | None, path: str) -> str:
    """A path from the install root, as a row's or a gap's `data-path`."""
    if base == "root":
        return path
    assert flavor, (base, path)
    return flavor + ("/" + path if path else "")


def _place_keys(groups: list[dict[str, Any]]) -> set[str]:
    return {_key(g["base"], p["flavor_folder"], p["path"]) for g in groups for p in g["gaps"]}


def _own_api_only(out: dict[str, Any]) -> None:
    """No external request: every request the page made went to its own
    server's API, by path."""
    assert out["requests"], "the page asked for nothing"
    for request in out["requests"]:
        url = request["url"]
        assert url.startswith(("/api/commands?", "/api/run?")), url


def _target(scenario: dict[str, Any], rel: str, flavor: str = FLAVOR) -> str:
    """The key of the explain the page asks for: the absolute path of `rel`
    in a flavor folder, `/`-separated."""
    root = json.loads(scenario["install_tree"])["install_root"]
    return _norm(root + os.sep + flavor + "/" + rel)


def _explain(scenario: dict[str, Any], rel: str) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(scenario["explains"][_target(scenario, rel)])
    return report


def _set_explain(scenario: dict[str, Any], rel: str, report: dict[str, Any]) -> None:
    scenario["explains"][_target(scenario, rel)] = json.dumps(report)


def _add_run(scenario: dict[str, Any], view: dict[str, Any], stdout: str) -> None:
    scenario["runs"].append(
        {"command": view["command"], "params": view["params"], "stdout": stdout}
    )


def _runs_of(out: dict[str, Any], command: list[str]) -> list[dict[str, Any]]:
    return [
        r["body"]
        for r in out["requests"]
        if r["body"] is not None and r["body"]["command"] == command
    ]


def _verdicts(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every verdict `tree --install --explain --json` carries, by path from
    the install root: its items and its `ancestors` (the folders above them
    that are not listed themselves; M13-33 adds them, conductor ruling of
    2026-10-16)."""
    whole = json.loads(scenario["install_tree"])
    out = {}
    for item in [*whole.get("ancestors", []), *whole["items"]]:
        out[_key(item["base"], item["flavor_folder"], item["path"])] = item["verdict"]
    return out


def _group_of(verdict: dict[str, Any]) -> str:
    return "link" if verdict.get("link") is True else str(verdict["level"])


def _only_copy_and_preview(pane: dict[str, Any]) -> None:
    """The pane's controls are its hints' copy buttons and the Preview
    button; nothing else in it listens for anything."""
    for classes in pane["buttons"]:
        assert {"copy", "preview"} & set(classes.split()), classes
    assert pane["listening"] == [], pane["listening"]


def _clean(pane: dict[str, Any]) -> None:
    assert "missing" not in pane, pane
    assert pane["status"] == "" or "error" not in pane["status"].lower(), pane["status"]


# ─── fixtures (constructed) ──────────────────────────────────────────────────


@pytest.fixture
def two_flavors(install: Path) -> Path:
    """The capture with a second flavor folder, a copy of the first."""
    shutil.copytree(install / FLAVOR, install / SECOND)
    return install


@pytest.fixture
def gapped(install: Path) -> Path:
    """The capture with paths no file-map row describes: a file at the
    install root, a folder in the flavor folder, and one unknown file in two
    character folders of the same kind (one group, two places)."""
    (install / "m13-27t-unknown.bin").write_bytes(b"x")
    stuff = install / FLAVOR / "m13-27t-stuff"
    stuff.mkdir()
    (stuff / "a.txt").write_bytes(b"a")
    (stuff / "b.txt").write_bytes(b"bb")
    for folder in SAME_KIND_CHARACTERS:
        (install / FLAVOR / folder / NOTES).write_bytes(b"n")
    return install


@pytest.fixture
def gapped_more(gapped: Path, tmp_path: Path) -> Path:
    """`gapped`, plus a file only a catch-all row describes (`Logs/`) and a
    symlink with no row."""
    (gapped / FLAVOR / "Logs" / "m13-27t-other.dat").write_bytes(b"o")
    target = tmp_path / "elsewhere_constructed"
    target.mkdir()
    try:
        (gapped / FLAVOR / "m13-27t-link").symlink_to(target, target_is_directory=True)
        # A link with a row (an addon folder): in `links`, and in no group.
        addon = gapped / FLAVOR / "Interface" / "AddOns" / "m13-27t-LinkedAddon"
        addon.symlink_to(target, target_is_directory=True)
    except OSError as error:  # Windows without the symlink privilege
        pytest.skip(f"cannot make a symlink here: {error}")
    return gapped


HOSTILE_ROOT = [IMG, "line" + LSEP + "sep", RLO + "aul.exe"]


@pytest.fixture
def hostile_gaps(install: Path) -> Path:
    if os.name == "nt":
        pytest.skip("Windows forbids < and > in file names")
    for name in HOSTILE_ROOT:
        (install / name).write_bytes(b"h")
    folder = install / FLAVOR / "<script>"
    folder.mkdir()
    (folder / (IMG + ".txt")).write_bytes(b"h")
    return install


# ─── the page shell ──────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_the_page_has_the_gaps_tab_and_the_gaps_only_filter(server: Any) -> None:
    """page.html names the new parts: the gaps tab and its view (hidden
    until picked, like the commands), and the "gaps only" checkbox."""
    body = _page(server).body.decode("utf-8")
    assert re.search(r'<button id="tab-gaps"[^>]*role="tab"', body), "the gaps tab"
    assert re.search(r'<section id="gaps-view"[^>]*\bhidden\b', body), "the gaps view, hidden"
    assert re.search(r'<input id="gaps-only"[^>]*type="checkbox"', body) or re.search(
        r'<input type="checkbox"[^>]*id="gaps-only"', body
    ), "the gaps-only checkbox"
    # Still one script and one style, both hashed by the CSP (first stage).
    assert body.count("<script") == 1 and body.count("<style") == 1


# ─── the tree: the whole install ─────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_the_tree_is_the_whole_install_constructed(
    server: Any, two_flavors: Path, home: Path, tmp_path: Path
) -> None:
    """The tree comes from `tree --install --explain --json`: the install
    root, its own entries, and every flavor folder, each item with its badge
    from Python. It opens on the first flavor folder with `WTF/` and
    `Interface/AddOns/` expanded (owner, 2026-10-16). Picking a root file or
    a file in the second flavor folder asks `explain` for that path."""
    steps: list[dict[str, Any]] = [
        {"open_all": True},
        {"click": ".build.info"},
        {"click": SECOND + "/WTF/Config.wtf"},
    ]
    scenario, _ = _scenario(server, [], steps)
    whole = json.loads(scenario["install_tree"])
    root = whole["install_root"]
    assert sorted(whole["flavor_folders"]) == sorted([FLAVOR, SECOND])
    for rel, flavor in ((".build.info", None), ("WTF/Config.wtf", SECOND)):
        path = root + os.sep + (flavor + "/" if flavor else "") + rel
        params = {"path": path, "root": root, **({"flavor": flavor} if flavor else {})}
        scenario["explains"][_norm(path)] = _stdout(server, ["explain"], params)
    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)

    asked = _asked(out, ["tree"])
    whole_asks = [p for p in asked if p.get("whole_install") is True]
    assert whole_asks, asked
    assert all(p.get("explain") is True for p in whole_asks), whole_asks
    assert all(set(p) <= {"whole_install", "explain", "root"} for p in whole_asks), whole_asks

    # The first view.
    first = _rows(out["first"])
    flavor_first = whole["flavor_folders"][0]
    flavor_other = next(f for f in whole["flavor_folders"] if f != flavor_first)
    assert first[""]["expanded"] == "true", "the install root is open"
    for folder in ("", "/WTF", "/Interface", "/Interface/AddOns"):
        assert first[flavor_first + folder]["expanded"] == "true", flavor_first + folder
    assert first[flavor_other]["expanded"] == "false", "only the first flavor folder opens"
    assert ".build.info" in first, "the install root's own entries are drawn"
    tree_root = out["first"]["treeRoot"]
    assert tree_root.startswith("~") and str(home) not in tree_root, tree_root
    assert tree_root.rstrip("/\\").endswith(Path(root).name), tree_root

    # Everything, opened: every item at its path from the install root, its
    # badge saying Python's label; every folder drawn above them (the
    # report's `ancestors`) too. The install root has no verdict.
    drawn = _rows(out["steps"][0])
    verdicts = _verdicts(scenario)
    assert whole.get("ancestors"), "M13-33: `tree --install` lists the folders above its items"
    for item in whole["items"]:
        assert _key(item["base"], item["flavor_folder"], item["path"]) in drawn, item["path"]
    for key, row in drawn.items():
        if key == "":
            continue
        assert key in verdicts, key
        badge = row["badge"]
        assert badge is not None, key
        assert badge["text"].startswith(verdicts[key]["label"]), (key, badge)
    for flavor in whole["flavor_folders"]:
        assert flavor + "/WTF/Config.wtf" in drawn, flavor
    for path in drawn:
        assert path == "" or path.split("/")[0] in {*whole["flavor_folders"]} | {
            i["path"] for i in whole["items"] if i["base"] == "root"
        }, path

    # The detail pane for a root file and for the second flavor folder's file.
    explains = _asked(out, ["explain"])
    assert len(explains) == 2, explains
    for params, (rel, flavor) in zip(
        explains, ((".build.info", None), ("WTF/Config.wtf", SECOND)), strict=True
    ):
        expected = root + os.sep + (flavor + "/" if flavor else "") + rel
        assert _norm(params["path"]) == _norm(expected), params
        assert params.get("root") == root, params
        if flavor:
            assert params.get("flavor", flavor) == flavor, params
    for pane, rel in zip(out["steps"][1:], (".build.info", "Config.wtf"), strict=True):
        _clean(pane)
        assert rel in pane["title"], pane["title"]
        assert pane["verdictBadge"], pane


# ─── "gaps only" ─────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_gaps_only_shows_exactly_the_gaps_and_their_ancestors_constructed(
    server: Any, gapped_more: Path, tmp_path: Path
) -> None:
    """With "gaps only" on, the tree shows exactly what `filemap gaps --json`
    reports as thin: the places in `groups` and in `catch_all`, and the
    `links`, each with the folders that lead to it. A folder not entered has
    a file-map row and stays out, as does every other file (conductor
    ruling, 2026-10-16). Off again, the first view returns."""
    steps: list[dict[str, Any]] = [{"gaps_only": True}, {"gaps_only": False}]
    scenario, _ = _scenario(server, [], steps)
    gaps = json.loads(scenario["gaps"])
    assert len(gaps["groups"]) >= 3 and gaps["catch_all"] and gaps["links"]
    assert gaps["not_entered"], "the capture has folders not entered"
    hits = _place_keys(gaps["groups"]) | _place_keys(gaps["catch_all"]) | set(gaps["links"])
    assert "m13-27t-unknown.bin" in hits and FLAVOR + "/m13-27t-stuff" in hits
    assert {FLAVOR + "/" + c + "/" + NOTES for c in SAME_KIND_CHARACTERS} <= hits
    assert FLAVOR + "/Logs" in hits and FLAVOR + "/m13-27t-link" in hits
    linked_addon = FLAVOR + "/Interface/AddOns/m13-27t-LinkedAddon"
    assert linked_addon in gaps["links"], gaps["links"]
    assert linked_addon not in _place_keys(gaps["groups"]) | _place_keys(gaps["catch_all"])
    entered = {_key(n["base"], n["flavor_folder"], n["path"]) for n in gaps["not_entered"]}
    assert not entered & hits
    # Every place is something the install tree draws.
    whole = json.loads(scenario["install_tree"])
    listed = {_key(i["base"], i["flavor_folder"], i["path"]) for i in whole["items"]}
    for hit in hits:
        assert hit in listed or any(p.startswith(hit + "/") for p in listed), hit

    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)
    gap_runs = _runs_of(out, ["filemap", "gaps"])
    assert gap_runs, "the page asks `filemap gaps`"
    for run in gap_runs:
        assert set(run["params"]) <= {"root"}, run["params"]

    filtered = out["steps"][0]
    assert "missing" not in filtered, filtered
    expected = hits.union(*(_ancestors(h) for h in hits))
    assert set(_rows(filtered)) == expected, (
        sorted(set(_rows(filtered)) - expected)[:8],
        sorted(expected - set(_rows(filtered)))[:8],
    )
    assert [r["path"] for r in out["steps"][1]["rows"]] == [r["path"] for r in out["first"]["rows"]]


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_the_verdict_and_text_filters_cover_the_whole_install_constructed(
    server: Any, two_flavors: Path, tmp_path: Path
) -> None:
    """The verdict filter and the text filter work across the whole tree:
    the install root's own entries and the second flavor folder too, not
    only the first flavor folder (code review, round 1 on #226). Each shows
    exactly its matches and the folders that lead to them."""
    steps: list[dict[str, Any]] = [{"level": "no"}, {"level": ""}, {"text": "config.wtf"}]
    scenario, _ = _scenario(server, [], steps)
    verdicts = _verdicts(scenario)
    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)

    def shows(view: dict[str, Any], hits: set[str]) -> None:
        assert hits, view["status"]
        expected = hits.union(*(_ancestors(h) for h in hits))
        assert set(_rows(view)) == expected, (
            sorted(set(_rows(view)) - expected)[:8],
            sorted(expected - set(_rows(view)))[:8],
        )

    no = {k for k, v in verdicts.items() if _group_of(v) == "no"}
    assert ".build.info" in no, "a `no` file at the install root"
    assert any(k.startswith(SECOND + "/") for k in no), "and in the second flavor folder"
    shows(out["steps"][0], no)
    assert [r["path"] for r in out["steps"][1]["rows"]] == [r["path"] for r in out["first"]["rows"]]
    found = {k for k in verdicts if "config.wtf" in k.lower()}
    assert {FLAVOR + "/WTF/Config.wtf", SECOND + "/WTF/Config.wtf"} <= found
    shows(out["steps"][2], found)


# ─── the gaps tab ────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_the_gaps_tab_draws_groups_catch_all_not_entered_and_links_constructed(
    server: Any, gapped_more: Path, tmp_path: Path
) -> None:
    """The gaps tab draws `filemap gaps --json`: every group and every
    catch-all-only group (marked as such) with its places, every folder not
    entered, and every link. A group's header names the place (its name,
    under its parent's row) and never an account, realm or character
    folder, the way `filemap gaps` prints it."""
    scenario, _ = _scenario(server, [], [{"tab": "gaps"}])
    gaps = json.loads(scenario["gaps"])
    assert gaps["groups"] and gaps["catch_all"] and gaps["not_entered"] and gaps["links"]
    shared = [g for g in gaps["groups"] if g["name"] == NOTES and g["places"] == 2]
    assert shared, "one group with a place in two character folders"
    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)
    view = out["steps"][0]
    assert "missing" not in view, view
    assert view["hidden"] is False and view["explorerHidden"] is True, view

    every = [*gaps["groups"], *gaps["catch_all"]]
    assert len(view["groups"]) == len(every), [g["heads"] for g in view["groups"]]
    assert sum(1 for g in view["groups"] if g["catchAll"]) == len(gaps["catch_all"])
    wanted = {frozenset(_place_keys([g])): g for g in every}
    seen: set[frozenset[str]] = set()
    for drawn in view["groups"]:
        places = frozenset(drawn["places"])
        assert places in wanted, sorted(places)
        assert places not in seen, sorted(places)
        seen.add(places)
        group = wanted[places]
        assert drawn["catchAll"] is (group in gaps["catch_all"]), group["name"]
        assert len(drawn["heads"]) == 1, drawn["heads"]
        head = drawn["heads"][0]
        assert group["name"] in head, (group["name"], head)
        # A <summary> is the header a player reads: it is, or holds, `.gap-head`.
        assert all(drawn["summaries"]), drawn
        if group["files"]:
            assert str(group["files"]) in drawn["text"], (group["name"], drawn["text"])
        # The group's own text, everything but its places, not-entered
        # folders and links (titles included), names no account, realm or
        # character folder (security review, round 1 on #226).
        bare = drawn["bare"]
        assert group["name"] in bare, bare
        for place in group["gaps"]:
            if place["parent"]:
                assert place["parent"] not in bare, bare
        for name in IDENTIFYING:
            assert name not in bare, (name, bare)
    assert set(view["places"]) == _place_keys(every)

    entered = {_key(n["base"], n["flavor_folder"], n["path"]) for n in gaps["not_entered"]}
    assert set(view["notEntered"]) == entered
    assert set(view["links"]) == set(gaps["links"])


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_hostile_names_in_gaps_draw_as_inert_text_constructed(
    server: Any, hostile_gaps: Path, tmp_path: Path
) -> None:
    """Gaps named with markup, U+2028 and U+202E: drawn as text (the
    stand-in DOM throws on an HTML sink), the two invisible characters shown
    as a code and never applied, in the headers and in the places."""
    scenario, _ = _scenario(server, [], [{"open_all": True}, {"tab": "gaps"}])
    gaps = json.loads(scenario["gaps"])
    names = {g["name"] for g in gaps["groups"]}
    assert set(HOSTILE_ROOT) | {"<script>"} <= names, names
    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)
    # In the tree: the install root's own hostile entries (security review,
    # round 1 on #226).
    rows = _rows(out["steps"][0])
    for name in [*HOSTILE_ROOT, FLAVOR + "/<script>"]:
        assert name in rows, repr(name)
        row = rows[name]
        assert row["badge"] is not None, repr(name)
        for text in (row["name"], row["nameTitle"], row["text"], row["aria"]):
            assert LSEP not in text and RLO not in text, repr(text)
        if LSEP in name:
            assert "2028" in row["name"], row["name"]
        if RLO in name:
            assert "202E" in row["name"].upper(), row["name"]
    assert IMG in rows[IMG]["name"]
    view = out["steps"][1]
    assert "missing" not in view, view
    heads = [h for g in view["groups"] for h in g["heads"]]
    assert heads, view
    for text in [view["text"], *heads, *(g["text"] for g in view["groups"])]:
        assert LSEP not in text and RLO not in text, repr(text)
    joined = "\n".join(heads)
    assert IMG in joined, "markup is drawn as its own text"
    assert "<script>" in joined
    assert "2028" in joined and "202E" in joined.upper(), joined
    assert set(HOSTILE_ROOT) <= set(view["places"]), view["places"]


# ─── the detail pane: snapshot facts ─────────────────────────────────────────


@pytest.fixture
def snapped(server: Any, install: Path) -> dict[str, Any]:
    """A real snapshot of the copied capture, taken with `snap create`
    through the server (it writes only to the store under the user data
    folder), then a SavedVariables file changed, so it differs from its
    copy. Returns the manifest."""
    manifest: dict[str, Any] = json.loads(
        _stdout(server, ["snap", "create"], {"flavor": FLAVOR, "message": "m13-27t label"})
    )
    with (install / FLAVOR / SV_FILE).open("ab") as handle:
        handle.write(b"\n")
    return manifest


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_the_detail_pane_shows_the_snapshot_facts(
    server: Any, snapped: dict[str, Any], home: Path, tmp_path: Path
) -> None:
    """`explain`'s `snapshot` is shown in Python's words (`snapshot.words`,
    which M13-27 adds; conductor ruling of 2026-10-16): the snapshot that
    holds the file, by id and label, and whether the file on disk is the
    same or differs."""
    rels = ["WTF/Config.wtf", SV_FILE]
    scenario, _ = _scenario(server, rels, [{"click": r} for r in rels])
    facts = [_explain(scenario, r)["snapshot"] for r in rels]
    for fact in facts:
        assert fact["holds"] is True and fact["confirmed"] is True, fact
        assert fact["label"] == "m13-27t label", fact
        assert isinstance(fact.get("words"), str), "M13-27: `snapshot.words` in explain --json"
        assert fact["snapshot_id"] in fact["words"] and fact["label"] in fact["words"], fact
    assert [f["live"] for f in facts] == ["same", "differs"], facts
    assert facts[0]["words"] != facts[1]["words"], "Python words `live`"
    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)
    for pane, fact in zip(out["steps"], facts, strict=True):
        _clean(pane)
        assert fact["words"].replace(str(home), "~") in pane["pane"], (fact["words"], pane["pane"])
        assert "could not be confirmed" not in pane["pane"].lower()
        assert str(home) not in pane["pane"]


def _snapshot_variant(base: dict[str, Any], words: str, **fields: Any) -> dict[str, Any]:
    report = json.loads(json.dumps(base))
    report["snapshot"].update(fields, words=words)
    return report


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_the_pane_shows_the_snapshot_words_and_words_nothing_itself_constructed(
    server: Any, snapped: dict[str, Any], tmp_path: Path
) -> None:
    """The pane says what `snapshot.words` says, verbatim, and does not word
    the other fields itself: two replies with the same words read the same
    even when `live`, `confirmed` and `path` differ, and different words
    read differently when the fields are the same. (Words and fields edited
    in the real JSON: constructed.)"""
    rels = ["WTF/Config.wtf", SV_FILE, ACCOUNT + "/SavedVariables/WowLab.lua"]
    rels.append(ACCOUNT + "/SavedVariables/Syndicator.lua")
    scenario, _ = _scenario(server, rels, [{"click": r} for r in rels])
    base = _explain(scenario, "WTF/Config.wtf")
    same = "m13-27t constructed words, the first"
    other = "m13-27t constructed words, the second"
    variants = [
        _snapshot_variant(base, same),
        _snapshot_variant(
            base, same, live="differs", differs=True, confirmed=False, path="WTF/CONFIG.WTF"
        ),
        _snapshot_variant(base, other),
        _snapshot_variant(base, other + " " + IMG + " line" + LSEP + "sep " + RLO + "x"),
    ]
    for rel, report in zip(rels, variants, strict=True):
        _set_explain(scenario, rel, report)
    out = _drive(tmp_path / "node", scenario)
    residue = []
    for pane in out["steps"]:
        _clean(pane)
        # Everything but the title and the path, which name the file clicked.
        residue.append(pane["rest"])
    assert same in residue[0] and same in residue[1], residue[:2]
    assert residue[0] == residue[1], "the page words no field itself"
    assert other in residue[2] and same not in residue[2], residue[2]
    assert IMG in residue[3] and LSEP not in residue[3] and RLO not in residue[3], residue[3]
    assert "2028" in residue[3] and "202E" in residue[3].upper(), residue[3]


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_a_copy_that_could_not_be_confirmed_is_said_so_constructed(
    server: Any, snapped: dict[str, Any], tmp_path: Path
) -> None:
    """M13-32: when the snapshot holds the file under another spelling that
    could not be confirmed to name it (`confirmed` False), or the latest
    snapshot that held it did (`last_held_confirmed` False), the pane shows
    Python's words for it, which say it could not be confirmed and name the
    spelling. With `confirmed` True there is no such sentence. (The two
    cases cannot be made on a test volume: fields and words edited in the
    real JSON, constructed.)"""
    rels = ["WTF/Config.wtf", SV_FILE, ACCOUNT + "/SavedVariables/WowLab.lua"]
    scenario, _ = _scenario(server, rels, [{"click": r} for r in rels])
    base = _explain(scenario, "WTF/Config.wtf")
    held = base["snapshot"]
    assert held["holds"] is True and held["confirmed"] is True
    assert isinstance(held.get("words"), str), "M13-27: `snapshot.words` in explain --json"
    assert "could not be confirmed" not in held["words"]
    odd_words = (
        "In the latest snapshot (m13-27t): it holds WTF/CONFIG.WTF, which could not be "
        "confirmed to be this file; the file on disk is the same"
    )
    gone_words = (
        "In the latest snapshot (m13-27t): not held; the latest snapshot that holds it is "
        "m13-27t-earlier, as WTF/config.WTF, which could not be confirmed to be this file"
    )
    odd = _snapshot_variant(base, odd_words, path="WTF/CONFIG.WTF", confirmed=False)
    _set_explain(scenario, SV_FILE, odd)
    gone = _snapshot_variant(
        base,
        gone_words,
        holds=False,
        path=None,
        confirmed=None,
        live=None,
        differs=None,
        last_held={"id": "m13-27t-earlier", "created_at": "2026-10-01T00:00:00Z", "label": ""},
        last_held_path="WTF/config.WTF",
        last_held_confirmed=False,
    )
    _set_explain(scenario, ACCOUNT + "/SavedVariables/WowLab.lua", gone)
    out = _drive(tmp_path / "node", scenario)
    for pane in out["steps"]:
        _clean(pane)
    plain, odd_pane, gone_pane = (p["pane"] for p in out["steps"])
    assert "could not be confirmed" not in plain.lower(), plain
    assert odd_words in odd_pane, odd_pane
    assert gone_words in gone_pane, gone_pane


# ─── the detail pane: hints ──────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_each_hint_has_a_copy_button_that_copies_the_command_exactly(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """Every hint is shown with a copy button; the button puts exactly
    `hint.command` on the clipboard (quoted for its shell, home folder and
    all), runs nothing, and the hint says which shell's syntax it is in
    (`hint.shell`). On screen the home folder is `~`."""
    rels = ["WTF/Config.wtf", SV_FILE]
    scenario, _ = _scenario(server, rels, [])
    hints = {r: _explain(scenario, r)["hints"] for r in rels}
    assert all(len(h) >= 2 for h in hints.values()), hints
    assert any(str(home) in h["command"] for h in hints[SV_FILE]), "a hint names a path"
    assert any(" " in h["command"] for h in hints[SV_FILE]), "a quoted path"
    steps: list[dict[str, Any]] = []
    for rel in rels:
        steps.append({"click": rel})
        steps += [{"copy": n} for n in range(len(hints[rel]))]
    scenario["steps"] = steps
    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)
    at = 0
    for rel in rels:
        pane = out["steps"][at]
        _clean(pane)
        assert len(pane["hints"]) == len(hints[rel]), pane["hints"]
        assert pane["copyButtons"] == len(hints[rel]), pane
        for shown, hint in zip(pane["hints"], hints[rel], strict=True):
            assert shown["copies"] == 1, shown
            assert hint["shell"].lower() in shown["text"].lower(), (hint["shell"], shown)
            assert hint["command"].replace(str(home), "~") in shown["text"], shown
            assert str(home) not in shown["text"], shown
        for n, hint in enumerate(hints[rel]):
            copied = out["steps"][at + 1 + n]
            assert "missing" not in copied, copied
            assert copied["clipboard"][-1] == hint["command"], copied
            assert copied["newRequests"] == 0, "a hint is never run"
        at += 1 + len(hints[rel])
    assert len(out["clipboard"]) == sum(len(h) for h in hints.values())


# SavedVariables names holding shell metacharacters (constructed): a quote,
# a command substitution, a backtick, `;` and `&`. Allowed on every OS.
METACHAR = ["m13-27t q'$(touch pwned)`id`;&.lua", "m13-27t ps'$(touch pwned)`id`;&.lua"]


@pytest.fixture
def metachar(install: Path) -> Path:
    saved = install / FLAVOR / ACCOUNT / "SavedVariables"
    for name in METACHAR:
        (saved / name).write_text("X = 1\n", encoding="utf-8")
    return install


def _powershell(hint: dict[str, Any]) -> dict[str, Any]:
    """The hint as Python quotes it for PowerShell: every argument in single
    quotes, a quote doubled (`ExplainHint`)."""
    line = ["wowlab", *hint["argv"]]
    return {
        **hint,
        "shell": "powershell",
        "command": " ".join("'" + a.replace("'", "''") + "'" for a in line),
    }


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_a_hint_says_its_own_shell_and_copies_exactly_constructed(
    server: Any, metachar: Path, tmp_path: Path
) -> None:
    """The shell named is the hint's own (`posix` or `powershell`), not one
    the page assumes, and each copy is that hint's `command` byte for byte,
    whatever the shell: a page that quotes `argv` itself would put the
    wrong quoting on the clipboard (security review, round 1 on #226). One
    file name holds `'`, `$(…)`, a backtick, `;` and `&`. (`shell` and
    `command` edited to PowerShell's: constructed.)"""
    posix_rel = ACCOUNT + "/SavedVariables/" + METACHAR[0]
    ps_rels = [SV_FILE, ACCOUNT + "/SavedVariables/" + METACHAR[1]]
    rels = ["WTF/Config.wtf", posix_rel, *ps_rels]
    scenario, _ = _scenario(server, rels, [])
    for rel in ps_rels:
        report = _explain(scenario, rel)
        report["hints"] = [_powershell(h) for h in report["hints"]]
        _set_explain(scenario, rel, report)
    hints = {r: _explain(scenario, r)["hints"] for r in rels}
    # The name is an argument of a posix hint, quoted by Python (its `'`
    # becomes `'"'"'`), so the command is not the name pasted in.
    named = [h for h in hints[posix_rel] if any(METACHAR[0] in a for a in h["argv"])]
    assert named and all("'\"'\"'" in h["command"] for h in named), hints[posix_rel]
    assert any("''" in h["command"] for h in hints[ps_rels[1]]), hints[ps_rels[1]]
    steps: list[dict[str, Any]] = []
    for rel in rels:
        steps.append({"click": rel})
        steps += [{"copy": n} for n in range(len(hints[rel]))]
    scenario["steps"] = steps
    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)
    at = 0
    for rel in rels:
        pane = out["steps"][at]
        _clean(pane)
        assert len(pane["hints"]) == len(hints[rel]), (rel, pane["hints"])
        for shown, hint in zip(pane["hints"], hints[rel], strict=True):
            other = "posix" if hint["shell"] == "powershell" else "powershell"
            assert hint["shell"] in shown["text"].lower(), (hint["shell"], shown)
            assert other not in shown["text"].lower(), (other, shown)
        for n, hint in enumerate(hints[rel]):
            copied = out["steps"][at + 1 + n]
            assert "missing" not in copied, copied
            assert copied["clipboard"][-1] == hint["command"], (copied, hint["command"])
            assert copied["newRequests"] == 0
        at += 1 + len(hints[rel])


HOSTILE_SV_FILES = [IMG + ".lua", "line" + LSEP + "sep.lua", RLO + "aul.exe.lua"]


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_hostile_names_in_hints_copy_exactly_and_draw_inert_constructed(
    server: Any, hostile: Path, tmp_path: Path
) -> None:
    """A hint naming a hostile file: drawn as text, U+2028 and U+202E shown
    as codes, yet the copy is the command exactly, characters and all."""
    rels = [ACCOUNT + "/SavedVariables/" + n for n in HOSTILE_SV_FILES]
    scenario, _ = _scenario(server, rels, [])
    hints = {r: _explain(scenario, r)["hints"] for r in rels}
    steps: list[dict[str, Any]] = []
    for rel in rels:
        named = [h for h in hints[rel] if rel.rsplit("/", 1)[-1] in h["command"]]
        assert named, (rel, hints[rel])
        steps.append({"click": rel})
        steps += [{"copy": n} for n in range(len(hints[rel]))]
    scenario["steps"] = steps
    out = _drive(tmp_path / "node", scenario)
    at = 0
    for rel in rels:
        pane = out["steps"][at]
        _clean(pane)
        assert len(pane["hints"]) == len(hints[rel]), pane["hints"]
        for shown in pane["hints"]:
            assert LSEP not in shown["text"] and RLO not in shown["text"], repr(shown)
        for n, hint in enumerate(hints[rel]):
            assert out["steps"][at + 1 + n]["clipboard"][-1] == hint["command"]
        at += 1 + len(hints[rel])
    shown_all = "\n".join(h["text"] for p in out["steps"] if "hints" in p for h in p["hints"])
    assert IMG in shown_all and "2028" in shown_all and "202E" in shown_all.upper()


# ─── the detail pane: Preview ────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_preview_runs_the_first_view_exactly_and_draws_a_tree(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """The owner's check, "preview Config.wtf and one SavedVariables file":
    Preview runs the entry's `views[0]` through `/api/run` with exactly its
    `command` and `params`, nothing added, and draws the JSON it printed as a
    collapsible, read-only tree in the pane."""
    rels = ["WTF/Config.wtf", SV_FILE]
    scenario, _ = _scenario(server, rels, [])
    views = {r: _explain(scenario, r)["views"] for r in rels}
    for rel in rels:
        assert views[rel], rel
        view = views[rel][0]
        _add_run(scenario, view, _stdout(server, view["command"], view["params"]))
    scenario["steps"] = [s for r in rels for s in ({"click": r}, {"preview": True})]
    out = _drive(tmp_path / "node", scenario)
    _own_api_only(out)
    for n, rel in enumerate(rels):
        picked, previewed = out["steps"][2 * n], out["steps"][2 * n + 1]
        _clean(picked)
        assert picked["previewButtons"] == 1, picked
        assert "missing" not in previewed, previewed
        view = views[rel][0]
        sent = previewed["newRequests"]
        assert len(sent) == 1, sent
        assert sent[0]["url"].startswith("/api/run?"), sent
        assert sent[0]["body"] == {"command": view["command"], "params": view["params"]}, sent
        printed = json.loads(scenario["runs"][n]["stdout"])
        assert isinstance(printed, dict) and printed, rel
        for key in printed:
            assert key in previewed["pane"], (rel, key)
        assert previewed["details"] >= 1, "a collapsible tree"
        assert previewed["fields"] == 0, "read-only"


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_preview_runs_only_the_first_view_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """An entry with two views has one Preview button, and it runs only
    `views[0]`; the second view is never run. (A second view added to the
    real JSON: constructed.)"""
    rel = "WTF/Config.wtf"
    scenario, _ = _scenario(server, [rel], [{"click": rel}, {"preview": True}])
    report = _explain(scenario, rel)
    first = report["views"][0]
    second = {
        "command": ["binds", "list"],
        "params": {"flavor": FLAVOR},
        "argv": ["binds", "list", "--flavor", FLAVOR],
        "does": "m13-27t constructed second view",
    }
    report["views"].append(second)
    _set_explain(scenario, rel, report)
    _add_run(scenario, first, _stdout(server, first["command"], first["params"]))
    _add_run(scenario, second, json.dumps({"m13-27t": "second view"}))
    out = _drive(tmp_path / "node", scenario)
    picked, previewed = out["steps"]
    _clean(picked)
    assert picked["previewButtons"] == 1, picked
    assert [r["body"] for r in previewed["newRequests"]] == [
        {"command": first["command"], "params": first["params"]}
    ]
    assert not _runs_of(out, ["binds", "list"]), "the second view never runs"


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_no_view_means_no_preview_button(server: Any, install: Path, tmp_path: Path) -> None:
    """A path with no view (a folder; `Logs/`) has no Preview button, while
    one with a view does: the button follows `views`, not the pane. Every
    control in a pane with no view, used, runs nothing (security review,
    round 1 on #226)."""
    rels = ["WTF", "Logs", "WTF/Config.wtf"]
    steps = [s for r in rels for s in ({"click": r}, {"activate": True})]
    scenario, _ = _scenario(server, rels, steps)
    assert [bool(_explain(scenario, r)["views"]) for r in rels] == [False, False, True]
    out = _drive(tmp_path / "node", scenario)
    counts = []
    for n in range(len(rels)):
        pane, used = out["steps"][2 * n], out["steps"][2 * n + 1]
        _clean(pane)
        counts.append(pane["previewButtons"])
        _only_copy_and_preview(used)
    assert counts == [0, 0, 1], counts
    for n in (0, 1):
        assert out["steps"][2 * n + 1]["newRequests"] == [], out["steps"][2 * n + 1]


def _refused_views(listed: dict[str, Any]) -> list[dict[str, Any]]:
    """A view for every command the server will not run (each leaf that
    declares `--yes`, and `ui`: `excluded`), and for each refused parameter
    (`looks page --out`, `log tail --follow`), derived from the command list
    the page reads, so a new one is covered too."""
    views = []
    for command in listed["commands"]:
        if command["excluded"]:
            views.append({"command": command["path"], "params": {}})
        for param in command["params"]:
            if param["refused"]:
                value = "true" if param["kind"] == "flag" else "m13-27t-out.html"
                views.append({"command": command["path"], "params": {param["name"]: value}})
    return [
        {**v, "argv": [*v["command"]], "does": "m13-27t constructed refused view"} for v in views
    ]


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_a_refused_command_is_never_offered_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """A view naming a command the server refuses gets no Preview button and
    is never run: every `--yes` command, `looks page --out` and `log tail
    --follow` (ADR-0029, rulings of 2026-10-15). (Refused views put in the
    real JSON: constructed.)"""
    listed = _send(server, "GET", "/api/commands").json()
    refused = _refused_views(listed)
    paths = {tuple(v["command"]) for v in refused}
    for must in (("snap", "restore"), ("undo",), ("doctor",), ("looks", "page"), ("log", "tail")):
        assert must in paths, must
    assert len([c for c in listed["commands"] if c["excluded"]]) >= 8
    tree = json.loads(_stdout(server, ["tree"], {"explain": True, "flavor": FLAVOR}))
    control = "WTF/Config.wtf"  # positive control: its real view is offered
    files = sorted(
        i["path"]
        for i in tree["items"]
        if i["kind"] == "file" and i["explained"] and i["path"] != control
    )
    assert len(files) >= len(refused), (len(files), len(refused))
    rels = files[: len(refused)]
    every = [control, *rels]
    steps = [s for r in every for s in ({"click": r}, {"activate": True})]
    # Then every control the page drew anywhere, tree rows included
    # (security review, round 2 on #226).
    steps.append({"activate": "everywhere"})
    scenario, _ = _scenario(server, every, steps)
    assert _explain(scenario, control)["views"], control
    for rel, view in zip(rels, refused, strict=True):
        report = _explain(scenario, rel)
        report["views"] = [view]
        report["hints"] = []
        _set_explain(scenario, rel, report)
    out = _drive(tmp_path / "node", scenario)
    _clean(out["steps"][0])
    assert out["steps"][0]["previewButtons"] == 1, "the control's view is offered"
    assert out["steps"][1]["newRequests"], "and used, it runs"
    for n, view in enumerate(refused, start=1):
        pane, used = out["steps"][2 * n], out["steps"][2 * n + 1]
        _clean(pane)
        assert pane["previewButtons"] == 0, view
        # Every control in the pane, used: none of them is another way in
        # (security review, round 1 on #226).
        _only_copy_and_preview(used)
        assert used["newRequests"] == [], (view, used["newRequests"])
    named = {tuple(v["command"]) for v in refused}
    for request in out["requests"]:
        if request["body"] is not None:
            assert tuple(request["body"]["command"]) not in named, request


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_whether_a_view_is_offered_comes_from_the_command_list_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """The page decides from `/api/commands` (`excluded`, and `refused` on a
    parameter), not from a list of its own (§14.5 D3): with `cvar list`
    marked excluded and `sv dump`'s `file` marked refused in the command list
    it reads, neither real view is offered, while `binds list`, left as it
    is, still is. (The command list edited: constructed.)"""
    rels = ["WTF/Config.wtf", SV_FILE, ACCOUNT + "/bindings-cache.wtf"]
    steps = [s for r in rels for s in ({"click": r}, {"activate": True})]
    steps.append({"activate": "everywhere"})
    scenario, _ = _scenario(server, rels, steps)
    views = [_explain(scenario, r)["views"][0] for r in rels]
    assert [v["command"] for v in views] == [["cvar", "list"], ["sv", "dump"], ["binds", "list"]]
    for command in scenario["commands"]["commands"]:
        if command["path"] == ["cvar", "list"]:
            command["excluded"] = "m13-27t constructed"
        if command["path"] == ["sv", "dump"]:
            for param in command["params"]:
                if param["name"] in views[1]["params"]:
                    param["refused"] = "m13-27t constructed"
    out = _drive(tmp_path / "node", scenario)
    counts = []
    for n in range(len(rels)):
        pane, used = out["steps"][2 * n], out["steps"][2 * n + 1]
        _clean(pane)
        counts.append(pane["previewButtons"])
        _only_copy_and_preview(used)
    assert counts == [0, 0, 1], counts
    assert _runs_of(out, ["binds", "list"]), "the offered view, used, runs"
    assert not _runs_of(out, ["cvar", "list"]) and not _runs_of(out, ["sv", "dump"])


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_a_refused_first_view_is_not_replaced_by_the_next_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """Preview is `views[0]` or nothing: when it is refused and `views[1]` is
    allowed, there is no Preview and nothing runs; the page does not fall
    back to the next view (security review, round 1 on #226). A control
    entry, whose `views[0]` is allowed, is offered. (Views edited:
    constructed.)"""
    rel, control = "WTF/Config.wtf", ACCOUNT + "/bindings-cache.wtf"
    steps = [s for r in (control, rel) for s in ({"click": r}, {"activate": True})]
    steps.append({"activate": "everywhere"})
    scenario, _ = _scenario(server, [control, rel], steps)
    report = _explain(scenario, rel)
    allowed = report["views"][0]
    assert allowed["command"] == ["cvar", "list"]
    refused = {
        "command": ["looks", "page"],
        "params": {"out": "m13-27t-out.html"},
        "argv": ["looks", "page", "--out", "m13-27t-out.html"],
        "does": "m13-27t constructed refused view",
    }
    report["views"] = [refused, allowed]
    report["hints"] = []
    _set_explain(scenario, rel, report)
    _add_run(scenario, allowed, _stdout(server, allowed["command"], allowed["params"]))
    out = _drive(tmp_path / "node", scenario)
    _clean(out["steps"][0])
    assert out["steps"][0]["previewButtons"] == 1, "the control is offered"
    pane, used = out["steps"][2], out["steps"][3]
    _clean(pane)
    assert pane["previewButtons"] == 0, pane
    _only_copy_and_preview(used)
    assert used["newRequests"] == [], used["newRequests"]
    assert not _runs_of(out, ["cvar", "list"]) and not _runs_of(out, ["looks", "page"])


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_a_hostile_preview_draws_as_inert_text_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """A preview whose JSON holds markup, U+2028 and U+202E in keys and
    values: drawn as text, the invisible characters escaped, never applied.
    (The view's output replaced: constructed.)"""
    rel = "WTF/Config.wtf"
    scenario, _ = _scenario(server, [rel], [{"click": rel}, {"preview": True}])
    view = _explain(scenario, rel)["views"][0]
    printed = {
        IMG: SCRIPT_CLOSE + "<b>bold</b>",
        "line" + LSEP + "sep": RLO + "aul.exe",
        "nested": {SCRIPT_CLOSE: [IMG, "line" + LSEP + "sep", RLO + "x"]},
        # Values a careless page would turn into a link or a navigation.
        "href": "https://m13-27t.invalid/x",
        "src": "javascript:alert(1)",
        "url": ["https://m13-27t.invalid/y", "//m13-27t.invalid/z"],
    }
    _add_run(scenario, view, json.dumps(printed, ensure_ascii=False))
    out = _drive(tmp_path / "node", scenario)
    # Every request stayed on the server's API, and nothing navigated (the
    # harness traps `location`, `window.open`, URL attributes and link
    # elements; security review, round 1 on #226).
    _own_api_only(out)
    previewed = out["steps"][1]
    assert "missing" not in previewed, previewed
    assert len(previewed["newRequests"]) == 1
    text = previewed["pane"]
    assert IMG in text and SCRIPT_CLOSE in text, "markup drawn as its own text"
    assert LSEP not in text and RLO not in text, repr(text)
    assert "2028" in text and "202E" in text.upper(), text
    assert "https://m13-27t.invalid/x" in text and "javascript:alert(1)" in text, "drawn as text"


# ─── the placeholder badge ───────────────────────────────────────────────────


def _placeholder(rel: str, entry_id: str) -> dict[str, Any]:
    """Python's verdict for `rel` in the flavor folder, classified as
    `entry_id`, under a reparse point that is no link (M13-32,
    `verdict_of(..., placeholder=True)`)."""
    entry = next(e for e in filemap.load().entries if e.id == entry_id)
    made = verdict_module.verdict_of(entry, "flavor", rel, placeholder=True)
    out: dict[str, Any] = made.model_dump(mode="json")
    return out


def _swap_verdict(
    report: dict[str, Any], key: tuple[str | None, str], verdict: dict[str, Any]
) -> int:
    swapped = 0
    for item in [*report.get("items", []), *report.get("ancestors", [])]:
        if (item.get("flavor_folder", FLAVOR), item["path"]) == key:
            item["verdict"] = verdict
            if item.get("explained"):
                item["explained"]["verdict"] = verdict
            swapped += 1
    return swapped


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
def test_a_placeholder_verdict_has_its_own_badge_class_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """A verdict with `placeholder` True (a path under a reparse point that
    is no link) gets the badge class `badge-placeholder`, on top of `badge`
    and its level's, in the tree and in the detail pane; its text is
    Python's label, "wowlab won't touch". No other badge has the class.
    (Config.wtf's verdict swapped for the placeholder one Python makes:
    constructed.)"""
    rel = "WTF/Config.wtf"
    verdict = _placeholder(rel, "config-wtf")
    assert verdict["placeholder"] is True and verdict["link"] is False
    assert verdict["level"] == "gate" and verdict["label"] == verdict_module.LABELS["no"]
    scenario, _ = _scenario(server, [rel], [{"open_all": True}, {"click": rel}])
    whole = json.loads(scenario["install_tree"])
    assert _swap_verdict(whole, (FLAVOR, rel), verdict) == 1
    scenario["install_tree"] = json.dumps(whole)
    for flavor, text in scenario["trees"].items():
        one = json.loads(text)
        if flavor == FLAVOR:
            assert _swap_verdict(one, (FLAVOR, rel), verdict) == 1
        scenario["trees"][flavor] = json.dumps(one)
    report = _explain(scenario, rel)
    report["verdict"] = verdict
    _set_explain(scenario, rel, report)
    out = _drive(tmp_path / "node", scenario)

    rows = out["steps"][0]["rows"]
    marked = [r for r in rows if r["badge"] and "badge-placeholder" in r["badge"]["cls"].split()]
    assert [r["path"] for r in marked] in ([rel], [FLAVOR + "/" + rel]), [r["path"] for r in marked]
    classes = marked[0]["badge"]["cls"].split()
    assert {"badge", "badge-gate", "badge-placeholder"} <= set(classes), classes
    assert marked[0]["badge"]["text"].startswith(verdict["label"]), marked[0]["badge"]
    pane = out["steps"][1]
    _clean(pane)
    assert "badge-placeholder" in pane["verdictBadge"].split(), pane["verdictBadge"]
    assert verdict["label"] in pane["verdict"] and verdict["words"] in pane["verdict"]


def _media_applies(query: str, scheme: str) -> bool:
    """Whether a `@media` query holds on a screen in `scheme` ("dark" or
    "light"). Only `screen`, `all` and `prefers-color-scheme` are taken as
    holding; anything else (`print`, a width, `not ...`) is taken as not
    holding, so a rule behind it never counts as painting the badge (code
    review, round 1 on #226)."""
    for part in query.lower().split(","):
        part = part.strip()
        features = re.findall(r"\(([^)]*)\)", part)
        words = [w for w in re.sub(r"\([^)]*\)", " ", part).split() if w not in ("and", "only")]
        if any(w not in ("screen", "all") for w in words):
            continue
        if all(
            re.fullmatch(r"\s*prefers-color-scheme\s*:\s*" + scheme + r"\s*", f) for f in features
        ):
            return True
    return False


def _rules(css: str, scheme: str = "dark") -> list[tuple[str, str]]:
    """The style rules that hold on a screen in `scheme`, in source order:
    top-level ones and those in a `@media` block that holds there;
    (selector, body)."""
    out: list[tuple[str, str]] = []
    at = 0
    while True:
        opened = css.find("{", at)
        if opened < 0:
            return out
        head = css[at:opened].strip()
        depth = 0
        for end in range(opened, len(css)):
            if css[end] == "{":
                depth += 1
            elif css[end] == "}":
                depth -= 1
                if depth == 0:
                    break
        body = css[opened + 1 : end]
        if head.startswith("@media"):
            if _media_applies(head[len("@media") :], scheme):
                out += _rules(body, scheme)
        elif not head.startswith("@"):
            out.append((head, body))
        at = end + 1


_PAINT = ("color", "background", "background-color", "background-image", "border-color")


def _effective(classes: set[str], css: str, scheme: str = "dark") -> dict[str, str]:
    """What the badge is painted with on a screen in `scheme`: for each paint
    property, the value of the matching rule with the most classes, the
    later one on a tie. Only selectors made of classes are considered, as
    every badge rule is."""
    best: dict[str, tuple[int, int, str]] = {}
    for order, (head, body) in enumerate(_rules(css, scheme)):
        for selector in head.split(","):
            selector = selector.strip()
            if not re.fullmatch(r"(\.[\w-]+)+", selector):
                continue
            needed = set(selector[1:].split("."))
            if not needed <= classes:
                continue
            for declaration in body.split(";"):
                if ":" not in declaration:
                    continue
                name, value = (part.strip() for part in declaration.split(":", 1))
                if name in _PAINT and (name not in best or best[name][:2] <= (len(needed), order)):
                    best[name] = (len(needed), order, value)
    return {name: value for name, (_, _, value) in best.items()}


@pytest.mark.xfail(strict=True, reason="M13-27 not implemented")
@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("level", ["gate", "no", "folder", "not_applicable", "unclassified"])
def test_the_placeholder_badge_is_painted_like_its_label(level: str, scheme: str) -> None:
    """A placeholder badge says "wowlab won't touch", the `no` label, so on
    screen, in each colour scheme on its own, it is painted as a `no` badge
    whatever its row's level (backlog, M13-27T, 2026-10-16)."""
    css = _css()
    assert re.search(r"\.badge-placeholder(?![\w-])", css), "a rule for the class"
    painted = _effective({"badge", f"badge-{level}", "badge-placeholder"}, css, scheme)
    as_no = _effective({"badge", "badge-no"}, css, scheme)
    assert painted.get("color") and painted.get("color") == as_no.get("color"), (painted, as_no)
    for name in ("background", "background-color", "background-image", "border-color"):
        assert painted.get(name) == as_no.get(name), (name, painted, as_no)


def test_the_paint_reader_takes_only_screen_rules_constructed() -> None:
    """The reader above, on a constructed sheet: a print-only or width-only
    rule does not count; a light-scheme rule counts in light only."""
    base = ".badge-no { color: #ff0000; }\n.badge-gate { color: #ffd700; }\n"
    rule = ".badge.badge-placeholder { color: #ff0000; }"
    on = {"badge", "badge-gate", "badge-placeholder"}
    assert _effective(on, base + rule)["color"] == "#ff0000"
    for query in ("print", "(max-width: 600px)", "not screen", "screen and (min-width: 1px)"):
        assert _effective(on, base + "@media " + query + " { " + rule + " }")["color"] == "#ffd700"
    light = base + "@media (prefers-color-scheme: light) { " + rule + " }"
    assert _effective(on, light, "light")["color"] == "#ff0000"
    assert _effective(on, light, "dark")["color"] == "#ffd700"
    dark = base + "@media screen and (prefers-color-scheme: dark) { " + rule + " }"
    assert _effective(on, dark, "dark")["color"] == "#ff0000"
    assert _effective(on, dark, "light")["color"] == "#ffd700"
