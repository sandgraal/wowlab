# Combat Log

While combat logging is on (`/combatlog` in game), the client appends one timestamped event per line to a file under `Logs/`. wowlab can read it, stream it and follow it live. It is **read only**: wowlab never writes to or rotates your logs.

## Commands

```bash
uv run wowlab log tail                     # the last 10 lines of the newest log, tokenized
uv run wowlab log tail -n 200              # the last 200 (up to 100,000)
uv run wowlab log tail --follow            # keep printing as the client writes
uv run wowlab log tail --follow --json     # one JSON object per line
```

`--follow` keeps going, **including into a new log file** when the client starts one, until you interrupt it. If there is no log yet, it waits for one.

## What "tokenized" means

Each line is split into its timestamp, event name and comma-separated fields, with quoted strings and nested lists handled. wowlab **keeps every field as text**; it does not decide what each field means for you. The format has a header line naming the combat-log version and whether advanced logging is on, and events differ in field count. The reference is [`docs/LAB_FORMATS.md`](https://github.com/sandgraal/wowlab/blob/main/docs/LAB_FORMATS.md) §8.

A line wowlab cannot parse is kept as raw text and marked unparsed, never dropped. Following survives the client truncating or rotating the file.

Lines are kept byte for byte. One owner-granted exception exists for safety: a single line longer than 1 MiB keeps only its first 1 MiB, flagged as truncated with its true length, so a crafted file cannot exhaust memory.

## Things to know

- **Logs name other players.** A combat log from a group or open world contains other people's names. wowlab reads it locally, but do not post one publicly. The project's own test corpus of combat logs was pseudonymised by a scrub tool before it was committed.
- **Performance.** The reader is built to stream large logs with bounded memory rather than load them whole.
- Turn logging on in game with `/combatlog`, off with `/combatlog` again.

## What is planned

Fight replays, generative art from logs, and per-session dashboards are on the [Roadmap](Roadmap) menu; none is built yet.
