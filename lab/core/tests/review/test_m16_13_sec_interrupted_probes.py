"""Security probes for M16-13 (security-reviewer, constructed inputs).

- A journal record whose id is not the strict `\\d{8}-<8 hex>` shape cannot
  reach the `undo --expect` line: an id holding ESC or a space makes the
  journal unreadable, which is a different problem with no undo line.
- The `wowlab-temp` row matches only the exact lowercase name guard makes
  (low-1, fixed in 7e5754e by the `<wowlab-temp>` token), and a misspelt
  token matches only its own literal text.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import filemap, guard, snapshot


def _last_path() -> tuple[Path, dict[str, Any]]:
    last = guard.history()[-1]
    path = snapshot.default_store_path() / "journal" / f"{last.id}.json"
    return path, json.loads(path.read_text(encoding="ascii"))


def test_an_id_with_control_text_never_reaches_the_undo_line(flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    path, record = _last_path()
    record["state"] = "open"
    record["id"] = "00000001-abcdef12\x1b]0;pwned\x07; rm -rf ~"
    path.write_text(json.dumps(record), encoding="ascii")
    text = run("doctor")
    assert "\x1b" not in text.stdout
    assert "undo --expect" not in text.stdout
    assert "journal of the write gate cannot be read" in text.stdout


def test_a_file_name_with_control_text_never_loads(flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    path, record = _last_path()
    bad = "00000009-abcdef1\x1b"
    record["state"] = "open"
    record["id"] = bad
    (path.parent / f"{bad}.json").write_text(json.dumps(record), encoding="ascii")
    text = run("doctor")
    assert "\x1b" not in text.stdout
    assert f"--expect {bad}" not in text.stdout


def test_unicode_digit_ids_load_and_print_pre_existing(flavor: Path) -> None:
    """Pre-existing: `_RECORD_NAME` uses `\\d` without re.ASCII."""
    ok("addon", "install", "lab", "--yes")
    path, record = _last_path()
    uid = chr(0x660) * 7 + chr(0x669) + "-abcdef12"  # Arabic-Indic digits
    record["state"] = "open"
    record["id"] = uid
    path.unlink()
    (path.parent / f"{uid}.json").write_text(json.dumps(record), encoding="utf-8")
    text = run("doctor")
    # Loaded as a record (no control character, so nothing to escape).
    assert f"wowlab undo --expect {uid}" in text.stdout


def test_only_the_exact_lowercase_name_classifies_as_wowlab_temp() -> None:
    """low-1, fixed in 7e5754e: case variants are not wowlab's temp."""
    fm = filemap.load()
    hexes = "0123456789abcdef" * 2
    lower = ("WTF", f".wowlab-{hexes}.tmp")
    found_lower = fm.classify(lower, "flavor", "file")
    assert found_lower is not None and found_lower.id == "wowlab-temp"
    for name in (
        f".WOWLAB-{hexes.upper()}.TMP",
        f".wowlab-{hexes.upper()}.tmp",
        f".Wowlab-{hexes}.tmp",
        f".wowlab-{hexes}.TMP",
        f".wowlab-{hexes}.tmp\n",
        f".wowlab-{hexes}.tmp.bak",
        f"x.wowlab-{hexes}.tmp",
        f".wowlab-{hexes[:-1]}.tmp",
        f".wowlab-{hexes}0.tmp",
        f".wowlab-{hexes[:-1]}g.tmp",
        f".wowlab-{hexes[:-1]}\uff10.tmp",  # fullwidth digit
        f".wowlab-{hexes}xtmp",
    ):
        found = fm.classify(("WTF", name), "flavor", "file")
        assert found is None or found.id != "wowlab-temp", name
        assert not guard._TEMP_NAME.fullmatch(name), name
    d = fm.classify(lower, "flavor", "dir")
    assert d is None or d.id != "wowlab-temp"
    # Outside the three allowlisted roots the row does not apply.
    out = fm.classify(("Logs", lower[1]), "flavor", "file")
    assert out is None or out.id != "wowlab-temp"


def test_a_malformed_token_matches_only_its_literal_text() -> None:
    """A misspelt token is not the token: it falls to fnmatch, where `<`,
    `>` and `-` are literal, so it matches no real temp name."""
    hexes = "0123456789abcdef" * 2
    real = (f".wowlab-{hexes}.tmp",)
    for pattern in (
        "<wowlab-temp",
        "wowlab-temp>",
        "<WOWLAB-TEMP>",
        "<wowlab-temp> ",
        "<wowlab-temp>x",
        "<wowlab_temp>",
        "<wowlab-temp>*",
    ):
        assert not filemap._match((pattern,), real), pattern
    assert filemap._match(("<wowlab-temp>",), real)
    assert not filemap._match(("<wowlab-temp>",), ("anything.lua",))
