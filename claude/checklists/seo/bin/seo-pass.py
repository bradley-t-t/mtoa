#!/usr/bin/env python3
"""Drives the SEO rules into a project's own files, so every one of them lands rather than being reported.

This is a build checklist, not an audit. A gate that is not met is markup to write,
a file to add, or a template to change; the only ways past one are making that
change or naming the condition that puts the gate out of scope. A gate the sweep
failed cannot be recorded as passing, and a gate marked fixed reopens if the next
sweep still fails it. The report at the end records what changed.

Most SEO tooling reads a live URL and scores it. A score is a survey: it names what
is wrong and leaves the work undone, and the next crawl finds the same list. So
every check here reads the project's own source instead - templates, components,
head metadata, robots.txt, sitemaps, structured data - because that is the only
place a finding can actually be fixed.

    start     open a run and print the gates it must satisfy
    scan      re-derive what the project contains, and therefore which gates apply
    verify    run every automated check and record its verdict
    resolve   answer a gate: pass, fixed, or n/a, with the evidence
              --batch <path|-> answers many at once, a status and a note each
    status    what is still outstanding; --full adds each gate's rule and fix
    report    render the filled checklist
    finish    close the run - refuses while anything is unanswered
    gates     print the registry, whole or filtered
    pages     the routes the run found, and the head metadata each one carries
    files     the file ledger: what is still to be read and ruled on
    file-clear  rule a file as needing no SEO change, with the reason
                --batch <path|-> clears many at once, a reason each

    guard-stop   Stop hook: refuse to end a session with a run still open
    guard-land   PreToolUse hook: refuse `git commit` / `gh pr create` mid-run
    hook-skill   PostToolUse hook: open a run when the skill is invoked
    hook-edit    PostToolUse hook: open a run when a crawlable surface is edited
    hook-read    PostToolUse hook: credit a file in the ledger once it is read
"""

import importlib.util
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()

# Resolved from this file's own location, so the gates work wherever they land -
# a synced ~/.claude, an installed copy in a repo, a container that has the
# checklist and nothing else. A fixed path under ~/.claude only exists on a
# machine that already ran the sync, which is exactly the machine that did not
# need it.
ENGINE = Path(__file__).resolve().parent.parent
if not (ENGINE / "checklist" / "gates").is_dir():
    ENGINE = HOME / ".claude" / "checklists" / "seo"
GATES_DIR = ENGINE / "checklist" / "gates"

# Every instruction this file prints names a command that exists where it is printed.
# A container holding only the installed copy has no ~/.claude, so a message
# pointing there names nothing and the run never opens.
TOOL = str(Path(__file__).resolve())


def shell_tokens(command):
    """The command's tokens, tolerating a quote the shell itself would reject.

    An apostrophe inside a commit message is ordinary text to a guard reading the
    command, but strict parsing raises on it and every caller here answers that by
    giving up the walk. A walk that resolves no repository reads as one covering
    every repository, so the guard then refuses a landing in a tree it never
    examined.
    """
    try:
        return shlex.split(command)
    except ValueError:
        try:
            return [t.strip("\"'") for t in shlex.split(command, posix=False)]
        except ValueError:
            return command.split()


def _state_root():
    override = os.environ.get("INTELLIGENCE_STATE_DIR")
    if override:
        return Path(override)
    # A cloud session cannot write under ~/.claude, so the ledger lives beside
    # it there. Runs are per-session state and are deliberately not synced.
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return Path.home() / ".pass-state"
    return HOME / ".claude" / "state"


RUNS = _state_root() / "seo-runs"
CURRENT = RUNS / "current.json"

KINDS = ["site", "page", "app", "docs", "store", "local-business", "blog"]


def session_id(payload=None):
    """Which session this call belongs to, or None when it cannot be told.

    A run is per-session state. One pointer for the whole machine means two sessions
    share it, so a run opened for one project refuses to let an unrelated session
    end and the second session's run silently replaces the first one's.
    """
    if payload:
        got = payload.get("session_id")
        if got:
            return str(got)
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


# Two agents under one session id are two workers, and the working tree each acts
# in is what separates them. The suffix keeps their pointers apart. A call made
# outside any repository carries none, so a run opened from anywhere else answers
# to the plain name.
def workspace_suffix():
    try:
        here = Path.cwd().resolve()
    except OSError:
        return ""
    for path in (here, *here.parents):
        if (path / ".git").exists():
            return "-" + hashlib.sha1(str(path).encode()).hexdigest()[:8]
    return ""


def legacy_pointer(session):
    """The pointer with no working tree in its name."""
    return CURRENT if not session else RUNS / ("current-%s.json" % session)


def claim_shared(run, pointer):
    """Point the shared pointer at a run while no other open run holds it.

    An owned run writing it unconditionally puts one worker's id where another
    worker reads for its own, which costs that worker its run.
    """
    held = _run_at(CURRENT)
    if held is None or held.get("closed") or held.get("id") == run["id"] \
            or not run.get("session"):
        CURRENT.write_text(pointer)


def target_key(target):
    """A stable name for the tree a run acts on."""
    if not target:
        return ""
    try:
        resolved = Path(str(target)).expanduser().resolve()
    except OSError:
        return ""
    return "-" + hashlib.sha1(str(resolved).encode()).hexdigest()[:8]


def run_target(run):
    """The tree a run belongs to, from the run itself."""
    if not isinstance(run, dict):
        return None
    got = run.get("repo") or run.get("target")
    if not got:
        targets = run.get("targets") or []
        got = targets[0] if targets else None
    return got


def pointer_for(session, target):
    """The pointer naming one run: one session, one tree.

    Agents dispatched from one parent carry that parent's session id, so the
    session alone names a fleet. Several runs over different repositories then
    share a pointer, and each save takes it from the last: captures land in
    another run and a verify sweeps one tree while reporting against another.
    """
    if not session:
        return CURRENT
    return RUNS / ("current-%s%s.json" % (session, target_key(target)))


def session_runs(session):
    """Every run this session holds a pointer for, newest first."""
    if not session:
        return []
    found = []
    for path in sorted(RUNS.glob("current-%s*.json" % session)):
        run = _run_at(path)
        if run is not None and not run.get("closed"):
            found.append(run)
    found.sort(key=lambda r: r.get("id") or "", reverse=True)
    return found


def run_here(runs, here=None):
    """The one run among these that owns the directory the call is made in.

    With several open and none owning it, nothing is returned rather than the
    newest: guessing is what put one run's readings in another's ledger.
    """
    try:
        cwd = Path(here or Path.cwd()).resolve()
    except OSError:
        return None
    for run in runs:
        target = run_target(run)
        if not target:
            continue
        try:
            root = Path(str(target)).expanduser().resolve()
        except OSError:
            continue
        # At or below the tree, never above it: a directory holding two
        # worktrees is not either of them.
        if cwd == root or root in cwd.parents:
            return run
    # Standing outside every checkout, a lone run answers: otherwise a run whose
    # tree has moved could never be closed. Standing inside a different one, it
    # does not, because a run that has read nothing here would gate a landing
    # here on gates belonging to another project.
    if len(runs) != 1:
        return None
    try:
        inside = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(cwd),
                                capture_output=True, text=True)
    except OSError:
        # A directory that cannot be entered cannot be shown to be outside every
        # checkout, and a run that governs an unreadable place governs anywhere.
        return None
    return None if inside.returncode == 0 else runs[0]


def clear_pointers(run):
    """Drop every pointer naming this run.

    A pointer written under one name and removed under another survives the run
    it names, and a closed run behind a live pointer reads as an open one.
    """
    # Every pointer in the directory is considered, not the spellings this build
    # would have written: a run pinned by hand or by an older build carries a name
    # no formula reproduces. Only a pointer naming this run is removed.
    for pointer in list(RUNS.glob("current*.json")) + [CURRENT]:
        try:
            if pointer.is_file() and json.loads(pointer.read_text()).get("id") == run.get("id"):
                pointer.unlink()
        except (OSError, ValueError):
            continue


def session_pointer(session):
    if not session:
        return CURRENT
    return RUNS / ("current-%s%s.json" % (session, workspace_suffix()))


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


# ---------------------------------------------------------------- file kinds

MARKUP_EXT = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".astro", ".php",
              ".erb", ".liquid", ".hbs", ".ejs", ".njk", ".twig", ".mdx"}
CODE_EXT = {".js", ".ts", ".mjs", ".cjs"}
TEXT_EXT = {".md", ".txt", ".xml", ".json", ".yml", ".yaml"}
SOURCE_EXT = MARKUP_EXT | CODE_EXT | TEXT_EXT

# What a browser is served. Every gate here asks about a page - its head, its
# markup, its structured data - and a README documenting one of those, a lockfile
# naming a package or a config key spelt like an attribute is writing about the
# site rather than the site. Read as markup, that writing is reported as the
# defect, and where a match is what closes a gate it certifies a site by the
# sentence describing what it lacks. SOURCE_EXT is the whole sweep, and a check
# that wants it reads site["tree"] and says why.
SERVED_EXT = MARKUP_EXT | CODE_EXT

# Markdown-family files can be real routes, so they stay in the served set - but a
# fenced block in one is an example of markup rather than markup. Read whole, a
# route demonstrating the tags a page owes closes the gate the site itself does
# not meet, and a line warning against an anti-pattern is reported as the route
# committing it.
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[^\n]*$", re.M | re.S)
PROSE_EXT = {".md", ".mdx"}

RASTER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"}
MODERN_EXT = {".webp", ".avif"}
IMAGE_EXT = RASTER_EXT | MODERN_EXT | {".svg"}

# Trees nobody should be judged on. A dependency's missing alt text is not this
# project's, and sweeping it buries the files that are.
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", ".svelte-kit",
             "out", "vendor", "__pycache__", ".venv", "coverage", ".turbo", ".cache",
             ".intelligence", ".astro", "storybook-static", ".vercel", ".output",
             ".idea", ".vscode", ".fleet", ".gradle"}

# Files that look like pages but are never crawled: tests, stories, and the component
# library. Judging them holds a run open on markup no visitor reaches.
NON_PAGE = re.compile(r"\.(test|spec|stories|story|d)\.[jt]sx?$|__tests__|__mocks__", re.I)

# An HTML file with no document around it is a fragment - a header, a footer, a block
# included into the pages that do get served. Nothing ever requests it, so judging it
# as a route reports every page-level tag as missing on markup no visitor reaches, and
# a canonical or an og:image added to satisfy that would ship inside whichever page
# includes it. The test is what the file contains rather than where it sits, because
# the directory a fragment lives in is a convention and conventions vary per project.
DOCUMENT_MARKER = re.compile(r"<!doctype\s+html|<html[\s>]|<head[\s>]", re.I)


def _ignored_paths(root, paths):
    """The subset git ignores, asked in one batch.

    check-ignore --stdin answers for many paths in one process, which matters
    on a tree with several thousand files. A target outside a repository, or a
    git that will not answer, yields nothing: the hardcoded list above is what
    covers that case, and a ledger too wide is a worse failure than one file
    too many.
    """
    if not paths:
        return set()
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin"],
            input="\n".join(str(p) for p in paths), capture_output=True,
            text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    if done.returncode not in (0, 1):
        return set()
    return {line.strip() for line in done.stdout.splitlines() if line.strip()}


def iter_files(targets):
    seen = []
    for target in targets:
        p = Path(target).expanduser()
        if p.is_file():
            seen.append(p)
        elif p.is_dir():
            found = [c for c in p.rglob("*")
                     if c.is_file() and not any(part in SKIP_DIRS for part in c.parts)]
            # What git ignores is build output or local state. The skip list
            # above cannot keep up with what each project names its build.
            ignored = _ignored_paths(p, found)
            seen.extend(c for c in found if str(c) not in ignored)
    return seen


