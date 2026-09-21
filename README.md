# wowlab

A local-only toolchain over your own World of Warcraft install. It reads the
install directly, explains what every file in it is, snapshots it, and lets
you change the client's configurable state and change it back. It runs on
one machine, for one player; it is never distributed or deployed, and it
uploads nothing.

Works against any modern (CASC-era) product Battle.net installs, and is
developed against retail and *World of Warcraft: Forever*. Flavor folder,
product and build are discovered at run time, never hard-coded.

## Status

**Wave 1 in progress.** Wave 1 is the core library and CLI (`wowlab_core`,
`wowlab`). Today the package scaffold exists and `wowlab --version` is the
only behaviour; the modules below are specified in
[`docs/LAB_PLAN.md`](docs/LAB_PLAN.md) and ticketed in
[`docs/BACKLOG.md`](docs/BACKLOG.md). Later waves (character customization,
offline character tools, addon tooling) are chosen by the owner one at a
time from [`docs/LAB_IDEAS.md`](docs/LAB_IDEAS.md).

## CLI surface (planned)

Everything here except `--version` is **planned**, not built
(`docs/LAB_PLAN.md` §6.11):

```
wowlab doctor                       # install(s), flavors, builds, client running?, store health
wowlab install show [--json]
wowlab tree [PATH] [--explain]      # inventory; --explain adds the file-map entry per path
wowlab explain PATH                 # what is this file, who writes it, is it safe to edit
wowlab sv list [--account A] [--character C]
wowlab sv dump FILE [--json] [--path 'Var.key[3].name']
wowlab cvar list|get NAME [--scope global|account|character]
wowlab binds list / wowlab macros list
wowlab addons list [--json]
wowlab db2 builds | wowlab db2 fetch TABLE [--build B] | wowlab db2 head TABLE
wowlab log tail [--follow]
wowlab snap create [-m LABEL] | list | show ID | diff A B | verify | gc
wowlab snap restore ID [--paths …] [--dry-run] [--yes]
wowlab undo
```

## Scope boundary

wowlab reads anything in the install and writes only what the client treats
as user configuration: `WTF/`, `Interface/AddOns/`, `Fonts/` and loose
`Interface/` overrides. Every write goes through one gate that refuses while
the client runs, snapshots first, and can undo.

Permanently out of scope in this repository
([ADR-0023](docs/DECISIONS.md)): reading or writing client process memory,
library injection, packet capture or modification, input automation,
modifying `Data/` or executables, and defeating integrity checks. SavedVariables
and every other Lua-syntax file are parsed as data; Lua is never executed.

## Getting started

Requires [uv](https://docs.astral.sh/uv/) (it provisions Python 3.12).
Pinned versions and package-manager-free install steps:
[`docs/SETUP.md`](docs/SETUP.md). A game install is not needed to develop or
run the tests.

```bash
make setup
make ci
uv run wowlab --version
```

| Target | What it does |
|---|---|
| `make lint` | ruff check, ruff format check, `mypy --strict`. The commit gate. |
| `make test` | The full pytest suite. Anything marked `live` is excluded and never runs in CI. |
| `make test-parser` | Parser fixtures and round-trip suites. Required green for any parser change. |
| `make hooks-test` | The agent hook scripts under Python 3.12 and 3.9. |
| `make ci` | Everything CI runs. |

Set `WOWLAB_WOW_ROOT` if your install is not in a default location
(`.env.example`).

## How work happens

The code is written by Claude Code agents coordinated by a conductor
session; the owner makes the decisions and supplies real fixtures from the
install. Tickets are agent-sized with verifiable acceptance criteria, every
branch gets an independent review, and the two load-bearing files
(`luadata.py`, `guard.py`) get their tests written first by a different
agent. Work proceeds in **waves**: one milestone at a time, with the owner
picking the next after reviewing the last.

```
lab/core/   wowlab-core: the library and the wowlab CLI (later apps: lab/<app>/)
scripts/    repository tooling; the fixture capture and scrub tool lands here
tests/      tests for the agent harness hooks
docs/       spec, formats, file map, ideas, decisions, backlog, glossary, workflow
.claude/    agent definitions, skills, path rules, guard hooks
```

## Documents

| Document | What it holds |
|---|---|
| [`AGENTS.md`](AGENTS.md) | The constitution: hard invariants L1–L8, conventions, anti-patterns, when to stop and ask. `CLAUDE.md` imports it. |
| [`docs/LAB_PLAN.md`](docs/LAB_PLAN.md) | The spec: purpose, architecture, per-module specifications, testing, how waves work. |
| [`docs/LAB_FORMATS.md`](docs/LAB_FORMATS.md) | Grammar reference for the client file formats. |
| [`docs/LAB_FILE_MAP.md`](docs/LAB_FILE_MAP.md) | Every path in an install: what it is, who writes it, whether it is safe to edit. |
| [`docs/LAB_IDEAS.md`](docs/LAB_IDEAS.md) | The menu for later waves. Not tickets. |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architecture Decision Records. |
| [`docs/BACKLOG.md`](docs/BACKLOG.md) | The current wave's tickets. |
| [`docs/GLOSSARY.md`](docs/GLOSSARY.md) | WoW and client-filesystem terms, several of them misleading. |
| [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) | The local install, wago.tools, format references, breakage log. |
| [`docs/AGENT_WORKFLOW.md`](docs/AGENT_WORKFLOW.md) | Roles, ticket lifecycle, guardrails, reusing the harness. |
| [`docs/SETUP.md`](docs/SETUP.md) | Machine setup. |

## History

This repository previously held a different project. It was retired on
2026-09-21 (ADR-0025); its last state is the git tag `bronze-final`.

## License and affiliation

[Apache-2.0](LICENSE). World of Warcraft and Blizzard Entertainment are
trademarks of Blizzard Entertainment, Inc. wowlab is an independent personal
project, not affiliated with or endorsed by Blizzard. No game assets or
Blizzard code are committed here. Security and privacy reports:
[`SECURITY.md`](SECURITY.md).
