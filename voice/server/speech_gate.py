#!/usr/bin/env python3
"""The speech gate: every answer and announcement is checked against Sunday's speaking
rules before it is said. The rules in system_prompt.md are background for the model;
this is what enforces them.

  A  giving up without having tried another route
  B  asking him to type, read a screen, or use a chat or keyboard (saying a canvas that was
     actually sent is on his screen is not asking him anything)
  C  speaking as something other than the one assistant (chats, sessions, agents, the desk)
  D  stating as done or live something that is still in progress

A regex prefilter clears ordinary speech in microseconds. Anything it flags goes to a
Haiku judge, run from a process started ahead of time so the call costs about a second
rather than three. A judge error or timeout fails open and says so loudly.

    speech_gate.py replay     # the real lines that broke the rules, and good ones that must pass
    speech_gate.py newstest   # the news check's "nothing new" replies are never spoken
    speech_gate.py check "<text>" [--question Q] [--active "label"]...
"""

import asyncio
import json
import logging
import os
import re
import sys
import time

try:
    import mtoa_config  # the owner's names and hosts; see mtoa_config.py
except ImportError:  # run from another directory
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("mtoa_config", str(__import__("pathlib").Path(__file__).resolve().parent / "mtoa_config.py"))
    mtoa_config = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(mtoa_config)
_render = mtoa_config.render


def _render_all(table):
    return {key: _render(value) for key, value in table.items()}

log = logging.getLogger("voice")

JUDGE_MODEL = os.environ.get("VOICE_GATE_MODEL", "claude-haiku-4-5-20251001")
JUDGE_TIMEOUT = float(os.environ.get("VOICE_GATE_TIMEOUT", "8"))
JUDGE_SPARES = int(os.environ.get("VOICE_GATE_SPARES", "2"))
JUDGE_SPARE_AGE = float(os.environ.get("VOICE_GATE_SPARE_AGE", "1200"))   # a spare older than this is replaced
ALWAYS_JUDGE = os.environ.get("VOICE_GATE_ALWAYS", "0") == "1"

RULES = _render_all({
    "A": "giving up: it declines, defers or says it can't, without having tried another route",
    "B": "asking {him} to type, read a screen, or use a chat, app or keyboard, when {he} answers by voice",
    "C": "talking about chats, sessions, agents, the desk or relaying, as if something other than {assistant} did the work",
    "D": "stating as done, live or working something that is still in progress",
})

# ---------------------------------------------------------------- prefilter

_A = re.compile(
    r"\b(?:can(?:no|')?t|cannot|could(?:n'?t| not)|unable|not able|isn'?t (?:working|available|responding|loading)|"
    r"not (?:working|available|responding|loading|reachable|connected)|(?:doesn'?t|won'?t|didn'?t) (?:work|load|connect|go through)|"
    r"unavailable|unreachable|offline|(?:has |have |had |was |got )?(?:dropped|disconnected|timed out|failed|broken|down)\b|"
    r"don'?t have (?:access|a way|permission|the)|no (?:access|way to|connection)|lost (?:the )?(?:connection|access)|"
    r"(?:try|ask|check) (?:again )?(?:later|back)|later on|for now|right now|at the moment|not yet|haven'?t been able|"
    r"(?:you'?ll|you will|you'd|you) (?:need|have|want) to|I'?m afraid|unfortunately|sorry)\b", re.I)
_B = re.compile(
    r"\b(?:type|typing|typed|keyboard|screen|click|tap|window|popup|pop-up|chat|message (?:box|window)|"
    r"(?:read|look at|check|open) (?:it |that |this )?(?:on|in) (?:the |your )|in the (?:app|browser|terminal)|"
    r"text (?:me|it|back)|write (?:back|in)|reply (?:in|there|with)|paste|enter (?:yes|it|the))\b", re.I)
_C = re.compile(
    r"\b(?:chats?|sessions?|agents?|subagents?|the desk|sunday desk|desk|relay\w*|helpers?|assistants?|claude|"
    r"(?:pass|passed|passing|hand|handed|handing|sent|send|forward\w*)(?: it| that| this| them)? (?:on|along|off|over|to)|"
    r"(?:told|asked|got|had|let) (?:it|them|the other)|it'?s (?:working|on it|doing|handling)|they'?re (?:working|on it|doing|handling)|"
    r"another (?:copy|instance|one of me)|the other (?:one|copy|instance)|copy of me|worker|bot|background (?:job|task|work))\b", re.I)
