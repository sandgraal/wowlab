"""Source scan, not a fixture test (M11-20).

Client APIs that crashed the Forever client when the lab-addon called them.
`pcall` cannot protect against a C++ assertion inside the client, so the only
safe call is none: no file under `lab/addon/WowLab/` may mention these names,
in code, comments or strings. Nothing here runs Lua (L3); the files are read
as bytes.

- `GetCategoryAppearances` (`C_TransmogCollection`): called for an
  `Enum.TransmogCollectionType` category on entering the world, it hit a C++
  assertion and crashed the client on the first M11-03 login
  (docs/LAB_PLAN.md §13.1, amended 2026-09-28).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "lab" / "addon" / "WowLab"
FILES = sorted(p for p in ADDON.rglob("*") if p.is_file())

CRASHING_APIS = ("GetCategoryAppearances",)


def test_the_scan_sees_the_addon_sources() -> None:
    names = {p.name for p in FILES}
    assert "WowLab.toc" in names, f"expected the addon under {ADDON}"
    assert "Collections.lua" in names, f"expected Collections.lua under {ADDON}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(ADDON).as_posix())
@pytest.mark.parametrize("api", CRASHING_APIS)
def test_source_scan_addon_never_mentions_a_crashing_api(api: str, path: Path) -> None:
    text = path.read_bytes().decode("utf-8", errors="replace")
    hits = [n for n, line in enumerate(text.splitlines(), start=1) if api in line]
    assert not hits, f"{path.relative_to(ROOT)} mentions {api} on line(s) {hits} (M11-20)"
