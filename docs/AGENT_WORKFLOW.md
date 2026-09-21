# Agent workflow

How work moves through this repository. `AGENTS.md` is the constitution;
this document is the operating manual. It is written so that another
project can copy the setup — see the last section.

## Roles

| Role | Lives in | Writes | Reads first |
|---|---|---|---|
| **Owner** (human) | — | ADR status, wave picks, real fixtures from the install | everything |
| **Conductor** (main Claude session) | `/conduct` skill | harness, docs, backlog ticks, handoffs, wave reviews | backlog frontier |
| **implementer** | `.claude/agents/implementer.md` | one ticket, own worktree | ticket, `docs/LAB_PLAN.md` sections it cites, rules |
| **test-writer** | `.claude/agents/test-writer.md` | graders before the implementation exists | `docs/LAB_PLAN.md`, `docs/LAB_FORMATS.md`, fixtures |
| **code-reviewer** | `.claude/agents/code-reviewer.md` | probe tests under `lab/core/tests/review/` only | invariants L1–L8, then diff |
| **domain-reviewer** | `.claude/agents/domain-reviewer.md` | nothing | glossary, file map, format reference, then diff |
| **security-reviewer** | `.claude/agents/security-reviewer.md` | nothing | ADR-0021, ADR-0023, workflows, then diff |
| **pr-shepherd** | `.claude/agents/pr-shepherd.md` | PR text, thread replies, comment-level fixes, rebases, lock regeneration | PR state, checks, threads |
| **Automated review** | `.github/workflows/claude-review.yml` | PR comments | `AGENTS.md`, diff |

Every writer is graded by someone who did not write the thing. For the two
load-bearing files, `lab/core/src/wowlab_core/luadata.py` and
`lab/core/src/wowlab_core/guard.py`, that separation is enforced at the
ticket level: a `[TEST]` ticket lands graders on `main` as
`pytest.mark.xfail(strict=True)` before the `[IMPL]` ticket starts.

## Ticket lifecycle

```
docs/BACKLOG.md  ──frontier──▶  conductor dispatches (parallel, worktrees)
                                       │
                 ┌─────────────────────┼──────────────────────┐
                 ▼                     ▼                      ▼
          test-writer            implementer            implementer
          (load-bearing)         (ticket B)             (ticket C)
                 │                     │                      │
            push + report         push + report          push + report
                 │                     │                      │
                 ▼                     ▼                      ▼
     code-reviewer (+ domain-reviewer / security-reviewer by area)
                 │  findings → same implementer, ≤2 rounds
                 ▼
            pr-shepherd: draft PR → CI → threads → ready → merge (squash)
                 │
                 ▼
     conductor: verify on main, remove worktree, recompute frontier,
                dispatch newly unblocked tickets in the same turn,
                batch backlog ticks into one docs(backlog) PR per session
```

**Frontier** = tickets whose heading is `## [ ] …`, that no merged PR title
names in parentheses, and whose `Depends on` list is entirely done. The
SessionStart hook prints it; `/conduct` recomputes it after every merge.

**Waves** (ADR-0024). The backlog holds one wave, which is one milestone.
When the wave's review ticket is the only one left, the conductor writes
`docs/handoffs/M<n>-review.md` and stops dispatching. The owner picks the
next wave from `docs/LAB_IDEAS.md`; the conductor lands the plan section,
ADRs and tickets in one docs PR; dispatch resumes when it merges.

## Conventions

- **Branch:** `m<milestone>/<nn>-<slug>`; graders: `…-tests`.
- **Commit:** `type(scope): summary (M10-04)` — `feat`, `fix`, `test`, `docs`, `chore`, `ci`, `refactor`.
- **PR title:** same as the squash commit. The ticket id in parentheses is
  how the frontier knows the ticket is done, so it is not optional.
- **PR body:** the template. Proof per acceptance criterion means the
  command and its one-line result, not a checkbox.
- **PR footer:** `session: <id>` identifies the session that owns the PR.
  Another session adopting it posts an ownership comment first, and never
  `--force-with-lease`s over commits it has not fetched and read.
- **Reports:** every agent ends with the structured block its definition
  specifies. The conductor routes on those fields, not on prose.

## Definition of done

CI green (all required checks); `make ci` output pasted; every acceptance
criterion has proof; new external-format parsing has a real, scrubbed
fixture with a provenance row; any change to a load-bearing file has
`make test-parser` and the full suite green and graders activated by marker
deletion only; nothing writes into an install outside `guard`, and no test
needs or touches a real install; anything contradicting an ADR ships with a
superseding ADR (`Proposed`) rather than a silent deviation; review threads
all replied to and resolved.

## Stop-and-ask

