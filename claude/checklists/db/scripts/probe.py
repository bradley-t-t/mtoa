#!/usr/bin/env python3
"""Reads a project's database and the code that is supposed to use it, and writes
what it found as one JSON file for db-pass.py to answer gates from.

The pairing is the whole point. A schema read on its own says a table exists; a
repository read on its own says a name is absent. Only the two together say a
table is standing that nothing reads, which is the finding this checklist exists
to produce, and it is the one that never shows up in either half alone.

Credentials are read at the point of use and never written to the output: the
Supabase access token comes from SUPABASE_ACCESS_TOKEN, and every value that
looks like a key is scrubbed from anything quoted back.

    probe.py all --root <path> --project-ref <ref> --out /tmp/db-probe.json

--project-ref may be omitted when the repository names one, in which case the
first ref found in a .env, a config file or a committed URL is used, and the
output records which file it came from so a wrong guess is visible rather than
silent.
"""

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import sys
import urllib.request
from pathlib import Path

API = "https://api.supabase.com/v1"

# Supabase sits behind a WAF that refuses urllib's default agent outright. A run
# that does not set this reads as "no access to the project" while the token is
# perfectly good, which has already cost one investigation a wrong conclusion.
AGENT = "curl/8.7.1"

SKIP_DIRS = {"node_modules", ".git", ".next", ".nuxt", "dist", "build", "out",
             "vendor", "__pycache__", ".venv", "coverage", ".turbo", ".cache",
             ".claude", ".svelte-kit", ".output", "supabase/.branches"}

CODE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
            ".astro", ".html", ".py", ".sql", ".json"}

# A key shape reaching a file on disk is a leak whoever reads the file inherits,
# so the scrub runs over everything quoted back rather than over a chosen field.
SECRET_SHAPES = [
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"sbp_[A-Za-z0-9]{20,}"),
    re.compile(r"sb_secret_[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?:ghp|gho|ghs)_[A-Za-z0-9]{20,}"),
    re.compile(r"service_role[\"'\s:=]+[A-Za-z0-9._-]{20,}"),
]

REF_PATTERNS = [
    re.compile(r"https://([a-z]{20})\.supabase\.co"),
    re.compile(r"project[_-]?ref[\"'\s:=]+([a-z]{20})"),
]


def scrub(value):
    """Replace anything key-shaped with a marker, at any depth."""
    if isinstance(value, str):
        for shape in SECRET_SHAPES:
            value = shape.sub("[redacted]", value)
        return value
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def api(path, token, method="GET", body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bearer " + token, "User-Agent": AGENT}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def sql(ref, token, query):
    return api("/projects/%s/database/query" % ref, token, "POST", {"query": query})


# ------------------------------------------------------------------ the code

def code_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix in CODE_EXT:
                yield Path(dirpath) / name


def code_index(root):
    """Every code file's text, keyed by path relative to the root.

    Migrations are held separately. A table named only in the migration that
    created it is not a table the application uses, and folding the two together
    is how a dead table reads as a live one.
    """
    root = Path(root).resolve()
    app, migrations = {}, {}
    for path in code_files(root):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(root))
        if "supabase/migrations" in rel.replace(os.sep, "/"):
            migrations[rel] = text
        else:
            app[rel] = text
    return app, migrations


def references(name, index):
    """Files naming this identifier, excluding test files.

    A table used only by its own test is not a table the product uses, and
    counting those hides exactly the case this checklist is looking for.
    """
    word = re.compile(r"\b%s\b" % re.escape(name))
    hits = []
    for rel, text in index.items():
        norm = rel.replace(os.sep, "/")
        if "__tests__" in norm or ".test." in norm or ".spec." in norm:
            continue
        if word.search(text):
            hits.append(rel)
    return sorted(hits)


def find_ref(root):
    for path in code_files(Path(root)):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for pattern in REF_PATTERNS:
            found = pattern.search(text)
            if found:
                return found.group(1), str(path)
    for name in (".env", ".env.local", ".env.example"):
        candidate = Path(root) / name
        if candidate.is_file():
            for pattern in REF_PATTERNS:
                found = pattern.search(candidate.read_text(errors="replace"))
                if found:
                    return found.group(1), str(candidate)
    return None, None


# ------------------------------------------------------------------ the database

SCHEMA_SQL = """
select
  c.relname as table_name,
  c.relrowsecurity as rls_enabled,
  coalesce(s.n_live_tup, 0) as live_rows,
  coalesce(s.n_dead_tup, 0) as dead_rows,
  coalesce(s.n_tup_upd, 0) as updates,
  pg_total_relation_size(c.oid) as total_bytes,
  s.last_autovacuum,
  (select count(*) from pg_policy p where p.polrelid = c.oid) as policy_count,
  (select coalesce(string_agg(distinct g.grantee, ','), '')
     from information_schema.role_table_grants g
    where g.table_schema = 'public' and g.table_name = c.relname) as grantees,
  -- Which privileges the signed-out role actually holds, not merely that it
  -- appears somewhere in the grant list. A table a visitor may write to and a
  -- table a visitor may read are the same row above and opposite findings.
  (case when exists (select 1 from pg_roles where rolname = 'anon')
        then (select coalesce(string_agg(p, ','), '')
                from unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p
               where has_table_privilege('anon', c.oid, p))
   end) as anon_privileges
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_stat_user_tables s on s.relid = c.oid
where n.nspname = 'public' and c.relkind = 'r'
order by c.relname;
"""

