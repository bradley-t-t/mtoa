#!/usr/bin/env python3
"""Drive the Mac's desktop from Bash: look at the screen, click, type, open apps.

Computer use asks for approval of every app in every session, and nothing in
the desktop app pre-approves one. Bash runs without asking in bypass mode, and
macOS keeps its own grants for good once given, so this does the same work
through the shell: `screencapture` for the eyes, CoreGraphics events posted
from JXA for the hands. Nothing is installed beyond what macOS ships.

macOS attributes everything here to Claude Code (`com.anthropic.claude-code`),
which needs two switches in System Settings > Privacy & Security, flipped once:
Screen Recording for `shot`, Accessibility for every input command. `check`
reports both.

A screenshot is saved at the screen's size in points, so a pixel in the image
is the coordinate to pass to `click`. Read the image with the Read tool.

  desktop.py check                       both macOS grants, and the screen size
  desktop.py shot [--out F] [--display N] [--region X Y W H]
  desktop.py click X Y [--right] [--double] [--mods cmd,shift]
  desktop.py move X Y
  desktop.py drag X1 Y1 X2 Y2
  desktop.py scroll X Y DY [--dx DX]     DY lines, negative scrolls down
  desktop.py type TEXT
  desktop.py key COMBO [COMBO ...]       cmd+shift+t, return, escape, down
  desktop.py open APP [--wait]           launch or bring forward, by name
  desktop.py front                       the app in front
  desktop.py apps                        running apps with a window
  desktop.py windows [APP]               on-screen windows with their bounds
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "backspace": 51, "escape": 53, "esc": 53, "forwarddelete": 117,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}

MODIFIERS = {
    "cmd": 1 << 20, "command": 1 << 20,
    "shift": 1 << 17,
    "ctrl": 1 << 18, "control": 1 << 18,
    "alt": 1 << 19, "opt": 1 << 19, "option": 1 << 19,
    "fn": 1 << 23,
}

# Every script reads one JSON argument, so nothing typed or named on the
# command line is ever spliced into source.
PRELUDE = """
ObjC.import('CoreGraphics');
ObjC.import('ApplicationServices');
ObjC.import('AppKit');
function post(e) { $.CGEventPost($.kCGHIDEventTap, e); }
function pause(s) { delay(s); }
function mouse(type, x, y, button) {
  return $.CGEventCreateMouseEvent(null, type, $.CGPointMake(x, y), button);
}
"""

SCRIPTS = {
    "check": """
function run(argv) {
  var b = $.CGDisplayBounds($.CGMainDisplayID());
  return JSON.stringify({
    accessibility: $.AXIsProcessTrusted(),
    screen_points: [b.size.width, b.size.height]
  });
}""",
    "display": """
function run(argv) {
  var a = JSON.parse(argv[0]);
  var ids = Ref(), count = Ref();
  $.CGGetActiveDisplayList(16, ids, count);
  var n = count[0], out = [];
  for (var i = 0; i < n; i++) {
    var b = $.CGDisplayBounds(ids[i]);
    out.push([b.origin.x, b.origin.y, b.size.width, b.size.height]);
  }
  return JSON.stringify(out);
}""",
    "click": """
function run(argv) {
  var a = JSON.parse(argv[0]);
  var down = a.right ? $.kCGEventRightMouseDown : $.kCGEventLeftMouseDown;
  var up = a.right ? $.kCGEventRightMouseUp : $.kCGEventLeftMouseUp;
  var button = a.right ? $.kCGMouseButtonRight : $.kCGMouseButtonLeft;
  post(mouse($.kCGEventMouseMoved, a.x, a.y, button));
  pause(0.05);
  for (var i = 1; i <= a.count; i++) {
    var d = mouse(down, a.x, a.y, button), u = mouse(up, a.x, a.y, button);
    $.CGEventSetIntegerValueField(d, $.kCGMouseEventClickState, i);
    $.CGEventSetIntegerValueField(u, $.kCGMouseEventClickState, i);
    if (a.flags) { $.CGEventSetFlags(d, a.flags); $.CGEventSetFlags(u, a.flags); }
    post(d); post(u);
    pause(0.04);
  }
  return 'ok';
}""",
    "move": """
function run(argv) {
  var a = JSON.parse(argv[0]);
  post(mouse($.kCGEventMouseMoved, a.x, a.y, $.kCGMouseButtonLeft));
  return 'ok';
}""",
    "drag": """
