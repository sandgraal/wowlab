"""A crash in `scripts/check_wiki.py` never reads as "no personal data" (M13-40).

The wiki-daily job lets pages out when `check_wiki` exits 0 or 1, and Python
exits 1 on any uncaught exception. So `main` turns a crash into 2, or 3 when
the personal-data check, which runs first, found something.

Every input is `constructed`: the small passing wiki of `test_check_wiki.py`
with one invented page. No real page makes a check raise (the security
review of #236 tried 16 hostile ones), so the crash itself is injected by
replacing one check with a function that raises; the page decides whether
there is personal data to find. Names and addresses are invented.
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

import pytest
from test_check_wiki import SCRIPTS, _build, _commit_all, cw

PERSONAL = "# Extra\n\nWrite to jane.doe@example.org.\n"
CLEAN = "# Extra\n\nNothing to see.\n"
SECRETS = ("jane", "example.org")

# Every check that runs after the personal-data one.
LATER_CHECKS = ("check_generated", "check_links", "check_commands", "check_coverage", "check_diff")


def _raise(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise RuntimeError("a check fell over on jane.doe@example.org")


@pytest.mark.parametrize("method", LATER_CHECKS)
@pytest.mark.parametrize(("page", "code"), [(PERSONAL, 3), (CLEAN, 2)])
def test_constructed_a_later_check_that_raises_exits_3_or_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, page: str, code: int
) -> None:
    root = _build(tmp_path / "wiki", Extra=page)
    monkeypatch.setattr(cw.Checker, method, _raise)
    assert cw.main([str(root)]) == code


@pytest.mark.parametrize(("page", "code"), [(PERSONAL, 3), (CLEAN, 2)])
def test_constructed_building_the_command_tree_that_raises_exits_3_or_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: str, code: int
) -> None:
    root = _build(tmp_path / "wiki", Extra=page)
    monkeypatch.setattr(cw.gen, "root_command", _raise)
    assert cw.main([str(root)]) == code


@pytest.mark.parametrize(("page", "code"), [(PERSONAL, 3), (CLEAN, 2)])
def test_constructed_reading_that_fails_part_way_still_checks_what_was_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: str, code: int
) -> None:
    # `Extra.md` is read before `Zzz.md`, which makes reading raise.
    root = _build(tmp_path / "wiki", Extra=page, Zzz="# Zzz\n")
    parse = cw._parse_page

    def parse_or_raise(name: str, text: str) -> Any:
        if name == "Zzz.md":
            _raise()
        return parse(name, text)

    monkeypatch.setattr(cw, "_parse_page", parse_or_raise)
    assert cw.main([str(root)]) == code


def test_constructed_a_crash_prints_the_exception_type_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _build(tmp_path / "wiki", Extra=PERSONAL)
    monkeypatch.setattr(cw.Checker, "check_links", _raise)
    assert cw.main([str(root)]) == cw.EXIT_PERSONAL
    captured = capsys.readouterr()
    assert captured.err == "check_wiki: a check failed (RuntimeError); the wiki is not checked\n"
    # What was found before the crash is printed, withheld as always.
    assert captured.out == f"Extra.md:3: an email address {cw.PERSONAL_SUFFIX}\n"
    for secret in SECRETS:
        assert secret not in captured.out + captured.err


def test_constructed_git_failing_after_personal_data_was_found_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _build(tmp_path / "wiki", Extra=PERSONAL)
    assert cw.main([str(root), "--base", "no-such-ref"]) == cw.EXIT_PERSONAL
    assert "git failed reading --base" in capsys.readouterr().err


def test_constructed_the_personal_data_check_runs_before_every_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _build(tmp_path / "wiki")
    _commit_all(root)
    order: list[str] = []
    for method in ("load", "check_privacy", *LATER_CHECKS):
        original = getattr(cw.Checker, method)

        def record(self: Any, *args: Any, _m: str = method, _f: Any = original) -> Any:
            order.append(_m)
            return _f(self, *args)

        monkeypatch.setattr(cw.Checker, method, record)
    cw.check(root)
    assert order[:2] == ["load", "check_privacy"]
    assert sorted(order[2:]) == sorted(LATER_CHECKS)


def test_constructed_an_unexpected_error_is_not_swallowed_by_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The library entry point still raises; only the program maps it to an exit code.
    root = _build(tmp_path / "wiki")
    monkeypatch.setattr(cw.Checker, "check_links", _raise)
    with pytest.raises(RuntimeError):
        cw.check(root)
    with pytest.raises(subprocess.CalledProcessError):
        monkeypatch.undo()
        cw.check(root, "no-such-ref")


# ─── round 1 of review: crashes outside `run`, and SystemExit ────────────────


@pytest.mark.parametrize("status", [0, 1, None])
@pytest.mark.parametrize(("page", "code"), [(PERSONAL, 3), (CLEAN, 2)])
def test_constructed_a_check_that_raises_system_exit_exits_3_or_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int | None, page: str, code: int
) -> None:
    # e.g. a `sys.exit(0)` at the top of a module a check imports.
    def leave(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise SystemExit(status)

    root = _build(tmp_path / "wiki", Extra=page)
    monkeypatch.setattr(cw.Checker, "check_generated", leave)
    assert cw.main([str(root)]) == code


def test_constructed_ctrl_c_is_not_turned_into_an_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise KeyboardInterrupt

    root = _build(tmp_path / "wiki", Extra=PERSONAL)
    monkeypatch.setattr(cw.Checker, "check_links", interrupt)
    with pytest.raises(KeyboardInterrupt):
        cw.main([str(root)])


@pytest.mark.parametrize(("page", "code"), [(PERSONAL, 3), (CLEAN, 2)])
def test_constructed_sorting_the_failures_that_raises_exits_3_or_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: str, code: int
) -> None:
    root = _build(tmp_path / "wiki", Extra=page + "`wowlab nope`\n")
    monkeypatch.setattr(cw, "_order", _raise)
    assert cw.main([str(root)]) == code


def test_constructed_a_location_ending_in_a_non_ascii_digit_sorts_as_a_name() -> None:
    # `'²'.isdigit()` holds and `int('²')` raises.
    failure = cw.Failure("x:\u00b2", "unexpected file")
    assert cw._order(failure) == ("x:\u00b2", 0, "unexpected file")
    assert cw._order(cw.Failure("Extra.md:12", "m")) == ("Extra.md", 12, "m")


def test_constructed_a_line_stdout_cannot_encode_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def add_failure(self: Any, *_args: Any) -> None:
        self.fail("\udcff.md", "a constructed failure")

    root = _build(tmp_path / "wiki", Extra=CLEAN)
    monkeypatch.setattr(cw.Checker, "check_links", add_failure)
    assert cw.main([str(root)]) == cw.EXIT_FAILURES
    assert capsys.readouterr().out == "\\udcff.md: a constructed failure\n"


@pytest.mark.parametrize("body", ["raise SystemExit(0)\n", "raise ImportError('no')\n"])
def test_constructed_an_import_that_fails_exits_2_when_run_as_the_gate(
    tmp_path: Path, body: str
) -> None:
    # A `wowlab_core` ahead of the real one on the path stands in for a broken
    # install: a missing dependency, or a module that exits as it loads.
    fake = tmp_path / "fake" / "wowlab_core"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(body, encoding="utf-8")
    root = _build(tmp_path / "wiki", Extra=PERSONAL)
    env = {**os.environ, "PYTHONPATH": str(fake.parent)}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_wiki.py"), str(root)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == cw.EXIT_ERROR, result.stdout + result.stderr
    assert result.stdout == ""
    assert "loading the checker failed" in result.stderr
    assert "Traceback" not in result.stderr


def _break_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys.modules["gen_command_reference"], "render")


def _break_default_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.modules["wowlab_core.install"], "default_roots", _raise)


@pytest.mark.parametrize("breaks", [_break_render, _break_default_roots])
def test_constructed_module_set_up_that_raises_exits_2_when_run_as_the_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    breaks: Any,
) -> None:
    # `GENERATED` reads `gen.render` and `_DEFAULT_ROOT` runs
    # `install.default_roots` as the module loads, after the imports.
    root = _build(tmp_path / "wiki", Extra=PERSONAL)
    breaks(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["check_wiki.py", str(root)])
    with pytest.raises(SystemExit) as gate:
        runpy.run_path(str(SCRIPTS / "check_wiki.py"), run_name="__main__")
    assert gate.value.code == cw.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "loading the checker failed" in captured.err
    # Imported as a library, the error propagates unchanged.
    with pytest.raises((AttributeError, RuntimeError)):
        runpy.run_path(str(SCRIPTS / "check_wiki.py"), run_name="check_wiki_library")
