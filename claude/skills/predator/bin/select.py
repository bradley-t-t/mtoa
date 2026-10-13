#!/usr/bin/env python3
"""Pick hunt targets from the slop catalog and remember what has been hunted.

The catalog is 1000 numbered conditions. A run hunts a handful. State is kept
per project so a second run covers new ground instead of re-treading the first.
"""
import argparse
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CATALOG = os.path.join(SKILL, "references", "catalog.md")
STATE = os.path.join(SKILL, "state")

ITEM = re.compile(r"^(\d+)\.\s+(.*\S)\s*$")
SECTION = re.compile(r"^##\s+(\d+)\.\s+(.*\S)\s*$")


def load(path):
    if not os.path.exists(path):
        sys.exit("catalog not found: %s" % path)
    items, section, snum = {}, "unsectioned", 0
    for line in open(path, encoding="utf-8"):
        s = SECTION.match(line)
        if s:
            snum, section = int(s.group(1)), s.group(2)
            continue
        m = ITEM.match(line)
        if m:
            n = int(m.group(1))
            items[n] = {"n": n, "section": section, "section_n": snum, "text": m.group(2)}
    if not items:
        sys.exit("catalog parsed to zero items: %s" % path)
    return items


def state_path(project):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", project).strip("-") or "unknown"
    return os.path.join(STATE, slug + ".json")


def read_state(project):
    p = state_path(project)
    if not os.path.exists(p):
        return {"project": project, "runs": [], "resolved": [], "unresolved": [], "clean": []}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def write_state(project, data):
    os.makedirs(STATE, exist_ok=True)
    with open(state_path(project), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")


def cmd_select(a):
    items = load(a.catalog)
    if a.items:
        want = []
        for chunk in a.items.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if not chunk.isdigit() or int(chunk) not in items:
                sys.exit("not a catalog number: %s" % chunk)
            want.append(items[int(chunk)])
        out = want
        pool_size = len(items)
        skipped = 0
    else:
        pool = list(items.values())
        if a.section:
            pool = [i for i in pool if i["section_n"] == a.section]
            if not pool:
                sys.exit("no section %d in catalog" % a.section)
        pool_size = len(pool)
        skipped = 0
        if not a.repeat:
            st = read_state(a.project)
            seen = set(st.get("resolved", [])) | set(st.get("clean", []))
            fresh = [i for i in pool if i["n"] not in seen]
            skipped = len(pool) - len(fresh)
            if len(fresh) >= a.count:
                pool = fresh
        random.shuffle(pool)
        out = sorted(pool[: a.count], key=lambda i: i["n"])
    print(json.dumps({
        "targets": out,
        "pool": pool_size,
        "already_hunted": skipped,
        "catalog": a.catalog,
    }, indent=1))


def cmd_record(a):
    st = read_state(a.project)
    payload = json.loads(sys.stdin.read())
    run = {
        "at": a.at,
        "root": a.root,
        "resolved": sorted(set(payload.get("resolved", []))),
        "unresolved": sorted(set(payload.get("unresolved", []))),
        "clean": sorted(set(payload.get("clean", []))),
        "branch": a.branch,
    }
    st["runs"].append(run)
    for key in ("resolved", "unresolved", "clean"):
        st[key] = sorted(set(st.get(key, [])) | set(run[key]))
    st["unresolved"] = sorted(set(st["unresolved"]) - set(st["resolved"]))
    write_state(a.project, st)
    print(json.dumps({"recorded": run, "runs": len(st["runs"])}, indent=1))


def cmd_status(a):
    items = load(a.catalog)
    st = read_state(a.project)
    done = set(st.get("resolved", [])) | set(st.get("clean", []))
    print(json.dumps({
        "project": a.project,
        "runs": len(st.get("runs", [])),
        "catalog": len(items),
        "covered": len(done),
        "remaining": len(items) - len(done),
        "resolved": len(st.get("resolved", [])),
        "clean": len(st.get("clean", [])),
        "unresolved": st.get("unresolved", []),
    }, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", default=CATALOG)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="pick targets for a run")
    s.add_argument("--project", required=True)
    s.add_argument("--count", type=int, default=5)
    s.add_argument("--section", type=int, help="restrict to one catalog section")
    s.add_argument("--items", help="explicit comma-separated catalog numbers")
    s.add_argument("--repeat", action="store_true", help="allow already-hunted numbers")
    s.add_argument("--catalog", default=CATALOG)
    s.set_defaults(func=cmd_select)

    r = sub.add_parser("record", help="write a run's outcome to state, payload on stdin")
    r.add_argument("--project", required=True)
    r.add_argument("--root", default="")
    r.add_argument("--branch", default="")
    r.add_argument("--at", required=True, help="ISO timestamp from the caller")
    r.set_defaults(func=cmd_record)

    t = sub.add_parser("status", help="coverage so far for a project")
    t.add_argument("--project", required=True)
    t.add_argument("--catalog", default=CATALOG)
    t.set_defaults(func=cmd_status)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
