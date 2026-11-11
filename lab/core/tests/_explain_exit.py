"""The exit code `wowlab explain` is expected to end with (M16-18).

`explain` exits 1 when the path matches nothing: it is outside the install,
or nothing is on disk under any reading of it, no file-map row matches it,
and no file or row goes by its name. Every other answer exits 0. A test
helper that runs `explain` on paths of both kinds compares the exit code
with `expected_exit`, read from what the run printed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

NOT_FOUND = "not found; nothing is at this path"
OUTSIDE = "not inside the install root"
# The headings `explain` prints above the candidates for a name.
CANDIDATE_HEADINGS = ("on disk by this name", "file-map rows for a file by this name")


def expected_exit(args: Sequence[str], stdout: str) -> int:
    """1 for an `explain` run whose output says the path matches nothing,
    else 0 (and 0 for any other command)."""
    if not args or args[0] != "explain":
        return 0
    if "--json" in args:
        report = json.loads(stdout)
        result = report["result"]
        nothing = (
            result["status"] == "unclassified"
            and (result["base"] is None or report["absent"])
            and not report.get("candidates")
        )
    else:
        first = stdout.splitlines()[0] if stdout else ""
        nothing = (NOT_FOUND in first or OUTSIDE in first) and not any(
            h in stdout for h in CANDIDATE_HEADINGS
        )
    return 1 if nothing else 0
