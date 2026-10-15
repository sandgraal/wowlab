# Installing wowlab and your first run

This page is for players. It covers getting wowlab onto your computer,
pointing it at your World of Warcraft install, and what happens the first
time you run `wowlab doctor`. Machine setup for developing wowlab is in
[`SETUP.md`](SETUP.md).

## What wowlab will and will not do

The first run prints this too, before it does anything else (ADR-0031):

- **It reads your install.** Looking changes nothing there and leaves no
  temporary, cache or lock file behind.
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
- **Nothing is uploaded.** The only network traffic is to
  [wago.tools](https://wago.tools), a public site of game data.
  `wowlab doctor` downloads its public list of game versions and looks for
  your installed versions in it; nothing from your install is sent. Run
  `wowlab doctor --offline` to skip that. `wowlab db2` and `wowlab looks`
  download public game tables from the same site.

## Platforms

wowlab is developed on macOS. Windows and Linux are **untested**: a
platform is called supported only when CI runs the test suite on it and a
person has run the first run below on a real install of it (ADR-0031).
There is no native Linux client, so a Linux install means Wine, Lutris or
Proton; wowlab finds those only when you point it at them.

## Getting wowlab

You need [uv](https://docs.astral.sh/uv/). It brings Python 3.12 with it.

**From a release.** No release has been published yet. When the first one
is, its page carries a wheel and a SHA-256 sums file, and the README gives
the one `uv tool install` line that installs it.

**From source**, until then:

```bash
git clone https://github.com/sandgraal/wowlab
cd wowlab
uv sync --frozen
uv run wowlab doctor
```

From a source checkout every command is `uv run wowlab …`. The examples
below write `wowlab …`.

## Pointing wowlab at your install

The install folder is the one that holds `.build.info`, with the flavor
folders (named like `_name_`, one per game version you have installed)
inside it. Without help, wowlab looks only where the game installs by
default:

- macOS: `/Applications/World of Warcraft`
- Windows: `World of Warcraft` at the top of each drive, then under
  `Program Files (x86)` and `Program Files`, the system drive first

If yours is somewhere else, name it for one command with `--root`:

```bash
wowlab doctor --root "/path/to/World of Warcraft"
```

or for every command with `WOWLAB_WOW_ROOT`:

```bash
# macOS or Linux (add it to your shell's profile to keep it)
export WOWLAB_WOW_ROOT="/path/to/World of Warcraft"
```

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
2. **The usual report**: the install, each flavor and its version, whether
   wago.tools lists that version, your server-sync settings, whether the
   game is running, and the state of the snapshot store.
3. **What a first snapshot would hold**, for each flavor: the `WTF`,
   `Interface` and `Fonts` folders (the same ones `wowlab snap create`
   takes), how many files, how big, and an estimated time. The estimate is
   on the slow side on purpose. Anything unusual is said here, before any
   snapshot is taken (see below).
4. **The offer.** In a terminal, wowlab asks whether to take the snapshot
   (the default answer is no). With several flavors installed, it lists
   them and asks which one; pressing Enter takes none.
5. **The snapshot**, into wowlab's own store. Nothing is written into your
   install.
6. **The check**: `wowlab snap verify` runs on the store and re-hashes
   every stored file.
7. **How to undo**, as the last line. The first run changes nothing in your
   install, so there is nothing to undo yet; the line gives the
   `wowlab snap restore <id>` that would put your files back as they are
   now, and reminds you that `wowlab undo` reverses any later change.

Options for the first run:

| Option | What it does |
|---|---|
| `--yes` | Take the snapshot without asking. With several flavors, add `--flavor`. |
| `--flavor <folder>` | The flavor to snapshot, as `wowlab doctor` lists it. |
| `--offline` | Do not contact wago.tools. |
| `--json` | Print the report as JSON (the `DoctorReport` model; its `first_run` field holds the first-run part). Never asks: without `--yes` no snapshot is taken. |

When input is not a terminal (a script, a pipe) and `--yes` is not given,
wowlab does not ask and takes no snapshot; it prints the command that
would.

### Things the first run tells you about

- **A synced WTF folder.** If WTF sits inside OneDrive or Dropbox, or in a
  folder Syncthing syncs, wowlab says so and carries on; it never refuses.
  It recognises them by folder names (`OneDrive…`, `Dropbox…`) and by the
  marker files the services leave (`.stfolder`, a `.dropbox` file). A sync
  service can leave online-only placeholders (reading them for a snapshot
  makes the service download them) and conflict copies (wowlab lists them
  and snapshots them like any other file, and never merges them), and it
  copies whatever a later restore puts back to your other computers.
- **Links.** A symlink, or on Windows a junction, in WTF, Interface or Fonts
  is listed with where it points. Snapshots record the link and never
  follow it, so the files behind it are not in the snapshot. If WTF itself
  is a link, the first run says that your settings and SavedVariables are
  not in the snapshot.
- **A large snapshot.** The size and estimated time are always printed
  before the snapshot; a large one (a gigabyte, or a minute or more) is
  called out.
- **Several flavors.** Each is listed with its own size and estimate, and
  you choose which to snapshot (`--flavor` with `--yes`).
- **The game running.** wowlab says so plainly. A snapshot only reads, so
  it can still be taken, exactly as `wowlab snap create` takes one, but
  your SavedVariables on disk are from your last logout or `/reload`, and
  the game writes over them when you next log out, `/reload` or exit.
  Commands that change files refuse until the game is closed.

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
  shows the plan first); `wowlab snap list` shows your snapshots.
- Both go through the same write gate: the game must be closed, a snapshot
  is taken first, and you are asked before anything is written.
