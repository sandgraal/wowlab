# FAQ

## Is it safe to use? Could it get me banned?

wowlab never reads or writes the game's memory, injects code, touches network traffic, or automates input, and that is a permanent rule, not a current limitation ([Safety and Guarantees](Safety-and-Guarantees)). It only reads and changes files the game treats as your settings and saved data, the same kind of files you could edit by hand. That said, wowlab is an independent project, not affiliated with or endorsed by Blizzard, and nobody here can speak for Blizzard's policies. Use your own judgement.

## Will it change my files without telling me?

No. Reading never writes. Anything that changes your install shows you a plan first and asks, takes a snapshot, and refuses while the game is running. `--yes` skips the question, never the snapshot.

## I ran something and want it undone.

Close the game, then `uv run wowlab undo`. It reverses the most recent change made through the write gate. To go further back, pick a snapshot with `wowlab snap list` and use `wowlab snap restore ID`. See [Snapshots and Undo](Snapshots-and-Undo).

## It says "refused" or exits with code 3.

That is the write gate. The most common reason is that the game is still running (or its launcher left a process behind). Close it and try again. The message names the reason.

## "No WoW install found".

wowlab searches the default locations only. Set `WOWLAB_WOW_ROOT` to the folder that holds `.build.info`, or pass `--root`. See [Getting Started](Getting-Started#point-it-at-your-install).

## Does it work on retail, or only on Forever?

wowlab discovers flavor folders, product codes and versions from your install instead of assuming them, so nothing in the library is tied to one flavor. It was built and graded against real captures from the Forever beta on macOS, so that is where it has been proven. Other platforms and flavors follow the same rules but have had less real-world testing. The continuous-integration suite also runs a Windows job for the write gate's path behaviour.

## Why does my edit disappear when I log out?

The client rewrites SavedVariables and many settings files when you log out, `/reload` or exit cleanly, so an edit made while the game runs is overwritten. That is why wowlab's write gate refuses while the client runs.

## Why does `wowlab char show` show my previous session?

Because the addon's data is written at logout, `/reload` or a clean exit, an offline tool always sees the last saved session. Log out (or `/reload`) after changing something you want reflected. A crash writes nothing.

## Can wowlab change my talents or action bars?

No. Those are kept by the server, not in the local files wowlab can change (as far as has been verified on this client). Profiles and snapshots do not save or restore them.

## Can I apply a saved look to my character?

Not from wowlab. The customization sandbox is data only: it explores options, checks looks against the game's tables and saves them. Applying a look happens at the in-game barber shop.

## Does it upload anything?

Never. There are no accounts, no server and no telemetry. The only network access is downloading public game tables from wago.tools (skip with `--offline`).

## Where is my data?

In your user data folder (on macOS and Linux, `~/.local/share/wowlab/`), never inside your install. See [Getting Started](Getting-Started#where-wowlab-keeps-its-own-data).

## Can I share a snapshot, log or crash report to ask for help?

Be careful. Snapshots contain your settings and addon data, combat logs contain other players' names, and crash reports contain your character name, GUID, BattleTag and guild roster. Don't post any of them publicly.

## Is there an installer or a hosted version?

No. wowlab runs from source on your own machine, by design.

## I found a bug or a security problem.

Open an issue on the [repository](https://github.com/sandgraal/wowlab). Security and privacy reports: see [`SECURITY.md`](https://github.com/sandgraal/wowlab/blob/main/SECURITY.md).

## What is it built with?

Python 3.12, one uv workspace, `ruff`, `mypy --strict`, pytest, and Pydantic v2 models at module boundaries. The only Lua is the in-game lab-addon, which runs inside the client and is never executed by wowlab.
