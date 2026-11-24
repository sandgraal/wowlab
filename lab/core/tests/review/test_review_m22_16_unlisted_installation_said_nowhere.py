# Probe from review of m22/16-addon-provenance; reproduces `addons list` saying WowUp "does not manage this game folder" and "its list holds no addon" while it joins, by name, a record whose installation the settings do not list
"""`addonsource`'s docstring: "When the installations cannot be read (no
`preferences.json`, or a record whose installation is not listed), records
are joined by folder name alone, and the report says so." When
`preferences.json` lists only another game folder and a record names an
installation it does not list, `_scope` gives `manages=False` and
`by_name=1`, and `match_sources` joins that record by name, so the folder
line says "from Wago through WowUp". But `_scope_lines` (and the page's
`sourcesScope`) return early on `manages is False`. The first line says
WowUp does not manage this game folder and that its list holds no addon,
and nothing says the record was matched by name.

Constructed: an invented install (one flavor folder, `_x_`, one addon
`Foo`) and an invented WowUp list and settings. The positive control has
no settings file: the by-name line is said there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wowlab_core import addonsource, cli, install

runner = CliRunner()
RECORD = {
    "r1": {
        "name": "Foo",
        "providerName": "Wago",
        "installedVersion": "1.0",
        "installedFolderList": ["Foo"],
        "installationId": "GONE",
    }
}


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, settings: bool) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(addonsource, "wowup_folder", lambda platform=None: home / "WowUp")
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    root = tmp_path / "World of Warcraft"
    addon = root / "_x_" / "Interface" / "AddOns" / "Foo"
    addon.mkdir(parents=True)
    (addon / "Foo.toc").write_text("## Interface: 11500\n## Version: 1.0\n", encoding="utf-8")
    (root / ".build.info").write_bytes(
        (Path(__file__).resolve().parents[1] / "fixtures" / "macos" / ".build.info").read_bytes()
    )
    (root / "_x_" / ".flavor.info").write_bytes(
        (
            Path(__file__).resolve().parents[1] / "fixtures" / "macos" / "forever" / ".flavor.info"
        ).read_bytes()
    )
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    folder = addonsource.wowup_folder()
    assert home in folder.parents
    folder.mkdir(parents=True)
    (folder / "addons.json").write_text(json.dumps(RECORD), encoding="utf-8")
    if settings:
        other = {"wow_installations": [{"id": "B", "location": str(root / "_y_" / "C.app")}]}
        (folder / "preferences.json").write_text(json.dumps(other), encoding="utf-8")


def _out() -> str:
    result = runner.invoke(cli.app, ["addons", "list", "--flavor", "_x_"], terminal_width=200)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result.stdout


def test_constructed_control_no_settings_says_matched_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, settings=False)
    out = _out()
    assert "from Wago through WowUp" in out
    assert "matched to these addons by folder name" in out


def test_constructed_an_unlisted_installation_is_said_and_not_contradicted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, settings=True)
    out = _out()
    assert "from Wago through WowUp" in out  # the record is joined by name
    assert "its list holds no addon" not in out, out
    assert "matched to these addons by folder name" in out, out
