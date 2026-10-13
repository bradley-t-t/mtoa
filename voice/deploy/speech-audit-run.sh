#!/usr/bin/env bash
# Hourly check of Sunday's speech log for repeats and stale answers.
#
# Installed by deploy/deploy-server.sh at ~/bin/speech-audit-run.sh alongside the voice
# assistant it reads, and fired by cron:
#
#   17 * * * *  flock -n ~/routine-logs/speech-audit.lock ~/bin/speech-audit-run.sh
#
# The audit is plain Python, no model turn. Its last line is the routine status
# line, and a run that finds problems goes through routine-deliver.py exactly like
# the other routines, so it reaches the owner by the usual attention path. A clean
# run opens no vault and sends nothing.
set -uo pipefail

ID=speech-audit
LOGS="$HOME/routine-logs"
# The routine mail key's name in the credential store, from the profile.
MAIL_CRED=$(python3 "$HOME/.claude/tools/mtoa_config.py" get notify.mail_credential resend-api-key)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOGS/$ID-$STAMP.log"
mkdir -p "$LOGS"

"$HOME/voice-assistant/venv/bin/python" "$HOME/voice-assistant/speech_audit.py" --hours 1 > "$LOG" 2>&1
STATUS=$?

if [ $STATUS -ne 0 ] || grep -q '^ROUTINE-STATUS: attention' "$LOG"; then
  "$HOME/.claude/tools/with-credential.py" "$MAIL_CRED"=RESEND_API_KEY \
    -- python3 "$HOME/bin/routine-deliver.py" "$ID" "$STATUS" "$LOG" 2>&1 \
    | sed "s|^|$(date -u +%Y-%m-%dT%H:%M:%SZ)  $ID  |" >> "$LOGS/deliver.log"
else
  # What routine-deliver.py does for a clean run: clear the hold, so the next real
  # problem is not mistaken for a repeat of an older one.
  rm -f "$LOGS/$ID.deliver.json"
fi

python3 - "$ID" "$STATUS" "$LOG" <<PY
import json, os, sys, time
routine, status, log = sys.argv[1], int(sys.argv[2]), sys.argv[3]
path = os.path.join(os.path.dirname(log), routine + ".status.json")
json.dump({"routine": routine, "exit": status, "at": time.time(),
           "log": os.path.basename(log)}, open(path, "w"))
PY

ln -sfn "$LOG" "$LOGS/$ID-latest.log"
# Hourly runs add up; a week of them is plenty.
find "$LOGS" -name "$ID-2*.log" -mtime +7 -delete
exit $STATUS