_D = re.compile(
    r"\b(?:done|finished|complete[d]?|live|deployed|shipped|released|published|working|works|fixed|now|ready|"
    r"set up|all set|sorted|in place|updated|takes? priority|will (?:never|always|now)|never|always|is on|are on|"
    r"went out|sent|merged|landed|resolved|solved)\b", re.I)


# The words rule D allows for unfinished work: "I'm sending it", "I'm working on it", "it hasn't
# gone out yet". They are struck out before the D words are looked for, so the right way
# to describe work underway never needs a judge to clear it.
_D_UNDERWAY = re.compile(
    r"\b(?:(?:I'?m|I am|we'?re|we are|still)\s+(?:still\s+)?\w+ing\b(?:\s+on\b)?|"
    r"(?:hasn'?t|haven'?t|isn'?t|aren'?t|wasn'?t|not)\s+(?:been\s+|yet\s+|quite\s+)?\w+(?:\s+(?:out|up|live))?|"
    r"not yet|(?:I'?ll|I will) (?:tell|let) you (?:when|once|as soon as)[^.!?]*)", re.I)


# Saying the detail is on his screen, when a canvas went there with this answer: "the breakdown
# is on your screen", "I've put the steps on your screen". It asks him nothing, so it is struck
# out before the B words are looked for. Only with a canvas actually sent; anything else that
# mentions a screen still goes to the judge, and so does any request left in the sentence.
_NAMED = r"(?!(?:click|tap|type|open|scroll|read|look|press|enter|reply|write|check|select|copy|paste)\b)\w+"
_B_SHOWN = re.compile(
    rf"\b(?:(?:it|that|this|they)(?:'?s|'?re| is| are)|(?:the|your)(?: {_NAMED}){{1,4}} (?:is|are)|(?:I'?ve|I have|I)"
    rf"(?: just| also)? (?:put|pulled|brought|got|shown|showed|showing|putting|pulling)"
    rf"(?: (?:it|that|them|the(?: {_NAMED}){{1,4}}))?"
    r"(?: up)?)(?: all| also| now)?(?: up)? on (?:your|the|his) screens?(?: now)?|\bon (?:your|the) screen now\b", re.I)


def prefilter(text: str, in_progress=(), rules: str = "ABCD", shown: str = "") -> list:
    """The rules this text might break. Empty means it is cleared without a judge. `shown` names
    the canvas sent to his screen with this speech, if one was."""
    hits = []
    if "A" in rules and _A.search(text):
        hits.append("A")
    if "B" in rules and _B.search(_B_SHOWN.sub(" ", text) if shown else text):
        hits.append("B")
    if "C" in rules and _C.search(text):
        hits.append("C")
    if "D" in rules and in_progress and _D.search(_D_UNDERWAY.sub(" ", text)):
        hits.append("D")
    return hits


def rules_for(kind: str) -> str:
    """The rules the Pi judges for each kind of speech. A /say announcement was already
    checked against all four by the Ratchet hook on the Mac, which reads the whole
    transcript of the work it reports; this judge sees one paragraph and no record of
    what finished, so it judges A, B and C there and leaves D to the hook. Replies and
    late answers are the Pi's own words and keep all four."""
    return "ABC" if kind == "announcement" else "ABCD"


# ---------------------------------------------------------------- judge

