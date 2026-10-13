#!/usr/bin/env bash
# Answer for a repair that never came back.
#
# Installed at ~/bin/routine-repair-sweep.sh and fired by cron:
#
#   */5 * * * *  ~/bin/routine-repair-sweep.sh
#
# routine-repair.sh sends on every path out of itself, so this only ever fires
# for a repair whose runner is gone: the machine rebooted mid-turn, the process
# was killed, or it never started at all. Without it, a dispatch that died would
# be the one thing this whole system refuses to allow - a failure the owner was
# never told about, looking exactly like a quiet night.
#
# The overdue check runs first and needs no credential, so the ordinary case,
# which is nothing pending, costs one Python start and never opens the vault.
set -uo pipefail

LOGS=$HOME/routine-logs
DELIVER=$HOME/bin/routine-deliver.py
WITH=$HOME/.claude/tools/with-credential.py
MAIL_CRED=$(python3 "$HOME/.claude/tools/mtoa_config.py" get notify.mail_credential resend-api-key)

python3 "$DELIVER" --overdue "$LOGS" >/dev/null 2>&1 || exit 0

"$WITH" "$MAIL_CRED"=RESEND_API_KEY \
  -- python3 "$DELIVER" --sweep "$LOGS" 2>&1 \
  | sed "s|^|$(date -u +%Y-%m-%dT%H:%M:%SZ)  repair-sweep  |" >> "$LOGS/deliver.log"
