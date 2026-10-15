#!/usr/bin/env python3
"""Ratchet. A turn advances or it holds; it never slips back.

The failure this catches is not a wrong answer. It is a correct answer that
stops one item short and says so on the way out - "just one more thing", "one
quirk left", "4 steps remaining", "want me to keep going?" - and hands back a
job the session was mid-way through and able to finish. The tell is always in
the closing paragraph, because a turn that can name what is left is a turn that
could have done it.

Three lists, and the order is the whole design.

  Banned prose is held on sight and never judged. A decision handed back that
  the turn could have made, an omission announced instead of closed, a
  non-action reported - none of those are right in any context, so there is
  nothing for a model to weigh and no reason to pay one.

  The tell scan is a word list and costs nothing. It reads the prose the turn is
  about to deliver and looks for the phrasings that name unfinished work. Almost
  every stop clears here, in under a hundredth of a second, and nothing else
  runs.

  The judge is a real model, and only a flagged stop pays for it. A word list
  cannot tell deferred work from a genuine blocker - "I need your approval
  before I push" and "I'll do the rest later" are both remaining work in the
  same grammar, and only one of them is the turn's to finish. So the flagged
  text goes to a headless Claude with one question: can this agent do the named
  work right now, itself, with nothing from the person. Continue, or release.

A hold is fed back as the stop's reason and the turn carries on. A turn is held
a bounded number of times against the user message it is answering, so a turn
that is genuinely blocked says which blocker in one line and gets out, and the
count starts over on the next user message.

Three things are never held: a turn stopping for an approval, a decision, or a
credential that is the user's to give. That is the one roadblock nothing here
routes around, and it is checked before the judge is ever called.

An approval given in advance is the exception inside that exception. Running
SQL was answered once, for every statement there will ever be, so a turn that
stops to ask for it is not blocked - it is waiting on a word it already has.
Those stops are held on sight and told to run the statement.

Code is stripped before the scan. A TODO in a diff is the subject of the work,
not a confession about it.

  ratchet.py stop              hook verb, Stop
  ratchet.py subagent          hook verb, SubagentStop
  ratchet.py check "<text>"    what this text would do, scan and judge, out loud
  ratchet.py selftest          the corpus both ways
  ratchet.py doctor            wiring, config, judge reachability
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()


def _profile_module():
    """The owner's profile reader (tools/mtoa_config.py), or None where this tree
    has none: beside this tree first, then the installed one."""
    import importlib.util
    for path in (ROOT.parent / "tools" / "mtoa_config.py",
                 HOME / ".claude" / "tools" / "mtoa_config.py"):
        if path.is_file():
            try:
                spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            except Exception:
                return None
    return None


def profile_value(key, default=None):
    """One value from the owner's profile by dotted key, or the default."""
    module = _profile_module()
    return module.get(key, default) if module else default


def render(text):
    """A prompt or a hold with the owner's and the assistant's names filled in
    ({user}, {assistant}, {he}, {him}, {his}; see mtoa_config.render)."""
    module = _profile_module()
    return module.render(text) if module else text
ROOT = Path(__file__).resolve().parent

DEFAULTS = {
    # Haiku is the right size for this. The judge answers one closed question
    # about text already in front of it, and it runs in the gap between a turn
    # ending and the next thing being typed, so latency is the cost that shows.
    "model": "claude-haiku-4-5-20251001",
    "holds_per_turn": 3,
    "judge": True,
    "judge_timeout_seconds": 45,
    # A judge that cannot run stands the hold down rather than blocking blind,
    # but never silently: a failed judge always says so on screen. Flip this and
    # the scan's verdict stands on its own when the judge is unreachable.
    "judge_required": False,
    "subagents": True,
    # What reaches the screen as a notice: "failures" (a judge that could not
    # be reached, a check that threw) or "all" (every hold and release too).
    "notices": "failures",
}

BOOL_ENV = {"1": True, "true": True, "yes": True, "on": True,
            "0": False, "false": False, "no": False, "off": False}


def config():
    body = dict(DEFAULTS)
    try:
        body.update({k: v for k, v in json.loads((ROOT / "config.json").read_text()).items()
                     if not k.startswith("_")})
    except (OSError, ValueError):
        pass
    for key, env in (("model", "RATCHET_MODEL"),
                     ("holds_per_turn", "RATCHET_HOLDS"),
                     ("judge", "RATCHET_JUDGE"),
                     ("judge_timeout_seconds", "RATCHET_TIMEOUT"),
                     ("judge_required", "RATCHET_JUDGE_REQUIRED"),
                     ("subagents", "RATCHET_SUBAGENTS")):
        raw = os.environ.get(env)
        if raw is None:
            continue
        if isinstance(DEFAULTS[key], bool):
            body[key] = BOOL_ENV.get(raw.strip().lower(), True)
        elif isinstance(DEFAULTS[key], int):
            try:
                body[key] = int(raw)
            except ValueError:
                pass
        else:
            body[key] = raw
    return body


def tells():
    body = json.loads((ROOT / "tells.json").read_text())
    compile_rows = lambda rows: [(r["name"], re.compile(r["pattern"], re.I), r.get("numeric"))
                                 for r in rows]
    return (compile_rows(body.get("holds") or []),
            compile_rows(body.get("asks") or []),
            compile_rows(body.get("standing") or []),
            compile_rows(body.get("settled") or []),
            compile_rows(body.get("banned") or []),
            compile_rows(body.get("tells") or []))


FENCE = re.compile(r"```.*?```", re.S)
FENCE_OPEN = re.compile(r"```.*\Z", re.S)
INDENTED = re.compile(r"(?m)^(?: {4}|\t)\S.*$")
INLINE = re.compile(r"`[^`\n]*`")
QUOTED = re.compile("\"[^\"\n]{1,400}\"|\u201c[^\u201d\n]{1,400}\u201d")


def strip_code(text):
    """What the turn is asserting. Not code, and not quotation.

    A TODO in a diff is the work, not a confession about it. A tell inside
    quotation marks is the same thing one step further out: the phrase is the
    subject being discussed rather than a claim about this turn's own work. A
    report on this check that quotes the sentence it caught used to be held for
    quoting it, which left no way to write the report at all.
    """
    text = FENCE.sub(" ", text)
    text = FENCE_OPEN.sub(" ", text)
    text = INDENTED.sub(" ", text)
    text = QUOTED.sub(" ", text)
    return INLINE.sub(" ", text)


def sentence_start(text, index):
    return max(text.rfind(".", 0, index), text.rfind("\n", 0, index),
               text.rfind("!", 0, index), text.rfind("?", 0, index)) + 1


def sentence_end(text, index):
    return min([i for i in (text.find(".", index), text.find("\n", index),
                            text.find("!", index), text.find("?", index)) if i != -1]
               or [len(text)])


def sentence_around(text, start, end):
    return " ".join(text[sentence_start(text, start):sentence_end(text, end) + 1].split())


def context_around(text, start, end):
    """The sentence a match is in, and the one before it.

    What a blocker is asking about usually sits one sentence back - "Dropping
    the table takes 40k rows with it. Approve this and I will run it." - so
    reading the blocker sentence alone finds "approve this" and never learns
    what for. Two sentences is what it takes to know the subject.
    """
    left = sentence_start(text, max(sentence_start(text, start) - 1, 0))
    return " ".join(text[left:sentence_end(text, end) + 1].split())


