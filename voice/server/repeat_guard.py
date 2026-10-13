#!/usr/bin/env python3
"""The repeat guard: Sunday never says aloud again what he has already heard since he last
asked for it. It is enforced here, in code, at the one place every line goes to a speaker
(CubeSession.speak in voice_server.py), whatever produced the line: an answer, a follow-up,
the filler said while an answer is worked out, a /say announcement or relayed reply, a /late
answer. Nothing about it rests on the prompt.

What a line is checked against depends on what it answers:

  an answer        what was said aloud since the request it answers. A request is something
                   he said with words in it: "Sunday", "hey", "oh" ask for nothing, so an
                   answer to one of those is keyed on his request before it.
  /say answer=1    what was said since his latest request, or since ?asked= when given.
  /late            what was said since the question it answers was asked.
  /say, relayed    everything said in the last half hour: nobody asked for it.
  a command's line only what is being said at the same moment.

Him asking again moves the starting point, so a question asked twice is answered twice. A
line is a repeat of one said since that point when it is:

  exact      the same words once case, punctuation, contractions and numbers are put in one
             form ("11:13" is "eleven thirteen", "I'm" is "I am", "twenty-two" is "22")
  near       the same words give or take one in four that carry meaning, the same numbers
             exactly ("plant 401" is never "plant 410"), and in much the same order
  contained  word for word a part of a longer line said earlier
  restated   the same information in other words, which only a model can see: a Haiku judge.
             It is asked only when what was said since holds most of the line's telling words
             (its meaningful words and numbers, less ones too general to tell news apart), and
             its "restated" stands only when the earlier lines it names hold most of them too.
             A short line that shares a topic word ("Repeat check.") can never stand in for a
             longer one that carries facts it does not.

The first three are decided here in microseconds and cannot fail. The judge fails open: when
it errors or is slow the line is said and the failure is logged as an error.

Greetings and acknowledgements ("I'm here", "What do you need?", "Yes?", "Go ahead", "Anything
else?") and the filler said while an answer is worked out carry no news, so they are not
compared word by word. One is said only when nothing else has been said since his latest
request and in the last two minutes: an answer, an announcement or another greeting has
already told him Sunday is here.

A refused line is silent: nothing about it is said aloud. voice_server.py writes it to the
speech log as {"kind": "repeat"} with the earlier line it matched, and to the main log.

    repeat_guard.py selftest           # the deterministic cases, today's real lines among them
    repeat_guard.py selftest --judge   # also the restatements only the judge can see (Haiku)
    repeat_guard.py check "<new line>" --said "<earlier line>" [--said ...]
"""

import asyncio
import difflib
import itertools
import json
import logging
import os
import re
import sys
import time
from collections import Counter

import barge
import speech_gate

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

JUDGE_MODEL = os.environ.get("VOICE_REPEAT_MODEL", "claude-haiku-4-5-20251001")
JUDGE_TIMEOUT = float(os.environ.get("VOICE_REPEAT_TIMEOUT", "5"))
JUDGE_SPARES = int(os.environ.get("VOICE_REPEAT_SPARES", "2"))   # two calls in a row never wait on a cold start
JUDGE_SPARE_AGE = float(os.environ.get("VOICE_REPEAT_SPARE_AGE", "1200"))
NEWS_WINDOW = float(os.environ.get("VOICE_NEWS_WINDOW", "1800"))        # what an announcement is checked against
GREETING_WINDOW = float(os.environ.get("VOICE_GREETING_WINDOW", "120"))  # a greeting after this much quiet is fine
KEEP_FOR = 6 * 3600                 # an answer keyed on a request this old still sees what was said since
NEAR_EXTRA = 0.25                   # near: the longer line may add this share of meaningful words
NEAR_ORDER = 0.6                    # and the two must line up this well, word for word
CONTAINED = 0.85                    # contained: this share of the line, in runs of 2+ words, inside an earlier one
CONTAINED_MIN = 4                   # meaningful words a line needs before containment alone condemns it
COVER = float(os.environ.get("VOICE_REPEAT_COVER", "0.6"))   # restated: the share of a line's telling
                                    # words what it repeats must hold, before the judge is asked and after

# ---------------------------------------------------------------- one form for every line

# Words that carry no meaning of their own. "not", "no" and "never" are not among them:
# "the site is live" and "the site is not live" are different news.
_STOP = set("""a an the is are was were be been being am to of in on at for and or but it its this that
these those i you he she we they me him her us them my your his our their so do does did as by with from
will would shall have has had just then there here up out about into over than also very really can could
should may might must get got gets getting go goes going one ones all any some there's oh well okay ok yes
yeah sure right now""".split()) | {barge.ORDINAL_MARK}


def _stem(w: str) -> str:
    """Enough to make "sites" and "site", "built" and "builds" one word on both sides."""
    if w.isdigit() or len(w) <= 3:
        return w
    for end, keep in (("ing", 4), ("ies", 3), ("ed", 3), ("es", 3), ("s", 3)):
        if w.endswith(end) and len(w) - len(end) >= keep and not w.endswith("ss"):
            return w[: -len(end)] + ("y" if end == "ies" else "")
    return w


def words(text: str) -> list:
    """The words of a line in one form: lower case, contractions spelled out, punctuation gone,
    and each number as its digits, one token per number however it was written or said -
    the same reading barge.strip_echo uses, so "3:47" and "three forty seven" are both 347."""
    out = []
    for _, toks, _, _ in barge._tokens(text or ""):
        run = ""
        for t in toks:
            if t.isdigit():
                run += t
                continue
            if run:
                out.append(run)
                run = ""
            if t != barge.ORDINAL_MARK:
                out.append(t)
        if run:
            out.append(run)
    return out