JUDGE_PROMPT = _render("""You enforce four rules on what {assistant}, a voice assistant, is about to say aloud to {user}. Judge only the text under SPEECH, using the context given. Reply with one JSON object and nothing else:
{"verdict": "ok" or "broken", "rule": "A", "B", "C", "D" or "", "why": "one short sentence"}

A. Giving up. The speech declines, defers, or says {assistant} can't do it, can't see it, or that something isn't working, dropped, is unavailable or can't be reached ("I can't", "isn't working", "the connection dropped", "unable", "not available", "I don't have access"). That is broken unless TOOLS RUN show this turn actually tried another route for the same thing after the first one failed: `relay.py ask desk` for anything on {his} accounts (mail, calendar, Teams, GitHub, Figma, Vercel, Stripe), `ssh {desk_ssh}` for {his} Mac, a web search or fetch, or a different tool. Saying {he} should try later, or offering old information instead of getting current information, is giving up too. Not giving up: a plain fact about the world that is the answer to {his} question ("the site is down" when {he} asked if it is up), saying a part is still being checked, or asking for a spoken yes before sending, spending or deleting.

B. Asking {him} to type, to read or look at a screen, or to use a chat, app, window, popup or keyboard, to answer or to do a step. {He} talks to {assistant} by voice and answers by voice. Asking {him} for a spoken answer is fine. Rule B is only broken by a request that {he} do something with {his} hands or eyes. A statement about what {assistant} itself could or could not see, watch or test ("I could not watch it on screen myself, so the drag has not been tried yet") asks {him} nothing and is ok; so is describing what a screen, tab or window now shows.
When ON HIS SCREEN says a canvas was sent with this speech, saying plainly that the detail is on {his} screen ("The breakdown is on your screen.", "I've put the breakdown by folder on your screen.") is ok: it tells {him} where {assistant} put the detail, the way a person says "it's in your inbox", and asks {him} to do nothing. Asking {him} to type, click, open, scroll, reply in writing or do anything else with it is still broken. When ON HIS SCREEN says nothing was sent, claiming something is on {his} screen is broken.

C. Speaking from outside the single-{assistant} perspective: mentioning chats, sessions, agents, helpers, the desk or {assistant} Desk, relaying, passing things on, "I've told it", "it's working on it", "they're on it", or anything else doing the work as separate from {assistant}. All work is {assistant}'s own, said in the first person. "It" meaning a website, file or thing is fine; "it" meaning another worker is not. Naming something {assistant} built or runs - a pipeline, a script, a scheduled job, a check, a site - and saying what it is doing or holding is fine; so is a device or piece of hardware as the subject of a sentence - the cube, the Mac, a speaker, a microphone, a browser - saying what it heard, played or does ("the Mac picked up my voice", "the Mac stands down while this speaker talks"). Rule C is only about another assistant, chat, session or agent doing {assistant}'s work. {assistant} saying in the first person that it does, holds, skips or builds something its own pipeline, scripts or jobs carry out ("I am holding their sites", "I only build once a letter can send") is exactly right and never breaks rule C.

D. Stating as done, live, working, fixed, or settled behaviour something that is still in progress. IN PROGRESS lists work not finished yet, and STILL CHECKING lookups have not come back. If the speech presents any of that work as finished, live, or as how things now are or will always be, it is broken; it must be described as in progress.
Describing that work as underway is exactly what rule D asks for, and is always ok: the present progressive ("I'm sending it", "I'm working on getting it to you", "I'm on it", "I'm fixing it") and "it hasn't gone out yet", "I'll tell you when it lands". Those say the work is happening, not that it is finished or settled.
Rule D is about the particular work each IN PROGRESS line names, and nothing else. A claim about different work is ok even while that runs: if IN PROGRESS is a lookup about the preview email, "both sites are fixed and live" is a claim about the sites, not the email, and is ok. Only judge a claim broken when the in-progress line is about that same thing. Words describing something else in the past ("a copy of the email exactly as it went out to {him}") are not a claim that the in-progress work is done. Asking {his} yes on follow-on items (a policy line, a tag, a next step) does not make the work already reported unfinished: "X is live. Two things need your yes: Y and Z" is ok when X is not listed as in progress.

When the case names the rules to judge, judge only those; a rule it says does not apply is never the verdict.

Decide on what the words actually say. A normal answer (the time, a fact, a finished result, small talk) is ok. Only one rule goes in "rule": the clearest one broken.""")


