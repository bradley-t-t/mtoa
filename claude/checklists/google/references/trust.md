# Trust and safety

## What the surface is

The failures in this group do not cost a site positions. They remove it: an interstitial in
front of every arrival, a manual demotion, a browser refusing to load the page, or a
disappearance from the index with no report attached.

They are also the failures a repository cannot prove absent. Source code says nothing about
whether Google flagged the domain, whether a certificate chain is complete over the wire,
whether a CDN is serving a different page to a crawler, or whether an agency bought links
four years ago.

## Safe Browsing

Google maintains a list of URLs hosting malware, unwanted software, social engineering or
phishing. A match puts a full-page red interstitial in Chrome, Firefox and Safari for every
visitor, including ones arriving directly, and search results carry a warning. Traffic goes
to zero the same day.

The **Safe Browsing Lookup API** (`safebrowsing.googleapis.com/v4/threatMatches:find`)
takes a list of URLs and returns matches, with threat types. It answers for the exact URLs
submitted, so a check covers the apex, `www`, and any subdomain the site serves from. The
**Update API** is the local-database variant and is unnecessary for checking a handful of
hosts.

Search Console's security issues report is where a flagged site sees the reason and files a
review. Reviews take a few days. The flag persists until the review passes, so removing the
malicious file and waiting achieves nothing on its own.

A flag can also come from a compromised advertising script or a subdomain nobody
remembered, which is why the check covers hosts rather than the home page.

## HTTPS

Three things are checked and they fail independently:

**The certificate matches the name.** A certificate issued for `www.example.com` and served
on the apex fails, and a site that redirects apex to www still serves a certificate on the
apex to make the redirect.

**The chain is complete.** Browsers cache intermediate certificates, so a site missing its
intermediates works in the browser of anyone who has visited a site sharing that issuer and
fails for a crawler holding nothing. This is the failure that gets reported as "works for
me".

**It has not expired and will not soon.** Automatic renewal that stopped running is
invisible until the day it matters, and it is a weekend as often as not. Reading the
current expiry is the check; confirming that renewal is configured is not.

### Mixed content

A page served over HTTPS that loads a script, stylesheet, font, frame or image over HTTP
has that resource blocked or upgraded by the browser. The page a visitor sees is not the
page that was tested, and the failure is silent unless somebody has the console open.
Absolute HTTP URLs left in a template and a third-party embed that never moved are the two
sources.

## Manual actions

A manual action is a person at Google deciding to demote or remove the site. It is reported
in Search Console, it has no API, and it persists until a reconsideration request is
accepted, however clean the site becomes in the meantime.

The categories that reach ordinary sites:

- **Unnatural links to your site.** Paid, exchanged or automated inbound links.
- **Thin content with little or no added value.**
- **Structured data problem.** Markup describing content absent from the page.
- **User-generated spam.** Comments and forum posts nobody moderated.
- **Cloaking or sneaky redirects.**
- **Pure spam**, which is total removal.

A reconsideration request is read by a person. It has to say what was wrong, what was
removed, and what stops it recurring. A request that asserts the site is clean without
evidence is refused, and each refusal lengthens the next cycle.

## Link profiles

Sites inherit link histories. A domain bought from somebody else, or a site handed over
from an agency that bought links, carries the pattern into the new owner's account.

The Search Console links report exports the top linking sites. What to read for: hundreds
of links from unrelated domains in unrelated languages, links from directories that exist
only to sell them, sitewide footer links, and anchor text that is commercial rather than
natural.

Where the links cannot be removed at the source, a disavow file tells Google to ignore
those domains. Disavow is a blunt instrument: it is uploaded whole, it replaces the previous
file, and disavowing a domain that was helping costs the links it provided.

## Cloaking

Serving different content by user agent is a policy breach whether it was deliberate or
fell out of infrastructure. The accidental versions are more common than the deliberate
ones:

- Bot protection at the CDN challenging Googlebot.
- A paywall that serves the full article to the crawler and a teaser to visitors, without
  the paywall structured data that makes it legitimate.
- Geographic redirects that send a crawler in one country somewhere a visitor is not sent.
- A cache that stores the crawler's response and serves it to everyone, or the reverse.

The check is a fetch as Googlebot compared against a fetch as a browser, on the same URL,
looking at rendered text rather than bytes.

## Hacked content

Injected content is served selectively, which is what makes it survive. It appears for
visitors arriving from search and not for direct visits, or for a crawler and not for a
browser, or only on URLs nobody links to. The owner looking at the site sees nothing.

Checking means fetching with a search referrer, fetching as a crawler, and comparing
against a plain fetch, plus reading the index for pages nobody published. Cleaning the
injected content without closing the entry point buys days.

## Third-party scripts

Every third-party script on a page is that vendor's release process operating on this
domain's reputation. A widget that pulls further scripts at runtime is an open door, and
several Safe Browsing flags on client sites originate in an analytics or chat vendor rather
than in the site.

What reduces it: knowing who owns every origin the page loads, removing anything nobody can
name an owner for, pinning to versioned URLs, and subresource integrity where the vendor
serves stable files.

## Timing

Safe Browsing reviews take a few days. Security issue reviews are similar. Reconsideration
requests take one to several weeks, and a refused one restarts the clock. A certificate
change is immediate at the edge and takes as long as the DNS or CDN cache to propagate.
An index recovering after a removal is weeks rather than days, and it does not always
return to where it was.

## What is outside anyone's control

Whether a review passes and when. Whether an inherited link pattern is judged as a scheme.
How long a reconsideration takes. Whether the index restores previous positions after a
penalty lifts. And what a third-party vendor ships to their CDN tomorrow.
