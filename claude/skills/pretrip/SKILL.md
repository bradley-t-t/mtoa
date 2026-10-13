---
name: pretrip
description: The inspection before a release - clear the pull requests a release would strand, baseline the live site and the host, read the words the release will carry with humanizer and whichever marketing skills the changed files call for, sweep what the last release left behind, and say what is waiting to go live. Use on /pretrip, or "is this ready to release", "check before we ship", "pre-trip", "anything waiting". Run it before /merge; /ship runs it in between.
---

# pretrip

The walk around the truck before the trip. **`/merge` proves the release
shipped. `/pretrip` owns the standing health of the repo, the machine and the
fleet** - everything that has to be true before a promotion, and everything left
lying around from the last one. It runs the same whether or not there is
anything to release; a repo with nothing to release is the case it most needs
to run on. Nothing here touches `main`, moves the version or deploys.

The workflow is `/commit`, `/pretrip`, `/merge`, or `/ship` for all three. The
readings are one tool, `~/.claude/tools/release-yard.py`, run from inside the
checkout. Shell variables do not survive between commands, so the commands
below name what step 1 prints - `<slug>`, `<primary>` - and the file step 3
writes - `<live-file>` - as placeholders to fill in.

Why each rule below exists, with the incidents that set it, is
`~/.claude/standards/release-history.md`. Read it before changing a rule here.
No command in this file uses a positional parameter - a dollar sign and a digit -
because skill arguments are expanded into the body before it runs.

## 1. Read the machine

```bash
~/.claude/tools/release-yard.py preflight pretrip
```

It prints what the next release carries, then `slug=`, `primary=`, `has_site=`
and `monitored=` lines. It refuses - `REFUSE:` and exit 1 - when `develop` or `main` is
missing, when it could not ask whether a release is in flight, or when one is.
A release in flight is not a problem to clear: say whose it is and stop.

`has_site` empty skips the site baseline and the host. `monitored` empty - a repo
outside the profile's `github.owners` - skips the host.

## 2. Hold the sessions this can collide with

Only the peers in this repo: those whose name begins with the basename of the
primary checkout or one of its worktrees, followed by `-`.

```bash
git worktree list --porcelain | sed -n 's|^worktree ||p' | while read -r p; do basename "$p"; done
```

Filter `ListAgents` to those, drop any peer that has sent this session a stop
notice, and `SendMessage` each: `Running /pretrip on <SLUG>. Hold every push,
pull request, /commit and /merge to this repo until I report done or failed.`
If none match, send nothing and say so in one clause.

Holds arriving here get no reply. The only things sent back are a stop notice
to a peer sending what this session does not need, and doing what a message
asked - the land-or-draft request in step 5 is the one this command sends.

## 3. Where the live sessions are

Call `list_sessions` once and write the `cwd` of every session whose
`isArchived` is false - this one included - one per line, to a file. Never
filter on `isRunning`, which is false for a session idle between turns. Where
`list_sessions` is unavailable (a cloud session, the host), match `ListAgents`
names against worktree basenames instead, write those worktree paths, and say in
the report that the weaker test was used.

That file is `<live-file>` below; put it in the scratchpad.

## 4. Push what origin has no copy of

This runs before anything else that can remove a directory, and it is the only
step that is urgent rather than tidy: everything else here is late work, and
this is work that exists on one disk.

```bash
~/.claude/tools/release-yard.py unpushed --live <live-file>
```

Every `nobody is in it, push it now` line is pushed now, whatever its age -
not reviewed, not landed, pushed. A push is reversible and needs nobody's
agreement. A `live session` line is a session mid-work; name it in the report
and leave it.

```bash
git -C <primary> push -u origin "<branch>"                                          # a branch
git -C <primary> branch "<name>" "<sha>" && git -C <primary> push -u origin "<name>"   # a detached HEAD
```

## 5. Clear what would strand the release

```bash
~/.claude/tools/release-yard.py waiting
```

A release carries `develop` as it stands, so a green pull request left open
misses this release and every one after it, and `/merge` refuses over this
list. Clearing it is this step's job. **Never a `version-*` pull request** -
that is a release in flight, and it ends the run the same way step 1 does.

Every other entry gets one of four outcomes:

- **Green and mergeable.** Read the diff as you would your own - the release
  carries it - then land it. `/commit` does not do this; it lands only the
  worktree it stands in.
  ```bash
  gh pr view <n> --repo <slug> --json isDraft,state,mergeable,mergeStateStatus,updatedAt,headRefOid
  gh pr checks <n> --repo <slug> --watch
  gh pr merge <n> --repo <slug> --squash --delete-branch
  ```
- **Behind `develop`.** Rebase it in its own worktree if that tree is clean,
  push, wait for `check`, merge. A conflict is somebody's work: the fourth case.
- **A live session owns it** - a peer named after its worktree. Send that one
  peer a single message naming the pull request and asking it to land or draft
  it, then watch the pull request, not the inbox. Never merge it yourself while
  a session is in it.
