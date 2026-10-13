#!/usr/bin/env python3
"""Find work that landed and never went live.

`/commit` puts a branch on `develop` and stops. `/merge` puts `develop` on
`main` and stops. Neither has ever looked at what the other left behind, so
work strands in three places and nothing on this machine notices any of them:

  - commits sitting on `develop` that no release has carried to `main`
  - a pull request sitting green and unmerged against `develop`
  - a commit sitting in a worktree that was never pushed at all

All three are silent. A release carries `develop` exactly as it stands, so a
branch in a green pull request does not go out late - it misses that release
and every release after it. Commits already on `develop` are worse, because
they read as landed everywhere a person would look.

The third is worse than either, and this tool was blind to it for as long as it
existed. The first two ask origin, and a commit that never left the machine is
invisible to both. On 2026-09-10 that hid a nine-file change while this tool
reported the whole yard clear, and it is the only one of the three where the
work can be destroyed outright rather than merely delayed - the remote holds no
copy to recover from, so a removed worktree takes it with it.

This is the thing that looks. It reads all three, per repo, with an age on
each, and exits non-zero when anything has been waiting longer than the
threshold, so a routine or a cron can read its status without parsing the
report.

  release-lag.py                     every owned checkout under ~/Development
  release-lag.py --root <path>       one repo
  release-lag.py --stale-hours 12    what counts as stranded (default 6)
  release-lag.py --no-fetch          read the refs already on disk
  release-lag.py --json              the same reading, as JSON

An unread pull-request list is reported as unread, never as clear. A repo whose
pull requests could not be listed is stranded-unknown and exits non-zero on its
own: "I could not check" and "there is nothing there" must never print alike.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

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


def owners():
    """The profile's GitHub owners as slug prefixes; empty means none are set."""
    return _profile().owners()


SEARCH_ROOTS = (
    Path.home() / "Development" / "WebstormProjects",
    Path.home() / "Development",
)
DEFAULT_STALE_HOURS = 6.0


def run(args, cwd=None, timeout=60):
    """A command's stdout, or None when it failed for any reason."""
    try:
        done = subprocess.run(args, cwd=cwd, timeout=timeout,
                              capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def slug_of(root, runner=run):
    url = runner(["git", "-C", str(root), "remote", "get-url", "origin"])
    if not url:
        return None
    url = url.strip()
    for marker in ("github.com:", "github.com/"):
        if marker in url:
            url = url.split(marker, 1)[1]
            break
    else:
        return None
    return url[:-4] if url.endswith(".git") else url


def is_owned(slug):
    prefixes = owners()
    return bool(slug) and bool(prefixes) and slug.startswith(prefixes)


def checkouts(search_roots=SEARCH_ROOTS, runner=run):
    """Every owned primary checkout on this machine, deduped by slug.

    Worktrees under `.claude/worktrees/` share their parent's remote and are
    not walked: a repo is read once however many sessions are open in it.
    """
    found = {}
    for base in search_roots:
        if not Path(base).is_dir():
            continue
        for entry in sorted(Path(base).iterdir()):
            if not (entry / ".git").exists():
                continue
            slug = slug_of(entry, runner=runner)
            if is_owned(slug) and slug not in found:
                found[slug] = entry
    return [(slug, found[slug]) for slug in sorted(found)]


def hours_since(stamp, now=None):
    """Hours between an ISO-8601 stamp and now, or None if it will not parse."""
    if not stamp:
        return None
    text = stamp.strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - when).total_seconds() / 3600.0


def describe_age(hours):
    if hours is None:
        return "age unknown"
    if hours < 1:
        return "%dm" % max(1, round(hours * 60))
    if hours < 48:
        return "%dh" % round(hours)
    return "%dd" % round(hours / 24)


def unreleased(root, runner=run):
    """Commits on origin/develop that origin/main does not have.

    Returns (count, oldest_iso). A repo with no develop, or no main, has no
    release gap to measure and reads as zero.
    """
    for ref in ("refs/remotes/origin/develop", "refs/remotes/origin/main"):
        if runner(["git", "-C", str(root), "show-ref", "--verify",
                   "--quiet", ref]) is None:
            return 0, None
    out = runner(["git", "-C", str(root), "log", "--format=%cI",
                  "origin/main..origin/develop", "--no-merges"])
    if not out:
        return 0, None
    stamps = [line for line in out.splitlines() if line.strip()]
    return len(stamps), (stamps[-1] if stamps else None)


