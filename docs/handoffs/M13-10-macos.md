# macOS first run on a real install (2026-10-16)

This is the human half of ADR-0031's platform rule for macOS: a person ran the
first-run flow on a real install, and CI runs the suite on `macos-latest`. The
owner ran it. The conductor recorded it, redacted: the account folder and the
home path are replaced with placeholders.

## The machine

- **Date:** 2026-10-16.
- **System:** macOS 27.0, `arm64` (Apple Silicon).
- **Game:** the Forever beta. Product `wow_classic_beta`, version 1.60.1.70425,
  installed under the owner's home at `Games/World of Warcraft`.

## How wowlab was installed

From the pre-release `v0.1.0rc1` (M13-03, `docs/handoffs/M13-03.md`) with the
release's `uv tool install` line, into a throwaway home. No clone was involved.

The first run used a second, empty home, `HOME=/tmp/wowlab-mac-firstrun`. That
gave wowlab an empty snapshot store, so it was a true first run, while it read
the owner's real install through `--root`.

## The run

```
HOME=/tmp/wowlab-mac-firstrun /tmp/wowlab-rc1-test/bin/wowlab doctor --offline --root "$HOME/Games/World of Warcraft"
```

What it showed, in order:

1. "First run: wowlab has no snapshot of this install yet", then the five-point
   trust statement (M13-04, ADR-0031). It said that with `--offline` no request
   is sent.
2. `Install: <HOME>/Games/World of Warcraft (found by --root)`, then
   `Flavor _classic_beta_: product wow_classic_beta, version 1.60.1.70425`. It
   also reported the server-sync settings in `Config.wtf` and the account's
   `config-cache.wtf`: all three were not set, which leaves the client default.
3. `Client: not running`, and the snapshot store under the empty home, not yet
   created.
4. The offer: "a snapshot would hold WTF, Interface and Fonts: 4,709 files,
   547.0 MB; it is estimated to take about 26 seconds", with a size for each
   part. The owner answered `y`.
5. `Snapshot <id> (first run): 4,709 files, 547.0 MB`, then
   `Verified the store (wowlab snap verify): 4,308 objects and 1 manifest checked. all sound`.
6. The undo line: "this run changed nothing in your install", how to preview a
   restore of that snapshot with `--dry-run`, and that `wowlab undo` reverses a
   later change.

## The redacted report

```
HOME=/tmp/wowlab-mac-firstrun /tmp/wowlab-rc1-test/bin/wowlab doctor --offline --redact --root "$HOME/Games/World of Warcraft"
```

```
Install: <HOME>/Games/World of Warcraft (found by --root)
Flavor _classic_beta_: product wow_classic_beta, version 1.60.1.70425
  Game data: not checked (--offline)
  Server sync (when on, the server can replace local binds, macros or settings at login; which other *-cache files it replaces is not known):
    WTF/Config.wtf: synchronizeBindings not set (client default; wowlab does not know whether that default is on)
    WTF/Config.wtf: synchronizeMacros not set (client default; wowlab does not know whether that default is on)
    WTF/Config.wtf: synchronizeConfig not set (client default; wowlab does not know whether that default is on)
    WTF/Account/<ACCOUNT>/config-cache.wtf: synchronizeBindings not set (client default; wowlab does not know whether that default is on)
    WTF/Account/<ACCOUNT>/config-cache.wtf: synchronizeMacros not set (client default; wowlab does not know whether that default is on)
    WTF/Account/<ACCOUNT>/config-cache.wtf: synchronizeConfig not set (client default; wowlab does not know whether that default is on)
Client: not running
Snapshot store: <HOME>/Library/Application Support/wowlab/store
  1 snapshot(s) that load; none damaged
  no transactions in the journal
  (`wowlab snap verify` re-hashes every stored object)
```

## Nothing in the install changed

`doctor` only reads. The snapshot went to the throwaway store, and the run
printed "this run changed nothing in your install". The owner's own store and
the game folder were not touched.

## Not covered

The run was offline (`--offline`), so the wago.tools version check was not
exercised. That check has its own recorded responses in the test suite.
