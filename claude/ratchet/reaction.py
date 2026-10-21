#!/usr/bin/env python3
"""Reaction: notice when a reply missed, remember it everywhere, and ask before changing how replies are made.

The user's next message is the best judge of the last reply. "I didn't tell you
to do anything", "that is not a good summary?", "I just said without users",
asking the same thing again, a turn that needed holds to get anything out -
each says the reply system got something wrong, and none of it was read. The
same miss came back in the next chat, and the next, and the user had to say it
again every time.

  Every message is read two ways. The cues in reaction.json, the same question
  asked again and a turn held twice or more are caught the instant the message
  lands, and the turn starts with the miss in front of it. Everything else is
  read in context, by a model, alongside the turn: the reply, the user's
  earlier messages and the new one, however it is worded. A miss found that way
  holds the reply once with the miss and what the user wanted in front of it.

  Every miss is kept in ~/.claude/notes/misses/<machine>.jsonl - one file per
  machine, so no two machines ever write the same file - which config-sync
  carries to every other machine and cloud container. The history a turn sees
  is every machine's.

  The conversations that leave no Claude Code transcript are read too: the
  claude.ai chats the desktop app caches and the voice assistant's spoken
  conversation on the host (elsewhere.py). At most hourly, a sweep started
  from the prompt hook reads both, judges each new exchange the same two ways,
  and keeps what missed in the same ledger.

  A miss of a kind in `ask` that has happened before - anywhere - and was never
  followed by a change makes the question required: the stop hook holds a reply
  on that turn until an Adapt question has been answered, or the turn has
  changed the reply system itself. A settlement is then written beside the
  misses, and every open miss of that kind counts as adapted.
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
SYSTEM = [HOME / ".claude" / p for p in ("ratchet", "standards", "CLAUDE.md", "settings.json", "profile/CLAUDE.md")]
PASTED = re.compile(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", re.S)
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)

CONTEXT = """Your last reply may have missed. %s: "%s"%s

Before anything else, work out what the user actually wanted and answer that. Then find what in the reply system made the last reply miss - a rule in the reply forms below, a hold that pushed the rewrite, a check that let it through. When a change would make the next reply right, ask with AskUserQuestion, header "Adapt", with the concrete change as the options, and apply what is picked."""

HISTORY = """

The same kind of miss, earlier, with nothing changed since:
%s"""

MUST = """

It has happened before, so this turn asks that Adapt question before it ends: the stop hook holds a reply on this turn until one has been answered, or the reply system itself has been changed."""

HOLD = """Reaction: the user's message said the last reply missed - %s - and the same kind of miss has happened before with nothing changed, so this reply was kept off the screen and the user has not seen it.

  "%s"%s

Find what in the reply system made it miss, then ask with AskUserQuestion, header "Adapt", with the concrete change as the options - and apply what is picked. Answer the user's message as well."""

SEEN = """Reaction: read in context, the user's message says your last reply missed - %s - so this reply was kept off the screen and the user has not seen it.

  "%s"%s

Answer what the user actually wanted. If this reply already does, send it again as it is. Then find what in the reply system made the last reply miss; when a change would make the next reply right, ask with AskUserQuestion, header "Adapt", with the concrete change as the options, and apply what is picked."""

WANTED = """
  What they wanted: %s"""

LATE = """Read in context after that reply was already on screen, an earlier message of the user's said a reply missed - %s: "%s"%s
Take it into account in this reply.

"""

JUDGE = """You read exchanges between a user and an AI assistant, to find each place where the user's message says the assistant's previous reply missed what they wanted.

The reply missed when the user's message says it misread them or answered a different question (misread); did, built or proposed something they never asked for (unasked); left out or cut what they asked for (left out); was wrong, false or broken, or called something done that was not (wrong); repeated a mistake they had already corrected (again); or made them ask the same thing again (re-asked). Frustration aimed at the reply counts. A follow-up question, a new request, approval, thanks, or frustration aimed at something other than the reply does not.

Answer with JSON only, on one line: a list with one object per exchange, in order. An exchange that did not miss is {"n": 1, "missed": false}. One that did is {"n": 2, "missed": true, "kind": "misread", "wanted": "<what the user actually wanted, in one sentence>"}, with kind one of misread, unasked, left out, wrong, again or re-asked.

%s"""


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


