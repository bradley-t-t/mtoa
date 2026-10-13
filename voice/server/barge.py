#!/usr/bin/env python3
"""Barge-in: what to do with what he said while Sunday was talking.

A wake or follow-up while Sunday talks never cuts it off by itself. The device keeps
playing, ducked, while he speaks, and once his words are transcribed this decides:

  stop      an explicit stop ("stop", "quiet", "hold on", "never mind", ...): silence, no answer
  redirect  a correction of the very thing being said ("no, I meant...", "not that one",
            "wrong site"), or a stop word followed by a new ask: cut it off and answer him
  continue  anything else is a separate request: let the current speech finish, then
            answer this after it

Heard with no wake word (the open ear while it talks), he may not be talking to Sunday at
all, and the mic also writes down Sunday's own voice a little wrong, so the bar is higher:
an obvious stop stops it, a plain correction redirects it, and anything else is continue,
which there means it is never answered. Only words that could be a stop or a correction go
to the judge, told that he may be talking to someone else; everything else is continue at once.

First, anything in the transcript that is Sunday's own words coming back through the mic
is taken out: the echo canceller leaves enough of its voice for the recognizer to write
down, and its own sentences must never be taken as his. Both sides are compared in one
spoken form ("28" is "twenty eight", "7 o'clock" is "seven o clock", "3:47" is "three forty
seven"). A transcript that is mostly that, or whose leftover is a few words it said, is
dropped whole (own_voice): never answered, never acknowledged. A regex then decides the clear cases at no cost. The rest goes to a Haiku judge started
ahead of time, isolated the same way as the speech gate's. A judge that fails or times
out falls back to "continue": Sunday keeps talking unless he obviously asked it not to.

    barge.py check "<his words>" [--saying "<what Sunday was saying>"] [--question "<what it was answering>"]
    barge.py selftest       # the regex cases; add --judge to run the unclear ones through Haiku
"""

import asyncio
import difflib
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

JUDGE_MODEL = os.environ.get("VOICE_BARGE_MODEL", "claude-haiku-4-5-20251001")
JUDGE_TIMEOUT = float(os.environ.get("VOICE_BARGE_TIMEOUT", "4"))
JUDGE_SPARES = int(os.environ.get("VOICE_BARGE_SPARES", "1"))
JUDGE_SPARE_AGE = float(os.environ.get("VOICE_BARGE_SPARE_AGE", "1200"))

_LEAD = r"^(?:(?:hey|okay|ok|oh|um|uh|so|sunday|please|no|dude|god|jesus|bro|yo|just)[,.!]?\s+)*"
_TAIL = r"(?:[,.!]?\s+(?:it|that|this|there|now|please|sunday|for (?:a|one) (?:sec(?:ond)?|minute|moment)|" \
        r"a (?:sec(?:ond)?|minute|moment)|one (?:sec(?:ond)?|minute|moment)|talking|speaking|right there))*[.!?]*$"
_STOPS = (r"stop|stop it|quiet|be quiet|quiet down|shut up|hush|hold on|hold up|hang on|wait|wait up|pause|"
          r"never ?mind|nevermind|forget it|cancel|cancel that|enough|that's enough|that is enough|"
          r"mute|go mute|okay stop|ok stop|no stop|stop stop|"
          # however he says it: "shut the fuck up", "stop fucking talking", "fuck off", "STFU"
          r"shut (?:the (?:fuck|hell|heck) |the f |your mouth )?up|shut it|shush|shh+|silence|zip it|"
          r"stop (?:fucking |the fuck |bloody )?(?:talking|speaking|it)|(?:just |please )?stop(?: (?:fucking|now))?|"
          r"fuck off|fucking stop|stfu|be quiet (?:already|now)|quiet (?:already|now)|go away|"
          r"(?:i said |i told you to )(?:stop|shut up|be quiet|shut the fuck up)")
# The whole utterance is a stop: silence, nothing to answer.
STOP = re.compile(_LEAD + r"(?:" + _STOPS + r")" + _TAIL, re.I)
# Begins with a stop word and goes on: usually "hold on, what's ...", sometimes "cancel my
# 3 o'clock". Unclear, so the judge decides.
STOP_LEAD = re.compile(_LEAD + r"(?:stop|quiet|shut up|hold on|hold up|hang on|wait|pause|never ?mind|nevermind|"
                       r"forget (?:it|that)|cancel|enough)\b", re.I)
# A stop that is safe to act on from a partial transcript, before he has finished:
# these words never begin a separate request.
EARLY_STOP = re.compile(_LEAD + r"(?:stop|stop it|stop talking|quiet|be quiet|shut up|hush|hold on|hold up|"
                        r"hang on|wait|never ?mind|nevermind|enough|that's enough|"
                        r"shut (?:the (?:fuck|hell|heck) )?up|stop (?:fucking )?talking|fuck off|stfu|shush|silence)[.!]?$", re.I)
# Safe to act on from a partial transcript of words heard with no wake word: none of these
# begins something said to somebody else. "Wait" and "hold on" can, so they wait for him to finish.
PLAIN_STOP = re.compile(_LEAD + r"(?:stop|stop it|stop talking|quiet|be quiet|shut up|hush|that's enough|"
                        r"shut (?:the (?:fuck|hell|heck) )?up|stop (?:fucking )?talking|fuck off|stfu|shush)[.!]?$", re.I)
# Nothing but an acknowledgement or a noise: with no wake word, never worth asking the judge about.
ACK_ONLY = re.compile(r"^(?:(?:yeah|yes|yep|yup|okay|ok|uh huh|mhm+|mm+|hmm+|hm|right|sure|cool|nice|great|thanks|"
                      r"thank you|got it|alright|all right|oh|ah|uh|um|wow|really|interesting|no|nope|huh|haha|ha)"
                      r"[,.!?]?\s*)+$", re.I)
# A correction of what is being said.
REDIRECT = re.compile(
    _LEAD + r"(?:no|nope|nah)?[,.!]?\s*(?:"
    r"i (?:meant|mean|said|asked|wanted|was asking|didn'?t (?:say|ask|mean))|"
    r"not (?:that|this|the) (?:one|site|thing|file|email|meeting|person|place|project)|"
    r"not that\b|not what i (?:asked|said|meant|wanted)|that'?s not (?:what|it|right|the)|"
    r"that'?s (?:wrong|incorrect)|you'?re wrong|wrong (?:one|site|thing|file|email|person|place|project|day|time|"
    r"meeting|answer|person|number|name)|(?:the )?other one|instead|actually,? (?:i|no|not|the)|"
    r"you misheard|you heard (?:me )?wrong|that'?s not what)", re.I)
