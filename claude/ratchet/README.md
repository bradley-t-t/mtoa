<h1 align="center">Ratchet</h1>

<p align="center">
  <b>A turn advances or it holds. It never slips back.</b>
</p>
<p align="center">
  The session that says "just one more thing" on its way out has told you it knew<br />
  what was left and stopped anyway. This reads that sentence and refuses the stop.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/hook-Stop_and_SubagentStop-30b6ff?style=for-the-badge" alt="Stop and SubagentStop" />
  <img src="https://img.shields.io/badge/Python-3.9+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/judge-Haiku_4.5-c51a4a?style=for-the-badge" alt="Haiku 4.5" />
</p>

<br />

## What it catches

Not wrong answers. Correct answers that stop one item short and say so on the
way out.

> Everything works. **Just one more thing:** the config loader still reads the old key.
>
> That is fixed. **4 steps left** before this is releasable.
>
> Tests pass, though **3 are still failing** in the integration suite.
>
> **Want me to keep going** and do the serializer too?

Every one of those is a turn that knew what was left. A turn that can name the
remaining work is a turn that could have done it.

And the same failure wearing a suit - work handed back as though it were yours:

> **Three things for you to decide** rather than things I left half done.
>
> **What I deliberately did not add:** the bare handoff as a tell.
>
> **One thing noticed and left alone:** the scan returns on the first match.
>
> **No reply sent** to the peer; the release was over by then.

A decision you cannot make cheaply is a blocker. A menu appended to finished
work is the turn declining to choose. An omission announced is an omission.

Code is stripped before any of it runs, and so is quotation. A TODO in a diff is
the work rather than a confession about it, and a tell inside quotation marks is
the phrase being discussed rather than a claim about this turn - a report on
this check that quotes what it caught is not itself a confession.

<br />

## Three lists, and the order is the design

| Stage | Cost | What it decides |
| :--- | :--- | :--- |
| Scan | under 10ms | Does the closing text name unfinished work at all |
| Banned | under 10ms | Is it prose that is wrong however the work went - held on sight |
| Standing | under 10ms | Is it asking for an approval already given - held on sight |
| Judge | 7 to 10s | Can this agent do that work right now, itself, with nothing from you |

Almost every stop clears on the scan and nothing else runs. Only a flagged stop
pays for the judge, and banned prose never pays for it at all - there is no
context in which the sentence was right, so there is nothing to weigh.

The judge exists because a word list cannot tell deferred work from a real
blocker. *"I need your approval before I push"* and *"I'll do the rest later"*
are both remaining work in the same grammar, and only one of them is the turn's
to finish. So the flagged text goes to a headless Claude with one closed
question, and it answers `continue` or `release`.

```
stop -> scan --clear--------------------------> let through
          |
          +--banned-------------------------> HOLD, cut the sentences
          |
        flagged -> judge --release------------> let through
                     |
                  continue -> HOLD, with the work named
```

<br />

## What is never held

A turn stopping for **your approval, your decision, or a credential you hold**
is stopping correctly. Those phrasings are read straight off `tells.json` as
holds, and no tell overrides them.

A blocker excuses **the sentence it is in**, and nothing else. It used to excuse
the whole message on sight, which meant a *"your call"* in the opening paragraph
stood the check down and took a real tell in the closing paragraph with it. Now
a message that is only a blocker still clears in under 10ms, and a message that
is a blocker plus deferred work goes to the judge - which releases genuine
blockers anyway.

Everything else the agent chose to put off is fair game. Deferring is not being
blocked, and neither is declining to choose.

<br />

## What is held even though it looks like a blocker

An approval already given is not a roadblock. **Running SQL** was answered once,
for every statement there will ever be - read, write, migration, schema change,
destructive - so a turn that stops to ask before it queries is not blocked. It
is waiting on a word it already holds, which is the exact failure the rest of
this catches. Those stops are **held on sight**, with no judge, and told to run
the statement.

`standing` in `tells.json` is what takes the stand-down back. It is read over
the blocker sentence **and the one before it**, because the subject usually sits
one sentence back from the asking - *"Dropping the table takes 40k rows with it.
Approve this and I will run it."* reads as bare *"approve this"* on its own.

`asks` is the second way in, because the outright request is the easy half. A
turn that has decided not to do something rarely asks - *"I would rather you
looked at it first"*, *"holding off until you say"*, *"I have not run it yet"* -
and none of that is a hold phrase, so on the holds list alone the subject never
gets looked at. An ask counts only where the subject is already answered.
`settled` cancels a standing hit the same way it cancels a tell, so a report
that the migration ran is a report rather than a request.

