#!/usr/bin/env python3
"""Read the speech log and say whether Sunday has been repeating itself or answering
things the conversation had already moved past.

    speech_audit.py [--hours 1] [--log data/speech.jsonl] [--report data/speech-audit.md]

The voice server writes one JSON line per thing it heard ("heard") and per thing it
meant to say ("said": the full intended text, how many sentences played, and where
and why it was cut off). This reads the last few hours of both and flags:

  repeat       the same or nearly the same line said twice within half an hour,
               unasked for the second time - asked again counts however differently
               the mic wrote the second ask down, and asking what was said is asking
               for it again
  self-heard   a "heard" line the server kept that is Sunday's own recent speech picked
               up by the mic
  double-heard the same words heard twice within a minute, with no answer in between
  echo-leak    words dropped as Sunday's own voice, then heard again and answered
  stale        a reply that started playing after a newer question had been heard
  late-relay   a relayed reply spoken more than once

It writes a short report and ends with the routine status line the Pi's routines
use, so `routine-deliver.py` raises it through the usual attention path.
"""

import argparse
import difflib
import json
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


# One line sitting inside another is the same line only when it is most of it.
# Without this, a stock opener shared by two different answers, or his own words
# quoted back to him inside a long reply, counted as a match on containment alone.
CONTAINED_ENOUGH = 0.6


