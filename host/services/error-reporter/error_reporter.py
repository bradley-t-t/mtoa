#!/usr/bin/env python3
"""Receives browser error reports from the sites and turns them into work.

Listens on localhost behind a Tailscale Funnel that terminates TLS. Sites POST
JSON to /report; each accepted report goes to Slack, and one that carries a
new fingerprint also files a ticket for an agent to pick up. The same process
serves /status.json for the public status board, /server.json for the console's
reading of this machine, and a webhook path the boards and the ticket repo call
back on.

Three separate limits stand between a broken page and the channel: a per-IP
rate, a per-fingerprint cooldown, and a global cap over five minutes.
"""

import hashlib
import hmac
import json
import re
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import os
import sys

import server_feed
import status_feed

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
COLOR_ERROR = 0xE67E22
MAX_BODY_BYTES = 32 * 1024

with open(CONFIG_PATH) as handle:
    CONFIG = json.load(handle)

# The Slack helper is deployed beside the services rather than inside one.
sys.path.insert(0, os.path.join(BASE, os.pardir, "lib"))
import slack  # noqa: E402
import mtoa_config  # noqa: E402  the owner's names, from the profile

TOKEN, CHANNEL = slack.settings(CONFIG)

LOCK = threading.Lock()
PER_IP = {}
DEDUPE = {}
GLOBAL_SENDS = deque()


def prune(now):
    for ip in [k for k, v in PER_IP.items() if not v or now - v[-1] > 120]:
        del PER_IP[ip]
    for key in [k for k, v in DEDUPE.items() if now - v["last_sent"] > 2 * CONFIG["dedupe_cooldown_seconds"]]:
        del DEDUPE[key]


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


# What a rebuild changes about an address, and nothing else.
#
# A built asset carries a content hash the bundler regenerates on every build,
# so the same file has a different name after every deploy. The fingerprint is
# what decides whether a defect has been seen before, and while it can see that
# token the answer is always no: the fault arrives forever as a first sighting,
# a recurrence count never rises, and an open ticket never blocks a duplicate.
# Measured over every ticket ever filed, 146 of 447 carry a name a rebuild
# rewrote, and they stand for 268 distinct faults rather than 447.
#
# The token is recognised by mixed case rather than by length, and that is the
# whole difficulty. A Vite hash is drawn from [A-Za-z0-9_-] and is effectively
# never all one case; a file a person named on these sites is kebab-case and
# lower. `dSappg14` and `CFiiKkjO` are hashes, `dumbbell` and `portrait` are
# words, and a rule that could not tell them apart would file one ticket for
# two different photographs - which is exactly the pair this queue carried on
# 2026-09-09. The 8-to-10 window is Vite's own, and it is what keeps
# `rotate-ccw-BioUyuA_.js` from collapsing onto `rotate-cw-lGzZCjY8.js`: the
# shorter tail is the hash, and the longer one is a name.
# The sentinel has to be refused by the rule that writes it. `HASH` is itself
# mixed case, so once it is in place the name segment ahead of it supplies the
# lowercase and the sentinel supplies the uppercase, and a second pass reads the
# pair as a fresh hash: `rotate-ccw-HASH.js` loses its `ccw`, and `LiveChat`
# loses its `Live`. Nothing calls this twice today, so nothing is wrong in
# production, but a rule whose own output defeats it is one refactor away from
# quietly merging two different files. The lookahead below is what makes
# `stable(stable(x))` equal `stable(x)`.
HASHED_ASSET = re.compile(
    r"-(?![A-Za-z0-9_-]*HASH\.)"
    r"(?=[A-Za-z0-9_-]{8,10}\.)(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[A-Z])"
    r"[A-Za-z0-9_-]+(\.(?:m?js|css|woff2?|mp4|webm|webp|avif|png|jpe?g|gif|svg|map))"
)

# A query string is the same problem arriving a second way. The retry the sites
# added to recover a lost chunk puts `?retry=2` on the address, which forked one
# reader's single fault across three fingerprints before this was written.
QUERY_STRING = re.compile(r"\?[^\s\"')\]]*")