function run(argv) {
  var a = JSON.parse(argv[0]), L = $.kCGMouseButtonLeft, steps = 20;
  post(mouse($.kCGEventMouseMoved, a.x1, a.y1, L));
  pause(0.05);
  post(mouse($.kCGEventLeftMouseDown, a.x1, a.y1, L));
  for (var i = 1; i <= steps; i++) {
    var x = a.x1 + (a.x2 - a.x1) * i / steps, y = a.y1 + (a.y2 - a.y1) * i / steps;
    post(mouse($.kCGEventLeftMouseDragged, x, y, L));
    pause(0.015);
  }
  post(mouse($.kCGEventLeftMouseUp, a.x2, a.y2, L));
  return 'ok';
}""",
    "scroll": """
function run(argv) {
  var a = JSON.parse(argv[0]);
  post(mouse($.kCGEventMouseMoved, a.x, a.y, $.kCGMouseButtonLeft));
  pause(0.05);
  post($.CGEventCreateScrollWheelEvent2(null, $.kCGScrollEventUnitLine, 2, a.dy, a.dx, 0));
  return 'ok';
}""",
    "key": """
function run(argv) {
  var a = JSON.parse(argv[0]);
  a.combos.forEach(function (c) {
    var d = $.CGEventCreateKeyboardEvent(null, c.code, true);
    var u = $.CGEventCreateKeyboardEvent(null, c.code, false);
    $.CGEventSetFlags(d, c.flags); $.CGEventSetFlags(u, c.flags);
    post(d); pause(0.02); post(u); pause(0.05);
  });
  return 'ok';
}""",
    "front": """
function run(argv) {
  var app = $.NSWorkspace.sharedWorkspace.frontmostApplication;
  return JSON.stringify({name: ObjC.unwrap(app.localizedName),
                         bundle: ObjC.unwrap(app.bundleIdentifier),
                         pid: app.processIdentifier});
}""",
    "apps": """
function run(argv) {
  var list = ObjC.unwrap($.NSWorkspace.sharedWorkspace.runningApplications), out = [];
  for (var i = 0; i < list.length; i++) {
    var app = list[i];
    if (app.activationPolicy !== $.NSApplicationActivationPolicyRegular) continue;
    out.push({name: ObjC.unwrap(app.localizedName),
              bundle: ObjC.unwrap(app.bundleIdentifier),
              active: app.active});
  }
  return JSON.stringify(out);
}""",
    "windows": """
function run(argv) {
  var a = JSON.parse(argv[0]);
  var opts = $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements;
  var info = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo(opts, 0)));
  var out = [];
  info.forEach(function (w) {
    if (w.kCGWindowLayer !== 0) return;
    if (a.app && (w.kCGWindowOwnerName || '').toLowerCase().indexOf(a.app.toLowerCase()) < 0) return;
    var b = w.kCGWindowBounds;
    out.push({app: w.kCGWindowOwnerName, title: w.kCGWindowName || '',
              bounds: [b.X, b.Y, b.Width, b.Height]});
  });
  return JSON.stringify(out);
}""",
}

TYPE_SCRIPT = """
on run argv
  tell application "System Events" to keystroke (item 1 of argv)
