# Versioning

## CalVer, and why

Every project here is a service that ships continuously, not a library somebody depends
on a major of. Nobody asks whether a breaking change landed between two deployments of
a website; they ask when a build is from. CalVer answers that in the number itself.

`2026.35.2` — year, ISO week, and a count of the releases inside that week, which
resets as the week rolls over. `bump-version.py` writes it, using the project's own
release tool where one exists (`scripts/calver.js` on clientthree, which also writes
`public/release.json`) and falling back to one step otherwise.

## The three copies

`package.json`, `public/release.json` and the README badge all carry the version, and
each is written by a different step. They drift apart quietly, and the application ends
up reporting a version older than the manifest it shipped with — which is the exact
failure the tool exists to prevent.

Never hand-bump a manifest a project has a tool for. A hand-bump into `package.json`
that leaves `release.json` behind is the drift, one step before it becomes visible.

## Moving once

Every pull request into `develop` moves the version on, once, in the same commit as the
change. The standard `ci.yml` already refuses a pull request whose version is not
strictly ahead of develop's, and `version-bump-guard.sh` refuses `gh pr create` locally
on the same test. Promotions into `main` inherit develop's version and are not bumped
again.
