"""CONSTRUCTED hostile inputs (L8): top-level assignments count against the
`wowlab_core.luadata` cost budget (`docs/LAB_PLAN.md` §6.4, amendment of
2026-09-23; M10-04 fix round 2, security finding 1).

Before the budget counted them, `a=1\\n` repeated to `MAX_FILE_BYTES` was 67
million assignments and about 10.8 GB. Every input here is constructed, and
the test ids say so. No install, no fixture tree.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from wowlab_core import luadata

pytestmark = pytest.mark.parser


def test_constructed_repeated_assignments_past_the_budget_are_refused() -> None:
    """64 MiB of `a=1\\n` (16.8 million assignments, all identical, so each is
    shared whole) is inside `MAX_FILE_BYTES` but over `MAX_COST`: refused
    with a position on an assignment, not parsed."""
    data = b"\r\n" + b"a=1\n" * (16 * 1024 * 1024)
    with pytest.raises(luadata.LuaLimitError) as caught:
        luadata.parse(data)
    err = caught.value
    assert err.token.startswith(b"a=1")
    assert err.column == 1
    assert err.line > 2


@pytest.mark.parametrize(
    "item",
    [b"a=1\n", b"a = 'x'\n", b"a=nil\n", b"a={}\n"],
    ids=["constructed-number", "constructed-string", "constructed-nil", "constructed-table"],
)
def test_constructed_assignments_are_charged_like_entries(
    item: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the budget lowered, a run of top-level assignments crosses it at
    an assignment and raises; the first few fit."""
    monkeypatch.setattr(luadata, "MAX_COST", 20_000)
    fits = luadata.parse(b"\r\n" + item * 10)
    assert len(fits.assignments) == 10
    with pytest.raises(luadata.LuaLimitError) as caught:
        luadata.parse(b"\r\n" + item * 1000)
    assert caught.value.token.startswith(item[:1])
    assert caught.value.column == 1


def test_constructed_distinct_assignments_are_charged_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct assignments build their own objects, so fewer of them fit the
    same budget than identical ones."""
    monkeypatch.setattr(luadata, "MAX_COST", 100_000)

    def fitting(item: bytes | None) -> int:
        lines = [item or b"v%06d=%06d\n" % (i, i) for i in range(5000)]
        data = b"\r\n" + b"".join(lines)
        with pytest.raises(luadata.LuaLimitError) as caught:
            luadata.parse(data)
        return caught.value.line - 2

    assert fitting(None) < fitting(b"v000000=000000\n")


# ── escapes are charged (M10-04 fix round 3, security finding S1) ───────────


@pytest.mark.parametrize(
    "template",
    [b'X = {["%s"] = 1}', b'X = {"%s"}', b'X = "%s"'],
    ids=["constructed-escaped-key", "constructed-escaped-value", "constructed-escaped-top-level"],
)
def test_constructed_large_escaped_string_is_refused_by_the_budget(
    template: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every backslash in a string literal is charged: a string of 100 000
    `\\n` escapes is refused under a lowered budget (a key before it is
    decoded), and 1000 of them fit."""
    monkeypatch.setattr(luadata, "MAX_COST", 1_000_000)
    with pytest.raises(luadata.LuaLimitError) as caught:
        luadata.parse(template.replace(b"%s", b"\\n" * 100_000))
    assert "MAX_COST" in caught.value.message
    doc = luadata.parse(template.replace(b"%s", b"\\n" * 1000))
    assert doc.to_python()["X"] in ({"\n" * 1000: 1}, ["\n" * 1000], "\n" * 1000)


def test_constructed_escaped_value_decodes_to_the_right_bytes() -> None:
    """A 4 MiB value of simple, decimal and line-break escapes decodes to
    the bytes Lua 5.1 gives (`_unescape` builds its output at C speed and
    in linear memory)."""
    unit = b"a\\n\\065\\\\\\\r\n"
    count = 4 * 1024 * 1024 // len(unit)
    doc = luadata.parse(b'X = "' + unit * count + b'"')
    assert doc.assignments[0].value.data == b"a\nA\\\n" * count


# ── an error keeps a short token (security finding S2) ──────────────────────


@pytest.mark.parametrize(
    "data",
    [
        b"X = " + b"a" * (1024 * 1024),
        b'X = "' + b"a" * (1024 * 1024),
        b'X = "' + b"\\1" * (512 * 1024),
        b"X = {" + b"b" * (1024 * 1024) + b"}",
        b"X = 1" + b"a" * (1024 * 1024),
    ],
    ids=[
        "constructed-huge-bare-identifier",
        "constructed-huge-unterminated-string",
        "constructed-huge-unterminated-escapes",
        "constructed-huge-bare-identifier-in-table",
        "constructed-huge-malformed-number",
    ],
)
def test_constructed_error_keeps_at_most_forty_bytes_of_its_token(data: bytes) -> None:
    with pytest.raises(luadata.LuaDataError) as caught:
        luadata.parse(data)
    err = caught.value
    assert len(err.token) <= 40
    assert len(str(err)) < 400
    assert "-byte token" in err.message


# ── the error position is counted in constant memory (fix round 4, item 1) ──

POSITION_CHILD = r"""
import resource, sys
from wowlab_core import luadata
size = int(sys.argv[1])
data = b"\nX = 1" + b"\r\n\n" * (size // 3) + b"@\n"
scale = 1 if sys.platform == "darwin" else 1024
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
try:
    luadata.parse(data)
except luadata.LuaDataError as err:
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    print(err.line, err.column, after - before)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="uses the resource module")
def test_constructed_refusal_after_a_long_mixed_line_break_run_stays_small() -> None:
    """16 MiB of `\\r\\n\\n` (both pair orders, so no C-speed shortcut) and
    then a refused byte: the line count is right and positioning the error
    adds far less than the input's size (it once took about 1 GB here)."""
    size = 16 * 1024 * 1024
    out = subprocess.run(
        [sys.executable, "-c", POSITION_CHILD, str(size)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    line, column, grown = int(out[0]), int(out[1]), int(out[2])
    assert (line, column) == (2 + 2 * (size // 3), 1)
    assert grown < 64 * 1024 * 1024, f"positioning the error grew RSS by {grown} bytes"
