#!/usr/bin/env bash
# Bring a checkout under ~/work to its remote branch, and say what it took.
#
# Installed at ~/bin/work-sync.sh and called as step 0 by every
# routine that reads a project checkout before working in it:
#
#   ~/bin/work-sync.sh ~/work/example-com develop
#
# The checkouts under ~/work are readers. No routine commits to their
# long-lived branches - a routine that has work to land cuts its own branch
# from origin and pushes that - so `git pull --ff-only` was the whole of this
# step for months. What --ff-only cannot survive is the remote's history being
# replaced underneath the clone. acme-com was republished as a new public
# repository on 2026-09-05; the clone here kept the old 429 commits; and from
# the next morning on, step 0 died with "Not possible to fast-forward" on two
# histories that no longer shared a single commit. It is not a merge conflict
# and waiting does not clear it.
#
# So this resyncs rather than fast-forwards, and it tags before it moves: a
# branch tip being left behind becomes archive/<branch>-<date>-<sha>, which
# keeps every commit on it reachable and the move reversible.
#
# It still refuses the one case that belongs to a person - a commit authored in
# this checkout that the remote does not carry - rather than archiving its way
# past it. The branch reflog is what tells the two apart: every entry on a
# reader branch is a clone, a fetch, a pull or a reset, so a commit, merge,
# rebase or cherry-pick entry still sitting on the branch means somebody worked
# here and nothing should move until they say so.
#
# Exit codes, because callers act on them:
#   0  the branch is at origin's tip (already there, fast-forwarded, or resynced)
#   2  tracked files are modified, so nothing was touched
#   3  the branch carries local commits the remote does not have
#   1  git itself failed; 64 the arguments did
set -uo pipefail

DIR=${1:-}
BRANCH=${2:-}

if [ -z "$DIR" ] || [ -z "$BRANCH" ]; then
  echo "usage: work-sync.sh <checkout> <branch>" >&2
  exit 64
fi
if ! git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1; then
  echo "work-sync: $DIR is not a git checkout" >&2
  exit 64
fi

git() { command git -C "$DIR" "$@"; }

git fetch --prune --quiet origin || { echo "work-sync: fetch failed in $DIR" >&2; exit 1; }

REMOTE="origin/$BRANCH"
TARGET=$(git rev-parse -q --verify "$REMOTE^{commit}") || {
  echo "work-sync: $DIR has no $REMOTE" >&2
  exit 1
}

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "work-sync: $DIR has modified tracked files; left alone" >&2
  git status --short --untracked-files=no >&2
  exit 2
fi

LOCAL=$(git rev-parse -q --verify "refs/heads/$BRANCH") || LOCAL=""

if [ -z "$LOCAL" ]; then
  git checkout -q -B "$BRANCH" "$TARGET" || exit 1
  echo "work-sync: $DIR created $BRANCH at ${TARGET:0:7}"
  exit 0
fi

if [ "$LOCAL" = "$TARGET" ]; then
  git checkout -q "$BRANCH" || exit 1
  echo "work-sync: $DIR $BRANCH already at ${TARGET:0:7}"
  exit 0
fi

if git merge-base --is-ancestor "$LOCAL" "$TARGET"; then
  git checkout -q "$BRANCH" && git merge --ff-only --quiet "$TARGET" || exit 1
  echo "work-sync: $DIR $BRANCH fast-forwarded ${LOCAL:0:7} -> ${TARGET:0:7}"
  exit 0
fi

# The branch is not on origin's line. Anything committed here and still on the
# branch is the one thing this script will not decide.
AUTHORED=()
while read -r sha subject; do
  # A fast-forward writes "merge <ref>: Fast-forward" and creates no commit, so
  # the verb alone would read every pull on the branch as work done here.
  case "$subject" in
    *": Fast-forward") continue ;;
  esac
  case "$subject" in
    commit*|merge*|rebase*|cherry-pick*|am*|revert*) ;;
    *) continue ;;
  esac
  git merge-base --is-ancestor "$sha" "$TARGET" && continue
  git merge-base --is-ancestor "$sha" "$LOCAL" || continue
  AUTHORED+=("$sha")
done < <(git reflog show "$BRANCH" --format='%H %gs' 2>/dev/null)

if [ ${#AUTHORED[@]} -gt 0 ]; then
  echo "work-sync: $DIR $BRANCH carries commits made here that $REMOTE does not have:" >&2
  git log --no-walk --format='  %h %cs %s' "${AUTHORED[@]}" >&2
  echo "work-sync: nothing moved; this one is a person's call" >&2
  exit 3
fi

TAG="archive/$BRANCH-$(git log -1 --format=%cs "$LOCAL")-${LOCAL:0:7}"
git tag -f "$TAG" "$LOCAL" >/dev/null || exit 1
git checkout -q -B "$BRANCH" "$TARGET" || exit 1

if git merge-base "$LOCAL" "$TARGET" >/dev/null 2>&1; then
  WHY="diverged from"
else
  WHY="shared no history with"
fi
echo "work-sync: $DIR $BRANCH $WHY $REMOTE; ${LOCAL:0:7} kept as $TAG, branch resynced to ${TARGET:0:7}"
exit 0
