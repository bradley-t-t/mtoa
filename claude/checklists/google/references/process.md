# How a run works

## The unit of scope is a site, not a file

Every other checklist in this set opens against a directory and answers for the files
inside it. This one opens against a portfolio. The properties it drives live in Google's
accounts, so the question a run answers is "which of the sixteen sites is wrong on
Google's side", and the answer is a per-site verdict for every gate.

A run therefore carries a site list before it carries a target path. Each site brings a
domain, the Search Console property covering it, the analytics property, the Business
Profile where one exists, and the repository its fixes land in. A site missing any of
those is a finding rather than an omission: an unregistered property is the reason
nothing can be read for it.

The registry is answered per site and reported per site. A gate that passes on fifteen
sites and fails on one is a failing gate, and the report names which one.

## This is an instruction to change things

The deliverable is the state of the Google properties afterwards. A run that ends with a
list of what is wrong has produced nothing: the account settings are still wrong, the
sitemaps are still unsubmitted, the disapproved products are still disapproved. Every
gate carries a fix, and the fix is the work.

Two categories of finding leave this run rather than being fixed inside it:

- Anything that lives in the repository goes to `/seo-checklist`. Meta tags, JSON-LD
  templates, robots.txt, the sitemap's contents, the copy on a page.
- Anything that is a loading-speed problem goes to `/perf-checklist`. Bundle size,
  images, the critical path, main-thread work.

Both handovers are recorded. A finding delegated without a record is a finding dropped,
because nothing downstream knows it exists.

## Where the boundary sits against the SEO checklist

The split is not by topic, it is by who holds the truth.

| Question | Owner |
| --- | --- |
| Does the page emit valid Product JSON-LD? | seo-checklist |
| Did Google resolve that Product and grant the result? | this checklist |
| Does robots.txt allow the crawler? | seo-checklist |
| Is the URL actually indexed? | this checklist |
| Does the sitemap file list every route? | seo-checklist |
| Was the sitemap submitted and read without error? | this checklist |
| Is the hero image 400KB? | perf-checklist |
| Does the field record for LCP pass? | this checklist |

The repository can be perfect while Google's answer is wrong, and the reverse happens
just as often. Running one checklist and assuming it covers the other is how a site with
flawless markup earns no rich results for a year.

## The run protocol gates

The `PRC` group is the bookkeeping that stops the checklist becoming a survey. It answers
to the run's own state rather than to anybody's word for it.

- **The run was opened before the work.** Changes made outside a run are changes nothing
  recorded, and an unrecorded change is indistinguishable afterwards from one that was
  never made.
- **Scope is every site, not the one named.** A prompt mentioning one domain does not
  narrow the run to it. Narrowing has to be argued: each site left out is named, with the
  reason.
- **Every site in scope was read and ruled on.** A site ends either changed or explicitly
  ruled as needing no change. A site nobody opened and a site considered and left alone
  look identical without this.
- **Findings belonging elsewhere were handed over.** The run holds a delegation record for
  `/seo-checklist` and `/perf-checklist`, or an explicit statement that there was nothing
  to hand over.
- **Every applicable gate is answered.** A gate that is not met is work to do. The only
  ways past one are changing the property or naming the condition that puts it out of
  scope.
- **The filled checklist was rendered.** The run ends with every gate, its verdict per
  site, the evidence, and a section listing what was answered not applicable and why.

A gate the sweep failed cannot be recorded as passing, and a gate marked fixed reopens if
the next sweep still fails it.

## Probes, attestations and manual checks

`check.type` is one of three things, and the difference matters:

- **probe** is a request against an API or a live URL. It cannot be answered by hand,
  which is the whole point. Most gates here are probes.
- **attest** is for a fact Google exposes only in a dashboard, with no API and no
  observable consequence. It is a claim by whoever ran the checklist, so it is used
  sparingly.
- **manual** is a judgement about the run itself rather than about Google.

A probe that could not be taken is recorded as failing with the reason. An unreachable
API is an unknown, and an unknown filed as a pass is worse than a failure, because nobody
comes back to it.

## Lag is the defining property of this work

Nothing on Google's side is immediate, and the delays differ by orders of magnitude:

| Change | Before it shows |
| --- | --- |
| Property verification | minutes |
| Sitemap submission read | hours to days |
| URL submitted for indexing | hours to weeks |
| Structured data recrawled and reported | days to weeks |
| Business Profile edit reviewed | hours to days, sometimes longer |
| Merchant feed refetched | hours |
| Field vitals record moving | 28 days at minimum |
| Manual action reconsideration | days to weeks |

A run that lands a fix and then reports the gate as passing has invented the result. The
honest close is to record the fix, the date it landed, and the date the verdict can be
taken. Gates that depend on a delayed surface carry a recheck rather than a pass.

## What nobody controls

Google chooses the site name, the favicon it shows, the sitelinks, the snippet, whether a
knowledge panel exists and what it says. Every one of those can be influenced by signals
and none can be set. A run's job on those gates is to make the signals unambiguous and
consistent, record the state, and recheck. Reporting one as fixed the day the signal
changed is a guess.
