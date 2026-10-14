#!/usr/bin/env python3
"""The deterministic half of /pretrip and /merge: the gates, the yard reading and
the sweep, run as one tool so the skills carry decisions rather than shell.

Every reading here used to be a bash block pasted into a skill body, and three
whole classes of bug came with that and nowhere else: a skill invoked with
arguments expands `$1` in its body before the body runs, so a field reference
silently matched nothing; zsh does not word-split an unquoted expansion, so a
`for` loop ran once over a whole list; and /bin/bash on macOS is 3.2, which
cannot parse a `case` inside a continued command substitution. None of those
exist in a file.

  preflight pretrip|merge   the gates each command opens with; REFUSE exits 1
  waiting                   pull requests open against develop
  copy-files                reader-facing files the next release changes
  pi                        heartbeat, site status and open tickets
  unpushed   [--live F]     work origin has no copy of
  abandoned  [--live F]     dirty worktrees and unlanded branches nobody is in
  sweep      [--live F] [--dry-run]   the post-release sweep, eight `swept:` lines
  deploy-wait --deploy-slug S --sha SHA   wait for the deployment of one commit
  landed <ref> | reached-origin <ref>  exit 0 when true

`--live F` names a file holding one live session's working directory per line,
read from `list_sessions` (every session whose `isArchived` is false). A sweep
that removes anything refuses to run without one. `--root` names the checkout;
it defaults to the one the working directory is in. Options go before or after
the subcommand.

Nothing here ever destroys work. A worktree or branch is removed only when git
or GitHub can see its work somewhere else, `-d` is used wherever git can judge
that itself, and every hold names its reason.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

LONG_LIVED = ("develop", "main")
def _profile():
    """The owner's profile reader (mtoa_config.py), loaded by path from beside
    this tool first and from the installed tree second."""
    import importlib.util
    for path in (Path(__file__).resolve().parent / "mtoa_config.py",
                 Path.home() / ".claude" / "tools" / "mtoa_config.py"):
        if path.is_file():
            spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    class Absent:
        """No reader anywhere: every setting is unset."""
        @staticmethod
        def get(key, default=None):
            return default

        @staticmethod
        def owners():
            return ()
    return Absent


# Repos whose Vercel project sits in another repository: (has a site, the repo
# Vercel builds). FleetApp's site builds from another-user/fleetapp.
def site_elsewhere():
    """A repo whose deploys are watched under another slug (github.elsewhere)."""
    return _profile().get("github.elsewhere") or {}


def feed_url():
    """The host's status feed, or None where the profile names no host."""
    url = _profile().get("host.url")
    return url.rstrip("/") + "/status.json" if url else None


FEED_STALE_MINUTES = 15
# Ignored paths that rebuild from a checkout and an install, so never hold a
# worktree. launch.json is rewritten by the Browser pane whenever it is missing.
REBUILDS = re.compile(r"^(node_modules|dist|dist-ssr|build|coverage|\.next|\.turbo|\.vercel"
                      r"|\.vite|\.parcel-cache|\.claude/launch\.json)/?$")
CACHES = re.compile(r"(^|/)(__pycache__|\.pytest_cache|\.ruff_cache|\.mypy_cache)/?$")
TEXT_MIME = ("inode/x-empty", "application/json", "application/xml", "application/javascript")
COPY_SKIP = re.compile(r"^(node_modules|dist|build|coverage)/")
COPY_KEEP = re.compile(r"\.(astro|svelte|vue|[jt]sx?|html|mdx?|json|ya?ml|css)$", re.I)
PATCH_SCAN = 500


class Refuse(Exception):
    pass


