# Probe from review of m15/11-gltf-char-model; reproduces a partly written first run naming files that never existed as "(old)".
"""`_write_default` labels every step it did not reach "(old)". On a first
run (or for a PNG this run makes and the last did not) there is no old file:
the message tells the player an old `.glb` and old PNGs are there when the
folder holds only the one new PNG. Constructed install (the branch's own
`test_cli_char_model` fixture); the second write is made to fail as a
viewer holding the file open would on Windows."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "parser"))

import pytest  # noqa: E402
import test_cli_char_model as t  # noqa: E402

from wowlab_core import cascfiles  # noqa: E402


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[t.Env]:
    yield from t._env(tmp_path, monkeypatch, set())


def _fail_second_write(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cascfiles.replace_derived
    calls = {"n": 0}

    def flaky(data: bytes, target: Path, folder: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermissionError(13, "in use", str(target))
        real(data, target, folder)

    monkeypatch.setattr(cascfiles, "replace_derived", flaky)


def test_positive_control_on_a_rerun_old_files_are_there(
    env: t.Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert env.run("--character", "Sangraal-Sett").exit_code == 0
    _fail_second_write(monkeypatch)
    result = env.run("--character", "Sangraal-Sett")
    assert result.exit_code == 1
    text = " ".join(result.stderr.split())
    assert "1-Sangraal-Sett.glb (old)" in text
    assert env.glb().exists()


def test_a_first_run_does_not_call_missing_files_old(
    env: t.Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_second_write(monkeypatch)
    result = env.run("--character", "Sangraal-Sett")
    assert result.exit_code == 1
    text = " ".join(result.stderr.split())
    assert not env.glb().exists()
    # Fails on 8d6a328: "Not done: …hair.png (old), …eyes.png (old), …glb (old)".
    assert "1-Sangraal-Sett.glb (old)" not in text, text
