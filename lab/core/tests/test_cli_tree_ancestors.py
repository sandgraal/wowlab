"""`tree --json` lists the folders above its items, and `tree`/`explain`
carry a file-map row's text without Markdown (M13-25, JSON only).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
with its flavor folder named as its provenance row says (`_classic_beta_`),
and `--root` pointing at the copy. Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wowlab_core import cli, filemap

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    install = tmp_path / "wow"
    install.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install / ".build.info")
    shutil.copytree(CAPTURE / "forever", install / FLAVOR)
    return install


def _run(*args: str) -> str:
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def _tree(root: Path, *more: str) -> Any:
    return json.loads(_run("tree", "--root", str(root), "--json", *more))


def test_every_folder_above_an_item_is_listed_with_its_verdict(root: Path) -> None:
    report = cli.TreeReport.model_validate(_tree(root, "--explain"))
    items = {i.path for i in report.items}
    ancestors = {a.path: a for a in report.ancestors}
    for path in items:
        parts = path.split("/")
        for n in range(1, len(parts)):
            above = "/".join(parts[:n])
            assert above in items or above in ancestors, above
    assert not set(ancestors) & items
    assert [a.path for a in report.ancestors] == sorted(ancestors)
    for path in ("Interface/AddOns", "WTF/Account", "WTF/Account/90000001#6/SavedVariables"):
        folder = ancestors[path]
        assert folder.kind == "folder"
        assert folder.verdict.level == "folder"
        assert folder.verdict.label == "folder"
        assert folder.explained is not None
    assert ancestors["Interface/AddOns"].explained is not None
    assert ancestors["Interface/AddOns"].explained.entry_id == "addons-folder"
    assert ancestors["WTF/Account"].explained is not None
    assert ancestors["WTF/Account"].explained.entry_id == "accounts-folder"
    assert report.flavor_folder_verdict.level == "folder"


def test_ancestors_stay_inside_the_asked_path(root: Path) -> None:
    report = _tree(root, "WTF/Account/90000001#6")
    for folder in report["ancestors"]:
        assert folder["path"].startswith("WTF/Account/90000001#6/"), folder["path"]
    assert all(a["explained"] is None for a in _tree(root)["ancestors"])


def test_the_text_output_has_no_ancestors(root: Path) -> None:
    text = _run("tree", "--root", str(root), "--explain")
    assert "\n  Interface/AddOns/\n" not in text
    assert "\n  WTF/Account/\n" not in text


def test_plain_text_drops_markdown_and_names_the_verify_mark() -> None:
    entries = filemap.load().entries
    for entry in entries:
        plain = cli._plain_text(entry)
        for text in (plain.what, plain.written_by):
            assert "`" not in text and "**" not in text and "[verify" not in text, entry.id
    account = filemap.load().entry("account-folder")
    assert "(not yet confirmed)" in cli._plain_text(account).what
    assert cli._player_text("a **[verify purpose]** b") == "a (purpose not yet confirmed) b"
    assert cli._player_text("`## SavedVariables:` **x**") == "## SavedVariables: x"


def test_explain_json_carries_plain_text(root: Path) -> None:
    report = json.loads(_run("explain", "--root", str(root), "--json", "WTF/Account/90000001#6"))
    assert "(not yet confirmed)" in report["plain"]["what"]
    assert "**" in report["result"]["entry"]["what"]  # the row itself is unchanged
    unknown = json.loads(_run("explain", "--root", str(root), "--json", "WTF/zzz-constructed"))
    assert unknown["plain"] is None
    assert unknown["verdict"]["label"] == "unknown"


def test_tree_items_carry_plain_text(root: Path) -> None:
    for item in _tree(root, "--explain")["items"]:
        if item["explained"] is not None:
            plain = item["explained"]["plain"]
            assert "`" not in plain["what"] and "**" not in plain["what"], item["path"]
