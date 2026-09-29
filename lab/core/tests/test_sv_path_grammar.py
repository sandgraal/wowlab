"""Graders for one key-path grammar in `sv dump --path` and `sv merge --key` (M11-25T).

Written before the implementation (ADR-0013; `svmerge.py` is load-bearing,
owner 2026-09-29). Each grader of the missing behaviour carries one
`xfail(strict=True)` marker line, which M11-25 deletes and does not
otherwise edit. Tests without a marker pass on `main` already and pin what
the change must keep.

Where the expectations come from:

- `docs/LAB_PLAN.md` §6.11: `wowlab sv dump FILE [--json] [--path
  'Var.key[3].name']`.
- §13.4 and its M11-09T rulings: `--key SRC=DST`, "`SRC` and `DST` are paths
  in `sv dump --path` syntax"; "Paths are spelled as `wowlab sv dump`
  prints them: `WowLabCharDB["probe"]["loads"]`, with `[n]` for a number key
  or a positional entry"; `SvMergeReport` lists `conflicts`, `absent` and
  `taken`, each with a `path`.
- `docs/BACKLOG.md` M11-25: every printed path parses back to the same key
  (the round-trip option, chosen over documenting unaddressable keys in
  `--help`: every key the SavedVariables grammar admits is a NAME, a string,
  a number or a boolean, and each has a spelling the grammar can read back).
- `docs/LAB_FORMATS.md` §4.1 and its 2026-09-22 amendment: a key is
  `["string"]`, `[number]`, `[true]`/`[false]`, or a bare NAME; strings are
  byte strings, with Lua 5.1 escapes `\\a \\b \\f \\n \\r \\t \\v \\\\ \\" \\'`,
  decimal `\\ddd` up to 255 (one byte, at most three digits), and a
  backslash before a line break; `\\x`, `\\u{}`, `\\z`, `\\ddd` above 255 and
  any other character after a backslash are rejected. A quoted key in a
  path is read with the same rules, so a path spells a key exactly as the
  file may.

The round trip, as the owner uses it: take a path the tools print (`sv
dump` text lines; `sv merge` conflict, absent and taken lines, text and
`--json`), paste it into `--path` and into `--key`, and it names the same
Lua key (the same bytes for a string, the same number, the same boolean,
`[n]` and `[n.0]` alike as Lua compares them). Checked through the CLI
only: no test imports a private helper, and none assumes the name of the
shared parser the implementer creates.

What "pasted" requires of a printed path, derived from the above: it holds
no raw control character (C0 or DEL), so it stays on one line of the text
output and can be typed or pasted into a shell argument. A key holding such
a byte is printed with a Lua escape.

Real fixtures (L8): every SavedVariables file under `fixtures/macos/forever/`
(every key the corpus holds; none needs a Lua escape, see the report), and
the M11-03 `WowLab.lua` pair for `sv merge`. For speed, each real file is
round-tripped once per distinct printed key spelling (the last step of a
path), in the first context it occurs; the grammar reads a path step by
step, so this covers every key.

Constructed (labelled `constructed` in the test id): `PathGrammar.lua`, a
per-character file the corpus cannot supply, whose keys need quotes,
backslashes, control characters, `]]`, non-ASCII and non-UTF-8 bytes, digit
strings next to numbers (`["1"]` and `[1]`, `[2.0]`), and strings that are
not identifiers. Every leaf holds a unique integer, so a path that resolves
to the wrong key shows. The source character's copy holds the same keys
with other values (every leaf a two-way conflict) and one key the target
lacks. The install is the captured tree copied into `tmp_path` by
`test_cli.py`'s fixtures; the user data directory is redirected there and
the process table is a fake one. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    ACCOUNT,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401  (autouse: the store lives in tmp_path)
)

from wowlab_core import luadata

ACCT = f"WTF/Account/{ACCOUNT}"
CHAR_A = "Labchard-Labrealmg"  # the target (--into), M11-03
CHAR_B = "Labcharb-Labrealmf"  # the source (--from), M11-03
LAB = "WowLab.lua"
LAB_A = f"{ACCT}/1/{CHAR_A}/SavedVariables/{LAB}"
LAB_B = f"{ACCT}/1/{CHAR_B}/SavedVariables/{LAB}"
PG = "PathGrammar.lua"  # constructed
PG_A = f"{ACCT}/1/{CHAR_A}/SavedVariables/{PG}"
PG_B = f"{ACCT}/1/{CHAR_B}/SavedVariables/{PG}"

FIXTURE_FLAVOR = Path(__file__).resolve().parent / "fixtures" / "macos" / "forever"
REAL_SV = sorted(
    p.relative_to(FIXTURE_FLAVOR).as_posix()
    for p in (FIXTURE_FLAVOR / "WTF").rglob("*")
    if p.is_file()
    and (p.name.endswith(".lua") or p.name.endswith(".lua.bak"))
    and "SavedVariables" in p.parts
)

# ─── keys as Lua compares them ───────────────────────────────────────────────

# ("s", bytes) for a string or a bare NAME, ("n", float) for a number or a
# positional entry, ("b", bool) for a boolean.
KeyId = tuple[str, object]
# A node: the top-level variable, then the Lua key of every step under it.
Chain = tuple[str, tuple[KeyId, ...]]


def _s(data: bytes) -> KeyId:
    return ("s", data)


def _n(value: float) -> KeyId:
    return ("n", float(value))


def _key_id(entry: luadata.Entry, position: int | None) -> KeyId:
    if position is not None:
        return _n(position)
    key = entry.key
    if isinstance(key, str):
        return _s(key.encode("ascii"))
    if isinstance(key, luadata.LuaString):
        return _s(key.data)
    if isinstance(key, luadata.LuaNumber):
        return _n(key.as_float())
    if isinstance(key, luadata.LuaBool):
        return ("b", key.value)
    raise AssertionError(f"entry without a key: {entry.style}")


@dataclass(frozen=True)
class Node:
    chain: Chain
    first: int  # index of its first leaf in file order
    end: int  # one past its last leaf
    leaf: bool  # a scalar or an empty table: one `sv dump` line
    value: luadata.LuaValue
    unique: bool  # no duplicate key on the way (`sv dump --path` refuses those)


def _nodes(doc: luadata.LuaDocument) -> list[Node]:
    """Every value in the document, depth first in file order, with the leaf
    lines `sv dump` prints for it. Iterative, like the dump."""
    out: list[Node] = []
    leaves = 0
    heads = [a.name for a in doc.assignments]
    for assignment in doc.assignments:
        head_unique = heads.count(assignment.name) == 1
        # Open tables: their steps, value, uniqueness and index in `out`.
        stack: list[tuple[tuple[KeyId, ...], luadata.LuaValue, bool, int]] = []
        pending: list[tuple[tuple[KeyId, ...], luadata.LuaValue, bool]] = [
            ((), assignment.value, head_unique)
        ]
        while pending:
            steps, value, unique = pending.pop()
            while stack and not _is_prefix(stack[-1][0], steps):
                _close(out, stack.pop()[3], leaves)
            is_leaf = not isinstance(value, luadata.LuaTable) or not value.entries
            out.append(Node((assignment.name, steps), leaves, leaves + 1, is_leaf, value, unique))
            if is_leaf:
                leaves += 1
                continue
            assert isinstance(value, luadata.LuaTable)
            stack.append((steps, value, unique, len(out) - 1))
            position = 0
            children: list[tuple[tuple[KeyId, ...], luadata.LuaValue, bool]] = []
            ids: list[KeyId] = []
            for entry in value.entries:
                pos: int | None = None
                if entry.style is luadata.KeyStyle.POSITIONAL:
                    position += 1
                    pos = position
                ids.append(_key_id(entry, pos))
            for entry, kid in zip(value.entries, ids, strict=True):
                children.append(((*steps, kid), entry.value, unique and ids.count(kid) == 1))
            pending.extend(reversed(children))
        while stack:
            _close(out, stack.pop()[3], leaves)
    return out


def _is_prefix(parent: tuple[KeyId, ...], child: tuple[KeyId, ...]) -> bool:
    return len(parent) < len(child) and child[: len(parent)] == parent


def _close(out: list[Node], index: int, leaves: int) -> None:
    node = out[index]
    out[index] = Node(node.chain, node.first, leaves, node.leaf, node.value, node.unique)


# ─── reading printed paths back out of the output ───────────────────────────

_HEAD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NAME_STEP = re.compile(r"\.[A-Za-z_][A-Za-z0-9_]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _scan(text: str) -> tuple[str, list[str], str]:
    """(path, steps, rest): the longest key path at the start of `text`, read
    lexically as the grammar spells one (NAME, then `.NAME` or `[...]`, a
    quoted step ending at its unescaped closing quote). Only finds where a
    printed path ends; what it means is the CLI's business."""
    head = _HEAD.match(text)
    assert head is not None, f"no variable name at the start of {text!r}"
    pos = head.end()
    steps: list[str] = []
    while pos < len(text):
        if text[pos] == ".":
            m = _NAME_STEP.match(text, pos)
            if m is None:
                break
            steps.append(m.group())
            pos = m.end()
            continue
        if text[pos] != "[":
            break
        i = pos + 1
        while i < len(text) and text[i] == " ":
            i += 1
        if i < len(text) and text[i] in "\"'":
            quote = text[i]
            i += 1
            while i < len(text) and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
            i += 1
            while i < len(text) and text[i] == " ":
                i += 1
            if i >= len(text) or text[i] != "]":
                break
        else:
            close = text.find("]", i)
            if close < 0:
                break
            i = close
        steps.append(text[pos : i + 1])
        pos = i + 1
    return text[:pos], steps, text[pos:]


