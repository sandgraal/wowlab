# Probe from review of m16/17-filemap-player-words; reproduces the player-text lint passing module names and doc pointers it claims to refuse
"""The M16-17 lint in `test_filemap.py` (`test_what_and_written_by_are_a_players_text`)
is said to refuse, in a row's `what` or `written_by`, "a module name in
backticks" and "a doc pointer". Its module set is a hand-written list of 14
names; `wowlab_core` has 36 modules, so text such as "read by `gamedata`" or
"`keymap`" passes. Its doc pattern names `docs/`, `LAB_PLAN`, `LAB_FORMATS` and
`GLOSSARY` only, so "see LAB_FILE_MAP.md" or "DECISIONS.md" passes.

Constructed strings (labelled): they exercise the lint's own patterns, not a
parser.

Cases:
- control: `luadata` in backticks and `docs/` are refused today;
- probe: every `wowlab_core` module in backticks, and the other top-level doc
  names, are refused (fails at ba7b4bd).
"""

from __future__ import annotations

import importlib.util
import pkgutil
import re
from pathlib import Path
from types import ModuleType

import pytest

import wowlab_core

_TESTS = Path(__file__).resolve().parents[1]


def _lint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_m16_17_lint", _TESTS / "test_filemap.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refused(lint: ModuleType, text: str) -> bool:
    if lint._NOT_FOR_PLAYERS.search(text):
        return True
    return bool(lint._MODULES & set(re.findall(r"`([a-z_]+)`", text)))


def test_control_known_module_and_docs_path_are_refused() -> None:
    lint = _lint()
    assert _refused(lint, "read by `luadata`")
    assert _refused(lint, "see docs/LAB_PLAN.md")


_MODULE_NAMES = sorted(
    m.name for m in pkgutil.iter_modules(wowlab_core.__path__) if not m.name.startswith("_")
)
# INSTALL.md is written for players; every other top-level doc is the spec's.
_DOC_NAMES = sorted(
    p.stem for p in (_TESTS.parents[2] / "docs").glob("*.md") if p.stem != "INSTALL"
)


@pytest.mark.parametrize("name", _MODULE_NAMES)
def test_every_module_in_backticks_is_refused_constructed(name: str) -> None:
    assert _refused(_lint(), f"read by `{name}`"), name


@pytest.mark.parametrize("name", _DOC_NAMES)
def test_every_top_level_doc_is_refused_constructed(name: str) -> None:
    assert _refused(_lint(), f"see {name}.md"), name
