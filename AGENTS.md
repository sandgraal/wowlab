# wowlab — Constitution

This file wins over every other instruction in the repository. `CLAUDE.md`
imports it. Read it, then `docs/LAB_PLAN.md`, before touching anything.

## What this is

wowlab (the Lab) is a local toolchain for one player's own machine. It reads
a World of Warcraft install directly, explains what every file in it is,
snapshots it, and lets the owner change the client's configurable state and
change it back. Wave 1 is a Python library and a CLI (`wowlab_core`,
`wowlab`); character-customization tools, offline character tools and addon
experiments are built on that base in later, owner-selected waves. It is
never distributed, never deployed, and never uploads anything (ADR-0019).

## Hard invariants

Violating any of these is a bug even if tests pass.

**L1 — Reads never write.** Every module except `guard` opens the install
read-only. No temp files, caches or lock files inside the install. Caches
and the snapshot store live under the user data directory
(`docs/LAB_PLAN.md` §6.9).

**L2 — One write gate.** Every byte written into an install goes through
`wowlab_core.guard` (ADR-0021): client not running, path inside an
allowlisted subtree, snapshot taken first, operation journaled, atomic
replace. There is no second code path and no `force` that skips the snapshot.

**L3 — No Lua execution.** SavedVariables and every other Lua-syntax file
are parsed as data by a constrained literal parser. No interpreter, no
`load`, no third-party library that evaluates. Functions, metatables, calls,
operators and bare identifiers as values are rejected with a position.

**L4 — Lossless.** Parsers keep what they do not understand. `luadata` keeps
key order, key style, and the original text of every number. The WTF text
parsers keep every line, including ones they cannot classify. For an
unmodified document, `serialize(parse(x)) == x` byte for byte on every real
fixture.

**L5 — Game data is keyed by build and never overwritten.** A cached table
for build A is never replaced by build B (ADR-0022).

**L6 — Nothing is hard-coded about a flavor.** No `_retail_`, no
`_classic_beta_`, no product code, no interface number, no build number in
library code. Tests may name them; the library discovers them (ADR-0020).

**L7 — Out of scope, permanently, in this repository** (ADR-0023): process
memory reads or writes, DLL/dylib injection, packet capture or modification,
input automation, editing anything under `Data/` or any executable, and
bypassing the client's integrity checks. A ticket that seems to need one of
these is mis-specified; stop and report.

**L8 — Real fixtures.** Any parser of an external format is graded against
real captures with a provenance row (ADR-0012). Constructed inputs are
allowed only for hostile-input and boundary tests, and are labelled as such.

## Where truth lives

- `docs/LAB_PLAN.md` — purpose, architecture, per-module specs, testing, waves. The spec.
- `docs/LAB_FORMATS.md` — grammar reference for every client file format we parse.
- `docs/LAB_FILE_MAP.md` — every path in an install: what it is, who writes it, edit safety.
- `docs/LAB_IDEAS.md` — the menu for later waves. A menu, not tickets.
- `docs/DECISIONS.md` — ADRs. Read before proposing an alternative. Status is
  `Proposed` until the repository owner flips it to `Accepted`; no agent does.
- `docs/BACKLOG.md` — agent-sized tickets with acceptance criteria. Ticket
  headings carry status (`## [ ] M10-04 — …`). Implementers never edit it.
- `docs/GLOSSARY.md` — WoW domain and client-filesystem terms. Several are counterintuitive.
- `docs/AGENT_WORKFLOW.md` — how work moves: roles, lifecycle, conventions.
- `docs/DATA_SOURCES.md` — the local install, wago.tools, format references, breakage log.
- `docs/SETUP.md` — machine setup. Machine-specific notes go in `CLAUDE.local.md` (gitignored).

## Operating mode: conductor

The main session **orchestrates; it does not implement** (ADR-0013). Work
starts with `/conduct milestone M10` (or `next`, or ticket ids). Feature
code is written by the `implementer` agent and graders by `test-writer`,
each in its own worktree; `code-reviewer` grades every branch spec-first;
`domain-reviewer` and `security-reviewer` are added by area; `pr-shepherd`
opens the PR, resolves every thread, and **merges autonomously** once the
required checks are green, threads are resolved, and reviews are clean.

- The conductor's only source edits are harness (`.claude/`), docs, backlog
  ticks, and handoffs. Everything else goes to an agent, including fixes for
  review comments (`SendMessage` to the implementer that has the context).
- Parallel is the default. Dispatch every independent eligible ticket at
  once; holding one back needs a named file or interface collision.
- Test-writer / implementer separation is mandatory for the two
  load-bearing files, `lab/core/src/wowlab_core/luadata.py` (M10-04) and
  `lab/core/src/wowlab_core/guard.py` (M10-11): a `[TEST]` ticket lands
  graders before the `[IMPL]` ticket creates the file. The session that writes the code never
  writes, edits, or weakens the tests that grade it.
