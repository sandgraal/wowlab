"""The header's flavor picker of `wowlab ui` (M16-114, docs/LAB_PLAN.md §16
CLI conventions, amendment of 2026-11-11).

The page is driven under Node with the stand-in DOM of `test_ui_explorer.py`
and the Character tab's steps of `test_ui_character.py`, fed what the real
CLI printed through the real server. The install is the captured one
(`fixtures/macos/`) copied under a stand-in home folder, with a second
flavor folder added beside it: `_alpha_`, holding only a `.flavor.info`
that names the capture's own product, so it has a version and no character
and sorts first. That second folder is a `constructed` boundary input (L8).
Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import test_ui_character as char_tab
import test_ui_explorer as first_stage
from test_ui_explorer import FLAVOR, _page_ids, _send, _stdout

home = first_stage.home
install = first_stage.install
server = first_stage.server
timed = char_tab.timed

EMPTY = "_alpha_"  # constructed; sorts before the capture's flavor folder (tests may name it, L6)
NEWER = char_tab.NEWER
ACCOUNT = char_tab.ACCOUNT
PICK_READS = char_tab.PICK_READS

_VIEW = r"""
function flavorView() {
  const box = ids["run-flavor-box"];
  const picker = ids["run-flavor"];
  return {
    boxHidden: box ? box.hidden : null,
    value: picker ? picker.value : null,
    options: picker ? picker.options.map((o) => o.value) : null,
  };
}
"""

_STEPS = r"""
      } else if (step.run_flavor !== undefined) {
        const before = requests.length;
        ids["run-flavor"].value = step.run_flavor; ids["run-flavor"].fire("change"); await tick();
        out.steps.push(Object.assign(flavorView(), { newRequests: requests.slice(before) }));
      } else if (step.command_run !== undefined) {
        const before = requests.length;
        const button = ids.commands.all().find((e) => e.tagName === "button" && e.textContent === step.command_run);
        if (!button) { out.steps.push({ missing: step.command_run }); continue; }
        button.fire("click"); await tick();
        for (const [name, value] of Object.entries(step.fields || {})) {
          const input = everywhere().find((e) => e.id === "param-" + name);
          if (!input) { out.steps.push({ missing: "param-" + name }); continue; }
          input.value = value;
        }
        ids.run.fire("click"); await tick();
        out.steps.push(Object.assign(flavorView(), { newRequests: requests.slice(before) }));
"""

_ANCHOR = "      } else if (step.preview !== undefined) {\n"
_VIEW_ANCHOR = "vm.createContext(ctx);\n"


_CHAR_HARNESS = char_tab._harness


def _harness() -> str:
    text = _CHAR_HARNESS()
    assert text.count(_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    text = text.replace(_ANCHOR, _STEPS.lstrip("\n") + _ANCHOR, 1)
    # The first view also says what the header shows.
    return text.replace(
        "out.first = snap();", "out.first = Object.assign(snap(), flavorView());", 1
    )


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = char_tab._harness
    char_tab._harness = _harness
    try:
        return char_tab._drive(folder, scenario)
    finally:
        char_tab._harness = original


@pytest.fixture(autouse=True)
def no_env_flavor(monkeypatch: pytest.MonkeyPatch) -> None:
    from wowlab_core import install as discovery

    monkeypatch.delenv(discovery.ENV_FLAVOR, raising=False)


@pytest.fixture
def two(install: Path, timed: Path) -> Path:
    """The capture, its lab files at fixed times (the later one is `NEWER`),
    with a second flavor folder, constructed: the capture's product (so a
    version), no WTF/ (so no character)."""
    other = install / EMPTY
    other.mkdir()
    (other / ".flavor.info").write_bytes((install / FLAVOR / ".flavor.info").read_bytes())
    return install


def _list(flavor: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"brief": True}
    if flavor is not None:
        params["flavor"] = flavor
    return params


def _scenario(server: Any, steps: list[dict[str, Any]], flavors: list[str]) -> dict[str, Any]:
    """The command list, the whole install's tree, `char list` with no flavor
    and with each of `flavors`, the picked character's three reads in the
    capture's flavor, and `cvar list` in each of `flavors`, as the real CLI
    printed them."""
    runs = []
    for flavor in [None, *flavors]:
        reply = first_stage._run(server, ["char", "list"], _list(flavor)).json()
        runs.append(
            {
                "command": ["char", "list"],
                "params": _list(flavor),
                "stdout": reply["stdout"],
                "exit_code": reply["exit_code"],
            }
        )
    for flavor in flavors:
        params = {"flavor": flavor}
        runs.append({"command": ["cvar", "list"], "params": params, "stdout": "{}", "exit_code": 0})
    picked = {"character": NEWER, "account": ACCOUNT, "flavor": FLAVOR}
    for command in PICK_READS:
        runs.append(
            {
                "command": list(command),
                "params": picked,
                "stdout": _stdout(server, list(command), picked),
            }
        )
    return {
        "ids": _page_ids(),
        "sep": os.sep,
        "commands": _send(server, "GET", "/api/commands").json(),
        "first_flavor": FLAVOR,
        "trees": {},
        "install_tree": _stdout(server, ["tree"], {"whole_install": True, "explain": True}),
        "explains": {},
        "runs": runs,
        "held": [],
        "steps": steps,
    }


def _runs(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [r["body"] for r in out["requests"] if r["body"] is not None]


def test_the_header_holds_the_picker_hidden(server: Any) -> None:
    body = first_stage._page(server).body.decode("utf-8")
    header = body[body.index('<header class="banner">') : body.index("</header>")]
    assert '<label id="run-flavor-box" class="run-flavor" hidden>' in header
    assert '<select id="run-flavor"></select>' in header


def test_one_flavor_shows_no_picker_and_adds_no_flavor(
    server: Any, install: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"char_tab": True}], [])
    out = _drive(tmp_path / "node", scenario)
    assert out["first"]["boxHidden"] is True
    runs = _runs(out)
    # Besides the Account tab's `char list --summary` (M17-01), one `char
    # list`: the tab's, with no flavor; none at the start.
    assert [r for r in runs if r["command"] == ["char", "list"]] == [
        {"command": ["char", "list"], "params": {"summary": True}},
        {"command": ["char", "list"], "params": _list(None)},
    ]
    # Only the picked character's reads name a flavor: the one `char list` gave.
    reads = [list(c) for c in PICK_READS]
    assert all("flavor" not in r["params"] for r in runs if r["command"] not in reads)


def test_two_flavors_show_the_picker_on_the_one_with_characters_constructed(
    server: Any, two: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [], [FLAVOR, EMPTY])
    listed = json.loads(scenario["runs"][0]["stdout"])
    assert listed["flavor_folder"] is None
    # The constructed folder has a version and sorts first: the start is not
    # "the first with a version" but "the only one with characters".
    assert [f["folder"] for f in listed["flavors"]] == [EMPTY, FLAVOR]
    assert all(f["version"] for f in listed["flavors"])
    out = _drive(tmp_path / "node", scenario)
    assert out["first"]["boxHidden"] is False
    assert out["first"]["options"] == [EMPTY, FLAVOR]
    assert out["first"]["value"] == FLAVOR
    # The start was read from one `char list` run with no flavor, after the
    # tree, which comes after the Account tab's run (M17-01).
    runs = _runs(out)
    assert runs[0] == {"command": ["char", "list"], "params": {"summary": True}}
    assert runs[1:3] == [
        {"command": ["tree"], "params": {"whole_install": True, "explain": True}},
        {"command": ["char", "list"], "params": _list(None)},
    ]


def test_no_characters_anywhere_starts_on_the_first_with_a_version_constructed(
    server: Any, two: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [], [])
    report = json.loads(scenario["runs"][0]["stdout"])
    for f in report["flavors"]:
        f["characters"] = 0
    report["flavors"][0]["version"] = None
    scenario["runs"][0]["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node", scenario)
    assert out["first"]["value"] == report["flavors"][1]["folder"]
    for f in report["flavors"]:
        f["version"] = None
    scenario["runs"][0]["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node2", scenario)
    assert out["first"]["value"] == out["first"]["options"][0]


def test_a_flavor_not_looked_inside_is_not_passed_over_constructed(
    server: Any, two: Path, tmp_path: Path
) -> None:
    """When `char list` could not look inside a place in the other flavor,
    that flavor may hold characters: the start falls to the first flavor
    with a version (the constructed one, which sorts first). The place is
    added to the real report's JSON (constructed)."""
    scenario = _scenario(server, [], [])
    report = json.loads(scenario["runs"][0]["stdout"])
    report["not_looked_at"] = [
        {"path": "WTF/Account", "reason": "a link, not followed", "account": None, "flavor": EMPTY}
    ]
    scenario["runs"][0]["stdout"] = json.dumps(report)
    scenario["runs"][0]["exit_code"] = 1
    out = _drive(tmp_path / "node", scenario)
    assert out["first"]["value"] == EMPTY
    # A place in the flavor with characters itself does not change the start.
    report["not_looked_at"][0]["flavor"] = FLAVOR
    scenario["runs"][0]["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node2", scenario)
    assert out["first"]["value"] == FLAVOR


