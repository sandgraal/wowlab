# The `/simc` export format

Reference for `api/src/bronze_api/services/simc_parser.py` and the fixture
corpus. The SimulationCraft addon writes this text; SimC reads it as a
profile. **Every statement below is a hypothesis until a real fixture
confirms it** — items are marked *verify* where no fixture exists yet. The
addon's source is public (see `docs/DATA_SOURCES.md`, "SimC addon export")
and resolves most *verify* rows before fixtures arrive; fixtures are still
what tests run against. Update this file with dated notes whenever a fixture
or the addon source teaches us something.

## Shape

```
# Charname - Augmentation - 2026-09-09 14:22 - US/Realmname          ← header comment (advisory)
# SimC Addon 12.1.0-01                                                ← *verify*: addon/WoW/requires lines
# WoW 12.1.0.61234, TOC 120100                                        ← source of snapshots.game_version (*verify*)
# Requires SimulationCraft 1210-01 or newer
evoker="Charname"                                                     ← class key = class; value = name (authoritative)
level=90
race=dracthyr
region=us
server=realmname                                                      ← NOT the API realm slug; see below
role=spell
professions=blacksmithing=100/jewelcrafting=100
spec=augmentation

talents=CkEBb...                                                      ← Blizzard loadout string, opaque to us

head=,id=228851,bonus_id=10356/9633/8902,gem_id=213743,enchant_id=7936,ilevel=protect
neck=,id=228843,bonus_id=10356/9633,gem_id=213482/213482
...
main_hand=,id=228906,bonus_id=10356,enchant_id=7448,crafted_stats=40/36,crafting_quality=5

### Gear from Bags                                                     ← comment sections; see below
# head=,id=228763,bonus_id=10353/9627
```

## Key-value lines

