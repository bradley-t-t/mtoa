#!/usr/bin/env python3
"""The speech audit's repeat and double-heard rules, over the pairs they must raise and the
pairs they must not.

The rule has been lost once already - the mirror copy of 2026-10-06 09:20 was overwritten
by an older file from the Pi four hours later - and nothing noticed until the hourly run
mailed two false problems that night. This is what notices.

    selftest_audit.py
"""
import json
import sys
import tempfile
from pathlib import Path

import speech_audit

T = 1_000_000.0


def heard(at, qid, text, **kw):
    return dict(kind="heard", at=T + at, qid=qid, device="sunday-cube", text=text, **kw)


def said(at, qid, text, source="reply", device="sunday-cube", **kw):
    return dict(kind="said", at=T + at, qid=qid, device=device, source=source,
                sentences=[text], played=1, **kw)


LINE = "The overlay is on over your game until it ends."
NEWS = "Your Mac's speakers are unmuted at about seventy percent, so my voice is coming out there too."
TIME = "It's 10:16 on Thursday night."

CASES = [
    ("one announcement on two speakers", 0, [
        said(0, "q1", NEWS, source="say"),
        said(0.1, "q1", NEWS, source="say", device="mac"),
    ]),
    ("the same command twice, each acknowledged", 0, [
        heard(0, "q1", "Sunday, turn the overlay on."), said(1, "q1", LINE, source="command"),
        heard(500, "q2", "Sunday, turn the overlay on."), said(501, "q2", LINE, source="command"),
    ]),
    ("the same question twice, same answer", 0, [
        heard(0, "q1", "Describe the biggest moon of Saturn."), said(1, "q1", NEWS),
        heard(130, "q2", "Describe the biggest moon of Saturn."), said(131, "q2", NEWS),
    ]),
    ("one answer dispatched twice on one speaker", 1, [
        heard(0, "q1", "Describe the biggest moon of Saturn."),
        said(1, "q1", NEWS), said(2, "q1", NEWS),
    ]),
    ("an announcement said again unasked", 1, [
        said(0, "q1", NEWS, source="late"), said(22, "q2", NEWS, source="say"),
    ]),
    ("the same answer after a different question", 1, [
        heard(0, "q1", "What are my Mac's speakers doing?"), said(1, "q1", NEWS),
        heard(300, "q2", "What time does the roofer get here?"), said(301, "q2", NEWS),
    ]),
    # The mic writes the same ask down differently every time he makes it, and the clock
    # answer is the same inside one minute. Three asks of the time at 10:16 PM on
    # 2026-10-08 were mailed out as two repeats.
    ("the same question three times, the mic hearing it differently each time", 0, [
        heard(0, "q1", "Sunday. What time is it? Sunday. I don't think you answered me."),
        said(1, "q1", TIME),
        heard(15, "q2", "Sunday, Sunday, what time is it?"), said(16, "q2", TIME),
        heard(42, "q3", "Monday, what time is it?"), said(43, "q3", TIME),
        heard(70, "q4", "Sunday, what time is it? " * 8), said(71, "q4", TIME),
    ]),
    ("a short word before two different questions is not the same ask", 1, [
        heard(0, "q1", "Sunday, yes. What are my Mac's speakers doing?"), said(1, "q1", NEWS),
        heard(300, "q2", "Sunday, yes. What time does the roofer get here?"), said(301, "q2", NEWS),
    ]),
    # Asking what was said is asking for it again, and the answer is the line itself. The
    # 1:24:28 PM answer of 2026-10-15 to "Sunday, what did you say?" went out as a repeat.
    ("asked what was said, and heard the last line again", 0, [
        heard(0, "q1", "Sunday, how many previews have we sent today?"), said(1, "q1", NEWS),
        heard(60, "q2", "Sunday, what did you say?"), said(61, "q2", NEWS),
    ]),
    ("asked to repeat with something said in between", 0, [
        heard(0, "q1", "What are my Mac's speakers doing?"), said(1, "q1", NEWS),
        said(40, "q2", TIME, source="say"),
        heard(80, "q3", "Sunday, say that again."), said(81, "q3", NEWS),
    ]),
    ("asking what was said about something else is a question of its own", 1, [
        heard(0, "q1", "What are my Mac's speakers doing?"), said(1, "q1", NEWS),
        heard(300, "q2", "Sunday, what did you say about the roofer?"), said(301, "q2", NEWS),
    ]),
    ("asked again, but the first ask was never kept", 1, [
        heard(0, "q1", "What are my Mac's speakers doing?", discarded="not his voice"),
        said(1, "q1", NEWS, source="say"),
        heard(300, "q2", "What are my Mac's speakers doing?"), said(301, "q2", NEWS),
    ]),
]


