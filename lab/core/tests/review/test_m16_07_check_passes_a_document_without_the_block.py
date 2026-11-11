# Probe from review of m16/07-network-disclosure; reproduces `gen_network_block.py --check` reporting "up to date" for a document whose block was deleted.
"""`scripts/gen_network_block.py --check` is documented as "exit 1 when a
copy is stale". A copy deleted outright (both markers gone) is the stalest
copy, but `render_doc` leaves a marker-less document unchanged, so `--check`
prints "up to date" and exits 0. The drift test catches it through its
`count(BEGIN) == 1` assertion; the generator, run alone, does not.
Constructed documents in `tmp_path` only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from wowlab_core import disclosure

REPO = Path(__file__).resolve().parents[4]


def _gen() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gen_network_block_probe", REPO / "scripts" / "gen_network_block.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_positive_control_a_stale_block_fails_check_constructed(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text(f"top\n{disclosure.BEGIN}\nold words\n{disclosure.END}\n", encoding="utf-8")
    gen = _gen()
    gen.DOCS = (doc,)
    assert gen.main(["--check"]) == 1


def test_a_document_that_lost_its_block_fails_check_constructed(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text("top\nthe block was deleted by hand\n", encoding="utf-8")
    gen = _gen()
    gen.DOCS = (doc,)
    assert gen.main(["--check"]) == 1