# A build id or a cache key, which moves for the same reason and looks like
# nothing else in a message.
LONG_HEX = re.compile(r"\b[0-9a-f]{16,}\b")


def stable(message):
    """The message with everything a rebuild changes taken out of it.

    Deliberately left in: the host, the path, the error class and the site.
    Those are what make two faults different, and a fingerprint that cannot see
    them would file one ticket for the whole website.
    """
    text = QUERY_STRING.sub("", message)
    text = HASHED_ASSET.sub(r"-HASH\1", text)
    return LONG_HEX.sub("HEX", text)


def short_url(url):
    """The most readable piece of a URL: the file name when the path ends in one, the host otherwise."""
    parts = re.match(r"https?://([^/\s]+)(/\S*)?", url)
    host = parts.group(1)
    segment = (parts.group(2) or "").rstrip("/").rsplit("/", 1)[-1]
    return segment if segment and "." in segment and len(segment) <= 45 else host


def readable(message):
    """Turns a raw error message into a sentence a person can scan: short URLs, plain phrasing."""
    text = re.sub(r"https?://\S+", lambda m: short_url(m.group(0)), message).strip()
    for raw, plain in REWRITES:
        text = text.replace(raw, plain)
    return text


def cut_words(text, limit):
    """Shortens to `limit`, marking the cut with an ellipsis.

    Prefers the last word boundary, but falls back to a hard cut when that
    boundary sits in the first half — one very long token would otherwise throw
    away most of the text it was meant to shorten.
    """
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0]
    return (head if len(head) > limit // 2 else text[:limit]) + "..."


def clip(value, limit):
    return str(value)[:limit] if value else ""


def post_slack(embed):
    slack.post(TOKEN, CHANNEL, embed, username="Error Reporter")


DISMISSED_PATH = os.path.join(BASE, "dismissed.json")
DISMISSED_LOCK = threading.Lock()
# An error already judged not to be the site's fault should not cost another
# ticket and another agent run every time it happens again. It is held back
# instead, and let through only if it starts happening often enough that the
# judgement deserves revisiting.
RECUR_THRESHOLD = 10
RECUR_WINDOW = 86400


# What every ticket this fingerprint has ever produced concluded, kept for as
# long as the fingerprint keeps arriving.
#
# The ledger above answers one question - is this error held back right now -
# and it answers it by forgetting. Reaching the threshold deletes the entry, so
# the moment a fingerprint earns a second look is the moment everything known
# about it is thrown away, and the ticket that follows carries one browser's
# report and no sign that eleven tickets came before it.
#
# That is what the queue has actually been doing. `fp:deaad70208f6`, a hero
# video on Dickinson Bayou Fleeting, filed thirteen tickets: twelve turns read
# a single report each, reasoned that one lost request looks like a network
# blip, and closed no-defect. The thirteenth found the defect and fixed it. The
# font on Delux Financial Solutions ran the same course over twelve tickets, and
# `fp:` families on RootRise Holdings were fixed four separate times because
# nothing told the fourth run the first three had already shipped a fix that did
# not hold.
#
# None of those runs was reasoning badly on what it was given. One report of one
# file failing for one visitor genuinely is indistinguishable from a blip. What
# separates them is the count, and the count is the one thing the ticket never
# carried. So it is written down here, survives the release, and goes into the
# ticket body where the run reads it.
HISTORY_PATH = os.path.join(BASE, "history.json")
HISTORY_LOCK = threading.Lock()
# How many prior tickets to name before the list stops being worth reading.
HISTORY_LIST_CAP = 12


def read_history():
    try:
        with open(HISTORY_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def write_history(history):
    tmp_path = HISTORY_PATH + ".tmp"
    with open(tmp_path, "w") as handle:
        json.dump(history, handle, indent=2)
    os.replace(tmp_path, HISTORY_PATH)


def note_report(fingerprint):
    """Counts one arrival of this error, filed or not.

    Called once per report on the dispatch path, so it counts the reports a
    hold-back swallowed as well as the ones that became tickets. That total is
    the honest measure of how often visitors are meeting this, which is the
    number a transient explanation has to survive.
    """
    with HISTORY_LOCK:
        history = read_history()
        entry = history.setdefault(fingerprint, {})
        entry["reports"] = entry.get("reports", 0) + 1
        entry["last_seen"] = time.time()
        entry.setdefault("first_seen", entry["last_seen"])
        entry.setdefault("filings", [])
        write_history(history)


def note_filing(fingerprint, issue_number):
    """Records that this fingerprint produced a ticket."""
    if not issue_number:
        return
    with HISTORY_LOCK:
        history = read_history()
        entry = history.setdefault(fingerprint, {})
        filings = entry.setdefault("filings", [])
        if not any(filing.get("issue") == issue_number for filing in filings):
            filings.append({"issue": issue_number, "at": time.time(), "verdict": None})
        # The count of reports restarts against the ticket now open, so the
        # figure the next ticket carries is reports since the last verdict
        # rather than reports since the beginning of time.
        entry["reports_at_filing"] = entry.get("reports", 0)
        write_history(history)


def note_verdict(fingerprint, issue_number, verdict):
    """Records what a ticket concluded, creating the fingerprint's history if it has none.

    Creating it here is what backfills: the verdict sweep reads every ticket
    touched in the last fortnight, so the queue standing when this shipped
    arrives with its verdicts rather than starting from an empty file.
    """
    if not issue_number:
        return
    with HISTORY_LOCK:
        history = read_history()
        entry = history.setdefault(fingerprint, {})
        filings = entry.setdefault("filings", [])
        for filing in filings:
            if filing.get("issue") == issue_number:
                if filing.get("verdict") != verdict:
                    filing["verdict"] = verdict
                    write_history(history)
                return
        filings.append({"issue": issue_number, "at": time.time(), "verdict": verdict})
        filings.sort(key=lambda filing: filing.get("issue") or 0)
        write_history(history)


def note_release(fingerprint):
    """Records that the recurrence threshold let this error file again."""
    with HISTORY_LOCK:
        history = read_history()
        entry = history.setdefault(fingerprint, {})
        entry["released"] = entry.get("released", 0) + 1
        write_history(history)


def history_note(fingerprint):
    """The prior tickets on this fingerprint, as a block for the ticket body.

    Empty for a first sighting, which is most reports and wants no preamble.
    """
    with HISTORY_LOCK:
        entry = read_history().get(fingerprint) or {}
    filings = [filing for filing in entry.get("filings", []) if filing.get("issue")]
    if not filings:
        return ""

    numbers = [filing["issue"] for filing in filings]
    shown = numbers[-HISTORY_LIST_CAP:]
    listed = ", ".join("#%d" % number for number in shown)
    if len(numbers) > len(shown):
        listed = "%s, and %d older" % (listed, len(numbers) - len(shown))

    lines = ["## Previously",
             "",
             "This error has been filed %d time%s before: %s."
             % (len(numbers), "" if len(numbers) == 1 else "s", listed)]

    tally = {}
    for filing in filings:
        verdict = filing.get("verdict") or "still open"
        tally[verdict] = tally.get(verdict, 0) + 1
    counted = ", ".join("%d %s" % (count, verdict)
                        for verdict, count in sorted(tally.items(), key=lambda item: -item[1]))
    lines.append("Those closed: %s." % counted)

    since = entry.get("reports", 0) - entry.get("reports_at_filing", 0)
    if since > 0:
        # A floor, and worth saying so: the per-IP limit, the dedupe cooldown
        # and the global cap each turn a report away before it is counted, so
        # the figure is smallest exactly when the error is at its worst.
        lines.append("Reports of it since the last of those was filed: %d, and that is a floor "
                     "- a burst from one visitor counts once and the rate limits drop the rest."
                     % since)

    fixed = [filing["issue"] for filing in filings if filing.get("verdict") == "resolved"]
    if fixed:
        lines.append("")
        if len(fixed) == 1:
            shipped = "A fix shipped in #%d and was verified live" % fixed[0]
            read = "its conclusion"
        else:
            shipped = ("%d separate tickets shipped a fix and verified it live, most recently #%d"
                       % (len(fixed), fixed[-1]))
            read = "their conclusions"
        lines.append("This has been fixed before and has come back. %s. Whatever is wrong now, "
                     "the answer is not that fix again: read %s and find out why it did not "
                     "hold." % (shipped, read))

    dismissed = [filing for filing in filings if filing.get("verdict") == "no-defect"]
    if dismissed:
        lines.append("")
        lines.append("%d earlier ticket%s closed no-defect, so the transient explanation has "
                     "already been tried and the error came back anyway. Repeats are the "
                     "evidence a single report cannot carry: these are separate visitors on "
                     "separate connections meeting the same file, and a blip does not choose "
                     "one file and stay on it. Closing this no-defect again needs an "
                     "explanation of why this asset in particular keeps failing while the site "
                     "is correct - not that it answers when you fetch it now."
                     % (len(dismissed), "" if len(dismissed) == 1 else "s"))

    return "\n".join(lines) + "\n"


def read_dismissed():
    try:
        with open(DISMISSED_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def write_dismissed(ledger):
    tmp_path = DISMISSED_PATH + ".tmp"
    with open(tmp_path, "w") as handle:
        json.dump(ledger, handle, indent=2)
    os.replace(tmp_path, DISMISSED_PATH)


def fingerprint_of(title):
    match = re.search(r"\(fp:([0-9a-f]+)\)", title or "")
    return match.group(1) if match else None


def record_verdict(issue):
    """Keeps the ledger in step with what the tickets say.

    A ticket closed as no-defect adds its fingerprint; anything else about that
    fingerprint - a reopen, a fix, a relabel - takes it back out, so a verdict
    that changes stops holding the error back.
    """
    fingerprint = fingerprint_of(issue.get("title"))
    if not fingerprint:
        return
    labels = {label.get("name") for label in issue.get("labels", [])}

    # The history keeps every verdict, not just the one that holds an error
    # back, because a fix that did not hold matters to the next run as much as a
    # dismissal that was wrong.
    for name in ("resolved", "no-defect", "unresolved"):
        if name in labels:
            note_verdict(fingerprint, issue.get("number"), name)
            break
    else:
        note_verdict(fingerprint, issue.get("number"), None)

    dismissed = issue.get("state") == "closed" and "no-defect" in labels
    with DISMISSED_LOCK:
        ledger = read_dismissed()
        if dismissed:
            entry = ledger.get(fingerprint, {})
            if entry.get("issue") == issue.get("number"):
                # Already on the ledger under this ticket. The sweep below reads
                # the same closure every few minutes, and rewriting the file for
                # it would bury the verdicts that did change.
                return
            entry.update({"issue": issue.get("number"), "at": time.time()})
            entry.setdefault("recurrences", 0)
            ledger[fingerprint] = entry
            write_dismissed(ledger)
            print("fp:%s ruled out by issue #%s" % (fingerprint, issue.get("number")))
        elif fingerprint in ledger:
            del ledger[fingerprint]
            write_dismissed(ledger)
            print("fp:%s no longer ruled out" % fingerprint)


# A verdict only holds an error back once it reaches the ledger, and the hook
# above is not a reliable way for it to get there: a webhook is a setting on the
# repository, made by hand, that no rebuild of this machine restores. The ticket
# repository ran without one, so no closure ever arrived, and a single fingerprint
# already ruled out filed five tickets in a day and cost five agent turns. The
# service therefore reads the verdicts itself on a timer as well; the hook stays
# as the fast path rather than the only one.
VERDICT_SWEEP_SECONDS = 300
VERDICT_SWEEP_LOOKBACK = 14 * 86400


def sweep_verdicts(since):
    """Records the verdict on every ticket touched since `since`; returns how many it read."""
    repo = CONFIG["errors_repo"]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))
    read = 0
    for page in range(1, 6):
        # Oldest first, so where one fingerprint carries several tickets the
        # newest verdict is the one that ends up on the ledger.
        batch = github_request("GET", "/repos/%s/issues?state=all&since=%s&sort=updated"
                               "&direction=asc&per_page=100&page=%d" % (repo, stamp, page))
        for issue in batch:
            if "pull_request" in issue:
                continue
            record_verdict(issue)
            read += 1
        if len(batch) < 100:
            break
    return read


def keep_verdicts_current():
    """Walks the ticket repository for closures the hook never delivered."""
    since = time.time() - VERDICT_SWEEP_LOOKBACK
    while True:
        started = time.time()
        try:
            read = sweep_verdicts(since)
            # The next window overlaps this one, so a ticket closed while the
            # sweep was running is read again rather than stepped over.
            since = started - VERDICT_SWEEP_SECONDS
            print("verdict sweep read %d ticket(s)" % read)
        except Exception as error:
            print("verdict sweep failed: %s" % error)
        time.sleep(VERDICT_SWEEP_SECONDS)


def held_back(fingerprint):
    """True when this error was already ruled out and has not earned a second look.

    Despite the name this writes: a call against a ruled-out fingerprint counts
    the recurrence into the ledger, and reaching RECUR_THRESHOLD inside
    RECUR_WINDOW drops the entry so the next report files a fresh ticket. Call
    it once per report, on the path that would otherwise dispatch.
    """
    released = False
    with DISMISSED_LOCK:
        ledger = read_dismissed()
        entry = ledger.get(fingerprint)
        if not entry:
            return False
        now = time.time()
        first = entry.get("first_recurrence")
        if not first or now - first > RECUR_WINDOW:
            entry["first_recurrence"] = now
            entry["recurrences"] = 1
        else:
            entry["recurrences"] = entry.get("recurrences", 0) + 1
        if entry["recurrences"] >= RECUR_THRESHOLD:
            # Frequent enough to be worth another look: let it through once and
            # start the count again, so it cannot file on every report after.
            # The hold-back entry goes; what the fingerprint has done does not,
            # and the ticket about to be filed is the one that needs it most.
            del ledger[fingerprint]
            released = True
        else:
            ledger[fingerprint] = entry
        write_dismissed(ledger)

    # Outside the ledger lock, so the two files are never held at once and the
    # order they are taken in cannot matter.
    if released:
        note_release(fingerprint)
        print("fp:%s recurred %d times since it was ruled out; filing again"
              % (fingerprint, RECUR_THRESHOLD))
        return False
    print("fp:%s held back (ruled out in #%s, %d recurrence(s) since)"
          % (fingerprint, entry.get("issue"), entry["recurrences"]))
    return True


AI_FOOTER = re.compile(r"\n*-{3}\n*_?Generated (?:by|with) \[?Claude[^\n]*\n*")


def strip_footer(comment_url, body):
    """Rewrites a just-posted issue comment without the attribution footer.

    A harness can append the footer server-side after the comment is created,
    so the text that lands is not the text anyone wrote. Editing through the
    API does not re-trigger the append, which is what makes the repair stick.
    """
    prefix = "https://api.github.com/"
    if not (comment_url or "").startswith(prefix):
        print("footer strip skipped: %s is not a GitHub API URL" % comment_url)
        return
    try:
        cleaned = AI_FOOTER.sub("", body).rstrip()
        github_request("PATCH", comment_url[len(prefix) - 1:], {"body": cleaned})
        print("stripped attribution footer from %s" % comment_url.rsplit("/", 1)[-1])
    except Exception as error:
        print("footer strip failed: %s" % error)


def refresh_board():
    """Kicks the status, queue, and attention boards so they reflect this event now.

    Fire-and-forget: each board rewrites its own Slack message on a timer
    anyway, so a start that fails costs freshness rather than correctness.
    """
    try:
        subprocess.Popen(["sudo", "systemctl", "start", "--no-block",
                          "status-board.service", "queue-board.service",
                          "attention-board.service"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:
        print("board refresh failed: %s" % error)


def github_request(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request("https://api.github.com" + path, data=body, method=method, headers={
        "Authorization": "Bearer %s" % CONFIG["github_token"],
        "Accept": "application/vnd.github+json",
        "User-Agent": "sunday-error-reporter/1.0",
    })
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read() or "{}")


def dispatch_issue(fingerprint, site, kind, message, stack, page, agent, legacy=None):
    """Files one work ticket per error fingerprint; an open ticket blocks re-dispatch.

    `legacy` is the fingerprint this error would have had before the message was
    normalised, and it is consulted against the dismissed ledger alone. That
    ledger is keyed on the old hash and carries hundreds of entries actively
    holding errors back; changing the hash without reading both would release
    every one of them at once, and the next report for each would file a fresh
    ticket up to the open cap. Nothing writes the old key any more, so the
    entries age out on their own as their tickets leave the verdict sweep's
    window.
    """
    repo = CONFIG["errors_repo"]
    # Counted before anything can turn the report away, so the total covers the
    # reports a hold-back swallowed. Those are most of them, and leaving them
    # out would understate how often visitors meet this by an order of
    # magnitude.
    note_report(fingerprint)
    if held_back(fingerprint):
        return
    if legacy and legacy != fingerprint and held_back(legacy):
        return
    try:
        open_issues = github_request("GET", "/repos/%s/issues?state=open&labels=auto-report&per_page=100" % repo)
        if any("fp:%s" % fingerprint in issue.get("title", "") for issue in open_issues):
            print("fp:%s already has an open issue, not dispatching" % fingerprint)
            return
        if len(open_issues) >= CONFIG.get("max_open_issues", 20):
            print("open issue cap reached, not dispatching fp:%s" % fingerprint)
            return
        site_repo = CONFIG["repo_map"].get(site, "")
        body = "Site: %s\nRepo: %s\nKind: %s\nPage: %s\nBrowser: %s\n\nMessage:\n```\n%s\n```\n" % (
            site, site_repo or "unknown", kind or "unknown", page or "unknown", agent or "unknown", message)
        if stack:
            body += "\nStack:\n```\n%s\n```\n" % stack[:4000]
        # What every earlier ticket on this fingerprint concluded. It goes in
        # the body rather than a comment because the body is what a run reads
        # before it decides which job it is doing.
        previously = history_note(fingerprint)
        if previously:
            body += "\n" + previously
        issue = github_request("POST", "/repos/%s/issues" % repo, {
            "title": "%s: %s (fp:%s)" % (site, cut_words(readable(message), 110), fingerprint),
            "body": body,
            "labels": ["auto-report"],
        })
        number = issue.get("number")
        note_filing(fingerprint, number)
        print("dispatched fp:%s as issue #%s" % (fingerprint, number))
        post_slack({
            "title": "Agent dispatched for %s (ticket #%s)" % (site, number),
            "url": issue.get("html_url"),
            "description": cut_words(readable(message), 600),
            "fields": [{"name": "Ticket", "value": issue.get("html_url", "")}],
            "color": 0x3498DB,
        })
        refresh_board()
    except Exception as error:
        print("dispatch failed for fp:%s: %s" % (fingerprint, error))


def extension_only(stack):
    """Whether every frame in this stack comes from a browser extension.

    Extensions inject scripts into the page, and a rejection thrown inside one
    reaches the page's own `unhandledrejection` handler with nothing of the
    site in its stack. A MetaMask connection failure filed a ticket against a
    site holding no wallet code at all. A stack naming only extension scripts
    describes the visitor's browser rather than the site, so it stops here; a
    stack with one site frame in it is the site's and passes.
    """
    schemes = re.findall(r"([a-z][a-z0-9+.\-]*)://", stack or "", re.I)
    if not schemes:
        return False
    return all(scheme.lower().endswith("extension") for scheme in schemes)


# A client that is not a reader.
#
# Anchored on the punctuation after "bot" rather than on the word itself,
# because CUBOT is a phone and the user agent it sends is a reader's.
NOT_A_READER = re.compile("|".join([
    # Googlebot/2.1, Applebot/0.1, PetalBot; - the shape almost all of them share.
    r"bot[/;)]", r"bot$",
    # The ones that never say "bot".
    r"\b(?:slurp|facebookexternalhit|feedfetcher|mediapartners)\b",
    r"crawler|spider",
    # Driven browsers. A Chrome somebody is looking at never says headless.
    r"headlesschrome|phantomjs|puppeteer|playwright|selenium|lighthouse",
    # Not a browser at all.
    r"\b(?:curl|wget|scrapy|okhttp|libwww-perl|python-requests|go-http-client|node-fetch)\b",
]), re.I)


def not_a_reader(user_agent):
    """Whether this report describes a crawler's fetch rather than a visit.

    The same judgement `extension_only` makes about a stack, made about the
    client instead. A crawler fetches on a budget a browser does not have: it
    caps what it will load, gives up sooner, and often runs the page with less
    than a browser would. So it loses assets a reader would have been served,
    and every one it loses arrives here as a defect in the site.

    It earns its place on what it would have saved. Of the thirty tickets filed
    before this was written, seven came from crawlers and headless clients, and
    all three open on the night it was added were one stylesheet failing on one
    page for Applebot and two headless Chromes. One of those three had already
    taken a release: the monitor read it, changed the priority the recovery asks
    at, and shipped 2026.37.68 for a condition no reader ever reported.

    A missing user agent passes, the way a missing Origin does below, and for
    the same reason: `sendBeacon` and non-browser callers send none, and those
    reports are the ones most worth keeping.
    """
    return bool(NOT_A_READER.search(user_agent or ""))


def origin_allowed(origin):
    """Whether a report from this Origin is accepted.

    A missing Origin passes: `sendBeacon` and non-browser callers send none,
    and those are the reports most worth keeping. Preview deployments match on
    the host suffix Vercel gives them (`preview_suffix` in config.json, or
    agency.vercel_preview_suffix in the profile), so a new preview does not need a
    config change to report. This filters obvious noise; it is not an auth boundary,
    since a client can send any Origin it likes.
    """
    if not origin:
        return True
    if origin in CONFIG["allowed_origins"]:
        return True
    host = origin.split("//", 1)[-1]
    suffix = CONFIG.get("preview_suffix") or mtoa_config.get("agency.vercel_preview_suffix", "")
    return bool(suffix) and host.endswith(suffix)


class Handler(BaseHTTPRequestHandler):
    server_version = "sunday-error-reporter/1.0"

    def log_message(self, format, *args):
        print("%s %s" % (self.client_ip(), format % args))

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() or self.client_address[0]

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def respond(self, code, text, content_type="text/plain"):
        body = text.encode()
        self.send_response(code)
        self.send_cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.respond(204, "")

    def bearer(self):
        """The token the caller presented, if it presented one."""
        header = self.headers.get("Authorization", "")
        return header[len("Bearer "):].strip() if header.startswith("Bearer ") else ""

    def do_GET(self):
        if self.path == "/healthz":
            self.respond(200, "ok")
        elif self.path.split("?")[0] == "/status.json":
            self.respond(200, status_feed.feed(CONFIG), "application/json")
        elif self.path.split("?")[0] == "/server.json":
            # Unlike the feed above it, this one is nobody's business but the
            # studio's: it names the units running here, how full the card is
            # and what the machine is. The Funnel in front of this process is
            # public, so the door is a shared secret the website's own proxy
            # carries, and a deployment that was never given one keeps the
            # route shut rather than opening it to everybody.
            wanted = CONFIG.get("server_feed_token") or ""
            if not wanted or not hmac.compare_digest(self.bearer(), wanted):
                self.respond(404, "not found")
                return
            self.respond(200, server_feed.feed(), "application/json")
        elif CONFIG.get("board_refresh_token") and self.path == "/hook/%s" % CONFIG["board_refresh_token"]:
            refresh_board()
            self.respond(204, "")
        else:
            self.respond(404, "not found")

    def do_POST(self):
        if CONFIG.get("board_refresh_token") and self.path == "/hook/%s" % CONFIG["board_refresh_token"]:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            refresh_board()
            try:
                event = json.loads(raw)
            except ValueError:
                event = {}
            comment = event.get("comment") or {}
            if AI_FOOTER.search(comment.get("body") or ""):
                threading.Thread(target=strip_footer, args=(comment["url"], comment["body"]), daemon=True).start()
            issue = event.get("issue")
            if issue:
                threading.Thread(target=record_verdict, args=(issue,), daemon=True).start()
            self.respond(204, "")
            return
        if self.path != "/report":
            self.respond(404, "not found")
            return
        if not origin_allowed(self.headers.get("Origin")):
            self.respond(403, "origin not allowed")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY_BYTES:
            self.respond(413, "bad length")
            return
        try:
            report = json.loads(self.rfile.read(length))
        except ValueError:
            self.respond(400, "bad json")
            return

        site = clip(report.get("site"), 100) or "unknown site"
        message = clip(report.get("message"), 1500) or "(no message)"
        now = time.time()
        ip = self.client_ip()

        if extension_only(report.get("stack")):
            print("dropped extension-only report from %s: %s" % (site, message[:120]))
            self.respond(202, "ignored")
            return

        if not_a_reader(report.get("userAgent")):
            print("dropped crawler report from %s: %s" % (site, message[:120]))
            self.respond(202, "ignored")
            return

        with LOCK:
            prune(now)
            bucket = PER_IP.setdefault(ip, deque())
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= CONFIG["per_ip_per_minute"]:
                self.respond(429, "rate limited")
                return
            bucket.append(now)

            # Slack dedupe wants the message exactly as it arrived: three
            # different broken images in one minute are three lines worth
            # reading. The ticket fingerprint wants the opposite, and it is
            # computed from `stable()` at the dispatch below.
            key = hashlib.sha1(("%s|%s" % (site, message[:300])).encode()).hexdigest()
            entry = DEDUPE.get(key)
            if entry and now - entry["last_sent"] < CONFIG["dedupe_cooldown_seconds"]:
                entry["suppressed"] += 1
                self.respond(202, "deduplicated")
                return
            repeats = entry["suppressed"] if entry else 0
            DEDUPE[key] = {"last_sent": now, "suppressed": 0}

            while GLOBAL_SENDS and now - GLOBAL_SENDS[0] > 300:
                GLOBAL_SENDS.popleft()
            if len(GLOBAL_SENDS) >= CONFIG["global_per_5min"]:
                print("global cap hit, dropping report from %s" % site)
                self.respond(202, "throttled")
                return
            GLOBAL_SENDS.append(now)

        fields = []
        if report.get("kind"):
            fields.append({"name": "Type", "value": clip(report["kind"], 40)})
        if report.get("url"):
            fields.append({"name": "Page", "value": clip(report["url"], 500)})
        if report.get("stack"):
            fields.append({"name": "Stack", "value": "```%s```" % clip(report["stack"], 900)})
        if report.get("userAgent"):
            fields.append({"name": "Browser", "value": clip(report["userAgent"], 200)})
        if repeats:
            fields.append({"name": "Repeats", "value": "%d duplicate reports suppressed during the last cooldown" % repeats})
        embed = {
            "title": "Error on %s" % site,
            "description": cut_words(message, 1500),
            "color": COLOR_ERROR,
            "fields": fields,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        }
        try:
            post_slack(embed)
        except Exception as error:
            print("slack post failed: %s" % error)
            self.respond(502, "forward failed")
            return
        if CONFIG.get("github_token"):
            # The ticket fingerprint is its own hash, over the message with the
            # rebuilt parts taken out, so one defect keeps one fingerprint for
            # its whole life rather than for one deploy.
            fingerprint = hashlib.sha1(
                ("%s|%s" % (site, stable(message)[:300])).encode()).hexdigest()[:12]
            threading.Thread(target=dispatch_issue, args=(
                fingerprint, site, report.get("kind"), message,
                clip(report.get("stack"), 4000), report.get("url"), report.get("userAgent"),
                key[:12],
            ), daemon=True).start()
        else:
            refresh_board()
        self.respond(200, "reported")


def main():
    if CONFIG.get("github_token"):
        threading.Thread(target=keep_verdicts_current, daemon=True).start()
    # Loopback unless config.json says otherwise: on the Pi a proxy in front of it
    # is the only way in, and in a container the port is published instead.
    listen = CONFIG.get("listen_host", "127.0.0.1")
    server = ThreadingHTTPServer((listen, CONFIG["listen_port"]), Handler)
    print("listening on %s:%d" % (listen, CONFIG["listen_port"]))
    server.serve_forever()


if __name__ == "__main__":
    main()
