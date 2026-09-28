# lab/

Everything that runs lives here. One directory per app; each is a uv
workspace member.

| Directory | Package | What it is |
|---|---|---|
| `core/` | `wowlab-core` (import `wowlab_core`, command `wowlab`) | The library and CLI every later tool stands on: install discovery, layout, parsers, game data, snapshots, the write gate. Spec: `docs/LAB_PLAN.md`. |
| `addon/` | none (Lua for the game client, ADR-0026) | The Lab's own addons, first `WowLab/` (§13.1). Never run by the Lab; linted by `make lint-lua`. See `addon/README.md`. |

Later waves add siblings of `core/` (`lab/<app>/`). Apps depend on
`wowlab-core`; `wowlab-core` depends on none of them. Nothing here is
distributed, deployed, or uploads anything (ADR-0019).

```bash
make setup
uv run wowlab --version
uv run wowlab doctor     # the install, its flavors, whether the client runs, the store
uv run wowlab --help     # every command (docs/LAB_PLAN.md §6.11)
```

Exit codes: 0 ok, 1 error, 2 usage, 3 refused by the write gate. Only
`wowlab snap restore` and `wowlab undo` change an install, both through the
gate, and both print the plan and ask first unless given `--yes`.

`WOWLAB_WOW_ROOT` points the tools at an install when it is not in a default
location (`.env.example`).

Tests: `lab/core/tests/`. Real fixtures and their provenance index:
`lab/core/tests/fixtures/README.md`. Reviewer probes:
`lab/core/tests/review/`.
