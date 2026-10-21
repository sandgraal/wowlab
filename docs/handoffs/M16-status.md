# M16 status: before the first dispatch

Written by the conductor on 2026-10-21, for the session that runs
`/conduct next` when work resumes. No M16 ticket has been dispatched yet.
Read this first, then `docs/LAB_PLAN.md` §18 and the M16 section of
`docs/BACKLOG.md`. Delete this file once M16's first batch has merged.

## Where things stand

`main` is at 05ea27d. Two merges on 2026-10-21 got it there.

**#322** brought in the outside evaluation and its follow-on:
- the evaluation itself: `docs/handoffs/outside-evaluation-2026-10.md`, with ten seat reports beside it;
- the owner's answers to its questions, and the M16 wave;
- `.claude/rules/player-copy.md`;
- the accepted 2026-10-21 amendments to ADR-0013, ADR-0026, ADR-0031 and ADR-0032.

**#323** bumped certifi to 2026.10.19 for GHSA-6q48-6c7w-9gw2. Trivy fails on any branch cut before cd6f2c5 until it merges `main`.

The frontier on `main`: 35 M16 tickets are ready. M16-01 is done (the owner accepted all the amendments). M15-05 is ready, and M15-06 to M15-09 are blocked on M16-47. M13-76 is blocked on M16-02, which absorbs it.

## Open PRs to settle before or beside the first batch

| PR | What | Action |
|---|---|---|
| #320 | M15-05 `casc` encoding/root, 5 behind `main` | Finish it (§17.9: Wave 6 pauses *after* M15-05). Merge `main` in, re-run the reviews per its ticket, merge. |
| #321 | Wiki job's README one-liner, 2 behind | Check it is still true on `main`; merge or close. The next daily run replaces it if closed. |
| #310, #311 | Dependabot groups, 12 behind; #311 rewrites `uv.lock` | `@dependabot rebase` (certifi changed `uv.lock`), then review as usual. |

## First batch

Dispatch everything ready, with these exceptions.

**Serial on `cli.py`.** Run M16-14, M16-15, M16-16, M16-18, M16-19 and M16-22 one at a time. Start with M16-15, because M16-16 depends on it.

**Load-bearing twins first.**
- M16-03T, M16-09T, M16-11T, M16-23T and M16-26T go to `test-writer`.
- M16-39T is graders only.
- Each `[IMPL]` twin waits for its `[TEST]` twin to merge.

**Do the cheap trust fix early.** Dispatch M16-02 in the first batch. Seven panel seats found the problem, and M13-76 closes with it.

**M16-45 waits for `domain-reviewer`.** It shows as ready, but its "Before dispatch" note asks `domain-reviewer` to check the `CLICK` premise first: GSE-style buttons may carry text the player typed. Run that check as a short dispatch and act on the answer before the implementer starts.

**Owner tickets are not agent work.**
- M16-32 (Discussions, the announcement, PyPI) needs owner steps.
- M16-38 (the `@live` L1 sweep) is run by the owner.

**M16-05 waits on the launch.** It depends on M16-45, M16-46 and the launch date, which the owner has not confirmed. 2026-11-04 came from the panel's web search and has not been checked against a second source. Ask the owner.

## Known environment notes

- In a cloud container running as root, `tests/repo/test_wiki_daily_outside.py::…[read-only-as-user-fails]` fails. That failure is environmental and does not happen in CI. M13-81 should skip it under uid 0.
- `make ci` stops at `lint-lua` where selene is not installed. Run `make selene` first, or rely on CI.
- Until M16-07 lands, `wowlab doctor` contacts wago.tools. Agents testing by hand pass `--offline` (ADR-0012).
- The panel's sandbox kit is reusable for hands-on checks: `docs/handoffs/outside-evaluation-2026-10/_make_sandbox.sh` and `_brief.md`. The script builds an install from the real fixtures, with an isolated `HOME` and `XDG_*`. Its scratch path in the brief must be replaced.
- The shell hook blocks a redirect to a path held in a variable (`> "$F"`). Use a literal path, a `python3 -c` write, or the Write tool.

## Owner actions outstanding

- Confirm the Forever launch date (M16-05).
- M16-32: enable Discussions and set up PyPI trusted publishing.
- Delete `.claude/hooks/precommit_gate.py` with `git rm` from a terminal. It has been unregistered since 2026-10-17, and the shell guard blocks agents from removing it.
- The remote branch `claude/vibrant-albattani-hoh9lm` is fully merged (0 ahead of `main`). This session's delete was refused, so remove it from the GitHub UI.
