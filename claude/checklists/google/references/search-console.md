# Search Console

## What the surface is

Search Console is the only place Google reports back on a site it crawls. Everything
else in this checklist is either a public API or a dashboard for a separate product;
this one holds the crawl record, the index record, the enhancement reports, the manual
actions, the security issues, the link graph and the query data. A site without a
verified property is a site nobody can diagnose, which is why property verification is
the first gate rather than a setup step.

## Property types, and why domain properties win

Two kinds exist:

- **URL prefix.** Covers exactly one scheme and host, so `https://www.example.com/` and
  `https://example.com/` are separate properties with separate data.
- **Domain.** Covers every subdomain and both schemes under one record, verified by a DNS
  TXT record.

A site verified only as a URL prefix reports on a fraction of itself, and the fraction is
usually the one nobody visits. The `www` prefix property of a site that redirects to the
apex shows almost no data, which reads as a traffic collapse rather than as a
misconfigured property. Every site here gets a domain property, and the prefix properties
stay only where a legacy report needs them.

DNS verification needs access to the registrar. Where that is not available for a client
site, an HTML file or a meta tag verifies a prefix property, and the run records that the
coverage is partial.

## The API

`searchconsole.googleapis.com`, v1 for most of it, with a separate v1 URL Inspection
endpoint.

What it gives:

- `sites.list` and `sites.get` for which properties the credential can see.
- `searchanalytics.query` for clicks, impressions, position and CTR, dimensioned by query,
  page, country, device and date.
- `sitemaps.list`, `sitemaps.get` and `sitemaps.submit`.
- `urlInspection.index.inspect` for one URL at a time: its index status, the canonical
  Google chose, the crawl time, the referring sitemaps, the mobile usability result and
  the rich result types detected.

What it does not give, and what has to come from the dashboard:

- The manual actions report and the security issues report have no API. Reading them is
  an attestation or a scrape of the interface.
- Index coverage counts by reason are not exposed. The URL Inspection endpoint answers
  per URL, so a coverage picture is assembled URL by URL rather than read whole.
- The links report exports only from the interface.
- Reconsideration requests are submitted by hand.

## Rate limits that shape how a run is written

URL Inspection is capped at 2,000 queries per day and 600 per minute per property. A run
across sixteen sites cannot inspect every URL of every site, so inspection is spent on
representatives: one URL per template, plus anything a sitemap or a report flagged. Search
Analytics is capped at 1,200 queries per minute and returns at most 25,000 rows per
request, paged with `startRow`.

## Data delays

Search Analytics data lags by two to three days, and the most recent day in a response is
usually incomplete. A comparison drawn against yesterday shows a decline that is an
artifact of the lag. The 16-month window is the full history the API holds; anything older
has to have been exported at the time.

The reports themselves refresh on their own schedules. Index coverage updates daily but
the changes it reports may be a week old, because the underlying crawl happened earlier.
Enhancement reports lag furthest, since an item only enters them after a recrawl.

## Common failures

**The property is verified to somebody who left.** Verification survives, but the person
holding the owner role is gone and nobody can add users. The fix is a second owner on
every property before it is needed, verified by DNS rather than by a file in a repository
somebody may redeploy over.

**Only the redirecting host is verified.** Data looks like it collapsed. Nothing is wrong
with the site.

**A sitemap was submitted once, by hand, years ago.** It is still listed, still fetched,
and still points at a URL that now 404s. The report shows a fetch error nobody reads.

**The wrong canonical.** URL Inspection reports the canonical Google chose separately from
the one the page declares. When they differ, Google preferred another signal, and the page
declaring the canonical is not the one ranking.

**Query data disappears for a client site.** Nearly always a property that was deleted
during a domain migration and recreated, which starts the 16-month window over.

## What is outside anyone's control

Google decides which queries are shown at all: anonymised queries are withheld entirely,
so clicks and impressions in the totals exceed the sum of the rows. Position is an average
across impressions and cannot be reconciled with any single observed ranking. The
enhancement reports appear and disappear as Google adds and retires result types, and a
report vanishing is not a signal about the site.