FUNCTION_SQL = """
select p.proname as name, pg_get_function_identity_arguments(p.oid) as args
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
order by p.proname;
"""

# Every column is cast to text: the first branch of a union fixes the type for
# all of them, and pg_proc.proname is `name`, which Postgres caps at 63
# characters. Left uncast, every policy expression and function body below is
# silently cut to that length and a caller past the cut reads as absent.
#
# Where a function is called from inside the database. A trigger, a policy, a
# view, a column default and another function's body are all callers, and none
# of them is visible to a scan of the application source - which is why a
# security definer trigger function and a policy helper both read as orphaned
# from the repository side.
FUNCTION_CALLERS_SQL = """
select 'trigger' as kind, (t.tgname || ' on ' || c.relname)::text as site,
       p.proname::text as body
  from pg_trigger t
  join pg_proc p on p.oid = t.tgfoid
  join pg_class c on c.oid = t.tgrelid
 where not t.tgisinternal
union all
select 'policy', (pol.policyname || ' on ' || pol.tablename)::text,
       (coalesce(pol.qual, '') || ' ' || coalesce(pol.with_check, ''))::text
  from pg_policies pol
 where pol.schemaname = 'public'
union all
select 'function', p.proname::text, p.prosrc::text
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where n.nspname = 'public'
union all
select 'view', c.relname::text, pg_get_viewdef(c.oid)::text
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind in ('v', 'm')
union all
select 'default', (c.relname || '.' || a.attname)::text, pg_get_expr(d.adbin, d.adrelid)::text
  from pg_attrdef d
  join pg_class c on c.oid = d.adrelid
  join pg_namespace n on n.oid = c.relnamespace
  join pg_attribute a on a.attrelid = d.adrelid and a.attnum = d.adnum
 where n.nspname = 'public';
"""

CRON_SQL = "select jobid, jobname, schedule, active, left(command, 400) as command from cron.job order by jobid;"

# pg_cron is an extension, so cron.job is absent on a project that never enabled
# it and the query above fails to parse. That failure and a real outage read the
# same afterwards, which leaves a project with no jobs unable to answer the gate
# at all. to_regclass resolves a missing relation to null rather than raising, so
# asking first turns "no such schema" into a count of zero.
CRON_PRESENT_SQL = "select to_regclass('cron.job') is not null as present;"

# Only the relations a checklist can act on: an index nothing has ever read, and
# a foreign key with nothing to serve it. Both are read from the counters rather
# than from the plan, so a fresh instance reports honestly small numbers instead
# of a verdict it has not earned.
INDEX_SQL = """
select s.relname as table_name, s.indexrelname as index_name, s.idx_scan as scans,
       pg_relation_size(s.indexrelid) as bytes
from pg_stat_user_indexes s
join pg_index i on i.indexrelid = s.indexrelid
where s.schemaname = 'public' and not i.indisunique and not i.indisprimary
order by s.idx_scan, bytes desc;
"""

FK_SQL = """
select tc.table_name as child, ccu.table_name as parent, tc.constraint_name as name
from information_schema.table_constraints tc
join information_schema.constraint_column_usage ccu
  on ccu.constraint_name = tc.constraint_name
where tc.constraint_type = 'FOREIGN KEY' and tc.table_schema = 'public'
order by parent, child;
"""

BLOAT_SQL = """
select n.nspname as schema_name, c.relname as table_name,
       coalesce(s.n_live_tup, 0) as live_rows,
       pg_total_relation_size(c.oid) as total_bytes,
       s.last_autovacuum
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_stat_all_tables s on s.relid = c.oid
where c.relkind = 'r' and n.nspname in ('net', 'cron', 'storage', 'auth')
order by pg_total_relation_size(c.oid) desc
limit 20;
"""

SLOWEST_SQL = """
select calls, round(total_exec_time::numeric, 0) as total_ms,
       round(mean_exec_time::numeric, 1) as mean_ms,
       round(max_exec_time::numeric, 0) as max_ms, left(query, 160) as query
from extensions.pg_stat_statements
order by max_exec_time desc
limit 10;
"""

SLOTS_SQL = """
select slot_name, active, wal_status,
       pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) as behind_bytes
from pg_replication_slots;
"""


