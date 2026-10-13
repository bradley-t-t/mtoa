#!/usr/bin/env python3
"""Deliver a routine's outcome to the owner, and only when it needs him.

Installed at ~/bin/routine-deliver.py. Every routine's run script
calls it once, last, with the routine's id, the exit status of the turn, and the
log the turn wrote.

WHO HEARS ABOUT A RUN. A routine that did its job is not news. The morning
report already reads every routine's status file and says at six who ran and who
did not, so a message per run buys nothing and costs the one thing that makes a
message worth reading, which is that its arrival means something. This sends
only when a person has to do something: the turn failed, or it finished and said
itself that a step did not complete.

HOW A RUN SAYS WHICH IT WAS. The last line a routine prints is

    ROUTINE-STATUS: ok
    ROUTINE-STATUS: attention <one line saying what needs a person>

and the prompts require it. A run that printed no line at all is an attention
case rather than a quiet one: a turn that died before its summary looks exactly
like a turn that had nothing to say, and the whole point of this file is that
those two never look alike.

WHAT NOW HAPPENS BEFORE THE MESSAGE. An attention case used to be delivered the
moment it was found: the reason, the tail of the log, and nothing done about it.
The owner read it, opened a session, and fixed by hand what a turn on this
machine could have fixed itself. So an attention case now dispatches one repair
turn first - a single agent, handed the failure and a strict limit, told to fix
the cause - and the message that finally goes carries both halves: what broke,
and whether it is fixed. The dispatch is detached, so the routine's own run
script exits and drops its lock while the repair works.

THE HOUR IS THE PROMISE. The repair turn is stopped at its limit and the message
goes immediately after, so the owner hears inside the hour whether the agent
finished or was cut off. A repair that dies without reporting - a reboot, a kill,
a runner that could not start - would otherwise be silence, and silence on this
machine reads exactly like a clean night. So the dispatch writes a pending record
carrying a deadline, and --sweep sends for any record whose deadline has passed.
Nothing dispatched is ever left unanswered.

REPEATS. The error routine can fire every ten minutes, so one broken thing could
send six messages an hour and spend six agents on it. The same routine failing
the same way again inside SUPPRESS_HOURS is not new information: it is held, and
no repair is dispatched for it. A different reason goes out at once, and the held
state names how many were folded into the next send.

HANDED BACK. A complaint a repair turn ended not-fixed on is the other kind of
repeat, and the slower one. That turn found the thing a person has to do and
said so, and the routine behind it goes on reporting the condition every time it
fires - rightly, because the condition is real and the status line is how it
reaches him. What that morning does not get is another agent to reach the same
conclusion: the message goes as it always does, naming the repair that already
tried and what it said, and the next turn on it waits REPAIR_RETRY_HOURS. Only a
turn that reached its own verdict holds anything off - one stopped at its limit
diagnosed nothing - and a routine reporting ok once clears the record.

DELIVERY. The recipient is the owner's own address from the profile
(user.email) and the sender refuses every other address, the same arrangement
the morning report keeps and for the same reason: no email reaches anybody but
the owner unless they say so for that send, and a routine that took its
recipient from its own config file is one bad edit away from breaking that. The
key is never written down - the run script hands it in, under the credential
name the profile gives (notify.mail_credential):

    ~/.claude/tools/with-credential.py <notify.mail_credential>=RESEND_API_KEY \
      -- python3 ~/bin/routine-deliver.py <id> <exit> <log>

MODES.

    routine-deliver.py <routine> <exit> <log>   what a run script calls
    routine-deliver.py --report <pending> <exit>   what the repair runner calls
    routine-deliver.py --sweep [logs]           send for a repair that went quiet
    routine-deliver.py --overdue [logs]         name those, exit 0 if any (no key)
"""
import html
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path


def _profile():
    """The owner's profile reader (mtoa_config.py): beside the services in the
    checkout, in ~/lib where the host runs them, or in the installed setup."""
    import importlib.util
    here = Path(__file__).resolve().parent
    for path in (here.parent / "services" / "lib" / "mtoa_config.py",
                 here.parent / "lib" / "mtoa_config.py",
                 Path.home() / "lib" / "mtoa_config.py",
                 Path.home() / ".claude" / "tools" / "mtoa_config.py"):
        if path.is_file():
            spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def setting(key, default=None):
    """One value from the owner's profile, or the default where none is set."""
    module = _profile()
    return module.get(key, default) if module else default


