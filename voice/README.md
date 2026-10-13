# sunday-voice

A voice assistant on a ThirdReality speaker cube, with its brain on the Pi `sunday`.
There's no Home Assistant and no cloud speech service.

## How it works

- **Cube** (`cube/sunday_cube.py`, run by `/etc/init.d/S99sunday`): listens for the
  wake word "sunday" (microWakeWord). It reads both mics and the speaker reference
  from the ALSA loopback `hw:0,4`, and removes its own voice with SpeexDSP echo
  cancellation. While listening it streams the cleaned 16 kHz audio to the Pi, and
  it plays the 48 kHz reply through `hw:0,1`. Saying "sunday" while it talks doesn't
  cut it off (see Talking over Sunday), and "stop" silences it at once. The top button works as push-to-talk or stop, and the
  volume and mute buttons work too.
- **The link:** the cube starts a sentence once 0.3 s of it is queued or the whole clause is in, and if the network falls behind mid-sentence it pauses and waits for more (longer each time in one reply) instead of playing the scraps. The brain pings every 5 s and gives a link up after 30 s without an answer; the cube reconnects after 15 s of hearing nothing. Speech sent while the link is down waits on the brain, and a cube back within 4 minutes is sent it again in the audio already made, from the start of the first sentence it did not get whole. `wifi_watch` in `S99sunday` checks the gateway and the brain every 5 s and bounces the link before rebooting.
- **Pi** (`server/voice_server.py`, `voice-assistant.service`, port 8794):
  - Silero VAD detects when you stop talking.
  - Parakeet TDT 0.6B (int8, sherpa-onnx) transcribes on the Pi.
  - Claude answers through a long-lived Claude Code session on the Pi's own login,
    which keeps short-term memory.
  - Kokoro (af_heart) speaks each sentence as soon as Claude writes it, a clause at a time; Piper is the fallback voice, chosen by `VOICE_TTS` in the systemd drop-in `/etc/systemd/system/voice-assistant.service.d/voice.conf`.
  - ElevenLabs mode swaps in a cloud voice (Emma, `eleven_flash_v2_5`), streamed to the cube as it arrives. Any clause it can't start within 1.5 seconds, or can't say at all, is spoken by Kokoro instead, and three failures in a row put ElevenLabs aside for five minutes. The voice id, model and level are set in `deploy/voice.conf`, which the deploy installs as the drop-in. The key comes from the vault at start through `deploy/voice-start.sh`.
  - Older conversations are condensed into long-term notes in `data/memory.sqlite3`.

## The speech gate

`server/speech_gate.py` checks every answer and announcement before a word of it is spoken. It holds four rules: Sunday never gives up without trying another route (A), never asks Alex to type, read a screen or use a chat (B), never talks about chats, sessions, agents or the desk as if something other than Sunday did the work (C), and never calls work in progress done (D). The prompt describes the same rules, but the gate is what enforces them.

- A regex prefilter clears ordinary speech at no cost. Anything it flags goes to a Haiku judge. The judge is given his question, every tool the turn ran with what came back, and the queue of work in progress. Judge processes are started ahead of time, so a verdict takes about a second.
- A broken answer isn't spoken. It goes back to the same Sunday session with the rule it broke, and only the new answer is spoken once it passes. Sunday says nothing while it retries, and the popup keeps showing the work. After two retries Sunday says one sentence on what it tried and what it is doing next.
- Rule B allows one thing about his screen: saying the detail is on it, and only when a canvas went there with that speech (see The canvas). The code half strips that sentence before it looks for screen words, and the judge is told what was sent. Without a canvas, "it's on your screen" is broken, and asking him to click, type or scroll is broken either way.
- `/say?checked=1` skips the Pi's judge: it is for announcements the Ratchet hook on the Mac already judged against all four rules with the transcript of the work. Announcements without it, such as relay.py's, are judged here as before.
- `/say?answer=1` marks the text as the answer to a question he has just asked. Him asking again is never a reason to say less, so only what was said aloud since that question counts against it (see Never twice): since his latest request, or since `asked=<epoch seconds>` when the caller knows when he asked. It is a late answer, so it waits until he can hear it (see Late answers).
- A `/say` waits for the verdict and answers with what was spoken: `{"ok": true, "spoken": ..., "trimmed": ...}`, or `{"ok": false, "reason": ..., "repeat": true}` when the gate refused it or he had already heard all of it, so the caller rewords and resends instead of believing it was heard. A `/late` answer is never dropped as old news, since it answers his question: a gate refusal is reworded once, and only a second refusal leaves it unsaid, logged as an error. It is dropped only when the same answer already reached him since he asked; the same answer still waiting or playing as a `/say?answer=1` is joined to it and said once. A `/say` announcement is judged here on A, B and C only: the Ratchet hook on the Mac has already checked it against all four with the full transcript of the work, and this judge sees one paragraph with no record of what finished. Replies and `/late` answers keep all four.
- When the judge errors or times out, the answer is spoken unchecked and the failure is logged as an error.
- Every verdict is a `"kind": "gate"` line in `data/speech.jsonl`. `speech_gate.py replay` runs the real lines that broke the rules, plus good ones that must pass.