# Words that can mean he is correcting rather than asking something new.
CORRECTION_CUE = re.compile(r"\b(?:no|nope|not|wrong|meant|mean|instead|other|actually|rather|that one|didn'?t|"
                            r"said|incorrect|misheard|i asked|stop|wait|hold|cancel|never ?mind|enough|quiet)\b", re.I)
# Plainly a new question or request.
NEW_ASK = re.compile(
    _LEAD + r"(?:also|and|oh and|one more thing|another thing|after that|when you'?re done|then)?[,]?\s*"
    r"(?:what|what'?s|when|when'?s|where|where'?s|who|who'?s|why|how|how'?s|which|is|are|was|were|do|does|did|"
    r"can you|could you|would you|will you|can i|should i|"
    r"tell me|remind me|set|add|send|text|call|email|check|look up|find|play|turn|open|show|give me|"
    r"what about|how about|let me know|put|schedule|book|make|create|write|read)\b", re.I)


ECHO_RUN = 3          # this many words in a row that Sunday is saying are its own voice, not his
ECHO_LEFT = 2         # fewer of his own words than this left over: the whole thing was echo
# A transcript that is mostly Sunday's own recent speech is dropped whole and never answered:
# more than this share of its words in stretches of what it said (two or more words in a row,
# in the same order), or a residue of fewer than ECHO_FRAGMENT words that are all words it said.
ECHO_SHARE = 0.5
ECHO_FRAGMENT = 4
# Its name, which it cannot have said itself when it was not saying it: him calling it.
_NAME = re.compile(r"\bsunday\b", re.I)

# ---- the words both sides are compared in
#
# Sunday writes "seven o clock", "twenty eight" and "three forty seven"; the recognizer writes
# what it hears back as "7 o'clock", "28" and "3:47", and as often runs numbers together:
# "one twenty eight" comes back as "128", "twenty eight forty seven" as "2847", "twenty twenty
# six" as "2026". So both sides are put in one form before they are compared: every number,
# written or said, as its digits, one token each ("3:47" and "three forty seven" are both
# 3 4 7), "o'clock" however it is written, contractions spelled out, punctuation and case gone.

