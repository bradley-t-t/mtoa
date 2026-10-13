#!/usr/bin/env python3
"""Cases the database checklist has to get right.

Each one answers to a way this checklist could report a clean database that is
not: a gate the probe failed recorded as passing, a schema question settled from
the repository instead of the catalogue, a storage bucket counted as a missing
table, a retired table read as an unapplied migration, and a run that dropped
tables closing without saying where the rows went.

  db-pass-cases.py        run them
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = (Path(__file__).resolve().parent.parent.parent
          / "checklists" / "db" / "bin" / "db-pass.py")


def subject(handed):
    """The file holding the gate logic, out of whatever was handed over.

    The gate logic lives in the checklist tree and `tools/db-pass.py` is the entry
    point the hooks call, which re-executes it and holds nothing of its own. The
    cases runner matches a tool to its cases by name, so that entry point is what
    it hands over -- and importing it yields a module with none of the engine's
    names on it.
    """
    handed = Path(handed)
    try:
        if "os.execv" in handed.read_text(errors="ignore"):
            return ENGINE
    except OSError:
        pass
    return handed


TARGET = subject(os.environ.get("INTELLIGENCE_TEST_TARGET") or ENGINE)

FAILURES: list[str] = []


def check(name, got, want):
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (name, got, want))


def run(args, state, cwd=None):
    env = dict(os.environ)
    env["INTELLIGENCE_STATE_DIR"] = str(state)
    env.pop("CLAUDE_CODE_REMOTE", None)
    env["CLAUDE_CODE_SESSION_ID"] = "cases"
    done = subprocess.run([sys.executable, str(TARGET)] + args, capture_output=True,
                          text=True, env=env, cwd=str(cwd) if cwd else None)
    return done.returncode, done.stdout + done.stderr


def probe_file(tmp, tables=None, functions=None, migrations=None, root=None,
               database=None, edge_functions=None):
    """A probe shaped like the real one, with only the fields a case needs.

    `database` overrides keys on the catalogue side, which is the only place a
    step that failed still looks different from one that answered with nothing.
    """
    data = {
        "root": str(root or tmp),
        "project_ref": "abcdefghijklmnopqrst",
        "migration_files": migrations or [],
        "paired": {
            "tables": tables if tables is not None else [],
            "functions": functions or [],
            "edge_functions": edge_functions or [],
        },
        "database": {"tables": [], "cron_jobs": [], "indexes": [], "system_tables": [],
                     "slowest": [], "replication_slots": [], "foreign_keys": []},
    }
    data["database"].update(database or {})
    path = Path(tmp) / "probe.json"
    path.write_text(json.dumps(data))
    return path


def table(name, **kw):
    row = {"name": name, "live_rows": 1, "total_bytes": 8192, "rls_enabled": True,
           "policy_count": 1, "grantees": ["authenticated"], "app_references": ["src/a.js"],
           "migration_references": ["supabase/migrations/1.sql"]}
    row.update(kw)
    return row


def case_failed_gate_cannot_pass(tmp):
    """A gate the sweep failed is work to do, not an answer to write down."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("orphan", app_references=[])])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    code, out = run(["resolve", "ORP-01", "pass", "looks fine to me"], state)
    check("failed gate refuses pass", code, 1)
    check("failed gate says why", "cannot be recorded" in out or "answers to the probe" in out, True)


def case_probe_gate_refuses_attestation(tmp):
    """The catalogue settles these, so no note about the source code can."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    code, out = run(["resolve", "SEC-01", "na", "we do not use RLS here"], state)
    check("probe-only gate refuses na", code, 1)
    check("probe-only gate explains", "attestation" in out, True)


def case_self_settling_cannot_be_answered(tmp):
    """A run cannot close by writing down that it is finished."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    code, out = run(["resolve", "PRC-06", "pass", "everything is answered"], state)
    check("self-settling gate refuses an answer", code, 1)
    check("self-settling gate explains", "run's own state" in out, True)


def case_storage_bucket_is_not_a_missing_table(tmp):
    """`storage.from('x')` reads a bucket; only `.from('x')` reads a table."""
    state = Path(tmp) / "state"
    root = Path(tmp) / "app"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.js").write_text(
        "const a = await supabase.storage\n    .from('reports')\n    .list()\n"
        "const b = await db.from('kept').select('*')\n")
    run(["start", "--target", str(root)], state)
    probe = probe_file(tmp, tables=[table("kept")], root=root)
    run(["probe", "--file", str(probe)], state)
    code, out = run(["verify"], state)
    check("bucket is not reported as a missing table", "reports" in out, False)


