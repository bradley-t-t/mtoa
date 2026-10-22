"""Wait: a turn never sits in the foreground waiting on a long job.

Nothing a turn writes reaches the screen until it ends, so a turn that waits on
a job somewhere else - a run on the host, CI, a deploy, a crawl - in a loop of
foreground commands shows the user nothing at all for as long as the job takes.
On 2026-10-22 a turn polled the host's first engine run in ten-minute waits for
three hours, and the user's only view of it was silence until they asked
whether it had hung.

So a foreground wait is denied once the turn has run long enough that waiting
more would keep the user looking at nothing: when the minutes the turn has
already run, plus the most the wait can take, pass BUDGET. The turn is told to
hand the wait to a background command, whose finish wakes the session, and to
end with what is finished and a Running line naming the job.

Deterministic, no judge, and it acts on the tool call, so nothing is waited on
and then regretted. A wait in the background (run_in_background, Monitor) is
never touched, and neither is ordinary work that happens to take a while.
"""
import calendar
import re
import time

BUDGET = 10  # minutes a turn may run before it stops waiting in the foreground

# A command whose job is to wait: a loop that sleeps until something is true, a
# watch that blocks until checks or a run finish, or a long bare sleep. Each has
# to stand where a command starts - the start of a line, after ; & | ( or do, or
# opening a quoted `bash -c` - so a file being written that only mentions one is
# not taken for it.
START = r"(?:^|[;&|(]|\bdo\b|\bthen\b|\bbash\s+-c\s*['\"]|\bsh\s+-c\s*['\"])\s*"
LOOP = re.compile(START + r"(?:until|while)\b[^\n]*?(?:\n[^\n]*?){0,6}\bsleep\s+\d", re.I | re.M)
WATCH = re.compile(START + r"(?:timeout\s+\d+\S*\s+)?gh\s+(?:pr\s+checks\b[^\n|;&]*--watch|run\s+watch)\b", re.I | re.M)
SLEEP = re.compile(START + r"sleep\s+(\d+)", re.M)
# The body of a heredoc is a file's text, not commands.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\b", re.S)

DENY = """Wait: this turn has run %d minutes, and the user has seen nothing of it - a turn's text stays off the screen until it ends. This wait could hold them there up to %d minutes more.

Do not wait here. Start the wait as a background command (run_in_background, a loop that exits when the job is done), so its finish wakes this session. Then end the turn now with what is finished - its own verdict, or **Started.** when nothing is - and a Running line that names the job and when it should end. Pick the work up again when the notification arrives."""


def waits(tool, given):
    """Whether the call is a foreground wait on something else finishing."""
    if tool != "Bash" or not isinstance(given, dict) or given.get("run_in_background"):
        return False
    command = HEREDOC.sub("<<heredoc", str(given.get("command") or ""))
    if LOOP.search(command) or WATCH.search(command):
        return True
    return any(int(n) >= 30 for n in SLEEP.findall(command))


def stamp(row):
    try:
        return calendar.timegm(time.strptime(str(row.get("timestamp"))[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def turn_minutes(rows, real_user_turn):
    """Minutes from the message that began this turn to the newest row."""
    began = last = None
    for row in rows:
        at = stamp(row)
        if at is None:
            continue
        last = at
        if real_user_turn(row) and not row.get("isSidechain"):
            began = at
    if began is None or last is None:
        return 0
    return max(0, (last - began) // 60)


def refuse(tool, given, rows, real_user_turn):
    """The denial for a foreground wait past the budget, or None."""
    if not waits(tool, given):
        return None
    ran = turn_minutes(rows, real_user_turn)
    most = int(given.get("timeout") or 120000) // 60000
    if ran + most <= BUDGET:
        return None
    return DENY % (ran, most)


# --- selftest -------------------------------------------------------------------

def selftest():
    def row(kind, minute, text="x"):
        stamp_text = "2026-10-22T16:%02d:00Z" % minute
        if kind == "user":
            return {"type": "user", "timestamp": stamp_text, "message": {"content": text}}
        return {"type": "assistant", "timestamp": stamp_text, "message": {"content": [{"type": "text", "text": text}]}}

    def real(r):
        return r.get("type") == "user"

    loop = {"command": "until ssh host 'grep -q done log'; do sleep 60; done", "timeout": 600000}
    cases = [
        ("a long wait late in a turn", [row("user", 0), row("assistant", 25)], loop, True),
        ("a long wait at the start of a turn", [row("user", 0), row("assistant", 1)], loop, True),
        ("a short wait early in a turn", [row("user", 0), row("assistant", 2)],
         {"command": "until curl -s x; do sleep 5; done", "timeout": 120000}, False),
        ("the same wait in the background", [row("user", 0), row("assistant", 25)],
         dict(loop, run_in_background=True), False),
        ("a checks watch late in a turn", [row("user", 0), row("assistant", 12)],
         {"command": "gh pr checks 7 --watch --required"}, True),
        ("a build late in a turn", [row("user", 0), row("assistant", 40)],
         {"command": "npm run build", "timeout": 600000}, False),
        ("a long bare sleep", [row("user", 0), row("assistant", 9)], {"command": "sleep 300"}, True),
        ("a wait inside bash -c", [row("user", 0), row("assistant", 30)],
         {"command": "timeout 580 bash -c 'until ssh h \"grep -q x f\"; do sleep 45; done'; ssh h tail f"}, True),
        ("a file written about waits", [row("user", 0), row("assistant", 30)],
         {"command": "python3 - <<'EOF'\ntext = '''while it runs, `gh pr checks --watch`\nand sleep 30 in a loop'''\nEOF\necho ok"}, False),
        ("a command that names a watch in a message", [row("user", 0), row("assistant", 30)],
         {"command": "git commit -m 'Stop waiting on gh pr checks --watch'"}, False),
    ]
    wrong = 0
    for name, rows, given, want in cases:
        got = refuse("Bash", given, rows, real) is not None
        if got != want:
            wrong += 1
            print("WRONG", name, "refused" if got else "let run")
    print("wait selftest: %d cases, %d wrong" % (len(cases), wrong))
    return wrong


if __name__ == "__main__":
    raise SystemExit(1 if selftest() else 0)
