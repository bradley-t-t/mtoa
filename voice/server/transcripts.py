#!/usr/bin/env python3
"""Searchable memory of every Claude Code chat on this machine and the Mac.

Keeps a compact full-text index of what was said in each session - the
person's messages and Claude's replies, without tool output - so the voice
assistant can answer "where are we at on X" from the actual conversations.

    transcripts.py sync                 pull new and changed sessions from both machines
    transcripts.py search "query"       sessions that best match, newest weighted up
    transcripts.py recent [--days 3]    sessions touched lately, with titles
    transcripts.py read <session> [--tail 30]   the end of one conversation

The extractor below runs on each machine as plain Python with no
dependencies; the Mac is read over `ssh <desk.ssh>`, so nothing is
installed there.
"""

import argparse
import contextlib
import json
import os
import socket
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
    _spec = _ilu.spec_from_file_location("mtoa_config", str(Path(__file__).resolve().parent / "mtoa_config.py"))
    mtoa_config = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(mtoa_config)

DB = Path(os.environ.get("TRANSCRIPTS_DB", Path(__file__).resolve().parent / "data" / "transcripts.sqlite3"))
MAC = mtoa_config.get("desk.ssh", "")

EXTRACT = r'''
import json, os, re, sys, time
from pathlib import Path
since = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
root = Path.home() / ".claude" / "projects"
noise = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z]+|task-notification)>.*?</\1>", re.S)

def text_of(content):
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(b.get("text", ""))
    return "\n".join(out)

for f in root.glob("*/*.jsonl"):
    try:
        mtime = f.stat().st_mtime
    except OSError:
        continue
    if mtime <= since:
        continue
    title = custom = cwd = None
    msgs = []
    try:
        for line in open(f, errors="replace"):
            if '"type":"user"' not in line and '"type":"assistant"' not in line and 'itle"' not in line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            t = m.get("type")
            if t == "ai-title":
                title = m.get("aiTitle") or title
            elif t == "custom-title":
                custom = m.get("customTitle") or custom
            elif t in ("user", "assistant"):
                if m.get("isSidechain") or m.get("isMeta") or m.get("toolUseResult") is not None:
                    continue
                cwd = m.get("cwd") or cwd
                text = noise.sub("", text_of((m.get("message") or {}).get("content"))).strip()
                if not text or text.startswith("[Request interrupted"):
                    continue
                msgs.append([m.get("timestamp", ""), t, text[:6000]])
    except OSError:
        continue
    if msgs:
        print(json.dumps({"session": f.stem, "title": custom or title or "", "cwd": cwd or "",
                          "mtime": mtime, "messages": msgs}))
'''


