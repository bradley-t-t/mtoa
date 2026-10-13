#!/usr/bin/env python3
"""Runs the cases a tool answers to.

A tool at `tools/x.py` answers to `tools/tests/x-cases.py` by name alone. That
one convention is what lets an edit be checked at the moment it is made and lets
the sync judge a candidate copy before letting it replace anything, without
either of them holding a list of which files matter.

  cases.py all          run every set of cases here
  cases.py for <path>   run the cases that answer for one file
  cases.py hook-edit    PostToolUse: run the cases for the file just edited

A set of cases is handed the copy under test in INTELLIGENCE_TEST_TARGET and
exits non-zero when that copy fails.

`all` closes with one line naming every suite that failed, died before reaching
a verdict, or answers for a tool the tree no longer holds. That line is the
whole report for anything reading a probe's tail, `intelligence-check.py` above
all: a suite's own closing count speaks for that suite, and the suite that
happened to run last is not the run.
"""
from __future__ import annotations

import json
import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent


def _sandbox():
    """The suite-sandbox module, loaded by path.

    Every suite is started through it, so a copy of this runner executed from
    outside the installed tree still finds one: beside the tools it belongs to
    first, then the installed tree.
    """
    here = TOOLS / "suite-sandbox.py"
    path = here if here.is_file() else \
        Path.home() / ".claude" / "tools" / "suite-sandbox.py"
    spec = importlib.util.spec_from_file_location("intelligence_suite_sandbox",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SANDBOX = _sandbox()


def cases_for(path):
    """The cases answering for a file, and the copy they would be run against.

    `scripts/x.py` answers to `scripts-x-cases.py` -- namespaced, so a script
    and a tool sharing a name can never claim the same cases file.

    A subject may be a shell script as well as a Python one; the cases file is
    Python either way, because it runs the subject rather than importing it.
    The guards that run before anything else are shell scripts under `scripts/`,
    so restricting subjects to `.py` would put exactly those outside the
    mechanism.
    """
    # Absolute, because the suite is handed this path and runs it from a fixture
    # directory of its own. A relative one resolves here and nowhere the suite
    # goes, so every case in it fails at once on a file the interpreter cannot
    # open - which reads as the tool being broken rather than the path being
    # wrong, and buries whatever the suite was actually there to catch.
    target = Path(path).resolve()
    name = target.name
    if target.suffix not in (".py", ".sh"):
        return None, None
    if name.endswith("-cases.py"):
        stem = name[:-len("-cases.py")]
        if stem.startswith("scripts-"):
            base = TOOLS.parent / "scripts" / stem[len("scripts-"):]
            subject = next((c for c in (base.with_suffix(".py"),
                                        base.with_suffix(".sh")) if c.is_file()),
                           base.with_suffix(".py"))
        else:
            subject = TOOLS / (stem + ".py")
        return (HERE / name if (HERE / name).is_file() else None, subject)
    prefix = "scripts-" if target.parent.name == "scripts" else ""
    cases = HERE / (prefix + target.stem + "-cases.py")
    return (cases if cases.is_file() else None), target


def declared_subjects(cases):
    """The tools a suite names for itself, for one that answers for several.

    A suite bound by its own name alone covers one tool. A contract several tools
    share has no such name, and binding it to one of them hides it from the others.
    `ANSWERS_FOR` is that declaration, read out of the source rather than imported,
    since importing a suite runs it.
    """
    try:
        tree = ast.parse(Path(cases).read_text())
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "ANSWERS_FOR" not in names:
            continue
        try:
            return [str(v) for v in ast.literal_eval(node.value)]
        except ValueError:
            return []
    return []


# Long enough for the slowest suite in the tree, short enough that one which
# hangs cannot hold a health check open behind it.
TIMEOUT = 300


def decoded(chunk):
    if chunk is None:
        return ""
    return chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")


def why_no_verdict(code, stderr):
    """The reason a suite never reached a verdict, or None where it reached one.

    A suite reporting failures and a suite dying on an import both leave Python
    exiting 1, so the exit code alone cannot separate them. What does is how the
    run ended: on a signal, or with a traceback as the last thing on stderr.
    Python prints one there and the suites print their own verdicts to stdout,
    so a traceback a suite captured out of a subprocess it drove is not mistaken
    for its own death.
    """
    if code == 0:
        return None
    if code < 0:
        return "killed by signal %d" % -code
    err = decoded(stderr)
    if "Traceback (most recent call last)" in err[-4000:]:
        lines = [ln for ln in err.strip().splitlines() if ln.strip()]
        return lines[-1].strip()[:160] if lines else "died without saying why"
    return None


def run(cases, target):
    """A suite's exit code, everything it printed, and why it never reported.

    The third value is None whenever the suite reached a verdict of its own,
    whichever way that verdict came out.
    """
    env = dict(os.environ)
    if target and Path(target).is_file():
        env["INTELLIGENCE_TEST_TARGET"] = str(target)
    try:
        code, out, err = SANDBOX.run_sandboxed(cases, env, timeout=TIMEOUT)
    except OSError as why:
        return None, "", "could not be started: %s" % why
    if code is None:
        return None, out + err, "no verdict inside %ds" % TIMEOUT
    return code, out + err, why_no_verdict(code, err)


def listed(entries, limit=6, reasons=False):
    """Suite names for the summary line, with the reason where one adds anything."""
    names = []
    for cases, why in entries[:limit]:
        name = Path(cases).name
        stem = name[:-len("-cases.py")] if name.endswith("-cases.py") else name
        note = " ".join(str(why).split())[:80] if reasons and why else ""
        names.append("%s (%s)" % (stem, note) if note else stem)
    if len(entries) > limit:
        names.append("and %d more" % (len(entries) - limit))
    return ", ".join(names)


def summary(passed, failures, errors, unbound):
    """One line saying what the whole run came to, sound or not."""
    wrong = len(failures) + len(errors) + len(unbound)
    if not wrong:
        return "%d suite(s) pass" % passed
    parts = []
    if failures:
        parts.append("%d failed: %s" % (len(failures), listed(failures)))
    if errors:
        parts.append("%d never reported: %s"
                     % (len(errors), listed(errors, reasons=True)))
    if unbound:
        parts.append("%d unbound: %s" % (len(unbound), listed(unbound, reasons=True)))
    return "BLOCKED %d of %d suite(s) - %s" % (wrong, passed + wrong, "; ".join(parts))


PENDING = Path.home() / ".claude" / "state" / ".cases-hook-failure"


def main():
    verb = sys.argv[1] if len(sys.argv) > 1 else "all"
    if verb == "hook-edit":
        try:
            payload = json.load(sys.stdin)
        except (ValueError, OSError):
            return 0
        edited = str((payload.get("tool_input") or {}).get("file_path") or "")
        # A failure found by an earlier detached run surfaces here, on the next
        # edit, instead of never.
        overdue = None
        if PENDING.exists():
            try:
                overdue = PENDING.read_text()
                PENDING.unlink()
            except OSError:
                overdue = None
        cases, target = cases_for(edited)
        if cases:
            # Detached: a full suite can hold an edit for many seconds, and the
            # edit is already on disk either way. The run reports through the
            # pending file above rather than by stalling this call.
            subprocess.Popen([sys.executable, __file__, "check-edited",
                              str(cases), str(target)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if overdue:
            sys.stderr.write(overdue)
            return 2
        return 0

    if verb == "check-edited":
        code, out, why = run(Path(sys.argv[2]), sys.argv[3])
        if code or why:
            PENDING.parent.mkdir(parents=True, exist_ok=True)
            PENDING.write_text(
                "BLOCKED: an earlier edit broke the cases written for its tool.\n\n"
                "%s\nBring it back into step before anything else lands.\n"
                % ("%s\n%s" % (out.rstrip(), why) if why else out.rstrip()))
        return 0

    if verb == "for":
        cases, target = cases_for(sys.argv[2])
        if not cases:
            print("no cases answer for %s" % sys.argv[2])
            return 0
        code, out, why = run(cases, target)
        if out.strip():
            print(out.rstrip())
        if why:
            print("%s never reported: %s" % (Path(cases).name, why))
        return 1 if (code or why) else 0

    passed, failures, errors, unbound = 0, [], [], []
    for cases in sorted(HERE.glob("*-cases.py")):
        _, target = cases_for(cases)
        declared = declared_subjects(cases)
        if declared:
            # A suite naming its own subjects runs against the tree rather than
            # against one file, so no target is passed.
            missing = [d for d in declared if not (TOOLS.parent / d).exists()]
            if missing:
                unbound.append((cases, "names %s, which the tree does not hold"
                                % ", ".join(missing)))
                continue
            target = None
        elif not target or not Path(target).is_file():
            # A suite bound to a tool that does not exist speaks for nothing. It is
            # named rather than skipped: a suite nobody runs reads exactly like a
            # suite that passes, which is how a failing contract survives a green run.
            unbound.append((cases, "answers for %s, which the tree does not hold"
                            % Path(target).name if target else "no tool"))
            continue
        code, out, why = run(cases, target)
        if out.strip():
            print(out.rstrip())
        if why:
            errors.append((cases, why))
        elif code:
            failures.append((cases, out))
        else:
            passed += 1
    for cases, why in errors:
        print("NO VERDICT %s: %s" % (Path(cases).name, why))
    for cases, why in unbound:
        print("UNBOUND %s: %s" % (Path(cases).name, why))
    print(summary(passed, failures, errors, unbound))
    return 1 if failures or errors or unbound else 0


if __name__ == "__main__":
    raise SystemExit(main())
