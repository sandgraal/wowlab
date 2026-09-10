"""CI check: every agent, skill, and rule file has well-formed YAML frontmatter.

Runs with only PyYAML available (`uv run --with pyyaml`), so keep it dependency-light.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / ".claude"
REQUIRED = {
    "agents": {"name", "description"},
    "skills": {"name", "description"},
    "rules": {"paths"},
}
VALID_MODELS = {"inherit", "sonnet", "opus", "haiku", "fable"}


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("unterminated frontmatter")
    data = yaml.safe_load(text[4:end])
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data


def main() -> int:
    failures: list[str] = []
    checked = 0
    for kind, required in REQUIRED.items():
        for path in sorted((ROOT / kind).rglob("*.md")):
            checked += 1
            try:
                fm = frontmatter(path)
            except (ValueError, yaml.YAMLError) as exc:
                failures.append(f"{path}: {exc}")
                continue
            missing = required - set(fm)
            if missing:
                failures.append(f"{path}: missing {sorted(missing)}")
            if kind == "agents" and fm["name"] != path.stem:
                failures.append(f"{path}: name {fm['name']!r} != filename")
            if kind == "skills" and fm["name"] != path.parent.name:
                failures.append(f"{path}: name {fm['name']!r} != directory")
            if kind == "agents" and "model" in fm and fm["model"] not in VALID_MODELS:
                failures.append(f"{path}: unknown model {fm['model']!r}")
            if kind == "rules" and not isinstance(fm["paths"], list):
                failures.append(f"{path}: paths must be a list")
    for line in failures:
        print(f"::error::{line}")
    print(f"checked {checked} files, {len(failures)} problems")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
