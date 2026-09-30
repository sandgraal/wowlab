# Probe from review of m11/33-cli-excepthook; reproduces lab/core/pyproject.toml declaring typer>=0.16 while cli.py imports typer._click, which typer ships only from 0.26.0
"""`cli.py` (M11-33) imports `Context`, `ClickException`, `UsageError` and
`NoArgsIsHelpError` from `typer._click`, the copy of click that typer
vendors. `lab/core/pyproject.toml` still declares `typer>=0.16`, so the
declared range admits versions where `import wowlab_core.cli` fails.

The reviewer checked this on 2026-09-30 by importing the branch's `cli.py`
in isolated environments (`uv run --isolated --no-project --with typer==V`).
Under typer 0.16.0, 0.20.0, 0.24.0 and 0.25.0 it fails with
`ModuleNotFoundError: No module named 'typer._click'`; those versions depend
on click instead. Under 0.26.0, 0.27.0 and 0.27.2 it imports. Main's
`cli.py` imports under 0.16.0 and 0.25.0. A test cannot install typer
versions (no network in tests), so this one checks the declared range
statically against that first version.

The check applies only while `cli.py` imports the private module. If the
import goes, the test passes whatever the range says.

Control: the range check itself tells `>=0.16` and a missing bound from
`>=0.26` and `>=0.27,<0.28`. Constructed inputs (requirement strings), not
fixtures.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]  # lab/core
PYPROJECT = CORE / "pyproject.toml"
CLI = CORE / "src" / "wowlab_core" / "cli.py"

# The first typer release that ships `typer._click` (verified as the
# docstring says).
FIRST_WITH_PRIVATE_CLICK = (0, 26, 0)

_PRIVATE_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+typer\._click\b|^\s*from\s+typer\s+import\s+[^\n]*\b_click\b",
    re.MULTILINE,
)
_LOWER = re.compile(r"(>=|>|==|~=)\s*([0-9]+(?:\.[0-9]+)*)")


def _version(text: str) -> tuple[int, int, int]:
    parts = [int(p) for p in text.split(".")][:3]
    parts += [0] * (3 - len(parts))
    return (parts[0], parts[1], parts[2])


def _admits_below(requirement: str, bound: tuple[int, int, int]) -> bool:
    """Whether `requirement` admits some version below `bound`: true when it
    has no lower bound, or when its highest lower bound is below `bound`."""
    lows = [_version(v) for _, v in _LOWER.findall(requirement)]
    if not lows:
        return True
    return max(lows) < bound


def _typer_requirement() -> str:
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    found = [d for d in deps if re.match(r"typer(?![\w-])", d)]
    assert len(found) == 1, deps
    return str(found[0])


def test_positive_control_the_range_check_tells_a_low_floor_from_a_high_one() -> None:
    assert _admits_below("typer>=0.16", FIRST_WITH_PRIVATE_CLICK)
    assert _admits_below("typer", FIRST_WITH_PRIVATE_CLICK)
    assert _admits_below("typer>0.25", FIRST_WITH_PRIVATE_CLICK)
    assert not _admits_below("typer>=0.26", FIRST_WITH_PRIVATE_CLICK)
    assert not _admits_below("typer>=0.27,<0.28", FIRST_WITH_PRIVATE_CLICK)
    assert not _admits_below("typer==0.27.2", FIRST_WITH_PRIVATE_CLICK)


def test_positive_control_the_import_pattern_finds_the_private_module() -> None:
    assert _PRIVATE_IMPORT.search("from typer._click import Context\n")
    assert _PRIVATE_IMPORT.search("from typer._click.exceptions import UsageError\n")
    assert _PRIVATE_IMPORT.search("from typer import _click\n")
    assert not _PRIVATE_IMPORT.search("from typer.core import TyperGroup\n")


def test_the_declared_typer_range_has_the_click_module_cli_imports() -> None:
    if not _PRIVATE_IMPORT.search(CLI.read_text(encoding="utf-8")):
        return  # no private import: any declared range will do
    requirement = _typer_requirement()
    assert not _admits_below(requirement, FIRST_WITH_PRIVATE_CLICK), (
        f"{PYPROJECT.name} declares {requirement!r}, but cli.py imports typer._click, "
        f"which typer ships only from {'.'.join(map(str, FIRST_WITH_PRIVATE_CLICK))}"
    )
