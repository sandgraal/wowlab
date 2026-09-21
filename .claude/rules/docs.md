---
paths:
  - "docs/**"
---

# Documentation

- `docs/DECISIONS.md` is append-only. A new decision is a new ADR (`/adr new`); changing an old one is a superseding ADR or a dated amendment under the original. **Status stays `Proposed` until the repository owner flips it to `Accepted`** — no agent or conductor does that.
- `docs/BACKLOG.md` ticket headings carry their status: `## [ ] M10-04 — Title` / `## [x] …`. Implementers never edit this file; the conductor ticks boxes in a batched `docs(backlog)` PR. It holds the current wave only (ADR-0024).
- `docs/LAB_PLAN.md` is the spec. `docs/LAB_IDEAS.md` is a menu: never turn a row into a ticket without the owner's wave pick.
- `docs/LAB_FORMATS.md` and `docs/LAB_FILE_MAP.md` defer to real fixtures. When a capture contradicts them, the document gets a dated amendment and the **[verify]** marker is resolved; the fixture is never edited to fit.
- `docs/GLOSSARY.md` is for domain terms only. If you had to learn a WoW or client concept to do a ticket, add it.
- `docs/DATA_SOURCES.md` has a breakage log per source; every real-world surprise from the install or from wago.tools gets a dated entry.
- Amendments are dated (`2026-09-09`), not relative ("last week").
