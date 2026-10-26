# Wellspring — the design of `wowlab ui`

Owner request, 2026-10-26: redesign `wowlab ui` as a fantasy-themed app. This is the spec for that redesign; tickets M16-50 to M16-52 build it, and ADR-0035 (Proposed) decides how its fonts ship. Until M16-52 merges, `main` still draws the old look described by `lab/core/src/wowlab_core/ui/templates/page.css`.

| File | What it is |
|---|---|
| `docs/design/WELLSPRING.md` | This book: the rules, with token names. |
| `docs/design/wellspring-tokens.json` | Every token and its value per theme (`warband`, `bastion`), with a usage note. The values are the spec. |
| `docs/design/wellspring-components.css` | A reference stylesheet drawing every component on the tokens. It is a reference, not a file the package loads: its class names are the design system's, and M16-52 maps them onto the classes `page.js` already emits (table below). |
| `docs/design/wellspring-sigil-*.svg` | The mark in fixed colours, for places that cannot inherit colour. The page draws it inline. |

wowlab Wellspring dresses a careful local tool in the world it serves: a shattered world with a living core, titan-made order laid over it, eldritch things gnawing underneath, and two coalitions who never stop fighting over it. The chrome carries that world. The words stay plain, because the player is deciding whether a file is safe to change.

## The world, mapped onto the interface

| In the setting | In wowlab | Where it shows |
|---|---|---|
| The latent life-force at the core | wowlab itself: the thing that keeps the player's data alive | `wellspring`: primary actions, focus, selection, success, the glow rising from the page floor |
| Titan architects and their safeguards | The write gate, snapshots, undo | `brass` structure, corner brackets on every panel, the Order verdict |
| Destruction | Files wowlab will never touch; refusals; errors | `ruin` |
| Eldritch incursion, the unknown | Files no map row describes | `void` |
| Inert bedrock | Folders, things not used | `stone` |
| Dimensional elsewhere | Links that point outside | `astral`, dotted |
| Decay from outside | A file the server may overwrite at login | `blight`, dashed |
| Two coalitions | The two themes | Warband (dark, default) and Bastion (light), each with its own `heraldry` |

The core belongs to neither coalition: `wellspring` is the one colour both themes share in role, and it is the only thing that glows.

## Content fundamentals

The fantasy lives in colour, type, ornament and structure. It never lives in the words a player reads to make a decision.

- The answer comes first, then at most one caveat. "wowlab can edit. Yes, through wowlab's write gate only, with the client closed."
- Write to "you", about "your game folder". wowlab speaks of itself in the third person: "wowlab can edit", "wowlab won't touch".
- Use the game's words for the game's things (SavedVariables, flavor folder, /reload, Battle.net app). Never invent lore names for real files or actions: a snapshot is a snapshot, not a "ward".
- Lore names belong only to the design system (token names, theme names, this book) and to decorative headings that carry no instruction.
- Sentence case. No exclamation marks, no emoji, no archaic voice ("thou", "ye"), no marketing adjectives.
- Say what is unknown in the player's terms: "wowlab cannot tell whether the server replaces this file at login."
- Never show ticket ids, decision numbers, module names or doc paths to a player.
- A notice leads with one word that carries the state: "Done.", "Refused.", "Note."

## Visual foundations

**Themes.** Warband (`data-theme="warband"`) is the default: char and hide, bone-coloured text, an oxblood `heraldry` band. Bastion (`data-theme="bastion"`) is the light theme: limestone, slate text, a lapis `heraldry` band. Every token exists in both, so a component never names a theme. The gear area stays dark in both (`gear-*`) so the client's item-quality colours read as they do in the game.

**Grounds.** Pages sit on `ground`, with a faint `wellspring-glow` ellipse rising from below the bottom edge (the core beneath the world) and a vignette toward `ground-deep`. Content sits in `surface` panels; cards inside a panel use `surface-raised`; anything typed or raw (fields, code, JSON) sits in `surface-sunken`.

**Colour roles.**
- `text` for reading, `text-muted` for secondary copy.
- `brass` for headings, labels and the selected tab's text.
- `brass-deep` for ornament only: corner brackets, dotted guides, the banner rule.
- `wellspring` for the one thing to do or the one place you are: primary buttons, focus, the selected row's bar, the active tab's underline, success. Text on a wellspring fill is `on-wellspring`.
- `heraldry` only on large shapes: the banner's top band, the codex's top edge, the cover.
- The six forces (`order`, `ruin`, `void`, `stone`, `astral`, `blight`) only for verdicts, and `ruin` for errors. Each has `-bg` and `-border` partners.

**Type.** Three families, all OFL-licensed and shipped as files: no font is ever fetched.
- `font-display`, Alegreya SC: the name (`display` 40px), titles (`title` 28px), panel headings (`heading` 20px), labels, tabs and buttons (`label` 15px), badges (`plaque` 13px). Lowercase text in this face sets as small capitals, so write labels in sentence case and let the face engrave them.
- `font-serif`, Alegreya: everything a player reads at length. `body` 17px/26px; `subheading` 17px bold; `body-small` 15px; `caption` 14px italic; `item-name` 19px bold.
- `font-mono`, IBM Plex Mono, the clockwork register: paths, commands, macro bodies (`code` 14px), JSON and item levels (`code-small` 13px).

