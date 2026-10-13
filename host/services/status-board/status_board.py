#!/usr/bin/env python3
"""Keeps one Slack message showing every site the public status page shows.

Runs as a oneshot under a systemd timer. Sites, names, domains and health all
come from the same feed the status page reads, so the board and the page cannot
name different sites or disagree about one. The message is never edited: when
status changes, the old message is deleted and a fresh one posted.

`--check` compares the posted message against the feed and exits non-zero on a
difference, which is what proves the two are in step without reading the
channel by eye.
"""

import fcntl
import hashlib
import json
import os
import sys
import time
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
USER_AGENT = "sunday-status-board/2.0"
DEFAULT_STATUS_URL = "http://127.0.0.1:8791/status.json"

GREEN, YELLOW, RED = "\U0001F7E2", "\U0001F7E1", "\U0001F534"
EMBED_COLORS = {GREEN: 0x2ECC71, YELLOW: 0xF1C40F, RED: 0xE74C3C}
# Slack shows a symbol by name, so --check has to read the names back as dots.
EMOJI_NAMES = {GREEN: ":large_green_circle:", YELLOW: ":large_yellow_circle:",
               RED: ":red_circle:"}

with open(CONFIG_PATH) as handle:
    CONFIG = json.load(handle)

# The Slack helper is deployed beside the services rather than inside one.
sys.path.insert(0, os.path.join(BASE, os.pardir, "lib"))
import slack  # noqa: E402

TOKEN, CHANNEL = slack.settings(CONFIG)


def feed():
    """The status page's own feed, uncached, so the board never shows an older list than the page."""
    url = CONFIG.get("status_url", DEFAULT_STATUS_URL)
    url += ("&" if "?" in url else "?") + "t=%d" % time.time()
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=25) as response:
        return json.loads(response.read())


def dot_for(site, unresolved_sites):
    """Red when the site is not answering or carries a defect nobody could fix,
    yellow while anything else is open against it, green otherwise.

    The feed calls both kinds of open ticket "degraded" because a visitor has no
    use for the distinction; here the two are worth telling apart.
    """
    if site["status"] == "outage" or site["name"] in unresolved_sites:
        return RED
    if site["status"] == "degraded":
        return YELLOW
    return GREEN


def rows_from(body):
    """One row per site on the public page, in the order the board lists them."""
    unresolved = {i["site"] for i in body.get("incidents", []) if i.get("status") == "identified"}
    rows = [(s["name"], s.get("domain") or "", dot_for(s, unresolved)) for s in body.get("sites", [])]
    rows.sort(key=lambda row: row[0].lower())
    return rows


def lines_from(rows):
    return ["%s %s — %s" % (dot, name, domain) for name, domain, dot in rows]


def load_state():
    try:
        with open(STATE_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def posted_lines():
    """The lines the board is currently showing in Slack, or None if it is showing nothing."""
    state = load_state()
    if not state.get("slack_ts"):
        return None
    posted = slack.posted_attachment(TOKEN, CHANNEL, state["slack_ts"])
    if not posted:
        return None
    text = slack.plain(posted.get("text") or "")
    for dot, name in EMOJI_NAMES.items():
        text = text.replace(name, dot)
    return text.splitlines()


def check():
    """Reports every way the posted board differs from the feed. Exit code is the verdict."""
    try:
        body = feed()
    except Exception as error:
        print("FAIL: status feed unreadable (%s)" % error)
        return 2
    expected = lines_from(rows_from(body))
    if not expected:
        print("FAIL: the feed lists no sites")
        return 2

    showing = posted_lines()
    if showing is None:
        print("FAIL: the board has no message posted; %d site(s) on the page" % len(expected))
        return 1

    missing = [line for line in expected if line not in showing]
    extra = [line for line in showing if line not in expected]
    if not missing and not extra:
        print("in step: %d site(s), matching the status page" % len(expected))
        return 0
    print("FAIL: the board and the status page disagree")
    for line in missing:
        print("  on the page, not on the board: %s" % line)
    for line in extra:
        print("  on the board, not on the page: %s" % line)
    return 1


def main():
    try:
        body = feed()
    except Exception as error:
        # The reporter serves this feed and the page reads it too, so a failure
        # here is already an outage the monitor raises. Freezing the board on
        # the last true list beats posting a guess about what is live.
        print("status feed unreadable (%s); keeping the current message" % error)
        return
    rows = rows_from(body)
    if not rows:
        print("feed lists no sites, keeping the current message")
        return

    lines = lines_from(rows)
    worst = RED if any(r[2] == RED for r in rows) else YELLOW if any(r[2] == YELLOW for r in rows) else GREEN
    content_key = hashlib.sha1("\n".join(lines).encode()).hexdigest()

    state = load_state()
    if state.get("content_key") == content_key and state.get("slack_ts"):
        print("no change")
        return

    if state.get("slack_ts"):
        slack.delete(TOKEN, CHANNEL, state["slack_ts"])

    slack_ts = slack.post(TOKEN, CHANNEL, {
        "title": "Sites",
        "description": "\n".join(lines),
        "color": EMBED_COLORS[worst],
    }, username="Site Status", fallback="Sites")
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w") as handle:
        json.dump({"content_key": content_key, "slack_ts": slack_ts}, handle)
    os.replace(tmp_path, STATE_PATH)
    print("board refreshed: %d sites, worst %s" % (len(rows), worst))


if __name__ == "__main__":
    # One instance at a time; a second waits, then sees fresh state and posts nothing.
    with open(os.path.join(BASE, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sys.exit(check() if "--check" in sys.argv[1:] else main() or 0)