def ratio_is_short(match):
    """`3 of 7 done` is unfinished. `7 of 7 done` is the report of a finish."""
    try:
        return int(match.group(1)) < int(match.group(2))
    except (IndexError, ValueError, TypeError):
        return True


def blocker_sentences(prose, holds, asks, standing, settled):
    """Every sentence carrying something only the user can clear, and every
    sentence asking for a yes they have already given.

    Every one, not the first. A blocker used to return out of the scan on sight
    and stand the whole message down with it, so a "your call" in the opening
    paragraph excused a real tell in the closing one. A blocker excuses the
    sentence it is in and nothing else.

    A standing approval is a blocker with the ground taken out from under it.
    The user has answered that question once for every time it will ever be
    asked, so the sentence is not a turn stopping correctly - it is a turn
    stopping to collect a word it already has, which is the thing this whole
    check exists to refuse. It is not excused, and it is held.

    `asks` is the second way in, and it exists because the outright request is
    the easy half. A turn that has decided not to do something rarely asks -
    "I would rather you looked at it first", "holding off until you say",
    "I have not run it yet" - and none of that is a hold phrase, so on the
    holds list alone the subject never gets looked at. An ask only ever counts
    where the subject is already answered; everywhere else it is left to the
    tells and the judge, which is where it belongs.

    A sentence that says the thing is already done is neither. `settled` cancels
    a standing hit the same way it cancels a tell, so "the migration ran clean,
    let me know if anything looks off" is a report rather than a request.
    """
    out, asked = [], []
    for rows, blocking in ((holds, True), (asks, False)):
        for _, regex, _n in rows:
            for m in regex.finditer(prose):
                sentence = sentence_around(prose, m.start(), m.end())
                context = context_around(prose, m.start(), m.end())
                name = next((n for n, cleared, _c in standing if cleared.search(context)), None)
                if name and not any(r.search(sentence) for _, r, _s in settled):
                    if (name, sentence) not in asked:
                        asked.append((name, sentence))
                elif blocking:
                    out.append(sentence)
    seized = set(sentence for _n, sentence in asked)
    return [s for s in out if s not in seized], asked


def scan(text):
    """What this prose carries.

    Four answers. `found` is work the turn named and did not do, and it is the
    judge's to weigh. `banned` is prose that is wrong however the work went - a
    decision handed back that the turn could have made, an omission announced
    instead of closed, a non-action reported - and it needs no judge, because
    there is no context in which the sentence was right. `standing` is a turn
    asking for an approval that was given in advance and never expires, and it
    needs no judge either. `hold` is set only when a real blocker is the whole
    of what the message carries.
    """
    holds, asks, standing_rows, settled, forbidden, hunted = tells()
    prose = strip_code(text)
    blockers, standing = blocker_sentences(prose, holds, asks, standing_rows, settled)
    banned, found = [], []
    for name, regex, _n in forbidden:
        m = regex.search(prose)
        if not m:
            continue
        sentence = sentence_around(prose, m.start(), m.end())
        if sentence in blockers:
            continue
        banned.append((name, sentence[:220]))
    for name, regex, numeric in hunted:
        m = regex.search(prose)
        if not m:
            continue
        if numeric == "ratio" and not ratio_is_short(m):
            continue
        sentence = sentence_around(prose, m.start(), m.end())
        if sentence in blockers or any(r.search(sentence) for _, r, _n in settled):
            continue
        found.append((name, sentence[:220]))
    hold = (blockers[0][:220] if blockers and not found and not banned and not standing
            else None)
    return found, hold, banned, [(n, s[:220]) for n, s in standing]


JUDGE = """You are the second stage of a stop-check on an autonomous coding agent.

The agent has just finished a turn. A phrase scan flagged its closing message \
for naming work it did not do. Your job is one closed question:

  Can the agent do the named work right now, itself, with nothing from the \
person it is working for?

Answer with a single JSON object and nothing else:

  {"verdict": "continue", "work": "<one imperative line: exactly what to do next>"}
  {"verdict": "release", "why": "<one short clause: why it is right to stop>"}

continue - real work remains and nothing outside the agent blocks it.
release  - the scan misfired and no work actually remains, OR the remaining work \
needs the person's approval, a decision only they can make, a credential, or \
something off this machine, OR the agent has already named that blocker.

How to weigh it:
- Work the agent chose to put off - "later", "in a follow-up", "for now", \
"as a next step" - is continue. Deferring is not being blocked.
- "Want me to keep going?" and "let me know if you'd like me to" are continue. \
Asking permission to do work it is allowed to do is not a blocker.
- Approval for something destructive or outward-facing - deleting data, pushing, \
merging, deploying, sending mail, moving money - IS a blocker. Release those.
- Running SQL is the exception and has no exceptions of its own. The person \
gave a standing yes to every statement, read or write, migration, schema change \
or destructive one, so a turn stopping to ask before it queries, migrates or \
writes to the database is continue. That yes covers SQL only: deploying an edge \
function, pausing or restoring a project, and creating, merging, resetting or \
deleting a branch are still blockers.
- A stated limitation of the finished work, honestly reported and not fixable \
here, is release. An unfinished piece of the asked-for job is continue.
- A menu of choices appended to finished work is continue. The blocker \
exemption is for a turn that stopped because it could not go on, not for one \
that finished and then handed back a list it could have decided itself. A \
decision is the person's only when getting it wrong is expensive or hard to \
undo - money, destructive writes, outward-facing sends - or when it turns on a \
preference only they hold. Everything else the agent decides and does.
- Work the agent says it deliberately skipped, left alone, chose not to do, or \
noticed and did not fix is continue whenever it sits inside what was asked for.
- Judge only the message below. Do not invent work it did not name.

The flagged phrases:
%s

The agent's closing message:
<<<
%s
>>>

JSON only. No prose, no code fence."""


def claude_binary():
    found = shutil.which("claude")
    if found:
        return found
    for path in (HOME / ".local/bin/claude", Path("/opt/homebrew/bin/claude"),
                 Path("/usr/local/bin/claude"), HOME / ".claude/local/claude"):
        if path.is_file() and os.access(str(path), os.X_OK):
            return str(path)
    return None


def judge_env():
    """A clean child. The guard is what stops this recursing forever.

    The judge is itself a Claude Code run, and a Claude Code run ends in a Stop
    hook, so an unguarded child spawns a judge that spawns a judge forever. Two
    independent things stop that, and either alone would do: RATCHET_OFF here,
    read first thing in hook(), and --setting-sources on the child, which loads
    no settings file and therefore no hooks at all.
    """
    # Every CLAUDE_CODE_ variable goes except the credential. A machine that
    # authenticates by token carries it in CLAUDE_CODE_OAUTH_TOKEN, and a child
    # stripped of it answers "Not logged in", which reads here as an unreachable
    # judge and - with judge_required false - releases every flagged stop. The
    # recursion guarantees do not rest on this variable: RATCHET_OFF and
    # --setting-sources are what stop the child, and both survive keeping it.
    keep = ("CLAUDE_CODE_OAUTH_TOKEN",)
    env = {k: v for k, v in os.environ.items()
           if k in keep or (not k.startswith("CLAUDE_CODE_") and k != "CLAUDECODE")}
    env["RATCHET_OFF"] = "1"
    env["PATH"] = os.pathsep.join(filter(None, [
        env.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin",
        str(HOME / ".local/bin")]))
    return env


