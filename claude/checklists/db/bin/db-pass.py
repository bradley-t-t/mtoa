#!/usr/bin/env python3
"""Drives the database and the code that uses it into agreement, gate by gate.

This is a build checklist, not an audit. A gate that is not met is a table to
drop, a policy to write, a job to unschedule, or a prune to install; the only
ways past one are making that change or naming the condition that puts the gate
out of scope. A gate the probe failed cannot be recorded as passing, and a gate
marked fixed reopens if the next probe still fails it.

It asks one question and leaves the neighbouring ones alone: does the database
match what the code actually does. Whether a service answers at all belongs to
the stack checklist, how long a page takes to the performance checklist. A
finding that belongs to one of those is delegated by name and carried in the
report, never resolved here and never dropped.

Two things separate it from its siblings. Its subject is not in the repository,
so every gate that matters answers to a query against the live catalogue and
refuses an attestation - a table created by hand is invisible to any number of
migration files read carefully. And one of its verbs destroys data, so PRC-04
holds the run open until a run that dropped anything says where the rows went.

    start       open a run and print the gates it must satisfy
    probe       file a probe taken by scripts/probe.py
    tables      the table ledger: what is still to be ruled on
    table-clear rule a table as kept, naming what reads it
                --batch <path|-> keeps many at once, a table and a note each
    backup      record where the rows of the tables this run drops were exported
    verify      run every automated check and record its verdict
    resolve     answer a gate: pass, fixed, n/a or disputed, with the evidence
                --batch <path|-> answers many at once, a status and a note each
    delegate    hand a finding to the checklist that owns it
    status      what is still outstanding; --full adds each gate's rule and fix
    report      render the filled checklist
    finish      close the run - refuses while anything is unanswered
    gates       print the registry, whole or filtered

    guard-stop  Stop hook: refuse to end a session with a run still open
    guard-land  PreToolUse hook: refuse `git commit` / `gh pr create` mid-run
    hook-skill  PostToolUse hook: open a run when the skill is invoked
"""

import argparse
import importlib.util
import hashlib
import json
import os
import re
import subprocess
import sys
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
    ENGINE = HOME / ".claude" / "checklists" / "db"
GATES_DIR = ENGINE / "checklist" / "gates"
SKILL = ENGINE
PROBE_TOOL = str(SKILL / "scripts" / "probe.py")
TOOL = str(Path(__file__).resolve())


def _state_root():
    override = os.environ.get("INTELLIGENCE_STATE_DIR")
    if override:
        return Path(override)
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return HOME / ".pass-state"
    return HOME / ".claude" / "state"


RUNS = _state_root() / "db-runs"
CURRENT = RUNS / "current.json"

STATUSES = ("pass", "fixed", "na", "disputed")

# Gates settled by the run's own bookkeeping. An attestation about one of these
# says nothing, so `resolve` refuses to take an answer for them.
SELF_SETTLING = {"run_opened", "all_resolved", "report_emitted", "table_coverage",
                 "probe_filed", "delegation", "backup_filed"}

# Gates that answer to a request that was actually made. A checklist whose whole
# subject is outside the repository cannot let source-reading stand in for it, so
# these refuse `pass` and `na` and take `fixed` or `disputed` only.
PROBE_ONLY = {"tables_referenced", "functions_have_source", "functions_referenced",
              "cron_targets_exist", "db_functions_referenced", "code_targets_exist",
              "rls_enabled", "policies_present", "anon_grants", "bloat",
              "housekeeping_pruned", "autovacuum_recent", "slow_statements",
              "write_amplification", "slots_healthy", "unused_indexes",
              "tables_in_migrations", "migrations_applied"}

# Gates whose answer may never be carried forward. Every one of these reads the
# live catalogue, and this checklist's whole subject is a database that changes
# without the repository moving - a table created by hand this afternoon is
# invisible to the bytes on disk, which is the drift the pairing exists to find.
#
# PROBE_ONLY is already the larger part of it, and those gates could not be
# carried in any case: they refuse `pass` and `na`, and only those two statuses
# are carryable. `manual` joins them for the reason rather than the mechanism -
# SEC-03 asks whether a blanket policy stands in for a real one, so an answer
# given today says nothing about a policy added tomorrow.
#
# What that leaves is two gates of twenty-eight: SEC-05, which greps the
# repository for a service key behind a browser-visible prefix, and MIG-03,
# which reads the migration files for a drop with no backup noted. Both read
# only files under the project root, so both are honestly keyed on those bytes.
# The carry is small on purpose; a third gate that reads only the repository
# would join them without another edit here.
NETWORK_CHECKS = PROBE_ONLY | {"manual"}


def _load_pass_cache():
    """The shared carry-forward store, loaded by path.

    Hooks and skills run this tool as a script from arbitrary directories, so
    nothing arrives through sys.path. A copy installed inside a repository finds
    its own sibling first.
    """
    for path in (ENGINE.parent.parent / "tools" / "pass_cache.py",
                 Path.home() / ".claude" / "tools" / "pass_cache.py"):
        if path.is_file():
            spec = importlib.util.spec_from_file_location("intelligence_pass_cache", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


try:
    pass_cache = _load_pass_cache()
except Exception:
    # A tool that cannot find the store sweeps everything, which is the
    # behaviour carrying answers forward improves on rather than replaces.
    pass_cache = None

_CACHE = None
_CACHE_OPENED = False
_CACHE_OFF = False


def cache_disable():
    """--no-cache: sweep every gate, store nothing."""
    global _CACHE_OFF
    _CACHE_OFF = True


def cache_for(run):
    """The one store a command uses, or None where there is none."""
    global _CACHE, _CACHE_OPENED
    if _CACHE_OFF or pass_cache is None:
        return None
    if not _CACHE_OPENED:
        _CACHE_OPENED = True
        _CACHE = pass_cache.open_for("db", run.get("targets") or [], GATES_DIR, TOOL,
                                     network=NETWORK_CHECKS, self_settling=SELF_SETTLING)
    return _CACHE


CLIENT_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".env", ".example", ""}


