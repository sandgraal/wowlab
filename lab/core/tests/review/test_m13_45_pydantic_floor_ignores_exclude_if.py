# Probe from review of m13/45-why-not-a-flavor; reproduces `pydantic>=2.11` admitting a version that ignores `Field(exclude_if=...)`
"""M13-45 keeps the JSON of an install with no `_..._` folder byte for
byte as on `main` by leaving `not_taken` out with `Field(exclude_if=...)`.
`exclude_if` first appears in pydantic 2.12; under 2.11, which
`lab/core/pyproject.toml` still admits (`pydantic>=2.11`), `Field` takes it
as a deprecated extra keyword (a `PydanticDeprecatedSince20` warning) and
ignores it, so every `install show --json`, `doctor --json` and `explain
--json` gains `"not_taken":[]` / `"not_taken":null`. Run by the reviewer
(pydantic 2.11.10 in a scratch venv):

    {"a":1,"b":[]}   # 2.11.10: exclude_if ignored
    {"a":1}          # 2.12.0: honoured

The lock (2.14.0) hides it; a `uv tool install` resolves from the
package's own metadata. The floor has to name a version with `exclude_if`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
FIRST_WITH_EXCLUDE_IF = (2, 12)


def test_positive_control_installed_pydantic_honours_exclude_if() -> None:
    class M(BaseModel):
        a: int = 1
        b: tuple[int, ...] = Field(default=(), exclude_if=lambda v: not v)

    assert M().model_dump_json() == '{"a":1}'


def test_declared_pydantic_floor_has_exclude_if() -> None:
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    spec = next(d for d in deps if re.match(r"pydantic\s*[<>=!~]", d))
    floor = re.search(r">=\s*(\d+)\.(\d+)", spec)
    assert floor is not None, spec
    assert (int(floor[1]), int(floor[2])) >= FIRST_WITH_EXCLUDE_IF, spec
