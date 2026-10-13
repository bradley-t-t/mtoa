#!/usr/bin/env python3
"""Drives the wiring rules into a project, so every one of them is verified rather than assumed.

This is a build checklist, not an audit. A gate that is not met is a setting to
change, a key to move, a policy to write, or a service to wake up; the only ways
past one are making that change or naming the condition that puts the gate out of
scope. A gate the sweep failed cannot be recorded as passing, and a gate marked
fixed reopens if the next sweep still fails it.

It asks one question and leaves the neighbouring ones alone: is the thing wired up
and alive. How a page looks belongs to the design checklist, whether anybody finds
it to the SEO checklist, how long it takes to arrive to the performance checklist.
A finding from this run that belongs to one of those is delegated by name and
carried in the report, never resolved here and never dropped.

Two things separate it from those three. It needs credentials at the point of use,
so the probes read them from the environment and this tool never stores one. And a
large part of what it checks cannot be read out of source at all - a paused
project, a development key in production, a webhook nobody registered - so those
gates answer to a request that was actually made, and refuse an attestation.

    start     open a run and print the gates it must satisfy
    scan      re-derive what the project contains, and therefore which gates apply
    services  what the project talks to, and what the probe found for each
    probe     file a probe result taken by scripts/probe.py
    verify    run every automated check and record its verdict
    resolve   answer a gate: pass, fixed, n/a or disputed, with the evidence
              --batch <path|-> answers many at once, a status and a note each
    delegate  hand a finding to the checklist that owns it
    status    what is still outstanding; --full adds each gate's rule and fix
    report    render the filled checklist
    finish    close the run - refuses while anything is unanswered
    gates     print the registry, whole or filtered
    files     the file ledger: what is still to be read and ruled on
    file-clear  rule a file as needing no wiring change, with the reason
    scopes    the named scopes a run can be narrowed to

    guard-stop   Stop hook: refuse to end a session with a run still open
    guard-land   PreToolUse hook: refuse `git commit` / `gh pr create` mid-run
    hook-skill   PostToolUse hook: open a run when the skill is invoked
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
    ENGINE = HOME / ".claude" / "checklists" / "stack"
GATES_DIR = ENGINE / "checklist" / "gates"
SKILL = ENGINE
PROBE_TOOL = str(SKILL / "scripts" / "probe.py")

TOOL = str(Path(__file__).resolve())


# ---------------------------------------------------------------- the profile
#
# Whose repositories these are, and the agency bar every client site carries,
# are the owner's facts rather than this engine's. They are read from the
# profile through tools/mtoa_config.py: the copy in the tree this file sits in
# first, then the installed one. With neither, every setting reads as unset and
# whatever it drives stands down.


class _Unset:
    """No profile reader anywhere: every setting is unset."""

    @staticmethod
    def get(key, default=None):
        return default

    @staticmethod
    def owners():
        return ()

    @staticmethod
    def render(text, extra=None):
        for name, value in (extra or {}).items():
            text = text.replace("{%s}" % name, str(value))
        return text


_PROFILE = None


def _profile():
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = _Unset
        here = Path(__file__).resolve()
        tree = [here.parents[3]] if len(here.parents) > 3 else []
        for root in tree + [HOME / ".claude"]:
            path = root / "tools" / "mtoa_config.py"
            if not path.is_file():
                continue
            try:
                spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception:
                continue
            _PROFILE = module
            break
    return _PROFILE


def setting(key, default=None):
    """One value from the owner's profile by dotted key."""
    return _profile().get(key, default)


def owned(slug):
    """Whether a slug sits under one of the GitHub owners the profile names."""
    prefixes = tuple(p.lower() for p in _profile().owners())
    return bool(slug) and bool(prefixes) and slug.lower().startswith(prefixes)


def agency_bar():
    """The bar every client site carries, as the profile describes it, or {}."""
    bar = setting("agency.bar") or {}
    return bar if isinstance(bar, dict) else {}


def profile_words():
    """The names gate text carries as placeholders, filled from the profile."""
    bar = agency_bar()
    return {
        "agency": setting("agency.name") or "agency",
        "agency_domain": setting("agency.domain") or "the agency's site",
        "agency_bar": bar.get("name") or "agency bar",
        "agency_bar_component": bar.get("component_name") or "shared",
        "agency_bar_accent": bar.get("accent_var") or "the bar's accent property",
    }


# Conditions the profile decides rather than the project. A gate carrying one
# stands down for anybody whose profile does not set it. It is not something the
# project contains, so the context a run reports leaves it out, and so do the
# messages naming a gate's conditions wherever the profile meets it.
PROFILE_CONDITIONS = {
    "agency_bar": "agency bar (agency.bar)",
    "agency_status": "agency status feed (agency.status_feed)",
    "ticket_queue": "ticket queue (github.tickets)",
}


def profile_condition(name):
    """Whether the profile sets up what a profile condition names."""
    if name == "agency_bar":
        return bar_marker() is not None
    if name == "agency_status":
        return bool(setting("agency.status_feed"))
    if name == "ticket_queue":
        return bool(setting("github.tickets"))
    return False


def project_when(gate):
    """A gate's conditions as the project answers them: the profile's are left
    out where the profile meets them, and named where it does not."""
    when = gate.get("when", "always")
    if not isinstance(when, list) or not any(c in PROFILE_CONDITIONS for c in when):
        return when
    kept = [c for c in when if c not in PROFILE_CONDITIONS or not profile_condition(c)]
    return kept if len(kept) > 1 else (kept[0] if kept else "always")


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
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return Path.home() / ".pass-state"
    return HOME / ".claude" / "state"


RUNS = _state_root() / "stack-runs"
CURRENT = RUNS / "current.json"

KINDS = ["site", "app", "api", "tool", "library"]
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
TARGET_NOUN = "project"

# Gates whose verdict comes from the run's own bookkeeping rather than from the
# project, so an attestation about them means nothing.
SELF_SETTLING = {"run_opened", "all_resolved", "report_emitted", "file_coverage",
                 "scope_coverage", "probe_filed", "delegation"}


def session_id(payload=None):
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
    return CURRENT if not session else RUNS / ("current-%s%s.json" % (session, workspace_suffix()))


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


# ---------------------------------------------------------------- the tree

SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro",
              ".html", ".json", ".yml", ".yaml", ".toml", ".sql", ".css", ".md", ".sh"}

# The sweep reads the whole tree, and every gate here asks about something a browser
# is served or a server runs. A README naming a thing, a lockfile pinning it and a
# workflow installing it are writing about the code rather than the code, so a check
# that reads them reports the writing as the defect - and where a match is what
# closes a gate, certifies a project by the sentence describing what it lacks. This
# is the set a check reads unless it says otherwise; SOURCE_EXT is how one asks for
# the rest of the tree.
CODE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro",
            ".html", ".css"}

# What the project declares about itself, as opposed to what it does. Read for a
# dependency or a setting, never for a defect in the code.
MANIFEST_EXT = {".json", ".yml", ".yaml", ".toml"}

SKIP_DIRS = {"node_modules", ".git", ".next", ".nuxt", ".svelte-kit", "out", "vendor",
             "__pycache__", ".venv", "coverage", ".turbo", ".cache", ".intelligence",
             ".astro", "storybook-static", ".output", ".idea", ".vscode", ".fleet",
             ".gradle", ".pnpm-store"}

# The build output is read on purpose - ENV-03 asks what survived into the bundle,
# which is a different question from what the source says.
BUILD_DIRS = ("dist", "build", ".next/static", ".output/public", "out")

# Files that carry no wiring decision. Judging them holds a run open on assets.
BINARY_LIKE = re.compile(r"\.(png|jpe?g|gif|webp|avif|ico|svg|woff2?|ttf|otf|eot|mp4|"
                         r"webm|mp3|wav|pdf|zip|gz|lock)$", re.I)


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


def iter_files(targets, exts=SOURCE_EXT, skip_build=True):
    seen = []
    for target in targets:
        root = Path(target)
        if root.is_file():
            seen.append(root)
            continue
        every = sorted(root.rglob("*"))
        # What git ignores is build output or local state. The skip list below
        # cannot keep up with what each project names its build.
        ignored = _ignored_paths(root, [p for p in every if p.is_file()]) if skip_build else set()
        for path in every:
            if str(path) in ignored:
                continue
            parts = set(path.parts)
            if parts & SKIP_DIRS:
                continue
            if skip_build and any(str(path).startswith(str(root / b)) for b in BUILD_DIRS):
                continue
            if path.is_file() and path.suffix.lower() in exts:
                seen.append(path)
    return seen


def project_root(path):
    here = Path(path).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "package.json").is_file() or (candidate / ".git").is_dir():
            return candidate
    return here


def repo_root(start=None):
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start or os.getcwd(),
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def git(root, *args, timeout=20):
    """One git call, returning its stdout or an empty string. Never raises."""
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                             timeout=timeout)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def differs_from_git(path):
    root = repo_root(str(Path(path).parent))
    if not root:
        return True
    rel = str(Path(path).resolve().relative_to(Path(root).resolve()))
    return bool(git(root, "diff", "--", rel) or git(root, "diff", "--cached", "--", rel)
                or git(root, "ls-files", "--others", "--exclude-standard", "--", rel))


def read(path, limit=400000):
    try:
        return Path(path).read_text(errors="replace")[:limit]
    except OSError:
        return ""


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def base(path):
    return Path(path).name


# ---------------------------------------------------------------- the registry


def load_gates():
    groups = []
    # The agency's names reach gate text as {agency}-style placeholders, filled
    # here so every reader of a gate - status, report, the registry - sees words.
    words = profile_words()
    render = _profile().render
    for path in sorted(GATES_DIR.glob("*.json")):
        raw = json.loads(path.read_text())
        defaults = raw.get("defaults", {})
        gates = []
        for gate in raw["gates"]:
            merged = dict(defaults)
            merged.update(gate)
            for field in ("title", "rule", "fix"):
                if isinstance(merged.get(field), str):
                    merged[field] = render(merged[field], words)
            merged.setdefault("check", {"type": "manual"})
            merged["group"] = raw["group"]
            merged["prefix"] = raw["prefix"]
            merged.setdefault("kinds", ["*"])
            merged.setdefault("when", "always")
            merged.setdefault("severity", "required")
            # A gate with nothing behind it records whatever it is told and enforces
            # nothing. The rule stays in the skill's prose; it does not sit in a
            # checklist as though a machine were watching it.
            kind = merged["check"].get("type")
            if CHECKS.get(kind) is None and kind not in SELF_SETTLING:
                continue
            gates.append(merged)
        groups.append({"group": raw["group"], "prefix": raw["prefix"],
                       "description": render(raw["description"], words), "gates": gates})
    if not groups:
        raise SystemExit("no gates found under %s - the skill is installed without its "
                         "checklist directory" % GATES_DIR)
    return groups


def all_gates(groups):
    return [gate for group in groups for gate in group["gates"]]


def gate_index(groups):
    return {gate["id"]: gate for gate in all_gates(groups)}


# A scope narrows a run to one part of the question. Each names the prefixes it keeps
# and the sentence a report leads with, so a narrowed run says so rather than reading
# as a whole one.
SCOPES = {
    "services": (("SVC",), "the live services", "Supabase, Clerk, Stripe and Vercel only"),
    "env":      (("ENV",), "environment variables", "the variables and what can reach them"),
    "chrome":   (("CHR",), "brand chrome", "the footer bar and the icon set"),
    "tooling":  (("TLG",), "tooling", "formatter, linter, types, lockfile"),
    "version":  (("VER",), "versioning", "CalVer and the copies that must agree"),
    "repo":     (("REP",), "repository compliance", "branches, protection, workflow"),
    "deps":     (("DEP",), "dependencies", "what is installed and what is used"),
    "auth":     (("AUT",), "auth wiring", "middleware, sessions and row policies"),
    "observe":  (("OBS",), "observability", "reporting, monitoring and the status board"),
    "live":     (("LIV",), "the live page", "what the deployed page does when it loads"),
    "legal":    (("LEG",), "the legal surface", "privacy, terms, contact and consent"),
}


def in_scope(gate, scope):
    if not scope:
        return True
    keep = SCOPES.get(scope)
    if not keep:
        return True
    return gate["prefix"] in keep[0] or gate["prefix"] == "PRC"


