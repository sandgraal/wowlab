# `docs/LAB_PLAN.md`, Appendix A: §6.10's earlier text and its dated amendments

Moved here from the end of `docs/LAB_PLAN.md` on 2026-11-19, unchanged, because the plan had
reached the repository's 512 KB limit for one file. A reference to "Appendix A", "A.1" to
"A.8", or to an older wording of §6.10 resolves here.

§6.10 was rewritten as one current section on 2026-11-12 (M16-110), with no rule changed. This appendix keeps what it replaced, unchanged: the base text of M10-11 (A.1) and each dated amendment in the order it was added (A.2 to A.8). The one exception is the dated 2026-11-12 sentence on `undone_as` under A.8's (a1), added by PR #500 on the same day and kept here so both texts carry it. Every owner decision keeps its date and its words here. If §6.10 and this appendix read differently, that is an error in §6.10: the latest dated amendment here governs until §6.10 is corrected.

How an older reference resolves:
- "§6.10" quoting a phrase of the original text (for example "open a journal entry under the store" or "of the default subtrees") is A.1.
- "§6.10 item 1" to "item 4", "the 2026-09-22 amendment", "clarified 2026-09-22", and "reworded" or "added 2026-09-23" are A.2.
- "amended 2026-09-28" is A.3; "amended 2026-10-17" is A.4; "amended 2026-10-22" is A.5.
- "2026-11-10 (a)", "(b)" and "(c)" with no ticket named are A.6; the M16-84 amendment of 2026-11-10, with its (a) to (d), is A.7. "2026-11-10 (d)" is always A.7, with or without a ticket named.
- "§6.10 (a)", "(a1)", "(a2)", "(b)", "(e)" and "(e1)" with no date, or dated 2026-11-11, are A.8. The 2026-11-12 sentence on `undone_as` under (a1) is A.8 too (M16-69, #490).

### A.1 The base text (M10-11)
The only module that writes into an install (L2, ADR-0021).

```python
with guard.transaction(flavor, label="try new keybinds") as tx:
    tx.write(rel_path, data)  # bytes
    tx.delete(rel_path)
    tx.restore(snapshot_id, paths=None)
```

On enter: refuse if `process` reports the client running or unknown; take a
pre-write snapshot of the default subtrees (deduplicated, so cheap); open a
journal entry under the store. On each operation: resolve the path, refuse
anything outside the allowlist (`WTF/`, `Interface/AddOns/`, `Fonts/`, loose
`Interface/` overrides), refuse symlink escapes and case-collision tricks,
refuse `Data/`, executables, `.build.info`, `.flavor.info`, `.product.db`
and anything at the install root; write to a temp file in the same
directory, fsync, atomic replace. On exit: close the journal with the list
of paths touched and their before/after hashes. On exception: roll back
from the pre-write snapshot and record that it did.

`guard.undo()` restores the pre-write snapshot of the most recent
transaction. `guard.history()` lists transactions.

A dry-run mode returns the plan (paths, before/after hashes, bytes) without
touching anything; the CLI prints the plan and asks unless `--yes`.

### A.2 Amended 2026-09-22 (items 1 to 4, with the 2026-09-22 clarification and the 2026-09-23 rewording and additions)

*Amended 2026-09-22 (owner decisions after the M10-11 reviews, worded
after the PR #49 security review; tickets M10-16T and M10-16):*

1. **One writer at a time.** `transaction()` (and so `tx.restore`) and
   `undo()` hold two exclusive, non-blocking OS advisory locks for their
   whole duration: the store lock on `<store>/lock`, and the install lock on
   `<user data dir>/locks/<key>.lock`, never inside the install (L1).
   `<key>` is the SHA-256 hex of `"<st_dev>:<st_ino>"` of the install root
   as guard validated it: the directory's identity, not its spelling, so a
   symlinked, case-variant, Unicode-variant or firmlinked spelling of one
   install maps to one lock. guard takes the user data directory from
   `wowlab_core.snapshot`; it does not import `platformdirs`.
   - **Order.** `transaction()` validates the flavor and runs the
     store-overlap check (lock paths included), then takes the store lock,
     then the install lock, then runs the client check. `undo()` reads the
     most recent record once without a lock only to find the flavor,
     validates it and runs the store-overlap check, then takes the store
     lock (never creating `<store>/`), re-reads the journal and plans only
     from what it reads under the lock, then takes the install lock for the
     flavor the re-read record names (a different install by identity
     raises `GuardError`) and runs the client check. `undo()` on a store
     directory that does not exist raises `GuardError` without creating it.
     The locks are released on exit, including on an exception, and the OS
     releases them when the process ends.
   - **Mechanism.** `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on POSIX and
     `msvcrt.locking(fd, LK_NBLCK, 1)` on one byte at offset 2^30 (1 GiB)
     on Windows, far past the end of the empty lock file, so no ordinary read
     of the file overlaps the mandatory lock (reworded 2026-09-23 after the
     `lab (windows)` run on PR #59: a lock at offset 0 made reads of the store
     fail with a permission error; SQLite uses the same technique; the same
     offset is used to unlock, nothing is ever written through the
     descriptor (so the file stays empty and is never extended), and the
     offset is fixed, since guards locking different offsets would not
     exclude each other); `lockf` and
     `fcntl(F_SETLK)` are not used. A process also refuses, without asking
     the OS, a lock it already holds, so a nested transaction raises
     `GuardBusyError`. `undo()`'s own transaction runs under the locks
     `undo()` already holds; it does not take them again. A held lock raises `GuardBusyError` (a `GuardError`)
     and nothing is written or journaled; any other failure to open or lock
     (unsupported on the volume, permission, I/O) raises `GuardError`, never
     `GuardBusyError`. guard never proceeds unlocked.
   - **Lock files.** Opened with `O_CREAT | O_RDWR | O_NOFOLLOW` (on Windows,
     refused if a reparse point), never truncated, written or deleted; each
     must be a regular file by `fstat` with a link count of 1 (a hard link
     to another file is refused). The lock files and `locks/` are part of
     the store-overlap check. *Added 2026-09-23 (M10-16 security review):*
     the store directory, `<store>/lock`, `<user data dir>/locks/` and the
     install lock are each refused, before anything is created, if they are
     inside any install, not only the one being written. Each is resolved
     (following links and junctions), and it and every existing ancestor are
     examined: a directory holding an entry named `.build.info` or
     `.flavor.info` (of any kind, by `lstat`) is an install, and an error
     other than not-found while examining one counts as an install. On enter, a
     journal that cannot be read in full (an I/O error, or any record
     `history()` would refuse as damaged) raises `GuardError` under the locks
     and before the pre-write snapshot, with nothing written or journaled
     (not in a dry run), since a change `undo()` could not reverse
     must not be written. A path taken from a journal record or a snapshot manifest is
     looked up (`stat`, `lstat`, `samefile`, `resolve`) only when it is a
     local absolute path: absolute, not beginning with `\\` or `//`, and on
     Windows with a drive letter. Anything else is treated as another install
     without being looked up, and `undo()` refuses a most recent record whose
     flavor or install path is not such a path.
   - **Dry run.** A dry run takes the same locks; creating the lock files and
     their directories is the only thing it writes, never inside the install.
   - **Scope.** The exclusion covers processes of one OS user sharing one
     user data directory on one machine. Other OS users, another
     `XDG_DATA_HOME`, or another machine on a network install are not
     excluded. Within that scope, journal sequence numbers are unique per
     store and `undo()`'s "most recent transaction" is well defined.
2. **Leftover temp files are cleaned up.** Before creating a temp file
   inside the install, guard records its flavor-relative path in the
   journal, written and fsynced first, like the before-hash. On enter, after
   the pre-write snapshot and the new journal record are written (not in a
   dry run), `transaction()` and `undo()` consider each temp path named by an
   earlier record whose install root and flavor folder are this flavor's (by
   identity), limited to records of this flavor from the most recent one of
   this flavor whose cleanup finished, that record included (its own temp
   paths and its `temps_left` are considered again). A
   temp path is removed only if its last component fullmatches
   `\.wowlab-[0-9a-f]{32}\.tmp` exactly as the directory lists it, the path
   passes the write rules (allowlist, the walk that follows no link,
   junction or reparse point, the case/Unicode-collision and short-name
   refusals), and the target is a regular file; the directory chain is
   re-checked before and after the unlink as for any delete. Each removal is
   recorded in the new record and fsynced before the unlink; a removal whose
   unlink fails is moved from `temps_removed` to `temps_left` in the same
   record. A path that
   fails any check, or whose removal fails, is left alone and listed; it
   does not stop the transaction. A named temp path is looked up only after
   it passes the write rules, and only by the walk that follows no link,
   junction or reparse point; a path that fails a rule is listed in
   `temps_left` without being looked up. A path that passes them and that
   the walk finds absent (its last component or a parent directory missing)
   is listed in neither `temps_removed` nor `temps_left`; a link, junction,
   reparse point or directory at the name is not absent. The cleanup never
   deletes anything else.
3. **A file changed after the pre-write snapshot is refused** (not in a dry
   run, which has no pre-write snapshot). At the first touch of a path in a
   transaction (write, delete or restore), guard compares the disk with the
   pre-write snapshot's entry for that path (content hash and kind, or
   absent against present). If they differ, the operation raises
   `ChangedSinceSnapshotError` (a `GuardError`) naming the path and telling
   the caller to retry; nothing is written for that path, every later
   operation in the transaction raises `GuardError` without touching the
   disk, and the transaction rolls back on exit and cannot commit, even if
   the caller catches the error. After the temp file is written and fsynced,
   and before each replace or unlink, guard re-reads the target and requires
   the hash it last read or wrote there (for a create: that it is still
   absent), then checks identity, size and mtime by `lstat` as the last step
   before the call; a mismatch raises the same error. Where the platform
   offers a rename that does not replace (`os.rename` on Windows), a create
   uses it, and a create whose rename finds the target present raises
   `ChangedSinceSnapshotError`. The window between the last check and the call is not closed
   and is documented like the module's existing residual window. Rollback
   and undo therefore only ever need bytes the pre-write snapshot holds.
   *Clarified 2026-09-22 from the M10-16T graders:* "the hash it last read
   or wrote there" is guard's own record of the path within this
   transaction (the hash of its last write there that landed, else the hash
   read at its first touch), not a fresh read at the start of the
   operation. So a path this transaction already wrote, then changed by
   something else, then touched again, raises `ChangedSinceSnapshotError`,
   with the same consequences as a refusal at first touch: nothing more is
   written for it, every later operation raises `GuardError` without
   touching the disk, and the transaction cannot commit. On rollback each
   touched path is compared with that record. A path whose disk matches it
   is put back to its pre-transaction content; a path whose disk already
   holds its pre-transaction content needs nothing and counts as rolled
   back; any other path is left as it is, named in the record's note and in
   the error raised at exit, and the record ends `rollback_incomplete`.
   Rollback replaces or removes only content whose hash is guard's own
   record for that path; it never overwrites content that no snapshot holds
   and this transaction did not write. A path whose only operation was
   refused (at first touch or at the re-check) is compared the same way
   with what guard read there: it keeps whatever is on disk, makes the
   record `rollback_incomplete` only if the disk no longer matches that
   read, and is left as it is by an `undo()` of the record. `undo()` of a
   `rollback_incomplete` record restores its other journaled paths from the
   pre-write snapshot as for any record, replacing what rollback left; its
   own pre-write snapshot holds those bytes, so a second `undo()` puts them
   back.
