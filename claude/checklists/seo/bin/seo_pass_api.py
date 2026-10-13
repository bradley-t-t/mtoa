"""Route discovery from seo-pass, reusable by the emit scripts.

The checklist and the scripts that satisfy it have to agree on what a route is, or a
sitemap lists pages the sweep never saw and the sweep fails gates the sitemap already
covered. Both read this, and seo-pass.py stays the one place a route is defined.

The module name is spelled with underscores because seo-pass.py cannot be imported
under its own hyphenated name.
"""

import importlib.util
import re
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_seo_pass", Path(__file__).resolve().parent / "seo-pass.py")
_seo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seo)

page_title_of = _seo.page_title
page_description_of = _seo.page_description

# A segment naming a parameter rather than a page. These enumerate only against real
# data, so a sitemap built from the file tree lists the template instead of the pages.
DYNAMIC = re.compile(r"\[.+\]|:\w+|\(\.{3}\)|\{.+\}")

# What to strip from a route-relative path before it becomes a URL. Each entry is the
# framework's own convention for "this directory is the routing root".
ROUTE_PREFIXES = ("src/app/", "src/pages/", "src/routes/", "src/views/", "src/screens/",
                  "app/", "pages/", "public/", "static/", "templates/", "views/", "src/")

# Segments a framework uses to organise files without adding to the URL: a Next.js
# route group, and the private folders that are never routed at all. Left in, a sitemap
# publishes /app/(admin)/admin for a page served at /admin.
NON_URL_SEGMENT = re.compile(r"^\(.+\)$|^_")


def url_path(rel, kind):
    """The public path a route file is served at."""
    path = rel
    for prefix in ROUTE_PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    path = re.sub(r"\.(html?|jsx?|tsx?|vue|svelte|astro|mdx?|php)$", "", path)
    path = re.sub(r"/?\+page$", "", path)
    path = re.sub(r"/?page$", "", path) if kind == "next-app" else path
    path = re.sub(r"/?index$", "", path)
    # A view component is named for its route rather than placed at it: the directory
    # is the route and the file repeats it, so AboutView.jsx under about/ is /about.
    if kind == "spa-route":
        parts = [p for p in path.split("/") if p]
        if len(parts) > 1 and parts[-1].lower().startswith(parts[-2].lower()):
            parts = parts[:-1]
        elif len(parts) > 1:
            parts[-1] = re.sub(r"(View|Page|Screen|Route)$", "", parts[-1])
            parts[-1] = re.sub(r"(?<!^)(?=[A-Z])", "-", parts[-1]).lower()
        path = "/".join(p for p in parts if p)
    path = "/".join(seg for seg in path.split("/")
                    if seg and not NON_URL_SEGMENT.match(seg))
    path = path.strip("/")
    return "/" + path if path else "/"


# How a router names the component it mounts at a path. The JSX form covers
# react-router and its imitators; the object form covers createBrowserRouter and the
# route arrays Vue and Angular use.
ROUTE_BINDING = [
    re.compile(r"""<Route\b[^>]*?\bpath\s*=\s*["']([^"']+)["'][^>]*?"""
               r"""\b(?:element|component)\s*=\s*\{\s*<?\s*([A-Z]\w+)""", re.S),
    re.compile(r"""<Route\b[^>]*?\b(?:element|component)\s*=\s*\{\s*<?\s*([A-Z]\w+)"""
               r"""[^>]*?\bpath\s*=\s*["']([^"']+)["']""", re.S),
    re.compile(r"""\bpath\s*:\s*["']([^"']+)["'][^}]{0,200}?"""
               r"""\b(?:element|component)\s*:\s*<?\s*([A-Z]\w+)""", re.S),
]


def router_map(root):
    """Which component each path mounts, read from the router rather than the file tree.

    A view component sits wherever its author put it, so a path derived from the
    directory is a guess - SuccessView under checkout/ is served at /order-success as
    often as at /checkout/success. The router config is the only thing that knows.
    """
    found = {}
    for path in Path(root).rglob("*"):
        if path.suffix.lower() not in {".js", ".jsx", ".ts", ".tsx", ".vue"}:
            continue
        if any(part in _seo.SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if "Route" not in text and "path:" not in text:
            continue
        for i, rx in enumerate(ROUTE_BINDING):
            for m in rx.finditer(text):
                route, component = (m.group(2), m.group(1)) if i == 1 else (m.group(1), m.group(2))
                if route.startswith("/") and "*" not in route:
                    found.setdefault(component, route)
    return found


def routes_of(root):
    """Every static route under `root`, each with the URL path it is served at.

    Dynamic segments are left out: they enumerate only against the data behind them,
    and a sitemap listing `/blog/[slug]` is a sitemap with a broken entry in it.
    """
    root = Path(root).expanduser().resolve()
    run = {"targets": [str(root)], "kind": "site", "flags": []}
    pages, _ = _seo.collect_pages(run)
    mounted = router_map(root)
    out = []
    for page in pages:
        if DYNAMIC.search(page["rel"]):
            continue
        component = Path(page["rel"]).stem
        page["url_path"] = mounted.get(component) or url_path(page["rel"], page["kind"])
        page["from_router"] = component in mounted
        # A view the router never mounts is not served anywhere, so listing it in a
        # sitemap publishes a 404.
        if page["kind"] == "spa-route" and mounted and component not in mounted:
            continue
        out.append(page)
    return out


def dynamic_routes_of(root):
    """The routes left out of `routes_of`, so a run can say what it did not list."""
    run = {"targets": [str(Path(root).expanduser().resolve())], "kind": "site", "flags": []}
    pages, _ = _seo.collect_pages(run)
    return [p["rel"] for p in pages if DYNAMIC.search(p["rel"])]
