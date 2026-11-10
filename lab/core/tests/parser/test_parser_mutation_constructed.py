"""Seeded mutation test for `luadata` and the WTF parsers (M16-39T; L3, L4, L8).

Every mutant here is constructed (hostile input, L8): it starts from the
bytes of a real fixture in the index and has one to four byte-level
mutations applied by the stdlib `random`, with fixed seeds and a fixed
iteration count, so a run is the same on every machine and every Python.
The invariants (`docs/BACKLOG.md` M16-39T, from qa:F15):

- `luadata.parse` either returns a document or raises `LuaDataError` (its
  subclass `LuaLimitError` included). Nothing else escapes: no
  `RecursionError`, `IndexError`, `UnicodeDecodeError`, `OverflowError`,
  bare `ValueError` or anything else (AGENTS.md L3; `docs/LAB_PLAN.md` §6.4;
  `luadata.parse` docstring: "Bad input raises nothing else").
- When it returns, `serialize(parse(x)) == x` byte for byte (L4): a mutant
  that still parses is a document like any other, and the document model
  keeps it whole.
- The WTF parsers (`parse_config`, `parse_bindings`, `parse_macros`) accept
  any bytes and `doc.to_bytes() == x` (L4; `docs/LAB_FORMATS.md` §5-§7 and
  the `wtfconfig` module docstring: "For any input"). They raise nothing.

The seed corpus is every `savedvariables` row of the fixture index for
`luadata`, and every `config-wtf`, `bindings` and `macros` row for each WTF
parser; each parser also gets mutants of the other family's files, which
must be refused (luadata) or kept whole (WTF) just the same. A new capture
joins the corpus the day its index row lands.

Reproducing a failure: every mutant is a pure function of
`(fixture, seed, iteration)` (`mutant()` below); the failure message names
all three and the mutation trace, so

    from test_parser_mutation_constructed import mutant
    data, trace = mutant("<fixture path>", <seed>, <iteration>, "lua")

rebuilds the exact bytes. `WOWLAB_MUTATION_ROUNDS=<n>` multiplies the
iteration count for a deeper local run; `make test` and `make test-parser`
run with the default: about eight seconds of CPU in one process, spread
across the xdist workers by `make test`.
"""

from __future__ import annotations

import os
import random
from collections.abc import Callable

import pytest
from _luadata_oracle import FIXTURES, indexed

from wowlab_core import luadata
from wowlab_core.wtfconfig import (
    BindingsDocument,
    ConfigDocument,
    MacrosDocument,
    parse_bindings,
    parse_config,
    parse_macros,
)

pytestmark = pytest.mark.parser

# Fixed seeds: changing one changes every mutant, so treat them as part of
# the test. Three seeds, so a case that only one seed reaches still shows.
SEEDS = (20261109, 1639, 0x5EED)
# Mutants per (fixture, seed). The corpus is ~25 small files (the largest is
# 44 KB), so the whole module stays near a second of parse time.
ROUNDS = max(1, int(os.environ.get("WOWLAB_MUTATION_ROUNDS", "1")))
ITERATIONS = 40 * ROUNDS
# Mutants per fixture when a parser is fed the other family's files.
CROSS_ITERATIONS = 10 * ROUNDS
MAX_MUTATIONS = 4
# Failures listed per case; the count is always given in full.
SHOWN = 5

SAVEDVARIABLES = indexed("savedvariables")
WTF_KINDS = ("config-wtf", "bindings", "macros")
WTF_FILES = [name for kind in WTF_KINDS for name in indexed(kind)]

# Tokens that move a SavedVariables document across its grammar: structure,
# both quote kinds, escapes the grammar has and does not have, long brackets
# and comments, every line-break form, the keywords and the operators and
# call syntax L3 refuses, number spellings at and past the edges, bytes that
# are not UTF-8, and runs that reach the depth and number-length bounds.
LUA_TOKENS: tuple[bytes, ...] = (
    b"{",
    b"}",
    b"[",
    b"]",
    b"=",
    b",",
    b";",
    b'"',
    b"'",
    b"\\",
    b"\\\\",
    b'\\"',
    b"\\n",
    b"\\0",
    b"\\255",
    b"\\256",
    b"\\999",
    b"\\x41",
    b"\\z",
    b"\\u{41}",
    b"\\\n",
    b"\\\r\n",
    b"-",
    b"--",
    b"-- c\n",
    b"--[[",
    b"]]",
    b"--[==[ x ]==]",
    b"[[",
    b"[=[",
    b"]=]",
    b"\n",
    b"\r",
    b"\r\n",
    b"\n\r",
    b" ",
    b"\t",
    b"\x00",
    b"\x0b",
    b"\x0c",
    b"\xff",
    b"\xc3",
    b"\xef\xbb\xbf",
    b"nil",
    b"true",
    b"false",
    b"function",
    b"end",
    b"local",
    b"return",
    b"setmetatable",
    b"(",
    b")",
    b"..",
    b"...",
    b"+",
    b"*",
    b"#",
    b"~=",
    b":",
    b".",
    b"0x",
    b"0X1p4",
    b"0xg",
    b"1e",
    b"1e+",
    b"1e999",
    b"-1e999",
    b"1.",
    b".5",
    b"-0",
    b"00",
    b"inf",
    b"nan",
    b"9" * 400,
    b"9" * 4400,
    b"{" * 250,
    b"[" * 64,
    b"a" * 64,
    b"_G",
    b"x = ",
    b"[1] = ",
    b'["k"] = ',
    b"[true] = ",
)

