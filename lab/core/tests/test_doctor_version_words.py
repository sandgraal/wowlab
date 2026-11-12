"""`doctor` says in words why a flavor's version is unknown when
`.build.info` has no row for its product (M16-118).

The install is the captured tree (`fixtures/macos/`): its `.build.info`
and its flavor folder, copied into `tmp_path` under the name its provenance
row gives. Constructed (L8, boundary): a second flavor folder, `_forever_`,
holding only a `.flavor.info` whose product no `.build.info` row names, as
on the owner's macOS install of 2026-11-11 (`_classic_beta_` beside
`_forever_`, one of them with no row). A second constructed folder's
`.flavor.info` names no product at all. The user data directory is
redirected and the process table holds no client. Nothing reads or writes a
real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

CAPTURE = Path(__file__).resolve().parent / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
NO_ROW = "_forever_"  # constructed
NO_ROW_PRODUCT = "wow_constructed_no_row"  # constructed: no .build.info row names it
NO_PRODUCT = "_blank_"  # constructed: .flavor.info with the header and no product

WORDS = (
    f"Flavor {NO_ROW}: product {NO_ROW_PRODUCT}, version unknown\n"
    "  The version is unknown because .build.info, the Battle.net app's list of the "
    f"game versions it has installed, has no row for product {NO_ROW_PRODUCT}. That "
    "happens when this folder was left behind by a game version the app no longer "
    "lists (a beta that ended or was renamed, or one uninstalled) or one it has not "
    "finished installing. wowlab still reads this folder, and this alone does not mean "
    "the install is damaged.\n"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import platformdirs

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)


def _install(tmp_path: Path, *, blank: bool = False) -> Path:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    (root / NO_ROW).mkdir()
    (root / NO_ROW / ".flavor.info").write_bytes(
        f"Product Flavor!STRING:0\n{NO_ROW_PRODUCT}\n".encode()
    )
    if blank:
        (root / NO_PRODUCT).mkdir()
        (root / NO_PRODUCT / ".flavor.info").write_bytes(b"Product Flavor!STRING:0\n")
    return root


def _doctor(root: Path, *extra: str) -> str:
    result = CliRunner().invoke(
        cli.app,
        ["doctor", "--offline", "--root", str(root), *extra],
        terminal_width=10_000,
    )
    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    return result.stdout


def test_a_flavor_with_no_build_info_row_says_why_its_version_is_unknown_constructed(
    tmp_path: Path,
) -> None:
    text = _doctor(_install(tmp_path))

    assert WORDS in text
    # The old words are gone from doctor's flavor lines (the first-run
    # section's flavor label, `firstrun.flavor_label`, is not this ticket's).
    assert f"{NO_ROW}: product {NO_ROW_PRODUCT}, version no version" not in text
    # The flavor that has a row keeps its version line as before.
    assert f"Flavor {FLAVOR}: product wow_classic_beta, version 1." in text


def test_a_flavor_info_with_no_product_says_so_constructed(tmp_path: Path) -> None:
    text = _doctor(_install(tmp_path, blank=True))

    assert (
        f"Flavor {NO_PRODUCT}: product (none), version unknown\n"
        "  The version is unknown because this folder's .flavor.info names no product, "
        "so no row of .build.info can match it.\n"
    ) in text


def test_json_still_says_version_null_constructed(tmp_path: Path) -> None:
    data = json.loads(_doctor(_install(tmp_path), "--json"))

    by_folder = {f["folder"]: f for f in data["flavors"]}
    assert by_folder[NO_ROW]["version"] is None
    assert by_folder[NO_ROW]["product"] == NO_ROW_PRODUCT
