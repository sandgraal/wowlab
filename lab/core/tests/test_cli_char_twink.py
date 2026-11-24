"""`wowlab char twink` (M22-06) through Typer's runner.

The install is the `macos-70888` capture copied into `tmp_path` with its
flavor folder named as its provenance row says (`_forever_`): Sandgraal, an
Undead warlock recorded at level 13, wearing eleven items. The game tables
are the recordings of 1.60.1.70888 under `fixtures/wago/` (`Item`,
`ItemSparse`, `ItemXBonusTree` and `ItemBonusTreeNode` as ID-filtered
subsets that hold every item the full tables rank in the 19 and 29 lists,
the character's own items and a few items that must be left out;
`RandPropPoints`, `ItemBonus` and `ItemNameDescription` whole), served by a
stand-in source in place of wago.tools: nothing is fetched. The expected
lists below are what the full tables gave on 2026-11-23, which the subsets
reproduce.

Inputs labelled `constructed` are boundary cases (L8): a user twink.toml, a
damaged one, a source that is down. Nothing reads or writes a real install:
`WOWLAB_WOW_ROOT` points at the copy, and the user data and config folders
are redirected into `tmp_path`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gamedata, install, twink
from wowlab_core.gamedata import GameData

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WAGO = FIXTURES / "wago"
BUILD = "1.60.1.70888"
FLAVOR = "_forever_"  # the provenance rows' flavor folder; tests may name it (L6)
CHARACTER = "1/Sandgraal-Rizzen"
PLANNER = "https://forevertwinking.com/planner?class=warlock&race=undead&bracket=l29"
runner = CliRunner()


def recorded(table: str) -> Path:
    """The recording of `table` at `BUILD`: the whole table or its subset."""
    whole = WAGO / f"{table}.{BUILD}.csv"
    return whole if whole.is_file() else WAGO / f"{table}.{BUILD}.subset.csv"


class Tables:
    """Serves the recorded tables of `BUILD`, and refuses any other."""

    name = "fixtures"

    def __init__(self, down: bool = False) -> None:
        self.asked: list[str] = []
        self.down = down

    def fetch_builds(self, dest: BinaryIO) -> str:
        dest.write(b"{}")
        return "fixture:builds"

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        if self.down:
            raise gamedata.SourceUnavailable("fixture:down", 3, "constructed: the source is down")
        assert build == BUILD, build
        self.asked.append(table)
        dest.write(recorded(table).read_bytes())
        return f"fixture:{table}.{build}.csv"


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tables:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    tables = Tables()

    @contextmanager
    def fake() -> Iterator[GameData]:
        yield GameData(tables, cache_dir=data / "gamedata")

    monkeypatch.setattr(cli, "_open_gamedata", fake)
    return tables


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos-70888" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70888" / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    monkeypatch.chdir(tmp_path)
    before = _state(root)
    yield root
    assert _state(root) == before, "the install copy changed"


def _state(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def run(*args: str) -> Any:
    return runner.invoke(cli.app, ["char", "twink", "--character", CHARACTER, *args])


def report(*args: str) -> cli.CharTwinkReport:
    result = run("--json", *args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return cli.CharTwinkReport.model_validate_json(result.stdout)


def slot(r: cli.CharTwinkReport, n: int) -> twink.TwinkSlot:
    [found] = [s for s in r.twink.slots if s.slot == n]
    return found


def ids(s: twink.TwinkSlot) -> list[int]:
    return [c.item_id for c in s.candidates]


# ─── the default bracket and the download ────────────────────────────────────


def test_the_default_bracket_is_the_first_at_or_above_the_level(served: Tables, root: Path) -> None:
    r = report()
    p = r.twink
    assert (p.level, p.bracket, p.bracket_from) == (13, 19, "level")
    assert p.brackets == [19, 29, 39, 49, 59]
    assert (p.class_file, p.race_id) == ("WARLOCK", 5)
    assert p.weights_name == "affliction"
    assert p.weight_sets == ["affliction", "destruction", "demonology"]
    assert p.weights_source == "default" and r.config_source == "default"
    assert p.tables_build == BUILD
    assert r.flavor_folder == FLAVOR and r.character == CHARACTER
    # The 19 list as the full tables ranked it. Discarded Circlet (271812,
    # required level 1, item level 43) is left out: a required level of 1 is
    # none, and its item level is far above 19.
    assert ids(slot(r, 1)) == [4368, 4373]
    assert ids(slot(r, 16)) == [16889, 3452, 16894, 5613, 276888]
    # No trinket with a stat the affliction weights count, at 19.
    assert slot(r, 13).candidates == [] and slot(r, 13).gap is None
    assert slot(r, 13).gap_words == "no candidate in the game tables for this slot"
    # Worn and listed: the wand is the best the tables hold.
    ranged = slot(r, 18)
    assert ranged.worn is not None and ranged.worn.item_id == 11287
    assert [c.worn for c in ranged.candidates] == [False, False, False, False, True]
    assert p.planner == PLANNER.replace("l29", "l19")


def test_the_tables_are_downloaded_once_for_the_version(served: Tables, root: Path) -> None:
    first = run("--json")
    assert first.exit_code == 0, first.stderr
    assert sorted(served.asked) == sorted(twink.TABLES)
    assert "downloading 8 game tables for 1.60.1.70888 from wago.tools" in first.stderr
    assert cli.CharTwinkReport.model_validate_json(first.stdout).downloaded == list(twink.TABLES)
    served.asked.clear()
    second = report()
    assert served.asked == [] and second.downloaded == []


def test_a_source_that_is_down_is_an_error_constructed(served: Tables, root: Path) -> None:
    served.down = True
    result = run("--json")
    assert result.exit_code == 1
    assert "the game tables could not be read" in result.stderr
    error = json.loads(result.stdout)
    assert error["kind"] == "CliError" and error["exit_code"] == 1
    assert error["message"].startswith("the game tables could not be read: fixture:down")
    # Nothing was cached: the next run asks again.
    assert served.asked == []


# ─── the 29 list ─────────────────────────────────────────────────────────────


def test_the_29_list_slot_by_slot(served: Tables, root: Path) -> None:
    r = report("--bracket", "29")
    p = r.twink
    assert (p.bracket, p.bracket_from) == (29, "asked")
    expected = {
        1: [253981, 253955, 253975, 7050, 253949],
        2: [288926, 272075, 7427, 10299, 19537],
        3: [11884, 215365, 4314, 5274, 17047],
        5: [253967, 253961, 253907, 7065, 14158],
        6: [20164, 16975, 253931, 253925, 215366],
        7: [253993, 253943, 253987, 253937, 274741],
        8: [20162, 4320, 7027, 253895, 276274],
        9: [271740, 251486, 16981, 270028, 5315],
        10: [253919, 7047, 253913, 3759, 10654],
        11: [288931, 288930, 19521, 19525, 282283],
        12: [288931, 288930, 19521, 19525, 282283],
        13: [21120, 21566, 21568],
        14: [21120, 21566, 21568],
        15: [19529, 19533, 3719, 20427, 20428],
        16: [249392, 15109, 249393, 281312, 271767],
        17: [15935, 15934, 2944, 249394, 15975],
        18: [6677, 5246, 5818, 5250, 5356],
    }
    assert {s.slot: ids(s) for s in p.slots} == expected
    assert all(c.required_level <= 29 for s in p.slots for c in s.candidates)
    # A required level of 1 with an item level far above the bracket: A
    # Lesson in Destruction (276897, item level 38), Denric's Dog Tags
    # (276900), Discarded Circlet (271812) are quest rewards, left out.
    every = {c.item_id for s in p.slots for c in s.candidates}
    assert every.isdisjoint({276897, 276900, 271812})
    assert p.planner == PLANNER and p.planner_absent is None


def test_a_candidates_stats_and_score(served: Tables, root: Path) -> None:
    head = slot(report("--bracket", "29"), 1)
    best = head.candidates[0]
    assert (best.name, best.quality, best.item_level, best.required_level) == (
        "Filigreed Shadow Circlet",
        3,
        30,
        25,
    )
    assert [(s.name, s.amount) for s in best.stats] == [("stamina", 12), ("shadow_damage", 14)]
    assert best.score == 23.6  # 12 * 0.8 + 14 * 1.0
    assert best.suffix is None and best.other_suffixes == []
    assert best.wowhead == "https://www.wowhead.com/forever/item=253981"
    assert best.source is None  # where it comes from: not known yet
    assert head.worn is None and head.gap == 23.6
    assert head.gap_words == "the slot is empty; the best candidate scores 23.6"


def test_a_caster_weapon_carries_its_spell_power(served: Tables, root: Path) -> None:
    staff = slot(report("--bracket", "29"), 16).candidates[0]
    assert (staff.item_id, staff.name, staff.two_hand) == (249392, "Glimmering Staff", True)
    # No stat slot states spell power; the caster flag adds it (WoWSims' rule).
    assert [(s.name, s.amount) for s in staff.stats] == [
        ("intellect", 11),
        ("stamina", 11),
        ("spell_power", 34),
    ]


def test_an_item_with_suffixes_is_scored_per_suffix(served: Tables, root: Path) -> None:
    rod = slot(report("--bracket", "29"), 17).candidates[0]
    assert (rod.item_id, rod.name) == (15935, "Durable Rod")
    assert rod.suffix is not None
    assert (rod.suffix.id, rod.suffix.name) == (12761, "of Shadow Wrath")
    # No `?bonus=` until Wowhead is confirmed to read it (twink.WOWHEAD_BONUS).
    assert rod.wowhead == "https://www.wowhead.com/forever/item=15935"
    assert rod.score == 10.0 and rod.suffix.score == 10.0
    others = rod.other_suffixes
    assert others and all(o.score <= rod.score for o in others)
    assert [o.score for o in others] == sorted((o.score for o in others), reverse=True)
    assert len({o.id for o in others} | {rod.suffix.id}) == len(others) + 1


def test_the_worn_items_and_the_gap(served: Tables, root: Path) -> None:
    r = report("--bracket", "29")
    chest = slot(r, 5)
    assert chest.worn is not None
    assert (chest.worn.item_id, chest.worn.name, chest.worn.score) == (
        2578,
        "Barbaric Linen Vest",
        3.6,
    )
    assert chest.gap == 17.6 and chest.gap_words == "the best candidate scores 17.6 more"
    # Not in the downloaded ItemSparse: listed, not scored, no gap.
    waist = slot(r, 6)
    assert waist.worn is not None and waist.worn.item_id == 14113
    assert waist.worn.score is None and waist.gap is None
    assert waist.worn.unscored == "a hotfix the copy of the game tables wowlab downloads lacks"
    # The suffix is the link's bonus id that is a suffix list (12720, of the Owl).
    off = slot(r, 17).worn
    assert off is not None and off.suffix is not None
    assert (off.item_id, off.suffix.id, off.suffix.name, off.score) == (
        15970,
        12720,
        "of the Owl",
        0.55,
    )
    assert off.wowhead == "https://www.wowhead.com/forever/item=15970"
    assert not off.above_cap
    # The shirt and the tabard are not twink slots.
    assert {s.slot for s in r.twink.slots}.isdisjoint({4, 19})


def test_items_that_are_left_out(served: Tables, root: Path) -> None:
    every = {c.item_id for s in report("--bracket", "29").twink.slots for c in s.candidates}
    # Mail (2865) and a two-handed axe (7958): not a warlock's; a test ring
    # (24358) and a deprecated cowl (3068): by name; Deathmist Mask (22074):
    # no required level, item level 60; boots requiring level 30 (254001);
    # the League of Arathor's rewards (20096, 20099, 21119) and the Theramore
    # Expeditionary Force's seal (271920), which an Undead
    # cannot earn, while the Defilers' (20162, 20164, 21120) stay.
    assert every.isdisjoint({2865, 7958, 24358, 3068, 22074, 254001, 20096, 20099, 21119, 271920})
    assert {20162, 20164, 21120} <= every
    panel = report("--bracket", "29").twink
    assert panel.no_level_left_out >= 1 and panel.other_side_left_out == 4
    feet = slot(report("--bracket", "39"), 8)
    assert 254001 in ids(feet)


# ─── the options ─────────────────────────────────────────────────────────────


def test_another_weight_set_and_top(served: Tables, root: Path) -> None:
    r = report("--bracket", "29", "--weights", "demonology", "--top", "2")
    assert r.twink.weights_name == "demonology"
    assert r.twink.weights["stamina"] == 1.0
    assert all(len(s.candidates) <= 2 for s in r.twink.slots)


def test_a_bracket_or_weight_set_not_offered_is_refused(served: Tables, root: Path) -> None:
    result = run("--bracket", "30")
    assert result.exit_code == 2
    assert "30 is not a bracket; the brackets end at levels 19, 29, 39, 49, 59" in result.stderr
    result = run("--weights", "frost")
    assert result.exit_code == 2
    assert "no weight set 'frost' for WARLOCK" in result.stderr
    assert "affliction, destruction, demonology" in result.stderr


def test_your_own_twink_toml_replaces_what_it_sets_constructed(
    served: Tables, root: Path, tmp_path: Path
) -> None:
    config = tmp_path / "userconfig" / "wowlab"
    config.mkdir(parents=True)
    (config / "twink.toml").write_text(
        "[brackets]\ncaps = [29]\n\n[weights.WARLOCK.affliction]\nstamina = 1.0\n",
        encoding="utf-8",
    )
    r = report()
    assert r.config_source == "user" and r.twink.weights_source == "user"
    assert r.twink.brackets == [29] and r.twink.bracket == 29
    assert r.twink.weights == {"stamina": 1.0}
    head = slot(r, 1).candidates[0]
    assert head.score == sum(s.amount for s in head.stats if s.name == "stamina")


def test_a_damaged_twink_toml_is_refused_by_name_constructed(
    served: Tables, root: Path, tmp_path: Path
) -> None:
    config = tmp_path / "userconfig" / "wowlab"
    config.mkdir(parents=True)
    (config / "twink.toml").write_text("[weights.WARLOCK.x]\nluck = 1\n", encoding="utf-8")
    result = run("--json")
    assert result.exit_code == 1
    assert "the twink settings cannot be used" in result.stderr
    assert "'luck' is not a stat name" in result.stderr
    assert served.asked == []  # refused before any table is read


# ─── the text ────────────────────────────────────────────────────────────────


def test_the_text_answer(served: Tables, root: Path) -> None:
    result = run("--bracket", "29")
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    assert (
        "Twink gaps for the level 29 bracket, scored with the affliction weights (shipped)" in out
    )
    assert "  slot  1 Head: empty" in out
    assert (
        "    best: Filigreed Shadow Circlet (requires level 25, binds when picked up): score 23.6, "
        "12 stamina, 14 "
        "shadow damage" in out
    )
    assert "    best: Durable Rod of Shadow Wrath (requires level 29): score 10" in out
    assert "    also: Durable Rod of Shadow Wrath (requires level 29): score 10" not in out
    assert "  slot  6 Waist: Aboriginal Sash of the Sorcerer: not scored (" in out
    assert "  slot  5 Chest: Barbaric Linen Vest: score 3.6" in out
    assert "    gap: the best candidate scores 17.6 more" in out
    assert f"Plan on ForeverTwinking: {PLANNER}" in out
    assert out.rstrip().endswith("notes: --notes shows them")
    for word in ("M22", "ADR", "[verify]", "twink.py", "TwinkPanel"):
        assert word not in out


@pytest.mark.parametrize("args", [("--bracket", "29"), ("--bracket", "29", "--notes"), ()])
def test_the_text_is_player_copy(served: Tables, root: Path, args: tuple[str, ...]) -> None:
    from test_cli_player_copy import Install as PlayerCopyInstall
    from test_cli_player_copy import hits

    result = run(*args)
    assert result.exit_code == 0, result.stderr
    found = hits(result.stdout + result.stderr, PlayerCopyInstall.of(root))
    assert found == []
    # "(not yet confirmed)" once at most, as every report.
    assert result.stdout.count("not yet confirmed") <= 1


def test_the_notes(served: Tables, root: Path) -> None:
    out = run("--bracket", "29", "--notes").stdout
    flat = " ".join(out.split())
    assert (
        "Where each item comes from (a quest, a drop, a vendor, a craft) is not known yet" in flat
    )
    assert "have no stats in the copy wowlab downloads" in flat
    assert "4,108 of the 4,944 classic-range ones are in the game's own hotfix cache" in flat
    assert "items only the other side's quests or vendors give are not yet left out" in flat
    assert "Crit, hit and haste weights count per point of rating" in flat
    assert "named as a test, placeholder or retired item were left out" in flat
    assert "A two-handed weapon also takes the off hand" in flat
    assert "a weight set replaces the whole set" in flat
    assert os.sep + "twink.toml" in out or "/twink.toml" in out


def test_a_level_above_the_bracket_says_so(
    served: Tables, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The record is level 13; the 19 bracket's own words name no caveat,
    # so the level is raised in the library's view of it (constructed).
    from wowlab_core import dressing

    monkeypatch.setattr(dressing, "level_of", lambda record: 24)
    r = report("--bracket", "19")
    assert r.twink.level == 24
    assert "above the bracket's top level" in r.twink.words
    text = run("--bracket", "19").stdout
    assert "a bracket's battlegrounds take characters up to its top level only" in " ".join(
        text.split()
    )


def test_help_names_the_model() -> None:
    out = runner.invoke(cli.app, ["char", "twink", "--help"], terminal_width=120).stdout
    assert "--bracket" in out and "--weights" in out and "--top" in out
    assert "Reads only." in " ".join(out.split())


def test_the_report_round_trips(served: Tables, root: Path) -> None:
    result = run("--json", "--bracket", "29")
    again = cli.CharTwinkReport.model_validate_json(result.stdout)
    buffer = io.StringIO(again.model_dump_json())
    assert cli.CharTwinkReport.model_validate_json(buffer.getvalue()) == again
