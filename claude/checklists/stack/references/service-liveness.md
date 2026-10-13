# Service liveness

Source describes intent. A request describes the world. Everything in the SVC group
exists because those two drift apart silently, and the drift only shows up in front of
a visitor.

## Supabase

A free-tier project **pauses on its own** after about a week without traffic. A paused
project fails every request, and the failure looks the same as a bad key, so the site
looks broken and the code looks wrong. Probe `/rest/v1/` with the publishable key: 200
means awake and the key belongs to this project; 401 means the key is for a different
one; no answer at all is usually the pause.

**RLS is the whole security model.** The publishable key ships to every visitor's
browser by design, so a table without row level security is a table the internet can
read, and usually write. Enabling RLS with no policy denies everything, which is the
safe end to start from. The check runs against `pg_class.relrowsecurity` through the
management API, because the dashboard's own view is the one that gets skimmed.

**Migrations drift the other way.** A schema changed by hand in the dashboard leaves
`supabase/migrations` describing a database that no longer exists, and the next
environment built from those files is a different database. Capture the hand change as
a migration rather than deleting the evidence.

**A function that was never deployed** fails only on the path that calls it, which is
usually the path nobody tests. The repository directory and the deployed list have to
agree in both directions.

## Clerk

The failure that hides is **development keys in production**. Sign-in succeeds,
sessions issue, everything looks correct, and the users land in the development
instance. `pk_test`/`sk_test` anywhere near production is the finding.

The second is **the deployed domain missing from the allowed origins or the redirect
URLs**. The redirect back from sign-in breaks, and it breaks only in production, where
nobody was testing. Add the domain with and without `www` as the site actually serves
them.

An unregistered webhook endpoint receives nothing; an unsigned one accepts anything
that posts to it. Both are silent.

## Stripe

Key mode is the whole gate. A test key in production takes payments that never arrive.
A live key in a preview deployment takes real money from whoever clicked through a
branch. Neither errors.

A **webhook that is not registered** means payment succeeds and the application never
learns, so the order is paid for and never fulfilled. A **stale price id** fails at the
checkout call, which is the last place a visitor is willing to meet an error. Read
prices from the environment rather than pinning them in source when they differ between
modes.

## Vercel

`vercel.current` is the one worth reading twice. A promotion that merged and never
built leaves a green pull request and an unchanged site — a release has already been
reported finished in exactly that state. Compare the production deployment's commit
against the tip of `main`.

Where a site is hosted from a second repository rather than the one work lands in, a
promotion that stops at `origin` finishes green and changes nothing a visitor sees.
Pushing `main` to the deploy remote is what closes that gap, and `/merge` does it as a step.

Deployment protection has no correct default: left on it hides a public site behind a
login wall, turned off it publishes an internal tool. The gate is that somebody chose.