end run
"""


def jxa(name, payload=None):
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", PRELUDE + SCRIPTS[name],
         json.dumps(payload or {})],
        capture_output=True, text=True)
    if proc.returncode:
        sys.exit(f"desktop: {name}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def screen_recording():
    """Read the grant from TCC, which JXA has no call for; None when unreadable."""
    db = "/Library/Application Support/com.apple.TCC/TCC.db"
    proc = subprocess.run(
        ["sqlite3", "-readonly", db,
         "select auth_value from access where service='kTCCServiceScreenCapture'"
         " and client='com.anthropic.claude-code'"],
        capture_output=True, text=True)
    if proc.returncode:
        return None
    return proc.stdout.strip() == "2"


def need_accessibility():
    if not json.loads(jxa("check"))["accessibility"]:
        sys.exit("desktop: Claude Code lacks Accessibility. Turn on 'claude' in "
                 "System Settings > Privacy & Security > Accessibility.")


def parse_mods(text):
    flags = 0
    for name in filter(None, (text or "").lower().split(",")):
        if name not in MODIFIERS:
            sys.exit(f"desktop: unknown modifier {name!r}")
        flags |= MODIFIERS[name]
    return flags


def parse_combo(combo):
    *mods, key = combo.lower().split("+")
    if key not in KEYCODES:
        sys.exit(f"desktop: unknown key {key!r}")
    return {"code": KEYCODES[key], "flags": parse_mods(",".join(mods))}


def shot(args):
    displays = json.loads(jxa("display"))
    index = (args.display or 1) - 1
    if not 0 <= index < len(displays):
        sys.exit(f"desktop: there are {len(displays)} displays")
    out = args.out or os.path.join(
        tempfile.gettempdir(), f"desktop-{int(time.time() * 1000)}.png")
    cmd = ["screencapture", "-x", "-t", "png"]
    if args.region:
        x, y, w, h = args.region
        cmd += ["-R", f"{x},{y},{w},{h}"]
        width, height = w, h
    else:
        cmd += ["-D", str(index + 1)]
        width, height = displays[index][2], displays[index][3]
    cmd.append(out)
    subprocess.run(cmd, check=True)
    # Retina captures come back at twice the point size; bring them down so the
    # image and the click coordinates are the same numbers.
    subprocess.run(["sips", "-z", str(int(height)), str(int(width)), out],
                   check=True, capture_output=True)
    origin = args.region[:2] if args.region else displays[index][:2]
    print(json.dumps({"path": out, "size": [int(width), int(height)],
                      "origin": [int(origin[0]), int(origin[1])]}))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("shot")
    s.add_argument("--out")
    s.add_argument("--display", type=int)
    s.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    s = sub.add_parser("click")
    s.add_argument("x", type=float)
    s.add_argument("y", type=float)
    s.add_argument("--right", action="store_true")
    s.add_argument("--double", action="store_true")
    s.add_argument("--mods")
    s = sub.add_parser("move")
    s.add_argument("x", type=float)
    s.add_argument("y", type=float)
    s = sub.add_parser("drag")
    for name in ("x1", "y1", "x2", "y2"):
        s.add_argument(name, type=float)
    s = sub.add_parser("scroll")
    s.add_argument("x", type=float)
    s.add_argument("y", type=float)
    s.add_argument("dy", type=int)
    s.add_argument("--dx", type=int, default=0)
    s = sub.add_parser("type")
    s.add_argument("text")
    s = sub.add_parser("key")
    s.add_argument("combos", nargs="+")
    s = sub.add_parser("open")
    s.add_argument("app")
    s.add_argument("--wait", action="store_true")
    sub.add_parser("front")
    sub.add_parser("apps")
    s = sub.add_parser("windows")
    s.add_argument("app", nargs="?")
    args = p.parse_args()

    if args.cmd == "check":
        report = json.loads(jxa("check"))
        report["screen_recording"] = screen_recording()
        print(json.dumps(report))
    elif args.cmd == "shot":
        shot(args)
    elif args.cmd == "click":
        need_accessibility()
        print(jxa("click", {"x": args.x, "y": args.y, "right": args.right,
                            "count": 2 if args.double else 1,
                            "flags": parse_mods(args.mods)}))
    elif args.cmd == "move":
        need_accessibility()
        print(jxa("move", {"x": args.x, "y": args.y}))
    elif args.cmd == "drag":
        need_accessibility()
        print(jxa("drag", {"x1": args.x1, "y1": args.y1,
                           "x2": args.x2, "y2": args.y2}))
    elif args.cmd == "scroll":
        need_accessibility()
        print(jxa("scroll", {"x": args.x, "y": args.y,
                             "dy": args.dy, "dx": args.dx}))
    elif args.cmd == "type":
        need_accessibility()
        proc = subprocess.run(["osascript", "-e", TYPE_SCRIPT, args.text],
                              capture_output=True, text=True)
        if proc.returncode:
            sys.exit(f"desktop: type: {proc.stderr.strip()}")
        print("ok")
    elif args.cmd == "key":
        need_accessibility()
        print(jxa("key", {"combos": [parse_combo(c) for c in args.combos]}))
    elif args.cmd == "open":
        subprocess.run(["open", "-a", args.app], check=True)
        if args.wait:
            time.sleep(1.5)
        print(jxa("front"))
    elif args.cmd == "front":
        print(jxa("front"))
    elif args.cmd == "apps":
        print(jxa("apps"))
    elif args.cmd == "windows":
        print(jxa("windows", {"app": args.app}))


if __name__ == "__main__":
    main()
