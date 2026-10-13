# Shopping and Ads

## What the surface is

Two accounts sit between a store and the money Google can send it. Merchant Center holds
the product feed and decides whether each item is servable. Google Ads holds the campaigns
and the conversion actions that tell bidding what worked.

Neither lives in the repository, and neither is visible from the site. A store can have
flawless product pages, correct Product markup, and a Merchant account suspended three
months ago. The site gives no sign.

This whole group is gated on the site selling or advertising. A brochure site never sees
it.

## Merchant Center

### Claiming and verification

The store domain is verified and then claimed. Verification proves control, usually by the
same methods Search Console uses and often inherited from it. Claiming binds the domain to
one Merchant account.

A domain can be claimed by exactly one account. When a previous agency holds the claim, the
new account cannot claim it until the old one releases it, and there is a dispute process
for when nobody can reach the old owner. This is the slowest part of onboarding a client
store.

### The feed

A primary feed supplies the catalogue. The routes in:

- A scheduled fetch from a URL the store serves.
- An upload, by file or by SFTP.
- The Content API, now the Merchant API.
- Automatic feeds built from the site's structured data, which is the weakest of the four
  and depends entirely on the Product markup being right.

Items expire 30 days after their last refresh. A feed whose schedule stopped keeps serving
until that clock runs out, which is why the failure appears as products vanishing weeks
after the thing that broke.

### Disapprovals

An item that breaks a policy or a data requirement is disapproved and is invisible in both
free listings and Shopping ads. The Diagnostics page reports them by issue and by item.
The recurring causes:

- **Price mismatch.** The feed price and the price Google reads from the landing page
  differ. Currency, tax handling and a sale price that only applies in a cart all produce
  this.
- **Availability mismatch.** Same mechanism, on stock state.
- **Missing GTIN or brand.** Required for most branded products.
- **Missing shipping.** Settings are per-country and their absence disapproves items
  outright in most markets.
- **Image problems.** Placeholders, watermarks, promotional overlays.
- **Landing page unreachable to the crawler.** Often bot protection at the CDN.

An account-level issue is worse than any item-level one: it suspends the whole catalogue.
Requesting review before the underlying cause is fixed spends one of a limited number of
reviews.

### Automatic item updates

Google reads the Product structured data on the landing page and corrects the feed's price
and availability from it. With this off, a price change on the site and a stale feed produce
a mismatch disapproval. With it on, the same change produces a silent correction. It
depends on the page carrying price and availability in its markup, which is where this
group meets the rich results group.

### Shipping and returns

Shipping services and rates are set per target country. A returns policy sets the window
and the method, and its absence withholds the returns annotations shopping surfaces show.
Both are account settings rather than feed attributes, though feed-level overrides exist.

### The API

The Merchant API (`merchantapi.googleapis.com`), replacing the Content API for Shopping,
covers accounts, product inputs, product status and issues, data sources and reports.
Product status is what settles the disapproval gates: it returns per-item issues with the
attribute each one names.

## Google Ads

### Conversions are the thing to check

A campaign optimises against its conversion actions. When those are wrong, the campaign is
efficiently buying the wrong thing, and every report looks healthy because the numbers are
about the wrong events.

Two failures matter most:

**Conversions that count page loads.** A conversion action pointed at a thank-you URL
counts arrivals rather than purchases, and it counts a refresh twice.

**Conversions without values.** Value-free conversions give smart bidding one signal where
it needs two, so it optimises for the cheapest conversion rather than the most valuable
one. A store bidding this way buys low-value orders and reports a falling cost per
conversion while revenue drops.

A conversion action that has recorded nothing for a week has usually stopped firing, and
the campaign keeps spending against a signal that no longer arrives. Consent changes,
container edits and a tag removed during a redesign all cause it.

### Links

- **Merchant to Ads**, so Shopping campaigns have inventory. Sent from Merchant Center,
  accepted in Ads. A pending link leaves campaigns with nothing to serve.
- **Ads to GA4**, so paid sessions are attributed and audiences built in analytics reach
  campaigns. Created in the GA4 admin, accepted in Ads.
- **Auto-tagging on**, which is what puts the `gclid` on the landing URL. Without it,
  attribution falls back to referrer parsing and paid traffic lands as organic.

### The API

The Google Ads API is versioned and changes several times a year, with older versions
retired on a published schedule. It needs a developer token with basic or standard access,
which is an application rather than a setting. Conversion action status, conversion counts
and the link records are all readable through it.

## Timing

A feed refetch is minutes to hours. A disapproval clears on the next fetch plus review,
typically within a day, though a policy review after an appeal takes three to five business
days. An account suspension appeal takes a week or more and there is a limit on attempts.

Conversion data in Ads settles within three hours for most actions, and conversion windows
of up to 90 days mean historical numbers keep rising after the fact.

## Common failures

**Two Merchant accounts on one domain.** Reporting is split, the claim is contested, and
duplicate accounts are themselves a suspension cause.

**The feed is right and the page is stale.** A cached product page serving last week's price
disapproves items that are correct in the feed. The fix is in the site.

**Test conversions left in place.** A conversion action created during setup fires on a
staging event and pollutes bidding.

**The GTIN was invented.** An invalid check digit fails validation, and a valid one
belonging to another product is worse.

## What is outside anyone's control

Whether a review passes and how long it takes. Which policies apply in which market, and
policy changes applied to existing catalogues without notice. Whether an item Google
approved is actually shown, which is an auction decision. And the retirement schedule of
the API versions, which forces migration work on a calendar nobody sets.
