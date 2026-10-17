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

# How the child measures (M13-20T). On Linux `ru_maxrss` is kept across
# `execve` (getrusage(2)), so a child of a large pytest process starts at the
# parent's high-water mark and anything below it reads as no growth: this
# test then passed without measuring. The child reads its own `VmHWM` from
# `/proc/self/status` on Linux. The input is built so that nothing is freed
# before the parse (the old `a + b * n + c` freed two 16 MiB temporaries that
# were in `before` and that the parse could reuse unseen), and `body` is kept.
POSITION_CHILD = r"""
import resource, sys
from wowlab_core import luadata
size, parser = int(sys.argv[1]), sys.argv[2]
parse = luadata.parse
if parser == "line-starts":
    # CONSTRUCTED stand-in, a test of the probe itself: a parser that, on a
    # refusal, positions the error from a list of every line start, so it
    # holds memory in proportion to the lines before the error.
    def parse(data):
        try:
            return luadata.parse(data)
        except luadata.LuaDataError:
            starts = [i for i, byte in enumerate(data) if byte == 10]
            raise
body = b"\r\n\n" * (size // 3)
data = b"".join((b"\nX = 1", body, b"@\n"))
def peak():
    # This process's peak RSS in bytes. VmHWM on Linux is this process's
    # own, new at exec; `ru_maxrss` there is the parent's high-water mark.
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
        raise SystemExit("no VmHWM in /proc/self/status")
    scale = 1 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
before = peak()
try:
    parse(data)
except luadata.LuaDataError as err:
    after = peak()
    print(err.line, err.column, after - before)
"""
POSITION_SIZE = 16 * 1024 * 1024
POSITION_LIMIT = 64 * 1024 * 1024


def _position_readings(parser: str = "luadata") -> tuple[int, int, int]:
    """The refusal's line and column, and the child's RSS growth in bytes."""
    out = subprocess.run(
        [sys.executable, "-c", POSITION_CHILD, str(POSITION_SIZE), parser],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return int(out[0]), int(out[1]), int(out[2])


@pytest.mark.skipif(sys.platform == "win32", reason="uses the resource module")
def test_constructed_refusal_after_a_long_mixed_line_break_run_stays_small() -> None:
    """16 MiB of `\\r\\n\\n` (both pair orders, so no C-speed shortcut) and
    then a refused byte: the line count is right and positioning the error
    adds far less than the input's size (it once took about 1 GB here)."""
    line, column, grown = _position_readings()
    assert (line, column) == (2 + 2 * (POSITION_SIZE // 3), 1)
    assert grown < POSITION_LIMIT, f"positioning the error grew RSS by {grown} bytes"


@pytest.mark.skipif(sys.platform == "win32", reason="uses the resource module")
def test_constructed_stand_in_positioning_in_linear_memory_fails_the_refusal_probe() -> None:
    """The measurement above sees a refusal that holds memory per line.

    CONSTRUCTED stand-in, not `luadata`: it raises `luadata`'s own error but
    first lists every line start, 11.2 million offsets (about 430 MiB on
    macOS), the kind of positioning this probe was written against. The
    same child and limit must report it over the limit; under the inherited
    `ru_maxrss` of a large pytest parent it read as no growth and passed.
    """
    readings = []
    for _ in range(3):  # a loaded Mac can read low (pages compressed); one round must see it
        line, column, grown = _position_readings("line-starts")
        assert (line, column) == (2 + 2 * (POSITION_SIZE // 3), 1)
        readings.append(grown)
        if grown >= POSITION_LIMIT:
            return
    pytest.fail(
        f"a refusal holding a list of every line start grew RSS by only {readings} bytes; "
        "the probe cannot see it"
    )
