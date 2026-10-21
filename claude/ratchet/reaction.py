#!/usr/bin/env python3
"""Reaction: notice when a reply missed, remember it, and ask before changing how replies are made.

The user's next message is the best judge of the last reply. "I didn't tell you
to do anything", "that is not a good summary?", "I just said without users",
asking the same thing again, a turn that needed holds to get anything out -
each says the reply system got something wrong, and none of it was read. The
same miss came back in the next chat, and the next, and the user had to say it
again every time.

Three parts, all deterministic.

  The prompt hook reads the message for those signals - the cues in
  reaction.json, the same question asked again, the last turn held twice or
  more - and when there is one, the turn starts with it in front of it: what
  missed, what the user said, and the same misses from the last two weeks on
  this machine. The turn answers what the user wanted, then works out what in
  the reply system made the reply miss and, when a change would make the next
  one right, asks with AskUserQuestion under the header "Adapt" and applies
  what is picked.

  Every miss is kept in state/ratchet/misses.jsonl, with what the user said and
  the reply it answered.

  A miss of a kind in `ask` that has happened before and was never followed by
  a change makes the question required: the stop hook holds a reply on that
  turn until an Adapt question has been answered, or the turn has changed the
  reply system itself. Then every open miss of that kind is marked adapted, and
  the count starts again.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
SYSTEM = [HOME / ".claude" / p for p in ("ratchet", "standards", "CLAUDE.md", "settings.json", "profile/CLAUDE.md")]
PASTED = re.compile(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", re.S)
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)

CONTEXT = """Your last reply may have missed. %s: "%s"

Before anything else, work out what the user actually wanted and answer that. Then find what in the reply system made the last reply miss - a rule in the reply forms below, a hold that pushed the rewrite, a check that let it through. When a change would make the next reply right, ask with AskUserQuestion, header "Adapt", with the concrete change as the options, and apply what is picked."""

HISTORY = """

The same kind of miss, earlier, with nothing changed since:
%s"""

MUST = """

It has happened before, so this turn asks that Adapt question before it ends: the stop hook holds a reply on this turn until one has been answered, or the reply system itself has been changed."""

HOLD = """Reaction: the user's message said the last reply missed - %s - and the same kind of miss has happened before with nothing changed, so this reply was kept off the screen and the user has not seen it.

  "%s"

Find what in the reply system made it miss, then ask with AskUserQuestion, header "Adapt", with the concrete change as the options - and apply what is picked. Answer the user's message as well."""


def rules():
    return json.loads((ROOT / "reaction.json").read_text())


def off():
    return (os.environ.get("REACTION_OFF") or "").strip().lower() in ("1", "true", "yes", "on")


def state_root():
    sys.path.insert(0, str(ROOT))
    import ratchet
    return ratchet.state_root()


def clean(text):
    return " ".join(REMINDER.sub(" ", PASTED.sub(" ", text or "")).split())


def fingerprint(text):
    return hashlib.sha1(clean(text).lower().encode("utf-8")).hexdigest()[:16]


def clip(text, width=160):
    text = clean(text)
    return text if len(text) <= width else text[:width - 3].rstrip() + "..."


def words(text):
    return set(re.findall(r"[a-z0-9']+", clean(text).lower()))


def earlier(transcript, prompt):
    """(the reply the last turn ended on, the user's earlier messages, the last
    turn's stop holds). The message being answered may already be the last row,
    or not yet written; either way it is not one of the earlier ones."""
    sys.path.insert(0, str(ROOT))
    import ratchet
    turns = []
    for row in ratchet.rows(transcript) if transcript and Path(transcript).is_file() else []:
        if row.get("isSidechain"):
            continue
        if ratchet.real_user_turn(row):
            text = "\n".join(ratchet.parts(row, "text"))
            if clean(text):
                turns.append({"said": text, "reply": [], "holds": 0})
        elif not turns:
            continue
        elif row.get("type") == "assistant":
            tools = ratchet.parts(row, "tool_use")
            if tools:
                # A turn that ended on a tool call - a question card, a command -
                # ended on that, and that is what the user is reacting to.
                turns[-1]["reply"] = ["[%s]" % tools[-1].get("name", "a tool call")]
            else:
                kept = [t for t in turns[-1]["reply"] if not t.startswith("[")]
                kept.extend(t for t in ratchet.parts(row, "text") if t.replace("\u200b", "").strip())
                turns[-1]["reply"] = kept
        elif row.get("type") == "attachment":
            found = row.get("attachment") or {}
            if found.get("type") == "hook_blocking_error" and found.get("hookEvent") == "Stop":
                turns[-1]["holds"] += 1
    if turns and fingerprint(turns[-1]["said"]) == fingerprint(prompt):
        turns.pop()
    if not turns:
        return "", [], 0
    return "\n".join(turns[-1]["reply"]).strip(), [t["said"] for t in turns], turns[-1]["holds"]


def signals(prompt, said, holds, body=None):
    """The kinds of miss this message signals, in reaction.json order."""
    body = body or rules()
    text = clean(prompt)
    kinds = [kind for kind, pattern in (body.get("cues") or {}).items() if re.search(pattern, text, re.I)]
    mine = words(text)
    if len(mine) >= 4:
        for before in said[-3:]:
            theirs = words(before)
            if len(theirs) >= 4 and len(mine & theirs) / len(mine | theirs) >= float(body.get("reask_overlap") or 0.6):
                kinds.append("re-asked")
                break
    if holds >= int(body.get("held_turn") or 2):
        kinds.append("held")
    return kinds


def ledger():
    return state_root() / "misses.jsonl"


