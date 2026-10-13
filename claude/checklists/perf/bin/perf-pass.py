#!/usr/bin/env python3
"""Drives loading and responsiveness fixes into a project's own files, and proves the page still looks the same.

Every rule here changes how bytes reach the browser, never what those bytes
describe. A route is split, an image is offered at more widths, an animation is
expressed as a transform: the reader sees the page they saw before, sooner. That
invariant is the reason this checklist can run unattended, and it is the easiest
one to break by accident, so it is measured rather than promised - the rendered
page is captured before the work and again after, and any element that moved,
any computed style that changed, any route that stopped rendering holds the run
open.

The other half is that a score is not a diagnosis. A lab run is a simulation on
one throttled connection and is dominated by loading, so a site whose real
visitors are waiting on interaction can be optimised for a week without the
rating moving. The run reads the field record first and names the metric that is
actually failing.

    start     open a run and print the gates it must satisfy
    scan      re-derive what the project contains, and therefore which gates apply
    verify    run every automated check and record its verdict
    resolve   answer a gate: pass, fixed, n/a, or disputed, with the evidence
              --batch <path|-> answers many at once, a status and a note each
    status    what is still outstanding; --full adds each gate's rule and fix
    report    render the filled checklist, with the before and after numbers
    finish    close the run - refuses while anything is unanswered
    gates     print the registry, whole or filtered
    routes    the routes the run found, and what each one costs
    files     the file ledger: what is still to be read and ruled on
    file-clear  rule a file as needing no performance change, with the reason
                --batch <path|-> clears many at once, a reason each

    measure   record a measurement against the run: --label before|after --file <psi json>
    parity    file a capture: --phase before|after --file <capture json>; --diff compares them
    budget    the weights this project intends to hold

    guard-stop   Stop hook: refuse to end a session with a run still open
    guard-land   PreToolUse hook: refuse `git commit` / `gh pr create` mid-run
    hook-skill   PostToolUse hook: open a run when the skill is invoked
    hook-read    PostToolUse hook: credit a file in the ledger once it is read
    hook-edit    PostToolUse hook: record what a run touched
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
    ENGINE = HOME / ".claude" / "checklists" / "perf"
GATES_DIR = ENGINE / "checklist" / "gates"
SKILL = ENGINE
SCRIPTS = SKILL / "scripts"

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


RUNS = _state_root() / "perf-runs"
CURRENT = RUNS / "current.json"

KINDS = ["site", "app", "spa", "docs", "store", "landing"]


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
STYLE_EXT = {".css", ".scss", ".sass", ".less", ".styl"}
CONFIG_EXT = {".json", ".yml", ".yaml", ".toml"}
SOURCE_EXT = MARKUP_EXT | CODE_EXT | STYLE_EXT | CONFIG_EXT

# What the browser is served. A vercel.json CSP naming a font host, a lockfile
# naming a package and a CI workflow installing one all describe the project
# rather than run in it, so a check that reads them reports the description as
# the defect - and where a match is what closes a gate, certifies a page by the
# config key that mentions the thing it lacks. A check that wants a manifest
# reads project["manifest"] or names CONFIG_EXT and says why.
SERVED_EXT = MARKUP_EXT | CODE_EXT | STYLE_EXT

# Markdown-family files can be real routes, so they stay in the markup set - but a
# fenced block in one is an example of markup rather than markup. Read whole, a
# guide showing the anti-pattern it warns against is reported as the page
# committing it, and a guide naming the remedy closes the gate for a page that
# never applied it.
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[^\n]*$",
                   re.M | re.S)
PROSE_EXT = {".md", ".mdx"}
RASTER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"}
MODERN_EXT = {".webp", ".avif"}
IMAGE_EXT = RASTER_EXT | MODERN_EXT | {".svg"}
FONT_EXT = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
MEDIA_EXT = IMAGE_EXT | FONT_EXT | {".mp4", ".webm", ".mov"}

# Trees nobody should be judged on. A dependency's unsplit bundle is not this
# project's, and sweeping it buries the files that are.
SKIP_DIRS = {"node_modules", ".git", ".next", ".nuxt", ".svelte-kit",
             "vendor", "__pycache__", ".venv", "coverage", ".turbo", ".cache",
             ".intelligence", ".astro", "storybook-static", ".vercel", ".output",
             ".idea", ".vscode", ".fleet", ".gradle"}

# The build output is read for what it weighs, never judged as source. Minified
# code fails every readability rule ever written and none of that is a defect.
BUILD_DIRS = {"dist", "build", "out", ".next", ".output", ".svelte-kit"}

NON_PAGE = re.compile(r"\.(test|spec|stories|story|d)\.[jt]sx?$|__tests__|__mocks__", re.I)


def _ignored_paths(root, paths):
    """The subset git ignores, asked in one batch.

    check-ignore --stdin answers for many paths in one process, which matters
    on a tree with several thousand files. A target outside a repository, or a
    git that will not answer, yields nothing: the hardcoded skip list is what
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


def iter_files(targets, include_build=False):
    seen = []
    skip = SKIP_DIRS if include_build else (SKIP_DIRS | BUILD_DIRS)
    for target in targets:
        p = Path(target).expanduser()
        if p.is_file():
            seen.append(p)
        elif p.is_dir():
            found = [c for c in p.rglob("*")
                     if c.is_file() and not any(part in skip for part in c.parts)]
            # A run that asked for the build wants the build; otherwise what
            # git ignores is output rather than something to rule on.
            if not include_build:
                ignored = _ignored_paths(p, found)
                found = [c for c in found if str(c) not in ignored]
            seen.extend(found)
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


# ---------------------------------------------------------------- the project model

ROUTE_PATTERNS = [
    (re.compile(r"(^|/)app/(.*/)?page\.[jt]sx?$"), "next-app"),
    (re.compile(r"(^|/)pages/(?!api/).*\.[jt]sx?$"), "next-pages"),
    (re.compile(r"(^|/)src/pages/.*\.(astro|md|mdx|html)$"), "astro"),
    (re.compile(r"(^|/)src/routes/(.*/)?\+page\.svelte$"), "sveltekit"),
    (re.compile(r"(^|/)pages/.*\.vue$"), "nuxt"),
    (re.compile(r"(^|/)src/(pages|views|routes|screens)/.*\.[jt]sx?$"), "spa-route"),
    (re.compile(r"(^|/)src/(pages|views|routes|screens)/.*\.vue$"), "spa-route"),
    (re.compile(r"\.html?$"), "html"),
]


def classify_route(path, root):
    try:
        rel = str(Path(path).resolve().relative_to(root))
    except (ValueError, OSError):
        rel = str(path)
    rel = rel.replace(os.sep, "/")
    if NON_PAGE.search(rel) or any(part in BUILD_DIRS for part in rel.split("/")):
        return None, rel
    for pattern, kind in ROUTE_PATTERNS:
        if pattern.search(rel):
            return kind, rel
    return None, rel


def read_sources(targets):
    out = {}
    for f in iter_files(targets):
        if f.suffix.lower() in SOURCE_EXT and f.stat().st_size < 4_000_000:
            try:
                out[str(f.resolve())] = f.read_text(errors="replace")
            except OSError:
                continue
    return out


def find_build(root):
    """The most recently written build output under the project, if there is one.

    Half the gates in this checklist answer about what shipped rather than what was
    written, and the two differ by everything the bundler did. A run with no build
    reports those gates as inconclusive rather than passing them on the source.
    """
    best, when = None, 0
    for name in BUILD_DIRS:
        candidate = Path(root) / name
        if not candidate.is_dir():
            continue
        files = [f for f in candidate.rglob("*") if f.is_file()]
        if not files:
            continue
        newest = max(f.stat().st_mtime for f in files)
        if newest > when:
            best, when = candidate, newest
    return best


def build_is_stale(build_dir, targets):
    """How far behind the source the build output is, in seconds, or None.

    Half the gates here read what shipped rather than what was written. A build from
    last week answers them about a page nobody is being served, and every one of
    those answers looks exactly like a real one.
    """
    if not build_dir or not build_dir.is_dir():
        return None
    built = max((f.stat().st_mtime for f in build_dir.rglob("*") if f.is_file()), default=0)
    written = max((f.stat().st_mtime for f in iter_files(targets)
                   if f.suffix.lower() in SOURCE_EXT), default=0)
    return int(written - built) if written > built else 0


def build_assets(build_dir):
    """Every shipped asset with its byte weight, grouped by what it is."""
    if not build_dir or not build_dir.is_dir():
        return {}
    out = {}
    for f in build_dir.rglob("*"):
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        kind = ("js" if suffix in {".js", ".mjs"} else
                "css" if suffix == ".css" else
                "html" if suffix in {".html", ".htm"} else
                "font" if suffix in FONT_EXT else
                "image" if suffix in IMAGE_EXT else "other")
        out[str(f)] = {"kind": kind, "bytes": f.stat().st_size,
                       "name": f.name, "rel": str(f.relative_to(build_dir))}
    return out


def manifest(root):
    path = Path(root) / "package.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {}


def build_project(run):
    root = Path(run["targets"][0])
    root = root if root.is_dir() else root.parent
    project = project_root(root) or root
    sources = read_sources(run["targets"])
    routes = []
    for path in sorted(sources):
        if Path(path).suffix.lower() not in MARKUP_EXT:
            continue
        kind, rel = classify_route(path, project)
        if kind:
            routes.append({"path": path, "rel": rel, "kind": kind,
                           "bytes": len(sources[path])})
    build = find_build(project)
    media = [f for f in iter_files(run["targets"]) if f.suffix.lower() in MEDIA_EXT]
    stale = build_is_stale(build, run["targets"])
    return {
        "root": project,
        "sources": sources,
        "routes": routes,
        "build": build,
        "assets": build_assets(build),
        "media": media,
        "manifest": manifest(project),
        "stale": stale,
        "probe": run.get("probe") or {},
    }


# What the project contains, and therefore which gates it answers for. Read from the
# source rather than declared, because a declaration is a claim and this is a fact.
CONTEXT_SIGNS = {
    "js": r"\.[jt]sx?$|<script\b",
    "css": r"\.(css|scss|sass|less)$|<style\b|className=|class=",
    "markup": r"\.(html|htm|jsx|tsx|vue|svelte|astro)$",
    "router": r"react-router|createBrowserRouter|vue-router|@angular/router|<Routes\b|"
              r"<Route\b|defineRouter|createRouter",
    "image": r"<img\b|<picture\b|next/image|<Image\b|background-image|\.(webp|avif|png|jpe?g)\b",
    "font": r"@font-face|fonts\.googleapis|next/font|font-family",
    "animation": r"framer-motion|from ['\"]motion|gsap|animejs|@keyframes|transition:|"
                 r"animate\(|aos|AOS|useSpring|useAnimation",
    "raf": r"requestAnimationFrame|useFrame|new Renderer\(|THREE\.|ogl|pixi",
    "thirdparty": r"googletagmanager|google-analytics|gtag\(|facebook\.net|hotjar|"
                  r"intercom|drift|segment\.com|clarity\.ms|plausible|<script[^>]+src=[\"']https?://",
    "embed": r"youtube\.com/embed|player\.vimeo|<iframe\b|google\.com/maps/embed",
    "list": r"\.map\(\s*\(?[\w{]|v-for=|\{#each\b",
    "spa": r"<div id=[\"']root[\"']|<div id=[\"']app[\"']|createRoot\(|ReactDOM\.render|"
           r"createApp\(|mount\(",
    "script": r"<script\b",
}


def build_context(run, project):
    # The signs are read off what the browser is served. A cautionary sentence in a
    # workflow or a key in a manifest names a font, a third-party script and an
    # animation library the project never loads, and each one adds a gate the run
    # then has to answer for a thing that is not there.
    served = dict(source_items(project))
    names = "\n".join(served.keys())
    blob = names + "\n" + "\n".join(served.values())
    ctx = {"always": True}
    for name_, pattern in CONTEXT_SIGNS.items():
        ctx[name_] = bool(re.search(pattern, blob, re.M))
    deps = dict((project["manifest"].get("dependencies") or {}))
    deps.update(project["manifest"].get("devDependencies") or {})
    ctx["deps"] = bool(deps)
    ctx["built"] = bool(project["build"])
    ctx["routes"] = bool(project["routes"])
    ctx["multiroute"] = len(project["routes"]) > 1
    ctx["live"] = bool(run.get("url"))
    # A single-page app is the case the whole loading half of this checklist exists
    # for, so it is read from the framework as well as the markup: a React entry with
    # a router is one whatever the index file happens to say.
    ctx["spa"] = ctx["spa"] or bool(ctx["router"] and re.search(r"\breact\b|\bvue\b|svelte", names + blob, re.I))
    for flag in run.get("flags", []):
        ctx[flag] = True
    for flag in ("ssr", "static", "nomeasure"):
        ctx.setdefault(flag, False)
    return ctx


# ---------------------------------------------------------------- scopes

# A scope keeps the gates whose id starts with one of its prefixes, plus any exact
# ids named beside them; the third field is what a scoped run says it covers.
SCOPES = {
    "delivery": (["DEL"], [], "redirects, compression, caching and cold origins"),
    "js": (["JSB"], [], "bundle splitting, duplicate dependencies and first-load weight"),
    "render": (["RND"], [], "the critical path to first paint"),
    "media": (["IMG"], [], "images, fonts and embeds"),
    "inp": (["INP"], [], "responsiveness and main-thread work"),
    "measure": (["MSR"], [], "before and after numbers"),
    "parity": (["PAR"], [], "proof that nothing a reader sees changed"),
}

# Check types no scope narrows out: they answer for the run itself rather than
# for an area of the page.
SCOPE_ALWAYS = {"run_opened", "all_resolved", "report_emitted", "file_coverage",
                "scope_coverage", "baseline_taken"}


def in_scope(gate, scope):
    if not scope:
        return True
    picked = SCOPES.get(scope)
    if not picked:
        return True
    prefixes, ids, _ = picked
    if gate["check"]["type"] in SCOPE_ALWAYS:
        return True
    # Parity answers for every change this run made, whatever the run narrowed to.
    # A scoped run that skipped it would be a licence to move the page quietly.
    if gate["id"].startswith("PAR-"):
        return True
    if gate["id"] in ids:
        return True
    return any(gate["id"].startswith(p + "-") for p in prefixes)


def scope_line(run):
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


def kb(n):
    return "%.1fKB" % (n / 1024.0)


def source_items(project, suffixes=SERVED_EXT):
    """Source files, narrowed by extension, with comments and examples stripped.

    Every check below asks whether the project does something. Code inside a comment
    is not the project doing it, and a commented-out block reads to a regex exactly
    like a live one. A fenced block in a Markdown route is the same thing one step
    out: an example of the code rather than the code. The default is what a browser
    is served, so a check reaches a manifest only by naming CONFIG_EXT.
    """
    for path, text in project["sources"].items():
        suffix = Path(path).suffix.lower()
        if suffixes and suffix not in suffixes:
            continue
        if suffix in PROSE_EXT:
            text = FENCE.sub("", text)
        yield path, HTML_COMMENT.sub("", text)


