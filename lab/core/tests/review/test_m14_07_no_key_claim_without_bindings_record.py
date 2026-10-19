# Probe from review of m14/07-char-macros; reproduces: with no usable bindings
# record, `char macros` prints "no key reaches it" for a candidate slot and
# drops keymap's caveat that keys left out of the files may still be bound.
"""The real 70425 schema-3 record (its two macro slots, 64 and 65) is put in
a character folder of the captured macOS install with 29 constructed
character macros, so the candidate numbering places macros 20 and 29. The
record's `bindings` section is then replaced by an absent section
(constructed), the state `keymap` covers with the note "a file may leave out
keys at the client's defaults [verify], so keys not listed may still be
bound". `char binds` prints that note; `char macros` should not claim more
than it.

Positive control: `char binds` on the same tree carries the caveat."""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SCHEMA_3 = (
    FIXTURES
    / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
CHAR_DIR = "WTF/Account/90000001#6/1/Labcharb-Labrealmd"
CAVEAT = "keys not listed may still be bound"


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "data")
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: tmp_path / "config")
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos/.build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos/forever", root / FLAVOR)
    char = root / FLAVOR / CHAR_DIR
    (char / "SavedVariables").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCHEMA_3, char / "SavedVariables/WowLab.lua")
    body = "".join(
        f'VER 3 {0x0100000000000000 + n:016X} "Lab {n}" "134400"\r\n/say lab {n}\r\nEND\r\n'
        for n in range(1, 30)
    )
    (char / "macros-cache.txt").write_bytes(body.encode())
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    original = labaddon.read_char

    def no_bindings(path: Path) -> labaddon.CharDB:
        record = original(path)
        absent = labaddon.AbsentProbedSection(absent="constructed: not gathered")
        return record.model_copy(update={"bindings": absent})

    monkeypatch.setattr(labaddon, "read_char", no_bindings)
    return root


def test_positive_control_char_binds_carries_the_caveat(tree: Path) -> None:
    out = CliRunner().invoke(cli.app, ["char", "binds", "--character", "Labcharb-Labrealmd"])
    assert out.exit_code == 0, out.stdout
    assert CAVEAT in out.stdout


def test_char_macros_no_key_claim_keeps_the_caveat_constructed(tree: Path) -> None:
    out = CliRunner().invoke(cli.app, ["char", "macros", "--character", "Labcharb-Labrealmd"])
    assert out.exit_code == 0, out.stdout
    assert "may sit at slot 64 (unconfirmed)" in out.stdout
    assert "no key reaches it on the page its bar shows by default" in out.stdout
    assert CAVEAT in out.stdout