# Words that carry meaning but not news: too general to tell one piece of news from another,
# so they never count towards a line being covered by what was said before it. Only coverage
# reads this; the exact, near and contained rules still compare every meaningful word.
_GENERIC_WORDS = """only every each when before after through already more most less much many still again even
too what which who whom how why where because if while until since both other another such same own around
anything anywhere nothing something someone anyone everything everywhere somewhere thing things way lot lots
see saw seen said say says tell told know knew think make made need want like let keep put take come came back
off under"""


class Line:
    """One thing said aloud, or about to be."""
    _ids = itertools.count(1)
    __slots__ = ("id", "at", "text", "words", "content", "nums", "telling", "qid", "device", "kind", "source")

    def __init__(self, text: str, at: float = 0.0, qid=None, device=None, kind: str = "content", source: str = ""):
        self.id = next(Line._ids)
        self.at = at or time.time()
        self.text = " ".join((text or "").split())
        self.words = words(self.text)
        self.nums = Counter(w for w in self.words if w.isdigit())
        self.content = Counter(_stem(w) for w in self.words if not w.isdigit() and w not in _STOP)
        # What it tells him: its meaningful words less the general ones, and its numbers.
        self.telling = {w for w in self.content if w not in _GENERIC} | set(self.nums)
        self.qid, self.device, self.kind, self.source = qid, device, kind, source

    def __repr__(self):
        return f"Line({self.text!r}, {self.kind}, {self.qid})"


def _contained(short: Line, long: Line) -> bool:
    """short is word for word a part of long: every meaningful word of it is there, and most of
    it lines up in runs of two or more words, in order. One word swapped is not containment:
    "the plan for the Dallas site is final" is not inside "the plan for the Austin site is final"."""
    if sum(short.content.values()) < CONTAINED_MIN or short.nums - long.nums or short.content - long.content \
            or len(short.words) > len(long.words):
        return False
    blocks = difflib.SequenceMatcher(None, short.words, long.words, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 2) >= CONTAINED * len(short.words)


def compare(new: Line, old: Line) -> str:
    """"exact", "near", "contained" or "" for a new line against one said earlier."""
    if new.words and new.words == old.words:
        return "exact"
    if new.nums == old.nums and (new.content or old.content):
        short, long = sorted((new.content, old.content), key=lambda c: sum(c.values()))
        extra = sum((long - short).values())
        if not (short - long) and extra <= int(NEAR_EXTRA * sum(long.values())) and \
                difflib.SequenceMatcher(None, new.words, old.words, autojunk=False).ratio() >= NEAR_ORDER:
            return "near"
    if _contained(new, old):
        return "contained"
    return ""


_GENERIC = {_stem(w) for w in _GENERIC_WORDS.split()}


def covered(new: Line, earlier: list) -> float:
    """The share of the new line's telling words that the earlier lines hold between them.
    A line can only restate what was said when this is most of it: "Repeat check." holds two
    of the nineteen in "My answers never went through the repeat check, only announcements
    did, so I now check every line in code before it reaches the speaker", and the facts in
    the other seventeen were never said. A line with no telling words is never covered."""
    if not new.telling:
        return 0.0
    have = set().union(*(x.telling for x in earlier)) if earlier else set()
    return len(new.telling & have) / len(new.telling)


# ---------------------------------------------------------------- greetings and his requests

# Sounds and the wake word, which ask for nothing. A line of his made only of these is not a
# request, so it does not move the starting point and never earns a greeting of its own.
_NOT_ASKING = set("sunday hey hi hello oh ohh um umm uh uhh hmm hm mm mmm mhm ah ahh er erm yo so well like huh".split())


def has_content(heard: str) -> bool:
    """Whether what he said asks for anything: anything beyond the wake word and sounds."""
    return any(w not in _NOT_ASKING for w in words(heard))


_NAMES = tuple(dict.fromkeys(w for w in (str(mtoa_config.get("user.name", "")).split()[:1]
                                          + str(mtoa_config.get("assistant.name", "Sunday")).split()[:1])
                              for w in [w.lower()] if w))
_GREETING_LEAD = set("yes yeah yep yup hi hey hello sure okay ok oh right alright so well mhm".split()) | set(_NAMES)
_GREETINGS = {" ".join(words(p)) for p in """i'm here|i'm still here|still here|right here|here|i'm listening|listening|
i'm all ears|all ears|what do you need|what do you need me to do|what can i do for you|what can i help with|
what can i help you with|how can i help|how can i help you|how can i help you today|what's up|what is it|
what would you like|what would you like me to do|what do you want|what else|anything else|is there anything else|
what else can i do for you|anything else i can help with|anything else you need|go ahead|go on|i'm ready|
ready when you are|you called|you rang|did you need something|do you need something|need something|
what did you need|what can i do|at your service|talk to me|i'm back|i'm here for you|yes i'm here""".split("|")}


def is_greeting(text: str) -> bool:
    """A line that only says Sunday is here or invites him to talk: "I'm here", "What do you
    need?", "Yes?", "Hey, go ahead". "Yes." answering a question is not one."""
    parts = [p for p in re.split(r"[,.;:!?—-]+\s*", text or "") if p.strip()]
    if not parts:
        return False
    for i, part in enumerate(parts):
        ws = words(part)
        lead = []
        while ws and ws[0] in _GREETING_LEAD:
            lead.append(ws.pop(0))
        while ws and ws[-1] in _NAMES:
            ws.pop()
        if ws:
            if " ".join(ws) not in _GREETINGS:
                return False
        elif not (text.rstrip().endswith("?") or {"hi", "hey", "hello"} & set(lead)
                  or len(parts) > 1 and lead):
            return False
    return True


# ---------------------------------------------------------------- what has been said

