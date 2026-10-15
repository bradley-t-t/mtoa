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


HEAD = re.compile(r"^\*\*(?P<verdict>[^*]+?)\.\*\*(?:\s+(?P<answer>.*))?$")
BULLET = re.compile(r"^-\s+\*\*(?P<label>Why|What|Touched|Proof):\*\*\s*(?P<value>.*)$", re.I)

# A respond block: ```respond, then `key: value` lines, then the same fence. A
# longer fence (````respond) lets a form carry a code block of its own. A line
# that starts no key continues the one above it, and a form runs to the fence.
FENCE = re.compile(r"(?ms)^(?P<fence>`{3,})respond[ \t]*\n(?P<body>.*?)^(?P=fence)[ \t]*$")
KEY = re.compile(r"^\s*(verdict|answer|why|what|touched|proof|plain|form)\s*:[ \t]?(.*)$", re.I)


def block_fields(text):
    """The fields of the last respond block in the text, or None when it has none."""
    found = None
    for found in FENCE.finditer(text or ""):
        pass
    if found is None:
        return None
    fields, key = {}, None
    for line in found.group("body").splitlines():
        hit = None if key == "form" else KEY.match(line)
        if hit:
            key = hit.group(1).lower()
            fields[key] = hit.group(2).rstrip()
        elif key:
            fields[key] += "\n" + line
    fields = {k: v.strip() for k, v in fields.items() if v.strip()}
    return fields or None


def block(text):
    """The reply a respond block renders to, or None when the text carries none.

    This is how a plain answer reaches the screen. Bare prose is never shown,
    because when it is drawn nothing yet says whether a tool call follows it -
    a one-line answer and a line of narration are the same bytes. A respond
    block is never narration, so the display hook draws its render and hides
    everything else."""
    fields = block_fields(text)
    return render(fields) if fields else None


def parse(text):
    """A reply's text back into fields, or None when it is not a reply we can
    canonicalise - narration, or anything out of shape. Reads three forms: a
    fenced ```respond block of `key: value` lines, the shaped reply (a bold
    verdict line and labelled bullets), and a bare one- or two-sentence plain
    answer. The parser is the one bridge from prose to fields, so the renderer
    and the stop hook can meet on the same structure."""
    text = (text or "").strip()
    if not text:
        return None
    if FENCE.search(text):
        return block_fields(text)
    lines = text.split("\n")
    head = HEAD.match(lines[0].strip())
    if head:
        fields = {"verdict": head.group("verdict").strip(),
                  "answer": (head.group("answer") or "").strip()}
        for line in lines[1:]:
            b = BULLET.match(line.strip())
            if b:
                fields[b.group("label").lower()] = b.group("value").strip()
        return fields
    # No verdict line: a bare plain answer is the only other reply we render.
    if "**" not in text and "```" not in text and not re.search(r"(?m)^\s*(?:[-*+#|>]|\d+[.)])\s", text):
        return {"plain": text}
    return None


def canonical(text, body=None):
    """(rendered, None) when this reply canonicalises to a valid shaped or plain
    reply, else (None, problems). The live bridge: the display hook shows the
    render, and the stop hook refuses to end a turn whose reply does not
    canonicalise - so the screen only ever carries respond's own output."""
    fields = parse(text)
    if fields is None:
        return None, [(shape.clip(text), "not a shaped or plain reply - nothing to render")]
    return respond(fields, body)


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
    # canonical() must be the identity on an already-valid reply, so a correctly
    # shaped reply is never mangled when it passes through the live path.
    for name, fields, should_pass in CASES:
        if not should_pass or fields.get("form"):
            # A requested form is authored as a field and handed over verbatim;
            # raw form content is not parseable prose, so it does not round-trip
            # - which is why an inline form belongs in a file, not the reply.
            continue
        rendered = render(fields)
        back, probs = canonical(rendered, body)
        if probs:
            bad.append("canonical held a valid reply (%s): %s" % (name, probs[0][1]))
        elif back != rendered:
            bad.append("canonical changed a valid reply (%s):\n  %r\n  %r" % (name, rendered, back))
    # a hand-written shaped reply parses and validates
    hand = ("**Fixed.** It reads forwards again.\n\n"
            "- **What:** I pinned speaker 0.\n"
            "- **Proof:** The cube said four, forwards.")
    if canonical(hand, body)[1]:
        bad.append("canonical held a hand-written valid shaped reply")
    # narration and out-of-shape prose must not canonicalise
    for junk in ("Let me check the config and wire it in.",
                 "Okay, now I'll run the tests, then sync, then report back to you."):
        if canonical(junk, body)[0] is not None:
            bad.append("canonical rendered narration as a reply: %r" % junk)
    # a respond block renders; bare prose and a shaped reply carry none
    answer = "Gmail bounced it because the DMARC policy rejects Resend's signing domain."
    blocks = [
        ("plain block", "```respond\nplain: %s\n```" % answer, answer),
        ("prose before the block is dropped", "Here it is.\n\n```respond\nplain: %s\n```" % answer, answer),
        ("a value runs onto the next line", "```respond\nplain: Gmail bounced it because the DMARC\npolicy rejects Resend's signing domain.\n```",
         "Gmail bounced it because the DMARC\npolicy rejects Resend's signing domain."),
        ("shape block", "```respond\nverdict: Yes\n```", "**Yes.**"),
        ("form carries its own code fence", "````respond\nform: Run this:\n```bash\nls\n```\n````",
         "Run this:\n```bash\nls\n```"),
        ("the last block is the reply", "```respond\nplain: first\n```\n```respond\nplain: %s\n```" % answer, answer),
    ]
    for name, text, want in blocks:
        got = block(text)
        if got != want:
            bad.append("block (%s): wanted %r, got %r" % (name, want, got))
    for name, text in (("bare prose", answer), ("narration", "Confirmed. Now the fix - compute turn in display():"),
                       ("shaped reply", hand)):
        if block(text) is not None:
            bad.append("block rendered %s, which carries no respond block" % name)
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
