# Probe from review of m10/02-lab-capture (fix round 1, 307c6cc); reproduces an
# identity string surviving, unreported, inside a "protected" TOC key or SET name.
"""Round-2 review probes for `scripts/lab_capture.py` (M10-02).

Constructed boundary input, invented names, no install (ADR-0012).

Fix round 1 made the CVar name of a `SET` line and the key of a TOC `## key:`
line immune to identity edits, so a character called "Interface" no longer
rewrites `## Interface:`. The same spans were also exempted from both
"surviving identity string" checks. `TOC_KEY_RE` takes everything between `##`
and the first colon as the key, and `SET_NAME_RE` takes any run of non-blank
bytes, so free text qualifies: the name is neither replaced nor reported, and
the file is written. Before the fix round these bytes were replaced.

Expected: the name is gone from the output, or the file is refused. One way:
only treat a span as vocabulary when it looks like vocabulary (a TOC key of
`[A-Za-z0-9-]+`, a CVar name of `[A-Za-z0-9_.]+`).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"
CHARACTER = "Thrallmar"
REALM = "Area 52"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_round2"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


@pytest.mark.parametrize(
    ("data", "flags"),
    [
        pytest.param(
            b"## Notes for Thrallmar: my bars\n", {"toc": True}, id="constructed-toc-free-text-key"
        ),
        pytest.param(
            b"## Thrallmar's bars, see http://example.invalid/x\n",
            {"toc": True},
            id="constructed-toc-comment-with-url-colon",
        ),
        pytest.param(
            b'SET Thrallmar_pos "1"\n', {"blank_cvars": True}, id="constructed-set-name-with-name"
        ),
    ],
)
def test_constructed_identity_in_a_protected_span_is_replaced_or_refused(
    data: bytes, flags: dict[str, bool]
) -> None:
    identity = lab_capture.Identity(characters=[CHARACTER], realms=[REALM])

    # Positive controls: the value side of the same formats is scrubbed, and a
    # real directive key that merely spells like a name is left alone.
    value = identity.scrub(b"## Title: Thrallmar UI\n", toc=True)
    assert value.data == b"## Title: Labchara UI\n" and not value.problems
    word = lab_capture.Identity(characters=["Interface"]).scrub(b"## Interface: 1\n", toc=True)
    assert word.data == b"## Interface: 1\n" and not word.problems

    result = identity.scrub(data, **flags)
    assert CHARACTER.encode() not in result.data or result.problems, (
        "the character name survived inside a protected span and the file was not refused"
    )