class Guard:
    """Every line said aloud lately and every request he made, and the rule over them."""

    def __init__(self):
        self.lines: list = []        # Line, oldest first
        self.requests: list = []     # (when, what he said) for each request with words in it
        self.version = 0             # moves whenever what counts as said changes
        self.judged: dict = {}       # (line words, ids it was judged against) -> matched text or ""

    # what he asked

    def heard(self, at: float, text: str):
        if has_content(text):
            self.requests.append((at, " ".join((text or "").split())))
            self.requests = [r for r in self.requests if r[0] > time.time() - KEEP_FOR][-500:]

    def last_request(self, before: float | None = None) -> float | None:
        """When he last asked for something, at or before `before`."""
        for at, _ in reversed(self.requests):
            if before is None or at <= before + 0.5:
                return at
        return None

    # what was said

    def said(self, line: Line):
        self.lines.append(line)
        now = time.time()
        self.lines = [x for x in self.lines if now - x.at < KEEP_FOR][-2000:]
        self.version += 1

    def settle(self, qid, device, played: list):
        """An utterance has ended on one speaker: anything of it that never finished playing
        was not heard, so it no longer counts as said. Its reservation (the lines recorded
        with no speaker when it was sent out) goes too: what played is recorded per speaker."""
        left = Counter(" ".join(p.split()) for p in played)
        keep = []
        for x in self.lines:
            if x.qid == qid and x.device == "" and qid is not None:
                self.version += 1
                continue
            if x.qid == qid and x.device == device:
                if left[x.text] > 0:
                    left[x.text] -= 1
                else:
                    self.version += 1
                    continue
            keep.append(x)
        self.lines = keep

    # the rule

    def earlier(self, line: Line, since: float) -> list:
        """Content said since `since` that this line is compared with: anything another
        utterance said, and what this one already said on the same speaker. The same
        announcement on two speakers at once is one line, not a repeat."""
        return [x for x in self.lines if x.at >= since and x.kind == "content" and x is not line
                and (x.qid != line.qid or x.device == line.device)]

    def decide(self, line: Line, since: float, greet_since: float) -> dict:
        """The deterministic verdict on one line. {"say": False, "rule", "matched"} refuses it;
        {"say": True, "ask": [...]} says it unless the judge finds it restates one of "ask"."""
        if line.kind in ("greeting", "filler"):
            prior = [x for x in self.lines if x.at >= greet_since and x.qid != line.qid]
            if prior:
                return {"say": False, "rule": "greeting", "matched": prior[-1]}
            return {"say": True, "ask": []}
        earlier = self.earlier(line, since)
        for x in reversed(earlier):
            rule = compare(line, x)
            if rule:
                return {"say": False, "rule": rule, "matched": x}
        # A line can also sit inside an earlier answer said as several sentences.
        whole: dict = {}
        for x in earlier:
            if x.qid != line.qid:
                whole.setdefault((x.qid, x.device), []).append(x)
        for parts in whole.values():
            if len(parts) > 1:
                joined = Line(" ".join(p.text for p in parts), parts[0].at, parts[0].qid, parts[0].device)
                if _contained(line, joined):
                    return {"say": False, "rule": "contained", "matched": joined}
        # The judge is asked only when what was said since holds most of what this line tells:
        # a line carrying facts nobody said is new, however many topic words it shares.
        ask = [x for x in earlier if x.qid != line.qid and line.telling & x.telling]
        return {"say": True, "ask": ask if covered(line, ask) >= COVER else []}

    def cached(self, line: Line, against: list):
        key = (tuple(line.words), tuple(x.id for x in against))
        return self.judged.get(key)

    def remember(self, line: Line, against: list, matched: str):
        self.judged[(tuple(line.words), tuple(x.id for x in against))] = matched
        if len(self.judged) > 400:
            for k in list(self.judged)[:200]:
                self.judged.pop(k, None)

    def seed(self, records: list):
        """Rebuild the record from the speech log after a restart, so a restart is never a
        reason to say again what he heard a minute before it."""
        now = time.time()
        for r in records:
            at = r.get("at", 0)
            if now - at > KEEP_FOR:
                continue
            if r.get("kind") == "heard" and not r.get("discarded") and not r.get("command"):
                self.heard(at, r.get("text", ""))
            elif r.get("kind") == "said":
                played = (r.get("sentences") or [])[: r.get("played", 0)]
                for i, s in enumerate(played):
                    kind = "greeting" if is_greeting(s) else "content"
                    self.lines.append(Line(s, at + i * 0.01, r.get("qid"), r.get("device"), kind, r.get("source", "")))
                for s in r.get("filler_played") or []:
                    self.lines.append(Line(s, at, r.get("qid"), r.get("device"), "filler", r.get("source", "")))
        self.lines.sort(key=lambda x: x.at)
        self.requests.sort()
        self.version += 1


# ---------------------------------------------------------------- the judge, for restatements

JUDGE_PROMPT = _render("""You stop a voice assistant, {assistant}, from telling {user} again something {he} has already heard. ALREADY SAID is what {assistant} has said aloud to {him}, oldest first. NEW is numbered sentences {assistant} is about to say.

A NEW sentence is a REPEAT only when every fact in it is already in ALREADY SAID: every claim, figure, name, place, time, status, reason, plan and promise, in the same or different words. A figure that only works out what {he} already heard counts as heard (if {he} heard "about thirty in all" and "eight are already built", then "twenty two more" tells {him} nothing new).

A NEW sentence is NEW when it tells {him} anything ALREADY SAID does not: a different subject or piece of work, a different number, name, place, time or result, a new reason, detail, explanation or next step. Sharing words, a topic, a device or a feature with an earlier line does not make it a repeat. A short ALREADY SAID line, a test phrase or a line that only names the topic can never make a longer NEW sentence a repeat: "Kitchen lights." does not repeat "The kitchen lights now turn off at eleven on weeknights and at midnight on Fridays". "I am fixing the wake word after I talk" is NEW after "I am fixing coughs waking me", and "plant 410 has no order" is NEW after "plant 401 has no order". When unsure, it is NEW.

Reply with one JSON object and nothing else: {"repeats": [{"n": <NEW sentence number>, "of": ["<every ALREADY SAID line that together hold all of it, copied exactly>"]}]}, with an empty list when every sentence is NEW.""")


