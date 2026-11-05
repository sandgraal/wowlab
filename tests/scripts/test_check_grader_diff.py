"""`scripts/check_grader_diff.py`: graders change by marker deletion only (M16-40).

Most tests grade one change to one grader: a constructed base text (labelled
`CONSTRUCTED_*`: the shapes a grader's markers take, written for these
tests) and a head made from it by a named edit. A clean activation passes;
every weakening named in the ticket fails (an assertion changed, a test
deleted, `strict=False`, a `skip` added, a marker only partly removed), and
so do a few more. Others run the activation on the real graders on `main`,
and a last group runs the whole gate on a throwaway git repository built in
`tmp_path`, as CI does.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_grader_diff.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_grader_diff", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load()

# Constructed: a grader with every marker shape the gate accepts.
CONSTRUCTED_GRADER = '''\
"""Constructed grader for the M16-40 gate tests."""

import pytest

from wowlab_core import guard

PENDING = pytest.mark.xfail(strict=True, reason="M99-01 not implemented")


@pytest.mark.xfail(strict=True, reason="M99-01 not implemented")
def test_one() -> None:
    assert guard.answer() == 1


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="M99-01 not implemented",
)
def test_two() -> None:
    assert guard.answer() == 2


@PENDING
def test_three() -> None:
    assert guard.answer() == 3


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(
            4,
            marks=pytest.mark.xfail(strict=True, reason="M99-01 not implemented"),
        ),
        5,
    ],
)
def test_four(value: int) -> None:
    assert guard.answer() == value
'''

ONE_LINE = '@pytest.mark.xfail(strict=True, reason="M99-01 not implemented")\n'
MULTI_LINE = (
    "@pytest.mark.xfail(\n"
    "    strict=True,\n"
    "    raises=AssertionError,\n"
    '    reason="M99-01 not implemented",\n'
    ")\n"
)
ALIAS_USE = "@PENDING\n"
ALIAS_BINDING = 'PENDING = pytest.mark.xfail(strict=True, reason="M99-01 not implemented")\n'
PARAM_MARKS = '            marks=pytest.mark.xfail(strict=True, reason="M99-01 not implemented"),\n'


def _edit(old: str, new: str, text: str = CONSTRUCTED_GRADER) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def _check(head: str, base: str = CONSTRUCTED_GRADER) -> object:
    return gate.check_file(base.encode(), head.encode())


# ─── the marker grammar ──────────────────────────────────────────────────────


def test_constructed_grader_marker_blocks_are_found_whole() -> None:
    lines = gate.split_lines(CONSTRUCTED_GRADER)
    blocks = sorted(gate.find_markers(CONSTRUCTED_GRADER).blocks)
    texts = ["".join(lines[s:e]) for s, e in blocks]
    assert texts == [ALIAS_BINDING, ONE_LINE, MULTI_LINE, ALIAS_USE, PARAM_MARKS]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            '@pytest.mark.xfail(strict=False, reason="x")\ndef test_a(): ...\n', id="strict-false"
        ),
        pytest.param('@pytest.mark.xfail(reason="x")\ndef test_a(): ...\n', id="no-strict"),
        pytest.param('@pytest.mark.skip(reason="x")\ndef test_a(): ...\n', id="skip"),
        pytest.param(
            '@pytest.mark.xfail(strict=1, reason="x")\ndef test_a(): ...\n', id="strict-one"
        ),
        pytest.param(
            "import pytest\nx = 1; P = pytest.mark.xfail(strict=True)\n", id="shares-a-line"
        ),
        pytest.param(
            "import pytest\n@pytest.mark.xfail(strict=True)  # ok\n@pytest.mark.slow\n"
            "def test_a(): ...\n",
            id="comment-is-fine-but-other-marks-stay",
        ),
    ],
)
def test_constructed_non_markers_are_not_blocks(source: str) -> None:
    lines = gate.split_lines(source)
    texts = ["".join(lines[s:e]) for s, e in gate.find_markers(source).blocks]
    assert all("strict=True" in t and "slow" not in t for t in texts)
    if "# ok" not in source:
        assert texts == []


# ─── clean activations pass ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "removed",
    [
        pytest.param([ONE_LINE], id="one-line-decorator"),
        pytest.param([MULTI_LINE], id="multi-line-decorator"),
        pytest.param([ALIAS_USE], id="alias-decorator"),
        pytest.param([PARAM_MARKS], id="param-marks-keyword"),
        pytest.param([ALIAS_USE, ALIAS_BINDING], id="alias-and-binding"),
        pytest.param(
            [ONE_LINE, MULTI_LINE, ALIAS_USE, ALIAS_BINDING, PARAM_MARKS], id="every-marker"
        ),
    ],
)
def test_constructed_clean_marker_removal_passes(removed: list[str]) -> None:
    head = CONSTRUCTED_GRADER
    for block in removed:
        head = _edit(block, "", head)
    verdict = _check(head)
    assert verdict.ok, verdict.lines


def test_unchanged_grader_passes() -> None:
    assert _check(CONSTRUCTED_GRADER).ok


def test_constructed_crlf_grader_marker_removal_passes() -> None:
    base = CONSTRUCTED_GRADER.replace("\n", "\r\n")
    head = base.replace(MULTI_LINE.replace("\n", "\r\n"), "")
    verdict = _check(head, base)
    assert verdict.ok, verdict.lines


def test_constructed_removal_among_identical_neighbours_passes() -> None:
    # Two identical markers on adjacent tests: whichever a diff tool aligns,
    # removing one is a whole block.
    base = f"import pytest\n\n{ONE_LINE}def test_a(): ...\n\n{ONE_LINE}def test_b(): ...\n"
    head = base.replace(f"{ONE_LINE}def test_b", "def test_b")
    assert _check(head, base).ok


# ─── weakening diffs fail ────────────────────────────────────────────────────


WEAKENINGS = {
    "assertion-changed": (
        "    assert guard.answer() == 1\n",
        "    assert guard.answer() in (0, 1)\n",
    ),
    "assertion-deleted": ("    assert guard.answer() == 1\n", "    pass\n"),
    "test-deleted": (
        f"{ONE_LINE}def test_one() -> None:\n    assert guard.answer() == 1\n\n\n",
        "",
    ),
    "test-deleted-with-its-marker-left": (
        "@PENDING\ndef test_three() -> None:\n    assert guard.answer() == 3\n\n\n",
        "",
    ),
    "strict-false": (ONE_LINE, ONE_LINE.replace("strict=True", "strict=False")),
    "multi-line-strict-false": ("    strict=True,\n    raises", "    strict=False,\n    raises"),
    "alias-strict-false": (ALIAS_BINDING, ALIAS_BINDING.replace("strict=True", "strict=False")),
    "skip-added": ("def test_three()", '@pytest.mark.skip(reason="later")\ndef test_three()'),
    "skip-replaces-marker": (ONE_LINE, '@pytest.mark.skip(reason="later")\n'),
    "marker-partly-removed": ("@pytest.mark.xfail(\n    strict=True,\n", ""),
    "marker-closing-line-removed": ('    reason="M99-01 not implemented",\n)\n', ""),
    "param-dropped-with-its-marks": (
        f"        pytest.param(\n            4,\n{PARAM_MARKS}        ),\n",
        "",
    ),
    "marker-and-blank-line-removed": (f"\n\n{ONE_LINE}", "\n"),
    "line-added": (
        "def test_four(value: int) -> None:\n",
        "def test_four(value: int) -> None:\n    return\n",
    ),
    "trailing-newline-dropped": (
        "    assert guard.answer() == value\n",
        "    assert guard.answer() == value",
    ),
    "line-ending-changed": (
        "    assert guard.answer() == 2\n",
        "    assert guard.answer() == 2\r\n",
    ),
}


@pytest.mark.parametrize("name", sorted(WEAKENINGS))
def test_constructed_weakening_diff_fails(name: str) -> None:
    old, new = WEAKENINGS[name]
    verdict = _check(_edit(old, new))
    assert not verdict.ok, name
    assert any("hunk at base line" in line for line in verdict.lines), verdict.lines


def test_grader_deleted_fails() -> None:
    verdict = gate.check_file(CONSTRUCTED_GRADER.encode(), None)
    assert not verdict.ok
    assert verdict.lines == ["  the grader is deleted"]


def test_constructed_marker_removal_plus_assertion_change_fails() -> None:
    head = _edit(ONE_LINE, "")
    head = _edit("== 2\n", "in (1, 2)\n", head)
    verdict = _check(head)
    assert not verdict.ok
    text = "\n".join(verdict.lines)
    # The clean removal is not reported; the edit is.
    assert "in (1, 2)" in text
    assert "M99-01 not implemented" not in text


def test_constructed_unparseable_base_fails_closed() -> None:
    verdict = gate.check_file(b"def broken(:\n", b"def broken(:\n\n")
    assert not verdict.ok


def test_log_lines_cannot_start_a_workflow_command() -> None:
    # Constructed: a grader line that would be a GitHub workflow command if
    # printed bare at the start of a log line.
    head = _edit(
        "    assert guard.answer() == 1\n", "::error::injected\n    assert guard.answer() == 1\n"
    )
    verdict = _check(head)
    assert not verdict.ok
    assert not any(line.startswith("::") for line in verdict.lines)


# ─── the real graders on main ────────────────────────────────────────────────


def _tracked_tests() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return sorted(p for p in out.decode().split("\0") if p and gate.is_test_file(p))


REAL_MARKER = re.compile(r"^\s*(@|\w+\s*=\s*)pytest\.mark\.xfail\((?=.*strict=True)")
REAL_PENDING = [
    p
    for p in _tracked_tests()
    if p != "tests/scripts/test_check_grader_diff.py"
    and any(REAL_MARKER.match(line) for line in (REPO / p).read_text("utf-8").splitlines())
]


@pytest.mark.parametrize("path", REAL_PENDING)
def test_real_grader_full_activation_passes(path: str) -> None:
    """Deleting every strict-xfail marker line of a real grader is clean, and
    each marker the file applies is one of the blocks the gate knows."""
    text = (REPO / path).read_text("utf-8")
    lines = gate.split_lines(text)
    found = gate.find_markers(text)
    assert found.blocks, path
    removed = {i for s, e in found.blocks for i in range(s, e)}
    head = "".join(line for i, line in enumerate(lines) if i not in removed)
    verdict = gate.check_file(text.encode(), head.encode())
    assert verdict.ok, verdict.lines
    # Every line naming a strict xfail is inside a block the gate removes.
    for i, line in enumerate(lines):
        if REAL_MARKER.match(line):
            assert i in removed, f"{path}:{i + 1} is not a whole marker block"


@pytest.mark.parametrize("path", REAL_PENDING)
def test_real_pending_graders_count_on_any_load_bearing_change(path: str) -> None:
    # Whatever [TEST] graders are pending on main (the list shrinks as each
    # [IMPL] ticket activates its own), each counts as a grader on a change
    # to any load-bearing file, whatever it imports.
    data = (REPO / path).read_bytes()
    for module in ("guard", "luadata", "proposal"):
        assert gate.grader_reason(path, [data], {module}) is not None, (path, module)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("lab/core/src/wowlab_core/guard.py", "guard"),
        ("lab/core/src/wowlab_core/luadata.py", "luadata"),
        ("lab/core/src/wowlab_core/proposal.py", "proposal"),
        ("lab/core/src/wowlab_core/guard/__init__.py", "guard"),
        ("lab/core/src/wowlab_core/guardian.py", None),
        ("lab/core/src/wowlab_core/svmerge.py", None),
        ("lab/core/tests/test_guard.py", None),
    ],
)
def test_load_bearing_module(path: str, expected: str | None) -> None:
    assert gate.load_bearing_module(path) == expected


@pytest.mark.parametrize(
    ("path", "source", "reason"),
    [
        ("lab/core/tests/test_guard_x.py", "", "named for guard"),
        (
            "lab/core/tests/test_cli_x.py",
            "from wowlab_core import (\n    cli,\n    guard,\n)\n",
            "imports guard",
        ),
        ("lab/core/tests/test_cli_x.py", "import wowlab_core.guard as g\n", "imports guard"),
        ("lab/core/tests/test_cli_x.py", "from wowlab_core.guard import plan\n", "imports guard"),
        ("lab/core/tests/test_cli_x.py", "from wowlab_core import luadata\n", None),
        ("lab/core/tests/test_cli_x.py", "from wowlab_core import guardrails\n", None),
        (
            "lab/core/tests/test_cli_x.py",
            "import pytest\nX = pytest.mark.xfail(strict=True)\n",
            "carries",
        ),
        ("lab/core/tests/test_cli_x.py", "def broken(:\n", "does not parse"),
        ("lab/core/tests/helpers.py", "from wowlab_core import guard\n", None),
        ("lab/core/src/wowlab_core/test_guard.py", "", None),
        (
            "lab/core/tests/test_cli_x.py",
            "import wowlab_core\n\ndef test_a():\n    wowlab_core.guard.plan()\n",
            "imports guard",
        ),
        (
            "lab/core/tests/test_cli_x.py",
            "import wowlab_core as w\n\ndef test_a():\n    w.guard.plan()\n",
            "imports guard",
        ),
        (
            "lab/core/tests/test_cli_x.py",
            "import wowlab_core.cli\n\ndef test_a():\n    wowlab_core.guard.plan()\n",
            "imports guard",
        ),
        (
            "lab/core/tests/test_cli_x.py",
            "import wowlab_core\n\ndef test_a():\n    wowlab_core.cli.main()\n",
            None,
        ),
        (
            "lab/core/tests/test_cli_x.py",
            "import other as wowlab_core\n\ndef test_a():\n    wowlab_core.guard.plan()\n",
            None,
        ),
    ],
)
def test_constructed_grader_rule(path: str, source: str, reason: str | None) -> None:
    got = gate.grader_reason(path, [source.encode()], {"guard"})
    if reason is None:
        assert got is None
    else:
        assert got is not None and got.startswith(reason), got


# ─── the whole gate on a throwaway repository ────────────────────────────────


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "gate@example.invalid")
    _git(root, "config", "user.name", "gate")
    _git(root, "config", "commit.gpgsign", "false")
    lib = root / "lab/core/src/wowlab_core"
    lib.mkdir(parents=True)
    (lib / "guard.py").write_text("def answer() -> int:\n    return 0\n")
    (lib / "cli.py").write_text("X = 1\n")
    tests = root / "lab/core/tests"
    tests.mkdir(parents=True)
    (tests / "test_guard_constructed.py").write_text(CONSTRUCTED_GRADER)
    (tests / "test_cli_constructed.py").write_text("def test_cli() -> None:\n    assert True\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _commit_and_run(repo: Path, changes: dict[str, str | None]) -> tuple[int, str]:
    for rel, text in changes.items():
        path = repo / rel
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    code, lines = gate.run(repo, "HEAD^1", "HEAD")
    return code, "\n".join(lines)


GUARD_CHANGE = {"lab/core/src/wowlab_core/guard.py": "def answer() -> int:\n    return 1\n"}
GRADER = "lab/core/tests/test_guard_constructed.py"


def test_constructed_repo_clean_activation_passes(repo: Path) -> None:
    code, log = _commit_and_run(repo, {**GUARD_CHANGE, GRADER: _edit(ONE_LINE, "")})
    assert code == 0, log
    assert "ok   'lab/core/tests/test_guard_constructed.py'" in log


def test_constructed_repo_weakened_grader_fails(repo: Path) -> None:
    head = _edit(ONE_LINE, ONE_LINE.replace("strict=True", "strict=False"))
    code, log = _commit_and_run(repo, {**GUARD_CHANGE, GRADER: head})
    assert code == 1, log
    assert "FAIL 'lab/core/tests/test_guard_constructed.py'" in log


def test_constructed_repo_deleted_grader_fails(repo: Path) -> None:
    code, log = _commit_and_run(repo, {**GUARD_CHANGE, GRADER: None})
    assert code == 1, log
    assert "the grader is deleted" in log


def test_constructed_repo_renamed_grader_fails(repo: Path) -> None:
    moved = "lab/core/tests/test_moved_constructed.py"
    code, log = _commit_and_run(repo, {**GUARD_CHANGE, GRADER: None, moved: CONSTRUCTED_GRADER})
    assert code == 1, log


def test_constructed_repo_without_load_bearing_change_is_not_gated(repo: Path) -> None:
    head = _edit(ONE_LINE, ONE_LINE.replace("strict=True", "strict=False"))
    code, log = _commit_and_run(repo, {"lab/core/src/wowlab_core/cli.py": "X = 2\n", GRADER: head})
    assert code == 0, log
    assert "no load-bearing file changed" in log


def test_constructed_repo_non_grader_and_new_files_may_change(repo: Path) -> None:
    code, log = _commit_and_run(
        repo,
        {
            **GUARD_CHANGE,
            "lab/core/tests/test_cli_constructed.py": "def test_cli() -> None:\n    assert 1\n",
            "lab/core/tests/test_guard_new_constructed.py": "def test_new() -> None:\n    assert True\n",
        },
    )
    assert code == 0, log
    assert "graders changed: 0" in log


@pytest.mark.parametrize(
    ("rel", "text", "reason"),
    [
        (
            "lab/core/tests/conftest.py",
            "def pytest_configure(config): ...\n",
            "a conftest.py added",
        ),
        ("conftest.py", "", "a conftest.py added"),
        ("lab/core/tests/_oracle.py", "def expected():\n    return 3\n", "a test support file"),
        ("lab/core/tests/_oracle.py", None, "a test support file"),
        ("lab/core/tests/parser/conftest.py", "X = 2\n", "a test support file"),
        ("lab/core/tests/pytest.ini", "[pytest]\naddopts = -p no:x\n", "pytest configuration"),
        (
            "pyproject.toml",
            '[project]\nname = "x"\n\n[tool.pytest.ini_options]\naddopts = "-k not guard"\n',
            "pytest configuration",
        ),
        ("pyproject.toml", "[tool.pytest\n", "pytest configuration"),
        (
            "setup.cfg",
            "[metadata]\nname = x\n[tool:pytest]\naddopts = -x\n",
            "pytest configuration",
        ),
        ("tox.ini", "[tox]\nenvlist = a\n[pytest]\naddopts = -x\n", "pytest configuration"),
    ],
)
def test_constructed_repo_support_and_config_changes_fail(
    repo: Path, rel: str, text: str | None, reason: str
) -> None:
    base_files = {
        "lab/core/tests/_oracle.py": "def expected():\n    return 2\n",
        "lab/core/tests/parser/conftest.py": "X = 1\n",
        "pyproject.toml": '[project]\nname = "x"\n',
        "setup.cfg": "[metadata]\nname = x\n",
        "tox.ini": "[tox]\nenvlist = a\n",
    }
    for name, body in base_files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "support files")
    code, log = _commit_and_run(repo, {**GUARD_CHANGE, rel: text})
    assert code == 1, log
    assert f"FAIL {rel!r} ({reason}" in log, log


@pytest.mark.parametrize(
    ("rel", "base", "head"),
    [
        pytest.param(
            "pyproject.toml",
            '[project]\nname = "x"\n',
            '[project]\nname = "y"\n',
            id="pyproject-other-table",
        ),
        pytest.param(
            "setup.cfg",
            "[metadata]\nname = x\n",
            "[metadata]\nname = y\n",
            id="setup-cfg-other-section",
        ),
        pytest.param(
            "tox.ini", "[tox]\nenvlist = a\n", "[tox]\nenvlist = b\n", id="tox-other-section"
        ),
        pytest.param("lab/core/tests/_new_helper.py", None, "X = 1\n", id="new-helper"),
        pytest.param("lab/core/tests/fixtures/data.txt", "a\n", "b\n", id="non-python"),
    ],
)
def test_constructed_support_changes_that_are_allowed(
    rel: str, base: str | None, head: str
) -> None:
    got = gate.support_reason(rel, None if base is None else base.encode(), head.encode())
    assert got is None


def test_constructed_repo_cli_exit_codes(repo: Path) -> None:
    _commit_and_run(repo, {**GUARD_CHANGE, GRADER: _edit("== 1\n", "== 0\n")})
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--repo", str(repo)], capture_output=True, text=True
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    bad = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--repo", str(repo), "--base", "no-such-rev"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2


# ─── the CI job ──────────────────────────────────────────────────────────────


def test_ci_runs_the_gate_from_the_base_revision() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text("utf-8"))
    job = workflow["jobs"]["grader-diff"]
    assert job["name"] == "Graders activated by marker deletion only"
    assert job["permissions"] == {"contents": "read"}
    checkout, step = job["steps"]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 2}
    run = step["run"]
    assert "git show HEAD^1:scripts/check_grader_diff.py" in run
    assert 'python3 -I "$script" --repo . --base HEAD^1 --head HEAD' in run
    assert "${{" not in run
    assert job["timeout-minutes"] == 10


# ─── alias bindings, marks= scope, and the log's bounds ──────────────────────

ALIAS_BASE = "import pytest\nCHECK = pytest.mark.xfail(strict=True)\n"
ALIAS_TEST = "@CHECK\ndef test_a(x=1):\n    assert x\n"


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param("def helper(CHECK):\n    return CHECK\n", id="argument"),
        pytest.param("for CHECK in []:\n    pass\n", id="for-target"),
        pytest.param("from os import sep as CHECK\n", id="import-alias"),
        pytest.param("def CHECK():\n    pass\n", id="def-name"),
        pytest.param("class CHECK:\n    pass\n", id="class-name"),
        pytest.param("try:\n    pass\nexcept Exception as CHECK:\n    pass\n", id="except-name"),
        pytest.param("CHECK += 1\n", id="augmented"),
        pytest.param("del CHECK\n", id="delete"),
        pytest.param("y = [CHECK := 1]\n", id="walrus"),
    ],
)
def test_constructed_alias_with_a_second_binding_is_not_a_marker(extra: str) -> None:
    base = ALIAS_BASE + extra + ALIAS_TEST
    assert not gate.check_file(base.encode(), base.replace("@CHECK\n", "").encode()).ok


def test_constructed_marks_keyword_outside_param_is_not_a_marker() -> None:
    base = (
        "import pytest\n\n"
        "def test_a():\n"
        "    run(\n"
        "        marks=pytest.mark.xfail(strict=True),\n"
        "    )\n"
    )
    head = base.replace("        marks=pytest.mark.xfail(strict=True),\n", "")
    assert not gate.check_file(base.encode(), head.encode()).ok


def test_constructed_explain_summarises_a_huge_change() -> None:
    base = [f"x = {i}\n" for i in range(gate.EXPLAIN_MAX_LINES)]
    head = ["y\n", *base[1:]]
    lines = gate.explain(base, head, set())
    assert len(lines) == 1 and "too large to show" in lines[0]


def test_constructed_explain_caps_its_output() -> None:
    base = [f"x = {i}\n" for i in range(1000)]
    head = [f"y = {i}\n" for i in range(1000)]
    lines = gate.explain(base, head, set())
    assert len(lines) == gate.EXPLAIN_MAX_OUTPUT + 1
    assert "more log line(s) not shown" in lines[-1]
