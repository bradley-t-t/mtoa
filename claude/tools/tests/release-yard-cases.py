#!/usr/bin/env python3
"""Cases the release-yard tool has to get right.

The tool removes worktrees and branches, so the cases pin the holds before the
removals: work that exists on one disk is named and never taken, a worktree a
live session stands in is held, an ignored file the primary checkout does not
have holds its worktree, and a sweep without the live sessions refuses. Then the
removals themselves, because a sweep that holds everything reads as working and
clears nothing: a squash-merged branch is landed although `git cherry` says
otherwise, and a worktree whose only ignored files rebuild or are copies goes.

Every case runs against a throwaway origin and checkout built here, with a
stand-in `gh` on the PATH, so nothing reaches GitHub or a real repo.

  release-yard-cases.py     run them
"""
from __future__ import annotations

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
              or Path(__file__).resolve().parent.parent / "release-yard.py")

passed = failed = 0


def check(what, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"pass  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}\n        {detail}")


class Fixture:
    def __init__(self, base):
        self.base = Path(base)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text('#!/bin/sh\n'
                      'case "$FAKE_GH" in fail) exit 1;; esac\n'
                      'case "$*" in *"--base develop"*) printf "%s" "$FAKE_GH_DEVELOP";; esac\n'
                      'exit 0\n')
        gh.chmod(0o755)
        empty = self.base / "gitconfig"
        empty.write_text("[init]\n\tdefaultBranch = main\n")
        self.env = dict(os.environ,
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
                        FAKE_GH="", FAKE_GH_DEVELOP="")
        self.origin = self.base / "origin.git"
        self.seed = self.base / "seed"
        self.primary = self.base / "primary"
        self.trees = self.primary / ".claude" / "worktrees"

    def git(self, *args, cwd=None, ok=True):
        done = subprocess.run(["git", *args], cwd=cwd or self.primary, env=self.env,
                              capture_output=True, text=True)
        if ok and done.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {done.stderr}")
        return done.stdout.strip()

    def commit(self, cwd, name, body, message):
        Path(cwd, name).write_text(body)
        self.git("add", name, cwd=cwd)
        self.git("commit", "-q", "-m", message, cwd=cwd)

    def tool(self, *args, cwd=None, **env):
        done = subprocess.run([sys.executable, str(TARGET), *args], cwd=cwd or self.primary,
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def build(self):
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin), cwd=self.base)
        self.git("clone", "-q", str(self.origin), str(self.seed), cwd=self.base, ok=False)
        s = self.seed
        Path(s, ".gitignore").write_text(".env.local\n.claude/launch.json\n.claude/worktrees/\n")
        # A real repo tracks something under .claude/, so git lists an ignored
        # launch.json on its own rather than folding the directory into one line.
        Path(s, ".claude").mkdir()
        Path(s, ".claude", "settings.json").write_text("{}\n")
        self.git("add", ".gitignore", ".claude/settings.json", cwd=s)
        self.commit(s, "README.md", "one\n", "Start")
        self.git("push", "-q", "origin", "main", cwd=s)
        self.git("checkout", "-q", "-b", "develop", cwd=s)
        self.commit(s, "app.txt", "base\n", "Base")
        self.git("push", "-q", "-u", "origin", "develop", cwd=s)

        self.git("clone", "-q", str(self.origin), str(self.primary), cwd=self.base)
        p = self.primary
        self.git("checkout", "-q", "develop")
        Path(p, ".env.local").write_text("KEY=shared\n")
        self.git("branch", "old", "main")

        # Merged by squash on origin: two commits here, one there, same total.
        self.git("checkout", "-q", "-b", "squashed")
        self.commit(p, "s.txt", "a\n", "S one")
        self.commit(p, "s.txt", "a\nb\n", "S two")
        self.git("checkout", "-q", "develop")
        self.commit(s, "s.txt", "a\nb\n", "Squash of squashed")

        # Never pushed, and pushed but not merged.
        self.git("checkout", "-q", "-b", "unpushed")
        self.commit(p, "u.txt", "u\n", "Unpushed work")
        self.git("checkout", "-q", "-b", "pushed", "develop")
        self.commit(p, "p.txt", "p\n", "Pushed work")
        self.git("push", "-q", "origin", "pushed")

        # A branch already on develop whose upstream was deleted on merge.
        self.git("checkout", "-q", "-b", "gonebr", "develop")
        self.git("push", "-q", "-u", "origin", "gonebr")
        self.git("push", "-q", "origin", "--delete", "gonebr")
        self.git("checkout", "-q", "develop")

        # Worktrees, each landed by squash unless named otherwise.
        self.trees.mkdir(parents=True)
        for name, body in (("wt-landed", "l\n"), ("wt-envsame", "e\n"), ("wt-envdiff", "d\n"),
                           ("wt-launch", "n\n"), ("wt-live", "v\n")):
            path = self.trees / name
            self.git("worktree", "add", "-q", "-b", name, str(path), "develop")
            self.commit(path, f"{name}.txt", body, f"{name} work")
            self.commit(s, f"{name}.txt", body, f"Squash of {name}")
        (self.trees / "wt-envsame" / ".env.local").write_text("KEY=shared\n")
        (self.trees / "wt-envdiff" / ".env.local").write_text("KEY=only-here\n")
        (self.trees / "wt-launch" / ".claude" / "launch.json").write_text("{}\n")

        dirty = self.trees / "wt-dirty"
        self.git("worktree", "add", "-q", "-b", "dirtyb", str(dirty), "develop")
        self.commit(dirty, "dirty.txt", "x\n", "Dirty branch work")
        self.git("push", "-q", "origin", "dirtyb", cwd=dirty)
        Path(dirty, "dirty.txt").write_text("x\nchanged\n")
        old = time.time() - 48 * 3600
        os.utime(dirty / "dirty.txt", (old, old))

        self.git("worktree", "add", "-q", "--detach", str(self.trees / "wt-detached"), "develop")

        self.git("push", "-q", "origin", "develop", cwd=s)
        self.git("fetch", "-q", "origin")
        self.live = self.base / "live.txt"
        # Written unresolved on purpose: the temp root is a symlink on macOS.
        self.live.write_text(f"{self.trees / 'wt-live'}\n")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(tmp)
        fx.build()
        t = fx.tool

        code, _ = t("landed", "squashed")
        check("a branch squash-merged onto develop is landed", code == 0)
        code, _ = t("landed", "pushed")
        check("a pushed, unmerged branch is not landed", code == 1)
        code, _ = t("landed", "old")
        check("a branch at an old main tip is landed", code == 0)
        code, _ = t("reached-origin", "pushed")
        check("a pushed branch has reached origin", code == 0)
        code, _ = t("reached-origin", "unpushed")
        check("a never-pushed branch has not reached origin", code == 1)
        code, _ = t("reached-origin", "squashed")
        check("an unpushed branch whose squash is on develop has reached origin", code == 0)

        code, text = t("unpushed", "--live", str(fx.live))
        check("unpushed names the never-pushed branch", "NEVER PUSHED: unpushed, 1 commits" in text, text)
        check("unpushed names nothing else", text.count("NEVER PUSHED") == 1, text)

        code, text = t("abandoned", "--live", str(fx.live))
        check("a dirty worktree untouched for two days is abandoned",
              "abandoned: " + os.path.realpath(fx.trees / "wt-dirty") in text and "48h old" in text, text)
        check("an unlanded branch in a worktree is named as one",
              "unlanded: dirtyb, 1 commits develop does not have, in a worktree" in text, text)
        check("an unlanded branch with no worktree is abandoned",
              "abandoned: pushed, 1 commits develop does not have, no worktree" in text, text)

        code, text = t("sweep")
        check("a sweep without the live sessions refuses", code == 1 and "REFUSE: no --live" in text, text)

        code, dry = t("sweep", "--dry-run")
        check("a dry run removes nothing", (fx.trees / "wt-landed").is_dir()
              and "squashed" in fx.git("branch"), dry)
        check("a dry run says what it would remove",
              "would remove landed" in dry and "would remove squash-merged squashed" in dry, dry)

        code, text = t("sweep", "--live", str(fx.live))
        check("the sweep prints all eight swept lines", text.count("swept:") == 8, text)
        check("the sweep fast-forwards local develop", "fast-forwarded develop" in text, text)
        check("a squash-merged worktree is removed",
              not (fx.trees / "wt-landed").exists() and "removed landed" in text, text)
        check("a worktree whose ignored file the primary holds byte-for-byte is removed",
              not (fx.trees / "wt-envsame").exists(), text)
        check("a worktree holding launch.json alone is removed",
              not (fx.trees / "wt-launch").exists(), text)
        check("a worktree holding an ignored file found nowhere else is held",
              (fx.trees / "wt-envdiff").is_dir() and "it holds .env.local" in text, text)
        check("a worktree a live session stands in is held",
              (fx.trees / "wt-live").is_dir() and "is a live session's working directory" in text, text)
        check("a dirty worktree is held",
              (fx.trees / "wt-dirty").is_dir() and "the tree is dirty" in text, text)
        check("a clean detached worktree is removed", not (fx.trees / "wt-detached").exists(), text)
        check("a branch whose upstream is gone is removed", "removed local gonebr" in text, text)
        branches = fx.git("branch")
        check("a squash-merged branch with no worktree is removed", "squashed" not in branches, branches)
        check("a never-pushed branch survives the sweep", " unpushed" in branches, branches)
        check("a pushed, unmerged branch survives the sweep", " pushed" in branches, branches)
        check("left standing names the never-pushed branch",
              "unpushed" in text and "NEVER PUSHED - origin has no copy of this" in text, text)
        check("left standing gives every survivor a reason", "NO REASON" not in text, text)
        check("a branch held by its worktree's unique ignored file says so",
              "its worktree holds .env.local" in text, text)

        code, text = t("preflight", "pretrip", "--slug", "Acme/example")
        check("a pretrip preflight with nothing in flight passes", code == 0, text)
        check("a pretrip preflight says the repo is monitored", "monitored=1" in text, text)
        code, text = t("preflight", "pretrip", "--slug", "Acme/example", FAKE_GH="fail")
        check("an unreadable gh refuses rather than reading as clear",
              code == 1 and "could not ask whether a release is open into main" in text, text)
        code, text = t("waiting", "--slug", "Acme/example", FAKE_GH_DEVELOP="  #7  feat  A feature\n")
        check("waiting lists what is open against develop", "#7  feat  A feature" in text, text)
        code, text = t("preflight", "merge", "--slug", "Acme/example",
                       FAKE_GH_DEVELOP="  #7  feat  A feature\n")
        check("a merge refuses over a pull request waiting on develop",
              code == 1 and "waiting to land on develop" in text, text)

        fx.git("push", "-q", "origin", "develop:version-2026.1.1")
        code, text = t("preflight", "pretrip", "--slug", "Acme/example")
        check("a version branch on origin is a release in flight",
              code == 1 and "a release is already in flight" in text, text)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