- Work runs in owner-selected waves (ADR-0024). One wave is one milestone.
  When a wave's review ticket is the only one left, the conductor writes
  `docs/handoffs/M<n>-review.md` and stops dispatch until the owner picks
  the next wave. Nothing in `docs/LAB_IDEAS.md` is built without a ticket.
- Subagents never edit `.claude/`, `AGENTS.md`, `CLAUDE.md`,
  `docs/DECISIONS.md`, or `docs/BACKLOG.md`; a hook enforces it. They report
  the need instead.

## Conventions

Python 3.12, one uv workspace (members under `lab/`), ruff for lint and
format, `mypy --strict` on `lab/core/src`, pytest, Pydantic v2 models for
anything that crosses a module boundary, `pathlib` everywhere. There is no
other language or runtime in this repository; one arrives only through an
ADR in the wave that needs it (ADR-0020). Runtime dependencies are limited
to the list in `docs/LAB_PLAN.md` §6; adding one needs a line in the PR body.

Tests use real fixtures, not generated examples. For anything parsing an
external format, the fixture is a real capture that went through
`scripts/lab_capture.py` (ticket M10-02 creates it), with a provenance row in
`lab/core/tests/fixtures/README.md`. The repository is public: an unscrubbed
capture is a leak. No test needs a real install, and none writes to one.

- Branches: `m<milestone>/<nn>-<slug>` — `m10/04-luadata-parser`, `m10/04-luadata-parser-tests`.
- Commits: `type(scope): summary (M10-04)`. Squash-merged; small commits are fine.
- PRs: draft on first push, ready when reviews are clean, body from the
  template with proof per acceptance criterion and a `session:` footer.
- `.claude/rules/` carries area-specific rules that load when you open a file
  in that area. They extend this file; they never override it.

## Verification

```bash
make setup         # uv sync, pre-commit, .env
make lint          # ruff check + format --check + mypy --strict (the commit gate)
make test          # pytest, all suites except @live
make test-parser   # parser fixtures + round-trip (run on any parser change)
make hooks-test    # harness hooks under 3.12 and 3.9
make ci            # all of the above
uv run wowlab --version
```

A change to `luadata.py` or any format parser requires `make test-parser`
green before review; a change to `guard.py` requires the full suite. A
ticket is done when CI is green, acceptance criteria have pasted proof, new
external-format parsing has a real fixture with a provenance row, graders
were activated by marker deletion only, and anything contradicting an ADR
ships with a superseding ADR rather than a silent deviation.

## Anti-patterns specific to this codebase

**Do not call a live service from tests or CI.** wago.tools is community-run
and unmetered on trust. Record responses as fixtures and replay them; a live
check is `@pytest.mark.live` and run by hand (ADR-0012).

**Do not write into an install outside `guard`.** No helper, no "just this
once", no flag that skips the client check, the allowlist or the snapshot. A
tool that believes it needs to bypass the gate is mis-specified.

**Do not execute Lua.** Not to parse, not to "check" a file, not through a
library that evaluates. Reject function definitions and metatables.

**Do not hard-code a flavor.** Folder names, product codes, interface and
build numbers come from discovery. The Forever beta's folder will change at
launch; a constant is a bug with a date on it.

**Do not build from `docs/LAB_IDEAS.md`.** It is a menu. An agent building
something from it without a ticket is off-task, however small the thing is.

**Do not parse and re-serialize in the scrub tool.** `scripts/lab_capture.py`
makes byte-level targeted replacements. A fixture produced by the parser
under test proves nothing about that parser.

**Do not special-case a fixture that will not round-trip.** It is a finding
against the document model; report it.

## Stop and ask

Escalate rather than deciding unilaterally when:

- A ticket seems to need a write outside `guard`, or a path outside its allowlist.
- Anything touches what ADR-0023 excludes, or comes close enough to argue about.
- A real fixture contradicts `docs/LAB_FORMATS.md` in a way that changes a
  deliverable (a contradiction that only changes the reference is an
  amendment: follow the fixture, date the note, say so in the report).
- A performance target is missed (`docs/LAB_PLAN.md` §6.4, ADR-0020): report
  the numbers and stop; no C extension or second language without an ADR.
- A capture or fixture turns out to contain an identifier the scrub tool missed.
- An ADR needs to move from Proposed to Accepted, or be superseded.
- Reviewer and implementer still disagree after two fix rounds.

## Domain warning

Several things in the client are named misleadingly and will be modelled
wrong by anyone reasoning from the names. A "flavor folder" is a directory
name that changes between beta and launch, not an identifier. The "interface
version" is a compatibility label derived from the patch number, not an API
level. A "secret value" is not encrypted; it is a number addon code may
display but not compute with. SavedVariables are written at logout or
`/reload`, so an offline tool always sees the previous session, and the
client overwrites external edits made while it runs. Gear and talent terms
(item level, bonus IDs, spec versus loadout) mislead the same way. Read
`docs/GLOSSARY.md` before designing anything that touches these.
