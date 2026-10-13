#!/usr/bin/env python3
"""Drives the design skill's rules into the work, so every one of them lands rather than being browsed.

This is a build checklist, not an audit. A gate that is not met is work to do; the
only ways past one are changing the code or naming the condition that makes it
inapplicable. A gate the sweep failed cannot be recorded as passing, and a gate
marked fixed reopens if the next sweep still fails it. The report at the end is a
record of what changed, not a survey of what was found.


The design skill knows a great deal - the slop test, the polish checklist, the UX
guideline set, the brand and asset rules. Left as prose it gets read the way prose
gets read: the model picks what looks relevant and the rest goes unchecked. Nothing
about that is visible afterwards, because an unchecked item and a passing one look
identical in the output.

So the checklist is data, the applicable subset is computed rather than chosen, and
the run cannot close with an item left silent. Gates that a regex can settle are
settled here; the rest are answered by the model with its evidence recorded beside
them. A check that cannot reach a verdict says so and still needs the answer - an
unimplemented check must never read as a pass.

    start     open a run and print the gates it must satisfy
    scan      re-derive what the target contains, and therefore which gates apply
    verify    run every automated check and record its verdict
    resolve   answer a gate: pass, fixed, or n/a, with the evidence
              --batch <path|-> answers many at once, a status and a note each
    status    what is still outstanding; --full adds each gate's rule and fix
    report    render the filled checklist for the user
    finish    close the run - refuses while anything is unanswered
    gates     print the registry, whole or filtered
    files     the file ledger: what is still to be read and ruled on
    file-clear  rule a file as needing no design change, with the reason
                --batch <path|-> clears many at once, a reason each
    skeleton-probe  record a browser capture of the loading and loaded states
    cohesion  compare every file against the others for design mismatches
    spacing-probe  record a rendered capture of the space between things
    layout-probe   record a rendered capture of headline wrap, fold, and bar height
    mobile-probe   record a phone capture of reach, menu fit, rail clearance, and taps
    read      record the design read: page kind, audience, vibe, constraints
    scopes    the named scopes a run can be narrowed to

    guard-stop   Stop hook: refuse to end a session with a run still open
    guard-land   PreToolUse hook: refuse `git commit` / `gh pr create` mid-run
    guard-skill  PreToolUse hook: point a design invocation at this checklist
    hook-skill   PostToolUse hook: open a run when the design skill is invoked
    hook-edit    PostToolUse hook: open a run when a design surface is edited
    hook-read    PostToolUse hook: credit a file in the ledger once it is read
Answers already given are carried into a later run when neither the gate, this
tool, nor any file in scope has moved since, so a repeat run works only what
actually changed. --fresh on start or verify re-asks everything, and
INTELLIGENCE_PASS_NO_CACHE=1 turns it off everywhere. Answers that reach past
the project are never carried.
"""

import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
import zlib
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
    ENGINE = HOME / ".claude" / "checklists" / "design"
GATES_DIR = ENGINE / "checklist" / "gates"

# Every instruction this file prints has to name a command that exists where it is
# printed. A cloud container has the repository's copy and no ~/.claude at all, so
# a message pointing at ~/.claude/tools/design-pass.py names nothing and the run
# never opens. This resolves to whichever copy is doing the talking.
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
    # A cloud session cannot write under ~/.claude, so the ledger lives beside it
    # there. Runs are per-session state and are deliberately not synced.
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return Path.home() / ".pass-state"
    return HOME / ".claude" / "state"


RUNS = _state_root() / "design-runs"
CURRENT = RUNS / "current.json"


def session_id(payload=None):
    """Which Claude Code session this call belongs to, or None if it cannot tell.

    A run is per-session state, but the pointer to it used to be one file for the
    whole machine: two sessions on the same Mac shared it, so a run opened while
    working on one project refused to let an unrelated session end, and the
    second session's run silently replaced the first one's pointer. Hooks are
    handed the id in their payload and everything else reads it from the
    environment, so the two agree.
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
    """The run pointer for one session, or the shared one when there is no id."""
    if not session:
        return CURRENT
    return RUNS / ("current-%s%s.json" % (session, workspace_suffix()))

KINDS = ["web-ui", "component", "email", "logo", "icon", "banner", "social", "slides", "brand", "tokens", "cip"]

SOURCE_EXT = {".css", ".scss", ".sass", ".less", ".html", ".htm", ".jsx", ".tsx", ".js", ".ts",
              ".vue", ".svelte", ".astro", ".md"}

# What the reader is shown. Markdown stays in the sweep, because a README is a file
# the ledger holds and the copy pass reads - but it is writing about the design
# rather than the design. Read as markup it fails on its own subject matter: a
# theming note quoting `background: #000` is a colour literal, a badge's alt text
# carries the em-dash the copy rule forbids, a fenced CSS example puts a spacing
# value off the scale, and a heading called Navigation makes the file part of the
# bar. Every one of those is a finding no edit to the design can clear. A check
# whose subject is prose reads the tree and says so.
SERVED_EXT = SOURCE_EXT - {".md"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".avif"}

# Substrings that mark a path as not ours to judge. A dependency's slop
# is not this run's slop, and sweeping it buries the files that are.
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "out", "vendor", "__pycache__",
             ".venv", "coverage", ".turbo", ".cache", ".intelligence"}


# ---------------------------------------------------------------- registry


def load_gates():
    """Read every gate file, applying each file's defaults to the gates inside it."""
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
            # A gate with nothing behind it records whatever it is told and enforces
            # nothing, which is indistinguishable in a report from a rule that held.
            # The rule still stands in the skill's prose; what it does not do is
            # occupy a checklist as though a machine were watching it.
            if CHECKS.get(merged["check"].get("type")) is None:
                continue
            gates.append(merged)
        if gates:
            groups.append({"group": data["group"],
                           "description": data.get("description", ""), "gates": gates})
    return groups


def all_gates(groups):
    return [g for grp in groups for g in grp["gates"]]


# ---------------------------------------------------------------- context


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


def iter_files(targets):
    """Every file under the run's targets, minus the trees nobody should be judged on."""
    seen = []
    for target in targets:
        p = Path(target).expanduser()
        if p.is_file():
            seen.append(p)
        elif p.is_dir():
            found = [c for c in p.rglob("*")
                     if c.is_file() and not any(part in SKIP_DIRS for part in c.parts)]
            # What git ignores is build output or local state, never something
            # a person rules on. The skip list cannot keep up with what each
            # project names its build.
            ignored = _ignored_paths(p, found)
            seen.extend(c for c in found if str(c) not in ignored)
    return seen


DATA_ISLAND = re.compile(
    r"<script[^>]*type\s*=\s*[\"\']application/(?:ld\+)?json[\"\'][^>]*>.*?</script>",
    re.S | re.I,
)


def blank(match):
    """Drop a span's content but keep its newlines, so reported line numbers hold."""
    return "\n" * match.group(0).count("\n")


def read_sources(targets):
    """The source text of the run's targets, keyed by path, for the regex checks.

    Read on a thread pool: a large tree is hundreds of small files and the cost is
    waiting on the disk, not parsing them.
    """
    wanted = [f for f in iter_files(targets)
              if f.suffix.lower() in SOURCE_EXT and f.stat().st_size < 2_000_000]
    out = {}
    if not wanted:
        return out

    def one(path):
        try:
            return str(path), DATA_ISLAND.sub(blank, path.read_text(errors="replace"))
        except OSError:
            return None

    if len(wanted) < 24:
        for path in wanted:
            got = one(path)
            if got:
                out[got[0]] = got[1]
        return out
    import concurrent.futures as futures
    with futures.ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 4)) as pool:
        for got in pool.map(one, wanted):
            if got:
                out[got[0]] = got[1]
    return out


def image_files(targets):
    return [f for f in iter_files(targets) if f.suffix.lower() in IMAGE_EXT]


CONTEXT_SIGNS = {
    "css": r"\.(css|scss|sass|less)$|<style|className=|class=|style=\{",
    "html": r"\.(html|htm|jsx|tsx|vue|svelte|astro)$",
    "react": r"\.(jsx|tsx)$",
    "interactive": r"<button|<a\s|role=[\"']button|onClick|onPress|Pressable|<Touchable|cursor:\s*pointer|:hover",
    "form": r"<form|<input|<textarea|<select|useForm|<Form\b|TextInput",
    "nav": r"<nav\b|role=[\"']navigation|NavBar|Navigation|<header\b|Sidebar",
    "footer": r"<footer\b|Footer",
    # A bar that opens something. The panel gates only apply where one exists,
    # and a flat row of links is NAV-14's problem rather than theirs.
    "panel": (r"aria-expanded|aria-haspopup|mega-?menu|MegaMenu|megaMenu"
              r"|nav__panel|site-header__panel|NavDisclosure|Disclosure\b"),
    "hero": r"hero|Hero|<h1\b",
    "chart": r"chart\.js|Chart\.js|recharts|Recharts|d3\b|ApexCharts|echarts|<canvas|VictoryChart",
    "image": r"<img\b|<picture\b|background-image|next/image|<Image\b",
    "video": r"<video\b",
    "motion": r"@keyframes|transition|animate|framer-motion|motion\.|useSpring|Animated\.",
    "framer": r"framer-motion|motion\.",
    "sticky": r"position:\s*sticky|position:\s*fixed|sticky\s|fixed\s",
    "grid": r"grid-template-columns|grid-cols-",
    "table": r"<table\b|DataTable|<Table\b",
    "modal": r"<dialog|Dialog|Modal|Drawer|Sheet|Popover|role=[\"']dialog",
    "toast": r"toast|Toast|Sonner|notification",
    "tooltip": r"[Tt]ooltip",
    "carousel": r"[Cc]arousel|[Ss]lider|swiper|autoplay",
    "tabs": r"<Tabs|role=[\"']tab|type=[\"']radio",
    "drag": r"draggable|onDrag|DndContext|pointerdown",
    "fetch": r"fetch\(|useQuery|useSWR|axios|getServerSideProps|loading|isLoading|Skeleton",
    "list": r"\.map\(|<ul\b|<ol\b|FlatList",
    "router": r"useRouter|<Link\b|react-router|next/link|createBrowserRouter",
    "search": r"[Ss]earch",
    "font": r"@font-face|font-family|next/font|fonts\.googleapis",
    "svg": r"<svg\b",
    "tailwind": r"tailwind|@apply|class(Name)?=\"[^\"]*\b(flex|grid|text-|bg-|p-\d)",
    "data": r"<table|price|Price|\$\{?\d|toLocaleString",
    "mobile": r"react-native|SafeAreaView|Flutter|SwiftUI|@media[^{]*max-width:\s*(4[0-9][0-9]|3[0-9][0-9])px",
    "macrostructure": r"macrostructure",
    "study": r"studied-DNA",
    "web-build": r"vite\.config|next\.config|webpack|rollup|package\.json",
    "tokens": r"--color-|--space-|:root\s*\{|@theme",
    "quote": r"<blockquote|[Tt]estimonial|<Quote\b|quote-|\bquotes?\b",
    "logo-wall": r"[Tt]rusted by|[Uu]sed by|[Bb]acked by|[Cc]ustomers include|logo-?[Ww]all|LogoCloud|logo-cloud",
    "copy": r".",
}

# A marketing surface and an app screen are both web-ui, and much of what governs
# the first is wrong on the second: hero fold, eyebrow density, one accent for the
# whole page, no scroll cue. Neither announces itself in a file extension, so it is
# read from what the source contains, and declared outright when the reading is wrong.
MARKETING_SIGNS = (
    r"\bhero\b", r"testimonial", r"\bpricing\b", r"trusted by", r"used by",
    r"frequently asked", r"\bfaq\b", r"newsletter", r"\bsubscribe\b",
    r"get started", r"book a demo", r"request a demo", r"\blanding\b",
    r"\bwaitlist\b", r"case stud", r"\bportfolio\b", r"selected work",
    r"our services", r"\bmanifesto\b", r"start a project",
)

APP_SIGNS = (
    r"\bdashboard\b", r"<Sidebar|SideNav|AppShell|<Shell\b",
    r"RequireAuth|ProtectedRoute|withAuth|AuthGuard",
    r"useSession|getServerSession|supabase\.auth|signIn\(|currentUser",
    r"DataTable|useReactTable|<DataGrid", r"\badmin\b", r"react-admin",
    r"\bworkspace\b", r"\btenant\b", r"\bsettings page\b",
)


def infer_marketing(blob, run, ctx):
    """Whether the target is a marketing surface rather than an app screen.

    Declared flags win outright, because the reading is a guess and a wrong guess
    either fires forty inapplicable gates at a dashboard or lets a landing page
    past every rule written for one.
    """
    flags = set(run.get("flags", []))
    if "marketing" in flags:
        return True
    if "app" in flags:
        return False
    if run["kind"] not in {"web-ui", "slides"}:
        return False
    low = blob.lower()
    marketing = sum(1 for p in MARKETING_SIGNS if re.search(p, low))
    app = sum(1 for p in APP_SIGNS if re.search(p, blob, re.I))
    if app > marketing:
        return False
    return marketing >= 3 or (ctx.get("hero") and marketing >= 2)


def build_context(run):
    """Which conditions the target actually meets, so gates scope themselves."""
    tree = read_sources(run["targets"])
    sources = {path: text for path, text in tree.items()
               if Path(path).suffix.lower() in SERVED_EXT}
    blob = "\n".join(sources.keys()) + "\n" + "\n".join(sources.values())
    ctx = {"always": True}
    for name, pattern in CONTEXT_SIGNS.items():
        # MULTILINE matters: several signs anchor on a file extension with `$`,
        # and the blob is one string of filenames followed by their contents.
        ctx[name] = bool(re.search(pattern, blob, re.M))
    imgs = image_files(run["targets"])
    ctx["png_export"] = any(f.suffix.lower() in {".png", ".webp"} for f in imgs)
    ctx["svg_export"] = any(f.suffix.lower() == ".svg" for f in imgs)
    ctx["asset_export"] = bool(imgs)
    ctx["renderable"] = bool(ctx["html"] or ctx["css"] or imgs)
    ctx["icons"] = bool(re.search(r"lucide|heroicons|phosphor|react-icons|material-icons|<svg", blob, re.I))
    ctx["brand_asset"] = bool(re.search(r"logo|Logo|wordmark|brand", blob)) or run["kind"] in {"logo", "brand", "cip"}
    ctx["copy"] = True
    # Declared by the operator rather than inferred - a redesign, an ad, and a print
    # job all look like their neighbours in source.
    for flag in run.get("flags", []):
        ctx[flag] = True
    for flag in ("redesign", "ad", "print"):
        ctx.setdefault(flag, False)
    if not ctx["web-build"]:
        ctx["web-build"] = any(f.name in {"package.json", "vite.config.ts", "vite.config.js",
                                          "next.config.js", "next.config.mjs", "next.config.ts"}
                               for f in iter_files(run["targets"]))
    ctx["marketing"] = infer_marketing(blob, run, ctx)
    ctx["app"] = bool(ctx["html"] or ctx["react"]) and not ctx["marketing"]
    return ctx, sources



# ---------------------------------------------------------------- scopes

# A run answers for the whole deliverable by default, and PRC-08 exists to stop
# that being narrowed quietly. A scope is the other thing: a narrowing said out
# loud. It is recorded on the run, every report leads with it and with what it
# did not look at, and finish repeats it, so a scoped pass can never be read as a
# full one. What it changes is which gates are asked, never how hard any of them
# is to answer.

SCOPES = {
    "nav": (["NAV"], ["SLP-42", "TST-60", "TST-61", "HDR-02", "HDR-03", "HDR-04"],
            "the navigation bar and its panels"),
    "footer": (["FTR"], ["SLP-43", "PROD-08", "PROD-06"], "the footer"),
    "hero": ([], ["TST-01", "TST-02", "TST-03", "TST-04", "TST-05", "TST-06", "TST-07",
                  "TST-21", "SLP-06", "SLP-44"], "the hero"),
    "fields": (["FLD", "UXF"], ["PROD-18"], "inputs, selects, menus, and forms"),
    "tables": (["TBL", "UXR"], [], "tables and data rows"),
    "code": (["COD"], [], "code blocks, command surfaces, and the prompts a page ships"),
    "motion": (["POL"], ["SLP-10", "SLP-11", "SLP-12", "SLP-13", "SLP-14", "SLP-15",
                         "TST-49", "TST-50", "TST-51", "TST-52", "TST-53"],
               "animation, transitions, and motion states"),
    "loading": (["SKL"], [], "loading placeholders and their correspondence"),
    "spacing": (["SPC"], ["UNI-07", "SYS-03"], "the space between things"),
    "colour": ([], ["UNI-01", "UNI-02", "UNI-08", "TST-31", "TST-32", "TST-35", "TST-36",
                    "TST-37", "SLP-07", "SLP-22", "SLP-23"], "colour, themes, and surfaces"),
    "type": (["UXY"], ["REF-01", "REF-02", "REF-03", "SLP-01", "SLP-37", "SLP-38", "SLP-38a",
                       "SLP-51", "SLP-55", "TST-33", "TST-34"], "typography"),
    "copy": ([], ["UNI-03", "PRC-03", "PRC-04", "SLP-19", "SLP-46",
                  "TST-38", "TST-39", "TST-40", "TST-41", "TST-42", "TST-43"],
             "every word a reader sees"),
    "a11y": (["UXA"], ["UNI-08", "PTR-09", "PTR-10", "PTR-11", "PTR-12", "UXL-01", "UXL-04"],
             "accessibility"),
    "images": (["PLT"], ["UXP-01", "UXP-02", "UNI-09", "UNI-11", "SLP-33",
                         "TST-25", "TST-26", "TST-44", "TST-45", "TST-46", "TST-47", "TST-48"],
               "images, icons, logos, drawn plates, and the favicon"),
    "plates": (["PLT"], ["TST-48", "UNI-09"], "art the project drew for itself"),
    "pointer": (["PTR"], [], "what the interface does under a cursor and a finger"),
    "cohesion": (["COH"], [], "each file against the others"),
    "tokens": (["SYS"], ["UNI-02", "UNI-07"], "the token layer"),
    "brand": (["BRD"], ["UNI-10", "UNI-11"], "brand marks and their use"),
    "production": (["PROD"], [], "what a live site owes before it ships"),
    "slop": (["SLP"], [], "the anti-default test"),
    "marketing": (["TST"], [], "what a landing page owes that an app screen does not"),
    "mobile": (["MOB"], ["NAV-19", "NAV-20", "UXL-01", "UXL-04", "UXL-10", "TST-61"],
               "what a phone decides: reach, fit, and the thumb"),
}

# The run answers for itself whatever the scope: opened, swept, reported, closed.
SCOPE_ALWAYS = {"run_opened", "all_resolved", "report_emitted", "writing_pass"}


def scope_gates(scope):
    """The gate ids and prefixes one scope selects, or None when it is not a scope."""
    return SCOPES.get(scope)


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
            "deliverable was not looked at." % (scope, what))

def applicable(gate, run, ctx):
    if not in_scope(gate, run.get("scope")):
        return False, "outside the %s scope this run declared" % run["scope"]
    kinds = gate["kinds"]
    if "*" not in kinds and run["kind"] not in kinds and not (set(kinds) & set(run.get("extra_kinds", []))):
        return False, "kind %s is out of scope for this gate" % run["kind"]
    when = gate["when"]
    # A gate may name several conditions, and then all of them have to hold. One
    # condition alone cannot separate a hero rule meant for a landing page from an
    # app screen that happens to contain an <h1>.
    for condition in (when if isinstance(when, list) else [when]):
        if not ctx.get(condition, False):
            return False, "target contains no %s" % condition
    return True, ""


# ---------------------------------------------------------------- checks

PASS, FAIL, UNKNOWN = "pass", "fail", "inconclusive"

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2b00-\u2bff\ufe0f\u2705\u274c]"
)
TOKEN_BLOCK = re.compile(r"(:root|\[data-theme[^\]]*\]|@theme)\s*\{[^}]*\}", re.S)


def _hits(sources, pattern, flags=re.I):
    out = []
    rx = re.compile(pattern, flags)
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                out.append("%s:%d" % (Path(path).name, i))
    return out


CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _css_blocks(text):
    return re.findall(r"([^{}]+)\{([^{}]*)\}", CSS_COMMENT.sub(blank, text))


def _forbidden(spec):
    """The one or many patterns a forbid check is written with.

    Gate files declare either `pattern` or `patterns`; reading only the first
    made every gate written the plural way raise KeyError and come back
    inconclusive, which reads on the report as the deliverable not being
    measurable rather than as the check not having run."""
    if "pattern" in spec:
        return spec["pattern"]
    # A global flag is only legal at the very start of an expression, so any
    # a pattern carries is lifted to the front of the joined one rather than
    # left where it would sit in the middle of an alternation.
    flags, parts = "", []
    for raw in spec["patterns"]:
        body = raw
        while body.startswith("(?") and body[2:3] in "aiLmsux" and ")" in body[:8]:
            head, _, body = body.partition(")")
            for flag in head[2:]:
                if flag not in flags:
                    flags += flag
        parts.append("(?:%s)" % body)
    prefix = "(?%s)" % flags if flags else ""
    return prefix + "|".join(parts)


def check_forbid_regex(gate, sources, run, ctx):
    spec = gate["check"]
    hits = _hits(sources, _forbidden(spec))
    floor = spec.get("min_count", 1)
    if len(hits) >= floor:
        return FAIL, "%s - %d occurrence(s): %s" % (spec.get("message", "forbidden pattern"), len(hits), ", ".join(hits[:6]))
    return PASS, "no occurrences"


def check_require_regex(gate, sources, run, ctx):
    spec = gate["check"]
    mode = spec.get("mode", "any")
    found, missing = [], []
    for pattern in spec["patterns"]:
        hits = _hits(sources, pattern)
        (found if hits else missing).append(pattern)
    if mode == "all":
        if missing:
            return FAIL, "%s missing: %s" % (spec.get("message", "required pattern"), ", ".join(missing))
        return PASS, "all present"
    if found:
        return PASS, "found %s" % found[0]
    return FAIL, spec.get("message", "required pattern absent")


check_require_any_regex = check_require_regex


def check_require_file(gate, sources, run, ctx):
    """One of the named path suffixes exists under a target. For files whose
    presence is the whole point (robots.txt, sitemap.xml, app/not-found.tsx),
    where a content regex would miss the empty file that ships and answers the
    request. Paths are matched as suffixes so 'public/robots.txt' catches it
    anywhere under the tree."""
    spec = gate["check"]
    suffixes = [p.replace("\\", "/") for p in spec["paths"]]
    mode = spec.get("mode", "any")
    found, missing = [], []
    for suffix in suffixes:
        hit = False
        for target in run["targets"]:
            base = Path(target)
            if base.is_file() and str(base).replace("\\", "/").endswith(suffix):
                hit = True
                break
            if base.is_dir():
                for child in base.rglob("*"):
                    if child.is_file() and str(child).replace("\\", "/").endswith(suffix):
                        hit = True
                        break
                if hit:
                    break
        (found if hit else missing).append(suffix)
    if mode == "all":
        if missing:
            return FAIL, "%s missing: %s" % (spec.get("message", "required file"), ", ".join(missing))
        return PASS, "all present"
    if found:
        return PASS, "found %s" % found[0]
    return FAIL, spec.get("message", "required file absent") + " - looked for: " + ", ".join(suffixes)


def check_require_regex_if(gate, sources, run, ctx):
    spec = gate["check"]
    if not _hits(sources, spec["if_pattern"], re.I | re.S):
        return PASS, "condition not present"
    for pattern in spec["patterns"]:
        if _hits(sources, pattern):
            return PASS, "guard present"
    return FAIL, spec.get("message", "guard absent")


TOKEN_SELECTOR = re.compile(r":root|data-theme|@theme|prefers-color-scheme", re.I)


def strip_token_blocks(text):
    """Blank every declaration block that defines tokens, keeping line numbers."""
    def drop(match):
        return blank(match) if TOKEN_SELECTOR.search(match.group(1)) else match.group(0)
    return re.sub(r"([^{}]+)\{([^{}]*)\}", drop, text)


def check_forbid_regex_outside_tokens(gate, sources, run, ctx):
    spec = gate["check"]
    rx = re.compile(spec["pattern"])
    offenders = []
    for path, text in sources.items():
        stripped = strip_token_blocks(text)
        for i, line in enumerate(stripped.splitlines(), 1):
            bare = re.sub(r"//.*|/\*.*?\*/", "", line)
            if rx.search(bare):
                offenders.append("%s:%d %s" % (Path(path).name, i, bare.strip()[:60]))
    if offenders:
        return FAIL, "%s - %d: %s" % (spec.get("message", "literal outside tokens"), len(offenders), " | ".join(offenders[:5]))
    return PASS, "every colour resolves through a token"


def check_forbid_regex_in_state(gate, sources, run, ctx):
    spec = gate["check"]
    state, rx = spec["state"], re.compile(spec["pattern"], re.I)
    offenders = []
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if state in selector and rx.search(body):
                offenders.append("%s: %s" % (Path(path).name, selector.strip()[:50]))
    if offenders:
        return FAIL, "%s - %s" % (spec.get("message", "forbidden in state"), "; ".join(offenders[:5]))
    return PASS, "state rules are clean"


def check_forbid_emoji(gate, sources, run, ctx):
    offenders = []
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            if EMOJI.search(line):
                offenders.append("%s:%d" % (Path(path).name, i))
    for f in iter_files(run["targets"]):
        if EMOJI.search(f.name):
            offenders.append("filename %s" % f.name)
    if offenders:
        return FAIL, "emoji at %s" % ", ".join(offenders[:8])
    return PASS, "none"


def check_active_scale_range(gate, sources, run, ctx):
    spec = gate["check"]
    lo, hi = spec["min"], spec["max"]
    bad, seen = [], 0
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if ":active" not in selector:
                continue
            for value in re.findall(r"scale\(([\d.]+)\)", body):
                seen += 1
                if not (lo <= float(value) <= hi):
                    bad.append("%s scale(%s)" % (selector.strip()[:40], value))
        for value in re.findall(r"active:scale-(\d+)", text):
            seen += 1
            if not (lo <= int(value) / 100 <= hi):
                bad.append("active:scale-%s" % value)
    if bad:
        return FAIL, "press scale out of %s-%s: %s" % (lo, hi, "; ".join(bad[:5]))
    if seen:
        return PASS, "%d press scale(s) in range" % seen
    return UNKNOWN, "no numeric press scale found - confirm the press feedback by hand"


def check_hover_effect_stack(gate, sources, run, ctx):
    limit = gate["check"].get("max", 2)
    props = ("translate", "scale", "rotate", "box-shadow", "background", "color", "border-color", "opacity")
    bad = []
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if ":hover" not in selector or "autofill" in selector:
                continue
            used = {p for p in props if p in body}
            if "translate" in used and "scale" in used:
                used.discard("translate")
            if len(used) > limit:
                bad.append("%s: %s" % (selector.strip()[:40], ", ".join(sorted(used))))
    if bad:
        return FAIL, "stacked hover effects - %s" % "; ".join(bad[:4])
    return PASS, "hover effects are single-purpose"


def check_overshoot_bezier(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        for match in re.finditer(r"cubic-bezier\(([^)]+)\)", text):
            try:
                nums = [float(n) for n in match.group(1).split(",")]
            except ValueError:
                continue
            if len(nums) == 4 and (nums[1] > 1.05 or nums[3] > 1.05 or nums[1] < -0.05 or nums[3] < -0.05):
                bad.append("%s %s" % (Path(path).name, match.group(0)))
    if bad:
        return FAIL, "overshoot curve on UI state: %s" % "; ".join(bad[:4])
    return PASS, "no overshoot curves"


def check_animated_layout_property(gate, sources, run, ctx):
    layout = r"\b(width|height|top|left|right|bottom|margin|padding|font-size)\b"
    bad = []
    for path, text in sources.items():
        for match in re.finditer(r"transition(-property)?\s*:\s*([^;}]+)", text, re.I):
            if re.search(layout, match.group(2)):
                bad.append("%s: transition: %s" % (Path(path).name, match.group(2).strip()[:50]))
        for block in re.findall(r"@keyframes[^{]*\{(.*?)\n\}", text, re.S):
            if re.search(layout + r"\s*:", block):
                bad.append("%s: keyframe animates a layout property" % Path(path).name)
    if bad:
        return FAIL, "; ".join(dict.fromkeys(bad))[:400]
    return PASS, "only transform and opacity animate"


def check_duration_cap(gate, sources, run, ctx):
    cap = gate["check"].get("max_ms", 300)
    exempt = tuple(gate["check"].get("exempt", []))
    bad = []
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if any(word in selector.lower() for word in exempt):
                continue
            # `s` and `ms` both appear in transition shorthands; normalise before
            # comparing, or a 0.2s transition reads as 0.2 and always passes.
            for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s)\b", body):
                ms = float(value) * (1 if unit == "ms" else 1000)
                if cap < ms < 10000:
                    bad.append("%s %s%s" % (selector.strip()[:36], value, unit))
    if bad:
        return FAIL, "over %dms: %s" % (cap, "; ".join(bad[:5]))
    return PASS, "UI motion stays under %dms" % cap


def check_state_coverage(gate, sources, run, ctx):
    wanted = gate["check"]["states"]
    blob = "\n".join(sources.values())
    missing = []
    for state in wanted:
        bare = state.lstrip(":")
        if not re.search(re.escape(state), blob) and not re.search(r"\b%s:" % re.escape(bare), blob):
            missing.append(state)
    if missing:
        return FAIL, "no styling found for %s" % ", ".join(missing)
    return PASS, "all of %s present" % ", ".join(wanted)


def check_outline_none_orphan(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        if re.search(r"outline:\s*(none|0)\b", text, re.I) and not re.search(r":focus-visible", text):
            bad.append(Path(path).name)
    if bad:
        return FAIL, "outline removed with no focus-visible replacement in %s" % ", ".join(bad[:4])
    return PASS, "focus is never removed bare"


def check_div_onclick(gate, sources, run, ctx):
    hits = _hits(sources, r"<(div|span|li)[^>]*onClick", re.I)
    if hits:
        return UNKNOWN, "handler on a non-control element at %s - confirm each has role, tabIndex, and key handling, or convert it" % ", ".join(hits[:6])
    return PASS, "clickables are real controls"


def check_measure_range(gate, sources, run, ctx):
    lo, hi = gate["check"]["min"], gate["check"]["max"]
    bad, seen = [], 0
    for path, text in sources.items():
        for value in re.findall(r"(?:max-width|--[a-z0-9-]*measure[a-z0-9-]*):\s*([\d.]+)ch", text, re.I):
            seen += 1
            if not (lo <= float(value) <= hi):
                bad.append("%sch" % value)
    if bad:
        return FAIL, "prose measure outside %d-%dch: %s" % (lo, hi, ", ".join(bad[:5]))
    if seen:
        return PASS, "%d measure(s) in range" % seen
    return UNKNOWN, "no ch-based measure found - confirm prose width by hand"


def check_font_family_count(gate, sources, run, ctx):
    limit = gate["check"].get("max", 3)
    families = set()
    for text in sources.values():
        for value in re.findall(r"font-family:\s*([^;}]+)", text, re.I):
            first = value.split(",")[0].strip().strip("'\"")
            if first and not first.startswith("var(") and first.lower() not in {"inherit", "initial", "unset"}:
                families.add(first.lower())
        for value in re.findall(r"--font-([a-z]+)\s*:", text):
            families.add("token:" + value)
    families = {f for f in families if not f.startswith("token:")} | {f for f in families if f.startswith("token:")}
    if len(families) > limit:
        return FAIL, "%d families: %s" % (len(families), ", ".join(sorted(families)[:8]))
    return PASS, "%d families" % len(families)


def check_italic_heading(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if re.search(r"\b(h[1-6]|__title|__display|hero|wordmark|display)\b", selector, re.I) and \
               re.search(r"font-style:\s*italic", body, re.I):
                bad.append("%s %s" % (Path(path).name, selector.strip()[:40]))
        for match in re.finditer(r"<h[1-6][^>]*>(.*?)</h[1-6]>", text, re.S | re.I):
            if re.search(r"<(em|i)\b", match.group(1), re.I):
                bad.append("%s: em/i inside a heading" % Path(path).name)
        if re.search(r"class(Name)?=\"[^\"]*\bitalic\b[^\"]*\"[^>]*>\s*<h[1-6]", text):
            bad.append("%s: italic utility on a heading" % Path(path).name)
    if bad:
        return FAIL, "; ".join(dict.fromkeys(bad))
    return PASS, "headings are roman"


METRIC = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|x)\s|\b\d{1,3}(?:,\d{3})+\+?\b|\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|days?|weeks?)\s+(?:per|a|each)\b"
    r"|\btrusted by\b|\b\d+(?:\.\d+)?%\s*(?:uptime|faster|more|less|increase|growth|conversion)",
    re.I,
)


def check_invented_metric(gate, sources, run, ctx):
    hits = []
    for path, text in sources.items():
        for match in METRIC.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line_text = text[line_start:line_end if line_end != -1 else len(text)]
            # An asset URL is not prose: font axis ranges and cache-busting digits
            # are not claims anyone reads.
            if re.search(r"https?://|url\(|src\s*=", line_text):
                continue
            line = text[: match.start()].count("\n") + 1
            hits.append("%s:%d %s" % (Path(path).name, line, match.group(0).strip()[:30]))
    if hits:
        return UNKNOWN, "quantitative claims found - confirm every one came from the user: %s" % "; ".join(hits[:8])
    return PASS, "no quantitative claims"


def check_grid_minmax(gate, sources, run, ctx):
    bad = []
    # The grid usually lives in a stylesheet and the image in the markup, so the
    # question is whether the deliverable has images at all, not this one file.
    has_image = re.search(r"<img|<picture|background-image|<Image", "\n".join(sources.values()), re.I)
    for path, text in sources.items():
        for match in re.finditer(r"grid-template-(?:columns|rows):\s*([^;}]+)", text, re.I):
            value = match.group(1)
            if re.search(r"(^|\s)1fr", value) and "minmax(0" not in value and has_image:
                line = text[: match.start()].count("\n") + 1
                bad.append("%s:%d %s" % (Path(path).name, line, value.strip()[:40]))
    if bad:
        return FAIL, "bare 1fr track in an image-bearing grid: %s" % "; ".join(bad[:5])
    return PASS, "image grid tracks use minmax(0, 1fr)"


