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
serious harm to someone. It shares the cap of three holds per user message;
`refusal-corpus.json` is its scan corpus and `REFUSAL_OFF=1` stands it down for
one run.

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
answer is shown as written, not hidden. It once was hidden like narration and
brought back by a word-for-word resend once the stop hook confirmed it ended the
turn, but the desktop app (build 2.37337+) drops an identical resend as a
duplicate and never draws it, so every short reply vanished - hidden, then its
resend swallowed. The display hook now only hides what the turn will replace
with DIFFERENT text that the app does draw: a genuine hold, or an in-shape reply
the stop pipeline sends back. The cost is that a bare mid-turn narration line,
which reads like a plain answer, can show; the silent-turn reminder that drove
most of those is off, so it is rare.

Three hooks share one decision (`shape.decide`) and one per-turn state, so the
screen and the stop never disagree. `MessageDisplay` (`hook.py display`) runs
before any text is drawn and keeps text that is out of shape off the screen -
display only, the stored message and what the model reads are untouched. `Stop`
then sends that reply back to be rewritten, and the rewrite is what appears. A
Stop hook alone cannot do this: it runs after the text is drawn, so every held
reply used to be seen twice. `UserPromptSubmit` (`hook.py prompt`) puts the
shape and the approved example at the top of every message typed, so the first
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
hook stops itself at `DISPLAY_BUDGET` (seven seconds, by `SIGALRM`, as
`OutOfTime`, a BaseException no check's `except Exception` can swallow); on
timeout it shows the text rather than hide what it could not clear, since a
hidden reply only comes back as a rewrite and a reply it cannot judge is not one
it can prove belongs hidden. Replayed against the live transcript, the plain
answer that had been vanishing now shows on the first draft, and an out-of-shape
progress line is still hidden for its rewrite.

Three stages, cheapest first. The layout is a parse - the verdict, one sentence
after it, labelled lines of at most two sentences - and a reply in the shape is
then read for asides - "by the way", "you'll need to", "left for later", "let me
know" - so every compliant stop clears in milliseconds. A reply out of shape is
excused on sight when this turn's message was a slash command, since the skill
sets its own output. Anything else goes to a headless Haiku with one question:
did the user ask, in their own words, for this reply to take another form. It
must quote the words, the quote must appear in what the user wrote, and it must
name a form (`form_words`), so "fix it" is never read as a request for a table.
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
