"""What the Sunday Server can say about itself.

Everything visitor-facing that is not a site runs on this one machine, and its
failure is silent: the boards stop posting, the collector stops filing, and an
empty ticket queue reads exactly like a clean night. `status.json` beside this
answers for the sites. Nothing answered for the machine underneath them, so the
only way to know it was alive was to open a terminal and ask it.

This is that question as a feed. Three readings, and they are deliberately
different kinds of fact:

  the machine   how long it has been up, what it is carrying, how warm it is,
                and how much room is left on the card
  the routines  every unit, timer and cron line that is meant to be running,
                whether it is, and when it last did something
  the queue     nothing. The queue is the sites' business and lives in the
                other feed, because a board reporting on itself is one figure
                that cannot fail independently of the thing reporting it.

The routines list is the point of the whole file. A machine at four percent
load with a dead social routine looks perfectly healthy from every angle but
the one that matters, so a routine is asked directly rather than inferred from
the machine being up.

Nothing here writes anything, runs as root, or shells out to anything that can
change state. Every reading is taken with a timeout and a reading that could
not be taken is reported as unread rather than dropped: a feed that omits the
section it could not fill is a feed that lies by being tidy.
"""

import fcntl
import json
import os
import re
import subprocess
import threading
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
import mtoa_config  # noqa: E402  the owner's names, from the profile

HOME = Path.home()
ROUTINE_LOGS = HOME / "routine-logs"

# A reading is a few file reads and a handful of `systemctl show` calls, which
# is fast but not free, and the console polls this. Half a minute is shorter
# than anything here moves and long enough that a page left open costs nothing.
CACHE_SECONDS = 30

# Every subprocess this takes is a local read that answers immediately or is
# wedged. Five seconds is generous for the first and short enough that a wedged
# one cannot hold the request open.
COMMAND_TIMEOUT = 5

# What runs on this machine, in the order a person would ask about it.
#
# Held here rather than discovered, and that is the whole design: a routine
# that has been uninstalled disappears from a discovered list and reads as
# nothing being wrong, where the thing worth knowing is that something which
# should be running is not. So the list is a declaration of what is meant to be
# here, and a routine missing from the machine is reported as missing.
#
# `label` and `does` travel with the facts rather than living on the website,
# because this is where the routines are. A routine added here appears on the
# console's Server tab the moment this feed carries it, with no second edit in
# another repository to remember.
#
# These are the routines this repository ships. A host that runs more of its own
# declares the whole list as host.routines_feed in the profile, which replaces this
# one, so the order a person reads them in stays the profile's to set.
ROUTINES = [
    {
        "id": "error-reporter",
        "label": "Error Reporter",
        "kind": "service",
        "cadence": "Continuous",
        "does": "Receives browser errors, dedupes them, posts to Slack, files tickets, and serves the public feed.",
    },
    {
        "id": "uptime-monitor",
        "label": "Uptime Monitor",
        "kind": "timer",
        "cadence": "Every minute",
        "does": "Checks every site from outside and alerts on a site going down and coming back.",
    },
    {
        "id": "status-board",
        "label": "Status Board",
        "kind": "timer",
        "cadence": "Every 2 minutes",
        "does": "Posts one message carrying every site with a red, yellow or green dot.",
    },
    {
        "id": "queue-board",
        "label": "Queue Board",
        "kind": "timer",
        "cadence": "Every 2 minutes",
        "does": "Posts the work queue and what was fixed today.",
    },
    {
        "id": "attention-board",
        "label": "Attention Board",
        "kind": "timer",
        "cadence": "Every 2 minutes",
        "does": "The only channel that notifies. Stays silent unless something needs a person.",
    },
    {
        "id": "expiry-monitor",
        "label": "Expiry Monitor",
        "kind": "timer",
        "cadence": "Daily",
        "does": "Watches certificates from 14 days out and domain registrations from 60.",
    },
    {
        "id": "error-monitor",
        "label": "Error Monitor",
        "kind": "routine",
        "cadence": "Every 10 minutes",
        "does": "Claims and fixes the tickets the reporter and the maintenance sweep file.",
        # The tick reads the queue every ten minutes and starts a turn only
        # when a ticket is waiting, so the turn's own record ages for as long
        # as the queue stays empty. What the tick saw is the reading that says
        # the routine is alive; the record says how the last turn ended.
        "tick": "error-monitor.tick.json",
        "tick_behind_after_minutes": 30,
        "lock": "error-monitor.lock",
    },
    {
        "id": "config-sync",
        "label": "Setup Sync",
        "kind": "stamp",
        "cadence": "Hourly",
        "does": "Reconciles this machine's setup against the mirror, so it never runs instructions more than an hour old.",
        # The runner stamps a file on success rather than writing a status
        # record, so it is read from the stamp. A routine read the wrong way
        # reports as broken forever, which is worse than not reporting it.
        "stamp": "config-sync-last-success",
        "behind_after_minutes": 180,
    },
    {
        "id": "routine-repair",
        "label": "Routine Repair",
        "kind": "sweep",
        "cadence": "Every 5 minutes",
        "does": "Answers for a routine that said it needs a person, and mails what broke and whether it is fixed.",
        # A sweep that finds nothing to repair leaves nothing behind, so there
        # is no last run to read and none is expected. Its cron line existing
        # is the whole of what can be checked.
        "cron": "routine-repair-sweep.sh",
    },
]