_UNITS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine".split())}
_TEENS = {w: n + 10 for n, w in enumerate(
    "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: (n + 2) * 10 for n, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"thousand": 1000, "million": 10 ** 6, "billion": 10 ** 9}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
             "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
             "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
             "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60, "seventieth": 70,
             "eightieth": 80, "ninetieth": 90, "hundredth": 100, "thousandth": 1000}
_NUMBER_WORDS = set(_UNITS) | set(_TEENS) | set(_TENS) | set(_SCALES) | {"hundred"} | set(_ORDINALS)
_CONTRACTIONS = [(re.compile(p, re.I), r) for p, r in (
    (r"\bwon't\b", "will not"), (r"\bcan't\b", "can not"), (r"\bcannot\b", "can not"), (r"\bshan't\b", "shall not"),
    (r"\bain't\b", "is not"), (r"\blet's\b", "let us"), (r"\by'all\b", "you all"),
    (r"n't\b", " not"), (r"'re\b", " are"), (r"'ve\b", " have"), (r"'ll\b", " will"), (r"'m\b", " am"),
    (r"'d\b", " would"), (r"'s\b", " is"))]
# One raw word as the recognizer or Sunday wrote it; "o clock" written as two is one.
_RAW = re.compile(r"\bo['’]?\s?clock\b\S*|\S+", re.I)
_PIECE = re.compile(r"\$?\d+(?:[,.:]\d+)*(?:st|nd|rd|th)?%?|[a-z]+")
ORDINAL_MARK = "th"   # the token after an ordinal's digits: "28th" and "twenty eighth" are 2 8 th


def _digit_piece(piece: str) -> list:
    """A number as written ("28", "3:47", "1,200", "28th", "$5.50", "50%") as its tokens."""
    money, percent = piece.startswith("$"), piece.endswith("%")
    core = piece.strip("$%")
    ordinal = bool(re.search(r"\d(?:st|nd|rd|th)$", core))
    core = re.sub(r"(?:st|nd|rd|th)$", "", core)
    if re.fullmatch(r"\d+\.\d+", core) and not money:
        whole, frac = core.split(".")
        out = list(whole) + ["point"] + list(frac)
    elif money and "." in core:
        whole, frac = core.split(".", 1)
        out = list(whole.replace(",", "")) + ["dollars"] + list(frac)
        money = False
    else:
        out = list(re.sub(r"\D", "", core))
    return out + ([ORDINAL_MARK] if ordinal else []) + (["dollars"] if money else []) + (["percent"] if percent else [])


def _pieces(raw: str) -> list:
    """The words of one raw word, lower case, contractions spelled out, numbers as digits."""
    w = raw.lower().replace("’", "'").replace("&", " and ")
    if re.fullmatch(r"\W*o'?\s?clock\W*", w):
        return ["oclock"]
    for pat, rep in _CONTRACTIONS:
        w = pat.sub(rep, w)
    w = w.replace("'", "")
    out = []
    for piece in _PIECE.findall(re.sub(r"[-/_]", " ", w)):
        out += _digit_piece(piece) if piece[0] in "$0123456789" else [piece]
    return out


def _read_numbers(words: list, k: int) -> tuple:
    """Number words from words[k] on, as said: "three forty seven" is 3 and 47, "twenty twenty
    six" is 20 and 26, "two thousand twenty six" is 2026. (index after them, [(digits,
    ordinal)])."""
    numbers, total, chunk, last = [], 0, 0, None

    def close(ordinal=False):
        nonlocal total, chunk, last
        if last is not None:
            numbers.append((str(total + chunk), ordinal))
        total, chunk, last = 0, 0, None

    i = k
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w == "and" and last in ("hundred", "scale") and nxt in _NUMBER_WORDS:
            i += 1
            continue
        if w == "oh" and last is not None and nxt in _UNITS:
            close()
            numbers.append(("0", False))
            i += 1
            continue
        if w == "a" and nxt in ("hundred", "thousand", "million") and last is None:
            i += 1
            continue
        ordinal = w in _ORDINALS
        if ordinal:
            n = _ORDINALS[w]
            kind = "unit" if n < 10 else "teen" if n < 20 else "tens" if n < 100 else "hundred" if n == 100 else "scale"
        elif w in _UNITS:
            n, kind = _UNITS[w], "unit"
        elif w in _TEENS:
            n, kind = _TEENS[w], "teen"
        elif w in _TENS:
            n, kind = _TENS[w], "tens"
        elif w == "hundred":
            n, kind = 100, "hundred"
        elif w in _SCALES:
            n, kind = _SCALES[w], "scale"
        else:
            break
        joins = (kind == "unit" and (last == "tens" and chunk % 10 == 0 or last in ("hundred", "scale"))) or \
                (kind in ("teen", "tens") and last in ("hundred", "scale")) or \
                (kind == "hundred" and last in ("unit", "teen", "tens") and chunk < 100) or \
                (kind == "scale" and last in ("unit", "teen", "tens", "hundred"))
        if not joins:
            close()
        if kind == "hundred":
            chunk = (chunk or 1) * 100 if joins or not ordinal else 100
        elif kind == "scale":
            total, chunk = total + (chunk or 1) * n, 0
        else:
            chunk += n
        last = kind
        i += 1
        if ordinal:
            close(True)
    close()
    return i, numbers


def _tokens(text: str) -> list:
    """(raw word, its tokens, number of each token or None, lead) for each word as written.
    Number words said across several raw words ("twenty eight") are read as one, and their
    tokens belong to the first of them; the others have none and name it as lead."""
    raws = _RAW.findall(text or "")
    flat = [(p, i) for i, raw in enumerate(raws) for p in _pieces(raw)]
    toks = [[] for _ in raws]
    nums = [[] for _ in raws]
    lead = [None] * len(raws)
    k = 0
    while k < len(flat):
        w, i = flat[k]
        if w in _NUMBER_WORDS and not (w == "second" and k + 1 < len(flat) and flat[k + 1][0] == "of"):
            j, numbers = _read_numbers([x for x, _ in flat], k)
            if j > k:
                members = sorted({flat[x][1] for x in range(k, j)})
                for digits, ordinal in numbers:
                    toks[members[0]] += list(digits) + ([ORDINAL_MARK] if ordinal else [])
                    nums[members[0]] += [digits] * len(digits) + ([None] if ordinal else [])
                for m in members[1:]:
                    lead[m] = members[0]
                k = j
                continue
        toks[i].append(w)
        nums[i].append(None)
        k += 1
    for i in range(len(raws)):
        # Digits as written are one number per raw word: "3:47" is 347, "28" is 28.
        digits = "".join(t for t, n in zip(toks[i], nums[i]) if t.isdigit() and n is None)
        nums[i] = [n if n is not None else (digits if t.isdigit() else None) for t, n in zip(toks[i], nums[i])]
    return [(raw, toks[i], nums[i], lead[i]) for i, raw in enumerate(raws)]


def canon(text: str) -> list:
    """The tokens of a whole text in the form both sides are compared in."""
    return [w for _, ws, _, _ in _tokens(text) for w in ws]


def _words(text: str) -> list:
    return canon(text)


def word_count(text: str) -> int:
    """Words as written, a number said in several words counting as one."""
    return sum(1 for _, ws, _, lead in _tokens(text) if ws and lead is None)


def _streams(words: list) -> list:
    """Runs of consecutive digits: "three forty seven" and "3:47" are both "347"."""
    out, cur = [], ""
    for w in words + [""]:
        if w.isdigit():
            cur += w
        elif cur:
            out.append(cur)
            cur = ""
    return out


def _runs(toks: list, said: list, whole: bool = True) -> list:
    """The stretches of the heard words that are, in order, what Sunday said: one set of token
    positions (into the flattened form) per stretch. With whole, a raw word only counts when
    all of it matched, so taking a run out never cuts a number in half: "1950" against
    "nineteen ninety one" is not its own. Without, part of a number counts too: the recognizer
    writes "one twenty eight" as "128"."""
    flat, owner = [], []
    for i, (_, words, _, _) in enumerate(toks):
        flat += words
        owner += [i] * len(words)
    if not said or not flat:
        return []
    runs = []
    for b in difflib.SequenceMatcher(None, flat, said, autojunk=False).get_matching_blocks():
        if not b.size:
            continue
        span = range(b.a, b.a + b.size)
        if whole:
            full = {i for i in {owner[k] for k in span} if all(k in span for k, o in enumerate(owner) if o == i)}
            run = [k for k in span if owner[k] in full]
        else:
            run = list(span)
        if run:
            runs.append(run)
    return runs


def _run_length(run: list, toks: list) -> int:
    """How many words a run is, counting each number once however many digits it has."""
    flat = [(w, n, i) for i, (_, ws, ns, _) in enumerate(toks) for w, n in zip(ws, ns)]
    seen, length = set(), 0
    for k in run:
        w, n, i = flat[k]
        if n is None:
            length += 1
        elif (i, n) not in seen:
            seen.add((i, n))
            length += 1
    return length


def strip_echo(heard: str, saying: str) -> tuple[str, int]:
    """His words with any run of Sunday's own words taken out, and how many tokens that was.
    Compared in one form, so "28" is "twenty eight" and "7 o'clock" is "seven o clock"."""
    toks = _tokens(heard)
    owner = [i for i, (_, ws, _, _) in enumerate(toks) for _ in ws]
    gone = {owner[k] for run in _runs(toks, canon(saying)) if _run_length(run, toks) >= ECHO_RUN for k in run}
    gone |= {i for i, t in enumerate(toks) if t[3] in gone}
    if not gone:
        return heard, 0
    keep = [raw for i, (raw, ws, _, lead) in enumerate(toks) if i not in gone and (ws or lead is not None)]
    return " ".join(keep), sum(len(toks[i][1]) for i in gone)


# A short answer that is only "yes" or "no" is his, whatever Sunday said; so is a mute or unmute.
_YES_NO = re.compile(r"^(?:yes|yeah|yep|yup|no|nope|nah)\b[\s,.!?]*(?:(?:sunday|please|thanks)[.!?]?)?$", re.I)
_MUTE = re.compile(r"\b(?:un-?)?mute[ds]?\b", re.I)


def own_voice(heard: str, recent: str) -> dict | None:
    """Why this transcript is Sunday's own recent speech coming back, or None if it is his.
    recent: what Sunday was saying around when he spoke, on every device. Dropped whole when:
      - more than ECHO_SHARE of its telling words (not those too common to tell its voice from
        his) sit in stretches of what it said - two or more tokens in a row, in its order, at
        least one of them telling - and either there are several such stretches (its voice
        written down badly keeps whole pieces of it in order: "7 o'clock one 28 hour we loans
        47") or the one stretch leaves nothing new beside it (a correction like "founded in
        1950" against "founded in nineteen ninety one" adds something of his and is kept);
        single shared words never count, so a question that reuses its words ("how many went
        out at seven?") is his;
      - or what is left once runs of its words are taken out is under ECHO_FRAGMENT words, all
        of them things it said ("o'clock 28 47").
    A stop, a correction, a mute and a bare yes or no are never dropped."""
    text = " ".join((heard or "").split())
    said = canon(recent)
    toks = _tokens(text)
    flat = [(w, n) for _, ws, ns, _ in toks for w, n in zip(ws, ns)]
    words = [w for w, _ in flat]
    if not words or not said:
        return None
    rest, _ = strip_echo(text, recent)
    if any(STOP.match(x) or PLAIN_STOP.match(x) for x in sentences(text) + sentences(rest) + [rest]) \
            or REDIRECT.search(text) or _YES_NO.match(text) or _MUTE.search(text):
        return None
    vocab = {w for w in said if not w.isdigit()}
    streams = _streams(said)

    def known(w, n):
        # A number is one it said when its digits run together somewhere in what it said.
        return any(n in st for st in streams) if w.isdigit() else w in vocab

    runs = [r for r in _runs(toks, said, whole=False) if len(r) >= 2 and any(words[k] not in _COMMON for k in r)]
    content = [k for k, w in enumerate(words) if w not in _COMMON]
    inside = {k for r in runs for k in r}
    mine = sum(1 for k in content if k in inside)
    if content and mine / len(content) > ECHO_SHARE:
        new = [words[k] for k in content if k not in inside and not known(*flat[k])]
        if len(runs) >= 2 or not new:
            return {"why": f"{mine} of its {len(content)} telling words are what it just said", "matched": mine,
                    "words": len(content), "rest": ""}
    left = [t for t in _tokens(rest) if t[1] or t[3] is not None]
    if left and len(left) < ECHO_FRAGMENT and all(known(w, n) for _, ws, ns, _ in left for w, n in zip(ws, ns)):
        return {"why": f"what is left, {rest!r}, is only words it just said",
                "matched": len(words) - sum(len(t[1]) for t in left), "words": len(words), "rest": rest}
    return None


# Words too common to tell his voice from its own.
_COMMON = set("a an the is are was were be been to of in on at for and or but it its this that these those i you he "
              "she we they me my your our so do does did not no as by with from".split()) | {ORDINAL_MARK}

# ---- its own voice, misheard
#
# The recognizer can write Sunday's own sentence back as different words that sound like it:
# "Abel Jimenez now has Plant Manager access at Plant 402 in Smyrna Tools" came back on
# 2026-10-06 18:28 as "Both commanders now has client manager access and client forward too",
# and own_voice, which matches words exactly, kept it. So what was heard is also aligned
# against every stretch of what it was saying, word by word, with words counting as the same
# when they sound alike (spelled alike, or alike once the vowels are gone: "client" and
# "plant", "commanders" and "Jimenez", "too" and "tools"; numbers when all but one digit agree).
# The share of what was heard that lines up that way is the match. A real question about the
# same thing reuses its words too ("Who is the plant manager at 406?" lines up as well as the
# mishearing does), so a match never drops a question or a request, and never makes a stop or
# a correction out of anything: it only keeps what sounds like its own sentence from cutting it.

FUZZY_ECHO = float(os.environ.get("VOICE_FUZZY_ECHO", "0.6"))   # this share lined up is its own voice
FUZZY_MIN = 5          # under this many words, too short to tell
FUZZY_DROP_MIN = 6     # a statement this long that lines up is dropped unanswered; shorter ones only never cut


def _skeleton(w: str) -> str:
    return w[:1] + re.sub(r"[aeiouy]", "", w[1:]) if len(w) > 2 else w


def _alike(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.isdigit() or b.isdigit():
        return a.isdigit() and b.isdigit() and len(a) == len(b) >= 2 and sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) < 3 or len(b) < 3 or a in _COMMON or b in _COMMON:
        return False      # short and common words only count when they are the same word
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if len(a) >= 4 and len(b) >= 4:
        r = max(r, difflib.SequenceMatcher(None, _skeleton(a), _skeleton(b)).ratio())
    return r >= 0.6


def _sound_words(text: str) -> list:
    """Words in the compared form, each number as one word."""
    out = []
    for _, ws, ns, lead in _tokens(text):
        if lead is not None:
            continue
        cur = ""
        for w in ws:
            if w.isdigit():
                cur += w
            else:
                if cur:
                    out.append(cur)
                    cur = ""
                if w != ORDINAL_MARK:
                    out.append(w)
        if cur:
            out.append(cur)
    return out


def _aligned(alike: list, a: int, b: int) -> int:
    """How many heard words line up, in order, with words a..b of what was said that sound
    like them. alike[k][j]: heard word k sounds like said word j."""
    prev = [0] * (b - a + 1)
    for row in alike:
        cur = [0] * (b - a + 1)
        for j in range(1, b - a + 1):
            cur[j] = max(prev[j], cur[j - 1], prev[j - 1] + row[a + j - 1])
        prev = cur
    return prev[-1]


def sounds_like_said(heard: str, saying: str) -> tuple[float, str]:
    """(share of what was heard that lines up with some stretch of what Sunday was saying no
    longer than 1.4 times it, that stretch). 0 when too short to tell."""
    h, s = _sound_words(heard)[:40], _sound_words(saying)[-400:]
    n = len(h)
    if n < FUZZY_MIN or not s:
        return 0.0, ""
    cache: dict = {}
    alike = [[cache.setdefault((x, y), _alike(x, y)) if (x, y) not in cache else cache[(x, y)] for y in s] for x in h]
    longest = int(n * 1.4) + 1
    best, where = 0.0, ""
    for i in range(len(s)):
        j = min(len(s), i + longest)
        if i and j - i < max(2, int(n * 0.7)):
            break
        share = _aligned(alike, i, j) / n
        if share > best:
            best, where = share, " ".join(s[i:j])
    return round(best, 2), where


def is_request(text: str) -> bool:
    """A question or a request in its own right, whatever words it shares with Sunday's."""
    t = " ".join(text.split())
    return bool(NEW_ASK.match(t) or t.rstrip().endswith("?"))


def _root(w: str) -> str:
    return w.split("'")[0]


def his_words(heard: str, around: str) -> list:
    """The words in what the mic heard that are not Sunday's own: each that does not closely
    match a word it was saying around then. The recognizer writes its residual voice back
    approximately ("here's" for "here is", "fitting" as "fitted"), so the match is loose,
    and words too common to tell apart never count as his."""
    pool = sorted({_root(w) for w in _words(around)})
    return [w for w in (_root(w) for w in _words(heard)) if w and w not in _COMMON and not _said(w, pool)]


def _said(w: str, pool: list) -> bool:
    if w in pool:
        return True
    # Loose only for longer words: a short one ("stop") is never its own because it sounds
    # like one it said ("top").
    if len(w) >= 5 and difflib.get_close_matches(w, pool, n=1, cutoff=0.8):
        return True
    return len(w) >= 6 and any(len(os.path.commonprefix([w, p])) >= 4 and len(p) >= 6 for p in pool)


def his_tail(sentence: str, around: str) -> str:
    """The words at the end of a sentence that are his, back to the last one that is its own:
    the recognizer runs a short "stop" into the sentence of its own voice around it."""
    pool = sorted({_root(w) for w in _words(around)})
    toks = [(raw, ws) for raw, ws, _, lead in _tokens(re.sub(r"[^\w\s'\u2019$%:.,-]", " ", sentence))
            if ws or lead is not None]
    i = len(toks)
    while i > 0:
        if any(w not in _COMMON and _said(w, pool) for w in toks[i - 1][1]):
            break
        i -= 1
    tail = toks[i:]
    return " ".join(raw.strip(",.!?;:") for raw, _ in tail) \
        if any(w not in _COMMON for _, words in tail for w in words) else ""


def sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s.strip()]


