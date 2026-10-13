#!/usr/bin/env python3
"""Runs Lighthouse locally, against a build that has not been deployed yet.

PageSpeed Insights can only measure what is already live, which is the wrong end of
the loop: the whole point of this checklist is to fix the page before it ships. This
runs the same engine on this machine against a local preview server, so the effect of
a change is known before anybody sees it.

What it gives up is the field record. Nothing local can know what real visitors
experienced, so this answers the lab half and psi.py answers the other.

    lighthouse.py measure --url http://localhost:4173 --out after.json --label after
    lighthouse.py serve   --dir dist            print the command that serves a build

It writes the same shape psi.py does, so perf-pass.py takes either - but a before
taken one way and an after taken the other is refused by MSR-01 rather than averaged
into a claim.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH"),
    str(Path.home() / "Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

LAB = {
    "first-contentful-paint": "fcp",
    "largest-contentful-paint": "lcp",
    "total-blocking-time": "tbt",
    "cumulative-layout-shift": "cls",
    "speed-index": "si",
    "interactive": "tti",
}


def find_chrome():
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    cache = Path.home() / "Library/Caches/ms-playwright"
    if cache.is_dir():
        for entry in sorted(cache.iterdir()):
            shell = entry / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell"
            if shell.exists():
                return str(shell)
    return None


def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return round((ordered[middle - 1] + ordered[middle]) / 2, 3)


def one_run(url, strategy, chrome, out_path):
    args = ["npx", "--yes", "lighthouse", url,
            "--only-categories=performance",
            "--form-factor=%s" % ("mobile" if strategy == "mobile" else "desktop"),
            "--output=json", "--output-path=%s" % out_path,
            "--chrome-flags=--headless=new --no-sandbox --disable-gpu",
            "--quiet"]
    if strategy == "desktop":
        args.append("--preset=desktop")
    else:
        args.append("--screenEmulation.mobile")
    env = dict(os.environ)
    env["CHROME_PATH"] = chrome
    done = subprocess.run(args, capture_output=True, text=True, env=env, timeout=300)
    if not Path(out_path).exists():
        return None, (done.stderr or done.stdout)[-400:]
    return json.loads(Path(out_path).read_text()), ""


def read_lab(payload):
    audits = payload.get("audits") or {}
    metrics = {}
    score = ((payload.get("categories") or {}).get("performance") or {}).get("score")
    if score is not None:
        metrics["score"] = round(score * 100)
    for audit_id, short in LAB.items():
        value = (audits.get(audit_id) or {}).get("numericValue")
        if value is None:
            continue
        metrics[short] = round(value, 3) if short == "cls" else round(value)
    return metrics


def opportunities(payload, limit=8):
    out = []
    for audit in (payload.get("audits") or {}).values():
        details = audit.get("details") or {}
        if details.get("type") != "opportunity":
            continue
        saving = details.get("overallSavingsMs") or 0
        if saving <= 0 and not audit.get("displayValue"):
            continue
        out.append((saving, audit.get("title", ""), audit.get("displayValue", "")))
    out.sort(reverse=True)
    return out[:limit]


def measure(args):
    chrome = find_chrome()
    if not chrome:
        print("No Chrome on this machine. Set CHROME_PATH, or install one:\n"
              "  npx playwright install chromium", file=sys.stderr)
        return 2
    runs, last = [], {}
    with tempfile.TemporaryDirectory() as work:
        for n in range(max(1, args.runs)):
            out_path = str(Path(work) / ("run-%d.json" % n))
            payload, error = one_run(args.url, args.strategy, chrome, out_path)
            if payload is None:
                if not runs:
                    print("Lighthouse did not produce a result:\n%s" % error, file=sys.stderr)
                    return 1
                break
            last = payload
            got = read_lab(payload)
            runs.append(got)
            if args.runs > 1:
                print("  run %d: score %s, LCP %sms" % (n + 1, got.get("score", "-"),
                                                        got.get("lcp", "-")))
    metrics, spread = {}, {}
    for short in set().union(*(r.keys() for r in runs)):
        values = [r[short] for r in runs if short in r]
        metrics[short] = median(values)
        if len(values) > 1:
            spread[short] = [min(values), max(values)]
    record = {"tool": "lighthouse", "url": args.url, "strategy": args.strategy,
              "fetched": last.get("fetchTime", ""), "runs": len(runs), "spread": spread,
              "metrics": metrics, "field": {}}
    print("\n%s  %s  (local, %d run(s))" % (args.url, args.strategy, len(runs)))
    print("  score %s | FCP %sms | LCP %sms | TBT %sms | CLS %s | SI %sms"
          % (metrics.get("score", "-"), metrics.get("fcp", "-"), metrics.get("lcp", "-"),
             metrics.get("tbt", "-"), metrics.get("cls", "-"), metrics.get("si", "-")))
    chances = opportunities(last)
    if chances:
        print("\n  opportunities:")
        for saving, title, display in chances:
            print("    %-46s %s" % (title[:46], display))
    print("\n  This is the lab half. Nothing local knows what visitors experienced:\n"
          "  read the field record with psi.py field --url <the live url>.")
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + "\n")
        print("\nWritten to %s" % out)
        print("File it against the run:\n"
              "  perf-pass.py measure --file %s --label %s" % (out, args.label or "after"))
    return 0


def serve(args):
    directory = Path(args.dir).expanduser()
    if not directory.is_dir():
        print("%s is not a directory" % directory, file=sys.stderr)
        return 1
    print("Serve the build, then measure and capture against it:\n\n"
          "  npx --yes serve -s %s -l %d\n\n"
          "  lighthouse.py measure --url http://localhost:%d --out after.json --label after\n"
          "  node capture.js --url http://localhost:%d --out after-capture.json --built\n"
          % (directory, args.port, args.port, args.port))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--url", required=True)
    m.add_argument("--strategy", default="mobile", choices=["mobile", "desktop"])
    m.add_argument("--out")
    m.add_argument("--label", choices=["before", "after"])
    m.add_argument("--runs", type=int, default=3)
    m.set_defaults(fn=measure)
    s = sub.add_parser("serve")
    s.add_argument("--dir", default="dist")
    s.add_argument("--port", type=int, default=4173)
    s.set_defaults(fn=serve)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
