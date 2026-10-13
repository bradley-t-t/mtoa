# Brand chrome

## The bar

The agency bar, where the profile describes one (`agency.bar`), is the standard
footer rail: attribution and mark on the left, live status and build on the right, a
hairline above it, thirty-eight to forty-two pixels tall. Without one, the bar's gates
stand down. It is detached from whatever real footer the site has, and it carries no
client links.

It is one shared component, imported. A hand-rolled copy per project is how nine sites
end up with nine slightly different bars and no way to change any of them at once.

It is mounted in the shell that wraps every route, never per page. A visitor who lands
on a deep link is the one most likely to want to know who built the thing.

**It names no colour of its own.** Every tone it shows is mixed from the ink it
inherits:

```css
color-scheme: inherit;
background: transparent;
color: color-mix(in oklab, currentColor 92%, transparent);
--bar-accent: currentColor;
```

with the label beside it at seventy-four percent, the hairline at thirteen, and the wash
at four. A translucent ink wash darkens a light ground and lightens a dark one without
being told which it is looking at, and the accent starts as the page's own ink so a
project that never sets the accent property (`agency.bar.accent_var`, shown here as
`--bar-accent`) still gets a bar that matches; setting it retargets the one accent for a
client whose palette it fights.

The ground stays transparent and no tone is a system colour. `Canvas`, `Window` and
their siblings answer to the browser's colour-scheme preference rather than to the page,
so a bar built on them renders a white glass under a dark site whenever the visitor's
system is set to light — matching the operating system while missing the page it sits
on. Inheriting `color-scheme` is what keeps any system tone the host does use pointing
the same way the host does.

That is what lets the same markup land on a white brochure site, a warm restaurant page
and a dark dashboard with no per-project palette and no theme prop.

## Where the boundary runs

This checklist asks whether the bar is **there, current, and pointing somewhere that
answers**. How it looks on a given page — spacing against the content above it,
whether its accent fights the palette, whether the hover state reads — is
`/design-checklist`, and a finding of that shape is delegated rather than fixed here.

The icon set is the same split. A favicon, an apple-touch-icon and a manifest that
exist and belong to this project are wiring. The share-card image is metadata, and
metadata is `/seo-checklist`.

## The year

Any year the chrome shows is computed at render. A hardcoded one is correct for a year
and quietly wrong for every year after, on every site at once. Verbatim licences, terms
and archived changelog entries carry the year they were written and are outside this.
