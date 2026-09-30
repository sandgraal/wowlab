# Snapshots and Undo

A **snapshot** is a saved copy of your client's local state that you can compare and put back. **Undo** reverses the last change wowlab made.

## What a snapshot holds

`wowlab snap create` saves a flavor's:

- `WTF/` — settings, key bindings, macros, layouts and every addon's SavedVariables
- `Interface/` — your addons
- `Fonts/`

Add `--screenshots` to also capture `Screenshots/`. It reads your install and writes only into wowlab's own store.

Snapshots are **content-addressed and immutable**: each distinct file is stored once, and a snapshot is a list of paths and hashes. Taking a second snapshot of a mostly unchanged install costs very little. An id looks like `20260101T120000.000000Z-1a2b3c4d`; anywhere a command takes an ID you can give a unique prefix.

```bash
uv run wowlab snap create -m "before trying the new nameplate addon"
uv run wowlab snap list
uv run wowlab snap show 2026010         # a unique prefix is enough
```

> [!NOTE]
> Snapshots are also taken **automatically** before anything wowlab writes into your install. Those are labelled `guard pre-write: …`. Your own labelled ones are the baselines worth keeping.

## Comparing two snapshots

```bash
uv run wowlab snap diff OLDER NEWER
```

The result lists files added, removed and changed. For SavedVariables files that parse, it goes further and says **which values changed**, down to the individual addon setting, so you can answer "what did that addon update do to my config" without opening a file.

## Putting things back

```bash
uv run wowlab snap restore ID --dry-run     # show the plan, change nothing
uv run wowlab snap restore ID               # show the plan, ask, then restore
uv run wowlab snap restore ID --paths WTF/Config.wtf   # only this path
```

A restore goes through the [write gate](Safety-and-Guarantees): the game must be closed, a pre-write snapshot is taken first, and `wowlab undo` reverses it.

Things to know:

- **Files the file map marks "leave alone"** — client-written backups (`.lua.bak`, `.old`), `Interface/AddOns/Blizzard_*` and file-browser metadata — are *not* restored in a whole-snapshot restore. They are counted in the plan. Name one with `--paths` to restore it anyway.
- **`--paths` can delete.** A path the snapshot covers but does not hold is deleted, because the snapshot says it did not exist then.
- **Restore is not a game-state time machine.** Things the server keeps (action-bar contents and talents, as far as has been verified on this client) are not in these files, so no restore brings them back.
- **`*-cache` files** may be replaced by the server at your next login if server sync is on (`wowlab doctor` tells you). Log in to confirm the result.

## Undo

```bash
uv run wowlab undo
```

Undoes the **most recent** change made through the write gate (a restore, a profile apply, a merge, an addon install, or an earlier undo), from the snapshot taken before it. It shows you the change it is about to undo and asks first.

It is deliberately strict: the journal is read again after you answer, and the undo is refused if the most recent record changed in the meantime. Only the change you were shown is undone.

## Keeping the store healthy

| Command | What it does |
|---|---|
| `wowlab snap verify` | Re-hashes every stored object and checks every manifest. Exits 1 on any problem. |
| `wowlab snap gc --dry-run` | Lists what garbage collection would remove: stored objects that no snapshot refers to and that are older than an hour. |
| `wowlab snap gc` | Removes them. Holds the store lock for the whole run, so nothing else changes the store meanwhile. |
| `wowlab snap list` | Names any damaged manifest it finds and exits 1. |

Some objects that writes made through the gate depend on are kept even when no snapshot refers to them, because the loader check in `sv merge` compares against them (see [SavedVariables and Merging](SavedVariables-and-Merging#the-loader-check)). `wowlab snap gc --help` says exactly what is kept.

Snapshots are immutable and do not expire, and there is no command that deletes a single snapshot. `snap gc` only reclaims *objects* that no snapshot refers to. (A deleted **profile** keeps its snapshot too, relabelled.)

## Recipes

**Try something risky.**
```bash
uv run wowlab snap create -m "before experiment"
# ... change things, log in, decide you hate it ...
uv run wowlab snap diff <before> <after>   # what changed?
uv run wowlab snap restore <before>        # put it all back (game closed)
```

**"My UI broke and I don't know why."** Diff your most recent good snapshot against a fresh one. The value-level diff of SavedVariables usually points at the culprit.

**Roll back the last thing wowlab did.** `uv run wowlab undo`.
