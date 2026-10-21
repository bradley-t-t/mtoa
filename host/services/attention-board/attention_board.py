#!/usr/bin/env python3
"""Keeps one Slack message listing only what somebody has to act on.

This is the channel that notifies, so it carries five things and nothing
else: tickets an agent could not resolve, sites that are not answering,
outreach mail the mail server is refusing in either direction, claims an
agent abandoned, and what the device guard found on a machine. Work in progress never appears here - an error being fixed is not
something to be told about.

When nothing needs him the message is deleted and none is posted, so an empty
channel is the all-clear. The message is never edited: on change the old one
goes and a fresh one takes its place, which is what makes it notify.
"""

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
MONITOR_DIR = os.path.join(os.path.expanduser("~"), "uptime-monitor")
GUARD_DIR = os.path.join(os.path.expanduser("~"), "device-guard")
USER_AGENT = "sunday-attention-board/1.0"
RED = 0xE74C3C

# The outreach tables, where the profile names them (outreach.supabase_url). A
# host with no outreach behind it carries no mail section at all.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
import mtoa_config  # noqa: E402
SUPABASE = os.environ.get("SUPABASE_URL") or mtoa_config.get("outreach.supabase_url", "")

# What makes a run of refused letters an outage rather than one bad address: a
# length, because the mail server refuses a single letter for its own reasons,
# and an age, because a run still inside its first minutes is a retry in
# progress. Both have to hold.
MAIL_FAILURES = 5
MAIL_MINUTES = 20

# The same question asked of the inbound side, which has no letter of its own
# to count: how many of the reply reader's hourly passes have to be refused
# before the mailbox is shut rather than busy. Three is three hours of replies
# nobody has read.
REPLIES_FAILURES = 3

# How many passes an unreadable letters table is allowed before it is raised
# itself. At one pass every two minutes that is half an hour, which rides out a
# blip without ever letting "cannot tell" pass for "nothing wrong".
MAIL_UNREAD_PASSES = 15

# The Slack helper is deployed beside the services rather than inside one.
sys.path.insert(0, os.path.join(BASE, os.pardir, "lib"))
import slack  # noqa: E402

with open(CONFIG_PATH) as handle:
    CONFIG = json.load(handle)

TOKEN, CHANNEL = slack.settings(CONFIG)


def github(path):
    request = Request("https://api.github.com" + path, headers={
        "User-Agent": USER_AGENT,
        "Authorization": "Bearer %s" % CONFIG["github_token"],
        "Accept": "application/vnd.github+json",
    })
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def table(name, query):
    """One read of an outreach table with the service-role key the unit hands in.

    A missing key raises, which is reported rather than read as a clean mail
    path: not knowing whether mail works is not the same as mail working.
    """
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise RuntimeError("no service-role key in the environment")
    request = Request(SUPABASE.rstrip("/") + "/rest/v1/" + name + "?" + query, headers={
        "User-Agent": USER_AGENT,
        "apikey": key,
        "Authorization": "Bearer %s" % key,
    })
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def letters():
    """The last outbound attempts newest first, and when one last got through.

    Both are needed to say anything true. The window shows whether the refusals
    are a run or one bad address; the last letter that went says how long the
    outage has actually been, which a window of refusals cannot, because its own
    oldest row is only as old as the window.
    """
    recent = table("outreach_messages",
                   "select=status,error,created_at&direction=eq.outbound"
                   "&status=in.(sent,failed)&order=created_at.desc&limit=60")
    gone = table("outreach_messages",
                 "select=created_at&direction=eq.outbound&status=eq.sent"
                 "&order=created_at.desc&limit=1")
    return recent, (stamp(gone[0]["created_at"]) if gone else None)


def unread():
    """The run of refused passes the reply reader is in, or None while it reads.

    Both directions ride one login, and only the outbound half leaves a row
    per letter: a mailbox that stops being read leaves nothing behind but the
    job's own runs, so this reads those, two days of them: a window that ends
    inside the outage reports the window rather than the outage. A pass still
    in flight has not failed and is skipped rather than counted. Nothing else watches this at all, and
    a reply nobody reads is a reply nobody answers - 42 hours of them by the
    morning of 8 October, with the outbound alert standing the whole time and
    saying nothing about the inbox.
    """
    runs = table("outreach_runs", "select=started_at,finished_at,error&job=eq.watch"
                                  "&order=started_at.desc&limit=48")
    refused = []
    for run in runs:
        if not run.get("finished_at"):
            continue
        if not run.get("error"):
            break
        refused.append(run)
    if len(refused) < REPLIES_FAILURES:
        return None
    return {"passes": len(refused), "since": stamp(refused[-1]["started_at"]),
            "error": refused[0]["error"]}