def project_root(path):
    """The repo the target sits in, or None when it is not inside one."""
    p = Path(path).expanduser().resolve()
    for candidate in [p] + list(p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def repo_root(start=None):
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=str(start or Path.cwd()), capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    root = out.stdout.strip()
    return str(Path(root).resolve()) if out.returncode == 0 and root else None


def differs_from_git(path):
    """Whether `path` still differs from what git has for it.

    Outside a working tree, or when git cannot answer, the file counts as changed:
    an unanswerable question must never read as a clean bill.
    """
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", path.name],
                             cwd=str(path.parent), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    if out.returncode != 0:
        return True
    return bool(out.stdout.strip())


# ---------------------------------------------------------------- registry


def load_gates():
    if not GATES_DIR.is_dir():
        raise SystemExit("No gate registry at %s" % GATES_DIR)
    groups = []
    for path in sorted(GATES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        defaults = data.get("defaults") or {}
        gates = []
        for gate in data["gates"]:
            merged = dict(defaults)
            merged.update(gate)
            merged.setdefault("kinds", ["*"])
            merged.setdefault("severity", "required")
            merged.setdefault("check", {"type": "manual"})
            merged.setdefault("when", "always")
            merged["group"] = data["group"]
            gates.append(merged)
        groups.append({"group": data["group"], "description": data.get("description", ""),
                       "gates": gates})
    return groups


def all_gates(groups):
    return [g for grp in groups for g in grp["gates"]]


def gate_index(groups):
    return {g["id"]: g for g in all_gates(groups)}


# ---------------------------------------------------------------- the site model

HEAD_BLOCK = re.compile(r"<head\b[^>]*>(.*?)</head>", re.S | re.I)
SVELTE_HEAD = re.compile(r"<svelte:head\b[^>]*>(.*?)</svelte:head>", re.S | re.I)

# Where a route lives, per framework. The route file is the unit a gate answers
# about, because that is the file a person edits to fix the gate.
ROUTE_PATTERNS = [
    (re.compile(r"(^|/)app/(.*/)?page\.[jt]sx?$"), "next-app"),
    (re.compile(r"(^|/)app/(.*/)?layout\.[jt]sx?$"), "next-layout"),
    (re.compile(r"(^|/)pages/(?!api/).*\.[jt]sx?$"), "next-pages"),
    (re.compile(r"(^|/)src/pages/.*\.(astro|md|mdx|html)$"), "astro"),
    (re.compile(r"(^|/)src/routes/(.*/)?\+page\.svelte$"), "sveltekit"),
    (re.compile(r"(^|/)pages/.*\.vue$"), "nuxt"),
    (re.compile(r"(^|/)src/(pages|views|routes|screens)/.*\.[jt]sx?$"), "spa-route"),
    (re.compile(r"(^|/)src/(pages|views|routes|screens)/.*\.vue$"), "spa-route"),
    (re.compile(r"\.html?$"), "html"),
    (re.compile(r"(^|/)(templates|views|layouts)/.*\.(html|erb|liquid|hbs|ejs|njk|twig|php)$"),
     "template"),
]


def classify_route(path, root):
    try:
        rel = str(Path(path).resolve().relative_to(root))
    except (ValueError, OSError):
        rel = str(path)
    rel = rel.replace(os.sep, "/")
    if NON_PAGE.search(rel):
        return None, rel
    for pattern, kind in ROUTE_PATTERNS:
        if pattern.search(rel):
            return kind, rel
    return None, rel


def head_text(text, route_kind):
    """The part of a route file that carries its head metadata.

    An HTML page keeps it in <head>. A framework route builds it in code - a metadata
    export, a helmet component, a svelte:head block - so for those the whole file is
    the head, since narrowing it would hide the very lines a gate asks about.
    """
    if route_kind in ("html", "template", "astro"):
        blocks = HEAD_BLOCK.findall(text)
        if blocks:
            return "\n".join(blocks)
    if route_kind == "sveltekit":
        blocks = SVELTE_HEAD.findall(text)
        if blocks:
            return "\n".join(blocks)
    return text


def collect_pages(run):
    """Every route under the run's targets, with its own text and its layout's.

    A Next.js page carries no title of its own when the layout sets one, so a gate
    that reads only the page file reports every route as untitled. The layout's text
    travels with each page it wraps, and a gate answers on the pair.
    """
    root = Path(run["targets"][0])
    root = root if root.is_dir() else root.parent
    project = project_root(root) or root
    layouts, pages = {}, []
    for f in iter_files(run["targets"]):
        if f.suffix.lower() not in MARKUP_EXT:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        kind, rel = classify_route(f, project)
        if kind in ("html", "template") and not DOCUMENT_MARKER.search(text[:4000]):
            continue
        if kind == "next-layout":
            layouts[str(f.parent.resolve())] = text
            continue
        if kind is None:
            continue
        pages.append({"path": str(f.resolve()), "rel": rel, "kind": kind, "text": text})
    for page in pages:
        inherited = []
        here = Path(page["path"]).parent.resolve()
        for candidate in [here] + list(here.parents):
            if str(candidate) in layouts:
                inherited.append(layouts[str(candidate)])
            if candidate == project:
                break
        page["chain"] = page["text"] + "\n" + "\n".join(inherited)
        page["head"] = head_text(page["text"], page["kind"]) + "\n" + "\n".join(
            head_text(t, "next-layout") for t in inherited)
    return pages, project


def read_sources(targets):
    out = {}
    for f in iter_files(targets):
        if f.suffix.lower() in SOURCE_EXT and f.stat().st_size < 4_000_000:
            try:
                out[str(f.resolve())] = f.read_text(errors="replace")
            except OSError:
                continue
    return out


def served_text(path, text):
    """One file as a browser receives it, with any documented example dropped."""
    if Path(path).suffix.lower() in PROSE_EXT:
        return FENCE.sub("", text)
    return text


def build_site(run):
    """The project in two shapes: what a browser is served, and the whole sweep.

    site["sources"] is the served set and is what every check reads unless it says
    otherwise. site["tree"] is every file the sweep touched - manifests, Markdown,
    lockfiles - and a check reaching for it is asking about the writing about the
    project on purpose.
    """
    pages, project = collect_pages(run)
    tree = read_sources(run["targets"])
    return {"pages": pages,
            "sources": {path: served_text(path, text) for path, text in tree.items()
                        if Path(path).suffix.lower() in SERVED_EXT},
            "tree": tree,
            "images": [f for f in iter_files(run["targets"]) if f.suffix.lower() in IMAGE_EXT],
            "root": project}


CONTEXT_SIGNS = {
    "markup": r"\.(html|htm|jsx|tsx|vue|svelte|astro|php|erb|liquid|hbs|ejs|njk|twig|mdx)$",
    "html": r"\.(html|htm)$",
    "react": r"\.(jsx|tsx)$",
    "next": r"next\.config|next/head|next/link|from ['\"]next/",
    "astro": r"\.astro$|astro\.config",
    "svelte": r"\.svelte$|svelte\.config",
    "vue": r"\.vue$|nuxt\.config",
    "spa": r"react-router|createBrowserRouter|vue-router|@angular/router",
    "image": r"<img\b|<picture\b|next/image|<Image\b|background-image",
    "link": r"<a\s|<Link\b|href=",
    "script": r"<script\b",
    "font": r"@font-face|fonts\.googleapis|next/font|font-family",
    "article": r"<article\b|BlogPosting|blog|/posts?/|/articles?/",
    "product": r"<Product\b|price|Price|addToCart|add-to-cart|/products?/",
    "video": r"<video\b|youtube\.com/embed|vimeo\.com",
    "nav": r"<nav\b|role=[\"']navigation",
}


def build_context(run, site):
    blob = "\n".join(site["sources"].keys()) + "\n" + "\n".join(site["sources"].values())
    ctx = {"always": True}
    for name_, pattern in CONTEXT_SIGNS.items():
        ctx[name_] = bool(re.search(pattern, blob, re.M))
    ctx["pages"] = bool(site["pages"])
    ctx["multipage"] = len(site["pages"]) > 1
    ctx["raster"] = any(f.suffix.lower() in RASTER_EXT for f in site["images"])
    ctx["jsonld"] = bool(re.search(r"application/ld\+json|\"@context\"|'@context'", blob))
    ctx["content"] = True
    # Declared by the operator, because none of these can be read out of source: a
    # single-locale site and one whose translations are not built yet look the same.
    for flag in run.get("flags", []):
        ctx[flag] = True
    for flag in ("international", "local", "ecommerce", "editorial"):
        ctx.setdefault(flag, False)
    return ctx



# ---------------------------------------------------------------- scopes

# A run answers for the whole project by default, and PRC-02 exists to stop that
# being narrowed quietly. A scope is the other thing: a narrowing said out loud.
# It is recorded on the run, every report leads with it and with what it did not
# look at, and finish repeats it, so a scoped pass can never be read as a full
# one. What it changes is which gates are asked, never how hard any is to answer.

SCOPES = {
    "crawl": (["CRW"], [], "robots, sitemaps, and what a crawler is allowed to reach"),
    "meta": (["HED"], [], "titles, descriptions, and canonical links"),
    "social": (["SOC"], [], "share cards"),
    "schema": (["SCH"], [], "structured data"),
    "structure": (["STR"], [], "headings, landmarks, and internal links"),
    "images": (["IMG"], [], "image alt text, dimensions, and loading"),
    "speed": (["PRF"], [], "loading and Core Web Vitals"),
    "content": (["CNT"], [], "content credibility and depth"),
    "local": (["LOC", "GEO"], [], "local business signals and hreflang"),
}

SCOPE_ALWAYS = {"run_opened", "all_resolved", "report_emitted", "writing_pass"}


def in_scope(gate, scope):
    if not scope:
        return True
    picked = SCOPES.get(scope)
    if not picked:
        return True
    prefixes, ids, _ = picked
    if gate["check"]["type"] in SCOPE_ALWAYS:
        return True
    if gate["id"] in ids:
        return True
    return any(gate["id"].startswith(p + "-") for p in prefixes)


def scope_line(run):
    """One sentence naming the narrowing, for anything that reports a run."""
    scope = run.get("scope")
    if not scope:
        return ""
    picked = SCOPES.get(scope)
    what = picked[2] if picked else scope
    return ("SCOPED RUN: %s. This answers for %s and for nothing else - the rest of the "
            "project was not looked at." % (scope, what))

def applicable(gate, run, ctx):
    if not in_scope(gate, run.get("scope")):
        return False, "outside the %s scope this run declared" % run["scope"]
    kinds = gate["kinds"]
    if "*" not in kinds and run["kind"] not in kinds:
        return False, "kind %s is out of scope for this gate" % run["kind"]
    if not ctx.get(gate["when"], False):
        return False, "project contains no %s" % gate["when"]
    return True, ""


# ---------------------------------------------------------------- check helpers

PASS, FAIL, UNKNOWN = "pass", "fail", "inconclusive"

HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def base(path):
    return Path(path).name


def rel_of(page):
    return page["rel"]


def _hits(sources, pattern, flags=re.I):
    out = []
    rx = re.compile(pattern, flags)
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                out.append("%s:%d" % (base(path), i))
    return out


def meta_content(blob, key, attr="name"):
    """The content of <meta name=key>, whichever attribute order the file uses."""
    for pattern in (
        r"<meta[^>]*\b%s\s*=\s*[\"']%s[\"'][^>]*\bcontent\s*=\s*[\"']([^\"']*)[\"']",
        r"<meta[^>]*\bcontent\s*=\s*[\"']([^\"']*)[\"'][^>]*\b%s\s*=\s*[\"']%s[\"']",
    ):
        m = re.search(pattern % (attr, re.escape(key)), blob, re.I)
        if m:
            return m.group(1)
    return None


# Where a framework route declares its head metadata. A route file is full of object
# keys called `title` - a card, a tab, a table column - and reading those as the page
# title reports a component label as the line a search result will show.
META_SCOPE = re.compile(
    r"export\s+const\s+metadata|export\s+async\s+function\s+generateMetadata|"
    r"useHead\s*\(|useSeoMeta\s*\(|definePageMeta\s*\(|<Helmet|<Head\b|<NextSeo|"
    r"<SEO\b|<Seo\b|\bhead\s*:\s*\{", re.I)

# How far past a metadata marker its declaration plausibly runs. Long enough to hold
# a title, a description and an openGraph block; short enough that the next component
# in the file is not swept in with it.
META_WINDOW = 900


def metadata_scope(page):
    """The regions of a route file that declare head metadata, and nothing else."""
    spans = []
    if page["kind"] in ("html", "template", "astro", "sveltekit"):
        spans.append(page["head"])
    text = page["chain"]
    for m in META_SCOPE.finditer(text):
        spans.append(text[m.start():m.start() + META_WINDOW])
    return "\n".join(spans)


# The framework equivalents of a meta tag. Reading only the tag reports every Next.js
# route as missing a description that its metadata export plainly sets.
FRAMEWORK_FIELD = {
    "title": r"\btitle\s*:|<title\b|\btitleTemplate\b",
    "description": r"\bdescription\s*:|\bmetaDescription\b|name\s*=\s*[\"']description[\"']",
    "canonical": r"\bcanonical\s*:|\balternates\s*:|rel\s*=\s*[\"']canonical[\"']",
    "robots": r"\brobots\s*:|name\s*=\s*[\"']robots[\"']",
}


def page_title(page):
    m = re.search(r"<title[^>]*>(.*?)</title>", page["chain"], re.S | re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"\btitle\s*:\s*[\"'`]([^\"'`]{2,})[\"'`]", metadata_scope(page))
    return m.group(1).strip() if m else None


def page_description(page):
    got = meta_content(page["chain"], "description")
    if got:
        return got.strip()
    m = re.search(r"\bdescription\s*:\s*[\"'`]([^\"'`]{2,})[\"'`]", metadata_scope(page))
    return m.group(1).strip() if m else None


def has_field(page, field):
    return bool(re.search(FRAMEWORK_FIELD[field], metadata_scope(page), re.I))


def indexable(page):
    """Whether a route is meant to reach a result page at all.

    A route carrying noindex has no result to compete for, so a canonical, a
    description and a share card have nothing to describe on it. Holding it to them
    produces markup no crawler keeps, on the one page whose job is to say there is
    nothing here.
    """
    scope = page.get("head") or page.get("chain") or ""
    return not re.search(r"<meta[^>]+name\s*=\s*[\"']robots[\"'][^>]*content\s*="
                         r"\s*[\"'][^\"']*noindex", scope, re.I)


def failing_pages(site, predicate, pages=None):
    return [p["rel"] for p in (site["pages"] if pages is None else pages) if not predicate(p)]


def verdict_pages(bad, total, what, fix_hint=""):
    if not bad:
        return PASS, "all %d route(s) %s" % (total, what)
    detail = "%d of %d route(s) fail: %s" % (len(bad), total, ", ".join(sorted(bad)[:8]))
    if len(bad) > 8:
        detail += " (+%d more)" % (len(bad) - 8)
    return FAIL, detail + ((" - " + fix_hint) if fix_hint else "")


# ---------------------------------------------------------------- head metadata


def check_title_present(gate, site, run, ctx):
    bad = failing_pages(site, lambda p: bool(page_title(p)) or has_field(p, "title"))
    return verdict_pages(bad, len(site["pages"]), "set a title")


def check_title_length(gate, site, run, ctx):
    lo, hi = gate["check"].get("min", 30), gate["check"].get("max", 60)
    bad = ["%s (%d chars)" % (p["rel"], len(page_title(p)))
           for p in site["pages"] if page_title(p) and not (lo <= len(page_title(p)) <= hi)]
    if not bad:
        return PASS, "every readable title sits within %d-%d characters" % (lo, hi)
    return FAIL, "%d title(s) outside %d-%d: %s" % (len(bad), lo, hi, ", ".join(bad[:8]))


def check_title_unique(gate, site, run, ctx):
    seen = {}
    for page in site["pages"]:
        title = page_title(page)
        if title:
            seen.setdefault(title, []).append(page["rel"])
    dupes = {t: v for t, v in seen.items() if len(v) > 1}
    if not dupes:
        return PASS, "%d distinct title(s) across the routes that set one" % len(seen)
    return FAIL, "%d title(s) reused: %s" % (len(dupes), "; ".join(
        "%r on %s" % (t, ", ".join(v[:4])) for t, v in list(dupes.items())[:4]))


def check_description_present(gate, site, run, ctx):
    pages = [p for p in site["pages"] if indexable(p)]
    bad = failing_pages(site, lambda p: bool(page_description(p)) or has_field(p, "description"),
                        pages)
    return verdict_pages(bad, len(pages), "set a meta description")


def check_description_length(gate, site, run, ctx):
    lo, hi = gate["check"].get("min", 120), gate["check"].get("max", 160)
    bad = ["%s (%d chars)" % (p["rel"], len(page_description(p)))
           for p in site["pages"]
           if page_description(p) and not (lo <= len(page_description(p)) <= hi)]
    if not bad:
        return PASS, "every readable description sits within %d-%d characters" % (lo, hi)
    return FAIL, "%d description(s) outside %d-%d: %s" % (len(bad), lo, hi, ", ".join(bad[:8]))


def check_description_unique(gate, site, run, ctx):
    seen = {}
    for page in site["pages"]:
        desc = page_description(page)
        if desc:
            seen.setdefault(desc, []).append(page["rel"])
    dupes = {d: v for d, v in seen.items() if len(v) > 1}
    if not dupes:
        return PASS, "%d distinct description(s)" % len(seen)
    return FAIL, "%d description(s) reused across routes: %s" % (
        len(dupes), "; ".join(", ".join(v[:3]) for v in list(dupes.values())[:4]))


def check_canonical(gate, site, run, ctx):
    pages = [p for p in site["pages"] if indexable(p)]
    bad = failing_pages(site, lambda p: has_field(p, "canonical"), pages)
    return verdict_pages(bad, len(pages), "declare a canonical URL",
                         "add rel=canonical, or the framework's alternates.canonical")


def check_viewport(gate, site, run, ctx):
    """The viewport belongs to the document shell, not to a route.

    Checked per route it fails every page of any stack that sets it once in the shell,
    which is every stack that has one.
    """
    if re.search(r"viewport", "\n".join(site["sources"].values()), re.I):
        return PASS, "the document shell declares a viewport"
    return FAIL, ("no viewport meta tag anywhere. Mobile is the primary crawler, and a page "
                  "without one renders at desktop width on a phone and is judged on that.")


def check_html_lang(gate, site, run, ctx):
    """A lang attribute on the root element, wherever this stack puts it.

    Frameworks hoist it out of the route file, so a page checked on its own reads as
    missing one on every stack that has a document shell.
    """
    blob = "\n".join(site["sources"].values())
    if re.search(r"<html[^>]*\blang\s*=\s*[\"'][a-zA-Z]{2}", blob):
        return PASS, "root element declares a language"
    if re.search(r"\blang\s*[:=]\s*[\"'][a-zA-Z]{2}", blob):
        return PASS, "the document shell sets a language attribute"
    return FAIL, "no lang attribute on the root element; screen readers and hreflang both need it"


def check_charset(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(r"<meta[^>]*charset|charset\s*=\s*[\"']utf-8", blob, re.I):
        return PASS, "the document declares a charset"
    return FAIL, "no charset declaration in the document shell"


def check_no_accidental_noindex(gate, site, run, ctx):
    """noindex left in the source is the one SEO defect that removes a site outright."""
    hits = [h for h in _hits(site["sources"], r"noindex") if "robots.txt" not in h]
    if not hits:
        return PASS, "no noindex directive in the source"
    return UNKNOWN, ("noindex appears at %s. Every one must be deliberate and scoped to a "
                     "route that should stay out of the index." % ", ".join(hits[:8]))


def check_meta_robots_explicit(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(FRAMEWORK_FIELD["robots"], blob, re.I):
        return PASS, "the project sets robots directives explicitly"
    return FAIL, "nothing sets a robots directive; indexing is left to whatever the host sends"


# ---------------------------------------------------------------- social cards


OG_REQUIRED = ["og:title", "og:description", "og:image", "og:url", "og:type"]


def check_open_graph(gate, site, run, ctx):
    bad = []
    for page in site["pages"]:
        if not indexable(page):
            continue
        scope = metadata_scope(page)
        if re.search(r"\bopenGraph\s*:", scope):
            continue
        missing = [k for k in OG_REQUIRED if k not in scope]
        if missing:
            bad.append("%s (missing %s)" % (page["rel"], ", ".join(missing)))
    if not bad:
        return PASS, "every route carries a complete Open Graph set"
    return FAIL, "%d route(s) short of the Open Graph set: %s" % (len(bad), "; ".join(bad[:6]))


def check_twitter_card(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(r"twitter:card|\btwitter\s*:\s*\{|twitter\.card", blob, re.I):
        return PASS, "a Twitter card type is declared"
    return FAIL, "no twitter:card declared, so shared links fall back to a bare text link"


def check_og_image_absolute(gate, site, run, ctx):
    """og:image has to be absolute: crawlers do not resolve it against the page."""
    bad = []
    for path, text in site["sources"].items():
        for m in re.finditer(r"og:image[\"'][^>]*content\s*=\s*[\"']([^\"']+)", text, re.I):
            if not m.group(1).startswith(("http://", "https://", "{", "$")):
                bad.append("%s -> %s" % (base(path), m.group(1)))
    if not bad:
        return PASS, "every literal og:image is absolute"
    return FAIL, "%d relative og:image value(s): %s" % (len(bad), ", ".join(bad[:6]))


# ---------------------------------------------------------------- structure


HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.S | re.I)


def headings_of(page):
    body = HTML_COMMENT.sub(" ", page["text"])
    return [(int(level), re.sub(r"<[^>]+>|\s+", " ", txt).strip())
            for level, txt in HEADING.findall(body)]


def check_single_h1(gate, site, run, ctx):
    bad = ["%s (%d)" % (p["rel"], len([h for h in headings_of(p) if h[0] == 1]))
           for p in site["pages"] if len([h for h in headings_of(p) if h[0] == 1]) > 1]
    if not bad:
        return PASS, "no route declares more than one h1"
    return FAIL, "%d route(s) with several h1 elements: %s" % (len(bad), ", ".join(bad[:8]))


def check_h1_present(gate, site, run, ctx):
    """Routes whose own markup carries headings but never opens with an h1."""
    bad = []
    for page in site["pages"]:
        levels = [h[0] for h in headings_of(page)]
        if levels and 1 not in levels:
            bad.append("%s (starts at h%d)" % (page["rel"], min(levels)))
    if not bad:
        return PASS, "every route that renders headings opens with an h1"
    return FAIL, "%d route(s) render headings with no h1: %s" % (len(bad), ", ".join(bad[:8]))


def check_heading_order(gate, site, run, ctx):
    bad = []
    for page in site["pages"]:
        levels = [h[0] for h in headings_of(page)]
        for prev, nxt in zip(levels, levels[1:]):
            if nxt > prev + 1:
                bad.append("%s (h%d then h%d)" % (page["rel"], prev, nxt))
                break
    if not bad:
        return PASS, "no route skips a heading level"
    return FAIL, "%d route(s) skip a level: %s" % (len(bad), ", ".join(bad[:8]))


def check_landmarks(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    missing = [tag for tag in ("<main", "<nav", "<header", "<footer")
               if not re.search(tag + r"\b", blob, re.I)
               and not re.search(r"role\s*=\s*[\"']%s[\"']" % tag.strip("<"), blob, re.I)]
    if not missing:
        return PASS, "main, nav, header and footer landmarks all present"
    return FAIL, ("no %s landmark anywhere in the markup; the accessibility tree is the "
                  "cleanest signal an agent reads" % ", ".join(m.strip("<") for m in missing))


VAGUE_ANCHOR = r">\s*(click here|read more|learn more|here|this|more|link|download)\s*<"


def check_descriptive_anchors(gate, site, run, ctx):
    hits = _hits(site["sources"], VAGUE_ANCHOR)
    if not hits:
        return PASS, "no anchor carries a placeholder label"
    return FAIL, ("%d anchor(s) whose whole label is a placeholder: %s. The anchor text is "
                  "what the destination ranks for." % (len(hits), ", ".join(hits[:8])))


def check_internal_links(gate, site, run, ctx):
    """A route linked from nowhere is one the crawler reaches only via the sitemap."""
    bare = [p["rel"] for p in site["pages"]
            if not re.search(r"<a\s|<Link\b|href=", p["chain"], re.I)]
    if not bare:
        return PASS, "every route links onward to something"
    return FAIL, "%d route(s) contain no outgoing link: %s" % (len(bare), ", ".join(bare[:8]))


def check_route_shape(gate, site, run, ctx):
    bad = [p["rel"] for p in site["pages"]
           if re.search(r"[A-Z]|_", Path(p["rel"]).parent.as_posix())]
    if not bad:
        return PASS, "route segments are lowercase and hyphenated"
    return FAIL, ("%d route path(s) use uppercase or underscores: %s. URLs are case-sensitive, "
                  "and an underscore does not separate words for a crawler."
                  % (len(bad), ", ".join(sorted(bad)[:8])))


def check_breadcrumbs(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(r"BreadcrumbList|aria-label\s*=\s*[\"']breadcrumb", blob, re.I):
        return PASS, "the project renders a breadcrumb trail"
    return FAIL, ("no breadcrumb trail. It is the one navigation element Google renders in "
                  "place of the URL in a result.")


# ---------------------------------------------------------------- crawlability


def find_file(site, *names):
    """A crawler-visible file, wherever this stack serves static assets from."""
    root = site["root"]
    places = [root, root / "public", root / "static", root / "www", root / "dist",
              root / "src" / "public", root / "assets"]
    for place in places:
        for target in names:
            if (place / target).is_file():
                return place / target
    for target in names:
        for found in root.rglob(target):
            if not any(part in SKIP_DIRS for part in found.parts):
                return found
    return None


def check_robots_txt(gate, site, run, ctx):
    found = find_file(site, "robots.txt")
    if not found:
        return FAIL, ("no robots.txt anywhere the site serves static files. Without one the "
                      "crawler has no sitemap pointer and no crawl policy.")
    return PASS, "robots.txt at %s" % found


def check_robots_declares_sitemap(gate, site, run, ctx):
    found = find_file(site, "robots.txt")
    if not found:
        return FAIL, "no robots.txt, so nothing declares a sitemap"
    text = found.read_text(errors="replace")
    if re.search(r"^\s*Sitemap\s*:\s*https?://", text, re.I | re.M):
        return PASS, "robots.txt declares an absolute sitemap URL"
    if re.search(r"^\s*Sitemap\s*:", text, re.I | re.M):
        return FAIL, "the Sitemap line in robots.txt is not an absolute URL"
    return FAIL, "robots.txt declares no Sitemap line"


def check_no_blanket_disallow(gate, site, run, ctx):
    found = find_file(site, "robots.txt")
    if not found:
        return FAIL, "no robots.txt to read"
    text = found.read_text(errors="replace")
    for block in re.split(r"(?im)^\s*User-agent\s*:", text)[1:]:
        agent = block.splitlines()[0].strip()
        if agent == "*" and re.search(r"^\s*Disallow\s*:\s*/\s*$", block, re.I | re.M):
            return FAIL, "robots.txt disallows the whole site for every crawler"
    return PASS, "no blanket disallow for the default user-agent"


def check_sitemap(gate, site, run, ctx):
    found = find_file(site, "sitemap.xml", "sitemap-index.xml", "sitemap_index.xml")
    if found:
        return PASS, "sitemap at %s" % found
    # The manifest names the generator, so this one reads the whole tree on purpose.
    blob = "\n".join(site["tree"].values()) + "\n" + "\n".join(site["tree"].keys())
    if re.search(r"next-sitemap|@astrojs/sitemap|sitemap\.xml\.[jt]s|generateSitemaps|"
                 r"vite-plugin-sitemap|gatsby-plugin-sitemap|sitemap_generator", blob, re.I):
        return PASS, "a sitemap is generated at build time"
    return FAIL, "no sitemap.xml and no generator configured to produce one"


AI_CRAWLERS = ["GPTBot", "ChatGPT-User", "ClaudeBot", "PerplexityBot", "Bytespider",
               "Google-Extended", "CCBot", "anthropic-ai", "Applebot-Extended"]


def check_ai_crawler_policy(gate, site, run, ctx):
    """An explicit stance on AI crawlers, either direction.

    The gate is not that they be blocked. It is that the choice was made: silence
    means whatever each company decides, which is not a decision the project made.
    """
    found = find_file(site, "robots.txt")
    if not found:
        return FAIL, "no robots.txt, so no stance on AI crawlers"
    text = found.read_text(errors="replace")
    named = [k for k in AI_CRAWLERS if re.search(re.escape(k), text, re.I)]
    if named:
        return PASS, "robots.txt names %d AI crawler(s): %s" % (len(named), ", ".join(named))
    return FAIL, ("robots.txt takes no position on AI crawlers. Name them and allow or disallow "
                  "deliberately - blocking Google-Extended does not affect Search, and blocking "
                  "GPTBot does not stop ChatGPT citing the site.")


def check_llms_txt(gate, site, run, ctx):
    found = find_file(site, "llms.txt")
    if found:
        return PASS, "llms.txt at %s" % found
    return FAIL, "no llms.txt giving an AI reader the site's shape in one fetch"


# ---------------------------------------------------------------- structured data


LDJSON = re.compile(r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
                    re.S | re.I)

DEPRECATED_TYPES = ["HowTo", "SpecialAnnouncement", "CourseInfo", "EstimatedSalary",
                    "LearningVideo", "ClaimReview", "VehicleListing"]

PLACEHOLDER = re.compile(r"\[(?:Company Name|Your|Insert|Logo URL|Phone|Website URL)[^\]]*\]"
                         r"|YOUR_|lorem ipsum|example\.com", re.I)


def jsonld_blocks(site):
    """Every literal JSON-LD island in the project, with the file it came from."""
    return [(path, body.strip()) for path, text in site["sources"].items()
            for body in LDJSON.findall(text)]


def check_jsonld_present(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if jsonld_blocks(site) or re.search(r"application/ld\+json|[\"']@context[\"']", blob):
        return PASS, "the project emits JSON-LD"
    return FAIL, ("no structured data anywhere. JSON-LD is how a page states what it is rather "
                  "than leaving it to be inferred.")


def check_jsonld_parses(gate, site, run, ctx):
    bad = []
    for path, body in jsonld_blocks(site):
        if "{{" in body or "${" in body:
            continue
        try:
            data = json.loads(body)
        except ValueError as exc:
            bad.append("%s (%s)" % (base(path), str(exc)[:60]))
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict) or "@context" not in node or "@type" not in node:
                bad.append("%s (no @context/@type)" % base(path))
    if not bad:
        return PASS, "every literal JSON-LD island parses and declares @context and @type"
    return FAIL, "%d invalid JSON-LD island(s): %s" % (len(bad), ", ".join(bad[:6]))


def check_jsonld_no_deprecated(gate, site, run, ctx):
    hits = ["%s -> %s" % (base(path), kind) for path, text in site["sources"].items()
            for kind in DEPRECATED_TYPES
            if re.search(r"[\"']@type[\"']\s*:\s*[\"']%s[\"']" % kind, text)]
    if not hits:
        return PASS, "no retired schema type in use"
    return FAIL, ("%d retired type(s) in the markup: %s. These produce no rich result and have "
                  "not since Google removed them." % (len(hits), ", ".join(hits[:6])))


def check_jsonld_no_placeholder(gate, site, run, ctx):
    bad = sorted({base(path) for path, body in jsonld_blocks(site) if PLACEHOLDER.search(body)})
    if not bad:
        return PASS, "no placeholder text left inside the structured data"
    return FAIL, ("placeholder values still in the JSON-LD at %s. Structured data that "
                  "contradicts the page is a manual-action risk." % ", ".join(bad[:6]))


def check_jsonld_absolute_urls(gate, site, run, ctx):
    bad = ["%s -> %s" % (base(path), m.group(1))
           for path, body in jsonld_blocks(site)
           for m in re.finditer(
               r"[\"'](?:url|logo|image|sameAs|@id)[\"']\s*:\s*[\"'](/[^\"']*)[\"']", body)]
    if not bad:
        return PASS, "every URL in the structured data is absolute"
    return FAIL, ("%d relative URL(s) inside JSON-LD: %s. Structured data is consumed away from "
                  "the page, so a relative path resolves against nothing."
                  % (len(bad), ", ".join(bad[:6])))


def check_identity_schema(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    # @type carries a string or an array of them, so a node naming both its subtype and
    # its base - the shape a FinancialService uses to say it is also an Organization -
    # reads as anonymous to a match written for the scalar spelling alone.
    for kind in ("Organization", "LocalBusiness", "Person", "WebSite"):
        if re.search(r"[\"']@type[\"']\s*:\s*(?:[\"']%s[\"']|\[[^\]]*[\"']%s[\"'])"
                     % (kind, kind), blob):
            return PASS, "the site declares its identity as %s" % kind
    return FAIL, ("nothing declares who publishes this site. Organization or Person is the node "
                  "every other entity claim hangs off.")


def check_breadcrumb_schema(gate, site, run, ctx):
    if "BreadcrumbList" in "\n".join(site["sources"].values()):
        return PASS, "BreadcrumbList markup present"
    return FAIL, "no BreadcrumbList markup, so results show the raw URL instead of the trail"


def check_article_schema(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(r"[\"']@type[\"']\s*:\s*[\"'](?:Article|BlogPosting|NewsArticle)[\"']", blob):
        return PASS, "article routes carry Article markup"
    return FAIL, "the project publishes articles but emits no Article or BlogPosting markup"


def check_product_schema(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(r"[\"']@type[\"']\s*:\s*[\"'](?:Product|ProductGroup)[\"']", blob):
        return PASS, "product routes carry Product markup"
    return FAIL, "the project sells products but emits no Product markup"


# ---------------------------------------------------------------- images


IMG_TAG = re.compile(r"<(?:img|Image)\b[^>]*>", re.I)


def img_tags(site):
    out = []
    for path, text in site["sources"].items():
        if Path(path).suffix.lower() not in MARKUP_EXT:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for tag in IMG_TAG.findall(line):
                out.append((path, i, tag))
    return out


def check_img_alt(gate, site, run, ctx):
    bad = ["%s:%d" % (base(p), i) for p, i, tag in img_tags(site)
           if not re.search(r"\balt\s*=", tag, re.I)]
    if not bad:
        return PASS, "every image element carries an alt attribute"
    return FAIL, ("%d image(s) with no alt attribute: %s. Decorative images take alt=\"\"; there "
                  "is no third option." % (len(bad), ", ".join(bad[:8])))


GENERIC_ALT = {"image", "photo", "picture", "logo", "icon", "img", "banner", "graphic"}


def check_img_alt_quality(gate, site, run, ctx):
    bad = []
    for p, i, tag in img_tags(site):
        m = re.search(r"\balt\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        if not m or not m.group(1).strip():
            continue
        value = m.group(1).strip()
        if re.search(r"\.(png|jpe?g|webp|gif|svg)$", value, re.I) or value.lower() in GENERIC_ALT:
            bad.append("%s:%d (%r)" % (base(p), i, value[:30]))
        elif len(value) > 125:
            bad.append("%s:%d (%d chars)" % (base(p), i, len(value)))
    if not bad:
        return PASS, "alt text describes the image rather than naming the file"
    return FAIL, "%d alt value(s) that describe nothing: %s" % (len(bad), ", ".join(bad[:8]))


def check_img_dimensions(gate, site, run, ctx):
    bad = ["%s:%d" % (base(p), i) for p, i, tag in img_tags(site)
           if not (re.search(r"\bwidth\s*=", tag, re.I) and re.search(r"\bheight\s*=", tag, re.I))
           and not re.search(r"\bfill\b|aspect-", tag, re.I)]
    if not bad:
        return PASS, "every image reserves its space before it loads"
    return FAIL, ("%d image(s) with no width/height: %s. An image without dimensions is the "
                  "single most common cause of layout shift." % (len(bad), ", ".join(bad[:8])))


def check_img_modern_format(gate, site, run, ctx):
    heavy = [f for f in site["images"]
             if f.suffix.lower() in RASTER_EXT and f.stat().st_size > 200_000]
    if not heavy:
        return PASS, "no raster image over 200KB"
    listing = ", ".join("%s (%dKB)" % (f.name, f.stat().st_size // 1024)
                        for f in sorted(heavy, key=lambda x: -x.stat().st_size)[:8])
    return FAIL, ("%d raster image(s) over 200KB: %s. WebP or AVIF at the same visual quality is "
                  "typically a third the bytes." % (len(heavy), listing))


def check_img_lazy(gate, site, run, ctx):
    tags = img_tags(site)
    if len(tags) <= 3:
        return PASS, "%d image element(s); deferring buys nothing at this count" % len(tags)
    lazy = [t for t in tags
            if re.search(r"loading\s*=\s*[\"']lazy[\"']|data-src|priority", t[2], re.I)]
    if not lazy:
        return FAIL, ("none of the %d image elements defer loading. Everything below the fold "
                      "should carry loading=\"lazy\"." % len(tags))
    return PASS, "%d of %d image elements defer loading" % (len(lazy), len(tags))


def check_lcp_image_eager(gate, site, run, ctx):
    """The hero must not be lazy: deferring the largest paint delays the metric it is measured by."""
    bad = sorted({base(path) for path, text in site["sources"].items()
                  if Path(path).suffix.lower() in MARKUP_EXT
                  for tag in IMG_TAG.findall(text[:4000])
                  if re.search(r"loading\s*=\s*[\"']lazy[\"']", tag, re.I)})
    if not bad:
        return PASS, "no lazily-loaded image sits at the top of a document"
    return FAIL, ("the first image in %s is lazy-loaded. Mark the hero eager, and preload it."
                  % ", ".join(bad[:6]))


# ---------------------------------------------------------------- performance


def check_script_defer(gate, site, run, ctx):
    bad = []
    for path, text in site["sources"].items():
        if Path(path).suffix.lower() not in MARKUP_EXT:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for tag in re.findall(r"<script\b[^>]*\bsrc\s*=[^>]*>", line, re.I):
                if not re.search(r"\b(?:defer|async)\b|type\s*=\s*[\"']module[\"']", tag, re.I):
                    bad.append("%s:%d" % (base(path), i))
    if not bad:
        return PASS, "no render-blocking script tag"
    return FAIL, ("%d external script(s) block rendering: %s. Add defer or async unless the "
                  "script must run before first paint." % (len(bad), ", ".join(bad[:8])))


def check_font_display(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if not re.search(r"@font-face|fonts\.googleapis|next/font", blob, re.I):
        return PASS, "no web font loaded"
    if re.search(r"font-display\s*:|display\s*=\s*swap|display\s*:\s*[\"']swap", blob, re.I):
        return PASS, "web fonts declare a display strategy"
    return FAIL, ("web fonts load with no font-display, so text stays invisible until the font "
                  "arrives and the paint that counts is delayed.")


def check_preconnect(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    hosts = {h for h in re.findall(r"https?://([a-z0-9.-]+\.[a-z]{2,})/", blob, re.I)
             if not h.startswith(("localhost", "127."))}
    if len(hosts) < 3:
        return PASS, "few enough third-party origins that preconnect buys nothing"
    if re.search(r"rel\s*=\s*[\"'](?:preconnect|dns-prefetch)[\"']", blob, re.I):
        return PASS, "the document preconnects to its third-party origins"
    return FAIL, ("%d third-party origins are fetched with no preconnect: %s"
                  % (len(hosts), ", ".join(sorted(hosts)[:6])))


def check_lcp_preload(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(r"rel\s*=\s*[\"']preload[\"']|\bpriority\b|fetchpriority", blob, re.I):
        return PASS, "the largest paint candidate is given priority"
    return FAIL, ("nothing preloads or prioritises the hero image. Its request otherwise waits "
                  "behind the stylesheet that discovers it.")


def check_no_layout_shift_sources(gate, site, run, ctx):
    hits = _hits(site["sources"], r"document\.write\(|innerHTML\s*\+?=\s*[\"'`]<")
    if not hits:
        return PASS, "nothing injects markup after first paint"
    return FAIL, ("%d site(s) of post-paint markup injection: %s. Content arriving after layout "
                  "moves everything below it." % (len(hits), ", ".join(hits[:6])))


def check_back_button_intact(gate, site, run, ctx):
    """Defeating the Back button is a spam-policy violation Google enforces with manual actions."""
    hits = _hits(site["sources"], r"window\.onpopstate\s*=|onpopstate\s*=\s*function")
    if not hits:
        return PASS, "no history manipulation outside the router"
    return UNKNOWN, ("history is manipulated at %s. Confirm the Back button still leaves the site "
                     "in one press." % ", ".join(hits[:6]))


def check_html_weight(gate, site, run, ctx):
    """Googlebot reads the first 2MB of HTML; anything past it is not indexed."""
    heavy = ["%s (%dKB)" % (base(path), len(text.encode()) // 1024)
             for path, text in site["sources"].items()
             if Path(path).suffix.lower() in {".html", ".htm"} and len(text.encode()) > 1_500_000]
    if not heavy:
        return PASS, "no document approaches the 2MB fetch limit"
    return FAIL, "%d document(s) near or past Googlebot's 2MB cap: %s" % (len(heavy), ", ".join(heavy))


# ---------------------------------------------------------------- content and GEO


def visible_text(page):
    body = HTML_COMMENT.sub(" ", page["text"])
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", body)


def word_count(page):
    return len(re.findall(r"[A-Za-z][A-Za-z'-]{1,}", visible_text(page)))


def check_thin_content(gate, site, run, ctx):
    floor = gate["check"].get("min_words", 300)
    # A route that renders its copy from data carries none of its own, and counting the
    # template's words would call every such route thin.
    # A route carrying noindex competes for nothing, so word count says nothing about
    # it. An error page is the clearest case: being short is the whole job.
    thin = ["%s (%d words)" % (p["rel"], word_count(p)) for p in site["pages"]
            if indexable(p) and word_count(p) < floor
            and not re.search(r"\{\s*(?:children|slot|content|body)\b|<slot", p["text"], re.I)]
    if not thin:
        return PASS, "no route falls below %d words of its own copy" % floor
    return FAIL, "%d route(s) under %d words: %s" % (len(thin), floor, ", ".join(thin[:8]))


def check_no_lorem(gate, site, run, ctx):
    hits = _hits(site["sources"], r"lorem ipsum|dolor sit amet|TK TK")
    if not hits:
        return PASS, "no filler copy left in the markup"
    return FAIL, "%d occurrence(s) of placeholder copy: %s" % (len(hits), ", ".join(hits[:8]))


def check_question_headings(gate, site, run, ctx):
    """Question-shaped headings match the query, which is what a generative answer selects on."""
    total = sum(len(headings_of(p)) for p in site["pages"])
    if total < 4:
        return PASS, "too few headings for the shape of them to matter"
    questions = sum(1 for p in site["pages"] for _, txt in headings_of(p)
                    if txt.endswith("?")
                    or re.match(r"(how|what|why|when|where|which|who|can|does|is)\b", txt, re.I))
    if questions:
        return PASS, "%d of %d headings are question-shaped" % (questions, total)
    return FAIL, ("none of the %d headings is phrased as a question. A heading that matches the "
                  "query is what an AI answer quotes." % total)


def check_dates_visible(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(r"datePublished|dateModified|<time\b|Last updated", blob, re.I):
        return PASS, "publication or update dates are rendered"
    return FAIL, "nothing renders a publication or last-updated date on editorial content"


def check_author_signals(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if re.search(r"[\"']author[\"']\s*:|rel\s*=\s*[\"']author[\"']|byline|Written by|<address\b",
                 blob, re.I):
        return PASS, "content carries an author attribution"
    return FAIL, ("nothing attributes the content to an author. Experience and authorship are "
                  "what the quality raters are told to look for.")


def check_answer_front_loaded(gate, site, run, ctx):
    """The opening has to answer the heading, because that is the part that gets quoted."""
    late = []
    for page in site["pages"]:
        words = visible_text(page).split()
        if len(words) < 120:
            continue
        if not re.search(r"\bis\b|\bare\b|\bmeans\b|\brefers to\b|\bdoes\b",
                         " ".join(words[:60]), re.I):
            late.append(page["rel"])
    if not late:
        return PASS, "every long route states its answer in the opening lines"
    return FAIL, ("%d route(s) open without a direct statement: %s. Roughly half of AI citations "
                  "come from the first third of a page." % (len(late), ", ".join(late[:8])))


def check_semantic_html(gate, site, run, ctx):
    hits = _hits(site["sources"], r"<div[^>]*\bonclick|<span[^>]*\bonclick")
    if not hits:
        return PASS, "interaction lives on real buttons and links"
    return FAIL, ("%d clickable div/span: %s. An agent reading the accessibility tree sees "
                  "nothing it can act on." % (len(hits), ", ".join(hits[:8])))


# ---------------------------------------------------------------- international


def hreflang_values(site):
    return set(re.findall(r"hreflang\s*=\s*[\"']([a-zA-Z-]+)[\"']",
                          "\n".join(site["sources"].values())))


def check_hreflang_reciprocal(gate, site, run, ctx):
    langs = hreflang_values(site)
    if not langs:
        return FAIL, "the project is flagged international but declares no hreflang"
    if "x-default" not in {l.lower() for l in langs}:
        return FAIL, ("hreflang declares %s but no x-default, so a visitor whose locale is "
                      "unlisted is sent nowhere in particular." % ", ".join(sorted(langs)))
    return PASS, "hreflang covers %s including x-default" % ", ".join(sorted(langs))


def check_locale_format(gate, site, run, ctx):
    bad = [l for l in hreflang_values(site)
           if l.lower() != "x-default" and not re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", l)]
    if not bad:
        return PASS, "every hreflang value is a well-formed language or language-region code"
    return FAIL, "malformed hreflang value(s): %s" % ", ".join(sorted(bad))


# ---------------------------------------------------------------- local


def check_nap_consistency(gate, site, run, ctx):
    """One address and one phone number, or the citation signal splits in two."""
    blob = "\n".join(site["sources"].values())
    found = re.findall(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", blob)
    # A tel: href is E.164 and the text beside it is punctuated for a reader, so one
    # number legitimately appears in several shapes on a correctly marked-up page.
    # What splits a citation is a second number, not a second format, so the digits
    # are what get compared.
    phones = {re.sub(r"\D", "", n).lstrip("1") for n in found}
    if len(phones) <= 1:
        return PASS, "one phone number across the site" if phones else "no phone number rendered"
    return FAIL, ("%d different phone numbers appear: %s. Citations are matched on an exact "
                  "name, address and phone." % (len(phones), ", ".join(sorted(phones)[:6])))


def check_localbusiness_schema(gate, site, run, ctx):
    blob = "\n".join(site["sources"].values())
    if not re.search(r"[\"']@type[\"']\s*:\s*[\"'][A-Za-z]*(?:LocalBusiness|Store|Restaurant|"
                     r"Dentist|Plumber|Attorney|MedicalBusiness)[\"']", blob):
        return FAIL, "no LocalBusiness markup on a site flagged local"
    missing = [f for f in ("address", "telephone", "openingHours", "geo") if '"%s"' % f not in blob]
    if missing:
        return FAIL, "LocalBusiness markup omits %s" % ", ".join(missing)
    return PASS, "LocalBusiness markup carries address, telephone, hours and geo"


# ---------------------------------------------------------------- process gates


def check_run_opened(gate, site, run, ctx):
    return PASS, "run %s opened %s" % (run["id"], run["opened"])


def check_all_resolved(gate, site, run, ctx):
    open_ids = [g for g, r in run["results"].items() if r["status"] not in ("pass", "fixed", "na")]
    if open_ids:
        return FAIL, "%d gate(s) still unanswered" % len(open_ids)
    return PASS, "every applicable gate answered"


def check_report_emitted(gate, site, run, ctx):
    if run.get("report_at"):
        return PASS, "report rendered %s" % run["report_at"]
    return FAIL, "no report rendered yet"


def check_scope_coverage(gate, site, run, ctx):
    """Routes of the same kind sitting outside the run's targets.

    A prompt names one page; the deliverable is every route a crawler reaches. Scoping
    the run to what the prompt mentioned is how ninety gates become nine.
    """
    root = site["root"]
    if not root:
        return PASS, "targets are not inside a repo; scope is whatever was pointed at"
    targets = [Path(t).resolve() for t in run["targets"]]
    outside = []
    for f in iter_files([root]):
        if f.suffix.lower() not in MARKUP_EXT:
            continue
        kind, rel = classify_route(f, root)
        if kind is None:
            continue
        resolved = f.resolve()
        if any(resolved == t or t in resolved.parents for t in targets):
            continue
        outside.append(rel)
    if outside:
        return UNKNOWN, ("%d route(s) sit outside the run's targets: %s. Widen the targets, or "
                         "say why they are not part of this deliverable."
                         % (len(outside), ", ".join(sorted(outside)[:8])))
    return PASS, "targets cover every route in the project"


def check_file_coverage(gate, site, run, ctx):
    files = run.get("files") or {}
    if not files:
        return UNKNOWN, "no file ledger on this run; rescan it"
    unread = [p for p, r in files.items() if not r["read"]]
    unruled = [p for p, r in files.items() if r["status"] == "open"]
    if not unruled:
        return PASS, "all %d file(s) read and ruled on" % len(files)
    detail = "%d of %d file(s) not ruled on" % (len(unruled), len(files))
    if unread:
        detail += ", %d of them never read" % len(unread)
    return FAIL, detail + ": " + ", ".join(base(p) for p in sorted(unruled)[:10])


def check_writing_pass(gate, site, run, ctx):
    """Titles and descriptions are read by people, so they answer to the writing standard."""
    tool = HOME / ".claude" / "tools" / "writing-pass.py"
    if not tool.exists():
        return UNKNOWN, "writing-pass.py not present - invoke avoid-ai-writing and attest by hand"
    # Copy is the subject here, so Markdown is read on purpose rather than by accident.
    files = [p for p in site["tree"] if Path(p).suffix.lower() in MARKUP_EXT | {".md"}]
    if not files:
        return UNKNOWN, "no copy-bearing file under the targets - attest the copy pass by hand"
    proc = subprocess.run([str(tool), "check", *files[:40]], capture_output=True, text=True)
    if proc.returncode == 0:
        return PASS, "writing-pass clean over %d file(s)" % len(files[:40])
    # Only the rows that failed. The passing rows come first and are the bulk of the
    # output, so truncating the head of it reports a failure by showing what passed.
    flagged = [l.strip() for l in (proc.stdout or proc.stderr).splitlines()
               if l.strip() and not l.strip().startswith("ok")]
    return FAIL, " | ".join(flagged)[:400] or "writing-pass exited %d" % proc.returncode


CHECKS = {
    "manual": None,
    "title_present": check_title_present,
    "title_length": check_title_length,
    "title_unique": check_title_unique,
    "description_present": check_description_present,
    "description_length": check_description_length,
    "description_unique": check_description_unique,
    "canonical": check_canonical,
    "viewport": check_viewport,
    "html_lang": check_html_lang,
    "charset": check_charset,
    "no_accidental_noindex": check_no_accidental_noindex,
    "meta_robots_explicit": check_meta_robots_explicit,
    "open_graph": check_open_graph,
    "twitter_card": check_twitter_card,
    "og_image_absolute": check_og_image_absolute,
    "single_h1": check_single_h1,
    "h1_present": check_h1_present,
    "heading_order": check_heading_order,
    "landmarks": check_landmarks,
    "descriptive_anchors": check_descriptive_anchors,
    "internal_links": check_internal_links,
    "route_shape": check_route_shape,
    "breadcrumbs": check_breadcrumbs,
    "robots_txt": check_robots_txt,
    "robots_declares_sitemap": check_robots_declares_sitemap,
    "no_blanket_disallow": check_no_blanket_disallow,
    "sitemap": check_sitemap,
    "ai_crawler_policy": check_ai_crawler_policy,
    "llms_txt": check_llms_txt,
    "jsonld_present": check_jsonld_present,
    "jsonld_parses": check_jsonld_parses,
    "jsonld_no_deprecated": check_jsonld_no_deprecated,
    "jsonld_no_placeholder": check_jsonld_no_placeholder,
    "jsonld_absolute_urls": check_jsonld_absolute_urls,
    "identity_schema": check_identity_schema,
    "breadcrumb_schema": check_breadcrumb_schema,
    "article_schema": check_article_schema,
    "product_schema": check_product_schema,
    "img_alt": check_img_alt,
    "img_alt_quality": check_img_alt_quality,
    "img_dimensions": check_img_dimensions,
    "img_modern_format": check_img_modern_format,
    "img_lazy": check_img_lazy,
    "lcp_image_eager": check_lcp_image_eager,
    "script_defer": check_script_defer,
    "font_display": check_font_display,
    "preconnect": check_preconnect,
    "lcp_preload": check_lcp_preload,
    "no_layout_shift_sources": check_no_layout_shift_sources,
    "back_button_intact": check_back_button_intact,
    "html_weight": check_html_weight,
    "thin_content": check_thin_content,
    "no_lorem": check_no_lorem,
    "question_headings": check_question_headings,
    "dates_visible": check_dates_visible,
    "author_signals": check_author_signals,
    "answer_front_loaded": check_answer_front_loaded,
    "semantic_html": check_semantic_html,
    "hreflang_reciprocal": check_hreflang_reciprocal,
    "locale_format": check_locale_format,
    "nap_consistency": check_nap_consistency,
    "localbusiness_schema": check_localbusiness_schema,
    "writing_pass": check_writing_pass,
    "run_opened": check_run_opened,
    "all_resolved": check_all_resolved,
    "report_emitted": check_report_emitted,
    "scope_coverage": check_scope_coverage,
    "file_coverage": check_file_coverage,
}


# These describe the run rather than the site, so the tool settles them from its own
# state. Left resolvable by hand they would be the way around everything else: mark
# "every gate answered" as n/a and the report requirement evaporates.
SELF_SETTLING = {"run_opened", "all_resolved", "report_emitted", "file_coverage"}


# ---------------------------------------------------------------- carry-forward

# Check types that answer to something outside the project, whose answer a file
# hash therefore cannot vouch for. Every gate here reads the tree under the
# targets and nothing else, so the set is empty; it is still declared, because
# the day one of these starts asking a live host is the day its answer stops
# being a property of the files.
NETWORK_CHECKS = set()


def _load_pass_cache():
    """The shared carry-forward store, loaded by path.

    Hooks and skills run this tool as a script from arbitrary directories, so
    nothing arrives through sys.path. The skill's own sibling tools directory
    comes first, so a copy installed inside a repo uses the copy that travelled
    with it.
    """
    for path in (ENGINE.parent.parent / "tools" / "pass_cache.py",
                 HOME / ".claude" / "tools" / "pass_cache.py"):
        if path.is_file():
            spec = importlib.util.spec_from_file_location("intelligence_pass_cache", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


try:
    pass_cache = _load_pass_cache()
except Exception:
    # A copy that travelled without the store sweeps every gate and reads every
    # file, which is the behaviour carrying answers forward improves on rather
    # than replaces. Nothing about a missing store may stop a run.
    pass_cache = None

_CACHE = None
_CACHE_OPENED = False
_CACHE_OFF = False


def cache_disable():
    """--no-cache: sweep every gate, read every file, store nothing."""
    global _CACHE_OFF
    _CACHE_OFF = True


def cache_for(run):
    """The one store a command uses, or None where there is none.

    One object across both halves of a run: two would each hold half the
    answers, and whichever saved second would drop the other's.
    """
    global _CACHE, _CACHE_OPENED
    if _CACHE_OFF or pass_cache is None:
        return None
    if not _CACHE_OPENED:
        _CACHE_OPENED = True
        _CACHE = pass_cache.open_for("seo", run.get("targets") or [], GATES_DIR, TOOL,
                                     network=NETWORK_CHECKS, self_settling=SELF_SETTLING)
    return _CACHE


def cache_asking(run, paths):
    """The store, once it holds what this run asks and which files it asks of.

    A gate answer keys on both. A run over another kind, another declared flag,
    another URL or another narrowing is asking a different question, and the
    answer to one is not the answer to the other.
    """
    cache = cache_for(run)
    if cache is None:
        return None
    try:
        cache.set_scope(paths)
        cache.set_question(kind=run.get("kind"), flags=run.get("flags") or (),
                           url=run.get("url") or "", scope=run.get("scope") or "")
        return cache if cache.ready() else None
    except Exception:
        return None


def cache_save(cache):
    if cache is None:
        return
    try:
        cache.save()
    except Exception:
        return


def sweep_scope(site):
    """Every file the sweep reads, which is what a gate answer answers to.

    The whole tree, because the sitemap gate and the copy pass read it: a scope
    holding only the served files serves a stale answer when a manifest moves.
    """
    paths = set(site["tree"])
    paths.update(str(Path(page["path"]).resolve()) for page in site["pages"])
    paths.update(str(f.resolve()) for f in site["images"])
    return sorted(paths)


def carry_files(run, files):
    """Serve the ruling an earlier run gave a file whose bytes have not moved.

    Rulings are the expensive half of a run: each one is a file somebody read.
    A file this run has already touched is left alone - it is being worked on,
    and what an earlier run made of it says nothing about what it holds now.
    """
    cache = cache_for(run)
    if cache is None:
        return
    try:
        hashes = pass_cache.hash_paths(sorted(files))
        cache.forget_missing(files)
        touched = set(run.get("touched") or [])
        for path, record in files.items():
            if record.get("status") != "open" or path in touched:
                continue
            row = cache.carry_file(path, hashes.get(path))
            if not row or row.get("status") != "clear":
                continue
            record.update({"status": "clear", "read": True, "at": now(), "by": "cache",
                           "note": "cleared in run %s at %s, unchanged since: %s"
                                   % (row.get("run") or "?", row.get("at") or "?",
                                      row.get("note") or "")})
    except Exception:
        return


def record_clears(run, rows):
    """Store the rulings this run gave, keyed on the bytes they were given for."""
    cache = cache_for(run)
    if cache is None:
        return
    rows = list(rows)
    try:
        hashes = pass_cache.hash_paths([path for path, _ in rows])
        for path, note in rows:
            cache.record_file(path, hashes.get(path), "clear", note,
                              run_id=run.get("id", "?"))
        cache.save()
    except Exception:
        return


def carried_files_line(run):
    """What the ledger owed no read for, or nothing.

    Read off the ledger rather than off this call's own work: the carry lands in
    whichever command refreshes the ledger first, which is usually the one that
    opened the run, and the count is worth printing in every command after it.
    """
    carried = [p for p, r in (run.get("files") or {}).items()
               if r.get("by") == "cache" and r.get("status") == "clear"]
    if not carried:
        return ""
    return ("  %d file(s) were ruled on by an earlier run and have not changed since."
            % len(carried))


# ---------------------------------------------------------------- file ledger

LEDGER_EXT = MARKUP_EXT | {".txt", ".xml"} | IMAGE_EXT


def ledger_candidates(run):
    keep, excluded = [], []
    for f in iter_files(run["targets"]):
        if not NON_PAGE.search(f.name) and f.suffix.lower() in LEDGER_EXT:
            keep.append(str(f.resolve()))
        else:
            excluded.append(str(f.resolve()))
    return sorted(keep), sorted(excluded)


def refresh_ledger(run):
    keep, excluded = ledger_candidates(run)
    files = run.setdefault("files", {})
    for path in keep:
        files.setdefault(path, {"status": "open", "note": "", "at": "", "read": False})
    for path in list(files):
        if path not in keep:
            files.pop(path)
    for path in run.get("touched", []):
        if path in files:
            files[path]["read"] = True
    run["excluded"] = excluded
    carry_files(run, files)
    return files


def mark_read(run, paths):
    files = run.setdefault("files", {})
    hit = False
    for path in paths:
        resolved = str(Path(path).expanduser().resolve())
        if resolved in files and not files[resolved]["read"]:
            files[resolved]["read"] = True
            hit = True
    return hit


# Reads that go through the shell count too: a file opened with cat or sed has been
# read as surely as one opened with the Read tool.
READ_VERBS = {"cat", "head", "tail", "less", "more", "bat", "sed", "nl", "awk", "grep"}


def shell_read_paths(command, base_dir):
    tokens = shell_tokens(command)
    paths, verb = [], None
    for token in tokens:
        if Path(token).name in READ_VERBS:
            verb = Path(token).name
            continue
        if token in {"|", "&&", ";"}:
            verb = None
            continue
        if verb and not token.startswith("-"):
            candidate = Path(token).expanduser()
            if not candidate.is_absolute():
                candidate = Path(base_dir) / candidate
            if candidate.is_file():
                paths.append(str(candidate))
    return paths


# ---------------------------------------------------------------- run state


def _run_at(pointer):
    if not pointer.exists():
        return None
    try:
        return json.loads((RUNS / ("%s.json" % json.loads(pointer.read_text())["id"])).read_text())
    except (ValueError, OSError, KeyError):
        return None


def load_run(required=True, session=None, own_only=False, here=None):
    """The run this call should act on.

    The session's own pointer first, then the shared one - but only when the run it
    names has no owner. Adopting a run another session owns is how one session's work
    ends up recorded against another's checklist.

    `here` scopes the answer to one directory: a run governs it when the run's
    target stands at or above it, and a lone run also answers from outside every
    working tree, so a run whose tree has moved stays reachable.
    """
    mine = session or session_id()
    run = _run_at(session_pointer(mine))
    if run is None:
        run = run_here(session_runs(mine), here)
    if run is None:
        run = _run_at(legacy_pointer(mine))
    if run is None and not own_only:
        shared = _run_at(CURRENT)
        if shared is not None and (not shared.get("session") or shared.get("session") == mine):
            run = shared
    # The directory the caller names is authoritative. A run reached by any
    # pointer governs that directory only when it owns it: a run that has read
    # nothing here otherwise gates a landing here on another project's gates.
    if run is not None and here is not None:
        run = run_here([run], here)
    if run is None and required:
        raise SystemExit("No SEO run is open. Start one:\n  %s start --target <path>" % TOOL)
    return run


def save_run(run):
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / ("%s.json" % run["id"])).write_text(json.dumps(run, indent=2))
    pointer = json.dumps({"id": run["id"]})
    claim_shared(run, pointer)
    if run.get("session"):
        pointer_for(run["session"], run_target(run)).write_text(pointer)


def settle_self(run, index):
    others = [g for g in run["order"] if index[g]["check"]["type"] not in SELF_SETTLING]
    outstanding = [g for g in others if run["results"][g]["status"] == "open"]
    for gid in run["order"]:
        kind = index[gid]["check"]["type"]
        if kind not in SELF_SETTLING:
            continue
        result = run["results"][gid]
        if kind == "run_opened":
            ok, note = True, "run %s opened %s" % (run["id"], run["opened"])
        elif kind == "all_resolved":
            ok = not outstanding
            note = ("every other gate answered" if ok
                    else "%d gate(s) still unanswered" % len(outstanding))
        elif kind == "file_coverage":
            files = run.get("files") or {}
            unruled = [p for p, r in files.items() if r["status"] == "open"]
            # A target that no longer exists leaves a ledger that cannot be rebuilt. The
            # work is done and the gate settles on that rather than holding forever.
            gone = (all(not Path(t).exists() for t in run.get("targets", []))
                    if run.get("targets") else False)
            ok = (bool(files) or gone) and not unruled
            note = ("the targets no longer exist; every file was ruled on before they went"
                    if ok and gone else "all %d file(s) read and ruled on" % len(files) if ok
                    else "%d of %d file(s) not ruled on" % (len(unruled), len(files)))
        else:
            ok = bool(run.get("report_at"))
            note = "report rendered %s" % run["report_at"] if ok else "no report rendered yet"
        result["status"] = "pass" if ok else "open"
        result["note"] = note
        result["auto"] = note
        result["at"] = now() if ok else ""


def refresh(run):
    groups = load_gates()
    site = build_site(run)
    ctx = build_context(run, site)
    run["context"] = sorted(k for k, v in ctx.items() if v)
    results = run.setdefault("results", {})
    order = []
    for gate in all_gates(groups):
        ok, _ = applicable(gate, run, ctx)
        if not ok:
            results.pop(gate["id"], None)
            continue
        order.append(gate["id"])
        results.setdefault(gate["id"], {"status": "open", "note": "", "auto": "", "at": ""})
    for gid in list(results):
        if gid not in order:
            results.pop(gid)
    run["order"] = order
    files = refresh_ledger(run)
    for path in run.get("touched", []):
        if path in files and files[path]["status"] == "open":
            files[path].update({"status": "changed", "note": "changed in this run", "at": now()})
    settle_self(run, gate_index(groups))
    return groups, ctx, site


def target_root(targets):
    """The working tree a run belongs to, taken from its target.

    Deriving it from the process cwd instead is how a run opened inside an agent
    thread records the parent session's worktree: the repo-level gates then read
    a different project's manifest, lockfile and build output, and report
    findings about a tree the run never touched.
    """
    first = Path(str(targets[0])).expanduser().resolve() if targets else None
    if not first:
        return None
    root = repo_root(first if first.is_dir() else first.parent)
    return str(root) if root else None


def refuse_foreign_cwd(targets):
    """Stops a run whose target sits outside the tree the caller is standing in."""
    root = target_root(targets)
    if not root:
        return
    try:
        here = repo_root(Path.cwd())
    except Exception:
        return
    if here and Path(here).resolve() != Path(root).resolve():
        sys.stderr.write(
            "REFUSED: the target is in %s but this command is running in %s.\n"
            "cd into the target's worktree first. A run that opens from another\n"
            "tree records that tree as its repo, and every repo-level gate then\n"
            "answers for the wrong project.\n" % (root, here))
        sys.exit(2)


def open_run(targets, title, kind="site", session=None):
    refuse_foreign_cwd(targets)
    run = {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session or session_id(),
        "kind": kind,
        "targets": [str(t) for t in targets],
        "repo": target_root(targets) or repo_root(),
        "title": title,
        "flags": [],
        "results": {},
        "touched": [],
    }
    refresh(run)
    save_run(run)
    return run


# ---------------------------------------------------------------- commands


def parse_flags(argv):
    out, key = {}, None
    for token in argv:
        if token.startswith("--"):
            key = token[2:]
            out.setdefault(key, [])
        elif key:
            out[key].append(token)
    return out


def print_checklist(run, groups, header=""):
    index = gate_index(groups)
    if header:
        print(header)
    line = scope_line(run)
    if line:
        print(line)
    print("Targets: %s" % ", ".join(run["targets"]))
    print("Found: %s" % ", ".join(run.get("context", [])[:16]))
    print("%d gate(s) apply.\n" % len(run["order"]))
    current = None
    for gid in run["order"]:
        gate = index[gid]
        if gate["group"] != current:
            current = gate["group"]
            print("== %s" % current)
        auto = "auto" if gate["check"]["type"] != "manual" else "by hand"
        print("   %-8s [%s|%s] %s" % (gid, gate["severity"], auto, gate["title"]))
    print("\nNext:\n  %s verify" % TOOL)


def cmd_start(argv):
    flags = parse_flags(argv)
    kind = (flags.get("kind") or ["site"])[0]
    if kind not in KINDS:
        raise SystemExit("kind must be one of: %s" % ", ".join(KINDS))
    here = os.getcwd()
    targets = flags.get("target") or [str(project_root(here) or here)]
    refuse_foreign_cwd(targets)
    run = {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session_id(),
        "kind": kind,
        "targets": [str(Path(t).expanduser().resolve()) for t in targets],
        "repo": target_root(targets) or repo_root(),
        "title": " ".join(flags.get("title", [])) or "SEO run",
        "flags": flags.get("flag", []),
        "scope": (flags.get("scope") or [None])[0],
        "results": {},
        "touched": [],
    }
    groups, _, _ = refresh(run)
    save_run(run)
    print_checklist(run, groups, header="SEO run %s opened - %s" % (run["id"], run["title"]))
    return 0


def cmd_scan(argv):
    run = load_run()
    flags = parse_flags(argv)
    if flags.get("kind"):
        run["kind"] = flags["kind"][0]
    if flags.get("target"):
        run["targets"] = [str(Path(t).expanduser().resolve()) for t in flags["target"]]
    if flags.get("flag"):
        run["flags"] = sorted(set(run.get("flags", []) + flags["flag"]))
    if flags.get("scope"):
        run["scope"] = flags["scope"][0] if flags["scope"][0] != "all" else None
    groups, _, _ = refresh(run)
    save_run(run)
    print_checklist(run, groups, header="Run %s rescoped" % run["id"])
    return 0


def cmd_verify(argv):
    run = load_run()
    groups, ctx, site = refresh(run)
    index = gate_index(groups)
    cache = cache_asking(run, sweep_scope(site))
    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0, "manual": 0}
    carried = 0
    lines = []
    for gid in run["order"]:
        gate = index[gid]
        fn = CHECKS.get(gate["check"]["type"], "missing")
        if fn is None:
            counts["manual"] += 1
            continue
        if fn == "missing":
            run["results"][gid]["auto"] = "no checker implemented for %s" % gate["check"]["type"]
            counts[UNKNOWN] += 1
            lines.append("  ?  %-8s %s - checker not implemented, answer by hand"
                         % (gid, gate["title"]))
            continue
        result = run["results"][gid]
        # A gate marked fixed is swept again whatever is stored: finding out
        # whether the change took is the entire point of the mark.
        if cache is not None and result.get("status") != "fixed":
            hit = cache.carry_gate(gate)
            if hit and hit.get("status") == PASS:
                counts[PASS] += 1
                carried += 1
                result.update({"status": PASS, "at": now(),
                               "auto": "pass: cached (gate and sources unchanged)",
                               "note": "cached pass from run %s at %s"
                                       % (hit.get("run") or "?", hit.get("at") or "?")})
                lines.append("  ok %-8s %s   [cached]" % (gid, gate["title"]))
                continue
        try:
            status, detail = fn(gate, site, run, ctx)
        except Exception as exc:  # a broken checker must never read as a pass
            status, detail = UNKNOWN, "checker error: %s" % exc
        result["auto"] = "%s: %s" % (status, detail)
        counts[status] += 1
        if cache is not None:
            # Only a pass is stored, and anything else drops what was stored
            # before it, so a gate that has started failing cannot be served
            # from a record of the day it passed.
            cache.record_gate(gate, status, detail, run_id=run.get("id", "?"))
        if result.get("status") == "fixed" and status == FAIL:
            result["status"] = "open"
            lines.append("  X  %-8s %s\n       marked fixed, but the sweep still fails: %s"
                         % (gid, gate["title"], detail))
            continue
        if result.get("by") == "hand" and result.get("status") != "fixed" and status != PASS:
            lines.append("  ok %-8s %s\n       answered by hand: %s\n       sweep still says: %s"
                         % (gid, gate["title"], result["note"], detail))
            continue
        if status == PASS:
            result.update({"status": "pass", "note": detail, "at": now()})
            lines.append("  ok %-8s %s" % (gid, gate["title"]))
        else:
            result["status"] = "open"
            lines.append("  %s  %-8s %s\n       %s"
                         % ("X" if status == FAIL else "?", gid, gate["title"], detail))
    save_run(run)
    cache_save(cache)
    print("Automated sweep over %d applicable gate(s):\n" % len(run["order"]))
    print("\n".join(lines) or "  (nothing automatable in scope)")
    print("\n  %d passed, %d failed, %d inconclusive, %d by hand."
          % (counts[PASS], counts[FAIL], counts[UNKNOWN], counts["manual"]))
    if carried:
        print("  %d of the passes were cached from a prior run (gate and sources unchanged)."
              % carried)
    files_line = carried_files_line(run)
    if files_line:
        print(files_line)
    print("  Failed and inconclusive gates stay open: change the project, then verify again.")
    return 0


TARGET_NOUN = "project"


EVIDENCE = re.compile(r"[\w-]+\.[a-zA-Z]{2,4}\b|:\d+|\b\d+(\.\d+)?(px|rem|em|%|ms|s|ch)?\b|[\"\u201c\u2018']")


def _condition_still_holds(gate, ctx):
    """Whether the tool can still see the condition that pulled this gate in."""
    when = gate.get("when", "always")
    for condition in (when if isinstance(when, list) else [when]):
        if condition != "always" and not ctx.get(condition, False):
            return False
    return True


def _validate_answer(gid, status, note, run, index, ctx=None):
    """Every reason one answer cannot stand, so a batch reports all of them at once."""
    faults = []
    if status not in ("pass", "fixed", "na", "disputed"):
        faults.append("status must be pass, fixed, na, or disputed, not %r" % status)
    # A note is a record, not a check. What decides a gate is the sweep, so an answer
    # stands or falls on its status and the run's own state.
    if gid not in run["results"]:
        faults.append("not applicable to this run")
        return faults
    gate = index[gid]
    if gate["check"]["type"] in SELF_SETTLING:
        faults.append("answers to the run's own state, not to an attestation")
    swept = str(run["results"][gid].get("auto", ""))
    failing = swept.startswith("fail")
    if status == "pass" and failing:
        faults.append("the sweep found this failing, so it cannot be answered as passing: %s"
                      % swept[:120])
    # A failing gate closed as not-applicable is the escape hatch that makes the
    # whole checklist advisory: the sweep just proved the condition is present and
    # unmet. Fixing it, or disputing the measurement on the record, are the ways out.
    if status == "na" and failing:
        faults.append("the sweep found this failing, so it is present and unmet, not inapplicable. "
                      "Change the %s, or answer disputed with what you measured if the check "
                      "itself is wrong: %s" % (TARGET_NOUN, swept[:100]))
    if status == "na" and ctx is not None and _condition_still_holds(gate, ctx):
        when = gate.get("when", "always")
        named = "+".join(when) if isinstance(when, list) else when
        if named != "always":
            faults.append("the gate entered this run because the %s contains %s, and it still does, "
                          "so not-applicable contradicts what the tool can see" % (TARGET_NOUN, named))
    if status == "disputed" and len(note.strip()) < 40:
        faults.append("disputing a check takes the measurement: what it reported, what is actually "
                      "there, and how that was established")
    if status in ("disputed", "na", "fixed") and note.strip() and not EVIDENCE.search(note):
        faults.append("the note carries no evidence - name what was found: a path, a line, "
                      "a measurement or a quoted string")
    if status == "disputed" and not failing:
        faults.append("nothing to dispute - the sweep did not fail this gate")
    return faults

def cmd_resolve_batch(source):
    """Answer many gates in one call, each with its own status and its own note.

    A run's wall-clock cost is round trips. The sweep settles what a script can
    settle in seconds; the rest is one exchange per gate unless the answers can
    travel together. Nothing is relaxed to do it: every answer meets the same
    conditions one at a time, and a batch carrying a single bad answer is refused
    whole rather than partly applied, so nothing lands unnoticed beside a failure.
    """
    run = load_run()
    groups, ctx, _ = refresh(run)
    index = gate_index(groups)
    raw = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    try:
        rows = json.loads(raw)
    except ValueError as exc:
        raise SystemExit("--batch expects JSON: a list of {id, status, note}. %s" % exc)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise SystemExit("--batch expects a non-empty list of {id, status, note}")

    problems, answers, seen = [], [], set()
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            problems.append("row %d is not an object" % n)
            continue
        gid = str(row.get("id", "")).strip()
        status = str(row.get("status", "")).strip()
        note = str(row.get("note", ""))
        if not gid:
            problems.append("row %d has no id" % n)
            continue
        if gid in seen:
            problems.append("%s appears twice" % gid)
            continue
        seen.add(gid)
        faults = _validate_answer(gid, status, note, run, index, ctx)
        if faults:
            problems.extend("%s: %s" % (gid, f) for f in faults)
            continue
        answers.append((gid, status, note))

    for gid, status, note in answers:
        run["results"][gid].update({"status": status, "note": note, "at": now(), "by": "hand"})
    if answers:
        save_run(run)
    if problems:
        kept = ("%d answer(s) were recorded; " % len(answers)) if answers else ""
        print("%s%d cannot stand:\n\n%s\n\n"
              "Send those again. A gate the sweep failed is work to do, not a finding\n"
              "to record." % (kept, len(problems), "\n".join("  " + p for p in problems)),
              file=sys.stderr)
        if not answers:
            return 1

    counts = {}
    for _, status, _ in answers:
        counts[status] = counts.get(status, 0) + 1
    print("Answered %d gate(s): %s" % (
        len(answers), ", ".join("%d %s" % (v, k) for k, v in sorted(counts.items()))))
    remaining = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    print("%d gate(s) still open." % len(remaining))
    return 0


def cmd_resolve(argv):
    if "--batch" in argv:
        at = argv.index("--batch")
        if at + 1 >= len(argv):
            raise SystemExit("--batch takes a path, or - to read the JSON from stdin")
        return cmd_resolve_batch(argv[at + 1])
    run = load_run()
    groups, ctx, _ = refresh(run)
    index = gate_index(groups)
    ids, status, note, i = [], "pass", "", 0
    while i < len(argv):
        if argv[i] == "--status":
            status = argv[i + 1]; i += 2
        elif argv[i] == "--note":
            note = argv[i + 1]; i += 2
        else:
            # A batch built from `status` arrives as one argument under zsh, which does
            # not word-split an unquoted expansion. Splitting here turns a list that
            # looks right into a list that works.
            ids.extend(t for t in re.split(r"[\s,]+", argv[i]) if t)
            i += 1
    if status not in ("pass", "fixed", "na", "disputed"):
        raise SystemExit("--status must be pass, fixed, na, or disputed")
    unknown = [g for g in ids if g not in run["results"]]
    if unknown:
        raise SystemExit("not applicable to this run: %s" % ", ".join(unknown))
    problems = []
    for gid in ids:
        problems.extend("%s: %s" % (gid, f)
                        for f in _validate_answer(gid, status, note, run, index, ctx))
    if problems:
        raise SystemExit(
            "Refused, and nothing was recorded:\n\n%s\n\n"
            "A gate that is not met is work to do, not a finding to record."
            % "\n".join("  " + p for p in problems))
    for gid in ids:
        run["results"][gid].update({"status": status, "note": note, "at": now(), "by": "hand"})
    save_run(run)
    print("Answered %d gate(s) as %s: %s" % (len(ids), status, ", ".join(ids)))
    print("%d gate(s) still open."
          % len([g for g in run["order"] if run["results"][g]["status"] == "open"]))
    return 0


def cmd_status(argv):
    flags = parse_flags(argv)
    run = load_run()
    groups, _, _ = refresh(run)
    index = gate_index(groups)
    save_run(run)
    # The bookkeeping gates are listed apart from the answerable ones. Mixed in, a batch
    # built from this output would include them and be refused wholesale.
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"
                and index[g]["check"]["type"] not in SELF_SETTLING]
    pending = [g for g in run["order"] if run["results"][g]["status"] == "open"
               and index[g]["check"]["type"] in SELF_SETTLING]
    line = scope_line(run)
    if line:
        print(line)
    print("Run %s - %s (%s)" % (run["id"], run["title"], run["kind"]))
    line = scope_line(run)
    if line:
        print(line)
    print("Targets: %s" % ", ".join(run["targets"]))
    print("%d applicable gate(s), %d to answer.\n" % (len(run["order"]), len(open_ids)))
    full = "full" in flags
    for gid in open_ids:
        gate = index[gid]
        print("  %-8s %s\n           %s"
              % (gid, gate["title"], run["results"][gid].get("auto", "not swept yet")[:200]))
        if full:
            print("           rule: %s" % gate["rule"])
            print("           fix:  %s" % gate.get("fix", ""))
    if pending:
        print("\nSettled by the run itself once the rest are answered: %s" % ", ".join(pending))
    if open_ids:
        # The cost of a run is round trips rather than checks: the sweep settles
        # what it can in seconds, and the rest is answered in one call carrying a
        # status and a note per gate.
        print("\nAnswer them in one call - each gate keeps its own status and its own note:\n"
              "  %s resolve --batch - <<'JSON'\n"
              "  [{\"id\": \"%s\", \"status\": \"fixed\", \"note\": \"what changed\"}]\n"
              "  JSON" % (TOOL, open_ids[0]))
    return 0


def cmd_pages(argv):
    run = load_run()
    _, _, site = refresh(run)
    save_run(run)
    print("%d route(s) under %s\n" % (len(site["pages"]), ", ".join(run["targets"])))
    for page in sorted(site["pages"], key=rel_of):
        title, desc = page_title(page), page_description(page)
        print("  %-48s %s" % (page["rel"][:48], page["kind"]))
        print("      title       %s" % (("%r (%d)" % (title, len(title))) if title else "-"))
        print("      description %s" % (("%d chars" % len(desc)) if desc else "-"))
        print("      words       %d" % word_count(page))
    return 0


def cmd_files(argv):
    run = load_run()
    flags = parse_flags(argv)
    files = refresh_ledger(run)
    save_run(run)
    order = sorted(files)
    open_files = [p for p in order if files[p]["status"] == "open"]
    for path in (order if "all" in flags else open_files):
        record = files[path]
        print("  %-8s %-5s %s" % (record["status"], "read" if record["read"] else "-", path))
    print("\n%d file(s) in the ledger, %d still to rule on, %d excluded by type."
          % (len(files), len(open_files), len(run.get("excluded", []))))
    files_line = carried_files_line(run)
    if files_line:
        print(files_line.strip())
    if open_files:
        print("Read each one, then either change it or clear it:")
        print("  %s file-clear <path>... --note \"<what you read and why it needs nothing>\"" % TOOL)
    return 0


def cmd_file_clear_batch(source, run, files):
    """Clear many files in one call, each with its own reason.

    The run travels in alongside its own ledger. Loading a second copy here would
    leave the clears on one object and save the other, so every batch would report
    success and record nothing.
    """
    raw = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    try:
        rows = json.loads(raw)
    except ValueError as exc:
        raise SystemExit("--batch expects JSON: a list of {path, note}. %s" % exc)
    if isinstance(rows, dict):
        rows = [rows]
    problems, cleared = [], []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            problems.append("row %d is not an object" % n)
            continue
        raw_path = str(row.get("path", "")).strip()
        note = str(row.get("note", ""))
        if not raw_path:
            problems.append("row %d has no path" % n)
            continue
        path = str(Path(raw_path).expanduser().resolve())
        if path not in files:
            problems.append("%s: not in this run's ledger" % Path(raw_path).name)
            continue
        if not files[path]["read"]:
            problems.append("%s: not read yet, so there is nothing to clear it on" % Path(path).name)
            continue
        if len(note.strip()) < 12:
            problems.append("%s: note must say what was read and why it needs no change" % Path(path).name)
            continue
        cleared.append((path, note))
    if problems:
        raise SystemExit(
            "Refused, and nothing was recorded - %d file(s) cannot be cleared:\n\n%s\n\n"
            "Open each file first, then send the batch again."
            % (len(problems), "\n".join("  " + p for p in problems)))
    for path, note in cleared:
        files[path].update({"status": "clear", "note": note, "at": now()})
    save_run(run)
    record_clears(run, cleared)
    print("Cleared %d file(s)." % len(cleared))
    left = [p for p, v in files.items() if v["status"] == "open"]
    print("%d file(s) still to rule on." % len(left))
    return 0


def cmd_file_clear(argv):
    if "--batch" in argv:
        at = argv.index("--batch")
        if at + 1 >= len(argv):
            raise SystemExit("--batch takes a path, or - to read the JSON from stdin")
        run = load_run()
        return cmd_file_clear_batch(argv[at + 1], run, refresh_ledger(run))
    run = load_run()
    paths, note, i = [], "", 0
    while i < len(argv):
        if argv[i] == "--note":
            note = argv[i + 1]; i += 2
        else:
            paths.append(argv[i]); i += 1
    if len(note.strip()) < 12:
        raise SystemExit("--note must say what you read and why it needs no change")
    files = refresh_ledger(run)
    resolved = [str(Path(p).expanduser().resolve()) for p in paths]
    unknown = [p for p in resolved if p not in files]
    if unknown:
        raise SystemExit("not in this run's ledger: %s" % ", ".join(unknown))
    unread = [p for p in resolved if not files[p]["read"]]
    if unread:
        raise SystemExit("not read yet, so there is nothing to clear them on: %s\n"
                         "Open each file first, then clear it." % ", ".join(unread))
    for path in resolved:
        files[path].update({"status": "clear", "note": note, "at": now()})
    save_run(run)
    record_clears(run, [(path, note) for path in resolved])
    print("Cleared %d file(s). %d still to rule on."
          % (len(resolved), len([p for p, r in files.items() if r["status"] == "open"])))
    return 0


def cmd_report(argv):
    run = load_run()
    groups, _, _ = refresh(run)
    index = gate_index(groups)
    flags = parse_flags(argv)
    # The report gate cannot wait for itself: stamp it once everything describing the
    # deliverable is answered, then let the bookkeeping gates catch up before the table
    # is built, so the rendered list shows their real state.
    outstanding = [g for g in run["order"] if run["results"][g]["status"] == "open"
                   and index[g]["check"]["type"] not in SELF_SETTLING]
    if not outstanding:
        run["report_at"] = run.get("report_at") or now()
        settle_self(run, index)
        save_run(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    body = ["# SEO checklist - %s" % run["title"], "",
            "Run `%s`, kind `%s`, opened %s." % (run["id"], run["kind"], run["opened"]), "",
            "Targets: %s" % ", ".join("`%s`" % t for t in run["targets"]), ""]
    fixed = [g for g in run["order"] if run["results"][g]["status"] == "fixed"]
    if fixed:
        body += ["## Changed", ""]
        body += ["- **%s %s** - %s" % (g, index[g]["title"], run["results"][g]["note"])
                 for g in fixed]
        body.append("")
    else:
        body += ["Nothing was changed in this run.", ""]
    tally = {}
    for gid in run["order"]:
        tally[run["results"][gid]["status"]] = tally.get(run["results"][gid]["status"], 0) + 1
    body += ["%d gates applied: %s." % (len(run["order"]), ", ".join(
        "%d %s" % (n, {"pass": "passed", "fixed": "fixed", "na": "not applicable",
                       "disputed": "DISPUTED", "open": "OPEN"}.get(s, s))
        for s, n in sorted(tally.items()))), ""]
    # A disputed gate is a rule the run says the tool measured wrongly. It closes
    # so one bad check cannot jam a build, and it leads the report so the claim is
    # read rather than absorbed: every one is either a defect in the check or a
    # rule that quietly went unenforced.
    disputed = [g for g in run["order"] if run["results"][g]["status"] == "disputed"]
    if disputed:
        body += ["### Disputed - the check was answered as wrong (%d)" % len(disputed), "",
                 "Each of these is a rule that did not get enforced this run. Read them first.", ""]
        for gid in disputed:
            body += ["- **%s %s**" % (gid, index[gid]["title"]),
                     "  - sweep said: %s" % run["results"][gid].get("auto", "")[:200],
                     "  - the run's measurement: %s" % run["results"][gid]["note"]]
        body += [""]
    # Anything answered n/a is listed up front rather than buried in its group. A gate
    # only appears because its condition was found, so every n/a is a claim the tool read
    # the project wrong, and that claim should be the easiest thing here to check.
    skipped = [g for g in run["order"] if run["results"][g]["status"] == "na"]
    if skipped:
        body += ["### Answered not applicable (%d)" % len(skipped), ""]
        body += ["- **%s %s** - %s" % (g, index[g]["title"],
                                       run["results"][g]["note"] or "no reason given")
                 for g in skipped]
        body.append("")
    files = run.get("files") or {}
    if files:
        changed = [p for p, r in files.items() if r["status"] == "changed"]
        cleared = [p for p, r in files.items() if r["status"] == "clear"]
        body += ["### Files (%d in scope, %d excluded by type)"
                 % (len(files), len(run.get("excluded", []))), "",
                 "| File | Verdict | On what |", "| --- | --- | --- |"]
        for path in sorted(files):
            record = files[path]
            body.append("| `%s` | %s | %s |" % (
                path, {"changed": "changed", "clear": "no change needed",
                       "open": "OPEN"}[record["status"]],
                (record["note"] or "").replace("|", "/")[:220]))
        body += ["", "%d changed, %d read and left alone." % (len(changed), len(cleared)), ""]
    current = None
    for gid in run["order"]:
        gate, result = index[gid], run["results"][gid]
        if gate["group"] != current:
            current = gate["group"]
            body += ["", "## %s" % current, "",
                     "| Gate | Check | Status | How it was answered |",
                     "| --- | --- | --- | --- |"]
        mark = {"pass": "pass", "fixed": "fixed", "na": "n/a", "disputed": "DISPUTED",
                "open": "OPEN"}.get(result["status"], result["status"])
        note = (result["note"] or result["auto"] or "").replace("|", "/").replace("\n", " ")
        body.append("| %s | %s | %s | %s |" % (gid, gate["title"], mark, note[:300]))
    text = "\n".join(body) + "\n"
    out = Path((flags.get("out") or [str(RUNS / ("%s-report.md" % run["id"]))])[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    print("\nWritten to %s" % out)
    if open_ids:
        print("\n%d gate(s) are still OPEN - the run cannot close until they are answered."
              % len(open_ids))
        return 1
    return 0


def cmd_finish(argv):
    run = load_run()
    flags = parse_flags(argv)
    if "no-deliverable" in flags:
        # A file whose content is back to what git has shipped nothing: the run opens on
        # the path being edited, not on the edit changing anything a crawler sees.
        touched = [t for t in (run.get("touched") or [])
                   if Path(t).exists() and differs_from_git(Path(t))]
        if touched:
            print("Cannot close as no-deliverable: this run edited %d crawlable file(s) - %s.\n"
                  "Work the checklist and report on it instead."
                  % (len(touched), ", ".join(base(t) for t in touched[:8])), file=sys.stderr)
            return 1
        run["closed"] = now()
        run["closed_reason"] = " ".join(flags.get("note", [])) or "no SEO deliverable was produced"
        save_run(run)
        clear_pointers(run)
        print("Run %s closed with no deliverable: %s" % (run["id"], run["closed_reason"]))
        return 0
    refresh(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    if open_ids:
        save_run(run)
        print("Cannot close: %d gate(s) unanswered - %s"
              % (len(open_ids), ", ".join(open_ids[:12])), file=sys.stderr)
        return 1
    run["closed"] = now()
    save_run(run)
    clear_pointers(run)
    print("Run %s closed. %d gates answered." % (run["id"], len(run["order"])))
    return 0


def cmd_gates(argv):
    flags = parse_flags(argv)
    groups = load_gates()
    kind = (flags.get("kind") or [None])[0]
    total = 0
    for group in groups:
        gates = [g for g in group["gates"] if not kind or "*" in g["kinds"] or kind in g["kinds"]]
        if not gates:
            continue
        print("\n== %s (%d)" % (group["group"], len(gates)))
        print("   %s" % group["description"])
        for gate in gates:
            total += 1
            auto = gate["check"]["type"]
            print("   %-8s [%s|%s|%s] %s" % (gate["id"], gate["severity"], gate["when"],
                                             "auto" if auto != "manual" else "by hand",
                                             gate["title"]))
            if "full" in flags:
                print("            %s" % gate["rule"])
    print("\n%d gate(s)." % total)
    return 0


# ---------------------------------------------------------------- hooks


CRAWLABLE = MARKUP_EXT | {".txt", ".xml"}
SEO_MARKERS = re.compile(
    r"<head\b|<meta\b|<title\b|application/ld\+json|export\s+const\s+metadata|"
    r"generateMetadata|<svelte:head|useHead\(|<urlset|hreflang", re.I)


def is_crawlable_surface(path):
    p = Path(path)
    if p.name in {"robots.txt", "sitemap.xml", "llms.txt"}:
        return True
    if p.suffix.lower() not in CRAWLABLE or NON_PAGE.search(p.name):
        return False
    try:
        return bool(SEO_MARKERS.search(p.read_text(errors="replace")[:200000]))
    except OSError:
        return False


def cmd_hook_read():
    """PostToolUse(Read|Bash): credit a file in the ledger once it has been read."""
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    run = load_run(required=False)
    if not run or run.get("closed"):
        return 0
    tool_input = payload.get("tool_input") or {}
    paths = [tool_input[key] for key in ("file_path", "notebook_path") if tool_input.get(key)]
    if tool_input.get("command"):
        paths.extend(shell_read_paths(tool_input["command"], payload.get("cwd") or os.getcwd()))
    if not paths:
        return 0
    refresh_ledger(run)
    if mark_read(run, paths):
        save_run(run)
    return 0


def cmd_hook_edit():
    """PostToolUse(Write|Edit): SEO work opens its own run and records what it touched.

    Waiting for the skill to be invoked leaves the obvious hole - hand-edit the head
    metadata and no run ever exists, so nothing is checked and nothing says so. The run
    opens on the edit instead, and the Stop hook makes it finish.
    """
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path or not is_crawlable_surface(path):
        return 0
    here = session_id(payload)
    run = load_run(required=False, session=here)
    if run is None or run.get("closed"):
        run = open_run([Path(path)], "SEO work on %s" % Path(path).name, session=here)
        print("Editing %s opened SEO run %s over that file.\n"
              "Work the checklist - the session cannot end with it open:\n"
              "  %s verify\n"
              "\n"
              "Widen it only if the change is wider than the file:\n"
              "  %s scan --target <path> --scope all"
              % (Path(path).name, run["id"], TOOL, TOOL), file=sys.stderr)
    if here and not run.get("session"):
        run["session"] = here
    touched = run.setdefault("touched", [])
    resolved = str(Path(path).resolve())
    if resolved not in touched:
        touched.append(resolved)
        save_run(run)
    return 0


def invokes(called, skill):
    """Whether a Skill call names this skill, with or without a plugin prefix."""
    return called == skill or called.endswith(":" + skill)


def cmd_hook_skill():
    """PostToolUse(Skill): an invocation opens its own run, so none is skipped."""
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    called = str((payload.get("tool_input") or {}).get("skill") or "")
    if not invokes(called, "seo-checklist"):
        return 0
    mine = session_id(payload)
    if session_pointer(mine).exists():
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    run = open_run([project_root(cwd) or cwd], "SEO skill invocation", session=mine)
    print("An SEO run (%s) was opened for this invocation. Scope it to the project before "
          "starting:\n  %s scan --kind <%s> --target <path>\n"
          "Then verify, change what fails, answer every gate, and report. The session cannot "
          "end with it open." % (run["id"], TOOL, "|".join(KINDS)), file=sys.stderr)
    return 0


def cmd_guard_stop():
    """Stop hook: refuse to end a session that left its own SEO run open.

    Its own, and no other's. A run is per-session state: blocking every session on
    whichever run was opened last stops work in projects that run has never seen.
    """
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    mine = session_id(payload)
    if not mine:
        return 0
    run = load_run(required=False, session=mine, own_only=True,
                   here=payload.get("cwd") or os.getcwd())
    if not run or run.get("closed"):
        return 0
    refresh(run)
    save_run(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    if not open_ids and run.get("report_at"):
        return 0
    unruled = [p for p, r in (run.get("files") or {}).items() if r["status"] == "open"]
    detail = ("%d gate(s) unanswered: %s." % (len(open_ids), ", ".join(open_ids[:15]))
              if open_ids else "the filled checklist has not been rendered.")
    if unruled:
        detail += ("\n\n%d file(s) in scope have not been read and ruled on:\n%s"
                   % (len(unruled), "\n".join("  " + p for p in sorted(unruled)[:10])))
    print("SEO run %s over %s is still open - %s\n"
          "\n"
          "This is a build checklist. Change the project until the sweep passes, then:\n"
          "  %s verify\n"
          "  %s resolve <ID> --status fixed --note \"<what changed>\"\n"
          "  %s report\n"
          "  %s finish\n"
          "\n"
          "A run that produced no crawlable change closes with: %s finish --no-deliverable"
          % (run["id"], ", ".join(run["targets"]), detail, TOOL, TOOL, TOOL, TOOL, TOOL),
          file=sys.stderr)
    return 2


LANDING = re.compile(r"\bgit\s+commit\b|\bgh\s+pr\s+create\b")


CD_RAW = re.compile(r"(?:\A|[;&|]|\s)cd\s+(\"[^\"]*\"|'[^']*'|[^\s;&|]+)")
GIT_C_RAW = re.compile(r"\bgit\b[^;&|]*?\s-C\s+(\"[^\"]*\"|'[^']*'|[^\s;&|]+)")


def landing_dir_from_text(command, base):
    """The tree a landing acts on, read off the raw command.

    Strict tokenizing raises on an apostrophe inside a commit message, and
    answering that with the process directory hands back a path in no working
    tree at all - which reads as covering every tree, so one project's open run
    refuses a landing in every other project on the machine.

    Nothing found returns nothing, which is not-covered rather than covered.
    """
    where = Path(base)
    found = None
    for pattern in (CD_RAW, GIT_C_RAW):
        for match in pattern.finditer(command):
            raw = match.group(1).strip("\"'")
            if not raw:
                continue
            target = Path(raw).expanduser()
            found = target if target.is_absolute() else (where / target)
    return found


def run_covers(run, root):
    """Whether this run has anything to say about the tree being landed."""
    # A tree that could not be resolved is not this run's tree. Reading it as
    # every tree is how an open run over one project refuses a landing in another.
    if not root:
        return False
    if run.get("repo") and Path(run["repo"]).resolve() == Path(root).resolve():
        return True
    here = Path(root).resolve()
    return any(here == Path(t).resolve() or here in Path(t).resolve().parents
               or Path(t).resolve() in here.parents for t in run.get("targets", []))


def landing_dir(payload, command):
    """The tree a landing command acts on.

    A hook is handed the session's directory, which in an agent thread is the home
    directory rather than the checkout, so a command that changes into the repository
    before landing arrives with a cwd that sits in no working tree at all. `repo_root`
    answers None for it, `run_covers` reads that as covering everything, and one
    project's open run then refuses a landing in every other project on the machine.
    The directory in effect where the landing verb appears is what decides which tree
    is being landed, which is how the design checklist has always read it.
    """
    where = Path(payload.get("cwd") or os.getcwd())
    try:
        tokens = shell_tokens(command)
    except ValueError:
        return landing_dir_from_text(command, where)
    for i, token in enumerate(tokens):
        head = Path(token).name
        if head == "cd" and i + 1 < len(tokens):
            target = Path(tokens[i + 1]).expanduser()
            where = target if target.is_absolute() else (where / target)
        elif head == "git" and "-C" in tokens[i + 1:i + 3]:
            at = tokens.index("-C", i) + 1
            if at < len(tokens):
                where = Path(tokens[at]).expanduser()
    return where


def cmd_guard_land():
    """PreToolUse(Bash): refuse to land work while the calling session's SEO run is open."""
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not LANDING.search(command):
        return 0
    where = landing_dir(payload, command)
    run = load_run(required=False, session=session_id(payload), own_only=True,
                   here=where or payload.get("cwd") or os.getcwd())
    if not run or run.get("closed"):
        return 0
    if not run_covers(run, repo_root(where)):
        return 0
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    if not open_ids and run.get("report_at"):
        return 0
    print("BLOCKED: SEO run %s is still open over %s.\n"
          "\n"
          "%d gate(s) unanswered: %s\n"
          "\n"
          "Landing now ships the markup the checklist has not been through. Work it, then land:\n"
          "  %s verify\n"
          "  %s report\n"
          "  %s finish"
          % (run["id"], ", ".join(run["targets"]), len(open_ids), ", ".join(open_ids[:12]),
             TOOL, TOOL, TOOL), file=sys.stderr)
    return 2


def cmd_scopes(argv):
    """The named scopes a run can be narrowed to."""
    for name in sorted(SCOPES):
        print("  %-10s %s" % (name, SCOPES[name][2]))
    print("\n  Narrow a run with --scope <name> on start or scan, and widen it again\n"
          "  with --scope all. Every report leads with what a scoped run left out.")
    return 0


# ------------------------------------------------------------------ repair

# The engine is loaded by path as often as by name, so its own directory is
# not on the path by the time this runs.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import seo_fixes  # noqa: E402

# Each entry names a fault with one correct repair derivable from the project. A
# check absent here has no mechanical answer - a description or an alt attribute
# needs prose - and its gate stays failing until somebody writes it.
FIXERS = {
    "canonical": lambda g, s, r, c: seo_fixes.fix_canonical(g, s, r, c, indexable, has_field),
    "twitter_card": seo_fixes.fix_twitter_card,
    "script_defer": lambda g, s, r, c: seo_fixes.fix_script_defer(g, s, r, c, MARKUP_EXT),
    "img_dimensions": lambda g, s, r, c: seo_fixes.fix_image_dimensions(g, s, r, c, s["root"]),
}


def cmd_fix(argv):
    """Apply every repair the failing gates have, then re-sweep to prove it landed."""
    run = load_run()
    groups, ctx, site = refresh(run)
    index = gate_index(groups)
    applied, skipped = [], []
    for gid in run["order"]:
        gate = index[gid]
        fixer = FIXERS.get(gate["check"]["type"])
        if not fixer:
            continue
        fn = CHECKS.get(gate["check"]["type"])
        if fn is None:
            continue
        status, _ = fn(gate, site, run, ctx)
        if status != FAIL:
            continue
        changed = fixer(gate, site, run, ctx) or []
        if changed:
            applied.append((gid, gate["title"], changed))
        else:
            skipped.append(gid)
    if not applied:
        if skipped:
            print("No repair landed. %d failing gate(s) have a fixer that declined, which\n"
                  "means the fault is real but its repair is not derivable on this stack:\n  %s"
                  % (len(skipped), ", ".join(skipped)))
        else:
            print("Nothing to repair: no failing gate has a mechanical fix.")
        return 0
    for gid, title, changed in applied:
        print("  fixed %-8s %-42s %s" % (gid, title[:42], ", ".join(changed[:6])))
    if skipped:
        print("  declined %s - the fault is real, the repair is not derivable here"
              % ", ".join(skipped))
    print("\n%d gate(s) repaired. Re-sweeping." % len(applied))
    groups, ctx, site = refresh(run)
    save_run(run)
    return cmd_verify([])


COMMANDS = {
    "scopes": cmd_scopes,
    "start": cmd_start, "scan": cmd_scan, "verify": cmd_verify, "resolve": cmd_resolve,
    "status": cmd_status, "report": cmd_report, "finish": cmd_finish, "gates": cmd_gates,
    "pages": cmd_pages, "files": cmd_files, "file-clear": cmd_file_clear,
    "fix": cmd_fix,
}
HOOKS = {
    "hook-read": cmd_hook_read, "hook-edit": cmd_hook_edit, "hook-skill": cmd_hook_skill,
    "guard-stop": cmd_guard_stop, "guard-land": cmd_guard_land,
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    verb, argv = sys.argv[1], sys.argv[2:]
    if "--no-cache" in argv:
        # Lifted out of argv before dispatch: a command reading positional
        # arguments would otherwise take it for one.
        argv = [item for item in argv if item != "--no-cache"]
        cache_disable()
    if verb in HOOKS:
        return HOOKS[verb]()
    if verb not in COMMANDS:
        print("unknown command %r. One of: %s" % (verb, ", ".join(sorted(COMMANDS))),
              file=sys.stderr)
        return 2
    return COMMANDS[verb](argv)


if __name__ == "__main__":
    sys.exit(main())