# The three states a row can be in, worst first. `overall` is the worst of them,
# so one dead routine on a quiet machine still reads as a machine with a problem.
RANK = {"down": 3, "degraded": 2, "operational": 1}

# Room left on the card, and how warm the board is. Both are gradual failures
# that give plenty of warning and no alert, which is exactly the kind this feed
# is for. The board throttles at 80C, and the numbers below leave room to notice
# it coming before it arrives.
DISK_WARN_PERCENT = 80
DISK_CRITICAL_PERCENT = 92
TEMP_WARN_C = 70
TEMP_CRITICAL_C = 80

_lock = threading.Lock()
_cache = {"body": None, "built": 0.0}


def _run(args):
    """One local command's output, or an empty string where it would not run."""
    try:
        done = subprocess.run(
            args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _stamp(when):
    """A moment, written the way every other feed on this machine writes one."""
    if when is None:
        return None
    return datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def host():
    """What this machine is, and how long it has been up.

    The boot moment rather than a duration, because a duration is only true for
    the second it was measured in and a reader looking at a page for a minute
    would watch it go stale. The website counts forward from the moment.
    """
    model = _read("/proc/device-tree/model").replace("\x00", "").strip()
    release = {}
    for line in _read("/etc/os-release").splitlines():
        name, _, value = line.partition("=")
        release[name] = value.strip('"')
    try:
        booted = time.time() - float(_read("/proc/uptime", "0").split()[0])
    except (ValueError, IndexError):
        booted = None
    return {
        "name": "Sunday Server",
        "model": model or None,
        "os": release.get("PRETTY_NAME") or None,
        "kernel": os.uname().release,
        "booted_at": _stamp(booted),
    }


def load():
    """What the machine is carrying, against how much it can carry.

    The per-core figure is what makes the raw number readable: 0.08 says nothing
    on its own, and eight percent of one core says the machine is idle.
    """
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        return {"error": "the load average could not be read"}
    cores = os.cpu_count() or 1
    return {
        "one": round(one, 2),
        "five": round(five, 2),
        "fifteen": round(fifteen, 2),
        "cores": cores,
        "percent": round(one / cores * 100),
    }


def memory():
    """Memory and swap, in megabytes.

    Available rather than free, because Linux spends every spare page on cache
    and a machine reporting 2GB free out of 8GB is not a machine under pressure.
    """
    fields = {}
    for line in _read("/proc/meminfo").splitlines():
        name, _, value = line.partition(":")
        try:
            fields[name] = int(value.split()[0]) // 1024
        except (ValueError, IndexError):
            continue
    if "MemTotal" not in fields:
        return {"error": "memory could not be read"}
    total = fields["MemTotal"]
    available = fields.get("MemAvailable", fields.get("MemFree", 0))
    swap_total = fields.get("SwapTotal", 0)
    return {
        "total_mb": total,
        "used_mb": total - available,
        "available_mb": available,
        "percent": round((total - available) / total * 100) if total else None,
        "swap_total_mb": swap_total,
        "swap_used_mb": swap_total - fields.get("SwapFree", swap_total),
    }


def disk():
    """Room left where everything is written.

    The root filesystem alone. Every service on this machine writes under
    the home directory, which is on it, so a second row would be the same figure
    twice.
    """
    try:
        usage = os.statvfs("/")
    except OSError:
        return {"error": "the disk could not be read"}
    block = usage.f_frsize
    total = usage.f_blocks * block
    free = usage.f_bavail * block
    used = total - free
    gb = 1024**3
    return {
        "total_gb": round(total / gb, 1),
        "used_gb": round(used / gb, 1),
        "free_gb": round(free / gb, 1),
        "percent": round(used / total * 100) if total else None,
    }


def temperature():
    """How warm the board is, in Celsius.

    Read from the thermal zone rather than from `vcgencmd`, which lives in a
    package a rebuilt machine may not have and needs a group this service is
    not in. The zone is a plain file and is always there.
    """
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(raw) / 1000, 1)
    except ValueError:
        return None


