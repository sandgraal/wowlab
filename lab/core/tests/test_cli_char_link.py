"""`wowlab char link set|list|forget` and `char gear`'s `saved_links` (M21-03).

On the Forever launch client's capture (`macos-70888/`, flavor folder
`_forever_` as its provenance row names it, one character
`1/Sandgraal-Rizzen`) copied into `tmp_path`, with the user data directory
redirected there. The addresses are the owner's two recorded
ForeverTwinking examples (M21-O1) and a Warcraft Logs report address of the
ticket's shape (its code made for the test). Every case checks that the
install's bytes are unchanged (L1): only wowlab's own `links.json` is
written. Hostile pastes are `constructed`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_forever_"  # macos-70888's provenance row; tests may name it (L6)
CHARACTER = "1/Sandgraal-Rizzen"
ACCOUNT = "LABACCOUNTA"
FT = "https://forevertwinking.com/armory/13-sandgraal-rizzen"
BUILD = "https://forevertwinking.com/builds/dwhe4ngw2p"
WCL = "https://forever.warcraftlogs.com/reports/aBcD1234eFgH5678"

runner = CliRunner()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    return wow


def _state(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _store(tmp_path: Path) -> Path:
    return tmp_path / "userdata" / "wowlab" / "links.json"


def run(*args: str, stdin: str | None = None) -> Any:
    return runner.invoke(cli.app, list(args), input=stdin)


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _set(url: str, *extra: str) -> Any:
    return ok("char", "link", "set", url, "--character", "Sandgraal-Rizzen", "--yes", *extra)


def test_set_list_and_forget_change_only_wowlabs_own_file(root: Path, tmp_path: Path) -> None:
    before = _state(root)
    _set(FT)
    _set(BUILD)
    _set(WCL + "?fight=3")
    listed = json.loads(ok("char", "link", "list", "--json").stdout)
    assert listed["file"] == str(_store(tmp_path))
    assert [(r["site"], r["kind"], r["url"]) for r in listed["links"]] == [
        ("forevertwinking", "profile", FT),
        ("forevertwinking", "build", BUILD),
        ("warcraftlogs", "report", WCL),
    ]
    assert {(r["flavor_folder"], r["account"], r["character"]) for r in listed["links"]} == {
        (FLAVOR, ACCOUNT, CHARACTER)
    }
    assert listed["refused"] == []
    done = json.loads(
        ok(
            "char", "link", "forget", "--site", "forevertwinking", "--kind", "build",
            "--character", "Sandgraal-Rizzen", "--yes", "--json",
        ).stdout
    )  # fmt: skip
    assert done["changed"] is True and done["dry_run"] is False
    assert [r["url"] for r in done["dropped"]] == [BUILD]
    assert [r["url"] for r in done["links"]] == [FT, WCL]
    assert _state(root) == before


def test_a_dry_run_shows_the_plan_and_writes_nothing(root: Path, tmp_path: Path) -> None:
    before = _state(root)
    plan = json.loads(
        ok(
            "char", "link", "set", FT, "--character", "Sandgraal-Rizzen", "--dry-run", "--json"
        ).stdout
    )
    assert plan["dry_run"] is True and plan["changed"] is True
    assert plan["plan_lines"] == [f"ForeverTwinking for {CHARACTER} in account {ACCOUNT}: {FT}"]
    assert plan["saved"]["url"] == FT and plan["links"] == []
    assert not _store(tmp_path).exists()
    assert _state(root) == before


def test_without_yes_it_asks_and_a_no_writes_nothing(root: Path, tmp_path: Path) -> None:
    result = run("char", "link", "set", FT, "--character", "Sandgraal-Rizzen", stdin="n\n")
    assert result.exit_code == 1
    assert "Nothing was changed." in result.stderr
    assert not _store(tmp_path).exists()


def test_the_same_address_again_changes_nothing(root: Path, tmp_path: Path) -> None:
    _set(FT)
    written = _store(tmp_path).read_bytes()
    again = json.loads(_set(FT, "--json").stdout)
    assert again["changed"] is False and "nothing would change" in again["plan_lines"][0]
    assert _store(tmp_path).read_bytes() == written


def test_a_newer_address_replaces_the_old(root: Path) -> None:
    _set(FT)
    newer = "https://forevertwinking.com/armory/14-sandgraal-rizzen"
    done = json.loads(_set(newer, "--json").stdout)
    assert [r["url"] for r in done["dropped"]] == [FT]
    assert [r["url"] for r in done["links"]] == [newer]


@pytest.mark.parametrize(
    "address",
    [
        "https://forevertwinking.com/armory/sandgraal-rizzen",
        "https://evil.example/armory/13-sandgraal-rizzen",
        "http://forevertwinking.com/armory/13-sandgraal-rizzen",
        "https://u:p@forevertwinking.com/armory/13-sandgraal-rizzen",
        "javascript:alert(1)",
        "-x",
    ],
    ids=lambda a: f"constructed: {a!r}",
)
def test_an_address_of_another_shape_is_refused_constructed(
    root: Path, tmp_path: Path, address: str
) -> None:
    result = run("char", "link", "set", "--character", "Sandgraal-Rizzen", "--yes", "--", address)
    assert result.exit_code == 2, result.stdout
    assert "not kept" in result.stderr
    assert not _store(tmp_path).exists()


def test_a_named_site_must_match_the_address(root: Path) -> None:
    result = run(
        "char", "link", "set", FT, "--site", "warcraftlogs", "--character", "Sandgraal-Rizzen",
        "--yes",
    )  # fmt: skip
    assert result.exit_code == 2
    code = run(
        "char", "link", "set", "aBcD1234eFgH5678", "--site", "warcraftlogs",
        "--character", "Sandgraal-Rizzen", "--yes", "--json",
    )  # fmt: skip
    assert code.exit_code == 0 and json.loads(code.stdout)["saved"]["url"] == WCL


def test_a_character_not_in_the_game_folder_is_refused(root: Path, tmp_path: Path) -> None:
    result = run("char", "link", "set", FT, "--character", "Nobody-Here", "--yes")
    assert result.exit_code == 2 and "no character folder" in result.stderr
    assert not _store(tmp_path).exists()


def test_list_of_one_character_and_text_output(root: Path) -> None:
    _set(FT)
    text = ok("char", "link", "list", "--character", "Sandgraal-Rizzen").stdout
    assert FT in text and "ForeverTwinking" in text
    assert ok("char", "link", "list").stdout.count(FT) == 1


def test_char_gear_carries_the_saved_links(root: Path) -> None:
    before = json.loads(ok("char", "gear", "--json").stdout)
    assert before["saved_links"] == []
    _set(FT)
    _set(WCL)
    report = json.loads(ok("char", "gear", "--json").stdout)
    assert [(r["label"], r["url"]) for r in report["saved_links"]] == [
        ("ForeverTwinking", FT),
        ("Warcraft Logs report", WCL),
    ]
    assert list(report)[-2:] == ["flavor_folder", "dressing"]
    text = ok("char", "gear").stdout
    assert f"ForeverTwinking: {FT}" in text


def test_an_entry_edited_by_hand_is_listed_apart_and_never_in_gear_constructed(
    root: Path, tmp_path: Path
) -> None:
    _set(FT)
    path = _store(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["characters"][0]["links"][0]["url"] = "https://evil.example/armory/13-x"
    path.write_text(json.dumps(data), encoding="utf-8")
    listed = json.loads(ok("char", "link", "list", "--json").stdout)
    assert listed["links"] == [] and len(listed["refused"]) == 1
    report = json.loads(ok("char", "gear", "--json").stdout)
    assert report["saved_links"] == []


def test_a_damaged_file_does_not_stop_char_gear_constructed(root: Path, tmp_path: Path) -> None:
    path = _store(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    report = json.loads(ok("char", "gear", "--json").stdout)
    assert report["saved_links"] == []
    assert any("could not be read" in n for n in report["notes"])
    assert run("char", "link", "list").exit_code == 1


def test_an_address_kept_for_a_folder_that_is_gone_is_listed_so(root: Path, tmp_path: Path) -> None:
    """Domain review, M21-03: a renamed character folder leaves its address
    under the old folder; `list` says so instead of hiding it (constructed:
    the folder renamed in the copy)."""
    _set(FT)
    folder = root / FLAVOR / "WTF" / "Account" / ACCOUNT / "1"
    (folder / "Sandgraal-Rizzen").rename(folder / "Sandgraal-Renamed")
    listed = json.loads(ok("char", "link", "list", "--json").stdout)
    assert [r["url"] for r in listed["links"]] == [FT]
    assert [r["url"] for r in listed["gone"]] == [FT]
    text = ok("char", "link", "list").stdout
    assert "Kept for a character folder that is gone" in text and FT in text
    # The file is untouched by listing.
    stored = json.loads(_store(tmp_path).read_text(encoding="utf-8"))
    assert stored["characters"][0]["key"]["character"] == CHARACTER


def test_set_help_says_the_address_belongs_to_the_folder(root: Path) -> None:
    text = " ".join(ok("char", "link", "set", "--help").stdout.split())
    assert "belongs to this character's folder" in text
