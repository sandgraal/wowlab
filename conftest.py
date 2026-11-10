"""Which tests run alone, after the parallel pass (M13-18), and which get a
longer hang guard (M13-81).

`make test` and CI run the suite in two passes. The first runs every test
not marked `serial` across all cores with pytest-xdist (`-n auto`). The
second runs the `serial` tests one at a time, without xdist, on a machine
the first pass no longer loads. Both passes keep `not live`, so together they
collect exactly what one serial run collects.

A test is `serial` when it measures the machine: an RSS target, a CPU-time
(or wall-clock) budget around a parse or a scan. Those numbers are calibrated
on an idle machine. With every core busy, CPU time also rises (a lower clock,
shared caches), and a budget can fail for the load rather than the code; and
the other workers' memory pressure can make the system compress pages, which
makes `ru_maxrss` read low, so an RSS target can pass when it should fail.
Tests that only bound allocations with `tracemalloc` stay parallel: that
counts the test process's own allocations and does not change with load.
Tests whose clock is a hang guard with a margin of seconds (a deadline that
must stop "within moments", a FIFO read that must not block) also stay
parallel. One test is serial for another reason, said where it is listed.

The marker is applied here, by node id, rather than in the test files:
review probes (`lab/core/tests/review/`) belong to code-reviewer, and the
graders of the load-bearing files are never edited by the session that
implements them (AGENTS.md). `tests/repo/test_serial_marking.py` checks every
entry below still names a file and a test that exist, so a rename cannot
quietly move a measurement into the parallel pass. It also reads every test
module and fails when one measures without being listed: a module that reads
RSS (`ru_maxrss`, `getrusage`, `VmHWM`) must be in SERIAL_FILES whole; a
module that reads a CPU clock (`cpu_clock()`, `process_time()`) outside its
test functions must be in SERIAL_FILES, and a test function that reads one
must be in SERIAL_TESTS if its file is not.

This file is also loaded by the hook tests under Python 3.9
(`make hooks-test`), so it keeps to 3.9 syntax and the standard library.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

# Every test in these files measures (node id prefix: the file).
SERIAL_FILES: frozenset[str] = frozenset(
    {
        # RSS of a child parsing a document at the bound (ru_maxrss).
        "lab/core/tests/review/test_m10_04_bounded_document_misses_memory_target.py",
        "lab/core/tests/review/test_m10_04_dense_array_misses_memory_target.py",
        # CPU time of a child parsing a document at MAX_COST.
        "lab/core/tests/review/test_m10_04_escaped_key_list_at_budget_misses_time_target.py",
        "lab/core/tests/review/test_m10_04_semicolon_list_at_budget_misses_time_target.py",
        # CPU-time budgets on hostile lines (tests/parser/_cpu_clock.py).
        "lab/core/tests/review/test_m10_06_toc_parse_quadratic_time.py",
        "lab/core/tests/review/test_m10_13_unclosed_quote_quadratic_time_target.py",
        # A module-level CPU clock, read by its scrub timing probe.
        "lab/core/tests/review/test_review_m10_02_followup_blank_runs.py",
        # RSS of a child refusing a long line-break run (ru_maxrss).
        "lab/core/tests/test_luadata_budget_constructed.py",
    }
)

# Single tests that measure, in files whose other tests do not (node id
# without the parameter suffix: every parametrization is included).
SERIAL_TESTS: frozenset[str] = frozenset(
    {
        # CPU-time budgets (tests/parser/_cpu_clock.py, or process_time directly).
        "lab/core/tests/parser/test_combatlog_constructed.py"
        "::test_an_unclosed_quote_over_a_megabyte_is_refused_in_linear_time_constructed",
        "lab/core/tests/parser/test_db2lake_constructed.py"
        "::test_constructed_a_header_wider_than_sqlite_allows_is_malformed_and_quick",
        "lab/core/tests/parser/test_db2lake_constructed.py"
        "::test_constructed_200k_rows_load_within_the_cpu_budget",
        "lab/core/tests/parser/test_luadata_constructed.py"
        "::test_multi_megabyte_document_parses_whole",
        "lab/core/tests/parser/test_toc_constructed.py"
        "::test_hostile_lines_parse_in_linear_time_constructed",
        "lab/core/tests/test_snap_gc_keep_rule.py"
        "::test_the_keep_rule_takes_exactly_seven_parts_and_stops_splitting_a_long_path_constructed",
        "tests/scripts/test_lab_capture_followup.py"
        "::test_constructed_many_short_lines_scrub_in_linear_time",
        "tests/scripts/test_lab_capture_followup3.py"
        "::test_constructed_unclosed_openers_do_not_rescan_the_file",
        "tests/scripts/test_lab_capture_followup6.py"
        "::test_constructed_long_separator_runs_scan_in_linear_time",
        "tests/scripts/test_lab_capture_round2.py"
        "::test_constructed_one_megabyte_run_scrubs_in_bounded_time",
        # CPU-time ratio of guard's no-op whole restore at 8,000 and 2,000 files.
        "lab/core/tests/test_guard_listing_memo.py"
        "::test_constructed_the_no_op_whole_restore_check_scales_linearly",
        # Wall clock against a bare walk of the same tree.
        "lab/core/tests/test_layout.py"
        "::test_400_addon_folders_inventory_under_two_seconds_constructed",
        # Not a measurement: it calls os.fork(), and an xdist worker has threads
        # (execnet's I/O thread), so Python 3.12 warns that the child may
        # deadlock. Alone, the process forking has one thread.
        "lab/core/tests/test_guard_pins_m10_16.py"
        "::test_constructed_pin_a_forked_child_leaving_the_with_neither_unlocks_nor_commits",
    }
)

# Tests that may legitimately run longer than the hang guard's 300 s
# (`timeout` in pyproject.toml, M13-81), by node id without the parameter
# suffix, with the seconds they get. None is listed: the slowest measured
# test took 255 s (a lab-addon review probe, on 4 cores at load average about
# 20; M13-81), 45 s inside the guard. An entry goes here, never into the test
# file, for the same reason as the serial list.
# `tests/repo/test_make_test_fast.py` checks that every entry names a test
# that exists.
TIMEOUT_OVERRIDES: dict[str, int] = {}

SERIAL = pytest.mark.serial


def is_serial(nodeid: str) -> bool:
    """Whether the test with this node id runs in the serial pass."""
    base = nodeid.split("[", 1)[0]
    return base.split("::", 1)[0] in SERIAL_FILES or base in SERIAL_TESTS


@pytest.hookimpl(tryfirst=True)  # before `-m` deselects by marker
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if is_serial(item.nodeid):
            item.add_marker(SERIAL)
        seconds = TIMEOUT_OVERRIDES.get(item.nodeid.split("[", 1)[0])
        if seconds is not None:
            item.add_marker(pytest.mark.timeout(seconds))


@pytest.fixture(autouse=True)
def _undo_monkeypatch_before_tmp_path_cleanup(request: pytest.FixtureRequest) -> Iterator[None]:
    """Undo a test's monkeypatching before pytest deletes its `tmp_path` (M13-81).

    With `tmp_path_retention_policy = "failed"` (pyproject.toml), pytest removes
    a passing test's `tmp_path` when that fixture is torn down. A test that
    asks for `monkeypatch` before `tmp_path` still has its patches in place
    then, so a patched `os` function (an `os.scandir` that takes no
    descriptor, in the M10-18T graders) breaks `shutil.rmtree` and the test
    errors at teardown. This fixture depends on both, so it is torn down
    first and undoes the patches; `monkeypatch`'s own teardown then has
    nothing left to undo. Fixtures the test sets up later are torn down
    before this one, with their patches still in place, as before. It edits
    no test file, for the same reason as the serial list.
    """
    if "tmp_path" in request.fixturenames and "monkeypatch" in request.fixturenames:
        request.getfixturevalue("tmp_path")
        patches = request.getfixturevalue("monkeypatch")
        yield
        patches.undo()
    else:
        yield
