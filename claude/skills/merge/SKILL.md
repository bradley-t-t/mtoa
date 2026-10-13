---
name: merge
description: Move the version, put develop into main - which is what makes the site go live - and prove the promotion shipped: the deployment succeeded, the version serving is the one that just landed, and the page loads with nothing in the console. Use on /merge, or "release this", "push it live", "put develop into main", "go live". Landing a branch onto develop is /commit; the inspection and the cleanup before a release are /pretrip.
---

# merge

Puts `develop` into `main`. `main` is the release, and where the repo serves a
site, `main` is what deploys. Half this command is the merge; on a repo with a
site the other half is proving the promotion shipped, and a merge reported done
without that half is a guess, not a release.

That is the whole of it. The inspection before a release - stranded pull
requests, the site and host baselines, the sweep, what is waiting - is `/pretrip`.
The workflow is `/commit`, `/pretrip`, `/merge`, or `/ship` for all three. It
runs on any repo with the two-branch `develop`→`main` shape.

The gates are one tool, `~/.claude/tools/release-yard.py`, run from inside the
checkout. Shell variables do not survive between commands, so values the
preflight prints - `<slug>`, `<version>` and the rest - are placeholders to
fill in. Why each rule exists is `~/.claude/standards/release-history.md`; read
it before changing one. No command here uses a positional parameter, because
skill arguments are expanded into the body before it runs.

## Refuse rather than guess

```bash
~/.claude/tools/release-yard.py preflight merge
```

It prints what the release carries, then `slug=`, `primary=`,
`nothing_to_release=`, `has_site=`, `build_slug=` and `deploy_slug=`. It refuses
- `REFUSE:` and exit 1 - over:

- **No `develop` or `main`.** The setup mirror (`github.mirror`) carries `main` alone
  and is refused here by design.
- **Work landed on `main` directly** (a non-merge commit `develop` lacks).
  Merging over it loses it or writes a merge nobody reviewed. Stop and report.
- **A dirty working tree.**
- **A release already in flight**, or a question about it that could not be
  asked. Stop, say whose release it is, and lift the hold. The work on
  `develop` goes out in that release.
- **Pull requests waiting on `develop`**, or a list it could not read. Run
  `/pretrip` and come back. Do not work the list from here, and do not walk
  away from the release because something was open.
- **The build repository's `main` holds commits this side lacks** (FleetApp,
  below). Collect them into `develop` first - `docs/COLLABORATION.md`.

Work on `main`, a dirty tree and a release in flight stop the whole command,
because going on would collide with somebody or act on something unseen. The
rest stop the release.

**`NEVER PUSHED` lines do not refuse; they are one decision.** Match each
against `list_sessions`. A live session standing in it is mid-work: name it and
release. Nobody in it: stop and name the branch. One `git push -u origin
<branch>` clears it, lands nothing, and the release goes on unchanged.

**`nothing_to_release=1`** is a flag, not a refusal. Steps 1, 3, 4 and 5 are
skipped; step 2 still reads the version already released, and steps 6 through
8 still prove the live site serves it.

**`has_site` empty** - no Vercel project - skips steps 5 through 8, and the
release ends when `main` is updated.

**`build_slug`** names the repository Vercel actually builds when it is not this
one. A repo can merge under one owner while its Vercel project, domain and
environment sit under another, and GitHub does not know they are related: step
4's push is what ships, and every proof after it asks `<deploy_slug>`, because
no deployment ever appears against this repo. A repo arranged that way is one
entry in `github.elsewhere` in the profile.

## Hold the sessions this can collide with

Only the peers in this repo: those whose name begins with the basename of the
primary checkout or one of its worktrees, followed by `-`.

```bash
git worktree list --porcelain | sed -n 's|^worktree ||p' | while read -r p; do basename "$p"; done
```

Filter `ListAgents` to those, drop any peer that has sent a stop notice, and
`SendMessage` each: `Running /merge on <slug>. Hold every push, pull request,
/commit and /merge to this repo until I report done or failed.` None match:
send nothing and say so in one clause. One message per peer per stage; no
chasing, no relaying, and no reply to holds that arrive here - the only things
sent back are a stop notice and doing what a message asked.

## The run

Steps 1-4 promote; steps 5-8 prove the promotion shipped.

**1. Move the version.** Skipped when `nothing_to_release=1`, and on a repo with
no `package.json`. The version moves here and nowhere else, so open branches
never conflict on `package.json`, `public/release.json` or the README badge. It
lands through a pull request into `develop` like everything else; a refused
push of `version-<version>` means a release of that version is already in
flight - stop and find out whose.

