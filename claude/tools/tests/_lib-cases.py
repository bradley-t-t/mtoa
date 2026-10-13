#!/usr/bin/env python3
"""Cases the shared tool library has to get right.

Every tool that loads _lib.py inherits these contracts, so they are pinned
here once rather than re-proved through each tool's suite: the state root each
environment resolves, what counts as an owned repository, the ledger read and
write round trip, the landing-command walk with its `cd` and `git -C` tracking
and the wider PR-edit verb, the transcript reader a Stop hook judges a reply
from, the reduction of a reply to the sentences that explain it, the skill a
turn answers to, and the replace-and-hold protocol two tools writing the same
day plan both answer to.

  _lib-cases.py        run them
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The owners these cases name reach the tool through a profile of their own, the
# way a machine's profile would, so no case depends on the machine it runs on.
import json as _json
import tempfile as _tempfile
_PROFILE = Path(_tempfile.mkdtemp()) / "mtoa.json"
_PROFILE.write_text(_json.dumps({"github": {"owners": ["octo-user", "Acme"]}}))
os.environ["MTOA_CONFIG"] = str(_PROFILE)

TARGET = Path(os.environ.get("INTELLIGENCE_TEST_TARGET")
              or Path(__file__).resolve().parent.parent / "_lib.py")

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def load():
    spec = importlib.util.spec_from_file_location("candidate", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def state_root_cases(mod):
    with tempfile.TemporaryDirectory() as tmp:
        saved = {k: os.environ.get(k)
                 for k in ("INTELLIGENCE_STATE_DIR", "CLAUDE_CODE_REMOTE", "HOME")}
        try:
            os.environ["INTELLIGENCE_STATE_DIR"] = tmp + "/elsewhere"
            os.environ["CLAUDE_CODE_REMOTE"] = "1"
            check("state root: override outranks everything",
                  mod.state_root(), Path(tmp) / "elsewhere")
            del os.environ["INTELLIGENCE_STATE_DIR"]
            os.environ["HOME"] = tmp
            check("state root: cloud session lives off the config tree",
                  mod.state_root(), Path(tmp) / ".pass-state")
            del os.environ["CLAUDE_CODE_REMOTE"]
            check("state root: default is the config state directory",
                  mod.state_root(), Path(tmp) / ".claude" / "state")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def run_cases(mod):
    check("run: stdout on success", mod.run(["echo", "hi"]), "hi\n")
    check("run: failing command reads as None", mod.run(["false"]), None)
    check("run: missing binary reads as None",
          mod.run(["no-such-binary-anywhere"]), None)
    check("run: stdin reaches the command", mod.run(["cat"], stdin="text"), "text")


def repo_cases(mod):
    check("owned: first prefix", mod.is_owned("octo-user/x"), True)
    check("owned: second prefix", mod.is_owned("Acme/site"), True)
    check("owned: foreign repo", mod.is_owned("someone-else/x"), False)
    check("owned: no slug", mod.is_owned(None), False)

    roots = mod.vendored_roots(["third/VENDOR.md", "src/app.py"])
    check("vendored: the marker names its directory", roots, {Path("third")})
    check("vendored: a file under the marker",
          mod.is_vendored(Path("third/lib.js"), roots), True)
    check("vendored: a sibling stays in scope",
          mod.is_vendored(Path("src/app.py"), roots), False)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()

        def git(*args):
            subprocess.run(["git", *args], cwd=root, check=True,
                           capture_output=True)

        git("init", "-q")
        check("slug: no remote reads as None", mod.origin_slug(root), None)
        git("remote", "add", "origin", "git@github.com:octo-user/demo.git")
        check("slug: ssh remote", mod.origin_slug(root), "octo-user/demo")
        git("remote", "set-url", "origin",
            "https://github.com/Acme/site.git")
        check("slug: https remote", mod.origin_slug(root), "Acme/site")
        got = mod.repo_root(root)
        check("repo root: found from inside",
              got and got.resolve(), root.resolve())

        bare = Path(tmp) / "not-a-repo"
        bare.mkdir()
        check("repo root: absent outside", mod.repo_root(bare), None)


def ledger_cases(mod):
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        empty = {"files": {}}

        check("ledger: slug flattens into the file name",
              mod.state_path(state, "owner/repo"), state / "owner__repo.json")

        first = mod.load_state(state, "owner/repo", empty)
        check("ledger: missing file falls back to empty", first, {"files": {}})
        first["files"]["a.py"] = "hash"
        check("ledger: the fallback is a copy, not the template",
              mod.load_state(state, "owner/repo", empty), {"files": {}})

        mod.save_state(state, "owner/repo", {"files": {"a.py": "hash"}})
        check("ledger: round trip",
              mod.load_state(state, "owner/repo", empty),
              {"files": {"a.py": "hash"}})
        (state / "owner__repo.json").write_text("{not json")
        check("ledger: garbled file falls back to empty",
              mod.load_state(state, "owner/repo", empty), {"files": {}})


def context_cases(mod):
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        empty = {"files": {}}

        bare = Path(tmp) / "not-a-repo"
        bare.mkdir()
        check("context: stands down outside a repo",
              mod.context(state, empty, cwd=bare), None)

        def make(name, slug):
            root = Path(tmp) / name
            root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True,
                           capture_output=True)
            subprocess.run(["git", "remote", "add", "origin",
                            f"git@github.com:{slug}.git"], cwd=root,
                           check=True, capture_output=True)
            return root

        foreign = make("foreign", "someone-else/thing")
        check("context: stands down in a foreign repo",
              mod.context(state, empty, cwd=foreign), None)

        owned = make("owned", "octo-user/demo")
        got = mod.context(state, empty, cwd=owned)
        check("context: an owned repo answers", got is not None, True)
        if got:
            root, slug, loaded = got
            check("context: the slug", slug, "octo-user/demo")
            check("context: the root", root.resolve(), owned.resolve())
            check("context: the state", loaded, {"files": {}})


def landing_cases(mod):
    base = "/base"
    lc = mod.landing_cwd
    check("parser: plain commit", lc("git commit -m msg", base), Path(base))
    check("parser: git status lands nothing", lc("git status", base), None)
    check("parser: commit message quoting the verbs",
          lc('git commit -m "run git commit later"', base), Path(base))
    check("parser: quoted echo lands nothing",
          lc("echo 'git commit'", base), None)
    # Unquoted, the message tokens are indistinguishable from the command, and
    # the walk reads them as one. Refusing here costs a caller a retry;
    # missing a real commit costs an unchecked landing.
    check("parser: unquoted echo still trips",
          lc("echo git commit", base), Path(base))
    check("parser: cd rebases the landing",
          lc("cd /other && git commit", base), Path("/other"))
    check("parser: relative cd resolves against the event cwd",
          lc("cd sub && git commit", base), Path(base) / "sub")
    check("parser: git -C", lc("git -C /other commit", base), Path("/other"))
    check("parser: stacked git -C reads every flag",
          lc("git -C /other -C sub commit", base), Path("/other/sub"))
    check("parser: gh pr create", lc("gh pr create --fill", base), Path(base))
    check("parser: gh pr view lands nothing", lc("gh pr view", base), None)
    # An apostrophe in a message is ordinary text rather than a quoting error, so
    # the walk tokenizes leniently instead of giving the landing up. Reading it as
    # "no landing here" is the permissive direction the comment above rejects, and
    # reading it as "no repository here" makes a guard refuse a tree it never read.
    check("parser: an unbalanced quote still lands",
          lc('git commit -m "oops', base), Path(base))
    check("parser: an apostrophe in the message keeps the cd",
          lc('cd /other && git commit -m "Alex\'s fix"', base), Path("/other"))
    check("parser: gh pr edit is not a landing by default",
          lc("gh pr edit 7 --body text", base), None)
    check("parser: gh pr edit lands under the edit flag",
          lc("gh pr edit 7 --body text", base, edit_verbs=True), Path(base))

    check("landing: commit verb and argument tail",
          mod.landing('git commit -m "msg"', base),
          ("commit", Path(base), ["commit", "-m", "msg"]))
    check("landing: -C consumed before the tail",
          mod.landing("git -C /other commit --amend", base),
          ("commit", Path("/other"), ["commit", "--amend"]))
    check("landing: pr tail starts after the subcommand",
          mod.landing("gh pr create --title T --body B", base),
          ("pr", Path(base), ["--title", "T", "--body", "B"]))
    check("landing: pr edit tail under the edit flag",
          mod.landing("gh pr edit 7 --body B", base, edit_verbs=True),
          ("pr", Path(base), ["7", "--body", "B"]))


def transcript_cases(mod):
    rows = [
        {"type": "user", "message": {"role": "user", "content": "do it"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {}},
            {"type": "text", "text": "First."}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Second."}]}},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "transcript.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "\n\n")
        got = mod.read_transcript(path)
        check("transcript: rows read, blank lines skipped", len(got), 3)

        text, used_tool = mod.final_reply(got)
        check("reply: the turn's text blocks join in order",
              text, "First.\nSecond.")
        check("reply: tool use is seen through the whole turn", used_tool, True)

        text, used_tool = mod.final_reply(rows[:1] + rows[2:])
        check("reply: a toolless turn reads as an answer", used_tool, False)

        check("reply: a trailing user turn ends the reply",
              mod.final_reply(rows + [{"type": "user",
                                       "message": {"content": "next"}}]),
              ("", False))

        check("transcript: missing file reads as empty",
              mod.read_transcript(Path(tmp) / "gone.jsonl"), [])
        bad = Path(tmp) / "bad.jsonl"
        bad.write_text("{not json}\n")
        check("transcript: unparseable file reads as empty",
              mod.read_transcript(bad), [])


def prose_cases(mod):
    """What a guard judging a reply sees, and what it must not see.

    Both guards that read a reply read it through here, so a fence that leaks
    is a budget nobody can meet and a script's own error strings answering as
    the writer's words.
    """
    check("prose: a reply with no fence is unchanged",
          mod.prose_only("Yes. Two routines remain."), "Yes. Two routines remain.")

    fenced = "Here it is.\n\n```bash\nlog 'the Pi is not answering'\nexit 1\n```\n\nThat is all."
    got = mod.prose_only(fenced)
    check("prose: the fenced body is dropped", "not answering" in got, False)
    check("prose: the sentences around it survive",
          "Here it is." in got and "That is all." in got, True)

    check("prose: tilde fences count too",
          "inner" in mod.prose_only("a\n\n~~~\ninner\n~~~\n\nb"), False)
    check("prose: an unterminated fence swallows the rest",
          mod.prose_only("a\n\n```\ninner\nmore"), "a\n\n")
    check("prose: a longer fence is not closed by a shorter one",
          "inner" in mod.prose_only("a\n\n````\n```\ninner\n```\n````\n\nb"), False)
    check("prose: an indented fence still opens a block",
          "inner" in mod.prose_only("a\n\n  ```\n  inner\n  ```\n\nb"), False)
    check("prose: inline code is prose",
          mod.prose_only("Run `git status` now."), "Run `git status` now.")

    two = mod.prose_only("one\n\n```\nA\n```\n\ntwo\n\n```\nB\n```\n\nthree")
    check("prose: every block goes, not just the first",
          "A" not in two and "B" not in two, True)
    check("prose: the text between blocks survives",
          "two" in two and "three" in two, True)


def atomic_write_cases(mod):
    """A file is replaced whole, so no reader lands on half of one."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "day-plans" / "2026-08-28.json"
        mod.write_atomic(target, "one\n")
        check("atomic write: it makes the path it was given",
              target.read_text(), "one\n")
        mod.write_atomic(target, "two\n")
        check("atomic write: and replaces what was there",
              target.read_text(), "two\n")
        check("atomic write: leaving nothing beside it",
              sorted(x.name for x in target.parent.iterdir()),
              ["2026-08-28.json"])


