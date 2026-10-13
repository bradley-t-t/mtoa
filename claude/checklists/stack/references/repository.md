# Repository compliance

All of this is written down in CLAUDE.md already. None of it was ever verified by
anything, which is how a repository ends up half set up and looking finished.

## The branch model

`main` is production and only `develop` merges into it. `develop` is integration and
every worktree branch merges there. Worktree branches are cut from `develop`, never
from `main` — `develop` is ahead of `main` between promotions, so a branch cut from
`main` is missing work already integrated, and that only shows up when its pull request
reintroduces merged commits or conflicts on files `develop` has since changed.

`git merge-base --is-ancestor origin/develop HEAD` is the whole check.

## Protection, and the one asymmetry

Both branches require the `check` context, refuse force pushes and deletions, and
require conversations resolved. `enforce_admins` stays off so a bootstrap merge is
possible.

`develop`'s check is **strict** and `main`'s is not, and that is deliberate. Strict
means the head branch must contain the base. On `develop` that is the point: a feature
branch is tested against the develop it will land on. On `main` it is a treadmill,
because a merge-commit promotion puts a commit on `main` that `develop` never receives,
so `develop` reads as behind from the moment the promotion lands. Those commits carry
no code, and `check` still gates every promotion.

## Two checkouts

A repo is held once. One was held twice and a build from the stale twin overwrote a
night of work; the twin looks exactly like the real one until it does.
The gate sweeps for siblings itself, comparing origin URLs and common git directories so a worktree is never read as one.

Work happens in a worktree, never the primary checkout. Two sessions in one checkout
share a HEAD and an index, and the second to switch branches carries the first one's
uncommitted work away with it — and neither can see that it happened.

## The workflow

`ci.yml` matches the standard byte for byte on **both** branches. A workflow that
drifted on one gates nothing there, and the branch it still gates is not the one
anybody looks at. The standard copy is at `references/ci.yml` in this skill.
