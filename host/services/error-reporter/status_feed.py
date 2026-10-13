"""Builds the public status feed the agency status page reads.

Everything here is written for a site visitor: site names, plain-English
incident descriptions, and uptime - never URLs, hostnames, ticket links, or
stack traces. Health comes from the uptime monitor's state and history, and
incidents from the error tickets, with the agents' `Public:` lines preferred
over the built-in phrasing whenever one exists.
"""

import json
import os
import re
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# The owner's profile reader, deployed beside the services in ../lib.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
import mtoa_config  # noqa: E402

MONITOR_DIR = os.path.join(os.path.expanduser("~"), "uptime-monitor")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "status-cache.json")
CACHE_SECONDS = 20
WINDOW_DAYS = 30
USER_AGENT = "sunday-status-feed/1.0"

# Internal services are not visitor-facing; a monitored name carrying one of
# these markers stays off the public page.
HIDDEN_NAME_MARKERS = ("Backend", "Error Reporter")

# The name a ticket reads under when it belongs to no site of its own.
# Everything behind the sites is one service as far as a visitor is
# concerned, and a ticket raised against it is that service reporting.
INFRASTRUCTURE_NAME = mtoa_config.get("host.status_name", "Cloud Services")
HOSTLIKE = re.compile(r"(https?://\S+|\b[\w.-]+\.(?:com|org|net|co|io|app|dev)\b(?:/\S*)?)", re.I)

TEMPLATES = {
    "resource": ("Some files did not load", "A visitor's browser could not load part of a page. We are checking whether the cause is on our side."),
    "network": ("A background request failed", "Part of the site could not reach one of the services it depends on."),
    "http": ("A service answered with an error", "One of the services behind the site returned an error to a visitor."),
    "console": ("A page reported an error", "Something went wrong while a page was running. We are looking into it."),
    "uncaught": ("A page hit an error", "Something went wrong while a page was running. We are looking into it."),
    "rejection": ("A page hit an error", "Something went wrong while a page was running. We are looking into it."),
}
DEFAULT_TEMPLATE = ("We are looking into a reported error", "A visitor ran into a problem on the site and we are checking it.")
RESOLVED_TEMPLATES = {
    "resource": "Files that had failed to load were fixed and checked on the live site; they load normally now.",
    "network": "A request that was failing to reach one of the site's services was fixed and checked on the live site.",
    "http": "A service behind the site that had been answering with errors was fixed and checked on the live site.",
    "console": "An error a page had been hitting was fixed and checked on the live site.",
    "uncaught": "An error a page had been hitting was fixed and checked on the live site.",
    "rejection": "An error a page had been hitting was fixed and checked on the live site.",
}
DEFAULT_RESOLVED = "The reported problem was fixed and checked on the live site."
# Claim-time wording that must never stand as the description of a resolved incident.
IN_PROGRESS_WORDING = re.compile(r"\b(looking into|investigat|checking|working on|is being|are being|now)\b", re.I)
STATE_TEXT = {
    "investigating": "We are looking into it now.",
    "identified": "We know what is wrong; the fix needs hands-on work and is in progress.",
    "resolved": "Fixed and verified.",
}

_lock = threading.Lock()
_cache = {"built": 0, "body": None, "building": False}
_public_lines = {}


def scrub(text):
    """Strips anything that reads like an address so public copy never names internals."""
    return re.sub(r"\s{2,}", " ", HOSTLIKE.sub("a service", text or "")).strip()


def github(config, path):
    request = Request("https://api.github.com" + path, headers={
        "User-Agent": USER_AGENT,
        "Authorization": "Bearer %s" % config["github_token"],
        "Accept": "application/vnd.github+json",
    })
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def public_line(config, issue):
    """The newest `Public:` sentence an agent left on the ticket, cached per ticket revision."""
    key = (issue["number"], issue.get("updated_at"))
    if key in _public_lines:
        return _public_lines[key]
    text = ""
    try:
        comments = github(config, "/repos/%s/issues/%d/comments?per_page=100" % (config["errors_repo"], issue["number"]))
        for comment in comments:
            for line in (comment.get("body") or "").splitlines():
                if line.strip().lower().startswith("public:"):
                    text = line.split(":", 1)[1].strip()
    except Exception:
        pass
    _public_lines[key] = text
    return text


