"""A command's module is imported when the command is looked up (M15-22).

`import wowlab_core.cli` imports `_common` and nothing else of the package;
looking a command up imports the modules `_COMMAND_MODULES` names for it;
the tree is the same whatever was imported first; and a name set on the
package reaches a module that was not imported yet. Each case runs in a
fresh interpreter, since this test process has imported everything long
before. Nothing here reads an install.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from typing import Any

import typer

from wowlab_core import cli
from wowlab_core.cli import _common

_LOADED = "[n for n in _common._PART_NAMES if f'wowlab_core.cli.{n}' in sys.modules]"


def _child(code: str) -> Any:
    """Run `code` in a fresh `python -P`; it prints one JSON value last."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("WOWLAB_")}
    done = subprocess.run(
        [sys.executable, "-P", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


def _part(callback: Any) -> str:
    return str(callback.__module__).rpartition(".")[2]


def _loaded_after(*argv: str) -> list[str]:
    out = _child(
        f"""
        import json, sys
        import typer
        from wowlab_core import cli
        from wowlab_core.cli import _common
        root = typer.main.get_command(cli.app)
        try:
            root.main({list(argv)!r}, prog_name="wowlab", standalone_mode=False)
        except SystemExit:
            pass
        print(json.dumps({_LOADED}))
        """
    )
    assert isinstance(out, list)
    return out


def test_importing_the_package_imports_no_command_module() -> None:
    assert _child(
        f"""
        import json, sys
        from wowlab_core import cli
        from wowlab_core.cli import _common
        assert cli.app is _common.app and cli._say_err is _common._say_err
        print(json.dumps({_LOADED}))
        """
    ) == ["_common"]


def test_version_imports_no_command_module() -> None:
    assert _loaded_after("--version") == ["_common"]


def test_a_command_imports_its_own_modules_only() -> None:
    assert _loaded_after("install", "show", "--help") == ["_common", "_cmd_install"]
    loaded = _loaded_after("snap", "list", "--help")
    assert {"_cmd_snap", "_cmd_restore"} <= set(loaded)
    assert not {"_cmd_ui", "_cmd_charmodel", "_cmd_looks", "_cmd_install"} & set(loaded)


def test_every_registration_is_under_its_key_in_the_table() -> None:
    """`_COMMAND_MODULES` names, for every root command and group, every
    module that registers one of its commands, and nothing else."""
    root = typer.main.get_command(cli.app)  # every module, through `commands`
    assert isinstance(root, typer.core.TyperGroup)
    assert len(root.commands) > 10
    groups = {g.name: g.typer_instance for g in _common.app.registered_groups}
    found: dict[str, set[str]] = {_common._ROOT_COMMANDS: set()}
    for info in _common.app.registered_commands:
        assert info.callback is not None
        name = info.name or typer.main.get_command_name(info.callback.__name__)
        part = _part(info.callback)
        found.setdefault(name, set()).add(part)
        found[_common._ROOT_COMMANDS].add(part)
    for name, sub in groups.items():
        assert name is not None and sub is not None
        found[name] = {_part(i.callback) for i in sub.registered_commands}
    assert {k: set(v) for k, v in _common._COMMAND_MODULES.items()} == found


def test_the_tree_is_the_same_whatever_was_imported_first() -> None:
    walk = """
        import json, sys
        import typer
        from wowlab_core import cli
        def walk(cmd):
            subs = getattr(cmd, "commands", None)
            return None if subs is None else {n: walk(c) for n, c in subs.items()}
        root = typer.main.get_command(cli.app)
        print(json.dumps(list(walk(root).items())))
    """
    canonical = _child("from wowlab_core import cli; cli._load_all()\n" + textwrap.dedent(walk))
    backwards = _child(
        "import importlib\n"
        "from wowlab_core.cli import _common\n"
        "for n in reversed(_common._PART_NAMES):\n"
        "    importlib.import_module(f'wowlab_core.cli.{n}')\n" + textwrap.dedent(walk)
    )
    looked_up = _child(
        "import typer\n"
        "from wowlab_core import cli\n"
        "r = typer.main.get_command(cli.app)\n"
        "for n in ('ui', 'char', 'undo', 'snap', 'doctor'):\n"
        "    r.get_command(None, n)\n" + textwrap.dedent(walk)
    )
    assert canonical == backwards == looked_up
    names = [name for name, _ in canonical]
    assert names[:5] == ["doctor", "tree", "explain", "undo", "ui"]
    snap = dict(canonical)["snap"]
    assert list(snap)[-1] == "restore"


def test_a_name_set_on_the_package_reaches_a_module_not_imported_yet() -> None:
    assert _child(
        f"""
        import json, sys
        from wowlab_core import cli
        from wowlab_core.cli import _common
        def fake(message):
            pass
        stub = type(sys)("contextlib")
        before = {_LOADED}
        cli._say_err = fake
        cli.contextlib = stub  # a library module, which a later import would bind afresh
        from wowlab_core.cli import _cmd_restore, _cmd_sv, _cmd_ui
        print(json.dumps([
            before,
            _cmd_ui._say_err is fake,
            _cmd_restore._say_err is fake,
            _cmd_sv.contextlib is stub,
        ]))
        """
    ) == [["_common"], True, True, True]


def test_an_unknown_command_still_names_the_close_one() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("WOWLAB_")}
    done = subprocess.run(
        [sys.executable, "-P", "-m", "wowlab_core", "instal"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 2
    assert "Did you mean 'install'?" in done.stderr
