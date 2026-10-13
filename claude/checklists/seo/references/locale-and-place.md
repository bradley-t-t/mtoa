# Locale and Place

Both gate groups here are opened by a flag rather than inferred, because a
single-locale site and one whose translations are not built yet look identical in
source.

## hreflang

Every locale points at every other, and every one of them points back. A one-way
annotation is ignored.

An `x-default` entry catches everyone whose locale is not listed. Without it a visitor
outside the set is sent nowhere in particular.

Codes are a two-letter language, optionally with a two-letter region in caps:
`en`, `en-GB`, `es-MX`. A malformed code does not degrade — the whole annotation is
dropped.

The `lang` attribute on `<html>` has to agree with the hreflang for that page. They
are two halves of one claim.

Content parity matters as much as the tags. A locale carrying a stub translation of a
full page is a worse result than not offering that locale, and machine translation
shipped unreviewed reads as exactly what it is.

## Local business

Name, address and phone appear identically everywhere they appear. Citations are
matched on exact strings, so a second phone number splits one business into two as far
as the match is concerned. Keep them in one constant and render every appearance from
it, including the ones inside the structured data.

`LocalBusiness` markup carrying `address`, `telephone`, `openingHours` and `geo`, built
from the same source as the rendered contact block. A node missing any of them is a
partial answer to the question a local result is built from.

Use the most specific subtype that fits — `Restaurant`, `Dentist`, `Plumber`,
`Attorney`, `Store` — rather than the generic parent.

Location pages are where local SEO turns into a doorway-page problem. The signs are
always the same: only the city name changes between pages, no local detail, no local
business signals, keyword-stuffed URLs. Hold 60% unique content per page past 30
pages, and require a real presence behind each one past 50.

What makes a location page real: named landmarks and neighbourhoods, services specific
to that location, the team who actually work there, and reviews from customers in that
area.
