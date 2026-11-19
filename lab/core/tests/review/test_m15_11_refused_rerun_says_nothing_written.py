# Probe from review of m15/11-gltf-char-model; reproduces a re-run refused at the stale-PNG step saying "nothing was written" after it replaced the PNGs.
"""`_write_default` checks the `.glb` and the PNGs it will write, then
replaces the PNGs, and only afterwards checks (through `remove_derived`) the
older PNGs it will remove. A link (or a folder) planted at an older PNG's
name is refused there, with "nothing was written", after the new PNGs have
replaced the old ones and before the `.glb` is: the folder is left with new
textures beside an old model and the message is false. Constructed install
(the branch's own `test_cli_char_model` fixture)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "parser"))

import pytest  # noqa: E402
import test_cli_char_model as t  # noqa: E402


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[t.Env]:
    yield from t._env(tmp_path, monkeypatch, set())


def _prime(env: t.Env) -> tuple[Path, Path]:
    assert env.run("--character", "Sangraal-Sett").exit_code == 0
    glb = env.glb()
    body = glb.with_name("Sangraal-Sett.body.png")
    body.write_bytes(b"older body texture")
    glb.write_bytes(b"older model")
    return glb, body


def test_positive_control_a_clean_rerun_replaces_both(env: t.Env) -> None:
    glb, body = _prime(env)
    result = env.run("--character", "Sangraal-Sett")
    assert result.exit_code == 0, result.output
    assert body.read_bytes() != b"older body texture"
    assert glb.read_bytes() != b"older model"


def test_a_refused_rerun_writes_nothing(env: t.Env, tmp_path: Path) -> None:
    glb, body = _prime(env)
    victim = tmp_path / "victim.png"
    victim.write_bytes(b"v")
    (glb.parent / "Sangraal-Sett.texture9.png").symlink_to(victim)
    result = env.run("--character", "Sangraal-Sett")
    assert result.exit_code == 1, result.output
    assert "nothing was written" in result.stderr
    assert victim.read_bytes() == b"v"
    assert glb.read_bytes() == b"older model"
    # Fails on 38ad7a6: the body PNG was already replaced.
    assert body.read_bytes() == b"older body texture", "a PNG was replaced before the refusal"