- **Not ready.** Mark it a draft, or close it - but only the person who can say
  it is not ready gets to. Where that is not clear, stop the run, naming the
  pull request and why.

Re-run `~/.claude/tools/release-yard.py waiting` afterwards; it is what `/merge` will find. Never release
past this list without knowing, and never walk away from a release because
something else was open.

## 6. Baseline the live site

Skipped when `has_site` is empty. This is the reading `/merge` cannot take,
because by then the site is the new one.

```bash
curl -sI --max-time 20 https://<domain>/ | head -3
curl -s  --max-time 20 https://<domain>/release.json
```

The domain is the shortest `www.` or apex custom domain on the Vercel project,
cross-checked against `gh api repos/<slug> --jq .homepage`. FleetApp's
project is under another account: take the domain from its README and read
`https://www.fleetapp.app/api/version`, which reports the live commit.

The version served must equal `package.json` on `origin/main`; anything else
means a previous release merged and did not ship, and that is reported before
another goes on top of it. Then open the site in the in-app browser and read
`read_console_messages` and `read_network_requests`. Errors found now belong to
the build already live - that is exactly why they are recorded first.

## 7. Ask the host

Skipped when `has_site` or `monitored` is empty.

```bash
~/.claude/tools/release-yard.py pi
```

Exit 0 is a live feed. Exit 1 is a feed older than fifteen minutes: the host is
down and nothing in the feed means anything. Exit 2 is no answer at all, which
is a different finding - run `ssh <host.ssh> 'systemctl is-active
error-reporter.service'` before calling the host down, because this machine may
simply not reach it. The site must read `operational`, and the open tickets are
the baseline the release is made on top of. A site missing from the feed was
never onboarded; say so rather than reading its absence as quiet.

## 8. Read the words the release will carry

```bash
~/.claude/tools/release-yard.py copy-files
```

The list it prints is `COPY_FILES` below; `no reader-facing files` skips this
whole step in one line. Otherwise the scope
is exactly those files - what `origin/main...origin/develop` changes - and
never the rest of the tree however badly it reads; `/predator` sweeps a whole
project, and a tree that wants one gets that said in one line at the end. A
reader is somebody using the product: comments, prompts, skill bodies and commit
messages are never rewritten as copy, and the setup mirror holds none.
The pass runs inside the hold, because it lands a branch.

**Which skills.** `humanizer` always. Every other skill only where
`COPY_FILES` holds the surface it governs, per the table under "Which skills"
in `~/.claude/skills/predator/SKILL.md` - read the list against that table, not
from memory. Six agents at most, usually fewer.

**Read.** One subagent per chosen skill, launched in one message. This stage
edits nothing.

```
Agent({
  subagent_type: "general-purpose",
  description: "<skill> sweep",
  prompt: "Invoke the <skill> skill with the Skill tool and read it in full before you look at anything else.
Then read these files, and only these files, for every place the text a reader sees breaks that skill's
guidance: <COPY_FILES>. That list is what this release changes - git diff --name-only
origin/main...origin/develop. Do not open anything outside it and do not widen to the rest of the tree: a
string this release did not touch is out of scope however badly it reads. Read each file's diff for what
changed, then the file around it for the context that change sits in.
Report each finding as: file, line, the exact string as it stands today, the exact string that replaces it,
and which part of the skill it answers to. A replacement you cannot write out is not a finding.
Do not edit anything. This stage only reads.
Say what you covered, so a reader can see what you did not reach."
})
```

A finding is a string you can point at; zero findings is a clean and is
reported as one. A finding outside the release is named in one line and left.

**Rewrite, in a worktree of its own** - never the one this run stands in.

```bash
date -u +copy-%Y%m%d-%H%M%S      # <copy-branch>; <copy-worktree> is <primary>/.claude/worktrees/<copy-branch>
git -C <primary> worktree add -b <copy-branch> <copy-worktree> origin/develop
```

One subagent per file, so no two hold the same file, each handed the `HOUSE`
block from `~/.claude/skills/predator/workflow/predator.js` verbatim. Invent
nothing - not a number, customer, claim, guarantee or date; every fact in the
new sentence was in the old one or in the repo. Never write context-derived
copy. UI labels stay Title Case, prose sentence case. A marketing skill that can
send, post or schedule stops at the file. Then the repo's own gates, and the
pull request:

```bash
cd <copy-worktree> && npm run build && npm test
git -C <copy-worktree> add -A && git -C <copy-worktree> commit -m "<what changed, in the repo's voice>"
git -C <copy-worktree> push -u origin <copy-branch>
gh pr create --repo <slug> --base develop --head <copy-branch> --title "<what changed>" --body "<the findings, by file>"
```

