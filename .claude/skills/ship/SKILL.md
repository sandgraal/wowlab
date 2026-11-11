---
name: ship
description: Verify the current wowlab branch (lint, the tests the diff touches, make test-fast; the full make ci only for the files that need it), commit in the project format, push, and write the draft PR's body with the proof the reviewers need.
---

Ship the current branch.

1. Verify exactly as `.claude/agents/implementer.md` ("Verify before
   reporting") says: its lists decide what runs, from `make lint` and the
   tests the diff touches up to the full `make ci` for the files it names.
   Show the real tail of each output. The PR's CI is the proof of the full
   suite. Any failure stops here; report it instead of shipping.
2. Run the ticket's own acceptance command(s) (from `docs/BACKLOG.md`) and
   keep the output for the PR body.
3. Commit as `type(scope): summary (M10-04)`. The lint gate and gitleaks run
   as hooks; never bypass them.
4. `git push -u origin <branch>` then `git ls-remote origin <branch>` to
   confirm the sha landed.
5. Write the PR body to a file in the scratchpad named for the ticket
   (`m16-95-body.md`, never `body.md`: the scratchpad is shared, see
   `pr-shepherd.md`) and give its absolute path in your report's
   `pr body:` line. Body: ticket id, each acceptance criterion with the command
   and one-line result that proved it, judgment calls flagged as claims for
   the reviewer, and the footers `session: <id>` and
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
   Subagents do not open the PR: `gh pr create` is refused in the cloud
   sessions and the REST call is hook-blocked. The conductor or
   `pr-shepherd` opens it as a draft from that body. A session that holds
   the GitHub MCP tools may open it itself.
6. Report the pushed sha and the body's path. A pushed branch waiting on its
   draft PR and review is the expected end state.
