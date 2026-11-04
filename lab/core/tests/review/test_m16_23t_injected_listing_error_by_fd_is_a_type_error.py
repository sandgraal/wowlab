# Probe from review of m16/23-guard-listing-memo-tests; reproduces the listing seam's injected error turning into TypeError for a listing by descriptor
"""Constructed: an empty directory under tmp_path. The M16-23T seam says a
listing by descriptor is counted and can be made to fail; a failure armed for
a directory listed by fd must raise the injected error, as it does by path."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent.parent


def _memo_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_m16_23t_review_memo", _TESTS / "test_guard_listing_memo.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc/self/fd")
@pytest.mark.parametrize("by", ["path", "fd"])
def test_constructed_an_armed_failure_raises_the_injected_error(tmp_path: Path, by: str) -> None:
    m = _memo_module()
    (tmp_path / "SV").mkdir()
    listings = m.Listings(types.SimpleNamespace(path=tmp_path), "SV")
    fd = os.open(tmp_path / "SV", os.O_RDONLY)
    try:
        with pytest.MonkeyPatch.context() as patched:
            listings.arm(patched)
            listings.fail["SV"] = 1
            with pytest.raises(m.InjectedListingError):
                os.scandir(fd if by == "fd" else tmp_path / "SV")
    finally:
        os.close(fd)
    assert listings.calls["SV"] == 1
