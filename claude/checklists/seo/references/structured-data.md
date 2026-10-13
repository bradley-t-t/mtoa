# Structured Data

How a page states what it is rather than leaving it to be inferred. It is consumed
away from the page by machines that never see it rendered, which is why every failure
here is one a browser cannot show you.

JSON-LD, always. It is Google's stated preference and the only format that does not
entangle the markup with the meaning.

## The failures that are invisible in a browser

- **A syntax error discards the block whole.** A trailing comma does not degrade the
  markup; it removes it, silently.
- **Relative URLs resolve against nothing.** `url`, `logo`, `image`, `sameAs` and
  `@id` are read outside the page's context. Build them from the site origin.
- **Placeholder values contradict the page.** A bracketed template string left in a
  node is a manual-action risk, not a cosmetic slip.
- **Script-injected markup is processed late.** Per Google's December 2025 JavaScript
  guidance, structured data injected by script — `Product` and `Offer` above all —
  may be delayed. Serve it in the initial HTML.

## Type status, as of mid-2026

**Live, recommend freely.** Organization, LocalBusiness, SoftwareApplication,
WebApplication, Product (with Certification), ProductGroup, Offer, Service, Article,
BlogPosting, NewsArticle, Review, AggregateRating, BreadcrumbList, WebSite, WebPage,
Person, ProfilePage, ContactPage, VideoObject, ImageObject, Event, JobPosting, Course,
DiscussionForumPosting, QAPage, BroadcastEvent, Clip, SeekToAction, SoftwareSourceCode.

**Retired, never recommend.** HowTo (rich results removed September 2023),
SpecialAnnouncement (July 2025), CourseInfo, EstimatedSalary and LearningVideo (June
2025), ClaimReview (June 2025), VehicleListing (June 2025), Practice Problem (support
removed January 2026), Book Actions.

**No rich result, keep if useful.** FAQPage — Google retired FAQ rich results for all
sites on 7 May 2026. Existing markup does not need removing; it simply buys nothing in
Google's results. For question-and-answer pages that carry real questions, use QAPage, which expanded its
comment-thread properties in March 2026.

**Dataset** is not discontinued. It feeds Google Dataset Search and has no Search rich
result. Do not advise removing it as though it were killed.

For adult products, `hasAdultConsideration` (added May 2026) is required, with the
value `https://schema.org/SexualContentConsideration`.

## What every site owes

An identity node. `Organization` or `Person` in the shell, carrying `name`, `url`,
`logo` and `sameAs` pointing at the profiles that corroborate it. Every other entity
claim hangs off this one, and without it the brand is a string rather than a thing.

`WebSite` alongside it, with `potentialAction` if the site has real search.

## Per route

| Route | Type | Properties that carry |
| --- | --- | --- |
| Article or post | BlogPosting | headline, author, datePublished, dateModified, image |
| Product | Product + Offer | name, image, description, offers.price, priceCurrency, availability |
| Any nested route | BreadcrumbList | position, name, item on each entry |
| Local business | LocalBusiness | address, telephone, openingHours, geo |
| Video | VideoObject | name, description, thumbnailUrl, uploadDate, duration |

## Writing it

Identify the page type from the content, pick the type that matches, fill every
required property and the recommended ones you can fill truthfully, and validate
before it ships. A property you cannot fill honestly is a property you leave out —
markup that disagrees with the visible page is worse than no markup.
