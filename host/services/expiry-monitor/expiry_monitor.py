#!/usr/bin/env python3
"""Reads how long each domain has left, on its certificate and at its registry.

The uptime monitor answers whether a site is serving right now. Both of the
things checked here take a site down completely and give weeks of warning
first, and neither produces a single failed request until the day it lands. A
lapsed certificate makes every browser refuse the page; a lapsed registration
takes the name into a redemption period that costs a fee to reverse and can end
with somebody else holding it.

So the ladder starts far enough out to renew: 45 days, then 30, 14, 7, 3, 1,
then daily once it is expired. State records the last rung reported, so a
domain crosses each threshold once rather than alerting every run for six
weeks.

Runs as a oneshot under a systemd timer. State lives beside the script.
"""

import json
import os
import re
import socket
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
USER_AGENT = "sunday-expiry-monitor/1.0"

# The Slack helper is deployed beside the services rather than inside one.
sys.path.insert(0, os.path.join(BASE, os.pardir, "lib"))
import slack  # noqa: E402

COLOR_URGENT = 0xE74C3C
COLOR_SOON = 0xE67E22
COLOR_INFO = 0x3498DB

# A certificate and a registration fail the same way and are renewed by
# completely different things. Certificates renew themselves 30 days out, so a
# rung above that reports the platform working; a registration renews when a
# card somebody owns is charged, and a declined card is silent, so that ladder
# starts far enough out to fix the payment.
LADDER = {
    "certificate": (14, 7, 3, 1),
    "registration": (60, 45, 30, 14, 7, 3, 1),
}

# Registries that sell under a second-level label, where the registrable name
# is three labels rather than two.
MULTI_LABEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "co.nz", "com.au",
               "co.za", "com.br", "co.jp", "co.in"}

RDAP = "https://rdap.org/domain/%s"

# RDAP is a redirector in front of whichever registry holds the name, and it
# rate-limits a burst: nineteen at once came back 429 across the board. A daily
# run has all the time it needs, so registrations go one at a time.
RDAP_GAP_SECONDS = 3
RDAP_RETRY_SECONDS = 20


def registrable(host):
    """The name a registry actually holds, out of a hostname.

    A certificate is issued to www.example.com and the registration belongs to
    example.com, so the two checks read different names off the same site.
    """
    host = host.strip().lower().rstrip(".")
    labels = host.split(".")
    if len(labels) < 2:
        return host
    if len(labels) > 2 and ".".join(labels[-2:]) in MULTI_LABEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def hostname(url):
    match = re.match(r"^[a-z]+://([^/:?#]+)", url.strip(), re.I)
    return match.group(1) if match else url.strip().split("/")[0]


def days_until(when):
    return (when - datetime.now(timezone.utc)).total_seconds() / 86400.0


