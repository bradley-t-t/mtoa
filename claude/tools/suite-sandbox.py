#!/usr/bin/env python3
"""The sandbox a set of cases runs inside, so no suite can start a live session.

A cases file exercises the tools that drive this machine, and some of those
tools reach for the session command on the PATH: a hand-off starts an unattended
session and a close ends one. A suite that reaches either spends a real session
on a fixture, against a brief written into a temporary tree the suite deletes on
its way out, and nothing in the suite's own exit code says that happened. The
session then runs on a file that is already gone.

The sandbox is placed by whatever starts a suite rather than by the suite
itself, which is the whole point of it. A directory holding a refusing stand-in
goes on the front of the suite's PATH, so the real binary is not reachable by
name, and INTELLIGENCE_SUITE_SANDBOX names the file that stand-in records a
reach in. Both are set on the way in, so a suite is marked from its first line
rather than from whichever line it happens to set a fixture root on, and a
runner that supplies no fixture roots at all -- config-sync judging a candidate
copy against its cases -- marks one just the same.

A suite that puts a stand-in of its own on the PATH is exercising the call
deliberately, and the sandbox defers to it: a session command under the system
temporary directory belongs to a fixture and gets the call, anything else is the
machine and does not.

  suite-sandbox.py path                 the directory to put first on a suite's PATH
  suite-sandbox.py check                run every suite inside it, name what reached
  suite-sandbox.py reached <log>        what one run reached for, one line each

check exits 2 naming any suite that reached for a live session, so a cases file
that would start one cannot be added quietly.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
HOME = Path.home()

# The stand-in lives beside the tools rather than under the state root, because
# a suite redirects the state root and would otherwise be handed a sandbox
# inside the tree it is about to delete.
SANDBOX = TOOLS.parent / "cache" / "suite-sandbox"

# The name the session command answers to on the PATH, and what the stand-in is
# written as so a call by name lands on it.
COMMAND = "claude"

MARK = "INTELLIGENCE_SUITE_SANDBOX"

# Long enough for a real set of cases, short enough that one that hangs cannot
# stall the sweep. Matches what the runners already allow a suite.
TIMEOUT = 60

REFUSAL = """a set of cases may not start or end a live session. The brief a
suite writes lives in a temporary tree the suite deletes, so a session started
from one opens on a file that is already gone.
Read the brief out of the sandbox before it goes,
or hand off for real from a run against the live tree, which needs no sandbox
and has none."""

STANDIN = '''#!/usr/bin/env python3
"""Stands in for the session command while a set of cases runs.