def read_json(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def daily_uptime(url, state_entry, now):
    """One bucket per day for the last 30 days: the share of that day the check was passing."""
    window_start = now - WINDOW_DAYS * 86400
    transitions = []
    try:
        with open(os.path.join(MONITOR_DIR, "history.jsonl")) as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("url") == url:
                    transitions.append((record["at"], record["state"]))
    except OSError:
        pass
    transitions.sort()
    # Build the down intervals, closing an open one at "now" when still down.
    intervals = []
    down_since = None
    for at, state in transitions:
        if state == "down" and down_since is None:
            down_since = at
        elif state == "up" and down_since is not None:
            intervals.append((down_since, at))
            down_since = None
    if down_since is None and state_entry.get("down") and state_entry.get("down_since"):
        down_since = state_entry["down_since"]
    if down_since is not None:
        intervals.append((down_since, now))
    days = []
    day_start = int(now // 86400) * 86400 - (WINDOW_DAYS - 1) * 86400
    since = state_entry.get("since")
    for i in range(WINDOW_DAYS):
        start = day_start + i * 86400
        end = min(start + 86400, now)
        if end <= start:
            continue
        date = time.strftime("%Y-%m-%d", time.gmtime(start))
        # Only the part of a day that was actually watched can be scored. A day
        # that ended before watching started carries no figure at all rather
        # than a hopeful 100%.
        measured_start = start if since is None else max(start, since)
        if measured_start >= end:
            days.append({"date": date, "uptime": 100.0, "measured": False})
            continue
        down = 0.0
        for a, b in intervals:
            down += max(0.0, min(b, end) - max(a, measured_start))
        span = end - measured_start
        days.append({"date": date, "uptime": round(max(0.0, 100.0 - down / span * 100.0), 2), "measured": True})
    return days


def uptime_percent(url, state_entry, now):
    """Uptime over the window actually watched, so a young check cannot claim 30 days."""
    """Share of the last 30 days the check was passing, from the monitor's transition log."""
    window_start = now - WINDOW_DAYS * 86400
    transitions = []
    try:
        with open(os.path.join(MONITOR_DIR, "history.jsonl")) as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("url") == url:
                    transitions.append((record["at"], record["state"]))
    except OSError:
        pass
    transitions.sort()
    down_seconds = 0.0
    down_since = None
    for at, state in transitions:
        if state == "down" and down_since is None:
            down_since = at
        elif state == "up" and down_since is not None:
            down_seconds += max(0.0, min(at, now) - max(down_since, window_start))
            down_since = None
    if down_since is None and state_entry.get("down") and state_entry.get("down_since"):
        down_since = state_entry["down_since"]
    if down_since is not None:
        down_seconds += max(0.0, now - max(down_since, window_start))
    since = state_entry.get("since")
    measured_from = max(window_start, since) if since is not None else window_start
    measured = max(1.0, now - measured_from)
    return round(max(0.0, 100.0 - down_seconds / measured * 100.0), 2)


def build(config):
    now = time.time()
    monitor_config = read_json(os.path.join(MONITOR_DIR, "config.json"), {"sites": []})
    monitor_state = read_json(os.path.join(MONITOR_DIR, "state.json"), {})
    excluded = set(config.get("status_exclude", []))

    # A client site paused over an unpaid invoice is off on purpose, and a
    # client's billing is nobody's business on a public page, so the site and
    # its incidents leave the page while the pause lasts. The monitor marks the
    # entry; any domain of a site being paused takes the whole row with it.
    paused = {site.get("alias_of") or site["name"] for site in monitor_config.get("sites", [])
              if monitor_state.get(site["url"], {}).get("paused")}

    # `auto-report` alone, on purpose. The error monitor also works
    # `maintenance` tickets, and those carry a client site's PageSpeed score.
    # This page is public, so a score a client has not been told about is not
    # published here. Widening this query would publish it.
    open_issues = github(config, "/repos/%s/issues?state=open&labels=auto-report&per_page=100" % config["errors_repo"])
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - WINDOW_DAYS * 86400))
    closed_issues = github(config, "/repos/%s/issues?state=closed&labels=auto-report&since=%s&per_page=100" % (config["errors_repo"], since))

    # The reporter files a ticket under its site, ahead of a colon. A ticket
    # raised against the setup itself carries a sentence instead, and splitting
    # that on a colon prints a whole ticket title where the page prints a site
    # name. So a prefix is a site only where the monitor or the repository map
    # knows it, and everything else - an internal service included - reads as
    # the infrastructure it came from.
    known_sites = {site["name"] for site in monitor_config.get("sites", [])}
    known_sites.update(config.get("repo_map", {}))
    known_sites.update(excluded)
    known_sites = {name for name in known_sites if not any(marker in name for marker in HIDDEN_NAME_MARKERS)}

    def site_of(issue):
        prefix = issue["title"].split(":", 1)[0].strip()
        return prefix if prefix in known_sites else INFRASTRUCTURE_NAME

    def labels_of(issue):
        return {label["name"] for label in issue.get("labels", [])}

    def kind_of(issue):
        match = re.search(r"^Kind:\s*(\S+)", issue.get("body") or "", re.M)
        return (match.group(1) if match else "").lower()

    incidents = []
    for issue in open_issues:
        labels = labels_of(issue)
        state = "identified" if "unresolved" in labels else "investigating"
        incidents.append((issue, state))
    for issue in closed_issues:
        labels = labels_of(issue)
        if "resolved" in labels and issue.get("closed_at"):
            incidents.append((issue, "resolved"))

    public = []
    degraded_sites = set()
    for issue, state in incidents:
        site = site_of(issue)
        if state != "resolved":
            degraded_sites.add(site)
        title, summary = TEMPLATES.get(kind_of(issue), DEFAULT_TEMPLATE)
        line = scrub(public_line(config, issue))
        if state == "resolved":
            summary = RESOLVED_TEMPLATES.get(kind_of(issue), DEFAULT_RESOLVED)
            if line and IN_PROGRESS_WORDING.search(line) and not re.search(r"\b(again|normally|restored|fixed|resolved|back)\b", line, re.I):
                line = ""
        updates = [{"at": issue["created_at"], "text": "We noticed a problem and opened an investigation."}]
        if state != "investigating" or "in-progress" in labels_of(issue):
            updates.append({"at": issue.get("closed_at") or issue.get("updated_at"), "text": line or STATE_TEXT[state]})
        public.append({
            "id": issue["number"],
            "site": site,
            "title": title,
            "summary": line or summary,
            "status": state,
            "opened_at": issue["created_at"],
            "updated_at": issue.get("updated_at"),
            "resolved_at": issue.get("closed_at") if state == "resolved" else None,
            "updates": updates,
        })
    public = [incident for incident in public if incident["site"] not in paused]
    public.sort(key=lambda i: i["opened_at"], reverse=True)

    # One row per site, not per domain. A site that answers on several brand
    # domains is checked on each of them - an alias going down is still an
    # outage - but it reads as the one site it is, with the others listed
    # beside it. `alias_of` in the monitor config names the row an entry joins.
    STATUS_RANK = {"operational": 0, "degraded": 1, "outage": 2}
    rows = {}
    order = []
    for site in monitor_config.get("sites", []):
        name = site["name"]
        if name in excluded or any(marker in name for marker in HIDDEN_NAME_MARKERS):
            continue
        primary = site.get("alias_of") or name
        if primary in excluded or primary in paused:
            continue
        entry = monitor_state.get(site["url"], {})
        if entry.get("down"):
            status = "outage"
        elif primary in degraded_sites or name in degraded_sites:
            status = "degraded"
        else:
            status = "operational"
        domain = re.sub(r"^https?://", "", site["url"]).rstrip("/")
        measurement = {
            "domain": domain,
            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(entry["since"])) if entry.get("since") else None,
            "status": status,
            "uptime": uptime_percent(site["url"], entry, now),
            "days": daily_uptime(site["url"], entry, now),
            "down_since": entry.get("down_since") if entry.get("down") else None,
        }
        if primary not in rows:
            rows[primary] = []
            order.append(primary)
        # The row's own entry leads; aliases follow in config order.
        if site.get("alias_of"):
            rows[primary].append(measurement)
        else:
            rows[primary].insert(0, measurement)

    sites = []
    for name in order:
        group = rows[name]
        worst = max(group, key=lambda m: STATUS_RANK[m["status"]])
        # A row is only as healthy as its least healthy domain, day by day.
        day_count = min(len(m["days"]) for m in group)
        days = []
        for i in range(day_count):
            # A day is scored only where some domain in the row was watched; a
            # day none of them were watched carries no figure rather than a zero.
            scored = [m["days"][i]["uptime"] for m in group if m["days"][i].get("measured")]
            days.append({
                "date": group[0]["days"][i]["date"],
                "uptime": min(scored) if scored else 100.0,
                "measured": bool(scored),
            })
        down_since = min((m["down_since"] for m in group if m["down_since"]), default=None)
        sites.append({
            "id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
            "name": name,
            "measured_since": min((m["since"] for m in group if m["since"]), default=None),
            "domain": group[0]["domain"],
            "domains": [m["domain"] for m in group],
            "status": worst["status"],
            "uptime_30d": round(min(m["uptime"] for m in group), 2),
            "days": days,
            "open_issues": sum(1 for i in public if i["site"] == name and i["status"] != "resolved"),
            "down_since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(down_since)) if down_since else None,
        })

    overall = "outage" if any(s["status"] == "outage" for s in sites) else "degraded" if any(s["status"] == "degraded" for s in sites) else "operational"
    outages_30d = 0
    try:
        with open(os.path.join(MONITOR_DIR, "history.jsonl")) as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("state") == "down" and record.get("at", 0) >= now - WINDOW_DAYS * 86400 and not any(m in record.get("name", "") for m in HIDDEN_NAME_MARKERS):
                    outages_30d += 1
    except OSError:
        pass

    watched_from = min((s["measured_since"] for s in sites if s["measured_since"]), default=None)
    return {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "measured_since": watched_from,
        "overall": overall,
        "window_days": WINDOW_DAYS,
        "outages_30d": outages_30d,
        "sites": sites,
        "incidents": public,
    }


