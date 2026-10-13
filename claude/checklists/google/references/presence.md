# Brand presence

## What the surface is

This is the result page somebody sees when they already know the brand and type its name.
It is the result the owner looks at, the one a client judges the work by, and the one that
decides whether a person who was already looking for the business finds it.

It is also the part of Google nobody sets. The site name, the favicon, the sitelinks, the
snippet and the knowledge panel are all chosen by Google from signals it weighs itself.
Every gate here works by making the signals consistent and unambiguous, then rechecking.
A gate reported as fixed the day a signal changed is a guess.

## The brand query

A search for the business name should return the site first. When it does not, the usual
causes are:

- The home page is not indexed, or Google chose a different canonical for it.
- A directory listing, a marketplace profile or a social page carries stronger signals for
  the name.
- The brand name is a common word, and the site competes with the generic meaning.
- A retired domain or a former trading name still ranks and takes the query.

Losing a brand query is the most expensive ranking failure a small site can have, because
the traffic it costs was already decided to visit.

## The site name

The string above the result, in place of the domain. Google picks it from four signals and
settles on whichever is consistent:

- The `WebSite` structured data node's `name` and `alternateName`.
- The home page `<title>`.
- `og:site_name`.
- The heading and logo text in the header.

Four different spellings across those four places is why a site shows its domain, a
tagline, or a truncated page title instead of its name. Making all four identical is the
whole fix, and the markup half belongs to `/seo-checklist`.

The change takes effect on a recrawl and Google applies it at its own discretion. A site
name request cannot be filed, and there is no report saying which signal was used.

## The favicon

The icon beside the result. Google's requirements are modest and the failure rate is high:

- A square image, at least 48 pixels, ideally a multiple of 48.
- Referenced from the home page with a `rel` of `icon`, `shortcut icon` or `apple-touch-
  icon`.
- Served from a URL that is crawlable, so not blocked in robots.txt and not behind
  authentication.
- Stable at that URL, since Google caches it and refreshes it on its own schedule.

A missing or rejected favicon leaves a generic globe, which is the single clearest signal
in a search result that nobody is looking after the site. Refresh after a fix takes days to
weeks and cannot be requested.

## Sitelinks

The links Google draws under the main result. They are generated from internal linking,
site structure and what people click, and they cannot be chosen. A demotion tool existed
and was withdrawn.

What weak sitelinks say: the navigation buries what matters, or the pages that ought to
appear are linked from too few places. What to look for is a sitelink pointing at a login
screen, a legal page, a dead route or a filter URL, which means those pages are more
internally linked than the ones that should be there.

The fix is structural. Strengthen the internal links to the page that belongs there,
reduce the links to the one that does not, and repair the dead route. It resolves over
weeks.

## The snippet

Google writes the description under the result and only sometimes uses the meta
description. It quotes whatever part of the page best answers the query, which on a brand
query is whatever best says what the business is.

A snippet built out of navigation labels means the home page has no prose worth quoting.
The fix is a plain sentence near the top of the page, in real text rather than in an image
or a heading, stating what the business is and who it serves. The copy belongs to
`/seo-checklist`.

## The knowledge panel

The block on the right of a desktop result, or above the results on a phone. It exists for
entities Google is confident about, which for a business means a Business Profile, an
`Organization` or `LocalBusiness` node with `sameAs` links, and corroboration from sources
Google trusts.

The facts in it come from everywhere, not from the site. A wrong founding year, an old
logo or a former parent company is something Google read elsewhere and preferred. Correcting
it means correcting the source, then submitting feedback on the panel.

A panel can be claimed through entity verification, which gives the business the ability to
suggest edits directly and see them applied faster. Claiming needs a verified presence on
one of the profiles the panel links to.

Whether a business qualifies for a panel at all is Google's decision and there is no
application.

## Contact details

The phone number, address and hours Google shows come from the Business Profile and from
the aggregators feeding it, reconciled against the site. When they disagree with the
contact page, the visitor calls a number that is answered by nobody and never reaches the
site to find the right one.

Correcting the profile is the first step and rarely the last, because Google reconciles
across sources and a stale directory entry keeps reintroducing the old value.

## Legacy properties

A previous domain, a staging host or a former trading name left indexed splits the brand's
signals and sends people to a site nobody maintains. The route out is a permanent redirect
from every retired URL to its match on the live site, kept in place indefinitely rather
than for a migration window, and the new URLs submitted for indexing.

Deleting the old domain instead of redirecting it throws away every link pointing at it.

## Timing

| Change | Before it shows |
| --- | --- |
| Site name signals corrected | days to weeks, at Google's discretion |
| Favicon replaced | days to weeks |
| Sitelinks reshaped | weeks |
| Snippet rewritten | days, after a recrawl |
| Knowledge panel fact corrected at source | weeks, sometimes longer |
| Business Profile edit | minutes to days |
| Retired domain redirected | weeks for the index to consolidate |

## What is outside anyone's control

Whether a knowledge panel exists. Which sitelinks are drawn. Which snippet is quoted for
any given query. Whether the site name Google chose is the one that was signalled. Whether
a competitor bidding on the brand name appears above the organic result, which is an ad
auction and not a search result at all. The work here is to remove every ambiguity in the
signals and record what the result page shows afterwards.
