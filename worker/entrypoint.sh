#!/bin/sh
# Bronze sim worker entrypoint (M0-03).
#
# M0 proves the pinned SimC binary runs, then idles; the dequeue → profile →
# simc → result loop arrives with M2 (.claude/rules/worker.md). The health
# marker is written only after SimC has actually executed, so a broken build
# shows up as an unhealthy container in `make up`, not at the first sim.
set -eu

MARKER=/tmp/bronze-simc-ok
SMOKE_LOG=/tmp/bronze-simc-smoke.log

echo "bronze-worker: simc ref ${SIMC_REF:-unknown} at $(command -v simc)"

# spell_query needs no profile and exercises the compiled-in game data.
if ! simc spell_query=spell.id=133 >"$SMOKE_LOG" 2>&1; then
  echo "bronze-worker: simc smoke test failed:" >&2
  cat "$SMOKE_LOG" >&2
  exit 1
fi
touch "$MARKER"

echo "bronze-worker: SimC OK. Job loop lands with M2; idling."
exec sleep infinity