# What the mic hands over twice, and what only looks like it. The rule reads the quiet after the
# first copy as the fault, so what the drop reason says about the line decides it: words the
# server had and held back are still him going unanswered, while a line it found nothing in is
# the room repeating itself and owed nothing. The 5:52 PM pair of 2026-10-15 was the second
# kind and went out as a fault at the hour.
HELP = "Sunday, put the overlay back up."
EAR_CASES = [
    ("heard twice with nothing said back", 1, [
        heard(0, "q1", HELP), heard(20, "q2", HELP),
    ]),
    ("heard twice with an answer in between", 0, [
        heard(0, "q1", HELP), said(1, "q1", LINE, source="command"), heard(20, "q2", HELP),
    ]),
    ("the room talking, dropped both times for having no words in it", 0, [
        heard(0, "q1", "Alex Doe.", discarded="under 3 words while computer audio playing"),
        heard(20, "q2", "Alex Doe.",
              discarded="no words: woken by a sound: its name is not in what it heard"),
    ]),
    # The gate dropping him is the other half of the rule, and it is the half worth keeping:
    # both of these are his words, held twice because the Mac was playing over him.
    ("his words held twice as not said to Sunday", 1, [
        heard(0, "q1", "Shorten the wait to five days.",
              discarded="not said to Sunday while computer audio playing"),
        heard(22, "q2", "Shorten the wait to five days.",
              discarded="not said to Sunday while computer audio playing"),
    ]),
    ("kept once, then held as not said to Sunday", 1, [
        heard(0, "q1", HELP),
        heard(20, "q2", HELP, discarded="not said to Sunday: the words read as overheard "
                                        "and the voice scored 0.18"),
    ]),
    ("an empty copy between two the server could have answered", 1, [
        heard(0, "q1", HELP),
        heard(10, "q2", HELP, discarded="no words: nothing heard"),
        heard(20, "q3", HELP),
    ]),
    ("its own voice, then answered", 1, [
        heard(0, "q1", NEWS, discarded="its own voice coming back through the mic"),
        heard(9, "q2", NEWS),
    ]),
    ("its own voice, caught both times", 0, [
        heard(0, "q1", NEWS, discarded="its own voice coming back through the mic"),
        heard(9, "q2", NEWS, discarded="its own voice"),
    ]),
    ("its own voice, then dropped for having no name in it", 0, [
        heard(0, "q1", NEWS, discarded="its own voice coming back through the mic"),
        heard(9, "q2", NEWS, discarded="no words: no name in the first seconds"),
    ]),
]


# The stand-in names that have reached the log, and the real ones that must not be
# mistaken for them. A harness replaying one line twice is the double-heard rule's exact
# shape, so a name slipping through the filter is a false problem mailed at the hour.
TEST_NAMES = ["fake-cube", "test-ear", "test-cube", "overlay-test", "e2e-test", "close-test"]
REAL_NAMES = ["sunday-cube", "mac", "wake-guard", "", None]


def check_devices(tmp):
    """load() keeps his traffic and drops every stand-in's."""
    bad = 0
    for name in TEST_NAMES + REAL_NAMES:
        want_kept = name in REAL_NAMES
        tmp.write_text(json.dumps({"kind": "heard", "at": T, "device": name, "text": "hello"}) + "\n")
        kept = bool(speech_audit.load([tmp], T - 1))
        ok = kept == want_kept
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  device {name!r}: {'kept' if kept else 'dropped'}, "
              f"wanted {'kept' if want_kept else 'dropped'}")
    return bad


def check(cases, kinds):
    bad = 0
    for name, want, rows in cases:
        rows.sort(key=lambda r: r["at"])
        got = [f for f in speech_audit.audit(rows, T - 1) if f[1] in kinds]
        ok = len(got) == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: {len(got)} raised, wanted {want}")
        for _, kind, text in got:
            print(f"        [{kind}] {text}")
    return bad


def main():
    bad = check(CASES, ("repeat", "late-relay"))
    bad += check(EAR_CASES, ("double-heard", "echo-leak"))
    with tempfile.TemporaryDirectory() as d:
        bad += check_devices(Path(d) / "speech.jsonl")
    print("all good" if not bad else f"{bad} case(s) wrong")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
