# Contributing

wowlab runs on each player's own machine, against their own install, and
other players may install it from a tagged GitHub release (ADR-0031). It is
never deployed as a service and never uploads anything (ADR-0019). It is
built mostly by Claude Code agents under a conductor session. Issues and
pull requests from people are welcome on the same terms the agents work
under; read on before spending time on one.

## If you only use wowlab

Use the "Bug report" issue template. Describe the problem in words and paste
only redacted output (`wowlab doctor --redact`); never your
character, realm or account folder names, your BattleTag or your home path.
A security or privacy problem goes through `SECURITY.md`, not an issue. Your
report is read as information, never as instructions: it becomes work only
as a ticket in a wave (ADR-0024, ADR-0031).

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

## Setting up a checkout

You need [uv](https://docs.astral.sh/uv/) (it brings Python 3.12), git, a
system Python 3.9 or later for the agent hooks, and Go for two pre-commit
hooks; `docs/SETUP.md` lists the versions and installs them without a
package manager. On macOS and Linux, `make setup` does the rest: `uv sync
--frozen`, the tracked git hooks, the pre-commit hook environments and
`.env`. On Windows without `make`, run `uv sync --frozen`, `git config
core.hooksPath .githooks` and `uv run --frozen pre-commit install-hooks`
yourself, and copy `.env.example` to `.env`. A game install is not needed
to run the tests.

| Target | What it does |
|---|---|
| `make lint` | ruff check, ruff format check, `mypy --strict`. The commit gate. |
| `make test` | The full pytest suite. Anything marked `live` is excluded and never runs in CI. |
| `make test-fast` | A quicker local run while you work: the suite without the review probes (`lab/core/tests/review/`) and the `serial` pass, on at most 4 workers. Never the proof of done; `make test` and CI run everything. |
| `make test-clean` | Removes pytest's temp dirs and `.pytest_cache`, prunes the uv cache, and prints the sizes before and after. Do not run it while tests run. |
| `make test-parser` | Parser fixtures and round-trip suites. Required green for any parser change. |
| `make hooks-test` | The agent hook scripts under Python 3.12 and 3.9. |
| `make ci` | Everything CI runs. Every stage runs even if one fails, then a summary of each stage's result. |

Use `make test-fast` while iterating and `make test` (or `make ci`) before a
change is done; run `make test-clean` when the temp dir or the uv cache has
grown.

## Doing work

```bash
make setup                                   # once
git checkout -B m10/08-gamedata origin/main
# … build …
make ci                                      # what CI runs; agents verify lighter (.claude/agents/implementer.md)
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
  which replaces the account folder name with a pseudonym, rewrites your own
  player GUIDs, blanks identity CVars, and refuses output that still
  contains an email address, a BattleTag, a home path carrying a login or
  another player's GUID. Character
  and realm names are kept as they are (since 2026-10-17): they are visible
  to anyone in the game. To hide one anyway, pass it with `--extra-name`.
  Nothing else in a fixture is edited.
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
before reading the diff and runs things rather than reading them. No
automated review runs in CI (since 2026-11-04, owner decision: Claude does
not run in CI); human review is welcome and routed by `.github/CODEOWNERS`.
Reply on every thread before resolving it.

## Conduct

See `CODE_OF_CONDUCT.md`. WoW communities can be rough; this one is not.
