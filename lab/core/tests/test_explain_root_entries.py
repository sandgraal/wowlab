"""`explain` of an entry at the install root, typed relatively (M13-57).

A relative path with no file-map row that an `lstat` finds at the install
root (a link, a file, a folder) is judged there, as its absolute spelling
is, and never re-read inside the only flavor folder.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
its flavor folder named as its provenance row says (`_classic_beta_`), and
`--root` points at the copy; the current folder is `tmp_path`, outside the
install. Nothing reads or writes a real install.

Constructed (L8, boundary): the entries at the install root (`Foo`, a
symlink to a file outside the install; `notes.txt`; the folder `Stuff`),
the same names inside the flavor folder, and the second flavor folder.
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

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SECOND = "_retail_"  # constructed: a second copy of the captured flavor folder
LINK = "Foo"
UNKNOWN = "wowlab does not know what this file is"


@pytest.fixture(autouse=True)
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no client in it."""
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


@pytest.fixture(autouse=True)
def userdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import platformdirs

    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)


def _install(tmp_path: Path, *, second: bool = False) -> Path:
    """The install root: the captured flavor folder (and a second copy when
    `second`), with `Foo` (a link), `notes.txt` and `Stuff/` at the root and
    a regular file or folder of each name inside the flavor folder."""
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    for folder in (FLAVOR, SECOND) if second else (FLAVOR,):
        shutil.copytree(CAPTURE / "forever", root / folder)
        (root / folder / LINK).write_bytes(b"constructed: a regular file in the flavor\n")
        (root / folder / "notes.txt").write_bytes(b"constructed: in the flavor\n")
        (root / folder / "Stuff").mkdir()
    target = tmp_path / "elsewhere" / "foo.txt"
    target.parent.mkdir()
    target.write_bytes(b"constructed: outside the install\n")
    try:
        (root / LINK).symlink_to(target)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    (root / "notes.txt").write_bytes(b"constructed: at the install root\n")
    (root / "Stuff").mkdir()
    return root


def _run(*args: str, code: int = 0) -> Any:
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == code, (args, result.output, result.exception)
    return result


def _both(root: Path, name: str, *extra: str) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """(relative JSON, absolute JSON, relative text, absolute text)."""
    absolute = str(root / name)
    rel = json.loads(_run("explain", name, "--root", str(root), "--json", *extra).stdout)
    ab = json.loads(_run("explain", absolute, "--root", str(root), "--json").stdout)
    rel_text = _run("explain", name, "--root", str(root), *extra).stdout
    ab_text = _run("explain", absolute, "--root", str(root)).stdout
    return rel, ab, rel_text, ab_text


def _same_but_query(rel: dict[str, Any], ab: dict[str, Any], name: str, absolute: str) -> None:
    assert (rel.pop("query"), ab.pop("query")) == (name, absolute)
    assert rel == ab


def test_a_root_link_typed_relatively_is_the_link_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)

    rel, ab, rel_text, ab_text = _both(root, LINK)

    assert (rel["result"]["base"], rel["result"]["path"]) == ("root", LINK)
    assert rel["verdict"]["link"] is True
    assert UNKNOWN not in rel_text
    _same_but_query(rel, ab, LINK, str(root / LINK))
    assert rel_text == ab_text.replace(str(root / LINK), LINK)


@pytest.mark.parametrize("name", ["notes.txt", "Stuff"])
def test_a_root_file_or_folder_typed_relatively_is_judged_at_the_root_constructed(
    tmp_path: Path, name: str
) -> None:
    root = _install(tmp_path)

    rel, ab, rel_text, ab_text = _both(root, name)

    assert (rel["result"]["base"], rel["result"]["path"]) == ("root", name)
    assert rel["result"].get("flavor_folder") is None
    _same_but_query(rel, ab, name, str(root / name))
    assert rel_text == ab_text.replace(str(root / name), name)


def test_a_root_link_with_a_named_flavor_stays_at_the_root_constructed(tmp_path: Path) -> None:
    """`--flavor` naming the flavor does not re-read a root entry in it; one
    naming no flavor is refused as on main."""
    root = _install(tmp_path)

    rel, ab, _, _ = _both(root, LINK, "--flavor", FLAVOR)
    nope = _run("explain", LINK, "--flavor", "_nope_", "--root", str(root), code=2)

    _same_but_query(rel, ab, LINK, str(root / LINK))
    assert rel["verdict"]["link"] is True
    assert "no flavor folder '_nope_'" in nope.stderr


def test_a_root_link_with_several_flavors_has_no_flavor_hint_constructed(tmp_path: Path) -> None:
    """The hint to pass `--flavor` is left out: it would not read the path
    inside a flavor folder."""
    root = _install(tmp_path, second=True)

    for extra in ((), ("--flavor", SECOND)):
        rel, ab, rel_text, ab_text = _both(root, LINK, *extra)
        _same_but_query(rel, ab, LINK, str(root / LINK))
        assert "pass --flavor" not in rel_text
        assert rel_text == ab_text.replace(str(root / LINK), LINK)


def test_a_path_only_inside_the_flavor_is_still_read_there(tmp_path: Path) -> None:
    """Control: nothing at the root by that name, so it is read in the flavor."""
    root = _install(tmp_path)

    data = json.loads(_run("explain", "WTF/Config.wtf", "--root", str(root), "--json").stdout)

    assert (data["result"]["base"], data["result"]["flavor_folder"]) == ("flavor", FLAVOR)
