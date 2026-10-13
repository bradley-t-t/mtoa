---
name: notes
description: Read and write the day's notes, which sync across every machine and every Claude account through the intelligence mirror. Use on /notes, or "note that", "make a note", "what did I note", "check my notes", "what was I working on", "did I write anything about X". Also read them before answering a question that depends on what was decided earlier.
argument-hint: "[the note to write, or a term to search for]"
---

# notes

Notes taken through the day, kept where every machine and every account reads
them.

A note written on the laptop is worth nothing to the session running on the host,
or to a cloud container opened tomorrow, unless it travels. These live under
`~/.claude/notes`, which is a sync path, so `config-sync.py` carries them to the
private mirror (`github.mirror`) and out to every other machine on its own
schedule, and `cloud/bootstrap.sh` installs them into a fresh container.

## Writing one

```bash
~/.claude/tools/note.py add "the thing worth remembering"
```

Tags go inline as `#tag`, because a note is prose first and a tag is just a word
worth finding later.

Write a note when something was **decided**, **discovered**, or **left
unfinished** — the three things a later session cannot reconstruct from the code.
A note that only restates what a commit message already says is noise; the git
log is already there. Good ones read like:

- `Google Business is at its floor of 4 posts while Facebook has 30. #social`
- `Decided the morning report reads only, never dispatches. The error monitor keeps that job.`
- `Vercel stopped deploying fleetapp after 15:15 — git integration, not CI. #open`

Never put a credential, a key or a token in a note. The mirror is private but it
is still a repository every machine and every container clones, and a secret in
a note is a secret in all of them. `~/.claude/tools/with-credential.py` is how a
value reaches a command without being written down.

## Reading them

```bash
~/.claude/tools/note.py today            # today, every machine merged
~/.claude/tools/note.py day 2026-09-04   # one day
~/.claude/tools/note.py recent 20        # the last 20, newest first
~/.claude/tools/note.py search outreach  # every note carrying a term
~/.claude/tools/note.py days             # which days hold notes
```

**Read before answering, not only when asked.** When a question turns on
something decided earlier — why a thing is built the way it is, what was tried,
what was deliberately left — `search` costs one command and is usually the
difference between an answer and a guess.

## How they travel

One file per day per machine, `notes/2026-09-04.<machine>.md`, and no file is
ever edited after it is written. That shape is deliberate: the sync settles a
contested file by last edit wins, which is right for a tool and fatal for a log.
Two machines appending to one shared file would lose whichever half synced
first. A file only one machine ever appends to is never contested, so every note
survives and a day is read by merging the files rather than by holding one.

Notes reach the mirror on the sync's own schedule — hourly on the host, and
whenever `config-sync.py sync` runs anywhere else. To hand them over straight
away rather than waiting:

```bash
~/.claude/tools/note.py sync
```

A note is not a ticket. Something that needs working goes in the queue as an
issue on the ticket queue (`github.tickets`), where the error routine can pick it up. A
note is what a person or a session needs to know, and nothing reads it but a
reader.