def case_missing_table_in_code_is_caught(tmp):
    """The drift that fails at runtime: code reading a table that is gone."""
    state = Path(tmp) / "state"
    root = Path(tmp) / "app2"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.js").write_text("await db.from('gone_away').select('*')\n")
    run(["start", "--target", str(root)], state)
    probe = probe_file(tmp, tables=[table("kept")], root=root)
    run(["probe", "--file", str(probe)], state)
    code, out = run(["verify"], state)
    check("missing table is caught", "gone_away" in out, True)


def case_later_drop_is_not_an_unapplied_migration(tmp):
    """A migration retiring a table is the newer truth, not a failure to apply."""
    state = Path(tmp) / "state"
    root = Path(tmp) / "app3"
    migrations = root / "supabase" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "1_create.sql").write_text("create table public.old_thing (id uuid);\n")
    (migrations / "2_drop.sql").write_text(
        "-- rows exported to the backup archive first\n"
        "drop table if exists public.old_thing cascade;\n")
    run(["start", "--target", str(root)], state)
    probe = probe_file(tmp, tables=[table("kept")], root=root,
                       migrations=["supabase/migrations/1_create.sql",
                                   "supabase/migrations/2_drop.sql"])
    run(["probe", "--file", str(probe)], state)
    code, out = run(["verify"], state)
    check("a retired table is not an unapplied migration", "old_thing" in out, False)


def case_drop_without_backup_holds_the_run_open(tmp):
    """The one verb with no undo stays accounted for."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")])
    run(["probe", "--file", str(probe)], state)
    run(["backup", "--path", "", "--tables", "gone_one"], state)
    run(["verify"], state)
    code, out = run(["status", "--full"], state)
    check("a drop with no backup keeps PRC-04 open", "PRC-04" in out, True)


def case_batch_takes_the_shape_its_siblings_take(tmp):
    """`/checklist` answers all five runs from one template, so the rows match."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp, "--kind", "site", "--title", "a project"], state)
    probe = probe_file(tmp, tables=[table("kept")])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    path = Path(tmp) / "batch.json"
    path.write_text(json.dumps(
        [{"id": "SEC-03", "status": "disputed", "note": "no blanket policy stands"}]))
    code, out = run(["resolve", "--batch", str(path)], state)
    check("a JSON batch is taken", "Recorded 1 answer" in out, True)
    check("a JSON batch exits clean", code, 0)


def case_batch_refuses_a_duplicate_row(tmp):
    """Two answers for one gate means one of them was not read."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    path = Path(tmp) / "dupe.json"
    path.write_text(json.dumps([
        {"id": "SEC-03", "status": "disputed", "note": "first"},
        {"id": "SEC-03", "status": "disputed", "note": "second"}]))
    code, out = run(["resolve", "--batch", str(path)], state)
    check("a duplicate row is refused", "appears twice" in out, True)
    check("a duplicate row is non-zero", code, 1)


def case_table_clear_batch_needs_a_reader(tmp):
    """Keeping a table without naming its reader is the claim the ledger exists for."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept"), table("other")])
    run(["probe", "--file", str(probe)], state)
    path = Path(tmp) / "keep.json"
    path.write_text(json.dumps([
        {"table": "kept", "note": "read by src/a.js"},
        {"table": "other", "note": ""}]))
    code, out = run(["table-clear", "--batch", str(path)], state)
    check("the noted table is kept", "Kept 1 table" in out, True)
    check("the unnoted table is refused", "needs the reader named" in out, True)


def case_finish_refuses_while_open(tmp):
    """The run cannot close over an unanswered gate."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    code, out = run(["finish"], state)
    check("finish refuses", code, 1)
    check("finish says what is open", "gate(s) open" in out, True)


def case_hooks_are_silent_without_a_run(tmp):
    """A session that never opened a run is never obstructed by this skill."""
    state = Path(tmp) / "state-empty"
    for verb in ("guard-stop", "guard-land", "hook-skill"):
        done = subprocess.run(
            [sys.executable, str(TARGET), verb], input="{}", capture_output=True,
            text=True, env={**os.environ, "INTELLIGENCE_STATE_DIR": str(state),
                            "CLAUDE_CODE_SESSION_ID": "nobody"})
        check("%s exits 0 with no run" % verb, done.returncode, 0)
        check("%s stays quiet" % verb, done.stdout.strip(), "")


def engine():
    """The tool as a module, for the cases that ask what it classifies."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("db_pass_engine", TARGET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_every_catalogue_gate_is_barred_from_the_cache(tmp):
    """Nothing that reads the live database may be carried forward.

    This is the whole risk the cache introduces here: a table created by hand
    this afternoon is invisible to the repository, so an answer keyed on the
    repository's bytes would report a database that has moved as unchanged.
    PROBE_ONLY is the list of gates that answer to the catalogue, so every one
    of them has to be inside NETWORK_CHECKS - and a gate added to one set and
    not the other is exactly the drift no reader would notice.
    """
    mod = engine()
    missing = sorted(mod.PROBE_ONLY - mod.NETWORK_CHECKS)
    check("every probe-only gate is barred from the cache", missing, [])
    check("the manual policy gate is barred too", "manual" in mod.NETWORK_CHECKS, True)


