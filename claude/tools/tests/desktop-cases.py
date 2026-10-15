#!/usr/bin/env python3
"""Cases the desktop tool has to get right.

Nothing here posts an event or takes a screenshot, because these run on every
machine the setup syncs to, cloud containers included, and only a Mac has a
screen to drive. What they pin is the part that decides where a keystroke goes:
a combo resolves to the right key code and modifier flags, an unknown key or
modifier stops the run instead of sending something else, and nothing typed on
the command line is ever spliced into the script osascript runs.

  desktop-cases.py     run them
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "desktop.py")

passed = failed = 0


def check(what, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"pass  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}\n        got  {got!r}\n        want {want!r}")


def load():
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def refuses(fn, *args):
    try:
        fn(*args)
    except SystemExit:
        return True
    return False


def main():
    mod = load()
    cmd, shift, ctrl, alt = 1 << 20, 1 << 17, 1 << 18, 1 << 19

    check("combo: a bare key carries no flags",
          mod.parse_combo("return"), {"code": 36, "flags": 0})
    check("combo: cmd+shift+t",
          mod.parse_combo("cmd+shift+t"), {"code": 17, "flags": cmd | shift})
    check("combo: case does not matter",
          mod.parse_combo("CMD+A"), {"code": 0, "flags": cmd})
    check("combo: modifier aliases agree",
          mod.parse_combo("control+option+left"),
          mod.parse_combo("ctrl+alt+left"))
    check("combo: ctrl and alt land on their own bits",
          mod.parse_combo("ctrl+alt+down")["flags"], ctrl | alt)
    check("combo: an unknown key stops the run",
          refuses(mod.parse_combo, "cmd+nosuchkey"), True)
    check("combo: an unknown modifier stops the run",
          refuses(mod.parse_combo, "hyper+a"), True)

    check("mods: empty is no flags", mod.parse_mods(None), 0)
    check("mods: a list sums", mod.parse_mods("cmd,shift"), cmd | shift)

    check("keys: every letter has a code",
          [c for c in "abcdefghijklmnopqrstuvwxyz" if c not in mod.KEYCODES], [])
    check("keys: every digit has a code",
          [c for c in "0123456789" if c not in mod.KEYCODES], [])
    check("keys: no two names share a code unless they are aliases",
          sorted(n for n in mod.KEYCODES
                 if list(mod.KEYCODES.values()).count(mod.KEYCODES[n]) > 1),
          sorted(["return", "enter", "delete", "backspace", "escape", "esc"]))

    # Arguments reach osascript as one JSON argv, so a script that takes any
    # must read them there and nowhere else.
    for name, body in mod.SCRIPTS.items():
        if "a." in body:
            check(f"script {name}: reads its arguments from argv",
                  "JSON.parse(argv[0])" in body, True)
        check(f"script {name}: has a run entry point",
              "function run(argv)" in body, True)
    check("type: the text is an argv item, not source",
          "item 1 of argv" in mod.TYPE_SCRIPT, True)

    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
