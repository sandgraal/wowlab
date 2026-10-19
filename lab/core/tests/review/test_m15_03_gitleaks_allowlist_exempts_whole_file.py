# Probe from review of m15/03-casc-index; reproduces the `.gitleaks.toml` CASC allowlist exempting the whole derived fixture, not three lines
"""`.gitleaks.toml` says its new `[[allowlists]]` entry covers "only these
three field names, only a whole line holding 32 lowercase hex digits, only in
a derived fixture's `casc-index.json`", and the file's own rule is "a narrow
per-line regex ..., never a per-file exemption". gitleaks (8.28.0, and 8.30.1
as CI pins it) skips a file whose path matches a global allowlist's `paths`
before any line is read, `condition = "AND"` notwithstanding: the scan
reports `scanned ~0 bytes` and a credential-shaped line added to that
fixture is never reported.

The probe copies the repository's config and the fixture into `tmp_path`
at the same relative path, adds one line no allowlist regex matches, and
scans the copy with `gitleaks dir` from `tmp_path` (so paths are relative,
as `gitleaks git` gives them in CI). Positive control: the same tree under
the default rules alone reports that line. Needs a `gitleaks` binary; skipped
where there is none (the CI test jobs).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
REL = Path("lab/core/tests/fixtures/macos-70450/derived/casc-index.json")
# Built at run time so this source line is not itself a finding.
PLANTED = "".join(['  "secret', '_key": "', "9a0263e0", "6fdace8ab940a4a45a8e28fb", '",'])
GITLEAKS = shutil.which("gitleaks")

pytestmark = pytest.mark.skipif(GITLEAKS is None, reason="no gitleaks binary on PATH")


def _scan(tree: Path, config: Path) -> list[dict[str, object]]:
    report = tree / "report.json"
    subprocess.run(
        [
            str(GITLEAKS),
            "dir",
            "--no-banner",
            "--exit-code",
            "0",
            "-c",
            str(config),
            "-f",
            "json",
            "-r",
            str(report),
            "lab",
        ],
        cwd=tree,
        check=True,
        capture_output=True,
    )
    found: list[dict[str, object]] = json.loads(report.read_text(encoding="utf-8"))
    return found


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "repo"
    target = tree / REL
    target.parent.mkdir(parents=True)
    text = (REPO / REL).read_text(encoding="utf-8")
    target.write_text(text + PLANTED + "\n", encoding="utf-8")
    return tree


def _planted_line() -> int:
    return len((REPO / REL).read_text(encoding="utf-8").splitlines()) + 1


def test_positive_control_default_rules_report_the_planted_line(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    config = tmp_path / "default.toml"
    config.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
    lines = {f["StartLine"] for f in _scan(tree, config)}
    assert _planted_line() in lines


def test_the_repository_config_still_reports_a_line_outside_its_allowlist(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path)
    lines = {f["StartLine"] for f in _scan(tree, REPO / ".gitleaks.toml")}
    assert _planted_line() in lines, (
        "the CASC allowlist exempts the whole fixture: a line that is not one of "
        "its three hex fields is not scanned"
    )