## Never twice

`server/repeat_guard.py` keeps Sunday from saying aloud again anything he has already heard since he last asked for it. It is code, not a prompt rule, and it sits in `CubeSession.speak`, the one place every line goes to a speaker, so answers, follow-ups, the filler said while an answer is worked out, `/say` announcements, relayed replies and `/late` answers all pass through it.

- What a line is checked against depends on what it answers. An answer counts what was said since the request it answers, so a question asked twice is answered twice. "Sunday", "hey" or "oh" alone ask for nothing, so an answer to one of them counts from his request before it. `/say?answer=1` counts from his latest request, `/late` from when he asked, and a plain `/say` or relayed reply, which nobody asked for, from half an hour back.
- A line is refused when it says the same words again once case, punctuation, contractions and numbers are put in one form (barge.py's reading, so "11:13" is "eleven thirteen"), when it is the same give or take one word in four with exactly the same numbers ("plant 401" is never "plant 410"), or when it is word for word part of a longer line said earlier. Those three are decided in code in microseconds and cannot fail.
- A restatement in other words is for a Haiku judge, which refuses a line only when every fact in it was already said. It is asked only when what was said since holds at least 60% of the line's telling words (its meaningful words and numbers, less ones too general to tell news apart), and its refusal stands only when the lines it names hold 60% too; otherwise the line is said and the overruling is logged. So a short line that shares a topic word ("Repeat check.") can never stand in for a longer one carrying facts it does not. Two judge processes are kept started ahead of time, so a call takes one to three seconds, and a normal answer never makes one, because nothing else has been said since his question. When the judge errors or times out the line is said and the failure is logged as an error.
- Greetings and acknowledgements ("I'm here", "What do you need?", "Yes?", "Go ahead", "Anything else?") and the filler line are said only when nothing else has been said since his latest request and in the last two minutes.
- A refused line is silent: no "skip", no "nothing new". A reply refused whole leaves no follow-up window. "Stop" and "mute" are commands, never speech, so they always act.
- Each line is checked twice: free before its voice is made, and finally with the speaker held, just before its first sound, against everything said by then on every speaker. A line that never finished playing does not count as said. After a restart the guard reads back the last six hours of `data/speech.jsonl`.
- Every refusal is a `"kind": "repeat"` line in `data/speech.jsonl`, with the earlier line it matched, and a "not said, a repeat" line in `voice.log`. `repeat_guard.py selftest` runs the cases, today's real repeats among them; `selftest --judge` also runs the ones only the judge can decide. `repeat_guard.py check "<line>" --said "<earlier line>"` tries one.

## Late answers

An answer that comes back after its question was answered is a late answer: a lookup's reply
file, which relay.py hands to `/late`, or a chat reading its answer out through
`/say?answer=1`. Sending one to a speaker is not the same as him hearing it, so late answers
wait in a queue in `server/voice_server.py` (`late_worker`) and are said one at a time, in the
order they came.

- **When.** A late answer waits while a mic is open for him on any device, while a reply's
  follow-up window is about to open, while anything is talking, and while his newer question
  is being answered (for up to three minutes, `VOICE_LATE_TURN_WAIT`). Then it needs 1.5 s of
  quiet (`VOICE_LATE_QUIET`) before it starts. One that comes in while he is asking something
  else is said after the answer to that.
- **Where.** On the device he last spoke to, if that was in the last 15 minutes
  (`VOICE_LATE_HERE`) and the device is connected, and a cube only while the Mac says he is
  home. Otherwise it goes where an announcement goes: the cube, and the Mac only while he is
  away.
- **Said means heard.** It counts as said only once the last of it has played at full volume.
  If the device turns it down for him (a wake or the open ear hearing him over it, or him
  talking to another device), cuts it off, or loses its link while it plays, none of it
  counts: the repeat guard forgets its lines, the speech log records that try with
  `"played": 0` and the reason, and it is said again in full once he is done, on the device he
  is using then. After three tries (`VOICE_LATE_TRIES`) it is left unsaid and logged as an
  error. A stop or mute while it plays ends it for good; one still waiting is not touched by a
  stop said to something else.
- **Once.** The same answer arriving both ways, the chat reading it out through `/say` while
  relay.py hands the reply file to `/late`, is one late answer, matched by the repeat guard's
  exact, near and contained rules, and said once. `/say` answers ok for the second copy.
- **Never twice still holds.** Every line is still checked as it goes out, so a late answer
  whose news the answer to his newer question already gave is not said, in whole or in part.
- Every step is a `late answer <id>` line in `voice.log`: held and why, came again, saying it
  on a device and which try, not heard and why, heard in full. `/health` lists the ones
  waiting or playing under `"late"`. Its card on the popup comes up when it starts playing.

## Talking over Sunday

A wake or follow-up while Sunday is talking never stops it by itself. The device keeps
playing at about 35% while he talks (`duck_volume` in the cube's config, `SUNDAY_EAR_DUCK`
on the Mac), and once his words are transcribed `server/barge.py` decides:

- **Stop** for an explicit stop: "stop", "quiet", "shut up", "hold on", "wait", "pause",
  "never mind", "cancel", "enough", or mute. The short ones act on the running transcript,
  before he has finished, and the cube's own "stop" word and its top button stop it at once.
- **Redirect** for a correction of what is being said ("no, I meant...", "not that one",
  "wrong site"): the current answer is cut off and his correction is answered with it in mind.
- **Continue** for anything else: the volume comes back, the current answer finishes, and the
  new one is spoken after it.

A regex settles the clear cases; the rest go to a Haiku judge kept warm and isolated like the
speech gate's, and a judge that fails means "don't stop". Every decision is a `"kind": "barge"`
line in `data/speech.jsonl` with its reason. `barge.py selftest [--judge]` runs the cases.
Words of Sunday's own that leak through the echo canceller are taken out of the transcript
first, compared in one spoken form ("28" is "twenty eight", "7 o'clock" is "seven o clock").
Any transcript that is mostly what a device was playing while he spoke, or whose leftover is
a few words it said, is dropped whole: not answered, not acknowledged, and logged as
`"discarded": "its own voice"`. Its name left over is his, unless it was saying its name
itself: a "Sunday" said just after a line the cube woke on itself is in that same listen.

The recognizer can also write its own sentence back as other words that sound like it ("Abel
Jimenez now has Plant Manager access" came back as "Both commanders now has client manager
access"), so what was heard is lined up word by word against what it was saying, words that
sound alike counting as the same. When 60% or more lines up, it never stops or cuts anything,
and a statement of six words or more is dropped as its own voice; a question or a request is
still answered after. A redirect always needs a correction in his words ("no", "I meant",
"not that", "wrong", "actually"...): the judge alone never cuts on a bare statement. A device
woken on its own speech while it talks acts only on a plain stop or a plain correction,
unless the speaker check has found the voice plainly his.

`tools/barge_test.py [continue|stop|redirect|all] [--ear]` talks over a brain as a simulated
cube or Mac ear and prints what played when. On the real cube, `echo "wake <file>" >
/tmp/sunday-inject` acts as a heard "sunday" and sends that 16 kHz mono s16le file as the speech.

## Words, not sounds

The wake word also fires on coughs, a throat cleared, a sneeze, a click or a dish set down
(his coughs reached 0.79 to 0.92 on 2026-10-06), the voice detector calls a cough speech, and
a television says "Sunday" and words like it. So nothing chimes, lights a ring, turns down or
shows on a screen, on the cube or the Mac, until words of his are heard after the wake: its
name alone is not enough, and a wake with none is never answered.

- **The wake word itself.** Every wake word wake (cube or Mac) holds its chime, ring, duck and
  overlay until the brain says "speech". The brain transcribes the second before the wake
  fired, which holds all of "Sunday", and notes whether the recognizer was sure of the name
  there: about 0.2 s on the Pi (`wake word heard ... in N ms` in `voice.log`). A cough comes
  back empty, as a filler, or as "Yeah." or "Okay." the recognizer was unsure of. Its name
  there says "speech" to nothing; it means only that the words he says next are said to
  Sunday.
- **The cube waits for words too.** The cube's own second before a wake comes back empty or as
  "Yeah." far more often than not even when he said the name right beside it (2026-10-07
  morning: every one of his cube wakes), so on the cube real words after the wake are enough
  to clear it, with or without its name. It chimes and lights its ring once they are in, never
  for the wake word alone, so a cough or a television at the cube costs nothing.
- **Then his words.** On the Mac, with no name in that second, the running transcripts clear it
  once its name turns up in what he goes on to say (a cough, then "Sunday, ..."), or a plain
  stop or mute does at once. After its name, or in a follow-up window, real words are enough.
- **Whose words.** Words that would clear a listen must also be his: a voice scoring 0.35 or
  more on the speaker check (every television and room stretch scored 0.28 or less, 2026-10-05
  to 10-08), or else the directed-or-overheard judge reading them as said to Sunday. The judge
  takes 0.5 to 2 s (0.67 s typically), one word is too little for it, and the newest words are
  what it reads next, so "Sunday, what time is it?" chimes about 2.4 s after the wake. A short
  question is often over first, and then it chimes when he stops, once the turn below has
  decided it is his. Until then the overlay shows nothing, not even "listening"
  (`words on mac (words after its name; said to Sunday)`, or `words on mac read as overheard
  (voice 0.04): nothing shows yet`; `VOICE_SPEAKER_HIS`).
- **When he stops.** A wake nothing cleared while he spoke, its name in it or not ("Sunday" and
  then a television talking straight on), is dropped only when both say it was not him: the
  judge reads the whole of it as overheard and the voice scores under 0.25
  (`discarded (not said to Sunday: the words read as overheard and the voice scored 0.03)`;
  `VOICE_SPEAKER_NOT_HIS`). The judge alone called one in four of his requests overheard, and
  each of those scored 0.38 or more, so neither drops anything by itself. Anything else is
  answered, and its chime and overlay come then. The same words put to the judge twice within
  half a minute, while he spoke and once he stopped, are judged once.
- **A television at the Mac.** A Mac wake with no name in its wake word that nothing has
  cleared after four seconds is read again as one stretch, the wake word and everything since,
  which holds "Sunday, what time is it" far better than the wake word's second alone. Its name
  there lets his words after it clear it; otherwise the mic closes then (`closing the mic on
  mac: no name in its first 4 s`), instead of staying open on a game show for a whole utterance
  while his own "Sunday" goes unheard (`VOICE_UNNAMED_CLOSE`, default 4.0).
- **Its own reply at the Mac.** A Mac wake while the Mac itself is talking, with no name in its
  wake word, closes as soon as that is read (about 0.4 s): every one in the log through
  2026-10-13 was its own voice, and held open for four seconds it kept the reply turned down
  and was still open when the reply ended and he went on without its name (`no wake word in
  the wake on mac over its own reply`). Words of his over the reply are the open ear's to hear.
- **After a reply.** Every reply opens a follow-up window, 5.5 s to start talking without its
  name (`VOICE_FOLLOWUP_WINDOW`), and what it hears there is a decision: put to the judge with
  what Sunday just said, an answer to it, or more for it, is answered as part of the
  conversation, and him talking to someone else in the room or on a call, or the room itself,
  is dropped without a sound (`discarded (not said to Sunday: the follow-up read as
  overheard)`). Words the judge already read as said to Sunday while he spoke are not judged
  again. Its answer opens the next window, so the conversation goes on until he stops.
- **His name alone at the Mac.** His "Sunday" at the Mac reads back as "Hmm." or nothing more
  often than not (2026-10-08 evening: every attempt but one). So a Mac wake at least -22 dBFS
  whose next second, 0.45 to 1.05 s after the mic opened, is 12 dB quieter is its name said
  alone: nothing chimes yet, and what he says after the pause counts, as on the cube, and
  chimes once it is in. A television or the room goes on talking and gets neither (`its name
  said alone on mac`; `VOICE_LONE_LOUD`, `VOICE_LONE_DROP`).
- **His name, then a wait, at the Mac.** Nothing chimes for its name alone, so he waits for the
  chime or says "Sunday" again. When he pauses in a Mac listen nothing has cleared, what it holds
  is read before the mic closes: with its name and nothing after it (the wake word and
  everything since read as one stretch, so a "Sunday" the wake word's second misread counts),
  the mic stays open 8 s more for what he wants, and his words then clear it as above
  (`its name and nothing after it yet on mac ... the mic stays open`). Nothing said in those
  8 s ends it as before (`nothing after its name on mac in 8 s`); words after its name end it
  at once; no name anywhere ends it as a sound (`VOICE_NAME_WAIT`, default 8.0).
- **His name, then a question, while the computer plays.** A listen whose wake word was heard
  as "Sunday" is said to Sunday: the computer-audio gate does not judge what follows it.
- **Cube or Mac.** The Mac goes ahead when the cube has not heard a wake in 400 ms. If the cube
  then hears its name while the Mac has heard no words yet, the Mac stands down and the cube
  answers: he is beside the cube, and the Mac woke on a sound or a far-off word.
- **The turn.** What was heard is dropped, and logged once as `dropped, no words (<why>)` with
  the transcript, when no real word is left: `server/nonspeech.py` lowercases it, takes out
  bracketed and parenthesized annotations, punctuation and filler sounds ("uh", "hmm"), and
  needs a word that is not only a sound's name, a lone "the", or a phrase recognizers make up
  from noise ("thank you", "bye"). A short word the recognizer was not sure of (its least sure
  piece under -1.0 log probability) does not count. "stop", "yes", "no", "mute", "Sunday" and
  numbers count; a plain stop or mute always acts. A wake word wake that nothing cleared also
  needs a word besides its name (`its name and nothing after it`), and on the Mac its name in
  the wake word and what followed, or it was woken by a sound.
- **The open ear** drops what it hears over Sunday's speech the same way, before anything else
  reads it.
- The button needs no words: it is someone at the cube.

```bash
ssh voice-host 'cd ~/voice-assistant && venv/bin/python nonspeech.py selftest'   # text cases, his real coughs, made sounds
python3 server/nonspeech.py selftest                                          # text cases only, on the Mac
ssh voice-host 'cd ~/voice-assistant && venv/bin/python wake_guard.py'           # live: a cough or a television shows nothing anywhere
```

`wake_guard.py` wakes the running brain as a pretend cube with his real coughs, a television
(`server/fixtures/nonspeech/`) and a plain "Sunday" with nothing after it, and checks that
none of them sends "speech" (the chime) or "duck", that no "listening", "partial" or "status"
goes anywhere and that no reply comes. Then "Sunday" and "What time is it?" must chime only
after the question began, and it prints how long the chime took. Last, a pretend Mac ear wakes
it while the pretend cube plays, 0.1 s after it goes quiet, and 0.65 s after: only the first
two stand down. Whatever the brain sends goes to the pretend devices, so nothing plays in the
room.

## Whose voice

The wake word fires on "Sunday" in any audio, and the gates after it read only words, so
`server/speaker_id.py` checks whose voice it is. Every wake (the word itself, which the cube
and the Mac send with it, plus what follows), every follow-up window and every stretch the
open ear hears is scored against a voiceprint of Alex's (NeMo TitaNet-small through
sherpa-onnx, `models/speaker/`), alongside the transcription, so it adds no wait to an answer.

- **Shadow mode** (the start): every decision is scored and logged as a `speaker check` line in
  `voice.log` and a `"speaker"` field in `data/speech.jsonl`; nothing is blocked.
- **Enforcing**: below the reject line is ignored with no chime, ring, overlay or answer; a
  borderline score goes ahead only if the directed-or-overheard judge says it was said to
  Sunday. The devices hold a wake's chime and ring until the brain says "speech", which it
  does the moment the voice is plainly his (the wake word alone, or the first 0.8 s after it)
  and words have been heard (see Words, not sounds).
  A stop or mute over its speech acts whoever says it. The cube's top button always counts.
- It switches to enforcing by itself once it holds 20 samples of him, 10 of them from real
  wakes, and his held-out samples clear the line at 98% or more while every test voice falls
  under it. The reject line is his 2nd percentile less 0.03.
- His samples: a spoken enrollment (`GET /enroll` from the Pi asks him once, aloud, and his
  answer is kept rather than answered), and every answered turn he follows up within 3 minutes
  that sounds like the samples already kept (0.35 or more until there are enough to set a line,
  then the line itself). Up to 80 are kept; an outlier against the rest is dropped.
- Test voices that are not his (`data/voiceprint/impostors.npz`): Kokoro, Piper, macOS and
  ElevenLabs voices in the room, from a phone speaker and from a TV, and Sunday's own voice
  through the cube's mic. Every wake's audio and verdict is kept in `data/voiceprint/wakes/`
  (the last 400) so the line can be set again from real wakes.
- The cube also reports, with each wake, its echo canceller's residual against its own voice's
  residual a moment before; a wake while it plays that is no louder than its own voice, in a
  voice not plainly his, is its own voice.

```bash
ssh voice-host 'cd ~/voice-assistant && venv/bin/python speaker_id.py status'   # samples, lines, mode
ssh voice-host 'cd ~/voice-assistant && venv/bin/python speaker_id.py score some.wav'
ssh voice-host 'cd ~/voice-assistant && venv/bin/python speaker_id.py mode shadow'  # by hand; auto hands it back
```

`/health` carries the same under `"speaker"`, with the last decision.

## Pronunciation

`server/pronounce.py` is the last pass before a clause is spoken. It reads each heteronym's sense from the sentence around it (the site is live, where you live; I read it a minute ago) and respells the word for the voice that will say it, so "live" meaning online goes out as "lyve" and the past tense of "read" as "red". The respelling reaches the voice alone; the popup, the logs and the speech gate see the words as written. The table is `RULES`, one row per sense with a respelling for ElevenLabs and one for Kokoro, and `python3 server/pronounce.py check` runs it against `CASES`. Kokoro's respellings were checked against the phonemes sherpa-onnx gives it through espeak-ng. ElevenLabs flash v2.5 drops a word in an SSML phoneme tag instead of saying it, so respelling is the only way to steer it.

## Memory of other chats

`server/transcripts.py` indexes every Claude Code chat on this Mac and on sunday, keeping only what was said. `transcripts-sync.timer` refreshes the index every 5 minutes. The speaker searches it when you ask where some piece of work stands.

When you ask it to pass something to a specific recent chat, `server/relay.py` resumes that chat headless on its own machine and speaks the chat's reply. It won't touch a chat that was active in the last two minutes.

## Popup

`popup/` is a small always-on-top glass panel at the top center of the screen your cursor is on. It shows your words as you speak, what Sunday is doing, and its reply. It listens on `ws://sunday:8794/events`.

- **Mac:** installed at `/Applications/Sunday.app`, opens at login, and reaches sunday through an SSH tunnel.
- **Windows:** installed at `%LOCALAPPDATA%\Programs\Sunday`, and started at logon by the `Sunday` scheduled task.

While the cube's ring is lit (listening, thinking or speaking), a blue line runs around the edge of every screen. The cube reports its ring to the brain, which passes it to the popup as a `light` event. The line is click-through and sits above everything. A wake with no words never lights the ring (see Words, not sounds), so a cough never shows the line, not even for a moment.

### What the panel shows

Each card has one label for where the exchange stands, your words as the recognizer heard them, one plain line of what Sunday is doing, and its answer.

| Label | When |
| :--- | :--- |
| Listening | after the wake, with your words appearing as you say them |
| Thinking | the question is in; the line under it says what Sunday is doing ("Searching the web", "Running a command") |
| Answering | Sunday is speaking the answer |
| Answered | the answer has been said; the card stays a little longer for a longer answer, and while the pointer is over it |
| Announcement | something Sunday says without being asked (`/say`) |
| Follow-Up | a late answer to an earlier question, with that question quoted above it |
| Stopped, Closed, Skipped | "stop", "close that", or a question replaced by the next one |
| Muted | the microphone is off; it stays until you unmute |
| Couldn't Answer | the turn failed; ask again |
| Can't Reach Sunday | the popup lost the brain and is reconnecting |

When a Mac ear rather than the cube heard you, the card says "This Mac". The bar under the cards counts work still in progress, or warns when the speaker is offline or its microphone button is off. A question's own lookup is shown once, on its card, not again in the list.

### The canvas

Some answers hold more than a voice can carry. For those Sunday says the short answer aloud, one or two sentences, and puts the detail on your screen: a full-screen sheet of frosted glass over a dimmed, blurred desktop, with headings, lists, tables, code, charts, figure tiles and notes. It opens on the screen your cursor is on, fills in while the answer is still being written, and says at the bottom how to close it. It is never closed on a timer.

- **When:** a technical question gets one (how something works, how things compare, how to set up or fix something, what some code does, figures you asked for). The time, the weather, a yes or no, one fact, small talk and where some work stands never do. `server/system_prompt.md` holds the rule; `canvas.worth_showing()` drops one too slight to need a screen and says it instead.
- **Never spoken:** the model writes the canvas after its spoken sentences as `<canvas title="...">markdown, with chart, stats and note fences</canvas>`. `server/canvas.py` cuts it out before the speech gate, the repeat guard or a voice sees a word of it, and checks and cleans every block. What is said is trimmed to two sentences once the detail is on screen.
- **Closing it:** say "close that", "hide that", "take that down", "got it", "thanks", "thank you", "that's all" or "I'm done" (with or without "Sunday" first). These act like "stop": no model turn and no reply, and only while a canvas is up; with nothing on screen they go to Sunday as before. A bare "thanks" is what the recognizer writes for noise, so it closes the canvas only when every word of it was heard clearly. Esc, a click on the dimmed area, the close button and the next question close it too, and closing it on one screen closes it on every screen.
- **Show Details** on an answered card brings a closed canvas back.
- **Safety:** the canvas renders from data, never from model HTML. Markdown runs with HTML off, links and images off, and through DOMPurify; code is highlighted locally; charts are drawn from numbers. The page has a strict Content Security Policy and can't load anything from the network. markdown-it, DOMPurify, highlight.js, Chart.js and the code font are bundled in `popup/vendor/` (`npm run vendor` copies them from `node_modules`).
- **Windows:** the canvas obeys the overlay switch and the game rule like the rest of the overlay. Held back over a game it is logged as `canvas held back for <game>: <title>` in `overlay.log`, the brain hears that it was held, and it opens if you turn the overlay on, or the game ends, within two minutes.

The schema is at the top of `server/canvas.py`: `{"title", "subtitle", "blocks": [...]}` with blocks `heading`, `text` (markdown), `list`, `table`, `code`, `chart` (bar, line, pie or donut; one number per label, eight series at most), `stats`, `callout` and `divider`. `canvas.py selftest` runs its cases, including the closing phrases and the gate's handling of "it's on your screen".

### The overlay on Windows

On Windows the panel and the blue line together are the overlay, and it stays off while a game is running. Sunday still hears you and answers through its speakers; only the Windows screen holds back.

- **By voice:** "Sunday, turn the overlay off" and "Sunday, turn the overlay on" (also "overlay off", "overlay on", "hide the overlay", "show the overlay"). Sunday confirms in a few words.
- **Hotkey:** Ctrl+Alt+Shift+O flips it, from anywhere, games included. Turning it on shows "Overlay on" for two seconds.
- **Tray:** Show Overlay in the Sunday tray menu. While a game is running the menu also names it.

The game rule. `popup/native/game-watch.ps1` checks every two seconds, and a game is any of these for as long as its process runs:

- an app in front that went fullscreen: Direct3D exclusive, or a window with no title bar covering its whole screen. The desktop and the shell never count, and neither does a browser, so a fullscreen video doesn't. Steam's Big Picture and hosted Store apps count only while they are in front.
- a program inside a game library: Steam (`steamapps\common`), Epic, Riot, EA, GOG, Ubisoft, Rockstar, Xbox.
- a program a launcher started (Steam, Epic, Battle.net, Riot, EA, GOG, Ubisoft, Rockstar, Xbox) that isn't part of the launcher or of Windows.
- a known game executable, or any Unreal Engine game (`...-Win64-Shipping.exe`).

A launcher on its own is not a game, since Steam runs all day. While a game runs the overlay is off unless you turn it on, and on it stays until you turn it off or that game closes; then the default, on, comes back. Turned off with no game running, it stays off, through restarts, until you turn it on. The app logs each game, each switch, each wake and each canvas it held back to `%APPDATA%\Sunday\logs\overlay.log`, and `/health` shows the Windows overlay under `"overlay"`.

To rebuild, run `npm run dist:mac` and `npm run dist:win` in `popup/`.

## The Mac ear

Sunday.app also listens for "sunday" on the Mac's built-in microphone (`popup/ear/mac_ear.py`,
with `popup/native/ear-audio` for the sound). It runs the cube's own wake model. No audio
leaves the Mac before a wake is cleared.

`ear-audio` sends the microphone twice: through macOS voice processing, which cancels what
the Mac plays, and plain, as a recording app gets it. Voice processing also strips his voice
from across the room down to scraps nothing can read: on 2026-10-13 three "Sunday, what time
is it?" from his usual spot never woke it and 21 of 25 wakes that morning closed with no name
heard, while the plain microphone heard every word and woke at 0.93 to 0.98. So the ear listens
to the plain microphone, turned up 12 dB for the brain's speech detector (`SUNDAY_EAR_PLAIN_GAIN_DB`,
and down at once for anything louder than -6 dBFS), and to the voice-processed one only while
Sunday talks on the Mac and for a second after, or while the Mac plays anything else (its popup's
meter, relayed by the brain as `computer_audio` with the machines playing). Its own ding is not
counted as the Mac playing. The wake model was trained on the cube's denoised feed, quiet
between words, so it hears the plain microphone turned down until the room sits at -92 dBFS
(`SUNDAY_EAR_WAKE_ROOM_DB`): at the microphone's own -65 dBFS his three calls from across the
room at 12:41 that day woke it none of the times, and at -92 all three. Each microphone has its
own front end for the wake model, so one never carries the other's sound into its next score.
`mic: plain` and `mic: voice-processed (why)` in `ear.log` mark each switch.

