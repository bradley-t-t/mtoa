---
name: checklist
description: Run every checklist a project answers to at once - design, SEO, performance, stack, database and Google, swept concurrently and reported as one record. Use on /checklist, or "run the checklists", "check everything", "full pass on this site", "is this ready to ship". Also before releasing a public site.
---

# The full checklist pass

Six checklists, one question each. Design answers for what a reader sees, SEO
for whether they arrive, performance for how long they wait, stack for whether
the thing underneath is wired up and alive, the database for whether the schema
still matches what the code does, and Google for the accounts Google keeps about
the site, which is the one question no amount of reading the repository answers.

Separate registries, separate state, no run reads another's results. Running
them in series multiplies the wall-clock for nothing, so this runs them together.

**This is an instruction to change the project, not to survey it.** A gate the
sweep failed cannot be recorded as passing. The only ways past one are changing
the code or naming the condition that makes it inapplicable.

## 0. The tools

```bash
DESIGN="$HOME/.claude/checklists/design/bin/design-pass.py"
SEO="$HOME/.claude/checklists/seo/bin/seo-pass.py"
PERF="$HOME/.claude/checklists/perf/bin/perf-pass.py"
STACK="$HOME/.claude/checklists/stack/bin/stack-pass.py"
DB="$HOME/.claude/checklists/db/bin/db-pass.py"
GOOGLE="$HOME/.claude/checklists/google/bin/google-pass.py"
PERFDIR="$HOME/.claude/checklists/perf/scripts"
STACKPROBE="$HOME/.claude/checklists/stack/scripts/probe.py"
DBPROBE="$HOME/.claude/checklists/db/scripts/probe.py"
GOOGLEPROBE="$HOME/.claude/checklists/google/scripts/probe.py"
```

Each resolves its own registry from its own location. If any is missing, say so
rather than running five sixths of a pass and reporting a full one.

Google is not scoped to the project. Under `/checklist` open it against this
project's domain and leave `--all` to a run of its own:

```bash
"$GOOGLE" start --site <domain> --registry <agency.repo checkout>
```

## 1. Open the runs, concurrently

```bash
"$DESIGN" start --kind web-ui --target <path> --title "<what this is>" &
"$SEO"    start --kind site   --target <path> --title "<what this is>" &
"$PERF"   start --kind site   --target <path> --url https://<host> --title "<what this is>" &
"$STACK"  start --kind site   --target <path> --url https://<host> --title "<what this is>" &
"$DB"     start --kind site   --target <path> --project-ref <ref> --title "<what this is>" &
wait
```

Three have an order. **The performance baseline and capture must be taken before
anything is edited** - a `PreToolUse` hook refuses a source edit until both
exist, because neither can be established afterwards.

```bash
GOOGLE_PAGESPEED_API_KEY=<from CryptoFort> "$PERFDIR/psi.py" measure \
  --url https://<host> --runs 3 --out /tmp/perf-before.json
"$PERF" measure --file /tmp/perf-before.json --label before
"$PERF" scan --metric <the metric psi named worst in the field>

node "$PERFDIR/capture.js" --url https://<host> --out /tmp/perf-before-capture.json --built
"$PERF" parity --phase before --file /tmp/perf-before-capture.json
```

The stack and database probes go alongside, because they are the slowest thing
in the pass and their findings change what the others are looking at. Every
credential comes from CryptoFort into the call that needs it, never into a file:

```bash
~/.claude/tools/with-credential.py <name>=<VAR> ... -- \
  "$STACKPROBE" all --root <path> --url https://<host>
"$STACK" probe --file <the path the probe printed>

~/.claude/tools/with-credential.py <name>=SUPABASE_ACCESS_TOKEN -- \
  "$DBPROBE" all --root <path>
"$DB" probe --file <the path the probe printed>
"$STACK" services
```

Read `services` before going further. A paused project, a development key in
production, or a production deployment that is not the commit `main` points at
makes most of the rest beside the point until it is fixed.

The design run needs its design read before any file is written:

```bash
"$DESIGN" read --page-kind <landing|portfolio|editorial|product> --audience "<who>" \
  --vibe "<the words the brief used>" --palette "<family>" --face "<display face>"
```

## 2. Sweep, concurrently

