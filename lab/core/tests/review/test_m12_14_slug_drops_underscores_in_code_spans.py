# Probe from review of m12/14-wiki-daily; reproduces check_wiki's slug() dropping underscores GitHub keeps (heading with a code span such as `_retail_`)
"""`check_wiki.slug` computes GitHub's anchor for a heading, and the link
check uses it to decide whether `Page#anchor` resolves. `_plain` removes
backticks first and then strips underscores at word edges as if they were
emphasis. Inside a code span an underscore is literal: GitHub renders
`## The `_retail_` folder` with the text "The _retail_ folder", and its
slugger (github-slugger, which keeps `_`, a connector punctuation character)
gives `the-_retail_-folder`. `slug` gives `the-retail-folder`.

So a correct link, `[x](#the-_retail_-folder)`, fails the gate (a whole run
is discarded), and a link that is broken on GitHub, `[x](#the-retail-folder)`,
passes. Flavor folders (`_retail_`, `_classic_`) are exactly what the wiki
names in code spans.

Positive control: an underscore between word characters outside a code span
(`snake_case`, not emphasis in GFM) is kept, and plain emphasis is still
dropped, as GitHub does.

Constructed: heading strings only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "scripts"


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


def test_control_plain_underscores_and_emphasis() -> None:
    assert cw.slug("The snake_case name") == "the-snake_case-name"
    assert cw.slug("An _emphasised_ word") == "an-emphasised-word"


def test_underscores_inside_a_code_span_are_kept() -> None:
    assert cw.slug("The `_retail_` folder") == "the-_retail_-folder"
    assert cw.slug("`__init__.py`") == "__init__py"
