"""The install an `@live` test reads, or a skip saying why there is none
(M15-15, shared by M15-25).

A live test compares what the owner's install holds with a derived fixture
recorded from one product and build (`FACTS["product"]`, `["flavor"]`,
`["version"]`). It is skipped, never failed, when:

- `$WOWLAB_WOW_ROOT` is not set (`live_root`);
- the install's `.build.info` has no row for the fixture's product
  (`live_install`): the fixture's flavor folder is then left over from a
  version no longer installed, and opening it would refuse, as the 70450
  tests found when the beta closed;
- the flavor is at another build than the fixture's (`live_source`).

Any other refusal (two Active rows, a missing folder) is not caught and
fails the test. Everything here reads; nothing writes (L1).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import casc
from wowlab_core.install import ENV_ROOT, Install, discover


def live_root(what: str) -> Path:
    """`$WOWLAB_WOW_ROOT`, or a skip naming the tests (`what`) it runs."""
    value = os.environ.get(ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {ENV_ROOT} to the install to run the live {what} tests")
    return Path(value)


def live_install(facts: Mapping[str, Any], what: str) -> Install:
    """The install, or a skip when its `.build.info` has no row for the
    fixture's product: the fixture's flavor folder is then left over from a
    version no longer installed, so there is no build to compare."""
    found = discover(live_root(what))
    if not any((r.product or "").strip() == facts["product"] for r in found.products):
        pytest.skip(
            f".build.info has no row for the fixture's product {facts['product']!r}: "
            f"the folder {facts['flavor']} is left over from a version no longer installed"
        )
    return found


def live_source(facts: Mapping[str, Any], what: str) -> casc.CascSource:
    """The fixture's flavor opened read-only, or a skip when the install
    has none (`live_install`) or is at another build than the fixture's."""
    opened = casc.open_source(live_install(facts, what), facts["flavor"])
    if opened.report.index.version != facts["version"]:
        pytest.skip(
            f"the install is at {opened.report.index.version}; the fixture at {facts['version']}"
        )
    return opened
