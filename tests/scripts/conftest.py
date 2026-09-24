"""Shared set-up for the `scripts/lab_capture.py` suites.

A real run moves each combat log's timestamps and file-name stamp by that
log's own random, secret offset (`draw_time_shift`, docs/LAB_PLAN.md section
8, amendment 2026-09-24). Under test, every draw returns DEFAULT_OFFSET, a
fixed offset inside the real range, so every suite that captures a combat log
exercises the shift. A test of the draw itself or of per-log independence
replaces `draw_time_shift` again (test_lab_capture_followup5_round1.py).

Only the tests in ZERO_OFFSET are pinned to an offset of zero. Each of them
was written before the shift and asserts real timestamps, a real log file
name, or output byte-identical to its input or to an earlier run. They are
left unedited, and the shift they do not see is covered by
test_constructed_capture_changes_only_timestamp_identity_and_guid_spans.
"""

from __future__ import annotations

import pytest
from test_lab_capture import lab_capture

DEFAULT_OFFSET = 123 * 86400 + 4567  # invented; inside SHIFT_MIN..SHIFT_MAX

ZERO_OFFSET = {
    # Oracle: bytes outside the identity spans must equal the source, by offset.
    "test_lab_capture.py::test_bytes_outside_identity_spans_are_identical_by_offset",
    # Expects the log under its real file name and starting with its real timestamp.
    "test_lab_capture.py::test_capture_set_matches_the_runbook",
    # Maps each output back to its source by path, so needs the real log file name.
    "test_lab_capture.py::test_pseudonyms_are_stable_across_files_paths_and_flavors",
    # Looks the log up under its real file name.
    "test_lab_capture_followup3.py::"
    "test_constructed_combat_log_own_guid_from_the_command_line_allows_the_rewrite",
}


@pytest.fixture(autouse=True)
def fixed_time_shift(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    node = request.node.nodeid.split("tests/scripts/", 1)[-1].split("[", 1)[0]
    offset = 0 if node in ZERO_OFFSET else DEFAULT_OFFSET
    monkeypatch.setattr(lab_capture, "draw_time_shift", lambda: offset)