class RepeatJudge(speech_gate.Judge):
    """Haiku processes started ahead of time and used once each, like the speech gate's, so
    the rare call costs about a second rather than a cold start."""

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

    def close(self):
        for _, p in self.spares:
            if p.returncode is None:
                p.kill()
        self.spares.clear()


judge = RepeatJudge()


def _case(new: list, earlier: list) -> str:
    said = "\n".join(f"- ({time.strftime('%-I:%M %p', time.localtime(x.at))}) {x.text}" for x in earlier[-40:])
    return f"ALREADY SAID:\n{said}\n\nNEW:\n" + "\n".join(f"{i}. {x.text}" for i, x in enumerate(new, 1))


def _parse(out: str, n: int) -> dict:
    """{index of a NEW sentence: [the ALREADY SAID lines it names]}."""
    start = out.find("{")
    if start < 0:
        raise ValueError(f"no JSON in {out[:120]!r}")
    v, _ = json.JSONDecoder().raw_decode(out[start:])
    found = {}
    for r in v.get("repeats") or []:
        k = int(r.get("n", 0)) if isinstance(r, dict) else int(r)
        of = r.get("of", []) if isinstance(r, dict) else []
        of = [of] if isinstance(of, str) else of if isinstance(of, list) else []
        if 1 <= k <= n:
            found[k - 1] = [str(x)[:400] for x in of if str(x).strip()]
    return found


def cited(quotes: list, ask: list) -> list:
    """The earlier lines the judge named, read as whole utterances: a line it quotes, or one
    a quote sits inside, brings in the rest of what was said with it."""
    hit = set()
    for q in quotes:
        ql = " ".join(q.lower().split())
        best = max(ask, key=lambda x: difflib.SequenceMatcher(None, x.text.lower(), ql).ratio(), default=None)
        for x in ask:
            if ql and (ql in x.text.lower() or x.text.lower() in ql):
                hit.add(x.qid)
        if best is not None and difflib.SequenceMatcher(None, best.text.lower(), ql).ratio() >= 0.6:
            hit.add(best.qid)
    return [x for x in ask if x.qid in hit]


async def restated(new: list, earlier: list) -> dict:
    """{"repeats": {index into new: [the earlier lines it names]}, "by", "ms", "why"}. Fails open:
    on any error or timeout nothing is a repeat, and the failure is logged as an error."""
    t0 = time.monotonic()
    try:
        out = await asyncio.wait_for(judge.ask(_case(new, earlier)), JUDGE_TIMEOUT)
        found = _parse(out, len(new))
        return {"repeats": found, "by": "judge", "ms": int((time.monotonic() - t0) * 1000), "why": ""}
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        log.error("REPEAT JUDGE FAILED OPEN after %d ms (%s: %s); saying unchecked for restatement: %s",
                  ms, type(e).__name__, e, " | ".join(x.text for x in new)[:200])
        return {"repeats": {}, "by": "error", "ms": ms, "why": f"{type(e).__name__}: {e}"[:300]}


async def vet(guard: Guard, lines: list, since: float, greet_since: float, use_judge: bool = True) -> list:
    """The verdict on each of several lines about to be said in this order: a dict per line,
    {"say": True} or {"say": False, "rule", "matched", "by", "ms"}. Deterministic rules first;
    one judge call covers every line that shares enough with something said, and its
    verdicts are kept so the same question is never asked twice."""
    out = []
    pending = []          # (index, line, what it is compared with) for the judge
    for i, line in enumerate(lines):
        d = guard.decide(line, since, greet_since)
        if not d["say"]:
            out.append({"say": False, "rule": d["rule"], "matched": d["matched"], "by": "code", "ms": 0})
            continue
        out.append({"say": True, "by": "code", "ms": 0})
        if d["ask"]:
            hit = guard.cached(line, d["ask"])
            if hit is None:
                pending.append((i, line, d["ask"]))
            elif hit:
                out[i] = {"say": False, "rule": "restated", "matched": Line(hit, 0), "by": "judge", "ms": 0}
    if pending and use_judge:
        against = sorted({x.id: x for _, _, ask in pending for x in ask}.values(), key=lambda x: x.at)
        v = await restated([line for _, line, _ in pending], against)
        for k, (i, line, ask) in enumerate(pending):
            quotes = v["repeats"].get(k)
            if quotes is None:
                if v["by"] == "judge":
                    guard.remember(line, ask, "")
                out[i] = {"say": True, "by": v["by"], "ms": v["ms"], "why": v["why"]}
                continue
            # The judge's "restated" stands only when the lines it names hold most of what this
            # line tells him. Otherwise it is new information, and it is said.
            olds = cited(quotes, ask)
            share = covered(line, olds)
            if share < COVER:
                guard.remember(line, ask, "")
                log.info("repeat judge overruled: it named %s, which holds %d%% of what this line tells, "
                         "under %d%%; saying it: %s", [x.text[:60] for x in olds] or quotes[:2],
                         share * 100, COVER * 100, line.text[:160])
                out[i] = {"say": True, "by": "judge", "ms": v["ms"], "overruled": True,
                          "why": f"named lines hold {share:.0%} of it"}
                continue
            old = Line(" ".join(x.text for x in olds), olds[-1].at, olds[-1].qid, olds[-1].device) \
                if len(olds) > 1 else olds[0]
            guard.remember(line, ask, old.text)
            out[i] = {"say": False, "rule": "restated", "matched": old, "by": "judge", "ms": v["ms"],
                      "covered": round(share, 2)}
    return out


