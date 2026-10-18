# Probe from review of m13/57-explain-root-entries; reproduces `explain Foo/missing.txt` (relative, `Foo` a root link) still judged inside the only flavor with `link: false`
"""M13-57: a relative path at a root entry is judged at the install root, as
its absolute spelling is. `_found_at_root` decides that with one `lstat` of
the whole typed path, which the OS resolves through a link in the middle.
So for a path under a root link `Foo`:

- `Foo/missing.txt` (nothing at the link's target) is still re-read inside
  the only flavor: `verdict.link` is false and the words are "wowlab does
  not know what this file is", while the absolute spelling says the link.
  That is the ticket's defect, one level down.
- `Foo/present.txt` gets the link verdict only because a file of that name
  exists outside the install, where the link points: the answer depends on
  a stat through a link (§6.2, M13-29 amendment: the probe walks down from
  the root one part at a time and stops at any link).

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`. Constructed (L8, boundary): the root symlink `Foo` to a folder
outside the install, and the file `present.txt` in that folder. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

CAPTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (L6: tests may name it)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import platformdirs

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "ud" / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)  # outside the install


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "present.txt").write_bytes(b"constructed: outside the install\n")
    try:
        (root / "Foo").symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    return root


def _json(path: str, root: Path) -> dict[str, Any]:
    result = CliRunner().invoke(cli.app, ["explain", path, "--root", str(root), "--json"])
    assert result.exit_code == 0, (path, result.output)
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def test_positive_control_the_root_link_itself_is_the_link_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)

    assert _json("Foo", root)["verdict"]["link"] is True


@pytest.mark.parametrize("name", ["present.txt", "missing.txt"])
def test_a_path_under_a_root_link_reads_as_its_absolute_spelling_constructed(
    tmp_path: Path, name: str
) -> None:
    root = _install(tmp_path)
    typed = f"Foo/{name}"

    rel = _json(typed, root)
    ab = _json(str(root / "Foo" / name), root)

    assert ab["verdict"]["link"] is True  # the absolute spelling: inside a link
    assert rel["verdict"]["link"] is True, rel["verdict"]["words"]
    assert rel["verdict"] == ab["verdict"]
