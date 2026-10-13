"""Repairs the performance gates make to a project rather than report.

Loading faults divide cleanly. Some need a decision nobody has made - which image
is the hero, what a bundle should split along - and stay failing until somebody
makes it. The rest have one correct form: a third-party script defers, a face
declares a display strategy, an image reserves its box from dimensions the file
already carries. Those the tool writes.

No repair here changes what a reader sees, which is the invariant the parity gates
exist to hold: attributes that govern when bytes arrive, never what they render.
"""

import re
import struct
from pathlib import Path

MARKUP = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".astro"}
STYLE = {".css", ".scss", ".sass", ".less"}


def _iter(project, suffixes):
    for path, text in project.get("sources", {}).items():
        if Path(path).suffix.lower() in suffixes:
            yield Path(path), text


def fix_thirdparty_defer(gate, project, run, ctx):
    """A src script that blocks the parser takes defer; a module already defers."""
    changed = []
    for path, text in _iter(project, MARKUP):
        updated = text
        for tag in re.findall(r"<script\b[^>]*\bsrc\s*=[^>]*>", text, re.I):
            if re.search(r"\b(?:defer|async)\b|type\s*=\s*[\"']module[\"']", tag, re.I):
                continue
            updated = updated.replace(tag, tag[:-1].rstrip() + " defer>", 1)
        if updated != text:
            path.write_text(updated)
            changed.append(path.name)
    return changed


def fix_font_display(gate, project, run, ctx):
    """A face with no display strategy blocks its text until the file lands."""
    changed = []
    for path, text in _iter(project, STYLE):
        updated = text
        for block in re.findall(r"@font-face\s*\{[^}]*\}", text, re.I):
            if re.search(r"font-display", block, re.I):
                continue
            body = block.rstrip()[:-1].rstrip()
            indent = "  "
            hit = re.search(r"\n(\s+)\S", block)
            if hit:
                indent = hit.group(1)
            updated = updated.replace(
                block, "%s\n%sfont-display: swap;\n}" % (body, indent), 1)
        if updated != text:
            path.write_text(updated)
            changed.append(path.name)
    return changed


def _png_size(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    return None


def fix_image_dimensions(gate, project, run, ctx):
    """Width and height read off the file, so the box is held before it decodes."""
    root = Path(project.get("root") or ".")
    changed = []
    for path, text in _iter(project, MARKUP):
        updated = text
        for tag in re.findall(r"<img\b[^>]*>", text, re.I):
            if re.search(r"\bwidth\s*=", tag, re.I) and re.search(r"\bheight\s*=", tag, re.I):
                continue
            src = re.search(r'\bsrc\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if not src or src.group(1).startswith(("http", "data:", "{")):
                continue
            rel = src.group(1).lstrip("/")
            candidate = next((c for c in (root / "public" / rel, root / rel, root / "static" / rel)
                              if c.is_file()), None)
            if not candidate:
                continue
            try:
                size = _png_size(candidate.read_bytes()[:32])
            except OSError:
                size = None
            if not size:
                continue
            updated = updated.replace(
                tag, tag[:-1].rstrip() + ' width="%d" height="%d">' % size, 1)
        if updated != text:
            path.write_text(updated)
            changed.append(path.name)
    return changed