def may_want_sunday(text: str) -> bool:
    """Words heard with no wake word that could be telling Sunday to stop or correcting it:
    worth turning it down for while they are decided. Anything else is not."""
    parts = sentences(text)
    return any(STOP.match(p) or PLAIN_STOP.match(p) or STOP_LEAD.match(p) for p in parts) or bool(REDIRECT.search(text))


def regex_decision(heard: str):
    """("stop"|"redirect"|"continue", reason) from the words alone, or None if unclear."""
    text = " ".join(heard.split())
    if not text:
        return "continue", "nothing heard"
    if STOP.match(text):
        return "stop", "explicit stop"
    if REDIRECT.search(text):
        return "redirect", "correction of what was being said"
    if STOP_LEAD.match(text):
        return None
    if NEW_ASK.match(text) and not CORRECTION_CUE.search(text) and len(text.split()) >= 3:
        return "continue", "a new, separate request"
    return None


JUDGE_PROMPT = _render("""{assistant} is a voice assistant. While it was talking, {user} said something to it. Decide what that requires. Reply with one JSON object and nothing else:
{"decision": "stop" or "redirect" or "continue", "why": "a few words"}

stop: {he} only wants it to stop talking, with nothing else in {his} words to answer ("stop", "hold on", "that's enough", "never mind", "quiet, please").
redirect: {he} is correcting or redirecting the very thing it is saying or answering: it misheard, picked the wrong one, answered the wrong question, or {he} wants something different from the same answer ("no, I meant the other site", "not that one", "the Tuesday one", "hold on, I asked about Austin"). Also any stop word followed by a question or request ("wait, what time is it?", "hold on, call Sam") is redirect, never stop: it stops and answers what {he} went on to ask.
continue: anything else - a separate request or question that doesn't depend on the current answer being wrong ("also remind me to call mom", "what's the weather tomorrow", "cancel my 3 o'clock meeting", "pause the music"). The current answer finishes and this is answered after it.

Only stop or redirect when {his} words plainly ask for it. Words that don't fit the conversation - unrelated, garbled, or sounding overheard from a TV or video - are continue, never stop. When unsure, choose continue. Decide on what {his} words actually say against what {assistant} was saying.""")

