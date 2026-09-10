# The `/simc` export format

Reference for `api/src/bronze_api/services/simc_parser.py` and the fixture
corpus. The SimulationCraft addon writes this text; SimC reads it as a
profile. **Every statement below is a hypothesis until a real fixture
confirms it** — items are marked *verify* where no fixture exists yet.
Update this file with dated notes whenever a fixture teaches us something.

## Shape

```
# Charname - Augmentation - 2026-09-09 14:22 - US/Realmname          ← header comment (advisory)
evoker="Charname"                                                     ← class key = class; value = name (authoritative)
level=90
race=dracthyr
region=us
server=realmname
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
| `<class>="Name"` | Class is the **key**: one of `warrior paladin hunter rogue priest deathknight shaman mage warlock monk druid demonhunter evoker`. Never read class positionally. |
| `level` | Integer. |
| `race` | Lowercase slug (`dracthyr`, `bloodelf`, `highmountaintauren`). |
| `region` / `server` | Region code and realm slug. Identity is (region, server, name). |
| `role` | `attack`, `spell`, `tank`, `heal`, `hybrid`. Advisory. |
| `spec` | Lowercase spec slug at export time. **Not** a property of the character. |
| `talents` | Loadout string for the current loadout. Header byte carries serialization version + spec id (may lie for off-spec). |
| `professions` | `name=skill/name=skill`. *verify* format across versions. |
| `covenant`, `soulbind`, `renown` | Shadowlands-era lines that may still appear in old exports. Tolerate and preserve. |

The header comment (`# Name - Spec - timestamp - Region/Realm`) is
**advisory**: use it for `captured_at`, cross-check name against the class
line, never trust it over the key-value lines. Strip it before hashing for
dedupe (the timestamp changes every export).

## Slot lines

Sixteen equipped slots: `head neck shoulder back chest wrist hands waist legs
feet finger1 finger2 trinket1 trinket2 main_hand off_hand`. A slot line is
`<slot>=,` followed by comma-separated `key=value` sub-attributes.

| Sub-attribute | Shape | Notes |
|---|---|---|
| `id` | int | Base item id. **Not** the item's identity on its own. |
| `bonus_id` | `int/int/...` | Modifies what the item *is*: item level, upgrade track position, socket, affix. Part of identity. |
| `gem_id` | `int/int/...` | One per socket. *verify*: how an empty socket is represented (omitted, `0`, or absent entry). |
| `enchant_id` | int | |
| `crafted_stats` | `int/int` | Player-chosen secondary allocation on crafted gear. |
| `crafting_quality` | int 1–5 | *verify* key name. |
| `ilevel` | int or `protect` | *verify*: the literal `protect` seen in exports. |
| `context`, `drop_level` | int | Occasionally present. |
| `embellishment`? | *verify* | Embellishments are encoded via bonus ids in most captures. |

**Treat the sub-attribute key set as open.** Unknown keys are preserved
verbatim in `parsed.equipped[slot]` (and `_raw` holds the whole line). A
patch that adds a key must not lose data.

## Comment sections

Everything after the equipped block is comments to SimC but data to us. Each
`###` heading starts a section; `#`-prefixed lines inside are slot lines or
key-value lines with the `# ` stripped.

| Section | Contents | Bronze uses it for |
|---|---|---|
| `### Gear from Bags` | `# <slot>=,id=...` lines: alternates the player owns | Top Gear candidate pool (M3) |
| `### Weekly Reward Choices` | `# <slot>=,id=...` lines for the Great Vault items, **present only when the export was taken with the vault window open** | Vault ranking (M2) — ADR-0015. *verify* heading text and that all nine appear. |
| `### Saved Loadouts` | `# <Loadout name>` then `# talents=...`, repeated | Loadout library seed (M3). *verify*. |
| `### Additional Character Info` | `# upgrade_currencies=...`, `# checksum=...`, others | Planner inputs (M4); checksum ignored. *verify* keys. |

Canonical `parsed` additions beyond plan §8.1: `vault: [slot items]`,
`loadouts: [{name, talents}]`, `extra: {key: raw value}` — proposed in
ADR-0015, confirmed when fixtures land.

## Canonicalization for `content_hash`

Strip the header comment and every `#` line that carries a timestamp; strip
trailing whitespace; keep everything else byte-for-byte, in order. Two
exports of the same state minutes apart must hash equal; a changed gem must
not.

## Fixture index

`api/tests/fixtures/simc/README.md` is the index; `make test-parser` fails
if a file is missing a row. Columns: `file, class, spec, game_version,
captured_by, consent, sections, edge_cases`. Add fixtures only with
`/fixture`. Never edit a committed fixture; a new patch is a new file.

## Change log

- 2026-09-09 — Initial reference written from the plan and prior knowledge; no
  fixtures yet. Everything marked *verify* is open.