def test_every_run_carries_the_picked_flavor_constructed(
    server: Any, two: Path, tmp_path: Path
) -> None:
    steps = [
        {"char_tab": True},
        {"command_run": "cvar list"},
        {"run_flavor": EMPTY},
        {"command_run": "cvar list"},
        {"command_run": "cvar list", "fields": {"flavor": FLAVOR}},
    ]
    out = _drive(tmp_path / "node", _scenario(server, steps, [FLAVOR, EMPTY]))
    tab, first, switched, second, own = out["steps"]
    assert [r["body"] for r in tab["newRequests"]] == [
        {"command": ["char", "list"], "params": _list(FLAVOR)},
        *[
            {
                "command": list(c),
                "params": {"character": NEWER, "account": ACCOUNT, "flavor": FLAVOR},
            }
            for c in PICK_READS
        ],
    ]
    assert [r["body"] for r in first["newRequests"]] == [
        {"command": ["cvar", "list"], "params": {"flavor": FLAVOR}}
    ]
    # Another flavor: the Character tab, still shown, reads that one.
    assert switched["value"] == EMPTY
    assert [r["body"] for r in switched["newRequests"]] == [
        {"command": ["char", "list"], "params": _list(EMPTY)}
    ]
    assert [r["body"] for r in second["newRequests"]] == [
        {"command": ["cvar", "list"], "params": {"flavor": EMPTY}}
    ]
    # A flavor typed in the command's own field wins.
    assert [r["body"] for r in own["newRequests"]] == [
        {"command": ["cvar", "list"], "params": {"flavor": FLAVOR}}
    ]
    # Every run after the start that takes --flavor carried one; the whole
    # install's tree, which refuses it, and the Account tab's `char list
    # --summary` (M17-01), which reads every flavor, never did.
    takes = {
        tuple(c["path"])
        for c in _send(server, "GET", "/api/commands").json()["commands"]
        if any(p["name"] == "flavor" for p in c["params"])
    }
    runs = _runs(out)
    summary = [b for b in runs if b["params"].get("summary")]
    assert summary == [{"command": ["char", "list"], "params": {"summary": True}}]
    for body in runs[3:]:
        if body["params"].get("summary"):
            continue
        if tuple(body["command"]) in takes and body["params"].get("whole_install") is not True:
            assert body["params"].get("flavor") in (FLAVOR, EMPTY), body
    assert all("flavor" not in b["params"] for b in runs if b["params"].get("whole_install"))