def ledger_dir(default=False):
    """Where every machine's misses are kept: a path the mirror syncs. Tests point
    it elsewhere with REACTION_LEDGER, or keep it beside RATCHET_STATE."""
    if not default:
        if os.environ.get("REACTION_LEDGER"):
            return Path(os.environ["REACTION_LEDGER"])
        if os.environ.get("RATCHET_STATE"):
            return Path(os.environ["RATCHET_STATE"]) / "misses"
    return HOME / ".claude" / "notes" / "misses"


def machine():
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return "cloud"
    return re.sub(r"[^a-z0-9-]", "-", socket.gethostname().split(".")[0].lower()) or "unknown"


def ledger():
    """This machine's own file. No other machine writes it."""
    return ledger_dir() / ("%s.jsonl" % machine())


def migrate():
    """Carry the misses kept before the ledger moved into a synced path. Never
    into a ledger a test pointed elsewhere."""
    if ledger_dir() != ledger_dir(default=True):
        return
    old = state_root() / "misses.jsonl"
    try:
        lines = [l for l in old.read_text().splitlines() if l.strip()]
    except OSError:
        return
    path = ledger()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            entry.setdefault("machine", machine())
            entry.setdefault("source", "claude-code")
            entry.pop("adapted", None)
            handle.write(json.dumps(entry) + "\n")
    try:
        old.unlink()
    except OSError:
        pass


def entries():
    """Every machine's entries: misses and settlements."""
    migrate()
    out = []
    for path in sorted(ledger_dir().glob("*.jsonl")):
        try:
            for line in path.read_text().splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            continue
    return out


def adapted(miss, settlements):
    for s in settlements:
        if float(s.get("at") or 0) < float(miss.get("at") or 0):
            continue
        if s.get("fp") and s.get("fp") == miss.get("fp"):
            return s
        if set(s.get("kinds") or []) & set(miss.get("kinds") or []):
            return s
    return None


def misses():
    """Every machine's misses, oldest first, each with whether a change has followed it."""
    found = entries()
    settlements = [e for e in found if e.get("settled")]
    out, keys = [], set()
    for m in sorted((e for e in found if not e.get("settled")), key=lambda e: float(e.get("at") or 0)):
        if m.get("key"):
            if m["key"] in keys:
                continue
            keys.add(m["key"])
        done = adapted(m, settlements)
        out.append(dict(m, adapted={"at": done.get("at"), "how": done.get("how")} if done else None))
    return out


def append(entry, body=None, send=True):
    """Add an entry to this machine's file, pruned to keep_days, then send it on."""
    body = body or rules()
    cutoff = time.time() - 86400 * int(body.get("keep_days") or 60)
    path = ledger()
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    try:
        for line in path.read_text().splitlines():
            try:
                old = json.loads(line)
            except ValueError:
                continue
            if float(old.get("at") or 0) >= cutoff:
                kept.append(old)
    except OSError:
        pass
    entry = dict(entry, machine=machine())
    kept.append(entry)
    spare = path.with_suffix(".tmp%d" % os.getpid())
    spare.write_text("".join(json.dumps(e) + "\n" for e in kept))
    os.replace(spare, path)
    if send:
        publish()
    return entry


def publish():
    """Send the ledger to every other machine: config-sync, in the background."""
    if os.environ.get("REACTION_LEDGER") or os.environ.get("RATCHET_STATE"):
        return
    detach([str(HOME / ".claude" / "tools" / "config-sync.py")])


def detach(args):
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(HOME))
    except OSError:
        pass


