#!/usr/bin/env python3
"""Whether the Pi is still answering, judged by the feed rather than by silence.

The error reporter, the uptime monitor and all three boards run on one machine.
When that machine stops, every one of them stops with it: no alert fires, no
board updates, and the ticket queue stops filling. A run that reads an empty
queue and finishes clean is reading a dead collector, and the two look identical
from the outside.

The public feed carries the timestamp of its last rebuild, so the difference is
one read: a feed that answers and is recent means the pipeline is alive, and a
feed that is stale by more than a board's own interval means it is not,
whatever the queue says.

The feed's host is a Tailscale MagicDNS name, which resolves on the tailnet and
nowhere else, so a machine off the tailnet cannot read it directly however alive
the Pi is. A read that fails at the transport is therefore taken again on the Pi
itself over ssh, and only a feed neither route reaches counts as a machine that
has stopped. A server answering with an HTTP error has answered, so that stays a
failure with no second route to try, and the age of the feed decides the verdict
the same way whichever route carried the read.

  heartbeat.py check [--url U] [--max-age-minutes N]

Exits 0 when the feed is fresh, 1 when it is stale or unreachable.
STATUS_FEED overrides the URL.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from pathlib import Path
from urllib.request import Request, urlopen


def _profile():
    """The owner's profile reader (mtoa_config.py): beside the services in the
    checkout, in ~/lib where the host runs them, or in the installed setup."""
    import importlib.util
    here = Path(__file__).resolve().parent
    for path in (here.parent / "services" / "lib" / "mtoa_config.py",
                 here.parent / "lib" / "mtoa_config.py",
                 Path.home() / "lib" / "mtoa_config.py",
                 Path.home() / ".claude" / "tools" / "mtoa_config.py"):
        if path.is_file():
            spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def setting(key, default=None):
    """One value from the owner's profile, or the default where none is set."""
    module = _profile()
    return module.get(key, default) if module else default


# The host's public feed (host.url in the profile).
FEED = (setting("host.url") or "").rstrip("/") + "/status.json" if setting("host.url") else ""

# A read taken on the Pi crosses the network twice, so it is slower than the
# direct one by the width of a round trip rather than of a fetch.
HOP_TIMEOUT = 120

# The Pi over ssh, for the read that has to happen on the tailnet itself.
PI_HOST = os.environ.get("PI_HOST") or setting("host.ssh", "")

# The boards rebuild every two minutes. Fifteen leaves room for a slow rebuild
# and a retried post without leaving room for an outage.
MAX_AGE_MINUTES = 15


def read_feed(url, timeout=20):
    request = Request(url, headers={"User-Agent": "status-heartbeat/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def read_feed_via_pi(url, timeout=HOP_TIMEOUT):
    """The same feed, fetched on the Pi and handed back over ssh.

    The feed's host is a MagicDNS name that resolves on the tailnet and nowhere
    else, so a machine off the tailnet cannot read it directly however alive the
    Pi is. Running the fetch on the Pi settles that: the machine whose own name
    the feed is served under is the one asking. PI_HOST names the route.
    """
    command = "curl -fsS --max-time 20 %s" % shlex.quote(url)
    done = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", PI_HOST, command],
        capture_output=True, text=True, timeout=timeout,
    )
    if done.returncode != 0:
        raise OSError(done.stderr.strip() or "the Pi did not answer")
    return json.loads(done.stdout)


def age_minutes(stamp):
    when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 60.0


def check(url, limit):
    route = "directly"
    try:
        feed = read_feed(url)
    except HTTPError as error:
        # The server answered, so the machine is up and the answer is its own.
        return 1, "the feed answered HTTP %d" % error.code
    except ValueError as error:
        return 1, "the feed is unreadable: %s" % error
    except (URLError, OSError) as error:
        direct = getattr(error, "reason", error)
        try:
            feed = read_feed_via_pi(url)
        except (URLError, OSError, ValueError, subprocess.SubprocessError) as hop:
            return 1, ("the feed is unreachable: %s directly, and %s through "
                       "the Pi. Both routes failed, so this is the machine "
                       "rather than the route." % (direct, hop))
        route = "through the Pi"
    stamp = feed.get("updated_at")
    if not stamp:
        return 1, "the feed carries no updated_at, so its age cannot be read"
    try:
        age = age_minutes(stamp)
    except ValueError:
        return 1, "the feed's updated_at is unreadable: %r" % stamp
    sites = len(feed.get("sites") or [])
    if age > limit:
        return 1, ("the feed, read %s, was last rebuilt %.0f minutes ago, past "
                   "the %d minute mark. The collector, the monitor and the "
                   "boards all run on that machine, so an empty ticket queue "
                   "right now says nothing about the sites." % (route, age, limit))
    return 0, ("the feed rebuilt %.0f minute(s) ago over %d site(s), read %s"
               % (age, sites, route))


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("verb", nargs="?", default="")
    parser.add_argument("--url", default=os.environ.get("STATUS_FEED") or FEED)
    parser.add_argument("--max-age-minutes", type=int, default=MAX_AGE_MINUTES)
    args = parser.parse_args()
    if args.verb != "check":
        print(__doc__.strip(), file=sys.stderr)
        return 2
    code, message = check(args.url, args.max_age_minutes)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