def _lines(stdout: str) -> list[str]:
    assert stdout.endswith("\n") or stdout == "", stdout[-200:]
    return stdout.split("\n")[:-1]


def _control(path: str) -> list[str]:
    return [hex(ord(c)) for c in _CONTROL.findall(path)]


# ─── the constructed document ───────────────────────────────────────────────

HEAD = "PathGrammarDB"
OTHER = "PathGrammarOther"

# (id, the key as the file writes it, the Lua key it loads as, whether a
# printed path must use a Lua escape for it). Bytes: Python `\\` is one Lua
# backslash; a Python `\t`, `\x01`, `\x7f`, `\xff` or `\n` is that raw byte.
TOP: list[tuple[str, bytes, KeyId, bool]] = [
    ("plain", b'["plain"]', _s(b"plain"), False),
    ("bare-name", b"bare_name", _s(b"bare_name"), False),
    ("space", b'["with space"]', _s(b"with space"), False),
    ("double-quote", b'["dq\\"inside"]', _s(b'dq"inside'), False),
    ("single-quoted", b"['sq\\'inside']", _s(b"sq'inside"), False),
    ("backslash", b'["back\\\\slash"]', _s(b"back\\slash"), False),
    ("trailing-backslash", b'["trailing\\\\"]', _s(b"trailing\\"), False),
    ("backslash-then-quote", b'["\\\\\\""]', _s(b'\\"'), False),
    ("close-long-bracket", b'["]]"]', _s(b"]]"), False),
    ("long-brackets", b'["a]]b[[c"]', _s(b"a]]b[[c"), False),
    ("close-bracket", b'["]"]', _s(b"]"), False),
    ("open-bracket", b'["["]', _s(b"["), False),
    ("utf8", '["é"]'.encode(), _s("é".encode()), False),
    ("utf8-cjk", '["日本"]'.encode(), _s("日本".encode()), False),
    ("leading-digit", b'["9lives"]', _s(b"9lives"), False),
    ("dash", b'["with-dash"]', _s(b"with-dash"), False),
    ("dot", b'["a.b"]', _s(b"a.b"), False),
    ("keyword", b'["end"]', _s(b"end"), False),
    ("empty-string", b'[""]', _s(b""), False),
    ("equals", b'["a=b"]', _s(b"a=b"), False),
    ("spaced-equals", b'[" = "]', _s(b" = "), False),
    ("quote-bracket-equals", b'["a\\"]=x"]', _s(b'a"]=x'), False),
    ("dashes", b'["--"]', _s(b"--"), False),
    ("single-quote", b'["\'"]', _s(b"'"), False),
    ("double-quote-in-single", b"['\"']", _s(b'"'), False),
    ("digit-string", b'["1"]', _s(b"1"), False),
    ("integer", b"[1]", _n(1), False),
    ("float-integer", b"[2.0]", _n(2), False),
    ("digit-string-2", b'["2"]', _s(b"2"), False),
    ("negative", b"[-3]", _n(-3), False),
    ("hex", b"[0x10]", _n(16), False),
    ("exponent", b"[1e3]", _n(1000), False),
    ("fraction", b"[0.5]", _n(0.5), False),
    ("string-true", b'["true"]', _s(b"true"), False),
    ("true", b"[true]", ("b", True), False),
    ("false", b"[false]", ("b", False), False),
    ("newline-escape", b'["nl\\nx"]', _s(b"nl\nx"), True),
    ("cr-escape", b'["cr\\rx"]', _s(b"cr\rx"), True),
    ("tab-escape", b'["tab\\tx"]', _s(b"tab\tx"), True),
    ("bell-backspace-formfeed-vtab", b'["\\a\\b\\f\\v"]', _s(b"\a\b\f\v"), True),
    ("decimal-control-escapes", b'["ctl\\001\\031\\127"]', _s(b"ctl\x01\x1f\x7f"), True),
    ("decimal-then-digit", b'["\\0067"]', _s(b"\x067"), True),
    ("nul-escape", b'["nul\\000x"]', _s(b"nul\x00x"), True),
    ("raw-tab", b'["raw\ttab"]', _s(b"raw\ttab"), True),
    ("raw-control-byte", b'["raw\x01ctl"]', _s(b"raw\x01ctl"), True),
    ("raw-del", b'["raw\x7fdel"]', _s(b"raw\x7fdel"), True),
    ("backslash-newline", b'["cont\\\nline"]', _s(b"cont\nline"), True),
    ("utf8-by-decimal-escapes", b'["\\195\\169scaped"]', _s("éscaped".encode()), True),
    ("invalid-utf8-by-escapes", b'["\\255\\254"]', _s(b"\xff\xfe"), True),
    ("invalid-utf8-raw", b'["raw\xffbyte"]', _s(b"raw\xffbyte"), True),
]
NESTED = _s(b'nested"tbl')
LIST = _s(b"list")
ONLY_OURS = _s(b"only\nours")
ONLY_THEIRS = _s(b"only\ttheirs")

