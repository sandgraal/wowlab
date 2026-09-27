"""`scripts/lab_capture.py`: fifth follow-up to M10-02, review fix round 1.

- Owner decision 2026-09-24, security re-review H1, M-b, L-c, L-d: every
  combat-log timestamp and the stamp in the log's file name move by a secret
  offset drawn for that log alone, into one fixed shape verified on real logs;
  another source shape, a changing UTC-offset suffix, or a date or time inside
  a record refuses the log; --combat-log messages never echo a name.
- Code review 3: an oracle on the synthetic install with a real-range offset.
- M1: only `WoWCombatLog[-MMDDYY_HHMMSS].txt` names are captured, and a log
  that is compressed, holds a NUL byte or is not UTF-8 is refused unscrubbed.
- L3: `--combat-log` also requires the combat-log kind.
- L4: a GUID body without a type, or with lookalike dashes, refuses the log.
- L5 and code review 1: the owner's own names, other players' names and loose
  second names are hunted in a folded form (NFKD, format characters and
  non-spacing marks dropped, casefold) with lookalike separators; detect only.
- Code review 4: disabling the `--combat-log` name check fails a test here.

CONSTRUCTED INPUT. Every log line and file body below is written for the test
in the shape of docs/LAB_FORMATS.md section 8, with invented names, GUIDs,
dates and offsets only; trees are synthetic, under `tmp_path`. No test needs
or touches a real install (ADR-0012).
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

import pytest
from conftest import DEFAULT_OFFSET
from test_lab_capture import (
    _sensitive_spans,
    build_install,
    capture,
    lab_capture,
    outputs,
    source_of,
)

Identity = lab_capture.Identity
OtherPlayers = lab_capture.OtherPlayers
TimeShift = lab_capture.TimeShift
UNCLASSIFIED = lab_capture.UNIT_GUID_UNCLASSIFIED_LABEL
OTHER_NAME = lab_capture.OTHER_NAME_LABEL
FOLDED = lab_capture.FOLDED_SURVIVOR_LABEL
UNSTAMPED = lab_capture.UNSTAMPED_LABEL
UNSHIFTABLE = lab_capture.UNSHIFTABLE_LABEL
RECORD_TIME = lab_capture.RECORD_TIME_LABEL
UNVERIFIED = lab_capture.UNVERIFIED_STAMP_LABEL
SUFFIX_CHANGE = lab_capture.SUFFIX_CHANGE_LABEL

OFFSET = 100 * 86400 + 3661  # invented: 100 days, 1 hour, 1 minute, 1 second
OWN = b"Player-1-0000ABCD"
OWN_UNIT = b'Player-1-0000ABCD,"Orlavin-KestrelHollow-",0x511,0x0'
BOAR = b"Creature-0-3771-0-58-2222-00004A2C11"
TS = b"9/20/2026 21:14:05.871-4  "
HEADER = TS + b"COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
LOG_NAME = "WoWCombatLog-092026_211403.txt"


def _identity(**extra: object) -> object:
    return Identity(characters=["Orlavin", "Ash"], realms=["Kestrel Hollow"], guids=[OWN], **extra)


def _process(
    log: bytes,
    tmp_path: Path,
    *,
    shift: object | None = None,
    others: object | None = None,
    name: str = LOG_NAME,
) -> tuple[list[str], bytes, object]:
    source = tmp_path / name
    source.write_bytes(log)
    rel = PurePosixPath(f"_classic_beta_/Logs/{name}")
    item = lab_capture.Item(source, rel, "_classic_beta_", "1", lab_capture.kind_of(name))
    outcome = lab_capture.process(item, _identity(), None, others, None, shift)
    return list(outcome.problems), outcome.result.data, outcome


def _line(body: bytes, stamp: bytes = TS) -> bytes:
    return stamp + body + b"\n"


# ─── timestamps: shifted into the one verified shape ─────────────────────────


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        (b"9/20/2026 21:14:05.871-4", b"12/29/2026 22:15:06.871-4"),
        (b"9/20/2026 21:14:05.1234+2", b"12/29/2026 22:15:06.1234+2"),  # fraction, suffix kept
        (b"9/20/2026 21:14:05", b"12/29/2026 22:15:06"),  # no fraction, no suffix
        (b"9/5/2026 07:03:01.000-4", b"12/14/2026 08:04:02.000-4"),  # one-digit day
    ],
    ids=["retail", "fraction-and-suffix", "bare", "one-digit-day"],
)
def test_constructed_timestamp_moves_by_the_offset_and_keeps_its_shape(
    stamp: bytes, expected: bytes, tmp_path: Path
) -> None:
    log = _line(b"COMBAT_LOG_VERSION,22", stamp + b"  ") + _line(
        b"SPELL_DAMAGE," + BOAR + b',"Boar"', stamp + b"  "
    )
    problems, data, outcome = _process(log, tmp_path, shift=TimeShift(OFFSET))
    assert problems == []
    assert data.count(expected + b"  ") == 2 and stamp + b"  " not in data
    assert outcome.timestamps_shifted and outcome.result.count("timestamp") == 2


def test_constructed_fixed_output_shape_month_day_unpadded_hour_padded(tmp_path: Path) -> None:
    """-200 days + 6 hours from 9/20 21:14 and 10/1 21:14: month and day come out
    unpadded (`3/5`, `3/16`), the hour always two digits (`03`), the year four."""
    log = _line(b"X,1") + _line(b"X,2", b"10/1/2026 21:14:05.871-4  ")
    problems, data, _o = _process(log, tmp_path, shift=TimeShift(-200 * 86400 + 6 * 3600))
    assert problems == []
    assert data == b"3/5/2026 03:14:05.871-4  X,1\n3/16/2026 03:14:05.871-4  X,2\n"


@pytest.mark.parametrize(
    "stamp",
    [
        b"9/20 21:14:05.871-4",  # no year
        b"9/20/26 21:14:05.871-4",  # two-digit year
        b"09/20/2026 21:14:05.871-4",  # padded month
        b"9/05/2026 21:14:05.871-4",  # padded day (unverified: report it)
        b"9/20/2026 1:14:05.871-4",  # one-digit hour
    ],
    ids=["yearless", "two-digit-year", "padded-month", "padded-day", "one-digit-hour"],
)
def test_constructed_timestamp_in_an_unverified_shape_refuses(stamp: bytes, tmp_path: Path) -> None:
    log = HEADER + _line(b"X,2", stamp + b"  ")
    problems, _d, _o = _process(log, tmp_path, shift=TimeShift(OFFSET))
    assert problems == [f"{UNVERIFIED} x1"]


@pytest.mark.parametrize(
    "stamp",
    [b"9/20/2026 21:14:06.000-5", b"9/20/2026 21:14:06.000+1", b"9/20/2026 21:14:06.000"],
    ids=["dst-change", "other-zone", "suffix-dropped"],
)
def test_constructed_utc_suffix_that_changes_within_the_log_refuses(
    stamp: bytes, tmp_path: Path
) -> None:
    log = HEADER + _line(b"X,1") + _line(b"X,2", stamp + b"  ") + _line(b"X,3")
    problems, _d, _o = _process(log, tmp_path, shift=TimeShift(OFFSET))
    assert problems == [f"{SUFFIX_CHANGE} x1"]  # a count only


def test_constructed_impossible_date_refuses(tmp_path: Path) -> None:
    log = _line(b"X,1") + _line(b"X,2", b"2/30/2026 21:14:05.871-4  ")
    problems, _d, _o = _process(log, tmp_path, shift=TimeShift(OFFSET))
    assert problems == [f"{UNSHIFTABLE} x1"]


@pytest.mark.parametrize(
    "line",
    [b"COMBAT_LOG_VERSION,22\n", b"9/20/2026  X,1\n", b"20.09.2026 21:14:05  X,1\n"],
    ids=["no-stamp", "no-time", "other-date-shape"],
)
def test_constructed_line_without_a_recognised_timestamp_refuses(
    line: bytes, tmp_path: Path
) -> None:
    log = HEADER + line + b"\n\r\n" + _line(b"X,2")  # blank lines are no records
    problems, _d, _o = _process(log, tmp_path, shift=TimeShift(OFFSET))
    assert problems == [f"{UNSTAMPED} x1"]


@pytest.mark.parametrize(
    "field",
    [b'"Warden of 9/21"', b'"Opens at 21:15"', b"2026-09-21", b'"at 21:15:30"', b"9/21/2026"],
)
def test_constructed_date_or_time_inside_a_record_refuses(field: bytes, tmp_path: Path) -> None:
    log = HEADER + _line(b"ENCOUNTER_START,1234," + field + b",1,5,36")
    problems, _d, _o = _process(log, tmp_path, shift=TimeShift(OFFSET))
    assert problems == [f"{RECORD_TIME} x1"]


def test_constructed_record_numbers_are_not_dates(tmp_path: Path) -> None:
    """GUID digit groups, hex flags, decimals and bracketed groups are not dates."""
    log = HEADER + _line(
        b"SPELL_DAMAGE," + OWN_UNIT + b"," + BOAR + b',"Boar",0x10a48,0x0,1.5,-12.25,'
        b"2026,9,[(1,2),(3,4)],0000000000000000,nil,12.1.5"
    )
    problems, _d, _o = _process(log, tmp_path, shift=TimeShift(OFFSET))
    assert problems == []


def test_constructed_file_name_moves_with_the_offset(tmp_path: Path) -> None:
    problems, _d, outcome = _process(HEADER, tmp_path, shift=TimeShift(OFFSET))
    assert problems == []
    assert outcome.dest.name == "WoWCombatLog-122926_221504.txt"
    row = lab_capture.provenance_row(outcome, "macos", "owner", "owner")
    assert "WoWCombatLog-122926_221504.txt" in row and "092026_211403" not in row
    assert "timestamps-shifted" in row.split(" | ")[7]
    assert TimeShift(OFFSET).file_name("WoWCombatLog.txt") == "WoWCombatLog.txt"


def test_constructed_impossible_file_name_date_refuses_and_withholds_the_path(
    tmp_path: Path,
) -> None:
    problems, _d, outcome = _process(
        HEADER, tmp_path, shift=TimeShift(OFFSET), name="WoWCombatLog-133226_211403.txt"
    )
    assert lab_capture.LOG_NAME_UNSHIFTABLE_LABEL in problems
    assert outcome.label.name == "<path withheld>"


def test_constructed_the_offset_is_never_shown() -> None:
    assert str(OFFSET) not in repr(TimeShift(OFFSET))
    assert repr(TimeShift(OFFSET)) == repr(TimeShift(-OFFSET))


def test_constructed_draw_uses_secrets_and_spans_months_either_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()  # the suite's fixed offset (conftest.py)
    draws = [lab_capture.draw_time_shift() for _ in range(400)]
    low, high = lab_capture.SHIFT_MIN_SECONDS, lab_capture.SHIFT_MAX_SECONDS
    assert all(isinstance(d, int) and low <= abs(d) <= high for d in draws)
    assert min(draws) < 0 < max(draws) and len(set(draws)) > 390
    assert low >= 60 * 86400  # at least two months either way
    calls: list[int] = []

    def fake(bound: int) -> int:
        calls.append(bound)
        return 0

    monkeypatch.setattr(lab_capture.secrets, "randbelow", fake)
    assert lab_capture.draw_time_shift() == -low
    assert calls == [high - low + 1, 2]


# ─── timestamps through the command line ─────────────────────────────────────

CLI_TS = b"9/20/2026 21:14:05.871-4  "
CLI_OWN = b'Player-1234-0ABCDEF0,"Thrallmar-Area52-US",0x511,0x0'
OLDER = "WoWCombatLog-091926_100000.txt"
OTHER_OFFSET = -(97 * 86400 + 12345)  # invented, inside the real range


def _add_log(root: Path, name: str, body: bytes, mtime: int) -> None:
    path = root / "_retail_" / "Logs" / name
    path.write_bytes(body)
    os.utime(path, (mtime, mtime))


def test_constructed_cli_gives_each_log_its_own_offset_and_never_prints_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """H1: one draw per log, in capture order (the --combat-log order here)."""
    root = tmp_path / "World of Warcraft"
    build_install(root)
    older = CLI_TS + b"SWING_DAMAGE," + CLI_OWN + b"," + BOAR + b',"Boar"\n'
    _add_log(root, OLDER, older, 1_800_000_000)
    draws = iter([OFFSET, OTHER_OFFSET])
    monkeypatch.setattr(lab_capture, "draw_time_shift", lambda: next(draws))
    out = tmp_path / "incoming"
    code = capture(
        root, out, "--flavor", "_retail_", "--combat-log", OLDER, "--combat-log", LOG_NAME
    )
    assert code == 0
    assert next(draws, None) is None  # exactly one draw per log
    printed = capsys.readouterr()
    logs = {PurePosixPath(d).name: data for d, data in outputs(out).items() if "Combat" in d}
    older_name = TimeShift(OFFSET).file_name(OLDER)
    newer_name = TimeShift(OTHER_OFFSET).file_name(LOG_NAME)
    assert set(logs) == {older_name, newer_name}
    assert older_name == "WoWCombatLog-122826_110101.txt"
    assert logs[older_name].startswith(b"12/29/2026 22:15:06.871-4  ")
    # 9/20/2026 21:14:03 minus 97 days, 3:25:45: 6/15/2026 17:48:18.
    assert newer_name == "WoWCombatLog-061526_174818.txt"
    assert logs[newer_name].startswith(b"6/15/2026 17:48:18.123-4  ")
    rows = [line for line in printed.out.splitlines() if line.startswith("| ") and "Combat" in line]
    assert len(rows) == 2 and all("timestamps-shifted" in row for row in rows)
    for text in (printed.out, printed.err):
        assert "091926_100000" not in text and "092026_211403" not in text
        assert str(OFFSET) not in text and str(-OTHER_OFFSET) not in text
    for data in outputs(out).values():
        assert str(OFFSET).encode() not in data and str(-OTHER_OFFSET).encode() not in data


def test_constructed_two_identical_logs_get_independent_real_offsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real `secrets` draw, once per log: two logs with the same timestamps come
    out with different ones (equal only with odds of about one in 60 million)."""
    monkeypatch.undo()  # the suite's fixed offset (conftest.py)
    root = tmp_path / "World of Warcraft"
    build_install(root)
    body = CLI_TS + b"SWING_DAMAGE," + CLI_OWN + b"," + BOAR + b',"Boar"\n'
    _add_log(root, OLDER, body, 1_800_000_000)
    _add_log(root, "WoWCombatLog-091926_100001.txt", body, 1_800_000_001)
    out = tmp_path / "incoming"
    args = ("--flavor", "_retail_", "--combat-log", OLDER)
    assert capture(root, out, *args, "--combat-log", "WoWCombatLog-091926_100001.txt") == 0
    logs = [data for dest, data in outputs(out).items() if "Combat" in dest]
    assert len(logs) == 2
    stamps = [data.split(b"  ", 1)[0] for data in logs]
    assert stamps[0] != stamps[1] and CLI_TS.strip() not in stamps