def open_misses(kinds, body=None, before=None):
    """Earlier misses of these kinds, anywhere, within recur_days that nothing was changed for."""
    body = body or rules()
    cutoff = time.time() - 86400 * int(body.get("recur_days") or 14)
    return [m for m in misses()
            if float(m.get("at") or 0) >= cutoff and not m.get("adapted")
            and (before is None or float(m.get("at") or 0) < before)
            and set(m.get("kinds") or []) & set(kinds)]


def where(m):
    source = m.get("source") or "claude-code"
    if source == "claude-code":
        return m.get("machine") or "here"
    return {"voice": "spoken", "claude.ai": "claude.ai"}.get(source, source)


def history_text(history):
    return HISTORY % "\n".join('  - %s, %s, %s: "%s"' % (time.strftime("%b %-d", time.localtime(m["at"])), where(m),
                                                         ", ".join(m.get("kinds") or []), m.get("said") or "")
                                for m in history[-3:])


def jobs():
    return state_root() / "judged"


def job_path(session, fp):
    return jobs() / ("%s.%s.json" % (re.sub(r"[^A-Za-z0-9_-]", "_", session or "anon"), fp))


def note(session, transcript, prompt):
    """For the prompt hook: the context to put in front of the turn, or None.
    A cue records the miss now; anything else is read in context alongside the
    turn. Either way the hourly sweep of the other places is started when due."""
    if off():
        return None
    body = rules()
    sweep_due(body)
    reply, said, holds = earlier(transcript, prompt)
    if not said or clean(prompt).startswith("/"):
        return None
    kinds = signals(prompt, said, holds, body)
    now = time.time()
    late = "".join(LATE % (", ".join(j.get("kinds") or []), clip(j.get("said") or "", 240),
                           WANTED % j["wanted"] if j.get("wanted") else "")
                   for j in unseen(session, fingerprint(prompt)))
    if not kinds:
        path = job_path(session, fingerprint(prompt))
        path.parent.mkdir(parents=True, exist_ok=True)
        for stale in path.parent.glob("*.json"):
            try:
                if now - stale.stat().st_mtime > 86400:
                    stale.unlink()
            except OSError:
                pass
        path.write_text(json.dumps({"state": "pending", "at": now, "session": session, "fp": fingerprint(prompt),
                                    "said": prompt, "reply": reply, "earlier": said[-3:]}))
        detach([sys.executable, str(ROOT / "reaction.py"), "judge", str(path)])
        return late.strip() or None
    asked = [k for k in kinds if k in (body.get("ask") or [])]
    history = open_misses(asked or kinds, body, before=now)
    must = bool(asked and open_misses(asked, body, before=now))
    append({"at": now, "session": session, "fp": fingerprint(prompt), "kinds": kinds, "source": "claude-code",
            "said": clip(prompt, 240), "reply": clip(reply, 240), "must": must}, body)
    text = CONTEXT % (", ".join(kinds), clip(prompt, 240), "")
    if history:
        text += history_text(history)
    if must:
        text += MUST
    return (late + "\n\n" + text).strip() if late else text


# --- reading in context ---------------------------------------------------------

def ask_model(exchanges, timeout=None):
    """One model call over up to a handful of exchanges: a verdict for each, or None."""
    sys.path.insert(0, str(ROOT))
    import ratchet
    binary = ratchet.claude_binary()
    if not binary or not exchanges:
        return None
    blocks = []
    for n, x in enumerate(exchanges, 1):
        earlier_text = "\n".join("  - " + clip(e, 300) for e in (x.get("earlier") or [])[-3:]) or "  (none)"
        blocks.append("Exchange %d\nThe user's earlier messages:\n%s\nThe assistant's reply:\n  %s\nThe user's next message:\n  %s"
                      % (n, earlier_text, clip(x.get("reply") or "", 1500), clip(x.get("said") or "", 800)))
    cfg = dict(ratchet.config())
    if timeout:
        cfg["judge_timeout_seconds"] = timeout
    try:
        raw, code = ratchet.ask_claude(binary, JUDGE % "\n\n".join(blocks), cfg)
    except (subprocess.TimeoutExpired, OSError):
        return None
    try:
        text = json.loads(raw).get("result") or ""
    except ValueError:
        text = raw
    found = re.search(r"\[.*\]", text, re.S)
    if not found:
        return None
    try:
        answers = json.loads(found.group(0))
    except ValueError:
        return None
    out = [None] * len(exchanges)
    for a in answers if isinstance(answers, list) else []:
        try:
            n = int(a.get("n")) - 1
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= n < len(out):
            out[n] = a
    return out


