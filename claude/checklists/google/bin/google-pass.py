#!/usr/bin/env python3
"""The gate engine for /google-checklist.

Every other checklist in this family answers for one project's files. This one
answers for one portfolio's accounts: what is true inside Search Console,
Business Profile, GA4, Merchant Center and Google's index, across every site at
once. A run's scope is sites rather than paths, and a result is keyed by both
the site and the gate.

Two consequences follow, and they are the whole design.

Nothing here is settled by reading a file. A sitemap committed to a repository
and a sitemap Google has read are different facts, and only the second one
decides whether anything ranks. So the gates answer to readings taken over the
network and filed against the run, and a gate that answers to a probe refuses an
attestation.

The work splits by who is able to do it. An agent requests indexing, files a
sitemap, or fixes a canonical. Only a person claims a listing, passes a
verification, or signs in. The run carries that split and hands out one human
step at a time rather than returning a backlog nobody can act on.

Credentials are read from the environment at the point of use and never written
here. The probe script holds that contract; this tool only files what it wrote.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()

# Resolved from this file's own location, so the gates work wherever they land -
# a synced ~/.claude, an installed copy in a repo, a container that has the
# checklist and nothing else. A fixed path under ~/.claude only exists on a
# machine that already ran the sync, which is exactly the machine that did not
# need it.
ENGINE = Path(__file__).resolve().parent.parent
if not (ENGINE / "checklist" / "gates").is_dir():
    ENGINE = HOME / ".claude" / "checklists" / "google"
GATES_DIR = ENGINE / "checklist" / "gates"
OVERLAY = ENGINE / "checklist" / "sites.json"
SKILL = ENGINE
PROBE_TOOL = str(SKILL / "scripts" / "probe.py")

TOOL = str(Path(__file__).resolve())
TARGET_NOUN = "site"


def _profile():
    """The profile reader, tools/mtoa_config.py: the copy in the tree this file
    sits in first, then the installed one, else nothing."""
    import importlib.util
    here = Path(__file__).resolve()
    tree = [here.parents[3]] if len(here.parents) > 3 else []
    for root in tree + [HOME / ".claude"]:
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


PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
ANSWERS = ("pass", "fixed", "na", "disputed", "blocked")

# A gate settled from the run's own state. An attestation to one of these is a
# claim about bookkeeping the tool can already see, so it is refused outright.
SELF_SETTLING = {"run_opened", "site_coverage", "all_resolved", "report_emitted",
                 "delegation"}

# Gates that answer for the run rather than for any one site. They are held once
# under this key instead of once per site: sixteen copies of "the report was
# rendered" are the same fact sixteen times, and they inflate every count the
# standing page draws.
RUN_LEVEL = SELF_SETTLING | {"writing_pass"}
RUN_SCOPE = "(run)"

# Google's own kinds, declared per site rather than per run: one portfolio holds
# a go-kart track and a mobile app, and they owe different things.
KINDS = ["site", "local-business", "ecommerce", "product", "app", "blog"]

# Context keys a gate may wait on. Everything but `always` and `local` is
# declared, because no fetch of a home page reliably proves a business has a
# storefront or runs ads.
CONTEXT_KEYS = ("always", "local", "storefront", "ecommerce", "blog", "app",
                "forms", "multisite", "ads")

SCOPES = {
    "search-console": (["GSC"], "properties, verification, sitemaps and coverage"),
    "indexing": (["IDX"], "what Google has actually indexed and chosen as canonical"),
    "analytics": (["GA4"], "the measurement property and the tag that feeds it"),
    "business": (["GBP"], "the Business Profile a local search actually returns"),
    "rich-results": (["RCH"], "the result types Google grants the pages"),
    "vitals": (["CWV"], "the field record at the 75th percentile"),
    "commerce": (["CMR"], "Merchant Center feeds and Ads conversion tracking"),
    "trust": (["TRS"], "Safe Browsing, HTTPS and manual actions"),
    "presence": (["PRS"], "what a search for the brand name returns"),
}
SCOPE_ALWAYS = SELF_SETTLING | {"writing_pass"}

# A fix only a person can carry out. The reading may still be a probe; this
# decides who the open item is handed to, not how it is answered.
HUMAN_FIX = re.compile(
    r"\b(dashboard|search console|business profile|merchant center|ads account|"
    r"sign in|signs in|log in|claim|claims|verif|postcard|phone call|"
    r"account owner|ownership|billing|payment|subscri|support|appeal|"
    r"reinstat|suspend|request review|permission)", re.I)

LANDING = re.compile(r"\bgit\s+commit\b|\bgh\s+pr\s+create\b")


def now():
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())


def _state_root():
    """Where per-machine run state lives.

    A cloud session cannot write under ~/.claude, so the ledger lives
    beside it there. Runs are per-session state and are deliberately not synced.
    """
    override = os.environ.get("INTELLIGENCE_STATE_DIR")
    if override:
        return Path(override)
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return HOME / ".pass-state"
    return HOME / ".claude" / "state"


RUNS = _state_root() / "google-runs"
CURRENT = RUNS / "current.json"


def session_id(payload=None):
    """Which session this call belongs to, or None when it cannot be told.

    One pointer for the whole machine means two sessions share it, so a run
    opened for one portfolio refuses to let an unrelated session end.
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
    got = run.get("repo") or run.get("target") or run.get("registry")
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
    inside = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(cwd),
                            capture_output=True, text=True)
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


def _run_at(pointer):
    if not pointer.exists():
        return None
    try:
        named = json.loads(pointer.read_text())["id"]
        return json.loads((RUNS / ("%s.json" % named)).read_text())
    except (ValueError, OSError, KeyError):
        return None


def load_run(required=True, session=None, own_only=False, here=None):
    """The session's own pointer first, then the shared one when it has no owner.

    Adopting a run another session owns is how one session's work ends up
    recorded against another's checklist.

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
        raise SystemExit("No Google run is open. Start one:\n  %s start --all" % TOOL)
    return run


def save_run(run):
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / ("%s.json" % run["id"])).write_text(json.dumps(run, indent=2))
    pointer = json.dumps({"id": run["id"]})
    claim_shared(run, pointer)
    if run.get("session"):
        pointer_for(run["session"], run_target(run)).write_text(pointer)


def parse_flags(argv):
    out, key = {}, None
    for token in argv:
        if token.startswith("--"):
            key = token[2:]
            out.setdefault(key, [])
        elif key:
            out[key].append(token)
    return out


# ---------------------------------------------------------------- the registry

REGISTRY_REL = Path("src") / "app" / "data" / "portfolio.js"
EXCLUSIONS_REL = Path("scripts") / "portfolio-sites.json"

# The portfolio is the list the daily maintenance routine already polices
# against reality, so it is read rather than copied. The site the portfolio
# itself is published from - the agency's own, as the profile names it - is
# absent from it by design and is added back here.
def own_site():
    """The agency's own site as a portfolio row, or None where the profile has none."""
    domain = setting("agency.domain")
    if not domain:
        return None
    return {"domain": domain, "name": setting("agency.name") or domain, "kind": "product",
            "url": setting("agency.url") or "https://%s" % domain, "town": ""}


