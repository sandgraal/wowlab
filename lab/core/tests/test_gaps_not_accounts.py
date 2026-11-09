"""`filemap gaps` and symlink entry ids below a folder of `WTF/Account/` that
is no account folder (M13-69).

Found in the code review of #280 (M13-50): `explain`, `tree --explain` and
the inventory walk give no row to a file below such a folder whose name has
an account row (`config-cache.wtf` in the client's own `SavedVariables/`),
while `gaps` and `Symlink.entry_id` still gave it that row, so `filemap
gaps` said "every path the walk reached has a row" where `explain` said
"no file-map row". Both now honour the walk's `not_accounts`.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
with its flavor folder named as its provenance row says (`_classic_beta_`).
Every test here is `constructed` (L8): the client's folder, the stray file
in it, the old-style folder and the links are added by the test to that
copy. Nothing reads or writes a real install: `WOWLAB_WOW_ROOT` points at
the copy, and the user data directory is redirected into `tmp_path`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gaps, install, layout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
CLIENT_DIR = "WTF/Account/SavedVariables"
OLD_DIR = "WTF/Account/LEGACYACCT"  # old-style name, no character folder yet
SYMLINKS = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def flavor(root: Path) -> Path:
    return root / FLAVOR


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _client(flavor: Path, *, stray: bool) -> None:
    """Constructed: the client's folder as the owner's listing shows it, and
    (`stray`) a `config-cache.wtf` in it, a name with an account row."""
    for name in ("Blizzard_SavedSets.lua", "Blizzard_SavedSets.lua.bak"):
        _write(flavor / CLIENT_DIR / name, b"SavedSetsSaved = {}\n")
    if stray:
        _write(flavor / CLIENT_DIR / "config-cache.wtf", b'SET foo "1"\n')


def _gaps(root: Path) -> gaps.GapReport:
    return gaps.find_gaps(install.discover(root))


def _places(report: gaps.GapReport) -> dict[str, str | None]:
    return {p.path: g.parent_entry_id for g in report.groups for p in g.gaps}


def _explained(path: str) -> layout.Classified | layout.Unclassified:
    result = runner.invoke(cli.app, ["explain", path, "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    return cli.ExplainReport.model_validate_json(result.stdout).result


def test_constructed_stray_config_cache_in_the_client_folder_is_a_gap(
    root: Path, flavor: Path
) -> None:
    _client(flavor, stray=True)
    stray = f"{CLIENT_DIR}/config-cache.wtf"
    # `explain` gives it no row (M13-50) ...
    assert layout.below_no_account(_explained(stray))
    # ... and `gaps` now agrees: one gap, under the client folder's row.
    report = _gaps(root)
    assert _places(report) == {stray: "client-savedvariables-folder"}
    assert report.files == 1
    text = runner.invoke(cli.app, ["filemap", "gaps"]).stdout
    assert "every path the walk reached has a row" not in text
    assert "config-cache.wtf" in text
    # The same name in the account folder keeps its row.
    real = _explained(f"WTF/Account/{ACCOUNT}/config-cache.wtf")
    assert isinstance(real, layout.Classified) and real.entry.id == "account-config-cache"


def test_constructed_client_folder_without_a_stray_has_no_gap(root: Path, flavor: Path) -> None:
    # What the client writes there (`Blizzard_*.lua`, `.lua.bak`): the two agree.
    _client(flavor, stray=False)
    report = _gaps(root)
    assert report.groups == []
    for name in ("Blizzard_SavedSets.lua", "Blizzard_SavedSets.lua.bak"):
        assert isinstance(_explained(f"{CLIENT_DIR}/{name}"), layout.Classified)


def test_constructed_old_style_folder_is_a_gap_as_explain_says(root: Path, flavor: Path) -> None:
    _write(flavor / OLD_DIR / "config-cache.wtf", b'SET foo "1"\n')
    assert layout.below_no_account(_explained(f"{OLD_DIR}/"))
    report = _gaps(root)
    (group,) = report.groups
    assert (group.name, group.kind, group.parent_entry_id, group.files) == (
        "LEGACYACCT",
        "folder",
        "accounts-folder",
        1,
    )


@SYMLINKS
def test_constructed_link_below_a_folder_that_is_no_account_takes_no_account_row(
    root: Path, flavor: Path
) -> None:
    _client(flavor, stray=False)
    account = flavor / "WTF" / "Account" / ACCOUNT
    (flavor / OLD_DIR).mkdir(parents=True)
    target = account / "config-cache.wtf"
    (flavor / CLIENT_DIR / "config-cache.wtf").symlink_to(target)
    (flavor / OLD_DIR / "config-cache.wtf").symlink_to(target)
    # In the account folder, a link with an account row's name keeps it.
    target.rename(account / "config-cache.real")
    target.symlink_to(account / "config-cache.real")
    lay = layout.Layout.for_flavor(install.discover(root).flavors[0], root)
    for walk in (lay.inventory(), lay.wtf_walk()):
        assert walk.not_accounts == (OLD_DIR, CLIENT_DIR)
        ids = {s.path: s.entry_id for s in walk.symlinks}
        assert ids == {
            f"{CLIENT_DIR}/config-cache.wtf": None,
            f"{OLD_DIR}/config-cache.wtf": None,
            f"WTF/Account/{ACCOUNT}/config-cache.wtf": "account-config-cache",
        }
        # As `Layout.classify` gives them with the walk's list.
        names = [p.rsplit("/", 1)[-1] for p in walk.not_accounts]
        for path, entry_id in ids.items():
            found = lay.classify(path, not_accounts=names)
            assert (found.entry.id if isinstance(found, layout.Classified) else None) == entry_id
