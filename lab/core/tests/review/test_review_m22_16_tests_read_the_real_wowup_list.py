# Probe from review of m22/16-addon-provenance; reproduces tests that run `addons list` without moving the home folder, so they read the machine's real WowUp addons.json
"""M22-16 makes `wowlab addons list` (and every `explain` view that runs it)
read WowUp's `addons.json` from the user's home: on macOS
`~/Library/Application Support/WowUp/`, elsewhere `platformdirs`' roaming
config folder. Seven tests already on `main` run that command with `HOME`
left as the machine's, so on the owner's Mac they read the owner's real
WowUp list, and their output depends on the machine they run on
(AGENTS.md: no test needs or reads a real install or machine file; L8).
Found by an audit hook on the review run: each id below opened
`/Users/<login>/Library/Application Support/WowUp/addons.json`.

The probe runs those tests in a child pytest whose `HOME` (and
`XDG_CONFIG_HOME`) point at a stand-in that holds a sentinel `addons.json`,
with an audit hook that records every open of the sentinel. A test that
isolates itself opens a WowUp folder under its own `tmp_path`, never the
sentinel. The positive control proves the hook sees a read of the sentinel.

Constructed (a stand-in home and a sentinel list); POSIX only, since on
Windows `platformdirs` ignores the folder variables.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wowup" / "macos" / "addons.json"

UNISOLATED = [
    "lab/core/tests/test_cli.py::test_addons_list",
    "lab/core/tests/test_cli.py::test_read_commands_leave_the_install_untouched",
    "lab/core/tests/test_cli_explain_detail.py::test_each_view_decodes_the_captured_file",
    "lab/core/tests/test_cli_player_copy.py::test_read_output_is_player_copy",
    "lab/core/tests/test_filemap_addon_executables.py::test_views_for_paths_the_row_takes_constructed",
]

HOOK = """
import os, sys
_SENTINEL = os.environ["M22_16_SENTINEL"]
_LOG = os.environ["M22_16_LOG"]
def _hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.path.realpath(os.fsdecode(args[0])) == _SENTINEL:
            with open(_LOG, "a", encoding="utf-8") as f:
                f.write(os.environ.get("PYTEST_CURRENT_TEST", "-") + "\\n")
sys.addaudithook(_hook)
"""

pytestmark = pytest.mark.skipif(os.name == "nt", reason="platformdirs ignores HOME on Windows")


def _stand_in(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"
    home.mkdir()
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(HOOK, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("WOWLAB_WOW_ROOT", "WOWLAB_FLAVOR")}
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    folder = subprocess.run(
        [
            sys.executable,
            "-c",
            "from wowlab_core import addonsource; print(addonsource.wowup_folder())",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    sentinel = Path(folder) / "addons.json"
    assert home in sentinel.parents, sentinel
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(FIXTURE.read_bytes())
    log = tmp_path / "opens.log"
    log.write_text("", encoding="utf-8")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(site), env.get("PYTHONPATH", "")]))
    env["M22_16_SENTINEL"] = os.path.realpath(sentinel)
    env["M22_16_LOG"] = str(log)
    return env, log


def test_the_hook_sees_a_read_of_the_home_list_constructed(tmp_path: Path) -> None:
    env, log = _stand_in(tmp_path)
    subprocess.run(
        [sys.executable, "-c", "from wowlab_core import addonsource; addonsource.read_wowup()"],
        env=env,
        check=True,
    )
    assert log.read_text(encoding="utf-8").strip() != ""


def test_no_test_reads_the_home_wowup_list_constructed(tmp_path: Path) -> None:
    env, log = _stand_in(tmp_path)
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "--basetemp",
            str(tmp_path / "inner"),
            *UNISOLATED,
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=280,
    )
    assert ran.returncode == 0, ran.stdout[-3000:] + ran.stderr[-3000:]
    readers = sorted({line.split(" (")[0] for line in log.read_text(encoding="utf-8").splitlines()})
    assert readers == [], "these tests read the home folder's WowUp list: " + "; ".join(readers)
