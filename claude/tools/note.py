#!/usr/bin/env python3
"""Notes taken through the day, kept where every machine and every account reads them.

A note written on the laptop is worth nothing to the session running on the Pi,
or to a cloud container opened tomorrow, unless it travels. These live under
`~/.claude/notes`, which is a sync path, so `config-sync.py` carries them to the
intelligence mirror and out to every other machine on its own schedule, and
`cloud/bootstrap.sh` installs them into a fresh container. Nothing here syncs
anything itself; it writes files the existing sync already understands.

  note.py add "text"        append a note to today
  note.py add -             append a note read from stdin
  note.py today             read today, every machine's notes merged
  note.py day 2026-09-04    read one day
  note.py recent [n]        the last n notes, newest first (default 10)
  note.py search <term>     every note carrying a term
  note.py days              which days hold notes
  note.py sync              hand them to the mirror now rather than on the hour

ONE FILE PER DAY PER MACHINE, and never edited after it is written. The sync
settles a contested file by last edit wins, which is right for a tool and fatal
for a log: two machines appending to one shared file would lose whichever half
synced first. A file only one machine ever appends to is never contested, so
every note survives and a day is read by merging the files rather than by
holding one.

Tags are written inline as `#tag` because a note is prose first. `search` reads
them the same way it reads any other word.
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows holds the file by opening it for append instead
    fcntl = None

HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
NOTES = HOME / "notes"

# Kept in one place because the reader parses what the writer wrote.
TIME_FORMAT = "%-I:%M %p" if os.name != "nt" else "%I:%M %p"
ENTRY = re.compile(r"^## (\d{1,2}:\d{2} [ap]m)\s*$", re.I)
FILENAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.([A-Za-z0-9-]+)\.md$")


def host():
    """This machine, as a short slug fit for a filename.

    The name is part of the filename rather than of the note, because it is what
    keeps two machines out of each other's file. A machine that renames itself
    starts a second file for the day, which merges on read like any other.
    """
    name = (platform.node() or "unknown").split(".")[0].strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return slug or "unknown"


def clock(moment):
    return moment.strftime(TIME_FORMAT).lower()


def path_for(day, machine=None):
    return NOTES / f"{day.isoformat()}.{machine or host()}.md"


def add(text, when=None):
    """Append one note to today's file for this machine."""
    body = text.strip()
    if not body:
        raise ValueError("a note with nothing in it is not a note")
    moment = when or datetime.now().astimezone()
    target = path_for(moment.date())
    NOTES.mkdir(parents=True, exist_ok=True)

    opening = ""
    if not target.exists():
        opening = (f"# {moment.strftime('%A, %B %-d %Y')} on {host()}\n\n"
                   if os.name != "nt" else
                   f"# {moment.strftime('%A, %B %d %Y')} on {host()}\n\n")

    # Opened for append and locked, so two sessions on one machine writing at
    # the same moment interleave as whole notes rather than as half lines.
    with open(target, "a", encoding="utf-8") as handle:
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(f"{opening}## {clock(moment)}\n{body}\n\n")
        finally:
            if fcntl:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return target


def read_day(day):
    """Every note written on one day, on any machine, oldest first."""
    found = []
    if not NOTES.is_dir():
        return found
    for file in sorted(NOTES.glob(f"{day}.*.md")):
        match = FILENAME.match(file.name)
        machine = match.group(2) if match else "unknown"
        found.extend(_parse(file, day, machine))
    found.sort(key=lambda note: (note["sort"], note["machine"]))
    return found


def _parse(file, day, machine):
    """The notes in one file, as records."""
    notes, at, lines = [], None, []

    def close():
        if at is None:
            return
        body = "\n".join(lines).strip()
        if body:
            try:
                sort = datetime.strptime(at.lower().replace(" ", ""), "%I:%M%p").time()
            except ValueError:
                sort = datetime.min.time()
            notes.append({"day": day, "at": at, "machine": machine,
                          "text": body, "sort": sort})

    for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
        heading = ENTRY.match(line)
        if heading:
            close()
            at, lines = heading.group(1), []
        elif at is not None:
            lines.append(line)
    close()
    return notes


def days():
    """Every day that holds a note, newest first."""
    if not NOTES.is_dir():
        return []
    found = {match.group(1) for match in
             (FILENAME.match(file.name) for file in NOTES.glob("*.md")) if match}
    return sorted(found, reverse=True)


def render(notes, show_day=False):
    if not notes:
        return "no notes"
    out = []
    for note in notes:
        stamp = f"{note['day']} {note['at']}" if show_day else note["at"]
        out.append(f"{stamp}  [{note['machine']}]\n{note['text']}\n")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")

    writing = sub.add_parser("add", help="append a note to today")
    writing.add_argument("text", nargs="*", help='the note, or - to read stdin')

    sub.add_parser("today", help="read today")
    one = sub.add_parser("day", help="read one day")
    one.add_argument("date")
    lately = sub.add_parser("recent", help="the last n notes")
    lately.add_argument("count", nargs="?", type=int, default=10)
    finding = sub.add_parser("search", help="every note carrying a term")
    finding.add_argument("term")
    sub.add_parser("days", help="which days hold notes")
    sub.add_parser("sync", help="hand them to the mirror now")

    args = parser.parse_args(argv)
    command = args.command or "today"

    if command == "add":
        words = args.text or []
        text = sys.stdin.read() if words == ["-"] else " ".join(words)
        if not text.strip() and not sys.stdin.isatty():
            text = sys.stdin.read()
        try:
            target = add(text)
        except ValueError as err:
            print(err, file=sys.stderr)
            return 1
        print(f"noted in {target.name}")
        return 0

    if command == "today":
        print(render(read_day(datetime.now().astimezone().date().isoformat())))
        return 0

    if command == "day":
        print(render(read_day(args.date)))
        return 0

    if command == "days":
        found = days()
        print("\n".join(found) if found else "no notes")
        return 0

    if command == "recent":
        gathered = []
        for day in days():
            gathered.extend(reversed(read_day(day)))
            if len(gathered) >= args.count:
                break
        print(render(gathered[:args.count], show_day=True))
        return 0

    if command == "search":
        term = args.term.lower()
        hits = [note for day in days() for note in read_day(day)
                if term in note["text"].lower()]
        print(render(hits, show_day=True))
        return 0 if hits else 1

    if command == "sync":
        import subprocess
        tool = Path(__file__).resolve().parent / "config-sync.py"
        return subprocess.run([sys.executable, str(tool), "sync"]).returncode

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
