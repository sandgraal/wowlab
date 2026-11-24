"""`wowlab blizzard` (M22-11): login, logout, status, fetch and show.

The install is the `macos-70888` capture (`.build.info` and its flavor
folder, whose `Config.wtf` names the region `US`), copied into `tmp_path`.
The user data directory is redirected, every socket is refused, and
Blizzard's answers are the **constructed** replies of `test_bnet.py`
(provenance there), handed to the client through `bnet.urllib_send`. No
test asks Blizzard anything (ADR-0012), and none reads or writes a real
install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
import test_bnet as recorded
from test_bnet import no_network  # noqa: F401  (autouse: every socket refused)
from typer.testing import CliRunner

from wowlab_core import bnet, cli, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAUNCH = FIXTURES / "macos-70888"
FOREVER = "_forever_"
NAME = "Sandgraal-Rizzen"
SECRET = recorded.SECRET
TOKEN = recorded.TOKEN
CLIENT_ID = recorded.CLIENT_ID

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    return redirected


@pytest.fixture(autouse=True)
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    found = tmp_path / "World of Warcraft"
    found.mkdir()
    shutil.copy2(LAUNCH / ".build.info", found / ".build.info")
    shutil.copytree(LAUNCH / "forever", found / FOREVER)
    monkeypatch.setenv(install.ENV_ROOT, str(found))
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    monkeypatch.chdir(tmp_path)
    return found


@pytest.fixture
def blizzard(monkeypatch: pytest.MonkeyPatch) -> recorded.Recorded:
    """Blizzard's answers, recorded (constructed); every request kept."""
    send = recorded.Recorded(recorded.answers())
    monkeypatch.setattr(bnet, "urllib_send", send)
    return send


def run(*args: str, stdin: str | None = None) -> Any:
    return runner.invoke(cli.app, ["blizzard", *args], input=stdin)


def login() -> None:
    done = run("login", "--client-id", CLIENT_ID, stdin=SECRET + "\n")
    assert done.exit_code == 0, done.output


def _clean(*texts: str) -> None:
    for text in texts:
        assert SECRET not in text and TOKEN not in text, text


# ─── without an API client ───────────────────────────────────────────────────


def test_status_without_a_client_says_how_to_add_one(blizzard: recorded.Recorded) -> None:
    done = run("status")
    assert done.exit_code == 0
    said = " ".join(done.stdout.split())
    assert "develop.battle.net" in said and "wowlab blizzard login" in said
    assert "asks Blizzard for nothing" in said
    report = json.loads(run("status", "--json").stdout)
    assert report["configured"] is False and report["setup"] == bnet.SETUP_WORDS
    assert blizzard.requests == []


def test_fetch_without_a_client_asks_nothing_and_says_how(blizzard: recorded.Recorded) -> None:
    done = run("fetch", NAME)
    assert done.exit_code == 1
    assert "develop.battle.net" in " ".join(done.stderr.split())
    done = run("fetch", NAME, "--json")
    assert done.exit_code == 1
    error = json.loads(done.stdout)
    assert error["message"] == bnet.SETUP_WORDS
    assert blizzard.requests == []


def test_show_without_a_client_says_so_and_asks_nothing(blizzard: recorded.Recorded) -> None:
    report = json.loads(run("show", NAME, "--json").stdout)
    assert report["configured"] is False and report["profile"] is None
    assert report["setup"] == bnet.SETUP_WORDS and report["fetched"] is False
    assert (report["region"], report["game"], report["name"]) == (
        "us",
        "forever",
        "Sandgraal Rizzen",
    )
    text = run("show", NAME).stdout
    assert "nothing fetched from Blizzard yet" in text and "develop.battle.net" in text
    assert blizzard.requests == []


# ─── login and logout ────────────────────────────────────────────────────────