# Tokens that move a WTF line across its grammars: the three line heads, the
# macro terminator, quotes, spaces, every break form, a lone CR, and bytes
# that are not UTF-8.
WTF_TOKENS: tuple[bytes, ...] = (
    b"SET ",
    b"SET a ",
    b'SET a "b"',
    b"set ",
    b"bind ",
    b"bind A B",
    b'bind A "B"',
    b'VER 1 0 "',
    b'VER 1 0 "n" "1"',
    b'VER 1 0 "n" "1"\n',
    b'" "',
    b"END",
    b"END\n",
    b"END\r\n",
    b"end",
    b'"',
    b" ",
    b"  ",
    b"\t",
    b"\n",
    b"\r",
    b"\r\n",
    b"\n\r",
    b"\r\r\n",
    b"\x00",
    b"\xff",
    b"\xc3",
    b"\xed\xa0\x80",
    b"\xef\xbb\xbf",
    b"#",
    b"-",
)

FAMILIES = {"lua": LUA_TOKENS, "wtf": WTF_TOKENS}


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# Trivia that keeps a well-formed document well-formed when it lands at a
# token boundary: these bias the mutants toward ones that still parse, so
# the round-trip invariant is exercised and not only the refusal one.
TRIVIA: tuple[bytes, ...] = (
    b" ",
    b"\t",
    b"\n",
    b"\r\n",
    b"\r",
    b"\n\r",
    b"-- c\n",
)
BOUNDARIES = b",;{}=\n"
DIGITS = b"0123456789"
OPS = (
    "flip",
    "delete",
    "insert",
    "insert",
    "duplicate",
    "truncate",
    "swap",
    "trivia",
    "trivia",
    "digit",
    "digit",
)


def _find(data: bytes, start: int, wanted: bytes) -> int:
    """The first index at or after `start` (wrapping) whose byte is in `wanted`, or -1."""
    n = len(data)
    for k in range(n):
        if data[(start + k) % n] in wanted:
            return (start + k) % n
    return -1


def _mutate_once(rng: random.Random, data: bytes, tokens: tuple[bytes, ...]) -> tuple[bytes, str]:
    """One mutation of `data`, and a short description of it."""
    n = len(data)
    op = "insert" if n == 0 else rng.choice(OPS)
    if op == "trivia":
        at = _find(data, rng.randrange(n), BOUNDARIES)
        token = rng.choice(TRIVIA)
        if at >= 0:
            return data[: at + 1] + token + data[at + 1 :], f"trivia@{at + 1}:{token!r}"
        op = "insert"
    if op == "digit":
        at = _find(data, rng.randrange(n), DIGITS)
        digit = rng.choice(DIGITS)
        if at >= 0 and rng.random() < 0.5:
            return data[:at] + bytes((digit,)) + data[at + 1 :], f"digit@{at}:{digit - 48}"
        if at >= 0:
            return data[:at] + bytes((digit,)) + data[at:], f"digit+@{at}:{digit - 48}"
        op = "insert"
    if op == "insert":
        at = rng.randint(0, n)
        token = rng.choice(tokens)
        shown = token[:16] + (b"..." if len(token) > 16 else b"")
        return data[:at] + token + data[at:], f"insert@{at}:{shown!r}"
    if op == "flip":
        at = rng.randrange(n)
        byte = rng.randrange(256)
        return data[:at] + bytes((byte,)) + data[at + 1 :], f"flip@{at}:{byte:#04x}"
    if op == "delete":
        at = rng.randrange(n)
        size = rng.randint(1, 16)
        return data[:at] + data[at + size :], f"delete@{at}+{size}"
    if op == "duplicate":
        start = rng.randrange(n)
        size = rng.randint(1, 64)
        piece = data[start : start + size]
        at = rng.randint(0, n)
        return data[:at] + piece + data[at:], f"duplicate@{start}+{size}->{at}"
    if op == "truncate":
        at = rng.randrange(n)
        return data[:at], f"truncate@{at}"
    # swap two short spans (left before right); reverse one when they overlap
    a, b = sorted((rng.randrange(n), rng.randrange(n)))
    size = rng.randint(1, 8)
    if b - a < size:
        return data[:a] + data[a : a + size][::-1] + data[a + size :], f"reverse@{a}+{size}"
    left, right = data[a : a + size], data[b : b + size]
    return data[:a] + right + data[a + size : b] + left + data[b + size :], f"swap@{a},{b}+{size}"


