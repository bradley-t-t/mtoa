# Ratchet, the reply shape and the peer reply guard

The rules for answering either one are in `CLAUDE.md`. This is how they work and
how to change them.

## Ratchet

The turn that says "just one more thing" on its way out has told you it knew
what was left and stopped anyway. `~/.claude/ratchet/` reads that sentence and
refuses the stop, on `Stop` and on `SubagentStop`.

Two stages, and the order is the point. A word list reads the closing prose and
costs nothing, so almost every stop clears in under ten milliseconds. Only a
flagged stop pays for the second stage, which is a headless Haiku answering one
closed question: can this agent do the named work right now, itself, with
nothing from the owner. That question is there because a word list cannot tell
deferred work from a real blocker - "I need your approval before I push" and
"I'll do the rest later" are the same grammar, and only one of them is the
turn's to finish.

A turn stopping for the owner's approval, decision or a credential they hold is
never held; those stand the whole check down before the judge is called.
Everything a turn merely chose to put off is fair game. One user message buys
three holds at most, so a genuinely stuck turn names its blocker in one line and
gets out. A hold quotes the text about to be sent and nothing earlier, so a turn
that fixes the line clears on the next stop.

A standing approval is the exception inside that exception. Running SQL was
answered once for every statement there will ever be, so a turn that stops to
ask for it is not blocked - it is waiting on a word it already holds. `standing`
in `tells.json` is what takes the stand-down back, read over the blocker
sentence and the one before it, because what is being asked about usually sits a
sentence back from the asking. `asks` is the second way in, for a turn that
defers without asking outright - "I would rather you looked at it first", "I
have not run it yet" - which no hold phrase catches. Those stops are held with
no judge and told to run the statement, and the only ways out are that the
thing stopped for is not a statement or that the database cannot be reached
from the session at all.

