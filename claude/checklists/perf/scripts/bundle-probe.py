#!/usr/bin/env python3
"""Reads what a build actually ships, and where its weight comes from.

A bundler reports what it emitted; the question a performance run asks is which of
those bytes the first screen needed. This answers that from the build output and the
source together: which chunks the entry document pulls, what is inside the biggest
one, which dependencies are represented twice, and which are large enough that
loading them before anything needs them is the whole cost.

    bundle-probe.py weigh   --root <path>     every asset, by kind, raw and compressed
    bundle-probe.py chunks  --root <path>     the chunk graph the entry document pulls
    bundle-probe.py deps    --root <path>     what is in the manifest and what imports it
    bundle-probe.py budget  --root <path> [--write]   record the current weights as a ceiling
"""

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

BUILD_DIRS = ["dist", "build", "out", ".next/static", ".output/public", ".svelte-kit/output"]
SKIP = {"node_modules", ".git", "__pycache__", ".venv"}

# Packages built for a server. One imported from browser source is weight the
# visitor pays for and an interface that expects a secret in the environment.
SERVER_ONLY = {"openai", "stripe", "nodemailer", "@prisma/client", "mongoose", "pg",
               "aws-sdk", "googleapis", "twilio", "resend", "bcrypt", "jsonwebtoken", "sharp"}

# Library families that solve the same problem. A manifest carrying two of one
# family pays for both and uses one.
EQUIVALENT = [
    ("animation", {"framer-motion", "motion", "gsap", "animejs", "react-spring",
                   "@react-spring/web", "aos", "react-awesome-reveal"}),
    ("dates", {"moment", "dayjs", "date-fns", "luxon"}),
    ("http", {"axios", "superagent", "got", "ky", "node-fetch"}),
    ("3d", {"three", "ogl", "babylonjs", "pixi.js", "@react-three/fiber"}),
    ("utility", {"lodash", "lodash-es", "underscore", "ramda"}),
    ("charts", {"chart.js", "recharts", "victory", "echarts", "d3", "apexcharts"}),
    ("carousel", {"swiper", "slick-carousel", "embla-carousel-react", "keen-slider"}),
]


def kb(n):
    return "%.1fKB" % (n / 1024.0)


def find_build(root):
    """The build directory with the newest file, so a stale dist beside a fresh out never wins."""
    best, when = None, 0
    for name in BUILD_DIRS:
        candidate = root / name
        if not candidate.is_dir():
            continue
        files = [f for f in candidate.rglob("*") if f.is_file()]
        if not files:
            continue
        newest = max(f.stat().st_mtime for f in files)
        if newest > when:
            best, when = candidate, newest
    return best


def kind_of(path):
    suffix = path.suffix.lower()
    if suffix in (".js", ".mjs"):
        return "js"
    if suffix == ".css":
        return "css"
    if suffix in (".html", ".htm"):
        return "html"
    if suffix in (".woff", ".woff2", ".ttf", ".otf"):
        return "font"
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg"):
        return "image"
    return "other"


def compressed(path):
    try:
        return len(gzip.compress(path.read_bytes(), 6))
    except (OSError, ValueError):
        return 0


def assets(build):
    out = []
    for f in sorted(build.rglob("*")):
        if f.is_file():
            out.append((f, kind_of(f), f.stat().st_size))
    return out


def entry_document(build):
    for f in build.rglob("index.html"):
        return f
    for f in build.rglob("*.html"):
        return f
    return None


def weigh(args):
    root = Path(args.root).expanduser().resolve()
    build = find_build(root)
    if not build:
        print("No build output under %s. Run the project's build first." % root, file=sys.stderr)
        return 1
    rows = {}
    for path, kind, size in assets(build):
        rows.setdefault(kind, []).append((path, size))
    print("Build at %s\n" % build)
    total = 0
    for kind in ("js", "css", "html", "font", "image", "other"):
        items = sorted(rows.get(kind, []), key=lambda pair: -pair[1])
        if not items:
            continue
        raw = sum(size for _, size in items)
        total += raw
        line = "%-6s %3d file(s) %11s raw" % (kind, len(items), kb(raw))
        if kind in ("js", "css", "html"):
            line += "  %11s compressed" % kb(sum(compressed(p) for p, _ in items))
        print(line)
        for path, size in items[:6]:
            print("       %-48s %10s" % (str(path.relative_to(build))[:48], kb(size)))
    print("\ntotal %s raw" % kb(total))
    return 0