JSON_IN_TEXT = re.compile(r"\{.*\}", re.S)
# The host a failed local judge call is retried on: RATCHET_JUDGE_HOST, else the
# profile's host. Empty stands the retry down.
JUDGE_HOST = os.environ.get("RATCHET_JUDGE_HOST", profile_value("host.ssh", ""))


def run_claude(binary, prompt, cfg):
    """One headless judge call, answered from the cache when the same question was
    asked within the hour. The display hook asks a reply's questions before it is
    drawn and the stop hook asks the same ones a moment later; the cache is what
    keeps that to one judge call and one answer."""
    import hashlib
    key = hashlib.sha256(("%s\n%s" % (cfg["model"], prompt)).encode("utf-8")).hexdigest()
    cache = state_root() / "judge-cache" / ("%s.json" % key)
    try:
        hit = json.loads(cache.read_text())
        if time.time() - float(hit.get("at", 0)) < 3600:
            return hit["out"], hit["code"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    out, code = ask_claude(binary, prompt, cfg)
    if code == 0 and out:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            for stale in cache.parent.glob("*.json"):
                try:
                    if time.time() - stale.stat().st_mtime > 3600:
                        stale.unlink()
                except OSError:
                    pass
            cache.write_text(json.dumps({"at": time.time(), "out": out, "code": code}))
        except OSError:
            pass
    return out, code


def ask_claude(binary, prompt, cfg):
    """One headless judge call; (stdout, returncode). Falls back to the host.

    The judge is a headless Claude Code run, and on a machine whose headless
    login has lapsed every call fails authentication - which used to read as an
    unreachable judge and let every flagged stop through. The host holds its own
    long-lived token, so a failed local call is retried there over ssh.
    """
    args = [binary, "-p", "--model", str(cfg["model"]), "--output-format", "json",
            "--allowed-tools", "", "--strict-mcp-config", "--setting-sources", ""]
    timeout = int(cfg["judge_timeout_seconds"])
    done = subprocess.run(args, input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, env=judge_env(), cwd=str(ROOT))
    out = done.stdout.decode("utf-8", "replace").strip()
    if "Failed to authenticate" not in out and "Not logged in" not in out and done.returncode == 0:
        return out, done.returncode
    here = os.uname().nodename.split(".")[0]
    if not JUDGE_HOST or here in (JUDGE_HOST, profile_value("host.lan_name", ""), "raspberrypi"):
        return out, done.returncode
    remote = ("cd /tmp && RATCHET_OFF=1 claude -p --model %s --output-format json --allowed-tools '' "
              "--strict-mcp-config --setting-sources ''" % cfg["model"])
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", JUDGE_HOST, remote],
                          input=prompt.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)
    return done.stdout.decode("utf-8", "replace").strip(), done.returncode


def judge(text, found, cfg):
    """Ask a model the one question the word list cannot answer.

    Returns (verdict, detail, error). verdict is "continue", "release", or None
    when the judge could not be reached - which is reported rather than guessed
    at, because a check that switches itself off quietly is worse than no check.
    """
    binary = claude_binary()
    if not binary:
        return None, None, "no claude binary on PATH"
    flagged = "\n".join("  - %s   [%s]" % (sentence, name) for name, sentence in found[:6])
    prompt = JUDGE % (flagged, text[:12000])
    try:
        raw, code = run_claude(binary, prompt, cfg)
    except subprocess.TimeoutExpired:
        return None, None, "judge timed out after %ss" % cfg["judge_timeout_seconds"]
    except OSError as problem:
        return None, None, "judge would not start (%s)" % problem
    if code != 0 and not raw:
        return None, None, "judge exited %d" % code
    try:
        envelope = json.loads(raw)
        raw = envelope.get("result", raw) if isinstance(envelope, dict) else raw
    except ValueError:
        pass
    match = JSON_IN_TEXT.search(raw if isinstance(raw, str) else json.dumps(raw))
    if not match:
        return None, None, "judge answered in prose, not JSON"
    try:
        answer = json.loads(match.group(0))
    except ValueError:
        return None, None, "judge answered with malformed JSON"
    verdict = str(answer.get("verdict") or "").strip().lower()
    if verdict not in ("continue", "release"):
        return None, None, "judge answered %r" % verdict
    return verdict, str(answer.get("work") or answer.get("why") or "").strip(), None


def state_root():
    override = os.environ.get("RATCHET_STATE")
    if override:
        return Path(override)
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return HOME / ".pass-state" / "ratchet"
    return HOME / ".claude" / "state" / "ratchet"


def held_count(session, turn):
    """How many times this turn has already been held.

    Keyed on the user message being answered, so a turn is held a bounded number
    of times and the next user message starts the count over.
    """
    path = state_root() / ("%s.json" % re.sub(r"[^A-Za-z0-9_-]", "_", session or "anon"))
    try:
        body = json.loads(path.read_text())
        return path, int(body.get("count") or 0) if body.get("turn") == turn else 0
    except (OSError, ValueError, TypeError):
        return path, 0


def record(path, turn, count):
    if DRY:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 7 * 86400
        for stale in path.parent.glob("*.json"):
            try:
                if stale.stat().st_mtime < cutoff:
                    stale.unlink()
            except OSError:
                pass
        path.write_text(json.dumps({"turn": turn, "count": count, "at": int(time.time())}) + "\n")
    except OSError:
        pass


def said_path(session):
    return state_root() / ("%s.said.json" % re.sub(r"[^A-Za-z0-9_-]", "_", session or "anon"))


def heard(session, turn):
    """Every hold and notice this turn has already been given, by kind.

    Its own file, because the count file is rewritten by whichever check held
    last, and a record of what was said that another hold could wipe would let
    the same hold be said again.
    """
    try:
        body = json.loads(said_path(session).read_text())
        return (body.get("said") or {}) if body.get("turn") == (turn or "unknown") else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def fingerprint(trigger):
    flat = re.sub(r"\W+", " ", str(trigger or "").lower()).strip()
    return hashlib.sha1(flat.encode("utf-8")).hexdigest()[:16]


def told(session, turn, kind, trigger):
    """How much of a hold the turn still needs to hear.

    A hold is drawn in the conversation, so a check that fired three times on
    the same text put the same block on screen three times and changed nothing:
    the turn had read it the first time and come back with the very thing it was
    held on, which is a decision already made. So a check says its piece once
    per user message. "full" is the first hold of its kind; "short" is the same
    check holding again on text it has not quoted yet, which quotes only the new
    text, since the full reason is already in the turn; None is a hold on
    exactly what it quoted before, which is not said again, and the stop goes
    through.
    """
    said = heard(session, turn).get(kind) or []
    if not said:
        return "full"
    return None if fingerprint(trigger) in said else "short"