# Nodes below the top level: (id, chain under HEAD, needs an escape).
DEEP: list[tuple[str, tuple[KeyId, ...], bool]] = [
    ("nested-table", (NESTED,), False),
    ("nested-backslash", (NESTED, _s(b"in\\ner")), False),
    ("nested-number", (NESTED, _n(3)), False),
    ("nested-newline", (NESTED, _s(b"in\nner")), True),
    ("positional", (LIST, _n(1)), False),
    ("positional-table", (LIST, _n(3)), False),
    ("positional-then-tab", (LIST, _n(3), _s(b"deep\t")), True),
    ("empty-table-newline", (_s(b"empty\n"),), True),
    ("absent-newline", (ONLY_OURS,), True),
]


def _document(values: int, extra: bytes) -> bytes:
    """Constructed: the client's layout (leading empty line, one entry per
    line, a comma after each, no indentation) with LF endings, since one key
    holds a backslash-newline. Leaf values are `values + n`, unique."""
    n = iter(range(1, 1000))
    lines = [b"", HEAD.encode() + b" = {"]
    for _id, source, _key, _escape in TOP:
        lines.append(source + b" = %d," % (values + next(n)))
    lines += [
        b'["nested\\"tbl"] = {',
        b'["in\\\\ner"] = %d,' % (values + 101),
        b'["in\\nner"] = %d,' % (values + 102),
        b"[3] = %d," % (values + 103),
        b"},",
        b"list = {",
        b"%d," % (values + 201),
        b"%d," % (values + 202),
        b"{",
        b'["deep\\t"] = %d,' % (values + 203),
        b"},",
        b"},",
        b'["empty\\n"] = {',
        b"},",
        extra,
        b"}",
        OTHER.encode() + b" = %d" % (values + 400),
        b"",
    ]
    return b"\n".join(lines)


OURS = _document(1000, b'["only\\nours"] = 1901,')  # constructed: the target
THEIRS = _document(2000, b'["only\\ttheirs"] = 2902,')  # constructed: the source

CASES: list[tuple[str, Chain, bool]] = [
    *[(i, (HEAD, (key,)), escape) for i, _source, key, escape in TOP],
    *[(i, (HEAD, chain), escape) for i, chain, escape in DEEP],
    ("top-level-scalar", (OTHER, ()), False),
]
PLAIN = [pytest.param(chain, id=f"constructed-{i}") for i, chain, escape in CASES if not escape]
ESCAPED = [pytest.param(chain, id=f"constructed-{i}") for i, chain, escape in CASES if escape]


def _by_chain(data: bytes) -> dict[Chain, Node]:
    nodes = _nodes(luadata.parse(data))
    return {node.chain: node for node in nodes}


def test_the_constructed_document_holds_every_case_constructed() -> None:
    """Self-check of the constructed input, not a grader: each case is one
    key, with no duplicate on its way, and every leaf value is unique."""
    ours, theirs = _by_chain(OURS), _by_chain(THEIRS)
    for _id, chain, _escape in CASES:
        assert chain in ours and ours[chain].unique, chain
    for chain in ours:
        if chain[1] and chain[1][0] == ONLY_OURS:
            assert chain not in theirs
        else:
            assert chain in theirs, chain
    assert (HEAD, (ONLY_THEIRS,)) in theirs
    numbers = [n.value.raw for n in ours.values() if isinstance(n.value, luadata.LuaNumber)]
    assert len(numbers) == len(set(numbers)) == len(TOP) + 7 + 1


