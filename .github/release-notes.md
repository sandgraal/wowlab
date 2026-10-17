<!--
  Release-notes template, filled by .github/workflows/release.yml through
  scripts/release_check.py (M13-03). Placeholders are TAG, VERSION and
  INSTALL, each in double braces; any other name in double braces fails the
  release before it is published. GitHub appends the list of merged pull
  requests below this text.
-->
wowlab {{VERSION}} runs on your own machine against your own World of Warcraft install. It is read-only by default. Every command that changes files asks first and goes through one write gate; a snapshot is taken before the change, and `wowlab undo` reverses it. The only network traffic is fetching public game tables, and nothing is uploaded (ADR-0031).

## What is in it

The first public release. It reads an install and tells you what every file is, keeps snapshots of your setup, and puts them back.

- **Find and explain:** `doctor` (finds the install; a first run says what wowlab will do and offers a first snapshot), `install show`, `tree`, `explain` (what a file is, who writes it, whether it is safe to touch, with the snapshot facts and the commands that read it), `filemap gaps`.
- **The explorer:** `wowlab ui` opens a read-only view of the whole install in your browser. It runs the commands that read, and the ones that save only to wowlab's own data (snapshots, profiles, saved looks, the game-table cache, pages); never one that writes game files, never a command that takes `--yes`. It listens only on this machine behind a token.
- **Snapshots and undo:** `snap create`, `snap list`, `snap show`, `snap diff`, `snap restore`, `snap verify`, `snap gc`, `undo`.
- **Settings, binds, macros, addons:** `cvar`, `binds`, `macros list` and `macros check`, `addons`, `sv list`, `sv dump`, `sv merge`, `addon install lab` (the lab-addon, which records your spellbook, action bars and bindings on logout).
- **Profiles and looks:** `profile save`, `apply`, `list`, `show`, `delete`; `looks import-char`, `options`, `races`, `compare`, `page`.
- **Characters and game data:** `char list`, `char show`, `db2 builds`, `db2 fetch`, `db2 head`, `log tail`.
- **For bug reports:** `doctor --redact` prints output with no character, realm, account folder, BattleTag or home path in it; names it cannot find on its own, such as your guild's, go in `--redact-name`.

**Platforms.** macOS is tested by hand on the owner's install. Windows and Linux are untested: CI runs the test suite on both, but nobody has yet run the first-run flow on a real Windows install, and there is no native Linux client. See the README's platform table before relying on either.

**`--json`.** Every read command has a `--json` form backed by a Pydantic model named in its `--help` (`DoctorReport`, `ExplainReport`, `TreeReport`, `SnapListReport`, `RestoreReport`, `UndoReport`, `CVarListReport`, `CVarGetReport`, `BindsReport`, `MacrosReport`, `MacrosCheckReport`, `AddonsReport`, `AddonInstallReport`, `AddonRemoveReport`, `SvListReport`, `SvDumpReport`, `SvMergeReport`, `DiffReport`, `ProfileReport`, `ProfileListReport`, `ProfileApplyReport`, `ProfileDeleteReport`, `LookReport`, `LooksOptionsReport`, `LooksRacesReport`, `LooksCompareReport`, `LooksImportReport`, `LooksPageReport`, `CharListReport`, `CharShowReport`, `BuildsReport`, `FetchReport`, `HeadReport`, `LogTailReport` (`LogTailLine` per line with `--follow`), `InstallTreeReport`, `LooksListReport`, and the `GapReport`, `Install`, `Manifest` (`snap create` and `snap show`), `VerifyReport` and `GcReport` models, which their help names as `gaps.GapReport`, `install.Install`, `snapshot.Manifest`, `snapshot.VerifyReport` and `snapshot.GcReport`). The generated command reference on the wiki lists each one.

## Install

With [uv](https://docs.astral.sh/uv/) installed:

```
{{INSTALL}}
```

Then run `wowlab --version` and `wowlab doctor --offline`.

## Check the download

The wheel, the sdist and `SHA256SUMS` are attached below. Download them into one folder and run `sha256sum -c SHA256SUMS` (on macOS, `shasum -a 256 -c SHA256SUMS`); each file has to print `OK`. On Windows, run `Get-FileHash -Algorithm SHA256 <file>` in PowerShell for each file and compare the hash with its line in `SHA256SUMS` (the case of the letters does not matter).

## Changes to `--json` output

Scripts that read `wowlab … --json` depend on the models behind it. A release that changes a model's shape lists here each model, what changed, and what a script reading it has to do (ADR-0031).

_None listed._