# What each bit of the firmware's throttle word means, and whether it is a
# thing happening now or a thing that happened at some point since boot. A
# machine that browned out once during a storm and has been fine since should
# not read the same as one browning out while you look at it.
THROTTLE_BITS = [
    (0, "now", "the power supply is not keeping up"),
    (1, "now", "the board is hot enough to be capped"),
    (2, "now", "the board is hot enough to be slowed"),
    (3, "now", "the board is too hot and is being slowed"),
    (16, "since_boot", "the power supply has not kept up at some point"),
    (17, "since_boot", "the board has been capped at some point"),
    (18, "since_boot", "the board has been slowed at some point"),
    (19, "since_boot", "the board has been too hot at some point"),
]


def throttling():
    """Whether the board is being held back, and whether it ever has been.

    Undervoltage is the failure mode a board like this one has that a rack
    server does not, and it presents as everything being mysteriously slow
    rather than as anything breaking. It is worth a line of its own for that
    reason.

    `vcgencmd` is the only thing that reports it, and a machine without it is
    reported as unread rather than as healthy.
    """
    raw = _run(["vcgencmd", "get_throttled"])
    match = re.search(r"0x([0-9a-fA-F]+)", raw)
    if not match:
        return {"read": False}
    word = int(match.group(1), 16)
    now = [text for bit, when, text in THROTTLE_BITS if when == "now" and word >> bit & 1]
    ever = [text for bit, when, text in THROTTLE_BITS if when == "since_boot" and word >> bit & 1]
    return {"read": True, "now": now, "since_boot": ever}


def _unit(name, prop):
    return _run(["systemctl", "show", name, "--property", prop, "--value"])


def _unit_row(spec):
    """A systemd unit's own answer about itself.

    A timer is asked about the timer rather than the service it fires, because
    a timer that has stopped fires nothing and a service that ran and exited is
    inactive by design. When the timer last fired and when it fires next are
    both the timer's, and both are what a reader is actually asking.
    """
    if spec["kind"] == "service":
        name = spec["id"] + ".service"
        active = _unit(name, "ActiveState")
        if not active:
            return {"state": "down", "detail": "the unit is not installed"}
        if active != "active":
            return {"state": "down", "detail": "the service is %s" % active}
        since = _unit(name, "ActiveEnterTimestamp")
        return {
            "state": "operational",
            "detail": _unit(name, "SubState") or "active",
            "since": _parse_systemd_time(since),
        }

    name = spec["id"] + ".timer"
    active = _unit(name, "ActiveState")
    if not active:
        return {"state": "down", "detail": "the timer is not installed"}
    if active != "active":
        return {"state": "down", "detail": "the timer is %s" % active}
    return {
        "state": "operational",
        "detail": "waiting",
        "last_run": _parse_systemd_time(_unit(name, "LastTriggerUSec")),
        "next_run": _next_elapse(name),
    }


# How systemd writes a span: "1w 3d 19h 5min 25.468913s". The three long units
# are matched before the single letters they start with, or "min" reads as a
# minute followed by "in" and "ms" reads as a millisecond followed by a second.
SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*(us|ms|min|[wdhs])")
SPAN_SECONDS = {"us": 1e-6, "ms": 1e-3, "s": 1, "min": 60, "h": 3600, "d": 86400, "w": 604800}


def _next_elapse(name):
    """When a timer fires next, whichever clock it is set against.

    A calendar timer - anything with an `OnCalendar=` - carries a wall-clock
    answer and is read straight off. A timer set on an interval since boot
    carries a span rather than a moment, because that is genuinely what it
    knows, and its wall-clock answer is that span counted from the moment the
    machine started. Reading only the first is what left every one of the
    minute-and-two-minute routines saying they would never fire again.
    """
    wall = _parse_systemd_time(_unit(name, "NextElapseUSecRealtime"))
    if wall:
        return wall
    span = _unit(name, "NextElapseUSecMonotonic")
    if not span or span.startswith("n/a"):
        return None
    seconds = sum(float(size) * SPAN_SECONDS[unit] for size, unit in SPAN.findall(span))
    if not seconds:
        return None
    try:
        booted = time.time() - float(_read("/proc/uptime", "0").split()[0])
    except (ValueError, IndexError):
        return None
    return _stamp(booted + seconds)