def _scope_flag(flags):
    """The scope a run was started with, with `all` reading as no scope at all.

    `--scope all` is the way a person says they mean the whole question, and it
    reached the run as the literal string `all`. Nothing in SCOPES answers to
    that, so `in_scope` waved every gate through and the run behaved correctly -
    but `scope_line` printed nothing, so a run narrowed to everything and a run
    narrowed to nothing were the same run from the outside. Clearing it here
    makes `all` mean what it says rather than reading as a narrowing that got
    lost.
    """
    named = (flags.get("scope") or [None])[0]
    return None if named == "all" else named


def scope_line(run):
    scope = run.get("scope")
    if not scope or scope not in SCOPES:
        return ""
    return ("Scoped to %s - %s. Everything else this project owes is outside this run."
            % (SCOPES[scope][1], SCOPES[scope][2]))


# ---------------------------------------------------------------- the project


SERVICE_MARKERS = {
    # Not a bare supabase.co: every site here loads analytics from an edge
    # function on one, and calling a service is not running one.
    "supabase": re.compile(r"@supabase/|createClient\(|SUPABASE_"
                           r"|supabase/config\.toml", re.I),
    "clerk": re.compile(r"@clerk/|CLERK_|clerk\.(dev|com|accounts)", re.I),
    "stripe": re.compile(r"\bstripe\b|STRIPE_|pk_(test|live)_|price_[A-Za-z0-9]", re.I),
    "vercel": re.compile(r"vercel\.json|@vercel/|VERCEL_", re.I),
}

ENV_REF = re.compile(r"(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]{2,})"
                     r"|(?:process\.env|import\.meta\.env)\[['\"]([A-Z][A-Z0-9_]{2,})['\"]\]")

BUILTIN_ENV = frozenset({"DEV", "PROD", "MODE", "SSR", "BASE_URL", "NODE_ENV"})

PUBLIC_PREFIX = re.compile(r"^(VITE_|NEXT_PUBLIC_|PUBLIC_|REACT_APP_|NUXT_PUBLIC_)")

# What a secret looks like once it is a value rather than a name. Shapes rather than
# names, because the next one to leak arrives under a name nobody thought to list.
SECRET_SHAPE = re.compile(
    r"sk_live_[A-Za-z0-9]{10,}|sk_test_[A-Za-z0-9]{10,}|rk_live_[A-Za-z0-9]{10,}"
    r"|whsec_[A-Za-z0-9]{10,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|sb_secret_[A-Za-z0-9_-]{10,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}")

# A name that says the value behind it must never reach a browser.
SECRET_NAME = re.compile(
    r"SERVICE_ROLE|SECRET|PRIVATE_KEY|_TOKEN$|PASSWORD|^SK_|_SK$|WEBHOOK_SECRET|MASTER_KEY",
    re.I)


def build_project(run):
    """Everything the checks read, gathered once."""
    targets = [Path(t) for t in run["targets"]]
    root = Path(run.get("repo") or targets[0])
    if root.is_file():
        root = root.parent
    files = iter_files(targets)
    sources = []
    for path in files:
        if BINARY_LIKE.search(path.name):
            continue
        sources.append((path, read(path)))
    blob = "\n".join(text for path, text in sources if path.suffix.lower() in CODE_EXT)
    manifest = "\n".join(text for path, text in sources
                         if path.suffix.lower() in MANIFEST_EXT)
    package = read_json(root / "package.json") or {}
    tsconfig = read_json(root / "tsconfig.json")
    ci = read(root / ".github" / "workflows" / "ci.yml")
    built = []
    for name in BUILD_DIRS:
        out = root / name
        if out.is_dir():
            built.extend(iter_files([out], exts={".js", ".mjs", ".cjs", ".css", ".html", ".json"},
                                    skip_build=False))
    env_names = set()
    for match in ENV_REF.finditer(blob):
        name = match.group(1) or match.group(2)
        if name not in BUILTIN_ENV:
            env_names.add(name)
    return {
        "root": root,
        "targets": targets,
        "files": files,
        "sources": sources,
        "blob": blob,
        "manifest": manifest,
        "package": package,
        "tsconfig": tsconfig,
        "ci": ci,
        "built": built,
        "env_names": sorted(env_names),
        "run": run,
    }


def owner_repo(root):
    """The owner/name this checkout pushes to, or None."""
    url = git(str(root), "remote", "get-url", "origin").strip()
    if not url:
        return None
    match = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", url)
    return "%s/%s" % (match.group(1), match.group(2)) if match else None


def build_context(run, project):
    root, blob, package = project["root"], project["blob"], project["package"]
    deps = dict(package.get("dependencies") or {})
    deps.update(package.get("devDependencies") or {})
    names = " ".join(deps)
    slug = owner_repo(root) or ""
    branch = git(str(root), "rev-parse", "--abbrev-ref", "HEAD").strip()
    ctx = {
        "always": True,
        "node": bool(package),
        "typescript": bool(project["tsconfig"]) or "typescript" in deps,
        "framework": any(k in deps for k in ("react", "next", "vue", "svelte", "astro", "nuxt")),
        "pages": any(p.suffix.lower() in {".html", ".jsx", ".tsx", ".vue", ".svelte", ".astro"}
                     for p in project["files"]),
        "env_refs": bool(project["env_names"]),
        "git": bool(repo_root(str(root))),
        "owned_repo": owned(slug),
        "branch_work": bool(branch) and branch not in ("main", "master", "develop", "HEAD"),
        "release_tool": bool((root / "scripts" / "calver.js").is_file()
                             or (package.get("scripts") or {}).get("release")
                             or (package.get("scripts") or {}).get("version")),
        # The declared dependencies beside the code: an analytics client is a package
        # a project installs as often as a script tag it writes.
        "analytics": bool(re.search(r"gtag\(|googletagmanager|plausible|umami|posthog|"
                                    r"@vercel/analytics|mixpanel", names + "\n" + blob, re.I)),
        "auth": False,
        "public_site": False,
        "deployed": bool(run.get("url")),
        # What the profile sets up rather than what the tree holds: the bar every
        # client site carries, the status feed its board reads, the ticket queue a
        # report lands in. Unset, the gates that answer to them stand down.
        **{name: profile_condition(name) for name in PROFILE_CONDITIONS},
    }
    for service, marker in SERVICE_MARKERS.items():
        ctx[service] = bool(marker.search(names) or marker.search(blob))
    ctx["vercel"] = ctx["vercel"] or (root / "vercel.json").is_file() or (root / ".vercel").is_dir()
    # The CLI config on disk is the project owning one, whatever the code says.
    ctx["supabase"] = ctx["supabase"] or (root / "supabase" / "config.toml").is_file()
    ctx["supabase_migrations"] = (root / "supabase" / "migrations").is_dir()
    ctx["supabase_functions"] = (root / "supabase" / "functions").is_dir()
    ctx["supabase_storage"] = bool(re.search(r"storage\s*\.\s*from\(|createBucket|"
                                             r"supabase/storage", blob, re.I))
    ctx["supabase_auth"] = bool(re.search(r"auth\.(getUser|getSession|getClaims|signIn)",
                                          blob, re.I))
    # svix arrives as a dependency and is used through an import the code names, so
    # the declared names count here alongside the code.
    ctx["clerk_webhook"] = bool(re.search(r"svix|CLERK_WEBHOOK|clerk.*webhook",
                                          names + "\n" + blob, re.I))
    ctx["stripe_prices"] = bool(re.search(r"price_[A-Za-z0-9]{6,}", blob))
    ctx["auth"] = ctx["clerk"] or ctx["supabase_auth"]
    # Middleware, route handlers and server components only exist where something
    # serves the request. A static bundle has nowhere to put them.
    ctx["server_routes"] = bool(
        any(k in (package.get("dependencies") or {}) for k in ("next", "nuxt", "@remix-run/node"))
        or (root / "src" / "routes").is_dir() and (root / "svelte.config.js").is_file()
        or (root / "middleware.ts").is_file() or (root / "middleware.js").is_file()
        or (root / "src" / "middleware.ts").is_file())
    # Whether the thing has visitors cannot be read out of a tree: an internal tool and
    # a public site are the same React application. The flag says which.
    ctx["public_site"] = ctx["pages"] and "app" not in (run.get("flags") or [])
    for flag in run.get("flags") or []:
        key = flag.replace("-", "_")
        if key in ("payments", "auth", "public_site"):
            ctx[key] = True
        if key == "app":
            ctx["public_site"] = False
    if ctx["payments"] if "payments" in ctx else False:
        ctx["stripe"] = True
    ctx.setdefault("payments", ctx.get("stripe", False))
    return ctx


def applicable(gate, run, ctx):
    if not in_scope(gate, run.get("scope")):
        return False, "outside the run's scope"
    kinds = gate.get("kinds", ["*"])
    if "*" not in kinds and run.get("kind") not in kinds:
        return False, "does not apply to a %s" % run.get("kind")
    when = gate.get("when", "always")
    for condition in (when if isinstance(when, list) else [when]):
        if not ctx.get(condition, False):
            if condition in PROFILE_CONDITIONS:
                return False, "the profile names no %s" % PROFILE_CONDITIONS[condition]
            return False, "the project has no %s" % condition
    return True, ""


# ---------------------------------------------------------------- checks
#
# Every check answers (status, detail). The detail is what the run reads to know
# what to change, so it names files and lines rather than restating the rule.


def rel(project, path):
    try:
        return str(Path(path).resolve().relative_to(project["root"].resolve()))
    except ValueError:
        return str(path)


def hits(project, pattern, exts=CODE_EXT, where=None):
    """Every (file, line number, line) a pattern matches, across the run's code.

    The default is the code rather than the sweep. A check written against the
    sweep reads documentation and manifests as if a browser were served them,
    which is how a README's badge colours became the footer's palette. A check
    that genuinely wants the rest of the tree passes exts=SOURCE_EXT and says so.
    """
    found = []
    for path, text in project["sources"]:
        if exts and path.suffix.lower() not in exts:
            continue
        if where and not where(path):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                found.append((rel(project, path), n, line.strip()[:120]))
    return found


def named(rows, limit=6):
    return ", ".join("%s:%d" % (r[0], r[1]) for r in rows[:limit]) + (
        " and %d more" % (len(rows) - limit) if len(rows) > limit else "")


def verdict(ok, good, bad):
    return (PASS, good) if ok else (FAIL, bad)


def check_probe(gate, project, run, ctx):
    """A gate settled by a request that was actually made."""
    name = gate["check"].get("probe", "")
    filed = (run.get("probe") or {}).get(name)
    if not filed:
        return UNKNOWN, ("no probe result filed for %s - run scripts/probe.py and file it "
                         "with `%s probe --file <result.json>`" % (name, TOOL))
    if filed.get("ok"):
        return PASS, "%s (probed %s)" % (filed.get("detail", "answered"), filed.get("at", ""))
    return FAIL, "%s (probed %s)" % (filed.get("detail", "did not answer"), filed.get("at", ""))


def check_vercel_linked(gate, project, run, ctx):
    # Either shape the CLI writes: project.json for a linked directory,
    # repo.json for a linked git repository, which is what a checkout is.
    link = read_json(project["root"] / ".vercel" / "project.json")
    if not link:
        repo = read_json(project["root"] / ".vercel" / "repo.json") or {}
        for entry in repo.get("projects") or []:
            if entry.get("directory") in (".", "", None):
                link = {"projectId": entry.get("id"), "orgId": entry.get("orgId"),
                        "projectName": entry.get("name")}
                break
    if not link:
        return FAIL, ("no .vercel/project.json or .vercel/repo.json - this checkout is not "
                      "linked to a Vercel project, so nothing here says where it deploys")
    name = link.get("projectName") or link.get("projectId", "")
    return PASS, "linked to Vercel project %s" % name


# ---- environment


def check_public_prefix_secrets(gate, project, run, ctx):
    leaked = [n for n in project["env_names"]
              if PUBLIC_PREFIX.match(n) and SECRET_NAME.search(PUBLIC_PREFIX.sub("", n))]
    example = project["root"] / ".env.example"
    if example.is_file():
        for line in read(example).splitlines():
            key = line.split("=", 1)[0].strip()
            if key and PUBLIC_PREFIX.match(key) and SECRET_NAME.search(PUBLIC_PREFIX.sub("", key)):
                leaked.append(key)
    return verdict(not leaked,
                   "no secret-shaped name carries a public prefix",
                   "these are inlined into the client by the prefix alone: %s"
                   % ", ".join(sorted(set(leaked))))