class Judge:
    """Haiku processes started ahead of time and used once each, so every verdict is
    judged fresh and none waits on a cold start."""

    def __init__(self):
        self.spares: list = []
        self.filling = None

    async def _spawn(self):
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", "--model", JUDGE_MODEL,
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--setting-sources", "", "--strict-mcp-config", "--allowed-tools", "", "--tools", "",
            "--system-prompt", JUDGE_PROMPT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd="/tmp", limit=1 << 22,
            env={**os.environ, "RATCHET_OFF": "1", "PEER_REPLY_GUARD_OFF": "1", "MAX_THINKING_TOKENS": "0"})
        return (time.monotonic(), proc)

    async def fill(self):
        now = time.monotonic()
        for born, proc in [s for s in self.spares if now - s[0] > JUDGE_SPARE_AGE or s[1].returncode is not None]:
            self.spares.remove((born, proc))
            if proc.returncode is None:
                proc.kill()
        while len(self.spares) < JUDGE_SPARES:
            self.spares.append(await self._spawn())

    def refill(self):
        if not self.filling or self.filling.done():
            self.filling = asyncio.get_running_loop().create_task(self.fill())

    async def keep(self):
        while True:
            try:
                await self.fill()
            except Exception as e:
                log.warning("speech gate: could not start a judge: %s", e)
            await asyncio.sleep(60)

    async def ask(self, case: str) -> str:
        proc = None
        while self.spares:
            _, p = self.spares.pop(0)
            if p.returncode is None:
                proc = p
                break
        if proc is None:
            _, proc = await self._spawn()
        self.refill()
        try:
            msg = {"type": "user", "message": {"role": "user", "content": case}}
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            await proc.stdin.drain()
            proc.stdin.close()
            while True:
                line = await proc.stdout.readline()
                if not line:
                    raise RuntimeError("judge exited without a verdict")
                m = json.loads(line)
                if m.get("type") == "result":
                    if m.get("is_error"):
                        raise RuntimeError(str(m.get("result"))[:200])
                    return m.get("result") or ""
        finally:
            if proc.returncode is None:
                proc.kill()


judge = Judge()


def _case(text: str, question: str, tools: list, in_progress: list, kind: str, rules: str = "ABCD",
          shown: str = "") -> str:
    parts = []
    if rules != "ABCD":
        skipped = ", ".join(r for r in RULES if r not in rules)
        parts.append(f"RULES TO JUDGE: {', '.join(rules)} only. Rule {skipped} does not apply here: this "
                     f"announcement was already checked against it, with the full record of what has finished.")
    if kind == "reply":
        parts.append(f"HIS QUESTION: {question or '(none)'}")
    else:
        parts.append(f"This is an {kind}: said out of the blue, not in reply to a question."
                     + (f" It is about: {question}" if question else ""))
    if tools:
        parts.append("TOOLS RUN THIS TURN, in order:\n" + "\n".join(f"- {t}" for t in tools[-30:]))
    elif kind == "reply":
        parts.append("TOOLS RUN THIS TURN: none")
    parts.append("IN PROGRESS: " + ("; ".join(in_progress) if in_progress else "nothing"))
    if "B" in rules:
        parts.append("ON HIS SCREEN: " + (f"a canvas was sent with this speech: {shown}" if shown
                                          else "nothing was sent to his screen with this speech"))
    parts.append(f"SPEECH:\n{text}")
    return "\n\n".join(parts)


def _parse(out: str) -> dict:
    start = out.find("{")
    if start < 0:
        raise ValueError(f"no JSON in {out[:120]!r}")
    # The first object only: the judge sometimes adds a second line after it.
    v, _ = json.JSONDecoder().raw_decode(out[start:])
    verdict = str(v.get("verdict", "")).lower()
    if verdict not in ("ok", "broken"):
        raise ValueError(f"bad verdict {verdict!r}")
    rule = str(v.get("rule", "")).upper()[:1]
    if verdict == "broken" and rule not in RULES:
        raise ValueError(f"broken with no rule: {out[:120]!r}")
    return {"verdict": verdict, "rule": rule if verdict == "broken" else "", "why": str(v.get("why", ""))[:300]}


