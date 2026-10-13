---
name: commit
description: Land the branch in the current worktree onto develop - committed, pushed, opened as a pull request into develop, CI passed, squash merged, branch and worktree removed. Use on /commit, or "land this", "get this onto develop", "ship this branch". The inspection before a release is /pretrip and putting develop into main is /merge, not this.
argument-hint: "[a subject line for the squash commit]"
---

# commit

Puts a branch that came off `develop` onto `develop`.

Every project I work on holds two long-lived branches - an owned web project, a
personal side project like a 2D Java game, the shape is the same. Work lands on
`develop`. `main` is the release, and where the repo serves a site, that is
what deploys. This command covers the first move only, and applies wherever the
two-branch shape holds; nothing here is specific to the owned web repos.

## Refuse rather than guess

Run the preflight. If any line fails, say which one and stop. Do not route
around a failure, and do not ask to.

```bash
git rev-parse --show-toplevel >/dev/null || exit 1
ROOT=$(git rev-parse --show-toplevel)
SLUG=$(git -C "$ROOT" remote get-url origin | sed 's|.*github.com[:/]||; s|\.git$||')
BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
git -C "$ROOT" fetch --quiet origin develop

case "$BRANCH" in develop|main) echo "REFUSE: on $BRANCH, which is a long-lived branch"; exit 1;; esac
git -C "$ROOT" show-ref --verify --quiet refs/remotes/origin/develop || { echo "REFUSE: $SLUG has no develop"; exit 1; }
git -C "$ROOT" merge-base --is-ancestor "$(git -C "$ROOT" merge-base origin/develop HEAD)" origin/develop || { echo "REFUSE: $BRANCH did not come off develop"; exit 1; }
git -C "$ROOT" diff --check || { echo "REFUSE: conflict markers in the tree"; exit 1; }
UNLANDED=$(git -C "$ROOT" cherry origin/develop HEAD 2>/dev/null | grep -c '^+')
[ -n "$(git -C "$ROOT" status --porcelain)" ] || [ "$UNLANDED" != "0" ] || { echo "REFUSE: nothing to land"; exit 1; }
```

The last test reads patches rather than commits, and it has to. `develop` takes
a squash, so landing a branch rewrites its commits into one new commit with a
new SHA, and the branch that was just merged still has commits `develop` cannot
reach. `git log origin/develop..HEAD` counts those and reports work to land on a
branch whose work is already on `develop` - which is not an edge case but the
state every branch is in the moment after it merges. A second `/commit` in the
same session therefore sailed through the preflight and would have opened a
duplicate pull request for work already landed, which is how this was found.

`git cherry` compares patch ids instead of SHAs: `+` marks a commit whose patch
is not upstream, `-` one that is, however it got there - squash, rebase or
cherry-pick. Zero `+` lines and a clean tree means there is genuinely nothing to
land. It is the same test the `/pretrip` sweep uses to decide a local branch is
safe to delete, and the two should agree about what "already landed" means.

`nothing to land` ends the landing and not the run. Step 7 still runs, because
what it looks for was never on this branch: it reads what is sitting on
`develop` unreleased and what is open against `develop`, and an empty branch
here says nothing either way. A branch with nothing on it is in fact the most
likely place to be standing when something else has been waiting, since it is
where a session lands after its work has already gone in. Report the refusal,
run step 7, say what it found, and stop there.

Where `/commit` was asked for alongside the commands that follow it - as
`/ship`, or as several named in one message - this refusal is not an answer
about those. `/pretrip` and `/merge` have their own preflights and reach
conclusions this one cannot: whether the live site is serving what `develop`
says, whether the host has filed anything, what the last release left behind to
sweep, and whether the promotion shipped. Hand it over rather than answering
for them.

Two more refusals no script settles. A tree holding changes from work you did
not do is not yours to land, so read `git status` and `git diff` before
committing and stop if either shows something you cannot account for. A branch
that is somebody else's session's worktree is theirs; check `git worktree list`
against where you are.

## Announce to the sessions this can actually collide with

Concurrent sessions on this machine can be pushing to the same remote, moving
the same version, or landing another branch off the same `develop`. A note lets
them hold rather than collide.

**Only the sessions in this repo.** A peer working somewhere else cannot
collide with this run, and telling it to hold is noise it has to read, weigh
and answer - which it does, because a hold sounds like it needs one. That is
not a small cost: over 2026-09-03 to 09-05 the two commands sent 148 messages
between sessions, most of them to peers named after the home directory, which are
sessions open in the home directory and not in any repo at all. One session
spent the seventeen minutes after its release had already shipped doing nothing
but acknowledging holds, relaying warnings, and answering messages that arrived
stale and out of order. None of it touched the release.

A session's name is the basename of the directory it is open in plus a short
suffix, so the peers in this repo are the ones named after the primary checkout
or one of its worktrees.

```bash
PRIMARY=$(git -C "$ROOT" worktree list | head -1 | cut -d' ' -f1)
git -C "$ROOT" worktree list --porcelain | sed -n 's|^worktree ||p' \
  | while read -r p; do basename "$p"; done
basename "$PRIMARY"
```

