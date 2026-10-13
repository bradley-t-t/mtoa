#!/usr/bin/env python3
"""Reads a bot token off stdin into the shared Slack config. Prints none of it.

    pbpaste | ssh <host.ssh> "python3 ~/lib/set_slack.py <channel id>"
"""
import json
import os
import sys

SHARED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slack.json")

channel = sys.argv[1] if len(sys.argv) > 1 else ""
token = sys.stdin.read().strip()
if not token.startswith("xoxb-"):
    raise SystemExit("stdin did not carry an xoxb- token")
try:
    with open(SHARED_PATH) as handle:
        config = json.load(handle)
except (OSError, ValueError):
    config = {}
config["bot_token"] = token
if channel:
    config["channel"] = channel
if not config.get("channel"):
    raise SystemExit("no channel: pass one as the first argument")
with open(SHARED_PATH, "w") as handle:
    json.dump(config, handle, indent=2)
os.chmod(SHARED_PATH, 0o600)
print("wrote %s: %d character token, channel %s" % (SHARED_PATH, len(token), config["channel"]))
