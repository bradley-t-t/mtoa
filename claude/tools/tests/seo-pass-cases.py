#!/usr/bin/env python3
"""The cases the SEO gate tool answers to.

The tool named in INTELLIGENCE_TEST_TARGET is the local shim; the engine it runs
sits inside the skill, and that is what these load. Nothing here touches the
network, so a case set that passes on one machine passes on every machine.

What is covered is the part that fails silently: whether a check reads the site
or the writing about the site. A README documents the tags a page should carry,
and read as markup it both invents defects in prose and certifies a page by the
paragraph describing what that page lacks. The other half is the checks that
legitimately read everything - the sitemap gate, whose generator is named in the
manifest, and the copy pass, whose subject is the Markdown itself.
"""

import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        return
    FAILURES.append("%s%s" % (name, (" - " + detail) if detail else ""))


def base(path):
    return Path(path).name


def load_engine():
    """The engine the shim runs, imported under a name a module can hold.

    The copy under test is the one in the tree these cases sit in, so it comes
    first: a suite that loads an installed engine reports on a file the edit
    never reached.
    """
    target = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
                  or Path.home() / ".claude/tools/seo-pass.py")
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "checklists/seo/bin/seo-pass.py",
        target.resolve().parent.parent / "checklists/seo/bin/seo-pass.py",
        Path.home() / ".claude/checklists/seo/bin/seo-pass.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("seo_pass_engine", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def fake_site(root, **files):
    root = Path(root).resolve()
    for rel, text in files.items():
        path = root / rel.replace("|", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def run_of(root, **extra):
    run = {"id": "cases", "opened": "now", "session": "cases", "kind": "site",
           "targets": [str(root)], "repo": str(root), "url": "", "title": "cases",
           "flags": [], "results": {}, "touched": [], "probe": {}, "delegated": []}
    run.update(extra)
    return run


README = (
    "# Project\n\n"
    "[![build](https://img.shields.io/badge/build-passing-07101c)](https://github.com/x/y)\n\n"
    "Every page sets `<meta name=\"viewport\" content=\"width=device-width\">`\n"
    "and `<meta charset=\"utf-8\">` inside `<html lang=\"en\">`.\n\n"
    "A staging route may carry `<meta name=\"robots\" content=\"noindex\">`.\n\n"
    "```json\n"
    "{\"@context\": \"https://schema.org\", \"@type\": \"Organization\",\n"
    " \"name\": \"[Company Name]\", \"url\": \"https://example.com\"}\n"
    "```\n\n"
    "The shell is `<header>`, `<nav>`, `<main>` and `<footer>`.\n"
    "Social cards use `<meta property=\"og:image\" content=\"/og.png\">`\n"
    "and `twitter:card`. Do not ship `lorem ipsum` or a `<div onclick=...>`.\n"
    "Last updated 2026-08-29 by the maintainers.\n")

PAGE = ("<!doctype html><html><head><title>Tides</title></head>\n"
        "<body><h1>Tides</h1><p>Hourly readings for the bayou.</p></body></html>\n")

GUIDE = (
    "# Marking up an organization\n\n"
    "A route states who publishes it with one node in the head:\n\n"
    "```html\n"
    "<script type=\"application/ld+json\">\n"
    "{\"@context\": \"https://schema.org\", \"@type\": \"Organization\",\n"
    " \"name\": \"[Company Name]\", \"url\": \"/about\"}\n"
    "</script>\n"
    "```\n\n"
    "The tide tables are published every morning before the first ferry.\n")


def the_readme_is_not_the_page(engine):
    """A README documenting the tags a page owes is not a page carrying them.

    The sweep reads the whole tree, so the file that explains the standard reaches
    every check written against markup. It fails both ways at once: a documented
    example closes a gate the site does not meet, and a cautionary line is reported
    as a defect in a file no browser is served.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = fake_site(
            tmp,
            **{"package.json": json.dumps({"name": "x", "version": "2026.35.4",
                                           "author": "A Maintainer"}),
               "index.html": PAGE,
               "README.md": README})
        run = run_of(root)
        site = engine.build_site(run)
        ctx = engine.build_context(run, site)

        served = set(site["sources"])
        check("the README is not a source a check reads",
              not any(p.endswith("README.md") for p in served),
              "sources were %s" % sorted(base(p) for p in served))
        check("the manifest is not a source a check reads",
              not any(p.endswith("package.json") for p in served),
              "sources were %s" % sorted(base(p) for p in served))
        check("the page is a source a check reads",
              any(p.endswith("index.html") for p in served),
              "sources were %s" % sorted(base(p) for p in served))
        check("the whole tree is still available to a check that asks",
              any(p.endswith("README.md") for p in site.get("tree", {})),
              "tree was %s" % sorted(base(p) for p in site.get("tree", {})))
        check("the cache scope covers every file the sweep reads",
              any(p.endswith("README.md") for p in engine.sweep_scope(site)),
              "scope was %s" % sorted(base(p) for p in engine.sweep_scope(site)))

        # A documented example must not close a gate the site does not meet.
        for name, fn in (("viewport", engine.check_viewport),
                         ("charset", engine.check_charset),
                         ("html lang", engine.check_html_lang),
                         ("robots directive", engine.check_meta_robots_explicit),
                         ("landmarks", engine.check_landmarks),
                         ("twitter card", engine.check_twitter_card),
                         ("json-ld present", engine.check_jsonld_present),
                         ("rendered date", engine.check_dates_visible),
                         ("author attribution", engine.check_author_signals)):
            status, detail = fn({"check": {}}, site, run, ctx)
            check("a documented %s example does not close its gate" % name,
                  status != engine.PASS, detail)

        # And a line of prose must not be reported as a defect in the markup.
        for name, fn in (("noindex", engine.check_no_accidental_noindex),
                         ("lorem", engine.check_no_lorem),
                         ("og:image", engine.check_og_image_absolute),
                         ("clickable div", engine.check_semantic_html)):
            status, detail = fn({"check": {}}, site, run, ctx)
            check("%s does not report the README" % name, "README.md" not in detail, detail)

        # The context decides which gates a run even has, so a word in a document
        # must not conjure a gate family.
        check("a JSON-LD example in a fence does not turn on the schema gates",
              not ctx.get("jsonld"), "the context said jsonld=%s" % ctx.get("jsonld"))


def a_fenced_example_in_a_route_is_not_markup(engine):
    """A Markdown route is served, and the fence inside it is an example.

    Markdown-family files can be real routes, so narrowing by extension alone
    leaves the fenced block reachable. Read whole, the guide showing the node it
    documents closes the identity gate for a site that declares nothing, and the
    placeholder inside that example is reported as placeholder data shipped.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = fake_site(
            tmp,
            **{"package.json": json.dumps({"name": "x", "version": "2026.35.4"}),
               "index.html": PAGE,
               "src|pages|guide.mdx": GUIDE})
        run = run_of(root)
        site = engine.build_site(run)
        ctx = engine.build_context(run, site)

        served = {base(p): text for p, text in site["sources"].items()}
        check("a Markdown route is a source a check reads", "guide.mdx" in served,
              "sources were %s" % sorted(served))
        check("the fenced example is not read as markup",
              "application/ld+json" not in served.get("guide.mdx", ""),
              "guide.mdx read as %r" % served.get("guide.mdx", "")[:200])
        check("the prose around the fence is still read",
              "first ferry" in served.get("guide.mdx", ""),
              "guide.mdx read as %r" % served.get("guide.mdx", "")[:200])
        check("the whole file is still available to a check that asks",
              any("application/ld+json" in text for p, text in site["tree"].items()
                  if p.endswith("guide.mdx")),
              "tree held no guide.mdx")

        check("a JSON-LD example in a route's fence does not turn on the schema gates",
              not ctx.get("jsonld"), "the context said jsonld=%s" % ctx.get("jsonld"))
        for name, fn in (("json-ld present", engine.check_jsonld_present),
                         ("identity schema", engine.check_identity_schema)):
            status, detail = fn({"check": {}}, site, run, ctx)
            check("a fenced %s example does not close its gate" % name,
                  status != engine.PASS, detail)
        for name, fn in (("placeholder", engine.check_jsonld_no_placeholder),
                         ("relative URL", engine.check_jsonld_absolute_urls)):
            status, detail = fn({"check": {}}, site, run, ctx)
            check("the %s gate does not report the fenced example" % name,
                  "guide.mdx" not in detail, detail)


def the_manifest_still_names_the_generator(engine):
    """The sitemap gate reads the whole tree, because that is where the answer is.

    No file is served that says a sitemap is built; the dependency that builds it
    is named in the manifest. Narrowing this one to the served set would fail every
    project that generates its sitemap correctly.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = fake_site(
            tmp,
            **{"package.json": json.dumps({"name": "x", "version": "2026.35.4",
                                           "devDependencies": {"next-sitemap": "^4.2.3"}}),
               "index.html": PAGE})
        run = run_of(root)
        site = engine.build_site(run)
        ctx = engine.build_context(run, site)
        status, detail = engine.check_sitemap({"check": {}}, site, run, ctx)
        check("the sitemap gate reads the manifest that names the generator",
              status == engine.PASS, "%s: %s" % (status, detail))


SPY = ("#!/usr/bin/env python3\n"
       "import sys\n"
       "from pathlib import Path\n"
       "Path(__file__).with_name('seen.txt').write_text('\\n'.join(sys.argv[1:]))\n")


def the_copy_pass_reads_the_markdown(engine):
    """Copy is the subject of the writing pass, so Markdown reaches it on purpose.

    Every other gate reads the served set; this one would then hand the writing
    tool the templates and never the prose, which is most of what a person reads.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = fake_site(
            tmp,
            **{"package.json": json.dumps({"name": "x", "version": "2026.35.4"}),
               "index.html": PAGE,
               "README.md": README})
        home = Path(tmp).resolve() / "home"
        tool = home / ".claude" / "tools" / "writing-pass.py"
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text(SPY)
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)

        run = run_of(root)
        site = engine.build_site(run)
        ctx = engine.build_context(run, site)
        real_home = engine.HOME
        try:
            engine.HOME = home
            engine.check_writing_pass({"check": {}}, site, run, ctx)
        finally:
            engine.HOME = real_home

        seen = tool.with_name("seen.txt")
        handed = seen.read_text().splitlines() if seen.is_file() else []
        check("the writing tool was handed the files to read", bool(handed),
              "the tool recorded nothing")
        check("the copy pass hands the writing tool the Markdown",
              any(p.endswith("README.md") for p in handed),
              "it was handed %s" % sorted(base(p) for p in handed))
        check("the copy pass hands the writing tool the markup",
              any(p.endswith("index.html") for p in handed),
              "it was handed %s" % sorted(base(p) for p in handed))


def main():
    engine = load_engine()
    if engine is None:
        print("seo-pass: the skill is not installed, so its engine could not be loaded")
        return 1
    try:
        the_readme_is_not_the_page(engine)
        a_fenced_example_in_a_route_is_not_markup(engine)
        the_manifest_still_names_the_generator(engine)
        the_copy_pass_reads_the_markdown(engine)
    except Exception as exc:
        print("seo-pass cases raised: %s" % exc)
        return 1

    if FAILURES:
        print("%d case(s) failed:" % len(FAILURES))
        for row in FAILURES:
            print("  " + row)
        return 1
    print("seo-pass: every case passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
