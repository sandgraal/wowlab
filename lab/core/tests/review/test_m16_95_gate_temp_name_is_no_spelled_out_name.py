# Probe from review of m16/95-account-folder-file-notes; reproduces the new "file" rule firing for the `wowlab-temp` row, whose `<wowlab-temp>` token is no spelled-out name
"""M16-95's rule is for a file row "that spells out its name" (LAB_PLAN
§6.2, amendment of 2026-11-11 (M16-95)); `FileMapEntry.names_the_file` says
its last segment is "a name spelled out, not a wildcard or a token".
`<wowlab-temp>` is a token of the pattern grammar, but `filemap._literal`
does not list it, so the `wowlab-temp` row (`WTF/**/<wowlab-temp>`,
`Interface/**/<wowlab-temp>`) counts as spelling out a name. A link named
like a gate temp then flips from "real one" to "real file" in four places
the amendment and the PR's user-facing list do not name: `Interface/`
(against `interface-override`), `Interface/AddOns/` (`addon`),
`WTF/Account/<acct>/` (`realm-folder`) and `WTF/Account/<acct>/<realm>/`
(`character-folder`).

Constructed (L8, boundary): places classified with nothing on disk, and an
end-to-end `tree` run on the captured tree copied into `tmp_path` with a
symlink to a constructed file outside it. No real install is read or
written.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, filemap, layout

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "WTF/Account/90000001#6"
TEMP = ".wowlab-" + "0" * 32 + ".tmp"
FILE_END = "wowlab does not read through links, so it reads this only when the real file is there"
ONE_END = "wowlab does not read through links, so it reads this only when the real one is there"


def test_the_temp_token_spells_out_no_name_constructed() -> None:
    rows = {e.id: e for e in filemap.load().entries}
    # Positive control: a row that does spell out its name.
    assert rows["account-macros-cache"].names_the_file(
        ["WTF", "Account", "90000001#1", "macros-cache.txt"]
    )
    assert not rows["wowlab-temp"].names_the_file(["Interface", TEMP])


@pytest.fixture
def flavor_tree(tmp_path: Path) -> layout.Layout:
    root = tmp_path / "World of Warcraft"
    (root / "_constructed_" / "WTF" / "Account").mkdir(parents=True)
    return layout.Layout(root / "_constructed_", install_root=root)


@pytest.mark.parametrize(
    ("rel", "folder_row"),
    [
        pytest.param(f"Interface/{TEMP}", "interface-override", id="constructed-interface"),
        pytest.param(f"Interface/AddOns/{TEMP}", "addon", id="constructed-addons"),
        pytest.param(f"WTF/Account/90000001#1/{TEMP}", "realm-folder", id="constructed-account"),
        pytest.param(
            f"WTF/Account/90000001#1/R/{TEMP}", "character-folder", id="constructed-realm"
        ),
    ],
)
def test_a_gate_temp_name_keeps_real_one_constructed(
    flavor_tree: layout.Layout, rel: str, folder_row: str
) -> None:
    as_file = flavor_tree.classify(rel, is_dir=False)
    as_folder = flavor_tree.classify(rel, is_dir=True)
    assert isinstance(as_file, layout.Classified) and as_file.entry.id == "wowlab-temp"
    assert isinstance(as_folder, layout.Classified) and as_folder.entry.id == folder_row
    assert cli._link_stands_for(as_file, as_folder) == "one"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import platformdirs

    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    install = tmp_path / "wow"
    install.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install / ".build.info")
    shutil.copytree(CAPTURE / "forever", install / FLAVOR)
    monkeypatch.chdir(tmp_path)
    return install


def _notes(root: Path) -> dict[str, Any]:
    result = CliRunner().invoke(cli.app, ["tree", "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output
    return {i["path"]: i["note"] for i in json.loads(result.stdout)["items"]}


def _link(link: Path, tmp_path: Path) -> None:
    target = tmp_path / "shared" / link.name
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"constructed\n")
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def test_tree_note_for_a_linked_gate_temp_name_constructed(root: Path, tmp_path: Path) -> None:
    control = f"{ACCOUNT}/macros-cache.txt"
    (root / FLAVOR / control).unlink(missing_ok=True)
    _link(root / FLAVOR / control, tmp_path)
    rel = f"{ACCOUNT}/{TEMP}"
    _link(root / FLAVOR / rel, tmp_path)
    notes = _notes(root)
    assert notes[control].endswith(FILE_END), notes[control]  # positive control
    assert notes[rel].endswith(ONE_END), notes[rel]
