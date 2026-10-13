#!/usr/bin/env bash
# Start the error routine only when there is something for it to work.
#
# Installed at ~/bin/error-monitor-tick.sh and fired by cron:
#
#   */10 * * * *  ~/bin/error-monitor-tick.sh
#
# The routine is continuous rather than scheduled: it exists to answer a filed
# ticket. Waking a full turn on a timer to discover an empty queue costs the
# same as one that finds work, so the queue is read here, cheaply, and the turn
# only starts when the answer is yes.
#
# A turn may run for an hour while this fires every ten minutes, so the lock is
# what keeps one run from landing on top of another.
set -uo pipefail

LOCK=$HOME/routine-logs/error-monitor.lock
mkdir -p $HOME/routine-logs

REPO=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/queue-board/config.json'))).get('errors_repo',''))" 2>/dev/null)
[ -n "$REPO" ] || exit 0

# The count has to ask the same question the routine's claim rule asks, which
# skips a ticket already labeled in-progress or unresolved. Counting every open
# auto-report ticket instead meant a parked one - a real defect this
# environment cannot fix, left open on purpose - woke a full turn every ten
# minutes to rediscover there was nothing to claim, for as long as it sat
# there.
#
# That question alone was not enough, because it also skips a ticket stranded
# in-progress by a run that died, which is the one case where the queue holds
# work and nothing wakes to do it. #284 sat claimed for eight hours and was
# read as somebody else's work by every one of the forty-eight ticks that
# passed over it. The old answer was that the attention board raises a stalled
# claim after three hours, but that tells a person rather than fixing it, and a
# routine that needs a person to restart it is not continuous. So the stale
# ones are asked for separately below, and either question waking a turn is
# enough. The routine's own claim rule uses the same 90-minute cutoff.
#
# Two labels reach the routine, and a comma is OR in GitHub search.
# `auto-report` is a browser error the reporter filed. `maintenance` is a
# PageSpeed score the daily routine measured under 90. Both are a defect on a
# live site and both are worked the same way, so both wake a turn. Every other
# open ticket stays invisible here, which is what keeps the fifteen unlabeled
# ones from waking a run around the clock.
OPEN=$(gh issue list --repo "$REPO" --state open \
  --search 'label:auto-report,maintenance -label:unresolved -label:in-progress' \
  --limit 1 --json number --jq length 2>/dev/null)

# A turn is capped at an hour, so a claim older than 90 minutes belongs to no
# live run. Anything younger is left alone: a run mid-flight is not a dead one.
STALE_BEFORE=$(date -u -d '90 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)
if [ -n "$STALE_BEFORE" ]; then
  STALE=$(gh issue list --repo "$REPO" --state open \
    --search "label:auto-report,maintenance label:in-progress -label:unresolved updated:<$STALE_BEFORE" \
    --limit 1 --json number --jq length 2>/dev/null)
else
  STALE=0
fi

# What this tick saw, written for the server feed. The routine leaves a
# record only when a turn finishes, and a quiet queue means no turn, so read
# from that record alone a clean day looks like a routine that stopped - the
# console said "Error Monitor is behind" over an empty queue on 2026-09-14.
# This is the thing that fires every ten minutes, so this is what says the
# routine is alive, and it says how much was waiting so a ticket nothing starts
# on still shows. A tick whose queries went unanswered writes nothing, because
# a question gh could not answer is not a queue that was read.
TICK=$HOME/routine-logs/error-monitor.tick.json
if [ -n "${OPEN:-}" ] && [ -n "${STALE:-}" ]; then
  printf '{"at": %s, "open": %s, "stale": %s}\n' "$(date -u +%s)" "$OPEN" "$STALE" > "$TICK.tmp" \
    && mv "$TICK.tmp" "$TICK"
fi

[ "${OPEN:-0}" -gt 0 ] || [ "${STALE:-0}" -gt 0 ] || exit 0

exec flock -n "$LOCK" $HOME/bin/error-monitor-run.sh