def probe_database(ref, token):
    out = {}
    steps = [
        ("tables", SCHEMA_SQL), ("functions", FUNCTION_SQL),
        ("function_callers", FUNCTION_CALLERS_SQL), ("indexes", INDEX_SQL), ("foreign_keys", FK_SQL), ("system_tables", BLOAT_SQL),
        ("slowest", SLOWEST_SQL), ("replication_slots", SLOTS_SQL),
    ]
    for name, query in steps:
        try:
            out[name] = sql(ref, token, query)
        except Exception as exc:  # a probe that could not be taken is a finding
            out[name] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        present = sql(ref, token, CRON_PRESENT_SQL)
        out["cron_jobs"] = (sql(ref, token, CRON_SQL)
                            if present and present[0].get("present") else [])
    except Exception as exc:  # a probe that could not be taken is a finding
        out["cron_jobs"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        out["edge_functions"] = [
            {"slug": f.get("slug"), "status": f.get("status"), "version": f.get("version")}
            for f in api("/projects/%s/functions" % ref, token)
        ]
    except Exception as exc:
        out["edge_functions"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    return out


# ------------------------------------------------------------------ the pairing

def sql_callers(name, rows):
    """The call sites inside the database that name this function.

    A function's own body names it in every recursive call and in nothing else,
    so the row describing it is skipped; counting it would make every function
    its own caller. Returns None when the reading was not taken, which is a
    different answer from a function nothing calls.
    """
    if not isinstance(rows, list):
        return None
    word = re.compile(r"\b%s\b" % re.escape(name))
    found = []
    for row in rows:
        if row.get("kind") == "function" and row.get("site") == name:
            continue
        if word.search(row.get("body") or ""):
            found.append("%s %s" % (row.get("kind"), row.get("site")))
    return sorted(set(found))


def pair(db, app_index, migration_index, root):
    tables = db.get("tables")
    functions = db.get("functions")
    result = {"tables": [], "functions": [], "edge_functions": []}
    if isinstance(tables, list):
        for row in tables:
            name = row["table_name"]
            app_hits = references(name, app_index)
            result["tables"].append({
                "name": name,
                "live_rows": row.get("live_rows"),
                "total_bytes": row.get("total_bytes"),
                "rls_enabled": row.get("rls_enabled"),
                "policy_count": row.get("policy_count"),
                "grantees": (row.get("grantees") or "").split(",") if row.get("grantees") else [],
                "anon_privileges": (row["anon_privileges"].split(",")
                                    if row.get("anon_privileges") else
                                    ([] if row.get("anon_privileges") == "" else None)),
                "app_references": app_hits,
                "migration_references": references(name, migration_index),
            })
    callers = db.get("function_callers")
    if isinstance(functions, list):
        for row in functions:
            name = row["name"]
            result["functions"].append({
                "name": name,
                "args": row.get("args"),
                "app_references": references(name, app_index),
                "sql_references": sql_callers(name, callers),
            })
    deployed = db.get("edge_functions")
    if isinstance(deployed, list):
        local = set()
        fn_root = Path(root) / "supabase" / "functions"
        if fn_root.is_dir():
            local = {p.name for p in fn_root.iterdir()
                     if p.is_dir() and not p.name.startswith("_")}
        for row in deployed:
            slug = row.get("slug")
            # A function's own directory names it in every file path it holds, so
            # counting those would make every deployed function look called.
            own = "supabase/functions/%s/" % slug
            outside = {rel: text for rel, text in app_index.items()
                       if own not in rel.replace(os.sep, "/")}
            result["edge_functions"].append({
                "slug": slug,
                "status": row.get("status"),
                "has_source": slug in local,
                "app_references": references(slug, outside),
            })
        result["local_only_functions"] = sorted(
            local - {row.get("slug") for row in deployed})
    return result


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=["all"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--project-ref")
    parser.add_argument("--out")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    ref, ref_source = args.project_ref, "--project-ref"
    if not ref:
        ref, ref_source = find_ref(root)

    app_index, migration_index = code_index(root)
    out = {
        "root": str(root),
        "project_ref": ref,
        "project_ref_source": ref_source,
        "code_files": len(app_index),
        "migration_files": sorted(migration_index),
    }

    if not token:
        out["error"] = ("SUPABASE_ACCESS_TOKEN is not set, so nothing about the "
                        "database was read. The schema half of every gate is "
                        "unanswered, not passing.")
    elif not ref:
        out["error"] = ("No Supabase project ref was given and none was found in "
                        "the repository. Pass --project-ref.")
    else:
        db = probe_database(ref, token)
        out["database"] = db
        out["paired"] = pair(db, app_index, migration_index, root)

    target = guarded_probe_out(args.out or default_probe_out("db"), args.force)
    target.write_text(json.dumps(scrub(out), indent=2, default=str))
    print("wrote %s" % target)
    if out.get("error"):
        print(out["error"], file=sys.stderr)
        return 1
    paired = out.get("paired", {})
    orphan_tables = [t["name"] for t in paired.get("tables", []) if not t["app_references"]]
    orphan_fns = [f["slug"] for f in paired.get("edge_functions", [])
                  if not f["has_source"]]
    print("%d tables, %d with no application reference" %
          (len(paired.get("tables", [])), len(orphan_tables)))
    if orphan_tables:
        print("  " + ", ".join(orphan_tables))
    if orphan_fns:
        print("%d deployed functions with no source here: %s" %
              (len(orphan_fns), ", ".join(orphan_fns)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
