"""`wowlab binds coach`: its inputs and defaults (M16-36, docs/LAB_PLAN.md §16.2).

Four things the coach settles before any finding: the bindings it cannot
see, which combat log is read (by the caster name that matches the
character judged), the minimum sample under which "bound, never cast" is
withheld, and which character is judged without --character (the newest
record schema, then the one written last, with the reason printed).

The install is the captured tree of `test_cli_binds_coach.py`
(`fixtures/macos/`, build 69913) with its real Forever combat log (build
70009, casts by `"Labchard-LabrealmbPartbPartcPartd-"`) and real lab-addon
records copied into its character folders: the schema-3 record of
`macos-70425/` and the schema-4 record of `macos-70450/`. Those are
`cross-capture` combinations, said in the test ids; pseudonyms from
different capture runs prove nothing about the real characters, so these
tests match names as strings and never assume two pseudonyms are one
character. Inputs labelled `constructed` are boundary cases (L8): logs in
the real log's format-22 layout with made-up casters, a record with a count
changed, an unreadable record, and casts counted up to the minimum sample.
Nothing here reads or writes a real install.
"""

from __future__ import annotations

import os
import shutil
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from test_cli_binds_coach import (
    ACCOUNT,
    CHARACTER,
    FIXTURES,
    HEADER,
    LOG_NAME,
    SCHEMA_3,
    cast,
    constructed_counts,
    default_table,
    flavor,
    ok,
    put_log,
    real_counts,
    real_record,
    record,
    report,
    root,
    user_dirs,
)

from wowlab_core import bindscoach, cli, combatlog, labaddon

__all__ = ["flavor", "record", "root", "user_dirs"]  # the fixtures, used by name

SCHEMA_4 = (
    FIXTURES
    / "macos-70450/forever/WTF/Account/90000001#6/1/Sangraal-Sett/SavedVariables/WowLab.lua"
)
LOG_CASTER = "Labchard-LabrealmbPartbPartcPartd-"  # the real log's caster, as written
# A character folder of the captured tree whose first name is the real log's
# caster's (its own WowLab.lua is schema 1; tests copy a schema-3 one over it).
NAMESAKE = "Labchard-Labrealmg"
NAMESAKE_RECORD = f"WTF/Account/{ACCOUNT}/1/{NAMESAKE}/SavedVariables/WowLab.lua"
OTHER = "Labcharb-Labrealmf"  # a schema-1 WowLab.lua (M11-03)
OTHER_RECORD = f"WTF/Account/{ACCOUNT}/1/{OTHER}/SavedVariables/WowLab.lua"


