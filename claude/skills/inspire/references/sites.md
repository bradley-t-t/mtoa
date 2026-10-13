# Where to look, by element

This file says where to go. It does not say what the numbers are - the numbers
come out of `bin/extract.js` against the live page, because a value written down
here is stale the next time the site ships. Treat every line below as a pointer
to something worth measuring, and measure it.

Sites are tagged by the lane they are worth the trip for: **F** form, **M**
motion, **B** behavior. A site strong in one lane is not automatically worth
copying in another.

Standing benchmark for the whole site rather than one element:
`~/.claude/checklists/design/references/reference-sites.md`, which holds what
linear.app, stripe.com and apple.com already measured out to, and the REF gates
that came from it. Read it before going anywhere else - a question it already
answers does not need a browser.

---

## Navigation

| Site | Worth the trip for |
| :--- | :--- |
| apple.com | **F B** The sticky bar: surface alpha with `saturate()` in the backdrop filter so colour survives the blur. Full keyboard model on the mega menu |
| linear.app | **F M** Header hairline and blur tokens; the menu open curve, which is short and does not bounce |
| stripe.com | **M B** Mega-menu open and close, hover intent so a diagonal mouse path does not close it, focus returned to the trigger on Escape |
| vercel.com | **F** Nav that survives being almost invisible - it is mostly hairline and spacing |
| ssense.com | **F** Nav as typography, no chrome at all |
| rapha.cc | **B** Mobile drawer that scroll-locks the page correctly |

## Hero

| Site | Worth the trip for |
| :--- | :--- |
| apple.com | **F M** Display type ramp and the scroll-driven product reveal. The leading compresses as size grows, per step |
| linear.app | **F** Headline balance and the restraint of a single accent |
| stripe.com | **F M** Type scale under the gradient; the gradient is theirs, the scale is technique |
| teenage.engineering | **F** Hero as a product photograph and almost nothing else |
| analogue.co | **F** Product given the whole viewport, type kept out of its way |
| framer.com | **M** Hero that is a live demo of the tool - motion carrying the argument |

## Feature sections

| Site | Worth the trip for |
| :--- | :--- |
| stripe.com | **F M** Alternating content with a code panel that is a real, syntax-lit artifact |
| linear.app | **M** Scroll-triggered reveals with a stagger small enough to read as one movement |
| vitsoe.com | **F** Long-form product argument, generous measure, no card anywhere |
| ia.net | **F** Typography carrying a feature section with no illustration at all |
| posthog.com | **F** Hand-drawn illustration used systematically rather than as decoration |

## Cards and grids

| Site | Worth the trip for |
| :--- | :--- |
| pentagram.com | **F M** Work grid: image-first, hover that is a crop change rather than a shadow |
| are.na | **F B** Dense grid that stays navigable, keyboard-traversable |
| moma.org | **F B** Collection grid with real metadata and no truncation lies |
| ssense.com | **F** Product grid at extreme density that still reads |
| raycast.com | **F** Feature cards with hairline borders instead of shadows |

## Pricing

| Site | Worth the trip for |
| :--- | :--- |
| linear.app | **F B** Plan comparison, monthly/annual toggle, honest per-seat maths |
| mercury.com | **F** Tier table that does not shout at the recommended plan |
| vercel.com | **B** Usage-based pricing made legible - the hard case |
| resend.com | **F** Three tiers, no dark patterns, nothing decorative |
| ramp.com | **F** Editorial typography applied to a plan table |

## Forms, inputs, controls

| Site | Worth the trip for |
| :--- | :--- |
| stripe.com | **F M B** Checkout and Elements. Field focus ring, inline validation timing, error copy that says what to do. The best worked example on the web |
| linear.app | **M B** Command palette: keyboard model, filter timing, focus handling |
| raycast.com | **B** Keyboard-first interaction shown honestly on a marketing page |
| mercury.com | **F B** Financial inputs - masking, formatting on blur not on keypress |
| figma.com | **F** Control density that stays calm |

Companion reading, already in the design checklist:
`references/code/fields-and-dropdowns.md`.

## Buttons and small controls

