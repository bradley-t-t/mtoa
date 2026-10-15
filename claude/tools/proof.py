#!/usr/bin/env python3
"""Prove a fix where the problem was seen: one check that fails before the change and passes after.

A reply may say Fixed, Done, Shipped or Built only when this tool holds a record,
from the same turn, of one check failing while the problem was there and the
same check passing once it was gone. Ratchet's stop hook reads that record, not
the reply. Without a fail-then-pass pair the reply is sent back to say "Not
verified." or "Not fixed.", and with one, its Proof line has to carry the
evidence line printed here, word for word, so the proof is the check's and not
the writer's.

  proof.py before --where site --says "the site still serves 2026.42.7" \\
      --problem "<the user's own words>" -- 'curl -s https://x.com/release.json | grep -qv 2026.42.7'
  proof.py after <id>            the same command again; it has to pass now
  proof.py before --where app --says "..." --problem "..." --ask
  proof.py after <id> --asked    the user's answer to an AskUserQuestion with the options Gone and Still there
  proof.py show [<id>] [--full]  this session's records, with the checks' output

A check is a shell command whose exit status is the answer: non-zero while the
problem is there, zero once it is gone. A check that already passes before the
change never saw the problem, so it is refused as proof. --where names where the
problem was seen, and the command has to read that place:

  site     a live URL, never localhost
  app      the screen, through desktop.py, screencapture or osascript
  voice    Sunday on the host, over ssh
  chat     a session transcript, or usage.py
  file     a file that exists
  command  the output of a command the problem names

Where only the user's eyes or hands can tell, --ask makes their report the
before, and their own answer to a question card the after. --problem has to
quote the user's own words from this session, at least three of them, so the
check is tied to what they reported rather than to something easier.

Records live in ~/.claude/state/proof/<session>.jsonl, and ratchet's guard keeps
every tool but this one from writing there.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".claude" / "state" / "proof"
PLACES = {"site": "the live site", "app": "the app", "voice": "Sunday", "chat": "the chat",
          "file": "the file", "command": "the command output"}
LOCAL_HOSTS = re.compile(r"^(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|[^/:]+\.(?:localhost|test|local))(?::\d+)?$", re.I)
GONE, STILL = "gone", "still there"


def ratchet():
    sys.path.insert(0, str(HOME / ".claude" / "ratchet"))
    import ratchet as engine
    return engine


def session():
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if not sid:
        sys.exit("proof.py: no CLAUDE_CODE_SESSION_ID - run it from inside a session")
    found = sorted((HOME / ".claude" / "projects").glob("*/%s.jsonl" % sid),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return sid, (str(found[0]) if found else "")


def turn(transcript):
    return ratchet().closing_message(transcript, False)[1] if transcript else None


def squash(text):
    text = re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', text or ""))
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S)
    return " ".join(text.lower().split())


def user_said(transcript, quote):
    """Whether the quote is the user's own words somewhere in this session."""
    want = squash(quote)
    if len(want.split()) < 3:
        return False
    engine = ratchet()
    for row in engine.rows(transcript):
        if row.get("isSidechain") or not engine.real_user_turn(row):
            continue
        if want in squash("\n".join(engine.parts(row, "text"))):
            return True
    return False