def run(args, cwd=None, data=None):
    """(exit code, stdout). Text in and out unless `data` is bytes."""
    try:
        done = subprocess.run(args, cwd=cwd, input=data, capture_output=True,
                              text=not isinstance(data, bytes), timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return 127, "" if not isinstance(data, bytes) else b""
    return done.returncode, done.stdout


def out(args, cwd=None):
    """stdout stripped, or None where the command failed."""
    code, text = run(args, cwd)
    return text.strip() if code == 0 else None


def raw(args, cwd=None):
    """stdout untouched, or None where the command failed. For output whose
    lines carry meaning in their leading columns, which `out` would strip."""
    code, text = run(args, cwd)
    return text if code == 0 else None


def lines(text):
    return [l for l in (text or "").splitlines() if l.strip()]


class Yard:
    def __init__(self, root=None, slug=None, live=None):
        start = root or os.getcwd()
        top = out(["git", "rev-parse", "--show-toplevel"], start)
        if not top:
            raise Refuse("not inside a git checkout")
        self.root = top
        url = out(["git", "remote", "get-url", "origin"], top) or ""
        m = re.search(r"github\.com[:/](.+?)(\.git)?$", url)
        self.slug = slug or (m.group(1) if m else None)
        porcelain = out(["git", "worktree", "list", "--porcelain"], top) or ""
        first = next((l[9:] for l in porcelain.splitlines() if l.startswith("worktree ")), top)
        self.primary = first
        self.live = []
        if live:
            self.live = [l.strip() for l in Path(live).read_text().splitlines() if l.strip()]
        self._patch_ids = {}
        self._landed = {}

    def git(self, *args, cwd=None):
        return out(["git", "-C", cwd or self.primary, *args])

    # -- worktrees and liveness --------------------------------------------

    def worktrees(self):
        """[(path, branch or None, head sha)] from the porcelain listing."""
        rows, path, branch, head = [], None, None, None
        for line in (self.git("worktree", "list", "--porcelain") or "").splitlines() + [""]:
            if line.startswith("worktree "):
                path, branch, head = line[9:], None, None
            elif line.startswith("HEAD "):
                head = line[5:]
            elif line.startswith("branch "):
                branch = line[7:].removeprefix("refs/heads/")
            elif not line and path:
                rows.append((path, branch, head))
                path = None
        return rows

    def worktree_of(self, branch):
        return next((p for p, b, _ in self.worktrees() if b == branch), None)

    def live_in(self, directory):
        """The live session standing in `directory` or under it, or None.

        The trailing slash on both sides keeps a sibling named like a prefix of
        this one (wt-other, wtx) from reading as inside it.
        """
        real = os.path.realpath(directory).rstrip("/")
        for cwd in self.live:
            # Both sides resolved: /tmp and /private/tmp are one directory, and
            # a comparison that resolves only one side holds nothing there.
            if (os.path.realpath(cwd).rstrip("/") + "/").startswith(real + "/"):
                return cwd
        return None

    def branches(self):
        names = lines(self.git("for-each-ref", "--format=%(refname:short)", "refs/heads/"))
        return [n for n in names if n not in LONG_LIVED]

    def dirty(self, path):
        return bool(out(["git", "-C", path, "status", "--porcelain"]))

    def own_commits(self, ref):
        """Commits on `ref` neither long-lived branch has, merges not counted.

        Against both, because a branch cut from main has an ancient merge base
        with develop, and counting from that reports every release merge in
        between as the branch's own.
        """
        return self.git("rev-list", "--count", "--no-merges", ref,
                        "^origin/develop", "^origin/main") or "0"

    # -- has this landed ---------------------------------------------------

    def _patch_id(self, diff):
        code, text = run(["git", "patch-id", "--stable"], self.primary, diff)
        return text.decode().split(" ")[0].strip() if code == 0 and text else ""

    def _commit_patch_id(self, sha):
        if sha not in self._patch_ids:
            code, show = run(["git", "-C", self.primary, "show", sha], data=b"")
            self._patch_ids[sha] = self._patch_id(show) if code == 0 else ""
        return self._patch_ids[sha]

    def landed(self, ref):
        """Whether git or GitHub can see this ref's work somewhere else.

        Squash merges defeat `git cherry`, which compares one commit at a time,
        so the cumulative patch is what gets compared.
        """
        tip = self.git("rev-parse", ref)
        if not tip:
            return False
        if (ref, tip) not in self._landed:
            self._landed[(ref, tip)] = self._landed_now(ref, tip)
        return self._landed[(ref, tip)]

    def _landed_now(self, ref, tip):
        if self.slug:
            merged = out(["gh", "pr", "list", "--repo", self.slug, "--head", ref,
                          "--state", "merged", "--json", "headRefOid",
                          "--jq", ".[].headRefOid"]) or ""
            if tip in merged.split():
                return True
        # An ancestor of either long-lived branch carries nothing of its own.
        for base in ("origin/develop", "origin/main"):
            if self.git("merge-base", base, ref) == tip:
                return True
        base = self.git("merge-base", "origin/develop", ref)
        if not base:
            return False
        code, diff = run(["git", "-C", self.primary, "diff", f"{base}..{ref}"], data=b"")
        pid = self._patch_id(diff) if code == 0 else ""
        if not pid:
            return False
        since = lines(self.git("rev-list", f"--max-count={PATCH_SCAN}",
                               "origin/develop", f"^{base}"))
        return any(self._commit_patch_id(c) == pid for c in since)

    def reached_origin(self, ref):
        """Whether any copy of this commit exists off this disk."""
        tip = self.git("rev-parse", ref)
        if not tip:
            return False
        if self.git("branch", "-r", "--contains", tip):
            return True
        return self.landed(ref)

    # -- readings ----------------------------------------------------------

    def in_flight(self):
        code, heads = run(["git", "-C", self.root, "ls-remote", "--heads", "origin", "version-*"])
        if code != 0:
            raise Refuse("could not ask origin whether a release is in flight")
        found = [l.split("refs/heads/", 1)[-1] for l in lines(heads)]
        if self.slug:
            open_main = raw(["gh", "pr", "list", "--repo", self.slug, "--base", "main",
                             "--state", "open", "--json", "number,headRefName",
                             "--jq", '.[] | "  #\\(.number)  \\(.headRefName)"'])
            if open_main is None:
                raise Refuse("could not ask whether a release is open into main")
            found += lines(open_main)
        if found:
            raise Refuse("a release is already in flight:\n" + "\n".join(found))

    def waiting(self):
        """Non-draft pull requests open against develop. Unreadable is a refusal."""
        if not self.slug:
            raise Refuse("no GitHub slug for origin, so the open pull requests cannot be read")
        text = raw(["gh", "pr", "list", "--repo", self.slug, "--base", "develop",
                    "--state", "open", "--json", "number,title,headRefName,isDraft",
                    "--jq", '.[] | select(.isDraft | not) | "  #\\(.number)  \\(.headRefName)  \\(.title)"'])
        if text is None:
            raise Refuse("could not read the pull requests open against develop")
        return lines(text)

    def has_site(self):
        if self.slug in site_elsewhere():
            return True
        return any(Path(p, ".vercel", "project.json").is_file() for p in (self.root, self.primary))

    def unpushed(self):
        """[(ref, commits, live cwd or None)] for work origin has no copy of.

        Every branch and every detached worktree HEAD, deduped on the commit with
        branches first, so a worktree on a branch is named once, by name.
        """
        seen, refs = set(), []
        heads = [h for _, _, h in self.worktrees() if h]
        for ref in self.branches() + heads:
            tip = self.git("rev-parse", ref)
            if tip and tip not in seen:
                seen.add(tip)
                refs.append(ref)
        found = []
        for ref in refs:
            if self.reached_origin(ref):
                continue
            where = self.worktree_of(ref) or next(
                (p for p, b, h in self.worktrees() if b is None and h == ref), None)
            found.append((ref, self.own_commits(ref), self.live_in(where) if where else None))
        return found


# -- commands ----------------------------------------------------------------


def say_unpushed(yard):
    found = yard.unpushed()
    for ref, n, live in found:
        tail = f" - live session in {live}, leave it" if live else " - nobody is in it, push it now"
        print(f"NEVER PUSHED: {ref}, {n} commits that exist nowhere else{tail}")
    return found


def cmd_preflight(yard, which):
    yard.git("fetch", "--quiet", "origin", "main", "develop", cwd=yard.root)
    for b in LONG_LIVED:
        if yard.git("show-ref", "--verify", "--quiet", f"refs/remotes/origin/{b}", cwd=yard.root) is None:
            raise Refuse(f"no {b}")
    carries = lines(yard.git("log", "origin/main..origin/develop", "--oneline", cwd=yard.root))
    if carries:
        print("the next release carries:")
        print("\n".join(carries))
    else:
        print("nothing to release: develop holds nothing main does not")

    if which == "merge":
        if lines(yard.git("log", "origin/develop..origin/main", "--no-merges", "--oneline", cwd=yard.root)):
            raise Refuse("work landed on main directly; reconcile first")
        if yard.dirty(yard.root):
            raise Refuse("the working tree is dirty")

    yard.in_flight()

    if which == "merge":
        stuck = yard.waiting()
        if stuck:
            raise Refuse("these are waiting to land on develop and this release would leave them behind:\n"
                         + "\n".join(stuck))
        if say_unpushed(yard):
            print("origin has no copy of the work above")

    build = site_elsewhere().get(yard.slug or "")
    if which == "merge" and build:
        if out(["git", "-C", yard.root, "remote", "get-url", "upstream"]) is None:
            yard.git("remote", "add", "upstream", f"https://github.com/{build}.git", cwd=yard.root)
        if yard.git("fetch", "--quiet", "upstream", "main", cwd=yard.root) is None:
            raise Refuse(f"could not read main on {build}")
        if yard.git("merge-base", "--is-ancestor", "upstream/main", "origin/develop", cwd=yard.root) is None:
            extra = yard.git("log", "--oneline", "origin/develop..upstream/main", cwd=yard.root) or ""
            raise Refuse(f"{build} holds commits this side does not, and this release could not be "
                         f"pushed to it:\n{extra}\nCollect them into develop first - docs/COLLABORATION.md.")

    print(f"slug={yard.slug or ''}")
    print(f"primary={yard.primary}")
    print(f"nothing_to_release={'' if carries else '1'}")
    print(f"has_site={'1' if yard.has_site() else ''}")
    if which == "pretrip":
        prefixes = _profile().owners()
        monitored = bool(yard.slug) and bool(prefixes) and yard.slug.startswith(prefixes)
        print(f"monitored={'1' if monitored else ''}")
    else:
        print(f"build_slug={build or ''}")
        print(f"deploy_slug={build or yard.slug or ''}")
    return 0


def cmd_waiting(yard):
    found = yard.waiting()
    if found:
        print("waiting on develop:")
        print("\n".join(found))
    else:
        print("nothing waiting on develop")
    return 0


def cmd_copy_files(yard):
    names = lines(yard.git("diff", "--name-only", "origin/main...origin/develop", cwd=yard.root))
    keep = [n for n in names if not COPY_SKIP.search(n) and COPY_KEEP.search(n)]
    if keep:
        print("\n".join(keep))
    else:
        print("no reader-facing files in this release: no copy pass")
    return 0


def socks_proxy():
    code, text = run(["ps", "-eo", "args"])
    for line in (text or "").splitlines():
        if "tailscaled" in line:
            m = re.search(r"--socks5-server=(\S+)", line)
            if m:
                return m.group(1)
    return None


def cmd_pi(yard):
    """Exit 0 alive, 1 stale, 2 no answer at all - three different findings."""
    feed_at = feed_url()
    if not feed_at:
        print("NO HOST: the profile names no host.url, so there is no feed to read")
        return 2
    proxy = socks_proxy()
    args = ["curl", "-s", "--max-time", "20"] + (["--socks5-hostname", proxy] if proxy else []) + [feed_at]
    code, text = run(args)
    try:
        feed = json.loads(text) if code == 0 and text else None
    except ValueError:
        feed = None
    if not feed:
        print("NO ANSWER: the feed did not come back"
              + (f" through {proxy}" if proxy else " (no Tailscale SOCKS proxy found)")
              + ". Run `ssh %s 'systemctl is-active error-reporter.service'` before calling the host down."
              % (_profile().get("host.ssh") or "<host>"))
        return 2
    from datetime import datetime
    stamp = datetime.fromisoformat(feed["updated_at"].replace("Z", "+00:00")).timestamp()
    age = (time.time() - stamp) / 60
    print("feed rebuilt %.0f min ago" % age)
    for site in feed.get("sites", []):
        print(site.get("domain"), site.get("status"))
    queue = _profile().get("github.tickets")
    tickets = out(["gh", "issue", "list", "--repo", queue, "--label",
                   "auto-report", "--state", "open", "--limit", "20"]) if queue else None
    print("open auto-report tickets:" if tickets else
          ("open auto-report tickets: none" if tickets is not None else "open auto-report tickets: COULD NOT READ"))
    if tickets:
        print(tickets)
    if age > FEED_STALE_MINUTES:
        print(f"STALE: older than {FEED_STALE_MINUTES} minutes, so the host is not alive and nothing above means anything")
        return 1
    return 0


def cmd_unpushed(yard):
    if not say_unpushed(yard):
        print("nothing unpushed: origin has a copy of every branch and worktree HEAD")
    return 0


def newest_edit(path):
    status = raw(["git", "-C", path, "status", "--porcelain"]) or ""
    stamps = []
    for line in status.splitlines():
        try:
            stamps.append(os.stat(os.path.join(path, line[3:])).st_mtime)
        except OSError:
            continue
    return max(stamps) if stamps else None


def cmd_abandoned(yard):
    found = 0
    base = Path(yard.primary, ".claude", "worktrees")
    for wt in sorted(base.iterdir()) if base.is_dir() else []:
        if not wt.is_dir() or not yard.dirty(str(wt)) or yard.live_in(str(wt)):
            continue
        newest = newest_edit(str(wt))
        if newest is None:
            continue
        age = int((time.time() - newest) // 3600)
        if age >= 24:
            print(f"abandoned: {wt}, newest edit {age}h old")
            found += 1
    with_tree = {b for _, b, _ in yard.worktrees() if b}
    for b in yard.branches():
        if yard.landed(b):
            continue
        n = yard.own_commits(b)
        # A worktree is a reason to leave the directory alone, never a reason to
        # stop looking at the branch it holds.
        print(f"unlanded: {b}, {n} commits develop does not have, in a worktree" if b in with_tree
              else f"abandoned: {b}, {n} commits develop does not have, no worktree")
        found += 1
    if not found:
        print("nothing abandoned")
    return 0


def holds_unique(yard, wt):
    """The first ignored file in `wt` that exists nowhere else, or None.

    Build output rebuilds, a binary rebuilds, and a text file the primary
    checkout holds byte-for-byte is a copy. A directory is not compared and
    holds outright.
    """
    status = raw(["git", "-C", wt, "status", "--porcelain", "--ignored"]) or ""
    for line in status.splitlines():
        if not line.startswith("!! "):
            continue
        rel = line[3:]
        if REBUILDS.search(rel) or CACHES.search(rel):
            continue
        full = os.path.join(wt, rel)
        if os.path.isdir(full):
            return rel
        mime = out(["file", "-b", "--mime-type", full]) or ""
        if not (mime.startswith("text/") or mime in TEXT_MIME):
            continue
        twin = os.path.join(yard.primary, rel)
        try:
            if os.path.isfile(twin) and Path(twin).read_bytes() == Path(full).read_bytes():
                continue
        except OSError:
            pass
        return rel
    return None


def cmd_sweep(yard, dry, live_given):
    act = "would remove" if dry else "removed"
    freed = set()

    def checked_out(b):
        return b in {x for _, x, _ in yard.worktrees() if x} - freed

    def do(*args):
        if not dry:
            return yard.git(*args) is not None
        if args[:2] == ("branch", "-D"):
            return not checked_out(args[2])
        if args[:2] == ("branch", "-d"):
            # git's own -d test, approximated: not checked out anywhere, and
            # merged into its upstream where that exists, else into HEAD.
            b = args[2]
            up = yard.git("rev-parse", "--abbrev-ref", f"{b}@{{upstream}}")
            into = up if up and yard.git("rev-parse", "--verify", "--quiet", up) else "HEAD"
            return not checked_out(b) and yard.git("merge-base", "--is-ancestor", b, into) is not None
        return True

    if not dry and not live_given:
        raise Refuse("no --live file. Removing worktrees without knowing where the live sessions "
                     "stand is the exact incident the hold exists for; write one, or pass an "
                     "empty file to say deliberately that there are none")
    print(f"swept: read {len(yard.live)} live working directories")

    yard.git("fetch", "--prune", "--quiet", "origin", cwd=yard.root)
    print("swept: remote-tracking refs")

    for b in LONG_LIVED:
        if yard.git("show-ref", "--verify", "--quiet", f"refs/heads/{b}") is None:
            continue
        before = yard.git("rev-parse", b)
        tree = yard.worktree_of(b)
        if not dry:
            # Attempted even where the tree is dirty: git refuses only a
            # fast-forward that would overwrite a modified file.
            if tree:
                yard.git("merge", "--ff-only", "--quiet", f"origin/{b}", cwd=tree)
            else:
                yard.git("fetch", "--quiet", "origin", f"{b}:{b}")
        after = yard.git("rev-parse", b)
        behind = yard.git("rev-list", "--count", f"{b}..origin/{b}") or "0"
        ahead = yard.git("rev-list", "--count", f"origin/{b}..{b}") or "0"
        if before != after:
            print(f"fast-forwarded {b} to {yard.git('rev-parse', '--short', b)}")
        elif ahead != "0":
            print(f"left {b} behind origin/{b}: it holds {ahead} commits of its own")
        elif behind != "0":
            print(f"would fast-forward {b}, {behind} behind" if dry else
                  f"left {b} {behind} behind: the fast-forward would overwrite uncommitted work in {tree or yard.primary}")
    print("swept: local develop and main")

    base = Path(yard.primary, ".claude", "worktrees")
    root_real = os.path.realpath(yard.root)
    for wt in sorted(base.iterdir()) if base.is_dir() else []:
        if not wt.is_dir():
            continue
        wt = str(wt)
        if os.path.realpath(wt) == root_real:
            print(f"held {wt}: this session is running in it")
            continue
        live = yard.live_in(wt)
        if live:
            print(f"held {wt}: {live} is a live session's working directory")
            continue
        if yard.dirty(wt):
            print(f"held {wt}: the tree is dirty")
            continue
        kept = holds_unique(yard, wt)
        if kept:
            print(f"held {wt}: it holds {kept}, which the primary checkout does not")
            continue
        branch = out(["git", "-C", wt, "symbolic-ref", "--short", "HEAD"])
        if not branch:
            print(f"{act} detached {wt}" if do("worktree", "remove", wt)
                  else f"held {wt}: git refused to remove it")
            continue
        track = yard.git("for-each-ref", "--format=%(upstream:track)", f"refs/heads/{branch}", cwd=wt)
        if track == "[gone]" or yard.landed(branch):
            if do("worktree", "remove", wt):
                print(f"{act} landed {wt} ({branch})")
                freed.add(branch)
            else:
                print(f"held {wt}: git refused to remove it")
            continue
        n = yard.git("rev-list", "--count", f"origin/develop..{branch}") or "0"
        print(f"held {wt}: {branch} carries {n} commits develop does not")
    if not dry:
        yard.git("worktree", "prune")
    print("swept: worktrees whose work has landed")

    gone = set()
    for line in lines(yard.git("for-each-ref", "--format=%(refname:short) %(upstream:track)", "refs/heads/")):
        name, _, track = line.partition(" ")
        if track == "[gone]" and name not in LONG_LIVED and do("branch", "-d", name):
            print(f"{act} local {name}")
            gone.add(name)
    print("swept: branches with a gone upstream")

    for line in lines(raw(["git", "-C", yard.primary, "branch", "--merged", "origin/develop"])):
        b = line[2:].strip()
        if b and b not in LONG_LIVED and b not in gone and not b.startswith("(") and do("branch", "-d", b):
            print(f"{act} merged {b}")
            gone.add(b)
    print("swept: branches already merged into origin/develop")

    for b in yard.branches():
        if b in gone or (dry and checked_out(b)):
            continue
        if yard.landed(b) and do("branch", "-D", b):
            print(f"{act} squash-merged {b}")
            gone.add(b)
    print("swept: branches whose patches are already on develop")

    print("=== left standing ===")
    for b in yard.branches():
        if b in gone:
            continue
        why = []
        if not yard.landed(b):
            why = [f"carries {yard.own_commits(b)} commits develop does not"]
        # Unlanded is a reason to survive; unpushed never is.
        if not yard.reached_origin(b):
            why = ["NEVER PUSHED - origin has no copy of this"]
        tree = yard.worktree_of(b)
        if tree and not (dry and b in freed):
            if yard.dirty(tree):
                why.append("its worktree is dirty")
            if yard.live_in(tree):
                why.append("a live session is working in it")
            unique = holds_unique(yard, tree)
            if unique:
                why.append(f"its worktree holds {unique}, which the primary checkout does not")
        # A branch carrying nothing is empty, so an empty count is never a reason.
        why = [w for w in why if not w.startswith("carries 0 commits")]
        print("  %-52s %s" % (b, "; ".join(why) or "NO REASON - the sweep should have taken this"))
    print("swept: asserted what is left standing")
    return 0


def cmd_deploy_wait(slug, sha, appear=5, settle=15):
    """Poll every 30s. Exit 0 on success with the URL, 1 on any failure."""
    deadline, appear_by = time.time() + settle * 60, time.time() + appear * 60
    state = None
    while True:
        did = out(["gh", "api", f"repos/{slug}/deployments?sha={sha}&per_page=1", "--jq", ".[0].id"])
        if not did:
            if time.time() > appear_by:
                print(f"FAILED: no deployment for {sha} after {appear} minutes")
                return 1
        else:
            status = out(["gh", "api", f"repos/{slug}/deployments/{did}/statuses",
                          "--jq", ".[0] | [.state, .environment_url] | @tsv"]) or ""
            state, _, url = status.partition("\t")
            print(state or "pending")
            if state == "success":
                print(f"environment_url={url}")
                return 0
            if state in ("failure", "error"):
                print(f"FAILED: deployment {state}")
                return 1
        if time.time() > deadline:
            print(f"FAILED: deployment still {state or 'absent'} after {settle} minutes")
            return 1
        time.sleep(30)


def main(argv=None):
    def options(parser, default):
        parser.add_argument("--root", default=default)
        parser.add_argument("--slug", default=default, help="owner/name, where origin is not a GitHub URL")
        parser.add_argument("--live", default=default, help="file of live session working directories")

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    options(p, None)
    common = argparse.ArgumentParser(add_help=False)
    options(common, argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("preflight", parents=[common])
    pf.add_argument("which", choices=("pretrip", "merge"))
    for name in ("waiting", "copy-files", "pi", "unpushed", "abandoned"):
        sub.add_parser(name, parents=[common])
    sw = sub.add_parser("sweep", parents=[common])
    sw.add_argument("--dry-run", action="store_true")
    dw = sub.add_parser("deploy-wait", parents=[common])
    dw.add_argument("--sha", required=True)
    dw.add_argument("--deploy-slug", required=True)
    for name in ("landed", "reached-origin"):
        sub.add_parser(name, parents=[common]).add_argument("ref")
    a = p.parse_args(argv)
    try:
        if a.cmd == "deploy-wait":
            return cmd_deploy_wait(a.deploy_slug, a.sha)
        if a.cmd == "pi":
            return cmd_pi(None)
        yard = Yard(a.root, a.slug, a.live)
        if a.cmd == "preflight":
            return cmd_preflight(yard, a.which)
        if a.cmd == "landed":
            return 0 if yard.landed(a.ref) else 1
        if a.cmd == "reached-origin":
            return 0 if yard.reached_origin(a.ref) else 1
        return {"waiting": cmd_waiting, "copy-files": cmd_copy_files,
                "unpushed": cmd_unpushed, "abandoned": cmd_abandoned}.get(
                    a.cmd, lambda y: cmd_sweep(y, a.dry_run, a.live is not None))(yard)
    except Refuse as why:
        print(f"REFUSE: {why}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