def _mtime(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _newest(flavor: Path) -> int:
    return max(p.stat().st_mtime_ns for p in flavor.rglob("*") if p.is_file())


def caster_log(flavor: Path, caster: str, *lines: str, name: str, offset: int) -> Path:
    """A constructed log by `caster`, `offset` seconds newer than every file."""
    body = [line.replace('"Constructed-Unit-"', f'"{caster}"') for line in lines]
    path = put_log(flavor, HEADER, *body, name=name)
    _mtime(path, _newest(flavor) + offset * 10**9)
    return path


@pytest.fixture
def namesake(flavor: Path) -> Path:
    """The real schema-3 record as NAMESAKE's WowLab.lua."""
    target = flavor / NAMESAKE_RECORD
    shutil.copy2(SCHEMA_3, target)
    return target


# ─── bindings the coach cannot see ───────────────────────────────────────────


UNSEEN_REAL = (
    "Left out of the bindings record, counted with no list: 1 binding command it did not read, "
    "whether or not a key is bound to it (not named in plain upper case), and 0 keys whose "
    "name is not printable ASCII. Any of these may reach a spell, so a spell reported with no "
    "key may have one, and a key reported as costly may not be the only one. A key bound "
    "straight to a spell or to an addon's button may be in neither count [verify]."
)


def test_unseen_bindings_are_said_before_any_finding_cross_capture(record: Path) -> None:
    """The real record counts one binding command it left out: said right
    after the record's own line, before the key costs and every report, in
    `char binds`'s words; the marker reads "(not yet confirmed)"."""
    rep = report("--character", CHARACTER)
    assert (rep.commands_unseen, rep.keys_unseen) == (1, 0)
    assert rep.unseen == UNSEEN_REAL
    lines = ok("binds", "coach", "--character", CHARACTER).stdout.splitlines()
    at = lines.index(UNSEEN_REAL.replace(" [verify]", " (not yet confirmed)"))
    assert lines[at - 1].startswith("Action bars and bindings: ")
    first_finding = next(i for i, line in enumerate(lines) if line.startswith("Key costs:"))
    assert at < first_finding


def test_unseen_words_name_no_addon_constructed() -> None:
    for commands, keys in [(0, 0), (1, 0), (0, 1), (3, 2), (bindscoach.LEFT_OUT_LIMIT, 0)]:
        words = bindscoach.unseen_words(commands, keys)
        for name in ("Bartender", "Dominos", "ElvUI", "GSE", "BindPad"):
            assert name not in words
    assert bindscoach.unseen_words(3, 2).startswith(
        "Left out of the bindings record, counted with no list: 3 binding commands it did not "
        "read, whether or not a key is bound to them (not named in plain upper case), and 2 keys "
        "whose name is not printable ASCII. Any of these"
    )


def test_unseen_is_said_at_zero_too_constructed() -> None:
    """With nothing left out, the counts and the standing note are still said."""
    rec = real_record()
    binds = rec.bindings
    assert isinstance(binds, labaddon.Bindings)
    clean = rec.model_copy(update={"bindings": binds.model_copy(update={"commands_left_out": 0})})
    got = bindscoach.coach(real_counts(), clean, default_table())
    assert (got.commands_unseen, got.keys_unseen) == (0, 0)
    assert got.unseen == (
        "Left out of the bindings record, counted with no list: 0 binding commands it did not "
        "read, whether or not a key is bound to them (not named in plain upper case), and 0 keys "
        "whose name is not printable ASCII. A key bound straight to a spell or to an addon's "
        "button may be in neither count [verify]."
    )


def test_no_bindings_record_has_no_unseen_counts(root: Path) -> None:
    rep = report("--character", CHARACTER)  # no WowLab.lua in that folder
    assert (rep.commands_unseen, rep.keys_unseen, rep.unseen) == (None, None, None)


def test_left_out_limit_reason_says_plain_command_names_constructed() -> None:
    rec = real_record()
    binds = rec.bindings
    assert isinstance(binds, labaddon.Bindings)
    many = rec.model_copy(
        update={
            "bindings": binds.model_copy(update={"commands_left_out": bindscoach.LEFT_OUT_LIMIT})
        }
    )
    got = bindscoach.coach(
        constructed_counts(cast(697, "Summon Voidwalker")), many, default_table()
    )
    reasons = [c.reason for c in got.cannot_judge if c.report == "cast-without-bind"]
    assert reasons and "that are not plain command names" in reasons[0]


def test_no_coach_output_names_an_addon_cross_capture(record: Path) -> None:
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    for name in ("Bartender", "Dominos", "ElvUI"):
        assert name not in text


# ─── the combat log, by caster name ──────────────────────────────────────────


def test_real_log_source_names() -> None:
    assert real_counts().source_names == (LOG_CASTER,)


def test_source_names_are_capped_constructed() -> None:
    lines = [
        cast(172, "Corruption")
        .replace("Constructed-Unit-", f"Unit{i}-Realm-")
        .replace("Player-9999-00000001", f"Player-9999-{i:08d}")
        for i in range(combatlog.SOURCE_NAMES_KEPT + 5)
    ]
    got = constructed_counts(*lines)
    assert got.mine == combatlog.SOURCE_NAMES_KEPT + 5
    assert len(got.source_names) == combatlog.SOURCE_NAMES_KEPT
    assert got.source_names[0] == "Unit0-Realm-"


def test_the_log_with_the_characters_casts_is_chosen_cross_capture(
    flavor: Path, namesake: Path
) -> None:
    """A newer log by someone else is passed over for the real one, whose
    caster's first name is the character folder's."""
    caster_log(
        flavor, "Constructed-Unit-", cast(755, "Health Funnel"), name="WoWCombatLog-a.txt", offset=5
    )
    rep = report("--character", f"1/{NAMESAKE}")
    assert rep.log.file == f"Logs/{LOG_NAME}"
    assert (rep.log.chosen_by, rep.log.character_name, rep.log.caster_match) == (
        "caster name",
        "Labchard",
        "matches",
    )
    assert rep.log.casters == [LOG_CASTER] and rep.log.warning is None
    text = ok("binds", "coach", "--character", f"1/{NAMESAKE}").stdout
    assert f"Combat log: Logs/{LOG_NAME} (the newest with casts by Labchard) (79 lines;" in text
    assert "Warning:" not in text


def test_the_newest_matching_log_is_read_once(
    flavor: Path, namesake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The newest log matches: no other log is opened."""
    caster_log(
        flavor,
        "Other-Elsewhere-",
        cast(755, "Health Funnel"),
        name="WoWCombatLog-a.txt",
        offset=5,
    )
    caster_log(
        flavor,
        "Labchard-Elsewhere-",
        cast(755, "Health Funnel"),
        name="WoWCombatLog-b.txt",
        offset=1,
    )
    read: list[str] = []
    real_read_log = combatlog.read_log

    def spy(path: Path, **kwargs: Any) -> Any:
        read.append(Path(path).name)
        return real_read_log(path, **kwargs)

    monkeypatch.setattr(combatlog, "read_log", spy)
    rep = report("--character", f"1/{NAMESAKE}")
    assert read == ["WoWCombatLog-b.txt"]
    assert (rep.log.file, rep.log.chosen_by) == ("Logs/WoWCombatLog-b.txt", "caster name")


def test_a_log_by_another_character_warns_cross_capture(record: Path) -> None:
    """CHARACTER's first name is not the real log's caster's: the only log is
    read, with a warning naming whose casts it holds."""
    rep = report("--character", CHARACTER)
    assert (rep.log.file, rep.log.chosen_by, rep.log.caster_match) == (
        f"Logs/{LOG_NAME}",
        "newest",
        "does not match",
    )
    assert rep.log.warning == (
        f"the only combat log has no casts by Labcharb, the character judged; its casts are by "
        f"{LOG_CASTER}: the findings below may be about another character."
    )
    lines = ok("binds", "coach", "--character", CHARACTER).stdout.splitlines()
    assert lines[0].startswith(f"Combat log: Logs/{LOG_NAME} (the newest) (79 lines;")
    assert lines[1] == f"  Warning: {rep.log.warning}"


def test_no_matching_log_falls_back_to_the_newest_constructed(flavor: Path, record: Path) -> None:
    caster_log(
        flavor, "Someone-Else-", cast(755, "Health Funnel"), name="WoWCombatLog-a.txt", offset=5
    )
    rep = report("--character", CHARACTER)
    assert (rep.log.file, rep.log.chosen_by, rep.log.scan_note) == (
        "Logs/WoWCombatLog-a.txt",
        "newest",
        None,
    )
    assert rep.log.warning == (
        "none of the 2 combat logs read has casts by Labcharb, the character judged, "
        "so the newest is read; its casts are by Someone-Else-: the findings below may be about "
        "another character."
    )


def test_only_the_newest_logs_are_looked_through_constructed(flavor: Path, namesake: Path) -> None:
    """The real log (by the character) is older than _COACH_LOG_SCAN other
    logs: it is not looked at, and the newest is read with the warning."""
    for i in range(cli._COACH_LOG_SCAN):
        caster_log(
            flavor,
            "Someone-Else-",
            cast(755, "Health Funnel"),
            name=f"WoWCombatLog-{i:02d}.txt",
            offset=i + 1,
        )
    rep = report("--character", f"1/{NAMESAKE}")
    last = f"WoWCombatLog-{cli._COACH_LOG_SCAN - 1:02d}.txt"
    assert (rep.log.file, rep.log.chosen_by, rep.log.caster_match) == (
        f"Logs/{last}",
        "newest",
        "does not match",
    )
    assert rep.log.warning is not None
    assert rep.log.warning.startswith(f"none of the {cli._COACH_LOG_SCAN} combat logs read")


def test_an_unreadable_older_log_is_skipped_and_said_constructed(
    flavor: Path, namesake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulated: read_log raises for the real (older) log. The newest is
    read, and one line names the log skipped."""
    newest = caster_log(
        flavor, "Someone-Else-", cast(755, "Health Funnel"), name="WoWCombatLog-a.txt", offset=5
    )
    real_read_log = combatlog.read_log

    def read_log(path: Any, **kwargs: Any) -> Any:
        if Path(path).name != newest.name:
            raise PermissionError(13, "Permission denied", str(path))
        return real_read_log(path, **kwargs)

    monkeypatch.setattr(combatlog, "read_log", read_log)
    rep = report("--character", f"1/{NAMESAKE}")
    assert (rep.log.file, rep.log.chosen_by) == ("Logs/WoWCombatLog-a.txt", "newest")
    assert (
        rep.log.scan_note == f"1 older combat log could not be read and was skipped ({LOG_NAME})."
    )
    text = ok("binds", "coach", "--character", f"1/{NAMESAKE}").stdout
    assert f"  Note: {rep.log.scan_note}\n" in text


def test_the_older_logs_scan_stops_at_its_byte_budget_constructed(
    flavor: Path, namesake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a budget smaller than the real log, the real (older) log is not
    read: the newest is, and one line says the scan stopped."""
    caster_log(
        flavor, "Someone-Else-", cast(755, "Health Funnel"), name="WoWCombatLog-a.txt", offset=5
    )
    real_size = (flavor / "Logs" / LOG_NAME).stat().st_size
    monkeypatch.setattr(cli, "_COACH_LOG_BUDGET", real_size - 1)
    rep = report("--character", f"1/{NAMESAKE}")
    assert (rep.log.file, rep.log.caster_match) == ("Logs/WoWCombatLog-a.txt", "does not match")
    assert rep.log.scan_note is not None
    assert rep.log.scan_note == (
        f"1 older combat log ({LOG_NAME}) was not read, since older logs are looked through only "
        "up to 0 MiB in all; --log FILE reads one."
    )
    assert rep.log.warning is not None
    assert rep.log.warning.startswith(
        "the newest combat log, the only one read, has no casts by Labchard, the character judged;"
    )
    monkeypatch.setattr(cli, "_COACH_LOG_BUDGET", real_size)
    rep = report("--character", f"1/{NAMESAKE}")
    assert (rep.log.file, rep.log.chosen_by, rep.log.scan_note) == (
        f"Logs/{LOG_NAME}",
        "caster name",
        None,
    )


def test_a_newest_log_without_your_casts_has_no_warning_constructed(
    flavor: Path, record: Path
) -> None:
    """A log with none of your casts cannot be told to be anyone's; its
    reports already say it has none."""
    caster_log(
        flavor,
        "Constructed-Unit-",
        cast(172, "Corruption", flags="0x10a48"),
        name="WoWCombatLog-a.txt",
        offset=5,
    )
    rep = report("--character", CHARACTER)
    # The older real log is not the character's either, so the newest is read.
    assert (rep.log.file, rep.log.caster_match, rep.log.warning) == (
        "Logs/WoWCombatLog-a.txt",
        "no casts",
        None,
    )


def test_log_option_warns_on_a_mismatch_cross_capture(record: Path, namesake: Path) -> None:
    rep = report("--character", CHARACTER, "--log", LOG_NAME)
    assert (rep.log.chosen_by, rep.log.caster_match) == ("--log", "does not match")
    assert rep.log.warning == (
        f"this log's casts are by {LOG_CASTER}, not Labcharb, the character judged: the "
        "findings below may be about another character."
    )
    rep = report("--character", f"1/{NAMESAKE}", "--log", LOG_NAME)
    assert (rep.log.chosen_by, rep.log.caster_match, rep.log.warning) == ("--log", "matches", None)


def test_first_names_match_without_case_or_normal_form_constructed() -> None:
    """A folder name read back decomposed (as macOS may store it) and a log
    name in another case still match."""
    decomposed = unicodedata.normalize("NFD", "Zoë-Second")
    counts = constructed_counts(cast(172, "Corruption").replace("Constructed-Unit-", "zoË-Realm-"))
    assert cli._caster_match(counts, decomposed) == "matches"
    assert cli._caster_match(counts, "Zoe-Second") == "does not match"
    assert cli._caster_match(counts, None) == "no character"


def test_casts_without_a_quoted_name_are_not_no_casts_constructed() -> None:
    line = cast(172, "Corruption").replace('"Constructed-Unit-"', "nil")
    counts = constructed_counts(line)
    assert (counts.mine, counts.source_names) == (1, ())
    assert cli._caster_match(counts, "Labchard-Labrealmg") == "no caster name"
    none = constructed_counts(cast(172, "Corruption", flags="0x10a48"))
    assert cli._caster_match(none, "Labchard-Labrealmg") == "no casts"


def test_an_oversized_older_log_is_skipped_and_smaller_ones_still_read_constructed(
    flavor: Path, namesake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Budget patched to the real log's size: the second-newest log is
    larger (constructed padding), so it is not read and the scan goes on to
    the older real log, which has the character's casts."""
    real_size = (flavor / "Logs" / LOG_NAME).stat().st_size
    padding = [cast(755, "Health Funnel")] * (real_size // 100 + 10)
    big = caster_log(flavor, "Someone-Else-", *padding, name="WoWCombatLog-b.txt", offset=3)
    assert big.stat().st_size > real_size
    caster_log(
        flavor, "Someone-Else-", cast(755, "Health Funnel"), name="WoWCombatLog-c.txt", offset=6
    )
    monkeypatch.setattr(cli, "_COACH_LOG_BUDGET", real_size)
    rep = report("--character", f"1/{NAMESAKE}")
    assert (rep.log.file, rep.log.chosen_by, rep.log.caster_match) == (
        f"Logs/{LOG_NAME}",
        "caster name",
        "matches",
    )
    assert rep.log.scan_note is not None
    assert rep.log.scan_note.startswith(
        "1 older combat log (WoWCombatLog-b.txt) was not read, since older logs are looked "
        "through only up to "
    )
    assert rep.log.scan_note.endswith("; --log FILE reads one.")


def test_a_first_name_two_folders_share_is_said_cross_capture(flavor: Path, record: Path) -> None:
    """The captured tree has two real folders with the first name Labcharb
    (`1/Labcharb-Labrealmd`, the record's, and `1/Labcharb-Labrealmf`). A
    constructed log by `Labcharb-…` matches the first name, which is shared."""
    caster_log(
        flavor, "Labcharb-Realm-", cast(755, "Health Funnel"), name="WoWCombatLog-a.txt", offset=5
    )
    rep = report("--character", CHARACTER)
    assert (rep.log.file, rep.log.chosen_by, rep.log.caster_match) == (
        "Logs/WoWCombatLog-a.txt",
        "caster name",
        "first name shared",
    )
    assert rep.log.warning == (
        f"1/{OTHER} also has the first name Labcharb, and the coach matches a log to a "
        "character by first name only: these casts may be another of your characters'."
    )
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert f"  Warning: {rep.log.warning}\n" in text


def test_a_retail_style_twin_is_not_another_character_cross_capture(
    flavor: Path, namesake: Path
) -> None:
    """`Labrealmb Partb Partc Partd/Labchard` holds only AddOns.txt beside the
    real `1/Labchard-Labrealmg`: a twin, not a second Labchard."""
    twin = flavor / f"WTF/Account/{ACCOUNT}/Labrealmb Partb Partc Partd/Labchard"
    assert sorted(p.name for p in twin.iterdir()) == ["AddOns.txt"]
    rep = report("--character", f"1/{NAMESAKE}")
    assert (rep.log.caster_match, rep.log.warning) == ("matches", None)


# ─── the minimum sample for "bound, never cast" ──────────────────────────────


def test_bound_never_cast_is_withheld_below_the_minimum_constructed() -> None:
    """Boundary: one cast under the shipped minimum withholds, the minimum
    judges."""
    table = default_table()
    least = table.never_cast_min_casts
    assert least == 200
    under = bindscoach.coach(
        constructed_counts(*[cast(172, "Corruption")] * (least - 1)), real_record(), table
    )
    assert under.never_cast_withheld and under.bound_never_cast == []
    [c] = under.cannot_judge
    assert c.report == "bound-never-cast"
    assert c.reason.startswith(f"withheld: the log has {least - 1} of your casts, under the ")
    assert f"minimum sample of {least}" in c.reason
    at = bindscoach.coach(
        constructed_counts(*[cast(172, "Corruption")] * least), real_record(), table
    )
    assert not at.never_cast_withheld and at.cannot_judge == []
    assert at.bound_never_cast and 2 not in {b.slot for b in at.bound_never_cast}


def test_no_casts_is_not_withheld_but_cannot_judge_constructed() -> None:
    got = bindscoach.coach(
        constructed_counts(cast(172, "Corruption", flags="0x10a48")), real_record(), default_table()
    )
    assert not got.never_cast_withheld
    assert [c.reason for c in got.cannot_judge] == [
        "the log has none of your casts, so every bound spell would be listed"
    ]


def test_the_minimum_is_stated_in_text_and_json_cross_capture(record: Path) -> None:
    rep = report("--character", CHARACTER)
    assert rep.never_cast_withheld and rep.costs.table.never_cast_min_casts == 200
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert (
        "Binds to spells not cast in this log: withheld, this log has 6 of your casts, under "
        "the minimum sample of 200\n" in text
    )
    assert bindscoach.RARE_SPELLS not in rep.caveats  # withheld: no list to qualify


def test_a_judged_list_says_rare_spells_can_be_missing_constructed() -> None:
    table = default_table().model_copy(update={"never_cast_min_casts": 1})
    got = bindscoach.coach(constructed_counts(cast(172, "Corruption")), real_record(), table)
    assert got.bound_never_cast and bindscoach.RARE_SPELLS in got.caveats


def test_cannot_judge_counts_agree_in_text_and_json(record: Path) -> None:
    rep = report("--character", CHARACTER)
    text = ok("binds", "coach", "--character", CHARACTER).stdout
    assert f"Cannot judge: {len(rep.cannot_judge)}\n" in text


def test_no_casts_heading_has_no_sample_words_constructed(flavor: Path, record: Path) -> None:
    caster_log(
        flavor,
        "Constructed-Unit-",
        cast(172, "Corruption", flags="0x10a48"),
        name="WoWCombatLog-a.txt",
        offset=5,
    )
    text = ok("binds", "coach", "--character", CHARACTER, "--log", "WoWCombatLog-a.txt").stdout
    assert "Binds to spells not cast in this log: 0\n" in text
    assert "judged from" not in text


# ─── the default character ───────────────────────────────────────────────────


def test_default_character_is_the_newest_schema_cross_capture(flavor: Path, record: Path) -> None:
    """A schema-4 record beats a schema-3 one written later, and says so."""
    four = flavor / OTHER_RECORD
    shutil.copy2(SCHEMA_4, four)
    newest = _newest(flavor)
    _mtime(four, newest + 10**9)
    _mtime(record, newest + 5 * 10**9)  # the schema-3 record is written last
    rep = report()
    assert (rep.character, rep.chosen_by, rep.record_schema) == (f"1/{OTHER}", "newest schema", 4)
    assert rep.chosen_because == (
        "its record has the newest schema (4) of the 3 characters' records; "
        f"1/{CHARACTER} was written later, at schema 3"
    )
    text = ok("binds", "coach").stdout
    assert (
        f"Action bars and bindings: {OTHER_RECORD} (schema 4) ({rep.chosen_because}; choose "
        "another with --character)\n" in text
    )


def test_default_character_then_the_newest_mtime_cross_capture(
    flavor: Path, record: Path, namesake: Path
) -> None:
    newest = _newest(flavor)
    _mtime(record, newest + 1 * 10**9)
    _mtime(namesake, newest + 2 * 10**9)
    rep = report()
    assert (rep.character, rep.record_schema) == (f"1/{NAMESAKE}", 3)
    assert rep.chosen_because == (
        "its record has the newest schema (3) of the 3 characters' records, and of the 2 at "
        "that schema it was written last"
    )
    _mtime(record, newest + 3 * 10**9)
    assert report().character == f"1/{CHARACTER}"


def test_default_character_tie_is_said_cross_capture(
    flavor: Path, record: Path, namesake: Path
) -> None:
    newest = _newest(flavor) + 10**9
    _mtime(record, newest)
    _mtime(namesake, newest)
    rep = report()
    assert rep.chosen_because is not None
    assert rep.chosen_because.endswith(
        f"; tied on that with 1/{min(CHARACTER, NAMESAKE)}, it is the last by folder name"
    )
    assert rep.character == f"1/{max(CHARACTER, NAMESAKE)}"


def test_default_character_without_a_usable_record_still_picks_the_log_by_name_constructed(
    flavor: Path, namesake: Path
) -> None:
    """The top-ranked folder's record cannot be used: it is not a literal
    table (constructed, rejected by the parser, never run), and it is the
    only character record, so it ranks first. Its folder still names the
    character the log is chosen for. An invalid file, not two case variants
    of the name, so that this holds on a case-insensitive volume too."""
    (flavor / OTHER_RECORD).unlink()
    namesake.write_bytes(b"WowLabDB = function() end\n")
    caster_log(
        flavor, "Someone-Else-", cast(755, "Health Funnel"), name="WoWCombatLog-a.txt", offset=5
    )
    rep = report()
    assert rep.record_file == NAMESAKE_RECORD and rep.record_error is not None
    assert rep.record_schema is None
    assert rep.log.character_name == "Labchard"
    assert (rep.log.file, rep.log.chosen_by) == (f"Logs/{LOG_NAME}", "caster name")


def test_an_unreadable_record_ranks_last_constructed(flavor: Path, record: Path) -> None:
    broken = flavor / OTHER_RECORD
    broken.write_bytes(b"WowLabDB = function() end\n")  # constructed: rejected, never run
    _mtime(broken, _newest(flavor) + 10**9)
    rep = report()
    assert (rep.character, rep.record_schema, rep.record_error) == (f"1/{CHARACTER}", 3, None)


def test_the_only_character_is_said(flavor: Path, record: Path) -> None:
    for rel in (OTHER_RECORD, NAMESAKE_RECORD):
        (flavor / rel).unlink()
    rep = report()
    assert rep.chosen_because == "the only character with a lab-addon record"
    assert rep.character == f"1/{CHARACTER}"


def test_character_option_has_no_reason(record: Path) -> None:
    rep = report("--character", CHARACTER)
    assert (rep.chosen_by, rep.chosen_because) == ("--character", None)


def test_char_commands_keep_written_last(flavor: Path, record: Path) -> None:
    """Only `binds coach` takes the newest schema; `char binds` still takes
    the record written last (docs/LAB_PLAN.md §16.2, amended 2026-11-04)."""
    four = flavor / OTHER_RECORD
    shutil.copy2(SCHEMA_4, four)
    newest = _newest(flavor)
    _mtime(four, newest + 10**9)
    _mtime(record, newest + 5 * 10**9)
    out = ok("char", "binds", "--json").stdout
    assert cli.CharBindsReport.model_validate_json(out).character == f"1/{CHARACTER}"
    assert report().character == f"1/{OTHER}"
