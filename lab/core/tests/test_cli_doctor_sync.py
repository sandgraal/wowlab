"""`doctor`'s server-sync lines when a sync setting has no line (M16-127).

On one client (`docs/LAB_FORMATS.md` §5 and §7, amended 2026-11-12), with no
line in any file, two of the three sync settings read as on and the third
did not exist at all. So a setting with no line is described as possibly on
or possibly absent, never as "the client's default".

Every input here is `constructed` (L8, boundary): the `Config.wtf` and
account `config-cache.wtf` are written by the test, beside the captured
`.build.info` and `.flavor.info` (`fixtures/macos/`, README row), in an
install under `tmp_path`. The user data directory is redirected, no client
is in the process table and game data is replayed (`test_cli`'s autouse
fixtures). Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from test_cli import (
    CAPTURE,
    FLAVOR,
    _plain,
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    run,
    user_data,  # noqa: F401  (autouse: the store lives in tmp_path)
)

from wowlab_core import install

ACCOUNT = "12345678#1"  # constructed; the shape `<digits>#<digits>` makes it an account
MACHINE = "WTF/Config.wtf"
ACCOUNT_FILE = f"WTF/Account/{ACCOUNT}/config-cache.wtf"
NAMES = ("synchronizeBindings", "synchronizeMacros", "synchronizeConfig")
UNSET_ONE = (
    "not set (the client may treat it as on or may not have it at all; wowlab cannot tell which)"
)


def _install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, machine: bytes, account: bytes
) -> Path:
    root = tmp_path / "World of Warcraft"
    flavor = root / FLAVOR
    (flavor / "WTF" / "Account" / ACCOUNT).mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copy2(CAPTURE / "forever" / ".flavor.info", flavor / ".flavor.info")
    (flavor / MACHINE).write_bytes(machine)
    (flavor / ACCOUNT_FILE).write_bytes(account)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _doctor(*args: str) -> str:
    result = run("doctor", "--offline", *args)
    assert result.exit_code in (0, 1), (result.stdout, result.exception)
    return _plain(result.stdout)


def _flat(text: str) -> str:
    return " ".join(text.split())


_PLAIN_CVARS = b'SET farclip "8000"\r\nSET showTargetOfTarget "1"\r\n'


def test_no_sync_line_anywhere_is_one_line_with_no_default_claimed_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, monkeypatch, machine=_PLAIN_CVARS, account=_PLAIN_CVARS)
    out = _flat(_doctor())
    assert (
        f"Server sync: the three sync settings wowlab reads are not set in {MACHINE} or "
        f"{ACCOUNT_FILE} (when on, the server can replace your bindings, macros or settings "
        "at login; with no line, the client may treat a setting as on or may not have it at "
        "all, and wowlab cannot tell which)"
    ) in out
    assert "client's default" not in out
    for name in NAMES:
        assert name not in out


def test_one_set_lists_each_and_unset_ones_may_be_absent_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(
        tmp_path,
        monkeypatch,
        machine=_PLAIN_CVARS + b'SET synchronizeBindings "0"\r\n',
        account=_PLAIN_CVARS + b'SET synchronizeConfig "1"\r\n',
    )
    flat = _flat(_doctor())
    assert (
        "Server sync (when on, the server can replace your bindings, macros or settings at login):"
    ) in flat
    assert f'{MACHINE}: synchronizeBindings "0"' in flat
    assert f"{MACHINE}: synchronizeMacros {UNSET_ONE}" in flat
    assert f"{MACHINE}: synchronizeConfig {UNSET_ONE}" in flat
    assert f"{ACCOUNT_FILE}: synchronizeBindings {UNSET_ONE}" in flat
    assert f"{ACCOUNT_FILE}: synchronizeMacros {UNSET_ONE}" in flat
    assert f'{ACCOUNT_FILE}: synchronizeConfig "1"' in flat
    assert "client's default" not in flat


def test_json_keeps_unset_as_null_and_set_values_verbatim_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--json` is unchanged by the wording: no line is `null`, a line is its
    value as written."""
    _install(
        tmp_path,
        monkeypatch,
        machine=b'SET synchronizeMacros "0"\r\n',
        account=_PLAIN_CVARS,
    )
    result = run("doctor", "--offline", "--json")
    (flavor,) = json.loads(result.stdout)["flavors"]
    got = {(s["file"], s["name"]): s["value"] for s in flavor["sync_cvars"]}
    assert got == {
        (MACHINE, "synchronizeBindings"): None,
        (MACHINE, "synchronizeMacros"): "0",
        (MACHINE, "synchronizeConfig"): None,
        (ACCOUNT_FILE, "synchronizeBindings"): None,
        (ACCOUNT_FILE, "synchronizeMacros"): None,
        (ACCOUNT_FILE, "synchronizeConfig"): None,
    }


def test_sync_wording_names_no_flavor_or_build_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L6: what an unset setting means is said without naming a client, a
    flavor folder or a build number."""
    _install(
        tmp_path,
        monkeypatch,
        machine=b'SET synchronizeConfig "1"\r\n',
        account=_PLAIN_CVARS,
    )
    flat = _flat(_doctor())
    unset = [m.group(0) for m in re.finditer(r"not set \([^)]*\)", flat)]
    assert len(unset) == 5
    for text in unset:
        assert text == UNSET_ONE
        assert not re.search(r"\d", text)
