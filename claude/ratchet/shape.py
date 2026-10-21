#!/usr/bin/env python3
"""Shape: refuse a closing reply that is not in the reply shape.

Ratchet's scan reads what a reply says. This reads how it is laid out, because
the layout is what lets a reply drift. A free paragraph has room for "oh, and
three things I left", an announced omission, a step for the user to carry out;
a reply with a fixed set of slots has nowhere to put any of them. So the shape
is enforced rather than recommended:

  **<Verdict>.** <one sentence that answers what was asked>

  - **Why:** <why it was that way - only when something was wrong>
  - **What:** <what was done, or what the answer rests on>
  - **Touched:** <every file, branch, URL, setting or deletion it changed>
  - **Proof:** <how it is known - required on every finished verdict>

The verdict is the past tense of what was asked (Shipped, Fixed, Cleaned), a
Yes or No, the answer itself, or Started, in bold ending in a full stop. A
turn never ends waiting on the user: what needs them is asked inside the turn. The labelled lines are one line each, in that order, and only the
labels in shape.json; Proof is the one that may not be left out of a finished
verdict. Nothing goes before the verdict or after the last line. A bare "Yes."
or "No." is a whole reply.

Three stages, cheapest first.

  The layout is a parse and costs nothing. A reply in the shape is read for
  asides - the "by the way", the "you'll need to", the "left for later" - and a
  reply clear of both goes through. That is every compliant stop.

  A reply out of shape is excused when the user asked for another form in
  their own words. A slash command they typed this turn excuses it on sight,
  because the skill it runs sets its own output.

  Anything else goes to a headless Haiku with one closed question: did the user
  ask for this reply to take another form - a table, a draft, code, a long
  explanation. It has to quote the words, and the quote is checked against what
  the user actually wrote before the excuse is accepted, so a lenient judge
  cannot invent one. An excuse is kept for the rest of the turn.

A question that one or two sentences answer, in a turn that edited nothing, is
answered in exactly that: one or two plain sentences, no formatting, nothing
off the point (`plain`). Plain prose is also what narration looks like, and
nothing at display time says which it is, so a plain answer is written inside a
respond block and only its render is shown; bare prose is kept off the screen
and sent back to be written as a block (PROSE).

The same decision runs twice. The MessageDisplay hook sees each message before
it is drawn and keeps one out of shape off the screen; the Stop hook then sends
it back. Stop alone could not do the first part - it runs after the text is
already drawn - so every reply it held used to be seen twice.

Only a session a person is reading is shaped. A headless run (`claude -p`, the
Pi's services, ratchet's own judges) has a program reading its output, and the
shape would break it; those carry an sdk- entrypoint and are skipped, and so is
every subagent.

  shape.py check "<reply>"      what the layout check makes of a reply
  shape.py selftest             shape-corpus.json both ways
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

VERDICT = re.compile(r"^\*\*(?P<verdict>[^*\n]+?[.?!])\*\*(?:[ \t]+(?P<rest>\S.*))?$")
BULLET = re.compile(r"^- \*\*(?P<label>[^*\n]+?):\*\*[ \t]+(?P<body>\S.*)$")
LOOSE_LABEL = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*\*\*(?P<label>[^*\n]+?)(?::\*\*|\*\*:)")
RELAYED = ("Another Claude session sent a message", "<cross-session-message",
           "<task-notification", "<side-sessions-event", "<ci-monitor-event",
           "Stop hook feedback")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def rules():
    body = json.loads((ROOT / "shape.json").read_text())
    body["_asides"] = [(r["name"], re.compile(r["pattern"], re.I | re.M))
                       for r in body.get("asides") or []]
    return body


def clip(line, width=90):
    line = " ".join(line.split())
    return line if len(line) <= width else line[:width - 3] + "..."


ABBREVIATIONS = re.compile(r"\b(?:a\.m|p\.m|e\.g|i\.e|etc|vs|approx|incl|No|Mr|Ms|Dr|St)\.", re.I)
SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s+(?=[A-Z0-9\"'`(\[*])")


def sentences(text):
    """How many sentences a line holds. Code, abbreviations and file names are
    blanked first, because `config.json` or 9 p.m. is not where a sentence ends."""
    text = re.sub(r"`[^`]*`", "Code", text)
    text = ABBREVIATIONS.sub("abbr", text)
    return len([part for part in SENTENCE_END.split(text.strip()) if part.strip()])


def needs_proof(verdict, body):
    """A verdict that says work was finished, which nobody should take on trust."""
    word = verdict.rstrip(".?!").strip()
    if word.lower() in [w.lower() for w in body.get("proof_exempt") or []]:
        return False
    return bool(body.get("proof_verdicts") and re.match(body["proof_verdicts"], word, re.I))


def layout(text, body):
    """Where the reply leaves the shape: [(line, why)], empty when it holds it."""
    text = text.strip()
    if text in (body.get("bare") or []):
        return []
    lines = text.split("\n")
    out = []
    head = VERDICT.match(lines[0].strip())
    if not head:
        out.append((lines[0], "the first line is not a bold verdict ending in a full stop, "
                              "with the answer after it"))
    else:
        if len(head.group("verdict").split()) > int(body["verdict_max_words"]):
            out.append((lines[0], "the bold verdict runs past %d words - the detail "
                                  "belongs in the sentence after it" % body["verdict_max_words"]))
        rest = head.group("rest") or ""
        if rest and sentences(rest) > int(body.get("verdict_sentences") or 1):
            out.append((lines[0], "%d sentences after the verdict - it takes one, and the "
                                  "detail goes in the labelled lines, or whole in a Markdown file" % sentences(rest)))
        elif len(lines[0]) > int(body["verdict_line_max_chars"]):
            out.append((lines[0], "the verdict line runs past %d characters"
                                  % body["verdict_line_max_chars"]))
    if head and needs_proof(head.group("verdict"), body) and not re.search(
            r"(?m)^- \*\*%s:\*\*" % re.escape(body.get("proof_label") or "Proof"), text):
        out.append((lines[0], "\"%s\" is a finished verdict with no **%s:** line - say what you ran, "
                              "measured or saw that proves it" % (head.group("verdict"), body.get("proof_label") or "Proof")))
    if len(lines) == 1:
        return out
    rest = lines[1:]
    if rest[0].strip():
        out.append((rest[0], "a line straight under the verdict - the verdict is one "
                             "line, then a blank line, then the labelled lines"))
    else:
        rest = rest[1:]
    labels = list(body.get("labels") or [])
    seen = []
    for line in rest:
        if not line.strip():
            out.append(("(blank line)", "a second blank line - the labelled lines sit "
                                        "together, and the reply ends at the last one"))
            continue
        hit = BULLET.match(line.rstrip())
        if not hit:
            loose = LOOSE_LABEL.match(line)
            if loose and loose.group("label").strip() in labels:
                why = "a labelled line not written as `- **%s:** ...`" % loose.group("label").strip()
            else:
                why = ("not a labelled line - after the verdict the reply is only %s "
                       "lines, nothing else" % ", ".join("`- **%s:**`" % l for l in labels))
            out.append((line, why))
            continue
        label = hit.group("label").strip()
        if label not in labels:
            out.append((line, "\"%s\" is not one of the labels (%s)" % (label, ", ".join(labels))))
            continue
        if label in seen:
            out.append((line, "\"%s\" appears twice" % label))
            continue
        if seen and labels.index(label) < labels.index(seen[-1]):
            out.append((line, "out of order - the labels run %s" % " / ".join(labels)))
        seen.append(label)
        said = hit.group("body")
        if sentences(said) > int(body.get("line_sentences") or 2):
            out.append((line, "%d sentences - a labelled line holds %d at most"
                              % (sentences(said), int(body.get("line_sentences") or 2))))
        elif len(line) > int(body["line_max_chars"]):
            out.append((line, "runs past %d characters - one line says one thing"
                              % body["line_max_chars"]))
    return out


def skeleton(text):
    """A bold verdict first, then only labelled lines."""
    lines = [line.rstrip() for line in text.strip().split("\n")]
    rest = [line for line in lines[1:] if line.strip()]
    return bool(VERDICT.match(lines[0].strip()) and rest
                and all(BULLET.match(line) for line in rest))


def narration(text, body):
    """Plain prose short enough to be a running commentary: one paragraph, no
    list, table or code, and no more sentences than a progress note takes."""
    text = text.strip()
    if "\n\n" in text or "```" in text or re.search(r"(?m)^\s*(?:[-*+|]|\d+[.)])\s", text):
        return False
    return sentences(text) <= int(body.get("narration_sentences") or 3) and len(text) <= 500


PARTIAL = ("a partial result is not a reply. Finish the work and prove it yourself; where it "
           "needs the user's approval, a decision or a credential, ask them with the "
           "AskUserQuestion tool now, get the answer and finish - a turn never ends waiting on them")
WAITING = ("hands the user something to approve, decide or do, and a turn never ends waiting "
           "on them. Ask it with the AskUserQuestion tool now, inside the turn, get the answer, "
           "and finish the work - then reply with what was done")


def waiting(text, body):
    """A reply that ends the turn on the user: an approval, a decision, their hands."""
    import ratchet
    if not body.get("waiting_claims"):
        return []
    prose = ratchet.strip_code(text)
    m = re.search(body["waiting_claims"], prose, re.I)
    return [(ratchet.sentence_around(prose, m.start(), m.end()), WAITING)] if m else []


def partial(text, body):
    """A reply that reports the work as part done - in the verdict, or in a claim
    that it is not yet proven. Nothing partly finished is a finished reply. A Yes
    or No answers a question about something else's state, so its sentence may
    say what has not happened yet; its verdict still may not be partial."""
    head = VERDICT.match(text.strip().split("\n")[0])
    if not head:
        return []
    verdict = head.group("verdict").rstrip(".?!").strip()
    out = []
    if verdict.lower() in [h.lower() for h in body.get("honest_verdicts") or []]:
        # "Not fixed." and "Not verified." say what is true when the work could
        # not be finished or proven, and the reply that tells the truth is never
        # the one sent back - a false "Fixed." is what gate.py stops.
        return out
    if body.get("partial_verdicts") and re.search(body["partial_verdicts"], verdict, re.I):
        out.append((text.strip().split("\n")[0], "\"%s\" is a partial verdict - %s" % (verdict, PARTIAL)))
        return out
    if verdict.lower() in ("yes", "no") or not body.get("partial_claims"):
        return out
    import ratchet
    prose = ratchet.strip_code(text)
    m = re.search(body["partial_claims"], prose, re.I)
    if m:
        out.append((ratchet.sentence_around(prose, m.start(), m.end()), "not finished - " + PARTIAL))
    return out


def progress(text, body):
    """A verdict that says work is still going on, which no finished reply says."""
    head = VERDICT.match(text.strip().split("\n")[0])
    if not head:
        return None
    word = head.group("verdict").rstrip(".?!").strip().lower()
    return word if word in [w.lower() for w in body.get("progress_verdicts") or []] else None


def asides(text, body):
    """Sentences in an otherwise shaped reply that leave the topic: [(line, why)]."""
    import ratchet
    prose = ratchet.strip_code(text)
    out = []
    for name, pattern in body["_asides"]:
        for m in pattern.finditer(prose):
            out.append((ratchet.sentence_around(prose, m.start(), m.end()), name))
            break
    return out


def problems(text, body=None):
    # Both lists, every time: a hold that names only the layout gets a rewrite
    # that keeps the aside, and a second hold is a third copy on screen.
    body = body or rules()
    found = layout(text, body) + asides(text, body)
    found += partial(text, body)
    found += [w for w in waiting(text, body) if w[0] not in [f[0] for f in found]]
    word = progress(text, body)
    if word:
        found.append((text.strip().split("\n")[0], "\"%s\" is a progress note, and nothing is "
                      "shown until the work is finished - write the finished reply instead" % word.title()))
    return found


# --- who is reading, and what they asked for -------------------------------------

def state(session):
    """This check's own file. Ratchet keeps one record per session and every check
    that writes it replaces the last one's, so a shape count kept there would be
    reset by any other hold and an excuse forgotten by the next."""
    import ratchet
    return ratchet.state_root() / ("%s.shape.json" % re.sub(r"[^A-Za-z0-9_-]", "_", session or "anon"))


def buffered(session, message, delta, final):
    """A terminal's finished-line batches for one message, joined. Cleared on the last."""
    path = state(session).with_suffix(".display")
    try:
        held = json.loads(path.read_text())
    except (OSError, ValueError):
        held = {}
    text = (held.get("text", "") if held.get("message") == message else "") + delta
    try:
        if final:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"message": message, "text": text}))
    except OSError:
        pass
    return text


