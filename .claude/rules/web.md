---
paths:
  - "web/**"
---

# Web conventions

- Next.js App Router, TypeScript strict, pnpm. Server components by default; client components only for interaction.
- Read `docs/PRODUCT.md` before building any page. Its quality bar is part of every web ticket's acceptance: empty, loading, and error states designed; keyboard navigable; WCAG AA contrast; mobile-readable results; deltas never rely on red/green alone.
- Dark theme is the default. Class colors and item-quality colors come from shared tokens with accessible-contrast variants; never hard-code a hex.
- Item and spell tooltips use the Wowhead tooltip script with attribution (see `docs/DATA_SOURCES.md`). Do not scrape Wowhead.
- Every sim-backed number shows its uncertainty. Two options within noise are presented as a tie, not a ranking.
- API-sourced snapshots are labelled lower fidelity than `/simc` snapshots wherever they appear.
- No account is required to get an answer. Claiming a character is a separate, optional flow.
- Every result page has a stable URL and an Open Graph card, because WoW communities share through Discord.
