"""Premature: refuse a claim that work is done while that work is still running.

Ratchet's phrase scan reads what a turn admits it left. This reads the other
failure, which no word list can see: a reply that states as fact - "the cube
now takes priority" - a change that a background agent has only just been told
to make. The words are fine; what makes them false is the transcript, so the
check reads the transcript.

In flight means a background agent or background command this session started
or messaged, whose completion notification has not arrived since. When anything
is in flight, a headless Haiku is asked one closed question: does this text
state as already done, in effect or true something that only the in-flight work
would make true. A yes holds the stop, or refuses the spoken announcement before
it reaches the speaker.

It runs in two places:
  Stop            the closing prose of the turn (ratchet.hook calls check_stop)
  PreToolUse Bash  a Sunday announcement (curl .../say) before it is spoken

An announcement may say the detail is on his screen only when a canvas went there:
a /show (curl .../8794/show or canvas.py show) in the same command, or one this turn
ran that answered "shown": true. canvas_shown() reads which, and the judge is told.
"""
import json
import re
import subprocess

AGENT_ID = re.compile(r"agentId:\s*([A-Za-z0-9]+)")
RESUMED = re.compile(r'"resumedAgentId"\s*:\s*"([A-Za-z0-9]+)"|queued for delivery to ([A-Za-z0-9]+)')
BG_COMMAND = re.compile(r"Command running in background with ID:\s*([A-Za-z0-9]+)")
DONE = re.compile(r"<task-id>([A-Za-z0-9]+)</task-id>.*?<status>(completed|failed|killed|stopped)</status>", re.S)


def _profile():
    """The owner's profile reader (tools/mtoa_config.py), or None where none exists."""
    import importlib.util
    from pathlib import Path
    for path in (Path(__file__).resolve().parent.parent / "tools" / "mtoa_config.py",
                 Path.home() / ".claude" / "tools" / "mtoa_config.py"):
        if path.is_file():
            try:
                spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            except Exception:
                return None
    return None


def _render(text):
    """The text with the owner's and the assistant's names filled in."""
    module = _profile()
    return module.render(text) if module else text


# The voice server's port, from the profile; 8794 where none is set.
PORT = int((_profile().get("host.voice_port", 8794) if _profile() else 8794) or 8794)
SAY = re.compile(r"%d/say" % PORT)
SHOW = re.compile(r"%d/show\?(?!close)|canvas\.py\s+show\b" % PORT)
SHOWN = re.compile(r'"shown"\s*:\s*true')
SAY_TEXT = re.compile(r"text='(.*?)'(?:\"|\s|$)", re.S)

PROMPT = """You check one thing: whether a message states as already done something that is still being worked on.

Work still in progress right now (started or asked for, not yet finished):
%s

Work the message's author finished and saw the result of in this turn (claims about these are true):
%s

The message about to be sent:
<<<
%s
>>>

Does the message state as already done, already in effect, or already true anything that only this in-progress work would make true? Claims about other work, which the message itself did and checked, are fine; only claims that depend on the in-progress work listed above count. Describing it as underway, being built, or going to happen is fine. Saying it is done, live, working, or that something "now" behaves a new way, when that depends on the in-progress work, is not.

Answer with JSON only: {"verdict": "premature" or "ok", "claim": "<the sentence that states it as done, or empty>"}"""

HOLD = """Ratchet: this states as done what is still in progress.

  "%s"

Still running: %s

Nothing is true until it is built and checked. Say it as work underway ("I'm \
making the cube take priority"), or wait for the work to finish and verify it, \
then say it as done. Do not delete the point - say it in the right tense."""


def _rows(transcript):
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except OSError:
        return


def _content(row):
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [p for p in content if isinstance(p, dict)] if isinstance(content, list) else []


def _result_text(part):
    body = part.get("content")
    if isinstance(body, list):
        return "\n".join(p.get("text", "") for p in body if isinstance(p, dict))
    return str(body or "")