def client_source_files(root):
    """Every file SEC-05 reads, listed once so two callers cannot disagree.

    The scope a carried answer is keyed on has to be the bytes the checker
    actually read. Building that list twice is how the two drift: a suffix added
    to the checker and not to the scope would leave a gate carrying its answer
    across an edit to a file it reads.
    """
    base = Path(root)
    if not base.is_dir():
        return []
    found = []
    for path in base.rglob("*"):
        rel = str(path.relative_to(base)).replace(os.sep, "/")
        if "node_modules" in rel or "/.git/" in rel or not path.is_file():
            continue
        if path.suffix not in CLIENT_SUFFIXES:
            continue
        found.append(path)
    return found


def sweep_scope(run):
    """The repository files the carryable gates actually read.

    Only two gates can be served from this, so the scope is what they read and
    nothing else: the files SEC-05 greps, and the migration files MIG-03 reads.
    The probe's `code_files` is a count rather than a list, so the walk has to
    happen here - and it is the same walk the checker makes, through the same
    helper, so the two cannot fall out of step.
    """
    probe = _probe(run)
    root = probe.get("root")
    if not root or not Path(root).is_dir():
        return []
    base = Path(root)
    paths = {str(path.resolve()) for path in client_source_files(root)}
    for rel in probe.get("migration_files") or []:
        paths.add(str((base / rel).resolve()))
    return sorted(paths)


def cache_asking(run, paths):
    """The store, once it holds what this run asks and which files it asks of."""
    cache = cache_for(run)
    if cache is None:
        return None
    try:
        cache.set_scope(paths)
        cache.set_question(kind=run.get("kind"), flags=run.get("flags") or (),
                           url=run.get("project_ref") or "", scope=run.get("scope") or "")
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


# The log relations no product owns. Each is written by the platform and pruned
# by nobody, which is what HLT-02 exists to catch.
HOUSEKEEPING = {("net", "_http_response"), ("cron", "job_run_details")}

BLOAT_BYTES = 64 * 1024 * 1024      # below this a ratio means nothing
BLOAT_RATIO = 40                    # bytes on disk per byte of live row
SLOW_MS = 5000                      # a statement this slow is queueing others
AMPLIFICATION = 50                  # updates per live row
UNUSED_INDEX_BYTES = 1024 * 1024


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


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

    Ownership is the only test, one run or many. A lone run handed back wherever
    the call is made owns every directory on the machine, so one project's open
    run gates a landing in every other project and its readings land in their
    ledger.
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


# ------------------------------------------------------------------ the registry

def load_gates():
    gates, groups = [], {}
    for path in sorted(GATES_DIR.glob("*.json")):
        block = json.loads(path.read_text())
        defaults = block.get("defaults", {})
        groups[block["prefix"]] = {"group": block["group"],
                                   "description": block.get("description", "")}
        for gate in block["gates"]:
            merged = dict(defaults)
            merged.update(gate)
            merged["prefix"] = block["prefix"]
            merged["group"] = block["group"]
            gates.append(merged)
    return gates, groups


def gate_index(gates):
    return {g["id"]: g for g in gates}


# ------------------------------------------------------------------ run state

