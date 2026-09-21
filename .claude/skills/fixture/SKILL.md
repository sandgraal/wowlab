---
name: fixture
description: Retired 2026-09-21 (ADR-0025); does nothing. Pending deletion by the repository owner. Fixtures enter through scripts/lab_capture.py (ticket M10-02).
disable-model-invocation: true
---

This skill is retired and has no steps. Do not act on it. Tell the user:
fixtures come from `scripts/lab_capture.py` per `docs/handoffs/M10-03.md`.

The shell guard blocks `git rm` under `.claude/` for every Claude session,
so the deletion is the owner's: `git rm -r .claude/skills/fixture`.