# The mirror, and it is not running. `start_publisher` is called from nowhere
# and `publish_target` returns None without SUPABASE_URL and
# SUPABASE_SERVICE_ROLE_KEY, neither of which is in the service's
# environment. So the block below is inert on the Pi today and the public
# page reads /status.json off the Tailscale Funnel instead.
#
# It is kept rather than deleted because it answers a failure that has
# actually happened: ticket #515 recorded the Vercel-to-Pi route dropping
# for five minutes while the funnel served other traffic fine, and a page
# reading a mirrored object would not have noticed. Wiring it means putting
# a service-role key in a long-running daemon's environment, which is a
# decision to take rather than one to restore quietly.
PUBLISH_INTERVAL_SECONDS = 60
PUBLISH_TIMEOUT_SECONDS = 15
PUBLISH_CACHE_CONTROL = "max-age=15"


def publish_target():
    """The object the feed is mirrored to and the key that writes it, or None."""
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not base or not key:
        return None
    bucket = os.environ.get("STATUS_BUCKET", "status")
    name = os.environ.get("STATUS_OBJECT", "status.json")
    return "%s/storage/v1/object/%s/%s" % (base, bucket, name), key


def publish(body):
    """Mirrors a freshly built body to object storage.

    The status page reads the mirrored object rather than this host, which is a
    home connection a datacenter has to hold a link to for the length of a
    response. Reaching the object costs the page nothing that depends on this
    host being reachable at the moment a visitor arrives.

    An upload that fails leaves the previous object in place and is reported
    rather than raised: serving /status.json here does not depend on it.
    """
    target = publish_target()
    if not target:
        return False
    url, key = target
    payload = body.encode("utf-8")
    request = Request(url, data=payload, method="POST")
    request.add_header("apikey", key)
    request.add_header("Authorization", "Bearer %s" % key)
    request.add_header("Content-Type", "application/json")
    request.add_header("Cache-Control", PUBLISH_CACHE_CONTROL)
    request.add_header("x-upsert", "true")
    try:
        with urlopen(request, timeout=PUBLISH_TIMEOUT_SECONDS) as response:
            response.read()
    except HTTPError as error:
        print("status feed publish rejected: HTTP %s" % error.code)
        return False
    except Exception as error:
        print("status feed publish failed: %s" % type(error).__name__)
        return False
    print("status feed published %d bytes" % len(payload))
    return True


