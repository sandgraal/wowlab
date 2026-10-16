"""`python -m wowlab_core`: the `wowlab` command, as the console script runs it.

`wowlab ui` starts its runs this way, with the running interpreter by
absolute path and `-P`, so no name is looked up on PATH and nothing in the
working directory is imported (ADR-0029, owner rulings 2026-10-15)."""

from wowlab_core.cli import app

if __name__ == "__main__":
    app(prog_name="wowlab")
