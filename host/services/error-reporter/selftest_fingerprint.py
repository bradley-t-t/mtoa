#!/usr/bin/env python3
"""Hold `stable()` to the two things it has to get right at once.

The ticket fingerprint decides whether a defect has been seen before, so it must
read two builds of one file as one fault and two different files as two. Those
pull in opposite directions and the whole rule is one regex, which makes it very
easy to fix one direction and quietly break the other: a rule loose enough to
merge `index-BDgwk8cx.js` with `index-CykicU4n.js` is one character away from
merging the two photographs this queue carried as #500 and #501.

So the corpus below is written as pairs, and both halves of it are the test.
MERGES says these are one fault and the fingerprint must agree. APART says these
are different files and it must not. A change that satisfies one list and not the
other is not an improvement.

    python3 selftest_fingerprint.py

Loads the regexes out of `error_reporter.py` itself rather than importing it,
because importing it reads a config file and starts a server.
"""

import re
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "error_reporter.py"


def load():
    """The real `stable()` and its regexes, lifted out of the shipped file."""
    src = SOURCE.read_text()
    fragment = src[src.index("HASHED_ASSET = re.compile("):src.index("def short_url(")]
    namespace = {"re": re}
    exec(compile(fragment, str(SOURCE), "exec"), namespace)
    return namespace["stable"]


# Two builds of one file. Each row is one fault, and every name in it has to
# reach the same fingerprint.
MERGES = [
    ("a bundled entry point", [
        "index-BDgwk8cx.js", "index-CykicU4n.js", "index-B3snsEq5.js"]),
    ("a lazily imported chunk", [
        "LiveChat-C8ELiptI.js", "LiveChat-D1cAr9nP.js", "LiveChat-BQm2wXvA.js"]),
    ("a stylesheet", [
        "site-BXYKdx0S.css", "site-CH6ATeK4.css"]),
    ("a hashed video", [
        "1-Ck9dRt2W.mp4", "1-DpQ8vN3x.mp4"]),
    ("a hashed font", [
        "big-shoulders-display-var-CH3aOuT8.woff2",
        "big-shoulders-display-var-BpQ2xL9m.woff2"]),
    ("the same chunk behind the retry the sites add to recover it", [
        "index-BDgwk8cx.js", "index-BDgwk8cx.js?retry=2", "index-BDgwk8cx.js?retry=3"]),
    ("one address carrying a build id", [
        "/api/version?build=a1b2c3d4e5f60718", "/api/version?build=99887766554433221"]),
]

# Different files. No two names in a row may reach the same fingerprint.
APART = [
    ("two icons whose names differ by one letter", [
        "rotate-ccw-BioUyuA_.js", "rotate-cw-lGzZCjY8.js"]),
    ("two photographs a person named", [
        "studio-step-up-dumbbell.webp", "studio-step-up-portrait.webp"]),
    ("a logo and an icon", [
        "clientone-logo.webp", "logo-192.png"]),
    ("two variable fonts from one family", [
        "source-sans-3-latin-variable.woff2", "source-serif-4-latin-variable.woff2"]),
    ("two chunks from one site", [
        "LiveChat-C8ELiptI.js", "SiteSearch-DiiX0Ab2.js"]),
    ("two different endpoints", [
        "/api/analytics", "/api/calls-desk"]),
]

# Names a person wrote. A rebuild does not touch them, so neither does this.
UNTOUCHED = [
    "studio-step-up-dumbbell.webp",
    "coach-portrait-544.webp",
    "logo-mark-148.png",
    "archivo-latin-variable.woff2",
    "clientnine-com-desktop.webp",
    "hero.mp4",
]


def main():
    stable = load()
    failures = []

    def check(ok, line):
        print("  %-4s %s" % ("pass" if ok else "FAIL", line))
        if not ok:
            failures.append(line)

    print("one fault, however many builds:")
    for label, names in MERGES:
        prints = {stable(n) for n in names}
        check(len(prints) == 1, "%s -> %s" % (label, " / ".join(sorted(prints))))

    print()
    print("different files, still different:")
    for label, names in APART:
        prints = [stable(n) for n in names]
        check(len(set(prints)) == len(prints), "%s -> %s" % (label, " vs ".join(prints)))

    print()
    print("a name a person wrote survives intact:")
    for name in UNTOUCHED:
        check(stable(name) == name, "%s -> %s" % (name, stable(name)))

    print()
    print("the rule refuses its own output:")
    # The sentinel is mixed case, so without a guard the name segment ahead of it
    # pairs with it and a second pass reads the two as a fresh hash. Nothing calls
    # `stable()` twice today; a rule whose output defeats it is one refactor from
    # merging two different files.
    for name in [n for _, names in MERGES for n in names] + [n for _, names in APART for n in names]:
        once = stable(name)
        check(stable(once) == once, "%s -> %s -> %s" % (name, once, stable(once)))

    print()
    if failures:
        print("%d failure(s)" % len(failures))
        return 1
    print("all clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