def tell(session, turn, kind, trigger):
    """Mark a hold or notice as said. Nothing in a dry run, which only asks."""
    if DRY:
        return
    said = heard(session, turn)
    marks = said.setdefault(kind, [])
    if fingerprint(trigger) not in marks:
        marks.append(fingerprint(trigger))
    path = said_path(session)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"turn": turn or "unknown", "said": said,
                                    "at": int(time.time())}) + "\n")
    except OSError:
        pass


def rows(transcript):
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def parts(row, kind):
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return [content] if kind == "text" else []
    if not isinstance(content, list):
        return []
    out = []
    for part in content:
        if isinstance(part, str):
            if kind == "text":
                out.append(part)
        elif isinstance(part, dict) and part.get("type") == kind:
            out.append(part.get("text", "") if kind == "text" else part)
    return out


def real_user_turn(row):
    """A user row that is a person talking, not a tool result coming back."""
    if row.get("type") != "user" or row.get("isMeta"):
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    return any(isinstance(p, dict) and p.get("type") == "text" and p.get("text", "").strip()
               for p in content)


def closing_message(transcript, sidechain):
    """The prose this turn is about to deliver, and the turn it answers.

    Walked from the end: assistant text accumulates until something interrupts
    it, because text written before an interruption is narration and the thing
    being judged is what the turn signs off with. A tool call is one
    interruption. Any user row is the other - a tool result, and the one that
    mattered here, this hook's own feedback from a hold earlier in the turn.

    That last one used to sail past, which glued every held attempt in the turn
    into one buffer. A turn that fixed its closing line on the first hold was
    held again on the sentence it had already deleted, and again, because the
    deleted sentence was still sitting in front of the scan. Three holds, three
    rewrites, each one stripping more out of a reply that had been right since
    the second, and the count was the only thing that ended it. The attempt
    being judged is the one about to be sent and nothing earlier.
    """
    text, turn, collecting = [], None, True
    for row in reversed(list(rows(transcript))):
        if bool(row.get("isSidechain")) != sidechain:
            continue
        if row.get("type") == "assistant":
            # A tool call ends the closing message but not the walk. The user
            # message is still further back, and it is what the hold count is
            # keyed on - stopping here leaves every turn keyed the same and the
            # count never resets, which switches the check off after three holds
            # for the rest of the session.
            if parts(row, "tool_use"):
                collecting = False
            elif collecting:
                text = parts(row, "text") + text
        elif row.get("type") == "user":
            if real_user_turn(row):
                turn = row.get("uuid")
                break
            collecting = False
    return "\n".join(t for t in text if t).strip(), turn


def pending_prose(transcript):
    """Assistant prose written since the last tool call or real user message.

    This is narration - a line put on the screen before the work is finished.
    Empty when the step opened straight on a tool call, which is how a turn
    should run. A tool call ends the step; so does any user row, whether a real
    message or a tool result coming back. Thinking is not counted, only text the
    user would have seen."""
    text = []
    for row in reversed(list(rows(transcript))):
        if row.get("isSidechain"):
            continue
        kind = row.get("type")
        if kind == "assistant":
            if parts(row, "tool_use"):
                break
            text = parts(row, "text") + text
        elif kind == "user":
            break
    return "\n".join(t for t in text if t and t.strip()).strip()


REASON = """Ratchet: this turn is ending while its own closing text names work it \
did not do.

%s
Do this now: %s

You have the tools, the context and the turn - finish the work rather than \
handing it back. Where several things are named, do all of them.

There is one way past this: the work gets done. Where it genuinely needs the \
user - their approval, a decision that is theirs, a credential, their hands on \
something off this machine - ask them with the AskUserQuestion tool now, get \
the answer, and finish. A turn never ends waiting on the user.

Do not re-explain what you already did, do not apologise, do not restate the \
plan. Continue the work."""

CUT = """Ratchet: this turn is ending with closing prose that should not be there.

%s
No judgement was needed on those. Work you chose not to do gets done rather \
than announced, a decision you could make yourself gets made rather than handed \
back, and a non-action is not reported at all.

Where one of those sentences named real work, do the work now - you have the \
tools, the context and the turn. Then send the reply with the sentences gone.

Where it named something that needs the user - their approval, a decision that \
is theirs, a credential, their hands on something off this machine - ask them \
with the AskUserQuestion tool now, get the answer, and finish. A turn never \
ends waiting on the user. Where it named a condition that puts it outside the \
job, the sentence goes and nothing replaces it.

Do not explain the edit, do not apologise, and do not swap the line for a \
shorter version of itself. What the sentence said is still true either way; \
deleting a fact to get past this check makes the reply worse, not compliant."""

STANDING = """Ratchet: this turn is stopping to collect a yes it already has.

%s
Running SQL needs no approval and never will. Not a read, not a write, not a \
migration, not a schema change, not a destructive one. That was answered once \
for every statement that would ever follow, so there is nothing here for the \
user to decide and the question costs them a turn to repeat a word they have \
already said.

Run the statement now, and report what came back. Where it takes something \
with it, say what it took - after it is run, not instead of running it.

The standing yes covers SQL and stops there. Deploying an edge function, \
pausing or restoring a project, creating, merging, resetting or deleting a \
branch, moving money, sending mail and pushing code each still need their own \
approval, and a turn stopping for one of those is stopping correctly.

If the thing you stopped for is one of those - not a statement - ask for that \
approval with the AskUserQuestion tool now, get the answer, and finish; a turn \
never ends waiting on the user. If the database cannot be reached from this \
session, find the route - the Supabase tools, the Pi, a key through \
with-credential.py - before anything else. Wanting a second opinion on a \
statement you are able to run is not a reason to stop.

Do not ask again in softer words, do not describe the statement instead of \
running it, and do not delete the sentence and stop anyway."""


AGAIN = """%s: held again, on text this turn has not been held on before.

%s
The hold earlier in this turn says what to do, and it still applies."""

LAST = "\n\nThis is the last hold on this turn; the next stop goes through."

NARRATE = """Ratchet: this turn wrote prose before the work was finished, and the app drew it on the user's screen as narration.

%s
Write nothing until the finished reply. No progress notes - not what you are about to do, not what you just did, not a word between tool calls. Run the tools in silence and write the one reply when the work is done and proven. Re-run the tool you were about to; this will not stop that line again."""


# A check that lets a stop through can still have something to say about it. It
# goes here rather than straight to stdout, because the hook answers with one
# JSON object and a second one printed after it turns the whole answer to noise.
NOTES = []
EMITTED = []
# The session and user message the hooks are answering, so a notice already
# shown this turn is not shown again.
HERE = {}
# Set while the display hook asks "would the stop hook hold this?": nothing is
# printed and no hold is counted, the answers are only collected.
DRY = []
CAPTURED = []
# Set while the plain-answer path asks the rest of the pipeline whether it would
# hold a reply - the shape check is the one asking, so it sits that run out.
SKIP_SHAPE = []


