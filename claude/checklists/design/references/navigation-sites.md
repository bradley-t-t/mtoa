# Navigation sites

What the navigation bars of stripe.com, www.apple.com, www.anduril.com, ramp.com,
mercury.com, www.saronic.com, and shield.ai ship, measured 2026-08-23 from each
homepage and every stylesheet it links. These seven agree on far more than their
registers suggest — a defense contractor and a payments company land on the same
heights, the same token families, and in two cases the same easing curve — and
where they agree the agreement is a rule, held as the NAV gates in
`checklist/gates/13-navigation.json`. Sections carry the evidence; gates cite the
section.

## Two surface states

A bar over a hero has an at-top state and a scrolled state, and a transition
between them. Shield AI's `.header` sits transparent over the hero behind
gradient scrims (`linear-gradient(to bottom, rgba(0,0,0,.75), transparent)` on
its pseudo-elements), swaps to solid `#050506` as `.header--solid` and when
`.is-fixed`, and moves between the two with `transition: background-color
ease-out 0.2s`. Saronic's `SiteHeader` holds `position: sticky; top: 0` on
`background-color: var(--body-bg)` and goes transparent only under its
`[data-animated=true]` hero state. Apple is the one-state form: `#globalnav`
carries `var(--globalnav-background)` — `rgba(22, 22, 23, .8)` — with
`var(--globalnav-backdrop-filter)` at all times, so there is nothing to swap.
Either shape works; a bar that snaps between states, or scrolls content beneath
a transparent surface, is neither.

## Nav motion tokens

The bar's timings are a named token family, not literals in transitions. Ramp:
`--nav-desktop-popup-duration: .3s`, `--nav-mobile-duration: .24s`,
`--nav-mobile-duration-slow: .3s`, `--nav-hamburger-duration: .25s`, easing as
`--nav-mobile-easing`. Stripe: `--navigation-duration: 240ms`,
`--navigation-duration-slow: 300ms`, `--navigation-hamburger-duration: 0.25s`,
easing as `--navigation-easing` — and under `@media (prefers-reduced-motion:
reduce)` all three are set to `0s` at `:root`, so one media query stills the
whole bar. Both sites ship the identical curve: `cubic-bezier(.45, .05, .55,
.95)`. Every measured duration sits in the 240-300ms band the polish gates
already require; what the tokens add is one definition to change.

## Heights as tokens

Every measured bar names its height once: Apple `--globalnav-height: 48px`
(44px compact), Stripe `--navigation-height: 76px`, Saronic `--height: 64px`
scoped to the header component, Mercury `--navbar-height: var(--spacing-72)`
referenced from a dozen `calc()` offsets, Ramp `--nav-height: calc(56px +
var(--nav-banner-height))` so the announcement banner participates in the same
arithmetic. HDR-11 already holds this as a gate; the agreement here is the
evidence that it is not optional.

## Disclosure triggers

A top-level item that opens a panel is a button with `aria-expanded`, not an
anchor with a dead href. Ramp's bar renders seven true links beside seven
buttons, five of them carrying `aria-expanded` for its five groups; Stripe's
five triggers and Mercury's four do the same; Apple's globalnav carries it on
its flyout controls. The items that navigate stay anchors.

## Structured panels

Past a handful of destinations, a panel is a directory, not a link dump.
Mercury ships the whole structure server-rendered: 54 destinations inside the
header, grouped under its four triggers, nineteen of them carrying a one-line
description beneath the label. Stripe's Products panel groups Payments,
Billing, Connect, and Issuing under headings; Ramp's panels group the same way.
The description is what lets a first-time visitor choose between two plausible
links without opening both.

## One loud action

The bar closes with a quiet account link beside one action cluster — Stripe's
Sign in beside Contact sales, Ramp's Sign in beside Get started, Mercury's Log
in beside Open Account, Shield AI's lone Schedule a Demonstration. At most one
of them is a filled button; two competing filled buttons split the one decision
the bar is allowed to push.

## Tracked micro-labels

Uppercase at small sizes carries positive tracking. Anduril sets
`letter-spacing: .03rem` on 86 of its 109 uppercase rules — the exceptions are
display headlines tracked negative, which is the other end of the same curve.
Shield AI's uppercase runs `.07em` to `.11em`. The lowercase-first sites barely
touch the transform at all — Stripe uses `text-transform: uppercase` once,
Apple never — which is the other honest answer: track capitals wide, or do not
set capitals.

## Hide on scroll

Saronic is the only one of the seven that hides its bar, and it does it with
the tokens that place it: `[data-hidden=true]` moves the header to `top:
calc(var(--announcement-bar-height) - var(--height))`, so the 64px bar slides
away by exactly its own height and the announcement offset stays correct. The
bar returns on the first upward scroll and never hides while its menu is open.

## One panel that travels

Stripe's shipped bar keeps one panel alive while the pointer moves along the
triggers: the open surface slides and resizes to the next item with a content
crossfade rather than closing and replaying the open animation, and the
diagonal path from trigger to panel never closes it. This is behavior, not a
stylesheet fact — it reads from the rendered site, and it is the first thing
imitations of this bar drop.

## Top-level restraint

Four to seven top-level destinations, one or two words each. Stripe holds five
named groups, Ramp six, Mercury four beside Pricing, Shield AI six, Saronic six
behind a single Menu button rather than crowding its bar. Apple runs twelve and
gets away with it because every label is one word. Nothing measured here ships
a top level past that, and nothing wraps a label.
