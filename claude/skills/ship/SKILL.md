---
name: ship
description: Run /commit, /pretrip and /merge back-to-back - land the branch in the current worktree onto develop, inspect and clear what a release would strand, then promote develop to main (which releases the site, where the repo serves one). Use on /ship, or "ship this all the way", "commit and merge", "land and release". Not for landing alone (/commit), inspecting alone (/pretrip) or releasing alone (/merge).
argument-hint: "[a subject line for the squash commit]"
---

# ship

Run `/commit`, then `/pretrip`, then `/merge`.

`/ship` is the promise that the work reaches the live site, so the three parts
are one obligation rather than three commands that happen to run in order. A
`/ship` that lands on `develop` and stops has not shipped anything; it has
moved the work to the place work goes quiet.

1. **Invoke the `commit` skill**, passing the argument through as the subject
   line. If `/commit` stops for any reason - refused preflight, red check,
   merge conflict, a hold from another session in this repo, anything - stop
   `/ship` there too and report which step failed. Do not go on from an
   unfinished `/commit`.

   `nothing to land` is the one refusal that is not a stop. It means this
   branch's work is already on `develop`, which is a normal state to ship
   from - the work is there, it is simply unreleased. Report it in a clause and
   go on to step 2.

2. **Keep the worktree.** `/commit` leaves the directory it is running in
   alone, and this is why: two commands have not run yet, and removing the
   floor under them turns a shipped release into a landed branch nobody
   released. `/pretrip`'s sweep takes the directory later, from a session not
   standing in it.

3. **Invoke the `pretrip` skill.** It clears the pull requests a release would
   strand, baselines the live site and the host, reads the words the release will
   carry, sweeps what the last release left behind, and ends in a verdict. Read
   the verdict rather than the prose.

4. **Invoke the `merge` skill** on a `ready` verdict. Its own preflight decides
   whether there is a release to make; on a repo that carries no site, the
   release ends when `main` is updated.

## The verdict is the gate

`/pretrip` ends with **ready** or **not ready and why**, and that is the whole
of what `/ship` reads to decide whether to go on.

**Ready** goes straight to `/merge`, without re-asking anything `/pretrip`
already answered. `/merge` runs its own cheap gates a minute later and will say
so if `develop` moved in between.

**Not ready** stops `/ship`, and it stops it the way `/pretrip` named the
reason. There are only a few, and each one is a fact somebody has to act on
rather than something running `/merge` at it would clear: a pull request
genuinely not ready with nobody present to say so, a host that cannot be reached,
a live site already serving the wrong version, a repo whose pull requests could
not be read, a branch origin has no copy of that nobody is working in. Report
it by name, say what is now sitting on `develop` unreleased behind it, and
stop - having named the thing a person has to decide, rather than having
decided it.

What is not a stop is a pull request that was merely open. Clearing those is
`/pretrip`'s job and it does it: green ones land, behind ones rebase, ones a
live session owns get one message. `/ship` exists because the caller asked for
the work to be live, and answering with "another branch was open" leaves both
that branch and this one unreleased - one stranded branch turned into a
stranded release, which is worse than what the guard was protecting.

## Nothing is lost on the way through

`/ship` is the command that touches the most of a repo at once, and it is the
one standing in a worktree while two later commands sweep worktrees. So the
first obligation is not the release, it is that no work goes missing between
the three parts.

`/pretrip` owns the test - a branch or a worktree HEAD carrying a commit origin
has no copy of - and it pushes what it finds before it does anything else. What
`/ship` owes on top of that is to not run ahead of it:

- **The worktree stays until `/pretrip` has read it.** Step 2 already says this
  and this is the other half of why. Removing the directory this run is standing
  in also removes the only copy of anything committed in it and never pushed,
  and that is not recoverable from the remote afterwards, because the remote is
  what never had it.
- **A `nothing to land` from `/commit` is not an all-clear.** It says this
  branch's patches are on `develop`. It says nothing about the four other
  worktrees beside it, which is exactly where a commit goes quiet, and it is
  the state a session is most likely to be in when one has.
- **Report what was pushed.** A `/ship` whose `/pretrip` pushed a branch says so
  in the closing report, naming the branch, even when the release went out
  cleanly. Work rescued silently is work nobody knows was ever at risk, and the
  next session learns nothing.

On 2026-09-10 a nine-file change written that morning sat committed and
unpushed in a worktree while `/commit` reported the branch clear and
`release-lag.py` reported the whole yard clear, because every reading in the
workflow asked origin and origin had never heard of it. It was found because a
person remembered writing it, which is not a control.

## Holds stay per-skill

Each of the three opens and lifts its own, to the sessions in this repo and no
others. Do not collapse them into one "starting /ship" message - a peer that
reads that cannot tell whether the branch has already landed on `develop`, is
being swept around, or is mid-release, and the whole point of the hold is that
peers know exactly which stage this session is in.

Six messages out over a `/ship`, and none back. A hold arriving here while this
runs is honoured by holding and answered with silence, whatever repo it names -
`/commit` carries why. Read it, hold if it is this repo, carry on.

## Finish by saying it is live

`/merge` ends with the proof: the deployment succeeded, the version serving is
the one that just landed, and the page loads clean. `/pretrip` has already read
the host and `release-lag.py`. `/ship` reports the release in one plain sentence
naming the version and the address a visitor uses, and adds anything `/pretrip`
found that a person still has to act on.

A `/ship` that ended anywhere short of that says where it stopped and what is
sitting unreleased because of it. "Landed on develop" is a report of an
unfinished ship, and it is never written as a success.
