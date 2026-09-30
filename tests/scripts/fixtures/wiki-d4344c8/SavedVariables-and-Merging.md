# SavedVariables and Merging

A **SavedVariables** file is where an addon keeps its settings and data: a Lua file the client writes when you log out or `/reload`, and reads when you log in. wowlab reads and merges them **as data**. It never runs Lua.

## Reading

```bash
uv run wowlab sv list                     # every SavedVariables file: account-wide and per character
uv run wowlab sv dump SomeAddon.lua       # every value, one per line, in file order
uv run wowlab sv dump SomeAddon.lua --path 'SomeAddonDB["Default"].scale'   # one value
```

`sv dump` prints one `path = value` line per value:

```text
DBT_AllPersistentOptions["Default"]["DBM"]["TimerY"] = -260
DBT_AllPersistentOptions["Default"]["DBM"]["TimerPoint"] = "TOPRIGHT"
DBT_AllPersistentOptions["Default"]["DBM"]["HugeBarsEnabled"] = true
```

The path grammar is the same one `--path` and `sv merge --key` accept: `Var.key[3].name` and `Var["some key"]`. A quoted key takes Lua 5.1 escapes (`\n`, `\\`, `\"`, `\ddd`), and every key wowlab prints can be read back in.

`--json` gives the same content as machine-readable JSON. Related readers: `cvar`, `binds`, `macros`, `addons` (see the [Command Reference](Command-Reference)).

### Why an unmodified file comes back byte for byte

The parser keeps key order, key style and the original text of every number, so `serialize(parse(x)) == x` for every real file it has been tested on. Merging edits only the keys you name and leaves the rest of the document as it was.

## Merging

`sv merge` copies settings **between characters, from a snapshot, or within one file**, key by key, and writes the result through the [write gate](Safety-and-Guarantees) (game closed, snapshot first, `wowlab undo` reverses it).

```bash
# Copy one addon profile between two characters (two-way)
uv run wowlab sv merge SomeAddon.lua --from <source-char> --into <target-char> --key SomeAddonDB.profile

# Three-way: take what changed since a common ancestor snapshot
uv run wowlab sv merge SomeAddon.lua --from <snapshot> --base <older-snapshot> --into <target-char>

# Copy a subtree to another key inside the same file
uv run wowlab sv merge SomeAddon.lua --into <target-char> --key 'SomeAddonDB.profiles.Old=SomeAddonDB.profiles.New'
```

`--into` is always required: it names the target character, and its `WowLab.lua` is read for the [loader check](#the-loader-check).

### Two-way and three-way

| Mode | How you ask | Rule |
|---|---|---|
| **Two-way** | `--from <character or snapshot>` | Every key whose values differ is a conflict. |
| **Three-way** | `--from SNAPSHOT --base SNAPSHOT` | A change on one side is taken; the same change on both sides is taken once; *different* changes are conflicts. |

**Conflicts are listed, never guessed.** With conflicts, nothing is written and the command exits 1, unless you resolve them all with `--take ours` (keep the target's value) or `--take theirs` (take the source's).

**A key on one side only is reported as "absent" and never deleted.** Many addons leave out values equal to their defaults when they write, and wowlab cannot see those defaults, so it will not treat a missing key as a deletion.

`--key` limits the merge to named subtrees and is repeatable. `SRC=DST` copies `SRC` to `DST`.

### Account-wide files

A file with `## SavedVariables:` in an addon's manifest is shared by every character. Merging one from another *character* is refused (exit 2), since both characters share the same file. Use `--from SNAPSHOT` (another machine, or an earlier state), or `--key SRC=DST` to copy between the per-character keys an addon keeps inside the file.

On Forever, a per-character file resolves to `<digits>/<First>-<Second>/SavedVariables/`, never to the `<Realm>/<First>/` twin folder that holds only `AddOns.txt`.

### The loader check

The Forever beta had a bug where SavedVariables were written but not loaded back (reported since fixed). If that bug returned, an addon would load defaults at your next login and save them at logout, **overwriting your merge**, which would look like a merge bug.

So before writing, `sv merge` reads the target character's `WowLab.lua` (recorded by the [lab-addon](Lab-Addon-and-Characters)) and refuses with exit 3 and a clear reason when the addon's load counter shows the file failed to load or stopped going up across snapshots. `--force-loader-check` overrides it. With no capture of `WowLab.lua`, it warns and continues.

Install the lab-addon and play a session on the target character to give the check something to read.

### Always verify after a merge

The addon reads the merged file at your next login, may migrate it, and rewrites it at logout. **A merge is proven only after one login and logout.** A subtree taken from an older addon version may be reset by the addon.

## Tips

- Run without `--yes` so you see the plan and are asked before anything is written, and read the conflict list before you add `--take`.
- Take a labelled snapshot first if the merge matters to you: `wowlab snap create -m "before merge"`.
- SavedVariables are written at logout, so wowlab always sees the *previous* session. Log out fully before reading or merging.