def test_login_keeps_the_client_and_never_prints_the_secret(user_data: Path) -> None:
    done = run("login", "--client-id", CLIENT_ID, "--json", stdin=SECRET + "\n")
    assert done.exit_code == 0, done.output
    _clean(done.stdout, done.stderr)
    report = json.loads(done.stdout)
    assert report == {
        "saved": True,
        "credentials_path": str(user_data / "blizzard" / "client.json"),
    }
    assert CLIENT_ID not in done.stdout
    status = json.loads(run("status", "--json").stdout)
    assert status["configured"] is True and status["setup"] is None
    _clean(json.dumps(status))
    # Both asked for when no option is given; the secret's prompt hides it.
    done = run("login", stdin=f"{CLIENT_ID}\n{SECRET}\n")
    assert done.exit_code == 0, done.output
    assert "Client ID" in done.stderr and "Client secret" in done.stderr
    _clean(done.stdout, done.stderr)


def test_the_secret_is_never_an_option() -> None:
    done = run("login", "--client-secret", SECRET)
    assert done.exit_code == 2
    help_text = run("login", "--help").stdout
    assert "--client-secret" not in help_text and "--secret" not in help_text


@pytest.mark.parametrize(
    "secret",
    ["has space inside 1234", "short", ""],
    ids=lambda s: f"constructed: {s!r}",
)
def test_a_secret_of_another_shape_is_refused_and_not_quoted(secret: str, user_data: Path) -> None:
    done = run("login", "--client-id", CLIENT_ID, stdin=secret + "\n")
    assert done.exit_code == 1
    if secret:
        assert secret not in done.stderr
    assert not (user_data / "blizzard" / "client.json").exists()


def test_no_secret_given_saves_nothing(user_data: Path) -> None:
    done = run("login", "--client-id", CLIENT_ID, stdin="")
    assert done.exit_code == 1
    assert "nothing was saved" in done.stderr
    assert not (user_data / "blizzard" / "client.json").exists()


def test_logout_forgets_the_client() -> None:
    login()
    assert json.loads(run("logout", "--json").stdout)["removed"] is True
    assert json.loads(run("status", "--json").stdout)["configured"] is False
    assert run("logout").stdout.strip() == "No Blizzard API client is kept."


# ─── fetch and show ──────────────────────────────────────────────────────────


def test_fetch_asks_once_per_part_and_shows_each_with_its_time(
    blizzard: recorded.Recorded, user_data: Path
) -> None:
    login()
    done = run("fetch", NAME, "--json")
    assert done.exit_code == 0, done.output
    _clean(done.stdout, done.stderr)
    report = json.loads(done.stdout)
    assert [r.url.split("?")[0] for r in blizzard.requests] == [
        "https://oauth.battle.net/token",
        f"{recorded.API}{recorded.CHAR}",
        f"{recorded.API}{recorded.CHAR}/equipment",
        f"{recorded.API}{recorded.CHAR}/character-media",
        f"{recorded.API}{recorded.GUILD_PATH}",
    ]
    assert report["fetched"] is True and report["saved_fetches"] == 1
    assert report["region"] == "us"  # from the capture's Config.wtf
    p = report["profile"]
    assert p["character"]["average_item_level"] == 31
    assert p["character"]["ruleset"] == "PvP"
    assert [i["name"] for i in p["equipment"]] == ["Simple Blouse", "Shadow Wand"]
    assert p["guild"]["name"] == "The Crew"
    assert p["portrait"][0]["url"].startswith("https://render.worldofwarcraft.com/")
    for part in ("character", "equipment", "portrait", "guild"):
        assert p[f"{part}_state"]["status"] == "ok" and p[f"{part}_state"]["fetched_at"]
    cli.BlizzardProfileReport.model_validate_json(done.stdout)
    saved = list((user_data / "blizzard" / "profiles").rglob("*.json"))
    assert len(saved) == 1
    _clean(saved[0].read_text(encoding="utf-8"))
    # The text shows the same, each part with its time.
    blizzard.requests.clear()
    text = run("fetch", NAME).stdout
    _clean(text)
    assert "Sandgraal Rizzen (US), from Blizzard's armory" in text
    assert "Level 29 Undead Warlock (Affliction), Horde; ruleset: PvP" in text
    assert "Item level: 31 average, 30 equipped" in text
    assert "Guild: The Crew (12 members); fetched 20" in text
    assert "Portrait: https://render.worldofwarcraft.com/" in text
    assert "Head: Simple Blouse (item level 30, Uncommon)" in text
    assert len(blizzard.requests) == 5


