# Probe from review of m13/50-account-folders; reproduces a stray WTF/Account/SavedVariables/ folder still explained as an account folder, and its files as account files, beside the branch's "not an account folder" note.
"""M13-50 acceptance: on a constructed tree with a stray folder under
`WTF/Account/`, "the files under it are reported as what they are" in
`layout`, `sv list`, `tree` and `explain`.

At 2688b8c / ad5a6a4 the layout drops the stray folder from the accounts and
gives each file below it scope `other` and no account, and `tree` notes the
folder as "not an account folder". But the file-map verdict carried beside
that note still says the opposite:

- `tree --explain --json` (the UI explorer's source): the stray folder's row
  has `note` "not an account folder: ..." and `explained.entry_id`
  "account-folder" ("One folder per game account that logged in on this
  machine") in the same row;
- `layout.WtfFile` for `WTF/Account/SavedVariables/config-cache.wtf` is
  scope `other`, account None, entry_id "account-config-cache" (an
  account-scoped row for a file the layout says belongs to no account). The
  precedent on main for a stray below an account (a `SavedVariables/` folder
  at realm depth, `test_layout.py`) is scope `other` with entry_id None.

Expected: no output names the stray folder an account folder while another
field of the same row says it is not one, and a file the layout puts in no
account carries no account-scoped file-map row. The positive controls show
the real account's rows keep "account folder" / "account-config-cache".

CONSTRUCTED INPUT: the captured `fixtures/macos/` tree copied into
`tmp_path` (flavor folder named as its provenance row says), plus a stray
`WTF/Account/SavedVariables/` folder this probe adds. No real install, no
network (ADR-0012).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, layout

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
STRAY_DIR = "WTF/Account/SavedVariables"

runner = CliRunner()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    stray = root / FLAVOR / STRAY_DIR
    stray.mkdir(parents=True)
    (stray / "config-cache.wtf").write_bytes(b'SET foo "1"\n')
    (stray / "SavedVariables.lua").write_bytes(b"x = 1\n")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _layout(root: Path) -> layout.Layout:
    inst = install.discover(root)
    return layout.Layout.for_flavor(inst.flavors[0], inst.root)


def test_constructed_tree_explain_row_does_not_contradict_itself(root: Path) -> None:
    result = runner.invoke(cli.app, ["tree", "--explain", "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    items = {i.path: i for i in cli.TreeReport.model_validate_json(result.stdout).items}
    real = items[f"WTF/Account/{ACCOUNT}"]
    # Positive control: the real account folder is an account folder in both fields.
    assert real.note == "account folder"
    assert real.explained is not None and real.explained.entry_id == "account-folder"
    stray = items[STRAY_DIR]
    assert stray.note is not None and stray.note.startswith("not an account folder")
    entry = stray.explained.entry_id if stray.explained is not None else None
    assert entry != "account-folder", (stray.note, entry)


def test_constructed_no_account_file_has_no_account_row(root: Path) -> None:
    files = {f.path: f for f in _layout(root).wtf_files()}
    real = files[f"WTF/Account/{ACCOUNT}/config-cache.wtf"]
    # Positive control: the real account's file is account-scoped by both fields.
    assert (real.scope, real.account, real.entry_id) == ("account", ACCOUNT, "account-config-cache")
    stray = files[f"{STRAY_DIR}/config-cache.wtf"]
    assert (stray.scope, stray.account) == ("other", None)
    assert stray.entry_id is None or not stray.entry_id.startswith("account-"), stray.entry_id
