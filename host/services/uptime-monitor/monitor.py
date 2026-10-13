#!/usr/bin/env python3
"""Checks each configured site and posts a Slack alert when one goes down or recovers.

Runs as a oneshot under a systemd timer. State lives next to the script so
alerts fire on transitions only, never on every failing check.
"""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
HISTORY_PATH = os.path.join(BASE, "history.jsonl")
USER_AGENT = "sunday-uptime-monitor/1.0"
# What a client site's pause page carries; see check_site.
PAUSED_HEADER = "x-site-paused"

# The Slack helper is deployed beside the services rather than inside one.
sys.path.insert(0, os.path.join(BASE, os.pardir, "lib"))
import slack  # noqa: E402
COLOR_DOWN = 0xE74C3C
COLOR_UP = 0x2ECC71
COLOR_INFO = 0x3498DB


def check_site(url, timeout, accept=()):
    """Whether the site answered, what it said, and whether it is paused on purpose."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return True, "HTTP %d" % response.status, False
    except HTTPError as error:
        # A client site paused over an unpaid invoice answers every page with a
        # 503 from its own middleware, marked so it cannot be mistaken for a
        # failure. The host is answering, so it is not down, and it comes back
        # by itself once the invoice is paid, so nobody is alerted either way.
        if error.code == 503 and error.headers.get(PAUSED_HEADER):
            return True, "paused (%s)" % error.headers.get(PAUSED_HEADER), True
        # Some backends answer probes with an auth challenge; a listed status
        # still proves the host is up, unlike the DNS failure a dead one gives.
        return error.code in accept, "HTTP %d" % error.code, False
    except URLError as error:
        return False, str(error.reason), False
    except Exception as error:
        return False, str(error) or type(error).__name__, False


def post_alerts(config, embeds):
    token, channel = slack.settings(config)
    if not (token and channel):
        print("slack is not configured; nothing posted", file=sys.stderr)
        return False
    ok = True
    # Slack stops rendering attachments long before this, and one site per
    # message would be a wall of notifications, so they go up in tens.
    for start in range(0, len(embeds), 10):
        batch = embeds[start:start + 10]
        try:
            slack.post(token, channel, batch, username="Uptime Monitor",
                       fallback=batch[0].get("title"))
        except Exception as error:
            print("slack post failed: %s" % error, file=sys.stderr)
            ok = False
    return ok


def humanize(seconds):
    seconds = int(seconds)
    if seconds < 3600:
        return "%d min" % max(1, seconds // 60)
    if seconds < 86400:
        return "%dh %dm" % (seconds // 3600, seconds % 3600 // 60)
    return "%dd %dh" % (seconds // 86400, seconds % 86400 // 3600)


def load_state():
    try:
        with open(STATE_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w") as handle:
        json.dump(state, handle, indent=2)
    os.replace(tmp_path, STATE_PATH)


def main():
    with open(CONFIG_PATH) as handle:
        config = json.load(handle)
    sites = config["sites"]
    timeout = config.get("timeout_seconds", 12)
    threshold = config.get("fail_threshold", 3)

    if "--test" in sys.argv:
        sent = post_alerts(config, [{
            "title": "Uptime monitor online",
            "description": "Watching %d sites from the Pi. Alerts fire after %d failed checks a minute apart." % (len(sites), threshold),
            "color": COLOR_INFO,
        }])
        sys.exit(0 if sent else 1)

    state = load_state()
    now = time.time()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda site: check_site(site["url"], timeout, tuple(site.get("accept", ()))), sites))

    down_embeds = []
    up_embeds = []
    down_sites = []
    up_sites = []

    for site, (ok, detail, paused) in zip(sites, results):
        entry = state.setdefault(site["url"], {"fails": 0, "down": False, "down_since": None})
        # The status page reads this to leave the site off while the pause lasts.
        if paused:
            entry["paused"] = True
        else:
            entry.pop("paused", None)
        if ok:
            if entry["down"]:
                duration = humanize(now - (entry["down_since"] or now))
                up_embeds.append({
                    "title": "%s is back up" % site["name"],
                    "description": "%s\nDown for %s (%s now)" % (site["url"], duration, detail),
                    "color": COLOR_UP,
                })
                up_sites.append(entry)
            else:
                entry["fails"] = 0
        else:
            entry["fails"] += 1
            if entry["fails"] == 1:
                entry["down_since"] = now
            print("%s failing (%d/%d): %s" % (site["name"], entry["fails"], threshold, detail))
            if entry["fails"] >= threshold and not entry["down"]:
                down_embeds.append({
                    "title": "%s is down" % site["name"],
                    "description": "%s\n%s\nFailing since <t:%d:f>" % (
                        site["url"], detail, int(entry["down_since"])),
                    "color": COLOR_DOWN,
                })
                down_sites.append(entry)

    # Mark transitions only after Slack accepts them, so a failed post retries next run.
    posted = False
    if down_embeds and post_alerts(config, down_embeds):
        posted = True
        for entry in down_sites:
            entry["down"] = True
    if up_embeds and post_alerts(config, up_embeds):
        posted = True
        for entry in up_sites:
            entry["down"] = False
            entry["fails"] = 0
            entry["down_since"] = None
    if posted:
        # One history line per transition Slack accepted, picked out of the
        # full sweep by identity with the embed lists.
        with open(HISTORY_PATH, "a") as handle:
            for site, (ok, detail, _) in zip(sites, results):
                entry = state[site["url"]]
                if any(entry is e for e in (down_sites if not ok else up_sites)):
                    handle.write(json.dumps({"at": now, "url": site["url"], "name": site["name"], "state": "down" if not ok else "up", "detail": detail}) + "\n")
        subprocess.Popen(["sudo", "systemctl", "start", "--no-block",
                          "status-board.service", "queue-board.service"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    save_state(state)


if __name__ == "__main__":
    main()
