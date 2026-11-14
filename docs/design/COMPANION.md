# Companion — the M18 design of `wowlab ui`

*Owner decision, 2026-11-14, in session ("mockup approved"), on the conductor's mockup made from the owner's own characters (claude.ai artifact "wowlab companion redesign", private to the owner).* This book replaces `WELLSPRING.md` as the spec for `wowlab ui` once M18-03 merges. Until then `main` draws Wellspring. Tickets M18-03 to M18-06 build it; ADR-0035's amendment of 2026-11-14 decides how its fonts ship.

## The idea

A player opens wowlab to see their characters and to know their UI is safe. Every screen answers one of those two questions first, in plain words, and keeps everything else (where a figure came from, which command produced it, which file it read) one click away under **About this data** or in the **Advanced** section. Nothing on the first screen of a section is a file path, a folder name, a numeric id, a `[verify]` mark, a CLI command or a paragraph of caveats.

## Sections

The header has the mark, the name and five sections, in this order:

| Section | Holds |
|---|---|
| **Characters** (home) | Status cards, then character cards (M18-04); a character page per character (M18-05) |
| **Backups** | The backup list per game version, take one, restore one (today's Backup card and `snap` views) |
| **Addons** | The patch-day check (today's patch-day card), grouped so expected noise is one line |
| **Keybinds** | Today's Keys table and the keybind coach |
| **Advanced** | Today's Explorer, Gaps, Commands and Looks tabs, unchanged in behaviour |

On the right of the header: a dot and the game being read, named for people ("WoW Forever · 1.60.1"), never a folder name like `_forever_`. With more than one game version installed, it is a picker.

## Tokens (one dark theme)

| Token | Value | Use |
|---|---|---|
| `bg` | `#0d1015` | Page |
| `bar` | `#11151c` | Header, inset rows |
| `surface` | `#151a22` | Cards and panels |
| `line` | `#222a35` | Card borders, dividers, bar tracks |
| `line-strong` | `#2c3644` | Secondary button borders, dashed "missing" card |
| `text` | `#e7eaee` | Body text |
| `muted` | `#9aa4b1` | Secondary text (passes 4.5:1 on `surface`) |
| `faint` | `#8a94a1` | Labels at 12 px and up (passes 4.5:1 on `bar`) |
| `accent` | `#f4c15d` | The one primary button per screen ("Back up now"), the mark, key labels |
| `ok` | `#4ade80` | Good status, gains ("+6 levels") |
| `link` | `#7cc4ff` | Links |
| `prof` | `#6ee7b7` | Profession bars (main professions); `#93a3b8` for secondary skills |

Radii: cards 14 px, buttons and rows 10 px, icon tiles 12 px. Spacing: 16 px between cards, 22 px card padding, 28–36 px between page blocks. Content is centred at 1240 px max with 32 px gutters; at phone width cards stack and the nav wraps. Buttons are at least 44 px tall.

**Class colours** (WoW's own): Warrior `#C69B6D`, Paladin `#F48CBA`, Hunter `#AAD372`, Rogue `#FFF468`, Priest `#FFFFFF`, Death Knight `#C41E3A`, Shaman `#0070DD`, Mage `#3FC7EB`, Warlock `#8788EE`, Monk `#00FF98`, Druid `#FF7C0A`, Demon Hunter `#A330C9`, Evoker `#33937F`. A character card has a 6 px stripe and a level tile in the class colour; class names in text use a lighter tint where the colour fails 4.5:1 on `surface`.

**Item quality colours**, tuned for the dark surface (the game's own blues and purples are too dark to read here): Poor `#9d9d9d`, Common `#e7eaee`, Uncommon `#3ddc68`, Rare `#4da3ff`, Epic `#b970f7`, Legendary `#ff9a2e`; an empty slot's border `#5b6573` (a decoration) and the word "Empty" in `faint` `#8a94a1`.

**Type:** Sora (600, 700) for headings, names and big numbers; Inter (400, 500, 600) for everything else; IBM Plex Mono only in Advanced. Body 15 px, card titles 18 px, page titles 26–34 px.

## Words

- Names, never ids: "Undead Warlock", "Tailoring 51/150". When a name table is not cached, the screen says once, at the top, "Names need a one-time download" with a button (M18-02), and shows the id meanwhile.
- Times as people say them: "18 hours ago", "Nov 12", "last played".
- One reassurance per status, not a policy: "Your UI is backed up", "17 of 21 addons load on this version. 4 are Deadly Boss Mods raid packs for retail. Nothing to fix."
- Every caveat that the current screens print (modification time versus recorded time, `[verify]` marks, which command ran) moves under **About this data**, a closed disclosure at the bottom of the screen. Nothing is deleted; it moves.