4. **Public surface.** `GuardBusyError` and `ChangedSinceSnapshotError` are
   exported from `wowlab_core.guard`, and so is
   `guard.store_lock(store: Path | None = None)`, a context manager that
   takes the store lock alone (same mechanism, same in-process refusal, the
   same lock-file opening rules: `O_NOFOLLOW`, regular file with one link,
   never truncated, written or deleted; it has no install of its own to
   compare with, but a store directory inside any install is refused as
   above (reworded 2026-09-23 after the M10-16 security review; the
   earlier wording exempted it and would have let it create a lock file
   under `Data/`); `GuardBusyError` if held; a store directory that does not exist raises `GuardError` and nothing
   is created). `HistoryRecord` gains
   `temps_removed`
   and `temps_left` (flavor-relative paths). The journal format becomes 2;
   format-1 records read as naming no temp paths and as having run no
   cleanup.

### A.3 Amended 2026-09-28

*Amended 2026-09-28 (M10-17T/M10-17, from the M10-14 security and code
reviews):* `guard.store_lock(store=None, create: bool = False)`. With
`create=True`, the inside-any-install check runs first, on the store path
and every ancestor, through links and junctions (an ancestor that cannot be
examined counts as an install); only then are the store and any missing
parents created, and the lock taken as above. Nothing is created anywhere
when it refuses. `create=False` keeps the behaviour above: a missing store
raises `GuardError` and nothing is created. `guard.undo(*, store=None,
expected_id: str | None = None)`: with `expected_id`, the journal's last
record id is compared under the store lock, and a mismatch (another
transaction committed after the caller read the journal, or an id that names
no record) raises `GuardError` naming an id, with nothing in the store or the
install changed. Without it, `undo` behaves as before. The CLI passes both
(M10-17): the first `snap create` holds the store lock like every later one,
and `undo` passes the id of the record it showed the owner.
Out of scope (conductor, 2026-09-28, from the M10-17T security review): a
process running as the owner that swaps a link or plants a marker inside the
owner's user data directory between the check and the mkdir. It already has
the owner's rights, and some window always remains after the last check. The
check-before-create order stops every mistake short of that; an
implementation may re-check after creating, or create relative to directory
handles, as defence in depth.

