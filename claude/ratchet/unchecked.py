"""Unchecked: refuse a stop that tells the user something can't be done without
having gone and checked.

Ratchet's phrase scan reads a turn that admits it left work. Premature reads a
turn that states finished what is still running. This reads the third failure,
the one that opened the file: a turn that hands back "I can't reach it", "that's
unreachable", "I don't have access" when nothing this turn went and looked for a
route. The words may even turn out true; what the check tests is whether they
were earned - whether the turn tried before it said them.

Two stages, cheap first. A denial phrase in the closing text is a single regex
and costs nothing on a stop that has none, which is almost all of them. Only a
flagged stop pays for a headless Haiku answering one closed question: did the
turn actually probe for a route to the thing it says it cannot do, or is it
asserting incapacity from assumption. The transcript is what answers it, so the
judge reads the tool calls this turn made (premature.done_this_turn), not the
prose alone - the prose is exactly what is in doubt.

A standing exception rides through untouched, and it is the same one the whole
account runs on: a thing that cannot be done without the user's approval, a
credential only they hold, or an action the assistant must not take is a
decision, not a missing route, and was never this check's to hold. The judge is
told to pass those, and most never reach it because "I need your go-ahead"
carries no denial phrase to flag in the first place.
"""
import json
import re
import subprocess

# Closing-text phrases that hand the user back an inability to do what they
# asked. Over-captures on purpose: the judge separates an unchecked give-up from
# a checked dead end and from a refusal that is the user's own to lift.
DENIAL_PATTERNS = [
    r"i'?m unable to", r"i am unable to",
    r"i can'?t reach", r"i cannot reach",
    r"i can'?t access", r"i cannot access",
    r"i can'?t see\b", r"i cannot see\b",
    r"i can'?t connect to", r"i can'?t get to",
    r"i don'?t have access", r"i have no access", r"no access to",
    r"i have no route", r"there'?s no route", r"there is no route",
    r"no route to (?:it|that|the|your|them)",
    r"no way (?:for me )?to reach", r"there'?s no way (?:for me )?to",
    r"there is no way (?:for me )?to",
    r"i don'?t have a way to", r"i don'?t have any way to", r"i have no way to",
    r"\bunreachable\b", r"\bnot reachable\b",
    r"i can'?t do that from", r"i can'?t do that here", r"i can'?t do it from",
    r"from (?:this session|here|this mac|where i'?m running)[^.\n]{0,60}\bcan'?t\b",
    r"that'?s not something i can", r"that'?s outside what i can",
    r"i can'?t (?:do|fix|run|build|open|read|edit|change|pilot|drive) (?:that|this|it) (?:from|here|in this)",
]
DENIAL = re.compile("|".join("(?:%s)" % p for p in DENIAL_PATTERNS), re.I)

PROMPT = """You check one thing: whether a message tells the user that something they asked for cannot be done, cannot be reached, or is outside the assistant's access - when the assistant never actually checked whether it could.

The assistant's closing message:
<<<
%s
>>>

What the assistant actually did this turn before writing that - each tool call and the tail of its result:
%s

The claim is UNCHECKED when the message says the request can't be done, can't be reached, or isn't accessible, and nothing above is a genuine attempt to find a route to that thing: no command, no search, no connection test, no session or agent lookup, no credential check, no other probe aimed at it.

The claim is OK - do not flag it - when any of these hold:
- The turn above actually probed for a route and found none; the message is reporting a dead end it checked.
- The inability is the user's own to lift, not a missing route: it needs the user's approval or confirmation, a credential only the user holds, or it is an action the assistant must not take.
- The message is not telling the user the request is impossible: it asks a question, describes work underway, or reports a result.
- The thing called impossible is not what the user asked for.

Answer with JSON only: {"verdict": "unchecked" or "ok", "claim": "<the sentence that denies the request, or empty>"}"""

HOLD = """Ratchet: this hands back that something can't be done, but nothing this turn went and checked.

  "%s"

A blocked path is a route to find, not an answer to hand back. Before you say you can't, look for the route the request actually needs and try it - the other browser, ssh and the tailnet, ListAgents for a session on the machine, a tool search for a connector, the web. Then do the thing. Where the only route left runs through the user - their approval, their login, their hands - ask them with the AskUserQuestion tool now, get the answer and finish; a turn never ends waiting on them. The one path that is never yours to route around is the user's own approval: a write that needs their yes still needs it, and it is asked for inside the turn."""


def flags(text):
    """The cheap first stage: a denial phrase in the closing text, or None."""
    m = DENIAL.search(text or "")
    return m.group(0) if m else None


def ask(text, transcript, cfg, binary):
    """('unchecked', claim) | ('ok', '') | (None, problem)."""
    import premature
    import ratchet
    did = premature.done_this_turn(transcript) or "(nothing - this turn made no tool calls)"
    try:
        raw, _ = ratchet.run_claude(binary, PROMPT % (text[:8000], did), cfg)
    except (subprocess.TimeoutExpired, OSError) as problem:
        return None, str(problem)
    try:
        envelope = json.loads(raw)
        raw = envelope.get("result", raw) if isinstance(envelope, dict) else raw
    except ValueError:
        pass
    m = re.search(r"\{.*\}", raw if isinstance(raw, str) else json.dumps(raw), re.S)
    if not m:
        return None, "judge answered in prose"
    try:
        answer = json.loads(m.group(0))
    except ValueError:
        return None, "judge answered with malformed JSON"
    verdict = str(answer.get("verdict", "")).lower()
    if verdict not in ("unchecked", "ok"):
        return None, "judge answered %r" % verdict
    return verdict, str(answer.get("claim", "")).strip()


def reason(claim):
    return HOLD % (claim or "(see the closing text)")


# --- selftest over the deterministic first stage -------------------------------

def selftest():
    import pathlib
    corpus = json.loads((pathlib.Path(__file__).resolve().parent / "unchecked-corpus.json").read_text())
    bad = []
    for line in corpus.get("must_flag", []):
        if not flags(line):
            bad.append("MISSED (should flag): %s" % line)
    for line in corpus.get("must_not", []):
        hit = flags(line)
        if hit:
            bad.append("FALSE (should not flag, hit %r): %s" % (hit, line))
    if bad:
        print("\n".join(bad))
        print("\n%d failure(s)" % len(bad))
        return 1
    print("ok: %d must-flag, %d must-not" % (len(corpus.get("must_flag", [])), len(corpus.get("must_not", []))))
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        hit = flags(sys.argv[2])
        print("flagged: %r" % hit if hit else "clear (no denial phrase)")
    elif len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    else:
        print("usage: unchecked.py check \"<closing text>\" | selftest")
