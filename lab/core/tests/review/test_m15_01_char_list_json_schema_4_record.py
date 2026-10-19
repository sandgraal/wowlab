# Probe from review of m15/01-addon-identity; reproduces `char list --json` on a
# schema-4 record raising pydantic serializer warnings and printing a report
# that no longer validates, because `labaddon.CharacterFile.record` still
# names schema 1 to 3 only.
"""`CharacterFile.record` (labaddon.py) is typed `CharDBV1 | CharDBV2 |
CharDBV3 | None`. M15-01 added `CharDBV4` to `CharDB`, `CHAR_SCHEMAS` and
`CharShowReport.record`, but not here, and `read_all` puts a schema-4 record
there. Dumping `CharListReport` then falls outside the union: pydantic
emits `PydanticSerializationUnexpectedValue` UserWarnings (a multi-line
`UserWarning` on stderr when `wowlab char list --json` runs from a shell),
and the printed JSON is refused by `CharListReport.model_validate`.

The schema-4 file is constructed (labelled): the real M13-05 record
(`macos-70425/`, schema 3, index row in `fixtures/README.md`) with its
`schema` line set to 4 and an `identity` table in the shape
`lab/addon/WowLab/Identity.lua` writes. The schema-3 case is the positive
control: the same real file, unchanged, warns nothing and re-validates.
Nothing here reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
import warnings
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
LAB_FILE = "WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
IDENTITY = (
    b'["identity"] = {\r\n["race_id"] = 5,\r\n["sex_raw"] = 3,\r\n["class_id"] = 9,\r\n'
    b'["class_file"] = "WARLOCK",\r\n["events_unregistered"] = {\r\n},\r\n},\r\n'
)


@pytest.mark.parametrize("schema_4", [False, True], ids=["real-schema-3", "constructed-schema-4"])
def test_char_list_json_is_quiet_and_revalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema_4: bool
) -> None:
    monkeypatch.setattr(
        platformdirs, "user_data_path", lambda *a, **k: tmp_path / "userdata" / "wowlab"
    )
    monkeypatch.setattr(
        platformdirs, "user_config_path", lambda *a, **k: tmp_path / "userconfig" / "wowlab"
    )
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    flavor = root / FLAVOR
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70425" / "forever", flavor)
    shutil.copy2(FIXTURES / "macos" / "forever" / ".flavor.info", flavor / ".flavor.info")
    if schema_4:
        path = flavor / LAB_FILE
        data = path.read_bytes()
        assert data.count(b'["schema"] = 3,\r\n') == 1
        path.write_bytes(data.replace(b'["schema"] = 3,\r\n', b'["schema"] = 4,\r\n' + IDENTITY))
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = CliRunner().invoke(cli.app, ["char", "list", "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    messages = [str(w.message) for w in caught]
    assert not [m for m in messages if "PydanticSerializationUnexpectedValue" in m], messages[:1]

    report = cli.CharListReport.model_validate(json.loads(result.stdout))
    record = report.characters[0].record
    assert record is not None and record.schema_ == (4 if schema_4 else 3)
