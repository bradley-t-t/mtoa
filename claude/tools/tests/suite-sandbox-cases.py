#!/usr/bin/env python3
"""What the sandbox has to get right for a set of cases to reach no live session.

The failure it exists to stop is silent from both ends. A suite that starts a
session prints nothing about it and exits on its own verdict; the session opens
on a brief inside a temporary tree that is deleted seconds later and spends a
real run on it. Neither side leaves a mark, so the cases here are about what the
sandbox refuses and what it must not get in the way of.

Two of them matter more than the rest. A suite standing in for the session
command on purpose has to keep getting its own stand-in, or the sandbox turns
into a false refusal in every suite that exercises a listing. And a reach has to
decide the exit code even when the suite goes on to report success, since a
suite that already started a session has done the damage whatever it prints.

  suite-sandbox-cases.py     run them
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "suite-sandbox.py")

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (label, got, want))


def contains(label, haystack, needle):
    if needle not in haystack:
        FAILURES.append("%s: %r missing from the output" % (label, needle))


def load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def suite(folder, body):
    """One cases file that does whatever the body says, written where it runs."""
    path = folder / "written-cases.py"
    path.write_text(body)
    return path


def main():
    mod = load()
    with tempfile.TemporaryDirectory() as room:
        tmp = Path(room)
        sandbox = mod.sandbox_path()

        # ------------------------------------------- the stand-in itself
        check("the sandbox holds a stand-in for the session command",
              (sandbox / mod.COMMAND).is_file(), True)
        check("it is executable, so a call by name reaches it",
              os.access(sandbox / mod.COMMAND, os.X_OK), True)

        log = tmp / "reached.log"
        log.write_text("")
        reaching = subprocess.run(
            [str(sandbox / mod.COMMAND), "--bg", "a brief"],
            capture_output=True, text=True,
            env=dict(os.environ, PATH="%s%s%s" % (sandbox, os.pathsep,
                                                  os.environ.get("PATH", "")),
                     **{mod.MARK: str(log)}))
        check("a reach for a live session is refused", reaching.returncode, 2)
        contains("the refusal says why the session would be pointless",
                 reaching.stderr, "already gone")
        contains("the refusal names the way forward", reaching.stderr,
                 "brief out of the sandbox")
        check("the reach is recorded with what it asked for",
              mod.reached(log), ["--bg a brief"])

        # A suite exercising the listing on purpose puts its own stand-in ahead
        # of what it inherited, and a refusal there is a false one.
        fixture = tmp / "bin"
        fixture.mkdir()
        (fixture / mod.COMMAND).write_text(
            "#!/bin/sh\necho \"the fixture answered: $*\"\n")
        (fixture / mod.COMMAND).chmod(0o755)
        quiet = tmp / "quiet.log"
        quiet.write_text("")
        deferring = subprocess.run(
            [str(sandbox / mod.COMMAND), "agents", "--json"],
            capture_output=True, text=True,
            env=dict(os.environ,
                     PATH="%s%s%s%s%s" % (sandbox, os.pathsep, fixture,
                                          os.pathsep, os.environ.get("PATH", "")),
                     **{mod.MARK: str(quiet)}))
        check("a stand-in the suite placed itself gets the call",
              deferring.returncode, 0)
        contains("and answers it", deferring.stdout, "the fixture answered")
        check("deferring records no reach", mod.reached(quiet), [])

        # ------------------------------------------- what a runner gets back
        env = mod.apply(dict(os.environ), log)
        check("the sandbox goes on the front of the PATH",
              env["PATH"].split(os.pathsep)[0], str(sandbox))
        check("the mark names the log the stand-in writes to",
              env[mod.MARK], str(log))
        check("the environment it was given is otherwise carried through",
              env.get("HOME"), os.environ.get("HOME"))

        # A suite that reaches and then reports success is the shape the exit
        # code has to override, since the session is already running by then.
        reaching_suite = suite(tmp, "\n".join([
            "import subprocess, sys",
            "subprocess.run(['%s', '--bg', 'x'], capture_output=True)" % mod.COMMAND,
            "print('0 failure(s)')",
            "sys.exit(0)",
        ]))
        code, out, err = mod.run_sandboxed(reaching_suite, dict(os.environ))
        check("a suite that reached fails however it reported", code, 2)
        contains("and the failure names the reach", err, "reached for a live session")
        contains("the suite's own output is still carried", out, "0 failure(s)")

        # The ordinary case has to come back untouched, or the sandbox is a
        # gate that gets switched off inside a day.
        passing = suite(tmp, "print('0 failure(s)')")
        code, out, err = mod.run_sandboxed(passing, dict(os.environ))
        check("a suite that reached for nothing passes", code, 0)
        contains("with its own verdict intact", out, "0 failure(s)")
        check("and nothing is added to its stderr", err, "")

        failing = suite(tmp, "import sys; print('1 failure(s)'); sys.exit(1)")
        code, out, err = mod.run_sandboxed(failing, dict(os.environ))
        check("a suite that failed on its own still fails on its own", code, 1)

        # The two streams stay apart, because the runner reads stderr alone to
        # tell a suite that died from one that captured a death.
        dying = suite(tmp, "raise SystemError('the tool is not importable')")
        code, out, err = mod.run_sandboxed(dying, dict(os.environ))
        contains("a traceback stays on stderr", err, "Traceback")
        check("and off stdout", "Traceback" in out, False)

        # ------------------------------------------- the sweep
        check("every cases file in the tree is a suite the sweep runs",
              [p.name for p in mod.suites()]
              == sorted(p.name for p in (TARGET.parent / "tests").glob("*-cases.py")),
              True)
        check("a cases file is matched to the tool it answers for",
              mod.target_for(TARGET.parent / "tests" / "bump-version-cases.py"),
              TARGET.parent / "bump-version.py")
        # The tree holds no scripts/ directory, so the namespaced branch has
        # nothing to resolve against and answers None like any other miss.
        check("a namespaced one matches nothing while the tree holds no scripts/",
              mod.target_for(TARGET.parent / "tests"
                             / "scripts-github-no-attribution-cases.py"), None)
        check("one answering for a tool the tree no longer holds matches nothing",
              mod.target_for(TARGET.parent / "tests" / "gone-cases.py"), None)

    if FAILURES:
        for line in FAILURES:
            print(line)
    print("%d failure(s)" % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