# ─── code review 3: only timestamp, identity and GUID spans change ───────────

_STAMP_SPAN = re.compile(rb"(?m)^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}")
_GUID_SPAN = re.compile(rb"(?:Player|Creature|Pet|Vehicle|GameObject)-[0-9A-Fa-f-]+")


def test_constructed_capture_changes_only_timestamp_identity_and_guid_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An oracle that never asks the tool what it edited, with an offset in the real
    range: each output is mapped back to its source (shifted log names included),
    the source is cut at every timestamp, identity and GUID span, and every piece
    between the cuts must come out unchanged, in order, with no line added or lost."""
    monkeypatch.setattr(lab_capture, "draw_time_shift", lambda: OTHER_OFFSET)
    root = tmp_path / "World of Warcraft"
    build_install(root)
    out = tmp_path / "incoming"
    assert capture(root, out) == 0
    shifted = {TimeShift(OTHER_OFFSET).file_name(p.name): p.name for p in root.glob("*/Logs/*")}
    logs = 0
    for dest, data in outputs(out).items():
        parts = dest.split("/")
        if parts[-1] in shifted:
            parts[-1] = shifted[parts[-1]]
            logs += 1
        original = source_of(root, "/".join(parts)).read_bytes()
        if "WoWCombatLog" in dest:
            original = b"".join(original.splitlines(keepends=True)[:2000])
        found = sorted(
            [
                *_sensitive_spans(original),
                *(m.span() for m in _STAMP_SPAN.finditer(original)),
                *(m.span() for m in _GUID_SPAN.finditer(original)),
            ]
        )
        spans: list[tuple[int, int]] = []
        for start, end in found:
            if spans and start <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
            else:
                spans.append((start, end))
        cuts = [0, *(offset for span in spans for offset in span), len(original)]
        pieces = [original[a:b] for a, b in zip(cuts[0::2], cuts[1::2], strict=True)]
        pattern = rb"[^\r\n]*?".join(re.escape(piece) for piece in pieces)
        assert re.fullmatch(pattern, data, re.DOTALL), f"{dest}: a byte outside the spans changed"
        assert data.count(b"\n") == original.count(b"\n"), dest
        if "WoWCombatLog" in dest:
            assert data.startswith(b"6/15/2026 17:48:18.123-4  "), dest  # the shift ran
    assert logs == 2  # one log per flavor, each found under its shifted name


# ─── M1 / L3: names and content ──────────────────────────────────────────────


def test_constructed_default_pick_ignores_compressed_and_odd_names(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    for name in (f"{LOG_NAME}.gz", "WoWCombatLog-old.txt", "WoWCombatLog-092126_190000.lua"):
        _add_log(root, name, b"\x1f\x8b\x08\x00" + b"x" * 40, 2_000_000_000)
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_") == 0
    moved = TimeShift(DEFAULT_OFFSET).file_name(LOG_NAME)
    assert [d for d in outputs(out) if "Combat" in d] == [f"macos/_retail_/Logs/{moved}"]


@pytest.mark.parametrize(
    ("body", "label"),
    [
        (b"\x1f\x8b\x08\x00\x00\x00\x00\x00", "compressed-file signature"),
        (b"PK\x03\x04" + HEADER, "compressed-file signature"),
        (b"BZh91AY&SY" + HEADER, "compressed-file signature"),
        (b"\xfd7zXZ\x00\x00" + HEADER, "compressed-file signature"),
        (HEADER + b"9/20/2026 21:14:06.000-4  X,\x00\n", "NUL byte"),
        (HEADER + b"9/20/2026 21:14:06.000-4  X,\xff\xfe\n", "not valid UTF-8"),
    ],
    ids=["gzip", "zip", "bz2", "xz", "nul", "not-utf8"],
)
def test_constructed_non_text_log_is_refused_unscrubbed(
    body: bytes, label: str, tmp_path: Path
) -> None:
    problems, data, outcome = _process(body, tmp_path, shift=TimeShift(OFFSET))
    assert any(label in p for p in problems)
    assert data == b"" and outcome.result.edits == ()


def test_constructed_cli_refuses_a_non_utf8_log(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    _add_log(root, OLDER, CLI_TS + b'SWING_DAMAGE,"Caf\xe9"\n', 2_000_000_000)  # Latin-1 byte
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_") == 1
    assert not [d for d in outputs(out) if "Combat" in d]


@pytest.mark.parametrize("name", ["WoWCombatLog-old.lua", "WoWCombatLog..txt", f"{LOG_NAME}.gz"])
def test_constructed_existing_file_with_a_bad_name_is_refused_by_the_name_check(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code review 4 and L3: the file exists under Logs/, so only
    `_check_combat_log_names` stands between it and the output."""
    root = tmp_path / "World of Warcraft"
    build_install(root)
    _add_log(root, name, CLI_TS + b"X,1\n", 2_000_000_000)
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--combat-log", name) == 2
    assert outputs(out) == {}
    # With the check disabled the same run writes that file: the check is what refuses it.
    monkeypatch.setattr(lab_capture, "_check_combat_log_names", lambda names: None)
    unguarded = tmp_path / "unguarded"
    capture(root, unguarded, "--flavor", "_retail_", "--combat-log", name)
    assert [d for d in outputs(unguarded) if d.endswith(name) or "Combat" in d]


