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
parallel. Three tests are serial for other reasons, said where they are listed.

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

It also checks, for every test that has a `tmp_path`, that a test which
points the user's folders into that `tmp_path` really moved the user data
directory there (M18-14, `user_store_leaks` below).

This file is also loaded by the hook tests under Python 3.9
(`make hooks-test`), so it keeps to 3.9 syntax and the standard library.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

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
        # RSS of a child parsing a WTF document at its budget (VmHWM; M16-25).
        "lab/core/tests/parser/test_wtfconfig_budget_rss_constructed.py",
        # RSS of a child parsing two-line macro records at MACROS_BUDGET (VmHWM).
        "lab/core/tests/review/test_review_m16_25_macros_record_density.py",
        # RSS of a child decoding a 4096 x 4096 DXT5 of random blocks (M15-07).
        "lab/core/tests/parser/test_blp_peak_rss_constructed.py",
    }
)

# Single tests that measure, in files whose other tests do not (node id
# without the parameter suffix: every parametrization is included).
SERIAL_TESTS: frozenset[str] = frozenset(
    {
        # CPU-time budgets (tests/parser/_cpu_clock.py, or process_time directly).
        "tests/repo/test_crew_book_out_of_repo.py::test_constructed_the_scan_is_linear_in_the_input",
        "lab/core/tests/parser/test_combatlog_constructed.py"
        "::test_an_unclosed_quote_over_a_megabyte_is_refused_in_linear_time_constructed",
        "lab/core/tests/parser/test_db2lake_constructed.py"
        "::test_constructed_a_header_wider_than_sqlite_allows_is_malformed_and_quick",
        "lab/core/tests/parser/test_db2lake_constructed.py"
        "::test_constructed_200k_rows_load_within_the_cpu_budget",
        "lab/core/tests/parser/test_crewbook_constructed.py"
        "::test_constructed_five_thousand_players_read_within_the_budget",
        "lab/core/tests/parser/test_luadata_constructed.py"
        "::test_multi_megabyte_document_parses_whole",
        "lab/core/tests/parser/test_toc_constructed.py"
        "::test_hostile_lines_parse_in_linear_time_constructed",
        "lab/core/tests/parser/test_blp_constructed.py"
        "::test_a_1024_square_dxt1_decodes_within_half_a_second_constructed",
        "lab/core/tests/parser/test_compositor_constructed.py"
        "::test_a_2048x1024_composite_within_three_seconds_constructed",
        "lab/core/tests/parser/test_compositor_constructed.py"
        "::test_the_worst_layout_within_the_cost_models_bound_constructed",
        "lab/core/tests/test_snap_gc_keep_rule.py"
        "::test_the_keep_rule_takes_exactly_seven_parts_and_stops_splitting_a_long_path_constructed",
        "tests/scripts/test_lab_capture_followup.py"
        "::test_constructed_many_short_lines_scrub_in_linear_time",
        "tests/scripts/test_lab_capture_followup3.py"
        "::test_constructed_unclosed_openers_do_not_rescan_the_file",
        "tests/scripts/test_lab_capture_followup6.py"
        "::test_constructed_long_separator_runs_scan_in_linear_time",
        "tests/scripts/test_lab_capture_own_guids.py"
        "::test_constructed_hostile_link_text_scrubs_in_linear_time",
        "tests/scripts/test_lab_capture_own_guids.py"
        "::test_constructed_many_own_link_lines_scrub_in_linear_time",
        "tests/scripts/test_lab_capture_round2.py"
        "::test_constructed_one_megabyte_run_scrubs_in_bounded_time",
        # CPU-time growth of guard's no-op whole restore from 1,000 to 8,000 files.
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
        # Not a measurement: each starts a process named like the game client
        # (M15-18). Every process on the machine sees it, so a test in another
        # worker that asks the real probe would find the client running.
        "tests/scripts/test_perf_budget.py::test_constructed_the_pipeline_runs_on_a_small_tree",
        "tests/scripts/test_perf_budget.py"
        "::test_constructed_without_the_stub_the_gate_refuses_the_same_children",
    }
)