| Site | Worth the trip for |
| :--- | :--- |
| linear.app | **F M** The press: a small transform on a fast curve, not a shadow change |
| apple.com | **F** Radius, padding and label weight relationships across button sizes |
| vercel.com | **F** Two-tone system, primary and hairline-ghost, and nothing else |
| stripe.com | **M** Loading state that does not resize the button |

`references/code/click-effect.md` covers the press mechanics.

## Tables and data

| Site | Worth the trip for |
| :--- | :--- |
| linear.app | **F B** Dense rows, sticky headers, hover that does not shift layout |
| bloomberg.com/graphics | **F** Charting with an actual house style |
| pudding.cool | **F M** Data as narrative; scroll-linked chart transitions |
| rijksmuseum.nl | **B** Faceted filtering over a huge collection without a reload |

`references/code/tables-and-headers.md`.

## Testimonials, logos, proof

| Site | Worth the trip for |
| :--- | :--- |
| linear.app | **F** Logo wall at one optical weight, not one pixel height |
| vercel.com | **F** Quotes given room instead of being stacked into a carousel |
| rapha.cc | **F** Photography as proof, no quote card |

A marquee that scrolls forever is the default answer and usually the wrong one.
Check what these do instead before reaching for one.

## Footers

| Site | Worth the trip for |
| :--- | :--- |
| apple.com | **F B** The dense one done right: legal, disclosure, region picker, all navigable |
| stripe.com | **F** Column rhythm at scale |
| vitsoe.com | **F** A footer that is three lines and finished |
| klim.co.nz | **F** Typographic footer treated as a designed surface |

## Modals, drawers, popovers

| Site | Worth the trip for |
| :--- | :--- |
| linear.app | **M B** Open and close curves, focus trap, Escape, scroll lock, return focus |
| stripe.com | **B** Payment sheet: the trap is correct and the backdrop is inert |
| figma.com | **F M** Popover positioning under a viewport edge |

Motion here is the whole thing and it is where rebuilds fail: the open animation
gets matched, the close gets left at 0ms, and the component feels broken in a way
nobody can name.

## Page transitions and scroll

| Site | Worth the trip for |
| :--- | :--- |
| locomotive.ca | **M** Scroll-linked motion from the people whose library named the genre |
| lusion.co | **M** WebGL with a point, and a graceful path when it cannot run |
| active-theory.net | **M** Sustained experimental transition work |
| obys.agency | **M** Kinetic type as the transition itself |
| apple.com | **M** Scroll-driven product sequences that hold 60fps on a laptop |

Every one of these needs the reduced-motion check in `capture.md`. Motion is the
lane where copying without the fallback ships something actively worse.

## Empty, loading, error

| Site | Worth the trip for |
| :--- | :--- |
| linear.app | **F B** Empty states that say what to do next rather than apologising |
| height.app | **F** Skeletons matched to the shape of what arrives |
| stripe.com | **B** Error copy: what happened, what to do, no error code as the headline |

The lane the whole industry skips. An element is not matched until its empty and
its error are.

## Long-form and editorial

| Site | Worth the trip for |
| :--- | :--- |
| craigmod.com | **F** Reading typography: measure, leading, the space around a figure |
| ia.net | **F** The argument that typography is the interface |
| pudding.cool | **F M** Visual essay where the form is the argument |
| letterformarchive.org | **F B** Deep-zoom archive viewer, a genuine piece of interface design |
| commercialtype.com | **F** Dense catalogue navigated without friction |

## Type specimens and scale

| Site | Worth the trip for |
| :--- | :--- |
| klim.co.nz | **F** The best specimen pages on the web. Go here for a type scale |
| abcdinamo.com | **F M** Interactive testers embedded in browsing |
| grillitype.com | **F** Tester UI treated as a designed object |
| sharptype.co | **F** Type shown at the sizes people use it |

---

## Choosing

Three references per element, not one. One reference produces a copy; three
produce a decision, because the places they disagree are where the choice
actually is and the places they agree are close to a rule.

Where all three are outside this project's register - a luxury house's motion
budget on a tooling site - say so and pick again. A well-matched element in the
wrong register is a worse outcome than the thing it replaced.
