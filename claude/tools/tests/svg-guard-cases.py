#!/usr/bin/env python3
"""Cases the SVG background gate has to get right.

Most of these are the false positives found when the gate was first run over
the portfolio, and each is here because it is shaped exactly like a background
without being one: a mask that decides what survives rather than covering
anything, a turbulence filter that throws the fill away, a six-percent wash, a
sixty-percent scrim, a QR quiet zone measured in a template literal. A gate
that refuses those gets switched off inside a day, so they are held as cases
rather than as a note.

The other half is the drawing itself. A root `fill` is inherited by the shapes
under it and never paints the canvas, which is why an icon set declaring
`fill="currentColor"` has to pass; and `viewBox` is camel case in SVG's own
vocabulary, so the JSX alias must not rewrite it or every canvas goes unread.

  svg-guard-cases.py     run them
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "svg-guard.py")
FAILURES = []


def load():
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


g = load()


def check(label, got, want):
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (label, got, want))


def kinds(text, suffix=".svg"):
    return [kind for _, kind, _, _, _ in g.background_findings(text, suffix)]


# -- what counts as a ground -------------------------------------------------

check("a full-canvas rect with a solid fill is one",
      kinds('<svg viewBox="0 0 40 20"><rect width="40" height="20" fill="#fff"/>'
            '<path d="M2 2h4"/></svg>'),
      ["full-canvas rect"])
check("a rect sized in percent is one",
      kinds('<svg><rect width="100%" height="100%" fill="#000"/></svg>'),
      ["full-canvas rect"])
check("a rect with no fill at all is one, since that paints it black",
      kinds('<svg viewBox="0 0 40 20"><rect width="40" height="20"/></svg>'),
      ["full-canvas rect"])
check("a fill named by a class in the file's own style block is one",
      kinds('<svg viewBox="0 0 40 20"><style>.g{fill:#07101c}</style>'
            '<rect class="g" width="40" height="20"/></svg>'),
      ["full-canvas rect"])
check("a ground inside the outermost group is still the ground",
      kinds('<svg viewBox="0 0 40 20"><g><rect width="40" height="20" '
            'fill="#101010"/></g></svg>'),
      ["full-canvas rect"])
check("a background declared on the root element is one",
      kinds('<svg viewBox="0 0 8 8" style="background:#fff"><path d="M0 0h2"/>'
            "</svg>"),
      ["style on the root svg"])
check("the canvas is read from width and height when there is no viewBox",
      kinds('<svg width="60" height="30"><rect width="60" height="30" '
            'fill="#222"/></svg>'),
      ["full-canvas rect"])

# -- what does not ------------------------------------------------------------

check("a root fill is inherited by the shapes, not painted on the canvas",
      kinds('<svg viewBox="0 0 24 24" fill="currentColor">'
            '<path d="M4 4h16"/></svg>'), [])
check("a black root fill is inherited the same way",
      kinds('<svg viewBox="0 0 24 72" fill="#000"><path d="M1 1h2"/></svg>'), [])
check("a rect inside a mask is not covering anything",
      kinds('<svg viewBox="0 0 10 10"><mask id="m"><rect width="10" '
            'height="10" fill="white"/></mask>'
            '<path d="M1 1h2" mask="url(#m)"/></svg>'), [])
check("a masked rect is placed by its mask rather than by its size",
      kinds('<svg viewBox="0 0 10 10"><rect width="100%" height="100%" '
            'fill="rgba(0,0,0,0.6)" mask="url(#m)"/></svg>'), [])
check("a filtered rect draws noise rather than its fill",
      kinds("<svg width='140' height='140'><filter id='n'><feTurbulence/>"
            "</filter><rect width='100%' height='100%' filter='url(#n)'/>"
            "</svg>"), [])
check("a wash over the drawing is a tint, not a ground",
      kinds('<svg viewBox="0 0 320 200"><rect width="320" height="200" '
            'fill="currentColor" fillOpacity={0.06} /></svg>', ".jsx"), [])
check("a scrim at six tenths is seen through",
      kinds('<svg viewBox="0 0 10 10"><rect width="10" height="10" '
            'fill="rgba(0,0,0,0.6)"/></svg>'), [])
check("three-component rgb has no alpha to read",
      kinds('<svg viewBox="0 0 10 10"><rect width="10" height="10" '
            'fill="rgb(10,20,0)"/></svg>'),
      ["full-canvas rect"])
check("a size written as a template hole cannot be shown to span the canvas",
      kinds('<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">'
            '<rect width="${W}" height="${H}" fill="url(#sky)"/></svg>', ".js"),
      [])
check("a fill taken from a custom property is the page's choice, not a slab",
      kinds('<svg viewBox="0 0 32 32"><rect width="32" height="32" rx="8" '
            'style="fill: var(--accent)" /></svg>', ".html"), [])
check("a rect that does not reach the edges is part of the drawing",
      kinds('<svg viewBox="0 0 40 20"><rect x="4" y="4" width="10" '
            'height="10" fill="#333"/></svg>'), [])
check("a stylesheet rule is only read in a stylesheet",
      kinds("svg { background: #fff; }", ".md"), [])
check("the same rule in a stylesheet is read",
      kinds("svg { background: #fff; }", ".css"),
      ["css painting behind svg"])

# -- repair -------------------------------------------------------------------

STRIPPED = g.repair('<svg viewBox="0 0 40 20"><rect width="40" height="20" '
                    'fill="#fff"/><path d="M2 2h4"/></svg>', ".svg")
check("repair takes the ground out", "<rect" in STRIPPED, False)
check("repair leaves the drawing", '<path d="M2 2h4"/>' in STRIPPED, True)
check("repair leaves a clean file alone",
      g.repair('<svg viewBox="0 0 4 4"><path d="M1 1h2"/></svg>', ".svg"),
      '<svg viewBox="0 0 4 4"><path d="M1 1h2"/></svg>')
check("a repaired file has nothing left to find", kinds(STRIPPED), [])

# -- the scene measure --------------------------------------------------------

SCENE = ('<svg viewBox="0 0 800 350">'
         + "".join('<rect x="%d" y="20" width="8" height="3"/>' % (i * 3)
                   for i in range(60))
         + "</svg>")
ICONS = ('<svg viewBox="0 0 24 24">'
         + "".join('<path d="M%d 2 C4 8 8 4 20 20"/>' % i for i in range(50))
         + "</svg>")
check("sixty boxes on one canvas is a scene", len(g.scene_findings(SCENE)), 1)
check("fifty curves is artwork", g.scene_findings(ICONS), [])
check("a handful of boxes is a mark",
      g.scene_findings('<svg viewBox="0 0 24 24"><rect x="1" y="1" width="4" '
                       'height="4"/><rect x="9" y="1" width="4" height="4"/>'
                       "</svg>"), [])


# -- the hooks ----------------------------------------------------------------

def hook(verb, payload, env=None):
    run = subprocess.run([sys.executable, str(TARGET), verb],
                         input=json.dumps(payload), capture_output=True,
                         text=True, env={**os.environ, **(env or {})})
    return run.returncode


def write(path, content):
    return {"tool_name": "Write",
            "tool_input": {"file_path": path, "content": content}}


with tempfile.TemporaryDirectory() as tmp:
    target = str(Path(tmp) / "mark.svg")
    ground = ('<svg viewBox="0 0 40 20"><rect width="40" height="20" '
              'fill="#0b1220"/><path d="M2 2h4"/></svg>')
    clean = '<svg viewBox="0 0 40 20"><path d="M2 2h4"/></svg>'

    check("the edit hook refuses a ground", hook("guard-edit",
                                                 write(target, ground)), 2)
    check("the edit hook passes a transparent graphic",
          hook("guard-edit", write(target, clean)), 0)
    check("the edit hook ignores a file that holds no graphic",
          hook("guard-edit", write(str(Path(tmp) / "a.ts"), "const a = 1")), 0)
    check("the named override lets a ground through",
          hook("guard-edit", write(target, ground),
               {"INTELLIGENCE_ALLOW_SVG_BACKGROUND": "1"}), 0)
    check("the edit hook refuses a scene",
          hook("guard-edit", write(str(Path(tmp) / "scene.svg"), SCENE)), 2)
    # iOS renders a transparent apple-touch-icon black, so the one file a
    # platform composites onto its own ground is carved out here and in
    # 10-universal.json, and the two readings have to stay the same.
    check("the icon iOS composites keeps its ground",
          hook("guard-edit", write(str(Path(tmp) / "apple-touch-icon.svg"),
                                   ground)), 0)


def git(root, *args):
    subprocess.run(["git"] + list(args), cwd=root, capture_output=True,
                   text=True, check=False)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "repo"
    root.mkdir()
    git(root, "init", "-q", ".")
    git(root, "config", "user.email", "t@t.t")
    git(root, "config", "user.name", "T")
    (root / "mark.svg").write_text('<svg viewBox="0 0 40 20">'
                                   '<path d="M2 2h4"/></svg>')
    git(root, "add", "-A")
    commit = {"tool_name": "Bash",
              "tool_input": {"command": "git commit -m x"}, "cwd": str(root)}
    check("a clean commit is not held", hook("guard-bash", commit), 0)

    (root / "mark.svg").write_text('<svg viewBox="0 0 40 20"><rect width="40" '
                                   'height="20" fill="#fff"/></svg>')
    git(root, "add", "-A")
    check("a commit staging a ground is refused", hook("guard-bash", commit), 2)
    check("a command that is not a commit is not this hook's business",
          hook("guard-bash", {"tool_name": "Bash",
                              "tool_input": {"command": "git status"},
                              "cwd": str(root)}), 0)


if FAILURES:
    for line in FAILURES:
        print("FAIL " + line)
    print("%d failing" % len(FAILURES))
    sys.exit(1)
print("all cases pass")