def in_flight(transcript):
    """{id: what it is doing} for background work started or messaged and not finished."""
    pending_calls = {}   # tool_use id -> (kind, label, brief)
    work = {}            # task id -> (label, brief)
    state = {}           # task id -> running?
    for row in _rows(transcript):
        if row.get("isSidechain"):
            continue
        if row.get("type") != "assistant":
            # Completion notices arrive as queue operations and attachments, not as content.
            flat = json.dumps(row)
            if "task-notification" in flat:
                for m in DONE.finditer(flat.replace("\\n", "\n")):
                    state[m.group(1)] = False
        for part in _content(row):
            kind = part.get("type")
            if kind == "tool_use":
                name, args = part.get("name"), part.get("input") or {}
                if name == "Agent":
                    pending_calls[part.get("id")] = ("agent", args.get("description", "an agent"),
                                                     args.get("prompt", ""))
                elif name == "SendMessage":
                    pending_calls[part.get("id")] = ("message", args.get("summary", "a follow-up"),
                                                     args.get("message", ""))
                elif name == "Bash" and args.get("run_in_background"):
                    pending_calls[part.get("id")] = ("command", args.get("description", "a command"),
                                                     args.get("command", ""))
            elif kind == "tool_result":
                call = pending_calls.pop(part.get("tool_use_id"), None)
                text = _result_text(part)
                if call:
                    ident = None
                    if call[0] == "agent":
                        m = AGENT_ID.search(text)
                        ident = m and m.group(1)
                    elif call[0] == "message":
                        m = RESUMED.search(text)
                        ident = m and (m.group(1) or m.group(2))
                    else:
                        m = BG_COMMAND.search(text)
                        ident = m and m.group(1)
                    if ident:
                        label, brief = work.get(ident, (call[1], ""))
                        work[ident] = (label, (brief + "\nLatest: " + call[2]) if brief else call[2])
                        state[ident] = True
                for m in DONE.finditer(text):
                    state[m.group(1)] = False
            elif kind == "text" and row.get("type") == "user":
                for m in DONE.finditer(part.get("text", "")):
                    state[m.group(1)] = False
    return {i: work[i] for i, running in state.items() if running and i in work}


def _describe(flight):
    lines = []
    for label, brief in flight.values():
        brief = " ".join(brief.split())
        lines.append("- %s: %s" % (label, brief[-700:]))
    return "\n".join(lines)


def ask(text, flight, cfg, binary, env, cwd, transcript=None):
    """('premature', claim) | ('ok', '') | (None, problem)."""
    import ratchet
    try:
        raw, _ = ratchet.run_claude(binary, PROMPT % (_describe(flight), done_this_turn(transcript) or "(none)",
                                                      text[:8000]), cfg)
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
    if verdict not in ("premature", "ok"):
        return None, "judge answered %r" % verdict
    return verdict, str(answer.get("claim", "")).strip()


def reason(claim, flight):
    return HOLD % (claim or "(see the closing text)", "; ".join(label for label, _ in flight.values()))


def say_text(command):
    if not SAY.search(command or ""):
        return ""
    m = SAY_TEXT.search(command)
    text = m.group(1) if m else ""
    # The text often sits in a shell variable set earlier in the same command.
    def expand(v):
        found = re.search(r"(?:^|[\s;&])%s=(\"(.*?)\"|'(.*?)')" % re.escape(v.group(1) or v.group(2)), command, re.S)
        return (found.group(2) if found.group(2) is not None else found.group(3)) if found else v.group(0)
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", expand, text)


def canvas_shown(command, transcript=None):
    """What the judge is told went to his screen with this announcement: "" for nothing.
    A /show in the same command counts; so does one this turn ran whose answer says a
    screen showed it. One that was held back everywhere, or failed, does not."""
    if SHOW.search(command or ""):
        return _render("a canvas is sent to {his} screen by the same command that speaks this")
    if not transcript:
        return ""
    calls, found = {}, ""
    for row in _rows(transcript):
        if row.get("isSidechain"):
            continue
        if row.get("type") == "user" and any(p.get("type") == "text" and p.get("text", "").strip()
                                             for p in _content(row)) and not row.get("isMeta"):
            calls, found = {}, ""      # a person spoke: a new turn starts
        for part in _content(row):
            if part.get("type") == "tool_use" and part.get("name") == "Bash":
                cmd = (part.get("input") or {}).get("command", "")
                if SHOW.search(cmd):
                    calls[part.get("id")] = cmd
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in calls:
                if SHOWN.search(_result_text(part)):
                    found = _render("a canvas this turn sent, which {his} screen showed")
                calls.pop(part.get("tool_use_id"))
    return found