def registry_search():
    """Where a checkout of the agency's site is looked for, by the profile's agency.repo."""
    repo = setting("agency.repo")
    if not repo:
        return ()
    return (Path("/home/user") / repo,
            HOME / "Development" / "WebstormProjects" / repo,
            HOME / "Development" / "IdeaProjects" / repo,
            HOME / repo)


def registry_root(explicit=None):
    """The checkout of the agency's site the portfolio is read from."""
    named = explicit or os.environ.get("GOOGLE_SITE_REGISTRY")
    if named:
        root = Path(named).expanduser().resolve()
        if not (root / REGISTRY_REL).is_file():
            raise SystemExit("%s holds no %s" % (root, REGISTRY_REL))
        return root
    for root in registry_search():
        if (root / REGISTRY_REL).is_file():
            return root
    return None


def standing_down(flags):
    """Why this checklist has nothing to do here, or None when it does.

    The portfolio it reads is the agency's, so a profile naming no agency and a
    run naming no checkout of its own leave it with no sites and nothing wrong.
    """
    if setting("agency.repo") or (flags.get("registry") or [None])[0] \
            or os.environ.get("GOOGLE_SITE_REGISTRY"):
        return None
    return ("Not applicable: the Google checklist reads the agency's portfolio, and the "
            "profile names no agency (agency.repo). Point it at a portfolio checkout with "
            "--registry <checkout> to run it anyway.")


