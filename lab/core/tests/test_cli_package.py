"""The `wowlab_core.cli` package (M16-111): `cli.py` split by command group.

The split is a move: `wowlab_core.cli` stays the one import path, so every
name a module of the package defines is the same object on the package; a
name set on the package (a test's `monkeypatch`) reaches the code of every
module that uses it, and is put back when the patch is undone; and the
commands are registered in the order they were before the split, which is
the order `wowlab --help` lists them in. Nothing here reads an install.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import ModuleType

import pytest
import typer

from wowlab_core import cli

PACKAGE = Path(cli.__file__).parent


def _parts() -> list[ModuleType]:
    return [
        importlib.import_module(f"wowlab_core.cli.{p.stem}")
        for p in sorted(PACKAGE.glob("_*.py"))
        if p.name not in ("__init__.py", "__main__.py")
    ]


def _defined(module: ModuleType) -> set[str]:
    """The names a module's own top-level statements bind (not its imports)."""
    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def test_every_name_a_module_defines_is_the_same_object_on_the_package() -> None:
    parts = _parts()
    assert len(parts) > 10, "the walk finds the modules"
    seen: dict[str, str] = {}
    for part in parts:
        for name in _defined(part):
            assert name not in seen, f"{name} defined in {seen[name]} and {part.__name__}"
            seen[name] = part.__name__
            assert getattr(cli, name, None) is getattr(part, name), (
                f"{part.__name__}.{name} is not re-exported by wowlab_core.cli"
            )
    assert "app" in seen and "_confirm" in seen


def test_a_name_set_on_the_package_reaches_every_module_that_uses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cli._say_err
    holders = [p for p in _parts() if p.__dict__.get("_say_err") is original]
    assert len(holders) > 1, "more than one module calls _say_err"

    def fake(message: str) -> None:
        raise AssertionError(message)

    with monkeypatch.context() as m:
        m.setattr(cli, "_say_err", fake)
        assert cli._say_err is fake
        assert all(p.__dict__["_say_err"] is fake for p in holders)
    assert cli._say_err is original
    assert all(p.__dict__["_say_err"] is original for p in holders)


def test_a_module_value_set_on_the_package_reaches_the_module_that_reads_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wowlab_core.cli import _cmd_binds

    monkeypatch.setattr(cli, "_COACH_LOG_BUDGET", 7)
    assert _cmd_binds._COACH_LOG_BUDGET == 7


def test_a_name_deleted_from_the_package_comes_back_to_every_module_on_undo() -> None:
    """`monkeypatch.delattr(cli, ...)` deletes the name everywhere it was held;
    its undo, a set of a name the package no longer has, puts it back there."""
    original = cli._say_err
    holders = [p for p in _parts() if p.__dict__.get("_say_err") is original]
    assert len(holders) > 1, "more than one module calls _say_err"
    m = pytest.MonkeyPatch()
    try:
        m.delattr(cli, "_say_err")
        assert not any("_say_err" in p.__dict__ for p in holders)
    finally:
        m.undo()
    assert cli._say_err is original
    assert all(p.__dict__.get("_say_err") is original for p in holders)


def test_a_name_another_module_binds_to_a_different_object_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only modules holding the package's own object follow a patch."""
    from wowlab_core.cli import _cmd_ui

    monkeypatch.setattr(_cmd_ui, "_m16_111_probe", "theirs", raising=False)
    monkeypatch.setattr(cli, "_m16_111_probe", "ours", raising=False)
    monkeypatch.setattr(cli, "_m16_111_probe", "ours again")
    assert _cmd_ui.__dict__["_m16_111_probe"] == "theirs"


def test_commands_are_registered_in_the_order_they_were_before_the_split() -> None:
    root = typer.main.get_command(cli.app)
    assert isinstance(root, typer.core.TyperGroup)
    assert list(root.commands) == [
        "doctor",
        "tree",
        "explain",
        "undo",
        "ui",
        "install",
        "filemap",
        "sv",
        "cvar",
        "binds",
        "macros",
        "addons",
        "addon",
        "db2",
        "names",
        "snap",
        "log",
        "profile",
        "looks",
        "char",
    ]
    snap = root.commands["snap"]
    assert isinstance(snap, typer.core.TyperGroup)
    assert list(snap.commands) == [
        "create",
        "list",
        "show",
        "diff",
        "verify",
        "gc",
        "export",
        "import",
        "restore",
    ]