def chunks(args):
    root = Path(args.root).expanduser().resolve()
    build = find_build(root)
    if not build:
        print("No build output under %s." % root, file=sys.stderr)
        return 1
    document = entry_document(build)
    if not document:
        print("No entry document in %s." % build, file=sys.stderr)
        return 1
    html = document.read_text(errors="replace")
    referenced = set(re.findall(r"[\"'/]([\w.-]+\.(?:js|css))[\"']", html))
    js = [(p, s) for p, k, s in assets(build) if k == "js"]
    first = [(p, s) for p, s in js if p.name in referenced]
    rest = [(p, s) for p, s in js if p.name not in referenced]
    print("Entry document: %s\n" % document.relative_to(build))
    print("Pulled by the document (%d):" % len(first))
    for path, size in sorted(first, key=lambda pair: -pair[1]):
        print("  %-48s %10s raw %10s compressed"
              % (path.name[:48], kb(size), kb(compressed(path))))
    print("\n  first load: %s compressed" % kb(sum(compressed(p) for p, _ in first)))
    if rest:
        print("\nLoaded later, on demand (%d):" % len(rest))
        for path, size in sorted(rest, key=lambda pair: -pair[1])[:12]:
            print("  %-48s %10s raw" % (path.name[:48], kb(size)))
    else:
        print("\nNothing is loaded on demand: every route's code is in the first load.")
    preloads = len(re.findall(r"rel=[\"']modulepreload", html, re.I))
    print("\n%d modulepreload link(s) in the document." % preloads)
    return 0


def deps(args):
    root = Path(args.root).expanduser().resolve()
    try:
        package = json.loads((root / "package.json").read_text())
    except (OSError, ValueError):
        print("No package.json under %s." % root, file=sys.stderr)
        return 1
    declared = dict(package.get("dependencies") or {})
    sources = {}
    for f in root.rglob("*"):
        if not f.is_file() or any(part in SKIP for part in f.parts):
            continue
        if f.suffix.lower() in (".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"):
            try:
                sources[f] = f.read_text(errors="replace")
            except OSError:
                continue
    used = {}
    for path, text in sources.items():
        for spec in re.findall(r"from\s*[\"']([^\"'.][^\"']*)[\"']|import\s*\(\s*[\"']([^\"'.][^\"']*)", text):
            name = spec[0] or spec[1]
            if not name:
                continue
            pkg = "/".join(name.split("/")[:2]) if name.startswith("@") else name.split("/")[0]
            used.setdefault(pkg, set()).add(path.name)
    print("%d dependency(ies) declared.\n" % len(declared))
    unused = sorted(set(declared) - set(used))
    if unused:
        print("Declared and never imported (%d): %s\n" % (len(unused), ", ".join(unused)))
    server = sorted(set(used) & SERVER_ONLY)
    if server:
        print("Server-only packages imported from source (%d):" % len(server))
        for pkg in server:
            print("  %-28s %s" % (pkg, ", ".join(sorted(used[pkg])[:4])))
        print()
    for label, family in EQUIVALENT:
        present = sorted(set(declared) & family)
        if len(present) > 1:
            print("Two or more %s libraries: %s" % (label, ", ".join(present)))
            for pkg in present:
                print("  %-28s imported by %s" % (pkg, ", ".join(sorted(used.get(pkg, [])) [:4]) or "nothing"))
    return 0


def budget(args):
    root = Path(args.root).expanduser().resolve()
    build = find_build(root)
    if not build:
        print("No build output under %s." % root, file=sys.stderr)
        return 1
    js = [(p, s) for p, k, s in assets(build) if k == "js"]
    css = [(p, s) for p, k, s in assets(build) if k == "css"]
    images = [(p, s) for p, k, s in assets(build) if k == "image"]
    record = {
        "javascript_kb": round(sum(compressed(p) for p, _ in js) / 1024, 1),
        "css_kb": round(sum(compressed(p) for p, _ in css) / 1024, 1),
        "images_kb": round(sum(s for _, s in images) / 1024, 1),
        "chunks": len(js),
    }
    for key, value in record.items():
        print("  %-14s %s" % (key, value))
    out = root / "performance-budget.json"
    if args.write:
        existing = {}
        if out.is_file():
            try:
                existing = json.loads(out.read_text())
            except ValueError:
                existing = {}
        # A ceiling that rises whenever it is re-recorded is not a ceiling. The lower
        # of the two is kept, so recording after an improvement tightens it and
        # recording after a regression leaves the old limit standing.
        merged = dict(record)
        for key, value in record.items():
            if isinstance(existing.get(key), (int, float)):
                merged[key] = min(value, existing[key])
        out.write_text(json.dumps(merged, indent=2) + "\n")
        print("\nRecorded in %s" % out)
        tightened = [k for k in record if merged.get(k) != record.get(k)]
        if tightened:
            print("Kept the existing lower ceiling for: %s" % ", ".join(tightened))
    else:
        print("\nPass --write to record these as the ceiling in %s" % out)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("weigh", weigh), ("chunks", chunks), ("deps", deps), ("budget", budget)):
        p = sub.add_parser(name)
        p.add_argument("--root", default=".")
        if name == "budget":
            p.add_argument("--write", action="store_true")
        p.set_defaults(fn=fn)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
