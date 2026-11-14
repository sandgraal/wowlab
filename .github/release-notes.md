<!--
  Release-notes template, filled by .github/workflows/release.yml through
  scripts/release_check.py. Placeholders are TAG, VERSION and
  INSTALL, each in double braces; any other name in double braces fails the
  release before it is published. GitHub appends the list of merged pull
  requests below this text. The "Changes to `--json` output" section is the
  list ADR-0031 asks a release to carry; player text carries no ticket or
  ADR ids.
-->
wowlab {{VERSION}} runs on your own machine against your own World of Warcraft install. It is read-only by default. Every command that changes files asks first and goes through one write gate; a snapshot is taken before the change, and `wowlab undo` reverses it. The only network traffic is fetching public game tables, and nothing is uploaded.

## New in v0.2

- **The Account tab.** `wowlab ui` now opens on a home tab that lists every character you have played on this computer, in every game version you have installed, as of its last logout: level, class, race, spec, item level, professions and when you last played it. Sort by any column, filter by class, and click a character to open it. A character with nothing recorded yet says how to fix that. The same table is on the command line as `wowlab char list --summary`.
- **Take a backup now.** A button at the top of the Account tab, one for each game version you have installed, saves your settings, keybinds, macros, addons and their saved settings into wowlab's own folder, never your game folder. After a patch the tab tells you the game updated since your last backup, and **Restore this backup…** shows what putting it back would change before anything changes. On the command line it is still `wowlab snap create`, which now backs up every game version you have installed when you do not name one.
- **The Patch day card.** Also on the Account tab: the addons that are out of date for your game version or missing an addon they need, a count of the ones that are fine, and the saved data that no installed addon claims or that wowlab cannot read. The addon check is also `wowlab addons check`.
- **A character over time.** `wowlab char history` reads your backups back, plus the game's current file: one row per save, with level, item level and spec, and the gear and currencies that changed since the one before. The Character tab's **History** panel shows the same thing, with two small charts. It reads what the lab-addon (`wowlab addon install lab`) records, and the game saves that when you log out or type /reload. History appears once there are two different saves of a character, and the file in the game folder counts as one. So take a backup after you log out or /reload; a backup taken while you are still playing keeps the save from before.
- **The Mac app and the Windows app.** If you would rather not use a terminal, download the app for your computer below (see the two sections under Install). The Mac app is for Apple silicon. The Windows app is untested: it is built and started by the release checks, but no person has yet run it on a real Windows install. If you try it, please report what happens, and include the output of `wowlab doctor --redact`, which leaves out your character, realm and account names.
- **The package is now called `wowlab`.** v0.1.0 was published as `wowlab-core`. The command is still `wowlab`, and your snapshots, profiles and settings stay where they were. If you installed v0.1.0, run `uv tool uninstall wowlab-core` before you install this one.
- **`wowlab doctor` stays offline.** In v0.1.0, doctor asked wago.tools whether it lists your game version unless you added `--offline`. Now it makes no network request unless you add `--check-builds`.
- **Saved data wowlab cannot read.** `wowlab sv list --unreadable` lists every SavedVariables file wowlab cannot read, for example one cut off during a save, with why, and where reading stopped when it can say. It only reads. `wowlab sv list --orphans` lists the saved data that no installed addon declares, with its size.
- **Move a backup to another computer.** `wowlab snap export` writes one snapshot into a single file, and `wowlab snap import` reads it on the other computer.

## What is in it

It reads an install and tells you what every file is, keeps snapshots of your setup, and puts them back. v0.1.0 was the first public release; this is the whole toolset as of v0.2.