def case_the_repository_side_gates_stay_carryable(tmp):
    """The wiring is not dead: two gates read only files and may be carried.

    If this fails the integration has become decorative - every gate excluded,
    a bucket written holding nothing, and a sweep that re-reads the repository
    every run for no reason.
    """
    mod = engine()
    for kind in ("service_key_client_side", "drops_document_backup"):
        check("%s is not barred as network" % kind, kind in mod.NETWORK_CHECKS, False)
        check("%s is not barred as self-settling" % kind, kind in mod.SELF_SETTLING, False)


def case_bookkeeping_gates_are_barred_from_the_cache(tmp):
    """A gate settled by the run's own state says nothing about the next run."""
    mod = engine()
    leaked = sorted(mod.SELF_SETTLING & mod.NETWORK_CHECKS)
    check("the two exclusion sets do not overlap", leaked, [])
    for kind in ("run_opened", "table_coverage", "report_emitted"):
        check("%s is self-settling" % kind, kind in mod.SELF_SETTLING, True)


def _verify_twice(tmp, extra=None):
    """Open a run over a clean tree, verify, then verify again."""
    state = Path(tmp) / "state"
    root = Path(tmp) / "repo"
    (root / "supabase" / "migrations").mkdir(parents=True)
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.js").write_text("export const x = 1\n")
    (root / "supabase" / "migrations" / "1.sql").write_text(
        "create table public.a (id uuid primary key);\n")
    probe = probe_file(tmp, tables=[table("a")],
                       migrations=["supabase/migrations/1.sql"], root=root)
    data = json.loads(probe.read_text())
    # The real probe records a COUNT here, not a list of paths. The first draft
    # of these cases made it a list, the fixture passed, and the tool crashed on
    # the first real database it met.
    data["code_files"] = 1
    probe.write_text(json.dumps(data))
    run(["start", "--target", str(root), "--project-ref", "abcdefghijklmnopqrst"], state)
    run(["probe", "--file", str(probe)], state)
    first = run(["verify"] + (extra or []), state)
    second = run(["verify"] + (extra or []), state)
    return state, first, second


def case_a_second_sweep_carries_the_repository_gates(tmp):
    """The defect this wiring closes: nothing was ever stored, so every run
    re-swept the whole database even when neither the repository nor the gate
    registry had moved."""
    state, first, second = _verify_twice(tmp)
    check("the first sweep runs", first[0], 0)
    check("the second sweep runs", second[0], 0)
    check("the second sweep says what it carried", "carried from an earlier run" in second[1], True)
    buckets = list((state / "pass-cache" / "db").glob("db-*.json")) \
        if (state / "pass-cache" / "db").is_dir() else []
    check("a db bucket now exists", len(buckets) >= 1, True)


def case_no_cache_carries_nothing(tmp):
    """--no-cache sweeps everything and stores nothing, on every verb."""
    state, first, second = _verify_twice(tmp, extra=["--no-cache"])
    check("the sweep still runs with --no-cache", second[0], 0)
    check("nothing is carried", "carried from an earlier run" in second[1], False)
    check("no bucket is written", (state / "pass-cache" / "db").is_dir(), False)


def case_probe_shape_matches_the_real_probe(tmp):
    """code_files is a count, not a list of paths.

    The scope builder read it as paths once, which parsed, passed a fixture that
    had guessed a list, and then raised TypeError on the first real probe. A
    case that asserts the shape is the only thing standing between the two.
    """
    mod = engine()
    root = Path(tmp) / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.js").write_text("const x = 1\n")
    scope = mod.sweep_scope({"probe": {"root": str(root), "code_files": 520,
                                       "migration_files": []}})
    check("a counted code_files does not raise", isinstance(scope, list), True)
    check("the walk finds the source itself", any(s.endswith("/src/a.js") for s in scope), True)


def case_the_scope_is_the_bytes_the_checker_read(tmp):
    """One walk feeds both, so a suffix cannot be added to one and not the other."""
    mod = engine()
    root = Path(tmp) / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "keep.ts").write_text("ok\n")
    (root / "src" / "ignore.png").write_bytes(b"\x89PNG")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").write_text("vendor\n")
    walked = {str(p) for p in mod.client_source_files(str(root))}
    check("a source file is read", any(w.endswith("/src/keep.ts") for w in walked), True)
    check("a binary is not", any(w.endswith(".png") for w in walked), False)
    check("vendor code is not", any("node_modules" in w for w in walked), False)


