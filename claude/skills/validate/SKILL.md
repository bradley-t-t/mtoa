---
name: validate
description: Stop and prove we are talking about the same thing - my complaint restated in plain English at the top, then a short itemized list of what is actually wrong and a short itemized list of what to do about it. Use on /validate, or "are we talking about the same thing", "what do you think I am asking", "restate this", "recap in plain English", "summarize what is going on", "what is the actual problem here". Also reach for it unprompted when a thread has gone long or sideways and the shared picture is worth checking before more work lands.
argument-hint: "[the complaint, if there is no thread to read it from]"
---

# validate

A checkpoint. It answers one question - do we both think this conversation is
about the same thing - and it answers it in English I can scan in ten seconds.

The failure it exists to catch is the expensive one: an agent that understood
the words, missed the point, and spent an hour building against its own reading.
Restating the complaint back to me is the cheapest way to surface that, and it
only works if the restatement is genuinely a rewrite. Echoing my sentence back
proves nothing except that the text was copied.

## The reply

Three parts, this order, nothing before them and nothing between them.

**What you are saying.** One short paragraph, my complaint in your words. Not a
quote, not my phrasing lightly shuffled - the thing you believe I am upset about
or asking for, said the way you would say it to somebody who just walked in. If
the thread has drifted, this is the drifted-to version, because that is what we
are actually working on now. Where the ask is genuinely ambiguous, name the
reading you took and say what the other one was, in one sentence.

**Issues.** A numbered list of what is actually wrong. One line each, the
problem itself and not its history.

**Actions.** A numbered list of what to do about it. One line each, imperative,
each one a move somebody could start on. Not a research plan, not "look into
X" - what to change and where.

No preamble, no closing offer to get started, no recap of the conversation that
led here.

## Validate means go and check

The middle word is the work. Every line in the issues list is a claim about the
system as it is right now, so it gets confirmed against the system before it is
written down - the file read, the endpoint hit, the log queried, the queue
enumerated. A line that turned out to be wrong is worth more than the whole rest
of the reply, because it is the assumption the next hour would have been built
on.

This binds hardest on absence. "Nothing is running that", "no routine covers
this", "that never fires" are claims about every machine and every schedule, and
they go in the list only after the peers, both crontabs, the host's timers, the
scheduled cloud tasks and the repo's Actions have actually been enumerated. If
checking one costs more than the checkpoint is worth, say the line is unverified
rather than dropping it or asserting it.

Where my account and the system disagree, both go in the list. What I saw
happened; a check that says otherwise means something between the event and the
check is lying, and that is its own issue.

## It stops there

`/validate` produces the reply and ends. Reading, querying and probing are the
job; editing, committing, deploying and sending are not, however obvious the fix
in Actions looks. The point of a checkpoint is that I get to redirect at it. An
action taken before I have read the list is the misunderstanding this skill was
called to catch, now written to disk.

## When there is nothing to read

Called with an argument and no thread behind it, the argument is the complaint,
and the same three parts apply. Called with neither, say so in one line rather
than inventing a complaint to validate.

## Plain English throughout

Every line in both lists reads to somebody who does not have this repo open.
Names of real things - files, services, branches - stay, because they are the
specifics; jargon that only restates the problem in harder words goes. No emoji.
