# AI Readers

Google's own position, published under Search Central: optimising for generative
search is still SEO. AEO and GEO are labels for the same work. Frame everything here
as fundamentals applied to a different surface, and where a community recommendation
contradicts Google's primary source, take Google's.

Google rejects several popular tactics outright as ineffective: `llms.txt` as a
ranking input, chunking content for retrieval, AI-rephrasing existing copy, and
mention-farming. What it says works is the same content quality test as always —
who made it, how, and why.

## Where the surfaces actually diverge

**Brand mentions outweigh backlinks.** Ahrefs' December 2025 study of 75,000 brands
found mentions correlate about three times more strongly with AI visibility than
backlinks do. YouTube mentions correlate strongest at roughly 0.737; domain rating
sits near 0.266.

**Platforms do not agree with each other.** Only about 11% of domains are cited by
both ChatGPT and Google AI Overviews for the same query. There is no single ranking to
optimise toward.

**Position matters less than passage quality.** 92% of AI Overview citations come from
top-ten pages, but 47% come from pages ranking below position five. The selection
logic is not the ranking logic.

**Front-loading decides what gets quoted.** Around 44% of citations come from the first
30% of a page, and the passages that travel run 134 to 167 words and read correctly
with no surrounding context.

**Multi-modal content is selected more often** — pages carrying images, video or
tables see substantially higher selection rates than walls of text.

## What that means for a page

- A direct answer in the first 40 to 60 words of each section.
- Question-shaped headings, matching how the query is typed.
- Short paragraphs, two to four sentences.
- Tables for anything comparative, lists for anything sequential.
- Specific figures with a named source, rather than general claims.
- Definitions phrased `X is` or `X refers to`.

## Agentic browsing

Agents read a site three ways: vision models over screenshots, raw DOM, and the
accessibility tree — the cleanest of the three. What the tree needs is what a screen
reader needs: real `button` and `a` elements rather than `div onclick`, labels
associated with their inputs, landmarks, and targets big enough to hit.

Lighthouse ships an Agentic Browsing category, default-on since 13.3.0 and Chrome 150.
It reports a pass ratio, X of N, not a 0-100 score. The PageSpeed REST API does not
expose it; run it from the Lighthouse CLI with
`--only-categories=agentic-browsing`, from DevTools, or the PSI web UI.

Treat these as opportunities rather than failures. Nothing here should gate a release.

## llms.txt

Google does not use it. It is a one-fetch map for the readers that do, and it costs a
file:

```
# Site Name

> One line on what this site is.

## Docs
- [Getting started](https://example.com/docs/start): what it covers
- [API reference](https://example.com/docs/api): what it covers
```

Title, a summary line, then linked sections with a short gloss each. Worth having,
never worth trading a ranking signal for.
