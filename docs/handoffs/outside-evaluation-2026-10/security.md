# Security and privacy engineer

## Verdict

I would recommend wowlab to a player on a normal single-user machine. The parts that run today are more defensive than most local tools I review. In the UI server, guard and luadata, every probe I sent was refused, escaped or bounded. Nothing in the shipped code gets close to a remote compromise. The real risk is in two places. The first is the planned ADR-0032 / §16.5 advice loop: a model's reply, or a stranger's proposal file, becomes macros that the client executes on a keypress, and the validator as specified checks spell names and bracket balance but not code. The second is a set of small local-exposure gaps that line up badly on a shared machine: the token in the browser's argv, `sv dump` reading any absolute path, and a world-readable snapshot store. The biggest single problem is that the proposal validator (F3 of §16.5) treats `/run`, `/script` and `/console` macro lines as ordinary text. Fix that before F3 is ticketed, not after.

## What works

- **UI server request gate.** Host is checked exactly, so DNS rebinding is refused (`Host: evil.example:<port>` → 403 `host`). A foreign or `null` Origin gets 403 `origin`. The token is 256 bits, compared in constant time, and a duplicated `token` field is refused. The page CSP is `default-src 'none'` with hash-only script and style, `base-uri 'none'`, `form-action 'none'` and `frame-ancestors 'none'`. The server also sends `Referrer-Policy: no-referrer` and `Cache-Control: no-store`, and nothing is logged.
- **Argv building in `/api/run`.** Options go as `--name=value` and arguments after `--`, so `{"path":"--help"}` and `{"name":"--root=/etc"}` arrive as data. The confirm tokens are one-time and bound to the exact argv: a replayed token got 403 `confirm`. `log tail --follow`, `looks page --out` and `ui` are refused.
- **XSS.** I planted `<img src=x onerror=…>` as an addon folder name, TOC file name, SV file name and SV key. I put `</script><svg onload=…>` in a TOC Title and Notes, an SV value, a CVar and an item link in `WowLab.lua`, and added a file name with a newline and a U+202E folder name. In headless Chromium none of it executed: no dialog, no page error, no injected element, and no request left the server's origin. Control characters render as `⟨U+000A⟩` (screenshot `work/security/shots/explorer-picked.png`).
- **Terminal escapes in the CLI.** OSC title, CSI clear and OSC-8 hyperlink sequences in a TOC Title, folder name, SV string, CVar and combat-log line all print as literal `\x1b…` in `addons list`, `tree`, `sv dump`, `cvar get/list` and `log tail`. Many tools miss this; wowlab does not.
- **Guard.** A symlinked `WTF/Config.wtf`, and a symlinked `SavedVariables/` directory pointing outside the install, are both refused ("is a link; guard never follows one"), and the outside file is untouched. The lexical allowlist rejects `..`, empty parts, trailing dots and spaces, reserved device names, executable suffixes and a case-variant `AddOns`. The journal is write-ahead and fsynced.
- **luadata on hostile input.** Depth over 200 is refused at once (100k and 1M opening braces take 0.00 s). The parser also refuses a 1M-digit number, decimal escapes over 255, long brackets, hex floats, `function`, calls and `..`. The parse budget (`MAX_COST`) stopped a 66 MB bomb at about 1 GB RSS, with no crash and no RecursionError.
- **Workflows.** Every action is pinned to a full SHA. There is no `pull_request_target`. Fork PRs never reach a secret, `claude.yml` refuses fork PRs explicitly, and `include_comments_by_actor` keeps strangers' comments out of the prompt. The reviewer runs no shell, and `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` is set. The release job is the only job with `contents: write` and sits behind an environment approval.
- **Network client (`gamedata`).** It sends a descriptive User-Agent, follows no redirects and sends no cookies.

## Findings

### F1 — The planned proposal validator passes executable macro lines (`/run`, `/script`, `/console`) from a model or a stranger
- Severity: major (design stage; nothing built yet)
- Area: `docs/LAB_PLAN.md` §16.5 F3/F4/F5, ADR-0032
- Evidence: §16.5 F3 lists what the validator checks: key names, binding commands, spell names in the spellbook, the 255 limit, duplicate names, balanced brackets, and hostile cases ("a command with a shell character, a key of a thousand characters, a body with `|` escape sequences"). `grep -n -i "/run\|/script" docs/BACKLOG.md docs/LAB_IDEAS.md docs/LAB_PLAN.md` finds nothing for macros. A WoW macro body can hold `/run <Lua>`, `/script`, `/console <cvar>` and `/click <frame>`. These run when the player presses the bound key, which counts as a hardware event, so even protected calls are allowed. Example: `PickupContainerItem(0,1) DeleteCursorItem()`, or `SendMail` at a mailbox.
- Impact: there are two realistic paths. (a) Prompt injection. The packet carries macro text the player copied from websites, and with `--send` a model's reply is parsed straight into a proposal. (b) Stage one's "drop target for the proposal" invites players to drop in proposals found on forums and Discord. Either path writes in-game Lua through guard with a valid snapshot. Undo restores the file, but by then the item has already been deleted. This is not an L3 violation in the letter, since wowlab runs nothing, but it delivers foreign code to the client.
- Proposal: in §16.5 F3, have the validator classify every macro line. `/run`, `/script`, `/console`, and `/click` on a frame the record does not list are code. Code is refused unless it is unchanged from the player's current macro. If the owner wants to allow new code at all, require a separate per-line confirmation that shows the Lua, never batched. Add these as labelled hostile cases in F3's grading. Make the validator load-bearing under ADR-0013, which ADR-0032 already floats.
- Status: new

