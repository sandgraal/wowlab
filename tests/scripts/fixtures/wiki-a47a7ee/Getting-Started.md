# Getting Started

wowlab is a personal project that **runs from source** on your own machine. There is no installer, no app store and no hosted version, by design.

## What you need

- [uv](https://docs.astral.sh/uv/), which brings Python 3.12 with it.
- A World of Warcraft install, to *use* the tools. (You do not need one to run the test suite: the tests use committed fixtures and none touches a real install.)

## Install

```bash
git clone https://github.com/sandgraal/wowlab && cd wowlab
make setup
uv run wowlab --version
```

Every command below is run as `uv run wowlab …` from the checkout.

## Point it at your install

wowlab looks in the usual places first:

| Platform | Default locations searched |
|---|---|
| macOS | `/Applications/World of Warcraft` |
| Windows | `C:\Program Files (x86)\World of Warcraft`, `C:\Program Files\World of Warcraft`, and the same folders under each fixed drive |

If yours is somewhere else (a Wine, Lutris or Proton prefix, or a custom folder on macOS), name the **install root**: the folder that contains the file `.build.info`.

```bash
export WOWLAB_WOW_ROOT="/path/to/World of Warcraft"
```

You can also pass `--root PATH` to any command; it wins over the environment variable. There are no credentials anywhere in the project.

## Your first five minutes

```bash
uv run wowlab doctor            # finds the install, its flavors and versions, whether the game runs
uv run wowlab install show      # the install root, its flavors and its .build.info rows
uv run wowlab tree --explain    # everything in a flavor folder, each path explained
uv run wowlab snap create -m "before I touch anything"
uv run wowlab snap list
```

`doctor` also reports your **server-sync settings** (`synchronizeBindings`, `synchronizeMacros`, `synchronizeConfig`). When those are on, the server can replace your local binds, macros or settings at login, which matters for anything that edits them. wowlab tells you when a setting is unset and says plainly that it does not know the client's default.

Add `--offline` to `doctor` to skip asking wago.tools whether each version is listed.

> [!TIP]
> Take a snapshot *first*. It costs a few seconds, and `wowlab undo` and `wowlab snap restore` can only go back to something that was saved. Anything that writes into your install also takes its own snapshot automatically, but your own labelled one is the baseline you will actually reach for.

## Things worth knowing up front

- **Close the game before anything that writes.** The write gate refuses while the client is running. The client rewrites its settings and SavedVariables files when you log out or `/reload`, so an outside edit made while the game runs is lost.
- **Only some commands change your install.** `undo`, `snap restore`, `profile apply`, `sv merge` and `addon install` / `addon remove` do, and every one goes through the write gate, shows a plan and asks first unless you pass `--yes` (most also take `--dry-run`). Everything else reads, or writes only to wowlab's own data folder. See the [Command Reference](Command-Reference).
- **Everything has `--json`.** Every command that reports something can print machine-readable JSON instead of text.
- **Exit codes:** `0` ok, `1` error, `2` usage, `3` refused by the write gate.

## Where wowlab keeps its own data

In your platform's user data folder (on macOS and Linux, `~/.local/share/wowlab/`), never inside the game install or the repository:

| What | Holds |
|---|---|
| snapshot store | every snapshot and profile, content-addressed and immutable, plus the journal of changes made through the write gate |
| game-table cache | tables downloaded from wago.tools, one copy per exact game version |
| saved looks | your customization looks, as JSON |
| generated pages | the local HTML page from `wowlab looks page` |

## Next

- [Safety and Guarantees](Safety-and-Guarantees) — what the write gate does and refuses
- [Snapshots and Undo](Snapshots-and-Undo)
- [Command Reference](Command-Reference)