Call `ListAgents`, keep only the peers whose name begins with one of those
names followed by `-`, minus any peer that has told this session to stop
sending - a stop notice holds for the life of the session - and `SendMessage`
those and no others, naming the skill, the repo slug and the branch: `Running /commit on <SLUG> branch <BRANCH>. Hold
any push to this repo until I report done or failed.` Wait no more than a few
seconds for acknowledgements and proceed - the message is a courtesy, not a
lock. Send a follow-up on the same channels when the run finishes, so peers
know the hold is lifted.

Where the list comes back empty, send nothing and say so in one clause. A run
with no peer in the repo is the normal case, not a reason to broadcast wider.

The filter is deliberately narrow at the edges. A peer whose name is a socket
address or a cloud session cannot be placed in a repo and is left out, and that
is the right trade: the announcement was never the thing keeping two sessions
off each other. The fetch, the rebase, `--force-with-lease` and a strict
`develop` are, and every one of them holds against a session that never heard a
word from this one.

## Receiving a hold

A hold that names a repo this session is not working in is not addressed to
this session, whatever it says about holding everything. Do not stop work, do
not reply, and do not relay it onward. Read the repo slug in the message, and
if it is not the repo under this working directory, that is the end of it.

Silence is the whole of that. "This does not apply to me", "acknowledged, not
relevant here", "ignoring - different repo", "no action needed on my end" are
the failure this section exists to stop: a message whose entire content is that
no message was needed, which the sender pays a turn to read and learns nothing
from. It is worse than the hold it answers, because the hold at least carried a
fact. Read the slug, drop the message, carry on.

The one thing worth sending back is a stop. A peer sending this session
messages it does not need - holds for a repo it is not in, status it never
asked for - is told once, in one line: `I am not in <slug>. Stop sending me
holds for it.` That is one message per peer for the life of the session, sent
in place of the silence rather than on top of it, and nothing follows it: no
second notice however many more arrive, and no answer when the peer replies.
Whether the sender honours it is the sender's business; this session goes quiet
to that peer either way. A stop notice arriving here is honoured the same turn -
take that peer out of the announce list above for the rest of the session, and
do not confirm it.

A hold from a peer in this repo is honoured by holding. It is not answered.
The announcement asks for a hold, not for an answer: the session that sent it
waits a few seconds and proceeds, gates nothing on a reply, and reads its own
git, `gh` and `list_sessions` for everything a reply could carry. So the reply
is a turn spent in that session's inbox to establish that nothing needed doing.

Over 2026-09-06 to 09-08 the three commands sent 370 messages between sessions,
57 of them replies to a hold. They took three shapes, and each is something the
session it was sent to already reads from a source of truth:

- **"Nothing in flight here, go ahead."** Forty-seven of the fifty-seven. The
  run proceeds identically whether it arrives or not.
- **"`develop` moved to `<sha>`, rebase before you push."** Fourteen. Every one
  of the three commands fetches `origin` on its own - this one twice - and
  rebases when it is behind. A peer's reading of the remote is the same
  reading, taken earlier and going stale in transit.
- **"I am live in this worktree, leave it in place for your sweep."** Twelve.
  `/pretrip` reads every live session's `cwd` from `list_sessions` before it
  removes anything, and says where the sweep is written that the guard belongs
  there and not in a peer's inbox. Sent twelve times in three days regardless.

Two things still go back, and they are the only two. A stop, as above. And an
answer to a message that asked this session to *do* something rather than to
hold - `/pretrip` asking the session that owns a pull request to land or draft
it - where the answer is landing or drafting it, and one line only where it
cannot be done here.

Messages between sessions are delivered out of order and sometimes long after
the thing they describe is done, so a hold whose work has visibly finished -
the release is out, the branch is merged - is stale twice over.

## The run

**1. Read what is actually changing.** `git status --porcelain` and
`git diff` over the whole tree, staged and not. The subject line comes from
this, not from the conversation.

**2. Commit.** One commit, everything staged.

```bash
git -C "$ROOT" add -A
git -C "$ROOT" commit -m "<subject>"
```

The subject is one line, imperative, sentence case, naming what changed in the
project's own terms: `Put the hero's entrance back and let the tag settle
first`. No conventional-commit prefix, no emoji, no trailer, no mention of any
tool or model. CI reads every commit on the pull request and refuses the branch
over any of those.

**Nothing here moves the version.** `package.json`, `public/release.json` and
the README badge are release metadata, and they move once per release under
`/merge`. Two branches open at once would otherwise rewrite all three to the
same new value and conflict on every one of them by construction.

**3. Push, under a name no other session holds.** Sessions run concurrently and
pick branch names off the same work, so the name is checked against the remote
rather than assumed free. A collision is settled by renaming this branch, never
by forcing over what is already there.

```bash
git -C "$ROOT" fetch --quiet origin
if git -C "$ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH" &&
   ! git -C "$ROOT" merge-base --is-ancestor "origin/$BRANCH" HEAD; then
  git -C "$ROOT" branch -m "$BRANCH-$(git -C "$ROOT" rev-parse --short HEAD)"
  BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
