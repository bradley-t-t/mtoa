#!/usr/bin/env python3
"""Bring the README version badge into step with package.json.

bump-version.py moves package.json and public/release.json and then calls
this, so the badge a reader sees on the repository page says the version the
manifest says. Run from the project root, or name it with --root.

shields.io spells a dash in a version as a double dash, so 2026.35.27-1 is
written into the badge as 2026.35.27--1. The alt text keeps the plain form,
which is what the release gate in ci.yml reads back.

    version-badge.py [--root <path>]
"""

import json
import os
import re
import sys

args = sys.argv[1:]
if any(a in ("--help", "-h", "help") for a in args):
    sys.stdout.write(__doc__)
    raise SystemExit(0)

root = os.getcwd()
if "--root" in args:
    root = args[args.index("--root") + 1]

manifest = os.path.join(root, "package.json")
readme = os.path.join(root, "README.md")
if not (os.path.exists(manifest) and os.path.exists(readme)):
    raise SystemExit(0)

with open(manifest) as handle:
    version = json.load(handle).get("version")
if not version:
    raise SystemExit(0)

with open(readme) as handle:
    text = handle.read()

shield = version.replace("-", "--")
# The colour ends the badge's path segment and is what tells the version apart
# from it, since both sit between dashes. shields.io takes either a hex code or
# one of its named colours, and the badge may be closed by a query string, by
# the end of a markdown link, or by nothing at all, so none of those may be the
# thing the match depends on. Anchoring on hex-followed-by-a-query alone left
# every plain markdown badge silently unmoved: the run reported the bump, the
# README went on showing the old number, and nothing failed.
moved = re.sub(
    r"(img\.shields\.io/badge/version-)[0-9][0-9A-Za-z.+-]*?"
    r"(-(?:[0-9A-Fa-f]{3,8}|[A-Za-z]+)(?=[?\"&)\s]|$))",
    lambda m: m.group(1) + shield + m.group(2),
    text,
)
moved = re.sub(r'alt="Version [^"]*"', 'alt="Version %s"' % version, moved)

if moved == text:
    print("README badge already %s" % version)
    raise SystemExit(0)

with open(readme, "w") as handle:
    handle.write(moved)
print("README badge -> %s" % version)
