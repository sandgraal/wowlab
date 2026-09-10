# Contributing

Bronze is built mostly by Claude Code agents under a conductor session, and
humans are welcome on the same terms. Everything below applies to both.

## Before anything

Read `AGENTS.md` (the constitution), then `docs/AGENT_WORKFLOW.md`. Domain
newcomers: `docs/GLOSSARY.md` before designing anything.

## Picking work

Work is tracked in `docs/BACKLOG.md` as agent-sized tickets with explicit
dependencies and verifiable acceptance criteria. Pick a ticket whose
`Depends on` list is done. If you want to propose new work, open an issue
with the "Agent-sized ticket" template; the conductor folds accepted tickets
into the backlog.

Two kinds of work need a human specifically:

- **Real fixtures.** The parser corpus takes real `/simc` exports only. If
  you play, your exports (with consent recorded, identity optionally
  rewritten) are the most valuable contribution you can make. Use
  `/fixture` or follow `api/tests/fixtures/simc/README.md`.
- **Credentials.** Blizzard and Warcraft Logs client credentials are never
  committed; tests replay recorded fixtures.

## Doing work

```bash
make setup                                   # once
git checkout -B m1/02-simc-parser origin/main
# … build …
make ci                                      # must be green
git commit -m "feat(parser): parse slot sub-attributes as an open set (M1-02)"
gh pr create --draft --base main             # template asks for proof per criterion
```

- Load-bearing files (`simc_parser.py`, `profile_builder.py`,
  `talent_codec.py`, `gap_analysis.py`) and migrations follow the
  test-first split: graders land first as `xfail(strict=True)`, written by
  someone other than the implementer.
- Never `--no-verify`, never bare force-push, never edit a merged migration
  or a committed fixture. Hooks and CI enforce these; do not route around them.
- Decisions that contradict an ADR ship as a superseding ADR (`/adr`), status
  `Proposed`, for the owner to accept.

## Review

Every PR gets an independent review that derives expectations from the spec
before reading the diff and runs things rather than reading them. An
automated Claude review comments on open PRs; human review is welcome and
routed by `.github/CODEOWNERS`. Reply on every thread before resolving it.

## Conduct

See `CODE_OF_CONDUCT.md`. WoW communities can be rough; this one is not.
