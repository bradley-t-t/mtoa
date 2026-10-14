#!/usr/bin/env python3
"""Prune the Claude Code Desktop Recent-folders list.

The desktop app derives the folder picker's Recent list from two on-disk
sources: the session records under
`~/Library/Application Support/Claude/claude-code-sessions/`, grouped by
each session's `cwd`/`originCwd` project root, and the roots listed under
`untrackedDirGc.roots` in `git-worktrees.json`. This tool clears both so
only the folders in the keep list survive on the next app restart.

The desktop app holds those files open while it is running and does not
re-read them until it relaunches, so the picker will keep showing the old
list until the app is quit and reopened. On a machine without the desktop
app installed the paths do not exist and the tool is a no-op.
"""
import argparse
import json
import os
import sys
from pathlib import Path

APP_ROOT = Path.home() / "Library/Application Support/Claude"
SESSIONS_ROOT = APP_ROOT / "claude-code-sessions"
WORKTREES_FILE = APP_ROOT / "git-worktrees.json"
def default_keep():
    """The folders the profile always keeps (desktop.keep_folders)."""
    import importlib.util
    for path in (Path(__file__).resolve().parent / "mtoa_config.py",
                 Path.home() / ".claude" / "tools" / "mtoa_config.py"):
        if path.is_file():
            spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return tuple(module.get("desktop.keep_folders") or ())
    return ()


def project_root(cwd: str) -> str:
    if not cwd:
        return ""
    return cwd.split("/.claude/worktrees/", 1)[0]


def basename_of(path: str) -> str:
    return os.path.basename(path.rstrip("/")) or path


def prune_sessions(keep: set[str], dry_run: bool) -> None:
    if not SESSIONS_ROOT.is_dir():
        print(f"sessions dir absent: {SESSIONS_ROOT} (skipped)")
        return
    records = list(SESSIONS_ROOT.glob("*/*/local_*.json"))
    to_delete: list[Path] = []
    kept = 0
    unreadable = 0
    for record in records:
        try:
            data = json.loads(record.read_text())
        except Exception:
            unreadable += 1
            continue
        cwd = data.get("originCwd") or data.get("cwd") or ""
        name = basename_of(project_root(cwd))
        # A pinned session is kept on purpose, whatever folder it works in.
        if name in keep or data.get("isStarred"):
            kept += 1
            continue
        to_delete.append(record)

    print(
        f"session records: scanned={len(records)} kept={kept} "
        f"unreadable={unreadable} to_delete={len(to_delete)}"
    )
    if dry_run:
        return
    for path in to_delete:
        path.unlink()


def prune_untracked_roots(keep: set[str], dry_run: bool) -> None:
    if not WORKTREES_FILE.exists():
        print(f"worktrees file absent: {WORKTREES_FILE} (skipped)")
        return
    doc = json.loads(WORKTREES_FILE.read_text())
    roots = doc.get("untrackedDirGc", {}).get("roots", {})
    removed: list[str] = []
    for path in list(roots.keys()):
        stripped = path.split("/.claude/worktrees", 1)[0]
        if basename_of(stripped) in keep:
            continue
        removed.append(path)
        if not dry_run:
            del roots[path]
    print(f"untrackedDirGc.roots: kept={len(roots)} to_delete={len(removed)}")
    if dry_run or not removed:
        return
    WORKTREES_FILE.write_text(json.dumps(doc, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="append",
        metavar="NAME",
        help="folder basename to keep (repeatable); defaults to desktop.keep_folders in the profile",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    keep = set(args.keep) if args.keep else set(default_keep())
    if not APP_ROOT.is_dir():
        print(f"no desktop app at {APP_ROOT}, nothing to prune")
        return 0

    print(f"keep list: {', '.join(sorted(keep))}")
    prune_sessions(keep, args.dry_run)
    prune_untracked_roots(keep, args.dry_run)

    if args.dry_run:
        print("dry-run; no files changed")
    else:
        print("done. quit and reopen the desktop app to see the picker refresh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
