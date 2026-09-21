---
paths:
  - ".retired/fixtures"
---

# Retired — delete this file

Emptied on 2026-09-21 (ADR-0025). It matches no path and carries no rule;
the fixture rules are in `.claude/rules/lab-fixtures.md`. The shell guard
blocks `git rm` under `.claude/` for every Claude session, so the deletion
is the owner's: `git rm .claude/rules/fixtures.md`.
