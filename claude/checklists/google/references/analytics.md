# Analytics

## What the surface is

GA4 is where a site's own record of its visitors lives, and it is the one Google property
that silently produces plausible numbers when it is wired up wrong. A broken tag reports
zero, which somebody notices. A tag firing twice, a tag on the wrong property, a
cross-domain journey counted as two visits, or a conversion event that never fires all
report something, and the something looks like a result.

The measurement itself is the deliverable here. A site whose analytics cannot be trusted
gives no basis for any other decision in this checklist.

## The pieces

- **Property.** The container. One per site, with a numeric property ID.
- **Data stream.** One per platform, carrying the measurement ID, the `G-` string the page
  loads.
- **Events.** Everything is an event. Page views, scrolls, clicks and purchases share one
  shape.
- **Key events.** What used to be called conversions. Marking an event as one is what
  makes it available to Ads.
- **Google tag or GTM.** The tag on the page. Either is fine; both at once is not.

## The APIs

Two, and they answer different questions.

**Admin API** (`analyticsadmin.googleapis.com/v1beta`) for configuration: list properties
and streams, read and set data retention, read the Google Ads links, read key event
definitions. This is what settles almost every gate in this group, because the gates are
about configuration rather than about numbers.

**Data API** (`analyticsdata.googleapis.com/v1beta`) for reporting: `runReport` for
dimensions and metrics over a date range, `runRealtimeReport` for the last 30 minutes.
Realtime is how a tag is confirmed to be firing without waiting a day.

Neither API can tell you the tag is on every page. That comes from fetching the pages and
looking for the measurement ID, or from comparing the page view count against the route
list.

## Settings that are wrong by default

**Data retention is two months.** The default on every new property, and it silently
discards anything older for exploration reports. It is a dropdown, it goes to 14 months,
and there is no way to recover what was already dropped.

**Google Signals is off**, and turning it on changes what demographic data exists.

**Internal traffic is not filtered.** The office and the developer's machine are in every
report until a filter is defined and activated. Defining one without activating it is the
common half-measure, and an unactivated filter marks the traffic without excluding it.

**Cross-domain measurement is unconfigured.** A checkout on a payment host or a booking on
a scheduling host starts a new session with a referral source of the site itself. The
symptom is self-referrals in the acquisition report.

**Unwanted referrals are not listed.** Payment providers appear as traffic sources and
take credit for conversions the original source earned.

**Enhanced measurement counts things twice.** A single-page app that also fires its own
`page_view` on route change gets two per navigation, doubling the count.

## Consent mode

Where a consent banner exists, GA4 needs consent mode wired to it or the tag either sends
nothing or sends everything, depending on which mistake was made. Consent mode v2 adds
`ad_user_data` and `ad_personalization` alongside the storage signals, and Ads audiences
depend on them. A site with a banner and no consent mode has an analytics record shaped by
the banner rather than by its visitors.

## Timing

Realtime reports are immediate and cover 30 minutes. Standard reports settle within 24 to
48 hours, and the current day is always incomplete. A configuration change applies going
forward and never retroactively: a filter defined today does not clean last month, and
retention raised today does not restore what expired.

Attribution can revise historical figures for up to 90 days as later conversions are
credited back to earlier sessions, so a number read today for last week may differ from
the same number read next month.

## Common failures

**Two properties, one site.** A Universal Analytics migration left a GA4 property nobody
uses, and a second one created later carries the real data. Reports are compared across
both without anyone noticing they are different properties.

**The tag is in the template but the template is not on every route.** A marketing route
outside the app shell has no tag, and the pages with the highest intent are missing from
every report.

**Key events were never marked.** The events fire and the data is there, but nothing is
flagged as a key event, so Ads sees no conversions and the reports show no goal
completions.

**The measurement ID belongs to another client.** Copying a template between client sites
carries the tag with it, and one client's data lands in another's property. This is a
privacy incident, not only a data problem.

**Ecommerce events use the wrong parameter names.** GA4 expects a specific `items` array
shape. A close-but-wrong payload reports the event count correctly and the revenue as
zero.

## What is outside anyone's control

Ad blockers and tracking prevention remove a share of traffic that varies by audience and
cannot be measured from inside the tool. GA4 applies thresholding and withholds rows when
a report could identify individuals, so totals exceed the sum of visible rows. Sampling
enters exploration reports above a volume threshold. And Google changes the interface,
the metric names and the model periodically, so a report built on a named metric can stop
returning it.