# The only address this sends to, ever. See DELIVERY above.
RECIPIENT = setting("user.email", "")
FROM = setting("notify.mail_from", "")
RESEND = "https://api.resend.com/emails"

# Resend sits behind Cloudflare, which refuses Python's default urllib signature
# with a 403 carrying "error code: 1010" and no mention of the key. A request
# that names itself gets through. The morning report learned this the hard way.
USER_AGENT = "sunday-routine-deliver/1.0"

# How long the same routine failing the same way stays quiet after its first
# send. Long enough that a tight loop cannot fill an inbox, short enough that
# something still broken tomorrow says so again.
SUPPRESS_HOURS = 6

# Enough of the log to see what happened without pasting an hour of transcript.
TAIL = 4000

# The repair turn, and the promise made when one is dispatched. The runner stops
# the agent at REPAIR_LIMIT and sends immediately after; REPAIR_DEADLINE is the
# outer wall the sweep enforces on the runner itself, so the gap between them is
# what a dying runner is allowed to cost. Both are in seconds.
REPAIR_RUNNER = Path.home() / "bin" / "routine-repair.sh"
REPAIR_LIMIT = 3000
REPAIR_DEADLINE = 3600

# How long a complaint a repair turn already handed back stays the owner's
# rather than the machine's. A turn that ended not-fixed found the thing a
# person has to do and said it; the routine behind it goes on reporting the same
# condition every morning, which is right, because the condition is real. Giving
# that morning another agent is not: it spends an hour rewriting a sentence he
# is already holding. So the complaint is delivered every time and repaired no
# more until this has passed, by which point the world has had long enough to
# change that one more look is worth the hour.
REPAIR_RETRY_HOURS = 168
LOGS = Path.home() / "routine-logs"

STATUS_LINE = re.compile(r"^ROUTINE-STATUS:\s*(ok|attention)\b", re.I | re.M)
REPAIR_LINE = re.compile(r"^REPAIR-STATUS:\s*(fixed|not-fixed)\b", re.I | re.M)
REPAIR_HEADS = re.compile(
    r"^(WHAT BROKE|WHAT I DID|WHAT IS TRUE NOW|REPAIR-STATUS):", re.I | re.M)


def continued(text, start):
    """A status line, and the lines its sentence wrapped onto.

    An agent writes at about eighty columns and its last line is a whole
    sentence, so that sentence reaches the log split across two or three
    physical lines. Reading only the first of them is how "The routine can no
    longer end its turn with its measurement" became an entire verdict, in an
    email whose missing half - "running in the background" - was the part that
    said what had actually been fixed. So the sentence is read to the blank
    line that ends it, and put back together as one.
    """
    out = []
    for line in text[start:].splitlines():
        line = line.strip()
        if not line or FENCE.match(line) or REPAIR_HEADS.match(line):
            break
        out.append(line)
    return " ".join(out)


def verdict(routine, status, text):
    """What this run was, and the one line that says so.

    Order matters. A turn that exited badly is an attention case whatever it
    printed on the way down, because the thing that decides is the exit rather
    than the prose above it.
    """
    if status == 124:
        # timeout(1)'s own status. Naming it beats "exit 124" in a subject line.
        return "attention", "the turn hit its time limit and was stopped"
    if status != 0:
        return "attention", "the turn exited %s" % status

    found = list(STATUS_LINE.finditer(text))
    if not found:
        return "attention", ("the run printed no status line, so it did not "
                             "reach the end of its own summary")
    last = found[-1]
    if last.group(1).lower() == "ok":
        return "ok", ""
    return "attention", (continued(text, last.end())
                         or "the run said a step did not complete")


def record(state_path, reason):
    """The standing complaint this run repeats, or None when it is a new one.

    Only the same reason counts as the same complaint. A routine that starts
    failing a different way has something new to say and says it, whatever is
    being held for the old failure.
    """
    try:
        last = json.loads(state_path.read_text())
    except (OSError, ValueError):
        return None
    if last.get("reason") != reason:
        return None
    return {"reason": reason, "folded": int(last.get("folded", 0)),
            "at": float(last.get("at", 0))}


