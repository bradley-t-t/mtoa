#!/usr/bin/env bash
# Try to fix the routine that just reported attention, then say what happened.
#
# Installed at ~/bin/routine-repair.sh, with the prompt at
# ~/routine-repair/routine-repair.prompt.md. Nothing calls this on a
# schedule and nothing calls it by hand: routine-deliver.py starts it, detached,
# at the moment it would otherwise have emailed the owner a failure and left it
# there. Its one argument is the pending record that dispatch wrote.
#
# WHY IT IS DETACHED. The run script above it holds the lock that keeps the next
# tick off its back. A repair that ran inside that script would hold the lock for
# its own hour too, so a machine with one broken routine would stop working the
# queue while it thought about it.
#
# THE CLOCK IS THE POINT. The turn is stopped at LIMIT and the message goes
# immediately after, which is what makes "an email inside the hour, fixed or
# not" a promise rather than a hope. An agent that needs longer than that has
# found something a person should see, and the message says so.
#
# THE MESSAGE IS SENT ON EVERY PATH OUT OF HERE. The trap is set before anything
# can fail, because the failure this file is most likely to hit - a missing
# prompt, an unreadable record, a lock held by another repair - is exactly the
# one that would otherwise leave a dispatched repair silent. Silence on this
# machine reads as a clean night.
set -uo pipefail

# Never, on any path. This process was started by one holding the send key, and
# the turn below runs unattended with permissions skipped: a key it can read is
# a key it can send with. Dispatch strips it from what it hands down; this is
# the second lock on the same door. The vault is asked for a fresh copy at the
# end, in a process the turn is not inside.
unset RESEND_API_KEY

PENDING=${1:-}
LOGS=$HOME/routine-logs
PROMPT=$HOME/routine-repair/routine-repair.prompt.md
DELIVER=$HOME/bin/routine-deliver.py
WITH=$HOME/.claude/tools/with-credential.py
MAIL_CRED=$(python3 "$HOME/.claude/tools/mtoa_config.py" get notify.mail_credential resend-api-key)

# 1 until the turn sets its own. Every early exit below is a repair that did not
# happen, and the report has to read that way rather than as a clean turn.
STATUS=1
ROUTINE=repair

report() {
  "$WITH" "$MAIL_CRED"=RESEND_API_KEY \
    -- python3 "$DELIVER" --report "$PENDING" "$STATUS" 2>&1 \
    | sed "s|^|$(date -u +%Y-%m-%dT%H:%M:%SZ)  $ROUTINE-repair  |" \
    >> "$LOGS/deliver.log"
}
trap report EXIT

if [ -z "$PENDING" ] || [ ! -r "$PENDING" ]; then
  echo "routine-repair: no pending record at '${PENDING:-}'" >&2
  exit 1
fi

# The record, as shell. Reading it here rather than passing eight arguments
# keeps dispatch and this script agreeing on one file instead of on an argument
# order that only one of them can see.
eval "$(python3 - "$PENDING" <<'PY'
import json, shlex, sys
record = json.load(open(sys.argv[1]))
for key in ("routine", "reason", "log", "repair_log"):
    print("%s=%s" % (key.upper(), shlex.quote(str(record.get(key, "")))))
print("RAN_EXIT=%s" % shlex.quote(str(record.get("exit", ""))))
print("LIMIT=%s" % shlex.quote(str(record.get("limit", 3000))))
PY
)"

[ -n "${ROUTINE:-}" ] || { echo "routine-repair: record names no routine" >&2; exit 1; }

set -a
[ -r $HOME/.claude/machine.env ] && . $HOME/.claude/machine.env
set +a

if [ ! -f "$PROMPT" ]; then
  echo "routine-repair: no prompt at $PROMPT" >&2
  exit 1
fi

# One repair at a time on this machine. Dispatch refuses to start a second while
# one is live, so reaching this means a record outlived its runner; the trap
# still reports, and the owner hears that this failure got no agent.
exec 9>"$LOGS/routine-repair.lock"
if ! flock -n 9; then
  echo "routine-repair: another repair holds the lock" >&2
  exit 1
fi

TURN="$LOGS/$ROUTINE-repair.prompt"
if ! python3 "$HOME/.claude/tools/mtoa_config.py" render "$PROMPT" routine-repair > "$LOGS/$ROUTINE-repair.prompt.body"; then
  echo "routine-repair: the prompt could not be filled from the profile" >&2
  exit 1
fi
{
  printf '[ROUTINE REPAIR %s]\n\n' "$ROUTINE"
  cat "$LOGS/$ROUTINE-repair.prompt.body"
  printf '\n\n---\n\n# The run you are fixing\n\n'
  printf -- '- Routine: `%s`\n' "$ROUTINE"
  printf -- '- What it reported: %s\n' "$REASON"
  printf -- '- Exit status: `%s`\n' "$RAN_EXIT"
  printf -- '- Its log: `%s/%s`\n' "$LOGS" "$LOG"
  printf -- '- Its run script: `%s/bin/%s-run.sh`\n' "$HOME" "$ROUTINE"
  printf -- '- Your budget: %s seconds from now, and the runner stops you at it.\n\n' "$LIMIT"
  printf 'The tail of that log:\n\n```\n'
  tail -c 6000 "$LOGS/$LOG" 2>/dev/null
  printf '\n```\n'
} > "$TURN"

# The strongest model, for the same reason the error routine gets it: this turn
# edits the plumbing every other routine runs on, unattended, with no review
# behind it.
MODEL=${ROUTINE_MODEL:-claude-opus-5}

timeout "$LIMIT" claude -p --model "$MODEL" --dangerously-skip-permissions \
  < "$TURN" > "$LOGS/$REPAIR_LOG" 2>&1
STATUS=$?

ln -sfn "$LOGS/$REPAIR_LOG" "$LOGS/$ROUTINE-repair-latest.log"
exit $STATUS
