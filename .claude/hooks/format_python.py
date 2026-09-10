#!/usr/bin/env python3
"""PostToolUse(Edit|Write): keep Python files ruff-clean as they are written.

Runs `ruff format` and `ruff check --fix` on the edited file through the
checkout's own environment (`uv run --frozen`, so the lockfile is never
touched). Never blocks; if the environment is missing it does nothing and
`make lint` reports the drift later.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import checkout_root, read_hook_input


def main() -> None:
    data = read_hook_input() or {}
    if data.get("tool_name") not in {"Edit", "Write", "MultiEdit"}:
        return
    file_path = str(data.get("tool_input", {}).get("file_path", ""))
    if not file_path.endswith(".py"):
        return
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(str(data.get("cwd") or ".")) / path
    if not path.exists():
        return
    root = checkout_root(path.parent)
    if root is None or not (root / ".venv").exists() or shutil.which("uv") is None:
        return
    for args in (["ruff", "format", "-q", str(path)], ["ruff", "check", "--fix", "-q", str(path)]):
        subprocess.run(
            ["uv", "run", "--frozen", *args],
            cwd=str(root),
            capture_output=True,
            timeout=50,
            check=False,
        )


if __name__ == "__main__":
    main()