PROBE = """import fcntl, sys
handle = open(sys.argv[1], 'a+')
try:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    print('free')
except OSError:
    print('held')
"""


def plan_lock_cases(mod):
    """The hold two writers of one plan take, and what it keeps out.

    A plan is loaded, changed, and written back, so the hold has to span the
    whole of that rather than the write alone. It also has to let a tool that
    already holds it run a tool that takes it, since the carrier's merge
    rewrites a page through the tool that owns the plans.
    """
    if getattr(mod, "fcntl", None) is None:
        return

    def probe(path):
        return subprocess.run([sys.executable, "-c", PROBE, str(path)],
                              capture_output=True, text=True).stdout.strip()

    saved = os.environ.pop(mod.PLAN_LOCK_HELD, None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            lock = state / mod.PLAN_LOCK
            with mod.plan_lock(state) as taken:
                check("plan lock: it is taken", taken, True)
                check("plan lock: another process is kept out",
                      probe(lock), "held")
                check("plan lock: a process inside it says so",
                      os.environ.get(mod.PLAN_LOCK_HELD), "1")
                with mod.plan_lock(state) as again:
                    check("plan lock: and passes straight back through",
                          again, False)
                check("plan lock: which leaves the hold standing",
                      probe(lock), "held")
            check("plan lock: the hold ends with the block", probe(lock), "free")
            check("plan lock: and the variable with it",
                  mod.PLAN_LOCK_HELD in os.environ, False)

            # A holder that outlived its work cannot be allowed to stop the
            # day: the wait is bounded and the write goes ahead without it.
            blocker = subprocess.Popen(
                [sys.executable, "-c",
                 "import fcntl,sys,time\n"
                 "h=open(sys.argv[1],'a+')\n"
                 "fcntl.flock(h.fileno(), fcntl.LOCK_EX)\n"
                 "print('holding', flush=True)\n"
                 "time.sleep(30)\n", str(lock)],
                stdout=subprocess.PIPE, text=True)
            try:
                blocker.stdout.readline()
                started = time.monotonic()
                with mod.plan_lock(state, wait=0.4) as taken:
                    check("plan lock: a wait that expires yields no hold",
                          taken, False)
                check("plan lock: and gives up when it said it would",
                      time.monotonic() - started < 5, True)
            finally:
                blocker.kill()
                blocker.wait()
    finally:
        if saved is not None:
            os.environ[mod.PLAN_LOCK_HELD] = saved


def answer_prose_cases(mod):
    """The sentences a reply explains itself in, with its page left out.

    Two guards weigh an answer through this one reduction, so anything that
    leaks charges a reply for its own evidence: a value in a table, a file in a
    list, or the question quoted above the answer, none of which the writer
    wrote as prose.
    """
    check("answer: plain sentences are unchanged",
          mod.answer_prose("Yes. Nothing is published yet."),
          "Yes. Nothing is published yet.")

    page = ("> does the mirror carry the row\n"
            "\n"
            "Yes, since this morning.\n"
            "\n"
            "# A heading\n"
            "| what | where |\n"
            "|---|---|\n"
            "| the row | tools/guard-hub.py |\n"
            "- one file remains\n"
            "1. then the other\n"
            "\n"
            "```bash\ncat tools/guard-hub.py\n```\n")
    got = mod.answer_prose(page)
    check("answer: the quoted question is not the answer's words",
          "does the mirror" in got, False)
    check("answer: the table is not prose", "tools/guard-hub.py" in got, False)
    check("answer: the heading is not prose", "A heading" in got, False)
    check("answer: bullets and steps are not prose",
          "one file remains" in got or "then the other" in got, False)
    check("answer: the fence is not prose", "cat tools" in got, False)
    check("answer: the sentences survive", got.strip(), "Yes, since this morning.")

    check("answer: a quote inside a sentence stays prose",
          mod.answer_prose("It reads > as a comparison."),
          "It reads > as a comparison.")


def active_skill_cases(mod):
    """Which skill a reply answers to, read off the turn it was written in.

    A skill governs its own turn and stops there, so a rule from the last one
    cannot be held over a reply nobody asked to be shaped that way.
    """
    def user(text):
        return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}

    marker = "Base directory for this skill: /Users/x/.claude/skills/short"
    command = "<command-name>/short</command-name><command-args>is it live</command-args>"

    check("skill: the body marker names the skill",
          mod.active_skill([user("is it live"), user(marker)]), "short")
    check("skill: the slash command names it too",
          mod.active_skill([user(command)]), "short")
    check("skill: a plain prompt after it ends the turn",
          mod.active_skill([user(marker), user("is it live")]), None)
    check("skill: no skill in the transcript",
          mod.active_skill([user("is it live")]), None)
    check("skill: an assistant row decides nothing",
          mod.active_skill([user(marker),
                            {"type": "assistant",
                             "message": {"content": [{"type": "text", "text": "yes"}]}}]),
          "short")

    check("skill: a row of plain text reads whole",
          mod.row_text({"message": {"content": "is it live"}}), "is it live")
    check("skill: a tool call carries its input",
          "short" in mod.row_text(
              {"message": {"content": [{"type": "tool_use", "input": {"skill": "short"}}]}}),
          True)
    check("skill: a row with nothing in it reads empty",
          mod.row_text({"message": {"content": None}}), "")


def main():
    mod = load()
    state_root_cases(mod)
    run_cases(mod)
    repo_cases(mod)
    ledger_cases(mod)
    context_cases(mod)
    landing_cases(mod)
    transcript_cases(mod)
    prose_cases(mod)
    answer_prose_cases(mod)
    active_skill_cases(mod)
    atomic_write_cases(mod)
    plan_lock_cases(mod)

    for f in FAILURES:
        print(f"FAIL {f}")
    print(f"{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