def _registry_via_node(path):
    """Evaluate the portfolio module, which is the only reader that cannot drift."""
    script = (
        "import(process.argv[1]).then(m => "
        "process.stdout.write(JSON.stringify(m.PORTFOLIO_PROJECTS ?? [])))"
    )
    try:
        done = subprocess.run(["node", "--input-type=module", "-e", script, str(path)],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    try:
        rows = json.loads(done.stdout)
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


FIELD = re.compile(r"(name|url|displayUrl|kind|town)\s*:\s*'([^']*)'")


def _registry_via_text(path):
    """A reader for a machine with no node, keyed on the fields that matter."""
    try:
        text = path.read_text()
    except OSError:
        return []
    rows, current = [], {}
    for key, value in FIELD.findall(text):
        if key in current:
            rows.append(current)
            current = {}
        current[key] = value
    if current:
        rows.append(current)
    return [r for r in rows if r.get("url")]


def load_registry(root):
    """Every site in the portfolio, plus the site the portfolio is published from."""
    if root is None:
        return []
    path = root / REGISTRY_REL
    rows = _registry_via_node(path)
    if rows is None:
        rows = _registry_via_text(path)
    excluded = set()
    try:
        payload = json.loads((root / EXCLUSIONS_REL).read_text())
        excluded = {row["domain"] for row in payload.get("excluded", []) if row.get("domain")}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    out = []
    for row in rows:
        domain = (row.get("displayUrl") or "").strip().lower()
        if not domain:
            domain = re.sub(r"^https?://(www\.)?", "", row.get("url", "")).strip("/").lower()
        if not domain or domain in excluded:
            continue
        out.append({"domain": domain,
                    "name": row.get("name") or domain,
                    "kind": row.get("kind") or "client",
                    "url": row.get("url") or ("https://%s" % domain),
                    "town": row.get("town") or ""})
    own = own_site()
    if own and not any(r["domain"] == own["domain"] for r in out):
        out.append(own)
    return sorted(out, key=lambda r: r["domain"])


def load_overlay():
    """Per-site kind and context declarations, held with the skill.

    A tree cannot show that a business has a storefront or that it runs ads, and
    guessing produces a checklist that asks a mobile app for its opening
    hours.
    """
    try:
        return json.loads(OVERLAY.read_text())
    except (OSError, ValueError):
        return {}


def site_kind(row, overlay):
    """A portfolio entry's kind, as this checklist means it.

    The portfolio draws one line, between a client and a product of its own. A
    client is a business that serves somebody within driving distance, so it
    owes a Business Profile whether or not its entry names a town: the town
    field is filled in for some and not others, and a barber shop with the field
    left blank still ranks on local searches.
    """
    named = (overlay.get(row["domain"]) or {}).get("kind")
    if named in KINDS:
        return named
    return "product" if row.get("kind") == "product" else "local-business"


def context_for(site):
    """Which gates a site is answerable for."""
    ctx = {key: False for key in CONTEXT_KEYS}
    ctx["always"] = True
    ctx["local"] = site["kind"] == "local-business" or bool(site.get("town"))
    for flag in site.get("flags", []):
        if flag in ctx:
            ctx[flag] = True
    return ctx


# ------------------------------------------------------------------- the gates

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
            merged["prefix"] = data.get("prefix", merged["id"].split("-")[0])
            gates.append(merged)
        groups.append({"group": data["group"], "prefix": data.get("prefix", ""),
                       "description": data.get("description", ""), "gates": gates})
    return groups


def all_gates(groups):
    return [g for grp in groups for g in grp["gates"]]


def gate_index(groups):
    return {g["id"]: g for g in all_gates(groups)}


def gate_owner(gate):
    """Who carries out this gate's fix.

    The reading and the repair have different owners: a probe can tell nobody
    claimed the listing, and only a person can claim it.

    A gate states its own owner where the split matters. Read from the fix
    otherwise, which routes rather than rules: the wrong guess costs an item
    appearing in the wrong half of `next`, where its fix is printed beside it,
    and never costs a wrong verdict.
    """
    named = gate.get("owner")
    if named in ("human", "agent"):
        return named
    kind = gate["check"]["type"]
    if kind == "attest":
        return "human"
    if kind in ("writing_pass",) or kind in SELF_SETTLING:
        return "agent"
    return "human" if HUMAN_FIX.search(gate.get("fix", "")) else "agent"


def in_scope(gate, scope):
    if not scope:
        return True
    picked = SCOPES.get(scope)
    if not picked:
        return True
    if gate["check"]["type"] in SCOPE_ALWAYS:
        return True
    return any(gate["id"].startswith(p + "-") for p in picked[0])


def applicable(gate, site, run, ctx):
    if not in_scope(gate, run.get("scope")):
        return False, "outside the %s scope this run declared" % run["scope"]
    kinds = gate["kinds"]
    if "*" not in kinds and site["kind"] not in kinds:
        return False, "kind %s is out of scope for this gate" % site["kind"]
    when = gate["when"]
    wanted = when if isinstance(when, list) else [when]
    for key in wanted:
        if not ctx.get(key, False):
            return False, "%s is not a %s site" % (site["domain"], key)
    return True, ""


def scope_line(run):
    scope = run.get("scope")
    if not scope or scope not in SCOPES:
        return ""
    return "SCOPED RUN: %s only - %s\n" % (scope, SCOPES[scope][1])


def key(domain, gid):
    return "%s|%s" % (domain, gid)


def split_key(item):
    domain, _, gid = item.partition("|")
    return domain, gid


# ---------------------------------------------------------------- the checkers

def check_probe(gate, site, run, ctx):
    """A gate settled by a reading that was actually taken."""
    name = gate["check"].get("probe", "")
    filed = (run.get("probe") or {}).get(key(site["domain"], name))
    if not filed:
        return UNKNOWN, ("no probe filed for %s on %s - take it with %s and file it with "
                         "`%s probe --file <result.json>`" % (name, site["domain"],
                                                              PROBE_TOOL, TOOL))
    when = filed.get("at", "")
    if filed.get("ok"):
        return PASS, "%s (probed %s)" % (filed.get("detail", "answered"), when)
    return FAIL, "%s (probed %s)" % (filed.get("detail", "did not answer"), when)


def check_attest(gate, site, run, ctx):
    """A reading Google exposes to a person and to no API.

    It stays inconclusive until someone records what they saw, and a probe that
    contradicts the record still wins.
    """
    name = gate["check"].get("probe", "")
    if name:
        filed = (run.get("probe") or {}).get(key(site["domain"], name))
        if filed and not filed.get("ok"):
            return FAIL, "%s (probed %s)" % (filed.get("detail", ""), filed.get("at", ""))
    return UNKNOWN, "answers to a reading taken in a Google dashboard, recorded by hand"


def check_writing_pass(gate, site, run, ctx):
    """Copy a person reads is held to the writing standard like any other string."""
    tool = HOME / ".claude" / "tools" / "writing-pass.py"
    if not tool.is_file():
        return UNKNOWN, "the writing standard tool is not installed on this machine"
    root = run.get("registry")
    if not root:
        return UNKNOWN, "no registry checkout to read copy from"
    try:
        done = subprocess.run([sys.executable, str(tool), "status"], cwd=root,
                              capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return UNKNOWN, "the writing standard could not be run: %s" % exc
    text = (done.stdout or "") + (done.stderr or "")
    if done.returncode == 0:
        return PASS, "the writing standard reports nothing blocking"
    return FAIL, text.strip().splitlines()[-1][:200] if text.strip() else "flagged copy"


CHECKS = {
    "manual": None,
    "probe": check_probe,
    "attest": check_attest,
    "writing_pass": check_writing_pass,
    # The self-settling types are recomputed from run state on every refresh and
    # never reach this table for their real value.
    "run_opened": None,
    "site_coverage": None,
    "all_resolved": None,
    "report_emitted": None,
    "delegation": None,
}


def settle_self(run, index):
    """Recompute the gates that answer to the run's own state."""
    for item in run.get("order", []):
        domain, gid = split_key(item)
        gate = index.get(gid)
        if gate is None:
            continue
        kind = gate["check"]["type"]
        if kind not in SELF_SETTLING:
            continue
        result = run["results"].setdefault(item, {"status": "open", "note": "", "auto": ""})
        ok, note = _settled(kind, run, index)
        result["auto"] = "%s: %s" % (PASS if ok else FAIL, note)
        result["status"] = "pass" if ok else "open"
        result["note"] = note
        result["at"] = now()


def _settled(kind, run, index):
    if kind == "run_opened":
        return True, "run %s opened %s" % (run["id"], run["opened"])
    if kind == "site_coverage":
        known = {row["domain"] for row in run.get("known", [])}
        swept = set(run.get("sites", {}))
        missing = sorted(known - swept)
        if not missing:
            return True, "every site in the portfolio is in this run (%d)" % len(swept)
        return False, ("%d site(s) in the portfolio are outside this run: %s. Widen with "
                       "`%s scan --all`, or name why they are excluded."
                       % (len(missing), ", ".join(missing), TOOL))
    if kind == "all_resolved":
        outstanding = [i for i in run.get("order", [])
                       if run["results"].get(i, {}).get("status") == "open"
                       and index[split_key(i)[1]]["check"]["type"] not in SELF_SETTLING]
        if not outstanding:
            return True, "every applicable gate is answered"
        return False, "%d gate(s) unanswered" % len(outstanding)
    if kind == "report_emitted":
        if run.get("report_at"):
            return True, "checklist rendered %s" % run["report_at"]
        return False, "the filled checklist has not been rendered"
    if kind == "delegation":
        handed = run.get("delegated") or []
        if handed:
            return True, "%d finding(s) handed over" % len(handed)
        if run.get("nothing_delegated"):
            return True, "nothing outside this checklist came up"
        return False, ("nothing recorded either way - hand over what belongs to another "
                       "checklist, or record that nothing came up with `%s delegate --none`"
                       % TOOL)
    return False, "unsettled"


# ------------------------------------------------------------------- refreshing

def refresh(run):
    """Rebuild the applicable set from the registry and the gates, then settle."""
    groups = load_gates()
    index = gate_index(groups)
    overlay = load_overlay()
    root = run.get("registry")
    run["known"] = load_registry(Path(root)) if root else run.get("known", [])

    order, contexts = [], {}
    contexts[RUN_SCOPE] = {name: True for name in CONTEXT_KEYS}
    for gate in all_gates(groups):
        if gate["check"]["type"] in RUN_LEVEL and in_scope(gate, run.get("scope")):
            order.append(key(RUN_SCOPE, gate["id"]))
    for domain, site in sorted(run.get("sites", {}).items()):
        site["domain"] = domain
        ctx = context_for(site)
        contexts[domain] = ctx
        for gate in all_gates(groups):
            if gate["check"]["type"] in RUN_LEVEL:
                continue
            ok, _ = applicable(gate, site, run, ctx)
            if ok:
                order.append(key(domain, gate["id"]))
    run["order"] = order
    known = set(order)
    for item in list(run["results"]):
        if item not in known:
            del run["results"][item]
    for item in order:
        run["results"].setdefault(item, {"status": "open", "note": "", "auto": "", "at": ""})
    run["context"] = {d: sorted(k for k, v in c.items() if v) for d, c in contexts.items()}
    settle_self(run, index)
    return groups, contexts, index


def open_run(flags, session):
    root = registry_root((flags.get("registry") or [None])[0])
    overlay = load_overlay()
    known = load_registry(root)
    named = [d.strip().lower() for d in flags.get("site", [])]
    if "all" in flags or not named:
        picked = known
    else:
        by_domain = {row["domain"]: row for row in known}
        unknown = [d for d in named if d not in by_domain]
        if unknown:
            raise SystemExit("not in the portfolio: %s\nKnown: %s"
                             % (", ".join(unknown), ", ".join(sorted(by_domain))))
        picked = [by_domain[d] for d in named]
    sites = {}
    for row in picked:
        declared = overlay.get(row["domain"]) or {}
        sites[row["domain"]] = {"domain": row["domain"], "name": row["name"],
                                "url": row["url"], "town": row.get("town", ""),
                                "kind": site_kind(row, overlay),
                                "flags": list(declared.get("flags", []))}
    for token in flags.get("flag", []):
        domain, _, name = token.partition(":")
        if not name:
            for site in sites.values():
                site["flags"].append(domain)
        elif domain in sites:
            sites[domain]["flags"].append(name)
    scope = (flags.get("scope") or [None])[0]
    if scope in ("all", "none"):
        scope = None
    return {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session,
        "title": " ".join(flags.get("title", [])) or "Google run",
        "registry": str(root) if root else "",
        "sites": sites,
        "known": known,
        "scope": scope,
        "results": {},
        "probe": {},
        "delegated": [],
    }


# -------------------------------------------------------------------- commands

def cmd_start(argv):
    flags = parse_flags(argv)
    existing = load_run(required=False, own_only=True)
    if existing and not existing.get("closed") and "force" not in flags:
        raise SystemExit("Run %s is already open on %d site(s). Use `%s status`, or pass "
                         "--force to replace it." % (existing["id"], len(existing["sites"]), TOOL))
    if standing_down(flags):
        print(standing_down(flags))
        return 0
    run = open_run(flags, session_id())
    if not run["sites"]:
        raise SystemExit("No sites resolved. Point at the portfolio with --registry <checkout>.")
    refresh(run)
    save_run(run)
    print("%sRun %s open on %d site(s), %d gate(s) applied."
          % (scope_line(run), run["id"], len(run["sites"]), len(run["order"])))
    if not run["registry"]:
        print("No portfolio checkout found, so the site list could not be verified against it.")
    print("\nNext: take the readings, then file them.\n  %s all --out /tmp/google-probe.json"
          "\n  %s probe --file /tmp/google-probe.json\n  %s verify"
          % (PROBE_TOOL, TOOL, TOOL))
    return 0


def cmd_scan(argv):
    run = load_run()
    flags = parse_flags(argv)
    fresh = open_run(flags, run.get("session"))
    run["sites"].update(fresh["sites"])
    if flags.get("registry"):
        run["registry"] = fresh["registry"]
    if "scope" in flags:
        run["scope"] = fresh["scope"]
    refresh(run)
    save_run(run)
    print("%sRun %s now covers %d site(s), %d gate(s)."
          % (scope_line(run), run["id"], len(run["sites"]), len(run["order"])))
    return 0


def cmd_sites(argv):
    run = load_run()
    flags = parse_flags(argv)
    refresh(run)
    save_run(run)
    rows = []
    for domain in sorted(run["sites"]):
        site = run["sites"][domain]
        counts = tally(run, domain)
        rows.append((domain, site["kind"], counts))
    width = max((len(r[0]) for r in rows), default=6)
    print("%-*s  %-14s  %5s %5s %5s %5s" % (width, "site", "kind", "open", "pass", "n/a", "block"))
    for domain, kind, counts in rows:
        print("%-*s  %-14s  %5d %5d %5d %5d"
              % (width, domain, kind, counts["open"], counts["pass"] + counts["fixed"],
                 counts["na"], counts["blocked"]))
    known = {row["domain"] for row in run.get("known", [])}
    missing = sorted(known - set(run["sites"]))
    if missing:
        print("\n%d portfolio site(s) outside this run: %s" % (len(missing), ", ".join(missing)))
    if "full" in flags:
        print("\nContext declared per site:")
        for domain in sorted(run["sites"]):
            print("  %-*s  %s" % (width, domain, ", ".join(run["context"].get(domain, []))))
    return 0


def tally(run, domain=None):
    counts = {"open": 0, "pass": 0, "fixed": 0, "na": 0, "disputed": 0, "blocked": 0}
    for item in run.get("order", []):
        if domain and split_key(item)[0] != domain:
            continue
        status = run["results"].get(item, {}).get("status", "open")
        counts[status] = counts.get(status, 0) + 1
    return counts


def cmd_verify(argv):
    run = load_run()
    groups, contexts, index = refresh(run)
    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0, "manual": 0}
    lines = []
    for item in run["order"]:
        domain, gid = split_key(item)
        gate = index[gid]
        result = run["results"][item]
        if gate["check"]["type"] in SELF_SETTLING:
            continue
        fn = CHECKS.get(gate["check"]["type"], "missing")
        if fn is None:
            counts["manual"] += 1
            continue
        if fn == "missing":
            result["auto"] = "no checker implemented for %s" % gate["check"]["type"]
            counts[UNKNOWN] += 1
            lines.append("  ?  %-24s %-8s %s - checker not implemented"
                         % (domain, gid, gate["title"]))
            continue
        site = run["sites"].get(domain) or {"domain": domain, "kind": "site", "flags": []}
        try:
            status, detail = fn(gate, site, run, contexts.get(domain, {}))
        except Exception as exc:  # a broken checker must never read as a pass
            status, detail = UNKNOWN, "checker error: %s" % exc
        result["auto"] = "%s: %s" % (status, detail)
        counts[status] += 1
        if result.get("status") == "fixed" and status == FAIL:
            result["status"] = "open"
            lines.append("  X  %-24s %-8s marked fixed, but the reading still fails: %s"
                         % (domain, gid, detail[:90]))
            continue
        if result.get("by") == "hand" and result.get("status") not in ("open", "fixed") \
                and status != PASS:
            lines.append("  ok %-24s %-8s answered by hand; the reading says: %s"
                         % (domain, gid, detail[:80]))
            continue
        if status == PASS:
            result.update({"status": "pass", "note": detail, "at": now()})
        elif status == FAIL and result.get("status") not in ("na", "disputed", "blocked"):
            result["status"] = "open"
            lines.append("  X  %-24s %-8s %s\n       %s"
                         % (domain, gid, gate["title"], detail[:120]))
    settle_self(run, index)
    save_run(run)
    print(scope_line(run), end="")
    for line in lines[:60]:
        print(line)
    if len(lines) > 60:
        print("  ... %d more" % (len(lines) - 60))
    print("\n%d passed, %d failing, %d without a reading, %d by judgement."
          % (counts[PASS], counts[FAIL], counts[UNKNOWN], counts["manual"]))
    missing = sorted(probes_wanted(run, index) - set(run.get("probe", {})))
    if missing:
        print("%d probe(s) not filed. Take them with %s, then `%s probe --file <result.json>`."
              % (len(missing), PROBE_TOOL, TOOL))
    return 0


def probes_wanted(run, index):
    """Every site-and-probe pair the applicable gates need answered."""
    want = set()
    for item in run.get("order", []):
        domain, gid = split_key(item)
        check = index[gid]["check"]
        name = check.get("probe")
        if check["type"] in ("probe", "attest") and name:
            want.add(key(domain, name))
    return want


def _condition_still_holds(gate, ctx):
    when = gate["when"]
    wanted = when if isinstance(when, list) else [when]
    return all(ctx.get(k, False) for k in wanted)


def _validate_answer(item, status, note, run, index, ctx=None):
    """Every reason one answer cannot stand, so a batch reports all of them at once."""
    faults = []
    if status not in ANSWERS:
        faults.append("status must be one of %s, not %r" % (", ".join(ANSWERS), status))
    if item not in run.get("results", {}):
        faults.append("not applicable to this run")
        return faults
    domain, gid = split_key(item)
    gate = index.get(gid)
    if gate is None:
        faults.append("no gate %s in the registry" % gid)
        return faults
    kind = gate["check"]["type"]
    if kind in SELF_SETTLING:
        faults.append("answers to the run's own state, not to an attestation")
    swept = str(run["results"][item].get("auto", ""))
    failing = swept.startswith("fail")
    if kind == "probe" and status in ("pass", "na"):
        faults.append("this gate answers to a reading, not to an attestation - take it with "
                      "%s and file it with `%s probe --file <result.json>`. Where that surface "
                      "does not apply to %s, declare the site's context instead of answering "
                      "n/a." % (PROBE_TOOL, TOOL, domain))
    if kind == "attest" and status == "pass" and len(note.strip()) < 40:
        faults.append("recording a dashboard reading takes what was seen, where, and when - "
                      "at least a sentence, so it is followable without this session")
    if status == "pass" and failing:
        faults.append("the reading found this failing, so it cannot be answered as passing: %s"
                      % swept[:120])
    if status == "na" and failing:
        faults.append("the reading found this failing, so it is present and unmet, not "
                      "inapplicable")
    if status == "na" and ctx is not None and _condition_still_holds(gate, ctx):
        faults.append("the gate entered this run because %s is %s, and it still is - answering "
                      "n/a takes changing the site's declared context, not an attestation"
                      % (domain, gate["when"]))
    if status == "disputed" and len(note.strip()) < 40:
        faults.append("disputing a reading takes the measurement that contradicts it")
    if status == "disputed" and not failing:
        faults.append("nothing to dispute - the reading did not fail this gate")
    if status == "blocked" and len(note.strip()) < 40:
        faults.append("recording a gate as blocked takes what it is waiting on and when that "
                      "clears - Google's own clocks are the reason this status exists")
    return faults


def _apply(run, item, status, note):
    run["results"][item].update({"status": status, "note": note, "at": now(), "by": "hand"})


def _batch_rows(source):
    text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SystemExit("could not read the batch as JSON: %s" % exc)
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise SystemExit("a batch is a list of {site, id, status, note} objects")
    return payload


def cmd_resolve(argv):
    if "--batch" in argv:
        return cmd_resolve_batch(argv)
    run = load_run()
    groups, contexts, index = refresh(run)
    ids, status, note, mode = [], "", "", None
    for token in argv:
        if token == "--status":
            mode = "status"
        elif token == "--note":
            mode = "note"
        elif token == "--site":
            mode = "site"
        elif token.startswith("--"):
            mode = None
        elif mode == "status":
            status = token
        elif mode == "note":
            note = (note + " " + token).strip()
        elif mode == "site":
            ids.append(("site", token))
        else:
            ids.append(("gate", token))
    domains = [v for k, v in ids if k == "site"] or sorted(run["sites"]) + [RUN_SCOPE]
    gates = [v for k, v in ids if k == "gate"]
    if not gates:
        raise SystemExit("resolve takes one or more gate ids, and --site to say which site")
    items = [key(d, g) for d in domains for g in gates if key(d, g) in run["results"]]
    if not items:
        raise SystemExit("no applicable gate matched %s on %s"
                         % (", ".join(gates), ", ".join(domains)))
    problems, applied = [], 0
    for item in items:
        domain = split_key(item)[0]
        faults = _validate_answer(item, status, note, run, index, contexts.get(domain))
        if faults:
            problems.append("%s: %s" % (item, "; ".join(faults)))
            continue
        _apply(run, item, status, note)
        applied += 1
    settle_self(run, index)
    save_run(run)
    for row in problems:
        print(row, file=sys.stderr)
    print("%d answered." % applied)
    return 1 if problems and not applied else 0


def cmd_resolve_batch(argv):
    run = load_run()
    groups, contexts, index = refresh(run)
    flags = parse_flags(argv)
    source = (flags.get("batch") or ["-"])[0]
    rows = _batch_rows(source)
    problems, applied, seen = [], 0, set()
    for row in rows:
        if not isinstance(row, dict):
            problems.append("%r is not an object" % row)
            continue
        gid = str(row.get("id", "")).strip()
        domain = str(row.get("site", "")).strip().lower()
        if not gid or not domain:
            problems.append("%r names no site and gate" % row)
            continue
        item = key(domain, gid)
        if item in seen:
            problems.append("%s appears twice" % item)
            continue
        seen.add(item)
        status = str(row.get("status", "")).strip()
        note = str(row.get("note", "")).strip()
        faults = _validate_answer(item, status, note, run, index, contexts.get(domain))
        if faults:
            problems.append("%s: %s" % (item, "; ".join(faults)))
            continue
        _apply(run, item, status, note)
        applied += 1
    settle_self(run, index)
    save_run(run)
    for row in problems:
        print(row, file=sys.stderr)
    print("%d answered, %d refused." % (applied, len(problems)))
    return 1 if problems and not applied else 0


def cmd_probe(argv):
    """File readings. A gate in this checklist answers to one of these, never to a claim."""
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
        raise SystemExit('%s is not a probe result - expected {"probes": {"<site>|<name>": '
                         '{"ok": bool, "detail": str}}}' % source)
    store = run.setdefault("probe", {})
    for name, row in rows.items():
        if not isinstance(row, dict) or "ok" not in row:
            raise SystemExit("probe %r carries no ok verdict" % name)
        if "|" not in name:
            raise SystemExit("probe %r names no site - a reading belongs to one site, so the "
                             "key is <site>|<probe>" % name)
        store[name] = {"ok": bool(row["ok"]), "detail": str(row.get("detail", "")),
                       "at": str(row.get("at") or now())}
    groups, contexts, index = refresh(run)
    save_run(run)
    want = probes_wanted(run, index)
    missing = sorted(want - set(store))
    failing = sorted(n for n, r in store.items() if not r["ok"])
    print("%d reading(s) filed. %d of %d wanted are now answered."
          % (len(rows), len(want & set(store)), len(want)))
    if missing:
        print("\n%d still unread: %s" % (len(missing), ", ".join(missing[:20])))
    if failing:
        print("\n%d reading(s) are failing right now. These are settings to change, not "
              "findings to record - none of them takes a hand answer." % len(failing))
    return 0


def cmd_delegate(argv):
    """Hand a finding to the checklist that owns it, on the record."""
    run = load_run()
    flags = parse_flags(argv)
    if "none" in flags:
        run["nothing_delegated"] = True
        _, _, index = refresh(run)
        save_run(run)
        print("Recorded: nothing outside this checklist came up in this run.")
        return 0
    to = (flags.get("to") or [""])[0]
    note = " ".join(flags.get("note", []))
    if to not in ("seo", "perf", "stack", "design"):
        raise SystemExit("--to must be seo, perf, stack or design. Use --none when nothing "
                         "outside this checklist came up.")
    if len(note.strip()) < 20:
        raise SystemExit("--note must say what was found and what was done with it, so the "
                         "handover is followable without this session")
    run.setdefault("delegated", []).append({"to": to, "note": note, "at": now()})
    run["nothing_delegated"] = False
    _, _, index = refresh(run)
    save_run(run)
    print("Handed to /%s-checklist: %s" % (to, note))
    return 0


def _ready(run, index, only=None):
    """Open gates that can be worked now, split by who is able to work them."""
    agent, human = [], []
    for item in run["order"]:
        domain, gid = split_key(item)
        if only and domain != only:
            continue
        if run["results"][item]["status"] != "open":
            continue
        gate = index[gid]
        if gate["check"]["type"] in SELF_SETTLING:
            continue
        (human if gate_owner(gate) == "human" else agent).append((domain, gate))

    def rank(row):
        return (0 if row[1]["severity"] == "blocker" else 1, row[1]["id"], row[0])

    return sorted(agent, key=rank), sorted(human, key=rank)


def cmd_next(argv):
    """What to work now.

    A portfolio-wide list of every open item is a backlog rather than a next
    step, so the site is the unit: one site is worked down at a time, its agent
    work goes out together, and the person is asked for one thing.
    """
    run = load_run()
    groups, contexts, index = refresh(run)
    save_run(run)
    flags = parse_flags(argv)
    named = [d.strip().lower() for d in flags.get("site", [])]
    if not named:
        agent, human = _ready(run, index)
        if not agent and not human:
            print("Nothing open. Render the checklist:\n  %s report" % TOOL)
            return 0
        print("%d open item(s) across %d site(s). Worst first:\n" % (len(agent) + len(human),
                                                                    len(run["sites"])))
        ranked = sorted(run["sites"], key=lambda d: -tally(run, d)["open"])
        for domain in ranked[:16]:
            a, h = _ready(run, index, domain)
            blockers = sum(1 for _, g in a + h if g["severity"] == "blocker")
            print("  %-26s %3d open  %3d waiting on a person  %3d blocker(s)"
                  % (domain, len(a) + len(h), len(h), blockers))
        print("\nWork one down:\n  %s next --site %s" % (TOOL, ranked[0] if ranked else "<site>"))
        return 0

    for domain in named:
        if domain not in run["sites"]:
            print("%s is not in this run." % domain, file=sys.stderr)
            continue
        agent, human = _ready(run, index, domain)
        print("## %s (%s)" % (domain, run["sites"][domain]["kind"]))
        if not agent and not human:
            print("  Nothing open.\n")
            continue
        if agent:
            print("\nReady for an agent (%d):" % len(agent))
            for _, gate in agent[:40]:
                print("  %-8s %-9s %s\n      %s"
                      % (gate["id"], gate["severity"], gate["title"], gate["fix"]))
            if len(agent) > 40:
                print("  ... %d more" % (len(agent) - 40))
        shown = human if "all" in flags else human[:1]
        if human:
            print("\nWaiting on a person (%d open):" % len(human))
            for _, gate in shown:
                print("  %-8s %-9s %s\n      %s"
                      % (gate["id"], gate["severity"], gate["title"], gate["fix"]))
            if "all" not in flags and len(human) > 1:
                print("  %d more stay held back until this one is answered." % (len(human) - 1))
        print("")
    return 0


def cmd_status(argv):
    run = load_run()
    groups, contexts, index = refresh(run)
    save_run(run)
    flags = parse_flags(argv)
    counts = tally(run)
    print("%sRun %s - %s" % (scope_line(run), run["id"], run["title"]))
    print("Opened %s over %d site(s)." % (run["opened"], len(run["sites"])))
    print("%d gates applied: %d passed, %d fixed, %d n/a, %d blocked, %d disputed, %d OPEN."
          % (len(run["order"]), counts["pass"], counts["fixed"], counts["na"],
             counts["blocked"], counts["disputed"], counts["open"]))
    worst = sorted(run["sites"], key=lambda d: -tally(run, d)["open"])
    print("\nWorst first:")
    for domain in worst[:12]:
        c = tally(run, domain)
        print("  %-24s %3d open  %3d done" % (domain, c["open"], c["pass"] + c["fixed"]))
    if "full" in flags:
        print("\nOpen gates:")
        for item in run["order"]:
            if run["results"][item]["status"] != "open":
                continue
            domain, gid = split_key(item)
            gate = index[gid]
            if gate["check"]["type"] in SELF_SETTLING:
                continue
            print("  %-24s %-8s %s" % (domain, gid, gate["title"]))
            print("      %s" % gate["rule"])
            print("      fix: %s" % gate["fix"])
    return 0


def cmd_gates(argv):
    groups = load_gates()
    flags = parse_flags(argv)
    for group in groups:
        print("\n## %s (%s)" % (group["group"], group["prefix"]))
        if group["description"]:
            print(group["description"])
        for gate in group["gates"]:
            print("  %-8s %-10s %-6s %s" % (gate["id"], gate["check"]["type"],
                                            gate_owner(gate), gate["title"]))
            if "full" in flags:
                print("      %s" % gate["rule"])
                print("      fix: %s" % gate["fix"])
    print("\n%d gates in %d groups." % (len(all_gates(groups)), len(groups)))
    return 0


def cmd_owners(argv):
    """Which gates carry an explicit owner and which are inferred from the fix."""
    groups = load_gates()
    inferred = [g for g in all_gates(groups) if g.get("owner") not in ("human", "agent")]
    for gate in inferred:
        print("  %-8s %-6s %s" % (gate["id"], gate_owner(gate), gate["title"]))
    print("\n%d of %d gates infer their owner from the fix."
          % (len(inferred), len(all_gates(groups))))
    return 0


def cmd_export(argv):
    """Write the standing the console page reads."""
    run = load_run()
    groups, contexts, index = refresh(run)
    flags = parse_flags(argv)
    out = (flags.get("out") or [None])[0]
    if not out:
        root = run.get("registry")
        if not root:
            raise SystemExit("export takes --out <path>, or a run opened against a checkout")
        out = str(Path(root) / "src" / "app" / "data" / "google-standing.json")
    payload = {"generated": now(), "run": run["id"], "sites": []}
    for domain in sorted(run["sites"]):
        site = run["sites"][domain]
        entry = {"site": domain, "name": site["name"], "kind": site["kind"],
                 "totals": tally(run, domain), "groups": []}
        for group in groups:
            items = []
            for gate in group["gates"]:
                item = key(domain, gate["id"])
                if item not in run["results"]:
                    continue
                result = run["results"][item]
                items.append({"id": gate["id"], "title": gate["title"],
                              "status": result["status"], "severity": gate["severity"],
                              "owner": gate_owner(gate), "note": result.get("note", ""),
                              "fix": gate["fix"]})
            if not items:
                continue
            totals = {s: sum(1 for i in items if i["status"] == s)
                      for s in ("pass", "fixed", "na", "open", "blocked", "disputed")}
            totals["applied"] = len(items)
            entry["groups"].append({"group": group["group"], "prefix": group["prefix"],
                                    "totals": totals, "items": items})
        entry["totals"]["applied"] = sum(g["totals"]["applied"] for g in entry["groups"])
        payload["sites"].append(entry)
    target = Path(out).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n")
    run["export_at"] = now()
    save_run(run)
    print("Standing for %d site(s) written to %s" % (len(payload["sites"]), target))
    return 0


def cmd_report(argv):
    run = load_run()
    groups, contexts, index = refresh(run)
    flags = parse_flags(argv)
    counts = tally(run)
    out = []
    out.append("# Google checklist - %s" % run["title"])
    out.append("")
    out.append("Run %s, opened %s, over %d site(s)."
               % (run["id"], run["opened"], len(run["sites"])))
    if scope_line(run):
        out.append("")
        out.append(scope_line(run).strip())
    out.append("")
    changed = [(i, run["results"][i]) for i in run["order"]
               if run["results"][i]["status"] == "fixed"]
    out.append("## Changed")
    out.append("")
    if changed:
        for item, result in changed:
            domain, gid = split_key(item)
            out.append("- **%s** %s - %s" % (domain, gid, result.get("note", "")))
    else:
        out.append("Nothing was changed in this run.")
    out.append("")
    out.append("%d gates applied across %d sites: %d passed, %d fixed, %d not applicable, "
               "%d blocked, %d disputed, %d OPEN."
               % (len(run["order"]), len(run["sites"]), counts["pass"], counts["fixed"],
                  counts["na"], counts["blocked"], counts["disputed"], counts["open"]))
    out.append("")
    for label, status in (("Blocked on an external clock", "blocked"),
                          ("Disputed", "disputed"),
                          ("Answered not applicable", "na")):
        rows = [(i, run["results"][i]) for i in run["order"]
                if run["results"][i]["status"] == status]
        if not rows:
            continue
        out.append("### %s (%d)" % (label, len(rows)))
        out.append("")
        for item, result in rows:
            domain, gid = split_key(item)
            out.append("- **%s** %s - %s" % (domain, gid, result.get("note", "")))
        out.append("")
    out.append("## Sites")
    out.append("")
    out.append("| Site | Kind | Applied | Done | Open | Blocked |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for domain in sorted(run["sites"], key=lambda d: -tally(run, d)["open"]):
        c = tally(run, domain)
        out.append("| %s | %s | %d | %d | %d | %d |"
                   % (domain, run["sites"][domain]["kind"],
                      sum(c.values()), c["pass"] + c["fixed"], c["open"], c["blocked"]))
    out.append("")
    for group in groups:
        rows = []
        for gate in group["gates"]:
            for domain in sorted(run["sites"]):
                item = key(domain, gate["id"])
                if item not in run["results"]:
                    continue
                result = run["results"][item]
                note = (result.get("note") or result.get("auto") or "")[:300].replace("|", "/")
                rows.append("| %s | %s | %s | %s | %s |"
                            % (domain, gate["id"], gate["check"]["type"],
                               result["status"].upper(), note))
        if not rows:
            continue
        out.append("## %s" % group["group"])
        out.append("")
        out.append("| Site | Gate | Check | Status | How it was answered |")
        out.append("| --- | --- | --- | --- | --- |")
        out.extend(rows)
        out.append("")
    if run.get("delegated"):
        out.append("## Handed to another checklist")
        out.append("")
        for row in run["delegated"]:
            out.append("- /%s-checklist: %s" % (row["to"], row["note"]))
        out.append("")
    text = "\n".join(out)
    target = Path((flags.get("out") or [str(RUNS / ("%s-report.md" % run["id"]))])[0])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n")
    print(text)
    outstanding = [i for i in run["order"] if run["results"][i]["status"] == "open"
                   and index[split_key(i)[1]]["check"]["type"] not in SELF_SETTLING]
    if not outstanding:
        run["report_at"] = run.get("report_at") or now()
        settle_self(run, index)
    save_run(run)
    print("\nWritten to %s" % target)
    return 1 if outstanding else 0


def cmd_finish(argv):
    run = load_run()
    flags = parse_flags(argv)
    groups, contexts, index = refresh(run)
    open_ids = [i for i in run["order"] if run["results"][i]["status"] == "open"]
    if open_ids:
        save_run(run)
        print("Cannot close: %d gate(s) unanswered - %s"
              % (len(open_ids), ", ".join(open_ids[:8])), file=sys.stderr)
        return 1
    carried = [i for i in run["order"] if run["results"][i]["status"] == "blocked"]
    run["carried"] = carried
    run["closed"] = now()
    run["closed_reason"] = " ".join(flags.get("note", [])) or "every applicable gate answered"
    save_run(run)
    clear_pointers(run)
    print("Run %s closed. %d gates answered across %d site(s)."
          % (run["id"], len(run["order"]), len(run["sites"])))
    if carried:
        print("%d gate(s) carried, waiting on a Google clock: %s"
              % (len(carried), ", ".join(carried[:8])))
    return 0


# ----------------------------------------------------------------------- hooks

def _payload():
    try:
        return json.load(sys.stdin)
    except (ValueError, OSError):
        return None


def cmd_hook_skill():
    payload = _payload()
    if payload is None:
        return 0
    called = str((payload.get("tool_input") or {}).get("skill") or "")
    if not re.fullmatch(r"(?:.*:)?google-checklist", called):
        return 0
    run = load_run(required=False, own_only=True)
    if run and not run.get("closed"):
        print("A Google run is already open: %s over %d site(s)."
              % (run["id"], len(run["sites"])))
        return 0
    print("No Google run is open. Open one before the work:\n  %s start --all" % TOOL)
    return 0


def cmd_hook_read():
    return 0


def cmd_guard_stop():
    payload = _payload()
    if payload is None:
        return 0
    run = load_run(required=False, session=session_id(payload), own_only=True,
                   here=payload.get("cwd") or os.getcwd())
    if not run or run.get("closed"):
        return 0
    index = gate_index(load_gates())
    outstanding = [i for i in run.get("order", [])
                   if run["results"].get(i, {}).get("status") == "open"]
    if not outstanding:
        return 0
    print("A Google run is open with %d gate(s) unanswered across %d site(s).\n"
          "Answer them, or close the run:\n  %s status --full\n  %s report\n  %s finish"
          % (len(outstanding), len(run.get("sites", {})), TOOL, TOOL, TOOL), file=sys.stderr)
    return 2


def cmd_guard_land():
    payload = _payload()
    if payload is None:
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not LANDING.search(command):
        return 0
    run = load_run(required=False, session=session_id(payload), own_only=True,
                   here=payload.get("cwd") or os.getcwd())
    if not run or run.get("closed"):
        return 0
    # A run with no portfolio checkout names no tree, and a landing in any
    # tree is outside it.
    if not run.get("registry"):
        return 0
    outstanding = [i for i in run.get("order", [])
                   if run["results"].get(i, {}).get("status") == "open"]
    if not outstanding:
        return 0
    print("A Google run over this checkout has %d gate(s) unanswered. Landing now publishes a "
          "standing that is not true.\n  %s status --full\n  %s export"
          % (len(outstanding), TOOL, TOOL), file=sys.stderr)
    return 2


# ------------------------------------------------------------------- unattended

# A probe that could not be taken names the variable it wanted, so the reason a
# gate is parked can be read off the reading itself rather than guessed from the
# gate.
# How often the run is touched while a reading phase runs. Short enough to stay
# inside any window a stop guard reads recency against.
HEARTBEAT_SECONDS = 30

WANTED_CREDENTIAL = re.compile(r"\b(GOOGLE_[A-Z0-9_]+)\b")

WAITS_ON_A_PERSON = (
    "this gate's fix takes a person signed in to the Google account, which no unattended "
    "run can carry out. It clears when that sign-in happens and the reading is filed.")


def _park_note(auto):
    """Why a gate is waiting, and what ends the wait."""
    wanted = sorted(set(WANTED_CREDENTIAL.findall(auto or "")))
    if wanted:
        return ("the reading needs %s, which this machine does not hold, so it was never "
                "taken. It clears once that credential is in the vault and the probe runs "
                "again." % ", ".join(wanted))
    return WAITS_ON_A_PERSON


def _take_readings(run, out):
    """Run the probe script over the run's sites and file what it wrote.

    A probe that raises still writes the readings it took before it fell over, so
    the file is read whatever the exit status. Losing sixteen sites' worth of
    answered gates to one unreachable endpoint is the failure this avoids.
    """
    cmd = [sys.executable, PROBE_TOOL, "all", "--out", str(out)]
    if run.get("registry"):
        cmd += ["--registry", run["registry"]]
    for domain in sorted(run["sites"]):
        cmd += ["--sites", domain]
    # The probe names each site as it reaches it, and sixteen sites take long
    # enough that swallowing that leaves the run looking hung. Its progress goes
    # straight through; only the summary it writes at the end is captured.
    #
    # The run file is kept warm for the same span. A stop guard reads recency to
    # tell a run somebody is working from one walked away from, and a reading
    # phase this long writes nothing, so an untouched run would answer for a
    # working one.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    marker = RUNS / ("%s.json" % run["id"])
    while proc.poll() is None:
        try:
            time.sleep(min(HEARTBEAT_SECONDS, 30))
            if marker.exists():
                os.utime(marker, None)
        except OSError:
            break
    proc.wait()
    if not Path(out).exists():
        raise SystemExit("the probe wrote no readings and exited %d" % proc.returncode)
    payload = json.loads(Path(out).read_text())
    rows = payload.get("probes") or {}
    store = run.setdefault("probe", {})
    for name, row in rows.items():
        if isinstance(row, dict) and "ok" in row and "|" in name:
            store[name] = {"ok": bool(row["ok"]), "detail": str(row.get("detail", "")),
                           "at": str(row.get("at") or now())}
    return len(rows), proc.returncode


def cmd_autorun(argv):
    """Every gate this checklist can settle with nobody watching.

    The split the run already carries makes this possible: a reading a probe can
    take is not the same as a fix a person has to carry out. This takes every
    reading the machine holds a credential for, files it, and parks the rest as
    waiting rather than leaving them to read as unswept.

    What is left open at the end is the work, and it is left open deliberately. A
    failing reading whose fix lives in a repository is not something an unattended
    pass can honestly answer, and recording one as parked would hide it.
    """
    flags = parse_flags(argv)
    run = load_run(required=False, own_only=True)
    if run and not run.get("closed") and "force" not in flags:
        print("Continuing run %s, open on %d site(s)." % (run["id"], len(run["sites"])))
    else:
        if standing_down(flags):
            print(standing_down(flags))
            return 0
        run = open_run(flags, session_id())
        if not run["sites"]:
            raise SystemExit("No sites resolved. Point at the portfolio with "
                             "--registry <checkout>.")
        refresh(run)
        save_run(run)
        print("Run %s open on %d site(s), %d gate(s) applied."
              % (run["id"], len(run["sites"]), len(run["order"])))

    out = (flags.get("out") or [None])[0] or "/tmp/google-probe-%s.json" % run["id"]
    filed, code = _take_readings(run, out)
    groups, contexts, index = refresh(run)
    save_run(run)
    print("%d reading(s) filed from %s%s"
          % (filed, out, "" if code == 0 else " (the probe exited %d)" % code))

    cmd_verify([])
    run = load_run()
    groups, contexts, index = refresh(run)

    parking = flags.get("park") != ["no"]
    parked, work = 0, []
    for item in run["order"]:
        result = run["results"][item]
        if result.get("status") != "open":
            continue
        domain, gid = split_key(item)
        gate = index.get(gid)
        if gate is None or gate["check"]["type"] in SELF_SETTLING:
            continue
        auto = str(result.get("auto", ""))
        unreachable = bool(WANTED_CREDENTIAL.search(auto))
        if parking and (gate_owner(gate) == "human" or unreachable):
            _apply(run, item, "blocked", _park_note(auto))
            parked += 1
            continue
        work.append((domain, gate, auto))
    settle_self(run, index)
    save_run(run)

    print("\n%d gate(s) parked as waiting - on a sign-in, or on a credential this "
          "machine does not hold." % parked)
    if not work:
        print("Nothing is left that an agent could fix in a repository.")
    else:
        print("\n%d gate(s) are open with a fix that lives in a repository:\n" % len(work))
        by_site = {}
        for domain, gate, auto in work:
            by_site.setdefault(domain, []).append((gate, auto))
        for domain in sorted(by_site, key=lambda d: -len(by_site[d])):
            print("  %s (%d)" % (domain, len(by_site[domain])))
            for gate, auto in by_site[domain][:12]:
                print("    %-8s %s" % (gate["id"], gate["title"]))
            if len(by_site[domain]) > 12:
                print("    ... %d more" % (len(by_site[domain]) - 12))
    outstanding = [i for i in run["order"]
                   if run["results"][i]["status"] == "open"
                   and index[split_key(i)[1]]["check"]["type"] in SELF_SETTLING]
    if outstanding:
        print("\nStill answering to a judgement no unattended run can make: %s"
              % ", ".join(sorted({split_key(i)[1] for i in outstanding})))
    print("\n  %s next --site <domain>    what to work first" % TOOL)
    print("  %s report                  render the filled checklist" % TOOL)
    return 0


COMMANDS = {
    "start": cmd_start, "scan": cmd_scan, "sites": cmd_sites, "verify": cmd_verify,
    "resolve": cmd_resolve, "status": cmd_status, "report": cmd_report,
    "finish": cmd_finish, "gates": cmd_gates, "probe": cmd_probe,
    "delegate": cmd_delegate, "next": cmd_next, "export": cmd_export,
    "owners": cmd_owners, "autorun": cmd_autorun,
}
HOOKS = {
    "hook-skill": cmd_hook_skill, "hook-read": cmd_hook_read,
    "guard-stop": cmd_guard_stop, "guard-land": cmd_guard_land,
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    verb, argv = sys.argv[1], sys.argv[2:]
    if verb in HOOKS:
        return HOOKS[verb]()
    if verb not in COMMANDS:
        print("unknown command %r. One of: %s" % (verb, ", ".join(sorted(COMMANDS))),
              file=sys.stderr)
        return 2
    return COMMANDS[verb](argv)


if __name__ == "__main__":
    sys.exit(main())
