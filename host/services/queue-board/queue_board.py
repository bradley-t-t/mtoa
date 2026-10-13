#!/usr/bin/env python3
"""Keeps one Slack message showing the error work queue.

Sections: tickets an agent is working, tickets waiting, tickets parked as
unresolved, and tickets resolved in the last 24 hours. Older closed tickets
never appear. The message is never edited: on change the old one is deleted
and a fresh one posted.
"""

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
USER_AGENT = "sunday-queue-board/1.0"
DAY_SECONDS = 24 * 3600

with open(CONFIG_PATH) as handle:
    CONFIG = json.load(handle)

# The Slack helper is deployed beside the services rather than inside one.
sys.path.insert(0, os.path.join(BASE, os.pardir, "lib"))
import slack  # noqa: E402

TOKEN, CHANNEL = slack.settings(CONFIG)


def github(path):
    request = Request("https://api.github.com" + path, headers={
        "User-Agent": USER_AGENT,
        "Authorization": "Bearer %s" % CONFIG["github_token"],
        "Accept": "application/vnd.github+json",
    })
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read())


REWRITES = [
    ("Failed to load img:", "Image failed to load:"),
    ("Failed to load link:", "Linked file failed to load:"),
    ("Failed to load source:", "Media file failed to load:"),
    ("Failed to load script:", "Script failed to load:"),
    ("TypeError: Failed to fetch fetching", "Request failed to reach"),
    ("Failed to fetch fetching", "Request failed to reach"),
    ("Fetch is aborted fetching", "Request aborted while reaching"),
    ("Load failed fetching", "Request failed to reach"),
]


def short_url(url):
    """The most readable piece of a URL: the file name when the path ends in one, the host otherwise."""
    parts = re.match(r"https?://([^/\s]+)(/\S*)?", url)
    host = parts.group(1)
    segment = (parts.group(2) or "").rstrip("/").rsplit("/", 1)[-1]
    return segment if segment and "." in segment and len(segment) <= 45 else host


def line_for(issue):
    title = issue["title"].split(" (fp:")[0]
    site, joiner, message = title.partition(": ")
    if not joiner:
        site, message = "", title
    message = re.sub(r"https?://\S+", lambda m: short_url(m.group(0)), message).strip()
    for raw, readable in REWRITES:
        message = message.replace(raw, readable)
    if len(message) > 90:
        message = message[:90].rsplit(" ", 1)[0] + "..."
    ref = "[#%d](%s)" % (issue["number"], issue.get("html_url", ""))
    return "%s %s — %s" % (ref, site, message) if site else "%s %s" % (ref, message)


def main():
    repo = CONFIG["errors_repo"]
    # Both labels the error monitor claims. `labels=` is AND on this endpoint,
    # so each is fetched on its own and the two are merged by number.
    cutoff = time.time() - DAY_SECONDS
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))
    open_issues, closed = [], []
    for label in ("auto-report", "maintenance"):
        open_issues += github(
            "/repos/%s/issues?state=open&labels=%s&per_page=100" % (repo, label))
        closed += github(
            "/repos/%s/issues?state=closed&labels=%s&since=%s&per_page=100"
            % (repo, label, since))
    open_issues = list({issue["number"]: issue for issue in open_issues}.values())
    closed = list({issue["number"]: issue for issue in closed}.values())

    working, waiting, unresolved = [], [], []
    for issue in open_issues:
        labels = {label["name"] for label in issue.get("labels", [])}
        if "unresolved" in labels:
            unresolved.append(issue)
        elif "in-progress" in labels:
            working.append(issue)
        else:
            waiting.append(issue)
    # Only tickets an agent fixed count as resolved; client-side and transient
    # closes carry the no-defect label and stay off the board.
    recent = [i for i in closed if i.get("closed_at")
              and "resolved" in {label["name"] for label in i.get("labels", [])}
              and time.mktime(time.strptime(i["closed_at"], "%Y-%m-%dT%H:%M:%SZ")) >= cutoff]

    sections = [
        ("Being worked", working),
        ("Waiting", waiting),
        ("Unresolved", unresolved),
        ("Fixed in the last 24 hours", recent),
    ]
    parts = []
    for header, issues in sections:
        if issues:
            parts.append("**%s**\n%s" % (header, "\n".join(line_for(i) for i in issues)))
    description = "\n\n".join(parts) if parts else "No open work."
    color = 0xE74C3C if unresolved else 0x3498DB if (working or waiting) else 0x95A5A6

    content_key = hashlib.sha1(description.encode()).hexdigest()
    try:
        with open(STATE_PATH) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    if state.get("content_key") == content_key and state.get("slack_ts"):
        print("no change")
        return

    if state.get("slack_ts"):
        slack.delete(TOKEN, CHANNEL, state["slack_ts"])

    slack_ts = slack.post(TOKEN, CHANNEL, {
        "title": "Error Queue",
        "description": description[:12000],
        "color": color,
    }, username="Work Queue", fallback="Error Queue")
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w") as handle:
        json.dump({"content_key": content_key, "slack_ts": slack_ts}, handle)
    os.replace(tmp_path, STATE_PATH)
    print("queue board refreshed: %d working, %d waiting, %d unresolved, %d recent" % (
        len(working), len(waiting), len(unresolved), len(recent)))


if __name__ == "__main__":
    # One instance at a time; a second waits, then sees fresh state and posts nothing.
    with open(os.path.join(BASE, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main()
