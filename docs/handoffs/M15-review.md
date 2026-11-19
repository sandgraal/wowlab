# Wave 6 (M15, character-studio's data half) review

Written by the conductor on 2026-11-19, following ADR-0024 and ticket
M15-12. The wave opened on 2026-10-19 (#305), paused after M15-05 for the
launch edition (owner decision, 2026-10-21), and resumed on 2026-11-17
after v0.3 (#593).

## What shipped

| Ticket | PR | What it is |
|---|---|---|
| M15-01 | #307 | The lab-addon's schema 4 records the character's race, body type and class |
| M15-02 | #317 | The Character tab's dressing-room link |
| M15-03, M15-04, M15-05, M15-14 | #312, #318, #320, #360 | `casc`: the build config, the local index, BLTE, encoding and root, and `CascSource.read`, all read-only |
| M15-06 | #597 | `wowlab casc stat` and `casc cat` |
| M15-07 | #598 | `blp`: textures to RGBA and PNG (DXT1, DXT5 and palette) |
| M15-08 | #595 | `m2`: models and skins, read as data |
| M15-09, M15-13 | #596, #600 | `renderplan`: the body and its customization, then the gear chain |
| M15-10 | #603 | `compositor`: the body texture |
| M15-11 | #611 | `gltf` and `wowlab char model`: the character written as a `.glb` file |
| M15-22 | #621 | The CLI imports a command's module when that command runs |
| M15-24 | #619 | A probe: where held items could be placed (no drawing) |
| M15-15, M15-17, M15-25 | #616, #614, #622 | Review leftovers |
| M15-18, M15-19, M15-20, M15-23 | #604, #605, #606, #612 | CI repairs: the budget test, two Windows failures, typer 0.29 |
| M15-21 | #608, #609 | Shorter pull-request runs; desktop and budget suites at night |

M15-16 was closed by an owner ruling, not a change: the full test job is
not a GitHub required check, so a merge waits for it by name (#601, #602).

What a player gets: one new command. `wowlab char model` reads the
character's saved look and gear, reads the models and textures from the
install's own archives, and writes a `.glb` that any glTF viewer opens.
It is on `main` and in no release. The viewer inside `wowlab ui` is Wave 7
(§17.8), not built.

## What the install taught

- **Streaming does not matter on this install.** At 70450 it held 20 of
  1,243,524 file ids as not-local and 0 of 300 sampled model ids. A partly
  downloaded install is still unchecked.
- **Encryption is rare.** 9 of 4,079 sampled archive records had an
  encrypted block; no LZ4 block was met. Whether any player gear is
  encrypted is still open.
- **Timings held without a second language.** The index loads inside the
  2 s target, and a texture decodes in 0.15 s against 0.5 s. The body
  texture took 0.24 s for a fixture character and 2.6 s for the constructed
  worst case, CPU time on the owner's M1. No target was missed, so
  ADR-0020's stop-and-ask never came up. Windows is unmeasured.
- **Palette textures exist.** 86 of 300 sampled customization textures at
  70959 are palette textures, which the plan had not expected.
- **The beta's folder outlived the beta.** When 70450 closed, its flavor
  folder stayed with no row in `.build.info`. Commands now say so, and the
  70450 live tests skip with that reason (M15-15).
- **Held items have no rest position.** The hand and shield points of both
  fixture body models are stored at the origin, so `char model` draws no
  weapon. The probe (M15-24) found points 26 to 33 stored away from the
  origin. That they are the sheath points is a hypothesis, and the rotation
  that would give a carry angle is not read yet.

## The state of §17.7's [verify] list

Settled by a test on the install:

- The M2 vertex count against the skin's, for `humanmale_hd` and
  `humanfemale_hd` (the seven skins' sets, one after another).
- Whether the install streams (it does not, in any way that matters here).
- Attachment positions in the bind pose: not so for the held-item points
  (0, 1, 2), which are stored at the origin. Why is not settled (below).
- Texture formats (DXT1, DXT5 and palette, all decoded).

Seen by the owner on 2026-11-19, in a glTF viewer, on a character no
fixture covers (an undead in a robe at 1.60.1.70959, front view, stand-in
look because the addon holds no barber-shop record for it):

- Y-up is right: the character stands upright, head up.
- Shoulder items placed as stored sit on the shoulders, one each side.
  This is the first time an attached model was drawn.
- The body texture lands where it belongs: the face on the head, the robe,
  belt, sleeves and gloves in their places. The robe's skirt is drawn.
- The rest pose is arms out and down, hands open.

Whether the tables describe how Forever's playable races look is not
settled by this: the look is the stand-in, so hair, face and skin were not
compared with the game.

Still waiting for the owner:

- Mirroring (an asymmetric feature compared with the game) and which way
  the character faces: one front view does not settle either.
- Head and back items placed as stored (none worn).
- The real look, after a barber-shop visit records it, and with it whether
  the tables describe how Forever's playable races look.

Still open, with nothing on Forever to test against:

- The bonus-id semantics (no type-7 `ItemBonus` row at 70959), and whether
  Forever has transmog at all.
- The compositor's note of 2026-11-18, every item: layer order, the
  per-slot texture layer table and whether its numbers share a number space
  with `Layer`, the blend formulas, item pieces always painted with mode
  15, how a stretched texture is filtered, the canvas starting black, and
  whether base skin 959287's 918 black pixels ever show in game.
- `casc`: which `.idx` version wins when two exist; whether LZ4 ever
  appears; whether any player gear is encrypted; the root's architecture
  bits and whether an Apple-silicon client skips a block beyond the `0x108`
  mask; a partly downloaded install; when the client fetches a file it
  lacks; the Windows handle question; why `ecache/` exists.
- `blp`: the 4-bit palette alpha; the DXT choice enum and the DXT rule
  conflict; the `TextureFileData` usage type.
- `m2`: whether a LOD skin's lookup is relative to its set's start, and
  the vertex layout of other models; every race's HD body keeping its bones
  inside; collection models at the origin; the blend-mode enum and the
  two-face flag (both taken from wow.export).
- `renderplan`: the `+1` in the item geoset value; the per-slot
  geoset-group table and the default geoset rule.
- Attachments: that the held-item points are placed by animation; that
  points 26 to 33 are the sheath points.
- How a browser's CSP admits the vendored modules (Wave 7, ADR-0034).

## Cheaper or dearer than estimated

- **Cheaper:** the readers. Pure Python met every timing target, so no
  ticket needed a faster path.
- **Dearer:** the wave took 25 numbered tickets against the 12 planned.
  Of the 13 added, four were review leftovers (M15-14, M15-15, M15-17,
  M15-25), six were CI and test repairs (M15-16, M15-18 to M15-21, M15-23),
  one split the render plan in two (M15-13), one made the CLI start faster
  (M15-22), and one was a probe the plan had not foreseen (M15-24).
- **Dearer:** weapons. The plan expected to place every attached item at a
  stored position; held items need either the sheath points or the stand
  animation.

## What went wrong

- **The plan assumed attachment points are model-space positions.** They
  are not for held items. The domain review of #611 caught the first
  draft's wording, which stated the animation explanation as fact; the plan
  now says what was observed and what is inferred.
- **CI went red on Windows three times** (M15-19, M15-23, and a probe in
  M18). Each was a path or file-time difference no Mac run shows. The
  desktop suites now run nightly and on demand, so a Windows failure is
  seen the next morning, not on the pull request.
- **The test run got long.** M15-21 measured where the time goes and moved
  the desktop and budget suites to the night. The test job on #611 still
  took 19 minutes.
- **A security review found a false sentence in the README.** The "what
  leaves your machine" block said no other command makes a request, while
  `char model` downloads tables. #611 added the sentence; the owner
  approved it on 2026-11-19.
- **The usage limit stopped two agents mid-ticket.** Both resumed.

## Not done

- **§11 item 5** (owner decision, 2026-10-21): the wave's dated amendments
  are not folded into §17's body, and there is no
  `docs/handoffs/M15-plan-history.md`. No earlier wave review did this
  either. It is a rewrite of about 670 lines of the spec (§17 through §17.7); the conductor
  asks before doing it.
- **A follow-up to M15-22:** `cli/_common.py` still imports `keymap`,
  `labaddon` and `guard` for every command, about 0.15 s. Not filed.
- **Held items in `char model`:** the owner's choice between leaving them
  out, one more probe of the sheath bones' rotation, or drawing them
  upright and labelled approximate.

## Next wave

The owner picked the crew book on 2026-11-18: M19, `docs/LAB_PLAN.md` §20,
ADR-0036 (accepted 2026-11-11). Its plan and tickets merged in #613, so
dispatch continues with M19-01, M19-02 and M19-04. `wowlab snap rm`
(M19-09) waits for the owner's yes.

What could follow it: Wave 7, the viewer panel in `wowlab ui` (§17.8,
ADR-0034, accepted and not built). It needs the owner's `.glb` check
first, since the axis signs decide what the viewer shows.

**Ideas unblocked** (ADR-0024): the viewer (§17.8). An outfit designer,
transmog and animation stay out.
