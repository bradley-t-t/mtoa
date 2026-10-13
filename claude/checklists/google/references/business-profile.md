# Business Profile

## What the surface is

A Business Profile is the record behind the map pin, the panel on a brand search, and the
listing in local results. For a business with a location or a service area it carries more
traffic than the website, and it is the one Google property members of the public can edit.

It also exists whether or not anyone claimed it. Google generates profiles from third-party
data, and an unclaimed profile for a real business is a live listing with a phone number
nobody chose, hours nobody set, and a "Suggest an edit" link open to strangers.

## Claiming and verification

Verification proves the business is the business. The methods offered depend on the
category, the country and the account history, and they are not chosen by the applicant:

- **Video recording.** The most common method now. A continuous unedited recording showing
  the location, the signage, the equipment and proof of management.
- **Postcard.** A code mailed to the address, arriving in about two weeks.
- **Phone or text**, and **email**, offered to a minority of businesses.
- **Instant verification** where the domain is already verified in Search Console under
  the same account.

Verification is where most of the delay in this group lives. A video review takes about
five business days and can be rejected for reasons that are not itemised. A rejection is
appealable, and a second attempt with the same weakness is rejected again.

A profile can also be suspended, which is different from unverified: the listing disappears
and reinstatement goes through a separate appeal. Editing several fields at once on a
newly verified profile is a known trigger.

## The API

The Business Profile APIs are a family rather than one service:

- `mybusinessbusinessinformation.googleapis.com` for locations, categories, hours,
  attributes and the address.
- `mybusinessaccountmanagement.googleapis.com` for accounts and users.
- `businessprofileperformance.googleapis.com` for views, searches, calls and direction
  requests.
- `mybusinessverifications.googleapis.com` for verification state and options.

Access requires an approved quota request. A fresh Cloud project has a default quota of
zero on these APIs, and the approval takes days. A run that needs the API on a new project
plans for that rather than discovering it.

Reviews and review replies moved out of the public API surface. Reading and replying to
reviews at scale is not generally available, so those gates are settled in the dashboard.

## What the record has to contain

- **Name.** The business name as it appears on the premises. Keywords appended to it are a
  documented violation and the most common reason a competitor's edit succeeds.
- **Primary category.** The single largest ranking factor in local results. Secondary
  categories add coverage and dilute nothing.
- **Address or service area.** A business without a location visitors can walk into hides
  the address and defines a service area instead. A hidden address that is also listed is a
  suspension risk.
- **Hours**, including special hours for holidays. Wrong hours send somebody to a locked
  door, and that arrives as a one-star review.
- **Phone number.** The same number the site publishes, not a tracking number, unless the
  tracking number is set as the primary with the real one secondary.
- **Website URL.** Pointing at the page for that location, not the site root, where a
  business has several.
- **Photos.** The exterior for recognition, the interior, and the logo. Profiles with
  photos are chosen over profiles without them.

## Consistency is the mechanism

Google reconciles the business against other sources: directories, aggregators, the site
itself. Name, address and phone matching everywhere is what makes the entity confident.
A single stale listing with an old suite number keeps Google uncertain, and uncertainty
shows up as weaker placement rather than as an error anybody can read.

The site's own contact page and its LocalBusiness markup are part of that set, which is
where this group meets the rich results group.

## Timing

Most edits go live in minutes. Some hold for review, and a name or category change on an
established profile can hold for days. Performance data lags two to three days and is kept
for six months, which is why it is exported rather than read when it matters.

Suggested edits from the public can go live without notice. A claimed profile gets an
email; an unclaimed one gets nothing.

## Common failures

**Duplicate profiles.** A move, a rebrand or a second claim leaves two listings for one
business, splitting reviews and confusing ranking. Duplicates are resolved by reporting
one as a duplicate, which is a slow manual process.

**The profile is claimed by a former agency.** The business cannot edit its own listing.
The route back is the ownership request flow, which gives the current owner seven days to
respond and takes weeks when they do not.

**Keyword stuffing inherited from someone else's work.** The name field carries a service
description. Removing it costs nothing and prevents a suspension.

**The address is a virtual office.** Ineligible, and a suspension when detected.

**Hours were set once.** Holiday hours were never filled in, so the profile says open on a
day the business is closed.

## What is outside anyone's control

Google decides whether a business qualifies for a profile at all, what verification method
is offered, whether a video is accepted, and how long any of it takes. Reviews cannot be
removed except by reporting a policy breach and waiting. Public edits to hours and
attributes can land at any time. And the panel that shows the profile assembles facts from
sources beyond the profile, so a corrected profile does not always produce a corrected
panel.
