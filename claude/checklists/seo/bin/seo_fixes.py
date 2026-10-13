"""Repairs the SEO gates make to a project without being told what to write.

A gate that reports a fault and waits is a recommendation. Where the fault has one
correct repair derivable from what the project already holds - a canonical that is
the route's own URL, a share card built from the title already in the head, a
script tag missing `defer` - the tool applies it.

Every fixer re-derives the fault from the same inputs its checker read, so none
depends on parsing a failure message. A fixer returns the paths it changed. A
repair that needs prose nobody has written yet - a description, alt text - has no
fixer, and its gate stays failing.
"""

import re
import struct
from pathlib import Path

HEAD_CLOSE = re.compile(r"</head>", re.I)


def _insert_into_head(text, block):
    """A tag placed above the closing head tag, at the head's own indent."""
    match = HEAD_CLOSE.search(text)
    if not match:
        return None
    line_start = text.rfind("\n", 0, match.start()) + 1
    indent = text[line_start:match.start()]
    return text[:line_start] + indent + block + "\n" + text[line_start:]


def _site_url(site, run):
    for page in site["pages"]:
        found = re.search(r'rel=["\']canonical["\'][^>]*href=["\'](https?://[^"\'/]+)',
                          page["text"], re.I)
        if found:
            return found.group(1)
    return (run.get("url") or "").rstrip("/") or None


def _route_url(page, base):
    rel = page["rel"].replace("index.html", "").rstrip("/")
    return "%s/%s" % (base.rstrip("/"), rel) if rel else base.rstrip("/") + "/"


def fix_canonical(gate, site, run, ctx, indexable, has_field):
    base = _site_url(site, run)
    if not base:
        return []
    changed = []
    for page in site["pages"]:
        if not indexable(page) or has_field(page, "canonical"):
            continue
        block = '<link rel="canonical" href="%s" />' % _route_url(page, base)
        updated = _insert_into_head(page["text"], block)
        if updated:
            Path(page["path"]).write_text(updated)
            changed.append(page["rel"])
    return changed


def fix_twitter_card(gate, site, run, ctx):
    """One declaration in the shell covers every route, so it sits on the home page."""
    blob = "\n".join(p["text"] for p in site["pages"])
    if re.search(r"twitter:card", blob, re.I):
        return []
    home = next((p for p in site["pages"] if p["rel"] in ("index.html", "index.htm")), None)
    if not home:
        return []
    updated = _insert_into_head(
        home["text"], '<meta name="twitter:card" content="summary_large_image" />')
    if not updated:
        return []
    Path(home["path"]).write_text(updated)
    return [home["rel"]]


def fix_script_defer(gate, site, run, ctx, markup_ext):
    """A src script that blocks the parser takes `defer`; a module already defers."""
    changed = []
    for path, text in site["sources"].items():
        if Path(path).suffix.lower() not in markup_ext:
            continue
        updated = text
        for tag in re.findall(r"<script\b[^>]*\bsrc\s*=[^>]*>", text, re.I):
            if re.search(r"\b(?:defer|async)\b|type\s*=\s*[\"']module[\"']", tag, re.I):
                continue
            updated = updated.replace(tag, tag[:-1].rstrip() + " defer>", 1)
        if updated != text:
            Path(path).write_text(updated)
            changed.append(Path(path).name)
    return changed


def _png_size(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    return None


def fix_image_dimensions(gate, site, run, ctx, root):
    """Width and height read off the file, so the box is reserved before it loads."""
    changed = []
    for page in site["pages"]:
        text = page["text"]
        updated = text
        for tag in re.findall(r"<img\b[^>]*>", text, re.I):
            if re.search(r"\bwidth\s*=", tag, re.I) and re.search(r"\bheight\s*=", tag, re.I):
                continue
            src = re.search(r'\bsrc\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if not src or src.group(1).startswith(("http", "data:")):
                continue
            candidate = Path(root) / "public" / src.group(1).lstrip("/")
            if not candidate.is_file():
                candidate = Path(root) / src.group(1).lstrip("/")
            if not candidate.is_file():
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
            Path(page["path"]).write_text(updated)
            changed.append(page["rel"])
    return changed
