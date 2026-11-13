"""One Account-tab card that fails leaves the rest of the page standing (the
hub-filter crash, owner report 2026-11-13).

That crash was a throw in the hub's filters: it stopped the page's start-up,
so no card and no explorer tree was drawn. Each card now runs on its own;
its failure is said in its own line, and the other cards and the tree still
load.

The page is driven under Node with the harness of
`test_ui_account_patch_day.py`, fed what the real CLI printed through the
real server, on that module's two-capture install. The failures are
`constructed` (L8): a copy of the templates in `tmp_path` whose `drawHub`
throws, or whose `takePatchDay` fails after its first await. Each run is
compared with a run of the unchanged page on the same scenario. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import test_ui_account as hub_tab
import test_ui_account_patch_day as card_tab
import test_ui_character as char_tab

both = card_tab.both
home = card_tab.home
server = card_tab.server
no_env_flavor = card_tab.no_env_flavor

FAILED = "This part of the page could not be drawn: "

_STEPS = r"""
      } else if (step.lines !== undefined) {
        out.steps.push({ lines: {
          hub: ids["hub-status"].textContent,
          backup: ids["backup-status"].textContent,
          patchDay: ids["patch-day-status"].textContent,
        }, hubRows: ids["hub-table"].all().filter((e) => e.tagName === "tr" && has(e, "hub-row")).length,
          patchBlocks: ids["patch-day-lines"].children.length, treeRoot: snap().treeRoot });
"""

_CARD_HARNESS = card_tab._harness


def _harness() -> str:
    text = _CARD_HARNESS()
    assert text.count(hub_tab._ANCHOR) == 1
    return text.replace(hub_tab._ANCHOR, _STEPS.lstrip("\n") + hub_tab._ANCHOR, 1)


def _drive(
    folder: Path, scenario: dict[str, Any], templates: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    with monkeypatch.context() as m:
        m.setattr(char_tab, "_TEMPLATES", templates)
        m.setattr(card_tab, "_harness", _harness)
        return card_tab._drive(folder, scenario)


def _templates(folder: Path, old: str, new: str) -> Path:
    """A copy of the templates whose page.js has `old` replaced by `new`
    once (constructed)."""
    shutil.copytree(char_tab._TEMPLATES, folder)
    page = folder / "page.js"
    text = page.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    page.write_text(text.replace(old, new, 1), encoding="utf-8")
    return folder


def _tree_asked(out: dict[str, Any]) -> bool:
    return any(r["body"] is not None and r["body"]["command"] == ["tree"] for r in out["requests"])


@pytest.fixture
def baseline(
    server: Any, both: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The scenario, and what the unchanged page draws from it."""
    scenario = card_tab._scenario(server, [{"lines": True}], tree=True)
    out = _drive(tmp_path / "node-plain", scenario, char_tab._TEMPLATES, monkeypatch)
    [step] = out["steps"]
    assert _tree_asked(out) and step["treeRoot"]
    assert step["hubRows"] > 0 and step["patchBlocks"] > 0
    assert not any(FAILED in line for line in step["lines"].values())
    return scenario, step


@pytest.mark.parametrize(
    ("old", "new", "failing", "error"),
    [
        (
            "  function drawHub(focusKey) {\n",
            '  function drawHub(focusKey) {\n    throw new Error("constructed drawHub failure");\n',
            "hub",
            "Error: constructed drawHub failure",
        ),
        (
            "  async function takePatchDay(report) {\n",
            "  async function takePatchDay(report) {\n    await null;\n"
            '    throw new Error("constructed patch-day failure");\n',
            "patchDay",
            "Error: constructed patch-day failure",
        ),
    ],
    ids=["drawHub-throws-constructed", "takePatchDay-rejects-constructed"],
)
def test_a_card_that_fails_says_so_and_the_rest_still_load(
    baseline: tuple[dict[str, Any], dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: str,
    new: str,
    failing: str,
    error: str,
) -> None:
    scenario, plain = baseline
    templates = _templates(tmp_path / "templates", old, new)
    out = _drive(tmp_path / "node-broken", scenario, templates, monkeypatch)
    [step] = out["steps"]
    # The failing card's own line names the error.
    assert step["lines"][failing] == FAILED + error
    # Every other card's line reads as it does on the unchanged page.
    for name, text in plain["lines"].items():
        if name != failing:
            assert step["lines"][name] == text, name
    # The card that did not fail is drawn; the explorer tree is read and drawn.
    if failing == "hub":
        assert step["patchBlocks"] == plain["patchBlocks"]
    else:
        assert step["hubRows"] == plain["hubRows"]
    assert _tree_asked(out)
    assert step["treeRoot"] == plain["treeRoot"]
