# Images and Loading

## The three metrics that feed ranking

| Metric | Good | Needs work | Poor |
| --- | --- | --- | --- |
| LCP, largest contentful paint | <= 2.5s | 2.5 to 4.0s | > 4.0s |
| INP, interaction to next paint | <= 200ms | 200 to 500ms | > 500ms |
| CLS, cumulative layout shift | <= 0.1 | 0.1 to 0.25 | > 0.25 |

Measured on real visitors at the 75th percentile, from the Chrome User Experience
Report, at both page and origin level. Lab tools are for debugging; field data is what
ranks.

INP replaced FID in March 2024 and FID left Chrome's field tools in September 2024. Do
not reference FID.

These are a tiebreaker rather than a primary signal — they matter most when content
quality between competitors is close. The thresholds have not moved since they were
defined. There is no Visual Stability Index, no Core Web Vitals 2.0, no Engagement
Reliability metric, and LCP was never lowered to 2.0s; those appear in third-party
blogs and are contradicted by web.dev and the CrUX release notes.

LCP breaks into four subparts, which is how you find out which phase is the problem:
time to first byte (target under 800ms), resource load delay, resource load time, and
element render delay.

## Images

Every image element carries `alt`. A decorative image takes `alt=""`, which says it is
decorative; a missing attribute says nothing at all. Alt text runs 10 to 125
characters and describes what is in the picture — a filename, the word `image`, or a
run of keywords all pass a presence check and describe nothing.

Every image sets `width` and `height`, or an aspect ratio. An image without dimensions
is the most common cause of layout shift, and layout shift is one of the three metrics.

Flag raster files over 200KB and treat over 500KB as urgent. WebP or AVIF at the same
visual quality is typically a third the bytes.

`loading="lazy"` below the fold, and never on the hero. Deferring the largest paint
delays the metric it is measured by, which is the opposite of the point. Give the hero
`fetchpriority="high"` or the framework's priority flag, and preload it.

When a JavaScript lazy-loader is in play — Perfmatters, EWWW, lazysizes — it strips the
native attribute and uses `data-src` placeholders on purpose. That is not a missing
lazy attribute.

## Loading

- Every external script carries `defer`, `async`, or `type="module"`. A blocking
  script in the head stops parsing for a whole round trip while the reader looks at
  nothing.
- Every web font sets `font-display`. Without it, text is invisible until the font
  arrives, so the paint that gets measured waited for a download nobody needed.
- Preconnect the third-party origins on the critical path. Each cold origin costs DNS,
  TCP and TLS before the first byte of whatever it was fetching.
- No `document.write`, and no markup assembled into `innerHTML` at runtime. Content
  arriving after layout moves everything below it.

## Page experience beyond the metrics

HTTPS is confirmed but light, affecting well under 1% of queries. Security headers are
worth having and are not a ranking lever — do not over-weight them.

What does bite:

- **Intrusive interstitials.** Full-page overlays, standalone consent-redirect pages,
  persistent blocking dialogs, and distracting ad density. Small banners and standard
  legal dialogs are fine.
- **Back-button hijacking.** Added to Google's spam policies in April 2026 and enforced
  since June 2026 with manual actions and automated demotions. One press of Back leaves
  the site, including when the culprit is a third-party ad script.
- **Content behind interaction.** Key content stays visible on load rather than hidden
  behind tabs or accordions; content that needs a click to appear is less likely to
  qualify for anything.
- **Mobile and desktop parity.** Googlebot Smartphone is the primary crawler. A mobile
  version is not strictly required, but the primary content, titles, descriptions,
  robots directives and structured data have to match across the two. The risk is
  losing content, not being excluded.

Touch targets 48x48px with 8px between them, 16px base font, no horizontal scroll.

## Single-page apps

Soft navigations — URL changes with no full page load — are still a measurement blind
spot. The Soft Navigations API is in its final origin trial through Chrome 147 to 149,
targeting an unflagged ship around Chrome 151, with no ranking impact yet. An SPA's
route changes are largely unmeasured today, which is worth knowing before trusting a
clean CrUX report on one.
