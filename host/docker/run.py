#!/usr/bin/env python3
"""Run the host services in one container, on the schedules their systemd timers keep.

On every start the code is laid out under $HOME as deploy.map lays it out on a real
host (~/<service>/, ~/lib/, ~/.claude/tools/), leaving config.json, slack.json and
every state file where they are. Then the error reporter runs for good, restarted if
it stops, and the rest run on their timers' intervals. A service with no config.json
is not set up, and is skipped with one line saying so.
"""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

CODE = Path(os.environ.get("MTOA_CODE", "/opt/mtoa"))
HOME = Path.home()

# (service, script, every N seconds, or "HH:MM" UTC once a day, or None to keep running)
JOBS = [
    ("error-reporter", "error_reporter.py", None),
    ("uptime-monitor", "monitor.py", 60),
    ("status-board", "status_board.py", 120),
    ("queue-board", "queue_board.py", 120),
    ("attention-board", "attention_board.py", 120),
    ("expiry-monitor", "expiry_monitor.py", "09:00"),
]


def log(*parts):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *parts, flush=True)


def lay_out():
    for service in sorted((CODE / "services").iterdir()):
        dest = HOME / service.name
        dest.mkdir(parents=True, exist_ok=True)
        for path in service.iterdir():
            if path.is_file() and not path.name.endswith((".service", ".timer")):
                shutil.copy2(path, dest / path.name)
    tools = HOME / ".claude" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    for path in (CODE / "tools").iterdir():
        shutil.copy2(path, tools / path.name)


def ready(service):
    if (HOME / service / "config.json").is_file():
        return True
    log(service, "is not set up: no", HOME / service / "config.json")
    return False


def run_once(service, script):
    started = time.time()
    done = subprocess.run([sys.executable, "-u", str(HOME / service / script)], cwd=HOME / service)
    log(service, "exited", done.returncode, "after %.0fs" % (time.time() - started))


def keep_running(service, script):
    delay = 5
    while True:
        started = time.time()
        run_once(service, script)
        delay = 5 if time.time() - started > 60 else min(delay * 2, 300)
        time.sleep(delay)


def every(service, script, seconds):
    while True:
        run_once(service, script)
        time.sleep(seconds)


def daily(service, script, at):
    hour, minute = (int(x) for x in at.split(":"))
    while True:
        now = time.gmtime()
        wait = ((hour - now.tm_hour) * 3600 + (minute - now.tm_min) * 60 - now.tm_sec) % 86400
        time.sleep(wait or 86400)
        run_once(service, script)


def main():
    lay_out()
    threads = []
    for service, script, when in JOBS:
        if not ready(service):
            continue
        target = (keep_running if when is None else daily if isinstance(when, str) else every)
        args = (service, script) if when is None else (service, script, when)
        thread = threading.Thread(target=target, args=args, daemon=True, name=service)
        thread.start()
        threads.append(thread)
        log(service, "started", "" if when is None else "(%s)" % when)
    if not threads:
        log("nothing is set up; add a config.json for a service and restart")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
