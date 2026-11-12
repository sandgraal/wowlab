# Probe from review of m16/114-flavor-picker; reproduces the UI adding the picked --flavor to `doctor --redact-name`, which the CLI refuses
"""`doctor --redact-name NAME` implies `--redact`, and `doctor` refuses
`--flavor` with either (cli.py, "--redact skips the first run, so --yes and
--flavor do nothing with it"). `withFlavor` in page.js skips only a run
whose `whole_install` or `redact` is `true` (`EVERY_FLAVOR`), so a Commands
tab run of `doctor` with only `--redact-name` filled gets the header's
flavor and is refused. The positive control: plain `doctor` does get it.

Uses the harness of `test_ui_flavor_picker.py` (the install there is the
committed capture plus one `constructed` flavor folder; nothing reads or
writes a real install).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_flavor_picker as picker
from typer.testing import CliRunner

from wowlab_core import cli

# The fixtures the picker's tests use, under the names they use.
home = picker.home
install = picker.install
server = picker.server
timed = picker.timed
two = picker.two
no_env_flavor = picker.no_env_flavor


def _doctor_bodies(srv: Any, tmp_path: Path, fields: dict[str, str]) -> list[dict[str, Any]]:
    steps = [{"command_run": "doctor", "fields": fields}]
    out = picker._drive(tmp_path / "node", picker._scenario(srv, steps, []))
    (step,) = out["steps"]
    assert "missing" not in step, step
    return [r["body"] for r in step["newRequests"] if r["body"] is not None]


def test_positive_control_plain_doctor_gets_the_picked_flavor(
    server: Any, two: Path, tmp_path: Path
) -> None:
    bodies = _doctor_bodies(server, tmp_path, {})
    assert bodies and bodies[0]["command"] == ["doctor"]
    assert bodies[0]["params"].get("flavor") == picker.FLAVOR


def test_redact_name_alone_gets_no_flavor(server: Any, two: Path, tmp_path: Path) -> None:
    bodies = _doctor_bodies(server, tmp_path, {"redact_name": "Guildname"})
    assert bodies and bodies[0]["command"] == ["doctor"]
    params = bodies[0]["params"]
    assert params.get("redact_name"), params
    # The CLI refuses this combination, so the page must not build it.
    result = CliRunner().invoke(
        cli.app, ["doctor", "--json", "--redact-name", "Guildname", "--flavor", picker.FLAVOR]
    )
    assert result.exit_code == 2 and "--flavor do nothing" in (result.stdout + result.stderr)
    assert "flavor" not in params, params