def since_for(guard: Guard, source: str, now: float, question: str = "", asked_at: float | None = None,
              anchor: float | None = None) -> float:
    """Where the comparison starts for a line from this source: what was said aloud from this
    moment on is what it must not repeat.

      reply, followup  the request it answers; an answer to "Sunday" or "hey" alone answers
                       his request before it
      anchor given     /say answer=1 (his latest request, or ?asked=) and /late (when asked)
      say, relay       the last half hour: an announcement answers nothing he asked
      anything else    now: a command's confirmation only refuses what is said at the same moment"""
    if anchor is not None:
        return anchor
    if source in ("reply", "followup") and asked_at:
        return asked_at if has_content(question) else (guard.last_request(asked_at) or asked_at - NEWS_WINDOW)
    if source in ("say", "relay"):
        return now - NEWS_WINDOW
    return now


def greeting_since(guard: Guard, now: float) -> float:
    """A greeting is refused when anything was said from here on: since his latest request,
    and within the last two minutes."""
    return max(guard.last_request(now) or 0.0, now - GREETING_WINDOW)


# ---------------------------------------------------------------- selftest

def _script(guard: Guard, events: list, t0: float):
    """Play a conversation into a guard: ("heard", s, text) or ("said", s, text, qid[, kind[, device]])."""
    for e in events:
        if e[0] == "heard":
            guard.heard(t0 + e[1], e[2])
        else:
            text, qid = e[2], e[3]
            kind = e[4] if len(e) > 4 else ("greeting" if is_greeting(text) else "content")
            guard.said(Line(text, t0 + e[1], qid, e[5] if len(e) > 5 else "cube", kind))


