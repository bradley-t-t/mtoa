# What "no design change" means, rule by rule

Every gate in this checklist is a change to how bytes reach the browser. None of them
changes what those bytes describe. This file is the line between the two, because the
distinction is obvious in the abstract and slippery in practice: half the fastest fixes
available are one keystroke from being a design decision.

## The test

Two pages, side by side, at the same width, scrolled to the same place. If a reader
could point at anything and call it different, the change failed - however much faster
it is.

That includes position, size, colour, type, weight, spacing, radius, shadow, image
content, image crop, the order of anything, whether a control responds, whether an
animation plays, and what happens on hover. It does not include which file the browser
chose from a `srcset`, which chunk a module arrived in, when a request started, or how
many bytes any of it took.

## The changes that are always safe

These alter delivery and nothing else. They are the body of the checklist.

- **Splitting a bundle.** The same modules run in the same order; they arrive in more
  files. Safe until a fallback occupies different space than the route it stands in for,
  so the fallback reserves the real height, and the parity capture catches it when it
  does not.
- **`srcset` and `sizes`.** The browser picks a file. The rendered picture is the same
  picture, laid out identically, because `width` and `height` are unchanged.
- **`loading="lazy"` below the fold.** The image appears when it is reached, as before.
  On an element already on the first screen this is not safe: it delays the thing being
  measured. That is why the hero gate and the below-fold gate are separate.
- **`fetchpriority`, `preload`, `preconnect`, `modulepreload`.** Ordering hints. They
  change when a request starts and never what it returns.
- **Compression and cache headers.** Identical bytes, fewer of them or fetched less
  often.
- **Removing a redirect.** The same document, one round trip sooner.
- **Deferring third-party script.** Analytics still fires, later. Where the widget draws
  something, deferring it changes when that appears, which the capture will show.
- **`content-visibility: auto` with `contain-intrinsic-size`.** The browser skips layout
  for sections nobody has reached. Without the intrinsic size the scrollbar jumps, so
  the gate asks for both.
- **Dropping an unused dependency.** Nothing imported it.

## The changes that look safe and are not

- **Swapping a web font for a system stack.** The largest single loading win available
  and a straightforward redesign. Refused. Subsetting, preloading, `font-display` and a
  metric-matched fallback get most of the win and keep the face.
- **Removing an animation.** Moving a layout-property animation onto `transform` keeps
  the motion and returns the main thread. Deleting the animation is a different change
  wearing the same commit message.
- **Lowering image quality.** A visible change, at any setting a person can see. Format
  and dimensions are where the bytes are.
- **Removing a shadow, a blur, or a gradient** because it costs paint. It is design.
- **Cutting a section, a testimonial, or a gallery** to reduce weight. Lazy load it.
- **Replacing a component with a lighter one that looks nearly the same.** Nearly is the
  whole problem.
- **Turning off an effect under `prefers-reduced-motion`** is correct, and is not a
  licence to turn it off generally.

## Where a gate and the appearance are in real conflict

Some pages are slow because of what they are. A hero video, a WebGL field, a font with
nine weights, a map on the landing page: each is a real cost and each may be the point.

The gate is then answered `na`, naming the design decision:

```bash
perf-pass.py resolve RND-08 --status na \
  --note "the intro overlay is the brand's opening beat, decided in the design run on 2026-08-04; it holds for 900ms and is skippable"
```

That is a real answer and it is on the record. What is not allowed is quietly removing
the thing and reporting a faster page.

## Why this is measured rather than trusted

An agent working down a list of loading fixes will, eventually, take the one that also
happens to change the page, because the fastest version of almost anything is the
version that does less. A rule in a file is a reminder to a reader who can skip it.

So the invariant is a measurement. `capture.js` records every visible element's box and
computed style before the work and after it, and `perf-pass.py parity --diff` compares
them. The parity gates refuse a hand answer entirely: `pass` and `na` are both rejected
on PAR-01 through PAR-08, because an attestation is the thing the captures exist to
replace.

A capture pair on an unchanged page produces an empty diff. That is the property the
whole mechanism rests on, and it is worth re-checking on any project whose markup is
generated unusually: capture twice with no edits in between, and confirm the diff is
clean before it is trusted to report the truth after a change.
