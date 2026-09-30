# Probe from review of m12/14-wiki-daily; reproduces a new external host passing --base through a www. autolink or a protocol-relative link
"""With `--base`, `check_wiki` fails a page that links to a host the wiki at
the base does not link to ("a new external site is added by hand"). It finds
hosts with `_URL`, which needs `http://` or `https://`. GitHub renders two
other forms as links to an external site:

- `www.evil.example/a` in prose: GFM's extended autolink makes it a link to
  `http://www.evil.example/a`;
- `[x](//evil.example/a)`: a protocol-relative link, which the browser opens
  as `https://evil.example/a`; `_resolve` also skips it (`startswith("//")`).

Both pass the gate. The daily job pushes with no pull request, so the
new-host rule is the only thing between a prompt-injected link and the
public wiki.

Positive control: the same host as `https://evil.example/a` fails.

Constructed: the real wiki fixture (d4344c8), committed in `tmp_path` as the
base, plus the generated page and an index naming every module; the host is
under the reserved `.example` TLD.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
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


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=probe", "-c", "user.email=probe@invalid", *args],
        check=True,
        capture_output=True,
    )


def _wiki_with(tmp_path: Path, line: str | None) -> Path:
    root = tmp_path / "wiki"
    shutil.copytree(REAL_WIKI, root)
    (root / "CLI-Reference.md").write_text(gen.render(), encoding="utf-8")
    modules = "".join(f"- `wowlab_core.{m}`\n" for m in cw.public_modules())
    (root / "Library.md").write_text(f"# Library\n\n{modules}", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    if line is not None:
        faq = root / "FAQ.md"
        faq.write_text(faq.read_text(encoding="utf-8") + f"\n{line}\n", encoding="utf-8")
    return root


def _host_failures(root: Path) -> list[str]:
    return [str(f) for f in cw.check(root, "HEAD").failures if "evil.example" in f.message]


def test_control_baseline_passes(tmp_path: Path) -> None:
    assert [str(f) for f in cw.check(_wiki_with(tmp_path, None), "HEAD").failures] == []


def test_control_an_https_link_to_a_new_host_fails(tmp_path: Path) -> None:
    assert _host_failures(_wiki_with(tmp_path, "See [x](https://evil.example/a).")) != []


@pytest.mark.parametrize(
    "line",
    ["See www.evil.example/a for more.", "See [x](//evil.example/a)."],
)
def test_a_new_host_in_another_link_form_fails(tmp_path: Path, line: str) -> None:
    assert _host_failures(_wiki_with(tmp_path, line)) != []