def waiting_prs(slug, runner=run):
    """Non-draft pull requests open against develop.

    Returns (list, read_ok). `read_ok` is False when the list could not be
    read at all, which is reported as unknown rather than as none: a repo
    whose pull requests nobody could see is exactly the repo that strands one.
    """
    out = runner(["gh", "pr", "list", "--repo", slug, "--base", "develop",
                  "--state", "open", "--json",
                  "number,title,headRefName,isDraft,createdAt"], timeout=60)
    if out is None:
        return [], False
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return [], False
    return [r for r in rows if not r.get("isDraft")], True



def worktree_heads(root, runner=run):
    """Every local branch tip in this checkout, plus every detached worktree HEAD.

    A worktree's HEAD is read separately because a detached one is not a branch
    and `for-each-ref` cannot see it, and a detached HEAD is exactly what a
    session that landed its branch is left standing on.
    """
    heads = {}
    out = runner(["git", "-C", str(root), "for-each-ref",
                  "--format=%(refname:short) %(objectname)", "refs/heads/"])
    for line in (out or "").splitlines():
        name, _, tip = line.strip().partition(" ")
        if name and tip and name not in ("develop", "main"):
            heads[tip] = name
    out = runner(["git", "-C", str(root), "worktree", "list", "--porcelain"])
    path = None
    for line in (out or "").splitlines():
        key, _, value = line.partition(" ")
        if key == "worktree":
            path = value
        elif key == "HEAD" and path:
            heads.setdefault(value, "(detached) %s" % os.path.basename(path))
        elif key == "branch":
            path = path  # a named branch is already covered above
    return heads


def _reached_origin(root, tip, runner=run):
    """Whether git can see this commit's work anywhere on origin.

    Three ways it can be there, and a squash merge is the one that matters:
    landing a branch rewrites its commits into one new commit with a new SHA,
    so the branch's own commits are on no remote and the work is safe anyway.
    Comparing commits one at a time by patch id gets that wrong for every
    branch of more than one commit - the squash matches none of them - so the
    branch's CUMULATIVE patch is what is compared.
    """
    if runner(["git", "-C", str(root), "branch", "-r", "--contains", tip]):
        return True
    for ref in ("origin/develop", "origin/main"):
        base = runner(["git", "-C", str(root), "merge-base", ref, tip])
        if base and base.strip() == tip:
            return True
    base = runner(["git", "-C", str(root), "merge-base", "origin/develop", tip])
    if not base:
        return False
    base = base.strip()
    mine = runner(["git", "-C", str(root), "diff", "%s..%s" % (base, tip)])
    if mine is None:
        return False
    want = _patch_id(mine)
    if not want:
        return False
    listing = runner(["git", "-C", str(root), "rev-list", "--max-count=500",
                      "origin/develop", "^%s" % base])
    for commit in (listing or "").splitlines():
        commit = commit.strip()
        if not commit:
            continue
        shown = runner(["git", "-C", str(root), "show", commit])
        if shown is not None and _patch_id(shown) == want:
            return True
    return False


