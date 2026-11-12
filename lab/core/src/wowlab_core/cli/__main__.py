"""`python -m wowlab_core.cli`: the `wowlab` command, as `python -m wowlab_core`
runs it (M16-111). `cli` became a package, which Python runs only through
this file; `wowlab ui` starts its runs with `-m wowlab_core`."""

from wowlab_core.cli import app

if __name__ == "__main__":
    app(prog_name="wowlab")