def _hits(project, pattern, suffixes=SERVED_EXT, flags=re.I):
    out = []
    rx = re.compile(pattern, flags)
    for path, text in source_items(project, suffixes):
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                out.append("%s:%d" % (base(path), i))
    return out


def verdict_list(bad, total, what, hint=""):
    if not bad:
        return PASS, "all %d %s" % (total, what)
    shown = ", ".join(bad[:8])
    more = " and %d more" % (len(bad) - 8) if len(bad) > 8 else ""
    return FAIL, "%d of %d do not %s: %s%s%s" % (
        len(bad), total, what, shown, more, (" - " + hint) if hint else "")


def built_html(project):
    """The HTML the build emits, which is what the browser receives."""
    out = {}
    for path, meta in project["assets"].items():
        if meta["kind"] == "html":
            try:
                out[path] = Path(path).read_text(errors="replace")
            except OSError:
                continue
    return out


def entry_html(project):
    """The document a visitor lands on, built where there is a build, source otherwise."""
    built = built_html(project)
    for path, text in built.items():
        if base(path) == "index.html":
            return path, text, True
    if built:
        path = sorted(built)[0]
        return path, built[path], True
    for path, text in source_items(project, {".html", ".htm"}):
        if base(path) == "index.html":
            return path, text, False
    return None, "", False


CURL_TIMEOUT = 25


def probe(run, url, method="GET"):
    """Status, headers and timing for one URL, cached on the run.

    Four gates answer about what the server sends rather than what the repo holds,
    and a response header cannot be read out of source. The result is kept on the
    run so a re-run of the sweep does not re-request the site each time.
    """
    cache = run.setdefault("probe", {})
    key = "%s %s" % (method, url)
    if key in cache:
        return cache[key]
    args = ["curl", "-sS", "-o", "/dev/null", "-D", "-", "--max-time", str(CURL_TIMEOUT),
            "-w", "\n__STATUS__ %{http_code} %{time_starttransfer} %{num_redirects} %{url_effective}\n",
            "-H", "Accept-Encoding: br, gzip",
            "-A", "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
            url]
    if method == "HEAD":
        args.insert(1, "-I")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=CURL_TIMEOUT + 10)
    except (OSError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": str(exc)}
        cache[key] = result
        return result
    headers, status, ttfb, hops, final = {}, 0, 0.0, 0, url
    for line in out.stdout.splitlines():
        if line.startswith("__STATUS__"):
            parts = line.split()
            status = int(parts[1]) if len(parts) > 1 else 0
            ttfb = float(parts[2]) if len(parts) > 2 else 0.0
            hops = int(parts[3]) if len(parts) > 3 else 0
            final = parts[4] if len(parts) > 4 else url
        elif ":" in line and not line.startswith("HTTP/"):
            name_, _, value = line.partition(":")
            headers.setdefault(name_.strip().lower(), value.strip())
    result = {"ok": out.returncode == 0 and status > 0, "status": status, "ttfb": ttfb,
              "hops": hops, "final": final, "headers": headers,
              "error": out.stderr.strip()[:200] if out.returncode != 0 else ""}
    cache[key] = result
    return result


def need_url(run):
    return run.get("url")


def no_url(what):
    return UNKNOWN, ("no URL on this run, so %s could not be read. Add one: "
                     "perf-pass.py scan --url https://<host>" % what)


# ---------------------------------------------------------------- delivery


def check_redirect_chain(gate, project, run, ctx):
    url = need_url(run)
    if not url:
        return no_url("the redirect chain")
    host = re.sub(r"^https?://", "", url).split("/")[0]
    other = host[4:] if host.startswith("www.") else "www." + host
    results = []
    for candidate in ("https://%s" % host, "https://%s" % other):
        got = probe(run, candidate)
        if not got.get("ok"):
            continue
        results.append((candidate, got["hops"], got["status"], got["final"]))
    if not results:
        return UNKNOWN, "neither host answered"
    bad = ["%s takes %d hop(s) to reach %s" % (u, h, f) for u, h, s, f in results if h > 0]
    if bad:
        return FAIL, ("%s. A redirect is a full round trip before the browser knows there "
                      "is a page." % "; ".join(bad))
    return PASS, "both %s and %s serve the document directly" % (host, other)


def check_compression(gate, project, run, ctx):
    url = need_url(run)
    if not url:
        return no_url("content encoding")
    got = probe(run, url)
    if not got.get("ok"):
        return UNKNOWN, "the document did not answer: %s" % got.get("error", "no response")
    encoding = got["headers"].get("content-encoding", "")
    if encoding in ("br", "gzip", "zstd", "deflate"):
        return PASS, "the document arrives %s encoded" % encoding
    return FAIL, "the document arrives uncompressed (no content-encoding header)"


def check_static_cache(gate, project, run, ctx):
    url = need_url(run)
    if not url:
        return no_url("cache headers")
    hashed = [m for m in project["assets"].values()
              if m["kind"] in ("js", "css")
              and re.search(r"[-.][A-Za-z0-9_]{8,}\.(js|css)$", m["name"])]
    if not hashed:
        return UNKNOWN, "no content-hashed assets in the build to ask about"
    origin = re.match(r"https?://[^/]+", url)
    if not origin:
        return UNKNOWN, "the run's URL has no origin to resolve assets against"
    sample = hashed[0]
    got = probe(run, "%s/%s" % (origin.group(0), sample["rel"].lstrip("/").replace(os.sep, "/")))
    if not got.get("ok") or got["status"] >= 400:
        return UNKNOWN, ("could not fetch %s to read its cache policy (status %s)"
                         % (sample["name"], got.get("status")))
    cache = got["headers"].get("cache-control", "")
    age = re.search(r"max-age=(\d+)", cache)
    if age and int(age.group(1)) >= 2_592_000:
        return PASS, "%s is served %s" % (sample["name"], cache)
    return FAIL, ("%s is served %s - a content-hashed file cannot go stale, so it should "
                  "be immutable for a year" % (sample["name"], cache or "with no cache policy"))


def check_html_cache(gate, project, run, ctx):
    url = need_url(run)
    if not url:
        return no_url("the document cache policy")
    got = probe(run, url)
    if not got.get("ok"):
        return UNKNOWN, "the document did not answer"
    cache = got["headers"].get("cache-control", "")
    if not cache:
        return FAIL, "the document carries no Cache-Control, so every cache in the path invents one"
    return PASS, "the document declares %s" % cache


def check_ttfb_budget(gate, project, run, ctx):
    url = need_url(run)
    if not url:
        return no_url("time to first byte")
    got = probe(run, url)
    if not got.get("ok"):
        return UNKNOWN, "the document did not answer"
    limit = gate["check"].get("max", 800) / 1000.0
    if got["ttfb"] <= limit:
        return PASS, "first byte in %dms" % (got["ttfb"] * 1000)
    return FAIL, ("first byte in %dms against a %dms budget%s"
                  % (got["ttfb"] * 1000, limit * 1000,
                     " - %d redirect(s) are inside that" % got["hops"] if got["hops"] else ""))


THIRD_PARTY_SRC = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']https?://([^/\"']+)", re.I)


def check_preconnect(gate, project, run, ctx):
    path, html, built = entry_html(project)
    if not html:
        return UNKNOWN, "no entry document found to read"
    origins = set(THIRD_PARTY_SRC.findall(html))
    origins |= set(re.findall(
        r"<link\b[^>]*\bhref\s*=\s*[\"']https?://([^/\"']+)[^>]*\brel\s*=\s*[\"']stylesheet",
        html, re.I))
    if not origins:
        return PASS, "the first screen depends on no external origin"
    declared = set(re.findall(
        r"rel\s*=\s*[\"'](?:preconnect|dns-prefetch)[\"'][^>]*href\s*=\s*[\"']https?://([^/\"']+)",
        html, re.I))
    declared |= set(re.findall(
        r"href\s*=\s*[\"']https?://([^/\"']+)[\"'][^>]*rel\s*=\s*[\"'](?:preconnect|dns-prefetch)",
        html, re.I))
    missing = sorted(origins - declared)
    if not missing:
        return PASS, "all %d external origin(s) are preconnected" % len(origins)
    return FAIL, ("%d origin(s) on the critical path are not preconnected: %s"
                  % (len(missing), ", ".join(missing[:5])))


def check_thirdparty_defer(gate, project, run, ctx):
    path, html, built = entry_html(project)
    if not html:
        return UNKNOWN, "no entry document found to read"
    head = re.search(r"<head\b[^>]*>(.*?)</head>", html, re.S | re.I)
    region = HTML_COMMENT.sub("", head.group(1) if head else html)
    bad = []
    for tag in re.findall(r"<script\b[^>]*>", region, re.I):
        if "src=" not in tag.lower():
            continue
        if re.search(r"\b(defer|async)\b|type\s*=\s*[\"']module[\"']", tag, re.I):
            continue
        src = re.search(r"src\s*=\s*[\"']([^\"']+)", tag, re.I)
        bad.append(src.group(1)[:60] if src else tag[:60])
    if not bad:
        return PASS, "every script in the document head defers, is async, or is a module"
    return FAIL, "%d script(s) block the document: %s" % (len(bad), ", ".join(bad[:5]))


def check_unused_preload(gate, project, run, ctx):
    path, html, built = entry_html(project)
    if not html:
        return UNKNOWN, "no entry document found to read"
    preloads = re.findall(r"<link\b[^>]*\brel\s*=\s*[\"']preload[\"'][^>]*>", html, re.I)
    if not preloads:
        return PASS, "nothing is preloaded, so nothing is preloaded needlessly"
    # This one asks whether anything at all names the file, so it takes the whole
    # tree on purpose: a build config or a manifest naming a preloaded asset is a
    # real use of it, and reading only the served set would call it needless.
    blob = html + "\n" + "\n".join(t for _, t in source_items(project, SOURCE_EXT))
    unused = []
    for tag in preloads:
        href = re.search(r"href\s*=\s*[\"']([^\"']+)", tag, re.I)
        if not href:
            continue
        name_ = base(href.group(1))
        # A preload earns its place when something other than the preload tag names
        # the same file, so one mention in the whole project is a preload for nothing.
        if len(re.findall(re.escape(name_), blob)) <= 1:
            unused.append(name_)
    if not unused:
        return PASS, "all %d preload(s) name an asset the page uses" % len(preloads)
    return FAIL, ("%d preload(s) name an asset nothing else references: %s - a preload for "
                  "an unused file competes with the ones that matter"
                  % (len(unused), ", ".join(unused[:5])))


# ---------------------------------------------------------------- javascript delivery

# Packages that solve the same problem. Shipping two of them costs both and uses one.
EQUIVALENT = [
    ("animation", {"framer-motion", "motion", "gsap", "animejs", "react-spring",
                   "@react-spring/web", "aos", "react-awesome-reveal"}),
    ("dates", {"moment", "dayjs", "date-fns", "luxon", "js-joda"}),
    ("http", {"axios", "superagent", "got", "ky", "node-fetch"}),
    ("3d", {"three", "ogl", "babylonjs", "pixi.js", "@react-three/fiber"}),
    ("utility", {"lodash", "lodash-es", "underscore", "ramda"}),
    ("state", {"redux", "@reduxjs/toolkit", "zustand", "jotai", "recoil", "mobx", "valtio"}),
    ("charts", {"chart.js", "recharts", "victory", "echarts", "d3", "apexcharts"}),
    ("icons", {"react-icons", "lucide-react", "@heroicons/react", "feather-icons",
               "@fortawesome/react-fontawesome"}),
    ("carousel", {"swiper", "slick-carousel", "embla-carousel-react", "keen-slider"}),
]

# Libraries large enough that loading them before they are needed is the whole cost.
HEAVY = {
    "three": 600, "ogl": 60, "babylonjs": 2000, "pixi.js": 400,
    "@stripe/stripe-js": 40, "chart.js": 200, "echarts": 800, "d3": 250,
    "monaco-editor": 2000, "@monaco-editor/react": 2000, "codemirror": 300,
    "pdfjs-dist": 400, "jspdf": 350, "html2canvas": 200, "xlsx": 900,
    "quill": 300, "draft-js": 250, "@tiptap/react": 300, "swiper": 150,
    "framer-motion": 120, "motion": 120, "gsap": 70, "mapbox-gl": 800,
    "leaflet": 150, "video.js": 500, "openai": 250, "@supabase/supabase-js": 120,
}

# Packages built for a server. In a browser bundle each is weight the visitor pays
# for and an interface that expects a secret in the environment.
SERVER_ONLY = {"openai", "anthropic", "@anthropic-ai/sdk", "stripe", "nodemailer",
               "@prisma/client", "prisma", "mongoose", "pg", "mysql2", "redis",
               "ioredis", "aws-sdk", "@aws-sdk/client-s3", "googleapis", "twilio",
               "resend", "@sendgrid/mail", "bcrypt", "jsonwebtoken", "sharp"}


def deps_of(project):
    return dict(project["manifest"].get("dependencies") or {})


def client_sources(project):
    """Files that end up in the browser bundle, excluding anything a server runs."""
    out = {}
    for path, text in source_items(project, MARKUP_EXT | CODE_EXT):
        rel = str(path).replace(os.sep, "/")
        if re.search(r"/(api|server|scripts?|functions|supabase|edge)/|\.server\.|"
                     r"/node_modules/|\.config\.[jt]s$", rel, re.I):
            continue
        out[path] = text
    return out


STATIC_IMPORT = re.compile(r"^\s*import\s[^;\n]*?from\s*[\"']([^\"']+)[\"']", re.M)
SIDE_IMPORT = re.compile(r"^\s*import\s*[\"']([^\"']+)[\"']", re.M)
DYNAMIC_IMPORT = re.compile(r"import\s*\(\s*[\"']?([^\"')]+)")


def package_of(specifier):
    if specifier.startswith("."):
        return None
    parts = specifier.split("/")
    return "/".join(parts[:2]) if specifier.startswith("@") else parts[0]


def imported_packages(sources):
    found = {}
    for path, text in sources.items():
        for pattern in (STATIC_IMPORT, SIDE_IMPORT):
            for spec in pattern.findall(text):
                pkg = package_of(spec)
                if pkg:
                    found.setdefault(pkg, []).append(base(path))
    return found


def check_route_splitting(gate, project, run, ctx):
    client = client_sources(project)
    routers = {p: t for p, t in client.items()
               if re.search(r"<Routes\b|createBrowserRouter|<Router\b|RouterProvider|"
                            r"createRouter\(|routes\s*[:=]\s*\[", t)}
    if not routers:
        return UNKNOWN, "no router declaration found to read route imports from"
    static_pages, lazy_pages = [], []
    for path, text in routers.items():
        for spec in STATIC_IMPORT.findall(text):
            if re.search(r"(^|/)(pages|views|routes|screens)/", spec, re.I):
                static_pages.append("%s -> %s" % (base(path), spec))
        lazy_pages.extend(DYNAMIC_IMPORT.findall(text))
    if not static_pages:
        return PASS, ("every route is reached through a dynamic import (%d found)"
                      % len(lazy_pages))
    return FAIL, ("%d route(s) are statically imported into the router, so every visitor "
                  "downloads all of them before anything is drawn: %s"
                  % (len(static_pages), ", ".join(static_pages[:6])))


