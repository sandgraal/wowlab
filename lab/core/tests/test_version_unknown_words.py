"""Every other place that says a flavor's version is unknown says what is
true in all three of `doctor`'s cases (M16-118), or the case itself where
it can tell (M16-121): its `.flavor.info` gives no product, `.build.info`
has no row for its product, or the row it has carries a blank version.

The install is the captured tree (`fixtures/macos/`): its `.build.info` and
its flavor folder, copied into `tmp_path` under the name its provenance row
gives. Constructed (L8, boundary), as in `test_doctor_version_words.py`: a
flavor folder whose product no `.build.info` row names, one whose
`.flavor.info` names no product, and one whose product's constructed
`.build.info` row has a blank Version cell. The user data directory is
redirected and the process table holds no client. Nothing reads or writes a
real install, and nothing asks the network: every refusal here comes first.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import psutil
import pytest
from typer.testing import CliRunner, Result

from wowlab_core import addoninstall, cli, firstrun, gamedata, install

CAPTURE = Path(__file__).resolve().parent / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
NO_ROW = "_old_beta_"  # constructed: no .build.info row names its product
NO_ROW_PRODUCT = "wow_constructed_no_row"
NO_PRODUCT = "_blank_"  # constructed: .flavor.info with the header and no product
BLANK_VERSION = "_no_version_"  # constructed: its product's row has a blank Version cell
BLANK_VERSION_PRODUCT = "wow_constructed_blank_version"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import platformdirs

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)


def _flavor_info(root: Path, folder: str, product: str) -> None:
    (root / folder).mkdir()
    (root / folder / ".flavor.info").write_bytes(f"Product Flavor!STRING:0\n{product}".encode())


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    _flavor_info(root, NO_ROW, f"{NO_ROW_PRODUCT}\n")
    _flavor_info(root, NO_PRODUCT, "")
    # A constructed row: 15 cells as in the captured header, Version (the
    # 13th) blank, Product last.
    cells = ["us", "1"] + [""] * 12 + [BLANK_VERSION_PRODUCT]
    with (root / ".build.info").open("ab") as out:
        out.write(("|".join(cells) + "\n").encode())
    _flavor_info(root, BLANK_VERSION, f"{BLANK_VERSION_PRODUCT}\n")
    return root


def _run(*args: str) -> Result:
    return CliRunner().invoke(cli.app, list(args), terminal_width=10_000)


# ─── the first-run section's flavor label ────────────────────────────────────


def test_flavor_label_says_version_unknown_in_every_case_constructed() -> None:
    assert firstrun.flavor_label(NO_ROW, NO_ROW_PRODUCT, None) == (
        f"{NO_ROW} ({NO_ROW_PRODUCT}, version unknown)"
    )
    assert (
        firstrun.flavor_label(NO_PRODUCT, "", None) == f"{NO_PRODUCT} (no product, version unknown)"
    )
    assert firstrun.flavor_label(FLAVOR, "wow_classic_beta", "1.60.1.69913") == (
        f"{FLAVOR} (wow_classic_beta, 1.60.1.69913)"
    )


# ─── doctor's Game data line ─────────────────────────────────────────────────


@pytest.mark.parametrize("folder", [NO_ROW, NO_PRODUCT, BLANK_VERSION])
def test_doctor_game_data_line_does_not_repeat_the_reason_constructed(
    root: Path, folder: str
) -> None:
    text = _run("doctor", "--offline", "--root", str(root), "--flavor", folder).stdout

    block = text.split(f"Flavor {folder}:", 1)[1].split("\nFlavor ", 1)[0] + "\n"
    assert "  Game data: no version to look up\n" in block
    assert "has no version for product" not in text
    assert "no version in .build.info" not in text


def test_doctor_json_game_data_detail_is_unchanged_constructed(root: Path) -> None:
    data = json.loads(_run("doctor", "--offline", "--root", str(root), "--json").stdout)

    by_folder = {f["folder"]: f for f in data["flavors"]}
    game_data = by_folder[NO_ROW]["game_data"]
    assert game_data["status"] == "no_version"
    assert game_data["detail"] == f".build.info has no version for product {NO_ROW_PRODUCT!r}"


# ─── the --build refusal ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("folder", "words"),
    [
        (
            NO_PRODUCT,
            f"{NO_PRODUCT}'s .flavor.info gives no product code, so its version is unknown; "
            "pass --build",
        ),
        (
            NO_ROW,
            f".build.info has no row for {NO_ROW}'s product {NO_ROW_PRODUCT}, so its version "
            "is unknown (wowlab doctor says why); pass --build",
        ),
        (
            BLANK_VERSION,
            f"the .build.info row for {BLANK_VERSION}'s product {BLANK_VERSION_PRODUCT} has "
            "no version in it; pass --build",
        ),
    ],
)
@pytest.mark.parametrize("command", ["fetch", "head"])
def test_db2_build_refusal_names_the_case_constructed(
    root: Path, folder: str, words: str, command: str
) -> None:
    result = _run("db2", command, "ChrClasses", "--root", str(root), "--flavor", folder)

    assert result.exit_code != 0
    assert words in result.stderr
    assert "no version in .build.info" not in result.stderr
    assert not (root.parent / "userdata").exists() or not any(
        (root.parent / "userdata").rglob("*.csv")
    ), "nothing was fetched"


def test_db2_build_refusal_escapes_a_hostile_product_constructed(tmp_path: Path) -> None:
    root = tmp_path / "hostile"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    _flavor_info(root, NO_ROW, "wow\x1b[31mred\n")

    result = _run("db2", "fetch", "ChrClasses", "--root", str(root), "--flavor", NO_ROW)

    assert "\x1b" not in result.stderr
    assert "product wow\\x1b[31mred, so its version is unknown" in result.stderr


# ─── the library's messages ──────────────────────────────────────────────────


def test_build_not_published_without_a_version_constructed() -> None:
    assert str(gamedata.BuildNotPublished(None, NO_ROW_PRODUCT)) == (
        f"the version of the flavor (product {NO_ROW_PRODUCT!r}) is unknown, so there is no "
        "build to look up"
    )
    assert str(gamedata.BuildNotPublished(None)) == (
        "the version of the flavor is unknown, so there is no build to look up"
    )


def test_interface_version_without_a_version_constructed() -> None:
    with pytest.raises(addoninstall.AddonVersionError) as caught:
        addoninstall.interface_version(None)
    assert str(caught.value) == (
        "the flavor's version is unknown, so the TOC's ## Interface: cannot be filled; "
        "nothing was changed"
    )


def test_addon_install_refusal_constructed(root: Path) -> None:
    result = _run("addon", "install", "lab", "--root", str(root), "--flavor", NO_ROW, "--yes")

    assert result.exit_code != 0
    assert (
        "the flavor's version is unknown, so the TOC's ## Interface: cannot be filled; "
        "nothing was changed"
    ) in result.stderr
    assert not (root / NO_ROW / "Interface").exists()


def test_doctor_lab_addon_line_for_an_unknown_version_constructed(root: Path) -> None:
    addon = root / NO_ROW / addoninstall.ADDON_FOLDER
    addon.mkdir(parents=True)
    (addon / addoninstall.TOC_NAME).write_bytes(b"## Interface: 16001\n")

    text = _run("doctor", "--offline", "--root", str(root), "--flavor", NO_ROW).stdout

    assert (
        "  Lab-addon: installed; wowlab cannot tell whether it fits this client (the "
        "flavor's version is unknown, so the TOC's ## Interface: cannot be filled)\n"
    ) in text