def verdict_kinds(answer, body):
    if not answer or not answer.get("missed"):
        return []
    kind = str(answer.get("kind") or "").strip().lower()
    known = list((body.get("cues") or {}).keys()) + ["re-asked"]
    return [kind if kind in known else "misread"]


def judge(job_file):
    """The background half of note(): read one message in context and keep it if it missed."""
    path = Path(job_file)
    try:
        job = json.loads(path.read_text())
    except (OSError, ValueError):
        return 1
    body = rules()
    answers = ask_model([job]) or [None]
    kinds = verdict_kinds(answers[0], body)
    result = dict(job, state="done", missed=bool(kinds), kinds=kinds,
                  wanted=str((answers[0] or {}).get("wanted") or ""), reached=answers[0] is not None)
    if kinds:
        asked = [k for k in kinds if k in (body.get("ask") or [])]
        must = bool(asked and open_misses(asked, body, before=float(job.get("at") or time.time())))
        append({"at": float(job.get("at") or time.time()), "session": job.get("session"), "fp": job.get("fp"),
                "kinds": kinds, "source": "claude-code", "read": "context", "said": clip(job.get("said") or "", 240),
                "reply": clip(job.get("reply") or "", 240), "wanted": clip(result["wanted"], 240), "must": must}, body)
    result.pop("reply", None)
    result.pop("earlier", None)
    spare = path.with_suffix(".tmp%d" % os.getpid())
    spare.write_text(json.dumps(result))
    os.replace(spare, path)
    return 0


# --- the other places -----------------------------------------------------------

def sweep_due(body):
    stamp = state_root() / "sweep.json"
    try:
        last = float(json.loads(stamp.read_text()).get("at") or 0)
    except (OSError, ValueError, TypeError):
        last = 0
    if time.time() - last < 3600 * float(body.get("sweep_hours") or 1):
        return
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({"at": time.time()}))
    except OSError:
        return
    detach([sys.executable, str(ROOT / "reaction.py"), "sweep"])


def sweep(read_only=False):
    """Read the claude.ai cache and the spoken conversation for misses not yet judged."""
    sys.path.insert(0, str(ROOT))
    import elsewhere
    body = rules()
    days = float(body.get("recur_days") or 14)
    found = {"claude.ai": [], "voice": []}
    try:
        found["claude.ai"] = elsewhere.claude_ai(days)
    except Exception:
        pass
    try:
        found["voice"] = elsewhere.voice(days)
    except Exception:
        pass
    if read_only:
        print(json.dumps({k: len(v) for k, v in found.items()}))
        return 0
    seen_file = state_root() / "swept.json"
    try:
        seen = json.loads(seen_file.read_text())
    except (OSError, ValueError):
        seen = {}
    seen.update({m["key"]: m.get("at") for m in misses() if m.get("key")})
    fresh = [x for v in found.values() for x in v if x["key"] not in seen]
    fresh.sort(key=lambda x: x["at"])
    fresh = fresh[-int(body.get("sweep_max") or 40):]
    cued, rest = [], []
    for x in fresh:
        kinds = signals(x["said"], x.get("earlier") or [], 0, body)
        (cued if kinds else rest).append((x, kinds))
    judged = []
    for start in range(0, len(rest), 8):
        batch = [x for x, _ in rest[start:start + 8]]
        answers = ask_model(batch, timeout=int(body.get("sweep_timeout") or 150))
        if answers is None:
            continue
        judged.extend((x, verdict_kinds(a, body), a) for x, a in zip(batch, answers))
    kept = 0
    for x, kinds, answer in [(x, k, None) for x, k in cued] + judged:
        seen[x["key"]] = x["at"]
        if not kinds:
            continue
        append({"at": x["at"], "session": x.get("session"), "fp": fingerprint(x["said"]), "key": x["key"],
                "kinds": kinds, "source": x["source"], "read": "context" if answer else "cue",
                "said": clip(x["said"], 240), "reply": clip(x["reply"], 240),
                "wanted": clip(str((answer or {}).get("wanted") or ""), 240), "must": False}, body, send=False)
        kept += 1
    cutoff = time.time() - 86400 * days
    seen = {k: v for k, v in seen.items() if float(v or 0) >= cutoff}
    seen_file.parent.mkdir(parents=True, exist_ok=True)
    seen_file.write_text(json.dumps(seen))
    # Hourly, whether or not anything was kept here: the sync is also what brings
    # every other machine's misses to this one.
    publish()
    print(json.dumps({"read": {k: len(v) for k, v in found.items()}, "judged": len(cued) + len(judged), "kept": kept}))
    return 0