def _parse_systemd_time(text):
    """A systemd timestamp as an ISO moment, or None where there is not one.

    systemd prints these in the machine's own zone with the zone abbreviated,
    which `strptime` cannot read back. The abbreviation is dropped and the
    result is read as local time, which is what it is.
    """
    if not text or text.startswith("n/a"):
        return None
    cleaned = re.sub(r"\s+[A-Z]{2,5}$", "", text.strip())
    cleaned = re.sub(r"^[A-Za-z]{3}\s+", "", cleaned)
    try:
        when = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return _stamp(when.astimezone().timestamp())


def _routine_row(spec):
    """A cron routine's answer, which is the record its runner left behind.

    A routine that has never run and one that ran badly are different facts,
    and a routine installed this morning has not failed by not having fired
    yet, so the two are reported apart.
    """
    record_path = ROUTINE_LOGS / ("%s.status.json" % spec["id"])
    try:
        record = json.loads(record_path.read_text())
        ran = float(record["at"])
        exit_code = record.get("exit")
    except (OSError, ValueError, KeyError, TypeError):
        if record_path.exists():
            return {"state": "degraded", "detail": "its last record could not be read"}
        return {"state": "degraded", "detail": "it has not run since it was installed"}

    behind = (time.time() - ran) / 60 > spec.get("behind_after_minutes", 60)
    if exit_code == 0 and not behind:
        return {"state": "operational", "detail": "finished cleanly", "last_run": _stamp(ran)}
    if exit_code == 0:
        return {"state": "degraded", "detail": "it has not run when it should have", "last_run": _stamp(ran)}
    # 124 is what `timeout` exits with, which for the turns on this machine is
    # a routine that ran out its allowance rather than one that crashed.
    reason = "it ran out of time" if exit_code == 124 else "it exited %s" % exit_code
    return {"state": "degraded", "detail": reason, "last_run": _stamp(ran)}


def _held(path):
    """Whether something holds this lock right now, which for a turn is the run itself."""
    try:
        with open(path, "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def _ticked_row(spec):
    """A routine whose tick reads a queue and starts a turn only when something waits.

    Its runner leaves a record only when a turn finishes, so on a quiet queue
    the newest record ages with nothing wrong: read the way a daily routine is
    read, an empty queue said "Error Monitor is behind" on the console for the
    whole of a clean night. The tick writes what it saw each time it looks, so
    the tick is what proves the routine alive, the lock is what says a turn is
    running, and the record is read for how the last turn ended and for whether
    a ticket the tick saw waiting has gone unstarted.
    """
    tick_path = ROUTINE_LOGS / spec["tick"]
    try:
        tick = json.loads(tick_path.read_text())
        looked = float(tick["at"])
        waiting = int(tick.get("open") or 0) + int(tick.get("stale") or 0)
    except (OSError, ValueError, KeyError, TypeError):
        if tick_path.exists():
            return {"state": "degraded", "detail": "its last look at the queue could not be read"}
        return {"state": "degraded", "detail": "it has not looked at the queue since it was installed"}

    ran = exit_code = None
    try:
        record = json.loads((ROUTINE_LOGS / ("%s.status.json" % spec["id"])).read_text())
        ran = float(record["at"])
        exit_code = record.get("exit")
    except (OSError, ValueError, KeyError, TypeError):
        pass
    last_run = _stamp(ran) if ran else None

    if (time.time() - looked) / 60 > spec.get("tick_behind_after_minutes", 30):
        return {"state": "degraded", "detail": "it has not looked at the queue when it should have", "last_run": last_run}
    if _held(ROUTINE_LOGS / spec["lock"]):
        return {"state": "operational", "detail": "working a ticket", "last_run": last_run}
    if exit_code not in (None, 0):
        reason = "it ran out of time" if exit_code == 124 else "it exited %s" % exit_code
        return {"state": "degraded", "detail": reason, "last_run": last_run}
    # The tick saw work, execs the run in the same breath, and nothing holds
    # the lock: either the turn already finished, which the record is newer
    # than the look to say, or it never started. A couple of minutes of grace
    # covers the reading that lands between the stamp and the lock.
    if waiting and (ran is None or ran < looked) and time.time() - looked > 120:
        return {"state": "degraded", "detail": "a ticket is waiting and no turn has started on it", "last_run": last_run}
    detail = "nothing waiting" if ran is None else "finished cleanly, nothing waiting"
    return {"state": "operational", "detail": detail, "last_run": last_run}


def _stamp_row(spec):
    """A routine that marks its success by touching a file."""
    marker = ROUTINE_LOGS / spec["stamp"]
    try:
        ran = marker.stat().st_mtime
    except OSError:
        return {"state": "degraded", "detail": "no run has ever succeeded"}
    behind = (time.time() - ran) / 60 > spec.get("behind_after_minutes", 180)
    return {
        "state": "degraded" if behind else "operational",
        "detail": "it has not succeeded when it should have" if behind else "last run succeeded",
        "last_run": _stamp(ran),
    }


def _sweep_row(spec):
    """A sweep that leaves nothing behind when it finds nothing to do.

    There is no last run to read, so the only thing that can be checked is
    whether the line that fires it is still in the table. That is a narrower
    answer than the other kinds give and is reported as the narrower answer it
    is rather than dressed up as a clean run.
    """
    table = _run(["crontab", "-l"])
    if not table:
        return {"state": "degraded", "detail": "the schedule could not be read"}
    if spec["cron"] not in table:
        return {"state": "down", "detail": "it is no longer scheduled"}
    return {"state": "operational", "detail": "scheduled"}


def routines():
    """Every routine that is meant to be running, and whether it is."""
    rows = []
    own = mtoa_config.get("host.routines_feed")
    for spec in (own if isinstance(own, list) and own else ROUTINES):
        if spec["kind"] in ("service", "timer"):
            reading = _unit_row(spec)
        elif spec["kind"] == "stamp":
            reading = _stamp_row(spec)
        elif spec["kind"] == "sweep":
            reading = _sweep_row(spec)
        elif "tick" in spec:
            reading = _ticked_row(spec)
        else:
            reading = _routine_row(spec)
        rows.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "kind": spec["kind"],
                "cadence": spec["cadence"],
                "does": spec["does"],
                "state": reading.get("state", "degraded"),
                "detail": reading.get("detail", ""),
                "last_run": reading.get("last_run"),
                "next_run": reading.get("next_run"),
                "since": reading.get("since"),
            }
        )
    return rows