# Each case: name, the conversation so far (seconds from its start), then what is about to be
# said: (mode, seconds, qid, question or asked, [lines]), and what must happen to each line -
# "say", a rule the code must refuse it on, "judge" (the code must leave it to the judge), or
# with --judge, "restated" (the judge must refuse it) and "new" (the judge must let it through).
T = "Thirty eight preview letters went out today."
CASES = [
    # ---- today's real lines, 2026-10-06
    ("11:13 'I'm here. What do you need?' after the time was given, to 'Hey, oh, oh.'",
     [("heard", 0, "What time is it?"), ("said", 1, "Let me check the current time.", "q8", "filler"),
      ("said", 3, "It's eleven thirteen in the morning.", "q8"), ("heard", 13, "Hey, oh, oh.")],
     ("reply", 13.5, "q9", "Hey, oh, oh.", [("Got it, checking now.", "filler"), "I'm here.", "What do you need?"]),
     ["greeting", "greeting", "greeting"]),
    ("22:25 'I'm here. What do you need?' to 'Sunday.' said over its own restated answer",
     [("heard", 0, "I don't think you answered me."), ("said", 1, "You're right, checking on that now.", "q37", "filler"),
      ("said", 9, "For tomorrow I expect about thirty previews built and sent.", "q39"),
      ("said", 30, "About thirty tomorrow.", "q37"), ("heard", 43, "Sunday.")],
     ("reply", 43.2, "q43", "Sunday.", [("I'm looking into that for you.", "filler"), "I'm here.", "What do you need?"]),
     ["greeting", "greeting", "greeting"]),
    ("22:25 the answer to 'I don't think you answered me' restating the /say that answered it first",
     [("heard", 0, "Sunday. How many previews do you think will get through tomorrow?"),
      ("said", 35, "On the previews for tomorrow, I'm still working out a solid number and will tell you as soon as "
                   "I have it.", "q34"),
      ("heard", 213, "I don't think you answered me."),
      ("said", 214, "You're right, checking on that now.", "q37", "filler"),
      ("said", 222, "For tomorrow I expect about thirty previews built and sent.", "q39"),
      ("said", 225, "The eight already built go out at eight, about fifteen of the forty nine coming due in insurance, "
                    "accounting, title, HVAC and roofing should pass the script checks, and the morning run builds "
                    "about ten of the seventy six parked ones, all under the fifty a day sending cap.", "q39"),
      ("said", 236, "The hundred and thirty two in other trades like real estate, flooring and plumbing still wait, "
                    "because the script cannot build those yet and the morning run only takes parked ones.", "q39")],
     ("reply", 246, "q37", "I don't think you answered me.",
      ["About thirty tomorrow.",
       "Eight are already built and go out at eight, and I expect around twenty-two more to get built and sent "
       "through the day."]),
     ["restated", "restated"]),
    ("19:00 plant 401: an answer=1 /say restating the two said before it, with no new request",
     [("heard", 0, "What did the supplier say about tomorrow's concrete order for plant 401?"),
      ("said", 290, "On plant 401, I checked your email, texts and Teams, and the supplier has sent nothing about the "
                    "concrete order for tomorrow.", "q16"),
      ("said", 296, "The only cement texts today were the end of day silo counts from plants 410, 455 and 461.", "q16"),
      ("said", 435, "Andre at Third Party Cement has not texted you since August of last year.", "q19")],
     ("answer", 532, "q20", "",
      ["On plant 401, the supplier has not said anything, so the order for tomorrow is not confirmed anywhere I can see.",
       "I checked your email, texts, Teams and calls, and Andre at Third Party Cement has not texted you since August "
       "of last year."]),
     ["restated", "restated"]),
    ("the same /say line sent twice", [("said", 0, "Test one.", "s1")],
     ("say", 5, "s2", "", ["Test one."]), ["exact"]),
    ("a short line again in other punctuation and numbers", [("said", 0, "It's 11:13 in the morning.", "s1")],
     ("say", 60, "s2", "", ["It is eleven thirteen in the morning!"]), ["exact"]),
    ("a sentence of an earlier announcement said again on its own",
     [("said", 0, "Preview visitor tracking is live and checked. I record which sections prospects read, what they "
                  "tap and whether they come back, never what they type.", "s1")],
     ("say", 120, "s2", "", ["I record which sections prospects read, what they tap and whether they come back."]),
     ["contained"]),
    ("a late answer that already reached him since he asked",
     [("heard", 0, "How many letters went out today?"), ("said", 5, "I'm still checking that.", "q1"),
      ("said", 40, T, "s1")],
     ("late", 70, "l1", 1.0, [T]), ["exact"]),
    ("an answer in near words: the same words give or take one",
     [("heard", 0, "Is the Smyrna site live?"), ("said", 4, "The Smyrna Tools site is live.", "s1")],
     ("reply", 6, "q2", "Is the Smyrna site live?", ["The Smyrna site is live."]), ["near"]),
    ("a greeting after an announcement, with nothing asked since",
     [("heard", 0, "Sunday, what's on today?"), ("said", 4, "Nothing is on your calendar today.", "q1"),
      ("said", 60, "The Live tab fix is live.", "s1"), ("heard", 80, "Sunday.")],
     ("reply", 81, "q2", "Sunday.", ["Yes?"]), ["greeting"]),
    ("an answer to a bare wake that repeats the answer before it",
     [("heard", 0, "What time is it?"), ("said", 2, "It's eleven thirteen in the morning.", "q1"),
      ("heard", 12, "Sunday.")],
     ("reply", 12.5, "q2", "Sunday.", ["It's eleven thirteen in the morning."]), ["exact"]),
    # ---- what must still be said
    ("the same question asked twice is answered twice",
     [("heard", 0, "What time is it?"), ("said", 2, "It's eleven thirteen in the morning.", "q1"),
      ("heard", 30, "What time is it?")],
     ("reply", 31, "q2", "What time is it?", ["It's eleven thirteen in the morning."]), ["say"]),
    ("answer=1 when he asked again after hearing it",
     [("heard", 0, "How many letters went out?"), ("said", 20, T, "s1"), ("heard", 60, "How many letters went out?")],
     ("answer", 75, "s2", "", [T]), ["say"]),
    ("a late answer after 'still checking'",
     [("heard", 0, "Sunday. How many previews do you think will get through tomorrow?"),
      ("said", 35, "On the previews for tomorrow, I'm still working out a solid number and will tell you as soon as "
                   "I have it.", "q34")],
     ("late", 220, "l1", 0.5, ["On the previews for tomorrow, I expect about thirty built and sent."]), ["say"]),
    ("a late answer stands alone after other talk",
     [("heard", 0, "Did the supplier confirm plant 401?"), ("said", 3, "I'm still checking that.", "q1"),
      ("heard", 30, "What time is it?"), ("said", 32, "It's eleven thirteen.", "q2")],
     ("late", 90, "l1", 1.0, ["On plant 401, the supplier has not confirmed tomorrow's order."]), ["say"]),
    ("different answers that share words",
     [("heard", 0, "Is the Smyrna site up?"), ("said", 3, "The Smyrna site is live and the contact form works.", "q1"),
      ("heard", 20, "And the Dallas one?")],
     ("reply", 22, "q2", "And the Dallas one?", ["The Dallas site is live, but the contact form is broken."]),
     ["say"]),
    ("different news that shares words, as an announcement",
     [("said", 0, "The Smyrna site is live and the contact form works.", "s1")],
     ("say", 100, "s2", "", ["The Smyrna site is down and the contact form is broken."]), ["judge"]),
    ("one word swapped in a long line is different news",
     [("said", 0, "The plan for the Austin site is final and ships on Friday.", "s1")],
     ("say", 60, "s2", "", ["The plan for the Dallas site is final and ships on Friday."]), ["judge"]),
    ("numbers that differ: plant 401 and plant 410",
     [("said", 0, "Plant 401 has no concrete order for tomorrow.", "s1")],
     ("say", 60, "s2", "", ["Plant 410 has no concrete order for tomorrow."]), ["judge"]),
    ("the 22:31 announcement refused by the old news check: different work, shared words",
     [("said", 0, "I am on two fixes now.", "s1"),
      ("said", 3, "The first makes sure I never say something again that you already heard, including short lines "
                  "like I am here, what do you need.", "s1"),
      ("said", 9, "The second makes coughs and other sounds that are not words stop waking me.", "s1")],
     ("say", 147, "s2", "", ["I am also fixing the few seconds after I stop talking when I do not answer my name, so I "
                             "hear you the moment I finish without waking on my own voice."]), ["say"]),
    ("a greeting after a long quiet", [("heard", 0, "What time is it?"), ("said", 2, "It's nine.", "q1"),
                                       ("heard", 600, "Sunday.")],
     ("reply", 601, "q2", "Sunday.", ["I'm here.", "What do you need?"]), ["say", "say"]),
    ("a greeting that answers his question", [("heard", 0, "Sunday, are you there?")],
     ("reply", 1, "q1", "Sunday, are you there?", [("Checking now.", "filler"), "I'm here."]), ["say", "say"]),
    ("a short answer after its own filler", [("heard", 0, "Is the Pi up?"), ("said", 1.2, "Let me check.", "q1", "filler")],
     ("reply", 3, "q1", "Is the Pi up?", ["Yes.", "Anything else?"]), ["say", "say"]),
    ("'Yes.' to a second question", [("heard", 0, "Is the site live?"), ("said", 2, "Yes.", "q1"),
                                     ("heard", 10, "Is the Pi up?")],
     ("reply", 12, "q2", "Is the Pi up?", ["Yes."]), ["say"]),
    ("one announcement on two speakers at once is said on both",
     [("said", 0, "The Live tab fix is live.", "s1", "content", "cube")],
     ("say", 0.1, "s1", "", [("The Live tab fix is live.", "content", "mac")]), ["say"]),
    ("a speaker test with the same opening but a different number",
     [("said", 0, "This is the third speaker test. Pine trees keep their needles all winter.", "s1")],
     ("say", 200, "s2", "", ["This is the fourth speaker test. Pine trees keep their needles all winter."]), ["judge"]),
    ("lines older than the half hour are not compared for an announcement",
     [("said", 0, "The Live tab fix is live.", "s1")],
     ("say", 1900, "s2", "", ["The Live tab fix is live."]), ["say"]),
    # ---- 23:03: a two-word test line stood in for a sentence of news, and the judge dropped it.
    ("23:03 'Repeat check.' must not stand in for the news about the repeat check",
     [("said", 0, "Repeat check.", "s1")],
     ("say", 202, "s2", "", ["My answers never went through the repeat check, only announcements did, so I now check "
                             "every line in code before it reaches the speaker and stay silent when you already "
                             "heard it."]), ["say"]),
    ("23:03 the same sentence against everything said in the half hour before it",
     [("heard", 0, "You're pulling up the blue outline around my screen even when words are not going on."),
      ("said", 1, "I'm on it.", "q113"),
      ("said", 3, "I'm finding what broke the blue outline fix and locking it down so it can't come back, and I'll "
                  "tell you when it's verified.", "q113"),
      ("heard", 43, "I want you to make it to where whenever I am playing a game on my Windows computer the overlay "
                    "stays off."),
      ("said", 44, "The overlay will stay off while you game on Windows unless you toggle it on, and I'll tell you "
                   "when it works.", "q116"),
      ("said", 631, "Two.", "s127"), ("said", 632, "That is the second check.", "s127"),
      ("said", 706, "Four.", "s133"), ("said", 707, "That is the fourth check.", "s133"),
      ("said", 743, "Six.", "s135"), ("said", 744, "That is the sixth check.", "s135"),
      ("said", 860, "Repeat check.", "s1"), ("said", 871, "This is my new voice.", "s3"),
      ("said", 893, "Guard live.", "s4"),
      ("said", 1062, "I found why I repeated myself.", "s5")],
     ("say", 1062.5, "s5", "", ["My answers never went through the repeat check, only announcements did, so I now "
                                "check every line in code before it reaches the speaker and stay silent when you "
                                "already heard it.", "If you ask again, I still answer."]), ["say", "say"]),
    ("a short test line sharing a topic word with news: the guard",
     [("said", 0, "Guard live.", "s1")],
     ("say", 120, "s2", "", ["The guard now checks every line before it reaches the speaker, so a repeat is never "
                             "said aloud."]), ["say"]),
    ("a short test line sharing a topic word with news: the first test",
     [("said", 0, "Test one.", "s1")],
     ("say", 300, "s2", "", ["The first test of the new speaker volume ran this morning, and the cube played every "
                             "sentence clearly."]), ["say"]),
    ("a short test line sharing a topic word with news: the voice check",
     [("said", 0, "Voice check.", "s1")],
     ("say", 90, "s2", "", ["Your voice check finished, and I now know your voice well enough to ignore the TV."]),
     ["say"]),
    ("a short status sharing its words with a longer one that adds facts",
     [("said", 0, "The site is live.", "s1")],
     ("say", 400, "s2", "", ["The site is live on the new host, and the contact form now sends to your sales "
                             "inbox."]), ["say"]),
]