The standing yes covers SQL and stops there. Deploying an edge function, pausing
or restoring a project, and creating, merging, resetting or deleting a branch
still release, and so does everything outside the database. A held turn has two
ways out that are not asking again: what it stopped for is not a statement, or
the database cannot be reached from this session at all. One line either way.

Replayed over every closing message on this machine - 20,870 of them - it holds
46, and they read almost entirely as the thing it is for: a turn stopping to
have a database write approved, several of them quoting the old rule back.

```
blocker sentence -> is its subject already approved?
                      no  -> released, the sentence is excused
                      yes -> HOLD, run the statement
```

<br />

## The count

A turn is held at most `holds_per_turn` times, counted against the user message
it is answering. So a turn that is genuinely stuck names its blocker in one line
and gets out, and the count starts over the moment you say something new.

<br />

## One attempt at a time

A hold is fed back into the turn as a user row, and the walk that finds the
closing message stops at one. Every attempt is judged on its own.

It did not, once, and the failure was invisible from either end. The feedback
sailed past, every held attempt in the turn got glued into a single buffer, and
the scan kept reading a sentence the turn had already deleted. A turn that fixed
its closing line on the first hold was held again on the old sentence, and
again, until the count ran out - three holds, three rewrites, each one stripping
more out of a reply that had been right since the second. From the outside it
read as an agent that would not listen. It was listening; the check was quoting
a message that no longer existed.

So a hold quotes the text about to be sent and nothing earlier. Both hold texts
name the two ways out and no third - do the work, or say in one line why it
cannot be done here - because a check whose only satisfiable move is deleting a
true sentence buys a clean closing paragraph by making the reply worse.
`selftest` replays the loop against a synthetic transcript.

<br />

## Said once

Every hold is drawn in the conversation, so a check that fired three times on
the same text put the same block on screen three times and changed nothing. The
turn had read it the first time and come back with the very thing it was held
on, which is a decision already made, not a reply that missed the message.

So each check says its piece once per user message. The first hold of a kind
carries the full reason. A second hold from the same check on text it has not
quoted yet carries only the new quote and a line pointing back at the reason
already in the turn. A hold on exactly what it quoted before is not said again,
and the stop goes through. A notice already shown on that message is not shown
again either. What was said lives in `<session>.said.json`, apart from the count,
and the display hook reads it too, so a reply the stop hook will let through is
never kept off the screen. `selftest` checks the ledger both ways.

<br />

## Running it by hand

```bash
~/.claude/ratchet/ratchet.py check "Done. One quirk left in the parser."
~/.claude/ratchet/ratchet.py selftest     # the corpus, both directions
~/.claude/ratchet/ratchet.py doctor       # config, wiring, judge reachability
```

`check` prints the scan verdict, the judge verdict and what the turn would have
been told. It is the way to tune a phrase without waiting to trip over it.

```bash
python3 ~/.claude/ratchet/premature.py speechtest   # the Sunday announcement rules, judge and all
```

A Sunday announcement may say the detail is on his screen only when a canvas
went there: a `/show` in the same command, or one this turn ran that answered
`"shown": true`. `canvas_shown()` reads which and the judge is told, so "the
breakdown is on your screen" passes with a canvas and is held as rule B
without one. `SHOW_CASES` and `SPEECH_CASES` in `premature.py` are what
`speechtest` replays.

Before the judge, `ear.py` reads the announcement for anything written for the
eye: "Oct 2", "CDT", "00:00", "$792.30", brackets, symbols, shorthand, units,
links, file names, versions, IDs, a chat reply's labels or list, more than
ninety words, or a yes or no said in more than ten ("Yes, every two hours." is
the whole answer). The voice reads every character, so any of them is denied with
each piece named and how a person would say it, every time it is sent that
way. `RULES` is the table, and `HELD` and `CLEAR` are what it must catch and
must not.

```bash
python3 ~/.claude/ratchet/ear.py check "It's due Oct 2 at 5 p.m. CDT."
python3 ~/.claude/ratchet/ear.py selftest
```

<br />

## The files

| File | What it is |
| :--- | :--- |
| `hook.py` | The entry `settings.json` names. Thin on purpose - the engine under it is free to move |
| `ratchet.py` | Scan, judge, verdict, and the CLI |
| `tells.json` | The word list: `tells` go to the judge, `banned` is held on sight, `holds` stand a sentence down, `standing` takes that back for a subject approved in advance, `asks` is the second way into `standing`, `settled` cancels a tell in its own sentence. Edit here, then run `selftest` |
| `corpus.json` | What the scan must catch, must cut, must hold as an approval already given, and must leave alone. `selftest` runs all four, then the transcript replay |
| `config.json` | Model, cap, timeouts, switches |

