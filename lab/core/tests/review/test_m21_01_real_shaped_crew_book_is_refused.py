# Probe from review of m21/01-name-limits-lifted; reproduces a crew book of the shape the addon writes (each player's `guid`, Crew.lua:926) refused by the capture tool.
"""M21-01's acceptance: "the capture tool captures a constructed crew book
unchanged". The branch's constructed book has no `guid` field, but the addon
writes one for every player it records (`lab/addon/WowLab/Crew.lua:926`,
`entry.guid = ns.String(guid)`), and the crew book lives in the account-wide
`WowLab.lua` beside `WowLabDB`, not beside `WowLabCharDB` (the TOC's
`SavedVariables: WowLabDB, WowLabCrewDB`), so `labaddon_other_guids` never
maps another player's GUID there and the file is refused as "unmapped player
GUID". A real crew book can therefore still not be captured.

CONSTRUCTED INPUT, in `tmp_path` only; every key and GUID is invented.
Positive control: the same book without the `guid` line is captured.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]


def _helpers() -> ModuleType:
    sys.path.insert(0, str(REPO / "tests" / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "m21_01_probe_capture", REPO / "tests" / "scripts" / "test_lab_capture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _book(with_guid: bool) -> bytes:
    lines = [
        "WowLabCrewDB = {",
        '["schema"] = 2,',
        '["players"] = {',
        '["Inventeda-Fakerealm"] = {',
        '["class"] = "MAGE",',
        *(['["guid"] = "Player-77-0BADBEEF",'] if with_guid else []),
        "},",
        "},",
        "}",
    ]
    return ("\r\n" + "\r\n".join(lines) + "\r\n").encode()


@pytest.mark.parametrize("with_guid", [False, True], ids=["control: no guid", "addon shape"])
def test_a_crew_book_as_the_addon_writes_it_is_captured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], with_guid: bool
) -> None:
    h = _helpers()
    root = tmp_path / "World of Warcraft"
    h.build_install(root)
    saved = root / "_retail_" / "WTF" / "Account" / h.ACCOUNT / "SavedVariables"
    (saved / "WowLab.lua").write_bytes(_book(with_guid))
    out = tmp_path / "incoming"
    code = h.capture(root, out, "--sv", "WowLab.lua")
    printed = capsys.readouterr()
    captured = [p for p in h.outputs(out) if p.endswith("/SavedVariables/WowLab.lua")]
    assert code == 0 and captured, printed.out + printed.err