async def check(text: str, question: str = "", tools=(), in_progress=(), kind: str = "reply",
                shown: str = "") -> dict:
    """{"verdict": "ok"|"broken"|"error", "rule", "why", "by": "prefilter"|"judge"|"error", "ms", "flags"}.
    shown: the canvas sent to his screen with this speech ("" when none), which is what lets it say so."""
    t0 = time.monotonic()
    tools, in_progress = list(tools), list(in_progress)
    rules = rules_for(kind)
    flags = prefilter(text, in_progress, rules, shown)
    if not flags and not ALWAYS_JUDGE:
        return {"verdict": "ok", "rule": "", "why": "", "by": "prefilter", "flags": [], "ms": 0}
    try:
        out = await asyncio.wait_for(judge.ask(_case(text, question, tools, in_progress, kind, rules, shown)),
                                     JUDGE_TIMEOUT)
        v = _parse(out)
        if v["verdict"] == "broken" and v["rule"] not in rules:
            # A rule this kind of speech is not judged on here is never a reason to drop it.
            v = {"verdict": "ok", "rule": "", "why": f"rule {v['rule']} not judged here: {v['why']}"[:300]}
        v.update(by="judge", flags=flags, ms=int((time.monotonic() - t0) * 1000))
        return v
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        log.error("SPEECH GATE FAILED OPEN after %d ms (%s: %s); speaking unchecked: %s",
                  ms, type(e).__name__, e, text[:160])
        return {"verdict": "error", "rule": "", "why": f"{type(e).__name__}: {e}"[:300], "by": "error",
                "flags": flags, "ms": ms}


# ---------------------------------------------------------------- what to tell Sunday

ROUTES = _render_all({
    "A": ("Solve it another way before answering: `~/voice-assistant/relay.py ask desk \"<{his} question>\"` for anything "
          "on {his} accounts (mail, calendar, Teams, GitHub, Figma, Vercel, Stripe), `ssh {desk_ssh}` for {his} Mac, the "
          "web, or another tool. Only if every route truly fails, say in one sentence what you tried and what you are "
          "doing next, and start doing it."),
    "B": ("{He} answers by voice only. Do that step yourself, or ask {him} for a spoken answer. Never ask {him} to type, "
          "read a screen, click, or use a chat or keyboard. You may say the detail is on {his} screen only when your "
          "answer carries a canvas block."),
    "C": ("You are {assistant}, the one assistant, and all of that work is yours. Say it in the first person, with no "
          "chats, sessions, agents, desk, relaying or anything else doing the work."),
    "D": ("That work is still in progress. If a lookup on it already came back this turn or earlier, answer from "
          "that and do not ask the same thing again; otherwise say it's in progress (\"I'm on it\", \"I'm sending "
          "it\"), never done, live or settled."),
})


def retry_prompt(verdict: dict, reply: str, in_progress=()) -> str:
    rule = verdict["rule"]
    extra = f" Still in progress: {'; '.join(in_progress)}." if rule == "D" and in_progress else ""
    return (f"SPEECH GATE: your answer was not spoken. It broke rule {rule}, {RULES[rule]}. {verdict['why']}\n\n"
            f"Your answer was: \"{reply.strip()}\"\n\n{ROUTES[rule]}{extra} Then answer his question again. Only your "
            f"new answer will be spoken, under the same rules.")


def final_prompt(verdict: dict, reply: str) -> str:
    return (f"SPEECH GATE: that answer was not spoken either; it broke rule {verdict['rule']}, {RULES[verdict['rule']]}. "
            f"\"{reply.strip()}\"\n\nStart whatever you are doing next on his question now, then reply with one sentence "
            f"only: what you tried and what you are doing next, in the first person, without declining, without "
            f"mentioning chats, sessions, agents or the desk, without asking him to type or read anything, and without "
            f"calling anything done that isn't.")


def routes_tried(tools: list) -> list:
    tried = []
    joined = "\n".join(tools)
    if "relay.py ask desk" in joined or "relay.py new" in joined or "relay.py send" in joined:
        tried.append("your accounts on the Mac")
    desk = mtoa_config.get("desk.ssh", "")
    if desk and ("ssh " + desk) in joined:
        tried.append("your Mac directly")
    if re.search(r"^(?:WebSearch|WebFetch)", joined, re.M):
        tried.append("the web")
    return tried


def fallback_line(tools: list) -> str:
    tried = routes_tried(tools)
    if not tried:
        return "I haven't got that yet, and I'm still working on it."
    said = tried[0] if len(tried) == 1 else ", ".join(tried[:-1]) + " and " + tried[-1]
    return f"I've tried {said} and haven't got it yet, so I'm still working on it."