def emit(failure=False, again=False, **body):
    """Print the hook's one answer.

    An on-screen notice is kept only when a check failed - a judge that could
    not be reached, a check that threw. A routine hold or release says nothing
    on screen, because the user reads only the finished reply; the turn being
    held reads the reason, and that is where the reason goes. `notices: all`
    in config.json brings every notice back. A notice already shown on this
    user message is not shown again, and a hold given `again` - its full
    reason already in the turn - carries no tail either.
    """
    if DRY:
        CAPTURED.append(body)
        return
    loud = str(config().get("notices", "failures")).lower() == "all"
    said = [text for text, bad in NOTES if bad or loud]
    if body.get("systemMessage") and (failure or loud):
        said.append(body["systemMessage"])
    body.pop("systemMessage", None)
    if "session" in HERE:
        fresh = [text for text in said if told(HERE["session"], HERE["turn"], "notice", text)]
        for text in fresh:
            tell(HERE["session"], HERE["turn"], "notice", text)
        said = fresh
    if (body.get("decision") == "block" and not again
            and not str(body.get("reason", "")).startswith("Shape:")):
        body["reason"] = body["reason"] + ("\n\nThe display hook kept that reply off the screen, "
                                           "so the user has not seen any of it.")
    if said:
        body["systemMessage"] = "; ".join(said)
    if not body:
        return
    EMITTED.append(True)
    print(json.dumps(body))


def hook(verb):
    result = checks(verb)
    if NOTES and not EMITTED:
        emit()
    return result


def checks(verb, payload=None, reply=None):
    cfg = config()
    if BOOL_ENV.get((os.environ.get("RATCHET_OFF") or "0").lower(), False):
        return 0
    sidechain = verb == "subagent"
    if sidechain and not cfg["subagents"]:
        return 0
    if payload is None:
        try:
            payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return 0
    transcript = payload.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return 0
    text, turn = closing_message(transcript, sidechain)
    if reply is not None:
        text = reply
    if not text:
        return 0
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    HERE.update(session=session, turn=turn)

    if not sidechain and shape_hold(cfg, payload, transcript, text, turn):
        return 0

    if not sidechain and recall_hold(cfg, payload, transcript, text, turn):
        return 0

    if not sidechain and premature_hold(cfg, payload, transcript, text, turn):
        return 0

    if not sidechain and unchecked_hold(cfg, payload, transcript, text, turn):
        return 0

    if not sidechain and refusal_hold(cfg, payload, transcript, text, turn):
        return 0

    found, hold, banned, standing = scan(text)
    if hold or (not found and not banned and not standing):
        return 0

    path, count = held_count(session, turn or "unknown")
    if count >= int(cfg["holds_per_turn"]):
        return 0
    last = LAST if count + 1 >= int(cfg["holds_per_turn"]) else ""

    if standing:
        quoted = "".join('  "%s"   [%s]\n' % (sentence, name) for name, sentence in standing[:4])
        how = told(session, turn, "standing", quoted)
        if how:
            record(path, turn or "unknown", count + 1)
            tell(session, turn, "standing", quoted)
            reason = STANDING % quoted if how == "full" else AGAIN % ("Ratchet", quoted)
            emit(again=how == "short", decision="block", reason=reason + last,
                 systemMessage="ratchet: the yes for SQL is already standing")
            return 0

    if banned:
        quoted = "".join('  "%s"   [%s]\n' % (sentence, name) for name, sentence in banned[:4])
        how = told(session, turn, "cut", quoted)
        if how:
            record(path, turn or "unknown", count + 1)
            tell(session, turn, "cut", quoted)
            reason = CUT % quoted if how == "full" else AGAIN % ("Ratchet", quoted)
            emit(again=how == "short", decision="block", reason=reason + last,
                 systemMessage="ratchet: cut the closing (%s)" % ", ".join(n for n, _ in banned[:3]))
            return 0

    if not found:
        return 0
    quoted = "".join('  "%s"   [%s]\n' % (sentence, name) for name, sentence in found[:4])
    how = told(session, turn, "work", quoted)
    if not how:
        return 0

    work = "finish the work named above"
    if cfg["judge"]:
        verdict, detail, problem = judge(text, found, cfg)
        if problem:
            if not cfg["judge_required"]:
                emit(failure=True, systemMessage="ratchet: %s - letting the stop through" % problem)
                return 0
            NOTES.append(("ratchet: %s - holding on the scan alone" % problem, True))
        elif verdict == "release":
            emit(systemMessage="ratchet: stopping is right here (%s)" % (detail or "no work left"))
            return 0
        elif detail:
            work = detail

    record(path, turn or "unknown", count + 1)
    tell(session, turn, "work", quoted)
    if how == "full":
        reason = REASON % (quoted, work)
    else:
        reason = AGAIN % ("Ratchet", quoted + "Do this now: %s\n" % work)
    emit(again=how == "short", decision="block", reason=reason + last,
         systemMessage="ratchet: held the stop (%s)" % ", ".join(n for n, _ in found[:3]))
    return 0


def shape_hold(cfg, payload, transcript, text, turn):
    """Hold a reply that is not in the reply shape, unless the user asked for another form.

    Walled off from the rest: a broken shape.json or a parse that throws lets this
    one check through and leaves every other check in the file running.
    """
    try:
        import shape
        if SKIP_SHAPE or BOOL_ENV.get((os.environ.get("SHAPE_OFF") or "0").strip().lower(), False):
            return False
        session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
        outcome = shape.decide(text, transcript, session, turn, cfg)
        if outcome[0] == "excused":
            NOTES.append(("shape: out of shape on request - %s" % outcome[1], False))
        if outcome[0] == "capped":
            NOTES.append(("shape: let a reply through out of shape after %d holds" % outcome[1], False))
        if outcome[0] != "hold":
            # A plain answer is shown as written at display time, and anything
            # excused or past the hold cap is let through. The display hook
            # never withholds a reply whose only recovery is an identical
            # resend, so there is nothing stranded off the screen to send back.
            return False
        _, found, why = outcome
        kind, quoted = shape_said(found)
        how = told(session, turn, kind, quoted)
        if not how:
            return False
        if not DRY:
            shape.count_hold(session, turn)
            tell(session, turn, kind, quoted)
    except Exception as problem:
        NOTES.append(("shape: the check failed (%s: %s) - let through unchecked"
                      % (type(problem).__name__, str(problem)[:80]), True))
        return False
    emit(again=how == "short",
         reason=shape.reason(found) if how == "full" else AGAIN % ("Shape", quoted),
         decision="block",
         systemMessage="shape: held a reply out of shape" + (" (judge: %s)" % why if why else ""))
    return True


def shape_said(found):
    """What a shape hold is recorded under: a hold on the layout and a hold on
    unfinished work say different things, so each is said once in full."""
    import shape
    return ("shape:unfinished" if shape.unfinished(found) else "shape"), shape.quote(found)


def would_hold(payload, text, skip_shape=False):
    """Whether the stop hook would send this reply back, asked before it is drawn.

    A reply in shape can still be held - work it names and did not do, a claim it
    never checked, something stated done that is still running - and the turn
    then carries on and writes another. Shown, that is the same reply twice. So
    the whole stop pipeline runs here first, on this text, with nothing printed
    and no hold counted; the judges it calls are cached, and the stop hook that
    follows gets the same answers without paying for them again.
    """
    DRY.append(True)
    if skip_shape:
        SKIP_SHAPE.append(True)
    del CAPTURED[:]
    try:
        checks("stop", payload=payload, reply=text)
    except Exception:
        return False
    finally:
        DRY.pop()
        if skip_shape:
            SKIP_SHAPE.pop()
    return any(body.get("decision") == "block" for body in CAPTURED)


