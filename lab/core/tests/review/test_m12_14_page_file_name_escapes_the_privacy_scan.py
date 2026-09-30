# Probe from review of m12/14-wiki-daily; reproduces a page named after a character (Name-Realm) or an email address passing check_wiki
"""`check_wiki` scans page text for personal data but never a page's file
name, and `_in_page_name` exempts any `First-Second` pair that is two
neighbouring words of *any* page name, including a page the run itself
creates. A run that adds `Thrall-Stormrage.md` and links it from the sidebar
therefore publishes the pair three times (file name, page URL, sidebar text)
and the gate passes. A page file named after an email address passes the
same way. The ticket: nothing matches "a `Name-Realm` pair outside the
documented placeholders, an email address or a BattleTag".

Positive control: the same pair in the prose of an existing page, with no
such page, is flagged; the baseline wiki passes.

Constructed: the real wiki fixture (d4344c8) plus the generated page and an
index naming every module; the names and address are invented.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "scripts"
REAL_WIKI = ROOT / "tests" / "scripts" / "fixtures" / "wiki-d4344c8"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cw = _load("check_wiki")
gen = _load("gen_command_reference")


def _wiki(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    shutil.copytree(REAL_WIKI, root)
    (root / "CLI-Reference.md").write_text(gen.render(), encoding="utf-8")
    modules = "".join(f"- `wowlab_core.{m}`\n" for m in cw.public_modules())
    (root / "Library.md").write_text(f"# Library\n\n{modules}", encoding="utf-8")
    return root


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_control_baseline_passes(tmp_path: Path) -> None:
    assert [str(f) for f in cw.check(_wiki(tmp_path)).failures] == []


def test_control_the_pair_in_prose_is_flagged(tmp_path: Path) -> None:
    root = _wiki(tmp_path)
    _append(root / "FAQ.md", "\nThrall-Stormrage is my main.\n")
    out = [str(f) for f in cw.check(root).failures]
    assert out == ["FAQ.md:67: a Name-Realm pair (not quoted here; the log is public)"]


def test_a_page_named_after_a_name_realm_pair_is_flagged(tmp_path: Path) -> None:
    root = _wiki(tmp_path)
    (root / "Thrall-Stormrage.md").write_text(
        "# Notes\n\nThrall-Stormrage is my main.\n", encoding="utf-8"
    )
    _append(root / "_Sidebar.md", "\n[Thrall-Stormrage](Thrall-Stormrage)\n")
    assert cw.check(root).failures != []


def test_a_page_named_after_an_email_address_is_flagged(tmp_path: Path) -> None:
    root = _wiki(tmp_path)
    (root / "someone@example.org.md").write_text("# Notes\n\nHello.\n", encoding="utf-8")
    assert cw.check(root).failures != []
