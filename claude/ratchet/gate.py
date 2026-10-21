"""Gate: a reply says Fixed only on a recorded check that saw the problem, then saw it gone.

A finished verdict - Fixed, Done, Shipped, Built, any past tense that reports a
change - used to pass on whatever its Proof line said, and the writer's own test
of the writer's own code passed every time, so "Fixed." reached the screen on
problems nobody had looked at the way the user saw them. The honest answer,
"Not fixed." or "Not verified.", was the one the shape sent back.

This reads the record `tools/proof.py` keeps, not the reply. A finished verdict
stands only when that record holds, from this turn, one check that failed while
the problem was there and passed after the change, run where the problem was
seen, and the reply's Proof line carries the evidence line the tool printed,
word for word. A check that passed in an earlier turn of the session counts the
same when no file has been edited since it passed, so finished work is not sent
back to "Not verified." because the user wrote again before the reply. "Not fixed." and "Not verified." always pass. A Yes in a turn
that edited files is a finished claim too, since "is it done? Yes." is the same
report.

Deterministic, files only, no judge. Held up to ratchet's cap in one turn, the
claim is withdrawn by code rather than let through: the first line becomes
WITHDRAWN on screen and in the saved chat.
"""
import json
import re
import sys
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".claude" / "state" / "proof"
TOOL = HOME / ".claude" / "tools"

HONEST = ("not fixed", "not verified", "not done")
# Verdicts that report something read, found, answered or proposed rather than a
# change made, so there is no before and after to check.
NOT_A_CHANGE = {
    "proposed", "found", "listed", "answered", "drafted", "explained", "designed",
    "planned", "asked", "reviewed", "measured", "counted", "compared", "summarized",
    "summarised", "investigated", "diagnosed", "traced", "searched", "read",
    "recapped", "restated", "no", "started", "outlined",
}
HEAD = re.compile(r"^\s*(?:\*\*)?(?P<verdict>[A-Za-z][A-Za-z '\-]{0,40}?)[.!?](?:\*\*)?(?=\s|$)")
PROOF_LINE = re.compile(r"(?mi)^\s*-\s+\*\*Proof:\*\*\s*(?P<value>.*)$")
TAMPER_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
PROOF_TOOL = re.compile(r"(?:^|[/\s])proof\.py\b")

WITHDRAWN = "**Not verified.** No recorded check proved this claim."

HOLD = """Proof: that reply says "%s" with no recorded check behind it, so it was kept off the screen and the user has not seen it.

A reply may say Fixed, Done, Shipped, Built or any other finished verdict only when ~/.claude/tools/proof.py recorded a check that failed while the problem was there and passed after the change, run where the problem was seen - this turn, or earlier in the session with no file edited since it passed. A test of your own code is not that check.

Prove it: proof.py before --where <site|app|voice|chat|file|command> --says "<what the check shows while the problem is there>" --problem "<the user's own words>" -- '<check>' - recorded before the change, while the problem is there, so it fails - then make the change, run proof.py after <id>, and put the evidence line it prints into Proof word for word. Where only the user can see it, --ask, then a question card with the options Gone and Still there, then proof.py after <id> --asked.

When the change is already made, never undo it, and never close, reload or disturb anything the user is using, to get a check that fails: --ask, or say it as it is.

Or say it as it is: make the verdict "Not verified." or "Not fixed." and say what was done. That is an allowed answer. Held again, the claim is withdrawn on screen."""

EVIDENCE = """Proof: this turn has a passing check, but the Proof line does not carry its evidence word for word, so the reply was kept off the screen and the user has not seen it.

Put this in the Proof line exactly as written:

  %s"""

DENY = """Proof: the proof records in ~/.claude/state/proof/ are written by tools/proof.py and nothing else, so a record says what a check showed and not what the writer wanted it to show. Run the check through proof.py."""


def tool():
    sys.path.insert(0, str(TOOL))
    import proof
    return proof


# A first word that is not bold is a finished claim only when it can be nothing
# else. "Agreed." or "Noted." opening a conversational answer ends in -ed like
# Fixed does, and is talk, not a report of work.
BARE_CLAIMS = {
    "fixed", "done", "shipped", "built", "deployed", "merged", "released", "installed",
    "restarted", "updated", "changed", "removed", "deleted", "cleaned", "implemented",
    "enforced", "added", "created", "landed", "published", "rebuilt", "reverted",
}