# ─── sv dump: what it prints, and --path reading it back ────────────────────


def _leaf_text(node: Node) -> str:
    value = node.value
    if isinstance(value, luadata.LuaNumber):
        return value.raw
    assert isinstance(value, luadata.LuaTable) and not value.entries, node.chain
    return "{}"


def _dumped_constructed(file: Path, data: bytes) -> dict[Chain, str]:
    """The path `sv dump` prints for every node of a constructed document.
    Leaves are found by their unique values (so a printed path that spans
    lines is still found, and then fails the one-line check); a table's path
    is the printed prefix of its first leaf."""
    stdout = ok("sv", "dump", str(file)).stdout
    parts = re.split(r" = (-?[0-9]+|\{\})\n", stdout)
    assert parts[-1] == "", stdout[-200:]
    by_value = dict(zip(parts[1::2], parts[0:-1:2], strict=True))
    nodes = _nodes(luadata.parse(data))
    leaves = [n for n in nodes if n.leaf]
    printed: dict[Chain, str] = {}
    for node in leaves:
        printed[node.chain] = by_value[_leaf_text(node)]
    for node in nodes:
        if node.leaf:
            continue
        first = leaves[node.first]
        path, steps, _ = _scan(printed[first.chain])
        assert len(steps) == len(first.chain[1]), (path, first.chain)
        printed[node.chain] = first.chain[0] + "".join(steps[: len(node.chain[1])])
    return printed


def _expected_dump(printed: dict[Chain, str], nodes: dict[Chain, Node], chain: Chain) -> str:
    node = nodes[chain]
    leaves = sorted((n for n in nodes.values() if n.leaf), key=lambda n: n.first)
    return "".join(f"{printed[n.chain]} = {_leaf_text(n)}\n" for n in leaves[node.first : node.end])


def _dump_round_trip(file: Path, data: bytes, chain: Chain) -> list[str]:
    """What is wrong with the printed path of `chain`, pasted into --path."""
    nodes = _by_chain(data)
    printed = _dumped_constructed(file, data)
    path = printed[chain]
    problems: list[str] = []
    if bad := _control(path):
        problems.append(f"the printed path {path!r} holds raw control characters {bad}")
    others = [c for c, p in printed.items() if p == path and c != chain]
    if others:
        problems.append(f"{path!r} is printed for {others} as well")
    text = run("sv", "dump", str(file), "--path", path)
    expected = _expected_dump(printed, nodes, chain)
    if (text.exit_code, text.stdout) != (0, expected):
        problems.append(
            f"--path {path!r}: exit {text.exit_code}, {text.stdout!r} {text.stderr!r}; "
            f"expected {expected!r}"
        )
    as_json = run("sv", "dump", str(file), "--path", path, "--json")
    if as_json.exit_code != 0:
        problems.append(f"--path {path!r} --json: exit {as_json.exit_code} {as_json.stderr!r}")
    else:
        report = json.loads(as_json.stdout)
        top = report["values"][0]
        value = nodes[chain].value
        if isinstance(value, luadata.LuaNumber):
            want: dict[str, Any] = {"type": "number", "raw": value.raw}
        else:
            assert isinstance(value, luadata.LuaTable)
            want = {"type": "table", "entries": len(value.entries)}
        if (report["path"], top["value"]) != (path, want):
            problems.append(f"--path {path!r} --json: {report['path']!r}, {top['value']}")
    return problems


@pytest.mark.parametrize("chain", PLAIN)
def test_sv_dump_path_of_a_plain_key_reads_back_as_that_key(tmp_path: Path, chain: Chain) -> None:
    file = tmp_path / PG
    file.write_bytes(OURS)
    assert _dump_round_trip(file, OURS, chain) == []


@pytest.mark.xfail(strict=True, reason="M11-25 not implemented")
@pytest.mark.parametrize("chain", ESCAPED)
def test_sv_dump_path_of_a_key_needing_an_escape_reads_back_as_that_key(
    tmp_path: Path, chain: Chain
) -> None:
    """The printed path spells the key with Lua escapes, stays on one line,
    and `--path` reads it back to the same bytes."""
    file = tmp_path / PG
    file.write_bytes(OURS)
    assert _dump_round_trip(file, OURS, chain) == []


# ─── sv dump over every real SavedVariables fixture (L8) ────────────────────


@pytest.mark.parametrize("rel", REAL_SV, ids=[r.split(f"{ACCOUNT}/", 1)[1] for r in REAL_SV])
def test_sv_dump_real_fixture_every_printed_path_reads_back(flavor: Path, rel: str) -> None:
    """Every key of every real SavedVariables file: its printed path is one
    line, one step per key, and `--path` reads it back to the same subtree
    (the same `sv dump` lines). Run once per distinct printed step."""
    file = flavor / rel
    nodes = _nodes(luadata.read(file))
    leaves = [n for n in nodes if n.leaf]
    lines = _lines(ok("sv", "dump", str(file)).stdout)
    assert len(lines) == len(leaves), "one line per leaf"
    printed: dict[Chain, str] = {}
    last_step: dict[Chain, str] = {}
    for node, line in zip(leaves, lines, strict=True):
        path, steps, rest = _scan(line)
        assert rest.startswith(" = "), line
        assert _control(path) == [], line
        assert len(steps) == len(node.chain[1]), (line, node.chain)
        for depth in range(len(steps) + 1):
            chain = (node.chain[0], node.chain[1][:depth])
            printed.setdefault(chain, node.chain[0] + "".join(steps[:depth]))
            last_step.setdefault(chain, steps[depth - 1] if depth else node.chain[0])
    assert len(set(printed.values())) == len(printed), "one printed path per key"
    seen: set[str] = set()
    checked = 0
    for node in nodes:
        step = last_step[node.chain]
        if step in seen or not node.unique:
            continue
        seen.add(step)
        path = printed[node.chain]
        expected = "".join(line + "\n" for line in lines[node.first : node.end])
        result = run("sv", "dump", str(file), "--path", path)
        assert (result.exit_code, result.stdout) == (0, expected), (path, result.stderr)
        checked += 1
    assert checked == len(seen) > 0