def mutant(fixture: str, seed: int, iteration: int, family: str) -> tuple[bytes, list[str]]:
    """The mutant for `(fixture, seed, iteration)`, and its mutation trace.

    A string seed is hashed with SHA-512 by `random.Random`, so the result
    is the same on every platform, every run and every supported Python.
    Half the mutants carry one mutation; the rest carry two to four.
    """
    rng = random.Random(f"M16-39T:{family}:{fixture}:{seed}:{iteration}")
    data = _read(fixture)
    trace = []
    count = 1 if rng.random() < 0.5 else rng.randint(2, MAX_MUTATIONS)
    for _ in range(count):
        data, step = _mutate_once(rng, data, FAMILIES[family])
        trace.append(step)
    return data, trace


def _report(failures: list[str], total: int, what: str) -> str:
    shown = "\n".join(failures[:SHOWN])
    more = f"\n... and {len(failures) - SHOWN} more" if len(failures) > SHOWN else ""
    return f"{len(failures)} of {total} mutants broke {what}:\n{shown}{more}"


def _cases(fixtures: list[str]) -> list[tuple[str, int]]:
    return [(name, seed) for name in fixtures for seed in SEEDS]


def _ids(cases: list[tuple[str, int]]) -> list[str]:
    return [f"constructed-{name}-seed{seed}" for name, seed in cases]


# ── the corpus is not empty ──────────────────────────────────────────────────


def test_the_mutation_corpus_is_the_indexed_real_fixtures() -> None:
    """The parametrized tests take their cases from the index; this keeps
    them from silently collecting nothing (L8: the seeds are real)."""
    assert len(SAVEDVARIABLES) >= 5, SAVEDVARIABLES
    for kind in WTF_KINDS:
        assert indexed(kind), f"no {kind} row in the fixture index"
    for name in SAVEDVARIABLES + WTF_FILES:
        assert (FIXTURES / name).is_file(), name


def test_mutants_are_deterministic_constructed() -> None:
    """The same `(fixture, seed, iteration)` gives the same bytes, and the
    mutator does change the input (a no-op mutator would pass everything)."""
    name = SAVEDVARIABLES[0]
    original = _read(name)
    first = [mutant(name, SEEDS[0], i, "lua") for i in range(ITERATIONS)]
    again = [mutant(name, SEEDS[0], i, "lua") for i in range(ITERATIONS)]
    assert first == again
    assert sum(data != original for data, _trace in first) >= ITERATIONS * 3 // 4


# ── luadata ──────────────────────────────────────────────────────────────────


def _lua_run(fixture: str, seed: int, family: str, iterations: int) -> tuple[list[str], int]:
    """The broken invariants over `iterations` mutants, and how many parsed."""
    failures = []
    parsed = 0
    for i in range(iterations):
        data, trace = mutant(fixture, seed, i, family)
        where = f"  {fixture} seed={seed} iteration={i} family={family} {trace}"
        try:
            doc = luadata.parse(data)
        except luadata.LuaDataError as exc:
            # The typed error carries its position (L3: rejected with a position).
            if not (isinstance(exc.line, int) and isinstance(exc.column, int)):
                failures.append(f"{where}: LuaDataError without an int line/column: {exc!r}")
            continue
        except Exception as exc:
            failures.append(f"{where}: parse raised {type(exc).__name__}: {exc!s:.200}")
            continue
        parsed += 1
        try:
            out = luadata.serialize(doc)
        except Exception as exc:
            failures.append(f"{where}: serialize raised {type(exc).__name__}: {exc!s:.200}")
            continue
        if out != data:
            at = next(
                (k for k, (x, y) in enumerate(zip(out, data, strict=False)) if x != y),
                min(len(out), len(data)),
            )
            failures.append(
                f"{where}: serialize(parse(x)) != x; first difference at byte {at}"
                f" (len {len(out)} vs {len(data)}): {out[at : at + 24]!r} vs {data[at : at + 24]!r}"
            )
    return failures, parsed


