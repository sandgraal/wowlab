# Macro placement — owner runbook: which macro does a macro slot name? (about 15 minutes in game)

Written 2026-10-19 for M14-07. `wowlab char macros` shows, for each macro
in `macros-cache.txt`, the action slots it may sit at. It can only say
"may sit at slot n (unconfirmed)", because one thing is not known: which
macro of the file an action slot's macro id names. This runbook settles it
with one login on one character, three throwaway macros, a logout and a few
commands. When it is settled, a later ticket lets `char macros` say
`confirmed`.

Nothing here is committed by you. You run the steps and tell the next
conductor session "macro placement done", with the table in section 5
filled in. The conductor writes the result into `docs/LAB_FORMATS.md` §7 as
a dated amendment, and only then is the `confirmed` rule built.

## What is known, and what this settles

The lab-addon records each filled action slot. A slot holding a macro keeps
an id only, never the macro's name or body. With no subtype, the id is taken
to be the macro index (`docs/LAB_FORMATS.md`, 2026-10-16, item 7). The one
capture with macro slots (Forever 1.60.1.70425) had two, with ids 149 and
140, and a character `macros-cache.txt` of 29 macros. The plain `/say` macro
put on a bar in that session was in neither macros file, so the file may not
have been rewritten at that logout.

`char macros` uses a candidate numbering: account macros are indices 1 to
120 and character macros follow from 121. The 120 offset is Retail's, from
memory **[verify]**; counting the records in each file's order is this
tool's assumption **[verify]** (the client may keep macros sorted by name,
for one). Because it counts positions in the file, a stale
`macros-cache.txt` with one record missing moves every later candidate.
Three questions are open:

1. Is that the numbering on your client: account macros from 1, character
   macros from 121?
2. Is the order the file's order, the order the macro window shows, or the
   order of the hex ids in the file's `VER 3 <id>` lines? Your new macros
   are named so that alphabetical order and creation order differ, which
   tells them apart.
3. Does the client rewrite `macros-cache.txt` at logout, so that a macro
   made in the session is in the file afterwards?

Every command is pasted from your wowlab folder. Use the flavor you play
and name it in the report. If the install has more than one flavor folder,
add `--flavor <folder>` to every `wowlab` command except `snap list`, as
`wowlab install show` lists it. Replace `<Character>` with your character's
folder name as `wowlab char list` prints it.

## 0. Before anything (client closed)

1. Quit the game client. Battle.net can stay open.
2. Make sure the lab-addon is installed and recent: schema 3 is needed for
   the action bars. It asks before writing, and `wowlab undo` reverses it:

   ```bash
   uv run wowlab addon install lab
   ```

3. Take a snapshot, so the macro files can always come back:

   ```bash
   uv run wowlab snap create -m "before the macro placement test"
   ```

4. Note how many macros each file has now, and when each was written. The
   first lines of this command say both, as `Account macros: <n> macros in
   <file>, written <time>` and `Character macros: …`:

   ```bash
   uv run wowlab char macros --character <Character>
   ```

   Write down the two counts and the two times. You don't need to note any
   macro's name or text.

## 1. In game, on one character

1. **Log in out of combat**, not in a vehicle and not skyriding (both swap
   the main bar). Wait for the chat line "WowLab: recording in 15 s" to
   pass.
2. **Open the macro window** (`/macro`). In the **General** (account) tab,
   make one new macro named `wowlab acct` with the text `/stand`.
3. In the **character** tab, make two new macros **in this order**:
   - first `zz wowlab one`, text `/sit`;
   - then `aa wowlab two`, text `/kneel`.

   Pick any icon. If the character tab refuses a new macro, write down the
   count it stopped at (that count is the cap, which the next session
   needs), make `aa wowlab two` in General instead, and say so in the
   report.

   Note, from top-left, which position the window shows each of the three
   in its tab and how many the tab holds (for example, illustrative only,
   "acct: 5th of 5"). If the window sorts by name, `aa wowlab two` comes
   before `zz wowlab one` although you made it last.
4. **Optional, if your client allows it** (the names and `/dump` on Forever
   are from memory, **[verify]**; if a line errors, skip this step). Type in
   chat, one at a time, and write down what each prints:

   ```
   /dump GetMacroIndexByName("wowlab acct")
   /dump GetMacroIndexByName("zz wowlab one")
   /dump GetMacroIndexByName("aa wowlab two")
   /dump GetNumMacros()
   /dump MAX_ACCOUNT_MACROS, MAX_CHARACTER_MACROS
   ```

   These are the client's own indices. They settle which index each macro
   has; the drag step shows whether a slot's id is that index.
5. **Drag each onto an empty button of the main action bar** (the bottom
   bar, on its first page): `wowlab acct` on one, `zz wowlab one` on
   another, `aa wowlab two` on a third. Note the three button numbers,
   counting from the left (button 1 is the one bound to the `1` key). If the
   main bar has no three free buttons, use another bar and name it.