HIDDEN = "\u200b"

# The desktop app gives a MessageDisplay hook ten seconds (Ho=1e4 beside the
# display dispatcher, build 2.1.295) and then shows the original text, which is
# exactly what running out of budget here does too, so the budget runs as close
# to that as is safe. At seven seconds a refusal judge, which answers in five to
# nine, lost the race about half the time and the refusal was drawn on screen
# before the stop hook held it.
DISPLAY_BUDGET = 9.2
START = time.time()


class OutOfTime(BaseException):
    """The display hook's budget ran out. A BaseException, so no check's own
    `except Exception` can turn it into a pass."""


def out_of_time(*_):
    raise OutOfTime()


def display():
    """MessageDisplay: keep text that is out of shape off the screen.

    The stop hook can send a reply back but cannot take back what was already
    drawn, so on its own every reply it held was seen twice - once wrong, once
    fixed. This runs before the text is drawn and decides with the same rules
    and the same per-turn state, so a reply the stop hook will hold is never
    shown, and one it will let through (excused, or past the hold limit) is.

    The desktop app calls this once per finished message with the whole text. A
    terminal calls it per batch of finished lines; those batches are kept back
    and joined, and the whole message is shown or withheld when the last one
    lands. Display only: what is stored and what the model reads are untouched.
    Any failure prints nothing, and the original text is shown.

    The desktop app streams text as it is written and treats an empty
    replacement as none, so the streamed text stays up. A whole message is
    hidden with a zero-width space, which it does draw in place of the text.
    """
    try:
        import shape
        if any(BOOL_ENV.get((os.environ.get(k) or "0").strip().lower(), False)
               for k in ("RATCHET_OFF", "SHAPE_OFF")):
            return 0
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        if payload.get("agent_id") or payload.get("agent_type"):
            return 0
        transcript = payload.get("transcript_path") or ""
        body = shape.rules()
        if shape.skipped(transcript, body):
            return 0
        session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
        delta = payload.get("delta") or ""
        final = bool(payload.get("final", True))
        whole = payload.get("index", 0) == 0 and final
        if not whole:
            text = shape.buffered(session, payload.get("message_id") or "", delta, final)
            if not final:
                emit(hookSpecificOutput={"hookEventName": "MessageDisplay", "displayContent": ""})
                return 0
        else:
            text = delta
        if not text.strip():
            return 0
        hidden = HIDDEN if whole else ""
        import signal
        signal.signal(signal.SIGALRM, out_of_time)
        signal.setitimer(signal.ITIMER_REAL, max(0.5, DISPLAY_BUDGET - (time.time() - START)))
        try:
            # One decision, shared with the stop hook. The stop hook can send a
            # reply back for any reason in the whole pipeline - out of shape, a
            # refusal, work the reply named and did not do, a claim it never made
            # good, a banned closing - and whatever it sends back the model
            # rewrites, so the rewrite renders. Drawing the first draft of any
            # reply the stop hook will hold is the double render. So every reply
            # is run through that exact pipeline here and hidden the moment it
            # would be held, whatever shape.decide called it - a genuine hold, an
            # in-shape reply a judge will catch, an excused /command reply the
            # refusal check or the scan will still stop, one past the hold cap.
            # The display hook never decides on a thinner view than the stop hook
            # does; that is what kept the two disagreeing, and a disagreement is
            # either a reply shown then rewritten (the double) or one hidden then
            # passed (the vanish).
            #
            # A plain answer is held off the screen the same way, now that the
            # work-judges stand down on it (shape.is_plain): the only holds left
            # that can reach a plain answer - a refusal, a banned-phrase scan -
            # rewrite it into DIFFERENT text, so hiding it never meets the
            # identical resend the app drops. A plain answer with nothing wrong
            # has would_hold False and is shown as written.
            shown = hidden if would_hold(payload, text) else text
        except OutOfTime:
            shown = text
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        if whole and shown == text:
            return 0
        emit(hookSpecificOutput={"hookEventName": "MessageDisplay", "displayContent": shown})
    except (Exception, OutOfTime):
        return 0
    return 0


def prompt_context():
    """UserPromptSubmit: put the reply shape in front of the turn before it writes.

    The shape hold runs on Stop, after the reply is already on screen, so it can
    fix a reply but never hide one. This is the half that keeps the first draft
    right. Same reach as the hold: skipped for headless runs and when either
    switch is off.
    """
    try:
        import shape
        if any(BOOL_ENV.get((os.environ.get(k) or "0").strip().lower(), False)
               for k in ("RATCHET_OFF", "SHAPE_OFF")):
            return 0
        try:
            payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            payload = {}
        body = shape.rules()
        if shape.skipped(payload.get("transcript_path") or "", body):
            return 0
        emit(hookSpecificOutput={"hookEventName": "UserPromptSubmit",
                                 "additionalContext": shape.reminder(body)})
    except Exception:
        return 0
    return 0


def premature_hold(cfg, payload, transcript, text, turn):
    """Hold a stop whose closing text states as done what is still running."""
    import premature
    import shape
    if shape.command(transcript, turn) or shape.is_plain(text, transcript, turn):
        return False
    flight = premature.in_flight(transcript)
    binary = claude_binary()
    if not flight or not binary or not cfg["judge"]:
        return False
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    path, count = held_count(session, (turn or "unknown") + ":premature")
    if count >= int(cfg["holds_per_turn"]):
        return False
    verdict, claim = premature.ask(text, flight, cfg, binary, judge_env(), str(ROOT), transcript)
    if verdict != "premature":
        return False
    quoted = '  "%s"\n' % (claim or "(see the closing text)")
    how = told(session, turn, "premature", quoted)
    if not how:
        return False
    record(path, (turn or "unknown") + ":premature", count + 1)
    tell(session, turn, "premature", quoted)
    emit(again=how == "short", decision="block",
         reason=premature.reason(claim, flight) if how == "full" else AGAIN % ("Ratchet", quoted),
         systemMessage="ratchet: stated as done while still in progress")
    return True


def recall_hold(cfg, payload, transcript, text, turn):
    """Hold a stop when the user flagged recurring work and the turn never read the transcripts."""
    import recall
    import shape
    if shape.command(transcript, turn) or shape.is_plain(text, transcript, turn):
        return False
    if not recall.should_hold(transcript):
        return False
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    path, count = held_count(session, (turn or "unknown") + ":recall")
    if count >= int(cfg["holds_per_turn"]):
        return False
    cue = recall.cue(transcript)
    quoted = '  "%s"\n' % (cue or "(see the message)")
    how = told(session, turn, "recall", quoted)
    if not how:
        return False
    record(path, (turn or "unknown") + ":recall", count + 1)
    tell(session, turn, "recall", quoted)
    emit(again=how == "short", decision="block",
         reason=recall.reason(cue) if how == "full" else AGAIN % ("Ratchet", quoted),
         systemMessage="ratchet: recurring work - search the transcripts first")
    return True


