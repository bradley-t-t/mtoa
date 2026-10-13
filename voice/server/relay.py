#!/usr/bin/env python3
"""Pass a spoken message to one of the owner's existing Claude Code chats.

    relay.py send <session id> "<message>"
    relay.py new <folder on the Mac> "<task>"
    relay.py ask <session id> "<question>"    waits up to ASK_WAIT seconds for that chat's answer; a
                                              question about where work stands goes to the desk, which
                                              keeps the record of every voice task, and so does anything
                                              asked of a session that never did work (a judge, a test)
    relay.py ask desk "<question>"            the same, put to the desk on his Mac, which has his
                                              accounts: mail, calendar, Teams, GitHub, Figma, Vercel
                                              and prints it; a later answer is said on its own

The chat is resumed headless on the machine it lives on (the Mac over
`ssh <desk.ssh>`, or this host), in its own working directory, and its reply
is spoken on the cube when it arrives. Sending returns at once, so the voice
turn is never held up by the other chat's work.

A chat open in the Mac's desktop app gets the message delivered into the live
session, through a headless Claude on the Mac using its session messaging; it
lands in that chat's queue if the chat is mid-turn, so nothing is ever refused
as busy. A chat that isn't open is resumed headless instead.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import mtoa_config  # the owner's names and hosts; see mtoa_config.py
except ImportError:  # run from another directory
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("mtoa_config", str(__import__("pathlib").Path(__file__).resolve().parent / "mtoa_config.py"))
    mtoa_config = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(mtoa_config)
_render = mtoa_config.render


def _render_all(table):
    return {key: _render(value) for key, value in table.items()}

# A killed relay still says its work has ended: SIGTERM unwinds like an exception,
# so every `working` block below reports "end" on the way out.
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

HERE = Path(__file__).resolve().parent
DB = HERE / "data" / "transcripts.sqlite3"
LOG = HERE / "data" / "relay.log"
MAC = mtoa_config.get("desk.ssh", "")
HOST = mtoa_config.get("host.ssh", "")
VOICE = (_render(' You are {assistant}. This work is {assistant}\'s own, so whatever is read aloud is {assistant} telling {user} about its own work, '
         'in the first person, from that one point of view.'))
SUFFIX = (_render("\n\n(Sent by voice through the {assistant} speaker. Reply in two or three plain sentences "
          "that read well aloud; they will be spoken back to {user}." + VOICE + ")"))
ASK_SUFFIX = SUFFIX.replace("(Sent by voice", "(Asked by voice")


@contextlib.contextmanager
def working(kind: str, label: str, status: str = "Working"):
    """Show this piece of work in the popup's queue while it runs. Never fails the work."""
    tid = f"{kind.lower().replace(' ', '-')}-{os.getpid()}-{time.time_ns()}"

    def tell(**q):
        try:
            urllib.request.urlopen("http://127.0.0.1:8794/activity?" + urllib.parse.urlencode(q), timeout=2).read()
        except OSError:
            pass

    tell(state="start", id=tid, kind=kind, label=label, status=status, pid=os.getpid())
    try:
        yield
    finally:
        tell(state="end", id=tid)


def log(*a):
    with open(LOG, "a") as f:
        print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, file=f)


def session(sid: str):
    db = sqlite3.connect(DB)
    if sid == "desk":
        # The desk on his Mac holds his app connectors: Outlook, Teams, GitHub, Figma and the rest.
        row = db.execute("select id, machine, cwd, title from sessions where title = ? order by rowid desc",
                         (DESK,)).fetchone()
    else:
        row = db.execute("select id, machine, cwd, title from sessions where id like ?", (sid + "%",)).fetchone()
    if not row:
        sys.exit(f"no indexed chat starts with {sid}")
    return row