def test_constructed_name_check_accepts_both_real_shapes() -> None:
    lab_capture._check_combat_log_names(["WoWCombatLog.txt", LOG_NAME])
    with pytest.raises(lab_capture.CaptureError):
        lab_capture._check_combat_log_names(["WoWCombatLog-92026_211403.txt"])


# ─── L4: broken or typeless GUID bodies ──────────────────────────────────────


@pytest.mark.parametrize(
    "token",
    [
        b"0-3771-0-58-2222-00004A2C11",  # no type
        b"Creature\xe2\x80\x910\xe2\x80\x913771\xe2\x80\x910\xe2\x80\x9158\xe2\x80\x912222"
        b"\xe2\x80\x9100004A2C11",  # U+2011 non-breaking hyphens
        b"Creature-0-3771-0\xe2\x88\x9258-2222-00004A2C11",  # one U+2212 minus
        b"Creature-0-3771-0-58-2222\xef\xbc\x8d00004A2C11",  # one U+FF0D full-width hyphen
        b"Crea\xe2\x80\x91ture-0-3771-0-58-2222-00004A2C11",  # a type glued to a dash lookalike
        b"xCreature\xc3\xa9-0-3771-0-58-2222-00004A2C11",  # a type broken by a letter
    ],
    ids=["typeless", "u2011", "u2212", "uff0d", "glued-type", "broken-type"],
)
def test_constructed_broken_guid_body_refuses(token: bytes, tmp_path: Path) -> None:
    log = HEADER + _line(b"SPELL_DAMAGE," + token + b',"x",0xa48,0x0')
    problems, _d, _o = _process(log, tmp_path)
    assert f"{UNCLASSIFIED} x1" in problems


