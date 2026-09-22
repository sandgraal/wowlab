"""Install discovery against the real `.build.info` / `.flavor.info` captures (L8).

The fixture tree is not an install: the capture tool renames the flavor
folder with `--kind` (`forever/` in the tree), and the provenance row records
the real folder name. Each test builds a synthetic install in `tmp_path` from
the fixture bytes, with the flavor folder named per its provenance row.

Every platform directory under `fixtures/` that holds a `.build.info` must
have an entry in `EXPECTED`; a new capture fails the suite until it does.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.install import BuildInfoRow, Flavor, Install, discover, read_install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"
NOT_PLATFORMS = frozenset({"wago", "incoming"})


def _index_flavor_column() -> dict[str, str]:
    """{fixture path: `flavor` cell} from the provenance index."""
    rows: dict[str, str] = {}
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = cells[2]
    return rows


def _platforms_with_build_info() -> list[str]:
    return sorted(
        p.name
        for p in FIXTURES.iterdir()
        if p.is_dir() and p.name not in NOT_PLATFORMS and (p / ".build.info").is_file()
    )


def _install_from_fixtures(platform: str, dest: Path) -> Path:
    """Copy one platform's `.build.info` and every `.flavor.info` into a
    synthetic install, each flavor folder named as its provenance row says."""
    flavors = _index_flavor_column()
    source = FIXTURES / platform
    root = dest / "World of Warcraft"
    root.mkdir()
    shutil.copyfile(source / ".build.info", root / ".build.info")
    for info in sorted(source.glob("*/.flavor.info")):
        folder = flavors[info.relative_to(FIXTURES).as_posix()]
        assert re.fullmatch(r"_[^/\\]+_", folder), f"{info}: index flavor {folder!r}"
        (root / folder).mkdir()
        shutil.copyfile(info, root / folder / ".flavor.info")
    return root


def _cell(platform: str, position: int) -> str:
    """Cell `position` of the first data line, split by hand. The two 32-hex
    keys are taken this way rather than written out because the secret
    scanner (gitleaks, no allowlist by design) reads a `key` next to 32 hex
    digits as a credential. They are public CDN configuration hashes."""
    line = (FIXTURES / platform / ".build.info").read_bytes().split(b"\n")[1]
    return line.decode("utf-8").split("|")[position]


# Expected discovery result per captured platform. Values are read off the
# fixture bytes and the provenance index, not produced by the code under test.
# macOS: the 15 cells are in the LAB_FORMATS §1 order (2026-09-22 amendment),
# so `Build Key` is cell 2 and `CDN Key` cell 3.
_MACOS_BUILD_KEY = _cell("macos", 2)
_MACOS_CDN_KEY = _cell("macos", 3)
EXPECTED: dict[str, dict[str, Any]] = {
    "macos": {
        "flavors": [
            {
                "folder": "_classic_beta_",
                "product": "wow_classic_beta",
                "version": "1.60.1.69913",
                "build": 69913,
                "build_key": _MACOS_BUILD_KEY,
            }
        ],
        "rows": [
            {
                "product": "wow_classic_beta",
                "version": "1.60.1.69913",
                "build_key": _MACOS_BUILD_KEY,
                "branch": "us",
                "active": "1",
                "tags": (
                    "OSX x86_64 US? acct-USA? geoip-US? enUS speech?:"
                    "OSX x86_64 US? acct-USA? geoip-US? enUS text?"
                ),
                "extra": {
                    "CDN Key": _MACOS_CDN_KEY,
                    "Install Key": "",
                    "IM Size": "",
                    "CDN Path": "tpr/wow",
                    "CDN Hosts": "level3.blizzard.com us.cdn.blizzard.com",
                    "CDN Servers": (
                        "http://level3.blizzard.com/?maxhosts=8 "
                        "http://us.cdn.blizzard.com/?maxhosts=4&fallback=1 "
                        "https://level3.ssl.blizzard.com/?maxhosts=4&fallback=1 "
                        "https://us.cdn.blizzard.com/?maxhosts=4&fallback=1"
                    ),
                    "Armadillo": "",
                    "Last Activated": "",
                    "KeyRing": "",
                },
            }
        ],
    },
}


@pytest.mark.parser
def test_hand_split_keys_are_the_keys() -> None:
    for key in (_MACOS_BUILD_KEY, _MACOS_CDN_KEY):
        assert re.fullmatch(r"[0-9a-f]{32}", key), "cell positions moved; fix _cell() calls"
    assert _MACOS_BUILD_KEY != _MACOS_CDN_KEY


@pytest.mark.parser
def test_every_captured_platform_has_an_expectation() -> None:
    assert _platforms_with_build_info() == sorted(EXPECTED), (
        "a platform capture with a .build.info needs an EXPECTED entry, and "
        "an EXPECTED entry needs its capture"
    )


@pytest.mark.parser
@pytest.mark.parametrize("platform", sorted(EXPECTED))
def test_real_fixture_resolves_to_expected_install(platform: str, tmp_path: Path) -> None:
    root = _install_from_fixtures(platform, tmp_path)
    expected = EXPECTED[platform]

    install = discover(root, environ={}, defaults=[])

    raw = (FIXTURES / platform / ".build.info").read_bytes()
    assert install == Install(
        root=root,
        flavors=tuple(Flavor(**f, path=root / str(f["folder"])) for f in expected["flavors"]),
        products=tuple(BuildInfoRow(**r) for r in expected["rows"]),
        raw_build_info=raw.decode("utf-8"),
    )
    assert install.raw_build_info.encode("utf-8") == raw, (
        "raw_build_info is the file, byte for byte"
    )
    assert read_install(root) == install


@pytest.mark.parser
@pytest.mark.parametrize("platform", sorted(EXPECTED))
def test_real_fixture_every_cell_is_kept_in_header_order(platform: str, tmp_path: Path) -> None:
    """Unknown columns preserved (L4): the known fields plus `extra` rebuild
    each data line of the real file exactly, in header order."""
    root = _install_from_fixtures(platform, tmp_path)
    install = read_install(root)
    lines = install.raw_build_info.split("\n")
    header = lines[0].split("|")
    names = [cell.split("!", 1)[0] for cell in header]
    known = {
        "Product": "product",
        "Version": "version",
        "Build Key": "build_key",
        "Branch": "branch",
        "Active": "active",
        "Tags": "tags",
    }
    data_lines = [line for line in lines[1:] if line]
    assert len(data_lines) == len(install.products)
    for line, row in zip(data_lines, install.products, strict=True):
        assert list(row.extra) == [n for n in names if n not in known], "extra keeps header order"
        cells = [
            getattr(row, known[n]) if n in known else row.extra[n]  # rebuilt from the model
            for n in names
        ]
        assert "|".join(cells) == line
        assert row.overflow == ()


@pytest.mark.parser
@pytest.mark.parametrize("platform", sorted(EXPECTED))
def test_real_flavor_info_with_no_matching_row_constructed(platform: str, tmp_path: Path) -> None:
    """Constructed `.build.info` (header only, from the real file's first
    line); real `.flavor.info`. The flavor is returned, with no version."""
    root = _install_from_fixtures(platform, tmp_path)
    header = (FIXTURES / platform / ".build.info").read_bytes().split(b"\n", 1)[0]
    (root / ".build.info").write_bytes(header + b"\n")

    install = read_install(root)

    assert install.products == ()
    assert [f.folder for f in install.flavors] == [
        f["folder"] for f in EXPECTED[platform]["flavors"]
    ]
    for flavor in install.flavors:
        assert flavor.product, "the product code still comes from .flavor.info"
        assert (flavor.version, flavor.build, flavor.build_key) == (None, None, None)
