#!/usr/bin/env python3
"""Cases the run-pointer pruner has to get right.

Everything here turns on one distinction: a run somebody is still in against a
run whose session ended. Getting it wrong in one direction costs a refusal in a
session that opened nothing; getting it wrong in the other takes an open run
away from whoever is working it. So the cases hold both, and they hold the
middle as well -- a pointer neither reading settles is reported and left alone,
because a verdict nothing supports is the one thing this must not invent.

Every fixture is a temporary directory and the state is pinned inside it through
INTELLIGENCE_STATE_DIR, so no case can reach the live run pointers.

  checklist-runs-cases.py     run them
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "checklist-runs.py")

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (label, got, want))


def load(state_dir):
    os.environ["INTELLIGENCE_STATE_DIR"] = str(state_dir)
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed(state, kind, session, run_id, age_hours=None, record=True):
    """One run pointer, and the record behind it aged to order."""
    runs = state / ("%s-runs" % kind)
    runs.mkdir(parents=True, exist_ok=True)
    (runs / ("current-%s.json" % session)).write_text(json.dumps({"id": run_id}))
    if record:
        held = runs / ("%s.json" % run_id)
        held.write_text(json.dumps({"id": run_id, "gates": {}}))
        if age_hours is not None:
            old = time.time() - age_hours * 3600
            os.utime(held, (old, old))
    return runs / ("current-%s.json" % session)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        mod = load(state)

        live = seed(state, "design", "aaa", "20260829-000001", age_hours=1)
        stale = seed(state, "design", "bbb", "20260820-000001", age_hours=100)
        orphan = seed(state, "design", "ccc", "20260101-000001", record=False)

        check("a run worked an hour ago is live",
              mod.verdict(live, 12)[0], "live")
        check("a run quiet for days is abandoned",
              mod.verdict(stale, 12)[0], "abandoned")
        check("it says how long it was quiet",
              "quiet" in mod.verdict(stale, 12)[1], True)
        check("a pointer to a run with no record is abandoned",
              mod.verdict(orphan, 12)[0], "abandoned")
        check("it says the record is missing",
              "no record" in mod.verdict(orphan, 12)[1], True)

        # The threshold is the whole judgement, so it has to be the threshold
        # and not the file's age in the abstract.
        check("a longer window keeps the same run live",
              mod.verdict(stale, 200)[0], "live")

        broken = state / "design-runs" / "current-ddd.json"
        broken.write_text("{ not json")
        check("a pointer that cannot be read is abandoned",
              mod.verdict(broken, 12)[0], "abandoned")
        nameless = state / "design-runs" / "current-eee.json"
        nameless.write_text(json.dumps({"target": "/somewhere"}))
        check("a pointer naming no run is abandoned",
              mod.verdict(nameless, 12)[0], "abandoned")

        rows = mod.survey(("design",), 12)
        check("every pointer is surveyed", len(rows), 5)
        check("report is non-zero while anything is abandoned",
              mod.report(rows, False), 1)

        check("a dry run removes nothing", mod.prune(rows, True), 0)
        check("and leaves them all on disk",
              len(mod.survey(("design",), 12)), 5)

        mod.prune(rows, False)
        left = mod.survey(("design",), 12)
        check("pruning removes only the abandoned", len(left), 1)
        check("the live run survives", left[0]["state"], "live")
        check("report is zero once nothing is abandoned",
              mod.report(left, False), 0)

        check("an engine with no runs directory surveys empty",
              mod.survey(("db",), 12), [])

        # -------------------------------------------------------------- usage
        for verb in ("", "frobnicate"):
            done = subprocess.run(
                [sys.executable, str(TARGET)] + ([verb] if verb else []),
                capture_output=True, text=True,
                env=dict(os.environ, INTELLIGENCE_STATE_DIR=str(state)))
            check("usage rejects %r" % verb, done.returncode, 2)

    for failure in FAILURES:
        print("FAIL %s" % failure)
    print("%d failure(s)" % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
