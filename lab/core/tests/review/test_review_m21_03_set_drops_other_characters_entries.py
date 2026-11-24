# Probe from review of m21/03-links; reproduces `char link set` for one character silently deleting another character's saved entry that today's shapes do not show.
"""`linkstore.read` leaves out (and lists in `refused`) a saved entry that
no longer checks out against `link_sites.toml`, and `char link list` shows
it "apart" ("Not shown: ..."), so the file still holds it. `char link set`
then writes back the *filtered* file: a change for one character drops every
refused entry of every other character, and its plan does not say so.

Constructed (L8): a hand-made `links.json` whose one entry for another
character is a ForeverTwinking build code one character longer than today's
shape (as a later or earlier wowlab's shapes could make it). The positive
control is that character's entry that does check out, which survives.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FLAVOR = "_forever_"  # macos-70888's provenance row; tests may name it (L6)
OTHER = {"flavor": FLAVOR, "account": "LABACCOUNTA", "character": "1/Other-Char"}
KEPT_URL = "https://forevertwinking.com/armory/14-other-char"
REFUSED_URL = "https://forevertwinking.com/builds/" + "a" * 33  # one past today's {4,32}


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(
        platformdirs, "user_config_path", lambda *a, **k: tmp_path / "userconfig" / "wowlab"
    )
    wow = tmp_path / "World of Warcraft"
    wow.mkdir()
    shutil.copy2(FIXTURES / "macos-70888" / ".build.info", wow / ".build.info")
    shutil.copytree(FIXTURES / "macos-70888" / "forever", wow / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(wow))
    monkeypatch.chdir(tmp_path)
    data.mkdir(parents=True)
    path = data / "links.json"
    entry = {"saved_at": "2026-11-23T00:00:00Z", "site": "forevertwinking"}
    path.write_text(
        json.dumps(
            {
                "format": 1,
                "characters": [
                    {
                        "key": OTHER,
                        "links": [
                            {**entry, "kind": "profile", "url": KEPT_URL},
                            {**entry, "kind": "build", "url": REFUSED_URL},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _urls(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [link["url"] for c in data["characters"] for link in c["links"]]


def test_list_shows_the_refused_entry_apart(store: Path) -> None:
    out = CliRunner().invoke(cli.app, ["char", "link", "list", "--json"])
    assert out.exit_code == 0, out.output
    report = json.loads(out.stdout)
    assert [r["url"] for r in report["links"]] == [KEPT_URL]
    assert [(r["character"], r["kind"]) for r in report["refused"]] == [("1/Other-Char", "build")]


def test_set_for_one_character_keeps_another_characters_entries(store: Path) -> None:
    args = ["char", "link", "set", "https://forevertwinking.com/builds/dwhe4ngw2p"]
    out = CliRunner().invoke(cli.app, [*args, "--character", "Sandgraal-Rizzen", "--yes"])
    assert out.exit_code == 0, out.output
    urls = _urls(store)
    assert "https://forevertwinking.com/builds/dwhe4ngw2p" in urls  # the change was made
    assert KEPT_URL in urls  # positive control: the other character's good entry stays
    # The defect: the other character's entry that is only "not shown" is gone.
    assert REFUSED_URL in urls
