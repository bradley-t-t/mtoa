# Core Web Vitals

## This group diagnoses, it does not optimise

Every speed fix belongs to `/perf-checklist`. That skill owns the files, the before-and-
after capture, and the proof that the page still renders as it did. This group owns one
question: what does Google's field record say, and which template is failing it.

A run that starts editing images here has taken work away from the checklist that verifies
it. The output is a handover naming the failing URL group, the metric, the measured value
and the form factor.

## Field data is the diagnosis

The Chrome UX Report is real Chrome visits to the URL over the previous 28 days, reported
at the 75th percentile. It is what search uses, and it is the only measurement that
describes the site as visitors experience it.

Lab data is one simulated device on one simulated connection. Three consecutive runs
against an unchanged page can return scores 30 points apart, which is wider than most
improvements. The lab is where causes are visible; the field is where the truth is.

When they disagree, the field record wins.

## The thresholds

| Metric | Good | Poor above | What it measures |
| --- | --- | --- | --- |
| LCP | 2.5s | 4.0s | when the largest element in the viewport finished painting |
| INP | 200ms | 500ms | the worst interaction delay in the visit |
| CLS | 0.1 | 0.25 | how much content moved without the reader causing it |
| TTFB | 800ms | 1.8s | when the first byte arrived |
| FCP | 1.8s | 3.0s | when anything appeared |

Passing means all three of LCP, INP and CLS are in the good band at the 75th percentile.
Three visitors in four had an experience at least this good, which is also the statement
that one in four did not.

## Mobile and desktop are separate records

CrUX collects them separately and they routinely differ by a factor of two. A site whose
desktop record passes and whose phone record fails is a failing site, because phone is
where the traffic is. Every metric is read twice, once per form factor, and the worse
reading is the verdict.

## Where the numbers come from

**PageSpeed Insights API** (`pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed`)
returns both the CrUX field record and a lab run in one response, keyed by
`strategy=mobile` or `strategy=desktop`. The `loadingExperience` block is the URL's field
record; `originLoadingExperience` is the origin's. An API key raises the quota well above
the unkeyed limit, and the key for this work lives in CryptoFort as
`google-pagespeed-api-key`, pulled at the point of use and never written to a file.

**CrUX API** (`chromeuxreport.googleapis.com`) returns the field record on its own, with
full histogram distributions rather than only the percentile, and supports origin-level
queries directly. Faster than PSI when no lab run is wanted.

**Search Console Core Web Vitals report** groups URLs by template and reports how many URLs
fall in each band per group. This is the report that answers "which page type is the
problem", which is the unit a fix actually applies to. It has no API.

## URLs without field data

CrUX has a traffic threshold. Below it, a URL has no record of its own and the response
either falls back to the origin or returns nothing. A missing record is an unknown, and an
unknown recorded as a pass is a failure nobody will look at again.

The fallback is a lab run, taken three times with the median kept, and recorded as
lab-derived so it cannot be mistaken later for a field reading. Lab numbers are harsher
than field numbers and are not comparable to them.

## The 28-day window

This is the single most important timing fact in the checklist. CrUX is a trailing 28-day
window, so:

- A fix landing today is invisible in the field record for weeks.
- A record improving today reflects changes made a month ago.
- A gate cannot be closed as fixed on the strength of a lab improvement.

The honest close is to record the fix, the landing date, and the date the field verdict
can be taken. Search Console's validation flow tracks the group in the meantime and is the
best available signal before the window turns over.

## The field record never attributes

It reports INP at 706ms and names no interaction. It reports LCP at 4.1s and names no
element. Attribution comes from elsewhere:

- The lab run in the PSI response names the LCP element and the long tasks for that one
  synthetic visit.
- The `web-vitals` library's attribution build records the element, the script and the
  event type from real sessions and reports them back to the site's own collector.

Diagnosing INP without attribution is guesswork, and INP is the metric where guesswork is
most expensive because the cause is an interaction nobody thought to test.

## Common failures

**Measuring the redirecting host.** A host that redirects puts the redirect inside every
number taken against it. Check the effective URL before recording a baseline.

**Measuring only the home page.** It is rarely the page carrying the traffic and rarely the
slowest. The origin record aggregates every page real visitors reached, which is why an
origin can fail while every URL somebody tested passes.

**Reading the lab score as the verdict.** The score is weighted toward loading, so a page
that loads fast and answers slowly scores in the nineties and fails INP in the field.

**Comparing a single before against a single after.** Lab variance is wider than the
improvement. Any lab comparison is a median of three at each end.

## What is outside anyone's control

The 28-day window and its refresh cadence. Whether a URL has enough traffic to earn a
record. The thresholds themselves, which Google has changed before and will again, and the
metric set, which replaced FID with INP in 2024. And the composition of the visitor sample,
which is Chrome users who opted into reporting, on their own devices and networks.