SPEECH = """You check one announcement that a voice assistant named {assistant} is about to say aloud to {user}, who uses it by voice only.

Work still in progress right now (started or asked for, not finished):
%s

Work that has finished and been reported back (claims about this are true):
%s

On {his} screen with it: %s

The announcement:
<<<
%s
>>>

It breaks a rule if it does any of these:
A. Gives up or says something can't be done, isn't working, or isn't available, instead of saying what is being done to solve it. Reporting what a search found, including that a message, email or record {he} asked about does not exist where it was looked for, is an answer and never breaks this rule.
B. Asks {him} to type, read a screen, open a chat, or use a keyboard. {He} answers by voice only. When a canvas is on {his} screen with it, saying plainly that the detail is on {his} screen ("The breakdown is on your screen.") asks {him} nothing and is fine; asking {him} to click, type, open, scroll or reply in writing still breaks this rule. When nothing went to {his} screen, saying something is on it breaks this rule.
C. Speaks of chats, sessions, agents, a desk, relaying, or another assistant doing the work as separate from {assistant}. {assistant} is one assistant and all of the work is its own. Its devices and the things it built are not other assistants: the cube, the Mac, a speaker, a microphone, a browser, a site or a pipeline can be the subject of a sentence ("the Mac stays quiet while this speaker talks") without breaking this rule.
D. States as done, live or working anything that only the in-progress work above would make true.

Answer with JSON only: {"verdict": "ok" or "broken", "rule": "A", "B", "C", "D" or "", "claim": "<the sentence that breaks it, or empty>"}"""

RULES = {"A": "gives up instead of solving", "B": "asks {him} to type or look at a screen",
         "C": "speaks as if something other than {assistant} did the work",
         "D": "states as done what is still in progress"}

SPEECH_HOLD = """Ratchet: this announcement breaks a speech rule (%s), so it was not spoken.

  "%s"

Fix the cause, not the sentence: %s"""

REMEDY = {"A": "solve the problem another way (a different tool, the Mac, the web) and announce the result, "
               "or say in one sentence what was tried and what is being done next.",
          "B": "get {his} answer by voice: ask the question aloud and wait for the spoken reply.",
          "C": "say it as {assistant}'s own work, in the first person.",
          "D": "wait for the work to finish and verify it, or say it as underway."}


def ask_speech(text, flight, cfg, binary, transcript=None, shown=""):
    """('broken', rule, claim) | ('ok', '', '') | (None, problem, '').
    shown: the canvas that went to his screen with it (canvas_shown), "" for none."""
    import ratchet
    try:
        raw, _ = ratchet.run_claude(binary, _render(SPEECH) % (_describe(flight) or "(none)",
                                                      finished(transcript) or "(none)",
                                                      shown or "nothing", text[:4000]), cfg)
    except (subprocess.TimeoutExpired, OSError) as problem:
        return None, str(problem), ""
    try:
        envelope = json.loads(raw)
        raw = envelope.get("result", raw) if isinstance(envelope, dict) else raw
    except ValueError:
        pass
    m = re.search(r"\{.*\}", raw if isinstance(raw, str) else json.dumps(raw), re.S)
    if not m:
        return None, "judge answered in prose", ""
    try:
        answer = json.loads(m.group(0))
    except ValueError:
        return None, "judge answered with malformed JSON", ""
    verdict = str(answer.get("verdict", "")).lower()
    rule = str(answer.get("rule", "")).upper()[:1]
    if verdict == "broken" and rule in RULES:
        return "broken", rule, str(answer.get("claim", "")).strip()
    return ("ok", "", "") if verdict == "ok" else (None, "judge answered %r" % verdict, "")


def speech_reason(rule, claim):
    return SPEECH_HOLD % (_render(RULES[rule]), claim or "(see the announcement)",
                          _render(REMEDY[rule]))