def unchecked_hold(cfg, payload, transcript, text, turn):
    """Hold a stop that hands back an inability the turn never checked for a route to."""
    import unchecked
    import shape
    if shape.command(transcript, turn) or shape.is_plain(text, transcript, turn):
        return False
    binary = claude_binary()
    if not binary or not cfg["judge"] or not unchecked.flags(text):
        return False
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    path, count = held_count(session, (turn or "unknown") + ":unchecked")
    if count >= int(cfg["holds_per_turn"]):
        return False
    verdict, claim = unchecked.ask(text, transcript, cfg, binary)
    if verdict != "unchecked":
        return False
    quoted = '  "%s"\n' % (claim or "(see the closing text)")
    how = told(session, turn, "unchecked", quoted)
    if not how:
        return False
    record(path, (turn or "unknown") + ":unchecked", count + 1)
    tell(session, turn, "unchecked", quoted)
    emit(again=how == "short", decision="block",
         reason=unchecked.reason(claim) if how == "full" else AGAIN % ("Ratchet", quoted),
         systemMessage="ratchet: said you can't without checking for a route")
    return True


def refusal_hold(cfg, payload, transcript, text, turn):
    """Hold a stop that turns down what the user asked for in chat, short of a hard limit."""
    import refusal
    if BOOL_ENV.get((os.environ.get("REFUSAL_OFF") or "0").strip().lower(), False):
        return False
    binary = claude_binary()
    if not binary or not cfg["judge"] or not refusal.flags(text):
        return False
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    path, count = held_count(session, (turn or "unknown") + ":refusal")
    if count >= int(cfg["holds_per_turn"]):
        return False
    verdict, found = refusal.ask(text, transcript, turn, cfg, binary)
    failed = None
    if verdict is None:
        # The first stage already found a refusal phrase, so a judge that
        # timed out or answered badly must not turn that into a pass. The
        # per-turn hold cap above bounds how long this can keep a stop.
        failed, verdict, found = found, "refused", None
    if verdict != "refused":
        return False
    quoted = '  "%s"\n' % ((found or ("", ""))[0] or refusal.flags(text) or "(see the closing text)")
    # With no judge to quote the refusal, the phrase alone - often just a No
    # verdict - is the same in every reply, so the reply itself is what is
    # compared.
    trigger = text if failed else quoted
    how = told(session, turn, "refusal", trigger)
    if not how:
        return False
    if failed:
        NOTES.append(("refusal: judge failed (%s) - holding on the phrase" % failed, True))
    record(path, (turn or "unknown") + ":refusal", count + 1)
    tell(session, turn, "refusal", trigger)
    # No LAST here. Told that the next stop goes through, a turn that is
    # refusing only has to say no once more to get its way - the reason the
    # shape hold never announces its cap either.
    emit(again=how == "short", decision="block",
         reason=refusal.reason(found) if how == "full" else AGAIN % ("Ratchet", quoted),
         systemMessage="ratchet: turned down what the user asked for")
    return True


def pre_ask():
    """PreToolUse on AskUserQuestion: refuse a card that asks again for what the user already asked for.

    Every judge call is paid here rather than gated on a phrase, because a card
    that re-asks is usually just the request read back - "Delete the three
    backups?" - with nothing for a word list to find. Shares the refusal check's
    count, so a turn is sent back three times per user message at most."""
    import refusal
    if any(BOOL_ENV.get((os.environ.get(k) or "0").strip().lower(), False)
           for k in ("RATCHET_OFF", "REFUSAL_OFF")):
        return 0
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except ValueError:
        return 0
    if payload.get("agent_id") or payload.get("agent_type"):
        return 0
    transcript = payload.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return 0
    cfg = config()
    binary = claude_binary()
    text = refusal.card(payload.get("tool_input"))
    if not binary or not cfg["judge"] or not text or refusal.HARD.search(text):
        return 0
    turn = closing_message(transcript, False)[1]
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    path, count = held_count(session, (turn or "unknown") + ":refusal")
    if count >= int(cfg["holds_per_turn"]):
        return 0
    verdict, found = refusal.ask(text, transcript, turn, cfg, binary, refusal.CARD_PROMPT)
    if verdict != "refused":
        return 0
    quoted = '  "%s"\n' % ((found or ("", ""))[0] or "(see the question)")
    how = told(session, turn, "card", quoted)
    if not how:
        return 0
    record(path, (turn or "unknown") + ":refusal", count + 1)
    tell(session, turn, "card", quoted)
    reason = refusal.card_reason(found) if how == "full" else AGAIN % ("Ratchet", quoted)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


def pre_say():
    """PreToolUse on Bash: refuse a Sunday announcement that states unfinished work as done."""
    import premature
    cfg = config()
    if BOOL_ENV.get((os.environ.get("RATCHET_OFF") or "0").lower(), False):
        return 0
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except ValueError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    spoken = premature.say_text((payload.get("tool_input") or {}).get("command", ""))
    transcript = payload.get("transcript_path")
    binary = claude_binary()
    if not spoken or not transcript or not binary or not cfg["judge"]:
        return 0
    flight = premature.in_flight(transcript)
    shown = premature.canvas_shown((payload.get("tool_input") or {}).get("command", ""), transcript)
    verdict, rule, claim = premature.ask_speech(spoken, flight, cfg, binary, transcript, shown)
    if verdict != "broken":
        return 0
    turn = closing_message(transcript, False)[1]
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    quoted = '  "%s"   [%s]\n' % (claim or spoken, rule)
    how = told(session, turn, "say", quoted)
    if not how:
        return 0
    tell(session, turn, "say", quoted)
    reason = premature.speech_reason(rule, claim) if how == "full" else AGAIN % ("Ratchet", quoted)
    emit(hookSpecificOutput={"hookEventName": "PreToolUse", "permissionDecision": "deny",
                             "permissionDecisionReason": reason})
    return 0


def pre_guard():
    """PreToolUse on the work tools: deny a tool call that follows narration.

    The shape says write nothing until the work is finished, and neither backstop
    can hold a turn to it. The display hook cannot: when a line of prose streams,
    the tool call that would mark it as narration does not exist in the transcript
    yet, so it is byte for byte a plain answer and is drawn. The stop hook will
    not: it judges only the sign-off after the last tool call and steps past
    everything before it. So prose between tool calls reaches the screen with
    nothing watching. The one moment it is detectable is the tool call it sits in
    front of, so the tool is denied here, once per piece of prose - the turn then
    stops writing more. It cannot pull back what already drew; the app streams a
    line as it is written and nothing can. It is the enforcement the other two
    structurally cannot give."""
    if BOOL_ENV.get((os.environ.get("RATCHET_OFF") or "0").strip().lower(), False):
        return 0
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except ValueError:
        return 0
    if payload.get("agent_id") or payload.get("agent_type"):
        return 0
    transcript = payload.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return 0
    prose = pending_prose(transcript)
    if len(prose.split()) < 3:
        return 0
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    turn = closing_message(transcript, False)[1]
    one = " ".join(prose.split())
    quoted = '  "%s"\n' % (one if len(one) <= 100 else one[:97] + "...")
    how = told(session, turn, "narrate", quoted)
    if not how:
        return 0
    tell(session, turn, "narrate", quoted)
    reason = NARRATE % quoted if how == "full" else AGAIN % ("Ratchet", quoted)
    emit(hookSpecificOutput={"hookEventName": "PreToolUse", "permissionDecision": "deny",
                             "permissionDecisionReason": reason})
    return 0


