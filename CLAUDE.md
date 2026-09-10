@AGENTS.md

# Claude Code notes

The constitution above is tool-agnostic. This file adds what is specific to
Claude Code in this repository.

## Entry points

- `/conduct next` — the conductor loop (frontier, dispatch, route, merge).
  Also `/conduct M1-02 M1-05` or `/conduct milestone M1`.
- `/start-ticket M1-02`, `/ship` — what agents run at the start and end of a ticket.
- `/adr new "…"`, `/adr amend 0005 "…"` — decisions, always `Proposed`.
- `/fixture <file> …` — the only way a `/simc` export enters the corpus.
- `/migration "…"` — Alembic revision with the up/down/up proof.
- `/patch-day 12.2.0` — the maintenance runbook when WoW or SimC releases.

## Harness

- Agents: `.claude/agents/{implementer,test-writer,code-reviewer,domain-reviewer,security-reviewer,pr-shepherd}.md`.
  Dispatch with `isolation: "worktree"` and `run_in_background: true`.
- Rules: `.claude/rules/*.md` load by path (`paths:` frontmatter) when a
  matching file is opened.
- Hooks (`.claude/settings.json`, scripts in `.claude/hooks/`, tests in
  `tests/harness/`): `guard_bash.py` blocks `--no-verify`, bare force-push,
  pushes to main, `--admin`, destructive `rm`, and shell writes into harness
  files; `precommit_gate.py` runs `make lint` before any `git commit`;
  `protect_paths.py` keeps merged migrations and `LICENSE` read-only and
  confines subagents away from the harness; `format_python.py` keeps `.py`
  files ruff-clean; `session_start.py` prints the ticket frontier.
  Hooks run on the system `python3` (3.9+) and must stay stdlib-only.
- Worktrees live under `.claude/worktrees/` (gitignored).
  `.worktreeinclude` copies `.env` into each; never `.venv`. The Makefile
  derives a compose project name and port block per worktree (`make env`).
- Personal overrides: `.claude/settings.local.json` (gitignored). Machine
  notes: `CLAUDE.local.md` (gitignored; see `docs/SETUP.md` for a template).

## Known hook gaps

The shell guard inspects redirects, `tee`, `sed -i`, `cp`, `mv`, and
`install`. Writes from inside `python -c`, a heredoc-fed interpreter, or an
editor are not inspected. The Edit/Write path is fully guarded; use it.
