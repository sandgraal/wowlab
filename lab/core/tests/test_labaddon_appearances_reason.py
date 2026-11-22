"""M16-64: a file an older lab-addon wrote holds the appearances reason with
a ticket id, "(M11-03)". The reader keeps it as the file holds it (the
model and `--json`); text output says it in the addon's current words, so
the id does not reach a player. Any other reason prints as read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import labaddon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACCOUNT = "WTF/Account/90000001#6/1"
OLDER_FILES = [
    f"macos/forever/{ACCOUNT}/Labchard-Labrealmg/SavedVariables/WowLab.lua",  # schema 1
    f"macos-70425/forever/{ACCOUNT}/Labchard-Labrealme/SavedVariables/WowLab.lua",  # 3
    "macos-70888/forever/WTF/Account/LABACCOUNTA/1/Sandgraal-Rizzen/SavedVariables/WowLab.lua",
]
ADDON_SOURCE = Path(__file__).resolve().parents[2] / "addon" / "WowLab" / "Collections.lua"


@pytest.mark.parametrize("name", OLDER_FILES, ids=["schema1", "schema3", "schema4"])
def test_an_older_files_reason_prints_without_the_ticket_id(name: str) -> None:
    path = FIXTURES / name
    assert labaddon.APPEARANCES_REASON_WITH_TICKET.encode() in path.read_bytes()
    record = labaddon.read_char(path)
    assert record.collections is not None and record.collections.appearances is not None
    # The model, and so --json, keeps the file's text.
    assert record.collections.appearances.absent == labaddon.APPEARANCES_REASON_WITH_TICKET
    assert "M11-03" in record.model_dump_json(by_alias=True)
    text = "\n".join(labaddon.describe(record))
    assert f"Appearances: absent ({labaddon.APPEARANCES_REASON})" in text
    assert "M11-03" not in text


def test_the_current_words_are_the_addons_own() -> None:
    """The desk's current wording is the string the addon now writes."""
    source = ADDON_SOURCE.read_text(encoding="utf-8")
    assert f'not_gathered = "{labaddon.APPEARANCES_REASON}"' in source
    assert labaddon.APPEARANCES_REASON_WITH_TICKET.replace(" (M11-03)", "") == (
        labaddon.APPEARANCES_REASON
    )


@pytest.mark.parametrize(
    "reason",
    [
        "not gathered: crashed once (M11-04); the addon no longer asks",
        labaddon.APPEARANCES_REASON_WITH_TICKET + " ",
    ],
    ids=["another-reason-constructed", "not-exactly-the-old-text-constructed"],
)
def test_any_other_reason_prints_as_read_constructed(reason: str) -> None:
    """Constructed (boundary input, L8): only the addon's exact old text is
    reworded; a reason the file holds otherwise is printed as read."""
    record = labaddon.read_char(FIXTURES / OLDER_FILES[1])
    assert record.collections is not None and record.collections.appearances is not None
    appearances = record.collections.appearances.model_copy(update={"absent": reason})
    collections = record.collections.model_copy(update={"appearances": appearances})
    text = "\n".join(labaddon.describe(record.model_copy(update={"collections": collections})))
    assert f"Appearances: absent ({reason})" in text
