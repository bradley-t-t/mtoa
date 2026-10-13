#!/usr/bin/env python3
"""Move a project's version on the way that project does it.

A repo with its own release tool (scripts/calver.js, which writes package.json
and public/release.json in CalVer) gets that tool. A package.json without one
gets the CalVer calver_next derives; pom.xml and gradle.properties get a single
patch step, since neither carries a CalVer scheme. Either way, release metadata
that shadows the version (public/release.json) and the README version badge
are brought into step, so a bump never leaves the app reporting an older
number than the manifest.

A step name is accepted and checked so a typo is refused rather than treated as
a path, but it does not choose the version: every project versions in CalVer,
which is what the stack checklist's VER-01 holds them to.

    bump-version.py [patch|minor|major] [--root <path>]
"""

import datetime
import json
import os
import re
import subprocess
import sys

_ARGV = [a for a in sys.argv[1:]]

if any(a in ("--help", "-h", "help") for a in _ARGV):
    sys.stdout.write(__doc__)
    raise SystemExit(0)

_STEPS = ("patch", "minor", "major")


def _requested_root():
    """The tree whose manifest moves, and a refusal when it is not this one.

    An agent thread starts in the parent session's worktree, so the working
    directory is not evidence of which repo the caller means. A version bumped
    in a repo nobody is working on stays invisible until that repo's next
    release reports a number that skipped one.
    """
    root = None
    if "--root" in _ARGV:
        root = _ARGV[_ARGV.index("--root") + 1]
    here = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip()
    if not root:
        return here
    want = os.path.realpath(os.path.expanduser(root))
    if here and os.path.realpath(here) != want:
        sys.stderr.write(
            "REFUSED: --root names %s but this command is running in %s.\n"
            "cd into the repo whose version is moving.\n" % (want, here))
        raise SystemExit(2)
    return want


_positional = [a for a in _ARGV if not a.startswith("-")]
_positional = [a for a in _positional
               if not (("--root" in _ARGV) and a == _ARGV[_ARGV.index("--root") + 1])]
if _positional and _positional[0] not in _STEPS:
    sys.stderr.write("REFUSED: %r is not a step. Use patch, minor or major.\n" % _positional[0])
    raise SystemExit(2)

ROOT = _requested_root()
if ROOT:
    os.chdir(ROOT)
STEP = _positional[0] if _positional else "patch"
TOOLS = os.path.dirname(os.path.abspath(__file__))
CALVER = re.compile(r"^(20\d{2})\.(\d{1,2})\.(\d{1,3})(?:[-+].*)?$")


def calver_next(current):
    """The next CalVer for this week: YYYY.WW.PATCH, ISO week-numbering.

    Every project versions in CalVer, so a project with no release tool of its
    own gets the same shape scripts/calver.js writes rather than a semantic
    step. The patch counts releases inside one week and resets when the week
    rolls over, and the three fields stay ordered as a semantic version so npm
    and every range check keep working.
    """
    year, week, _ = datetime.date.today().isocalendar()
    match = CALVER.match(current or "")
    if match and (int(match.group(1)), int(match.group(2))) == (year, week):
        return "%d.%d.%d" % (year, week, int(match.group(3)) + 1)
    return "%d.%d.0" % (year, week)


def read_package_version():
    with open("package.json") as handle:
        return json.load(handle).get("version", "")


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit("%s failed: %s" % (" ".join(cmd), (result.stderr or result.stdout).strip()))


old = new = ""
if os.path.exists("package.json"):
    old = read_package_version()
    if os.path.exists(os.path.join("scripts", "calver.js")):
        run(["node", "scripts/calver.js"])
    else:
        run(["npm", "version", calver_next(old), "--no-git-tag-version", "--allow-same-version"])
    new = read_package_version()
elif os.path.exists("pom.xml"):
    text = open("pom.xml").read()
    match = re.search(r"<version>([^<]+)</version>", text)
    old = match.group(1)
    parts = old.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new = ".".join(parts)
    open("pom.xml", "w").write(text.replace("<version>%s</version>" % old, "<version>%s</version>" % new, 1))
elif os.path.exists("gradle.properties"):
    text = open("gradle.properties").read()
    match = re.search(r"^version\s*=\s*(\S+)", text, re.M)
    old = match.group(1)
    parts = old.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new = ".".join(parts)
    open("gradle.properties", "w").write(text.replace(match.group(0), "version=%s" % new, 1))
else:
    print("no version manifest here; nothing to bump")
    sys.exit(0)

release = os.path.join("public", "release.json")
if os.path.exists(release) and os.path.exists("package.json"):
    with open(release) as handle:
        manifest = json.load(handle)
    if manifest.get("version") != new:
        manifest["version"] = new
        with open(release, "w") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")

badge_tool = os.path.join(TOOLS, "version-badge.py")
if os.path.exists(badge_tool):
    subprocess.run([sys.executable, badge_tool], capture_output=True, text=True)

print("%s -> %s" % (old, new))