# What only the judge can tell, run with --judge: for each line the code handed over,
# "restated" (it must be refused), "new" (it must be said) or "either".
JUDGED = {
    "22:25 the answer to 'I don't think you answered me' restating the /say that answered it first":
        ["restated", "restated"],
    # The second line adds that calls were checked too; the first is the same news outright.
    "19:00 plant 401: an answer=1 /say restating the two said before it, with no new request": ["restated", "either"],
    "different news that shares words, as an announcement": ["new"],
    "numbers that differ: plant 401 and plant 410": ["new"],
    "one word swapped in a long line is different news": ["new"],
    "a speaker test with the same opening but a different number": ["new"],
}

# The judge's "restated" stands only when the lines it names hold most of what the new line
# tells. These run with a judge that answers on cue, so the rule is tested without a model.
LONG = ("My answers never went through the repeat check, only announcements did, so I now check every line in "
        "code before it reaches the speaker and stay silent when you already heard it.")
OVERRULE = [
    # (what was said, the new line, what the judge names, what must happen)
    (["Repeat check.", "Every answer now goes through the repeat check in code, and I stay silent when you "
                       "already heard a line, because announcements were the only lines it read before."],
     LONG, ["Repeat check."], "say"),
    (["Repeat check.", "Every answer now goes through the repeat check in code, and I stay silent when you "
                       "already heard a line, because announcements were the only lines it read before."],
     LONG, ["Every answer now goes through the repeat check in code, and I stay silent when you already heard a "
            "line, because announcements were the only lines it read before."], "restated"),
    (["Repeat check.", "Every answer now goes through the repeat check in code, and I stay silent when you "
                       "already heard a line, because announcements were the only lines it read before."],
     LONG, ["something it never said"], "say"),
]


