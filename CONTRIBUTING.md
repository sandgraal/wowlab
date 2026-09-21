# Contributing

wowlab is a personal tool: it is built for one player's machine and is
never distributed (ADR-0019). The repository is public so the work is
visible and the harness reusable, not because it is looking for users. It is
built mostly by Claude Code agents under a conductor session. Issues and
pull requests from people are welcome on the same terms the agents work
under; read on before spending time on one.

## Before anything

Read `AGENTS.md` (the constitution), then `docs/AGENT_WORKFLOW.md`. Domain
newcomers: `docs/GLOSSARY.md` before designing anything.

## What gets built, and when

Work is tracked in `docs/BACKLOG.md` as agent-sized tickets with explicit
dependencies and verifiable acceptance criteria. The backlog holds **one
wave at a time**, and the owner picks each wave (ADR-0024). An idea that is
not a ticket does not get built, however good it is: propose it with the
"Agent-sized ticket" issue template, or as a row for `docs/LAB_IDEAS.md`, and
it is considered at the next wave review.

Anything past the scope boundary in ADR-0023 (process memory, injection,
packets, input automation, editing `Data/` or executables) is declined
without discussion. That is not a judgment of the idea; it does not belong
in this repository.

## Doing work

```bash
make setup                                   # once
git checkout -B m10/08-gamedata origin/main
# … build …
make ci                                      # must be green
git commit -m "feat(gamedata): cache tables by build, never overwrite (M10-08)"
gh pr create --draft --base main             # template asks for proof per criterion
```

- Load-bearing files (`lab/core/src/wowlab_core/luadata.py`,
  `lab/core/src/wowlab_core/guard.py`) follow the test-first split: graders
  land first as `xfail(strict=True)`, written by someone other than the
  implementer.
- Never `--no-verify`, never bare force-push, never edit a committed
  fixture. Hooks and CI enforce these; do not route around them.
- Decisions that contradict an ADR ship as a superseding ADR (`/adr`), status
  `Proposed`, for the owner to accept.

## Fixtures and your privacy

Parsers are graded on **real** files from a real install, and this
repository is public. Two rules follow.

- Fixtures enter only through `scripts/lab_capture.py` (ticket M10-02),
  which replaces account folder, character and realm names with pseudonyms,
  blanks identity CVars, and refuses output that still contains an email
  address, a BattleTag or a player GUID. Nothing else in a fixture is edited.
  Rules and index: `lab/core/tests/fixtures/README.md`.
- The corpus is the owner's install. If you want to offer a file that shows a
  shape the corpus lacks, run the tool yourself, read its output before you
  send it, and say so in the PR; it is recorded with consent `explicit`.
  Never attach a raw `WTF/` folder or SavedVariables file to an issue: they
  carry your account name and your characters.

Found an identifier in a committed fixture? That is a privacy leak, not a
bug report. Follow `SECURITY.md`.

## Review

Every PR gets an independent review that derives expectations from the spec
before reading the diff and runs things rather than reading them. An
automated Claude review comments on open PRs; human review is welcome and
routed by `.github/CODEOWNERS`. Reply on every thread before resolving it.

## Conduct

See `CODE_OF_CONDUCT.md`. WoW communities can be rough; this one is not.