def misses():
    out = []
    try:
        for line in ledger().read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def write(found, body=None):
    body = body or rules()
    cutoff = time.time() - 86400 * int(body.get("keep_days") or 60)
    found = [m for m in found if float(m.get("at") or 0) >= cutoff]
    path = ledger()
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_suffix(".tmp%d" % os.getpid())
    spare.write_text("".join(json.dumps(m) + "\n" for m in found))
    os.replace(spare, path)


def open_misses(kinds, body=None, before=None):
    """Earlier misses of these kinds within recur_days that nothing was changed for."""
    body = body or rules()
    cutoff = time.time() - 86400 * int(body.get("recur_days") or 14)
    return [m for m in misses()
            if float(m.get("at") or 0) >= cutoff and not m.get("adapted")
            and (before is None or float(m.get("at") or 0) < before)
            and set(m.get("kinds") or []) & set(kinds)]


def note(session, transcript, prompt):
    """For the prompt hook: the context to put in front of the turn, or None.
    Records the miss and whether the turn owes an Adapt question."""
    if off():
        return None
    body = rules()
    reply, said, holds = earlier(transcript, prompt)
    if not said:
        return None
    kinds = signals(prompt, said, holds, body)
    if not kinds:
        return None
    now = time.time()
    asked = [k for k in kinds if k in (body.get("ask") or [])]
    history = open_misses(asked or kinds, body, before=now)
    must = bool(asked and open_misses(asked, body, before=now))
    found = misses()
    found.append({"at": now, "session": session, "fp": fingerprint(prompt), "kinds": kinds,
                  "said": clip(prompt, 240), "reply": clip(reply, 240), "must": must, "adapted": None})
    write(found, body)
    text = CONTEXT % (", ".join(kinds), clip(prompt, 240))
    if history:
        text += HISTORY % "\n".join('  - %s, %s: "%s"' % (time.strftime("%b %-d", time.localtime(m["at"])),
                                                           ", ".join(m.get("kinds") or []), m.get("said") or "")
                                    for m in history[-3:])
    if must:
        text += MUST
    return text


def this_turn(transcript, turn):
    """(this turn's miss record, the Adapt answers this turn, whether it edited the reply system)."""
    sys.path.insert(0, str(ROOT))
    import ratchet
    current, seen, asks, answers, edited = "", turn is None, {}, [], False
    for row in ratchet.rows(transcript):
        if row.get("isSidechain"):
            continue
        if not seen:
            if row.get("uuid") == turn:
                seen, current = True, "\n".join(ratchet.parts(row, "text"))
            continue
        for part in ratchet.parts(row, "tool_use"):
            given = part.get("input") or {}
            if part.get("name") == "AskUserQuestion":
                if any(str(q.get("header") or "").strip().lower() == "adapt" for q in given.get("questions") or []):
                    asks[part.get("id")] = True
            if part.get("name") in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                path = Path(os.path.expanduser(str(given.get("file_path") or given.get("notebook_path") or "")))
                if any(str(path).startswith(str(s)) for s in SYSTEM):
                    edited = True
        for part in ratchet.parts(row, "tool_result"):
            if part.get("tool_use_id") in asks:
                content = part.get("content")
                answers.append(content if isinstance(content, str) else json.dumps(content))
    fp = fingerprint(current)
    record = next((m for m in reversed(misses()) if m.get("fp") == fp), None) if current else None
    return record, answers, edited


def settle(record, how):
    """Mark this miss, and every open miss sharing an ask kind, as adapted."""
    body = rules()
    kinds = set(record.get("kinds") or []) & set(body.get("ask") or []) or set(record.get("kinds") or [])
    found = misses()
    for m in found:
        if m.get("adapted"):
            continue
        if m.get("fp") == record.get("fp") or (set(m.get("kinds") or []) & kinds):
            m["adapted"] = {"at": time.time(), "how": clip(how, 240)}
    write(found, body)


def check(transcript, turn, dry=False):
    """For the stop hook: None to let the reply through, or the hold reason."""
    if off():
        return None
    record, answers, edited = this_turn(transcript, turn)
    if not record:
        return None
    if answers or edited:
        if not dry and not record.get("adapted"):
            settle(record, answers[-1] if answers else "changed the reply system")
        return None
    if not record.get("must") or record.get("adapted"):
        return None
    return HOLD % (", ".join(record.get("kinds") or []), record.get("said") or "")


# --- selftest -------------------------------------------------------------------

CASES = [
    ("I didnt fucking tell you to do anything, we are continueing a conversation", ["unasked", "frustration"]),
    ("That is not a good summary?", ["left out"]),
    ("I just said without users and your reply requires users?", ["misread"]),
    ("Okay so then wtf are you talking about", ["misread", "frustration"]),
    ("That isn't what I mean by repeating itself. Try again.", ["misread", "wrong"]),
    ("You did it again. You narrated by saying Confirmed.", ["again"]),
    ("Summarize plan", []),
    ("Seems like a lot of work for non-technical people", []),
    ("Go ahead.", []),
    ("Omg I got email back. Check it", []),
]


def selftest():
    body = rules()
    wrong = 0
    for text, want in CASES:
        got = signals(text, [], 0, body)
        if got != want:
            wrong += 1
            print("WRONG %r: wanted %s, got %s" % (text, want, got))
    got = signals("what is the reply rate for each employer", ["what is the reply rate for each employer here"], 0, body)
    if "re-asked" not in got:
        wrong += 1
        print("WRONG: a question asked again was not seen")
    if "held" not in signals("ok", [], 2, body):
        wrong += 1
        print("WRONG: a turn held twice was not seen")
    print("reaction selftest: %d cases, %d wrong" % (len(CASES) + 2, wrong))
    return wrong


if __name__ == "__main__":
    raise SystemExit(1 if selftest() else 0)
