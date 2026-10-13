---
name: queue
description: Wait for the sessions that are working right now to finish, then run /ship - land the branch onto develop, inspect what a release would strand, and release. Use on /queue, or "ship once the others are done", "queue this behind the running agents", "wait for them to finish then ship". Shipping immediately is /ship; this is /ship with a wait in front of it.
argument-hint: "[a subject line for the squash commit]"
---

# queue

`/ship`, held until the agents that were working when you typed it have stopped.

Sessions run concurrently on this machine, and several of them land branches on
the same `develop`. A release started in the middle of that either races their
work or trips over it: `/pretrip` finds their pull requests half-finished and
stops, or lands them mid-flight, and either way somebody's branch ends up
stranded. `/queue` is the answer to that - it does not coordinate anything, it
just does not start until they are done.

## Refuse what the wait cannot fix, before waiting

Run the `commit` preflight now, in this worktree. A refusal it would give in an
hour is a refusal it gives now, and no amount of waiting changes it: not in a
git repo, no `develop` on the remote, standing on `develop` or `main`, a branch
that did not come off `develop`. Name the one that failed and stop, having
waited on nobody. Discovering it after an hour in the queue is the same
refusal, delivered at the worst possible time.

`nothing to land` is not one of those. It means this branch's work is already on
`develop` and unreleased, which is a normal state to ship from, and `/ship`
already knows what to do with it. Note it and queue anyway.

## The roster is frozen the moment you type it

Call `list_sessions` once and write down the `sessionId` of every session that
comes back `isRunning`, excluding this one. Call `ListAgents` and add any
background subagent this session started. That list is the queue.

**It never grows.** A session that starts working after the command was typed is
not in it, and neither is one that goes quiet and picks up again. On a machine
where somebody is typing into three sessions all afternoon, a roster that keeps
taking on new arrivals is a release that never happens - which looks, from
outside, exactly like a queue that is still patiently waiting.

Wait on all of them, not only the ones in this repo. A peer in another
directory cannot collide with this release, and the rule against *messaging*
those peers is about the turn it costs them to read it - waiting is silent and
costs them nothing, so the narrowing does not apply here. What was asked for is
the work that was in flight, wherever it was.

## Done means done twice

A session leaves the queue when `list_sessions` reports it not running on two
consecutive polls. One reading is not enough: a session between turns is
indistinguishable from a session finished, and a poll landing in that gap
retires it while it is mid-task. Two readings two minutes apart is cheap and
settles it.

Once a session has left the queue it stays out, even if it starts working
again. The queue is the work that existed when the command was typed.

## The wait

`list_sessions` is a tool call, so each poll costs a turn. Do not spin on it.
Poll, and if anything is still running, arm the next poll and let the turn end:

```bash
sleep 120
```

Run that with `run_in_background`, which wakes this session when it exits. Two
minutes means an hour of waiting is thirty polls, and a release never sits
cleared-but-unstarted for long.

**Never end a waiting turn without that wake armed.** A queue whose alarm was
never set has stopped, not waited, and nothing about it says so.

Close a waiting turn with one line: which sessions are still working and how
long the queue has been up. It is a genuine blocker rather than work being put
off - the wait is on other sessions, and the queue restarts itself.

## Say nothing to anyone while you wait

A peer being waited on does not need to hear it. It changes nothing that peer
does, and it costs it a turn to read. The three commands inside `/ship` open and
lift their own holds when they actually run, and those are the only messages
this session ever sends.

## When it clears

Invoke the `ship` skill, passing the argument through as the subject line, and
let it run exactly as written - `/commit`, `/pretrip`, `/merge`, and its own
rules about what stops it. Report what it reports.

Put one line in front of that report saying how long the queue waited and what
it waited on. A release that started forty minutes after it was asked for should
say why.

An empty roster is not a special case. Nothing was running, the queue was clear
on arrival, `/ship` starts immediately, and a clause says so.

## The ceiling

Two hours, then stop. Name every session still running, say the branch is still
unlanded and nothing has been released, and end there.

Do not ship past the ceiling. The wait is the whole command, and going ahead
without it is the one thing it was told not to do. Running `/queue` again takes
a fresh roster, which by then is usually the right question anyway.
