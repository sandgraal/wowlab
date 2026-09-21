"""`wowlab --version` is the scaffold's only behaviour (M10-01)."""

from typer.testing import CliRunner

from wowlab_core import __version__
from wowlab_core.cli import app

runner = CliRunner()


def test_version_flag_prints_the_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"wowlab {__version__}"


def test_version_is_a_real_version_string() -> None:
    assert __version__[0].isdigit(), __version__
    assert "unknown" not in __version__, "wowlab-core is not installed in this environment"


def test_no_arguments_shows_help_instead_of_doing_anything() -> None:
    result = runner.invoke(app, [])
    assert "--version" in result.output
