"""`_live_install`, the skip the `@live` tests share (M15-25): constructed
installs (`parser/_casc_build.make_install`), no real install read."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

from wowlab_core.install import ENV_ROOT

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _casc_build import FLAVOR, PRODUCT, VERSION, make_install, make_source_install
from _live_install import live_install, live_root, live_source

FACTS = {"product": PRODUCT, "flavor": FLAVOR, "version": VERSION}


def test_constructed_no_root_set_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ROOT, " ")
    with pytest.raises(pytest.skip.Exception, match=f"set {ENV_ROOT} to the install"):
        live_root("m2")


def test_constructed_the_fixtures_product_listed_is_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = make_install(tmp_path)
    monkeypatch.setenv(ENV_ROOT, str(root))
    assert live_install(FACTS, "m2").root == root


def test_constructed_a_product_with_no_row_skips_before_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flavor folder is on disk, but `.build.info` lists only another
    product: a leftover folder, so the test skips with the reason rather
    than failing in `casc.open_source`."""
    root, _ = make_install(tmp_path, rows=[("1", "wow_other")])
    monkeypatch.setenv(ENV_ROOT, str(root))
    with pytest.raises(pytest.skip.Exception, match="left over from a version no longer"):
        live_source(FACTS, "m2")


def _source_install(tmp_path: Path) -> Path:
    return make_source_install(
        tmp_path, stored=[], encoding_rows=[], root_blocks=[], rng=random.Random(25)
    )


def test_constructed_the_fixtures_build_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_ROOT, str(_source_install(tmp_path)))
    assert live_source(FACTS, "m2").report.index.version == VERSION


def test_constructed_another_build_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ROOT, str(_source_install(tmp_path)))
    with pytest.raises(pytest.skip.Exception, match=r"the fixture at 9\.9\.9\.1"):
        live_source({**FACTS, "version": "9.9.9.1"}, "m2")
