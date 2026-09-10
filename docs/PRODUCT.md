# Product

What Bronze is for, who uses it, and what "good" means to a player. Every
player-facing ticket is measured against the quality bar at the end.
`domain-reviewer` grades against this document.

## The moment

Tuesday morning (Wednesday in EU). Reset has happened. The Great Vault is
open in the player's UI with up to nine choices, and they must pick exactly one
before they do anything else this week. Today they alt-tab through Raidbots,
Wowhead and a Discord channel, guess, and move on. Bronze's job is to make
that moment take thirty seconds and feel certain.

The flow: **install the SimulationCraft addon (once) → open the Great Vault
→ type `/simc` → paste into Bronze → ranked answer with a confidence
interval and the fight profile it assumed → share the card to the guild
Discord.** No account. No credits. No setup beyond the addon every simmer
already has. Every other feature exists because that moment earned a return
visit.

## Personas

**The Tuesday raider.** One or two mains, raids Heroic or Mythic, cares about
one number per week: which vault choice. Wants the answer, then the reason,
then the upgrade headroom and crest cost of the pick. Will install the
companion agent once the site has proven useful, and then never paste again.

**The alt army.** Five to ten characters, mostly Mythic+. Cannot hold the
state of ten characters in their head. Wants one list: per character, what
is capped, what is worth doing, what the vault will give. Their fight profile
is dungeon-shaped, not Patchwerk. Multi-character planning (M4) is for them.

**The officer.** Looks at other people's characters more than their own.
Needs shareable, public, read-only pages and an honest fidelity label
(this snapshot came from `/simc` two hours ago vs. from the API last week).
Later: group-relative valuation for support specs.

**The theorycrafter.** Wants the raw SimC output, the exact profile that was
simmed, the SimC version, the fight profile, the target error, and a way to
compare loadouts structurally. Will find our mistakes first and tell
everyone. Give them the evidence view and they become the best advocates.

**Healers and tanks** are a third of every roster and are not a persona of
their own only because the product cannot yet give them the same answer;
see the quality bar for what they get instead.

## Principles

1. **No account to get an answer.** The paste box is the landing page. Claiming
   a character is optional and later.
2. **Answer first, evidence one click away.** The recommendation is the
   headline; the per-ability breakdown, the profile, the fight profile, the
   SimC version, and the raw JSON are behind a disclosure, never gone.
3. **Honest uncertainty.** Every sim number carries its error. Two options
   whose intervals overlap are shown as a tie with "pick by preference or
   other content", not ranked by a decimal. Every result names the fight
   profile it assumed (style, length, target count) and offers the other
   profile one click away, because trinket and weapon rankings flip between
   single-target and dungeon profiles. API-sourced snapshots are labelled
   lower fidelity than `/simc` snapshots. The gap analysis reports the
   residual it could not attribute.
4. **Nothing changed is a result.** "Your vault has nothing better than what
   you wear" is a first-class answer, shown with the same confidence as an
   upgrade, and it says what to take anyway: the item with the most upgrade
   headroom (highest track), one you can Catalyst into tier, or an off-spec
   piece — and why. Never "highest item level"; item level is where a piece
   is now, the track is where it can go.
5. **WoW-native visual language.** Dark theme by default. Class colors and
   item-quality colors from shared tokens with accessible-contrast variants
   (several canonical class colors fail AA on dark backgrounds; ours pass).
   Spec and item icons. Item and spell tooltips via Wowhead, with
   attribution, and the tooltip link carries the item's bonus ids, gems and
   enchant so it shows *this* item, not the base item.
6. **Fast.** Snapshots are immutable, so character pages are cacheable at the
   edge. Targets: character page server time p75 under 300 ms; vault ranking
   under 60 s with a visible progress state and a cached hit returned
   instantly; paste-to-page under 2 s.
7. **Built to be shared.** Every result has a stable URL and an Open Graph
   card that unfurls in Discord, because that is where WoW communities talk.
8. **Respect the week.** Region-aware reset countdown from one per-region
   table (day and UTC hour; see `docs/GLOSSARY.md`, *verify* per region).
   Weekly planning is anchored to the player's region, not the server's
   clock.