def check_bundle_secrets(gate, project, run, ctx):
    if not project["built"]:
        return UNKNOWN, ("no build output found under %s - build the project, then verify "
                         "again. This gate reads the bundle, not the source."
                         % ", ".join(BUILD_DIRS))
    found = []
    for path in project["built"]:
        text = read(path, limit=4000000)
        for match in SECRET_SHAPE.finditer(text):
            found.append((rel(project, path), match.group(0)[:12] + "..."))
    if found:
        where = ", ".join("%s (%s)" % (f, s) for f, s in found[:6])
        return FAIL, ("%d secret-shaped value(s) survived into the built client: %s. "
                      "Rotate anything already deployed." % (len(found), where))
    return PASS, "%d built file(s) carry no key shape" % len(project["built"])


def check_env_example(gate, project, run, ctx):
    example = None
    for name in (".env.example", ".env.sample", ".env.template"):
        if (project["root"] / name).is_file():
            example = project["root"] / name
            break
    if not example:
        # A project that reads no variable has nothing for the file to describe, and an
        # empty .env.example teaches a fresh clone nothing it did not already know.
        if not project["env_names"]:
            return PASS, "the code reads no environment variable, so there is nothing to list"
        return FAIL, ("no .env.example - a fresh clone has nothing describing the %d variable(s) "
                      "the code reads" % len(project["env_names"]))
    listed, valued = set(), []
    for line in read(example).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        listed.add(key.strip())
        if value.strip().strip("\"'"):
            valued.append(key.strip())
    missing = sorted(set(project["env_names"]) - listed)
    faults = []
    if missing:
        faults.append("%d variable(s) the code reads are not listed: %s"
                      % (len(missing), ", ".join(missing[:10])))
    if valued:
        faults.append("%d entr(y/ies) carry a value: %s" % (len(valued), ", ".join(valued[:8])))
    return verdict(not faults, "%s lists all %d variable(s), no values"
                   % (base(example), len(listed)), "; ".join(faults))


def check_env_committed(gate, project, run, ctx):
    root = str(project["root"])
    tracked = [f for f in git(root, "ls-files").splitlines()
               if re.match(r"(^|.*/)\.env(\.|$)", f)
               and not re.search(r"\.(example|sample|template)$", f)]
    if tracked:
        return FAIL, ("%d env file(s) are tracked by git: %s. Untrack them and rotate every "
                      "value they carried - removing it in a later commit does not unpublish it."
                      % (len(tracked), ", ".join(tracked)))
    history = git(root, "log", "--all", "--diff-filter=A", "--name-only", "--pretty=format:",
                  "--", "*.env", ".env.*", timeout=30)
    past = sorted({f for f in history.splitlines()
                   if f and not re.search(r"\.(example|sample|template)$", f)})
    if past:
        return FAIL, ("no env file is tracked now, but %d appear(s) in the history: %s. Those "
                      "values are published; rotate them." % (len(past), ", ".join(past[:5])))
    return PASS, "no env file is tracked, and none appears in the history"


# ---- brand chrome


def bar_packages():
    """The packages the shared bar is published as, in the profile's order."""
    return [p for p in (agency_bar().get("packages") or []) if isinstance(p, str) and p.strip()]


def bar_marker():
    """What names the bar in a tree: its attribute, its packages, its components.

    None where the profile describes no bar, which stands every bar gate down.
    """
    bar = agency_bar()
    names = [bar.get("marker")] + bar_packages() + list(bar.get("components") or [])
    names = [n for n in names if isinstance(n, str) and n.strip()]
    if not names:
        return None
    return re.compile("|".join(re.escape(n) for n in names), re.I)


# A component shell for the frameworks that have one, and for a static site the
# include its build substitutes into every route. Both answer the same question -
# whether one edit reaches every page - and a site composed from HTML partials has
# no .jsx to point at.
SHELL_FILE = re.compile(r"(^|/)(app|_app|layout|root|shell|main)\.(jsx?|tsx?|vue|svelte|astro)$"
                        r"|(^|/)App\.|(^|/)RootLayout"
                        # A component under layout/ or layouts/ is a shell whatever it is
                        # called. React projects name these for the surface they wrap -
                        # MarketingPage, DashboardShell - and matching only the filename
                        # reports a project that mounts the bar correctly as one that
                        # scatters it per page.
                        r"|(^|/)_?layouts?/[^/]+\.(jsx?|tsx?|vue|svelte|astro)$"
                        r"|(^|/)(_?partials|_?includes|_layouts)/"
                        r"(footer|header|layout|shell|base|head\w*)\.html?$", re.I)
