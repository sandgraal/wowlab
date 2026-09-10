# Product

What Bronze is for, who uses it, and what "good" means to a player. Every
player-facing ticket is measured against the quality bar at the end.
`domain-reviewer` grades against this document.

## The moment

Tuesday morning (Wednesday in EU). Reset has happened. The Great Vault is
open in the player's UI with up to nine items, and they must pick exactly one
before they do anything else this week. Today they alt-tab through Raidbots,
Wowhead and a Discord channel, guess, and move on. Bronze's job is to make
that moment take thirty seconds and feel certain.

The flow: **open the vault window → type `/simc` → paste into Bronze →
ranked answer with a confidence interval → share the card to the guild
Discord.** No account. No credits. No setup. Every other feature exists
because that moment earned a return visit.

## Personas

**The Tuesday raider.** One or two mains, raids Heroic or Mythic, cares about
one number per week: which vault item. Wants the answer, then the reason.
Will install the companion agent once the site has proven useful, and then
never paste again.

**The alt army.** Five to ten characters, mostly Mythic+. Cannot hold the
state of ten characters in their head. Wants one list: per character, what
is capped, what is worth doing, what the vault will give. Multi-character
planning (M4) is for them.

**The officer.** Looks at other people's characters more than their own.
Needs shareable, public, read-only pages and an honest fidelity label
(this snapshot came from `/simc` two hours ago vs. from the API last week).
Later: group-relative valuation for support specs.

**The theorycrafter.** Wants the raw SimC output, stat weights, the exact
profile that was simmed, and a way to compare loadouts structurally. Will
find our mistakes first and tell everyone. Give them the evidence view and
they become the best advocates.

## Principles

1. **No account to get an answer.** The paste box is the landing page. Claiming
   a character is optional and later.
2. **Answer first, evidence one click away.** The recommendation is the
   headline; the per-ability breakdown, the profile, and the raw JSON are
   behind a disclosure, never gone.
3. **Honest uncertainty.** Every sim number carries its error. Two options
   whose intervals overlap are shown as a tie with "pick by preference or
   other content", not ranked by a decimal. Healer sims and support-spec
   sims say what they cannot model. API-sourced snapshots are labelled lower
   fidelity than `/simc` snapshots. The gap analysis reports the residual it
   could not attribute.
4. **Nothing changed is a result.** "Your vault has nothing better than what
   you wear; take the highest item level for future upgrades" is a first-class
   answer, shown with the same confidence as an upgrade.
5. **WoW-native visual language.** Dark theme by default. Class colors and
   item-quality colors from shared tokens with accessible-contrast variants
   (the canonical class colors fail AA on dark backgrounds; ours pass). Spec
   and item icons. Item and spell tooltips via Wowhead, with attribution.
6. **Fast.** Snapshots are immutable, so character pages are cacheable at the
   edge. Targets: character page server time p75 under 300 ms; vault ranking
   under 60 s with a visible progress state and a cached hit returned
   instantly; paste-to-page under 2 s.
7. **Built to be shared.** Every result has a stable URL and an Open Graph
   card that unfurls in Discord, because that is where WoW communities talk.
8. **Respect the week.** Region-aware reset countdown. Weekly planning is
   anchored to the player's region, not the server's clock.
9. **Diagnostic, never a score.** Gap analysis explains; it does not grade.
   Every attribution cites its numbers. No leaderboards of shame, ever.
10. **Player's data, player's control.** Public pages show only what the
    Armory already shows. Claiming lets you hide a character. The companion
    agent is open source, reproducible, read-only, upload-only.

## Quality bar for every player-facing ticket

A web ticket is not done until all of these are true and were checked by
`domain-reviewer`:

- **States.** Empty (no snapshots yet, no vault items in this export), loading
  (with progress for sims), and error states are designed, not defaulted. A
  parser error shows the offending line with its number, explains what was
  expected, and offers a one-click report that captures the input.
- **Uncertainty.** Every sim-derived number shows ± or an interval. Ties are
  ties.
- **Accessibility.** Keyboard navigable, visible focus, AA contrast on the
  dark theme, deltas never encoded by red/green alone (sign and shape too),
  icons have text alternatives.
- **Mobile.** The result (vault ranking, diff summary, gap headline) is
  readable on a phone without horizontal scrolling. Tables collapse; the
  answer does not.
- **Shareable.** Stable URL, OG card, and a copy-link action on every result.
- **Copy.** Onboarding says "open your Great Vault, then type /simc" where it
  matters. Ability, item, and currency names are current for the pinned
  patch. No jargon without a hover definition for terms in the glossary.
- **Fidelity.** Source and age of the underlying snapshot are visible on
  every page that shows a result.

## Anti-features

- No ads inside results. No dark patterns around claiming or the agent.
- No scraping of Wowhead, Raidbots, or Warcraft Logs pages; only their
  sanctioned APIs and embeds.
- No "recommended" without a sim behind it. No static stat-priority lists.
- No rotation advice in-game, no automation, nothing adjacent to the ToS.
- No shaming. No "you are bottom 10%" framing anywhere.

## Later, on purpose

Discord bot (paste in Discord, get the card back); guild roster view;
group-relative valuation for Augmentation and other support specs;
patch-delta re-sim of every stored snapshot; alt-aware gear routing. All of
these fall out of the snapshot + sim substrate; none of them ship before
vault ranking is loved.