def test_constructed_space_split_type_is_still_rewritten_not_kept(tmp_path: Path) -> None:
    """`Crea ture-0-...`: the letters after the blank read as the type; the
    location parts are invented all the same, so nothing real is kept."""
    log = HEADER + _line(b'SPELL_DAMAGE,Crea ture-0-3771-0-58-2222-00004A2C11,"x"')
    problems, data, _o = _process(log, tmp_path)
    assert problems == [] and b"Crea ture-0-1-0-2-2222-0000000000," in data


def test_constructed_well_formed_and_already_counted_guids_are_not_counted_again(
    tmp_path: Path,
) -> None:
    log = HEADER + _line(
        b"SPELL_DAMAGE," + BOAR + b',"Boar",0xa48,0x0,Creature-0-3771-0-58-2222-00004a2c11,"x"'
    )
    problems, _d, _o = _process(log, tmp_path)
    assert problems == [f"{UNCLASSIFIED} x1"]  # the lower-case one, once


# ─── L5 and code review 1: folded spellings ──────────────────────────────────


def _scrub(text: str, **extra: object) -> object:
    return _identity(**extra).scrub(('Notes = "' + text + '"\n').encode("utf-8"))


@pytest.mark.parametrize(
    "text",
    [
        "\uff2f\uff52\uff4c\uff41\uff56\uff49\uff4e waves",  # full-width character name
        "Orlàvin waves",  # an accent added
        "Orla\u00advin waves",  # soft hyphen inside
        "Orla\u2019vin waves",  # right single quotation mark inside
        "Kestrel\u2011Hollow",  # non-breaking hyphen in the realm
        "Kestrel \u2013 Hollow",  # en dash and blanks in the realm
        "Kestrel\u02bcHollow",  # modifier-letter apostrophe
        "Kestrel\u00a0\u00a0Hollow",  # doubled NBSP
        "\uff21\uff53\uff48 waves",  # a short name in full-width letters
        "Ásh waves",  # a short name with an accent
    ],
    ids=[
        "full-width",
        "accent",
        "soft-hyphen",
        "u2019",
        "u2011",
        "en-dash",
        "u02bc",
        "doubled-nbsp",
        "short-full-width",
        "short-accent",
    ],
)
def test_constructed_owner_name_in_a_folded_spelling_refuses(text: str) -> None:
    result = _scrub(text)
    assert f"{FOLDED} x1" in result.problems
    assert result.data == ('Notes = "' + text + '"\n').encode("utf-8")  # detect only


