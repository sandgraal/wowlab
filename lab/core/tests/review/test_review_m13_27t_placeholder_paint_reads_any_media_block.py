# Probe from review of m13/27t-explorer-stage2-tests; reproduces the placeholder
# paint grader accepting a `.badge-placeholder` rule that only applies in print.
"""CONSTRUCTED CSS (L8: our own template text, not an external format).

`test_ui_explorer_stage2.py::_rules` flattens the body of every `@media`
block into the top-level rules, whatever the media query, and `_effective`
then lets a later rule win. So
`test_the_placeholder_badge_is_painted_like_its_label` passes on page.css
plus one rule inside `@media print` that paints `.badge.badge-placeholder`
like `.badge-no`: on screen, in either colour scheme, a placeholder on a
`gate` row is still painted gate gold, which is what M13-27T's "Placeholder
badge" bullet forbids. The same flattening means the dark scheme is never
graded on its own when a light block overrides it.

Cases (on a constructed badge sheet shaped like page.css):
- control: with no placeholder rule the badge is not painted like `no`; with
  the rule at the top level it is, and `_effective` says so;
- the probe: the same rule only under `@media print` must not read as
  painted like `no` (fails today).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]
if str(_TESTS) not in sys.path:  # the stage-2 file imports the first stage by name
    sys.path.insert(0, str(_TESTS))
_SPEC = importlib.util.spec_from_file_location(
    "_m13_27t_probe_stage2", _TESTS / "test_ui_explorer_stage2.py"
)
assert _SPEC and _SPEC.loader
stage2 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stage2)

_LEVELS = ["gate", "no", "folder", "not_applicable", "unclassified"]


def _painted_like_no(css: str) -> bool:
    as_no = stage2._effective({"badge", "badge-no"}, css)
    for level in _LEVELS:
        painted = stage2._effective({"badge", f"badge-{level}", "badge-placeholder"}, css)
        if not painted.get("color") or painted != as_no:
            return False
    return True


# A constructed badge sheet, so the probe does not move with page.css.
_BASE = """
.badge { color: #d7d7d7; background: #2a2a2a; border-color: #7a7a7a; }
.badge-gate { color: #ffdb75; background: #2e2104; border-color: #d4a838; }
.badge-no { color: #ff9a8c; background: #2c0907; border-color: #c4392b; }
.badge-unclassified { color: #dcdcdc; background: #333333; border-color: #8a8a8a; }
.badge-folder, .badge-not_applicable { color: #c4b9a3; background: #26221b; border-color: #5e5546; }
"""
_AS_NO = ".badge.badge-placeholder { color: #ff9a8c; background: #2c0907; border-color: #c4392b; }"


def test_control_a_top_level_rule_paints_the_placeholder_like_no() -> None:
    assert not _painted_like_no(_BASE), "no rule, no placeholder paint"
    assert _painted_like_no(_BASE + _AS_NO)


def test_a_print_only_rule_is_not_a_placeholder_painted_on_screen() -> None:
    css = _BASE + "@media print { " + _AS_NO + " }"
    assert not _painted_like_no(css), "a print-only rule passes the paint grader"
