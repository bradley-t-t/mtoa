# Observability

A site with no reporting is not a site with no errors. It is a site where the errors
are only ever seen by the person who left.

## Four places, one path

The snippet has to be in the **document the build actually serves** — added to a
template the build does not use, it reports nothing and reads as done. The collector on
the Pi has to know the site, or a report arriving from it is dropped rather than
attributed. The uptime monitor has to have a target, because reporting says what broke
while somebody was there and the monitor is what notices when nobody is. And the site
has to appear on the public status board, because being watched and being seen to be
watched are different things.

`/onboard-error-reporting` does all four in one pass.

## Why the round trip is a gate of its own

Every step above can be configured correctly and still not connect. The only proof the
path works is a report that made the trip: an error thrown in a real browser on the
deployed site, arriving in Discord, opening a ticket in the ticket queue
(`github.tickets` in the profile).

Nothing in the probe can manufacture one, so this gate is filed as failing with
instructions. Throw the error, follow it, and answer the gate with what arrived and
when. A site is not onboarded until one has.
