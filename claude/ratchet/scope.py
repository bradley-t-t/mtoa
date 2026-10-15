"""Scope: usage means all usage unless the user names less.

When the user asks how the assistant behaves - holds, repeats, narration,
anything that recurs - they mean every session on every machine, not the chat in
view. A turn that opens one transcript to answer that has already narrowed the
question before anything is written, so the first read of a session transcript
in a turn is denied until `tools/usage.py` has run: it reads every session on
this machine and the host, and a narrower read is free after it.

Deterministic, no judge, and it acts on the tool call rather than the reply, so
nothing is drafted narrow and then held.
"""
import re

USAGE = re.compile(r"(?:^|[/\s])usage\.py\b")
# A proof.py check of a chat reads that one chat on purpose: it is where the
# problem was seen.
PROOF = re.compile(r"(?:^|[/\s])proof\.py\b")

# A read of a session transcript: the projects tree on any machine, or the app's
# own transcript tools.
TRANSCRIPT_PATH = re.compile(r"\.claude/projects\b(?!/[^/\s'\"]+/memory\b)")
TRANSCRIPT_TOOLS = re.compile(r"search_session_transcripts|export_transcript")
READERS = ("Read", "Grep", "Glob", "Bash")

DENY = """Scope: usage means all usage - every session on this machine and the host - unless the user named less, and this turn is opening one transcript before looking at the rest.

Run ~/.claude/tools/usage.py first (holds, or search <regex>). A narrower read of one transcript is free after it."""


def reads_transcript(tool, given):
    if TRANSCRIPT_TOOLS.search(tool or ""):
        return True
    if tool not in READERS or not isinstance(given, dict):
        return False
    text = " ".join(str(given.get(k) or "") for k in ("file_path", "path", "pattern", "glob", "command"))
    return bool(TRANSCRIPT_PATH.search(text)) and not USAGE.search(text) and not PROOF.search(text)


def ran_usage(rows):
    """Whether the current turn has already run usage.py, from transcript rows."""
    ran = False
    for row in rows:
        if row.get("isSidechain"):
            continue
        kind = row.get("type")
        content = (row.get("message") or {}).get("content")
        if kind == "user" and not row.get("isMeta"):
            if isinstance(content, str) and content.strip():
                ran = False
            elif isinstance(content, list) and any(
                    isinstance(p, dict) and p.get("type") == "text" and p.get("text", "").strip()
                    for p in content):
                ran = False
        elif kind == "assistant" and isinstance(content, list):
            for p in content:
                if (isinstance(p, dict) and p.get("type") == "tool_use" and p.get("name") == "Bash"
                        and USAGE.search(str((p.get("input") or {}).get("command") or ""))):
                    ran = True
    return ran


def should_deny(tool, given, rows):
    return reads_transcript(tool, given) and not ran_usage(rows)


CASES = [
    ("Read", {"file_path": "/Users/x/.claude/projects/-Users-x/abc.jsonl"}, [], True),
    ("Bash", {"command": "grep -c hook ~/.claude/projects/-Users-x/abc.jsonl"}, [], True),
    ("Bash", {"command": "ssh voice-host 'ls ~/.claude/projects'"}, [], True),
    ("Grep", {"pattern": "hold", "path": "/Users/x/.claude/projects"}, [], True),
    ("mcp__ccd_session_mgmt__search_session_transcripts", {"query": "x"}, [], True),
    ("Bash", {"command": "~/.claude/tools/usage.py search hook"}, [], False),
    ("Bash", {"command": "cd ~/.claude/projects && ~/.claude/tools/usage.py"}, [], False),
    ("Read", {"file_path": "/Users/x/.claude/ratchet/shape.py"}, [], False),
    ("Bash", {"command": "ls ~/Development"}, [], False),
    ("Bash", {"command": "~/.claude/tools/proof.py before --where chat --says x --problem y -- 'grep -c z ~/.claude/projects/a/b.jsonl'"}, [], False),
    ("Read", {"file_path": "/Users/x/.claude/projects/-Users-x/memory/MEMORY.md"}, [], False),
    ("Read", {"file_path": "/Users/x/.claude/projects/-Users-x/abc.jsonl"}, [
        {"type": "user", "message": {"content": "why does this keep happening"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "~/.claude/tools/usage.py"}}]}},
    ], False),
    ("Read", {"file_path": "/Users/x/.claude/projects/-Users-x/abc.jsonl"}, [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "~/.claude/tools/usage.py"}}]}},
        {"type": "user", "message": {"content": "and now this one"}},
    ], True),
]


def selftest():
    wrong = 0
    for tool, given, rows, want in CASES:
        got = should_deny(tool, given, rows)
        if got != want:
            wrong += 1
            print("WRONG", tool, given, "wanted", want, "got", got)
    print("scope selftest: %d cases, %d wrong" % (len(CASES), wrong))
    return wrong


if __name__ == "__main__":
    raise SystemExit(1 if selftest() else 0)
