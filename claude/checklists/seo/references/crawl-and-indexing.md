# Crawlability and Indexing

Everything else on this checklist is downstream of whether a crawler arrives and is
allowed to stay. A site with perfect markup and a stray `Disallow: /` ranks for
nothing, and nothing on the page says so.

## robots.txt

Served from the site root. It carries the crawl policy and, more importantly, the
only discovery path that works without anybody submitting anything:

```
Sitemap: https://example.com/sitemap.xml
```

The `Sitemap` line must be an absolute URL. Relative paths are ignored.

A `Disallow` is not a way to keep a page out of the index. It stops the crawl, which
means the page can still be indexed from links pointing at it, now without any of its
own content to describe it. To keep a page out of the index, let it be crawled and
serve `noindex`.

Crawl rate adjusts itself and backs off on 5xx and slow responses. There is no manual
crawl-rate control — the Search Console setting was removed in January 2024. Crawling
is influenced through sitemaps, server responsiveness, and robots directives, and
through nothing else.

Google's crawling and robots reference lives at `developers.google.com/crawling` since
November 2025; the IP-range files moved to `/crawling/ipranges/` and `googlebot.json`
became `common-crawlers.json`.

## AI crawlers

| Crawler | Company | Purpose |
| --- | --- | --- |
| GPTBot | OpenAI | Model training |
| ChatGPT-User | OpenAI | Browsing on a person's request |
| ClaudeBot | Anthropic | Model training |
| PerplexityBot | Perplexity | Index and training |
| Bytespider | ByteDance | Model training |
| Google-Extended | Google | Gemini training, not Search |
| CCBot | Common Crawl | Open dataset |
| Applebot-Extended | Apple | Model training |

Two distinctions decide most of these:

- Blocking `Google-Extended` stops training use. It does not affect Google Search
  indexing or AI Overviews, both of which run on `Googlebot`.
- Blocking `GPTBot` stops training. It does not stop ChatGPT citing the site, which
  happens through `ChatGPT-User`.

User-triggered fetchers ignore robots.txt by design. Google documents `Google-Agent`,
`Google-NotebookLM` and `Google Messages` as fetchers a person invokes, and none of
them can be blocked from robots.txt. Server-side access control is the only lever.

Around three to five percent of sites carry AI-specific rules. The gate here is not
that they be blocked — it is that the choice was made, because silence hands it to
each company separately.

## Canonicals

Every route names its own canonical URL. Without one, a query string, a trailing
slash, an uppercase path and a www variant are four pages competing for one ranking,
and the one that wins is chosen for you.

Serve the canonical in the initial HTML. Google's December 2025 JavaScript guidance is
explicit: when a canonical in the raw HTML differs from one injected by script, either
may be used. The same holds for `noindex` — if the raw HTML carries it and script
removes it, the raw one may still be honoured.

Google does not render JavaScript on responses with a non-200 status. Anything a
script injects on an error page is invisible.

## Fetch limits

Googlebot reads the first 2MB of an HTML response and the first 64MB of a PDF. This is
long-standing rather than new, and it bites when inline base64 images, oversized inline
CSS, or a bloated navigation push the body copy and the JSON-LD past the cap. Content
past 2MB is not indexed, and nothing reports that it was dropped.

## URL shape

Lowercase, hyphenated, no query parameters for content. URLs are case-sensitive, so
`/Blog` and `/blog` are two pages; an underscore does not separate words for a crawler,
so `blog_posts` is one token and `blog-posts` is two.

Redirects run one hop. A chain costs crawl budget and loses signal at each step.

## Sitemaps

Either a committed `sitemap.xml` or a generator wired into the build. A sitemap is how
routes nothing links to are discovered at all, and it is the only place `lastmod`
carries.

`IndexNow` is worth wiring for Bing, Yandex and Naver. Google does not use it.