def test_show_reads_the_saved_fetch_and_asks_nothing(blizzard: recorded.Recorded) -> None:
    login()
    assert run("fetch", NAME).exit_code == 0
    blizzard.requests.clear()
    for form in (NAME, "Sandgraal Rizzen", "sandgraal-rizzen"):
        report = json.loads(run("show", form, "--json").stdout)
        assert report["fetched"] is False and report["saved_fetches"] == 1, form
        assert report["profile"]["character"]["level"] == 29
    assert blizzard.requests == []
    other = json.loads(run("show", NAME, "--region", "eu", "--json").stdout)
    assert other["region"] == "eu" and other["profile"] is None


def test_a_region_given_is_the_one_asked(blizzard: recorded.Recorded) -> None:
    login()
    run("fetch", NAME, "--region", "eu")
    assert blizzard.requests[1].url.startswith("https://eu.api.blizzard.com/")
    assert "namespace=profile-forever-eu" in blizzard.requests[1].url


def test_no_profile_ends_with_an_error_and_says_why_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = recorded.Recorded(recorded.answers(profile=recorded.reply(404)))
    monkeypatch.setattr(bnet, "urllib_send", send)
    login()
    done = run("fetch", NAME)
    assert done.exit_code == 1
    assert "below level 10" in " ".join(done.stdout.split())
    assert len(send.requests) == 2


def test_refused_credentials_are_one_line_with_no_secret_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = recorded.Recorded(recorded.answers(token=recorded.reply(401)))
    monkeypatch.setattr(bnet, "urllib_send", send)
    login()
    done = run("fetch", NAME, "--json")
    assert done.exit_code == 1
    _clean(done.stdout, done.stderr)
    assert "refused the client ID and secret" in json.loads(done.stdout)["message"]
    assert CLIENT_ID not in done.stdout + done.stderr


@pytest.mark.parametrize(
    "name",
    ["../etc", "Sand/graal-Rizzen", "-Rizzen", "Sandgraal" + chr(0x202E) + "Rizzen", "Sandgraal"],
    ids=lambda n: f"constructed: {n!r}",
)
def test_a_name_wowlab_cannot_send_is_refused_before_asking(
    blizzard: recorded.Recorded, name: str
) -> None:
    login()
    done = run("fetch", "--", name)
    assert done.exit_code == 1, done.output
    assert blizzard.requests == []


def test_a_game_version_no_entry_covers_asks_nothing_constructed(
    blizzard: recorded.Recorded, root: Path
) -> None:
    info = root / ".build.info"
    info.write_bytes(info.read_bytes().replace(b"1.60.1.70888", b"11.2.5.70888"))
    login()
    done = run("fetch", NAME)
    assert done.exit_code == 1
    assert "does not know where Blizzard keeps the characters" in " ".join(done.stderr.split())
    assert blizzard.requests == []


def test_no_region_in_the_settings_asks_for_one_constructed(
    blizzard: recorded.Recorded, root: Path
) -> None:
    config = root / FOREVER / "WTF" / "Config.wtf"
    config.write_bytes(
        b"".join(
            line
            for line in config.read_bytes().splitlines(keepends=True)
            if not line.startswith(b"SET portal ")
        )
    )
    login()
    done = run("fetch", NAME)
    assert done.exit_code == 1
    assert "give it with --region" in done.stderr
    assert blizzard.requests == []
    assert run("fetch", NAME, "--region", "us").exit_code == 0
    assert len(blizzard.requests) == 5


def test_the_install_is_never_written(blizzard: recorded.Recorded, root: Path) -> None:
    before = sorted((p, p.stat().st_mtime_ns) for p in root.rglob("*"))
    login()
    run("fetch", NAME)
    run("show", NAME)
    run("logout")
    assert sorted((p, p.stat().st_mtime_ns) for p in root.rglob("*")) == before
