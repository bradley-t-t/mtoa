"""Repairs the design gates make to a deliverable rather than report.

Most design faults are judgement - which layout family a section takes, whether a
hero earns its visual - and no tool settles those. A few are arithmetic: a spacing
value off the 4px scale has one nearest step, a press scale outside its range has
one correct value, a grid track that floors at its content's intrinsic width has
one correct floor. Those the tool writes.

Nothing here changes a colour, a word, or an arrangement. Each fixer re-derives
the fault from the same sources its checker read and returns the paths it changed.
"""

import re
from pathlib import Path

STYLE = {".css", ".scss", ".sass", ".less"}

SPACE_PROPS = (r"(?:padding|margin|gap|row-gap|column-gap|inset|top|bottom|left|right)"
               r"(?:-(?:block|inline)(?:-(?:start|end))?|-(?:top|bottom|left|right))?")


def _style_files(sources):
    for path, text in sources.items():
        if Path(path).suffix.lower() in STYLE:
            yield Path(path), text


def fix_spacing_scale(gate, sources, run, ctx):
    """A spacing value off the 4px scale snaps to the nearest step on it.

    1px and 2px are hairlines rather than spacing and the checker exempts them, so
    they are left where they are.
    """
    changed = []
    for path, text in _style_files(sources):
        updated = text

        def snap(match):
            prop, value = match.group(1), match.group(2)
            def one(hit):
                px = float(hit.group(1))
                if not px or px in (1.0, 2.0) or px % 4 == 0:
                    return hit.group(0)
                return "%dpx" % (round(px / 4) * 4 or 4)
            return "%s:%s" % (prop, re.sub(r"(\d+(?:\.\d+)?)px", one, value))

        updated = re.sub(r"(%s):([^;}]+)" % SPACE_PROPS, snap, updated, flags=re.I)
        if updated != text:
            path.write_text(updated)
            changed.append(path.name)
    return changed


def fix_active_scale_range(gate, sources, run, ctx):
    """A press outside 0.95-0.98 reads as a jolt or as nothing; 0.97 sits inside it."""
    changed = []
    for path, text in _style_files(sources):
        updated = re.sub(
            r"(--press\s*:\s*)(0?\.\d+)",
            lambda m: m.group(1) + ("0.97" if not 0.95 <= float(m.group(2)) <= 0.98
                                    else m.group(2)),
            text)
        updated = re.sub(
            r"(scale\s*\(\s*)(0?\.\d+)(\s*\))",
            lambda m: (m.group(1) + "0.97" + m.group(3)
                       if not 0.95 <= float(m.group(2)) <= 0.98 else m.group(0)),
            updated)
        if updated != text:
            path.write_text(updated)
            changed.append(path.name)
    return changed


def fix_grid_minmax(gate, sources, run, ctx):
    """A track floored at its content overflows; min() floors it at the track's share."""
    changed = []
    for path, text in _style_files(sources):
        updated = text

        def floor(match):
            value = match.group(1)
            if "minmax(0" in value or "min(" in value:
                return match.group(0)
            fixed = re.sub(r"minmax\(\s*(\d+(?:\.\d+)?(?:rem|px|em|ch))\s*,",
                           r"minmax(min(\1, 100%),", value)
            if fixed == value:
                fixed = re.sub(r"(^|\s)1fr", r"\1minmax(0, 1fr)", value)
            return match.group(0).replace(value, fixed)

        updated = re.sub(r"grid-template-(?:columns|rows):\s*([^;}]+)", floor,
                         updated, flags=re.I)
        if updated != text:
            path.write_text(updated)
            changed.append(path.name)
    return changed


def fix_font_display(gate, sources, run, ctx):
    """A face with no display strategy holds its text back until the file lands."""
    changed = []
    for path, text in _style_files(sources):
        updated = text
        for block in re.findall(r"@font-face\s*\{[^}]*\}", text, re.I):
            if re.search(r"font-display", block, re.I):
                continue
            indent = "  "
            hit = re.search(r"\n(\s+)\S", block)
            if hit:
                indent = hit.group(1)
            updated = updated.replace(
                block, "%s\n%sfont-display: swap;\n}" % (block.rstrip()[:-1].rstrip(), indent), 1)
        if updated != text:
            path.write_text(updated)
            changed.append(path.name)
    return changed
