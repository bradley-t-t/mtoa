"""Recall: when the user says this is work already done before, search the
transcripts before finishing or improvising.

"We do this all the time, see transcripts." The method already exists; fumbling
the filesystem to rediscover it is the waste this refuses. The trigger is a
recurrence cue in the user's own latest message this turn; the required move is
one transcript read this turn (search_session_transcripts, tools/usage.py, or
export/get/list of a session). Cue present and no such read yet holds the stop.

Deterministic on purpose, no judge. "Go look at how we did it" is not a thing to
weigh, it is a thing to do, so there is nothing for a model to talk itself out
of - the only way past the hold is to actually read the transcripts.
"""
import re

# Cues in the user's message that say this is established, recurring work and
# the record of how it was done already exists.
CUES = re.compile(r"""(?ix)
    \b(?:see|check|read|look\s+at)\s+(?:the\s+|your\s+|our\s+|my\s+)?(?:past\s+|old\s+|prior\s+|previous\s+)?transcripts?\b
  | \bwe\s+do\s+this\s+all\s+the\s+time\b
  | \bwe(?:'ve|\s+have)\s+done\s+this\s+(?:before|many\s+times|a\s+(?:lot|million\s+times))\b
  | \byou(?:'ve|\s+have)\s+done\s+this\s+before\b
  | \bwe\s+always\s+(?:do|did)\b
  | \blike\s+(?:we\s+did\s+)?last\s+time\b
  | \blike\s+(?:we\s+)?(?:always|usually)\s+do\b
  | \bthe\s+usual\s+way\b
  | \bsame\s+(?:as|way\s+as)\s+(?:before|last\s+time|always)\b
  | \b(?:just\s+)?like\s+before\b
  | \bevery\s+time\s+we\b
""")

# A transcript read this turn satisfies the cue.
READ = re.compile(r"search_session_transcripts|export_transcript|get_session\b|list_events|usage\.py\b", re.I)

HOLD = """Ratchet: the user said this is work already done before, and this turn never looked it up.

  "%s"

Search the transcripts for how it was done before you finish or improvise - search_session_transcripts with the names, files and terms that identify the work, then read the session that comes back. The record already exists; recovering it beats rediscovering it, and that is the whole reason this was pointed at the transcripts."""


def _scan(transcript):
    """(latest real user text this turn, [tool names called since it])."""
    import premature
    user_text, names = "", []
    for row in premature._rows(transcript):
        if row.get("isSidechain"):
            continue
        kind = row.get("type")
        content = (row.get("message") or {}).get("content")
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            parts = [p for p in content if isinstance(p, dict)]
        else:
            parts = []
        if kind == "user" and not row.get("isMeta"):
            txt = " ".join(p.get("text", "") for p in parts
                           if p.get("type") == "text").strip()
            if txt:               # a real user message starts a fresh turn
                user_text, names = txt, []
        elif kind == "assistant":
            for p in parts:
                if p.get("type") == "tool_use":
                    command = (p.get("input") or {}).get("command") if p.get("name") == "Bash" else ""
                    names.append(" ".join(filter(None, [p.get("name", ""), str(command or "")])))
    return user_text, names


def cue(transcript):
    m = CUES.search(_scan(transcript)[0])
    return m.group(0).strip() if m else None


def should_hold(transcript):
    user_text, names = _scan(transcript)
    if not CUES.search(user_text):
        return False
    return not any(READ.search(n or "") for n in names)


def reason(c):
    return HOLD % (c or "(see the message)")


def flags(text):
    """Cue detection on a bare string - for the CLI and selftest."""
    m = CUES.search(text or "")
    return m.group(0).strip() if m else None


def selftest():
    import json
    import pathlib
    corpus = json.loads((pathlib.Path(__file__).resolve().parent / "recall-corpus.json").read_text())
    bad = []
    for line in corpus.get("must_cue", []):
        if not flags(line):
            bad.append("MISSED (should cue): %s" % line)
    for line in corpus.get("must_not", []):
        hit = flags(line)
        if hit:
            bad.append("FALSE (should not cue, hit %r): %s" % (hit, line))
    if bad:
        print("\n".join(bad))
        print("\n%d failure(s)" % len(bad))
        return 1
    print("ok: %d must-cue, %d must-not" % (len(corpus.get("must_cue", [])), len(corpus.get("must_not", []))))
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        hit = flags(sys.argv[2])
        print("cue: %r" % hit if hit else "clear (no recurrence cue)")
    elif len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    else:
        print("usage: recall.py check \"<user message>\" | selftest")
