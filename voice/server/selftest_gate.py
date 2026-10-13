#!/usr/bin/env python3
"""The audio gate, old prompt against new, over the lines it got wrong and the lines it
must keep rejecting. Each case run REPS times because the verdict is not stable."""
import subprocess, sys, re, pathlib, collections

REPS = 5
CTX_WAIT = ("On Wednesday the 87 who got their meeting ask on September 30th clear the week, then more clear every "
            "day after that, so the routine is back to ten a day from Wednesday on; if you'd rather not lose "
            "tomorrow, I can shorten the wait to five days.")
CTX_DONE = ("Done. The wait is now five days, and ninety four prospects qualify right now, so tomorrow's run has "
            "plenty for ten.")
CTX_STORY = "Every night for thirty years, Edda climbed the two hundred steps to light the lamp."

CASES = [
    ("his instruction, answering Sunday's own offer", "Shorten the wait to five days.", CTX_WAIT, True),
    ("his correction, mid-conversation", "You should not stop talking just to listen to me if you hear me "
     "speaking over you.", CTX_DONE, True),
    ("his follow-up", "Did you hear me? Are you shortening the wait to five days?", CTX_WAIT, True),
    ("narration, Sunday had just spoken", "Quaint little village nestled between the hills, there lived an old "
     "baker named Tom who woke before dawn", CTX_DONE, False),
    ("story dialogue", "and then the dragon turned to the knight and said, you shall not pass this bridge tonight",
     CTX_STORY, False),
    ("someone else's phone call", "I'm telling you, bro, that game is unplayable right now, they ruined the "
     "whole thing", CTX_DONE, False),
]

def prompt_of(path):
    src = pathlib.Path(path).read_text()
    m = re.search(r'class Gatekeeper.*?PROMPT = \((.*?)\n    \)\n', src, re.S)
    return eval("(" + m.group(1) + ")")

def verdict(prompt, text, ctx, with_ctx):
    asked = f"The assistant just said: {ctx}\n\nTranscript: {text}" if with_ctx and ctx else f"Transcript: {text}"
    out = subprocess.run(["claude", "-p", "--model", "claude-haiku-4-5", "--tools", "", "--setting-sources", "",
                          "--system-prompt", prompt, asked], capture_output=True, text=True, timeout=120).stdout.upper()
    return True if "OVERHEARD" not in out and "DIRECTED" in out else False if "OVERHEARD" in out else None

bad = 0
for label, path, with_ctx in (("live (no context)", sys.argv[1], False), ("patched (context)", sys.argv[2], True)):
    prompt = prompt_of(path)
    print(f"\n== {label} ==")
    for name, text, ctx, want in CASES:
        got = [verdict(prompt, text, ctx, with_ctx) for _ in range(REPS)]
        wrong = sum(1 for g in got if g is not want and g is not None)
        tally = collections.Counter("DIRECTED" if g else "OVERHEARD" if g is False else "timeout" for g in got)
        print(f"  {'ok  ' if not wrong else 'WRONG'} want {'DIRECTED ' if want else 'OVERHEARD'} "
              f"{dict(tally)}  {name}")
        if label.startswith("patched"):
            bad += wrong
print("\nall pass" if not bad else f"\n{bad} wrong verdicts on the patched prompt")
sys.exit(1 if bad else 0)
