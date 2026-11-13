"""A stand-in for the GitHub CLI's `gh release` subcommands, for the tests
of release.yml's `app_release` job (M17-08). Constructed: it keeps one
release's assets as files in `$FAKE_GH_ASSETS` and appends each call to
`$FAKE_GH_LOG`; it makes no network request.

    gh release view TAG --repo R --json assets --jq '.assets[].name'
    gh release download TAG --repo R --pattern GLOB --dir DIR
    gh release upload TAG --repo R [--clobber] FILE...

`$FAKE_GH_CORRUPT_UPLOAD`, when set, names an asset whose upload stores
other bytes than were sent, so the job's read-back has something to catch.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import sys
from pathlib import Path


def _options(args: list[str]) -> tuple[list[str], dict[str, str], set[str]]:
    positional: list[str] = []
    values: dict[str, str] = {}
    flags: set[str] = set()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--clobber":
            flags.add(arg)
        elif arg.startswith("--"):
            values[arg] = args[i + 1]
            i += 1
        else:
            positional.append(arg)
        i += 1
    return positional, values, flags


def main(argv: list[str]) -> int:
    assets = Path(os.environ["FAKE_GH_ASSETS"])
    with Path(os.environ["FAKE_GH_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps(argv) + "\n")
    if argv[:1] != ["release"] or len(argv) < 3:
        sys.stderr.write(f"fake gh: unsupported: {argv}\n")
        return 2
    command = argv[1]  # argv[2] is the tag
    files, values, flags = _options(argv[3:])
    if command == "view":
        assert values.get("--json") == "assets" and values.get("--jq") == ".assets[].name"
        for name in sorted(p.name for p in assets.iterdir()):
            sys.stdout.write(name + "\n")
        return 0
    if command == "download":
        matched = [
            p for p in sorted(assets.iterdir()) if fnmatch.fnmatch(p.name, values["--pattern"])
        ]
        if not matched:
            sys.stderr.write("no assets match the file pattern\n")
            return 1
        target = Path(values["--dir"])
        target.mkdir(parents=True, exist_ok=True)
        for path in matched:
            if (target / path.name).exists():
                sys.stderr.write(f"{path.name} already exists\n")
                return 1
            shutil.copyfile(path, target / path.name)
        return 0
    if command == "upload":
        for file in files:
            source = Path(file)
            stored = assets / source.name
            if stored.exists() and "--clobber" not in flags:
                sys.stderr.write(f"asset under the same name already exists: {source.name}\n")
                return 1
            data = source.read_bytes()
            if os.environ.get("FAKE_GH_CORRUPT_UPLOAD") == source.name:
                data += b"tampered\n"
            stored.write_bytes(data)
        return 0
    sys.stderr.write(f"fake gh: unsupported: {argv}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
