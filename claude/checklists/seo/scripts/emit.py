#!/usr/bin/env python3
"""Writes the crawlable files a project is missing, from what the project already contains.

Four gates in this checklist are satisfied by a file that does not exist yet -
robots.txt, sitemap.xml, llms.txt, and the identity JSON-LD - and each one is
mechanical given the project's routes and its site URL. Hand-writing them is how a
sitemap ends up listing routes that were deleted and a robots.txt points at a host
the site moved off.

    robots    write robots.txt with a sitemap line and an AI-crawler stance
    sitemap   write sitemap.xml from the routes the project actually has
    llms      write llms.txt from the routes and their titles
    schema    print a JSON-LD node for a type, filled from what is passed

The three file-writing subcommands print to stdout unless given --write, so the
output can be read before it lands; schema always prints.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

# The gate engine sits in the checklist tree; the emitters read routes and titles
# off the same source it does, so both answer for one reading of a project.
ENGINE_BIN = Path(__file__).resolve().parents[3] / "checklists" / "seo" / "bin"
if not ENGINE_BIN.is_dir():
    ENGINE_BIN = Path.home() / ".claude" / "checklists" / "seo" / "bin"
sys.path.insert(0, str(ENGINE_BIN))
from seo_pass_api import routes_of, page_title_of  # noqa: E402


AI_CRAWLERS = ["GPTBot", "ChatGPT-User", "ClaudeBot", "PerplexityBot", "Bytespider",
               "Google-Extended", "CCBot", "Applebot-Extended"]

# The crawlers that feed model training rather than the fetchers that produce
# citations, so blocking them costs nothing a project usually wants to keep:
# Google-Extended feeds Gemini training and not Search, and GPTBot feeds training
# and not the browsing fetcher.
TRAINING_ONLY = ["GPTBot", "ClaudeBot", "Google-Extended", "Bytespider", "CCBot",
                 "Applebot-Extended"]


def emit(text, out, write):
    if write:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print("wrote %s" % out)
    else:
        print(text, end="")


def static_dir(root):
    """Where this stack serves files from, so robots.txt lands somewhere it is served."""
    for candidate in ("public", "static", "www", "src/public"):
        if (root / candidate).is_dir():
            return root / candidate
    return root


def cmd_robots(args):
    root = Path(args.root).expanduser().resolve()
    lines = ["User-agent: *", "Allow: /", ""]
    if args.ai == "block":
        blocked = AI_CRAWLERS
    elif args.ai == "training":
        blocked = TRAINING_ONLY
    else:
        blocked = []
    for agent in blocked:
        lines += ["User-agent: %s" % agent, "Disallow: /", ""]
    if args.ai == "allow":
        for agent in AI_CRAWLERS:
            lines += ["User-agent: %s" % agent, "Allow: /", ""]
    for path in args.disallow or []:
        lines.insert(1, "Disallow: %s" % path)
    lines.append("Sitemap: %s/sitemap.xml" % args.site_url.rstrip("/"))
    emit("\n".join(lines) + "\n", static_dir(root) / "robots.txt", args.write)


# An error page is served in place of a route rather than as one. Listing it in a
# sitemap or an llms.txt points a crawler at a page whose whole job is to report that
# there is nothing there.
ERROR_ROUTE = re.compile(r"(^|/)(404|500|error|not[-_]?found)(\.|/|$)", re.I)


def route_urls(root, site_url):
    """The public URL of every route, derived from where its file sits."""
    base = site_url.rstrip("/")
    # Two route files can serve one URL - an index page and its layout, a view named
    # for the directory it sits in - so the first one to claim a URL keeps it.
    claimed = {}
    for page in routes_of(root):
        if ERROR_ROUTE.search(page.get("rel", "")) or ERROR_ROUTE.search(page["url_path"]):
            continue
        claimed.setdefault(base + page["url_path"], page)
    return sorted(claimed.items())


def cmd_sitemap(args):
    root = Path(args.root).expanduser().resolve()
    stamp = args.lastmod or date.today().isoformat()
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, _ in route_urls(root, args.site_url):
        body += ["  <url>", "    <loc>%s</loc>" % url,
                 "    <lastmod>%s</lastmod>" % stamp, "  </url>"]
    body.append("</urlset>")
    emit("\n".join(body) + "\n", static_dir(root) / "sitemap.xml", args.write)


def component_label(page):
    """A readable name for a route whose title gate has not been satisfied yet."""
    stem = re.sub(r"(View|Page|Screen|Route)$", "", Path(page["rel"]).stem) or "Home"
    return re.sub(r"(?<!^)(?=[A-Z])", " ", stem)


def cmd_llms(args):
    root = Path(args.root).expanduser().resolve()
    lines = ["# %s" % args.name, "", "> %s" % args.summary, "", "## Pages", ""]
    for url, page in route_urls(root, args.site_url):
        title = page_title_of(page) or component_label(page)
        lines.append("- [%s](%s)" % (title, url))
    emit("\n".join(lines) + "\n", static_dir(root) / "llms.txt", args.write)


def node(kind, **fields):
    out = {"@context": "https://schema.org", "@type": kind}
    out.update({k: v for k, v in fields.items() if v})
    return out


def cmd_schema(args):
    url = (args.site_url or "").rstrip("/")
    if args.type == "organization":
        built = node("Organization", name=args.name, url=url or None,
                     logo=args.logo, sameAs=args.same_as or None,
                     contactPoint=({"@type": "ContactPoint", "telephone": args.phone,
                                    "contactType": "customer service"} if args.phone else None))
    elif args.type == "website":
        built = node("WebSite", name=args.name, url=url or None)
    elif args.type == "localbusiness":
        built = node(args.subtype or "LocalBusiness", name=args.name, url=url or None,
                     telephone=args.phone, image=args.logo,
                     address={"@type": "PostalAddress", "streetAddress": args.street,
                              "addressLocality": args.city, "addressRegion": args.region,
                              "postalCode": args.postal, "addressCountry": args.country},
                     openingHours=args.hours or None,
                     geo=({"@type": "GeoCoordinates", "latitude": args.lat,
                           "longitude": args.lon} if args.lat and args.lon else None))
    elif args.type == "breadcrumb":
        built = node("BreadcrumbList", itemListElement=[
            {"@type": "ListItem", "position": i + 1, "name": part.split("=")[0],
             "item": url + part.split("=", 1)[1] if "=" in part else None}
            for i, part in enumerate(args.crumb or [])])
    elif args.type == "article":
        built = node("BlogPosting", headline=args.name, image=args.logo,
                     datePublished=args.published, dateModified=args.modified or args.published,
                     author=({"@type": "Person", "name": args.author} if args.author else None),
                     mainEntityOfPage=url or None)
    else:
        built = node("Product", name=args.name, image=args.logo, description=args.summary,
                     offers=({"@type": "Offer", "price": args.price,
                              "priceCurrency": args.currency,
                              "availability": "https://schema.org/InStock"}
                             if args.price else None))
    text = json.dumps(built, indent=2)
    if args.script:
        text = '<script type="application/ld+json">\n%s\n</script>' % text
    print(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    def shared(p, needs_url=True):
        p.add_argument("--root", default=".")
        if needs_url:
            p.add_argument("--site-url", required=True)
        p.add_argument("--write", action="store_true")

    r = sub.add_parser("robots"); shared(r)
    r.add_argument("--ai", choices=["allow", "block", "training", "silent"], default="training",
                   help="training blocks the training-only crawlers and leaves the citing "
                        "fetchers alone")
    r.add_argument("--disallow", action="append")
    r.set_defaults(fn=cmd_robots)

    s = sub.add_parser("sitemap"); shared(s)
    s.add_argument("--lastmod")
    s.set_defaults(fn=cmd_sitemap)

    l = sub.add_parser("llms"); shared(l)
    l.add_argument("--name", required=True)
    l.add_argument("--summary", required=True)
    l.set_defaults(fn=cmd_llms)

    c = sub.add_parser("schema")
    c.add_argument("--type", required=True,
                   choices=["organization", "website", "localbusiness", "breadcrumb",
                            "article", "product"])
    c.add_argument("--site-url", default="")
    for flag in ("name", "logo", "phone", "subtype", "street", "city", "region", "postal",
                 "country", "lat", "lon", "published", "modified", "author", "summary",
                 "price", "currency"):
        c.add_argument("--" + flag.replace("_", "-"), dest=flag, default=None)
    c.add_argument("--same-as", action="append", dest="same_as")
    c.add_argument("--hours", action="append")
    c.add_argument("--crumb", action="append", help="Name=/path, repeated in order")
    c.add_argument("--script", action="store_true", help="wrap in a script tag")
    c.set_defaults(fn=cmd_schema)

    args = parser.parse_args()
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
