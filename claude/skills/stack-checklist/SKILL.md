---
name: stack-checklist
description: Verify that everything a project depends on is wired up and alive - Supabase, Clerk, Stripe and Vercel reached over the network, environment variables present and not leaking, the agency footer bar in place where the profile names one, Prettier and ESLint and TypeScript actually enforced, CalVer moving, branch protection and the standard workflow on both branches, dependencies clean, auth in the request path, error reporting onboarded, the deployed page loading without errors, every built page linking the stylesheets its own markup needs, and the legal routes served - as a gate checklist that cannot close while anything is unanswered. Use whenever the user runs /stack-checklist, or asks to "check the stack", "is everything wired up", "is Supabase still up", "check the env vars", "did this deploy properly", "is the footer on every page", "check branch protection", "is prettier actually running", "audit the third party services", "why is production broken", "is this project set up right", "check RLS", "is the webhook registered", "why does the page flash unstyled before it loads", or otherwise wants a project's plumbing verified rather than its appearance. This is an instruction to change the project and its service settings, never to produce an audit or a list of recommendations.
argument-hint: "[path] [--url https://host] [--flag payments|auth|public-site|app]"
---

# stack-checklist

Every wiring rule this skill knows lives as a gate in `checklist/gates/*.json`, and
`bin/stack-pass.py` decides which of them apply from what the project actually
contains, settles what a script or a request can settle, and refuses to close while
anything is unanswered.

## One question, and the three it hands over

This checklist asks whether a thing is **wired up and alive**. It does not ask how it
looks, whether anybody finds it, or how long it takes to arrive. Those are three other
checklists with their own registries:

| Found this | Goes to |
| --- | --- |
| the footer bar is missing, or points at a dead link | stays here |
| the footer bar looks wrong, is misaligned, or fights the palette | `/design-checklist` |
| a page has no title, description, canonical or share card | `/seo-checklist` |
| the bundle is enormous, the fonts block, the images are unsized | `/perf-checklist` |

Handing one over is a recorded step, not a decision to skip it:

```bash
stack-pass.py delegate --to design --note "the bar sits 4px under the CTA on mobile at 375px"
stack-pass.py delegate --none        # nothing outside this checklist came up
```

PRC-05 stays open until one of those two has been called. A finding dropped because it
belonged to somebody else is the failure this gate exists to catch.

## This skill acts

`/stack-checklist` is an instruction to change the project and the settings behind it,
not to grade either. A gate that is not met is a key to move, a policy to write, a
branch rule to apply, or a paused project to resume.

## Two things that make it unlike its siblings

**It needs credentials, and it never holds one.** Every probe reads what it needs from
the environment of the call that runs it. Pull each from CryptoFort at the point of use,
and never into a file, a repo, or the synced config:

```bash
SUPABASE_ACCESS_TOKEN=<pat> SUPABASE_URL=<url> SUPABASE_ANON_KEY=<key> \
STRIPE_SECRET_KEY=<key> CLERK_SECRET_KEY=<key> VERCEL_TOKEN=<token> \
  scripts/probe.py all --root . --url https://<host> --out /tmp/stack-probe.json
```

**A large part of it cannot be read out of source.** A paused Supabase project, a
development Clerk key in production, a Stripe webhook nobody registered and a promotion
that merged and never built all leave the repository looking correct. Those gates answer
to a request that was actually made, and `resolve` refuses `pass` and `na` on every one
of them. A probe that could not be taken is filed as failing with the reason, so an
unverified service and a verified one never look alike.

## The run protocol

**A gate that is not met is work to do.** The only ways past one are changing the
project, or naming the condition that puts the gate out of scope. A gate the sweep
failed cannot be recorded as passing, and a gate marked fixed reopens if the next sweep
still fails it.

### 0. Find the tool

```bash
SP="$HOME/.claude/checklists/stack/bin/stack-pass.py"
PROBE="$HOME/.claude/checklists/stack/scripts/probe.py"
```

The engine resolves its gate registry from its own location, so a copy vendored into a
repo works the same way. If neither path resolves, the gates cannot run - say so rather
than proceeding by eye.

### 1. Open the run

```bash
stack-pass.py start --kind <site|app|api|tool|library> --target <path> \
  --url https://<host> --title "<what this is>"
```

It prints the gates that apply, derived from what the project contains: Supabase gates
when there is a Supabase client, Stripe gates when there are price identifiers,
TypeScript gates when there is a tsconfig, repository gates when the remote is one of
ours.

