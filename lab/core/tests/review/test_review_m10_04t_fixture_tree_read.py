# Probe from review of m10/04-luadata-parser-tests; reproduces a grader handing the
# committed fixture tree to `luadata.read()`, so a reader that writes beside its file pollutes it.
"""One defect found by running the M10-04T graders at fae43f0 against a mutant.

`test_real_savedvariables_read_equals_parse` calls `luadata.read(FIXTURES / name)`
on the committed corpus. A scratch `luadata` whose `read()` also wrote
`<file>.wlcache` beside its input was caught by the L1 grader (which reads a
copy in `tmp_path`), but it had already left five `.wlcache` files in
`lab/core/tests/fixtures/…/SavedVariables/`. They outlived the run: the next
`make test-parser`, with no mutant, failed
`test_lab_fixture_index.py::test_index_matches_files_on_disk` and
`test_layout_fixtures.py::test_every_inventoried_path_carries_its_row`.
Graders should hand the code under test a copy, as the L1 grader already does.

No `luadata` is needed: a spy stands in for the module, records the path its
`read()` receives, and stops the grader. The L1 grader is the positive control:
it passes a `tmp_path` copy today. Reads fixture paths only; writes only under
`tmp_path`; no install (ADR-0012).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

PARSER = Path(__file__).resolve().parents[1] / "parser"
if str(PARSER) not in sys.path:
    sys.path.insert(0, str(PARSER))

import test_luadata_fixtures as graders  # noqa: E402
from _luadata_oracle import FIXTURES, indexed  # noqa: E402

NAME = indexed("savedvariables")[0]


class _StopError(Exception):
    """Raised by the spy once it has seen the path."""


class _Spy:
    """Stands in for `wowlab_core.luadata`."""

    def __init__(self) -> None:
        self.paths: list[Path] = []

    def read(self, path: Any) -> Any:
        self.paths.append(Path(path).resolve())
        raise _StopError

    def parse(self, data: bytes) -> Any:
        raise _StopError


def _inside(path: Path, root: Path) -> bool:
    return path.is_relative_to(root.resolve())


def test_positive_control_l1_grader_reads_a_copy(tmp_path: Path) -> None:
    spy = _Spy()
    with pytest.raises(_StopError):
        graders.test_read_leaves_the_folder_untouched(spy, NAME, True, tmp_path)
    assert len(spy.paths) == 1
    assert _inside(spy.paths[0], tmp_path)
    assert not _inside(spy.paths[0], FIXTURES)


def test_read_equals_parse_grader_does_not_read_the_fixture_tree() -> None:
    spy = _Spy()
    with pytest.raises(_StopError):
        graders.test_real_savedvariables_read_equals_parse(spy, NAME)
    assert len(spy.paths) == 1
    assert not _inside(spy.paths[0], FIXTURES), (
        f"luadata.read() was handed {spy.paths[0]}, inside the committed fixture tree"
    )