fi
git -C "$ROOT" push -u origin "$BRANCH" || {
  # Two sessions pushing the same new name in the same moment both saw it free.
  git -C "$ROOT" branch -m "$BRANCH-$(git -C "$ROOT" rev-parse --short HEAD)"
  BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
  git -C "$ROOT" push -u origin "$BRANCH"
}
gh pr create --repo "$SLUG" --base develop --head "$BRANCH" \
  --title "<the same subject>" --body "<what changed and why, in prose>"
```

Say so when the branch was renamed. A `gh pr create` against a name somebody
else pushed comments on their pull request instead of opening one.

**4. Wait for `check`.** It is the required context on `develop`, and it runs
attribution, format, lint, typecheck, tests and the build.

```bash
gh pr checks <number> --repo "$SLUG" --watch
```

A failure is work to do: read the log with `gh run view <id> --log-failed`, fix
the cause in the branch, push again. Do not merge past a red check and do not
edit the workflow to make it green.

**5. Squash merge.** `develop` is strict, so the branch has to hold the
`develop` that is there now. When the merge is refused as behind, something
landed while this branch was open: rebase onto it, push, and wait for `check`
again, because the tree that passed is not the tree that would land.

```bash
git -C "$ROOT" fetch origin develop
git -C "$ROOT" rebase origin/develop
git -C "$ROOT" push --force-with-lease
gh pr checks <number> --repo "$SLUG" --watch
```

`--force-with-lease` rather than `--force`: it refuses when anything reached the
branch since the last fetch, which is the one case where a force push destroys
work. A rebase that conflicts is a conflict in the work itself, so read both
sides and resolve it in the branch.

```bash
gh pr merge <number> --repo "$SLUG" --squash --delete-branch
```

**6. Clean up.** Bring the primary checkout's `develop` up to date, and remove
the worktree if the work was in one.

```bash
git -C "$ROOT" fetch origin
git worktree list
git worktree remove <path>          # from the primary checkout, once merged
```

**Never the worktree this run is living in.** Removing it takes the working
directory out from under the command doing the removing, and under `/ship` it
takes it out from under the `/pretrip` and `/merge` that have not run yet - a
release left unmade over a directory that could have been swept by anyone,
later, from anywhere. Name it in the output and leave it where it is; `/pretrip`'s sweep
takes it away before some later release, from a session not standing in it.

```bash
WT=$(cd "$ROOT" && pwd -P)
PRIMARY=$(git -C "$ROOT" worktree list | head -1 | cut -d' ' -f1)
if [ "$WT" != "$(cd "$PRIMARY" && pwd -P)" ]; then
  echo "left $WT in place: this run is living in it"
fi
```

**7. Say what is now waiting to go live.** This is the step that stops work
stranding, and it is here because nothing else in the workflow looks.

`/commit` ends with the branch on `develop` and nothing live. That is correct
and it is also the exact moment work goes quiet: a commit on `develop` reads as
landed everywhere a person would look, and it stays there, unreleased, until
somebody happens to run `/merge`. Nobody else is watching that gap - not
`/pretrip` or `/merge`, which only ever look once they are already running, and
not the host, which watches live sites rather than branches.

So count it and say it, in the last line of the report, every time.

```bash
~/.claude/tools/release-lag.py --root "$ROOT" --no-fetch
```

It names three things: what is on `develop` and not on `main`, with the age of
the oldest; any non-draft pull request still open against `develop`; and any
branch or worktree in this checkout carrying a commit that was never pushed at
all. Close with one plain sentence: `N commits are on develop unreleased -
/pretrip then /merge puts them live`, or that the repo is clear. Do not go on
off the back of it unless that is what was asked; `/ship` is the command that
goes on, and the point here is that the gap is never invisible.

A repo the tool cannot read the pull requests for reports that, not silence.
Say what it said.

**Never pushed is the one line here that is not about a release.** The other
two are work waiting its turn; this one is work that exists on this machine and
nowhere else, so the remote holds no copy and removing the directory destroys
it. It is also the one this step was blind to until 2026-09-10, when a nine-file
change sat in a worktree while every command in the workflow reported the repo
clear - because the other two readings ask origin, and origin had never heard of
it. A branch named here is pushed before the run closes, even when there is
nothing else to do with it: `git push -u origin <branch>` costs nothing and is
the difference between work that is late and work that is gone.

**8. Release the hold.** `SendMessage` the same peers - the ones in this repo,
and no others - a one-liner: `Done with /commit on <SLUG> branch <BRANCH>. Hold
lifted.` A run that ended in failure gets the same message with `Failed` in
place of `Done` and one sentence saying where it stopped, so peers do not sit on
a hold that will never lift. Where no peer was announced to, there is no hold to
lift and nothing to send.

## What this does not do

It does not touch `main`, it does not move the version, and it does not deploy.
Nothing is live until `/pretrip` and `/merge` run, which is why step 7 exists.
