# Measuring

## The two numbers

**Lab.** Lighthouse, run once, on simulated hardware and a simulated connection. It is
a controlled experiment: reproducible in principle, comparable to itself, and nobody's
actual experience. Its score is weighted heavily toward loading, so a page that loads
fast and answers slowly scores well.

**Field.** The Chrome UX Report: real Chrome visits to the URL over the previous 28
days, reported at the 75th percentile. It is what the site is, and it is what search
uses. A URL with too little traffic has no record of its own and inherits the origin's.

They disagree constantly, and the field record wins every disagreement.

## The thresholds

| Metric | Good | Poor above | What it is |
| --- | --- | --- | --- |
| LCP | 2.5s | 4.0s | when the largest element in the viewport finished painting |
| INP | 200ms | 500ms | the worst delay between an interaction and the next paint |
| CLS | 0.1 | 0.25 | how much content moved without the reader causing it |
| FCP | 1.8s | 3.0s | when anything at all appeared |
| TTFB | 800ms | 1.8s | when the first byte of the document arrived |

TBT and Speed Index are lab-only. TBT is the lab's proxy for INP and correlates
loosely: a page with a TBT of zero can still have an INP in the hundreds, because the
blocking that matters happens on interaction rather than on load.

## Lab variance is wider than most improvements

Three consecutive PSI runs against the same unchanged page can return 36, 64 and 72.
That range is larger than most of what this checklist produces, so a single before and
a single after support no claim at all.

Every measurement here is a median of three or more:

```bash
scripts/psi.py measure --url https://<host> --runs 3 --out before.json
```

The record it writes carries the spread as well as the median, so a comparison drawn
across a range that wide is visible rather than implied.

## Measure the host people reach

A host that redirects puts the redirect inside every number taken against it. On one
site here, measuring `www.` rather than the apex cost half a second of FCP and three
points of score, none of which had anything to do with the page.

The check before measuring:

```bash
curl -s -o /dev/null -w "%{http_code} %{num_redirects} -> %{url_effective}\n" https://<host>
```

The home page is also rarely the page carrying the traffic, and rarely the slowest:

```bash
scripts/psi.py routes --url https://<host> --path / --path /pricing --path /contact --out runs/
```

## Before the site is deployed

PSI can only read what is live, which is the wrong end of the loop when the point is to
fix the page before it ships. `scripts/lighthouse.py` runs the same engine locally
against a preview server:

```bash
npx --yes serve -s dist -l 4173
scripts/lighthouse.py measure --url http://localhost:4173 --runs 3 --out after.json
```

Local numbers are harsher than PSI's and are not comparable to them: different
hardware, and no network simulation of the same shape. They are comparable to each
other, which is what a before and after needs. A run that measures locally does so for
both ends, and MSR-01 refuses a mixed pair.

## The limits of the field record

It is a 28-day trailing window, so a fix landing today does not appear in it for weeks.
And it never attributes: it reports INP at 706ms without naming the interaction that
caused it. Attribution comes from recording real interactions in the field with the
`web-vitals` library and its attribution build, which names the element and the script.

The lab is where causes are visible and the field is where the truth is. A run uses
both: the field to choose what to work on, the lab to confirm the change did what it
was meant to.

## Budgets

A number that improved once regresses quietly. `perf-pass.py budget --write` records
the current weights as a ceiling and keeps the lower of the existing and the new one,
so recording after a regression cannot raise it. Wired into CI, the next change that
adds a megabyte is caught by the build rather than by somebody noticing.