- **Find and explain:** `doctor` (finds the install; a first run says what wowlab will do and offers a first snapshot), `install show`, `tree`, `explain` (what a file is, who writes it, whether it is safe to touch, with the snapshot facts and the commands that read it), `filemap gaps`.
- **The explorer:** `wowlab ui` opens a view of the whole install in your browser. It runs the commands that read, and the ones that save only to wowlab's own data (snapshots, profiles, saved looks, the game-table cache, pages), directly. A command that takes `--yes` runs first as a plan that changes nothing, and runs for real only when you press Confirm under it, through the same write gate as on the command line. It listens only on this machine behind a token.
- **Snapshots and undo:** `snap create`, `snap list`, `snap show`, `snap diff`, `snap restore`, `snap verify`, `snap gc`, `snap export` and `snap import` (both v0.2), `undo`.
- **Settings, binds, macros, addons:** `cvar`, `binds`, `binds coach` (from your own combat log: spells cast often on awkward keys, cast with no bind, and binds never used; read-only), `macros list` and `macros check`, `addons`, `addons check` (v0.2), `sv list` (`--orphans` and `--unreadable` in v0.2), `sv dump`, `sv merge`, `addon install lab` (the lab-addon, which records your spellbook, action bars and bindings on logout).
- **Profiles and looks:** `profile save`, `apply`, `list`, `show`, `delete`; `looks import-char`, `options`, `races`, `compare`, `page`.
- **Characters and game data:** `char list` (`--summary` in v0.2), `char show`, `char gear`, `char binds`, `char macros` and `char history` (all four v0.2), `db2 builds`, `db2 fetch`, `db2 head`, `names fetch` (the one download that shows races, classes, specs and professions by name), `log tail`.
- **For bug reports:** `doctor --redact` prints output with no character, realm, account folder, BattleTag or home path in it; names it cannot find on its own, such as your guild's, go in `--redact-name`.

**Platforms.** macOS is tested by hand on the owner's install. Windows and Linux are untested: CI runs the test suite on both, but nobody has yet run the first-run flow on a real Windows install, and there is no native Linux client. See the README's platform table before relying on either.

**`--json`.** Every read command has a `--json` form backed by a Pydantic model named in its `--help` (`DoctorReport`, `ExplainReport`, `TreeReport`, `SnapListReport`, `RestoreReport`, `UndoReport`, `CVarListReport`, `CVarGetReport`, `BindsReport`, `BindsCoachReport`, `MacrosReport`, `MacrosCheckReport`, `AddonsReport`, `AddonsCheckReport` (v0.2), `AddonInstallReport`, `AddonRemoveReport`, `SvListReport`, `SvDumpReport`, `SvMergeReport`, `DiffReport`, `ProfileReport`, `ProfileListReport`, `ProfileApplyReport`, `ProfileDeleteReport`, `LookReport`, `LooksOptionsReport`, `LooksRacesReport`, `LooksCompareReport`, `LooksImportReport`, `LooksPageReport`, `CharListReport`, `CharSummaryReport` (v0.2, `char list --summary`), `CharShowReport`, `CharGearReport` (v0.2), `CharBindsReport` (v0.2), `CharMacrosReport` (v0.2), `CharHistoryReport` (v0.2), `BuildsReport`, `FetchReport`, `HeadReport`, `NamesFetchReport` (`names fetch`), `LogTailReport` (`LogTailLine` per line with `--follow`), `InstallTreeReport`, `LooksListReport`, and the `GapReport`, `Install`, `Manifest` (`snap create` and `snap show`), `SnapCreateReport` (v0.2, `snap create` with several flavors), `VerifyReport`, `GcReport`, `ExportReport` (v0.2, `snap export`) and `ImportReport` (v0.2, `snap import`) models, which their help names as `gaps.GapReport`, `install.Install`, `snapshot.Manifest`, `snapshot.VerifyReport`, `snapshot.GcReport`, `snaparchive.ExportReport` and `snaparchive.ImportReport`). The generated command reference on the wiki lists each one.

**Fonts.** `wowlab ui` ships Alegreya and Alegreya SC (`OFL-Alegreya.txt`) and IBM Plex Mono (`OFL-IBMPlexMono.txt`) unchanged, under the SIL Open Font License 1.1.

## Install

