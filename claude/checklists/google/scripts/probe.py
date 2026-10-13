#!/usr/bin/env python3
"""Takes the readings the /google-checklist gates answer to, one site at a time.

Every gate in this checklist answers for something that happened outside the
repository: what Google indexed, what Search Console holds, what a Business
Profile says, what the field record reports. So each reading is a request, and
each is filed under the site it belongs to. The key is `<domain>|<probe>`,
because a sitemap error on one site says nothing about the next.

Credentials are read from the environment at the point of use and never written
anywhere. Pull them from CryptoFort into the call that needs them:

    GOOGLE_PAGESPEED_API_KEY    PageSpeed Insights and the Chrome UX Report
    GOOGLE_OAUTH_TOKEN          a bearer token for Search Console, GA4, Business
                                Profile and Merchant Center
    GOOGLE_SAFE_BROWSING_KEY    the Safe Browsing lookup
    GOOGLE_KG_API_KEY           the Knowledge Graph lookup, falling back to the
                                PageSpeed key
    GOOGLE_CSE_KEY  GOOGLE_CSE_CX
                                a Programmable Search engine, for what a search
                                for the brand returns
    GSC_SITE_PREFIX             how a domain maps to a Search Console property,
                                `sc-domain:<domain>` by default
    GA4_PROPERTY_MAP            JSON mapping a domain to its GA4 property id
    GOOGLE_MERCHANT_ID  GOOGLE_ADS_CUSTOMER_ID  GOOGLE_ADS_DEVELOPER_TOKEN
                                Merchant Center and Ads

A probe that cannot be taken is filed as failing, with the reason. It is never
filed as passing and never quietly dropped: an unread thing and a passing thing
have to look different, or the checklist is back to guessing. A missing
credential is one of those failures, and its detail names the variable.

    probe.py all --sites example.com --out /tmp/google-probe.json
    probe.py all --registry ~/Development/WebstormProjects/{agency_repo} --out /tmp/p.json
    probe.py gsc | idx | ga4 | gbp | rch | cwv | cmr | trs | prs --sites example.com

Output is {"taken", "sites", "probes"}, which `google-pass.py probe --file`
reads. Every detail is scrubbed of key shapes before it is written, because the
file is quoted into a report that people read.
"""

import argparse
import json
import os
import re
import socket
import ssl
import subprocess
import tempfile
import time
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TIMEOUT = 20


def _profile():
    """The profile reader, tools/mtoa_config.py: the copy in the tree this file
    sits in first, then the installed one, else nothing."""
    import importlib.util
    here = Path(__file__).resolve()
    tree = [here.parents[3]] if len(here.parents) > 3 else []
    for root in tree + [Path.home() / ".claude"]:
        path = root / "tools" / "mtoa_config.py"
        if path.is_file():
            try:
                spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            except Exception:
                continue
    return None


PROFILE = _profile()


def setting(key, default=None):
    """One value from the owner's profile by dotted key; unset without a reader."""
    return PROFILE.get(key, default) if PROFILE is not None else default


# PageSpeed runs a full Lighthouse pass before it answers, so it gets its own budget.
SLOW = 150
PAGE_LIMIT = 6
SITEMAP_SAMPLE = 25
CRAWL_STALE_DAYS = 30
CERT_RENEW_DAYS = 21

# Google's own field thresholds, the same ones perf-checklist measures against.
LCP_MS, INP_MS, CLS_SCORE = 2500, 200, 0.1

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
CRAWLER_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

# Redacted out of every detail before it is written. The output file is quoted into
# a report, and a probe that leaks the credential it used is worse than no probe.
SECRET_SHAPE = re.compile(
    r"ya29\.[A-Za-z0-9_.\-]{10,}|1//0[A-Za-z0-9_\-]{10,}"
    r"|AIza[A-Za-z0-9_\-]{20,}|GOCSPX-[A-Za-z0-9_\-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|sk_(?:live|test)_[A-Za-z0-9]{6,}"
    r"|\"private_key\"\s*:\s*\"[^\"]*\""
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|[?&](?:key|api_key|access_token|token|developerToken)=[A-Za-z0-9_.\-]{8,}")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def scrub(text):
    return SECRET_SHAPE.sub("<redacted>", str(text))[:400]


def env(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def as_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def sh(*args, cwd=None, timeout=60):
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout, done.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


class Headers(dict):
    """Response headers, read without regard to case."""

    def get(self, name, default=""):
        return dict.get(self, str(name).lower(), default)


def collect(raw, final=""):
    """Header lines as one mapping, repeats joined the way a reader would see them."""
    out = Headers()
    for name, value in (raw.items() if raw else []):
        name = name.lower()
        out[name] = "%s, %s" % (out[name], value) if name in out else value
    if final:
        # Where a redirect landed, kept beside the headers so a caller can read it.
        out["x-final-url"] = final
    return out


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hands a redirect back rather than taking it, for the probes about redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


FOLLOWING = urllib.request.build_opener()
STOPPING = urllib.request.build_opener(NoRedirect)


def http(url, headers=None, method="GET", data=None, timeout=TIMEOUT, follow=True,
         limit=2000000):
    """One request. Returns (status, body text, headers). Never raises.

    A transport failure - DNS, TLS, a refused connection, a timeout - comes back
    as status 0 with the reason in the body, so a caller reports what happened
    instead of losing a whole family to an exception.
    """
    payload = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
    sent = {"Accept": "application/json", "User-Agent": BROWSER_UA}
    if isinstance(data, (dict, list)):
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    opener = FOLLOWING if follow else STOPPING
    try:
        # The request is built inside the try because an address that is not one
        # raises here, and every caller asked for a reading rather than an exception.
        request = urllib.request.Request(url, data=payload, method=method, headers=sent)
        with opener.open(request, timeout=timeout) as response:
            body = response.read(limit).decode("utf-8", "replace")
            return response.status, body, collect(response.headers, response.geturl())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200000).decode("utf-8", "replace"), collect(exc.headers, url)
    except Exception as exc:
        return 0, str(exc), Headers()


def http_bytes(url, limit=200000, timeout=TIMEOUT):
    """The first bytes of a resource, for reading an image's own dimensions."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        with FOLLOWING.open(request, timeout=timeout) as response:
            return response.status, response.read(limit)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return 0, b""


def absolute(href, base):
    href = (href or "").strip()
    if not href or href.startswith(("data:", "mailto:", "tel:", "javascript:", "#")):
        return ""
    return urllib.parse.urljoin(base if base.endswith("/") else base + "/", href)


def host_of(url):
    return urllib.parse.urlsplit(url).netloc.lower()


def stem(domain):
    return re.sub(r"^www\.", "", (domain or "").lower())


def visible_text(html):
    """What the page shows, with the script, style and markup taken out."""
    text = re.sub(r"(?is)<(script|style|template|noscript)\b.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def title_of(html):
    found = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return re.sub(r"\s+", " ", found.group(1)).strip() if found else ""


def meta_content(html, key, attr="name"):
    pattern = r"""(?is)<meta\b[^>]*\b%s\s*=\s*["']%s["'][^>]*>""" % (attr, re.escape(key))
    tag = re.search(pattern, html or "")
    if not tag:
        return ""
    value = re.search(r"""(?is)\bcontent\s*=\s*["']([^"']*)["']""", tag.group(0))
    return value.group(1).strip() if value else ""


def digits(text):
    return re.sub(r"\D", "", str(text or ""))


def simple(value):
    """A name flattened to letters and numbers, for comparing two spellings of one."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def flat(value):
    """A sentence flattened to words, so a curly apostrophe and a straight one match."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def days_since(stamp):
    """Whole days between an RFC 3339 stamp and now, or None when it will not parse."""
    if not stamp:
        return None
    text = str(stamp).replace("Z", "+00:00")
    text = re.sub(r"\.(\d{1,6})\d*", r".\1", text)
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).days


def listed(values, limit=6):
    values = [str(v) for v in values if v]
    shown = ", ".join(values[:limit])
    return "%s%s" % (shown, " and %d more" % (len(values) - limit) if len(values) > limit else "")


class Probes(dict):
    """The results, keyed `<domain>|<probe>`, because a reading belongs to one site."""

    def ok(self, site, name, detail):
        self["%s|%s" % (site, name)] = {"ok": True, "detail": scrub(detail), "at": now()}

    def bad(self, site, name, detail):
        self["%s|%s" % (site, name)] = {"ok": False, "detail": scrub(detail), "at": now()}

    def need(self, site, name, variable, what):
        self.bad(site, name, "%s is not set, so %s. Read it from CryptoFort into this call "
                             "rather than into a file." % (variable, what))

    def need_all(self, site, names, variable, what):
        for name in names:
            self.need(site, name, variable, what)

    def bad_all(self, site, names, detail):
        for name in names:
            self.bad(site, name, detail)

    def has(self, site, name):
        return "%s|%s" % (site, name) in self

    def fill(self, site, names, detail):
        """Files the names a family left unanswered, so nothing goes missing silently."""
        for name in names:
            if not self.has(site, name):
                self.bad(site, name, detail)


class Site:
    """One site, and the pages every probe on it shares.

    A run over a portfolio fetches each page once. The structured data, the
    measurement tag, the mixed content and the third-party scripts are all read
    off the same bytes.
    """

    def __init__(self, domain):
        cleaned = re.sub(r"^https?://", "", str(domain)).strip("/ ").lower()
        self.domain = re.sub(r"^www\.", "", cleaned.split("/")[0])
        self.cache = {}
        self.origin = ""
        self.state = {}

    def get(self, url, agent=BROWSER_UA, follow=True, timeout=TIMEOUT):
        key = (url, agent, follow)
        if key not in self.cache:
            self.cache[key] = http(url, headers={"User-Agent": agent, "Accept": HTML_ACCEPT},
                                   follow=follow, timeout=timeout)
        return self.cache[key]

    def resolve(self):
        """The origin that answers, which is the one every other variant redirects to."""
        if self.origin:
            return self.origin
        for candidate in ("https://www.%s" % self.domain, "https://%s" % self.domain):
            status, _, headers = self.get(candidate)
            if status == 200:
                parts = urllib.parse.urlsplit(headers.get("x-final-url") or candidate)
                self.origin = "%s://%s" % (parts.scheme or "https", parts.netloc or self.domain)
                return self.origin
        self.origin = "https://%s" % self.domain
        return self.origin

    def pages(self):
        """The home page and up to five routes it links, fetched once each."""
        if "pages" in self.state:
            return self.state["pages"]
        origin = self.resolve()
        status, html, headers = self.get(origin)
        rows = [(origin, status, html, headers)]
        for url in self.routes(html, origin)[:PAGE_LIMIT - 1]:
            rows.append((url,) + self.get(url))
        self.state["pages"] = rows
        return rows

    def routes(self, html, origin):
        """Same-origin routes the home page links, in the order it links them."""
        seen, out = {origin, origin + "/"}, []
        for href in re.findall(r"""(?is)<a\b[^>]+href\s*=\s*["']([^"']+)["']""", html or ""):
            url = absolute(href, origin).split("#")[0].split("?")[0]
            if not url.startswith(origin + "/"):
                continue
            url = url.rstrip("/")
            if url in seen or re.search(r"\.(?:pdf|zip|jpe?g|png|svg|webp|xml|json|ico)$",
                                        url, re.I):
                continue
            seen.add(url)
            out.append(url)
        return out

    def live(self):
        """The pages that answered 200, which are the ones worth reading."""
        return [(url, html, headers) for url, status, html, headers in self.pages()
                if status == 200 and html]

    def robots(self):
        if "robots" not in self.state:
            self.state["robots"] = self.get("%s/robots.txt" % self.resolve())
        return self.state["robots"]

    def sitemaps(self):
        """The sitemap addresses robots.txt names, or the conventional one."""
        if "sitemaps" in self.state:
            return self.state["sitemaps"]
        status, body, _ = self.robots()
        named = re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", body or "") if status == 200 else []
        found = [absolute(url, self.resolve()) for url in named]
        if not found:
            candidate = "%s/sitemap.xml" % self.resolve()
            code, body, _ = self.get(candidate)
            found = [candidate] if code == 200 and "<" in (body or "") else []
        self.state["sitemaps"] = found
        return found

    def sitemap_urls(self):
        """Every URL the sitemaps list, index files followed one level down."""
        if "sitemap_urls" in self.state:
            return self.state["sitemap_urls"]
        urls, indexes = [], []
        for address in self.sitemaps()[:4]:
            status, body, _ = self.get(address)
            if status != 200 or not body:
                continue
            entries = re.findall(r"(?is)<loc>\s*([^<\s]+)\s*</loc>", body)
            if "<sitemapindex" in body.lower():
                indexes.extend(entries[:5])
            else:
                urls.extend(entries)
        for address in indexes:
            status, body, _ = self.get(address)
            if status == 200 and body:
                urls.extend(re.findall(r"(?is)<loc>\s*([^<\s]+)\s*</loc>", body))
        seen, out = set(), []
        for url in urls:
            if url not in seen:
                seen.add(url)
                out.append(url)
        self.state["sitemap_urls"] = out
        return out

    def brand(self):
        """The name the site calls itself, for the searches and the profile comparisons."""
        if "brand" in self.state:
            return self.state["brand"]
        name = ""
        for url, html, headers in self.live()[:1]:
            name = meta_content(html, "og:site_name", attr="property")
            if not name:
                for block in json_ld(html):
                    if str(block.get("@type", "")).endswith(("Organization", "LocalBusiness",
                                                             "WebSite")) and block.get("name"):
                        name = str(block["name"])
                        break
            if not name:
                title = title_of(html)
                parts = re.split(r"\s+[|\u2013\u2014-]\s+", title)
                name = parts[-1].strip() if len(parts) > 1 else title.strip()
        self.state["brand"] = name or self.domain.split(".")[0]
        return self.state["brand"]


# ---------------------------------------------------------------- shared readings


def robots_groups(text):
    """The rule groups robots.txt declares, keyed by the agent each names."""
    groups, agents, rules, fresh = {}, [], [], True
    for line in (text or "").splitlines():
        line = line.split("#")[0].strip()
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not fresh:
                for agent in agents:
                    groups.setdefault(agent, []).extend(rules)
                agents, rules, fresh = [], [], True
            agents.append(value.lower())
        elif field in ("allow", "disallow"):
            fresh = False
            rules.append((field, value))
    for agent in agents:
        groups.setdefault(agent, []).extend(rules)
    return groups


def robots_rules(text, agent="googlebot"):
    groups = robots_groups(text)
    return groups.get(agent, groups.get("*", []))


def path_matches(pattern, path):
    if not pattern:
        return False
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if char == "*" else re.escape(char) for char in body)
    return re.match(regex + ("$" if anchored else ""), path) is not None


def robots_allows(rules, path):
    """Google's rule: the longest matching pattern wins, and allow takes a tie."""
    longest, verdict = -1, True
    for kind, pattern in rules:
        if not path_matches(pattern, path):
            continue
        if len(pattern) > longest or (len(pattern) == longest and kind == "allow"):
            longest, verdict = len(pattern), kind == "allow"
    return verdict


def path_of(url):
    parts = urllib.parse.urlsplit(url)
    return "%s%s" % (parts.path or "/", "?" + parts.query if parts.query else "")


def hops(site, url, limit=8):
    """The redirect chain from one URL, taken a hop at a time rather than followed."""
    chain, current = [], url
    for _ in range(limit):
        status, _, headers = site.get(current, follow=False)
        if status in (301, 302, 303, 307, 308):
            target = absolute(headers.get("location"), current) or headers.get("location")
            chain.append((current, status, target))
            if not target or target == current:
                break
            current = target
            continue
        chain.append((current, status, ""))
        break
    return chain


LD_SCRIPT = re.compile(
    r"""(?is)<script\b[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""")


def ld_blocks(html):
    """Every structured data block the page carries, as it was written."""
    return [block.strip() for block in LD_SCRIPT.findall(html or "")]


def flatten_ld(data):
    out = []
    if isinstance(data, list):
        for row in data:
            out.extend(flatten_ld(row))
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            for row in graph:
                out.extend(flatten_ld(row))
            if set(data) <= {"@context", "@graph"}:
                return out
        out.append(data)
    return out


def json_ld(html):
    """Every typed item the page declares, with any @graph flattened out."""
    items = []
    for raw in ld_blocks(html):
        text = re.sub(r"(?s)^<!\[CDATA\[|\]\]>$", "", raw).strip()
        items.extend(flatten_ld(as_json(text)))
    return items


def ld_types(item):
    value = item.get("@type") or item.get("type") or ""
    values = value if isinstance(value, list) else [value]
    return [str(v).rsplit("/", 1)[-1] for v in values if v]


def has_type(item, *names):
    declared = {t.lower() for t in ld_types(item)}
    return any(name.lower() in declared for name in names)


def ld_text(value):
    """A property's value as one string, whichever shape the item used for it."""
    if isinstance(value, dict):
        for field in ("name", "url", "text", "value", "@id"):
            if value.get(field):
                return str(value[field])
        return " ".join(str(found) for key, found in value.items()
                        if isinstance(found, (str, int, float)) and not key.startswith("@"))
    if isinstance(value, list):
        return ", ".join(ld_text(row) for row in value if row)
    return str(value or "")