6. Don't click the macros, and don't change bars or forms after this.
7. **Log out cleanly**, then exit the client. The files are only complete
   after logout.

## 2. Read what the client wrote (client closed)

1. Are the new macros in the files? Run the `char macros` command of step
   0.4 again (it is also step 2.3). Write down the two counts and
   times from its first lines, and whether each time changed. Then find
   the three new macros in its list: each is printed as `account macro <n>
   "wowlab acct"` or `character macro <n> "…"`, where `<n>` is its
   position in the file. Write down each `<n>`, or "missing".
2. What the record says the three buttons hold. This prints one row per
   key; the rows for your three buttons' keys (`ACTIONBUTTON<n>` for main
   bar button n) say "runs macro <id>":

   ```bash
   uv run wowlab char binds --character <Character>
   ```

   Write down the id for each of the three buttons. If a button has no key,
   its slot is in the list "Filled slots no key reaches" instead, with the
   same words.
3. Where `char macros` puts them:

   ```bash
   uv run wowlab char macros --character <Character>
   ```

   For each of the three macros, copy its `placement:` line and any
   `may sit at slot` line under it.

If the new macros are missing from the files in step 2.1, the client did
not rewrite the file at logout. Log in once more on the same character,
wait for the recording line, log out, exit, and repeat section 2. Say in the
report that you needed the second round.

## 3. Optional: a capture for the fixtures

A captured `WowLab.lua` and `macros-cache.txt` let the next session grade
the `confirmed` rule on a real file (L8). The tool copies files out of the
install and replaces every name it finds with a pseudonym. It never writes
to the install. It copies `macros-cache.txt` and `bindings-cache.wtf` too,
and it does **not** rewrite macro names or bodies: read `macros-cache.txt`
in full, and name nothing in a macro you would not publish. Only the files
the next session picks are committed, and reviewers check them. With no
`--character` it takes the character you played last.

```bash
git switch -c m14/macro-place-capture origin/main
```

Dry run first. Read what it plans to copy:

```bash
uv run python scripts/lab_capture.py --root "$HOME/Games/World of Warcraft" --out lab/core/tests/fixtures/incoming/m14-macro-place --flavor _classic_beta_ --kind _classic_beta_=forever --sv WowLab.lua --own-guid "$(cat ~/.config/wowlab/own-guid)" --dry-run
```

Then the real run, which is the same command without `--dry-run`. If the dry
run names a flavor folder other than `_classic_beta_`, use that name in both
commands. If a run says `REFUSED`, don't edit anything; paste the refusal.
Stop there and do not commit.

## 4. Put things back

Delete the three macros in game (the macro window's Delete button), or
leave them; they do nothing unless clicked. Restoring the files from the
snapshot is not needed, and a restored `macros-cache.txt` may be replaced
by the server at the next login when macro sync is on.

## 5. What to report

One row per macro. Values are what you saw, not what you expected.

| Macro | Tab | Position in the macro window | Position `<n>` in `char macros` after logout (or "missing") | `/dump` index (optional step 1.4) | Bar and button | Id `char binds` shows for that button | `char macros` placement line(s) |
|---|---|---|---|---|---|---|---|
| `wowlab acct` | General | | | | | | |
| `zz wowlab one` | character | | | | | | |
| `aa wowlab two` | character | | | | | | |

Also say:
- the macro counts and written times of step 0.4 and of step 2.1, and whether each
  time changed at logout; if the character tab refused a macro, the count it stopped at;
- what `GetNumMacros()` and `MAX_ACCOUNT_MACROS, MAX_CHARACTER_MACROS`
  printed in step 1.4, or that you skipped it (and the error, if one came);
- whether you needed the second login of section 2;
- the flavor and client build (`wowlab doctor` prints both), and whether
  `synchronizeMacros` is on (`doctor` prints it);
- whether you did the capture of section 3, with the tool's summary lines.

## What the next session does with it

It compares each button's id with the macro's place in the files. If the
id on `wowlab acct`'s button is that macro's position in the account file,
and the ids on the other two buttons are 120 plus each macro's position in
the character file, the candidate numbering holds as `char macros` uses it.
If you did step 1.4, it also compares the `/dump` indices with the file
positions, and the button ids with the `/dump` indices; if `aa wowlab two`
went to General because the character tab was full, it is checked against
the account numbering instead. If the ids follow the
macro window's order or the hex ids instead, the rule is that one. The
result goes into `docs/LAB_FORMATS.md` §7 as a dated amendment, with the
capture committed if you made one, and a ticket then builds the rule that
reports `confirmed`. If the ids fit no rule, the placement stays
`unconfirmed`, and the amendment records the ids seen.