def this_turn(transcript, turn):
    """(the text this turn answers, when it was written, the Adapt answers this
    turn, whether it edited the reply system)."""
    sys.path.insert(0, str(ROOT))
    import ratchet
    import calendar
    current, at, seen, asks, answers, edited = "", 0.0, turn is None, {}, [], False
    for row in ratchet.rows(transcript):
        if row.get("isSidechain"):
            continue
        if not seen:
            if row.get("uuid") == turn:
                seen, current = True, "\n".join(ratchet.parts(row, "text"))
                try:
                    at = calendar.timegm(time.strptime(str(row.get("timestamp"))[:19], "%Y-%m-%dT%H:%M:%S"))
                except (ValueError, OverflowError):
                    at = 0.0
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
    return current, at, answers, edited


def record_for(session, fp, since=0.0):
    """This turn's own miss: its words, its session, kept no earlier than the turn began."""
    return next((m for m in reversed(misses())
                 if m.get("fp") == fp and (not session or not m.get("session") or m.get("session") == session)
                 and float(m.get("at") or 0) >= since - 120), None)


def mark_seen(session, fp):
    path = job_path(session, fp)
    try:
        job = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if not job.get("seen"):
        job["seen"] = True
        spare = path.with_suffix(".tmp%d" % os.getpid())
        spare.write_text(json.dumps(job))
        os.replace(spare, path)


def unseen(session, fp):
    """Misses read in context for this session's earlier messages whose turn never
    saw them - the reply was already on screen before the reading came back."""
    out = []
    for path in sorted(jobs().glob("%s.*.json" % re.sub(r"[^A-Za-z0-9_-]", "_", session or "anon"))):
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if job.get("state") == "done" and job.get("missed") and not job.get("seen") and job.get("fp") != fp:
            out.append(job)
            mark_seen(session, job.get("fp"))
    return out


def wait_for(session, fp, dry):
    """The background reading of this turn's message, waited for when it is still running."""
    path = job_path(session, fp)
    limit = float(rules().get("judge_wait") or 20)
    stop = time.time() + limit
    while True:
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if job.get("state") == "done":
            return job
        if time.time() >= stop or time.time() - float(job.get("at") or 0) > 90:
            return None
        time.sleep(0.4)


def settle(record, how):
    """Write a settlement: this miss, and every open miss sharing an ask kind, counts as adapted."""
    body = rules()
    kinds = set(record.get("kinds") or []) & set(body.get("ask") or []) or set(record.get("kinds") or [])
    append({"settled": True, "at": time.time(), "fp": record.get("fp"), "kinds": sorted(kinds),
            "how": clip(how, 240)}, body)