UNADDRESSED = ("HE DID NOT SAY SUNDAY'S NAME. Its mic picked this up while it was talking, and he may be talking to "
               "someone else in the room or on the phone, or it may be a TV. stop only if his words plainly tell Sunday "
               "to stop talking. redirect only if they plainly correct what Sunday is saying right now. Anything else "
               "is continue: it was not said to Sunday. Here a stop word followed by a question or request is not "
               "redirect: \"wait, what time is it?\" is continue, and so is any question or request that does not "
               "say what Sunday got wrong.")


class Judge:
    """Haiku processes started ahead of time and used once each, isolated like the speech
    gate's: no settings, no MCP servers, no tools, no thinking."""

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
                log.warning("barge judge: could not start one: %s", e)
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
                    raise RuntimeError("judge exited without a decision")
                m = json.loads(line)
                if m.get("type") == "result":
                    if m.get("is_error"):
                        raise RuntimeError(str(m.get("result"))[:200])
                    return m.get("result") or ""
        finally:
            if proc.returncode is None:
                proc.kill()

    def close(self):
        for _, p in self.spares:
            if p.returncode is None:
                p.kill()
        self.spares = []


judge = Judge()


def _parse(out: str) -> dict:
    start = out.find("{")
    if start < 0:
        raise ValueError(f"no JSON in {out[:120]!r}")
    v, _ = json.JSONDecoder().raw_decode(out[start:])
    decision = str(v.get("decision", "")).lower()
    if decision not in ("stop", "redirect", "continue"):
        raise ValueError(f"bad decision {decision!r}")
    return {"decision": decision, "why": str(v.get("why", ""))[:200]}


def has_cue(text: str) -> bool:
    """Plain words of a correction: without one, nothing he said is taken as one."""
    return bool(REDIRECT.search(text) or CORRECTION_CUE.search(text))


