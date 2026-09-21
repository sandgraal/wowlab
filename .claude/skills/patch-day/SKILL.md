---
name: patch-day
description: Retired 2026-09-21 (ADR-0025); does nothing. Pending deletion by the repository owner.
disable-model-invocation: true
---

This skill is retired and has no steps. Do not act on it. A client patch is
handled by a fresh fixture capture (`docs/handoffs/M10-03.md`) and dated
amendments to `docs/LAB_FORMATS.md`; there is no runbook skill for it yet.

The shell guard blocks `git rm` under `.claude/` for every Claude session,
so the deletion is the owner's: `git rm -r .claude/skills/patch-day`.