def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
        create table if not exists sessions (
            id text primary key, machine text, title text, cwd text,
            started text, updated text, mtime real, turns integer);
        create virtual table if not exists messages using fts5(
            session unindexed, ts unindexed, role unindexed, text, tokenize = 'porter');
        create table if not exists sync_state (machine text primary key, since real);
    """)
    return c


def ingest(c: sqlite3.Connection, machine: str, lines) -> int:
    n = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        s = json.loads(line)
        msgs = s["messages"]
        c.execute("delete from messages where session = ?", (s["session"],))
        c.executemany("insert into messages (session, ts, role, text) values (?, ?, ?, ?)",
                      [(s["session"], ts, role, text) for ts, role, text in msgs])
        c.execute("""insert into sessions (id, machine, title, cwd, started, updated, mtime, turns)
                     values (?, ?, ?, ?, ?, ?, ?, ?)
                     on conflict(id) do update set title = excluded.title, cwd = excluded.cwd,
                       updated = excluded.updated, mtime = excluded.mtime, turns = excluded.turns""",
                  (s["session"], machine, s["title"], s["cwd"], msgs[0][0], msgs[-1][0], s["mtime"], len(msgs)))
        n += 1
    return n


def sync(args):
    c = db()
    started = time.time()
    sources = [("sunday", [sys.executable, "-c", EXTRACT])]
    if MAC:
        sources.append(("mac", ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", MAC, "python3", "-"]))
    for machine, cmd in sources:
        row = c.execute("select since from sync_state where machine = ?", (machine,)).fetchone()
        since = row[0] if row else 0.0
        if cmd[0] == "ssh":
            cmd = cmd + [str(since)]
            p = subprocess.run(cmd, input=EXTRACT, capture_output=True, text=True, timeout=600)
        else:
            p = subprocess.run(cmd + [str(since)], capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            print(f"{machine}: unreachable ({p.stderr.strip()[:120]})")
            continue
        n = ingest(c, machine, p.stdout.splitlines())
        # A little overlap, so a file written during the read is picked up next time.
        c.execute("insert into sync_state values (?, ?) on conflict(machine) do update set since = excluded.since",
                  (machine, started - 120))
        c.commit()
        print(f"{machine}: {n} sessions updated")


def ago(iso: str) -> str:
    try:
        t = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return iso
    d = time.time() - t
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return f"{int(d // 86400)} days ago"


# Sessions that are machinery rather than work: the stop-check and speech-gate judges,
# the relay's own delivery runs, the news and barge checks, test benches. Their titles
# echo the work they judged ("Dome Break end-to-end check and fix"), so a title search
# finds them, but they never did any of it; resumed and asked for status, they answer
# from the brief they judged, which is how a judge said "not shipped" about shipped work.
MACHINERY_CWDS = ("/.claude/ratchet", "/gate-test", "/barge-test", "/voice-assistant/data")
MACHINERY_OPENERS = (
    "Call ListAgents.", "You are the second stage of a stop-check", "You check one", "Reply with JSON only",
    "Sunday is a voice assistant. Below is", "SUNDAY WAS ANSWERING:", "HIS QUESTION:", "RULES TO JUDGE:",
    "This is an announcement:", "This is an late answer:", "The assistant just said:",
    "Transcript:", "You enforce four rules", "You screen what",
)


def opener(c: sqlite3.Connection, sid: str) -> str:
    row = c.execute("select text from messages where session = ? and role = 'user' order by rowid limit 1",
                    (sid,)).fetchone()
    return (row[0] if row else "").lstrip()


def machinery(c: sqlite3.Connection, sid: str, cwd: str) -> bool:
    """True for a session that only ever judged, relayed or tested: never a place work was done."""
    cwd = cwd or ""
    if cwd == "/tmp" or cwd.startswith("/tmp/") or any(m in cwd for m in MACHINERY_CWDS):
        return True
    first = opener(c, sid)
    if first.startswith("[") and "] " in first[:40] and not first.startswith(("[SCHEDULED", "[ROUTINE")):
        first = first.split("] ", 1)[1]     # the brain's own "[Monday 7:50 PM] ..." turns
    return first.startswith(MACHINERY_OPENERS) or first.startswith(("EARLIER EXCHANGES", "HIS NEW QUESTION", "(Context only.", "(The conversation so far"))


def where(cwd: str) -> str:
    desk, host = mtoa_config.get("desk.home", ""), mtoa_config.get("host.home", "")
    if desk:
        cwd = cwd.replace(desk.rstrip("/") + "/", "~/")
    if host:
        cwd = cwd.replace(host.rstrip("/") + "/", "voice-host:~/")
    return cwd


def search(args):
    c = db()
    q = " OR ".join(f'"{w}"' for w in args.query.replace('"', " ").split() if len(w) > 2) or '""'
    rows = c.execute("""
        with hits as (
            select session, bm25(messages) score, snippet(messages, 3, '[', ']', ' ... ', 24) snip
            from messages where messages match ?)
        select h.session, s.title, s.machine, s.cwd, s.updated, count(*), min(h.score),
               (select snip from hits h2 where h2.session = h.session order by h2.score limit 1)
        from hits h join sessions s on s.id = h.session
        group by h.session
        order by min(h.score) + (julianday('now') - julianday(substr(s.updated, 1, 19))) * 0.6
        limit ?""", (q, args.limit * 6)).fetchall()
    rows = [r for r in rows if not machinery(c, r[0], r[3])][:args.limit]
    for sid, title, machine, cwd, updated, hits, best, snip in rows:
        print(f"{sid}  {title or '(untitled)'}  [{machine} {where(cwd)}, {ago(updated)}, {hits} matching messages]")
        print(f"    {snip}")
    if not rows:
        print("no matching sessions")


def recent(args):
    c = db()
    rows = c.execute("""select id, title, machine, cwd, updated, turns from sessions
                        where julianday('now') - julianday(substr(updated, 1, 19)) < ?
                        order by updated desc limit ?""", (args.days, args.limit * 8)).fetchall()
    rows = [r for r in rows if not machinery(c, r[0], r[3])][:args.limit]
    for sid, title, machine, cwd, updated, turns in rows:
        print(f"{sid}  {title or '(untitled)'}  [{machine} {where(cwd)}, {ago(updated)}, {turns} messages]")


def read(args):
    c = db()
    s = c.execute("select id, title, machine, cwd from sessions where id like ?", (args.session + "%",)).fetchone()
    if not s:
        sys.exit("no such session")
    print(f"{s[1]}  [{s[2]} {where(s[3])}]")
    rows = c.execute("select ts, role, text from messages where session = ? order by rowid", (s[0],)).fetchall()
    for ts, role, text in rows[-args.tail:]:
        print(f"\n--- {role} {ts[:16]}\n{text[:args.chars]}")


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


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync").set_defaults(fn=sync)
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--limit", type=int, default=8); p.set_defaults(fn=search)
    p = sub.add_parser("recent"); p.add_argument("--days", type=float, default=3); p.add_argument("--limit", type=int, default=25); p.set_defaults(fn=recent)
    p = sub.add_parser("read"); p.add_argument("session"); p.add_argument("--tail", type=int, default=30)
    p.add_argument("--chars", type=int, default=1500); p.set_defaults(fn=read)
    a = ap.parse_args()
    if a.cmd == "sync":
        a.fn(a)      # the five-minute refresh is housekeeping, not something he is waiting on
        return
    label = {"search": lambda: f"Searching Chats: {a.query}", "recent": lambda: "Listing Recent Chats",
             "read": lambda: f"Reading Chat {a.session[:8]}"}[a.cmd]()
    with working("Transcripts", label, "Searching"):
        a.fn(a)


if __name__ == "__main__":
    main()
