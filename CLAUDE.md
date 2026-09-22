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

## Models

Opus-class work runs on Opus 5.5, pinned by full id: the conductor session
(`.claude/settings.json`), `implementer`, `test-writer`, `code-reviewer`,
`domain-reviewer`, `security-reviewer`, and both GitHub workflows. Other
Opus versions and Fable cost more tokens for no gain here and are not used.
`pr-shepherd` stays on `sonnet`.

Context window: Anthropic's pricing has no per-token premium for the 1M
window, but every turn resends the whole context. Caching makes that cheaper
per token, but it is still billed on every turn. A session allowed to grow
toward 1M pays roughly in proportion to its size on each turn after. Choose
by how long a session must hold state:

- **`claude-opus-5-5[1m]`: the conductor only.** It runs a whole wave:
  frontier, parallel dispatches, agent reports, review routing, merges.
  Compaction mid-wave loses routing state, and that costs more than the
  larger context.
- **`claude-opus-5-5` (standard): the repo's agents and both workflows.** Each
  is scoped to one ticket, review or PR. All of `docs/` is under 40K tokens,
  and each review on #34 used about 30K, so the standard window fits, and
  compacting early keeps later turns cheap. The conductor dispatches only
  the repo agents under `.claude/agents/`. Built-in types such as
  `general-purpose` have no pin and would likely inherit the 1M session model.
- **Do not read a fixture whole into context.** Grep it or read the slice
  you need. The corpus under `lab/core/tests/fixtures/` is already larger
  than all of `docs/`.

If an agent keeps compacting mid-ticket and losing context, that is the
signal to split the ticket, not to give the agent the 1M window.
`scripts/check_frontmatter.py` accepts only `inherit`, `sonnet`, `haiku` and
`claude-opus-5-5` in agent frontmatter, so it rejects the bare `opus` alias,
`fable` and the `[1m]` variant. Do not pass a `model` override to `Agent`;
the frontmatter decides.

## Known hook gaps

The shell guard inspects redirects, `tee`, `sed -i`, `cp`, `mv`, and
`install`. Writes from inside `python -c`, a heredoc-fed interpreter, or an
editor are not inspected. The Edit/Write path is fully guarded; use it.

The guard also blocks `git rm` under `.claude/` for everyone, the conductor
included. Deleting a harness file is an owner action in a terminal.