async def selftest(use_judge: bool) -> int:
    bad = 0
    t0 = time.time() - 4000
    if use_judge:
        # In service the spares are started minutes ahead; give these the same head start, so
        # the cases time the judge and not the start of its process.
        await judge.fill()
        await asyncio.sleep(6)
    for name, events, (mode, at, qid, extra, lines), expect in CASES:
        guard = Guard()
        _script(guard, events, t0)
        now = t0 + at
        if mode == "reply":
            asked_at = max(t0 + e[1] for e in events if e[0] == "heard" and e[2] == extra)
            since = since_for(guard, "reply", now, extra, asked_at)
        elif mode == "answer":
            since = since_for(guard, "say", now, anchor=guard.last_request(now) or now - NEWS_WINDOW)
        elif mode == "late":
            since = since_for(guard, "late", now, anchor=t0 + extra)
        else:
            since = since_for(guard, mode, now)
        greet = greeting_since(guard, now)
        built = []
        for x in lines:
            text, kind, device = (x + ("cube",))[:3] if isinstance(x, tuple) else \
                (x, "greeting" if is_greeting(x) else "content", "cube")
            built.append(Line(text, now, qid, device, kind))
        code = []
        for line in built:
            d = guard.decide(line, since, greet)
            code.append(d["rule"] if not d["say"] else "judge" if d["ask"] else "say")
        for i, (got, want) in enumerate(zip(code, expect)):
            # "restated" and "new" are the judge's to decide; the code must at least hand them over.
            ok = got == want or (want in ("restated",) and got == "judge")
            bad += not ok
            print(f"{'PASS' if ok else 'FAIL'}  code   expect {want:<9} got {got:<9} {name[:70]}  [{built[i].text[:50]}]")
        if use_judge and name in JUDGED:
            v = await vet(guard, built, since, greet)
            for i, line in enumerate(built):
                if code[i] != "judge":
                    continue
                want = JUDGED[name][i]
                got = "restated" if not v[i]["say"] and v[i].get("rule") == "restated" else \
                    "new" if v[i]["say"] and v[i]["by"] == "judge" else "error" if v[i].get("by") == "error" else "?"
                ok = got == want or (want == "either" and got in ("restated", "new"))
                bad += not ok
                print(f"{'PASS' if ok else 'FAIL'}  judge  expect {want:<9} got {got:<9} {v[i]['ms']:>5} ms  "
                      f"{line.text[:60]}" + (f"\n        of: {v[i]['matched'].text[:100]}" if not v[i]["say"] else ""))
    real_ask = judge.ask
    try:
        for said, new, names, expect in OVERRULE:
            async def cue(case, names=names):
                return json.dumps({"repeats": [{"n": 1, "of": names}]})
            judge.ask = cue
            guard = Guard()
            _script(guard, [("said", k, x, f"s{k}") for k, x in enumerate(said)], t0)
            line = Line(new, t0 + 60, "new", "cube")
            v = (await vet(guard, [line], t0 - 1, t0 - 1))[0]
            got = "say" if v["say"] else v["rule"]
            ok = got == expect
            bad += not ok
            print(f"{'PASS' if ok else 'FAIL'}  judge names {names[0][:30]!r:<34} expect {expect:<9} got {got:<9} "
                  f"{v.get('why', '') or v.get('covered', '')}")
    finally:
        judge.ask = real_ask
    for heard, expect in (("Sunday.", False), ("Hey, oh, oh.", False), ("Uh", False), ("Sunday. Sunday.", False),
                          ("Sunday, are you there?", True), ("Yes.", True), ("Okay.", True), ("Stop.", True),
                          ("I don't think you answered me.", True)):
        ok = has_content(heard) == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  asks for something {expect!s:<5} {heard}")
    for text, expect in (("I'm here.", True), ("What do you need?", True), ("Yes?", True), ("Go ahead.", True),
                         ("Anything else?", True), ("Hey %s, what's up?" % _NAMES[0].title(), True), ("I'm here, what do you need?", True),
                         ("Yes.", False), ("Okay.", False), ("Got it.", False), ("It's eleven thirteen.", False),
                         ("Yes, I'm here.", True), ("Here is the plan.", False), ("I'm here at the plant.", False)):
        ok = is_greeting(text) == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  greeting {expect!s:<5} {text}")
    for a, b, expect in (("plant 401", "plant four oh one", True), ("3:47", "three forty seven", True),
                         ("twenty-two", "22", True), ("I'm here", "I am here", True), ("plant 401", "plant 410", False)):
        ok = (words(a) == words(b)) == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  one form {expect!s:<5} {a!r} {b!r} -> {words(a)} {words(b)}")
    if use_judge:
        judge.close()
    print(f"{'all passed' if not bad else f'{bad} FAILED'}")
    return bad


async def _check_cli(argv):
    new, said = argv[0], []
    i = 1
    while i < len(argv) - 1:
        if argv[i] == "--said":
            said.append(argv[i + 1])
        i += 2
    guard = Guard()
    t0 = time.time() - 60
    for k, s in enumerate(said):
        guard.said(Line(s, t0 + k, f"old{k}", "cube"))
    lines = [Line(s, time.time(), "new", "cube", "greeting" if is_greeting(s) else "content")
             for s in barge.sentences(new)]
    v = await vet(guard, lines, t0 - 1, t0 - 1)
    for line, r in zip(lines, v):
        print(json.dumps({"text": line.text, "say": r["say"], "rule": r.get("rule", ""), "by": r.get("by"),
                          "ms": r.get("ms"), "matched": r["matched"].text if not r["say"] else ""}))
    judge.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sys.argv[1:2] == ["selftest"]:
        sys.exit(1 if asyncio.run(selftest("--judge" in sys.argv)) else 0)
    elif sys.argv[1:2] == ["check"] and len(sys.argv) > 2:
        asyncio.run(_check_cli(sys.argv[2:]))
    else:
        sys.exit(__doc__)