def _run_at(pointer):
    if not pointer.is_file():
        return None
    try:
        run_id = json.loads(pointer.read_text())["id"]
    except (ValueError, KeyError):
        return None
    path = RUNS / ("%s.json" % run_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def load_run(required=True, session=None, own_only=False, here=None):
    """The calling session's run, or the shared one when no session claims it.

    A run belongs to the session that opened it. The shared pointer is a fallback
    and only when the run it names is unclaimed or claimed by this same session -
    a guard acting on another session's run would refuse work that session is
    still doing, which is why the hooks pass own_only.

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
        if shared and shared.get("session") in (None, mine):
            run = shared
    # The directory the caller names is authoritative. A run reached by any
    # pointer governs that directory only when it owns it: a run that has read
    # nothing here otherwise gates a landing here on another project's gates.
    if run is not None and here is not None:
        run = run_here([run], here)
    if run is None and required:
        raise SystemExit("No database run is open. Start one:\n"
                         "  %s start --target <path>" % TOOL)
    return run


def save_run(run):
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / ("%s.json" % run["id"])).write_text(json.dumps(run, indent=2))
    pointer = json.dumps({"id": run["id"]})
    claim_shared(run, pointer)
    if run.get("session"):
        pointer_for(run["session"], run_target(run)).write_text(pointer)


def clear_run(run):
    clear_pointers(run)


# ------------------------------------------------------------------ the checks
#
# Each returns (verdict, evidence). "unknown" is not a pass: it says the probe
# carried nothing to judge by, and the gate stays open.

def _probe(run):
    return run.get("probe") or {}


def _paired(run):
    return _probe(run).get("paired") or {}


def _db(run):
    return _probe(run).get("database") or {}


def _rows(run, key):
    got = _db(run).get(key)
    return got if isinstance(got, list) else []


def _reading(run, key):
    """The rows a probe step returned, or None when that step did not answer.

    A step that raised records an error object in place of its rows, and the
    pairing fills its own slot with an empty list either way. Only the database
    side still separates a project that has none of something from one whose
    probe could not ask, and the two have opposite verdicts: none is a pass,
    unasked is a gate that stays open.
    """
    got = _db(run).get(key)
    return got if isinstance(got, list) else None


def check_tables_referenced(run):
    tables = _paired(run).get("tables")
    if not tables:
        return "unknown", "no table list in the probe"
    cleared = set(run.get("tables_cleared", {}))
    orphans = [t["name"] for t in tables
               if not t.get("app_references") and t["name"] not in cleared]
    if orphans:
        return "fail", "%d table(s) with no reader: %s" % (len(orphans), ", ".join(orphans))
    return "pass", "all %d tables are named by the code" % len(tables)


def check_functions_have_source(run):
    if _reading(run, "edge_functions") is None:
        return "unknown", "no deployed function list in the probe"
    fns = _paired(run).get("edge_functions") or []
    if not fns:
        return "pass", "the project deploys no functions"
    missing = [f["slug"] for f in fns if not f.get("has_source")]
    if missing:
        return "fail", "%d deployed with no source here: %s" % (len(missing), ", ".join(missing))
    return "pass", "all %d deployed functions have source" % len(fns)


def check_functions_referenced(run):
    if _reading(run, "edge_functions") is None:
        return "unknown", "no deployed function list in the probe"
    fns = _paired(run).get("edge_functions") or []
    if not fns:
        return "pass", "the project deploys no functions"
    cleared = set(run.get("tables_cleared", {}))
    orphans = [f["slug"] for f in fns
               if not f.get("app_references") and f["slug"] not in cleared]
    if orphans:
        return "fail", "%d with no caller: %s" % (len(orphans), ", ".join(orphans))
    return "pass", "every deployed function is called"


def check_cron_targets_exist(run):
    jobs = _reading(run, "cron_jobs")
    if jobs is None:
        return "unknown", "no cron job list in the probe"
    if not jobs:
        return "pass", "the project schedules no jobs"
    tables = {t["name"] for t in _paired(run).get("tables", [])}
    slugs = {f["slug"] for f in _paired(run).get("edge_functions", [])}
    broken = []
    for job in jobs:
        command = job.get("command") or ""
        wanted = set(re.findall(r"public\.([a-z_]+)", command))
        called = set(re.findall(r"/functions/v1/([a-z0-9-]+)", command))
        gone = (wanted - tables) | (called - slugs)
        if gone:
            broken.append("%s -> %s" % (job.get("jobname"), ", ".join(sorted(gone))))
    if broken:
        return "fail", "; ".join(broken)
    return "pass", "all %d jobs point at things that exist" % len(jobs)


def check_db_functions_referenced(run):
    fns = _paired(run).get("functions")
    if not fns:
        return "unknown", "no database function list in the probe"
    cleared = set(run.get("tables_cleared", {}))
    # The rule counts a trigger and a job as callers, and a policy helper is the
    # same shape: called from inside the database and named nowhere in the
    # application. Reading only the source makes every one of them look orphaned.
    orphans = sorted({f["name"] for f in fns
                      if not f.get("app_references") and not f.get("sql_references")
                      and f["name"] not in cleared})
    if orphans:
        return "fail", "%d with no caller: %s" % (len(orphans), ", ".join(orphans))
    return "pass", "every database function is called"


def check_code_targets_exist(run):
    """The drift read from the code's side rather than the database's.

    A `.from('x')` naming a table that no longer exists fails at runtime, so this
    one is the half that produces user-visible errors rather than quiet cost.
    """
    probe = _probe(run)
    root = probe.get("root")
    if not root or not Path(root).is_dir():
        return "unknown", "the probe recorded no readable root"
    tables = {t["name"] for t in _paired(run).get("tables", [])}
    if not tables:
        return "unknown", "no table list in the probe"
    called, buckets = set(), set()
    pattern = re.compile(r"""\.from\(\s*['"`]([a-z_][a-z0-9_]*)['"`]""")
    # `storage.from('bucket')` reads a storage bucket rather than a table, and
    # the two are told apart by the receiver rather than the name. The client is
    # routinely broken across lines, so this spans them rather than looking
    # immediately behind the dot.
    bucket_pattern = re.compile(
        r"""storage\s*\.\s*from\(\s*['"`]([a-z_][a-z0-9_]*)['"`]""", re.S)
    skip = {"node_modules", ".git", "dist", "build", "supabase/migrations"}
    for path in Path(root).rglob("*"):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx", ".mjs"}:
            continue
        if any(part in rel for part in skip):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        called |= set(pattern.findall(text))
        buckets |= set(bucket_pattern.findall(text))
    missing = {name for name in called - buckets if name not in tables}
    if missing:
        return "fail", "code reads tables that do not exist: %s" % ", ".join(sorted(missing))
    return "pass", "every table the code reads exists"


def check_rls_enabled(run):
    tables = _paired(run).get("tables")
    if not tables:
        return "unknown", "no table list in the probe"
    open_tables = [t["name"] for t in tables if not t.get("rls_enabled")]
    if open_tables:
        return "fail", "RLS off on: %s" % ", ".join(open_tables)
    return "pass", "RLS is on for all %d tables" % len(tables)


def check_policies_present(run):
    tables = _paired(run).get("tables")
    if not tables:
        return "unknown", "no table list in the probe"
    cleared = set(run.get("tables_cleared", {}))
    bare = [t["name"] for t in tables
            if t.get("rls_enabled") and not t.get("policy_count")
            and t["name"] not in cleared]
    if bare:
        return "fail", "RLS on with no policy: %s" % ", ".join(bare)
    return "pass", "every protected table has a policy"


# Writing is the one thing a signed-out visitor legitimately does: a registration
# form, a contact form, a waitlist. Reading back is not, and neither is changing
# or removing what is already there.
ANON_MAY = {"INSERT"}


def check_anon_grants(run):
    tables = _paired(run).get("tables")
    if not tables:
        return "unknown", "no table list in the probe"
    reached, writable = [], []
    for row in tables:
        held = row.get("anon_privileges")
        if held is None:
            # An older probe recorded grantee names without their privileges, and
            # the name alone cannot tell a public form from a published table.
            if "anon" in (row.get("grantees") or []):
                reached.append(row["name"])
            continue
        beyond = sorted(set(held) - ANON_MAY)
        if beyond:
            reached.append("%s (%s)" % (row["name"], ", ".join(beyond).lower()))
        elif held:
            writable.append(row["name"])
    if reached:
        return "fail", "anon reaches: %s" % ", ".join(reached)
    if writable:
        return "pass", "anon may only insert, into: %s" % ", ".join(writable)
    return "pass", "no table is granted to anon"


def check_service_key_client_side(run):
    probe = _probe(run)
    root = probe.get("root")
    if not root or not Path(root).is_dir():
        return "unknown", "the probe recorded no readable root"
    marker = re.compile(r"(?:REACT_APP_|VITE_|NEXT_PUBLIC_|PUBLIC_)[A-Z_]*SERVICE_ROLE")
    hits = []
    for path in client_source_files(root):
        try:
            if marker.search(path.read_text(errors="replace")):
                hits.append(str(path.relative_to(root)).replace(os.sep, "/"))
        except OSError:
            continue
    if hits:
        return "fail", "service-role key behind a client prefix in: %s" % ", ".join(hits[:5])
    return "pass", "no service-role key sits behind a client-side prefix"


def check_bloat(run):
    rows = _rows(run, "system_tables") + [
        {"schema_name": "public", "table_name": t["name"], "live_rows": t.get("live_rows"),
         "total_bytes": t.get("total_bytes")}
        for t in _paired(run).get("tables", [])]
    if not rows:
        return "unknown", "no relation sizes in the probe"
    bad = []
    for row in rows:
        size = int(row.get("total_bytes") or 0)
        live = int(row.get("live_rows") or 0)
        if size < BLOAT_BYTES:
            continue
        # A row is generously assumed to be 1 kB; anything past the ratio is space
        # the rows cannot account for however wide they are.
        if size > max(live, 1) * 1024 * BLOAT_RATIO:
            bad.append("%s.%s %d rows in %.0f MB" % (
                row.get("schema_name", "public"), row["table_name"], live, size / 1048576))
    if bad:
        return "fail", "; ".join(bad)
    return "pass", "no relation is mostly empty space"


def check_housekeeping_pruned(run):
    jobs = _rows(run, "cron_jobs")
    system = _rows(run, "system_tables")
    if not system:
        return "unknown", "no system relation sizes in the probe"
    commands = " ".join((j.get("command") or "") for j in jobs)
    unpruned = []
    for schema, table in sorted(HOUSEKEEPING):
        present = any(r.get("schema_name") == schema and r.get("table_name") == table
                      for r in system)
        if present and table not in commands:
            unpruned.append("%s.%s" % (schema, table))
    if unpruned:
        return "fail", "nothing prunes: %s" % ", ".join(unpruned)
    return "pass", "the housekeeping relations are pruned on a schedule"


def check_autovacuum_recent(run):
    tables = _paired(run).get("tables")
    raw = _rows(run, "tables")
    if not raw:
        return "unknown", "no vacuum timestamps in the probe"
    stale = []
    for row in raw:
        dead = int(row.get("dead_rows") or 0)
        live = int(row.get("live_rows") or 0)
        if dead > 1000 and dead > live:
            stale.append("%s (%d dead / %d live)" % (row["table_name"], dead, live))
    if stale:
        return "fail", "; ".join(stale)
    return "pass", "no table is carrying more dead rows than live ones"


def check_slow_statements(run):
    rows = _rows(run, "slowest")
    if not rows:
        return "unknown", "pg_stat_statements carried nothing"
    bad = []
    for row in rows:
        worst = float(row.get("max_ms") or 0)
        if worst >= SLOW_MS:
            bad.append("%.0f s: %s" % (worst / 1000, (row.get("query") or "")[:70].strip()))
    if bad:
        return "fail", "; ".join(bad[:4])
    return "pass", "no statement's worst run approaches the timeout"


def check_write_amplification(run):
    rows = _rows(run, "tables")
    if not rows:
        return "unknown", "no write counters in the probe"
    # n_tup_upd is not in the schema query; the gate reads it when a probe
    # carries it and says so plainly when it does not.
    counted = [r for r in rows if r.get("updates") is not None]
    if not counted:
        return "unknown", "the probe carried no update counters"
    bad = ["%s (%d updates / %d rows)" % (r["table_name"], r["updates"], r.get("live_rows") or 0)
           for r in counted
           if int(r["updates"]) > max(int(r.get("live_rows") or 1), 1) * AMPLIFICATION]
    if bad:
        return "fail", "; ".join(bad)
    return "pass", "no table is rewritten out of proportion to its size"


def check_slots_healthy(run):
    slots = _rows(run, "replication_slots")
    if not slots:
        return "pass", "the project has no replication slots"
    bad = []
    for slot in slots:
        if not slot.get("active"):
            bad.append("%s inactive" % slot.get("slot_name"))
        elif int(slot.get("behind_bytes") or 0) > 512 * 1024 * 1024:
            bad.append("%s %d MB behind" % (slot.get("slot_name"),
                                            int(slot["behind_bytes"]) / 1048576))
    if bad:
        return "fail", "; ".join(bad)
    return "pass", "every slot is active and caught up"


def check_unused_indexes(run):
    rows = _rows(run, "indexes")
    if not rows:
        return "unknown", "no index counters in the probe"
    bad = ["%s.%s (%.1f MB)" % (r["table_name"], r["index_name"], int(r["bytes"]) / 1048576)
           for r in rows
           if int(r.get("scans") or 0) == 0 and int(r.get("bytes") or 0) > UNUSED_INDEX_BYTES]
    if bad:
        return "fail", "never scanned: %s" % ", ".join(bad[:6])
    return "pass", "every index of consequence has been scanned"


def check_tables_in_migrations(run):
    tables = _paired(run).get("tables")
    if not tables:
        return "unknown", "no table list in the probe"
    missing = [t["name"] for t in tables if not t.get("migration_references")]
    if missing:
        return "fail", "%d table(s) no migration creates: %s" % (
            len(missing), ", ".join(missing[:12]))
    return "pass", "every table is created by a migration"


def check_migrations_applied(run):
    probe = _probe(run)
    files = probe.get("migration_files") or []
    root = probe.get("root")
    tables = {t["name"] for t in _paired(run).get("tables", [])}
    if not files or not root or not tables:
        return "unknown", "the probe carried no migrations or no table list"
    created = re.compile(r"create table (?:if not exists )?(?:public\.)?([a-z_]+)", re.I)
    dropped = re.compile(r"drop table (?:if exists )?(?:public\.)?([a-z_]+)", re.I)
    # Drops are gathered across the whole directory before anything is judged. A
    # later migration retiring a table is the newer truth about it, and reading
    # each file alone reports every retirement as an unapplied creation.
    sources = {}
    gone = set()
    for rel in files:
        try:
            sources[rel] = (Path(root) / rel).read_text(errors="replace")
        except OSError:
            continue
        gone |= set(dropped.findall(sources[rel]))
    missing = []
    for rel, text in sources.items():
        for name in created.findall(text):
            if name not in tables and name not in gone:
                missing.append("%s creates %s" % (rel, name))
    if missing:
        return "fail", "; ".join(missing[:6])
    return "pass", "every migration's tables are present"


def check_drops_document_backup(run):
    probe = _probe(run)
    root = probe.get("root")
    files = probe.get("migration_files") or []
    if not root or not files:
        return "unknown", "the probe carried no migrations"
    silent = []
    for rel in files:
        try:
            text = (Path(root) / rel).read_text(errors="replace")
        except OSError:
            continue
        if re.search(r"drop table", text, re.I) and not re.search(
                r"backup|export|archive|restore", text, re.I):
            silent.append(rel)
    if silent:
        return "fail", "drops with no backup noted: %s" % ", ".join(silent)
    return "pass", "every destructive migration says where the rows went"


CHECKS = {
    "tables_referenced": check_tables_referenced,
    "functions_have_source": check_functions_have_source,
    "functions_referenced": check_functions_referenced,
    "cron_targets_exist": check_cron_targets_exist,
    "db_functions_referenced": check_db_functions_referenced,
    "code_targets_exist": check_code_targets_exist,
    "rls_enabled": check_rls_enabled,
    "policies_present": check_policies_present,
    "anon_grants": check_anon_grants,
    "service_key_client_side": check_service_key_client_side,
    "bloat": check_bloat,
    "housekeeping_pruned": check_housekeeping_pruned,
    "autovacuum_recent": check_autovacuum_recent,
    "slow_statements": check_slow_statements,
    "write_amplification": check_write_amplification,
    "slots_healthy": check_slots_healthy,
    "unused_indexes": check_unused_indexes,
    "tables_in_migrations": check_tables_in_migrations,
    "migrations_applied": check_migrations_applied,
    "drops_document_backup": check_drops_document_backup,
}


# ------------------------------------------------------------------ self-settling

def settle_self(run, kind, index):
    if kind == "run_opened":
        return "pass", "run %s opened %s" % (run["id"], run["opened"])
    if kind == "probe_filed":
        return ("pass", "probe filed %s" % run["probe_at"]) if run.get("probe") \
            else ("fail", "no probe has been filed")
    if kind == "table_coverage":
        tables = _paired(run).get("tables") or []
        if not tables:
            # A probe that reached a database and found nothing is a complete
            # answer; a run with no probe behind it is not. Reading both as the
            # same thing leaves a project with no database unable to close on a
            # ledger it has already satisfied.
            if not run.get("probe"):
                return "fail", "no probe, so no table ledger"
            return "pass", "the probe found no tables, so there are none to rule on"
        cleared = set(run.get("tables_cleared", {}))
        dropped = set(run.get("dropped", []))
        left = [t["name"] for t in tables if t["name"] not in cleared | dropped]
        return ("fail", "%d table(s) not ruled on: %s" % (len(left), ", ".join(left[:10]))) \
            if left else ("pass", "all %d tables ruled on" % len(tables))
    if kind == "backup_filed":
        if not run.get("dropped"):
            return "pass", "this run dropped nothing"
        return ("pass", "backup at %s" % run["backup"]) if run.get("backup") \
            else ("fail", "%d table(s) dropped with no backup recorded" % len(run["dropped"]))
    if kind == "delegation":
        return ("pass", "%d handover(s)" % len(run.get("delegated", []))) \
            if run.get("delegated") or run.get("delegated_none") \
            else ("fail", "nothing delegated and no `delegate --none`")
    if kind == "all_resolved":
        # A gate the sweep passed is settled, exactly as one answered by hand is.
        # Counting only the written answers reports every automatic pass as open,
        # which reads as a run with far more left to do than it has.
        verdicts = run.get("verdicts") or {}
        open_ids = [g for g in run["order"]
                    if g not in run["answers"]
                    and verdicts.get(g, {}).get("verdict") != "pass"
                    and index[g]["check"]["type"] != "all_resolved"]
        return ("fail", "%d gate(s) open" % len(open_ids)) if open_ids \
            else ("pass", "every other gate is answered")
    if kind == "report_emitted":
        return ("pass", "rendered %s" % run["reported"]) if run.get("reported") \
            else ("fail", "the checklist has not been rendered")
    return "unknown", "no automated check"


# ------------------------------------------------------------------ verbs

def cmd_start(args):
    gates, _ = load_gates()
    run = {
        "id": "db-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "opened": now(),
        "session": session_id(),
        "target": str(Path(args.target).resolve()),
        "kind": args.kind,
        "title": args.title,
        "project_ref": args.project_ref,
        "order": [g["id"] for g in gates],
        "answers": {},
        "verdicts": {},
        "tables_cleared": {},
        "dropped": [],
        "delegated": [],
    }
    save_run(run)
    print("Opened %s over %s\n" % (run["id"], run["target"]))
    print("%d gates. Take the probe first - every gate that matters answers to it:\n"
          "  %s all --root %s%s --out /tmp/db-probe.json\n"
          "  %s probe --file /tmp/db-probe.json\n"
          % (len(gates), PROBE_TOOL, run["target"],
             " --project-ref %s" % args.project_ref if args.project_ref else "", TOOL))
    for gate in gates:
        print("  %-8s %s" % (gate["id"], gate["title"]))
    return 0


def cmd_probe(args):
    run = load_run()
    data = json.loads(Path(args.file).read_text())
    run["probe"] = data
    run["probe_at"] = now()
    save_run(run)
    paired = data.get("paired", {})
    print("Filed a probe of %s (%d tables, %d deployed functions)." % (
        data.get("project_ref") or "an unnamed project",
        len(paired.get("tables", [])), len(paired.get("edge_functions", []))))
    if data.get("error"):
        print("The probe reported: %s" % data["error"])
    return 0


def cmd_tables(args):
    run = load_run()
    tables = _paired(run).get("tables") or []
    if not tables:
        print("No probe filed, so there is no table ledger yet.")
        return 1
    cleared = run.get("tables_cleared", {})
    dropped = set(run.get("dropped", []))
    for table in sorted(tables, key=lambda t: t["name"]):
        name = table["name"]
        if name in dropped:
            mark, note = "dropped", ""
        elif name in cleared:
            mark, note = "kept", cleared[name]
        else:
            mark = "OPEN"
            note = ("no reader in the code" if not table.get("app_references")
                    else "read by %s" % table["app_references"][0])
        print("  %-8s %-38s %s" % (mark, name, note))
    left = [t["name"] for t in tables if t["name"] not in cleared and t["name"] not in dropped]
    print("\n%d of %d still to rule on." % (len(left), len(tables)))
    return 0


def cmd_table_clear(args):
    run = load_run()
    cleared = run.setdefault("tables_cleared", {})
    if args.batch:
        problems, taken = [], 0
        for n, row in enumerate(read_batch(args.batch, "{table, note}"), 1):
            if not isinstance(row, dict):
                problems.append("row %d is not an object" % n)
                continue
            name = str(row.get("table") or row.get("name") or "").strip()
            note = str(row.get("note", "")).strip()
            if not name:
                problems.append("row %d names no table" % n)
            elif not note:
                problems.append("%s needs the reader named in its note" % name)
            else:
                cleared[name] = note
                taken += 1
        save_run(run)
        print("Kept %d table(s)." % taken)
        for problem in problems:
            print("  refused: %s" % problem)
        return 1 if problems else 0
    if not args.name:
        raise SystemExit("table-clear takes a table name, or --batch")
    if not args.note:
        raise SystemExit("A cleared table needs the reader named: --note \"read by X\"")
    cleared[args.name] = args.note
    save_run(run)
    print("Kept %s: %s" % (args.name, args.note))
    return 0


def cmd_backup(args):
    run = load_run()
    run["backup"] = args.path
    run["dropped"] = sorted(set(run.get("dropped", [])) | set(args.tables or []))
    save_run(run)
    print("Recorded a backup at %s covering %d table(s)." % (args.path, len(run["dropped"])))
    return 0


def cmd_delegate(args):
    run = load_run()
    if args.none:
        run["delegated_none"] = now()
    else:
        if not args.to or not args.note:
            raise SystemExit("A handover needs --to <checklist> and --note \"<what>\"")
        run.setdefault("delegated", []).append(
            {"to": args.to, "note": args.note, "at": now()})
    save_run(run)
    print("Recorded." if not args.none else "Recorded that nothing was delegated.")
    return 0


def cmd_verify(args):
    run = load_run()
    gates, _ = load_gates()
    index = gate_index(gates)
    cache = cache_asking(run, sweep_scope(run))
    carried = 0
    for gate in gates:
        kind = gate["check"]["type"]
        if kind in SELF_SETTLING:
            verdict, evidence = settle_self(run, kind, index)
        elif kind in CHECKS:
            answer = run["answers"].get(gate["id"])
            # A gate marked fixed is swept again whatever is stored: finding out
            # whether the change took is the whole point of the mark.
            hit = None
            if cache is not None and (not answer or answer.get("status") != "fixed"):
                hit = cache.carry_gate(gate)
            if hit and hit.get("status") == "pass":
                carried += 1
                verdict = "pass"
                evidence = "cached from run %s at %s (gate and sources unchanged)" % (
                    hit.get("run") or "?", hit.get("at") or "?")
            else:
                verdict, evidence = CHECKS[kind](run)
                if cache is not None:
                    # Only a pass is stored, and anything else drops what was
                    # stored before it, so a gate that has started failing
                    # cannot be served from the record of the day it passed.
                    cache.record_gate(gate, verdict, evidence,
                                      run_id=run.get("id", "?"))
        else:
            continue
        run.setdefault("verdicts", {})[gate["id"]] = {
            "verdict": verdict, "evidence": evidence, "at": now()}
        # A gate the sweep now fails cannot keep a passing answer from before it.
        answer = run["answers"].get(gate["id"])
        if verdict == "fail" and answer and answer["status"] in ("pass", "fixed"):
            del run["answers"][gate["id"]]
    cache_save(cache)
    save_run(run)
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    for gid, got in run["verdicts"].items():
        counts[got["verdict"]] = counts.get(got["verdict"], 0) + 1
        if got["verdict"] != "pass":
            print("  %-8s %-7s %s" % (gid, got["verdict"], got["evidence"]))
    print("\n%d pass, %d fail, %d unknown." % (counts["pass"], counts["fail"], counts["unknown"]))
    if carried:
        print("%d gate(s) carried from an earlier run." % carried)
    return 0


def _answer(run, index, gid, status, note):
    if gid not in index:
        return "%s is not a gate" % gid
    kind = index[gid]["check"]["type"]
    if kind in SELF_SETTLING:
        return "%s answers to the run's own state; it cannot be answered by hand" % gid
    if status not in STATUSES:
        return "%s is not one of %s" % (status, ", ".join(STATUSES))
    if kind in PROBE_ONLY and status in ("pass", "na"):
        return ("%s answers to the probe, not to an attestation. Change the database "
                "and re-verify (fixed), or dispute the reading." % gid)
    verdict = (run.get("verdicts") or {}).get(gid, {}).get("verdict")
    if verdict == "fail" and status in ("pass", "na"):
        return "%s is failing the sweep; it cannot be recorded as %s" % (gid, status)
    if not note:
        return "%s needs the evidence in --note" % gid
    run["answers"][gid] = {"status": status, "note": note, "at": now()}
    return None


def read_batch(source, shape):
    """The JSON rows of a --batch call, in the shape the sibling checklists take.

    One format across all five matters more than it looks: `/checklist` answers
    every run in the same pass from prompts written once, and a tool taking its
    rows some other way would be the one nobody remembered the syntax for.
    """
    raw = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    try:
        rows = json.loads(raw)
    except ValueError as exc:
        raise SystemExit("--batch expects JSON: a list of %s. %s" % (shape, exc))
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise SystemExit("--batch expects a non-empty list of %s" % shape)
    return rows


def cmd_resolve(args):
    run = load_run()
    gates, _ = load_gates()
    index = gate_index(gates)
    problems, taken, seen = [], 0, set()
    if args.batch:
        for n, row in enumerate(read_batch(args.batch, "{id, status, note}"), 1):
            if not isinstance(row, dict):
                problems.append("row %d is not an object" % n)
                continue
            gid = str(row.get("id", "")).strip()
            if not gid:
                problems.append("row %d has no id" % n)
                continue
            if gid in seen:
                problems.append("%s appears twice" % gid)
                continue
            seen.add(gid)
            failed = _answer(run, index, gid, str(row.get("status", "")).strip(),
                             str(row.get("note", "")))
            if failed:
                problems.append(failed)
            else:
                taken += 1
    else:
        failed = _answer(run, index, args.gate, args.status, args.note)
        if failed:
            problems.append(failed)
        else:
            taken = 1
    save_run(run)
    print("Recorded %d answer(s)." % taken)
    for problem in problems:
        print("  refused: %s" % problem)
    return 1 if problems else 0


def cmd_status(args):
    run = load_run()
    gates, groups = load_gates()
    index = gate_index(gates)
    for gate in gates:
        gid = gate["id"]
        answer = run["answers"].get(gid)
        verdict = (run.get("verdicts") or {}).get(gid, {})
        if answer:
            mark = answer["status"]
        elif verdict.get("verdict") == "pass":
            mark = "sweep-pass"
        else:
            mark = "OPEN"
        if mark in ("pass", "fixed", "na", "sweep-pass") and not args.full:
            continue
        print("  %-8s %-11s %s" % (gid, mark, gate["title"]))
        if verdict.get("evidence"):
            print("           %s" % verdict["evidence"])
        if args.full:
            print("           rule: %s" % gate["rule"])
            print("           fix:  %s" % gate.get("fix", "-"))
    settled = sum(1 for g in gates
                  if g["id"] in run["answers"]
                  or (run.get("verdicts") or {}).get(g["id"], {}).get("verdict") == "pass")
    print("\n%d of %d gates settled." % (settled, len(gates)))
    return 0


def _open_gates(run, gates):
    open_ids = []
    for gate in gates:
        gid = gate["id"]
        if gid in run["answers"]:
            continue
        if (run.get("verdicts") or {}).get(gid, {}).get("verdict") == "pass":
            continue
        open_ids.append(gid)
    return open_ids


def cmd_report(args):
    run = load_run()
    gates, groups = load_gates()
    lines = ["# Database checklist - %s" % run["id"],
             "", "Target: %s" % run["target"],
             "Project: %s" % (run.get("project_ref") or "not named"),
             "Opened: %s" % run["opened"], ""]
    current = None
    na_section = []
    for gate in gates:
        if gate["group"] != current:
            current = gate["group"]
            lines += ["", "## %s" % current, ""]
        gid = gate["id"]
        answer = run["answers"].get(gid)
        verdict = (run.get("verdicts") or {}).get(gid, {})
        if answer:
            mark = answer["status"]
            evidence = answer["note"]
        elif verdict.get("verdict") == "pass":
            mark, evidence = "pass", verdict.get("evidence", "")
        else:
            mark, evidence = "OPEN", verdict.get("evidence", "")
        lines.append("- **%s** %s - %s" % (gid, gate["title"], mark))
        if evidence:
            lines.append("  - %s" % evidence)
        if mark == "na":
            na_section.append("- %s %s - %s" % (gid, gate["title"], evidence))
    if run.get("dropped"):
        lines += ["", "## Dropped", "",
                  "%d table(s): %s" % (len(run["dropped"]), ", ".join(run["dropped"])),
                  "Backup: %s" % (run.get("backup") or "NONE RECORDED")]
    if run.get("delegated"):
        lines += ["", "## Handed over", ""]
        lines += ["- %s -> %s" % (d["to"], d["note"]) for d in run["delegated"]]
    lines += ["", "## Not applicable", ""]
    lines += na_section or ["Nothing was answered not applicable."]
    text = "\n".join(lines)
    print(text)
    run["reported"] = now()
    run["report"] = text
    save_run(run)
    return 0


def cmd_finish(args):
    run = load_run()
    gates, _ = load_gates()
    index = gate_index(gates)
    for gate in gates:
        kind = gate["check"]["type"]
        if kind in SELF_SETTLING:
            verdict, evidence = settle_self(run, kind, index)
            run.setdefault("verdicts", {})[gate["id"]] = {
                "verdict": verdict, "evidence": evidence, "at": now()}
    save_run(run)
    open_ids = _open_gates(run, gates)
    if open_ids:
        print("Refusing to close: %d gate(s) open.\n  %s\n\n%s status --full lists them."
              % (len(open_ids), ", ".join(open_ids), TOOL), file=sys.stderr)
        return 1
    run["closed"] = now()
    save_run(run)
    clear_run(run)
    print("Closed %s. %d gates answered." % (run["id"], len(gates)))
    return 0


def cmd_gates(args):
    gates, groups = load_gates()
    for gate in gates:
        if args.filter and args.filter.lower() not in (
                gate["id"] + gate["title"] + gate["rule"]).lower():
            continue
        print("%-8s %s\n         %s\n         fix: %s\n"
              % (gate["id"], gate["title"], gate["rule"], gate.get("fix", "-")))
    return 0


# ------------------------------------------------------------------ hooks
#
# Every hook exits 0 when no run belongs to the calling session, so a session
# that never opened one is never obstructed by this skill.

def _payload():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}


def cmd_hook_skill(args):
    payload = _payload()
    called = str((payload.get("tool_input") or {}).get("skill") or "")
    if not re.fullmatch(r"(?:.*:)?db-checklist", called):
        return 0
    if load_run(required=False, session=session_id(payload), own_only=True):
        return 0
    print("A database run belongs with this invocation. Open it before the work:\n"
          "  %s start --target <path> --project-ref <ref>\n"
          "Then take the probe; every gate that matters answers to it." % TOOL)
    return 0


def cmd_guard_stop(args):
    payload = _payload()
    if payload.get("stop_hook_active"):
        return 0
    run = load_run(required=False, session=session_id(payload), own_only=True,
                   here=payload.get("cwd") or os.getcwd())
    if not run:
        return 0
    gates, _ = load_gates()
    open_ids = _open_gates(run, gates)
    if not open_ids:
        return 0
    print(json.dumps({
        "decision": "block",
        "reason": "The database run %s is still open with %d gate(s) unanswered: %s.\n"
                  "Answer them or close the run:\n  %s status --full\n  %s finish"
                  % (run["id"], len(open_ids), ", ".join(open_ids[:8]), TOOL, TOOL)}))
    return 0


def cmd_guard_land(args):
    payload = _payload()
    run = load_run(required=False, session=session_id(payload), own_only=True,
                   here=payload.get("cwd") or os.getcwd())
    if not run:
        return 0
    gates, _ = load_gates()
    open_ids = _open_gates(run, gates)
    if not open_ids:
        return 0
    print("BLOCKED: database run %s is open over %s with %d gate(s) unanswered.\n"
          "  %s\n\nAnswer them, or close the run before landing:\n  %s status --full"
          % (run["id"], run["target"], len(open_ids), ", ".join(open_ids[:8]), TOOL),
          file=sys.stderr)
    return 2


def main():
    if "--no-cache" in sys.argv[1:]:
        # Lifted out before argparse sees it: the flag applies to every verb,
        # and declaring it on each subparser would let one be forgotten.
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--no-cache"]
        cache_disable()
    parser = argparse.ArgumentParser(prog="db-pass.py", description=__doc__)
    sub = parser.add_subparsers(dest="verb", required=True)

    start = sub.add_parser("start"); start.add_argument("--target", required=True)
    start.add_argument("--project-ref")
    # --kind and --title are taken for the same reason the batch shape is: the
    # aggregator opens all five runs from one template, and a tool that rejected
    # the arguments its siblings accept would break the pass rather than its own
    # invocation. Neither changes which gates apply here; the probe decides that.
    start.add_argument("--kind", default="database")
    start.add_argument("--title")
    start.set_defaults(fn=cmd_start)

    probe = sub.add_parser("probe"); probe.add_argument("--file", required=True)
    probe.set_defaults(fn=cmd_probe)

    tables = sub.add_parser("tables"); tables.set_defaults(fn=cmd_tables)

    clear = sub.add_parser("table-clear"); clear.add_argument("name", nargs="?")
    clear.add_argument("--note"); clear.add_argument("--batch")
    clear.set_defaults(fn=cmd_table_clear)

    backup = sub.add_parser("backup"); backup.add_argument("--path", required=True)
    backup.add_argument("--tables", nargs="*"); backup.set_defaults(fn=cmd_backup)

    delegate = sub.add_parser("delegate"); delegate.add_argument("--to")
    delegate.add_argument("--note"); delegate.add_argument("--none", action="store_true")
    delegate.set_defaults(fn=cmd_delegate)

    verify = sub.add_parser("verify"); verify.set_defaults(fn=cmd_verify)

    resolve = sub.add_parser("resolve")
    resolve.add_argument("gate", nargs="?"); resolve.add_argument("status", nargs="?")
    resolve.add_argument("note", nargs="?"); resolve.add_argument("--batch")
    resolve.set_defaults(fn=cmd_resolve)

    status = sub.add_parser("status"); status.add_argument("--full", action="store_true")
    status.set_defaults(fn=cmd_status)

    report = sub.add_parser("report"); report.set_defaults(fn=cmd_report)
    finish = sub.add_parser("finish"); finish.set_defaults(fn=cmd_finish)

    gates = sub.add_parser("gates"); gates.add_argument("filter", nargs="?")
    gates.set_defaults(fn=cmd_gates)

    for name, fn in (("hook-skill", cmd_hook_skill), ("guard-stop", cmd_guard_stop),
                     ("guard-land", cmd_guard_land)):
        hook = sub.add_parser(name); hook.set_defaults(fn=fn)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
