#!/usr/bin/env python3
"""Reads a URL's performance from the PageSpeed Insights API, lab and field in one call.

Two different numbers travel under the word "performance" and confusing them wastes
whole days. The lab score is Lighthouse run once, on a simulated slow connection, on
Google's hardware: reproducible, dominated by loading, and not what anybody
experienced. The field record is the Chrome UX Report - real visits to this URL over
the previous 28 days, reported at the 75th percentile, and it is the one that
describes the site and the one search ranks. A site can sit at 95 in the lab and be
rated poor in the field, and the reverse is just as common.

This asks for both, because the first question in any performance run is which of the
two is actually failing.

    psi.py measure --url <url> [--strategy mobile|desktop] --out result.json
    psi.py field   --url <url>            the field record alone, no lab run
    psi.py compare --before a.json --after b.json
    psi.py routes  --url <url> --path / --path /pricing --out results/

The API is free. Without a key it runs against a shared anonymous quota that is
usually exhausted, so it wants one: any Google Cloud project with the PageSpeed
Insights API enabled will do, and the free tier is 25,000 queries a day. Pass it as
--key, or put it in GOOGLE_PAGESPEED_API_KEY. On this setup it is in CryptoFort as
`google-pagespeed-api-key`; read it there at the point of use rather than writing it
into a file.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# The audit ids the run reports on, and the shorthand each is filed under.
LAB = {
    "first-contentful-paint": "fcp",
    "largest-contentful-paint": "lcp",
    "total-blocking-time": "tbt",
    "cumulative-layout-shift": "cls",
    "speed-index": "si",
    "interactive": "tti",
}

# The field metrics, and the threshold each is good below. These are Google's own
# and they are what "needs improvement" and "poor" are measured against.
FIELD = {
    "FIRST_CONTENTFUL_PAINT_MS": ("fcp", 1800),
    "LARGEST_CONTENTFUL_PAINT_MS": ("lcp", 2500),
    "INTERACTION_TO_NEXT_PAINT": ("inp", 200),
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": ("cls", 0.1),
    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": ("ttfb", 800),
}


def api_key(explicit=None):
    return explicit or os.environ.get("GOOGLE_PAGESPEED_API_KEY") or ""


def fetch(url, strategy, key, tries=3):
    query = {"url": url, "strategy": strategy, "category": "performance"}
    if key:
        query["key"] = key
    target = "%s?%s" % (ENDPOINT, urllib.parse.urlencode(query))
    last = ""
    for attempt in range(tries):
        try:
            out = subprocess.run(["curl", "-sS", "--max-time", "180", target],
                                 capture_output=True, text=True, timeout=200)
        except (OSError, subprocess.SubprocessError) as exc:
            last = str(exc)
            continue
        try:
            payload = json.loads(out.stdout)
        except ValueError:
            last = out.stdout[:300] or out.stderr[:300]
            continue
        if "error" in payload:
            message = payload["error"].get("message", "")
            last = message
            # A 429 without a key is the shared anonymous quota, which no amount of
            # waiting on this machine will free. With a key it is this project's own
            # rate limit and backing off does clear it.
            if payload["error"].get("code") == 429 and key:
                time.sleep(8 * (attempt + 1))
                continue
            break
        return payload, ""
    return None, last


def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return round((ordered[middle - 1] + ordered[middle]) / 2, 3)


def read_lab(payload):
    result = payload.get("lighthouseResult") or {}
    audits = result.get("audits") or {}
    metrics = {}
    score = ((result.get("categories") or {}).get("performance") or {}).get("score")
    if score is not None:
        metrics["score"] = round(score * 100)
    for audit_id, short in LAB.items():
        audit = audits.get(audit_id) or {}
        value = audit.get("numericValue")
        if value is None:
            continue
        metrics[short] = round(value, 3) if short == "cls" else round(value)
    return metrics, result


def read_field(payload):
    """The field record for the URL, falling back to the origin's.

    A page with too little traffic has no record of its own, and the origin's is a
    fair description of the same site built the same way.
    """
    for key, label in (("loadingExperience", "page"), ("originLoadingExperience", "origin")):
        block = payload.get(key) or {}
        raw = block.get("metrics") or {}
        if not raw:
            continue
        out = {"source": label, "overall": block.get("overall_category", "")}
        for name, (short, _) in FIELD.items():
            entry = raw.get(name)
            if entry and entry.get("percentile") is not None:
                value = entry["percentile"]
                out[short] = round(value / 100, 3) if short == "cls" else value
        return out
    return {}


def verdicts(field):
    rows = []
    for name, (short, threshold) in FIELD.items():
        if short not in field:
            continue
        value = field[short]
        rows.append((short, value, threshold, value <= threshold))
    return rows


def opportunities(result, limit=8):
    out = []
    for audit in (result.get("audits") or {}).values():
        details = audit.get("details") or {}
        if details.get("type") != "opportunity":
            continue
        saving = (details.get("overallSavingsMs") or 0)
        if saving <= 0 and not audit.get("displayValue"):
            continue
        out.append((saving, audit.get("title", ""), audit.get("displayValue", "")))
    out.sort(reverse=True)
    return out[:limit]


def measure(args):
    key = api_key(args.key)
    if not key:
        print("No API key. The anonymous quota is shared across every keyless caller and\n"
              "is usually exhausted. Read the key from CryptoFort (google-pagespeed-api-key)\n"
              "and pass it as --key, or set GOOGLE_PAGESPEED_API_KEY.\n", file=sys.stderr)
    # One lab run on a JavaScript-heavy page swings by tens of points between calls,
    # which is wider than most of the improvements this checklist produces. The median
    # of several is what a before and an after can honestly be compared on.
    runs, field, result, spread = [], {}, {}, {}
    for n in range(max(1, args.runs)):
        payload, error = fetch(args.url, args.strategy, key)
        if payload is None:
            if not runs:
                print("PageSpeed Insights did not answer: %s" % error, file=sys.stderr)
                return 1
            break
        got, result = read_lab(payload)
        runs.append(got)
        field = read_field(payload) or field
        if args.runs > 1:
            print("  run %d: score %s, LCP %sms" % (n + 1, got.get("score", "-"),
                                                    got.get("lcp", "-")))
    metrics = {}
    for short in set().union(*(r.keys() for r in runs)):
        values = [r[short] for r in runs if short in r]
        metrics[short] = median(values)
        if len(values) > 1:
            spread[short] = [min(values), max(values)]
    if spread and args.runs > 1:
        print("  median of %d run(s); score ranged %s to %s\n"
              % (len(runs), spread.get("score", ["-", "-"])[0], spread.get("score", ["-", "-"])[1]))
    record = {"tool": "psi", "url": args.url, "strategy": args.strategy,
              "fetched": result.get("fetchTime", ""), "runs": len(runs), "spread": spread,
              "metrics": metrics, "field": field}
    print("%s  %s" % (args.url, args.strategy))
    print("  lab   score %s | FCP %sms | LCP %sms | TBT %sms | CLS %s | SI %sms"
          % (metrics.get("score", "-"), metrics.get("fcp", "-"), metrics.get("lcp", "-"),
             metrics.get("tbt", "-"), metrics.get("cls", "-"), metrics.get("si", "-")))
    if field:
        print("  field %s record, rated %s" % (field.get("source"), field.get("overall")))
        for short, value, threshold, ok in verdicts(field):
            print("        %-5s %-8s %s (good is under %s)"
                  % (short.upper(), value, "ok" if ok else "OVER", threshold))
        failing = sorted((v / t, s, v, t) for s, v, t, ok in verdicts(field) if not ok)
        if failing:
            ratio, worst, value, threshold = failing[-1]
            others = ", ".join(s.upper() for _, s, _, _ in failing[:-1])
            print("\n  Worst in the field: %s at %s, %.1fx its %s threshold.%s"
                  % (worst.upper(), value, ratio, threshold,
                     ("\n  Also over: %s." % others) if others else ""))
            if worst == "inp":
                print("  The lab score is dominated by loading, so optimising for it will not\n"
                      "  move a responsiveness rating. Work the main thread.")
            print("\n  perf-pass.py scan --metric %s" % worst)
    else:
        print("  field no record - too little traffic for this URL or this origin")
    chances = opportunities(result)
    if chances:
        print("\n  lab opportunities:")
        for saving, title, display in chances:
            print("    %-46s %s" % (title[:46], display))
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + "\n")
        print("\nWritten to %s" % out)
        print("File it against the run:\n"
              "  perf-pass.py measure --file %s --label %s" % (out, args.label or "before"))
    return 0


def field_only(args):
    key = api_key(args.key)
    payload, error = fetch(args.url, args.strategy, key)
    if payload is None:
        print("PageSpeed Insights did not answer: %s" % error, file=sys.stderr)
        return 1
    field = read_field(payload)
    if not field:
        print("No field record for %s - too little traffic for this URL or this origin."
              % args.url)
        return 1
    print("%s - %s record, rated %s" % (args.url, field["source"], field.get("overall")))
    for short, value, threshold, ok in verdicts(field):
        print("  %-5s %-8s %s (good is under %s)"
              % (short.upper(), value, "ok" if ok else "OVER", threshold))
    return 0


def compare(args):
    try:
        a = json.loads(Path(args.before).read_text())
        b = json.loads(Path(args.after).read_text())
    except (OSError, ValueError) as exc:
        print("could not read both measurements: %s" % exc, file=sys.stderr)
        return 1
    faults = []
    for field_name in ("tool", "strategy", "url"):
        if a.get(field_name) != b.get(field_name):
            faults.append("%s: %s and %s" % (field_name, a.get(field_name), b.get(field_name)))
    if faults:
        print("These two are not comparable - %s" % "; ".join(faults), file=sys.stderr)
        return 1
    am, bm = a.get("metrics", {}), b.get("metrics", {})
    print("%s  %s\n" % (a.get("url"), a.get("strategy")))
    print("  %-8s %10s %10s %12s" % ("", "before", "after", "change"))
    moved = False
    for short in ("score", "fcp", "lcp", "tbt", "cls", "si", "tti"):
        if short not in am and short not in bm:
            continue
        was, is_ = am.get(short), bm.get(short)
        change = ""
        if isinstance(was, (int, float)) and isinstance(is_, (int, float)):
            delta = is_ - was
            better = delta > 0 if short == "score" else delta < 0
            if abs(delta) > (0.001 if short == "cls" else 1):
                moved = True
                change = "%+g %s" % (round(delta, 3), "better" if better else "WORSE")
        print("  %-8s %10s %10s %12s" % (short.upper(), was, is_, change))
    if not moved:
        print("\n  Nothing moved. That is a result: the theory was wrong.")
    return 0


def routes(args):
    key = api_key(args.key)
    out_dir = Path(args.out or ".").expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in args.path:
        target = args.url.rstrip("/") + path
        payload, error = fetch(target, args.strategy, key)
        if payload is None:
            print("  %-40s failed: %s" % (path, error[:60]))
            continue
        metrics, result = read_lab(payload)
        record = {"tool": "psi", "url": target, "strategy": args.strategy,
                  "fetched": result.get("fetchTime", ""), "metrics": metrics,
                  "field": read_field(payload)}
        name = (path.strip("/").replace("/", "-") or "home") + ".json"
        (out_dir / name).write_text(json.dumps(record, indent=2) + "\n")
        rows.append((path, metrics))
        print("  %-40s score %-4s LCP %-7s TBT %s"
              % (path, metrics.get("score", "-"), metrics.get("lcp", "-"),
                 metrics.get("tbt", "-")))
    if rows:
        worst = min(rows, key=lambda r: r[1].get("score", 100))
        print("\n  Slowest route: %s at %s." % (worst[0], worst[1].get("score", "-")))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--url", required=True)
        p.add_argument("--strategy", default="mobile", choices=["mobile", "desktop"])
        p.add_argument("--key")

    m = sub.add_parser("measure"); common(m)
    m.add_argument("--out"); m.add_argument("--label", choices=["before", "after"])
    m.add_argument("--runs", type=int, default=3,
                   help="lab runs to take the median of; one run swings too far to compare")
    m.set_defaults(fn=measure)

    f = sub.add_parser("field"); common(f); f.set_defaults(fn=field_only)

    c = sub.add_parser("compare")
    c.add_argument("--before", required=True); c.add_argument("--after", required=True)
    c.set_defaults(fn=compare)

    r = sub.add_parser("routes"); common(r)
    r.add_argument("--path", action="append", default=["/"]); r.add_argument("--out")
    r.set_defaults(fn=routes)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
