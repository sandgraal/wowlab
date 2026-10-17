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

import subprocess
from pathlib import Path
from typing import Any, NoReturn

import pytest
from test_check_wiki import _build, _commit_all, cw

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
