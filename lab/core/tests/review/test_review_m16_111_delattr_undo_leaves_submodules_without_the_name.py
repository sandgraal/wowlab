# Probe from review of m16/111-cli-package; reproduces monkeypatch.delattr(cli, name) undo restoring the package but not the submodules that held the name
"""`cli._Package.__delattr__` deletes a name from every submodule holding the
same object, but `monkeypatch`'s undo of a delattr is a `setattr` on a name the
package no longer has, and `_Package.__setattr__` returns early when the old
value is unset, so the submodules are never given the name back. Code in those
modules then raises NameError for the rest of the session. In the one-module
`cli.py` the undo restored the only namespace there was. Constructed (L8:
boundary of the patch mechanism, no install read)."""

from __future__ import annotations

import pytest

from wowlab_core import cli
from wowlab_core.cli import _cmd_binds


def test_positive_control_setattr_undo_restores_the_submodule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cli._COACH_LOG_BUDGET
    with monkeypatch.context() as m:
        m.setattr(cli, "_COACH_LOG_BUDGET", 7)
        assert _cmd_binds.__dict__["_COACH_LOG_BUDGET"] == 7
    assert _cmd_binds.__dict__["_COACH_LOG_BUDGET"] is original


def test_delattr_undo_restores_the_submodule() -> None:
    original = cli._COACH_LOG_BUDGET
    assert _cmd_binds.__dict__.get("_COACH_LOG_BUDGET") is original
    m = pytest.MonkeyPatch()
    try:
        m.delattr(cli, "_COACH_LOG_BUDGET")
        assert "_COACH_LOG_BUDGET" not in _cmd_binds.__dict__
    finally:
        m.undo()
    restored = _cmd_binds.__dict__.get("_COACH_LOG_BUDGET", "<missing>")
    # Put it back by hand so a failure here does not break later tests.
    _cmd_binds.__dict__["_COACH_LOG_BUDGET"] = original
    assert cli._COACH_LOG_BUDGET is original
    assert restored is original, f"submodule after undo: {restored!r}"
