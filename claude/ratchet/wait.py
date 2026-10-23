"""Wait: a turn never sits in the foreground waiting on a job.

Nothing a turn writes reaches the screen until it ends, so a turn that waits on
a job somewhere else - a run on the host, CI, a deploy, a crawl - in foreground
commands shows the user nothing at all for as long as the wait lasts. On
2026-10-22 a turn polled the host's first engine run in ten-minute waits for
three hours, and the user's only view of it was silence until they asked
whether it had hung. Minutes are the same hang, only shorter.

So every foreground wait that can last longer than LIMIT seconds is denied,
however old the turn is: a loop that sleeps until something is true, a checks or
run watch, a long bare sleep. The turn is told to start the wait in the
background and end now with what is finished and a Running line naming the job.

Deterministic, no judge, and it acts on the tool call, so nothing is waited on
and then regretted. A wait in the background (run_in_background, Monitor) is
never touched, and neither is ordinary work that happens to take a while.
"""
import re

LIMIT = 60  # seconds a foreground wait may last

# A command whose job is to wait: a loop that sleeps until something is true, a
# watch that blocks until checks or a run finish, or a long bare sleep. Each has
# to stand where a command starts - the start of a line, after ; & | ( or do, or
# opening a quoted `bash -c` - so a file being written that only mentions one is
# not taken for it.
START = r"(?:^|[;&|(]|\bdo\b|\bthen\b|\bbash\s+-c\s*['\"]|\bsh\s+-c\s*['\"])\s*"
LOOP = re.compile(START + r"(?:until|while)\b[^\n]*?(?:\n[^\n]*?){0,6}\bsleep\s+\d", re.I | re.M)
WATCH = re.compile(START + r"(?:timeout\s+\d+\S*\s+)?gh\s+(?:pr\s+checks\b[^\n|;&]*--watch|run\s+watch)\b", re.I | re.M)
SLEEP = re.compile(START + r"sleep\s+(\d+)", re.M)
# A `timeout N` in front of the wait caps it below the tool's own timeout.
CAP = re.compile(START + r"timeout\s+(\d+)(s|m|h)?\b", re.M)
# The body of a heredoc is a file's text, not commands.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\b", re.S)

DENY = """Wait: this call waits in the foreground on something else finishing, for up to %s, and the user sees nothing of this turn until it ends - a wait of minutes is a hang the same as one of hours.

Do not wait here. If this session needs the result, start the wait as a background command (run_in_background, a loop that exits when the job is done). Either way, end the turn now with what is finished - its own verdict, or **Started.** when nothing is - and a Running line naming the job and when it ends. What has to happen after the job belongs to the job itself - a step the host or CI runs on its own - never to a promise that this session will come back, because a session can end first."""


def command(given):
    return HEREDOC.sub("<<heredoc", str(given.get("command") or ""))


def waits(tool, given):
    """Whether the call is a foreground wait on something else finishing."""
    if tool != "Bash" or not isinstance(given, dict) or given.get("run_in_background"):
        return False
    text = command(given)
    if LOOP.search(text) or WATCH.search(text):
        return True
    return any(int(n) > LIMIT for n in SLEEP.findall(text))


def longest(given):
    """The most seconds the call can run: its own timeout, or a `timeout N` in it."""
    most = int(given.get("timeout") or 120000) // 1000
    for amount, unit in CAP.findall(command(given)):
        most = min(most, int(amount) * {"": 1, "s": 1, "m": 60, "h": 3600}[unit])
    return most


def spoken(seconds):
    return "%d seconds" % seconds if seconds < 120 else "%d minutes" % (seconds // 60)


def refuse(tool, given, rows=None, real_user_turn=None):
    """The denial for a foreground wait that can outlast LIMIT, or None."""
    if not waits(tool, given):
        return None
    most = longest(given)
    if most <= LIMIT:
        return None
    return DENY % spoken(most)


# --- selftest -------------------------------------------------------------------

def selftest():
    loop = {"command": "until ssh host 'grep -q done log'; do sleep 60; done", "timeout": 600000}
    cases = [
        ("a long wait late in a turn", loop, True),
        ("a two-minute wait at the start of a turn", {"command": "until curl -s x; do sleep 5; done"}, True),
        ("a wait held to thirty seconds", {"command": "timeout 30 bash -c 'until curl -s x; do sleep 1; done'"}, False),
        ("a wait whose tool timeout is a minute", {"command": "until curl -s x; do sleep 2; done", "timeout": 60000}, False),
        ("the same wait in the background", dict(loop, run_in_background=True), False),
        ("a checks watch", {"command": "gh pr checks 7 --watch --required"}, True),
        ("a build", {"command": "npm run build", "timeout": 600000}, False),
        ("a long bare sleep", {"command": "sleep 300"}, True),
        ("a short bare sleep", {"command": "sleep 5; curl -s x"}, False),
        ("a wait inside bash -c", {"command": "timeout 580 bash -c 'until ssh h \"grep -q x f\"; do sleep 45; done'; ssh h tail f", "timeout": 600000}, True),
        ("a file written about waits", {"command": "python3 - <<'EOF'\ntext = \'\'\'while it runs, `gh pr checks --watch`\nand sleep 30 in a loop\'\'\'\nEOF\necho ok"}, False),
        ("a command that names a watch in a message", {"command": "git commit -m 'Stop waiting on gh pr checks --watch'"}, False),
    ]
    wrong = 0
    for name, given, want in cases:
        got = refuse("Bash", given) is not None
        if got != want:
            wrong += 1
            print("WRONG", name, "refused" if got else "let run")
    print("wait selftest: %d cases, %d wrong" % (len(cases), wrong))
    return wrong


if __name__ == "__main__":
    raise SystemExit(1 if selftest() else 0)
