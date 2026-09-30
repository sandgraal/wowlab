# Probe from review of m12/09-char-list; reproduces read_all refusing an exactly spelled WowLab.lua beside WowLab.LUA ("none spelled exactly so"), and char show --character reading another spelling than the file char list names
"""The branch's rule (docstring of `_read_one`, §14.4 amendment of
2026-09-30): "The name spelled as the addon spells it wins; case variants
of it alone are more than one candidate", "as `sv merge` refuses them"
(`cli._character_sv`, which compares the whole file name).

1. `_read_one` decides "spelled exactly" by `f.addon == ADDON_NAME`, the
   stem, not by the file name. `layout` gives `WowLab.LUA` the addon name
   `WowLab` too, so a `SavedVariables/` holding `WowLab.lua` and
   `WowLab.LUA` has two "exact" candidates: nothing is read and the error
   says "none spelled exactly so", which is false. `_character_sv` reads
   `WowLab.lua` there.

2. `CharacterFile.character` is documented as "what `wowlab char show
   --character` takes". With `WowLab.lua` and `WOWLAB.lua` both listed,
   `char list` reads `WowLab.lua` and `char show --character <that>` reads
   `WOWLAB.lua` (`_lab_char_file` takes the first match in path order, and
   `O` sorts before `o`). On a case-sensitive volume these are two files,
   and the two commands show two different records for one character
   (seen in review on a case-sensitive APFS image: spec 1490 in `char
   list`, 1482 in `char show`). The second test asks that they agree on the
   exact spelling; which side changes is the implementer's call.

Positive control: with the listing untouched, both commands name the same
`WowLab.lua` and `read_all` reads it.

Constructed (L8): the `macos` capture copied into `tmp_path`; the second
spelling is added to `layout`'s listing (as the branch's own
`test_constructed_case_variants_in_the_listing_are_refused_on_any_volume`
does), so the tests run on case-insensitive volumes too. Nothing reads or
writes a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon, layout

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)
FIRST = "1/Labchard-Labrealmg"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def _with_variant(monkeypatch: pytest.MonkeyPatch, spelling: str) -> None:
    """Every Layout lists `spelling` beside FIRST's WowLab.lua."""
    real = layout.Layout.saved_variables

    def listing(self: layout.Layout, scope: Any = None) -> tuple[layout.SavedVariablesFile, ...]:
        out: list[layout.SavedVariablesFile] = []
        for f in real(self, scope):
            if f.character_folder == FIRST.split("/")[1] and f.path.endswith("/WowLab.lua"):
                stem = spelling.rsplit(".", 1)[0]
                path = f.path[: -len("WowLab.lua")] + spelling
                variant = f.model_copy(update={"path": path, "addon": stem})
                # In the folder's name order, as the walk lists a real file.
                out.extend(sorted((f, variant), key=lambda g: g.path))
            else:
                out.append(f)
        return tuple(out)

    monkeypatch.setattr(layout.Layout, "saved_variables", listing)


def _entry(flavor: Path) -> labaddon.CharacterFile:
    by = {e.character: e for e in labaddon.read_all(layout.Layout(flavor))}
    return by[FIRST]


def _shown_file(character: str) -> str:
    result = runner.invoke(cli.app, ["char", "show", "--character", character, "--json"])
    assert result.exit_code == 0, result.output
    return cli.CharShowReport.model_validate_json(result.stdout).file


def test_constructed_positive_control_one_spelling(flavor: Path) -> None:
    entry = _entry(flavor)
    assert entry.record is not None and entry.file.endswith("/SavedVariables/WowLab.lua")
    assert _shown_file(entry.character) == entry.file


def test_constructed_exact_name_beside_an_upper_case_extension_is_read(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_variant(monkeypatch, "WowLab.LUA")
    listed = [f.path for f in layout.Layout(flavor).saved_variables("character")]
    assert any(p.endswith(f"{FIRST}/SavedVariables/WowLab.LUA") for p in listed)
    entry = _entry(flavor)
    assert entry.error is None, entry.error  # today: "... none spelled exactly so ..."
    assert entry.file.endswith("/SavedVariables/WowLab.lua")


def test_constructed_char_show_reads_the_file_char_list_names(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_variant(monkeypatch, "WOWLAB.lua")
    entry = _entry(flavor)
    assert entry.record is not None and entry.file.endswith("/SavedVariables/WowLab.lua")
    assert _shown_file(entry.character) == entry.file  # today: .../WOWLAB.lua