### A.4 Amended 2026-10-17

*Amended 2026-10-17 (M13-15, long paths on Windows):* every lookup, read and
write the gate makes in an install (the walk, the temp file beside the
target, the replace or rename, an unlink, a folder it creates or removes)
passes a path past the plain limit to the system in extended-length form
(`wowlab_core.longpath`, as in §6.9), so a file deep in an install is
written, restored and undone with `LongPathsEnabled` off. That includes a
target whose plain path is at the limit, where only the temp file beside it
is past it. The journal, the pre-write manifest and every message name the
plain path. A flavor handed to `transaction()` in `\\?\C:\...` form is taken
by its plain spelling, so `undo()` and restore work on what it journals. A
flavor that resolves to anything but a local absolute path (a network path,
`\\?\UNC\...` included, or a volume with no drive letter) is refused on
enter with nothing written or journaled (on Windows a network spelling is
refused before any lookup; on POSIX, where `//x` is the local `/x`, only
the resolved path is held to it): `undo()` and restore accept only a
local absolute root (item 1), and a change `undo()` could not reverse is
never written.

### A.5 Amended 2026-10-22

*Amended 2026-10-22 (M16-03T review; text from the outside evaluation, 4(a) item 2, with the conductor's rulings on the review's open questions):*

Restore into another target. `tx.restore(snapshot_id, paths=None, *, into=Target)` maps every path the snapshot holds by its path relative to the flavor folder, and does so only when the caller names the target. The plan names both folders. The target gets its own pre-write snapshot, and the allowlist, link and client checks apply to it as to any write. Without `into`, a foreign snapshot is refused as before, and the refusal names the option (`--into-flavor` or `--into-root`). Folder names come from the manifest and from discovery, never from a constant (L6). The manifest's `flavor_folder` is untrusted input. Before it is used to map paths it must be exactly one path component: not empty, `.` or `..`, with no separator, drive or NUL. Otherwise the restore is refused with nothing written.

