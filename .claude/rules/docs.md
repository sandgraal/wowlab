---
paths:
  - "docs/**"
---

# Documentation

- `docs/DECISIONS.md` is append-only. A new decision is a new ADR (`/adr new`); changing an old one is a superseding ADR or a dated amendment under the original. **Status stays `Proposed` until the repository owner flips it to `Accepted`** — no agent or conductor does that.
- `docs/BACKLOG.md` ticket headings carry their status: `## [ ] M1-02 — Title` / `## [x] …`. Implementers never edit this file; the conductor ticks boxes in a batched `docs(backlog)` PR.
- `docs/GLOSSARY.md` is for domain terms only. If you had to learn a WoW concept to do a ticket, add it.
- `docs/DATA_SOURCES.md` has a breakage log per source; every real-world API surprise gets a dated entry.
- Amendments are dated (`2026-09-09`), not relative ("last week").
