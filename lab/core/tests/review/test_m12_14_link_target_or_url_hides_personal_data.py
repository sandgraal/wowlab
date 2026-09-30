# Probe from review of m12/14-wiki-daily; reproduces personal data in a link target or URL escaping the privacy scan (and being quoted in the public report)
"""`check_wiki.check_privacy` blanks every URL and every internal link target
before it scans a line (`_URL.sub`, `_INTERNAL_TARGET.sub`). The link check
vouches only that a target resolves; it does not vouch that a target holds
no personal data. So:

- `[main](Thrall-Stormrage)` or `[x](Home#someone@example.org)`: the privacy
  scan never sees the value, and the link failure prints it verbatim. The
  report is `report/check.txt`, uploaded as a public artifact and pasted into
  the public `wiki-daily` issue; the pages artifact is uploaded too, because
  the "Gate result" step keeps it back only when a line says "not quoted
  here". The module docstring promises the opposite: "A failure that quotes
  personal data names only the page, the line and the kind of match".
- `https://github.com/search?q=someone@example.org` (a host the wiki already
  links to, so the new-host rule is silent): nothing fires and the page is
  pushed.

Positive control: the same values in prose are flagged by kind and never
quoted, and the baseline wiki passes.

Constructed: the real wiki fixture (d4344c8) plus the generated page and an
index naming every module; one invented line is appended per case. The
names and the address are invented.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

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

SECRETS = ("Thrall", "Stormrage", "someone", "example.org")


def _wiki(tmp_path: Path, extra_line: str | None) -> Path:
    root = tmp_path / "wiki"
    shutil.copytree(REAL_WIKI, root)
    (root / "CLI-Reference.md").write_text(gen.render(), encoding="utf-8")
    modules = "".join(f"- `wowlab_core.{m}`\n" for m in cw.public_modules())
    (root / "Library.md").write_text(f"# Library\n\n{modules}", encoding="utf-8")
    if extra_line is not None:
        faq = root / "FAQ.md"
        faq.write_text(faq.read_text(encoding="utf-8") + f"\n{extra_line}\n", encoding="utf-8")
    return root


def _failures(root: Path) -> list[str]:
    return [str(f) for f in cw.check(root).failures]


def test_control_baseline_passes(tmp_path: Path) -> None:
    assert _failures(_wiki(tmp_path, None)) == []


@pytest.mark.parametrize(
    "line",
    ["Ask Thrall-Stormrage about it.", "Write to someone@example.org about it."],
)
def test_control_the_same_values_in_prose_are_flagged_and_not_quoted(
    tmp_path: Path, line: str
) -> None:
    out = _failures(_wiki(tmp_path, line))
    assert len(out) == 1 and "not quoted here" in out[0]
    assert not any(s in f for s in SECRETS for f in out)


@pytest.mark.parametrize(
    "line",
    [
        "See [my main](Thrall-Stormrage).",
        "See [home](Home#someone@example.org).",
        "See https://github.com/search?q=someone@example.org for it.",
    ],
)
def test_personal_data_in_a_link_target_or_url_is_flagged_and_not_quoted(
    tmp_path: Path, line: str
) -> None:
    out = _failures(_wiki(tmp_path, line))
    assert any("not quoted here" in f for f in out), out
    assert not any(s in f for s in SECRETS for f in out), out
