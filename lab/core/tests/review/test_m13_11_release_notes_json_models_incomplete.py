# Probe from review of m13/11-release-v0-1-0; reproduces the v0.1 release notes' `--json` model list omitting models the CLI's help names (snapshot.Manifest, LooksListReport, LogTailLine).
"""The release notes' `--json` paragraph names every model a command's help names.

`.github/release-notes.md` (M13-11) says every read command's `--json` form
is backed by a model named in its `--help`, and lists them. ADR-0031 makes
`--json` an interface other people script against, and the "Changes to
`--json` output" section of later releases is measured against this list,
so a model the CLI documents but the list leaves out is a gap in the v0.1
baseline. The rule for "a model named in help" is the command reference's
(`scripts/gen_command_reference.py`, `json_models`); a dotted name
(`snapshot.Manifest`) counts as listed when its last part is listed.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[4]
NOTES = REPO / ".github" / "release-notes.md"
SCRIPT = REPO / "scripts" / "gen_command_reference.py"


def _load() -> ModuleType:
    name = "gen_command_reference_m13_11_probe"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _listed() -> set[str]:
    text = NOTES.read_text(encoding="utf-8")
    paragraph = text.split("**`--json`.**", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"`([A-Za-z_.]+)`", paragraph))


def _named_by_help() -> dict[str, list[str]]:
    gen = _load()
    return {name: gen.json_models(cmd) for name, cmd in gen.iter_leaves(gen.root_command())}


def test_positive_control_the_list_is_read_and_doctor_is_on_it() -> None:
    listed = _listed()
    assert len(listed) >= 30
    assert "DoctorReport" in _named_by_help()["wowlab doctor"]
    assert "DoctorReport" in listed


def test_every_model_a_command_help_names_is_in_the_release_notes() -> None:
    listed = _listed()
    missing = sorted(
        f"{command}: {model}"
        for command, models in _named_by_help().items()
        for model in models
        if model.rsplit(".", 1)[-1] not in listed
    )
    assert missing == []
