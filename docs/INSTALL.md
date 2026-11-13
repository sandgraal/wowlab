# Installing wowlab and your first run

This page is for players. It covers getting wowlab onto your computer,
pointing it at your World of Warcraft install, and what happens the first
time you run `wowlab doctor`. Machine setup for developing wowlab is in
[`SETUP.md`](SETUP.md).

## What wowlab will and will not do

The first run prints this too, before it does anything else (ADR-0031):

- **It reads your install and writes nothing there**: no temporary, cache
  or lock file. One side effect to know about: if a sync service (OneDrive,
  Dropbox, iCloud Drive) keeps some of your files online-only, reading one
  makes the service download it.
- **Only six commands change files in your install:** `snap restore`,
  `undo`, `profile apply`, `sv merge`, `addon install` and `addon remove`.
  Each one shows what it will change and asks first (unless you pass
  `--yes`), refuses while the game is running, and takes a snapshot before
  it writes, so `wowlab undo` can put things back.
- **It never touches the game itself.** Nothing under `Data/`, no program
  file, no game memory, no network traffic of the game's, no key presses.
  It never runs code from your files: SavedVariables are read as data.
- **Its own files stay in its own folder.** Snapshots, the record of every
  change and downloaded game tables live in wowlab's data folder, never in
  your install. `wowlab doctor` prints where that is.

<!-- network:begin -->
**What leaves your machine. Nothing is uploaded: these three are wowlab's only network traffic, and no other command makes a request.**

- `wowlab doctor --check-builds` downloads the public list of game versions from wago.tools, a community-run public site of game data, and looks for your installed versions in it; nothing from your install is sent. Without `--check-builds`, `doctor` makes no network request.
- `wowlab db2` and `wowlab looks` download public game data from wago.tools: its list of game versions, and tables for one version at a time. A table request names that version: your installed one, or the one you give with `--build`.
- A dressing-room link opens in your own browser, and only when you click it, carrying your character's race, body type, class, level and equipped item and bonus ids to that site: public game ids only, never a name. wowlab itself makes no request to it.
<!-- network:end -->

## Platforms

wowlab is developed on macOS. Windows and Linux are **untested**: a
platform is called supported only when CI runs the test suite on it and a
person has run the first run below on a real install of it (ADR-0031).
There is no native Linux client, so a Linux install means Wine, Lutris or
Proton; wowlab finds those only when you point it at them.

## Getting wowlab