# ---------------------------------------------------------------- the news check's replies
#
# only_news asks a model to strike from an announcement what he has already heard. When none
# of it is new it is told to answer NONE, but it has also answered "(Nothing new remains)",
# which was then said aloud. Whatever it says about there being nothing new is never speech.

_META_NOTHING = re.compile(r"\bnothing\b.*\b(?:new|left|remains?|remaining)\b|"
                           r"\b(?:new|left|remains?|remaining)\b.*\bnothing\b", re.I)
_META_ALREADY = re.compile(r"\balready\b.*\b(?:said|told|heard)\b|\b(?:said|told|heard)\b.*\balready\b", re.I)


def news_meta(text: str) -> bool:
    """A reply from the news check that is a remark about the announcement rather than any
    of it: NONE, anything wholly in parentheses or brackets, or eight words or fewer saying
    nothing new is left or that it was already said."""
    core = " ".join((text or "").split()).strip(" .,!?:;\"'`*_-")
    if not core or core.upper() == "NONE":
        return True
    if re.fullmatch(r"[(\[{<].*[)\]}>]", core, re.S):
        return True
    return len(core.split()) <= 8 and bool(_META_NOTHING.search(core) or _META_ALREADY.search(core))


def news_kept(kept: str, original: str) -> str:
    """What may be spoken of the news check's reply: "" when it is only such a remark, and
    otherwise its sentences without any remark or bracketed aside the announcement did not
    have."""
    orig = " ".join((original or "").split()).lower()
    said = {p.strip().lower() for p in re.split(r"(?<=[.!?])\s+", orig) if p.strip()}
    kept = " ".join((kept or "").split())
    if news_meta(kept) and kept.lower() not in said:
        return ""
    kept = re.sub(r"\s*[(\[][^)\]]*[)\]]", lambda m: m.group(0) if m.group(0).strip().lower() in orig else "", kept)
    parts = [p for p in re.split(r"(?<=[.!?])\s+", kept) if p.strip()]
    out = " ".join(p for p in parts if p.strip().lower() in said or not news_meta(p)).strip()
    return "" if news_meta(out) and out.lower() not in said else out


NEWS_CASES = [
    # (the model's reply, the announcement, what may be spoken)
    ("NONE", "The site is live.", ""),
    ("None.", "The site is live.", ""),
    ("(Nothing new remains)", "The site is live.", ""),
    ("[nothing new]", "The site is live.", ""),
    ("Nothing new is left.", "The site is live.", ""),
    ("Nothing remains to say.", "The site is live.", ""),
    ("All of this was already said.", "The site is live.", ""),
    ("He has already heard all of it.", "The site is live.", ""),
    ("You were already told this.", "The site is live.", ""),
    ("The site is live. (The rest was already said.)", "The deploy finished. The site is live.", "The site is live."),
    ("The site is live. Nothing new remains beyond that.", "The deploy finished. The site is live.",
     "The site is live."),
    ("Sam's site is live.", "Chris's site is down. Sam's site is live.", "Sam's site is live."),
    ("The new header is live, and nothing else changed.", "The new header is live, and nothing else changed.",
     "The new header is live, and nothing else changed."),
    # The announcement's own words are spoken even when they read like a remark.
    ("Nothing new to report on the build.", "Nothing new to report on the build.",
     "Nothing new to report on the build."),
    ("Ten letters went out with the seven o clock run (the first of the day).",
     "Ten letters went out with the seven o clock run (the first of the day).",
     "Ten letters went out with the seven o clock run (the first of the day)."),
]


def newstest() -> int:
    bad = 0
    for reply, original, expect in NEWS_CASES:
        got = news_kept(reply, original)
        bad += got != expect
        print(f"{'PASS' if got == expect else 'FAIL'}  {reply!r} -> {got!r}")
    return bad


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return m.group(1) if m else text


# ---------------------------------------------------------------- replay