@pytest.mark.parametrize(
    "text",
    ["Ashen café waves", "The ash tree \u2013 café", "Orlavin waves"],
    ids=["short-inside-a-word", "unrelated-non-ascii", "ascii-rewritten"],
)
def test_constructed_owner_name_controls_pass(text: str) -> None:
    result = _scrub(text)
    assert not [p for p in result.problems if p.startswith(FOLDED)]


def test_constructed_ascii_rewrite_stays_byte_exact() -> None:
    result = _scrub("Orlavin of Kestrel Hollow")
    assert result.data == b'Notes = "Labcharb of Labrealma Partb"\n' and not result.problems


@pytest.mark.parametrize(
    "text",
    [
        b'"Quel\xe2\x80\x99Thalas"',  # U+2019
        b'"Quel\xe2\x80\x91Thalas"',  # U+2011
        b'"Qu\xc2\xadelThalas"',  # soft hyphen
        b'"Qu\xc3\xa9lThalas"',  # an accent
        b'"Qu\xc3\xa9l Thalas"',
    ],
    ids=["u2019", "u2011", "soft-hyphen", "accent", "accent-spaced"],
)
def test_constructed_loose_second_name_in_a_folded_spelling_refuses(text: bytes) -> None:
    identity = Identity(characters=["Kael"], realms=["QuelThalas"], loose=["QuelThalas"])
    result = identity.scrub(text)
    assert result.problems and result.data == text


