#!/usr/bin/env python3
"""What a killed turn was doing, for a log it never got to write.

`claude -p` buffers: the log is written when the turn ends, so a turn the
runner's timeout kills leaves a zero-byte file. The report that follows then
carries an empty tail, which reads exactly like a turn that did nothing - and
on 2026-09-12 that log stood for an hour of work, three merged pull requests
and a release live in production.

The session transcript is written as the turn goes and survives the kill. This
reads the one belonging to the killed run and prints its last exchanges, so the
run script can append them under the log's own heading.

    routine-salvage.py <log path> <cap seconds>

Nothing here is authoritative. It is the last thing said before the process
died, and it is here so a person has somewhere to start.
"""

import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

PROJECTS = os.path.expanduser("~/.claude/projects")
ENTRIES = 14
WIDTH = 220


def started(path):
    """The timestamp on a transcript's first record, as epoch seconds."""
    try:
        with open(path) as fh:
            stamp = json.loads(fh.readline()).get("timestamp")
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def transcript(start):
    """The transcript whose first record sits closest to the run's start.

    A session that began minutes either side of it is a different run - the
    routines overlap on this machine - so anything further out than two
    minutes is not this turn and is left alone.
    """
    best, best_gap = None, 120
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        began = started(path)
        if began is None:
            continue
        gap = abs(began - start)
        if gap < best_gap:
            best, best_gap = path, gap
    return best


def described(block):
    """One line for one content block."""
    kind = block.get("type")
    if kind == "text":
        body = block.get("text", "").strip()
        return "said: " + body if body else None
    if kind == "tool_use":
        return "%s %s" % (block.get("name", "tool"),
                          json.dumps(block.get("input", {}), default=str))
    if kind == "tool_result":
        body = block.get("content")
        if not isinstance(body, str):
            body = json.dumps(body, default=str)
        return "result: " + body
    return None


def lines(path):
    out = []
    for line in open(path):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        content = (record.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else []
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        for block in blocks:
            said = described(block)
            if not said:
                continue
            when = record.get("timestamp", "")[11:19] or "--:--:--"
            out.append("%s  %s" % (when, " ".join(said.split())[:WIDTH]))
    return out


def main():
    if len(sys.argv) < 3:
        print("usage: routine-salvage.py <log path> <cap seconds>",
              file=sys.stderr)
        return 2
    cap = float(sys.argv[2])
    start = time.time() - cap

    path = transcript(start)
    if not path:
        print("no transcript found for a run starting %s"
              % datetime.fromtimestamp(start, timezone.utc).isoformat())
        return 1

    said = lines(path)
    print("The turn was killed at the %d second cap before it could write "
          "anything. What follows is the tail of its session transcript, "
          "%s, which is not the turn's own report."
          % (cap, os.path.basename(path)))
    print()
    for line in said[-ENTRIES:]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
