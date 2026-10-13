#!/usr/bin/env python3
"""Cases the release-lag tool has to get right.

The tool exists because nothing on this machine looked at stranded work, so the
cases pin the two ways a looker fails silently rather than the happy path. A
pull-request list that could not be read must not read as none - that is the
exact shape of the bug the tool was written to end, and a tool that reports
"clear" over an unreachable `gh` is worse than no tool, because it is trusted.
And the exit code carries the whole reading for a cron, so it is checked
directly rather than inferred from the report.

  release-lag-cases.py     run them
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The owners these cases name reach the tool through a profile of their own, the
# way a machine's profile would, so no case depends on the machine it runs on.
import json as _json
import tempfile as _tempfile
_PROFILE = Path(_tempfile.mkdtemp()) / "mtoa.json"
_PROFILE.write_text(_json.dumps({"github": {"owners": ["octo-user", "Acme"]}}))
os.environ["MTOA_CONFIG"] = str(_PROFILE)

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "release-lag.py")

passed = failed = 0
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def check(what, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"pass  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}\n        got  {got!r}\n        want {want!r}")


def load():
    spec = importlib.util.spec_from_file_location("release_lag", TARGET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


def fake_runner(develop_commits=(), prs=None, pr_fails=False,
                has_refs=True):
    """A git/gh stand-in driven by the arguments, not by a repo on disk."""
    def runner(args, cwd=None, timeout=60):
        if args[0] == "gh":
            if pr_fails:
                return None
            import json as _json
            return _json.dumps(prs or [])
        joined = " ".join(args)
        if "show-ref" in joined:
            return "" if has_refs else None
        if "fetch" in joined:
            return ""
        if "log" in joined:
            return "\n".join(develop_commits)
        if "remote" in joined:
            return "https://github.com/Acme/example.git"
        return ""
    return runner


def main():
    rl = load()

    check("a github url becomes an owner/name slug",
          rl.slug_of(Path("/x"), runner=lambda *a, **k:
                     "git@github.com:Acme/acme-com.git"),
          "Acme/acme-com")
    check("a repo outside the two owners is not owned",
          rl.is_owned("someoneelse/thing"), False)
    check("an owned repo is owned", rl.is_owned("Acme/thing"), True)

    # The bug this tool was written to end: an unreadable list reading as none.
    unread = rl.read_repo("Acme/example", "/x", fetch=False,
                          runner=fake_runner(pr_fails=True), now=NOW)
    check("pull requests that could not be read are not reported as read",
          unread["prs_read"], False)
    check("a repo whose pull requests could not be read is stranded",
          unread["stranded"], True)
    check("and it says so rather than saying clear",
          "could not be read" in rl.line_for(unread), True)

    clear = rl.read_repo("Acme/example", "/x", fetch=False,
                         runner=fake_runner(prs=[]), now=NOW)
    check("a repo with nothing waiting reads clear",
          rl.line_for(clear), "clear")
    check("and is not stranded", clear["stranded"], False)

    fresh = rl.read_repo("Acme/example", "/x", fetch=False,
                         runner=fake_runner(develop_commits=[ago(0.5)]),
                         now=NOW)
    check("a commit that landed minutes ago is not yet stranded",
          fresh["stranded"], False)
    check("but it is still counted and shown",
          fresh["unreleased"], 1)

    old = rl.read_repo("Acme/example", "/x", fetch=False,
                       runner=fake_runner(develop_commits=[ago(2), ago(30)]),
                       now=NOW)
    check("the oldest unreleased commit sets the age, not the newest",
          round(old["unreleased_oldest_hours"]), 30)
    check("unreleased work past the threshold is stranded",
          old["commits_stranded"], True)

    pr = rl.read_repo("Acme/example", "/x", fetch=False,
                      runner=fake_runner(prs=[
                          {"number": 412, "headRefName": "b",
                           "isDraft": False, "createdAt": ago(50)}]),
                      now=NOW)
    check("a pull request sitting open past the threshold is stranded",
          pr["prs_stranded"], True)
    check("and it is named with its age", "#412 open 2d" in rl.line_for(pr),
          True)

    draft = rl.read_repo("Acme/example", "/x", fetch=False,
                         runner=fake_runner(prs=[
                             {"number": 9, "headRefName": "b",
                              "isDraft": True, "createdAt": ago(200)}]),
                         now=NOW)
    check("a draft is somebody deciding, so it does not strand a release",
          draft["stranded"], False)

    missing = rl.read_repo("Acme/example", "/x", fetch=False,
                           runner=fake_runner(develop_commits=[ago(99)],
                                              has_refs=False),
                           now=NOW)
    check("a repo carrying no develop has no release gap to measure",
          missing["unreleased"], 0)

    buf = io.StringIO()
    code = rl.report([clear], out=buf)
    check("a clear run exits zero", code, 0)
    check("and closes with the count a routine reads back",
          "stranded: 0 of 1" in buf.getvalue(), True)

    buf = io.StringIO()
    code = rl.report([clear, old], out=buf)
    check("a run holding stranded work exits non-zero", code, 1)
    check("and marks the row rather than only counting it",
          "STRANDED" in buf.getvalue(), True)

    check("ages under an hour read in minutes", rl.describe_age(0.25), "15m")
    check("ages under two days read in hours", rl.describe_age(30), "30h")
    check("older ages read in days", rl.describe_age(72), "3d")
    check("an unparseable stamp is unknown, not zero",
          rl.hours_since("not a date"), None)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
