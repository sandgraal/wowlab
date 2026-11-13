# wowlab

wowlab is a toolkit for your own World of Warcraft install. It runs on your
computer and reads the game folder there: what every file is, your
characters as of their last logout, your keybinds, macros and addons. It
backs up your settings and can put them back.

- **Read-only unless you ask.** Any command that changes a file shows you
  the change and asks first. It takes a backup before it writes anything,
  and `wowlab undo` reverses the change.
- **Nothing leaves your computer.** The only network traffic is downloading
  public game tables, and only when you ask for them. Nothing is uploaded.
- **No game files are touched.** wowlab never reads the game's memory and
  never edits the game program or its data files.

## Install

You need [uv](https://docs.astral.sh/uv/). Then run:

```
uv tool install --python 3.12 wowlab
wowlab doctor
```

`wowlab doctor` finds your install and tells you what wowlab can do with it.
`wowlab ui` opens the explorer in your browser. Its Account tab lists every
character, and **Take a backup now** saves your setup.

If you installed v0.1.0, which was called `wowlab-core`, run
`uv tool uninstall wowlab-core` first.

There is also a Mac app and a Windows app, which need no terminal. They are
on the [releases page](https://github.com/sandgraal/wowlab/releases), along
with a way to check that a download is the one wowlab's release workflow
built.

## More

The full guide, the list of commands and the platform notes are in the
[project README](https://github.com/sandgraal/wowlab#readme). Licensed under
Apache-2.0.
