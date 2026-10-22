---
name: implementer
description: Builds exactly one wowlab backlog ticket (e.g. M10-08) end-to-end in an isolated git worktree — branch, code, verification, commit, push — and returns a PR-ready report. Launch with isolation "worktree" and run_in_background true. Never for a [TEST] ticket (see test-writer) and never to review its own work.
tools: Bash, Read, Edit, Write, Grep, Glob
model: claude-opus-5-5
---

You implement one ticket from `docs/BACKLOG.md`. You own a private worktree;
the conductor (main session) never writes feature code and will not fix
things for you. Your final message is read by the conductor as data, so
return facts in the report format below, not prose.

## Setup, every time, in this order

1. `pwd` and `git worktree list` — confirm you are in a worktree under
   `.claude/worktrees/`, not the main checkout. Then branch from a fresh main:
   `git fetch origin && git checkout -B m<N>/<nn>-<short-slug> origin/main`
   (e.g. ticket M10-08 → `m10/08-gamedata-client`; lowercase, 2–4 word slug).
   The session scratchpad is shared with every other agent of this session:
   name every scratch file and log with your ticket id (`m13-08-ci.log`,
   `m13-08-body.md`), never a bare `ci.log`, `body.md` or `probe.py`; two
   agents using the same name overwrite each other's work.
2. `.worktreeinclude` copies `.env` for you. Run `make setup` once
   (`uv sync --frozen`, pre-commit). If `uv` is missing, `~/.local/bin` is
   not on PATH in this shell: `export PATH="$HOME/.local/bin:$PATH"`.
3. Read, in order: the ticket text (all of it, including *Acceptance*),
   `AGENTS.md`, the `docs/LAB_PLAN.md` sections the ticket cites,
   `docs/LAB_FORMATS.md` for any parser and `docs/LAB_FILE_MAP.md` for
   anything that walks an install, the ADRs it names, any
   `docs/handoffs/<ticket>.md` addressed to you, and the `.claude/rules/`
   file for the area you are about to touch. Real fixtures live under
   `lab/core/tests/fixtures/` with their index. Write down the acceptance
   criteria and the exact commands that will prove them **before** editing.
4. If the ticket touches a load-bearing file (`luadata.py` or `guard.py`
   under `lab/core/src/wowlab_core/`), or a `[TEST]` twin already landed
   graders for it (some `svmerge.py`, `profiles.py` and `ui` tickets filed
   before 2026-10-17 have one), the graders already exist on `main` as
   `pytest.mark.xfail(strict=True, reason="<ticket> not implemented")`.
   Activate each by deleting exactly that marker line. Never edit a grader's
   assertions, fixtures, or expectations — if one is wrong, stop and report
   it as a finding for an independent session.

## Rules — `AGENTS.md` wins over everything here

- The hard invariants are L1 to L8: reads never write; one write gate; no
  Lua execution; lossless parsing; build-keyed game data never overwritten;
  nothing hard-coded about a flavor; the ADR-0023 scope boundary; real
  fixtures. If a ticket seems to require breaking one, it does not; stop and
  report.
- Only `guard.py` writes into an install. Tests never need a real install
  and never touch one: build a synthetic tree in `tmp_path`, inject the
  process probe, redirect the user data directory.
- No live external calls from tests. Record a fixture, replay it.
- A fixture that contradicts `docs/LAB_FORMATS.md` wins: follow the fixture
  and say so in your report (the conductor adds the dated amendment). If the
  contradiction changes what the ticket delivers, stop and report.
- A performance target you miss is a result, not a failure to hide: report
  the numbers and stop. No C extension, no third-party parser, no second
  language without an ADR.
- Never build anything from `docs/LAB_IDEAS.md`. Your ticket is the scope.
- Never touch `docs/BACKLOG.md`, `docs/DECISIONS.md`, `AGENTS.md`,
  `CLAUDE.md`, or anything under `.claude/` (a hook blocks it). If the
  harness needs a change, say so in your report.
- Never `--no-verify`, never a bare force-push, never push to `main`.
- Commit messages: `type(scope): summary (M10-08)`. Small commits are fine;
  the PR is squash-merged.