# The words a reason is built out of rather than about. A routine writes its
# status line fresh every morning, so the scaffolding moves far more than the
# finding does: one dropped Buffer channel arrived as "Reconnect the Google
# Business Profile channel in the Buffer web app (ticket #820)" on one morning
# and "The Google Business Profile channel is disconnected in Buffer and must be
# reconnected in the Buffer web app so its queued posts can publish" on the
# next. Dropping these leaves the nouns that say which complaint it is.
FILLER = frozenset("""
    a an and are as at be because been before but by can cannot could did do
    does every for from had has have how in into is it its of on once only or
    other over same so still than that the their them then there these this to
    under until up was were what when where whether which while who why will
    with without would yet you your about after again against all also any both
    each few more most no nor not now some such one two
""".split())


def flattened(text):
    """A reason with everything a model varies between mornings taken out."""
    words = "".join(
        c if c.isalnum() or c.isspace() else " " for c in text).lower().split()
    # A ticket number, a count of failed posts and a date all move between
    # mornings while the thing underneath them stands still, so a bare number
    # carries no weight here and neither does a two-letter word.
    return {word for word in words
            if len(word) > 2 and not word.isdigit() and word not in FILLER}


# How much of the shorter reason has to turn up in the longer one before the two
# are the same complaint, and how many words have to be shared at all, so two
# four-word findings cannot match on one of them.
#
# Measured against the shorter rather than against their total, because the
# wordings one routine produces differ in length as much as in choice - ten
# words one morning and twenty-eight the next - and a ratio over the total reads
# that difference as disagreement when it is only a longer sentence about the
# same thing. Every repeat in this machine's own delivery history scores 0.54 or
# better by this measure, and no two findings that were genuinely different
# reach 0.35, so the figure sits in the gap with room on both sides.
SAME_COMPLAINT = 0.5
SAME_COMPLAINT_WORDS = 4


def alike(one, other):
    """Whether two reasons are the same complaint written out twice.

    A routine writes its own status line, so the same finding on two mornings
    arrives in two wordings - a ticket number appears, a channel is named in
    full one day and in short the next, a sentence explains itself at length and
    then not at all. Exact text is the right key for the hold, which has to
    swallow a tight loop of identical failures. It is the wrong one for asking
    whether a repair has already seen this, where a comma costs an hour of
    agent - and so is a sequence ratio, which reads a rewording of one finding
    as a new finding and spent three agents on one disconnected Buffer channel
    on three consecutive mornings. What survives a rewrite is which words are in
    it, so that is what is compared.
    """
    first, second = flattened(one), flattened(other)
    if not first or not second:
        return False
    if first == second:
        return True
    shared = first & second
    if len(shared) < SAME_COMPLAINT_WORDS:
        return False
    return len(shared) / min(len(first), len(second)) >= SAME_COMPLAINT


def handed_back(path, reason):
    """The repair that already gave this complaint to the owner, if it stands.

    Written only by a turn that reached its own verdict and said not-fixed. A
    turn stopped at its limit or dead on an exit never got to the diagnosis, so
    it blocks nothing: the next failure of it gets a fresh agent.
    """
    try:
        last = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - float(last.get("at", 0)) >= REPAIR_RETRY_HOURS * 3600:
        return None
    if not alike(last.get("reason", ""), reason):
        return None
    return last


# The width the plain-text copy is wrapped to. Narrow enough that a phone and a
# terminal both hold a line without breaking it a second time.
WIDTH = 78

NUMBERS = ("zero one two three four five six seven eight nine ten eleven "
           "twelve").split()
BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")
# A first word that is a name rather than a word: a domain, a path, a quoted
# command. Those keep the case they were written in.
IDENTIFIER = re.compile(r"^[`'\"]|^\S*[./_@]|^[A-Z]")
FENCE = re.compile(r"^\s*```")


def spelled(count):
    """Small numbers the way a person writes them inside a sentence."""
    return NUMBERS[count] if 0 <= count < len(NUMBERS) else str(count)


def sentence(text):
    """A quoted fragment, written out as its own sentence.

    The reasons this file carries are lowercase clauses - "the turn exited 3",
    "the run printed no status line" - because every one of them was written to
    sit after a colon. Standing on their own they need a capital and a full
    stop, and giving them one is most of what separates a typed note from a
    line of log.
    """
    text = " ".join(text.split())
    if not text:
        return ""
    if not IDENTIFIER.match(text):
        text = text[0].upper() + text[1:]
    return text if text[-1] in ".?!" else text + "."


