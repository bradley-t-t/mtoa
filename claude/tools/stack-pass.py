#!/usr/bin/env python3
"""Runs the stack gate engine.

The engine sits in its own tree rather than inside the skill that describes it, so
that retiring the skill leaves the gates and the thing that runs them standing.
The checklist hook runs the engine directly; this is the same entry point
kept under tools/, and it holds no logic of its own so the two cannot drift.

An engine it cannot find exits 2. The hook blocks on 2 and only prints on
anything else, so a gate tool that is not there refuses the action rather than
letting it through with a warning.
"""

import os
import sys
from pathlib import Path

CANDIDATES = [
    Path.home() / ".claude" / "checklists" / "stack" / "bin" / "stack-pass.py",
    Path(__file__).resolve().parent.parent / "checklists" / "stack" / "bin" / "stack-pass.py",
]


def main():
    for path in CANDIDATES:
        if path.is_file():
            os.execv(sys.executable, [sys.executable, str(path), *sys.argv[1:]])
    print("stack-pass: the stack gate engine is not installed - looked for %s"
          % " and ".join(str(p) for p in CANDIDATES), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
