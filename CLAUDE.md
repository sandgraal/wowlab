@AGENTS.md

# Claude Code notes

The constitution above is tool-agnostic. This file adds what is specific to
Claude Code in this repository.

## Entry points

- `/conduct milestone M10` — the conductor loop (frontier, dispatch, route,
  merge) for the current wave. Also `/conduct next` or `/conduct M10-08 M10-09`.
- `/start-ticket M10-04`, `/ship` — what agents run at the start and end of a ticket.
- `/adr new "…"`, `/adr amend 0022 "…"` — decisions, always `Proposed`.

## Harness

- Agents: `.claude/agents/{implementer,test-writer,code-reviewer,domain-reviewer,security-reviewer,pr-shepherd}.md`.
  Dispatch with `isolation: "worktree"` and `run_in_background: true`.
- Rules: `.claude/rules/*.md` load by path (`paths:` frontmatter) when a
  matching file is opened.
- Hooks (`.claude/settings.json`, scripts in `.claude/hooks/`, tests in
  `tests/harness/`): `guard_bash.py` blocks `--no-verify`, bare force-push,
  pushes to main, `--admin`, destructive `rm`, and shell writes into harness
  files; `precommit_gate.py` runs `make lint` before any `git commit`;
  `protect_paths.py` keeps `LICENSE` read-only, confines subagents away from
  the harness and confines `code-reviewer` to new files under `tests/review/`;
  `format_python.py` keeps `.py` files ruff-clean; `session_start.py` prints
  the ticket frontier. Hooks run on the system `python3` (3.9+) and must stay
  stdlib-only.
- Worktrees live under `.claude/worktrees/` (gitignored).
  `.worktreeinclude` copies `.env` into each; never `.venv`.
- Personal overrides: `.claude/settings.local.json` (gitignored). Machine
  notes: `CLAUDE.local.md` (gitignored; see `docs/SETUP.md` for a template).

## Known hook gaps

The shell guard inspects redirects, `tee`, `sed -i`, `cp`, `mv`, and
`install`. Writes from inside `python -c`, a heredoc-fed interpreter, or an
editor are not inspected. The Edit/Write path is fully guarded; use it.

The guard also blocks `git rm` under `.claude/` for everyone, the conductor
included. Deleting a harness file is an owner action in a terminal.