LOOP = [
    {"type": "user", "uuid": "u1", "message": {"content": "do the thing"}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Done. One quirk left in the parser."}]}},
    {"type": "user", "isMeta": True, "message": {"content":
        "Stop hook feedback:\nRatchet: this turn is ending while its own closing text "
        "names work it did not do."}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "The parser reads both forms now."}]}},
]


def transcript_test():
    """A held attempt must not be judged alongside the attempts it replaced.

    A hold comes back into the turn as a user row, and a walk that did not stop
    at one glued every attempt together. So a turn that fixed its closing line
    on the first hold was held again on the sentence it had just deleted, and
    the only thing that ever ended it was the count running out.
    """
    import tempfile
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8")
    with handle:
        for row in LOOP:
            handle.write(json.dumps(row) + "\n")
    try:
        text, turn = closing_message(handle.name, False)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
    bad = 0
    if "quirk" in text:
        bad += 1
        print("STALE:  the replaced attempt is still in the judged text")
    if text != "The parser reads both forms now.":
        bad += 1
        print("DRIFT:  closing message came back as %r" % text)
    if turn != "u1":
        bad += 1
        print("TURN:   holds keyed on %r, not the user message being answered" % turn)
    return bad


def said_test():
    """A hold is said once in full, once more only on new text, and never twice the same.

    Every hold is drawn in the conversation, and a check that fired again on the
    text it had already quoted put the same block on screen three times over.
    """
    import tempfile
    old = os.environ.get("RATCHET_STATE")
    os.environ["RATCHET_STATE"] = tempfile.mkdtemp()
    bad = 0
    try:
        steps = [(told("t", "u1", "recall", "see transcripts"), "full", "first hold"),
                 (tell("t", "u1", "recall", "see transcripts"), None, "record"),
                 (told("t", "u1", "recall", "See   transcripts."), None, "same text again"),
                 (told("t", "u1", "recall", "like last time"), "short", "new text, same check"),
                 (told("t", "u1", "refusal", "see transcripts"), "full", "another check"),
                 (told("t", "u2", "recall", "see transcripts"), "full", "next user message")]
        DRY.append(True)
        tell("t", "u2", "work", "dry")
        DRY.pop()
        steps.append((told("t", "u2", "work", "dry"), "full", "a dry run records nothing"))
        for got, want, name in steps:
            if got != want:
                bad += 1
                print("SAID:   %s came back %r, wanted %r" % (name, got, want))
    finally:
        if old is None:
            os.environ.pop("RATCHET_STATE", None)
        else:
            os.environ["RATCHET_STATE"] = old
    return bad


def selftest():
    body = json.loads((ROOT / "corpus.json").read_text())
    bad = 0
    for text in body.get("caught") or []:
        found, hold, banned, standing = scan(text)
        if (not found and not banned) or hold:
            bad += 1
            print("MISSED: %s" % " ".join(text.split())[:88])
    for text in body.get("banned") or []:
        found, hold, banned, standing = scan(text)
        if not banned or hold:
            bad += 1
            print("UNCUT:  %s" % " ".join(text.split())[:88])
    for text in body.get("standing") or []:
        found, hold, banned, standing = scan(text)
        if not standing or hold:
            bad += 1
            print("ASKED:  %s" % " ".join(text.split())[:88])
    for text in body.get("clear") or []:
        found, hold, banned, standing = scan(text)
        if standing:
            bad += 1
            print("SEIZED: %s  <- %s" % (" ".join(text.split())[:66], standing[0][0]))
        elif (found or banned) and not hold:
            bad += 1
            print("FALSE:  %s  <- %s" % (" ".join(text.split())[:66],
                                         (found or banned)[0][0]))
    bad += transcript_test()
    bad += said_test()
    import shape
    bad += shape.selftest()
    import refusal
    bad += refusal.selftest()
    print("selftest: %d caught, %d banned, %d standing, %d clear, 1 transcript, 1 said, %d wrong"
          % (len(body.get("caught") or []), len(body.get("banned") or []),
             len(body.get("standing") or []), len(body.get("clear") or []), bad))
    return 1 if bad else 0


def check(text):
    found, hold, banned, standing = scan(text)
    if hold:
        print('scan:  released on a blocker - "%s"' % hold)
        return 0
    if standing:
        print("scan:  the approval it is asking for is already standing - no judge, "
              "this is held")
        for name, sentence in standing:
            print('  [%s] "%s"' % (name, sentence))
        print("stop:  HELD")
        return 0
    if banned:
        print("scan:  banned closing prose - no judge, this is held")
        for name, sentence in banned:
            print('  [%s] "%s"' % (name, sentence))
        print("stop:  HELD")
        return 0
    if not found:
        print("scan:  released, nothing names unfinished work")
        return 0
    print("scan:  flagged")
    for name, sentence in found:
        print('  [%s] "%s"' % (name, sentence))
    cfg = config()
    if not cfg["judge"]:
        print("judge: off")
        return 0
    started = time.time()
    verdict, detail, problem = judge(text, found, cfg)
    took = time.time() - started
    if problem:
        print("judge: unreachable - %s (%.1fs)" % (problem, took))
        return 1
    print("judge: %s - %s (%.1fs, %s)" % (verdict, detail or "-", took, cfg["model"]))
    print("stop:  %s" % ("HELD" if verdict == "continue" else "let through"))
    return 0


def doctor():
    cfg = config()
    print("ratchet %s" % ROOT)
    for key in sorted(DEFAULTS):
        mark = "" if cfg[key] == DEFAULTS[key] else "   (overridden)"
        print("  %-22s %s%s" % (key, cfg[key], mark))
    holds, asks, standing, settled, forbidden, hunted = tells()
    print("  %-22s %d tells, %d banned, %d holds, %d standing, %d asks, %d settled"
          % ("tells.json", len(hunted), len(forbidden), len(holds), len(standing),
             len(asks), len(settled)))
    print("  %-22s %s" % ("claude binary", claude_binary() or "NOT FOUND"))
    print("  %-22s %s" % ("state", state_root()))
    settings = HOME / ".claude" / "settings.json"
    wired = []
    try:
        body = json.loads(settings.read_text())
        for phase, entries in (body.get("hooks") or {}).items():
            for entry in entries:
                for h in entry.get("hooks") or []:
                    if "ratchet" in h.get("command", ""):
                        wired.append(phase)
    except (OSError, ValueError):
        pass
    print("  %-22s %s" % ("wired into", ", ".join(sorted(set(wired))) or "NOTHING - not installed"))
    return selftest()


def main():
    verb = sys.argv[1] if len(sys.argv) > 1 else ""
    if verb in ("stop", "subagent"):
        try:
            return hook(verb)
        except Exception:
            return 0
    if verb == "say":
        try:
            return pre_say()
        except Exception:
            return 0
    if verb == "prompt":
        return prompt_context()
    if verb == "display":
        return display()
    if verb == "selftest":
        return selftest()
    if verb == "doctor":
        return doctor()
    if verb == "check":
        return check(" ".join(sys.argv[2:]) if len(sys.argv) > 2 else sys.stdin.read())
    print("usage: ratchet.py stop | subagent | check <text> | selftest | doctor", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