# Tests that may legitimately run longer than the hang guard's 300 s
# (`timeout` in pyproject.toml, M13-81), by node id without the parameter
# suffix, with the seconds they get. The slowest measured suite test took
# 255 s (a lab-addon review probe, on 4 cores at load average about 20;
# M13-81), 45 s inside the guard. An entry goes here, never into the test
# file, for the same reason as the serial list.
# `tests/repo/test_make_test_fast.py` checks that every entry names a test
# that exists.
TIMEOUT_OVERRIDES: dict[str, int] = {
    # The owner's `@live` L1 sweep (M16-38): it hashes a whole install and
    # runs every read command; never collected by `make test` or CI.
    "lab/core/tests/test_l1_sweep_live.py::test_live_l1_sweep_changes_nothing_in_the_install": 3600,
}

# The lab-addon grader mutation probes (M15-21): review probes that copy
# lab/addon/ into tmp_path, edit a Lua source, and run
# tests/addon/test_lab_addon.py against the copy. They read only the addon,
# that grader, wowlab_core.toc and three docs (a doc change can only make
# more grader tests fail, which cannot turn a probe red), and cost about a
# third of the suite's CPU time. Marked `addon_mutation` here, by node id
# prefix, for the same reason as the serial list. Owner decision,
# 2026-11-18: a pull request runs them only when one of the paths in ci.yml's
# `addon_probes` filter changes (the addon, its grader, toc.py, these files,
# this conftest, pyproject.toml, uv.lock); the nightly run (nightly.yml),
# `make test`, release.yml and the desktop suites run them always; `make
# test-fast` leaves them out with the rest of lab/core/tests/review/.
# `tests/repo/test_addon_mutation_marking.py` checks that this set is exactly
# the test files that load that grader, and that ci.yml's filter lists each.
ADDON_MUTATION_FILES: frozenset[str] = frozenset(
    {
        "lab/core/tests/review/test_m11_01_carry_check_misses_saved_subtables.py",
        "lab/core/tests/review/test_m11_01_line_based_privacy_tests_fail_open.py",
        "lab/core/tests/review/test_m11_01_pet_guid_check_misses_concatenated_lookup.py",
        "lab/core/tests/review/test_m11_13_stored_value_check_misses_call_stores.py",
        "lab/core/tests/review/test_m11_37_bidi_scan_skips_a_file_with_one_stray_byte.py",
        "lab/core/tests/review/test_m11_37_section_scan_accepts_a_client_call_in_carry.py",
        "lab/core/tests/review/test_m11_37_section_scan_misses_a_second_namespace_binding.py",
        "lab/core/tests/review/test_m11_37_section_scan_misses_client_function_handed_on.py",
        "lab/core/tests/review/test_m11_37_section_scan_misses_client_object_kept_in_a_file_local.py",
        "lab/core/tests/review/test_m11_37_section_scan_misses_ns_method_call_and_alias.py",
        "lab/core/tests/review/test_m11_37_section_scan_misses_self_and_client_given_parameters.py",
        "lab/core/tests/review/test_m13_05_combat_scan_accepts_a_combat_check_that_never_blocks.py",
        "lab/core/tests/review/test_m13_05_macro_pin_misses_the_name_stored_through_underscore.py",
        "lab/core/tests/review/test_m13_05_secret_scan_misses_a_cleaned_value_read_as_a_table_through_an_alias.py",
        "lab/core/tests/review/test_m13_05_secret_scan_misses_a_raw_value_not_from_a_named_handle.py",
        "lab/core/tests/review/test_m13_05_secret_scan_misses_a_rebound_is_secret.py",
        # M20-11 code review (#664): the once-per-match and 20,000 rules.
        "lab/core/tests/review/test_m20_11_hollow_once_and_limit_rules.py",
    }
)

SERIAL = pytest.mark.serial
ADDON_MUTATION = pytest.mark.addon_mutation


