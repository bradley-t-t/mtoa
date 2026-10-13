# Structure and Content

Structure is the only part of a page that survives being stripped of its styling,
which is the form every machine reader sees it in — a crawler, a screen reader, and
an agent working from the accessibility tree.

## Headings

One `h1` per route, naming what the page is. Several leave nothing stating the
subject; none leaves the outline with no root.

Levels descend one at a time. An `h2` followed by an `h4` tells a machine reader a
section is missing, and the outline it builds is wrong in a way the rendered page
never reveals. Heading level is structure; size is a stylesheet decision.

Phrase the headings people actually search as the question they would type. A heading
matching the query is the string a generative answer quotes, and a page of noun-phrase
headings matches none of them.

## Landmarks and controls

`main`, `nav`, `header`, `footer` as real elements. A page built entirely from `div`
gives the accessibility tree nothing to orient on.

Clicks bind to `button` and `a`. A clickable `div` is unreachable by keyboard and
invisible to an agent reading the tree — it looks like text that happens to move when
touched. Both keep whatever styling they already had.

## Links

Anchor text is what the destination ranks for, so a site of `read more` links passes
its signal to nothing. Name the destination's subject.

| Page type | Internal links |
| --- | --- |
| Blog post, 1,500+ words | 5 to 10 |
| Service page | 3 to 5 |
| Category page | every child |
| Product page | 2 to 4 |

Vary the anchor text rather than repeating one exact-match phrase, and leave no route
linked from nowhere.

## Word counts

Floors by type, below which a page competes with pages that are not thin:

| Page type | Words | Unique |
| --- | --- | --- |
| Homepage | 500 | 100% |
| Service or feature page | 800 | 100% |
| Blog post | 1,500 | 100% |
| Location page, primary | 600 | 60%+ |
| Location page, secondary | 500 | 40%+ |
| Product page | 400 | 80%+ |
| Category page | 400 | 100% |
| About | 400 | 100% |
| Landing page | 600 | 100% |

A route that renders its copy from data carries none of its own. That is not thin
content; it is a shell, and it is answered `n/a` naming the data source.

## Programmatic pages

Safe at scale when each page carries something real: integration docs, template and
tool pages, glossary entries over 200 words, product pages with their own specs,
user-generated profiles.

Risky: location pages with only the city swapped, `best X for Y` grids, competitor
alternative pages carrying no comparison of substance, and bulk generated text.
Google's doorway-page handling is aimed exactly at the first of those.

Thresholds worth holding: warn at 30 location pages and require 60% unique content per
page; stop at 50 and require a real business presence behind each one.

## Credibility

- A named author, linked to a page saying who they are.
- Published and last-updated dates rendered on the page, mirrored into `datePublished`
  and `dateModified`.
- Claims attributed to a source, and first-hand detail that could only come from
  having done the thing.

## The opening

Roughly 44% of AI citations come from the first 30% of a page, and the passages that
get quoted run 134 to 167 words. State the answer in the opening lines and let the
detail follow. A buried conclusion is not quoted, however good it is.

Self-contained blocks travel: a paragraph that reads correctly with no surrounding
context is one that can be lifted into an answer. Definitions phrased as `X is` or
`X refers to` are the shape that gets picked up most.