def similar(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    if len(a) < 25 or len(b) < 25:
        return a == b and len(a) >= 12
    short, long = sorted((a, b), key=len)
    if short in long and len(short) >= CONTAINED_ENOUGH * len(long):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


# Numbers are the part of a line that carries its fact, and they are one word against the
# dozens around them, so two lines differing only in a number score above any character
# ratio worth using: "This is the third speaker test. <eighty identical words>" and the
# fourth test are 0.96 similar and are not the same line. Only the repeat rules use this,
# because only they compare two lines Sunday meant to say, where the numbers are exact.
# What the mic wrote down is not - it turned "twenty eight" into "128" and "three forty
# seven" into "47" in the same breath - so the self-heard rule must keep matching across a
# mangled number, and goes on using `similar` alone.
NUMBERS = set("""zero one two three four five six seven eight nine ten eleven twelve thirteen
fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy
eighty ninety hundred thousand million billion first second third fourth fifth sixth seventh
eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth
eighteenth nineteenth twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth
ninetieth hundredth thousandth once twice half quarter""".split())


def numbers(text: str) -> list:
    """The numbers a line says, in the order it says them."""
    return [w for w in norm(text).split() if w in NUMBERS or any(c.isdigit() for c in w)]


def same_line(a: str, b: str) -> bool:
    """The same line said twice. A reply cut off part way through loses the numbers in the
    sentences it never reached, so the shorter run of numbers being the start of the longer
    one is still the same line; a different number in the same place is a different line."""
    na, nb = numbers(a), numbers(b)
    short, long = sorted((na, nb), key=len)
    return long[:len(short)] == short and similar(a, b)


# The wake word as it reaches the log, wider than the gate's own SUNDAY_LIKE because the
# gate fires on the audio and the transcript then spells the name however it likes. Local
# to the audit on purpose: reading the log more loosely must not widen what the gate wakes
# on. "Monday, what time is it?" was answered at 10:16:48 PM on 2026-10-08 and read here as
# a different question from "Sunday, Sunday, what time is it?" twenty-seven seconds before.
ASK_NOISE = re.compile(r"^(?:sunday|sundays|sunday's|sundae|sundaes|someday|sonday|sundy"
                       r"|monday|mondays|sunny|hey|ok|okay|yo|um|uh|so|and)$")


# Asking to hear something again. The guard already works this way - a request of his moves
# the starting point, so the line the request asks for is never held back at the speaker -
# and the audit has to draw the same line or every "what did you say?" goes out as a fault:
# "Sunday, what did you say?" at 1:24:28 PM on 2026-10-15 was answered with the previews
# count from eight minutes before, correctly, and mailed as a repeat. The ask has to be the
# whole of what he said once the wake word is off it, because "What did you say about the
# roofer?" is a question with an answer of its own rather than a request to hear the last
# one back, and a line said again off one of those is still a fault.
SAY_AGAIN = re.compile(r"^(?:(?:sorry|please|wait|again)\s+)*"
                       r"(?:(?:can|could|will|would)\s+you\s+)?(?:please\s+)?"
                       r"(?:what\s+(?:did|do)\s+you\s+(?:just\s+)?say"
                       r"|what\s+was\s+(?:that|it)"
                       r"|say\s+(?:that|it)\s+again"
                       r"|say\s+again"
                       r"|repeat\s+(?:that|it|yourself)"
                       r"|repeat"
                       r"|come\s+again"
                       r"|one\s+more\s+time"
                       r"|i\s+(?:didn't|did\s+not|couldn't|could\s+not)\s+(?:hear|catch)"
                       r"\s+(?:you|that|it|what\s+you\s+said)"
                       r"|i\s+missed\s+that)"
                       r"(?:\s+(?:again|please|one\s+more\s+time|for\s+me))*$")


def asked_to_repeat(text) -> bool:
    """He asked to hear the last thing again, rather than asking anything new."""
    words = norm(text).split()
    while words and ASK_NOISE.match(words[0]):
        words.pop(0)
    return bool(words) and bool(SAY_AGAIN.match(" ".join(words)))


def ask_core(text: str) -> list:
    """The questions inside something he said, with the wake word and the order taken out.
    The same ask reaches the log spelled differently every time he makes it - the name
    doubled, the name misheard, a complaint about the last answer tacked on the end, the
    whole question said eight times into one utterance - and every one of those is him
    asking the one thing again. Fragments shorter than a few words are dropped: "yes" or
    "thanks" said before two different questions is not the same ask."""
    parts = []
    for chunk in re.split(r"[.!?;,]+", text or ""):
        words = norm(chunk).split()
        while words and ASK_NOISE.match(words[0]):
            words.pop(0)
        k = " ".join(words)
        if len(k) >= 8 and k not in parts:
            parts.append(k)
    return parts


def was_echo(rec: dict) -> bool:
    """The server dropped this line because it recognised its own voice in it."""
    return str(rec.get("discarded", "")).startswith("its own voice")


def spoken_text(rec: dict) -> str:
    sentences = rec.get("sentences") or []
    played = rec.get("played", len(sentences))
    return " ".join(sentences[:played])


# A harness standing in for the cube names itself for what it is, and its traffic is not
# his, so none of the rules below should ever see it. tools/ask calls itself "fake-cube",
# but every other stand-in written since has picked its own name - "test-ear", "test-cube",
# "overlay-test", "e2e-test", "close-test" - and a prefix match on "fake" let all of them
# through. A replay harness says the same line twice and disconnects before either answer
# plays, which is exactly the shape of the double-heard rule: the 10:59:01 AM reading of
# "This is how you behave?" on 2026-10-09 was a test-ear replay, mailed out as a fault.
# Any name carrying "test" or "fake" as a word is a stand-in, wherever it sits in the name.
TEST_DEVICE = re.compile(r"(?:^|[^a-z0-9])(?:test|fake)(?:$|[^a-z0-9])")


def is_test_device(name) -> bool:
    return bool(TEST_DEVICE.search(str(name or "").lower()))


def load(paths, since):
    rows = []
    for p in paths:
        try:
            for line in p.read_text(errors="ignore").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("at", 0) >= since and not is_test_device(r.get("device")):
                    rows.append(r)
        except OSError:
            continue
    rows.sort(key=lambda r: r.get("at", 0))
    return rows


def when(ts: float) -> str:
    return time.strftime("%-I:%M:%S %p", time.localtime(ts))


def clip(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def audit(rows, window_start):
    said = [r for r in rows if r.get("kind") == "said" and r.get("played", 0) > 0 and r.get("source") != "filler"]
    # Filler is not a reply, but he hears it, so it is an answer for the purpose of
    # telling a repeat he got nothing for from one he did.
    answered_back = [r for r in rows if r.get("kind") == "said"
                     and (r.get("played", 0) > 0 or r.get("filler_played"))]
    heard = [r for r in rows if r.get("kind") == "heard"]
    found = []
    # What he said that started each turn, by qid. A line of his the server threw away
    # started nothing, so it is not an ask.
    asked_for = {}
    for h in heard:
        if h.get("qid") and not h.get("discarded") and norm(h.get("text")):
            asked_for.setdefault(h["qid"], h["text"])

    def flag(kind, at, text):
        if at >= window_start:
            found.append((at, kind, text))

    # Two lines carrying the same words are a fault only when the second one came out on
    # its own. Ask him the same thing twice and the same answer back is him being
    # consistent, which is the rule the guard itself keeps - it holds a line back only
    # until the next time he asks for it - and the same distinction the double-heard rule
    # below already draws. Both turns have to have started with words of his own that
    # match, so a doubled dispatch off one utterance keeps its shared qid and still
    # lands, and anything he never asked for - an announcement, a late answer, a relay -
    # is untouched by this. The words heard are the test rather than the "question"
    # field, because only a reply carries one: "Sunday, turn the overlay off" twice nine
    # minutes apart, each acknowledged, was reported as a repeat on 2026-10-06.
    # Whole transcripts are compared first and then their sentences, because two asks of the
    # same thing almost never reach the log as the same string: the three asks of the time at
    # 10:16 PM on 2026-10-08 came in as "Sunday. What time is it? Sunday. I don't think you
    # answered me.", "Sunday, Sunday, what time is it?" and "Monday, what time is it?", which
    # match each other nowhere near closely enough whole, and all three carry "what time is
    # it". The answer was the same because the minute was.
    def asked_again(a, b):
        if a.get("qid") == b.get("qid"):
            return False
        qa, qb = asked_for.get(a.get("qid")), asked_for.get(b.get("qid"))
        if not qa or not qb:
            return False
        if norm(qa) == norm(qb) or similar(qa, qb):
            return True
        return any(x == y or similar(x, y) for x in ask_core(qa) for y in ask_core(qb))

    # Said twice.
    for i, a in enumerate(said):
        for b in said[i + 1:]:
            if b["at"] - a["at"] > 1800:
                break
            # One announcement goes to every speaker that is connected and each of them
            # logs its own line, so one qid on two devices is one thing said in two rooms
            # rather than said twice. The 11:12 PM announcement of 2026-10-06 went out on
            # two speakers at once and was reported as a repeat of itself, to the second.
            if a.get("qid") == b.get("qid") and a.get("device") != b.get("device"):
                continue
            if same_line(spoken_text(a), spoken_text(b)):
                if asked_again(a, b) or asked_to_repeat(asked_for.get(b.get("qid"))):
                    continue
                kind = "late-relay" if a.get("source") == b.get("source") == "relay" else "repeat"
                flag(kind, b["at"], f'{when(b["at"])} said again ({b.get("source")}, first at {when(a["at"])}): '
                                    f'"{clip(spoken_text(b))}"')
                break
        # A sentence repeated inside one utterance.
        seen = set()
        for s in (a.get("sentences") or [])[: a.get("played", 0)]:
            k = norm(s)
            if len(k) >= 20 and k in seen:
                flag("repeat", a["at"], f'{when(a["at"])} one reply said the same sentence twice: "{clip(s)}"')
            seen.add(k)

    # Its own voice taken for his. A line the server itself threw away was never taken for
    # his, so the echo guard catching one is not a fault to raise: while the open ear was
    # going in, every announcement he tried came back through the other mic, was dropped as
    # echo exactly as intended, and was reported anyway.
    for h in heard:
        if h.get("discarded"):
            continue
        for s in said:
            if 0 <= h["at"] - s["at"] <= 90 and len(norm(h.get("text"))) >= 20 and similar(h.get("text"), spoken_text(s)):
                flag("self-heard", h["at"], f'{when(h["at"])} heard its own speech as a question: "{clip(h.get("text"))}"')
                break

    # The same words heard twice in a minute with nothing said back in between. The
    # silence is the whole signal: he repeats himself because the first try got no
    # answer, and that is either the gate dropping him or the pipeline handing the
    # same utterance over twice. Once he has been answered, saying it again is a
    # conversation - two tries at a command a minute apart, each answered
    # differently, is him steering, not a fault to raise at one in the morning.
    # The pair is only his if the first of it was. Words the server itself named as its own
    # voice were never him saying anything, so a second copy of them is not him repeating
    # himself - but it is still a fault, and a worse one, because the echo guard read the
    # identical words twice and changed its mind: the 4:45 PM leak of 2026-10-06 was dropped
    # at 4:45:10 as "its own voice coming back through the mic" and answered nine seconds
    # later with "I didn't catch that". Raised as what it is, so the fix is looked for in the
    # echo guard rather than in the gate. Caught both times, the guard did its job and there
    # is nothing to raise, the same reason the self-heard rule skips a discarded line.
    for i, a in enumerate(heard):
        for b in heard[i + 1:]:
            if b["at"] - a["at"] > 60:
                break
            if not norm(a.get("text")) or norm(a.get("text")) != norm(b.get("text")):
                continue
            if any(a["at"] <= s["at"] <= b["at"] for s in answered_back):
                break
            if was_echo(a):
                if not was_echo(b):
                    flag("echo-leak", b["at"], f'{when(b["at"])} answered words it had dropped as its own voice at '
                                               f'{when(a["at"])}: "{clip(b.get("text"))}"')
                break
            flag("double-heard", b["at"], f'{when(b["at"])} heard the same words twice in a minute with nothing '
                                          f'said back: "{clip(b.get("text"))}"')
            break

    # A reply that started after a newer question had come in.
    answered = [h for h in heard if not h.get("discarded")]
    for s in said:
        if s.get("source") not in ("reply", "followup") or not s.get("asked_at"):
            continue
        newer = [h for h in answered if s["asked_at"] < h["at"] < s["at"] and h.get("qid") != s.get("qid")]
        if newer:
            flag("stale", s["at"], f'{when(s["at"])} answered "{clip(s.get("question"), 60)}" after a newer question '
                                   f'("{clip(newer[-1].get("text"), 60)}" at {when(newer[-1]["at"])})')
    found.sort()
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--log", default=str(DATA / "speech.jsonl"))
    ap.add_argument("--report", default=str(DATA / "speech-audit.md"))
    a = ap.parse_args()

    now = time.time()
    window_start = now - a.hours * 3600
    log = Path(a.log)
    paths = [log] + sorted(log.parent.glob(log.name + ".*"))
    rows = load(paths, window_start - 1800)     # half an hour of lead-in for repeats
    found = audit(rows, window_start)

    said = sum(1 for r in rows if r.get("kind") == "said" and r["at"] >= window_start)
    heard = sum(1 for r in rows if r.get("kind") == "heard" and r["at"] >= window_start)
    cut = sum(1 for r in rows if r.get("kind") == "said" and r["at"] >= window_start and r.get("interrupted"))
    dropped = sum(1 for r in rows if r.get("kind") == "said" and r["at"] >= window_start
                  and (r.get("interrupted") or {}).get("cause") == "superseded")
    lines = [f"# Speech audit, {time.strftime('%Y-%m-%d %-I:%M %p', time.localtime(now))}", "",
             f"Last {a.hours:g} hour(s): {heard} heard, {said} said, {cut} cut off, {dropped} stale replies dropped "
             "before speaking.", ""]
    if found:
        kinds = {}
        for _, k, _ in found:
            kinds[k] = kinds.get(k, 0) + 1
        lines.append("Problems: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + ".")
        lines.append("")
        lines += [f"- [{k}] {t}" for _, k, t in found]
    else:
        lines.append("No repeats or stale answers.")
    report = "\n".join(lines) + "\n"
    try:
        Path(a.report).write_text(report)
    except OSError:
        pass
    print(report)
    if found:
        kinds = sorted({k for _, k, _ in found})
        print(f"ROUTINE-STATUS: attention Sunday's speech log shows {len(found)} problem(s) in the last "
              f"{a.hours:g}h ({', '.join(kinds)}); see {a.report}")
    else:
        print("ROUTINE-STATUS: ok")


if __name__ == "__main__":
    main()
