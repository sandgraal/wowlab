"""Fetch the pinned selene binary that lints the lab-addon sources (ADR-0026, M11-01).

selene is a static Lua linter shipped as one binary; it parses Lua and never
runs it. This script is the only place the version and checksums live: the
Make target `lint-lua` and the CI step both call it. It downloads the
`selene-light` build (no Roblox standard library, so selene itself never
needs the network) from the project's GitHub release, checks the archive's
SHA-256 against the pin below, and unpacks the binary into
`.tools/selene-<version>/` at the repository root (gitignored).

Network is used here, at install time, and nowhere else: linting runs the
binary offline.

    python3 scripts/fetch_selene.py          # fetch if missing, print the path
    python3 scripts/fetch_selene.py --print  # print the path, fetch nothing

Standard library only, so it runs on the system interpreter in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import platform
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.31.0"
URL = "https://github.com/Kampfkarren/selene/releases/download/{version}/{asset}"

# (system, machine) -> (release asset, SHA-256 of that asset). Checksums from
# the release's published asset digests, re-checked on download.
ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("Linux", "x86_64"): (
        f"selene-light-{VERSION}-linux.zip",
        "0580eed94b56e8b4bad3b1d6f5a4f9e1a9f5d88ae5bc511290c3cd33cc5e0360",
    ),
    ("Darwin", "arm64"): (
        f"selene-light-{VERSION}-macos.zip",
        "780cf678626f776cf7b75db82673994e9ec0660a9b3ec66fbb71d8a86a879a0a",
    ),
    ("Windows", "AMD64"): (
        f"selene-light-{VERSION}-windows.zip",
        "0b31e71beb46927997332be4171e035e6d880c61a72cc5787d8eb1fd3886497e",
    ),
}

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def binary_path() -> Path:
    name = "selene.exe" if platform.system() == "Windows" else "selene"
    return ROOT / ".tools" / f"selene-{VERSION}" / name


def fetch(target: Path) -> None:
    key = (platform.system(), platform.machine())
    if key not in ASSETS:
        raise SystemExit(
            f"fetch_selene: no pinned selene {VERSION} build for {key[0]} {key[1]}; "
            "install selene yourself and run `make lint-lua SELENE=<path>`"
        )
    asset, expected = ASSETS[key]
    url = URL.format(version=VERSION, asset=asset)
    print(f"fetch_selene: downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SystemExit(f"fetch_selene: {asset} is larger than expected; refusing it")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"fetch_selene: checksum mismatch for {asset}: expected {expected}, got {actual}"
        )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        payload = archive.read(target.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(payload)
    partial.chmod(partial.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    partial.replace(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print", action="store_true", help="print the path; fetch nothing")
    args = parser.parse_args(argv)
    target = binary_path()
    if not args.print and not target.is_file():
        fetch(target)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