### F2 — The `--send` packet redacts only the player's own names, while macros carry other players' names and free text; the key's storage and the UI path are unspecified
- Severity: major (design stage)
- Area: ADR-0032 stage two, `wowlab_core.redact`, §16.5 F4
- Evidence: ADR-0032 says redaction uses "character, realm and account placeholders". Macros routinely hold `/w Name`, `/tar Name`, `/cast [@Name]`, and text the player wrote. `scripts/lab_capture.py:1086` already treats these as sensitive (it notes "whisper/invite/target macro line or @Name"), but the send path has no equivalent rule. The ADR says the key lives "on disk in the user config directory" and gives no file mode, no keychain, and no format. The UI's `/api/run` exposes every leaf that is not excluded (see `work/security/cmds.json`), so a future `char advise --send` would appear there unless it is listed. Today's dry-run and declined confirm shapes do not show "the exact bytes" a human must approve.
- Impact: a third party's character names, and anything the player typed into a macro, go to a hosted model the third party never agreed to. The key file is readable by any local process or user that can read the config directory, and through F3's `sv dump` if its format is flat `key = "…"`.
- Proposal: amend the ADR-0032 text before stage two is ticketed. (1) Replace every name after a whisper/target/`@` verb, and any word the spellbook does not know, with a placeholder, and list them in the preview. (2) Store the key in the OS keychain, or failing that a file created 0600 in a format `luadata` refuses, for example starting with a `[provider]` table. (3) Add `char advise --send` to the UI's excluded set, or give it a confirm shape whose plan output is the exact request body. (4) Accept only an `https:` endpoint with no redirects, and either ignore `HTTPS_PROXY` or name it in the preview.
- Status: new (ADR-0032 is Accepted, and these are additions within it)

### F3 — `sv dump` (CLI and UI) parses any absolute path, inside the install or not
- Severity: minor
- Area: `sv dump`, `/api/run`
- Evidence: through the UI API with the token:
  `{"command":["sv","dump"],"params":{"file":"<work>/fake_secret.lua"}}` → exit 0, `"name":"secret_api_key" … "text":"sk-THIS-IS-A-SECRET"`;
  `{"file":"/etc/passwd"}` → `stderr: "… expected `=` after the name (at b':')"`, which echoes bytes of a file outside the install.
- Impact: this is harmless on its own, because whoever holds the token is the player. Combined with F4 (another local user takes the token) or F2 (a flat key file), it reads secrets from outside the install through a tool that says it reads the install.
- Proposal: when `sv dump` is given a path outside the resolved install, refuse it from the UI. From the CLI, either refuse unless `--outside` is given, or print a "not in the install" line. Drop the byte excerpt from refusals for files outside the install.
- Status: new

### F4 — The UI token is in the browser's argv and stays in the address bar; loopback is shared by every local user
- Severity: minor (realistic only on a multi-user Linux or macOS machine)
- Area: `wowlab ui` (`cli.py` `ui_command`, `page.js` line 25)
- Evidence: started with `BROWSER="/bin/sh -c 'sleep 15' %s"`, then `ps -eo pid,user,args`:
  `8336 root /bin/sh -c sleep 15 http://127.0.0.1:39229/?token=O68gd_…` (`webbrowser.open` passes the URL as argv, and `/proc/<pid>/cmdline` is world-readable by default). `page.js` reads the token from `location.search` and never calls `history.replaceState`, so the token stays in the URL bar, in history, and in a synced history.
- Impact: another account on the same machine can read the token while the browser process lives and connect to `127.0.0.1:<port>`. The Host check passes for it. It can then run every offered leaf as the player, including confirmed `addon install`, `snap restore` and `profile apply`, and F3's reads. Streamers show the address bar, but remote viewers cannot reach loopback, so that exposure is cosmetic.
- Proposal: open the browser on a one-time bootstrap URL (`/?boot=<nonce>`) that the server exchanges once for the real token, held in `sessionStorage`, and burn the nonce. Have `page.js` call `history.replaceState` to drop the query. Document "single-user machine" in INSTALL.md's trust notes until then.
- Status: new