def check_duplicate_deps(gate, project, run, ctx):
    deps = set(deps_of(project))
    clashes = []
    for label, family in EQUIVALENT:
        present = sorted(deps & family)
        if len(present) > 1:
            clashes.append("%s: %s" % (label, " and ".join(present)))
    if not clashes:
        return PASS, "no two dependencies do the same job"
    return FAIL, ("%d duplicated capability in the manifest - each is paid for in full and "
                  "used once: %s" % (len(clashes), "; ".join(clashes)))


def check_heavy_static_import(gate, project, run, ctx):
    client = client_sources(project)
    entries = {p: t for p, t in client.items()
               if re.search(r"(^|/)(main|index|app|_app|root)\.[jt]sx?$", str(p).replace(os.sep, "/"), re.I)
               or re.search(r"createRoot\(|ReactDOM\.render|createApp\(", t)}
    scope = entries or client
    bad = []
    for path, text in scope.items():
        static = set(STATIC_IMPORT.findall(text)) | set(SIDE_IMPORT.findall(text))
        for spec in static:
            pkg = package_of(spec)
            if pkg in HEAVY:
                bad.append("%s in %s (~%dKB)" % (pkg, base(path), HEAVY[pkg]))
    if not bad:
        return PASS, "no heavy optional library is imported before it is needed"
    unique = sorted(set(bad))
    return FAIL, ("%d heavy import(s) load before anything needs them: %s"
                  % (len(unique), ", ".join(unique[:6])))


def check_server_sdk_in_client(gate, project, run, ctx):
    client = client_sources(project)
    found = imported_packages(client)
    bad = ["%s (%s)" % (pkg, ", ".join(sorted(set(where))[:2]))
           for pkg, where in found.items() if pkg in SERVER_ONLY]
    if not bad:
        return PASS, "no server-only package is imported from client code"
    return FAIL, ("%d server-only package(s) are in the browser bundle: %s - move the call "
                  "behind an endpoint" % (len(bad), ", ".join(sorted(bad)[:5])))


VITE_CONFIG = re.compile(r"vite\.config\.[jt]s$")


def check_vendor_chunk(gate, project, run, ctx):
    configs = {p: t for p, t in project["sources"].items()
               if re.search(r"(vite|rollup|webpack|next|nuxt|astro|svelte)\.config\.[jtm]s$",
                            str(p).replace(os.sep, "/"))}
    declared = any(re.search(r"manualChunks|splitChunks|cacheGroups", t) for t in configs.values())
    chunks = [m for m in project["assets"].values() if m["kind"] == "js"]
    if declared:
        return PASS, "the build declares its own chunk split"
    if not chunks:
        return UNKNOWN, "no build output to count chunks in, and no chunk split declared"
    if len(chunks) > 2:
        return PASS, "the build emits %d JavaScript chunks" % len(chunks)
    return FAIL, ("the build emits %d JavaScript chunk(s) totalling %s - application code and "
                  "its dependencies share one file, so every deploy throws away a returning "
                  "visitor's cache"
                  % (len(chunks), kb(sum(c["bytes"] for c in chunks))))


def gzipped(path):
    import gzip
    try:
        return len(gzip.compress(Path(path).read_bytes(), 6))
    except (OSError, ValueError):
        return 0


def check_js_budget(gate, project, run, ctx):
    chunks = [(p, m) for p, m in project["assets"].items() if m["kind"] == "js"]
    if not chunks:
        return UNKNOWN, "no build output to weigh - run the project's build first"
    path, html, built = entry_html(project)
    if built and html:
        referenced = set(re.findall(r"[\"'/]([\w.-]+\.js)[\"']", html))
        first = [(p, m) for p, m in chunks if m["name"] in referenced] or chunks
    else:
        first = chunks
    total = sum(gzipped(p) for p, _ in first)
    limit = gate["check"].get("max", 200) * 1024
    biggest = sorted(first, key=lambda pair: pair[1]["bytes"], reverse=True)[:3]
    detail = ", ".join("%s %s raw" % (m["name"], kb(m["bytes"])) for _, m in biggest)
    if total <= limit:
        return PASS, "first-load JavaScript is %s compressed (%s)" % (kb(total), detail)
    return FAIL, ("first-load JavaScript is %s compressed against a %s budget - %s"
                  % (kb(total), kb(limit), detail))


def check_barrel_import(gate, project, run, ctx):
    client = client_sources(project)
    barrels = {str(Path(p).parent) for p in client
               if re.search(r"(^|/)index\.[jt]sx?$", str(p).replace(os.sep, "/"))
               and re.search(r"export\s+\*\s+from", client[p])}
    if not barrels:
        return PASS, "no directory re-exports itself through an index file"
    bad = []
    for path, text in client.items():
        for spec in STATIC_IMPORT.findall(text):
            if not spec.startswith("."):
                continue
            resolved = str((Path(path).parent / spec).resolve())
            if resolved in barrels:
                bad.append("%s -> %s" % (base(path), spec))
    if not bad:
        return PASS, "%d barrel file(s) exist and nothing imports through them" % len(barrels)
    return FAIL, ("%d import(s) go through a barrel that re-exports a whole directory: %s - "
                  "one named import pulls the directory in wherever anything in it has a "
                  "side effect" % (len(bad), ", ".join(sorted(set(bad))[:5])))


def check_modulepreload(gate, project, run, ctx):
    path, html, built = entry_html(project)
    if not built:
        return UNKNOWN, "no built document to read - modulepreload is emitted by the build"
    chunks = [m for m in project["assets"].values() if m["kind"] == "js"]
    if len(chunks) <= 1:
        return UNKNOWN, "one chunk, so there is no graph to preload"
    if re.search(r"rel\s*=\s*[\"']modulepreload", html, re.I):
        count = len(re.findall(r"rel\s*=\s*[\"']modulepreload", html, re.I))
        return PASS, "the built document declares %d modulepreload link(s)" % count
    # modulepreload is defined for module scripts. A build that emits classic
    # scripts - webpack appending a plain <script> per chunk, which is what
    # Create React App still does - warms the same request with `preload
    # as=script`, and fetching a classic chunk as a module would apply different
    # credentials and parsing rules than the request it stands in for. Asking for
    # the wrong one of the two is what this gate would otherwise reward.
    script_preload = re.findall(
        r"<link[^>]*rel\s*=\s*[\"']preload[\"'][^>]*as\s*=\s*[\"']script[\"'][^>]*>"
        r"|<link[^>]*as\s*=\s*[\"']script[\"'][^>]*rel\s*=\s*[\"']preload[\"'][^>]*>",
        html, re.I)
    if script_preload:
        return PASS, ("the built document preloads %d chunk(s) with rel=preload as=script, "
                      "which is the hint for the classic scripts this build emits"
                      % len(script_preload))
    return FAIL, ("the built document preloads none of its %d chunks, so each level of the "
                  "import graph is a serial round trip" % len(chunks))


def check_build_target(gate, project, run, ctx):
    configs = {p: t for p, t in project["sources"].items()
               if re.search(r"(vite|rollup|webpack|next|esbuild|tsconfig|babel)\.?[\w.]*\.(json|[jtm]s)$",
                            str(p).replace(os.sep, "/"))}
    browserslist = Path(project["root"]) / ".browserslistrc"
    if browserslist.is_file():
        return PASS, "targets declared in .browserslistrc"
    if project["manifest"].get("browserslist"):
        return PASS, "targets declared in package.json browserslist"
    for path, text in configs.items():
        if re.search(r"\btarget\s*:\s*[\"'\[]|\"target\"\s*:", text):
            hit = re.search(r"\btarget\s*:\s*[\"']([^\"']+)|\"target\"\s*:\s*\"([^\"]+)", text)
            value = (hit.group(1) or hit.group(2)) if hit else "declared"
            if re.search(r"es5|es2015|es2016", str(value), re.I):
                return FAIL, ("%s targets %s, so working syntax is transpiled into more code "
                              "that does the same thing more slowly" % (base(path), value))
            return PASS, "%s targets %s" % (base(path), value)
    return FAIL, ("no build target is declared, so the toolchain picks one and it is older "
                  "than the browsers this site's visitors run")


# ---------------------------------------------------------------- the critical path