`--url` is what makes the live page, the domain, the deployed origin and the status
board checkable. Without it those gates fail with "no URL given", which is correct: a
site nobody named cannot be loaded.

Three conditions cannot be read out of a tree, because an internal tool and a public
site are the same React application:

```bash
--flag public-site   # it has visitors, so it owes observability and legal routes
--flag app           # it does not; the same gates come out
--flag auth          # it makes access decisions
--flag payments      # it takes money
```

### 2. Probe before anything else

Nothing else in the run can stand without this, and it is where the real findings are.

```bash
scripts/probe.py all --root <path> --url https://<host> --out /tmp/stack-probe.json
stack-pass.py probe --file /tmp/stack-probe.json
stack-pass.py services      # what this project talks to, and what each one answered
```

Take the probe again after changing a service setting. It is the only thing that can
say the change took.

### 3. Sweep the source

```bash
stack-pass.py verify        # every automated check, with the file and line of each failure
```

Run it after a build where the build matters: ENV-03 reads the built client bundle
rather than the source, because a key reaches it through a re-export or a logging line
that reads nothing like a leak.

### 4. Change the project until both pass

This is the work. Not a plan for the work. Move the keys, write the policies, add the
scripts, apply the branch rules, mount the bar.

Read `references/` for the substance behind any gate; each gate names its source file.

### 5. Answer every gate

```bash
stack-pass.py status --full
stack-pass.py resolve --batch - <<'JSON'
[{"id": "TLG-03", "status": "fixed", "note": "added the format:check step to ci.yml on both branches"},
 {"id": "LEG-03", "status": "na",    "note": "no analytics and no tracker; the site sets no cookies"}]
JSON
```

`fixed` for a gate whose failure you removed. `na` for one the tool brought in wrongly,
with the condition that puts it out of scope. `disputed` for a check that measured
wrongly, with what you measured instead — it closes the gate so one bad check cannot
jam a run, and it leads the report under its own heading, because every dispute is
either a defect in the check or a rule that went unenforced.

Every file under the targets ends either changed or explicitly ruled as needing no
wiring change, and a file cannot be cleared until it has been read:

```bash
stack-pass.py files
stack-pass.py file-clear --batch - <<'JSON'
[{"path": "src/lib/format.ts", "note": "read in full; pure date maths, talks to nothing"}]
JSON
```

### 6. Report and close

```bash
stack-pass.py report        # the filled checklist, shown in full
stack-pass.py finish
```

The report leads with what answered over the network, then what changed, then what was
handed to another checklist. `finish` refuses while anything is open. A run that changed
nothing closes with `finish --no-deliverable`, and that works only when the run edited
nothing — a file whose content is back to what git has counts as untouched.

## Running one part of it

```bash
stack-pass.py scopes
stack-pass.py start --kind site --target . --scope services
stack-pass.py scan --scope all
```

The named scopes are services, env, chrome, tooling, version, repo, deps, auth, observe,
live and legal. Every scoped run leads with the narrowing and with what it did not look
at.

## What holds this in place

- A `PostToolUse` hook on `Skill` opens a run when the skill is invoked, and on nothing
  else. Unlike its siblings, editing a file never opens one: a config edit is not
  evidence that anybody wanted eighty gates in front of a one-line change.
- A `PostToolUse` hook on `Read` and `Bash` credits a file in the ledger once it has
  been read, so an unopened file and a considered one stop looking alike.
- A `Stop` hook refuses to end the session while the run is open.
- A `PreToolUse` hook holds back `git commit` and `gh pr create` while a gate is
  unanswered, so the wiring cannot land ahead of the checklist.
- PRC-02 names every service the project talks to, so scoping to the one the prompt
  mentioned has to be argued for rather than assumed.

## What a gate cannot be answered with

- **`pass` on a gate the sweep failed.**
- **`na` on a gate the sweep failed.** The sweep failing is proof the condition is
  present and unmet, which is the opposite of inapplicable.
- **`na` while the tool can still see the condition.** A gate enters a run because its
  condition was found. If it is still there, not-applicable contradicts the tool.
- **`pass` or `na` on a gate that answers to a probe.** A service is reachable or it is
  not, and no attestation can tell those apart. If the service is not part of this
  project at all, remove the dependency rather than answering n/a.
- **A note with no evidence.** An assertion is not a note. It has to name a file, a
  line, a measurement, or quote what was read.

## What this skill does not do

It does not write application code, restyle anything, or touch copy. It does not grade
performance or search visibility. Where a finding belongs to design, SEO or performance,
it is delegated by name and carried in this run's report — and answered there.
