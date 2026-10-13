# Reference sites

What linear.app, stripe.com, and www.apple.com ship, measured 2026-08-23 from each
homepage and every stylesheet it links. These three are the standing benchmark for
web design, and where all three agree the agreement is a rule, held as the REF gates
in `checklist/gates/12-reference.json`. Sections carry the evidence; gates cite the
section.

## Display tracking

Type tightens as it grows. Linear's ramp sets `-0.012em` on titles below 2rem and
`-0.022em` from 2rem up (`--title-4-letter-spacing` through `--title-9`). Stripe's
heading tokens carry `-0.01em` to `-0.02em` (`--hds-font-heading-hero-lg-letterSpacing`).
Apple uses `-0.022em`, `-0.016em`, and `-0.01em` across its headline classes — 332
declarations of `0em` on small type, negative values reserved for display. None of
the three leaves display type at the browser default.

## Display leading

Line-height compresses as size grows, on a per-step curve rather than one value.
Linear: 1.4 at 1.0625rem, 1.33 at 1.25-1.5rem, 1.125 at 2rem, 1.1 at 2.5rem, 1.0-1.06
at 3rem and above. Stripe hero headings sit at 1.05-1.12; `line-height:1` is its most
common declaration (51 uses). Apple's headline classes run 1.05-1.125 while body
classes hold 1.29-1.42, computed to exact ratios. Body stays 1.5-1.75; display never
borrows it.

## Type steps

The scale is not a list of sizes. Each named step defines size, line-height,
letter-spacing, and weight together: Linear's `--title-N-size` /
`--title-N-line-height` / `--title-N-letter-spacing` compose into a `--title-N` font
shorthand with `--font-weight-semibold` (590); Stripe's `--hds-font-heading-*` tokens
carry `-size`, `-lineHeight`, `-letterSpacing`, and `-weight` per step. Display weight
is a decision per step — Stripe's hero is 300, Apple's headlines 600, Linear's titles
590 — never the browser's default bold.

## Translucent chrome

Sticky navigation is an alpha surface plus backdrop blur, both tokens. Linear:
`--header-bg: #0b0b0bcc` (dark) / `#fffc` (light), `--header-blur: 20px`,
`--header-border: #ffffff14` / `#00000014`. Apple: `--globalnav-background:
rgba(22, 22, 23, .8)` with `--globalnav-backdrop-filter: saturate(180%) blur(20px)` —
the saturate keeps colour alive through the blur. All three ship the
`-webkit-backdrop-filter` prefix alongside the unprefixed property, and the surface
alpha sits at .8 or higher so the bar stays legible where the blur is unsupported.

## Hairlines

Separators are the theme's ink at low alpha, not a picked opaque grey. Linear's
header border is `#ffffff14` on dark and `#00000014` on light — 8% ink. Apple's
dividers run `rgba(0, 0, 0, .08)` to `rgba(0, 0, 0, .16)` and `rgba(255, 255, 255, .08)`
to `.24`. An alpha hairline composites correctly over any surface it crosses, which is
what lets one token serve every card, panel, and theme.

## Headline breaks

Line breaks in display copy are controlled, not left to the layout engine. Stripe
sets `text-wrap: balance` on headings (6 rules) and `text-wrap: pretty` on supporting
prose (23 rules). Linear balances hero headlines and sets `pretty` on body copy.
Apple hand-places `<br>` spans per breakpoint — the manual form of the same decision.
A headline that strands one word on its own line shipped unread.

## Font rendering

All three declare `-webkit-font-smoothing: antialiased` (with
`-moz-osx-font-smoothing: grayscale`) at the root, so weights render as drawn rather
than a step heavier on macOS. The ramp's weights are chosen with that rendering in
place — Linear's 510/590/680 variable weights and Stripe's 300 hero depend on it.

## Container token

The content width is one named value. Linear: `--page-max-width: 1024px`. Stripe:
`--hds-space-layout-content-maxWidth: 1264px`, with derived tokens computed from it.
Sections reference the token; the number exists once. Apple centres content at
980-1024px through shared layout classes. Supporting copy gets a narrower measure
(Linear's most common max-width is 640px, 79 uses) inside the same container.

## Logo walls

Third-party logo strips render as one ink. All three ship grayscale treatments in
their customer and partner logo sections, so the strip reads as a quiet band of
proof instead of a row of competing brand colours.
