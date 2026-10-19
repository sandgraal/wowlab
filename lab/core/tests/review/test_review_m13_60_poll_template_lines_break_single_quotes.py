# Probe from review of harness/m13-60-shepherd-cleanup-cloud; reproduces the
# poll-script template whose jq lines cannot be wrapped as printf '%s\n' '<line>'.
"""CONSTRUCTED (L8: our own harness text, not an external format).

`.claude/agents/pr-shepherd.md`, "Cloud sessions", **Waiting**, tells the
shepherd to make the poll script with one call,
`printf '%s\n' '<line>' '<line>' … > <scratchpad>/<ticket>/poll.sh`, and
lists the lines. Two of those lines (the `gh api … --jq '…'` ones) contain
single quotes, so wrapping them in single quotes as instructed does not give
printf one argument per line: the shell closes the quote at the jq filter,
the `|` inside it becomes a pipe and the `;` a command separator. Run from a
worktree-isolated agent the call was refused ("too complex to verify"), and
even outside the isolation it is a syntax error. The template is usable only
with each inner `'` written `'\\''`, which the definition never says.

Cases:
- control: a template line with no single quote survives the wrapping as
  exactly one argument, byte for byte;
- the probe: every template line survives it (fails today on the two jq
  lines).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SHEPHERD = ROOT / ".claude" / "agents" / "pr-shepherd.md"


def _template_lines() -> list[str]:
    text = SHEPHERD.read_text(encoding="utf-8")
    start = text.index("The lines:")
    end = text.index("- **Required or not.**", start)
    return re.findall(r"`([^`]*)`", text[start:end])


def _wrapped_as_instructed(lines: list[str]) -> list[str]:
    command = "printf '%s\\n' " + " ".join(f"'{line}'" for line in lines)
    return shlex.split(command)


def test_control_a_line_without_quotes_is_one_argument() -> None:
    lines = _template_lines()
    assert lines, "the template block was not found"
    plain = [line for line in lines if "'" not in line]
    assert plain, "the control needs at least one line without a quote"
    assert _wrapped_as_instructed(plain) == ["printf", "%s\\n", *plain]


@pytest.mark.parametrize("line", _template_lines())
def test_every_template_line_survives_the_single_quote_wrapping(line: str) -> None:
    assert _wrapped_as_instructed([line]) == ["printf", "%s\\n", line], (
        "wrapped in single quotes as the definition says, this line is not "
        "one printf argument; it needs its inner quotes escaped or removed"
    )