LUA_CASES = _cases(SAVEDVARIABLES)


@pytest.mark.parametrize(("fixture", "seed"), LUA_CASES, ids=_ids(LUA_CASES))
def test_luadata_mutants_raise_only_luadataerror_and_round_trip(fixture: str, seed: int) -> None:
    failures, _parsed = _lua_run(fixture, seed, "lua", ITERATIONS)
    assert not failures, _report(failures, ITERATIONS, "luadata")


def test_enough_luadata_mutants_parse_to_exercise_the_round_trip_constructed() -> None:
    """A mutator whose every mutant is refused would leave the round-trip
    invariant vacuous. On a mid-sized real file (3.7 KB), at least one mutant in
    ten still parses (about three in ten did when this test was written)."""
    name = next(n for n in SAVEDVARIABLES if n.endswith("/DBM-StatusBarTimers.lua"))
    parsed = sum(_lua_run(name, seed, "lua", ITERATIONS)[1] for seed in SEEDS)
    assert parsed * 10 >= ITERATIONS * len(SEEDS), parsed


# One seed for the cross-family case: WTF bytes are not Lua, so nearly all of
# these must be refused, and every refusal must be the typed one.
LUA_CROSS = [(name, SEEDS[0]) for name in WTF_FILES]


@pytest.mark.parametrize(("fixture", "seed"), LUA_CROSS, ids=_ids(LUA_CROSS))
def test_luadata_on_mutated_wtf_files_raises_only_luadataerror(fixture: str, seed: int) -> None:
    failures, _parsed = _lua_run(fixture, seed, "wtf", CROSS_ITERATIONS)
    assert not failures, _report(failures, CROSS_ITERATIONS, "luadata")


# ── the WTF parsers ──────────────────────────────────────────────────────────

WtfParser = Callable[[bytes], ConfigDocument | BindingsDocument | MacrosDocument]
WTF_PARSERS: dict[str, WtfParser] = {
    "config-wtf": parse_config,
    "bindings": parse_bindings,
    "macros": parse_macros,
}


def _wtf_failures(kind: str, fixture: str, seed: int, family: str, iterations: int) -> list[str]:
    parse = WTF_PARSERS[kind]
    failures = []
    for i in range(iterations):
        data, trace = mutant(fixture, seed, i, family)
        where = f"  {kind} {fixture} seed={seed} iteration={i} family={family} {trace}"
        try:
            out = parse(data).to_bytes()
        except Exception as exc:
            failures.append(f"{where}: raised {type(exc).__name__}: {exc!s:.200}")
            continue
        if out != data:
            failures.append(f"{where}: to_bytes() != input (len {len(out)} vs {len(data)})")
    return failures


# Every WTF parser on every WTF fixture: the grammars are total over bytes,
# so a `bindings` file through `parse_config` is as good a seed as its own.
WTF_CASES = [(kind, name, seed) for kind in WTF_PARSERS for name, seed in _cases(WTF_FILES)]


@pytest.mark.parametrize(
    ("kind", "fixture", "seed"),
    WTF_CASES,
    ids=[f"constructed-{kind}-{name}-seed{seed}" for kind, name, seed in WTF_CASES],
)
def test_wtf_mutants_parse_and_round_trip(kind: str, fixture: str, seed: int) -> None:
    failures = _wtf_failures(kind, fixture, seed, "wtf", ITERATIONS)
    assert not failures, _report(failures, ITERATIONS, kind)


# Mutated SavedVariables through the WTF parsers, one seed: Lua syntax, long
# lines, CRLF and every Lua token above, kept whole.
WTF_CROSS = [(kind, name, SEEDS[0]) for kind in WTF_PARSERS for name in SAVEDVARIABLES]


@pytest.mark.parametrize(
    ("kind", "fixture", "seed"),
    WTF_CROSS,
    ids=[f"constructed-{kind}-{name}-seed{seed}" for kind, name, seed in WTF_CROSS],
)
def test_wtf_parsers_keep_mutated_savedvariables_whole(kind: str, fixture: str, seed: int) -> None:
    failures = _wtf_failures(kind, fixture, seed, "lua", CROSS_ITERATIONS)
    assert not failures, _report(failures, CROSS_ITERATIONS, kind)
