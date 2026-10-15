#!/usr/bin/env python3
"""Read all usage: every Claude session on this machine and on the host.

Usage means all of it unless a narrower scope is named - every session, every
project, every machine - never the one chat in view. A behaviour seen in one
chat is a reading of one chat; whether it keeps happening is a question about
all of them, and this is the one command that asks it. Ratchet's guard denies a
turn's first read of a single transcript until this has run, so the whole
picture comes first and any narrower read follows it.

  usage.py                          stop holds by reason, day and session (last 24h)
  usage.py holds --since 7d         the same over a week (also 90m, 12h, or an ISO time)
  usage.py search <regex>           every typed message and reply that matches, newest first
  usage.py search <regex> --who user
  usage.py ... --json               the records, one JSON object per line

Each machine is read where it lives: this one from disk, the host over ssh by
running this same file there. A machine that could not be read is named as
unread, never counted as quiet, and cloud sessions are named as unread on every
run because their transcripts stay in their containers.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
NOT_TYPED = ("<", "This session is being continued")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]


def since_iso(text):
    m = re.fullmatch(r"(\d+)\s*([mhd])", text.strip())
    if not m:
        return text.replace("Z", "")
    n = int(m.group(1))
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[m.group(2)]
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%S")


def texts(row, kind):
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip() if kind == "user" else ""
    if not isinstance(content, list):
        return ""
    return " ".join(p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text").strip()


def reason(message):
    head = message.split(":", 1)[0].strip()[:20]
    detail = re.findall(r"\n\s{4,}(\S.*)", message)
    tail = detail[0] if detail else message[len(head) + 1:].strip().split("\n")[0]
    return "%s: %s" % (head, tail.split(" - ")[0].strip()[:80])


def collect(since, pattern=None, who="all"):
    """This machine's records since `since`, as dicts."""
    cut = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    rx = re.compile(pattern, re.I) if pattern else None
    sessions = 0
    for path in PROJECTS.glob("*/*.jsonl"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cut:
                continue
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        sessions += 1
        with handle:
            for line in handle:
                if rx is None:
                    if '"hook_blocking_error"' not in line and '"type":"user"' not in line:
                        continue
                elif not rx.search(line):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                stamp = row.get("timestamp") or ""
                if stamp[:19] < since[:19] or row.get("isSidechain"):
                    continue
                origin = row.get("turnOrigin") or ""
                base = {"t": stamp[:19], "s": path.stem[:8], "p": path.parent.name,
                        "e": "sdk" if origin == "sdk" else row.get("entrypoint") or ""}
                kind = row.get("type")
                if rx is None:
                    attachment = row.get("attachment") or {}
                    if attachment.get("type") == "hook_blocking_error" and attachment.get("hookEvent") == "Stop":
                        error = attachment.get("blockingError")
                        error = error.get("blockingError", "") if isinstance(error, dict) else str(error or "")
                        yield dict(base, k="hold", r=reason(error))
                    elif kind == "user" and not row.get("isMeta"):
                        said = texts(row, "user")
                        if said and not said.startswith(NOT_TYPED):
                            yield dict(base, k="turn")
                    continue
                if kind not in ("user", "assistant") or row.get("isMeta") or who not in ("all", kind):
                    continue
                said = texts(row, kind)
                if not said or said.startswith(NOT_TYPED) or not rx.search(said):
                    continue
                hit = rx.search(said)
                start = max(0, hit.start() - 160)
                yield dict(base, k="match", w=kind, x=" ".join(said[start:start + 420].split()))
    yield {"k": "read", "n": sessions}


def host():
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import _lib
        return _lib.setting("host.ssh") or ""
    except Exception:
        return ""


def gather(args):
    """[(machine, records or None)] for this machine and the host."""
    here = socket.gethostname().split(".")[0]
    since = since_iso(args.since)
    out = [(here, list(collect(since, args.pattern, args.who)))]
    remote = host()
    if remote and remote not in (here, socket.gethostname()):
        cmd = SSH + [remote, "python3", "-", "--local", "--since", since, "--who", args.who]
        if args.pattern:
            cmd += ["--pattern", args.pattern]
        try:
            done = subprocess.run(cmd, input=Path(__file__).read_text(), capture_output=True,
                                  text=True, timeout=90)
            got = [json.loads(l) for l in done.stdout.splitlines() if l.startswith("{")]
            out.append((remote, got if any(r.get("k") == "read" for r in got) else None))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            out.append((remote, None))
    elif remote:
        out.append(("the workstation", None))
    return since, out


def report(args):
    since, machines = gather(args)
    records = [dict(r, m=m) for m, got in machines if got for r in got if r["k"] != "read"]
    if args.json:
        for r in records:
            print(json.dumps(r))
        return 0
    read = ["%s %d sessions" % (m, next(r["n"] for r in got if r["k"] == "read"))
            for m, got in machines if got]
    unread = [m for m, got in machines if got is None] + ["cloud sessions"]
    print("Read since %sZ: %s. Unread: %s." % (since, ", ".join(read), ", ".join(unread)))
    if args.command == "search":
        hits = sorted((r for r in records if r["k"] == "match"), key=lambda r: r["t"], reverse=True)
        print("%d matches." % len(hits))
        for r in hits[:args.limit]:
            print("%s  %s %s %-9s %s | %s" % (r["t"], r["m"], r["s"], r["w"], r["p"][-40:], r["x"]))
        return 0
    typed = [r for r in records if r["k"] == "turn" and not r["e"].startswith("sdk")]
    holds = [r for r in records if r["k"] == "hold"]
    print("%d stop holds in %d typed turns (%.2f a turn)." % (
        len(holds), len(typed), len(holds) / max(len(typed), 1)))
    print("\nBy reason:")
    for why, n in collections.Counter(r["r"] for r in holds).most_common():
        print("  %3d  %s" % (n, why))
    print("\nBy day:")
    days = collections.Counter(r["t"][:10] for r in typed)
    held = collections.Counter(r["t"][:10] for r in holds)
    for day in sorted(set(days) | set(held)):
        print("  %s  %3d turns  %3d holds  %.2f" % (day, days[day], held[day], held[day] / max(days[day], 1)))
    print("\nBy session:")
    for (m, s, p), n in collections.Counter((r["m"], r["s"], r["p"]) for r in holds).most_common(12):
        print("  %3d  %s %s %s" % (n, m, s, p))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", nargs="?", default="holds", choices=["holds", "search"])
    parser.add_argument("pattern", nargs="?")
    parser.add_argument("--since", default="24h")
    parser.add_argument("--who", default="all", choices=["all", "user", "assistant"])
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--local", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pattern", dest="local_pattern", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.local:
        for record in collect(args.since, args.local_pattern, args.who):
            print(json.dumps(record))
        return 0
    if args.command == "search" and not args.pattern:
        parser.error("search takes a regex")
    if args.command == "holds":
        args.pattern = None
    return report(args)


if __name__ == "__main__":
    sys.exit(main())