**Prove it on the preview, never the live site** - the live site still shows
the old words. One independent subagent per changed file, not the one that
wrote it: open the pull request's Vercel preview, screenshot the route at
desktop and mobile, and answer whether the finding is gone and the page still
claims what it claimed. Uncertain is a fail; an invented fact is a fail and the
file goes back with the reason. A string that renders nowhere - meta, alt,
JSON-LD - or a repo with no preview is proved on the diff and the re-run search.

**Land it or close it.** `gh pr checks <n> --watch`, then `gh pr merge <n>
--squash --delete-branch`. Nothing this command opens stays open; the worktree
is left for the sweep.

## 9. Land the work nobody is coming back for

```bash
~/.claude/tools/release-yard.py abandoned --live <live-file>
```

Abandoned means no live session in it and its newest edit over a day old, or a
branch carrying commits `develop` lacks. Everything it prints is landed by the
ordinary route, as `/commit` done on somebody's behalf: a worktree cut from
`origin/develop`, the repo's own build and tests, the change read in the
browser at the routes it touches, a commit in the repo's voice, a pull request
into `develop`, `check` green, squash merge. Read the diff in full first.

Two pieces touching the same file are reconciled into one change that satisfies
both, keeping every rationale still true of the result - never pick a side, and
never invent a tuning reason the diffs do not give. One agent per independent
chunk, launched together; chunks that share a file go to one agent.

Never discard it, land it past a red check, widen it, or rewrite somebody's
prose in passing. Where a diff's intent is unreadable, or two chunks contradict
in a way only a person can settle, name it and stop the run.

## 10. Sweep what the last release left behind

```bash
~/.claude/tools/release-yard.py sweep --live <live-file> --dry-run     # optional: see it first
~/.claude/tools/release-yard.py sweep --live <live-file>
```

It prunes remote-tracking refs; fast-forwards local `develop` and `main`
(reporting, never rewriting, one with commits of its own or one an open file
blocks); removes worktrees whose branch is gone or landed - never this
session's, never one a live session stands in, never a dirty one, never one
holding an ignored file that is neither rebuildable nor identical in the
primary checkout; and deletes branches whose upstream is gone, that are merged,
or whose work is squash-merged onto `develop`. It refuses without `--live`.

Eight `swept:` lines are the evidence it ran; fewer is a step that did not.
The last block, `left standing`, is the one that matters: every survivor must
name a reason - it carries commits `develop` lacks, its worktree is dirty or
holds a file found nowhere else, or a live session is in it. A `NO REASON` line
is a defect in the sweep, and the run is **not ready** until that branch is
gone or somebody says what it is for. A `NEVER PUSHED` line means step 4 was
skipped; the fix is the push.

Report what was removed and move on.

## 11. Say what is waiting to go live

```bash
~/.claude/tools/release-lag.py --root <primary>
~/.claude/tools/release-lag.py
```

The first answers for this repo; the second, bare, for every checkout on the
machine, which is what catches a repo nobody has opened in a week. Expect
`clear`. Otherwise say it aloud:

- **Never pushed.** Step 4 not having run - not ready, fix with the push - or a
  peer's work in progress, which is reported and does not hold the release.
- **Commits on `develop` no release carried.** Say the count and that they go
  out next time.
- **A pull request open against `develop`.** Name it.
- **Pull requests it could not read.** Not clear: say why, and do not close the
  run as clean over a check that did not happen.

## 12. Prune the Recent-folders list

```bash
~/.claude/tools/recent-folders-prune.py
```

Every run, whatever the verdict. It keeps the folders in `desktop.keep_folders`;
`--keep NAME` adds another. It is a no-op where the desktop app is not installed.

## The verdict

One line: **ready**, or **not ready and why**. `/ship` reads it to decide
whether to go on.

Ready means: nothing in this checkout is unpushed except a live session's own
work in progress - first because it is the only condition a person cannot
recover from afterwards; the pull request list is clear or cleared; no release
is in flight; the live site is up and serving what `origin/main` says; the host
is alive with the site `operational`; the copy pass found nothing or landed
what it found; every abandoned piece was landed or named; the sweep printed
eight `swept:` lines with nothing left standing without a reason; and
`release-lag.py` was readable.

Not ready names the one thing and stops: a pull request nobody present can call
not-ready, a host that cannot be reached, a live site serving the wrong version,
unreadable pull requests, a branch left standing with no reason, a branch origin
has no copy of that nobody is working in, or abandoned work whose intent could
not be read. None of these is cleared by running `/merge` at it.

## Release the hold

`SendMessage` the same peers: `Done with /pretrip on <SLUG>. <ready|not ready>.
Hold lifted.` A run that stopped early sends the same with one sentence naming
where. No peer announced to, nothing to send.

## What this does not do

It does not touch `main`, move the version, open a release pull request or
deploy. The only branches it lands are its own copy pass and recovered
abandoned work, both into `develop` and green like any other; every other pull
request it clears lands on its own checks. It never destroys work: everything it
removes, git can already see somewhere else, and everything it cannot land it
names.