9. **Diagnostic, never a score.** Gap analysis explains; it does not grade.
   Every attribution cites its numbers. No leaderboards of shame, ever.
10. **Player's data, player's control.** Public pages show equipped gear and
    talents, which the Armory already shows. Bag alternates, vault choices,
    saved loadouts and currencies are visible only because the player pasted
    them, and claiming the character lets them hide those or the whole page.
    The companion agent is open source, reproducible, read-only, upload-only.

## Quality bar for every player-facing ticket

A web ticket is not done until all of these are true and were checked by
`domain-reviewer`:

- **States.** Empty (no snapshots yet; no vault choices in this export — which
  can mean "vault not opened before `/simc`" *or* "nothing to claim", and the
  copy covers both), loading (with progress for sims), and error states are
  designed, not defaulted. A parser error shows the offending line with its
  number, explains what was expected, and offers a one-click report that
  captures the input.
- **Uncertainty and profile.** Every sim-derived number shows ± or an
  interval, the fight profile, and the SimC version. Ties are ties.
- **Upgrade headroom.** Every vault card shows the item's upgrade track and
  position, and — when the export carries currencies — how many crests the
  player has and how many upgrades that buys. Never item level alone.
- **Tier and Catalyst.** For a candidate in a tier slot, the catalysed variant
  is simmed and shown alongside, with "if you have a charge" until charge
  counts arrive from the companion (M4). Set-bonus boundaries are never
  hidden inside a single number.
- **Healers and tanks.** A healer gets no sim-ranked answer, because
  SimulationCraft has no maintained healing model; they get track and
  headroom, tier and Catalyst status, and a stat comparison, with copy that
  says exactly that. A tank gets a DPS-profile sim labelled as such, with
  survivability stated as not modelled.
- **Accessibility.** Keyboard navigable, visible focus, AA contrast on the
  dark theme, deltas never encoded by red/green alone (sign and shape too),
  icons have text alternatives.
- **Mobile.** The result (vault ranking, diff summary, gap headline) is
  readable on a phone without horizontal scrolling. Tables collapse; the
  answer does not.
- **Shareable.** Stable URL, OG card, and a copy-link action on every result.
- **Copy.** Onboarding says "install the SimulationCraft addon, open your
  Great Vault, then type /simc and copy". Ability, item, and currency names
  come from game data at the snapshot's version, never from string
  constants. No jargon without a hover definition for terms in the glossary.
- **Fidelity.** Source and age of the underlying snapshot are visible on
  every page that shows a result ("pasted 12 minutes ago", "from the API,
  6 days ago").
- **Method.** Every sim-derived result carries a methodology disclosure:
  target error, iterations run, fight length and variance, SimC version, and
  the time it was computed, including when the hit came from the cache.
  bloodmallet prints this under every chart; a cached number without a
  "computed at" reads as live.

## Anti-features

- No ads inside results. No dark patterns around claiming or the agent.
- No scraping of Wowhead, Raidbots, or Warcraft Logs pages; only their
  sanctioned APIs and embeds.
- No "recommended" without a sim behind it. No static stat-priority lists.
- No rotation advice in-game, no automation, nothing adjacent to the ToS.
- No shaming. No "you are bottom 10%" framing anywhere.

## Later, on purpose

Sim a vault choice "as dropped" and "fully upgraded" (bonus-id substitution
from SimC's data, never stat arithmetic); Discord bot (paste in Discord, get
the card back); guild roster view; group-relative valuation for Augmentation
and other support specs; patch-delta re-sim of every stored snapshot;
alt-aware gear routing; personal comparison charts on the player's own
gear (what bloodmallet sells) and a stat-sweep view; a descriptive "what top
players run" lane with sample size and recency, labelled as such and never
as a recommendation; Discord alerts when the vault is simmed or a snapshot
lands; collections and completion (mounts, pets, toys, transmog) via the
companion agent; saved loadouts by name and rating history for PvP
players, with no sim claims (the export has no bracket field). All of these fall out of the snapshot + sim
substrate; none of them ship before vault ranking is loved. The full ranked
list and its sources are in `docs/COMPETITIVE_LANDSCAPE.md`.
