# Bindings persistence — owner runbook: does an edit to `bindings-cache.wtf` survive login?

The twin of `docs/handoffs/M13-07.md`, written 2026-10-18 at the owner's
request so both tests run in one play session. About twenty minutes: two
logins on one character, one small edit before each, and a few commands.
Nothing here is committed by you. You run the steps, then tell the next
conductor session "persistence tests done" with the tables at the end of
this file and of the M13-07 runbook filled in. The conductor writes the
result into `docs/LAB_FORMATS.md` §6 as a dated amendment, and only then
is a bindings writer built (`docs/LAB_PLAN.md` §16.5 F1 and F2:
`wowlab binds coach --apply`), in the form your result allows.

The question is one the Lab cannot answer by reading files: when the client
is closed and a key binding is changed in `bindings-cache.wtf`, does the
next login keep the change, or does the server put its own copy back? The
file map says "server may replace" for this file and `macros-cache.txt`
alike (`docs/LAB_FILE_MAP.md`). The `synchronizeBindings` CVar may decide
it, so the test runs once with it on and once with it off. Neither value is
a safe default to assume.

Two files can hold bindings. The account file,
`WTF/Account/<ACCOUNT>/bindings-cache.wtf`, is the one the client keeps for
every character. The character file,
`WTF/Account/<ACCOUNT>/<Realm folder>/<Character>/bindings-cache.wtf`,
exists only when character-specific key bindings are switched on for that
character (the Forever capture had none; `docs/LAB_FILE_MAP.md`). This
runbook edits the **account** file. If you also play with
character-specific bindings on, say so in the report and, if you have
time, repeat run A on the character file as run D.

Every command is pasted from your wowlab folder. Use the flavor you play;
name it in the report. If the install has more than one flavor folder, add
`--flavor <folder>` to each `wowlab` command, as `wowlab install show` lists
it.

## 0. Before anything

1. Quit the game client. Battle.net can stay open.
2. Take a snapshot, so the original file can always come back (one
   snapshot serves both runbooks if you take it before either edit):

   ```bash
   uv run wowlab snap create -m "before the macros and bindings persistence tests"
   ```

3. Record the current sync setting. `doctor` prints the three
   `synchronize*` CVars once for `WTF/Config.wtf` and once for each
   account's `config-cache.wtf`, and the lines can disagree. Write down
   every `synchronizeBindings` line with its file (a value, or "not set
   (client default)"). Which file the client obeys is part of what this
   test finds out:

   ```bash
   uv run wowlab doctor
   ```

4. List the account's bindings so you know the file and have a baseline:

   ```bash
   uv run wowlab binds list
   ```

   Pick one binding you will recognise and can live without for two
   logins, for example a key bound to `TOGGLEBACKPACK` or to an action
   button you do not use in combat. Write down its line as `binds list`
   prints it (key, action, line number).

## 1. Run A: with `synchronizeBindings` as it is now

1. With the client closed, open the account's `bindings-cache.wtf` in a
   plain text editor (not Word; TextEdit in plain-text mode, Notepad, or
   `nano`). Keep the line endings the file already has.
2. Change the key of the binding you picked to a key that is not bound to
   anything else, for example `bind CTRL-F11 TOGGLEBACKPACK` in place of
   `bind B TOGGLEBACKPACK`. Change one `bind` line and nothing else: not
   any header line, not the order, not the line endings. Save.
3. Note the file's modification time now (`ls -l` on macOS/Linux, the
   Properties dialog on Windows). You will compare it after logout.
4. Start the game, log in on one character, and press the new key. Write
   down whether it does the action. Then open Key Bindings in the game
   menu, find the action, and write down which key the panel shows for it.
5. Log out to the character list, then quit the client fully. The caches
   are written at logout, so an offline look before this sees the old
   session.
6. With the client closed, look at the file again. Write down: is your
   edit still there; did the modification time change at logout.

   ```bash
   uv run wowlab binds list
   ```

   shows the lines too, if you prefer not to read the file.

## 2. Flip the CVar, then run B

1. Start the game, log in on the same character, and in the chat box set
   the CVar to the opposite of what `doctor` reported for the account's
   `config-cache.wtf`, then log out and quit. If it showed `1`, type:

   ```
   /console synchronizeBindings 0
   ```

   If it showed `0`, type `/console synchronizeBindings 1` instead. If it
   showed "not set (client default)", treat run A as the default, do run B
   with `0`, and, if you have time, a run C with `1`.

2. Confirm the file now records the new value:

   ```bash
   uv run wowlab doctor
   ```

3. Repeat section 1, steps 1 to 6, with a different key (for example
   `CTRL-F12`), so you can tell the two runs apart in the file.

## 3. Put things back

Restore the binding in game (Key Bindings, rebind it to the key it had), or
restore the file:

```bash
uv run wowlab snap list
uv run wowlab snap restore <id> --paths "WTF/Account/<ACCOUNT>/bindings-cache.wtf"
```

Set `synchronizeBindings` back to what `doctor` showed in step 0.3 (in
game, `/console synchronizeBindings <value>`), unless you prefer the new
value. Note that a restore of this file while sync is on may itself be
replaced at the next login; that is one more data point, so write it down
if it happens.

## 4. What to report

One row per run. Values are what you saw, not what you expected.

| Run | `synchronizeBindings` (every line `doctor` prints, with its file) | New key worked after login? | Key the panel showed | Edit still in the file after logout? | File mtime changed at logout? | Flavor, client build (`doctor` prints both) |
|---|---|---|---|---|---|---|
| A | | | | | | |
| B | | | | | | |
| C (optional) | | | | | | |
| D (optional, character file) | | | | | | |

Also say:
- whether the file's line endings were LF or CRLF (the editor usually says;
  `file bindings-cache.wtf` on macOS/Linux says "with CRLF line terminators"
  when they are), and whether that changed after logout;
- whether any line other than the one you edited changed after logout
  (compare `binds list` before and after, or `diff` the file against the
  snapshot's copy), and whether the file gained or lost any non-`bind`
  line (`docs/LAB_FORMATS.md` §6 marks header and mode lines **[verify]**);
- whether the old key still did the action after login (the client may
  have kept both);
- anything the client did that surprised you.

Nothing in this runbook writes through the Lab: the edit is yours, by hand,
on purpose, because this test is about what the client does with a file
the Lab did not write. The snapshot is there so the original always comes
back.