def keep_published(config, interval=PUBLISH_INTERVAL_SECONDS):
    """Rebuilds on a timer so the mirrored object tracks the monitor.

    A build is otherwise driven by a request to /status.json, and the status
    page reads the mirror instead of this host, so nothing asks for one. The
    timer is what keeps the object from holding a single stale build.
    """
    while True:
        try:
            rebuild(config)
        except Exception as error:
            print("status feed refresh failed: %s" % type(error).__name__)
        time.sleep(interval)


def start_publisher(config):
    threading.Thread(target=keep_published, args=(config,), daemon=True).start()


def rebuild(config):
    """Builds a fresh body and keeps it, on disk as well so a restart starts warm."""
    try:
        body = json.dumps(build(config))
    except Exception as error:
        print("status feed build failed: %s" % error)
        # The flag a request set on the way in comes back down here, or no
        # request ever starts another build: feed() reads it as a build still
        # running and serves the cache forever. One DNS failure at 04:29 on
        # 2026-09-15 left the flag up, and the feed sat four and a half hours
        # stale on a reporter that answered every request with 200.
        with _lock:
            _cache["building"] = False
        return None
    with _lock:
        _cache.update(built=time.time(), body=body, building=False)
    try:
        with open(CACHE_PATH, "w") as handle:
            handle.write(body)
    except OSError as error:
        print("status feed cache write failed: %s" % error)
    publish(body)
    return body