def reads_place(where, command, problem):
    """None when the command reads the place it claims, else why it does not."""
    if where == "site":
        hosts = re.findall(r"https?://([^/\s'\"]+)", command)
        if not hosts:
            return "a site check needs the live URL in the command"
        if all(LOCAL_HOSTS.match(h) for h in hosts):
            return "a site check has to read the live site, not a local server"
        return None
    if where == "app":
        if re.search(r"desktop\.py|screencapture|osascript", command):
            return None
        return "an app check has to read the screen (desktop.py, screencapture or osascript), or use --ask"
    if where == "voice":
        host = ""
        try:
            sys.path.insert(0, str(HOME / ".claude" / "tools"))
            import _lib
            host = _lib.setting("host.ssh") or ""
        except Exception:
            pass
        if re.search(r"\bssh\b", command) and (not host or host in command):
            return None
        return "a voice check has to read Sunday on the host over ssh, or use --ask"
    if where == "chat":
        if re.search(r"\.claude/projects|usage\.py", command):
            return None
        return "a chat check has to read a session transcript or run usage.py, or use --ask"
    if where == "file":
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = command.split()
        for token in tokens:
            path = Path(os.path.expanduser(token))
            if "/" in token and path.exists():
                return None
        return "a file check has to name a file that exists"
    if where == "command":
        names = {Path(t).name for t in re.findall(r"[\w./~-]+", command) if len(Path(t).name) > 2}
        if any(name.lower() in squash(problem) for name in names):
            return None
        return "a command check has to run a command the problem names"
    return "unknown place %r" % where


def log_path(sid):
    return STATE / ("%s.jsonl" % sid)


def records(sid):
    out = []
    try:
        for line in log_path(sid).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def append(sid, record):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(log_path(sid), "a") as handle:
        handle.write(json.dumps(record) + "\n")


