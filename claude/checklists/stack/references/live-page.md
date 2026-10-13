# The live page

Source cannot answer any of this. Every gate here fails on a build that compiled, a
deploy that succeeded and a page that looks right.

**It answers.** The production URL returns the site over HTTPS, and the non-canonical
host redirects rather than serving a second copy of everything.

**The console is clean.** An error on load is a code path that already failed; the
visitor sees whatever the page was going to render after it. An error thrown by a third
party is still an error the visitor's browser reports.

**Nothing it asks for is missing.** A font, an image or a chunk returning 404 is
invisible in a build log and obvious on the page. Note that a `preconnect` or
`dns-prefetch` names an origin to open a socket to, not a file to fetch — its origin
answering 404 is the normal case. And an edge function answers 405 to HEAD and 200 to
GET, so a probe that does not retry with GET reports working things as broken.

**Every third party answers.** One that stopped serving takes a piece of the page with
it, long after anybody was looking. Remove it or self-host it.

How *much* any of it weighs, and how long the page takes because of it, is
`/perf-checklist`. This gate only asks whether it arrives at all.