def _verdict(state, gate):
    """One gate's recorded verdict, read from the run rather than the listing.

    `verify` prints only what is not passing, so a gate missing from its output
    is indistinguishable from one it never considered.
    """
    runs = sorted((Path(state) / "db-runs").glob("db-*.json"))
    if not runs:
        return None
    data = json.loads(runs[-1].read_text())
    return (data.get("verdicts", {}).get(gate) or {}).get("verdict")


def case_no_deployed_functions_is_an_answer(tmp):
    """A project that deploys nothing has answered the question, not dodged it.

    An empty list and a step that never ran both leave the pairing empty, so a
    check reading only that slot holds the gate open for every project without
    edge functions - a state no amount of work can clear.
    """
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")], database={"edge_functions": []})
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("no deployed functions passes ORP-02", _verdict(state, "ORP-02"), "pass")
    check("no deployed functions passes ORP-03", _verdict(state, "ORP-03"), "pass")


def case_unreadable_function_list_stays_open(tmp):
    """A step that could not be taken is unknown, never a pass."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")],
                       database={"edge_functions": {"error": "HTTPError: 401"}})
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("unreadable function list is unknown", _verdict(state, "ORP-02"), "unknown")
    check("unreadable caller list is unknown", _verdict(state, "ORP-03"), "unknown")


def case_no_scheduled_jobs_is_an_answer(tmp):
    """pg_cron absent means zero jobs, which is the gate met rather than unread."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")], database={"cron_jobs": []})
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("no cron jobs passes ORP-04", _verdict(state, "ORP-04"), "pass")


def case_unreadable_job_list_stays_open(tmp):
    """A cron query that raised leaves the gate open with its reason."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")],
                       database={"cron_jobs": {"error": "HTTPError: 400"}})
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("unreadable job list is unknown", _verdict(state, "ORP-04"), "unknown")


def case_anon_reading_a_table_is_a_finding(tmp):
    """A signed-out role holding select is the table published, whatever the app does."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("camp_registrations", grantees=["anon"],
                                          anon_privileges=["SELECT", "INSERT"])])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("anon select fails SEC-04", _verdict(state, "SEC-04"), "fail")


def case_anon_insert_only_is_a_public_form(tmp):
    """Insert with no read back is what a registration form is, and it passes.

    The grantee list alone cannot tell that apart from a published table, so a
    check reading only the name holds the gate open for every project carrying a
    form a signed-out visitor can submit.
    """
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("camp_registrations", grantees=["anon"],
                                          anon_privileges=["INSERT"])])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("anon insert only passes SEC-04", _verdict(state, "SEC-04"), "pass")


def case_anon_privileges_absent_falls_back_to_the_name(tmp):
    """A probe taken before privileges were recorded still reports the reach."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("camp_registrations", grantees=["anon"],
                                          anon_privileges=None)])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("older probe still fails SEC-04", _verdict(state, "SEC-04"), "fail")


def case_a_trigger_is_a_caller(tmp):
    """A security definer trigger function is called, just not from JavaScript.

    The rule counts a trigger as a caller. Reading only the application source
    reports every trigger function and every policy helper as an orphan, which
    is a fail no amount of correct work can clear.
    """
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")], functions=[
        {"name": "handle_new_user", "args": "", "app_references": [],
         "sql_references": ["trigger on_auth_user_created on users"]},
        {"name": "is_camp_staff", "args": "", "app_references": [],
         "sql_references": ["policy staff read every profile on user_profiles"]},
    ])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("a trigger caller passes ORP-05", _verdict(state, "ORP-05"), "pass")


def case_a_function_nothing_calls_still_fails(tmp):
    """No caller on either side is the finding the gate exists for."""
    state = Path(tmp) / "state"
    run(["start", "--target", tmp], state)
    probe = probe_file(tmp, tables=[table("kept")], functions=[
        {"name": "old_report_helper", "args": "", "app_references": [],
         "sql_references": []},
    ])
    run(["probe", "--file", str(probe)], state)
    run(["verify"], state)
    check("an uncalled function fails ORP-05", _verdict(state, "ORP-05"), "fail")


def main():
    cases = [v for k, v in sorted(globals().items()) if k.startswith("case_")]
    for case in cases:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                case(tmp)
            except Exception as exc:  # a case that cannot run is a failure
                FAILURES.append("%s raised %s: %s" % (case.__name__, type(exc).__name__, exc))
    if FAILURES:
        print("db-pass: %d failure(s)" % len(FAILURES))
        for failure in FAILURES:
            print("  " + failure)
        return 1
    print("db-pass: %d cases pass" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
