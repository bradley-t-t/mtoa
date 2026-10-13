# Rich results

## What the surface is

Structured data on a page is a claim. A rich result is Google accepting the claim and
drawing something extra in the search result: stars, a price, a breadcrumb trail, an
image, an event date. The gap between the two is where this group lives.

The SEO checklist can prove the markup is valid JSON-LD carrying the properties Google
documents. It cannot prove Google parsed it, that the item entered the enhancement reports
clean, that the result type is one Google still draws, or that the item was not quietly
dropped for an image two pixels too small. Those are answers only Google gives, about a
live URL.

## What settles a question here

**The Rich Results Test** (`search.google.com/test/rich-results`) renders the URL the way
Google renders it and reports the items it detected, the result types they are eligible
for, and the errors and warnings per item. It renders JavaScript, which is what makes it
the authority over reading the source.

**The URL Inspection API** returns a `richResultsResult` block per inspected URL, with the
detected item types and their issue severity. This is the API path, and it is subject to
the 2,000-per-day inspection cap, so it is spent on one URL per template.

**The Schema Markup Validator** (`validator.schema.org`) checks the vocabulary rather than
Google's requirements. It reports valid on markup Google will not use. Useful for syntax,
never for eligibility.

**The enhancement reports** in Search Console are the property-wide view, grouped by type,
with counts of invalid, valid-with-warning and valid items. They have no API. Their value
is that they cover every URL Google has crawled rather than the ones somebody thought to
test.

## Eligible, valid and rendered are three different things

- **Valid** means the item parsed and carries the required properties.
- **Eligible** means the item could produce a rich result.
- **Rendered** means Google actually drew one for a real query.

The third never follows from the second. Google decides per query and per result whether
the enhancement is useful, and a site can be eligible for months without a single rich
result appearing. That is not a defect and no gate here treats it as one. The gates stop
at eligibility, which is the part that is controllable.

## Errors versus warnings

An error disqualifies the item completely. There is no partial result: an Article missing a
required property produces a plain blue link, exactly as if there were no markup.

A warning names a recommended property. The item stays eligible, and it competes against
items that supplied the recommendation. Warnings accumulate quietly because nothing
appears to break.

The important consequence: one bad template puts every URL built from it into the report
as a separate invalid item. A report showing 1,400 invalid items usually has one cause.

## Images

The requirement that catches most sites:

- At least 1,200 pixels wide.
- Crawlable, so not blocked by robots.txt and not behind a signed URL.
- Supplied in 16x9, 4x3 and 1x1 where the result type accepts several, listed as an array
  so Google picks.
- A real image URL, not a data URI and not a placeholder.

An image Google rejects drops the item to a plain result and reports nothing, so the
failure is invisible in the enhancement report as well as on the page.

## Types that were retired

Marking these up produces no result, validates cleanly, and reports as valid:

- **HowTo**, removed 2023.
- **FAQ**, restricted in 2023 to government and health sites, so removed in effect for
  everybody else.
- **SpecialAnnouncement**, **ClaimReview**, **EstimatedSalary**, **CourseInfo**,
  **LearningVideo** and **VehicleListing**, withdrawn at various points.

Keeping them costs page weight and maintenance and earns nothing. The rendered content
stays; only the markup goes.

## The types worth earning

| Type | What it draws | Who earns it |
| --- | --- | --- |
| BreadcrumbList | The trail in place of the URL | Any site with hierarchy |
| Product with Offer | Price, availability, review stars | Anything sold |
| LocalBusiness | Hours, address, phone in the panel | A business with a location |
| Article or BlogPosting | Headline, image, date | Editorial routes |
| Event | Date and venue | Anything ticketed or scheduled |
| Recipe, JobPosting, Video | Their own formats | Where the content genuinely is one |

## The self-serving review rule

A site may not mark up reviews of itself. `aggregateRating` and `review` on the site's own
`Organization` or `LocalBusiness` node earn no stars, and Google names the practice in its
policy documentation. Product reviews on product pages are legitimate; a five-star rating
on the company's own homepage markup is not.

## Timing

An item enters the enhancement reports after Google recrawls the URL, which is days to
weeks. The Rich Results Test answers immediately, which makes it the tool for confirming a
fix, and the report the tool for confirming coverage. Validate Fix in Search Console asks
Google to recheck the affected set and takes one to four weeks to complete.

## Manual actions

Structured data that describes content absent from the page is the most common cause of a
structured data manual action. The penalty removes rich results across the whole property
rather than for the offending item, it persists until a reconsideration request is
accepted, and hidden markup is treated more harshly than wrong markup. Nothing in this
group is worth that risk, which is why page parity is a blocker.

## What is outside anyone's control

Whether a rich result is drawn for any given query. Which result types exist at all, since
Google adds and removes them. How Google's renderer treats a page's JavaScript, which
differs from any local rendering. And the review cadence for the enhancement reports, which
cannot be requested for a single URL.