# ─── sv merge: the paths it prints, pasted back into --key and --path ───────


def _pair(flavor: Path) -> None:
    """Constructed: the same file in both characters' SavedVariables/."""
    (flavor / PG_A).write_bytes(OURS)
    (flavor / PG_B).write_bytes(THEIRS)


def _merge(*extra: str) -> Any:
    return run("sv", "merge", PG, "--from", CHAR_B, "--into", CHAR_A, *extra)


def _key_copy(key: str, *, file: str = PG) -> Any:
    """Two-way `--key KEY` from the source character, with no answer at the
    prompt (end of input, which is not a yes): §13.4 copies a `--key` subtree
    whole, so the report lists what it would take, and nothing is written
    (exit 1, "Nothing was changed")."""
    return run("sv", "merge", file, "--from", CHAR_B, "--into", CHAR_A, "--key", key, "--json")


def _items(report: dict[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str]], int]:
    """(taken (path, value), absent (path, missing_from), conflicts)."""
    taken = [(t["path"], t["value"]) for t in report["taken"]]
    absent = [(a["path"], a["missing_from"]) for a in report["absent"]]
    return taken, absent, len(report["conflicts"])


def _report(result: Any) -> dict[str, Any]:
    report = json.loads(result.stdout)
    assert isinstance(report, dict)
    return report


def _dump_line(file: Path, path: str) -> tuple[int, str]:
    result = run("sv", "dump", str(file), "--path", path)
    return result.exit_code, result.stdout


def _conflict_problems(
    flavor: Path, conflict: dict[str, Any], printed: dict[Chain, str]
) -> list[str]:
    """One conflict's path: spelled as `sv dump` prints that key (§13.4),
    and pasted into --path (both sides) and --key it names that key."""
    ours, theirs = _by_chain(OURS), _by_chain(THEIRS)
    path = conflict["path"]
    (chain,) = [
        c
        for c, n in ours.items()
        if isinstance(n.value, luadata.LuaNumber) and n.value.raw == conflict["ours"]
    ]
    o, t = _leaf_text(ours[chain]), _leaf_text(theirs[chain])
    problems: list[str] = []
    if bad := _control(path):
        problems.append(f"{path!r} holds raw control characters {bad}")
    if path != printed[chain]:
        problems.append(f"{path!r} is not spelled as sv dump prints it: {printed[chain]!r}")
    if conflict["theirs"] != t:
        problems.append(f"{path!r}: theirs {conflict['theirs']!r}, the file holds {t}")
    for file, want in ((flavor / PG_A, o), (flavor / PG_B, t)):
        got = _dump_line(file, path)
        if got != (0, f"{path} = {want}\n"):
            problems.append(f"sv dump {file.parent.parent.name} --path {path!r}: {got}")
    limited = _key_copy(path)
    if limited.exit_code != 1 or not limited.stdout.strip():
        problems.append(f"--key {path!r}: exit {limited.exit_code} {limited.stderr!r}")
    else:
        got = _items(_report(limited))
        if got != ([(path, t)], [], 0):
            problems.append(f"--key {path!r} copied {got}")
    return problems


def _merge_round_trip(flavor: Path, *, escaped: bool) -> list[str]:
    _pair(flavor)
    printed = _dumped_constructed(flavor / PG_A, OURS)
    report = _merge("--json")
    assert report.exit_code == 1, (report.stdout, report.stderr)
    conflicts = _report(report)["conflicts"]
    ours = _by_chain(OURS)
    expected = {
        _leaf_text(n)
        for c, n in ours.items()
        if isinstance(n.value, luadata.LuaNumber) and c[1][:1] != (ONLY_OURS,)
    }
    assert {c["ours"] for c in conflicts} == expected, "every leaf on both sides differs"
    escape_chains = {chain for _i, chain, escape in CASES if escape}
    problems: list[str] = []
    for conflict in conflicts:
        (chain,) = [
            c
            for c, n in ours.items()
            if isinstance(n.value, luadata.LuaNumber) and n.value.raw == conflict["ours"]
        ]
        needs = any(
            chain[1][: d + 1] in {e[1] for e in escape_chains} for d in range(len(chain[1]))
        )
        if needs == escaped:
            problems += _conflict_problems(flavor, conflict, printed)
    return problems


def test_sv_merge_conflict_paths_of_plain_keys_read_back_constructed(flavor: Path) -> None:
    assert _merge_round_trip(flavor, escaped=False) == []


@pytest.mark.xfail(strict=True, reason="M11-25 not implemented")
def test_sv_merge_conflict_paths_of_keys_needing_an_escape_read_back_constructed(
    flavor: Path,
) -> None:
    """Every `conflicts[].path` in `--json` whose key needs a Lua escape: one
    line, spelled as `sv dump` prints the key, and it names that key again
    when pasted into `sv dump --path` on either side and into `--key`."""
    assert _merge_round_trip(flavor, escaped=True) == []


def _text_paths(stdout: str, marker: str) -> list[str]:
    """Paths from the indented report lines of `sv merge` text output that
    follow a path with `marker` (": ours " for a conflict, "  (missing from"
    for an absent key, " = " for a taken one)."""
    out: list[str] = []
    for line in stdout.split("\n"):
        if not line.startswith("    "):
            continue
        path, _steps, rest = _scan(line[4:])
        if rest.startswith(marker):
            out.append(path)
    return out


@pytest.mark.xfail(strict=True, reason="M11-25 not implemented")
def test_sv_merge_text_report_prints_the_same_paths_as_json_constructed(flavor: Path) -> None:
    """The text report lists every conflict and absent path on its own line,
    each exactly as `--json` prints it."""
    _pair(flavor)
    report = _report(_merge("--json"))
    text = _merge()
    assert text.exit_code == 1, text.stderr
    assert _text_paths(text.stdout, ": ours ") == [c["path"] for c in report["conflicts"]]
    assert _text_paths(text.stdout, "  (missing from ") == [a["path"] for a in report["absent"]]
    for path in [c["path"] for c in report["conflicts"]] + [a["path"] for a in report["absent"]]:
        assert _control(path) == [], path


