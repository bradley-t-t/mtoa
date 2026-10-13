#!/usr/bin/env python3
"""Checklist run pointers whose session ended, and the guards they still block.

The six checklist engines each claim a run by writing a pointer at
`<state>/<kind>-runs/current-<session>.json`. A session that ends without
closing its run leaves the pointer behind, and nothing has ever removed one. The
Stop guard reads whatever pointer it resolves, so an abandoned run from last
week refuses a session this week over gates nobody in it opened: forty-five of
them had accumulated over seven days, and four separate design runs were
refusing turns across three sessions apiece.

Age alone is not the reading. A run somebody is working is rewritten on every
answer, so the file's own clock says whether anybody is still in it, and a
pointer to a run that no longer has a record is a pointer to nothing whatever
its clock says. Both of those are conclusive. What answers to neither is
reported and left alone, because a wrong prune costs somebody an open run and a
missed one costs a refusal in a session that never opened it.

  checklist-runs.py report            every pointer, and the verdict on it
  checklist-runs.py prune             remove the ones judged abandoned
  checklist-runs.py prune --dry-run   name them and remove nothing
  checklist-runs.py install           wire the session-start prune

  --hours N   how long a run has to have been quiet to read as abandoned (12)
  --kind K    one engine rather than all six

report exits non-zero while anything is judged abandoned, so a run that ignores
it is not a run that answered it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path


def _shared():
    """The shared _lib module, loaded by path.

    Hooks run this from arbitrary directories, so the module is found beside
    this file rather than through sys.path, with the installed tree as the
    fallback for a copy executed from elsewhere.
    """
    here = Path(__file__).resolve().parent / "_lib.py"
    path = here if here.is_file() else Path.home() / ".claude" / "tools" / "_lib.py"
    spec = importlib.util.spec_from_file_location("intelligence_tools_lib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_lib = _shared()
state_root = _lib.state_root

KINDS = ("design", "seo", "perf", "stack", "db", "google")

# A run being worked is rewritten on every answer, so quiet for this long means
# the session holding it is over. Twelve hours clears a night without touching
# anything opened during a working day.
QUIET_HOURS = 12


def pointers(kind):
    """Every claim on this engine's runs, session-scoped and shared alike."""
    runs = state_root() / ("%s-runs" % kind)
    if not runs.is_dir():
        return []
    return sorted(p for p in runs.glob("current*.json") if p.is_file())


def verdict(pointer, hours):
    """Whether this pointer is abandoned, and the reading that decided it."""
    runs = pointer.parent
    try:
        held = json.loads(pointer.read_text())
    except (OSError, ValueError):
        return "abandoned", "the pointer cannot be read"
    run_id = held.get("id")
    if not run_id:
        return "abandoned", "the pointer names no run"
    record = runs / ("%s.json" % run_id)
    if not record.is_file():
        return "abandoned", "run %s has no record" % run_id
    try:
        quiet = (time.time() - record.stat().st_mtime) / 3600
    except OSError:
        return "unknown", "the record cannot be read"
    if quiet >= hours:
        return "abandoned", "run %s quiet %dh" % (run_id, quiet)
    return "live", "run %s worked %dh ago" % (run_id, quiet)


def survey(kinds, hours):
    rows = []
    for kind in kinds:
        for pointer in pointers(kind):
            state, why = verdict(pointer, hours)
            rows.append({"kind": kind, "pointer": str(pointer),
                         "name": pointer.name, "state": state, "why": why})
    return rows


def report(rows, as_json):
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        stale = [r for r in rows if r["state"] == "abandoned"]
        if not rows:
            print("no checklist run pointers on this machine")
        else:
            for kind in sorted({r["kind"] for r in rows}):
                mine = [r for r in rows if r["kind"] == kind]
                gone = len([r for r in mine if r["state"] == "abandoned"])
                print("  %-8s %d pointer(s), %d abandoned" % (kind, len(mine), gone))
            for row in stale[:10]:
                print("    %s  %s" % (row["name"], row["why"]))
            if len(stale) > 10:
                print("    %d more abandoned, not listed" % (len(stale) - 10))
    return 1 if any(r["state"] == "abandoned" for r in rows) else 0


def prune(rows, dry_run):
    gone = [r for r in rows if r["state"] == "abandoned"]
    if not gone:
        print("nothing abandoned: every run pointer belongs to a session still in it")
        return 0
    for row in gone:
        if dry_run:
            print("  would remove %s  (%s)" % (row["name"], row["why"]))
            continue
        try:
            Path(row["pointer"]).unlink()
        except OSError as err:
            print("  could not remove %s: %s" % (row["name"], err), file=sys.stderr)
    if not dry_run:
        print("removed %d abandoned run pointer(s) across %d engine(s)"
              % (len(gone), len({r["kind"] for r in gone})))
    return 0


def install():
    """Put the session-start prune back into settings.json.

    The config sync settles a conflict by edit time over whole files, so a
    machine holding an older settings.json with a later clock drops this wiring
    and reports the run as a pull. Rewiring on every session start is what makes
    that cost one session rather than standing.
    """
    settings = Path.home() / ".claude" / "settings.json"
    try:
        data = json.loads(settings.read_text())
    except (OSError, ValueError):
        return 1
    command = "~/.claude/tools/checklist-runs.py prune"
    entries = data.setdefault("hooks", {}).setdefault("SessionStart", [])
    if any(command in hook.get("command", "")
           for entry in entries for hook in entry.get("hooks", [])):
        return 0
    entries.append({"hooks": [{"type": "command", "command": command,
                               "timeout": 20}]})
    settings.write_text(json.dumps(data, indent=2) + "\n")
    print("wired SessionStart")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=("report", "prune", "install"))
    parser.add_argument("--hours", type=int, default=QUIET_HOURS)
    parser.add_argument("--kind", choices=KINDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.verb == "install":
        return install()

    kinds = (args.kind,) if args.kind else KINDS
    rows = survey(kinds, args.hours)
    if args.verb == "report":
        return report(rows, args.json)
    return prune(rows, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
