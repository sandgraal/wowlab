---
name: ship
description: Verify the current wowlab branch (lint, the tests the diff touches, make test-fast; the full make ci only for the files that need it), commit in the project format, push, and open or update a draft PR with the proof the reviewers need.
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
5. `gh pr create --draft --base main` (or push to update). Body: ticket id,
   each acceptance criterion with the command and one-line result that
   proved it, judgment calls flagged as claims for the reviewer, and the
   footers `session: <id>` and
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
6. Report the PR URL and `gh pr checks`. A draft PR waiting on review is the
   expected end state; `pr-shepherd` takes it from here.