@pytest.mark.xfail(strict=True, reason="M11-25 not implemented")
def test_sv_merge_absent_paths_read_back_on_the_side_that_has_them_constructed(
    flavor: Path,
) -> None:
    """A key on one side only: its printed path finds it with `--path` on
    that side, and `--key` with it names that key again: absent from theirs
    (nothing to copy), or copied whole from theirs into ours."""
    _pair(flavor)
    report = _report(_merge("--json"))
    absent = {a["missing_from"]: a["path"] for a in report["absent"]}
    assert set(absent) == {"ours", "theirs"} and len(report["absent"]) == 2
    for path in absent.values():
        assert _control(path) == [], path
    only_ours, only_theirs = absent["theirs"], absent["ours"]
    assert _dump_line(flavor / PG_A, only_ours) == (0, f"{only_ours} = 1901\n")
    assert _dump_line(flavor / PG_B, only_theirs) == (0, f"{only_theirs} = 2902\n")
    kept = _key_copy(only_ours)
    assert kept.exit_code == 0, kept.stderr
    assert _items(_report(kept)) == ([], [(only_ours, "theirs")], 0)
    copied = _key_copy(only_theirs)
    assert copied.exit_code == 1, copied.stderr
    assert _items(_report(copied)) == ([(only_theirs, "2902")], [], 0)
    assert (flavor / PG_A).read_bytes() == OURS, "declined: nothing written"


@pytest.mark.xfail(strict=True, reason="M11-25 not implemented")
@pytest.mark.parametrize("json_out", [True, False], ids=["json", "text"])
def test_sv_merge_taken_paths_read_back_after_the_write_constructed(
    flavor: Path, json_out: bool
) -> None:
    """`--take theirs` writes every conflict from theirs; each taken path,
    pasted into `sv dump --path` on the written target, finds theirs' value."""
    _pair(flavor)
    result = _merge("--take", "theirs", "--yes", *(["--json"] if json_out else []))
    assert result.exit_code == 0, (result.stdout, result.stderr)
    theirs = _by_chain(THEIRS)
    wanted = {
        _leaf_text(n)
        for c, n in theirs.items()
        if isinstance(n.value, luadata.LuaNumber) and c[1][:1] != (ONLY_THEIRS,)
    }
    if json_out:
        taken = [(t["path"], t["value"]) for t in _report(result)["taken"]]
    else:
        taken = []
        for line in result.stdout.split("\n"):
            if line.startswith("    "):
                path, _steps, rest = _scan(line[4:])
                if rest.startswith(" = "):
                    taken.append((path, rest[3:]))
    assert {value for _path, value in taken} == wanted
    for path, value in taken:
        assert _control(path) == [], path
        assert _dump_line(flavor / PG_A, path) == (0, f"{path} = {value}\n")


# ─── --key SRC=DST: printed paths and escapes on both sides ─────────────────


def _written_keys(flavor: Path) -> dict[Chain, Node]:
    return {n.chain: n for n in _nodes(luadata.read(flavor / PG_A))}