```bash
echo "$(mktemp -d)/bump"                                   # <bump>
git worktree add --quiet -b version-bump <bump> origin/develop
(cd <bump> && ~/.claude/tools/bump-version.py patch --root <bump>)
node -p "require('<bump>/package.json').version"           # <version>
git -C <bump> branch -m version-<version>
git -C <bump> commit --quiet -am "Set the version to <version>"
git -C <bump> push -u origin version-<version>
gh pr create --repo <slug> --base develop --head version-<version> \
  --title "Set the version to <version>" --body "<what this release carries, in prose>"
gh pr checks <n> --repo <slug> --watch
gh pr merge <n> --repo <slug> --squash --delete-branch
git worktree remove <bump> && git branch -D version-<version> && git fetch --quiet origin develop
```

The local `version-<version>` branch is deleted here by name - `--delete-branch` reaches only origin's copy,
and `-D` because the squash rewrote the commit.

**2. Read what is being released.** Runs either way.

```bash
git log origin/main..origin/develop --oneline
git show origin/develop:package.json | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>console.log(JSON.parse(s).version))'
```

The version must be the one step 1 set; anything else means the bump did not
land or something landed after it. The log is the release, all of it: `develop`
collects reviewed, green work from every session, so fold it all in. Never stop
to ask whether an unrelated commit belongs, never isolate this session's work,
and never cherry-pick part of `develop` onto `main`. The release pull request
describes the whole log, not the part this session recognises.

**3. Open the release pull request and merge it.** Skipped when
`nothing_to_release=1`. `main` takes a merge commit, not a squash.

```bash
gh pr create --repo <slug> --base main --head develop --title "Release <version>" --body "<step 2's log, in prose>"
gh pr checks <n> --repo <slug> --watch
gh pr merge <n> --repo <slug> --merge
```

`check` runs the version gate here: the version must be ahead of `main`, with
`public/release.json` and the badge agreeing. A red check stops the release.

**4. Push the release to the build repository.** Only where `build_slug` is
set, and not when `nothing_to_release=1`. Nothing before this changed what a
visitor sees.

```bash
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream https://github.com/<build_slug>.git
git fetch --quiet origin main && git fetch --quiet upstream
git log --oneline upstream/main..origin/main
git merge-base --is-ancestor upstream/main origin/main && git push upstream origin/main:main
```

The push is only ever a fast-forward. If the ancestor test fails, never force
it - that discards work on a repository this account does not own. Stop, say
which commits arrived on the other side during the release, and leave it
half-landed: the next release has to collect them before anything ships.

**5. Wait for the deployment of that exact commit.** Skipped when
`nothing_to_release=1` or `has_site` is empty. Vercel reports to GitHub against
the merge commit, so this needs no Vercel token. Run it in the background; it
takes up to fifteen minutes.

```bash
git fetch --quiet origin main && git rev-parse origin/main        # <sha>
~/.claude/tools/release-yard.py deploy-wait --deploy-slug <deploy_slug> --sha <sha>
```

Exit 0 prints `environment_url=`. Exit 1 is a failed release: a deployment that
failed (read the build log in the Vercel dashboard or `vercel logs <url>`), none
appearing within five minutes, or none settling within fifteen. A deployment
that never appears is a release that merged and did not ship, so it is reported
as a failure, never waited on further.

**6. Find the address a visitor uses.** Skipped when `has_site` is empty.

```bash
vercel inspect <environment_url> 2>&1 | grep -A8 -i aliases
```

The shortest `www.` or apex custom domain, cross-checked against `gh api
repos/<slug> --jq .homepage`. Where `build_slug` is set the project is under
another account; take the domain from the repo's README or `docs/` -
`www.fleetapp.app` for FleetApp.

**7. Prove the live site serves this release.** Skipped when `has_site` is
empty.

```bash
curl -sI --max-time 20 https://<domain>/ | head -3
curl -s  --max-time 20 https://<domain>/release.json
```

The version in `release.json` must equal `<version>`; a 200 alone proves only
that the old build is still up. A repo with no `release.json` compares the live
commit to `<sha>` instead - FleetApp's `https://www.fleetapp.app/api/version`
reports it, and that equality is the whole proof step 4 shipped.

**8. Load the page and read the console.** Skipped when `has_site` is empty.
Open `https://<domain>/` in the in-app browser and read `read_console_messages`
and `read_network_requests`. Anything at error level, or a 4xx or 5xx on a
first-party request, is a failed release.

## Release the hold

Once every check passed, `SendMessage` the same peers: `Done with /merge on
<slug>. Version <version> is live. Hold lifted.` On failure, the same with
`Failed` and one sentence naming the step. Anything that landed on `develop`
during the run is not in this release and is `/pretrip`'s to report next time;
say the version is live and stop.

## When validation fails

Stop and report exactly which step failed and what it returned. Do not roll
`main` back, revert or redeploy: which of those comes next is my call.
Send the failure hold-lift before stopping - a stuck hold is its own outage.
