---
name: migration
description: Retired 2026-09-21 (ADR-0025); does nothing. Pending deletion by the repository owner. This repository has no database.
disable-model-invocation: true
---

This skill is retired and has no steps. Do not act on it.

The shell guard blocks `git rm` under `.claude/` for every Claude session,
so the deletion is the owner's: `git rm -r .claude/skills/migration`.