The `AGENTS.md` list, plus: reviewer/implementer deadlock after two rounds,
a grader on `main` that looks wrong, a required check failing for
infrastructure reasons after one rerun, and any ADR status change. The
conductor surfaces each as one paragraph with the two options it would
choose between, in chat and as a `⛔ Blocked:` PR comment, and keeps every
other ticket moving.

## Worktree hygiene

- Agents run in `.claude/worktrees/<name>/`. `.worktreeinclude` copies `.env`
  only. Never copy `.venv` (absolute paths inside); run `make setup`.
- Nothing in the toolchain binds a port or shares state between checkouts;
  the snapshot store and game-table cache live in the user data directory,
  and tests redirect them to `tmp_path`.
- `uv.lock` conflicts are never hand-merged: take `origin/main`'s copy and
  run `uv lock`.
- Pytest, ruff and mypy exclude `.claude/worktrees` so the main checkout
  never lints or tests an agent's tree.
- Git hooks are tracked in `.githooks/` and wired with `core.hooksPath`
  (by `make setup`). Never run `pre-commit install`: it rewrites the shared
  `.git/hooks/pre-commit` to point at *one* worktree's `.venv`, and removing
  that worktree breaks every commit in every checkout — this happened.
- Remove a worktree after its PR merges (`git worktree remove <path>`).

## Guardrails and their limits

Hooks in `.claude/hooks/` (tests in `tests/harness/`, run on Python 3.9
and 3.12 because hooks execute on the system interpreter):

| Hook | Event | Enforces |
|---|---|---|
| `guard_bash.py` | PreToolUse Bash | no `--no-verify`, no bare force-push, no push to main, no `--admin`, no `rm -rf` outside scratch, no shell writes or deletes touching the harness, ADRs, the backlog or LICENSE |
| `precommit_gate.py` | PreToolUse Bash (`git commit`) | `make lint` passes in the committing worktree |
| `protect_paths.py` | PreToolUse Edit/Write | LICENSE read-only; subagents cannot edit harness, ADRs, backlog; `code-reviewer` confined to new files under `tests/review/` (in practice `lab/core/tests/review/`) |
| `format_python.py` | PostToolUse Edit/Write | ruff format + fix on `.py` |
| `session_start.py` | SessionStart | prints branch state and the frontier |

These hooks guard the repository. They are unrelated to `wowlab_core.guard`,
which guards the game install at run time.

What the shell guard does see: redirects in every spelling, `tee`, `sed`
in place, copy/move/link/rsync/dd/patch targets including directory
destinations, `rm` and `git rm`, `git checkout`/`git restore` pathspecs,
`cd` inside the same command, `bash -c` and `eval` strings, `$CLAUDE_PROJECT_DIR`
and `$PWD` indirection, symlinks, and case-only renames. Anything it cannot
tokenise or resolve is denied, not skipped. One consequence: nobody, the
conductor included, can delete a harness file from a Claude Code shell;
that is an owner action in a terminal.

Limits: writes made from inside `python -c`, a heredoc-fed interpreter, or
an editor are invisible to it, and so is anything a pre-approved tool does
internally (`uv sync` running a build hook). That is why `uv run python:*`
and `uv add:*` are not pre-approved, why the Edit/Write path is the one that
is fully guarded, and why CI (`harness`, `gitleaks`, `semgrep`, `trivy`) and
the branch ruleset are the backstop. The hooks exist to fail earlier and
explain why.

## How humans participate

Open issues with the ticket template; the conductor folds them into the
backlog if the owner wants them in the current wave. Review any PR;
reply-then-resolve applies to you too. Mention `@claude` in an issue or PR
comment to get an answer with the repo's context loaded. Real fixtures come
from the owner's install through `scripts/lab_capture.py`
(`docs/handoffs/M10-03.md`). Accept or reject ADRs by editing their status
in a PR you merge yourself.

## Reusing this harness in another project

1. Copy `.claude/` (agents, skills, rules, hooks, settings), `tests/harness/`,
   `.github/` (workflows, templates, ruleset, bootstrap script),
   `.worktreeinclude`, `.pre-commit-config.yaml`, and the `Makefile` targets
   `lint`/`test`/`ci`.
2. Write your own `AGENTS.md`: what the thing is, hard invariants, where
   truth lives, conventions, verification commands, anti-patterns, stop-and-
   ask. Keep it under 200 lines; put machine notes in `CLAUDE.local.md`.
3. Replace domain content: the rules' `paths:`, the reviewer checklists,
   the spec the agents read (`docs/LAB_PLAN.md` here), the fixture policy,
   the load-bearing file list, the protected paths in
   `.claude/hooks/_common.py`.
4. Write a backlog in the `## [ ] ID — Title` / `**Depends on:**` format;
   the frontier logic and SessionStart hook work unchanged.
5. Run `scripts/bootstrap-github.sh` (edit `REPO` and the required check
   names in `.github/rulesets/main.json`), add `ANTHROPIC_API_KEY`, and start
   with `/conduct next`.
