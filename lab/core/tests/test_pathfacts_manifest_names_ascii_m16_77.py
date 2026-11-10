"""`explain`'s snapshot facts read an id from ASCII digits only (M16-77).

`pathfacts._newer` decides whether a manifest file that does not load may
be newer than the latest readable snapshot: it is when its name is a later
snapshot id, or when its name is no id at all (`docs/LAB_PLAN.md` §6.9;
M16-13's domain review). When it may be, `explain` withholds its
`snap restore` hint (`damaged_newer`), since the copy it would offer may not
be the newest. `pathfacts._MANIFEST_NAME` matched the digits as Unicode
digits, so a name with ASCII digits up front and non-ASCII digits after
read as an id, compared as an older one, and the hint was offered anyway.
The rule: a name with any non-ASCII digit carries no id.

Every name is constructed (hostile-input, L8): its leading ASCII digits
spell 2020, before any snapshot a test takes, and the rest are in the
digit set under test. The end-to-end test runs `explain` on the captured
tree copied into `tmp_path` by `test_cli_explain_detail.py`'s fixtures,
with the user data directory redirected and no client in the process
table; nothing reads or writes a real install. Each `ascii-control` test
shows the same name in ASCII digits is an older id, so each other test
fails for the right reason without the fix.
"""

# ruff: noqa: F811  (fixtures imported from test_cli_explain_detail are requested by name)
from __future__ import annotations

from pathlib import Path

import pytest
from test_cli_explain_detail import (
    SYNDICATOR,
    _restore_hints,
    explained,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import pathfacts, snapshot

NON_ASCII_ZEROS = [
    pytest.param(0x0660, id="constructed-arabic-indic"),
    pytest.param(0x06F0, id="constructed-extended-arabic-indic"),
    pytest.param(0x0966, id="constructed-devanagari"),
    pytest.param(0xFF10, id="constructed-fullwidth"),
    pytest.param(None, id="constructed-one-arabic-indic-digit-among-ascii"),
]
OLDER_ID = "20200101T000000.000000Z-00000000"
LATEST_ID = "20261110T120000.000000Z-abcdef12"


def _older_name(zero: int | None) -> str:
    """`OLDER_ID`'s file name, its digits after the year in the set whose
    zero is `zero`; None spells only its last digit in Arabic-Indic."""
    moment, rest = OLDER_ID[:23], OLDER_ID[23:]
    if zero is None:
        spelled = moment[:-2] + chr(0x0660) + "Z"
    else:
        spelled = moment[:4] + "".join(chr(zero + int(c)) if c.isdigit() else c for c in moment[4:])
    return f"{spelled}{rest}.json"


def test_an_older_ascii_name_is_an_older_id_ascii_control() -> None:
    assert pathfacts._MANIFEST_NAME.match(f"{OLDER_ID}.json") is not None
    assert pathfacts._newer(f"{OLDER_ID}.json", LATEST_ID) is False


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_non_ascii_digit_name_carries_no_id(zero: int | None) -> None:
    name = _older_name(zero)
    assert not name.isascii()
    assert pathfacts._MANIFEST_NAME.match(name) is None
    assert pathfacts._newer(name, LATEST_ID) is True


def test_an_older_damaged_manifest_leaves_the_restore_hint_ascii_control(
    root: Path, flavor: Path
) -> None:
    ok("snap", "create")
    manifests = snapshot.SnapshotStore().manifests_dir
    [good] = list(manifests.iterdir())
    (manifests / f"{OLDER_ID}.json").write_bytes(good.read_bytes()[:40])
    (flavor / SYNDICATOR).write_bytes((flavor / SYNDICATOR).read_bytes() + b"\n")
    report = explained(SYNDICATOR)
    assert report.snapshot is not None
    assert (report.snapshot.damaged, report.snapshot.damaged_newer) == (1, False)
    assert _restore_hints(report)


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_non_ascii_digit_damaged_manifest_withholds_the_restore_hint(
    root: Path, flavor: Path, zero: int | None
) -> None:
    """A damaged manifest whose name is no id may be newer: `explain`
    offers no `snap restore` from the latest readable snapshot."""
    ok("snap", "create")
    manifests = snapshot.SnapshotStore().manifests_dir
    [good] = list(manifests.iterdir())
    (manifests / _older_name(zero)).write_bytes(good.read_bytes()[:40])
    (flavor / SYNDICATOR).write_bytes((flavor / SYNDICATOR).read_bytes() + b"\n")
    report = explained(SYNDICATOR)
    assert report.snapshot is not None
    assert report.snapshot.live == "differs"
    assert (report.snapshot.damaged, report.snapshot.damaged_newer) == (1, True)
    assert not _restore_hints(report)