<br />

## Config

Every key is overridable per run by environment variable.

| Key | Default | Env | What it does |
| :--- | :--- | :--- | :--- |
| `model` | `claude-haiku-4-5-20251001` | `RATCHET_MODEL` | The judge. Haiku answers one closed question about text already in front of it |
| `holds_per_turn` | `3` | `RATCHET_HOLDS` | Holds allowed against one user message |
| `judge` | `true` | `RATCHET_JUDGE` | Off leaves the scan deciding alone, which is blunt |
| `judge_timeout_seconds` | `45` | `RATCHET_TIMEOUT` | |
| `judge_required` | `false` | `RATCHET_JUDGE_REQUIRED` | On, an unreachable judge holds on the scan alone instead of standing down |
| `subagents` | `true` | `RATCHET_SUBAGENTS` | Whether `SubagentStop` is checked too |
| | | `RATCHET_OFF=1` | Stands the whole thing down for one run |

A judge that cannot be reached lets the stop through - but never quietly. It
always says on screen what failed, because a check that switches itself off
without saying so is worse than no check at all.

<br />

## Why it cannot recurse

The judge is itself a Claude Code run, and a Claude Code run ends in a Stop
hook. Two independent things stop that, and either alone would do: the child
gets `RATCHET_OFF=1`, and it is launched with `--setting-sources ""`, so it
loads no settings file and therefore no hooks.

The same launch passes `--strict-mcp-config`, which is purely speed. A judge
that booted this machine's full MCP server list took 21 seconds against 4.

<br />

## Saying you can't without checking

`unchecked.py` is a sibling check wired into the same Stop pass, next to
`premature.py`. It reads the third failure, the one a word list on the closing
prose cannot tell from a real blocker: a turn that hands back *I can't reach
it*, *that's unreachable*, *I don't have access* when nothing that turn went and
looked for a route.

Cheap first. A denial phrase in the closing text is a single regex and costs
nothing on a stop that has none, which is almost all of them. Only a flagged
stop pays for the judge, and the judge is given the tool calls the turn actually
made - because the prose is exactly what is in doubt. It holds a give-up the
turn never tested and passes a dead end the turn checked, a refusal that needs
the user's approval or a credential they hold, and anything that is not a claim
the request is impossible.

```
unchecked.py check "I can't reach your Windows machine from this session."
unchecked.py selftest
```

The standing exception is the same one the account runs on: the user's own
approval is never a roadblock to route around, so a write that needs their yes
still trips nothing here. `unchecked-corpus.json` is what the first-stage scan
must catch and must not; edit it and run `selftest`.

<br />

## Telling the user no

`refusal.py` is the fourth sibling in the same Stop pass. It reads a turn that
turns the user down - *I can't do that based on your instructions*, *I'd rather
not*, *that needs your explicit approval* after the user already said to do it,
or a smaller thing done in place of what was asked. The user's word in chat is
the authority on this account and outranks every written instruction, so a
refusal that rests on CLAUDE.md, a skill, a standard, a checklist, another
hook's message, a guess or plain caution is held, and the hold tells the turn
to do the work.

Cheap first. A refusal phrase in the closing text is one regex, and it
over-captures on purpose: any first-person can't or won't, work handed back to
the user, a yes asked for again, something done instead, and a No verdict. The
narrower list it replaced caught 7 of 44 plain refusals, so most never reached
the judge. Only a flagged stop pays for the judge, which is
shown the person's own messages - never text relayed from a file, a tool or
another session - and the tool calls the turn made. It releases a reply that
declines nothing the person asked for, a dead end the turn actually tried, an
instruction found inside observed content, and the hard limits no instruction
lifts: typing a password, card, bank or ID number, API key or token into a
field outside a local test of the user's own app; creating an account or
signing in with a password; trading or moving money; solving a CAPTCHA;
changing system security settings; running a file from an untrusted source;
permanently deleting mail or messages; and anything that would seriously harm
someone. The hold names those limits too, so a turn that hits one says so once
and does the rest of the request instead of spending its holds.

```
refusal.py check "I can't do that based on your instructions."
refusal.py selftest
```

A question card is the other way to ask again for a yes already given, and a
tool call never reaches a Stop hook, so `hook.py ask` runs on `PreToolUse` for
`AskUserQuestion` and denies a card the judge reads as the request handed back.
A card that touches mail deletion, money or a credential is let through
unjudged.

It shares ratchet's cap of three holds per user message and its own count
(`<turn>:refusal`), and never announces the last hold. `refusal-corpus.json` is what the first-stage scan must
catch and must not; edit it and run `selftest`, which `ratchet.py selftest`
also runs. `REFUSAL_OFF=1` stands it down for one run.