def check_empty_shell(gate, project, run, ctx):
    path, html, built = entry_html(project)
    if not html:
        return UNKNOWN, "no entry document found to read"
    body = re.search(r"<body\b[^>]*>(.*?)</body>", html, re.S | re.I)
    inner = HTML_COMMENT.sub("", body.group(1) if body else "")
    # Script tags describe how the page will be built rather than what it shows, so
    # what a reader would see on arrival is whatever survives their removal.
    visible = re.sub(r"<script\b.*?</script>|<template\b.*?</template>", "", inner, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", visible)
    words = len([w for w in text.split() if len(w) > 1])
    if words >= 20:
        return PASS, "the document arrives with %d words of content already in it" % words
    return FAIL, ("the document arrives with %d word(s) of content in the body - first paint "
                  "cannot happen until the bundle has downloaded, parsed, executed and "
                  "rendered. Prerender the routes at build time." % words)


def check_css_weight(gate, project, run, ctx):
    sheets = [(p, m) for p, m in project["assets"].items() if m["kind"] == "css"]
    if not sheets:
        return UNKNOWN, "no built stylesheet to weigh - run the project's build first"
    path, html, built = entry_html(project)
    blocking = sheets
    if built and html:
        referenced = set(re.findall(r"[\"'/]([\w.-]+\.css)[\"']", html))
        blocking = [(p, m) for p, m in sheets if m["name"] in referenced] or sheets
    total = sum(gzipped(p) for p, _ in blocking)
    limit = gate["check"].get("max", 60) * 1024
    if total <= limit:
        return PASS, "render-blocking CSS is %s compressed" % kb(total)
    return FAIL, ("render-blocking CSS is %s compressed against a %s budget, and every byte "
                  "is parsed before anything paints" % (kb(total), kb(limit)))


def check_css_import(gate, project, run, ctx):
    # The cost is a second round trip at runtime, so the shipped stylesheet is what
    # answers. A bundler inlines an authored @import, and reading the source instead
    # reports a round trip the visitor never makes.
    build = find_build(project["root"])
    if build:
        rx = re.compile(r"^\s*@import\s+(?:url\()?[\"']", re.I | re.M)
        shipped = []
        for f in build.rglob("*"):
            if f.is_file() and f.suffix.lower() in STYLE_EXT:
                try:
                    if rx.search(f.read_text(errors="replace")):
                        shipped.append(base(str(f)))
                except OSError:
                    continue
        if not shipped:
            return PASS, "no stylesheet in the build discovers another at runtime"
        return FAIL, ("%d shipped stylesheet(s) discover another at runtime: %s"
                      % (len(shipped), ", ".join(shipped[:6])))
    hits = [h for h in _hits(project, r"^\s*@import\s+(?:url\()?[\"']", STYLE_EXT)]
    if not hits:
        return PASS, "no stylesheet discovers another at runtime"
    return FAIL, ("%d @import(s) turn one round trip into two before anything paints: %s"
                  % (len(hits), ", ".join(hits[:6])))


def check_font_display(gate, project, run, ctx):
    faces = []
    for path, text in source_items(project, STYLE_EXT | MARKUP_EXT):
        for block in re.findall(r"@font-face\s*\{[^}]*\}", text, re.I):
            faces.append((base(path), block))
    loaders = _hits(project, r"next/font|useFont\(|<link[^>]+fonts\.googleapis")
    if not faces and not loaders:
        return UNKNOWN, "no font declaration found to read"
    bad = [name_ for name_, block in faces if "font-display" not in block.lower()]
    google = [h for h in _hits(project, r"fonts\.googleapis[^\"']*")
              if not re.search(r"display=", h)]
    if not bad and not faces:
        return PASS, "fonts are loaded through a loader that sets display itself"
    if bad:
        return FAIL, ("%d of %d @font-face rules set no font-display, so text stays invisible "
                      "until the file arrives: %s"
                      % (len(bad), len(faces), ", ".join(sorted(set(bad))[:5])))
    return PASS, "all %d @font-face rule(s) declare a display strategy" % len(faces)


def check_font_delivery(gate, project, run, ctx):
    remote = _hits(project, r"fonts\.googleapis\.com|use\.typekit|fonts\.bunny\.net")
    local = [f for f in project["media"] if f.suffix.lower() in FONT_EXT]
    path, html, built = entry_html(project)
    preloaded = re.findall(r"<link[^>]+rel\s*=\s*[\"']preload[\"'][^>]+as\s*=\s*[\"']font",
                           html or "", re.I)
    faults = []
    if remote:
        faults.append("%d reference(s) to a third-party font host add a cold origin to the "
                      "critical path: %s" % (len(remote), ", ".join(remote[:3])))
    if local and not preloaded:
        faults.append("%d self-hosted font file(s) and no preload, so each is discovered only "
                      "once the stylesheet referencing it has parsed" % len(local))
    heavy = [f for f in local if f.stat().st_size > 100_000]
    if heavy:
        faults.append("%d font file(s) over 100KB are not subset: %s"
                      % (len(heavy), ", ".join(f.name for f in heavy[:4])))
    if not faults:
        if not local and not remote:
            return UNKNOWN, "no font files found under the targets"
        return PASS, ("%d font file(s) are self-hosted, preloaded and under 100KB each"
                      % len(local))
    return FAIL, "; ".join(faults)


def check_font_fallback(gate, project, run, ctx):
    faces = re.findall(r"@font-face\s*\{[^}]*\}",
                       "\n".join(t for _, t in source_items(project, STYLE_EXT | MARKUP_EXT)), re.I)
    if not faces:
        return UNKNOWN, "no @font-face rule to read a fallback beside"
    adjusted = [f for f in faces if re.search(r"size-adjust|ascent-override|descent-override", f, re.I)]
    # A design system names its faces once, as tokens, and every rule then reads
    # font-family: var(--font-body). The fallback lives in the token, so matching only
    # the literal property reports a site with a full stack as having none.
    stacks = _hits(project, r"(?:font-family|--[\w-]*font[\w-]*)\s*:[^;]+,[^;]+")
    if adjusted:
        return PASS, "%d face(s) declare metric overrides for their fallback" % len(adjusted)
    if not stacks:
        return FAIL, ("no font-family declares a fallback, so text has nothing to render in "
                      "while the web font downloads")
    return FAIL, ("%d font stack(s) declare a fallback but no face declares size-adjust or "
                  "ascent-override, so the swap reflows every line it was standing in for"
                  % len(stacks))


LAZY_ATTR = re.compile(r"loading\s*=\s*[\"']lazy[\"']", re.I)


HERO_FILE = re.compile(r"hero|banner|masthead|jumbotron|index|home|landing|header", re.I)

BACKGROUND_IMAGE = re.compile(r"background(?:-image)?\s*[:=]\s*[^;\n]*url\(|"
                              r"backgroundImage\s*:", re.I)


def hero_candidates(project):
    """Images the first screen is likely to measure as its largest paint.

    A background image counts, and counts against the page: the browser cannot find
    it until the stylesheet that names it has parsed, and it cannot be given a fetch
    priority at all.
    """
    tags, backgrounds = [], []
    for path, text in source_items(project, MARKUP_EXT | STYLE_EXT):
        if not HERO_FILE.search(str(path).replace(os.sep, "/")):
            continue
        for tag in re.findall(r"<(?:img|Image)\b[^>]*>", text, re.I):
            tags.append((base(path), tag))
        for i, line in enumerate(text.splitlines(), 1):
            if BACKGROUND_IMAGE.search(line):
                backgrounds.append("%s:%d" % (base(path), i))
    return tags, backgrounds


def check_lcp_discoverable(gate, project, run, ctx):
    path, html, built = entry_html(project)
    if not html:
        return UNKNOWN, "no entry document found to read"
    preloads = re.findall(r"<link[^>]+rel\s*=\s*[\"']preload[\"'][^>]+as\s*=\s*[\"']image[\"'][^>]*>",
                          html, re.I)
    priority = re.search(r"fetchpriority\s*=\s*[\"']high", html, re.I)
    if preloads and priority:
        return PASS, "the document preloads %d image(s) and marks one high priority" % len(preloads)
    if preloads:
        return FAIL, ("the document preloads %d image(s) but marks none fetchpriority=high, so "
                      "the hero queues behind everything else the parser found"
                      % len(preloads))
    if ctx.get("spa"):
        return FAIL, ("the document references no image, so whatever the browser measures as "
                      "the largest paint cannot start downloading until the bundle has run")
    return FAIL, "no image is preloaded from the document"


ENTRANCE = re.compile(r"splash|preloader|intro-?(overlay|screen|animation)|"
                      r"loading-?screen|curtain|page-?transition|enter-?site", re.I)


def check_entrance_gate(gate, project, run, ctx):
    hits = _hits(project, ENTRANCE.pattern, MARKUP_EXT | CODE_EXT | STYLE_EXT)
    if not hits:
        return PASS, "nothing stands between the document and the content"
    return FAIL, ("%d reference(s) to an entrance gate, each a deliberate delay in front of "
                  "the moment the metrics measure: %s. Where it is a design decision, answer "
                  "n/a with that reason." % (len(hits), ", ".join(hits[:5])))


# ---------------------------------------------------------------- images and embeds

IMG_TAG = re.compile(r"<(?:img|Image)\b[^>]*>", re.I)


def img_tags(project):
    out = []
    for path, text in source_items(project, MARKUP_EXT):
        for tag in IMG_TAG.findall(text):
            out.append((base(path), tag))
    return out


def check_responsive_srcset(gate, project, run, ctx):
    tags = img_tags(project)
    if not tags:
        return UNKNOWN, "no image elements found in the source"
    # A framework image component generates srcset itself, so asking it to declare
    # one by hand would fail every correctly written page.
    plain = [(f, t) for f, t in tags if not re.match(r"<Image\b", t, re.I)]
    if not plain:
        return PASS, "every image goes through a component that generates its own widths"
    bad = [f for f, t in plain if "srcset" not in t.lower()]
    if not bad:
        return PASS, "all %d image(s) offer widths through srcset" % len(plain)
    return FAIL, ("%d of %d image(s) offer one file to every device, so a 390 point screen "
                  "downloads the desktop file: %s"
                  % (len(bad), len(plain), ", ".join(sorted(set(bad))[:6])))


def check_lcp_image_eager(gate, project, run, ctx):
    heroes, backgrounds = hero_candidates(project)
    if not heroes and backgrounds:
        return FAIL, ("the first screen draws its largest image as a CSS background (%s), so "
                      "the browser cannot find it until the stylesheet naming it has parsed "
                      "and it can carry no fetch priority. An img element with "
                      "fetchpriority=high shows the same picture."
                      % ", ".join(sorted(set(backgrounds))[:4]))
    if not heroes:
        return UNKNOWN, "no hero image found to judge"
    lazy = [f for f, t in heroes if LAZY_ATTR.search(t)]
    priority = [f for f, t in heroes if re.search(r"fetchpriority\s*=\s*[\"']high", t, re.I)]
    if lazy:
        return FAIL, ("%d hero image(s) declare loading=lazy, which delays the metric by "
                      "exactly the time it takes to discover them: %s"
                      % (len(lazy), ", ".join(sorted(set(lazy))[:4])))
    if not priority:
        return FAIL, ("no hero image carries fetchpriority=high, so it competes with every "
                      "other request the parser found (%d candidate(s))" % len(heroes))
    return PASS, "the hero loads eagerly and at high priority"


def check_below_fold_lazy(gate, project, run, ctx):
    tags = img_tags(project)
    if not tags:
        return UNKNOWN, "no image elements found in the source"
    gallery = [(f, t) for f, t in tags
               if re.search(r"gallery|grid|carousel|thumb|testimonial|footer|logo-?strip", f, re.I)]
    if not gallery:
        return UNKNOWN, "no below-fold image group found to judge"
    bad = [f for f, t in gallery if not LAZY_ATTR.search(t)]
    if not bad:
        return PASS, "all %d below-fold image(s) are lazy" % len(gallery)
    return FAIL, ("%d of %d below-fold image(s) load eagerly and compete with the first "
                  "screen: %s" % (len(bad), len(gallery), ", ".join(sorted(set(bad))[:6])))


def check_image_dimensions(gate, project, run, ctx):
    tags = img_tags(project)
    if not tags:
        return UNKNOWN, "no image elements found in the source"
    bad = []
    for f, t in tags:
        if re.search(r"\bwidth\s*=|\bheight\s*=|aspect-?ratio|\bfill\b", t, re.I):
            continue
        bad.append(f)
    if not bad:
        return PASS, "all %d image(s) reserve their space before they load" % len(tags)
    return FAIL, ("%d of %d image(s) declare no dimensions, so the page reflows as each file "
                  "lands: %s" % (len(bad), len(tags), ", ".join(sorted(set(bad))[:6])))


NOT_A_PHOTO = re.compile(r"favicon|apple-touch|android-chrome|mstile|icon|logo|"
                         r"share-card|og-|maskable|safari-pinned", re.I)


def check_modern_format(gate, project, run, ctx):
    photos = [f for f in project["media"] if f.suffix.lower() in RASTER_EXT
              and f.suffix.lower() != ".gif" and f.stat().st_size > 20_000
              and not NOT_A_PHOTO.search(f.name)]
    if not photos:
        return PASS, "no legacy raster photographs over 20KB under the targets"
    modern = [f for f in project["media"] if f.suffix.lower() in MODERN_EXT]
    return FAIL, ("%d photograph(s) ship as %s while %d modern file(s) exist alongside - the "
                  "same picture is about a third of the bytes as AVIF or WebP: %s"
                  % (len(photos), "/".join(sorted({f.suffix for f in photos})), len(modern),
                     ", ".join(f.name for f in photos[:5])))


def check_oversized_image(gate, project, run, ctx):
    big = [f for f in project["media"]
           if f.suffix.lower() in (RASTER_EXT | MODERN_EXT) and f.stat().st_size > 300_000]
    if not big:
        return PASS, "no image file over 300KB under the targets"
    return FAIL, ("%d image file(s) are over 300KB, which is more than a phone screen can "
                  "use: %s" % (len(big), ", ".join("%s %s" % (f.name, kb(f.stat().st_size))
                                                   for f in sorted(big, key=lambda x: -x.stat().st_size)[:5])))


def check_embed_facade(gate, project, run, ctx):
    hits = _hits(project, r"<iframe\b[^>]*(youtube|vimeo|google\.com/maps|spotify|soundcloud)",
                 MARKUP_EXT)
    if not hits:
        return PASS, "no third-party player or map is embedded directly"
    facade = _hits(project, r"facade|lite-?youtube|click-?to-?load|poster\s*=|thumbnail",
                   MARKUP_EXT | CODE_EXT)
    if facade:
        return PASS, "embeds are represented by a still until the reader asks for them"
    return FAIL, ("%d embed(s) mount their third-party player on load, which costs more than "
                  "the rest of the page: %s" % (len(hits), ", ".join(hits[:5])))


def check_image_budget(gate, project, run, ctx):
    images = [f for f in project["media"] if f.suffix.lower() in (RASTER_EXT | MODERN_EXT)]
    if not images:
        return UNKNOWN, "no image files under the targets to weigh"
    total = sum(f.stat().st_size for f in images)
    limit = gate["check"].get("max", 500) * 1024
    # The budget is for what one screen loads, and a repository holds every screen's
    # worth, so the whole directory failing is a signal to check what the first one
    # actually pulls rather than a verdict on the total.
    if total <= limit:
        return PASS, "%d image(s) totalling %s under the targets" % (len(images), kb(total))
    eager = [f for f, t in img_tags(project) if not LAZY_ATTR.search(t)]
    return FAIL, ("%d image(s) totalling %s sit under the targets and %d image element(s) "
                  "load eagerly - confirm what the first screen pulls and lazy-load the rest"
                  % (len(images), kb(total), len(eager)))


# ---------------------------------------------------------------- responsiveness

# Properties whose animation makes the browser lay the page out again on every
# frame, on the same thread that has to answer the next tap.
LAYOUT_PROPS = r"width|height|top|left|right|bottom|margin|padding|font-size|line-height"


def check_compositor_animation(gate, project, run, ctx):
    bad = []
    for path, text in source_items(project, STYLE_EXT | MARKUP_EXT | CODE_EXT):
        for block in re.findall(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", text, re.I):
            props = re.findall(r"(?m)^\s*(" + LAYOUT_PROPS + r")\s*:", block)
            if props:
                bad.append("%s @keyframes animates %s" % (base(path), "/".join(sorted(set(props)))))
        for decl in re.findall(r"transition\s*:\s*([^;{}]+)", text, re.I):
            props = re.findall(r"\b(" + LAYOUT_PROPS + r")\b", decl)
            if props:
                bad.append("%s transitions %s" % (base(path), "/".join(sorted(set(props)))))
    if not bad:
        return PASS, "continuous motion animates transform and opacity only"
    unique = sorted(set(bad))
    return FAIL, ("%d animation(s) drive a layout property, so the browser lays the page out "
                  "again on every frame: %s. The same motion expressed as a transform looks "
                  "identical." % (len(unique), "; ".join(unique[:6])))


SCROLL_LISTENER = re.compile(
    r"addEventListener\s*\(\s*[\"'](scroll|wheel|touchmove|touchstart)[\"']\s*,([^)]*)\)", re.I)


def check_passive_listeners(gate, project, run, ctx):
    bad = []
    for path, text in source_items(project, CODE_EXT | MARKUP_EXT):
        for name_, rest in SCROLL_LISTENER.findall(text):
            if "passive" not in rest:
                bad.append("%s %s" % (base(path), name_))
    if not bad:
        return PASS, "every scroll and touch listener is registered passive"
    unique = sorted(set(bad))
    return FAIL, ("%d listener(s) are not passive, so the browser waits for each before it "
                  "may scroll: %s" % (len(unique), ", ".join(unique[:6])))


def check_unbounded_raf(gate, project, run, ctx):
    loops = {}
    for path, text in source_items(project, CODE_EXT | MARKUP_EXT):
        if re.search(r"requestAnimationFrame|useFrame\(|new Renderer\(", text):
            loops[base(path)] = text
    if not loops:
        return UNKNOWN, "no animation frame loop found to judge"
    bad = []
    for name_, text in loops.items():
        stops = bool(re.search(r"cancelAnimationFrame|visibilitychange|IntersectionObserver|"
                               r"isIntersecting|document\.hidden", text))
        respects = bool(re.search(r"prefers-reduced-motion", text))
        missing = []
        if not stops:
            missing.append("never stops")
        if not respects:
            missing.append("ignores reduced motion")
        if missing:
            bad.append("%s %s" % (name_, " and ".join(missing)))
    if not bad:
        return PASS, "every frame loop stops off screen and respects reduced motion"
    return FAIL, ("%d frame loop(s) run for the length of the visit: %s - each competes with "
                  "every interaction on the page" % (len(bad), "; ".join(bad[:5])))


LAYOUT_READ = r"offsetWidth|offsetHeight|offsetTop|offsetLeft|clientWidth|clientHeight|" \
              r"scrollWidth|scrollHeight|getBoundingClientRect|getComputedStyle|scrollTop"


def check_layout_thrash(gate, project, run, ctx):
    bad = []
    for path, text in source_items(project, CODE_EXT | MARKUP_EXT):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not re.search(LAYOUT_READ, line):
                continue
            window = "\n".join(lines[i:i + 6])
            if re.search(r"\.style\.\w+\s*=|\.setAttribute\(\s*[\"']style|classList\.(add|remove|toggle)",
                         window):
                bad.append("%s:%d" % (base(path), i + 1))
    if not bad:
        return PASS, "no measurement is followed by a style write in the same pass"
    return FAIL, ("%d place(s) read a measured property and then write a style, forcing a "
                  "synchronous layout each time: %s. Read everything first, then write."
                  % (len(bad), ", ".join(bad[:6])))


def check_handler_yield(gate, project, run, ctx):
    heavy = []
    for path, text in source_items(project, CODE_EXT | MARKUP_EXT):
        for match in re.finditer(r"(?:onClick|onChange|onInput|onSubmit|addEventListener\s*\(\s*[\"']"
                                 r"(?:click|input|change|submit)[\"'])", text):
            window = text[match.start():match.start() + 1200]
            works = re.search(r"\.filter\(|\.sort\(|\.reduce\(|JSON\.parse\(|new RegExp\(|"
                              r"for\s*\(|while\s*\(", window)
            yields_ = re.search(r"await |requestAnimationFrame|setTimeout|startTransition|"
                                r"scheduler\.|queueMicrotask|useDeferredValue", window)
            if works and not yields_:
                line = text[:match.start()].count("\n") + 1
                heavy.append("%s:%d" % (base(path), line))
    if not heavy:
        return PASS, "input handlers paint before they do further work"
    return FAIL, ("%d handler(s) do list or parse work with no yield, so the paint that ends "
                  "the measured delay waits for all of it: %s"
                  % (len(heavy), ", ".join(sorted(set(heavy))[:6])))


def check_long_list(gate, project, run, ctx):
    hits = _hits(project, r"\.map\(\s*\(?[\w{]", MARKUP_EXT)
    if not hits:
        return UNKNOWN, "no rendered list found to judge"
    windowed = _hits(project, r"react-window|react-virtual|virtua|TanStack.*[Vv]irtual|"
                     r"content-visibility|slice\(\s*\d|\.slice\(\s*(page|start|offset)",
                     MARKUP_EXT | CODE_EXT | STYLE_EXT)
    if windowed:
        return PASS, "lists are windowed, paginated, or skipped with content-visibility"
    return FAIL, ("%d rendered list(s) and nothing windowing, paginating or skipping them - "
                  "every row that exists is layout the browser carries through every "
                  "interaction: %s" % (len(hits), ", ".join(hits[:5])))


def check_content_visibility(gate, project, run, ctx):
    hits = _hits(project, r"content-visibility", STYLE_EXT | MARKUP_EXT | CODE_EXT)
    sections = _hits(project, r"<section\b", MARKUP_EXT)
    if not sections:
        return UNKNOWN, "no page sections found to judge"
    if hits:
        intrinsic = _hits(project, r"contain-intrinsic-size", STYLE_EXT | MARKUP_EXT | CODE_EXT)
        if not intrinsic:
            return FAIL, ("content-visibility is set without contain-intrinsic-size, so a "
                          "skipped section has no height and the page shifts when it is "
                          "approached: %s" % ", ".join(hits[:4]))
        return PASS, "off-screen sections are skipped and declare their intrinsic size"
    return FAIL, ("%d section(s) and none declare content-visibility, so every one costs "
                  "style and layout at load whether or not anybody scrolls to it"
                  % len(sections))


# Matched on the host a script is fetched from rather than on a product name, so a
# variable called drift in a particle field is not read as an embedded chat widget.
THIRD_PARTY_HOST = re.compile(
    r"googletagmanager\.com|google-analytics\.com|connect\.facebook\.net|"
    r"static\.hotjar\.com|widget\.intercom\.io|js\.driftt\.com|cdn\.segment\.com|"
    r"clarity\.ms|plausible\.io|snap\.licdn\.com|analytics\.tiktok\.com", re.I)


def check_thirdparty_main_thread(gate, project, run, ctx):
    path, html, built = entry_html(project)
    scripts = re.findall(r"<script\b[^>]*\bsrc\s*=\s*[\"'](https?://[^\"']+)", html or "", re.I)
    scripts += _hits(project, THIRD_PARTY_HOST.pattern, MARKUP_EXT | CODE_EXT)
    if not scripts:
        return PASS, "no third-party script is embedded in the document"
    late = re.search(r"addEventListener\s*\(\s*[\"']load[\"']|requestIdleCallback|"
                     r"setTimeout\([^,]+,\s*[1-9]\d{3}", html or "", re.I)
    blocking = [s for s in scripts if isinstance(s, str) and s.startswith("http")]
    if late:
        return PASS, "third-party code is loaded after the load event"
    return FAIL, ("%d third-party script(s) load with the page and each can hold the main "
                  "thread for longer than the whole interaction budget: %s. Load them after "
                  "load, or on an interaction."
                  % (len(scripts), ", ".join(str(s)[:50] for s in scripts[:4])))


# ---------------------------------------------------------------- measurement

# The thresholds Core Web Vitals rates as good, in milliseconds - except CLS,
# which is a unitless shift score.
GOOD = {"lcp": 2500, "inp": 200, "cls": 0.1, "fcp": 1800, "ttfb": 800}


def measurements(run, label):
    return [m for m in run.get("measures", []) if m.get("label") == label]


def check_baseline_taken(gate, project, run, ctx):
    before = measurements(run, "before")
    if before:
        first = before[0]
        return PASS, ("baseline %s taken %s: score %s, LCP %sms"
                      % (first.get("tool", "?"), first.get("at", "?"),
                         first.get("score", "?"), first.get("lcp", "?")))
    if run.get("touched"):
        return FAIL, ("%d file(s) have been edited and no baseline was taken first, so no "
                      "later number can be compared to anything" % len(run["touched"]))
    return FAIL, "no baseline measurement on this run yet, and nothing has been edited yet"


def check_measure_paired(gate, project, run, ctx):
    before, after = measurements(run, "before"), measurements(run, "after")
    if not before or not after:
        return FAIL, ("%d before and %d after measurement(s) - both are needed to say "
                      "anything moved" % (len(before), len(after)))
    a, b = before[-1], after[-1]
    faults = []
    if a.get("tool") != b.get("tool"):
        faults.append("taken with %s and %s" % (a.get("tool"), b.get("tool")))
    if a.get("strategy") != b.get("strategy"):
        faults.append("one %s and one %s" % (a.get("strategy"), b.get("strategy")))
    if a.get("url") != b.get("url"):
        faults.append("different URLs: %s and %s" % (a.get("url"), b.get("url")))
    if faults:
        return FAIL, "the two measurements are not comparable: %s" % "; ".join(faults)
    return PASS, "before and after both %s, %s, %s" % (a.get("tool"), a.get("strategy"), a.get("url"))


def check_field_data_read(gate, project, run, ctx):
    with_field = [m for m in run.get("measures", []) if m.get("field")]
    if not with_field:
        return FAIL, ("no measurement on this run carries field data - the lab score is a "
                      "simulation, the field record is what visitors experience")
    field = with_field[-1]["field"]
    return PASS, "field record read: %s" % ", ".join(
        "%s %s" % (k.upper(), v) for k, v in sorted(field.items()) if k != "overall")


def check_failing_metric_named(gate, project, run, ctx):
    named = run.get("target_metric")
    if not named:
        return FAIL, ("the run has not named the metric it is fixing. Read the field record, "
                      "then: perf-pass.py scan --metric <lcp|inp|cls|fcp|ttfb>")
    with_field = [m for m in run.get("measures", []) if m.get("field")]
    if with_field:
        field = with_field[-1]["field"]
        value = field.get(named)
        if value is not None and named in GOOD:
            try:
                over = float(value) > GOOD[named]
            except (TypeError, ValueError):
                over = True
            if not over:
                worst = [k for k, v in field.items()
                         if k in GOOD and _num(v) and float(v) > GOOD[k]]
                if worst:
                    return FAIL, ("the run names %s, which is within threshold at %s, while "
                                  "%s is not" % (named.upper(), value,
                                                 " and ".join(w.upper() for w in worst)))
    return PASS, "the run is working %s" % named.upper()


def _num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def check_measured_canonical(gate, project, run, ctx):
    measures = run.get("measures", [])
    if not measures:
        return FAIL, "nothing has been measured yet"
    bad = []
    for m in measures:
        url = m.get("url", "")
        if not url:
            continue
        got = probe(run, url)
        if got.get("ok") and got.get("hops", 0) > 0:
            bad.append("%s redirects %d time(s), and the redirect is inside every number "
                       "taken against it" % (url, got["hops"]))
    if bad:
        return FAIL, "; ".join(sorted(set(bad))[:3])
    urls = sorted({m.get("url") for m in measures if m.get("url")})
    if len(urls) < 2 and ctx.get("multiroute"):
        return FAIL, ("only %s was measured, and the project serves %d routes - the home page "
                      "says nothing about the routes carrying the traffic"
                      % (urls[0] if urls else "one URL", len(project["routes"])))
    return PASS, "measured %d URL(s), none of which redirect: %s" % (len(urls), ", ".join(urls[:4]))


def check_improvement_shown(gate, project, run, ctx):
    before, after = measurements(run, "before"), measurements(run, "after")
    if not before or not after:
        return FAIL, "no before and after pair to compare"
    a, b = before[-1], after[-1]
    metric = run.get("target_metric") or "lcp"
    moved = []
    for key in ("score", "fcp", "lcp", "tbt", "cls", "si"):
        if _num(a.get(key)) and _num(b.get(key)):
            delta = float(b[key]) - float(a[key])
            better = delta > 0 if key == "score" else delta < 0
            if abs(delta) > (0.001 if key == "cls" else 1):
                moved.append("%s %s -> %s%s" % (key.upper(), a[key], b[key],
                                                "" if better else " (worse)"))
    if not moved:
        return FAIL, ("nothing moved between the two measurements. That is a result: the "
                      "theory was wrong. Record it and name the next candidate.")
    target_ok = True
    if _num(a.get(metric)) and _num(b.get(metric)):
        target_ok = float(b[metric]) < float(a[metric])
    if not target_ok:
        return FAIL, ("%s did not improve (%s -> %s), which is the metric this run set out "
                      "to fix. Other movement: %s"
                      % (metric.upper(), a[metric], b[metric], "; ".join(moved[:4])))
    return PASS, "; ".join(moved[:6])


def check_budget_recorded(gate, project, run, ctx):
    root = Path(project["root"])
    for name_ in ("performance-budget.json", ".perf-budget.json", "budget.json",
                  "lighthouserc.json", "lighthouserc.js"):
        if (root / name_).is_file():
            return PASS, "budget recorded in %s" % name_
    if re.search(r"budget", json.dumps(project["manifest"]), re.I):
        return PASS, "budget declared in package.json"
    return FAIL, ("no budget file, so the next change that adds a megabyte is caught by "
                  "somebody noticing the site feels slow. scripts/bundle-probe.py budget "
                  "--write records the current weights as the ceiling.")


# ---------------------------------------------------------------- parity

# A box that lands within a pixel of where it was has not moved: browsers round
# subpixel layout differently between runs and a strict compare would fail on noise.
GEOMETRY_TOLERANCE = 1.0

# The computed properties the parity diff compares: the ones whose change a
# reader would see.
STYLE_PROPS = ["color", "backgroundColor", "fontFamily", "fontSize", "fontWeight",
               "lineHeight", "letterSpacing", "textAlign", "borderRadius", "boxShadow",
               "opacity", "transform", "display", "flexDirection", "justifyContent",
               "alignItems", "gap", "padding", "margin", "borderColor", "borderWidth",
               "textTransform", "zIndex", "overflow", "objectFit"]


def captures(run, phase):
    """Every capture filed for a phase, as a flat list."""
    return (run.get("parity") or {}).get(phase) or []


def capture_key(capture):
    return "%s@%s" % (capture.get("route", "/"),
                      (capture.get("viewport") or {}).get("w", "?"))


def paired_captures(run):
    before = {capture_key(c): c for c in captures(run, "before")}
    after = {capture_key(c): c for c in captures(run, "after")}
    return before, after, sorted(set(before) & set(after))


def element_map(capture):
    return {e.get("sel"): e for e in capture.get("elements", []) if e.get("sel")}


def geometry_deltas(run):
    before, after, shared = paired_captures(run)
    moved, missing = [], []
    for key in shared:
        was, now_ = element_map(before[key]), element_map(after[key])
        for sel, a in was.items():
            b = now_.get(sel)
            if b is None:
                missing.append("%s %s" % (key, sel))
                continue
            for axis in ("x", "y", "w", "h"):
                try:
                    delta = abs(float(b.get(axis, 0)) - float(a.get(axis, 0)))
                except (TypeError, ValueError):
                    continue
                if delta > GEOMETRY_TOLERANCE:
                    moved.append("%s %s %s moved %.0fpx" % (key, sel, axis, delta))
                    break
    return moved, missing


def style_deltas(run):
    before, after, shared = paired_captures(run)
    changed = []
    for key in shared:
        was, now_ = element_map(before[key]), element_map(after[key])
        for sel, a in was.items():
            b = now_.get(sel)
            if b is None:
                continue
            sa, sb = a.get("style") or {}, b.get("style") or {}
            for prop in STYLE_PROPS:
                if prop in sa and prop in sb and sa[prop] != sb[prop]:
                    changed.append("%s %s %s: %s -> %s"
                                   % (key, sel, prop, str(sa[prop])[:30], str(sb[prop])[:30]))
    return changed


def check_parity_before(gate, project, run, ctx):
    before = captures(run, "before")
    if not before:
        return FAIL, ("no capture was filed before the work. Serve the current build, run "
                      "scripts/parity-probe.js in the page, and file it: "
                      "perf-pass.py parity --phase before --file <capture.json>")
    viewports = sorted({(c.get("viewport") or {}).get("w") for c in before})
    routes = sorted({c.get("route", "/") for c in before})
    if len(viewports) < 2:
        return FAIL, ("the baseline covers one viewport (%s). A layout that holds on a phone "
                      "and breaks on a desktop passes a single-viewport check."
                      % (viewports[0] if viewports else "unknown"))
    return PASS, ("%d capture(s) taken before the work across %d route(s) and %d viewport(s)"
                  % (len(before), len(routes), len(viewports)))


def check_parity_geometry(gate, project, run, ctx):
    before, after, shared = paired_captures(run)
    if not before:
        return FAIL, "no before capture to compare against"
    if not after:
        return FAIL, ("%d capture(s) taken before the work and none after. Capture the built "
                      "output and file it: perf-pass.py parity --phase after --file <capture.json>"
                      % len(before))
    if not shared:
        return FAIL, ("the before and after captures share no route and viewport pair, so "
                      "nothing can be compared. Capture the same routes at the same widths.")
    moved, missing = geometry_deltas(run)
    if missing:
        return FAIL, ("%d element(s) present before the work are gone after it: %s"
                      % (len(missing), ", ".join(missing[:6])))
    if moved:
        return FAIL, ("%d element(s) moved or resized: %s. Revert what moved them, or reserve "
                      "the space the deferred content occupied."
                      % (len(moved), "; ".join(moved[:6])))
    counted = sum(len(element_map(before[k])) for k in shared)
    return PASS, ("%d element(s) across %d route and viewport pair(s) are within %.0fpx of "
                  "where they were" % (counted, len(shared), GEOMETRY_TOLERANCE))


def check_parity_style(gate, project, run, ctx):
    before, after, shared = paired_captures(run)
    if not before or not after:
        return FAIL, "both captures are needed before computed style can be compared"
    if not shared:
        return FAIL, "the captures share no route and viewport pair to compare"
    changed = style_deltas(run)
    if changed:
        return FAIL, ("%d computed style change(s): %s. Put them back; find the loading win "
                      "somewhere it does not cost the appearance."
                      % (len(changed), "; ".join(changed[:6])))
    return PASS, ("every captured element computes the same %d style properties it did before"
                  % len(STYLE_PROPS))


def check_parity_routes(gate, project, run, ctx):
    before, after, shared = paired_captures(run)
    if not before or not after:
        return FAIL, "both captures are needed before the route inventory can be compared"
    was = set()
    for c in before.values():
        was |= set(c.get("routes") or [])
        was.add(c.get("route", "/"))
    now_ = set()
    for c in after.values():
        now_ |= set(c.get("routes") or [])
        now_.add(c.get("route", "/"))
    lost = sorted(was - now_)
    if lost:
        return FAIL, ("%d route(s) exist before the work and not after: %s - a split at the "
                      "wrong boundary turns a route into a blank frame"
                      % (len(lost), ", ".join(lost[:8])))
    blank = [capture_key(c) for c in after.values()
             if len(c.get("elements") or []) < 3]
    if blank:
        return FAIL, ("%d route(s) captured after the work render almost nothing: %s"
                      % (len(blank), ", ".join(blank[:6])))
    return PASS, "all %d route(s) still exist and still render" % len(now_ or was)


def check_parity_interactive(gate, project, run, ctx):
    before, after, shared = paired_captures(run)
    if not before or not after:
        return FAIL, "both captures are needed before the controls can be compared"
    lost, dead = [], []
    for key in shared:
        was = {c.get("sel"): c for c in (before[key].get("interactive") or [])}
        now_ = {c.get("sel"): c for c in (after[key].get("interactive") or [])}
        for sel, control in was.items():
            if sel not in now_:
                lost.append("%s %s" % (key, sel))
            elif control.get("bound") and not now_[sel].get("bound"):
                dead.append("%s %s" % (key, sel))
    if lost:
        return FAIL, ("%d control(s) present before the work are gone: %s"
                      % (len(lost), ", ".join(lost[:6])))
    if dead:
        return FAIL, ("%d control(s) no longer have a handler bound: %s - each has lost the "
                      "module it was waiting on" % (len(dead), ", ".join(dead[:6])))
    counted = sum(len(before[k].get("interactive") or []) for k in shared)
    if not counted:
        return UNKNOWN, "the captures recorded no interactive controls to compare"
    return PASS, "all %d captured control(s) still respond" % counted


def check_parity_console(gate, project, run, ctx):
    before, after, shared = paired_captures(run)
    if not before or not after:
        return FAIL, "both captures are needed before console output can be compared"
    was, now_ = set(), set()
    for c in before.values():
        was |= {e.get("text", "")[:120] for e in (c.get("console") or [])
                if e.get("level") == "error"}
    for c in after.values():
        now_ |= {e.get("text", "")[:120] for e in (c.get("console") or [])
                 if e.get("level") == "error"}
    new = sorted(now_ - was)
    if new:
        return FAIL, ("%d error(s) the page did not throw before: %s - a dynamic import that "
                      "fails is silent to every loading metric and fatal to the feature "
                      "behind it" % (len(new), "; ".join(new[:4])))
    return PASS, ("no error was introduced (%d before, %d after)" % (len(was), len(now_)))


def check_parity_motion(gate, project, run, ctx):
    before, after, shared = paired_captures(run)
    if not before or not after:
        return FAIL, "both captures are needed before motion can be compared"
    lost, gained = [], []
    for key in shared:
        was = {a.get("sel"): a for a in (before[key].get("animations") or [])}
        now_ = {a.get("sel"): a for a in (after[key].get("animations") or [])}
        lost += ["%s %s" % (key, sel) for sel in was if sel not in now_]
        gained += ["%s %s" % (key, sel) for sel in now_ if sel not in was]
    counted = sum(len(before[k].get("animations") or []) for k in shared)
    if lost:
        return FAIL, ("%d animation(s) no longer run: %s - a transform rewrite keeps the "
                      "motion, deleting the library ends it" % (len(lost), ", ".join(lost[:6])))
    # Motion the visitor did not have before is as much a change to what they see
    # as motion taken away, and it is the shape a deferred import takes when it
    # lands late enough to animate on arrival.
    if gained:
        return FAIL, ("%d animation(s) run that did not before: %s - the page has to move "
                      "the way it moved" % (len(gained), ", ".join(gained[:6])))
    if not counted:
        # Two empty captures agree, which is a comparison that came out even
        # rather than one that could not be made.
        return PASS, "neither capture recorded an animation, so none was lost or added"
    return PASS, "all %d captured animation(s) still run" % counted


def check_parity_built(gate, project, run, ctx):
    after = captures(run, "after")
    if not after:
        return FAIL, "no capture was taken after the work"
    dev = [capture_key(c) for c in after if not c.get("built")]
    if dev:
        return FAIL, ("%d capture(s) were taken against a dev server: %s. Dev serves "
                      "unminified modules with no splitting, so it renders a page the "
                      "visitor never receives." % (len(dev), ", ".join(dev[:5])))
    return PASS, "every after capture was taken on the built output"


# ---------------------------------------------------------------- process gates

# Check types answered from the run's own state by settle_self, never by the
# sweep and never by hand.
SELF_SETTLING = {"run_opened", "all_resolved", "report_emitted", "file_coverage"}


def check_run_opened(gate, project, run, ctx):
    return PASS, "run %s opened %s" % (run["id"], run["opened"])


def check_all_resolved(gate, project, run, ctx):
    return PASS, "settled from the run's own state"


def check_report_emitted(gate, project, run, ctx):
    return PASS, "settled from the run's own state"


def check_file_coverage(gate, project, run, ctx):
    return PASS, "settled from the run's own state"


def check_scope_coverage(gate, project, run, ctx):
    """Routes the project serves that no target covers.

    Scoping to the page a prompt mentioned is the quiet way a checklist becomes a
    survey, so widening has to be argued for rather than assumed.
    """
    root = Path(project["root"])
    targets = [Path(t).resolve() for t in run["targets"]]
    outside = []
    for f in iter_files([root]):
        if f.suffix.lower() not in MARKUP_EXT:
            continue
        kind, rel = classify_route(f, root)
        if not kind:
            continue
        resolved = f.resolve()
        if not any(resolved == t or t in resolved.parents for t in targets):
            outside.append(rel)
    if not outside:
        return PASS, "the targets cover every route in the project"
    return FAIL, ("%d route(s) sit outside the targets: %s. Widen with scan --target, or "
                  "answer n/a naming why they are outside this deliverable."
                  % (len(outside), ", ".join(sorted(outside)[:8])))


CHECKS = {
    "manual": None,
    "redirect_chain": check_redirect_chain,
    "compression": check_compression,
    "static_cache": check_static_cache,
    "html_cache": check_html_cache,
    "ttfb_budget": check_ttfb_budget,
    "preconnect": check_preconnect,
    "thirdparty_defer": check_thirdparty_defer,
    "unused_preload": check_unused_preload,
    "route_splitting": check_route_splitting,
    "duplicate_deps": check_duplicate_deps,
    "heavy_static_import": check_heavy_static_import,
    "server_sdk_in_client": check_server_sdk_in_client,
    "vendor_chunk": check_vendor_chunk,
    "js_budget": check_js_budget,
    "barrel_import": check_barrel_import,
    "modulepreload": check_modulepreload,
    "build_target": check_build_target,
    "empty_shell": check_empty_shell,
    "css_weight": check_css_weight,
    "css_import": check_css_import,
    "font_display": check_font_display,
    "font_delivery": check_font_delivery,
    "font_fallback": check_font_fallback,
    "lcp_discoverable": check_lcp_discoverable,
    "entrance_gate": check_entrance_gate,
    "responsive_srcset": check_responsive_srcset,
    "lcp_image_eager": check_lcp_image_eager,
    "below_fold_lazy": check_below_fold_lazy,
    "image_dimensions": check_image_dimensions,
    "modern_format": check_modern_format,
    "oversized_image": check_oversized_image,
    "embed_facade": check_embed_facade,
    "image_budget": check_image_budget,
    "compositor_animation": check_compositor_animation,
    "passive_listeners": check_passive_listeners,
    "unbounded_raf": check_unbounded_raf,
    "layout_thrash": check_layout_thrash,
    "handler_yield": check_handler_yield,
    "long_list": check_long_list,
    "content_visibility": check_content_visibility,
    "thirdparty_main_thread": check_thirdparty_main_thread,
    "baseline_taken": check_baseline_taken,
    "measure_paired": check_measure_paired,
    "field_data_read": check_field_data_read,
    "failing_metric_named": check_failing_metric_named,
    "measured_canonical": check_measured_canonical,
    "improvement_shown": check_improvement_shown,
    "budget_recorded": check_budget_recorded,
    "parity_before": check_parity_before,
    "parity_geometry": check_parity_geometry,
    "parity_style": check_parity_style,
    "parity_routes": check_parity_routes,
    "parity_interactive": check_parity_interactive,
    "parity_console": check_parity_console,
    "parity_motion": check_parity_motion,
    "parity_built": check_parity_built,
    "run_opened": check_run_opened,
    "all_resolved": check_all_resolved,
    "report_emitted": check_report_emitted,
    "file_coverage": check_file_coverage,
    "scope_coverage": check_scope_coverage,
}


# ---------------------------------------------------------------- carry-forward

# Check types that answer to something outside the project, whose answer a file
# hash therefore cannot vouch for. A header is served by whatever the host is
# configured with today, a field record moves with the visitors who produced it,
# and a parity capture is a rendered reading of a page that was open at the
# time - none of that travels with the repository, so a stored pass would report
# green across a change the store cannot see.
NETWORK_CHECKS = {
    "compression", "html_cache", "redirect_chain", "static_cache", "ttfb_budget",
    "parity_before", "parity_built", "parity_console", "parity_geometry",
    "parity_interactive", "parity_motion", "parity_routes", "parity_style",
    "baseline_taken", "budget_recorded", "field_data_read", "failing_metric_named",
    "measure_paired", "measured_canonical", "improvement_shown",
}


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
        _CACHE = pass_cache.open_for("perf", run.get("targets") or [], GATES_DIR, TOOL,
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


def sweep_scope(project):
    """Every file the sweep reads: the source it was written in, the media it
    serves, and the build it ships, since half these gates read what shipped."""
    paths = set(project["sources"])
    paths.update(project["assets"])
    paths.update(str(f.resolve()) for f in project["media"])
    paths.add(str(Path(project["root"]) / "package.json"))
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

# Fonts are deliberately absent: the harness cannot open a binary, so a ledger
# entry for one could only ever be attested rather than earned by a read.
LEDGER_EXT = MARKUP_EXT | CODE_EXT | STYLE_EXT | IMAGE_EXT | {".json"}


def ledger_candidates(run):
    keep, excluded = [], []
    for f in iter_files(run["targets"]):
        rel = str(f).replace(os.sep, "/")
        interesting = (f.suffix.lower() in LEDGER_EXT and not NON_PAGE.search(f.name)
                       and not re.search(r"/(package-lock|yarn\.lock|tsconfig)", rel))
        (keep if interesting else excluded).append(str(f.resolve()))
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
        raise SystemExit("No performance run is open. Start one:\n  %s start --target <path>" % TOOL)
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
    project = build_project(run)
    ctx = build_context(run, project)
    run["context"] = sorted(k for k, v in ctx.items() if v is True)
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
    return groups, ctx, project


def new_run(targets, title, kind="site", session=None, url=None):
    return {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session or session_id(),
        "kind": kind,
        "targets": [str(Path(t).expanduser().resolve()) for t in targets],
        "repo": target_root(targets) or repo_root(),
        "title": title,
        "url": url,
        "target_metric": None,
        "flags": [],
        "scope": None,
        "results": {},
        "touched": [],
        "measures": [],
        "parity": {"before": [], "after": []},
        "probe": {},
    }


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
    run = new_run(targets, title, kind, session)
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
    if run.get("url"):
        print("URL:     %s" % run["url"])
    else:
        print("URL:     none - the delivery gates cannot be read without one "
              "(scan --url https://<host>)")
    print("Found:   %s" % ", ".join(run.get("context", [])[:18]))
    print("%d gate(s) apply.\n" % len(run["order"]))
    current = None
    for gid in run["order"]:
        gate = index[gid]
        if gate["group"] != current:
            current = gate["group"]
            print("== %s" % current)
        auto = "auto" if gate["check"]["type"] != "manual" else "by hand"
        print("   %-8s [%s|%s] %s" % (gid, gate["severity"], auto, gate["title"]))
    print("\nBefore changing anything, take the baseline and the parity capture:\n"
          "  %s/psi.py measure --url <url> --label before\n"
          "  %s parity --phase before --file <capture.json>\n"
          "\nThen:\n  %s verify" % (SCRIPTS, TOOL, TOOL))


def cmd_start(argv):
    flags = parse_flags(argv)
    kind = (flags.get("kind") or ["site"])[0]
    if kind not in KINDS:
        raise SystemExit("kind must be one of: %s" % ", ".join(KINDS))
    here = os.getcwd()
    targets = flags.get("target") or [str(project_root(here) or here)]
    refuse_foreign_cwd(targets)
    run = new_run(targets, " ".join(flags.get("title", [])) or "performance run", kind,
                  url=(flags.get("url") or [None])[0])
    run["flags"] = flags.get("flag", [])
    run["scope"] = (flags.get("scope") or [None])[0]
    run["target_metric"] = (flags.get("metric") or [None])[0]
    groups, _, _ = refresh(run)
    save_run(run)
    print_checklist(run, groups,
                    header="Performance run %s opened - %s" % (run["id"], run["title"]))
    return 0


def cmd_scan(argv):
    run = load_run()
    flags = parse_flags(argv)
    if flags.get("kind"):
        run["kind"] = flags["kind"][0]
    if flags.get("target"):
        run["targets"] = [str(Path(t).expanduser().resolve()) for t in flags["target"]]
    if flags.get("url"):
        run["url"] = flags["url"][0]
        run["probe"] = {}
    if flags.get("metric"):
        metric = flags["metric"][0].lower()
        if metric not in GOOD:
            raise SystemExit("--metric must be one of: %s" % ", ".join(sorted(GOOD)))
        run["target_metric"] = metric
    if flags.get("flag"):
        run["flags"] = sorted(set(run.get("flags", []) + flags["flag"]))
    if flags.get("scope"):
        run["scope"] = flags["scope"][0] if flags["scope"][0] != "all" else None
    groups, _, _ = refresh(run)
    save_run(run)
    print_checklist(run, groups, header="Run %s rescoped" % run["id"])
    return 0


def _ago(seconds):
    for size, label in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return "%d %s%s" % (n, label, "" if n == 1 else "s")
    return "%d seconds" % seconds


def cmd_verify(argv):
    run = load_run()
    groups, ctx, project = refresh(run)
    index = gate_index(groups)
    cache = cache_asking(run, sweep_scope(project))
    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0, "manual": 0}
    carried = 0
    lines = []
    for gid in run["order"]:
        gate = index[gid]
        fn = CHECKS.get(gate["check"]["type"], "missing")
        if fn is None:
            counts["manual"] += 1
            continue
        if gate["check"]["type"] in SELF_SETTLING:
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
            status, detail = fn(gate, project, run, ctx)
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
    settle_self(run, index)
    save_run(run)
    cache_save(cache)
    stale = project.get("stale")
    if stale:
        print("The build output is %s behind the source. Gates that read what shipped are\n"
              "answering about a page nobody is served - build, then sweep again.\n"
              % _ago(stale))
    elif project["build"] is None:
        print("No build output found, so every gate that reads what shipped is inconclusive.\n"
              "Run the project's build first.\n")
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

# What lets a note count as evidence: a file name, a line reference, a number
# with or without units, or a quotation.
EVIDENCE = re.compile(r"[\w-]+\.[a-zA-Z]{2,4}\b|:\d+|\b\d+(\.\d+)?(px|rem|em|%|ms|s|ch|KB|MB|kb)?\b|[\"“‘']")


def _condition_still_holds(gate, ctx):
    when = gate.get("when", "always")
    for condition in (when if isinstance(when, list) else [when]):
        if condition != "always" and not ctx.get(condition, False):
            return False
    return True


def _inconclusive(run, gid):
    """Whether the sweep could not settle a gate, as against settling it against."""
    return str(run["results"].get(gid, {}).get("auto", "")).startswith("inconclusive")


def _validate_answer(gid, status, note, run, index, ctx=None):
    """Every reason one answer cannot stand, so a batch reports all of them at once."""
    faults = []
    if status not in ("pass", "fixed", "na", "disputed", "unmeasured"):
        faults.append("status must be pass, fixed, na, disputed, or unmeasured, not %r" % status)
    # A note is a record, not a check. What decides a gate is the sweep, so an answer
    # stands or falls on its status and the run's own state.
    if gid not in run["results"]:
        faults.append("not applicable to this run")
        return faults
    gate = index[gid]
    if gate["check"]["type"] in SELF_SETTLING:
        faults.append("answers to the run's own state, not to an attestation")
    # Parity is the invariant the whole checklist rests on. An attestation that the
    # page looks the same is exactly the thing the captures exist to replace.
    if gid.startswith("PAR-") and status in ("pass", "na"):
        faults.append("parity answers to the captures, not to an attestation - file a before "
                      "and an after capture and let the diff settle it")
    # A parity capture is a rendered reading, so a machine with nothing to render with
    # cannot take one. That is not a dispute - the check is right - and it is certainly
    # not a pass. `uninstrumented` records the absence on the run, and only then may a
    # parity gate be answered unmeasured. Without that marker this stays refused, so a
    # run that simply has not captured yet is still sent to capture rather than waved on.
    if status == "unmeasured" and not (gid.startswith("PAR-")
                                       and (_inconclusive(run, gid)
                                            or (run.get("uninstrumented")
                                                and not captures(run, "before")))):
        faults.append("unmeasured is only for a parity gate on a run marked uninstrumented "
                      "and holding no before capture. Record the absence first: "
                      "%s uninstrumented --reason \"<what is missing>\"" % TOOL)
    if status == "unmeasured" and len(note.strip()) < 40:
        faults.append("recording a gate as unmeasured takes the reason: which instrument was "
                      "missing, what it would have read, and why it could not be run")
    swept = str(run["results"][gid].get("auto", ""))
    failing = swept.startswith("fail")
    if status == "pass" and failing:
        faults.append("the sweep found this failing, so it cannot be answered as passing: %s"
                      % swept[:120])
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
    if status == "disputed" and not failing:
        faults.append("nothing to dispute - the sweep did not fail this gate")
    return faults


def _record(run, answers):
    for gid, status, note in answers:
        run["results"][gid].update({"status": status, "note": note, "at": now(), "by": "hand"})
    save_run(run)


def cmd_resolve_batch(source):
    """Answer many gates in one call, each with its own status and its own note.

    A run's wall-clock cost is round trips. The sweep settles what a script can
    settle in seconds; the rest is one exchange per gate unless the answers can
    travel together. Nothing is relaxed to do it: every answer meets the same
    conditions one at a time, and a batch carrying a single bad answer is refused
    whole rather than partly applied.
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
            ids.extend(t for t in re.split(r"[\s,]+", argv[i]) if t)
            i += 1
    if status not in ("pass", "fixed", "na", "disputed", "unmeasured"):
        raise SystemExit("--status must be pass, fixed, na, disputed, or unmeasured")
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
    _record(run, [(gid, status, note) for gid in ids])
    print("Answered %d gate(s) as %s: %s" % (len(ids), status, ", ".join(ids)))
    print("%d gate(s) still open."
          % len([g for g in run["order"] if run["results"][g]["status"] == "open"]))
    return 0


def cmd_status(argv):
    run = load_run()
    groups, _, _ = refresh(run)
    save_run(run)
    index = gate_index(groups)
    flags = parse_flags(argv)
    full = "full" in flags
    line = scope_line(run)
    if line:
        print(line)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    tally = {}
    for gid in run["order"]:
        s = run["results"][gid]["status"]
        tally[s] = tally.get(s, 0) + 1
    print("Run %s over %s" % (run["id"], ", ".join(run["targets"])))
    print("%d gate(s): %s\n" % (len(run["order"]),
                                ", ".join("%d %s" % (v, k) for k, v in sorted(tally.items()))))
    if not open_ids:
        print("Nothing open. Next:\n  %s report\n  %s finish" % (TOOL, TOOL))
        return 0
    current = None
    for gid in open_ids:
        gate = index[gid]
        if gate["group"] != current:
            current = gate["group"]
            print("== %s" % current)
        print("   %-8s %s" % (gid, gate["title"]))
        if full:
            print("        rule: %s" % gate["rule"])
            print("        fix:  %s" % gate.get("fix", ""))
        auto = run["results"][gid].get("auto")
        if auto:
            print("        sweep: %s" % auto[:300])
    print("\n%d gate(s) open." % len(open_ids))
    return 0


def cmd_routes(argv):
    run = load_run()
    _, _, project = refresh(run)
    save_run(run)
    if not project["routes"]:
        print("No routes found under %s" % ", ".join(run["targets"]))
        return 0
    print("%d route(s):\n" % len(project["routes"]))
    print("  %-52s %-12s %s" % ("file", "kind", "source"))
    for route in sorted(project["routes"], key=lambda r: r["rel"]):
        print("  %-52s %-12s %s" % (route["rel"][:52], route["kind"], kb(route["bytes"])))
    if project["assets"]:
        js = sum(m["bytes"] for m in project["assets"].values() if m["kind"] == "js")
        css = sum(m["bytes"] for m in project["assets"].values() if m["kind"] == "css")
        chunks = len([m for m in project["assets"].values() if m["kind"] == "js"])
        print("\nBuild at %s: %d JS chunk(s) %s raw, CSS %s raw"
              % (project["build"], chunks, kb(js), kb(css)))
        if project.get("stale"):
            print("That build is %s behind the source." % _ago(project["stale"]))
    else:
        print("\nNo build output found. Half the gates answer about what shipped rather than\n"
              "what was written, so run the project's build before verifying.")
    return 0


def cmd_files(argv):
    run = load_run()
    refresh(run)
    save_run(run)
    files = run.get("files") or {}
    unruled = sorted(p for p, r in files.items() if r["status"] == "open")
    print("%d file(s) in scope, %d excluded by type." % (len(files), len(run.get("excluded", []))))
    files_line = carried_files_line(run)
    if files_line:
        print(files_line.strip())
    if not unruled:
        print("Every file has been read and ruled on.")
        return 0
    print("\n%d still to rule on:" % len(unruled))
    for path in unruled[:200]:
        print("  %s %s" % ("read" if files[path]["read"] else "    ", path))
    print("\nChange each one, or clear it with a reason:\n"
          "  %s file-clear --batch - <<'JSON'\n"
          "  [{\"path\": \"<path>\", \"note\": \"<what it holds and why it needs no change>\"}]\n"
          "  JSON" % TOOL)
    return 0


def _clear_faults(path, note, run):
    faults = []
    resolved = str(Path(path).expanduser().resolve())
    if resolved not in (run.get("files") or {}):
        faults.append("not a file in this run's scope")
        return faults, resolved
    if len(note.strip()) < 12:
        faults.append("note must say what the file holds and why it needs no change")
    elif not EVIDENCE.search(note):
        faults.append("note carries no evidence - name what is in the file")
    if not run["files"][resolved]["read"]:
        faults.append("has not been read in this run - a file nobody opened and a file "
                      "considered and left alone are indistinguishable without that")
    return faults, resolved


def cmd_file_clear_batch(source):
    run = load_run()
    refresh(run)
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
        path, note = str(row.get("path", "")), str(row.get("note", ""))
        if not path:
            problems.append("row %d has no path" % n)
            continue
        faults, resolved = _clear_faults(path, note, run)
        if faults:
            problems.extend("%s: %s" % (base(path), f) for f in faults)
            continue
        cleared.append((resolved, note))
    if problems:
        raise SystemExit("Refused, and nothing was recorded - %d row(s) cannot stand:\n\n%s"
                         % (len(problems), "\n".join("  " + p for p in problems)))
    for resolved, note in cleared:
        run["files"][resolved].update({"status": "clear", "note": note, "at": now()})
    save_run(run)
    record_clears(run, cleared)
    print("Cleared %d file(s)." % len(cleared))
    print("%d still to rule on."
          % len([p for p, r in run["files"].items() if r["status"] == "open"]))
    return 0


def cmd_file_clear(argv):
    if "--batch" in argv:
        at = argv.index("--batch")
        if at + 1 >= len(argv):
            raise SystemExit("--batch takes a path, or - to read the JSON from stdin")
        return cmd_file_clear_batch(argv[at + 1])
    run = load_run()
    refresh(run)
    flags = parse_flags(argv)
    paths = flags.get("path") or []
    note = " ".join(flags.get("note", []))
    if not paths:
        raise SystemExit("file-clear takes --path <path> --note \"<reason>\", or --batch")
    problems, cleared = [], []
    for path in paths:
        faults, resolved = _clear_faults(path, note, run)
        problems.extend("%s: %s" % (base(path), f) for f in faults)
        if not faults:
            cleared.append(resolved)
    if problems:
        raise SystemExit("Refused:\n\n%s" % "\n".join("  " + p for p in problems))
    for resolved in cleared:
        run["files"][resolved].update({"status": "clear", "note": note, "at": now()})
    save_run(run)
    record_clears(run, [(resolved, note) for resolved in cleared])
    print("Cleared %d file(s)." % len(cleared))
    return 0


# ---------------------------------------------------------------- measurement and parity


def cmd_measure(argv):
    """File a measurement against the run, from a PSI or Lighthouse result.

    The numbers are the run's only defence against a change that felt right. Both
    tools write the same shape here, so a before taken one way and an after taken
    the other is refused by MSR-01 rather than averaged into a claim.
    """
    run = load_run()
    flags = parse_flags(argv)
    label = (flags.get("label") or ["before"])[0]
    if label not in ("before", "after"):
        raise SystemExit("--label must be before or after")
    source = (flags.get("file") or [None])[0]
    if not source:
        raise SystemExit("measure takes --file <result.json> from scripts/psi.py or "
                         "scripts/lighthouse.py, plus --label before|after")
    try:
        payload = json.loads(Path(source).expanduser().read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit("could not read %s: %s" % (source, exc))
    if "metrics" not in payload:
        raise SystemExit("%s is not a measurement written by scripts/psi.py or "
                         "scripts/lighthouse.py" % source)
    record = dict(payload["metrics"])
    record.update({"label": label, "at": now(), "tool": payload.get("tool", "unknown"),
                   "url": payload.get("url", ""), "strategy": payload.get("strategy", ""),
                   "field": payload.get("field") or {}})
    run.setdefault("measures", []).append(record)
    save_run(run)
    print("Recorded the %s measurement: %s %s %s" % (label, record["tool"], record["strategy"],
                                                     record["url"]))
    for key in ("score", "fcp", "lcp", "tbt", "cls", "si"):
        if key in record:
            print("  %-6s %s" % (key.upper(), record[key]))
    if record["field"]:
        print("  field: %s" % ", ".join("%s %s" % (k.upper(), v)
                                        for k, v in sorted(record["field"].items())))
    return 0


def _load_captures(source):
    payload = json.loads(Path(source).expanduser().read_text())
    if isinstance(payload, dict) and "captures" in payload:
        return payload["captures"]
    return payload if isinstance(payload, list) else [payload]


def cmd_uninstrumented(argv):
    """Record that this run has no rendering available to take a parity capture.

    A capture is a reading of the rendered page, so a machine with nothing to render
    with cannot take one. Left unrecorded, that is indistinguishable from a run that
    simply has not captured yet, and the two want opposite treatment: one should be
    sent to go and capture, the other has nowhere to be sent. Recording the absence
    here is what stands the edit guard down and lets the parity gates be answered
    unmeasured, and it is carried into the report so the gap is read rather than
    inferred.
    """
    flags = parse_flags(argv)
    reason = " ".join(flags.get("reason", [])).strip()
    if len(reason) < 20:
        raise SystemExit("--reason is required, and says what is missing: which engine, "
                         "and why it cannot be run here")
    run = load_run()
    if captures(run, "before"):
        raise SystemExit("this run already holds a before capture, so the reading was taken - "
                         "there is nothing uninstrumented about it")
    run["uninstrumented"] = {"at": now(), "reason": reason}
    save_run(run)
    print("Recorded: run %s has no rendering available.\n  %s\n\n"
          "The parity gates now take `unmeasured`, each with its own reason, and the report\n"
          "carries the absence. Nothing else in the run changes." % (run["id"], reason))
    return 0


def cmd_parity(argv):
    """File a rendered capture, or diff the two that are filed.

    The invariant this skill runs on is that a reader sees no difference. An
    attestation to that effect is worth nothing, so it is settled by comparing the
    page to itself.
    """
    run = load_run()
    flags = parse_flags(argv)
    if "diff" in flags:
        before, after, shared = paired_captures(run)
        print("before: %d capture(s), after: %d capture(s), %d comparable pair(s)"
              % (len(before), len(after), len(shared)))
        if not shared:
            print("Nothing to compare. File both phases at the same routes and widths.")
            return 1
        moved, missing = geometry_deltas(run)
        styles = style_deltas(run)
        for label, rows in (("gone", missing), ("moved", moved), ("restyled", styles)):
            print("\n%s: %d" % (label, len(rows)))
            for row in rows[:40]:
                print("  %s" % row)
        return 1 if (moved or missing or styles) else 0
    phase = (flags.get("phase") or [None])[0]
    source = (flags.get("file") or [None])[0]
    if phase not in ("before", "after") or not source:
        raise SystemExit("parity takes --phase before|after --file <capture.json>, "
                         "or --diff to compare what is filed")
    try:
        rows = _load_captures(source)
    except (OSError, ValueError) as exc:
        raise SystemExit("could not read %s: %s" % (source, exc))
    bad = [r for r in rows if not isinstance(r, dict) or "elements" not in r]
    if bad:
        raise SystemExit("%d capture(s) in %s carry no elements - the file must come from "
                         "scripts/parity-probe.js" % (len(bad), source))
    empty = [r for r in rows if not (r.get("elements") or [])]
    if empty:
        raise SystemExit("%d capture(s) in %s recorded no elements at all. A page that threw "
                         "on boot renders an empty root, and an empty capture diffs clean "
                         "against another empty one, so what it proves is nothing."
                         % (len(empty), source))
    store = run.setdefault("parity", {"before": [], "after": []})
    existing = {capture_key(c) for c in store.setdefault(phase, [])}
    added = 0
    for row in rows:
        if capture_key(row) in existing:
            store[phase] = [c for c in store[phase] if capture_key(c) != capture_key(row)]
        store[phase].append(row)
        added += 1
    save_run(run)
    print("Filed %d %s capture(s): %s"
          % (added, phase, ", ".join(sorted(capture_key(r) for r in rows))[:300]))
    counted = sum(len(r.get("elements") or []) for r in rows)
    print("%d element(s) recorded." % counted)
    if phase == "before":
        print("\nNothing may be edited until this is filed, and it is filed. Work the sweep:\n"
              "  %s verify" % TOOL)
    return 0


def cmd_budget(argv):
    run = load_run()
    _, _, project = refresh(run)
    save_run(run)
    assets = project["assets"]
    if not assets:
        print("No build output to weigh. Run the project's build first.")
        return 1
    rows = {}
    for path, meta in assets.items():
        rows.setdefault(meta["kind"], []).append((path, meta))
    print("Build at %s\n" % project["build"])
    total_raw = 0
    for kind in sorted(rows):
        items = sorted(rows[kind], key=lambda pair: -pair[1]["bytes"])
        raw = sum(m["bytes"] for _, m in items)
        total_raw += raw
        comp = sum(gzipped(p) for p, m in items) if kind in ("js", "css", "html") else 0
        print("%-6s %3d file(s)  %10s raw%s"
              % (kind, len(items), kb(raw), "  %10s compressed" % kb(comp) if comp else ""))
        for path, meta in items[:5]:
            print("        %-46s %10s" % (meta["rel"][:46], kb(meta["bytes"])))
    print("\ntotal %s raw" % kb(total_raw))
    if "write" in parse_flags(argv):
        out = Path(project["root"]) / "performance-budget.json"
        budget = {"recorded": now(), "url": run.get("url"),
                  "javascript_kb": round(sum(gzipped(p) for p, m in assets.items()
                                             if m["kind"] == "js") / 1024, 1),
                  "css_kb": round(sum(gzipped(p) for p, m in assets.items()
                                      if m["kind"] == "css") / 1024, 1),
                  "images_kb": round(sum(m["bytes"] for m in assets.values()
                                         if m["kind"] == "image") / 1024, 1),
                  "chunks": len([m for m in assets.values() if m["kind"] == "js"])}
        out.write_text(json.dumps(budget, indent=2) + "\n")
        print("\nRecorded as the ceiling in %s" % out)
    return 0


# ---------------------------------------------------------------- report and close


def _metric_table(run):
    before, after = measurements(run, "before"), measurements(run, "after")
    if not before and not after:
        return ["No measurement was recorded on this run.", ""]
    body = ["## Numbers", "",
            "| Metric | Before | After | Change |", "| --- | --- | --- | --- |"]
    a = before[-1] if before else {}
    b = after[-1] if after else {}
    for key, label in (("score", "Performance score"), ("fcp", "First Contentful Paint"),
                       ("lcp", "Largest Contentful Paint"), ("tbt", "Total Blocking Time"),
                       ("cls", "Cumulative Layout Shift"), ("si", "Speed Index")):
        if key not in a and key not in b:
            continue
        change = ""
        if _num(a.get(key)) and _num(b.get(key)):
            delta = float(b[key]) - float(a[key])
            better = delta > 0 if key == "score" else delta < 0
            change = "%+g %s" % (round(delta, 3), "better" if better else "worse")
        body.append("| %s | %s | %s | %s |" % (label, a.get(key, "-"), b.get(key, "-"), change))
    body.append("")
    field = (b.get("field") or a.get("field") or {})
    if field:
        body += ["Field record at the 75th percentile: %s."
                 % ", ".join("%s %s" % (k.upper(), v) for k, v in sorted(field.items())), ""]
    if run.get("target_metric"):
        body += ["This run set out to fix **%s**." % run["target_metric"].upper(), ""]
    return body


def _parity_section(run):
    before, after, shared = paired_captures(run)
    if not before and not after:
        return ["## Parity", "",
                "No capture was filed, so nothing proves the page renders as it did.", ""]
    moved, missing = geometry_deltas(run)
    styles = style_deltas(run)
    counted = sum(len(element_map(before[k])) for k in shared)
    body = ["## Parity", "",
            "%d element(s) across %d route and viewport pair(s) were compared before and "
            "after the work." % (counted, len(shared)), ""]
    if moved or missing or styles:
        body += ["%d moved, %d disappeared, %d changed computed style."
                 % (len(moved), len(missing), len(styles)), ""]
        for row in (missing + moved + styles)[:20]:
            body.append("- %s" % row)
    else:
        body += ["Nothing moved, nothing disappeared, and no computed style changed. "
                 "The page a reader sees is the page they saw.", ""]
    body.append("")
    return body


def cmd_report(argv):
    run = load_run()
    groups, _, _ = refresh(run)
    index = gate_index(groups)
    flags = parse_flags(argv)
    outstanding = [g for g in run["order"] if run["results"][g]["status"] == "open"
                   and index[g]["check"]["type"] not in SELF_SETTLING]
    if not outstanding:
        run["report_at"] = run.get("report_at") or now()
        settle_self(run, index)
        save_run(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    body = ["# Performance checklist - %s" % run["title"], "",
            "Run `%s`, kind `%s`, opened %s." % (run["id"], run["kind"], run["opened"]), ""]
    line = scope_line(run)
    if line:
        body += [line, ""]
    body += ["Targets: %s" % ", ".join("`%s`" % t for t in run["targets"]), ""]
    if run.get("url"):
        body += ["Measured: `%s`" % run["url"], ""]
    body += _metric_table(run)
    body += _parity_section(run)
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
                       "disputed": "DISPUTED", "unmeasured": "UNMEASURED",
                       "open": "OPEN"}.get(s, s))
        for s, n in sorted(tally.items()))), ""]
    disputed = [g for g in run["order"] if run["results"][g]["status"] == "disputed"]
    if disputed:
        body += ["### Disputed - the check was answered as wrong (%d)" % len(disputed), "",
                 "Each of these is a rule that did not get enforced this run. Read them first.", ""]
        for gid in disputed:
            body += ["- **%s %s**" % (gid, index[gid]["title"]),
                     "  - sweep said: %s" % str(run["results"][gid].get("auto", ""))[:200],
                     "  - the run's measurement: %s" % run["results"][gid]["note"]]
        body += [""]
    unmeasured = [g for g in run["order"] if run["results"][g]["status"] == "unmeasured"]
    if unmeasured:
        why = (run.get("uninstrumented") or {}).get("reason", "no reason recorded")
        body += ["### Unmeasured - no instrument to take the reading (%d)" % len(unmeasured), "",
                 "This run had no rendering available: %s. None of these is a pass - they "
                 "are the rules it could not enforce." % why, ""]
        for gid in unmeasured:
            body += ["- **%s %s**" % (gid, index[gid]["title"]),
                     "  - sweep said: %s" % str(run["results"][gid].get("auto", ""))[:200],
                     "  - why it went unread: %s" % run["results"][gid]["note"]]
        body += [""]
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
                "unmeasured": "UNMEASURED",
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
        touched = [t for t in (run.get("touched") or [])
                   if Path(t).exists() and differs_from_git(Path(t))]
        if touched:
            print("Cannot close as no-deliverable: this run edited %d file(s) - %s.\n"
                  "Work the checklist and report on it instead."
                  % (len(touched), ", ".join(base(t) for t in touched[:8])), file=sys.stderr)
            return 1
        run["closed"] = now()
        run["closed_reason"] = " ".join(flags.get("note", [])) or "no performance change was made"
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
            print("   %-8s [%s|%s|%s] %s" % (gate["id"], gate["severity"], gate["when"],
                                             gate["check"]["type"], gate["title"]))
    print("\n%d gate(s) in the registry." % total)
    return 0


def cmd_scopes(argv):
    for name_ in sorted(SCOPES):
        print("  %-9s %s" % (name_, SCOPES[name_][2]))
    print("\n  Narrow a run with --scope <name> on start or scan, and widen it again with\n"
          "  --scope all. Parity is never narrowed out: it answers for every change the run\n"
          "  made, whatever the run scoped to. Every report leads with what a scoped run\n"
          "  left out.")
    return 0


# ---------------------------------------------------------------- hooks


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
    if not (invokes(called, "perf-checklist") or invokes(called, "checklist")):
        return 0
    mine = session_id(payload)
    if session_pointer(mine).exists():
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    run = open_run([project_root(cwd) or cwd], "performance skill invocation", session=mine)
    print("A performance run (%s) was opened for this invocation.\n"
          "\n"
          "Scope it, then take the two things that have to exist before any edit:\n"
          "  %s scan --kind <%s> --target <path> --url https://<host>\n"
          "  %s/psi.py measure --url https://<host> --label before --out before.json\n"
          "  %s measure --file before.json --label before\n"
          "  %s parity --phase before --file <capture.json>\n"
          "\n"
          "Editing a source file before the parity capture is filed is refused: a capture\n"
          "taken afterwards records the state the work already produced."
          % (run["id"], TOOL, "|".join(KINDS), SCRIPTS, TOOL, TOOL), file=sys.stderr)
    return 0


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
    """PostToolUse(Write|Edit): record what an open run touched.

    A run opens on an invocation and on nothing else. Opening one because a
    stylesheet was edited puts a whole site's gates in front of a change nobody
    asked to have checked.
    """
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    path = (payload.get("tool_input") or {}).get("file_path")
    if not path:
        return 0
    run = load_run(required=False, session=session_id(payload), own_only=True)
    if run is None or run.get("closed"):
        return 0
    touched = run.setdefault("touched", [])
    resolved = str(Path(path).resolve())
    if resolved not in touched:
        touched.append(resolved)
        save_run(run)
    return 0


EDITABLE = MARKUP_EXT | CODE_EXT | STYLE_EXT


def cmd_guard_edit():
    """PreToolUse(Write|Edit): refuse a source edit before the baseline exists.

    PAR-01 and PRC-02 both ask for something taken before the work, and both are
    unanswerable after it. A rule saying to capture first is a reminder; refusing the
    first edit is the thing that makes the capture exist.
    """
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    path = (payload.get("tool_input") or {}).get("file_path")
    if not path or Path(path).suffix.lower() not in EDITABLE:
        return 0
    run = load_run(required=False, session=session_id(payload), own_only=True)
    if not run or run.get("closed"):
        return 0
    if not run_covers(run, repo_root(Path(path).parent)):
        return 0
    have_parity = bool(captures(run, "before")) or bool(run.get("uninstrumented"))
    have_measure = bool(measurements(run, "before"))
    if have_parity and have_measure:
        return 0
    missing = []
    if not have_measure:
        missing.append("no baseline measurement")
    if not have_parity:
        missing.append("no before capture")
    print("BLOCKED: performance run %s has %s.\n"
          "\n"
          "This checklist rests on the page rendering exactly as it did, and on the numbers\n"
          "moving. Neither can be established after the fact. Take both, then edit:\n"
          "\n"
          "  %s/psi.py measure --url %s --label before --out before.json\n"
          "  %s measure --file before.json --label before\n"
          "\n"
          "  Serve the current build, run scripts/parity-probe.js in the page, then:\n"
          "  %s parity --phase before --file <capture.json>\n"
          "\n"
          "A run that is not doing performance work closes with: %s finish --no-deliverable"
          % (run["id"], " and ".join(missing), SCRIPTS,
             run.get("url") or "https://<host>", TOOL, TOOL, TOOL), file=sys.stderr)
    return 2


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
    """Whether this run has anything to say about the tree being edited or landed.

    A tree that could not be resolved is not this run's tree. Reading it as every
    tree is how an open run over one project refuses a landing in another.
    """
    if not root:
        return False
    if run.get("repo") and Path(run["repo"]).resolve() == Path(root).resolve():
        return True
    here = Path(root).resolve()
    return any(here == Path(t).resolve() or here in Path(t).resolve().parents
               or Path(t).resolve() in here.parents for t in run.get("targets", []))


def cmd_guard_stop():
    """Stop hook: refuse to end a session that left its own performance run open."""
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
    print("Performance run %s over %s is still open - %s\n"
          "\n"
          "This is a build checklist. Change the project until the sweep passes, prove the\n"
          "page still renders as it did, then:\n"
          "  %s verify\n"
          "  %s parity --phase after --file <capture.json>\n"
          "  %s resolve <ID> --status fixed --note \"<what changed>\"\n"
          "  %s report\n"
          "  %s finish\n"
          "\n"
          "A run that changed nothing closes with: %s finish --no-deliverable"
          % (run["id"], ", ".join(run["targets"]), detail, TOOL, TOOL, TOOL, TOOL, TOOL, TOOL),
          file=sys.stderr)
    return 2


LANDING = re.compile(r"\bgit\s+commit\b|\bgh\s+pr\s+create\b")


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
    """PreToolUse(Bash): refuse to land work while the calling session's run is open."""
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
    parity_open = [g for g in open_ids if g.startswith("PAR-")]
    extra = ("\n%d parity gate(s) are unanswered, so nothing has established that the page "
             "still renders as it did.\n" % len(parity_open)) if parity_open else ""
    print("BLOCKED: performance run %s is still open over %s.\n"
          "\n"
          "%d gate(s) unanswered: %s\n"
          "%s\n"
          "Landing now ships changes the checklist has not been through. Work it, then land:\n"
          "  %s verify\n"
          "  %s report\n"
          "  %s finish"
          % (run["id"], ", ".join(run["targets"]), len(open_ids), ", ".join(open_ids[:12]),
             extra, TOOL, TOOL, TOOL), file=sys.stderr)
    return 2


# ------------------------------------------------------------------ repair

# The engine is loaded by path as often as by name, so its own directory is
# not on the path by the time this runs.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_fixes  # noqa: E402

# Only faults with one correct form appear here. Which image is the hero, and
# where a bundle should split, are decisions; nothing in this file guesses one.
FIXERS = {
    "thirdparty_defer": perf_fixes.fix_thirdparty_defer,
    "font_display": perf_fixes.fix_font_display,
    "image_dimensions": perf_fixes.fix_image_dimensions,
}


def cmd_fix(argv):
    """Apply every repair the failing gates have, then re-sweep to prove it landed."""
    run = load_run()
    groups, ctx, project = refresh(run)
    index = gate_index(groups)
    applied, declined = [], []
    for gid in run["order"]:
        gate = index[gid]
        kind = gate["check"]["type"]
        fixer, fn = FIXERS.get(kind), CHECKS.get(kind)
        if not fixer or fn is None:
            continue
        status, _ = fn(gate, project, run, ctx)
        if status != FAIL:
            continue
        changed = fixer(gate, project, run, ctx) or []
        (applied if changed else declined).append(
            (gid, gate["title"], changed) if changed else gid)
    if not applied:
        if declined:
            print("No repair landed. %d failing gate(s) have a fixer that declined:\n  %s"
                  % (len(declined), ", ".join(declined)))
        else:
            print("Nothing to repair: no failing gate has a mechanical fix.")
        return 0
    for gid, title, changed in applied:
        print("  fixed %-8s %-38s %s" % (gid, title[:38], ", ".join(changed[:5])))
    if declined:
        print("  declined %s" % ", ".join(declined))
    print("\n%d gate(s) repaired. Re-sweeping." % len(applied))
    return cmd_verify([])


COMMANDS = {
    "start": cmd_start, "scan": cmd_scan, "verify": cmd_verify, "resolve": cmd_resolve,
    "status": cmd_status, "report": cmd_report, "finish": cmd_finish, "gates": cmd_gates,
    "routes": cmd_routes, "files": cmd_files, "file-clear": cmd_file_clear,
    "measure": cmd_measure, "parity": cmd_parity, "budget": cmd_budget, "scopes": cmd_scopes,
    "uninstrumented": cmd_uninstrumented, "fix": cmd_fix,
}
HOOKS = {
    "hook-read": cmd_hook_read, "hook-edit": cmd_hook_edit, "hook-skill": cmd_hook_skill,
    "guard-stop": cmd_guard_stop, "guard-land": cmd_guard_land, "guard-edit": cmd_guard_edit,
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
