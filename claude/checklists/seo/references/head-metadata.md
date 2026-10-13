# Head Metadata

Four lines decide what a result looks like, and a person scanning results reads
nothing else. They are also the cheapest thing on this checklist to get right and the
most commonly left to a framework default.

## Title

30 to 60 characters. Under 30 wastes the most valuable line on the page; over 60 is
truncated mid-phrase and the end is never seen.

- Primary subject near the front, brand at the end.
- Unique per route. Two routes sharing a title are two results a person cannot tell
  apart, and two candidates the crawler weighs against each other for one query.

Good: `Emergency Plumbing in Austin | ABC Plumbing`
Bad: `Home`, or `ABC Plumbing - Plumbing - Plumber - Plumbing Services`

## Description

120 to 160 characters. It does not feed ranking; it decides the click. Without one the
result shows whatever fragment of body copy matched the query, which is rarely the
sentence worth showing.

Unique per route, for the same reason a title is. A description repeated across a site
describes the site, and the reader is choosing between pages.

## The rest of the shell

| Tag | Why |
| --- | --- |
| `lang` on `<html>` | Screen readers pick a voice from it; hreflang means nothing without it |
| `<meta charset="utf-8">` | First element in the head, or the browser guesses and mangles non-ASCII titles |
| `viewport` | Mobile is the primary crawler; without it a phone renders at desktop width |
| `robots` | States the indexing policy rather than inheriting whatever the host sends |

## Where these live per stack

A gate that reads only the route file reports every framework route as untitled,
because the metadata is code rather than markup.

| Stack | Where the head is |
| --- | --- |
| Plain HTML | `<head>` |
| Next.js App Router | `export const metadata` / `generateMetadata` in `page.tsx` or the nearest `layout.tsx` |
| Next.js Pages Router | `<Head>` from `next/head` |
| Astro | frontmatter plus the layout's `<head>` |
| SvelteKit | `<svelte:head>` |
| Nuxt / Vue | `useHead()` or `definePageMeta()` |
| React SPA | `react-helmet`, or nothing — which is the usual finding |

A React SPA with one `index.html` and seven routes has one title. That is the defect,
not a false positive: the routes are indexed separately and described identically.

## Share cards

`og:title`, `og:description`, `og:image`, `og:url`, `og:type` on every route, plus
`twitter:card`. A partial set is worse than none — the card renders with a hole where
the image should be.

`og:image` must be an absolute URL. The card is built by a service fetching the tag
away from the page, so a relative path resolves against nothing and the image is
dropped silently. Build it from the site origin rather than writing a path.

1200x630 is the size that survives every crop.
