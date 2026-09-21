"""`wowlab --version` is the scaffold's only behaviour (M10-01)."""

import re

from typer.testing import CliRunner

from wowlab_core import __version__
from wowlab_core.cli import app

runner = CliRunner()

# Typer renders help through Rich, which forces colour when it sees
# GITHUB_ACTIONS, FORCE_COLOR or PY_COLORS, and then styles the two dashes of
# an option separately. Compare plain text, never styled text.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def test_version_flag_prints_the_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert _plain(result.output).strip() == f"wowlab {__version__}"


def test_version_is_a_real_version_string() -> None:
    assert __version__[0].isdigit(), __version__
    assert "unknown" not in __version__, "wowlab-core is not installed in this environment"


def test_no_arguments_shows_help_instead_of_doing_anything() -> None:
    result = runner.invoke(app, [])
    assert "--version" in _plain(result.output)
