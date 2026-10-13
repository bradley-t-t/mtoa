#!/usr/bin/env bash
# Run the error routine as one scheduled turn.
#
# Installed at ~/bin/error-monitor-run.sh, with the prompt at
# ~/error-monitor/error-monitor.prompt.md. Nothing calls this
# directly on a schedule: error-monitor-tick.sh reads the ticket queue every
# ten minutes and execs this, under the lock, only when a claimable ticket is
# open.
#
# The prompt lives beside the routine rather than in a repository checkout, so
# the routine keeps working with no mirror on the machine to pull from.
#
# The rule that no routine moves money or sends a message is matched against a
# marker in the prompt. A timer that starts a turn without it runs outside the
# rule while looking identical to a run inside it, so the marker is stamped here
# rather than written into a prompt file a future runner could copy without.
set -uo pipefail

ID=error-monitor
PROMPT=$HOME/error-monitor/error-monitor.prompt.md
LOGS=$HOME/routine-logs
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOGS/$ID-$STAMP.log"

mkdir -p "$LOGS"
set -a
[ -r $HOME/.claude/machine.env ] && . $HOME/.claude/machine.env
set +a

if [ ! -f "$PROMPT" ]; then
  echo "$ID: no prompt at $PROMPT" | tee "$LOG"
  exit 1
fi

# The cap, and the part of it the turn does not get to spend on the work.
#
# The turn cannot see its own clock: it has no elapsed time and no start, so
# "you have an hour" is a number it can only guess against. Both bounds are
# therefore stamped in as absolute UTC times it can compare `date -u` to, and
# the reserve is subtracted here rather than in the prompt so the two can never
# drift apart. Ten minutes is what step 3 and the status line cost when the
# work is already decided - reading each claimed ticket back, concluding the
# ones the worker did not, printing one line.
CAP=3600
RESERVE=600
DEADLINE=$(date -u -d "+$CAP seconds" +%Y-%m-%dT%H:%M:%SZ)
RETURN_BY=$(date -u -d "+$((CAP - RESERVE)) seconds" +%Y-%m-%dT%H:%M:%SZ)

# The prompt as this owner's profile fills it in. A prompt that cannot be
# filled is not run half-filled.
if ! python3 "$HOME/.claude/tools/mtoa_config.py" render "$PROMPT" "$ID" > "$LOGS/$ID.prompt.body"; then
  echo "$ID: the prompt could not be filled from the profile" | tee "$LOG"
  exit 1
fi

{
  printf '[SCHEDULED TASK %s] [THIS TURN IS KILLED AT %s. EVERY WORKER RETURNS BY %s.]\n\n' \
    "$ID" "$DEADLINE" "$RETURN_BY"
  cat "$LOGS/$ID.prompt.body"
} > "$LOGS/$ID.prompt"

# The model routines are judged by. Set here rather than on the machine, so an
# interactive session started by hand is unaffected by it. This routine fixes
# defects on live sites and lands them through two pull requests, so it runs on
# the strongest model rather than the cheapest.
MODEL=${ROUTINE_MODEL:-claude-opus-5}

# The turn hands each repo's tickets to a background worker and waits for them.
# Print mode gives those workers ten minutes after the main turn ends, then
# kills them and exits - so a routine whose work is two pull requests, two
# required checks and a live fetch per ticket was being stopped at the point it
# had barely started landing anything.
#
# It took three runs that way, on 2 and 4 September, and every one of them died
# with most of its hour unspent: 12, 31 and 13 minutes used out of 60, mid-fix,
# with tickets left claimed in-progress and no status line printed. The logs
# read "Waiting on worker 184" and "FleetApp (284) is still running" as the
# process was terminated under them.
#
# Zero removes that inner clock, which leaves the timeout below as the only
# bound - and that is the bound this routine was designed around, the same 3600
# seconds the tick's 90-minute stale-claim rule is derived from. The failure
# mode this trades into is the honest one: a worker that overruns the hour is
# killed by the timeout and reported as a turn that ran out of time, which was
# taken for loud and is not. See the salvage below: print mode writes its log
# when the turn ends, so a turn killed at the cap writes nothing at all.
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0

timeout "$CAP" claude -p --model "$MODEL" --dangerously-skip-permissions \
  < "$LOGS/$ID.prompt" > "$LOG" 2>&1
STATUS=$?

# What the turn was doing when it was killed, where the log is empty.
#
# Print mode buffers: nothing reaches the log until the turn ends, so a turn the
# timeout kills leaves a zero-byte file and the report that follows carries an
# empty tail. The run of 2026-09-12T10:20Z is the case - an hour of work, three
# merged pull requests and a live release, and a log a person could learn
# nothing whatever from. The session transcript is the only surviving account,
# so its last exchanges are copied into the log rather than left for somebody to
# go and find. It is appended under a heading and never replaces anything: a log
# with output in it is the turn's own words and is left alone.
if [ "$STATUS" -eq 124 ] && [ ! -s "$LOG" ]; then
  python3 $HOME/bin/routine-salvage.py "$LOG" "$CAP" >> "$LOG" 2>&1 \
    || echo "no transcript recovered" >> "$LOG"
fi

# The door that answers on a machine with no terminal attached. It emails the
# owner only when the run needs him and stays quiet otherwise, so most nights
# nothing leaves here and the morning report carries the run instead; the rule
# it applies is written down in routine-deliver.py.
#
# The send key travels from the vault into that one process and is rendered
# nowhere. It is deliberately not in the environment of the turn above: that
# turn runs unattended with permissions skipped, and a key it can read is a key
# it can send with.
#
# What the delivery did is worth one line. Discarding it was how a routine could
# fail to report for a week while every run looked fine from the outside.
MAIL_CRED=$(python3 "$HOME/.claude/tools/mtoa_config.py" get notify.mail_credential resend-api-key)
$HOME/.claude/tools/with-credential.py "$MAIL_CRED"=RESEND_API_KEY \
  -- python3 $HOME/bin/routine-deliver.py "$ID" "$STATUS" "$LOG" 2>&1 \
  | sed "s|^|$(date -u +%Y-%m-%dT%H:%M:%SZ)  $ID  |" >> "$LOGS/deliver.log"

# A record a health check can read without parsing prose. Silence on this
# machine looks exactly like a clean night, so the last outcome of every run is
# written down rather than inferred from whether anything complained.
python3 - "$ID" "$STATUS" "$LOG" <<PY
import json, os, sys, time
routine, status, log = sys.argv[1], int(sys.argv[2]), sys.argv[3]
path = os.path.join(os.path.dirname(log), routine + ".status.json")
json.dump({"routine": routine, "exit": status, "at": time.time(),
           "log": os.path.basename(log)}, open(path, "w"))
PY

ln -sfn "$LOG" "$LOGS/$ID-latest.log"
exit $STATUS