REPLAY = [
    # (text, question, tools, in_progress, expect[, kind])
    ("On your emails, I can't see your inbox right now because the Outlook connection has dropped.",
     "What emails do I have?", [], [], "A"),
    ("It's ready to ship, so type yes in the Sunday Desk chat and I will put it live.",
     "Is the site ready?", [], [], "B"),
    ("I've told it to get Figma itself and carry on.",
     "Did you get the Figma file?", [], [], "C"),
    ("The cube now takes priority, and the two will never both answer.",
     "Is the cube getting priority now?", [],
     ["New Chat: sunday-voice: make the cube take priority over the Mac ear so the two never both answer (Working)"], "D"),
    ("It's 7:20.", "What time is it?", [], [], "ok"),
    ("Jamie's site now says coaching is coming soon.", "What does Jamie's site say about coaching?",
     ["Bash: ssh my-mac 'curl -s https://jamie.example/coaching' -> <h2>Coaching coming soon</h2>"], [], "ok"),
    ("Jamie's site now says coaching is coming soon.", "What does Jamie's site say about coaching?",
     ["Bash: ssh my-mac 'curl -s https://jamie.example/coaching' -> <h2>Coaching coming soon</h2>"],
     ["New Chat: sunday-voice: make the cube take priority over the Mac ear (Working)"], "ok"),
    # Refused under D on 2026-10-06 although the work was finished and verified: the judge
    # read "two things need your yes" as the work being unfinished.
    ("Preview visitor tracking is live and checked. I record which sections prospects read, what they tap and "
     "whether they come back, never what they type, and your own visits are left out. The Previews tab shows it "
     "per prospect and per template. Two things need your yes: adding a line about it to the privacy policy, which "
     "by its own terms means emailing clients the change, and tagging the letter link so visits from the letter "
     "show as letter traffic.", "", [], [], "ok", "announcement"),
    # Refused on 2026-10-06, so he heard "still working" instead: C for naming a pipeline
    # Sunday built, B for saying what Sunday itself could not watch.
    ("The scripted preview pipeline is running on the Pi, and five sites are built and live, but none are sent "
     "yet. Sending is held at ten a day because the outreach ramp was halted for bounces, so tell me if you want "
     "it at fifty.", "", [], [], "ok", "announcement"),
    ("Yes, it is live on your Mac. The pop out window is gone, and the overlay now moves by its bottom bar and "
     "resizes from any edge. I could not watch it on screen myself, so the drag and resize have not been tried "
     "by hand yet.", "", [], [], "ok", "announcement"),
    ("The pipeline is done, so open the Previews tab and click Approve to start the send.",
     "", [], [], "B", "announcement"),
    ("I passed that to the other chat and it is working on the send now.", "", [], [], "C", "announcement"),
    # Refused under C on 2026-10-06 for being first person about work the pipeline does.
    ("842 of them got their second email in the last five days so I am holding their sites until the wait is up, "
     "and I only build once a letter can send.", "", [], [], "ok", "announcement"),
    # Refused under C on 2026-10-06: a device named as the subject read as a separate agent.
    ("The Mac picked up my voice from this speaker and took my own words as your question, so I am making the Mac "
     "stand down whenever this speaker is talking, and making me drop anything that is mostly my own words, numbers "
     "included.", "", [], [], "ok", "announcement"),
    # Refused under D on 2026-10-06 although each said the work was underway, the way rule D
    # asks: the present progressive read as "done" for work that had just been started.
    ("I'm sending one of the preview emails to your inbox exactly as it went out, and I'll tell you when it lands. "
     "It hasn't been sent yet.",
     "Sunday, send me an email of one of the preview emails that we sent out, exactly how it was sent out to my "
     "inbox. I want to preview it.",
     ["Bash: cd ~/voice-assistant; ./relay.py new /Users/you/Development/WebstormProjects/acme-com "
      "\"Alex wants to preview one of the preview outreach emails exactly as it was sent to a prospect.\" -> "
      "started a new chat in /Users/you/Development/WebstormProjects/acme-com on the Mac; its result "
      "will be spoken when it finishes"],
     ["New Chat: acme-com: Alex wants to preview one of the preview (third letter, site-preview) outreach "
      "emails exactly as it wa (Starting)"], "ok"),
    ("I'm working on getting that preview email to your inbox. It hasn't gone out yet, and I'll tell you once "
     "it's there.",
     "Sunday, send me an email of one of the preview emails that we sent out, exactly how it was sent out to my "
     "inbox. I want to preview it.",
     ["Bash: cd ~/voice-assistant; ./relay.py new /Users/you/Development/WebstormProjects/acme-com "
      "\"Alex wants to preview one of the preview outreach emails exactly as it was sent to a prospect.\" -> "
      "started a new chat in /Users/you/Development/WebstormProjects/acme-com on the Mac; its result "
      "will be spoken when it finishes"],
     ["New Chat: acme-com: Alex wants to preview one of the preview (third letter, site-preview) outreach "
      "emails exactly as it wa (Starting)"], "ok"),
    # Refused under D on 2026-10-06 though both sites were released and verified: the only
    # thing in progress was a lookup about the preview email, which is other work.
    ("Both sites are fixed and live. The preview email still isn't in your inbox, and I'm still on it.",
     "Status",
     ["Bash: cd ~/voice-assistant; (./relay.py ask f2e2b1bb \"Status of Springfield Go-Karts zero views fix? Shipped?\" "
      "& ./relay.py ask 55cc8b2f \"Status of Dome Break analytics and privacy?\" & ./relay.py ask desk \"Has the "
      "preview email been sent to Alex's inbox yet? Status.\" & wait) -> Springfield Go-Karts is live as twenty "
      "twenty-six point forty-one point zero, and a real visit landed in the console. Dome Break is live as release "
      "2026.41.1 with the tracker added. STILL CHECKING (lookup 0123456789abcdef): no answer on this yet."],
     ["a lookup that has not come back: Has the preview email been sent to Alex's inbox yet? Status."], "ok"),
    # A canvas went to his screen with the answer: saying so asks him nothing (rule B). The
    # seventh field names what was sent; without one, the same words claim a screen that is empty.
    ("TCP checks that every packet arrives in order, and UDP just sends them. The comparison is on your screen.",
     "What's the difference between TCP and UDP?", [], [], "ok", "reply", "TCP and UDP: a table and a bar chart"),
    ("Disk use on the Pi is at eighty two percent. I've put the breakdown by folder on your screen.",
     "How full is the Pi's disk?", ["Bash: df -h / -> /dev/root 58G 47G 9.1G 84% /"], [], "ok", "reply",
     "Disk use on sunday: figures and a bar chart"),
    ("Seven tickets are open, and two of them are about the Springfield site. The full list of open tickets is on your screen.",
     "What tickets are open?", ["Bash: gh issue list --state open -> 7 issues"], [], "ok", "reply",
     "Open tickets: a table"),
    ("TCP checks that every packet arrives in order, and UDP just sends them. The comparison is on your screen.",
     "What's the difference between TCP and UDP?", [], [], "B", "reply", ""),
    ("The steps are on your screen, so click the first link and type in the code it gives you.",
     "How do I turn on two factor for GitHub?", [], [], "B", "reply", "GitHub two factor: a list of steps"),
    ("I've put the numbers on your screen. Open the Previews tab and click Approve to start the send.",
     "", [], [], "B", "announcement", "Preview sends: a table"),
]


