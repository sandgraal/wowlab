"""`wowlab binds coach` (M13-08, docs/LAB_PLAN.md §15.2) and `bindscoach`.

The install is the captured tree (`fixtures/macos/`, build 69913) copied into
`tmp_path` with its flavor folder named as its provenance row says
(`_classic_beta_`), as `test_cli_macros_check.py` does. It holds the real
Forever combat log (`Logs/WoWCombatLog-040126_021630.txt`, build 70009). The
action bars, bindings and spellbook come from the real schema-3 record
(`fixtures/macos-70425/`, M13-05), copied into a character folder of that
tree: a `cross-capture` combination (two builds, two capture runs), said in
the test ids. Both are a level-15-or-lower warlock, and the log is older:
it casts Immolate and Shadow Bolt at their rank 1 and 2 ids where the bars
hold ranks 2 and 3.

Inputs labelled `constructed` are boundary cases (L8): combat-log lines in
the real log's format-22 layout with made-up spells, a record with one
section replaced or its probes changed, cost tables written by the test, a
hostile spell name. Nothing here reads or writes a real install:
`WOWLAB_WOW_ROOT` points at the copy, and the user data and user config
directories are redirected into `tmp_path`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import bindscoach, cli, combatlog, install, labaddon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
SCHEMA_3 = (
    FIXTURES
    / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)
LOG_NAME = "WoWCombatLog-040126_021630.txt"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
CHARACTER = "Labcharb-Labrealmd"
CHAR_DIR = f"WTF/Account/{ACCOUNT}/1/{CHARACTER}"
RECORD = f"{CHAR_DIR}/SavedVariables/WowLab.lua"
SCHEMA_1_CHARACTER = "1/Labchard-Labrealmg"  # a schema-1 WowLab.lua (M11-03)
HEADER = (
    "4/1/2026 02:16:30.666-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,"
    "BUILD_VERSION,1.60.1,PROJECT_ID,18"
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    return config


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


@pytest.fixture
def record(flavor: Path) -> Path:
    """The real schema-3 record as CHARACTER's WowLab.lua."""
    target = flavor / RECORD
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCHEMA_3, target)
    return target


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def report(*args: str) -> cli.BindsCoachReport:
    out = ok("binds", "coach", *args, "--json").stdout
    return cli.BindsCoachReport.model_validate_json(out)


