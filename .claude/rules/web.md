---
paths:
  - "web/**"
---

# Web conventions

- Next.js App Router, TypeScript strict, pnpm. Server components by default; client components only for interaction.
- Read `docs/PRODUCT.md` before building any page. Its quality bar is part of every web ticket's acceptance: empty, loading, and error states designed; keyboard navigable; WCAG AA contrast; mobile-readable results; deltas never rely on red/green alone; healers and tanks get the designed non-sim path.
- Dark theme is the default. Class colors and item-quality colors come from shared tokens with accessible-contrast variants; never hard-code a hex.
- Item and spell tooltips use the Wowhead tooltip script with attribution. **The tooltip link must carry the item's `bonus=`, `gems=`, `ench=` (and level where relevant) from the snapshot's sub-attributes**; a link by item id alone shows the base item, which is the bonus-id trap rendered on every hover. Do not scrape Wowhead.
- Every sim-backed number shows its uncertainty, the fight profile it assumed, and the SimC version. Two options within noise are presented as a tie, not a ranking.
- Every vault card shows upgrade track and position, never item level alone; tier-slot candidates show the catalysed variant.
- API-sourced snapshots are labelled lower fidelity than `/simc` snapshots wherever they appear; every page shows the snapshot's source and age.
- No account is required to get an answer. Claiming a character is a separate, optional flow that can hide pasted-only data (bags, vault choices, loadouts) or the page.
- Every result page has a stable URL and an Open Graph card, because WoW communities share through Discord.
- Names of abilities, items, and currencies come from game data at the snapshot's `game_version`, never from string constants.