A wake while its reply plays on the Mac does not cost the follow-up window after the reply: the
window is dropped only once his words are taken (the brain's "speech") or the reply is stopped,
and a listen still open when the reply ends has the window open as soon as it closes.

His "Sunday, what time is it?" in one breath is at "is it" by the time the wake word fires, so
the Mac sends the 0.8 s before the wake at the head of what follows it (`"lead"` in the wake),
and the brain's mic-opened times for its name said alone start that much later.

A wake word needs 0.6 whether or not one of his computers is playing sound. His own "Sunday"
at the Mac scores 0.62 to 0.88 (seven of ten under 0.85, 2026-10-07 to 10-09), and the desktop
plays a game or a video for most of a morning, so the 0.85 it once needed then left him unheard
there; what that sound wakes it with shows and says nothing (see Words, not sounds).
`SUNDAY_EAR_CUTOFF` and `SUNDAY_EAR_PLAYING_CUTOFF` set the two; 0.85 still holds while a
reply of its own plays (`SUNDAY_EAR_SPEAKING_CUTOFF`).

The cube has priority. While the cube's speaker can be heard in the room, a Mac wake or Mac
follow-up window stands down: what the Mac heard is most likely the cube's voice, which the
Mac's voice processing cannot cancel. The cube reports that itself (`"playing"`), from the
loopback of what its speaker actually plays plus the room's echo tail, which it times at the
end of every stretch of playback as how long its mic takes to fall back to the room's level
(the 90th percentile of the last 20, logged as `echo tail N ms`). A Mac wake stands down only
if the cube was audible until less than `VOICE_EAR_FIRE_LAG` (0.4 s) before the wake arrived,
since a wake word fires 0.02 to 0.38 s after the word ends: a "Sunday" he starts once the cube
has gone quiet always gets through. A cube that does not report goes by the clock here
(playback plus 1.5 s, `VOICE_CUBE_EAR_TAIL`). Otherwise a wake on the Mac is only reported, and the brain waits up to
`VOICE_EAR_WAIT` (0.4 s) for the cube to report the same one. If the cube heard it, the Mac
drops it without a sound. If not, because the cube is too far, its mute button is on, or it's
offline, the Mac sends what was said since the wake, chimes once his words are in (see Words,
not sounds), then answers through its own speakers. A cube wake that arrives after the Mac was cleared stands down. Away from home
(the Mac isn't on the home Wi-Fi router) the Mac answers at once. Announcements play on the
cube, and on the Mac only while it's away. A late answer plays on whichever he last spoke to
(see Late answers).

- Turn it off with Listen on This Mac in the tray menu.
- It runs from its own Python environment in `~/Library/Application Support/Sunday/ear-venv`,
  made at first start. The log is `~/Library/Logs/Sunday/ear.log`.
- The cube's Bluetooth radio is off and its stack was retired with the factory software,
  so proximity is decided by who heard the wake rather than by Bluetooth signal strength.

The cube's own wake word runs three models. One hears the raw cancelled feed and decides, at
`speaking_wake_cutoff`, while speech is queued; one hears the denoised feed and decides after,
once its score has fallen under 0.5 (`REARM`), so the score it gave its own last word never wakes it. A model
that heard its own last words scored as its name stays deaf to "Sunday" for most of a second
after, so a third is started fresh each time the speaker goes quiet with nothing left queued,
and decides once the measured echo tail has passed. `cube/wake_replay.py` replays four lines
recorded from the cube (`cube/fixtures/line-end*.raw`, two of them ending in words it scores
as its name) with his real "Sunday" mixed in from 0 to 2 s after each: it must wake from 0.2 s
on and never on its own voice once speech is no longer queued.

```bash
cat cube/wake_replay.py | ssh cube 'mkdir -p /tmp/wake-replay/fixtures && cat > /tmp/wake-replay/wake_replay.py'
for f in cube/fixtures/*.raw; do cat $f | ssh cube "cat > /tmp/wake-replay/fixtures/${f##*/}"; done
ssh cube 'cd /tmp/wake-replay && python3 wake_replay.py'
```

## Any computer as an ear

`clients/mic_ear.py` makes any machine with a microphone and a speaker one more ear: Linux,
Windows, a Mac without the app, or a Pi with a USB speakerphone. It is the Mac ear with its
audio done by PortAudio, so it hears the same wake word, defers to the cube the same way and
speaks the same protocol. With no echo cancellation it stops listening while it talks;
`SUNDAY_EAR_DUPLEX=1` keeps it listening on a speakerphone that cancels its own echo.

```bash
pip install numpy sounddevice websockets pymicro-wakeword
SUNDAY_EAR_DEVICE=kitchen python3 clients/mic_ear.py
```

It finds the server from `host.lan_ip` and `host.voice_port` in the profile, or from
`SUNDAY_EAR_SERVER`. `SUNDAY_EAR_INPUT` and `SUNDAY_EAR_OUTPUT` pick the devices by number
or name (`python3 -m sounddevice` lists them).

## Operating it

```bash
deploy/deploy-server.sh                       # push the brain to sunday and restart it
deploy/deploy-cube.sh                         # push the agent to the cube and restart it
tools/ask "What's the weather like on Mars?"  # talk to the brain from the Mac
ssh voice-host 'curl -s localhost:8794/health'    # status, last latency
ssh voice-host 'curl -s "localhost:8794/say?text=Dinner%20is%20ready"'   # announce on the cube, unless he heard it
ssh voice-host '~/voice-assistant/venv/bin/python ~/voice-assistant/canvas.py show /tmp/plan.md --title "The plan"'   # a canvas on his screen
ssh voice-host 'curl -s "localhost:8794/show?close=1"'   # take it down
ssh voice-host '~/voice-assistant/venv/bin/python ~/voice-assistant/repeat_guard.py selftest --judge'
curl -s voice-host:8794/unmute                 # turn the microphone back on (also /mute)
```

- Say "Sunday, mute" to turn the microphone off; Sunday says nothing back. While muted it
  ignores everything except "Sunday, unmute". The cube's mute button, `/unmute` and the
  popup's Unmute button also turn it back on. The mute survives a server restart.
- "Sunday, stop" silences it without a reply.
- `/show` is the canvas's `/say`, from localhost on sunday only: `?spec=<canvas JSON>`, or
  `?md=<markdown>&title=..&subtitle=..`, or `?close=1`; with neither it reports what is showing.
  It answers with where it landed, `{"ok": true, "id", "shown": true, "screens": {"Desktop":
  "held back: KSP_x64.exe is running", "my-macbook": "shown"}, "problems": []}`,
  so a caller says "it's on your screen" only when `shown` is true. `canvas.py show <file|->`
  takes a JSON spec or markdown and does the encoding. A canvas sent with an announcement lets
  the announcement say the detail is on his screen; Ratchet checks that too.
- "Sunday, use ElevenLabs" and "Sunday, switch to the normal voice" change the voice and
  confirm in a few words. So do `/voice/elevenlabs` and `/voice/local`, and the ElevenLabs button
  in the popup or its tray menu. `/voice` shows the mode, the fallbacks and the quota left. The
  mode survives a restart.
- The personality is `server/system_prompt.md`.
- The voice, model and timings are set by environment variables at the top of
  `server/voice_server.py`.
- Cube settings (server address, volume, wake word) live in `/data/sunday/config.json`.
- Logs: `/tmp/sunday.log` on the cube, `~/voice-assistant/voice.log` on sunday.

## Factory image

`~/Archives/cube-factory-2026-10-05` holds the factory root filesystem, `/data` and
the boot, recovery and logo partitions.

Never read `/dev/mtd0` or `/dev/mtd1` on the cube. A raw read of the bootloader
locks the flash controller until the cube is power-cycled.