The account rules below apply only when the snapshot holds at least one account folder. A snapshot with none, such as a CVar-only profile or a flavor captured before its first login, restores without a mapping and creates no account folder. A mapping given for such a snapshot is refused. Account folders are mapped automatically only when each side has exactly one. Otherwise the player gives the mapping, and these mappings are refused with the accounts named and nothing written:
- no mapping, when either side has more than one account folder;
- no mapping, when the target has no account folder;
- two source folders mapped to one target folder;
- a mapping that leaves out a source account folder the snapshot holds. This applies to a `paths=` restore too: the mapping covers every account folder the snapshot holds, not only those the named paths touch.

A mapping may name a target account folder that does not exist yet; the gate creates it like any allowlisted folder. A mapping value must be one plain folder name. A sole target account folder that is a link is refused.

`paths=` names files in the snapshot's own spelling, the one `snap show` lists. A path in the target-side spelling of a mapped account folder is refused, and is never planned as a delete.

The target root must not overlap the snapshot store in either direction (L1). A client running from the target refuses the write, whatever the source install's state.

### A.6 Amended 2026-11-10 (M16-13, M16-66, M16-70)

*Amended 2026-11-10 (M16-13, M16-66, M16-70; #416, #432, #430):*

(a) **Journal record names.** A record is `journal/<8 ASCII digits>-<8 lowercase hex>.json` and nothing else (`\A[0-9]{8}-[0-9a-f]{8}\.json\Z`). The loader ignores any other name in `journal/`, including one spelled with other Unicode digits. `doctor` and `undo` never offer it.

(b) **Private store folders.** The gate creates the store root and `journal/` with mode 0700 in the `mkdir` itself, so they are never wider than 0700, whatever the umask. M16-78 asks the owner whether the user data directory's other folders follow.

(c) **`doctor` and the journal.** When the most recent record is still `open`, meaning an interrupted change, `doctor` reports it with exit 1 and the exact `wowlab undo --expect <id>` line.

A `.wowlab-<32 hex>.tmp` file is explained by its own file-map row (`docs/LAB_FILE_MAP.md`).

### A.7 Amended 2026-11-10 (M16-84)

*Amended 2026-11-10 (M16-84T, M16-84; #453):* **The files a rollback left alone.**

(a) **On the record.** `HistoryRecord.paths_left: tuple[str, ...]` lists the files the rollback of a `rollback_incomplete` record left as they are (item 3 as clarified 2026-09-22): a path changed by another program, a path the system refused to put back, and a refused path whose disk moved since guard read it. Each is relative to the flavor folder and spelled as `paths[].path` spells it, once; order is not part of the contract. A directory the rollback could not remove is not listed (it stays in the note). The list is empty for every other state, and for a record written before M16-84: the note is never parsed.

(b) **In the journal.** The record file carries the list under `paths_left`, and the paths whose only operation was refused (which `paths` does not name) under `paths_refused`. On read, each entry of either must pass the allowlist's lexical check, appear once, and, for `paths_left`, be one of the record's `paths` or `paths_refused`, on a `rollback_incomplete` record only. Anything else is a damaged record: `history()` raises `GuardError`.

(c) **The format stays 2.** Both keys are written only when they are not empty, so a record with nothing left alone or refused is what the M16-71 gate wrote. A gate from before M16-84 forbids unknown keys, so it refuses a record that carries either key as damaged rather than reading it without the list. A format-1 record carries neither.

(d) **`doctor`.** The `rollback_incomplete` problem (M16-71) names each file in `paths_left`, sorted and relative to the flavor folder, and names that folder once (the last component of the record's `flavor_path`, never a constant). It groups the files by what doctor reads there now. That read is read-only and is made only when the record's flavor is one of the install's discovered flavors, the way a snapshot reads (no link followed); the journal's own path is never opened.

A file in `paths` holds the change's `after` ("still holds wowlab's change", or "is still deleted, as wowlab's change left it" when the change deleted it), its `before` ("already holds what was there before the change", or "is gone again, as before the change" when the change created it), something else ("another program, such as the game when it saves your settings or addon data, has changed X since wowlab's change", or "has deleted X since wowlab's change"; the wording holds whether the change wrote or deleted X), or could not be read ("wowlab could not put back X, which holds either wowlab's change or what another program, such as the game when it saves your settings or addon data, has written to it since"). No timing is stated: SavedVariables are written at logout or /reload, `Config.wtf` on exit and on some setting changes. A file in `paths_left` but not in `paths` was never written by the change, because its only operation was refused: "wowlab did not write X, which another program changed while wowlab was working, and undo leaves it as it is". Groups are joined with "; ", and no group holds a semicolon of its own.

The command's clause says what `undo()` does: it puts back every file in the record's `paths` (every file the change wrote or deleted) whose bytes differ from the pre-change bytes, including what the game has saved to them since the failure. It keeps a copy, so running undo again next, before the player plays, brings them back (the wording M16-92 gives undo's plan). `wowlab undo --expect <id>` stays last, with one exception. A record whose `paths` and `created_dirs` are both empty (its only operations were refused before any folder was made) wrote nothing, and `undo` of it records nothing either (it says the change "changed no files" and returns), so offering the command would not clear the problem. Its problem opens "the last change wowlab made failed before it changed any file" and ends "wowlab changed no file, so there is nothing for undo to put back, and undo leaves this record as it is; wowlab doctor stops reporting it after wowlab's next change.", with no command. A record with no `paths` but with `created_dirs` (a write refused after the gate made its folders, whose removal another program's file then blocks) is not that exception: `undo` removes those folders if they are empty and records itself, as M16-86's plan says. Its problem opens "the last change wowlab made failed after it created folders and before it changed any file", names the files as above, and ends "undo removes the folders it created if they are empty and records the undo, and wowlab doctor stops reporting this change: wowlab undo --expect <id>". `--json` gains `store.last_transaction.created_dirs`, a count. A record that lists no file gets the same clauses without names.

`--json` gains `store.last_transaction.flavor_folder` and `store.last_transaction.paths_left`, a list of `{path, now, exists}`: `now` is one of `wowlab_change`, `before_change`, `changed_since`, `unknown` or `not_written`, and `exists` is whether the file is there now (null when it was not read). The problem and the list hold names as stored, and only the output escapes them. The text output escapes control, format and separator characters as `\xNN` (a real file name may hold a bidi mark), and `--json` is ASCII. So `--redact` sees each name unescaped and replaces account, realm and character folders in those paths like any other. Those names come from the install, the snapshots and the paths of every journal record, since a folder the transaction created is in no snapshot.

### A.8 Amended 2026-11-11, with the 2026-11-12 sentence on `undone_as`

*Amended 2026-11-11 (owner decisions in session; M16-69, M16-78, M16-90, M16-100T, M16-108T):*

(a) **A change killed between the temp cleanup and its snapshot changed nothing.** Item 2's order changes: "after the pre-write snapshot and the new journal record are written" now reads "after the new journal record is written and before the pre-write snapshot is taken". The record still comes first, because each removal is journaled in it and fsynced before its unlink; how a record names a snapshot that does not exist yet is the implementer's to design (M16-69). If wowlab is killed after that cleanup and before the snapshot exists, the record means that the change touched no player file. `undo` closes the record without restoring anything and never brings a removed temp back. `doctor` says the change stopped before it touched anything. Snapshots stay lossless: a temp is left out of a snapshot because it was removed before the snapshot was taken, never by a name filter.

(a1) **A killed undo leaves the change it was undoing visible.** *Owner decision, 2026-11-11.* If the change killed between the cleanup and its snapshot was itself an `undo` of an earlier record D (D `open` or `rollback_incomplete`), then closing the killed record does not settle D. After that close, `doctor` reports D again as it did before the undo started, with D's own `wowlab undo --expect <D id>` line, and exits 1 until D is undone. `wowlab undo` offers D again. Nothing about D is lost or hidden because an undo of it was interrupted. *Undo target (owner decision, 2026-11-11, "1 yes"):* `wowlab undo` passes over the closed record of a killed `undo` that changed no file and offers D. The rule is precise as follows.
- **An undo names what it undoes.** Every record `undo()` writes carries a new field `undoes`, the id of the record it undoes. `history()` refuses, as a damaged record, an `undoes` that is not the id of an earlier record of the same flavor. The field is additive. Like M16-84's fields, an older gate that refuses unknown keys stops at it, which is accepted.
  *Added 2026-11-12 (M16-69, #490, round 3):* every undo record journals the place guard resolved, as every other record does. When the undone record's own `[install_root, flavor_path]` spelling differs from that place (an install moved behind a link), the undo record also carries `undone_as`, that spelling, so the same-flavor check on `undoes` can still match it. `undone_as` is written only on an undo record and only when the spellings differ, never as null, and it is read for nothing else: it is never a path to change or display. An older gate that refuses unknown keys stops at it, as with `undoes`.
- **Which records are passed over.** A record K is passed over when all of these hold:
  - it has `undoes` set;
  - it was killed between its cleanup and its pre-write snapshot, so no snapshot of it was ever taken (amendment (a));
  - it has been closed.

  If closing K appends a record, that record names K in `undoes`, changes no file, and is passed over with K. An undo that completed normally, with or without changing files, is never passed over. That rule is what stops `undo` from offering D again after D was really undone.
- **The undo target replaces "the most recent record".** The target is the newest record that is not passed over. This replaces "the most recent transaction" and "the most recent record" for `undo()` in this section's opening sentence ("`guard.undo()` restores the pre-write snapshot of the most recent transaction"), in item 1, in the "refuses a most recent record whose…" rule and in the "well defined" sentence above. It does the same for `undo` in §16 ("undoes the newest journal record at run time"). It also replaces the record that 2026-11-10 (c) has `doctor` report.
- **`--expect` keeps its protection.** It must name the target's id. Any record newer than the target that is not passed over is a mismatch and raises, as in the 2026-09-28 amendment.
- **`doctor`** reports D as the change to undo, and K as interrupted before it changed anything.

(a2) **Every closed record that never took a snapshot is passed over.** *Owner decision, 2026-11-11 (M16-69 review, M16-108T): "Skip it" and "Skip all, show D".* This widens (a1)'s "Which records are passed over": the first condition, "it has `undoes` set", is dropped. A record K is now passed over when both of these hold:
- no pre-write snapshot of it was ever taken;
- it has been closed.

It does not matter how K came to be closed:
- K may be a `transaction` or an `undo` killed between its cleanup and its snapshot (amendment (a)), and later closed by `undo`.
- The pre-write snapshot may have failed in the same run (for example a full disk or an unreadable file), and the gate then closed K itself.

Such a record changed no player file: at most it removed leftover temps. So skipping it hides no change, and it never locks `undo` out of the changes before it. If closing K appends a record, that record names K in `undoes`, changes no file, and is passed over with K, as in (a1).

*What "never took a snapshot" means (2026-11-11, from the M16-108T security review; this makes the owner's "changed no player file" exact):*
- It is something the gate writes positively into the record: K's own field says that no pre-write snapshot was taken. A snapshot id that names no manifest in the store does not count. A record whose manifest has gone missing (deleted by hand, a damaged disk, a journal restored without `snapshots/`) is not passed over. `undo` keeps refusing it, as before, because its snapshot cannot be read.
- K's `paths` and `created_dirs` must both be empty. A record that lists a path or a created folder is never passed over, whatever it says about snapshots.
- A record that closes K is passed over only if it too has no `paths`, no `created_dirs` and no snapshot.
- Any number of such records in a row are passed over.
- `undo` closes a record without restoring anything only when that record would be passed over once closed: no snapshot, no `paths` and no `created_dirs`. Any other record without a readable snapshot is refused as before, and nothing changes.

What follows from this:
- **A killed transaction.** After its record K is closed, the target is the newest record before K that is not passed over. If that record D is still `open` or `rollback_incomplete`, `doctor` reports D as (a1) describes for a killed undo. It gives D's own `wowlab undo --expect <D id>` line, names K as stopped before it changed anything, and exits 1 until D is undone. If D is settled, `doctor` has no problem.
- **A failed snapshot.** The change before it stays the target, as when a failed snapshot journaled nothing.
- **What is unchanged:**
  - a K that is still `open` is the target, until `undo` closes it;
  - a record that took a pre-write snapshot is never passed over;
  - an undo that completed normally is never passed over;
  - `--expect` must name the target.

(b) **The whole user data directory is private.** Every folder wowlab creates under the user data directory is made 0700 in the `mkdir` itself, whatever the umask. That covers the store root and `journal/` (2026-11-10, (b)), the parents made on the way to a missing store, `locks/`, and each module's folder (`gamedata/`, `looks/`, `pages/`, `db2lake/`). The parents wowlab makes on the way to a store given explicitly outside the user data directory are made 0700 too. A system folder above the user data directory (such as `~/.local/share`) is not wowlab's, and this rule does not set its mode. This extends 2026-11-10 (b) to every module.

(e) **A flavor path with a control or format character is refused.** *Owner decision, 2026-11-11 ("2 yes").* The forbidden characters are exactly the ones the CLI's output escaping covers:
- C0 controls and DEL;
- C1 controls;
- the pinned table `svmerge._FORMAT_CHARACTERS` (Unicode format, line- and paragraph-separator characters as of Unicode 15.0, U+2065 included);
- lone surrogates.

The path checked is the one that would be journaled: the resolved `flavor_path`, which includes the install root. A clean symlink whose target holds such a character is refused too.

`transaction()` (a dry run included) and `undo()` refuse a flavor whose resolved path holds any of them, with `GuardError`, before any snapshot, lock-held write or journal record. `history()` refuses a record whose `flavor_path` holds one, as a damaged record. The gate never writes a record that `history()` would refuse, so a player whose install path holds such a character gets the refusal at the first change, never a lockout after it. Relative paths inside the flavor are unchanged: a real file name may hold a bidi mark, and only the output escapes it (the M16-84 amendment of 2026-11-10, item (d)).

(e1) **A record that names a temp outside the flavor is damaged.** *Owner decision, 2026-11-11 ("A"), from the M16-90 implementation.*
- **The conflict.** Two graders on `main` disagreed about a journal record whose `temps` or `temps_left` names a path that is not a plain flavor-relative name: outside the allowlist, at the install root, under `Data/`, in another flavor folder, outside the install, absolute or UNC.
  - M16-69T, item 2 of the 2026-09 owner decisions, required the next change to carry on and list that path in its own `temps_left`.
  - M16-90T required `history()` to refuse the record.
- **The ruling:** refuse. `history()` refuses such a record as damaged, like any other field that fails the write rules, and every command that reads the journal stops there. Under ADR-0037, `doctor` gives its generic answer: the snapshots are kept, and you can restore one. Only a hand-edited or corrupted journal can hold such a record, because the gate journals only the temps it creates inside the flavor.
- **What item 2 still requires.** The cleanup removes only regular temp files that match the pattern, that the gate wrote and that sit inside the allowlist. A link, a directory, another name or a case variant inside the flavor is still left alone and listed in `temps_left`, and the change carries on. Only the paths that are not plain flavor-relative names change: they now make the record damaged.