async def decide(heard: str, saying: str = "", question: str = "", addressed: bool = True,
                 recent: str = "", strict: bool = False) -> dict:
    """{"decision": "stop"|"redirect"|"continue", "reason", "by": "echo"|"regex"|"judge"|"fallback", "ms",
    "text": his words with Sunday's own taken out, "echo": how many words were its own}.
    addressed=False: heard with no wake word, so only a plain stop or a plain correction counts.
    recent: everything Sunday said lately on any device, besides what it is saying now.
    strict: a device woke on its own speech while it was talking, which its own voice can do:
    only a plain stop or a plain correction acts, and the judge is not asked.
    by "echo" means the whole transcript was its own voice: never answered, never acknowledged."""
    t0 = time.monotonic()
    v = await _decide(heard, saying, question, t0, addressed, recent, strict)
    if v["decision"] == "redirect" and v["by"] == "judge" and not has_cue(v.get("text") or heard):
        # A judge reads a statement that contradicts Sunday as a correction, and its own voice
        # misheard is exactly that. A redirect needs his words to say so.
        v = {**v, "decision": "continue", "by": "regex",
             "reason": f"no correction in the words (the judge said redirect: {v['reason']})"}
    return v


async def _decide(heard: str, saying: str, question: str, t0: float, addressed: bool = True,
                  recent: str = "", strict: bool = False) -> dict:
    mine, echoed = strip_echo(heard, saying)
    own = own_voice(heard, " ".join(x for x in (recent, saying) if x))
    # "Sunday" left once its own words are out, when it was not saying its name, is him calling
    # it right after a line it woke on itself (2026-10-07: "One more time. Sunday."), not echo.
    called = bool(_NAME.search(mine)) and not _NAME.search(f"{recent} {saying}")
    if not called and (own or (echoed and word_count(mine) < ECHO_LEFT and not STOP.match(" ".join(mine.split())))):
        why = own["why"] if own else f"{echoed} words"
        return {"decision": "continue", "reason": f"only its own voice coming back through the mic ({why})",
                "by": "echo", "ms": 0, "text": "", "echo": own["matched"] if own else echoed}
    plain = " ".join(heard.split())
    if not (STOP.match(plain) or any(STOP.match(s) or PLAIN_STOP.match(s) for s in sentences(plain))
            or REDIRECT.search(plain)):
        share, like = sounds_like_said(heard, " ".join(x for x in (recent, saying) if x))
        if share >= FUZZY_ECHO:
            if not is_request(plain) and not called and len(_sound_words(heard)) >= FUZZY_DROP_MIN:
                return {"decision": "continue", "by": "echo", "ms": 0, "text": "", "echo": word_count(heard),
                        "reason": f"only its own voice, misheard: {share:.0%} of it sounds like {like!r}"}
            return {"decision": "continue", "by": "regex", "ms": 0, "text": mine, "echo": echoed,
                    "reason": f"sounds like what it was saying ({share:.0%} like {like!r}), so it never cuts"}
    heard = mine
    base = {"text": heard, "echo": echoed}
    if strict:
        text = " ".join(heard.split())
        if STOP.match(text) or any(STOP.match(s) or PLAIN_STOP.match(s) for s in sentences(text)):
            return {"decision": "stop", "reason": "explicit stop", "by": "regex", "ms": 0, **base}
        if REDIRECT.search(text):
            return {"decision": "redirect", "reason": "explicit correction", "by": "regex", "ms": 0, **base}
        return {"decision": "continue", "by": "regex", "ms": 0, **base,
                "reason": "woke on its own speech, and not plainly a stop or a correction"}
    if not addressed:
        if any(STOP.match(s) for s in sentences(heard)):
            # A stop said on its own, even run together with something else the mic caught.
            return {"decision": "stop", "reason": "explicit stop", "by": "regex", "ms": 0, **base}
        if ACK_ONLY.match(" ".join(heard.split())):
            return {"decision": "continue", "reason": "only an acknowledgement", "by": "regex", "ms": 0, **base}
        if not (may_want_sunday(heard) or CORRECTION_CUE.search(heard)):
            return {"decision": "continue", "reason": "nothing in it is a stop or a correction", "by": "regex",
                    "ms": 0, **base}
    hit = regex_decision(heard)
    if hit and (addressed or hit[0] != "redirect"):
        return {"decision": hit[0], "reason": hit[1], "by": "regex", "ms": 0, **base}
    case = (f"SUNDAY WAS ANSWERING: {question or '(an announcement, not a question)'}\n\n"
            f"SUNDAY WAS SAYING: {saying[-600:] or '(unknown)'}\n\nHE SAID: {heard}"
            + ("" if addressed else "\n\n" + UNADDRESSED))
    try:
        v = _parse(await asyncio.wait_for(judge.ask(case), JUDGE_TIMEOUT))
        return {"decision": v["decision"], "reason": v["why"] or "judge", "by": "judge",
                "ms": int((time.monotonic() - t0) * 1000), **base}
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        log.error("barge judge failed after %d ms (%s: %s); not stopping", ms, type(e).__name__, e)
        return {"decision": "continue", "reason": f"judge failed ({type(e).__name__}), so not stopping",
                "by": "fallback", "ms": ms, **base}


# ---------------------------------------------------------------- selftest

CASES = [
    # (his words, expected regex decision or None for the judge)
    ("Stop.", "stop"), ("Sunday, stop.", "stop"), ("stop talking", "stop"), ("Quiet.", "stop"),
    ("shut up", "stop"), ("Hold on.", "stop"), ("hold on a second", "stop"), ("Wait.", "stop"),
    ("wait a sec", "stop"), ("Pause.", "stop"), ("Never mind.", "stop"), ("nevermind", "stop"),
    ("Cancel.", "stop"), ("cancel that", "stop"), ("Enough.", "stop"), ("That's enough.", "stop"),
    ("Mute.", "stop"), ("okay stop", "stop"),
    ("No, I meant the other one.", "redirect"), ("Not that one.", "redirect"), ("Wrong site.", "redirect"),
    ("I said Austin, not Boston.", "redirect"), ("That's not what I asked.", "redirect"),
    ("no I meant the Morgan site", "redirect"), ("the other one", "redirect"),
    ("What's the weather tomorrow?", "continue"), ("Also remind me to call mom at five.", "continue"),
    ("What time is it?", "continue"), ("Can you check my email after that?", "continue"),
    ("Tell me a joke when you're done.", "continue"), ("Who won the game last night?", "continue"),
    ("Cancel my three o'clock meeting.", None), ("Wait, what time is it?", None), ("Pause the music.", None),
    ("Hmm, interesting.", None), ("Thanks.", None),
    ("Don't shoot me, I'm pregnant. I didn't care, I shot her anyway.", None),
]


