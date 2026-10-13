#!/usr/bin/env python3
"""Keeps every SVG transparent behind its drawing, everywhere, without asking.

An SVG that paints its own backdrop is not a graphic, it is a graphic glued to a
tile. It looks right in the one place it was drawn and wrong in every other: a
white card on a dark section, a navy slab where the page ends, a rectangle whose
edges the section divider cuts through. The failure is invisible to whoever
authored it, because they were looking at the one page where the tile happened
to match, and it stays in the tree until somebody reloads the site on a
different background.

So this does not report. A background rect is removable without judgment -- it
is the first drawn element, it spans the canvas, and nothing under it is drawn
against it -- which makes automatic repair the honest form. `fix` strips them in
place across a whole tree. The two hooks exist for the case repair cannot reach:
an edit about to introduce one, and a commit carrying one that was written
before the tool existed.

The second rule is the one the first cannot express. A backdrop is a mistake in
a drawing that is otherwise fine; a marine terminal assembled out of forty grey
boxes is a drawing that was never going to come out right, and stripping its
background only makes the wrongness transparent. What separates the two is
measurable: exported artwork is curves, and a scene a model talked itself into
is axis-aligned rectangles and line segments in bulk. `guard-edit` refuses a new
SVG shaped like the second, and leaves every drawing already in the tree alone,
since the ones that shipped are not this gate's business.

  scan [path]     every SVG under a tree that paints its own background
  fix [path]      strip those backgrounds in place, and say what moved
  check           scan the working tree, non-zero when anything paints one
  guard-edit      PreToolUse(Write|Edit): refuse an edit that paints one
  guard-bash      PreToolUse(Bash): refuse a commit whose staged SVGs paint one

INTELLIGENCE_ALLOW_SVG_BACKGROUND=1 covers a graphic whose backdrop is the
drawing -- a printed sheet, a social card sized to a platform's frame. It is an
override for a person who has looked at the file, not a way past a refusal.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Extensions whose text can carry SVG markup. CSS is here for the data URI and
# for a rule painting behind an `svg` selector, which reads as a background to
# every eye that meets it and to none of the markup checks.
MARKUP = (".svg", ".html", ".htm", ".jsx", ".tsx", ".js", ".ts", ".mjs",
          ".astro", ".vue", ".svelte", ".md", ".mdx", ".css", ".scss")

# A vendored copy of the config tree is not the project's own graphics, and its
# reference material quotes markup it never renders.
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".vercel",
             ".turbo", "coverage", ".cache", "out", "vendor", ".intelligence"}

# Stylesheets are the only place the CSS test runs. The same pattern loosed on
# prose matches a fenced example and a paragraph three screens later, which is
# how a guard earns a reputation for crying wolf.
STYLESHEET = (".css", ".scss")

# The one icon a platform composites onto a ground of its own. iOS renders a
# transparent apple-touch-icon black, so stripping this one breaks the thing the
# rule exists to protect. Gate UNI in 10-universal.json carves out the same file
# for the same reason, and the two must not disagree.
COMPOSITED = re.compile(r"apple-touch-icon", re.I)

SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.I)
ATTR = re.compile(r"""([:@\w.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|\{([^}]*)\})""")
ELEMENT = re.compile(r"<\s*(/?)\s*([a-zA-Z][\w:-]*)([^>]*?)(/?)\s*>")
DRAWN = {"rect", "circle", "ellipse", "line", "polyline", "polygon", "path",
         "text", "image", "use", "foreignObject"}
BOXY = {"rect", "line", "polyline", "polygon"}
TRANSPARENT = {"none", "transparent", "", "rgba(0,0,0,0)"}
# Elements that precede the drawing without being part of it, so a background
# rect is still the first thing drawn when it sits behind them.
PRELUDE = {"defs", "title", "desc", "metadata", "style", "symbol", "g",
           "clippath", "mask", "lineargradient", "radialgradient", "filter"}

CURVE = re.compile(r"[CcSsQqTtAa]")
# The fourth component of an rgba(), or the one after the slash in the modern
# spelling. Three-component rgb() has no alpha and must not match, or every
# black fill reads as fully transparent.
ALPHA = re.compile(
    r"rgba\(\s*[\d.%]+\s*,\s*[\d.%]+\s*,\s*[\d.%]+\s*,\s*([\d.]+%?)\s*\)"
    r"|rgba?\([^)/]*/\s*([\d.]+%?)\s*\)", re.I)
CSS_SVG_BACKGROUND = re.compile(
    r"(?<![\w.#-])svg(?![\w-])[^{};]{0,120}\{[^{}]{0,400}?"
    r"\bbackground(?:-color)?\s*:\s*([^;{}]+)", re.I)

# A drawing this many primitives deep, mostly boxes and straight lines, is a
# scene rather than a mark. The number is where an icon set's most detailed
# glyph stops and an assembled illustration starts; below it the ratio test
# alone would refuse ordinary geometric logos.
SCENE_ELEMENTS = 40
SCENE_BOXY_SHARE = 0.6


def attrs_of(tag_text):
    """Attributes of one start tag, lowercased by name.

    JSX spellings collapse to the same names as XML ones: a value in braces is
    kept as its source text, since what matters here is whether a fill is
    literally `none` rather than what an expression evaluates to.
    """
    found = {}
    for match in ATTR.finditer(tag_text):
        raw = match.group(1)
        value = match.group(2)
        if value is None:
            value = match.group(3)
        if value is None:
            value = match.group(4) or ""
        found[raw.lower()] = value.strip()
        # JSX spells the hyphenated attributes in camel case, so both readings
        # are kept. Only the alias is derived: `viewBox` is camel case in SVG
        # itself, and rewriting it would lose the canvas.
        alias = re.sub(r"(?<=[a-z])(?=[A-Z])", "-", raw).lower()
        found.setdefault(alias, value.strip())
    return found


def style_of(attrs):
    """The style declarations of a tag, as a name-to-value mapping."""
    raw = attrs.get("style", "")
    out = {}
    for part in raw.split(";"):
        if ":" not in part:
            continue
        name, _, value = part.partition(":")
        out[name.strip().strip("'\"").lower()] = value.strip().strip("'\",")
    return out


def is_solid(value):
    """Whether a paint value covers what is behind it.

    An absent fill is solid black by the specification, which is exactly the
    backdrop that gets written by hand and never noticed, so the caller passes
    None for absent rather than skipping the test.
    """
    if value is None:
        return True
    text = value.strip().lower().replace(" ", "")
    if text in TRANSPARENT:
        return False
    if text.startswith("rgba(") and re.search(r",0(?:\.0+)?\)$", text):
        return False
    if text.startswith("{") or text.startswith("var(--"):
        return False
    return True


def shaped_away(attrs):
    """Whether something other than the rect itself decides where it lands.

    A rect handed a mask, a clip or a filter is not covering the canvas: the
    mask decides what survives, and a turbulence filter throws the fill away
    entirely and draws noise in its place. Each of the three is a texture or a
    scrim written in the shape of a ground, and removing one takes out the
    effect rather than a backdrop.
    """
    return bool(attrs.keys() & {"mask", "filter", "clip-path", "clippath"})


def opaque(attrs):
    """Whether a shape hides what is behind it rather than tinting it.

    A ground is opaque by definition, which is what separates it from the three
    things shaped exactly like one: a six-percent wash over an illustration, a
    sixty-percent scrim with a spotlight masked out of it, and a noise pass. All
    three want to be seen through, so none of them is a background and none is
    this tool's to remove.
    """
    for name in ("fill-opacity", "opacity"):
        raw = attrs.get(name)
        if raw is None:
            continue
        try:
            if float(raw.strip().rstrip("%")) < (100 if "%" in raw else 1):
                return False
        except ValueError:
            return False
    paint = attrs.get("fill", "") + style_of(attrs).get("fill", "")
    alpha = ALPHA.search(paint)
    if not alpha:
        return True
    stated = alpha.group(1) or alpha.group(2)
    return float(stated.rstrip("%")) >= (100 if "%" in stated else 1)


def canvas_of(attrs):
    """The drawing area, from the viewBox or from the width and height.

    Returned as (x, y, width, height) in user units, or None when the file
    states neither -- in which case only a percentage-sized rect can be shown to
    span it, and a numeric one is left alone rather than guessed at.
    """
    box = attrs.get("viewbox") or attrs.get("viewBox")
    if box:
        parts = re.split(r"[\s,]+", box.strip())
        if len(parts) == 4:
            try:
                return tuple(float(p) for p in parts)
            except ValueError:
                pass
    try:
        width = float(re.sub(r"[a-z%]+$", "", attrs.get("width", "").strip()))
        height = float(re.sub(r"[a-z%]+$", "", attrs.get("height", "").strip()))
    except ValueError:
        return None
    return (0.0, 0.0, width, height)


def spans_canvas(attrs, canvas):
    """Whether a rect covers the whole drawing area.

    Percentages answer on their own. Numbers need the canvas, and are compared
    with a one-unit tolerance because a hand-written backdrop is routinely a
    unit shy of the viewBox or a unit past it.
    """
    width = attrs.get("width", "").strip()
    height = attrs.get("height", "").strip()
    if width.endswith("%") and height.endswith("%"):
        try:
            return float(width[:-1]) >= 100 and float(height[:-1]) >= 100
        except ValueError:
            return False
    if canvas is None:
        return False
    origin_x, origin_y, box_w, box_h = canvas
    try:
        rect_w = float(re.sub(r"[a-z]+$", "", width))
        rect_h = float(re.sub(r"[a-z]+$", "", height))
        rect_x = float(re.sub(r"[a-z]+$", "", attrs.get("x", "0") or "0"))
        rect_y = float(re.sub(r"[a-z]+$", "", attrs.get("y", "0") or "0"))
    except ValueError:
        return False
    return (rect_w >= box_w - 1 and rect_h >= box_h - 1
            and rect_x <= origin_x + 1 and rect_y <= origin_y + 1)


def blocks(text):
    """Every SVG in a file, as (start, end, inner_start) character offsets.

    Nesting is counted rather than matched to the first close tag, so an SVG
    holding another one yields the outer block once instead of ending early.
    """
    found = []
    for opening in SVG_OPEN.finditer(text):
        if opening.group(0).rstrip().endswith("/>"):
            continue
        depth = 1
        cursor = opening.end()
        while depth and cursor < len(text):
            nxt = ELEMENT.search(text, cursor)
            if not nxt:
                cursor = len(text)
                break
            if nxt.group(2).lower() == "svg" and not nxt.group(4):
                depth += 0 if nxt.group(1) else 1
                if nxt.group(1):
                    depth -= 1
            cursor = nxt.end()
        found.append((opening.start(), cursor, opening.end()))
    return found


def first_drawn(text, inner_start, end):
    """The first element that puts ink on the canvas, as a regex match.

    Wrappers and definitions are stepped over: a backdrop written inside the
    outermost group is the same backdrop, and refusing to see it there is how a
    guard gets worked around by accident.
    """
    cursor = inner_start
    while cursor < end:
        match = ELEMENT.search(text, cursor)
        if not match or match.start() >= end:
            return None
        name = match.group(2).lower()
        if match.group(1):
            cursor = match.end()
            continue
        if name in DRAWN:
            return match
        if name in PRELUDE:
            if name in ("defs", "clippath", "mask", "symbol", "filter",
                        "lineargradient", "radialgradient"):
                closing = re.search(r"</\s*%s\s*>" % re.escape(name),
                                    text[match.end():end], re.I)
                cursor = match.end() + (closing.end() if closing else 0)
                continue
            cursor = match.end()
            continue
        cursor = match.end()
    return None


def line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def background_findings(text, suffix=""):
    """Every place a graphic in this text paints behind itself.

    Each finding is (line, kind, excerpt, start, end). The offsets are what
    `repair` removes, so a finding that cannot name the span it would delete is
    not reported: an unremovable finding is a report, and this tool does not
    report.

    The suffix decides whether the stylesheet test runs, since that one reads a
    selector and a declaration block rather than a tag and would answer for
    fenced examples in prose.
    """
    found = []
    for start, end, inner in blocks(text):
        opening = text[start:inner]
        attrs = attrs_of(opening)
        style = style_of(attrs)

        for name in ("background", "background-color"):
            if name in style and is_solid(style[name]):
                span = re.search(
                    r"\s*%s\s*:[^;\"']*;?" % re.escape(name), opening, re.I)
                if span:
                    found.append((line_of(text, start), "style on the root svg",
                                  span.group(0).strip(),
                                  start + span.start(), start + span.end()))

        for hit in re.finditer(r"class(?:Name)?\s*=\s*[\"']([^\"']*)[\"']",
                               opening, re.I):
            classes = [c for c in hit.group(1).split()
                       if re.fullmatch(r"(?:\w+:)?bg-\[?[#\w./-]+\]?", c)]
            if classes:
                found.append((line_of(text, start), "background class on the svg",
                              " ".join(classes), 0, 0))

        canvas = canvas_of(attrs)
        drawn = first_drawn(text, inner, end)
        if drawn and drawn.group(2).lower() == "rect":
            rect = attrs_of(drawn.group(3))
            paint = rect.get("fill", style_of(rect).get("fill"))
            if "fill" not in rect and "fill" not in style_of(rect):
                paint = None
            if (spans_canvas(rect, canvas) and is_solid(paint)
                    and opaque(rect) and not shaped_away(rect)):
                found.append((line_of(text, drawn.start()), "full-canvas rect",
                              drawn.group(0).strip()[:160],
                              drawn.start(), drawn.end()))

    if suffix.lower() in STYLESHEET:
        for hit in CSS_SVG_BACKGROUND.finditer(text):
            if is_solid(hit.group(1)):
                found.append((line_of(text, hit.start()),
                              "css painting behind svg",
                              hit.group(0).split("{")[0].strip()
                              + " { background }", 0, 0))

    return sorted(found, key=lambda f: f[0])


def repair(text, suffix=""):
    """The same text with every removable background taken out.

    Spans come back sorted, so they are cut from the end forward and no earlier
    removal moves a later offset. A finding with no span -- a utility class, a
    stylesheet rule -- is left for a person, because deleting the wrong half of
    a class list or a shared CSS rule breaks more than it fixes.
    """
    spans = [(s, e) for _, _, _, s, e in background_findings(text, suffix)
             if e > s]
    for start, end in sorted(spans, reverse=True):
        cut = text[start:end]
        text = text[:start] + text[end:]
        if cut.strip().startswith("<rect"):
            text = re.sub(r"\n[ \t]*\n", "\n", text, count=0)
    return text


def scene_findings(text):
    """Every SVG here that is a scene built out of boxes.

    Counted per graphic rather than per file, so one assembled illustration is
    not excused by the plain icons sitting beside it in the same component.
    """
    found = []
    for start, end, inner in blocks(text):
        body = text[inner:end]
        primitives = [m.group(2).lower() for m in ELEMENT.finditer(body)
                      if not m.group(1) and m.group(2).lower() in DRAWN]
        if len(primitives) < SCENE_ELEMENTS:
            continue
        straight = sum(1 for name in primitives if name in BOXY)
        straight += sum(1 for m in re.finditer(r"<path\b[^>]*\bd\s*=\s*[\"']([^\"']*)",
                                               body, re.I)
                        if not CURVE.search(m.group(1)))
        share = straight / len(primitives)
        if share >= SCENE_BOXY_SHARE:
            found.append((line_of(text, start), len(primitives), share))
    return found


def files_under(root):
    root = Path(root)
    if root.is_file():
        return [root]
    out = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MARKUP:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        out.append(path)
    return sorted(out)


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def scan(root, quiet=False):
    """Report every painted background under a tree; return the file count."""
    hits = 0
    for path in files_under(root):
        text = read(path)
        if text is None or "<svg" not in text and "svg" not in text.lower():
            continue
        found = background_findings(text, path.suffix)
        if not found:
            continue
        hits += 1
        if not quiet:
            print(path)
            for line, kind, excerpt, _, _ in found:
                print("  %d  %s\n      %s" % (line, kind, excerpt))
    return hits


def fix(root):
    """Strip every removable background under a tree, and name what moved."""
    changed = 0
    left = []
    for path in files_under(root):
        text = read(path)
        if text is None or "<svg" not in text or COMPOSITED.search(path.name):
            continue
        found = background_findings(text, path.suffix)
        if not found:
            continue
        after = repair(text, path.suffix)
        if after != text:
            path.write_text(after, encoding="utf-8")
            changed += 1
            print("stripped %d  %s" % (len(found), path))
        remaining = background_findings(after, path.suffix)
        for line, kind, excerpt, _, _ in remaining:
            left.append((path, line, kind, excerpt))
    if left:
        print("\nnot removable here, they belong to a person:")
        for path, line, kind, excerpt in left:
            print("  %s:%d  %s\n      %s" % (path, line, kind, excerpt))
    print("\n%d file(s) rewritten, %d finding(s) left" % (changed, len(left)))
    return 1 if left else 0


def deny(reason, detail, remedy):
    sys.stderr.write("BLOCKED: %s\n\n" % reason)
    sys.stderr.write(detail.rstrip() + "\n\n")
    sys.stderr.write(remedy.rstrip() + "\n")
    return 2


def payload():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}


def proposed(tool, tool_input, path):
    """The file an edit would leave behind, or None when it would not apply."""
    if tool == "Write":
        return str(tool_input.get("content") or "")
    text = read(path) or ""
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        edits = [tool_input]
    for edit in edits:
        old = str(edit.get("old_string") or "")
        new = str(edit.get("new_string") or "")
        if not old:
            text = new
            continue
        if old not in text:
            return None
        text = (text.replace(old, new) if edit.get("replace_all")
                else text.replace(old, new, 1))
    return text


def tracked(path):
    """Whether git already holds this file, so its drawings predate the gate."""
    try:
        run = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)],
                             cwd=str(path.parent), capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return run.returncode == 0


def guard_edit():
    if os.environ.get("INTELLIGENCE_ALLOW_SVG_BACKGROUND") == "1":
        return 0
    data = payload()
    tool = data.get("tool_name") or ""
    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return 0
    tool_input = data.get("tool_input") or {}
    target = str(tool_input.get("file_path") or "")
    if not target or Path(target).suffix.lower() not in MARKUP:
        return 0

    path = Path(target)
    if COMPOSITED.search(path.name):
        return 0
    content = proposed(tool, tool_input, path)
    if content is None or "<svg" not in content:
        return 0

    found = background_findings(content, path.suffix)
    if found:
        detail = "\n".join("  line %d  %s\n      %s" % (line, kind, excerpt)
                           for line, kind, excerpt, _, _ in found)
        return deny(
            "this edit leaves an SVG painting its own background.",
            detail,
            "A graphic carries its drawing and nothing else, so it reads the "
            "same on\nevery section it lands on. Drop the backdrop rather than "
            "recolouring it;\nthe page underneath is the background.\n\n"
            "  ~/.claude/tools/svg-guard.py fix %s\n\n"
            "INTELLIGENCE_ALLOW_SVG_BACKGROUND=1 covers a graphic whose "
            "backdrop is the drawing." % target)

    was = scene_findings(read(path) or "") if path.exists() else []
    now = scene_findings(content)
    grew = len(now) > len(was) or (
        now and was and max(n[1] for n in now) > max(w[1] for w in was))
    if now and grew and not (tracked(path) and not grew):
        detail = "\n".join(
            "  line %d  %d drawn elements, %d%% of them boxes and straight lines"
            % (line, count, round(share * 100)) for line, count, share in now)
        return deny(
            "this is a scene assembled out of rectangles, not a graphic.",
            detail,
            "A drawing at this size made of boxes and straight segments does "
            "not come\nout looking like the thing it depicts, and no amount of "
            "adjustment gets it\nthere. Use a photograph, a real exported "
            "asset, or a mark simple enough to\nbe right: a silhouette, a "
            "wordmark, a single shape.\n\n"
            "INTELLIGENCE_ALLOW_SVG_BACKGROUND=1 covers artwork that was "
            "exported rather\nthan written.")
    return 0


def repo_root(start):
    try:
        run = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=str(start), capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(run.stdout.strip()) if run.returncode == 0 else None


def staged(root):
    try:
        run = subprocess.run(["git", "diff", "--cached", "--name-only",
                              "--diff-filter=ACMR"], cwd=str(root),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if run.returncode != 0:
        return []
    return [n for n in run.stdout.splitlines()
            if Path(n).suffix.lower() in MARKUP]


def staged_text(root, name):
    try:
        run = subprocess.run(["git", "show", ":" + name], cwd=str(root),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return run.stdout if run.returncode == 0 else None


def guard_bash():
    """Refuse a commit whose staged SVGs paint their own background.

    The staged content is what is judged rather than the working tree, so a file
    fixed after staging is not counted against the commit and a file broken
    after staging is not let through by a clean disk.
    """
    if os.environ.get("INTELLIGENCE_ALLOW_SVG_BACKGROUND") == "1":
        return 0
    data = payload()
    command = str((data.get("tool_input") or {}).get("command") or "")
    if not re.search(r"\bgit\b[^|;&]*\bcommit\b", command):
        return 0
    root = repo_root(data.get("cwd") or os.getcwd())
    if root is None:
        return 0
    problems = []
    for name in staged(root):
        text = staged_text(root, name)
        if text is None or "<svg" not in text:
            continue
        for line, kind, excerpt, _, _ in background_findings(text, Path(name).suffix):
            problems.append((name, line, kind, excerpt))
    if not problems:
        return 0
    detail = "\n".join("  %s:%d  %s\n      %s" % row for row in problems)
    return deny(
        "staged SVGs paint their own background.",
        detail,
        "  ~/.claude/tools/svg-guard.py fix .\n  git add -A\n\n"
        "INTELLIGENCE_ALLOW_SVG_BACKGROUND=1 covers a graphic whose backdrop "
        "is the drawing.")


def check():
    root = repo_root(os.getcwd()) or Path.cwd()
    hits = scan(root)
    if hits:
        print("\n%d file(s) paint a background. "
              "~/.claude/tools/svg-guard.py fix ." % hits)
        return 1
    print("every SVG in %s is transparent behind its drawing" % root.name)
    return 0


def main(argv):
    verb = argv[0] if argv else ""
    where = argv[1] if len(argv) > 1 else "."
    if verb == "scan":
        return 1 if scan(where) else 0
    if verb == "fix":
        return fix(where)
    if verb == "check":
        return check()
    if verb == "guard-edit":
        return guard_edit()
    if verb == "guard-bash":
        return guard_bash()
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
