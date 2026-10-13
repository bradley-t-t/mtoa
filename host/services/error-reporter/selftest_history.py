#!/usr/bin/env python3
"""Hold the fingerprint history to the thing it exists to stop.

A ticket used to reach a run carrying one browser's report and nothing else, so
a fault that had already been filed a dozen times looked exactly like a fault
nobody had ever seen. The queue's own record of what that cost:

  fp:deaad70208f6  a hero video      13 tickets, 12 closed no-defect, then fixed
  fp:e2fd5d7df641  a webfont         12 tickets, 11 closed no-defect, then fixed
  RootRise, two fingerprints          4 tickets each, all four closed resolved

The first two are a real defect dismissed as a network blip once per ticket,
because one report of one lost file genuinely does look like one. The third is a
fix that did not hold, re-shipped four times by four runs none of which knew the
other three had been there.

So the test is the replay. Run each of those histories through the reporter's
own bookkeeping and require the body of the ticket at the end to carry what the
earlier ones concluded. A change that files the thirteenth ticket still looking
like the first is the regression this file exists to catch.

    python3 selftest_history.py

Loads the history functions out of `error_reporter.py` rather than importing it,
because importing it reads a config file and starts a server.
"""

import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "error_reporter.py"


def load(base):
    """The real history bookkeeping, lifted out of the shipped file.

    `BASE` is redirected at a scratch directory so a run of this test cannot
    touch the ledger the live service is keeping.
    """
    src = SOURCE.read_text()
    fragment = src[src.index("HISTORY_PATH = os.path.join("):src.index("# A verdict only holds")]
    namespace = {
        "os": __import__("os"), "json": json, "time": time, "threading": threading,
        "BASE": str(base),
    }
    exec(compile(fragment, str(SOURCE), "exec"), namespace)
    return namespace


FAILURES = []


def check(label, condition, detail=""):
    print("  %s %s%s" % ("pass" if condition else "FAIL", label,
                         "" if condition else "  <- " + detail))
    if not condition:
        FAILURES.append(label)


def replay(api, fingerprint, tickets, reports_between=10):
    """Files each ticket in turn, closing it with its verdict, and returns the last body."""
    body = ""
    for number, verdict in tickets:
        for _ in range(reports_between):
            api["note_report"](fingerprint)
        body = api["history_note"](fingerprint)
        api["note_filing"](fingerprint, number)
        if verdict:
            api["note_verdict"](fingerprint, number, verdict)
    return body


def main():
    with tempfile.TemporaryDirectory() as scratch:
        api = load(Path(scratch))

        print("\nA first sighting says nothing")
        first = replay(api, "aaaa00000001", [(1, None)])
        check("no Previously block on the first ticket", first == "",
              "got %r" % first[:80])

        print("\nThe hero video: twelve dismissals, then the thirteenth ticket")
        video = [(number, "no-defect") for number in
                 (89, 97, 98, 102, 104, 108, 127, 135, 138, 147, 148, 150)]
        body = replay(api, "deaad70208f6", video + [(160, None)])
        check("names how many times it was filed before", "filed 12 times before" in body,
              body[:200])
        check("names the earlier tickets", "#150" in body and "#89" in body, body[:200])
        check("tallies the verdicts", "12 no-defect" in body, body[:200])
        check("counts the reports since the last filing", re.search(r"since the last of those was filed: \d+, and that is a floor", body) is not None,
              body[:300])
        check("puts the burden on the dismissal", "keeps failing" in body, body[:400])

        print("\nA fix that did not hold")
        rootrise = [(number, "resolved") for number in (211, 240, 266)]
        body = replay(api, "bbbb00000002", rootrise + [(301, None)])
        check("says the earlier fix did not hold", "come back" in body and "did not hold" in body,
              body[:300])
        check("names the most recent fix", "#266" in body, body[:300])
        check("does not lecture about dismissals when there were none",
              "keeps failing" not in body, body[:300])
        check("counts the fixes", "3 separate tickets" in body, body[:300])

        print("\nA verdict that changed is the one that counts")
        api["note_filing"]("cccc00000003", 400)
        api["note_verdict"]("cccc00000003", 400, "no-defect")
        api["note_verdict"]("cccc00000003", 400, "resolved")
        body = api["history_note"]("cccc00000003")
        check("reads the later verdict", "1 resolved" in body, body[:200])
        check("does not also count the earlier one", "no-defect" not in body, body[:200])

        print("\nThe sweep backfills a fingerprint the reporter never filed")
        api["note_verdict"]("dddd00000004", 500, "no-defect")
        api["note_verdict"]("dddd00000004", 501, "no-defect")
        body = api["history_note"]("dddd00000004")
        check("history exists without a filing having been recorded",
              "filed 2 times before" in body, body[:200])

        print("\nA long history stays readable")
        many = [(number, "no-defect") for number in range(600, 640)]
        body = replay(api, "eeee00000005", many + [(700, None)], reports_between=1)
        check("caps the list it prints", body.count("#") <= 14, body[:400])
        check("still names the true total", "filed 40 times before" in body, body[:200])
        check("says how many it left out", "and 28 older" in body, body[:300])

        print("\nA held-back report still counts toward the total")
        api["note_report"]("ffff00000006")
        api["note_report"]("ffff00000006")
        history = json.loads((Path(scratch) / "history.json").read_text())
        check("reports counted with no ticket filed",
              history["ffff00000006"]["reports"] == 2,
              json.dumps(history.get("ffff00000006")))

    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("all clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