def run(command, cwd=None):
    try:
        done = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True, timeout=300,
                              cwd=cwd if cwd and Path(cwd).is_dir() else None)
        return done.returncode, (done.stdout + done.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return 124, "timed out after 300 seconds"


def clock(stamp):
    return datetime.fromisoformat(stamp).strftime("%-I:%M %p").lower().replace(" am", " a.m.").replace(" pm", " p.m.")


def evidence(before, after):
    """The Proof line's evidence, written by code from the two runs."""
    place, says = PLACES[before["where"]], before["says"]
    if before.get("ask"):
        return 'Check %s in %s: you reported "%s", and at %s you answered Gone' % (
            before["id"], place, says, clock(after["time"]))
    return 'Check %s in %s showed "%s" at %s and showed it gone at %s' % (
        before["id"], place, says, clock(before["time"]), clock(after["time"]))


def plain_says(text):
    text = re.sub(r"[.!?;]+", "", " ".join((text or "").split())).strip()
    if not text or len(text.split()) > 12:
        sys.exit("proof.py: --says is what the check shows while the problem is there, in 12 words or fewer")
    return text


def cmd_before(args):
    sid, transcript = session()
    says = plain_says(args.says)
    if not args.problem or not user_said(transcript, args.problem):
        sys.exit("proof.py: --problem has to quote the user's own words from this session, three words at least")
    # One argument is a shell string as written; several are words, quoted back
    # together so `-- bash -c '...'` runs as it was typed.
    command = (args.command[0] if len(args.command) == 1 else shlex.join(args.command)).strip()
    if args.ask:
        if command:
            sys.exit("proof.py: --ask takes no command - the user's report is the before")
    else:
        if not command:
            sys.exit("proof.py: give the check after --, a command that fails while the problem is there")
        why = reads_place(args.where, command, args.problem)
        if why:
            sys.exit("proof.py: %s" % why)
    now = datetime.now().isoformat(timespec="seconds")
    ident = hashlib.sha1((sid + now + command + says).encode()).hexdigest()[:6]
    record = {"id": ident, "phase": "before", "turn": turn(transcript), "time": now,
              "where": args.where, "says": says, "problem": args.problem, "command": command,
              "cwd": os.getcwd(), "ask": bool(args.ask)}
    if args.ask:
        record.update(exit=1, output="reported by the user")
        append(sid, record)
        print("Check %s recorded from your report. After the change, ask with AskUserQuestion, "
              "options exactly \"Gone\" and \"Still there\", then run: proof.py after %s --asked" % (ident, ident))
        return 0
    code, output = run(command)
    record.update(exit=code, output=output)
    append(sid, record)
    if code == 0:
        print("Check %s passes already, so it never saw the problem and cannot prove a fix.\n"
              "Write a check that fails while the problem is there." % ident)
        return 2
    print("Check %s failed (exit %d), so it sees the problem. After the change run: proof.py after %s"
          % (ident, code, ident))
    if output.strip():
        print(output.strip()[-800:])
    return 0


def asked_answer(transcript, after_turn):
    """The user's answer to this turn's last Gone / Still there question card."""
    engine = ratchet()
    seen, asks, answer = after_turn is None, {}, None
    for row in engine.rows(transcript):
        if row.get("isSidechain"):
            continue
        if not seen:
            seen = row.get("uuid") == after_turn
            continue
        for part in engine.parts(row, "tool_use"):
            if part.get("name") == "AskUserQuestion":
                for q in (part.get("input") or {}).get("questions") or []:
                    labels = {squash(o.get("label")) for o in q.get("options") or []}
                    if {GONE, STILL} <= labels:
                        asks[part.get("id")] = q.get("question") or ""
        for part in engine.parts(row, "tool_result"):
            question = asks.get(part.get("tool_use_id"))
            if question is None:
                continue
            given = ((row.get("toolUseResult") or {}).get("answers") or {}).get(question)
            if given is None:
                content = part.get("content")
                content = content if isinstance(content, str) else json.dumps(content)
                hit = re.search(r'"%s"="([^"]*)"' % re.escape(question), content)
                given = hit.group(1) if hit else ""
            answer = (question, given)
    return answer


def cmd_after(args):
    sid, transcript = session()
    befores = [r for r in records(sid) if r.get("id") == args.id and r.get("phase") == "before"]
    if not befores:
        sys.exit("proof.py: no check %s in this session" % args.id)
    before = befores[-1]
    if not before.get("ask") and int(before.get("exit") or 0) == 0:
        sys.exit("proof.py: check %s passed before the change, so it cannot prove anything" % args.id)
    now = datetime.now().isoformat(timespec="seconds")
    current = turn(transcript)
    record = {"id": args.id, "phase": "after", "turn": current, "time": now}
    if before.get("ask"):
        if not args.asked:
            sys.exit("proof.py: check %s is the user's report - ask them, then run with --asked" % args.id)
        found = asked_answer(transcript, current)
        if not found:
            sys.exit("proof.py: no answered question this turn with the options \"Gone\" and \"Still there\"")
        question, given = found
        code = 0 if squash(given) == GONE else 1
        record.update(exit=code, output='%s = %s' % (question, given))
    else:
        code, output = run(before["command"], before.get("cwd"))
        record.update(exit=code, output=output)
    append(sid, record)
    if code != 0:
        print("Check %s still fails (exit %d): the problem is still there, and the reply says \"Not fixed.\""
              % (args.id, code))
        if record["output"].strip():
            print(record["output"].strip()[-800:])
        return 1
    print("Check %s passes. Put this in the Proof line, word for word:\n\n%s" % (args.id, evidence(before, record)))
    return 0


def cmd_show(args):
    sid, _ = session()
    for r in records(sid):
        if args.id and r.get("id") != args.id:
            continue
        keys = ("id", "phase", "time", "where", "says", "exit", "command") + (("output",) if args.full else ())
        print(json.dumps({k: r.get(k) for k in keys}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    before = sub.add_parser("before")
    before.add_argument("--where", required=True, choices=sorted(PLACES))
    before.add_argument("--says", required=True)
    before.add_argument("--problem", required=True)
    before.add_argument("--ask", action="store_true")
    before.add_argument("command", nargs=argparse.REMAINDER)
    after = sub.add_parser("after")
    after.add_argument("id")
    after.add_argument("--asked", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("id", nargs="?")
    show.add_argument("--full", action="store_true", help="the checks' output too")
    args = parser.parse_args()
    if args.verb == "before":
        args.command = args.command[1:] if args.command[:1] == ["--"] else args.command
        return cmd_before(args)
    if args.verb == "after":
        return cmd_after(args)
    return cmd_show(args)


if __name__ == "__main__":
    sys.exit(main())
