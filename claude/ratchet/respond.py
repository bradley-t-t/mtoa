#!/usr/bin/env python3
"""The reply as structured fields, rendered into the shape deterministically.

The model hands `respond` the parts of its reply and this renders them into the
one house shape. The format is produced by a template here, never written by the
model, so a reply cannot come out malformed. Rendering is deterministic - no
model and no judge touch it. Validation runs the rendered text back through the
stop hook's own checks (shape.py), so this layer and the stop hook can never
drift apart.

This is the core of the reply-as-a-tool system: fields in, a valid shaped reply
out, or the reasons it is not one yet. It is standalone - nothing is wired to it
until it is proven. `respond.py selftest` is the proof.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import shape  # noqa: E402

# The labelled lines, in the only order they may appear.
LABELS = ("why", "what", "touched", "proof")


def render(fields):
    """Fields -> the shaped reply text. Which of three forms is decided only by
    which fields are set, never by the model laying anything out:

      form   a requested table, draft, code or file, handed over verbatim
      plain  a one- or two-sentence answer to a question that changed nothing
      shape  a bold verdict line followed by the labelled lines
    """
    if fields.get("form"):
        return str(fields["form"]).strip()
    if fields.get("plain"):
        return str(fields["plain"]).strip()
    verdict = str(fields.get("verdict") or "").strip().rstrip(".").strip()
    answer = str(fields.get("answer") or "").strip()
    head = "**%s.** %s" % (verdict, answer) if answer else "**%s.**" % verdict
    lines = []
    for key in LABELS:
        val = fields.get(key)
        if val and str(val).strip():
            lines.append("- **%s:** %s" % (key.capitalize(), str(val).strip()))
    return head if not lines else head + "\n\n" + "\n".join(lines)


def validate(fields, body=None):
    """[] when respond can render a valid reply from these fields, else the reasons.

    The structure is correct by construction, so what is left to check is the
    content a template cannot fix: a real verdict, proof when the verdict is a
    finished one, no partial or narration wording, each line within its limits.
    It checks by rendering and running the result through shape.py - the same
    checks the stop hook runs - so respond and the stop hook cannot disagree.
    """
    body = body or shape.rules()
    text = render(fields)
    if not text.strip():
        return [("(empty)", "respond was given nothing to say")]
    if fields.get("form"):
        return []  # a requested form is handed over as-is
    if fields.get("plain") and not (fields.get("verdict") or fields.get("answer")):
        return shape.plain(text, body)
    # The verdict must be a single clean verdict, never a clause - a hole shape.py's
    # word count leaves open, and the exact mistake that slipped through before.
    verdict = str(fields.get("verdict") or "").strip().rstrip(".").strip()
    if not verdict:
        return [("(no verdict)", "a shape reply needs a verdict")]
    if (re.search(r"[,;:]|[-–—]|\b(?:but|with|except|though|although|however)\b",
                  verdict, re.I)):
        return [(verdict, "the verdict is a clause - the but, with or except goes in the sentence after it")]
    return shape.problems(text, body)


def respond(fields, body=None):
    """(rendered text, None) for a valid reply, else (None, problems)."""
    problems = validate(fields, body)
    if problems:
        return None, problems
    return render(fields), None


# --- selftest -------------------------------------------------------------------

CASES = [
    # (name, fields, should_pass)
    ("full shape", {"verdict": "Fixed", "answer": "Sunday reads forwards again after 9 p.m.",
                    "why": "A second speaker loaded at 21:00 read right to left and nothing pinned speaker 0.",
                    "what": "I pinned Piper to speaker 0 and restarted the voice server once.",
                    "proof": "At 9:14 p.m. the cube answered two plus two with four, forwards."}, True),
    ("no-why shipped", {"verdict": "Shipped", "answer": "Acme is live at 2026.41.24.",
                        "what": "The release already carried everything, so nothing new moved.",
                        "proof": "example.com serves 2026.41.24 matching main, console clean."}, True),
    ("bare yes", {"verdict": "Yes"}, True),
    ("plain answer", {"plain": "Gmail bounced it because the DMARC policy rejects Resend's signing domain."}, True),
    ("requested form", {"form": "| a | b |\n| - | - |\n| 1 | 2 |"}, True),
    # must fail:
    ("finished verdict, no proof", {"verdict": "Fixed", "answer": "It works now.",
                                    "what": "Changed the thing."}, False),
    ("partial verdict", {"verdict": "Mostly fixed", "answer": "Nearly there.",
                         "proof": "Ran it once."}, False),
    ("verdict is a clause", {"verdict": "Fixed, but the parser still lags",
                             "answer": "It mostly works.", "proof": "Ran it."}, False),
    ("plain, three sentences", {"plain": "One thing is true. A second thing is also true. And a third thing."}, False),
    ("empty", {}, False),
]


def selftest():
    body = shape.rules()
    bad = []
    for name, fields, should_pass in CASES:
        text, problems = respond(fields, body)
        passed = problems is None
        if passed != should_pass:
            got = "passed" if passed else ("held: %s" % (problems[0][1] if problems else "?"))
            bad.append("%s: wanted %s, %s" % (name, "pass" if should_pass else "hold", got))
    # the renderer must round-trip: a valid render re-validates clean
    text, _ = respond(CASES[0][1], body)
    if text and shape.problems(text, body):
        bad.append("round-trip: a rendered shape reply did not pass the stop checks")
    if bad:
        print("\n".join(bad))
        print("\n%d failure(s)" % len(bad))
        return 1
    print("respond selftest: %d cases, render + validate, round-trips through shape.py, 0 wrong"
          % len(CASES))
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    print('usage: respond.py selftest')
