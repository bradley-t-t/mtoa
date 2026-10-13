# Auth wiring

Every failure in this group looks identical to working software from the outside, right
up until somebody changes a URL by hand.

## The matcher

Where something serves the request — Next, Nuxt, SvelteKit, Remix — the middleware
matcher is what puts the check in the path. A matcher written when the application had
four routes silently stops covering the nine added since, and the uncovered ones serve
to anybody. Inverting it, so it matches everything and lists the public exceptions, is
the shape that does not rot.

A static bundle has no request to intercept. There the row policies are the enforcement
and the matcher gate does not apply.

## getSession is not a trust decision

`getSession()` returns what the cookie says **without verifying it**, so any
authorization decision made on it is a decision made on user input. It is fine for
knowing whether to render a signed-in shell. `getClaims()` and `getUser()` verify.

## A filter is not a policy

`.eq('user_id', me)` sent from the client is a suggestion. The key is in the browser,
so the same request can be made again without the filter. The policy goes in the
database; once it is there the query stops needing the filter that was pretending to be
one.

## The service role key

It bypasses every policy in the database by design — that is the whole reason it
exists, and the whole reason it cannot ship. It belongs behind an API route or an edge
function. A migration granting to the postgres role of the same name is the policy
working, and is not this.

Anything that has ever been built into a client bundle is rotated, not just moved.