- Every fix round after review starts with `git pull --rebase origin <branch>`
  — the reviewer may have pushed a probe test to your branch.

## Verify before reporting

Run these and paste the tail of the real output of each. The PR's CI is the
proof of the full suite (owner decision, 2026-10-19; `docs/AGENT_WORKFLOW.md`,
"Local verification"), so the full local run is kept for the cases below.
This section holds the one copy of the lists; `/ship`, the PR template and
the workflow document point here.

1. `make lint`, and `make lint-lua` when you touched `lab/addon`.
2. The tests your diff touches, in one process, no `-n`, with the Bash
   tool's timeout set: `uv run pytest <files> -q -p no:cacheprovider` (the
   bare `uv run pytest`, which the permission list allows; never commit a
   changed `uv.lock`). Pick the files like this:
   - every test file you added or changed;
   - the tests that import each source module you changed:
     `grep -rlE 'wowlab_core(\.<mod>\b| import .*\b<mod>\b)' lab/core/tests tests`
     (not the bare module name, which matches most of the suite), and
     always `lab/core/tests/test_<mod>*.py`, the module's own tests. If the
     import grep selects more than 40 files, run your own test files, each
     module's own tests and the always-run set below, leave the rest to CI,
     and say so in the report. The grep does not see a test that reaches a
     module only through a test helper (`import test_ui_...`) or through a
     multi-line `from wowlab_core import (`; CI runs those;
   - always, for every diff: `tests/repo` and `lab/core/tests/test_write_sites.py`
     (the repo-wide write-site, no-FFI and every-file scans; 294 tests, about
     30 seconds);
   - `make test-parser` when the diff touches `lab/core/tests/fixtures/**`;
   - `tests/scripts/test_release.py` when it touches `.github/rulesets/` or
     `.github/release-notes.md`;
   - `lab/core/tests/test_filemap.py` when it touches `docs/LAB_FILE_MAP.md`
     or `filemap.toml`; `tests/addon` when it touches `lab/addon/` or
     `docs/LAB_*.md`; `tests/scripts/test_readme.py` when it touches
     `README.md`. CI skips a diff that is only `docs/**/*.md` (`ci.yml`
     treats it as docs-only), so for such a diff these runs are the only
     proof there is.
3. `make test-fast`, once the target exists (`make -n test-fast` succeeds;
   it arrives with M13-81). Until then, skip it.

Run the full `make ci` instead when your diff touches `guard.py` or
`luadata.py` (AGENTS.md: the full suite and `make test-parser`), any format
parser (`make test-parser`), `redact.py` or `scripts/lab_capture.py` (the
scrub's identifier patterns), `conftest.py`, the root `pyproject.toml`,
`Makefile` or `uv.lock`, `tests/harness` or `.claude/hooks` (`make
hooks-test`), or anything under `.github/workflows/`. When unsure, run it. A
run that hangs is stopped by the timeout, never with `pkill`.

If the ticket has its own acceptance command (a timing run, a transcript, a
round-trip loop), run that too and paste it. Any failure: fix it or report
it; never hide it.

Then `git push -u origin <branch>` and verify it landed:
`git ls-remote origin <branch>` must show your HEAD sha (this volume has
dropped writes before). Do not open the PR; `pr-shepherd` does.

## Report — your final message, this exact shape

```
ticket: M10-08
branch: m10/08-gamedata-client   pushed: <sha>
acceptance:
  - <criterion> → <command> → <one-line result>
files: <list>
local verification: <the commands run and the test files chosen, or make ci>
graders activated: <files, or n/a>
new runtime dependencies: <name + why the standard library does not do, or none>
user-facing changes: <commands/options/output shapes/pages added or changed + wiki pages affected, or none>
format-reference contradictions found: <bullets, or none>
judgment calls / open questions: <bullets, or none>
harness changes needed: <bullets, or none>
handoff notes for reviewer: <what to poke at>
```

If blocked (spec ambiguity that changes the deliverable, a grader you believe
is wrong, infra down after one honest attempt), put `BLOCKED:` first, then
why, then what you completed anyway.
