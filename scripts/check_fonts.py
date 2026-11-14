"""Check the pinned UI fonts against their sources (ADR-0035, M16-51, M18-03).

`wowlab ui` writes eight WOFF2 files into its page's own style; they and
their three SIL Open Font License texts live in
`lab/core/src/wowlab_core/ui/templates/fonts/`, copied unchanged from the
URLs below (ADR-0035's table and its amendments of 2026-10-26 and
2026-11-14). This script downloads each URL and checks that the bytes
served there, the pin below and the file in the package are one and the
same.

Run by hand, never in CI and never from a test (ADR-0012): it makes
eleven requests to fonts.gstatic.com and raw.githubusercontent.com. The
offline check of the package files against the same pins is
`lab/core/tests/test_ui_fonts.py`.

    python3 scripts/check_fonts.py

Standard library only. Writes nothing. Exit 0 when every file matches, 1
when any does not (one line per file on stderr), 2 on a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "lab" / "core" / "src" / "wowlab_core" / "ui" / "templates" / "fonts"

_GSTATIC = "https://fonts.gstatic.com/s/"
_OFL = (
    "https://raw.githubusercontent.com/google/fonts/e7db6ca603fd4862010383ff17ffd6cc4ffac1c0/ofl/"
)


class Pin(NamedTuple):
    name: str
    url: str
    size: int
    sha256: str


PINS: tuple[Pin, ...] = (
    Pin(
        "ibm-plex-mono-400-latin.woff2",
        _GSTATIC + "ibmplexmono/v20/-F63fjptAgt5VM-kVkqdyU8n1i8q1w.woff2",
        14708,
        "08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7",
    ),
    Pin(
        "ibm-plex-mono-400-latin-ext.woff2",
        _GSTATIC + "ibmplexmono/v20/-F63fjptAgt5VM-kVkqdyU8n1iEq129k.woff2",
        13348,
        "6bc0f226a5b7884a8170e3f62c63d7675609d4631bdc5931b5cdab81821f00eb",
    ),
    Pin(
        "ibm-plex-mono-500-latin.woff2",
        _GSTATIC + "ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgg.woff2",
        14888,
        "01d285447409c8a588692162439a038b8cbd7871309ee20267b0d2d91c6e8e22",
    ),
    Pin(
        "ibm-plex-mono-500-latin-ext.woff2",
        _GSTATIC + "ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwl5FgtIU.woff2",
        13432,
        "6bb06407c97584b0867a959e05e8874693bfeb8c317de190811c51598f2d99ea",
    ),
    Pin(
        "inter-latin.woff2",
        _GSTATIC + "inter/v20/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa1ZL7W0Q5nw.woff2",
        48432,
        "c940764593d0fe5d596be327ca7558855e018039fb78509aa21921fd3644c3e4",
    ),
    Pin(
        "inter-latin-ext.woff2",
        _GSTATIC + "inter/v20/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa25L7W0Q5n-wU.woff2",
        85272,
        "a28eb6d3ccb534ae0c94ca999371df024aab60b08c3c8a5720ee9e32fa0faaa2",
    ),
    Pin(
        "sora-latin.woff2",
        _GSTATIC + "sora/v17/xMQ9uFFYT72X5wkB_18qmnndmSdSnh2BAfO5mnuyOo1lfiQwV6-xo6eeIw.woff2",
        25240,
        "3902474d3ece89c8580d5ef3325cf58753e6e45a86f9957b4dacc4fce2e7fde1",
    ),
    Pin(
        "sora-latin-ext.woff2",
        _GSTATIC + "sora/v17/xMQ9uFFYT72X5wkB_18qmnndmSdSnh2BAfO5mnuyOo1lfiQwWa-xo6eeI7fM.woff2",
        12116,
        "8d9b4a4136bf8cf8b00c65978e7801c372d14ed901fa387cc7352a40b96cd006",
    ),
    Pin(
        "OFL-IBMPlexMono.txt",
        _OFL + "ibmplexmono/OFL.txt",
        4456,
        "7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da",
    ),
    Pin(
        "OFL-Inter.txt",
        _OFL + "inter/OFL.txt",
        4377,
        "5b9321a4298cfeb6b34354164a1c3afc3db114569984c502b9b35d988fd58c57",
    ),
    Pin(
        "OFL-Sora.txt",
        _OFL + "sora/OFL.txt",
        4385,
        "ba0b9729c9428ba79a0459ab8ec575791b51509dbec213e383d0316d37fec299",
    ),
)


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as reply:
        data: bytes = reply.read()
    return data


def check(pin: Pin, served: bytes, packaged: bytes | None) -> list[str]:
    """Why `pin` does not hold: the served bytes against the pin, and the
    package's file against the served bytes."""
    problems = []
    digest = hashlib.sha256(served).hexdigest()
    if len(served) != pin.size or digest != pin.sha256:
        problems.append(f"the source serves {len(served)} bytes, SHA-256 {digest}")
    if packaged is None:
        problems.append("the package has no such file")
    elif packaged != served:
        problems.append("the package's file differs from the source")
    return problems


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n", 1)[0]).parse_args(argv)
    failed = 0
    for pin in PINS:
        try:
            served = _fetch(pin.url)
        except OSError as err:
            print(f"{pin.name}: download failed: {err}", file=sys.stderr)
            failed += 1
            continue
        path = FONTS / pin.name
        packaged = path.read_bytes() if path.is_file() else None
        if problems := check(pin, served, packaged):
            print(f"{pin.name}: {'; '.join(problems)}", file=sys.stderr)
            failed += 1
        else:
            print(f"{pin.name}: ok ({pin.size} bytes)")
    extra = sorted(p.name for p in FONTS.iterdir()) if FONTS.is_dir() else []
    extra = [name for name in extra if name not in {pin.name for pin in PINS}]
    for name in extra:
        print(f"{name}: in the package but not pinned", file=sys.stderr)
    return 1 if failed or extra else 0


if __name__ == "__main__":
    sys.exit(main())
