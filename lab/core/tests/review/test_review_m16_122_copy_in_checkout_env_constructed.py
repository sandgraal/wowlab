# Probe from review of m16/122-doctor-names-version; reproduces an installed (non-editable)
# copy in a checkout's own environment being called "installed in <checkout>", with the
# checkout's newer version hidden.
"""Constructed (boundary). A source checkout at 0.2.0 whose `.venv` holds a
non-editable copy of wowlab-core 0.1.0 (`uv sync --no-editable`, or `pip
install lab/core`). That copy is stale against the checkout: the owner's
case in M16-122 ("every fix tested looked unfixed"). `RunningWowlab` says an
installed copy has `checkout` None, yet `_running_wowlab` walks up from the
package folder, finds the checkout around `.venv`, reports it as the
location ("installed in <checkout>") and, because the install is
"installed", never says the checkout is at another version.

Positive control: the same checkout, run as an editable install, does say
it (so the version comparison itself works)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wowlab_core import cli

WHEEL = json.dumps({"url": "file:///x/wowlab_core-0.1.0-py3-none-any.whl", "archive_info": {}})
EDITABLE = json.dumps({"url": "file:///x/lab/core", "dir_info": {"editable": True}})


def _clone(tmp_path: Path) -> Path:
    clone = tmp_path / "clone"
    core = clone / "lab" / "core"
    (core / "src" / "wowlab_core").mkdir(parents=True)
    (core / "pyproject.toml").write_text(
        '[project]\nname = "wowlab-core"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    return clone


def test_copy_in_checkout_env_is_not_called_the_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = _clone(tmp_path)
    env = clone / ".venv"
    package = env / "lib" / "python3.12" / "site-packages" / "wowlab_core"
    package.mkdir(parents=True)
    w = cli._running_wowlab("0.1.0", package, WHEEL, True, clone, env)
    assert w.install == "installed"
    cli._print_wowlab(w)
    out = capsys.readouterr().out
    # Either it is not presented as the checkout, or the stale version is said.
    assert w.checkout is None or "0.2.0" in out, out


def test_positive_control_editable_checkout_says_the_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = _clone(tmp_path)
    package = clone / "lab" / "core" / "src" / "wowlab_core"
    w = cli._running_wowlab("0.1.0", package, EDITABLE, True, clone, clone / ".venv")
    cli._print_wowlab(w)
    assert "0.2.0" in capsys.readouterr().out