def machine_token() -> str:
    # A Claude Code Bash tool may not pass its own login through; read it from the Pi's env file.
    for line in (Path.home() / ".claude" / "machine.env").read_text().splitlines():
        if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def on(machine: str, script: str, timeout: int) -> subprocess.CompletedProcess:
    """Run a shell script on the chat's machine. On the Mac, Claude Code cannot
    reach its keychain login over SSH, so this Pi's own token is handed over on
    stdin (never on a command line) and exported there for the one command."""
    if machine == "mac":
        remote = "IFS= read -r CLAUDE_CODE_OAUTH_TOKEN; export CLAUDE_CODE_OAUTH_TOKEN; exec zsh -lc " + shlex.quote(script)
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", MAC, remote]
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "") or machine_token()
        return subprocess.run(cmd, input=token + "\n", capture_output=True, text=True, timeout=timeout)
    return subprocess.run(["bash", "-lc", script], capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def say(text: str):
    url = "http://127.0.0.1:8794/say?text=" + urllib.parse.quote(text[:1500])
    try:
        urllib.request.urlopen(url, timeout=10).read()
    except OSError as e:
        log("could not speak the reply:", e)


SPEAK_BACK = (
    _render("\n\n(Sent by voice through the {assistant} speaker. After you reply, have your reply read aloud by ")
    + "running: ssh " + HOST + " \"curl -s --get localhost:8794/say --data-urlencode text='<your reply, two or "
    "three plain sentences>'\")" + VOICE
)


def send(sid: str, message: str):
    row = session(sid)
    if row[3] != DESK and machinery(row):
        # A judge or a delivery run never did the work, so a change of course goes to the desk.
        log("send rerouted to", DESK, "(never did any work), was for", row[3], "|", message)
        message += f"\n\n(This is about the work titled {json.dumps(row[3] or 'untitled')}.)"
        row = session("desk")
    sid, machine, cwd, title = row
    subprocess.Popen([sys.executable, __file__, "_run", sid, message],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print(f"passing it to '{title}' on {machine}; its reply will be spoken when it arrives")


def deliver_live(title: str, message: str) -> bool:
    """Hand the message to the live desktop session with this title, if one is open."""
    if not title:
        return False
    prompt = (
        "Call ListAgents. If a session titled exactly " + json.dumps(title) + " is listed, call SendMessage "
        "to it with exactly the text between the markers, unchanged, and then print DELIVERED. If no such "
        "session is listed, print NOT_LIVE and do nothing else.\n<<<\n" + message + SPEAK_BACK + "\n>>>"
    )
    p = on("mac", "cd ~ && claude -p --permission-mode bypassPermissions --output-format text "
                  + shlex.quote(prompt) + " < /dev/null", 300)
    log("live delivery to", title, "->", p.stdout.strip()[-200:] or p.stderr.strip()[-200:])
    return "DELIVERED" in p.stdout


def run(sid: str, message: str):
    sid, machine, cwd, title = session(sid)
    with working("Relay", f"To {title or 'a chat'}: {message}", "Waiting for Reply"):
        reply = _run(sid, machine, cwd, title, message)
    # Said once its own work has left the queue, so the speech gate doesn't read the
    # report of finished work as a claim about work still in progress.
    if reply:
        say(reply)


def _run(sid, machine, cwd, title, message):
    log("->", title, "|", message)
    if machine == "mac" and deliver_live(title, message):
        return ""   # the live chat speaks its own reply
    script = (f"cd {shlex.quote(cwd or '~')} && claude -p --resume {sid} --permission-mode bypassPermissions "
              f"--output-format json {shlex.quote(message + SUFFIX)} < /dev/null")
    try:
        p = on(machine, script, 1800)
        reply = json.loads(p.stdout.strip().splitlines()[-1]).get("result", "") if p.stdout.strip() else ""
        if not reply:
            reply = "I didn't get an answer on that one." if p.returncode == 0 else "That one failed on me: " + p.stderr.strip()[:200]
    except subprocess.TimeoutExpired:
        reply = "That's taking longer than half an hour, so I've stopped waiting on it."
    except (ValueError, IndexError) as e:
        reply = "I lost the answer to that one."
    log("<-", title, "|", reply)
    return reply


def new(folder: str, task: str):
    subprocess.Popen([sys.executable, __file__, "_new", folder, task],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print(f"started a new chat in {folder} on the Mac; its result will be spoken when it finishes")


DESK = "Sunday Desk"   # standing desktop chat that takes new work; messages reach it with no clicks


def run_new(folder: str, task: str):
    """A fresh Claude Code chat on the Mac, working in the project's own folder:
    handed to the Sunday Desk chat, or a background one when the desk is not open."""
    with working("New Chat", f"{Path(folder).name}: {task}", "Starting"):
        reply = _run_new(folder, task)
    if reply:
        say(reply)


def _run_new(folder: str, task: str):
    log("new chat in", folder, "|", task)
    brief = (_render("New task from {user} by voice.") + f" Work it in {folder}.\n\n{task}{NEW_SUFFIX}\n\n"
             "(Run it in a background agent with a complete brief, as you do for every task here, and keep "
             "taking messages. When it finishes, or needs his decision, have the answer read aloud as below.)")
    if deliver_live(DESK, brief):
        return ""   # the desk speaks the result itself
    log("desk not live; running headless, which lacks his app connectors")
    script = (f"cd {shlex.quote(folder)} && claude -p --permission-mode bypassPermissions "
              f"--output-format json {shlex.quote(task + NEW_SUFFIX)} < /dev/null")
    try:
        p = on("mac", script, 4 * 3600)
        out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        reply = out.get("result", "") or ("That one failed on me: " + p.stderr.strip()[:200])
        log("new chat", out.get("session_id", "?"), "finished |", reply)
    except subprocess.TimeoutExpired:
        reply = "That's been going for four hours, so I've stopped it."
    except (ValueError, IndexError) as e:
        reply = "I lost track of how that one finished."
    subprocess.run([sys.executable, str(HERE / "transcripts.py"), "sync"], capture_output=True)
    return reply


NEW_SUFFIX = (
    _render("\n\n(Started by voice through the {assistant} speaker. Do the whole task the way you would in any of {his} "
    "chats, following {his} usual workflow. When you finish, end with two or three plain sentences on what "
    "you did and anything {he} needs to decide." + VOICE + ")")
)


REPLIES = HERE / "data" / "replies"
# How long a spoken question waits on one lookup. Whatever is not back by then is
# said on its own when it arrives, so checking several pieces of work never hangs.
ASK_WAIT = float(os.environ.get("VOICE_ASK_WAIT", "30"))
ASK_LATE_WAIT = int(os.environ.get("VOICE_ASK_LATE_WAIT", "900"))   # how long a late answer is still awaited
# The same question to the same place inside these windows is never asked twice: an
# answer that just came back is reused, and one still out is waited on rather than doubled.
ASK_REUSE = float(os.environ.get("VOICE_ASK_REUSE", "150"))
ASK_JOIN = float(os.environ.get("VOICE_ASK_JOIN", "300"))
LATE_GRACE = 3.0     # a question waiting on the same lookup gets this long to take the answer first
STILL_CHECKING = "STILL CHECKING (lookup {key}): no answer on this yet. I'll say it on its own when it comes in."

# Status of a piece of work, as opposed to a question about its content.
STATUS = re.compile(
    r"\b(?:status|ship(?:ped)?|live|deploy(?:ed)?|releas(?:e|ed)|merged?|land(?:ed)?|done|finish(?:ed)?|"
    r"complete[d]?|sent|fixed|progress|yet|still|ready|verified|working|went out|gone out|how(?:'s| is| are| did)|"
    r"where (?:are|is|does|do) (?:we|it|things|that))\b", re.I)
# What a chat says when it has finished something.
FINISHED = re.compile(
    r"\b(?:released|shipped|merged|deployed|landed|pushed|committed|(?:it'?s|is|are|went|now) live|live as|"
    r"fixed|finished|installed|went out|verified)\b", re.I)
VOICE_MARKS = (_render("(Sent by voice through the {assistant} speaker"),
               _render("(Asked by voice through the {assistant} speaker"))


def completion(sid: str) -> str:
    """The chat's own last report of finishing work, or "" if it never made one. A reply to a
    status question relayed to it is not evidence: that is what a chat that never did the
    work says too, from whatever brief it was shown."""
    db = sqlite3.connect(DB)
    last, skip = "", False
    for role, text in db.execute("select role, text from messages where session = ? order by rowid", (sid,)):
        if role == "user":
            skip = any(m in text for m in VOICE_MARKS) and bool(STATUS.search(text.split("\n\n(")[0]))
        elif not skip and FINISHED.search(text):
            last = text
    return " ".join(last.split())[:500]


def route(sid: str, question: str):
    """Where a question really goes: (row, question to put, why it was moved or "").

    The desk keeps the record of every task started by voice and how it ended, so a
    question about where work stands goes there, whichever chat it was aimed at. A chat
    is evidence on status only if it did the work, and then its own report of finishing
    goes to the desk with the question. A session that never did any work - a judge, a
    delivery run, a test bench - answers nothing at all; whatever is asked of it goes to
    the desk. Its "not yet" can never be heard over the desk's record, because it is
    never asked."""
    row = session(sid)
    if row[3] == DESK:
        return row, question, ""
    junk = machinery(row)
    if not junk and not STATUS.search(question):
        return row, question, ""
    done = "" if junk else completion(row[0])
    context = (f"\n\n(This is about the work titled {json.dumps(row[3] or 'untitled')}. Answer from your record of "
               "what was asked by voice and how each task ended, checking the live result where it can be checked. "
               "A chat with that title that never did the work is no evidence either way."
               + (f" When the work finished, the chat that did it reported: {json.dumps(done)}" if done else "") + ")")
    return session("desk"), question + context, "never did any work" if junk else "status goes to the desk"


def machinery(row) -> bool:
    sys.path.insert(0, str(HERE))
    import transcripts
    return transcripts.machinery(sqlite3.connect(DB), row[0], row[2])


def ask_key(sid: str, question: str) -> str:
    said = " ".join(re.sub(r"[^a-z0-9]+", " ", question.lower()).split())
    return hashlib.sha1(f"{sid}|{said}".encode()).hexdigest()[:16]


@contextlib.contextmanager
def asks_locked():
    REPLIES.mkdir(parents=True, exist_ok=True)
    with open(REPLIES / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def state(key: str) -> dict:
    try:
        return json.loads((REPLIES / f"ask-{key}.json").read_text())
    except (OSError, ValueError):
        return {}


def save(key: str, st: dict):
    tmp = REPLIES / f"ask-{key}.tmp"
    tmp.write_text(json.dumps(st))
    tmp.rename(REPLIES / f"ask-{key}.json")


def ask(sid: str, question: str) -> str:
    """Put a question to one of his chats and wait up to ASK_WAIT for its answer, so the
    speaker can tell him in its own words. The asking itself runs on in the background:
    an answer that lands after the wait is said on its own, if no question took it."""
    aimed = session(sid)
    row, put, why = route(sid, question)
    if why:
        log("ask rerouted to", row[3], f"({why}), was for", aimed[3], "|", question)
    key = ask_key(row[0], question)
    with asks_locked():
        now = time.time()
        for old in REPLIES.glob("ask-*.json"):
            with contextlib.suppress(OSError):
                if now - old.stat().st_mtime > 86400:
                    old.unlink()
        st = state(key)
        if st.get("answer") and now - st.get("answered_at", 0) < ASK_REUSE:
            log("ask reused the answer from", f"{now - st['answered_at']:.0f}s ago:", row[3], "|", question)
            st["taken"] = True
            save(key, st)
            return st["answer"]
        if st and not st.get("answer") and now - st.get("asked", 0) < ASK_JOIN:
            log("ask joined the same question already out", f"{now - st['asked']:.0f}s:", row[3], "|", question)
        else:
            rid = f"r{time.time_ns()}"
            save(key, {"rid": rid, "asked": now, "question": question, "title": row[3], "answer": "",
                       "answered_at": 0, "taken": False, "said_late": False})
            subprocess.Popen([sys.executable, __file__, "_ask", rid, key, row[0], put],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    with working("Chat Lookup", f"{aimed[3] or 'a chat'}: {question}", "Waiting for Answer"):
        deadline = time.time() + ASK_WAIT
        while time.time() < deadline:
            with asks_locked():
                st = state(key)
                if st.get("answer") and not st.get("said_late"):
                    st["taken"] = True
                    save(key, st)
                    return st["answer"]
            time.sleep(0.5)
        return STILL_CHECKING.format(key=key)


def ask_behind(rid: str, key: str, sid: str, question: str):
    """The asking itself, detached from the question: deliver it, wait for the answer, and
    if no question took it within a moment, have it said on its own."""
    row = session(sid)
    _, machine, cwd, title = row
    asked = time.time()
    f = REPLIES / f"{rid}.txt"
    back = (_render("\n\n(Asked by voice through the {assistant} speaker, which") + f" is waiting on your answer. Answer in two or "
            f"three plain sentences, then post that answer back by running: ssh {HOST} \"cat > "
            f"~/voice-assistant/data/replies/{rid}.txt\" <<'EOF'\n<your answer>\nEOF){VOICE}")
    log("ask ->", title, "|", question)
    answer = ""
    if machine == "mac" and deliver_live(title, question + back):
        deadline = asked + ASK_LATE_WAIT
        while time.time() < deadline and not (f.exists() and f.read_text().strip()):
            time.sleep(2)
        if f.exists():
            time.sleep(0.5)
            answer = f.read_text().strip()
    else:
        script = (f"cd {shlex.quote(cwd or '~')} && claude -p --resume {sid} --permission-mode bypassPermissions "
                  f"--output-format json {shlex.quote(question + ASK_SUFFIX)} < /dev/null")
        try:
            p = on(machine, script, ASK_LATE_WAIT)
            answer = json.loads(p.stdout.strip().splitlines()[-1]).get("result", "")
        except (subprocess.TimeoutExpired, ValueError, IndexError):
            answer = ""
    log("ask <-", title, "|", answer or "(none)")
    with contextlib.suppress(OSError):
        f.unlink()
    with asks_locked():
        st = state(key)
        if not answer:
            # Nothing came back: the next asking of it starts afresh rather than joining this one.
            (REPLIES / f"ask-{key}.json").unlink(missing_ok=True)
            return
        st.update(answer=answer, answered_at=time.time())
        save(key, st)
    time.sleep(LATE_GRACE)
    with asks_locked():
        st = state(key)
        if st.get("taken"):
            return
        st["said_late"] = True
        save(key, st)
    late(st.get("question") or question, answer, st.get("asked") or asked)


def late(question: str, answer: str, asked: float):
    q = urllib.parse.urlencode({"about": question[:500], "text": answer[:1500], "asked": f"{asked:.0f}"})
    try:
        urllib.request.urlopen("http://127.0.0.1:8794/late?" + q, timeout=10).read()
    except OSError as e:
        log("could not pass on the late answer:", e)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "send":
        send(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "_run":
        run(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "ask":
        print(ask(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) == 6 and sys.argv[1] == "_ask":
        ask_behind(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif len(sys.argv) == 4 and sys.argv[1] == "new":
        new(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "_new":
        run_new(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