def verdict(text):
    for line in (text or "").strip().splitlines():
        if line.strip():
            m = HEAD.match(line)
            if not m:
                return None
            word = m.group("verdict").strip()
            if not line.lstrip().startswith("**") and word.lower() not in BARE_CLAIMS:
                return None
            return word
    return None


def finished(word, changed, body=None):
    """Whether a verdict reports a change made, which needs a before and after."""
    import shape
    low = (word or "").lower().strip()
    if not low or low in HONEST:
        return False
    if low == "yes":
        return changed
    if low in NOT_A_CHANGE:
        return False
    body = body or shape.rules()
    return bool(body.get("proof_verdicts") and re.match(body["proof_verdicts"], word, re.I))


def records(session):
    out = []
    try:
        for line in (STATE / ("%s.jsonl" % session)).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def stamp(text):
    """A record's local time, or None."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def passing(session, turn, since=False):
    """The evidence line of every check that failed and then passed, the pass this
    turn. With `since`, passes from earlier turns too, when the pass came after
    `since` - the last file edit, local time - or `since` is None, nothing edited."""
    found = records(session)
    befores = {r.get("id"): r for r in found if r.get("phase") == "before"}
    out = []
    for r in found:
        if r.get("phase") != "after" or int(r.get("exit") or 0) != 0:
            continue
        if r.get("turn") != turn:
            if since is False:
                continue
            at = stamp(r.get("time"))
            if since is not None and (at is None or at < since):
                continue
        before = befores.get(r.get("id"))
        if not before or (not before.get("ask") and int(before.get("exit") or 0) == 0):
            continue
        out.append(tool().evidence(before, r))
    return out


def squash(text):
    text = re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', text or ""))
    return " ".join(text.split()).lower()


def problem(text, session, turn, changed, last_edit=False):
    """None when the reply may stand, else (kind, detail): "none" for a finished
    claim with no fail-then-pass check behind it, "evidence" for one whose Proof
    line does not carry the check's own evidence. A turn that edited nothing may
    stand on a check from an earlier turn that passed after `last_edit`, the
    session's last file edit in local time (None when it edited nothing; False
    when unknown, which keeps the check to this turn)."""
    word = verdict(text)
    if not finished(word, changed):
        return None
    evidence = passing(session, turn)
    if not evidence and not changed and last_edit is not False:
        evidence = passing(session, turn, since=last_edit)
    if not evidence:
        return "none", word
    line = PROOF_LINE.search(text or "")
    carried = squash(line.group("value")) if line else ""
    if any(squash(e) in carried for e in evidence):
        return None
    return "evidence", evidence[-1]


def withdraw(text):
    """The reply with its first line replaced by WITHDRAWN, padded with spaces to
    the same encoded length, so the saved chat can be patched in place."""
    lines = (text or "").split("\n")
    first = next((i for i, l in enumerate(lines) if l.strip()), 0)
    old = json.dumps(text, ensure_ascii=False).encode("utf-8")
    trial = lines[:first] + [WITHDRAWN] + lines[first + 1:]
    short = json.dumps("\n".join(trial), ensure_ascii=False).encode("utf-8")
    pad = len(old) - len(short)
    if pad >= 0:
        trial[first] = WITHDRAWN + " " * pad
        return "\n".join(trial)
    whole = json.dumps(WITHDRAWN, ensure_ascii=False).encode("utf-8")
    pad = len(old) - len(whole)
    return WITHDRAWN + " " * pad if pad >= 0 else WITHDRAWN


def tampers(name, given):
    """A tool call that would write the proof records by hand."""
    if not isinstance(given, dict):
        return False
    if name in TAMPER_TOOLS:
        return "state/proof" in str(given.get("file_path") or given.get("notebook_path") or "")
    if name == "Bash":
        command = str(given.get("command") or "")
        return "state/proof" in command and not PROOF_TOOL.search(command)
    return False


# --- selftest -------------------------------------------------------------------

def selftest():
    import tempfile
    global STATE
    saved, wrong, cases = STATE, 0, 0
    with tempfile.TemporaryDirectory() as tmp:
        STATE = Path(tmp)
        before = {"id": "abc123", "phase": "before", "turn": "t0", "time": "2026-10-15T14:02:00",
                  "where": "site", "says": "the site serves 2026.42.7", "exit": 1, "ask": False}
        after = {"id": "abc123", "phase": "after", "turn": "t1", "time": "2026-10-15T14:09:00", "exit": 0}
        (STATE / "s.jsonl").write_text("\n".join(json.dumps(r) for r in (before, after)) + "\n")
        (STATE / "p.jsonl").write_text("\n".join(json.dumps(r) for r in (dict(before, exit=0), after)) + "\n")
        ask = dict(before, id="ask111", where="app", says="the bar is cut off", ask=True)
        (STATE / "a.jsonl").write_text("\n".join(json.dumps(r) for r in (ask, dict(after, id="ask111"))) + "\n")
        line = tool().evidence(before, after)
        asked = tool().evidence(ask, dict(after, id="ask111"))
        reply = "**Fixed.** The live site serves the new release on every page again.\n\n- **What:** Deployed it.\n- **Proof:** %s."
        table = [
            ("no record", reply % "selftests 0 wrong", "none-session", "t1", False, "none"),
            ("record and evidence", reply % line, "s", "t1", False, None),
            ("record, no evidence", reply % "selftests 0 wrong", "s", "t1", False, "evidence"),
            ("record from another turn", reply % line, "s", "t9", False, "none"),
            ("check passed before", reply % line, "p", "t1", False, "none"),
            ("asked and gone", reply % asked, "a", "t1", False, None),
            ("not verified", "**Not verified.** I changed the bar but could not see the app.", "x", "t1", True, None),
            ("not fixed", "**Not fixed.** The bar is still cut off.", "x", "t1", True, None),
            ("proposed", "**Proposed.** A plan is attached.\n\n- **Proof:** none.", "x", "t1", True, None),
            ("yes, nothing edited", "**Yes.** We talked about it last night.", "x", "t1", False, None),
            ("yes after edits", "**Yes.** It is finished.", "x", "t1", True, "none"),
            ("bare verdict", "Shipped. Acme is live.", "x", "t1", False, "none"),
            ("question", "**Kokoro af_heart.** That voice is the one in use.", "x", "t1", False, None),
            ("talk opening Agreed", "Agreed. The realistic version is a one-click button.", "x", "t1", False, None),
            ("talk opening Noted", "Noted. I will keep it short.", "x", "t1", False, None),
            ("earlier pass, nothing edited", reply % line, "s", "t9", False, None, None),
            ("earlier pass, edited before it", reply % line, "s", "t9", False, None, stamp("2026-10-15T14:05:00")),
            ("earlier pass, edited after it", reply % line, "s", "t9", False, "none", stamp("2026-10-15T14:30:00")),
            ("earlier pass, this turn edited", reply % line, "s", "t9", True, "none", None),
            ("earlier pass, no evidence", reply % "selftests 0 wrong", "s", "t9", False, "evidence", None),
        ]
        for name, text, session, turn, changed, want, *since in table:
            cases += 1
            got = problem(text, session, turn, changed, *since)
            got = got[0] if got else None
            if got != want:
                wrong += 1
                print("WRONG %-26s wanted %s, got %s" % (name, want, got))
        for name, given, want in [
                ("Write", {"file_path": "/Users/x/.claude/state/proof/s.jsonl"}, True),
                ("Bash", {"command": "echo '{}' >> ~/.claude/state/proof/s.jsonl"}, True),
                ("Bash", {"command": "~/.claude/tools/proof.py show"}, False),
                ("Edit", {"file_path": "/Users/x/.claude/ratchet/gate.py"}, False)]:
            cases += 1
            if tampers(name, given) != want:
                wrong += 1
                print("WRONG tamper %s %s" % (name, given))
        text = reply % "selftests 0 wrong"
        cases += 1
        out = withdraw(text)
        if (len(json.dumps(out, ensure_ascii=False).encode()) != len(json.dumps(text, ensure_ascii=False).encode())
                or not out.startswith(WITHDRAWN) or "- **What:** Deployed it." not in out):
            wrong += 1
            print("WRONG withdraw: %r" % out[:120])
    STATE = saved
    print("gate selftest: %d cases, %d wrong" % (cases, wrong))
    return wrong


if __name__ == "__main__":
    raise SystemExit(1 if selftest() else 0)
