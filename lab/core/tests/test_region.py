"""The region and the character's names (M21-02): `region` and `charname`.

Real inputs: the `Config.wtf` of each flavor fixture that has one,
`fixtures/macos-70888/` (the Forever launch client, `SET portal "US"`) and
`fixtures/macos/` (the Forever beta, `SET portal "test"`), read in place
from the repository through the WTF reader; and the character folder names
of the captures. Every other input is `constructed` (L8, hostile or
boundary values) and its test id says so. Nothing reads or writes a real
install; the constructed files live in `tmp_path`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wowlab_core import charname, region, wtfconfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAUNCH = FIXTURES / "macos-70888"
BETA = FIXTURES / "macos"


def _state(root: Path) -> dict[str, tuple[int, int]]:
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
    }


# ─── real fixtures ───────────────────────────────────────────────────────────


def test_the_launch_client_names_its_region_in_upper_case() -> None:
    before = _state(LAUNCH / "forever" / "WTF")
    reading = region.read(LAUNCH / "forever", root=LAUNCH)
    assert reading == region.RegionReading(region="us", absent=None)
    # The raw line is as the client wrote it; only the reading folds the case.
    doc = wtfconfig.read_config(LAUNCH / "forever" / "WTF" / "Config.wtf")
    cvar = doc.get("portal")
    assert cvar is not None and cvar.value == "US"
    assert _state(LAUNCH / "forever" / "WTF") == before  # read-only (L1)


def test_the_beta_portal_test_is_no_region() -> None:
    reading = region.read(BETA / "forever", root=BETA)
    assert reading.region is None
    assert reading.absent == (
        'the portal setting in WTF/Config.wtf is "test", not one of us, eu, kr, tw, cn'
    )


def test_a_flavor_fixture_without_config_wtf_has_no_region() -> None:
    reading = region.read(FIXTURES / "macos-70450" / "forever", root=FIXTURES / "macos-70450")
    assert reading == region.RegionReading(region=None, absent="there is no WTF/Config.wtf")


def test_every_flavor_fixture_config_reads_to_a_region_or_a_reason() -> None:
    found = sorted(FIXTURES.glob("*/forever/WTF/Config.wtf"))
    assert len(found) >= 2
    for path in found:
        capture = path.parents[2]
        reading = region.read(path.parents[1], root=capture.parent)
        assert (reading.region is None) != (reading.absent is None), path


def test_the_capture_folder_names_split_into_first_and_second() -> None:
    folder = LAUNCH / "forever" / "WTF" / "Account" / "LABACCOUNTA" / "1"
    (name,) = [p.name for p in folder.iterdir() if p.is_dir()]
    first, second = charname.split_folder(name, numeric=charname.is_numeric_folder("1"))
    assert (first, second) == ("Sandgraal", "Rizzen")
    assert charname.game_name(first, second) == "Sandgraal Rizzen"


# ─── region_of and the reading (constructed values) ──────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("us", "us"),
        ("US", "us"),
        ("eu", "eu"),
        ("EU", "eu"),
        ("kr", "kr"),
        ("Kr", "kr"),
        ("tw", "tw"),
        ("tW", "tw"),
        ("test", None),
        ("cn", "cn"),
        ("CN", "cn"),
        ("", None),
        (" us", None),
        ("us ", None),
        ("u s", None),
        ("us\x00", None),
        ("u\x02s", None),
        ("us" + chr(0x202E), None),  # a right-to-left override
        (chr(0x130) + "s", None),  # a capital I with a dot: not ASCII, not folded
        (chr(0xFF55) + chr(0xFF53), None),  # fullwidth letters
        ("usa", None),
        ("us-west", None),
        ("u" * 300, None),
        (None, None),
    ],
    ids=lambda v: repr(v)[:24],
)
def test_region_of_constructed(value: str | None, expected: str | None) -> None:
    assert region.region_of(value) == expected


def _config(tmp_path: Path, data: bytes) -> Path:
    flavor = tmp_path / "_flavor_"
    (flavor / "WTF").mkdir(parents=True)
    (flavor / "WTF" / "Config.wtf").write_bytes(data)
    return flavor


@pytest.mark.parametrize(
    ("data", "expected", "said"),
    [
        (b'SET portal "eu"\n', "eu", None),
        (b'SET PORTAL "KR"\r\n', "kr", None),  # the name compared without case
        (b'SET portal "us"\nSET portal "tw"\n', "tw", None),  # the last line wins
        (b'SET portal "tw"\nSET portal "test"\n', None, '"test"'),
        (b'SET portal ""\n', None, "is empty"),
        (b'SET portal "cn"\n', "cn", None),
        (b'SET portal "jp"\n', None, '"jp", not one of us, eu, kr, tw, cn'),
        (b'SET portal " us"\n', None, "is not one of"),
        (b'SET portal "\xff\xfe"\n', None, "is not one of"),
        (b'SET portal "us\x1b[31m"\n', None, "is not one of"),
        (b'SET portal "' + b"x" * 300 + b'"\n', None, "is not one of"),
        (b'set portal "us"\n', None, "has no portal setting"),  # not the client's grammar
        (b"SET portal us\n", None, "has no portal setting"),
        (b'SET farclip "8000"\n', None, "has no portal setting"),
        (b"", None, "has no portal setting"),
    ],
    ids=lambda v: repr(v)[:32] if isinstance(v, bytes) else None,
)
def test_read_a_constructed_config(
    tmp_path: Path, data: bytes, expected: str | None, said: str | None
) -> None:
    flavor = _config(tmp_path, data)
    before = _state(tmp_path)
    reading = region.read(flavor, root=tmp_path)
    assert reading.region == expected
    if said is None:
        assert reading.absent is None
    else:
        assert reading.absent is not None and said in reading.absent
        # Nothing but a short plain value is quoted back.
        assert "\x1b" not in reading.absent and "xxxxxxxxxxxxxxxxx" not in reading.absent
    assert _state(tmp_path) == before  # read-only (L1)


def test_a_config_that_is_a_folder_is_not_read_constructed(tmp_path: Path) -> None:
    flavor = tmp_path / "_flavor_"
    (flavor / "WTF" / "Config.wtf").mkdir(parents=True)
    reading = region.read(flavor, root=tmp_path)
    assert reading.region is None
    assert reading.absent is not None and reading.absent.startswith("WTF/Config.wtf could not")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_linked_config_is_refused_constructed(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.wtf"
    elsewhere.write_bytes(b'SET portal "eu"\n')
    flavor = tmp_path / "_flavor_"
    (flavor / "WTF").mkdir(parents=True)
    try:
        (flavor / "WTF" / "Config.wtf").symlink_to(elsewhere)
    except OSError:
        pytest.skip("symlinks not permitted here")
    reading = region.read(flavor, root=tmp_path)
    assert reading.region is None and reading.absent is not None


def test_an_oversized_config_is_not_read_constructed(tmp_path: Path) -> None:
    big = b'SET portal "eu"\n' + b"#" * (wtfconfig.CONFIG_BUDGET.max_bytes + 1)
    reading = region.read(_config(tmp_path, big), root=tmp_path)
    assert reading == region.RegionReading(
        region=None, absent="WTF/Config.wtf is larger than wowlab reads, so it was not read"
    )


def test_a_reading_holds_exactly_one_of_region_and_reason_constructed() -> None:
    with pytest.raises(ValueError):
        region.RegionReading(region="us", absent="also a reason")
    with pytest.raises(ValueError):
        region.RegionReading(region=None, absent=None)


# ─── charname (constructed names) ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("folder", "numeric", "expected"),
    [
        ("Labchard-Labrealmg", True, ("Labchard", "Labrealmg")),
        ("Labchard-Labrealmg", False, ("Labchard-Labrealmg", None)),  # retail shape
        ("Labchard", True, ("Labchard", None)),
        ("A-b-c", True, ("A", "b-c")),  # split at the first hyphen, as layout does
        ("Ünïcödé-Námé", True, ("Ünïcödé", "Námé")),
        ("Rtl" + chr(0x202E) + "-x", True, ("Rtl" + chr(0x202E), "x")),
        ("x" * 300 + "-y", True, ("x" * 300, "y")),
    ],
    ids=lambda v: repr(v)[:24],
)
def test_split_folder_constructed(
    folder: str, numeric: bool, expected: tuple[str, str | None]
) -> None:
    assert charname.split_folder(folder, numeric=numeric) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Inventeda", ("Inventeda", None)),
        ("Inventeda Second", ("Inventeda", "Second")),
        ("Inventeda Second Third", ("Inventeda", "Second Third")),
        (" Inventeda", (" Inventeda", None)),
        ("Inventeda ", ("Inventeda ", None)),
        ("", ("", None)),
    ],
    ids=lambda v: repr(v)[:24],
)
def test_split_game_name_constructed(name: str, expected: tuple[str, str | None]) -> None:
    assert charname.split_game_name(name) == expected


def test_numeric_folder_is_ascii_digits_only_constructed() -> None:
    assert charname.is_numeric_folder("1") and charname.is_numeric_folder("0042")
    assert not charname.is_numeric_folder("Realm")
    assert not charname.is_numeric_folder("١٢")  # Arabic-Indic digits
    assert not charname.is_numeric_folder("")
    assert charname.game_name("A", None) == "A" and charname.game_name("A", "B") == "A B"


@pytest.mark.parametrize(
    ("name", "other", "second_names", "expected"),
    [
        # Forever: the client reports the second name where retail reports the realm.
        ("Tay", "Soulrend", True, ("Tay", "Soulrend", "Tay Soulrend", None)),
        ("Tay", "", True, ("Tay", None, "Tay", None)),
        # Retail: Name-Realm as it is.
        ("Tay", "Soulrend", False, ("Tay", None, "Tay", "Soulrend")),
        # A name part that already holds a space is split there, on either.
        ("Tay Soulrend", "Fakerealm", True, ("Tay", "Soulrend", "Tay Soulrend", "Fakerealm")),
        ("Tay Soulrend", "Fakerealm", False, ("Tay", "Soulrend", "Tay Soulrend", "Fakerealm")),
    ],
    ids=["forever", "forever-no-second", "retail", "spaced-forever", "spaced-retail"],
)
def test_key_names_constructed(
    name: str,
    other: str,
    second_names: bool,
    expected: tuple[str, str | None, str, str | None],
) -> None:
    assert tuple(charname.key_names(name, other, second_names=second_names)) == expected