def stamp(value):
    """A Postgres timestamp as epoch seconds."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def reason(error):
    """A mail error on one line, which is where it names its own cause."""
    text = " ".join((error or "").split())
    return text[:180] if text else "no reason recorded"


def read_json(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def parse_time(value):
    return time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def since(seconds):
    hours = seconds / 3600
    if hours < 1:
        return "%d min" % max(1, int(seconds // 60))
    if hours < 48:
        return "%dh" % round(hours)
    return "%dd" % round(hours / 24)


def readable(title):
    """The ticket title without its fingerprint, which means nothing to a reader."""
    return re.sub(r"\s*\(fp:[0-9a-f]+\)$", "", title)


def collect(now, state):
    """Every item that needs a person, each with a key that only changes when the item does.

    `state` carries across passes, which the letters read needs: a table it
    could not reach is only worth raising once it has stayed unreachable.
    """
    items = []
    repo = CONFIG["errors_repo"]

    # Both labels the error monitor claims, so a maintenance run that dies
    # mid-ticket is raised as stalled the same as an error run. `labels=` is
    # AND on this endpoint, so the two are fetched separately and merged.
    issues = []
    for label in ("auto-report", "maintenance"):
        issues += github("/repos/%s/issues?state=open&labels=%s&per_page=100" % (repo, label))
    issues = list({issue["number"]: issue for issue in issues}.values())
    stall_seconds = CONFIG.get("stall_hours", 3) * 3600
    for issue in issues:
        labels = {label["name"] for label in issue.get("labels", [])}
        if "unresolved" in labels:
            items.append({
                "key": "unresolved:%d" % issue["number"],
                "sort": (0, issue["number"]),
                "text": "[#%d](%s) %s" % (issue["number"], issue["html_url"], readable(issue["title"])),
                "group": "Needs a decision",
            })
        elif "in-progress" in labels:
            # A claim nobody concluded: the agent died mid-run, and without this
            # the ticket sits claimed forever and no other run will touch it.
            claimed_for = now - parse_time(issue["updated_at"])
            if claimed_for > stall_seconds:
                items.append({
                    "key": "stalled:%d" % issue["number"],
                    "sort": (2, issue["number"]),
                    "text": "[#%d](%s) %s — claimed %s ago with no verdict" % (
                        issue["number"], issue["html_url"], readable(issue["title"]), since(claimed_for)),
                    "group": "Stalled",
                })

    monitor_config = read_json(os.path.join(MONITOR_DIR, "config.json"), {"sites": []})
    monitor_state = read_json(os.path.join(MONITOR_DIR, "state.json"), {})
    for site in monitor_config.get("sites", []):
        entry = monitor_state.get(site["url"], {})
        if entry.get("down"):
            down_for = now - (entry.get("down_since") or now)
            items.append({
                "key": "down:%s" % site["url"],
                "sort": (1, site["name"]),
                "text": "**%s** is not answering — down %s" % (site["name"], since(down_for)),
                "group": "Down",
            })

    # A new way into one of the machines, or a protection that is off. The
    # device guard keeps each one open until it clears or is accepted.
    guard_state = read_json(os.path.join(GUARD_DIR, "state.json"), {})
    for device, entry in sorted(guard_state.get("devices", {}).items()):
        for key, finding in sorted(entry.get("findings", {}).items()):
            items.append({
                "key": "security:%s:%s" % (device, key),
                "sort": (1, "security", device, key),
                "text": "**%s**: %s" % (device, finding["text"]),
                "group": "Security",
            })

    # Outbound mail goes wrong as a whole rather than one letter at a time: the
    # login is refused and every letter behind it is refused with it. Nothing
    # else here watches it, so an outage stayed invisible until a routine
    # happened to try to send - 17 hours and 213 refused letters on 7 October.
    # Each read stands on its own: one of them failing must not blank the other,
    # because an item missing from this pass is an alert deleted from the
    # channel. Either failing counts toward the one unreadable counter.
    recent, last_sent, inbox, trouble = None, None, None, None
    if not SUPABASE:
        items.sort(key=lambda item: item["sort"])
        return items
    try:
        recent, last_sent = letters()
    except Exception as error:
        trouble = error
    try:
        inbox = unread()
    except Exception as error:
        trouble = trouble or error
    if trouble is None:
        state["mail_unread"] = 0
    else:
        passes = int(state.get("mail_unread") or 0) + 1
        state["mail_unread"] = passes
        print("outreach mail unreadable (%d pass(es)): %s" % (passes, trouble))
        if passes >= MAIL_UNREAD_PASSES:
            items.append({
                "key": "mail-unread",
                "sort": (1, "mail"),
                "text": "**Outreach mail cannot be read**, so whether letters are "
                        "going out is unknown — %s" % trouble,
                "group": "Down",
            })

    if recent:
        run = []
        for letter in recent:
            if letter.get("status") != "failed":
                break
            run.append(letter)
        refusing_for = now - stamp(run[-1]["created_at"]) if run else 0
        if len(run) >= MAIL_FAILURES and refusing_for >= MAIL_MINUTES * 60:
            quiet = since(now - last_sent) if last_sent else "as long as it has records for"
            items.append({
                "key": "mail-refused",
                "sort": (1, "mail"),
                "text": "**Outreach mail is being refused** — nothing has gone out in %s "
                        "and the last %d attempts all failed: %s" % (
                            quiet, len(run), reason(run[0].get("error"))),
                "group": "Down",
            })

    if inbox:
        items.append({
            "key": "replies-unread",
            "sort": (1, "mail"),
            "text": "**Outreach replies are not being read** — the mailbox has refused "
                    "the reply reader for its last %d passes, going back %s: %s" % (
                        inbox["passes"], since(now - inbox["since"]), reason(inbox["error"])),
            "group": "Down",
        })

    items.sort(key=lambda item: item["sort"])
    return items


def save(content_key, slack_ts, state):
    """What the next pass reads: the message standing, and the passes counted.

    Written on every pass, not only the ones that post, because the unreadable
    counter is what tells a blip from a table that has stopped answering.
    """
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w") as handle:
        json.dump({"content_key": content_key, "slack_ts": slack_ts,
                   "mail_unread": state.get("mail_unread", 0)}, handle)
    os.replace(tmp_path, STATE_PATH)


def main():
    now = time.time()
    state = read_json(STATE_PATH, {})
    try:
        items = collect(now, state)
    except Exception as error:
        # Saying nothing is safer than clearing a live alert on a failed read.
        print("attention board build failed: %s" % error)
        return

    # The key covers what the items are, never how long they have been that
    # way, so a duration ticking upward cannot repost and notify again.
    content_key = hashlib.sha1("|".join(item["key"] for item in items).encode()).hexdigest()
    if state.get("content_key") == content_key and bool(state.get("slack_ts")) == bool(items):
        print("no change (%d item(s))" % len(items))
        save(content_key, state.get("slack_ts"), state)
        return

    if state.get("slack_ts"):
        slack.delete(TOKEN, CHANNEL, state["slack_ts"])

    slack_ts = None
    if items:
        groups = {}
        for item in items:
            groups.setdefault(item["group"], []).append(item["text"])
        description = "\n\n".join(
            "**%s**\n%s" % (group, "\n".join(lines))
            for group, lines in groups.items()
        )
        heading = "%d thing%s waiting on you" % (len(items), "" if len(items) == 1 else "s")
        slack_ts = slack.post(TOKEN, CHANNEL, {
            "title": heading,
            "description": description[:12000],
            "color": RED,
        }, username="Needs You")

    save(content_key, slack_ts, state)
    print("attention board: %d item(s)%s" % (len(items), "" if items else " — channel cleared"))


if __name__ == "__main__":
    # One instance at a time; a second waits, then sees fresh state and posts nothing.
    with open(os.path.join(BASE, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main()
