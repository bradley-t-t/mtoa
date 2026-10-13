#!/usr/bin/env python3
"""Per-project record of which elements have been through /inspire.

A page holds a dozen elements and a run works them one at a time with a person
watching, so a run ends in the middle far more often than it ends at the end.
Without a record the next run opens on the element the last one already did.

State lives next to the skill, keyed by project, then route, then element.
"""

import argparse, json, os, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")

LANES = ("form", "motion", "behavior")


def path(project):
    return os.path.join(STATE, f"{project}.json")


def load(project):
    try:
        with open(path(project)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"project": project, "routes": {}}


def save(project, data):
    os.makedirs(STATE, exist_ok=True)
    with open(path(project), "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def cmd_status(a):
    data = load(a.project)
    routes = data["routes"]
    if a.route:
        routes = {k: v for k, v in routes.items() if k == a.route}
    if not routes:
        print(f"{a.project}: nothing recorded" + (f" for {a.route}" if a.route else ""))
        return 0
    for route, elements in sorted(routes.items()):
        print(f"\n{route}")
        for name, e in sorted(elements.items()):
            # A lane left unmatched is the useful part of the record: it is the
            # thing a person looked at and decided not to carry, or the thing a
            # run did not reach.
            done = ",".join(l for l in LANES if e.get("lanes", {}).get(l))
            skipped = ",".join(l for l in LANES if not e.get("lanes", {}).get(l))
            line = f"  {name:<24} {e['status']:<9} matched[{done or '-'}]"
            if skipped:
                line += f" open[{skipped}]"
            if e.get("reference"):
                line += f"  <- {e['reference']}"
            print(line)
    return 0


def cmd_record(a):
    data = load(a.project)
    el = data["routes"].setdefault(a.route, {}).setdefault(a.element, {})
    el["status"] = a.status
    el["lanes"] = {l: (l in (a.matched or [])) for l in LANES}
    if a.reference:
        el["reference"] = a.reference
    if a.note:
        el["note"] = a.note
    if a.files:
        el["files"] = a.files
    el["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save(a.project, data)
    print(f"recorded {a.route} / {a.element}: {a.status}")
    return 0


def cmd_remaining(a):
    """Elements named for this route that carry no full match yet."""
    data = load(a.project)
    seen = data["routes"].get(a.route, {})
    for name in a.elements:
        e = seen.get(name)
        if not e:
            print(f"{name}\tuntouched")
        elif e["status"] != "matched" or not all(e.get("lanes", {}).get(l) for l in LANES):
            open_lanes = ",".join(l for l in LANES if not e.get("lanes", {}).get(l)) or "-"
            print(f"{name}\t{e['status']}\topen[{open_lanes}]")
    return 0


def main():
    p = argparse.ArgumentParser(prog="ledger.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="what has been covered")
    s.add_argument("--project", required=True)
    s.add_argument("--route")
    s.set_defaults(fn=cmd_status)

    r = sub.add_parser("record", help="record one element")
    r.add_argument("--project", required=True)
    r.add_argument("--route", required=True)
    r.add_argument("--element", required=True)
    r.add_argument("--status", required=True,
                   choices=["matched", "partial", "declined", "inapplicable"])
    r.add_argument("--matched", nargs="*", choices=list(LANES),
                   help="lanes proved to match: form motion behavior")
    r.add_argument("--reference", help="the site the element was matched against")
    r.add_argument("--note")
    r.add_argument("--files", nargs="*")
    r.set_defaults(fn=cmd_record)

    m = sub.add_parser("remaining", help="which of these elements still need work")
    m.add_argument("--project", required=True)
    m.add_argument("--route", required=True)
    m.add_argument("elements", nargs="+")
    m.set_defaults(fn=cmd_remaining)

    a = p.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
