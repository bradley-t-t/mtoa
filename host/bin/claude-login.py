#!/usr/bin/env python3
"""Put this machine's Claude login where a nested `claude` can read it.

Every routine turn here authenticates from `CLAUDE_CODE_OAUTH_TOKEN`, sourced
out of ~/.claude/machine.env by the run script. Claude Code keeps that token
out of the environment it hands its own child processes, so a `claude` the turn
shells out to - the vision check behind `scripts.autobuild.gate`, the letter
writer - never sees it and falls back to ~/.claude/.credentials.json. When the
stored login there has lapsed, those calls fail with "OAuth session expired and
could not be refreshed" and the work they gate is skipped quietly: the preview
build went on drawing sites with nobody looking at them.

So the run script calls this before the turn starts. It reads the token from the
environment and files it as the stored login when the stored one is empty or
spent, and leaves a live one alone. It prints one line about what it did and
never the token.

    CLAUDE_CODE_OAUTH_TOKEN=... python3 ~/bin/claude-login.py
"""

import json
import os
import sys
import time

PATH = os.path.expanduser("~/.claude/.credentials.json")
# Long-lived tokens from `claude setup-token` do not carry an expiry, so one
# is filed a year out: far enough that nothing tries to refresh it, close
# enough that a token revoked upstream still shows up as a failed call rather
# than a login that claims to be good forever.
LIFETIME = 365 * 24 * 3600
# A login with less than this left is replaced rather than handed to a turn
# that runs for four hours.
MARGIN = 6 * 3600


def main():
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    try:
        with open(PATH) as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        stored = {}
    oauth = stored.get("claudeAiOauth") or {}
    expires = (oauth.get("expiresAt") or 0) / 1000
    if oauth.get("accessToken") and expires - time.time() > MARGIN:
        print("the stored Claude login is good until " + time.strftime("%Y-%m-%d %H:%M", time.localtime(expires)))
        return 0
    if not token:
        print("the stored Claude login has lapsed and CLAUDE_CODE_OAUTH_TOKEN is not set, "
              "so every nested claude call in this turn will be refused")
        return 1
    oauth["accessToken"] = token
    oauth["expiresAt"] = int((time.time() + LIFETIME) * 1000)
    oauth.setdefault("refreshToken", "")
    stored["claudeAiOauth"] = oauth
    new = PATH + ".new"
    with open(new, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(stored, handle)
    os.replace(new, PATH)
    print("filed this machine's token as the stored Claude login, so the turn's nested calls authenticate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