<br />

## Reactions

`reaction.py` turns the user's reaction to a reply into a record and, when it
recurs, a question. The prompt hook reads each typed message two ways. The cues
in `reaction.json`, the same question asked again and a last turn held twice or
more are caught at once and put in front of the turn. Everything else is read in
context by a model in the background - `reaction.py judge`, through the same
headless call as ratchet's other judges - and `adapt_hold` waits for that
reading at display and stop time: a miss it finds holds the reply once with the
miss and what the user wanted, and one that comes back after the reply was
already drawn goes in front of the next turn instead.

Every miss is kept in `~/.claude/notes/misses/<machine>.jsonl`, one file per
machine so no two machines write the same file, and config-sync carries them
everywhere; a write sends it on in the background. At most hourly
(`sweep_hours`), the prompt hook starts `reaction.py sweep`, which reads the
claude.ai chats the desktop app caches and the voice assistant's spoken log on
the host (`elsewhere.py`, no dependencies: LevelDB, snappy and enough of the V8
format), judges each new exchange the same two ways in batches, and keeps what
missed in the same ledger.

The reply never asks first. When a change would help, it ends with the offer -
`offer: yes` in the respond block, which `respond.py` draws as
"**Should I ask how to improve my reply?**" after the last line, and which
`shape.footer` takes off before every other check reads the reply. The next
message is read for a yes: the `yes` cue at once, anything else in context
beside the miss reading. `hook.py ask` refuses an AskUserQuestion under the
header "Adapt" on a turn without that yes, and `adapt_hold` holds a reply on a
yes turn until the question is asked. A kind listed in `ask` that already has an
open miss anywhere makes the offer required: `adapt_hold` holds the reply until
it ends with it or the reply system is edited. An answered Adapt question or an
edit writes a settlement beside the misses that closes every open miss of that
kind.

```
reaction.py selftest
reaction.py sweep [--read-only]
elsewhere.py claude.ai | voice
```

<br />

## The form follows the scenario

The shape is for reporting work done. `talk_hold` holds a verdict report on a
turn that called no tool - conversation is answered in normal paragraphs in a
respond block, with no limit on length. `doc_hold` holds a reply to a message
that asks for a document (`document_cues` in `shape.json`, less anything
`bounded_cues` finds) when no Markdown file went out through SendUserFile that
turn; the one-sentence pointer is a plain answer, and the file written to be
sent does not count as a change. Each has its own count up to ratchet's cap,
and `scenario_test` in `ratchet.py selftest` runs seven turns through the real
stop hook.

<br />

## A check, not a word

`gate.py` runs in the same Stop pass, right after the layout checks, and in the
display hook through `would_hold`. A finished verdict - any past tense that
reports a change, and a Yes in a turn that edited files - stands only on the
record `~/.claude/tools/proof.py` keeps: one check, run where the problem was
seen, that failed before the change and passed after it in this turn, with the
evidence line it printed carried in Proof word for word. "Not fixed." and "Not
verified." always pass. A false claim is held with no said-once pass-through,
its count in its own `<session>.proof.json` so no other hold resets it, and at
ratchet's cap it is withdrawn rather than released: the first line becomes
"Not verified." on screen and in the saved chat, patched in place at the same
length. `hook.py guard` denies every write to `state/proof/` but the tool's.

```
~/.claude/tools/proof.py before --where site --says "<what it shows>" --problem "<the user's words>" -- '<check>'
~/.claude/tools/proof.py after <id>
gate.py selftest
```

<br />

## All usage, not the chat in view

`scope.py` runs inside `hook.py guard`, before a tool call rather than after a
reply. A question about how the assistant behaves is a question about every
session on every machine, so the first read of a session transcript in a turn
- the projects tree anywhere, or the app's transcript search and export - is
denied until `~/.claude/tools/usage.py` has run. That reads this machine from
disk and the host over ssh, names what it could not read, and a narrower read
is free after it. No judge and no count: the deny lifts the moment the whole
picture has been read.

```
~/.claude/tools/usage.py                    stop holds by reason, day and session
~/.claude/tools/usage.py search <regex>     every typed message and reply that matches
scope.py selftest
```

<br />

## Install

`config-sync.py` carries `ratchet/` with the rest of the setup, so a machine
that syncs gets it. The wiring is two entries in `settings.json`:

```
Stop          ->  ratchet/hook.py stop
SubagentStop  ->  ratchet/hook.py subagent
```

Both are written in the guarded form, so a machine without the directory stands
down instead of failing every turn. `ratchet.py doctor` says whether this one is
actually wired.