**Spacing.** A 4px grid: `space-1` 4, `space-2` 8, `space-3` 12, `space-4` 16, `space-5` 24, `space-6` 32, `space-7` 48. Panels pad `space-4`; the codex pads `space-5`; panels sit `space-5` apart; the page gutter is `space-6` (`space-5` on narrow screens).

**Shape.** Cut stone, barely softened: `radius-sm` 2px on controls, badges and rows; `radius-md` 4px on panels and buttons. No pills, no circles in the interface. Every `panel` and the `codex` carry four brass corner brackets (`ornament-arm` 14px, `ornament-weight` 2px, `brass-deep`): the titans' fittings. They decorate; they never mean a state.

**Depth.** `shadow-panel` under panels. `shadow-glow` is the life-force halo: a hovered or focused primary button and the Confirm seal, and never more than one standing glow in a view. No other glows, no gradients on controls.

**Hierarchy lines.** Trees and the JSON view indent behind a 1px dotted `brass-deep` guide.

**Focus and states.** Every focusable element shows a 2px solid `wellspring` outline, offset 2px, at least 3:1 on every surface in both themes. Hover is `hover`. Selected is `selected` with a 3px `wellspring` bar or underline. Disabled controls are `text-muted` on `surface-sunken` and stay visible. A required empty field gets a 2px `wellspring` outline.

**Motion.** None. Nothing pulses, nothing animates the glow.

**Player text.** Names, paths, macro bodies and item names come from the player's files: `unicode-bidi: isolate` and `overflow-wrap: anywhere` on all of them.

## Verdicts

| Badge | Words | Glyph | Border |
|---|---|---|---|
| `badge-order` | wowlab can edit | ◆ | solid |
| `badge-ruin` | wowlab won't touch | ✕ | solid |
| `badge-void` | unknown | ? | solid |
| `badge-stone` | folder, not used | ▪ | solid |
| `badge-astral` | a link; wowlab won't follow it | ↗ | dotted |
| `badge-blight` | server may overwrite | ≈ | dashed |

The word always carries the meaning. Glyph and border style repeat it so verdicts survive colour blindness and greyscale. Order and Blight are both yellowish to a deuteranope; their glyphs and border styles differ, and Blight never appears alone.

## Iconography

The mark is the Wellspring sigil (`assets/Logos/`): a ring broken in three places (the shattered world), eight titan survey ticks, and a diamond core with a dotted fissure running down from it. In the interface it is inline SVG with classes (`.ring` `brass`, `.tick` `brass-deep`, `.core` and `.fissure` `wellspring`) so it follows the theme. The two SVG files are fixed-colour copies for places that cannot inherit colour: `wellspring-sigil-warband.svg` on dark grounds, `wellspring-sigil-bastion.svg` on light ones.

There is no icon set. Disclosure uses text triangles (▸ ▾); verdict glyphs are typographic. Status is never shown by an icon alone.

## Contrast

Every text token meets 4.5:1 on the grounds its usage note names, in both themes, checked pair by pair; `rule-strong` and the `wellspring` focus ring meet 3:1. Two exceptions are the client's own colours, kept exact: `quality-rare` (3.99:1) and `quality-epic` (3.94:1) on `gear-ground`. They pass only as large text, which `item-name` is.

## Mapping onto the existing page

`page.js` and the UI tests name classes and ids; the redesign keeps every one of them and restyles it. Lore names stay out of the page's class names too.

| Existing class | Wellspring component or force |
|---|---|
| `.banner`, `.crest`, `.tabs`, `.tab` | Banner (the crest becomes the inline sigil), Tabs |
| `.panel`, `.toolbar` | Panel, with corner brackets |
| `.parchment` and its children | Codex |
| `.hint` | Codex hint |
| `#run`, `#looks-write`, `.copy` | Button primary, Button secondary |
| `#confirm`, `.confirm-box` | Button seal, confirm box |
| `.badge-gate` | Order |
| `.badge-no`, `.badge.badge-placeholder` | Ruin |
| `.badge-unclassified` | Void |
| `.badge-folder`, `.badge-not_applicable` | Stone |
| `.badge.badge-link` | Astral (dotted) |
| `.badge-server` | Blight (dashed) |
| `.tree`, `.row`, `.twisty`, `.name`, `.note` | Tree |
| `.jt*` | JsonTree |
| `.gear-*`, `.dressing-*` | GearTable (the quality rules stay generated by the server) |
| `.macro*`, `.char-read-only` | Macro |
| `.error` status lines, `#stderr` | `ruin` text; a refusal is a Notice |

The theme follows the system: Warband by default, Bastion under `prefers-color-scheme: light`. A theme switch is not part of this redesign.

## Fonts in wowlab

The page loads nothing from the network, and every request to its server carries a session token, so the fonts cannot be fetched by URL. ADR-0035 (Proposed) puts the twelve WOFF2 files inside the package and writes them into the page's own style as `data:` URLs. Until it is accepted and M16-51 lands, the `font-*` stacks fall back to Palatino and Georgia, and every other rule in this book still holds.
