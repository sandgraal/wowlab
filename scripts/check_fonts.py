"""Check the pinned Wellspring fonts against their sources (ADR-0035, M16-51).

`wowlab ui` writes twelve WOFF2 files into its page's own style; they and
their two SIL Open Font License texts live in
`lab/core/src/wowlab_core/ui/templates/fonts/`, copied unchanged from the
URLs below (ADR-0035's table and its amendment of 2026-10-26). This script
downloads each URL and checks that the bytes served there, the pin below
and the file in the package are one and the same.

Run by hand, never in CI and never from a test (ADR-0012): it makes
fourteen requests to fonts.gstatic.com and raw.githubusercontent.com. The
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
        "alegreya-latin.woff2",
        _GSTATIC + "alegreya/v41/4UaBrEBBsBhlBjvfkSLhx6g.woff2",
        43476,
        "2431eca3ed8ed66e713cf7093d170d77a083a793a5f19a615506d63770abd973",
    ),
    Pin(
        "alegreya-latin-ext.woff2",
        _GSTATIC + "alegreya/v41/4UaBrEBBsBhlBjvfkSLvx6jx4w.woff2",
        37856,
        "16b24f84da5ac12dc79ea1060e3fd4e299f19146f607021f81bcf0b8123fe5bd",
    ),
    Pin(
        "alegreya-italic-latin.woff2",
        _GSTATIC + "alegreya/v41/4UaHrEBBsBhlBjvfkSLk96rp5w.woff2",
        44456,
        "1846c307495f922d3fc00ce3c32aaaba8eefc7c348425a8244a7ffc63ddc0993",
    ),
    Pin(
        "alegreya-italic-latin-ext.woff2",
        _GSTATIC + "alegreya/v41/4UaHrEBBsBhlBjvfkSLk96Tp56N1.woff2",
        38316,
        "3b6ec2f8e48aff730aaebeb52423ba2f7967c5b80b1727907e4b1154786fd54c",
    ),
    Pin(
        "alegreya-sc-500-latin.woff2",
        _GSTATIC + "alegreyasc/v28/taiTGmRtCJ62-O0HhNEa-ZZc-oU2SKo.woff2",
        30880,
        "98b4a138b6117d621a122a7c8590aa945720e4953d926b921a61e0d3f96a02c9",
    ),
    Pin(
        "alegreya-sc-500-latin-ext.woff2",
        _GSTATIC + "alegreyasc/v28/taiTGmRtCJ62-O0HhNEa-ZZc-oU4SKqGFQ.woff2",
        31944,
        "fae2927855146328875e379a3f0dcd5ab26629413347838b32190ea441b1a4b7",
    ),
    Pin(
        "alegreya-sc-700-latin.woff2",
        _GSTATIC + "alegreyasc/v28/taiTGmRtCJ62-O0HhNEa-ZYU_IU2SKo.woff2",
        30660,
        "988821cdcc03a1c0f7587143e4d921582595c5bcd803061863f7c3be7dd0d4b8",
    ),
    Pin(
        "alegreya-sc-700-latin-ext.woff2",
        _GSTATIC + "alegreyasc/v28/taiTGmRtCJ62-O0HhNEa-ZYU_IU4SKqGFQ.woff2",
        31656,
        "42e3b3a35260c3626cb1e5fb71a134da3e856a8820d4ab757261fc383aa762f5",
    ),
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
        "OFL-Alegreya.txt",
        _OFL + "alegreya/OFL.txt",
        4395,
        "f6f60d5d4cf4f4b1fc4e41353c897a2f5a16e6396c0cd8fa8bdfd2f4586a9a68",
    ),
    Pin(
        "OFL-IBMPlexMono.txt",
        _OFL + "ibmplexmono/OFL.txt",
        4456,
        "7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da",
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