def shorten(text, limit):
    """The front of a sentence, cut on a word rather than through one."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit - 3].rsplit(" ", 1)[0].rstrip(",;:.") + "..."


def paragraphs(body):
    """A block as paragraphs, rather than as the lines the agent typed.

    A repair turn writes its report wrapped at about eighty columns, and the
    old message kept every one of those newlines with white-space:pre-wrap. In
    any window narrower than the wrap - which is every phone - each line broke
    a second time, and four short paragraphs arrived looking like a wall of
    ragged log output. So the hard wraps come out and the blank lines stay: a
    paragraph is what the writer separated with a blank line, and the reader's
    own window decides where the lines end. A bulleted line keeps its break,
    because there the newline is the meaning rather than the wrapping.
    """
    out = []
    for chunk in re.split(r"\n\s*\n", body.strip()):
        lines, current = [], []
        for line in chunk.splitlines():
            line = line.strip()
            if not line or FENCE.match(line):
                continue
            if current and BULLET.match(line):
                lines.append(" ".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            lines.append(" ".join(current))
        if lines:
            out.append(lines)
    return out


def wrap(text):
    """One paragraph at the plain-text width.

    Neither hyphens nor long words are split. textwrap breaks on both by
    default, which is how a log filename and a repository name each arrived cut
    in half down the middle of a word.
    """
    return textwrap.fill(text, WIDTH, break_long_words=False,
                         break_on_hyphens=False)


def as_text(routine, when, stance, headline, facts, blocks, tail_title, tail):
    """The message for a client that shows no HTML.

    Written out rather than stripped down. The wrapping happens here, so the
    plain copy reads as a typed note instead of as the HTML one with its tags
    pulled off - which is what it was, back when the facts arrived carrying
    "<br>" and the plain-text reader got them verbatim.
    """
    parts = ["%s\n%s" % (routine, when), wrap("%s %s" % (stance, headline))]
    parts.extend(wrap(fact) for fact in facts)
    for title, body in blocks:
        parts.append("")
        parts.append(title)
        for lines in paragraphs(body):
            parts.append("\n".join(wrap(line) for line in lines))
    if tail:
        parts.extend(["", tail_title, tail])
    return "\n\n".join(parts) + "\n"


def as_html(routine, when, stance, headline, facts, blocks, tail_title, tail):
    """The same message, set to be read.

    One column at a readable measure, a rule between sections, and nothing in
    it that is not a heading, a paragraph or a log. Every string arrives plain
    and is escaped here, so nothing in this half can leak into the other.
    """
    esc = html.escape
    out = [
        '<!doctype html><html><head><meta charset="utf-8">',
        # Without this a phone lays the message out at desktop width and then
        # shows the reader the left seven-tenths of it.
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        '<meta name="supported-color-schemes" content="light dark">',
        "<style>@media (prefers-color-scheme:dark){"
        ".page{background:#121212!important}"
        ".ink{color:#e9e9e9!important}"
        ".muted{color:#9b9b9b!important}"
        ".rule{border-color:#333!important}"
        ".log{background:#1b1b1b!important;border-color:#333!important;"
        "color:#c9c9c9!important}}</style></head>",
        '<body class="page" style="margin:0;padding:28px 18px;'
        'background:#ffffff">',
        '<div class="ink" style="max-width:620px;margin:0 auto;'
        "font:15px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',"
        'Helvetica,Arial,sans-serif;color:#1c1c1c">',
        '<p style="margin:0 0 3px"><strong>%s</strong></p>' % esc(routine),
        '<p class="muted" style="margin:0 0 26px;color:#6b6b6b;font-size:13px">'
        "%s</p>" % esc(when),
        '<p style="margin:0 0 18px;font-size:17px;line-height:1.5">'
        "<strong>%s</strong> %s</p>" % (esc(stance), esc(headline)),
    ]
    for fact in facts:
        out.append('<p class="muted" style="margin:0 0 10px;color:#6b6b6b">'
                   "%s</p>" % esc(fact))
    for title, body in blocks:
        out.append('<div class="rule" style="border-top:1px solid #e5e5e5;'
                   'margin:28px 0 0;padding:22px 0 0">')
        out.append('<p style="margin:0 0 10px;font-size:13px;font-weight:600">'
                   "%s</p>" % esc(title))
        for lines in paragraphs(body):
            out.append('<p style="margin:0 0 12px">%s</p>'
                       % "<br>".join(esc(line) for line in lines))
        out.append("</div>")
    if tail:
        out.append('<div class="rule" style="border-top:1px solid #e5e5e5;'
                   'margin:28px 0 0;padding:22px 0 0">')
        out.append('<p class="muted" style="margin:0 0 10px;font-size:13px;'
                   'color:#6b6b6b">%s</p>' % esc(tail_title))
        out.append('<pre class="log" style="margin:0;background:#f7f7f7;'
                   "border:1px solid #e8e8e8;border-radius:6px;padding:14px;"
                   "white-space:pre-wrap;word-break:break-word;color:#333;"
                   'font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace">'
                   "%s</pre>" % esc(tail))
        out.append("</div>")
    out.append("</div></body></html>")
    return "".join(out)


def send(key, subject, routine, stance, headline, facts, blocks,
         tail_title=None, tail=None):
    """One message: where it stands, the facts under it, and a log if it helps.

    Every message this file sends has the same shape, whether it is a failure
    nobody has touched yet or the account of a repair, because the owner reads
    them in the same inbox and should not have to learn two layouts. The stance
    is the first thing on the page and the first thing in the subject, the
    sentence behind it says why, and the sections underneath are the agent's
    own report set as prose.
    """
    when = time.strftime("%A %-d %B, %-I:%M %p", time.localtime())
    fields = (routine, when, stance, headline, facts, blocks, tail_title, tail)

    body = json.dumps({
        "from": FROM, "to": [RECIPIENT], "subject": shorten(subject, 78),
        "text": as_text(*fields), "html": as_html(*fields),
    }).encode()

    request = urllib.request.Request(
        RESEND, data=body, method="POST",
        headers={"Authorization": "Bearer %s" % key,
                 "Content-Type": "application/json",
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode()).get("id")


def key_or_die():
    if not RECIPIENT or not FROM:
        sys.exit("The profile names no user.email or notify.mail_from, so there is "
                 "nobody this may send to.")
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        sys.exit("RESEND_API_KEY is not set. Pass it in with with-credential.py.")
    return key


def deliver(subject, routine, stance, headline, facts, blocks,
            tail_title=None, tail=None):
    """Send, or fail loudly. The caller decides what to say; this posts it."""
    try:
        return send(key_or_die(), subject, routine, stance, headline, facts,
                    blocks, tail_title, tail)
    except urllib.error.HTTPError as err:
        # The body is where the reason is. A bare status once cost an hour on a
        # Cloudflare refusal that read like a rejected key.
        sys.exit("not sent: HTTP %s: %s"
                 % (err.code, err.read().decode(errors="ignore")[:300]))
    except (urllib.error.URLError, OSError, ValueError) as err:
        sys.exit("not sent: %s" % err)


def pending_live(state_dir):
    """A repair already working, if there is one.

    One agent at a time on this machine. Two repair turns editing the same
    routine's files at once is a merge conflict with nobody to resolve it, and
    the second one would be starting from a picture the first is already
    changing. A second failure arriving while one runs is delivered as it always
    was: a message with the reason and the log, and no repair behind it.
    """
    now = time.time()
    for path in sorted(state_dir.glob("*.repair.json")):
        try:
            open_repair = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if float(open_repair.get("deadline", 0)) > now:
            return open_repair
    return None


def dispatch(routine, reason, status, log):
    """Start one repair turn for this failure. Returns its pending record.

    Detached on purpose. The routine's run script is holding a lock that keeps
    the next tick from starting on top of it, and a repair that ran inside that
    script would hold the lock for another hour while it worked.

    The send key is stripped from what the child inherits. This process has it
    in its environment - the run script handed it in - and the turn below runs
    unattended with permissions skipped, where a key it can read is a key it can
    send with. The runner asks the vault for its own copy when it has something
    to report, after the turn is over.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    state_dir = LOGS if not log.parent.name else log.parent
    open_repair = {
        "routine": routine, "reason": reason, "exit": status,
        "log": log.name, "repair_log": "%s-repair-%s.log" % (routine, stamp),
        "dispatched": time.time(), "deadline": time.time() + REPAIR_DEADLINE,
        "limit": REPAIR_LIMIT,
    }
    path = state_dir / ("%s.repair.json" % routine)
    path.write_text(json.dumps(open_repair))

    env = {k: v for k, v in os.environ.items() if k != "RESEND_API_KEY"}
    runner_log = (state_dir / "repair-runner.log").open("a")
    try:
        subprocess.Popen(
            [str(REPAIR_RUNNER), str(path)], env=env,
            stdin=subprocess.DEVNULL, stdout=runner_log, stderr=runner_log,
            start_new_session=True, close_fds=True)
    finally:
        runner_log.close()
    return open_repair