def address_text(value):
    """A postal address as one line, from whichever fields the item filled in."""
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    parts = [str(value.get(field, "")) for field in
             ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
    return ", ".join(part for part in parts if part) or ld_text(value)


def ld_urls(value):
    """Every URL a property carries, whether it is a string, an object or a list."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found = value.get("url") or value.get("contentUrl") or value.get("@id") or ""
        return [str(found)] if found else []
    if isinstance(value, list):
        out = []
        for row in value:
            out.extend(ld_urls(row))
        return out
    return []


PLACEHOLDER = re.compile(r"(?i)example\.(?:com|org)|lorem ipsum|your ?(?:company|business|name)"
                         r"|\bTODO\b|\bTBD\b|xxx-xxx|123-456-7890|\{\{|\bplaceholder\b")

# What Google needs before it will draw the result, by type.
REQUIRED = {
    "Organization": ("name", "url"),
    "LocalBusiness": ("name", "address", "telephone"),
    "Product": ("name", "offers"),
    "Article": ("headline", "datePublished"),
    "NewsArticle": ("headline", "datePublished"),
    "BlogPosting": ("headline", "datePublished"),
    "BreadcrumbList": ("itemListElement",),
    "Event": ("name", "startDate", "location"),
    "Recipe": ("name", "image", "recipeIngredient"),
    "VideoObject": ("name", "thumbnailUrl", "uploadDate"),
    "SoftwareApplication": ("name", "applicationCategory"),
    "WebSite": ("name", "url"),
    "FAQPage": ("mainEntity",),
}

# What it needs before the result is worth having.
RECOMMENDED = {
    "Organization": ("logo", "sameAs"),
    "LocalBusiness": ("openingHoursSpecification", "geo", "image", "priceRange"),
    "Product": ("image", "brand"),
    "Article": ("image", "author", "dateModified"),
    "NewsArticle": ("image", "author", "dateModified"),
    "BlogPosting": ("image", "author", "dateModified"),
    "Event": ("image", "offers"),
    "VideoObject": ("description", "duration"),
}

# The types Google renders a result for. Anything else is markup that earns nothing.
RENDERED = {
    "Organization", "LocalBusiness", "Restaurant", "Store", "Product", "ProductGroup",
    "Offer", "AggregateOffer", "Article", "NewsArticle", "BlogPosting", "BreadcrumbList",
    "Event", "Recipe", "VideoObject", "SoftwareApplication", "MobileApplication",
    "WebApplication", "WebSite", "WebPage", "Book", "Course", "Dataset", "JobPosting",
    "Movie", "Person", "QAPage", "Review", "AggregateRating", "ImageObject",
    "PostalAddress", "ContactPoint", "OpeningHoursSpecification", "GeoCoordinates",
    "ListItem", "SearchAction", "EntryPoint", "Service", "Place", "SiteNavigationElement",
}

# The page-shape types. They draw no result of their own and carry the items that
# do, so they are neither earning nor waste.
STRUCTURAL = {"WebPage", "CollectionPage", "AboutPage", "ContactPage", "ItemPage",
              "ProfilePage", "SearchResultsPage", "CheckoutPage", "Blog", "ItemList",
              "WebPageElement", "SiteNavigationElement", "Thing", "CreativeWork"}

# Marked up, validates, renders nothing: withdrawn or restricted out of reach.
RETIRED = {"FAQPage": "FAQ, restricted in 2023 to government and health sites",
           "HowTo": "HowTo, removed in 2023",
           "SpecialAnnouncement": "SpecialAnnouncement, withdrawn",
           "ClaimReview": "ClaimReview, withdrawn",
           "EstimatedSalary": "EstimatedSalary, withdrawn",
           "Course": "CourseInfo, withdrawn for sites outside the education pilot",
           "VehicleListing": "VehicleListing, withdrawn"}

# A local business is a subtype rather than the literal word, so the family is named.
LOCAL_TYPES = {"localbusiness", "restaurant", "store", "professionalservice", "homeandconstructionbusiness",
               "automotivebusiness", "healthandbeautybusiness", "foodestablishment",
               "medicalbusiness", "legalservice", "financialservice", "lodgingbusiness",
               "entertainmentbusiness", "sportsactivitylocation", "childcare", "dentist",
               "electrician", "plumber", "roofingcontractor", "generalcontractor"}


def is_local(item):
    return bool({t.lower() for t in ld_types(item)} & LOCAL_TYPES)


def image_size(data):
    """(width, height) read out of an image's own header, or None when it will not parse."""
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) > 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) > 30:
        chunk = data[12:16]
        if chunk == b"VP8X":
            return (int.from_bytes(data[24:27], "little") + 1,
                    int.from_bytes(data[27:30], "little") + 1)
        if chunk == b"VP8 ":
            return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                    int.from_bytes(data[28:30], "little") & 0x3FFF)
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            length = int.from_bytes(data[index + 2:index + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB,
                          0xCD, 0xCE, 0xCF):
                return (int.from_bytes(data[index + 7:index + 9], "big"),
                        int.from_bytes(data[index + 5:index + 7], "big"))
            index += 2 + max(length, 2)
    if data[:4] == b"\x00\x00\x01\x00" and len(data) > 8:
        return data[6] or 256, data[7] or 256
    text = data[:4000].decode("utf-8", "replace")
    if "<svg" in text:
        width = re.search(r"""width\s*=\s*["']?(\d+)""", text)
        height = re.search(r"""height\s*=\s*["']?(\d+)""", text)
        if width and height:
            return int(width.group(1)), int(height.group(1))
    return None


# ---------------------------------------------------------------- the Google calls


def bearer(token):
    return {"Authorization": "Bearer %s" % token}


def api(url, token, method="GET", data=None, timeout=TIMEOUT):
    """One authorised call. Returns (status, parsed body, raw text)."""
    status, body, _ = http(url, headers=bearer(token), method=method, data=data, timeout=timeout)
    return status, as_json(body), body


def api_error(status, body):
    """The status, plus whatever Google said was wrong with the call."""
    payload = as_json(body) if isinstance(body, str) else body
    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", ""))
        elif isinstance(error, str):
            message = error
    return "%s%s" % (status or "no answer", ": %s" % message[:180] if message else "")


def gsc_property(domain):
    """The Search Console property a domain maps to, a domain property by default."""
    prefix = env("GSC_SITE_PREFIX")
    if not prefix:
        return "sc-domain:%s" % domain
    if "{domain}" in prefix:
        return prefix.replace("{domain}", domain)
    if prefix.endswith((":", "/")):
        return "%s%s" % (prefix, domain)
    return prefix


def quoted(value):
    return urllib.parse.quote(str(value), safe="")


def ga4_property(domain):
    """The GA4 property id declared for a domain, bare of its resource prefix."""
    mapping = as_json(env("GA4_PROPERTY_MAP")) or {}
    value = mapping.get(domain) or mapping.get("www.%s" % domain) or ""
    return re.sub(r"^properties/", "", str(value)).strip()


def ago(days):
    """A date this many days back, in the form the Google APIs take."""
    return str((datetime.now(timezone.utc) - timedelta(days=days)).date())


def today():
    return str(datetime.now(timezone.utc).date())


PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"

FIELD_METRIC = {"LARGEST_CONTENTFUL_PAINT_MS": ("lcp", LCP_MS),
                "INTERACTION_TO_NEXT_PAINT": ("inp", INP_MS),
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": ("cls", CLS_SCORE)}


def psi(site, url, strategy, key):
    """One PageSpeed run, cached: the lab result and the field record arrive together."""
    slot = "psi:%s:%s" % (strategy, url)
    if slot in site.state:
        return site.state[slot]
    query = {"url": url, "strategy": strategy, "category": "performance"}
    if key:
        query["key"] = key
    status, body, _ = http("%s?%s" % (PSI_ENDPOINT, urllib.parse.urlencode(query)), timeout=SLOW)
    site.state[slot] = (status, as_json(body), body)
    return site.state[slot]


def field_record(payload, block="loadingExperience"):
    """The 75th percentile record for the URL, or for the origin, as Google reports it."""
    found = (payload or {}).get(block) or {}
    raw = found.get("metrics") or {}
    if not raw:
        return {}
    record = {"source": "page" if block == "loadingExperience" else "origin",
              "overall": found.get("overall_category", "")}
    for name, (short, _) in FIELD_METRIC.items():
        entry = raw.get(name) or {}
        if entry.get("percentile") is not None:
            value = entry["percentile"]
            record[short] = round(value / 100, 3) if short == "cls" else value
    return record


def field_verdicts(record):
    rows = []
    for _, (short, threshold) in FIELD_METRIC.items():
        if short in record:
            rows.append((short, record[short], threshold, record[short] <= threshold))
    return rows


def lab_metrics(payload):
    result = (payload or {}).get("lighthouseResult") or {}
    audits = result.get("audits") or {}
    out = {}
    for audit, short in (("largest-contentful-paint", "lcp"),
                         ("cumulative-layout-shift", "cls"),
                         ("total-blocking-time", "tbt"),
                         ("first-contentful-paint", "fcp")):
        value = (audits.get(audit) or {}).get("numericValue")
        if value is not None:
            out[short] = round(value, 3) if short == "cls" else round(value)
    score = ((result.get("categories") or {}).get("performance") or {}).get("score")
    if score is not None:
        out["score"] = round(score * 100)
    return out


def crux(site, key, form_factor=None):
    """The Chrome UX Report record for the origin, which is what the vitals report groups."""
    slot = "crux:%s" % (form_factor or "all")
    if slot in site.state:
        return site.state[slot]
    body = {"origin": site.resolve()}
    if form_factor:
        body["formFactor"] = form_factor
    status, text, _ = http("%s?key=%s" % (CRUX_ENDPOINT, quoted(key)), data=body,
                           method="POST", timeout=45)
    site.state[slot] = (status, as_json(text), text)
    return site.state[slot]


def search(query, count=10):
    """What a Programmable Search engine returns, which is the readable stand-in for a search."""
    key, engine = env("GOOGLE_CSE_KEY"), env("GOOGLE_CSE_CX")
    if not key or not engine:
        return None, ("GOOGLE_CSE_KEY and GOOGLE_CSE_CX are not set, so no search was run. "
                      "Read them from CryptoFort into this call rather than into a file.")
    params = urllib.parse.urlencode({"key": key, "cx": engine, "q": query, "num": count})
    status, body, _ = http("https://www.googleapis.com/customsearch/v1?%s" % params, timeout=30)
    payload = as_json(body)
    if status != 200 or payload is None:
        return None, "the search API answered %s" % api_error(status, body)
    return payload.get("items") or [], ""


# ---------------------------------------------------------------- search console

WEBMASTERS = "https://www.googleapis.com/webmasters/v3"
INSPECT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

GSC_NAMES = ("gsc.canonical_host", "gsc.domain_property", "gsc.hreflang_errors",
             "gsc.performance_rows", "gsc.sitemap_matches_robots", "gsc.sitemap_read",
             "gsc.sitemap_submitted", "gsc.verification_live")

HREFLANG_CODE = re.compile(r"^(?:x-default|[a-z]{2,3}(?:-[A-Za-z]{2,4})*)$")


def probe_canonical_host(probes, site):
    """Which host answers, and where each of the others finally lands.

    The first reading of every variant is taken without following anything, so a
    redirect stays visible as a redirect. Where it ends decides whether the four
    variants consolidate on one host; how many hops it took to get there is the
    redirect chain probe's question.
    """
    domain = site.domain
    answering, redirecting, silent = [], [], []
    for url in ("https://%s" % domain, "https://www.%s" % domain,
                "http://%s" % domain, "http://www.%s" % domain):
        status, body, headers = site.get(url, follow=False)
        if status == 200:
            answering.append(url)
        elif status in (301, 302, 303, 307, 308):
            chain = hops(site, url)
            redirecting.append((url, chain[-1][0] if chain else
                                absolute(headers.get("location"), url)))
        else:
            silent.append("%s (%s)" % (url, status or str(body)[:40]))
    aside = "; %d variant(s) serve nothing at all: %s" % (len(silent), listed(silent)) if silent \
        else ""
    if len(answering) > 1:
        probes.bad(domain, "gsc.canonical_host",
                   "%d hosts answer 200: %s. Two hosts serving the same pages split every "
                   "ranking signal between them%s" % (len(answering), listed(answering), aside))
        return
    if not answering:
        probes.bad(domain, "gsc.canonical_host",
                   "no host variant answers 200: %s" % listed(silent + [
                       "%s redirects to %s" % pair for pair in redirecting]))
        return
    winner = host_of(answering[0])
    stray = ["%s to %s" % (url, target or "nowhere") for url, target in redirecting
             if host_of(target or "") != winner]
    if stray:
        probes.bad(domain, "gsc.canonical_host",
                   "%s answers, and %d variant(s) redirect somewhere else: %s"
                   % (winner, len(stray), listed(stray)))
        return
    probes.ok(domain, "gsc.canonical_host",
              "%s answers 200 and the other %d variant(s) redirect to it%s"
              % (winner, len(redirecting), aside))


def probe_hreflang(probes, site):
    """The hreflang annotations the pages carry, judged on their own terms."""
    faults, pairs, pages = [], 0, 0
    for url, html, _ in site.live():
        tags = re.findall(r"(?is)<link\b[^>]*\brel\s*=\s*[\"']alternate[\"'][^>]*>", html)
        found = []
        for tag in tags:
            language = re.search(r"""(?is)\bhreflang\s*=\s*["']([^"']+)["']""", tag)
            href = re.search(r"""(?is)\bhref\s*=\s*["']([^"']+)["']""", tag)
            if not language or not href:
                continue
            found.append((language.group(1).strip(), href.group(1).strip()))
        if not found:
            continue
        pages += 1
        pairs += len(found)
        codes = [code for code, _ in found]
        for code, href in found:
            if not HREFLANG_CODE.match(code):
                faults.append("%s declares hreflang %r, which is not a language or region code"
                              % (path_of(url), code))
            if not href.startswith(("http://", "https://")):
                faults.append("%s points at %r, which is relative - Google reads absolute URLs "
                              "only" % (path_of(url), href[:40]))
        if not any(absolute(href, url).rstrip("/") == url.rstrip("/") for _, href in found):
            faults.append("%s lists %d alternates and none of them is itself" % (path_of(url),
                                                                                len(found)))
        if len(set(codes)) > 1 and "x-default" not in codes:
            faults.append("%s covers %s with no x-default" % (path_of(url), listed(sorted(set(codes)))))
    if not pairs:
        probes.ok(site.domain, "gsc.hreflang_errors",
                  "no page carries an hreflang annotation, so there is no international "
                  "targeting to be wrong")
        return
    if faults:
        probes.bad(site.domain, "gsc.hreflang_errors",
                   "%d fault(s) across %d annotated page(s): %s"
                   % (len(faults), pages, listed(faults, 4)))
        return
    probes.ok(site.domain, "gsc.hreflang_errors",
              "%d annotation(s) across %d page(s), each absolute, self-referencing and coded"
              % (pairs, pages))


def probe_gsc(probes, site, ctx):
    domain = site.domain
    probe_canonical_host(probes, site)
    probe_hreflang(probes, site)

    reached = ("gsc.domain_property", "gsc.verification_live", "gsc.sitemap_submitted",
               "gsc.sitemap_read", "gsc.sitemap_matches_robots", "gsc.performance_rows")
    token = env("GOOGLE_OAUTH_TOKEN")
    if not token:
        probes.need_all(domain, reached, "GOOGLE_OAUTH_TOKEN",
                        "Search Console was never read for this property")
        return
    prop = gsc_property(domain)
    ctx.setdefault("property", {})[domain] = prop

    status, data, raw = api("%s/sites" % WEBMASTERS, token)
    if status != 200 or not isinstance(data, dict):
        probes.bad_all(domain, reached, "the property list answered %s" % api_error(status, raw))
        return
    entries = data.get("siteEntry") or []
    properties = [str(row.get("siteUrl", "")) for row in entries]
    mine = [url for url in properties if domain in url]
    if prop in properties:
        probes.ok(domain, "gsc.domain_property",
                  "%s is on the account, alongside %d other propert(ies)"
                  % (prop, len(properties) - 1))
    else:
        probes.bad(domain, "gsc.domain_property",
                   "%s is not on the account. What it holds for this domain: %s. A URL-prefix "
                   "property misses the variants a domain property covers"
                   % (prop, listed(mine) or "nothing"))

    status, data, raw = api("%s/sites/%s" % (WEBMASTERS, quoted(prop)), token)
    level = (data or {}).get("permissionLevel", "") if isinstance(data, dict) else ""
    if status == 200 and level and level != "siteUnverifiedUser":
        probes.ok(domain, "gsc.verification_live", "%s answers with permission %s" % (prop, level))
    elif status == 200:
        probes.bad(domain, "gsc.verification_live",
                   "%s reports %s - the verification token has stopped resolving"
                   % (prop, level or "no permission level"))
    else:
        probes.bad(domain, "gsc.verification_live",
                   "%s answered %s" % (prop, api_error(status, raw)))

    status, data, raw = api("%s/sites/%s/sitemaps" % (WEBMASTERS, quoted(prop)), token)
    if status != 200 or not isinstance(data, dict):
        probes.bad_all(domain, ("gsc.sitemap_submitted", "gsc.sitemap_read",
                                "gsc.sitemap_matches_robots"),
                       "the sitemap list answered %s" % api_error(status, raw))
    else:
        held = data.get("sitemap") or []
        if not held:
            probes.bad_all(domain, ("gsc.sitemap_submitted", "gsc.sitemap_read"),
                           "no sitemap is submitted for %s, so Google discovers pages by "
                           "following links only" % prop)
        else:
            probes.ok(domain, "gsc.sitemap_submitted",
                      "%d sitemap(s) submitted: %s" % (len(held),
                                                       listed(row.get("path") for row in held)))
            broken, summary = [], []
            for row in held:
                errors = int(row.get("errors", 0) or 0)
                warnings = int(row.get("warnings", 0) or 0)
                submitted = sum(int(block.get("submitted", 0) or 0)
                                for block in (row.get("contents") or []))
                read = str(row.get("lastDownloaded", ""))
                age = days_since(read)
                summary.append("%s read %s, %d URLs, %d error(s), %d warning(s)"
                               % (row.get("path"), (read or "never")[:10], submitted, errors,
                                  warnings))
                if not read:
                    broken.append("%s has never been downloaded" % row.get("path"))
                elif errors:
                    broken.append("%s read %s with %d error(s)"
                                  % (row.get("path"), read[:10], errors))
                elif age is not None and age > 30:
                    broken.append("%s was last read %d days ago" % (row.get("path"), age))
            if broken:
                probes.bad(domain, "gsc.sitemap_read", listed(broken, 4))
            else:
                probes.ok(domain, "gsc.sitemap_read", listed(summary, 3))
        submitted_paths = {str(row.get("path", "")).rstrip("/") for row in held}
        declared = {url.rstrip("/") for url in site.sitemaps()}
        if not declared:
            probes.bad(domain, "gsc.sitemap_matches_robots",
                       "robots.txt names no sitemap, so the file Google holds (%s) is one "
                       "nothing on the site points at" % (listed(submitted_paths) or "none"))
        elif submitted_paths & declared:
            probes.ok(domain, "gsc.sitemap_matches_robots",
                      "%s is both submitted and named in robots.txt"
                      % listed(submitted_paths & declared))
        else:
            probes.bad(domain, "gsc.sitemap_matches_robots",
                       "Google holds %s and robots.txt names %s - two different files, and the "
                       "one Google reads is the stale one"
                       % (listed(submitted_paths) or "nothing", listed(declared)))

    status, data, raw = api(
        "%s/sites/%s/searchAnalytics/query" % (WEBMASTERS, quoted(prop)), token, method="POST",
        data={"startDate": ago(28), "endDate": today(), "dimensions": ["query"],
              "rowLimit": 25}, timeout=45)
    if status != 200 or not isinstance(data, dict):
        probes.bad(domain, "gsc.performance_rows",
                   "the performance query answered %s" % api_error(status, raw))
    else:
        rows = data.get("rows") or []
        clicks = sum(float(row.get("clicks", 0) or 0) for row in rows)
        impressions = sum(float(row.get("impressions", 0) or 0) for row in rows)
        if not rows:
            probes.bad(domain, "gsc.performance_rows",
                       "no search traffic in the last 28 days on %s - either nothing is indexed "
                       "or the property covers a host nobody visits" % prop)
        else:
            probes.ok(domain, "gsc.performance_rows",
                      "%d quer(ies) in 28 days, %d impression(s), %d click(s); top: %s"
                      % (len(rows), impressions, clicks,
                         listed([r.get("keys", [""])[0] for r in rows[:3]], 3)))


# ---------------------------------------------------------------- indexing

IDX_NAMES = ("idx.canonical_match", "idx.crawled_not_indexed", "idx.discovered_not_indexed",
             "idx.indexed_status", "idx.last_crawl_age", "idx.preview_noindex",
             "idx.redirect_chain", "idx.robots_verdict", "idx.sitemap_url_status",
             "idx.soft_404")

MISSING_PATH = "/probe-a-page-that-cannot-exist-9f3a2c"


def probe_robots_verdict(probes, site):
    """What robots.txt tells Googlebot about the pages that have to be reachable."""
    status, body, _ = site.robots()
    origin = site.resolve()
    if status == 404:
        probes.ok(site.domain, "idx.robots_verdict",
                  "no robots.txt, so nothing is blocked; every page is crawlable")
        return
    if status != 200:
        probes.bad(site.domain, "idx.robots_verdict",
                   "%s/robots.txt answered %s, so Googlebot's verdict could not be read"
                   % (origin, status or str(body)[:60]))
        return
    rules = robots_rules(body, "googlebot")
    checked = [origin] + [url for url, _, _ in site.live()[1:]] + site.sitemaps()
    blocked = [path_of(url) for url in checked if not robots_allows(rules, path_of(url))]
    groups = sorted(robots_groups(body))
    if blocked:
        probes.bad(site.domain, "idx.robots_verdict",
                   "robots.txt blocks Googlebot from %d of the %d address(es) that have to be "
                   "reachable: %s" % (len(blocked), len(checked), listed(blocked)))
        return
    probes.ok(site.domain, "idx.robots_verdict",
              "Googlebot is allowed every one of the %d address(es) checked, under %d rule(s) "
              "for agent(s) %s" % (len(checked), len(rules), listed(groups, 4)))


def probe_redirect_chain(probes, site):
    """How many hops each entry point takes before something answers."""
    starts = ["http://%s" % site.domain, "http://www.%s" % site.domain,
              "https://%s" % site.domain, "https://www.%s" % site.domain]
    long_chains, worst = [], 0
    for start in starts:
        chain = hops(site, start)
        redirects = [row for row in chain if row[2]]
        worst = max(worst, len(redirects))
        if len(redirects) > 1:
            long_chains.append("%s takes %d hops (%s)"
                               % (start, len(redirects),
                                  " then ".join(str(row[2]) for row in redirects)))
        elif chain and chain[-1][1] not in (200, 301, 302, 303, 307, 308) and not redirects:
            long_chains.append("%s answers %s" % (start, chain[-1][1] or "nothing"))
    if long_chains:
        probes.bad(site.domain, "idx.redirect_chain",
                   "%d entry point(s) do not resolve in one hop: %s. Each extra hop is a crawl "
                   "Google spends on nothing" % (len(long_chains), listed(long_chains, 3)))
        return
    probes.ok(site.domain, "idx.redirect_chain",
              "every one of the %d entry points resolves in at most one hop" % len(starts))


def probe_sitemap_url_status(probes, site):
    """Whether the URLs the sitemap lists are the ones the site actually serves."""
    urls = site.sitemap_urls()
    if not urls:
        probes.bad(site.domain, "idx.sitemap_url_status",
                   "no sitemap was found at %s or in robots.txt, so there are no URLs to check"
                   % ("%s/sitemap.xml" % site.resolve()))
        return
    sample = urls[:SITEMAP_SAMPLE]
    faults = []
    for url in sample:
        status, _, headers = site.get(url, follow=False)
        if status == 200:
            continue
        if status in (301, 302, 303, 307, 308):
            faults.append("%s redirects to %s" % (path_of(url),
                                                  path_of(headers.get("location") or "")))
        else:
            faults.append("%s answers %s" % (path_of(url), status or "nothing"))
    if faults:
        probes.bad(site.domain, "idx.sitemap_url_status",
                   "%d of %d sampled sitemap URL(s) do not answer 200: %s"
                   % (len(faults), len(sample), listed(faults, 5)))
        return
    probes.ok(site.domain, "idx.sitemap_url_status",
              "all %d sampled URL(s) of the %d listed answer 200" % (len(sample), len(urls)))


def probe_preview_noindex(probes, site):
    """Every place a noindex can be declared, read off the pages that are serving."""
    refusing = []
    for url, html, headers in site.live():
        header = str(headers.get("x-robots-tag", ""))
        if "noindex" in header.lower():
            refusing.append("%s carries X-Robots-Tag: %s" % (path_of(url), header[:60]))
        for tag in re.findall(r"(?is)<meta\b[^>]*\bname\s*=\s*[\"'](?:robots|googlebot)[\"'][^>]*>",
                              html):
            if "noindex" in tag.lower():
                refusing.append("%s carries %s" % (path_of(url), re.sub(r"\s+", " ", tag)[:70]))
    if refusing:
        probes.bad(site.domain, "idx.preview_noindex",
                   "%d page(s) on %s refuse indexing: %s" % (len(refusing), site.resolve(),
                                                             listed(refusing, 4)))
        return
    probes.ok(site.domain, "idx.preview_noindex",
              "none of the %d page(s) read on %s declares noindex, in markup or in a header"
              % (len(site.live()), site.resolve()))


def probe_soft_404(probes, site):
    """What a URL that cannot exist answers."""
    target = site.resolve() + MISSING_PATH
    chain = hops(site, target)
    final = chain[-1] if chain else (target, 0, "")
    redirects = [row for row in chain if row[2]]
    if final[1] in (404, 410):
        probes.ok(site.domain, "idx.soft_404",
                  "a URL that cannot exist answers %d%s" % (final[1],
                                                            " after a redirect" if redirects else ""))
        return
    if final[1] == 200:
        probes.bad(site.domain, "idx.soft_404",
                   "a URL that cannot exist answers 200%s - Google files that as a soft 404 and "
                   "spends crawls rediscovering it"
                   % (" at %s" % path_of(final[0]) if redirects else ""))
        return
    probes.bad(site.domain, "idx.soft_404",
               "a URL that cannot exist answers %s, which is neither 404 nor 410"
               % (final[1] or "nothing"))


def inspections(site, token, prop):
    """The URL Inspection reading for the home page and the routes it links, cached."""
    if "inspect" in site.state:
        return site.state["inspect"]
    out = []
    for url, _, _ in site.live()[:5]:
        status, data, raw = api(INSPECT, token, method="POST",
                                data={"inspectionUrl": url, "siteUrl": prop}, timeout=60)
        if status != 200 or not isinstance(data, dict):
            out.append((url, None, api_error(status, raw)))
            continue
        result = (data.get("inspectionResult") or {}).get("indexStatusResult") or {}
        out.append((url, result, ""))
    site.state["inspect"] = out
    return out


def probe_idx(probes, site, ctx):
    domain = site.domain
    probe_robots_verdict(probes, site)
    probe_redirect_chain(probes, site)
    probe_sitemap_url_status(probes, site)
    probe_preview_noindex(probes, site)
    probe_soft_404(probes, site)

    inspected = ("idx.indexed_status", "idx.canonical_match", "idx.discovered_not_indexed",
                 "idx.crawled_not_indexed", "idx.last_crawl_age")
    token = env("GOOGLE_OAUTH_TOKEN")
    if not token:
        probes.need_all(domain, inspected, "GOOGLE_OAUTH_TOKEN",
                        "no URL was inspected and what Google holds is unknown")
        return
    prop = gsc_property(domain)
    rows = inspections(site, token, prop)
    if not rows:
        probes.bad_all(domain, inspected,
                       "no page on %s answered 200, so there was nothing to inspect" % domain)
        return
    refused = [(url, why) for url, result, why in rows if result is None]
    if len(refused) == len(rows):
        probes.bad_all(domain, inspected,
                       "the URL Inspection API refused every request for %s: %s"
                       % (prop, refused[0][1]))
        return
    read = [(url, result) for url, result, _ in rows if result is not None]

    unindexed = ["%s: %s" % (path_of(url), result.get("coverageState", "no coverage state"))
                 for url, result in read if str(result.get("verdict", "")).upper() != "PASS"]
    if unindexed:
        probes.bad(domain, "idx.indexed_status",
                   "%d of %d inspected page(s) are not indexed: %s"
                   % (len(unindexed), len(read), listed(unindexed, 4)))
    else:
        probes.ok(domain, "idx.indexed_status",
                  "all %d inspected page(s) report indexed: %s"
                  % (len(read), listed({r.get("coverageState", "") for _, r in read}, 3)))

    split = ["%s: Google chose %s over %s" % (path_of(url), result.get("googleCanonical", "nothing"),
                                              result.get("userCanonical", "nothing"))
             for url, result in read
             if result.get("googleCanonical") and result.get("userCanonical")
             and str(result["googleCanonical"]).rstrip("/") != str(result["userCanonical"]).rstrip("/")]
    if split:
        probes.bad(domain, "idx.canonical_match",
                   "%d page(s) rank under an address the site did not choose: %s"
                   % (len(split), listed(split, 3)))
    else:
        probes.ok(domain, "idx.canonical_match",
                  "Google's chosen canonical matches the declared one on all %d inspected page(s)"
                  % len(read))

    def coverage(fragment):
        return ["%s: %s" % (path_of(url), result.get("coverageState", ""))
                for url, result in read
                if fragment in str(result.get("coverageState", "")).lower()
                and "not indexed" in str(result.get("coverageState", "")).lower()]

    for name, fragment, meaning in (
            ("idx.discovered_not_indexed", "discovered",
             "Google knows the URL and has chosen not to spend a crawl on it"),
            ("idx.crawled_not_indexed", "crawled",
             "Google read the page and declined to index it, which points at the content")):
        stuck = coverage(fragment)
        if stuck:
            probes.bad(domain, name, "%d page(s) sit there: %s. %s"
                       % (len(stuck), listed(stuck, 3), meaning))
        else:
            probes.ok(domain, name, "no inspected page carries that state, across %d page(s)"
                      % len(read))

    ages = [(days_since(result.get("lastCrawlTime")), url) for url, result in read]
    known = [(age, url) for age, url in ages if age is not None]
    if not known:
        probes.bad(domain, "idx.last_crawl_age",
                   "no inspected page reports a last crawl time, so Google has not fetched any "
                   "of them")
    else:
        oldest = max(known)
        if oldest[0] > CRAWL_STALE_DAYS:
            probes.bad(domain, "idx.last_crawl_age",
                       "the stalest of %d page(s) was crawled %d days ago (%s) - a change landed "
                       "since then is not in the index"
                       % (len(known), oldest[0], path_of(oldest[1])))
        else:
            probes.ok(domain, "idx.last_crawl_age",
                      "every inspected page was crawled within %d days, the stalest %d days ago"
                      % (CRAWL_STALE_DAYS, oldest[0]))


# ---------------------------------------------------------------- analytics

ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
ADMIN_ALPHA = "https://analyticsadmin.googleapis.com/v1alpha"
DATA = "https://analyticsdata.googleapis.com/v1beta"

GA4_NAMES = ("ga4.consent_mode", "ga4.data_retention", "ga4.deployment_uniform",
             "ga4.form_key_event", "ga4.internal_traffic_filter", "ga4.key_events",
             "ga4.local_key_events", "ga4.no_universal_analytics",
             "ga4.pageview_double_count", "ga4.product_links", "ga4.property_exists",
             "ga4.purchase_event_shape", "ga4.recent_events", "ga4.tag_singleton")

GTAG_LOADER = re.compile(r"googletagmanager\.com/gtag/js\?id=([A-Za-z0-9\-]+)", re.I)
GTM_LOADER = re.compile(r"googletagmanager\.com/gtm\.js\?id=(GTM-[A-Za-z0-9]+)", re.I)
GTAG_CONFIG = re.compile(r"""(?i)gtag\(\s*['"]config['"]\s*,\s*['"]([A-Za-z0-9\-]+)['"]""")
UNIVERSAL = re.compile(r"""(?i)google-analytics\.com/(?:analytics|ga)\.js"""
                       r"""|\bUA-\d{4,}-\d{1,3}\b|\bga\(\s*['"]create['"]""")
CONSENT_DEFAULT = re.compile(r"""(?i)gtag\(\s*['"]consent['"]\s*,\s*['"]default['"]""")
TAG_LOADER = re.compile(r"(?i)googletagmanager\.com/(?:gtag/js|gtm\.js)")

FORM_EVENTS = {"generate_lead", "form_submit", "form_submission", "contact", "form_start",
               "submit_form", "lead"}
LOCAL_EVENTS = {"click_to_call", "call_click", "phone_call", "tel_click", "get_directions",
                "directions", "directions_click", "map_click"}
LONG_RETENTION = {"FOURTEEN_MONTHS", "TWENTY_FIVE_MONTHS", "THIRTY_EIGHT_MONTHS", "FIFTY_MONTHS"}


def tag_reading(site):
    """The measurement tags each page carries, read once for the whole family."""
    if "tags" in site.state:
        return site.state["tags"]
    rows = []
    for url, html, _ in site.live():
        loaders = [found for found in GTAG_LOADER.findall(html)]
        containers = GTM_LOADER.findall(html)
        configs = GTAG_CONFIG.findall(html)
        consent = CONSENT_DEFAULT.search(html)
        first_tag = TAG_LOADER.search(html)
        rows.append({
            "url": url,
            "loaders": loaders,
            "containers": containers,
            "configs": configs,
            "ids": sorted({found for found in loaders + configs
                           if found.upper().startswith("G-")}),
            "universal": sorted(set(UNIVERSAL.findall(html)))[:3],
            "universal_seen": bool(UNIVERSAL.search(html)),
            "consent": bool(consent),
            "consent_first": bool(consent and first_tag and consent.start() < first_tag.start()),
            "shape": "gtm" if containers else "gtag" if loaders or configs else "none",
        })
    site.state["tags"] = rows
    return rows


def probe_ga4_page(probes, site, ctx):
    """What the delivered markup says about measurement, before any account is opened."""
    domain = site.domain
    rows = tag_reading(site)
    if not rows:
        probes.bad_all(domain, ("ga4.tag_singleton", "ga4.pageview_double_count",
                                "ga4.no_universal_analytics", "ga4.consent_mode"),
                       "no page on %s answered 200, so the markup was never read" % domain)
        ctx.setdefault("shape", {})[domain] = "unread"
        return

    ids = sorted({found for row in rows for found in row["ids"]})
    doubled = [row for row in rows if len(row["ids"]) > 1
               or len(row["loaders"]) > len(set(row["loaders"]))]
    untagged = [row for row in rows if not row["ids"] and not row["containers"]]
    if len(ids) > 1:
        probes.bad(domain, "ga4.tag_singleton",
                   "%d measurement IDs across %d page(s): %s. Two IDs on one page count every "
                   "visit twice and split the reporting"
                   % (len(ids), len(rows), listed(ids)))
    elif doubled:
        probes.bad(domain, "ga4.tag_singleton",
                   "%d page(s) load the tag more than once: %s"
                   % (len(doubled), listed(path_of(row["url"]) for row in doubled)))
    elif untagged and len(untagged) < len(rows):
        probes.bad(domain, "ga4.tag_singleton",
                   "%d of %d page(s) carry no measurement tag: %s"
                   % (len(untagged), len(rows), listed(path_of(row["url"]) for row in untagged)))
    elif not ids and not any(row["containers"] for row in rows):
        probes.bad(domain, "ga4.tag_singleton",
                   "no measurement tag on any of the %d page(s) read - nothing is being counted"
                   % len(rows))
    else:
        probes.ok(domain, "ga4.tag_singleton",
                  "one measurement ID (%s) on each of the %d page(s) read"
                  % (listed(ids) or listed({c for row in rows for c in row["containers"]}),
                     len(rows)))

    both = [row for row in rows if row["containers"] and row["configs"]]
    repeated = [row for row in rows if len(row["configs"]) > len(set(row["configs"]))]
    if both:
        probes.bad(domain, "ga4.pageview_double_count",
                   "%d page(s) carry a Tag Manager container and a direct gtag config: %s. Both "
                   "send a pageview unless one is switched off"
                   % (len(both), listed(path_of(row["url"]) for row in both)))
    elif repeated:
        probes.bad(domain, "ga4.pageview_double_count",
                   "%d page(s) configure the same measurement ID twice: %s"
                   % (len(repeated), listed(path_of(row["url"]) for row in repeated)))
    else:
        probes.ok(domain, "ga4.pageview_double_count",
                  "one pageview path per page across the %d page(s) read" % len(rows))

    legacy = [row for row in rows if row["universal_seen"]]
    if legacy:
        probes.bad(domain, "ga4.no_universal_analytics",
                   "%d page(s) still carry Universal Analytics markup (%s), which has collected "
                   "nothing since 2023 and loads on every visit"
                   % (len(legacy), listed(legacy[0]["universal"], 2)))
    else:
        probes.ok(domain, "ga4.no_universal_analytics",
                  "no Universal Analytics markup on any of the %d page(s) read" % len(rows))

    tagged = [row for row in rows if row["shape"] != "none"]
    late = [row for row in tagged if row["consent"] and not row["consent_first"]]
    without = [row for row in tagged if not row["consent"]]
    if not tagged:
        probes.ok(domain, "ga4.consent_mode",
                  "no measurement tag loads, so there is no consent default to place")
    elif without:
        probes.bad(domain, "ga4.consent_mode",
                   "%d of %d tagged page(s) load the tag with no consent default: %s"
                   % (len(without), len(tagged),
                      listed(path_of(row["url"]) for row in without)))
    elif late:
        probes.bad(domain, "ga4.consent_mode",
                   "%d page(s) declare consent after the tag has already loaded: %s"
                   % (len(late), listed(path_of(row["url"]) for row in late)))
    else:
        probes.ok(domain, "ga4.consent_mode",
                  "every tagged page declares a consent default before the tag loads")

    shapes = {row["shape"] for row in rows}
    ctx.setdefault("shape", {})[domain] = "mixed" if len(shapes) > 1 else shapes.pop()


def finish_ga4(probes, ctx):
    """One deployment shape across the portfolio, which only the whole run can answer."""
    shapes = ctx.get("shape") or {}
    for domain, shape in shapes.items():
        others = {name: value for name, value in shapes.items() if name != domain}
        if shape == "mixed":
            probes.bad(domain, "ga4.deployment_uniform",
                       "this site's own pages disagree: some load Tag Manager and some load "
                       "gtag directly")
            continue
        if not others:
            probes.bad(domain, "ga4.deployment_uniform",
                       "only %s was in this run, so its %s deployment was compared against "
                       "nothing. Run every site together for this one" % (domain, shape))
            continue
        odd = sorted({value for value in others.values()} - {shape})
        if odd:
            probes.bad(domain, "ga4.deployment_uniform",
                       "%s deploys by %s and the rest of the run deploys by %s - one shape per "
                       "portfolio is what makes a change repeatable"
                       % (domain, shape, listed(odd, 3)))
        else:
            probes.ok(domain, "ga4.deployment_uniform",
                      "%s and the other %d site(s) all deploy by %s"
                      % (domain, len(others), shape))


def probe_ga4(probes, site, ctx):
    domain = site.domain
    probe_ga4_page(probes, site, ctx)

    account = ("ga4.property_exists", "ga4.recent_events", "ga4.key_events",
               "ga4.form_key_event", "ga4.local_key_events", "ga4.purchase_event_shape",
               "ga4.product_links", "ga4.data_retention", "ga4.internal_traffic_filter")
    token = env("GOOGLE_OAUTH_TOKEN")
    on_page = sorted({found for row in tag_reading(site) for found in row["ids"]})
    if not token:
        probes.need_all(domain, account, "GOOGLE_OAUTH_TOKEN",
                        "the property behind the tag on the page (%s) was never opened"
                        % (listed(on_page) or "no tag found"))
        return
    prop = ga4_property(domain)
    if not prop:
        probes.bad_all(domain, account,
                       "GA4_PROPERTY_MAP names no property for %s, so there is nothing to read. "
                       "The page carries %s" % (domain, listed(on_page) or "no measurement ID"))
        return

    status, data, raw = api("%s/properties/%s" % (ADMIN, prop), token)
    if status != 200 or not isinstance(data, dict):
        probes.bad_all(domain, account,
                       "property %s answered %s" % (prop, api_error(status, raw)))
        return
    name = data.get("displayName", prop)
    scode, streams, sraw = api("%s/properties/%s/dataStreams" % (ADMIN, prop), token)
    measurement = [str((row.get("webStreamData") or {}).get("measurementId", ""))
                   for row in ((streams or {}).get("dataStreams") or [])]
    matched = [found for found in on_page if found in measurement]
    if on_page and measurement and not matched:
        probes.bad(domain, "ga4.property_exists",
                   "property %s (%s) has stream(s) %s and the page carries %s - the tag feeds a "
                   "different property" % (prop, name, listed(measurement), listed(on_page)))
    elif not on_page:
        probes.bad(domain, "ga4.property_exists",
                   "property %s (%s) exists with stream(s) %s and no page carries a measurement "
                   "tag" % (prop, name, listed(measurement) or "none"))
    else:
        probes.ok(domain, "ga4.property_exists",
                  "property %s (%s) serves %s, which is the ID on the page"
                  % (prop, name, listed(matched or measurement)))

    status, data, raw = api("%s/properties/%s:runReport" % (DATA, prop), token, method="POST",
                            data={"dateRanges": [{"startDate": "7daysAgo", "endDate": "today"}],
                                  "dimensions": [{"name": "eventName"}],
                                  "metrics": [{"name": "eventCount"}], "limit": 25}, timeout=45)
    rows = (data or {}).get("rows") or [] if isinstance(data, dict) else []
    events = {row["dimensionValues"][0]["value"]: int(row["metricValues"][0]["value"] or 0)
              for row in rows if row.get("dimensionValues") and row.get("metricValues")}
    if status != 200:
        probes.bad(domain, "ga4.recent_events",
                   "the report query answered %s" % api_error(status, raw))
    elif not events:
        probes.bad(domain, "ga4.recent_events",
                   "no event has arrived in property %s in seven days - the tag is on the page "
                   "and nothing is reaching the property" % prop)
    else:
        probes.ok(domain, "ga4.recent_events",
                  "%d event type(s) in seven days, %d hit(s) in all; top: %s"
                  % (len(events), sum(events.values()),
                     listed(["%s (%d)" % pair for pair in
                             sorted(events.items(), key=lambda kv: -kv[1])[:3]], 3)))

    status, data, raw = api("%s/properties/%s/keyEvents" % (ADMIN, prop), token)
    key_events = [str(row.get("eventName", "")) for row in ((data or {}).get("keyEvents") or [])] \
        if isinstance(data, dict) else []
    if status != 200:
        probes.bad_all(domain, ("ga4.key_events", "ga4.form_key_event", "ga4.local_key_events"),
                       "the key event list answered %s" % api_error(status, raw))
    else:
        if key_events:
            probes.ok(domain, "ga4.key_events",
                      "%d key event(s) defined: %s" % (len(key_events), listed(key_events)))
        else:
            probes.bad(domain, "ga4.key_events",
                       "no key event is defined on %s, so every visit counts the same and "
                       "nothing reports what the business actually wanted" % prop)
        forms = [name for name in key_events if name.lower() in FORM_EVENTS]
        if forms:
            probes.ok(domain, "ga4.form_key_event", "form submissions count as %s" % listed(forms))
        else:
            probes.bad(domain, "ga4.form_key_event",
                       "none of the %d key event(s) counts a form submission (looking for %s)"
                       % (len(key_events), listed(sorted(FORM_EVENTS), 4)))
        local = [name for name in key_events if name.lower() in LOCAL_EVENTS]
        if local:
            probes.ok(domain, "ga4.local_key_events",
                      "call clicks and direction requests count as %s" % listed(local))
        else:
            probes.bad(domain, "ga4.local_key_events",
                       "no key event counts a call click or a direction request (looking for %s)"
                       % listed(sorted(LOCAL_EVENTS), 4))

    purchases = events.get("purchase", 0)
    if status == 200 and not purchases:
        probes.bad(domain, "ga4.purchase_event_shape",
                   "no purchase event reached %s in seven days, so its shape could not be read"
                   % prop)
    else:
        pstatus, pdata, praw = api(
            "%s/properties/%s:runReport" % (DATA, prop), token, method="POST",
            data={"dateRanges": [{"startDate": "28daysAgo", "endDate": "today"}],
                  "dimensions": [{"name": "eventName"}],
                  "metrics": [{"name": "eventCount"}, {"name": "totalRevenue"},
                              {"name": "itemsPurchased"}],
                  "dimensionFilter": {"filter": {"fieldName": "eventName",
                                                 "stringFilter": {"value": "purchase"}}}},
            timeout=45)
        prow = ((pdata or {}).get("rows") or [{}])[0] if isinstance(pdata, dict) else {}
        values = [value.get("value", "0") for value in prow.get("metricValues", [])]
        count, revenue, items = (values + ["0", "0", "0"])[:3]
        if pstatus != 200:
            probes.bad(domain, "ga4.purchase_event_shape",
                       "the purchase query answered %s" % api_error(pstatus, praw))
        elif float(revenue or 0) <= 0 or float(items or 0) <= 0:
            probes.bad(domain, "ga4.purchase_event_shape",
                       "%s purchase(s) in 28 days carry revenue %s and %s item(s) - a purchase "
                       "with no value or no items is a page load being counted"
                       % (count, revenue, items))
        else:
            probes.ok(domain, "ga4.purchase_event_shape",
                      "%s purchase(s) in 28 days carrying %s in revenue across %s item(s)"
                      % (count, revenue, items))

    astatus, adata, araw = api("%s/properties/%s/googleAdsLinks" % (ADMIN, prop), token)
    ads = (adata or {}).get("googleAdsLinks") or [] if isinstance(adata, dict) else []
    cstatus, cdata, craw = api("%s/properties/%s/searchConsoleLinks" % (ADMIN_ALPHA, prop), token)
    console = (cdata or {}).get("searchConsoleLinks") or [] if isinstance(cdata, dict) else []
    if astatus == 200 and cstatus == 200 and ads and console:
        probes.ok(domain, "ga4.product_links",
                  "%d Ads link(s) and %d Search Console link(s) on %s"
                  % (len(ads), len(console), prop))
    else:
        faults = []
        if astatus != 200:
            faults.append("the Ads link list answered %s" % api_error(astatus, araw))
        elif not ads:
            faults.append("no Google Ads link, so campaign traffic arrives unattributed")
        if cstatus != 200:
            faults.append("the Search Console link list answered %s - where the Admin API does "
                          "not serve it, read the link under Property Settings > Product Links"
                          % api_error(cstatus, craw))
        elif not console:
            faults.append("no Search Console link, so queries never reach the property")
        probes.bad(domain, "ga4.product_links", listed(faults, 3))

    status, data, raw = api("%s/properties/%s/dataRetentionSettings" % (ADMIN, prop), token)
    retention = str((data or {}).get("eventDataRetention", "")) if isinstance(data, dict) else ""
    if status != 200:
        probes.bad(domain, "ga4.data_retention",
                   "the retention setting answered %s" % api_error(status, raw))
    elif retention in LONG_RETENTION:
        probes.ok(domain, "ga4.data_retention",
                  "event data is kept for %s" % retention.replace("_", " ").lower())
    else:
        probes.bad(domain, "ga4.data_retention",
                   "event data is kept for %s - the default throws away the history every "
                   "year-on-year comparison needs"
                   % (retention.replace("_", " ").lower() or "an unreported period"))

    status, data, raw = api("%s/properties/%s/dataFilters" % (ADMIN_ALPHA, prop), token)
    filters = (data or {}).get("dataFilters") or [] if isinstance(data, dict) else []
    internal = [row for row in filters
                if "internal" in json.dumps(row).lower() and row.get("state") == "ACTIVE"]
    if status != 200:
        probes.bad(domain, "ga4.internal_traffic_filter",
                   "the data filter list answered %s - where the Admin API does not serve it, "
                   "read Admin > Data Settings > Data Filters" % api_error(status, raw))
    elif internal:
        probes.ok(domain, "ga4.internal_traffic_filter",
                  "%d active internal traffic filter(s) on %s" % (len(internal), prop))
    else:
        probes.bad(domain, "ga4.internal_traffic_filter",
                   "%d data filter(s) and none of them excludes internal traffic - every visit "
                   "the people who build the site make is in the numbers" % len(filters))


# ---------------------------------------------------------------- business profile

ACCOUNTS = "https://mybusinessaccountmanagement.googleapis.com/v1"
INFO = "https://mybusinessbusinessinformation.googleapis.com/v1"
VERIFY = "https://mybusinessverifications.googleapis.com/v1"
QANDA = "https://mybusinessqanda.googleapis.com/v1"
LEGACY = "https://mybusiness.googleapis.com/v4"
PERFORMANCE = "https://businessprofileperformance.googleapis.com/v1"

READ_MASK = ("name,title,storefrontAddress,phoneNumbers,categories,regularHours,specialHours,"
             "websiteUri,serviceArea,profile,serviceItems,metadata,openInfo,latlng")

GBP_NAMES = ("gbp.categories", "gbp.description", "gbp.duplicates", "gbp.hours", "gbp.links",
             "gbp.messaging", "gbp.name", "gbp.nap", "gbp.owners", "gbp.performance",
             "gbp.photos", "gbp.photos.interior", "gbp.posts", "gbp.questions",
             "gbp.reviews", "gbp.service_area", "gbp.services", "gbp.verified")


def business_profile(site, token):
    """The listing that names this site, found once and shared by the whole family.

    Returns (location, account, siblings, reason). The siblings are every other
    location on the same accounts, which is what a duplicate is read against.
    """
    if "gbp" in site.state:
        return site.state["gbp"]
    status, data, raw = api("%s/accounts" % ACCOUNTS, token)
    if status != 200 or not isinstance(data, dict):
        site.state["gbp"] = (None, "", [], "the account list answered %s" % api_error(status, raw))
        return site.state["gbp"]
    accounts = [str(row.get("name", "")) for row in (data.get("accounts") or [])]
    if not accounts:
        site.state["gbp"] = (None, "", [], "the token reaches no Business Profile account")
        return site.state["gbp"]
    everything, found, owner = [], None, ""
    for account in accounts[:5]:
        status, data, raw = api("%s/%s/locations?readMask=%s&pageSize=100"
                                % (INFO, account, quoted(READ_MASK)), token)
        if status != 200 or not isinstance(data, dict):
            continue
        for location in data.get("locations") or []:
            location["_account"] = account
            everything.append(location)
            website = host_of(str(location.get("websiteUri", "")))
            if website and stem(website) == site.domain and found is None:
                found, owner = location, account
    if found is None:
        site.state["gbp"] = (None, "", everything,
                             "none of the %d location(s) on %d account(s) points at %s"
                             % (len(everything), len(accounts), site.domain))
        return site.state["gbp"]
    site.state["gbp"] = (found, owner, everything, "")
    return site.state["gbp"]


def probe_gbp(probes, site, ctx):
    domain = site.domain
    token = env("GOOGLE_OAUTH_TOKEN")
    if not token:
        probes.need_all(domain, GBP_NAMES, "GOOGLE_OAUTH_TOKEN",
                        "the Business Profile was never opened")
        return
    location, account, everything, reason = business_profile(site, token)
    if location is None:
        probes.bad_all(domain, GBP_NAMES, "%s. A listing nobody can read is a listing nobody "
                                          "is maintaining" % reason)
        return
    name = str(location.get("name", ""))
    legacy = "%s/%s" % (account, name) if account and not name.startswith("accounts/") else name
    title = str(location.get("title", ""))

    status, data, raw = api("%s/%s/VoiceOfMerchantState" % (VERIFY, name), token)
    state = data if isinstance(data, dict) else {}
    metadata = location.get("metadata") or {}
    if status == 200 and state.get("hasVoiceOfMerchant"):
        probes.ok(domain, "gbp.verified",
                  "%s is verified and unrestricted, opening state %s"
                  % (title, (location.get("openInfo") or {}).get("status", "unreported")))
    elif status == 200:
        problem = json.dumps({k: v for k, v in state.items() if k != "hasVoiceOfMerchant"})[:150]
        probes.bad(domain, "gbp.verified",
                   "%s does not hold Voice of Merchant, so it is not showing as a claimed "
                   "listing: %s" % (title, problem))
    else:
        probes.bad(domain, "gbp.verified",
                   "the verification state answered %s; the listing reports duplicate=%s, "
                   "suspended=%s" % (api_error(status, raw),
                                     bool(metadata.get("duplicateLocation")),
                                     not metadata.get("canOperateLocalPost", True)))

    status, data, raw = api("%s/%s/admins" % (ACCOUNTS, name), token)
    admins = (data or {}).get("admins") or [] if isinstance(data, dict) else []
    roles = [str(row.get("role", "")) for row in admins]
    if status != 200:
        probes.bad(domain, "gbp.owners", "the admin list answered %s" % api_error(status, raw))
    elif "PRIMARY_OWNER" in roles and len(admins) > 1:
        probes.ok(domain, "gbp.owners",
                  "%d admin(s) on %s: %s" % (len(admins), title, listed(roles)))
    else:
        probes.bad(domain, "gbp.owners",
                   "%s carries %d admin(s) (%s) - ownership that sits with one account ends when "
                   "that account does" % (title, len(admins), listed(roles) or "none"))

    address = json.dumps(location.get("storefrontAddress") or {})
    twins = [row for row in everything
             if row is not location
             and (str(row.get("title", "")).strip().lower() == title.strip().lower()
                  or json.dumps(row.get("storefrontAddress") or {}) == address != "{}")]
    if metadata.get("duplicateLocation"):
        probes.bad(domain, "gbp.duplicates",
                   "%s reports a duplicate at %s" % (title, metadata.get("duplicateLocation")))
    elif twins:
        probes.bad(domain, "gbp.duplicates",
                   "%d other listing(s) share this name or address: %s. Duplicates split the "
                   "reviews and the ranking" % (len(twins), listed(r.get("name") for r in twins)))
    else:
        probes.ok(domain, "gbp.duplicates",
                  "one listing for %s across the %d location(s) on the account"
                  % (title, len(everything)))

    categories = location.get("categories") or {}
    primary = ((categories.get("primaryCategory") or {}).get("displayName", ""))
    extra = [str((row or {}).get("displayName", ""))
             for row in (categories.get("additionalCategories") or [])]
    if primary and extra:
        probes.ok(domain, "gbp.categories",
                  "primary %s, plus %s" % (primary, listed(extra)))
    elif primary:
        probes.bad(domain, "gbp.categories",
                   "primary category %s and no additional ones - every search that names a "
                   "second service goes to a competitor who listed it" % primary)
    else:
        probes.bad(domain, "gbp.categories", "no primary category is set on %s" % title)

    brand = site.brand()
    plain = simple(title)
    if not title:
        probes.bad(domain, "gbp.name", "the listing carries no name")
    elif simple(brand) in plain and len(plain) <= len(simple(brand)) + 12:
        probes.ok(domain, "gbp.name", "the listing is %r and the site calls itself %r"
                  % (title, brand))
    else:
        probes.bad(domain, "gbp.name",
                   "the listing is %r and the site calls itself %r - a name carrying anything "
                   "but the name on the sign is a suspension waiting to happen" % (title, brand))

    phones = location.get("phoneNumbers") or {}
    listed_phone = digits(phones.get("primaryPhone", ""))
    text = " ".join(visible_text(html) for _, html, _ in site.live())
    site_phones = {digits(found) for found in re.findall(r"[\d][\d\s().+-]{8,}\d", text)}
    site_phones |= {digits(found) for _, html, _ in site.live()
                    for found in re.findall(r"""(?i)href=["']tel:([^"']+)""", html)}
    street = " ".join((location.get("storefrontAddress") or {}).get("addressLines", []) or [])
    faults = []
    if not listed_phone:
        faults.append("the listing carries no phone number")
    elif not any(listed_phone in found or found in listed_phone
                 for found in site_phones if len(found) >= 7):
        faults.append("the listing's number does not appear on the site, which shows %s"
                      % (listed(sorted(p for p in site_phones if len(p) >= 7), 3) or "none"))
    if street and street.split()[0].lower() not in text.lower():
        faults.append("the listing's address (%s) does not appear on the site" % street[:60])
    if faults:
        probes.bad(domain, "gbp.nap",
                   "%s. Google reads the site to confirm the listing, and a mismatch reads as "
                   "two businesses" % "; ".join(faults))
    else:
        probes.ok(domain, "gbp.nap",
                  "the listing's phone and address both appear on the site")

    regular = (location.get("regularHours") or {}).get("periods") or []
    special = (location.get("specialHours") or {}).get("specialHourPeriods") or []
    upcoming = []
    for period in special:
        start = period.get("startDate") or {}
        if start.get("year"):
            stamp = "%04d-%02d-%02d" % (start.get("year", 0), start.get("month", 1),
                                        start.get("day", 1))
            age = days_since(stamp + "T00:00:00Z")
            if age is not None and age < 0:
                upcoming.append(stamp)
    if not regular:
        probes.bad(domain, "gbp.hours", "no regular hours are set, so the listing shows nothing "
                                        "about when the business is open")
    elif not upcoming:
        probes.bad(domain, "gbp.hours",
                   "%d regular period(s) and no upcoming holiday hours - a closed door on a "
                   "public holiday that the listing says is open earns a review about it"
                   % len(regular))
    else:
        probes.ok(domain, "gbp.hours",
                  "%d regular period(s) and %d upcoming holiday date(s): %s"
                  % (len(regular), len(upcoming), listed(sorted(upcoming), 3)))

    area = ((location.get("serviceArea") or {}).get("places") or {}).get("placeInfos") or []
    if area:
        probes.ok(domain, "gbp.service_area",
                  "%d service area(s): %s" % (len(area),
                                              listed(row.get("placeName") for row in area)))
    elif location.get("storefrontAddress"):
        probes.ok(domain, "gbp.service_area",
                  "a storefront address and no service area, which is the shape for a business "
                  "customers come to")
    else:
        probes.bad(domain, "gbp.service_area",
                   "no storefront address and no service area, so the listing describes a "
                   "business that is nowhere")

    services = location.get("serviceItems") or []
    if services:
        probes.ok(domain, "gbp.services", "%d service(s) or product(s) listed" % len(services))
    else:
        probes.bad(domain, "gbp.services",
                   "nothing is listed under services, so the listing answers no question about "
                   "what the business does")

    description = str((location.get("profile") or {}).get("description", ""))
    if len(description) >= 250:
        probes.ok(domain, "gbp.description",
                  "the description uses %d of the 750 characters available" % len(description))
    else:
        probes.bad(domain, "gbp.description",
                   "the description is %d characters of the 750 available - the room is free and "
                   "it is read on every profile view" % len(description))

    status, data, raw = api("%s/%s/media" % (LEGACY, legacy), token)
    media = (data or {}).get("mediaItems") or [] if isinstance(data, dict) else []
    if status != 200:
        probes.bad_all(domain, ("gbp.photos", "gbp.photos.interior"),
                       "the media list answered %s" % api_error(status, raw))
    else:
        ages = [days_since(row.get("createTime")) for row in media]
        newest = min([age for age in ages if age is not None], default=None)
        if not media:
            probes.bad(domain, "gbp.photos", "no photo on the listing, so Google shows whatever "
                                             "a passer-by uploaded")
        elif newest is None or newest > 180:
            probes.bad(domain, "gbp.photos",
                       "%d photo(s), the newest %s - a listing that stopped being photographed "
                       "reads as a business that closed"
                       % (len(media), "%d days old" % newest if newest else "undated"))
        else:
            probes.ok(domain, "gbp.photos",
                      "%d photo(s), the newest %d days old" % (len(media), newest))
        inside = [row for row in media
                  if str((row.get("locationAssociation") or {}).get("category", "")).upper()
                  == "INTERIOR"]
        outside = [row for row in media
                   if str((row.get("locationAssociation") or {}).get("category", "")).upper()
                   == "EXTERIOR"]
        if inside and outside:
            probes.ok(domain, "gbp.photos.interior",
                      "%d interior and %d exterior photo(s)" % (len(inside), len(outside)))
        else:
            probes.bad(domain, "gbp.photos.interior",
                       "%d interior and %d exterior photo(s) - a customer who cannot see the "
                       "door or the room inside picks the listing that shows both"
                       % (len(inside), len(outside)))

    status, data, raw = api("%s/%s/reviews" % (LEGACY, legacy), token)
    reviews = (data or {}).get("reviews") or [] if isinstance(data, dict) else []
    if status != 200:
        probes.bad(domain, "gbp.reviews", "the review list answered %s" % api_error(status, raw))
    else:
        silent = [row for row in reviews if not row.get("reviewReply")]
        if not reviews:
            probes.bad(domain, "gbp.reviews",
                       "no review on the listing, which is the single strongest local ranking "
                       "signal there is")
        elif silent:
            probes.bad(domain, "gbp.reviews",
                       "%d of %d review(s) carry no owner reply, the oldest from %s"
                       % (len(silent), len(reviews),
                          str(silent[-1].get("createTime", ""))[:10] or "an unreported date"))
        else:
            probes.ok(domain, "gbp.reviews",
                      "every one of the %d review(s) carries an owner reply" % len(reviews))

    status, data, raw = api("%s/%s/questions" % (QANDA, name), token)
    questions = (data or {}).get("questions") or [] if isinstance(data, dict) else []
    if status != 200:
        probes.bad(domain, "gbp.questions",
                   "the question list answered %s" % api_error(status, raw))
    else:
        unanswered = [row for row in questions
                      if not any(str((answer.get("author") or {}).get("type", "")).upper()
                                 == "MERCHANT" for answer in (row.get("topAnswers") or []))]
        if not questions:
            probes.ok(domain, "gbp.questions", "no question has been asked on the listing")
        elif unanswered:
            probes.bad(domain, "gbp.questions",
                       "%d of %d question(s) carry no answer from the business, so the answer "
                       "showing is whatever a stranger wrote"
                       % (len(unanswered), len(questions)))
        else:
            probes.ok(domain, "gbp.questions",
                      "all %d question(s) carry an answer from the business" % len(questions))

    status, data, raw = api("%s/%s/localPosts" % (LEGACY, legacy), token)
    posts = (data or {}).get("localPosts") or [] if isinstance(data, dict) else []
    if status != 200:
        probes.bad(domain, "gbp.posts", "the post list answered %s" % api_error(status, raw))
    else:
        ages = [days_since(row.get("createTime")) for row in posts]
        newest = min([age for age in ages if age is not None], default=None)
        if newest is not None and newest <= 30:
            probes.ok(domain, "gbp.posts",
                      "%d post(s), the newest %d days old" % (len(posts), newest))
        else:
            probes.bad(domain, "gbp.posts",
                       "%d post(s), the newest %s - a post stops showing after a week, so a "
                       "profile that stopped posting shows none"
                       % (len(posts), "%d days old" % newest if newest is not None else "never"))

    website = str(location.get("websiteUri", ""))
    status, data, raw = api("%s/%s/attributes" % (INFO, name), token)
    attributes = (data or {}).get("attributes") or [] if isinstance(data, dict) else []
    links = [row for row in attributes if "url" in str(row.get("name", "")).lower()]
    if not website:
        probes.bad(domain, "gbp.links", "the listing carries no website link at all")
    elif stem(host_of(website)) != domain:
        probes.bad(domain, "gbp.links",
                   "the listing links %s, which is not %s" % (website, domain))
    else:
        code, _, _ = site.get(website)
        if code == 200:
            probes.ok(domain, "gbp.links",
                      "the listing links %s, which answers 200, alongside %d link attribute(s)"
                      % (website, len(links)))
        else:
            probes.bad(domain, "gbp.links",
                       "the listing links %s, which answers %s" % (website, code or "nothing"))

    metrics = "&".join("dailyMetrics=%s" % metric for metric in
                       ("CALL_CLICKS", "BUSINESS_DIRECTION_REQUESTS", "WEBSITE_CLICKS"))
    start = (datetime.now(timezone.utc) - timedelta(days=30)).date()
    end = datetime.now(timezone.utc).date()
    window = ("dailyRange.start_date.year=%d&dailyRange.start_date.month=%d&"
              "dailyRange.start_date.day=%d&dailyRange.end_date.year=%d&"
              "dailyRange.end_date.month=%d&dailyRange.end_date.day=%d"
              % (start.year, start.month, start.day, end.year, end.month, end.day))
    status, data, raw = api("%s/%s:fetchMultiDailyMetricsTimeSeries?%s&%s"
                            % (PERFORMANCE, name, metrics, window), token, timeout=45)
    series = (data or {}).get("multiDailyMetricTimeSeries") or [] if isinstance(data, dict) else []
    if status != 200:
        probes.bad(domain, "gbp.performance",
                   "the performance series answered %s" % api_error(status, raw))
    else:
        totals = {}
        for block in series:
            for row in block.get("dailyMetricTimeSeries") or []:
                metric = str(row.get("dailyMetric", ""))
                values = (row.get("timeSeries") or {}).get("datedValues") or []
                totals[metric] = sum(int(value.get("value", 0) or 0) for value in values)
        if not totals:
            probes.bad(domain, "gbp.performance",
                       "the performance series came back empty for the last 30 days")
        elif sum(totals.values()) == 0:
            probes.bad(domain, "gbp.performance",
                       "30 days of calls, direction requests and website clicks total zero - "
                       "either nobody is finding the listing or it is not showing")
        else:
            probes.ok(domain, "gbp.performance",
                      "30 days: %s" % listed(["%s %d" % (key.lower().replace("_", " "), value)
                                              for key, value in sorted(totals.items())], 3))

    probes.bad(domain, "gbp.messaging",
               "the Business Profile APIs serve no messaging setting, so this one is read in the "
               "profile under Messages: either replies happen inside the answering window or the "
               "feature is switched off. Record what it showed.")


# ---------------------------------------------------------------- rich results

RCH_NAMES = ("rch.article_eligible", "rch.breadcrumb", "rch.eligible", "rch.image_specs",
             "rch.invalid_items", "rch.local_eligible", "rch.page_parity",
             "rch.product_eligible", "rch.resolved", "rch.retired_types",
             "rch.review_policy", "rch.warning_items")

ASPECTS = ((16 / 9, "16x9"), (4 / 3, "4x3"), (1.0, "1x1"))
RICH_IMAGE_WIDTH = 1200
LOGO_WIDTH = 112


def structured(site):
    """Every structured data item on every page read, and the blocks that would not parse."""
    if "ld" in site.state:
        return site.state["ld"]
    pages = []
    for url, html, _ in site.live():
        items, broken = [], []
        for raw in ld_blocks(html):
            text = re.sub(r"(?s)^<!\[CDATA\[|\]\]>$", "", raw).strip()
            parsed = as_json(text)
            if parsed is None:
                broken.append(re.sub(r"\s+", " ", text)[:70])
            else:
                items.extend(flatten_ld(parsed))
        pages.append({"url": url, "html": html, "items": items, "broken": broken})
    site.state["ld"] = pages
    return pages


def every_item(pages):
    return [(page["url"], item) for page in pages for item in page["items"]]


def missing_fields(item, fields):
    return [field for field in fields if not item.get(field)]


def probe_rch(probes, site, ctx):
    domain = site.domain
    pages = structured(site)
    items = every_item(pages)
    if not pages:
        probes.bad_all(domain, RCH_NAMES,
                       "no page on %s answered 200, so no markup was read" % domain)
        return

    broken = [(page["url"], text) for page in pages for text in page["broken"]]
    untyped = [(url, item) for url, item in items if not ld_types(item)]
    if broken:
        probes.bad(domain, "rch.resolved",
                   "%d structured data block(s) will not parse, so Google reads none of the item "
                   "they carry: %s" % (len(broken),
                                       listed(["%s: %s" % (path_of(url), text)
                                               for url, text in broken], 3)))
    elif not items:
        probes.bad(domain, "rch.resolved",
                   "none of the %d page(s) read carries structured data, so no rich result is "
                   "available to earn" % len(pages))
    elif untyped:
        probes.bad(domain, "rch.resolved",
                   "%d item(s) declare no @type, which resolves to nothing: %s"
                   % (len(untyped), listed({path_of(url) for url, _ in untyped}, 3)))
    else:
        probes.ok(domain, "rch.resolved",
                  "%d item(s) across %d page(s) parse and carry a type: %s"
                  % (len(items), len(pages),
                     listed(sorted({t for _, item in items for t in ld_types(item)}), 5)))

    declared = sorted({t for _, item in items for t in ld_types(item)})
    earning = [t for t in declared if t in RENDERED or t.lower() in LOCAL_TYPES]
    idle = [t for t in declared if t not in earning and t not in RETIRED
            and t not in STRUCTURAL]
    if not declared:
        probes.bad(domain, "rch.eligible",
                   "no type is declared anywhere, so no result type is being asked for")
    elif not earning:
        probes.bad(domain, "rch.eligible",
                   "the page declares %s, none of which Google draws a result for"
                   % listed(declared))
    elif idle:
        probes.bad(domain, "rch.eligible",
                   "%s draw results; %s draw nothing and are page weight"
                   % (listed(earning, 4), listed(idle, 4)))
    else:
        probes.ok(domain, "rch.eligible",
                  "every declared type draws a result Google renders: %s" % listed(earning, 6))

    retired = sorted({t for t in declared if t in RETIRED})
    if retired:
        probes.bad(domain, "rch.retired_types",
                   "%d retired type(s) marked up: %s. Each validates cleanly and renders nothing"
                   % (len(retired), listed([RETIRED[t] for t in retired], 3)))
    else:
        probes.ok(domain, "rch.retired_types",
                  "no retired type is marked up across %d item(s)" % len(items))

    invalid, warned = [], []
    for url, item in items:
        types = ld_types(item)
        blob = json.dumps(item)
        for kind in types:
            gone = missing_fields(item, REQUIRED.get(kind, ()))
            if gone:
                invalid.append("%s %s is missing %s" % (path_of(url), kind, listed(gone, 3)))
            weak = missing_fields(item, RECOMMENDED.get(kind, ()))
            if weak:
                warned.append("%s %s has no %s" % (path_of(url), kind, listed(weak, 3)))
        if PLACEHOLDER.search(blob):
            invalid.append("%s %s carries a placeholder value (%s)"
                           % (path_of(url), listed(types, 2),
                              PLACEHOLDER.search(blob).group(0)[:30]))
        for field in ("url", "image", "logo", "contentUrl", "thumbnailUrl"):
            for value in ld_urls(item.get(field)):
                if value and not value.startswith(("http://", "https://", "data:")):
                    invalid.append("%s %s declares a relative %s (%s)"
                                   % (path_of(url), listed(types, 2), field, value[:40]))
    if invalid:
        probes.bad(domain, "rch.invalid_items",
                   "%d item(s) are invalid: %s" % (len(invalid), listed(invalid, 4)))
    else:
        probes.ok(domain, "rch.invalid_items",
                  "all %d item(s) carry the fields their type requires, in absolute URLs and "
                  "with no placeholder left in" % len(items))
    if warned:
        probes.bad(domain, "rch.warning_items",
                   "%d item(s) are valid only with warnings: %s"
                   % (len(warned), listed(warned, 4)))
    else:
        probes.ok(domain, "rch.warning_items",
                  "no item is missing a recommended field, across %d item(s)" % len(items))

    off_page = []
    for page in pages:
        readable = visible_text(page["html"])
        for item in page["items"]:
            for field in ("name", "headline", "telephone"):
                value = ld_text(item.get(field)).strip()
                if len(value) < 4 or len(value) > 120:
                    continue
                if field == "telephone":
                    # The last ten digits, so a country code on one side is not a mismatch.
                    marked = digits(value)[-10:]
                    if marked and marked not in digits(readable):
                        off_page.append("%s marks up telephone %s, which the page does not show"
                                        % (path_of(page["url"]), value))
                elif flat(value)[:60] not in flat(readable):
                    off_page.append("%s marks up %s %r, which the page does not show"
                                    % (path_of(page["url"]), field, value[:40]))
            price = ld_text(((item.get("offers") or {}) if isinstance(item.get("offers"), dict)
                             else {}).get("price"))
            if price and price not in readable:
                off_page.append("%s marks up price %s, which the page does not show"
                                % (path_of(page["url"]), price))
    if off_page:
        probes.bad(domain, "rch.page_parity",
                   "%d marked-up fact(s) do not appear on the page carrying them: %s. A fact "
                   "only the markup states is what a manual action is written about"
                   % (len(off_page), listed(off_page, 3)))
    else:
        probes.ok(domain, "rch.page_parity",
                  "every marked-up name, headline, phone and price appears on its own page")

    urls, faults = [], []
    for url, item in items:
        for field in ("image", "logo", "thumbnailUrl"):
            urls.extend([(url, field, found) for found in ld_urls(item.get(field))])
    status, robots_text, _ = site.robots()
    rules = robots_rules(robots_text, "googlebot") if status == 200 else []
    seen = set()
    for page_url, field, image in urls[:12]:
        if image in seen:
            continue
        seen.add(image)
        if image.startswith("data:"):
            faults.append("%s uses a data URI, which Google will not fetch" % path_of(page_url))
            continue
        target = absolute(image, page_url) if not image.startswith("http") else image
        if rules and host_of(target) == host_of(site.resolve()) \
                and not robots_allows(rules, path_of(target)):
            faults.append("%s is blocked by robots.txt" % path_of(target))
            continue
        code, data = http_bytes(target)
        if code != 200 or not data:
            faults.append("%s answers %s" % (path_of(target), code or "nothing"))
            continue
        size = image_size(data)
        if not size:
            faults.append("%s is a format whose dimensions could not be read" % path_of(target))
            continue
        width, height = size
        ratio = width / height if height else 0
        nearest = min(ASPECTS, key=lambda pair: abs(pair[0] - ratio))
        # A logo is drawn small beside the result and answers to its own minimum;
        # the 1200 pixel rule is for the image a result displays.
        if field == "logo":
            if min(width, height) < LOGO_WIDTH:
                faults.append("the logo %s is %dx%d, under the %d pixels Google draws one at"
                              % (path_of(target), width, height, LOGO_WIDTH))
        elif width < RICH_IMAGE_WIDTH:
            faults.append("%s is %dx%d, under the %d pixel width Google requires"
                          % (path_of(target), width, height, RICH_IMAGE_WIDTH))
        elif abs(nearest[0] - ratio) / nearest[0] > 0.03:
            faults.append("%s is %dx%d, which is none of 16x9, 4x3 or 1x1"
                          % (path_of(target), width, height))
    if not seen:
        probes.bad(domain, "rch.image_specs",
                   "no item declares an image, so every result that can carry one is drawn plain")
    elif faults:
        probes.bad(domain, "rch.image_specs",
                   "%d of %d image(s) fail Google's rules: %s"
                   % (len(faults), len(seen), listed(faults, 3)))
    else:
        probes.ok(domain, "rch.image_specs",
                  "all %d image(s) are fetchable, with result images at least %d wide in an "
                  "accepted aspect and logos at least %d"
                  % (len(seen), RICH_IMAGE_WIDTH, LOGO_WIDTH))

    trails, faults = 0, []
    inner = [page for page in pages if page["url"].rstrip("/") != site.resolve().rstrip("/")]
    for page in inner:
        crumbs = [item for item in page["items"] if has_type(item, "BreadcrumbList")]
        if not crumbs:
            faults.append("%s carries no breadcrumb, so the result shows the raw URL"
                          % path_of(page["url"]))
            continue
        for crumb in crumbs:
            elements = crumb.get("itemListElement") or []
            trails += 1
            positions = [row.get("position") for row in elements if isinstance(row, dict)]
            if len(elements) < 2:
                faults.append("%s has a trail of %d step(s)" % (path_of(page["url"]),
                                                                len(elements)))
            if positions != list(range(1, len(positions) + 1)):
                faults.append("%s numbers its trail %s" % (path_of(page["url"]),
                                                           listed(positions, 4)))
            for row in elements:
                if not isinstance(row, dict):
                    continue
                target = ld_text(row.get("item"))
                if target and not target.startswith(("http://", "https://")):
                    faults.append("%s points a step at %r, which is relative"
                                  % (path_of(page["url"]), target[:40]))
                if not (row.get("name") or ld_text(row.get("item"))):
                    faults.append("%s has a step with no name" % path_of(page["url"]))
    if not inner:
        probes.bad(domain, "rch.breadcrumb",
                   "only the home page was read, so no trail could be judged; a home page carries "
                   "no breadcrumb by design")
    elif faults:
        probes.bad(domain, "rch.breadcrumb",
                   "%d fault(s) across %d inner page(s): %s"
                   % (len(faults), len(inner), listed(faults, 3)))
    else:
        probes.ok(domain, "rch.breadcrumb",
                  "%d trail(s) across %d inner page(s), each numbered from 1 with absolute steps"
                  % (trails, len(inner)))

    locals_found = [(url, item) for url, item in items if is_local(item)]
    if not locals_found:
        probes.ok(domain, "rch.local_eligible",
                  "no LocalBusiness markup, which is right for a site with no place customers "
                  "visit")
    else:
        faults = []
        for url, item in locals_found:
            gone = missing_fields(item, ("name", "address", "telephone", "url",
                                         "openingHoursSpecification"))
            if gone:
                faults.append("%s is missing %s" % (path_of(url), listed(gone, 3)))
        if faults:
            probes.bad(domain, "rch.local_eligible",
                       "%d LocalBusiness item(s) fall short: %s"
                       % (len(locals_found), listed(faults, 3)))
        else:
            probes.ok(domain, "rch.local_eligible",
                      "%d LocalBusiness item(s) carry name, address, phone, url and hours"
                      % len(locals_found))

    articles = [(url, item) for url, item in items
                if has_type(item, "Article", "NewsArticle", "BlogPosting")]
    if not articles:
        probes.ok(domain, "rch.article_eligible",
                  "no Article markup, which is right for a site with no editorial routes")
    else:
        faults = []
        for url, item in articles:
            gone = missing_fields(item, ("headline", "datePublished", "author", "image"))
            if gone:
                faults.append("%s is missing %s" % (path_of(url), listed(gone, 3)))
            headline = ld_text(item.get("headline"))
            if len(headline) > 110:
                faults.append("%s has a %d character headline, over the 110 Google reads"
                              % (path_of(url), len(headline)))
        if faults:
            probes.bad(domain, "rch.article_eligible",
                       "%d Article item(s) fall short: %s" % (len(articles), listed(faults, 3)))
        else:
            probes.ok(domain, "rch.article_eligible",
                      "%d Article item(s) carry headline, date, author and image" % len(articles))

    products = [(url, item) for url, item in items if has_type(item, "Product", "ProductGroup")]
    if not products:
        probes.ok(domain, "rch.product_eligible",
                  "no Product markup, which is right for a site that sells nothing on the page")
    else:
        faults = []
        for url, item in products:
            offers = item.get("offers")
            offers = offers if isinstance(offers, list) else [offers] if offers else []
            if not offers:
                faults.append("%s declares a Product with no offer" % path_of(url))
                continue
            for offer in offers:
                if not isinstance(offer, dict):
                    faults.append("%s declares an offer that is not an object" % path_of(url))
                    continue
                gone = missing_fields(offer, ("price", "priceCurrency", "availability"))
                if gone:
                    faults.append("%s offer is missing %s" % (path_of(url), listed(gone, 3)))
        if faults:
            probes.bad(domain, "rch.product_eligible",
                       "%d Product item(s) fall short: %s" % (len(products), listed(faults, 3)))
        else:
            probes.ok(domain, "rch.product_eligible",
                      "%d Product item(s) carry price, currency and availability" % len(products))

    self_serving = []
    for url, item in items:
        if not (has_type(item, "Organization") or is_local(item)):
            continue
        if item.get("aggregateRating") or item.get("review"):
            self_serving.append("%s attaches a rating to its own %s"
                                % (path_of(url), listed(ld_types(item), 2)))
    if self_serving:
        probes.bad(domain, "rch.review_policy",
                   "%d self-serving review marking(s): %s. Google ignores a rating a business "
                   "gives itself and can treat it as spam"
                   % (len(self_serving), listed(self_serving, 3)))
    else:
        probes.ok(domain, "rch.review_policy",
                  "no rating is attached to the business's own entity across %d item(s)"
                  % len(items))


# ---------------------------------------------------------------- core web vitals

CWV_NAMES = ("cwv.cls", "cwv.field_record", "cwv.form_factor_split", "cwv.gsc_groups",
             "cwv.inp", "cwv.lab_fallback", "cwv.lcp", "cwv.origin_pass", "cwv.recheck")

CRUX_METRIC = {"largest_contentful_paint": ("lcp", LCP_MS),
               "interaction_to_next_paint": ("inp", INP_MS),
               "cumulative_layout_shift": ("cls", CLS_SCORE)}


def rated(record):
    """The metric-by-metric verdict on one field record, as a sentence."""
    return ", ".join("%s %s%s" % (short.upper(), value, "" if ok else " over %s" % threshold)
                     for short, value, threshold, ok in field_verdicts(record)) or "no metric"


def probe_cwv(probes, site, ctx):
    domain = site.domain
    key = env("GOOGLE_PAGESPEED_API_KEY")
    if not key:
        probes.need_all(domain, CWV_NAMES, "GOOGLE_PAGESPEED_API_KEY",
                        "no field record and no lab run were taken. The anonymous quota is "
                        "shared across every keyless caller and is usually exhausted")
        return
    url = site.resolve()
    status, payload, raw = psi(site, url, "mobile", key)
    if status != 200 or not isinstance(payload, dict):
        probes.bad_all(domain, CWV_NAMES,
                       "PageSpeed Insights answered %s for %s" % (api_error(status, raw), url))
        return
    page = field_record(payload, "loadingExperience")
    origin = field_record(payload, "originLoadingExperience")
    record = page or origin

    if page:
        probes.ok(domain, "cwv.field_record",
                  "the URL has its own 28-day record, rated %s: %s"
                  % (page.get("overall", "unrated").lower(), rated(page)))
    elif origin:
        probes.ok(domain, "cwv.field_record",
                  "no record for the URL, so the origin's stands in, rated %s: %s"
                  % (origin.get("overall", "unrated").lower(), rated(origin)))
    else:
        probes.bad(domain, "cwv.field_record",
                   "neither %s nor its origin has a field record - too few Chrome visits, so "
                   "the diagnosis has to come from the lab" % url)

    for name, short, threshold, unit in (("cwv.lcp", "lcp", LCP_MS, "ms"),
                                         ("cwv.inp", "inp", INP_MS, "ms"),
                                         ("cwv.cls", "cls", CLS_SCORE, "")):
        if not record:
            probes.bad(domain, name, "there is no field record to read %s from" % short.upper())
        elif short not in record:
            probes.bad(domain, name,
                       "the %s record carries no %s, which means too few visits interacted to "
                       "report one" % (record["source"], short.upper()))
        elif record[short] <= threshold:
            probes.ok(domain, name, "%s at the 75th percentile is %s%s, inside %s%s (%s record)"
                      % (short.upper(), record[short], unit, threshold, unit, record["source"]))
        else:
            probes.bad(domain, name,
                       "%s at the 75th percentile is %s%s against a threshold of %s%s (%s "
                       "record) - one visitor in four had worse"
                       % (short.upper(), record[short], unit, threshold, unit, record["source"]))

    dstatus, desktop_payload, draw = psi(site, url, "desktop", key)
    desktop = field_record(desktop_payload or {}, "loadingExperience") or \
        field_record(desktop_payload or {}, "originLoadingExperience")
    if dstatus != 200:
        probes.bad(domain, "cwv.form_factor_split",
                   "the desktop run answered %s, so only mobile was read"
                   % api_error(dstatus, draw))
    elif not desktop or not record:
        probes.bad(domain, "cwv.form_factor_split",
                   "only one form factor has a record (mobile %s, desktop %s), so the two cannot "
                   "be judged apart" % ("read" if record else "missing",
                                        "read" if desktop else "missing"))
    else:
        failing = [("mobile", record)] if any(not ok for *_, ok in field_verdicts(record)) else []
        failing += [("desktop", desktop)] if any(not ok for *_, ok in field_verdicts(desktop)) \
            else []
        if failing:
            probes.bad(domain, "cwv.form_factor_split",
                       "%s: %s" % (" and ".join("%s fails (%s)" % (kind, rated(row))
                                                for kind, row in failing),
                                   "the other form factor is not the one to fix"))
        else:
            probes.ok(domain, "cwv.form_factor_split",
                      "mobile (%s) and desktop (%s) both pass" % (rated(record), rated(desktop)))

    if origin:
        failing = [short.upper() for short, _, _, ok in field_verdicts(origin) if not ok]
        if failing:
            probes.bad(domain, "cwv.origin_pass",
                       "the origin record fails %s (%s) - the whole site is rated on this, not "
                       "the sampled URLs" % (listed(failing, 3), rated(origin)))
        else:
            probes.ok(domain, "cwv.origin_pass",
                      "the origin record passes every metric: %s" % rated(origin))
    else:
        probes.bad(domain, "cwv.origin_pass",
                   "the origin has no field record at all, so the site is rated on nothing")

    lab = lab_metrics(payload)
    if record:
        probes.ok(domain, "cwv.lab_fallback",
                  "a %s field record exists, so the lab is not the diagnosis here; the lab reads "
                  "score %s, LCP %sms, CLS %s"
                  % (record["source"], lab.get("score", "-"), lab.get("lcp", "-"),
                     lab.get("cls", "-")))
    elif lab:
        probes.ok(domain, "cwv.lab_fallback",
                  "no field record, so the lab run stands in: score %s, LCP %sms, TBT %sms, "
                  "CLS %s" % (lab.get("score", "-"), lab.get("lcp", "-"), lab.get("tbt", "-"),
                              lab.get("cls", "-")))
    else:
        probes.bad(domain, "cwv.lab_fallback",
                   "there is no field record and the lab run returned no metrics, so this URL "
                   "group is undiagnosed")

    status, payload, raw = crux(site, key)
    crux_record = (payload or {}).get("record") or {} if isinstance(payload, dict) else {}
    metrics = crux_record.get("metrics") or {}
    if status != 200 or not metrics:
        probes.bad(domain, "cwv.gsc_groups",
                   "the Chrome UX Report answered %s for %s, so the URL groups behind the Search "
                   "Console report could not be read" % (api_error(status, raw), site.resolve()))
    else:
        weak = []
        for name, (short, threshold) in CRUX_METRIC.items():
            block = metrics.get(name) or {}
            p75 = (block.get("percentiles") or {}).get("p75")
            good = ((block.get("histogram") or [{}])[0]).get("density", 0)
            if p75 is None:
                continue
            value = float(p75)
            if value > threshold or float(good) < 0.75:
                weak.append("%s p75 %s with %.0f%% good" % (short.upper(), p75, float(good) * 100))
        if weak:
            probes.bad(domain, "cwv.gsc_groups",
                       "the origin's own record fails %s, which is the same data the vitals "
                       "report groups URLs by" % listed(weak, 3))
        else:
            probes.ok(domain, "cwv.gsc_groups",
                      "the origin record passes every metric in the Chrome UX Report, which is "
                      "what the vitals report groups")

    period = crux_record.get("collectionPeriod") or {}
    first = period.get("firstDate") or {}
    last = period.get("lastDate") or {}
    window = ("%s to %s" % ("%(year)s-%(month)02d-%(day)02d" % {
        "year": first.get("year", 0), "month": first.get("month", 1), "day": first.get("day", 1)},
        "%(year)s-%(month)02d-%(day)02d" % {"year": last.get("year", 0),
                                            "month": last.get("month", 1),
                                            "day": last.get("day", 1)})) if first and last \
        else "an unreported window"
    probes.bad(domain, "cwv.recheck",
               "the record in hand covers %s and it is 28 days of visits, so it still carries "
               "the experience before any fix inside that window. Re-take this reading 28 days "
               "after the fix landed and record both dates." % window)


# ---------------------------------------------------------------- commerce

CONTENT = "https://shoppingcontent.googleapis.com/content/v2.1"
ADS_STREAM = "https://googleads.googleapis.com/v18/customers/%s/googleAds:searchStream"

CMR_NAMES = ("cmr.account_dupes", "cmr.ads_ga4_link", "cmr.conversion_health",
             "cmr.conversion_values", "cmr.disapprovals", "cmr.feed_fetch",
             "cmr.feed_page_parity", "cmr.item_updates", "cmr.merchant_ads_link",
             "cmr.policies", "cmr.website_claim")

MERCHANT_NAMES = ("cmr.website_claim", "cmr.feed_fetch", "cmr.disapprovals",
                  "cmr.item_updates", "cmr.policies", "cmr.feed_page_parity",
                  "cmr.account_dupes", "cmr.merchant_ads_link")
ADS_NAMES = ("cmr.conversion_health", "cmr.conversion_values")


def ads_query(customer, query, token, developer, login=""):
    """One Google Ads query. The developer token is as required as the bearer one."""
    headers = bearer(token)
    headers["developer-token"] = developer
    if login:
        headers["login-customer-id"] = re.sub(r"\D", "", login)
    status, body, _ = http(ADS_STREAM % re.sub(r"\D", "", customer), headers=headers,
                           method="POST", data={"query": query}, timeout=45)
    payload = as_json(body)
    rows = []
    for batch in payload if isinstance(payload, list) else [payload]:
        if isinstance(batch, dict):
            rows.extend(batch.get("results") or [])
    return status, rows, body


def probe_cmr(probes, site, ctx):
    domain = site.domain
    token = env("GOOGLE_OAUTH_TOKEN")
    if not token:
        probes.need_all(domain, CMR_NAMES, "GOOGLE_OAUTH_TOKEN",
                        "neither Merchant Center nor Ads was opened")
        return
    merchant = re.sub(r"\D", "", env("GOOGLE_MERCHANT_ID"))
    if not merchant:
        probes.need_all(domain, MERCHANT_NAMES, "GOOGLE_MERCHANT_ID",
                        "the Merchant Center account was never opened")
    else:
        probe_merchant(probes, site, token, merchant)

    customer = env("GOOGLE_ADS_CUSTOMER_ID")
    developer = env("GOOGLE_ADS_DEVELOPER_TOKEN")
    if not customer or not developer:
        probes.need_all(domain, ADS_NAMES,
                        "GOOGLE_ADS_CUSTOMER_ID and GOOGLE_ADS_DEVELOPER_TOKEN",
                        "no conversion action was read")
    else:
        probe_ads(probes, site, token, customer, developer)

    prop = ga4_property(domain)
    if not prop:
        probes.bad(domain, "cmr.ads_ga4_link",
                   "GA4_PROPERTY_MAP names no property for %s, so the link between Ads and the "
                   "analytics property could not be read" % domain)
        return
    status, data, raw = api("%s/properties/%s/googleAdsLinks" % (ADMIN, prop), token)
    links = (data or {}).get("googleAdsLinks") or [] if isinstance(data, dict) else []
    wanted = re.sub(r"\D", "", customer) if customer else ""
    matched = [row for row in links
               if not wanted or re.sub(r"\D", "", str(row.get("customerId", ""))) == wanted]
    if status != 200:
        probes.bad(domain, "cmr.ads_ga4_link",
                   "the Ads link list answered %s" % api_error(status, raw))
    elif matched:
        probes.ok(domain, "cmr.ads_ga4_link",
                  "%d Ads account(s) linked to property %s, including %s"
                  % (len(links), prop, matched[0].get("customerId", "the one configured")))
    elif links:
        probes.bad(domain, "cmr.ads_ga4_link",
                   "property %s links Ads account(s) %s, none of them %s"
                   % (prop, listed(row.get("customerId") for row in links), customer))
    else:
        probes.bad(domain, "cmr.ads_ga4_link",
                   "property %s links no Ads account, so campaign spend and site behaviour are "
                   "reported in two places that never meet" % prop)


def probe_merchant(probes, site, token, merchant):
    domain = site.domain
    status, account, raw = api("%s/%s/accounts/%s" % (CONTENT, merchant, merchant), token)
    if status != 200 or not isinstance(account, dict):
        probes.bad_all(domain, MERCHANT_NAMES,
                       "Merchant Center account %s answered %s" % (merchant,
                                                                   api_error(status, raw)))
        return
    website = str(account.get("websiteUrl", ""))
    sstatus, statuses, sraw = api("%s/%s/accountstatuses/%s" % (CONTENT, merchant, merchant),
                                  token)
    claimed = bool((statuses or {}).get("websiteClaimed")) if isinstance(statuses, dict) else False
    issues = (statuses or {}).get("accountLevelIssues") or [] if isinstance(statuses, dict) else []
    if not website:
        probes.bad(domain, "cmr.website_claim", "account %s holds no website URL" % merchant)
    elif stem(host_of(website)) != domain:
        probes.bad(domain, "cmr.website_claim",
                   "account %s holds %s, which is not %s" % (merchant, website, domain))
    elif claimed:
        probes.ok(domain, "cmr.website_claim",
                  "%s is verified and claimed on account %s, with %d account-level issue(s)"
                  % (website, merchant, len(issues)))
    else:
        probes.bad(domain, "cmr.website_claim",
                   "%s is on account %s and is not claimed, so no item can serve"
                   % (website, merchant))

    status, data, raw = api("%s/%s/datafeedstatuses" % (CONTENT, merchant), token)
    feeds = (data or {}).get("resources") or [] if isinstance(data, dict) else []
    if status != 200:
        probes.bad(domain, "cmr.feed_fetch",
                   "the feed status list answered %s" % api_error(status, raw))
    elif not feeds:
        probes.bad(domain, "cmr.feed_fetch",
                   "no product feed is submitted on account %s, so nothing can be listed"
                   % merchant)
    else:
        broken = ["%s is %s with %s error(s)" % (row.get("datafeedId"),
                                                 row.get("processingStatus", "unreported"),
                                                 len(row.get("errors") or []))
                  for row in feeds
                  if str(row.get("processingStatus", "")).lower() != "success"
                  or row.get("errors")]
        if broken:
            probes.bad(domain, "cmr.feed_fetch",
                       "%d of %d feed(s) did not fetch cleanly: %s"
                       % (len(broken), len(feeds), listed(broken, 3)))
        else:
            probes.ok(domain, "cmr.feed_fetch",
                      "all %d feed(s) processed successfully, %d item(s) submitted"
                      % (len(feeds), sum(int((row.get("itemsTotal") or 0)) for row in feeds)))

    status, data, raw = api("%s/%s/productstatuses?maxResults=100" % (CONTENT, merchant), token)
    products = (data or {}).get("resources") or [] if isinstance(data, dict) else []
    if status != 200:
        probes.bad(domain, "cmr.disapprovals",
                   "the product status list answered %s" % api_error(status, raw))
    else:
        refused = []
        for row in products:
            for issue in row.get("itemLevelIssues") or []:
                if str(issue.get("servability", "")).lower() == "disapproved":
                    refused.append("%s: %s" % (row.get("productId", "")[-24:],
                                               issue.get("description", "")[:50]))
        if refused:
            probes.bad(domain, "cmr.disapprovals",
                       "%d disapproval(s) across %d item(s): %s"
                       % (len(refused), len(products), listed(refused, 3)))
        elif not products:
            probes.bad(domain, "cmr.disapprovals",
                       "account %s holds no product at all" % merchant)
        else:
            probes.ok(domain, "cmr.disapprovals",
                      "none of the %d item(s) read is disapproved" % len(products))

    improvements = account.get("automaticImprovements") or {}
    updates = (improvements.get("itemUpdates") or {}).get("accountItemUpdatesSettings") or {}
    switched = [name for name, value in updates.items() if value]
    if switched:
        probes.ok(domain, "cmr.item_updates",
                  "automatic item updates are on: %s" % listed(switched, 4))
    else:
        probes.bad(domain, "cmr.item_updates",
                   "automatic item updates are off, so a price or availability change on the "
                   "site does not reach the listing until the feed is fetched again")

    sstatus, shipping, sraw = api("%s/%s/shippingsettings/%s" % (CONTENT, merchant, merchant),
                                  token)
    rstatus, returns, rraw = api("%s/%s/returnpolicyonline" % (CONTENT, merchant), token)
    services = (shipping or {}).get("services") or [] if isinstance(shipping, dict) else []
    policies = (returns or {}).get("returnPolicies") or [] if isinstance(returns, dict) else []
    if services and policies:
        probes.ok(domain, "cmr.policies",
                  "%d shipping service(s) and %d return policy(ies) configured"
                  % (len(services), len(policies)))
    else:
        probes.bad(domain, "cmr.policies",
                   "%d shipping service(s) (%s) and %d return policy(ies) (%s) - an item with "
                   "neither shows an estimate Google made up"
                   % (len(services), api_error(sstatus, sraw), len(policies),
                      api_error(rstatus, rraw)))

    status, data, raw = api("%s/%s/products?maxResults=5" % (CONTENT, merchant), token)
    listings = (data or {}).get("resources") or [] if isinstance(data, dict) else []
    if status != 200:
        probes.bad(domain, "cmr.feed_page_parity",
                   "the product list answered %s" % api_error(status, raw))
    elif not listings:
        probes.bad(domain, "cmr.feed_page_parity",
                   "no product to compare against a page on account %s" % merchant)
    else:
        drift = []
        for row in listings:
            link = str(row.get("link", ""))
            feed_price = str((row.get("price") or {}).get("value", ""))
            if not link:
                drift.append("%s carries no link" % row.get("offerId", ""))
                continue
            code, html, _ = site.get(link)
            if code != 200:
                drift.append("%s links %s, which answers %s" % (row.get("offerId", ""),
                                                                path_of(link), code or "nothing"))
                continue
            prices = set()
            for item in json_ld(html):
                offers = item.get("offers")
                for offer in (offers if isinstance(offers, list) else [offers] if offers else []):
                    if isinstance(offer, dict) and offer.get("price") is not None:
                        prices.add(str(offer["price"]))
            page_text = visible_text(html)
            if feed_price and prices and not any(
                    abs(float(found) - float(feed_price)) < 0.01
                    for found in prices if re.match(r"^\d+(\.\d+)?$", found)):
                drift.append("%s is %s in the feed and %s on the page"
                             % (row.get("offerId", ""), feed_price, listed(sorted(prices), 2)))
            elif feed_price and not prices and feed_price.split(".")[0] not in page_text:
                drift.append("%s is %s in the feed and the page shows no such price"
                             % (row.get("offerId", ""), feed_price))
        if drift:
            probes.bad(domain, "cmr.feed_page_parity",
                       "%d of %d sampled item(s) disagree with their page: %s. Google compares "
                       "the two and disapproves the item"
                       % (len(drift), len(listings), listed(drift, 3)))
        else:
            probes.ok(domain, "cmr.feed_page_parity",
                      "all %d sampled item(s) match the price on their own page" % len(listings))

    status, data, raw = api("%s/%s/accounts" % (CONTENT, merchant), token)
    accounts = (data or {}).get("resources") or [] if isinstance(data, dict) else []
    holding = [row for row in accounts
               if stem(host_of(str(row.get("websiteUrl", "")))) == domain]
    if status != 200 and status != 403:
        probes.bad(domain, "cmr.account_dupes",
                   "the sub-account list answered %s" % api_error(status, raw))
    elif len(holding) > 1:
        probes.bad(domain, "cmr.account_dupes",
                   "%d accounts hold %s: %s. Two accounts on one domain fight each other for "
                   "every listing" % (len(holding), domain,
                                      listed(row.get("id") for row in holding)))
    else:
        probes.ok(domain, "cmr.account_dupes",
                  "one account holds %s across the %d account(s) readable from %s"
                  % (domain, len(accounts) or 1, merchant))

    ads_links = account.get("adsLinks") or []
    active = [row for row in ads_links if str(row.get("status", "")).lower() == "active"]
    if active:
        probes.ok(domain, "cmr.merchant_ads_link",
                  "%d active Ads link(s) on account %s: %s"
                  % (len(active), merchant, listed(row.get("adsId") for row in active)))
    else:
        probes.bad(domain, "cmr.merchant_ads_link",
                   "%d Ads link(s) on account %s and none is active, so no shopping campaign can "
                   "serve the feed" % (len(ads_links), merchant))


def probe_ads(probes, site, token, customer, developer):
    domain = site.domain
    status, rows, raw = ads_query(
        customer,
        "SELECT conversion_action.name, conversion_action.status, conversion_action.type, "
        "conversion_action.value_settings.default_value, "
        "conversion_action.value_settings.always_use_default_value, "
        "metrics.all_conversions FROM conversion_action WHERE segments.date DURING LAST_30_DAYS",
        token, developer, env("GOOGLE_ADS_LOGIN_CUSTOMER_ID"))
    if status != 200:
        probes.bad_all(domain, ADS_NAMES,
                       "the Ads query answered %s" % api_error(status, raw))
        return
    actions = []
    for row in rows:
        action = row.get("conversionAction") or {}
        metrics = row.get("metrics") or {}
        settings = action.get("valueSettings") or {}
        actions.append({"name": action.get("name", ""),
                        "status": str(action.get("status", "")),
                        "type": str(action.get("type", "")),
                        "conversions": float(metrics.get("allConversions", 0) or 0),
                        "value": float(settings.get("defaultValue", 0) or 0)})
    enabled = [row for row in actions if row["status"] == "ENABLED"]
    silent = [row for row in enabled if row["conversions"] <= 0]
    if not actions:
        probes.bad(domain, "cmr.conversion_health",
                   "account %s defines no conversion action, so every campaign is optimising "
                   "against clicks" % customer)
    elif silent:
        probes.bad(domain, "cmr.conversion_health",
                   "%d of %d enabled conversion action(s) recorded nothing in 30 days: %s"
                   % (len(silent), len(enabled), listed(row["name"] for row in silent)))
    else:
        probes.ok(domain, "cmr.conversion_health",
                  "all %d enabled conversion action(s) recorded conversions in 30 days: %s"
                  % (len(enabled), listed(["%s (%.0f)" % (row["name"], row["conversions"])
                                           for row in enabled], 3)))
    valueless = [row for row in enabled if row["value"] <= 0]
    if not enabled:
        probes.bad(domain, "cmr.conversion_values",
                   "no enabled conversion action to carry a value")
    elif valueless:
        probes.bad(domain, "cmr.conversion_values",
                   "%d of %d conversion action(s) carry no value: %s. A conversion worth nothing "
                   "makes every bid strategy optimise for the cheapest one"
                   % (len(valueless), len(enabled),
                      listed(row["name"] for row in valueless)))
    else:
        probes.ok(domain, "cmr.conversion_values",
                  "all %d enabled conversion action(s) carry a value" % len(enabled))


# ---------------------------------------------------------------- trust

TRS_NAMES = ("trs.cert_expiry", "trs.cloaking", "trs.injected_content", "trs.link_profile",
             "trs.manual_actions", "trs.mixed_content", "trs.safe_browsing",
             "trs.security_issues", "trs.third_party_scripts", "trs.tls_chain")

SAFE_BROWSING = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

DNS_MISS = re.compile(r"(?i)name or service not known|nodename nor servname|getaddrinfo"
                      r"|no address associated|temporary failure in name resolution")

RESOURCE_TAG = re.compile(r"(?is)<(script|link|img|iframe|source|video|audio|embed)\b[^>]*>")
REL_ATTR = re.compile(r"""(?is)\brel\s*=\s*["']([^"']*)["']""")
SRC_ATTR = re.compile(r"""(?is)\b(?:src|href)\s*=\s*["']([^"']+)["']""")
HINT_REL = {"canonical", "alternate", "dns-prefetch", "preconnect", "prefetch", "manifest",
            "me", "author", "license", "help", "search", "profile", "pingback"}

INJECTION = (
    (re.compile(r"""(?is)style\s*=\s*["'][^"']*display\s*:\s*none[^"']*["'][^>]*>\s*<a\s"""),
     "a hidden block of links"),
    (re.compile(r"(?i)\b(?:viagra|cialis|payday loan|replica watches|escort service|"
                r"casino bonus)\b"), "keywords a hacked page carries"),
    (re.compile(r"(?i)eval\s*\(\s*(?:atob|unescape|decodeURIComponent|String\.fromCharCode)"),
     "script hidden behind an encoder"),
    (re.compile(r"""(?is)document\.write\s*\(\s*["']<script[^>]*src="""),
     "a script written into the page at runtime"),
    (re.compile(r"""(?is)<iframe\b[^>]*\b(?:width|height)\s*=\s*["']?(?:0|1)["']?"""),
     "an iframe sized to be invisible"),
)


def certificate(host, timeout=12):
    """The certificate a host serves, read off the socket with verification on."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as secure:
                return secure.getpeercert(), secure.version(), ""
    except Exception as exc:
        return None, "", str(exc)


def certificate_name(field):
    """The common name out of a certificate's subject or issuer tuple."""
    for group in field or ():
        for key, value in group:
            if key in ("commonName", "organizationName"):
                return value
    return "an unnamed party"


def probe_trs(probes, site, ctx):
    domain = site.domain
    origin = site.resolve()
    hosts = [host_of(origin)]
    other = domain if hosts[0].startswith("www.") else "www.%s" % domain
    hosts.append(other)

    chains, expiries, faults = [], [], []
    for host in hosts:
        cert, version, error = certificate(host)
        if cert is None:
            faults.append("%s: %s" % (host, error[:90]))
            continue
        chains.append("%s over %s, issued by %s" % (host, version,
                                                    certificate_name(cert.get("issuer"))))
        try:
            expires = datetime.strptime(cert.get("notAfter", ""), "%b %d %H:%M:%S %Y %Z")
            expiries.append((host, (expires.replace(tzinfo=timezone.utc)
                                    - datetime.now(timezone.utc)).days))
        except ValueError:
            faults.append("%s serves a certificate whose expiry would not parse" % host)
    # A host with no DNS record serves nothing and so serves nothing insecurely; a
    # host that answers and fails verification is the failure this gate is about.
    unresolved = [fault for fault in faults if DNS_MISS.search(fault)]
    breaking = [fault for fault in faults if fault not in unresolved]
    if not chains:
        probes.bad(domain, "trs.tls_chain",
                   "no host served a chain that verifies: %s" % listed(faults, 2))
    elif breaking:
        probes.bad(domain, "trs.tls_chain",
                   "%d host(s) answer and do not serve a chain that verifies: %s"
                   % (len(breaking), listed(breaking, 2)))
    else:
        probes.ok(domain, "trs.tls_chain",
                  "every host that resolves serves a chain that verifies: %s%s"
                  % (listed(chains, 2),
                     "; %s has no DNS record" % listed([f.split(":")[0] for f in unresolved], 2)
                     if unresolved else ""))

    if not expiries:
        probes.bad(domain, "trs.cert_expiry",
                   "no certificate could be read, so nothing is known about renewal")
    else:
        soonest = min(expiries, key=lambda pair: pair[1])
        if soonest[1] < 0:
            probes.bad(domain, "trs.cert_expiry",
                       "%s served a certificate that expired %d days ago" % (soonest[0],
                                                                             -soonest[1]))
        elif soonest[1] < CERT_RENEW_DAYS:
            probes.bad(domain, "trs.cert_expiry",
                       "%s expires in %d days, inside the %d day renewal window - a renewal that "
                       "fails silently takes the site off the web"
                       % (soonest[0], soonest[1], CERT_RENEW_DAYS))
        else:
            probes.ok(domain, "trs.cert_expiry",
                      "the soonest expiry is %s in %d days" % (soonest[0], soonest[1]))

    insecure, third, injected = [], {}, []
    for url, html, _ in site.live():
        for tag in RESOURCE_TAG.finditer(html):
            rel = REL_ATTR.search(tag.group(0))
            kinds = set((rel.group(1) if rel else "").lower().split())
            if kinds & HINT_REL:
                continue
            src = SRC_ATTR.search(tag.group(0))
            if not src:
                continue
            target = src.group(1).strip()
            if target.startswith("http://"):
                insecure.append("%s loads %s" % (path_of(url), target[:60]))
            if tag.group(1).lower() == "script" and target.startswith(("http://", "https://",
                                                                      "//")):
                host = host_of(target if "//" in target[:8] else "https:" + target)
                if host and stem(host) != domain:
                    third.setdefault(host, set()).add(path_of(url))
        for pattern, meaning in INJECTION:
            found = pattern.search(html)
            if found:
                injected.append("%s carries %s" % (path_of(url), meaning))
    if insecure:
        probes.bad(domain, "trs.mixed_content",
                   "%d subresource(s) load over plain HTTP: %s"
                   % (len(insecure), listed(insecure, 3)))
    else:
        probes.ok(domain, "trs.mixed_content",
                  "no page of the %d read loads a subresource over plain HTTP" % len(site.live()))

    if not third:
        probes.ok(domain, "trs.third_party_scripts",
                  "every script on the %d page(s) read is served from %s itself"
                  % (len(site.live()), domain))
    else:
        probes.ok(domain, "trs.third_party_scripts",
                  "%d third-party script origin(s): %s. Each one is a domain whose reputation "
                  "this one inherits" % (len(third), listed(sorted(third))))

    if injected:
        probes.bad(domain, "trs.injected_content",
                   "%d marker(s) of injected content: %s"
                   % (len(injected), listed(sorted(set(injected)), 3)))
    else:
        probes.ok(domain, "trs.injected_content",
                  "no hidden link block, obfuscated script, runtime script write or invisible "
                  "iframe on the %d page(s) read" % len(site.live()))

    human = site.get(origin)
    crawler = site.get(origin, agent=CRAWLER_UA)
    if human[0] != 200 or crawler[0] != 200:
        probes.bad(domain, "trs.cloaking",
                   "the page answered %s to a browser and %s to Googlebot"
                   % (human[0] or "nothing", crawler[0] or "nothing"))
    else:
        one, two = len(human[1]), len(crawler[1])
        ratio = min(one, two) / max(one, two) if max(one, two) else 1
        titles = (title_of(human[1]), title_of(crawler[1]))
        if ratio < 0.8 or titles[0] != titles[1]:
            probes.bad(domain, "trs.cloaking",
                       "a browser is served %d bytes titled %r and Googlebot %d bytes titled %r"
                       % (one, titles[0][:40], two, titles[1][:40]))
        else:
            probes.ok(domain, "trs.cloaking",
                      "Googlebot and a browser are served the same page: %d and %d bytes, one "
                      "title" % (one, two))

    key = env("GOOGLE_SAFE_BROWSING_KEY")
    if not key:
        probes.need(domain, "trs.safe_browsing", "GOOGLE_SAFE_BROWSING_KEY",
                    "no Safe Browsing lookup was made")
    else:
        entries = [{"url": url} for url, _, _ in site.live()] or [{"url": origin}]
        status, body, _ = http("%s?key=%s" % (SAFE_BROWSING, quoted(key)), method="POST",
                               data={"client": {"clientId": "google-checklist-probe",
                                                "clientVersion": "1.0"},
                                     "threatInfo": {
                                         "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING",
                                                         "UNWANTED_SOFTWARE",
                                                         "POTENTIALLY_HARMFUL_APPLICATION"],
                                         "platformTypes": ["ANY_PLATFORM"],
                                         "threatEntryTypes": ["URL"],
                                         "threatEntries": entries}}, timeout=30)
        payload = as_json(body)
        matches = (payload or {}).get("matches") or [] if isinstance(payload, dict) else None
        if status != 200 or matches is None:
            probes.bad(domain, "trs.safe_browsing",
                       "the Safe Browsing lookup answered %s" % api_error(status, body))
        elif matches:
            probes.bad(domain, "trs.safe_browsing",
                       "%d URL(s) are flagged: %s. A flagged site shows an interstitial in every "
                       "browser that uses this list"
                       % (len(matches), listed(["%s as %s" % ((row.get("threat") or {}).get("url"),
                                                              row.get("threatType"))
                                                for row in matches], 3)))
        else:
            probes.ok(domain, "trs.safe_browsing",
                      "Safe Browsing reports all %d URL(s) clean" % len(entries))

    prop = gsc_property(domain)
    probes.bad(domain, "trs.manual_actions",
               "Search Console serves no API for the manual actions report, so this one is read "
               "on %s under Security & Manual Actions > Manual actions. Record what it showed "
               "and when." % prop)
    probes.bad(domain, "trs.security_issues",
               "Search Console serves no API for the security issues report, so this one is read "
               "on %s under Security & Manual Actions > Security issues. Record what it showed "
               "and when." % prop)
    probes.bad(domain, "trs.link_profile",
               "Search Console serves no API for the links report, so this one is read on %s "
               "under Links > Top linking sites. Record the domains linking in, and whether any "
               "of them is one nobody would choose." % prop)


# ---------------------------------------------------------------- presence

PRS_NAMES = ("prs.brand_query", "prs.contact_parity", "prs.favicon", "prs.home_snippet",
             "prs.knowledge_panel", "prs.legacy_domain", "prs.panel_facts", "prs.sameas",
             "prs.site_name", "prs.sitelinks")

KNOWLEDGE = "https://kgsearch.googleapis.com/v1/entities:search"
ICON_REL = re.compile(r"(?i)\b(?:icon|shortcut icon|apple-touch-icon)\b")


def probe_prs(probes, site, ctx):
    domain = site.domain
    origin = site.resolve()
    pages = site.live()
    if not pages:
        probes.bad_all(domain, PRS_NAMES,
                       "no page on %s answered 200, so nothing about its presence was read"
                       % domain)
        return
    home_url, home, _ = pages[0]
    items = [item for _, item in every_item(structured(site))]
    brand = site.brand()

    icons, unfetchable = [], []
    for tag in re.findall(r"(?is)<link\b[^>]*>", home):
        rel = REL_ATTR.search(tag)
        if not rel or not ICON_REL.search(rel.group(1)):
            continue
        href = SRC_ATTR.search(tag)
        if not href:
            continue
        target = absolute(href.group(1), home_url)
        if target:
            icons.append(target)
        else:
            unfetchable.append("the home page declares the icon %r, which is not an address "
                               "Google can fetch" % href.group(1)[:40])
    if not icons:
        code, data = http_bytes("%s/favicon.ico" % origin)
        if code == 200 and data:
            icons.append("%s/favicon.ico" % origin)
    if not icons:
        probes.bad(domain, "prs.favicon",
                   "%s, and %s/favicon.ico serves nothing, so the result carries a generic globe"
                   % (listed(unfetchable, 2) or "the home page declares no icon", origin))
    else:
        faults, sizes = list(unfetchable), []
        for icon in icons[:3]:
            code, data = http_bytes(icon)
            if code != 200 or not data:
                faults.append("%s answers %s" % (path_of(icon), code or "nothing"))
                continue
            size = image_size(data)
            if not size:
                sizes.append("%s (dimensions unreadable)" % path_of(icon))
                continue
            sizes.append("%s %dx%d" % (path_of(icon), size[0], size[1]))
            if size[0] != size[1]:
                faults.append("%s is %dx%d rather than square" % (path_of(icon), size[0], size[1]))
            elif size[0] < 48:
                faults.append("%s is %dpx, under the 48 Google renders" % (path_of(icon), size[0]))
            if stem(host_of(icon)) != domain:
                faults.append("%s is served from another domain" % icon[:60])
        if faults:
            probes.bad(domain, "prs.favicon",
                       "%d icon fault(s): %s" % (len(faults), listed(faults, 3)))
        else:
            probes.ok(domain, "prs.favicon",
                      "%d icon(s) declared and served from the site: %s"
                      % (len(icons), listed(sizes, 3)))

    profiles = []
    for item in items:
        for value in ld_urls(item.get("sameAs")) if item.get("sameAs") else []:
            if value not in profiles:
                profiles.append(value)
    if not profiles:
        probes.bad(domain, "prs.sameas",
                   "no item declares sameAs, so nothing connects the site to the profiles the "
                   "brand already holds")
    else:
        faults = []
        for profile in profiles[:8]:
            if not profile.startswith(("http://", "https://")):
                faults.append("%s is not an absolute URL" % profile[:40])
                continue
            code, _, _ = http(profile, method="GET", timeout=15)
            # A profile page answering 403 or 405 to a script is still a profile that exists.
            if code in (0, 404, 410):
                faults.append("%s answers %s" % (profile[:50], code or "nothing"))
        if faults:
            probes.bad(domain, "prs.sameas",
                       "%d of %d declared profile(s) do not resolve: %s"
                       % (len(faults), len(profiles), listed(faults, 3)))
        else:
            probes.ok(domain, "prs.sameas",
                      "%d profile(s) declared and reachable: %s"
                      % (len(profiles), listed([host_of(p) for p in profiles], 4)))

    declared = {"og:site_name": meta_content(home, "og:site_name", attr="property")}
    for item in items:
        if has_type(item, "WebSite", "Organization") or is_local(item):
            name = ld_text(item.get("name"))
            if name:
                declared.setdefault((ld_types(item) or ["the markup"])[0], name)
    values = {source: value for source, value in declared.items() if value}
    plain = {simple(value) for value in values.values()}
    title = title_of(home)
    parts = [part.strip() for part in re.split(r"\s+[|–—-]\s+", title) if part.strip()]
    suffix = parts[-1] if len(parts) > 1 else title
    if not values:
        probes.bad(domain, "prs.site_name",
                   "nothing declares a site name: no og:site_name and no WebSite or Organization "
                   "item. Google shortens the title (%r) into whatever fits" % title[:60])
    elif len(plain) > 1:
        probes.bad(domain, "prs.site_name",
                   "the page declares %d different names: %s. Google picks one and it is not "
                   "always the one on the sign"
                   % (len(plain), listed(["%s says %r" % pair for pair in values.items()], 3)))
    elif suffix and simple(suffix) not in plain:
        probes.bad(domain, "prs.site_name",
                   "%s declare(s) %r and the title reads %r, so the result can show either"
                   % (listed(sorted(values), 2), list(values.values())[0], suffix[:40]))
    else:
        probes.ok(domain, "prs.site_name",
                  "%r is declared by %s and the title agrees"
                  % (list(values.values())[0], listed(sorted(values), 3)))

    marked_phones, marked_mails, marked_address = set(), set(), ""
    for item in items:
        if item.get("telephone"):
            marked_phones.add(digits(ld_text(item.get("telephone"))))
        if item.get("email"):
            marked_mails.add(ld_text(item.get("email")).lower().replace("mailto:", ""))
        if item.get("address") and not marked_address:
            marked_address = address_text(item.get("address"))
    text = " ".join(visible_text(html) for _, html, _ in pages)
    shown_phones = {digits(found) for _, html, _ in pages
                    for found in re.findall(r"""(?i)href=["']tel:([^"']+)""", html)}
    shown_mails = {found.lower() for _, html, _ in pages
                   for found in re.findall(r"""(?i)href=["']mailto:([^"'?]+)""", html)}
    faults = []
    for phone in marked_phones:
        if phone and shown_phones and not any(phone in found or found in phone
                                              for found in shown_phones):
            faults.append("the markup carries %s and the page links %s"
                          % (phone, listed(sorted(shown_phones), 2)))
    for mail in marked_mails:
        if mail and shown_mails and mail not in shown_mails:
            faults.append("the markup carries %s and the page links %s"
                          % (mail, listed(sorted(shown_mails), 2)))
    if marked_address and marked_address.split(",")[0].strip() and \
            flat(marked_address.split(",")[0]) not in flat(text):
        faults.append("the marked-up address %r appears nowhere on the pages read"
                      % marked_address[:50])
    if not marked_phones and not marked_mails and not shown_phones and not shown_mails:
        probes.bad(domain, "prs.contact_parity",
                   "no contact detail is declared or shown, so Google has nothing of its own to "
                   "show beside the result")
    elif faults:
        probes.bad(domain, "prs.contact_parity",
                   "%d contact detail(s) disagree between the markup and the page: %s"
                   % (len(faults), listed(faults, 3)))
    else:
        # The standing this writes is exported into a page the public can fetch,
        # so the note carries the finding and not the values behind it. A number
        # a site keeps off itself would otherwise be republished by the record
        # saying the site is correct.
        kinds = []
        if marked_phones | shown_phones:
            kinds.append("phone number")
        if marked_mails | shown_mails:
            kinds.append("email address")
        probes.ok(domain, "prs.contact_parity",
                  "the %s in the markup and on the page agree" % " and the ".join(kinds))

    results, why = search("%s %s" % (brand, domain.split(".")[0])
                          if brand.lower() != domain.split(".")[0].lower() else brand)
    if results is None:
        probes.bad_all(domain, ("prs.brand_query", "prs.home_snippet", "prs.legacy_domain"), why)
    else:
        hosts = [host_of(str(row.get("link", ""))) for row in results]
        if not hosts:
            probes.bad(domain, "prs.brand_query",
                       "a search for %r returns nothing at all" % brand)
        elif stem(hosts[0]) == domain:
            probes.ok(domain, "prs.brand_query",
                      "a search for %r returns %s first, ahead of %s"
                      % (brand, hosts[0], listed([h for h in hosts[1:] if stem(h) != domain], 3)))
        else:
            probes.bad(domain, "prs.brand_query",
                       "a search for %r returns %s first and %s is at position %s"
                       % (brand, hosts[0], domain,
                          next((str(index + 1) for index, host in enumerate(hosts)
                                if stem(host) == domain), "nowhere in the first page")))

        mine = [row for row in results if stem(host_of(str(row.get("link", "")))) == domain]
        home_row = next((row for row in mine
                         if str(row.get("link", "")).rstrip("/") == origin.rstrip("/")), None)
        snippet = str((home_row or {}).get("snippet", "")).strip()
        description = meta_content(home, "description")
        if not home_row:
            probes.bad(domain, "prs.home_snippet",
                       "the home page is not among the results for %r, so the snippet Google "
                       "writes for it could not be read" % brand)
        elif not snippet:
            probes.bad(domain, "prs.home_snippet",
                       "the home page result carries no snippet, which is what Google shows when "
                       "the page gives it nothing to quote")
        elif simple(brand)[:12] not in simple(snippet):
            probes.bad(domain, "prs.home_snippet",
                       "the snippet reads %r and never names the business. The description on the "
                       "page reads %r" % (snippet[:90], description[:60] or "nothing"))
        else:
            probes.ok(domain, "prs.home_snippet",
                      "the snippet reads %r" % snippet[:120])

        strangers = sorted({stem(host) for host in hosts
                            if stem(host) != domain and simple(brand)[:8] in simple(stem(host))})
        if strangers:
            probes.bad(domain, "prs.legacy_domain",
                       "%d domain(s) carrying the brand name rank alongside %s: %s. A retired "
                       "domain that still ranks takes the traffic the current one paid for"
                       % (len(strangers), domain, listed(strangers, 4)))
        else:
            probes.ok(domain, "prs.legacy_domain",
                      "no other domain carrying the brand name appears in the results")

    key = env("GOOGLE_KG_API_KEY", "GOOGLE_PAGESPEED_API_KEY")
    if not key:
        probes.need_all(domain, ("prs.knowledge_panel", "prs.panel_facts"),
                        "GOOGLE_KG_API_KEY or GOOGLE_PAGESPEED_API_KEY",
                        "the Knowledge Graph was never queried")
    else:
        params = urllib.parse.urlencode({"query": brand, "key": key, "limit": 5,
                                         "indent": "false"})
        status, body, _ = http("%s?%s" % (KNOWLEDGE, params), timeout=30)
        payload = as_json(body)
        entries = (payload or {}).get("itemListElement") or [] if isinstance(payload, dict) else []
        if status != 200:
            probes.bad_all(domain, ("prs.knowledge_panel", "prs.panel_facts"),
                           "the Knowledge Graph answered %s" % api_error(status, body))
        else:
            mine = [row for row in entries
                    if domain in json.dumps(row.get("result") or {}).lower()]
            best = (mine or entries or [{}])[0].get("result") or {}
            if mine:
                probes.ok(domain, "prs.knowledge_panel",
                          "the Knowledge Graph holds %r (%s) and it names %s"
                          % (best.get("name", brand), listed(best.get("@type") or [], 2), domain))
            elif entries:
                probes.bad(domain, "prs.knowledge_panel",
                           "the Knowledge Graph holds %d entit(ies) for %r and none of them names "
                           "%s, so the panel that shows is somebody else's"
                           % (len(entries), brand, domain))
            else:
                probes.bad(domain, "prs.knowledge_panel",
                           "the Knowledge Graph holds no entity for %r, so no panel is drawn"
                           % brand)
            if not best:
                probes.bad(domain, "prs.panel_facts",
                           "no entity was returned, so its facts could not be compared with the "
                           "site")
            else:
                panel_name = str(best.get("name", ""))
                panel_url = str((best.get("url") or ""))
                faults = []
                if panel_name and simple(panel_name) != simple(brand):
                    faults.append("it is named %r and the site calls itself %r"
                                  % (panel_name, brand))
                if panel_url and stem(host_of(panel_url)) != domain:
                    faults.append("it links %s rather than %s" % (panel_url, domain))
                if faults:
                    probes.bad(domain, "prs.panel_facts",
                               "the entity's facts disagree with the site: %s" % listed(faults, 2))
                else:
                    probes.ok(domain, "prs.panel_facts",
                              "the entity is named %r and links %s, both of which match the site"
                              % (panel_name or brand, panel_url or domain))

    probes.bad(domain, "prs.sitelinks",
               "no API returns the sitelinks Google draws under a result, so this one is read by "
               "searching for %r and looking at what it puts under the home page. Record which "
               "pages showed, and whether any of them is one nobody should land on first."
               % brand)


# ---------------------------------------------------------------- the run

FAMILIES = {
    "gsc": (probe_gsc, GSC_NAMES),
    "idx": (probe_idx, IDX_NAMES),
    "ga4": (probe_ga4, GA4_NAMES),
    "gbp": (probe_gbp, GBP_NAMES),
    "rch": (probe_rch, RCH_NAMES),
    "cwv": (probe_cwv, CWV_NAMES),
    "cmr": (probe_cmr, CMR_NAMES),
    "trs": (probe_trs, TRS_NAMES),
    "prs": (probe_prs, PRS_NAMES),
}

# Filled once every site has been read, because one portfolio is what it compares.
DEFERRED = {"ga4.deployment_uniform"}

REGISTRY_REL = Path("src") / "app" / "data" / "portfolio.js"
# The agency's own site, which its portfolio leaves out, and the checkouts of it
# the portfolio is looked for in: the profile's agency.domain and agency.repo.
OWN_SITE = setting("agency.domain") or ""
AGENCY_REPO = setting("agency.repo") or ""

REGISTRY_SEARCH = (Path("/home/user") / AGENCY_REPO,
                   Path.home() / "Development" / "WebstormProjects" / AGENCY_REPO,
                   Path.home() / "Development" / "IdeaProjects" / AGENCY_REPO,
                   Path.home() / AGENCY_REPO) if AGENCY_REPO else ()


def registry_sites(root):
    """The portfolio, read by evaluating the module the site itself ships.

    Evaluating it is the only reader that cannot drift from what the site
    publishes; the text reader below it covers a machine with no node.
    """
    path = Path(root).expanduser().resolve() / REGISTRY_REL
    if not path.is_file():
        raise SystemExit("%s holds no %s" % (root, REGISTRY_REL))
    script = ("import(process.argv[1]).then(m => "
              "process.stdout.write(JSON.stringify(m.PORTFOLIO_PROJECTS ?? [])))")
    code, out, _ = sh("node", "--input-type=module", "-e", script, str(path), timeout=60)
    rows = as_json(out) if code == 0 else None
    if not isinstance(rows, list):
        text = path.read_text(errors="replace")
        rows = [{"displayUrl": found} for found in re.findall(r"displayUrl\s*:\s*'([^']*)'", text)]
    domains = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        domain = str(row.get("displayUrl") or "").strip().lower()
        if not domain:
            domain = re.sub(r"^https?://(?:www\.)?", "",
                            str(row.get("url") or "")).strip("/").lower()
        domain = stem(domain.split("/")[0])
        if domain and domain not in domains:
            domains.append(domain)
    if OWN_SITE and OWN_SITE not in domains:
        domains.append(OWN_SITE)
    return sorted(domains)


def chosen_sites(args):
    named = [found for value in args.sites for found in re.split(r"[,\s]+", value) if found]
    if named:
        return [stem(re.sub(r"^https?://", "", value).strip("/").split("/")[0].lower())
                for value in named]
    if args.registry:
        return registry_sites(args.registry)
    for root in REGISTRY_SEARCH:
        if (root / REGISTRY_REL).is_file():
            return registry_sites(root)
    raise SystemExit("no site to read. Name one with --sites example.com, or point --registry at "
                     "a %s checkout to read the portfolio." % (AGENCY_REPO or "portfolio"))


def run_site(probes, site, families, ctx):
    for family in families:
        handler, names = FAMILIES[family]
        try:
            handler(probes, site, ctx)
        except Exception as exc:
            probes.fill(site.domain, [name for name in names if name not in DEFERRED],
                        "the %s probe raised %s: %s. The reading was not taken, so nothing here "
                        "is a pass" % (family, type(exc).__name__, exc))


# A reading written under a name another run is also writing is a reading two
# workers share: the second to arrive reads the first's numbers as its own. The
# window is wide enough to cover a slow sweep taking its own readings.
PROBE_COLLISION_SECONDS = 3600


def default_probe_out(kind):
    """A path no concurrent run holds."""
    return (Path(tempfile.gettempdir())
            / ("%s-probe-%d-%s.json" % (kind, os.getpid(), time.strftime("%H%M%S"))))


def guarded_probe_out(target, force=False):
    """The path to write, refusing one a recent reading already holds."""
    path = Path(target).expanduser()
    if path.exists() and not force:
        age = time.time() - path.stat().st_mtime
        if age < PROBE_COLLISION_SECONDS:
            raise SystemExit(
                "%s holds a reading written %d minute(s) ago, and overwriting it takes "
                "the numbers out from under whatever is reading it. Name a path of this "
                "run's own, or pass --force." % (path, int(age // 60)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.replace(
                                         "{agency_repo}", AGENCY_REPO or "<portfolio checkout>"),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    verbs = [("all", "every probe, one site at a time")] + \
            [(name, "%s alone" % name) for name in sorted(FAMILIES)]
    for verb, help_text in verbs:
        one = sub.add_parser(verb, help=help_text)
        one.add_argument("--sites", action="append", default=[],
                         help="a domain to read; repeat for more")
        one.add_argument("--registry",
                         help="a %s checkout, to read the portfolio from"
                              % (AGENCY_REPO or "portfolio"))
        one.add_argument("--out", help="where to write the result file")
        one.add_argument("--force", action="store_true",
                         help="take an output path a recent reading holds")
    return parser


def main():
    args = build_parser().parse_args()
    domains = chosen_sites(args)
    families = sorted(FAMILIES) if args.command == "all" else [args.command]
    probes, ctx = Probes(), {}
    for domain in domains:
        site = Site(domain)
        print("reading %s" % site.domain, file=sys.stderr)
        run_site(probes, site, families, ctx)
    if "ga4" in families:
        finish_ga4(probes, ctx)
    for domain in domains:
        for family in families:
            probes.fill(stem(domain), FAMILIES[family][1],
                        "the probe was never reached, so the reading does not exist")

    payload = {"taken": now(), "sites": [stem(domain) for domain in domains],
               "probes": dict(probes)}
    text = json.dumps(payload, indent=2)
    target = guarded_probe_out(args.out or default_probe_out("google"),
                              getattr(args, "force", False))
    target.write_text(text + "\n")
    args.out = str(target)
    print("Filed %d reading(s) across %d site(s) to %s\n"
          % (len(probes), len(domains), target))
    for name in sorted(probes):
        row = probes[name]
        print("  %-3s %-46s %s" % ("ok" if row["ok"] else "X", name, row["detail"][:100]))
    failing = [name for name, row in probes.items() if not row["ok"]]
    print("\n%d read, %d failing." % (len(probes), len(failing)))
    if args.out:
        print("File them against the run:\n"
              "  ~/.claude/checklists/google/bin/google-pass.py probe --file %s"
              % args.out)
    else:
        print("\n%s" % text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