def feed(config):
    """The last good body, refreshed behind the request rather than during it.

    A build reads GitHub and the monitor's history, which takes seconds. Doing
    that while a visitor waits is how the page ends up staring at a request
    that never lands, so a request is answered from the cache and the refresh
    happens after it.
    """
    with _lock:
        now = time.time()
        body = _cache["body"]
        stale = not body or now - _cache["built"] >= CACHE_SECONDS
        start_refresh = stale and not _cache.get("building")
        if start_refresh:
            _cache["building"] = True
    if body and not start_refresh:
        return body
    if body:
        threading.Thread(target=rebuild, args=(config,), daemon=True).start()
        return body
    # Nothing cached in memory: fall back to the last body written to disk, and
    # only build inline when there is nothing at all to answer with.
    try:
        with open(CACHE_PATH) as handle:
            disk = handle.read()
        if disk:
            with _lock:
                _cache.update(body=disk, built=0.0)
            threading.Thread(target=rebuild, args=(config,), daemon=True).start()
            return disk
    except OSError:
        pass
    fresh = rebuild(config)
    if fresh:
        return fresh
    with _lock:
        _cache["building"] = False
    return json.dumps({"updated_at": None, "overall": "unknown", "sites": [], "incidents": [], "error": "feed unavailable"})