def load(path, turn):
    try:
        mark = json.loads(path.read_text())
    except (OSError, ValueError):
        mark = {}
    if not isinstance(mark, dict) or mark.get("turn") != turn:
        mark = {"turn": turn, "count": 0, "excused": None}
    return mark


def save(path, mark):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mark) + "\n")
    except OSError:
        pass


def entrypoint(transcript):
    found = os.environ.get("CLAUDE_CODE_ENTRYPOINT")
    if found:
        return found
    import ratchet
    for row in ratchet.rows(transcript):
        if row.get("entrypoint"):
            return row["entrypoint"]
    return ""


def skipped(transcript, body):
    point = entrypoint(transcript)
    return any(point.startswith(prefix) for prefix in body.get("skip_entrypoints") or [])


def own_words(row):
    """A user row's own words, or None when the row is not the person talking."""
    import ratchet
    if not ratchet.real_user_turn(row):
        return None
    text = "\n".join(ratchet.parts(row, "text"))
    text = SYSTEM_REMINDER.sub(" ", text).strip()
    if not text or text.startswith(RELAYED) or any(m in text[:400] for m in RELAYED):
        return None
    return text


def words(transcript, turn, keep=6):
    """(this turn's row text, the person's last few messages oldest first)."""
    import ratchet
    said, current = [], ""
    for row in ratchet.rows(transcript):
        if row.get("isSidechain"):
            continue
        if row.get("uuid") == turn:
            current = "\n".join(ratchet.parts(row, "text"))
        text = own_words(row)
        if text:
            said.append(text)
    return current, said[-keep:]