def is_serial(nodeid: str) -> bool:
    """Whether the test with this node id runs in the serial pass."""
    base = nodeid.split("[", 1)[0]
    return base.split("::", 1)[0] in SERIAL_FILES or base in SERIAL_TESTS


def is_addon_mutation(nodeid: str) -> bool:
    """Whether the test with this node id is a lab-addon grader mutation probe."""
    return nodeid.split("::", 1)[0] in ADDON_MUTATION_FILES


def pytest_configure(config: pytest.Config) -> None:
    # Registered here, with the list it marks, rather than in pyproject.toml.
    config.addinivalue_line(
        "markers",
        "addon_mutation: a lab-addon grader mutation probe; on a pull request only when "
        "the addon or its grader changes (conftest.py, M15-21)",
    )


@pytest.hookimpl(tryfirst=True)  # before `-m` deselects by marker
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if is_serial(item.nodeid):
            item.add_marker(SERIAL)
        if is_addon_mutation(item.nodeid):
            item.add_marker(ADDON_MUTATION)
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


# ─── the user data directory stays in the test's tmp_path (M18-14) ───────────

# platformdirs' own override variable for each Windows folder variable. On
# Windows platformdirs asks the system for the known folder and ignores the
# plain variables, so a test that sets only `LOCALAPPDATA` still writes into
# the runner's real store (`lab/core/tests/_user_dirs.py`).
WINDOWS_OVERRIDES: dict[str, str] = {
    "APPDATA": "WIN_PD_OVERRIDE_APPDATA",
    "LOCALAPPDATA": "WIN_PD_OVERRIDE_LOCAL_APPDATA",
}

# The variables that move the user data directory, Windows first, then the rest.
_DATA_DIR_VARIABLES: dict[bool, tuple[str, ...]] = {
    True: ("APPDATA", "LOCALAPPDATA"),
    False: ("HOME", "XDG_DATA_HOME"),
}


def _inside(value: str | Path, area: Path) -> bool:
    path = Path(os.path.realpath(value))
    return path == area or area in path.parents


def user_store_leaks(
    environ: Mapping[str, str], area: Path, data_dir: Path | None, *, windows: bool
) -> list[str]:
    """Why a test that redirected the user's folders into `area` does not
    have its user data directory there; empty when it does, or when the test
    redirected nothing into `area`.

    Two rules. On every platform, a plain Windows folder variable pointing
    into `area` needs its platformdirs override pointing into `area` too, so
    a fixture is caught on any machine, before Windows CI runs it. And when a
    variable that moves the data directory on this platform points into
    `area`, `data_dir` (`platformdirs.user_data_path("wowlab")` at that
    moment) must be inside `area`.
    """
    area = Path(os.path.realpath(area))
    leaks = []
    for plain, override in WINDOWS_OVERRIDES.items():
        value = environ.get(plain, "")
        if value and _inside(value, area):
            moved = environ.get(override, "")
            if not (moved and _inside(moved, area)):
                leaks.append(
                    f"{plain} points into tmp_path but {override} does not "
                    "(use _user_dirs.redirect_user_dirs)"
                )
    redirected = any(
        environ.get(name, "") and _inside(environ[name], area)
        for name in _DATA_DIR_VARIABLES[windows]
    )
    if redirected and data_dir is not None and not _inside(data_dir, area):
        leaks.append(f"the user data directory {data_dir} is outside tmp_path {area}")
    return leaks


def _user_data_dir() -> Path | None:
    try:
        import platformdirs
    except ImportError:  # the hook tests' Python 3.9 has no workspace packages
        return None
    return Path(platformdirs.user_data_path("wowlab"))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> None:
    """Fail a test whose redirected user data directory is not in its tmp_path.

    It runs after the test's fixtures are set up, so it sees what they set.
    """
    area = getattr(item, "funcargs", {}).get("tmp_path")
    if not isinstance(area, Path):
        return
    leaks = user_store_leaks(os.environ, area, _user_data_dir(), windows=sys.platform == "win32")
    if leaks:
        pytest.fail("the user data directory escapes tmp_path: " + "; ".join(leaks), pytrace=False)