Reached only when a suite calls it by name and has put no stand-in of its own
ahead of this directory on the PATH. A stand-in a suite placed itself lives
under the system temporary directory with the rest of its fixtures, so one found
there is the suite exercising the call on purpose and gets it.
"""

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFUSAL = %(refusal)r
MARK = %(mark)r
COMMAND = %(command)r


def deferred_to():
    """A stand-in the suite placed itself, or None where the next one is real."""
    temporary = Path(tempfile.gettempdir()).resolve()
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if not part:
            continue
        try:
            folder = Path(part).resolve()
        except OSError:
            continue
        if folder == HERE:
            continue
        candidate = folder / COMMAND
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate if temporary in candidate.parents else None
    return None


standin = deferred_to()
if standin is not None:
    os.execv(str(standin), [str(standin)] + sys.argv[1:])

log = os.environ.get(MARK)
if log:
    try:
        with open(log, "a") as handle:
            handle.write(" ".join(sys.argv[1:])[:400] + "\\n")
    except OSError:
        pass
sys.stderr.write(REFUSAL + "\\n")
sys.exit(2)
''' % {"refusal": REFUSAL, "mark": MARK, "command": COMMAND}


def sandbox_path():
    """The stand-in directory, written fresh so it cannot serve a stale refusal.

    Rewritten on every call rather than created once: a sandbox left over from
    an older build would go on refusing in that build's words and would defer by
    that build's rule.
    """
    SANDBOX.mkdir(parents=True, exist_ok=True)
    standin = SANDBOX / COMMAND
    standin.write_text(STANDIN)
    standin.chmod(0o755)
    return SANDBOX


def apply(env, log):
    """One suite's environment, with the sandbox in front of it.

    `log` is where a reach gets recorded; the caller owns the file and reads it
    back once the suite has finished. The PATH entry goes first so a call by
    name lands here, and a suite's own stand-ins still win, because a suite
    builds its PATH by putting its fixture directory ahead of what it inherited.
    """
    folder = sandbox_path()
    ahead = env.get("PATH") or os.environ.get("PATH") or ""
    return dict(env, PATH="%s%s%s" % (folder, os.pathsep, ahead),
                **{MARK: str(log)})


def reached(log):
    """What a finished suite reached the real binary for, one entry per call."""
    try:
        text = Path(log).read_text()
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def suites():
    """Every cases file in the tree, in the order a reader would list them."""
    tests = TOOLS / "tests"
    return sorted(p for p in tests.glob("*-cases.py") if p.is_file())


def run_sandboxed(cases, env, timeout=TIMEOUT):
    """A suite's exit code and its two streams, with a reach turned into a failure.

    A suite that reaches for a live session has already done the thing the
    sandbox exists to stop, whatever verdict it goes on to print, so the reach
    decides the exit code and stderr names it. The code is None where the suite
    never reached a verdict at all. The streams stay apart, because a caller
    reading stderr for a traceback is separating a suite that died from one that
    captured a death out of a subprocess it drove.

    The log is a file beside the run rather than a value in memory, since the
    stand-in that writes it is a separate process.
    """
    with tempfile.TemporaryDirectory() as room:
        log = Path(room) / "reached.log"
        log.write_text("")
        try:
            done = subprocess.run([sys.executable, str(cases)],
                                  capture_output=True, text=True,
                                  env=apply(env, log), timeout=timeout)
            code, out, err = done.returncode, done.stdout or "", done.stderr or ""
        except subprocess.TimeoutExpired as expired:
            code = None
            out, err = _text(expired.stdout), _text(expired.stderr)
        calls = reached(log)
    if calls:
        return 2, out, err + "\nreached for a live session: %s" % "; ".join(calls)
    return code, out, err


def _text(chunk):
    if chunk is None:
        return ""
    return chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")


def target_for(cases):
    """The tool a cases file answers for, or None where the tree holds none."""
    stem = cases.name[:-len("-cases.py")]
    if stem.startswith("scripts-"):
        base = TOOLS.parent / "scripts" / stem[len("scripts-"):]
        for candidate in (base.with_suffix(".py"), base.with_suffix(".sh")):
            if candidate.is_file():
                return candidate
        return None
    subject = TOOLS / (stem + ".py")
    return subject if subject.is_file() else None


def cmd_check(argv):
    del argv
    print("sandbox: %s" % sandbox_path())
    caught, ran = [], 0
    for cases in suites():
        env = dict(os.environ)
        subject = target_for(cases)
        if subject is not None:
            env["INTELLIGENCE_TEST_TARGET"] = str(subject)
        with tempfile.TemporaryDirectory() as room:
            log = Path(room) / "reached.log"
            log.write_text("")
            try:
                subprocess.run([sys.executable, str(cases)],
                               capture_output=True, text=True,
                               env=apply(env, log), timeout=TIMEOUT)
            except (OSError, subprocess.SubprocessError):
                pass
            calls = reached(log)
        ran += 1
        if calls:
            caught.append("%s reached for a live session: %s"
                          % (cases.name, "; ".join(calls)))
    if caught:
        for line in caught:
            print(line, file=sys.stderr)
        print("a suite may not start or end a session. Stand the call in on the "
              "fixture's own PATH, or read the brief out of the sandbox before "
              "the suite deletes it.", file=sys.stderr)
        return 2
    print("%d suites, none reached for a live session" % ran)
    return 0


def cmd_reached(argv):
    if not argv:
        print("name the log to read", file=sys.stderr)
        return 2
    for line in reached(argv[0]):
        print(line)
    return 0


def main():
    verb = sys.argv[1] if len(sys.argv) > 1 else ""
    rest = sys.argv[2:]
    if verb == "path":
        print(sandbox_path())
        return 0
    if verb == "check":
        return cmd_check(rest)
    if verb == "reached":
        return cmd_reached(rest)
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
