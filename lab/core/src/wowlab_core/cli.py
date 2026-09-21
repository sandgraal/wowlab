"""The `wowlab` command. Commands arrive with M10-14 (docs/LAB_PLAN.md §6.11)."""

from typing import Annotated

import typer

from wowlab_core import __version__

app = typer.Typer(
    name="wowlab",
    help="Local-only toolchain over a World of Warcraft install.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"wowlab {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """Local-only toolchain over a World of Warcraft install."""