| Key | Notes |
|---|---|
| `<class>="Name"` | Class is the **key**: one of `warrior paladin hunter rogue priest deathknight shaman mage warlock monk druid demonhunter evoker`. *verify*: also accept `death_knight` / `demon_hunter` aliases and unquoted names (SimC's own profiles use them). Never read class positionally. |
| `level` | Integer. Sub-max-level characters export too. |
| `race` | Opaque string, preserved as-is. *verify* form: SimC's race enum uses underscores (`blood_elf`, `highmountain_tauren`); the set is open (new races per expansion). Never validate against a fixed list. |
| `region` | Region code. |
| `server` | The addon writes the realm **display name lowercased with spaces, apostrophes and hyphens removed** (`area52`, `kelthuzad`, `tarrenmill`), which is *not* the Blizzard API slug (`area-52`, `kelthuzad`, `tarren-mill`). *verify*. Ingest normalises it to the canonical `realm_slug` via the realm list (`data/wow/realm/index` or SimC's realm table) so a pasted character and an API-sourced one are the same row. Identity is (region, canonical realm slug, lowercased Unicode name). |
| `role` | *verify* which the addon emits; SimC roles are `attack spell hybrid dps heal tank`. Advisory. |
| `spec` | Lowercase spec slug at export time with underscores (`beast_mastery`). **Not** a property of the character. |
| `talents` | Loadout string for the current loadout. Its header encodes serialization version, spec id and a tree hash; the spec id may lie for off-spec exports. |
| `professions` | `name=skill/name=skill`. *verify* across versions. |
| `covenant`, `soulbind`, `renown` | Shadowlands-era lines that may still appear in old exports. Tolerate and preserve. |

The header comment (`# Name - Spec - timestamp - Region/Realm`) is
**advisory**: cross-check name against the class line, never trust it over
the key-value lines. The timestamp is the **client's local time with no
timezone**; do not use it as `captured_at`. For pastes, `captured_at =
ingested_at`, and the header time is preserved as `extra.captured_local`
(naive). *verify* whether newer addon versions emit an epoch or timezone.
The `# WoW <build>` line is the source of `snapshots.game_version` (*verify*
its exact shape); a paste without it gets the game version the deployment is
pinned to, labelled as assumed.

## Slot lines

Sixteen equipped slots: `head neck shoulder back chest wrist hands waist legs
feet finger1 finger2 trinket1 trinket2 main_hand off_hand`. A two-hander has
no `off_hand` line. A slot line is `<slot>=,` followed by comma-separated
`key=value` sub-attributes.

| Sub-attribute | Shape | Notes |
|---|---|---|
| `id` | int | Base item id. **Not** the item's identity on its own. |
| `bonus_id` | `int/int/...` | Modifies what the item *is*: item level, upgrade track position, socket, affix. Part of identity; compare as an ordered list. |
| `gem_id` | `int/int/...` | One per socket. *verify* how an empty socket is represented (omitted, `0`, or absent entry). |
| `enchant_id` | int | Runeforges on Death Knight weapons arrive here too (*verify*). |
| `crafted_stats` | `int/int` | Player-chosen secondary allocation on crafted gear. |
| `crafting_quality` | int 1–5 | *verify* key name. Part of identity: a recraft from quality 3 to 5 changes the item with identical id and bonus ids. |
| `ilevel` | int or `protect` | *verify*: the literal `protect` seen in exports. **Whether the addon writes a numeric item level per slot is unconfirmed.** If it does not, the item level of an equipped piece has exactly two honest sources: the Blizzard equipment API (`level.value`, API snapshots only) or the baseline sim's per-slot output (M2). It is **never** `game_items.item_level` (that is the base item) and never bonus-id arithmetic. Until one of those sources exists the field is null and the UI says so. |
| `context`, `drop_level` | int | Occasionally present. |
| `embellishment`? | *verify* | Embellishments are encoded via bonus ids in most captures. |

**Treat the sub-attribute key set as open.** Unknown keys are preserved
verbatim in `parsed.equipped[slot]` (and `_raw` holds the whole line). A
patch that adds a key must not lose data. **A slot has changed between two
snapshots when its full sub-attribute map (every key except `_raw`)
differs** — never an enumerated subset of keys.

## Comment sections

Everything outside the key-value and equipped blocks is comments to SimC but
data to us. *verify* all of the following against the addon source and the
M1-01 fixtures: which sections use a `###` heading, which are written
*before* the gear block, whether a terminator such as `### End of Weekly
Reward Choices` exists (it must not become an empty section), and that bare
`#` lines and item-name comment lines inside sections are skipped, not
errored.

| Section (working name) | Contents | Bronze uses it for |
|---|---|---|
| `### Gear from Bags` | `# <slot>=,id=...` lines: alternates the player owns | Top Gear candidate pool (M3) |
| `### Weekly Reward Choices` | `# <slot>=,id=...` lines for the Great Vault choices, present only when the vault window was opened before `/simc` (*verify*: whether opening it once per session suffices; whether unclaimed last-week rewards export as stale choices) | Vault ranking (M2) — ADR-0015. The export cannot distinguish "vault not opened" from "nothing to claim"; the UI empty state covers both. |
| `### Saved Loadouts` (or `# Saved Loadout: <name>` / `# talents=` pairs) | Every saved loadout with its talent string | Loadout library seed (M3) |
| `### Additional Character Info` | `# upgrade_currencies=...` (crests, flightstones — names from game data, not constants), `# slot_high_watermarks=...` (crest-discount input), `# checksum=...` (ignored; *verify* whether it covers the timestamp) | Planner inputs (M4); crest count on the vault card (M2) |

Canonical `parsed` additions beyond plan §8.1 (proposed in ADR-0015,
confirmed when fixtures land): `vault_choices: [slot items]` — named for
what it is, choices not items — `loadouts: [{name, talents}]`, and
`extra: {key: raw value}`.

## Canonicalization for `content_hash`

One definition, used everywhere (this section, plan §7, M1-04): the hash is
taken over the **canonical `parsed` payload** — sorted keys, no `_raw`, no
`extra.captured_local` — which includes `equipped`, `bags`, `vault_choices`,
`loadouts`, and the rest of `extra`. Two exports of the same state minutes
apart hash equal; a changed gem does not; and **the same character pasted
with and without the vault section is two snapshots**, because the vault
choices are state we must not silently drop. The addon-version and WoW-build
header lines are excluded from the hash so an addon update alone does not
create a snapshot; the `game_version` column still records the build.

## Fixture index

`api/tests/fixtures/simc/README.md` is the index; `make test-parser` fails
if a file is missing a row. Columns: `file, class, spec, game_version,
captured_by, consent, sections, edge_cases`. Add fixtures only with
`/fixture`. Never edit a committed fixture; a new patch is a new file.

## Change log

- 2026-09-09 — Initial reference written from the plan and prior knowledge; no
  fixtures yet. Everything marked *verify* is open.
- 2026-09-09 — Domain review: realm name vs slug, local-time header,
  per-slot item level provenance, open race/role sets, canonicalization now
  keeps comment sections, `vault` renamed `vault_choices`.