You need [uv](https://docs.astral.sh/uv/). It brings Python 3.12 with it.

**From a release.** The README gives the one `uv tool install` line that
installs the current release, and the release page
(https://github.com/sandgraal/wowlab/releases) carries the wheel, the sdist
and `SHA256SUMS`. To check a download, put the three files in one folder and
run `sha256sum -c SHA256SUMS --ignore-missing` (on macOS
`shasum -a 256 -c SHA256SUMS --ignore-missing`); each file has to print
`OK`. (`--ignore-missing` skips the apps' lines when you did not
download them.) On Windows run
`Get-FileHash -Algorithm SHA256 <file>` in PowerShell for each file and
compare the hash with its line in `SHA256SUMS`. The release workflow builds
every file from the tagged commit, and the repository's tag rules let only
the owner create a `v*` tag (`docs/handoffs/M13-03.md`).

The install line ends in `#sha256=` and the wheel's SHA-256. Nobody types
that hash. The v0.1.0 line's hash was taken from that release's
`SHA256SUMS`. From the next release on, the release workflow hashes the
wheel it has just built (the same file that goes into `SHA256SUMS` and onto
the release page) and writes the install line, hash included, into the
release notes, and the README's line is copied from there. `uv tool install`
does not check the hash itself (uv 0.12 and 0.13 install the wheel even when
it is wrong), and checking a file you downloaded says nothing about the copy
`uv tool install <address>` downloads again. So the line tells you which
file it means; it does not prove you got that file.

**From PyPI, once the first PyPI release is out.** The release workflow will
also publish the same wheel to PyPI, under the name `wowlab-core`. Then this
runs `wowlab doctor` without installing wowlab for good:

<!-- not-run: needs the network, and a PyPI release that is not out yet -->
```bash
uvx --from wowlab-core wowlab doctor
```

and `uv tool install wowlab-core` installs it. Before that first PyPI
release, both fail with a "not found" error.

**The checked install, in three steps.** To install exactly the file you
checked:

1. Download the wheel, the sdist and `SHA256SUMS` from the release page into
   one empty folder, and open a terminal there.
2. Check them. Run `sha256sum -c SHA256SUMS --ignore-missing` (macOS:
   `shasum -a 256 -c SHA256SUMS --ignore-missing`; Windows: `Get-FileHash`
   as above); each file has to print `OK`. (`--ignore-missing` skips the
   Mac app's line when you did not download it.) Then, for a release after
   v0.1.0, check where the wheel was built (next paragraph):

   <!-- not-run: needs the network -->
   ```bash
   gh attestation verify wowlab_core-<version>-py3-none-any.whl \
     --repo sandgraal/wowlab \
     --signer-workflow sandgraal/wowlab/.github/workflows/release.yml \
     --source-ref refs/tags/v<version>
   ```

3. Install the file you checked, not the address:

   <!-- not-run: an install, from the network -->
   ```bash
   uv tool install --python 3.12 ./wowlab_core-<version>-py3-none-any.whl
   ```

Replace `<version>` with the release's version, for example `0.2.0`.

**Check where a release was built.** From the first release after v0.1.0,
the wheel and the sdist each carry a signed build-provenance attestation:
a record, signed through Sigstore and kept on GitHub, that names the
repository, the workflow, the tag and the commit that built the file. The
command in step 2 needs the [GitHub CLI](https://cli.github.com/),
installed and signed in. `--signer-workflow` requires the signature to come
from wowlab's release workflow and `--source-ref` requires the build to be
of that release's tag; with them the command prints
`Verification succeeded!`, or fails if the file was changed or was built
anywhere else. (With `--repo` alone it would accept a file signed by any
workflow in the repository.) v0.1.0 was published before attestations were
added, so it has none: for it, `SHA256SUMS` is the check.

### The Mac app (Apple silicon), no terminal needed

From the first release after v0.1.0, the release page also carries
`wowlab-<version>-macos-arm64.zip`: wowlab as a Mac app, with its own Python
inside, for Macs with Apple silicon (M1 and later). Intel Macs use the
`uv tool install` line above. The app is the same wowlab as the wheel, built
from it by the release workflow; it never updates itself and makes no
network request of its own. To get a newer version, download the newer zip.
The zip is attached a little after the rest of the release; a release
without it is one whose app build failed, and the wheel is unaffected.

1. Download the zip from the release page and double-click it in Finder. It
   unpacks to `wowlab.app`. (Safari may unpack it for you and remove the
   zip; to check the download first, as below, keep the zip.)
2. Drag `wowlab.app` into your Applications folder. Open it from there, not
   from Downloads: started from Downloads, macOS runs it from a temporary
   copy, and the window it opens in the next step may not find it.
3. Double-click it. The app is not signed by an Apple developer account
   (that costs money and an identity check), so the first time macOS
   refuses to open it. On macOS 15 (Sequoia) and later, Control-click and
   Open no longer get past this; instead:
   - In the message that says Apple could not verify `wowlab`, click
     **Done**.
   - Open **System Settings → Privacy & Security** and scroll down to
     **Security**. It says that `wowlab` was blocked; click **Open Anyway**
     and confirm with your password or Touch ID.
   - Double-click the app again and choose **Open Anyway** in the message
     that follows.

   macOS remembers this; later double-clicks open it straight away. (These
   steps are the ones macOS 15 shows for an app from outside the App Store;
   they are not yet confirmed on this app on every macOS version.)
4. A Terminal window opens and runs `wowlab ui`, and your browser opens the
   explorer. The window is the server: it shows what wowlab is doing, and
   closing it (or pressing Ctrl-C in it) stops wowlab. If Terminal asks
   whether to terminate the running process when you close it, click
   **Terminate**.

From a terminal the app is the ordinary `wowlab` command:
`/Applications/wowlab.app/Contents/MacOS/wowlab --version` prints its
version, and every other command works the same way.

**Check the app's download.** The zip's line is in the release's
`SHA256SUMS`, and the zip carries its own build-provenance attestation, as
the wheel does. Both checks need a terminal; skip them only if you trust
the release page. In the folder holding the zip and `SHA256SUMS`, run
`shasum -a 256 -c SHA256SUMS --ignore-missing` (the zip's line has to print
`OK`) and:

<!-- not-run: needs the network -->
```bash
gh attestation verify wowlab-<version>-macos-arm64.zip \
  --repo sandgraal/wowlab \
  --signer-workflow sandgraal/wowlab/.github/workflows/release.yml \
  --source-ref refs/tags/v<version>
```

Some antivirus tools flag apps built the way this one is, whatever is in
them. If yours does, check the hash and the attestation as above; a file
that passes both is the one the release workflow built.

### The Windows app (64-bit), no terminal needed

Windows is untested (see Platforms above): CI builds this app and starts it
on Windows, but nobody has yet run the first run below with it on a real
Windows install.

The release page also carries `wowlab-<version>-windows-x64.zip`: wowlab as
a Windows program, with its own Python inside, for 64-bit Windows on an
Intel or AMD processor. It is the same wowlab as the wheel, built from it by
the release workflow; it never updates itself and makes no network request
of its own. To get a newer version, download the newer zip. The zip is
attached a little after the rest of the release; a release without it is
one whose Windows build failed, and the wheel is unaffected.

1. Download the zip from the release page. In File Explorer, right-click it
   and choose **Extract All…**, and extract it somewhere you keep programs,
   such as your Documents folder. It unpacks to a folder named `wowlab`
   holding `wowlab.exe` and a folder named `_internal`. Keep the two
   together, and do not run `wowlab.exe` from inside the zip without
   extracting it: it needs `_internal` beside it.
2. Double-click `wowlab.exe`. The app is not signed with a code-signing
   certificate (that costs money and an identity check), so the first time
   Windows may show **Windows protected your PC**. Click **More info**, check
   that the app is `wowlab.exe`, and click **Run anyway**. Windows remembers
   this for that copy. (These are the steps Windows 10 and 11 show for an
   unsigned program; they are not yet confirmed on this app.)
3. A console window opens and runs `wowlab ui`, and your browser opens the
   explorer. The window is the server: it shows what wowlab is doing, and
   closing it (or pressing Ctrl-C in it) stops wowlab.

From PowerShell or Command Prompt the app is the ordinary `wowlab` command:
`.\wowlab\wowlab.exe --version` prints its version, and every other command
works the same way.

**Check the app's download.** The zip's line is in the release's
`SHA256SUMS`, and the zip carries its own build-provenance attestation, as
the wheel does. In PowerShell, in the folder holding the zip, run
`Get-FileHash -Algorithm SHA256 wowlab-<version>-windows-x64.zip` and compare
the hash with the zip's line in `SHA256SUMS` (the case of the letters does
not matter). With the [GitHub CLI](https://cli.github.com/), run:

<!-- not-run: needs the network -->
```bash
gh attestation verify wowlab-<version>-windows-x64.zip \
  --repo sandgraal/wowlab \
  --signer-workflow sandgraal/wowlab/.github/workflows/release.yml \
  --source-ref refs/tags/v<version>
```

(In PowerShell, put it on one line without the backslashes.) Some antivirus
tools flag programs built the way this one is, whatever is in them. If yours does, check the hash and the
attestation as above; a file that passes both is the one the release
workflow built.

**From source**, if you want the development version:

<!-- not-run: a source checkout, from the network -->
```bash
git clone https://github.com/sandgraal/wowlab
cd wowlab
uv sync --frozen
uv run wowlab doctor
```

From a source checkout every command is `uv run wowlab …`. The examples
below write `wowlab …`.

**If `wowlab` is not found** after `uv tool install` reported success, the
folder uv puts its commands in is not on your `PATH` yet (uv warns about
this as it installs). Run `uv tool update-shell`, then open a new terminal.

## Pointing wowlab at your install

The install folder is the one that holds `.build.info`, with the flavor
folders inside it: one per game product you have installed (retail, a
Classic, a PTR or a beta…), named like `_name_`. The names are not fixed:
a beta's folder is renamed when it launches, which is why wowlab reads
them from the install instead of assuming them.

To find the folder, open the Battle.net launcher, choose the game, open
its settings menu and pick "Show in Finder" (macOS) or "Show in Explorer"
(Windows). On a Mac `.build.info` is a hidden file: press Cmd-Shift-. in
Finder to show hidden files.

Without help, wowlab looks in these folders:

- macOS: `World of Warcraft` in `/Applications`, `/Applications/Games`,
  `/Games`, `~/Applications`, `~/Games` and `/Users/Shared`, then on each
  volume under `/Volumes` (by name, and only when none of those holds it):
  at its top, in its `Applications` and in its `Games`
- Windows: `World of Warcraft` at the top of each drive, then under
  `Program Files (x86)`, `Program Files` and `Games`, the system drive first

The first install found is used, and wowlab says which folder it took.
When it finds none, it lists every folder it looked in.

On Linux there is no default: the game runs under Wine, Lutris or
Proton, inside a Windows-style folder tree called a prefix, and wowlab
does not search prefixes. These are the places such installs usually are;
yours may differ, and the folder you want is the `World of Warcraft`
folder that holds `.build.info`:

- Wine: `~/.wine/drive_c/Program Files (x86)/World of Warcraft`
- Lutris (its Battle.net installer): `~/Games/battlenet/drive_c/Program Files (x86)/World of Warcraft`
- Steam with Proton (Battle.net added as a non-Steam game):
  `~/.local/share/Steam/steamapps/compatdata/<number>/pfx/drive_c/Program Files (x86)/World of Warcraft`,
  where `<number>` is the id Steam gave the shortcut; with Steam from
  Flatpak, the same path under `~/.var/app/com.valvesoftware.Steam/.local/share/Steam/`

To search your home folder for it:

<!-- not-run: searches your own folders, not a wowlab command -->
```bash
find ~ -name .build.info -path '*World of Warcraft*' 2>/dev/null
```

The folder that line prints, without `/.build.info`, is the install folder.

If yours is somewhere else, name it for one command with `--root`:

```bash
wowlab doctor --root "/path/to/World of Warcraft"
```

or for every command with `WOWLAB_WOW_ROOT`:

<!-- not-run: sets a variable on your own machine -->
```bash
# macOS or Linux (add it to your shell's profile to keep it)
export WOWLAB_WOW_ROOT="/path/to/World of Warcraft"
```

<!-- not-run: sets a variable on your own machine -->
```powershell
# Windows PowerShell, this window only
$env:WOWLAB_WOW_ROOT = "D:\Games\World of Warcraft"
# or kept for new windows
setx WOWLAB_WOW_ROOT "D:\Games\World of Warcraft"
```

`--root` wins over `WOWLAB_WOW_ROOT`. When wowlab finds no install,
`wowlab doctor` lists where it looked and prints these same instructions.

## Your first run

Run `wowlab doctor`. A run is a first run while wowlab's store holds no
snapshot of this install. It goes like this:

1. **What wowlab will and will not do**, as above, before anything else.
2. **The usual report**: the install, each flavor and its version, your
   server-sync settings, whether the game is running, and the state of the
   snapshot store. Whether wago.tools lists each version is checked only
   with `--check-builds`.
3. **What a first snapshot would hold**, for each flavor: the `WTF`,
   `Interface` and `Fonts` folders (the same ones `wowlab snap create`
   takes), how many files, how big, and an estimated time. The estimate is
   on the slow side on purpose. Anything unusual is said here, before any
   snapshot is taken (see below).
4. **The offer.** In a terminal, wowlab asks whether to take the snapshot
   (the default answer is no). With several flavors installed, if the
   game is running, or wowlab cannot tell, it says so first; then it lists
   the flavors with their product code and version and asks which one;
   pressing Enter takes none. If the game is running, or wowlab cannot
   tell, it asks yes or no after that too.
5. **The snapshot**, into wowlab's own store, of one flavor. Nothing is
   written into your install. If other flavors are installed, wowlab names
   each with the command that snapshots it
   (`wowlab snap create --flavor <folder>`).
6. **The check**: `wowlab snap verify` runs on the store and re-hashes
   every stored file.
7. **How to undo**, as the last line. The first run changes nothing in your
   install, so there is nothing to undo yet. The line names the snapshot
   and what it holds: only those of `WTF`, `Interface` and `Fonts` that
   were there as folders, as they were on disk, with SavedVariables (as of
   your last logout or `/reload`) when `WTF` is one of them. If `WTF` is a
   link, the line says so: the link is recorded, not followed, and its
   settings and SavedVariables are not in the snapshot.
   `wowlab snap restore <id> --dry-run` shows what restoring it would
   change. A restore writes back the files it holds that changed since
   (client-written backups such as `.lua.bak` are skipped unless named
   with `--paths`); it does not remove files added since. It asks first
   (unless you pass `--yes`). `wowlab undo` reverses any later change
   wowlab makes. With server sync on (`wowlab doctor` shows the settings),
   the server may replace bindings, macros and settings at your next login
   whatever a restore put back.

The offer is made on every run until the store holds a snapshot of this
install; from then on, `wowlab doctor` is the plain report again. Use
`wowlab snap create` for later snapshots.

Options for the first run:

| Option | What it does |
|---|---|
| `--yes` | Take the snapshot without asking. With several flavors, add `--flavor`. |
| `--flavor <folder>` | The flavor to snapshot, as `wowlab doctor` lists it. |
| `--check-builds` | Ask wago.tools whether it lists each installed version. Without it, `doctor` makes no network request. (`--offline` is still accepted, does nothing, and says so.) |
| `--json` | Print the report as JSON (the `DoctorReport` model; its `first_run` field holds the first-run part). Never asks: without `--yes` no snapshot is taken. |

When input is not a terminal (a script, a pipe) and `--yes` is not given,
wowlab does not ask and takes no snapshot; it prints the command that
would.

### Things the first run tells you about

- **A synced WTF folder.** If WTF sits inside OneDrive or Dropbox, or in a
  folder Syncthing syncs, wowlab says so and carries on; it never refuses.
  This is a guess from heuristics, not a certainty: it goes by folder
  names (`OneDrive…`, `Dropbox…`) and by the marker files the services
  leave (`.stfolder`, a `.dropbox` file). iCloud Drive and Google Drive are
  not recognised. A sync service can leave online-only placeholders and
  conflict copies, and it copies whatever a later restore puts back to
  your other computers.
  - *Online-only files* are counted. Reading them for a snapshot makes the
    service download them; the estimate does not include that download,
    and the snapshot fails for any file the service cannot fetch.
    Online-only *folders* are named and not listed, since listing one
    would download it.
  - *Conflict copies* by Dropbox and Syncthing are listed by name and
    snapshotted like any other file; wowlab does not merge them. OneDrive's
    conflict copies cannot be told apart from ordinary files.
- **Links.** A symlink, or on Windows a junction, in WTF, Interface or Fonts
  is listed with where it points. Snapshots record the link and never
  follow it, so the files behind it are not in the snapshot. If WTF itself
  is a link (a common way to sync settings), the first run says that your
  settings and SavedVariables are not in the snapshot, and to back up the
  folder it points to yourself. The same holds for a flavor folder: a
  `_retail_` (or any `_..._` folder) that is a symlink or a junction is not
  followed and not taken as a flavor, even though the game reads through it
  like a folder.
  `wowlab doctor` and `wowlab install show` name it with where it points;
  nothing in it is read or snapshotted, so back up the folder it points to
  yourself. An install folder you reach through a link (the path you pass
  to `--root`, or one of its parent folders) is fine: only links inside the
  install are not followed.
- **A large snapshot.** The size and estimated time are always printed
  before the snapshot; a large one (a gigabyte, or a minute or more) is
  called out.
- **Several flavors.** Each is listed with its product code, version,
  size and estimate, and you choose which to snapshot (`--flavor` with
  `--yes`).
- **The game running.** wowlab says so plainly and names the client it
  found. A snapshot only reads, so it can still be taken, exactly as
  `wowlab snap create` takes one, but your SavedVariables on disk are from
  your last logout or `/reload`, and the game writes over them when you
  next log out, `/reload` or exit. On Windows, a file the game holds open
  may not be readable; if the snapshot fails, close the game and run it
  again. wowlab checks again right before the snapshot, so a game started
  while it waited for your answer is still recorded. Commands that change
  files refuse until the game is closed.

## The explorer in your browser

`wowlab ui` opens a local page that shows your install the way `wowlab
tree` and `wowlab explain` do, one folder at a time:

<!-- not-run: `wowlab ui` does not exit -->
```bash
wowlab ui
```

It picks a free port, listens only on this machine, and opens your
browser on its link; if no browser opens, the link is printed once, so
paste it. The link, a reload of the page and a second tab keep working
until you stop `wowlab ui` with Ctrl-C; a new `wowlab ui` makes a new
link, and the old one stops working.

`wowlab ui` is made for a single-user machine. On a computer other people
log in to, they can see the link in their process list while your browser
starts, and can open it too, until you stop `wowlab ui`. From the page,
`sv dump` reads only files inside your install.

Reading never changes anything. Six commands can change game files; each
shows a plan and runs only when you confirm, after a snapshot.

The page reads the install exactly as the other commands do and writes
nothing there itself. The commands that read, and the ones that save only
to wowlab's own data, run directly; a command that asks before changing
anything (`--yes`) runs first as a plan, and runs for real, through the
same write gate, only when you press Confirm under that plan.
`wowlab ui` takes no `--root`: when your install is not in a default
location, set `WOWLAB_WOW_ROOT` first (above), or the page says no install
was found and lists where it looked.

What to look for:

- **The tree.** The whole install, with each flavor folder. Click a file
  to see what it is, who writes it, and whether wowlab may edit it, the
  same answer `wowlab explain <path>` gives.
- **Gaps.** Files the file map does not know yet are marked, and listed
  together, as `wowlab filemap gaps` lists them.
- **Snapshot facts.** For a file that is in a snapshot, whether the copy
  on disk still matches it.
- **Hints.** Each detail pane ends with the commands that act on that
  file, written for your shell (`posix` or `powershell`) and copied byte
  for byte.
- **Previews.** For a file wowlab has a read command for, Preview runs
  that command with `--json` and shows the result: `cvar list`,
  `binds list`, `macros list`, `sv dump` (SavedVariables as data, never
  run), `addons list`, `install show`, or `log tail` for the newest combat
  log. Other files have no Preview button.
- **The commands tab.** Runs this wowlab's commands with `--json` and shows
  the result. Every command is offered except `ui` itself,
  `log tail --follow`, `looks page --out` and `snap export`. The six that change game
  files are `snap restore`, `undo`, `profile apply`, `sv merge`,
  `addon install` and `addon remove`: each shows its plan first, takes a
  snapshot before it writes, and `wowlab undo` reverses it.
- **Character.** One character as the lab-addon last saved it, at logout
  or `/reload`: gear, key bindings and macros, the same answers
  `wowlab char gear`, `wowlab char binds` and `wowlab char macros` give.
  It only reads.
- **Looks.** The page `wowlab looks page` writes into wowlab's own data
  folder: the races, body types, classes and customization options of the
  build's tables, and your saved looks checked against that build. It
  never touches your game folder.

If something looks wrong, the useful report is the path you clicked, what
the page said, and what `wowlab explain <that path>` says in a terminal.

## Where wowlab keeps its files

In the platform's user data folder, never in your install:

- macOS: `~/Library/Application Support/wowlab/`
- Windows: `%LOCALAPPDATA%\wowlab\wowlab\`
- Linux: `~/.local/share/wowlab/`

Snapshots, and the journal of every change made through the write gate,
are under `store/` in that folder; `wowlab doctor` prints the exact path.
wowlab has no command that deletes a snapshot. `wowlab snap gc` removes
stored files that no snapshot needs, after asking.

## Undo, in one place

- `wowlab undo` reverses the most recent change wowlab made to your install
  (a restore, a profile apply, a merge, an addon install or remove).
- `wowlab snap restore <id>` puts files back from a snapshot (`--dry-run`
  shows the plan first); `wowlab snap list` shows your snapshots. A
  whole restore also leaves out the screenshots in a snapshot taken with
  `snap create --screenshots`. wowlab never writes `Screenshots/`, not even
  with `--paths`. They stay in the snapshot, and the plan counts them and
  names the first ten that differ from the disk, are missing, or could not
  be read (`Screenshots/` now a link).
- **After the launch rename.** A snapshot goes back only into the folder it
  was taken of, unless you name another. When a beta launches, the game's
  folder gets a new name; you do not rename it yourself. Start the launched
  game and log in once with each character you want settings for, so its
  account, realm and character folders exist, and quit it. Then
  `wowlab snap restore <id> --into-flavor <new folder>` puts the snapshot
  into the new folder, and `wowlab profile apply <name> --into-flavor <new folder>`
  does the same for a profile. `--into-root <path>` restores into another
  install folder on this machine (a reinstall somewhere else, or a second
  install). To carry a snapshot to another computer,
  `wowlab snap export <id> <file>` writes it into one file (outside any
  install), and `wowlab snap import <file>` on the other computer adds it to
  that computer's snapshots once every file in it has passed its check;
  then restore it there with `--into-root`. The plan
  names both folders and which account folder under `WTF/Account/` goes
  where. When the snapshot and the target each have one account folder they
  are paired for you; otherwise say which goes where with
  `--map-account <snapshot's folder>=<target's folder>`, once for each
  account folder the snapshot holds (the target's may be a new one). A
  folder there that is no account folder (no character folder in it) goes
  back under its own name, and the plan says so. Per-character settings go
  only to the character folder of the same realm and character name: a
  character that has another realm or name in the new folder gets nothing
  from them. Applied to another folder, a profile deletes the files under
  its folders that are in the target but not in the profile; the plan
  lists them as "in the target folder, not in the profile". `--paths`
  names files as `wowlab snap show <id>` lists them.
- Both go through the same write gate: the game must be closed, a snapshot
  is taken first, and you are asked before anything is written.
