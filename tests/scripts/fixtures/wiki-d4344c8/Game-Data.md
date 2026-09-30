# Game Data

The game's database tables (classes, races, customization options, talent trees, items, spells…) are what turn raw ids in your files into meaning. wowlab downloads them from [wago.tools](https://wago.tools), a community-run site that publishes the tables for every build, and caches them on your machine.

## Commands

```bash
uv run wowlab db2 builds                       # products and versions as wago.tools lists them
uv run wowlab db2 builds --product <code>      # only one product
uv run wowlab db2 fetch ChrClasses             # download one table for your flavor's version
uv run wowlab db2 head ChrClasses -n 5         # the first rows as CSV (fetched first if needed)
uv run wowlab db2 head ChrClasses --build <full version>   # a different version
```

Table names are the game's (`ChrClasses`, `ChrRaces`, …). `--build` takes the **full four-part version string** (for example `1.60.1.70058`). The default is the version of your discovered flavor.

## How the cache works

- **Keyed by exact version, never overwritten.** A cached table for one version is never replaced by another's, and a table already on disk is never downloaded again. That is invariant L5.
- **Why the full version, not the build number.** The last component alone is not unique across products, so anything wowlab keys by build uses the whole string.
- **It lives in your user data folder**, never in your install.

## The one network access

Downloading these tables is the *only* thing wowlab does on the network. `wowlab doctor` asks wago.tools whether each of your versions is listed; add `--offline` to skip it. Commands that need a table you have not cached will fetch it, and once cached they work offline.

wago.tools is community-run, so wowlab is careful not to lean on it: the test suite replays recorded responses and never calls it live.

## What it powers today

- **[Customization Looks](Customization-Looks)** — races, options and choices come straight from these tables.
- Anything that needs to turn an id into a name.

## What is planned

A SQL layer over the cached tables with typed columns and cross-build joins (`db2lake`), and the tools built on it (a character planner that uses talent-tree data, build-to-build change reports). See the [Roadmap](Roadmap).
