---
name: start-ticket
description: Load a Bronze backlog ticket's context, verify its prerequisites and the environment, branch from main, and restate the acceptance gate before any code is written. Agents run this themselves at the start of a ticket.
argument-hint: M1-02
---

Start work on ticket `$ARGUMENTS`.

1. Find the ticket in `docs/BACKLOG.md`. Confirm every ticket in its
   `Depends on` list is ticked or merged
   (`gh pr list --state merged --search "(<id>)"`). If not, stop and report
   which prerequisite is open.
2. Read the `docs/IMPLEMENTATION_PLAN.md` sections the ticket cites, the
   relevant ADRs in `docs/DECISIONS.md`, `AGENTS.md`, the `.claude/rules/`
   file for the area, and `docs/handoffs/$ARGUMENTS.md` if it exists.
3. If the ticket touches a load-bearing file or a migration, confirm which
   side of the test-writer / implementer separation this session is on,
   and that the graders are already on `main` (for an [IMPL] ticket).
4. Verify the environment: `make lint` passes on a clean checkout (see
   `docs/SETUP.md` if not). For database tickets, `make up` and `make env`.
5. Create the branch from up-to-date main:
   `git fetch origin && git checkout -B m<N>/<nn>-<short-slug> origin/main`.
6. Before implementing, state in one short list: the acceptance criteria
   (verbatim), the commands that will prove each, and what is explicitly
   out of scope.
