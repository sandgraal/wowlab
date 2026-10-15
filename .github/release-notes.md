<!--
  Release-notes template, filled by .github/workflows/release.yml through
  scripts/release_check.py (M13-03). Placeholders are TAG, VERSION and
  INSTALL, each in double braces; any other name in double braces fails the
  release before it is published. GitHub appends the list of merged pull
  requests below this text.
-->
wowlab {{VERSION}} runs on your own machine against your own World of Warcraft install. It is read-only by default. Every command that changes files asks first and goes through one write gate; a snapshot is taken before the change, and `wowlab undo` reverses it. The only network traffic is fetching public game tables, and nothing is uploaded (ADR-0031).

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
