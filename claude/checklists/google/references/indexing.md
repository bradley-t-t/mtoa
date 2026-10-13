# Indexing

## What the surface is

Crawling, indexing and ranking are three stages, and a page can be lost at any of them.
Crawling is Google fetching the URL. Indexing is Google deciding the fetched page is worth
storing. Ranking is everything after that. A page that is crawled but not indexed is the
most common failure on a small site, and it produces no error anywhere: the URL is simply
absent from results.

The repository owns whether a crawler is allowed in and whether the sitemap lists the
right routes. This checklist owns whether Google went, what it decided, and whether the
sitemap it holds is the one the site publishes.

## What settles an indexing question

`urlInspection.index.inspect` in the Search Console API is the authority for a single URL.
It returns:

- `coverageState`, the phrase the interface shows, such as "Submitted and indexed" or
  "Crawled - currently not indexed".
- `googleCanonical` and `userCanonical`. A difference between them means Google ignored
  the page's declared canonical.
- `lastCrawlTime`, `crawledAs` and `robotsTxtState`.
- `pageFetchState`, which distinguishes a soft 404 from a real one.
- `indexingState`, which reports a `noindex` directive Google actually saw.

A `site:` query is not a substitute. It is approximate, it is rate limited, and its counts
swing by thousands between refreshes on a site that has not changed.

## The verdicts and what each one means

| Coverage state | What happened | Where the fix lives |
| --- | --- | --- |
| Submitted and indexed | Nothing to do | - |
| Crawled - currently not indexed | Google fetched it and judged it not worth storing | The page's content, so seo-checklist |
| Discovered - currently not indexed | Google knows the URL and has not fetched it | Crawl budget, internal links, server speed |
| Duplicate, Google chose a different canonical | Two URLs served the same content | Canonicals and redirects |
| Excluded by noindex tag | A directive is doing exactly what it says | The template emitting it |
| Blocked by robots.txt | Never fetched | robots.txt, so seo-checklist |
| Soft 404 | A 200 response carrying an empty or error page | The route's error handling |
| Redirect error | A loop or a chain too long to follow | The redirect configuration |

"Crawled - currently not indexed" is a quality judgement rather than a technical fault,
and no amount of resubmission changes it. The page has to become worth indexing.

## Sitemaps

A sitemap is a hint about what exists, never an instruction to index. Its value is in
discovery on a site whose internal linking is weak, and in the fetch record Search Console
keeps: a sitemap listing 240 URLs with 12 indexed is a diagnosis in one line.

`sitemaps.submit` takes the sitemap URL and returns nothing useful; `sitemaps.get` returns
the last fetch time, any errors, and per-type counts. A sitemap submitted for a property
it does not belong to is silently ignored.

Rules that get broken often:

- Every URL in the sitemap is canonical, returns 200, and is not blocked. A sitemap
  listing redirects and 404s costs trust in the whole file.
- `lastmod` is honest or absent. A file that stamps every entry with the build date is
  telling Google nothing changed matters, and Google stops reading the field.
- One sitemap holds at most 50,000 URLs and 50MB uncompressed. Beyond that it is an index
  file pointing at children.
- The sitemap URL is stable. A path that changes between deployments leaves the old one
  registered and erroring.

## The Indexing API is narrower than it looks

`indexing.googleapis.com` accepts `URL_UPDATED` and `URL_DELETED` notifications, and
Google documents it for job posting and broadcast event pages only. Using it for ordinary
pages is common and mostly harmless, but it is not a supported route to faster indexing
and it carries a 200 requests per day default quota. The supported route for one URL is
Request Indexing in URL Inspection, capped at roughly 10 to 12 per property per day.

## Timing

Discovery of a new URL on an established site takes hours to a few days. Indexing after
discovery takes anywhere from hours to several weeks, and on a low-authority site the
upper end is normal. A requested index can land in an hour or never. Removal after a
`noindex` is usually faster than indexing, a few days, because Google has to recrawl to
see the directive.

None of that is a schedule anyone can hold Google to. A run records the request and the
date, and rechecks.

## Common failures

**A staging host is indexed.** A preview deployment with no `noindex` and no
authentication competes with production for its own content, and it usually wins on
duplicate detection because it was crawled first.

**The canonical points at a template's own URL through a variable that renders empty.**
Every page declares the same canonical, and Google indexes one of them.

**Pagination collapses.** Page two onward carries the canonical of page one, so every item
past the first page is uncrawlable through the listing.

**A JavaScript route renders nothing to a crawler.** Google renders JavaScript, but on its
own queue and its own delay, and a route whose content depends on a client fetch that
fails during rendering is indexed empty.

**Parameters multiply.** Filter and sort parameters generate thousands of near-identical
URLs, and crawl budget is spent on them rather than on the pages that matter.

## What is outside anyone's control

Google decides whether to index a page and does not explain the decision. Crawl budget is
allocated by its own weighting of site size, health and update frequency. A recrawl cannot
be scheduled, only requested. And an index that held a page for years can drop it with no
change on the site and no report anywhere.