def squash(text):
    return " ".join(re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', text)).lower().split())


def command(transcript, turn):
    """The slash command the user typed this turn, or None. The cheap, judge-free
    half of the slash-command excuse, so the truth-judges (premature, unchecked,
    recall) can stand down on a /command turn the way the shape check already
    does - the skill sets its own output and has run its own verification, and
    re-judging its summary only churns a rewrite the display hook shows twice."""
    current, _ = words(transcript, turn)
    m = re.search(r"<command-name>\s*(/[^<\s]+)", current or "")
    return m.group(1) if m else None


def excused(transcript, turn, text, cfg):
    """(True, why) only when the user ran a slash command this turn whose skill
    sets its own output, else (False, None). Nothing else excuses a reply. A
    request for a table, a list, a draft or a long explanation does not buy
    freeform prose in the reply itself: the reply stays in the shape, and the
    long-form content it asked for is handed over as a file."""
    current, _ = words(transcript, turn)
    command = re.search(r"<command-name>\s*(/[^<\s]+)", current or "")
    if command:
        return True, "the user ran %s this turn" % command.group(1)
    return False, None


EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def changed_this_turn(transcript, turn):
    """Whether this turn edited a file. A plain answer is for a question; a turn
    that changed something owes the shape, with what it touched and the proof."""
    import ratchet
    seen = turn is None
    for row in ratchet.rows(transcript):
        if not seen:
            seen = row.get("uuid") == turn
            continue
        if row.get("type") == "assistant" and not row.get("isSidechain"):
            if any(part.get("name") in EDIT_TOOLS for part in ratchet.parts(row, "tool_use")):
                return True
    return False


def plain(text, body, changed=False):
    """[] when the text is a valid plain answer, else why it is not.

    One or two sentences of unformatted prose, answering a question in a turn
    that changed nothing: no bold, list, heading, table or code block, no claim
    of work done, and nothing off the point."""
    text = text.strip()
    out = []
    if "\n" in text or "**" in text or "```" in text or re.search(r"(?m)^\s*(?:[-*+#|>]|\d+[.)])\s", text):
        return [(clip(text), "formatted")]
    count = sentences(text)
    if count > int(body.get("plain_sentences") or 2):
        out.append((clip(text), "%d sentences - a plain answer is %d at most"
                                % (count, int(body.get("plain_sentences") or 2))))
    if len(text) > int(body.get("plain_max_chars") or 320):
        out.append((clip(text), "runs past %d characters" % int(body.get("plain_max_chars") or 320)))
    if changed:
        out.append((clip(text), "this turn changed files, so the reply owes the shape - what it "
                                "touched and the proof - not a plain answer"))
    claim = body.get("plain_claims") and re.search(body["plain_claims"], text, re.I)
    if claim:
        out.append((clip(text), "\"%s\" reports work done, which takes the shape with its proof"
                                % claim.group(0)))
    cue = body.get("work_cue") and re.search(body["work_cue"], text, re.I)
    if cue:
        # "Let me check...", "I'll run...", "Checking..." reads exactly like a
        # one-sentence plain answer, so it slips past every other test and is
        # drawn as written. It is narration - work announced, not an answer - so
        # it fails plain() here and is held off the screen like any other.
        out.append((clip(text), "\"%s\" announces work in progress - narration, not an answer"
                                % cue.group(0)))
    return out + asides(text, body) + waiting(text, body)


def is_plain(text, transcript, turn, body=None):
    """Whether this reply is a valid plain answer in a turn that changed nothing.

    The work-judges (recall, premature, unchecked) stand down on it the way they
    stand down on a slash command. A plain answer claims no work and changed no
    file, so there is nothing for them to hold; leaving them armed only lets a
    judge misfire on a one-line answer. With them down, the only holds that can
    reach a plain answer are a refusal and a banned-phrase scan, and both rewrite
    it into DIFFERENT text - so the display hook can hide it with no risk of the
    identical resend the app drops, which is what the vanish was."""
    body = body or rules()
    return not plain(text, body, changed_this_turn(transcript, turn))


def same(a, b):
    import difflib
    a, b = squash(a), squash(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def approved(session, turn, text):
    mark = load(state(session), turn or "unknown")
    return any(same(text, seen) for seen in mark.get("approved") or [])


def approve(session, turn, text):
    path = state(session)
    mark = load(path, turn or "unknown")
    mark["approved"] = ((mark.get("approved") or []) + [text])[-3:]
    mark["resends"] = int(mark.get("resends") or 0) + 1
    save(path, mark)
    return mark["resends"]


def withhold(session, turn, text):
    """Note a reply the display hook kept off the screen, so the stop hook knows
    the user has not seen it."""
    path = state(session)
    mark = load(path, turn or "unknown")
    mark["withheld"] = ((mark.get("withheld") or []) + [text])[-3:]
    save(path, mark)


def withheld(session, turn, text):
    """Whether this reply is off the screen: kept off by the display hook, and
    neither cleared since nor shown under a release."""
    mark = load(state(session), turn or "unknown")
    if mark.get("released") or any(same(text, seen) for seen in mark.get("approved") or []):
        return False
    return any(same(text, seen) for seen in mark.get("withheld") or [])


def released(session, turn):
    return bool(load(state(session), turn or "unknown").get("released"))


def release(session, turn):
    """After three resends that never matched, show the next reply as written."""
    path = state(session)
    mark = load(path, turn or "unknown")
    mark["released"] = True
    save(path, mark)


def decide(text, transcript, session, turn, cfg, body=None):
    """What both hooks do with one reply, so the screen and the stop never disagree.

      ("show", None)        in shape, or not this check's to shape
      ("plain", approved)   a valid plain answer; shown only once the stop hook
                            has confirmed it ends the turn
      ("excused", why)      out of shape because the user asked for another form
      ("capped", count)     out of shape, but held as often as a turn allows
      ("hold", found, why)  out of shape: keep it off the screen and send it back

    The excuse and the judge's refusal are both kept for the turn, so the display
    hook (which sees the reply first) and the stop hook ask the judge once
    between them. The count is only ever moved by the stop hook.
    """
    body = body or rules()
    if skipped(transcript, body):
        return ("show", None)
    if text.strip() in (body.get("bare") or []):
        return ("show", None)
    if not plain(text, body, changed_this_turn(transcript, turn)):
        # A sentence or two of plain prose is also exactly what narration looks
        # like, and nothing at display time says which this is. The caller
        # tells them apart by the respond block: a plain answer rendered from
        # one is shown, and bare prose is held off the screen (ratchet's
        # shape_hold, PROSE) and sent back to be written as one.
        return ("plain", approved(session, turn, text))
    found = problems(text, body)
    if not found:
        return ("show", None)
    path = state(session)
    mark = load(path, turn or "unknown")
    count = int(mark.get("count") or 0)
    if count >= int(body.get("holds") or 5):
        return ("capped", count)
    if narration(text, body):
        # A sentence or two of plain prose out of shape is somebody talking
        # while they work, never a table or a draft anyone asked for, so no
        # excuse reaches it and no judge is paid to say so.
        return ("hold", found, None)
    if skeleton(text):
        # Built on the shape itself - a bold verdict and nothing but labelled
        # lines - so it took no other form and there is no excuse to ask about.
        # The judge's seconds were seconds the draft sat on screen before the
        # display hook could hide it, and the rewrite then drew it twice.
        return ("hold", found, None)
    if mark.get("excused"):
        return ("excused", mark["excused"])
    why = None
    if not mark.get("refused"):
        ok, why = excused(transcript, turn, text, cfg)
        if ok:
            mark["excused"] = why
            save(path, mark)
            return ("excused", why)
        if why is None:
            mark["refused"] = True
            save(path, mark)
    return ("hold", found, why)


def count_hold(session, turn):
    path = state(session)
    mark = load(path, turn or "unknown")
    mark["count"] = int(mark.get("count") or 0) + 1
    save(path, mark)


HOLD = """Shape: that reply was out of shape, so it was kept off the screen and the user has not seen it. Write the reply again, in the shape at the top of this turn's message, short enough to read at a glance.

%s
Cut nothing that answers the request. When it does not fit the shape - a summary, a plan, a list, an explanation - the whole answer goes in a Markdown file sent with SendUserFile, and the reply is the shape: the verdict, one sentence pointing to the file, and the file under Touched. Leave out only what was never asked for. Where a sentence named work you can still do, do it before you send the reply rather than mentioning it."""


# Read at the top of every message the person types, so the first draft is in
# the shape. The hold runs after a reply is already on screen - a Stop hook
# cannot take back text that has been shown - so a draft out of shape is seen
# twice, once wrong and once fixed, and the only real cure is the first draft.
REMINDER = """Every closing reply takes one of two forms. When this message asks for another form - a summary, a plan, a table, a list, a draft, a long explanation - or the answer holds more than the shape, the whole answer goes in a Markdown file sent with SendUserFile, and the reply is the shape pointing to it: **Summarized.** The whole plan is in the attached file. Nothing that answers the question is ever cut to fit.

1. A question that one or two sentences answer, in a turn that changes nothing: exactly that. One or two plain sentences, under %(plain)d words, no bold, no list - the answer itself and nothing beside it - written inside a respond block, which is what puts it on the screen as plain sentences:

```respond
plain: Gmail has refused every message from you@example.com since Tuesday, because Resend signs with its own domain and example.com's DMARC policy rejects that.
```

Prose outside a respond block or the shape below is never shown. A line of prose is drawn before anything says whether a tool call follows it, so narration and a short answer are the same until the block marks which is which.

2. Work that was done, or an answer that needs more: the shape. It looks exactly like this:

**Fixed.** Sunday reads forwards again after 9 p.m.

- **Why:** Piper loads a second speaker at 21:00 that reads right to left, and nothing in the config pinned speaker 0.
- **What:** I pinned Piper to speaker 0 and restarted the voice server once.
- **Touched:** `/data/sunday/config.json` on the cube, and the voice server on the Pi.
- **Proof:** At 9:14 p.m. I asked the cube "Sunday, what's two plus two" and it said "four" forwards.

And when nothing was wrong, Why is left out:

**Shipped.** Acme is live at 2026.41.24, and nothing on develop is waiting.

- **What:** Release 2026.41.24 already carried everything, so the release had nothing new to move.
- **Touched:** Local develop and main synced, and the merged fix/previews-fit-viewport branch deleted.
- **Proof:** example.com serves 2026.41.24 matching main, the Vercel deploy succeeded, and the console is clean.

Rules:
- The bold verdict is the past tense of what was asked - **Shipped.** **Fixed.** **Cleaned.** **Built.** **Done.** - or **Yes.** **No.**, the answer itself like **Kokoro af_heart.**, or **Started.** for work handed to a background agent. Never a clause: the "but", "with" or "except" goes in the plain sentence after it.
- A finished verdict - Fixed, Done, Shipped, Built - needs proof from ~/.claude/tools/proof.py. Before changing anything, record a check where the problem was seen that fails now (proof.py before), rerun it after the change (proof.py after <id>), and copy the evidence line it prints into Proof word for word. A test of your own code is not proof.
- When it cannot be proven or finished, the verdict is **Not verified.** or **Not fixed.**, with what was done. That is an allowed answer; a Fixed no check backs never is. No "partly", "mostly" or "likely".
- A turn never ends waiting on the user. When the work needs their approval, a decision, a credential or their own hands, ask with the AskUserQuestion tool inside the turn, get the answer, and finish - the reply reports what was done, never what they should approve or do.
- After the verdict, one plain sentence under %(sentence)d words that answers what was asked.
- Then Why, What, Touched and Proof, in that order, one line each: at most two sentences, under %(line)d words. No other label exists.
- Why only when something was wrong, or the answer turns on a reason. What is what was done, or what the answer rests on. Touched names every file, branch, URL, setting or deletion that changed, with anything that cannot be undone first, and is left out when nothing changed. Proof is required on every finished verdict, and is what you ran, measured or saw - not what should be true.
- Nothing before the verdict and nothing after the last line: no aside, no note about what was kept, skipped, left or noticed, no step for the user to take, no offer.
- When the facts do not fit, they go whole into a Markdown file sent with SendUserFile, never cut. A bare "Yes." or "No." is a whole reply.
- Write no text until the work is finished. There are no progress notes: anything written mid-turn is kept off the screen, and so is any reply the stop checks would send back. A reminder to say what you are doing is answered by finishing the work."""


# The rule the proof lines replaced, which is what PROOF_OFF=1 puts back.
UNPROVEN = """- Nothing partial is a reply. No "partly", "mostly", "likely", "not fixed", "not yet verified": finish the work and prove it yourself before writing anything."""


def reminder(body=None):
    body = body or rules()
    text = REMINDER
    if (os.environ.get("PROOF_OFF") or "").strip().lower() in ("1", "true", "yes", "on"):
        text = re.sub(r"- A finished verdict - Fixed.*?No \"partly\", \"mostly\" or \"likely\".",
                      lambda _: UNPROVEN, text, flags=re.S)
    return text % {"plain": int(body.get("plain_max_words") or 45),
                       "sentence": int(body.get("sentence_max_words") or 30),
                       "line": int(body.get("line_max_words") or 40)}


PROSE = """Shape: that reply was kept off the screen and the user has not seen it. Prose is only ever shown inside a respond block or in the shape, because a line of prose is drawn before anything says whether a tool call follows it, and narration looks exactly like a short answer.

%s
Send the answer again as a respond block, with nothing before or after it:

```respond
plain: <the answer, one or two sentences>
```

If the turn changed anything, or the answer needs more than two sentences, write it in the shape instead. Output a slash command's skill sets out for itself goes in the block as `form:`."""


RESEND = """Shape: that reply is a plain answer, and it is confirmed as the turn's final reply. Plain text is kept off the screen until this check has seen it end the turn, because a sentence or two of prose is exactly what narration looks like - so the user has not seen it yet.

Send the same reply again, word for word, with nothing before or after it. It will be shown this time."""


RESEND_WITHHELD = """Shape: that reply is cleared as the turn's final reply, but it was kept off the screen while it was being checked, so the user has not seen it yet.

Send the same reply again, word for word, with nothing before or after it. It will be shown this time."""


UNFINISHED = """Shape: that reply reports unfinished work, so it was kept off the screen and the user has not seen it.

%s
Go back to the work. Finish it and prove it yourself - run it, measure it, play the audio, load the page - and only then write the reply. Changing the verdict without changing the work is a false report, and a reply that still says the work is unfinished is held again.

If what is left genuinely needs the user - an approval, a decision, a credential only they hold, or their own hands on something - ask them with the AskUserQuestion tool now, inside this turn, get the answer, and finish the work. A turn never ends waiting on the user, and a reply never hands them a step."""


def quote(found):
    """The lines a hold was given on, as the hold quotes them."""
    return "".join('  "%s"\n      %s\n' % (clip(line), why) for line, why in found[:6])


def unfinished(found):
    """Whether the hold is about work left undone rather than about the layout."""
    return any(PARTIAL in why or WAITING in why for _, why in found)


def reason(found):
    # No copy of the shape itself: the prompt hook put it at the top of the
    # message this turn answers, and a hold is drawn in the conversation, so a
    # copy here was the same rules on screen twice.
    if unfinished(found):
        return UNFINISHED % quote(found)
    return HOLD % quote(found)


# --- selftest -------------------------------------------------------------------

def decide_test(corpus, body):
    """decide() end to end over a throwaway transcript and state, judge off.

    The layout tests above never touch the transcript helpers, and a name clash
    there once broke every excuse check while the selftest stayed green - the
    hook caught the exception and let every reply out of shape through."""
    import os
    import tempfile
    bad = []
    work = tempfile.mkdtemp()
    old = os.environ.get("RATCHET_STATE")
    os.environ["RATCHET_STATE"] = work
    try:
        path = os.path.join(work, "t.jsonl")
        with open(path, "w") as handle:
            handle.write(json.dumps({"type": "user", "uuid": "u1",
                                     "message": {"content": "give me a table of the units"}}) + "\n")
        cfg = {"judge": False, "model": "", "judge_timeout_seconds": 1}
        cases = [(t, ("show",)) for t in (corpus.get("pass") or [])[:6]]
        cases += [(t, ("hold",)) for t in (corpus.get("hold") or [])[:12] if plain(t, body)]
        cases += [(t, ("plain",)) for t in corpus.get("plain_pass") or []]
        cases += [("| Unit | State |\n| :-- | :-- |\n| relay | up |\n\nAll units are up.", ("hold",))]
        for text, want in cases:
            try:
                got = decide(text, path, "selftest", "u1", cfg, body)
            except Exception as problem:
                bad.append("DECIDE THREW on %s: %s: %s" % (clip(text, 50), type(problem).__name__, problem))
                continue
            if got[0] not in want:
                bad.append("DECIDE %s (want %s): %s" % (got[0], want[0], clip(text, 60)))
        # A reply the display hook kept off the screen stays unseen until it is
        # cleared or a release lets the next one show as written.
        reply = (corpus.get("pass") or ["**Done.** It works."])[0]
        withhold("selftest", "u2", reply)
        if not withheld("selftest", "u2", reply):
            bad.append("WITHHELD lost a reply the display hook kept off the screen")
        approve("selftest", "u2", reply)
        if withheld("selftest", "u2", reply) or not approved("selftest", "u2", reply):
            bad.append("WITHHELD still unseen after it was cleared for a resend")
        withhold("selftest", "u3", reply)
        release("selftest", "u3")
        if withheld("selftest", "u3", reply) or not released("selftest", "u3"):
            bad.append("WITHHELD still unseen after a release")
    finally:
        if old is None:
            os.environ.pop("RATCHET_STATE", None)
        else:
            os.environ["RATCHET_STATE"] = old
    return bad


def selftest():
    corpus = json.loads((ROOT / "shape-corpus.json").read_text())
    body = rules()
    bad = []
    for text in corpus.get("pass") or []:
        found = problems(text, body)
        if found:
            bad.append("HELD (should pass): %s\n    <- %s" % (clip(text, 70), found[0][1]))
    for text in corpus.get("hold") or []:
        if not problems(text, body):
            bad.append("PASSED (should hold): %s" % clip(text, 70))
    bad += decide_test(corpus, body)
    for text in corpus.get("plain_pass") or []:
        found = plain(text, body)
        if found:
            bad.append("PLAIN HELD (should pass): %s\n    <- %s" % (clip(text, 70), found[0][1]))
    for text in corpus.get("plain_hold") or []:
        if not plain(text, body):
            bad.append("PLAIN PASSED (should hold): %s" % clip(text, 70))
    if bad:
        print("\n".join(bad))
        print("\n%d failure(s)" % len(bad))
        return 1
    print("shape selftest: %d pass, %d hold, %d plain pass, %d plain hold, decide end to end, 0 wrong"
          % (len(corpus.get("pass") or []), len(corpus.get("hold") or []),
             len(corpus.get("plain_pass") or []), len(corpus.get("plain_hold") or [])))
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    if len(sys.argv) >= 2 and sys.argv[1] == "check":
        reply = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else sys.stdin.read()
        found = problems(reply)
        if not found:
            print("in shape")
        for line, why in found:
            print('"%s"\n    %s' % (clip(line), why))
        sys.exit(1 if found else 0)
    elif len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    else:
        print('usage: shape.py check "<reply>" | selftest')
