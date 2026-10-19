# Probe from review of m14/04-looks-page-ui; reproduces the Looks tab leaving the previous page shown in its frame when a later "Write the page" fails (the run, or the read of /api/looks-page), while the status reports the failure.
"""The Looks tab (M14-04) on a second "Write the page".

The branch's own tests grade the first press only: a refused read shows
"no frame" (`test_a_refused_read_shows_the_servers_word_and_no_frame_constructed`)
and a failed run "reads nothing" with the frame hidden
(`test_a_failed_run_shows_the_cli_text_and_reads_nothing_constructed`).
`writeLooks` in `page.js` never hides the frame or clears its `srcdoc` on
the failure paths, so after one good press a second press that fails
leaves the earlier page drawn under an error status; when the run succeeds
and only the read is refused, the status line also says where the new page
was written, above the old one.

Positive control: the first press, which succeeds, shows the page.
Constructed (L8, boundary): the second press's run reply and route reply
are swapped in the Node harness the branch's tests use.
"""

# ruff: noqa: F811  (fixtures imported by name)

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_looks
from test_cli_looks import (
    no_install,  # noqa: F401  (autouse: discovery finds no install)
    source,  # noqa: F401  (autouse: recorded tables only)
    user_data,  # noqa: F401  (autouse: user data in tmp_path)
)
from test_ui_looks import _drive, _scenario, served  # noqa: F401

_LOOKS_TAB = "      } else if (step.looks_tab !== undefined) {\n"
_SWAP = (
    "      } else if (step.swap !== undefined) {\n"
    "        Object.assign(scenario, step.swap);\n"
    "        out.steps.push({ swapped: true });\n"
)


def _drive_with_swap(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    original = test_ui_looks._harness

    def harness() -> str:
        text = original()
        assert text.count(_LOOKS_TAB) == 1
        return text.replace(_LOOKS_TAB, _SWAP + _LOOKS_TAB, 1)

    test_ui_looks._harness = harness
    try:
        return _drive(folder, scenario)
    finally:
        test_ui_looks._harness = original


_FAILED_RUN = {
    "command": ["looks", "page"],
    "params": {},
    "reply": {
        "exit_code": 2,
        "timed_out": False,
        "truncated": False,
        "stdout": "",
        "stderr": "m14-04 probe constructed: the run failed",
    },
}


def test_a_refused_read_after_a_good_write_hides_the_old_page_constructed(
    served: Any, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [
        {"looks_write": True},
        {"swap": {"looks_page": {"ok": False, "body": {"error": "too_large"}}}},
        {"looks_write": True},
    ]
    scenario, page = _scenario(served, steps)
    out = _drive_with_swap(tmp_path / "node", scenario)
    first, _, second = out["steps"]
    # Positive control: the first press shows the page.
    assert first["srcdoc"] == page and first["frameHidden"] is False
    assert second["statusError"] is True and "too_large" in second["status"]
    assert second["frameHidden"] is True, "the earlier page is still shown under the error"


def test_a_failed_run_after_a_good_write_hides_the_old_page_constructed(
    served: Any, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [
        {"looks_write": True},
        {"swap": {"held": [_FAILED_RUN]}},
        {"looks_write": True},
    ]
    scenario, page = _scenario(served, steps)
    out = _drive_with_swap(tmp_path / "node", scenario)
    first, _, second = out["steps"]
    assert first["srcdoc"] == page and first["frameHidden"] is False
    assert second["statusError"] is True and "the run failed" in second["status"]
    assert second["whereHidden"] is True
    assert second["frameHidden"] is True, "the earlier page is still shown under the error"