async def replay():
    await judge.fill()
    bad = 0
    for text, question, tools, prog, expect, *kind in REPLAY:
        v = await check(text, question, tools, prog, *kind)     # kind, then the canvas shown with it
        got = v["rule"] if v["verdict"] == "broken" else v["verdict"]
        ok = got == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  expect {expect:<2} got {got:<5} by {v['by']:<9} {v['ms']:>5} ms"
              f"  {text[:70]}\n      {v['why']}")
    for _, p in judge.spares:
        p.kill()
    return bad


async def _check_cli(argv):
    text, question, prog = argv[0], "", []
    i = 1
    while i < len(argv):
        if argv[i] == "--question":
            question = argv[i + 1]
        elif argv[i] == "--active":
            prog.append(argv[i + 1])
        i += 2
    v = await check(text, question, [], prog)
    print(json.dumps(v))
    for _, p in judge.spares:
        p.kill()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sys.argv[1:2] == ["replay"]:
        sys.exit(asyncio.run(replay()))
    elif sys.argv[1:2] == ["newstest"]:
        sys.exit(1 if newstest() else 0)
    elif sys.argv[1:2] == ["check"] and len(sys.argv) > 2:
        asyncio.run(_check_cli(sys.argv[2:]))
    else:
        sys.exit(__doc__)