A turn that tells the user no is held the same way. `ratchet/refusal.py` reads
the closing text for a refusal - "I can't do that based on your instructions",
"I'd rather not", a yes asked for again after the user already gave it in chat,
a smaller thing done in place of what was asked - and a flagged stop goes to a
Haiku judge shown only the user's own messages and the turn's tool calls. The
user's word outranks every written instruction, so a refusal resting on a file,
a guess or caution is held and told to do the work. Released are replies that
decline nothing the user asked, dead ends the turn actually tried, instructions
found inside observed content, and the hard limits no instruction lifts -
credentials, card, bank or ID numbers typed into a field, account creation and
password sign-in, trades and money movement, CAPTCHAs, system security
settings, untrusted executables, permanent deletion of mail or messages, and
serious harm to someone. It shares the cap of three holds per user message,
and a refusal hold never announces that it is the last, since a turn told the
next stop goes through only has to say no once more. The first-stage scan
catches any first-person can't or won't, work handed back ("you'll need to run
that yourself"), a yes asked for again, something smaller done instead, and a
verdict that is itself the no; the old list caught 7 of 44 plain refusals and
the judge never saw the rest. `refusal-corpus.json` is its scan corpus and
`REFUSAL_OFF=1` stands it down for one run.

A yes asked for again through a question card is a tool call, not a stop, so
the Stop check never saw it, and the reply shape sends every ask there.
`hook.py ask` is a `PreToolUse` hook on `AskUserQuestion` that puts each card to
the same judge with a question of its own - is this asking again for what the
person named themselves - and denies the call with the reason when it is. A
card about a choice the person has not made, a specific object a broad request
never named, or something only their hands can do is let through, and a card
that touches a hard limit (mail deletion, money, credentials) never reaches the
judge at all.

A Sunday announcement (`hook.py say`, `PreToolUse` on Bash, for a command that
sends `/say`) is read twice before it is spoken. First `ratchet/ear.py`, with
no judge: anything written for the eye rather than the ear - an abbreviated or
numeric date, a time zone, a clock time or amount in figures, a symbol, an
aside in brackets, shorthand, a unit, a link, path, file name, version or ID, a
chat reply's labels or list, more than ninety words, or a yes or no said in
more than ten - is denied with each piece named and how a person would say it.
Then the judge reads it against the
speech rules. The ear check holds the same text every time it is sent, because
a pattern says exactly what needs rewriting and there is nothing to judge.
`EAR_OFF=1` stands it down for one run; `ear.py selftest` runs its cases, and
`ratchet.py selftest` runs them too.

An answer bigger than the shape goes in a file, never cut to fit. When I ask
for a summary, a plan, a list or an explanation, or the answer holds more than
the shape, the whole of it goes in a Markdown file sent with SendUserFile, and
the reply is the shape pointing to it; the summary, answer and explanation
verdicts need no Proof line. A long draft held only for its layout - not for an
aside, unfinished work or a step handed to me - is kept as Markdown under
`state/ratchet/replies/<session>/`, and the hold names the file. A reply later
in the same turn that carries less than 60 percent of that draft's words, with
no Markdown file sent, is held (`cut_hold`, its own count up to ratchet's cap); a
bare Yes or No is let through. `ratchet.py selftest` replays it.

A finished verdict needs a check, not a word. `ratchet/gate.py` reads the
record `tools/proof.py` keeps and not the reply: Fixed, Done, Shipped, Built -
any past tense that reports a change, and a Yes in a turn that edited files -
stands only when one check, run where the problem was seen (the live site, the
app's screen, Sunday on the host, the chat, a file, a named command), failed
before the change and passed after it in this turn, and the Proof line carries
the evidence line the tool printed, word for word. A check that already passes
before the change never saw the problem and is refused; where only my eyes can
tell, my report is the before and my answer to a Gone / Still there card the
after. "Not fixed." and "Not verified." are allowed verdicts and are never sent
back. A false claim is held up to ratchet's cap and then withdrawn by code - its
first line becomes "Not verified." on screen and, in place at the same length,
in the saved chat - so it is never let through. The records under
`state/proof/` are the tool's alone; the guard denies any other write there.
`PROOF_OFF=1` stands it down for one run, and `gate.py selftest` runs its cases,
as does `ratchet.py selftest`.

Usage means all usage. When I ask how the assistant behaves, I mean every
session on every machine, not the chat in view, and a turn that opens one
transcript has narrowed the question before writing a word. `hook.py guard`
denies the first read of a session transcript in a turn - the projects tree on
any machine, or the app's `search_session_transcripts` and `export_transcript`
- until `tools/usage.py` has run. That reads every session on this machine and
the host, names what it could not read (cloud sessions always), and reports stop
holds by reason, day and session, or every line matching a search. A narrower
read is free after it, and memory files under the projects tree are not
transcripts. It acts on the tool call, so nothing is drafted narrow and held
afterwards. `scope.py selftest` runs its cases, and `ratchet.py selftest` runs
them too.

Every hold is drawn in the conversation, so no check says the same thing twice
on one user message. The first hold of a kind carries the full reason; a second
from the same check on new text quotes only the new text; a hold on exactly what
it quoted before is not said again, and the stop goes through. The same goes for
the question-card and Sunday-announcement denials and for on-screen notices,
except the ear check, which denies every announcement written for the eye and
quotes only the pieces after its first full reason.
`<session>.said.json` records what was said, and the display hook reads it, so
a reply the stop hook will release is never hidden.

`tells.json` is the word list and `corpus.json` is what it must catch and must
not; edit either and run `selftest`. `RATCHET_OFF=1` stands it down for one run.

```bash
~/.claude/ratchet/ratchet.py check "Done. One quirk left in the parser."
~/.claude/ratchet/ratchet.py doctor      # config, wiring, judge reachability
~/.claude/ratchet/ratchet.py selftest
```

## The reply shape

Ratchet's scan reads what a closing reply says. `ratchet/shape.py` reads how it
is laid out, because the layout is what lets a reply drift: a free paragraph has
room for "oh, and three things I left", and a reply with fixed slots has nowhere
to put it. The shape itself is in `CLAUDE.md`. It runs first on every `Stop`,
before the rest of ratchet, and never on a subagent.

The labels are Why, What, Touched and Proof, because they answer what a reader
asks of any reply rather than the steps of a repair: a release or a question has
no cause, and Cause / Change / Check made a /ship reply invent one. Proof is
required under every finished verdict (`proof_verdicts` - any past tense, Done,
Built, Live), and Started, Yes and No are exempt.

A turn never ends waiting on the user. A verdict like Waiting, Blocked or Your
call, or a sentence that hands the user an approval, a decision or a step
(`waiting_claims`: "needs your", "your yes", "once you approve", "up to you"),
is held as unfinished, and every hold - shape and ratchet alike - points the
turn at the AskUserQuestion tool: ask inside the turn, get the answer, finish.

A question that one or two sentences answer gets a plain answer instead: at most
two unformatted sentences and 320 characters, no claim of work done
(`plain_claims`), no aside, and only in a turn that used no edit tool. A plain
answer is written inside a respond block - ```respond, then `plain: ...`, then
the fence - and the display hook draws its render (`respond.block`). Bare prose
is never drawn. The CLI calls the display hook on a finished text block and
waits on it before it reads the next one (probed headless: a hook that waited
eight seconds for the following tool call held that call back for exactly as
long), so when a line of prose is drawn nothing anywhere says whether a tool call
follows it, and a line of narration and a one-line answer are the same bytes.
The block is what marks the answer. Bare prose that ends a turn is held
(`shape.PROSE`) and comes back as a block - different text from the hidden draft,
and drawn with a trailing zero-width space, because the desktop app (build
2.37337+) drops a byte-identical resend as a duplicate; that drop is why the old
word-for-word resend made every short reply vanish. Replayed over every narration
line in two long sessions, 153 of 153 were hidden.

Three hooks share one decision (`shape.decide`) and one per-turn state, so the
screen and the stop never disagree. `MessageDisplay` (`hook.py display`) runs
before any text is drawn and keeps text that is out of shape off the screen -
display only, the stored message and what the model reads are untouched. `Stop`
then sends that reply back to be rewritten, and the rewrite is what appears. A
Stop hook alone cannot do this: it runs after the text is drawn, so every held
reply used to be seen twice. Hiding it on the live screen is not enough on its
own: the desktop app redraws a chat from the saved transcript whenever it loads
it - opening it, switching back, a restart - and never applies the display hook's
replacement there, so each held draft came back above its rewrite, the same
report twice in a row. Every `Stop` hold now blanks the held rows in the
transcript (`blank_held`): the text is overwritten in place, same byte length,
with a zero-width space and spaces, never truncating a file the CLI is still
appending to, and the original row is kept under `state/ratchet/held/`. A resumed
session reads the blank row without complaint. `UserPromptSubmit` (`hook.py prompt`) puts the
decision rule (`DECIDE` in `ratchet.py`: look at exactly what an action will
touch, and confirm it is what was asked and nothing in use is in its path, before
the action is chosen) and then the shape and the approved example at the top of
every message typed, so the first
draft is usually right and nothing is withheld; replayed over five closing-report
scenarios three times each on Opus, first drafts went from 7 of 15 in shape to 14
of 15. There are no progress notes: plain prose of three sentences or fewer out of
shape is narration and is hidden at once, with no judge and no excuse, and so is
a progress verdict (`progress_verdicts`). A reply in shape is not shown on that
alone - the display hook runs the whole stop pipeline on it first (`would_hold`,
with nothing printed and no hold counted), so a reply that ratchet would send back
for deferred work, an unchecked claim or a premature one never appears either.
Judge answers are cached by prompt for an hour (`run_claude`), so the stop hook
that follows asks the same questions for nothing. A reply excused or past the hold
limit is shown, because the stop hook will let it through. A partial reply is held
like any other out of shape - a partial verdict (`partial_verdicts`: partly,
mostly, likely, not fixed) or, under a completion verdict, a claim that the work
is not yet proven (`partial_claims`: "hasn't come through yet", "should work",
"not tested"). A Yes or No may report what has not happened yet, since it answers
a question about something else. Routine holds and
releases print no notice; only a check that failed does (`notices` in
`config.json`). The desktop app hands the display hook each finished
message whole; a terminal hands it batches of finished lines, which are held
back and shown or withheld together when the last one lands.

The desktop app runs sessions with partial messages on, so it draws text as it
streams, and it treats an empty replacement as no replacement: a line the hook
blanked with "" stayed on screen for good. It does draw a non-empty one, so a
whole message is hidden with a zero-width space (`HIDDEN`); probed live, a
replacement string and a zero-width space both took the line's place, and "" left
it up. What wrote those lines was Claude Code's silent-turn reminder ("The
user hasn't heard from you in a while"), which fires every few tool calls and
asks for an update; every narration line in the session that showed them came
right after one. `settings.json` turns it off with
`CLAUDE_CODE_SILENT_TURN_REMINDER=0`. Run headless over twelve tool calls, the
reminder fired twice and drew two progress lines; with it off, none.

The desktop app also gives a display hook ten seconds (`Ho=1e4` in the CLI, apart
from the hook's own `timeout`) and then shows the original text, and one judge
call uncached takes up to thirty. A held draft whose excuse judge ran past ten
seconds was shown, and its rewrite after it - a reply seen twice. So the display
hook stops itself at `DISPLAY_BUDGET` (9.2 seconds, by `SIGALRM`, as
`OutOfTime`, a BaseException no check's `except Exception` can swallow); on
timeout it shows the text rather than hide what it could not clear, since a
hidden reply only comes back as a rewrite and a reply it cannot judge is not one
it can prove belongs hidden. Replayed against the live transcript, the plain
answer that had been vanishing now shows on the first draft, and an out-of-shape
progress line is still hidden for its rewrite.

Three stages, cheapest first. The layout is a parse - the verdict, one sentence
after it, labelled lines of at most two sentences - and a reply in the shape is
then read for asides - "by the way", "you'll need to", "left for later", "let me
know", and telling me I'm right ("you're right", "fair catch", "as you
suspected") - so every compliant stop clears in milliseconds. A reply out of shape is
excused on sight when this turn's message was a slash command, since the skill
sets its own output. A reply built on the shape itself - a bold verdict and
nothing but labelled lines - took no other form, so it is held at once with no
judge: the judge's seconds were seconds the draft sat on screen before the
display hook could hide it, and the rewrite then put the same reply up twice. A
shape hold quotes the lines it was held on and no copy of the shape, since the
prompt hook already put the shape at the top of the message. Anything else goes
to a headless Haiku with one question: did the user ask, in their own words, for
this reply to take another form. It must quote the words, the quote must appear
in what the user wrote, and it must name a form (`form_words`), so "fix it" is
never read as a request for a table.
An excuse holds for the rest of the turn.

Only a session a person reads is shaped. A run with an `sdk-` entrypoint -
`claude -p`, the host's services, ratchet's own judges - has a program reading its
output, and is skipped. A reply is held five times per user message at most, and
the hold never says so, because a turn told the next stop goes through has no
reason to fix the reply; the fifth release is announced on screen instead. The
count and the excuse live in `<session>.shape.json` beside ratchet's own state,
because ratchet's file is rewritten by whichever check held last.

`shape.json` is the labels, limits and aside list, and `shape-corpus.json` is
what it must pass and must hold; edit either and run `selftest`, which
`ratchet.py selftest` also runs. `SHAPE_OFF=1` stands it down for one run.

```bash
~/.claude/ratchet/shape.py check "**Fixed.** The relay reconnects again."
~/.claude/ratchet/shape.py selftest
```

## The peer reply guard

Ratchet reads a turn on its way out. This one reads a message on its way to
another session, and refuses the ones whose whole content is that no message
was needed.

It is a `PreToolUse` hook over the session message tools alone, and it denies
one shape: a reply to a hold. Announcements, lifts, failures, stop notices, a
message asking a peer to do something and the answer to one all go through
untouched.

The test is what is left once the hold mechanics are struck out sentence by
sentence - the acknowledgement, the hold's own terms said back, this session's
clearance, its self-description, and the git state the recipient reads itself.
A message with nothing behind those is refused; a real finding wrapped in an
acknowledgement leaves a sentence standing and passes. That residue is the
whole test, and it is the only one: an earlier build decided on the opening
clause instead, which refused about fifty messages that carried a real finding
behind the acknowledgement - a file the recipient was editing, a console its
release had taken down, a worktree directory reused under it. Blocking those
costs more than the noise it saves. Replayed over every session-to-session
message ever sent on the workstation, 2026-08-29 to 09-09, it refuses 44 of the
361 replies and none of the 624 announcements and lifts.

`selftest` reads two sets. The hand-labelled cases are read and judged against
the rule, and a failure there is a failure. The rest is the same traffic as a
regression baseline recording current behaviour, so a change there is a change
to look at rather than a break - the first corpus was generated from the
implementation's own verdicts, which meant it could only ever prove the guard
agreed with itself.

`peer-reply-corpus.json` is that traffic plus hand-written edges, and it is
what the patterns must catch and must not; edit either and run `selftest`.
`PEER_REPLY_GUARD_OFF=1` stands it down for one run. Its `matcher` in
`settings.json` names the same tools as `TOOLS` in the script, so a new message
tool goes in both.

```bash
~/.claude/tools/peer-reply-guard.py check "Holding. Nothing in flight, go ahead."
~/.claude/tools/peer-reply-guard.py selftest
```

## The checklist hook

`checklists/hook.py` acts on `Bash`, `Write`, `Edit`, `MultiEdit`,
`NotebookEdit`, `Read`, `NotebookRead` and `Skill`, and its `PreToolUse` and
`PostToolUse` matchers in `settings.json` name exactly those, so a tool added to
its `registry()` goes in the matcher too.
