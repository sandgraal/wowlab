#!/bin/sh
# Bronze sim worker entrypoint (M0-03).
#
# M0 proves the pinned SimC binary runs and carries game data, then idles; the
# dequeue → profile → simc → result loop arrives with M2 (.claude/rules/worker.md).
# The health marker is written only after SimC has executed *and* answered a
# spell query with data, so a broken build or an empty data table shows up as
# an unhealthy container in `make up`, not at the first sim.
set -eu

MARKER=/tmp/bronze-simc-ok
SMOKE_LOG=/tmp/bronze-simc-smoke.log

echo "bronze-worker: simc ref ${SIMC_REF:-unknown} (branch ${SIMC_BRANCH:-unknown}) at $(command -v simc)"

# spell_query needs no profile and exercises the compiled-in game data. A
# query that matches nothing still exits 0, so the exit status alone only
# proves the process started; the data check below is the real test.
if ! simc spell_query=spell.id=133 >"$SMOKE_LOG" 2>&1; then
  echo "bronze-worker: simc smoke test failed:" >&2
  cat "$SMOKE_LOG" >&2
  exit 1
fi
# Spell 133 is Fireball at the pinned ref; an empty result means the binary
# ran without its game data.
if ! grep -q Fireball "$SMOKE_LOG"; then
  echo "bronze-worker: simc ran but spell_query=spell.id=133 returned no Fireball row:" >&2
  cat "$SMOKE_LOG" >&2
  exit 1
fi
touch "$MARKER"

# SimC's first output line is its own identity banner (engine version, game
# build, git build <branch> <sha>); surface it so `docker compose logs worker`
# shows exactly which engine answered.
echo "bronze-worker: $(head -n 1 "$SMOKE_LOG")"
echo "bronze-worker: SimC OK. Job loop lands with M2; idling."
exec sleep infinity
