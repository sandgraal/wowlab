#!/usr/bin/env python3
"""The smoke test's browser (ADR-0038 item 4, M17-08): writes the URL it is
given to the file `$WOWLAB_SMOKE_URL_FILE` names, and exits 0.

`scripts/app_smoke.py` sets `BROWSER` to this file's path. Python's
`webbrowser` runs a `BROWSER` program with the URL as its one argument and
treats exit 0 as opened, so `wowlab ui` serves as it would after opening a
real browser, and the smoke test reads the URL, key included, from the file.
The path is the whole `BROWSER` value, so this has to be an executable file
of its own (`webbrowser` takes no arguments from a `BROWSER` entry it does
not know). Standard library only, and runs on any Python 3.9 or later.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("record_url: expected one URL\n")
        return 2
    target = os.environ.get("WOWLAB_SMOKE_URL_FILE")
    if not target:
        sys.stderr.write("record_url: WOWLAB_SMOKE_URL_FILE is not set\n")
        return 2
    partial = Path(target + ".partial")
    partial.write_text(argv[0], encoding="utf-8")
    partial.replace(target)  # the reader never sees half a URL
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