```bash
"$DESIGN" verify > /tmp/design-sweep.txt 2>&1 &
"$SEO"    verify > /tmp/seo-sweep.txt    2>&1 &
"$PERF"   verify > /tmp/perf-sweep.txt   2>&1 &
"$STACK"  verify > /tmp/stack-sweep.txt  2>&1 &
"$DB"     verify > /tmp/db-sweep.txt     2>&1 &
wait
```

Build first. The performance sweep reads the build as well as the source, and
leads with a warning when the output is behind.

## 3. Fix what the sweeps found

Every tool names a file and a line. Fix the code. A sweep-failed gate cannot be
answered `pass`, so there is nothing to decide here.

## 4. Answer what a script cannot settle

```bash
for T in "$DESIGN" "$SEO" "$PERF" "$STACK" "$DB"; do "$T" status --full & done; wait
```

Then one `resolve` call per tool:

```bash
"$DESIGN" resolve --batch - <<'JSON'
[{"id": "PTR-02", "status": "fixed", "note": "added :active scale(0.97) to every button and row"},
 {"id": "SLP-28", "status": "na",    "note": "no hero video; the hero is a single still at 3:2"}]
JSON
```

Every row keeps its own status and note, the note minimum applies per row, and a
batch carrying one bad row is refused whole with every fault named.

Four groups take no hand answer, because their subject is outside the repository
and an attestation is exactly what the probe replaces:

| Refuses `pass` and `na` | Why |
| :--- | :--- |
| the stack run's service gates | they answer to a request that was actually made |
| the database run's orphan, access, health and provenance gates | a table made by hand in a dashboard is invisible to any migration file |
| the performance run's `PAR-01`..`PAR-08` | the before and after captures answer them |

Change the thing and probe again.

This is also where handovers get recorded. Anything a sweep turned up that
belongs to another checklist is filed against it and fixed in that run:

```bash
"$STACK" delegate --to design --note "the bar sits 4px under the CTA at 375px"
"$STACK" delegate --none      # nothing outside this checklist came up
"$DB"    delegate --to stack  --note "email-service reads a table that exists in no schema"
```

A finding dropped because it belonged to somebody else is the one nobody comes
back for.

### Subagents

A run is single-owner state, keyed on the session id, with the file ledger
credited by this session's hook and no lock on the run file. A subagent that
calls `resolve` either races the parent or opens a second run answering for
nothing.

Give one the reading instead - a slice of the deliverable and the open gates for
that slice - and have it return **proposed answers as JSON and nothing else**,
with no tool calls against any run. The parent merges and submits through
`resolve --batch`. Split by route or component tree so each reads a coherent
slice. Fan out only when the slices are independent and the reading is the cost.

## 5. Rule on every file, in batches

A file cannot be cleared until the harness has watched it be read.

```bash
sed -n '1,400p' <path> <path> <path>          # one call reads and credits many
"$DESIGN" file-clear --batch - <<'JSON'
[{"path": "src/lib/format.ts", "note": "read in full; pure date maths, carries no design"}]
JSON
```

The database run keeps the same ledger over tables. Every table ends dropped or
kept with its reader named:

```bash
"$DB" tables
"$DB" table-clear --batch - <<'JSON'
[{"table": "mixers_history", "note": "read by src/app/hooks/useStatusHistory.js"}]
JSON
```

## 6. Report as one record

```bash
for T in "$DESIGN" "$SEO" "$PERF" "$STACK" "$DB"; do "$T" report; done
```

Lead with what changed. The stack and database reports lead with what answered
over the network and out of the catalogue. Every gate answered `na` is listed in
its own section: a gate entered a run because its condition was found, so
skipping one is a claim worth reading.

## 7. Close them

Performance closes last - it needs the after capture and measurement, which need
the build the other runs' changes produced.

```bash
node "$PERFDIR/capture.js" --url http://localhost:4173 --out /tmp/perf-after-capture.json --built
"$PERF" parity --phase after --file /tmp/perf-after-capture.json
"$PERF" parity --diff

for T in "$DESIGN" "$SEO" "$PERF" "$STACK" "$DB"; do "$T" finish; done
```

Each refuses while anything is open, and a `Stop` hook refuses to end the session
with any run still going. Do not close five and leave the sixth.

A design change made in this pass moves the page and the parity diff reports it.
That is not a conflict: parity asks whether the *performance* work changed the
rendering, so answer it `disputed` naming the design gate that caused it.