def _machine_state(readings):
    """What the machine's own readings amount to, and why.

    A reading that could not be taken never lowers the verdict. Not knowing how
    warm the board is is a hole in the report rather than evidence of a hot
    board, and treating it as the latter would have the tab crying wolf on
    every machine without the firmware tool installed.
    """
    notes = []
    worst = "operational"

    def lower(to, why):
        nonlocal worst
        notes.append(why)
        if RANK[to] > RANK[worst]:
            worst = to

    room = readings["disk"].get("percent")
    if room is not None and room >= DISK_CRITICAL_PERCENT:
        lower("down", "the disk is nearly full")
    elif room is not None and room >= DISK_WARN_PERCENT:
        lower("degraded", "the disk is filling up")

    heat = readings["temperature_c"]
    if heat is not None and heat >= TEMP_CRITICAL_C:
        lower("down", "the board is too hot")
    elif heat is not None and heat >= TEMP_WARN_C:
        lower("degraded", "the board is running warm")

    held = readings["throttling"]
    if held.get("read") and held.get("now"):
        lower("degraded", held["now"][0])

    return worst, notes


def build():
    """One reading of the whole machine."""
    readings = {
        "host": host(),
        "load": load(),
        "memory": memory(),
        "disk": disk(),
        "temperature_c": temperature(),
        "throttling": throttling(),
    }
    rows = routines()
    machine, notes = _machine_state(readings)

    # The worst thing standing, whichever half of the report it came from. A
    # machine at four percent load with a dead routine is not an operational
    # machine, and the headline has to say so or nobody reads the rest.
    overall = machine
    for row in rows:
        if RANK.get(row["state"], 2) > RANK[overall]:
            overall = row["state"]

    stopped = [row["label"] for row in rows if row["state"] == "down"]
    behind = [row["label"] for row in rows if row["state"] == "degraded"]

    return {
        "updated_at": _stamp(time.time()),
        "overall": overall,
        # Why the headline says what it says, in the order a reader would want
        # it: what has stopped, what is behind, then the machine itself.
        "notes": (
            ["%s is not running" % name for name in stopped]
            + ["%s is behind" % name for name in behind]
            + notes
        ),
        **readings,
        "routines": rows,
    }


def feed():
    """The last reading, taken again once it is older than the cache allows.

    Inline rather than behind the request, unlike the site feed beside this: a
    build here is a handful of local file reads and `systemctl show` calls, all
    of which answer in milliseconds, so there is nothing to hide behind a
    thread and a background refresh would only add a way for the answer to be
    stale for reasons nobody can see.
    """
    with _lock:
        if _cache["body"] and time.time() - _cache["built"] < CACHE_SECONDS:
            return _cache["body"]
    body = json.dumps(build())
    with _lock:
        _cache.update(body=body, built=time.time())
    return body