### F5 — The UI's own words say it never changes game files; it does
- Severity: minor (consent clarity)
- Area: `ui --help`, `page.html:20`, the Commands tab intro
- Evidence: `wowlab ui --help` says "Commands that change your game files … are not offered in this version." `page.html:20` says "nothing on this page changes your game files; the Commands tab can save to wowlab's own data". The Commands intro says "none of them touches the game folder" and in the next sentences describes the ones that do (`work/security/shots/cmd-addons_list.png`). Through the API, `addon install lab` planned and confirmed, and `Interface/AddOns/WowLab` appeared in the sandbox. `/api/commands` lists `undo`, `sv merge`, `addon install/remove`, `snap restore` and `profile apply` as confirmable.
- Impact: the writes are safe (guard, snapshot, undo), but a player's consent rests on a sentence that is false. This also matters for any future security claim the UI makes.
- Proposal: rewrite the header and the Commands intro together with the docstring. Pin all three with a text test, as `test_readme.py` does for the README.
- Status: already-ticketed: M13-76 (the docstring only; `page.html`'s header and the Commands intro are not in its scope, so widen it)

### F6 — A player cannot verify a release independently, and the dependencies they get are not the ones tested
- Severity: minor
- Area: README install line, `docs/INSTALL.md` §From a release, `release.yml`, `lab/core/pyproject.toml`
- Evidence: README line 34 is `uv tool install … https://github.com/…/wowlab_core-0.1.0-py3-none-any.whl`, which installs straight from the URL with no hash. `SHA256SUMS` is uploaded by the same job, to the same release, as the wheel, so it is the same trust root. There is no signature and no build-provenance attestation in `release.yml`. The runtime dependencies are open ranges (`pydantic>=2.12`, `httpx>=0.28`, `psutil>=7.0`, `platformdirs>=4.3`), so a player's resolver pulls whatever PyPI serves that day, not `uv.lock`.
- Impact: if the release or an upstream package is compromised, a player gets code wowlab never tested. The checksum only proves the download was not corrupted.
- Proposal: (1) Put the hash in the README line (`…whl#sha256=<hash>`, which uv honours) so the one-liner verifies itself. (2) Add `actions/attest-build-provenance` to `release.yml` and document `gh attestation verify`. (3) Publish a hashed constraints file built from `uv.lock` with each release, and show `uv tool install --constraints` with it. (4) Name these in §15.3.
- Status: new

### F7 — The snapshot store's objects and manifests are world-readable; only the journal is 0600
- Severity: minor (Linux or Wine multi-user)
- Area: `snapshot` store (`<data dir>/wowlab/store`)
- Evidence: `ls -la` after a `snap create` with umask 0022: `store/` and `objects/` are `drwxr-xr-x`, `objects/bb/4496…` is `-rw-r--r--`, while `journal/00000001-….json` and `lock` are `-rw-------`.
- Impact: snapshots hold the whole `WTF/`, including account folder names, chat-cache, macros, and SavedVariables that can contain whisper history. Any other local account can read them where the home directory is traversable, which is the default on many Linux distributions. macOS `~/Library` is 0700, so macOS is covered.
- Proposal: create the store tree 0700 and objects 0600, as the journal already is, regardless of umask. `snap verify` or `doctor` should warn when the store is group- or world-readable.
- Status: new

### F8 — The capture scrub is identifier-pattern-based; free text and chat history in SavedVariables pass, and the auto-picker can choose such a file
- Severity: minor (the owner's own data in a public repo)
- Area: `scripts/lab_capture.py` (`_plan_*` automatic SV selection, `scrub`), `redact.py`
- Evidence: the detectors cover email, BattleTag, `Player-`/`BNetAccount-`/`Guild-`/`ClubFinder-` GUIDs, identity CVars, the account name, `--extra-name`, and home paths. Whisper/target macro lines are only *notes* (`lab_capture.py:1086`). Automatic SV selection picks the first SV per category that is "clean" (`first_clean`), and clean means no identifier hit. Nothing excludes chat-history addons (`grep -i "elephant\|wim\|prat\|chattynator" scripts/lab_capture.py` finds nothing), or friend notes, officer notes, phone numbers, Discord handles or real names typed into a note field. `IDENTITY_CVARS` carries five entries marked "[verify] … spelled from reviewer memory". The committed fixtures I grepped hold no such content today.
- Impact: one capture run on a player who uses a chat-log addon publishes private conversations from other people into a public repository's history, which per SECURITY.md needs a history rewrite to undo.
- Proposal: keep an addon deny-list for automatic picks (chat logs, mail, friend notes), and allow those only through `--sv` with an extra confirmation. Flag any SV string value of N or more words as "free text, review by hand" in the notes. Confirm the five `[verify]` CVars from a real `Config.wtf`.
- Status: new

### F9 — luadata's memory amplification is about 40×; the effective file limit is about 47 MB, not the advertised 256 MB
- Severity: minor
- Area: `luadata.MAX_COST`, `MAX_FILE_BYTES`; UI `MAX_CONCURRENT_RUNS`
- Evidence: `work/security/bombs.py` and `bomb_big.py`: an 8.4 MB file of 500k keyed entries peaks at 355 MB RSS. A 66 MB file of short keyed entries runs 16.1 s, reaches about 996 MB RSS, and is then refused: `document over the parse budget of 1150000000 (MAX_COST)` at byte 47,150,121.
- Impact: a crafted file costs one run about 1 GB and 16 s. With the UI's 4 concurrent runs that is about 4 GB on a gaming PC, which is a nuisance, not a compromise. Large real SVs (auction-house addons) may hit the budget, and that is a domain question for the raider seat.
- Proposal: say in `sv dump`'s refusal that the budget, not the file size, was hit. Consider checking a cheap byte-based estimate before allocating. Cap the UI to one parse-heavy run at a time.
- Status: new

### F10 — `doctor --redact` replaces the login name as a word anywhere, and leaves paths outside `$HOME` verbatim
- Severity: minor
- Area: `doctorredact.py`
- Evidence: with login `root`, `W doctor --offline --redact` prints `(found by $WOWLAB_WOW_<USER>)`, because the variable name was rewritten. The same output prints the store path and the install path in full when they are not under `$HOME` (lines 1 and 12).
- Impact: a short or common login (`user`, `admin`, `owner`, `game`) mangles unrelated words, so players may stop trusting the redaction. A Windows install on `D:\<Name>\Games\…`, or a macOS volume named after the user, goes out unredacted in a public issue.
- Proposal: replace the login only inside path-shaped tokens, and run the home-path detector from `lab_capture` (`/Users/<n>`, `C:\Users\<n>`) over every printed path, not only `$HOME`. Add a labelled test with the login `root` and one with an install outside home.
- Status: new

### F11 — `doctor` without `--offline` contacts wago.tools by default
- Severity: minor (privacy posture)
- Area: `doctor`, README trust model
- Evidence: the first-run text says "Without it, it downloads wago.tools' public list of game versions". `--offline` is opt-in (`cli.py:1540`), and the User-Agent carries the wowlab version (`gamedata.py:102`).
- Impact: running the first diagnostic command tells a third-party site your IP address, when you ran it, and your wowlab version. That is small, and it is disclosed, but it is the opposite of the "nothing leaves" instinct a player brings to `doctor`.
- Proposal: make `doctor` offline by default with an `--online` (or `--check-versions`) switch, or ask on the first run.
- Status: new

### F12 — SECURITY.md still says `doctor --redact` will ship "once it ships"
- Severity: minor
- Area: `SECURITY.md` §If you are a player
- Evidence: "Paste only redacted output (`wowlab doctor --redact`, once it ships)". The option exists (`W doctor --offline --redact` works).
- Impact: reporters may paste unredacted output because they believe the tool cannot do it yet.
- Proposal: drop "once it ships", and add the F10 caveat ("check paths outside your home folder").
- Status: new

## Top 3 changes

1. **F1, plus F2's UI clause:** before §16.5 F3 is ticketed, specify that `/run`, `/script`, `/console` and unknown `/click` lines are code. They are refused, or confirmed line by line, from any source. `char advise --send` is never reachable through `/api/run` without a confirm that shows the exact bytes.
2. **F2, F7 and F3 (local data at rest):** the store is 0700/0600, the future key goes in the keychain or a 0600 file, and `sv dump` stays inside the install from the UI. A packet sent off-machine gets third-party names redacted.
3. **F6:** a self-verifying README install line (`#sha256=`), build-provenance attestation, and a hashed constraints file, so a player can tell a real release from a swapped one.

## Not tested

- Windows and macOS behaviour: case-insensitive and Unicode-normalising file systems (guard's case-fold refusal), junctions and 8.3 names, the Job Object kill, and who can read another session's command line. There is no such machine here.
- The live-network paths (`db2 fetch`, `doctor` online, the dressing-room link target), per ADR-0012.
- The Looks tab's sandboxed `srcdoc` frame. Writing a looks page needs cached db2 tables, which the offline sandbox lacks, so I did not render a planted `pages/looks.html`. I reviewed the code only.
- TOCTOU races inside guard (between the re-check and `os.replace`). I read the design, which uses path-based mutation and re-checks the directory chain, but I did not run a concurrent attacker. Exploiting it needs the same user, which is out of scope.
- The GitHub workflows were read, not run. I did not verify the repository rulesets or environment protection on GitHub itself.
- ADR-0032 stage one and stage two are not built. F1 and F2 are review findings against the spec.