COLOUR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch?\(|\blab\("
                            r"|\b(?:white|black|gray|grey|silver|whitesmoke|gainsboro)\b")

# System colours name the browser's colour-scheme preference, not the page. A
# bar built on them matches the operating system and misses the site: a light
# system under a dark page paints the glass white.
SYSTEM_COLOUR = re.compile(
    r"\b(?:Canvas|CanvasText|Window|WindowText|ButtonFace|ButtonText|Field|FieldText"
    r"|Highlight|HighlightText|LinkText|VisitedText|GrayText|ActiveText|Mark|MarkText"
    r"|AccentColor|AccentColorText|SelectedItem|SelectedItemText)\b")

# A ground the bar paints for itself, as opposed to the page showing through.
OPAQUE_GROUND = re.compile(
    # The quotes are consumed possessively: a greedy run backtracks to nothing so
    # the lookahead lands on the quote instead of the value, and every ground
    # reads as opaque.
    r"\bbackground(?:-?[Cc]olor)?\s*:\s*[\"'\s]*+"
    r"(?!transparent|none|inherit|unset|initial|revert)[^;\n}]+")


# Naming the bar and rendering it are different things. A README documenting the
# dependency, a lockfile pinning it, a manifest depending on it and a workflow
# installing it all match the marker, and a documentation badge's hex colours
# then read as tones the bar named for itself. Listing the formats that are not
# the bar leaks, because prose arrives in whatever format a project reaches for
# and a README with no extension carries nothing to match on. So the bar is the
# markup, style and code a browser is served, and every other file is writing
# about it.
BAR_SOURCE = frozenset({
    ".html", ".htm", ".xhtml",
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx",
    ".vue", ".svelte", ".astro",
    ".php", ".erb", ".ejs", ".hbs", ".handlebars", ".mustache", ".njk", ".liquid",
    ".pug", ".twig", ".haml", ".slim",
    ".css", ".scss", ".sass", ".less", ".styl", ".pcss",
})


def bar_files(project):
    marker = bar_marker()
    if marker is None:
        return []
    return sorted({row[0] for row in hits(project, marker, exts=BAR_SOURCE)})


def check_footer_present(gate, project, run, ctx):
    if bar_marker() is None:
        return FAIL, ("the profile describes no agency bar (agency.bar), so there is "
                      "nothing to look for")
    where = bar_files(project)
    if not where:
        bar = agency_bar()
        return FAIL, ("the %s is not in this project - no file carries %s or imports %s"
                      % (bar.get("name") or "agency bar", bar.get("marker") or "its marker",
                         (bar_packages() or ["its package"])[0]))
    shells = [f for f in where if SHELL_FILE.search(f)]
    if shells:
        return PASS, "mounted in the shell that wraps every route: %s" % ", ".join(shells[:3])
    return FAIL, ("the bar appears in %d file(s) but none of them is a shell or layout: %s. "
                  "Mounted per page, it is missing from whichever page nobody remembered."
                  % (len(where), ", ".join(where[:6])))


def check_footer_shared(gate, project, run, ctx):
    where = bar_files(project)
    if not where:
        return FAIL, "no bar to judge - CHR-01 covers its absence"
    # The repository that publishes the bar carries the markup because it is the
    # source of it. Asking it to import the bar asks it to depend on itself.
    packages = bar_packages()
    published = str((project["package"] or {}).get("name") or "")
    if published in packages:
        return PASS, "this repository publishes the bar as %s" % published
    deps = dict(project["package"].get("dependencies") or {})
    deps.update(project["package"].get("devDependencies") or {})
    for shared in packages:
        if shared in deps:
            return PASS, "the shared bar is a dependency at %s" % deps[shared]
    imports = hits(project, re.compile(
        r"import[^\n]*(?:%s)" % "|".join(re.escape(p) for p in packages))) if packages else []
    if imports:
        return PASS, "the bar is imported rather than written here: %s" % named(imports, 3)
    return FAIL, ("the markup is written into this project (%s) rather than imported. A copy "
                  "per project is nine bars that cannot be changed at once." % ", ".join(where[:4]))


def check_footer_no_literals(gate, project, run, ctx):
    where = bar_files(project)
    if not where:
        return FAIL, "no bar to judge - CHR-01 covers its absence"
    # The bar's own formats, which reach past the code default into a stylesheet
    # dialect or a template language the ledger above already accepts as the bar.
    accent = agency_bar().get("accent_var") or ""
    rows = [r for r in hits(project, COLOUR_LITERAL, exts=BAR_SOURCE)
            if r[0] in where and not (accent and accent in r[2])]
    return verdict(len(rows) <= 1,
                   "the bar names at most the one brand accent",
                   "%d literal colour(s) in the bar: %s. Mix its tones from the inherited "
                   "ink instead: color-mix(in oklab, currentColor N%%, transparent)."
                   % (len(rows), named(rows)))


RAIL_HEIGHT = (28, 64)
RAIL_TYPE = (9.0, 14.0)
RAIL_CONTRAST = 4.5
RAIL_LINK_CONTRAST = 4.5


def check_rail_palette(gate, project, run, ctx):
    """CHR-08: the rail is judged on the ground the laid-out page put behind it.

    Source cannot answer this. The markup is identical whether the bar was
    mounted inside the footer's colour context or beside it, and every colour in
    it is right either way; what separates the two is what shows through, which
    only a rendered reading has. So an unfiled reading is a failure rather than
    something to attest around.
    """
    reading = (run or {}).get("rail")
    rail = (reading or {}).get("rail") if isinstance(reading, dict) else None
    if not isinstance(rail, dict) or not rail.get("found"):
        return FAIL, ("no rendered reading of the rail is on file - take one with "
                      "checklists/stack/scripts/code/rail-probe.js and record it "
                      "with stack-pass.py rail-probe --file")

    footer = (reading.get("footer") or {}) if isinstance(reading, dict) else {}
    faults = []

    ground, footer_ground = rail.get("ground"), footer.get("ground")
    if footer.get("found") and ground != footer_ground:
        faults.append("the rail sits on %s while the footer above it is on %s, so it was "
                      "mounted beside the footer's colour context rather than inside it"
                      % (ground, footer_ground))
    if rail.get("paintsOwnGround"):
        faults.append("the rail paints a ground of its own (%s); it shows whatever palette "
                      "it was mounted in" % rail.get("ownBackground"))

    for label, key, floor in (("ink", "contrast", RAIL_CONTRAST),
                              ("link ink", "linkContrast", RAIL_LINK_CONTRAST)):
        value = rail.get(key)
        if isinstance(value, (int, float)) and value < floor:
            faults.append("%s reads at %.2f against its own ground, under %.1f"
                          % (label, value, floor))

    if (rail.get("count") or 0) > 1:
        faults.append("%d rails on one page; there is one bar and it is mounted once"
                      % rail["count"])
    elif (rail.get("signatures") or 0) > (rail.get("count") or 0):
        faults.append("a hand-rolled credit sits beside the rail - %d signature(s) against "
                      "%d rail(s). Changing the bar has to change every site at once, which "
                      "a copy defeats" % (rail["signatures"], rail["count"]))

    height = rail.get("height")
    if isinstance(height, (int, float)) and not RAIL_HEIGHT[0] <= height <= RAIL_HEIGHT[1]:
        faults.append("the rail stands %gpx, outside the standard %g-%gpx"
                      % (height, RAIL_HEIGHT[0], RAIL_HEIGHT[1]))
    size = rail.get("fontSize")
    if isinstance(size, (int, float)) and not RAIL_TYPE[0] <= size <= RAIL_TYPE[1]:
        faults.append("its type is set at %gpx, outside the standard %g-%gpx"
                      % (size, RAIL_TYPE[0], RAIL_TYPE[1]))
    if rail.get("groups") is not None and rail.get("groups") != 2:
        faults.append("the rail shows %s group(s); the split rail is two"
                      % rail.get("groups"))

    return verdict(not faults,
                   "the rail renders on the footer's own ground, legible, once, and to the "
                   "standard geometry",
                   "; ".join(faults))


def check_footer_ground(gate, project, run, ctx):
    """CHR-07: the page shows through the bar, and no tone is a system colour."""
    where = bar_files(project)
    if not where:
        return FAIL, "no bar to judge - CHR-01 covers its absence"
    # The bar's own formats, for the same reason CHR-05 reads them.
    system = [r for r in hits(project, SYSTEM_COLOUR, exts=BAR_SOURCE) if r[0] in where]
    ground = [r for r in hits(project, OPAQUE_GROUND, exts=BAR_SOURCE) if r[0] in where]
    faults = []
    if system:
        faults.append("%d system colour(s): %s - those follow the browser's colour-scheme "
                      "preference, not the page" % (len(system), named(system)))
    if ground:
        faults.append("%d ground(s) the bar paints for itself: %s" % (len(ground), named(ground)))
    return verdict(not faults,
                   "the ground is the page's and every tone is mixed from the inherited ink",
                   "; ".join(faults))


YEAR_LITERAL = re.compile(r"(?:©|&copy;|Copyright|All rights reserved)[^\n]{0,20}\b(19|20)\d{2}\b"
                          r"|\b(19|20)\d{2}\b[^\n]{0,20}(?:©|&copy;|All rights reserved)")


VERBATIM = re.compile(r"licen[cs]e|terms|privacy|changelog", re.I)


def check_hardcoded_year(gate, project, run, ctx):
    rows = [r for r in hits(project, YEAR_LITERAL,
                            exts={".html", ".jsx", ".tsx", ".vue", ".svelte", ".astro",
                                  ".js", ".ts"})
            if not VERBATIM.search(r[0])]
    return verdict(not rows, "no year is typed into the chrome",
                   "%d hardcoded year(s): %s. Compute it at render." % (len(rows), named(rows)))


DEFAULT_ICON = re.compile(r"vite\.svg|next\.svg|react\.svg|nuxt\.svg|favicon\.ico\"\s*/>\s*<!--\s*default",
                          re.I)


def check_icon_set(gate, project, run, ctx):
    root = project["root"]
    public = next((root / d for d in ("public", "static", "assets") if (root / d).is_dir()), root)
    have = {name: any(public.glob(pattern)) for name, pattern in (
        ("favicon", "favicon.*"),
        ("apple-touch-icon", "apple-touch-icon*.png"),
        ("manifest", "*manifest*.json") )}
    if not have["manifest"]:
        have["manifest"] = any(public.glob("*.webmanifest"))
    declared = re.search(r"""<link[^>]*rel=["'][^"']*icon[^"']*["'][^>]*href=["']([^"']+)["']""",
                         project["blob"], re.I)
    if declared and (public / declared.group(1).lstrip("/")).exists():
        have["favicon"] = True
    missing = [k for k, v in have.items() if not v]
    default = hits(project, DEFAULT_ICON, exts={".html", ".jsx", ".tsx", ".astro", ".vue"})
    faults = []
    if missing:
        faults.append("missing from %s: %s" % (rel(project, public), ", ".join(missing)))
    if default:
        faults.append("the framework's own icon is still referenced: %s" % named(default, 3))
    return verdict(not faults, "favicon, apple-touch-icon and manifest all present and ours",
                   "; ".join(faults))


# ---- tooling


def scripts_of(project):
    return dict(project["package"].get("scripts") or {})


def check_prettier_config(gate, project, run, ctx):
    root = project["root"]
    found = [p for p in root.iterdir()
             if p.is_file() and re.match(r"^\.prettierrc|^prettier\.config\.", p.name)]
    if found and any(read(p).strip() not in ("", "{}", "[]") for p in found):
        return PASS, "configured in %s" % ", ".join(base(p) for p in found)
    if project["package"].get("prettier"):
        return PASS, "configured under the prettier key in package.json"
    if found:
        return FAIL, "%s is empty, so it settles nothing" % ", ".join(base(p) for p in found)
    return FAIL, "no Prettier configuration at the root"


def check_npm_script(gate, project, run, ctx):
    name = gate["check"].get("script", "")
    scripts = scripts_of(project)
    if name in scripts:
        return PASS, "%s -> %s" % (name, scripts[name][:80])
    close = [k for k in scripts if name.split(":")[0] in k]
    return FAIL, ("no %s script%s" % (name, (" - the package has %s" % ", ".join(close))
                                      if close else ""))


def check_ci_runs(gate, project, run, ctx):
    name = gate["check"].get("script", "")
    if not project["ci"]:
        return FAIL, "no .github/workflows/ci.yml, so nothing runs %s on a pull request" % name
    if re.search(r"\b%s\b" % re.escape(name), project["ci"]):
        return PASS, "ci.yml calls %s" % name
    return FAIL, "ci.yml does not call %s - the check exists and nothing runs it" % name


def check_eslint_config(gate, project, run, ctx):
    root = project["root"]
    flat = [p for p in root.iterdir() if p.is_file() and re.match(r"^eslint\.config\.", p.name)]
    legacy = [p for p in root.iterdir() if p.is_file() and re.match(r"^\.eslintrc", p.name)]
    if not flat and not legacy:
        return FAIL, "no ESLint configuration at the root"
    text = "\n".join(read(p) for p in flat + legacy)
    if legacy and not flat:
        return FAIL, ("%s is the old format - move to a flat eslint.config.js"
                      % ", ".join(base(p) for p in legacy))
    rules = re.search(r"rules\s*:\s*\{([^}]*)", text, re.S)
    count = len(re.findall(r"['\"][\w@/-]+['\"]\s*:", rules.group(1))) if rules else 0
    return verdict(count > 0,
                   "%s carries %d explicit rule(s)" % (", ".join(base(p) for p in flat), count),
                   "%s extends presets and adds no rules of its own, so it agrees with "
                   "everything" % ", ".join(base(p) for p in flat))


def check_lint_wired(gate, project, run, ctx):
    scripts = scripts_of(project)
    faults = []
    if "lint" not in scripts:
        faults.append("no lint script - the standard workflow runs `npm run lint --if-present`, "
                      "which passes silently without one")
    if project["ci"] and "lint" not in project["ci"]:
        faults.append("ci.yml never mentions lint")
    if not project["ci"]:
        faults.append("no ci.yml")
    return verdict(not faults, "lint -> %s, and ci.yml reaches it" % scripts.get("lint", "")[:60],
                   "; ".join(faults))


BARE_DISABLE = re.compile(r"eslint-disable(?:-next-line|-line)?\s*(?:\*/|$)")


def check_bare_disable(gate, project, run, ctx):
    rows = hits(project, BARE_DISABLE)
    return verdict(not rows, "every disable comment names its rule",
                   "%d bare disable(s): %s. Name the rule and the reason."
                   % (len(rows), named(rows)))


def check_ts_strict(gate, project, run, ctx):
    root = project["root"]
    configs = [p for p in root.glob("tsconfig*.json")]
    if not configs:
        return FAIL, "no tsconfig.json, so nothing is type checked"
    weak = []
    for path in configs:
        data = read_json(path) or {}
        opts = data.get("compilerOptions") or {}
        if opts.get("strict") is True:
            continue
        if "strict" not in opts and data.get("extends"):
            continue    # inherited; the base config answers for it
        weak.append(base(path))
    return verdict(not weak, "strict is on across %d tsconfig(s)" % len(configs),
                   "strict is off or unset in %s" % ", ".join(weak))


ANY_ESCAPE = re.compile(r":\s*any\b|\bas\s+any\b|@ts-ignore|@ts-nocheck")


def check_any_in_touched(gate, project, run, ctx):
    touched = [Path(p) for p in run.get("touched", [])
               if Path(p).suffix.lower() in {".ts", ".tsx"} and Path(p).is_file()]
    if not touched:
        return PASS, "this run touched no TypeScript"
    rows = []
    for path in touched:
        for n, line in enumerate(read(path).splitlines(), 1):
            if ANY_ESCAPE.search(line):
                rows.append((rel(project, path), n, line.strip()[:100]))
    return verdict(not rows, "the %d TypeScript file(s) this run touched carry no escape hatch"
                   % len(touched),
                   "%d escape hatch(es) in what this run changed: %s" % (len(rows), named(rows)))


def check_ci_typecheck(gate, project, run, ctx):
    if not project["ci"]:
        return FAIL, "no ci.yml"
    if re.search(r"tsc\s+--noEmit|npm run (type-?check|tsc)", project["ci"]):
        return PASS, "ci.yml runs the type check"
    return FAIL, ("ci.yml never runs tsc --noEmit - most bundlers transpile without checking, "
                  "so type errors ship")


LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb")


def check_lockfile(gate, project, run, ctx):
    root = project["root"]
    present = [n for n in LOCKFILES if (root / n).is_file()]
    tracked = set(git(str(root), "ls-files").splitlines())
    if not present:
        return FAIL, "no lockfile - CI resolves versions nobody has run"
    if len(present) > 1:
        return FAIL, ("%d lockfiles: %s. Two dependency trees, and whichever tool ran last wins."
                      % (len(present), ", ".join(present)))
    if present[0] not in tracked:
        return FAIL, "%s exists and is not tracked by git" % present[0]
    return PASS, "one lockfile, tracked: %s" % present[0]


def check_node_pinned(gate, project, run, ctx):
    pinned = ((project["package"].get("engines") or {}).get("node") or "").strip()
    if not pinned:
        return FAIL, "engines.node is not set, so CI picks a major nobody chose"
    want = re.search(r"(\d+)", pinned)
    ci_major = re.search(r"node-version:\s*['\"]?(\d+)", project["ci"] or "")
    if not ci_major:
        return FAIL, "engines.node is %s and ci.yml pins no node-version" % pinned
    if want and want.group(1) != ci_major.group(1):
        return FAIL, ("engines.node is %s and ci.yml builds on %s - the build differs from the "
                      "one anybody ran" % (pinned, ci_major.group(1)))
    return PASS, "engines.node %s, ci.yml on %s" % (pinned, ci_major.group(1))


EDITOR_PATHS = (".idea/", ".vscode/", "*.iml", ".fleet/")


def check_editor_ignored(gate, project, run, ctx):
    root = project["root"]
    ignore = read(root / ".gitignore")
    missing = [p for p in EDITOR_PATHS if p.strip("/*") not in ignore]
    tracked = [f for f in git(str(root), "ls-files").splitlines()
               if re.match(r"^\.(idea|vscode|fleet)/|\.iml$", f)]
    faults = []
    if missing:
        faults.append(".gitignore does not cover %s" % ", ".join(missing))
    if tracked:
        faults.append("%d editor file(s) are tracked: %s" % (len(tracked), ", ".join(tracked[:5])))
    return verdict(not faults, "editor configuration is ignored and none is tracked",
                   "; ".join(faults))


# ---- versioning


CALVER = re.compile(r"^(20\d{2})\.(\d{1,2})\.(\d{1,3})(?:[-+].*)?$")


def project_version(project):
    return str(project["package"].get("version") or "").strip()


def check_calver(gate, project, run, ctx):
    version = project_version(project)
    if not version:
        return FAIL, "package.json carries no version"
    if CALVER.match(version):
        return PASS, "%s is CalVer" % version
    return FAIL, ("%s is not CalVer - it reads as a semantic version, which answers a question "
                  "nobody asks of a service that ships continuously. Move it with "
                  "bump-version.py." % version)


# shields.io reads a single dash as a field separator and a double dash as one
# literal dash, so a version carrying its own dash is written escaped and the
# colour is whatever follows the first single dash after it. Matching lazily up
# to any dash reads 2026.35.27--2 as 2026.35.27 and reports drift against the
# manifest that states it in full.
BADGE = re.compile(r"version-((?:--|[0-9A-Za-z.+])+)-")


def check_version_agreement(gate, project, run, ctx):
    root = project["root"]
    version = project_version(project)
    seen = {"package.json": version}
    release = read_json(root / "public" / "release.json")
    if release is not None:
        seen["public/release.json"] = str(release.get("version") or "")
    readme = root / "README.md"
    if readme.is_file():
        found = BADGE.search(read(readme))
        if found:
            seen["README badge"] = found.group(1).replace("--", "-")
    disagree = {k: v for k, v in seen.items() if v != version}
    return verdict(not disagree,
                   "%s agrees across %s" % (version, ", ".join(seen)),
                   "package.json says %s; %s. Move all of them with bump-version.py."
                   % (version, "; ".join("%s says %s" % (k, v or "nothing")
                                         for k, v in disagree.items())))


def check_version_moved(gate, project, run, ctx):
    root = str(project["root"])
    version = project_version(project)
    git(root, "fetch", "--quiet", "origin", "develop", timeout=45)
    # A branch sitting on develop's tip owes no version move, because there is
    # nothing on it to release. Without this the gate reads the two versions as
    # equal - which they are - and calls that the drift it exists to catch, so
    # a run opened on a clean checkout before any work fails a gate about work
    # that has not happened yet.
    head = git(root, "rev-parse", "HEAD").strip()
    base = git(root, "rev-parse", "origin/develop").strip()
    if head and base and head == base:
        return PASS, "this branch is on develop's tip, so it carries no change to version"
    base_text = git(root, "show", "origin/develop:package.json")
    if not base_text:
        return UNKNOWN, "could not read package.json on origin/develop to compare against"
    try:
        theirs = str(json.loads(base_text).get("version") or "")
    except ValueError:
        return UNKNOWN, "origin/develop's package.json did not parse"
    if not version or version == theirs:
        return FAIL, ("develop is on %s and this branch is on %s - the version has to move once, "
                      "in the same commit as the change" % (theirs or "nothing", version or "nothing"))
    return PASS, "%s -> %s" % (theirs, version)


def check_release_tool_used(gate, project, run, ctx):
    root = project["root"]
    tool = "scripts/calver.js" if (root / "scripts" / "calver.js").is_file() else None
    if not tool:
        return PASS, "no project release script; bump-version.py's own step is the tool"
    changed = git(str(root), "diff", "--name-only", "origin/develop...HEAD").splitlines()
    if "package.json" in changed and "public/release.json" not in changed and \
            (root / "public" / "release.json").is_file():
        return FAIL, ("package.json moved and public/release.json did not - the version was "
                      "hand-bumped past %s, which is the drift VER-02 catches one step later"
                      % tool)
    return PASS, "%s is the project's release tool and the manifests moved together" % tool


# ---- repository


def check_single_readme(gate, project, run, ctx):
    root = project["root"]
    found = []
    for path in root.rglob("README*.md"):
        if set(path.parts) & SKIP_DIRS:
            continue
        found.append(rel(project, path))
    if not found:
        return FAIL, "no README.md at all"
    stray = [f for f in found if "/" in f]
    if stray:
        return FAIL, ("%d README(s) outside the root: %s. Each is a second source of truth that "
                      "goes stale unnoticed." % (len(stray), ", ".join(stray[:6])))
    return PASS, "one README, at the root"


def check_single_checkout(gate, project, run, ctx):
    """One clone of this repository on the machine, worktrees aside.

    A second clone is a second place the same branch can be edited, and the two
    disagree silently: work lands in one, the other keeps serving a stale tree,
    and whichever a session happens to open decides which. Worktrees are not
    that, because they share one object store and one set of branches, so the
    sweep compares the common git directory rather than the checkout path.
    """
    root = str(project["root"])
    origin = git(root, "remote", "get-url", "origin").strip()
    if not origin:
        return UNKNOWN, "no origin remote, so there is nothing to match a sibling against"
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()

    searched = []
    for base in (HOME / "Development", Path(root).resolve().parent):
        if base.is_dir() and base not in searched:
            searched.append(base)

    siblings = []
    for base in searched:
        for marker in base.glob("*/.git"):
            other = marker.parent
            if git(str(other), "remote", "get-url", "origin").strip() != origin:
                continue
            if git(str(other), "rev-parse", "--path-format=absolute",
                   "--git-common-dir").strip() == common:
                continue
            siblings.append(str(other))
        for marker in base.glob("*/*/.git"):
            other = marker.parent
            if git(str(other), "remote", "get-url", "origin").strip() != origin:
                continue
            if git(str(other), "rev-parse", "--path-format=absolute",
                   "--git-common-dir").strip() == common:
                continue
            siblings.append(str(other))

    siblings = sorted(set(siblings))
    if siblings:
        return FAIL, ("%d second checkout(s) of this repository: %s. Each is a place the same "
                      "branch can be edited without the other seeing it."
                      % (len(siblings), ", ".join(siblings[:4])))
    return PASS, "one checkout of this repository, worktrees aside"


def check_in_worktree(gate, project, run, ctx):
    root = str(project["root"])
    git_dir = git(root, "rev-parse", "--absolute-git-dir").strip()
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if not git_dir:
        return UNKNOWN, "not a git checkout"
    if common and git_dir != common:
        return PASS, "this is a worktree of %s" % common
    return FAIL, ("this is the primary checkout - two sessions here share one HEAD and one "
                  "index. Open a worktree: git worktree add <path> -b <slug> origin/develop")


def check_based_on_develop(gate, project, run, ctx):
    root = str(project["root"])
    git(root, "fetch", "--quiet", "origin", "develop", timeout=45)
    try:
        done = subprocess.run(["git", "merge-base", "--is-ancestor", "origin/develop", "HEAD"],
                              cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return UNKNOWN, "could not compare against origin/develop: %s" % exc
    if done.returncode == 0:
        return PASS, "origin/develop is an ancestor of HEAD"
    return FAIL, ("this branch is not based on origin/develop - its pull request will "
                  "reintroduce merged commits or conflict on files develop has since changed")


# ---- dependencies


def check_unused_deps(gate, project, run, ctx):
    deps = list((project["package"].get("dependencies") or {}))
    if not deps:
        return PASS, "no runtime dependencies declared"
    # The code alone: package.json lists every dependency by name, so a blob carrying
    # it reports the whole tree as used and the gate never fires. A build config and
    # the workflow are read beside it, because a plugin is depended on from there and
    # never imported anywhere else.
    blob = project["blob"]
    config = "\n".join(read(p) for p in project["root"].glob("*.config.*"))
    unused = []
    for name in deps:
        pattern = re.compile(r"""['"]%s(?:/[^'"]*)?['"]""" % re.escape(name))
        if pattern.search(blob) or pattern.search(config) or name in (project["ci"] or ""):
            continue
        unused.append(name)
    return verdict(not unused, "all %d runtime dependencies are imported" % len(deps),
                   "%d declared and never imported: %s. Each is installed on every machine "
                   "and stays in the audit surface." % (len(unused), ", ".join(sorted(unused))))


FRAMEWORKS = ("react", "react-dom", "next", "vue", "svelte")


def check_duplicate_framework(gate, project, run, ctx):
    modules = project["root"] / "node_modules"
    if not modules.is_dir():
        return UNKNOWN, "node_modules is not installed, so the resolved tree cannot be read"
    doubled = []
    for name in FRAMEWORKS:
        nested = [p for p in modules.rglob("node_modules/%s/package.json" % name)
                  if "/.bin/" not in str(p)]
        if nested and (modules / name).is_dir():
            doubled.append("%s (%d nested cop(y/ies), e.g. %s)"
                           % (name, len(nested), rel(project, nested[0].parent)))
    return verdict(not doubled, "one copy of each framework in the resolved tree",
                   "%s. Two copies break hooks and context in ways that read as application bugs."
                   % "; ".join(doubled))


VCS_PIN = re.compile(r"^(git\+|git:|github:|file:|link:|https?://)")


def check_vcs_pins(gate, project, run, ctx):
    package = project["package"]
    pinned = []
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in (package.get(section) or {}).items():
            if isinstance(spec, str) and VCS_PIN.match(spec):
                pinned.append("%s -> %s" % (name, spec))
    return verdict(not pinned, "every dependency resolves from the registry",
                   "%d pinned outside the registry: %s. These build on the machine that has "
                   "them and fail in CI." % (len(pinned), "; ".join(pinned[:6])))


# ---- auth


MIDDLEWARE = ("middleware.ts", "middleware.js", "src/middleware.ts", "src/middleware.js")
PROTECTED_HINT = re.compile(r"(^|/)(dashboard|account|admin|console|settings|billing|app)(/|$)", re.I)


def route_dirs(project):
    """The routes a browser can open. A note filed under docs/app/admin is writing
    about a route rather than one, and counting it holds a matcher to paths the
    application never serves."""
    seen = set()
    for path in project["files"]:
        if path.suffix.lower() not in CODE_EXT:
            continue
        text = rel(project, path)
        match = re.search(r"(?:^|/)(?:app|pages|routes|views)/(.+)", text)
        if match:
            seen.add("/" + match.group(1).rsplit("/", 1)[0])
    return sorted(seen)


def check_middleware_matcher(gate, project, run, ctx):
    root = project["root"]
    found = next((root / m for m in MIDDLEWARE if (root / m).is_file()), None)
    if not found:
        return FAIL, ("this project authenticates and has no middleware - every route is served "
                      "to whoever asks, and the client-side guard is a redirect anybody can skip")
    text = read(found)
    matcher = re.search(r"matcher\s*:\s*\[([^\]]*)\]", text, re.S)
    if not matcher:
        return UNKNOWN, "%s has no matcher array to read - confirm by hand what it covers" % base(found)
    patterns = re.findall(r"['\"]([^'\"]+)['\"]", matcher.group(1))
    if any(p.startswith("/((?!") or p == "/(.*)" for p in patterns):
        return PASS, "%s matches everything and lists its exceptions: %s" % (base(found), patterns[0][:90])
    protected = [d for d in route_dirs(project) if PROTECTED_HINT.search(d)]
    uncovered = [d for d in protected
                 if not any(d.startswith(p.split("/:")[0].split("(")[0].rstrip("/")) for p in patterns)]
    return verdict(not uncovered,
                   "%s covers every route that looks protected (%d)" % (base(found), len(protected)),
                   "%d protected-looking route(s) the matcher never reaches: %s"
                   % (len(uncovered), ", ".join(uncovered[:8])))


def check_session_api(gate, project, run, ctx):
    rows = hits(project, re.compile(r"\.auth\s*\.\s*getSession\s*\("))
    if not rows:
        return PASS, "nothing reads getSession"
    return FAIL, ("%d call(s) to getSession: %s. It returns what the cookie says without "
                  "verifying it, so any decision made on it is made on user input. Use "
                  "getClaims or getUser." % (len(rows), named(rows)))


OWNERSHIP_FILTER = re.compile(r"\.eq\(\s*['\"](user_id|owner_id|profile_id|account_id|org_id)['\"]")


def check_client_side_filter(gate, project, run, ctx):
    rows = [r for r in hits(project, OWNERSHIP_FILTER)
            if not re.search(r"(^|/)(api|server|functions)/|\.server\.|route\.[jt]s", r[0])]
    return verdict(not rows, "no ownership filter is applied from a client path",
                   "%d client-side ownership filter(s): %s. The key is in the browser, so the "
                   "request can be made again without the filter. Enforce it with an RLS policy."
                   % (len(rows), named(rows)))


SERVER_ONLY = re.compile(r"(^|/)(api|server|functions|supabase)/|\.server\.|"
                         r"route\.[jt]s$|middleware\.[jt]s$|\.node\.")


def check_service_role_reachable(gate, project, run, ctx):
    # The key, not the postgres role of the same name: a migration granting to
    # service_role is the policy working, and SQL never reaches a browser.
    rows = [r for r in hits(project, re.compile(r"SERVICE_ROLE_KEY"))
            if not SERVER_ONLY.search(r[0])
            and not r[0].endswith((".sql", ".md", ".mdx", ".txt", ".rst"))]
    return verdict(not rows, "the service role key is read only from server paths",
                   "%d client-reachable file(s) read the service role key: %s. That key bypasses "
                   "every policy by design. Rotate it if this has ever been built."
                   % (len(rows), named(rows)))


# ---- observability, legal


def reporter_pattern():
    """The snippet's endpoint, in any of the forms a project carries it.

    The host's own route comes from the profile's host.url. A tailnet name keeps
    any tailnet id after the machine's name, so a project that still carries the
    route under an older tailnet is read as carrying it.
    """
    forms = [r"error-reporting-service/report", r"errorReportingEndpoint"]
    host = re.sub(r"^[a-z]+://", "", str(setting("host.url") or ""), flags=re.I)
    host = host.split("/")[0].split(":")[0]
    if host:
        tailnet = re.match(r"^([^.]+)\.tail[0-9a-z]+\.ts\.net$", host, re.I)
        forms.append(re.escape(tailnet.group(1)) + r"\.tail[0-9a-z]+\.ts\.net/report"
                     if tailnet else re.escape(host) + "/report")
    return re.compile("|".join(forms), re.I)


def check_reporter_snippet(gate, project, run, ctx):
    rows = hits(project, reporter_pattern(),
                exts={".html", ".jsx", ".tsx", ".js", ".ts", ".astro", ".vue"})
    if not rows:
        return FAIL, ("the error reporting snippet is nowhere in this project - a failure in "
                      "front of a visitor reaches nobody. Run /onboard-error-reporting.")
    served = [r for r in rows if r[0].endswith(".html") or SHELL_FILE.search(r[0])]
    return verdict(bool(served), "the reporter is in the document the build serves: %s" % named(rows, 3),
                   "the snippet appears in %s but not in a served document or shell, so the "
                   "build may not carry it" % named(rows, 3))


LEGAL = {"privacy": re.compile(r"privacy", re.I),
         "terms": re.compile(r"\bterms\b", re.I),
         "contact": re.compile(r"contact", re.I)}


def check_legal_routes(gate, project, run, ctx):
    # What a browser can open. A privacy note filed under docs/ is this gate's own
    # subject written down, and counting it certifies a site whose visitors have
    # nowhere to click.
    names = [rel(project, p).lower() for p in project["files"]
             if p.suffix.lower() in CODE_EXT]
    routes = "\n".join(names) + "\n" + project["blob"][:400000]
    missing = [k for k, pattern in LEGAL.items()
               if not any(pattern.search(n) for n in names)
               and not re.search(r"['\"]/%s" % k, routes)]
    return verdict(not missing, "privacy, terms and contact are all served",
                   "no route for: %s" % ", ".join(missing))


# The whole filename stem, not any substring of a path. Unanchored, every route of a
# SvelteKit or Remix app sat under routes/ and read as the footer, so one page linking
# /privacy closed the gate the footer itself had failed. A footer is often named for
# where it sits - AppFooter, SiteFooter - so the stem carries a prefix and still ends
# in the word.
FOOTER_SOURCE = re.compile(r"^(?:[\w-]*footer|navigation|nav|nav-[\w-]+|[\w-]+-nav"
                           r"|links|routes|constants)$", re.I)

# A footer rarely holds its own hrefs. They live in the navigation constants it maps
# over, so a check reading only the component finds nothing and calls it missing. The
# directory has to be named exactly one of these: routes/ is deliberately absent,
# because every page of an application sits under it.
FOOTER_DIR = frozenset({"constants", "config", "navigation", "nav"})


def footer_sources(project):
    """The code that decides what the footer links to: the footer and navigation
    components, and the constants they map over."""
    held = []
    for path, text in project["sources"]:
        if path.suffix.lower() not in CODE_EXT:
            continue
        parts = {p.lower() for p in Path(rel(project, path)).parent.parts}
        if FOOTER_SOURCE.search(path.stem) or (parts & FOOTER_DIR):
            held.append((path, text))
    return held


def check_legal_linked(gate, project, run, ctx):
    footers = footer_sources(project)
    if not footers:
        return UNKNOWN, "no footer or navigation source found - confirm by hand where the links live"
    text = "\n".join(t for _, t in footers)
    missing = [k for k in LEGAL if not re.search(r"/%s" % k, text, re.I)]
    return verdict(not missing, "the site footer links all three",
                   "the site footer does not link: %s. A page linked from nowhere is one "
                   "nobody finds when they go looking." % ", ".join(missing))


CONSENT = re.compile(r"cookie[- ]?(consent|banner|notice)|CookieBanner|consent[- ]?manager", re.I)


def check_cookie_notice(gate, project, run, ctx):
    rows = hits(project, CONSENT)
    return verdict(bool(rows), "a cookie notice is present: %s" % named(rows, 3),
                   "the site loads a tracker and shows no notice")


CHECKS = {
    "manual": None,
    "probe": check_probe,
    "vercel_linked": check_vercel_linked,
    "public_prefix_secrets": check_public_prefix_secrets,
    "bundle_secrets": check_bundle_secrets,
    "env_example": check_env_example,
    "env_committed": check_env_committed,
    "footer_present": check_footer_present,
    "footer_shared": check_footer_shared,
    "footer_no_literals": check_footer_no_literals,
    "footer_ground": check_footer_ground,
    "rail_palette": check_rail_palette,
    "hardcoded_year": check_hardcoded_year,
    "icon_set": check_icon_set,
    "prettier_config": check_prettier_config,
    "npm_script": check_npm_script,
    "ci_runs": check_ci_runs,
    "eslint_config": check_eslint_config,
    "lint_wired": check_lint_wired,
    "bare_disable": check_bare_disable,
    "ts_strict": check_ts_strict,
    "any_in_touched": check_any_in_touched,
    "ci_typecheck": check_ci_typecheck,
    "lockfile": check_lockfile,
    "node_pinned": check_node_pinned,
    "editor_ignored": check_editor_ignored,
    "calver": check_calver,
    "version_agreement": check_version_agreement,
    "version_moved": check_version_moved,
    "release_tool_used": check_release_tool_used,
    "single_readme": check_single_readme,
    "single_checkout": check_single_checkout,
    "in_worktree": check_in_worktree,
    "based_on_develop": check_based_on_develop,
    "unused_deps": check_unused_deps,
    "duplicate_framework": check_duplicate_framework,
    "vcs_pins": check_vcs_pins,
    "middleware_matcher": check_middleware_matcher,
    "session_api": check_session_api,
    "client_side_filter": check_client_side_filter,
    "service_role_reachable": check_service_role_reachable,
    "reporter_snippet": check_reporter_snippet,
    "legal_routes": check_legal_routes,
    "legal_linked": check_legal_linked,
    "cookie_notice": check_cookie_notice,
    # Settled from the run's own state, in settle_self.
    "run_opened": None, "all_resolved": None, "report_emitted": None,
    "file_coverage": None, "scope_coverage": None, "probe_filed": None, "delegation": None,
}


# ---------------------------------------------------------------- carry-forward

# Check types that answer to something outside the project, whose answer a file
# hash therefore cannot vouch for. probe is a request that was made at a moment
# and rail_palette is a rendered reading of a page that was open at the time, so
# a service awake yesterday says nothing about today. single_checkout reads the
# machine for a second clone of the repo and based_on_develop reads the remote:
# both can turn false while every tracked byte stays where it was, and a carried
# pass would hide a second checkout for the length of the TTL - which is the
# drift that cost a night of work here once already.
NETWORK_CHECKS = {"probe", "rail_palette", "single_checkout", "based_on_develop"}


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
        _CACHE = pass_cache.open_for("stack", run.get("targets") or [], GATES_DIR, TOOL,
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


# Files the checks reach for by name rather than by extension, so a scope built
# from the source sweep alone would leave them out and a gate reading one would
# keep its answer across an edit to it.
NAMED_FILES = ("package.json", "package-lock.json", "tsconfig.json", ".env.example",
               ".gitignore", ".nvmrc", ".prettierrc", ".github/workflows/ci.yml")


def sweep_scope(project):
    """Every file the sweep reads: the source, the build it asks about, and the
    named files at the root that carry the project's wiring."""
    paths = {str(Path(p).resolve()) for p in project["files"]}
    paths.update(str(Path(p).resolve()) for p in project["built"])
    root = Path(project["root"])
    paths.update(str(root / name) for name in NAMED_FILES)
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


# ---------------------------------------------------------------- the file ledger


LEDGER_EXT = SOURCE_EXT - {".md"}


def ledger_candidates(run):
    keep = []
    for path in iter_files([Path(t) for t in run["targets"]], exts=LEDGER_EXT):
        if BINARY_LIKE.search(path.name):
            continue
        keep.append(str(path.resolve()))
    return keep


def refresh_ledger(run):
    files = run.setdefault("files", {})
    current = set(ledger_candidates(run))
    for path in current:
        files.setdefault(path, {"status": "open", "note": "", "read": False, "at": ""})
    for path in list(files):
        if path not in current:
            files.pop(path)
    run["excluded"] = sorted(
        str(p) for p in iter_files([Path(t) for t in run["targets"]], exts=SOURCE_EXT)
        if str(p.resolve()) not in current)
    carry_files(run, files)
    return files


def mark_read(run, paths):
    files = run.get("files") or {}
    changed = False
    for raw in paths:
        resolved = str(Path(raw).expanduser().resolve())
        if resolved in files and not files[resolved]["read"]:
            files[resolved]["read"] = True
            changed = True
    return changed


READ_COMMAND = re.compile(r"\b(cat|head|tail|sed|less|bat|rg|grep|awk)\b")


def shell_read_paths(command, base_dir):
    if not READ_COMMAND.search(command):
        return []
    tokens = shell_tokens(command)
    out = []
    for token in tokens:
        if token.startswith("-"):
            continue
        candidate = (Path(base_dir) / token).expanduser()
        if candidate.is_file():
            out.append(str(candidate.resolve()))
    return out


# ---------------------------------------------------------------- run lifecycle


def _run_at(pointer):
    if not pointer.exists():
        return None
    try:
        return json.loads((RUNS / ("%s.json" % json.loads(pointer.read_text())["id"])).read_text())
    except (ValueError, OSError, KeyError):
        return None


def load_run(required=True, session=None, own_only=False, here=None):
    """The calling session's run, or the shared one when no session claims it.

    A run belongs to the session that opened it. The shared pointer is read only
    as a fallback, and only when the run it names is unclaimed or claimed by this
    same session - two sessions once shared one pointer, and whichever run opened
    last replaced the other's. The hooks pass own_only, because a guard acting on
    another session's run would refuse work that session is still doing.

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
        raise SystemExit("No stack run is open. Start one:\n  %s start --target <path>" % TOOL)
    return run


def save_run(run):
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / ("%s.json" % run["id"])).write_text(json.dumps(run, indent=2))
    pointer = json.dumps({"id": run["id"]})
    claim_shared(run, pointer)
    if run.get("session"):
        pointer_for(run["session"], run_target(run)).write_text(pointer)


def probes_wanted(run, index):
    """Every probe name the applicable gates need answered."""
    want = set()
    for gid in run.get("order", []):
        check = index[gid]["check"]
        if check["type"] == "probe":
            want.add(check.get("probe", ""))
    return {name for name in want if name}


def settle_self(run, index, ctx=None):
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
        elif kind == "scope_coverage":
            named_services = [s for s in ("supabase", "clerk", "stripe", "vercel")
                              if ctx and ctx.get(s)]
            scope = run.get("scope")
            ok = not scope
            note = ("the run covers every service this project talks to: %s"
                    % (", ".join(named_services) or "none") if ok
                    else "scoped to %s, so the rest of what this project owes is outside it"
                    % scope)
        elif kind == "probe_filed":
            want = probes_wanted(run, index)
            filed = set((run.get("probe") or {}))
            missing = sorted(want - filed)
            ok = not missing
            note = ("%d probe result(s) filed, covering every gate that needs one" % len(filed)
                    if ok else "%d probe(s) never taken: %s" % (len(missing), ", ".join(missing[:8])))
        elif kind == "delegation":
            handed = run.get("delegated") or []
            ok = bool(handed) or bool(run.get("nothing_delegated"))
            note = ("%d finding(s) handed over: %s"
                    % (len(handed), "; ".join("%s -> %s" % (d["to"], d["note"][:60])
                                              for d in handed[:4])) if handed
                    else "nothing outside this checklist came up" if ok
                    else "nothing recorded either way - delegate what belongs elsewhere, or "
                         "answer n/a saying nothing came up")
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
    run["context"] = sorted(k for k, v in ctx.items()
                            if v and k != "always" and k not in PROFILE_CONDITIONS)
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
    settle_self(run, gate_index(groups), ctx)
    return groups, ctx, project


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


def open_run(targets, title, kind="site", session=None, url=None):
    refuse_foreign_cwd(targets)
    run = {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session or session_id(),
        "kind": kind,
        "targets": [str(Path(t).expanduser().resolve()) for t in targets],
        "repo": target_root(targets) or repo_root(),
        "url": url or "",
        "title": title,
        "flags": [],
        "results": {},
        "touched": [],
        "probe": {},
        "delegated": [],
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
    if run.get("url"):
        print("URL: %s" % run["url"])
    print("Found: %s" % ", ".join(run.get("context", [])[:18]))
    print("%d gate(s) apply.\n" % len(run["order"]))
    current = None
    for gid in run["order"]:
        gate = index[gid]
        if gate["group"] != current:
            current = gate["group"]
            print("== %s" % current)
        kind = gate["check"]["type"]
        how = "probe" if kind == "probe" else ("auto" if kind != "manual" else "by hand")
        print("   %-8s [%s|%s] %s" % (gid, gate["severity"], how, gate["title"]))
    want = probes_wanted(run, index)
    print("\n%d gate(s) answer to a request rather than a reading." % len(want))
    if want:
        print("Take them first - nothing else in this run can stand without them:\n"
              "  %s all --root %s%s --out /tmp/stack-probe.json\n"
              "  %s probe --file /tmp/stack-probe.json"
              % (PROBE_TOOL, run["targets"][0],
                 (" --url %s" % run["url"]) if run.get("url") else "", TOOL))
    print("\nThen:\n  %s verify" % TOOL)


def cmd_start(argv):
    flags = parse_flags(argv)
    kind = (flags.get("kind") or ["site"])[0]
    if kind not in KINDS:
        raise SystemExit("kind must be one of: %s" % ", ".join(KINDS))
    here = os.getcwd()
    targets = flags.get("target") or [str(project_root(here))]
    refuse_foreign_cwd(targets)
    run = {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session_id(),
        "kind": kind,
        "targets": [str(Path(t).expanduser().resolve()) for t in targets],
        "repo": target_root(targets) or repo_root(),
        "url": (flags.get("url") or [""])[0],
        "title": " ".join(flags.get("title", [])) or "Stack run",
        "flags": flags.get("flag", []),
        "scope": _scope_flag(flags),
        "results": {},
        "touched": [],
        "probe": {},
        "delegated": [],
    }
    groups, _, _ = refresh(run)
    save_run(run)
    print_checklist(run, groups, header="Stack run %s opened - %s" % (run["id"], run["title"]))
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
    if flags.get("flag"):
        run["flags"] = sorted(set(run.get("flags", []) + flags["flag"]))
    if flags.get("scope"):
        run["scope"] = flags["scope"][0] if flags["scope"][0] != "all" else None
    groups, _, _ = refresh(run)
    save_run(run)
    print_checklist(run, groups, header="Run %s rescoped" % run["id"])
    return 0


def cmd_services(argv):
    """What this project talks to, and what a request to each one found."""
    run = load_run()
    groups, ctx, project = refresh(run)
    index = gate_index(groups)
    save_run(run)
    filed = run.get("probe") or {}
    print("Run %s - %s\n" % (run["id"], run["title"]))
    for service in ("supabase", "clerk", "stripe", "vercel"):
        if not ctx.get(service):
            continue
        print("== %s" % service)
        for name in sorted(probes_wanted(run, index)):
            if not name.startswith(service + "."):
                continue
            got = filed.get(name)
            state = "not probed" if not got else ("ok" if got.get("ok") else "FAILING")
            print("   %-22s %-10s %s" % (name, state, (got or {}).get("detail", "")[:90]))
    other = sorted(n for n in probes_wanted(run, index)
                   if not n.split(".")[0] in ("supabase", "clerk", "stripe", "vercel"))
    if other:
        print("\n== other")
        for name in other:
            got = filed.get(name)
            state = "not probed" if not got else ("ok" if got.get("ok") else "FAILING")
            print("   %-22s %-10s %s" % (name, state, (got or {}).get("detail", "")[:90]))
    print("\n%d of %d probe(s) filed." % (len(filed), len(probes_wanted(run, index))))
    print("Variables the code reads (%d): %s"
          % (len(project["env_names"]), ", ".join(project["env_names"][:20])))
    return 0


def cmd_probe(argv):
    """File probe results. A gate in this checklist answers to one of these, never to a claim."""
    run = load_run()
    flags = parse_flags(argv)
    source = (flags.get("file") or [None])[0]
    if not source:
        raise SystemExit("probe takes --file <result.json> written by %s" % PROBE_TOOL)
    try:
        payload = json.loads(Path(source).expanduser().read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit("could not read %s: %s" % (source, exc))
    rows = payload.get("probes") if isinstance(payload, dict) else payload
    if not isinstance(rows, dict):
        raise SystemExit("%s is not a probe result - expected {\"probes\": {name: {ok, detail}}}"
                         % source)
    store = run.setdefault("probe", {})
    for name, row in rows.items():
        if not isinstance(row, dict) or "ok" not in row:
            raise SystemExit("probe %r carries no ok verdict" % name)
        store[name] = {"ok": bool(row["ok"]), "detail": str(row.get("detail", "")),
                       "at": str(row.get("at") or now())}
    groups, _, _ = refresh(run)
    save_run(run)
    failing = [n for n, r in store.items() if not r["ok"]]
    print("Filed %d probe result(s)." % len(rows))
    for name in sorted(rows):
        row = store[name]
        print("  %-3s %-24s %s" % ("ok" if row["ok"] else "X", name, row["detail"][:100]))
    want = probes_wanted(run, gate_index(groups))
    missing = sorted(want - set(store))
    if missing:
        print("\n%d gate(s) still have no probe: %s" % (len(missing), ", ".join(missing)))
    if failing:
        print("\n%d service(s) are failing right now: %s\n"
              "These are settings to change, not findings to record - none of them takes a "
              "hand answer." % (len(failing), ", ".join(sorted(failing))))
    return 0


def cmd_delegate(argv):
    """Hand a finding to the checklist that owns it, on the record."""
    run = load_run()
    flags = parse_flags(argv)
    if "none" in flags:
        run["nothing_delegated"] = True
        groups, _, _ = refresh(run)
        save_run(run)
        print("Recorded: nothing outside this checklist came up in this run.")
        return 0
    to = (flags.get("to") or [""])[0]
    note = " ".join(flags.get("note", []))
    if to not in ("design", "seo", "perf"):
        raise SystemExit("--to must be design, seo or perf - the three checklists this one hands "
                         "work to. Use --none when nothing outside this checklist came up.")
    if len(note.strip()) < 20:
        raise SystemExit("--note must say what was found and what was done with it, so the "
                         "handover is followable without this session")
    run.setdefault("delegated", []).append({"to": to, "note": note, "at": now()})
    run["nothing_delegated"] = False
    groups, _, _ = refresh(run)
    save_run(run)
    print("Handed to /%s-checklist: %s" % (to, note))
    print("%d finding(s) delegated in this run." % len(run["delegated"]))
    return 0


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
        except Exception as exc:      # a broken checker must never read as a pass
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


# The shapes evidence takes in a note: a file name, a :line reference, a
# measurement, or a quotation. A note matching none of them is an assertion.
EVIDENCE = re.compile(r"[\w-]+\.[a-zA-Z]{2,5}\b|:\d+|\b\d+(\.\d+)?(px|ms|s|%|KB|MB)?\b|[\"“‘']")


def _condition_still_holds(gate, ctx):
    when = gate.get("when", "always")
    for condition in (when if isinstance(when, list) else [when]):
        if condition != "always" and not ctx.get(condition, False):
            return False
    return True


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
    # A live service answers to a request or to nothing. This is the whole reason the
    # probe exists: a configuration that reads correctly and answers nothing is the
    # failure mode, and an attestation cannot tell the two apart.
    # LIV-02 reads a browser console, which is a rendered reading like any other. On a
    # machine with nothing to render with it cannot be taken, and calling that a dispute
    # says the check is wrong when it is only unread. `unmeasured` records it honestly,
    # and only for the gates that actually want a browser - a probe that failed for a
    # missing credential is not unmeasured, it is unconfigured, and the fix is the
    # credential.
    if status == "unmeasured" and not (gid in BROWSER_PROBES and run.get("uninstrumented")):
        faults.append("unmeasured is only for a gate whose reading needs a browser, on a run "
                      "marked uninstrumented. Record the absence first: "
                      "%s uninstrumented --reason \"<what is missing>\". A probe that failed "
                      "for want of a credential is unconfigured, not unmeasured" % TOOL)
    if status == "unmeasured" and len(note.strip()) < 40:
        faults.append("recording a gate as unmeasured takes the reason: which instrument was "
                      "missing, what it would have read, and why it could not be run")
    if gate["check"]["type"] == "probe" and status not in ("unmeasured",) and status in ("pass", "na"):
        faults.append("this gate answers to a request, not to an attestation - take the probe "
                      "with %s and file it with `%s probe --file <result.json>`. If the service "
                      "genuinely is not part of this project, remove the dependency rather than "
                      "answering n/a." % (PROBE_TOOL, TOOL))
    if (gate["check"]["type"] == "rail_palette" and status in ("pass", "na")
            and not (run.get("rail") or {}).get("rail")):
        faults.append("nothing separates a seated rail from a detached one in the markup, so "
                      "this gate answers to a rendered reading: take one with rail-probe.js "
                      "and file it with `%s rail-probe --file <result.json>`" % TOOL)
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
        when = project_when(gate)
        named_when = "+".join(when) if isinstance(when, list) else when
        if named_when != "always":
            faults.append("the gate entered this run because the %s contains %s, and it still "
                          "does, so not-applicable contradicts what the tool can see"
                          % (TARGET_NOUN, named_when))
    if status == "disputed" and len(note.strip()) < 40:
        faults.append("disputing a check takes the measurement: what it reported, what is "
                      "actually there, and how that was established")
    if status in ("disputed", "na", "fixed") and note.strip() and not EVIDENCE.search(note):
        faults.append("the note carries no evidence - name what was found: a path, a line, "
                      "a measurement or a quoted string")
    if status == "disputed" and not failing:
        faults.append("nothing to dispute - the sweep did not fail this gate")
    return faults


def cmd_resolve_batch(source):
    """Answer many gates in one call, each with its own status and its own note."""
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
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"
                and index[g]["check"]["type"] not in SELF_SETTLING]
    pending = [g for g in run["order"] if run["results"][g]["status"] == "open"
               and index[g]["check"]["type"] in SELF_SETTLING]
    probes = [g for g in open_ids if index[g]["check"]["type"] == "probe"]
    line = scope_line(run)
    if line:
        print(line)
    print("Run %s - %s (%s)" % (run["id"], run["title"], run["kind"]))
    print("Targets: %s" % ", ".join(run["targets"]))
    print("%d applicable gate(s), %d to answer.\n" % (len(run["order"]), len(open_ids)))
    full = "full" in flags
    for gid in open_ids:
        gate = index[gid]
        print("  %-8s %s\n           %s"
              % (gid, gate["title"], run["results"][gid].get("auto", "not swept yet")[:220]))
        if full:
            print("           rule: %s" % gate["rule"])
            print("           fix:  %s" % gate.get("fix", ""))
    if probes:
        print("\n%d of those answer to a request and take no hand answer: %s\n"
              "  %s all --root %s --out /tmp/stack-probe.json && %s probe --file /tmp/stack-probe.json"
              % (len(probes), ", ".join(probes), PROBE_TOOL, run["targets"][0], TOOL))
    if pending:
        print("\nSettled by the run itself once the rest are answered: %s" % ", ".join(pending))
    answerable = [g for g in open_ids if g not in probes]
    if answerable:
        print("\nAnswer them in one call - each gate keeps its own status and its own note:\n"
              "  %s resolve --batch - <<'JSON'\n"
              "  [{\"id\": \"%s\", \"status\": \"fixed\", \"note\": \"what changed\"}]\n"
              "  JSON" % (TOOL, answerable[0]))
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
            problems.append("%s: note must say what was read and why it needs no change"
                            % Path(path).name)
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
    print("%d file(s) still to rule on."
          % len([p for p, v in files.items() if v["status"] == "open"]))
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
    outstanding = [g for g in run["order"] if run["results"][g]["status"] == "open"
                   and index[g]["check"]["type"] not in SELF_SETTLING]
    if not outstanding:
        run["report_at"] = run.get("report_at") or now()
        settle_self(run, index)
        save_run(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    body = ["# Stack checklist - %s" % run["title"], "",
            "Run `%s`, kind `%s`, opened %s." % (run["id"], run["kind"], run["opened"]), "",
            "Targets: %s" % ", ".join("`%s`" % t for t in run["targets"]), ""]
    if run.get("url"):
        body += ["Deployed at `%s`." % run["url"], ""]
    probe = run.get("probe") or {}
    if probe:
        failing = [n for n, r in probe.items() if not r["ok"]]
        body += ["## What answered", "",
                 "%d service check(s) were made over the network; %d came back failing."
                 % (len(probe), len(failing)), "",
                 "| Probe | Verdict | What it found |", "| --- | --- | --- |"]
        for name in sorted(probe):
            row = probe[name]
            body.append("| `%s` | %s | %s |" % (name, "ok" if row["ok"] else "FAILING",
                                                row["detail"].replace("|", "/")[:200]))
        body.append("")
    fixed = [g for g in run["order"] if run["results"][g]["status"] == "fixed"]
    if fixed:
        body += ["## Changed", ""]
        body += ["- **%s %s** - %s" % (g, index[g]["title"], run["results"][g]["note"])
                 for g in fixed]
        body.append("")
    else:
        changed = changed_files(run)
        if changed:
            body += ["## Changed", "",
                     "No gate needed a hand answer of fixed: each one the sweep had failing "
                     "passes now because the %s moved. What moved:" % TARGET_NOUN, ""]
            body += ["- `%s`" % name for name in changed]
            body.append("")
        else:
            body += ["Nothing was changed in this run.", ""]
    handed = run.get("delegated") or []
    if handed:
        body += ["## Handed to another checklist (%d)" % len(handed), "",
                 "This checklist asks whether a thing is wired up and alive. Each of these "
                 "belongs to a checklist that owns the question it raises.", ""]
        body += ["- **/%s-checklist** - %s" % (d["to"], d["note"]) for d in handed]
        body.append("")
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
                     "  - sweep said: %s" % run["results"][gid].get("auto", "")[:200],
                     "  - the run's measurement: %s" % run["results"][gid]["note"]]
        body += [""]
    unmeasured = [g for g in run["order"] if run["results"][g]["status"] == "unmeasured"]
    if unmeasured:
        why = (run.get("uninstrumented") or {}).get("reason", "no reason recorded")
        body += ["### Unmeasured - no instrument to take the reading (%d)" % len(unmeasured), "",
                 "This run had no browser to read a rendered page with: %s. None of these is "
                 "a pass - they are the rules it could not enforce." % why, ""]
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


def changed_files(run):
    """Every path under the run's targets that differs from what git has."""
    seen = []
    for target in run.get("targets") or []:
        root = Path(target)
        if not root.exists():
            continue
        out = git(str(root), "status", "--porcelain")
        for line in out.splitlines():
            name = line[3:].strip().strip('"')
            if name and name not in seen:
                seen.append(name)
    return sorted(seen)


# Gates whose reading is taken in a browser: the console, the round trip that
# starts with a real error thrown in a page, and the rail's rendered ground.
# Everything else the probe reaches, it reaches over the network with a
# credential, so its failures are configuration.
BROWSER_PROBES = {"LIV-02", "OBS-05", "CHR-08"}


def cmd_rail_probe(argv):
    """File a rendered reading of the rail, as rail-probe.js writes it.

    With --script it prints the probe instead, with the profile's bar marker and
    agency domain set ahead of it, ready to evaluate in the page.
    """
    flags = parse_flags(argv)
    if "script" in flags:
        source = SKILL / "scripts" / "code" / "rail-probe.js"
        config = {"marker": agency_bar().get("marker") or "",
                  "domain": setting("agency.domain") or ""}
        print("window.railProbeConfig = %s;\n%s" % (json.dumps(config), source.read_text()))
        return 0
    run = load_run()
    source = (flags.get("file") or [None])[0]
    if not source:
        raise SystemExit("rail-probe takes --file <result.json> written by "
                         "checklists/stack/scripts/code/rail-probe.js")
    try:
        payload = json.loads(Path(source).expanduser().read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit("could not read %s: %s" % (source, exc))
    if not isinstance(payload, dict) or not isinstance(payload.get("rail"), dict):
        raise SystemExit("%s is not a rail reading - expected {\"rail\": {...}, "
                         "\"footer\": {...}}" % source)
    payload["at"] = payload.get("at") or now()
    run["rail"] = payload
    save_run(run)
    rail = payload["rail"]
    print("rail reading filed from %s: rail on %s, footer on %s"
          % (payload.get("captured", "?"), rail.get("ground"),
             (payload.get("footer") or {}).get("ground")))
    return 0


def cmd_uninstrumented(argv):
    """Record that this run has no browser available to read a rendered page with."""
    flags = parse_flags(argv)
    reason = " ".join(flags.get("reason", [])).strip()
    if len(reason) < 20:
        raise SystemExit("--reason is required, and says what is missing: which engine, "
                         "and why it cannot be run here")
    run = load_run()
    run["uninstrumented"] = {"at": now(), "reason": reason}
    save_run(run)
    print("Recorded: run %s has no browser available.\n  %s\n\n"
          "%s now takes `unmeasured`, with its own reason. Every other probe still "
          "answers to a request." % (run["id"], reason, ", ".join(sorted(BROWSER_PROBES))))
    return 0


def cmd_finish(argv):
    run = load_run()
    flags = parse_flags(argv)
    if "no-deliverable" in flags:
        touched = [t for t in (run.get("touched") or [])
                   if Path(t).exists() and differs_from_git(Path(t))]
        if touched:
            print("Cannot close as no-deliverable: this run changed %d file(s) - %s.\n"
                  "Work the checklist and report on it instead."
                  % (len(touched), ", ".join(base(t) for t in touched[:8])), file=sys.stderr)
            return 1
        run["closed"] = now()
        run["closed_reason"] = " ".join(flags.get("note", [])) or "no wiring change was produced"
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
            how = gate["check"]["type"]
            when = project_when(gate)
            print("   %-8s [%s|%s|%s] %s"
                  % (gate["id"], gate["severity"], when if isinstance(when, str)
                     else "+".join(when),
                     "probe" if how == "probe" else ("auto" if how != "manual" else "by hand"),
                     gate["title"]))
            if "full" in flags:
                print("            %s" % gate["rule"])
    print("\n%d gate(s)." % total)
    return 0


def cmd_scopes(argv):
    for name in sorted(SCOPES):
        print("  %-10s %s" % (name, SCOPES[name][2]))
    print("\n  Narrow a run with --scope <name> on start or scan, and widen it again\n"
          "  with --scope all. Every report leads with what a scoped run left out.")
    return 0


# ---------------------------------------------------------------- hooks


def invokes(called, skill):
    # A skill installed through a plugin is invoked as "plugin:skill", so the
    # bare name alone would miss the invocation that arrives namespaced.
    return called == skill or called.endswith(":" + skill)


def cmd_hook_skill():
    """PostToolUse(Skill): an invocation opens its own run, so none is skipped."""
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    called = str((payload.get("tool_input") or {}).get("skill") or "")
    if not invokes(called, "stack-checklist"):
        return 0
    mine = session_id(payload)
    if session_pointer(mine).exists():
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    run = open_run([project_root(cwd)], "Stack skill invocation", session=mine)
    print("A stack run (%s) was opened for this invocation. Scope it before starting:\n"
          "  %s scan --kind <%s> --target <path> --url https://<host>\n"
          "Take the probes first - the service gates take no hand answer - then verify, change "
          "what fails, answer every gate, and report. The session cannot end with it open."
          % (run["id"], TOOL, "|".join(KINDS)), file=sys.stderr)
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

    Unlike its siblings this one never opens a run. Editing a component is design
    work and editing a template is SEO work, but editing a config file is not
    evidence that anybody wanted the whole stack checked - and putting eighty gates
    in front of a one-line change is the failure the design run already learned.
    """
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    path = (payload.get("tool_input") or {}).get("file_path")
    if not path:
        return 0
    here = session_id(payload)
    run = load_run(required=False, session=here, own_only=True)
    if not run or run.get("closed"):
        return 0
    touched = run.setdefault("touched", [])
    resolved = str(Path(path).resolve())
    if resolved not in touched:
        touched.append(resolved)
        save_run(run)
    return 0


def cmd_guard_stop():
    """Stop hook: refuse to end a session that left its own stack run open."""
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
    groups, _, _ = refresh(run)
    save_run(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    if not open_ids and run.get("report_at"):
        return 0
    index = gate_index(groups)
    probes = [g for g in open_ids if index[g]["check"]["type"] == "probe"]
    unruled = [p for p, r in (run.get("files") or {}).items() if r["status"] == "open"]
    detail = ("%d gate(s) unanswered: %s." % (len(open_ids), ", ".join(open_ids[:15]))
              if open_ids else "the filled checklist has not been rendered.")
    if probes:
        detail += ("\n\n%d of them answer to a request and take no hand answer: %s"
                   % (len(probes), ", ".join(probes[:10])))
    if unruled:
        detail += ("\n\n%d file(s) in scope have not been read and ruled on:\n%s"
                   % (len(unruled), "\n".join("  " + p for p in sorted(unruled)[:10])))
    print("Stack run %s over %s is still open - %s\n"
          "\n"
          "This is a build checklist. Change the project until the sweep passes, then:\n"
          "  %s all --root %s --out /tmp/stack-probe.json\n"
          "  %s probe --file /tmp/stack-probe.json\n"
          "  %s verify\n"
          "  %s resolve --batch - <<'JSON' ... JSON\n"
          "  %s report\n"
          "  %s finish\n"
          "\n"
          "A run that changed nothing closes with: %s finish --no-deliverable"
          % (run["id"], ", ".join(run["targets"]), detail, PROBE_TOOL, run["targets"][0],
             TOOL, TOOL, TOOL, TOOL, TOOL, TOOL),
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


# Two gates answer to the state after a landing rather than the state before it.
# SVC-33 asks whether production serves the commit `main` points at, and
# production cannot serve a commit that has not landed. OBS-05 asks whether a
# real error travelled the whole path, and a reporting snippet cannot fire from
# a page that was never built. Holding a landing until they pass is holding it
# until it has already happened, so they sit outside the set that holds one back.
POST_LANDING = {"SVC-33", "OBS-05"}


def cmd_guard_land():
    """PreToolUse(Bash): refuse to land work while the calling session's stack run is open."""
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

    blocking = [g for g in open_ids if g not in POST_LANDING]
    if len(blocking) < len(open_ids):
        # A bookkeeping gate asks whether every applicable gate has been
        # answered, and a post-landing gate cannot be answered until the landing
        # happens. Left in the set, PRC-06 holds the only move able to clear it
        # and the run cannot finish from either end. So the bookkeeping stands
        # aside behind a post-landing gate rather than closing while one is open.
        blocking = [g for g in blocking if not g.startswith("PRC-")]
    if not blocking:
        return 0

    print("BLOCKED: stack run %s is still open over %s.\n"
          "\n"
          "%d gate(s) unanswered: %s\n"
          "\n"
          "Landing now ships wiring the checklist has not been through. Work it, then land:\n"
          "  %s verify\n"
          "  %s report\n"
          "  %s finish"
          % (run["id"], ", ".join(run["targets"]), len(blocking), ", ".join(blocking[:12]),
             TOOL, TOOL, TOOL), file=sys.stderr)
    return 2


# ------------------------------------------------------------------ repair

# The engine is loaded by path as often as by name, so its own directory is
# not on the path by the time this runs.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_fixes  # noqa: E402

# A gate here either describes a file this repository knows the correct form of,
# or a service that is not answering. Only the first kind has an entry: no edit in
# a working tree makes a paused project respond.
FIXERS = {
    "prettier_config": stack_fixes.fix_prettier_config,
    "eslint_config": stack_fixes.fix_eslint_config,
    "npm_script": stack_fixes.fix_scripts,
    "lint_wired": stack_fixes.fix_scripts,
    "ci_runs": stack_fixes.fix_ci_workflow,
    "editor_ignored": stack_fixes.fix_editor_ignored,
    "calver": stack_fixes.fix_calver,
}


def cmd_fix(argv):
    """Apply every repair the failing gates have, then re-sweep to prove it landed."""
    run = load_run()
    groups, ctx, project = refresh(run)
    index = gate_index(groups)
    applied, declined, seen = [], [], set()
    for gid in run["order"]:
        gate = index[gid]
        kind = gate["check"]["type"]
        fixer = FIXERS.get(kind)
        fn = CHECKS.get(kind)
        if not fixer or fn is None:
            continue
        status, _ = fn(gate, project, run, ctx)
        if status != FAIL:
            continue
        key = (kind, id(fixer))
        changed = [] if key in seen else (fixer(gate, project, run, ctx) or [])
        seen.add(key)
        if changed:
            applied.append((gid, gate["title"], changed))
        else:
            declined.append(gid)
    if not applied:
        if declined:
            print("No repair landed. %d failing gate(s) have a fixer that declined:\n  %s"
                  % (len(declined), ", ".join(declined)))
        else:
            print("Nothing to repair: no failing gate has a mechanical fix.")
        return 0
    for gid, title, changed in applied:
        print("  fixed %-8s %-38s %s" % (gid, title[:38], "; ".join(changed[:4])))
    if declined:
        print("  declined %s" % ", ".join(declined))
    print("\n%d gate(s) repaired. Re-sweeping." % len(applied))
    return cmd_verify([])


COMMANDS = {
    "start": cmd_start, "scan": cmd_scan, "services": cmd_services, "probe": cmd_probe,
    "verify": cmd_verify, "resolve": cmd_resolve, "delegate": cmd_delegate, "fix": cmd_fix, "uninstrumented": cmd_uninstrumented, "rail-probe": cmd_rail_probe,
    "status": cmd_status, "report": cmd_report, "finish": cmd_finish, "gates": cmd_gates,
    "files": cmd_files, "file-clear": cmd_file_clear, "scopes": cmd_scopes,
}
HOOKS = {
    "hook-skill": cmd_hook_skill, "hook-read": cmd_hook_read, "hook-edit": cmd_hook_edit,
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