def check_overflow_clip(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if re.search(r"(html|body)[^{]*\{[^}]*overflow-x:\s*hidden", blob, re.S | re.I):
        return FAIL, "overflow-x: hidden on the root - use clip, which preserves sticky and fixed descendants"
    if re.search(r"overflow-x:\s*clip", blob, re.I):
        return PASS, "overflow-x: clip present on the root"
    return FAIL, "no overflow-x: clip on html and body"


def check_sticky_top_zero(gate, sources, run, ctx):
    count = 0
    for text in sources.values():
        for selector, body in _css_blocks(text):
            if re.search(r"position:\s*sticky", body) and re.search(r"top:\s*0", body):
                count += 1
    if count > 1:
        return FAIL, "%d elements stick at top: 0 - offset the secondary ones to var(--banner-height)" % count
    return PASS, "at most one sticky element at top: 0"


def check_svg_aria(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        for match in re.finditer(r"<svg\b[^>]*>", text, re.I):
            tag = match.group(0)
            if "aria-hidden" not in tag and "aria-label" not in tag and "role=" not in tag:
                line = text[: match.start()].count("\n") + 1
                bad.append("%s:%d" % (Path(path).name, line))
    if bad:
        return FAIL, "%d svg(s) with neither aria-hidden nor a label: %s" % (len(bad), ", ".join(bad[:6]))
    return PASS, "every svg is labelled or hidden"


def check_img_alt(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        for match in re.finditer(r"<img\b[^>]*>", text, re.I):
            if "alt=" not in match.group(0):
                line = text[: match.start()].count("\n") + 1
                bad.append("%s:%d" % (Path(path).name, line))
    if bad:
        return FAIL, "img without alt at %s" % ", ".join(bad[:6])
    return PASS, "every img declares alt"


def check_img_dimensions(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        for match in re.finditer(r"<img\b[^>]*>", text, re.I):
            tag = match.group(0)
            if not (("width=" in tag and "height=" in tag) or "aspect" in tag):
                line = text[: match.start()].count("\n") + 1
                bad.append("%s:%d" % (Path(path).name, line))
    if bad:
        return UNKNOWN, "img without width/height at %s - confirm an aspect-ratio holds the box in CSS" % ", ".join(bad[:6])
    return PASS, "images declare their box"


def check_img_lazy(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"<img", blob, re.I):
        return PASS, "no raster images"
    if re.search(r"loading=[\"']lazy", blob) or re.search(r"next/image|<Image\b", blob):
        return PASS, "lazy loading in play"
    return UNKNOWN, "no loading=\"lazy\" found - confirm which images are below the fold"


def check_icon_button_label(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        for match in re.finditer(r"<button\b[^>]*>(.*?)</button>", text, re.S | re.I):
            tag, inner = match.group(0), match.group(1)
            text_content = re.sub(r"<[^>]+>", "", inner).strip()
            if not text_content and "aria-label" not in tag:
                line = text[: match.start()].count("\n") + 1
                bad.append("%s:%d" % (Path(path).name, line))
    if bad:
        return FAIL, "icon-only button without aria-label at %s" % ", ".join(bad[:6])
    return PASS, "icon-only buttons are labelled"


def check_form_label(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    inputs = len(re.findall(r"<input\b(?![^>]*type=[\"'](hidden|submit|button))", blob, re.I))
    labels = len(re.findall(r"<label\b|<FormLabel", blob, re.I))
    if inputs and labels < inputs:
        return FAIL, "%d input(s) but %d label(s)" % (inputs, labels)
    if inputs:
        return PASS, "%d input(s), %d label(s)" % (inputs, labels)
    return PASS, "no bare inputs"


def check_input_types(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    suspicious = []
    for match in re.finditer(r"<input\b[^>]*>", blob, re.I):
        tag = match.group(0)
        name = (re.search(r"(name|id)=[\"']([^\"']+)", tag) or [None, None, ""])[2]
        if re.search(r"email|phone|tel|url|number|amount|zip", str(name), re.I) and "type=\"text\"" in tag:
            suspicious.append(name)
    if suspicious:
        return FAIL, "text type on semantic fields: %s" % ", ".join(suspicious[:6])
    return PASS, "input types match their fields"


def check_autocomplete_attr(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    fields = re.findall(r"<input\b[^>]*(?:name|id)=[\"'](?:[^\"']*)(?:email|name|address|tel|phone|zip|card)[^>]*>", blob, re.I)
    if fields and "autocomplete" not in blob.lower():
        return FAIL, "%d identity or payment field(s) with no autocomplete attribute" % len(fields)
    return PASS, "autocomplete present or not applicable"


def check_heading_order(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        levels = [int(m.group(1)) for m in re.finditer(r"<h([1-6])\b", text, re.I)]
        if levels.count(1) > 1:
            bad.append("%s: %d h1 elements" % (Path(path).name, levels.count(1)))
        for a, b in zip(levels, levels[1:]):
            if b > a + 1:
                bad.append("%s: h%d follows h%d" % (Path(path).name, b, a))
                break
    if bad:
        return FAIL, "; ".join(bad[:4])
    return PASS, "heading levels are sequential"


def check_viewport_meta(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"name=[\"']viewport", blob, re.I):
        return UNKNOWN, "no viewport meta in the targets - confirm the document head defines it"
    if re.search(r"user-scalable\s*=\s*no|maximum-scale\s*=\s*1", blob, re.I):
        return FAIL, "zoom is disabled in the viewport meta"
    return PASS, "viewport meta present, zoom allowed"


def check_min_font_size(gate, sources, run, ctx):
    floor = gate["check"].get("min_px", 16)
    bad = []
    for path, text in sources.items():
        for match in re.finditer(r"font-size:\s*(\d+(?:\.\d+)?)px", text, re.I):
            if float(match.group(1)) < floor:
                line = text[: match.start()].count("\n") + 1
                bad.append("%s:%d %spx" % (Path(path).name, line, match.group(1)))
    if bad:
        return UNKNOWN, "type under %dpx at %s - confirm none of it is body copy" % (floor, ", ".join(bad[:6]))
    return PASS, "no type under %dpx" % floor


def check_line_height_range(gate, sources, run, ctx):
    lo, hi = gate["check"]["min"], gate["check"]["max"]
    bad = []
    for path, text in sources.items():
        for selector, blockbody in _css_blocks(text):
            if re.search(r"\b(h[1-6]|display|title|hero)\b", selector, re.I):
                continue
            for match in re.finditer(r"line-height:\s*([\d.]+)\s*;", blockbody):
                value = float(match.group(1))
                if value > 3:
                    continue
                if not (lo <= value <= hi):
                    bad.append("%s %s" % (selector.strip()[:34], match.group(1)))
    if bad:
        return UNKNOWN, "body line-height outside %s-%s: %s - confirm each is not display type" % (lo, hi, "; ".join(bad[:5]))
    return PASS, "body line-height in range"


def check_zindex_scale(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        for match in re.finditer(r"z-index:\s*(\d+)", text):
            if int(match.group(1)) > 1000:
                line = text[: match.start()].count("\n") + 1
                bad.append("%s:%d z-index: %s" % (Path(path).name, line, match.group(1)))
    if bad:
        return FAIL, "z-index off the scale: %s" % "; ".join(bad[:5])
    blob = "\n".join(sources.values())
    if re.search(r"z-index:\s*\d+", blob) and not re.search(r"--z-", blob):
        return UNKNOWN, "raw z-index values with no named layer scale - confirm the layers are defined"
    return PASS, "z-index comes from the scale"


# Feedback that reports an action is a corner toast, not a strip pushed into the
# page. The two are told apart by where they are anchored: a banner sits in flow
# and moves everything under it, a toast is fixed to a corner and floats.
INLINE_BANNER = re.compile(
    r"""role=["']alert["']""")
FIXED_FEEDBACK = re.compile(
    r"""fixed[^"'`]*\b(top|bottom)-[\w./\[\]-]+[^"'`]*\b(left|right)-[\w./\[\]-]+""")
TOAST_HOST = re.compile(r"ToastProvider|ToastHost|useToast|toast\.(success|error)"
                        r"|sonner|react-hot-toast|react-toastify", re.I)
BOTTOM_RIGHT = re.compile(r"bottom-[\w./\[\]-]+[^\"'`]*right-[\w./\[\]-]+"
                          r"|right-[\w./\[\]-]+[^\"'`]*bottom-[\w./\[\]-]+")
PAGE_FILE = re.compile(r"(^|/)(pages?|routes?|views?|app)/", re.I)


def check_toast_channel(gate, sources, run, ctx):
    """UXF-24: one transient channel in the corner, rather than banners per page."""
    blob = "\n".join(sources.values())
    if not re.search(r"role=[\"']alert[\"']|role=[\"']status[\"']|Toast|toast", blob):
        return PASS, "nothing here reports the outcome of an action"

    host = sorted({path for path, text in sources.items() if TOAST_HOST.search(text)})
    if not host:
        return FAIL, ("feedback is reported here but no shared toast channel exists. One host "
                      "in the shell, raised through a hook, is what keeps every message in one "
                      "corner behaving one way.")

    banners, oneoffs = [], []
    for path, text in sources.items():
        for match in INLINE_BANNER.finditer(text):
            window = text[max(0, match.start() - 400):match.start() + 200]
            # A live region inside the toast host is the toast; one anywhere else,
            # not anchored to a corner, is a strip sitting in the page.
            if TOAST_HOST.search(window) or re.search(r"\bfixed\b", window):
                continue
            banners.append("%s:%d" % (Path(path).name,
                                      text[: match.start()].count("\n") + 1))
        if not PAGE_FILE.search(path):
            continue
        for match in FIXED_FEEDBACK.finditer(text):
            window = text[max(0, match.start() - 300):match.start() + 300]
            if not re.search(r"toast|alert|notification|status|message", window, re.I):
                continue
            oneoffs.append("%s:%d" % (Path(path).name,
                                      text[: match.start()].count("\n") + 1))

    faults = []
    if banners:
        faults.append("%d inline banner(s) reporting an action in the page rather than the "
                      "corner: %s" % (len(banners), ", ".join(sorted(set(banners))[:5])))
    if oneoffs:
        faults.append("%d per-page one-off(s) placed by hand instead of raised through the "
                      "host: %s" % (len(oneoffs), ", ".join(sorted(set(oneoffs))[:5])))

    corner = [path for path in host
              if BOTTOM_RIGHT.search(sources[path]) and re.search(r"\bfixed\b", sources[path])]
    if not corner:
        faults.append("the host is not anchored bottom right, which is where every one of these "
                      "belongs so a visitor learns one place to look")

    if faults:
        return FAIL, "; ".join(faults)
    return PASS, ("one toast channel in %s, anchored bottom right, and no inline banner or "
                  "per-page one-off beside it" % Path(corner[0]).name)


# A bar that changes on scroll paints in two states, and only one of them gets
# looked at. The scrolled state is where it goes wrong: a translucent ground that
# relied on a backdrop filter, or two backdrop filters nested one inside the
# other, and the page reads straight through the chrome.
BACKDROP_RULE = re.compile(
    r"([^{}]+)\{([^}]*?(?:-webkit-)?backdrop-filter\s*:\s*([^;}]+)[^}]*)\}", re.S)
SCROLL_STATE = re.compile(r"\.(is-floating|is-stuck|is-scrolled|is-pinned|scrolled|stuck)\b")
TRANSLUCENT = re.compile(r"rgba?\([^)]*?[,/]\s*(0?\.\d+|0)\s*\)|hsla?\([^)]*?[,/]\s*(0?\.\d+|0)\s*\)")


def check_scrolled_bar(gate, sources, run, ctx):
    """NAV-17: the bar's scrolled state is opaque, and no backdrop nests inside another."""
    css = {path: text for path, text in sources.items()
           if re.search(r"\.(css|scss|sass|less)$", path) or "<style" in text}
    blob = "\n".join(css.values())
    if not SCROLL_STATE.search(blob):
        return PASS, "the bar has no scrolled state, so it paints one way"

    # Every selector that sets a backdrop filter, and what it sets. A selector
    # appearing twice keeps the later value, which is how a state class turns the
    # filter off again.
    filters, grounds, lines = {}, {}, {}
    for path, text in css.items():
        for selector, body, value in BACKDROP_RULE.findall(text):
            sel = " ".join(selector.strip().split("\n")[-1].split())
            filters[sel] = value.strip()
            lines[sel] = (Path(path).name, text[: text.find(body)].count("\n") + 1)
            ground = re.search(r"background(?:-color)?\s*:\s*([^;}]+)", body)
            if ground:
                grounds[sel] = ground.group(1).strip()

    nested, washed = [], []
    for sel, value in filters.items():
        if SCROLL_STATE.search(sel) and sel in grounds and TRANSLUCENT.search(grounds[sel]):
            washed.append("%s %s" % (lines.get(sel, ("?", 0))[0], sel))
        if value == "none" or " " not in sel:
            continue
        # The element this one sits inside, as the stylesheet names it.
        ancestor = sel.rsplit(" ", 1)[0].strip()
        if not ancestor:
            continue
        # An ancestor rule that turns the filter off in this same state is the
        # fix, not the fault, so resolve the most specific one that applies.
        applies = [a for a in filters
                   if a == ancestor or (ancestor.startswith(a) and " " not in a)]
        effective = None
        for a in sorted(applies, key=len, reverse=True):
            effective = filters[a]
            break
        if effective and effective != "none":
            where = lines.get(sel, ("?", 0))
            nested.append("%s:%d %s inside %s" % (where[0], where[1], sel, ancestor))

    faults = []
    if nested:
        seen = sorted(set(nested))
        faults.append("%d nested backdrop filter(s): %s. The inner layer composites against the "
                      "outer one rather than the page in WebKit, which paints the page's own "
                      "content over the bar. Set none on the outer, not a zero blur"
                      % (len(seen), "; ".join(seen[:3])))
    if washed:
        faults.append("the scrolled bar keeps a translucent ground (%s), so it is legible only "
                      "while the blur behind it works. A bar detached from the edge is an object; "
                      "give it an opaque ground" % "; ".join(sorted(set(washed))[:3]))
    if faults:
        return FAIL, "; ".join(faults)
    return PASS, "the scrolled bar paints its own ground and no backdrop filter nests in another"


def check_font_display(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"@font-face|next/font|fonts\.googleapis", blob, re.I):
        return PASS, "no custom fonts loaded here"
    if (re.search(r"font-display:\s*(swap|optional)", blob, re.I)
            or re.search(r"next/font", blob)
            or re.search(r"fonts\.googleapis\.com[^\"'>]*display=(swap|optional)", blob, re.I)):
        return PASS, "font-display handled"
    return FAIL, "custom font without font-display: swap or optional"


def check_spacing_scale(gate, sources, run, ctx):
    bad = []
    for path, text in sources.items():
        for match in re.finditer(
                r"(?:^|[;{\s])(padding|margin|gap|row-gap|column-gap|inset|top|bottom|left|right)"
                r"(?:-(?:block|inline)(?:-(?:start|end))?|-(?:top|bottom|left|right))?:\s*([^;}]+)",
                text, re.I):
            for value in re.findall(r"(\d+(?:\.\d+)?)px", match.group(2)):
                px = float(value)
                if px and px % 4 != 0 and px != 1 and px != 2:
                    line = text[: match.start()].count("\n") + 1
                    bad.append("%s:%d %spx" % (Path(path).name, line, value))
    if bad:
        return FAIL, "%d value(s) off the 4px scale: %s" % (len(bad), "; ".join(list(dict.fromkeys(bad))[:6]))
    return PASS, "spacing lands on the scale"


ICON_LIBS = {"lucide": r"lucide", "heroicons": r"heroicons|@heroicons", "material": r"material-icons|@mui/icons",
             "phosphor": r"phosphor", "feather": r"feather-icons", "fontawesome": r"font-?awesome|fa-"}


def check_icon_library_mix(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    found = [name for name, pattern in ICON_LIBS.items() if re.search(pattern, blob, re.I)]
    if len(found) > 1:
        return FAIL, "%d icon libraries in play: %s" % (len(found), ", ".join(found))
    if EMOJI.search(blob):
        return FAIL, "emoji used where an icon belongs"
    return PASS, "one icon family" if found else "no icon library detected"


def check_label_case(gate, sources, run, ctx):
    candidates = []
    patterns = [r"<button[^>]*>\s*([A-Za-z][^<>{]{2,40}?)\s*<", r"<a\b[^>]*>\s*([A-Za-z][^<>{]{2,40}?)\s*<",
                r"<th\b[^>]*>\s*([A-Za-z][^<>{]{2,40}?)\s*<", r"label:\s*[\"']([A-Za-z][^\"']{2,40})[\"']"]
    small = {"a", "an", "the", "and", "or", "but", "for", "nor", "on", "at", "to", "from", "by", "in", "of", "with", "as", "up", "via"}
    for path, text in sources.items():
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                label = match.group(1).strip()
                if not label or label.endswith((".", "?", "!")) or len(label.split()) > 6:
                    continue
                words = label.split()
                offenders = [w for i, w in enumerate(words)
                             if w[:1].islower() and w.lower() not in small and i > 0 and w[:1].isalpha()]
                if len(words) > 1 and offenders:
                    line = text[: match.start()].count("\n") + 1
                    candidates.append("%s:%d \"%s\"" % (Path(path).name, line, label))
    if candidates:
        return UNKNOWN, "labels that may need Title Case: %s" % "; ".join(list(dict.fromkeys(candidates))[:8])
    return PASS, "no sentence-case control labels found"


def check_svg_currentcolor(gate, sources, run, ctx):
    bad = []
    for f in image_files(run["targets"]):
        if f.suffix.lower() != ".svg":
            continue
        text = f.read_text(errors="replace")
        if re.search(r"(fill|stroke)=[\"']#|(fill|stroke)=[\"']rgb", text) and "currentColor" not in text:
            bad.append(f.name)
    if bad:
        return FAIL, "hardcoded colour in %s" % ", ".join(bad[:6])
    return PASS, "icons inherit colour"


# ---- image checks -------------------------------------------------------


def png_info(path):
    """width, height, bit depth, colour type, interlace, and whether tRNS is present."""
    return png_info_bytes(path.read_bytes())


def png_info_bytes(data):
    """The same header read, for a PNG that arrives as bytes rather than a file.

    An icon file carries its images inline, so the payload never exists on disk
    to be opened.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    depth, colour, interlace = data[24], data[25], data[28]
    idat, has_trns = bytearray(), False
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IDAT":
            idat += body
        elif kind == b"tRNS":
            has_trns = True
        elif kind == b"IEND":
            break
        pos += 12 + length
    return {"w": width, "h": height, "depth": depth, "colour": colour,
            "interlace": interlace, "trns": has_trns, "idat": bytes(idat)}


def png_corner_alpha(info):
    """Alpha at the four corners, or None when the file is outside what this decodes."""
    if info["depth"] != 8 or info["interlace"] != 0 or info["colour"] not in (4, 6):
        return None
    channels = 4 if info["colour"] == 6 else 2
    stride = info["w"] * channels
    try:
        raw = zlib.decompress(info["idat"])
    except zlib.error:
        return None
    rows, prev, pos = [], bytearray(stride), 0
    for _ in range(info["h"]):
        if pos >= len(raw):
            break
        ftype, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(len(line)):
            left = line[i - channels] if i >= channels else 0
            up = prev[i]
            upleft = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + left) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + up) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif ftype == 4:
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(line)
        prev = line
    if not rows:
        return None
    last = channels - 1
    top, bottom = rows[0], rows[-1]
    return [top[last], top[-1], bottom[last], bottom[-1]]


def check_png_transparent(gate, sources, run, ctx):
    results, bad = [], []
    for f in image_files(run["targets"]):
        if f.suffix.lower() != ".png":
            continue
        info = png_info(f)
        if not info:
            continue
        if info["colour"] not in (4, 6) and not info["trns"]:
            bad.append("%s has no alpha channel" % f.name)
            continue
        corners = png_corner_alpha(info)
        if corners is None:
            results.append("%s has an alpha channel (corners not decodable here)" % f.name)
        elif all(a == 255 for a in corners):
            bad.append("%s has an alpha channel but every corner is opaque" % f.name)
        else:
            results.append("%s is transparent at the corners" % f.name)
    if bad:
        return FAIL, "; ".join(bad[:6])
    if results:
        return PASS, "; ".join(results[:6])
    return UNKNOWN, "no PNG found under the targets - point the run at the export directory"


def check_png_dimensions(gate, sources, run, ctx):
    spec = run.get("sizes") or {}
    if not spec:
        found = []
        for f in image_files(run["targets"]):
            if f.suffix.lower() == ".png":
                info = png_info(f)
                if info:
                    found.append("%s %dx%d" % (f.name, info["w"], info["h"]))
        return UNKNOWN, "no size spec recorded for this run - confirm against the platform table: %s" % "; ".join(found[:8])
    bad = []
    for name, dims in spec.items():
        matches = [f for f in image_files(run["targets"]) if name in f.name]
        for f in matches:
            info = png_info(f)
            if info and [info["w"], info["h"]] != list(dims):
                bad.append("%s is %dx%d, expected %dx%d" % (f.name, info["w"], info["h"], dims[0], dims[1]))
    if bad:
        return FAIL, "; ".join(bad[:6])
    return PASS, "exports match the size spec"


# ---- process checks -----------------------------------------------------


def project_root(path):
    """The repo the target sits in, or None when it is not inside one."""
    p = Path(path)
    for candidate in [p] + list(p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def check_scope_coverage(gate, sources, run, ctx):
    """Flag deliverable files the run's targets leave out.

    A prompt names a file; the deliverable is everything the reader ends up seeing.
    Scoping the run to what the prompt mentioned is how a hundred gates quietly turn
    into thirty, so files of the same kind sitting outside the targets get named and
    have to be answered for.
    """
    roots = {project_root(t) for t in run["targets"]}
    roots.discard(None)
    if not roots:
        return PASS, "targets are not inside a repo; scope is whatever was pointed at"
    targets = [Path(t).resolve() for t in run["targets"]]
    outside, scanned = [], 0
    for root in roots:
        for f in root.rglob("*"):
            scanned += 1
            if scanned > 20000:
                return UNKNOWN, "project is too large to sweep for scope; confirm the targets cover the whole deliverable"
            if not f.is_file() or f.suffix.lower() not in SOURCE_EXT:
                continue
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            resolved = f.resolve()
            if any(resolved == t or t in resolved.parents for t in targets):
                continue
            outside.append(str(resolved.relative_to(root)))
    if outside:
        return UNKNOWN, ("%d file(s) of the same kind sit outside the run's targets: %s. "
                         "Widen the targets, or say why they are not part of this deliverable."
                         % (len(outside), ", ".join(sorted(outside)[:8])))
    return PASS, "targets cover every source file in the project"


def check_run_opened(gate, sources, run, ctx):
    return PASS, "run %s opened %s" % (run["id"], run["opened"])


def check_all_resolved(gate, sources, run, ctx):
    open_ids = [gid for gid, r in run["results"].items() if r["status"] not in ("pass", "fixed", "na")]
    if open_ids:
        return FAIL, "%d gate(s) still unanswered" % len(open_ids)
    return PASS, "every applicable gate answered"


def check_report_emitted(gate, sources, run, ctx):
    if run.get("report_at"):
        return PASS, "report rendered %s" % run["report_at"]
    return FAIL, "no report rendered yet"


def check_writing_pass(gate, sources, run, ctx):
    tool = HOME / ".claude" / "tools" / "writing-pass.py"
    if not tool.exists():
        return UNKNOWN, "writing-pass.py not present - invoke avoid-ai-writing and attest by hand"
    import subprocess
    # Copy is the subject, so this one reads the whole tree on purpose - the checks
    # around it are handed the served files alone.
    tree = read_sources(run["targets"])
    files = [p for p in tree if Path(p).suffix.lower() in {".md", ".html", ".jsx", ".tsx", ".vue", ".svelte"}]
    if not files:
        return UNKNOWN, "no copy-bearing file under the targets - attest the copy pass by hand"
    proc = subprocess.run([str(tool), "check", *files[:40]], capture_output=True, text=True)
    if proc.returncode == 0:
        return PASS, "writing-pass clean over %d file(s)" % len(files[:40])
    return FAIL, (proc.stdout or proc.stderr).strip()[:400]


# ---------------------------------------------------------------- file ledger

# A file only leaves the ledger by being read and ruled on. Extensions outside
# this set are recorded as excluded rather than dropped, so nothing leaves the
# accounting silently.
LEDGER_EXT = SOURCE_EXT | {".png", ".jpg", ".jpeg", ".webp", ".svg", ".avif", ".gif"}


def ledger_candidates(run):
    """Every file under the targets, split into what must be ruled on and what cannot."""
    keep, excluded = [], []
    for f in iter_files(run["targets"]):
        (keep if f.suffix.lower() in LEDGER_EXT else excluded).append(str(f.resolve()))
    return sorted(keep), sorted(excluded)


# ---------------------------------------------------------------- carried answers

CACHE_SKILL = "design"

# Answers that cannot stand in a later run. Some reach past the project's own
# files, where a content hash is evidence of nothing: a service is awake or it is
# not, and an audit is clean until somebody else publishes an advisory. Some are
# measurements of a rendered page and belong to the run that captured them, so
# carrying one would close a run over a capture it never took. Some exist to
# differ from the previous run, and answering one with the previous answer is the
# failure the gate was written to catch.
# Named where the design tool works them out, further down: the set is skill
# knowledge rather than cache plumbing, and one copy of it is the point.
NEVER_CARRIED = None


def _cache_lib():
    """The shared carry-forward library, or None where it did not travel.

    A skill can land without the tools tree beside it - an uploaded account
    skill, a container holding one directory. Carrying answers is an
    optimisation, so its absence costs a full sweep rather than a run.
    """
    for base in (Path(__file__).resolve().parent,
                 ENGINE.parent.parent / "tools",
                 Path.home() / ".claude" / "tools"):
        path = Path(base) / "pass_cache.py"
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("intelligence_pass_cache", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception:
            return None
    return None


_CACHE_LIB = _cache_lib()
_CACHE_HANDLES = {}

CARRIED_NOTE = re.compile(r"^carried from run [^:]*: ")


def _scope_files(run):
    """The files this run answers for, however its ledger reports them."""
    got = ledger_candidates(run)
    if isinstance(got, tuple):
        got = got[0]
    return sorted(got)


def cache_for(run):
    """The carry-forward store for this run, or None when it is off.

    Built once per run and held, because the scope digest hashes every file in
    the target and rebuilding it on each lookup would cost more than the sweep
    it saves.
    """
    if _CACHE_LIB is None or _CACHE_LIB.disabled() or run.get("no_cache"):
        return None
    key = run.get("id") or "-"
    if key in _CACHE_HANDLES:
        return _CACHE_HANDLES[key]
    handle = _CACHE_LIB.open_for(
        skill=CACHE_SKILL, targets=run.get("targets") or [], gates_dir=GATES_DIR,
        tool_path=__file__, network=NEVER_CARRIED, self_settling=SELF_SETTLING)
    if handle is not None:
        handle.set_scope(_scope_files(run))
        handle.set_question(kind=run.get("kind"), flags=run.get("flags") or [],
                            url=run.get("url") or run.get("site_url"),
                            scope=run.get("scope"))
    _CACHE_HANDLES[key] = handle
    return handle


def carry_file_rulings(run, files):
    """Fills the ledger from rulings given to files that have not changed since.

    A ruling is a file somebody opened and judged against the checklist, and it
    is the most expensive thing a run produces. The judgement holds while the
    file's bytes and the rules it was judged against both stand. What was carried
    is named on the entry, so the ledger reports which run did the reading rather
    than implying this one did.
    """
    cache = cache_for(run)
    if cache is None:
        return 0
    pending = [p for p, r in files.items() if r.get("status") == "open"]
    if not pending:
        return 0
    hashes = _CACHE_LIB.hash_paths(pending)
    carried = 0
    for path in pending:
        row = cache.carry_file(path, hashes.get(path))
        if not row:
            continue
        note = row.get("note") or "ruled on with no change needed"
        files[path].update({
            "status": row.get("status") or "clear",
            "note": "carried from run %s: %s" % (row.get("run") or "?", note),
            "at": row.get("at") or "", "read": True,
            "carried": row.get("run") or "?",
        })
        carried += 1
    return carried


def carry_gate_answer(run, gate, hand=False):
    """The answer this gate already has, or None when it owes one."""
    cache = cache_for(run)
    if cache is None:
        return None
    return cache.carry_gate(gate, hand=hand)


def persist_cache(run, index=None):
    """Records this run's settled answers against what produced them.

    Only settled-good answers, and only for gates whose answer can outlive a run.
    An open or failing gate is work still owed, and owed work is never served
    from a record.
    """
    cache = cache_for(run)
    if cache is None:
        return
    if index is None:
        index = gate_index(load_gates())
    for gid, result in (run.get("results") or {}).items():
        gate = index.get(gid)
        if gate is None or result.get("status") not in ("pass", "na"):
            continue
        note = CARRIED_NOTE.sub("", result.get("note") or "")
        cache.record_gate(gate, result["status"], note,
                          hand=(result.get("by") in ("hand", "carried")),
                          run_id=run.get("id", ""))
    files = run.get("files") or {}
    settled = [p for p, r in files.items() if r.get("status") not in ("open", "")]
    if settled:
        hashes = _CACHE_LIB.hash_paths(settled)
        for path in settled:
            row = files[path]
            cache.record_file(path, hashes.get(path), row.get("status"),
                              CARRIED_NOTE.sub("", row.get("note") or ""),
                              row.get("carried") or run.get("id", ""))
    cache.forget_missing(files.keys())
    cache.save()


def carried_summary(run):
    """What this run was handed rather than worked out, for the run to report."""
    cache = cache_for(run)
    return cache.summary() if cache is not None else ""


def refresh_ledger(run):
    """Seed a ledger slot per candidate file, and retire slots for files that went."""
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
    carry_file_rulings(run, files)
    run["excluded"] = excluded
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


def check_file_coverage(gate, sources, run, ctx):
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
    return FAIL, detail + ": " + ", ".join(Path(p).name for p in sorted(unruled)[:10])


# Reads that go through the shell still count, since a file opened with cat or sed
# has been read as surely as one opened with the Read tool.
READ_VERBS = {"cat", "head", "tail", "less", "more", "bat", "sed", "nl", "awk"}


def shell_read_paths(command, base):
    tokens = shell_tokens(command)
    paths, verb = [], None
    for token in tokens:
        head = Path(token).name
        if head in READ_VERBS:
            verb = head
            continue
        if token in {"|", "&&", ";"}:
            verb = None
            continue
        if verb and not token.startswith("-"):
            candidate = Path(token).expanduser()
            if not candidate.is_absolute():
                candidate = Path(base) / candidate
            if candidate.is_file():
                paths.append(str(candidate))
    return paths


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
    paths = []
    if tool_input.get("file_path"):
        paths.append(tool_input["file_path"])
    if tool_input.get("notebook_path"):
        paths.append(tool_input["notebook_path"])
    if tool_input.get("command"):
        paths.extend(shell_read_paths(tool_input["command"], payload.get("cwd") or os.getcwd()))
    if not paths:
        return 0
    refresh_ledger(run)
    if mark_read(run, paths):
        save_run(run)
    return 0


def cmd_files(argv):
    run = load_run()
    flags = parse_flags(argv)
    files = refresh_ledger(run)
    save_run(run)
    order = sorted(files)
    open_files = [p for p in order if files[p]["status"] == "open"]
    if "all" in flags:
        for path in order:
            record = files[path]
            print("  %-7s %-5s %s" % (record["status"], "read" if record["read"] else "-", path))
    else:
        for path in open_files:
            print("  %-5s %s" % ("read" if files[path]["read"] else "-", path))
    print("\n%d file(s) in the ledger, %d still to rule on, %d excluded by type."
          % (len(files), len(open_files), len(run.get("excluded", []))))
    if open_files:
        print("Read each one, then either change it or clear it:")
        print("  design-pass.py file-clear <path>... --note \"<what you read and why it needs nothing>\"")
    return 0


def cmd_file_clear_batch(source, run):
    """Clear many files in one call, each with its own reason.

    The ledger is read off the run this saves, not off a second copy: a batch
    written into one dict and saved from another lands nowhere, and the run
    reopens every file it just cleared."""
    files = refresh_ledger(run)
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
            problems.append("%s: note must say what was read and why it needs no design change" % Path(path).name)
            continue
        if not EVIDENCE.search(note):
            problems.append("%s: note carries no evidence - name what is in the file: a path, "
                            "a line, a measurement or a quoted string" % Path(path).name)
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
    persist_cache(run)
    print("Cleared %d file(s)." % len(cleared))
    left = [p for p, v in files.items() if v["status"] == "open"]
    print("%d file(s) still to rule on." % len(left))
    return 0


def cmd_file_clear(argv):
    if "--batch" in argv:
        at = argv.index("--batch")
        if at + 1 >= len(argv):
            raise SystemExit("--batch takes a path, or - to read the JSON from stdin")
        return cmd_file_clear_batch(argv[at + 1], load_run())
    run = load_run()
    paths, note = [], ""
    i = 0
    while i < len(argv):
        if argv[i] == "--note":
            note = argv[i + 1]
            i += 2
        else:
            paths.append(argv[i])
            i += 1
    if len(note.strip()) < 12:
        raise SystemExit("--note must say what you read and why it needs no change")
    files = refresh_ledger(run)
    resolved = [str(Path(p).expanduser().resolve()) for p in paths]
    unknown = [p for p in resolved if p not in files]
    if unknown:
        raise SystemExit("not in this run's ledger: %s" % ", ".join(unknown))
    unread = [p for p in resolved if not files[p]["read"]]
    if unread:
        raise SystemExit(
            "not read yet, so there is nothing to clear them on: %s\n"
            "Open each file first - the Read tool, or cat/sed/head - then clear it."
            % ", ".join(Path(p).name for p in unread))
    for path in resolved:
        files[path].update({"status": "clear", "note": note, "at": now()})
    save_run(run)
    persist_cache(run)
    remaining = [p for p in files if files[p]["status"] == "open"]
    print("Ruled on %d file(s). %d left in the ledger." % (len(resolved), len(remaining)))
    return 0


# ---------------------------------------------------------------- placeholders

# What a placeholder is called varies by codebase; what it does not vary in is
# being named after the job.
SKELETON_SELECTOR = re.compile(r"skeleton|shimmer|placeholder|pulse|loading-?(bar|block|row|card)", re.I)
LATE_WIDGETS = re.compile(
    r"chart\.js|Chart\.js|recharts|Recharts|<canvas|echarts|ApexCharts|mapbox|leaflet|google\.maps|"
    r"monaco|CodeMirror|<iframe|react-player|<video",
    re.I,
)


def keyframe_bodies(text):
    """Every @keyframes block as (name, body), counting braces rather than matching
    a closing newline - a one-line keyframes is still a keyframes."""
    out = []
    for match in re.finditer(r"@keyframes\s+([\w-]+)\s*\{", text):
        depth, i = 1, match.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        out.append((match.group(1), text[match.end():i - 1]))
    return out


def skeleton_blocks(sources):
    """Every CSS rule whose selector names a placeholder."""
    out = []
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if SKELETON_SELECTOR.search(selector):
                out.append((path, selector.strip(), body))
    return out


def check_skeleton_presence(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not SKELETON_SELECTOR.search(blob):
        return FAIL, ("the deliverable fetches but names no placeholder anywhere - "
                      "every loading path needs one shaped like what it is loading")
    spinner_only = re.search(r"spinner|<Loader|animate-spin", blob, re.I)
    if spinner_only:
        return UNKNOWN, ("placeholders exist and so does a spinner - confirm the spinner is only "
                         "on actions, never standing in for content that has a shape")
    return PASS, "placeholder markup present on the fetching paths"


def check_skeleton_tokens(gate, sources, run, ctx):
    literals = []
    for path, selector, body in skeleton_blocks(sources):
        for match in re.finditer(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(", body):
            literals.append("%s %s" % (Path(path).name, selector[:40]))
    if literals:
        return FAIL, "hardcoded colour in the placeholder rules: %s" % "; ".join(dict.fromkeys(literals))
    if not skeleton_blocks(sources):
        return UNKNOWN, "no placeholder rule found to inspect - confirm the placeholder is tokened"
    return PASS, "placeholder fill and highlight resolve through tokens"


def check_skeleton_motion(gate, sources, run, ctx):
    bad, names = [], set()
    for path, selector, body in skeleton_blocks(sources):
        for match in re.finditer(r"animation(?:-name)?:\s*([a-zA-Z_][\w-]*)", body):
            names.add(match.group(1))
    for path, text in sources.items():
        for name, frames in keyframe_bodies(text):
            if name not in names and not SKELETON_SELECTOR.search(name):
                continue
            if re.search(r"\b(background-position|width|height|left|right|top|bottom|margin|padding)\s*:", frames):
                bad.append("%s @keyframes %s" % (Path(path).name, name))
    if bad:
        return FAIL, "placeholder sweep animates a paint or layout property: %s" % "; ".join(bad)
    if not names:
        return UNKNOWN, "no placeholder animation found - confirm the placeholder is static by intent"
    return PASS, "placeholder motion stays on transform and opacity"


def check_skeleton_reduced_motion(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    animated = any(re.search(r"animation", body) for _, _, body in skeleton_blocks(sources))
    if not animated:
        return PASS, "placeholder does not animate, so there is nothing to reduce"
    reduced = re.search(r"prefers-reduced-motion[^{]*\{(.*?)\n\s*\}", blob, re.S)
    if not reduced:
        return FAIL, "animated placeholder with no prefers-reduced-motion block"
    if not (SKELETON_SELECTOR.search(reduced.group(1)) or re.search(r"animation[^;]*none|\*\s*\{", reduced.group(1))):
        return FAIL, "the reduced-motion block does not reach the placeholder animation"
    return PASS, "placeholder sweep stops under reduced motion"


def check_skeleton_aria(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    markup = [m.group(0) for m in re.finditer(r"<[a-zA-Z][^>]*>", blob)
              if SKELETON_SELECTOR.search(m.group(0))]
    if not markup:
        return UNKNOWN, "no placeholder element found in markup - confirm it is hidden from assistive tech"
    unhidden = [m[:70] for m in markup if "aria-hidden" not in m and "aria-busy" not in m and "role=" not in m]
    if unhidden and not re.search(r"aria-busy", blob):
        return FAIL, "%d placeholder element(s) with no aria-hidden and no aria-busy region: %s" % (
            len(unhidden), "; ".join(unhidden[:3]))
    if not re.search(r"aria-live|role=[\"']status", blob):
        return UNKNOWN, "placeholders are hidden but no live region announces the wait - confirm one exists"
    return PASS, "placeholders hidden, wait announced through a live region"


def check_skeleton_primitive_count(gate, sources, run, ctx):
    names = set()
    for text in sources.values():
        for match in re.finditer(r"(?:function|const|class)\s+([A-Z][\w]*(?:Skeleton|Placeholder|Loading|Shimmer))", text):
            names.add(match.group(1))
        for match in re.finditer(r"\.([\w-]*(?:skeleton|placeholder|shimmer)[\w-]*)\s*\{", text, re.I):
            names.add(match.group(1))
    bases = {n for n in names if re.fullmatch(r"[A-Za-z]*(Skeleton|Placeholder|Shimmer)|[a-z-]*(skeleton|placeholder|shimmer)", n)}
    if len(names) > 6:
        return UNKNOWN, "%d placeholder names in play (%s) - confirm they compose one primitive rather than nine" % (
            len(names), ", ".join(sorted(names)[:8]))
    if not names:
        return UNKNOWN, "no named placeholder primitive found"
    return PASS, "%d placeholder name(s): %s" % (len(names), ", ".join(sorted(names)))


def check_skeleton_late_widgets(gate, sources, run, ctx):
    hits = []
    for path, text in sources.items():
        for match in LATE_WIDGETS.finditer(text):
            line = text[: match.start()].count("\n") + 1
            window = text[max(0, match.start() - 400):match.end() + 400]
            if re.search(r"aspect-ratio|min-height|height:\s*\d", window) or SKELETON_SELECTOR.search(window):
                continue
            hits.append("%s:%d %s" % (Path(path).name, line, match.group(0)[:24]))
    if hits:
        return FAIL, ("widget(s) that measure their container on mount with no reserved box nearby: %s"
                      % "; ".join(dict.fromkeys(hits))[:400])
    return PASS, "late-measuring widgets reserve their box"


def check_skeleton_image_placeholder(gate, sources, run, ctx):
    bare = []
    for path, text in sources.items():
        for match in re.finditer(r"<img\b[^>]*>", text, re.I):
            tag = match.group(0)
            window = text[max(0, match.start() - 300):match.end() + 300]
            if re.search(r"background|placeholder|blurDataURL|blurhash|--(color|surface)", window, re.I):
                continue
            line = text[: match.start()].count("\n") + 1
            bare.append("%s:%d" % (Path(path).name, line))
    if bare:
        return UNKNOWN, ("image(s) with no placeholder fill behind them at %s - confirm each box is filled "
                         "before the file decodes" % ", ".join(bare[:6]))
    return PASS, "image boxes carry a placeholder fill"


# The measured gates read a capture taken in the browser rather than an assurance:
# a placeholder either lands on the content's box or it does not, and that is a
# number, not a judgement.
PROBE_TOLERANCE = {"box": 2.0, "position": 2.0, "gap": 1.0, "shift": 0.5}


def probe_verdict(run, dimension):
    probe = run.get("probe")
    if not probe:
        return FAIL, ("no capture on this run - render the loading state and the loaded state, capture both "
                      "with scripts/code/skeleton-probe.js, then: design-pass.py skeleton-probe --file <capture.json>")
    problems, checked = [], 0
    for item in probe.get("items", []):
        name = item.get("name", "unnamed")
        sk, ct = item.get("skeleton") or {}, item.get("content") or {}
        if dimension == "box":
            for axis in ("w", "h"):
                if axis in sk and axis in ct:
                    checked += 1
                    tol = max(PROBE_TOLERANCE["box"], abs(ct[axis]) * 0.01)
                    if abs(sk[axis] - ct[axis]) > tol:
                        problems.append("%s %s %.1f vs %.1f" % (name, axis, sk[axis], ct[axis]))
        elif dimension == "position":
            for axis in ("x", "y"):
                if axis in sk and axis in ct:
                    checked += 1
                    if abs(sk[axis] - ct[axis]) > PROBE_TOLERANCE["position"]:
                        problems.append("%s %s %.1f vs %.1f" % (name, axis, sk[axis], ct[axis]))
        elif dimension in ("gap", "count", "radius", "aspect"):
            if dimension in sk and dimension in ct:
                checked += 1
                a, b = sk[dimension], ct[dimension]
                same = (abs(a - b) <= (PROBE_TOLERANCE.get("gap", 1.0) if dimension == "gap" else 0.02)
                        if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a == b)
                if not same:
                    problems.append("%s %s %s vs %s" % (name, dimension, a, b))
        elif dimension == "cascade":
            continue
        elif dimension == "shift":
            if "shift" in item:
                checked += 1
                if abs(item["shift"]) > PROBE_TOLERANCE["shift"]:
                    problems.append("%s shifted %.1fpx" % (name, item["shift"]))
    if not checked:
        return FAIL, "the capture records nothing for %s - re-capture with that dimension included" % dimension
    if problems:
        return FAIL, "%d mismatch(es): %s" % (len(problems), "; ".join(problems[:6]))
    return PASS, "%d measurement(s) match at %s" % (checked, probe.get("captured", "the captured viewport"))


def check_skeleton_probe(gate, sources, run, ctx):
    if gate["check"]["dimension"] == "cascade":
        return cascade_verdict(run)
    return probe_verdict(run, gate["check"]["dimension"])


def cascade_verdict(run):
    """Whether the page held still across the whole load, not just at its ends.

    A before-and-after pair can match perfectly while the reader was moved twice
    in between - the top block resolving early and pulling everything up, then the
    next one landing and pushing it back. Only a recording of the load sees it.
    """
    res = (run.get("probe") or {}).get("resolution")
    if not res:
        return FAIL, ("no recording of the load on this run - run skeletonProbe.watch() through it, "
                      "then skeletonProbe.result() and design-pass.py skeleton-probe --file <capture.json>")
    if res.get("stillPending"):
        return FAIL, "%d placeholder(s) were still on screen when the recording ended" % res["stillPending"]
    moments = res.get("moments") or []
    moved = [m for m in moments if m.get("shifted", 0) > 1]
    if moved:
        return FAIL, ("the page moved %d time(s) while loading: %s"
                      % (len(moved), "; ".join("%dpx at %dms as placeholders went %d to %d"
                                               % (m["shifted"], m["at"], m["from"], m["to"]) for m in moved[:5])))
    if res.get("distinctMoments", 0) > 1:
        return PASS, ("%d resolution moments, none of which moved anything already on screen"
                      % res["distinctMoments"])
    return PASS, "the placeholders cleared in one moment, shifting nothing"


def check_resolve_together(gate, sources, run, ctx):
    """Blocks in one view each waiting on their own request."""
    res = (run.get("probe") or {}).get("resolution")
    if res and res.get("distinctMoments", 0) > 2:
        return FAIL, ("the view's placeholders cleared across %d separate moments (%s) - they should come "
                      "and go as a set" % (res["distinctMoments"],
                                           ", ".join("%dms" % m["at"] for m in res["moments"][:6])))
    counts = []
    for path, text in sources.items():
        flags = set(re.findall(r"\b(is[A-Z]\w*Loading|loading[A-Z]\w*|\w+Loading)\b", text))
        queries = len(re.findall(r"useQuery|useSWR|useFetch|await\s+fetch\(", text))
        if len(flags) > 1 or queries > 1:
            counts.append("%s (%d loading flag(s), %d request(s))" % (Path(path).name, len(flags), queries))
    if counts:
        return UNKNOWN, ("more than one independent loading state per file at %s - confirm they resolve on one "
                         "boundary, or that each block's box is exact enough that arriving alone moves nothing"
                         % "; ".join(counts[:5]))
    if res:
        return PASS, "the placeholders cleared in %d moment(s)" % res.get("distinctMoments", 1)
    return UNKNOWN, "no recording of the load - run skeletonProbe.watch() through it"


def check_content_entrance(gate, sources, run, ctx):
    """Whether what replaces a placeholder animates in rather than appearing."""
    res = (run.get("probe") or {}).get("resolution")
    if res and res.get("inserted"):
        animated, inserted = res.get("animatedIn", 0), res["inserted"]
        if animated == 0:
            return FAIL, "%d element(s) were inserted as the placeholders cleared and none animated in" % inserted
        if animated < inserted / 2:
            return FAIL, "only %d of %d arriving element(s) animated in; the rest appeared" % (animated, inserted)
        return PASS, "%d of %d arriving element(s) animated in" % (animated, inserted)
    blob = "\n".join(sources.values())
    if not re.search(r"isLoading|Skeleton|placeholder", blob, re.I):
        return PASS, "nothing resolves from a placeholder here"
    entrance = re.search(r"animate-in|fade-in|@starting-style|initial=\{|animation:[^;]*(fade|enter|in)\b|"
                         r"data-state=[\"']open|\.enter\b", blob, re.I)
    if entrance:
        return UNKNOWN, ("an entrance animation exists - confirm it is applied to the resolved block, runs once, "
                         "and is the same entrance used for page changes")
    return FAIL, "content resolves from a placeholder with no entrance animation: it will appear rather than arrive"


def cmd_skeleton_probe(argv):
    """Record a browser capture of both states so the measured gates can be settled."""
    flags = parse_flags(argv)
    if not flags.get("file"):
        raise SystemExit("--file <capture.json> is required; produce it with scripts/code/skeleton-probe.js")
    path = Path(flags["file"][0]).expanduser()
    data = json.loads(path.read_text())
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise SystemExit("the capture has no items; it must record each placeholder against its content")
    run = load_run()
    run["probe"] = data
    save_run(run)
    print("Recorded a capture of %d item(s) at %s." % (len(items), data.get("captured", "an unstated viewport")))
    for dimension in ("box", "position", "gap", "count", "radius", "aspect", "shift"):
        status, detail = probe_verdict(run, dimension)
        print("  %-9s %s: %s" % (dimension, status, detail))
    if data.get("resolution"):
        for label, fn in (("cascade", cascade_verdict), ("together", check_resolve_together),
                          ("entrance", check_content_entrance)):
            status, detail = fn(run) if fn is cascade_verdict else fn({"check": {}}, {}, run, {})
            print("  %-9s %s: %s" % (label, status, detail))
    else:
        print("  cascade   no recording of the load in this capture - add skeletonProbe.watch()")
    return 0


FIELD_SELECTOR = re.compile(r"input|textarea|\bselect\b|\bfield\b|form-control", re.I)
MENU_SELECTOR = re.compile(r"menu|dropdown|listbox|popover|combobox|option", re.I)


# ---------------------------------------------------------------- cohesion

# What each property looks like in source, and how to reduce a declaration to the
# value worth comparing. Cohesion is judged on values, not on the text around them.
COHESION_PROPS = {
    "border-radius": (r"border-radius:\s*([^;}]+)|\brounded-(\[[^\]]+\]|[a-z0-9]+)", None),
    "box-shadow": (r"box-shadow:\s*([^;}]+)|\bshadow-(\[[^\]]+\]|[a-z0-9]+)", None),
    # `border-radius: 8px` also starts with `border`, so the shorthand branch has
    # to name the sides it accepts rather than any border-* property.
    "border-width": (r"border-width:\s*([^;}]+)|border(?:-(?:top|right|bottom|left))?:\s*(\d+px)", None),
    "duration": (r"transition[^;}]*?(\d+(?:\.\d+)?m?s)\b|duration-(\d+)", None),
    "easing": (r"(cubic-bezier\([^)]+\))|\b(ease-in-out|ease-out|ease-in|linear)\b", None),
    "font-size": (r"font-size:\s*([^;}]+)", None),
    "spacing": (r"(?:padding|margin|gap)(?:-[a-z]+)?:\s*([^;}]+)", None),
    "font-family": (r"font-family:\s*([^;},]+)", None),
    "control-height": (r"(?:min-height|height):\s*(\d+px)", None),
    "icon-size": (r"(?:width|height):\s*(1[0-9]px|2[0-9]px|3[0-2]px)", None),
    "hover-effect": (r":hover[^{]*\{([^}]*)\}|hover:([a-z-]+)", None),
}

# Element kinds worth comparing against themselves across files.
COMPONENT_KEYS = {
    "table": r"table|thead|tbody|\btr\b|\bth\b|\btd\b|datagrid",
    "card": r"\bcard\b|tile\b|panel\b",
    "button": r"\bbtn\b|button",
    "field": r"input|textarea|\bselect\b|\bfield\b",
    "modal": r"modal|dialog|drawer|sheet",
    "header": r"header|masthead|topbar|toolbar",
    "menu": r"menu|dropdown|listbox|popover",
}


def cohesion_sample(sources, prop):
    """Every occurrence of a property, as (value, file, line)."""
    pattern = COHESION_PROPS[prop][0]
    rx = re.compile(pattern, re.I)
    out = []
    for path, text in sources.items():
        stripped = CSS_COMMENT.sub(blank, text)
        for match in rx.finditer(stripped):
            value = next((g for g in match.groups() if g), None)
            if not value:
                continue
            value = " ".join(value.split()).strip().strip('"\'').lower()
            if not value or value.startswith("var(") or value in {"inherit", "initial", "unset", "none", "0"}:
                continue
            # A field's 16px floor is a rule (FLD-05), not drift, so it must not
            # read as the odd one out among smaller UI type.
            if prop == "font-size":
                line_start = stripped.rfind("{", 0, match.start())
                selector_start = stripped.rfind("}", 0, line_start) + 1 if line_start != -1 else 0
                selector = stripped[selector_start:line_start] if line_start != -1 else ""
                if FIELD_SELECTOR.search(selector):
                    continue
            if prop == "hover-effect":
                value = ",".join(sorted({d.split(":")[0].strip() for d in value.split(";") if ":" in d}))
                if not value:
                    continue
            line = stripped[: match.start()].count("\n") + 1
            out.append((value, Path(path).name, line))
    return out


def minority_report(sample, min_total=8):
    """The values a deliverable mostly does, and the places doing something else.

    Judged against the leading value rather than the total: with four values in
    play a fifth used once is still an outlier, and a share-of-total test would
    call it ordinary. A value is outlying when it appears at most twice while the
    leading one appears at least four times as often - which needs a real sample,
    hence the floor on the total.
    """
    if len(sample) < min_total:
        return None, []
    counts = {}
    for value, _, _ in sample:
        counts[value] = counts.get(value, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    top = ranked[0][1]
    rare = {v for v, n in ranked if n <= 2 and top >= 4 * n}
    dominant = {v for v, _ in ranked if v not in rare}
    outliers = [(v, f, l) for v, f, l in sample if v in rare]
    return dominant, outliers


def check_cohesion_values(gate, sources, run, ctx):
    prop = gate["check"]["property"]
    sample = cohesion_sample(sources, prop)
    dominant, outliers = minority_report(sample)
    if dominant is None:
        return UNKNOWN, "only %d %s declaration(s) in scope - too few to compare; check by eye" % (len(sample), prop)
    if not outliers:
        return PASS, "%d %s declaration(s) across %d value(s): %s" % (
            len(sample), prop, len(dominant), ", ".join(sorted(dominant)[:6]))
    listed = "; ".join("%s at %s:%d" % (v, f, l) for v, f, l in outliers[:8])
    return FAIL, "the deliverable mostly uses %s; %d place(s) do not - %s" % (
        ", ".join(sorted(dominant)[:4]), len(outliers), listed)


def component_rules(sources, key):
    """Declaration blocks belonging to one kind of element, keyed by file."""
    rx = re.compile(COMPONENT_KEYS[key], re.I)
    out = []
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if rx.search(selector):
                out.append((Path(path).name, selector.strip(), body))
    return out


def check_cohesion_component(gate, sources, run, ctx):
    spec = gate["check"]
    keys = list(COMPONENT_KEYS) if spec.get("component") == "all" else [spec["component"]]
    props = spec.get("properties") or ["border-radius"]
    problems, compared = [], 0
    for key in keys:
        rules = component_rules(sources, key)
        files = {f for f, _, _ in rules}
        if len(files) < 2:
            continue
        for prop in props:
            seen = {}
            for fname, selector, body in rules:
                match = re.search(re.escape(prop) + r":\s*([^;}]+)", body, re.I)
                if not match:
                    continue
                value = " ".join(match.group(1).split()).lower()
                seen.setdefault(value, []).append("%s (%s)" % (fname, selector[:28]))
            if len(seen) > 1:
                compared += 1
                spread = "; ".join("%s in %s" % (v, ", ".join(w[:2])) for v, w in
                                   sorted(seen.items(), key=lambda kv: -len(kv[1]))[:3])
                problems.append("%s %s differs across files - %s" % (key, prop, spread))
    if problems:
        return FAIL, " | ".join(problems[:5])
    if not compared:
        return UNKNOWN, "the same element kind was not found styled in two or more files; compare by eye"
    return PASS, "%d component/property pair(s) agree across files" % compared


def check_cohesion_duplicate_components(gate, sources, run, ctx):
    names = {}
    for path, text in sources.items():
        for match in re.finditer(r"(?:export\s+(?:default\s+)?)?(?:function|const|class)\s+([A-Z][A-Za-z0-9]*)", text):
            names.setdefault(match.group(1), set()).add(Path(path).name)
    dupes = {n: f for n, f in names.items() if len(f) > 1}
    if dupes:
        return UNKNOWN, "component name(s) defined in more than one file: %s - confirm these are not two copies" % (
            "; ".join("%s in %s" % (n, ", ".join(sorted(f))) for n, f in list(dupes.items())[:5]))
    return PASS, "no component name is defined twice"


STATUS_WORDS = ("danger", "error", "success", "warning", "info", "critical", "positive", "negative")


def check_cohesion_status_tokens(gate, sources, run, ctx):
    mapping = {}
    for path, text in sources.items():
        for word in STATUS_WORDS:
            for match in re.finditer(r"[-\w]*%s[-\w]*\s*:\s*([^;}]+)" % word, text, re.I):
                value = " ".join(match.group(1).split()).lower()
                if value.startswith("var(") or not re.search(r"#|rgb|hsl|oklch", value):
                    continue
                mapping.setdefault(word, set()).add(value)
    conflicting = {w: v for w, v in mapping.items() if len(v) > 1}
    if conflicting:
        return FAIL, "status colour(s) defined more than one way: %s" % "; ".join(
            "%s = %s" % (w, " / ".join(sorted(v)[:3])) for w, v in list(conflicting.items())[:4])
    return PASS, "status colours resolve one way each" if mapping else "no literal status colours to compare"


def check_cohesion_ran(gate, sources, run, ctx):
    if run.get("cohesion_at"):
        return PASS, "cohesion sweep ran %s over %d file(s)" % (run["cohesion_at"], run.get("cohesion_files", 0))
    return FAIL, "the cross-file cohesion sweep has not run: design-pass.py cohesion"


def cmd_cohesion(argv):
    """Compare every file in scope against the others, property by property."""
    run = load_run()
    groups, ctx, sources = refresh(run)
    print("Comparing %d file(s) for design mismatches.\n" % len(sources))
    found = 0
    for prop in COHESION_PROPS:
        sample = cohesion_sample(sources, prop)
        dominant, outliers = minority_report(sample)
        if dominant is None:
            print("  %-15s %d declaration(s) - too few to compare" % (prop, len(sample)))
            continue
        if not outliers:
            print("  %-15s consistent: %s" % (prop, ", ".join(sorted(dominant)[:5])))
            continue
        found += len(outliers)
        print("  %-15s mostly %s" % (prop, ", ".join(sorted(dominant)[:4])))
        for value, fname, line in outliers[:10]:
            print("                  %s at %s:%d" % (value, fname, line))
        if len(outliers) > 10:
            print("                  ... and %d more" % (len(outliers) - 10))
    print()
    for key in COMPONENT_KEYS:
        rules = component_rules(sources, key)
        files = {f for f, _, _ in rules}
        if len(files) < 2:
            continue
        for prop in ("border-radius", "border-width", "padding", "box-shadow"):
            seen = {}
            for fname, selector, body in rules:
                match = re.search(re.escape(prop) + r":\s*([^;}]+)", body, re.I)
                if match:
                    seen.setdefault(" ".join(match.group(1).split()).lower(), []).append(fname)
            if len(seen) > 1:
                found += 1
                print("  %s %s differs: %s" % (
                    key, prop, "; ".join("%s in %s" % (v, ", ".join(sorted(set(f))[:3])) for v, f in seen.items())))
    run["cohesion_at"] = now()
    run["cohesion_files"] = len(sources)
    save_run(run)
    print("\n%s" % ("Nothing outlying." if not found else
                     "%d mismatch(es) above. Fix them, or answer the COH gates with why each is deliberate." % found))
    return 0


# ---------------------------------------------------------------- fields, tables, headers

def field_rules(sources):
    return [(p, sel.strip(), body) for p, text in sources.items()
            for sel, body in _css_blocks(text) if FIELD_SELECTOR.search(sel)]


def menu_rules(sources):
    return [(p, sel.strip(), body) for p, text in sources.items()
            for sel, body in _css_blocks(text) if MENU_SELECTOR.search(sel)]


def check_field_surface(gate, sources, run, ctx):
    rules = field_rules(sources)
    if not rules:
        return UNKNOWN, "no field rule found to inspect - confirm fields carry their own surface"
    if any(re.search(r"background(-color)?:", body, re.I) for _, _, body in rules):
        return PASS, "fields declare their own background"
    return FAIL, "no background on any field rule: the field takes the page ground and reads as a bordered label"


def check_field_font_size(gate, sources, run, ctx):
    small = []
    for path, selector, body in field_rules(sources):
        for match in re.finditer(r"font-size:\s*(\d+(?:\.\d+)?)px", body, re.I):
            if float(match.group(1)) < 16:
                small.append("%s %s at %spx" % (Path(path).name, selector[:30], match.group(1)))
    if small:
        return FAIL, "field text under 16px will zoom the page on iOS focus: %s" % "; ".join(small[:5])
    blob = "\n".join(sources.values())
    if re.search(r"text-(xs|sm)\b[^\"']*\b(input|field)|\b(input|field)[^\"']*text-(xs|sm)\b", blob):
        return UNKNOWN, "a small-text utility appears near a field - confirm the computed size is at least 16px"
    return PASS, "no field text under 16px"


def check_placeholder_only_label(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    placeholders = len(re.findall(r"placeholder\s*=", blob, re.I))
    labels = len(re.findall(r"<label\b|<FormLabel|aria-labelledby", blob, re.I))
    if placeholders and labels < placeholders:
        return FAIL, "%d placeholder(s) but %d label(s): a placeholder disappears the moment there is content" % (
            placeholders, labels)
    if placeholders:
        return PASS, "%d placeholder(s), %d label(s)" % (placeholders, labels)
    return PASS, "no placeholder-only fields"


def check_control_height_token(gate, sources, run, ctx):
    heights = set()
    for path, selector, body in field_rules(sources):
        for match in re.finditer(r"(?:min-)?height:\s*(\d+)px", body, re.I):
            heights.add(int(match.group(1)))
    for path, text in sources.items():
        for sel, body in _css_blocks(text):
            if re.search(r"\bbtn\b|button", sel, re.I):
                for match in re.finditer(r"(?:min-)?height:\s*(\d+)px", body, re.I):
                    heights.add(int(match.group(1)))
    if len(heights) > 2:
        return FAIL, "%d control heights in play (%s): input and button should share one token" % (
            len(heights), ", ".join("%dpx" % h for h in sorted(heights)))
    if not heights:
        return UNKNOWN, "no explicit control height found - confirm inputs and buttons share one"
    return PASS, "control height(s): %s" % ", ".join("%dpx" % h for h in sorted(heights))


def check_aria_invalid(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"error|invalid", blob, re.I):
        return UNKNOWN, "no validation surface found - confirm invalid fields carry aria-invalid"
    if re.search(r"aria-invalid", blob):
        return PASS, "aria-invalid present on the validation path"
    return FAIL, "validation exists but no aria-invalid: the invalid state is carried by colour alone"


def check_select_appearance(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"<select\b|<Select\b", blob):
        return PASS, "no select in the deliverable"
    if re.search(r"<Select\b", blob) and not re.search(r"<select\b", blob):
        return PASS, "select comes from a component library rather than the native control"
    if re.search(r"appearance:\s*none|appearance-none", blob, re.I):
        return PASS, "native select chrome is replaced"
    return FAIL, "a native <select> with no appearance: none keeps OS chrome that ignores every token"


def check_menu_keyboard(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    missing = [n for n, pattern in (
        ("aria-expanded", r"aria-expanded"),
        ("a listbox or menu role", r"role=[\"'](listbox|menu|combobox)"),
        ("aria-selected or aria-checked", r"aria-(selected|checked)"),
        ("key handling", r"onKeyDown|keydown|ArrowDown"),
    ) if not re.search(pattern, blob)]
    if missing:
        return FAIL, "a custom menu is missing %s" % ", ".join(missing)
    return PASS, "the menu declares expansion, role, selection, and key handling"


def check_menu_clipping(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if re.search(r"createPortal|<Portal|popover=|showPopover|\bportal\b", blob, re.I):
        return PASS, "the menu escapes its container through a portal or the popover API"
    if re.search(r"overflow(-[xy])?:\s*(hidden|auto|scroll)", blob, re.I):
        return FAIL, ("the deliverable has an overflow container and no portal or popover escape: "
                      "a menu inside one is clipped at the container edge")
    return UNKNOWN, "no overflow ancestor found - confirm nothing clips the menu at any width"


def check_menu_scroll(gate, sources, run, ctx):
    rules = menu_rules(sources)
    if not rules:
        return UNKNOWN, "no menu rule found to inspect"
    joined = "\n".join(body for _, _, body in rules)
    missing = [n for n, pattern in (("max-height", r"max-height"),
                                    ("overflow-y", r"overflow(-y)?:\s*(auto|scroll)"),
                                    ("overscroll-behavior", r"overscroll-behavior"))
               if not re.search(pattern, joined, re.I)]
    if missing:
        return FAIL, "the menu surface is missing %s" % ", ".join(missing)
    return PASS, "the menu caps its height, scrolls inside itself, and contains the scroll"


def check_menu_surface(gate, sources, run, ctx):
    rules = menu_rules(sources)
    if not rules:
        return UNKNOWN, "no menu rule found to inspect"
    problems = []
    for path, selector, body in rules:
        if re.search(r"background[^;]*rgba\([^)]*0?\.[0-9]", body, re.I):
            problems.append("%s %s is translucent" % (Path(path).name, selector[:30]))
    joined = "\n".join(body for _, _, body in rules)
    if not re.search(r"background", joined, re.I):
        problems.append("no background on any menu rule")
    if not re.search(r"box-shadow|border", joined, re.I):
        problems.append("no shadow or border separating the menu from what it covers")
    if problems:
        return FAIL, "; ".join(problems[:4])
    return PASS, "the menu sits on an opaque raised surface"


def check_menu_zindex(gate, sources, run, ctx):
    rules = menu_rules(sources)
    bad = []
    for path, selector, body in rules:
        for match in re.finditer(r"z-index:\s*(\d+)", body):
            if int(match.group(1)) > 1000:
                bad.append("%s %s z-index %s" % (Path(path).name, selector[:24], match.group(1)))
    if bad:
        return FAIL, "menu stacking guessed rather than taken from the scale: %s" % "; ".join(bad[:4])
    if not rules:
        return UNKNOWN, "no menu rule found to inspect"
    return PASS, "menu stacking comes from the layer scale"


def check_option_states(gate, sources, run, ctx):
    options = [(p, sel, body) for p, text in sources.items() for sel, body in _css_blocks(text)
               if re.search(r"option|menu-?item|listbox|dropdown", sel, re.I)]
    if not options:
        return UNKNOWN, "no option rule found to inspect"
    joined_sel = " ".join(sel for _, sel, _ in options)
    have = {n for n, pattern in (("hover", r":hover"),
                                 ("focus", r":focus|aria-selected|data-highlighted|\[data-active"),
                                 ("selected", r"selected|aria-checked|\[data-state"))
            if re.search(pattern, joined_sel, re.I)}
    missing = {"hover", "focus", "selected"} - have
    if missing:
        return FAIL, ("options do not distinguish %s: arrowing through a menu while the mouse rests elsewhere "
                      "lights two rows the same way" % ", ".join(sorted(missing)))
    return PASS, "hover, focused, and selected are three distinct option states"


def check_picker_indicator(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"type=[\"'](date|time|datetime-local|month|week)", blob, re.I):
        return PASS, "no native date or time input"
    if re.search(r"calendar-picker-indicator", blob):
        return PASS, "the native picker indicator is themed"
    return FAIL, "a native date or time input with no themed calendar-picker-indicator renders the platform icon"


def check_accent_color(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"type=[\"'](checkbox|radio)|<Checkbox|<Switch|role=[\"']switch", blob, re.I):
        return PASS, "no checkbox, radio, or switch"
    if re.search(r"accent-color", blob, re.I):
        return PASS, "accent-color set on the native controls"
    return UNKNOWN, "checkbox or radio present with no accent-color - confirm the custom control owns its focus ring and semantics"


FIELD_PRESENT = re.compile(
    r"<input\b|<textarea\b|<Input\b|<TextField\b|type=[\"'](?:text|email|password|search|tel|url|number)"
    r"|<SignIn\b|<SignUp\b|<LoginForm\b", re.I)
AUTOFILL_RULE = re.compile(r"([^{}]*):-webkit-autofill[^{]*\{", re.I)
# A selector that reaches fields the project did not draw itself: the bare
# element, or a descendant of one, rather than a class only its own markup
# carries.
AUTOFILL_BROAD = re.compile(r"(?:^|[,\s>+~])(?:input|textarea|select)\s*:-webkit-autofill", re.I)


def check_autofill_surface(gate, sources, run, ctx):
    """Every field the page can autofill, not only the ones the project drew.

    The rule scoped to a project's own field class is the shape this gate exists
    to catch: it passes a source read, and the browser's yellow still lands on
    every embedded widget's field -- a hosted sign-in above all, which is the one
    form a visitor is most likely to have saved credentials for.
    """
    blob = "\n".join(sources.values())
    if not FIELD_PRESENT.search(blob):
        return PASS, "no field that a browser can autofill"
    selectors = AUTOFILL_RULE.findall(blob)
    if not selectors:
        return FAIL, ("no :-webkit-autofill override: the browser paints its own yellow over a "
                      "filled field and keeps it through hover and focus")
    if any(AUTOFILL_BROAD.search(sel) for sel in selectors):
        return PASS, "%d :-webkit-autofill rule(s), at least one reaching every field" % len(selectors)
    # The capture runs back to the previous rule, so report only the selector's
    # own last line rather than everything that preceded it.
    named = [sel.strip().splitlines()[-1].strip() for sel in selectors if sel.strip()]
    return FAIL, ("%d :-webkit-autofill rule(s), all scoped to the project's own field class (%s) - "
                  "a field drawn by an embedded widget stays yellow"
                  % (len(selectors), ", ".join(n[:40] for n in named[:3])))


def check_row_hover(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if re.search(r"hover:\[&>td\]|tr:hover\s*>?\s*td|tbody\s+tr:hover", blob, re.I):
        return PASS, "row hover reaches the cells"
    if re.search(r"tr:hover|hover:bg-", blob, re.I):
        return UNKNOWN, "a row hover exists - confirm it reaches the cells, since cell backgrounds paint over a tr background"
    return FAIL, "no row hover: a table row that can be clicked has to answer the pointer"


def check_clickable_row(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    clickable = re.search(r"<tr[^>]*onClick|onRowClick|handleSelectItem", blob)
    if not clickable:
        return PASS, "no clickable row"
    missing = [n for n, pattern in (("cursor: pointer", r"cursor-pointer|cursor:\s*pointer"),
                                    ("a hover state", r":hover|hover:"),
                                    ("a focus ring", r"focus-visible"),
                                    ("keyboard reachability", r"tabIndex|onKeyDown|role="))
               if not re.search(pattern, blob)]
    if missing:
        return FAIL, "clickable rows are missing %s" % ", ".join(missing)
    return PASS, "clickable rows carry cursor, hover, focus, and keyboard access"


def check_sticky_thead(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"<thead|<th\b", blob, re.I):
        return UNKNOWN, "no table header found - confirm columns stay labelled while the body scrolls"
    if re.search(r"(thead|th)[^{}]*\{[^}]*position:\s*sticky", blob, re.I | re.S) or \
       re.search(r"sticky\s+top-0", blob):
        return PASS, "the header row sticks"
    return FAIL, "the table header does not stick: columns lose their labels by the second screen"


def check_tabular_nums(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    numeric = re.search(r"toLocaleString|toFixed|\$\{|count|total|amount|price|qty", blob, re.I)
    if not numeric:
        return UNKNOWN, "no numeric column detected - confirm figures use tabular numerals"
    if re.search(r"tabular-nums|font-variant-numeric", blob, re.I):
        return PASS, "figures use tabular numerals"
    return FAIL, "numeric content with no tabular numerals: digits will not line up down the column"


def check_row_separator(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    stripe = re.search(r"nth-child\(\s*(even|odd|2n)|odd:bg-|even:bg-", blob, re.I)
    rule = re.search(r"(tr|td)[^{}]*\{[^}]*border-(bottom|top)", blob, re.I | re.S) or re.search(r"divide-y|border-b\b", blob)
    if stripe and rule:
        return FAIL, "rows carry both a separator rule and zebra striping: pick one"
    if not stripe and not rule:
        return UNKNOWN, "no row separator found - confirm rows are distinguishable"
    return PASS, "rows are separated one way"


def check_aria_sort(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"sort", blob, re.I):
        return PASS, "no sortable column"
    if re.search(r"aria-sort", blob):
        return PASS, "the sorted column announces its direction"
    return FAIL, "sorting exists with no aria-sort: the current sort is visible only to sighted mouse users"


def check_table_overflow(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"<table", blob, re.I):
        return UNKNOWN, "no table element found"
    if re.search(r"overflow-x[-:]\s*auto|overflow-x-auto", blob, re.I):
        return PASS, "the table scrolls inside its own container"
    if re.search(r"@media[^{]*max-width|sm:hidden|md:table-cell", blob):
        return UNKNOWN, "responsive rules exist near the table - confirm it stacks or drops columns rather than overflowing"
    return FAIL, "a table with no overflow container and no small-screen layout will push the page sideways"


def check_hover_only_actions(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    mounted = re.search(r"(hovered|isHover|onMouseEnter)[^\n]{0,80}&&", blob)
    if mounted:
        return FAIL, "actions mounted on hover do not exist on touch and are hard to reach by keyboard"
    if re.search(r"group-hover:(opacity|visible|flex|block)", blob):
        return UNKNOWN, "hover-revealed actions found - confirm they are present at reduced emphasis and reachable on focus"
    return PASS, "no hover-only actions"


def check_table_states(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"<table|<tbody", blob, re.I):
        return UNKNOWN, "no table element found"
    have = {n for n, pattern in (("empty", r"no results|nothing (yet|here)|empty|isEmpty|length === 0"),
                                 ("loading", r"isLoading|loading|Skeleton"),
                                 ("failed", r"error|failed|retry"))
            if re.search(pattern, blob, re.I)}
    missing = {"empty", "loading", "failed"} - have
    if missing:
        return FAIL, "the table has no %s state" % " or ".join(sorted(missing))
    return PASS, "empty, loading, and failed states are all present"


def check_semantic_table(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if re.search(r"<table", blob, re.I):
        missing = [n for n, pattern in (("scope on header cells", r"<th[^>]*scope="),
                                        ("a caption or accessible name", r"<caption|aria-label|aria-labelledby"))
                   if not re.search(pattern, blob, re.I)]
        if missing:
            return FAIL, "the table is missing %s" % ", ".join(missing)
        return PASS, "real table semantics with scope and an accessible name"
    if re.search(r"role=[\"']table|role=[\"']grid", blob):
        return UNKNOWN, "an ARIA table role without table elements - confirm the whole grid pattern is implemented"
    return UNKNOWN, "no table element found"


def check_sticky_header_surface(gate, sources, run, ctx):
    problems = []
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if not re.search(r"position:\s*sticky|position:\s*fixed", body):
                continue
            if not re.search(r"header|nav|toolbar|topbar|thead|\bth\b", selector, re.I):
                continue
            if not re.search(r"background", body, re.I):
                problems.append("%s %s has no background" % (Path(path).name, selector.strip()[:30]))
            if not re.search(r"border|box-shadow", body, re.I):
                problems.append("%s %s has no rule or shadow beneath it" % (Path(path).name, selector.strip()[:30]))
    blob = "\n".join(sources.values())
    if not problems and re.search(r"sticky\s+top-0", blob) and not re.search(r"sticky[^\"']*bg-", blob):
        problems.append("a sticky utility with no background utility beside it")
    if problems:
        return FAIL, "; ".join(dict.fromkeys(problems))[:400]
    return PASS, "sticky headers are opaque and separated from what scrolls under them"


def check_sticky_offset(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"position:\s*(sticky|fixed)|sticky\s+top-0|fixed\s+top-0", blob):
        return PASS, "nothing sticks to the top"
    if re.search(r"scroll-margin-top|scroll-padding-top|--header-height|--banner-height", blob):
        return PASS, "the header's height is reserved for anchors and scroll"
    return FAIL, "a sticky header with no scroll-margin-top or reserved height hides the first row it lands on"


def check_header_height_token(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not re.search(r"position:\s*(sticky|fixed)|sticky\s+top-0", blob):
        return PASS, "no sticky header to reserve space for"
    if re.search(r"--(header|banner|topbar|nav)-height", blob):
        return PASS, "the header height is a token"
    return FAIL, "the header height is hardcoded: sticky offsets and scroll padding will drift from it"


NAV_SELECTOR = re.compile(r"\b(nav|navbar|nav-bar|header|masthead|topbar|globalnav|site-?head)", re.I)
NAV_PANEL_SELECTOR = re.compile(
    r"(nav|header|global|site)[a-z_-]*(menu|panel|dropdown|flyout|popover|popup|submenu)"
    r"|mega[-_]?menu|flyout|(menu|panel|dropdown)[a-z_-]*(nav|header)", re.I)
# A scrolled state is spelled a dozen ways across frameworks; what matters is that
# one of them exists, since a bar with a single transparent state has nothing to
# swap to when content arrives underneath it.
NAV_SCROLL_STATE = re.compile(
    r"scrolled|is-fixed|is-stuck|is-pinned|--solid|--sticky|--compact|--fixed"
    r"|data-(scrolled|stuck|pinned|solid|sticky|state)|headroom|scrollY|scrollTop"
    r"|useScroll|IntersectionObserver", re.I)


def nav_rules(sources):
    """Every CSS rule whose selector names the bar, with its file and body.

    _css_blocks on a 300KB minified stylesheet is slow, so slice each file to the
    windows around a nav selector match before parsing. A bar's rules live in a
    handful of blocks, not scattered across the whole sheet.
    """
    out = []
    for p, text in sources.items():
        windows = []
        for m in NAV_SELECTOR.finditer(text):
            start = max(0, m.start() - 200)
            end = min(len(text), m.end() + 2000)
            if not windows or start > windows[-1][1]:
                windows.append([start, end])
            else:
                windows[-1][1] = max(windows[-1][1], end)
        for start, end in windows[:200]:
            for sel, body in _css_blocks(text[start:end]):
                if NAV_SELECTOR.search(sel):
                    out.append((p, sel.strip(), body))
    return out


def nav_markup(sources):
    """The first header or nav element in each source, which is where the bar lives."""
    out = {}
    for path, text in sources.items():
        match = (re.search(r"<header\b.*?</header>", text, re.S | re.I)
                 or re.search(r"<nav\b.*?</nav>", text, re.S | re.I))
        if match:
            out[path] = match.group(0)
    return out


def check_nav_two_states(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    bars = [(path, sel, body) for path, sel, body in nav_rules(sources)
            if re.search(r"position:\s*(sticky|fixed)", body)]
    transparent = [(path, sel) for path, sel, body in bars
                   if re.search(r"background(-color)?:\s*(transparent|none|rgba\([^)]*,\s*0\s*\))", body, re.I)]
    if not transparent and not re.search(r"(fixed|sticky)[^\"']*bg-transparent", blob):
        if bars:
            return PASS, "the bar carries an opaque surface in its only state"
        return UNKNOWN, "no positioned bar rule found - confirm the bar has a surface once content scrolls under it"
    if NAV_SCROLL_STATE.search(blob):
        return PASS, "the bar declares a scrolled state to swap its surface to"
    where = ", ".join("%s %s" % (Path(p).name, s[:30]) for p, s in transparent[:4]) or "a transparent utility"
    return FAIL, ("the bar is transparent with no scrolled state to swap to, so content scrolls "
                  "through it: %s" % where)


def check_nav_disclosure_trigger(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    if not NAV_PANEL_SELECTOR.search(blob):
        return PASS, "the bar opens no panel"
    faults = []
    if not re.search(r"aria-expanded", blob):
        faults.append("no aria-expanded on any trigger")
    for path, seg in nav_markup(sources).items():
        for match in re.finditer(r"<a\b[^>]*>", seg, re.I):
            tag = match.group(0)
            if re.search(r"onClick|onPress|@click|v-on:click", tag) and not re.search(r"href=[\"'](?!#?[\"'])", tag):
                faults.append("%s:%d is an anchor used as a trigger"
                              % (Path(path).name, seg[: match.start()].count("\n") + 1))
    if faults:
        return FAIL, "; ".join(dict.fromkeys(faults))[:400]
    if not re.search(r"Escape|Esc\b|keyCode === 27", blob):
        return UNKNOWN, "triggers declare aria-expanded - confirm Escape closes the panel and returns focus"
    return PASS, "panel triggers are buttons that declare their expansion and close on Escape"


def check_nav_primary_action(gate, sources, run, ctx):
    strong = re.compile(r"(btn|button|cta)[-_]?(primary|solid|filled)|primary[-_]?(btn|button|cta)"
                        r"|variant=[\"']primary[\"']|isPrimary", re.I)
    for path, seg in nav_markup(sources).items():
        hits = [m.group(0)[:40] for m in strong.finditer(seg)]
        if len(hits) > 1:
            return FAIL, ("%d primary buttons in the bar - one action is allowed to be loud: %s"
                          % (len(hits), ", ".join(dict.fromkeys(hits))[:200]))
    if not nav_markup(sources):
        return UNKNOWN, "no bar markup found - confirm at most one action in it is a filled button"
    return PASS, "at most one filled action closes the bar"


def check_nav_hide_on_scroll(gate, sources, run, ctx):
    # A hide-on-scroll construct uses translateY(-) or a negative top offset to
    # move the whole bar out of view. `display: none` is a utility for hiding
    # something entirely, not for scroll behavior, so it does not qualify.
    hides = [(path, sel) for path, sel, body in nav_rules(sources)
             if re.search(r"translateY\(\s*-|top:\s*calc\([^)]*-\s*var|top:\s*-", body)
             and re.search(r"\bhidden\b|is-up|scrolled-down|scroll-up|--away|data-hidden|pinned", sel, re.I)]
    if not hides:
        return PASS, "the bar does not hide"
    blob = "\n".join(sources.values())
    if re.search(r"lastScroll|prevScroll|previousScroll|scrollDirection|deltaY|scrollingUp|direction ===", blob, re.I):
        return PASS, "the bar hides on scroll and reads direction to come back"
    where = ", ".join("%s %s" % (Path(p).name, s[:30]) for p, s in hides[:3])
    return FAIL, ("the bar hides with no upward-scroll detection, so it stays gone until the "
                  "reader reaches the top: %s" % where)


def check_nav_panel_seam(gate, sources, run, ctx):
    gaps = []
    for path, text in sources.items():
        for m in list(NAV_PANEL_SELECTOR.finditer(text))[:60]:
            window = text[max(0, m.start() - 100):min(len(text), m.end() + 1000)]
            for selector, body in _css_blocks(window):
                if not NAV_PANEL_SELECTOR.search(selector):
                    continue
                if not re.search(r"position:\s*(absolute|fixed)", body):
                    continue
                gap = (re.search(r"top:\s*calc\(\s*100%\s*\+\s*([0-9.]+)(px|r?em)", body)
                       or re.search(r"margin-top:\s*([0-9.]+)(px|r?em)", body))
                if gap and float(gap.group(1)) > 0:
                    gaps.append("%s %s leaves %s%s" % (Path(path).name, selector.strip()[:30],
                                                       gap.group(1), gap.group(2)))
    if gaps:
        return FAIL, ("the panel floats clear of the bar rather than continuing it: %s"
                      % "; ".join(dict.fromkeys(gaps))[:300])
    blob = "\n".join(sources.values())
    if NAV_PANEL_SELECTOR.search(blob) and not re.search(r"backdrop|scrim|overlay|dim|::backdrop", blob, re.I):
        return FAIL, "an open nav panel with nothing dimming the page behind it"
    return PASS, "the panel continues the bar and the page behind it is dimmed"


def check_nav_top_level_count(gate, sources, run, ctx):
    for path, seg in nav_markup(sources).items():
        if NAV_PANEL_SELECTOR.search(seg):
            continue
        labels = [t.strip() for t in re.findall(r"<(?:a|button)\b[^>]*>\s*([^<>]{2,40}?)\s*<", seg)]
        labels = [t for t in labels if not re.match(r"^(log ?in|sign ?in|sign ?up|get started|contact"
                                                    r"|menu|search|close|skip)", t, re.I)]
        if len(labels) > 7:
            return FAIL, ("%s carries %d top-level destinations: %s"
                          % (Path(path).name, len(labels), ", ".join(labels[:10])))
        wrapped = [t for t in labels if len(t.split()) > 2]
        if wrapped:
            return FAIL, "top-level labels run past two words: %s" % ", ".join(wrapped[:4])
    return PASS, "the top level stays inside four to seven short destinations"


def check_nav_panel_directory(gate, sources, run, ctx):
    for path, text in sources.items():
        for match in re.finditer(r"<(div|nav|ul|section)\b[^>]*(?:mega|flyout|nav[a-z_-]*(?:menu|panel|dropdown))"
                                 r"[^>]*>(.{0,6000}?)</\1>", text, re.S | re.I):
            panel = match.group(2)
            links = re.findall(r"<a\b[^>]*>", panel, re.I)
            if len(links) <= 6:
                continue
            headed = re.search(r"<h[2-6]\b|(group|section|column)[-_]?(title|head|label)", panel, re.I)
            described = len(re.findall(r">[^<>]{25,140}<", panel)) >= max(2, len(links) // 3)
            if not headed and not described:
                return FAIL, ("%s:%d holds %d destinations with neither headings nor descriptions"
                              % (Path(path).name, text[: match.start()].count("\n") + 1, len(links)))
    return PASS, "large panels are grouped and their destinations described"


ALPHA_GLASS_CEILING = 0.9


def _default_scope_only(text):
    """Return the concatenated body of every :root block not scoped to a theme.

    A rule qualified by [data-theme], .dark, or @media prefers-color-scheme is
    a theme override; its declarations do not answer for what the default
    surface looks like. Only the plain :root (optionally paired with html)
    contributes.
    """
    bodies = []
    for m in re.finditer(r"(?:^|[\s\n};])(:root(?:\s*,\s*html)?)\s*\{([^{}]*)\}", text):
        selector = m.group(1).strip()
        preceding = text[max(0, m.start() - 60):m.start()]
        if re.search(r"prefers-color-scheme:\s*dark|\.dark\b", preceding):
            continue
        bodies.append(m.group(2))
    return "\n".join(bodies)


def _read_color_value(val):
    """Return (alpha_ok, dark) for one CSS colour string."""
    alpha_ok = False
    dark = False
    val = val.strip()
    am = re.search(r"rgba?\([^)]*,\s*([01](?:\.\d+)?)\s*[,)]", val)
    hm = re.search(r"hsla?\([^)]*,\s*([01](?:\.\d+)?)\s*\)", val)
    if am and float(am.group(1)) <= ALPHA_GLASS_CEILING:
        alpha_ok = True
    if hm and float(hm.group(1)) <= ALPHA_GLASS_CEILING:
        alpha_ok = True
    hx = re.match(r"#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$", val)
    if hx and hx.group(2) and int(hx.group(2), 16) / 255.0 <= ALPHA_GLASS_CEILING:
        alpha_ok = True
    dm = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", val)
    if dm and sum(int(g) for g in dm.groups()) < 300:
        dark = True
    if hx and sum(int(hx.group(1)[i:i + 2], 16) for i in (0, 2, 4)) < 300:
        dark = True
    return alpha_ok, dark


def _surface_signal(token_name, all_text, seen=None):
    """Return (alpha_ok, dark, hits) for a token used on a bar's background.

    Reads the default light-mode value only - a token that gets rewritten under
    prefers-color-scheme, [data-theme], or a light-theme override does not
    change what the bar looks like when nothing is set, which is what SLP-42
    reads. Alpha_ok is true at alpha 0.9 or below; dark is true when the
    visible channels sum below 300. A value of the form var(--other) is
    resolved by looking up --other in the same default scope, capped at a
    handful of hops so a self-referential chain does not spin.
    """
    if seen is None:
        seen = set()
    if token_name in seen or len(seen) > 8:
        return False, False, []
    seen.add(token_name)
    default_text = _default_scope_only(all_text)
    alpha_ok = False
    dark = False
    hits = []
    for prefix in ("", "bg-", "surface-", "color-", "colors-"):
        var = "--%s%s" % (prefix, token_name)
        for m in re.finditer(re.escape(var) + r":\s*([^;}]+)", default_text):
            val = m.group(1).strip()
            hits.append(val[:80])
            vm = re.match(r"var\(\s*--([a-zA-Z][a-zA-Z0-9_-]*)\s*(?:,[^)]*)?\)$", val)
            if vm:
                a2, d2, h2 = _surface_signal(vm.group(1), all_text, seen)
                if a2:
                    alpha_ok = True
                if d2:
                    dark = True
                hits.extend(h2)
                continue
            a2, d2 = _read_color_value(val)
            if a2:
                alpha_ok = True
            if d2:
                dark = True
    return alpha_ok, dark, hits


def check_nav_ai_default(gate, sources, run, ctx):
    """A sticky or fixed header must earn its shape.

    A bar comparable to the reference sites - Stripe, Apple, Anduril - carries
    at least one polish signal: a see-through surface with a real blur (alpha
    at or below 0.9 in its default scope paired with backdrop-blur), a
    scroll-state swap that changes the surface between at-top and scrolled, a
    distinctive dark ground, or a gradient. A bar with none of those on a plain
    hairline is the AI-default template regardless of how many links it holds.
    """
    all_text = "\n".join(sources.values())
    scroll_swap = bool(re.search(
        r"data-scrolled|data-hidden|scrollDirection|useScroll|onScroll|"
        r"headroom|is-scrolled|isScrolled|scrolled:\s*bg-|"
        r"header--(scrolled|solid|compact|fixed)|scrollY|scrollPosition",
        all_text,
    ))
    faults = []
    for path, text in sources.items():
        for hm in re.finditer(r"<header\b[^>]*>", text):
            open_tag = hm.group(0)
            if not re.search(r"\bsticky\b|\bfixed\b|position:\s*(sticky|fixed)", open_tag):
                continue
            if not re.search(r"border-b\b|border-bottom:\s*[^0]|shadow-(sm|card|md)\b|shadow:\s*[^0]", open_tag):
                continue
            if re.search(r"bg-gradient|linear-gradient\(|radial-gradient\(", open_tag):
                continue
            has_alpha = False
            dark = False
            token_name = None
            bg = None
            arb = re.search(r"bg-\[(?:color:)?([^\]]+)\]", open_tag)
            if arb:
                inner = arb.group(1)
                token_name = inner
                vm = re.match(r"var\(\s*--([a-zA-Z][a-zA-Z0-9_-]*)\s*(?:,[^)]*)?\)$", inner)
                if vm:
                    alpha_ok, is_dark, _ = _surface_signal(vm.group(1), all_text)
                    has_alpha = alpha_ok
                    dark = is_dark
                else:
                    a2, d2 = _read_color_value(inner)
                    has_alpha = a2
                    dark = d2
            else:
                bg = re.search(r"bg-([a-zA-Z][a-zA-Z0-9_-]*)(?:/(\d+))?", open_tag)
                if bg:
                    token_name = bg.group(1)
                    if bg.group(2) and int(bg.group(2)) <= int(ALPHA_GLASS_CEILING * 100):
                        has_alpha = True
                    else:
                        alpha_ok, is_dark, _ = _surface_signal(token_name, all_text)
                        has_alpha = alpha_ok
                        dark = is_dark
            has_blur = bool(re.search(r"backdrop-blur|backdrop-filter:\s*[^;]*blur", open_tag))
            if scroll_swap or dark or (has_alpha and has_blur):
                continue
            surface = "bg=%s" % (token_name or "?")
            if token_name and not has_alpha:
                surface += "/opaque"
            if has_blur and not has_alpha:
                surface += "/blur-with-no-alpha"
            faults.append("%s (%s)" % (Path(path).name, surface))
    surface_fault = list(faults)
    structure_faults = []
    for path, text in sources.items():
        for hm in re.finditer(r"<header\b[^>]*>", text):
            close = text.find("</header>", hm.end())
            if close < 0:
                continue
            block = text[hm.start():close]
            open_tag = hm.group(0)
            is_site_nav = (
                bool(re.search(r"<nav\b", block))
                or re.search(r"\bsticky\b|\bfixed\b|position:\s*(sticky|fixed)|globalnav|SiteHeader|Navbar|navigation", open_tag + block[:400])
            )
            if not is_site_nav:
                continue
            if re.search(r"section-header|card-header|block-header|carousel__header|event-header|panel-header", open_tag, re.I):
                continue
            items = re.findall(r"<(?:a|NavLink|Link)\b[^>]*(?:to|href)=[^>]*>", block)
            items = [i for i in items if not re.search(
                r"(sign[- ]?in|log[- ]?in|sign[- ]?up|get started|contact|home|logo|"
                r"tel:|mailto:|aria-label=[\"'][^\"']*(home|logo)|Wordmark)", i, re.I)]
            trigger_tags = re.findall(r"<(?:button|a|div|Trigger)\b[^>]*aria-expanded=[^>]*>", text, re.S)
            desktop_triggers = [t for t in trigger_tags if not re.search(
                r"md:hidden|lg:hidden|sm:hidden|mobile-only|hamburger|menu-toggle|"
                r"aria-label=[\"'][^\"']*(menu|hamburger)", t, re.I,
            )]
            has_panel = len(desktop_triggers) > 0 or bool(re.search(
                r"<(?:div|nav|ul|section)\b[^>]*(?:mega|flyout|dropdown-panel|nav[a-z_-]*(?:menu|panel|dropdown))",
                text, re.I | re.S,
            ))
            if has_panel:
                continue
            structure_faults.append("%s (%d desktop link%s, none of which opens a panel)"
                                     % (Path(path).name, len(items), "" if len(items) == 1 else "s"))
    if surface_fault or structure_faults:
        parts = []
        if surface_fault:
            parts.append("no polish signal: %s" % "; ".join(dict.fromkeys(surface_fault))[:200])
        if structure_faults:
            parts.append("no structural richness: %s" % "; ".join(dict.fromkeys(structure_faults))[:200])
        return FAIL, ("nav bar is not comparable to Stripe/Apple/Anduril - a bar "
                      "earns its shape through both surface polish (alpha with real "
                      "backdrop-blur, a scroll-state swap, or a dark ground) AND depth "
                      "(a disclosure panel behind a trigger carrying aria-expanded; a "
                      "longer row of flat links is still a link list). Missing: %s"
                      % " | ".join(parts))
    return PASS, "the bar carries both a polished surface and a rich structure"


def check_surface_hover(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    containers = re.search(r"<(tr|li|article)[^>]*onClick|card[^\"']*onClick|onRowClick", blob, re.I)
    if not containers:
        return PASS, "no clickable row, card, or tile"
    if re.search(r"(tr|li|article|card)[^{}]*:hover|hover:bg-|group-hover:bg-", blob, re.I):
        return PASS, "the clickable surface tints on hover"
    return FAIL, "a clickable row or card with no surface hover reads as inert"


def check_global_user_select(gate, sources, run, ctx):
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if re.search(r"user-select:\s*none", body, re.I) and re.match(r"^\s*(\*|body|html|:root)\s*$", selector):
                return FAIL, "user-select: none applied page-wide in %s stops readers copying the content" % Path(path).name
    return PASS, "selection is blocked only on controls, if at all"


# ---------------------------------------------------------------- spacing

def spacing_verdict(run, dimension):
    """Judge one spacing relationship from the rendered capture."""
    cap = run.get("spacing")
    if not cap:
        return FAIL, ("no spacing capture on this run - run scripts/code/spacing-probe.js in the page, "
                      "then: design-pass.py spacing-probe --file <capture.json>")
    if dimension == "captured":
        return PASS, "captured at %s over %d stacked container(s)" % (
            cap.get("captured", "an unstated viewport"), cap.get("containers", 0))
    if dimension == "adjacent":
        hits = cap.get("adjacent", [])
        if hits:
            return FAIL, "%d pair(s) of panels share an edge: %s" % (
                len(hits), "; ".join("%s / %s in %s" % (h["a"], h["b"], h["parent"]) for h in hits[:4]))
        return PASS, "no two panels meet at zero gap"
    if dimension == "double-border":
        hits = cap.get("doubleBorder", [])
        if hits:
            return FAIL, "%d touching pair(s) draw two borders on one edge: %s" % (
                len(hits), "; ".join("%s / %s" % (h["a"], h["b"]) for h in hits[:4]))
        return PASS, "no doubled borders on a shared edge"
    if dimension == "uniform":
        hits = cap.get("uneven", [])
        if hits:
            return FAIL, "%d stack(s) with uneven gaps: %s" % (
                len(hits), "; ".join("%s %s" % (h["parent"], h["gaps"]) for h in hits[:4]))
        return PASS, "every stack spaces its children evenly"
    if dimension == "inset":
        hits = cap.get("inset", [])
        if hits:
            return FAIL, "%d child element(s) flush against a panel edge: %s" % (
                len(hits), "; ".join("%s in %s" % (h["child"], h["container"]) for h in hits[:4]))
        return PASS, "content keeps its inset from every container edge"
    if dimension == "tail":
        hits = cap.get("tail", [])
        if hits:
            return FAIL, "%d container(s) end flush with their last child: %s" % (
                len(hits), "; ".join("%s" % h["container"] for h in hits[:4]))
        return PASS, "every container keeps space beneath its last child"
    if dimension == "grouping":
        hist = {int(k): v for k, v in (cap.get("gapHistogram") or {}).items()}
        if len(hist) < 2:
            return UNKNOWN, "the page uses one gap value throughout - confirm grouping is carried some other way"
        steps = sorted(hist)
        close = [(a, b) for a, b in zip(steps, steps[1:]) if 0 < b - a <= 3]
        if close:
            return FAIL, ("gap tiers sit within 3px of each other and read as inconsistency rather than "
                          "hierarchy: %s" % ", ".join("%dpx vs %dpx" % (a, b) for a, b in close[:4]))
        return PASS, "gap tiers are distinct: %s" % ", ".join("%dpx" % s for s in steps[:8])
    if dimension == "field-rhythm":
        hits = [h for h in cap.get("uneven", []) if re.search(r"label|field|form|input", h["parent"], re.I)]
        if hits:
            return FAIL, "%d field group(s) space label, control, and helper differently: %s" % (
                len(hits), "; ".join("%s %s" % (h["parent"], h["gaps"]) for h in hits[:3]))
        return PASS, "field groups share one internal rhythm"
    return UNKNOWN, "unknown spacing dimension %s" % dimension


def check_spacing_probe(gate, sources, run, ctx):
    return spacing_verdict(run, gate["check"]["dimension"])


def check_spacing_tiers(gate, sources, run, ctx):
    return spacing_verdict(run, "grouping")


def check_stack_gap(gate, sources, run, ctx):
    """A stack spacing its children by margin rather than by the container's gap."""
    suspects = []
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if not re.search(r"display:\s*(flex|grid)", body):
                continue
            if re.search(r"\bgap\s*:|row-gap|column-gap", body):
                continue
            suspects.append("%s %s" % (Path(path).name, selector.strip()[:34]))
    blob = "\n".join(sources.values())
    child_margins = re.findall(r"margin-(?:top|bottom|block)[^:]*:\s*(?!0)", blob)
    if suspects and child_margins:
        return FAIL, ("%d flex/grid container(s) with no gap, alongside %d vertical margin(s) on children - "
                      "spacing a stack by margin collapses and goes missing when a child does: %s"
                      % (len(suspects), len(child_margins), "; ".join(suspects[:4])))
    if suspects:
        return UNKNOWN, "%d flex/grid container(s) declare no gap: %s - confirm each is a single-child or intentionally packed" % (
            len(suspects), "; ".join(suspects[:4]))
    return PASS, "stacks space their children with gap"


def check_conditional_margin(gate, sources, run, ctx):
    """Spacing carried on a conditionally rendered block's own margin."""
    hits = []
    for path, text in sources.items():
        for match in re.finditer(r"\{[^{}\n]{0,60}&&[^\n]{0,120}", text):
            window = match.group(0)
            if re.search(r"(mt-|mb-|my-|margin-top|margin-bottom)", window):
                line = text[: match.start()].count("\n") + 1
                hits.append("%s:%d" % (Path(path).name, line))
    if hits:
        return FAIL, ("conditionally rendered block(s) carrying their own vertical margin at %s - the gap "
                      "stays behind when the block does not render" % ", ".join(dict.fromkeys(hits))[:300])
    return PASS, "no conditional block carries its own vertical margin"


def cmd_spacing_probe(argv):
    """Record a rendered spacing capture so the SPC gates can be settled."""
    flags = parse_flags(argv)
    if not flags.get("file"):
        raise SystemExit("--file <capture.json> is required; produce it with scripts/code/spacing-probe.js")
    data = json.loads(Path(flags["file"][0]).expanduser().read_text())
    if "containers" not in data:
        raise SystemExit("that file is not a spacing capture; run spacingProbe.scan() and save its output")
    run = load_run()
    run["spacing"] = data
    save_run(run)
    print("Recorded a spacing capture at %s over %d stacked container(s).\n"
          % (data.get("captured", "an unstated viewport"), data.get("containers", 0)))
    for dimension in ("adjacent", "double-border", "uniform", "inset", "tail", "grouping", "field-rhythm"):
        status, detail = spacing_verdict(run, dimension)
        print("  %-13s %s: %s" % (dimension, status, detail))
    return 0


def cmd_layout_probe(argv):
    """Record a rendered layout capture so the measured marketing gates can settle."""
    flags = parse_flags(argv)
    if not flags.get("file"):
        raise SystemExit("--file <capture.json> is required; produce it with scripts/code/layout-probe.js")
    data = json.loads(Path(flags["file"][0]).expanduser().read_text())
    if data.get("error"):
        raise SystemExit("that capture reports: %s" % data["error"])
    if "hero" not in data and "nav" not in data:
        raise SystemExit("that file is not a layout capture; run layoutProbe.scan() and save its output")
    run = load_run()
    run["layout"] = data
    save_run(run)
    hero, nav = data.get("hero") or {}, data.get("nav") or {}
    print("Recorded a layout capture at %s (hero %s, nav %s).\n"
          % (data.get("captured", "an unstated viewport"),
             "found" if hero.get("found") else "not found",
             "found" if nav.get("found") else "not found"))
    for dimension in sorted(LAYOUT_DIMENSIONS):
        status, detail = layout_verdict(run, dimension)
        print("  %-14s %s: %s" % (dimension, status, detail))
    return 0



def cmd_mobile_probe(argv):
    """Record a phone capture so the measured mobile gates can settle."""
    flags = parse_flags(argv)
    if not flags.get("file"):
        raise SystemExit("--file <capture.json> is required; produce it with "
                         "scripts/code/mobile-probe.js at a phone width")
    data = json.loads(Path(flags["file"][0]).expanduser().read_text())
    if data.get("error"):
        raise SystemExit("that capture reports: %s" % data["error"])
    if "reach" not in data and "menu" not in data:
        raise SystemExit("that file is not a phone capture; run mobileProbe.scan() and save its output")
    run = load_run()
    run["mobile"] = data
    save_run(run)
    print("Recorded a phone capture at %s.\n" % data.get("captured", "an unstated viewport"))
    for dimension in sorted(MOBILE_DIMENSIONS):
        status, detail = mobile_verdict(run, dimension)
        print("  %-15s %s: %s" % (dimension, status, detail))
    return 0


READ_FLAGS = {
    "page-kind": "page_kind",
    "audience": "audience",
    "vibe": "vibe",
    "reference": "reference",
    "constraint": "constraint",
    "palette": "palette",
    "face": "face",
    "assets": "assets",
}


def cmd_read(argv):
    """Record the design read: what the brief is, before the first file is written."""
    flags = parse_flags(argv)
    run = load_run()
    read = run.setdefault("read", {})
    for flag, field in READ_FLAGS.items():
        if flags.get(flag):
            read[field] = " ".join(flags[flag])
    read["at"] = now()
    save_run(run)
    missing = [f for f in READ_FIELDS if not read.get(f)]
    print("Design read on run %s:" % run["id"])
    for field in ("page_kind", "audience", "vibe", "reference", "constraint", "palette", "face", "assets"):
        if read.get(field):
            print("  %-10s %s" % (field.replace("_", " "), read[field]))
    if missing:
        print("\nStill missing: %s" % ", ".join(m.replace("_", " ") for m in missing))
        return 1
    previous = _previous_in_genre(read["page_kind"].strip().lower(), run)
    if previous:
        print("\nThe last %s run used palette '%s' and face '%s'; this one has to differ."
              % (read["page_kind"], previous.get("palette") or "unrecorded",
                 previous.get("face") or "unrecorded"))
    return 0


# ---------------------------------------------------------------- coverage

# The difference between "this codebase contains a press effect" and "every
# pressable thing in it has one". The first is what a presence check answers, and
# it passes an app with forty buttons and one :active - which is how a design run
# reports that press feedback is handled while most of the app has none.

STATE_VARIANTS = {
    "active": ("active:", r":active"),
    "hover": ("hover:", r":hover"),
    "focus-visible": ("focus-visible:", r":focus-visible"),
    "cursor": ("cursor-pointer", r"cursor:\s*pointer"),
    "touch-action": ("touch-manipulation", r"touch-action:\s*manipulation"),
}

# Tags that carry the affordance natively, per state.
NATIVE = {"cursor": {"a"}}

INTERACTIVE_TAG = re.compile(
    r"<(button|a|Link|NavLink|Pressable|TouchableOpacity)\b([^>]*)>|"
    r"<([a-zA-Z][\w.]*)\b([^>]*\b(?:onClick|onPress)\s*=[^>]*)>",
    re.S,
)


def global_state_cover(sources, state):
    """Element tags and shared classes that a stylesheet already covers for a state."""
    _, css_state = STATE_VARIANTS[state]
    tags, classes = set(), set()
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            covered_here = re.search(css_state, selector) or (
                state != "cursor" and re.search(r"@apply[^;]*" + re.escape(STATE_VARIANTS[state][0]), body))
            if state == "cursor" and re.search(r"cursor:\s*pointer", body):
                covered_here = True
            # A property state such as touch-action names no pseudo-class, so it
            # reads as a plain declaration in the body rather than in the selector.
            if state == "touch-action" and re.search(css_state, body):
                covered_here = True
            if not covered_here:
                continue
            for part in re.split(r"[,]", selector):
                part = part.strip()
                for tag in re.findall(r"(?:^|[\s>+~])([a-z][a-z0-9]*)\b", part):
                    tags.add(tag)
                for cls in re.findall(r"\.([\w-]+)", part):
                    classes.add(cls)
                if re.search(r"\[role=[\"']button", part):
                    tags.add("role-button")
    return tags, classes


def interactive_elements(sources):
    """Every element in the markup that responds to a click, with its classes."""
    found = []
    for path, text in sources.items():
        if Path(path).suffix.lower() not in {".jsx", ".tsx", ".html", ".htm", ".vue", ".svelte", ".astro"}:
            continue
        for match in INTERACTIVE_TAG.finditer(text):
            tag = (match.group(1) or match.group(3) or "").lower()
            attrs = match.group(2) or match.group(4) or ""
            if tag in {"svg", "path", "i", "img"}:
                continue
            classes = " ".join(re.findall(r"class(?:Name)?\s*=\s*[\"'{]([^\"'}]*)", attrs))
            line = text[: match.start()].count("\n") + 1
            found.append({"file": Path(path).name, "line": line, "tag": tag,
                          "classes": classes, "attrs": attrs})
    return found


def check_interactive_coverage(gate, sources, run, ctx):
    state = gate["check"]["state"]
    variant, _ = STATE_VARIANTS[state]
    tags, classes = global_state_cover(sources, state)
    elements = interactive_elements(sources)
    if not elements:
        return UNKNOWN, "no interactive element found in the markup - check the affordances by hand"

    uncovered = []
    for el in elements:
        tag = el["tag"]
        base = {"link": "a", "navlink": "a"}.get(tag, tag)
        if base in NATIVE.get(state, set()) and "href" in el["attrs"]:
            continue
        if base in tags or ("role-button" in tags and 'role="button"' in el["attrs"]):
            continue
        if variant in el["classes"]:
            continue
        if any(c in classes for c in el["classes"].split()):
            continue
        uncovered.append("%s:%d <%s>" % (el["file"], el["line"], el["tag"]))

    total = len(elements)
    covered = total - len(uncovered)
    if uncovered:
        return FAIL, ("%d of %d interactive element(s) have no %s: %s%s"
                      % (len(uncovered), total, state, ", ".join(uncovered[:10]),
                         " and %d more" % (len(uncovered) - 10) if len(uncovered) > 10 else ""))
    return PASS, "all %d interactive element(s) covered for %s" % (covered, state)


def check_motion_coverage(gate, sources, run, ctx):
    """Whether the reduced-motion block reaches every animation, not just exists."""
    animated, reduced_scope, has_block = [], set(), False
    for path, text in sources.items():
        for name, _ in keyframe_bodies(text):
            animated.append("@keyframes %s (%s)" % (name, Path(path).name))
        for selector, body in _css_blocks(text):
            if re.search(r"transform|translate|animation:", body) and not re.search(r"prefers-reduced-motion", selector):
                if re.search(r"animation:|transition[^;]*transform", body):
                    animated.append("%s (%s)" % (selector.strip()[:34], Path(path).name))
        for match in re.finditer(r"@media[^{]*prefers-reduced-motion[^{]*\{", text):
            has_block = True
            depth, i = 1, match.end()
            while i < len(text) and depth:
                depth += 1 if text[i] == "{" else (-1 if text[i] == "}" else 0)
                i += 1
            inner = text[match.end():i]
            for selector, _ in _css_blocks(inner):
                reduced_scope.add(selector.strip())
    if not animated:
        blob = "\n".join(sources.values())
        if re.search(r"motion-reduce:|useReducedMotion", blob):
            return PASS, "motion is guarded per element with motion-reduce utilities"
        return PASS, "nothing animates"
    if not has_block:
        blob = "\n".join(sources.values())
        if re.search(r"motion-reduce:", blob):
            return UNKNOWN, ("%d animated rule(s) and motion-reduce utilities in play - confirm every one of "
                             "them carries the utility" % len(animated))
        return FAIL, "%d animated rule(s) with no prefers-reduced-motion block at all" % len(animated)
    catch_all = any(re.match(r"^[\s,]*(\*|html|body|:root)", sel) for sel in reduced_scope)
    if catch_all:
        return PASS, "a catch-all reduced-motion rule covers all %d animated rule(s)" % len(animated)
    return UNKNOWN, ("the reduced-motion block names %d selector(s) while %d rule(s) animate - confirm every "
                     "animation is reached: %s" % (len(reduced_scope), len(animated),
                                                   ", ".join(sorted(reduced_scope)[:6])))


DISPLAY_SELECTOR = re.compile(r"\bh[12]\b|display|__title|hero|masthead|headline", re.I)


def check_display_wrap_coverage(gate, sources, run, ctx):
    """Every display-size rule, not one of them, has to break long words."""
    missing, total = [], 0
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if not DISPLAY_SELECTOR.search(selector):
                continue
            if not re.search(r"font-size|clamp\(", body):
                continue
            total += 1
            if not re.search(r"overflow-wrap:\s*anywhere|word-break", body):
                missing.append("%s %s" % (Path(path).name, selector.strip()[:34]))
    if not total:
        blob = "\n".join(sources.values())
        if re.search(r"overflow-wrap:\s*anywhere", blob):
            return PASS, "overflow-wrap: anywhere present"
        return UNKNOWN, "no display-size rule found to inspect"
    if missing:
        return FAIL, "%d of %d display rule(s) lack overflow-wrap: anywhere: %s" % (
            len(missing), total, "; ".join(missing[:6]))
    return PASS, "all %d display rule(s) break long words" % total


def check_scroll_to_top(gate, sources, run, ctx):
    """Whether a route change puts the reader back at the top of the page."""
    blob = "\n".join(sources.values())
    if not re.search(r"useRouter|react-router|next/link|createBrowserRouter|<Route\b|useNavigate|usePathname", blob):
        return PASS, "no client-side routing in the deliverable"
    if re.search(r"<ScrollRestoration|scrollRestoration|scrollTo\(\s*0|scrollTo\(\{[^}]*top:\s*0|"
                 r"scrollIntoView\(\)[^\n]*top|window\.scroll\(0", blob):
        return PASS, "a route change returns the reader to the top"
    return FAIL, ("routes change with nothing scrolling the reader back to the top: the next page opens "
                  "wherever the last one was left")


# A click effect is the flourish that fires where the cursor is, independent of
# what was clicked. It is a different thing from the press feedback PTR-02 checks -
# that one answers for the control, this one answers for the click - and a site can
# have every button scaling on :active and still feel like nothing happens when you
# click it. It is also the thing an agent never adds unasked, because the rules
# nearest to it are the custom-cursor and particle bans, which are about replacing
# the pointer and about ambient decoration, not about answering a click.
GLOBAL_CLICK = re.compile(
    r"(document|window|body|ref\.current)\s*\.\s*addEventListener\s*\(\s*['\"`]"
    r"(click|pointerdown|mousedown)", re.I)
CLICK_POINT = re.compile(r"\b(clientX|pageX|offsetX)\b")
CLICK_NAMED = re.compile(r"click[-_ ]?(effect|spark|ripple|burst|wave|pulse|glow)|"
                         r"(ripple|spark|burst)[-_ ]?(at|on)?[-_ ]?click", re.I)


def click_effect_sources(sources):
    """Files that answer a click with something drawn at the pointer."""
    found = []
    for path, text in sources.items():
        if CLICK_NAMED.search(path) or CLICK_NAMED.search(text):
            found.append(path)
        elif GLOBAL_CLICK.search(text) and CLICK_POINT.search(text):
            found.append(path)
    return found


def check_click_effect(gate, sources, run, ctx):
    """Whether clicking anywhere on the page produces a visible answer at the cursor."""
    if not interactive_elements(sources):
        return PASS, "nothing in the deliverable is clickable"
    found = click_effect_sources(sources)
    if found:
        return PASS, "a click effect fires at the pointer (%s)" % ", ".join(sorted(found)[:3])
    return FAIL, ("clicking the page produces nothing at the cursor: press feedback answers for the "
                  "control that was hit, and nothing answers for the click itself")


def check_click_effect_safe(gate, sources, run, ctx):
    """A click effect must not swallow clicks, and must stand down for reduced motion."""
    found = click_effect_sources(sources)
    if not found:
        return PASS, "no click effect present; PTR-31 answers for that"
    blob = "\n".join(sources[p] for p in found if p in sources)
    faults = []
    # Tailwind spells it as a class, and a project that uses it never writes the
    # declaration at all - reading only for the CSS form failed every Tailwind site
    # for a layer that was already correct.
    if not re.search(r"pointer-events\s*:\s*none|pointerEvents\s*:\s*['\"`]none|pointer-events-none", blob):
        faults.append("it has no pointer-events: none, so the layer it draws on eats clicks")
    whole = "\n".join(sources.values())
    if not re.search(r"prefers-reduced-motion", whole):
        faults.append("nothing suppresses it under prefers-reduced-motion")
    if faults:
        return FAIL, "the click effect in %s: %s" % (", ".join(sorted(found)[:2]), "; ".join(faults))
    return PASS, "the click effect is non-blocking and stands down for reduced motion"


# ---------------------------------------------------------------- marketing surfaces

# A landing page is judged on things an app screen is not, and most of them are
# counts rather than judgements: how many eyebrows against how many sections, how
# many text elements in the hero, how many consecutive sections share a shape.
# Left as prose each one reads as a matter of taste; as a count each one has an
# answer.

TEXT_NODE = re.compile(r">([^<>{}\n][^<>{}]{0,400}?)<", re.S)
ATTR_TEXT = re.compile(r"\b(?:alt|title|placeholder|aria-label|label)\s*=\s*[\"']([^\"']{2,300})[\"']")
JSX_EXPR = re.compile(r"\{[^{}]*\}")

NEUTRALS = {"gray", "grey", "zinc", "slate", "stone", "neutral", "white", "black", "transparent",
            "current", "inherit"}
SEMANTIC = {"success", "error", "warning", "info", "danger", "destructive", "positive", "negative",
            "caution", "critical", "muted", "disabled", "invalid", "alert", "required", "expired",
            "overdue", "pending", "failed", "offline"}
HUE_NAMES = ["red", "orange", "amber", "yellow", "lime", "green", "emerald", "teal", "cyan",
             "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose"]


def _visible_strings(sources):
    """Every string a reader of the rendered page actually sees.

    Text between tags plus the attributes that surface as text. Interpolations are
    dropped: `{price}` is a value at runtime and judging its punctuation here would
    report on source that never reaches a screen.
    """
    out = []
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            for match in TEXT_NODE.finditer(line):
                body = JSX_EXPR.sub("", match.group(1)).strip()
                if body and not body.startswith(("//", "/*", "@")):
                    out.append((path, i, body))
            for match in ATTR_TEXT.finditer(line):
                out.append((path, i, match.group(1).strip()))
    return out


def _is_hero_file(path):
    return bool(re.search(r"(^|[/_-])hero", Path(path).stem, re.I))


HERO_MARK = re.compile(
    r"<(section|header|div|main)\b[^>]*(?:id|class|className)\s*=\s*[\"'{][^\"'}]*\bhero\b|<Hero\b",
    re.I,
)


def _hero_regions(sources):
    """The source of every hero, as (path, first line, text).

    A whole file when the file is the hero, otherwise a window opened at the hero
    marker and closed at the first section or header end after it. Matching JSX
    tags properly is not worth it here: the window only has to be tight enough
    that a rule about the hero is not answered by the section below it.
    """
    regions = []
    for path, text in sources.items():
        if _is_hero_file(path):
            regions.append((path, 1, text))
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not HERO_MARK.search(line):
                continue
            end = min(len(lines), i + 90)
            for j in range(i + 1, end):
                if re.search(r"</(section|header|main)>", lines[j], re.I):
                    end = j + 1
                    break
            regions.append((path, i + 1, "\n".join(lines[i:end])))
    return regions


SECTION_TAG = re.compile(r"<section\b|<Section\b", re.I)
HEADING_TAG = re.compile(r"<h2\b|<h3\b", re.I)


def _section_count(sources):
    """How many sections the page presents, by the strongest signal available."""
    blob = "\n".join(sources.values())
    tags = len(SECTION_TAG.findall(blob))
    if tags:
        return tags
    headings = len(HEADING_TAG.findall(blob))
    if headings:
        return headings
    return len(re.findall(r"<[A-Z][A-Za-z]*Section\b|<[A-Z][A-Za-z]+\s*/>", blob))


EYEBROW_UTILITY = re.compile(r"uppercase[^\"'`]{0,80}?tracking-|tracking-[^\"'`]{0,80}?uppercase")
EYEBROW_CSS = re.compile(r"text-transform\s*:\s*uppercase", re.I)


NEAR_HEADING = re.compile(r"<h[1-4]\b|text-(?:3xl|4xl|5xl|6xl|7xl)|font-(?:display|serif)\b|"
                          r"(?:class|className)\s*=\s*[\"'{][^\"'}]*\b(?:headline|heading|title)\b", re.I)


def _eyebrow_count(sources):
    """Uppercase wide-tracking micro-labels sitting above a heading."""
    count, where = 0, []
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if not EYEBROW_UTILITY.search(line):
                continue
            if not NEAR_HEADING.search("\n".join(lines[i:min(len(lines), i + 6)])):
                continue
            count += 1
            where.append("%s:%d" % (Path(path).name, i))
    for path, text in sources.items():
        for selector, body in _css_blocks(text):
            if EYEBROW_CSS.search(body) and re.search(r"letter-spacing\s*:\s*0?\.(0[6-9]|[1-9])", body):
                count += 1
                where.append("%s %s" % (Path(path).name, selector.strip()[:30]))
    return count, where


def check_eyebrow_density(gate, sources, run, ctx):
    sections = _section_count(sources)
    count, where = _eyebrow_count(sources)
    if not sections:
        return UNKNOWN, "no section structure found to count eyebrows against"
    cap = -(-sections // 3)
    if count > cap:
        return FAIL, ("%d eyebrow(s) across %d section(s); the cap is %d: %s"
                      % (count, sections, cap, ", ".join(where[:8])))
    return PASS, "%d eyebrow(s) across %d section(s), within a cap of %d" % (count, sections, cap)


# Each family is a shape a section can take. Two sections sharing one are two
# sections that look alike however different their copy is, which is what the
# repetition and run caps are counting.
# Utility classes name the shape inline; a project with its own stylesheet names it
# in a class instead, so both spellings have to be read or every section on a
# hand-written site classifies the same way and the variety it does have goes unseen.
LAYOUT_FAMILIES = (
    ("marquee", r"marquee|animate-scroll|infinite-scroll|ticker"),
    ("carousel", r"carousel|swiper|embla|slider|scroll-snap|snap-x"),
    ("bento", r"col-span-\d|row-span-\d|grid-areas|grid-template-areas|\bbento\b"),
    ("split", r'(grid-cols-2|md:grid-cols-2|lg:grid-cols-2|w-1/2|basis-1/2'
               r'|grid-cols-\[1fr_1fr\]|class="[^"]*\bsplit\b)'),
    ("columns-3", r"(grid-cols-3|md:grid-cols-3|lg:grid-cols-3)"),
    ("columns-4", r"(grid-cols-4|md:grid-cols-4|lg:grid-cols-4)"),
    ("table", r"<table\b|<Table\b|divide-y"),
    ("accordion", r"accordion|<details\b|disclosure"),
    ("stack", r'flex-col|space-y-|<article\b|class="[^"]*\b(steps|prose|stack)\b'),
)


def _section_blocks(sources):
    """Each section's own source, in page order, so its shape can be classified."""
    blocks = []
    for path, text in sorted(sources.items()):
        lines = text.splitlines()
        opens = [i for i, line in enumerate(lines) if SECTION_TAG.search(line)]
        for n, start in enumerate(opens):
            end = opens[n + 1] if n + 1 < len(opens) else len(lines)
            blocks.append((path, start + 1, "\n".join(lines[start:end])))
    return blocks


def _family_of(block):
    has_media = bool(re.search(r"<img\b|<Image\b|<picture\b|background-image|<video\b", block, re.I))
    for name, pattern in LAYOUT_FAMILIES:
        if re.search(pattern, block, re.I):
            if name == "split":
                return "split-media" if has_media else "split-text"
            return name
    return "full-width"


def check_layout_family_run(gate, sources, run, ctx):
    limit = gate["check"].get("max_run", 2)
    blocks = _section_blocks(sources)
    if len(blocks) < limit + 1:
        return PASS, "%d section(s), too few to repeat a family that many times" % len(blocks)
    runs, current, streak, in_file = [], None, 0, None
    for path, line, block in blocks:
        if path != in_file:
            in_file, current, streak = path, None, 0
        family = _family_of(block)
        if family == current:
            streak += 1
        else:
            current, streak = family, 1
        if family == "split-media" and streak > limit:
            runs.append("%s:%d is the %d%s consecutive image-and-text split"
                        % (Path(path).name, line, streak, "th" if streak > 3 else "rd"))
    if runs:
        return FAIL, "; ".join(runs[:4])
    return PASS, "no image-and-text split runs past %d in a row" % limit


def check_layout_family_diversity(gate, sources, run, ctx):
    spec = gate["check"]
    per, floor = spec.get("per_sections", 8), spec.get("min_families", 4)
    blocks = _section_blocks(sources)
    if not blocks:
        return UNKNOWN, "no sections found to compare"
    pages, thin = {}, []
    for path, _, block in blocks:
        pages.setdefault(path, []).append(_family_of(block))
    for path, families in sorted(pages.items()):
        if len(families) < 4:
            continue
        distinct = sorted(set(families))
        # Capped at what the classifier can tell apart, so a long page is not asked
        # for more families than exist.
        needed = min(len(LAYOUT_FAMILIES), max(2, round(len(families) * floor / per)))
        if len(distinct) < needed:
            counts = {f: families.count(f) for f in distinct}
            thin.append("%s runs %d section(s) on %d famil(y/ies) - %s - where %d are needed"
                        % (Path(path).name, len(families), len(distinct),
                           ", ".join("%s x%d" % (k, v) for k, v in sorted(counts.items())), needed))
    if thin:
        return FAIL, "; ".join(thin[:4])
    return PASS, ("%d page(s) of four or more sections, each across enough layout families"
                  % sum(1 for f in pages.values() if len(f) >= 4))


MARQUEE_USE = re.compile(r"<[A-Z][A-Za-z]*Marquee\b|<Marquee\b|"
                         r"(?:class|className)\s*=\s*[\"'{][^\"'}]*\b(?:animate-marquee|marquee|ticker-track)\b")


def check_marquee_count(gate, sources, run, ctx):
    limit = gate["check"].get("max", 1)
    used = set()
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("import") or "from \"" in line or "from '" in line:
                continue
            if MARQUEE_USE.search(line):
                used.add("%s:%d" % (Path(path).name, i))
    if len(used) > limit:
        return FAIL, "%d marquee(s) rendered: %s" % (len(used), ", ".join(sorted(used)[:6]))
    return PASS, "%d marquee(s) rendered" % len(used)


# ---------------------------------------------------------------- hero


def check_hero_top_padding(gate, sources, run, ctx):
    cap = gate["check"].get("max_rem", 6)
    offenders = []
    for path, line, block in _hero_regions(sources):
        for step in re.findall(r"\bp[ty]-(\d{1,3})\b", block):
            if int(step) / 4 > cap:
                offenders.append("%s:%d pt-%s is %.0frem" % (Path(path).name, line, step, int(step) / 4))
        for value, unit in re.findall(r"padding(?:-top|-block-start)?\s*:\s*(\d+(?:\.\d+)?)(rem|px)", block):
            rem = float(value) if unit == "rem" else float(value) / 16
            if rem > cap:
                offenders.append("%s:%d padding-top %s%s" % (Path(path).name, line, value, unit))
    if offenders:
        return FAIL, "hero top padding past %drem: %s" % (cap, "; ".join(sorted(set(offenders))[:5]))
    return PASS, "hero top padding is within %drem" % cap


HERO_TEXT_TAG = re.compile(r"<(h[1-6]|p)\b", re.I)
HERO_CTA_TAG = re.compile(r"<(button|a|Button|Link|NavLink)\b", re.I)
HERO_SMALL_TEXT = re.compile(r"<(span|div|small|em|strong)\b[^>]*>\s*[A-Za-z][^<>{}]{2,120}<", re.I)


def check_hero_stack_count(gate, sources, run, ctx):
    cap = gate["check"].get("max", 4)
    regions = _hero_regions(sources)
    if not regions:
        return UNKNOWN, "no hero found in source to count"
    worst = []
    for path, line, block in regions:
        headings = len(HERO_TEXT_TAG.findall(block))
        smalls = len(HERO_SMALL_TEXT.findall(block))
        ctas = 1 if HERO_CTA_TAG.search(block) else 0
        total = headings + smalls + ctas
        if total > cap:
            worst.append("%s:%d carries %d text element(s) (%d heading/paragraph, %d small label, %d CTA group)"
                         % (Path(path).name, line, total, headings, smalls, ctas))
    if worst:
        return FAIL, "; ".join(worst[:3])
    return PASS, "%d hero(es), each within %d text elements" % (len(regions), cap)


LOGO_WALL_MARK = re.compile(
    r"trusted[ _-]?by|used[ _-]?by|backed[ _-]?by|customers[ _-]?include|logo-?wall|logo[-_]?cloud", re.I)


def check_trust_strip_placement(gate, sources, run, ctx):
    inside = []
    for path, line, block in _hero_regions(sources):
        match = LOGO_WALL_MARK.search(block)
        if match:
            inside.append("%s:%d carries '%s' inside the hero" % (Path(path).name, line, match.group(0)))
    if inside:
        return FAIL, "; ".join(inside[:4])
    return PASS, "no logo wall inside a hero"


def check_hero_forbid_regex(gate, sources, run, ctx):
    spec = gate["check"]
    rx = re.compile(spec["pattern"], re.I)
    offenders = []
    for path, line, block in _hero_regions(sources):
        for n, text in enumerate(block.splitlines(), line):
            found = rx.search(text)
            if found:
                offenders.append("%s:%d %s" % (Path(path).name, n, found.group(0).strip()[:48]))
    if offenders:
        return FAIL, "%s - %s" % (spec.get("message", "forbidden in the hero"), "; ".join(offenders[:5]))
    return PASS, "the hero is clean"



# ---------------------------------------------------------------- grids and lists


EMPTY_TILE = re.compile(r"<(div|li|article|section)\b(?![^>]*\b(?:aria-hidden|role=)\b)[^>]*>\s*</\1>|"
                        r"<(div|li)\b[^>]*/>", re.I)
GRID_OPEN = re.compile(r"(grid-cols-\d|grid-template-columns)", re.I)


def check_grid_cell_fill(gate, sources, run, ctx):
    """Blank tiles pasted into a grid to make the arithmetic come out."""
    offenders = set()
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not GRID_OPEN.search(line):
                continue
            window = _bounded_window(lines, i, 25)
            for match in EMPTY_TILE.finditer(window):
                tile = match.group(0)
                if re.search(r"style\s*=|\bw-\d|\bh-\d|\bsize-\d|rounded-full|aria-|role=", tile):
                    continue
                at = i + window[:match.start()].count("\n") + 1
                offenders.add("%s:%d %s" % (Path(path).name, at, tile.strip()[:44]))
    if offenders:
        return FAIL, ("%d empty cell(s) inside a grid: %s"
                      % (len(offenders), "; ".join(sorted(offenders)[:5])))
    return PASS, "no blank tiles inside a grid"


def check_long_list_component(gate, sources, run, ctx):
    """A plain list carrying more items than a plain list should."""
    cap = gate["check"].get("max_plain_items", 5)
    offenders = []
    for path, text in sources.items():
        for match in re.finditer(r"<(ul|ol)\b[^>]*>(.*?)</\1>", text, re.S | re.I):
            items = len(re.findall(r"<li\b", match.group(2), re.I))
            if items > cap:
                line = text[:match.start()].count("\n") + 1
                offenders.append("%s:%d a plain list of %d items" % (Path(path).name, line, items))
    if offenders:
        return FAIL, "; ".join(offenders[:5])
    if re.search(r"<(ul|ol)\b", "\n".join(sources.values()), re.I):
        return PASS, "no plain list past %d items" % cap
    return PASS, "no plain lists"


def check_spec_list_hairlines(gate, sources, run, ctx):
    """A separator under every row of a long list, or two on one edge."""
    offenders = []
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if re.search(r"\bborder-t\b", line) and re.search(r"\bborder-b\b", line):
                offenders.append("%s:%d draws a border on both edges of one row" % (Path(path).name, i))
            if re.search(r"border-top[^;]*;\s*border-bottom", line):
                offenders.append("%s:%d draws a border on both edges of one row" % (Path(path).name, i))
        for match in re.finditer(r"divide-y[^\"'`]*[\"'`]", text):
            start = text[:match.start()].count("\n")
            window = "\n".join(lines[start:min(len(lines), start + 40)])
            rows = len(re.findall(r"<(li|div|tr)\b", window, re.I))
            if rows > 8:
                offenders.append("%s:%d divides %d rows with a hairline each" % (Path(path).name, start + 1, rows))
    if offenders:
        return FAIL, "; ".join(sorted(set(offenders))[:5])
    return PASS, "separators are used on one edge and sparsely"


def check_comparison_track_bar(gate, sources, run, ctx):
    """A filled track behind a partial bar, which is dashboard chrome."""
    offenders = []
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not re.search(r"rounded-full|border-radius:\s*9999|border-radius:\s*999", line):
                continue
            if not re.search(r"bg-(gray|grey|zinc|slate|stone|neutral)-(1|2|3)00\b|background:\s*#e[0-9a-f]{5}", line, re.I):
                continue
            window = "\n".join(lines[i:min(len(lines), i + 6)])
            if re.search(r"width:\s*[`\"'{]?\s*\$?\{?\w|w-\[\d+%\]|style=\{\{\s*width", window):
                offenders.append("%s:%d" % (Path(path).name, i + 1))
    if offenders:
        return FAIL, ("%d filled-track bar(s) used as a comparison visual: %s"
                      % (len(offenders), ", ".join(offenders[:5])))
    return PASS, "no filled comparison tracks"


# ---------------------------------------------------------------- decoration


def check_separator_density(gate, sources, run, ctx):
    cap = gate["check"].get("max_per_line", 1)
    offenders = []
    for path, line, body in _visible_strings(sources):
        dots = body.count("·")
        if dots > cap:
            offenders.append("%s:%d %d dots in '%s'" % (Path(path).name, line, dots, body[:44]))
    if offenders:
        return FAIL, "; ".join(offenders[:5])
    return PASS, "no line carries more than %d middle dot(s)" % cap


DOT_SHAPE = re.compile(
    r"rounded-full[^\"'`]*\b(?:w-(?:1|1\.5|2|2\.5)|size-(?:1|1\.5|2|2\.5)|h-(?:1|1\.5|2|2\.5))\b|"
    r"\b(?:w-(?:1|1\.5|2|2\.5)|size-(?:1|1\.5|2|2\.5))\b[^\"'`]*rounded-full"
)
DOT_MEANING = re.compile(r"status|online|offline|live|available|health|state|indicator|pulse|active", re.I)


def check_decorative_dots(gate, sources, run, ctx):
    offenders = []
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not DOT_SHAPE.search(line):
                continue
            window = "\n".join(lines[max(0, i - 3):min(len(lines), i + 4)])
            if DOT_MEANING.search(window):
                continue
            offenders.append("%s:%d" % (Path(path).name, i + 1))
    if offenders:
        return FAIL, ("%d small round dot(s) with no state behind them: %s"
                      % (len(offenders), ", ".join(offenders[:6])))
    return PASS, "no decorative dots"


def check_image_overlay_label(gate, sources, run, ctx):
    """Text positioned over a photograph rather than captioned below it."""
    offenders = []
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not re.search(r"<img\b|<Image\b|<picture\b", line, re.I):
                continue
            window = lines[max(0, i - 4):min(len(lines), i + 10)]
            for n, near in enumerate(window):
                if not re.search(r"\babsolute\b|position:\s*absolute", near):
                    continue
                if re.search(r">\s*[A-Za-z0-9][^<>{}]{1,60}<", near) or re.search(
                        r">\s*\{?[\"'][^\"']{2,60}", near):
                    offenders.append("%s:%d" % (Path(path).name, max(0, i - 4) + n + 1))
    if offenders:
        found = sorted(set(offenders))
        return FAIL, ("%d label(s) positioned over an image: %s" % (len(found), ", ".join(found[:5])))
    return PASS, "no labels overlaid on images"


# ---------------------------------------------------------------- colour and theme


def _hue_family(value):
    """Which hue a colour belongs to, as a coarse bucket, or None for a neutral."""
    value = value.strip().lower()
    named = re.match(r"(?:bg|text|border|ring|from|via|to|fill|stroke|shadow|accent|decoration)-([a-z]+)-\d{2,3}", value)
    if named:
        name = named.group(1)
        return None if name in NEUTRALS else name
    hexmatch = re.fullmatch(r"#([0-9a-f]{6}|[0-9a-f]{3})", value)
    if not hexmatch:
        return None
    raw = hexmatch.group(1)
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
    high, low = max(r, g, b), min(r, g, b)
    span = high - low
    if span < 0.12:
        return None
    if high == r:
        deg = (60 * ((g - b) / span)) % 360
    elif high == g:
        deg = 60 * ((b - r) / span) + 120
    else:
        deg = 60 * ((r - g) / span) + 240
    # Wide bands on purpose. This separates a warm-grey page with a blue CTA from
    # one accent expressed in two shades; splitting green from teal at a degree
    # boundary only reports that emerald sits near the line.
    buckets = [(15, "red"), (45, "orange"), (70, "yellow"), (200, "green"),
               (260, "blue"), (310, "violet"), (340, "pink"), (360, "red")]
    for edge, name in buckets:
        if deg < edge:
            return name
    return "red"


ACCENT_USE = re.compile(
    r"\b(?:bg|text|border|ring|from|via|to|fill|accent)-(" + "|".join(HUE_NAMES) + r")-\d{2,3}\b")

# Utility names and hex values have to land in the same buckets or one accent
# expressed both ways reads as two: emerald-600 and #047857 are the same green.
HUE_BUCKET = {
    "red": "red", "rose": "red", "orange": "orange", "amber": "orange",
    "yellow": "yellow", "lime": "yellow", "green": "green", "emerald": "green",
    "teal": "green", "cyan": "green", "sky": "blue", "blue": "blue",
    "indigo": "violet", "violet": "violet", "purple": "violet", "fuchsia": "violet",
    "pink": "pink",
}


def _bucket(hue):
    return HUE_BUCKET.get(hue, hue)


def check_accent_consistency(gate, sources, run, ctx):
    """More than one non-neutral hue doing accent work across the page."""
    families = {}
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            if SEMANTIC.intersection(re.findall(r"[a-z]+", line.lower())):
                continue
            for hue in ACCENT_USE.findall(line):
                families.setdefault(_bucket(hue), []).append("%s:%d" % (Path(path).name, i))
            for literal in re.findall(r"#[0-9a-fA-F]{6}\b", line):
                hue = _hue_family(literal)
                if hue:
                    families.setdefault(_bucket(hue), []).append("%s:%d" % (Path(path).name, i))
    if len(families) > 1:
        listed = sorted(families, key=lambda k: -len(families[k]))
        return FAIL, ("%d accent hues on one page: %s" % (len(families), "; ".join(
            "%s at %s" % (h, families[h][0]) for h in listed[:5])))
    if families:
        return PASS, "one accent hue: %s" % next(iter(families))
    return PASS, "no non-neutral accent in use"


SECTION_SURFACE = re.compile(
    r"<(?:section|main|header|footer)\b[^>]*(?:class|className)\s*=\s*[\"'{][^\"'}]*"
    r"\bbg-(white|black|(?:[a-z]+)-(\d{2,3}))\b", re.I)


def check_theme_lock(gate, sources, run, ctx):
    """A page alternating between light and dark surfaces down its own scroll."""
    flips, seen = [], 0
    for path, text in sorted(sources.items()):
        family, where = None, []
        for i, line in enumerate(text.splitlines(), 1):
            for match in SECTION_SURFACE.finditer(line):
                token, step = match.group(1), match.group(2)
                if token == "white" or (step and int(step) <= 200):
                    now_family = "light"
                elif token == "black" or (step and int(step) >= 700):
                    now_family = "dark"
                else:
                    continue
                seen += 1
                if family and now_family != family:
                    where.append("%s:%d goes %s" % (Path(path).name, i, now_family))
                family = now_family
        # One switch is a composed device; two is the page alternating.
        if len(where) > 1:
            flips.extend(where)
    if flips:
        return FAIL, ("the page changes lightness family %d time(s) down its scroll: %s"
                      % (len(flips), "; ".join(flips[:5])))
    if seen:
        return PASS, "%d section surface(s), at most one composed switch" % seen
    return UNKNOWN, "no section-level surface found to compare - confirm the theme holds across the page"


GLOW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)")


def check_neon_glow(gate, sources, run, ctx):
    """A saturated colour thrown wide behind an element."""
    offenders = []
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            for value in re.findall(r"(?:box-shadow|drop-shadow|filter)\s*:\s*([^;{}]+)", line, re.I):
                if "inset" in value:
                    continue
                blurs = [float(b) for b in re.findall(r"(\d+(?:\.\d+)?)px", value)]
                if not blurs or max(blurs) < 20:
                    continue
                for colour in GLOW_COLOUR.findall(value):
                    if _hue_family(colour):
                        offenders.append("%s:%d %s" % (Path(path).name, i, value.strip()[:52]))
                        break
            for utility in re.findall(r"shadow-(" + "|".join(HUE_NAMES) + r")-\d{2,3}", line):
                offenders.append("%s:%d shadow-%s" % (Path(path).name, i, utility))
    if offenders:
        return FAIL, "%d coloured glow(s): %s" % (len(offenders), "; ".join(sorted(set(offenders))[:5]))
    return PASS, "shadows are neutral or tinted, none thrown wide in colour"


# ---------------------------------------------------------------- copy


def check_visible_string_scan(gate, sources, run, ctx):
    """A pattern held against the strings a reader sees, and nothing else."""
    spec = gate["check"]
    rx = re.compile(spec["pattern"], re.I)
    offenders = []
    for path, line, body in _visible_strings(sources):
        found = rx.search(body)
        if found:
            offenders.append("%s:%d '%s'" % (Path(path).name, line, body[:56]))
    if offenders:
        return FAIL, "%s - %d: %s" % (spec.get("message", "forbidden in visible copy"),
                                      len(offenders), "; ".join(offenders[:6]))
    return PASS, "no occurrences in any visible string"


def check_straight_quotes(gate, sources, run, ctx):
    offenders = []
    for path, line, body in _visible_strings(sources):
        if re.search(r"(^|\s)\"[A-Za-z]|[A-Za-z]\"($|\s|[.,])", body):
            offenders.append("%s:%d '%s'" % (Path(path).name, line, body[:48]))
    if offenders:
        return FAIL, "ASCII quote marks in set copy: %s" % "; ".join(offenders[:5])
    return PASS, "quoted copy uses typographic marks or none"


CTA_INTENTS = {
    "contact": ("get in touch", "contact us", "contact", "let's talk", "lets talk", "start a project",
                "start something", "reach out", "talk to us", "say hello", "work with us"),
    "signup": ("try free", "get started", "sign up free", "sign up", "start free", "create account",
               "join free", "start building"),
    "portfolio": ("view work", "see selected work", "selected work", "browse projects", "view projects",
                  "see our work", "view case studies"),
    "demo": ("book a demo", "request a demo", "get a demo", "schedule a demo", "see it in action"),
    "pricing": ("see pricing", "view pricing", "compare plans", "view plans"),
}

CTA_TAG = re.compile(r"<(?:button|a|Button|Link|NavLink|CTA)\b[^>]*>\s*([^<>{}]{2,48}?)\s*<", re.I)


def check_cta_intent_dedupe(gate, sources, run, ctx):
    found = {}
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            for label in CTA_TAG.findall(line):
                flat = re.sub(r"\s+", " ", label).strip().lower().rstrip(".!→>")
                for intent, phrases in CTA_INTENTS.items():
                    if flat in phrases:
                        found.setdefault(intent, {}).setdefault(flat, "%s:%d" % (Path(path).name, i))
    clashes = {k: v for k, v in found.items() if len(v) > 1}
    if clashes:
        return FAIL, "; ".join(
            "%s intent carries %d labels: %s" % (
                intent, len(labels), ", ".join("'%s' at %s" % (l, w) for l, w in list(labels.items())[:4]))
            for intent, labels in clashes.items())
    if found:
        return PASS, "%d CTA intent(s), one label each" % len(found)
    return PASS, "no recognised CTA intents"


def _words(text):
    return [w for w in re.split(r"\s+", re.sub(r"<[^>]+>|\{[^{}]*\}", " ", text)) if w.strip()]


def check_copy_density(gate, sources, run, ctx):
    spec = gate["check"]
    head_cap = spec.get("max_headline_words", 8)
    body_cap = spec.get("max_body_words", 25)
    offenders = []
    for path, text in sources.items():
        for match in re.finditer(r"<(h[123])\b[^>]*>(.*?)</\1>", text, re.S | re.I):
            words = _words(match.group(2))
            if len(words) > head_cap:
                line = text[:match.start()].count("\n") + 1
                offenders.append("%s:%d headline of %d words" % (Path(path).name, line, len(words)))
        for match in re.finditer(r"<p\b[^>]*>(.*?)</p>", text, re.S | re.I):
            words = _words(match.group(1))
            if len(words) > body_cap:
                line = text[:match.start()].count("\n") + 1
                offenders.append("%s:%d paragraph of %d words" % (Path(path).name, line, len(words)))
    if offenders:
        return FAIL, ("%d block(s) past the density: %s"
                      % (len(offenders), "; ".join(offenders[:6])))
    return PASS, "headlines within %d words, paragraphs within %d" % (head_cap, body_cap)


QUOTE_BLOCK = re.compile(r"<blockquote\b[^>]*>(.*?)</blockquote>", re.S | re.I)
ATTRIBUTION = re.compile(
    r"<(?:cite|footer|figcaption|span|p|div)\b[^>]*>\s*([^<>{}]*[A-Za-z][^<>{}]{2,90}?)\s*<", re.I)


def check_quote_shape(gate, sources, run, ctx):
    """Quote length and whether the attribution names a role as well as a person."""
    cap = gate["check"].get("max_lines", 3)
    probe = (run.get("layout") or {}).get("quotes")
    offenders = []
    if probe:
        for item in probe:
            if item.get("lines", 0) > cap:
                offenders.append("a quote runs %d lines" % item["lines"])
            who = (item.get("attribution") or "").strip()
            if who and not item.get("attributionHasRole"):
                offenders.append("a quote's attribution is '%s' with no role" % who[:40])
    for path, text in sources.items():
        for match in QUOTE_BLOCK.finditer(text):
            line = text[:match.start()].count("\n") + 1
            body = " ".join(_words(match.group(1)))
            if len(body) > cap * 78:
                offenders.append("%s:%d quote of %d characters, past %d lines' worth"
                                 % (Path(path).name, line, len(body), cap))
            tail = text[match.end():match.end() + 400]
            names = ATTRIBUTION.findall(tail)
            if names and not any("," in n or re.search(r"\bat\b|\bof\b", n) for n in names[:3]):
                offenders.append("%s:%d attribution '%s' carries no role"
                                 % (Path(path).name, line, names[0][:40]))
    if offenders:
        return FAIL, "; ".join(sorted(set(offenders))[:5])
    return PASS, "quotes are short and their attributions name a role"


# ---------------------------------------------------------------- imagery


def check_image_presence(gate, sources, run, ctx):
    floor = gate["check"].get("min", 3)
    blob = "\n".join(sources.values())
    count = len(re.findall(r"<img\b|<Image\b|<picture\b|<video\b|background-image\s*:|backgroundImage", blob, re.I))
    assets = [f for f in image_files(run["targets"]) if f.suffix.lower() != ".svg"]
    total = count + len(assets)
    if total < floor:
        return FAIL, ("%d real image(s) on the page against a floor of %d - a page of type and gradients is "
                      "incomplete, not minimal" % (total, floor))
    return PASS, "%d image reference(s) and %d raster asset(s)" % (count, len(assets))


def _bounded_window(lines, start, limit, closers=r"</section>|</header>|</footer>"):
    """Lines from a marker to the end of the block it sits in, or a hard limit."""
    end = min(len(lines), start + limit)
    for j in range(start + 1, end):
        if re.search(closers, lines[j], re.I):
            end = j + 1
            break
    return "\n".join(lines[start:end])


def _logo_wall_windows(sources):
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if LOGO_WALL_MARK.search(line):
                yield path, i + 1, _bounded_window(lines, i, 25)


def check_logo_wall_marks(gate, sources, run, ctx):
    bare = []
    for path, line, window in _logo_wall_windows(sources):
        marks = len(re.findall(r"<img\b|<svg\b|<Image\b|simpleicons|devicon", window, re.I))
        words = len(re.findall(r"<(?:span|p|div|li)\b[^>]*>\s*[A-Z][A-Za-z0-9&.\- ]{1,24}\s*<", window))
        if words >= 3 and marks == 0:
            bare.append("%s:%d shows %d text wordmark(s) and no marks" % (Path(path).name, line, words))
    if bare:
        return FAIL, "; ".join(bare[:4])
    return PASS, "logo walls carry real marks"


def check_logo_wall_labels(gate, sources, run, ctx):
    labelled = []
    for path, line, window in _logo_wall_windows(sources):
        entries = re.findall(r"<(?:img|svg|Image|span|p|div|li)\b[^>]*>\s*([^<>{}]{0,30}?)\s*<", window)
        labels = [e.strip() for e in entries if e.strip() and e.strip() == e.strip().lower()
                  and 2 < len(e.strip()) < 24 and e.strip() not in {"and", "or"}]
        if labels and len(labels) >= 2:
            labelled.append("%s:%d %s under the marks"
                            % (Path(path).name, line, ", ".join("'%s'" % l for l in labels[:4])))
    if labelled:
        return FAIL, "%d category label(s) under logos: %s" % (len(labelled), "; ".join(labelled[:5]))
    return PASS, "logo walls carry logos and nothing else"


ICON_LIB = re.compile(r"lucide|phosphor|heroicons|tabler|radix-ui/react-icons|hugeicons|react-icons|feather",
                      re.I)


def check_handrolled_svg(gate, sources, run, ctx):
    """Inline path data complex enough to be a drawing rather than a shape."""
    cap = gate["check"].get("max_path_commands", 40)
    offenders = []
    for path, text in sources.items():
        if ICON_LIB.search(text) and "<svg" not in text:
            continue
        for match in re.finditer(r"<svg\b.*?</svg>", text, re.S | re.I):
            block = match.group(0)
            commands = sum(len(re.findall(r"[MmLlHhVvCcSsQqTtAaZz]", d))
                           for d in re.findall(r"\bd\s*=\s*[\"']([^\"']+)[\"']", block))
            if commands > cap:
                line = text[:match.start()].count("\n") + 1
                offenders.append("%s:%d an inline svg of %d path commands"
                                 % (Path(path).name, line, commands))
    # A plate is the exception the rule already names: a drawing asked for on
    # purpose, kept as a file, on the set's shared grid. AST-11 requires those
    # and AST-12/13 govern how they are used and whether they hold together, so
    # flagging them here would leave a project failing one gate for satisfying
    # another. What stays guarded is inline path data in a component, and a
    # drawing that belongs to no set.
    drawn = {path for path, _, _ in _plates(run)}
    for asset in image_files(run["targets"]):
        if asset.suffix.lower() != ".svg" or asset in drawn:
            continue
        try:
            body = asset.read_text(errors="replace")
        except OSError:
            continue
        commands = sum(len(re.findall(r"[MmLlHhVvCcSsQqTtAaZz]", d))
                       for d in re.findall(r"\bd\s*=\s*[\"']([^\"']+)[\"']", body))
        if commands > cap * 3:
            offenders.append("%s is a drawing of %d path commands" % (asset.name, commands))
    if offenders:
        return FAIL, "; ".join(offenders[:5])
    return PASS, "no hand-drawn decorative svg"



# ---------------------------------------------------------------- frontend mechanics


CONTINUOUS_NAME = re.compile(
    r"\b(mouse|cursor|pointer|scroll|progress|offset|velocity|tilt|parallax|magnet)[A-Za-z]*\b", re.I)
CONTINUOUS_SOURCE = re.compile(r"clientX|clientY|scrollY|scrollTop|deltaY|pageX|pageY|movementX|movementY")


def check_react_continuous_state(gate, sources, run, ctx):
    """A value that changes every frame held in state that re-renders the tree."""
    offenders = []
    for path, text in sources.items():
        if Path(path).suffix.lower() not in {".jsx", ".tsx", ".js", ".ts"}:
            continue
        setters = {}
        for match in re.finditer(r"const\s*\[\s*(\w+)\s*,\s*(set\w+)\s*\]\s*=\s*useState", text):
            setters[match.group(2)] = (match.group(1), text[:match.start()].count("\n") + 1)
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            call = re.search(r"\b(set[A-Z]\w*)\s*\(", line)
            if not call or call.group(1) not in setters:
                continue
            window = "\n".join(lines[max(0, i - 6):min(len(lines), i + 2)])
            name, _ = setters[call.group(1)]
            if CONTINUOUS_SOURCE.search(window) or (CONTINUOUS_NAME.search(name) and
                                                    re.search(r"mousemove|pointermove|scroll|wheel|drag", window, re.I)):
                offenders.append("%s:%d %s holds a continuous value" % (Path(path).name, i, name))
    if offenders:
        return FAIL, "; ".join(sorted(set(offenders))[:5])
    return PASS, "no continuous input value held in React state"


def _balanced_block(text, start):
    """The source from an opening brace to the one that closes it."""
    depth, i, opened = 0, start, False
    while i < len(text):
        if text[i] == "{":
            depth += 1
            opened = True
        elif text[i] == "}":
            depth -= 1
            if opened and depth == 0:
                return text[start:i + 1]
        i += 1
    return text[start:]


LEAKY_START = re.compile(
    r"addEventListener\(|setInterval\(|setTimeout\(|requestAnimationFrame\(|"
    r"new\s+(?:IntersectionObserver|ResizeObserver|MutationObserver)\(|\.observe\(|"
    r"ScrollTrigger\.create\(|gsap\.to\(")


def check_effect_cleanup(gate, sources, run, ctx):
    offenders = []
    for path, text in sources.items():
        if Path(path).suffix.lower() not in {".jsx", ".tsx", ".js", ".ts"}:
            continue
        for match in re.finditer(r"useEffect\s*\(\s*\(\s*\)\s*=>\s*\{", text):
            block = _balanced_block(text, match.end() - 1)
            if not LEAKY_START.search(block):
                continue
            if re.search(r"return\s*(\(\s*\)\s*=>|function)", block):
                continue
            line = text[:match.start()].count("\n") + 1
            started = LEAKY_START.search(block).group(0).strip("(")
            offenders.append("%s:%d starts %s and returns no cleanup" % (Path(path).name, line, started))
    if offenders:
        return FAIL, "; ".join(offenders[:5])
    return PASS, "every effect that starts something stops it"


def check_raf_state(gate, sources, run, ctx):
    offenders = []
    for path, text in sources.items():
        for match in re.finditer(r"requestAnimationFrame\s*\(", text):
            window = text[match.start():match.start() + 600]
            setter = re.search(r"\bset[A-Z]\w*\s*\(", window)
            if setter:
                line = text[:match.start()].count("\n") + 1
                offenders.append("%s:%d a frame loop calls %s" % (Path(path).name, line, setter.group(0).strip("(")))
    if offenders:
        return FAIL, "; ".join(sorted(set(offenders))[:5])
    return PASS, "no frame loop writes to state"


CLIENT_DIRECTIVE = re.compile(r"^\s*[\"']use client[\"']", re.M)
MOTION_USE = re.compile(r"framer-motion|motion/react|\bmotion\.[a-z]|useScroll\(|useMotionValue\(|useSpring\(")


def check_client_leaf_motion(gate, sources, run, ctx):
    """Animated components that would drag a server tree onto the client."""
    app_router = any(Path(t).expanduser().joinpath("app").is_dir() or "/app/" in str(t)
                     for t in run["targets"])
    if not app_router and not any("/app/" in p or p.endswith("/page.tsx") for p in sources):
        return UNKNOWN, "no app-router tree found - confirm the animated components are client leaves"
    offenders = []
    for path, text in sources.items():
        if Path(path).suffix.lower() not in {".jsx", ".tsx"}:
            continue
        if not MOTION_USE.search(text):
            continue
        head = "\n".join(text.splitlines()[:6])
        if not CLIENT_DIRECTIVE.search(head):
            offenders.append(Path(path).name)
    if offenders:
        return FAIL, ("%d component(s) use motion with no client directive at the top: %s"
                      % (len(offenders), ", ".join(sorted(offenders)[:6])))
    return PASS, "motion is confined to components that declare themselves client leaves"


GRAIN_MARK = re.compile(r"grain|noise|feTurbulence|film-grain|grain-overlay", re.I)


def check_grain_overlay(gate, sources, run, ctx):
    faults = []
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not GRAIN_MARK.search(line):
                continue
            window = "\n".join(lines[max(0, i - 4):min(len(lines), i + 12)])
            missing = []
            if not re.search(r"position\s*:\s*fixed|\bfixed\b", window):
                missing.append("is not on a fixed layer")
            if not re.search(r"pointer-events\s*:\s*none|pointer-events-none", window):
                missing.append("does not stand off the pointer")
            if missing:
                faults.append("%s:%d %s" % (Path(path).name, i + 1, " and ".join(missing)))
    if faults:
        return FAIL, "grain overlay %s" % "; ".join(sorted(set(faults))[:4])
    return PASS, "grain sits on a fixed inert layer, or there is none"


BUILTIN_MODULES = {
    "fs", "path", "os", "url", "util", "crypto", "http", "https", "stream", "events", "buffer",
    "child_process", "zlib", "assert", "net", "tls", "dns", "querystring", "readline", "worker_threads",
    "react", "react-dom", "next",
}

IMPORT_LINE = re.compile(r"""(?:^|\n)\s*import\s+(?:[^'"]*?\sfrom\s+)?['"]([^'"]+)['"]|"""
                         r"""require\(\s*['"]([^'"]+)['"]\s*\)""")


def _package_manifest(run):
    for target in run["targets"]:
        base = Path(target).expanduser()
        base = base if base.is_dir() else base.parent
        for candidate in [base] + list(base.parents)[:4]:
            manifest = candidate / "package.json"
            if manifest.is_file():
                try:
                    return manifest, json.loads(manifest.read_text())
                except (OSError, ValueError):
                    return manifest, None
    return None, None


def _bare_specifiers(sources):
    seen = {}
    for path, text in sources.items():
        for a, b in IMPORT_LINE.findall(text):
            spec = a or b
            if not spec or spec[0] in "./" or spec.startswith(("@/", "~/", "~", "#")):
                continue
            parts = spec.split("/")
            name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            seen.setdefault(name, Path(path).name)
    return seen


def check_dependency_verified(gate, sources, run, ctx):
    manifest, data = _package_manifest(run)
    if not manifest:
        return UNKNOWN, "no package.json under the targets to check imports against"
    if data is None:
        return FAIL, "%s does not parse as JSON" % manifest
    declared = set()
    for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        declared.update((data.get(field) or {}).keys())
    missing = {name: where for name, where in _bare_specifiers(sources).items()
               if name not in declared and name not in BUILTIN_MODULES}
    if missing:
        return FAIL, ("%d package(s) imported but not in %s: %s"
                      % (len(missing), manifest.name,
                         ", ".join("%s (%s)" % (k, v) for k, v in sorted(missing.items())[:8])))
    return PASS, "every bare import resolves to a declared dependency"


DESIGN_SYSTEMS = {
    "Material": r"@material/web|@mui/material|material-components",
    "Fluent": r"@fluentui/",
    "Carbon": r"@carbon/",
    "Polaris": r"@shopify/polaris",
    "Atlaskit": r"@atlaskit/",
    "Primer": r"@primer/",
    "GOV.UK": r"govuk-frontend",
    "USWDS": r"@uswds/|uswds",
    "Bootstrap": r"\bbootstrap\b",
    "Radix Themes": r"@radix-ui/themes",
    "Chakra": r"@chakra-ui/",
    "Ant Design": r"\bantd\b|@ant-design/",
    "Mantine": r"@mantine/",
}


def check_design_system_mix(gate, sources, run, ctx):
    manifest, data = _package_manifest(run)
    haystack = "\n".join(sources.values())
    if data:
        declared = set()
        for field in ("dependencies", "devDependencies"):
            declared.update((data.get(field) or {}).keys())
        haystack += "\n" + "\n".join(declared)
    present = sorted(name for name, pattern in DESIGN_SYSTEMS.items()
                     if re.search(pattern, haystack, re.I))
    if len(present) > 1:
        return FAIL, "%d design systems in one project: %s" % (len(present), ", ".join(present))
    if present:
        return PASS, "one design system: %s" % present[0]
    return PASS, "no packaged design system in use"


# ---------------------------------------------------------------- layout capture

# Line counts, bar heights, and whether a label wrapped are not visible in source
# at any level of effort: they are what the browser did with the CSS at a width.
# So they are measured, the same way the placeholder and spacing gates are.


# ---------------------------------------------------------------- mobile chrome

# What a phone decides and a desktop review cannot see. A bar that scrolls away
# with the page, a menu measured against the layout viewport rather than the
# visible one, and a bar pinned to the bottom edge with nothing reserving its
# height are three defects that read as correct at every width a reviewer uses,
# because the thing that breaks them - the browser's own retracting toolbar and
# the home indicator under it - is not in the page.

PINNED_EDGE = (r"position:\s*(fixed|sticky)"
               r"|\b(fixed|sticky)\b[^\"'\n]{0,80}\b(top-0|inset-x-0|inset-0)\b"
               r"|\b(top-0|inset-x-0)\b[^\"'\n]{0,80}\b(fixed|sticky)\b")

PINNED_BOTTOM = (r"position:\s*fixed[^;{}]*;[^{}]*bottom:\s*0"
                 r"|\bfixed\b[^\"'\n]{0,80}\bbottom-0\b"
                 r"|\bbottom-0\b[^\"'\n]{0,80}\bfixed\b")

OPENS_SOMETHING = (r"aria-controls|aria-expanded|aria-haspopup|mega-?menu|MegaMenu"
                   r"|MobileMenu|mobile-menu|NavPanel|nav-panel|Drawer|Flyout")


def _chrome_sources(sources):
    """The files that actually hold chrome, so a phone reading is not taken from
    a page that merely mentions a menu."""
    rx = re.compile(CONTEXT_SIGNS["nav"], re.I)
    return {p: t for p, t in sources.items() if rx.search(t)}


def check_mobile_nav_reach(gate, sources, run, ctx):
    """Reachable from wherever the visitor is, not from the top of the page."""
    chrome = _chrome_sources(sources)
    if not chrome:
        return UNKNOWN, "no file in the targets holds a bar to read"
    top = _hits(chrome, PINNED_EDGE)
    bottom = _hits(sources, PINNED_BOTTOM)
    if top:
        return PASS, "the bar is pinned to the top edge at %s" % ", ".join(top[:3])
    if bottom:
        return PASS, "a bar pinned to the bottom edge carries the actions at %s" % ", ".join(bottom[:3])
    return FAIL, ("the bar scrolls away with the page and nothing is pinned to either edge, so a "
                  "visitor a screen down has to scroll back to the top before they can navigate")


def check_menu_viewport_unit(gate, sources, run, ctx):
    """A menu measured against the layout viewport loses its last rows behind the
    browser's own toolbar, which only retracts once the visitor scrolls - and a
    menu is what they opened instead of scrolling."""
    stale = _hits(sources, r"\b\d{1,3}vh\b")
    if stale:
        return FAIL, ("%d place(s) measure against the layout viewport rather than the visible "
                      "one: %s. A phone's toolbar sits inside 100vh and outside 100dvh"
                      % (len(stale), ", ".join(stale[:6])))
    chrome = _chrome_sources(sources)
    if not chrome:
        return UNKNOWN, "no file in the targets holds a menu to read"
    live = _hits(sources, r"\b\d{1,3}(dvh|svh)\b")
    if live:
        return PASS, "every viewport measure reads the visible viewport: %s" % ", ".join(live[:3])
    caps = _hits(chrome, r"max-h(eight)?[-:\[]\s*\[?\s*\d{2,}")
    if caps:
        return FAIL, ("the menu caps its height at a fixed size (%s) and never reads the visible "
                      "viewport, so what fits is decided by the device rather than the screen"
                      % ", ".join(caps[:4]))
    return PASS, "the menu sets no height of its own, so nothing caps it short of the page"


def check_menu_scrolls(gate, sources, run, ctx):
    """A menu taller than the screen has to scroll inside itself, and has to stop
    there rather than dragging the page behind it."""
    chrome = _chrome_sources(sources)
    if not chrome:
        return UNKNOWN, "no file in the targets holds a menu to read"
    if not _hits(chrome, OPENS_SOMETHING):
        return PASS, "the bar opens nothing that could outgrow the screen"
    scroll = _hits(chrome, r"overflow-y-(auto|scroll)|overflow-y:\s*(auto|scroll)|overflow:\s*auto")
    if not scroll:
        return FAIL, ("the menu never scrolls inside itself, so a menu taller than the screen "
                      "loses its last rows with no way to reach them")
    chain = _hits(chrome, r"overscroll-(contain|none)|overscroll-behavior[a-z-]*:\s*(contain|none)")
    if not chain:
        return FAIL, ("the menu scrolls at %s but does not contain its overscroll, so reaching "
                      "its end drags the page along behind it" % scroll[0])
    return PASS, "the menu scrolls at %s with its overscroll contained at %s" % (scroll[0], chain[0])


UNPROMPTED_SWAP = r"setInterval\s*\(|useAnimationFrame\b"
PRESENCE_TAG = r"<AnimatePresence\b[^>]{0,240}>"
PRESENCE_OUT_OF_FLOW = r"mode\s*=\s*[{\"']*\s*[\"'](wait|popLayout)[\"']"
SCROLL_BOUND = r"useScroll\b|scrollYProgress|useScrollParallax\b|scroll-driven"
WIDTH_GATE = r"min-width:\s*\d|\(min-width|matchMedia\s*\("
LATE_REVEAL = r"(?<!root)(?<!Root)\bmargin:\s*[\"'`]\s*-\d+%"


def check_unprompted_motion(gate, sources, run, ctx):
    """The page moving while the reader is holding still, or moving further than
    they moved it.

    Read against a phone, where the columns a wide layout sets beside each other
    are stacked instead. Drift reads as depth only when something holds still
    next to the thing that moves; in one column there is nothing beside it, so
    the same travel reads as the page sliding under a thumb. The three ways a
    page does this to somebody are a swap on a clock, a reveal that starts once
    its subject is already being looked at, and scroll-bound movement nobody
    thought to stop at a narrow width."""
    faults = []

    for path, text in sources.items():
        if not re.search(UNPROMPTED_SWAP, text):
            continue
        for tag in re.findall(PRESENCE_TAG, text):
            if re.search(PRESENCE_OUT_OF_FLOW, tag):
                continue
            faults.append("%s swaps on a clock with both copies left in the flow, so the "
                          "page holds the taller of the two and drops back" % Path(path).name)
            break

    late = _hits(sources, LATE_REVEAL)
    if late:
        faults.append("%d reveal(s) start after their subject has reached the screen and are "
                      "watched rather than met: %s" % (len(late), ", ".join(late[:4])))

    scroll = _hits(sources, SCROLL_BOUND)
    if scroll:
        gated = [p for p, t in sources.items()
                 if re.search(SCROLL_BOUND, t) and re.search(WIDTH_GATE, t)]
        if not gated:
            faults.append("scroll-bound movement at %s with no width below which it stops"
                          % ", ".join(scroll[:3]))

    if faults:
        return FAIL, "; ".join(faults)
    if not scroll and not _hits(sources, PRESENCE_TAG):
        return PASS, "nothing in the page moves on its own or against the scroll"
    return PASS, ("every swap takes the outgoing copy out of the flow, every reveal is settled "
                  "before it is seen, and scroll-bound movement stops at a width")


def check_safe_area(gate, sources, run, ctx):
    """Chrome on a viewport edge sits over the home indicator and the browser's
    own controls unless it reads the inset the device reserves for them."""
    pinned = _hits(sources, PINNED_BOTTOM)
    if not pinned:
        return PASS, "nothing is pinned to the bottom edge"
    inset = _hits(sources, r"safe-area-inset|env\(\s*safe-area")
    if inset:
        return PASS, "the pinned chrome reads the device inset at %s" % inset[0]
    return FAIL, ("chrome is pinned to the bottom edge at %s and nothing reads "
                  "env(safe-area-inset-bottom), so it lands under the home indicator on every "
                  "phone that has one" % pinned[0])


THUMB_ACTIONS = (r"href=[\"'{`]*tel:|href=[\"'{`]*sms:|Book (a|an|now)|Apply (for|now|online)"
                 r"|Get a Quote|Request a Quote|Order Now|Add to Cart|Reserve|Check Availability"
                 r"|Call (us|now|the)|Start Your Order|Schedule")


def check_bottom_rail(gate, sources, run, ctx):
    """Whether the one thing a visitor came to do is under a thumb, or whether
    they have to find it. Applicability is read from the page rather than
    assumed: a rail is for a phone-shaped action, not for every site."""
    if _hits(sources, PINNED_BOTTOM):
        hits = _hits(sources, PINNED_BOTTOM)
        return PASS, "a bar is pinned to the bottom edge at %s" % ", ".join(hits[:2])
    if not ctx.get("marketing"):
        return PASS, "this is an app surface, where a persistent action bar belongs to the app shell"
    actions = _hits(sources, THUMB_ACTIONS)
    if not actions:
        return PASS, ("the page publishes no phone-shaped action - no tel:, no booking, no order, "
                      "no application - so there is nothing a rail would carry")
    return FAIL, ("the page's primary action is one a thumb should reach from anywhere (%s) and "
                  "nothing is pinned to the bottom edge on a small screen, so reaching it costs a "
                  "scroll back to the bar" % ", ".join(actions[:3]))


RESERVE = (r"padding-bottom[^;{}]*var\(--([a-z0-9-]+)\)"
           r"|pb-\[[^\]]*var\(--([a-z0-9-]+)\)"
           r"|h-\[var\(--([a-z0-9-]+)\)\]"
           r"|height:\s*var\(--([a-z0-9-]+)\)")


def check_rail_reserve(gate, sources, run, ctx):
    """A bar out of flow takes no height, so the end of the page finishes
    underneath it unless the same height is reserved somewhere in flow."""
    pinned = _hits(sources, PINNED_BOTTOM)
    if not pinned:
        return PASS, "nothing is pinned to the bottom edge"
    reserved = []
    rx = re.compile(RESERVE, re.I)
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), 1):
            match = rx.search(line)
            if match:
                token = next(g for g in match.groups() if g)
                reserved.append(("%s:%d" % (Path(path).name, i), token))
    if reserved:
        return PASS, ("the pinned height is reserved in flow at %s, from --%s"
                      % (reserved[0][0], reserved[0][1]))
    padded = _hits(sources, r"(body|#root|main|\.page)[^{}]*\{[^{}]*padding-bottom")
    if padded:
        return PASS, "the page reserves space beneath its last line at %s" % padded[0]
    return FAIL, ("a bar is pinned to the bottom edge at %s and nothing in flow reserves its "
                  "height, so the last of the page - the footer, the final action - finishes "
                  "underneath it" % pinned[0])


# ---------------------------------------------------------------- the panel's surface


PANEL_LINE = re.compile(r"panel|mega-?menu|menu|flyout|dropdown|drawer", re.I)
SEE_THROUGH = re.compile(r"backdrop-blur|backdrop-filter"
                         r"|\bbg-[a-z0-9-]+/\d{1,3}\b"
                         r"|background[^;{}]*rgba?\([^)]*[,/]\s*0?\.\d"
                         r"|background[^;{}]*\bopacity\b", re.I)
HAS_GROUND = re.compile(r"\bbg-|background(-color)?\s*:", re.I)


def check_panel_opaque(gate, sources, run, ctx):
    """A panel is a surface, not a filter. Anything showing through it puts the
    page's own type behind the menu's type, which is unreadable at exactly the
    moment a visitor is choosing where to go."""
    chrome = _chrome_sources(sources)
    if not chrome:
        return UNKNOWN, "no file in the targets holds a bar to read"
    if not _hits(chrome, OPENS_SOMETHING):
        return PASS, "the bar opens no panel"
    faults = []
    for path, text in chrome.items():
        for i, line in enumerate(text.splitlines(), 1):
            if not (PANEL_LINE.search(line) and HAS_GROUND.search(line)):
                continue
            if SEE_THROUGH.search(line):
                faults.append("%s:%d" % (Path(path).name, i))
    if faults:
        return FAIL, ("the open panel lets the page through at %s - an alpha ground, a backdrop "
                      "blur, or both. Give it the same solid surface the bar carries"
                      % ", ".join(faults[:5]))
    return PASS, "every surface the bar opens is opaque"


TOP_RAIL_NAMES = (r"TopBar|TopStrip|UtilityBar|UtilityStrip|AnnouncementBar|PreHeader|EyebrowBar"
                  r"|top-bar|utility-bar|announcement-bar|pre-header|eyebrow-bar|topbar")

RAIL_WORTHY = (r"href=[\"'{`]*tel:|href=[\"'{`]*mailto:"
               r"|LOCALE|currency|Language|Store Locator|Track (My )?Order|Free Shipping"
               r"|Sign In|Log In|Client Portal|Main Site|Parent|Careers hotline"
               r"|Mon-Fri|Open (today|daily)|Hours")


def check_top_rail(gate, sources, run, ctx):
    """Whether the strip above the bar was decided rather than never considered.
    It is not owed by every site; it is owed by one that has something to say
    above its own navigation and is saying it somewhere worse instead."""
    chrome = _chrome_sources(sources)
    if not chrome:
        return UNKNOWN, "no file in the targets holds a bar to read"
    rail = _hits(chrome, TOP_RAIL_NAMES)
    if rail:
        return PASS, "the bar carries a strip above it at %s" % ", ".join(rail[:3])
    worthy = _hits(chrome, RAIL_WORTHY)
    if not worthy:
        return PASS, ("the bar has nothing to put above itself - no number, no hours, no locale, "
                      "no second brand, no account - so a rail would be a bar of filler")
    return FAIL, ("the bar carries utility content it has nowhere to put (%s) and no strip above "
                  "it. Either lift it into a rail above the bar, or record why this one is better "
                  "off without one" % ", ".join(worthy[:3]))


# ---------------------------------------------------------------- drawn art


def _enclosing_object(text, offset):
    """The object literal a key sits directly inside, and the keys at its own
    level. An indentation window cannot tell a key from its cousin one object
    over, which is the difference between a plate on a destination and a plate
    on the block a destination sits in."""
    quoted = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`]*`",
                    lambda m: " " * len(m.group(0)), text)
    depth = 0
    start = None
    for i in range(offset, -1, -1):
        ch = quoted[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                start = i
                break
            depth -= 1
    if start is None:
        return []
    depth = 0
    end = len(quoted)
    for i in range(start, len(quoted)):
        ch = quoted[i]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                end = i
                break
    own = []
    depth = 0
    for match in re.finditer(r"[{}\[\]]|([A-Za-z_$][\w$]*)\s*:", quoted[start:end]):
        token = match.group(0)
        if token in "{[":
            depth += 1
        elif token in "}]":
            depth -= 1
        elif depth == 1:
            own.append(match.group(1))
    return own


ART_REF = re.compile(r"/art/|ArtPlate|Illustration|<Plate\b|illustration|artwork", re.I)
PLATE_KEY = re.compile(r"\b(art|illustration|plate|artwork)\s*:", re.I)


def _svg_files(run):
    out = []
    for target in run["targets"]:
        base = Path(target)
        if base.is_file() and base.suffix.lower() == ".svg":
            out.append(base)
        elif base.is_dir():
            out.extend(p for p in base.rglob("*.svg") if p.is_file())
    return out


DRAWING = re.compile(r"<(path|rect|circle|ellipse|polygon|polyline|line)\b")


def _plates(run):
    """The svg files that are drawings rather than glyphs: a mark on a 24 grid is
    an icon, a scene on a wider ground is a plate."""
    out = []
    for path in _svg_files(run):
        if re.search(r"favicon|logo|sprite", path.name, re.I):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        box = re.search(r'viewBox="([\d.\s-]+)"', text)
        width = 0.0
        if box:
            parts = box.group(1).split()
            if len(parts) == 4:
                try:
                    width = float(parts[2])
                except ValueError:
                    width = 0.0
        if len(DRAWING.findall(text)) < 5 or width and width <= 64:
            continue
        out.append((path, text, box.group(1) if box else ""))
    return out


def check_drawn_plates(gate, sources, run, ctx):
    """Art the project drew for its own subject, kept as a file and reused."""
    plates = _plates(run)
    if plates:
        return PASS, ("%d drawn plate(s) in the tree: %s"
                      % (len(plates), ", ".join(p[0].name for p in plates[:5])))
    if _hits(sources, ART_REF.pattern):
        return UNKNOWN, ("the source references drawn art but no plate file was found under the "
                         "targets - confirm where it lives")
    return FAIL, ("the page is illustrated entirely by photography and library glyphs. A surface "
                  "with something of its own to show - what it moves, builds, sells, or repairs - "
                  "draws it: flat vector plates in the project's own palette, kept as files under "
                  "public/art and reused, not one more stock photograph")


def check_plate_per_row(gate, sources, run, ctx):
    """A plate is imagery for the block it sits in. One per row turns it into a
    bullet: the reader stops seeing the drawing and starts seeing a list with
    decorations, and every row's mark has to be invented to fill the slot.

    What separates the two is what the plate sits beside. A destination carries a
    label and a route; a promoted block carries a heading and a sentence. A plate
    keyed on the first is a row marker, and a plate keyed on the second is the
    panel's own imagery, however many panels there are.
    """
    faults = []
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not ART_REF.search(line):
                continue
            window = "\n".join(lines[max(0, i - 25):i + 1])
            if ".map(" not in window:
                continue
            if not re.search(r"\b(to|href)\s*[=:]", window):
                continue
            if not re.search(r"nav|menu|panel|links?\b|destinations?", window, re.I):
                continue
            faults.append("%s:%d" % (Path(path).name, i + 1))
    for path, text in sources.items():
        lines = text.splitlines()
        for match in re.finditer(PLATE_KEY.pattern, text, re.I):
            line = text.count("\n", 0, match.start()) + 1
            if ART_REF.search(lines[line - 1]):
                continue
            keys = set(_enclosing_object(text, match.start()))
            if keys & {"label", "name"} and keys & {"to", "href"}:
                faults.append("%s:%d (a plate keyed on a destination row)"
                              % (Path(path).name, line))
    if faults:
        return FAIL, ("art is rendered once per row at %s. One plate belongs to a panel or a "
                      "section, not to each link inside it" % ", ".join(sorted(set(faults))[:4]))
    return PASS, "no plate is keyed to a destination row"


def check_plate_cohesion(gate, sources, run, ctx):
    """A set of plates is one set: the same ground, the same grid, the same
    handful of inks. Two grids and two palettes read as clip art."""
    plates = _plates(run)
    if len(plates) < 2:
        return PASS, "fewer than two plates, so there is no set to hold together"
    boxes = {}
    inks = {}
    for path, text, box in plates:
        boxes.setdefault(box, []).append(path.name)
        inks[path.name] = set(m.lower() for m in re.findall(r"#[0-9a-fA-F]{3,8}", text))
    faults = []
    if len(boxes) > 1:
        faults.append("%d viewBoxes across the set: %s"
                      % (len(boxes), "; ".join("%s in %s" % (b or "none", ", ".join(n))
                                               for b, n in list(boxes.items())[:3])))
    shared = set.intersection(*inks.values()) if inks else set()
    union = set().union(*inks.values()) if inks else set()
    if union and len(shared) < 2:
        odd = sorted(inks, key=lambda n: len(inks[n] & shared))[:2]
        faults.append("the plates share %d ink(s) out of %d; %s sit outside the palette"
                      % (len(shared), len(union), " and ".join(odd)))
    if faults:
        return FAIL, "; ".join(faults)
    return PASS, ("%d plates on one %s ground sharing %d ink(s)"
                  % (len(plates), list(boxes)[0] or "unstated", len(shared)))


def _svg_guard():
    """The tool that decides what a ground is, loaded rather than reimplemented.

    Two readings of the same question drift, and the gate would then pass a file
    the commit hook refuses. This engine asks the hook's own measure instead, and
    a tree without it answers nothing rather than answering differently.
    """
    global _SVG_GUARD
    if _SVG_GUARD is not None:
        return _SVG_GUARD or None
    for root in (Path.home() / ".claude" / "tools",
                 Path(__file__).resolve().parent.parent.parent.parent / "tools"):
        target = root / "svg-guard.py"
        if not target.is_file():
            continue
        spec = importlib.util.spec_from_file_location("svg_guard", str(target))
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            continue
        _SVG_GUARD = module
        return module
    _SVG_GUARD = False
    return None


_SVG_GUARD = None


def _plate_ground(text, box=""):
    """Every place the file paints behind its own drawing.

    A ground belongs to whatever renders the plate, never to the plate, so this
    is a list of faults rather than a property. The `box` argument is kept for
    callers that still pass the viewBox; the measure reads it out of the file.
    """
    guard = _svg_guard()
    if guard is None:
        return []
    return guard.background_findings(text, ".svg")


def _plate_scene(text):
    """Whether the drawing is a scene assembled out of boxes and straight lines.

    Returned as the measure itself rather than a verdict, so the gate can say
    how far past the line a file is instead of only that it is.
    """
    guard = _svg_guard()
    if guard is None:
        return []
    return guard.scene_findings(text)


def check_plate_depth(gate, sources, run, ctx):
    """A plate is a drawing, not a silhouette.

    The difference is countable. A silhouette is one outline in one fill: the
    subject's edge and nothing inside it. A drawing has the parts the subject
    actually has, and it has two tones wherever one material turns away from the
    light, which is what stops a flat vector reading as a sticker. Neither
    number judges taste; both refuse the shape that comes out of a first pass.
    """
    plates = _plates(run)
    if not plates:
        return PASS, "no plate to measure"
    shapes_floor = gate["check"].get("min_shapes", 12)
    inks_floor = gate["check"].get("min_inks", 5)
    thin = []
    for path, text, _ in plates:
        shapes = len(re.findall(r"<(path|rect|circle|ellipse|polygon|polyline|line)\b", text))
        inks = {m.lower() for m in re.findall(r"#[0-9a-fA-F]{3,8}", text)}
        if shapes < shapes_floor or len(inks) < inks_floor:
            thin.append("%s draws %d shape(s) in %d ink(s)" % (path.name, shapes, len(inks)))
    if thin:
        return FAIL, ("%s. A plate under %d shapes or %d inks is an outline: give the subject the "
                      "parts it has, and a second tone wherever a face turns away"
                      % ("; ".join(thin[:4]), shapes_floor, inks_floor))
    return PASS, ("%d plate(s), the thinnest at %d shapes"
                  % (len(plates),
                     min(len(re.findall(r"<(path|rect|circle|ellipse|polygon|polyline|line)\b", t))
                         for _, t, _ in plates)))


def check_plate_ground(gate, sources, run, ctx):
    """The ground belongs to whatever renders the plate, never to the plate.

    A file that paints its own backdrop looks right on the one surface it was
    drawn against and wrong on every other, and the author never sees it because
    they were looking at that surface. Moving the ground to the placement costs
    a class and makes the same drawing usable on a dark band, a light card and a
    page that changes theme under it.

    The measure is the commit hook's own, so a plate this gate passes is a plate
    the hook will let through.
    """
    plates = _plates(run)
    if not plates:
        return PASS, "no plate to place"
    if _svg_guard() is None:
        return UNKNOWN, ("svg-guard.py is not installed beside this engine, so the "
                         "ground could not be measured - check the config tree")
    painted = []
    for path, text, _ in plates:
        for line, kind, excerpt, _, _ in _plate_ground(text):
            painted.append("%s:%d %s" % (path.name, line, kind))
    if painted:
        return FAIL, ("%s. Take the ground out of the file and put it on whatever "
                      "renders the plate, so the drawing reads on every surface it "
                      "lands on: ~/.claude/tools/svg-guard.py fix <path>"
                      % "; ".join(painted[:4]))
    return PASS, "%d plate(s), none painting its own ground" % len(plates)


def check_svg_transparent(gate, sources, run, ctx):
    """Every SVG in the run, not only the drawn plates, is transparent.

    PLT-05 holds the plates. This holds the rest of the tree - marks, icons,
    favicons, exported artwork - because a backdrop in any of them is the same
    fault and the export path is where one arrives without anybody drawing it.
    """
    files = _svg_files(run)
    if not files:
        return PASS, "no SVG in the run"
    if _svg_guard() is None:
        return UNKNOWN, ("svg-guard.py is not installed beside this engine, so the "
                         "exports could not be measured - check the config tree")
    painted = []
    for path in files:
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for line, kind, excerpt, _, _ in _plate_ground(text):
            painted.append("%s:%d %s" % (path.name, line, kind))
    if painted:
        return FAIL, ("%s. An exported graphic carries its drawing and nothing "
                      "behind it: ~/.claude/tools/svg-guard.py fix <path>"
                      % "; ".join(painted[:4]))
    return PASS, "%d SVG file(s), none painting its own ground" % len(files)


def check_plate_scene(gate, sources, run, ctx):
    """A drawing too complicated to come out right is the wrong drawing.

    Past a certain size a flat vector stops being a mark and becomes a scene,
    and a scene assembled out of axis-aligned rectangles and straight segments
    does not end up looking like the thing it depicts however long it is
    adjusted. The signature is countable and it is the opposite of exported
    artwork, which is curves: forty or more drawn elements, most of them boxes
    and lines. The floor in PLT-04 asks for the subject's own parts; this is the
    ceiling that stops a first pass answering it by adding more boxes.
    """
    plates = _plates(run)
    if not plates:
        return PASS, "no plate to measure"
    if _svg_guard() is None:
        return UNKNOWN, ("svg-guard.py is not installed beside this engine, so the "
                         "drawings could not be measured - check the config tree")
    scenes = []
    for path, text, _ in plates:
        for _, count, share in _plate_scene(text):
            scenes.append("%s draws %d element(s), %d%% of them boxes and straight lines"
                          % (path.name, count, round(share * 100)))
    if scenes:
        return FAIL, ("%s. Reach for a photograph, a real exported asset, or a mark "
                      "simple enough to be right - a silhouette, a wordmark, a single "
                      "shape - rather than more rectangles" % "; ".join(scenes[:3]))
    return PASS, "%d plate(s), none of them a scene built out of boxes" % len(plates)


PAGE_PLATE_FLOOR = 40


def check_plate_scale(gate, sources, run, ctx):
    """A plate carrying a section is sized off the column, not pinned small.

    A drawing at the height of a heading reads as a mark above it - decoration
    the eye skips on its way to the words. Imagery for a block is as wide as the
    block will let it be. Chrome is the exception and always was: a panel has the
    room it has.
    """
    plates = {path.stem for path, _, _ in _plates(run)}
    if not plates:
        return PASS, "no plate to size"
    chrome = set(_chrome_sources(sources))
    small = []
    for path, text in sources.items():
        if path in chrome:
            continue
        for match in re.finditer(r"[^\n]*(?:/art/|ArtPlate)[^\n]*", text):
            row = match.group(0)
            if not any(n in row for n in plates) and "ArtPlate" not in row:
                continue
            classes = re.search(r'className\s*=\s*"([^"]*)"', row)
            if not classes:
                continue
            body = classes.group(1)
            if re.search(r"\bw-full\b|\bw-\[|\bmax-w-", body):
                continue
            height = re.search(r"\bh-(\d{1,2})\b", body)
            if height and int(height.group(1)) < PAGE_PLATE_FLOOR:
                line = text.count("\n", 0, match.start()) + 1
                small.append("%s:%d at h-%s" % (Path(path).name, line, height.group(1)))
    if small:
        return FAIL, ("%s. A page plate takes a width off its column - w-full with a max-width - "
                      "so it carries the section rather than sitting above its heading"
                      % "; ".join(small[:4]))
    return PASS, "every page plate is sized off its column"


# ---------------------------------------------------------------- the phone capture


MOBILE_DIMENSIONS = {
    "capture": "whether the phone reading was taken at all",
    "nav-reach": "whether the bar is still reachable a screen down",
    "menu-fit": "whether every row of the open menu can be reached",
    "rail-clearance": "whether chrome pinned to the bottom covers the end of the page",
    "side-scroll": "whether the page scrolls sideways at phone width",
    "tap-size": "whether every control is a target a thumb can hit",
}


def mobile_verdict(run, dimension):
    cap = run.get("mobile")
    if not cap:
        return FAIL, ("no phone capture on this run - render the page at 390x844, run "
                      "scripts/code/mobile-probe.js and await mobileProbe.scan(), then: "
                      "%s mobile-probe --file <capture.json>" % TOOL)
    if dimension == "capture":
        return PASS, ("a phone reading was taken at %s on %s"
                      % (cap.get("captured", "an unstated viewport"),
                         cap.get("url", "the page")))
    if dimension == "nav-reach":
        reach = cap.get("reach") or {}
        if not reach.get("found"):
            return FAIL, "the capture found no bar to measure"
        if reach.get("onScreenAtDepth"):
            return PASS, ("the bar is on screen %spx down, position %s"
                          % (reach.get("scrolledTo"), reach.get("positionAtDepth")))
        if reach.get("bottomRail"):
            return PASS, ("the bar scrolls away but a bar pinned to the bottom edge carries the "
                          "actions %spx down" % reach.get("scrolledTo"))
        return FAIL, ("%spx down the bar sits at %.0fpx, off the top of a %s viewport, and nothing "
                      "is pinned to the bottom edge either: navigating means scrolling back up"
                      % (reach.get("scrolledTo"), reach.get("topAtDepth", 0), cap.get("captured")))
    if dimension == "menu-fit":
        menu = cap.get("menu") or {}
        if not menu.get("found"):
            return UNKNOWN, ("the capture could not open a menu to measure: %s"
                             % menu.get("reason", "no reason recorded"))
        below = menu.get("rowsBelowFold") or []
        if not below:
            return PASS, ("all %d rows of the open menu are inside the %s viewport"
                          % (menu.get("rows", 0), cap.get("captured")))
        if menu.get("scrollsInside"):
            return PASS, ("%d row(s) sit below the fold and the menu scrolls inside itself at %s "
                          "to reach them" % (len(below), menu.get("scroller")))
        named = ", ".join("%s at %dpx" % (r.get("label") or r.get("element"), r.get("bottom", 0))
                          for r in below[:4])
        return FAIL, ("%d row(s) of the open menu fall below the %dpx viewport with no way to "
                      "scroll to them: %s. On a phone the browser's own toolbar takes another "
                      "60-100px off that" % (len(below), menu.get("viewportHeight", 0), named))
    if dimension == "rail-clearance":
        rail = cap.get("rail") or {}
        if not rail.get("found"):
            return PASS, "nothing is pinned to the bottom edge"
        covered = [b for b in rail.get("bars", []) if (b.get("covers") or 0) > 1]
        if not covered:
            return PASS, ("%d pinned bar(s) and the page ends above them"
                          % len(rail.get("bars", [])))
        worst = max(covered, key=lambda b: b.get("covers", 0))
        return FAIL, ("%s covers the last %.0fpx of the page, including %s. Reserve its height in "
                      "flow" % (worst.get("element"), worst.get("covers", 0),
                                worst.get("coveredElement") or "the end of the document"))
    if dimension == "side-scroll":
        side = cap.get("sideScroll") or {}
        over = side.get("overflowPx", 0)
        if over <= 1:
            return PASS, "the page does not scroll sideways at %s" % cap.get("captured")
        named = ", ".join("%s to %dpx" % (c.get("element"), c.get("right", 0))
                          for c in (side.get("culprits") or [])[:3])
        return FAIL, ("the page scrolls %.0fpx sideways at %s: %s"
                      % (over, cap.get("captured"), named or "no element identified"))
    if dimension == "tap-size":
        small = cap.get("smallTargets") or []
        menu = cap.get("menu") or {}
        small = small + (menu.get("smallTargets") or [])
        if not small:
            return PASS, "every control measures at least 44px on both axes"
        named = ", ".join("%s at %dx%d" % (s.get("label") or s.get("element"),
                                           s.get("width", 0), s.get("height", 0))
                          for s in small[:4])
        return FAIL, ("%d control(s) are under 44px on a phone: %s" % (len(small), named))
    return UNKNOWN, "unknown mobile dimension %s" % dimension


def check_mobile_probe(gate, sources, run, ctx):
    return mobile_verdict(run, gate["check"]["dimension"])


LAYOUT_DIMENSIONS = {
    "hero-headline": "the hero headline's rendered line count",
    "hero-subtext": "the hero subtext's word and line count",
    "nav-lines": "whether the navigation wrapped to a second row",
    "nav-height": "the navigation bar's measured height",
    "rounded-fit": "whether anything sits outside a rounded container's corner arc",
}


def layout_verdict(run, dimension):
    cap = run.get("layout")
    if not cap:
        return FAIL, ("no layout capture on this run - render the page at desktop width, run "
                      "scripts/code/layout-probe.js, then: %s layout-probe --file <capture.json>" % TOOL)
    if dimension == "rounded-fit":
        # A capture from before this dimension existed carries no key at all,
        # which is not the same as a capture that looked and found nothing.
        rows = cap.get("roundedFit")
        if rows is None:
            return FAIL, ("this capture predates the rounded-fit reading - re-capture with "
                          "scripts/code/layout-probe.js")
        if not rows:
            return PASS, "nothing sits outside a rounded container's corner arc"
        worst = sorted(rows, key=lambda r: (r.get("needed", 0) - r.get("inset", 0)), reverse=True)
        named = ["%s in %s is inset %.1fpx where the %dpx corner needs %.1f"
                 % (r.get("child"), r.get("container"), r.get("inset", 0),
                    r.get("radius", 0), r.get("needed", 0)) for r in worst[:3]]
        return FAIL, ("%d element(s) break out of a rounded container: %s. A square child "
                      "against a corner arc hangs outside it; inset the row by at least what "
                      "the radius needs at that height"
                      % (len(rows), "; ".join(named)))
    if dimension == "hero-headline":
        hero = cap.get("hero") or {}
        if not hero.get("found"):
            return FAIL, "the capture found no hero to measure"
        lines = hero.get("headlineLines")
        if lines is None:
            return FAIL, "the capture records no headline line count - re-capture"
        if lines > 2:
            return FAIL, "the hero headline wraps to %d lines: '%s'" % (lines, (hero.get("headlineText") or "")[:70])
        return PASS, "the hero headline holds %d line(s)" % lines
    if dimension == "hero-subtext":
        hero = cap.get("hero") or {}
        if not hero.get("found"):
            return FAIL, "the capture found no hero to measure"
        words, lines = hero.get("subtextWords"), hero.get("subtextLines")
        if words is None:
            return PASS, "the hero carries no subtext"
        faults = []
        if words > 20:
            faults.append("%d words" % words)
        if lines and lines > 4:
            faults.append("%d lines" % lines)
        if not hero.get("ctaInFold", True):
            faults.append("the primary CTA falls below the fold")
        if faults:
            return FAIL, "hero subtext runs to %s" % " and ".join(faults)
        return PASS, "hero subtext is %d words over %s line(s), CTA in the fold" % (words, lines)
    if dimension == "nav-lines":
        nav = cap.get("nav") or {}
        if not nav.get("found"):
            return FAIL, "the capture found no navigation to measure"
        rows = nav.get("rows", 1)
        if rows > 1:
            return FAIL, "the navigation wraps to %d rows at %s" % (rows, cap.get("captured", "the captured width"))
        return PASS, "the navigation holds one row at %s" % cap.get("captured", "the captured width")
    if dimension == "nav-height":
        nav = cap.get("nav") or {}
        if not nav.get("found"):
            return FAIL, "the capture found no navigation to measure"
        height = nav.get("height")
        if height is None:
            return FAIL, "the capture records no navigation height - re-capture"
        if height > 80:
            return FAIL, "the navigation measures %.0fpx against a cap of 80" % height
        return PASS, "the navigation measures %.0fpx" % height
    return UNKNOWN, "unknown layout dimension %s" % dimension


def check_layout_probe(gate, sources, run, ctx):
    return layout_verdict(run, gate["check"]["dimension"])


# ---------------------------------------------------------------- the design read


READ_FIELDS = ("page_kind", "audience", "vibe")


def check_design_read(gate, sources, run, ctx):
    read = run.get("read") or {}
    missing = [f for f in READ_FIELDS if not read.get(f)]
    if missing:
        return FAIL, ("the design read is missing %s - record it before the work: %s read "
                      "--page-kind <landing|portfolio|editorial|product> --audience <who> --vibe <words>"
                      % (", ".join(m.replace("_", " ") for m in missing), TOOL))
    return PASS, ("read as %s for %s, %s" % (read["page_kind"], read["audience"], read["vibe"]))


GENRE_HISTORY = _state_root() / "design-genre-history.json"


def _genre_history():
    try:
        return json.loads(GENRE_HISTORY.read_text())
    except (OSError, ValueError):
        return {}


def _project_of(path):
    """The project a run was on, read as its git origin so that two worktrees of
    one repository are one project. A path with no origin stands for itself."""
    if not path:
        return ""
    try:
        out = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().removesuffix(".git").lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return str(path)


def _record_project(record):
    """A history record's project, reading the run file for records written
    before the project was kept on them."""
    if record.get("project"):
        return record["project"]
    try:
        old = json.loads((RUNS / ("%s.json" % record.get("run"))).read_text())
    except (OSError, ValueError):
        return ""
    return _project_of(old.get("repo") or (old.get("targets") or [""])[0])


def _previous_in_genre(genre, run):
    """The last run of this page kind on a different project. Rotation is about
    two pages of one kind looking like one template; a rerun of the same site
    is the same page, and comparing it against itself would force the site to
    change shape every time it is checked. Each genre keeps its recent runs so
    that a rerun does not push the last other site out of the comparison."""
    entry = _genre_history().get(genre) or {}
    here = _project_of(run.get("repo") or (run.get("targets") or [""])[0])
    records = [entry] + list(entry.get("recent") or []) if entry else []
    for record in records:
        if not record or record.get("run") == run["id"]:
            continue
        if here and _record_project(record) == here:
            continue
        return record
    return None


def record_genre(run):
    """Keep what direction each genre was last taken in, so the next one can differ."""
    read = run.get("read") or {}
    genre = (read.get("page_kind") or "").strip().lower()
    if not genre:
        return
    history = _genre_history()
    earlier = history.get(genre) or {}
    recent = [dict((k, v) for k, v in earlier.items() if k != "recent")] if earlier else []
    recent += list(earlier.get("recent") or [])
    recent = [r for r in recent if r.get("run") != run["id"]][:10]
    stamped = _stamped({path: text for path, text in read_sources(run["targets"]).items()
                        if Path(path).suffix.lower() in SERVED_EXT})
    history[genre] = {
        "run": run["id"],
        "project": _project_of(run.get("repo") or (run.get("targets") or [""])[0]),
        "recent": recent,
        "nav_archetype": (stamped.get("nav") or [("", "")])[0][0],
        "footer_archetype": (stamped.get("footer") or [("", "")])[0][0],
        "panel_archetype": (stamped.get("panel") or [("", "")])[0][0],
        "palette": (read.get("palette") or "").strip().lower(),
        "face": (read.get("face") or "").strip().lower(),
        "vibe": (read.get("vibe") or "").strip().lower(),
    }
    GENRE_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    GENRE_HISTORY.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n")


def check_genre_rotation(gate, sources, run, ctx):
    read = run.get("read") or {}
    genre = (read.get("page_kind") or "").strip().lower()
    if not genre:
        return FAIL, "no page kind on the design read to compare against earlier runs of the same genre"
    previous = _previous_in_genre(genre, run)
    if not previous:
        return PASS, "no earlier %s run on another project to repeat" % genre
    palette = (read.get("palette") or "").strip().lower()
    face = (read.get("face") or "").strip().lower()
    if not palette and not face:
        return FAIL, ("the last %s run used palette '%s' and face '%s'; record this run's with "
                      "%s read --palette <family> --face <display face> so the two can be compared"
                      % (genre, previous.get("palette") or "unrecorded",
                         previous.get("face") or "unrecorded", TOOL))
    repeats = []
    if palette and palette == previous.get("palette"):
        repeats.append("palette '%s'" % palette)
    if face and face == previous.get("face"):
        repeats.append("display face '%s'" % face)
    if repeats:
        return FAIL, ("this run repeats the last %s run's %s - rotate it"
                      % (genre, " and ".join(repeats)))
    return PASS, ("differs from the last %s run: palette '%s' against '%s', face '%s' against '%s'"
                  % (genre, palette or "unrecorded", previous.get("palette") or "unrecorded",
                     face or "unrecorded", previous.get("face") or "unrecorded"))



# ---------------------------------------------------------------- archetype

# A defect-free surface and a designed one are different claims, and only the
# first is visible to a regex. What separates them is a decision: which shape out
# of the catalog this bar, this footer, this page is, made on purpose and stated.
#
# So the shape is declared in a stamp, the declaration is checked against the
# catalog, the default shape is refused, and the choice has to differ from the
# last run of the same kind. A bar can then be flawless and still fail, which is
# the whole point: flawless is what the rest of the gates already measure.

ARCHETYPE_DIR = ENGINE / "references/hallmark/references/components"

# The shapes every generated page reaches for first.
DEFAULT_ARCHETYPES = {
    "nav": {"n1b-saas-three-section", "n1-wordmark-2-links"},
    "footer": {"ft3-index-style-category-list"},
}

ARCHETYPE_STAMP = re.compile(r"/\*+\s*(nav|footer|panel)\s*:\s*([a-z0-9-]+)", re.I)


def _catalog(prefix):
    """Every archetype the catalog offers for one surface, by slug."""
    if not ARCHETYPE_DIR.is_dir():
        return set()
    out = set()
    for path in ARCHETYPE_DIR.glob("*.md"):
        stem = path.stem.lower()
        if prefix == "nav" and re.match(r"n\d", stem):
            out.add(stem)
        elif prefix == "footer" and stem.startswith("ft"):
            out.add(stem)
        elif prefix == "panel" and re.match(r"mm\d", stem):
            out.add(stem)
    return out


def _stamped(sources):
    found = {}
    for path, text in sources.items():
        for surface, slug in ARCHETYPE_STAMP.findall(text):
            found.setdefault(surface.lower(), []).append((slug.lower(), Path(path).name))
    return found


def check_archetype_stamp(gate, sources, run, ctx):
    """The surface names which catalog shape it is, and the name is a real one."""
    surface = gate["check"]["surface"]
    catalog = _catalog(surface)
    if not catalog:
        return UNKNOWN, "the archetype catalog is not installed beside this tool"
    stamped = _stamped(sources).get(surface, [])
    if not stamped:
        return FAIL, ("the %s declares no archetype. Pick one from %s and stamp it at the top of "
                      "its stylesheet as /* %s: <slug> */ - a shape chosen on purpose is the "
                      "difference between designed and default. Available: %s"
                      % (surface, ARCHETYPE_DIR.name, surface,
                         ", ".join(sorted(catalog)[:6]) + ", ..."))
    unknown = [s for s, _ in stamped if s not in catalog]
    if unknown:
        return FAIL, ("stamped %s is not in the catalog: %s. Real ones: %s"
                      % (surface, ", ".join(unknown), ", ".join(sorted(catalog))))
    return PASS, "%s declared as %s" % (surface, ", ".join(s for s, _ in stamped))


def check_archetype_not_default(gate, sources, run, ctx):
    """The declared shape is not the one every generated page reaches for."""
    surface = gate["check"]["surface"]
    stamped = _stamped(sources).get(surface, [])
    if not stamped:
        return FAIL, "no %s archetype declared, so there is nothing to judge" % surface
    defaults = DEFAULT_ARCHETYPES.get(surface, set())
    reached = [(s, where) for s, where in stamped if s in defaults]
    if reached:
        return FAIL, ("the %s is the default shape (%s at %s). Every generated page lands here "
                      "first. Choose a different archetype, or dispute this with the reason it "
                      "is right for this brief."
                      % (surface, reached[0][0], reached[0][1]))
    return PASS, "%s is %s, not the default shape" % (surface, stamped[0][0])


def check_archetype_rotation(gate, sources, run, ctx):
    """The shape differs from the last run of the same page kind."""
    surface = gate["check"]["surface"]
    stamped = _stamped(sources).get(surface, [])
    if not stamped:
        return FAIL, "no %s archetype declared, so nothing can be compared" % surface
    current = stamped[0][0]
    read = run.get("read") or {}
    genre = (read.get("page_kind") or "").strip().lower()
    if not genre:
        return FAIL, ("no page kind on the design read, so the %s cannot be compared against the "
                      "last run of its kind" % surface)
    previous = (_previous_in_genre(genre, run) or {}).get("%s_archetype" % surface)
    if not previous:
        return PASS, "no earlier %s run on another project to repeat; this one is %s" % (genre, current)
    if previous == current:
        return FAIL, ("the last %s run used the same %s archetype (%s). Two pages of one kind "
                      "sharing a shape is the template showing through." % (genre, surface, current))
    return PASS, "%s is %s against the last run's %s" % (surface, current, previous)


# ---------------------------------------------------------------- nav panels

# A row of links is a link list. What the reference bars do is open something:
# apple.com puts a full-width panel under every top-level word, anduril.com and
# stripe.com the same. Depth is the difference between a bar that navigates a
# product and a bar that names a few pages, and it is the one thing a flat bar
# can never fake with surface polish.

PANEL_TRIGGER = re.compile(
    r"<(?:button|a|div|summary|Trigger|[A-Z][A-Za-z]*Trigger)\b[^>]*"
    r"(?:aria-expanded|aria-haspopup|data-state=[\"\']?(?:open|closed))",
    re.I | re.S,
)
PANEL_BODY = re.compile(
    r"(?:mega-?menu|flyout|dropdown-?panel|nav[a-z_-]*(?:menu|panel|dropdown)|"
    r"<(?:Dropdown|Popover|Menu|NavigationMenu)(?:Menu)?Content\b|role=[\"\']menu[\"\'])",
    re.I,
)
GENERIC_MENU_LABEL = re.compile(
    r"[^<]*>\s*(?:<[^>]*>\s*)*(?:\{[^}]*\}\s*)?(?:open\s+|close\s+)?(?:menu|more|browse)\s*(?:</|\{|<)",
    re.I,
)
MOBILE_ONLY = re.compile(
    r"md:hidden|lg:hidden|sm:hidden|mobile-only|hamburger|menu-toggle|"
    r"aria-label=[\"\'][^\"\']*(?:menu|hamburger)", re.I,
)


def check_nav_has_panel(gate, sources, run, ctx):
    """A site bar opens at least one panel, rather than listing links and stopping."""
    floor = gate["check"].get("min_items", 3)
    blob = "\n".join(sources.values())
    triggers = [t for t in PANEL_TRIGGER.findall(blob) if not MOBILE_ONLY.search(t)]
    # A lone Menu button is a phone menu, whatever width it shows at. The panel
    # this gate asks for sits under a named destination - Services, Products -
    # so a trigger whose only label is the word Menu does not answer it.
    desktop_triggers = [t for t in PANEL_TRIGGER.finditer(blob)
                        if not MOBILE_ONLY.search(t.group(0))
                        and not GENERIC_MENU_LABEL.match(blob, t.end())]
    has_body = bool(PANEL_BODY.search(blob))

    widest, where = 0, None
    for path, text in sources.items():
        for hm in re.finditer(r"<(?:header|nav)\b[^>]*>", text):
            close = text.find("</header>", hm.end())
            if close < 0:
                close = text.find("</nav>", hm.end())
            if close < 0:
                continue
            block = text[hm.start():close]
            items = re.findall(r"<(?:a|NavLink|Link)\b[^>]*(?:to|href)=[^>]*>", block)
            items = [i for i in items if not re.search(
                r"(sign[- ]?in|log[- ]?in|sign[- ]?up|get started|tel:|mailto:|"
                r"aria-label=[\"\'][^\"\']*(?:home|logo)|Wordmark)", i, re.I)]
            if len(items) > widest:
                widest, where = len(items), "%s:%d" % (
                    Path(path).name, text[:hm.start()].count("\n") + 1)

    if widest < floor:
        return UNKNOWN, ("found %d top-level destination(s), too few to judge; confirm the bar is "
                         "the site's navigation" % widest)
    if desktop_triggers and has_body:
        return PASS, ("the bar opens %d panel(s) across %d top-level destination(s)"
                      % (len(desktop_triggers), widest))
    missing = []
    if not desktop_triggers:
        missing.append("no desktop trigger carries aria-expanded, aria-haspopup, or a data-state")
    if not has_body:
        missing.append("no panel body - no mega menu, flyout, dropdown panel, or role=menu")
    return FAIL, ("the bar lists %d destinations at %s and opens nothing: %s. A row of links is a "
                  "link list; the reference bars put a panel under their top-level words, which is "
                  "the depth surface polish cannot stand in for."
                  % (widest, where or "?", "; ".join(missing)))


# ---------------------------------------------------------------- site logo

# The export gates check a logo when the run is producing one. A website run is
# producing a site, so it never sees them, and a mark with a baked-in white panel
# ships and then sits in a white rectangle on every dark surface the site has.
# The file is right there in the repo either way.

LOGO_NAME = re.compile(r"(^|[/_-])(logo|wordmark|brandmark|brand-mark|logomark|favicon)", re.I)
SVG_FULL_RECT = re.compile(
    r"<rect\b[^>]*\bwidth\s*=\s*[\"\']?(100%|\d{2,})[\"\']?[^>]*>", re.I)
SVG_OPAQUE_FILL = re.compile(r"fill\s*=\s*[\"\']?(?!none|transparent)([^\"\'\s>]+)", re.I)


def _svg_has_opaque_ground(text):
    """A background plate behind the mark, which is the same defect as a flat PNG."""
    if re.search(r"style\s*=\s*[\"\'][^\"\']*background(-color)?\s*:\s*(?!none|transparent)", text, re.I):
        return "a background declared in its style attribute"
    for rect in SVG_FULL_RECT.finditer(text):
        tag = rect.group(0)
        fill = SVG_OPAQUE_FILL.search(tag)
        if fill and fill.group(1).lower() not in ("none", "transparent"):
            return "a full-width <rect> filled %s behind the mark" % fill.group(1)
    return None


def _ico_alpha(raw):
    """Whether an icon file's largest image carries alpha, or None when unreadable.

    An .ico holds a directory of images; each is either a PNG payload or a BMP
    whose bit count says whether the fourth channel is there at all.
    """
    if len(raw) < 22 or raw[:4] != b"\x00\x00\x01\x00":
        return None
    count = int.from_bytes(raw[4:6], "little")
    best = None
    for i in range(count):
        entry = 6 + i * 16
        if entry + 16 > len(raw):
            break
        size = int.from_bytes(raw[entry + 8:entry + 12], "little")
        offset = int.from_bytes(raw[entry + 12:entry + 16], "little")
        width = raw[entry] or 256
        if best is None or width > best[0]:
            best = (width, offset, size)
    if not best or best[1] + 8 > len(raw):
        return None
    payload = raw[best[1]:best[1] + best[2]]
    if payload[:8] == b"\x89PNG\r\n\x1a\n":
        info = png_info_bytes(payload)
        return bool(info and (info["colour"] in (4, 6) or info["trns"]))
    if len(payload) >= 16:
        return int.from_bytes(payload[14:16], "little") == 32
    return None


def check_logo_transparent(gate, sources, run, ctx):
    """Every logo asset the site ships carries no background of its own."""
    named = [f for f in iter_files(run["targets"])
             if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico"}
             and LOGO_NAME.search(f.name)]
    referenced = set()
    for text in sources.values():
        for src in re.findall(r"(?:src|href)\s*=\s*[\"\']([^\"\']+\.(?:png|jpe?g|svg|webp|ico))", text, re.I):
            if LOGO_NAME.search(src):
                referenced.add(Path(src).name)
        for src in re.findall(r"<link\b[^>]*rel\s*=\s*[\"\'][^\"\']*icon[^\"\']*[\"\'][^>]*", text, re.I):
            if re.search(r"apple-touch-icon", src, re.I):
                continue
            href = re.search(r"href\s*=\s*[\"\']([^\"\']+)", src, re.I)
            if href:
                referenced.add(Path(href.group(1)).name)
    for f in iter_files(run["targets"]):
        if f.name in referenced and f not in named:
            named.append(f)
    if not named:
        return UNKNOWN, ("no file named as a logo under the targets - confirm the mark the site "
                         "ships carries no background of its own")

    bad, good = [], []
    for f in named:
        ext = f.suffix.lower()
        if ext in (".jpg", ".jpeg"):
            bad.append("%s is a JPEG, which has no alpha channel at all" % f.name)
            continue
        if ext == ".svg":
            try:
                ground = _svg_has_opaque_ground(f.read_text(errors="replace"))
            except OSError:
                continue
            if ground:
                bad.append("%s carries %s" % (f.name, ground))
            else:
                good.append("%s draws on nothing" % f.name)
            continue
        if ext == ".png":
            info = png_info(f)
            if not info:
                continue
            if info["colour"] not in (4, 6) and not info["trns"]:
                bad.append("%s has no alpha channel" % f.name)
                continue
            corners = png_corner_alpha(info)
            if corners is not None and all(a == 255 for a in corners):
                bad.append("%s has an alpha channel but every corner is opaque" % f.name)
            else:
                good.append("%s is transparent at the corners" % f.name)
            continue
        if ext == ".ico":
            try:
                raw = f.read_bytes()
            except OSError:
                continue
            verdict = _ico_alpha(raw)
            if verdict is True:
                good.append("%s carries alpha" % f.name)
            elif verdict is False:
                bad.append("%s is an icon with no alpha channel" % f.name)
            continue
        try:
            head = f.read_bytes()[:64]
        except OSError:
            continue
        if head[:4] == b"RIFF" and b"WEBP" in head[:16]:
            # VP8X carries the alpha flag in bit 4 of the first byte of its chunk.
            at = head.find(b"VP8X")
            if at >= 0 and len(head) > at + 8 and not (head[at + 8] & 0x10):
                bad.append("%s is a WebP with its alpha flag unset" % f.name)
            else:
                good.append("%s is a WebP carrying alpha" % f.name)
    if bad:
        return FAIL, ("the logo ships with a background of its own: %s. It will sit in a filled "
                      "rectangle on every surface whose colour is not the one baked in."
                      % "; ".join(bad[:5]))
    if good:
        return PASS, "; ".join(good[:5])
    return UNKNOWN, "logo files found but none could be decoded here - confirm transparency by eye"


CODE_BLOCK = re.compile(r"<pre\b|<code\b|CodeBlock|CodeSurface|Snippet|Terminal\b|SyntaxHighlighter", re.I)
SHELL_SHAPED = re.compile(
    r"(?m)^\s*(?:\$\s*)?(?:curl|wget|npm|npx|pnpm|yarn|git|docker|kubectl|brew|apt|pip|python3?|node|sh|bash|zsh|make|cargo|go|sudo)\s+\S"
)
COPY_CONTROL = re.compile(r"clipboard\.writeText|navigator\.clipboard|copy-to-clipboard|useCopy", re.I)
TONE_CLASS = re.compile(r"(?:text-|color:\s*var\(--)[a-z][\w-]*", re.I)
CONFIG_SHAPED = re.compile(r"(?m)^\s*-?\s*[\w.-]+:\s")
HIGHLIGHTER = re.compile(r"shiki|prism|highlight\.js|hljs|SyntaxHighlighter|rehype-pretty", re.I)


def _code_sources(sources):
    """The files that actually render a code block, so a gate answers about
    those and reports nothing about the rest of the run."""
    return {p: t for p, t in sources.items() if CODE_BLOCK.search(t)}


def check_code_surface_drawn(gate, sources, run, ctx):
    hit = _code_sources(sources)
    if not hit:
        return UNKNOWN, "no code or command block found"
    bare = []
    for path, text in hit.items():
        drawn = re.search(r"<svg|SurfaceMark|window-?dots|::before", text, re.I)
        titled = re.search(r"(label|title|filename|caption)\s*[=:]", text, re.I)
        if not drawn and not titled:
            bare.append(Path(path).name)
    if bare:
        return FAIL, "a bare bordered pre is the browser default, not a designed surface: %s" % ", ".join(bare[:6])
    return PASS, "the code surface carries a drawn frame or a titled bar"


def check_code_copyable(gate, sources, run, ctx):
    hit = _code_sources(sources)
    if not hit:
        return UNKNOWN, "no code or command block found"
    runnable = {p: t for p, t in hit.items() if SHELL_SHAPED.search(t)}
    if not runnable:
        return UNKNOWN, "no runnable command found - confirm nothing here is meant to be typed"
    missing = [Path(p).name for p, t in runnable.items() if not COPY_CONTROL.search(t)]
    if missing:
        return FAIL, "commands a reader is meant to run with no copy control, leaving selection as the only way to take them: %s" % ", ".join(missing[:6])
    return PASS, "every runnable block can be copied"


def _tones_in_region(text, region):
    """The tones a code block carries, following the components it renders
    through. A line is usually drawn by a child, so the tones sit in that
    child's body rather than between the pre tags."""
    tones = set(TONE_CLASS.findall(region))
    seen, queue = set(), list(re.findall(r"<([A-Z][A-Za-z0-9]*)\b", region))
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        # A renderer is often chosen rather than named outright, so an alias is
        # followed to the components it stands for.
        alias = re.search(r"(?:const|let)\s+%s\s*=([^\n]*)" % name, text)
        if alias:
            queue.extend(re.findall(r"\b([A-Z][A-Za-z0-9]*)\b", alias.group(1)))
        body = re.search(
            r"(?:function\s+%s\b|(?:const|let)\s+%s\s*=\s*(?:\([^)]*\)|[A-Za-z0-9_]+)\s*=>)(.*?)(?=\n(?:export\s+)?(?:function|const|let)\s|\Z)" % (name, name),
            text,
            re.S,
        )
        if body:
            frag = body.group(1)
            tones |= set(TONE_CLASS.findall(frag))
            queue.extend(re.findall(r"<([A-Z][A-Za-z0-9]*)\b", frag))
    return tones


def _literals(text):
    """Every template literal the file names, so a block can be judged by what
    it is handed rather than by the identifier standing in for it."""
    return {m.group(1): m.group(2) for m in re.finditer(r"const\s+([A-Z][A-Z0-9_]*)\s*=\s*`([^`]*)`", text, re.S)}


def _component_of(text, at):
    """The component a fragment sits in."""
    head = text[:at]
    m = None
    for m in re.finditer(r"(?:function|const)\s+([A-Z][A-Za-z0-9]*)", head):
        pass
    return m.group(1) if m else None


def _component_body(text, name):
    m = re.search(
        r"(?:function\s+%s\b|(?:const|let)\s+%s\s*=)(.*?)(?=\n(?:export\s+)?(?:function|const|let)\s|\Z)" % (name, name),
        text,
        re.S,
    )
    return m.group(1) if m else ""


def _renders_code(text, region):
    """Whether a block is presented as code. A disclosure holding raw text is
    prose however many commands it quotes, and COD-04 answers for it; a block
    on a titled, framed surface is code and answers here."""
    owner = _component_of(text, text.index(region))
    if owner and re.search(r"aria-expanded|<details\b", _component_body(text, owner), re.I):
        return False
    return True


def check_code_tonal(gate, sources, run, ctx):
    hit = _code_sources(sources)
    if not hit:
        return UNKNOWN, "no code or command block found"
    flat = []
    for path, text in hit.items():
        if HIGHLIGHTER.search(text):
            continue
        for m in re.finditer(r"<pre\b.*?</pre>", text, re.S | re.I):
            if not _renders_code(text, m.group(0)):
                continue
            if len(_tones_in_region(text, m.group(0))) < 2:
                flat.append(Path(path).name)
                break
    if flat:
        return FAIL, "code set in one flat colour: nothing separates the command from its arguments (%s)" % ", ".join(flat[:6])
    return PASS, "code is told apart by role"


def check_prompt_not_dumped(gate, sources, run, ctx):
    long_prompts = []
    for path, text in sources.items():
        for m in re.finditer(r"=\s*`([^`]+)`", text, re.S):
            if m.group(1).count("\n") >= 12:
                long_prompts.append((path, Path(path).name))
    if not long_prompts:
        return UNKNOWN, "no long prompt or instruction block found"
    bad = []
    for path, name in long_prompts:
        text = sources[path]
        held = re.search(r"aria-expanded|<details|useState\(\s*false|max-h-|line-clamp", text, re.I)
        if not (COPY_CONTROL.search(text) and held):
            bad.append(name)
    if bad:
        return FAIL, "a long prompt set out in full with no copy control and nothing holding it back reads as a wall nobody crosses: %s" % ", ".join(sorted(set(bad))[:6])
    return PASS, "long prompts are summarised, copyable and held behind a disclosure"


def check_code_no_truncate(gate, sources, run, ctx):
    hit = _code_sources(sources)
    if not hit:
        return UNKNOWN, "no code or command block found"
    bad = []
    for path, text in hit.items():
        for m in re.finditer(r"<pre\b[^>]*", text, re.I):
            tag = m.group(0)
            span = text[m.start():m.start() + 400]
            if re.search(r"whitespace-pre-wrap|break-words|break-all|overflow-x-auto|overflow-auto|overflow-x:\s*auto", tag + span, re.I):
                continue
            if re.search(r"text-overflow|truncate|line-clamp", tag, re.I):
                bad.append(Path(path).name)
                break
            bad.append(Path(path).name)
            break
    if bad:
        return FAIL, "a command that neither wraps nor scrolls loses its tail at the width it is read: %s" % ", ".join(sorted(set(bad))[:6])
    return PASS, "commands wrap or scroll in their own container"


def check_code_labelled(gate, sources, run, ctx):
    hit = _code_sources(sources)
    if not hit:
        return UNKNOWN, "no code or command block found"
    missing = [
        Path(p).name
        for p, t in hit.items()
        if not re.search(r"(label|filename|title|caption)\s*[=:]\s*[\"'{]", t, re.I)
    ]
    if missing:
        return FAIL, "a code block that does not say whether it is a shell, a file, or which file, leaves the reader to guess where it goes: %s" % ", ".join(missing[:6])
    return PASS, "each block names what it is"


def check_code_graphic_keeps_text(gate, sources, run, ctx):
    drawn = {}
    for path, text in sources.items():
        for m in re.finditer(r"<svg\b.*?</svg>", text, re.S | re.I):
            if re.search(r"<text\b", m.group(0), re.I) and SHELL_SHAPED.search(m.group(0)):
                drawn[path] = m.group(0)
    if not drawn:
        return UNKNOWN, "no code drawn as a graphic"
    bad = [
        Path(p).name
        for p, frag in drawn.items()
        if not COPY_CONTROL.search(sources[p]) and not re.search(r"role=[\"']img|aria-label", frag, re.I)
    ]
    if bad:
        return FAIL, "code drawn as a graphic with no way to copy it and nothing for a screen reader: %s" % ", ".join(bad[:6])
    return PASS, "drawn code keeps its text reachable"


def check_mono_face_named(gate, sources, run, ctx):
    blob = "\n".join(sources.values())
    decls = re.findall(r"font-family\s*:\s*([^;}\n]+)", blob, re.I)
    mono = [d for d in decls if "mono" in d.lower()]
    if not mono:
        if re.search(r"font-mono\b", blob):
            return UNKNOWN, "a mono utility is used - confirm the theme names a face behind it"
        return UNKNOWN, "no monospace family declared"
    bare = [d.strip() for d in mono if re.fullmatch(r"\s*(ui-)?monospace\s*", d, re.I)]
    if bare:
        return FAIL, "monospace with no named face renders as whatever the machine happens to ship"
    return PASS, "the monospace stack names a face"


CHECKS = {
    "manual": None,
    "code_surface_drawn": check_code_surface_drawn,
    "code_copyable": check_code_copyable,
    "code_tonal": check_code_tonal,
    "prompt_not_dumped": check_prompt_not_dumped,
    "code_no_truncate": check_code_no_truncate,
    "code_labelled": check_code_labelled,
    "code_graphic_keeps_text": check_code_graphic_keeps_text,
    "mono_face_named": check_mono_face_named,
    "forbid_regex": check_forbid_regex,
    "require_regex": check_require_regex,
    "require_any_regex": check_require_any_regex,
    "require_file": check_require_file,
    "require_regex_if": check_require_regex_if,
    "forbid_regex_outside_tokens": check_forbid_regex_outside_tokens,
    "forbid_regex_in_state": check_forbid_regex_in_state,
    "forbid_emoji": check_forbid_emoji,
    "active_scale_range": check_active_scale_range,
    "hover_effect_stack": check_hover_effect_stack,
    "overshoot_bezier": check_overshoot_bezier,
    "animated_layout_property": check_animated_layout_property,
    "duration_cap": check_duration_cap,
    "state_coverage": check_state_coverage,
    "outline_none_orphan": check_outline_none_orphan,
    "div_onclick": check_div_onclick,
    "measure_range": check_measure_range,
    "font_family_count": check_font_family_count,
    "italic_heading": check_italic_heading,
    "invented_metric": check_invented_metric,
    "grid_minmax": check_grid_minmax,
    "overflow_clip": check_overflow_clip,
    "sticky_top_zero": check_sticky_top_zero,
    "svg_aria": check_svg_aria,
    "img_alt": check_img_alt,
    "img_dimensions": check_img_dimensions,
    "img_lazy": check_img_lazy,
    "icon_button_label": check_icon_button_label,
    "form_label": check_form_label,
    "input_types": check_input_types,
    "autocomplete_attr": check_autocomplete_attr,
    "heading_order": check_heading_order,
    "viewport_meta": check_viewport_meta,
    "min_font_size": check_min_font_size,
    "line_height_range": check_line_height_range,
    "zindex_scale": check_zindex_scale,
    "scrolled_bar": check_scrolled_bar,
    "toast_channel": check_toast_channel,
    "font_display": check_font_display,
    "spacing_scale": check_spacing_scale,
    "icon_library_mix": check_icon_library_mix,
    "label_case": check_label_case,
    "svg_currentcolor": check_svg_currentcolor,
    "png_transparent": check_png_transparent,
    "logo_transparent": check_logo_transparent,
    "png_dimensions": check_png_dimensions,
    "scope_coverage": check_scope_coverage,
    "file_coverage": check_file_coverage,
    "cohesion_values": check_cohesion_values,
    "cohesion_component": check_cohesion_component,
    "cohesion_duplicate_components": check_cohesion_duplicate_components,
    "cohesion_status_tokens": check_cohesion_status_tokens,
    "cohesion_ran": check_cohesion_ran,
    "interactive_coverage": check_interactive_coverage,
    "click_effect": check_click_effect,
    "click_effect_safe": check_click_effect_safe,
    "scroll_to_top": check_scroll_to_top,
    "motion_coverage": check_motion_coverage,
    "display_wrap_coverage": check_display_wrap_coverage,
    "spacing_probe": check_spacing_probe,
    "spacing_tiers": check_spacing_tiers,
    "stack_gap": check_stack_gap,
    "conditional_margin": check_conditional_margin,
    "field_surface": check_field_surface,
    "field_font_size": check_field_font_size,
    "placeholder_only_label": check_placeholder_only_label,
    "control_height_token": check_control_height_token,
    "aria_invalid": check_aria_invalid,
    "select_appearance": check_select_appearance,
    "menu_keyboard": check_menu_keyboard,
    "menu_clipping": check_menu_clipping,
    "menu_scroll": check_menu_scroll,
    "menu_surface": check_menu_surface,
    "menu_zindex": check_menu_zindex,
    "option_states": check_option_states,
    "picker_indicator": check_picker_indicator,
    "accent_color": check_accent_color,
    "autofill_surface": check_autofill_surface,
    "row_hover": check_row_hover,
    "clickable_row": check_clickable_row,
    "sticky_thead": check_sticky_thead,
    "tabular_nums": check_tabular_nums,
    "row_separator": check_row_separator,
    "aria_sort": check_aria_sort,
    "table_overflow": check_table_overflow,
    "hover_only_actions": check_hover_only_actions,
    "table_states": check_table_states,
    "semantic_table": check_semantic_table,
    "sticky_header_surface": check_sticky_header_surface,
    "nav_two_states": check_nav_two_states,
    "nav_disclosure_trigger": check_nav_disclosure_trigger,
    "nav_primary_action": check_nav_primary_action,
    "nav_hide_on_scroll": check_nav_hide_on_scroll,
    "nav_panel_seam": check_nav_panel_seam,
    "nav_top_level_count": check_nav_top_level_count,
    "nav_panel_directory": check_nav_panel_directory,
    "nav_ai_default": check_nav_ai_default,
    "nav_has_panel": check_nav_has_panel,
    "sticky_offset": check_sticky_offset,
    "header_height_token": check_header_height_token,
    "surface_hover": check_surface_hover,
    "global_user_select": check_global_user_select,
    "skeleton_probe": check_skeleton_probe,
    "resolve_together": check_resolve_together,
    "content_entrance": check_content_entrance,
    "skeleton_presence": check_skeleton_presence,
    "skeleton_tokens": check_skeleton_tokens,
    "skeleton_motion": check_skeleton_motion,
    "skeleton_reduced_motion": check_skeleton_reduced_motion,
    "skeleton_aria": check_skeleton_aria,
    "skeleton_primitive_count": check_skeleton_primitive_count,
    "skeleton_late_widgets": check_skeleton_late_widgets,
    "skeleton_image_placeholder": check_skeleton_image_placeholder,
    "run_opened": check_run_opened,
    "all_resolved": check_all_resolved,
    "report_emitted": check_report_emitted,
    "writing_pass": check_writing_pass,
    "eyebrow_density": check_eyebrow_density,
    "layout_family_run": check_layout_family_run,
    "layout_family_diversity": check_layout_family_diversity,
    "marquee_count": check_marquee_count,
    "hero_top_padding": check_hero_top_padding,
    "hero_stack_count": check_hero_stack_count,
    "trust_strip_placement": check_trust_strip_placement,
    "hero_forbid_regex": check_hero_forbid_regex,
    "grid_cell_fill": check_grid_cell_fill,
    "long_list_component": check_long_list_component,
    "spec_list_hairlines": check_spec_list_hairlines,
    "comparison_track_bar": check_comparison_track_bar,
    "separator_density": check_separator_density,
    "decorative_dots": check_decorative_dots,
    "image_overlay_label": check_image_overlay_label,
    "accent_consistency": check_accent_consistency,
    "theme_lock": check_theme_lock,
    "neon_glow": check_neon_glow,
    "visible_string_scan": check_visible_string_scan,
    "straight_quotes": check_straight_quotes,
    "cta_intent_dedupe": check_cta_intent_dedupe,
    "copy_density": check_copy_density,
    "quote_shape": check_quote_shape,
    "image_presence": check_image_presence,
    "logo_wall_marks": check_logo_wall_marks,
    "logo_wall_labels": check_logo_wall_labels,
    "handrolled_svg": check_handrolled_svg,
    "react_continuous_state": check_react_continuous_state,
    "effect_cleanup": check_effect_cleanup,
    "raf_state": check_raf_state,
    "client_leaf_motion": check_client_leaf_motion,
    "grain_overlay": check_grain_overlay,
    "dependency_verified": check_dependency_verified,
    "design_system_mix": check_design_system_mix,
    "layout_probe": check_layout_probe,
    "mobile_probe": check_mobile_probe,
    "mobile_nav_reach": check_mobile_nav_reach,
    "menu_viewport_unit": check_menu_viewport_unit,
    "unprompted_motion": check_unprompted_motion,
    "menu_scrolls": check_menu_scrolls,
    "safe_area": check_safe_area,
    "bottom_rail": check_bottom_rail,
    "rail_reserve": check_rail_reserve,
    "panel_opaque": check_panel_opaque,
    "top_rail": check_top_rail,
    "drawn_plates": check_drawn_plates,
    "plate_per_row": check_plate_per_row,
    "plate_cohesion": check_plate_cohesion,
    "plate_depth": check_plate_depth,
    "plate_ground": check_plate_ground,
    "svg_transparent": check_svg_transparent,
    "plate_scene": check_plate_scene,
    "plate_scale": check_plate_scale,
    "design_read": check_design_read,
    "genre_rotation": check_genre_rotation,
    "archetype_stamp": check_archetype_stamp,
    "archetype_not_default": check_archetype_not_default,
    "archetype_rotation": check_archetype_rotation,
}


# ---------------------------------------------------------------- parallel sweep

# Reading is I/O and the checks are regex over the whole source bundle, so the two
# halves want different machinery: threads for the reads, processes for the checks,
# because a regex holds the interpreter lock for its whole run and threads would
# take the same wall-clock as one.
#
# A worker that dies, or a platform that cannot fork, falls back to the serial
# sweep. A check whose verdict cannot be obtained is inconclusive and never a pass,
# so the fallback costs time and nothing else.

_WORK = {}


def _pool_init(sources, run, ctx):
    _WORK["sources"] = sources
    _WORK["run"] = run
    _WORK["ctx"] = ctx


def _pool_run(gate):
    fn = CHECKS.get(gate["check"]["type"])
    if fn is None:
        return gate["id"], None
    try:
        return gate["id"], fn(gate, _WORK["sources"], _WORK["run"], _WORK["ctx"])
    except Exception as exc:
        return gate["id"], (UNKNOWN, "the check raised %s: %s" % (type(exc).__name__, exc))


# Below this the pool costs more to start than the sweep costs to run.
PARALLEL_FLOOR = 40


def sweep(gates, sources, run, ctx):
    """Every gate's automated verdict, keyed by id, computed in parallel when it pays."""
    work = [g for g in gates if CHECKS.get(g["check"]["type"], "missing") is not None]
    if len(work) < PARALLEL_FLOOR or os.environ.get("INTELLIGENCE_NO_PARALLEL"):
        return {g["id"]: _serial_one(g, sources, run, ctx) for g in work}
    try:
        import concurrent.futures as futures
        workers = min(8, (os.cpu_count() or 2))
        with futures.ProcessPoolExecutor(
                max_workers=workers, initializer=_pool_init,
                initargs=(sources, run, ctx)) as pool:
            return dict(pool.map(_pool_run, work, chunksize=4))
    except Exception:
        return {g["id"]: _serial_one(g, sources, run, ctx) for g in work}


def _serial_one(gate, sources, run, ctx):
    fn = CHECKS.get(gate["check"]["type"])
    if fn is None:
        return None
    try:
        return fn(gate, sources, run, ctx)
    except Exception as exc:
        return UNKNOWN, "the check raised %s: %s" % (type(exc).__name__, exc)


# ---------------------------------------------------------------- run state


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _run_at(pointer):
    """The run a pointer names, or None when the pointer is missing or stale."""
    if not pointer.exists():
        return None
    try:
        path = RUNS / ("%s.json" % json.loads(pointer.read_text())["id"])
        return json.loads(path.read_text())
    except (ValueError, OSError, KeyError):
        return None


def load_run(required=True, session=None, own_only=False, here=None):
    """The run this call should act on.

    The session's own pointer first. Failing that, the shared one -- but only
    when the run it names has no owner, which means a run from an older build
    or one started by hand. Adopting a run another session owns is how one
    session's design work ends up recorded against another's checklist.

    `own_only` drops the fallback entirely, for the Stop guard, which must
    answer for this session and nothing else.

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
        raise SystemExit("No design run is open. Start one:\n  design-pass.py start --kind web-ui --target <path>")
    return run


def save_run(run):
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / ("%s.json" % run["id"])).write_text(json.dumps(run, indent=2))
    pointer = json.dumps({"id": run["id"]})
    claim_shared(run, pointer)
    owner = run.get("session")
    if owner:
        pointer_for(owner, run_target(run)).write_text(pointer)


# These three describe the run rather than the deliverable, so the tool settles them
# from its own state. Left resolvable by hand they would be the way around everything
# else: mark "every gate answered" as n/a and the report requirement evaporates.
SELF_SETTLING = {"run_opened", "all_resolved", "report_emitted", "file_coverage"}

# Checks that ask whether a required piece of the bar exists at all. A dispute
# says the tool measured wrongly, and for these the only thing measured is
# whether the thing was built, so a dispute is just a way of not building it.
# Every one of them failed on a site whose bar was a hamburger on desktop and
# was closed as disputed. The way past these is the code.
UNDISPUTABLE = {"nav_has_panel", "nav_panel_directory", "nav_disclosure_trigger",
                "archetype_stamp", "archetype_not_default", "archetype_rotation",
                "panel_opaque"}


# ---------------------------------------------------------------- gate cache

# Check types that read something a source hash cannot capture. Cross-file gates
# read the shape of the whole target and re-derive on every source change, so
# their result under one source snapshot is not evidence for a later one, even
# when the individual file that changed was not the one that flipped the verdict.
# Runtime-measured gates read a browser capture and answer to what the DOM
# actually did, which no file hash can prove.
UNCACHEABLE_CHECKS = {
    "cohesion_component", "cohesion_duplicate_components", "cohesion_ran",
    "cohesion_status_tokens", "cohesion_values",
    "spacing_probe", "spacing_scale", "spacing_tiers",
    "skeleton_probe", "skeleton_aria", "skeleton_image_placeholder",
    "skeleton_late_widgets", "skeleton_motion", "skeleton_presence",
    "skeleton_primitive_count", "skeleton_reduced_motion", "skeleton_tokens",
    "file_coverage", "report_emitted", "run_opened", "all_resolved",
    "resolve_together", "scope_coverage", "interactive_coverage",
    "state_coverage", "motion_coverage", "display_wrap_coverage",
    # These read the run, the machine, or the asset tree rather than the sources,
    # so a source hash is not evidence that their answer still holds.
    "layout_probe", "mobile_probe", "design_read", "genre_rotation", "archetype_rotation",
    "drawn_plates", "plate_cohesion", "plate_depth", "plate_ground",
    "plate_scene", "plate_scale", "svg_transparent",
    "dependency_verified",
    "design_system_mix", "image_presence", "handrolled_svg", "client_leaf_motion",
}


# A gate whose verdict is a measurement of the rendered page needs an instrument to
# take it, and on a machine with no rendering available there is no honest verdict to
# give. It must not read as a pass, and it cannot be disputed either, because the
# check is not wrong - only unread. So it is answered `unmeasured`: a status that
# closes the run, names the instrument that was missing, and reports in its own
# section instead of folding into a count. The carve-out is deliberately narrow: a
# gate whose capture was taken and which then failed is not unmeasured, and answering
# it that way is refused.
CAPTURE_KEY = {"spacing_probe": "spacing", "spacing_tiers": "spacing",
               "layout_probe": "layout", "skeleton_probe": "probe",
               "mobile_probe": "mobile"}


NEVER_CARRIED = set(UNCACHEABLE_CHECKS)


def _uninstrumented(gate, run):
    """Whether this gate needs a rendered capture that this run does not have."""
    key = CAPTURE_KEY.get(gate["check"]["type"])
    return bool(key) and not run.get(key)


def settle_self(run, index):
    """Set the bookkeeping gates from the run's state, not from an attestation."""
    others = [g for g in run["order"]
              if index[g]["check"]["type"] not in SELF_SETTLING]
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
            outstanding = [p for p, r in files.items() if r["status"] == "open"]
            # A target that no longer exists - a worktree removed once its branch
            # landed - leaves a ledger that cannot be rebuilt. The work is done and
            # the gate settles on that, rather than holding the run open forever.
            gone = all(not Path(t).exists() for t in run.get("targets", [])) if run.get("targets") else False
            ok = (bool(files) or gone) and not outstanding
            note = ("the run's targets no longer exist; every file was ruled on before they went" if ok and gone
                    else "all %d file(s) read and ruled on" % len(files) if ok
                    else "%d of %d file(s) not ruled on" % (len(outstanding), len(files)))
        else:
            ok = bool(run.get("report_at"))
            note = ("report rendered %s" % run["report_at"] if ok else "no report rendered yet")
        result["status"] = "pass" if ok else "open"
        result["note"] = note
        result["auto"] = note
        result["at"] = now() if ok else ""


def refresh(run):
    """Recompute which gates apply and seed a result slot for each."""
    groups = load_gates()
    ctx, sources = build_context(run)
    run["context"] = sorted(k for k, v in ctx.items() if v)
    results = run.setdefault("results", {})
    order = []
    for gate in all_gates(groups):
        ok, why = applicable(gate, run, ctx)
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
    return groups, ctx, sources


def gate_index(groups):
    return {g["id"]: g for g in all_gates(groups)}


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


def cmd_start(argv):
    flags = parse_flags(argv)
    kind = (flags.get("kind") or ["web-ui"])[0]
    if kind not in KINDS:
        raise SystemExit("kind must be one of: %s" % ", ".join(KINDS))
    scope = (flags.get("scope") or [None])[0]
    if scope and scope not in SCOPES:
        raise SystemExit("scope must be one of: %s" % ", ".join(sorted(SCOPES)))
    here = os.getcwd()
    targets = flags.get("target") or [str(project_root(here) or here)]
    refuse_foreign_cwd(targets)
    run = {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session_id(),
        "kind": kind,
        "extra_kinds": flags.get("also", []),
        "targets": [str(Path(t).expanduser().resolve()) for t in targets],
        "repo": target_root(targets) or repo_root(),
        "title": " ".join(flags.get("title", [])) or "design run",
        "flags": flags.get("flag", []),
        "scope": (flags.get("scope") or [None])[0],
        "results": {},
        "auto_scoped": False,
    }
    if "--fresh" in argv or "--no-cache" in argv:
        run["no_cache"] = True
    groups, ctx, _ = refresh(run)
    save_run(run)
    print_checklist(run, groups, header="Design run %s opened - %s" % (run["id"], run["title"]))
    handed = carried_summary(run)
    if handed:
        print("\nCarried from an earlier run, unchanged since: %s.\n"
              "Re-open them with --fresh, or with INTELLIGENCE_PASS_NO_CACHE=1." % handed)
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
    if flags.get("sizes"):
        run["sizes"] = json.loads(" ".join(flags["sizes"]))
    run["auto_scoped"] = True
    groups, ctx, _ = refresh(run)
    save_run(run)
    print_checklist(run, groups, header="Run %s rescoped" % run["id"])
    return 0


def cmd_verify(argv):
    no_cache = "--no-cache" in argv or "--fresh" in argv
    run = load_run()
    if no_cache:
        run["no_cache"] = True
    groups, ctx, sources = refresh(run)
    index = gate_index(groups)
    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0, "manual": 0}
    cached_hits = 0
    lines = []
    to_sweep = []
    for gid in run["order"]:
        gate = index[gid]
        fn = CHECKS.get(gate["check"]["type"], "missing")
        if fn is None or fn == "missing":
            continue
        if run["results"][gid].get("status") != "fixed" \
                and carry_gate_answer(run, gate):
            continue
        to_sweep.append(gate)
    verdicts = sweep(to_sweep, sources, run, ctx)
    for gid in run["order"]:
        gate = index[gid]
        fn = CHECKS.get(gate["check"]["type"], "missing")
        if fn is None:
            hit = carry_gate_answer(run, gate, hand=True)
            if hit and run["results"][gid].get("status") == "open":
                run["results"][gid].update({
                    "status": hit["status"], "by": "carried", "at": now(),
                    "note": "carried from run %s: %s" % (hit.get("run") or "?",
                                                         hit.get("note") or ""),
                    "auto": "carried: the gate and the project are unchanged since"})
                cached_hits += 1
                lines.append("  ok %-9s %s   [carried]" % (gid, gate["title"]))
                continue
            counts["manual"] += 1
            continue
        if fn == "missing":
            run["results"][gid]["auto"] = "no checker implemented for %s" % gate["check"]["type"]
            counts[UNKNOWN] += 1
            lines.append("  ?  %-9s %s - checker not implemented, answer by hand" % (gid, gate["title"]))
            continue
        result = run["results"][gid]
        hit = carry_gate_answer(run, gate) if result.get("status") != "fixed" else None
        if hit:
            if hit["status"] == "pass":
                counts[PASS] += 1
            cached_hits += 1
            result.update({
                "status": hit["status"], "at": now(),
                "note": "carried from run %s: %s" % (hit.get("run") or "?",
                                                     hit.get("note") or ""),
                "auto": "carried: the gate, this tool and the project are unchanged since"})
            lines.append("  ok %-9s %s   [carried]" % (gid, gate["title"]))
            continue
        got = verdicts.get(gid)
        if got is None:
            # Outside the swept set: recompute alone rather than read as a pass.
            got = _serial_one(gate, sources, run, ctx)
        status, detail = got if got else (UNKNOWN, "no verdict was produced")
        result["auto"] = "%s: %s" % (status, detail)
        counts[status] += 1
        if result.get("status") == "fixed" and status == FAIL:
            result["status"] = "open"
            lines.append("  X  %-9s %s\n       marked fixed, but the sweep still fails: %s"
                         % (gid, gate["title"], detail))
            continue
        if result.get("by") == "hand" and result.get("status") != "fixed" and status != PASS:
            lines.append("  ok %-9s %s\n       answered by hand: %s\n       sweep still says: %s"
                         % (gid, gate["title"], result["note"], detail))
            continue
        if status == PASS:
            # A hand answer carries evidence somebody gathered; the sweep's own
            # line is a summary. Overwriting one with the other loses the
            # reasoning and leaves the ledger saying less than it did.
            if result.get("by") == "hand" and result.get("note"):
                result["status"] = result.get("status") or "pass"
                result["swept"] = detail
            else:
                result["status"] = "pass"
                result["note"] = detail
            result["at"] = now()
            lines.append("  ok %-9s %s" % (gid, gate["title"]))
        elif status == FAIL:
            result["status"] = "open"
            lines.append("  X  %-9s %s\n       %s" % (gid, gate["title"], detail))
        else:
            result["status"] = "open"
            lines.append("  ?  %-9s %s\n       %s" % (gid, gate["title"], detail))
    save_run(run)
    persist_cache(run, index)
    print("Automated sweep over %d applicable gate(s):\n" % len(run["order"]))
    print("\n".join(lines) or "  (nothing automatable in scope)")
    print("\n  %d passed, %d failed, %d inconclusive, %d manual-only." %
          (counts[PASS], counts[FAIL], counts[UNKNOWN], counts["manual"]))
    if cached_hits:
        print("  %d answer(s) were carried from an earlier run: the gate, this tool\n"
              "  and every file in scope are unchanged since each was given." % cached_hits)
    print("  Failed and inconclusive gates stay open: fix them, or answer with evidence.")
    return 0


TARGET_NOUN = "deliverable"


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
    if status not in ("pass", "fixed", "na", "disputed", "unmeasured"):
        faults.append("status must be pass, fixed, na, disputed, or unmeasured, not %r" % status)
    # A note is a record, not a check. What decides a gate is the sweep, so an answer
    # stands or falls on its status and the run's own state; prose alongside it
    # changes nothing and asking for it only costs a round trip.
    if gid not in run["results"]:
        faults.append("not applicable to this run")
        return faults
    gate = index[gid]
    uninstrumented = _uninstrumented(gate, run)
    if gate["check"]["type"] in SELF_SETTLING and not (status == "unmeasured" and uninstrumented):
        faults.append("answers to the run's own state, not to an attestation")
    if status == "unmeasured" and not uninstrumented:
        faults.append("unmeasured is only for a gate whose reading needs a capture this run "
                      "does not have. This one is either not a measured gate or its capture "
                      "was taken, so it has a verdict already - fix it, or dispute it")
    if status == "unmeasured" and len(note.strip()) < 40:
        faults.append("recording a gate as unmeasured takes the reason: which instrument was "
                      "missing, what it would have read, and why it could not be run")
    swept = str(run["results"][gid].get("auto", ""))
    failing = swept.startswith("fail")
    if status == "pass" and failing:
        faults.append("the sweep found this failing, so it cannot be answered as passing: %s"
                      % swept[:120])
    # A failing gate closed as not-applicable is the escape hatch that makes the
    # whole checklist advisory: the sweep just proved the condition is present and
    # unmet. Fixing it, or disputing the measurement on the record, are the ways out.
    if status == "na" and failing and gate["check"]["type"] in UNDISPUTABLE:
        faults.append("the sweep found this failing, so it is present and unmet, not inapplicable. "
                      "Build it into the %s: %s" % (TARGET_NOUN, swept[:100]))
    elif status == "na" and failing:
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
    if status == "disputed" and gate["check"]["type"] in UNDISPUTABLE:
        faults.append("this gate asks whether the bar has the piece at all, and the sweep found "
                      "it missing. It cannot be disputed - build it: %s" % swept[:120])
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
        persist_cache(run)
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
    ids, status, note = [], "pass", ""
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--status":
            status = argv[i + 1]; i += 2
        elif token == "--note":
            note = argv[i + 1]; i += 2
        else:
            ids.append(token); i += 1
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
    persist_cache(run)
    print("Answered %d gate(s) as %s: %s" % (len(ids), status, ", ".join(ids)))
    remaining = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    print("%d gate(s) still open." % len(remaining))
    return 0


def cmd_status(argv):
    flags = parse_flags(argv)
    run = load_run()
    groups, _, _ = refresh(run)
    index = gate_index(groups)
    save_run(run)
    # The bookkeeping gates are listed apart from the answerable ones. Mixed in, a
    # batch built from this output would include them and be refused wholesale.
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"
                and index[g]["check"]["type"] not in SELF_SETTLING]
    pending = [g for g in run["order"] if run["results"][g]["status"] == "open"
               and index[g]["check"]["type"] in SELF_SETTLING]
    line = scope_line(run)
    if line:
        print(line)
    print("Run %s - %s (%s)" % (run["id"], run["title"], run["kind"]))
    print("Targets: %s" % ", ".join(run["targets"]))
    print("%d applicable gate(s), %d to answer.\n" % (len(run["order"]), len(open_ids)))
    full = "full" in flags
    for gid in open_ids:
        gate = index[gid]
        print("  %-9s [%s] %s" % (gid, gate["severity"], gate["title"]))
        if run["results"][gid]["auto"]:
            print("             %s" % run["results"][gid]["auto"])
        if full:
            print("             rule: %s" % gate["rule"])
            print("             fix:  %s" % gate.get("fix", ""))
    if pending:
        print("\nSettled by the run itself once the rest is done: %s" % ", ".join(pending))
    if open_ids:
        # The cost of a run is round trips rather than checks: the sweep settles
        # what it can in seconds, and the rest is answered in one call carrying a
        # status and a note per gate.
        print("\nAnswer them in one call - each gate keeps its own status and its own note:\n"
              "  %s resolve --batch - <<'JSON'\n"
              "  [{\"id\": \"%s\", \"status\": \"fixed\", \"note\": \"what changed\"}]\n"
              "  JSON" % (TOOL, open_ids[0]))
    return 0


def print_checklist(run, groups, header=""):
    index = gate_index(groups)
    if header:
        print(header)
    line = scope_line(run)
    if line:
        print(line)
    print("Kind: %s   Targets: %s" % (run["kind"], ", ".join(run["targets"])))
    print("Detected: %s\n" % ", ".join(c for c in run["context"] if c != "always"))
    current = None
    for gid in run["order"]:
        gate = index[gid]
        if gate["group"] != current:
            current = gate["group"]
            print("\n%s" % current.upper())
        auto = "auto" if gate["check"]["type"] != "manual" else "    "
        print("  [ ] %-9s %s %s" % (gid, auto, gate["title"]))
    print("\n%d gate(s) apply to this run. Run verify first, then answer what it could not settle." % len(run["order"]))


def cmd_report(argv):
    run = load_run()
    groups, _, _ = refresh(run)
    index = gate_index(groups)
    flags = parse_flags(argv)
    # The report gate cannot wait for itself: stamp it once everything describing the
    # deliverable is answered, then let the bookkeeping gates catch up before the
    # table is built, so the rendered list shows their real state.
    outstanding = [g for g in run["order"]
                   if run["results"][g]["status"] == "open"
                   and index[g]["check"]["type"] not in SELF_SETTLING]
    if not outstanding:
        run["report_at"] = run.get("report_at") or now()
        settle_self(run, index)
        save_run(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    body = []
    body.append("# Design checklist - %s" % run["title"])
    body.append("")
    body.append("Run `%s`, kind `%s`, opened %s." % (run["id"], run["kind"], run["opened"]))
    body.append("")
    body.append("Targets: %s" % ", ".join("`%s`" % t for t in run["targets"]))
    body.append("")
    line = scope_line(run)
    if line:
        body.append("> **%s**" % line)
        body.append("")
    fixed = [g for g in run["order"] if run["results"][g]["status"] == "fixed"]
    if fixed:
        body.append("## Changed")
        body.append("")
        for gid in fixed:
            body.append("- **%s %s** - %s" % (gid, index[gid]["title"], run["results"][gid]["note"]))
        body.append("")
    else:
        body.append("Nothing was changed in this run.")
        body.append("")
    tally = {}
    for gid in run["order"]:
        tally[run["results"][gid]["status"]] = tally.get(run["results"][gid]["status"], 0) + 1
    body.append("%d gates applied: %s." % (
        len(run["order"]),
        ", ".join("%d %s" % (n, {"pass": "passed", "fixed": "fixed", "na": "not applicable",
                                  "disputed": "DISPUTED", "unmeasured": "UNMEASURED",
                                  "open": "OPEN"}.get(s, s))
                  for s, n in sorted(tally.items()))))
    body.append("")
    # A disputed gate is a rule the run says the tool measured wrongly. It closes
    # so one bad check cannot jam a build, and it leads the report so the claim is
    # read rather than absorbed: every one is either a defect in the check or a
    # rule that quietly went unenforced.
    disputed = [g for g in run["order"] if run["results"][g]["status"] == "disputed"]
    if disputed:
        body.append("### Disputed - the check was answered as wrong (%d)" % len(disputed))
        body.append("")
        body.append("Each of these is a rule that did not get enforced this run. Read them first.")
        body.append("")
        for gid in disputed:
            body.append("- **%s %s**" % (gid, index[gid]["title"]))
            body.append("  - sweep said: %s" % run["results"][gid].get("auto", "")[:200])
            body.append("  - the run's measurement: %s" % run["results"][gid]["note"])
        body.append("")
    # Unmeasured gates are the ones this run had no instrument to read. They sit
    # beside the disputed ones rather than inside a tally, because both describe a
    # rule that went unenforced and both are worth reading before the pass is
    # believed. The difference is only whose fault it is: a dispute says the check is
    # wrong, an unmeasured says nothing looked.
    unmeasured = [g for g in run["order"] if run["results"][g]["status"] == "unmeasured"]
    if unmeasured:
        body.append("### Unmeasured - no instrument to take the reading (%d)" % len(unmeasured))
        body.append("")
        body.append("Each of these judges the rendered page, and this run had no rendering to "
                    "read. None of them is a pass: they are rules this run did not enforce.")
        body.append("")
        for gid in unmeasured:
            body.append("- **%s %s**" % (gid, index[gid]["title"]))
            body.append("  - sweep said: %s" % run["results"][gid].get("auto", "")[:200])
            body.append("  - why it went unread: %s" % run["results"][gid]["note"])
        body.append("")
    # Anything answered n/a is listed up front rather than buried in its group. A
    # gate only appears in a run because its condition was found in the target, so
    # every n/a is a claim that the tool read the target wrong - and that claim
    # should be the easiest thing on the page to check.
    skipped = [g for g in run["order"] if run["results"][g]["status"] == "na"]
    if skipped:
        body.append("### Answered not applicable (%d)" % len(skipped))
        body.append("")
        for gid in skipped:
            body.append("- **%s %s** - %s" % (gid, index[gid]["title"],
                                              run["results"][gid]["note"] or "no reason given"))
        body.append("")
    files = run.get("files") or {}
    if files:
        changed = [p for p, r in files.items() if r["status"] == "changed"]
        cleared = [p for p, r in files.items() if r["status"] == "clear"]
        body.append("### Files (%d in scope, %d excluded by type)" % (len(files), len(run.get("excluded", []))))
        body.append("")
        body.append("| File | Verdict | On what |")
        body.append("| --- | --- | --- |")
        for path in sorted(files):
            record = files[path]
            body.append("| `%s` | %s | %s |" % (
                path, {"changed": "changed", "clear": "no change needed", "open": "OPEN"}[record["status"]],
                (record["note"] or "").replace("|", "/")[:220]))
        body.append("")
        body.append("%d changed, %d read and left alone." % (len(changed), len(cleared)))
        body.append("")
    current = None
    for gid in run["order"]:
        gate, result = index[gid], run["results"][gid]
        if gate["group"] != current:
            current = gate["group"]
            body.append("")
            body.append("## %s" % current)
            body.append("")
            body.append("| Gate | Check | Status | How it was answered |")
            body.append("| --- | --- | --- | --- |")
        mark = {"pass": "pass", "fixed": "fixed", "na": "n/a", "disputed": "DISPUTED",
                "unmeasured": "UNMEASURED", "open": "OPEN"}.get(result["status"], result["status"])
        note = (result["note"] or result["auto"] or "").replace("|", "/").replace("\n", " ")
        body.append("| %s | %s | %s | %s |" % (gid, gate["title"], mark, note[:300]))
    text = "\n".join(body) + "\n"
    out = Path((flags.get("out") or [str(RUNS / ("%s-report.md" % run["id"]))])[0])
    out.write_text(text)
    print(text)
    print("\nWritten to %s" % out)
    if open_ids:
        print("\n%d gate(s) are still OPEN - the run cannot close until they are answered." % len(open_ids))
        return 1
    return 0


def cmd_finish(argv):
    run = load_run()
    flags = parse_flags(argv)
    if "no-deliverable" in flags:
        # Files that no longer exist shipped nothing, so there is nothing left to
        # check. Without this the escape hatch is unavailable for work that was
        # thrown away, and the only way out of a scratch run is to answer a
        # checklist about files that are gone. A file whose content is back to
        # what git has shipped nothing either: the run opens on the path being
        # edited, not on the edit changing anything a viewer could see, so a
        # comment rewritten inside a stylesheet or an index.html and then
        # reverted would otherwise hold the run open over a whole site.
        touched = [t for t in (run.get("touched") or []) if Path(t).exists()
                   and differs_from_git(Path(t))]
        if touched:
            shown = ", ".join(Path(t).name for t in touched[:8])
            print("Cannot close as no-deliverable: this run edited %d design file(s) - %s.\n"
                  "Work the checklist and report on it instead."
                  % (len(touched), shown), file=sys.stderr)
            return 1
        note = " ".join(flags.get("note", [])) or "no design deliverable was produced"
        run["closed"] = now()
        run["closed_reason"] = note
        save_run(run)
        clear_pointers(run)
        print("Run %s closed with no deliverable: %s" % (run["id"], note))
        return 0
    groups, _, _ = refresh(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    if open_ids:
        save_run(run)
        print("Cannot close: %d gate(s) unanswered - %s" % (len(open_ids), ", ".join(open_ids[:12])), file=sys.stderr)
        return 1
    run["closed"] = now()
    save_run(run)
    persist_cache(run)
    # Recorded on the way out rather than at the read, so a run that was abandoned
    # before it shipped anything does not become the direction the next one has to
    # differ from.
    record_genre(run)
    clear_pointers(run)
    line = scope_line(run)
    if line:
        print(line)
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
            when = gate["when"]
            print("   %-9s [%s|%s|%s] %s" % (gate["id"], gate["severity"],
                                             "+".join(when) if isinstance(when, list) else when,
                                             "auto" if auto != "manual" else "by hand", gate["title"]))
            if "full" in flags:
                print("             %s" % gate["rule"])
    print("\n%d gates total.%s" % (total, " Filtered to kind %s." % kind if kind else ""))
    return 0


# ---------------------------------------------------------------- hooks


# Files whose contents are the design. A stylesheet is one unconditionally; a
# markup or component file counts once it actually carries styling or structure,
# which keeps ordinary logic edits out of the net.
STYLE_EXT = {".css", ".scss", ".sass", ".less"}
MARKUP_EXT = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".astro"}
DESIGN_MARKERS = re.compile(
    r"class(Name)?\s*=|<style|style\s*=|styled\.|@apply|css`|makeStyles|"
    r"<(div|section|header|footer|nav|main|button|input|form|h[1-6])\b",
    re.I,
)


def is_design_surface(path):
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in STYLE_EXT:
        return True
    if suffix in {".png", ".svg", ".webp", ".jpg", ".jpeg"}:
        return True
    if suffix in MARKUP_EXT:
        try:
            return bool(DESIGN_MARKERS.search(p.read_text(errors="replace")[:200000]))
        except OSError:
            return False
    return False


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


def open_run(targets, title, kind="web-ui", session=None):
    run = {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session or session_id(),
        "kind": kind,
        "extra_kinds": [],
        "targets": [str(t) for t in targets],
        "repo": target_root(targets) or repo_root(),
        "title": title,
        "flags": [],
        "results": {},
        "touched": [],
        "auto_scoped": False,
    }
    refresh(run)
    save_run(run)
    return run



# What an edited file is about, when its name says. An edit opens a run over that
# file and that subject only: a run covering the whole project is a thing to ask
# for, not something a stylesheet edit imposes.
SCOPE_BY_PATH = [
    ("nav", r"nav|header|menu|topbar"),
    ("footer", r"footer"),
    ("hero", r"hero|masthead|banner"),
    ("fields", r"form|input|select|field|combobox|dropdown|checkbox|radio|switch"),
    ("tables", r"table|grid-row|datagrid|ledger"),
    ("code", r"code|snippet|terminal|console|prompt|command|highlight"),
    ("loading", r"skeleton|placeholder|shimmer|loading"),
    ("motion", r"motion|animation|transition|keyframe"),
    ("tokens", r"token|theme|variable"),
    ("type", r"typography|type-scale|font"),
    ("spacing", r"spacing|layout"),
]


def scope_for_path(path):
    """The subject an edited file names, or None when its name says nothing."""
    stem = str(path).lower()
    for name, pattern in SCOPE_BY_PATH:
        if re.search(pattern, stem):
            return name
    return None

def cmd_hook_edit():
    """PostToolUse(Write|Edit): design work opens its own run and records what it touched.

    Waiting for the skill to be invoked leaves the obvious hole - hand-edit the
    stylesheet and no run ever exists, so nothing is checked and nothing says so.
    The run opens on the edit instead, and the Stop hook makes it finish.
    """
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path or not is_design_surface(path):
        return 0

    here = session_id(payload)
    run = load_run(required=False, session=here)
    if run is None or run.get("closed"):
        return 0
    # A run left unowned by an older build belongs to whichever session is doing
    # the work under it, which is the session editing this file.
    if here and not run.get("session"):
        run["session"] = here
    touched = run.setdefault("touched", [])
    resolved = str(Path(path).resolve())
    if resolved not in touched or run.get("session") == here:
        if resolved not in touched:
            touched.append(resolved)
        save_run(run)
    return 0


# The checklist answers to its own name. `design` is a different skill - the canvas
# builder the CLI ships - so an invocation of it is design work pointed at the wrong
# file rather than a name that could mean either.
CHECKLIST_SKILL = "design-checklist"
CANVAS_SKILL = "design"


def invokes(name, skill):
    """Whether a Skill call names this skill, with or without a plugin prefix."""
    return name == skill or name.endswith(":" + skill)


def cmd_hook_skill():
    """PostToolUse(Skill): a design invocation opens its own run, so none is skipped."""
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tool_input = payload.get("tool_input") or {}
    name = str(tool_input.get("skill") or "")
    if not (invokes(name, CHECKLIST_SKILL) or invokes(name, CANVAS_SKILL)):
        return 0
    mine = session_id(payload)
    if session_pointer(mine).exists():
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    run = open_run([project_root(cwd) or cwd], "design skill invocation", session=mine)
    print(
        "A design run (%s) was opened for this invocation. Scope it to what you are actually "
        "building before you start:\n"
        f"  {TOOL} scan --kind <%s> --target <path>\n"
        "Then verify, answer every gate, and report. The session cannot end with it open."
        % (run["id"], "|".join(KINDS)),
        file=sys.stderr,
    )
    return 0


def cmd_guard_skill():
    """PreToolUse(Skill): a design invocation runs this checklist, whatever loaded.

    More than one skill answers to the name `design` - the CLI ships one that builds
    artboards and never touches a repo - and which one resolves is decided outside
    this file. Nothing reports the collision, because a skill by that name did
    resolve, so the wrong skill and the right one look identical from the outside.
    The PostToolUse hook that used to cover this fired after the wrong instructions
    were already in context.

    Firing first removes the question. The invocation is refused once per session and
    the checklist's own entry point is named in its place, so the run opens from the
    file on disk rather than from whatever the name happened to load.
    """
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    name = str((payload.get("tool_input") or {}).get("skill") or "")
    if not invokes(name, CANVAS_SKILL):
        return 0

    here = session_id(payload)
    cwd = payload.get("cwd") or os.getcwd()
    root = project_root(cwd) or cwd

    # Once per session. The redirect has to stop being a redirect the moment it has
    # been followed, or a second /design in the same session blocks forever with the
    # run already open and the file already read.
    marker = RUNS / ("skill-redirect-%s" % (here or "nosession"))
    if marker.exists():
        return 0
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now(timezone.utc).isoformat())

    run = load_run(required=False, session=here)
    if run is None or run.get("closed"):
        run = open_run([root], "design skill invocation", session=here)

    print(
        "Design work in this repository runs the gate checklist, and %s is what "
        "runs it. Follow what it prints; the canvas builder /design loads "
        "does not apply here.\n"
        "\n"
        "Run %s is open over %s. It is an instruction to change the files - not to "
        "plan, propose, mock up, or ask which direction to take:\n"
        % (TOOL, run["id"], root)
        + f"  {TOOL} scan --kind web-ui --target <path>\n"
        + f"  {TOOL} verify\n"
        + f"  {TOOL} report\n"
        + f"  {TOOL} finish\n"
        "\n"
        "The session cannot end while the run is open.",
        file=sys.stderr,
    )
    return 2


def cmd_guard_stop():
    """Stop hook: refuse to end a session that left its own design run open.

    Its own, and no other's. Several sessions share this Mac, and a run is
    per-session state: blocking every session on whichever run was opened last
    stops work in projects that run has never looked at, and the session that
    does own it is not the one being told to finish it. A session with no run
    of its own has nothing here to answer for.
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
    groups, _, _ = refresh(run)
    save_run(run)
    open_ids = [g for g in run["order"] if run["results"][g]["status"] == "open"]
    if not open_ids and run.get("report_at"):
        return 0
    files = run.get("files") or {}
    unruled = [p for p, r in files.items() if r["status"] == "open"]
    detail = ("%d gate(s) unanswered: %s." % (len(open_ids), ", ".join(open_ids[:15]))
              if open_ids else "the filled checklist has not been shown to the user.")
    if unruled:
        detail += ("\n\n%d file(s) in scope have not been read and ruled on:\n%s"
                   % (len(unruled), "\n".join("  " + p for p in sorted(unruled)[:20])))
    print(
        "BLOCKED: design run %s is still open - %s\n"
        "\n"
        "The design checklist is answered in full before a session ends, and the filled\n"
        "list goes to the user. Finish it:\n"
        "\n"
        f"  {TOOL} status\n"
        f"  {TOOL} verify\n"
        f"  {TOOL} resolve <ID> --status pass|fixed|na --note \"...\"\n"
        f"  {TOOL} report\n"
        f"  {TOOL} finish\n"
        "\n"
        "If this session produced no design deliverable, say so and close it:\n"
        f"  {TOOL} finish --no-deliverable --note \"<why>\"\n"
        % (run["id"], detail),
        file=sys.stderr,
    )
    return 2


def differs_from_git(path):
    """Whether `path` still differs from what git has for it.

    Outside a working tree, or when git cannot answer, the file counts as
    changed: an unanswerable question must never read as a clean bill.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", path.name],
            cwd=str(path.parent), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if out.returncode != 0:
        return True
    return bool(out.stdout.strip())


def repo_root(start=None):
    """The working tree the command is running in, or None outside one."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start or Path.cwd()), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    root = out.stdout.strip()
    return str(Path(root).resolve()) if out.returncode == 0 and root else None


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
    """
    Whether this run has anything to say about the tree being landed.

    A run is about a tree when it was opened there or when one of its targets
    lives under it. Anything else -- a run over a scratch file, a run left open
    in another project -- has no verdict to give on this commit, and blocking on
    it stops work in a repo the run has never looked at.

    Outside a working tree there is nothing to compare, so the guard stands.
    """
    # A tree that could not be resolved is not this run's tree. Reading it as
    # every tree is how an open run over one project refuses a landing in another.
    if not root:
        return False
    root = Path(root)
    opened_in = run.get("repo")
    if opened_in and Path(opened_in) == root:
        return True
    for target in run.get("targets") or []:
        try:
            path = Path(target).resolve()
        except OSError:
            continue
        if path == root or root in path.parents:
            return True
    # A run that predates this check records no repo. Judge it on its targets
    # alone rather than assuming it belongs to whatever is landing.
    return False


def cmd_guard_land():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    try:
        tokens = shell_tokens(command)
    except ValueError:
        return 0
    # `cd <repo> && git commit` is the ordinary shape, and this process stays in
    # the session's directory throughout, so the directory in effect when the
    # landing verb appears is what decides which tree is being landed.
    where = Path(payload.get("cwd") or os.getcwd())
    landing = None
    for i, token in enumerate(tokens):
        head = Path(token).name
        if head == "cd" and i + 1 < len(tokens):
            # `cd ~/repo` is the ordinary form and is not expanded for us here,
            # so an unexpanded tilde resolves to a path under the session's
            # directory that does not exist -- and the guard then judges the
            # wrong tree, or none.
            target = Path(tokens[i + 1]).expanduser()
            where = target if target.is_absolute() else (where / target)
        if head == "git" and "-C" in tokens[i + 1:i + 3]:
            at = tokens.index("-C", i) + 1
            if at < len(tokens):
                where = Path(tokens[at]).expanduser()
        if head == "git" and "commit" in tokens[i + 1:i + 4]:
            landing = where
        if head == "gh" and tokens[i + 1:i + 3] == ["pr", "create"]:
            landing = where
    if landing is None:
        return 0
    run = load_run(required=False, here=landing)
    if not run or run.get("closed"):
        return 0
    if not run_covers(run, repo_root(landing)):
        return 0
    groups, _, _ = refresh(run)
    save_run(run)
    index = gate_index(groups)
    blockers = [g for g in run["order"]
                if run["results"][g]["status"] == "open" and index[g]["severity"] == "blocker"]
    if not blockers:
        return 0
    listing = "\n".join("  %-9s %s" % (g, index[g]["title"]) for g in blockers[:15])
    print(
        "BLOCKED: design run %s has %d blocking gate(s) unanswered.\n"
        "\n"
        "%s\n"
        "\n"
        "Answer them, then retry:\n"
        f"  {TOOL} verify\n"
        f"  {TOOL} resolve <ID> --status pass|fixed|na --note \"...\"\n"
        % (run["id"], len(blockers), listing),
        file=sys.stderr,
    )
    return 2


# ------------------------------------------------------------------ repair

# The engine is loaded by path as often as by name, so its own directory is
# not on the path by the time this runs.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_fixes  # noqa: E402

# Only faults with one arithmetic answer appear here. Layout, colour and copy are
# judgement, and nothing in this file guesses at one.
FIXERS = {
    "spacing_scale": design_fixes.fix_spacing_scale,
    "active_scale_range": design_fixes.fix_active_scale_range,
    "grid_minmax": design_fixes.fix_grid_minmax,
    "font_display": design_fixes.fix_font_display,
}


def cmd_fix(argv):
    """Apply every repair the failing gates have, then re-sweep to prove it landed."""
    run = load_run()
    groups, ctx, sources = refresh(run)
    index = gate_index(groups)
    applied, declined, done = [], [], set()
    for gid in run["order"]:
        gate = index[gid]
        kind = gate["check"]["type"]
        fixer, fn = FIXERS.get(kind), CHECKS.get(kind)
        if not fixer or fn is None:
            continue
        status, _ = fn(gate, sources, run, ctx)
        if status != FAIL:
            continue
        changed = [] if kind in done else (fixer(gate, sources, run, ctx) or [])
        done.add(kind)
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
        print("  fixed %-9s %-38s %s" % (gid, title[:38], ", ".join(changed[:5])))
    if declined:
        print("  declined %s" % ", ".join(declined))
    print("\n%d gate(s) repaired. Re-sweeping." % len(applied))
    return cmd_verify([])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    argv = sys.argv[2:]
    if mode == "start":
        return cmd_start(argv)
    if mode == "scan":
        return cmd_scan(argv)
    if mode == "verify":
        return cmd_verify(argv)
    if mode == "fix":
        return cmd_fix(argv)
    if mode == "resolve":
        return cmd_resolve(argv)
    if mode == "status":
        return cmd_status(argv)
    if mode == "report":
        return cmd_report(argv)
    if mode == "finish":
        return cmd_finish(argv)
    if mode == "gates":
        return cmd_gates(argv)
    if mode == "guard-stop":
        return cmd_guard_stop()
    if mode == "guard-land":
        return cmd_guard_land()
    if mode == "guard-skill":
        return cmd_guard_skill()
    if mode == "hook-skill":
        return cmd_hook_skill()
    if mode == "hook-edit":
        return cmd_hook_edit()
    if mode == "hook-read":
        return cmd_hook_read()
    if mode == "files":
        return cmd_files(argv)
    if mode == "file-clear":
        return cmd_file_clear(argv)
    if mode == "skeleton-probe":
        return cmd_skeleton_probe(argv)
    if mode == "cohesion":
        return cmd_cohesion(argv)
    if mode == "spacing-probe":
        return cmd_spacing_probe(argv)
    if mode == "layout-probe":
        return cmd_layout_probe(argv)
    if mode == "mobile-probe":
        return cmd_mobile_probe(argv)
    if mode == "read":
        return cmd_read(argv)
    if mode == "scopes":
        for name in sorted(SCOPES):
            prefixes, ids, what = SCOPES[name]
            print("  %-11s %s" % (name, what))
        print("\n  Narrow a run with --scope <name> on start or scan, and widen it again\n"
              "  with --scope all. Every report leads with what a scoped run left out.")
        return 0
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