def check(transcript, turn, dry=False, session=None):
    """For the stop hook: None to let the reply through, or (how, reason) -
    "context" for a miss read in context that the turn has not seen yet, "adapt"
    for a repeat that needs the Adapt question."""
    if off():
        return None
    current, at, answers, edited = this_turn(transcript, turn)
    if not current:
        return None
    session = session or Path(transcript).stem
    fp = fingerprint(current)
    record = record_for(session, fp, at)
    if not record:
        job = wait_for(session, fp, dry)
        if not job or not job.get("missed"):
            return None
        record = record_for(session, fp, at)
        if not record:
            return None
    if not dry:
        mark_seen(session, fp)
    if answers or edited:
        if not dry and not record.get("adapted"):
            settle(record, answers[-1] if answers else "changed the reply system")
        return None
    wanted = WANTED % record["wanted"] if record.get("wanted") else ""
    kinds = ", ".join(record.get("kinds") or [])
    if record.get("must") and not record.get("adapted"):
        return "adapt", HOLD % (kinds, record.get("said") or "", wanted)
    if record.get("read") == "context":
        text = SEEN % (kinds, record.get("said") or "", wanted)
        history = [m for m in open_misses(record.get("kinds") or [], before=float(record.get("at") or 0))]
        if history:
            text += history_text(history)
        return "context", text
    return None


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
    import tempfile
    saved = os.environ.get("REACTION_LEDGER")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["REACTION_LEDGER"] = tmp
        now = time.time()
        Path(tmp, "elsewhere.jsonl").write_text(json.dumps({"at": now - 60, "machine": "elsewhere", "fp": "a" * 16,
                                                            "kinds": ["misread"], "said": "x"}) + "\n")
        if len(open_misses(["misread"], body, before=now)) != 1:
            wrong += 1
            print("WRONG: a miss kept on another machine is not history here")
        append({"settled": True, "at": now, "fp": "b" * 16, "kinds": ["misread"], "how": "test"}, body)
        if open_misses(["misread"], body, before=now + 1):
            wrong += 1
            print("WRONG: a settlement did not close the miss before it")
        Path(tmp, "elsewhere.jsonl").write_text(Path(tmp, "elsewhere.jsonl").read_text() + json.dumps(
            {"at": now + 5, "machine": "elsewhere", "fp": "c" * 16, "key": "k1", "kinds": ["wrong"], "said": "y"}) + "\n"
            + json.dumps({"at": now + 6, "machine": "other", "fp": "c" * 16, "key": "k1", "kinds": ["wrong"], "said": "y"}) + "\n")
        if len([m for m in misses() if m.get("key") == "k1"]) != 1:
            wrong += 1
            print("WRONG: the same exchange swept on two machines counted twice")
    if saved is None:
        os.environ.pop("REACTION_LEDGER", None)
    else:
        os.environ["REACTION_LEDGER"] = saved
    sys.path.insert(0, str(ROOT))
    import elsewhere
    sample = b"hello hello hello hello"
    try:
        literal = bytes([len(sample)]) + bytes([(len(sample) - 1) << 2]) + sample
        if elsewhere.unsnappy(literal) != sample:
            raise ValueError
        copied = bytes([len(sample)]) + bytes([(6 - 1) << 2]) + b"hello " + bytes([((len(sample) - 6) - 1) << 2 | 2]) + (6).to_bytes(2, "little")
        if elsewhere.unsnappy(copied) != sample:
            raise ValueError
    except Exception:
        wrong += 1
        print("WRONG: snappy did not unpack a literal and a copy")
    v8 = b'"\x04treeo"\x10conversationUuid"\x01c"\x08messagesA\x01o"\x04uuid"\x01m"\x06sender"\x05human"\x07contentA\x01o"\x04type"\x04text"\x04text"\x02hi{\x02$\x00\x01{\x04$\x00\x01{\x02'
    got = list(elsewhere.trees(v8))
    if not got or elsewhere.words_of(got[0]["messages"][0]) != "hi":
        wrong += 1
        print("WRONG: a cached conversation tree was not read")
    print("reaction selftest: %d cases, %d wrong" % (len(CASES) + 7, wrong))
    return wrong


if __name__ == "__main__":
    verb = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if verb == "judge":
        raise SystemExit(judge(sys.argv[2]))
    if verb == "sweep":
        raise SystemExit(sweep(read_only="--read-only" in sys.argv))
    raise SystemExit(1 if selftest() else 0)