async def selftest(use_judge: bool) -> int:
    bad = 0
    for text, expect in CASES:
        got = regex_decision(text)
        got = got[0] if got else None
        ok = got == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  regex expect {str(expect):<8} got {str(got):<8} {text}")
    for f in ("stop", "stop it", "hold on", "wait", "never mind"):
        ok = bool(EARLY_STOP.match(f))
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  early stop {f}")
    porch = ("A note on the porch light schedule for the fall. Starting tonight the porch light comes on at six "
             "fifteen instead of seven. It turns off at eleven on weeknights and at midnight on Fridays and Saturdays.")
    for heard, expect_left in (
            ("On the porch light schedule for the fourth. It turns off at 11 on weeknights and at midnight on Fridays.",
             "echo"),
            ("It turns off at eleven on weeknights. What's the weather tomorrow?", "What's the weather tomorrow?"),
            ("What's the weather tomorrow?", "What's the weather tomorrow?")):
        mine, n = strip_echo(heard, porch)
        got = "echo" if n and word_count(mine) < ECHO_LEFT else mine
        ok = got == expect_left
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  echo -> {got!r}  from {heard[:60]}")
    for f in ("pause", "cancel", "stop the", "what"):
        ok = not EARLY_STOP.match(f)
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  not an early stop {f}")
    for f, expect in (("stop", True), ("Shut the fuck up.", True), ("shut up", True), ("Be quiet.", True),
                      ("fuck off", True), ("Sunday, stop.", True), ("wait", False), ("hold on", False),
                      ("stop by the store", False), ("hey can you grab the door", False)):
        ok = bool(PLAIN_STOP.match(f)) == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  plain stop {expect!s:<5} {f}")
    garden = "The hose by the shed has a slow leak at the fitting, and a new washer will fix it. Here is a note."
    for heard, expect in (("Here's in blocking.", ["blocking"]), ("the hose has a slow leak at the fitted", []),
                          ("Stop.", ["stop"]), ("Hey, can you grab the door?", ["hey", "can", "grab", "door"])):
        got = his_words(heard, garden)
        ok = got == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  his words {got} from {heard!r}")
    towels = "Bath towels go on the middle shelf, and the hand towels sit in the basket beside them."
    for heard, expect in (("the hand towels stop.", "stop"), ("the middle shelf shut the fuck up", "shut the fuck up"),
                          ("the hand towels sit in the basket", ""), ("I can't stop", "I can't stop")):
        got = his_tail(heard, towels)
        bad += got != expect
        print(f"{'PASS' if got == expect else 'FAIL'}  his tail {got!r} from {heard!r}")
    got = his_words("Stop.", "Plates go on the bottom rack, and the glasses go on top between the tines.")
    bad += got != ["stop"]
    print(f"{'PASS' if got == ['stop'] else 'FAIL'}  his words {got} from 'Stop.' while saying 'on top'")
    for f, expect in (("Stop.", True), ("No, I meant the other site.", True), ("Hold on, what's that?", True),
                      ("I love cream.", False), ("Hey, can you grab the door?", False), ("Yeah, okay.", False),
                      ("The so it came as that of his mind.", False), ("A note on the bird. Shut up.", True)):
        ok = may_want_sunday(f) == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  may want sunday {expect!s:<5} {f}")
    for f, expect in (("Yeah, okay.", True), ("Mhm.", True), ("Thank you.", True), ("okay stop", False),
                      ("hey can you grab the door", False)):
        ok = bool(ACK_ONLY.match(f)) == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  acknowledgement {expect!s:<5} {f}")
    for raw, expect in (("7 o'clock", "7 oclock"), ("seven o clock", "7 oclock"), ("twenty eight", "2 8"),
                        ("3:47", "3 4 7"), ("three forty seven", "3 4 7"), ("twenty eighth", "2 8 th"),
                        ("28th", "2 8 th"), ("first", "1 th"), ("half-hourly", "half hourly"), ("won't", "will not"),
                        ("twenty twenty six", "2 0 2 6"), ("two thousand twenty six", "2 0 2 6"),
                        ("one thousand two hundred", "1 2 0 0"), ("1,200", "1 2 0 0"), ("nineteen ninety one", "1 9 9 1"),
                        ("three hundred and five", "3 0 5"), ("three oh five", "3 0 5"), ("50%", "5 0 percent"),
                        ("$5", "5 dollars")):
        got = " ".join(canon(raw))
        bad += got != expect
        print(f"{'PASS' if got == expect else 'FAIL'}  spoken form {raw!r} -> {got!r}")
    # The cube's announcement on 2026-10-06 16:08, and what the mics wrote down of it.
    letters = ("Thirty eight preview letters have gone out today, each with its own site built and live first. Ten went "
               "out with the seven o clock run this morning, and the other twenty eight came from the half hourly runs "
               "this afternoon, the latest at three forty seven.")
    for heard, said, expect in (
            ("7 o'clock one 28 hour we loans 47.", letters, True),
            ("Letters have gone out today, each with its own site built and live first. 10 went out with the 7 o'clock "
             "run this morning, and the other 28 came from the half-hourly runs this afternoon, the latest at 3:47.",
             letters, True),
            ("o'clock 28 47.", letters, True),
            ("7 o'clock 128 hour, we loans 47.", letters, True),
            ("O'Clock 2847.", letters, True),
            ("what time is it", letters, False), ("Stop.", letters, False), ("How many went out at seven?", letters, False),
            ("Why twenty eight?", letters, False), ("No.", letters, False),
            ("No, I won't say it again. That was you speaking. You need to ensure that you don't reply to yourself",
             letters + " I didn't catch that. Could you say it again?", False),
            # A correction that repeats most of what it said, with a word of his own.
            ("Founded in 1950.", "Southeast Arbor is live saying thirty five years in business, founded in nineteen "
             "ninety one.", False),
            ("the hand towels stop.", towels, False)):
        got = own_voice(heard, said) is not None
        bad += got != expect
        print(f"{'PASS' if got == expect else 'FAIL'}  own voice {expect!s:<5} {heard[:70]}")
    # What was left of it on 2026-10-06 was "o'clock 28 47.", which was answered.
    full = ("Letters have gone out today, each with its own site built and live first. 10 went out with the 7 o'clock "
            "run this morning, and the other 28 came from the half-hourly runs this afternoon, the latest at 3:47.")
    got = strip_echo(full, letters)
    bad += got[0] != ""
    print(f"{'PASS' if got[0] == '' else 'FAIL'}  strip echo leaves {got[0]!r} ({got[1]} words its own)")
    # 2026-10-06 18:28: the cube woke on its own announcement and the judge redirected on the
    # recognizer's mishearing of it, cutting the announcement. Never again: continue, no cut.
    plant = ("Abel Jimenez now has Plant Manager access at Plant 402 in Smyrna Tools, on the account he signed up "
             "with, and I checked it in the database. I have not messaged Armando or Abel. One thing to know: there "
             "is a second account for an Abiel Jiminez as Plant Manager at Plant 406 from September, which may be "
             "the same man, and Steven Morris is still Plant Manager at 402 as well. Do you want either of those changed?")

    async def no_judge(case):
        raise AssertionError("the judge was asked")

    async def redirecting_judge(case):
        return '{"decision": "redirect", "why": "he contradicts what Sunday said"}'

    real_ask = judge.ask
    try:
        judge.ask = no_judge
        for heard, kw, expect, cut in (
                ("Both commanders now has client manager access and client forward too.", {"strict": True},
                 "continue", False),
                ("Both commanders now has client manager access and client forward too.", {}, "continue", False),
                ("Both commanders now has client manager access and client forward too.", {"addressed": False},
                 "continue", False),
                ("No, I meant plant 401.", {"strict": True}, "redirect", True),
                ("No, I meant plant 401.", {}, "redirect", True),
                ("Stop.", {"strict": True}, "stop", True), ("Shut up.", {"strict": True}, "stop", True),
                ("Sunday, shut the fuck up.", {"strict": True}, "stop", True), ("stop", {"addressed": False}, "stop", True),
                ("Who is the plant manager at 406?", {}, "continue", False),
                ("There is a second account for an April Jimenez as plan manager.", {"strict": True}, "continue", False)):
            v = await decide(heard, plant, "", **kw)
            ok = v["decision"] == expect and (v["decision"] in ("stop", "redirect")) == cut
            if heard.startswith("Who is"):
                ok = ok and v["by"] != "echo"     # a question about the same thing is his, and answered
            bad += not ok
            print(f"{'PASS' if ok else 'FAIL'}  18:28 {kw or ''} expect {expect:<8} got {v['decision']:<8} by "
                  f"{v['by']:<6} {heard[:50]}  ({v['reason'][:70]})")
        # 2026-10-07: the cube woke on its own line, and his "Sunday" right after it was left
        # over once its words were out. His, and answered; its own name coming back is not.
        for heard, said, expect_text in (
                ("One more time. Sunday.", "Testing the speaker one more time.", "Sunday."),
                ("And the yard is quiet. Sunday.", "The front gate is latched and the yard is quiet.", "Sunday."),
                ("You can say Sunday, overlay on.", "You can say Sunday, overlay on or overlay off.", ""),
                ("Overlay off. Sunday.", "You can say overlay on or overlay off, Sunday.", "")):
            v = await decide(heard, said, "", strict=True)
            ok = v["decision"] == "continue" and v["text"] == expect_text and (v["by"] == "echo") == (not expect_text)
            bad += not ok
            print(f"{'PASS' if ok else 'FAIL'}  its name left over: got {v['decision']:<8} by {v['by']:<6} "
                  f"{v['text']!r} from {heard!r}")
        judge.ask = redirecting_judge
        for heard, expect in (("Steven should be a viewer at that plant.", "continue"),
                              ("No, the Tuesday one.", "redirect")):
            v = await decide(heard, "Your meeting on Monday is with Sam at ten, then Chris at two.", "")
            ok = v["decision"] == expect
            bad += not ok
            print(f"{'PASS' if ok else 'FAIL'}  judge says redirect, cue needed: expect {expect:<8} got "
                  f"{v['decision']:<8} {heard}")
    finally:
        judge.ask = real_ask
    for heard, said, expect in (
            ("Both commanders now has client manager access and client forward too.", plant, True),
            ("Who is the plant manager at 406?", plant, True),
            ("Hey Nick. Hey man. About to head back that way.", plant, False),
            ("what time is it", letters, False)):
        share, _ = sounds_like_said(heard, said)
        ok = (share >= FUZZY_ECHO) == expect
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  sounds like it {expect!s:<5} {share:.2f} {heard[:60]}")
    if use_judge:
        await judge.fill()
        judged = [("Cancel my three o'clock meeting.", "continue"), ("Wait, what time is it?", "redirect"),
                  ("Pause the music.", "continue"),
                  ("Don't shoot me, I'm pregnant. I didn't care, I shot her anyway.", "continue"),
                  ("No, the Tuesday one.", "redirect")]
        for text, expect in judged:
            v = await decide(text, "Your meeting on Monday is with Sam at ten, then Chris at two.",
                             "When is my meeting with Sam?")
            ok = v["decision"] == expect
            bad += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {v['by']:<8} expect {expect:<8} got {v['decision']:<8} "
                  f"{v['ms']:>5} ms  {text}  ({v['reason']})")
        unaddressed = [("Hey, can you grab the door?", "continue"), ("Yeah, okay.", "continue"),
                       ("No, I meant the other site.", "redirect"), ("Shut the fuck up.", "stop"),
                       ("Actually, I will have the chicken instead.", "continue"),
                       ("What time is the game tonight?", "continue")]
        for text, expect in unaddressed:
            v = await decide(text, "Here is the plan for the Morgan site: the new header goes live tonight, then the "
                             "contact page tomorrow.", "What's the plan for the Morgan site?", addressed=False)
            ok = v["decision"] == expect
            bad += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {v['by']:<8} no name, expect {expect:<8} got {v['decision']:<8} "
                  f"{v['ms']:>5} ms  {text}  ({v['reason']})")
        judge.close()
    return bad


async def _check_cli(argv):
    heard, saying, question = argv[0], "", ""
    i = 1
    while i < len(argv) - 1:
        if argv[i] == "--saying":
            saying = argv[i + 1]
        elif argv[i] == "--question":
            question = argv[i + 1]
        i += 2
    print(json.dumps(await decide(heard, saying, question)))
    judge.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sys.argv[1:2] == ["selftest"]:
        sys.exit(1 if asyncio.run(selftest("--judge" in sys.argv)) else 0)
    elif sys.argv[1:2] == ["check"] and len(sys.argv) > 2:
        asyncio.run(_check_cli(sys.argv[2:]))
    else:
        sys.exit(__doc__)