@pytest.mark.xfail(strict=True, reason="M11-25 not implemented")
@pytest.mark.parametrize(
    ("src", "dst", "dst_key"),
    [
        pytest.param(
            (HEAD, (NESTED,)),
            f'{HEAD}["copy\\n\\\\\\"\\001\\195\\169"]',
            _s(b'copy\n\\"\x01\xc3\xa9'),
            id="constructed-table-to-escaped-key",
        ),
        pytest.param(
            (HEAD, (_s(b"nl\nx"),)),
            f'{HEAD}["got\\tit\\255"]',
            _s(b"got\tit\xff"),
            id="constructed-escaped-key-to-escaped-key",
        ),
    ],
)
def test_key_copy_reads_escapes_on_both_sides_constructed(
    flavor: Path, src: Chain, dst: str, dst_key: KeyId
) -> None:
    """A copy within one file, `--key SRC=DST` with no `--from`: SRC is the
    path `sv dump` prints, DST is typed with Lua 5.1 escapes. The copy lands
    under the key DST spells, and every taken path reads back to it."""
    _pair(flavor)
    source = _dumped_constructed(flavor / PG_A, OURS)[src]
    result = run("sv", "merge", PG, "--into", CHAR_A, "--key", f"{source}={dst}", "--yes", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    written = _written_keys(flavor)
    copied = written.get((HEAD, (dst_key,)))
    assert copied is not None, f"no key {dst_key} after the copy: {sorted(map(str, written))}"
    before = _by_chain(OURS)[src].value
    assert _python(copied.value) == _python(before)
    taken = _report(result)["taken"]
    assert taken, "the copy is reported as taken"
    for item in taken:
        path = item["path"]
        assert _control(path) == [], path
        got = run("sv", "dump", str(flavor / PG_A), "--path", path, "--json")
        assert got.exit_code == 0, (path, got.stderr)


def _python(value: luadata.LuaValue) -> Any:
    if isinstance(value, luadata.LuaTable):
        pos = 0
        out: dict[Any, Any] = {}
        for entry in value.entries:
            p: int | None = None
            if entry.style is luadata.KeyStyle.POSITIONAL:
                pos += 1
                p = pos
            out[_key_id(entry, p)] = _python(entry.value)
        return out
    if isinstance(value, luadata.LuaNumber):
        return ("n", value.as_float())
    if isinstance(value, luadata.LuaString):
        return ("s", value.data)
    if isinstance(value, luadata.LuaBool):
        return ("b", value.value)
    return None


def test_key_copy_with_an_equals_sign_inside_a_quoted_key_constructed(flavor: Path) -> None:
    """`=` inside a quoted step is part of the key, not the SRC=DST split,
    on either side."""
    _pair(flavor)
    source = _dumped_constructed(flavor / PG_A, OURS)[(HEAD, (_s(b"a=b"),))]
    dst = f'{HEAD}["x = y"]'
    result = run("sv", "merge", PG, "--into", CHAR_A, "--key", f"{source}={dst}", "--yes", "--json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    copied = _written_keys(flavor).get((HEAD, (_s(b"x = y"),)))
    assert copied is not None and isinstance(copied.value, luadata.LuaNumber)
    assert copied.value.raw == _leaf_text(_by_chain(OURS)[(HEAD, (_s(b"a=b"),))])


# ─── --path and --key read one grammar ──────────────────────────────────────


def _resolved_by_both(flavor: Path, spelling: str) -> tuple[tuple[int, str], tuple[int, Any]]:
    """(sv dump --path on the target: exit, stdout) and (sv merge --key from
    the source, declined: exit, the values it would take), for one spelling."""
    dump = run("sv", "dump", str(flavor / PG_A), "--path", spelling)
    merge = _key_copy(spelling)
    taken: Any = None
    if merge.exit_code in (0, 1) and merge.stdout.strip():
        taken = sorted(t["value"] for t in _report(merge)["taken"])
    return (dump.exit_code, dump.stdout), (merge.exit_code, taken)


def _value(chain: Chain) -> str:
    return _leaf_text(_by_chain(OURS)[chain])


def _their_value(chain: Chain) -> str:
    return _leaf_text(_by_chain(THEIRS)[chain])


# Spellings both commands must read the same way, and already do: (spelling,
# the key it names, or None for one neither may read, exit 2).
SAME_GRAMMAR: list[tuple[str, Chain | None]] = [
    (f"{HEAD}.plain", (HEAD, (_s(b"plain"),))),
    (f'{HEAD}["plain"]', (HEAD, (_s(b"plain"),))),
    (f"{HEAD}['plain']", (HEAD, (_s(b"plain"),))),
    (f"{HEAD}.bare_name", (HEAD, (_s(b"bare_name"),))),
    (f'{HEAD}["bare_name"]', (HEAD, (_s(b"bare_name"),))),
    (f'{HEAD}["dq\\"inside"]', (HEAD, (_s(b'dq"inside'),))),
    (f"{HEAD}['dq\"inside']", (HEAD, (_s(b'dq"inside'),))),
    (f"{HEAD}['sq\\'inside']", (HEAD, (_s(b"sq'inside"),))),
    (f'{HEAD}["sq\'inside"]', (HEAD, (_s(b"sq'inside"),))),
    (f'{HEAD}["back\\\\slash"]', (HEAD, (_s(b"back\\slash"),))),
    (f'{HEAD}["a=b"]', (HEAD, (_s(b"a=b"),))),
    (f'{HEAD}["]]"]', (HEAD, (_s(b"]]"),))),
    (f'{HEAD}[""]', (HEAD, (_s(b""),))),
    (f'{HEAD}["é"]', (HEAD, (_s("é".encode()),))),
    (f'{HEAD}["1"]', (HEAD, (_s(b"1"),))),
    (f"{HEAD}[1]", (HEAD, (_n(1),))),
    (f"{HEAD}[1.0]", (HEAD, (_n(1),))),
    (f"{HEAD}[2]", (HEAD, (_n(2),))),
    (f"{HEAD}[2.0]", (HEAD, (_n(2),))),
    (f'{HEAD}["2"]', (HEAD, (_s(b"2"),))),
    (f"{HEAD}[-3]", (HEAD, (_n(-3),))),
    (f"{HEAD}[16]", (HEAD, (_n(16),))),
    (f"{HEAD}[0x10]", (HEAD, (_n(16),))),
    (f"{HEAD}[1000]", (HEAD, (_n(1000),))),
    (f"{HEAD}[1e3]", (HEAD, (_n(1000),))),
    (f"{HEAD}[0.5]", (HEAD, (_n(0.5),))),
    (f"{HEAD}[true]", (HEAD, (("b", True),))),
    (f'{HEAD}["true"]', (HEAD, (_s(b"true"),))),
    (f"{HEAD}[false]", (HEAD, (("b", False),))),
    (f'{HEAD}["nested\\"tbl"][3]', (HEAD, (NESTED, _n(3)))),
    (f"{HEAD}.list[2]", (HEAD, (LIST, _n(2)))),
    (f"{HEAD}..plain", None),
    (f"{HEAD}.", None),
    (f"{HEAD}[", None),
    (f'{HEAD}["plain"', None),
    (f'{HEAD}["plain]', None),
    (f"{HEAD}[plain]", None),
    (f'{HEAD}["plain"]x', None),
    (f"{HEAD}.9lives", None),
    (f"9{HEAD}", None),
    ('["plain"]', None),
    ("", None),
]


@pytest.mark.parametrize(
    ("spelling", "chain"),
    [pytest.param(s, c, id=f"constructed-{n}") for n, (s, c) in enumerate(SAME_GRAMMAR)],
)
def test_path_and_key_read_one_grammar_constructed(
    flavor: Path, spelling: str, chain: Chain | None
) -> None:
    """`sv dump --path X` and `sv merge --key X` accept the same X and name
    the same key with it (§13.4: `--key` paths are in `sv dump --path`
    syntax); what one refuses as unreadable, the other refuses too (exit 2)."""
    _pair(flavor)
    (dump_exit, dump_out), (merge_exit, merge_taken) = _resolved_by_both(flavor, spelling)
    if chain is None:
        assert (dump_exit, merge_exit) == (2, 2), (dump_out, merge_taken)
        return
    assert (dump_exit, dump_out) == (0, f"{spelling} = {_value(chain)}\n")
    assert (merge_exit, merge_taken) == (1, [_their_value(chain)])


# Spellings whose meaning comes from the Lua 5.1 escape rules
# (docs/LAB_FORMATS.md §4, 2026-09-22 amendment), which both commands read
# as `\X` → `X` on main: (spelling, the key it names, or None: refused).
ESCAPES: list[tuple[str, str, Chain | None]] = [
    ("newline", f'{HEAD}["nl\\nx"]', (HEAD, (_s(b"nl\nx"),))),
    ("cr", f'{HEAD}["cr\\rx"]', (HEAD, (_s(b"cr\rx"),))),
    ("tab", f'{HEAD}["tab\\tx"]', (HEAD, (_s(b"tab\tx"),))),
    ("abfv", f'{HEAD}["\\a\\b\\f\\v"]', (HEAD, (_s(b"\a\b\f\v"),))),
    ("decimal", f'{HEAD}["ctl\\1\\31\\127"]', (HEAD, (_s(b"ctl\x01\x1f\x7f"),))),
    ("decimal-three-digits", f'{HEAD}["\\0067"]', (HEAD, (_s(b"\x067"),))),
    ("decimal-letters", f'{HEAD}["\\112lain"]', (HEAD, (_s(b"plain"),))),
    ("nul", f'{HEAD}["nul\\0x"]', (HEAD, (_s(b"nul\x00x"),))),
    ("raw-tab-by-escape", f'{HEAD}["raw\\9tab"]', (HEAD, (_s(b"raw\ttab"),))),
    ("utf8-by-escapes", f'{HEAD}["\\195\\169scaped"]', (HEAD, (_s("éscaped".encode()),))),
    ("raw-utf8-by-escapes", f'{HEAD}["\\195\\169"]', (HEAD, (_s("é".encode()),))),
    ("invalid-utf8", f'{HEAD}["\\255\\254"]', (HEAD, (_s(b"\xff\xfe"),))),
    ("invalid-utf8-raw", f'{HEAD}["raw\\255byte"]', (HEAD, (_s(b"raw\xffbyte"),))),
    ("backslash-newline", f'{HEAD}["cont\\\nline"]', (HEAD, (_s(b"cont\nline"),))),
    ("nested", f'{HEAD}["nested\\"tbl"]["in\\nner"]', (HEAD, (NESTED, _s(b"in\nner")))),
    ("positional-then-tab", f'{HEAD}.list[3]["deep\\t"]', (HEAD, (LIST, _n(3), _s(b"deep\t")))),
    ("hex-escape", f'{HEAD}["\\x70lain"]', None),
    ("unicode-escape", f'{HEAD}["\\u{{70}}lain"]', None),
    ("z-escape", f'{HEAD}["\\zplain"]', None),
    ("decimal-over-255", f'{HEAD}["\\256"]', None),
    ("unknown-escape", f'{HEAD}["\\plain"]', None),
]


@pytest.mark.xfail(strict=True, reason="M11-25 not implemented")
@pytest.mark.parametrize(
    ("spelling", "chain"),
    [pytest.param(s, c, id=f"constructed-{i}") for i, s, c in ESCAPES],
)
def test_path_and_key_read_lua_escapes_constructed(
    flavor: Path, spelling: str, chain: Chain | None
) -> None:
    """A quoted step is a Lua 5.1 string literal as the file grammar reads
    it, in `--path` and `--key` alike: `\\ddd` is one byte (at most three
    digits, up to 255), `\\n` a newline, and `\\x`, `\\u{}`, `\\z`, `\\256`
    and an unknown escape are refused with exit 2, naming the option."""
    _pair(flavor)
    (dump_exit, dump_out), (merge_exit, merge_taken) = _resolved_by_both(flavor, spelling)
    if chain is None:
        assert (dump_exit, merge_exit) == (2, 2), (dump_out, merge_taken)
        dump = run("sv", "dump", str(flavor / PG_A), "--path", spelling)
        merge = _key_copy(spelling)
        assert isinstance(dump.exception, SystemExit) and isinstance(merge.exception, SystemExit)
        assert "--path" in dump.stderr and "--key" in merge.stderr
        return
    # The line starts with the spelling as given, which the text output may
    # make terminal-safe (a raw line break in it); the value names the key.
    assert dump_exit == 0 and dump_out.count("\n") == 1, dump_out
    assert dump_out.endswith(f" = {_value(chain)}\n"), dump_out
    assert (merge_exit, merge_taken) == (1, [_their_value(chain)])


# ─── the real pair: every path sv merge prints reads back (L8) ──────────────


# The lab-addon's probe: kept ours whatever `--key` says (owner ruling for
# M11-24, §13.4), so it is left out of the `--key` round trip here.
PROBE = 'WowLabCharDB["probe"]'


def test_sv_merge_real_pair_every_printed_path_reads_back(flavor: Path) -> None:
    """The M11-03 pair, two-way: each conflict and absent path (once per
    distinct last step) finds its value with `sv dump --path` on the side(s)
    holding it, and `--key` with it names that key alone (copied from theirs,
    or absent from theirs). Declined at the prompt: nothing is written."""
    result = run("sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--json")
    assert result.exit_code == 1, result.stderr
    report = _report(result)
    items = [(c["path"], c) for c in report["conflicts"]]
    items += [(a["path"], a) for a in report["absent"]]
    assert len(items) > 200
    seen: set[str] = set()
    for path, item in items:
        _path, steps, rest = _scan(path)
        assert rest == "" and _control(path) == [], path
        if steps[-1] in seen or path.startswith(PROBE):
            continue
        seen.add(steps[-1])
        sides = [(LAB_A, "ours"), (LAB_B, "theirs")]
        if "missing_from" in item:
            sides = [s for s in sides if s[1] != item["missing_from"]]
        for rel, side in sides:
            got = run("sv", "dump", str(flavor / rel), "--path", path)
            assert got.exit_code == 0, (path, got.stderr)
            if "missing_from" not in item and got.stdout.count("\n") == 1:
                assert got.stdout == f"{path} = {item[side]}\n"
        limited = _key_copy(path, file=LAB)
        assert limited.exit_code in (0, 1) and limited.stdout.strip(), (path, limited.stderr)
        taken, absent, conflicts = _items(_report(limited))
        if item.get("missing_from") == "theirs":
            assert (taken, absent, conflicts) == ([], [(path, "theirs")], 0)
        else:
            assert ([p for p, _value in taken], absent, conflicts) == ([path], [], 0)
            if "theirs" in item:
                assert taken[0][1] == item["theirs"]
