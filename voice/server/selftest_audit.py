#!/usr/bin/env python3
"""The speech audit's repeat rule, over the pairs it must raise and the pairs it must not.

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


def main():
    bad = 0
    for name, want, rows in CASES:
        rows.sort(key=lambda r: r["at"])
        got = [f for f in speech_audit.audit(rows, T - 1) if f[1] in ("repeat", "late-relay")]
        ok = len(got) == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: {len(got)} raised, wanted {want}")
        for _, kind, text in got:
            print(f"        [{kind}] {text}")
    with tempfile.TemporaryDirectory() as d:
        bad += check_devices(Path(d) / "speech.jsonl")
    print("all good" if not bad else f"{bad} case(s) wrong")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