def repair_sections(text):
    """The four things a repair turn is asked to print, as it last printed them.

    Later occurrences win. An agent that drafts a section and then revises it
    has said the second one, and the email should carry what it settled on.
    """
    found = {}
    heads = list(REPAIR_HEADS.finditer(text))
    for index, head in enumerate(heads):
        name = head.group(1).upper()
        if name == "REPAIR-STATUS":
            continue
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        body = text[head.end():end].strip()
        if body:
            found[name] = body
    return found


def unrepeated(text, sections):
    """The part of a repair log worth pasting under the report.

    A turn prints its report into the same log the runner reads, so the message
    used to set the four sections and then paste them again underneath,
    verbatim, as the last four thousand characters of that log. What the reader
    has not already seen is everything above the report, so that is what goes.
    """
    if not text:
        return None
    head = REPAIR_HEADS.search(text)
    kept = (text[:head.start()] if head and sections else text).strip()
    while kept.splitlines() and FENCE.match(kept.splitlines()[-1]):
        kept = "\n".join(kept.splitlines()[:-1]).strip()
    return kept[-TAIL:] if kept else None


def report(argv):
    """Say what the repair turn did. Called by the runner, once, at the end.

    The verdict is the runner's exit first and the agent's own line second, for
    the same reason the routine verdict works that way: a turn stopped at its
    limit has not fixed anything, whatever it was saying when the clock ran out.
    """
    if len(argv) != 2:
        sys.exit("usage: routine-deliver.py --report <pending> <exit>")
    path, status = Path(argv[0]), int(argv[1])
    try:
        open_repair = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        sys.exit("no pending repair at %s: %s" % (path, err))

    routine = open_repair.get("routine", "routine")
    reason = open_repair.get("reason", "")
    limit = int(open_repair.get("limit", REPAIR_LIMIT))
    repair_log = path.parent / open_repair.get("repair_log", "")
    text = repair_log.read_text(errors="ignore") if repair_log.exists() else ""

    found = list(REPAIR_LINE.finditer(text))
    if status == 124:
        fixed, line = False, ("the repair turn hit its %d-minute limit and was "
                              "stopped" % (limit // 60))
    elif status != 0:
        fixed, line = False, "the repair turn exited %s" % status
    elif not found:
        fixed, line = False, ("the repair turn ended without saying whether it "
                              "fixed anything")
    else:
        last = found[-1]
        fixed = last.group(1).lower() == "fixed"
        line = continued(text, last.end()) or (
            "the repair turn said it was fixed" if fixed
            else "the repair turn could not fix it")

    minutes = max(1, round((time.time() - float(open_repair.get(
        "dispatched", time.time()))) / 60))
    sections = repair_sections(text)
    blocks = [("What broke", sections.get("WHAT BROKE") or reason)]
    if sections.get("WHAT I DID"):
        blocks.append(("What the agent did", sections["WHAT I DID"]))
    if sections.get("WHAT IS TRUE NOW"):
        blocks.append(("What is true now", sections["WHAT IS TRUE NOW"]))

    stance = "Fixed." if fixed else "Not fixed."
    headline = sentence(line) + ("" if fixed else " This one is yours.")
    # The exit status earns a mention only when it was clean, because that is
    # the surprising half: the turn finished, said nothing, and was a failure
    # anyway. Any other status is already the whole of the reason above it.
    ran = ("the repair that picked it up ran for %s minute%s"
           % (spelled(minutes), "" if minutes == 1 else "s"))
    facts = [
        "%s %s" % (sentence(reason),
                   "The turn exited 0, and %s." % ran
                   if open_repair.get("exit") == 0 else sentence(ran)),
        "Logs: %s for the run, %s for the repair."
        % (open_repair.get("log", ""), open_repair.get("repair_log", "")),
    ]

    sent = deliver(
        subject="%s is %s: %s" % (routine, "fixed" if fixed else "not fixed",
                                  sentence(line)),
        routine=routine,
        stance=stance,
        headline=headline,
        facts=facts,
        blocks=blocks,
        tail_title="What else the repair turn printed",
        tail=unrepeated(text, sections))

    # A complaint that is fixed is news the next time it happens, so the hold
    # goes with it. One that is not fixed keeps its hold: the owner has just
    # been told, and the routine failing the same way in ten minutes is the same
    # thing he is already looking at.
    tried = path.parent / ("%s.tried.json" % routine)
    if fixed:
        (path.parent / ("%s.deliver.json" % routine)).unlink(missing_ok=True)
        tried.unlink(missing_ok=True)
    elif found and status == 0:
        # The turn reached a verdict of its own and the verdict was that this
        # one is the owner's. Only that case holds the next dispatch off.
        tried.write_text(json.dumps(
            {"routine": routine, "reason": reason, "line": line,
             "repair_log": open_repair.get("repair_log", ""), "at": time.time()}))
    path.unlink(missing_ok=True)
    print("delivered %s to %s: %s: %s"
          % (sent, RECIPIENT, "fixed" if fixed else "not fixed", line))
    return 0


def overdue(state_dir):
    """Every dispatched repair whose deadline has passed without a word."""
    now = time.time()
    late = []
    for path in sorted(state_dir.glob("*.repair.json")):
        try:
            open_repair = json.loads(path.read_text())
        except (OSError, ValueError):
            # An unreadable record is a repair nobody can account for, which is
            # the case this whole file exists to stop being silent about.
            late.append((path, {}))
            continue
        if float(open_repair.get("deadline", 0)) <= now:
            late.append((path, open_repair))
    return late


def sweep(state_dir):
    """Send for a repair that never reported, and clear it.

    The runner sends on every path it can reach, including its own failures, so
    reaching this means the runner itself is gone: the machine rebooted, the
    process was killed, or it never started. The owner still hears, inside the
    hour, that something broke and that nothing came back about it.
    """
    for path, open_repair in overdue(state_dir):
        routine = open_repair.get("routine") or path.name.split(".")[0]
        reason = open_repair.get("reason", "an attention case with no record left")
        repair_log = path.parent / open_repair.get("repair_log", "")
        text = repair_log.read_text(errors="ignore") if repair_log.exists() else ""
        sent = deliver(
            subject="%s is not fixed: the repair agent never reported back"
                    % routine,
            routine=routine,
            stance="Not fixed.",
            headline="A repair turn was dispatched for this and never reported "
                     "back, so nobody knows what it did. This one is yours.",
            facts=[
                sentence(reason) + (" The turn exited 0."
                                    if open_repair.get("exit") == 0 else ""),
                "The repair was dispatched at %s and is past its deadline."
                % time.strftime("%-I:%M %p", time.localtime(
                    float(open_repair.get("dispatched", time.time())))),
                "Log: %s." % open_repair.get("log", ""),
            ],
            blocks=[("What broke", sentence(reason)),
                    ("What the agent did",
                     "Unknown. The turn was dispatched and the runner never "
                     "came back, so this was sent by the sweep rather than by "
                     "the repair itself.")],
            tail_title="What the repair turn printed before it went quiet",
            tail=unrepeated(text, repair_sections(text)))
        path.unlink(missing_ok=True)
        print("delivered %s to %s: the repair for %s never reported back"
              % (sent, RECIPIENT, routine))
    return 0


def run(routine, status, log):
    """The path a routine's run script takes, once, at the end of a turn."""
    text = log.read_text(errors="ignore") if log.exists() else ""
    kind, reason = verdict(routine, status, text)
    state_dir = log.parent if log.parent.name else LOGS
    state = state_dir / ("%s.deliver.json" % routine)

    if kind == "ok":
        # Nothing to say, and saying it anyway is how a channel stops being
        # read. The status file the runner writes is the record; the morning
        # report reads it. Clear the hold so the next real failure is not
        # mistaken for a repeat of an older one.
        state.unlink(missing_ok=True)
        (state_dir / ("%s.tried.json" % routine)).unlink(missing_ok=True)
        print("quiet: the run reported itself clean")
        return 0

    standing = record(state, reason)
    if standing is not None and time.time() - standing["at"] < SUPPRESS_HOURS * 3600:
        # The hold keeps its original timestamp. Refreshing it on every repeat
        # would let a routine failing every ten minutes stay silent forever. It
        # also holds the repair: an agent already tried this one and said what
        # it could, and spending another every ten minutes on the same failure
        # is how six agents work one broken thing.
        standing["folded"] += 1
        state.write_text(json.dumps(standing))
        print("held: same complaint inside %sh (%s so far)"
              % (SUPPRESS_HOURS, standing["folded"] + 1))
        return 0

    # A hold that has run out still knows how many it swallowed, and the message
    # that breaks the silence is the right place to say so.
    folded = standing["folded"] if standing else 0

    working = pending_live(state_dir)
    tried = handed_back(state_dir / ("%s.tried.json" % routine), reason)
    if (os.environ.get("ROUTINE_REPAIR_DISPATCH", "on") != "off"
            and os.access(REPAIR_RUNNER, os.X_OK)
            and working is None and tried is None):
        # The hold goes down before the turn starts rather than after it
        # reports, because the turn takes an hour and the tick behind it fires
        # every ten minutes. Six of them would dispatch six agents at the same
        # failure before the first had anything to say.
        state.write_text(json.dumps({"reason": reason, "folded": 0,
                                     "at": time.time()}))
        open_repair = dispatch(routine, reason, status, log)
        print("dispatched: a repair turn for %s, reporting by %s"
              % (reason, time.strftime("%-I:%M %p", time.localtime(
                  open_repair["deadline"]))))
        return 0

    # No repair behind this one: none is installed, one is already working, or
    # the dispatch is switched off. The message goes as it always did.
    if working:
        why = "a repair is already working on %s" % working["routine"]
    elif tried:
        why = ("the repair that ran on %s could not fix it and said it needs "
               "a person" % time.strftime("%-d %B", time.localtime(
                   float(tried.get("at", time.time())))))
    else:
        why = "no repair runner is installed"
    facts = ["%sNothing was dispatched for it: %s."
             % ("The turn exited 0. " if status == 0 else "", why)]
    if folded:
        facts.append(sentence("%s more like it %s held in the last %s hours"
                              % (spelled(folded),
                                 "was" if folded == 1 else "were",
                                 SUPPRESS_HOURS)))
    if tried:
        facts.append(sentence(
            "That repair said %s. Its log is %s, and the next turn on this one "
            "fires if it is still being reported in %s days"
            % (shorten(tried.get("line", "it could not be fixed"), 160).rstrip("."),
               tried.get("repair_log", ""), spelled(REPAIR_RETRY_HOURS // 24))))
    facts.append("Log: %s." % log.name)

    sent = deliver(
        subject="%s needs a look: %s" % (routine, sentence(reason)),
        routine=routine,
        stance="Needs a look.",
        headline=sentence(reason),
        facts=facts,
        blocks=[],
        tail_title="The tail of the log",
        tail=text[-TAIL:].strip() if text.strip() else None)
    # Written only once the message is away. A send that failed has told nobody
    # anything, and a hold over it would swallow the retry as a repeat.
    state.write_text(json.dumps({"reason": reason, "folded": 0, "at": time.time()}))
    print("delivered %s to %s: %s" % (sent, RECIPIENT, reason))
    return 0


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--report"]:
        return report(argv[1:])
    if argv[:1] == ["--sweep"]:
        return sweep(Path(argv[1]) if len(argv) > 1 else LOGS)
    if argv[:1] == ["--overdue"]:
        late = overdue(Path(argv[1]) if len(argv) > 1 else LOGS)
        for path, open_repair in late:
            print(open_repair.get("routine") or path.name)
        return 0 if late else 1
    if len(argv) != 3:
        sys.exit("usage: routine-deliver.py <routine> <exit> <log>")
    return run(argv[0], int(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    raise SystemExit(main())
