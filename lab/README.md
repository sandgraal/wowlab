# lab/

Everything that runs lives here. One directory per app; each is a uv
workspace member.

| Directory | Package | What it is |
|---|---|---|
| `core/` | `wowlab-core` (import `wowlab_core`, command `wowlab`) | The library and CLI every later tool stands on: install discovery, layout, parsers, game data, snapshots, the write gate. Spec: `docs/LAB_PLAN.md`. |

Later waves add siblings of `core/` (`lab/<app>/`). Apps depend on
`wowlab-core`; `wowlab-core` depends on none of them. Nothing here is
distributed, deployed, or uploads anything (ADR-0019).

```bash
make setup
uv run wowlab --version
```

`WOWLAB_WOW_ROOT` points the tools at an install when it is not in a default
location (`.env.example`).

Tests: `lab/core/tests/`. Real fixtures and their provenance index:
`lab/core/tests/fixtures/README.md`. Reviewer probes:
`lab/core/tests/review/`.
