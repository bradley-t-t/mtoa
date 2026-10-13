#!/usr/bin/env python3
"""Hook dispatcher for the six checklist engines.

A checklist run is stateful: the file ledger is credited by what the session is
watched reading, the performance run needs its baseline before a source edit,
and a run left half answered has to be refused at the end rather than reported
as a full pass. None of that can live in the engines alone, because an engine
only runs when it is called; the hooks are what call it.

This reads the hook event once and calls only the engines that could act on it.
An engine with no open run is a proven exit 0, so a session doing anything else
costs one interpreter and a stat.

  pre     PreToolUse
  post    PostToolUse
  stop    Stop

Every called engine's output is forwarded. The exit is 2 when any of them
refused, which is what blocks the action.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
KINDS = ("design", "seo", "perf", "stack", "db", "google")

# The landing regexes the engines gate on, matched as supersets: `git ...
# commit` however the tokens are spelled, and `gh pr create` / `gh pr edit`.
LANDING = re.compile(
    r"git(?:\s+\S+)*?\s+[\"']?commit\b|gh\s+[\"']?pr[\"']?\s+[\"']?(?:create|edit)\b")

# How recently a run's state has to have been written for it to count as in
# flight. Every resolve, verify and probe rewrites it, so a run somebody is
# working touches this constantly and a run walked away from goes quiet.
IN_FLIGHT_SECONDS = int(os.environ.get("CHECKLIST_IN_FLIGHT_SECONDS") or 300)


def engine(kind):
    return ROOT / kind / "bin" / ("%s-pass.py" % kind)


def state_root():
    override = os.environ.get("CHECKLIST_STATE_DIR")
    if override:
        return Path(override)
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return HOME / ".pass-state"
    return HOME / ".claude" / "state"


def session_id(payload):
    return payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")


def run_pointer_exists(kind, payload, own_only=False):
    """Whether an engine could resolve an open run.

    The engines key a run on the session and the target it was opened against,
    so one session can hold a run per target and the pointer is
    <state>/<kind>-runs/current-<session><target key>.json. The target key is
    the engine's business, so this matches on the session and leaves the rest to
    the glob. The shared current.json is the fallback; with no pointer file at
    all, every hook verb is a proven exit 0.
    """
    runs = state_root() / ("%s-runs" % kind)
    sid = session_id(payload)
    if sid and any(runs.glob("current-%s*.json" % sid)):
        return True
    if not own_only and (runs / "current.json").is_file():
        return True
    if own_only and not sid and (runs / "current.json").is_file():
        return True
    return False


def run_in_flight(kind, payload):
    """Whether somebody is still working this run.

    Subagents inherit the session id of the agent that spawned them, so an open
    run being worked and an open run walked away from carry the same owner. Only
    recency separates them. Anything unreadable answers no, because an engine
    that cannot tell should still be called.
    """
    try:
        runs = state_root() / ("%s-runs" % kind)
        sid = session_id(payload)
        owned = sorted(runs.glob("current-%s*.json" % sid)) if sid else []
        pointer = owned[0] if owned else runs / "current.json"
        if not pointer.is_file():
            return False
        run_id = json.loads(pointer.read_text()).get("id")
        if not run_id:
            return False
        newest = 0.0
        for path in list(runs.glob("%s.json" % run_id)) + list(runs.glob("*-%s.json" % run_id)):
            newest = max(newest, path.stat().st_mtime)
        if not newest:
            return False
        return (time.time() - newest) < IN_FLIGHT_SECONDS
    except Exception:
        return False


def agents_working(payload):
    """Whether a subagent has taken a step lately.

    A subagent writes its transcript on every tool call, which is a truer
    liveness signal than a run's own state for a pass whose reading was fanned
    out.
    """
    try:
        sid = session_id(payload)
        if not sid:
            return False
        subagents = HOME / ".claude" / "projects"
        if not subagents.is_dir():
            return False
        cutoff = time.time() - IN_FLIGHT_SECONDS
        return any(path.stat().st_mtime > cutoff
                   for path in subagents.glob("*/subagents/*.jsonl"))
    except Exception:
        return False


def run_needs_answering(kind, payload):
    """An owned run that nobody is working."""
    if not run_pointer_exists(kind, payload, own_only=True):
        return False
    return not run_in_flight(kind, payload) and not agents_working(payload)


def edited_path(payload):
    ti = payload.get("tool_input") or {}
    return str(ti.get("file_path") or ti.get("notebook_path") or "")


def command_of(payload):
    return str((payload.get("tool_input") or {}).get("command") or "")


def skill_name(payload):
    return str((payload.get("tool_input") or {}).get("skill") or "")


def registry():
    is_bash = lambda t: t == "Bash"
    is_edit = lambda t: t in ("Write", "Edit", "NotebookEdit")
    is_post_edit = lambda t: t in ("Write", "Edit", "MultiEdit")
    is_skill = lambda t: t == "Skill"
    is_read = lambda t: t in ("Read", "Bash", "NotebookRead")

    rows = {"pre": [], "post": [], "stop": []}

    # A landing is refused while any run is open, by the engine that owns it.
    for kind in KINDS:
        rows["pre"].append(
            (is_bash, (kind, "guard-land"),
             lambda p, k=kind: LANDING.search(command_of(p))
             and run_pointer_exists(k, p)))

    # The performance run's baseline has to exist before a source edit, because
    # a before-measurement cannot be taken afterwards.
    rows["pre"].append((is_edit, ("perf", "guard-edit"),
                        lambda p: run_pointer_exists("perf", p)))

    # The design skill opens against a design read.
    rows["pre"].append((is_skill, ("design", "guard-skill"),
                        lambda p: re.fullmatch(r"(?:.*:)?design", skill_name(p))))

    # Invoking a checklist skill opens or resumes its run.
    for kind, pattern in (("design", r"(?:.*:)?(?:design-checklist|design)"),
                          ("seo", r"(?:.*:)?seo-checklist"),
                          ("perf", r"(?:.*:)?(?:perf-checklist|checklist)"),
                          ("stack", r"(?:.*:)?stack-checklist"),
                          ("db", r"(?:.*:)?db-checklist"),
                          ("google", r"(?:.*:)?google-checklist")):
        rows["post"].append(
            (is_skill, (kind, "hook-skill"),
             lambda p, x=pattern: re.fullmatch(x, skill_name(p))))

    # The file ledger: a file cannot be cleared until the harness has watched it
    # be read, which is this row.
    for kind in ("design", "seo", "perf", "stack"):
        rows["post"].append(
            (is_read, (kind, "hook-read"),
             lambda p, k=kind: run_pointer_exists(k, p)))

    for kind in ("perf", "stack"):
        rows["post"].append(
            (is_post_edit, (kind, "hook-edit"),
             lambda p, k=kind: edited_path(p)
             and run_pointer_exists(k, p, own_only=True)))

    # A session cannot end with a run still open and nobody in it.
    for kind in KINDS:
        rows["stop"].append(
            (None, (kind, "guard-stop"),
             lambda p, k=kind: run_needs_answering(k, p)))

    return rows


def dispatch(event):
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {}
    name = str(payload.get("tool_name") or "")
    blocked = False
    for tools_match, (kind, verb), when in registry().get(event, []):
        if tools_match is not None and not tools_match(name):
            continue
        path = engine(kind)
        if not path.is_file():
            continue
        try:
            due = bool(when(payload))
        except Exception:
            due = True
        if not due:
            continue
        try:
            done = subprocess.run([sys.executable, str(path), verb],
                                  input=raw.encode(), timeout=600,
                                  capture_output=True)
        except (OSError, subprocess.TimeoutExpired) as err:
            print("checklist hook: %s %s did not run (%s)" % (kind, verb, err),
                  file=sys.stderr)
            continue
        sys.stdout.buffer.write(done.stdout)
        sys.stderr.buffer.write(done.stderr)
        if done.returncode == 2:
            blocked = True
    return 2 if blocked else 0


def main():
    verb = sys.argv[1] if len(sys.argv) > 1 else ""
    if verb in ("pre", "post", "stop"):
        return dispatch(verb)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