def done_this_turn(transcript):
    """What the current turn ran and saw come back: each tool call's description and the
    tail of its result, so a claim about finished work is not mistaken for in-flight work."""
    if not transcript:
        return ""
    calls, lines = {}, []
    for row in _rows(transcript):
        if row.get("isSidechain"):
            continue
        if row.get("type") == "user" and any(p.get("type") == "text" and p.get("text", "").strip()
                                             for p in _content(row)) and not row.get("isMeta"):
            calls, lines = {}, []      # a person spoke: a new turn starts
        for part in _content(row):
            if part.get("type") == "tool_use":
                args = part.get("input") or {}
                calls[part.get("id")] = args.get("description") or part.get("name", "")
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in calls:
                out = " ".join(_result_text(part).split())[-200:]
                lines.append("- %s -> %s" % (calls.pop(part.get("tool_use_id")), out))
    return "\n".join(lines[-12:])


RESULT = re.compile(r"<summary>(.*?)</summary>.*?<result>(.*?)</result>", re.S)


def finished(transcript, keep=6):
    """The latest background work that reported back finished, plus what this turn ran."""
    if not transcript:
        return ""
    seen = []
    for row in _rows(transcript):
        if row.get("type") == "queue-operation" and row.get("operation") == "enqueue":
            body = str(row.get("content", ""))
            if "<status>completed</status>" in body:
                m = RESULT.search(body)
                if m:
                    seen.append("- %s: %s" % (m.group(1).strip(), " ".join(m.group(2).split())[:500]))
    done = "\n".join(seen[-keep:])
    turn = done_this_turn(transcript)
    return (done + ("\n" + turn if turn else "")).strip()


# What the speech check must decide, judge and all: (announcement, canvas sent, rule or "ok").
SPEECH_CASES = [
    ("The Pi's disk is at forty percent. The breakdown by folder is on your screen.", True, "ok"),
    ("I checked the open tickets: seven are open, and the full list is on your screen.", True, "ok"),
    ("The Pi's disk is at forty percent. The breakdown by folder is on your screen.", False, "B"),
    ("The steps are on your screen, so click the first link and type in the code it gives you.", True, "B"),
    ("The numbers are on your screen. Open the Previews tab and click Approve to start the send.", True, "B"),
]

# canvas_shown on commands alone: (command, canvas sent).
SHOW_CASES = [
    ("ssh voice-host 'curl -s \"localhost:8794/show?md=%23%23+Disk\"' && ssh voice-host 'curl -s localhost:8794/say --get "
     "--data-urlencode text=\"It is on your screen.\"'", True),
    ("ssh voice-host 'cd ~/voice-assistant && venv/bin/python canvas.py show /tmp/disk.json'; "
     "ssh voice-host \"curl -s 'localhost:8794/say?text=done'\"", True),
    ("ssh voice-host 'curl -s localhost:8794/show?close=1'; ssh voice-host \"curl -s 'localhost:8794/say?text=done'\"", False),
    ("ssh voice-host \"curl -s 'localhost:8794/say?text=The+site+is+live'\"", False),
]


def speechtest():
    """Replays SHOW_CASES (no judge) and SPEECH_CASES (the judge, as the hook runs it)."""
    import ratchet
    bad = 0
    for command, want in SHOW_CASES:
        got = bool(canvas_shown(command))
        bad += got != want
        print("%s  canvas sent %-5s %s" % ("PASS" if got == want else "FAIL", got, command[:90]))
    cfg, binary = ratchet.config(), ratchet.claude_binary()
    if not binary:
        print("SKIP  the judge cases: no claude binary on this machine")
        return 1 if bad else 0
    for text, sent, want in SPEECH_CASES:
        shown = _render("a canvas is sent to {his} screen by the same command that speaks this") if sent else ""
        verdict, rule, claim = ask_speech(text, {}, cfg, binary, None, shown)
        got = rule if verdict == "broken" else (verdict or "error: " + rule)
        bad += got != want
        print("%s  expect %-3s got %-3s canvas %-5s %s" % ("PASS" if got == want else "FAIL", want, got, sent, text[:80]))
    print("all passed" if not bad else "%d failed" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    if sys.argv[1:2] == ["speechtest"]:
        sys.exit(speechtest())
    print("usage: premature.py speechtest", file=sys.stderr)
    sys.exit(2)
