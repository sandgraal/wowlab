# Profiles

A **profile** is a named set of your client's local UI files, saved into the snapshot store and applied back through the [write gate](Safety-and-Guarantees). Use it to switch between setups like `raid`, `clean` or `gamepad`, with undo.

> [!WARNING]
> **Read this before you apply a profile.** A profile covers **every account and every character folder that existed when it was saved**, not only the character you play. Applying it writes the saved bytes back **and deletes files created in those places since the save**. `wowlab profile apply NAME --dry-run` shows exactly what would happen; use it first.

## What a profile can hold

Choose one or more **presets** (repeat `--preset`):

| Preset | Files |
|---|---|
| `ui` | `Config.wtf` (machine-wide, so it also holds graphics, sound, locale and the last account), account and character `config-cache.wtf`, the edit-mode caches, `layout-local.txt`, `chat-cache.txt` |
| `bindings` | account and character `bindings-cache.wtf`, `click-bindings-cache.txt` |
| `macros` | account and character `macros-cache.txt` |
| `addons` | `Interface/AddOns/` as saved, `AddOns.txt`, and addon SavedVariables |

Or name exact files or folders with `--subtree PATH` (relative to the flavor folder, under `WTF/`, `Interface/` or `Fonts/`, and narrower than `WTF/Account/`; for a whole area use `snap create` and `snap restore`).

**Never included:** the lab-addon (`Interface/AddOns/WowLab/` and its `WowLab.lua`) is always left alone, by presets and subtrees alike.

**Not in any profile:** action-bar contents and talents. The server keeps them (not yet verified on this client), so they are not in these files.

## Commands

```bash
uv run wowlab profile save raid --preset ui --preset bindings
uv run wowlab profile list
uv run wowlab profile show raid
uv run wowlab profile apply raid --dry-run
uv run wowlab profile apply raid
uv run wowlab profile delete raid
```

| Command | Notes |
|---|---|
| `profile save NAME` | Reads your install; writes only a labelled snapshot into wowlab's store. |
| `profile list` | Oldest first; names any damaged manifest and exits 1. |
| `profile show NAME` | The profile and every file in it, plus the notes `save` gave (for example, a saved subtree that was itself a link is held as the link). |
| `profile apply NAME` | Through the write gate: game closed, pre-write snapshot, journaled. `wowlab undo` reverses it. |
| `profile delete NAME` | Removes the profile. **Its snapshot stays in the store**, relabelled `deleted-profile:NAME`. Nothing in your install changes. |

## What `apply` does, exactly

- Writes every saved file back to its saved bytes.
- **Deletes files** under the profile's recorded places that did not exist at save time. For example: a macro file or character-specific key bindings created since, on *any* of those characters.
- With the `addons` preset: **addons installed since are removed** (code and settings), and **addons updated since go back to the saved version**, which an addon manager will not know about.
- Leaves alone any file the file map marks as "leave alone" (client-written backups, `Blizzard_*` folders, file-browser metadata), and anything the gate will not write or delete (an executable). Both are listed.
- Lists every `*-cache*` file it writes. The server may replace these at your next login, so **the result is proven only by logging in**.
- A very large apply is refused with a count and a pointer to `snap restore` or a narrower profile (the plan sets the ceiling at 2,000 files).

A character folder created *after* a preset save is not part of that profile and is not touched.

## Tips

- Take a manual snapshot before your first apply: `uv run wowlab snap create -m "before profiles"`.
- Keep presets small. A `bindings` profile only touches bindings; an `addons` profile is a big hammer.
- Check `wowlab doctor` for server-sync settings. With `synchronizeBindings` on, the server may write your bindings again at login.
- Macro buttons may point at a macro by its *position in the list*. After applying a `macros` profile, check your action bars.