def certificate_expiry(host, timeout):
    """When the certificate served for `host` stops being valid, or an error.

    A handshake is the only way to read what is actually being served: a
    certificate renewed at the registrar and never deployed looks fine
    everywhere except on the socket.
    """
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
    except ssl.SSLCertVerificationError as error:
        return None, "certificate does not verify: %s" % error.verify_message
    except (socket.timeout, TimeoutError):
        return None, "timed out"
    except (OSError, ssl.SSLError) as error:
        return None, str(error) or type(error).__name__
    raw_date = (cert or {}).get("notAfter")
    if not raw_date:
        return None, "no notAfter on the certificate"
    try:
        when = datetime.strptime(raw_date, "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return None, "unreadable notAfter: %s" % raw_date
    return when.replace(tzinfo=timezone.utc), None


def registry_expiry(domain, timeout, retry=True):
    """When the registration lapses, from RDAP, or an error.

    RDAP is HTTPS and returns JSON, so it needs no whois binary on the host and
    no parsing of a registrar's free text.
    """
    request = Request(RDAP % domain, headers={"User-Agent": USER_AGENT,
                                              "Accept": "application/rdap+json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as error:
        if error.code == 429 and retry:
            time.sleep(RDAP_RETRY_SECONDS)
            return registry_expiry(domain, timeout, retry=False)
        return None, "RDAP HTTP %d" % error.code
    except (URLError, socket.timeout, TimeoutError) as error:
        return None, "RDAP unreachable: %s" % getattr(error, "reason", error)
    except (ValueError, OSError) as error:
        return None, "RDAP unreadable: %s" % error
    for event in body.get("events") or []:
        if event.get("eventAction") == "expiration":
            raw_date = (event.get("eventDate") or "").replace("Z", "+00:00")
            try:
                when = datetime.fromisoformat(raw_date)
            except ValueError:
                return None, "unreadable expiration: %s" % event.get("eventDate")
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return when, None
    return None, "RDAP carries no expiration event"


def rung(days, kind):
    """The ladder rung a remaining-days figure has reached, or None.

    Expired reports on every run, since by then the site is already refusing
    connections and the count of days is the only thing still moving.
    """
    if days <= 0:
        return 0
    for step in sorted(LADDER.get(kind, LADDER["registration"])):
        if days <= step:
            return step
    return None


def post_alerts(config, embeds):
    token, channel = slack.settings(config)
    if not (token and channel):
        print("slack is not configured; nothing posted", file=sys.stderr)
        return False
    ok = True
    for start in range(0, len(embeds), 10):
        batch = embeds[start:start + 10]
        try:
            slack.post(token, channel, batch, username="Expiry Monitor",
                       fallback=batch[0].get("title"))
        except Exception as error:
            print("slack post failed: %s" % error, file=sys.stderr)
            ok = False
    return ok


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


def read_certificate(site, timeout):
    """The certificate half for one site, which is a handshake with that host."""
    host = hostname(site["url"])
    when, error = certificate_expiry(host, timeout)
    return ("certificate", host, when, error)


def read_registrations(sites, timeout):
    """The registration half for every site, paced so the redirector answers.

    One name per registrable domain: two sites on the same domain share one
    registration, and asking twice spends a request on an answer already held.
    """
    out, seen = [], {}
    for site in sites:
        domain = registrable(hostname(site["url"]))
        if domain in seen:
            out.append(("registration", domain) + seen[domain])
            continue
        if seen:
            time.sleep(RDAP_GAP_SECONDS)
        seen[domain] = registry_expiry(domain, timeout * 2)
        out.append(("registration", domain) + seen[domain])
    return out


def embed_for(site, kind, name, when, days):
    at = int(when.timestamp())
    if days <= 0:
        return {
            "title": "%s: %s has expired" % (site["name"], kind),
            "description": "%s\nExpired <t:%d:R> (<t:%d:f>)" % (name, at, at),
            "color": COLOR_URGENT,
        }
    return {
        "title": "%s: %s expires in %d days" % (site["name"], kind, int(days)),
        "description": "%s\nExpires <t:%d:f> (<t:%d:R>)" % (name, at, at),
        "color": COLOR_URGENT if days <= 7 else COLOR_SOON,
    }


def main():
    with open(CONFIG_PATH) as handle:
        config = json.load(handle)
    sites = config["sites"]
    timeout = config.get("timeout_seconds", 20)

    if "--test" in sys.argv:
        sent = post_alerts(config, [{
            "title": "Expiry monitor online",
            "description": "Reading certificates and registrations for %d sites. "
                           "Certificates alert at %s days out, registrations at "
                           "%s, and both daily once expired."
                           % (len(sites),
                              ", ".join(str(s) for s in sorted(LADDER["certificate"], reverse=True)),
                              ", ".join(str(s) for s in sorted(LADDER["registration"], reverse=True))),
            "color": COLOR_INFO,
        }])
        sys.exit(0 if sent else 1)

    state = load_state()
    with ThreadPoolExecutor(max_workers=8) as pool:
        certificates = list(pool.map(lambda site: read_certificate(site, timeout),
                                     sites))
    registrations = read_registrations(sites, timeout)
    readings = [[cert, registration]
                for cert, registration in zip(certificates, registrations)]

    embeds = []
    pending = []
    failures = []
    for site, rows in zip(sites, readings):
        for kind, name, when, error in rows:
            key = "%s|%s" % (kind, name)
            entry = state.setdefault(key, {"reported": None})
            if error:
                # An unreadable expiry is reported once per run to stderr and
                # never to the channel: RDAP rate-limits and a registrar's outage
                # is not a site's problem.
                failures.append("%s %s: %s" % (site["name"], kind, error))
                continue
            days = days_until(when)
            entry["expires_at"] = when.isoformat()
            entry["days"] = round(days, 1)
            step = rung(days, kind)
            if step is None:
                entry["reported"] = None
                continue
            already = entry.get("reported")
            if already is not None and step >= already and days > 0:
                continue
            embeds.append(embed_for(site, kind, name, when, days))
            pending.append((entry, step))

    for line in failures:
        print(line, file=sys.stderr)

    # The rung is recorded only once Slack has taken the message, so a failed
    # post is retried on the next run rather than swallowed.
    if embeds and post_alerts(config, embeds):
        for entry, step in pending:
            entry["reported"] = step

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