def test_constructed_short_loose_name_with_a_lookalike_apostrophe_refuses() -> None:
    identity = Identity(characters=["Moon"], realms=["Qorv"], loose=["Qorv"])
    assert identity.scrub('"qo\u2019rv"'.encode()).problems
    assert not identity.scrub('"qorvings" "café"'.encode()).problems


def _one_player(unit_name: str, text: str) -> bytes:
    unit = b'Player-1-00C0FFEE,"' + unit_name.encode() + b'",0x512,0x0'
    return (
        HEADER
        + _line(b"SWING_DAMAGE," + unit + b"," + BOAR + b',"Boar",0xa48,0x0,1,-1')
        + _line(b"SPELL_DAMAGE," + OWN_UNIT + b"," + BOAR + b',"Boar",0xa48,0x0,585,"Smite"')
        + _line(b"EMOTE," + BOAR + b',"Boar",0000000000000000,nil,"' + text.encode() + b'"')
    )


@pytest.mark.parametrize(
    ("unit_name", "text"),
    [
        ("Zoëlinde-KestrelHollow-", "Zoelinde waves."),  # accent dropped in the text
        ("Zoelinde-KestrelHollow-", "Zoëlinde waves."),  # accent added in the text
        ("Zorvinth-KestrelHollow-", "Zor\u00advinth waves."),  # soft hyphen
        ("Zorvinth-KestrelHollow-", "Zor\u2019vinth waves."),
        ("Zorvinth-KestrelHollow-", "Zor\u02bcvinth waves."),
        ("Zorvinth-KestrelHollow-", "Zor\u2011vinth waves."),
        ("Zorvinth-KestrelHollow-", "Zor \u2013 vinth waves."),
        ("Zorvinth-KestrelHollow-", "Zor\u2018\u2019vinth waves."),
    ],
    ids=[
        "accent-dropped",
        "accent-added",
        "soft-hyphen",
        "u2019",
        "u02bc",
        "u2011",
        "en-dash",
        "doubled",
    ],
)
def test_constructed_other_player_name_in_a_folded_spelling_refuses(
    unit_name: str, text: str, tmp_path: Path
) -> None:
    problems, _d, _o = _process(_one_player(unit_name, text), tmp_path, others=OtherPlayers())
    assert problems == [f"{OTHER_NAME} x1"]


def test_constructed_suite_default_offset_is_inside_the_real_range() -> None:
    """conftest.DEFAULT_OFFSET must look like a real draw, or suites run unshifted."""
    low, high = lab_capture.SHIFT_MIN_SECONDS, lab_capture.SHIFT_MAX_SECONDS
    assert low <= abs(DEFAULT_OFFSET) <= high
    assert lab_capture.draw_time_shift() == DEFAULT_OFFSET  # what this test runs with