With [uv](https://docs.astral.sh/uv/) installed:

```
{{INSTALL}}
```

Installed v0.1.0 (wowlab-core)? Run `uv tool uninstall wowlab-core` first.

Then run `wowlab --version` and `wowlab doctor`.

## The Mac app (Apple silicon)

No terminal needed: download `wowlab-{{VERSION}}-macos-arm64.zip` below (it is attached a little after the release appears; if it never does, its build failed, so use the install line above), double-click it, drag `wowlab.app` into Applications and open it from there. It is the same wowlab, with its own Python inside, for Macs with Apple silicon; Intel Macs use the install line. It never updates itself.

The app is not signed by an Apple developer account, so macOS refuses it the first time. On macOS 15 (Sequoia) and later, Control-click and Open no longer get past this: click **Done** in the message, open **System Settings → Privacy & Security**, click **Open Anyway** beside the line about `wowlab` and confirm with your password or Touch ID, then open the app again and choose **Open Anyway**. (Not yet confirmed on this app on every macOS version.) A Terminal window then runs `wowlab ui` and your browser opens the explorer; closing the window stops wowlab.

The zip has its line in `SHA256SUMS` and its own attestation: `gh attestation verify wowlab-{{VERSION}}-macos-arm64.zip --repo sandgraal/wowlab --signer-workflow sandgraal/wowlab/.github/workflows/release.yml --source-ref refs/tags/{{TAG}}`.

## The Windows app (64-bit, untested)

No terminal needed: download `wowlab-{{VERSION}}-windows-x64.zip` below (it is attached a little after the release appears; if it never does, its build failed, so use the install line above). In File Explorer, right-click it, choose **Extract All…**, open the `wowlab` folder it makes and double-click `wowlab.exe`; keep `wowlab.exe` beside the `_internal` folder. It is the same wowlab, with its own Python inside, for 64-bit Windows. It never updates itself. Windows is untested: CI builds and starts this app, but nobody has yet run the first run with it on a real Windows install.

The app is not signed with a code-signing certificate, so Windows may show **Windows protected your PC** the first time: click **More info**, then **Run anyway**. (Not yet confirmed on this app.) A console window then runs `wowlab ui` and your browser opens the explorer; closing the window stops wowlab.

The zip has its line in `SHA256SUMS` and its own attestation: `gh attestation verify wowlab-{{VERSION}}-windows-x64.zip --repo sandgraal/wowlab --signer-workflow sandgraal/wowlab/.github/workflows/release.yml --source-ref refs/tags/{{TAG}}`.

## Check the download

The `#sha256=` at the end of the install line is the wheel's hash from `SHA256SUMS`, but `uv tool install` does not check it, and checking a file you downloaded says nothing about the copy `uv tool install <address>` downloads again. To install exactly the file you checked:

1. Download the wheel, the sdist and `SHA256SUMS` attached below into one empty folder, and open a terminal there.
2. Check them. Run `sha256sum -c SHA256SUMS --ignore-missing` (on macOS, `shasum -a 256 -c SHA256SUMS --ignore-missing`; `--ignore-missing` skips the apps' lines when you did not download them); each file has to print `OK`. On Windows, run `Get-FileHash -Algorithm SHA256 <file>` in PowerShell for each file and compare the hash with its line in `SHA256SUMS` (the case of the letters does not matter). Then check where the wheel was built: the wheel and the sdist each carry a signed build-provenance attestation. With the [GitHub CLI](https://cli.github.com/) run

   ```
   gh attestation verify wowlab-{{VERSION}}-py3-none-any.whl --repo sandgraal/wowlab --signer-workflow sandgraal/wowlab/.github/workflows/release.yml --source-ref refs/tags/{{TAG}}
   ```

   It has to print `Verification succeeded!`; it fails if the file was changed or was built anywhere but wowlab's release workflow from the tag {{TAG}}.
3. Install the file you checked: `uv tool install --python 3.12 ./wowlab-{{VERSION}}-py3-none-any.whl`.

## Changes to `--json` output

Scripts that read `wowlab … --json` depend on the models behind it. Each release lists here every model whose shape changed, what changed, and what a script reading it has to do. Compared with v0.1.0, every change below adds something: no field was removed or renamed. A few behaviours change, and each one says so below: `explain`'s exit code, `snap create` and `char list` with several game versions, `char list` listing every character folder, `binds coach`'s `chosen_by`, and `looks import-char`'s class. New fields that are often empty are left out of the JSON when empty, so for most installs the output is what it was plus the new fields.

New models, with nothing existing changed:

- `AddonsCheckReport` (`addons check --json`): each addon against this game version, with its interface, dependencies and the characters that have it turned on.
- `CharSummaryReport` (`char list --summary --json`): the Account tab's table, one row per character.
- `CharGearReport` (`char gear --json`): one character's equipped gear slot by slot. Each slot has the item link decoded (`slot_name`, `item_id`, `name`, `colour`, `quality`, `enchant_id`, `gem_ids`, `suffix_id`, `spec_id`, `bonus_ids`, `upgrade`) with `raw_link` beside it. The average item level has `absent`, the reason when there is none. It also has `identity` (described under `CharShowReport`) and `dressing`: either `{url, site, note}`, the address of an outside dressing room (Wowhead's) built from public game ids only, or `{items, note}`, the item ids with the reason there is no address. `wowlab ui`'s Character tab shows the address as a **See it dressed** link, which opens only when you click it; wowlab requests nothing.
- `CharBindsReport` (`char binds --json`): one row per key on one binding command, with the slot it presses, what the lab-addon's record says that slot holds, and which sources have it; plus `left_out_note`, the sentence about bindings the record left out.
- `CharMacrosReport` (`char macros --json`): every account and character macro with its body, where it may sit, and the `macros check` findings for it. `placement.status` is `unconfirmed` or `none`; `confirmed` exists in the model but is not produced yet.
- `CharHistoryReport` (`char history --json`): one character over time, one row per distinct save, oldest first, and the copies that could not be read, with the reason.
- `SnapCreateReport` (`snap create --json` on an install with several game versions and none named). `snap create` used to exit 2 there. It now backs up each one and prints `{"snapshots": [<manifest>, ...]}`. With one game version, or one named, it prints the bare manifest as before.
- `ExportReport` (`snap export --json`) and `ImportReport` (`snap import --json`), for the new commands that move a snapshot to another machine as one file and back.

Fields added to existing models:

- `DoctorReport` (`doctor --json`):
  - `wowlab`: which wowlab is running and where it is installed from.
  - `next_steps`: the "Next steps" the text output prints.
  - `build_info_missing`: true when the install has no `.build.info`.
  - `cannot_work_out`: when something failed that wowlab cannot explain, the newest whole snapshot it offers for a restore.
  - `not_taken`: described under `Install` below.
  - Each flavor gains `lab_addon`, the installed lab-addon against this game version.
  - The last-change block gains `flavor_folder`, `snapshot_taken`, `created_dirs`, `temps_removed`, `passed_over` and `paths_left`.
  - The snapshot store block gains `exposure`, which says whether anyone besides you can open the store.
  - Doctor no longer goes online by default. Without `--check-builds`, each flavor's `game_data.status` is `not_checked`. In v0.1.0 that took `--offline`, which is still accepted and does nothing.
- `Install` (`install show --json`), `DoctorReport` and `ExplainReport` gain `not_taken`: for each folder named `_..._` that was not taken as a game version, `{name, reason, target, note}`.
  - `reason` is one of `link`, `no_flavor_info`, `not_checked`, `flavor_info_changed` or `folder_changed`.
  - It is left out when there is no such folder, so most installs print the same JSON as before.
- `ExplainReport` (`explain --json`):
  - `candidates`: for a relative path with nothing on disk and no file-map row, the files whose last parts match it, then the rows that name it.
  - The file-map row and the snapshot facts gain `notes`, `kind` (who took the snapshot) and `newer_pre_write`.
  - **Exit code:** `wowlab explain` now exits 1 for a path that matches nothing; it exited 0 before. A script that runs it on a path that may not exist must allow exit 1.
- `SvDumpReport` is unchanged. `sv dump` now also takes a name as `sv list` lists it: an addon name, a file name, or a path under its account folder. New `--account` and `--character` options pick between files that share a name.
- `TreeReport` and `InstallTreeReport` (`tree --json`): each explained file gains `notes`.
- `Manifest` (`snap create`, `snap show`): `kind`, who took the snapshot: `user`, `first-run`, `profile`, `pre-write` or `imported`. A snapshot taken by v0.1.0 has none.
- `VerifyReport` (`snap verify --json`): `exposure`, as in `doctor`.
- `DiffReport` (`snap diff --json`): `plan_lines`, the text output line by line, with keys, macros and settings by name.
- `RestoreReport` (`snap restore --json`) and `ProfileApplyReport` (`profile apply --json`):
  - `plan_lines`, as in `DiffReport`.
  - `into`, only with `--into-flavor` or `--into-root`: where the snapshot was taken and the account folders mapped.
- `UndoReport` (`undo --json`):
  - `plan_lines`, as in `DiffReport`.
  - `written_paths`, the files the undo wrote.
  - `unlisted_written`, the files it wrote that the plan did not list.
  - `undo --expect <journal id>` undoes only that record; otherwise it exits 3 and undoes nothing.
- `AddonInstallReport` (`addon install --json`): `addon_version`, the version the installed lab-addon's TOC states, which is this wowlab's.
- `AddonInstallReport`, `AddonRemoveReport`, `ProfileApplyReport` and `doctor`'s last-change block: each path left behind gains `exists` and `now`.
- `SvListReport` (`sv list --json`):
  - `orphans`, true with the new `--orphans` (only the files no installed addon declares).
  - `unreadable`, true with the new `--unreadable`.
  - `read_failures`, one per listed file with `--unreadable`: `{path, backup, kind, reason, line, column, detail}`. `kind` is `refused`, with the line and column where reading stopped, `too_large` (not checked) or `not_opened`.
- `SvMergeReport` (`sv merge --json`):
  - `target_sha256`, the target file as the run read it.
  - `merged_sha256`, the bytes the merge writes or would write; null when unresolved conflicts stop it.
  - `removed`, what the merge takes out of the target.
  - `replace`, true with `--replace`.
  - Pass the two hashes back as `sv merge --expect <target_sha256>:<merged_sha256>` to write only if nothing changed in between; otherwise it exits 3 and writes nothing.
- `CharListReport` (`char list --json`):
  - `flavors`, each game version listed with its version and how many character folders it has.
  - `installed_version`.
  - Each character gains `flavor`, `record_schema`, `client_build`, `older_client` and `no_lab_record`.
  - Records skipped as not looked at gain `flavor`.
  - **Behaviour change:** it now lists every character folder, not only those with a lab-addon file. A folder without one has a row with `place` `"no_lab_file"` (a new value), `no_lab_record` saying what its other files hold, and `record`, `summary` and `mtime_ns` null.
  - **Behaviour change:** with several game versions and none named, it lists them all and the top-level `flavor_folder` is null; each row names its own `flavor`.
  - A v0.1.0 script that reads `record` on every row, or treats `flavor_folder` as always a string, needs a change: skip rows whose `place` is `"no_lab_file"`, and allow a null `flavor_folder`.
- `CharShowReport` (`char show --json`):
  - `installed_version`.
  - `older_client`, true when the record was written by an older game version.
  - `lab_addon`, the installed lab-addon against this game version.
  - `identity`, the character's race, body type and class as `{race_id, sex, sex_raw, class_id, class_file}`, each beside its `<field>_absent` reason. A record from an older lab-addon has every value absent: reinstall it with `wowlab addon install lab`, then log in and out.
  - In text, `char show` prints one new `Identity:` line.
- `BindsCoachReport` (`binds coach --json`):
  - **Value change:** `chosen_by` is `"newest schema"` where it was `"latest"`. A script that compared it with `"latest"` should compare with `"newest schema"`.
  - `log.chosen_by` can also be `"caster name"`.
  - New fields: `chosen_because`, `commands_unseen`, `keys_unseen`, `unseen` and `never_cast_withheld`.
  - New in `log`: `character_name`, `casters`, `caster_match`, `warning` and `scan_note`.
  - `log.counts.source_names` and `costs.table.never_cast_min_casts` are also new.
- `LooksImportReport` (`looks import-char --json`):
  - New fields: `class_from`, `recorded_class_id`, `recorded_class_file`, `recorded_class_absent` and `recorded_race_id`.
  - **Value change:** without `--class`, a record that has the character's class now gives the look its `class_id`, where it used to be null. `--class` still wins.