def _state(path: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(path.rglob("*")):
        st = p.lstat()
        out[p.relative_to(path).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


def cast(spell_id: int, name: str, flags: str = "0x511") -> str:
    """One constructed cast in the real log's format-22 layout."""
    return (
        f"4/1/2026 02:16:37.862-4  SPELL_CAST_SUCCESS,Player-9999-00000001,"
        f'"Constructed-Unit-",{flags},0x80000000,Creature-0-1-0-2-1554-0000000000,'
        f'"Constructed Target",0x10a48,0x80000000,{spell_id},"{name}",0x20'
    )


def put_log(flavor: Path, *lines: str, name: str = "WoWCombatLog-050126_000000.txt") -> Path:
    """A constructed log, newer than the real one, so it is the one read."""
    path = flavor / "Logs" / name
    path.write_bytes("".join(f"{line}\r\n" for line in lines).encode("utf-8"))
    real = flavor / "Logs" / LOG_NAME
    st = real.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    return path


def real_record() -> labaddon.CharDBV3:
    rec = labaddon.read_char(SCHEMA_3)
    assert isinstance(rec, labaddon.CharDBV3)
    return rec


def real_counts() -> combatlog.CastCounts:
    return combatlog.cast_counts(combatlog.read_log(CAPTURE / "forever" / "Logs" / LOG_NAME))


def default_table() -> bindscoach.CostTable:
    return bindscoach.load_costs().table


def constructed_counts(*lines: str) -> combatlog.CastCounts:
    data = "".join(f"{line}\r\n" for line in (HEADER, *lines)).encode()
    return combatlog.cast_counts(combatlog.tokenize(data))


# ─── the real log and the real record ────────────────────────────────────────

NEVER_CAST = [
    (4, "ACTIONBUTTON4", "4", 1120, "Drain Soul"),
    (6, "ACTIONBUTTON6", "6", 1108, "Curse of Weakness"),
    (7, "ACTIONBUTTON7", "7", 755, "Health Funnel"),
    (9, "ACTIONBUTTON9", "9", 20577, "Cannibalize"),
    (12, "ACTIONBUTTON12", "NUMPADMINUS", 5019, "Shoot"),
    (61, "MULTIACTIONBAR1BUTTON1", "Q", 7744, "Will of the Forsaken"),
    (62, "MULTIACTIONBAR1BUTTON2", "BUTTON3", 1454, "Life Tap"),
    (63, "MULTIACTIONBAR1BUTTON3", "E", 5782, "Fear"),
]


def test_binds_coach_on_the_real_log_and_record_cross_capture(
    root: Path, record: Path, user_dirs: Path
) -> None:
    before = _state(root)
    rep = report("--character", CHARACTER)
    assert (rep.flavor_folder, rep.account, rep.character) == (FLAVOR, ACCOUNT, f"1/{CHARACTER}")
    assert (rep.chosen_by, rep.record_file, rep.record_schema, rep.record_error) == (
        "--character",
        RECORD,
        3,
        None,
    )
    assert rep.log.file == f"Logs/{LOG_NAME}"
    assert rep.log.counts is not None and rep.log.counts.mine == 6
    assert rep.costs.source == "default"
    assert rep.costs.user_file == str(user_dirs / bindscoach.COSTS_FILE)
    assert rep.matched_by == "spell id or spellbook name"
    # Ranks are one spell: the log's Immolate (348, rank 1) and Shadow Bolt
    # (695, rank 2) reach the bars' rank 2 (707) and rank 3 (705) by name.
    assert [
        (s.spell_name, s.spell_ids, s.casts, s.slots, [k.key for k in s.keys]) for s in rep.spells
    ] == [
        ("Bane of Agony", [980], 2, [3], ["3"]),
        ("Corruption", [172], 2, [2], ["2"]),
        ("Immolate", [348], 1, [1, 49], ["1"]),
        ("Shadow Bolt", [695], 1, [5, 50, 67], ["5"]),
    ]
    assert rep.frequent_on_high_cost == []  # six casts: none reaches five of one spell
    assert rep.cast_without_bind == []
    assert [
        (b.slot, b.command, ", ".join(k.key for k in b.keys), b.spell_id, b.spell_name)
        for b in rep.bound_never_cast
    ] == NEVER_CAST
    assert rep.cannot_judge == []
    assert rep.caveats == []  # commands_left_out = 1, but nothing is cast with no bind
    assert any("/combatlog" in n for n in rep.notes)
    assert _state(root) == before  # reads never write (L1)
    assert not user_dirs.exists()  # nor into the user config folder
    assert not (user_dirs.parent.parent / "userdata").exists()


def test_binds_coach_text_says_the_table_is_a_default_and_that_combatlog_must_be_on(
    record: Path,
) -> None:
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert f"Combat log: Logs/{LOG_NAME} (79 lines; COMBAT_LOG_VERSION 22)\n" in text
    assert "  Your casts: 6 of 7 SPELL_CAST_SUCCESS record(s)" in text
    assert "Key costs: the shipped default table, an opinion." in text
    assert bindscoach.COSTS_FILE in text
    assert (
        "  High-cost: 4 or more. Frequent: at least 5% of your casts and at least 5 casts.\n"
        in text
    )
    assert "       1  Shadow Bolt (695): 5 (cost 2)\n" in text
    assert "Binds to spells never cast: 8\n" in text
    assert "  7 (cost 4): slot 7, Health Funnel (755)\n" in text
    assert "/combatlog" in text


def test_binds_coach_without_character_takes_the_latest_record(flavor: Path, record: Path) -> None:
    newest = max(p.stat().st_mtime_ns for p in flavor.rglob("WowLab.lua"))
    os.utime(record, ns=(newest + 10**9, newest + 10**9))
    rep = report()
    assert (rep.character, rep.chosen_by, rep.record_schema) == (f"1/{CHARACTER}", "latest", 3)
    text = ok("binds", "coach").stdout
    assert "(written last; choose another with --character)" in text


# ─── constructed: one log per report ─────────────────────────────────────────


def test_frequent_spell_on_a_high_cost_key_constructed(flavor: Path, record: Path) -> None:
    """Health Funnel sits on slot 7, key 7 (cost 4 in the default table)."""
    put_log(flavor, HEADER, *[cast(755, "Health Funnel")] * 10, *[cast(172, "Corruption")] * 10)
    rep = report("--character", CHARACTER)
    [f] = rep.frequent_on_high_cost
    assert (f.spell_name, f.spell_ids, f.casts, f.share, f.cheapest) == (
        "Health Funnel",
        [755],
        10,
        0.5,
        4,
    )
    assert [(k.key, k.cost) for k in f.keys] == [("7", 4)]
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert (
        "Frequent spells on high-cost keys: 1\n  Health Funnel: 10 casts (50%), 7 (cost 4)\n"
        in text
    )


def test_frequent_needs_both_thresholds_constructed() -> None:
    table = default_table()
    rec = real_record()
    # Four casts: under frequent_min_casts (5).
    few = bindscoach.coach(constructed_counts(*[cast(755, "Health Funnel")] * 4), rec, table)
    assert few.frequent_on_high_cost == []
    # Five casts, but 5 of 200 is 2.5%: under frequent_share (5%).
    many = [cast(172, "Corruption")] * 195
    rare = bindscoach.coach(
        constructed_counts(*[cast(755, "Health Funnel")] * 5, *many), rec, table
    )
    assert rare.frequent_on_high_cost == []
    # Corruption is frequent, but on key 2 (cost 1).
    assert rare.spells[0].spell_name == "Corruption"
    hit = bindscoach.coach(constructed_counts(*[cast(755, "Health Funnel")] * 5), rec, table)
    assert [f.spell_name for f in hit.frequent_on_high_cost] == ["Health Funnel"]


def test_frequent_judges_the_cheapest_key_constructed() -> None:
    """A spell with one cheap key is not on high-cost keys, however many it has."""
    table = default_table().model_copy(update={"high_cost": 1})
    got = bindscoach.coach(
        constructed_counts(*[cast(172, "Corruption")] * 10), real_record(), table
    )
    assert [f.cheapest for f in got.frequent_on_high_cost] == [1]
    table = default_table().model_copy(update={"high_cost": 2})
    got = bindscoach.coach(
        constructed_counts(*[cast(172, "Corruption")] * 10), real_record(), table
    )
    assert got.frequent_on_high_cost == []


def test_cast_with_no_bind_clicked_and_on_no_slot_constructed(flavor: Path, record: Path) -> None:
    """Summon Voidwalker sits on slot 69, which no key reaches; the made-up
    spell is on no slot at all."""
    put_log(flavor, HEADER, cast(697, "Summon Voidwalker"), cast(999001, "Constructed Spell"))
    rep = report("--character", CHARACTER)
    assert [(u.spell_name, u.casts, u.slots) for u in rep.cast_without_bind] == [
        ("Constructed Spell", 1, []),
        ("Summon Voidwalker", 1, [69]),
    ]
    assert rep.cast_without_bind[0].detail.startswith("on no action slot: cast from a macro")
    assert rep.cast_without_bind[1].detail.startswith("on action slot(s) no key reaches: clicked")
    # The record left one binding command out, so the report says it may be wrong.
    assert [c for c in rep.caveats if "left out 1 command(s)" in c]
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert "Spells cast with no bind: 2\n" in text
    assert "  Summon Voidwalker: 1 cast (slots 69): on action slot(s) no key reaches" in text


def test_bound_never_cast_lists_only_bound_spell_slots_constructed() -> None:
    """Every bound spell slot but the one cast; item slots (8, 10, 11) and
    unbound spell slots (49, 50, 64 to 70) are never listed."""
    got = bindscoach.coach(
        constructed_counts(cast(1120, "Drain Soul")), real_record(), default_table()
    )
    assert [(b.slot, b.spell_id) for b in got.bound_never_cast] == sorted(
        [(s, i) for s, _, _, i, _ in NEVER_CAST if s != 4]
        + [(1, 707), (2, 172), (3, 980), (5, 705)]
    )


def test_bound_never_cast_cannot_judge_a_log_without_your_casts_constructed(
    flavor: Path, record: Path
) -> None:
    put_log(flavor, HEADER, cast(172, "Corruption", flags="0x10a48"))
    rep = report("--character", CHARACTER)
    assert rep.bound_never_cast == []
    assert [(c.report, c.reason) for c in rep.cannot_judge] == [
        (
            "bound-never-cast",
            "the log has none of your casts, so every bound spell would be listed",
        )
    ]


def test_ranks_match_by_id_alone_without_a_spellbook_constructed() -> None:
    rec = real_record().model_copy(update={"spellbook": None})
    got = bindscoach.coach(real_counts(), rec, default_table())
    assert got.matched_by == "spell id"
    assert [u.spell_name for u in got.cast_without_bind] == ["Immolate", "Shadow Bolt"]
    assert any("by spell id only" in c for c in got.caveats)
    # And the bars' rank 2 Immolate and rank 3 Shadow Bolt now read as never cast.
    assert {(b.slot, b.spell_id) for b in got.bound_never_cast} >= {(1, 707), (5, 705)}


def test_two_characters_in_one_log_are_said_constructed() -> None:
    other = cast(172, "Corruption").replace("Player-9999-00000001", "Player-9999-00000002")
    got = bindscoach.coach(
        constructed_counts(cast(172, "Corruption"), other), real_record(), default_table()
    )
    assert any("casts by 2 of your characters" in c for c in got.caveats)


# ─── cannot judge ────────────────────────────────────────────────────────────


def test_no_combat_log_says_combatlog_must_be_on(flavor: Path, record: Path) -> None:
    (flavor / "Logs" / LOG_NAME).unlink()
    rep = report("--character", CHARACTER)
    assert rep.log.file is None and rep.log.counts is None
    assert [c.report for c in rep.cannot_judge] == list(bindscoach.REPORTS)
    assert all("/combatlog" in c.reason for c in rep.cannot_judge)
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert "Combat log: no combat log under Logs/" in text


def test_no_record_still_counts_the_casts(root: Path) -> None:
    """The character's folder has no WowLab.lua: a reported gap, not an error."""
    rep = report("--character", CHARACTER)
    assert rep.record_file is None and rep.record_error is not None
    assert "no WowLab.lua in 1/" + CHARACTER in rep.record_error
    assert rep.log.counts is not None and rep.log.counts.mine == 6
    assert [s.spell_name for s in rep.spells] == [
        "Bane of Agony",
        "Corruption",
        "Immolate",
        "Shadow Bolt",
    ]
    assert all(s.keys == [] and s.slots == [] for s in rep.spells)
    assert [c.report for c in rep.cannot_judge] == list(bindscoach.REPORTS)
    assert rep.cast_without_bind == [] and rep.bound_never_cast == []


def test_a_character_folder_that_does_not_exist_is_a_usage_error(root: Path) -> None:
    result = run("binds", "coach", "--character", "Nobody-Nowhere")
    assert result.exit_code == 2


def test_schema_1_record_cannot_judge(root: Path) -> None:
    rep = report("--character", SCHEMA_1_CHARACTER)
    assert rep.record_schema == 1
    assert [c.report for c in rep.cannot_judge] == list(bindscoach.REPORTS)
    assert all("schema 1 has neither" in c.reason for c in rep.cannot_judge)


def _probes_with(
    probes: list[labaddon.ApiProbe], api: str, **update: Any
) -> list[labaddon.ApiProbe]:
    return [p.model_copy(update=update) if p.api == api else p for p in probes]


@pytest.mark.parametrize(
    ("section", "api", "update", "words"),
    [
        ("actionbars", "GetActionInfo", {"errors": 1}, "the action-bar record is incomplete"),
        ("actionbars", "GetActionInfo", {"secret": 1}, "the action-bar record is incomplete"),
        ("bindings", "GetBindingKey", {"errors": 1}, "the bindings record is incomplete"),
        ("bindings", "GetBindingKey", {"secret": 2}, "the bindings record is incomplete"),
    ],
    ids=["bars-error", "bars-secret", "binds-error", "binds-secret"],
)
def test_a_record_that_dropped_values_cannot_judge_constructed(
    section: str, api: str, update: dict[str, int], words: str
) -> None:
    rec = real_record()
    part = getattr(rec, section)
    part = part.model_copy(update={"probes": _probes_with(part.probes, api, **update)})
    got = bindscoach.coach(real_counts(), rec.model_copy(update={section: part}), default_table())
    assert [c.report for c in got.cannot_judge] == list(bindscoach.REPORTS)
    assert all(c.reason.startswith(words) for c in got.cannot_judge)
    assert got.bound_never_cast == [] and got.cast_without_bind == []


def test_an_untyped_slot_cannot_judge_constructed() -> None:
    rec = real_record()
    bars = rec.actionbars
    assert isinstance(bars, labaddon.ActionBars)
    slots = [bars.slots[0].model_copy(update={"type": None}), *bars.slots[1:]]
    rec = rec.model_copy(update={"actionbars": bars.model_copy(update={"slots": slots})})
    got = bindscoach.coach(real_counts(), rec, default_table())
    assert all("1 slot(s) have no type" in c.reason for c in got.cannot_judge)


@pytest.mark.parametrize("section", ["actionbars", "bindings"])
def test_an_absent_section_cannot_judge_constructed(section: str) -> None:
    absent = labaddon.AbsentProbedSection(absent="in combat: constructed")
    rec = real_record().model_copy(update={section: absent})
    got = bindscoach.coach(real_counts(), rec, default_table())
    assert all(
        "the section is absent: in combat: constructed" in c.reason for c in got.cannot_judge
    )
    missing = real_record().model_copy(update={section: None})
    got = bindscoach.coach(real_counts(), missing, default_table())
    assert all(c.reason.startswith("no ") for c in got.cannot_judge)


# ─── the key-cost table ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "cost"),
    [
        ("1", 1),
        ("7", 4),
        ("q", 1),
        ("SHIFT-1", 2),
        ("CTRL-Q", 3),
        ("ALT-CTRL-SHIFT-1", 6),
        ("LSHIFT-1", 2),
        ("RCTRL-E", 3),
        ("SHIFT-MOUSEWHEELUP", 3),
        ("NUMPADMINUS", 5),  # unlisted
        ("CTRL--", 7),  # CTRL plus the unlisted "-"
        ("-", 5),
        ("SHIFT", 5),  # a modifier alone is an unlisted key
        ("LSHIFT", 5),
    ],
)
def test_key_cost_adds_the_modifiers_to_the_key(key: str, cost: int) -> None:
    assert bindscoach.key_cost(key, default_table()) == bindscoach.KeyCost(key=key, cost=cost)


@pytest.mark.parametrize(
    ("command", "slot"),
    [
        ("ACTIONBUTTON1", 1),
        ("ACTIONBUTTON12", 12),
        ("MULTIACTIONBAR3BUTTON1", 25),
        ("MULTIACTIONBAR4BUTTON12", 48),
        ("MULTIACTIONBAR2BUTTON1", 49),
        ("MULTIACTIONBAR1BUTTON1", 61),
        ("MULTIACTIONBAR5BUTTON1", 145),
        ("MULTIACTIONBAR7BUTTON12", 180),
        ("ACTIONBUTTON13", None),
        ("ACTIONBUTTON0", None),
        ("BONUSACTIONBUTTON1", None),
        ("SHAPESHIFTBUTTON1", None),
        ("MULTIACTIONBAR8BUTTON1", None),
    ],
)
def test_command_slot_follows_the_default_ui(command: str, slot: int | None) -> None:
    assert bindscoach.command_slot(command) == slot


def test_the_shipped_table_parses_and_is_the_default() -> None:
    costs = bindscoach.load_costs()
    assert costs.source == "default"
    assert Path(costs.shipped).name == bindscoach.COSTS_FILE
    t = costs.table
    assert (t.high_cost, t.frequent_share, t.frequent_min_casts, t.unlisted) == (4, 0.05, 5, 5)
    assert t.modifiers == {"SHIFT": 1, "CTRL": 2, "ALT": 2, "META": 2}
    assert all(k == k.upper() for k in t.keys)


def test_a_players_table_overrides_key_by_key_constructed(
    flavor: Path, record: Path, user_dirs: Path
) -> None:
    user_dirs.mkdir(parents=True)
    (user_dirs / bindscoach.COSTS_FILE).write_text(
        '[coach]\nhigh_cost = 5\n\n[keys]\n"7" = 6\nnumpadminus = 1\n', encoding="utf-8"
    )
    put_log(flavor, HEADER, *[cast(755, "Health Funnel")] * 10)
    before = _state(user_dirs)
    rep = report("--character", CHARACTER)
    assert rep.costs.source == "user"
    assert rep.costs.table.high_cost == 5
    assert rep.costs.table.keys["7"] == 6 and rep.costs.table.keys["NUMPADMINUS"] == 1
    assert rep.costs.table.keys["Q"] == 1  # left out: the shipped value
    assert rep.costs.table.frequent_min_casts == 5
    assert [(f.spell_name, f.cheapest) for f in rep.frequent_on_high_cost] == [("Health Funnel", 6)]
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert (
        f"Key costs: your table {user_dirs / bindscoach.COSTS_FILE}, over the shipped default"
        in text
    )
    assert _state(user_dirs) == before


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("[coach\n", "not valid TOML"),
        ("[colors]\nred = 1\n", "unknown table or key colors"),
        ("[coach]\nhigh = 1\n", "[coach]: unknown key high"),
        ("[coach]\nhigh_cost = -1\n", "[coach] high_cost"),
        ("[coach]\nfrequent_share = 0\n", "[coach] frequent_share"),
        ("[coach]\nfrequent_share = 1.5\n", "[coach] frequent_share"),
        ("[coach]\nfrequent_min_casts = 0\n", "[coach] frequent_min_casts"),
        ('[keys]\nQ = "cheap"\n', "[keys] Q: a cost is a whole number"),
        ("[keys]\nQ = true\n", "[keys] Q: a cost is a whole number"),
        ("[keys]\nQ = 1.5\n", "[keys] Q: a cost is a whole number"),
        ('[keys]\n"CTRL-Q" = 1\n', "is not one key"),
        ("[modifiers]\nHYPER = 1\n", "'HYPER' is not a modifier"),
        ("keys = 1\n", "[keys] must be a table"),
    ],
    ids=lambda v: v if len(v) < 24 else v[:24],
)
def test_a_bad_players_table_is_an_error_naming_the_file_constructed(
    record: Path, user_dirs: Path, text: str, words: str
) -> None:
    user_dirs.mkdir(parents=True)
    path = user_dirs / bindscoach.COSTS_FILE
    path.write_text(text, encoding="utf-8")
    result = run("binds", "coach", "--character", CHARACTER, "--json")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert f"the key-cost table cannot be used: {path}: " in result.stderr
    assert words in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_players_table_that_is_a_link_is_refused_constructed(
    record: Path, user_dirs: Path, tmp_path: Path
) -> None:
    user_dirs.mkdir(parents=True)
    real = tmp_path / "elsewhere.toml"
    real.write_text("[coach]\nhigh_cost = 1\n", encoding="utf-8")
    (user_dirs / bindscoach.COSTS_FILE).symlink_to(real)
    result = run("binds", "coach", "--character", CHARACTER)
    assert result.exit_code == 1
    assert "the key-cost table cannot be used" in result.stderr


def test_a_shipped_table_without_a_threshold_is_refused_constructed() -> None:
    with pytest.raises(bindscoach.CostTableError, match="high_cost"):
        bindscoach.parse_costs(
            "[coach]\nfrequent_share = 0.1\nfrequent_min_casts = 1\nunlisted = 1\n"
        )


# ─── output ──────────────────────────────────────────────────────────────────


def test_json_validates_and_round_trips(record: Path) -> None:
    out = ok("binds", "coach", "--character", CHARACTER, "--json").stdout
    rep = cli.BindsCoachReport.model_validate_json(out)
    assert cli.BindsCoachReport.model_validate_json(rep.model_dump_json()) == rep


def test_a_hostile_spell_name_is_escaped_in_text_constructed(flavor: Path, record: Path) -> None:
    put_log(flavor, HEADER, cast(999002, "Bolt\x1b]0;owned\x07"))
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert "\x1b" not in text and "\x07" not in text
    assert "Bolt\\x1b]0;owned\\x07" in text