def _patch_id(diff_text):
    """The stable patch id of a diff, or None."""
    try:
        done = subprocess.run(["git", "patch-id", "--stable"],
                              input=diff_text, capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return (done.stdout or "").split(" ")[0].strip() or None


def unpushed(root, runner=run, now=None):
    """Work that exists on this machine and nowhere else.

    Everything else this tool measures lives on origin - commits on
    `origin/develop`, pull requests open against it - so a commit that was
    never pushed is invisible to all of it. That is not a corner case: it is
    the one failure where the work can actually be destroyed rather than
    merely delayed, because the remote does not have a copy to recover from.

    Returns a list of {branch, tip, commits, hours}, newest edit first.
    """
    found = []
    for tip, name in worktree_heads(root, runner=runner).items():
        if _reached_origin(root, tip, runner=runner):
            continue
        # Counted against BOTH long-lived branches and without merges. A
        # branch cut from `main` rather than `develop` has an ancient merge
        # base with develop, and counting from it reports every release merge
        # in between as this branch's own work - 68 commits for a one-commit
        # branch, which reads as a disaster and gets ignored as noise.
        count = runner(["git", "-C", str(root), "rev-list", "--count",
                        "--no-merges", tip,
                        "^origin/develop", "^origin/main"])
        when = runner(["git", "-C", str(root), "log", "-1", "--format=%cI", tip])
        found.append({
            "branch": name,
            "tip": tip[:9],
            "commits": int(count.strip()) if count and count.strip().isdigit() else 1,
            "hours": hours_since((when or "").strip() or None, now=now),
        })
    found.sort(key=lambda row: (row["hours"] is None, row["hours"] or 0))
    return found


def read_repo(slug, root, fetch=True, stale_hours=DEFAULT_STALE_HOURS,
              runner=run, now=None):
    if fetch:
        runner(["git", "-C", str(root), "fetch", "--quiet", "origin",
                "main", "develop"], timeout=120)
    count, oldest = unreleased(root, runner=runner)
    age = hours_since(oldest, now=now)
    prs, read_ok = waiting_prs(slug, runner=runner)
    for pr in prs:
        pr["hours"] = hours_since(pr.get("createdAt"), now=now)

    stale_prs = [p for p in prs
                 if p["hours"] is None or p["hours"] >= stale_hours]
    commits_stale = count > 0 and (age is None or age >= stale_hours)

    # Work that never reached origin. Reported however new it is, because a
    # branch nobody pushed is the only thing here that can be lost outright,
    # but only counted stranded once it is past the threshold - a session that
    # committed a minute ago is still working, not stranding.
    local = unpushed(root, runner=runner, now=now)
    local_stale = [row for row in local
                   if row["hours"] is None or row["hours"] >= stale_hours]

    return {
        "slug": slug,
        "root": str(root),
        "unreleased": count,
        "unreleased_oldest_hours": age,
        "prs": prs,
        "prs_read": read_ok,
        "unpushed": local,
        "commits_stranded": commits_stale,
        "prs_stranded": bool(stale_prs),
        "unpushed_stranded": bool(local_stale),
        "unknown": not read_ok,
        "stranded": (commits_stale or bool(stale_prs) or bool(local_stale)
                     or not read_ok),
    }


def line_for(reading):
    """One repo's reading, as the line a person reads back."""
    parts = []
    if reading["unreleased"]:
        parts.append("%d unreleased on develop, oldest %s"
                     % (reading["unreleased"],
                        describe_age(reading["unreleased_oldest_hours"])))
    for pr in reading["prs"]:
        parts.append("#%s open %s (%s)"
                     % (pr.get("number"), describe_age(pr.get("hours")),
                        pr.get("headRefName", "?")))
    for row in reading.get("unpushed", []):
        parts.append("%s never pushed: %d commit%s, newest %s"
                     % (row["branch"], row["commits"],
                        "" if row["commits"] == 1 else "s",
                        describe_age(row["hours"])))
    if not reading["prs_read"]:
        parts.append("pull requests could not be read")
    if not parts:
        return "clear"
    return "  ".join(parts)


def report(readings, out=sys.stdout):
    width = max([len(r["slug"]) for r in readings] or [1])
    for reading in readings:
        mark = "STRANDED" if reading["stranded"] else "        "
        print("%s  %-*s  %s" % (mark, width, reading["slug"],
                                line_for(reading)), file=out)
    stranded = [r for r in readings if r["stranded"]]
    print("", file=out)
    print("stranded: %d of %d" % (len(stranded), len(readings)), file=out)
    return 1 if stranded else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", help="one checkout instead of every owned one")
    ap.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.root:
        root = Path(args.root).expanduser().resolve()
        top = run(["git", "-C", str(root), "rev-parse", "--show-toplevel"])
        if top:
            root = Path(top)
        slug = slug_of(root)
        if not slug:
            print("no github origin at %s" % root, file=sys.stderr)
            return 2
        targets = [(slug, root)]
    else:
        targets = checkouts()

    if not targets:
        print("no owned checkouts found", file=sys.stderr)
        return 2

    readings = [read_repo(slug, root, fetch=not args.no_fetch,
                          stale_hours=args.stale_hours)
                for slug, root in targets]

    if args.json:
        print(json.dumps(readings, indent=2, default=str))
        return 1 if any(r["stranded"] for r in readings) else 0
    return report(readings)


if __name__ == "__main__":
    sys.exit(main())
